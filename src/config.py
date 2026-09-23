"""Load config.yaml and optional .env overrides."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def _deep_get(d: dict, *keys: str, default: Any = None) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


class Config:
    def __init__(self, path: Path | str | None = None) -> None:
        load_dotenv(ROOT / ".env")
        cfg_path = Path(path) if path else ROOT / "config.yaml"
        with open(cfg_path, encoding="utf-8") as f:
            self._raw: dict[str, Any] = yaml.safe_load(f) or {}

        self._spread_calibration: dict[str, Any] | None = None
        # Optional label only — runtime behaviour is identical for Demo and Real
        # accounts. Portfolio balance / leverage always come from MT5 account_info.
        self.mode = str(self._raw.get("mode", "portfolio")).lower()

        # MT5 credentials: .env / yaml first, then active dashboard account wins
        mt5 = self._raw.setdefault("mt5", {})
        mt5["login"] = _env_int("MT5_LOGIN", mt5.get("login"))
        mt5["password"] = os.getenv("MT5_PASSWORD") or mt5.get("password")
        mt5["server"] = os.getenv("MT5_SERVER") or mt5.get("server")
        mt5["path"] = os.getenv("MT5_PATH") or mt5.get("path")
        try:
            from .accounts import apply_active_account

            apply_active_account(mt5)
        except Exception:  # noqa: BLE001 — never block boot on account store issues
            pass

    @property
    def raw(self) -> dict[str, Any]:
        return self._raw

    def get(self, *keys: str, default: Any = None) -> Any:
        return _deep_get(self._raw, *keys, default=default)

    @property
    def symbols(self) -> list[str]:
        return list(self._raw.get("symbols", []))

    @property
    def weekday_symbols(self) -> list[str]:
        return list(
            self._raw.get(
                "weekday_symbols",
                [s for s in self.symbols if s.upper() != "BTCUSD"],
            )
        )

    @property
    def weekend_symbols(self) -> list[str]:
        return list(self._raw.get("weekend_symbols", ["BTCUSD"]))

    def active_symbols(self, now: datetime) -> list[str]:
        """Mon-Fri = FX/Gold, Sat-Sun = BTCUSD.

        `now` must come from the server clock (I-09); there is deliberately no
        local-time default so a caller cannot pick the wrong clock by omission.
        """
        if now.weekday() >= 5:  # 5=Sat, 6=Sun
            return list(self.weekend_symbols)
        return list(self.weekday_symbols)

    def atr_sl_timeframe(self, symbol: str) -> str:
        by_sym = self.get("risk", "atr_sl_timeframe_by_symbol", default={}) or {}
        if symbol in by_sym:
            return str(by_sym[symbol]).upper()
        return str(self.get("risk", "atr_sl_timeframe", default="M5")).upper()

    def atr_sl_mult(self, symbol: str) -> float:
        by_sym = self.get("risk", "atr_sl_mult_by_symbol", default={}) or {}
        if symbol in by_sym:
            return float(by_sym[symbol])
        return float(self.get("risk", "atr_sl_mult", default=1.0))

    def max_spread_points(self, symbol: str) -> float | None:
        """Calibrated spread cap in points, or None to fail closed (G10).

        An explicit number in config.yaml wins; otherwise the measured value
        from the calibration file is used. `TBD_CALIBRATE` with no calibration
        on file means unmeasured, and the risk gate rejects rather than letting
        an unbounded spread through.
        """
        caps = self.get("spread", "max_points_by_symbol", default={}) or {}
        value = caps.get(symbol)
        if value is not None and not isinstance(value, str):
            return float(value)
        return self.calibrated_spread_points(symbol)

    @property
    def spread_calibration_path(self) -> Path:
        return ROOT / self.get(
            "spread", "calibration_path", default="data/spread_calibration.json"
        )

    def calibrated_spread_points(self, symbol: str) -> float | None:
        if self._spread_calibration is None:
            self._spread_calibration = _read_json(self.spread_calibration_path)
        entry = (self._spread_calibration.get("symbols") or {}).get(symbol) or {}
        value = entry.get("max_points")
        return float(value) if value is not None else None

    def calibrated_avg_spread_points(self, symbol: str) -> float | None:
        """Average/typical spread from calibration, or None when unmeasured (Sprint 1 #5).

        Prefers an explicit average field; falls back to the mean of raw
        samples if `calibrate_spread.py` stored them. No synthetic estimate is
        produced — callers must treat None as "skip this check".
        """
        if self._spread_calibration is None:
            self._spread_calibration = _read_json(self.spread_calibration_path)
        entry = (self._spread_calibration.get("symbols") or {}).get(symbol) or {}
        for key in ("avg_points", "average_points", "mean_points", "typical_points"):
            value = entry.get(key)
            if value is not None:
                return float(value)
        samples = entry.get("samples")
        if samples:
            try:
                return float(sum(samples) / len(samples))
            except (TypeError, ZeroDivisionError):
                return None
        return None

    @property
    def spread_max_mult_of_avg(self) -> float:
        return float(self.get("spread", "max_mult_of_avg", default=2.0))

    def symbols_pending_spread_calibration(self) -> list[str]:
        """Symbols still fail-closed on an unmeasured spread cap (bootstrap note)."""
        return [s for s in self.symbols if self.max_spread_points(s) is None]

    @property
    def max_entry_drift_r(self) -> float:
        return float(self.get("risk", "max_entry_drift_r", default=0.25))

    @property
    def daily_max_loss_r(self) -> float:
        return float(self.get("risk", "daily_max_loss_r", default=3.0))

    @property
    def max_consecutive_losses(self) -> int:
        return int(self.get("risk", "max_consecutive_losses", default=3))

    @property
    def orphan_lookback_days(self) -> int:
        return int(self.get("learning", "orphan_lookback_days", default=14))

    def order_deviation(self, symbol: str) -> int:
        by_sym = self.get("mt5", "deviation_by_symbol", default={}) or {}
        if symbol in by_sym:
            return int(by_sym[symbol])
        return int(self.get("mt5", "deviation", default=20))

    @property
    def magic(self) -> int:
        return int(self._raw.get("magic", 999100))

    @property
    def lot_volume(self) -> float:
        """Fallback lot when money_management section is absent."""
        return float(self.get("lot", "volume", default=0.01))

    @property
    def max_risk_usd(self) -> float:
        """Fallback max risk when money_management section is absent."""
        return float(self.get("risk", "max_risk_usd", default=2.0))

    def money_management_cfg(self) -> dict[str, Any]:
        raw = self.get("money_management", default=None)
        if isinstance(raw, dict) and raw:
            return raw
        # Synthesize FIXED_TIER micro_100 from legacy keys.
        return {
            "mode": "FIXED_TIER",
            "fixed_tier": [
                {
                    "min_balance": 0,
                    "max_balance": 199.99,
                    "lot": self.lot_volume,
                    "max_risk_usd": self.max_risk_usd,
                }
            ],
            "dynamic": {
                "risk_per_trade_pct": 2.0,
                "max_risk_usd_cap": 50.0,
                "max_lot_size": 1.0,
                "min_lot_size": 0.01,
            },
        }

    @property
    def rr(self) -> float:
        return float(self.get("risk", "rr", default=3.0))

    @property
    def min_probability(self) -> float:
        return float(self.get("sniper", "min_probability", default=0.75))

    @property
    def scan_seconds(self) -> int:
        return int(self.get("sniper", "scan_seconds", default=10))

    def model_path(self, symbol: str) -> Path:
        models_dir = ROOT / self.get("paths", "models", default="models")
        return models_dir / f"{symbol.lower()}_sniper.pkl"

    def resolve_path(self, *keys: str) -> Path:
        rel = self.get(*keys, default="")
        p = Path(rel)
        return p if p.is_absolute() else ROOT / p


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except (json.JSONDecodeError, OSError):
        return {}


def _env_int(name: str, fallback: Any) -> int | None:
    val = os.getenv(name)
    if val is None or val == "":
        return int(fallback) if fallback not in (None, "") else None
    return int(val)


def load_config(path: Path | str | None = None) -> Config:
    return Config(path)
