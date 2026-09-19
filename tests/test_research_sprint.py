import random

from scripts.research_sprint import blend, bucket_returns, bucket_test, fit_w, shrink_test


def _rows(n, informative, seed=1):
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        p_mkt = rng.uniform(0.2, 0.8)
        y = 1 if rng.random() < p_mkt else 0
        p_model = (0.9 * y + 0.05) if informative else rng.uniform(0.1, 0.9)
        rows.append({"timestamp": i, "p_model": p_model, "p_market": p_mkt, "y": y})
    return rows


def test_blend_endpoints():
    assert blend(0, [0.9], [0.4]) == [0.4]
    assert blend(1, [0.9], [0.4]) == [0.9]


def test_fit_w_prefers_market_when_model_is_noise():
    rows = _rows(3000, informative=False)
    w = fit_w([r["p_model"] for r in rows], [r["p_market"] for r in rows], [r["y"] for r in rows])
    assert w <= 0.15


def test_a_kills_noise_and_survives_leaky_model():
    assert shrink_test(_rows(3000, informative=False))["survives"] is False
    assert shrink_test(_rows(3000, informative=True))["survives"] is True


def test_bucket_returns_fair_market_loses_only_fees():
    yes_ret, no_ret = bucket_returns(0.5, True)
    assert yes_ret > 0 and no_ret < 0
    rng = random.Random(3)
    ws = [(i, 0.5, 1 if rng.random() < 0.5 else 0) for i in range(6000)]
    assert not any(c["survives"] for c in bucket_test(ws))
