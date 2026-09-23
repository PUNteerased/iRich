"""QG-3 / G20: trades that closed while the bot was down still have to be learned from.

MT5 is the execution truth (I-08). The journal only records what the bot believed,
so on startup the two are reconciled - and reconciling twice must change nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.audit_log import TRADE_STATUS_CLOSED, TRADE_STATUS_EXECUTED
from src.trade_ledger import (
    SOURCE_LIVE,
    SOURCE_STARTUP_SYNC,
    ClosedDeal,
    TradeLedger,
    sync_orphaned_deals,
)

CRASH_AT = datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc)
RESTART_AT = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def ledger(audit, experience, confidence, breakers):
    return TradeLedger(audit, experience, confidence, breakers, rr=3.0)


def deal(deal_id=5001, position_id=9001, symbol="EURUSD", profit=3.60, at=None):
    return ClosedDeal(
        deal_id=deal_id,
        position_id=position_id,
        symbol=symbol,
        profit=profit,
        commission=-0.07,
        swap=0.0,
        price=1.09360,
        volume=0.01,
        time=at or (CRASH_AT + timedelta(hours=2)),
    )


def open_a_trade(ledger, model_identity, position_id=9001, symbol="EURUSD"):
    return ledger.record_execution(
        decision_id="20260814-100000-EURUSD-00001",
        symbol=symbol,
        position_id=position_id,
        model=model_identity,
        entry=1.09000,
        sl=1.08880,
        tp=1.09360,
        volume=0.01,
        risk_usd=1.20,
    )


def test_execution_is_journalled_as_open(ledger, model_identity, audit):
    open_a_trade(ledger, model_identity)
    assert 9001 in ledger.open_trades
    records = audit.read("trades")
    assert records[-1]["status"] == TRADE_STATUS_EXECUTED
    assert records[-1]["model_sha256"] == "deadbeef"


def test_journal_is_rebuilt_from_disk_after_a_restart(
    ledger, model_identity, audit, experience, confidence, breakers
):
    open_a_trade(ledger, model_identity)
    restarted = TradeLedger(audit, experience, confidence, breakers, rr=3.0)
    assert 9001 in restarted.open_trades


def test_sync_closes_the_trade_and_feeds_the_learning_loop(
    ledger, model_identity, confidence, experience, breakers
):
    open_a_trade(ledger, model_identity)
    before = confidence.get("EURUSD")

    applied = sync_orphaned_deals(
        ledger, lambda since, now: [deal()], RESTART_AT, lookback_days=14
    )

    assert applied == 1
    assert ledger.open_trades == {}
    assert confidence.get("EURUSD") > before  # a win raises the weight
    outcomes = experience.load_outcomes("EURUSD")
    assert len(outcomes) == 1
    assert outcomes[0]["outcome"] == "tp"
    assert outcomes[0]["source"] == SOURCE_STARTUP_SYNC
    assert outcomes[0]["journal_matched"] is True
    assert breakers.has_processed(5001)


def test_sync_is_idempotent(ledger, model_identity, confidence, experience):
    open_a_trade(ledger, model_identity)
    source = lambda since, now: [deal()]  # noqa: E731

    assert sync_orphaned_deals(ledger, source, RESTART_AT) == 1
    weight_after_first = confidence.get("EURUSD")

    # Same deal offered again: an overlapping window, or simply another restart.
    assert sync_orphaned_deals(ledger, source, RESTART_AT) == 0
    assert confidence.get("EURUSD") == pytest.approx(weight_after_first)
    assert len(experience.load_outcomes("EURUSD")) == 1


def test_sync_does_nothing_when_the_journal_is_clean(ledger):
    calls = []

    def source(since, now):
        calls.append((since, now))
        return []

    assert sync_orphaned_deals(ledger, source, RESTART_AT) == 0
    assert calls == []  # no history sweep at all


def test_sweep_window_is_capped_by_the_lookback(ledger, model_identity):
    """A bot that was off for months must not pull its whole deal history."""
    open_a_trade(ledger, model_identity)
    captured = {}

    def source(since, now):
        captured["since"] = since
        return []

    sync_orphaned_deals(ledger, source, RESTART_AT, lookback_days=2)
    assert captured["since"] >= RESTART_AT - timedelta(days=2)


def test_sweep_window_reaches_back_to_the_oldest_open_trade(ledger, model_identity):
    open_a_trade(ledger, model_identity)
    captured = {}

    def source(since, now):
        captured["since"] = since
        return []

    sync_orphaned_deals(ledger, source, RESTART_AT, lookback_days=30)
    # Journal ts is wall-clock at record time; window starts just before that trade.
    trade_ts = datetime.fromisoformat(str(ledger.open_trades[9001]["ts"]))
    assert captured["since"] < trade_ts
    assert captured["since"] >= trade_ts - timedelta(minutes=2)


def test_a_losing_orphan_counts_towards_the_breakers(ledger, model_identity, breakers):
    open_a_trade(ledger, model_identity)
    sync_orphaned_deals(
        ledger, lambda since, now: [deal(profit=-1.20)], RESTART_AT
    )
    assert breakers.state.consecutive_losses == 1
    assert breakers.state.daily_realized_usd < 0


def test_three_losing_orphans_lock_the_breaker_before_the_loop_starts(
    ledger, model_identity, breakers
):
    """Reconciliation must be able to halt a bot that woke up already in trouble."""
    for index, position in enumerate((9001, 9002, 9003)):
        open_a_trade(ledger, model_identity, position_id=position)
    deals = [
        deal(deal_id=6000 + i, position_id=p, profit=-1.20)
        for i, p in enumerate((9001, 9002, 9003))
    ]
    assert sync_orphaned_deals(ledger, lambda since, now: deals, RESTART_AT) == 3
    assert breakers.halted


def test_unknown_position_is_still_learned_from(ledger, breakers, experience):
    """A deal with no journal entry is flagged, not dropped."""
    ledger.record_execution(
        decision_id="20260814-100000-EURUSD-00001",
        symbol="EURUSD",
        position_id=9001,
        entry=1.09,
        sl=1.0888,
    )
    applied = ledger.apply_closed_deal(deal(position_id=7777), RESTART_AT)
    assert applied
    outcomes = experience.load_outcomes("EURUSD")
    assert outcomes[0]["decision_id"].startswith("orphan-")
    assert outcomes[0]["journal_matched"] is False
    assert outcomes[0]["source"] == SOURCE_LIVE
    # The journal entry for 9001 is untouched; only MT5 can close it.
    assert 9001 in ledger.open_trades


def test_closed_deal_nets_costs_into_the_outcome():
    """A gross win of 5 cents against 7 cents of commission is a loss."""
    assert ClosedDeal(1, 1, "EURUSD", profit=0.05, commission=-0.07).outcome == "sl"
    assert ClosedDeal(1, 1, "EURUSD", profit=3.60, commission=-0.07).outcome == "tp"
    assert ClosedDeal(1, 1, "EURUSD", profit=0.0).outcome == "be"


def test_closed_record_is_appended_to_the_journal(ledger, model_identity, audit):
    open_a_trade(ledger, model_identity)
    sync_orphaned_deals(ledger, lambda since, now: [deal()], RESTART_AT)
    statuses = [r["status"] for r in audit.read("trades")]
    assert statuses == [TRADE_STATUS_EXECUTED, TRADE_STATUS_CLOSED]
