"""Streamlit ops dashboard (Phase 2.C).

    streamlit run scripts/dashboard_streamlit.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_jsonl(path: Path, limit: int = 200) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def main() -> None:
    import streamlit as st

    from src.paths import log_dir, state_dir

    st.set_page_config(page_title="iRich Ops", layout="wide")
    st.title("iRich Ops Dashboard")
    st.caption("Read-only view of journals — no risk logic in UI.")

    base = log_dir()
    decisions = _load_jsonl(base / "decisions.jsonl")
    risks = _load_jsonl(base / "risk_events.jsonl")
    trades = _load_jsonl(base / "trades.jsonl")

    c1, c2, c3 = st.columns(3)
    c1.metric("Decisions (recent)", len(decisions))
    c2.metric("Risk events", len(risks))
    c3.metric("Trades", len(trades))

    breaker = state_dir() / "breaker_state.json"
    if breaker.exists():
        st.subheader("Breakers")
        st.json(json.loads(breaker.read_text(encoding="utf-8")))

    st.subheader("Recent rejects")
    codes = {}
    for d in decisions:
        code = str(d.get("code", "?"))
        codes[code] = codes.get(code, 0) + 1
    st.bar_chart(codes) if codes else st.write("No decisions yet.")

    st.subheader("Open / recent trades")
    st.json(trades[-20:])


if __name__ == "__main__":
    main()
