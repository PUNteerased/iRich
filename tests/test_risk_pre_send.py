"""I-11 / I-12: the $2 ceiling is checked against the price we are about to send."""

from __future__ import annotations

import pytest

from src.reject_codes import ACCEPTED, Reject
from src.risk import build_trade_levels, calc_risk_usd, post_fill_risk_usd
from tests.conftest import SL_CAPS


def test_risk_uses_tick_size_and_value(spec):
    # 12 pips = 120 ticks at tick_value 1.0 per lot, on 0.01 lot -> $1.20
    assert calc_risk_usd(spec, 0.01, 1.09000, 1.08880) == pytest.approx(1.20)


def test_levels_anchor_to_the_executable_price_not_the_signal_bar(spec):
    """The candidate bar closed at 1.09000; the market is now at 1.09050."""
    decision = build_trade_levels(
        spec=spec,
        side="BUY",
        executable_price=1.09050,
        structural_sl_distance=0.00120,
        rr=3.0,
        volume=0.01,
        max_risk_usd=2.0,
        sl_caps=SL_CAPS["EURUSD"],
    )
    assert decision.ok
    assert decision.entry == pytest.approx(1.09050)
    assert decision.sl == pytest.approx(1.08930)
    assert decision.tp == pytest.approx(1.09410)


def test_sell_levels_are_mirrored(spec):
    decision = build_trade_levels(
        spec=spec,
        side="SELL",
        executable_price=1.09000,
        structural_sl_distance=0.00120,
        rr=3.0,
        volume=0.01,
        max_risk_usd=2.0,
        sl_caps=SL_CAPS["EURUSD"],
    )
    assert decision.ok
    assert decision.sl > decision.entry > decision.tp


def test_pre_send_breach_rejects_before_sending(gold_spec):
    decision = build_trade_levels(
        spec=gold_spec,
        side="BUY",
        executable_price=2400.00,
        structural_sl_distance=2.40,  # 240 points -> $2.40
        rr=3.0,
        volume=0.01,
        max_risk_usd=2.0,
        sl_caps=SL_CAPS["XAUUSD"],
    )
    assert not decision.ok
    assert decision.code == Reject.PRE_SEND_RISK_BREACH


def test_accepted_decision_never_exceeds_the_ceiling(gold_spec):
    decision = build_trade_levels(
        spec=gold_spec,
        side="BUY",
        executable_price=2400.00,
        structural_sl_distance=1.90,
        rr=3.0,
        volume=0.01,
        max_risk_usd=2.0,
        sl_caps=SL_CAPS["XAUUSD"],
    )
    assert decision.code == ACCEPTED
    assert decision.risk_usd <= 2.0


def test_post_fill_risk_adds_costs_and_uses_the_fill_price(spec):
    """Slippage plus commission is exactly how a $1.20 plan becomes an excess."""
    planned = post_fill_risk_usd(spec, 0.01, 1.09000, 1.08880)
    slipped = post_fill_risk_usd(spec, 0.01, 1.09050, 1.08880, commission=0.07, swap=0.02)
    assert planned == pytest.approx(1.20)
    assert slipped == pytest.approx(1.70 + 0.09)
    assert slipped > planned


def test_zero_tick_value_reports_calc_failure():
    from src.market_data import SymbolSpec

    broken = SymbolSpec("EURUSD", point=1e-05, digits=5, tick_size=1e-05, tick_value=0.0)
    decision = build_trade_levels(
        spec=broken,
        side="BUY",
        executable_price=1.09,
        structural_sl_distance=0.0012,
        rr=3.0,
        volume=0.01,
        max_risk_usd=2.0,
        sl_caps=SL_CAPS["EURUSD"],
    )
    assert decision.code == Reject.RISK_CALC_FAILED
