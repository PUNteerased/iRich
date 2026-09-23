"""Unit tests for safe / efficient telemetry readers (no MT5)."""

from __future__ import annotations

import json
from pathlib import Path

from src.telemetry import count_codes_today, safe_read_json, tail_jsonl


def test_tail_jsonl_returns_last_n(tmp_path: Path):
    path = tmp_path / "decisions.jsonl"
    lines = [json.dumps({"i": i, "code": "REJECT_SPREAD", "ts": f"2026-09-23T00:00:{i:02d}+00:00"}) for i in range(50)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rows = tail_jsonl(path, 5)
    assert [r["i"] for r in rows] == [45, 46, 47, 48, 49]


def test_tail_jsonl_skips_broken_lines(tmp_path: Path):
    path = tmp_path / "decisions.jsonl"
    path.write_text(
        '{"i":1,"code":"A","ts":"2026-09-23T01:00:00+00:00"}\n'
        "NOT_JSON\n"
        '{"i":2,"code":"B","ts":"2026-09-23T01:00:01+00:00"}\n',
        encoding="utf-8",
    )
    rows = tail_jsonl(path, 10)
    assert [r["i"] for r in rows] == [1, 2]


def test_safe_read_json_retries_partial(tmp_path: Path):
    path = tmp_path / "breaker_state.json"
    path.write_text('{"ok": true, "n": 1}', encoding="utf-8")
    assert safe_read_json(path) == {"ok": True, "n": 1}
    path.write_text('{"ok":', encoding="utf-8")
    assert safe_read_json(path, retries=1) is None


def test_count_codes_today_from_tail_window(tmp_path: Path):
    path = tmp_path / "decisions.jsonl"
    rows = []
    for i in range(5):
        rows.append({"code": "REJECT_SPREAD", "ts": f"2026-09-22T12:00:{i:02d}+00:00"})
    for i in range(3):
        rows.append({"code": "REJECT_SESSION", "ts": f"2026-09-23T12:00:{i:02d}+00:00"})
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    tallied = count_codes_today(path, day_key="20260923")
    assert tallied == {"REJECT_SESSION": 3}
