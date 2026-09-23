"""QG-1: risk and execution read prices through the provider, never MetaTrader5."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.market_data import (
    MarketDataProvider,
    MockProvider,
    ReplayProvider,
    SymbolSpec,
    is_buy_side,
    load_symbol_specs,
)
from tests.conftest import EURUSD_SPEC, XAUUSD_SPEC


def test_mock_and_replay_both_satisfy_the_protocol():
    assert isinstance(MockProvider(), MarketDataProvider)
    assert isinstance(ReplayProvider({"EURUSD": EURUSD_SPEC}), MarketDataProvider)


def test_mock_provider_exposes_portfolio_mm_fields():
    provider = MockProvider(balance=125.5, leverage=3000.0, free_margin_value=120.0)
    assert provider.account_capital() == pytest.approx(125.5)
    assert provider.account_leverage() == pytest.approx(3000.0)
    snap = provider.account_snapshot()
    assert snap is not None
    assert snap.balance == pytest.approx(125.5)
    assert snap.free_margin == pytest.approx(120.0)
    assert snap.leverage == pytest.approx(3000.0)


def test_mock_provider_returns_none_for_unknown_symbols(provider):
    assert provider.get_tick("GBPUSD") is None
    assert provider.get_symbol_info("GBPUSD") is None


def test_side_names_map_to_buy(provider):
    for name in ("BUY", "buy", "BULLISH", "LONG"):
        assert is_buy_side(name)
    for name in ("SELL", "BEARISH", "SHORT"):
        assert not is_buy_side(name)


def test_replay_provider_needs_specs_up_front():
    """Falling back to live specs would price old trades with today's tick value."""
    with pytest.raises(ValueError, match="symbol specs"):
        ReplayProvider({})


def test_replay_tick_is_centred_on_the_bar_close():
    provider = ReplayProvider(
        specs={"XAUUSD": XAUUSD_SPEC},
        spread_points={"XAUUSD": 30.0},
    )
    bar_time = datetime(2026, 3, 2, 14, 0, tzinfo=timezone.utc)
    provider.advance("XAUUSD", close=2400.00, bar_time=bar_time)

    tick = provider.get_tick("XAUUSD")
    assert tick is not None
    assert tick.time == bar_time
    assert tick.bid == pytest.approx(2399.85)
    assert tick.ask == pytest.approx(2400.15)
    assert XAUUSD_SPEC.spread_points(tick) == pytest.approx(30.0, rel=1e-6)


def test_replay_provider_has_no_tick_before_the_first_bar():
    provider = ReplayProvider({"EURUSD": EURUSD_SPEC})
    assert provider.get_tick("EURUSD") is None
    assert provider.get_symbol_info("EURUSD") == EURUSD_SPEC


def test_replay_provider_advances_with_simulated_time():
    provider = ReplayProvider({"EURUSD": EURUSD_SPEC}, spread_points={"EURUSD": 10.0})
    first = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)
    second = first.replace(hour=10)
    provider.advance("EURUSD", 1.09000, first)
    assert provider.get_tick("EURUSD").time == first
    provider.advance("EURUSD", 1.09500, second)
    tick = provider.get_tick("EURUSD")
    assert tick.time == second
    assert tick.executable_price("BUY") == pytest.approx(1.09505)


def test_specs_round_trip_through_the_snapshot_file(tmp_path):
    path = tmp_path / "symbol_specs.json"
    path.write_text(
        json.dumps(
            {
                "captured_at": "2026-08-16T13:32:45+00:00",
                "symbols": {
                    "EURUSD": {
                        "symbol": "EURUSD",
                        "point": 1e-05,
                        "digits": 5,
                        "tick_size": 1e-05,
                        "tick_value": 1.0,
                        "filling_mode": 3,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    specs = load_symbol_specs(path)
    assert specs["EURUSD"] == EURUSD_SPEC


def test_missing_spec_file_returns_empty_rather_than_guessing(tmp_path):
    assert load_symbol_specs(tmp_path / "absent.json") == {}


def test_risk_gate_works_without_metatrader_installed(provider):
    """The whole point of the abstraction: gates run with no terminal present."""
    from src.risk import build_trade_levels
    from tests.conftest import SL_CAPS

    tick = provider.get_tick("EURUSD")
    spec = provider.get_symbol_info("EURUSD")
    decision = build_trade_levels(
        spec=spec,
        side="BUY",
        executable_price=tick.executable_price("BUY"),
        structural_sl_distance=0.0012,
        rr=3.0,
        volume=0.01,
        max_risk_usd=2.0,
        sl_caps=SL_CAPS["EURUSD"],
    )
    assert decision.ok
    assert decision.entry == pytest.approx(tick.ask)


def test_spec_uses_point_when_tick_size_is_missing():
    spec = SymbolSpec("EURUSD", point=1e-05, digits=5, tick_size=1e-05, tick_value=1.0)
    assert spec.tick_size == pytest.approx(spec.point)
