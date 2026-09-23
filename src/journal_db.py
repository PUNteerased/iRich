"""SQLite WAL store for decisions / risk / trades (Phase 2.C)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping


class JournalDB:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                decision_id TEXT,
                symbol TEXT,
                code TEXT,
                payload TEXT
            );
            CREATE TABLE IF NOT EXISTS risk_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                decision_id TEXT,
                symbol TEXT,
                code TEXT,
                payload TEXT
            );
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                decision_id TEXT,
                symbol TEXT,
                status TEXT,
                payload TEXT
            );
            """
        )
        self._conn.commit()

    def insert(
        self,
        table: str,
        ts: str,
        decision_id: str | None,
        symbol: str,
        code: str,
        payload: Mapping[str, Any],
    ) -> None:
        body = json.dumps(dict(payload), default=str)
        if table == "trades":
            self._conn.execute(
                "INSERT INTO trades (ts, decision_id, symbol, status, payload) VALUES (?,?,?,?,?)",
                (ts, decision_id, symbol, code, body),
            )
        else:
            self._conn.execute(
                f"INSERT INTO {table} (ts, decision_id, symbol, code, payload) VALUES (?,?,?,?,?)",
                (ts, decision_id, symbol, code, body),
            )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
