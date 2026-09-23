"""Walk-forward validation across rolling time windows (G15).

A single chronological holdout says how the model did on one stretch of market.
Walk-forward retrains on an expanding history and scores each subsequent window,
which is the only way to see whether performance survives a regime change.

Scores at the live probability threshold matter more than the overall report:
they describe the subset the bot would actually have traded.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from sklearn.metrics import precision_recall_fscore_support

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config
from src.training import build_labelled_frame, inverse_frequency_weights, make_model

TRADE_CLASSES = (1, 2)  # BUY, SELL


def fold_bounds(n: int, folds: int, min_train_frac: float) -> list[tuple[int, int, int]]:
    """(train_end, test_start, test_end) for each expanding-window fold."""
    first_train = int(n * min_train_frac)
    if first_train <= 0 or first_train >= n:
        return []
    test_size = (n - first_train) // folds
    if test_size <= 0:
        return []
    bounds = []
    for k in range(folds):
        train_end = first_train + k * test_size
        test_end = train_end + test_size if k < folds - 1 else n
        if test_end <= train_end:
            break
        bounds.append((train_end, train_end, test_end))
    return bounds


def evaluate_symbol(symbol: str, cfg, folds: int, min_train_frac: float) -> dict:
    data, feature_cols = build_labelled_frame(cfg, symbol)
    X = data[feature_cols]
    y = data["target"].astype(int)
    thr = cfg.min_probability

    results = []
    for index, (train_end, test_start, test_end) in enumerate(
        fold_bounds(len(data), folds, min_train_frac), start=1
    ):
        X_train, y_train = X.iloc[:train_end], y.iloc[:train_end]
        X_test, y_test = X.iloc[test_start:test_end], y.iloc[test_start:test_end]

        model = make_model()
        model.fit(X_train, y_train, sample_weight=inverse_frequency_weights(y_train))

        proba = model.predict_proba(X_test)
        pred = model.predict(X_test)
        mask = proba.max(axis=1) >= thr
        traded = mask & np.isin(pred, TRADE_CLASSES)

        if traded.any():
            precision, recall, _, _ = precision_recall_fscore_support(
                y_test[traded],
                pred[traded],
                labels=list(TRADE_CLASSES),
                average="micro",
                zero_division=0,
            )
        else:
            precision = recall = 0.0

        fold = {
            "fold": index,
            "train_rows": int(train_end),
            "test_rows": int(test_end - test_start),
            "test_from": str(data["time"].iloc[test_start]) if "time" in data else None,
            "test_to": str(data["time"].iloc[test_end - 1]) if "time" in data else None,
            "signals_at_threshold": int(traded.sum()),
            "signal_rate": round(float(traded.sum()) / max(1, len(y_test)), 5),
            "precision_at_threshold": round(float(precision), 4),
            "recall_at_threshold": round(float(recall), 4),
            "accuracy_all": round(float((pred == y_test.to_numpy()).mean()), 4),
        }
        results.append(fold)
        print(
            f"  fold {index}: train={fold['train_rows']} test={fold['test_rows']} "
            f"signals={fold['signals_at_threshold']} "
            f"precision@{thr}={fold['precision_at_threshold']} "
            f"acc={fold['accuracy_all']}"
        )

    precisions = [f["precision_at_threshold"] for f in results if f["signals_at_threshold"]]
    return {
        "symbol": symbol,
        "rows": int(len(data)),
        "threshold": thr,
        "folds": results,
        "folds_with_signals": len(precisions),
        "mean_precision_at_threshold": round(float(np.mean(precisions)), 4) if precisions else 0.0,
        "min_precision_at_threshold": round(float(np.min(precisions)), 4) if precisions else 0.0,
        "total_signals": int(sum(f["signals_at_threshold"] for f in results)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--min-train-frac", type=float, default=0.5)
    parser.add_argument("--out", default="data/walk_forward_results.json")
    args = parser.parse_args()

    cfg = load_config()
    symbols = args.symbols or cfg.symbols
    summaries = []
    for symbol in symbols:
        print(f"\n=== {symbol} ===")
        try:
            summary = evaluate_symbol(symbol, cfg, args.folds, args.min_train_frac)
        except Exception as exc:  # noqa: BLE001
            print(f"[ERROR] {symbol}: {exc}")
            continue
        summaries.append(summary)
        print(
            f"  mean precision@{summary['threshold']}="
            f"{summary['mean_precision_at_threshold']} "
            f"min={summary['min_precision_at_threshold']} "
            f"signals={summary['total_signals']}"
        )

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
