"""Risk guards for the $100 micro account: fixed lot, $2 ceiling, breakers.

Nothing here talks to MetaTrader5. Prices and symbol specs arrive through
MarketDataProvider (G19) so the same code path runs live and in replay.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .market_data import SymbolSpec, TickSnapshot, is_buy_side
from .paths import state_dir
from .reject_codes import ACCEPTED, Reject

logger = logging.getLogger(__name__)

# Cap keys in config -> the spec attribute their numbers are denominated in.
_CAP_UNITS = (
    ("min_pips", "max_pips", "pip"),
    ("min_points", "max_points", "point"),
    ("min_dollars", "max_dollars", "price"),
)

PROCESSED_DEAL_HISTORY = 500

# Sprint 2 #1 (Hold Longer, live): once favour crosses `trigger_r`, floor the
# SL at entry +/- `lock_r * initial_sl_distance` (never loosens). Mirrors
# `src.backtest.replay._staircase_floor`/`STAIRCASE_LEVELS` so live and replay
# stay in lockstep, without either module importing the other's internals.
DEFAULT_STAIRCASE_LEVELS: tuple[tuple[float, float], ...] = (
    (3.0, 1.0),
    (4.0, 2.0),
    (5.0, 3.0),
)


@dataclass
class RiskDecision:
    ok: bool
    volume: float
    risk_usd: float
    sl: float
    tp: float
    entry: float = 0.0
    sl_distance: float = 0.0
    code: str = ACCEPTED
    detail: str = ""


@dataclass(frozen=True)
class OpenPosition:
    """Broker-owned position, mapped away from MT5 structs."""

    ticket: int
    symbol: str
    is_buy: bool
    price_open: float
    sl: float
    tp: float
    volume: float = 0.0
    magic: int = 0


def sl_range(spec: SymbolSpec, caps: dict[str, Any] | None) -> tuple[float, float] | None:
    """Configured SL distance bounds in price units, or None when unconfigured."""
    if not caps:
        return None
    for min_key, max_key, unit in _CAP_UNITS:
        if min_key in caps and max_key in caps:
            if unit == "pip":
                mult = spec.pip
            elif unit == "point":
                mult = spec.point
            else:
                mult = 1.0
            return float(caps[min_key]) * mult, float(caps[max_key]) * mult
    return None


def validate_sl_in_range(
    distance: float,
    spec: SymbolSpec,
    caps: dict[str, Any] | None,
) -> tuple[bool, str]:
    """I-10: out-of-range SL is skipped, never squeezed into the band."""
    bounds = sl_range(spec, caps)
    if bounds is None:
        return False, "no_sl_caps_configured"
    low, high = bounds
    if distance < low:
        return False, f"sl_{distance:.6f}_below_min_{low:.6f}"
    if distance > high:
        return False, f"sl_{distance:.6f}_above_max_{high:.6f}"
    return True, "in_range"


def calc_risk_usd(
    spec: SymbolSpec,
    volume: float,
    entry: float,
    sl: float,
) -> float | None:
    if spec.tick_size <= 0 or spec.tick_value <= 0:
        return None
    ticks = abs(entry - sl) / spec.tick_size
    return ticks * spec.tick_value * volume


def post_fill_risk_usd(
    spec: SymbolSpec,
    volume: float,
    fill_price: float,
    sl: float,
    commission: float = 0.0,
    swap: float = 0.0,
) -> float | None:
    """Realised exposure once the fill price is known, including costs."""
    base = calc_risk_usd(spec, volume, fill_price, sl)
    if base is None:
        return None
    return base + abs(commission) + abs(swap)


def is_spread_ok(
    spec: SymbolSpec,
    tick: TickSnapshot,
    max_spread_points: float | None,
    avg_spread_points: float | None = None,
    max_mult_of_avg: float = 2.0,
) -> tuple[bool, float]:
    """Spread gate in points. Fails closed when no cap is calibrated (G10).

    Sprint 1 #5: on top of the absolute cap, reject a spread that has ballooned
    past `max_mult_of_avg` times the calibrated average/typical spread — this
    catches a temporary widening (thin liquidity, news) that would otherwise
    still clear a generously-sized absolute cap. Skipped entirely when no
    average is calibrated, so behaviour cannot get worse than before.
    """
    points = spec.spread_points(tick)
    if max_spread_points is None:
        return False, points
    if points > float(max_spread_points):
        return False, points
    if avg_spread_points is not None and avg_spread_points > 0:
        if points > float(max_mult_of_avg) * float(avg_spread_points):
            return False, points
    return True, points


def validate_entry_drift(
    candidate_entry: float,
    executable_price: float,
    sl_distance: float,
    max_drift_r: float,
) -> tuple[bool, float]:
    """I-12: reject when the market moved too far from the signal bar."""
    drift = abs(executable_price - candidate_entry)
    if sl_distance <= 0:
        return False, drift
    return drift <= max_drift_r * sl_distance, drift


def build_trade_levels(
    spec: SymbolSpec,
    side: str,
    executable_price: float,
    structural_sl_distance: float,
    rr: float,
    volume: float,
    max_risk_usd: float,
    sl_caps: dict[str, Any] | None,
) -> RiskDecision:
    """Anchor SL/TP to the executable price using the structural SL distance."""
    if structural_sl_distance <= 0:
        return RiskDecision(
            False,
            volume,
            0.0,
            0.0,
            0.0,
            entry=executable_price,
            code=Reject.INVALID_SL_DISTANCE,
            detail="non_positive_sl_distance",
        )

    in_range, detail = validate_sl_in_range(structural_sl_distance, spec, sl_caps)
    if not in_range:
        return RiskDecision(
            False,
            volume,
            0.0,
            0.0,
            0.0,
            entry=executable_price,
            sl_distance=structural_sl_distance,
            code=Reject.SL_RANGE,
            detail=detail,
        )

    digits = spec.digits
    buy = is_buy_side(side)
    # Round the anchor first, then derive SL and TP from it, so the 1:3 ratio and
    # the risk figure both describe the prices actually sent to the broker.
    entry = round(executable_price, digits)
    sl = round(entry - structural_sl_distance if buy else entry + structural_sl_distance, digits)
    actual_sl_distance = abs(entry - sl)
    tp = round(entry + actual_sl_distance * rr if buy else entry - actual_sl_distance * rr, digits)

    risk = calc_risk_usd(spec, volume, entry, sl)
    if risk is None:
        return RiskDecision(
            False,
            volume,
            0.0,
            sl,
            tp,
            entry=entry,
            sl_distance=actual_sl_distance,
            code=Reject.RISK_CALC_FAILED,
            detail="tick_size_or_value_unusable",
        )
    if risk > max_risk_usd:
        return RiskDecision(
            False,
            volume,
            risk,
            sl,
            tp,
            entry=entry,
            sl_distance=actual_sl_distance,
            code=Reject.PRE_SEND_RISK_BREACH,
            detail=f"risk_{risk:.2f}_gt_max_{max_risk_usd:.2f}",
        )

    return RiskDecision(
        True,
        volume,
        risk,
        sl,
        tp,
        entry=entry,
        sl_distance=actual_sl_distance,
        code=ACCEPTED,
        detail="ok",
    )


@dataclass
class BreakerState:
    month_key: str | None = None
    month_start_equity: float | None = None
    monthly_halted: bool = False
    day_key: str | None = None
    daily_realized_usd: float = 0.0
    daily_halted: bool = False
    consecutive_losses: int = 0
    consecutive_halted: bool = False
    processed_deals: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "month_key": self.month_key,
            "month_start_equity": self.month_start_equity,
            "monthly_halted": self.monthly_halted,
            "day_key": self.day_key,
            "daily_realized_usd": self.daily_realized_usd,
            "daily_halted": self.daily_halted,
            "consecutive_losses": self.consecutive_losses,
            "consecutive_halted": self.consecutive_halted,
            "processed_deals": self.processed_deals[-PROCESSED_DEAL_HISTORY:],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BreakerState:
        return cls(
            month_key=data.get("month_key"),
            month_start_equity=(
                float(data["month_start_equity"])
                if data.get("month_start_equity") is not None
                else None
            ),
            monthly_halted=bool(data.get("monthly_halted", False)),
            day_key=data.get("day_key"),
            daily_realized_usd=float(data.get("daily_realized_usd", 0.0)),
            daily_halted=bool(data.get("daily_halted", False)),
            consecutive_losses=int(data.get("consecutive_losses", 0)),
            consecutive_halted=bool(data.get("consecutive_halted", False)),
            processed_deals=[int(d) for d in data.get("processed_deals", [])],
        )


class RiskBreakers:
    """Monthly / daily / consecutive-loss locks on server time, persisted (G01B, G04).

    Month and day keys come from the caller's server clock (I-09); state is
    written to disk so a restart cannot silently release an active lock.
    """

    def __init__(
        self,
        path: Path | str | None = None,
        monthly_max_dd_pct: float = 10.0,
        daily_max_loss_r: float = 3.0,
        max_consecutive_losses: int = 3,
        risk_unit_usd: float = 2.0,
    ) -> None:
        self.path = Path(path) if path else state_dir() / "breaker_state.json"
        self.monthly_max_dd_pct = abs(monthly_max_dd_pct)
        self.daily_max_loss_r = abs(daily_max_loss_r)
        self.max_consecutive_losses = int(max_consecutive_losses)
        self.risk_unit_usd = abs(risk_unit_usd)
        self.state = BreakerState()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            self.state = BreakerState.from_dict(
                json.loads(self.path.read_text(encoding="utf-8"))
            )
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
            logger.warning("breaker state unreadable (%s); starting fresh", exc)
            self.state = BreakerState()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state.to_dict(), indent=2), encoding="utf-8")

    @property
    def daily_loss_limit_usd(self) -> float:
        return self.daily_max_loss_r * self.risk_unit_usd

    @property
    def halted(self) -> bool:
        return bool(
            self.state.monthly_halted
            or self.state.daily_halted
            or self.state.consecutive_halted
        )

    def active_code(self) -> Reject | None:
        if self.state.monthly_halted:
            return Reject.MONTHLY_DD
        if self.state.daily_halted:
            return Reject.DAILY_LOSS
        if self.state.consecutive_halted:
            return Reject.CONSECUTIVE_LOSS
        return None

    def roll(self, now: datetime) -> bool:
        """Apply month/day rollover. Returns True when state changed."""
        changed = False
        day_key = now.strftime("%Y%m%d")
        if self.state.day_key != day_key:
            self.state.day_key = day_key
            self.state.daily_realized_usd = 0.0
            self.state.daily_halted = False
            self.state.consecutive_losses = 0
            self.state.consecutive_halted = False
            changed = True
        month_key = now.strftime("%Y-%m")
        if self.state.month_key != month_key:
            self.state.month_key = month_key
            self.state.month_start_equity = None
            self.state.monthly_halted = False
            changed = True
        return changed

    def update_equity(self, equity: float, now: datetime) -> Reject | None:
        """Refresh monthly drawdown from current equity; returns active lock."""
        changed = self.roll(now)
        if self.state.month_start_equity is None and equity > 0:
            self.state.month_start_equity = float(equity)
            changed = True
        start = self.state.month_start_equity
        if start and start > 0 and not self.state.monthly_halted:
            dd_pct = (equity - start) / start * 100.0
            if dd_pct <= -self.monthly_max_dd_pct:
                self.state.monthly_halted = True
                changed = True
                logger.warning(
                    "Monthly breaker LOCK: equity=%.2f start=%.2f dd=%.2f%%",
                    equity,
                    start,
                    dd_pct,
                )
        if changed:
            self.save()
        return self.active_code()

    def register_closed_deal(self, deal_id: int, profit: float, now: datetime) -> bool:
        """Count one closed deal once. Returns False when already seen."""
        deal_id = int(deal_id)
        if deal_id in self.state.processed_deals:
            return False
        self.roll(now)
        self.state.processed_deals.append(deal_id)
        self.state.processed_deals = self.state.processed_deals[-PROCESSED_DEAL_HISTORY:]
        self.state.daily_realized_usd += float(profit)

        if profit < 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0

        if self.state.daily_realized_usd <= -self.daily_loss_limit_usd:
            if not self.state.daily_halted:
                logger.warning(
                    "Daily loss breaker LOCK: realized=%.2f limit=-%.2f",
                    self.state.daily_realized_usd,
                    self.daily_loss_limit_usd,
                )
            self.state.daily_halted = True
        if self.state.consecutive_losses >= self.max_consecutive_losses:
            if not self.state.consecutive_halted:
                logger.warning(
                    "Consecutive loss breaker LOCK: streak=%d",
                    self.state.consecutive_losses,
                )
            self.state.consecutive_halted = True

        self.save()
        return True

    def has_processed(self, deal_id: int) -> bool:
        return int(deal_id) in self.state.processed_deals

    def snapshot(self) -> dict[str, Any]:
        return self.state.to_dict()


def staircase_floor(
    favor_r: float,
    r_basis: float,
    entry: float,
    buy: bool,
    levels: tuple[tuple[float, float], ...] = DEFAULT_STAIRCASE_LEVELS,
) -> float | None:
    """Sprint 2 #1: once favour crosses a level's `trigger_r`, floor the SL at
    `entry +/- lock_r * r_basis` (r_basis == initial_sl_distance).

    Returns None when no level has triggered yet. Never proposes a *looser*
    floor than a level already crossed — the caller combines this with
    whatever BE/trail already proposed via max() (buy) / min() (sell), so a
    pullback bar can never walk the SL back down. Mirrors
    `src.backtest.replay._staircase_floor` exactly, so live and replay agree.
    """
    floor: float | None = None
    for trigger_r, lock_r in levels:
        if favor_r >= trigger_r:
            candidate = entry + lock_r * r_basis if buy else entry - lock_r * r_basis
            if floor is None:
                floor = candidate
            else:
                floor = max(floor, candidate) if buy else min(floor, candidate)
    return floor


def manage_open_position(
    position: OpenPosition,
    spec: SymbolSpec,
    tick: TickSnapshot,
    atr: float,
    break_even_r: float = 1.0,
    trailing_start_r: float = 1.5,
    trailing_atr_mult: float = 1.0,
    structure_trail_sl: float | None = None,
    prefer_structure_trail: bool = True,
    initial_sl_distance: float | None = None,
    be_lock_pips: float = 0.0,
    be_lock_atr_mult: float = 0.0,
    enable_staircase: bool = False,
    staircase_levels: tuple[tuple[float, float], ...] = DEFAULT_STAIRCASE_LEVELS,
    structure_max_giveback_atr: float = 1.0,
) -> dict[str, Any] | None:
    """Return the SL/TP change to apply, or None when nothing should move.

    When prefer_structure_trail is True and a swing-based SL is supplied, it
    normally wins over the ATR trail so winners can reach opposing-structure
    TP (Phase 2.B). Sprint 2 #4 adds a hybrid guard: if the structure
    candidate is looser than the ATR candidate by more than
    `structure_max_giveback_atr * atr`, the tighter ATR candidate is used
    instead — a stale/far swing level should not give back more than that
    much edge versus just trailing by ATR.

    Sprint 1 P0: |price_open - position.sl| collapses to 0 once break-even has
    moved the SL to the entry, which used to kill R math (favor / sl_dist) for
    every poll after that. `initial_sl_distance` — the SL distance recorded
    when the trade opened — is the basis the caller should keep supplying so
    trailing keeps working after BE. When it isn't supplied, behaviour falls
    back to the historical (buggy) |price_open - sl| basis on purpose, so the
    regression stays documented and testable.

    Sprint 1 #2: break-even no longer parks the SL exactly on price_open.
    `be_lock_pips` / `be_lock_atr_mult` push it `max(be_lock_pips*pip,
    atr*be_lock_atr_mult, 1*point)` into profit so a single-tick reversal
    cannot scratch the trade at true break-even (commission/spread would make
    that a small loss).

    Sprint 2 #1: `enable_staircase` floors the SL at entry +/- 1R/2R/3R once
    favour crosses 3R/4R/5R (see `staircase_floor`), on top of whatever
    BE/trail already proposed — never loosening it.
    """
    if position.sl == 0:
        return None
    sl_dist = abs(position.price_open - position.sl)
    if initial_sl_distance is not None and initial_sl_distance > 0:
        r_basis = float(initial_sl_distance)
    else:
        r_basis = sl_dist
    if r_basis <= 0:
        return None

    price = tick.exit_price("BUY" if position.is_buy else "SELL")
    favor = (price - position.price_open) if position.is_buy else (position.price_open - price)
    favor_r = favor / r_basis
    new_sl = position.sl

    if favor >= break_even_r * r_basis:
        be_offset = max(
            be_lock_pips * spec.pip,
            atr * be_lock_atr_mult,
            spec.point,
        )
        be = position.price_open + be_offset if position.is_buy else position.price_open - be_offset
        if position.is_buy and position.sl < be:
            new_sl = be
        if not position.is_buy and position.sl > be:
            new_sl = be

    # Sprint 2 #4: compute both trail candidates so structure can be checked
    # against ATR for the hybrid giveback guard, instead of overriding ATR
    # unconditionally whenever a structure candidate exists.
    atr_trail_candidate: float | None = None
    if favor >= trailing_start_r * r_basis and atr > 0:
        trail = atr * trailing_atr_mult
        atr_trail_candidate = (price - trail) if position.is_buy else (price + trail)

    structure_candidate: float | None = None
    if structure_trail_sl is not None:
        cand = float(structure_trail_sl)
        if cand == cand:  # not NaN
            structure_candidate = cand

    chosen_trail: float | None = None
    if prefer_structure_trail and structure_candidate is not None:
        chosen_trail = structure_candidate
        if atr_trail_candidate is not None and atr > 0:
            # Positive giveback means the ATR trail is tighter than structure
            # (structure allows more room back). Beyond the configured
            # multiple of ATR, prefer the tighter ATR candidate instead.
            giveback = (
                (atr_trail_candidate - structure_candidate)
                if position.is_buy
                else (structure_candidate - atr_trail_candidate)
            )
            if giveback > structure_max_giveback_atr * atr:
                chosen_trail = atr_trail_candidate
    elif atr_trail_candidate is not None:
        chosen_trail = atr_trail_candidate

    if chosen_trail is not None:
        if position.is_buy and chosen_trail > new_sl:
            new_sl = chosen_trail
        if not position.is_buy and (new_sl == 0 or chosen_trail < new_sl):
            new_sl = chosen_trail

    if enable_staircase:
        floor = staircase_floor(
            favor_r, r_basis, position.price_open, position.is_buy, staircase_levels
        )
        if floor is not None:
            new_sl = max(new_sl, floor) if position.is_buy else min(new_sl, floor)

    new_sl = round(new_sl, spec.digits)
    if new_sl == round(position.sl, spec.digits):
        return None

    return {
        "ticket": position.ticket,
        "symbol": position.symbol,
        "sl": new_sl,
        "tp": position.tp,
    }
