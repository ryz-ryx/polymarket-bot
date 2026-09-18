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
    # Public Polygon RPC by default -- fine for occasional redeemPositions calls
    # (low volume, not latency-sensitive like order placement). polygon-rpc.com
    # and llamarpc both failed to connect/resolve when actually tested from this
    # environment; 1rpc.io/matic verified working (chain_id 137 confirmed).
    # Override with a dedicated RPC (Alchemy/Infura/etc.) if this one degrades.
    polygon_rpc_url: str = os.getenv("POLYGON_RPC_URL", "https://1rpc.io/matic")
    
    target_asset: str = "BTC"
    # Comma-separated list of assets to trade concurrently, e.g. "BTC,ETH,SOL".
    # Unset (default) preserves existing single-asset behavior (just target_asset).
    target_assets: List[str] = [
        a.strip().upper() for a in os.getenv("TARGET_ASSETS", "").split(",") if a.strip()
    ] or [target_asset]
    # Council recommendation 2026-09-18: only BTC has shown a positive profit rate
    # (+6.4% vs ETH -27.6% / SOL -8.6% on the tracked paper sample). Any asset not in
    # this allowlist is dropped from target_assets no matter what TARGET_ASSETS says
    # (so a stale Railway env var can't silently re-enable ETH/SOL). Add an asset here
    # only after it clears the forward-test bar (scripts/forward_test_report.py).
    proven_assets: List[str] = [
        a.strip().upper() for a in os.getenv("PROVEN_ASSETS", "BTC").split(",") if a.strip()
    ]

    # TOTAL capital available across ALL traded assets combined (real funded wallet
    # balance, not a per-asset allowance) -- e.g. one $25 USDC wallet shared by
    # BTC/ETH/SOL, not $25 each. bot.py divides this across concurrently-traded
    # assets rather than giving each its own independent copy of the full amount.
    starting_balance_usd: float = float(os.getenv("STARTING_BALANCE_USD", "25.0"))
    max_portfolio_drawdown_pct: float = float(os.getenv("MAX_PORTFOLIO_DRAWDOWN_PCT", "0.25"))
    max_position_usd: float = float(os.getenv("MAX_POSITION_USD", "5.0"))
    max_daily_loss_usd: float = float(os.getenv("MAX_DAILY_LOSS_USD", "10.0"))
    max_portfolio_daily_loss_usd: float = float(os.getenv("MAX_PORTFOLIO_DAILY_LOSS_USD", "20.0"))
    slippage_tolerance: float = float(os.getenv("SLIPPAGE_TOLERANCE", "0.02"))
    # 2026-09-18: lowered 0.125->0.06 after Monte Carlo resampling of the bot's own actual
    # (stake, outcome) pairs (see Claude outputs/profit_rate_analysis.md) showed a ~66%
    # chance of a negative portfolio-level run over a 90-trade stretch even under the
    # measured edge -- classic symptom of sizing against an edge estimate that's still
    # noisy (Sharpe-like ratios near zero at this sample size). Kelly sizing assumes the
    # edge/probability estimate is exact; when it isn't, fractional Kelly needs to shrink
    # further than usual. This trades some expected growth rate for a much lower chance of
    # tripping the portfolio drawdown breaker before enough data accumulates to know if the
    # edge is real. Revisit upward only after a multi-day BTC-only sample confirms the edge
    # holds (see roadmap.md Phase 2/3).
    kelly_fraction: float = float(os.getenv("KELLY_FRACTION", "0.06"))
    # Staged live rollout: once paper_trading is turned off, each asset's
    # OrderExecutor auto-halts live order placement after this many real
    # fills, falling back to paper mode until manually reset (restart the
    # asset's live_trade_count.json to 0, or bump this). Forces a deliberate
    # look at real execution/redemption behavior on a handful of small real
    # trades before scaling up, instead of unlimited live trading the moment
    # PAPER_TRADING flips.
    live_test_max_trades: int = int(os.getenv("LIVE_TEST_MAX_TRADES", "5"))
    # Paper-trading realism: a real order incurs a real network round-trip
    # (sign + POST to CLOB + match) before it fills, during which the book can
    # move. Paper trading used to fill instantly against the same book snapshot
    # the signal was decided on -- this simulates that round-trip delay, then
    # re-fetches the book fresh before computing the paper fill, so paper PnL
    # reflects the same adverse-selection risk a real order would face. 300ms
    # is a conservative estimate for a signed CLOB order round-trip; there's no
    # live order history yet to measure the real figure against.
    sim_exec_latency_ms: int = int(os.getenv("SIM_EXEC_LATENCY_MS", "300"))
    # Polymarket's CLOB rejects market orders below this size -- paper trading
    # had no floor, so Kelly sizing could "fill" a $0.30 paper order that a
    # real order would never be accepted for. $1.00 is Polymarket's documented
    # CLOB minimum order size.
    min_order_usd: float = float(os.getenv("MIN_ORDER_USD", "1.0"))
    # Paper-trading realism: refuse simulated fills with less than this many seconds left
    # in the window. The two earliest local paper fills were at tau=1.3s and 14s with
    # p_model=1.0 against a stale-looking ask -- a real FOK order (sign + POST + match)
    # can't reliably fill that late, so paper PnL there is phantom. Paper only; the frozen
    # strategy and live path are untouched. Set 0 to disable.
    paper_min_entry_tau_sec: float = float(os.getenv("PAPER_MIN_ENTRY_TAU_SEC", "15.0"))
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

    # Shared secret for the dashboard's POST /api/control endpoint (pause/resume).
    # Left unset by default -- an unset secret means POST /api/control is refused
    # entirely (fail closed), not left open.
    control_secret: str = os.getenv("CONTROL_SECRET", "")

config = BotConfig()
# Enforce the proven-asset allowlist (see proven_assets above). Fail closed to BTC
# if the filter would otherwise leave nothing to trade.
_filtered_assets = [a for a in config.target_assets if a in config.proven_assets]
config.target_assets = _filtered_assets or [config.target_asset]
