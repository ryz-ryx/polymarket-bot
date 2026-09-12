"""
Two-way chat interface for Hermes: Telegram (long-poll) + Discord (bot), gated to me only.

Runs as a separate process alongside the trading bot (run.py). Listens for my messages
on Telegram and Discord, resolves them to the right /api/* endpoint or control action,
and replies in the same chat with no confirmation step.

ENV (read from .env via config.py, plus DISCORD_BOT_TOKEN / MY_DISCORD_USER_ID):
    TELEGRAM_BOT_TOKEN      -> Telegram long-poll listener (getUpdates, 30s timeout)
    TELEGRAM_CHAT_ID        -> only this chat ID is answered (others ignored)
    DISCORD_BOT_TOKEN       -> Discord bot token (NOT the webhook URL). Empty = Discord dormant.
    MY_DISCORD_USER_ID      -> only this Discord user ID is answered. Empty = Discord dormant.
    CONTROL_SECRET          -> shared secret for POST /api/control (pause/resume). Unset = control refused.
    RAILWAY_BASE_URL        -> base URL for the bot's API endpoints (default: https://polymarketbot.up.railway.app)
"""
import asyncio
import json
import os
import re
import sys
import time
from typing import Any, Dict, Optional

import aiohttp
from loguru import logger

from config import config

RAILWAY_BASE = os.getenv("RAILWAY_BASE_URL", "https://polymarketbot.up.railway.app").rstrip("/")
CONTROL_SECRET = config.control_secret or os.getenv("CONTROL_SECRET", "")
TELEGRAM_TOKEN = config.telegram_bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = config.telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
MY_DISCORD_USER_ID = os.getenv("MY_DISCORD_USER_ID", "")

# ---------------------------------------------------------------------------
# aiohttp session shared across all API calls
# ---------------------------------------------------------------------------

_session: Optional[aiohttp.ClientSession] = None


async def _ensure_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def _api(method: str, path: str, *, params: Optional[Dict] = None,
               json_payload: Optional[Dict] = None,
               timeout_sec: int = 20, base: str = RAILWAY_BASE,
               extra_headers: Optional[Dict] = None) -> Dict[str, Any]:
    """GET/POST to the Railway API. Returns parsed JSON body (empty dict on failure)."""
    session = await _ensure_session()
    url = f"{base}/{path.lstrip('/')}"
    kwargs: Dict[str, Any] = dict(timeout=aiohttp.ClientTimeout(total=timeout_sec))
    if params:
        kwargs["params"] = params
    headers: Dict[str, str] = {}
    if extra_headers:
        headers.update(extra_headers)
    if method.upper() == "POST":
        kwargs["data"] = json.dumps(json_payload) if json_payload else None
        headers["Content-Type"] = "application/json"
    if headers:
        kwargs["headers"] = headers
    try:
        async with session.request(method, url, **kwargs) as resp:
            text = await resp.text()
            if resp.status not in (200, 201, 204):
                logger.warning(f"ChatBot API {method} {resp.status} {url}: {text[:200]}")
                return {"error": f"HTTP {resp.status}", "body": text[:300]}
            if not text.strip():
                return {}
            return json.loads(text)
    except Exception as e:
        logger.warning(f"ChatBot API {method} {url}: {type(e).__name__}: {e}")
        return {"error": type(e).__name__, "msg": str(e)[:300]}


async def api_get(path: str, **kw) -> Dict[str, Any]:
    return await _api("GET", path, **kw)


async def api_post(path: str, **kw) -> Dict[str, Any]:
    return await _api("POST", path, **kw)


async def api_post_with_secret(path: str, json_payload: Dict, secret: str) -> Dict[str, Any]:
    """POST with the X-Control-Secret header for /api/control."""
    return await _api("POST", path, json_payload=json_payload,
                      extra_headers={"X-Control-Secret": secret})


