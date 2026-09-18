import random

from scripts.daily_scorecard import futility_checks


def _fills(n, win_prob, seed=1, price=0.5, stake=1.0):
    rng = random.Random(seed)
    fills = []
    for i in range(n):
        fills.append({"asset": "BTC", "type": "BUY", "window_id": i, "ts": 1000.0 + i * 10,
                      "price": price, "size_usd": stake})
        fills.append({"asset": "BTC", "type": "WIN" if rng.random() < win_prob else "LOSS",
                      "window_id": i, "ts": 1005.0 + i * 10})
    return fills


def test_look_not_reached_below_60():
    lines = futility_checks(_fills(30, 0.5), since_ts=0)
    assert all("not reached" in l for l in lines)


def test_clear_loser_is_killed_at_60():
    lines = futility_checks(_fills(60, 0.15), since_ts=0)  # ~15% wins at 0.50 entry
    assert "KILL" in lines[0] and "not reached" in lines[1]


def test_break_even_run_continues():
    lines = futility_checks(_fills(60, 0.5), since_ts=0)
    assert "continue" in lines[0]
