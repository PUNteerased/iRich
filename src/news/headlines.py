"""Fetch recent FX/macro headlines for FinBERT (G12 — Phase 2.B).

Uses ForexFactory / public RSS when available; falls back to empty list so the
caller can keep a neutral score rather than a constant synthetic prompt.
"""

from __future__ import annotations

import logging
import urllib.request
import xml.etree.ElementTree as ET
from typing import List

logger = logging.getLogger(__name__)

# Public RSS endpoints (may change; fail soft).
FEEDS = (
    "https://www.forexlive.com/feed/news",
    "https://www.fxstreet.com/rss/news",
)


def fetch_headlines(limit: int = 8, timeout: float = 5.0) -> List[str]:
    headlines: list[str] = []
    for url in FEEDS:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                data = resp.read()
            root = ET.fromstring(data)
            for item in root.findall(".//item"):
                title = (item.findtext("title") or "").strip()
                if title:
                    headlines.append(title)
                if len(headlines) >= limit:
                    return headlines
        except Exception as exc:  # noqa: BLE001
            logger.debug("headline feed failed %s: %s", url, exc)
            continue
    return headlines