async def telegram_bot_api(method: str, params: Optional[Dict] = None,
                            json_payload: Optional[Dict] = None) -> Dict[str, Any]:
    """Call the Telegram Bot API (https://api.telegram.org/bot<token>/<method>)."""
    session = await _ensure_session()
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    kwargs: Dict[str, Any] = dict(timeout=aiohttp.ClientTimeout(total=15),
                                  params=params, headers={"Content-Type": "application/json"})
    if json_payload:
        kwargs["data"] = json.dumps(json_payload)
    try:
        async with session.request("POST", url, **kwargs) as resp:
            text = await resp.text()
            return json.loads(text)
    except Exception as e:
        logger.warning(f"Telegram Bot API {method}: {type(e).__name__}: {e}")
        return {"ok": False, "error": str(e)[:200]}


async def close_session() -> None:
    if _session and not _session.closed:
        await _session.close()


# ---------------------------------------------------------------------------
# Natural-language resolver: map a message to the right endpoint + conversational reply
# ---------------------------------------------------------------------------

_NL_PATTERNS: list = [
    (re.compile(r"\b(pnl|profit|loss|day(today)?|what.s the (pnl|profit|loss|day))\b", re.I), "pnl"),
    (re.compile(r"\b(circuit breaker|tripped|halted|is the bot paused|is it paused|paused|stopped)\b", re.I), "circuit"),
    (re.compile(r"\b(drawdown|drawdown breaker|floor|permanent|max drawdown)\b", re.I), "drawdown"),
    (re.compile(r"\b(funnel|phantom|block(ed)?|noise|yield)\b", re.I), "funnel"),
    (re.compile(r"\b(calibration|brier|model|calibrated|confidence)\b", re.I), "calibration"),
    (re.compile(r"\b(trade|trades|executed|history|recent|what.s been traded)\b", re.I), "trades"),
    (re.compile(r"\b(logs?|log|debug|trace)\b", re.I), "logs"),
    (re.compile(r"\b(status|state|what.s happening|what.s going on|current|what.d it do|what.s it doing|report)\b", re.I), "status"),
    (re.compile(r"\b(balance|cash|bankroll|how much|portfolio value|total)\b", re.I), "balance"),
    (re.compile(r"\b(pause|stop|halt|resume|start|restart|unpause|continue)\b", re.I), "control"),
]


async def resolve_pnl() -> str:
    d = await api_get("api/state")
    if "error" in d:
        return f"Error fetching state: {d.get('error')}"
    assets = d.get("assets", {})
    port = d.get("portfolio", {})
    lines = []
    for asset in ["BTC"]:
        a = assets.get(asset, {})
        r = a.get("risk", {})
        pnl = r.get("daily_pnl", 0)
        cb = r.get("circuit_breaker_triggered", False)
        conf = a.get("confidence_weight", 0)
        cash = a.get("positions", {}).get("simulated_balance", 0)
        lines.append(f"{asset}: PnL ${pnl:+.2f} | circuit_breaker={'TRIPPED' if cb else 'OK'} | confidence={conf:.3f} | cash=${cash:.2f}")
    total = port.get("daily_pnl", 0)
    pcb = port.get("circuit_breaker_triggered", False)
    lines.append(f"Portfolio: PnL ${total:+.2f} | circuit_breaker={'TRIPPED' if pcb else 'OK'}")
    return "Daily PnL:\n" + "\n".join(lines)


async def resolve_circuit() -> str:
    d = await api_get("api/state")
    if "error" in d:
        return f"Error fetching state: {d.get('error')}"
    assets = d.get("assets", {})
    port = d.get("portfolio", {})
    lines = []
    any_tripped = False
    for asset in ["BTC"]:
        a = assets.get(asset, {})
        r = a.get("risk", {})
        cb = r.get("circuit_breaker_triggered", False)
        pnl = r.get("daily_pnl", 0)
        lines.append(f"{asset}: circuit_breaker={'TRIPPED' if cb else 'OK'} (PnL ${pnl:+.2f})")
        if cb:
            any_tripped = True
    pcb = port.get("circuit_breaker_triggered", False)
    lines.append(f"Portfolio circuit_breaker={'TRIPPED' if pcb else 'OK'}")
    if any_tripped or pcb:
        return "🚨 Circuit breaker status:\n" + "\n".join(lines) + "\n⚠️ One or more circuit breakers are TRIPPED."
    return "✅ All circuit breakers are OK:\n" + "\n".join(lines)


