"""Closed vocabulary of decision outcome codes (G07)."""

from __future__ import annotations

from collections import Counter
from enum import Enum


class Reject(str, Enum):
    """Reason a scan did not result in an order.

    Values are the wire format written to audit logs; never emit free text.
    """

    SPREAD = "REJECT_SPREAD"
    NEWS = "REJECT_NEWS"
    CALENDAR_UNAVAILABLE = "REJECT_CALENDAR_UNAVAILABLE"
    SL_RANGE = "REJECT_SL_RANGE"
    INVALID_SL_DISTANCE = "REJECT_INVALID_SL_DISTANCE"
    ENTRY_DRIFT = "REJECT_ENTRY_DRIFT"
    CONFIDENCE = "REJECT_CONFIDENCE"
    MTF = "REJECT_MTF"
    ATR_ABNORMAL = "REJECT_ATR_ABNORMAL"
    NEUTRAL_BIAS = "REJECT_NEUTRAL_BIAS"
    NEWS_CONTRADICT = "REJECT_NEWS_CONTRADICT"
    FUSION_SCORE = "REJECT_FUSION_SCORE"
    POSITION_EXISTS = "REJECT_POSITION_EXISTS"
    PRE_SEND_RISK_BREACH = "PRE_SEND_RISK_BREACH"
    RISK_CALC_FAILED = "REJECT_RISK_CALC_FAILED"
    MONTHLY_DD = "REJECT_MONTHLY_DD"
    DAILY_LOSS = "REJECT_DAILY_LOSS"
    CONSECUTIVE_LOSS = "REJECT_CONSECUTIVE_LOSS"
    BROKER_EXECUTION = "REJECT_BROKER_EXECUTION"
    NO_MONEY = "REJECT_NO_MONEY"
    NO_MODEL = "REJECT_NO_MODEL"
    DATA = "REJECT_DATA"
    MARKET_DATA_UNAVAILABLE = "REJECT_MARKET_DATA_UNAVAILABLE"
    HALT_FILE = "REJECT_HALT_FILE"
    FRIDAY_GAP = "REJECT_FRIDAY_GAP"
    # Sprint 4 #2: outside the symbol's configured UTC trading session.
    SESSION = "REJECT_SESSION"

    def __str__(self) -> str:
        return self.value


ACCEPTED = "OK"

# Recorded as a risk event but the trade stays open: closing it would realise
# the loss immediately, which is worse than the excess being logged.
POST_FILL_RISK_EXCESS = "POST_FILL_RISK_EXCESS"

# Sprint 2 #2: opposing_structure_tp extended TP beyond the base RR target.
# Informational risk event, not a reject — the trade still sends.
OPPOSING_TP_EXTEND = "OPPOSING_TP_EXTEND"

# Sprint 2 #5: hard time-stop flattened a position past risk.max_hold_bars_m1.
MAX_HOLD_FLATTEN = "MAX_HOLD_FLATTEN"

BREAKER_CODES = frozenset(
    {Reject.MONTHLY_DD, Reject.DAILY_LOSS, Reject.CONSECUTIVE_LOSS}
)


class RejectCounter:
    """Per-symbol tally so "why was there no trade this week" is answerable."""

    def __init__(self) -> None:
        self._counts: dict[str, Counter[str]] = {}

    def record(self, symbol: str, code: Reject | str) -> None:
        value = code.value if isinstance(code, Reject) else str(code)
        self._counts.setdefault(symbol, Counter())[value] += 1

    def for_symbol(self, symbol: str) -> dict[str, int]:
        return dict(self._counts.get(symbol, Counter()))

    def totals(self) -> dict[str, int]:
        total: Counter[str] = Counter()
        for counter in self._counts.values():
            total.update(counter)
        return dict(total)

    def summary(self) -> dict[str, dict[str, int]]:
        return {symbol: dict(counter) for symbol, counter in self._counts.items()}

    def reset(self) -> None:
        self._counts.clear()
