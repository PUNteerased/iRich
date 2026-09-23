"""Q-style confidence weights from TP/SL/BE outcomes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ConfidenceStore:
    def __init__(
        self,
        path: Path | str,
        symbols: list[str],
        alpha: float = 0.1,
        w_min: float = 0.3,
        w_max: float = 2.0,
    ) -> None:
        self.path = Path(path)
        self.alpha = alpha
        self.w_min = w_min
        self.w_max = w_max
        self.weights: dict[str, float] = {s: 1.0 for s in symbols}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for k, v in data.get("weights", {}).items():
                self.weights[k] = float(v)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"weights": self.weights}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def get(self, symbol: str) -> float:
        return float(self.weights.get(symbol, 1.0))

    def update(self, symbol: str, outcome: str, rr: float = 3.0) -> float:
        """
        outcome: 'tp' | 'sl' | 'be'
        """
        w = self.get(symbol)
        if outcome == "tp":
            reward = 1.0 * rr
        elif outcome == "sl":
            reward = -1.0
        elif outcome == "be":
            reward = 0.1
        else:
            reward = 0.0
        w = w + self.alpha * (reward - w)
        w = max(self.w_min, min(self.w_max, w))
        self.weights[symbol] = w
        self.save()
        return w

    def effective_min_prob(self, base: float, symbol: str) -> float:
        """Lower W => higher required probability; never below base."""
        w = self.get(symbol)
        if w < 1.0:
            return min(0.95, base / max(w, 1e-6))
        return base
