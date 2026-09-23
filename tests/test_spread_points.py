"""G10: spread is compared in points, and an uncalibrated cap fails closed."""

from __future__ import annotations

import pytest

from src.risk import is_spread_ok
from tests.conftest import BTCUSD_SPEC, EURUSD_SPEC, USDJPY_SPEC, XAUUSD_SPEC, make_tick


def test_points_are_scale_free_across_symbols():
    """The same 10-point spread reads as 10 on every symbol, whatever the digits."""
    cases = [
        (EURUSD_SPEC, 1.09000, 1.09010),
        (USDJPY_SPEC, 150.000, 150.010),
        (XAUUSD_SPEC, 2400.00, 2400.10),
        (BTCUSD_SPEC, 60000.00, 60000.10),
    ]
    for spec, bid, ask in cases:
        tick = make_tick(spec.symbol, bid, ask)
        assert spec.spread_points(tick) == pytest.approx(10.0, rel=1e-6)


def test_pip_is_ten_points_on_five_and_three_digit_quotes():
    assert EURUSD_SPEC.pip == pytest.approx(0.0001)
    assert USDJPY_SPEC.pip == pytest.approx(0.01)
    # Two-digit quotes have no separate pip convention.
    assert XAUUSD_SPEC.pip == pytest.approx(0.01)


def test_uncalibrated_cap_rejects(spec):
    """TBD_CALIBRATE must not behave like "no limit"."""
    tick = make_tick("EURUSD", 1.09000, 1.09010)
    ok, points = is_spread_ok(spec, tick, None)
    assert not ok
    assert points == pytest.approx(10.0)


def test_cap_boundary_is_inclusive(spec):
    tick = make_tick("EURUSD", 1.09000, 1.09010)
    assert is_spread_ok(spec, tick, 10.0)[0]
    assert is_spread_ok(spec, tick, 10.5)[0]
    assert not is_spread_ok(spec, tick, 9.9)[0]


def test_widening_spread_trips_the_gate(spec):
    """News widening: 8 points passes a 12 point cap, 30 does not."""
    assert is_spread_ok(spec, make_tick("EURUSD", 1.09000, 1.09008), 12.0)[0]
    assert not is_spread_ok(spec, make_tick("EURUSD", 1.09000, 1.09030), 12.0)[0]


def test_zero_point_spec_reports_infinite_spread():
    from src.market_data import SymbolSpec

    broken = SymbolSpec("EURUSD", point=0.0, digits=5, tick_size=1e-05, tick_value=1.0)
    tick = make_tick("EURUSD", 1.09000, 1.09010)
    ok, points = is_spread_ok(broken, tick, 10.0)
    assert not ok
    assert points == float("inf")


def test_config_reads_the_calibration_file(tmp_path):
    import json

    import yaml

    from src.config import Config

    calib = tmp_path / "spread_calibration.json"
    calib.write_text(
        json.dumps({"symbols": {"EURUSD": {"max_points": 14.5, "sample_count": 5000}}}),
        encoding="utf-8",
    )
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "mode": "demo",
                "spread": {
                    "calibration_path": str(calib),
                    "max_points_by_symbol": {
                        "EURUSD": "TBD_CALIBRATE",
                        "USDJPY": 12,
                        "XAUUSD": "TBD_CALIBRATE",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    cfg = Config(cfg_path)
    assert cfg.max_spread_points("EURUSD") == pytest.approx(14.5)
    # An explicit number in config wins without needing a calibration entry.
    assert cfg.max_spread_points("USDJPY") == pytest.approx(12.0)
    # No samples on file means unmeasured, so live fails closed.
    assert cfg.max_spread_points("XAUUSD") is None
