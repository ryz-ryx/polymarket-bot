"""Two-sided maker quoter (simulation/shadow use; posts nothing by itself).

Fair value is only a defence against being picked off, not a directional bet: it anchors the
quote and drives cancel-on-spot-move. Quotes are bids on one token; the opposite side is quoted
as a bid on the other token (buying NO at q is selling YES at 1-q), so both tokens use this.
"""
import math
from dataclasses import dataclass
from typing import Optional

SECONDS_PER_YEAR = 365.0 * 24 * 3600


@dataclass
class QuoterParams:
    half_spread: float = 0.02
    skew_per_share: float = 0.001
    size: float = 5.0
    max_inv: float = 20.0
    min_tau: float = 20.0
    max_tau: float = 285.0
    tick: float = 0.01
    min_price: float = 0.03
    max_price: float = 0.97
    pull_move_bps: float = 3.0


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def fair_prob_up(spot: float, strike: float, tau_s: float, vol_ann: float) -> float:
    """P(spot_T >= strike) under driftless lognormal. Ignores the 60s TWAP settlement."""
    if tau_s <= 0 or vol_ann <= 0 or spot <= 0 or strike <= 0:
        return 1.0 if spot >= strike else 0.0
    sd = vol_ann * math.sqrt(tau_s / SECONDS_PER_YEAR)
    return norm_cdf(math.log(spot / strike) / sd)


def quote_bid(fair: float, inventory: float, tau_s: float, p: QuoterParams) -> Optional[dict]:
    """Bid price/size on a token whose fair value is `fair`; `inventory` = shares held of it."""
    if tau_s < p.min_tau or tau_s > p.max_tau or inventory >= p.max_inv:
        return None
    raw = fair - p.half_spread - p.skew_per_share * inventory
    price = math.floor(raw / p.tick + 1e-9) * p.tick
    price = round(price, 4)
    if price < p.min_price or price > p.max_price:
        return None
    return {"price": price, "size": min(p.size, p.max_inv - inventory)}


def should_pull(spot_now: float, spot_before: float, p: QuoterParams) -> bool:
    """Cancel-on-move: True if spot moved more than pull_move_bps over the lookback."""
    if spot_before <= 0:
        return False
    return abs(spot_now - spot_before) / spot_before * 1e4 > p.pull_move_bps
