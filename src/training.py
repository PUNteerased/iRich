"""Shared dataset and model construction for retraining and walk-forward.

Both scripts build their data here so a change to labelling or features cannot
apply to one evaluation path and not the other.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from xgboost import XGBClassifier

from .labels import triple_barrier_labels
from .learning.replay import ExperienceBuffer
from .model_runner import build_training_frame

logger = logging.getLogger(__name__)

TIMEFRAMES = ("h1", "m15", "m5", "m1")

# Class ids: 0 = WAIT, 1 = BUY, 2 = SELL
SIDE_TO_CLASS = {"BUY": 1, "SELL": 2}


def load_raw_csv(raw_dir: Path, symbol: str, tf: str) -> pd.DataFrame:
    path = raw_dir / f"{symbol.lower()}_{tf}.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_csv(path)
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], utc=True)
    return df


def build_labelled_frame(cfg: Any, symbol: str) -> tuple[pd.DataFrame, list[str]]:
    """Merged multi-timeframe features with triple-barrier targets."""
    raw_dir = cfg.resolve_path("paths", "data_raw")
    frames = {tf: load_raw_csv(raw_dir, symbol, tf) for tf in TIMEFRAMES}
    frame, feature_cols = build_training_frame(
        frames["h1"], frames["m15"], frames["m5"], frames["m1"]
    )
    if "m1_atr_14" not in frame.columns:
        raise RuntimeError("m1_atr_14 missing")

    frame = frame.copy()
    # G21: label barriers use the same ATR scale as live structural SL.
    atr_sl_by_symbol = cfg.get("labels", "atr_sl_by_symbol", default={}) or {}
    label_tf_by = cfg.get("labels", "atr_sl_timeframe_by_symbol", default={}) or {}
    atr_tf = str(
        label_tf_by.get(symbol)
        or cfg.get("labels", "atr_sl_timeframe", default=None)
        or cfg.atr_sl_timeframe(symbol)
    ).lower()
    atr_col_pref = f"{atr_tf}_atr_14"
    if atr_col_pref in frame.columns:
        frame["atr_14"] = frame[atr_col_pref].fillna(frame["m1_atr_14"])
    else:
        frame["atr_14"] = frame["m1_atr_14"]
    if symbol in atr_sl_by_symbol:
        atr_sl = float(atr_sl_by_symbol[symbol])
    else:
        atr_sl = float(cfg.atr_sl_mult(symbol))
    frame["target"] = triple_barrier_labels(
        frame,
        atr_col="atr_14",
        atr_sl=atr_sl,
        atr_tp=float(cfg.get("labels", "atr_tp", default=3.0)),
        horizon=int(cfg.get("labels", "horizon_m1", default=45)),
    )
    data = frame.dropna(subset=feature_cols + ["target"]).reset_index(drop=True)
    return data, feature_cols


def experience_rows(
    cfg: Any,
    symbol: str,
    feature_cols: list[str],
) -> pd.DataFrame:
    """Real closed trades from the experience buffer as training rows (G06).

    A trade that reached TP labels its own side. A trade that hit SL labels WAIT
    rather than the opposite side: the entry was wrong, but nothing observed says
    the reverse trade would have won.
    """
    buffer = ExperienceBuffer(
        cfg.resolve_path("paths", "experience"),
        max_lines_per_day=int(cfg.get("learning", "max_lines_per_day", default=500)),
    )
    rows: list[dict[str, Any]] = []
    for trade in buffer.labelled_trades(symbol):
        features = trade.get("features") or {}
        if not all(col in features for col in feature_cols):
            continue
        outcome = trade.get("outcome")
        side = str(trade.get("side", "")).upper()
        if outcome == "tp" and side in SIDE_TO_CLASS:
            target = SIDE_TO_CLASS[side]
        elif outcome == "sl":
            target = 0
        else:
            continue
        record = {col: features[col] for col in feature_cols}
        record["target"] = target
        rows.append(record)
    if not rows:
        return pd.DataFrame(columns=feature_cols + ["target"])
    return pd.DataFrame(rows).dropna(subset=feature_cols + ["target"])


def inverse_frequency_weights(y: pd.Series) -> pd.Series:
    """WAIT dominates triple-barrier labels; rebalance so it cannot swamp fitting."""
    classes, counts = np.unique(y, return_counts=True)
    total = counts.sum()
    weights = {int(c): float(total / (len(classes) * cnt)) for c, cnt in zip(classes, counts)}
    return y.map(weights)


def make_model() -> XGBClassifier:
    return XGBClassifier(
        n_estimators=300,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        objective="multi:softprob",
        num_class=3,
        tree_method="hist",
        eval_metric="mlogloss",
    )
