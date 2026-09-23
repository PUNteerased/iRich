"""Trade lifecycle bookkeeping and orphaned-deal reconciliation (G05, G06, G20).

The trade journal is the bot's memory of what it opened. MT5 remains the
execution truth (I-08), so on startup the journal is reconciled against deal
history: anything that closed while the bot was down still has to reach the
learning loop and the breakers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from .audit_log import TRADE_STATUS_CLOSED, TRADE_STATUS_EXECUTED, AuditLog, ModelIdentity
from .learning.confidence import ConfidenceStore
from .learning.replay import ExperienceBuffer
from .risk import RiskBreakers

logger = logging.getLogger(__name__)

# Who observed the close (QG-3). Recovered outcomes are as valid as live ones,
# but they arrive late, so analysis has to be able to separate them.
SOURCE_LIVE = "live_poll"
SOURCE_STARTUP_SYNC = "startup_sync"


@dataclass(frozen=True)
class ClosedDeal:
    """A deal that closed exposure, mapped away from MT5 structs."""

    deal_id: int
    position_id: int
    symbol: str
    profit: float
    commission: float = 0.0
    swap: float = 0.0
    price: float = 0.0
    volume: float = 0.0
    time: datetime | None = None

    @property
    def net_profit(self) -> float:
        return self.profit + self.commission + self.swap

    @property
    def outcome(self) -> str:
        """TP / SL / BE bucket used by the confidence store."""
        if self.net_profit > 0:
            return "tp"
        if self.net_profit < 0:
            return "sl"
        return "be"


class TradeLedger:
    def __init__(
        self,
        audit: AuditLog,
        experience: ExperienceBuffer,
        confidence: ConfidenceStore,
        breakers: RiskBreakers,
        rr: float = 3.0,
        drift: Any = None,
    ) -> None:
        self.audit = audit
        self.experience = experience
        self.confidence = confidence
        self.breakers = breakers
        self.rr = rr
        # Sprint 1 #4: duck-typed (needs only `.record(r_multiple)`) so this
        # module never has to import learning.drift_guard — the dependency
        # runs one way (main.py wires Runtime.drift in after both exist).
        self.drift = drift
        self._open: dict[int, dict[str, Any]] = {}
        self.reload()

    def reload(self) -> dict[int, dict[str, Any]]:
        """Rebuild the open-trade map from the journal, newest record winning."""
        open_trades: dict[int, dict[str, Any]] = {}
        for record in self.audit.read("trades"):
            position_id = record.get("position_id")
            if position_id is None:
                continue
            position_id = int(position_id)
            if record.get("status") == TRADE_STATUS_EXECUTED:
                open_trades[position_id] = record
            elif record.get("status") == TRADE_STATUS_CLOSED:
                open_trades.pop(position_id, None)
        self._open = open_trades
        return open_trades

    @property
    def open_trades(self) -> dict[int, dict[str, Any]]:
        return dict(self._open)

    def record_execution(
        self,
        decision_id: str,
        symbol: str,
        position_id: int,
        model: ModelIdentity | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        record = self.audit.trade(
            decision_id,
            symbol,
            TRADE_STATUS_EXECUTED,
            model=model,
            position_id=int(position_id),
            **fields,
        )
        self._open[int(position_id)] = record
        return record

    def adopt_open_position(
        self,
        position: Any,
        now: datetime,
    ) -> dict[str, Any]:
        """Journal an MT5 position the bot has no memory of opening (Sprint 1 #3).

        Without a journal entry here, `apply_closed_deal` can never fold this
        position's eventual close into breakers / confidence / experience, and
        `manage_open_position` has no `sl_distance` basis to trail from once
        break-even moves the live SL off the entry. `position` duck-types
        `OpenPosition` (ticket, symbol, is_buy, price_open, sl, tp, volume).
        Idempotent: adopting an already-tracked ticket is a no-op.
        """
        ticket = int(position.ticket)
        existing = self._open.get(ticket)
        if existing is not None:
            return existing
        sl_distance = abs(position.price_open - position.sl) if position.sl else 0.0
        decision_id = f"adopted-{ticket}"
        record = self.audit.trade(
            decision_id,
            position.symbol,
            TRADE_STATUS_EXECUTED,
            model=None,
            position_id=ticket,
            order_ticket=ticket,
            side="BUY" if position.is_buy else "SELL",
            requested_price=position.price_open,
            fill_price=position.price_open,
            sl=position.sl,
            tp=position.tp,
            volume=position.volume,
            sl_distance=sl_distance,
            initial_sl_distance=sl_distance,
            adopted=True,
            adopted_at=now.isoformat(),
        )
        self._open[ticket] = record
        logger.info(
            "Adopted open position %s ticket=%s entry=%s sl=%s tp=%s (no prior journal entry)",
            position.symbol,
            ticket,
            position.price_open,
            position.sl,
            position.tp,
        )
        return record

    @staticmethod
    def _r_multiple(entry: dict[str, Any] | None, deal: "ClosedDeal") -> float | None:
        """Realised R for the drift guard: net P/L over the risk basis at entry.

        Prefers the post-fill risk (includes slippage/commission); falls back
        to the pre-send figure. Returns None when there is no journal entry or
        no usable risk basis, so the caller can skip drift.record cleanly.
        """
        if not entry:
            return None
        risk_usd = entry.get("post_fill_risk_usd") or entry.get("pre_send_risk_usd")
        if not risk_usd:
            return None
        try:
            risk_usd = float(risk_usd)
        except (TypeError, ValueError):
            return None
        if risk_usd <= 0:
            return None
        return deal.net_profit / risk_usd

    def apply_closed_deal(
        self,
        deal: Any,
        now: datetime,
        source: str = SOURCE_LIVE,
    ) -> bool:
        """Fold one closed deal into breakers, confidence and experience.

        `source` records who saw the close: the running loop, or the startup sweep
        after downtime. Both produce identical learning data, and telling them
        apart afterwards is the only way to audit how much was recovered rather
        than observed.

        Idempotent: the breaker state persists processed deal ids, so replaying
        the same deal (restart, overlapping sync window) changes nothing.
        """
        if not self.breakers.register_closed_deal(deal.deal_id, deal.net_profit, now):
            return False

        entry = self._open.pop(int(deal.position_id), None)
        decision_id = str(entry.get("decision_id")) if entry else f"orphan-{deal.deal_id}"

        weight = self.confidence.update(deal.symbol, deal.outcome, rr=self.rr)
        r_multiple = self._r_multiple(entry, deal)
        if r_multiple is not None and self.drift is not None:
            self.drift.record(r_multiple)
        self.experience.append_outcome(
            deal.symbol,
            {
                "decision_id": decision_id,
                "deal_id": deal.deal_id,
                "position_id": deal.position_id,
                "outcome": deal.outcome,
                "net_profit": deal.net_profit,
                "profit": deal.profit,
                "commission": deal.commission,
                "swap": deal.swap,
                "close_price": deal.price,
                "closed_at": deal.time.isoformat(),
                "source": source,
                # No journal entry: the position was not opened by this bot, or the
                # journal was lost. The outcome still counts, the decision cannot.
                "journal_matched": entry is not None,
                "r_multiple": r_multiple,
            },
            now=now,
        )
        self.audit.trade(
            decision_id,
            deal.symbol,
            TRADE_STATUS_CLOSED,
            model=entry if entry else None,
            position_id=deal.position_id,
            deal_id=deal.deal_id,
            outcome=deal.outcome,
            net_profit=deal.net_profit,
            close_price=deal.price,
            closed_at=deal.time.isoformat(),
            confidence_weight=weight,
            source=source,
            journal_matched=entry is not None,
            breaker=self.breakers.snapshot(),
        )
        logger.info(
            "Closed %s %s net=%.2f outcome=%s w=%.3f source=%s%s",
            deal.symbol,
            decision_id,
            deal.net_profit,
            deal.outcome,
            weight,
            source,
            "" if entry else " (no journal entry)",
        )
        return True

    def apply_closed_deals(
        self,
        deals: Iterable[Any],
        now: datetime,
        source: str = SOURCE_LIVE,
    ) -> int:
        return sum(1 for deal in deals if self.apply_closed_deal(deal, now, source))

    def sync_window_start(self, now: datetime, lookback_days: int) -> datetime:
        """Earliest time worth sweeping: oldest unclosed trade, capped by lookback.

        Capping matters — a bot that was off for months must not pull its entire
        deal history into memory just to start up.
        """
        floor = now - timedelta(days=max(1, lookback_days))
        oldest: datetime | None = None
        for record in self._open.values():
            raw = record.get("ts")
            if not raw:
                continue
            try:
                ts = datetime.fromisoformat(str(raw))
            except ValueError:
                continue
            if oldest is None or ts < oldest:
                oldest = ts
        if oldest is None:
            return floor
        return max(floor, oldest - timedelta(minutes=1))


def sync_orphaned_deals(
    ledger: TradeLedger,
    deal_source: Any,
    now: datetime,
    lookback_days: int = 14,
) -> int:
    """Reconcile trades that closed while the bot was down. Returns count applied."""
    ledger.reload()
    if not ledger.open_trades:
        logger.info("Orphan sync: journal has no open trades")
        return 0
    since = ledger.sync_window_start(now, lookback_days)
    logger.info(
        "Orphan sync: %d open journal entries, sweeping deals since %s",
        len(ledger.open_trades),
        since.isoformat(),
    )
    deals = deal_source(since, now)
    applied = ledger.apply_closed_deals(deals, now, source=SOURCE_STARTUP_SYNC)
    logger.info("Orphan sync: reconciled %d deal(s)", applied)
    return applied
