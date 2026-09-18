import pytest

from src.strategies.claud_quant import (
    ClaudQuantBinaryOptionStrategy,
    estimate_taker_fee_fraction,
)


def test_fee_curve_highest_at_cheap_prices():
    assert estimate_taker_fee_fraction(0.1) > estimate_taker_fee_fraction(0.5) > estimate_taker_fee_fraction(0.9)


def test_fee_clamped_at_boundaries():
    assert estimate_taker_fee_fraction(0.0) == pytest.approx(0.07 * 0.999)
    assert estimate_taker_fee_fraction(1.5) == pytest.approx(0.07 * 0.001)


def test_fair_prob_at_the_money_is_near_half():
    s = ClaudQuantBinaryOptionStrategy()
    p, _ = s.calculate_fair_probability(S_t=100.0, K=100.0, tau_seconds=120, annualized_vol=0.6)
    assert 0.45 < p < 0.55


def test_fair_prob_monotonic_in_spot():
    s = ClaudQuantBinaryOptionStrategy()
    lo, _ = s.calculate_fair_probability(S_t=99.9, K=100.0, tau_seconds=120, annualized_vol=0.6)
    hi, _ = s.calculate_fair_probability(S_t=100.1, K=100.0, tau_seconds=120, annualized_vol=0.6)
    assert lo < hi


def test_expired_window_resolves_deterministically():
    s = ClaudQuantBinaryOptionStrategy()
    assert s.calculate_fair_probability(101.0, 100.0, 0.0, 0.6)[0] == 1.0
    assert s.calculate_fair_probability(99.0, 100.0, 0.0, 0.6)[0] == 0.0


def test_positive_drift_raises_probability():
    s = ClaudQuantBinaryOptionStrategy()
    base, _ = s.calculate_fair_probability(100.0, 100.0, 120, 0.6)
    up, _ = s.calculate_fair_probability(100.0, 100.0, 120, 0.6, cbi_normalized=1.0)
    assert up > base
