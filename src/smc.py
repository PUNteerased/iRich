"""Smart Money Concepts: liquidity sweep, ChoCH, FVG, order blocks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

Bias = Literal["BULLISH", "BEARISH", "NEUTRAL"]


@dataclass
class StructureSignal:
    ok: bool
    side: Bias = "NEUTRAL"
    level: float | None = None
    sl: float | None = None
    reason: str = ""


def _swing_highs_lows(
    df: pd.DataFrame, left: int = 3, right: int = 3
) -> tuple[pd.Series, pd.Series]:
    """Fractal swings: bar i extends the window [i-left, i+right].

    A swing is therefore only knowable once bar i+right has closed. Callers
    that evaluate bar i must not read swing flags newer than i-right.
    """
    high = df["high"]
    low = df["low"]
    window = left + right + 1
    # Centre the rolling window on i, so the tail stays NaN and never flags.
    roll_max = high.rolling(window).max().shift(-right)
    roll_min = low.rolling(window).min().shift(-right)
    sh = (high == roll_max).fillna(False)
    sl = (low == roll_min).fillna(False)
    return sh, sl


def swing_levels(
    df: pd.DataFrame,
    swing: int = 3,
    lookback: int = 50,
) -> tuple[pd.Series, pd.Series]:
    """Last confirmed swing low/high level visible at each bar.

    Shifting by `swing` enforces causality: a swing at index j is only known
    once bar j+swing has closed, so bar i may reference swings up to i-swing.
    NaN means no swing inside the lookback window.
    """
    sh, sl = _swing_highs_lows(df, left=swing, right=swing)
    position = pd.Series(np.arange(len(df), dtype=float), index=df.index)

    def confirmed(level: pd.Series, flags: pd.Series) -> pd.Series:
        value = level.where(flags).ffill().shift(swing)
        found_at = position.where(flags).ffill().shift(swing)
        fresh = found_at.notna() & ((position - found_at) <= lookback)
        return value.where(fresh)

    return confirmed(df["low"], sl), confirmed(df["high"], sh)


def sweep_flags(
    df: pd.DataFrame,
    swing: int = 3,
    lookback: int = 50,
) -> tuple[pd.Series, pd.Series]:
    """Vectorised equivalent of detect_liquidity_sweep over every bar."""
    low_level, high_level = swing_levels(df, swing=swing, lookback=lookback)
    bull = (df["low"] < low_level) & (df["close"] > low_level)
    bear = (df["high"] > high_level) & (df["close"] < high_level)
    return bull.fillna(False), bear.fillna(False)


def choch_flags(
    df: pd.DataFrame,
    swing: int = 2,
    lookback: int = 40,
) -> tuple[pd.Series, pd.Series]:
    """Vectorised equivalent of detect_choch over every bar."""
    low_level, high_level = swing_levels(df, swing=swing, lookback=lookback)
    bull = df["close"] > high_level
    bear = df["close"] < low_level
    return bull.fillna(False), bear.fillna(False)


def fvg_zones(df: pd.DataFrame) -> pd.DataFrame:
    """Latest 3-bar imbalance visible at each bar, carried forward.

    Mirrors _last_fvg: bullish when low[i] > high[i-2], bearish when
    high[i] < low[i-2].
    """
    high, low = df["high"], df["low"]
    bull_gap = low > high.shift(2)
    bear_gap = high < low.shift(2)
    return pd.DataFrame(
        {
            "bull_top": low.where(bull_gap).ffill(),
            "bull_bottom": high.shift(2).where(bull_gap).ffill(),
            "bear_top": low.shift(2).where(bear_gap).ffill(),
            "bear_bottom": high.where(bear_gap).ffill(),
        },
        index=df.index,
    )


def detect_liquidity_sweep(
    df: pd.DataFrame, bias: Bias, lookback: int = 50, swing: int = 3
) -> StructureSignal:
    """Completed-bar liquidity sweep aligned with H1 bias."""
    if len(df) < lookback + swing * 2 + 5:
        return StructureSignal(False, reason="insufficient_bars")

    work = df.iloc[:-1].copy()  # drop forming bar
    sh, sl = _swing_highs_lows(work, left=swing, right=swing)
    i = len(work) - 1
    row = work.iloc[i]

    prior = work.iloc[max(0, i - lookback) : i]
    prior_sh = prior.loc[sh.loc[prior.index]]
    prior_sl = prior.loc[sl.loc[prior.index]]

    if bias == "BULLISH" and not prior_sl.empty:
        lvl = float(prior_sl["low"].iloc[-1])
        swept = float(row["low"]) < lvl and float(row["close"]) > lvl
        if swept:
            return StructureSignal(True, "BULLISH", lvl, float(row["low"]), "m15_sweep_low")
    if bias == "BEARISH" and not prior_sh.empty:
        lvl = float(prior_sh["high"].iloc[-1])
        swept = float(row["high"]) > lvl and float(row["close"]) < lvl
        if swept:
            return StructureSignal(True, "BEARISH", lvl, float(row["high"]), "m15_sweep_high")
    return StructureSignal(False, bias, reason="no_sweep")


def detect_choch(
    df: pd.DataFrame, bias: Bias, lookback: int = 40, swing: int = 2
) -> StructureSignal:
    """Change of Character on completed M5 bars."""
    if len(df) < lookback + 10:
        return StructureSignal(False, reason="insufficient_bars")

    work = df.iloc[:-1].copy()
    sh, sl = _swing_highs_lows(work, left=swing, right=swing)
    i = len(work) - 1
    close = float(work["close"].iloc[i])

    prior = work.iloc[max(0, i - lookback) : i]
    prior_sh = prior.loc[sh.loc[prior.index]]
    prior_sl = prior.loc[sl.loc[prior.index]]

    if bias == "BULLISH" and not prior_sh.empty:
        lvl = float(prior_sh["high"].iloc[-1])
        if close > lvl:
            return StructureSignal(True, "BULLISH", lvl, reason="m5_choch_bull")
    if bias == "BEARISH" and not prior_sl.empty:
        lvl = float(prior_sl["low"].iloc[-1])
        if close < lvl:
            return StructureSignal(True, "BEARISH", lvl, reason="m5_choch_bear")
    return StructureSignal(False, bias, reason="no_choch")


def _last_fvg(work: pd.DataFrame, bullish: bool) -> tuple[float, float, float] | None:
    """Return (top, bottom, mid) of latest FVG or None."""
    for i in range(len(work) - 1, 2, -1):
        c0 = work.iloc[i - 2]
        c2 = work.iloc[i]
        if bullish and float(c2["low"]) > float(c0["high"]):
            top = float(c2["low"])
            bottom = float(c0["high"])
            return top, bottom, (top + bottom) / 2.0
        if not bullish and float(c2["high"]) < float(c0["low"]):
            top = float(c0["low"])
            bottom = float(c2["high"])
            return top, bottom, (top + bottom) / 2.0
    return None


def detect_fvg_entry(
    df: pd.DataFrame, bias: Bias, atr: float | None = None, atr_sl_mult: float = 1.0
) -> StructureSignal:
    """M1 FVG touch entry on completed bars; SL beyond FVG extreme or ATR floor.

    `atr` should be the confirm-timeframe ATR (M5) so the floor clears M1 noise
    while entry timing stays on M1 (G21). Distance becomes
    max(FVG_extreme_distance, atr * atr_sl_mult).
    """
    if len(df) < 20:
        return StructureSignal(False, reason="insufficient_bars")

    work = df.iloc[:-1].copy()
    price = float(work["close"].iloc[-1])
    bullish = bias == "BULLISH"
    fvg = _last_fvg(work, bullish=bullish)
    if fvg is None:
        return StructureSignal(False, bias, reason="no_fvg")

    top, bottom, mid = fvg
    in_zone = bottom <= price <= top
    # also allow wick touch on last completed bar
    last = work.iloc[-1]
    touched = float(last["low"]) <= top and float(last["high"]) >= bottom
    if not (in_zone or touched):
        return StructureSignal(False, bias, mid, reason="price_not_in_fvg")

    if bullish:
        # Sprint 5: distance-space tightest-valid under dual floors.
        # min_dist = max(FVG_dist, ATR_floor) → unique SL that respects both
        # floors and is the shortest legal stop (never tighter than structure
        # or ATR). Equivalent to the historical min(bottom, price-atr*k) for
        # BUY, expressed explicitly so caps/risk layers can reason about it.
        fvg_dist = max(0.0, price - float(bottom))
        atr_dist = float(atr) * float(atr_sl_mult) if atr is not None and atr > 0 else 0.0
        min_dist = max(fvg_dist, atr_dist)
        structural_sl = price - min_dist
        return StructureSignal(True, "BULLISH", mid, structural_sl, "m1_fvg_bull")
    fvg_dist = max(0.0, float(top) - price)
    atr_dist = float(atr) * float(atr_sl_mult) if atr is not None and atr > 0 else 0.0
    min_dist = max(fvg_dist, atr_dist)
    structural_sl = price + min_dist
    return StructureSignal(True, "BEARISH", mid, structural_sl, "m1_fvg_bear")


def check_fvg_volume(df: pd.DataFrame, window: int = 20) -> bool:
    """Sprint 4 #1: completed FVG bar's tick volume >= SMA(volume, window).

    Drops the still-forming bar the same way `detect_fvg_entry` does, so the
    bar being volume-checked is the same bar the FVG touch is scored on.
    Fails open (returns True) when there is not enough history to compute the
    SMA yet — a cold-start data gap should not silently block every entry.
    """
    if df is None or len(df) < window + 2:
        return True
    work = df.iloc[:-1]
    volume_col = next(
        (c for c in ("tick_volume", "real_volume", "volume") if c in work.columns), None
    )
    if volume_col is None:
        return True
    vol = pd.to_numeric(work[volume_col], errors="coerce")
    sma = vol.rolling(window).mean()
    last_vol = vol.iloc[-1]
    last_sma = sma.iloc[-1]
    if pd.isna(last_vol) or pd.isna(last_sma):
        return True
    return float(last_vol) >= float(last_sma)


def detect_m15_break(
    df: pd.DataFrame, bias: Bias, lookback: int = 40, swing: int = 3
) -> StructureSignal:
    """Sprint 4 #1: optional M15 support/resistance break aligned with H1 bias.

    Same completed-bar swing logic as `detect_choch`, just run on M15 so it can
    be required alongside the M1 FVG (`sniper.require_m15_break_with_fvg`).
    """
    if len(df) < lookback + swing * 2 + 5:
        return StructureSignal(False, reason="insufficient_bars")

    work = df.iloc[:-1].copy()
    sh, sl = _swing_highs_lows(work, left=swing, right=swing)
    i = len(work) - 1
    close = float(work["close"].iloc[i])

    prior = work.iloc[max(0, i - lookback) : i]
    prior_sh = prior.loc[sh.loc[prior.index]]
    prior_sl = prior.loc[sl.loc[prior.index]]

    if bias == "BULLISH" and not prior_sh.empty:
        lvl = float(prior_sh["high"].iloc[-1])
        if close > lvl:
            return StructureSignal(True, "BULLISH", lvl, reason="m15_break_bull")
    if bias == "BEARISH" and not prior_sl.empty:
        lvl = float(prior_sl["low"].iloc[-1])
        if close < lvl:
            return StructureSignal(True, "BEARISH", lvl, reason="m15_break_bear")
    return StructureSignal(False, bias, reason="no_m15_break")


def detect_order_block(df: pd.DataFrame, bias: Bias, lookback: int = 30) -> StructureSignal:
    """Simple OB: last opposite candle before impulsive move."""
    if len(df) < lookback + 5:
        return StructureSignal(False, reason="insufficient_bars")
    work = df.iloc[:-1].tail(lookback)
    if bias == "BULLISH":
        # last bearish candle followed by strong bullish displacement
        for i in range(len(work) - 3, 1, -1):
            c = work.iloc[i]
            n1 = work.iloc[i + 1]
            if float(c["close"]) < float(c["open"]) and float(n1["close"]) > float(n1["open"]):
                if float(n1["close"]) > float(c["high"]):
                    return StructureSignal(
                        True,
                        "BULLISH",
                        float(c["low"]),
                        float(c["low"]),
                        "ob_bull",
                    )
    if bias == "BEARISH":
        for i in range(len(work) - 3, 1, -1):
            c = work.iloc[i]
            n1 = work.iloc[i + 1]
            if float(c["close"]) > float(c["open"]) and float(n1["close"]) < float(n1["open"]):
                if float(n1["close"]) < float(c["low"]):
                    return StructureSignal(
                        True,
                        "BEARISH",
                        float(c["high"]),
                        float(c["high"]),
                        "ob_bear",
                    )
    return StructureSignal(False, bias, reason="no_ob")


def smc_feature_frame(
    df: pd.DataFrame,
    prefix: str = "",
    swing: int = 3,
    lookback: int = 40,
    warmup: int = 20,
) -> pd.DataFrame:
    """Attach lightweight SMC numeric features for ML.

    A swing at index j is only confirmed once bar j+swing has closed, so row i
    may reference swings up to index i-swing and no further. Without that cut
    the flags near row i depend on bars after i, which leaks the future into
    both training data and the live feature row (G09).
    """
    out = df.copy()
    p = f"{prefix}_" if prefix else ""
    high, low, close = out["high"], out["low"], out["close"]
    atr = (high - low).rolling(14).mean().replace(0.0, np.nan)

    swing_low, swing_high = swing_levels(out, swing=swing, lookback=lookback)
    zones = fvg_zones(out)
    bull_mid = (zones["bull_top"] + zones["bull_bottom"]) / 2.0
    bear_mid = (zones["bear_top"] + zones["bear_bottom"]) / 2.0

    warm = pd.Series(np.arange(len(out)), index=out.index) >= warmup
    out[f"{p}sweep_bull"] = ((low < swing_low) & (swing_low <= close) & warm).astype(float)
    out[f"{p}sweep_bear"] = ((high > swing_high) & (swing_high >= close) & warm).astype(float)
    out[f"{p}choch_bull"] = ((close > swing_high) & warm).astype(float)
    out[f"{p}choch_bear"] = ((close < swing_low) & warm).astype(float)
    out[f"{p}fvg_bull_dist"] = ((close - bull_mid) / atr).where(warm)
    out[f"{p}fvg_bear_dist"] = ((bear_mid - close) / atr).where(warm)
    return out


SMC_FEATURE_COLS = [
    "sweep_bull",
    "sweep_bear",
    "choch_bull",
    "choch_bear",
    "fvg_bull_dist",
    "fvg_bear_dist",
]
