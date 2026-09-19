from src.resolution import avg_close, estimate_outcome, is_near_strike, settlement_twap, strike_twap

T0 = 1000


def _series(fn, start=T0 - 70, end=T0 + 310):
    return {t: fn(t) for t in range(start, end)}


def test_flat_series_is_up_by_the_ge_rule_with_zero_move():
    up, bps = estimate_outcome(_series(lambda t: 100.0), T0)
    assert up is True and bps == 0.0            # equal averages resolve Up (>=)


def test_rising_and_falling_series():
    up, bps = estimate_outcome(_series(lambda t: 100.0 + 0.01 * (t - T0)), T0)
    assert up is True and bps > 0
    dn, _ = estimate_outcome(_series(lambda t: 100.0 - 0.01 * (t - T0)), T0)
    assert dn is False


def test_uses_twap_at_both_ends_not_a_point_strike():
    # price spikes exactly at t0 but the prior minute averaged 100: the TWAP strike stays near 100.
    closes = _series(lambda t: 100.0)
    closes[T0] = 110.0
    assert abs(strike_twap(closes, T0) - 100.0) < 1e-9
    assert abs(settlement_twap(closes, T0) - 100.0) < 1e-9


def test_thin_coverage_returns_none():
    closes = _series(lambda t: 100.0)
    for t in range(T0 - 60, T0 - 20):           # remove 2/3 of the strike window
        closes.pop(t)
    assert estimate_outcome(closes, T0) is None
    assert avg_close({}, 0, 10) is None


def test_near_strike_flag():
    assert is_near_strike(2.9) and not is_near_strike(3.0)
