"""I-12: the signal bar's close is a candidate, not a fill price."""

from __future__ import annotations

import pytest

from src.market_data import TickSnapshot
from src.risk import validate_entry_drift
from tests.conftest import SERVER_NOW


SL_DISTANCE = 0.00120  # 12 pips
MAX_DRIFT_R = 0.25  # 3 pips


@pytest.mark.parametrize("executable", [1.09000, 1.09029, 1.08971])
def test_small_drift_is_accepted(executable):
    ok, drift = validate_entry_drift(1.09000, executable, SL_DISTANCE, MAX_DRIFT_R)
    assert ok
    assert drift <= MAX_DRIFT_R * SL_DISTANCE


@pytest.mark.parametrize("executable", [1.09031, 1.09500, 1.08000])
def test_drift_beyond_the_threshold_is_rejected(executable):
    ok, drift = validate_entry_drift(1.09000, executable, SL_DISTANCE, MAX_DRIFT_R)
    assert not ok
    assert drift > MAX_DRIFT_R * SL_DISTANCE


def test_threshold_scales_with_the_stop_distance():
    """A wide stop tolerates more drift than a tight one, in absolute price."""
    tight_ok, _ = validate_entry_drift(1.09, 1.0904, 0.0010, MAX_DRIFT_R)
    wide_ok, _ = validate_entry_drift(1.09, 1.0904, 0.0040, MAX_DRIFT_R)
    assert not tight_ok
    assert wide_ok


def test_zero_stop_distance_fails_closed():
    ok, _ = validate_entry_drift(1.09, 1.09, 0.0, MAX_DRIFT_R)
    assert not ok


def test_buy_pays_the_ask_and_sell_receives_the_bid():
    """Drift is measured against the side-correct price, which is half the point."""
    tick = TickSnapshot("EURUSD", bid=1.09000, ask=1.09010, time=SERVER_NOW)
    assert tick.executable_price("BUY") == pytest.approx(1.09010)
    assert tick.executable_price("SELL") == pytest.approx(1.09000)
    assert tick.exit_price("BUY") == pytest.approx(1.09000)
    assert tick.exit_price("SELL") == pytest.approx(1.09010)


def test_spread_alone_can_exhaust_the_drift_budget():
    """A 3 pip spread against a 3 pip budget leaves nothing for market movement."""
    tick = TickSnapshot("EURUSD", bid=1.09000, ask=1.09030, time=SERVER_NOW)
    ok, drift = validate_entry_drift(
        1.09000, tick.executable_price("BUY"), SL_DISTANCE, MAX_DRIFT_R
    )
    assert ok
    assert drift == pytest.approx(0.00030)
