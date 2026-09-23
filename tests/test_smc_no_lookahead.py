"""G09: SMC features must not depend on bars that had not closed yet.

The decisive test is truncation invariance. If the value at bar i changes once
later bars arrive, then the training rows saw a future the live row cannot, and
every backtest built on them is optimistic for a reason no metric will show.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.smc import (
    SMC_FEATURE_COLS,
    _swing_highs_lows,
    choch_flags,
    fvg_zones,
    smc_feature_frame,
    sweep_flags,
    swing_levels,
)

SWING = 3


@pytest.fixture
def bars():
    """Deterministic random walk with enough structure to trip the detectors."""
    rng = np.random.default_rng(7)
    n = 400
    close = 1.1000 + np.cumsum(rng.normal(0, 0.0004, n))
    high = close + np.abs(rng.normal(0, 0.0003, n))
    low = close - np.abs(rng.normal(0, 0.0003, n))
    return pd.DataFrame(
        {
            "time": pd.date_range("2026-01-01", periods=n, freq="1min", tz="UTC"),
            "open": np.r_[close[0], close[:-1]],
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": rng.integers(50, 500, n),
        }
    )


def test_swing_flags_never_fire_on_unconfirmable_tail(bars):
    """A swing at bar i needs bar i+right, so the last `right` bars cannot flag."""
    sh, sl = _swing_highs_lows(bars, left=SWING, right=SWING)
    assert not sh.iloc[-SWING:].any()
    assert not sl.iloc[-SWING:].any()


def test_swing_level_comes_from_a_confirmed_bar(bars):
    """The level visible at bar i must originate at or before bar i-swing."""
    sh, _ = _swing_highs_lows(bars, left=SWING, right=SWING)
    _, high_level = swing_levels(bars, swing=SWING, lookback=50)
    i = len(bars) - 1
    level = high_level.iloc[i]
    assert not pd.isna(level)
    origins = np.flatnonzero((bars["high"].to_numpy() == level) & sh.to_numpy())
    assert origins.size > 0
    assert origins.min() <= i - SWING


@pytest.mark.parametrize("cut", [200, 275, 331, 399])
def test_flags_do_not_change_when_later_bars_arrive(bars, cut):
    truncated = bars.iloc[:cut].reset_index(drop=True)
    last = cut - 1

    for name, fn in (("sweep", sweep_flags), ("choch", choch_flags)):
        full_bull, full_bear = fn(bars, swing=SWING)
        cut_bull, cut_bear = fn(truncated, swing=SWING)
        assert bool(cut_bull.iloc[last]) == bool(full_bull.iloc[last]), f"{name}_bull"
        assert bool(cut_bear.iloc[last]) == bool(full_bear.iloc[last]), f"{name}_bear"

    full_zones = fvg_zones(bars).iloc[last]
    cut_zones = fvg_zones(truncated).iloc[last]
    pd.testing.assert_series_equal(full_zones, cut_zones, check_names=False)


@pytest.mark.parametrize("cut", [150, 260, 399])
def test_feature_frame_row_is_stable_as_history_grows(bars, cut):
    """The row the model scores live must equal the row it was trained on."""
    full = smc_feature_frame(bars, swing=SWING)
    partial = smc_feature_frame(bars.iloc[:cut].reset_index(drop=True), swing=SWING)
    last = cut - 1
    for col in SMC_FEATURE_COLS:
        expected = full[col].iloc[last]
        actual = partial[col].iloc[last]
        if pd.isna(expected):
            assert pd.isna(actual), col
        else:
            assert actual == pytest.approx(expected, rel=1e-9, abs=1e-12), col


def test_feature_frame_is_stable_across_every_bar(bars):
    """Sweep the whole series rather than a few spot checks."""
    full = smc_feature_frame(bars, swing=SWING)
    for cut in range(120, len(bars) + 1, 37):
        partial = smc_feature_frame(bars.iloc[:cut].reset_index(drop=True), swing=SWING)
        last = cut - 1
        for col in SMC_FEATURE_COLS:
            expected, actual = full[col].iloc[last], partial[col].iloc[last]
            assert (pd.isna(expected) and pd.isna(actual)) or actual == pytest.approx(
                expected, rel=1e-9, abs=1e-12
            ), f"{col} at cut={cut}"


def test_warmup_rows_carry_no_signal(bars):
    frame = smc_feature_frame(bars, swing=SWING, warmup=20)
    head = frame.iloc[:20]
    assert (head["sweep_bull"] == 0.0).all()
    assert (head["choch_bear"] == 0.0).all()
    assert head["fvg_bull_dist"].isna().all()


def test_fvg_zones_use_the_documented_three_bar_pattern():
    """low[i] > high[i-2] is a bullish gap; the zone spans that imbalance."""
    df = pd.DataFrame(
        {
            # Bar 2 gaps above bar 0. Bar 3 does not gap above bar 1 (1.05 < 1.1).
            "high": [1.0, 1.1, 1.5, 1.6],
            "low": [0.9, 1.0, 1.2, 1.05],
            "close": [0.95, 1.05, 1.3, 1.4],
        }
    )
    zones = fvg_zones(df)
    assert zones["bull_top"].iloc[2] == pytest.approx(1.2)  # low[2]
    assert zones["bull_bottom"].iloc[2] == pytest.approx(1.0)  # high[0]
    # Carried forward while no newer gap appears.
    assert zones["bull_top"].iloc[3] == pytest.approx(1.2)
    assert zones["bull_top"].iloc[:2].isna().all()
