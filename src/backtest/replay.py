"""Bar replay over historical CSVs using the live risk contract (G16).

The gates, SL/TP construction and $2 ceiling come from src.risk, and prices come
from ReplayProvider, so nothing here can accidentally price a historical trade
with today's tick value.

Two properties matter for interpreting results:

* A decision is taken on a completed M1 bar and executed at the next bar's open,
  which is where entry drift comes from - the same asymmetry live trading has.
* When a single bar spans both SL and TP the trade is scored as SL. Bar data
  cannot say which came first, and the optimistic reading would flatter the
  result.

Sprint 3 (iRich Profit Upgrade v1.4) — exit simulation parity with live
management
--------------------------------------------------------------------------
Before this sprint the exit scan only ever checked the *static* SL/TP set at
open, which is not what the live bot does once a trade is running
(`src.risk.manage_open_position`, `src.zones.structure_trail_sl`,
`manage_positions` in `src/main.py`). `ReplayParams.enable_exit_management`
(default True) now reproduces that management one completed M1 bar at a
time, in this order, exactly mirroring the live call order:

1. Break-even lock once favour >= `break_even_r * initial_sl_distance`,
   parked `be_lock_pips`/`be_lock_atr_mult` past true break-even (Sprint 1
   #2) — never on top of `|price_open - sl|`, which collapses to 0 once BE
   has already moved the SL (Sprint 1 P0). `initial_sl_distance` (the
   structural SL distance recorded at open) is the fixed R basis for the
   life of the trade, same as the live `initial_sl_by_ticket` cache.
2. Structure trail (`prefer_structure_trail`, M5 swing levels) when a valid
   candidate exists, else ATR trail once favour >= `trailing_start_r *
   initial_sl_distance` — never both, matching `manage_open_position`.
3. Optional staircase scaffolding (`enable_staircase`): once favour crosses
   3R/4R/5R, the SL is floored at +1R/+2R/+3R (never loosened). Off by
   default; this is scaffolding for `scripts/exit_policy_ab.py`, not a
   locked live behaviour.

`ReplayParams.session_filter_enabled` (default False, opt-in) reuses
`src.session_filter.in_session` on the signal bar, the same gate Sprint 4 #2
wired into live entries — kept opt-in here so existing replay scripts that
predate this flag do not silently change trade counts.

Fusion parity (`ReplayParams.apply_fusion_news`) is a documented stub, not an
approximation: `src.fusion.fuse_signal` needs a historical news/sentiment
timeseries (`src.news.sentiment.SentimentEngine`) this vectorised bar replay
does not carry. Setting the flag True raises `NotImplementedError` rather
than silently reporting a result "as if" fusion had run.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..features import build_features
from ..market_data import ReplayProvider, SymbolSpec
from ..model_runner import ModelBundle, build_training_frame
from ..money_management import VirtualAccount, check_free_margin, resolve_mm
from ..reject_codes import ACCEPTED, Reject
from ..risk import build_trade_levels, calc_risk_usd, is_spread_ok, sl_range, validate_entry_drift
from ..session_filter import in_session
from ..smc import choch_flags, fvg_zones, sweep_flags, swing_levels

logger = logging.getLogger(__name__)

SIGNAL_COLS = ["bias_bull", "bias_bear", "sweep_bull", "sweep_bear", "choch_bull", "choch_bear"]


@dataclass
class ReplayParams:
    rr: float = 3.0
    volume: float = 0.01
    max_risk_usd: float = 2.0
    min_probability: float = 0.75
    max_entry_drift_r: float = 0.25
    atr_sl_mult: float = 1.0
    atr_zscore_max: float = 2.0
    require_full_mtf: bool = True
    max_spread_points: float | None = None
    max_hold_bars: int = 500
    sl_caps: dict[str, Any] | None = None
    mm_cfg: dict[str, Any] | None = None
    starting_balance: float = 100.0
    use_virtual_account: bool = True
    track_compounding: bool = True
    atr_sl_timeframe: str = "M5"
    # Sprint 4 #1: hard gate regardless of require_full_mtf — mirrors
    # smc.check_fvg_volume/mtf.evaluate_mtf so the A/B harness measures the
    # same funnel the live path applies.
    require_fvg_volume: bool = True
    volume_sma_window: int = 20
    # --- Sprint 3 (iRich Profit Upgrade v1.4): exit simulation parity with
    # live position management. Defaults mirror config.yaml's current
    # (Sprint 1/2) live values. break_even_r stays 1.0 here on purpose —
    # config.yaml must not move to 2.0 until scripts/exit_policy_ab.py
    # recommends it (that is a separate, deliberate config change).
    enable_exit_management: bool = True
    break_even_r: float = 1.0
    trailing_start_r: float = 1.5
    trailing_atr_mult: float = 1.0
    prefer_structure_trail: bool = True
    be_lock_pips: float = 2.0
    be_lock_atr_mult: float = 0.0
    # Optional staircase scaffolding: once favour crosses trigger_r, floor the
    # SL at entry +/- lock_r * initial_sl_distance (never loosens). Off by
    # default — scaffolding for the exit_policy A/B, not a locked behaviour.
    enable_staircase: bool = False
    staircase_levels: tuple[tuple[float, float], ...] = (
        (3.0, 1.0),
        (4.0, 2.0),
        (5.0, 3.0),
    )
    # Reuses src.session_filter.in_session on the signal bar (Sprint 4 #2's
    # live gate). Default False (opt-in) so existing replay scripts written
    # before this flag existed do not silently change trade counts.
    session_filter_enabled: bool = False
    # Stub only — see module docstring "Fusion parity". True raises rather
    # than silently approximating src.fusion.fuse_signal's news gate.
    apply_fusion_news: bool = False


@dataclass
class ReplayTrade:
    symbol: str
    side: str
    opened_at: pd.Timestamp
    closed_at: pd.Timestamp
    candidate_entry: float
    executable_price: float
    drift: float
    sl: float
    tp: float
    exit_price: float
    outcome: str
    r_multiple: float
    risk_usd: float
    prob: float
    bars_held: int
    volume: float = 0.01
    balance_after: float | None = None
    pnl_usd: float | None = None


@dataclass
class ReplayResult:
    symbol: str
    bars: int
    trades: list[ReplayTrade] = field(default_factory=list)
    rejects: Counter = field(default_factory=Counter)
    # Structural SL distances of every candidate that reached the range gate.
    # Needed to tell "the model never fires" apart from "the band is too narrow".
    sl_distances: list[float] = field(default_factory=list)
    sl_band: tuple[float, float] | None = None

    def sl_distance_report(self) -> dict[str, Any]:
        if not self.sl_distances:
            return {"count": 0}
        values = np.asarray(self.sl_distances, dtype=float)
        report = {
            "count": int(values.size),
            "min": round(float(values.min()), 6),
            "p25": round(float(np.percentile(values, 25)), 6),
            "median": round(float(np.median(values)), 6),
            "p75": round(float(np.percentile(values, 75)), 6),
            "max": round(float(values.max()), 6),
        }
        if self.sl_band:
            low, high = self.sl_band
            report.update(
                {
                    "band_min": round(low, 6),
                    "band_max": round(high, 6),
                    "below_band": int((values < low).sum()),
                    "in_band": int(((values >= low) & (values <= high)).sum()),
                    "above_band": int((values > high).sum()),
                }
            )
        return report

    def summary(self) -> dict[str, Any]:
        wins = [t for t in self.trades if t.outcome == "tp"]
        losses = [t for t in self.trades if t.outcome == "sl"]
        r_values = [t.r_multiple for t in self.trades]
        hold_bars = [t.bars_held for t in self.trades]
        return {
            "symbol": self.symbol,
            "bars": self.bars,
            "trades": len(self.trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(self.trades), 4) if self.trades else 0.0,
            "sum_r": round(float(np.sum(r_values)), 3) if r_values else 0.0,
            "expectancy_r": round(float(np.mean(r_values)), 4) if r_values else 0.0,
            # Sprint 3: mean bars held to exit — dynamic BE/trail management
            # changes hold time versus the old static SL/TP-only scan.
            "avg_hold_bars": round(float(np.mean(hold_bars)), 2) if hold_bars else 0.0,
            "max_risk_usd": round(max((t.risk_usd for t in self.trades), default=0.0), 4),
            "rejects": dict(self.rejects.most_common()),
            "sl_distance": self.sl_distance_report(),
        }


def _asof_shift(left: pd.DataFrame, right: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Attach higher-timeframe columns using only the previous completed bar."""
    r = right[["time"] + cols].sort_values("time").copy()
    r[cols] = r[cols].shift(1)
    r["time"] = r["time"] + pd.Timedelta(seconds=1)
    return pd.merge_asof(left.sort_values("time"), r, on="time", direction="backward")


