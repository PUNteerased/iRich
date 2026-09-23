"""Weekly retrain from raw CSVs merged with real closed-trade outcomes."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import joblib
import pandas as pd
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config
from src.training import (
    build_labelled_frame,
    experience_rows,
    inverse_frequency_weights,
    make_model,
)


def train_symbol(symbol: str, cfg, use_experience: bool = True) -> Path:
    data, feature_cols = build_labelled_frame(cfg, symbol)
    X = data[feature_cols]
    y = data["target"].astype(int)

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)

    n_experience = 0
    if use_experience:
        extra = experience_rows(cfg, symbol, feature_cols)
        n_experience = len(extra)
        if n_experience:
            # Real outcomes only join the training side; the holdout stays
            # untouched history so scores remain comparable across runs.
            X_train = pd.concat([X_train, extra[feature_cols]], ignore_index=True)
            y_train = pd.concat([y_train, extra["target"].astype(int)], ignore_index=True)

    model = make_model()
    model.fit(X_train, y_train, sample_weight=inverse_frequency_weights(y_train))

    pred = model.predict(X_test)
    print(f"\n=== {symbol} ===")
    print(f"train={len(X_train)} (experience rows: {n_experience}) test={len(X_test)}")
    print(classification_report(y_test, pred, digits=4, zero_division=0))

    proba = model.predict_proba(X_test)
    thr = cfg.min_probability
    mask = proba.max(axis=1) >= thr
    if mask.any():
        print(f"At prob>={thr}: n={mask.sum()}")
        print(classification_report(y_test[mask], pred[mask], digits=4, zero_division=0))
    else:
        print(f"At prob>={thr}: no samples reached the threshold")

    out = {
        "model": model,
        "feature_cols": feature_cols,
        "meta": {
            "symbol": symbol,
            "labels": cfg.get("labels"),
            "min_probability": cfg.min_probability,
            "train_rows": int(len(X_train)),
            "experience_rows": int(n_experience),
        },
    }
    path = cfg.model_path(symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(out, path)
    print(f"Saved {path}")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=None, help="Single symbol or all from config")
    parser.add_argument(
        "--no-experience",
        action="store_true",
        help="Train on raw history only, ignoring closed-trade outcomes",
    )
    args = parser.parse_args()
    cfg = load_config()
    symbols = [args.symbol] if args.symbol else cfg.symbols
    for s in symbols:
        try:
            train_symbol(s, cfg, use_experience=not args.no_experience)
        except Exception as exc:  # noqa: BLE001
            print(f"[ERROR] {s}: {exc}")


if __name__ == "__main__":
    main()
