"""24/7 Multi-Pair Sniper bot for $100 FBS Demo (FX weekdays + BTCUSD weekends)."""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.audit_log import AuditLog
from src.config import Config, load_config
from src.decision_id import DecisionIdFactory
from src.features import build_features
from src.friday_gap import block_new_entries_friday
from src.fusion import fuse_signal
from src.learning.confidence import ConfidenceStore
from src.learning.drift_guard import DriftGuard
from src.learning.replay import ExperienceBuffer
from src.market_data import MarketDataProvider
from src.model_runner import ModelRunner, build_live_feature_row
from src.money_management import check_free_margin, resolve_mm
from src.mt5_connector import MT5Connector
from src.news.forexfactory import ForexFactoryCalendar, is_high_impact_near
from src.news.headlines import fetch_headlines
from src.news.sentiment import SentimentEngine
from src.zones import opposing_structure_tp, structure_trail_sl
from src.alerts_telegram import send_telegram
from src.accounts import consume_mt5_reload_flag
from src.paths import halt_file, log_dir
from src.portfolio_snapshot import write_portfolio_snapshot
from src.reject_codes import ACCEPTED, POST_FILL_RISK_EXCESS, Reject, RejectCounter
from src.risk import (
    OpenPosition,
    RiskBreakers,
    build_trade_levels,
    is_spread_ok,
    manage_open_position,
    post_fill_risk_usd,
    validate_entry_drift,
    validate_sl_in_range,
)
from src.server_time import ServerClock
from src.session_filter import in_session
from src.sniper import build_sniper_candidate
from src.trade_ledger import TradeLedger, sync_orphaned_deals

logger = logging.getLogger("sniper.main")


def setup_logging(cfg: Config) -> None:
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)

    rotating = RotatingFileHandler(
        log_dir() / "sniper.log",
        maxBytes=int(cfg.get("logging", "max_bytes", default=5_000_000)),
        backupCount=int(cfg.get("logging", "backup_count", default=5)),
        encoding="utf-8",
    )
    rotating.setFormatter(fmt)
    root.addHandler(rotating)


@dataclass
class Runtime:
    cfg: Config
    mt5c: MT5Connector
    provider: MarketDataProvider
    clock: ServerClock
    runner: ModelRunner
    confidence: ConfidenceStore
    experience: ExperienceBuffer
    calendar: ForexFactoryCalendar
    sentiment: SentimentEngine
    breakers: RiskBreakers
    audit: AuditLog
    ids: DecisionIdFactory
    ledger: TradeLedger
    counter: RejectCounter
    drift: DriftGuard
    # Sprint 1 P0: cached SL distance at open, keyed by MT5 position ticket.
    # Survives break-even moving the live SL to entry, which otherwise zeroes
    # out the R basis manage_open_position needs to keep trailing.
    initial_sl_by_ticket: dict[int, float] = field(default_factory=dict)


def build_runtime(cfg: Config) -> Runtime:
    mt5c = MT5Connector(cfg)
    clock = ServerClock(mt5c.server_time)
    mt5c.add_connect_hook(clock.sync)

    runner = ModelRunner(cfg.resolve_path("paths", "models"))
    confidence = ConfidenceStore(
        path=ROOT / cfg.get("learning", "state_path", default="models/confidence_state.json"),
        symbols=cfg.symbols,
        alpha=float(cfg.get("learning", "alpha", default=0.1)),
        w_min=float(cfg.get("learning", "w_min", default=0.3)),
        w_max=float(cfg.get("learning", "w_max", default=2.0)),
    )
    experience = ExperienceBuffer(
        cfg.resolve_path("paths", "experience"),
        max_lines_per_day=int(cfg.get("learning", "max_lines_per_day", default=500)),
    )
    breakers = RiskBreakers(
        monthly_max_dd_pct=float(cfg.get("risk", "monthly_max_dd_pct", default=10.0)),
        daily_max_loss_r=cfg.daily_max_loss_r,
        max_consecutive_losses=cfg.max_consecutive_losses,
        risk_unit_usd=cfg.max_risk_usd,
    )
    audit = AuditLog(
        max_bytes=int(cfg.get("logging", "max_bytes", default=5_000_000)),
        backup_count=int(cfg.get("logging", "backup_count", default=5)),
    )
    # Sprint 1 #4: DriftGuard must exist before the ledger so it can be wired
    # into the close path at construction, with no import cycle (trade_ledger
    # only needs `.record(r)`, never the DriftGuard class itself).
    drift = DriftGuard(
        window=int(cfg.get("learning", "drift_window", default=20)),
        min_trades=int(cfg.get("learning", "drift_min_trades", default=10)),
        min_win_rate=float(cfg.get("learning", "drift_min_win_rate", default=0.30)),
        min_expectancy_r=float(cfg.get("learning", "drift_min_expectancy_r", default=-0.25)),
    )
    ledger = TradeLedger(audit, experience, confidence, breakers, rr=cfg.rr, drift=drift)

    return Runtime(
        cfg=cfg,
        mt5c=mt5c,
        provider=mt5c.provider,
        clock=clock,
        runner=runner,
        confidence=confidence,
        experience=experience,
        calendar=ForexFactoryCalendar(),
        sentiment=SentimentEngine(
            cache_minutes=int(cfg.get("news", "sentiment_cache_minutes", default=60))
        ),
        breakers=breakers,
        audit=audit,
        ids=DecisionIdFactory(),
        ledger=ledger,
        counter=RejectCounter(),
        drift=drift,
    )


