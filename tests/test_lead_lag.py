import math
import random

from scripts.lead_lag_test import Index, c1_points, c2_trades, cluster_boot, slope


def _ticks(lag_s, n_windows=40, seed=5):
    """Synthetic 1s ticks: spot random walk; YES mid follows spot with `lag_s` delay (or is noise)."""
    rng = random.Random(seed)
    rows, spot = [], 100.0
    for w in range(n_windows):
        base = 300.0 * (w + 10)
        hist = []
        for s in range(300):
            spot *= math.exp(rng.gauss(0, 0.0004))
            hist.append(spot)
            src = hist[max(s - lag_s, 0)] if lag_s is not None else 100.0 * math.exp(rng.gauss(0, 0.0004))
            mid = min(max(0.5 + (src / 100.0 - 1.0) * 40, 0.2), 0.8)
            rows.append({"ts": base + s, "window_id": w, "spot": spot, "strike": 100.0,
                         "yes_bid": mid - 0.005, "yes_ask": mid + 0.005, "no_bid": 1 - mid - 0.005, "no_ask": 1 - mid + 0.005})
    return rows


def test_c1_detects_planted_lag():
    pts = c1_points(Index(_ticks(lag_s=4)))
    s, lo, hi = cluster_boot(pts, slope)
    assert s > 0 and lo > 0


def test_c1_no_lag_no_pass():
    pts = c1_points(Index(_ticks(lag_s=None)))
    s, lo, hi = cluster_boot(pts, slope)
    assert not (lo is not None and lo > 0)


def test_c2_produces_trades_and_fees_reduce_net():
    idx = Index(_ticks(lag_s=4))
    with_fee = c2_trades(idx)
    gross = c2_trades(idx, fees=False)
    assert with_fee and len(with_fee) == len(gross)
    assert sum(x[1] for x in with_fee) < sum(x[1] for x in gross)