async def resolve_drawdown() -> str:
    d = await api_get("api/drawdown")
    if "error" in d:
        return f"Error fetching drawdown: {d.get('error')}"
    tripped = d.get("tripped", False)
    if tripped:
        return "🚨 PORTFOLIO DRAWDOWN BREAKER IS TRIPPED. All trading halted permanently. Floor is in effect. Manual reset required via /api/control."
    return "✅ Portfolio drawdown breaker is NOT tripped. Trading allowed (subject to daily circuit breakers)."


async def resolve_funnel() -> str:
    parts = []
    for asset in ["BTC"]:
        d = await api_get("api/funnel", params={"asset": asset, "window_hours": 4, "baseline_hours": 96})
        if "error" in d:
            parts.append(f"{asset}: error — {d.get('error')}")
            continue
        wc = d.get("window_counts", {})
        parts.append(
            f"{asset}: executed={wc.get('EXECUTED',0)} | blocked_noise={wc.get('BLOCKED_NOISE',0)} | "
            f"no_signal={wc.get('NO_SIGNAL',0)} | blocked_max_pos={wc.get('BLOCKED_WINDOW_MAX_POS',0)} | "
            f"phantom_rate={d.get('window_phantom_rate',0):.4f}"
        )
    return "Funnel (last 4h window vs 96h baseline):\n" + "\n".join(parts)


async def resolve_calibration() -> str:
    parts = []
    for asset in ["BTC"]:
        d = await api_get("api/calibration", params={"asset": asset})
        if "error" in d:
            parts.append(f"{asset}: error — {d.get('error')}")
            continue
        parts.append(
            f"{asset}: model_brier={d.get('model_brier',0):.4f} | market_brier={d.get('market_brier',0):.4f} | "
            f"model_better={'YES' if d.get('model_better_than_market') else 'NO'} | "
            f"resolved_windows={d.get('resolved_windows',0)}"
        )
    return "Calibration (last 7 days):\n" + "\n".join(parts)


async def resolve_trades() -> str:
    parts = []
    for asset in ["BTC"]:
        d = await api_get("api/recent_trades", params={"asset": asset, "status": "EXECUTED", "limit": 20})
        if "error" in d:
            parts.append(f"{asset}: error — {d.get('error')}")
            continue
        count = d.get("count", 0)
        if count == 0:
            parts.append(f"{asset}: no executed trades.")
            continue
        trades = d.get("trades", [])
        win_count = sum(1 for t in trades if t.get("outcome") == "YES")
        win_rate = (win_count * 100.0 / count) if count else 0
        mean_entry = sum(t.get("entry_price", 0) for t in trades) / len(trades) if trades else 0
        out_of_range = [t for t in trades if t.get("entry_price", 1) < 0.333 or t.get("entry_price", 1) > 0.50]
        lines = [f"Win Rate: {win_rate:.1f}% ({win_count}/{count})", f"Mean Entry Price: {mean_entry:.4f}"]
        if out_of_range:
            oors = ", ".join(f"{t['entry_price']:.4f}" for t in out_of_range)
            lines.append(f"Out-of-range entry prices (outside [0.333, 0.50]): {oors}")
        parts.append(f"{asset} (last {count} executed):\n" + "\n".join(f"  {l}" for l in lines))
    return "\n".join(parts)


async def resolve_logs() -> str:
    d = await api_get("api/logs", params={"lines": 40})
    if "error" in d:
        return f"Error fetching logs: {d.get('error')}"
    body = d.get("body", "")
    if not body:
        return "Logs endpoint returned empty."
    return "Recent logs (last 40 lines):\n```\n" + body + "\n```"


