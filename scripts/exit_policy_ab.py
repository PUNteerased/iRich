"""Sprint 3 (iRich Profit Upgrade v1.4): A/B harness for the exit policy.

Runs the replay engine (src.backtest.replay — same risk/SL/TP contract as
live, G16) with Sprint 3's live-parity exit management (break-even lock,
then structure-or-ATR trail, see src.backtest.replay module docstring)
across a small grid of:

    - risk.break_even_r  : 1.0 / 1.5 / 2.0   (be_trigger)
    - exit staircase     : off / on          (see ReplayParams.enable_staircase)

with the session filter (src.session_filter.in_session) turned ON for every
cell — this A/B is about the exit policy, not the entry funnel, so the entry
side is held fixed at whatever config.yaml already locks (require_full_mtf,
min_probability, etc.) plus the session gate.

Metrics reported per combo, per symbol AND aggregated across symbols:
expectancy_r, expectancy_r_per_week, win_rate, avg_hold_bars, max_dd_pct.
Full grid written to data/validation_reports/exit_policy_<timestamp>.json.

IMPORTANT — this script only *measures*. It does not, and must not, write
back to config.yaml. `risk.break_even_r` in config.yaml stays at 1.0 (Sprint 1
value) regardless of what this report recommends — lock live BE only after
reading this report and making that config change as its own deliberate
step (Sprint 2 territory), never automatically.

    python scripts/exit_policy_ab.py
    python scripts/exit_policy_ab.py --symbols EURUSD USDJPY
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.backtest.replay import ReplayEngine, ReplayParams, ReplayTrade, build_replay_frame
from src.config import load_config
from src.market_data import ReplayProvider, load_symbol_specs
from src.model_runner import ModelBundle, ModelRunner

SPECS_PATH = ROOT / "data" / "symbol_specs.json"

# Sprint 3 grid. See module docstring for why the entry funnel / session
# filter are held fixed rather than swept here.
GRID_BREAK_EVEN_R: list[float] = [1.0, 1.5, 2.0]
GRID_ENABLE_STAIRCASE: list[bool] = [False, True]

ComboKey = tuple[float, bool]


def load_csv(raw_dir: Path, symbol: str, tf: str) -> pd.DataFrame:
    path = raw_dir / f"{symbol.lower()}_{tf}.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_csv(path)
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], utc=True)
    return df


def max_drawdown_pct(trades: list[ReplayTrade]) -> float:
    """Peak-to-trough drawdown (%) over the compounding balance curve.

    Falls back to 0.0 when compounding was off (no balance_after values) —
    the caller reports trades/expectancy regardless, so this is a "not
    measured" rather than a "measured zero" case in that scenario.
    """
    balances = [t.balance_after for t in trades if t.balance_after is not None]
    if not balances:
        return 0.0
    peak = balances[0]
    worst = 0.0
    for b in balances:
        peak = max(peak, b)
        if peak > 0:
            worst = max(worst, (peak - b) / peak)
    return round(worst * 100.0, 4)


def weeks_span(frame: pd.DataFrame) -> float:
    """Calendar span of the replayed history, in weeks (never zero)."""
    if frame.empty or "time" not in frame.columns:
        return 0.0
    span_days = (frame["time"].max() - frame["time"].min()).total_seconds() / 86400.0
    return max(span_days / 7.0, 1e-9)


def run_combo(
    symbol: str,
    frame: pd.DataFrame,
    bundle: ModelBundle,
    spec: Any,
    replay_spread: float,
    base_cfg: dict[str, Any],
    break_even_r: float,
    enable_staircase: bool,
    weeks: float,
) -> dict[str, Any]:
    provider = ReplayProvider(specs={symbol: spec}, spread_points={symbol: replay_spread})
    params = ReplayParams(
        rr=base_cfg["rr"],
        volume=base_cfg["volume"],
        max_risk_usd=base_cfg["max_risk_usd"],
        min_probability=base_cfg["min_probability"],
        max_entry_drift_r=base_cfg["max_entry_drift_r"],
        atr_sl_mult=base_cfg["atr_sl_mult"],
        atr_zscore_max=base_cfg["atr_zscore_max"],
        require_full_mtf=base_cfg["require_full_mtf"],
        require_fvg_volume=base_cfg["require_fvg_volume"],
        volume_sma_window=base_cfg["volume_sma_window"],
        max_spread_points=base_cfg["max_spread_points"],
        sl_caps=base_cfg["sl_caps"],
        mm_cfg=base_cfg["mm_cfg"],
        starting_balance=base_cfg["starting_balance"],
        atr_sl_timeframe=base_cfg["atr_sl_timeframe"],
        max_hold_bars=base_cfg["max_hold_bars"],
        # Sprint 3 exit management — this is the axis under test.
        enable_exit_management=True,
        break_even_r=break_even_r,
        trailing_start_r=base_cfg["trailing_start_r"],
        trailing_atr_mult=base_cfg["trailing_atr_mult"],
        prefer_structure_trail=base_cfg["prefer_structure_trail"],
        be_lock_pips=base_cfg["be_lock_pips"],
        be_lock_atr_mult=base_cfg["be_lock_atr_mult"],
        enable_staircase=enable_staircase,
        # Session filter is ON for every cell in this grid (see module docstring).
        session_filter_enabled=True,
    )
    result = ReplayEngine(params, provider).run(symbol, frame, bundle)
    summary = result.summary()
    sum_r = summary["sum_r"]
    summary["expectancy_r_per_week"] = round(sum_r / weeks, 4) if weeks > 0 else 0.0
    summary["max_dd_pct"] = max_drawdown_pct(result.trades)
    summary["weeks"] = round(weeks, 3)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--specs", default=str(SPECS_PATH))
    parser.add_argument("--max-hold-bars", type=int, default=500)
    parser.add_argument("--starting-balance", type=float, default=100.0)
    parser.add_argument(
        "--replay-spread-points",
        type=float,
        default=None,
        help="Synthetic spread for replay; defaults to the calibrated cap",
    )
    parser.add_argument("--out-dir", default="data/validation_reports")
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

    combo_keys: list[ComboKey] = list(
        itertools.product(GRID_BREAK_EVEN_R, GRID_ENABLE_STAIRCASE)
    )
    per_symbol_rows: dict[str, list[dict[str, Any]]] = {}
    aggregate: dict[ComboKey, dict[str, float]] = {
        key: {"trades": 0, "wins": 0, "sum_r": 0.0, "hold_bars_sum": 0.0, "weeks_max": 0.0, "max_dd_pct": 0.0}
        for key in combo_keys
    }

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
        weeks = weeks_span(frame)

        calibrated = cfg.max_spread_points(symbol)
        replay_spread = (
            args.replay_spread_points
            if args.replay_spread_points is not None
            else (calibrated if calibrated is not None else 1.0)
        )
        # Keep the spread gate inert for the same reason entry_funnel_ab.py
        # does: bar data has no real spread, and this grid does not vary it.
        spread_cap = replay_spread + 1.0
        if calibrated is None:
            print(f"[WARN] {symbol}: no calibrated spread cap; spread gate is inert this run.")

        base_cfg = {
            "rr": cfg.rr,
            "volume": cfg.lot_volume,
            "max_risk_usd": cfg.max_risk_usd,
            "min_probability": cfg.min_probability,
            "max_entry_drift_r": cfg.max_entry_drift_r,
            "atr_sl_mult": cfg.atr_sl_mult(symbol),
            "atr_zscore_max": float(cfg.get("sniper", "atr_zscore_max", default=2.0)),
            "require_full_mtf": bool(cfg.get("sniper", "require_full_mtf", default=True)),
            "require_fvg_volume": bool(cfg.get("sniper", "require_fvg_volume", default=True)),
            "volume_sma_window": int(cfg.get("sniper", "volume_sma_window", default=20)),
            "max_spread_points": spread_cap,
            "sl_caps": (cfg.get("risk", "per_symbol_sl_caps", default={}) or {}).get(symbol),
            "mm_cfg": cfg.money_management_cfg(),
            "starting_balance": float(args.starting_balance),
            "atr_sl_timeframe": cfg.atr_sl_timeframe(symbol),
            "max_hold_bars": args.max_hold_bars,
            # Live values as of Sprint 1/2 — held fixed except break_even_r,
            # which is the axis under test.
            "trailing_start_r": float(cfg.get("risk", "trailing_start_r", default=1.5)),
            "trailing_atr_mult": float(cfg.get("risk", "trailing_atr_mult", default=1.0)),
            "prefer_structure_trail": bool(cfg.get("risk", "prefer_structure_trail", default=True)),
            "be_lock_pips": float(cfg.get("risk", "be_lock_pips", default=2.0)),
            "be_lock_atr_mult": float(cfg.get("risk", "be_lock_atr_mult", default=0.0)),
        }

        rows: list[dict[str, Any]] = []
        for break_even_r, enable_staircase in combo_keys:
            summary = run_combo(
                symbol,
                frame,
                bundle,
                specs[symbol],
                replay_spread,
                base_cfg,
                break_even_r,
                enable_staircase,
                weeks,
            )
            summary["break_even_r"] = break_even_r
            summary["enable_staircase"] = enable_staircase
            rows.append(summary)

            agg = aggregate[(break_even_r, enable_staircase)]
            agg["trades"] += summary["trades"]
            agg["wins"] += summary["wins"]
            agg["sum_r"] += summary["sum_r"]
            agg["hold_bars_sum"] += summary["avg_hold_bars"] * summary["trades"]
            agg["weeks_max"] = max(agg["weeks_max"], weeks)
            agg["max_dd_pct"] = max(agg["max_dd_pct"], summary["max_dd_pct"])

            print(
                f"[{symbol}] be_r={break_even_r} staircase={enable_staircase} -> "
                f"trades={summary['trades']} win_rate={summary['win_rate']} "
                f"expectancy_R={summary['expectancy_r']} R/week={summary['expectancy_r_per_week']} "
                f"avg_hold_bars={summary['avg_hold_bars']} max_dd={summary['max_dd_pct']}%"
            )

        per_symbol_rows[symbol] = rows

    aggregate_rows: list[dict[str, Any]] = []
    for (break_even_r, enable_staircase), agg in aggregate.items():
        trades = int(agg["trades"])
        weeks = agg["weeks_max"]
        aggregate_rows.append(
            {
                "break_even_r": break_even_r,
                "enable_staircase": enable_staircase,
                "trades": trades,
                "win_rate": round(agg["wins"] / trades, 4) if trades else 0.0,
                "expectancy_r": round(agg["sum_r"] / trades, 4) if trades else 0.0,
                "sum_r": round(agg["sum_r"], 4),
                "expectancy_r_per_week": round(agg["sum_r"] / weeks, 4) if weeks > 0 else 0.0,
                "avg_hold_bars": round(agg["hold_bars_sum"] / trades, 2) if trades else 0.0,
                "max_dd_pct": round(agg["max_dd_pct"], 4),
                "weeks": round(weeks, 3),
            }
        )
    aggregate_rows.sort(key=lambda r: r["expectancy_r_per_week"], reverse=True)

    monthly_dd_limit = float(cfg.get("risk", "monthly_max_dd_pct", default=10.0))
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "grid": {
            "break_even_r": GRID_BREAK_EVEN_R,
            "enable_staircase": GRID_ENABLE_STAIRCASE,
            "session_filter_enabled": True,
        },
        "note": (
            "Comment: lock live break_even_r (config.yaml risk.break_even_r) "
            "only after reading this report — do not auto-apply. Sprint 3 "
            "keeps config.yaml at break_even_r=1.0 regardless of what wins "
            "here; raising it to the recommended value is a separate, "
            "deliberate Sprint 2-style config change. Pick the combo with "
            "the best expectancy_r_per_week whose max_dd_pct stays under "
            "risk.monthly_max_dd_pct (the live monthly circuit breaker)."
        ),
        "monthly_max_dd_pct_limit": monthly_dd_limit,
        "per_symbol": per_symbol_rows,
        "aggregate": aggregate_rows,
    }

    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"exit_policy_{stamp}.json"
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out_path}")

    within_dd = [r for r in aggregate_rows if r["max_dd_pct"] <= monthly_dd_limit]
    best = (within_dd or aggregate_rows or [None])[0]
    if best:
        print(
            f"\nBest by R/week (max_dd <= {monthly_dd_limit}%): "
            f"break_even_r={best['break_even_r']} enable_staircase={best['enable_staircase']} -> "
            f"{best['expectancy_r_per_week']} R/week, win_rate={best['win_rate']}, "
            f"avg_hold_bars={best['avg_hold_bars']}, max_dd={best['max_dd_pct']}%, "
            f"trades={best['trades']}"
        )
        print(
            "\nComment: lock live break_even_r only after this report — "
            "config.yaml risk.break_even_r stays at 1.0 until that is done "
            "as its own deliberate change."
        )


if __name__ == "__main__":
    main()
