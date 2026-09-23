"""MetaTrader5 connection helpers.

This module is the only place allowed to import MetaTrader5 for trading calls;
prices and symbol specs are exposed through MarketDataProvider (G19).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import MetaTrader5 as mt5
import pandas as pd

from .config import Config
from .market_data import MT5LiveProvider
from .risk import OpenPosition
from .trade_ledger import ClosedDeal

logger = logging.getLogger(__name__)

TF_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

# Deal entry flag meaning "this deal closed exposure" — the outcome we learn from.
DEAL_ENTRY_OUT = getattr(mt5, "DEAL_ENTRY_OUT", 1)


class MT5Connector:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.connected = False
        self.provider = MT5LiveProvider()
        self._on_connect: list[Any] = []

    def add_connect_hook(self, hook: Any) -> None:
        """Register a callable to run after every successful (re)connect."""
        self._on_connect.append(hook)

    def connect(self) -> bool:
        # Re-apply active dashboard account on every connect so switches take effect.
        try:
            from .accounts import apply_active_account

            mt5_section = self.config.raw.setdefault("mt5", {})
            apply_active_account(mt5_section)
        except Exception:  # noqa: BLE001
            pass

        mt5_cfg = self.config.get("mt5") or {}
        kwargs: dict[str, Any] = {}
        if mt5_cfg.get("path"):
            kwargs["path"] = mt5_cfg["path"]
        if mt5_cfg.get("login"):
            kwargs["login"] = int(mt5_cfg["login"])
        if mt5_cfg.get("password"):
            kwargs["password"] = mt5_cfg["password"]
        if mt5_cfg.get("server"):
            kwargs["server"] = mt5_cfg["server"]

        try:
            mt5.shutdown()
        except Exception:  # noqa: BLE001
            pass

        ok = mt5.initialize(**kwargs) if kwargs else mt5.initialize()
        if not ok:
            logger.error("MT5 initialize failed: %s", mt5.last_error())
            self.connected = False
            return False

        acc = mt5.account_info()
        if acc is None:
            logger.error("No account_info: %s", mt5.last_error())
            self.connected = False
            return False

        self.connected = True
        snap = self.provider.account_snapshot()
        if snap is not None:
            logger.info(
                "MT5 connected: login=%s server=%s %s | balance=%.2f equity=%.2f "
                "free_margin=%.2f leverage=1:%s currency=%s",
                snap.login,
                snap.server,
                "DEMO" if snap.is_demo else "REAL",
                snap.balance,
                snap.equity,
                snap.free_margin,
                int(snap.leverage) if snap.leverage else "?",
                snap.currency,
            )
        else:
            logger.info(
                "MT5 connected: login=%s server=%s balance=%.2f leverage=1:%s",
                acc.login,
                acc.server,
                acc.balance,
                acc.leverage,
            )
        for hook in self._on_connect:
            try:
                hook()
            except Exception as exc:  # noqa: BLE001
                logger.warning("connect hook failed: %s", exc)
        return True

    def ensure(self) -> bool:
        if self.connected and mt5.terminal_info() is not None:
            return True
        logger.warning("MT5 reconnecting...")
        try:
            mt5.shutdown()
        except Exception:  # noqa: BLE001
            pass
        return self.connect()

    def shutdown(self) -> None:
        mt5.shutdown()
        self.connected = False

    def server_time(self) -> datetime | None:
        """Freshest tick time across configured symbols (source for ServerClock)."""
        return self.provider.server_time(self.config.symbols)

    def copy_rates(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        if not self.ensure():
            return pd.DataFrame()
        if not mt5.symbol_select(symbol, True):
            logger.warning("symbol_select failed for %s", symbol)
        tf = TF_MAP[timeframe]
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None:
            logger.warning("copy_rates failed %s %s: %s", symbol, timeframe, mt5.last_error())
            return pd.DataFrame()
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df

    def positions(self, magic_only: bool = False) -> tuple[OpenPosition, ...]:
        if not self.ensure():
            return ()
        positions = mt5.positions_get()
        if positions is None:
            return ()
        magic = self.config.magic
        out = []
        for p in positions:
            if magic_only and p.magic != magic:
                continue
            out.append(
                OpenPosition(
                    ticket=int(p.ticket),
                    symbol=str(p.symbol),
                    is_buy=p.type == mt5.POSITION_TYPE_BUY,
                    price_open=float(p.price_open),
                    sl=float(p.sl) if p.sl else 0.0,
                    tp=float(p.tp) if p.tp else 0.0,
                    volume=float(p.volume),
                    magic=int(p.magic),
                )
            )
        return tuple(out)

    def has_open_positions(self) -> bool:
        count_all = bool(self.config.get("position_guard", "count_all_positions", default=True))
        positions = self.positions(magic_only=not count_all)
        max_open = int(self.config.get("position_guard", "max_total_open", default=1))
        return len(positions) >= max_open

    def account_equity(self) -> float:
        if not self.ensure():
            return 0.0
        snap = self.provider.account_snapshot()
        return float(snap.equity) if snap else 0.0

    def account_balance(self) -> float:
        if not self.ensure():
            return 0.0
        snap = self.provider.account_snapshot()
        return float(snap.balance) if snap else 0.0

    def account_leverage(self) -> float:
        if not self.ensure():
            return 0.0
        snap = self.provider.account_snapshot()
        return float(snap.leverage) if snap else 0.0

    def account_snapshot(self):
        if not self.ensure():
            return None
        return self.provider.account_snapshot()

    def closed_deals(
        self,
        since: datetime,
        until: datetime | None = None,
        magic_only: bool = True,
    ) -> tuple[ClosedDeal, ...]:
        """Deals that closed exposure within a bounded window (G20).

        The window is bounded on purpose: sweeping an account's whole history on
        startup would cost time and memory for no benefit.
        """
        if not self.ensure():
            return ()
        until = until or datetime.now(timezone.utc) + timedelta(minutes=1)
        deals = mt5.history_deals_get(since, until)
        if deals is None:
            logger.warning("history_deals_get failed: %s", mt5.last_error())
            return ()
        magic = self.config.magic
        out = []
        for d in deals:
            if int(getattr(d, "entry", -1)) != DEAL_ENTRY_OUT:
                continue
            if magic_only and int(getattr(d, "magic", 0)) != magic:
                continue
            out.append(
                ClosedDeal(
                    deal_id=int(d.ticket),
                    position_id=int(getattr(d, "position_id", 0)),
                    symbol=str(d.symbol),
                    profit=float(d.profit),
                    commission=float(getattr(d, "commission", 0.0)),
                    swap=float(getattr(d, "swap", 0.0)),
                    price=float(d.price),
                    volume=float(d.volume),
                    time=datetime.fromtimestamp(int(d.time), tz=timezone.utc),
                )
            )
        return tuple(sorted(out, key=lambda deal: deal.time))

    def order_send(
        self,
        symbol: str,
        side: str,
        volume: float,
        price: float,
        sl: float,
        tp: float,
    ) -> Any:
        """Send at the price the risk gate validated (I-12).

        The price is NOT re-read from the tick here; doing so would mean the
        order fills at a level the $2 ceiling was never checked against.
        """
        if not self.ensure():
            return None
        spec = self.provider.get_symbol_info(symbol)
        if spec is None:
            logger.error("Missing symbol spec for %s", symbol)
            return None

        order_type = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL
        filling = spec.filling_mode
        if filling & 2:
            type_filling = mt5.ORDER_FILLING_IOC
        elif filling & 1:
            type_filling = mt5.ORDER_FILLING_FOK
        else:
            type_filling = mt5.ORDER_FILLING_RETURN

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": order_type,
            "price": float(price),
            "sl": float(sl),
            "tp": float(tp),
            "deviation": int(self.config.order_deviation(symbol)),
            "magic": self.config.magic,
            "comment": (
                "Weekend Sniper Bot"
                if symbol.upper() == "BTCUSD"
                else self.config.get("order_comment", default="Sniper $100 Bot")
            ),
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": type_filling,
        }
        result = mt5.order_send(request)
        if result is None:
            logger.error("order_send None: %s", mt5.last_error())
            return None
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error("order_send failed: retcode=%s comment=%s", result.retcode, result.comment)
        else:
            logger.info(
                "Executed %s %s vol=%.2f price=%s sl=%s tp=%s ticket=%s",
                side,
                symbol,
                volume,
                result.price,
                sl,
                tp,
                result.order,
            )
        return result

    def close_position(self, position: OpenPosition) -> Any:
        """Flatten one position with an opposite-side market deal (Sprint 1 #6).

        Used for the Friday-gap protect policy: closing FX/XAU exposure ahead
        of the weekend gap is cheaper than trailing through it.
        """
        if not self.ensure():
            return None
        spec = self.provider.get_symbol_info(position.symbol)
        tick = self.provider.get_tick(position.symbol)
        if spec is None or tick is None:
            logger.error(
                "Cannot close %s ticket=%s: no spec/tick", position.symbol, position.ticket
            )
            return None

        order_type = mt5.ORDER_TYPE_SELL if position.is_buy else mt5.ORDER_TYPE_BUY
        filling = spec.filling_mode
        if filling & 2:
            type_filling = mt5.ORDER_FILLING_IOC
        elif filling & 1:
            type_filling = mt5.ORDER_FILLING_FOK
        else:
            type_filling = mt5.ORDER_FILLING_RETURN
        price = tick.exit_price("BUY" if position.is_buy else "SELL")

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": position.symbol,
            "volume": float(position.volume),
            "type": order_type,
            "position": int(position.ticket),
            "price": float(price),
            "deviation": int(self.config.order_deviation(position.symbol)),
            "magic": self.config.magic,
            "comment": "Friday gap flatten",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": type_filling,
        }
        result = mt5.order_send(request)
        if result is None:
            logger.error("close_position order_send None: %s", mt5.last_error())
            return None
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(
                "close_position failed: retcode=%s comment=%s", result.retcode, result.comment
            )
        else:
            logger.info(
                "Closed %s ticket=%s @%s (Friday gap flatten)",
                position.symbol,
                position.ticket,
                result.price,
            )
        return result

    def modify_sltp(self, change: dict[str, Any]) -> Any:
        """Apply an SL/TP change produced by risk.manage_open_position."""
        if not self.ensure():
            return None
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": int(change["ticket"]),
            "symbol": change["symbol"],
            "sl": float(change["sl"]),
            "tp": float(change["tp"]),
        }
        return mt5.order_send(request)

    @staticmethod
    def is_done(result: Any) -> bool:
        return result is not None and result.retcode == mt5.TRADE_RETCODE_DONE
