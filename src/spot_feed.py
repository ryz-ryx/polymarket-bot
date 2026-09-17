import asyncio
import json
import time
import math
from typing import Callable, Optional, Deque
from collections import deque
import websockets
import aiohttp
from loguru import logger

SECONDS_PER_YEAR = 365.25 * 24 * 3600

class SpotFeed:
    """
    Sub-second real-time spot price listener via Binance combined stream.
    Includes REST fallback to fetch the exact window open strike K on startup or mid-window restart.
    """
    def __init__(self, symbol: str = "BTCUSDT", on_price_update: Optional[Callable[[float, float], None]] = None):
        self.symbol = symbol.lower()
        self.ws_url = f"wss://stream.binance.com:9443/stream?streams={self.symbol}@bookTicker/{self.symbol}@trade"
        self.latest_price: Optional[float] = None
        self.best_bid: Optional[float] = None
        self.best_ask: Optional[float] = None
        self.best_bid_qty: Optional[float] = None
        self.best_ask_qty: Optional[float] = None
        # Stoikov microprice: size-weighted mid that leans toward whichever side has LESS
        # resting size (the side more likely to get run through next tick). Used only for
        # the strategy's pricing input (S_t) -- realized vol, momentum, TWAP settlement
        # estimation, and window bookkeeping all intentionally keep using the plain mid,
        # since microprice is a short-horizon directional signal, not a "true price" proxy.
        self.microprice: Optional[float] = None
        self.latest_timestamp: float = 0.0
        
        # maxlen was 60 (structurally capped every get_momentum(lookback_seconds=X) call at
        # ~60s regardless of X requested). Raised to 220 to support the 180s lookback validated
        # 2026-09-17 against 45,667 real historical BTC 5m windows (Binance 1m klines + real
        # Polymarket resolutions, not live-only data): momentum measured at a 3-min lookback
        # scored Brier 0.1669 alone / 0.1279 combined with market price (out-of-sample,
        # logistic regression, 70/30 split) vs 0.1797 for market price alone -- the 10s lookback
        # actually in use structurally couldn't have captured this, the buffer never held enough
        # history to try.
        self.second_buckets: Deque[tuple[int, float]] = deque(maxlen=220)
        self.last_bucket_second: int = 0
        
        self.trade_flow: Deque[tuple[float, float]] = deque()
        self.annualized_vol: float = 0.65
        
        self.on_price_update = on_price_update
        self.is_running = False

    async def get_exact_window_open_price(self, window_ts: int) -> Optional[float]:
        """
        Fetches the exact open price for a 5-minute candle starting at window_ts.
        Prevents mid-window restart strike price drift.
        
        NOTE: Binance's interval=5m klines are UTC calendar-aligned (00:00, 00:05, 00:10...),
        which strictly coincides with the bot's window_ts = floor(time() / 300) * 300 boundaries.
        If window alignment logic is ever modified, this query must be updated accordingly.
        """
        start_ms = window_ts * 1000
        url = f"https://api.binance.com/api/v3/klines?symbol={self.symbol.upper()}&interval=5m&startTime={start_ms}&limit=1"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=4) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and isinstance(data, list):
                            open_p = float(data[0][1])
                            logger.info(f"Binance REST: Recovered exact historical strike K for window {window_ts}: ${open_p:.2f}")
                            return open_p
        except Exception as e:
            logger.debug(f"Error recovering historical window open price: {e}")
        return None

    async def start(self):
        self.is_running = True
        while self.is_running:
            try:
                logger.info(f"Connecting to Binance combined stream: {self.ws_url}")
                async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=10) as ws:
                    logger.info(f"Connected to Binance combined feed for {self.symbol}")
                    while self.is_running:
                        msg = await ws.recv()
                        raw_msg = json.loads(msg)
                        stream = raw_msg.get("stream", "")
                        data = raw_msg.get("data", {})

                        if "bookTicker" in stream:
                            self.best_bid = float(data["b"])
                            self.best_ask = float(data["a"])
                            self.best_bid_qty = float(data.get("B", 0.0))
                            self.best_ask_qty = float(data.get("A", 0.0))
                            mid_price = (self.best_bid + self.best_ask) / 2.0
                            ts = time.time()
                            self.latest_price = mid_price
                            self.latest_timestamp = ts

                            total_qty = self.best_bid_qty + self.best_ask_qty
                            if total_qty > 1e-9:
                                self.microprice = (
                                    (self.best_bid * self.best_ask_qty) + (self.best_ask * self.best_bid_qty)
                                ) / total_qty
                            else:
                                self.microprice = mid_price

                            current_sec = int(ts)
                            if current_sec > self.last_bucket_second:
                                self.second_buckets.append((current_sec, mid_price))
                                self.last_bucket_second = current_sec
                                self._recompute_realized_vol()

                            if self.on_price_update:
                                self.on_price_update(mid_price, ts)

                        elif "trade" in stream:
                            qty = float(data.get("q", 0.0))
                            is_buyer_maker = data.get("m", False)
                            ts = float(data.get("T", time.time() * 1000)) / 1000.0
                            signed_flow = -qty if is_buyer_maker else qty
                            self.trade_flow.append((ts, signed_flow))

                            cutoff = ts - 30.0
                            while self.trade_flow and self.trade_flow[0][0] < cutoff:
                                self.trade_flow.popleft()

            except Exception as e:
                logger.warning(f"Binance combined feed error: {e}. Reconnecting in 2s...")
                await asyncio.sleep(2)

    def _recompute_realized_vol(self):
        if len(self.second_buckets) < 10:
            return

        prices = [p for _, p in self.second_buckets]
        log_returns = []
        for i in range(1, len(prices)):
            if prices[i - 1] > 0:
                log_returns.append(math.log(prices[i] / prices[i - 1]))

        if len(log_returns) < 5:
            return

        mean_r = sum(log_returns) / len(log_returns)
        var_1s = sum((r - mean_r) ** 2 for r in log_returns) / (len(log_returns) - 1)
        
        raw_ann_vol = math.sqrt(var_1s * SECONDS_PER_YEAR)
        clamped_vol = max(min(raw_ann_vol, 2.50), 0.30)
        self.annualized_vol = 0.85 * self.annualized_vol + 0.15 * clamped_vol

    def get_ofi_normalized(self) -> float:
        if not self.trade_flow:
            return 0.0
        total_vol = sum(abs(flow) for _, flow in self.trade_flow)
        if total_vol <= 1e-6:
            return 0.0
        net_flow = sum(flow for _, flow in self.trade_flow)
        return max(min(net_flow / total_vol, 1.0), -1.0)

    def get_momentum(self, lookback_seconds: float = 10.0) -> float:
        if len(self.second_buckets) < 2:
            return 0.0
        target_sec = self.latest_timestamp - lookback_seconds
        current_price = self.second_buckets[-1][1]
        for sec, p in reversed(self.second_buckets):
            if sec <= target_sec:
                return current_price - p
        return current_price - self.second_buckets[0][1]

    def get_regime_factor(self, lookback_seconds: float = 60.0, min_factor: float = 0.4, max_factor: float = 1.6) -> float:
        """
        Variance-ratio regime detector: distinguishes trending markets (where recent
        momentum is informative) from mean-reverting chop (where momentum is noise or
        even contrarian). Uses the classic Lo-MacKinlay variance ratio at lag 2:
            VR(2) = Var(2-period log returns) / (2 * Var(1-period log returns))
        VR > 1 implies positive serial correlation (trending) -> momentum signal should
        be trusted more. VR < 1 implies negative serial correlation (mean-reverting) ->
        momentum should be dampened, since a recent move is more likely to snap back.
        Returns 1.0 (neutral, no adjustment) when there isn't enough tick history yet.
        """
        now_sec = self.second_buckets[-1][0] if self.second_buckets else None
        if now_sec is None:
            return 1.0
        window = [p for sec, p in self.second_buckets if sec > now_sec - lookback_seconds]
        if len(window) < 12:
            return 1.0

        one_step_returns = []
        for i in range(1, len(window)):
            if window[i - 1] > 0:
                one_step_returns.append(math.log(window[i] / window[i - 1]))
        if len(one_step_returns) < 10:
            return 1.0

        two_step_returns = []
        for i in range(2, len(window)):
            if window[i - 2] > 0:
                two_step_returns.append(math.log(window[i] / window[i - 2]))
        if len(two_step_returns) < 5:
            return 1.0

        def _variance(xs):
            m = sum(xs) / len(xs)
            return sum((x - m) ** 2 for x in xs) / max(len(xs) - 1, 1)

        var_1 = _variance(one_step_returns)
        var_2 = _variance(two_step_returns)
        if var_1 <= 1e-12:
            return 1.0

        vr = var_2 / (2.0 * var_1)
        return max(min(vr, max_factor), min_factor)

    def get_trailing_twap(self, seconds: float) -> Optional[float]:
        """
        Trailing time-weighted average price over the last `seconds` seconds,
        computed from the 1s-bucketed price history (second_buckets).

        Added because Polymarket's crypto Up/Down markets settle via a
        Chainlink TWAP Data Stream, NOT a single instantaneous price snapshot
        (as of the Aug 2026 Chainlink TWAP upgrade). Public reporting is
        inconsistent on the exact window (30s vs 60s), so we compute both
        30s and 60s trailing TWAPs and log them alongside the instantaneous
        snapshot price for every window; comparing all three against the
        confirmed on-chain outcome over many windows lets us empirically
        determine which window length (if either) actually matches
        Polymarket's real settlement, instead of guessing from secondhand
        blog posts.
        """
        if not self.second_buckets:
            return None
        now_sec = self.second_buckets[-1][0]
        window = [p for sec, p in self.second_buckets if sec > now_sec - seconds]
        if not window:
            return None
        return sum(window) / len(window)

    def stop(self):
        self.is_running = False
