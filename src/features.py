"""Technical indicator and price-action features (completed bars)."""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

try:
    import pandas_ta as ta
except ImportError:  # pragma: no cover
    ta = None


FEATURE_BASE = [
    "ema_20",
    "ema_50",
    "ema_200",
    "rsi_14",
    "atr_14",
    "macd",
    "macd_signal",
    "macd_hist",
    "bb_upper",
    "bb_mid",
    "bb_lower",
    "bb_pct",
    "candle_return",
    "upper_shadow_ratio",
    "lower_shadow_ratio",
    "body_ratio",
    "hour",
    "day_of_week",
    "atr_zscore",
]


def _ensure_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    colmap = {c.lower(): c for c in out.columns}
    for need in ("open", "high", "low", "close"):
        if need not in colmap and need.title() in out.columns:
            out.rename(columns={need.title(): need}, inplace=True)
            colmap = {c.lower(): c for c in out.columns}
    rename = {}
    for need in ("open", "high", "low", "close", "tick_volume", "real_volume", "volume", "time"):
        if need in colmap and colmap[need] != need:
            rename[colmap[need]] = need
    if rename:
        out.rename(columns=rename, inplace=True)
    if "volume" not in out.columns:
        if "tick_volume" in out.columns:
            out["volume"] = out["tick_volume"]
        elif "real_volume" in out.columns:
            out["volume"] = out["real_volume"]
        else:
            out["volume"] = 0.0
    if "time" in out.columns:
        out["time"] = pd.to_datetime(out["time"], utc=True, errors="coerce")
        out = out.sort_values("time").reset_index(drop=True)
    return out


def _ema(series: pd.Series, length: int) -> pd.Series:
    if ta is not None:
        return ta.ema(series, length=length)
    return series.ewm(span=length, adjust=False).mean()


def _rsi(series: pd.Series, length: int = 14) -> pd.Series:
    if ta is not None:
        return ta.rsi(series, length=length)
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(length).mean()
    loss = (-delta.clip(upper=0)).rolling(length).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    if ta is not None:
        return ta.atr(df["high"], df["low"], df["close"], length=length)
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(length).mean()


def build_features(df: pd.DataFrame, prefix: str = "") -> tuple[pd.DataFrame, list[str]]:
    """
    Build indicator features. Returns dataframe and feature column names.
    Does not drop NaNs so callers can align multi-TF frames first.
    """
    out = _ensure_ohlcv(df)
    p = f"{prefix}_" if prefix else ""

    out[f"{p}ema_20"] = _ema(out["close"], 20)
    out[f"{p}ema_50"] = _ema(out["close"], 50)
    out[f"{p}ema_200"] = _ema(out["close"], 200)
    out[f"{p}rsi_14"] = _rsi(out["close"], 14)
    out[f"{p}atr_14"] = _atr(out, 14)

    if ta is not None:
        macd = ta.macd(out["close"])
        if macd is not None and not macd.empty:
            out[f"{p}macd"] = macd.iloc[:, 0]
            out[f"{p}macd_hist"] = macd.iloc[:, 1]
            out[f"{p}macd_signal"] = macd.iloc[:, 2]
        else:
            out[f"{p}macd"] = np.nan
            out[f"{p}macd_hist"] = np.nan
            out[f"{p}macd_signal"] = np.nan
        bb = ta.bbands(out["close"], length=20)
        if bb is not None and not bb.empty:
            # pandas-ta column order can vary; pick by name
            cols = list(bb.columns)
            upper = next((c for c in cols if "BBU" in c), cols[0])
            mid = next((c for c in cols if "BBM" in c), cols[1] if len(cols) > 1 else cols[0])
            lower = next((c for c in cols if "BBL" in c), cols[-1])
            out[f"{p}bb_upper"] = bb[upper]
            out[f"{p}bb_mid"] = bb[mid]
            out[f"{p}bb_lower"] = bb[lower]
        else:
            out[f"{p}bb_upper"] = np.nan
            out[f"{p}bb_mid"] = np.nan
            out[f"{p}bb_lower"] = np.nan
    else:
        ema12 = out["close"].ewm(span=12, adjust=False).mean()
        ema26 = out["close"].ewm(span=26, adjust=False).mean()
        out[f"{p}macd"] = ema12 - ema26
        out[f"{p}macd_signal"] = out[f"{p}macd"].ewm(span=9, adjust=False).mean()
        out[f"{p}macd_hist"] = out[f"{p}macd"] - out[f"{p}macd_signal"]
        mid = out["close"].rolling(20).mean()
        std = out["close"].rolling(20).std()
        out[f"{p}bb_mid"] = mid
        out[f"{p}bb_upper"] = mid + 2 * std
        out[f"{p}bb_lower"] = mid - 2 * std

    bb_range = (out[f"{p}bb_upper"] - out[f"{p}bb_lower"]).replace(0, np.nan)
    out[f"{p}bb_pct"] = (out["close"] - out[f"{p}bb_lower"]) / bb_range

    out[f"{p}candle_return"] = out["close"].pct_change()
    body = (out["close"] - out["open"]).abs()
    upper_shadow = out["high"] - out[["open", "close"]].max(axis=1)
    lower_shadow = out[["open", "close"]].min(axis=1) - out["low"]
    candle_range = (out["high"] - out["low"]).replace(0, np.nan)
    body_safe = body.replace(0, np.nan)
    # Prefer body-relative shadows; fall back to range so doji bars stay usable
    out[f"{p}upper_shadow_ratio"] = (upper_shadow / body_safe).fillna(upper_shadow / candle_range)
    out[f"{p}lower_shadow_ratio"] = (lower_shadow / body_safe).fillna(lower_shadow / candle_range)
    out[f"{p}body_ratio"] = body / candle_range

    if "time" in out.columns:
        out[f"{p}hour"] = out["time"].dt.hour
        out[f"{p}day_of_week"] = out["time"].dt.dayofweek
    else:
        out[f"{p}hour"] = 0
        out[f"{p}day_of_week"] = 0

    atr = out[f"{p}atr_14"]
    atr_mean = atr.rolling(100, min_periods=20).mean()
    atr_std = atr.rolling(100, min_periods=20).std().replace(0, np.nan)
    out[f"{p}atr_zscore"] = (atr - atr_mean) / atr_std

    feature_cols = [f"{p}{c}" if p else c for c in FEATURE_BASE]
    return out, feature_cols


def drop_feature_nans(df: pd.DataFrame, feature_cols: Iterable[str]) -> pd.DataFrame:
    cols = list(feature_cols)
    return df.dropna(subset=cols).reset_index(drop=True)
