"""Settlement-rule helpers for Polymarket 5-minute crypto up/down markets (analysis use, not the frozen bot).

Measured on 672 real windows (scripts/twap_window_check.py): a 60-second TWAP at BOTH ends reproduces the
resolved outcome 96.6% of the time from Binance 1s closes, versus 90.2% for "TWAP at expiry vs point-price
strike" and 83.0% for point-in-time. Live market rules name the Chainlink btc-usd-twap-60s stream.

    strike     = average close over [t0 - 60, t0)
    settlement = average close over [t1 - 60, t1),  t1 = t0 + 300
    Up wins when settlement >= strike.

`closes` is {unix_second: close_price}. Binance is only a proxy for Chainlink, so expect a small residual
disagreement (about 3% overall, about 10% when the move is under 3 bps).
"""
from typing import Dict, Optional, Tuple

WINDOW_SEC = 300
TWAP_SEC = 60
MIN_COVERAGE = 0.8


def avg_close(closes: Dict[int, float], start: int, end: int, min_coverage: float = MIN_COVERAGE) -> Optional[float]:
    """Mean close over seconds [start, end); None if fewer than min_coverage of the seconds are present."""
    xs = [closes[t] for t in range(start, end) if t in closes]
    return sum(xs) / len(xs) if len(xs) >= (end - start) * min_coverage else None


def strike_twap(closes: Dict[int, float], t0: int, twap_sec: int = TWAP_SEC) -> Optional[float]:
    return avg_close(closes, t0 - twap_sec, t0)


def settlement_twap(closes: Dict[int, float], t0: int, window_sec: int = WINDOW_SEC, twap_sec: int = TWAP_SEC) -> Optional[float]:
    t1 = t0 + window_sec
    return avg_close(closes, t1 - twap_sec, t1)


def estimate_outcome(closes: Dict[int, float], t0: int, window_sec: int = WINDOW_SEC,
                     twap_sec: int = TWAP_SEC) -> Optional[Tuple[bool, float]]:
    """(up_wins, |move| in bps) under the 60s/60s rule, or None when data coverage is too thin."""
    strike = strike_twap(closes, t0, twap_sec)
    final = settlement_twap(closes, t0, window_sec, twap_sec)
    if strike is None or final is None or strike <= 0:
        return None
    return final >= strike, abs(final / strike - 1.0) * 1e4


def is_near_strike(move_bps: float, threshold_bps: float = 3.0) -> bool:
    """Windows this close to the strike are where a Binance proxy is least reliable."""
    return move_bps < threshold_bps
