"""Hybrid signal fusion gates for sniper entries."""

from __future__ import annotations

from dataclasses import dataclass

from .learning.confidence import ConfidenceStore
from .news.sentiment import SentimentEngine
from .reject_codes import ACCEPTED, Reject


@dataclass
class FusionDecision:
    signal: str  # BUY | SELL | WAIT
    score: float
    code: str
    detail: str = ""
    threshold: float = 0.0


def fuse_signal(
    symbol: str,
    side: str,
    model_prob: float,
    min_prob: float,
    news_blocked: bool,
    news_block_reason: str,
    breaker_code: Reject | None,
    has_open_position: bool,
    mtf_aligned: bool,
    mtf_reasons: list[str],
    usd_sentiment: float,
    confidence: ConfidenceStore,
    tech_weight: float = 0.6,
    news_weight: float = 0.4,
    sentiment_engine: SentimentEngine | None = None,
    mtf_soft_score: float = 0.0,
    online_confidence: bool = True,
) -> FusionDecision:
    """Hard gates first; the weighted score can never override them (I-07).

    Sprint 4 #4: when `online_confidence` is False, the threshold stays at the
    fixed `min_prob` — ConfidenceStore is still updated on trade outcomes
    elsewhere (learning), but must not raise the entry bar here until a
    walk-forward run recommends enabling it (`learning.online_confidence`).
    """
    thr = confidence.effective_min_prob(min_prob, symbol) if online_confidence else float(min_prob)

    if breaker_code is not None:
        return FusionDecision("WAIT", 0.0, breaker_code, "circuit_breaker", thr)
    if has_open_position:
        return FusionDecision("WAIT", 0.0, Reject.POSITION_EXISTS, "max_open_positions", thr)
    if news_blocked:
        code = (
            Reject.CALENDAR_UNAVAILABLE
            if news_block_reason == "calendar_unavailable_fail_closed"
            else Reject.NEWS
        )
        return FusionDecision("WAIT", 0.0, code, news_block_reason, thr)
    if not mtf_aligned:
        return FusionDecision(
            "WAIT", 0.0, Reject.MTF, ",".join(mtf_reasons or ["misaligned"]), thr
        )

    if model_prob < thr:
        return FusionDecision(
            "WAIT",
            model_prob,
            Reject.CONFIDENCE,
            f"prob_{model_prob:.3f}_lt_{thr:.3f}",
            thr,
        )

    # technical signed confidence in [-1,1]
    tech = model_prob if side == "BUY" else -model_prob
    engine = sentiment_engine or SentimentEngine()
    news_bias = engine.map_to_symbol_bias(symbol, usd_sentiment)
    final = tech_weight * tech + news_weight * news_bias

    # Sprint 4 #1: soft-MTF bonus (sweep/choch, only when require_full_mtf is
    # false) nudges the score toward the signalled side instead of hard-
    # rejecting on a missing sweep/choch.
    if mtf_soft_score:
        final = final + mtf_soft_score if side == "BUY" else final - mtf_soft_score

    # News must not strongly contradict
    if side == "BUY" and news_bias < -0.55:
        return FusionDecision("WAIT", final, Reject.NEWS_CONTRADICT, "contradict_buy", thr)
    if side == "SELL" and news_bias > 0.55:
        return FusionDecision("WAIT", final, Reject.NEWS_CONTRADICT, "contradict_sell", thr)

    if side == "BUY" and final <= 0:
        return FusionDecision("WAIT", final, Reject.FUSION_SCORE, "non_positive", thr)
    if side == "SELL" and final >= 0:
        return FusionDecision("WAIT", final, Reject.FUSION_SCORE, "non_negative", thr)

    return FusionDecision(side, final, ACCEPTED, "ok", thr)
