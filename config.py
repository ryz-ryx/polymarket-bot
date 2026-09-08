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

config = BotConfig()