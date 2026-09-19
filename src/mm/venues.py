"""Exchange price feeds for the L2 collector: parsers and a per-venue throttle (observation only).

Each parser takes one decoded JSON message and returns (bid, ask, server_ts_epoch_or_None), or None when the
message is not a usable quote. Formats verified live 2026-09-19 from the deployment host:
  binance  bookTicker: {"u":..,"s":"BTCUSDT","b":"81436.00","B":"..","a":"81436.01","A":".."}      (no server ts)
  coinbase ticker:     {"type":"ticker","product_id":"BTC-USD","best_bid":"..","best_ask":"..","time":"<ISO>"}
  kraken   v2 ticker:  {"channel":"ticker","type":"update|snapshot","data":[{"symbol":"BTC/USD","bid":..,"ask":..,"timestamp":"<ISO>"}]}
"""
import datetime
from typing import Callable, Dict, Optional, Tuple

Quote = Tuple[float, float, Optional[float]]


def _iso_to_epoch(s) -> Optional[float]:
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def parse_binance(m) -> Optional[Quote]:
    try:
        bid, ask = float(m["b"]), float(m["a"])
    except (KeyError, TypeError, ValueError):
        return None
    return (bid, ask, None) if bid > 0 and ask >= bid else None


def parse_coinbase(m) -> Optional[Quote]:
    if not isinstance(m, dict) or m.get("type") != "ticker":
        return None
    try:
        bid, ask = float(m["best_bid"]), float(m["best_ask"])
    except (KeyError, TypeError, ValueError):
        return None
    return (bid, ask, _iso_to_epoch(m.get("time"))) if bid > 0 and ask >= bid else None


def parse_kraken(m) -> Optional[Quote]:
    if not isinstance(m, dict) or m.get("channel") != "ticker" or m.get("type") not in ("update", "snapshot"):
        return None
    try:
        d = m["data"][0]
        bid, ask = float(d["bid"]), float(d["ask"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return (bid, ask, _iso_to_epoch(d.get("timestamp"))) if bid > 0 and ask >= bid else None


VENUES: Dict[str, dict] = {
    "binance": {"url": "wss://stream.binance.com:9443/ws/btcusdt@bookTicker", "sub": None, "parse": parse_binance},
    "coinbase": {"url": "wss://ws-feed.exchange.coinbase.com",
                 "sub": {"type": "subscribe", "product_ids": ["BTC-USD"], "channels": ["ticker"]}, "parse": parse_coinbase},
    "kraken": {"url": "wss://ws.kraken.com/v2",
               "sub": {"method": "subscribe", "params": {"channel": "ticker", "symbol": ["BTC/USD"]}}, "parse": parse_kraken},
}


class Throttle:
    """Pass a quote only if it changed and at least `min_interval` seconds passed since the last one passed."""

    def __init__(self, min_interval: float = 0.25) -> None:
        self.min_interval = min_interval
        self._last_ts = -1e18
        self._last_quote: Optional[Tuple[float, float]] = None

    def allow(self, ts: float, bid: float, ask: float) -> bool:
        if (bid, ask) == self._last_quote or ts - self._last_ts < self.min_interval:
            return False
        self._last_ts, self._last_quote = ts, (bid, ask)
        return True