def _initial_sl_distance(rt: Runtime, pos: OpenPosition) -> float:
    """Sprint 1 P0: SL distance at open, cached so BE moving SL to entry can't
    zero out the R basis manage_open_position trails from.

    Prefers the journal's `sl_distance` (set at execution, or when a position
    is adopted on startup); falls back to |price_open - sl| the first time a
    position is managed, and caches whichever value wins.
    """
    cached = rt.initial_sl_by_ticket.get(pos.ticket)
    if cached and cached > 0:
        return cached
    record = rt.ledger.open_trades.get(pos.ticket)
    value: float | None = None
    if record is not None:
        raw = record.get("sl_distance")
        if raw is not None:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                value = None
    if not value:
        value = abs(pos.price_open - pos.sl) if pos.sl else 0.0
    if value and value > 0:
        rt.initial_sl_by_ticket[pos.ticket] = value
    return value or 0.0


def _defensive_tighten_due(
    rt: Runtime, symbol: str, server_now: datetime, friday_block_hour: int
) -> bool:
    """Sprint 1 #6: halve the ATR trail ahead of high-impact news or the
    Friday gap so a late reversal costs less R than the normal trail would
    give back."""
    skip_cal = {s.upper() for s in (rt.cfg.get("news", "skip_calendar_symbols", default=[]) or [])}
    if symbol.upper() not in skip_cal:
        news_near, _ = is_high_impact_near(
            rt.calendar,
            rt.cfg.get("news", "currencies", default=["USD", "EUR", "JPY"]),
            pre_minutes=15,
            post_minutes=15,
        )
        if news_near:
            return True
    approaching, _ = block_new_entries_friday(
        server_now, block_from_hour_utc=max(0, friday_block_hour - 1)
    )
    return approaching


def _friday_flatten_due(
    rt: Runtime, symbol: str, server_now: datetime, friday_block_hour: int
) -> bool:
    """Sprint 1 #6: flatten FX/XAU (never BTC, which trades through the
    weekend) at the Friday gap-block hour, unless friday_flatten is off."""
    if symbol.upper() == "BTCUSD":
        return False
    if not bool(rt.cfg.get("risk", "friday_flatten", default=True)):
        return False
    due, _ = block_new_entries_friday(server_now, block_from_hour_utc=friday_block_hour)
    return due


