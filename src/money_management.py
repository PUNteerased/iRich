"""Dynamic money management: FIXED_TIER and DYNAMIC_PCT (no MT5 import).

Lot and max_risk_usd come only from declared config + account_capital (Balance).
Never from model probability or win/loss streak (I-03 revised).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from .market_data import SymbolSpec
from .reject_codes import Reject
from .risk import calc_risk_usd


@dataclass(frozen=True)
class MmDecision:
    ok: bool
    volume: float = 0.0
    max_risk_usd: float = 0.0
    mode: str = ""
    tier_id: int | None = None
    account_capital: float = 0.0
    code: str = ""
    detail: str = ""


@dataclass(frozen=True)
class ScalingGate:
    """Sprint 2 #6 scaffolding: rolling portfolio-performance gate.

    Distinct from I-03's "never size lot from model_prob" — this reads a
    rolling *realised* win-rate/avg-R (the same rolling window
    `learning.drift_guard.DriftGuard` already tracks from closed deals in the
    ledger), not a single trade's model confidence. It only ever matters when
    both `money_management.dynamic_scaling_enabled` is true and
    `option_a_lock` is false; both stay off by default.
    """

    passed: bool
    win_rate: float = 0.0
    avg_r: float = 0.0
    n_trades: int = 0
    detail: str = ""


def dynamic_scaling_gate_passed(
    win_rate: float,
    avg_r: float,
    n_trades: int,
    *,
    min_trades: int = 20,
    min_win_rate: float = 0.55,
    min_avg_r: float = 2.0,
) -> ScalingGate:
    """Sprint 2 #6: rolling WR >= min_win_rate and avg_r >= min_avg_r from
    recent closed trades (caller supplies the rolling stats — e.g.
    `Runtime.drift.state.win_rate` / `.expectancy_r` / `.n`, which are already
    computed from the ledger's closed deals). Not enough trades yet fails
    closed, same spirit as every other gate in this codebase (I-10 style:
    skip, never guess)."""
    if n_trades < min_trades:
        return ScalingGate(
            False, win_rate, avg_r, n_trades, detail=f"insufficient_trades_{n_trades}_lt_{min_trades}"
        )
    passed = win_rate >= min_win_rate and avg_r >= min_avg_r
    detail = "ok" if passed else f"gate_fail_wr={win_rate:.3f}_avg_r={avg_r:.3f}"
    return ScalingGate(passed, win_rate, avg_r, n_trades, detail=detail)


@dataclass(frozen=True)
class MarginCheck:
    ok: bool
    required_margin: float
    free_margin: float
    code: str = ""
    detail: str = ""


def _floor_lot(raw: float, step: float, vmin: float, vmax: float) -> float | None:
    if step <= 0:
        step = 0.01
    if vmax <= 0:
        vmax = raw
    floored = math.floor(raw / step + 1e-12) * step
    # avoid float dust
    floored = round(floored, 8)
    if floored < vmin - 1e-12:
        return None
    return min(floored, vmax)


def resolve_mm(
    account_capital: float,
    structural_sl_distance: float,
    spec: SymbolSpec,
    mm_cfg: Mapping[str, Any] | None,
    *,
    fallback_lot: float = 0.01,
    fallback_max_risk_usd: float = 2.0,
    side: str = "BUY",
    entry_price: float | None = None,
) -> MmDecision:
    """Resolve (volume, max_risk_usd) from FIXED_TIER or DYNAMIC_PCT."""
    capital = float(account_capital)
    cfg = dict(mm_cfg or {})
    mode = str(cfg.get("mode", "FIXED_TIER")).upper()

    # Sprint 1 #7: Demo-validation lock. While true, every trade is sized at
    # the smallest possible lot and the tightest risk ceiling, regardless of
    # tier/equity — the point of Option A is to prove the loop, not to size up.
    if bool(cfg.get("option_a_lock", False)):
        step = getattr(spec, "volume_step", 0.01) or 0.01
        vmin = getattr(spec, "volume_min", 0.01) or 0.01
        vmax = getattr(spec, "volume_max", 100.0) or 100.0
        floored = _floor_lot(0.01, step, vmin, vmax)
        if floored is None:
            return MmDecision(
                False,
                mode="OPTION_A_LOCK",
                account_capital=capital,
                code=Reject.PRE_SEND_RISK_BREACH,
                detail="option_a_lock_lot_below_volume_min",
            )
        return MmDecision(
            True,
            volume=floored,
            max_risk_usd=2.00,
            mode="OPTION_A_LOCK",
            account_capital=capital,
            code="OK",
        )

    if mode == "FIXED_TIER":
        tiers = list(cfg.get("fixed_tier") or [])
        if not tiers:
            return MmDecision(
                True,
                volume=fallback_lot,
                max_risk_usd=fallback_max_risk_usd,
                mode=mode,
                account_capital=capital,
                code="OK",
            )
        chosen = None
        tier_id = None
        for i, tier in enumerate(tiers):
            lo = float(tier["min_balance"])
            hi = float(tier["max_balance"])
            if lo <= capital <= hi:
                chosen = tier
                tier_id = i
                break
        if chosen is None:
            chosen = tiers[-1]
            tier_id = len(tiers) - 1
        lot = float(chosen["lot"])
        max_risk = float(chosen["max_risk_usd"])
        step = getattr(spec, "volume_step", 0.01) or 0.01
        vmin = getattr(spec, "volume_min", 0.01) or 0.01
        vmax = getattr(spec, "volume_max", 100.0) or 100.0
        floored = _floor_lot(lot, step, vmin, vmax)
        if floored is None:
            return MmDecision(
                False,
                mode=mode,
                tier_id=tier_id,
                account_capital=capital,
                code=Reject.PRE_SEND_RISK_BREACH,
                detail="lot_below_volume_min",
            )
        return MmDecision(
            True,
            volume=floored,
            max_risk_usd=max_risk,
            mode=mode,
            tier_id=tier_id,
            account_capital=capital,
            code="OK",
        )

    if mode == "DYNAMIC_PCT":
        dyn = dict(cfg.get("dynamic") or {})
        pct = float(dyn.get("risk_per_trade_pct", 2.0))
        cap = float(dyn.get("max_risk_usd_cap", 50.0))
        max_lot = float(dyn.get("max_lot_size", 1.0))
        min_lot = float(dyn.get("min_lot_size", 0.01))
        risk_usd = min(capital * (pct / 100.0), cap)
        if structural_sl_distance <= 0 or entry_price is None or entry_price <= 0:
            return MmDecision(
                False,
                mode=mode,
                account_capital=capital,
                code=Reject.INVALID_SL_DISTANCE,
                detail="dynamic_needs_sl_and_entry",
            )
        # Reverse lot from risk: risk ≈ calc_risk_usd(spec, lot, entry, sl)
        # Binary search / scale from unit lot risk.
        unit_sl = (
            entry_price - structural_sl_distance
            if str(side).upper() in ("BUY", "BULLISH", "LONG")
            else entry_price + structural_sl_distance
        )
        unit_risk = calc_risk_usd(spec, 1.0, entry_price, unit_sl)
        if unit_risk is None or unit_risk <= 0:
            return MmDecision(
                False,
                mode=mode,
                account_capital=capital,
                code=Reject.RISK_CALC_FAILED,
                detail="unit_risk_unavailable",
            )
        raw_lot = risk_usd / unit_risk
        step = getattr(spec, "volume_step", 0.01) or 0.01
        vmin = max(min_lot, getattr(spec, "volume_min", 0.01) or 0.01)
        vmax = min(max_lot, getattr(spec, "volume_max", 100.0) or 100.0)
        floored = _floor_lot(raw_lot, step, vmin, vmax)
        if floored is None:
            return MmDecision(
                False,
                mode=mode,
                account_capital=capital,
                max_risk_usd=risk_usd,
                code=Reject.PRE_SEND_RISK_BREACH,
                detail="dynamic_lot_below_min",
            )
        return MmDecision(
            True,
            volume=floored,
            max_risk_usd=risk_usd,
            mode=mode,
            account_capital=capital,
            code="OK",
        )

    return MmDecision(
        False,
        mode=mode,
        account_capital=capital,
        code=Reject.RISK_CALC_FAILED,
        detail=f"unknown_mm_mode:{mode}",
    )


def estimate_required_margin(
    spec: SymbolSpec,
    volume: float,
    price: float,
    *,
    leverage: float = 3000.0,
    contract_size: float | None = None,
) -> float:
    """Replay / offline margin estimate (not a substitute for order_calc_margin live)."""
    size = contract_size if contract_size is not None else getattr(spec, "contract_size", 100000.0)
    if size is None or size <= 0:
        size = 100000.0
    lev = leverage if leverage and leverage > 0 else 100.0
    notional = abs(price) * float(volume) * float(size)
    # FX-style: margin = notional / leverage. For metals/crypto, callers should
    # pass a realistic leverage (e.g. BTC ~50) via SymbolSpec.margin_leverage.
    lev_eff = float(getattr(spec, "margin_leverage", None) or lev)
    if lev_eff <= 0:
        lev_eff = lev
    return notional / lev_eff


def check_free_margin(
    required_margin: float,
    free_margin: float,
) -> MarginCheck:
    """Gotcha #1: estimated loss ≠ broker margin requirement."""
    if required_margin > free_margin + 1e-9:
        return MarginCheck(
            False,
            required_margin=required_margin,
            free_margin=free_margin,
            code=Reject.NO_MONEY,
            detail=f"margin_{required_margin:.4f}_gt_free_{free_margin:.4f}",
        )
    return MarginCheck(
        True,
        required_margin=required_margin,
        free_margin=free_margin,
        code="OK",
    )


class VirtualAccount:
    """Gotcha #3: compounding tracker for replay / MM property tests."""

    def __init__(
        self,
        starting_balance: float = 100.0,
        *,
        leverage: float = 3000.0,
    ) -> None:
        self.starting_balance = float(starting_balance)
        self.balance = float(starting_balance)
        self.peak = float(starting_balance)
        self.max_drawdown_pct = 0.0
        self.leverage = float(leverage)
        self.closed_trades = 0

    @property
    def account_capital(self) -> float:
        return self.balance

    @property
    def free_margin(self) -> float:
        # I-04: at most one position; between trades free margin == balance.
        return self.balance

    def apply_closed_trade(self, pnl_usd: float) -> float:
        self.balance = round(self.balance + float(pnl_usd), 4)
        self.closed_trades += 1
        self.peak = max(self.peak, self.balance)
        if self.peak > 0:
            dd = (self.peak - self.balance) / self.peak * 100.0
            self.max_drawdown_pct = max(self.max_drawdown_pct, dd)
        return self.balance
