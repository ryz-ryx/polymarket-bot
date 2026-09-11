import asyncio
import sys
from loguru import logger
from src.bot import Polymarket5mBot
from src import notifier
from config import config

if __name__ == "__main__":
    # Force UTF-8 on Windows console to support status emoji cleanly
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    logger.remove()
    logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>", level="INFO")

    bot = Polymarket5mBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
    except Exception as e:
        # This is exactly the failure mode alerts exist for: the bot dies while
        # nobody's watching the terminal. Best-effort ping before exiting -- if
        # neither channel is configured this is a silent no-op, and if the alert
        # itself fails it must not mask or replace the original crash traceback.
        logger.error(f"Bot crashed: {type(e).__name__}: {e}")
        if config.notify_on_crash:
            try:
                asyncio.run(notifier.alert_and_wait(f"\U0001F480 Polymarket5mBot CRASHED: {type(e).__name__}: {e}"))
            except Exception:
                pass
        raise