def build_replay_frame(
    df_h1: pd.DataFrame,
    df_m15: pd.DataFrame,
    df_m5: pd.DataFrame,
    df_m1: pd.DataFrame,
) -> tuple[pd.DataFrame, list[str]]:
    """Model features (identical to training) plus the MTF signal columns."""
    frame, feature_cols = build_training_frame(df_h1, df_m15, df_m5, df_m1)

    h1, _ = build_features(df_h1)
    h1["bias_bull"] = (h1["close"] > h1["ema_200"]).astype(float)
    h1["bias_bear"] = (h1["close"] < h1["ema_200"]).astype(float)
    frame = _asof_shift(frame, h1, ["bias_bull", "bias_bear"])

    m15 = build_features(df_m15)[0]
    m15["sweep_bull"], m15["sweep_bear"] = (f.astype(float) for f in sweep_flags(m15))
    frame = _asof_shift(frame, m15, ["sweep_bull", "sweep_bear"])

    m5 = build_features(df_m5)[0]
    m5["choch_bull"], m5["choch_bear"] = (f.astype(float) for f in choch_flags(m5))
    frame = _asof_shift(frame, m5, ["choch_bull", "choch_bear"])
    # Sprint 3: M5 swing levels for live-parity structure trailing
    # (src.zones.structure_trail_sl uses swing_levels(swing=2, lookback=40) on
    # M5). Merged the same way as the other M5-derived columns above: only the
    # previous *completed* M5 bar's swing level is visible to a given M1 row.
    m5["m5_swing_low"], m5["m5_swing_high"] = swing_levels(m5, swing=2, lookback=40)
    frame = _asof_shift(frame, m5, ["m5_swing_low", "m5_swing_high"])

    m1 = build_features(df_m1)[0]
    zones = fvg_zones(m1)
    m1 = pd.concat([m1[["time"]], zones], axis=1)
    frame = frame.merge(m1, on="time", how="left")

    frame = frame.dropna(subset=SIGNAL_COLS).reset_index(drop=True)
    # G21: structural SL ATR floor comes from M5 (already in training frame as m5_atr_14).
    if "m5_atr_14" not in frame.columns and "m1_atr_14" in frame.columns:
        frame["m5_atr_14"] = frame["m1_atr_14"]
    return frame, feature_cols


