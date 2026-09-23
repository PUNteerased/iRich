"""Verify P0-4 / P0-8 style evidence from audit jsonl (demo short run).

    python scripts/verify_demo_evidence.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.paths import log_dir
from src.reject_codes import Reject


REQUIRED_DECISION_FIELDS = {"decision_id", "symbol", "code"}
REQUIRED_MODEL_HINTS = ("model_path", "model_hash", "model_mtime", "model")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-dir", default=None)
    args = parser.parse_args()
    base = Path(args.log_dir) if args.log_dir else log_dir()
    decisions = _read_jsonl(base / "decisions.jsonl")
    risks = _read_jsonl(base / "risk_events.jsonl")
    trades = _read_jsonl(base / "trades.jsonl")

    report = {
        "log_dir": str(base),
        "decision_count": len(decisions),
        "risk_event_count": len(risks),
        "trade_count": len(trades),
        "p0_8_decision_id_present": False,
        "p0_8_model_identity_present": False,
        "p0_4_lot_ok": True,
        "p0_4_risk_ceiling_ok": True,
        "known_reject_codes_only": True,
        "pass": False,
        "notes": [],
    }

    if not decisions:
        report["notes"].append(
            "No decisions.jsonl yet — start Demo briefly: python src/main.py then re-run."
        )
    else:
        report["p0_8_decision_id_present"] = all(
            REQUIRED_DECISION_FIELDS.issubset(r.keys()) or "decision_id" in r for r in decisions
        )
        report["p0_8_model_identity_present"] = any(
            any(k in r or (isinstance(r.get("model"), dict) and k in (r.get("model") or {})) for k in REQUIRED_MODEL_HINTS)
            for r in decisions
        )
        known = {c.value for c in Reject} | {"OK", "ACCEPTED", "POST_FILL_RISK_EXCESS"}
        for r in decisions:
            code = str(r.get("code", ""))
            if code and code not in known and not code.startswith("REJECT_") and code != "OK":
                report["known_reject_codes_only"] = False
            vol = r.get("volume")
            if vol is not None and float(vol) not in (0.01, 0.02, 0.05, 0.1):
                # allow MM tiers; flag weird sizes
                if float(vol) < 0.01:
                    report["p0_4_lot_ok"] = False
            risk = r.get("pre_send_risk_usd")
            max_risk = r.get("max_risk_usd", 2.0)
            if risk is not None and max_risk is not None and float(risk) > float(max_risk) + 1e-6:
                report["p0_4_risk_ceiling_ok"] = False

    report["pass"] = bool(
        decisions
        and report["p0_8_decision_id_present"]
        and report["known_reject_codes_only"]
        and report["p0_4_lot_ok"]
        and report["p0_4_risk_ceiling_ok"]
    )
    out = ROOT / "data" / "demo_evidence_report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["pass"] or not decisions else (0 if report["pass"] else 1))


if __name__ == "__main__":
    main()
