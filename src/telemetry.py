"""Read-only telemetry helpers for the ops dashboard (no writes, no orders).

Gotchas enforced here:
1. Safe read — tolerate Windows file locks and partial JSONL lines.
2. Efficient tail — seek from EOF; never load an entire decisions.jsonl into RAM.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .audit_log import file_identity
from .config import Config, load_config
from .paths import ROOT, halt_file, log_dir, state_dir

DISPLAY_TZ = "Asia/Bangkok"

_portfolio_cache: dict[str, Any] = {"ts": 0.0, "data": None}
_PORTFOLIO_TTL_SEC = 2.5


def invalidate_portfolio_cache() -> None:
    _portfolio_cache["ts"] = 0.0
    _portfolio_cache["data"] = None


def safe_read_text(path: Path, *, retries: int = 2) -> str | None:
    """Read a text file, tolerating transient Windows locks."""
    last_exc: Exception | None = None
    for _ in range(max(1, retries)):
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except (PermissionError, OSError) as exc:
            last_exc = exc
    return None if last_exc is not None else None


def safe_read_json(path: Path, *, retries: int = 3) -> dict[str, Any] | None:
    """Read a JSON object file; retry briefly if mid-write truncated the body."""
    for _ in range(max(1, retries)):
        text = safe_read_text(path, retries=1)
        if text is None:
            return None
        text = text.strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        return data if isinstance(data, dict) else None
    return None


def _parse_jsonl_line(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return None
    return row if isinstance(row, dict) else None


def tail_jsonl(path: Path, limit: int = 100, *, chunk_size: int = 8192) -> list[dict[str, Any]]:
    """Return the last `limit` JSON objects from a JSONL file (oldest→newest).

    Seeks from EOF and walks backward — never loads the whole file.
    Incomplete final lines and decode errors are skipped (safe read).
    """
    limit = max(0, int(limit))
    if limit == 0 or not path.exists():
        return []

    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            pos = handle.tell()
            if pos == 0:
                return []
            chunks: list[bytes] = []
            # Collect enough bytes to cover `limit` lines (plus one partial).
            while pos > 0 and sum(1 for c in chunks for _ in c.split(b"\n")) <= limit + 1:
                read_size = min(chunk_size, pos)
                pos -= read_size
                handle.seek(pos)
                chunks.insert(0, handle.read(read_size))
                if pos == 0:
                    break
            data = b"".join(chunks)
    except (PermissionError, OSError, FileNotFoundError):
        return []

    # If we did not start at byte 0, drop the first (possibly partial) line.
    text = data.decode("utf-8", errors="replace")
    if pos > 0:
        nl = text.find("\n")
        if nl >= 0:
            text = text[nl + 1 :]
        else:
            text = ""

    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        row = _parse_jsonl_line(line)
        if row is not None:
            rows.append(row)
    if len(rows) > limit:
        rows = rows[-limit:]
    return rows


def count_codes_today(path: Path, *, day_key: str | None = None, max_scan_lines: int = 20_000) -> dict[str, int]:
    """Tally `code` for today's rows by scanning newest lines first (append-only log).

    Walks backward from EOF up to `max_scan_lines` parsed rows, then stops once
    a row older than `day_key` appears — avoids loading / scanning the whole file.
    """
    if day_key is None:
        day_key = datetime.now(timezone.utc).strftime("%Y%m%d")
    if not path.exists():
        return {}

    # Pull a generous newest window, then filter (memory-bounded).
    window = tail_jsonl(path, max_scan_lines)
    counts: Counter[str] = Counter()
    for row in reversed(window):
        ts = str(row.get("ts") or "")
        if len(ts) < 10:
            continue
        key = ts[0:4] + ts[5:7] + ts[8:10]
        if key < day_key:
            break
        if key != day_key:
            continue
        code = str(row.get("code") or row.get("status") or "?")
        counts[code] += 1
    return dict(counts)


def _portfolio_from_mt5(cfg: Config) -> dict[str, Any]:
    """Live portfolio for the dashboard.

    Prefer the bot-written snapshot (no MT5 contention). Only open a direct
    MT5 session when the snapshot is missing/stale (bot not running).
    """
    import time

    from .portfolio_snapshot import read_portfolio_snapshot

    now = time.monotonic()
    cached = _portfolio_cache.get("data")
    if cached is not None and (now - float(_portfolio_cache.get("ts") or 0)) < _PORTFOLIO_TTL_SEC:
        return cached

    file_snap = read_portfolio_snapshot()
    if file_snap is not None:
        _portfolio_cache["ts"] = now
        _portfolio_cache["data"] = file_snap
        return file_snap

    out: dict[str, Any] = {"available": False, "source": "mt5_direct"}
    try:
        from .mt5_connector import MT5Connector

        mt5c = MT5Connector(cfg)
        if not mt5c.connect():
            out["error"] = "mt5_connect_failed"
            _portfolio_cache["ts"] = now
            _portfolio_cache["data"] = out
            return out
        try:
            snap = mt5c.account_snapshot()
            if snap is None:
                out["error"] = "no_account_info"
                _portfolio_cache["ts"] = now
                _portfolio_cache["data"] = out
                return out
            positions = []
            for p in mt5c.positions(magic_only=False):
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
            out.update(
                {
                    "available": True,
                    "login": snap.login,
                    "server": snap.server,
                    "is_demo": snap.is_demo,
                    "currency": snap.currency,
                    "balance": snap.balance,
                    "equity": snap.equity,
                    "free_margin": snap.free_margin,
                    "margin": snap.margin,
                    "leverage": snap.leverage,
                    "positions": positions,
                }
            )
            _portfolio_cache["ts"] = now
            _portfolio_cache["data"] = out
            return out
        finally:
            mt5c.shutdown()
    except Exception as exc:  # noqa: BLE001 — telemetry must stay up
        out["error"] = str(exc)
        _portfolio_cache["ts"] = now
        _portfolio_cache["data"] = out
        return out


def build_health(cfg: Config | None = None) -> dict[str, Any]:
    cfg = cfg or load_config()
    logs = log_dir()
    decisions = logs / "decisions.jsonl"
    trades = logs / "trades.jsonl"
    now = datetime.now(timezone.utc)

    def _age(path: Path) -> float | None:
        try:
            return max(0.0, now.timestamp() - path.stat().st_mtime)
        except OSError:
            return None

    return {
        "ok": True,
        "ts": now.isoformat(),
        "display_timezone": DISPLAY_TZ,
        "halt": halt_file().exists(),
        "log_dir": str(logs),
        "decisions_age_sec": _age(decisions),
        "trades_age_sec": _age(trades),
        "decisions_exists": decisions.exists(),
        "trades_exists": trades.exists(),
        "symbols": list(cfg.symbols),
        "option_a_lock": bool(cfg.get("money_management", "option_a_lock", default=False)),
    }


def build_overview(cfg: Config | None = None, *, decision_limit: int = 40) -> dict[str, Any]:
    cfg = cfg or load_config()
    logs = log_dir()
    decisions_path = logs / "decisions.jsonl"
    day_key = datetime.now(timezone.utc).strftime("%Y%m%d")
    tally = count_codes_today(decisions_path, day_key=day_key)
    decisions = tail_jsonl(decisions_path, decision_limit)
    breaker = safe_read_json(state_dir() / "breaker_state.json") or {}
    portfolio = _portfolio_from_mt5(cfg)
    open_positions = portfolio.get("positions") or []
    primary = open_positions[0] if open_positions else None

    total = sum(tally.values()) or 1
    reject_rows = [
        {"code": code, "count": count, "percent": round(100.0 * count / total, 1)}
        for code, count in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
        if str(code).startswith("REJECT")
    ]

    mm = cfg.money_management_cfg()
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "display_timezone": DISPLAY_TZ,
        "portfolio": portfolio,
        "position": primary,
        "positions": open_positions,
        "breaker": breaker,
        "reject_tally": reject_rows,
        "decision_counts": tally,
        "decisions": decisions,
        "mm": {
            "mode": mm.get("mode", "FIXED_TIER"),
            "option_a_lock": bool(mm.get("option_a_lock", False)),
            "lot": cfg.lot_volume,
            "max_risk_usd": cfg.max_risk_usd,
        },
        "halt": halt_file().exists(),
        "active_symbols": list(
            portfolio.get("active_symbols")
            or cfg.active_symbols(datetime.now(timezone.utc))
        ),
        "data_source": {
            "portfolio": portfolio.get("source") or ("unavailable" if not portfolio.get("available") else "unknown"),
            "snapshot_age_sec": portfolio.get("snapshot_age_sec"),
        },
    }


def build_decisions(limit: int = 100) -> dict[str, Any]:
    limit = min(max(int(limit), 1), 500)
    rows = tail_jsonl(log_dir() / "decisions.jsonl", limit)
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "display_timezone": DISPLAY_TZ,
        "limit": limit,
        "count": len(rows),
        "items": rows,
    }


def build_trades(limit: int = 100, cfg: Config | None = None) -> dict[str, Any]:
    cfg = cfg or load_config()
    limit = min(max(int(limit), 1), 500)
    rows = tail_jsonl(log_dir() / "trades.jsonl", limit)
    portfolio = _portfolio_from_mt5(cfg)
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "display_timezone": DISPLAY_TZ,
        "items": rows,
        "open_positions": portfolio.get("positions") or [],
        "portfolio_available": bool(portfolio.get("available")),
    }


def build_risk(cfg: Config | None = None) -> dict[str, Any]:
    cfg = cfg or load_config()
    breaker = safe_read_json(state_dir() / "breaker_state.json") or {}
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "display_timezone": DISPLAY_TZ,
        "breaker": breaker,
        "limits": {
            "daily_max_loss_r": cfg.daily_max_loss_r,
            "max_consecutive_losses": cfg.max_consecutive_losses,
            "monthly_max_dd_pct": float(cfg.get("risk", "monthly_max_dd_pct", default=10.0)),
            "max_total_open": int(cfg.get("position_guard", "max_total_open", default=1)),
            "option_a_lock": bool(cfg.get("money_management", "option_a_lock", default=False)),
            "lot_volume": cfg.lot_volume,
            "max_risk_usd": cfg.max_risk_usd,
        },
        "halt": halt_file().exists(),
    }


def build_models(cfg: Config | None = None) -> dict[str, Any]:
    cfg = cfg or load_config()
    recent = tail_jsonl(log_dir() / "decisions.jsonl", 200)
    latest_by_symbol: dict[str, dict[str, Any]] = {}
    for row in reversed(recent):
        sym = str(row.get("symbol") or "")
        if sym and sym not in latest_by_symbol:
            latest_by_symbol[sym] = row

    models = []
    models_dir = ROOT / cfg.get("paths", "models", default="models")
    for symbol in cfg.symbols:
        path = models_dir / f"{symbol.lower()}_sniper.pkl"
        identity = file_identity(path)
        last = latest_by_symbol.get(symbol) or {}
        models.append(
            {
                "symbol": symbol,
                "exists": path.exists(),
                "model_path": identity.model_path,
                "model_mtime": identity.model_mtime,
                "model_sha256": identity.model_sha256,
                "last_code": last.get("code"),
                "last_detail": last.get("detail"),
                "last_ts": last.get("ts"),
                "last_prob": last.get("prob") or last.get("probability") or last.get("model_prob"),
                "last_signal": last.get("signal") or last.get("side"),
            }
        )
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "display_timezone": DISPLAY_TZ,
        "min_probability": float(cfg.get("sniper", "min_probability", default=0.75)),
        "items": models,
    }


def build_config_view(cfg: Config | None = None) -> dict[str, Any]:
    cfg = cfg or load_config()
    calibration = safe_read_json(cfg.spread_calibration_path) or {}
    spread_caps = {}
    for symbol in cfg.symbols:
        spread_caps[symbol] = {
            "max_points": cfg.max_spread_points(symbol),
            "avg_points": cfg.calibrated_avg_spread_points(symbol),
            "configured": (cfg.get("spread", "max_points_by_symbol", default={}) or {}).get(symbol),
        }
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "display_timezone": DISPLAY_TZ,
        "params": {
            "mm_mode": cfg.get("money_management", "mode", default="FIXED_TIER"),
            "option_a_lock": bool(cfg.get("money_management", "option_a_lock", default=False)),
            "rr": cfg.rr,
            "break_even_r": float(cfg.get("risk", "break_even_r", default=2.0)),
            "trailing_start_r": float(cfg.get("risk", "trailing_start_r", default=2.0)),
            "require_full_mtf": bool(cfg.get("sniper", "require_full_mtf", default=True)),
            "require_fvg_volume": bool(cfg.get("sniper", "require_fvg_volume", default=False)),
            "session_filter_enabled": bool(cfg.get("risk", "session_filter_enabled", default=False)),
            "friday_gap_block_hour_utc": int(cfg.get("risk", "friday_gap_block_hour_utc", default=18)),
            "min_probability": float(cfg.get("sniper", "min_probability", default=0.75)),
            "max_entry_drift_r": cfg.max_entry_drift_r,
            "max_mult_of_avg": cfg.spread_max_mult_of_avg,
        },
        "spread_caps": spread_caps,
        "calibration_updated_at": calibration.get("updated_at"),
        "calibration_method": calibration.get("method"),
        "symbols": {
            "all": list(cfg.symbols),
            "weekday": list(cfg.weekday_symbols),
            "weekend": list(cfg.weekend_symbols),
        },
    }


def build_analytics(limit: int = 2000) -> dict[str, Any]:
    """Aggregate closed trades from trades.jsonl — zeros when empty (no fake stats)."""
    rows = tail_jsonl(log_dir() / "trades.jsonl", min(max(int(limit), 1), 5000))
    closed = [r for r in rows if str(r.get("status", "")).upper() == "CLOSED"]
    pnls = []
    rs = []
    for r in closed:
        if r.get("pnl_usd") is not None:
            try:
                pnls.append(float(r["pnl_usd"]))
            except (TypeError, ValueError):
                pass
        if r.get("r_multiple") is not None:
            try:
                rs.append(float(r["r_multiple"]))
            except (TypeError, ValueError):
                pass
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "display_timezone": DISPLAY_TZ,
        "total_trades": len(closed),
        "wins": wins,
        "losses": losses,
        "total_pnl_usd": round(sum(pnls), 4) if pnls else 0.0,
        "total_r": round(sum(rs), 4) if rs else 0.0,
        "items": closed[-100:],
    }


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        text = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _ict_day_key(dt: datetime) -> str:
    try:
        from zoneinfo import ZoneInfo

        local = dt.astimezone(ZoneInfo(DISPLAY_TZ))
    except Exception:  # noqa: BLE001
        local = dt.astimezone(timezone.utc)
    return local.strftime("%Y-%m-%d")


def _closed_trade_events(limit: int = 5000) -> list[dict[str, Any]]:
    """Normalize closed trades into dated PnL events (ICT day)."""
    rows = tail_jsonl(log_dir() / "trades.jsonl", min(max(int(limit), 1), 5000))
    events: list[dict[str, Any]] = []
    for r in rows:
        if str(r.get("status", "")).upper() != "CLOSED":
            continue
        ts = _parse_ts(str(r.get("ts") or r.get("closed_at") or ""))
        if ts is None:
            continue
        try:
            pnl = float(r["pnl_usd"]) if r.get("pnl_usd") is not None else 0.0
        except (TypeError, ValueError):
            pnl = 0.0
        try:
            r_mult = float(r["r_multiple"]) if r.get("r_multiple") is not None else 0.0
        except (TypeError, ValueError):
            r_mult = 0.0
        events.append(
            {
                "day": _ict_day_key(ts),
                "ts": ts.isoformat(),
                "symbol": r.get("symbol"),
                "pnl_usd": pnl,
                "r_multiple": r_mult,
                "decision_id": r.get("decision_id"),
                "outcome": r.get("outcome") or r.get("exit_reason") or r.get("detail"),
            }
        )
    return events


def build_calendar(
    *,
    year: int | None = None,
    month: int | None = None,
    view: str = "month",
    day: str | None = None,
) -> dict[str, Any]:
    """Performance calendar in ICT — monthly day grid or weekly buckets."""
    try:
        from zoneinfo import ZoneInfo

        now_local = datetime.now(ZoneInfo(DISPLAY_TZ))
    except Exception:  # noqa: BLE001
        now_local = datetime.now(timezone.utc)

    y = int(year or now_local.year)
    m = int(month or now_local.month)
    view = (view or "month").lower()
    if view not in {"month", "week", "year"}:
        view = "month"

    events = _closed_trade_events()
    by_day: dict[str, dict[str, Any]] = {}
    for ev in events:
        day = ev["day"]
        bucket = by_day.setdefault(
            day,
            {"date": day, "pnl_usd": 0.0, "r_multiple": 0.0, "trades": 0, "wins": 0, "losses": 0},
        )
        bucket["pnl_usd"] = round(float(bucket["pnl_usd"]) + float(ev["pnl_usd"]), 4)
        bucket["r_multiple"] = round(float(bucket["r_multiple"]) + float(ev["r_multiple"]), 4)
        bucket["trades"] += 1
        if float(ev["pnl_usd"]) > 0:
            bucket["wins"] += 1
        elif float(ev["pnl_usd"]) < 0:
            bucket["losses"] += 1

    # Month cells (Mon-first grid) —
    import calendar as cal

    cal.setfirstweekday(cal.MONDAY)
    weeks_raw = cal.monthcalendar(y, m)
    month_days: list[dict[str, Any]] = []
    for week in weeks_raw:
        for d in week:
            if d == 0:
                month_days.append({"date": None, "in_month": False})
                continue
            key = f"{y:04d}-{m:02d}-{d:02d}"
            stats = by_day.get(key) or {
                "date": key,
                "pnl_usd": 0.0,
                "r_multiple": 0.0,
                "trades": 0,
                "wins": 0,
                "losses": 0,
            }
            month_days.append({**stats, "in_month": True, "day": d})

    week_rows: list[dict[str, Any]] = []
    for idx, week in enumerate(weeks_raw, start=1):
        days_in = [d for d in week if d]
        if not days_in:
            continue
        start = f"{y:04d}-{m:02d}-{days_in[0]:02d}"
        end = f"{y:04d}-{m:02d}-{days_in[-1]:02d}"
        pnl = 0.0
        trades = 0
        traded_days = 0
        for d in days_in:
            key = f"{y:04d}-{m:02d}-{d:02d}"
            stats = by_day.get(key)
            if not stats:
                continue
            pnl += float(stats["pnl_usd"])
            trades += int(stats["trades"])
            if int(stats["trades"]) > 0:
                traded_days += 1
        week_rows.append(
            {
                "week_index": idx,
                "label": f"Week {idx}",
                "start": start,
                "end": end,
                "pnl_usd": round(pnl, 4),
                "trades": trades,
                "days_traded": traded_days,
            }
        )

    # Yearly week buckets (ISO weeks in ICT year)
    year_weeks: list[dict[str, Any]] = []
    if view == "year":
        from datetime import timedelta

        # Jan 1 local → find Monday of that week through Dec 31
        try:
            from zoneinfo import ZoneInfo

            tz = ZoneInfo(DISPLAY_TZ)
        except Exception:  # noqa: BLE001
            tz = timezone.utc
        cursor = datetime(y, 1, 1, tzinfo=tz)
        # rewind to Monday
        cursor = cursor - timedelta(days=cursor.weekday())
        end_year = datetime(y, 12, 31, tzinfo=tz)
        w = 1
        while cursor.year <= y and cursor <= end_year + timedelta(days=7):
            w_end = cursor + timedelta(days=6)
            keys = [(cursor + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
            pnl = 0.0
            trades = 0
            for key in keys:
                stats = by_day.get(key)
                if not stats:
                    continue
                # only count days belonging to selected year for year view totals
                if key.startswith(f"{y:04d}-"):
                    pnl += float(stats["pnl_usd"])
                    trades += int(stats["trades"])
            year_weeks.append(
                {
                    "week_index": w,
                    "label": f"Week {w}",
                    "start": keys[0],
                    "end": keys[-1],
                    "pnl_usd": round(pnl, 4),
                    "trades": trades,
                    "empty": trades == 0,
                }
            )
            cursor = w_end + timedelta(days=1)
            w += 1
            if w > 54:
                break

    # Period summary (selected month)
    prefix = f"{y:04d}-{m:02d}-"
    period_days = [v for k, v in by_day.items() if k.startswith(prefix)]
    period_pnl = sum(float(d["pnl_usd"]) for d in period_days)
    period_trades = sum(int(d["trades"]) for d in period_days)
    period_wins = sum(int(d["wins"]) for d in period_days)
    period_losses = sum(int(d["losses"]) for d in period_days)
    period_r = sum(float(d["r_multiple"]) for d in period_days)
    win_rate = (period_wins / period_trades) if period_trades else 0.0
    avg_win = 0.0
    avg_loss = 0.0
    win_pnls = [float(d["pnl_usd"]) for d in period_days if float(d["pnl_usd"]) > 0]
    loss_pnls = [float(d["pnl_usd"]) for d in period_days if float(d["pnl_usd"]) < 0]
    if win_pnls:
        avg_win = sum(win_pnls) / len(win_pnls)
    if loss_pnls:
        avg_loss = sum(loss_pnls) / len(loss_pnls)
    gross_win = sum(win_pnls)
    gross_loss = abs(sum(loss_pnls))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)

    month_names = [
        "",
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ]

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "display_timezone": DISPLAY_TZ,
        "view": view,
        "year": y,
        "month": m,
        "month_label": f"{month_names[m]} {y}",
        "weekday_labels": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "days": month_days,
        "weeks": week_rows,
        "year_weeks": year_weeks,
        "summary": {
            "net_pnl_usd": round(period_pnl, 4),
            "trades": period_trades,
            "wins": period_wins,
            "losses": period_losses,
            "win_rate": round(win_rate, 4),
            "total_r": round(period_r, 4),
            "avg_win_usd": round(avg_win, 4),
            "avg_loss_usd": round(avg_loss, 4),
            "profit_factor": None if profit_factor == float("inf") else round(profit_factor, 4),
            "expected_value_usd": round(period_pnl / period_trades, 4) if period_trades else 0.0,
        },
        "selected_day": day,
        "selected_day_trades": [e for e in events if day and e["day"] == day],
    }
