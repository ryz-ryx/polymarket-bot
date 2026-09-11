import asyncio
import time
import json
import aiohttp
from typing import Dict, Any, Optional, Tuple
from loguru import logger

class MultiAssetResearchFeed:
    """
    Research-only feed for correlated 5-minute crypto Up/Down markets:
    - ETH (slug: eth-updown-5m-{window_ts})
    - SOL (slug: sol-updown-5m-{window_ts})

    Discovers active contracts and polls their top-of-book prices and implied probabilities
    to identify lead-lag relationships against BTC moves without placing orders.
    """
    GAMMA_API = "https://gamma-api.polymarket.com"
    CLOB_API = "https://clob.polymarket.com"

    def __init__(self, poll_interval: float = 2.0):
        self.poll_interval = poll_interval
        self.is_running = False
        self._session: Optional[aiohttp.ClientSession] = None
        
        # State: asset -> {slug, yes_ask, no_ask, p_implied_up, updated_at}
        self.market_state: Dict[str, Dict[str, Any]] = {
            "eth": {},
            "sol": {}
        }

    async def start(self):
        self.is_running = True
        self._session = aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"})
        logger.info("Starting Multi-Asset Research Feed (ETH & SOL 5m)...")
        asyncio.create_task(self._poll_loop())

    async def stop(self):
        self.is_running = False
        if self._session and not self._session.closed:
            await self._session.close()

    async def _fetch_book(self, token_id: str) -> Tuple[Optional[float], Optional[float]]:
        if not self._session or self._session.closed:
            return None, None
        try:
            url = f"{self.CLOB_API}/book?token_id={token_id}"
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    bids = data.get("bids", [])
                    asks = data.get("asks", [])
                    best_bid = sorted([float(b["price"]) for b in bids], reverse=True)[0] if bids else None
                    best_ask = sorted([float(a["price"]) for a in asks])[0] if asks else None
                    return best_bid, best_ask
                else:
                    # DIAGNOSTIC: was completely silent before (no log at all, not even
                    # debug) -- ETH(P)/SOL(P) showed N/A for the entire ~4h run with zero
                    # visibility into why. This is research-only data (doesn't gate trades)
                    # but the silent failure hid a real problem shared with PolymarketFeed.
                    body = await resp.text()
                    logger.warning(f"MultiAssetFeed._fetch_book: HTTP {resp.status} for token {token_id}: {body[:200]}")
        except Exception as e:
            logger.warning(f"MultiAssetFeed._fetch_book: {type(e).__name__} for token {token_id}: {e}")
        return None, None

    async def _discover_asset_market(self, asset: str, window_ts: int) -> Optional[Tuple[str, str, str]]:
        if not self._session or self._session.closed:
            return None
        slug = f"{asset}-updown-5m-{window_ts}"
        url = f"{self.GAMMA_API}/events?slug={slug}"
        try:
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                if resp.status == 200:
                    events = await resp.json()
                    if events and isinstance(events, list):
                        ev = events[0]
                        if ev.get("active") and not ev.get("closed"):
                            markets = ev.get("markets", [])
                            if markets:
                                m = markets[0]
                                clob_tokens = m.get("clobTokenIds", [])
                                if isinstance(clob_tokens, str):
                                    try:
                                        clob_tokens = json.loads(clob_tokens)
                                    except Exception:
                                        clob_tokens = []
                                if len(clob_tokens) >= 2:
                                    return slug, clob_tokens[0], clob_tokens[1]
                else:
                    body = await resp.text()
                    logger.warning(f"MultiAssetFeed._discover_asset_market: HTTP {resp.status} for {slug}: {body[:200]}")
        except Exception as e:
            logger.warning(f"MultiAssetFeed._discover_asset_market: {type(e).__name__} for {slug}: {e}")
        return None

    async def _poll_loop(self):
        while self.is_running:
            now = time.time()
            window_ts = int(now // 300) * 300
            for asset in ["eth", "sol"]:
                try:
                    res = await self._discover_asset_market(asset, window_ts)
                    if res:
                        slug, token_yes, token_no = res
                        (yes_bid, yes_ask), (no_bid, no_ask) = await asyncio.gather(
                            self._fetch_book(token_yes),
                            self._fetch_book(token_no)
                        )
                        p_implied = None
                        if yes_ask is not None:
                            p_implied = yes_ask
                        elif no_bid is not None:
                            p_implied = 1.0 - no_bid

                        self.market_state[asset] = {
                            "slug": slug,
                            "yes_ask": yes_ask,
                            "no_ask": no_ask,
                            "p_implied": p_implied,
                            "updated_at": now
                        }
                except Exception as e:
                    logger.debug(f"Multi-asset poll error ({asset}): {e}")

            await asyncio.sleep(self.poll_interval)

    def get_market_state(self, asset: str) -> Dict[str, Any]:
        state = self.market_state.get(asset.lower(), {})
        if state and (time.time() - state.get("updated_at", 0)) < 15.0:
            return state
        return {}
