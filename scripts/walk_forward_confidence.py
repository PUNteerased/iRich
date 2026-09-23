"""Walk-forward evaluation of online ConfidenceStore updates (P0-3).

Compares a fixed min_probability threshold against an online weight path that
raises the effective threshold after losses (never lowers below base).

    python scripts/walk_forward_confidence.py
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
from src.learning.confidence import ConfidenceStore
from src.training import build_labelled_frame, inverse_frequency_weights, make_model

TRADE_CLASSES = (1, 2)


def simulate_online_confidence(
    y_true: np.ndarray,
    pred: np.ndarray,
    proba: np.ndarray,
    base_thr: float,
    symbol: str,
    alpha: float,
    w_min: float,
    w_max: float,
    rr: float,
) -> dict:
    """Chronological path: apply confidence after each hypothetical fill."""
    store = ConfidenceStore(
        path=ROOT / "models" / "_tmp_confidence_wf.json",
        symbols=[symbol],
        alpha=alpha,
        w_min=w_min,
        w_max=w_max,
    )
    store.weights[symbol] = 1.0

    fixed_hits = []
    online_hits = []
    fixed_correct = []
    online_correct = []

    for i in range(len(y_true)):
        pmax = float(proba[i].max())
        side = int(pred[i])
        if side not in TRADE_CLASSES:
            continue
        # Fixed threshold
        if pmax >= base_thr:
            fixed_hits.append(i)
            fixed_correct.append(1 if int(y_true[i]) == side else 0)
        # Online threshold
        thr = store.effective_min_prob(base_thr, symbol)
        if pmax >= thr:
            online_hits.append(i)
            ok = int(y_true[i]) == side
            online_correct.append(1 if ok else 0)
            outcome = "tp" if ok else "sl"
            store.update(symbol, outcome, rr=rr)

    def _stats(correct: list[int]) -> dict:
        if not correct:
            return {"n": 0, "precision": 0.0}
        return {"n": len(correct), "precision": round(float(np.mean(correct)), 4)}

    # Cleanup temp file
    try:
        store.path.unlink(missing_ok=True)
    except OSError:
        pass

    return {
        "fixed": _stats(fixed_correct),
        "online": _stats(online_correct),
        "recommend_enable": (
            _stats(online_correct)["n"] > 0
            and _stats(online_correct)["precision"] >= _stats(fixed_correct)["precision"]
        ),
    }


def evaluate_symbol(symbol: str, cfg) -> dict:
    data, feature_cols = build_labelled_frame(cfg, symbol)
    X = data[feature_cols]
    y = data["target"].astype(int)
    split = int(len(data) * 0.7)
    if split < 100 or len(data) - split < 50:
        return {"symbol": symbol, "error": "insufficient_rows"}

    model = make_model()
    model.fit(
        X.iloc[:split],
        y.iloc[:split],
        sample_weight=inverse_frequency_weights(y.iloc[:split]),
    )
    X_test = X.iloc[split:]
    y_test = y.iloc[split:].to_numpy()
    proba = model.predict_proba(X_test)
    pred = model.predict(X_test)

    comparison = simulate_online_confidence(
        y_test,
        pred,
        proba,
        cfg.min_probability,
        symbol,
        alpha=float(cfg.get("learning", "alpha", default=0.1)),
        w_min=float(cfg.get("learning", "w_min", default=0.3)),
        w_max=float(cfg.get("learning", "w_max", default=2.0)),
        rr=cfg.rr,
    )
    return {"symbol": symbol, "test_rows": int(len(y_test)), **comparison}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--out", default="data/confidence_walk_forward.json")
    args = parser.parse_args()
    cfg = load_config()
    symbols = args.symbols or cfg.symbols
    rows = []
    for symbol in symbols:
        print(f"\n=== {symbol} confidence WF ===")
        row = evaluate_symbol(symbol, cfg)
        rows.append(row)
        print(json.dumps(row, indent=2))

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    enable_any = any(r.get("recommend_enable") for r in rows if "error" not in r)
    payload = {
        "results": rows,
        "recommend_enable_online_confidence": enable_any,
        "note": (
            "Enable learning.online_confidence only when recommend_enable is true "
            "for symbols you trade; default remains fixed 0.75 until then."
        ),
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")
    print(f"recommend_enable_online_confidence={enable_any}")


if __name__ == "__main__":
    main()
