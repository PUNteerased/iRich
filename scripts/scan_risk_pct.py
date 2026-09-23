"""Scan DYNAMIC_PCT risk_per_trade_pct on replay virtual balance (Phase 0.MM).

    python scripts/scan_risk_pct.py --pcts 2 3 4
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.backtest.replay import ReplayEngine, ReplayParams, build_replay_frame
from src.config import load_config
from src.market_data import ReplayProvider, load_symbol_specs
from src.model_runner import ModelRunner
from src.money_management import VirtualAccount


def load_csv(raw_dir: Path, symbol: str, tf: str):
    import pandas as pd

    path = raw_dir / f"{symbol.lower()}_{tf}.csv"
    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pcts", nargs="+", type=float, default=[2.0, 3.0, 4.0])
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--out", default="data/risk_pct_scan.json")
    args = parser.parse_args()

    cfg = load_config()
    specs = load_symbol_specs(ROOT / "data" / "symbol_specs.json")
    symbol = args.symbol
    raw_dir = cfg.resolve_path("paths", "data_raw")
    frames = {tf: load_csv(raw_dir, symbol, tf) for tf in ("h1", "m15", "m5", "m1")}
    frame, _ = build_replay_frame(frames["h1"], frames["m15"], frames["m5"], frames["m1"])
    bundle = ModelRunner(cfg.resolve_path("paths", "models")).get(symbol)
    if bundle is None:
        raise SystemExit(f"No model for {symbol}")

    rows = []
    for pct in args.pcts:
        mm = copy.deepcopy(cfg.money_management_cfg())
        mm["mode"] = "DYNAMIC_PCT"
        mm.setdefault("dynamic", {})["risk_per_trade_pct"] = pct
        acct = VirtualAccount(100.0)
        provider = ReplayProvider(specs={symbol: specs[symbol]}, virtual_account=acct)
        params = ReplayParams(
            rr=cfg.rr,
            volume=cfg.lot_volume,
            max_risk_usd=cfg.max_risk_usd,
            min_probability=cfg.min_probability,
            atr_sl_mult=cfg.atr_sl_mult(symbol),
            sl_caps=(cfg.get("risk", "per_symbol_sl_caps", default={}) or {}).get(symbol),
            mm_cfg=mm,
            starting_balance=100.0,
            max_spread_points=50.0,
        )
        result = ReplayEngine(params, provider).run(symbol, frame, bundle)
        summary = result.summary()
        rows.append(
            {
                "pct": pct,
                "trades": summary["trades"],
                "expectancy_r": summary["expectancy_r"],
                "final_balance": acct.balance,
                "max_drawdown_pct": round(acct.max_drawdown_pct, 3),
                "rejects_no_money": summary["rejects"].get("REJECT_NO_MONEY", 0),
            }
        )
        print(rows[-1])

    out = ROOT / args.out
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
