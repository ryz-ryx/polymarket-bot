"""Local order book rebuilt from Polymarket CLOB market-channel messages.

Snapshots ("book" events) replace the book; "price_change" entries set the size at one
price level (size 0 removes it). side BUY = bid, SELL = ask. Prices/sizes arrive as strings.
"""
from typing import Dict, List, Optional, Tuple

Level = Tuple[float, float]


class LocalBook:
    def __init__(self) -> None:
        self.bids: Dict[float, float] = {}
        self.asks: Dict[float, float] = {}

    def apply_snapshot(self, bids, asks) -> None:
        self.bids = self._levels(bids)
        self.asks = self._levels(asks)

    @staticmethod
    def _levels(levels) -> Dict[float, float]:
        out: Dict[float, float] = {}
        for lv in levels or []:
            try:
                p, s = float(lv["price"]), float(lv["size"])
            except (KeyError, TypeError, ValueError):
                continue
            if s > 0:
                out[p] = s
        return out

    def apply_change(self, side: str, price, size) -> None:
        try:
            p, s = float(price), float(size)
        except (TypeError, ValueError):
            return
        book = self.bids if str(side).upper() == "BUY" else self.asks
        if s <= 0:
            book.pop(p, None)
        else:
            book[p] = s

    def best_bid(self) -> Optional[Level]:
        return (max(self.bids), self.bids[max(self.bids)]) if self.bids else None

    def best_ask(self) -> Optional[Level]:
        return (min(self.asks), self.asks[min(self.asks)]) if self.asks else None

    def top(self, n: int = 3) -> Tuple[List[List[float]], List[List[float]]]:
        bids = [[p, self.bids[p]] for p in sorted(self.bids, reverse=True)[:n]]
        asks = [[p, self.asks[p]] for p in sorted(self.asks)[:n]]
        return bids, asks
