"""Sprint 4 #5: A/B harness for the entry funnel (soft-MTF vs AND-stack).

Runs the replay engine (src.backtest.replay — same risk/SL/TP contract as
live, G16) across a small grid of:

    - sniper.require_full_mtf : True / False
    - sniper.min_probability  : 0.70 / 0.75
    - sniper.atr_zscore_max   : 2.0 / 2.5 / 3.0

for every symbol with a trained model and exported history under
data/raw/. Reports trades, win_rate, expectancy_r, expectancy_r_per_week and
max_dd per combo, per symbol AND aggregated across symbols, and writes the
full grid to data/validation_reports/entry_funnel_<timestamp>.json.

README — how to read the result:
    * 0.55-0.65 min_probability is NOT in this grid on purpose. It comes
      later, as its own A/B pass, only after the require_full_mtf decision
      above has locked. Widening the MTF funnel and lowering the probability
      floor in the same experiment makes it impossible to say which change
      produced a given change in expectancy.
    * When enabling soft-MTF (require_full_mtf: false), keep
      min_probability >= 0.70 first — this grid never tests soft-MTF below
      0.70, and config.yaml must not either until a dedicated pass says so.
    * Pick the config with the best expectancy_r_per_week whose max_dd_pct
      stays under risk.monthly_max_dd_pct (the live monthly circuit
      breaker) — a config that wins on R/week but would have tripped the
      breaker historically is not a real candidate.
    * require_fvg_volume / volume_sma_window are held fixed at the
      config.yaml values for every cell in this grid — they are a Sprint 4 #1
      hard gate, not part of this experiment.

    python scripts/entry_funnel_ab.py
    python scripts/entry_funnel_ab.py --symbols EURUSD USDJPY
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

# Sprint 4 #5 grid. See module docstring for why 0.55-0.65 is excluded.
GRID_REQUIRE_FULL_MTF: list[bool] = [True, False]
GRID_MIN_PROBABILITY: list[float] = [0.70, 0.75]
GRID_ATR_ZSCORE_MAX: list[float] = [2.0, 2.5, 3.0]

ComboKey = tuple[bool, float, float]


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
    require_full_mtf: bool,
    min_probability: float,
    atr_zscore_max: float,
    weeks: float,
) -> dict[str, Any]:
    provider = ReplayProvider(specs={symbol: spec}, spread_points={symbol: replay_spread})
    params = ReplayParams(
        rr=base_cfg["rr"],
        volume=base_cfg["volume"],
        max_risk_usd=base_cfg["max_risk_usd"],
        min_probability=min_probability,
        max_entry_drift_r=base_cfg["max_entry_drift_r"],
        atr_sl_mult=base_cfg["atr_sl_mult"],
        atr_zscore_max=atr_zscore_max,
        require_full_mtf=require_full_mtf,
        require_fvg_volume=base_cfg["require_fvg_volume"],
        volume_sma_window=base_cfg["volume_sma_window"],
        max_spread_points=base_cfg["max_spread_points"],
        sl_caps=base_cfg["sl_caps"],
        mm_cfg=base_cfg["mm_cfg"],
        starting_balance=base_cfg["starting_balance"],
        atr_sl_timeframe=base_cfg["atr_sl_timeframe"],
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
        itertools.product(GRID_REQUIRE_FULL_MTF, GRID_MIN_PROBABILITY, GRID_ATR_ZSCORE_MAX)
    )
    per_symbol_rows: dict[str, list[dict[str, Any]]] = {}
    aggregate: dict[ComboKey, dict[str, float]] = {
        key: {"trades": 0, "wins": 0, "sum_r": 0.0, "weeks_max": 0.0, "max_dd_pct": 0.0}
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
        # This grid does not vary the spread gate, so keep it inert rather than
        # binding: bar data has no real spread (see run_replay.py's own note),
        # and setting spread_cap == replay_spread exactly makes every bar that
        # reaches this gate fail on float rounding, silently zeroing every
        # combo's trade count for a reason unrelated to require_full_mtf /
        # min_probability / atr_zscore_max.
        spread_cap = replay_spread + 1.0
        if calibrated is None:
            print(f"[WARN] {symbol}: no calibrated spread cap; spread gate is inert this run.")

        base_cfg = {
            "rr": cfg.rr,
            "volume": cfg.lot_volume,
            "max_risk_usd": cfg.max_risk_usd,
            "max_entry_drift_r": cfg.max_entry_drift_r,
            "atr_sl_mult": cfg.atr_sl_mult(symbol),
            "require_fvg_volume": bool(cfg.get("sniper", "require_fvg_volume", default=True)),
            "volume_sma_window": int(cfg.get("sniper", "volume_sma_window", default=20)),
            "max_spread_points": spread_cap,
            "sl_caps": (cfg.get("risk", "per_symbol_sl_caps", default={}) or {}).get(symbol),
            "mm_cfg": cfg.money_management_cfg(),
            "starting_balance": float(args.starting_balance),
            "atr_sl_timeframe": cfg.atr_sl_timeframe(symbol),
        }

        rows: list[dict[str, Any]] = []
        for require_full_mtf, min_prob, atr_zscore in combo_keys:
            summary = run_combo(
                symbol,
                frame,
                bundle,
                specs[symbol],
                replay_spread,
                base_cfg,
                require_full_mtf,
                min_prob,
                atr_zscore,
                weeks,
            )
            summary["require_full_mtf"] = require_full_mtf
            summary["min_probability"] = min_prob
            summary["atr_zscore_max"] = atr_zscore
            rows.append(summary)

            agg = aggregate[(require_full_mtf, min_prob, atr_zscore)]
            agg["trades"] += summary["trades"]
            agg["wins"] += summary["wins"]
            agg["sum_r"] += summary["sum_r"]
            agg["weeks_max"] = max(agg["weeks_max"], weeks)
            agg["max_dd_pct"] = max(agg["max_dd_pct"], summary["max_dd_pct"])

            print(
                f"[{symbol}] full_mtf={require_full_mtf} min_prob={min_prob} "
                f"atr_z={atr_zscore} -> trades={summary['trades']} "
                f"win_rate={summary['win_rate']} expectancy_R={summary['expectancy_r']} "
                f"R/week={summary['expectancy_r_per_week']} max_dd={summary['max_dd_pct']}%"
            )

        per_symbol_rows[symbol] = rows

    aggregate_rows: list[dict[str, Any]] = []
    for (require_full_mtf, min_prob, atr_zscore), agg in aggregate.items():
        trades = int(agg["trades"])
        weeks = agg["weeks_max"]
        aggregate_rows.append(
            {
                "require_full_mtf": require_full_mtf,
                "min_probability": min_prob,
                "atr_zscore_max": atr_zscore,
                "trades": trades,
                "win_rate": round(agg["wins"] / trades, 4) if trades else 0.0,
                "expectancy_r": round(agg["sum_r"] / trades, 4) if trades else 0.0,
                "sum_r": round(agg["sum_r"], 4),
                "expectancy_r_per_week": round(agg["sum_r"] / weeks, 4) if weeks > 0 else 0.0,
                "max_dd_pct": round(agg["max_dd_pct"], 4),
                "weeks": round(weeks, 3),
            }
        )
    aggregate_rows.sort(key=lambda r: r["expectancy_r_per_week"], reverse=True)

    monthly_dd_limit = float(cfg.get("risk", "monthly_max_dd_pct", default=10.0))
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "grid": {
            "require_full_mtf": GRID_REQUIRE_FULL_MTF,
            "min_probability": GRID_MIN_PROBABILITY,
            "atr_zscore_max": GRID_ATR_ZSCORE_MAX,
        },
        "note": (
            "0.55-0.65 min_probability comes later, as its own A/B pass, after "
            "this require_full_mtf decision locks. When enabling soft-MTF "
            "(require_full_mtf: false) keep min_probability >= 0.70 first. "
            "Choose the config with the best expectancy_r_per_week whose "
            "max_dd_pct stays under monthly_max_dd_pct_limit."
        ),
        "monthly_max_dd_pct_limit": monthly_dd_limit,
        "per_symbol": per_symbol_rows,
        "aggregate": aggregate_rows,
    }

    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"entry_funnel_{stamp}.json"
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out_path}")

    within_dd = [r for r in aggregate_rows if r["max_dd_pct"] <= monthly_dd_limit]
    best = (within_dd or aggregate_rows or [None])[0]
    if best:
        print(
            f"\nBest by R/week (max_dd <= {monthly_dd_limit}%): "
            f"require_full_mtf={best['require_full_mtf']} "
            f"min_probability={best['min_probability']} "
            f"atr_zscore_max={best['atr_zscore_max']} -> "
            f"{best['expectancy_r_per_week']} R/week, max_dd={best['max_dd_pct']}%, "
            f"trades={best['trades']}"
        )


if __name__ == "__main__":
    main()
