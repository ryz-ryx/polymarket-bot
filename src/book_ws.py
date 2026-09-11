import asyncio
import json
import random
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import aiohttp
from aiohttp.resolver import AsyncResolver
from loguru import logger

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
HEARTBEAT_INTERVAL_SEC = 10.0

class PolymarketBookWS:
    def __init__(self):
        self.is_running = False
        self._ws = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._task: Optional[asyncio.Task] = None
        self._subscribed_tokens: Set[str] = set()
        self._group_tokens: Dict[str, Set[str]] = {}
        self._desired_tokens: Set[str] = set()
        self.book_state: Dict[str, Dict[str, Any]] = {}
        self.connected: bool = False
        self._consecutive_failures: int = 0

    def start(self) -> None:
        if self._task is not None:
            return
        self.is_running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self.is_running = False
        if self._ws is not None and not self._ws.closed:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._session is not None and not self._session.closed:
            try:
                await self._session.close()
            except Exception:
                pass
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    def set_tokens(self, group: str, token_ids: List[Optional[str]]) -> None:
        self._group_tokens[group] = {t for t in token_ids if t}
        new_desired: Set[str] = set()
        for tokens in self._group_tokens.values():
            new_desired |= tokens
        to_add = new_desired - self._desired_tokens
        to_drop = self._desired_tokens - new_desired
        self._desired_tokens = new_desired
        if self.connected and self._ws is not None and not self._ws.closed and (to_add or to_drop):
            asyncio.create_task(self._apply_diff(to_add, to_drop))

    async def _apply_diff(self, to_add: Set[str], to_drop: Set[str]) -> None:
        try:
            if to_drop:
                await self._ws.send_str(json.dumps({"assets_ids": list(to_drop), "operation": "unsubscribe"}))
                self._subscribed_tokens -= to_drop
                for t in to_drop:
                    self.book_state.pop(t, None)
            if to_add:
                await self._ws.send_str(json.dumps({"assets_ids": list(to_add), "operation": "subscribe"}))
                self._subscribed_tokens |= to_add
        except Exception as e:
            logger.warning(f"PolymarketBookWS: subscription diff failed: {type(e).__name__}: {e}")

    async def _heartbeat(self, ws) -> None:
        try:
            while self.is_running and not ws.closed:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)
                await ws.send_str("PING")
        except Exception:
            return

    def _handle_event(self, ev: Dict[str, Any]) -> None:
        if ev.get("event_type") != "book":
            return
        asset_id = ev.get("asset_id")
        if not asset_id:
            return
        try:
            bids = [{"price": float(b["price"]), "size": float(b["size"])} for b in ev.get("bids", [])]
            asks = [{"price": float(a["price"]), "size": float(a["size"])} for a in ev.get("asks", [])]
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"PolymarketBookWS: malformed book event for {asset_id}: {e}")
            return
        best_bid = max((b["price"] for b in bids), default=None)
        best_ask = min((a["price"] for a in asks), default=None)
        self.book_state[asset_id] = {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "bids": bids,
            "asks": asks,
            "updated_at": time.time(),
        }

    async def _run(self) -> None:
        while self.is_running:
            connected_at: Optional[float] = None
            delay = 3.0
            try:
                logger.info(f"Connecting to Polymarket CLOB market WebSocket: {WS_URL}")
                resolver = AsyncResolver(nameservers=["1.1.1.1", "8.8.8.8"])
                connector = aiohttp.TCPConnector(resolver=resolver)
                self._session = aiohttp.ClientSession(connector=connector)
                
                async with self._session.ws_connect(
                    WS_URL,
                    timeout=aiohttp.ClientWSTimeout(ws_receive=25, ws_close=10),
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                        "Origin": "https://polymarket.com"
                    }
                ) as ws:
                    self._ws = ws
                    self.connected = True
                    connected_at = time.time()
                    if self._desired_tokens:
                        await ws.send_str(json.dumps({"assets_ids": list(self._desired_tokens), "type": "market"}))
                        self._subscribed_tokens = set(self._desired_tokens)
                        logger.info(f"PolymarketBookWS: subscribed to {len(self._subscribed_tokens)} token(s)")

                    hb_task = asyncio.create_task(self._heartbeat(ws))
                    try:
                        async for msg in ws:
                            if not self.is_running:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                raw = msg.data
                                if raw in ("PONG", "PING"):
                                    continue
                                try:
                                    data = json.loads(raw)
                                except (json.JSONDecodeError, TypeError):
                                    logger.debug(f"PolymarketBookWS: non-JSON frame: {raw[:120]!r}")
                                    continue
                                events = data if isinstance(data, list) else [data]
                                for ev in events:
                                    if isinstance(ev, dict):
                                        self._handle_event(ev)
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
                    finally:
                        hb_task.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if connected_at is not None and (time.time() - connected_at) >= 60.0:
                    self._consecutive_failures = 0
                self._consecutive_failures += 1
                base_backoff = min(30.0, 3.0 * (2 ** min(self._consecutive_failures - 1, 4)))
                jitter = random.uniform(0.1, 1.0)
                delay = round(base_backoff + jitter, 2)
                logger.warning(
                    f"PolymarketBookWS: connection error: {type(e).__name__}: {e} "
                    f"-- reconnecting in {delay:.1f}s (failure #{self._consecutive_failures})"
                )
            finally:
                self.connected = False
                self._ws = None
                if self._session is not None and not self._session.closed:
                    try:
                        await self._session.close()
                    except Exception:
                        pass
                    self._session = None
            if self.is_running:
                await asyncio.sleep(delay)

    def get_book(self, token_id: Optional[str], max_age_s: float = 5.0) -> Optional[Dict[str, Any]]:
        if not token_id:
            return None
        state = self.book_state.get(token_id)
        if state and (time.time() - state["updated_at"]) < max_age_s:
            return state
        return None

    def get_book_meta(self, token_id: Optional[str]) -> Tuple[bool, Optional[float], bool]:
        """
        Diagnostic helper: returns (has_fresh_book, age_s, has_any_entry)
        """
        if not token_id:
            return False, None, False
        state = self.book_state.get(token_id)
        if state is None:
            return False, None, False
        age_s = round(time.time() - state.get("updated_at", 0.0), 2)
        has_fresh = (age_s < 5.0)
        has_asks = bool(state.get("asks"))
        return has_fresh, age_s, has_asks
