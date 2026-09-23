"""ForexFactory calendar scrape + high-impact trading pause."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable
from xml.etree import ElementTree as ET

import requests

logger = logging.getLogger(__name__)


@dataclass
class CalendarEvent:
    title: str
    country: str
    impact: str
    datetime_utc: datetime
    forecast: str | None = None
    previous: str | None = None
    actual: str | None = None

    @property
    def is_high(self) -> bool:
        return self.impact.lower() in {"high", "red", "3"}


def _parse_float(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


class ForexFactoryCalendar:
    """Uses FF weekly XML feed (more stable than HTML scrape)."""

    FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"

    def __init__(self, timeout: int = 15) -> None:
        self.timeout = timeout
        self._cache: list[CalendarEvent] = []
        self._cache_at: datetime | None = None
        self.last_fetch_ok: bool = False

    @property
    def has_cache(self) -> bool:
        """Whether any calendar data is available for a fail-closed check."""
        return bool(self._cache)

    def fetch(self, force: bool = False) -> list[CalendarEvent]:
        now = datetime.now(timezone.utc)
        if (
            not force
            and self._cache_at
            and now - self._cache_at < timedelta(minutes=15)
            and self._cache
        ):
            return self._cache

        try:
            resp = requests.get(self.FEED_URL, timeout=self.timeout)
            resp.raise_for_status()
            events = self._parse_xml(resp.text)
            self._cache = events
            self._cache_at = now
            self.last_fetch_ok = True
            return events
        except Exception as exc:  # noqa: BLE001
            logger.warning("ForexFactory fetch failed: %s", exc)
            self.last_fetch_ok = False
            return self._cache

    def _parse_xml(self, xml_text: str) -> list[CalendarEvent]:
        root = ET.fromstring(xml_text)
        events: list[CalendarEvent] = []
        for node in root.findall("event"):
            title = (node.findtext("title") or "").strip()
            country = (node.findtext("country") or "").strip()
            impact = (node.findtext("impact") or "").strip()
            date_s = (node.findtext("date") or "").strip()
            time_s = (node.findtext("time") or "").strip()
            dt = self._parse_dt(date_s, time_s)
            if dt is None:
                continue
            events.append(
                CalendarEvent(
                    title=title,
                    country=country,
                    impact=impact,
                    datetime_utc=dt,
                    forecast=(node.findtext("forecast") or None),
                    previous=(node.findtext("previous") or None),
                    actual=(node.findtext("actual") or None),
                )
            )
        return events

    @staticmethod
    def _parse_dt(date_s: str, time_s: str) -> datetime | None:
        # Examples: date=07-15-2026, time=8:30am or All Day
        try:
            if not time_s or time_s.lower() in {"all day", "tentative"}:
                dt = datetime.strptime(date_s, "%m-%d-%Y")
                return dt.replace(tzinfo=timezone.utc)
            dt = datetime.strptime(f"{date_s} {time_s}", "%m-%d-%Y %I:%M%p")
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def high_impact_near(
        self,
        currencies: Iterable[str],
        pre_minutes: int = 30,
        post_minutes: int = 15,
        now: datetime | None = None,
    ) -> tuple[bool, CalendarEvent | None]:
        now = now or datetime.now(timezone.utc)
        curs = {c.upper() for c in currencies}
        # Map FF country codes loosely
        country_map = {"USD": "USD", "EUR": "EUR", "JPY": "JPY", "United States": "USD", "EMU": "EUR", "Japan": "JPY"}
        events = self.fetch()
        window_start = now - timedelta(minutes=post_minutes)
        window_end = now + timedelta(minutes=pre_minutes)

        for ev in events:
            if not ev.is_high:
                continue
            code = country_map.get(ev.country, ev.country.upper())
            if code not in curs and ev.country.upper() not in curs:
                # also match common labels
                if not any(c in ev.country.upper() for c in curs):
                    continue
            # Blackout: T-pre .. T+post  <=> event in [now-post, now+pre]
            if window_start <= ev.datetime_utc <= window_end:
                return True, ev
        return False, None

    def deviation_score(self, currencies: Iterable[str] = ("USD",)) -> float:
        """Average signed (actual-forecast) for recent high-impact prints."""
        scores: list[float] = []
        for ev in self.fetch():
            if not ev.is_high:
                continue
            if ev.country.upper() not in {c.upper() for c in currencies} and "USD" not in ev.country.upper():
                continue
            actual = _parse_float(ev.actual)
            forecast = _parse_float(ev.forecast)
            if actual is None or forecast is None or forecast == 0:
                continue
            scores.append((actual - forecast) / abs(forecast))
        if not scores:
            return 0.0
        return float(sum(scores) / len(scores))


def is_high_impact_near(
    calendar: ForexFactoryCalendar,
    currencies: Iterable[str],
    pre_minutes: int = 30,
    post_minutes: int = 15,
) -> tuple[bool, str]:
    blocked, ev = calendar.high_impact_near(currencies, pre_minutes, post_minutes)
    if blocked and ev:
        return True, f"{ev.country} {ev.title} @ {ev.datetime_utc.isoformat()}"
    return False, ""