def _fvg_entry(
    row: pd.Series,
    bullish: bool,
    atr: float,
    atr_sl_mult: float,
) -> tuple[bool, float] | None:
    """Reproduce smc.detect_fvg_entry for one completed bar (ATR = M5 floor)."""
    top = row["bull_top"] if bullish else row["bear_top"]
    bottom = row["bull_bottom"] if bullish else row["bear_bottom"]
    if pd.isna(top) or pd.isna(bottom):
        return None
    price = float(row["close"])
    in_zone = bottom <= price <= top
    touched = float(row["low"]) <= top and float(row["high"]) >= bottom
    if not (in_zone or touched):
        return None
    if bullish:
        structural_sl = min(bottom, price - atr * atr_sl_mult) if atr > 0 else bottom
    else:
        structural_sl = max(top, price + atr * atr_sl_mult) if atr > 0 else top
    return True, float(structural_sl)


def _staircase_floor(
    favor_r: float,
    r_basis: float,
    entry: float,
    buy: bool,
    levels: tuple[tuple[float, float], ...],
) -> float | None:
    """Sprint 3 optional scaffolding: once favour crosses `trigger_r`, floor
    the SL at entry +/- `lock_r * r_basis` (r_basis == initial_sl_distance).

    Returns None when no level has triggered yet. Never proposes a *looser*
    floor than a level already crossed — the caller combines this with
    whatever BE/trail already proposed via max() (buy) / min() (sell), so a
    pullback bar can never walk the SL back down.
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


def _advance_sl(
    *,
    buy: bool,
    entry: float,
    current_sl: float,
    close: float,
    atr: float,
    r_basis: float,
    spec: SymbolSpec,
    break_even_r: float,
    trailing_start_r: float,
    trailing_atr_mult: float,
    prefer_structure_trail: bool,
    swing_level: float | None,
    be_lock_pips: float,
    be_lock_atr_mult: float,
    enable_staircase: bool,
    staircase_levels: tuple[tuple[float, float], ...],
) -> float:
    """One completed M1 bar's SL update: BE lock, then structure-or-ATR
    trail, then the optional staircase floor.

    Mirrors `src.risk.manage_open_position` + `src.zones.structure_trail_sl`
    bar-by-bar, with `r_basis` (the trade's `initial_sl_distance`) fixed for
    the trade's whole life so BE parking the SL near entry never collapses
    the R basis for later bars (Sprint 1 P0). `swing_level` is the precomputed
    M5 swing low/high visible at this bar (NaN/None when there is none yet).

    Only ever returns an SL at least as good as `current_sl` for the trade's
    side — a bar with no favourable trigger returns `current_sl` unchanged.
    """
    if r_basis <= 0:
        return current_sl
    favor = (close - entry) if buy else (entry - close)
    favor_r = favor / r_basis
    new_sl = current_sl

    # 1) Break-even lock (src.risk.manage_open_position parity).
    if favor >= break_even_r * r_basis:
        be_offset = max(
            be_lock_pips * spec.pip,
            atr * be_lock_atr_mult,
            spec.point,
        )
        be = entry + be_offset if buy else entry - be_offset
        if buy and current_sl < be:
            new_sl = be
        elif not buy and current_sl > be:
            new_sl = be

    # 2) Structure trail candidate (src.zones.structure_trail_sl parity). The
    # acceptance bound uses `current_sl` as it stood *before* this bar's BE
    # step, same as live: main.py computes struct_sl from pos.sl before
    # calling manage_open_position for the same poll.
    structure_candidate: float | None = None
    if favor_r >= trailing_start_r and swing_level is not None and swing_level == swing_level:
        sl_span = abs(entry - current_sl)
        if buy and swing_level > current_sl and swing_level < entry + favor_r * sl_span:
            structure_candidate = float(swing_level)
        elif not buy and (current_sl == 0 or swing_level < current_sl) and (
            swing_level > entry - favor_r * sl_span
        ):
            structure_candidate = float(swing_level)

    # 3) Structure wins outright over ATR when preferred and available — no
    # magnitude comparison, matching manage_open_position's if/elif.
    if prefer_structure_trail and structure_candidate is not None:
        if buy and structure_candidate > new_sl:
            new_sl = structure_candidate
        elif not buy and (new_sl == 0 or structure_candidate < new_sl):
            new_sl = structure_candidate
    elif favor_r >= trailing_start_r and atr > 0:
        trail = atr * trailing_atr_mult
        if buy:
            candidate = close - trail
            if candidate > new_sl:
                new_sl = candidate
        else:
            candidate = close + trail
            if candidate < new_sl:
                new_sl = candidate

    # 4) Optional staircase floor (off by default; see ReplayParams).
    if enable_staircase:
        floor = _staircase_floor(favor_r, r_basis, entry, buy, staircase_levels)
        if floor is not None:
            new_sl = max(new_sl, floor) if buy else min(new_sl, floor)

    return round(new_sl, spec.digits)


def _simulate_exit(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    atr_trail: np.ndarray,
    swing_low: np.ndarray,
    swing_high: np.ndarray,
    start: int,
    buy: bool,
    entry: float,
    sl: float,
    tp: float,
    initial_sl_distance: float,
    spec: SymbolSpec,
    max_hold_bars: int,
    p: ReplayParams,
) -> tuple[float, int, str]:
    """Bar-by-bar exit scan.

    Each bar first checks the *current* SL/TP (as they stood entering the
    bar) against that bar's high/low — a single bar spanning both still
    scores as SL, same ambiguity the static scan always had (module
    docstring). Only once neither is hit does the SL move, from that bar's
    close, via `_advance_sl` — so the SL that gets checked next bar is never
    the same bar's own close reacting to itself.

    When `p.enable_exit_management` is False this reduces to the original
    Sprint <3 static SL/TP-only scan.
    """
    last = min(start + max_hold_bars, len(highs) - 1)
    current_sl = sl
    r_basis = initial_sl_distance if initial_sl_distance > 0 else abs(entry - sl)

    for j in range(start, last + 1):
        hit_sl = lows[j] <= current_sl if buy else highs[j] >= current_sl
        hit_tp = highs[j] >= tp if buy else lows[j] <= tp
        if hit_sl:
            # Bar data cannot order intrabar touches; score the adverse one.
            return current_sl, j, "sl"
        if hit_tp:
            return tp, j, "tp"

        if p.enable_exit_management and r_basis > 0:
            atr_j = float(atr_trail[j]) if np.isfinite(atr_trail[j]) else 0.0
            swing_j = swing_low[j] if buy else swing_high[j]
            current_sl = _advance_sl(
                buy=buy,
                entry=entry,
                current_sl=current_sl,
                close=float(closes[j]),
                atr=atr_j,
                r_basis=r_basis,
                spec=spec,
                break_even_r=p.break_even_r,
                trailing_start_r=p.trailing_start_r,
                trailing_atr_mult=p.trailing_atr_mult,
                prefer_structure_trail=p.prefer_structure_trail,
                swing_level=swing_j,
                be_lock_pips=p.be_lock_pips,
                be_lock_atr_mult=p.be_lock_atr_mult,
                enable_staircase=p.enable_staircase,
                staircase_levels=p.staircase_levels,
            )

    return float(closes[last]), last, "timeout"


class ReplayEngine:
    def __init__(self, params: ReplayParams, provider: ReplayProvider) -> None:
        self.params = params
        self.provider = provider
        if params.use_virtual_account and provider.virtual_account is None:
            provider.virtual_account = VirtualAccount(
                params.starting_balance,
                leverage=float(getattr(provider, "_default_leverage", 3000.0)),
            )

    def run(self, symbol: str, frame: pd.DataFrame, bundle: ModelBundle) -> ReplayResult:
        p = self.params
        if p.apply_fusion_news:
            # Sprint 3 stub — see module docstring "Fusion parity". A hard
            # failure here is deliberate: silently ignoring the flag would let
            # a caller believe fusion/news ran when it did not.
            raise NotImplementedError(
                "ReplayParams.apply_fusion_news has no historical news/sentiment "
                "timeseries wired into this vectorised replay yet (see "
                "src.fusion.fuse_signal, src.news.sentiment.SentimentEngine). "
                "Keep it False until a news-aware replay path exists."
            )
        spec = self.provider.get_symbol_info(symbol)
        if spec is None:
            raise ValueError(f"ReplayProvider has no spec for {symbol}")

        result = ReplayResult(
            symbol=symbol, bars=len(frame), sl_band=sl_range(spec, p.sl_caps)
        )
        missing = [c for c in bundle.feature_cols if c not in frame.columns]
        if missing:
            raise ValueError(f"{symbol}: frame missing model features {missing[:5]}")

        probs = bundle.model.predict_proba(frame[bundle.feature_cols])
        classes = [int(c) for c in getattr(bundle.model, "classes_", [0, 1, 2])]
        p_buy = _class_column(probs, classes, 1)
        p_sell = _class_column(probs, classes, 2)

        # Structural ATR column: prefer configured TF (M5 default), fall back to M1.
        atr_tf = str(getattr(p, "atr_sl_timeframe", "M5") or "M5").lower()
        atr_col = f"{atr_tf}_atr_14"
        if atr_col in frame.columns:
            atr_series = frame[atr_col].fillna(frame["m1_atr_14"]).to_numpy(dtype=float)
        elif "m5_atr_14" in frame.columns:
            atr_series = frame["m5_atr_14"].fillna(frame["m1_atr_14"]).to_numpy(dtype=float)
        else:
            atr_series = frame["m1_atr_14"].to_numpy(dtype=float)
        zscore = frame["m1_atr_zscore"].to_numpy()
        highs = frame["high"].to_numpy(dtype=float)
        lows = frame["low"].to_numpy(dtype=float)
        closes = frame["close"].to_numpy()
        opens = frame["open"].to_numpy()
        times = frame["time"].to_numpy()

        # Sprint 3: M1 ATR for trailing (matches main.py's manage_positions,
        # which reads build_features' M1 atr_14) and precomputed M5 swing
        # levels for structure trailing — both O(1) array lookups per bar
        # inside _simulate_exit rather than recomputed per trade.
        atr_trail = (
            frame["m1_atr_14"].fillna(0.0).to_numpy(dtype=float)
            if "m1_atr_14" in frame.columns
            else np.zeros(len(frame), dtype=float)
        )
        swing_low_arr = (
            frame["m5_swing_low"].to_numpy(dtype=float)
            if "m5_swing_low" in frame.columns
            else np.full(len(frame), np.nan)
        )
        swing_high_arr = (
            frame["m5_swing_high"].to_numpy(dtype=float)
            if "m5_swing_high" in frame.columns
            else np.full(len(frame), np.nan)
        )

        # Sprint 4 #1: completed-bar tick volume >= SMA(volume, window). Fails
        # open (True) while there is not enough history for the SMA yet, same
        # as smc.check_fvg_volume.
        if p.require_fvg_volume and "volume" in frame.columns:
            vol = frame["volume"].astype(float)
            vol_sma = vol.rolling(p.volume_sma_window).mean()
            vol_ok_arr = (vol >= vol_sma).fillna(True).to_numpy()
        else:
            vol_ok_arr = np.ones(len(frame), dtype=bool)

        next_open_index = 0
        for i in range(len(frame) - 1):
            if i < next_open_index:
                continue  # I-04: one position at a time
            row = frame.iloc[i]

            # Sprint 3: reuse src.session_filter.in_session on the signal bar
            # (Sprint 4 #2's live gate) — cheapest possible check, same
            # priority position as main.py's manage_positions/build order.
            if p.session_filter_enabled and not in_session(
                symbol, pd.Timestamp(times[i]).to_pydatetime()
            ):
                result.rejects[str(Reject.SESSION)] += 1
                continue

            if abs(zscore[i]) > p.atr_zscore_max:
                result.rejects[str(Reject.ATR_ABNORMAL)] += 1
                continue

            bull = bool(row["bias_bull"])
            bear = bool(row["bias_bear"])
            if not (bull or bear):
                result.rejects[str(Reject.NEUTRAL_BIAS)] += 1
                continue

            side = "BUY" if bull else "SELL"
            sweep_ok = bool(row["sweep_bull"] if bull else row["sweep_bear"])
            choch_ok = bool(row["choch_bull"] if bull else row["choch_bear"])
            atr_i = float(atr_series[i]) if np.isfinite(atr_series[i]) else 0.0
            fvg = _fvg_entry(row, bull, atr_i, p.atr_sl_mult)
            vol_ok = bool(vol_ok_arr[i])
            hard_fvg_ok = fvg is not None and vol_ok
            aligned = (
                (sweep_ok and choch_ok and hard_fvg_ok)
                if p.require_full_mtf
                else hard_fvg_ok
            )
            if not aligned:
                result.rejects[str(Reject.MTF)] += 1
                continue

            candidate_entry = float(closes[i])
            sl_distance = abs(candidate_entry - fvg[1])
            result.sl_distances.append(sl_distance)

            prob = float(p_buy[i] if bull else p_sell[i])
            if prob < p.min_probability:
                result.rejects[str(Reject.CONFIDENCE)] += 1
                continue

            # Decision on bar i executes at bar i+1's open.
            self.provider.advance(symbol, float(opens[i + 1]), pd.Timestamp(times[i + 1]))
            tick = self.provider.get_tick(symbol)
            if tick is None:
                result.rejects[str(Reject.MARKET_DATA_UNAVAILABLE)] += 1
                continue

            ok_spread, spread_pts = is_spread_ok(spec, tick, p.max_spread_points)
            if not ok_spread:
                result.rejects[str(Reject.SPREAD)] += 1
                continue

            executable_price = tick.executable_price(side)
            drift_ok, drift = validate_entry_drift(
                candidate_entry, executable_price, sl_distance, p.max_entry_drift_r
            )
            if not drift_ok:
                result.rejects[str(Reject.ENTRY_DRIFT)] += 1
                continue

            capital = self.provider.account_capital()
            mm = resolve_mm(
                capital,
                sl_distance,
                spec,
                p.mm_cfg,
                fallback_lot=p.volume,
                fallback_max_risk_usd=p.max_risk_usd,
                side=side,
                entry_price=executable_price,
            )
            if not mm.ok:
                result.rejects[str(mm.code or Reject.PRE_SEND_RISK_BREACH)] += 1
                continue

            levels = build_trade_levels(
                spec=spec,
                side=side,
                executable_price=executable_price,
                structural_sl_distance=sl_distance,
                rr=p.rr,
                volume=mm.volume,
                max_risk_usd=mm.max_risk_usd,
                sl_caps=p.sl_caps,
            )
            if not levels.ok:
                result.rejects[str(levels.code)] += 1
                continue

            required = self.provider.order_calc_margin(
                symbol, side, levels.volume, levels.entry
            )
            if required is None:
                result.rejects[str(Reject.MARKET_DATA_UNAVAILABLE)] += 1
                continue
            margin = check_free_margin(required, self.provider.free_margin())
            if not margin.ok:
                result.rejects[str(Reject.NO_MONEY)] += 1
                continue

            exit_price, exit_idx, outcome = _simulate_exit(
                highs,
                lows,
                closes,
                atr_trail,
                swing_low_arr,
                swing_high_arr,
                i + 1,
                side == "BUY",
                executable_price,
                levels.sl,
                levels.tp,
                levels.sl_distance,
                spec,
                p.max_hold_bars,
                p,
            )
            signed = (exit_price - executable_price) if side == "BUY" else (executable_price - exit_price)
            r_multiple = signed / levels.sl_distance if levels.sl_distance else 0.0
            # Sprint 3: relabel by economic result rather than "which level was
            # mechanically hit" — a trailed SL can be hit at a profit (or at
            # exactly break-even), which the old literal "sl"/"tp"/"timeout"
            # labels did not distinguish. win_rate below counts outcome=="tp".
            outcome = "tp" if r_multiple > 0 else "sl" if r_multiple < 0 else "be"

            pnl = calc_risk_usd(spec, levels.volume, executable_price, exit_price)
            if pnl is None:
                pnl = 0.0
            else:
                # calc_risk_usd is absolute distance; restore sign from r_multiple.
                pnl = abs(pnl) * (1.0 if r_multiple >= 0 else -1.0)

            balance_after = None
            if p.track_compounding and self.provider.virtual_account is not None:
                balance_after = self.provider.virtual_account.apply_closed_trade(pnl)

            result.rejects[ACCEPTED] += 1
            result.trades.append(
                ReplayTrade(
                    symbol=symbol,
                    side=side,
                    opened_at=pd.Timestamp(times[i + 1]),
                    closed_at=pd.Timestamp(times[exit_idx]),
                    candidate_entry=candidate_entry,
                    executable_price=executable_price,
                    drift=drift,
                    sl=levels.sl,
                    tp=levels.tp,
                    exit_price=exit_price,
                    outcome=outcome,
                    r_multiple=round(r_multiple, 4),
                    risk_usd=round(levels.risk_usd, 4),
                    prob=round(prob, 4),
                    bars_held=exit_idx - i,
                    volume=levels.volume,
                    balance_after=balance_after,
                    pnl_usd=round(pnl, 4),
                )
            )
            next_open_index = exit_idx + 1

        return result


def _class_column(probs: np.ndarray, classes: list[int], target: int) -> np.ndarray:
    if target in classes:
        return probs[:, classes.index(target)]
    return np.zeros(len(probs))