async def resolve_status() -> str:
    d = await api_get("api/state")
    if "error" in d:
        return f"Error fetching state: {d.get('error')}"
    assets = d.get("assets", {})
    port = d.get("portfolio", {})
    lines = []
    for asset in ["BTC"]:
        a = assets.get(asset, {})
        r = a.get("risk", {})
        cb = r.get("circuit_breaker_triggered", False)
        pnl = r.get("daily_pnl", 0)
        cash = a.get("positions", {}).get("simulated_balance", 0)
        conf = a.get("confidence_weight", 0)
        lines.append(f"{asset}: cb={'TRIPPED' if cb else 'OK'} | PnL=${pnl:+.2f} | cash=${cash:.2f} | conf={conf:.3f}")
    total_pnl = port.get("daily_pnl", 0)
    pcb = port.get("circuit_breaker_triggered", False)
    lines.append(f"Portfolio: daily_pnl=${total_pnl:+.2f} | circuit_breaker={'TRIPPED' if pcb else 'OK'}")
    return "Current state:\n" + "\n".join(lines)


async def resolve_balance() -> str:
    d = await api_get("api/state")
    if "error" in d:
        return f"Error fetching state: {d.get('error')}"
    cash = d.get("assets", {}).get("BTC", {}).get("positions", {}).get("simulated_balance", "N/A")
    total_pnl = d.get("portfolio", {}).get("daily_pnl", 0)
    try:
        cash_str = f"${float(cash):,.2f}"
    except (TypeError, ValueError):
        cash_str = str(cash)
    return f"BTC balance: {cash_str} | Daily PnL: ${total_pnl:+.2f}"


async def resolve_control(text: str) -> str:
    """Explicit pause/resume/start/stop/restart command. Requires CONTROL_SECRET."""
    if not CONTROL_SECRET:
        return "❌ Control actions are disabled — CONTROL_SECRET is not set. Pull it from Railway's Variables tab and set it in the chat bot's env to enable pause/resume from chat."
    lowered = text.lower()
    if any(w in lowered for w in ["pause", "stop", "halt"]):
        d = await api_post_with_secret("api/control", {"pause": True}, CONTROL_SECRET)
        if "error" in d:
            return f"❌ Failed to pause: {d.get('error')} — {d.get('body', '')[:200]}"
        return "✅ Bot paused. Trade evaluation halted until resumed."
    elif any(w in lowered for w in ["resume", "start", "restart", "unpause", "continue"]):
        d = await api_post_with_secret("api/control", {"pause": False}, CONTROL_SECRET)
        if "error" in d:
            return f"❌ Failed to resume: {d.get('error')} — {d.get('body', '')[:200]}"
        return "✅ Bot resumed. Trade evaluation active again."
    return "Unrecognized control command. Use 'pause' or 'resume'."


# Dispatcher: pick the right handler for a message
async def route_message(text: str) -> str:
    """Return a reply string for the given message text."""
    low = text.lower().strip()
    if low in ("pause", "stop", "halt", "resume", "start", "restart", "unpause", "continue"):
        return await resolve_control(low)
    if any(low.startswith(w) for w in ["pause", "stop", "halt", "resume", "start", "restart", "unpause", "continue"]):
        return await resolve_control(low)
    for pattern, handler_name in _NL_PATTERNS:
        if pattern.search(text):
            handler = {
                "pnl": resolve_pnl, "circuit": resolve_circuit, "drawdown": resolve_drawdown,
                "funnel": resolve_funnel, "calibration": resolve_calibration,
                "trades": resolve_trades, "logs": resolve_logs, "status": resolve_status,
                "balance": resolve_balance, "control": resolve_control,
            }.get(handler_name)
            if handler:
                return await handler()
    return (
        "I can answer questions about the bot's state. Try:\n"
        "• 'what's BTC's PnL today' / 'pnl'\n"
        "• 'is the circuit breaker tripped'\n"
        "• 'drawdown status'\n"
        "• 'funnel'\n"
        "• 'calibration'\n"
        "• 'recent trades' / 'trades'\n"
        "• 'logs'\n"
        "• 'status' / 'current state'\n"
        "• 'balance'\n"
        "• 'pause' / 'resume' (control — needs CONTROL_SECRET)"
    )


