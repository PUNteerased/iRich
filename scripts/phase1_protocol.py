"""Phase 1 demo forward-test protocol — lock P1-1 criteria before collecting.

Edit GATE_TARGETS below BEFORE starting the Demo collection window.
Do not change targets after seeing results (P1-1).

    python scripts/phase1_protocol.py --status
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# --- LOCK THESE BEFORE THE COLLECTION WINDOW (P1-1) ---
GATE_TARGETS = {
    "min_total_fills": 30,
    "min_fills_per_symbol": 8,
    "max_broker_reject_rate": 0.15,
    "max_monthly_dd_pct": 10.0,
    "declared_at": "2026-09-22T00:00:00+00:00",
    "notes": "Locked for iRich Phase 1 micro_100 Demo forward test.",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--lock", action="store_true", help="Write locked targets to disk")
    args = parser.parse_args()
    out = ROOT / "data" / "phase1_gate_targets.json"
    if args.lock or not out.exists():
        payload = dict(GATE_TARGETS)
        payload["locked_at"] = datetime.now(timezone.utc).isoformat()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Locked P1-1 targets -> {out}")
    if args.status:
        print(out.read_text(encoding="utf-8") if out.exists() else "not locked")
    checklist = ROOT / "data" / "phase1_checklist.md"
    checklist.write_text(
        "\n".join(
            [
                "# Phase 1 Demo Forward Test Checklist",
                "",
                "- [ ] P1-1 targets locked in data/phase1_gate_targets.json",
                "- [ ] Phase 0 gates P0-9/P0-12/P0-3 complete",
                "- [ ] Demo bot running: python src/main.py",
                "- [ ] P1-2 broker reject rate + reconnect drill logged",
                "- [ ] P1-3 breaker events visible in risk_events.jsonl",
                "- [ ] P1-4 weekly RejectCounter summary exported",
                "- [ ] P1-5 SL/R:R journal vs intent within digits tolerance",
                "- [ ] P1-6 regime split notes written",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(f"Checklist -> {checklist}")


if __name__ == "__main__":
    main()
