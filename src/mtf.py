"""Multi-timeframe alignment: H1 → M15 → M5 → M1."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .features import build_features
from .smc import (
    Bias,
    StructureSignal,
    check_fvg_volume,
    detect_choch,
    detect_fvg_entry,
    detect_liquidity_sweep,
    detect_m15_break,
    detect_order_block,
)


@dataclass
class MTFResult:
    bias: Bias
    sweep: StructureSignal
    choch: StructureSignal
    fvg: StructureSignal
    aligned: bool
    entry: float | None = None
    sl: float | None = None
    reasons: list[str] = field(default_factory=list)
    # Sprint 4 #1: hard confirmations added alongside the FVG.
    volume_ok: bool = True
    m15_break: StructureSignal | None = None
    # Sprint 4 #1: sum of sweep/choch bonuses when require_full is False
    # (soft-MTF mode) — fed into fusion.fuse_signal's score, never into the
    # hard alignment gate.
    soft_score: float = 0.0


def get_h1_bias(df_h1: pd.DataFrame, ema_len: int = 200) -> Bias:
    """H1 macro bias from completed bar vs EMA200."""
    if len(df_h1) < ema_len + 5:
        return "NEUTRAL"
    feat, _ = build_features(df_h1)
    # use last completed bar
    row = feat.iloc[-2] if len(feat) >= 2 else feat.iloc[-1]
    close = float(row["close"])
    ema = float(row["ema_200"])
    if not pd.notna(ema):
        return "NEUTRAL"
    ob = detect_order_block(df_h1, "BULLISH" if close > ema else "BEARISH")
    if close > ema:
        # optional OB agreement soft — bias still bullish above EMA
        return "BULLISH"
    if close < ema:
        return "BEARISH"
    return "NEUTRAL"


def _completed_atr(df: pd.DataFrame) -> float | None:
    """ATR(14) of the last completed bar on this timeframe (G21: M5 floor)."""
    if df is None or len(df) < 20:
        return None
    feat, _ = build_features(df)
    if feat.empty:
        return None
    completed = feat.iloc[-2] if len(feat) >= 2 else feat.iloc[-1]
    atr = completed.get("atr_14")
    if atr is None or not pd.notna(atr):
        return None
    value = float(atr)
    return value if value > 0 else None


def evaluate_mtf(
    df_h1: pd.DataFrame,
    df_m15: pd.DataFrame,
    df_m5: pd.DataFrame,
    df_m1: pd.DataFrame,
    atr_sl_mult: float = 1.0,
    require_full: bool = True,
    atr_sl_timeframe: str = "M5",
    require_fvg_volume: bool = True,
    volume_sma_window: int = 20,
    require_m15_break_with_fvg: bool = False,
    soft_sweep_bonus: float = 0.05,
    soft_choch_bonus: float = 0.05,
) -> MTFResult:
    """MTF alignment. Structural SL ATR floor TF is configurable (G21 default M5).

    Sprint 4 #1: `require_full` switches between two hard-gate shapes —
    - True (AND-stack, default/backward compatible): sweep AND choch AND FVG
      (+ volume, + optional M15 break) must all pass.
    - False (soft-MTF): only H1 bias + M1 FVG (+ volume, + optional M15 break)
      are hard; sweep/choch instead add a `soft_score` bonus consumed by
      `fusion.fuse_signal`, never a hard reject.
    """
    bias = get_h1_bias(df_h1)
    reasons: list[str] = []
    if bias == "NEUTRAL":
        return MTFResult(
            bias=bias,
            sweep=StructureSignal(False, reason="neutral_bias"),
            choch=StructureSignal(False, reason="neutral_bias"),
            fvg=StructureSignal(False, reason="neutral_bias"),
            aligned=False,
            reasons=["h1_neutral"],
        )

    sweep = detect_liquidity_sweep(df_m15, bias)
    choch = detect_choch(df_m5, bias)

    m1_feat, _ = build_features(df_m1)
    completed = m1_feat.iloc[-2] if len(m1_feat) >= 2 else m1_feat.iloc[-1]
    tf = str(atr_sl_timeframe).upper()
    atr_source = {"M1": df_m1, "M5": df_m5, "M15": df_m15, "H1": df_h1}.get(tf, df_m5)
    atr = _completed_atr(atr_source)
    fvg = detect_fvg_entry(df_m1, bias, atr=atr, atr_sl_mult=atr_sl_mult)

    volume_ok = check_fvg_volume(df_m1, window=volume_sma_window) if require_fvg_volume else True
    if require_m15_break_with_fvg:
        m15_break = detect_m15_break(df_m15, bias)
    else:
        m15_break = StructureSignal(True, bias, reason="m15_break_not_required")

    if not sweep.ok:
        reasons.append(sweep.reason or "sweep_fail")
    if not choch.ok:
        reasons.append(choch.reason or "choch_fail")
    if not fvg.ok:
        reasons.append(fvg.reason or "fvg_fail")
    if require_fvg_volume and not volume_ok:
        reasons.append("fvg_volume_fail")
    if require_m15_break_with_fvg and not m15_break.ok:
        reasons.append(m15_break.reason or "m15_break_fail")

    # Sprint 4 #1: FVG + volume + (optional) M15 break are hard regardless of
    # require_full — only sweep/choch move between hard and soft.
    hard_fvg_ok = fvg.ok and volume_ok and (m15_break.ok if require_m15_break_with_fvg else True)

    soft_score = 0.0
    if require_full:
        aligned = sweep.ok and choch.ok and hard_fvg_ok
    else:
        aligned = hard_fvg_ok and bias != "NEUTRAL"
        if sweep.ok:
            soft_score += soft_sweep_bonus
        if choch.ok:
            soft_score += soft_choch_bonus

    entry = float(completed["close"]) if aligned else None
    sl = fvg.sl if aligned else None

    return MTFResult(
        bias=bias,
        sweep=sweep,
        choch=choch,
        fvg=fvg,
        aligned=aligned,
        entry=entry,
        sl=sl,
        reasons=reasons,
        volume_ok=volume_ok,
        m15_break=m15_break,
        soft_score=round(soft_score, 6),
    )


def rates_to_df(rates: Any) -> pd.DataFrame:
    df = pd.DataFrame(rates)
    if df.empty:
        return df
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df
