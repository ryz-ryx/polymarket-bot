"""Complete-set parity tracker: YES ask + NO ask < $1.00 (buy both, redeem $1 at settlement).

Fees: taker fee per share is FEE_RATE * p * (1 - p) on each leg (crypto feeRate 0.07). Gas and
merge costs are ignored, so `net` is an upper bound. An "episode" is a contiguous stretch
where gross edge (1 - ya - na) > 0; it is emitted when the edge closes or on flush().
"""
from typing import Dict, Optional

FEE_RATE = 0.07


def leg_fee(price: float) -> float:
    return FEE_RATE * price * (1.0 - price)


def pair_edge(yes_ask: float, no_ask: float):
    gross = 1.0 - yes_ask - no_ask
    net = gross - leg_fee(yes_ask) - leg_fee(no_ask)
    return gross, net


class ArbTracker:
    def __init__(self) -> None:
        self._open: Dict[int, dict] = {}

    def update(self, window: int, ts: float, yes_ask, no_ask) -> Optional[dict]:
        """yes_ask/no_ask are (price, size) tuples or None. Returns a closed episode or None."""
        ep = self._open.get(window)
        gross = net = None
        shares = 0.0
        if yes_ask and no_ask:
            gross, net = pair_edge(yes_ask[0], no_ask[0])
            shares = min(yes_ask[1], no_ask[1])
        if gross is not None and gross > 0:
            if ep is None:
                self._open[window] = {"w": window, "start": ts, "end": ts, "gross": gross,
                                      "net": net, "shares": shares}
            else:
                ep["end"] = ts
                ep["gross"] = max(ep["gross"], gross)
                ep["net"] = max(ep["net"], net)
                ep["shares"] = max(ep["shares"], shares)
            return None
        if ep is not None:
            return self._close(window, ts)
        return None

    def _close(self, window: int, ts: float) -> dict:
        ep = self._open.pop(window)
        ep["end"] = ts
        ep["dur"] = round(ep["end"] - ep["start"], 3)
        return ep

    def flush(self, ts: float):
        return [self._close(w, ts) for w in list(self._open)]
