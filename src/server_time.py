"""Single clock (I-09): MT5 server time from a cached offset (G01A).

MetaTrader5 is a synchronous API, so asking the terminal for the time on every
scan adds a round trip per symbol per loop. The offset is captured on connect
and refreshed only on reconnect or when the server day rolls over.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

logger = logging.getLogger(__name__)

ServerTimeFn = Callable[[], datetime | None]


class ServerClock:
    def __init__(self, server_time_fn: ServerTimeFn) -> None:
        self._server_time_fn = server_time_fn
        self._offset = timedelta(0)
        self._synced = False
        self._day_key: str | None = None

    @property
    def synced(self) -> bool:
        return self._synced

    @property
    def offset_seconds(self) -> float:
        return self._offset.total_seconds()

    def sync(self) -> bool:
        """Re-read server time and recompute the offset. Call on connect."""
        server_now = self._server_time_fn()
        if server_now is None:
            logger.warning("ServerClock sync failed; keeping offset=%.1fs", self.offset_seconds)
            return False
        if server_now.tzinfo is None:
            server_now = server_now.replace(tzinfo=timezone.utc)
        self._offset = server_now - datetime.now(timezone.utc)
        self._synced = True
        self._day_key = self.now().strftime("%Y%m%d")
        logger.info("ServerClock synced: offset=%.1fs server_now=%s", self.offset_seconds, server_now)
        return True

    def now(self) -> datetime:
        """Server time without touching MT5."""
        return datetime.now(timezone.utc) + self._offset

    def now_checked(self) -> datetime:
        """Server time, resyncing once when the server day changes."""
        current = self.now()
        key = current.strftime("%Y%m%d")
        if self._day_key is None:
            self._day_key = key
        elif key != self._day_key:
            logger.info("Server day rollover %s -> %s; resyncing clock", self._day_key, key)
            self._day_key = key
            if self.sync():
                current = self.now()
        return current

    def weekday(self) -> int:
        return self.now().weekday()

    def date_key(self, now: datetime | None = None) -> str:
        return (now or self.now()).strftime("%Y%m%d")

    def month_key(self, now: datetime | None = None) -> str:
        return (now or self.now()).strftime("%Y-%m")


class FixedClock(ServerClock):
    """Deterministic clock for tests and replay."""

    def __init__(self, now: datetime) -> None:
        super().__init__(lambda: now)
        self._fixed = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        self._synced = True
        self._day_key = self._fixed.strftime("%Y%m%d")

    def set(self, now: datetime) -> None:
        self._fixed = now if now.tzinfo else now.replace(tzinfo=timezone.utc)

    def sync(self) -> bool:
        return True

    def now(self) -> datetime:
        return self._fixed
