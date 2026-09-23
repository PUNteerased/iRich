"""Shared fixtures. No test may read or write the running bot's state (TC-3)."""

from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.audit_log import AuditLog, ModelIdentity
from src.learning.confidence import ConfidenceStore
from src.learning.replay import ExperienceBuffer
from src.market_data import MockProvider, SymbolSpec, TickSnapshot
from src.risk import RiskBreakers

# Real FBS demo values captured via scripts/export_mt5_history.py --specs-only.
EURUSD_SPEC = SymbolSpec("EURUSD", 1e-05, 5, 1e-05, 1.0, filling_mode=3)
XAUUSD_SPEC = SymbolSpec("XAUUSD", 0.01, 2, 0.01, 1.0, filling_mode=3)
USDJPY_SPEC = SymbolSpec("USDJPY", 0.001, 3, 0.001, 0.6275927425175255, filling_mode=1)
BTCUSD_SPEC = SymbolSpec("BTCUSD", 0.01, 2, 0.01, 0.01, filling_mode=1)

SL_CAPS = {
    "EURUSD": {"min_pips": 10, "max_pips": 15},
    "USDJPY": {"min_pips": 12, "max_pips": 18},
    "XAUUSD": {"min_points": 150, "max_points": 250},
    "BTCUSD": {"min_dollars": 100, "max_dollars": 200},
}

SERVER_NOW = datetime(2026, 8, 17, 9, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Redirect state and log dirs into tmp_path, then remove them."""
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    state.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("IRICH_STATE_DIR", str(state))
    monkeypatch.setenv("IRICH_LOG_DIR", str(logs))
    yield {"state": state, "logs": logs}
    # pytest keeps recent tmp dirs; drop ours so repeated runs cannot pile up.
    shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.fixture
def spec():
    return EURUSD_SPEC


@pytest.fixture
def gold_spec():
    return XAUUSD_SPEC


@pytest.fixture
def provider():
    p = MockProvider()
    p.set_spec(EURUSD_SPEC)
    p.set_spec(XAUUSD_SPEC)
    p.set_tick("EURUSD", bid=1.09000, ask=1.09010, time=SERVER_NOW)
    p.set_tick("XAUUSD", bid=2400.00, ask=2400.30, time=SERVER_NOW)
    return p


@pytest.fixture
def breakers(isolated_state):
    return RiskBreakers(
        path=isolated_state["state"] / "breaker_state.json",
        monthly_max_dd_pct=10.0,
        daily_max_loss_r=3.0,
        max_consecutive_losses=3,
        risk_unit_usd=2.0,
    )


@pytest.fixture
def audit(isolated_state):
    log = AuditLog(directory=isolated_state["logs"], max_bytes=200_000, backup_count=2)
    yield log
    log.close()


@pytest.fixture
def experience(tmp_path):
    return ExperienceBuffer(tmp_path / "experience", max_lines_per_day=50)


@pytest.fixture
def confidence(isolated_state):
    return ConfidenceStore(
        path=isolated_state["state"] / "confidence_state.json",
        symbols=["EURUSD", "XAUUSD"],
        alpha=0.1,
    )


@pytest.fixture
def model_identity():
    return ModelIdentity(
        model_path="models/eurusd_sniper.pkl",
        model_mtime="2026-08-16T13:26:00+00:00",
        model_sha256="deadbeef",
    )


def make_tick(symbol: str, bid: float, ask: float) -> TickSnapshot:
    return TickSnapshot(symbol=symbol, bid=bid, ask=ask, time=SERVER_NOW)
