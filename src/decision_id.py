"""Correlation id linking one scan across every log (G18)."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from .paths import state_dir

logger = logging.getLogger(__name__)


class DecisionIdFactory:
    """Emits {YYYYMMDD}-{HHMMSS}-{SYMBOL}-{seq} on server time.

    The sequence resets on server-day rollover and is persisted so ids stay
    unique across restarts.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else state_dir() / "decision_seq.json"
        self._day_key: str | None = None
        self._seq = 0
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._day_key = data.get("day_key")
            self._seq = int(data.get("seq", 0))
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
            logger.warning("decision seq state unreadable (%s); starting fresh", exc)
            self._day_key, self._seq = None, 0

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"day_key": self._day_key, "seq": self._seq}
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def next_id(self, symbol: str, server_now: datetime) -> str:
        day_key = server_now.strftime("%Y%m%d")
        if day_key != self._day_key:
            self._day_key = day_key
            self._seq = 0
        self._seq += 1
        self._save()
        return f"{day_key}-{server_now.strftime('%H%M%S')}-{symbol.upper()}-{self._seq:05d}"
