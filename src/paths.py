"""Runtime state and log locations, overridable so tests never touch bot state."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

STATE_DIR_ENV = "IRICH_STATE_DIR"
LOG_DIR_ENV = "IRICH_LOG_DIR"


def state_dir(create: bool = True) -> Path:
    """Directory for breaker / sequence / confidence state files."""
    override = os.getenv(STATE_DIR_ENV)
    path = Path(override) if override else ROOT / "models"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir(create: bool = True) -> Path:
    """Directory for structured audit logs."""
    override = os.getenv(LOG_DIR_ENV)
    path = Path(override) if override else ROOT / "logs"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def halt_file() -> Path:
    """Kill switch flag (G14). Presence blocks new entries."""
    return ROOT / "HALT"
