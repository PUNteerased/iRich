"""Bootstrap spread caps from M1 bar ranges when live multi-session calibrate is pending.

Produces data/spread_calibration.json so Phase 1 is not permanently fail-closed.
Re-run scripts/calibrate_spread.py across London/NY, Asian, and news sessions and
overwrite these bootstrap values before trusting live fills.

    python scripts/bootstrap_spread_calibration.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config
from src.market_data import load_symbol_specs


def estimate_points(df: pd.DataFrame, point: float) -> dict:
    """Use fraction of M1 range as a conservative spread proxy."""
    if point <= 0 or df.empty:
        return {}
    ranges = ((df["high"] - df["low"]) / point).replace([np.inf, -np.inf], np.nan).dropna()
    if ranges.empty:
        return {}
    samples = ranges.clip(lower=0).tolist()
    # Cap at p50 of bar range / 8 — conservative enough for fail-open bootstrap.
    p50 = float(np.percentile(samples, 50))
    max_points = max(1.0, round(p50 / 8.0, 2))
    # Hard ceilings so bootstrap never opens the gate to absurd spreads.
    hard_cap = {
        "EURUSD": 25.0,
        "USDJPY": 30.0,
        "XAUUSD": 80.0,
        "BTCUSD": 200.0,
    }
    return {
        "max_points": max_points,
        "sample_count": len(samples),
        "p50_range_points": round(p50, 2),
        "source": "bootstrap_from_m1_range",
        "hard_cap_applied": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data/spread_calibration.json")
    args = parser.parse_args()
    cfg = load_config()
    specs = load_symbol_specs(ROOT / "data" / "symbol_specs.json")
    raw_dir = cfg.resolve_path("paths", "data_raw")
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "method": "bootstrap_from_m1_range",
        "warning": (
            "Replace with scripts/calibrate_spread.py multi-session samples "
            "(london_ny_overlap, asian_quiet, high_impact_news) before Phase 1 sign-off."
        ),
        "symbols": {},
    }
    for symbol in cfg.symbols:
        path = raw_dir / f"{symbol.lower()}_m1.csv"
        if not path.exists():
            print(f"[SKIP] {symbol}: no M1 csv")
            continue
        df = pd.read_csv(path)
        point = specs[symbol].point if symbol in specs else 1e-5
        entry = estimate_points(df, point)
        if not entry:
            print(f"[SKIP] {symbol}: empty ranges")
            continue
        hard = {"EURUSD": 25.0, "USDJPY": 30.0, "XAUUSD": 80.0, "BTCUSD": 200.0}.get(
            symbol.upper()
        )
        if hard is not None and entry["max_points"] > hard:
            entry["max_points"] = hard
            entry["hard_cap_applied"] = True
        payload["symbols"][symbol] = entry
        print(f"{symbol}: max_points={entry['max_points']} (bootstrap)")

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Mirror numeric caps into config.yaml friendly note file
    caps_path = ROOT / "data" / "spread_caps_from_bootstrap.json"
    caps = {s: v["max_points"] for s, v in payload["symbols"].items()}
    caps_path.write_text(json.dumps(caps, indent=2), encoding="utf-8")
    print(f"Wrote {out}")
    print("Update config.yaml max_points_by_symbol from calibration file (auto-read).")


if __name__ == "__main__":
    main()
