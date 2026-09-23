"""FinBERT hawkish/dovish sentiment for USD-centric news."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Iterable

logger = logging.getLogger(__name__)


@dataclass
class SentimentResult:
    score: float  # -1 dovish USD .. +1 hawkish USD
    label: str
    cached: bool = False


class SentimentEngine:
    """
    Lazy-loads ProsusAI/finbert. Falls back to keyword heuristic if unavailable.
    score > 0 => hawkish USD (EURUSD/XAUUSD bearish bias, USDJPY bullish bias)
    """

    def __init__(self, cache_minutes: int = 60) -> None:
        self.cache_minutes = cache_minutes
        self._pipe = None
        self._load_failed = False
        self._cache: dict[str, tuple[float, SentimentResult]] = {}

    def _ensure_model(self) -> bool:
        if self._pipe is not None:
            return True
        if self._load_failed:
            return False
        try:
            from transformers import pipeline

            self._pipe = pipeline(
                "sentiment-analysis",
                model="ProsusAI/finbert",
                tokenizer="ProsusAI/finbert",
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("FinBERT unavailable, using heuristic: %s", exc)
            self._load_failed = True
            return False

    def score_texts(self, texts: Iterable[str]) -> SentimentResult:
        joined = " ".join(t.strip() for t in texts if t and t.strip())
        if not joined:
            return SentimentResult(0.0, "neutral")

        key = hashlib.sha256(joined.encode("utf-8")).hexdigest()
        now = time.time()
        if key in self._cache:
            ts, val = self._cache[key]
            if now - ts < self.cache_minutes * 60:
                return SentimentResult(val.score, val.label, cached=True)

        if self._ensure_model():
            try:
                out = self._pipe(joined[:512])[0]
                label = str(out["label"]).lower()
                conf = float(out["score"])
                if "positive" in label:
                    score = conf  # treat positive financial tone as hawkish-leaning for USD feeds
                elif "negative" in label:
                    score = -conf
                else:
                    score = 0.0
                # Refine with hawk/dove keywords
                score = self._blend_keywords(joined, score)
                result = SentimentResult(max(-1.0, min(1.0, score)), label)
            except Exception as exc:  # noqa: BLE001
                logger.warning("FinBERT inference failed: %s", exc)
                result = self._heuristic(joined)
        else:
            result = self._heuristic(joined)

        self._cache[key] = (now, result)
        return result

    def map_to_symbol_bias(self, symbol: str, usd_hawkish_score: float) -> float:
        """
        Return signed bias for the symbol in [-1, 1]:
        positive => favor BUY, negative => favor SELL.
        """
        s = symbol.upper()
        if s in ("EURUSD", "XAUUSD"):
            return -usd_hawkish_score
        if s == "USDJPY":
            return usd_hawkish_score
        if s == "BTCUSD":
            # Hawkish USD / risk-off often weighs on BTC
            return -usd_hawkish_score
        return 0.0

    @staticmethod
    def _blend_keywords(text: str, base: float) -> float:
        t = text.lower()
        hawk = sum(k in t for k in ("hike", "restrictive", "inflation", "hawkish", "tighten"))
        dove = sum(k in t for k in ("cut", "dovish", "ease", "accommodative", "stimulus"))
        adj = 0.15 * (hawk - dove)
        return base + adj

    @classmethod
    def _heuristic(cls, text: str) -> SentimentResult:
        score = cls._blend_keywords(text, 0.0)
        score = max(-1.0, min(1.0, score))
        label = "hawkish" if score > 0.1 else "dovish" if score < -0.1 else "neutral"
        return SentimentResult(score, label)
