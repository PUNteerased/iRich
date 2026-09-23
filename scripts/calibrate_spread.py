"""Measure real spread per symbol and derive the points cap (G10).

Run this several times across different market conditions. A single quiet-market
run produces a cap that is too tight, which then rejects entries during session
overlap and news for reasons that look like the bot is simply idle.

Suggested sessions:
    --session-tag london_ny_overlap
    --session-tag asian_quiet
    --session-tag high_impact_news     (around +/-30 min of a red event)

The percentile is computed over the union of every sample ever appended, not
just the current run.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config
from src.market_data import MT5LiveProvider
from src.mt5_connector import MT5Connector

MAX_STORED_SAMPLES = 20_000


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def load_calibration(path: Path) -> dict:
    if not path.exists():
        return {"symbols": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"symbols": {}}
    data.setdefault("symbols", {})
    return data


def collect(
    provider: MT5LiveProvider,
    symbols: list[str],
    duration_minutes: float,
    interval_seconds: float,
) -> dict[str, list[float]]:
    samples: dict[str, list[float]] = {s: [] for s in symbols}
    deadline = time.time() + duration_minutes * 60.0
    while time.time() < deadline:
        for symbol in symbols:
            spec = provider.get_symbol_info(symbol)
            tick = provider.get_tick(symbol)
            if spec is None or tick is None:
                continue
            samples[symbol].append(spec.spread_points(tick))
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(interval_seconds, remaining))
    return samples


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="*", default=None, help="Default: all config symbols")
    parser.add_argument("--duration-minutes", type=float, default=10.0)
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    parser.add_argument("--session-tag", default="unspecified")
    parser.add_argument("--percentile", type=float, default=95.0)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Discard previous samples instead of appending to them",
    )
    args = parser.parse_args()

    cfg = load_config()
    symbols = args.symbols or cfg.symbols
    out_path = cfg.spread_calibration_path

    mt5c = MT5Connector(cfg)
    if not mt5c.connect():
        raise SystemExit("MT5 connection failed")

    print(
        f"Sampling {symbols} for {args.duration_minutes} min "
        f"every {args.interval_seconds}s (session={args.session_tag})"
    )
    started = datetime.now(timezone.utc)
    try:
        fresh = collect(
            mt5c.provider, symbols, args.duration_minutes, args.interval_seconds
        )
    finally:
        mt5c.shutdown()
    ended = datetime.now(timezone.utc)

    data = {"symbols": {}} if args.reset else load_calibration(out_path)
    data["updated_at"] = ended.isoformat()
    data["percentile"] = args.percentile

    for symbol in symbols:
        new_samples = fresh.get(symbol, [])
        if not new_samples:
            print(f"  {symbol}: no samples collected (market closed or symbol missing)")
            continue
        entry = data["symbols"].setdefault(symbol, {"samples": [], "sessions": []})
        entry["samples"] = (entry.get("samples", []) + new_samples)[-MAX_STORED_SAMPLES:]
        entry["sessions"].append(
            {
                "tag": args.session_tag,
                "started_at": started.isoformat(),
                "ended_at": ended.isoformat(),
                "count": len(new_samples),
                "median": round(statistics.median(new_samples), 2),
                "p95": round(percentile(new_samples, args.percentile), 2),
                "max": round(max(new_samples), 2),
            }
        )
        union = entry["samples"]
        entry["sample_count"] = len(union)
        entry["max_points"] = round(percentile(union, args.percentile), 2)
        entry["observed_max_points"] = round(max(union), 2)
        print(
            f"  {symbol}: n={len(new_samples)} session_p{args.percentile:.0f}="
            f"{percentile(new_samples, args.percentile):.2f} "
            f"-> union_p{args.percentile:.0f}={entry['max_points']:.2f} points "
            f"(total n={len(union)})"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")

    sessions = {
        symbol: sorted({s["tag"] for s in entry.get("sessions", [])})
        for symbol, entry in data["symbols"].items()
    }
    print("Sessions covered per symbol:")
    for symbol, tags in sessions.items():
        print(f"  {symbol}: {', '.join(tags)}")
    print(
        "\nCaps are read from this file automatically. Sample at least the three "
        "suggested sessions before trusting them."
    )


if __name__ == "__main__":
    main()