# ---------------------------------------------------------------------------
# Telegram side — long-poll listener using raw Bot API (getUpdates)
# ---------------------------------------------------------------------------

class TelegramChatBot:
    def __init__(self):
        self.token = TELEGRAM_TOKEN
        self.allowed_chat_id = TELEGRAM_CHAT_ID
        self._task: Optional[asyncio.Task] = None
        self._offset = 0
        self._running = False
        self._last_tested = False  # track whether we've confirmed the token works

    async def start(self) -> None:
        if not self.token:
            logger.info("Telegram: TELEGRAM_BOT_TOKEN not set — skipping Telegram listener.")
            return
        if not self.allowed_chat_id:
            logger.warning("Telegram: TELEGRAM_CHAT_ID not set — no chat ID filter, ALL chats will be answered.")
        logger.info(f"Telegram: long-polling getUpdates (allowed_chat_id={self.allowed_chat_id}, timeout=30s).")
        # Quick connectivity test before entering the poll loop
        test = await telegram_bot_api("getMe")
        if not test.get("ok"):
            logger.error(f"Telegram: bot token is invalid or bot is not reachable: {test.get('description', test.get('error'))}")
            return
        bot_info = test.get("result", {})
        logger.info(f"Telegram: bot connected — @{bot_info.get('username', 'unknown')} (id={bot_info.get('id')})")
        self._running = True
        self._offset = 0
        self._task = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        timeout_sec = 30
        backoff = 1.0
        while self._running:
            try:
                offset_val = self._offset
                params: Dict[str, str] = {
                    "offset": str(offset_val),
                    "timeout": str(timeout_sec),
                    "allowed_updates": '["message"]',
                }
                resp = await telegram_bot_api("getUpdates", params=params)
                backoff = 1.0
                if not resp.get("ok"):
                    desc = resp.get("description", resp.get("error", "unknown"))
                    logger.warning(f"Telegram getUpdates error: {desc}")
                    await asyncio.sleep(5)
                    continue
                updates = resp.get("result", [])
                for upd in updates:
                    self._offset = upd.get("update_id", self._offset) + 1
                    msg = upd.get("message")
                    if msg:
                        await self._handle_message(msg)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Telegram poll loop error: {type(e).__name__}: {e}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _handle_message(self, msg: Dict[str, Any]) -> None:
        chat_id = str(msg.get("chat", {}).get("id", ""))
        if self.allowed_chat_id and chat_id != self.allowed_chat_id:
            logger.debug(f"Telegram: ignoring message from unauthorized chat_id={chat_id}")
            return
        text = msg.get("text") or ""
        if not text.strip():
            return
        logger.info(f"Telegram [{chat_id}]: {text[:80]}")
        try:
            reply = await route_message(text)
            await self._send_reply(msg, reply)
        except Exception as e:
            logger.error(f"Telegram: error handling message: {type(e).__name__}: {e}")
            try:
                await self._send_reply(msg, f"⚠️ Error: {type(e).__name__}: {e}")
            except Exception:
                pass

    async def _send_reply(self, msg: Dict[str, Any], text: str) -> None:
        chat_id = msg.get("chat", {}).get("id")
        msg_id = msg.get("message_id")
        if chat_id is None:
            return
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:4000],
            "disable_web_page_preview": True,
        }
        if msg_id:
            payload["reply_to_message_id"] = msg_id
        resp = await telegram_bot_api("sendMessage", json_payload=payload)
        if not resp.get("ok"):
            logger.warning(f"Telegram sendMessage failed: {resp.get('description', resp.get('error'))[:200]}")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


# ---------------------------------------------------------------------------
# Discord side (discord.py) — dormant until DISCORD_BOT_TOKEN is set
# ---------------------------------------------------------------------------

try:
    import discord
    from discord import Message, Client
    from discord.intents import Intents
    _DISCORD_AVAILABLE = True
