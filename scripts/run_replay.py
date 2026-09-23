"""Replay the live decision path over data/raw CSVs (G16).

Symbol specs come from data/symbol_specs.json, captured alongside the price
history. If that file is missing the run stops rather than falling back to the
live terminal: pricing a historical trade with today's tick value would produce
a plausible-looking result that means nothing.

    python scripts/export_mt5_history.py --specs-only
    python scripts/run_replay.py
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.backtest.replay import ReplayEngine, ReplayParams, build_replay_frame
from src.config import load_config
from src.market_data import ReplayProvider, load_symbol_specs
from src.model_runner import ModelRunner

SPECS_PATH = ROOT / "data" / "symbol_specs.json"


def load_csv(raw_dir: Path, symbol: str, tf: str) -> pd.DataFrame:
    path = raw_dir / f"{symbol.lower()}_{tf}.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_csv(path)
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], utc=True)
    return df


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--specs", default=str(SPECS_PATH))
    parser.add_argument("--max-hold-bars", type=int, default=500)
    parser.add_argument(
        "--replay-spread-points",
        type=float,
        default=None,
        help="Synthetic spread for replay; defaults to the calibrated cap",
    )
    parser.add_argument(
        "--atr-sl-mult",
        type=float,
        default=None,
        help=(
            "Override risk.atr_sl_mult. Use to measure how the structural SL "
            "distribution moves against the configured SL band."
        ),
    )
    parser.add_argument("--out", default="data/replay_results.json")
    parser.add_argument("--starting-balance", type=float, default=100.0)
    args = parser.parse_args()

    cfg = load_config()
    symbols = args.symbols or cfg.symbols
    specs = load_symbol_specs(args.specs)
    if not specs:
        raise SystemExit(
            f"No symbol specs at {args.specs}. Run: "
            "python scripts/export_mt5_history.py --specs-only"
        )

    raw_dir = cfg.resolve_path("paths", "data_raw")
    runner = ModelRunner(cfg.resolve_path("paths", "models"))
    summaries = []

    for symbol in symbols:
        if symbol not in specs:
            print(f"[SKIP] {symbol}: no spec captured")
            continue
        bundle = runner.get(symbol)
        if bundle is None:
            print(f"[SKIP] {symbol}: no model")
            continue

        try:
            frames = {tf: load_csv(raw_dir, symbol, tf) for tf in ("h1", "m15", "m5", "m1")}
        except FileNotFoundError as exc:
            print(f"[SKIP] {symbol}: missing history {exc}")
            continue

        frame, _ = build_replay_frame(frames["h1"], frames["m15"], frames["m5"], frames["m1"])
        calibrated = cfg.max_spread_points(symbol)
        replay_spread = (
            args.replay_spread_points
            if args.replay_spread_points is not None
            else (calibrated if calibrated is not None else 1.0)
        )
        # Live fails closed without a calibrated cap, which in replay would reject
        # every candidate and hide the rest of the pipeline. Replay cannot test the
        # spread filter anyway - bar data has no spread - so lift the cap clear of the
        # synthetic spread and say so, rather than reporting zero trades for the wrong
        # reason.
        spread_cap = calibrated if calibrated is not None else replay_spread + 1.0
        if calibrated is None:
            print(
                f"[WARN] {symbol}: no calibrated spread cap; the spread gate is inert "
                f"in this run (live would REJECT_SPREAD). Run scripts/calibrate_spread.py."
            )
        provider = ReplayProvider(
            specs={symbol: specs[symbol]},
            spread_points={symbol: replay_spread},
        )
        params = ReplayParams(
            rr=cfg.rr,
            volume=cfg.lot_volume,
            max_risk_usd=cfg.max_risk_usd,
            min_probability=cfg.min_probability,
            max_entry_drift_r=cfg.max_entry_drift_r,
            atr_sl_mult=(
                args.atr_sl_mult if args.atr_sl_mult is not None else cfg.atr_sl_mult(symbol)
            ),
            atr_zscore_max=float(cfg.get("sniper", "atr_zscore_max", default=2.0)),
            require_full_mtf=bool(cfg.get("sniper", "require_full_mtf", default=True)),
            require_fvg_volume=bool(cfg.get("sniper", "require_fvg_volume", default=True)),
            volume_sma_window=int(cfg.get("sniper", "volume_sma_window", default=20)),
            max_spread_points=spread_cap,
            max_hold_bars=args.max_hold_bars,
            sl_caps=(cfg.get("risk", "per_symbol_sl_caps", default={}) or {}).get(symbol),
            mm_cfg=cfg.money_management_cfg(),
            starting_balance=float(args.starting_balance),
            atr_sl_timeframe=cfg.atr_sl_timeframe(symbol),
        )
        result = ReplayEngine(params, provider).run(symbol, frame, bundle)
        summary = result.summary()
        summary["replay_spread_points"] = replay_spread
        summary["spread_cap_points"] = spread_cap
        summary["spread_gate_calibrated"] = calibrated is not None
        summaries.append(summary)

        print(f"\n=== {symbol} ===")
        print(
            f"bars={summary['bars']} trades={summary['trades']} "
            f"win_rate={summary['win_rate']} expectancy_R={summary['expectancy_r']} "
            f"sum_R={summary['sum_r']} max_risk=${summary['max_risk_usd']}"
        )
        for code, count in summary["rejects"].items():
            print(f"  {code}: {count}")
        sl = summary["sl_distance"]
        if sl.get("count"):
            print(
                f"  SL distance of {sl['count']} aligned candidates: "
                f"min={sl['min']} median={sl['median']} max={sl['max']}"
            )
            if "band_min" in sl:
                print(
                    f"    band [{sl['band_min']}, {sl['band_max']}]: "
                    f"below={sl['below_band']} in={sl['in_band']} above={sl['above_band']}"
                )

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summaries, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
