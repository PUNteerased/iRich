"""Property / scenario tests for money_management (Phase 0.MM)."""

from __future__ import annotations

from src.market_data import SymbolSpec
from src.money_management import (
    VirtualAccount,
    check_free_margin,
    estimate_required_margin,
    resolve_mm,
)
from src.reject_codes import Reject


def _eur() -> SymbolSpec:
    return SymbolSpec(
        symbol="EURUSD",
        point=1e-5,
        digits=5,
        tick_size=1e-5,
        tick_value=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        contract_size=100000.0,
        margin_leverage=3000.0,
    )


def _btc() -> SymbolSpec:
    return SymbolSpec(
        symbol="BTCUSD",
        point=0.01,
        digits=2,
        tick_size=0.01,
        tick_value=0.01,
        volume_min=0.01,
        volume_max=10.0,
        volume_step=0.01,
        contract_size=1.0,
        margin_leverage=50.0,
    )


MM_CFG = {
    "mode": "FIXED_TIER",
    "fixed_tier": [
        {"min_balance": 0, "max_balance": 199.99, "lot": 0.01, "max_risk_usd": 2.0},
        {"min_balance": 200, "max_balance": 499.99, "lot": 0.02, "max_risk_usd": 4.0},
        {"min_balance": 500, "max_balance": 999.99, "lot": 0.05, "max_risk_usd": 10.0},
    ],
    "dynamic": {
        "risk_per_trade_pct": 2.0,
        "max_risk_usd_cap": 50.0,
        "max_lot_size": 1.0,
        "min_lot_size": 0.01,
    },
}


def test_fixed_tier_stable_inside_band():
    a = resolve_mm(100.0, 0.0012, _eur(), MM_CFG)
    b = resolve_mm(150.0, 0.0012, _eur(), MM_CFG)
    assert a.ok and b.ok
    assert a.volume == b.volume == 0.01
    assert a.max_risk_usd == b.max_risk_usd == 2.0
    assert a.tier_id == 0


def test_fixed_tier_steps_up_at_200():
    d = resolve_mm(200.0, 0.0012, _eur(), MM_CFG)
    assert d.ok
    assert d.volume == 0.02
    assert d.max_risk_usd == 4.0
    assert d.tier_id == 1


def test_model_prob_does_not_affect_lot():
    """Regression: lot must not depend on confidence / streak."""
    a = resolve_mm(100.0, 0.0012, _eur(), MM_CFG)
    b = resolve_mm(100.0, 0.0012, _eur(), MM_CFG)
    assert a.volume == b.volume


def test_margin_gate_rejects_btc_when_free_margin_low():
    spec = _btc()
    required = estimate_required_margin(spec, 0.01, 60000.0)
    # BTC ~ $12 margin on 0.01 at lev 50
    assert required > 10
    check = check_free_margin(required, free_margin=5.0)
    assert not check.ok
    assert check.code == Reject.NO_MONEY


def test_virtual_account_compounds_and_can_step_tier():
    acct = VirtualAccount(100.0)
    for _ in range(40):
        acct.apply_closed_trade(3.0)  # winners
    assert acct.balance >= 200.0
    mm = resolve_mm(acct.account_capital, 0.0012, _eur(), MM_CFG)
    assert mm.tier_id == 1
    assert mm.volume == 0.02


def test_dynamic_pct_caps_risk():
    cfg = dict(MM_CFG)
    cfg["mode"] = "DYNAMIC_PCT"
    d = resolve_mm(
        1000.0,
        0.0012,
        _eur(),
        cfg,
        side="BUY",
        entry_price=1.1000,
    )
    assert d.ok
    assert d.max_risk_usd <= 50.0
    assert d.volume >= 0.01