except ImportError:
    _DISCORD_AVAILABLE = False
    logger.warning("discord.py not installed — Discord bot disabled")


class DiscordChatBot:
    def __init__(self):
        self.token = DISCORD_BOT_TOKEN
        self.allowed_user_id = MY_DISCORD_USER_ID
        self.client: Optional[Client] = None

    async def start(self) -> None:
        if not self.token:
            logger.info("Discord: DISCORD_BOT_TOKEN not set — skipping Discord bot.")
            return
        if not _DISCORD_AVAILABLE:
            logger.error("Discord: discord.py is not installed. Run: pip install discord.py")
            return
        if not self.allowed_user_id:
            logger.warning("Discord: MY_DISCORD_USER_ID not set — no user filter, ALL users will be answered.")
        intents = Intents.default()
        intents.message_content = True
        try:
            self.client = Client(intents=intents)
            self.client.add_event_handler(self._on_message, "on_message")
            logger.info(f"Discord: connecting bot (allowed_user_id={self.allowed_user_id or 'ALL'}).")
            await self.client.start(self.token)
        except discord.LoginFailure:
            logger.error("Discord: invalid bot token — check DISCORD_BOT_TOKEN.")
        except Exception as e:
            logger.error(f"Discord: failed to start bot: {type(e).__name__}: {e}")

    async def stop(self) -> None:
        if self.client:
            try:
                await self.client.close()
            except Exception as e:
                logger.warning(f"Discord: error on shutdown: {e}")

    async def _on_message(self, message: Message) -> None:
        if message.author.id == self.client.user.id:
            return
        if self.allowed_user_id and str(message.author.id) != self.allowed_user_id:
            logger.debug(f"Discord: ignoring message from unauthorized user_id={message.author.id}")
            return
        # Only respond in server channels (remove this block to allow DMs)
        if message.guild is None:
            return
        text = message.content.strip()
        if not text:
            return
        logger.info(f"Discord [{message.guild.name}/{message.channel.name}] <{message.author.name}>: {text[:80]}")
        try:
            reply = await route_message(text)
            await message.reply(reply, mention_author=False)
        except Exception as e:
            logger.error(f"Discord: error replying: {type(e).__name__}: {e}")
            try:
                await message.reply(f"⚠️ Error: {type(e).__name__}: {e}", mention_author=False)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Main: run both listeners concurrently
# ---------------------------------------------------------------------------

async def main() -> None:
    logger.remove()
    logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>", level="INFO")
    logger.add("logs/chat_bot.log", rotation="00:00", retention="14 days", level="DEBUG", encoding="utf-8")

    logger.info("ChatBot starting...")
    logger.info(f"  Telegram: token={'SET' if TELEGRAM_TOKEN else 'EMPTY'} allowed_chat_id={TELEGRAM_CHAT_ID or 'NONE'}")
    logger.info(f"  Discord:  token={'SET' if DISCORD_BOT_TOKEN else 'EMPTY'} allowed_user_id={MY_DISCORD_USER_ID or 'NONE'}")
    logger.info(f"  Railway API base: {RAILWAY_BASE}")
    logger.info(f"  CONTROL_SECRET: {'SET' if CONTROL_SECRET else 'EMPTY (control commands disabled)'}")

    tele = TelegramChatBot()
    disc = DiscordChatBot()

    tasks = []
    if TELEGRAM_TOKEN:
        tasks.append(asyncio.create_task(tele.start(), name="telegram-listener"))
    if DISCORD_BOT_TOKEN and _DISCORD_AVAILABLE:
        tasks.append(asyncio.create_task(disc.start(), name="discord-bot"))

    if not tasks:
        logger.error("No chat channels configured. Set TELEGRAM_BOT_TOKEN or DISCORD_BOT_TOKEN in .env to enable.")
        return

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("ChatBot shutting down...")
    finally:
        await tele.stop()
        await disc.stop()
        await close_session()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("ChatBot stopped by user.")
    except Exception as e:
        logger.error(f"ChatBot crashed: {type(e).__name__}: {e}")
        raise
