"""Sprint 4 #2: session entry filter.

Fills taken outside a symbol's liquid session are more likely to sit in thin
books and chop around structural levels without following through. This is a
simple UTC hour-of-day gate, applied only to new entries (never to managing an
already-open position — flattening/trailing logic is untouched).
"""

from __future__ import annotations

from datetime import datetime

# symbol -> (start_hour_utc inclusive, end_hour_utc exclusive)
SESSION_WINDOWS: dict[str, tuple[int, int]] = {
    "EURUSD": (7, 16),
    "USDJPY": (7, 16),
    "XAUUSD": (12, 20),
    "BTCUSD": (8, 22),
}


def in_session(symbol: str, utc_dt: datetime) -> bool:
    """True when `utc_dt` falls inside the symbol's configured UTC session.

    The window is [start_hour:00, end_hour:00) — the start minute is included,
    the end-hour boundary is not (16:00 is outside a 07:00-16:00 window,
    15:59 is inside). Symbols with no configured window are never filtered
    here — that is a caller/config decision, not a silent default.
    """
    window = SESSION_WINDOWS.get(symbol.upper())
    if window is None:
        return True
    start_hour, end_hour = window
    minute_of_day = utc_dt.hour * 60 + utc_dt.minute
    start = start_hour * 60
    end = end_hour * 60
    return start <= minute_of_day < end
