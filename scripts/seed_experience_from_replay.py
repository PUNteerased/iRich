"""Sprint 6: seed ExperienceBuffer from a replay run (accepted fills only).

Usage:
  python scripts/seed_experience_from_replay.py --symbol EURUSD
  python scripts/seed_experience_from_replay.py --all

Does not change live config. Safe to re-run (appends dated jsonl).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.backtest.replay import ReplayParams, load_symbol_frames, run_replay  # noqa: E402
from src.config import load_config  # noqa: E402
from src.learning.replay import ExperienceBuffer  # noqa: E402
from src.paths import PROJECT_ROOT  # noqa: E402


def seed_symbol(symbol: str, cfg, buffer: ExperienceBuffer) -> int:
    params = ReplayParams(
        symbol=symbol,
        require_full_mtf=bool(cfg.get("sniper", "require_full_mtf", default=True)),
        min_probability=float(cfg.get("sniper", "min_probability", default=0.75)),
        atr_zscore_max=float(cfg.get("sniper", "atr_zscore_max", default=2.0)),
        require_fvg_volume=bool(cfg.get("sniper", "require_fvg_volume", default=True)),
        max_risk_usd=float(cfg.get("risk", "max_risk_usd", default=2.0)),
        rr=float(cfg.get("risk", "rr", default=3.0)),
        break_even_r=float(cfg.get("risk", "break_even_r", default=2.0)),
        enable_staircase=bool(cfg.get("risk", "enable_staircase", default=True)),
        session_filter_enabled=bool(cfg.get("risk", "session_filter_enabled", default=True)),
    )
    frames = load_symbol_frames(symbol)
    if frames is None:
        print(f"[{symbol}] no raw frames — skip")
        return 0
    result = run_replay(frames, params)
    now = datetime.now(timezone.utc)
    n = 0
    for trade in getattr(result, "trades", []) or []:
        if getattr(trade, "status", "") in ("rejected", "REJECT"):
            continue
        r_mult = float(getattr(trade, "r_multiple", 0.0) or 0.0)
        buffer.append_entry(
            symbol,
            {
                "source": "replay_seed",
                "side": getattr(trade, "side", None),
                "entry": getattr(trade, "entry", None),
                "sl": getattr(trade, "sl", None),
                "tp": getattr(trade, "tp", None),
                "prob": getattr(trade, "prob", None),
            },
            now=now,
        )
        buffer.append_outcome(
            symbol,
            {
                "source": "replay_seed",
                "r_multiple": r_mult,
                "pnl_r": r_mult,
                "win": r_mult > 0,
            },
            now=now,
        )
        n += 1
    print(f"[{symbol}] seeded {n} labelled trades into experience buffer")
    return n


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    cfg = load_config()
    buffer = ExperienceBuffer(PROJECT_ROOT / "data" / "experience")
    symbols = list(cfg.active_symbols) if args.all or not args.symbol else [args.symbol]
    total = sum(seed_symbol(sym, cfg, buffer) for sym in symbols)
    print(f"total_seeded={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
