"""Export H1/M15/M5/M1 history and symbol specs from MT5 to data/."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config
from src.mt5_connector import MT5Connector

SPECS_FILENAME = "symbol_specs.json"


def export_specs(mt5c: MT5Connector, symbols: list[str], out_dir: Path) -> Path:
    """Snapshot tick size/value so replay prices trades with the right spec.

    Replay must not read the live spec: that would value a trade from months ago
    using today's tick value.
    """
    specs = {}
    for symbol in symbols:
        spec = mt5c.provider.get_symbol_info(symbol)
        if spec is None:
            print(f"[WARN] no symbol_info for {symbol}")
            continue
        specs[symbol] = asdict(spec)
    path = out_dir / SPECS_FILENAME
    path.write_text(
        json.dumps(
            {"captured_at": datetime.now(timezone.utc).isoformat(), "symbols": specs},
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {path} symbols={list(specs)}")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Export MT5 OHLCV history")
    parser.add_argument("--bars-h1", type=int, default=5000)
    parser.add_argument("--bars-m15", type=int, default=10000)
    parser.add_argument("--bars-m5", type=int, default=15000)
    parser.add_argument("--bars-m1", type=int, default=30000)
    parser.add_argument("--specs-only", action="store_true", help="Only refresh symbol specs")
    args = parser.parse_args()

    cfg = load_config()
    out_dir = cfg.resolve_path("paths", "data_raw")
    out_dir.mkdir(parents=True, exist_ok=True)

    mt5c = MT5Connector(cfg)
    if not mt5c.connect():
        raise SystemExit("MT5 connect failed")

    counts = {"H1": args.bars_h1, "M15": args.bars_m15, "M5": args.bars_m5, "M1": args.bars_m1}
    try:
        export_specs(mt5c, cfg.symbols, out_dir.parent)
        if args.specs_only:
            return
        for symbol in cfg.symbols:
            for tf, n in counts.items():
                df = mt5c.copy_rates(symbol, tf, n)
                if df.empty:
                    print(f"[WARN] empty {symbol} {tf}")
                    continue
                path = out_dir / f"{symbol.lower()}_{tf.lower()}.csv"
                df.to_csv(path, index=False)
                print(f"Wrote {path} rows={len(df)}")
    finally:
        mt5c.shutdown()


if __name__ == "__main__":
    main()
