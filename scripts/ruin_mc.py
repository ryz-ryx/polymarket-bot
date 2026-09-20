"""Monte Carlo for the goal '$50 -> $500 within 12h' with zero edge and fee-only cost.

Each trade: return r ~ N(0, 0.3%), minus 0.24% round-trip cost, multiplied by leverage, all-in. 72 trades (one per 10 min).
Ruin = equity <= 5% of start. Success = reach 10x. Not a model of any real strategy: it shows the odds of the goal itself.
"""
import numpy as np

rng = np.random.default_rng(3)
N, TRADES, SD, COST = 20000, 72, 0.003, 0.0024
print("lev  P(reach 10x)  P(ruin)  median end equity ($50 start)")
for L in (1, 5, 10, 20):
    eq = np.full(N, 50.0)
    done = np.zeros(N, bool)
    ruined = np.zeros(N, bool)
    for _ in range(TRADES):
        r = rng.normal(0, SD, N) - COST
        eq = np.where(done | ruined, eq, eq * np.maximum(1 + L * r, 0))
        ruined |= eq <= 2.5
        done |= eq >= 500
    print(f"{L:>3}  {done.mean():>11.4f}  {ruined.mean():>7.3f}  {np.median(eq):>8.2f}")