def publish_portfolio_snapshot(rt: Runtime, active_symbols: list[str] | None = None) -> None:
    """Write live account + positions for the dashboard (no second MT5 client)."""
    try:
        snap = rt.provider.account_snapshot()
        if snap is None:
            return
        positions = []
        for p in rt.mt5c.positions(magic_only=False):
            positions.append(
                {
                    "ticket": p.ticket,
                    "symbol": p.symbol,
                    "side": "BUY" if p.is_buy else "SELL",
                    "entry": p.price_open,
                    "sl": p.sl,
                    "tp": p.tp,
                    "volume": p.volume,
                    "magic": p.magic,
                }
            )
        write_portfolio_snapshot(
            login=snap.login,
            server=snap.server,
            is_demo=snap.is_demo,
            currency=snap.currency,
            balance=snap.balance,
            equity=snap.equity,
            free_margin=snap.free_margin,
            margin=snap.margin,
            leverage=snap.leverage,
            positions=positions,
            active_symbols=active_symbols,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("portfolio snapshot skip: %s", exc)


def manage_positions(rt: Runtime) -> None:
    prefer_structure = bool(rt.cfg.get("risk", "prefer_structure_trail", default=True))
    trailing_atr_mult_base = float(rt.cfg.get("risk", "trailing_atr_mult", default=1.0))
    be_lock_pips = float(rt.cfg.get("risk", "be_lock_pips", default=2.0))
    be_lock_atr_mult = float(rt.cfg.get("risk", "be_lock_atr_mult", default=0.0))
    enable_staircase = bool(rt.cfg.get("risk", "enable_staircase", default=False))
    structure_max_giveback_atr = float(
        rt.cfg.get("risk", "structure_max_giveback_atr", default=1.0)
    )
    break_even_r = float(rt.cfg.get("risk", "break_even_r", default=1.0))
    trailing_start_r = float(rt.cfg.get("risk", "trailing_start_r", default=1.5))
    # Never activate structure trail before BE has room to park.
    activate_r = max(trailing_start_r, break_even_r)
    max_hold_bars = int(rt.cfg.get("risk", "max_hold_bars_m1", default=0) or 0)
    friday_block_hour = int(rt.cfg.get("risk", "friday_gap_block_hour_utc", default=18))
    server_now = rt.clock.now()

    for pos in rt.mt5c.positions(magic_only=True):
        spec = rt.provider.get_symbol_info(pos.symbol)
        tick = rt.provider.get_tick(pos.symbol)
        if spec is None or tick is None:
            continue

        if _friday_flatten_due(rt, pos.symbol, server_now, friday_block_hour):
            result = rt.mt5c.close_position(pos)
            if rt.mt5c.is_done(result):
                rt.initial_sl_by_ticket.pop(pos.ticket, None)
                logger.warning(
                    "Friday flatten: closed %s ticket=%s ahead of weekend gap",
                    pos.symbol,
                    pos.ticket,
                )
            else:
                rt.audit.risk_event(
                    None,
                    pos.symbol,
                    Reject.BROKER_EXECUTION,
                    action="friday_flatten",
                    ticket=pos.ticket,
                    retcode=getattr(result, "retcode", None),
                    comment=getattr(result, "comment", None),
                )
            continue

        rates = rt.mt5c.copy_rates(pos.symbol, "M1", 50)
        if rates.empty:
            continue

        if max_hold_bars > 0:
            record = rt.ledger.open_trades.get(pos.ticket) or {}
            opened_at = record.get("opened_at") or record.get("open_time")
            hold_bars = None
            if opened_at is not None:
                try:
                    import pandas as pd

                    t0 = pd.Timestamp(opened_at)
                    if t0.tzinfo is None:
                        t0 = t0.tz_localize("UTC")
                    hold_bars = int((pd.Timestamp(server_now, tz="UTC") - t0).total_seconds() // 60)
                except Exception:
                    hold_bars = None
            if hold_bars is None and len(rates) >= 2:
                # Fallback: approximate from M1 bar count since open if time missing.
                hold_bars = 0
            if hold_bars is not None and hold_bars >= max_hold_bars:
                result = rt.mt5c.close_position(pos)
                if rt.mt5c.is_done(result):
                    rt.initial_sl_by_ticket.pop(pos.ticket, None)
                    logger.warning(
                        "Max-hold flatten: closed %s ticket=%s after %s M1 bars",
                        pos.symbol,
                        pos.ticket,
                        hold_bars,
                    )
                continue

        feat, _ = build_features(rates)
        row = feat.iloc[-2] if len(feat) >= 2 else feat.iloc[-1]
        atr_value = row.get("atr_14")
        atr = float(atr_value) if atr_value == atr_value else 0.0

        initial_sl_distance = _initial_sl_distance(rt, pos)

        struct_sl = None
        if prefer_structure and initial_sl_distance > 0:
            m5 = rt.mt5c.copy_rates(pos.symbol, "M5", 80)
            if not m5.empty:
                price = tick.exit_price("BUY" if pos.is_buy else "SELL")
                favor = (price - pos.price_open) if pos.is_buy else (pos.price_open - price)
                favor_r = favor / initial_sl_distance
                struct_sl = structure_trail_sl(
                    m5,
                    side="BUY" if pos.is_buy else "SELL",
                    entry=pos.price_open,
                    current_sl=pos.sl,
                    favor_r=favor_r,
                    activate_r=activate_r,
                )

        trailing_atr_mult = trailing_atr_mult_base
        if _defensive_tighten_due(rt, pos.symbol, server_now, friday_block_hour):
            trailing_atr_mult = trailing_atr_mult_base * 0.5

        change = manage_open_position(
            pos,
            spec=spec,
            tick=tick,
            atr=atr,
            break_even_r=break_even_r,
            trailing_start_r=activate_r,
            trailing_atr_mult=trailing_atr_mult,
            structure_trail_sl=struct_sl,
            prefer_structure_trail=prefer_structure,
            initial_sl_distance=initial_sl_distance,
            be_lock_pips=be_lock_pips,
            be_lock_atr_mult=be_lock_atr_mult,
            enable_staircase=enable_staircase,
            structure_max_giveback_atr=structure_max_giveback_atr,
        )
        if not change:
            continue
        result = rt.mt5c.modify_sltp(change)
        applied = rt.mt5c.is_done(result)
        logger.info(
            "SL move %s ticket=%s -> %s (%s)",
            pos.symbol,
            pos.ticket,
            change["sl"],
            "ok" if applied else "rejected",
        )
        if not applied:
            rt.audit.risk_event(
                None,
                pos.symbol,
                Reject.BROKER_EXECUTION,
                action="modify_sltp",
                ticket=pos.ticket,
                retcode=getattr(result, "retcode", None),
                comment=getattr(result, "comment", None),
            )


def adopt_open_positions(rt: Runtime, server_now: datetime) -> int:
    """Journal-adopt MT5 positions the bot has no memory of opening (Sprint 1 #3).

    Runs once at startup, after sync_orphaned_deals: anything still open with
    our magic number but missing from the journal (crash before the trade
    record was written, journal loss, manual open) gets an adopted record so
    poll_closed_deals can fold its eventual close into learning, and
    manage_positions has an sl_distance basis to trail from.
    """
    adopted = 0
    for pos in rt.mt5c.positions(magic_only=True):
        if pos.ticket in rt.ledger.open_trades:
            continue
        rt.ledger.adopt_open_position(pos, server_now)
        distance = abs(pos.price_open - pos.sl) if pos.sl else 0.0
        if distance > 0:
            rt.initial_sl_by_ticket[pos.ticket] = distance
        adopted += 1
    if adopted:
        logger.warning("Adopted %d open MT5 position(s) with no journal entry", adopted)
    return adopted


def poll_closed_deals(rt: Runtime, server_now: datetime) -> int:
    """Fold newly closed trades into breakers, confidence and experience (G05/G06)."""
    if not rt.ledger.open_trades:
        return 0
    since = rt.ledger.sync_window_start(server_now, rt.cfg.orphan_lookback_days)
    deals = rt.mt5c.closed_deals(since, server_now)
    return rt.ledger.apply_closed_deals(deals, server_now)


def _reject(
    rt: Runtime,
    decision_id: str,
    symbol: str,
    code: Reject | str,
    model: Any = None,
    **fields: Any,
) -> bool:
    rt.counter.record(symbol, code)
    rt.audit.decision(decision_id, symbol, code, model=model, **fields)
    logger.info("[%s] %s %s", symbol, code, fields.get("detail", ""))
    return False


@dataclass
class EvaluatedCandidate:
    """Sprint 4 #3: everything `execute_candidate` needs, minus a live re-tick.

    Produced by `evaluate_candidate` once a symbol has cleared every gate up
    to (and including) fusion, so the main loop can score several symbols
    before committing to one (EV arbitration) instead of executing the first
    hit.
    """

    decision_id: str
    symbol: str
    model: Any
    candidate: Any
    decision: Any
    spread_pts: float
    score: float
    feat_row: Any
    bundle: Any
    spec: Any


def evaluate_candidate(rt: Runtime, symbol: str) -> EvaluatedCandidate | None:
    """Run every pre-execution gate; returns None (already rejected/logged)
    when the symbol has no live entry this cycle, else a scored candidate."""
    cfg = rt.cfg
    server_now = rt.clock.now()
    decision_id = rt.ids.next_id(symbol, server_now)

    # Sprint 4 #2: reject outside the symbol's configured UTC session before
    # touching models/rates — cheapest possible gate.
    if bool(cfg.get("risk", "session_filter_enabled", default=True)) and not in_session(
        symbol, server_now
    ):
        _reject(rt, decision_id, symbol, Reject.SESSION, detail="outside_session")
        return None

    bundle = rt.runner.get(symbol)
    if bundle is None:
        _reject(rt, decision_id, symbol, Reject.NO_MODEL, detail="model_file_missing")
        return None
    model = bundle.identity

    spec = rt.provider.get_symbol_info(symbol)
    tick = rt.provider.get_tick(symbol)
    if spec is None or tick is None:
        _reject(
            rt,
            decision_id,
            symbol,
            Reject.MARKET_DATA_UNAVAILABLE,
            model=model,
            detail="no_spec_or_tick",
        )
        return None

    max_spread_points = cfg.max_spread_points(symbol)
    avg_spread_points = cfg.calibrated_avg_spread_points(symbol)
    ok_spread, spread_pts = is_spread_ok(
        spec,
        tick,
        max_spread_points,
        avg_spread_points=avg_spread_points,
        max_mult_of_avg=cfg.spread_max_mult_of_avg,
    )
    if not ok_spread:
        detail = "TBD_CALIBRATE" if max_spread_points is None else "spread_above_cap"
        if (
            max_spread_points is not None
            and spread_pts <= float(max_spread_points)
            and avg_spread_points is not None
        ):
            detail = "spread_above_avg_mult"
        _reject(
            rt,
            decision_id,
            symbol,
            Reject.SPREAD,
            model=model,
            spread_points=round(spread_pts, 2),
            max_spread_points=max_spread_points,
            avg_spread_points=avg_spread_points,
            detail=detail,
        )
        return None

    df_h1 = rt.mt5c.copy_rates(symbol, "H1", 250)
    df_m15 = rt.mt5c.copy_rates(symbol, "M15", 200)
    df_m5 = rt.mt5c.copy_rates(symbol, "M5", 200)
    df_m1 = rt.mt5c.copy_rates(symbol, "M1", 300)
    if any(df.empty for df in (df_h1, df_m15, df_m5, df_m1)):
        _reject(rt, decision_id, symbol, Reject.DATA, model=model, detail="incomplete_rates")
        return None

    try:
        feat_row, _ = build_live_feature_row(df_h1, df_m15, df_m5, df_m1)
        if not bundle.feature_cols:
            _reject(
                rt, decision_id, symbol, Reject.DATA, model=model, detail="model_has_no_feature_cols"
            )
            return None
        missing = [c for c in bundle.feature_cols if c not in feat_row.index]
        if missing:
            _reject(
                rt,
                decision_id,
                symbol,
                Reject.DATA,
                model=model,
                detail="missing_features",
                missing=missing[:5],
            )
            return None
        probs = bundle.predict_proba_row(feat_row)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[%s] feature/predict failed: %s", symbol, exc)
        _reject(rt, decision_id, symbol, Reject.DATA, model=model, detail=f"feature_error:{exc}")
        return None

    candidate = build_sniper_candidate(
        df_h1,
        df_m15,
        df_m5,
        df_m1,
        probs,
        require_full_mtf=bool(cfg.get("sniper", "require_full_mtf", default=True)),
        atr_sl_mult=cfg.atr_sl_mult(symbol),
        atr_zscore_max=float(cfg.get("sniper", "atr_zscore_max", default=2.0)),
        atr_sl_timeframe=cfg.atr_sl_timeframe(symbol),
        require_fvg_volume=bool(cfg.get("sniper", "require_fvg_volume", default=True)),
        volume_sma_window=int(cfg.get("sniper", "volume_sma_window", default=20)),
        require_m15_break_with_fvg=bool(
            cfg.get("sniper", "require_m15_break_with_fvg", default=False)
        ),
        soft_sweep_bonus=float(cfg.get("sniper", "soft_sweep_bonus", default=0.05)),
        soft_choch_bonus=float(cfg.get("sniper", "soft_choch_bonus", default=0.05)),
    )
    if candidate.signal not in ("BUY", "SELL"):
        _reject(
            rt,
            decision_id,
            symbol,
            candidate.code,
            model=model,
            detail=candidate.reason,
            bias=candidate.bias,
            prob=round(candidate.confidence, 4),
            mtf_reasons=candidate.mtf.reasons,
        )
        return None

    skip_cal = {s.upper() for s in (cfg.get("news", "skip_calendar_symbols", default=[]) or [])}
    if symbol.upper() in skip_cal:
        news_blocked, news_reason = False, ""
    else:
        news_blocked, news_reason = is_high_impact_near(
            rt.calendar,
            cfg.get("news", "currencies", default=["USD", "EUR", "JPY"]),
            pre_minutes=int(cfg.get("news", "pre_minutes", default=30)),
            post_minutes=int(cfg.get("news", "post_minutes", default=15)),
        )
        if (
            cfg.get("news", "strict_news", default=True)
            and not rt.calendar.last_fetch_ok
            and not rt.calendar.has_cache
        ):
            news_blocked, news_reason = True, "calendar_unavailable_fail_closed"

    # Sprint 1 #9: an empty feed is neutral, not a synthetic Fed-flavoured
    # prompt — a fabricated headline used to bias every score hawkish/neutral
    # regardless of what actually happened.
    usd_headlines = fetch_headlines(limit=6)
    usd_sent = rt.sentiment.score_texts(usd_headlines).score if usd_headlines else 0.0
    usd_sent = max(-1.0, min(1.0, usd_sent + 0.25 * rt.calendar.deviation_score(["USD"])))

    decision = fuse_signal(
        symbol=symbol,
        side=candidate.signal,
        model_prob=candidate.confidence,
        min_prob=cfg.min_probability,
        news_blocked=news_blocked,
        news_block_reason=news_reason,
        breaker_code=rt.breakers.active_code(),
        has_open_position=rt.mt5c.has_open_positions(),
        mtf_aligned=candidate.mtf.aligned,
        mtf_reasons=candidate.mtf.reasons,
        usd_sentiment=usd_sent,
        confidence=rt.confidence,
        tech_weight=float(cfg.get("fusion", "tech_weight", default=0.6)),
        news_weight=float(cfg.get("fusion", "news_weight", default=0.4)),
        sentiment_engine=rt.sentiment,
        mtf_soft_score=candidate.soft_score,
        online_confidence=bool(cfg.get("learning", "online_confidence", default=False)),
    )
    if decision.signal not in ("BUY", "SELL"):
        _reject(
            rt,
            decision_id,
            symbol,
            decision.code,
            model=model,
            detail=decision.detail,
            prob=round(candidate.confidence, 4),
            threshold=round(decision.threshold, 4),
            fusion_score=round(decision.score, 4),
        )
        return None

    # Sprint 4 #3: EV arbitration score — prob * expected R:R. `cfg.rr` is the
    # fixed configured RR the bot always targets, so today this ranks mainly
    # on model probability; kept as prob*rr so a future per-symbol RR plugs in
    # without another signature change.
    score = float(candidate.confidence) * float(cfg.rr)

    return EvaluatedCandidate(
        decision_id=decision_id,
        symbol=symbol,
        model=model,
        candidate=candidate,
        decision=decision,
        spread_pts=spread_pts,
        score=score,
        feat_row=feat_row,
        bundle=bundle,
        spec=spec,
    )


def execute_candidate(rt: Runtime, evaluated: EvaluatedCandidate) -> bool:
    """Run the drift/margin/risk gates and send the order for the symbol the
    EV arbitration loop picked. Return True if an order was sent."""
    cfg = rt.cfg
    server_now = rt.clock.now()
    decision_id = evaluated.decision_id
    symbol = evaluated.symbol
    model = evaluated.model
    candidate = evaluated.candidate
    decision = evaluated.decision
    spread_pts = evaluated.spread_pts
    feat_row = evaluated.feat_row
    bundle = evaluated.bundle
    spec = evaluated.spec

    # I-12: re-read the tick so SL/TP anchor to the price we are about to send.
    exec_tick = rt.provider.get_tick(symbol)
    if exec_tick is None:
        return _reject(
            rt,
            decision_id,
            symbol,
            Reject.MARKET_DATA_UNAVAILABLE,
            model=model,
            detail="no_tick_before_send",
        )
    executable_price = exec_tick.executable_price(decision.signal)
    sl_distance = candidate.sl_distance

    drift_ok, drift = validate_entry_drift(
        candidate.entry, executable_price, sl_distance, cfg.max_entry_drift_r
    )
    if not drift_ok:
        rt.audit.risk_event(
            decision_id,
            symbol,
            Reject.ENTRY_DRIFT,
            candidate_entry=candidate.entry,
            executable_price=executable_price,
            drift=drift,
            sl_distance=sl_distance,
            max_drift_r=cfg.max_entry_drift_r,
        )
        return _reject(
            rt,
            decision_id,
            symbol,
            Reject.ENTRY_DRIFT,
            model=model,
            detail=f"drift_{drift:.6f}_gt_{cfg.max_entry_drift_r}R",
            drift=drift,
            sl_distance=sl_distance,
        )

    account_capital = rt.provider.account_capital()
    mm = resolve_mm(
        account_capital,
        sl_distance,
        spec,
        cfg.money_management_cfg(),
        fallback_lot=cfg.lot_volume,
        fallback_max_risk_usd=cfg.max_risk_usd,
        side=decision.signal,
        entry_price=executable_price,
    )
    if not mm.ok:
        return _reject(
            rt,
            decision_id,
            symbol,
            mm.code or Reject.PRE_SEND_RISK_BREACH,
            model=model,
            detail=mm.detail,
            account_capital=account_capital,
            mm_mode=mm.mode,
        )

    levels = build_trade_levels(
        spec=spec,
        side=decision.signal,
        executable_price=executable_price,
        structural_sl_distance=sl_distance,
        rr=cfg.rr,
        volume=mm.volume,
        max_risk_usd=mm.max_risk_usd,
        sl_caps=(cfg.get("risk", "per_symbol_sl_caps", default={}) or {}).get(symbol),
    )
    if not levels.ok:
        rt.audit.risk_event(
            decision_id,
            symbol,
            levels.code,
            detail=levels.detail,
            executable_price=executable_price,
            sl_distance=sl_distance,
            pre_send_risk_usd=round(levels.risk_usd, 4),
            max_risk_usd=mm.max_risk_usd,
            mm_mode=mm.mode,
            mm_lot=mm.volume,
            account_capital=mm.account_capital,
        )
        return _reject(
            rt,
            decision_id,
            symbol,
            levels.code,
            model=model,
            detail=levels.detail,
            pre_send_risk_usd=round(levels.risk_usd, 4),
        )

    # Sprint 2 #2: extend TP toward opposing H1 structure when farther than min_rr.
    try:
        df_h1_tp = rt.mt5c.copy_rates(symbol, "H1", 250)
        struct_tp = opposing_structure_tp(
            df_h1_tp,
            side=decision.signal,
            entry=levels.entry,
            sl_distance=levels.sl_distance or sl_distance,
            min_rr=float(cfg.get("risk", "opposing_tp_min_rr", default=3.0)),
            max_rr=float(cfg.get("risk", "opposing_tp_max_rr", default=8.0)),
        )
        if struct_tp.tp is not None and struct_tp.detail == "opposing_structure":
            buy = decision.signal.upper() in ("BUY", "BULLISH", "LONG")
            extended = float(struct_tp.tp)
            base_tp = float(levels.tp)
            if (buy and extended > base_tp) or (not buy and extended < base_tp):
                levels.tp = round(extended, spec.digits)
                logger.info(
                    "[%s] opposing TP extend -> %.5f (rr≈%s, %s)",
                    symbol,
                    levels.tp,
                    round(struct_tp.rr_to_structure or 0.0, 2),
                    struct_tp.detail,
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[%s] opposing_structure_tp skipped: %s", symbol, exc)

    required_margin = rt.provider.order_calc_margin(
        symbol, decision.signal, levels.volume, levels.entry
    )
    free_margin = rt.provider.free_margin()
    if required_margin is None:
        return _reject(
            rt,
            decision_id,
            symbol,
            Reject.MARKET_DATA_UNAVAILABLE,
            model=model,
            detail="order_calc_margin_failed",
        )
    margin = check_free_margin(required_margin, free_margin)
    if not margin.ok:
        rt.audit.risk_event(
            decision_id,
            symbol,
            Reject.NO_MONEY,
            detail=margin.detail,
            required_margin=round(margin.required_margin, 4),
            free_margin=round(margin.free_margin, 4),
            mm_mode=mm.mode,
            mm_lot=mm.volume,
            account_capital=mm.account_capital,
        )
        return _reject(
            rt,
            decision_id,
            symbol,
            Reject.NO_MONEY,
            model=model,
            detail=margin.detail,
            required_margin=round(margin.required_margin, 4),
            free_margin=round(margin.free_margin, 4),
        )

    result = rt.mt5c.order_send(
        symbol, decision.signal, levels.volume, levels.entry, levels.sl, levels.tp
    )
    if not rt.mt5c.is_done(result):
        rt.audit.risk_event(
            decision_id,
            symbol,
            Reject.BROKER_EXECUTION,
            action="order_send",
            retcode=getattr(result, "retcode", None),
            comment=getattr(result, "comment", None),
            requested_price=levels.entry,
            sl=levels.sl,
            tp=levels.tp,
        )
        return _reject(
            rt,
            decision_id,
            symbol,
            Reject.BROKER_EXECUTION,
            model=model,
            detail=f"retcode_{getattr(result, 'retcode', None)}",
        )

    fill_price = float(getattr(result, "price", levels.entry) or levels.entry)
    position_id = int(getattr(result, "order", 0))
    post_fill = post_fill_risk_usd(spec, levels.volume, fill_price, levels.sl)

    rt.audit.decision(
        decision_id,
        symbol,
        ACCEPTED,
        model=model,
        side=decision.signal,
        prob=round(candidate.confidence, 4),
        threshold=round(decision.threshold, 4),
        fusion_score=round(decision.score, 4),
        spread_points=round(spread_pts, 2),
        executable_price=executable_price,
        fill_price=fill_price,
        drift=drift,
        sl=levels.sl,
        tp=levels.tp,
        sl_distance=levels.sl_distance,
        pre_send_risk_usd=round(levels.risk_usd, 4),
        post_fill_risk_usd=round(post_fill, 4) if post_fill is not None else None,
    )
    rt.ledger.record_execution(
        decision_id,
        symbol,
        position_id=position_id,
        model=model,
        side=decision.signal,
        volume=levels.volume,
        requested_price=levels.entry,
        fill_price=fill_price,
        sl=levels.sl,
        tp=levels.tp,
        sl_distance=levels.sl_distance,
        pre_send_risk_usd=round(levels.risk_usd, 4),
        post_fill_risk_usd=round(post_fill, 4) if post_fill is not None else None,
        order_ticket=position_id,
    )
    rt.experience.append_entry(
        symbol,
        {
            "decision_id": decision_id,
            "side": decision.signal,
            "entry": fill_price,
            "sl": levels.sl,
            "tp": levels.tp,
            "prob": candidate.confidence,
            "fusion_score": decision.score,
            "features": feat_row[bundle.feature_cols].to_dict(),
            "position_id": position_id,
            **model.to_dict(),
        },
        now=server_now,
    )

    if post_fill is not None and post_fill > mm.max_risk_usd:
        # The trade is open; closing it now would realise the loss immediately.
        # Sprint 1 #8: try to tighten the SL back under the ceiling first —
        # only fall back to logging-and-holding when that would need an SL
        # tighter than the structural range allows (I-10: never squeeze SL
        # into an invalid band just to hit a risk number).
        tightened = False
        new_sl = levels.sl
        post_tighten_risk = post_fill
        if spec.tick_size > 0 and spec.tick_value > 0 and levels.volume > 0:
            needed_distance = mm.max_risk_usd * spec.tick_size / (spec.tick_value * levels.volume)
            sl_caps = (cfg.get("risk", "per_symbol_sl_caps", default={}) or {}).get(symbol)
            in_range, _ = validate_sl_in_range(needed_distance, spec, sl_caps)
            if in_range and needed_distance < levels.sl_distance:
                buy = decision.signal == "BUY"
                candidate_sl = round(
                    fill_price - needed_distance if buy else fill_price + needed_distance,
                    spec.digits,
                )
                mod_result = rt.mt5c.modify_sltp(
                    {
                        "ticket": position_id,
                        "symbol": symbol,
                        "sl": candidate_sl,
                        "tp": levels.tp,
                    }
                )
                if rt.mt5c.is_done(mod_result):
                    tightened = True
                    new_sl = candidate_sl
                    recalced = post_fill_risk_usd(spec, levels.volume, fill_price, candidate_sl)
                    if recalced is not None:
                        post_tighten_risk = recalced

        rt.audit.risk_event(
            decision_id,
            symbol,
            POST_FILL_RISK_EXCESS,
            post_fill_risk_usd=round(post_fill, 4),
            pre_send_risk_usd=round(levels.risk_usd, 4),
            max_risk_usd=mm.max_risk_usd,
            fill_price=fill_price,
            requested_price=levels.entry,
            tightened=tightened,
            new_sl=new_sl if tightened else None,
            post_tighten_risk_usd=round(post_tighten_risk, 4) if tightened else None,
        )
        if tightened:
            logger.warning(
                "[%s] %s tightened SL %s -> %s: risk $%.2f -> $%.2f (max $%.2f)",
                symbol,
                POST_FILL_RISK_EXCESS,
                levels.sl,
                new_sl,
                post_fill,
                post_tighten_risk,
                mm.max_risk_usd,
            )
        else:
            logger.warning(
                "[%s] %s post_fill=$%.2f > $%.2f (fill=%s requested=%s)",
                symbol,
                POST_FILL_RISK_EXCESS,
                post_fill,
                mm.max_risk_usd,
                fill_price,
                levels.entry,
            )

    # Sprint 1 #10: enough detail to sanity-check the trade from a phone.
    send_telegram(
        f"iRich opened {symbol} {decision.signal} lot={levels.volume:.2f} "
        f"fill={round(fill_price, spec.digits)} sl={round(levels.sl, spec.digits)} "
        f"tp={round(levels.tp, spec.digits)} risk=${levels.risk_usd:.2f}"
    )
    return True


def scan_symbol(rt: Runtime, symbol: str) -> bool:
    """Evaluate then execute one symbol in isolation. Return True if an order
    was sent. Kept for callers that scan a single symbol; the main loop uses
    `evaluate_candidate` + `execute_candidate` directly for EV arbitration
    (Sprint 4 #3) across the whole active symbol list in one scan cycle."""
    evaluated = evaluate_candidate(rt, symbol)
    if evaluated is None:
        return False
    return execute_candidate(rt, evaluated)


def main() -> None:
    cfg = load_config()

    setup_logging(cfg)
    rt = build_runtime(cfg)

    if not rt.mt5c.connect():
        raise SystemExit("MT5 connection failed")
    if not rt.clock.synced and not rt.clock.sync():
        raise SystemExit("Refusing to run: MT5 server time unavailable (I-09)")

    snap = rt.provider.account_snapshot()
    if snap is None or snap.balance <= 0:
        raise SystemExit("Refusing to run: account balance unavailable from MT5 portfolio")
    if snap.leverage <= 0:
        raise SystemExit("Refusing to run: account leverage unavailable from MT5 portfolio")

    rt.runner.load_all(cfg.symbols)
    server_now = rt.clock.now()

    sync_orphaned_deals(
        rt.ledger,
        rt.mt5c.closed_deals,
        server_now,
        lookback_days=cfg.orphan_lookback_days,
    )
    adopt_open_positions(rt, server_now)

    pending_calibration = cfg.symbols_pending_spread_calibration()
    if pending_calibration:
        logger.warning(
            "Spread TBD_CALIBRATE for %s — entries fail-closed until "
            "scripts/calibrate_spread.py (or scripts/bootstrap_spread_calibration.py) "
            "has sampled this symbol.",
            pending_calibration,
        )

    logger.info(
        "24/7 Sniper Engine | server_now=%s offset=%.1fs | weekday=%s | weekend=%s "
        "| portfolio balance=%.2f equity=%.2f free_margin=%.2f leverage=1:%s (%s) "
        "| option_a_lock=%s | breakers=%s",
        server_now.isoformat(),
        rt.clock.offset_seconds,
        cfg.weekday_symbols,
        cfg.weekend_symbols,
        snap.balance,
        snap.equity,
        snap.free_margin,
        int(snap.leverage),
        "DEMO" if snap.is_demo else "REAL",
        bool(cfg.get("money_management", "option_a_lock", default=False)),
        rt.breakers.snapshot(),
    )

    last_mode: str | None = None
    last_halt: bool | None = None
    try:
        while True:
            try:
                if not rt.mt5c.ensure():
                    time.sleep(cfg.scan_seconds)
                    continue

                if consume_mt5_reload_flag():
                    logger.info("Account switch detected — reconnecting MT5")
                    rt.mt5c.connected = False
                    if not rt.mt5c.connect():
                        logger.error("MT5 reconnect after account switch failed")
                        time.sleep(cfg.scan_seconds)
                        continue

                server_now = rt.clock.now_checked()
                active = cfg.active_symbols(server_now)
                publish_portfolio_snapshot(rt, active)
                mode = "weekend" if active == cfg.weekend_symbols else "weekday"
                if mode != last_mode:
                    logger.info("Mode=%s scanning %s", mode, active)
                    last_mode = mode

                breaker_code = rt.breakers.update_equity(rt.mt5c.account_equity(), server_now)
                poll_closed_deals(rt, server_now)

                manage_positions(rt)

                halted_by_file = halt_file().exists()
                if halted_by_file != last_halt:
                    logger.warning(
                        "Kill switch %s (%s)",
                        "ENGAGED" if halted_by_file else "released",
                        halt_file(),
                    )
                    last_halt = halted_by_file

                if halted_by_file:
                    rt.counter.record("*", Reject.HALT_FILE)
                elif breaker_code is not None:
                    logger.warning("Entries blocked by %s", breaker_code)
                elif rt.drift.state.halted:
                    logger.warning("Entries blocked by drift guard: %s", rt.drift.state.reason)
                elif block_new_entries_friday(
                    server_now,
                    block_from_hour_utc=int(
                        cfg.get("risk", "friday_gap_block_hour_utc", default=18)
                    ),
                )[0]:
                    rt.counter.record("*", Reject.FRIDAY_GAP)
                    logger.info("Friday gap guard: no new entries")
                elif not rt.mt5c.has_open_positions():
                    # Sprint 4 #3: EV arbitration — evaluate every active
                    # symbol this cycle, then execute only the best-scoring
                    # candidate (prob * expected_rr) instead of the first hit.
                    best: EvaluatedCandidate | None = None
                    for symbol in active:
                        evaluated = evaluate_candidate(rt, symbol)
                        if evaluated is not None and (best is None or evaluated.score > best.score):
                            best = evaluated
                    if best is not None:
                        execute_candidate(rt, best)

            except Exception as exc:  # noqa: BLE001
                logger.exception("Loop error: %s", exc)
                rt.mt5c.connected = False

            time.sleep(cfg.scan_seconds)
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    finally:
        logger.info("Reject tally: %s", rt.counter.totals())
        rt.audit.close()
        rt.mt5c.shutdown()


if __name__ == "__main__":
    main()
