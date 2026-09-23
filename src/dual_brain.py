"""Optional dual-brain gate: XGBoost primary + ANN confirm (Phase 2.B).

ANN is optional; when the model file is missing the gate is a no-op pass-through
so Phase 0/1 behaviour is unchanged.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class DualBrainGate:
    def __init__(self, models_dir: Path | str, min_ann_prob: float = 0.55) -> None:
        self.models_dir = Path(models_dir)
        self.min_ann_prob = min_ann_prob
        self._models: dict[str, Any] = {}

    def _load(self, symbol: str) -> Any | None:
        if symbol in self._models:
            return self._models[symbol]
        path = self.models_dir / f"{symbol.lower()}_ann.pkl"
        if not path.exists():
            self._models[symbol] = None
            return None
        try:
            import joblib

            self._models[symbol] = joblib.load(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ANN load failed %s: %s", symbol, exc)
            self._models[symbol] = None
        return self._models[symbol]

    def confirm(
        self,
        symbol: str,
        side: str,
        feature_row: Any,
        feature_cols: list[str],
    ) -> tuple[bool, float]:
        model = self._load(symbol)
        if model is None:
            return True, 1.0  # no ANN → pass
        try:
            x = np.asarray([[float(feature_row[c]) for c in feature_cols]], dtype=float)
            proba = model.predict_proba(x)[0]
            classes = [int(c) for c in getattr(model, "classes_", [0, 1, 2])]
            target = 1 if str(side).upper() == "BUY" else 2
            p = float(proba[classes.index(target)]) if target in classes else 0.0
            return p >= self.min_ann_prob, p
        except Exception as exc:  # noqa: BLE001
            logger.warning("ANN confirm failed: %s", exc)
            return True, 1.0
