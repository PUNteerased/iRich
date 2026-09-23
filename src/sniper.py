"""Sniper entry orchestration: MTF + ML probability + ATR sanity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .mtf import MTFResult, evaluate_mtf
from .features import build_features
from .reject_codes import ACCEPTED, Reject


@dataclass
class SniperCandidate:
    signal: str
    confidence: float
    entry: float
    raw_sl: float
    bias: str
    mtf: MTFResult
    feature_row: pd.Series | None = None
    reason: str = ""
    code: str = ACCEPTED

    @property
    def sl_distance(self) -> float:
        """Structural SL distance; the price anchor is chosen later (I-12)."""
        return abs(self.entry - self.raw_sl)

    @property
    def soft_score(self) -> float:
        """Sprint 4 #1: sweep/choch bonus when soft-MTF is active, else 0.0."""
        return self.mtf.soft_score if self.mtf is not None else 0.0


def check_atr_sanity(df_m1: pd.DataFrame, zscore_max: float = 2.0) -> bool:
    feat, _ = build_features(df_m1)
    row = feat.iloc[-2] if len(feat) >= 2 else feat.iloc[-1]
    z = row.get("atr_zscore", 0.0)
    if pd.isna(z):
        return True
    return abs(float(z)) <= zscore_max


def build_sniper_candidate(
    df_h1: pd.DataFrame,
    df_m15: pd.DataFrame,
    df_m5: pd.DataFrame,
    df_m1: pd.DataFrame,
    probs: np.ndarray | list[float],
    require_full_mtf: bool = True,
    atr_sl_mult: float = 1.0,
    atr_zscore_max: float = 2.0,
    atr_sl_timeframe: str = "M5",
    require_fvg_volume: bool = True,
    volume_sma_window: int = 20,
    require_m15_break_with_fvg: bool = False,
    soft_sweep_bonus: float = 0.05,
    soft_choch_bonus: float = 0.05,
) -> SniperCandidate:
    """
    probs: [p_wait, p_buy, p_sell]
    """
    mtf = evaluate_mtf(
        df_h1,
        df_m15,
        df_m5,
        df_m1,
        atr_sl_mult=atr_sl_mult,
        require_full=require_full_mtf,
        atr_sl_timeframe=atr_sl_timeframe,
        require_fvg_volume=require_fvg_volume,
        volume_sma_window=volume_sma_window,
        require_m15_break_with_fvg=require_m15_break_with_fvg,
        soft_sweep_bonus=soft_sweep_bonus,
        soft_choch_bonus=soft_choch_bonus,
    )
    p = np.asarray(probs, dtype=float)
    p_wait, p_buy, p_sell = float(p[0]), float(p[1]), float(p[2])

    if not check_atr_sanity(df_m1, atr_zscore_max):
        return SniperCandidate(
            "WAIT",
            max(p_buy, p_sell),
            0.0,
            0.0,
            mtf.bias,
            mtf,
            reason="atr_abnormal",
            code=Reject.ATR_ABNORMAL,
        )

    if mtf.bias == "BULLISH":
        side, conf = "BUY", p_buy
    elif mtf.bias == "BEARISH":
        side, conf = "SELL", p_sell
    else:
        return SniperCandidate(
            "WAIT",
            max(p_buy, p_sell),
            0.0,
            0.0,
            mtf.bias,
            mtf,
            reason="neutral_bias",
            code=Reject.NEUTRAL_BIAS,
        )

    if not mtf.aligned or mtf.entry is None or mtf.sl is None:
        return SniperCandidate(
            "WAIT",
            conf,
            0.0,
            0.0,
            mtf.bias,
            mtf,
            reason="mtf_not_aligned",
            code=Reject.MTF,
        )

    return SniperCandidate(
        signal=side,
        confidence=conf,
        entry=mtf.entry,
        raw_sl=mtf.sl,
        bias=mtf.bias,
        mtf=mtf,
        reason="candidate",
    )
