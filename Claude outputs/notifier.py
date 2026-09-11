"""
Discord webhook + Telegram bot alerts for the bot's key lifecycle events.

Both channels are entirely optional and independent -- if DISCORD_WEBHOOK_URL is
unset, Discord sends are silent no-ops; same for TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID
and Telegram. Every send is fire-and-forget (alert() returns immediately, it does
not await delivery) and every failure is caught and logged at WARNING, never raised
-- a bad token, a rate limit, or Discord/Telegram being down must NEVER crash or
stall the 1-second trading tick loop. This mirrors the same "never let an auxiliary
call block or kill the main loop" discipline already used for the Gamma/CLOB feeds.
"""
import asyncio
from typing import Optional
import aiohttp
from loguru import logger
from config import config

_session: Optional[aiohttp.ClientSession] = None


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def _send_discord(message: str) -> None:
    try:
        session = await _get_session()
        async with session.post(
            config.discord_webhook_url,
            json={"content": message[:1900]},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            if resp.status not in (200, 204):
                body = await resp.text()
                logger.warning(f"Notifier: Discord webhook HTTP {resp.status}: {body[:200]}")
    except Exception as e:
        logger.warning(f"Notifier: Discord send failed: {type(e).__name__}: {e}")


async def _send_telegram(message: str) -> None:
    try:
        session = await _get_session()
        url = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": config.telegram_chat_id,
            "text": message[:4000],
            "disable_web_page_preview": True,
        }
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.warning(f"Notifier: Telegram HTTP {resp.status}: {body[:200]}")
    except Exception as e:
        logger.warning(f"Notifier: Telegram send failed: {type(e).__name__}: {e}")


def alert(message: str) -> None:
    """
    Schedule delivery to every configured channel and return immediately.
    Call this from anywhere in the bot without `await` -- it must never add
    latency to the trading loop. Requires a running event loop (safe to call
    from inside bot.run()); for a call site outside any loop, use
    alert_and_wait() instead. Safe to call even with zero channels
    configured (becomes a no-op).
    """
    if config.discord_webhook_url:
        asyncio.create_task(_send_discord(message))
    if config.telegram_bot_token and config.telegram_chat_id:
        asyncio.create_task(_send_telegram(message))


async def alert_and_wait(message: str) -> None:
    """
    Same channels as alert(), but awaits delivery before returning instead of
    firing-and-forgetting. For the one call site (run.py's top-level crash
    handler) that runs after the main event loop has already ended -- there's
    no loop left for create_task() to schedule onto, and the process is about
    to exit anyway, so the alert must actually complete before we return.
    """
    tasks = []
    if config.discord_webhook_url:
        tasks.append(_send_discord(message))
    if config.telegram_bot_token and config.telegram_chat_id:
        tasks.append(_send_telegram(message))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def close() -> None:
    if _session and not _session.closed:
        await _session.close()
