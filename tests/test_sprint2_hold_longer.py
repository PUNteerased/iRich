"""Sprint 2: staircase, BE@2R with initial_sl_distance, opposing TP, option_a_lock."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.market_data import TickSnapshot
from src.money_management import dynamic_scaling_gate_passed, resolve_mm
from src.risk import OpenPosition, manage_open_position, staircase_floor
from src.zones import opposing_structure_tp
from tests.conftest import SERVER_NOW


def _pos(price_open: float, sl: float, is_buy: bool = True) -> OpenPosition:
    return OpenPosition(
        ticket=1,
        symbol="EURUSD",
        is_buy=is_buy,
        price_open=price_open,
        sl=sl,
        tp=price_open + 0.003 if is_buy else price_open - 0.003,
        volume=0.01,
    )


def _tick(bid: float, ask: float | None = None) -> TickSnapshot:
    return TickSnapshot(symbol="EURUSD", bid=bid, ask=ask or bid + 0.0001, time=SERVER_NOW)


def test_staircase_floor_locks_plus_one_r_at_3r():
    floor = staircase_floor(favor_r=3.0, r_basis=0.001, entry=1.10000, buy=True)
    assert floor is not None
    assert abs(floor - 1.10100) < 1e-9


def test_manage_staircase_raises_sl_after_be(spec):
    pos = _pos(price_open=1.10000, sl=1.10020)
    tick = _tick(bid=1.10310)
    change = manage_open_position(
        pos,
        spec=spec,
        tick=tick,
        atr=0.0005,
        break_even_r=2.0,
        trailing_start_r=2.0,
        initial_sl_distance=0.001,
        be_lock_pips=2.0,
        enable_staircase=True,
    )
    assert change is not None
    assert change["sl"] >= 1.10100 - 1e-9


def test_opposing_structure_tp_extends_beyond_3r():
    # Build a series with an explicit swing high well above entry via smc.swing_levels
    from src.smc import swing_levels

    n = 50
    close = np.full(n, 1.1000)
    high = np.full(n, 1.1005)
    low = np.full(n, 1.0995)
    # Pivot high at index -6 (confirmed with swing=3 on iloc[:-1])
    high[-8] = 1.1090
    high[-7] = 1.1080
    high[-6] = 1.1100  # swing high
    high[-5] = 1.1080
    high[-4] = 1.1070
    df = pd.DataFrame({"open": close, "high": high, "low": low, "close": close})
    low_lvl, high_lvl = swing_levels(df.iloc[:-1], swing=2, lookback=40)
    assert not high_lvl.dropna().empty
    out = opposing_structure_tp(
        df, side="BUY", entry=1.1000, sl_distance=0.001, min_rr=3.0, max_rr=8.0
    )
    assert out.tp is not None
    assert out.tp >= 1.1000 + 3.0 * 0.001 - 1e-9


def test_option_a_lock_forces_micro_lot(spec):
    mm = resolve_mm(
        account_capital=500.0,
        structural_sl_distance=0.0010,
        spec=spec,
        mm_cfg={
            "mode": "FIXED_TIER",
            "option_a_lock": True,
            "tiers": [
                {"id": 0, "min_balance": 0, "lot": 0.01, "max_risk_usd": 2.0},
                {"id": 1, "min_balance": 200, "lot": 0.02, "max_risk_usd": 4.0},
            ],
        },
        fallback_lot=0.01,
        fallback_max_risk_usd=2.0,
        side="BUY",
        entry_price=1.10,
    )
    assert mm.ok
    assert mm.volume == 0.01
    assert mm.max_risk_usd == 2.0


def test_dynamic_scaling_gate_requires_sample():
    g = dynamic_scaling_gate_passed(0.9, 3.0, n_trades=5, min_trades=20)
    assert g.passed is False
    g2 = dynamic_scaling_gate_passed(0.6, 2.5, n_trades=25, min_trades=20)
    assert g2.passed is True
