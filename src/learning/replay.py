"""Experience buffer: entry snapshots and closed-trade outcomes.

Entries and outcomes live in separate directories so retraining can read labels
without scanning every snapshot, and so a noisy scan day cannot crowd out the
outcome records that carry the learning signal.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ENTRIES = "entries"
OUTCOMES = "outcomes"

DEFAULT_MAX_LINES_PER_DAY = 500


class ExperienceBuffer:
    def __init__(
        self,
        directory: Path | str,
        max_lines_per_day: int = DEFAULT_MAX_LINES_PER_DAY,
    ) -> None:
        self.directory = Path(directory)
        self.max_lines_per_day = int(max_lines_per_day)
        for sub in (ENTRIES, OUTCOMES):
            (self.directory / sub).mkdir(parents=True, exist_ok=True)

    def _path(self, kind: str, symbol: str, now: datetime) -> Path:
        return self.directory / kind / f"{symbol.lower()}_{now.strftime('%Y%m%d')}.jsonl"

    def _append(
        self,
        kind: str,
        symbol: str,
        record: dict[str, Any],
        now: datetime | None,
    ) -> Path | None:
        now = now or datetime.now(timezone.utc)
        path = self._path(kind, symbol, now)
        if self._line_count(path) >= self.max_lines_per_day:
            logger.warning(
                "%s buffer for %s hit max_lines_per_day=%d; dropping record",
                kind,
                symbol,
                self.max_lines_per_day,
            )
            return None
        record = dict(record)
        record.setdefault("symbol", symbol)
        record.setdefault("ts", now.isoformat())
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
        return path

    @staticmethod
    def _line_count(path: Path) -> int:
        if not path.exists():
            return 0
        with open(path, encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())

    def append_entry(
        self,
        symbol: str,
        record: dict[str, Any],
        now: datetime | None = None,
    ) -> Path | None:
        return self._append(ENTRIES, symbol, record, now)

    def append_outcome(
        self,
        symbol: str,
        record: dict[str, Any],
        now: datetime | None = None,
    ) -> Path | None:
        return self._append(OUTCOMES, symbol, record, now)

    def _load(self, kind: str, symbol: str | None) -> list[dict[str, Any]]:
        pattern = f"{symbol.lower()}_*.jsonl" if symbol else "*.jsonl"
        rows: list[dict[str, Any]] = []
        for path in sorted((self.directory / kind).glob(pattern)):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return rows

    def load_entries(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return self._load(ENTRIES, symbol)

    def load_outcomes(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return self._load(OUTCOMES, symbol)

    def labelled_trades(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Entry snapshots joined to their outcome via decision_id."""
        outcomes = {
            row.get("decision_id"): row
            for row in self.load_outcomes(symbol)
            if row.get("decision_id")
        }
        joined: list[dict[str, Any]] = []
        for entry in self.load_entries(symbol):
            outcome = outcomes.get(entry.get("decision_id"))
            if outcome is None:
                continue
            merged = dict(entry)
            merged["outcome"] = outcome.get("outcome")
            merged["net_profit"] = outcome.get("net_profit")
            merged["closed_ts"] = outcome.get("ts")
            joined.append(merged)
        return joined
