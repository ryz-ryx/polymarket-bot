import os
from typing import List
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

    @property
    def target_assets(self) -> List[str]:
        raw = os.getenv("TARGET_ASSETS")
        if raw:
            assets = [a.strip().upper() for a in raw.split(",") if a.strip()]
            if assets:
                return assets
        return ["BTC", "ETH", "SOL"]

    starting_balance_usd: float = float(os.getenv("STARTING_BALANCE_USD", "70.0"))
    max_portfolio_drawdown_pct: float = float(os.getenv("MAX_PORTFOLIO_DRAWDOWN_PCT", "0.25"))
    max_position_usd: float = float(os.getenv("MAX_POSITION_USD", "5.0"))
    max_daily_loss_usd: float = float(os.getenv("MAX_DAILY_LOSS_USD", "10.0"))
    max_portfolio_daily_loss_usd: float = float(os.getenv("MAX_PORTFOLIO_DAILY_LOSS_USD", "20.0"))
    slippage_tolerance: float = float(os.getenv("SLIPPAGE_TOLERANCE", "0.02"))
    kelly_fraction: float = float(os.getenv("KELLY_FRACTION", "0.125"))
    spot_exchange: str = os.getenv("SPOT_EXCHANGE", "binance").lower()

    def get_max_position_usd(self, asset: str) -> float:
        env_val = os.getenv(f"MAX_POSITION_USD_{asset.upper()}")
        if env_val:
            try:
                return float(env_val)
            except ValueError:
                pass
        return self.max_position_usd

    def get_max_daily_loss_usd(self, asset: str) -> float:
        env_val = os.getenv(f"MAX_DAILY_LOSS_USD_{asset.upper()}")
        if env_val:
            try:
                return float(env_val)
            except ValueError:
                pass
        # By default, cap ETH at $5.00 daily loss to prevent dragging down portfolio circuit breaker
        if asset.upper() == "ETH":
            return min(self.max_daily_loss_usd, 5.0)
        return self.max_daily_loss_usd

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

    # Shared secret for the dashboard's POST /api/control endpoint (pause/resume).
    # Left unset by default -- an unset secret means POST /api/control is refused
    # entirely (fail closed), not left open.
    control_secret: str = os.getenv("CONTROL_SECRET", "")

config = BotConfig()