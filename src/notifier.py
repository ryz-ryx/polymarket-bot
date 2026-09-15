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

# Every send used to be single-shot: a rate limit (Telegram ~1 msg/sec/chat,
# Discord webhooks ~30/min) or any transient network blip silently dropped
# that alert forever, with only a WARNING log line nobody was watching --
# verified as the cause of "after a while it stops showing trades/PnL" on a
# busy trading day (every trade + every settlement + a rollup every 10
# windows adds up fast). Retry a bounded number of times with backoff,
# honoring a 429's Retry-After header when present, before giving up.
_MAX_ATTEMPTS = 4
_BASE_BACKOFF_SEC = 2.0


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def _send_discord(message: str) -> None:
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            session = await _get_session()
            async with session.post(
                config.discord_webhook_url,
                json={"content": message[:1900]},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                if resp.status in (200, 204):
                    return
                retry_after = resp.headers.get("Retry-After")
                body = await resp.text()
                if resp.status == 429 and attempt < _MAX_ATTEMPTS:
                    delay = float(retry_after) if retry_after else _BASE_BACKOFF_SEC * attempt
                    logger.warning(f"Notifier: Discord rate-limited (attempt {attempt}/{_MAX_ATTEMPTS}), retrying in {delay:.1f}s")
                    await asyncio.sleep(delay)
                    continue
                logger.warning(f"Notifier: Discord webhook HTTP {resp.status}: {body[:200]}")
                return
        except Exception as e:
            if attempt < _MAX_ATTEMPTS:
                logger.warning(f"Notifier: Discord send failed (attempt {attempt}/{_MAX_ATTEMPTS}): {type(e).__name__}: {e}, retrying")
                await asyncio.sleep(_BASE_BACKOFF_SEC * attempt)
                continue
            logger.warning(f"Notifier: Discord send failed permanently after {_MAX_ATTEMPTS} attempts: {type(e).__name__}: {e}")


async def _send_telegram(message: str) -> None:
    url = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": config.telegram_chat_id,
        "text": message[:4000],
        "disable_web_page_preview": True,
    }
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            session = await _get_session()
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    return
                body = await resp.text()
                if resp.status == 429 and attempt < _MAX_ATTEMPTS:
                    retry_after = None
                    try:
                        retry_after = (await resp.json()).get("parameters", {}).get("retry_after")
                    except Exception:
                        pass
                    delay = float(retry_after) if retry_after else _BASE_BACKOFF_SEC * attempt
                    logger.warning(f"Notifier: Telegram rate-limited (attempt {attempt}/{_MAX_ATTEMPTS}), retrying in {delay:.1f}s")
                    await asyncio.sleep(delay)
                    continue
                logger.warning(f"Notifier: Telegram HTTP {resp.status}: {body[:200]}")
                return
        except Exception as e:
            if attempt < _MAX_ATTEMPTS:
                logger.warning(f"Notifier: Telegram send failed (attempt {attempt}/{_MAX_ATTEMPTS}): {type(e).__name__}: {e}, retrying")
                await asyncio.sleep(_BASE_BACKOFF_SEC * attempt)
                continue
            logger.warning(f"Notifier: Telegram send failed permanently after {_MAX_ATTEMPTS} attempts: {type(e).__name__}: {e}")


def alert(message: str) -> None:
    """
    Schedule delivery to every configured channel and return immediately.
    Call this from anywhere in the bot without `await` -- it must never add
    latency to the trading loop. Requires a running event loop (safe to call
    from inside bot.run()); for a call site outside any loop, use
    alert_and_wait() instead. Safe to call even with zero channels
    configured (becomes a no-op).
    """
    try:
        loop = asyncio.get_running_loop()
        if config.discord_webhook_url:
            loop.create_task(_send_discord(message))
        if config.telegram_bot_token and config.telegram_chat_id:
            loop.create_task(_send_telegram(message))
    except RuntimeError:
        pass


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
