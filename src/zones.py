"""Zone state + opposing-structure TP / structure-aware trailing (Phase 2.B)."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .smc import swing_levels


@dataclass
class StructureTargets:
    tp: float | None
    trail_sl: float | None
    rr_to_structure: float | None
    detail: str = ""


def opposing_structure_tp(
    df_h1: pd.DataFrame,
    *,
    side: str,
    entry: float,
    sl_distance: float,
    min_rr: float = 3.0,
    max_rr: float = 8.0,
) -> StructureTargets:
    """If next H1 opposing swing is farther than min_rr, extend TP up to max_rr."""
    if df_h1 is None or len(df_h1) < 30 or sl_distance <= 0:
        return StructureTargets(None, None, None, "insufficient_h1")
    work = df_h1.iloc[:-1]
    low_lvl, high_lvl = swing_levels(work)
    buy = str(side).upper() in ("BUY", "BULLISH", "LONG")
    if buy:
        level = high_lvl.dropna()
        if level.empty:
            return StructureTargets(None, None, None, "no_opposing_high")
        opp = float(level.iloc[-1])
        dist = opp - entry
    else:
        level = low_lvl.dropna()
        if level.empty:
            return StructureTargets(None, None, None, "no_opposing_low")
        opp = float(level.iloc[-1])
        dist = entry - opp
    if dist <= 0:
        return StructureTargets(None, None, None, "opposing_behind_entry")
    rr = dist / sl_distance
    if rr < min_rr:
        tp = entry + min_rr * sl_distance if buy else entry - min_rr * sl_distance
        return StructureTargets(tp, None, rr, "use_min_rr")
    rr_clamped = min(rr, max_rr)
    tp = entry + rr_clamped * sl_distance if buy else entry - rr_clamped * sl_distance
    return StructureTargets(float(tp), None, rr_clamped, "opposing_structure")


def structure_trail_sl(
    df_m5: pd.DataFrame,
    *,
    side: str,
    entry: float,
    current_sl: float,
    favor_r: float,
    activate_r: float = 1.5,
) -> float | None:
    """Trail behind last confirmed M5 swing once favor_r >= activate_r."""
    if favor_r < activate_r or df_m5 is None or len(df_m5) < 20:
        return None
    work = df_m5.iloc[:-1]
    low_lvl, high_lvl = swing_levels(work, swing=2, lookback=40)
    buy = str(side).upper() in ("BUY", "BULLISH", "LONG")
    if buy:
        level = low_lvl.dropna()
        if level.empty:
            return None
        candidate = float(level.iloc[-1])
        if candidate > current_sl and candidate < entry + favor_r * abs(entry - current_sl):
            return candidate
    else:
        level = high_lvl.dropna()
        if level.empty:
            return None
        candidate = float(level.iloc[-1])
        if (current_sl == 0 or candidate < current_sl) and candidate > entry - favor_r * abs(
            entry - current_sl
        ):
            return candidate
    return None
