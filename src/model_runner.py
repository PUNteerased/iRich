"""Load sniper models and build live feature vectors."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from .audit_log import UNKNOWN_MODEL, ModelIdentity, file_identity
from .features import build_features, drop_feature_nans
from .smc import SMC_FEATURE_COLS, smc_feature_frame

logger = logging.getLogger(__name__)


class ModelBundle:
    def __init__(
        self,
        model: Any,
        feature_cols: list[str],
        meta: dict[str, Any] | None = None,
        identity: ModelIdentity | None = None,
    ) -> None:
        self.model = model
        self.feature_cols = feature_cols
        self.meta = meta or {}
        self.identity = identity or UNKNOWN_MODEL

    def predict_proba_row(self, row: pd.Series) -> np.ndarray:
        x = row[self.feature_cols].to_frame().T
        proba = self.model.predict_proba(x)[0]
        # Ensure length-3 [WAIT, BUY, SELL]
        classes = list(getattr(self.model, "classes_", [0, 1, 2]))
        out = np.zeros(3, dtype=float)
        for i, cls in enumerate(classes):
            idx = int(cls)
            if 0 <= idx < 3:
                out[idx] = float(proba[i])
        return out


class ModelRunner:
    def __init__(self, models_dir: Path) -> None:
        self.models_dir = Path(models_dir)
        self.models: dict[str, ModelBundle] = {}

    def load_symbol(self, symbol: str) -> ModelBundle | None:
        path = self.models_dir / f"{symbol.lower()}_sniper.pkl"
        if not path.exists():
            logger.warning("Model missing: %s", path)
            return None
        obj = joblib.load(path)
        identity = file_identity(path)
        if isinstance(obj, dict):
            bundle = ModelBundle(
                obj["model"], obj["feature_cols"], obj.get("meta", {}), identity
            )
        else:
            # bare model fallback
            bundle = ModelBundle(obj, [], {}, identity)
        self.models[symbol] = bundle
        logger.info(
            "Loaded %s: %s mtime=%s sha=%s features=%d",
            symbol,
            identity.model_path,
            identity.model_mtime,
            identity.model_sha256,
            len(bundle.feature_cols),
        )
        return bundle

    def load_all(self, symbols: list[str]) -> None:
        for s in symbols:
            self.load_symbol(s)

    def get(self, symbol: str) -> ModelBundle | None:
        return self.models.get(symbol) or self.load_symbol(symbol)


def build_live_feature_row(
    df_h1: pd.DataFrame,
    df_m15: pd.DataFrame,
    df_m5: pd.DataFrame,
    df_m1: pd.DataFrame,
) -> tuple[pd.Series, list[str]]:
    """
    Build a single as-of feature row from multi-TF frames (no lookahead).
    Uses last completed bar on each TF.
    """
    h1, h1_cols = build_features(df_h1, prefix="h1")
    m15, m15_cols = build_features(df_m15, prefix="m15")
    m5, m5_cols = build_features(df_m5, prefix="m5")
    m1, m1_cols = build_features(df_m1, prefix="m1")
    m1 = smc_feature_frame(m1, prefix="m1")
    smc_cols = [f"m1_{c}" for c in SMC_FEATURE_COLS]

    def last_completed(df: pd.DataFrame) -> pd.Series:
        return df.iloc[-2] if len(df) >= 2 else df.iloc[-1]

    row = {}
    for col in h1_cols:
        row[col] = last_completed(h1)[col]
    for col in m15_cols:
        row[col] = last_completed(m15)[col]
    for col in m5_cols:
        row[col] = last_completed(m5)[col]
    for col in m1_cols + smc_cols:
        row[col] = last_completed(m1)[col]

    series = pd.Series(row)
    feature_cols = h1_cols + m15_cols + m5_cols + m1_cols + smc_cols
    return series, feature_cols


def build_training_frame(
    df_h1: pd.DataFrame,
    df_m15: pd.DataFrame,
    df_m5: pd.DataFrame,
    df_m1: pd.DataFrame,
) -> tuple[pd.DataFrame, list[str]]:
    """
    Merge higher-TF features onto M1 index via backward asof merge.
    """
    h1, h1_cols = build_features(df_h1, prefix="h1")
    m15, m15_cols = build_features(df_m15, prefix="m15")
    m5, m5_cols = build_features(df_m5, prefix="m5")
    m1, m1_cols = build_features(df_m1, prefix="m1")
    m1 = smc_feature_frame(m1, prefix="m1")
    smc_cols = [f"m1_{c}" for c in SMC_FEATURE_COLS]

    base = m1[["time", "open", "high", "low", "close", "volume"] + m1_cols + smc_cols].copy()
    base = base.sort_values("time")

    def asof_merge(left: pd.DataFrame, right: pd.DataFrame, cols: list[str], pref: str) -> pd.DataFrame:
        r = right[["time"] + cols].sort_values("time").copy()
        # shift higher TF by 1 completed bar to avoid lookahead of forming candle
        r["time"] = r["time"] + pd.Timedelta(seconds=1)
        return pd.merge_asof(left, r, on="time", direction="backward")

    # Use previous completed higher-TF bar: shift features down 1 before merge
    h1_shift = h1.copy()
    h1_shift[h1_cols] = h1_shift[h1_cols].shift(1)
    m15_shift = m15.copy()
    m15_shift[m15_cols] = m15_shift[m15_cols].shift(1)
    m5_shift = m5.copy()
    m5_shift[m5_cols] = m5_shift[m5_cols].shift(1)

    out = asof_merge(base, h1_shift, h1_cols, "h1")
    out = asof_merge(out, m15_shift, m15_cols, "m15")
    out = asof_merge(out, m5_shift, m5_cols, "m5")

    feature_cols = h1_cols + m15_cols + m5_cols + m1_cols + smc_cols
    out = drop_feature_nans(out, feature_cols)
    return out, feature_cols
