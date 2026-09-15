import asyncio
import json
import time
from typing import Dict, Any, Optional, List, Tuple
import aiohttp
from loguru import logger
from src.book_ws import PolymarketBookWS

class PolymarketFeed:
    """
    Deterministic Polymarket 5-minute Up/Down Market Discovery & Real-Time Dual CLOB Feed.
    Includes on-chain ground truth resolution polling via Gamma API.
    """
    GAMMA_API = "https://gamma-api.polymarket.com"
    CLOB_API = "https://clob.polymarket.com"

    def __init__(self, asset: str = "BTC", book_ws: Optional[PolymarketBookWS] = None):
        self.asset = asset.lower()
        # Shared WebSocket book feed (see src/book_ws.py) -- replaces per-tick REST
        # /book polling, which is what triggered Cloudflare's bot-challenge on
        # 2026-09-09. Falls back to REST (with its own circuit breaker below) if the
        # WS feed isn't supplied or hasn't got a fresh snapshot yet.
        self.book_ws = book_ws
        self.current_window_ts: int = 0
        self.current_market: Optional[Dict[str, Any]] = None
        self.token_id_yes: Optional[str] = None
        self.token_id_no: Optional[str] = None
        self.market_title: str = ""
        self.session: Optional[aiohttp.ClientSession] = None
        self.last_fetch_ts: float = 0.0
        # Circuit breaker for CLOB /book: after repeated 403s (Cloudflare
        # challenge/block, not a documented rate limit -- Polymarket docs list
        # /book at 1500 req/10s, far above our ~4 req/s), stop hammering the
        # endpoint for a cooldown instead of retrying every tick.
        self._book_403_streak: int = 0
        self._book_backoff_until: float = 0.0

    async def get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            try:
                from aiohttp.resolver import AsyncResolver
                resolver = AsyncResolver(nameservers=["1.1.1.1", "8.8.8.8"])
                connector = aiohttp.TCPConnector(resolver=resolver)
            except Exception as e:
                logger.debug(f"AsyncResolver init notice: {e}")
                connector = None

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://polymarket.com/",
                "Origin": "https://polymarket.com",
            }
            self.session = aiohttp.ClientSession(connector=connector, headers=headers)
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
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
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
                                        if self.book_ws is not None:
                                            self.book_ws.set_tokens(self.asset.lower(), [self.token_id_yes, self.token_id_no])
                                        return self.current_market
                    else:
                        # DIAGNOSTIC: this was completely silent before (no branch at all for
                        # non-200), and this exact method is the one that must succeed for the
                        # bot to ever get real CLOB tokens -- a run that never once logs
                        # "Locked onto live 5m market" for ~40 straight windows means this call
                        # is failing every single time, silently.
                        body = await resp.text()
                        logger.warning(f"find_active_5min_market: HTTP {resp.status} for {slug}: {body[:200]}")
            except Exception as e:
                # Was logger.debug (invisible at normal INFO verbosity).
                logger.warning(f"find_active_5min_market: {type(e).__name__} for {slug}: {e}")

        return self.current_market

    async def check_connectivity(self) -> bool:
        """
        One-shot startup health check against both Polymarket API hosts this feed depends
        on. Added because a full run showed find_active_5min_market/_fetch_single_book/
        get_market_resolution ALL failing 100% of the time for ~4 hours while Binance calls
        (a different host, fresh ephemeral sessions) succeeded every time -- and every one
        of those failures was previously silent. This logs a clear pass/fail at INFO right
        at startup instead of waiting for the first real trade window to find out.
        """
        session = await self.get_session()
        ok = True
        for name, url in (
            ("Gamma API", f"{self.GAMMA_API}/events?slug={self.get_slug_for_window(int(time.time() // 300) * 300)}"),
            ("CLOB API", f"{self.CLOB_API}/"),
        ):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    logger.info(f"Polymarket connectivity check: {name} -> HTTP {resp.status}")
                    if resp.status >= 400 and resp.status != 404:
                        # 404 on a bare CLOB_API root is expected/harmless; anything else >=400 is not.
                        ok = False
            except Exception as e:
                logger.warning(f"Polymarket connectivity check: {name} -> {type(e).__name__}: {e}")
                ok = False
        return ok

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

    async def _fetch_single_book(self, token_id: str, max_ws_age_s: float = 20.0) -> Tuple[Optional[float], Optional[float], List[Dict[str, float]], List[Dict[str, float]]]:
        if self.book_ws is not None:
            ws_book = self.book_ws.get_book(token_id, max_age_s=max_ws_age_s)
            if ws_book is not None:
                return ws_book["best_bid"], ws_book["best_ask"], ws_book["bids"], ws_book["asks"]
        if time.time() < self._book_backoff_until:
            return None, None, [], []
        session = await self.get_session()
        try:
            url = f"{self.CLOB_API}/book?token_id={token_id}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                if resp.status == 200:
                    self._book_403_streak = 0
                    data = await resp.json()
                    raw_bids = data.get("bids", [])
                    raw_asks = data.get("asks", [])

                    parsed_bids = [{"price": float(b["price"]), "size": float(b["size"])} for b in raw_bids if "price" in b and "size" in b]
                    parsed_asks = [{"price": float(a["price"]), "size": float(a["size"])} for a in raw_asks if "price" in a and "size" in a]

                    best_bid = sorted([b["price"] for b in parsed_bids], reverse=True)[0] if parsed_bids else None
                    best_ask = sorted([a["price"] for a in parsed_asks])[0] if parsed_asks else None
                    return best_bid, best_ask, parsed_bids, parsed_asks
                elif resp.status == 403:
                    body = await resp.text()
                    self._book_403_streak += 1
                    logger.warning(
                        f"_fetch_single_book: HTTP 403 (streak={self._book_403_streak}) for token {token_id} | "
                        f"Server={resp.headers.get('Server', '-')} CF-RAY={resp.headers.get('CF-RAY', '-')} "
                        f"CF-Mitigated={resp.headers.get('cf-mitigated', '-')} | body: {body[:150]}"
                    )
                    if self._book_403_streak >= 3:
                        backoff_s = min(30 * (2 ** (self._book_403_streak - 3)), 300)
                        self._book_backoff_until = time.time() + backoff_s
                        logger.warning(f"_fetch_single_book: {self._book_403_streak} consecutive 403s -- backing off /book polling for {backoff_s:.0f}s")
                else:
                    body = await resp.text()
                    logger.warning(f"_fetch_single_book: HTTP {resp.status} for token {token_id}: {body[:200]}")
        except Exception as e:
            # Was logger.debug (invisible at normal INFO verbosity) -- BLOCKED_PHANTOM fired
            # on essentially every signal all night, which only happens when direct_ask is
            # None, which only happens when this call (or find_active_5min_market before it)
            # fails. Needs to be loud until we know why.
            logger.warning(f"_fetch_single_book: {type(e).__name__} for token {token_id}: {e}")
        return None, None, [], []

    async def fetch_fresh_asks(self, token_id: str) -> List[Dict[str, float]]:
        """
        Fetch the freshest possible ask ladder for one token (max 1s WS cache
        age, falling back to a live REST call) -- used to re-price a fill
        AFTER simulating execution latency, rather than reusing the book
        snapshot the trading signal was decided on.
        """
        if not token_id:
            return []
        _, _, _, asks = await self._fetch_single_book(token_id, max_ws_age_s=1.0)
        return asks

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
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
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
                else:
                    body = await resp.text()
                    logger.warning(f"get_market_resolution: HTTP {resp.status} for {slug}: {body[:200]}")
        except Exception as e:
            logger.warning(f"get_market_resolution: {type(e).__name__} for {slug}: {e}")
        return None

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
