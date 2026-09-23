"""Optional smoke tests against a live MetaTrader5 terminal.

Excluded from the default run. These are read-only: they never place an order.

    python -m pytest -m mt5
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.config import load_config

pytestmark = pytest.mark.mt5


@pytest.fixture(scope="module")
def connector():
    from src.mt5_connector import MT5Connector

    cfg = load_config()
    mt5c = MT5Connector(cfg)
    if not mt5c.connect():
        pytest.skip("MetaTrader5 terminal is not reachable")
    yield mt5c
    mt5c.shutdown()


def test_account_portfolio_fields_are_readable(connector):
    """MM inputs must come from the connected portfolio, Demo or Real."""
    snap = connector.account_snapshot()
    assert snap is not None
    assert snap.balance > 0
    assert snap.equity > 0
    assert snap.leverage > 0
    assert connector.account_balance() == pytest.approx(snap.balance)
    assert connector.account_leverage() == pytest.approx(snap.leverage)


def test_specs_are_present_for_every_configured_symbol(connector):
    cfg = load_config()
    for symbol in cfg.symbols:
        spec = connector.provider.get_symbol_info(symbol)
        assert spec is not None, symbol
        assert spec.point > 0
        assert spec.tick_size > 0
        assert spec.tick_value > 0


def test_ticks_are_two_sided_and_ordered(connector):
    cfg = load_config()
    seen = 0
    for symbol in cfg.symbols:
        tick = connector.provider.get_tick(symbol)
        if tick is None:
            continue  # symbol closed for this session
        assert tick.ask >= tick.bid
        assert tick.spread_price >= 0
        seen += 1
    assert seen > 0, "no symbol returned a tick at all"


def test_server_clock_syncs_and_stays_close_to_local(connector):
    """TC-1: the offset is read once here, not on every scan."""
    from src.server_time import ServerClock

    clock = ServerClock(connector.server_time)
    assert clock.sync()
    assert clock.synced
    # FBS servers run a few hours ahead of UTC, never days.
    assert abs(clock.offset_seconds) < 24 * 3600
    assert clock.now() - datetime.now(timezone.utc) < timedelta(days=1)


def test_history_sweep_returns_mapped_deals(connector):
    """QG-3 reads real deal history; an empty window is a valid answer."""
    now = datetime.now(timezone.utc)
    deals = connector.closed_deals(now - timedelta(days=7), now)
    for deal in deals:
        assert deal.symbol
        assert deal.deal_id > 0
        assert deal.outcome in {"tp", "sl", "be"}
