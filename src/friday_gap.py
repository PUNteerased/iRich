"""Friday gap / weekend flat policy (Phase 2.B)."""

from __future__ import annotations

from datetime import datetime, time, timezone


def block_new_entries_friday(
    server_now: datetime,
    *,
    block_from_hour_utc: int = 18,
) -> tuple[bool, str]:
    """Stop new entries late Friday server time to avoid weekend gap risk."""
    if server_now.tzinfo is None:
        server_now = server_now.replace(tzinfo=timezone.utc)
    if server_now.weekday() == 4 and server_now.time() >= time(block_from_hour_utc, 0):
        return True, "friday_gap_guard"
    return False, ""
