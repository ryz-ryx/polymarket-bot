import asyncio
import json
import time
from typing import Dict, Any, Optional, List, Tuple
import aiohttp
from loguru import logger

class PolymarketFeed:
    """
    Deterministic Polymarket 5-minute Up/Down Market Discovery & Real-Time Dual CLOB Feed.
    Includes on-chain ground truth resolution polling via Gamma API.
    """
    GAMMA_API = "https://gamma-api.polymarket.com"
    CLOB_API = "https://clob.polymarket.com"

    def __init__(self, asset: str = "BTC"):
        self.asset = asset.lower()
        self.current_window_ts: int = 0
        self.current_market: Optional[Dict[str, Any]] = None
        self.token_id_yes: Optional[str] = None
        self.token_id_no: Optional[str] = None
        self.market_title: str = ""
        self.session: Optional[aiohttp.ClientSession] = None
        self.last_fetch_ts: float = 0.0

    async def get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    def get_slug_for_window(self, window_ts: int) -> str:
        return f"{self.asset}-updown-5m-{window_ts}"

    async def find_active_5min_market(self) -> Optional[Dict[str, Any]]:
        now = time.time()
        window_ts = int(now // 300) * 300

        if self.current_window_ts == window_ts and self.current_market and self.token_id_yes and self.token_id_no:
            return self.current_market

        session = await self.get_session()
        candidate_windows = [window_ts, window_ts + 300, window_ts - 300]
        
        for w_ts in candidate_windows:
            slug = self.get_slug_for_window(w_ts)
            url = f"{self.GAMMA_API}/events?slug={slug}"
            try:
                async with session.get(url, timeout=4) as resp:
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
                                        self.current_window_ts = w_ts
                                        self.token_id_yes = clob_tokens[0]
                                        self.token_id_no = clob_tokens[1]
                                        self.current_market = m
                                        self.market_title = ev.get("title", slug)
                                        logger.info(
                                            f"Polymarket: Locked onto live 5m market '{self.market_title}' | "
                                            f"Tokens: YES={self.token_id_yes[:10]}... NO={self.token_id_no[:10]}..."
                                        )
                                        return self.current_market
            except Exception as e:
                logger.debug(f"Error querying slug {slug}: {e}")

        return self.current_market

    @staticmethod
    def simulate_walk_book(asks: List[Dict[str, float]], required_usd: float) -> Tuple[Optional[float], float, float]:
        """
        Simulates walking through an ask orderbook ladder for a given USD spend.
        Asks are expected to be a list of {'price': float, 'size': float}, sorted by price ascending.

        Returns:
            (vwap_price, total_filled_usd, total_shares)
            If available depth < required_usd, returns (None, total_filled_usd, total_shares).
        """
        if not asks or required_usd <= 0:
            return None, 0.0, 0.0

        remaining_usd = required_usd
        total_shares = 0.0
        total_cost = 0.0

        for level in sorted(asks, key=lambda x: x["price"]):
            price = level["price"]
            size = level["size"]  # available shares at this price level
            if price <= 0 or size <= 0:
                continue

            level_max_cost = price * size
            if remaining_usd <= level_max_cost:
                shares_bought = remaining_usd / price
                total_shares += shares_bought
                total_cost += remaining_usd
                remaining_usd = 0.0
                break
            else:
                total_shares += size
                total_cost += level_max_cost
                remaining_usd -= level_max_cost

        if remaining_usd > 1e-4:
            # Not enough liquidity to fill the requested size
            return None, total_cost, total_shares

        vwap = total_cost / total_shares if total_shares > 0 else None
        return vwap, total_cost, total_shares

    async def _fetch_single_book(self, token_id: str) -> Tuple[Optional[float], Optional[float], List[Dict[str, float]], List[Dict[str, float]]]:
        session = await self.get_session()
        try:
            url = f"{self.CLOB_API}/book?token_id={token_id}"
            async with session.get(url, timeout=3) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    raw_bids = data.get("bids", [])
                    raw_asks = data.get("asks", [])
                    
                    parsed_bids = [{"price": float(b["price"]), "size": float(b["size"])} for b in raw_bids if "price" in b and "size" in b]
                    parsed_asks = [{"price": float(a["price"]), "size": float(a["size"])} for a in raw_asks if "price" in a and "size" in a]

                    best_bid = sorted([b["price"] for b in parsed_bids], reverse=True)[0] if parsed_bids else None
                    best_ask = sorted([a["price"] for a in parsed_asks])[0] if parsed_asks else None
                    return best_bid, best_ask, parsed_bids, parsed_asks
        except Exception as e:
            logger.debug(f"Error fetching book for token {token_id}: {e}")
        return None, None, [], []

    async def get_live_market_prices(self) -> Dict[str, Any]:
        prices = {
            "direct_yes_bid": None,
            "direct_yes_ask": None,
            "direct_no_bid": None,
            "direct_no_ask": None,
            "yes_asks": [],
            "yes_bids": [],
            "no_asks": [],
            "no_bids": [],
            "yes_bid": 0.49,
            "yes_ask": 0.51,
            "no_bid": 0.49,
            "no_ask": 0.51,
            "is_live_book": False
        }

        if not self.token_id_yes or not self.token_id_no:
            return prices

        (yes_bid, yes_ask, yes_bids, yes_asks), (no_bid, no_ask, no_bids, no_asks) = await asyncio.gather(
            self._fetch_single_book(self.token_id_yes),
            self._fetch_single_book(self.token_id_no)
        )

        prices["direct_yes_bid"] = yes_bid
        prices["direct_yes_ask"] = yes_ask
        prices["direct_no_bid"] = no_bid
        prices["direct_no_ask"] = no_ask
        prices["yes_asks"] = yes_asks
        prices["yes_bids"] = yes_bids
        prices["no_asks"] = no_asks
        prices["no_bids"] = no_bids

        candidates_yes_bid: List[float] = []
        candidates_yes_ask: List[float] = []

        if yes_bid is not None:
            candidates_yes_bid.append(yes_bid)
        if no_ask is not None:
            candidates_yes_bid.append(round(1.0 - no_ask, 3))

        if yes_ask is not None:
            candidates_yes_ask.append(yes_ask)
        if no_bid is not None:
            candidates_yes_ask.append(round(1.0 - no_bid, 3))

        if candidates_yes_bid and candidates_yes_ask:
            best_yes_bid = max(candidates_yes_bid)
            best_yes_ask = min(candidates_yes_ask)

            if best_yes_ask >= best_yes_bid:
                prices["yes_bid"] = best_yes_bid
                prices["yes_ask"] = best_yes_ask
                prices["no_bid"] = round(1.0 - best_yes_ask, 3)
                prices["no_ask"] = round(1.0 - best_yes_bid, 3)
                prices["is_live_book"] = True

        return prices

    async def get_market_resolution(self, slug: str) -> Optional[int]:
        """
        Polls Gamma API for official settlement resolution of slug.
        Returns:
            1 if YES won (UP)
            0 if NO won (DOWN)
            None if not yet resolved by Polymarket/UMA
        """
        session = await self.get_session()
        url = f"{self.GAMMA_API}/events?slug={slug}"
        try:
            async with session.get(url, timeout=4) as resp:
                if resp.status == 200:
                    events = await resp.json()
                    if events and isinstance(events, list):
                        ev = events[0]
                        markets = ev.get("markets", [])
                        if markets:
                            m = markets[0]
                            outcome_prices = m.get("outcomePrices")
                            if isinstance(outcome_prices, str):
                                try:
                                    outcome_prices = json.loads(outcome_prices)
                                except Exception:
                                    pass

                            if outcome_prices and isinstance(outcome_prices, list) and len(outcome_prices) >= 2:
                                # Outcome ["1", "0"] -> YES won (UP)
                                # Outcome ["0", "1"] -> NO won (DOWN)
                                try:
                                    p_yes = float(outcome_prices[0])
                                    p_no = float(outcome_prices[1])
                                    if p_yes == 1.0 and p_no == 0.0:
                                        return 1
                                    elif p_yes == 0.0 and p_no == 1.0:
                                        return 0
                                except (ValueError, TypeError):
                                    pass
        except Exception as e:
            logger.debug(f"Error checking resolution for {slug}: {e}")
        return None

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
