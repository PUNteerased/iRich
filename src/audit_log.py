"""Structured audit logs (G11): decisions, risk events, trade journal.

Every record carries a decision_id (G18) and the identity of the model that
made the call (path, mtime, hash) so a losing trade can be traced back to the
exact file that produced it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Mapping

from .paths import log_dir

DECISIONS = "decisions"
RISK_EVENTS = "risk_events"
TRADES = "trades"

DEFAULT_MAX_BYTES = 5_000_000
DEFAULT_BACKUP_COUNT = 5

MODEL_FIELDS = ("model_path", "model_mtime", "model_sha256")

TRADE_STATUS_EXECUTED = "EXECUTED"
TRADE_STATUS_CLOSED = "CLOSED"


@dataclass(frozen=True)
class ModelIdentity:
    """Which brain decided. Versions are never numbered; files are hashed."""

    model_path: str | None = None
    model_mtime: str | None = None
    model_sha256: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "model_path": self.model_path,
            "model_mtime": self.model_mtime,
            "model_sha256": self.model_sha256,
        }


UNKNOWN_MODEL = ModelIdentity()


def file_identity(path: Path | str, hash_bytes: int = 1_000_000) -> ModelIdentity:
    """Identity of a model file: path, mtime and a short content hash."""
    import hashlib

    p = Path(path)
    if not p.exists():
        return ModelIdentity(model_path=str(p))
    mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()
    digest = hashlib.sha256()
    with open(p, "rb") as handle:
        while chunk := handle.read(hash_bytes):
            digest.update(chunk)
    return ModelIdentity(
        model_path=str(p),
        model_mtime=mtime,
        model_sha256=digest.hexdigest()[:8],
    )


class AuditLog:
    """Append-only JSONL sinks with size-bounded rotation (TC-4)."""

    def __init__(
        self,
        directory: Path | str | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        backup_count: int = DEFAULT_BACKUP_COUNT,
    ) -> None:
        self.directory = Path(directory) if directory else log_dir()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self._loggers: dict[str, logging.Logger] = {}

    def _sink(self, stream: str) -> logging.Logger:
        cached = self._loggers.get(stream)
        if cached is not None:
            return cached
        path = self.directory / f"{stream}.jsonl"
        logger = logging.getLogger(f"irich.audit.{stream}.{id(self)}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handler = RotatingFileHandler(
            path,
            maxBytes=self.max_bytes,
            backupCount=self.backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        self._loggers[stream] = logger
        return logger

    def _write(self, stream: str, record: dict[str, Any]) -> dict[str, Any]:
        record.setdefault("ts", datetime.now(timezone.utc).isoformat())
        self._sink(stream).info(json.dumps(record, default=str))
        return record

    def decision(
        self,
        decision_id: str,
        symbol: str,
        code: Any,
        model: Mapping[str, Any] | ModelIdentity | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "decision_id": decision_id,
            "symbol": symbol,
            "code": str(code),
        }
        record.update(_model_dict(model))
        record.update(fields)
        return self._write(DECISIONS, record)

    def risk_event(
        self,
        decision_id: str | None,
        symbol: str,
        code: Any,
        **fields: Any,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "decision_id": decision_id,
            "symbol": symbol,
            "code": str(code),
        }
        record.update(fields)
        return self._write(RISK_EVENTS, record)

    def trade(
        self,
        decision_id: str,
        symbol: str,
        status: str,
        model: Mapping[str, Any] | ModelIdentity | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "decision_id": decision_id,
            "symbol": symbol,
            "status": status,
        }
        record.update(_model_dict(model))
        record.update(fields)
        return self._write(TRADES, record)

    def read(self, stream: str) -> list[dict[str, Any]]:
        """Read a stream back, current file plus rotated backups, oldest first."""
        base = self.directory / f"{stream}.jsonl"
        paths = [base.with_name(f"{base.name}.{i}") for i in range(self.backup_count, 0, -1)]
        paths.append(base)
        rows: list[dict[str, Any]] = []
        for path in paths:
            if not path.exists():
                continue
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return rows

    def close(self) -> None:
        for logger in self._loggers.values():
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)
        self._loggers.clear()


def _model_dict(model: Mapping[str, Any] | ModelIdentity | None) -> dict[str, Any]:
    if isinstance(model, ModelIdentity):
        return model.to_dict()
    if model is None:
        return UNKNOWN_MODEL.to_dict()
    return {field: model.get(field) for field in MODEL_FIELDS}
