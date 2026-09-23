"""Sprint 1 (iRich Profit Upgrade v1.4): initial_sl_distance, BE lock offset,
and the Option A lot lock (Demo validation).

#1/#2 (P0): |price_open - sl| collapses to 0 once break-even parks the SL at
the entry, which used to kill R math (favor / sl_dist) for every later poll.
`initial_sl_distance` is the fix; these tests pin both the fix and the bug it
replaces so a regression here is caught immediately.
"""

from __future__ import annotations

import pytest

from src.market_data import TickSnapshot
from src.money_management import resolve_mm
from src.risk import OpenPosition, manage_open_position
from tests.conftest import SERVER_NOW


def _pos(price_open: float, sl: float, is_buy: bool = True) -> OpenPosition:
    return OpenPosition(
        ticket=1,
        symbol="EURUSD",
        is_buy=is_buy,
        price_open=price_open,
        sl=sl,
        tp=price_open + 0.0036 if is_buy else price_open - 0.0036,
        volume=0.01,
    )


def _tick(bid: float, ask: float | None = None) -> TickSnapshot:
    return TickSnapshot(symbol="EURUSD", bid=bid, ask=ask or bid + 0.0001, time=SERVER_NOW)


def test_after_be_trail_still_moves_with_initial_sl_distance(spec):
    """BE already parked the SL at entry (sl_dist == 0). With the initial R
    basis supplied, further favourable movement must still propose a trail."""
    position = _pos(price_open=1.09000, sl=1.09000)  # BE-locked: sl == entry
    tick = _tick(bid=1.09250)  # favor = 0.00250

    change = manage_open_position(
        position,
        spec=spec,
        tick=tick,
        atr=0.00050,
        break_even_r=1.0,
        trailing_start_r=1.5,
        trailing_atr_mult=1.0,
        prefer_structure_trail=False,
        initial_sl_distance=0.00120,
    )

    assert change is not None
    assert change["sl"] > position.sl


def test_without_initial_sl_distance_and_sl_at_entry_returns_none(spec):
    """Documents the old bug: no initial_sl_distance + sl == entry means the R
    basis is 0 and manage_open_position can propose nothing, even though price
    has moved favourably."""
    position = _pos(price_open=1.09000, sl=1.09000)
    tick = _tick(bid=1.09250)

    change = manage_open_position(
        position,
        spec=spec,
        tick=tick,
        atr=0.00050,
        break_even_r=1.0,
        trailing_start_r=1.5,
        trailing_atr_mult=1.0,
        prefer_structure_trail=False,
        # initial_sl_distance intentionally omitted
    )

    assert change is None


def test_be_lock_parks_sl_above_entry_for_buy(spec):
    """Sprint 1 #2: BE must not land exactly on price_open when be_lock_pips
    is configured — it should sit `be_lock_pips` (or more) into profit."""
    position = _pos(price_open=1.09000, sl=1.08880)  # not yet at BE
    tick = _tick(bid=1.09150)  # favor = 0.00150 >= break_even_r * 0.00120

    change = manage_open_position(
        position,
        spec=spec,
        tick=tick,
        atr=0.0,
        break_even_r=1.0,
        trailing_start_r=1.5,
        trailing_atr_mult=1.0,
        prefer_structure_trail=False,
        initial_sl_distance=0.00120,
        be_lock_pips=2.0,
        be_lock_atr_mult=0.0,
    )

    assert change is not None
    assert change["sl"] > position.price_open
    # 2 pips on a 5-digit EURUSD quote is 0.0002.
    assert change["sl"] == pytest.approx(position.price_open + 0.0002, abs=1e-6)


def test_be_lock_defaults_to_at_least_one_point_when_unconfigured(spec):
    """be_lock_pips=0 / be_lock_atr_mult=0 still must not land exactly on
    price_open — the 1*point floor keeps BE strictly in profit."""
    position = _pos(price_open=1.09000, sl=1.08880)
    tick = _tick(bid=1.09150)

    change = manage_open_position(
        position,
        spec=spec,
        tick=tick,
        atr=0.0,
        break_even_r=1.0,
        trailing_start_r=1.5,
        trailing_atr_mult=1.0,
        prefer_structure_trail=False,
        initial_sl_distance=0.00120,
        be_lock_pips=0.0,
        be_lock_atr_mult=0.0,
    )

    assert change is not None
    assert change["sl"] > position.price_open


def test_option_a_lock_forces_001_lot_regardless_of_tier(spec):
    """Sprint 1 #7: option_a_lock overrides FIXED_TIER sizing while Demo
    validation is running, even at a balance that would otherwise step up."""
    cfg = {
        "mode": "FIXED_TIER",
        "option_a_lock": True,
        "fixed_tier": [
            {"min_balance": 0, "max_balance": 199.99, "lot": 0.01, "max_risk_usd": 2.0},
            {"min_balance": 200, "max_balance": 499.99, "lot": 0.02, "max_risk_usd": 4.0},
        ],
    }
    decision = resolve_mm(500.0, 0.0012, spec, cfg)

    assert decision.ok
    assert decision.volume == pytest.approx(0.01)
    assert decision.max_risk_usd == pytest.approx(2.00)


def test_option_a_lock_off_uses_normal_tier_sizing(spec):
    """Sanity check: the lock is opt-in, not a permanent behaviour change."""
    cfg = {
        "mode": "FIXED_TIER",
        "option_a_lock": False,
        "fixed_tier": [
            {"min_balance": 0, "max_balance": 199.99, "lot": 0.01, "max_risk_usd": 2.0},
            {"min_balance": 200, "max_balance": 499.99, "lot": 0.02, "max_risk_usd": 4.0},
        ],
    }
    decision = resolve_mm(500.0, 0.0012, spec, cfg)

    assert decision.ok
    assert decision.volume == pytest.approx(0.02)
    assert decision.max_risk_usd == pytest.approx(4.00)
