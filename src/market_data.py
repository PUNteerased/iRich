"""Market data abstraction (G19).

Risk and execution code must read prices and symbol specs through this
interface. Calling MetaTrader5 directly breaks replay, and worse, silently
prices historical trades with today's tick value.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

BUY_SIDES = frozenset({"BUY", "BULLISH", "LONG"})


def is_buy_side(side: str) -> bool:
    return str(side).upper() in BUY_SIDES


@dataclass(frozen=True)
class TickSnapshot:
    symbol: str
    bid: float
    ask: float
    time: datetime

    @property
    def spread_price(self) -> float:
        return abs(self.ask - self.bid)

    def executable_price(self, side: str) -> float:
        """Price the order would actually fill at for this side."""
        return self.ask if is_buy_side(side) else self.bid

    def exit_price(self, side: str) -> float:
        """Price an open position of this side is marked against."""
        return self.bid if is_buy_side(side) else self.ask


@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    point: float
    digits: int
    tick_size: float
    tick_value: float
    filling_mode: int = 0
    volume_min: float = 0.01
    volume_max: float = 100.0
    volume_step: float = 0.01
    contract_size: float = 100000.0
    margin_leverage: float = 3000.0

    @property
    def pip(self) -> float:
        """Pip size derived from digits; 5/3-digit quotes use 10 points."""
        if self.digits in (3, 5):
            return self.point * 10
        return self.point

    def spread_points(self, tick: TickSnapshot) -> float:
        if self.point <= 0:
            return float("inf")
        return tick.spread_price / self.point


@dataclass(frozen=True)
class AccountSnapshot:
    """Live portfolio fields used by MM / margin (always from the connected account)."""

    login: int
    balance: float
    equity: float
    free_margin: float
    margin: float
    leverage: float
    currency: str
    server: str
    trade_mode: int
    is_demo: bool

    @property
    def account_capital(self) -> float:
        """Balance drives FIXED_TIER / DYNAMIC_PCT sizing."""
        return self.balance


@runtime_checkable
class MarketDataProvider(Protocol):
    def get_tick(self, symbol: str) -> TickSnapshot | None: ...

    def get_symbol_info(self, symbol: str) -> SymbolSpec | None: ...

    def account_capital(self) -> float:
        """Account Balance — used as MM capital (I-04 ⇒ equity==balance when flat)."""
        ...

    def account_leverage(self) -> float:
        """Account leverage ratio (e.g. 3000 for 1:3000)."""
        ...

    def account_snapshot(self) -> AccountSnapshot | None:
        """Full portfolio snapshot, or None when unavailable."""
        ...

    def free_margin(self) -> float: ...

    def order_calc_margin(self, symbol: str, side: str, volume: float, price: float) -> float | None:
        ...


class MT5LiveProvider:
    """Live provider backed by the MetaTrader5 terminal."""

    def __init__(self) -> None:
        self._mt5 = _import_mt5()
        self._selected: set[str] = set()

    def _ensure_selected(self, symbol: str) -> None:
        if symbol in self._selected:
            return
        if self._mt5.symbol_select(symbol, True):
            self._selected.add(symbol)
        else:
            logger.warning("symbol_select failed for %s", symbol)

    def get_tick(self, symbol: str) -> TickSnapshot | None:
        self._ensure_selected(symbol)
        tick = self._mt5.symbol_info_tick(symbol)
        if tick is None:
            logger.warning("symbol_info_tick returned None for %s", symbol)
            return None
        bid, ask = float(tick.bid), float(tick.ask)
        if bid <= 0 or ask <= 0:
            logger.warning("[%s] non-positive tick bid=%s ask=%s", symbol, bid, ask)
            return None
        return TickSnapshot(
            symbol=symbol,
            bid=bid,
            ask=ask,
            time=datetime.fromtimestamp(int(tick.time), tz=timezone.utc),
        )

    def get_symbol_info(self, symbol: str) -> SymbolSpec | None:
        self._ensure_selected(symbol)
        info = self._mt5.symbol_info(symbol)
        if info is None:
            logger.warning("symbol_info returned None for %s", symbol)
            return None
        point = float(info.point)
        tick_size = float(info.trade_tick_size) or point
        snap = self.account_snapshot()
        leverage = float(snap.leverage) if snap and snap.leverage > 0 else _default_leverage(symbol)
        return SymbolSpec(
            symbol=symbol,
            point=point,
            digits=int(info.digits),
            tick_size=tick_size,
            tick_value=float(info.trade_tick_value),
            filling_mode=int(getattr(info, "filling_mode", 0)),
            volume_min=float(getattr(info, "volume_min", 0.01) or 0.01),
            volume_max=float(getattr(info, "volume_max", 100.0) or 100.0),
            volume_step=float(getattr(info, "volume_step", 0.01) or 0.01),
            contract_size=float(getattr(info, "trade_contract_size", 0) or _default_contract(symbol)),
            margin_leverage=leverage,
        )

    def account_snapshot(self) -> AccountSnapshot | None:
        acc = self._mt5.account_info()
        if acc is None:
            return None
        real_mode = int(getattr(self._mt5, "ACCOUNT_TRADE_MODE_REAL", 2))
        trade_mode = int(acc.trade_mode)
        return AccountSnapshot(
            login=int(acc.login),
            balance=float(acc.balance),
            equity=float(acc.equity),
            free_margin=float(acc.margin_free),
            margin=float(acc.margin),
            leverage=float(acc.leverage) if acc.leverage else 0.0,
            currency=str(getattr(acc, "currency", "") or ""),
            server=str(getattr(acc, "server", "") or ""),
            trade_mode=trade_mode,
            is_demo=trade_mode != real_mode,
        )

    def account_capital(self) -> float:
        snap = self.account_snapshot()
        return float(snap.balance) if snap else 0.0

    def account_leverage(self) -> float:
        snap = self.account_snapshot()
        return float(snap.leverage) if snap else 0.0

    def free_margin(self) -> float:
        snap = self.account_snapshot()
        return float(snap.free_margin) if snap else 0.0

    def order_calc_margin(self, symbol: str, side: str, volume: float, price: float) -> float | None:
        self._ensure_selected(symbol)
        order_type = self._mt5.ORDER_TYPE_BUY if is_buy_side(side) else self._mt5.ORDER_TYPE_SELL
        margin = self._mt5.order_calc_margin(order_type, symbol, float(volume), float(price))
        if margin is None:
            logger.warning("order_calc_margin failed for %s: %s", symbol, self._mt5.last_error())
            return None
        return float(margin)

    def server_time(self, symbols: list[str]) -> datetime | None:
        """Server time from the freshest tick across candidate symbols."""
        latest: datetime | None = None
        for symbol in symbols:
            tick = self.get_tick(symbol)
            if tick is None:
                continue
            if latest is None or tick.time > latest:
                latest = tick.time
        return latest


class ReplayProvider:
    """Provider driven by historical bars (G19) + optional VirtualAccount (Gotcha #3)."""

    def __init__(
        self,
        specs: dict[str, SymbolSpec],
        spread_points: dict[str, float] | None = None,
        default_spread_points: float = 1.0,
        virtual_account: Any = None,
        default_leverage: float = 3000.0,
    ) -> None:
        if not specs:
            raise ValueError(
                "ReplayProvider needs symbol specs; export them with "
                "scripts/export_mt5_history.py --specs-only"
            )
        self._specs = dict(specs)
        self._spread_points = dict(spread_points or {})
        self._default_spread_points = float(default_spread_points)
        self._bars: dict[str, tuple[float, datetime]] = {}
        self.virtual_account = virtual_account
        self._default_leverage = float(default_leverage)

    def account_leverage(self) -> float:
        if self.virtual_account is not None:
            return float(getattr(self.virtual_account, "leverage", self._default_leverage))
        return self._default_leverage

    def account_snapshot(self) -> AccountSnapshot | None:
        balance = self.account_capital()
        free = self.free_margin()
        lev = self.account_leverage()
        return AccountSnapshot(
            login=0,
            balance=balance,
            equity=balance,
            free_margin=free,
            margin=max(0.0, balance - free),
            leverage=lev,
            currency="USD",
            server="replay",
            trade_mode=0,
            is_demo=True,
        )

    def advance(self, symbol: str, close: float, bar_time: datetime) -> None:
        """Move simulated time to a completed bar."""
        self._bars[symbol] = (float(close), bar_time)

    def get_tick(self, symbol: str) -> TickSnapshot | None:
        bar = self._bars.get(symbol)
        spec = self._specs.get(symbol)
        if bar is None or spec is None:
            return None
        close, bar_time = bar
        half = self._spread_points.get(symbol, self._default_spread_points) * spec.point / 2.0
        return TickSnapshot(symbol=symbol, bid=close - half, ask=close + half, time=bar_time)

    def get_symbol_info(self, symbol: str) -> SymbolSpec | None:
        return self._specs.get(symbol)

    def account_capital(self) -> float:
        if self.virtual_account is not None:
            return float(self.virtual_account.account_capital)
        return 100.0

    def free_margin(self) -> float:
        if self.virtual_account is not None:
            return float(self.virtual_account.free_margin)
        return self.account_capital()

    def order_calc_margin(self, symbol: str, side: str, volume: float, price: float) -> float | None:
        from .money_management import estimate_required_margin

        spec = self.get_symbol_info(symbol)
        if spec is None:
            return None
        return estimate_required_margin(
            spec,
            volume,
            price,
            leverage=float(getattr(spec, "margin_leverage", self._default_leverage)),
        )


def load_symbol_specs(path: Any) -> dict[str, SymbolSpec]:
    """Read a spec snapshot written by scripts/export_mt5_history.py."""
    import json
    from pathlib import Path as _Path

    p = _Path(path)
    if not p.exists():
        return {}
    payload = json.loads(p.read_text(encoding="utf-8"))
    out: dict[str, SymbolSpec] = {}
    for symbol, fields in (payload.get("symbols") or {}).items():
        out[symbol] = SymbolSpec(
            symbol=fields.get("symbol", symbol),
            point=float(fields["point"]),
            digits=int(fields["digits"]),
            tick_size=float(fields["tick_size"]),
            tick_value=float(fields["tick_value"]),
            filling_mode=int(fields.get("filling_mode", 0)),
            volume_min=float(fields.get("volume_min", 0.01)),
            volume_max=float(fields.get("volume_max", 100.0)),
            volume_step=float(fields.get("volume_step", 0.01)),
            contract_size=float(fields.get("contract_size", _default_contract(symbol))),
            margin_leverage=float(fields.get("margin_leverage", _default_leverage(symbol))),
        )
    return out


class MockProvider:
    """In-memory provider for unit tests; no terminal required."""

    def __init__(
        self,
        ticks: dict[str, TickSnapshot] | None = None,
        specs: dict[str, SymbolSpec] | None = None,
        balance: float = 100.0,
        free_margin_value: float | None = None,
        leverage: float = 3000.0,
        *,
        equity: float | None = None,
        is_demo: bool = True,
        login: int = 0,
        server: str = "mock",
        currency: str = "USD",
    ) -> None:
        self.ticks: dict[str, TickSnapshot] = dict(ticks or {})
        self.specs: dict[str, SymbolSpec] = dict(specs or {})
        self._balance = float(balance)
        self._equity = float(equity) if equity is not None else float(balance)
        self._free_margin = (
            float(free_margin_value) if free_margin_value is not None else float(balance)
        )
        self._leverage = float(leverage)
        self._is_demo = bool(is_demo)
        self._login = int(login)
        self._server = str(server)
        self._currency = str(currency)

    def set_tick(
        self,
        symbol: str,
        bid: float,
        ask: float,
        time: datetime | None = None,
    ) -> TickSnapshot:
        tick = TickSnapshot(
            symbol=symbol,
            bid=bid,
            ask=ask,
            time=time or datetime.now(timezone.utc),
        )
        self.ticks[symbol] = tick
        return tick

    def set_spec(self, spec: SymbolSpec) -> SymbolSpec:
        self.specs[spec.symbol] = spec
        return spec

    def get_tick(self, symbol: str) -> TickSnapshot | None:
        return self.ticks.get(symbol)

    def get_symbol_info(self, symbol: str) -> SymbolSpec | None:
        return self.specs.get(symbol)

    def account_capital(self) -> float:
        return self._balance

    def account_leverage(self) -> float:
        return self._leverage

    def account_snapshot(self) -> AccountSnapshot | None:
        return AccountSnapshot(
            login=self._login,
            balance=self._balance,
            equity=self._equity,
            free_margin=self._free_margin,
            margin=max(0.0, self._equity - self._free_margin),
            leverage=self._leverage,
            currency=self._currency,
            server=self._server,
            trade_mode=0 if self._is_demo else 2,
            is_demo=self._is_demo,
        )

    def free_margin(self) -> float:
        return self._free_margin

    def order_calc_margin(self, symbol: str, side: str, volume: float, price: float) -> float | None:
        from .money_management import estimate_required_margin

        spec = self.get_symbol_info(symbol)
        if spec is None:
            return None
        return estimate_required_margin(spec, volume, price)


def _default_contract(symbol: str) -> float:
    s = symbol.upper()
    if s.startswith("XAU") or s.startswith("GOLD"):
        return 100.0
    if s.startswith("BTC"):
        return 1.0
    return 100000.0


def _default_leverage(symbol: str) -> float:
    s = symbol.upper()
    if s.startswith("BTC"):
        return 50.0
    if s.startswith("XAU") or s.startswith("GOLD"):
        return 3000.0
    return 3000.0


def _import_mt5() -> Any:
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:  # pragma: no cover - platform dependent
        raise RuntimeError(
            "MetaTrader5 is unavailable; use ReplayProvider or MockProvider instead."
        ) from exc
    return mt5
