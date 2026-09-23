"""P0-11: the invariants that must hold for every accepted trade, not just the
examples chosen by hand.

Inputs are drawn from a seeded generator across all four symbols, so a violation
is reproducible from the printed case.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from src.decision_id import DecisionIdFactory
from src.reject_codes import ACCEPTED, BREAKER_CODES, Reject
from src.risk import build_trade_levels, calc_risk_usd, sl_range, validate_entry_drift
from tests.conftest import BTCUSD_SPEC, EURUSD_SPEC, SL_CAPS, USDJPY_SPEC, XAUUSD_SPEC

SPECS = {
    "EURUSD": (EURUSD_SPEC, 1.09000),
    "USDJPY": (USDJPY_SPEC, 150.000),
    "XAUUSD": (XAUUSD_SPEC, 2400.00),
    "BTCUSD": (BTCUSD_SPEC, 60000.00),
}

VOLUME = 0.01
MAX_RISK = 2.0
RR = 3.0
CASES = 400


def cases(seed: int = 20260816):
    """(symbol, spec, side, price, sl_distance) spanning inside and outside the band."""
    rng = random.Random(seed)
    for _ in range(CASES):
        symbol = rng.choice(list(SPECS))
        spec, base = SPECS[symbol]
        low, high = sl_range(spec, SL_CAPS[symbol])
        # Deliberately overshoot the band on both sides so rejects are exercised too.
        distance = rng.uniform(low * 0.2, high * 2.0)
        price = base * rng.uniform(0.98, 1.02)
        side = rng.choice(["BUY", "SELL"])
        yield symbol, spec, side, round(price, spec.digits), distance


def decide(spec, side, price, distance, caps):
    return build_trade_levels(
        spec=spec,
        side=side,
        executable_price=price,
        structural_sl_distance=distance,
        rr=RR,
        volume=VOLUME,
        max_risk_usd=MAX_RISK,
        sl_caps=caps,
    )


def test_volume_is_never_changed_by_the_risk_gate():
    """I-02: lot is fixed. The gate rejects; it never resizes."""
    for symbol, spec, side, price, distance in cases():
        decision = decide(spec, side, price, distance, SL_CAPS[symbol])
        assert decision.volume == VOLUME, (symbol, distance)


def test_accepted_trades_respect_the_dollar_ceiling():
    for symbol, spec, side, price, distance in cases():
        decision = decide(spec, side, price, distance, SL_CAPS[symbol])
        if decision.ok:
            assert decision.risk_usd <= MAX_RISK, (symbol, distance, decision.risk_usd)


def test_accepted_trades_keep_the_one_to_three_ratio():
    """I-03: measured on the rounded prices actually sent to the broker."""
    for symbol, spec, side, price, distance in cases():
        decision = decide(spec, side, price, distance, SL_CAPS[symbol])
        if not decision.ok:
            continue
        risk_leg = abs(decision.entry - decision.sl)
        reward_leg = abs(decision.tp - decision.entry)
        assert reward_leg == pytest.approx(RR * risk_leg, abs=0.51 * spec.point), (
            symbol,
            distance,
        )


def test_stop_and_target_sit_on_the_correct_sides():
    for symbol, spec, side, price, distance in cases():
        decision = decide(spec, side, price, distance, SL_CAPS[symbol])
        if not decision.ok:
            continue
        if side == "BUY":
            assert decision.sl < decision.entry < decision.tp, (symbol, distance)
        else:
            assert decision.tp < decision.entry < decision.sl, (symbol, distance)


def test_accepted_stop_distance_stays_inside_the_configured_band():
    for symbol, spec, side, price, distance in cases():
        decision = decide(spec, side, price, distance, SL_CAPS[symbol])
        if not decision.ok:
            continue
        low, high = sl_range(spec, SL_CAPS[symbol])
        # Rounding may move the distance by up to half a point.
        assert low - spec.point <= decision.sl_distance <= high + spec.point


def test_out_of_band_distances_are_rejected_never_adjusted():
    """G02: the rejected decision must not come back with a usable level."""
    rejected = 0
    for symbol, spec, side, price, distance in cases():
        low, high = sl_range(spec, SL_CAPS[symbol])
        if low <= distance <= high:
            continue
        decision = decide(spec, side, price, distance, SL_CAPS[symbol])
        rejected += 1
        assert not decision.ok
        assert decision.code == Reject.SL_RANGE
        assert decision.sl == 0.0 and decision.tp == 0.0
    assert rejected > 50  # the generator really did cover both sides of the band


def test_prices_are_rounded_to_the_symbol_digits():
    for symbol, spec, side, price, distance in cases():
        decision = decide(spec, side, price, distance, SL_CAPS[symbol])
        if not decision.ok:
            continue
        for value in (decision.entry, decision.sl, decision.tp):
            assert value == round(value, spec.digits), (symbol, value)


def test_every_decision_carries_a_known_code():
    known = {ACCEPTED} | {c.value for c in Reject}
    for symbol, spec, side, price, distance in cases():
        decision = decide(spec, side, price, distance, SL_CAPS[symbol])
        assert str(decision.code) in known


def test_drift_check_is_symmetric_around_the_candidate():
    rng = random.Random(11)
    for _ in range(300):
        distance = rng.uniform(0.0005, 0.0040)
        offset = rng.uniform(0, 0.0020)
        up, _ = validate_entry_drift(1.09, 1.09 + offset, distance, 0.25)
        down, _ = validate_entry_drift(1.09, 1.09 - offset, distance, 0.25)
        assert up == down


def test_risk_scales_linearly_with_stop_distance():
    for symbol, (spec, base) in SPECS.items():
        one = calc_risk_usd(spec, VOLUME, base, base - 10 * spec.point)
        two = calc_risk_usd(spec, VOLUME, base, base - 20 * spec.point)
        assert two == pytest.approx(2 * one, rel=1e-9), symbol


def test_decision_ids_are_unique_and_reset_each_server_day(isolated_state):
    factory = DecisionIdFactory(isolated_state["state"] / "decision_seq.json")
    start = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)
    ids = [
        factory.next_id(symbol, start + timedelta(seconds=i))
        for i in range(60)
        for symbol in ("EURUSD", "XAUUSD")
    ]
    assert len(set(ids)) == len(ids)
    assert ids[0].endswith("-00001")
    assert ids[0].startswith("20260817-090000-EURUSD")

    next_day = factory.next_id("EURUSD", start + timedelta(days=1))
    assert next_day.startswith("20260818-")
    assert next_day.endswith("-00001")


def test_decision_ids_stay_unique_across_a_restart(isolated_state):
    """A restart mid-day must not reissue ids already written to the logs."""
    path = isolated_state["state"] / "decision_seq.json"
    now = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)
    first = [DecisionIdFactory(path).next_id("EURUSD", now) for _ in range(5)]
    assert len(set(first)) == 5
    assert first[-1].endswith("-00005")


def test_breaker_codes_are_a_subset_of_the_reject_vocabulary():
    assert BREAKER_CODES <= set(Reject)
