"""Shared portfolio snapshot between the trading bot and telemetry API.

The MetaTrader5 Python binding is typically single-process on Windows. While
`src/main.py` holds the connection, the dashboard must NOT call
`mt5.initialize()` / `shutdown()` or it will knock the bot offline.

The bot writes this snapshot every scan; telemetry reads it (file-only).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import state_dir

SNAPSHOT_NAME = "portfolio_snapshot.json"
DEFAULT_MAX_AGE_SEC = 45.0


def snapshot_path() -> Path:
    return state_dir() / SNAPSHOT_NAME


def write_portfolio_snapshot(
    *,
    login: int,
    server: str,
    is_demo: bool,
    currency: str,
    balance: float,
    equity: float,
    free_margin: float,
    margin: float,
    leverage: float,
    positions: list[dict[str, Any]],
    active_symbols: list[str] | None = None,
) -> Path:
    path = snapshot_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "available": True,
        "source": "bot_snapshot",
        "ts": datetime.now(timezone.utc).isoformat(),
        "monotonic": time.monotonic(),
        "login": int(login),
        "server": str(server),
        "is_demo": bool(is_demo),
        "currency": str(currency or ""),
        "balance": float(balance),
        "equity": float(equity),
        "free_margin": float(free_margin),
        "margin": float(margin),
        "leverage": float(leverage),
        "positions": positions,
        "active_symbols": list(active_symbols or []),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, default=str), encoding="utf-8")
    tmp.replace(path)
    return path


def read_portfolio_snapshot(max_age_sec: float = DEFAULT_MAX_AGE_SEC) -> dict[str, Any] | None:
    """Return snapshot dict if present and fresh enough; else None."""
    path = snapshot_path()
    if not path.exists():
        return None
    try:
        age = time.time() - path.stat().st_mtime
        if age > max_age_sec:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("available"):
        return None
    data["snapshot_age_sec"] = round(age, 2)
    return data
