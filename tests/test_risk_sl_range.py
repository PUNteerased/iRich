"""G02 / I-10: an out-of-range SL is rejected, never squeezed into the band."""

from __future__ import annotations

import pytest

from src.reject_codes import ACCEPTED, Reject
from src.risk import build_trade_levels, sl_range, validate_sl_in_range
from tests.conftest import SL_CAPS


def levels(spec, distance, side="BUY", caps_key="EURUSD", price=1.09000, max_risk=2.0):
    return build_trade_levels(
        spec=spec,
        side=side,
        executable_price=price,
        structural_sl_distance=distance,
        rr=3.0,
        volume=0.01,
        max_risk_usd=max_risk,
        sl_caps=SL_CAPS[caps_key],
    )


def test_sl_range_resolves_pips_from_digits(spec):
    low, high = sl_range(spec, SL_CAPS["EURUSD"])
    assert low == pytest.approx(0.0010)  # 10 pips at digits 5
    assert high == pytest.approx(0.0015)


def test_sl_range_resolves_points_and_dollars(gold_spec):
    low, high = sl_range(gold_spec, SL_CAPS["XAUUSD"])
    assert low == pytest.approx(1.50)  # 150 points at point 0.01
    assert high == pytest.approx(2.50)


def test_distance_inside_band_is_accepted(spec):
    decision = levels(spec, 0.00120)
    assert decision.ok
    assert decision.code == ACCEPTED


@pytest.mark.parametrize("distance", [0.00050, 0.00099, 0.00151, 0.00400])
def test_distance_outside_band_is_rejected(spec, distance):
    decision = levels(spec, distance)
    assert not decision.ok
    assert decision.code == Reject.SL_RANGE


def test_rejected_sl_is_not_clamped(spec):
    """The whole point of G02: no adjusted level comes back."""
    too_wide = 0.00400
    decision = levels(spec, too_wide)
    assert not decision.ok
    assert decision.sl == 0.0
    assert decision.tp == 0.0
    assert decision.sl_distance == pytest.approx(too_wide)


def test_missing_caps_fails_closed(spec):
    decision = build_trade_levels(
        spec=spec,
        side="BUY",
        executable_price=1.09,
        structural_sl_distance=0.0012,
        rr=3.0,
        volume=0.01,
        max_risk_usd=2.0,
        sl_caps=None,
    )
    assert not decision.ok
    assert decision.code == Reject.SL_RANGE
    assert decision.detail == "no_sl_caps_configured"


def test_non_positive_distance_is_rejected(spec):
    assert levels(spec, 0.0).code == Reject.INVALID_SL_DISTANCE
    assert levels(spec, -0.001).code == Reject.INVALID_SL_DISTANCE


def test_validate_reports_which_side_of_the_band(spec):
    ok, detail = validate_sl_in_range(0.0005, spec, SL_CAPS["EURUSD"])
    assert not ok and "below_min" in detail
    ok, detail = validate_sl_in_range(0.0030, spec, SL_CAPS["EURUSD"])
    assert not ok and "above_max" in detail


def test_gold_wide_end_of_band_breaches_the_dollar_ceiling(gold_spec):
    """XAUUSD 250 points is in range but worth $2.50 on 0.01 lot: reject, not resize."""
    decision = levels(gold_spec, 2.50, caps_key="XAUUSD", price=2400.00)
    assert not decision.ok
    assert decision.code == Reject.PRE_SEND_RISK_BREACH
    assert decision.risk_usd == pytest.approx(2.50)
