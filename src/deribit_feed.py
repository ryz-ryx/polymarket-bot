import asyncio
import time
import aiohttp
from typing import Optional, Tuple
from loguru import logger

class DeribitFeed:
    """
    Asynchronous feed for Deribit public market data:
    1. Deribit Index Price (e.g. 'btc_usd', 'eth_usd'): Multi-exchange composite reference price.
    2. Deribit Volatility Index ('DVOL'): Market-implied 30-day annualized volatility.

    Used as an external benchmark against Binance spot and as a Bayesian prior
    for short-horizon realized volatility.
    """
    def __init__(self, currency: str = "BTC", index_poll_interval: float = 2.0, dvol_poll_interval: float = 60.0):
        self.currency = currency.upper()
        self.index_poll_interval = index_poll_interval
        self.dvol_poll_interval = dvol_poll_interval

        self.index_price: Optional[float] = None
        self.index_timestamp: float = 0.0

        # Annualized decimal volatility (e.g., 0.3945 for 39.45% DVOL)
        self.dvol_annualized: Optional[float] = None
        self.dvol_timestamp: float = 0.0

        self.is_running: bool = False
        self._session: Optional[aiohttp.ClientSession] = None
        self._headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    async def start(self):
        if self.currency not in ("BTC", "ETH"):
            # Deribit index / DVOL not supported for this asset (e.g. SOL)
            return
        self.is_running = True
        self._session = aiohttp.ClientSession(headers=self._headers)
        logger.info(f"Starting Deribit public market data feed for {self.currency} (Index + DVOL)...")
        asyncio.create_task(self._poll_index_loop())
        asyncio.create_task(self._poll_dvol_loop())

    async def stop(self):
        self.is_running = False
        if self._session and not self._session.closed:
            await self._session.close()

    async def _poll_index_loop(self):
        index_name = f"{self.currency.lower()}_usd"
        url = f"https://www.deribit.com/api/v2/public/get_index_price?index_name={index_name}"
        while self.is_running:
            try:
                if self._session and not self._session.closed:
                    async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            result = data.get("result", {})
                            idx = result.get("index_price")
                            if idx is not None:
                                self.index_price = float(idx)
                                self.index_timestamp = time.time()
            except Exception as e:
                logger.debug(f"Deribit index price poll notice ({self.currency}): {e}")

            await asyncio.sleep(self.index_poll_interval)

    async def _poll_dvol_loop(self):
        """
        Polls the Deribit Volatility Index (DVOL) for the asset.
        CRITICAL: Deribit returns DVOL as percentage points (e.g. 39.45 for 39.45%).
        We divide by 100.0 so that self.dvol_annualized matches the decimal scale
        (0.30 - 2.50) used across our pricing models.
        """
        while self.is_running:
            try:
                now_ms = int(time.time() * 1000)
                start_ms = now_ms - (120 * 1000)  # query last 2 minutes
                url = (
                    f"https://www.deribit.com/api/v2/public/get_volatility_index_data"
                    f"?currency={self.currency}&start_timestamp={start_ms}&end_timestamp={now_ms}&resolution=60"
                )
                if self._session and not self._session.closed:
                    async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            result = data.get("result", {})
                            points = result.get("data", [])
                            if points and isinstance(points, list):
                                latest_point = points[-1]
                                # format: [timestamp, open, high, low, close]
                                close_val = latest_point[4]
                                if close_val is not None:
                                    raw_pct = float(close_val)
                                    # Normalize percentage to decimal
                                    self.dvol_annualized = raw_pct / 100.0
                                    self.dvol_timestamp = time.time()
                                    logger.debug(f"Deribit DVOL updated: {raw_pct:.2f}% -> {self.dvol_annualized:.4f}")
            except Exception as e:
                logger.debug(f"Deribit DVOL poll notice: {e}")

            await asyncio.sleep(self.dvol_poll_interval)

    def get_composite_index(self) -> Optional[float]:
        # Return index if fresh within 15 seconds
        if self.index_price is not None and (time.time() - self.index_timestamp) < 15.0:
            return self.index_price
        return None

    def get_dvol(self) -> Optional[float]:
        # Return DVOL if fresh within 300 seconds (5 mins)
        if self.dvol_annualized is not None and (time.time() - self.dvol_timestamp) < 300.0:
            return self.dvol_annualized
        return None
