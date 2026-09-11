import asyncio
import time
import aiohttp
from typing import Optional
from loguru import logger

class CoinbaseFeed:
    """
    Lightweight asynchronous feed for Coinbase public spot prices.
    Serves as an independent secondary reference price / basis check,
    especially for assets like SOL where Deribit index is unavailable.
    """
    def __init__(self, currency: str = "SOL", poll_interval: float = 2.0):
        self.currency = currency.upper()
        self.poll_interval = poll_interval

        self.spot_price: Optional[float] = None
        self.spot_timestamp: float = 0.0

        self.is_running: bool = False
        self._session: Optional[aiohttp.ClientSession] = None
        self._headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    async def start(self):
        self.is_running = True
        self._session = aiohttp.ClientSession(headers=self._headers)
        logger.info(f"Starting Coinbase secondary spot feed for {self.currency}...")
        asyncio.create_task(self._poll_spot_loop())

    async def stop(self):
        self.is_running = False
        if self._session and not self._session.closed:
            await self._session.close()

    async def _poll_spot_loop(self):
        url = f"https://api.coinbase.com/v2/prices/{self.currency}-USD/spot"
        while self.is_running:
            try:
                if self._session and not self._session.closed:
                    async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            amount = data.get("data", {}).get("amount")
                            if amount is not None:
                                self.spot_price = float(amount)
                                self.spot_timestamp = time.time()
            except Exception as e:
                logger.debug(f"Coinbase spot poll notice ({self.currency}): {e}")

            await asyncio.sleep(self.poll_interval)

    def get_composite_index(self) -> Optional[float]:
        """
        Returns spot price if fresh within 15 seconds (matching DeribitFeed interface).
        """
        if self.spot_price is not None and (time.time() - self.spot_timestamp) < 15.0:
            return self.spot_price
        return None

    def get_dvol(self) -> Optional[float]:
        return None
