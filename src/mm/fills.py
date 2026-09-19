"""Conservative fill model for a resting bid, used by the maker replay.

A resting bid at price b is filled only by taker SELL prints. A print strictly below b fills us
(up to remaining size). A print exactly at b first consumes the queue that was ahead of us when
we joined, then fills us. Prints above b never fill us. Repricing resets the queue position.
"""
from dataclasses import dataclass


@dataclass
class RestingBid:
    price: float
    size: float
    queue_ahead: float
    filled: float = 0.0

    @property
    def remaining(self) -> float:
        return self.size - self.filled

    def on_trade(self, trade_price: float, trade_size: float, taker_side: str) -> float:
        """Shares of our bid filled by this print (0 if none)."""
        if taker_side.upper() not in ("S", "SELL") or self.remaining <= 1e-9:
            return 0.0
        if trade_price > self.price + 1e-9:
            return 0.0
        avail = trade_size
        if abs(trade_price - self.price) <= 1e-9:
            take = min(avail, self.queue_ahead)
            self.queue_ahead -= take
            avail -= take
        got = min(avail, self.remaining)
        self.filled += got
        return got
