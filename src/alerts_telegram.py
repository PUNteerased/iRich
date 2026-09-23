"""Telegram alert helper (Phase 2.C).

Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any


def send_telegram(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text[:3500]}).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=10) as resp:
            payload: dict[str, Any] = json.loads(resp.read().decode())
            return bool(payload.get("ok"))
    except Exception:  # noqa: BLE001
        return False
