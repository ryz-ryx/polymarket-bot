import random

from scripts.hist_tests import cluster_ci, grid_prices, mean, pnl_by_wallet_window, spearman, t2

W = 300


def _t(ts, wal, side, idx, p, sz, tx="aaaaaaaa"):
    return (ts, wal, side, idx, p, sz, tx)


def test_pnl_is_zero_sum_and_resolves_correctly():
    trades = {W: [_t(310, "A", "B", 0, 0.60, 100), _t(310, "B", "S", 0, 0.60, 100)]}
    pnl = pnl_by_wallet_window(trades, {W: 1})          # Up wins: A gains 40, B loses 40
    assert abs(pnl[("A", W)] - 40.0) < 1e-9 and abs(pnl[("B", W)] + 40.0) < 1e-9
    pnl = pnl_by_wallet_window(trades, {W: 0})          # Down wins
    assert abs(pnl[("A", W)] + 60.0) < 1e-9 and abs(sum(pnl.values())) < 1e-9


def test_no_token_yes_equivalent_price():
    trades = [_t(305, "A", "B", 1, 0.30, 10), _t(320, "B", "B", 0, 0.65, 10)]
    g = grid_prices(trades, W)
    assert abs(g[305] - 0.70) < 1e-9 and abs(g[330] - 0.65) < 1e-9


def _window(prices_after):
    base = [_t(W + 5, "A", "B", 0, 0.50, 10, "t0")]
    return base + [_t(W + 40 + i, "A", "S", 0, p, 5, f"t{i + 1}") for i, p in enumerate(prices_after)]


def test_t2_requires_strictly_through_print():
    # bid posted at t=W+30 is 0.50-0.02 = 0.48; a print AT 0.48 must not fill, one at 0.47 must.
    at = {W: _window([0.48, 0.50, 0.50])}
    thr = {W: _window([0.47, 0.50, 0.50])}
    assert t2(at, split_w=0)["fills"] == 0
    assert t2(thr, split_w=0)["fills"] >= 1


def test_cluster_ci_and_spearman_basics():
    rng = random.Random(0)
    data = {w: [rng.gauss(0.1, 0.05) for _ in range(5)] for w in range(30)}
    point, lo, hi = cluster_ci(data, mean)
    assert lo is not None and lo > 0
    assert spearman([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]) > 0.99
    assert spearman([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]) < -0.99
