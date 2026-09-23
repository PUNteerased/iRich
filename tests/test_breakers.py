"""G01A/G01B/G04: breakers run on server time and survive a restart."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.reject_codes import Reject
from src.risk import PROCESSED_DEAL_HISTORY, RiskBreakers
from src.server_time import FixedClock, ServerClock

AUG = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)
SEP = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


def reopen(breakers: RiskBreakers) -> RiskBreakers:
    """Simulate a restart against the same state file."""
    return RiskBreakers(
        path=breakers.path,
        monthly_max_dd_pct=breakers.monthly_max_dd_pct,
        daily_max_loss_r=breakers.daily_max_loss_r,
        max_consecutive_losses=breakers.max_consecutive_losses,
        risk_unit_usd=breakers.risk_unit_usd,
    )


def test_month_start_equity_is_anchored_once(breakers):
    breakers.update_equity(100.0, AUG)
    breakers.update_equity(105.0, AUG + timedelta(hours=1))
    assert breakers.state.month_start_equity == pytest.approx(100.0)


def test_monthly_drawdown_locks_at_the_threshold(breakers):
    breakers.update_equity(100.0, AUG)
    assert breakers.update_equity(91.0, AUG) is None
    assert breakers.update_equity(90.0, AUG) == Reject.MONTHLY_DD
    assert breakers.halted


def test_lock_survives_a_restart(breakers):
    breakers.update_equity(100.0, AUG)
    breakers.update_equity(89.0, AUG)
    assert breakers.halted

    restarted = reopen(breakers)
    assert restarted.halted
    assert restarted.active_code() == Reject.MONTHLY_DD
    # Recovering equity inside the same month does not release the lock.
    assert restarted.update_equity(99.0, AUG) == Reject.MONTHLY_DD


def test_new_month_releases_the_monthly_lock(breakers):
    breakers.update_equity(100.0, AUG)
    breakers.update_equity(85.0, AUG)
    assert breakers.halted
    assert breakers.update_equity(85.0, SEP) is None
    assert breakers.state.month_start_equity == pytest.approx(85.0)


def test_daily_loss_locks_at_three_r(breakers):
    breakers.update_equity(100.0, AUG)
    for deal_id in (1, 2):
        breakers.register_closed_deal(deal_id, -2.0, AUG)
    assert not breakers.state.daily_halted
    breakers.register_closed_deal(3, -2.0, AUG)
    assert breakers.state.daily_halted
    assert breakers.active_code() == Reject.DAILY_LOSS


def test_daily_lock_releases_next_server_day(breakers):
    breakers.update_equity(100.0, AUG)
    for deal_id in (1, 2, 3):
        breakers.register_closed_deal(deal_id, -2.0, AUG)
    assert breakers.halted
    breakers.roll(AUG + timedelta(days=1))
    assert not breakers.halted
    assert breakers.state.daily_realized_usd == pytest.approx(0.0)


def test_three_consecutive_losses_lock_even_when_small(breakers):
    """Three losing scratches trip the streak breaker before the 3R limit."""
    breakers.update_equity(100.0, AUG)
    for deal_id in (1, 2, 3):
        breakers.register_closed_deal(deal_id, -0.20, AUG)
    assert not breakers.state.daily_halted
    assert breakers.state.consecutive_halted
    assert breakers.active_code() == Reject.CONSECUTIVE_LOSS


def test_a_win_resets_the_streak(breakers):
    breakers.update_equity(100.0, AUG)
    breakers.register_closed_deal(1, -0.50, AUG)
    breakers.register_closed_deal(2, -0.50, AUG)
    breakers.register_closed_deal(3, 1.50, AUG)
    assert breakers.state.consecutive_losses == 0
    breakers.register_closed_deal(4, -0.50, AUG)
    assert not breakers.halted


def test_the_same_deal_is_counted_once(breakers):
    breakers.update_equity(100.0, AUG)
    assert breakers.register_closed_deal(77, -2.0, AUG)
    assert not breakers.register_closed_deal(77, -2.0, AUG)
    assert breakers.state.daily_realized_usd == pytest.approx(-2.0)
    assert breakers.state.consecutive_losses == 1


def test_processed_deal_history_is_bounded(breakers):
    breakers.update_equity(100.0, AUG)
    for deal_id in range(PROCESSED_DEAL_HISTORY + 50):
        breakers.register_closed_deal(deal_id, 0.10, AUG)
    assert len(breakers.state.processed_deals) == PROCESSED_DEAL_HISTORY


def test_corrupt_state_file_starts_fresh_instead_of_crashing(breakers):
    breakers.update_equity(100.0, AUG)
    breakers.path.write_text("{not json", encoding="utf-8")
    restarted = reopen(breakers)
    assert not restarted.halted
    assert restarted.state.month_key is None


def test_state_file_lives_under_the_test_state_dir(breakers, isolated_state):
    breakers.update_equity(100.0, AUG)
    assert breakers.path.exists()
    assert breakers.path.parent == isolated_state["state"]


def test_server_clock_offset_is_cached(monkeypatch):
    """TC-1: the hot path must not ask MT5 for the time."""
    calls = {"n": 0}
    server_now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)

    def fake_server_time():
        calls["n"] += 1
        return server_now

    clock = ServerClock(fake_server_time)
    assert clock.sync()
    assert calls["n"] == 1
    for _ in range(50):
        clock.now()
    assert calls["n"] == 1
    assert clock.synced


def test_server_clock_keeps_last_offset_when_sync_fails():
    clock = ServerClock(lambda: None)
    assert not clock.sync()
    assert clock.offset_seconds == pytest.approx(0.0)


def test_fixed_clock_drives_breaker_keys():
    clock = FixedClock(AUG)
    assert clock.date_key() == "20260817"
    assert clock.month_key() == "2026-08"
    clock.set(SEP)
    assert clock.month_key() == "2026-09"
