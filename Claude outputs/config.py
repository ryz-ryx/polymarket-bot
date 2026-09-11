import os
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

class BotConfig(BaseModel):
    paper_trading: bool = os.getenv("PAPER_TRADING", "true").lower() == "true"
    poly_private_key: str = os.getenv("POLY_PRIVATE_KEY", "")
    poly_clob_api_key: str = os.getenv("POLY_CLOB_API_KEY", "")
    poly_clob_secret: str = os.getenv("POLY_CLOB_SECRET", "")
    poly_clob_passphrase: str = os.getenv("POLY_CLOB_PASSPHRASE", "")
    
    chain_id: int = int(os.getenv("CHAIN_ID", "137"))
    clob_api_host: str = os.getenv("CLOB_API_HOST", "https://clob.polymarket.com")
    clob_ws_host: str = os.getenv("CLOB_WS_HOST", "wss://ws-subscriptions-clob.polymarket.com/ws/market")
    
    target_asset: str = os.getenv("TARGET_ASSET", "BTC").upper()
    max_position_usd: float = float(os.getenv("MAX_POSITION_USD", "25.0"))
    max_daily_loss_usd: float = float(os.getenv("MAX_DAILY_LOSS_USD", "50.0"))
    slippage_tolerance: float = float(os.getenv("SLIPPAGE_TOLERANCE", "0.02"))
    kelly_fraction: float = float(os.getenv("KELLY_FRACTION", "0.25"))
    spot_exchange: str = os.getenv("SPOT_EXCHANGE", "binance").lower()

    # Discord / Telegram alerts (both optional -- unset means that channel is silently
    # disabled). Never commit real values here; put them in your local .env only.
    discord_webhook_url: str = os.getenv("DISCORD_WEBHOOK_URL", "")
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    notify_on_startup: bool = os.getenv("NOTIFY_ON_STARTUP", "true").lower() == "true"
    notify_on_connectivity_failure: bool = os.getenv("NOTIFY_ON_CONNECTIVITY_FAILURE", "true").lower() == "true"
    notify_on_trade: bool = os.getenv("NOTIFY_ON_TRADE", "true").lower() == "true"
    notify_on_settlement: bool = os.getenv("NOTIFY_ON_SETTLEMENT", "true").lower() == "true"
    notify_on_rollup: bool = os.getenv("NOTIFY_ON_ROLLUP", "true").lower() == "true"
    notify_on_crash: bool = os.getenv("NOTIFY_ON_CRASH", "true").lower() == "true"

config = BotConfig()