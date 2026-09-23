"""Sprint 4 (iRich Profit Upgrade v1.4): soft-MTF + volume gate, session
filter, and the online_confidence opt-out.

Replay showed ~0 FX fills because require_full_mtf AND-stacks H1 bias, M15
sweep, M5 ChoCH and M1 FVG. These tests pin the fix at the unit level:

* soft-MTF (require_full_mtf=False) must still hard-require H1 bias + M1 FVG,
  but sweep/choch move from a hard reject to a `soft_score` bonus.
* the FVG-bar volume gate is hard regardless of require_full_mtf.
* the session filter rejects entries outside a symbol's configured UTC window.
* `learning.online_confidence: false` must not let ConfidenceStore raise the
  effective threshold above the fixed `sniper.min_probability`.

`evaluate_mtf`'s detectors are monkeypatched rather than reproduced with real
OHLC fixtures — this isolates the hard/soft gate-combining logic itself
(already covered against lookahead by test_smc_no_lookahead.py) from having
to also construct a bar series that reliably trips sweep/choch/fvg.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from src import mtf as mtf_module
from src.fusion import fuse_signal
from src.reject_codes import Reject
from src.session_filter import in_session
from src.smc import StructureSignal, check_fvg_volume


def _dummy_df(n: int = 60) -> pd.DataFrame:
    """Minimal OHLCV frame; evaluate_mtf's detectors are stubbed below so its
    actual price content never drives the assertions."""
    return pd.DataFrame(
        {
            "time": pd.date_range("2026-01-01", periods=n, freq="1min", tz="UTC"),
            "open": [1.10] * n,
            "high": [1.11] * n,
            "low": [1.09] * n,
            "close": [1.10] * n,
            "tick_volume": [100.0] * n,
        }
    )


def _stub_mtf(
    monkeypatch,
    bias: str = "BULLISH",
    sweep_ok: bool = False,
    choch_ok: bool = False,
    fvg_ok: bool = True,
    volume_ok: bool = True,
) -> None:
    monkeypatch.setattr(mtf_module, "get_h1_bias", lambda df_h1, ema_len=200: bias)
    monkeypatch.setattr(
        mtf_module,
        "detect_liquidity_sweep",
        lambda df, b, **kw: StructureSignal(sweep_ok, b, reason="sweep"),
    )
    monkeypatch.setattr(
        mtf_module,
        "detect_choch",
        lambda df, b, **kw: StructureSignal(choch_ok, b, reason="choch"),
    )
    monkeypatch.setattr(
        mtf_module,
        "detect_fvg_entry",
        lambda df, b, atr=None, atr_sl_mult=1.0: StructureSignal(
            fvg_ok, b, level=1.10, sl=1.09, reason="fvg"
        ),
    )
    monkeypatch.setattr(mtf_module, "check_fvg_volume", lambda df, window=20: volume_ok)
    monkeypatch.setattr(mtf_module, "_completed_atr", lambda df: 0.0010)


class TestSoftMtf:
    def test_full_mtf_hard_rejects_missing_sweep_or_choch(self, monkeypatch):
        _stub_mtf(monkeypatch, sweep_ok=False, choch_ok=False, fvg_ok=True)
        df = _dummy_df()
        result = mtf_module.evaluate_mtf(df, df, df, df, require_full=True)
        assert not result.aligned
        assert result.entry is None

    def test_soft_mtf_allows_bias_plus_fvg_without_sweep_or_choch(self, monkeypatch):
        """The Sprint 4 fix: with require_full_mtf False, a missing sweep/choch
        no longer blocks the candidate — only H1 bias + M1 FVG (+ volume) do."""
        _stub_mtf(monkeypatch, sweep_ok=False, choch_ok=False, fvg_ok=True)
        df = _dummy_df()
        result = mtf_module.evaluate_mtf(df, df, df, df, require_full=False)
        assert result.aligned
        assert result.entry is not None
        assert result.sl is not None
        assert result.soft_score == pytest.approx(0.0)  # no bonus: neither confirmed

    def test_soft_mtf_still_rejects_without_fvg(self, monkeypatch):
        _stub_mtf(monkeypatch, sweep_ok=True, choch_ok=True, fvg_ok=False)
        df = _dummy_df()
        result = mtf_module.evaluate_mtf(df, df, df, df, require_full=False)
        assert not result.aligned

    def test_soft_mtf_adds_bonus_when_sweep_and_choch_confirm(self, monkeypatch):
        _stub_mtf(monkeypatch, sweep_ok=True, choch_ok=True, fvg_ok=True)
        df = _dummy_df()
        result = mtf_module.evaluate_mtf(
            df,
            df,
            df,
            df,
            require_full=False,
            soft_sweep_bonus=0.05,
            soft_choch_bonus=0.07,
        )
        assert result.aligned
        assert result.soft_score == pytest.approx(0.12)

    def test_full_mtf_never_carries_a_soft_bonus(self, monkeypatch):
        """soft_score must stay 0 in AND-stack mode even when sweep/choch pass —
        it is only meaningful as a fusion bonus for the soft path."""
        _stub_mtf(monkeypatch, sweep_ok=True, choch_ok=True, fvg_ok=True)
        df = _dummy_df()
        result = mtf_module.evaluate_mtf(df, df, df, df, require_full=True)
        assert result.aligned
        assert result.soft_score == pytest.approx(0.0)


class TestVolumeGate:
    def test_hard_volume_gate_rejects_low_volume_even_with_fvg(self, monkeypatch):
        _stub_mtf(monkeypatch, fvg_ok=True, volume_ok=False)
        df = _dummy_df()
        result = mtf_module.evaluate_mtf(
            df, df, df, df, require_full=False, require_fvg_volume=True
        )
        assert not result.aligned
        assert not result.volume_ok
        assert "fvg_volume_fail" in result.reasons

    def test_hard_volume_gate_applies_in_full_mtf_mode_too(self, monkeypatch):
        _stub_mtf(monkeypatch, sweep_ok=True, choch_ok=True, fvg_ok=True, volume_ok=False)
        df = _dummy_df()
        result = mtf_module.evaluate_mtf(
            df, df, df, df, require_full=True, require_fvg_volume=True
        )
        assert not result.aligned

    def test_volume_gate_passes_when_volume_confirms(self, monkeypatch):
        _stub_mtf(monkeypatch, fvg_ok=True, volume_ok=True)
        df = _dummy_df()
        result = mtf_module.evaluate_mtf(
            df, df, df, df, require_full=False, require_fvg_volume=True
        )
        assert result.aligned

    def test_volume_gate_disabled_ignores_low_volume(self, monkeypatch):
        _stub_mtf(monkeypatch, fvg_ok=True, volume_ok=False)
        df = _dummy_df()
        result = mtf_module.evaluate_mtf(
            df, df, df, df, require_full=False, require_fvg_volume=False
        )
        assert result.aligned

    def test_check_fvg_volume_rejects_below_sma(self):
        """Completed FVG bar (df.iloc[-2]) volume must clear SMA(volume, window)."""
        n = 25
        df = pd.DataFrame({"tick_volume": [100.0] * n})
        df.loc[df.index[-2], "tick_volume"] = 5.0  # completed bar: thin volume
        assert check_fvg_volume(df, window=20) is False

    def test_check_fvg_volume_passes_at_or_above_sma(self):
        n = 25
        df = pd.DataFrame({"tick_volume": [100.0] * n})
        df.loc[df.index[-2], "tick_volume"] = 300.0  # completed bar: strong volume
        assert check_fvg_volume(df, window=20) is True

    def test_check_fvg_volume_fails_open_on_short_history(self):
        df = pd.DataFrame({"tick_volume": [1.0] * 10})
        assert check_fvg_volume(df, window=20) is True


class TestSessionFilter:
    @pytest.mark.parametrize(
        "symbol,hour,minute,expected",
        [
            ("EURUSD", 7, 0, True),
            ("EURUSD", 15, 59, True),
            ("EURUSD", 16, 0, False),
            ("EURUSD", 6, 59, False),
            ("USDJPY", 7, 0, True),
            ("USDJPY", 16, 0, False),
            ("XAUUSD", 12, 0, True),
            ("XAUUSD", 19, 59, True),
            ("XAUUSD", 20, 0, False),
            ("XAUUSD", 11, 59, False),
            ("BTCUSD", 8, 0, True),
            ("BTCUSD", 21, 59, True),
            ("BTCUSD", 22, 0, False),
            ("BTCUSD", 7, 59, False),
        ],
    )
    def test_session_windows(self, symbol, hour, minute, expected):
        dt = datetime(2026, 1, 5, hour, minute, tzinfo=timezone.utc)
        assert in_session(symbol, dt) is expected

    def test_lowercase_symbol_matches(self):
        dt = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
        assert in_session("eurusd", dt) is True

    def test_unconfigured_symbol_is_not_filtered(self):
        dt = datetime(2026, 1, 5, 3, 0, tzinfo=timezone.utc)
        assert in_session("GBPUSD", dt) is True


class TestOnlineConfidenceFlag:
    """learning.online_confidence: false must pin the threshold at
    sniper.min_probability, ignoring any ConfidenceStore raise from losses."""

    def test_online_confidence_true_raises_threshold_after_losses(self, confidence):
        confidence.update("EURUSD", "sl")  # weight -> 0.8 (w_min=0.3 floor not hit)
        decision = fuse_signal(
            symbol="EURUSD",
            side="BUY",
            model_prob=0.80,
            min_prob=0.75,
            news_blocked=False,
            news_block_reason="",
            breaker_code=None,
            has_open_position=False,
            mtf_aligned=True,
            mtf_reasons=[],
            usd_sentiment=0.0,
            confidence=confidence,
            online_confidence=True,
        )
        assert decision.threshold > 0.75
        assert decision.signal == "WAIT"
        assert decision.code == Reject.CONFIDENCE

    def test_online_confidence_false_ignores_store_raise(self, confidence):
        confidence.update("EURUSD", "sl")  # would raise the threshold if honoured
        decision = fuse_signal(
            symbol="EURUSD",
            side="BUY",
            model_prob=0.80,
            min_prob=0.75,
            news_blocked=False,
            news_block_reason="",
            breaker_code=None,
            has_open_position=False,
            mtf_aligned=True,
            mtf_reasons=[],
            usd_sentiment=0.0,
            confidence=confidence,
            online_confidence=False,
        )
        assert decision.threshold == pytest.approx(0.75)
        assert decision.signal == "BUY"

    def test_online_confidence_defaults_to_true_for_backward_compat(self, confidence):
        confidence.update("EURUSD", "sl")
        decision = fuse_signal(
            symbol="EURUSD",
            side="BUY",
            model_prob=0.80,
            min_prob=0.75,
            news_blocked=False,
            news_block_reason="",
            breaker_code=None,
            has_open_position=False,
            mtf_aligned=True,
            mtf_reasons=[],
            usd_sentiment=0.0,
            confidence=confidence,
        )
        assert decision.threshold > 0.75


class TestSoftScoreFusionBonus:
    """Sprint 4 #1: the soft-MTF sweep/choch bonus lands in the fusion score,
    never in the hard alignment gate."""

    def _decision(self, confidence, mtf_soft_score: float):
        return fuse_signal(
            symbol="EURUSD",
            side="BUY",
            model_prob=0.80,
            min_prob=0.75,
            news_blocked=False,
            news_block_reason="",
            breaker_code=None,
            has_open_position=False,
            mtf_aligned=True,
            mtf_reasons=[],
            usd_sentiment=0.5,  # EURUSD news_bias = -0.5 (mild, not a contradiction)
            confidence=confidence,
            tech_weight=0.3,
            news_weight=0.7,
            online_confidence=False,
            mtf_soft_score=mtf_soft_score,
        )

    def test_without_bonus_marginal_buy_is_rejected(self, confidence):
        decision = self._decision(confidence, mtf_soft_score=0.0)
        assert decision.signal == "WAIT"
        assert decision.code == Reject.FUSION_SCORE

    def test_bonus_tips_the_same_candidate_into_a_buy(self, confidence):
        decision = self._decision(confidence, mtf_soft_score=0.15)
        assert decision.signal == "BUY"
