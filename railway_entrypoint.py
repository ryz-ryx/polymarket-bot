"""
Railway entrypoint: runs the trading bot and the dashboard/healthcheck HTTP
server in a single process, so both share one filesystem and one Volume.

Local/Windows use is unaffected -- run.py is untouched, and dashboard.py can
still be run standalone if you want the dashboard without the bot.
"""
import asyncio
import os
import subprocess
import sys
import threading
import time

from loguru import logger

import dashboard
from src.bot import Polymarket5mBot
from src import notifier
from src import chat_bot
from src.reset_state import reset_all_state_if_requested
from config import config

def _supervise_l2_collector():
    """Observation-only order-book collector (scripts/collect_l2.py) as an isolated child
    process: a crash or hang there cannot touch the bot. Restarts with backoff. Disable with
    RUN_L2_COLLECTOR=0."""
    delay = 5.0
    while True:
        started = time.time()
        try:
            subprocess.Popen([sys.executable, "scripts/collect_l2.py"]).wait()
        except Exception as e:
            logger.warning(f"L2 collector supervisor: {type(e).__name__}: {e}")
        delay = 5.0 if time.time() - started > 120 else min(delay * 2, 300.0)
        time.sleep(delay)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    logger.remove()
    logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>", level="INFO")
    logger.add("logs/bot_{time:YYYY-MM-DD}.log", rotation="00:00", retention="14 days", level="DEBUG", encoding="utf-8")

    reset_all_state_if_requested()

    # Dashboard + /healthz run in a daemon thread so Railway's healthcheck
    # has something to hit immediately, without needing a second service.
    dash_thread = threading.Thread(target=dashboard.run, daemon=True)
    dash_thread.start()

    # Telegram chat interface (status queries + pause/resume) runs in its own
    # daemon thread, calling the dashboard's own HTTP API on localhost -- see
    # src/chat_bot.py. A no-op if TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID are unset.
    chat_thread = threading.Thread(target=chat_bot.run, daemon=True)
    chat_thread.start()

    if os.getenv("RUN_L2_COLLECTOR", "1") != "0":
        threading.Thread(target=_supervise_l2_collector, daemon=True).start()

    bot = Polymarket5mBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
    except Exception as e:
        logger.error(f"Bot crashed: {type(e).__name__}: {e}")
        if config.notify_on_crash:
            try:
                asyncio.run(notifier.alert_and_wait(f"\U0001F480 Polymarket5mBot CRASHED: {type(e).__name__}: {e}"))
            except Exception:
                pass
        raise
