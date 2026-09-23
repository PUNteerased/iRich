"""Sprint 3 (iRich Profit Upgrade v1.4): replay exit simulation parity with
live position management.

Before this sprint `_simulate_exit` only ever checked the *static* SL/TP set
at open. These tests pin the upgrade: break-even lock using
`initial_sl_distance` as a fixed R basis (never zero after BE, Sprint 1 P0),
structure-preferred-over-ATR trailing, the optional staircase floor, and the
session filter now wired into replay entries (Sprint 4 #2's live gate).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.backtest.replay import (
    ReplayEngine,
    ReplayParams,
    _advance_sl,
    _simulate_exit,
    _staircase_floor,
)
from src.market_data import ReplayProvider
from src.model_runner import ModelBundle
from src.reject_codes import ACCEPTED, Reject

STAIRCASE_LEVELS = ((3.0, 1.0), (4.0, 2.0), (5.0, 3.0))


class _FakeBuyModel:
    """Always scores BUY with a high, gate-clearing probability."""

    classes_ = [0, 1, 2]

    def predict_proba(self, X):
        n = len(X)
        return np.tile(np.array([0.05, 0.90, 0.05]), (n, 1))


def _bundle() -> ModelBundle:
    return ModelBundle(_FakeBuyModel(), feature_cols=["close"])


def _entry_frame(signal_hour: int) -> pd.DataFrame:
    """4-bar synthetic frame with exactly one bullish candidate at index 1.

    Only the columns `ReplayEngine.run` actually reads for a BUY candidate
    are populated — bear_top/bear_bottom/sweep_bear/choch_bear are never
    touched because `bull` is True at the candidate row (see replay.py's
    ternaries), so they are deliberately omitted.
    """
    base = pd.Timestamp("2026-01-05", tz="UTC")  # a Monday
    times = [base + pd.Timedelta(hours=signal_hour, minutes=m) for m in range(4)]
    return pd.DataFrame(
        {
            "time": times,
            "open": [1.09995, 1.10000, 1.10000, 1.10005],
            "high": [1.10000, 1.10005, 1.10010, 1.10010],
            "low": [1.09990, 1.09995, 1.09995, 1.09995],
            "close": [1.09998, 1.10000, 1.10005, 1.10002],
            "bias_bull": [0.0, 1.0, 0.0, 0.0],
            "bias_bear": [0.0, 0.0, 0.0, 0.0],
            "sweep_bull": [0.0, 1.0, 0.0, 0.0],
            "sweep_bear": [0.0, 0.0, 0.0, 0.0],
            "choch_bull": [0.0, 1.0, 0.0, 0.0],
            "choch_bear": [0.0, 0.0, 0.0, 0.0],
            "bull_top": [np.nan, 1.10010, np.nan, np.nan],
            "bull_bottom": [np.nan, 1.09990, np.nan, np.nan],
            "m1_atr_14": [0.0010, 0.0012, 0.0010, 0.0010],
            "m1_atr_zscore": [0.0, 0.0, 0.0, 0.0],
        }
    )


def _run_entry_scenario(spec, signal_hour: int, session_filter_enabled: bool):
    frame = _entry_frame(signal_hour)
    provider = ReplayProvider(specs={"EURUSD": spec}, spread_points={"EURUSD": 1.0})
    params = ReplayParams(
        sl_caps={"min_pips": 10, "max_pips": 15},
        max_spread_points=5.0,
        enable_exit_management=False,  # isolate the entry-side session gate
        session_filter_enabled=session_filter_enabled,
    )
    return ReplayEngine(params, provider).run("EURUSD", frame, _bundle())


class TestSessionFilterEntryGate:
    """Sprint 3: src.session_filter.in_session now gates replay entries too."""

    def test_outside_session_is_rejected_when_enabled(self, spec):
        """All 4 bars share hour=2 (outside EURUSD's 07:00-16:00 UTC window),
        so every one of the 3 signal-loop iterations is gated by session —
        not just the row that would otherwise have been a candidate."""
        result = _run_entry_scenario(spec, signal_hour=2, session_filter_enabled=True)
        assert len(result.trades) == 0
        assert result.rejects[str(Reject.SESSION)] == 3

    def test_inside_session_is_accepted_when_enabled(self, spec):
        result = _run_entry_scenario(spec, signal_hour=10, session_filter_enabled=True)
        assert len(result.trades) == 1
        assert result.rejects.get(str(Reject.SESSION), 0) == 0
        assert result.rejects[ACCEPTED] == 1

    def test_outside_session_is_accepted_when_disabled(self, spec):
        """Default is opt-in — existing replay scripts must not silently
        change trade counts just because this flag now exists."""
        result = _run_entry_scenario(spec, signal_hour=2, session_filter_enabled=False)
        assert len(result.trades) == 1
        assert result.rejects.get(str(Reject.SESSION), 0) == 0


class TestApplyFusionNewsStub:
    def test_apply_fusion_news_true_raises_instead_of_silently_approximating(self, spec):
        frame = _entry_frame(signal_hour=10)
        provider = ReplayProvider(specs={"EURUSD": spec}, spread_points={"EURUSD": 1.0})
        params = ReplayParams(apply_fusion_news=True)
        with pytest.raises(NotImplementedError):
            ReplayEngine(params, provider).run("EURUSD", frame, _bundle())


class TestAdvanceSl:
    """Unit-level coverage of the per-bar SL update `_simulate_exit` drives."""

    def _kwargs(self, spec, **overrides):
        base = dict(
            buy=True,
            entry=1.09000,
            current_sl=1.08880,  # initial_sl_distance == 0.00120
            close=1.09150,
            atr=0.0,
            r_basis=0.00120,
            spec=spec,
            break_even_r=1.0,
            trailing_start_r=1.5,
            trailing_atr_mult=1.0,
            prefer_structure_trail=False,
            swing_level=None,
            be_lock_pips=2.0,
            be_lock_atr_mult=0.0,
            enable_staircase=False,
            staircase_levels=STAIRCASE_LEVELS,
        )
        base.update(overrides)
        return base

    def test_be_locks_past_entry_not_exactly_on_it(self, spec):
        """Sprint 1 #2 parity: favor >= break_even_r*r_basis parks the SL
        `be_lock_pips` into profit, never exactly at price_open."""
        new_sl = _advance_sl(**self._kwargs(spec, close=1.09150))  # favor=0.00150 >= 0.0012
        assert new_sl > 1.09000
        assert new_sl == pytest.approx(1.09000 + 0.0002)  # 2 pips on 5-digit EURUSD

    def test_trail_continues_after_be_using_initial_sl_distance(self, spec):
        """Sprint 1 P0 parity: once BE has parked the SL near entry,
        |price_open - sl| collapses toward 0 — r_basis (initial_sl_distance)
        must still be the R denominator so a further favourable close keeps
        proposing a trail instead of going inert."""
        after_be = _advance_sl(**self._kwargs(spec, current_sl=1.09020, close=1.09150))
        after_trail = _advance_sl(
            **self._kwargs(
                spec,
                current_sl=after_be,
                close=1.09300,  # favor_r = 0.00300/0.00120 = 2.5 >= trailing_start_r
                atr=0.0005,
            )
        )
        assert after_trail > after_be
        assert after_trail == pytest.approx(1.09300 - 0.0005)

    def test_zero_r_basis_returns_current_sl_unchanged(self, spec):
        """Documents the old bug's failure mode: without a positive R basis,
        _advance_sl must not invent a move."""
        new_sl = _advance_sl(**self._kwargs(spec, r_basis=0.0, close=1.09500))
        assert new_sl == 1.08880

    def test_structure_wins_over_atr_when_preferred_even_if_atr_is_larger(self, spec):
        kwargs = self._kwargs(
            spec,
            current_sl=1.08880,
            close=1.09300,  # favor_r = 0.00300/0.00120 = 2.5
            atr=0.0006,  # ATR trail would propose close-atr = 1.09240
            swing_level=1.09100,  # structure candidate, smaller than the ATR trail
        )
        structure_sl = _advance_sl(**{**kwargs, "prefer_structure_trail": True})
        atr_sl = _advance_sl(**{**kwargs, "prefer_structure_trail": False})

        assert structure_sl == pytest.approx(1.09100)
        assert atr_sl == pytest.approx(1.09300 - 0.0006)
        assert atr_sl > structure_sl  # ATR would trail further; structure still wins

    def test_structure_candidate_ignored_when_it_fails_the_bound_check(self, spec):
        """zones.structure_trail_sl parity: a swing level that is not between
        current_sl and entry + favor_r*sl_span is not a valid candidate, and
        the ATR fallback takes over even with prefer_structure_trail=True."""
        kwargs = self._kwargs(
            spec,
            current_sl=1.08880,
            close=1.09300,
            atr=0.0006,
            swing_level=1.09999,  # far beyond the bound -> rejected
            prefer_structure_trail=True,
        )
        new_sl = _advance_sl(**kwargs)
        assert new_sl == pytest.approx(1.09300 - 0.0006)  # ATR fallback fired

    def test_nan_swing_level_is_treated_as_no_candidate(self, spec):
        kwargs = self._kwargs(
            spec,
            current_sl=1.08880,
            close=1.09300,
            atr=0.0006,
            swing_level=float("nan"),
            prefer_structure_trail=True,
        )
        new_sl = _advance_sl(**kwargs)
        assert new_sl == pytest.approx(1.09300 - 0.0006)

    def test_staircase_floors_sl_at_3r_4r_5r_and_never_loosens(self, spec):
        entry = 1.09000
        r_basis = 0.00100
        kwargs = self._kwargs(
            spec,
            entry=entry,
            r_basis=r_basis,
            atr=0.0,
            enable_staircase=True,
        )
        # 0.01R past each trigger — favor/r_basis landing exactly on 3.0/4.5/5.0
        # is float-boundary-flaky (0.003/0.001 is not bit-exact 3.0); a small
        # margin keeps the trigger comparison unambiguous.
        sl_at_3r = _advance_sl(**{**kwargs, "close": entry + 3.01 * r_basis})
        assert sl_at_3r == pytest.approx(entry + 1.0 * r_basis)

        sl_at_45r = _advance_sl(**{**kwargs, "current_sl": sl_at_3r, "close": entry + 4.51 * r_basis})
        assert sl_at_45r == pytest.approx(entry + 2.0 * r_basis)

        sl_at_5r = _advance_sl(**{**kwargs, "current_sl": sl_at_45r, "close": entry + 5.01 * r_basis})
        assert sl_at_5r == pytest.approx(entry + 3.0 * r_basis)

        # A pullback bar must never walk the already-locked floor back down.
        sl_after_pullback = _advance_sl(
            **{**kwargs, "current_sl": sl_at_5r, "close": entry + 0.5 * r_basis}
        )
        assert sl_after_pullback == pytest.approx(sl_at_5r)

    def test_staircase_off_never_floors(self, spec):
        entry = 1.09000
        r_basis = 0.00100
        new_sl = _advance_sl(
            **self._kwargs(
                spec,
                entry=entry,
                r_basis=r_basis,
                current_sl=1.09020,
                close=entry + 5.0 * r_basis,
                atr=0.0,
                enable_staircase=False,
            )
        )
        # No trail source active (prefer_structure_trail False, atr==0), no
        # staircase -> BE offset only, nowhere near the 3R floor.
        assert new_sl < entry + 1.0 * r_basis


class TestStaircaseFloorHelper:
    def test_returns_none_below_first_trigger(self):
        assert _staircase_floor(2.9, 0.0010, 1.09000, True, STAIRCASE_LEVELS) is None

    def test_buy_side_locks_progressively_higher(self):
        entry = 1.09000
        r_basis = 0.0010
        assert _staircase_floor(3.0, r_basis, entry, True, STAIRCASE_LEVELS) == pytest.approx(
            entry + 1.0 * r_basis
        )
        assert _staircase_floor(4.5, r_basis, entry, True, STAIRCASE_LEVELS) == pytest.approx(
            entry + 2.0 * r_basis
        )
        assert _staircase_floor(5.0, r_basis, entry, True, STAIRCASE_LEVELS) == pytest.approx(
            entry + 3.0 * r_basis
        )

    def test_sell_side_mirrors_downward(self):
        entry = 1.09000
        r_basis = 0.0010
        assert _staircase_floor(5.0, r_basis, entry, False, STAIRCASE_LEVELS) == pytest.approx(
            entry - 3.0 * r_basis
        )


class TestSimulateExit:
    """`_simulate_exit` end-to-end: BE then trail, exit at the *trailed*
    level (not the original static SL) once price reverses onto it."""

    def test_be_then_trail_and_final_hit_is_at_trailed_sl_not_original(self, spec):
        entry = 1.09000
        sl = 1.08880  # initial_sl_distance = 0.00120
        tp = 1.09360  # far away; never reached in this scenario
        initial_sl_distance = 0.00120

        highs = np.array([1.09160, 1.09260, 1.09150])
        lows = np.array([1.09100, 1.09150, 1.09050])  # bar2 dips onto the trailed SL
        closes = np.array([1.09150, 1.09250, 1.09100])
        atr_trail = np.array([0.0004, 0.0005, 0.0005])
        swing_low = np.full(3, np.nan)
        swing_high = np.full(3, np.nan)

        params = ReplayParams(
            enable_exit_management=True,
            break_even_r=1.0,
            trailing_start_r=1.5,
            trailing_atr_mult=1.0,
            prefer_structure_trail=False,
            be_lock_pips=2.0,
            be_lock_atr_mult=0.0,
            max_hold_bars=10,
        )

        exit_price, exit_idx, outcome = _simulate_exit(
            highs,
            lows,
            closes,
            atr_trail,
            swing_low,
            swing_high,
            0,
            True,
            entry,
            sl,
            tp,
            initial_sl_distance,
            spec,
            params.max_hold_bars,
            params,
        )

        assert outcome == "sl"
        assert exit_idx == 2
        # bar0: favor 0.00150 >= 1.0*0.0012 -> BE to 1.09020.
        # bar1: favor_r ~2.08 >= 1.5 -> ATR trail to close-atr = 1.09200.
        # bar2: low 1.09050 hits the *trailed* SL, not the original 1.08880.
        assert exit_price == pytest.approx(1.09200)
        assert exit_price > sl  # proves this is not the old static -1R exit

    def test_static_scan_unchanged_when_exit_management_disabled(self, spec):
        """Backward-compat: enable_exit_management=False reduces to the
        original Sprint <3 static SL/TP-only scan."""
        entry = 1.09000
        sl = 1.08880
        tp = 1.09360
        highs = np.array([1.09160, 1.09260, 1.09150])
        lows = np.array([1.09100, 1.09150, 1.09050])  # would hit the trailed SL, not the static one
        closes = np.array([1.09150, 1.09250, 1.09100])
        atr_trail = np.array([0.0004, 0.0005, 0.0005])
        swing_low = np.full(3, np.nan)
        swing_high = np.full(3, np.nan)

        params = ReplayParams(enable_exit_management=False, max_hold_bars=10)
        exit_price, exit_idx, outcome = _simulate_exit(
            highs, lows, closes, atr_trail, swing_low, swing_high,
            0, True, entry, sl, tp, 0.00120, spec, params.max_hold_bars, params,
        )
        # Static SL (1.08880) is never touched by these bars, and the SL
        # never moves with management disabled -> timeout at the last
        # simulated bar, priced at that bar's close.
        assert outcome == "timeout"
        assert exit_idx == 2
        assert exit_price == pytest.approx(float(closes[2]))
