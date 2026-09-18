import pytest

from src.risk_manager import RiskManager


def make(**kw):
    return RiskManager(max_position_usd=5.0, max_daily_loss_usd=10.0, kelly_fraction=0.25, **kw)


def test_no_edge_returns_zero():
    assert make().calculate_position_size(win_probability=0.4, odds=2.0, bankroll=100) == 0.0


def test_size_capped_at_max_position():
    assert make().calculate_position_size(0.9, 2.0, bankroll=10_000) == 5.0


def test_confidence_weight_scales_size_down():
    full = make().calculate_position_size(0.6, 2.0, 100, confidence_weight=1.0)
    half = make().calculate_position_size(0.6, 2.0, 100, confidence_weight=0.5)
    assert 0 < half < full


def test_zero_confidence_gives_zero_size():
    assert make().calculate_position_size(0.6, 2.0, 100, confidence_weight=0.0) == 0.0


def test_breaker_trips_at_daily_loss():
    rm = make()
    rm.daily_pnl = -10.0
    assert rm.can_trade() is False
    assert rm.circuit_breaker_triggered is True
    assert rm.calculate_position_size(0.9, 2.0, 100) == 0.0
