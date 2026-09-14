import os
import json
import time
from typing import Dict, Any, Optional, List
from loguru import logger
from config import config
from src.strategies.claud_quant import estimate_taker_fee_fraction

POSITIONS_FILE = "data/open_positions.json"

class OrderExecutor:
    """
    Handles order execution on Polymarket CLOB.
    Features:
    - Persistent disk-backed open positions (data/open_positions.json) across restarts.
    - Honest deferred settlement math against on-chain outcomePrices.
    """
    def __init__(self, paper_trading: bool = True, state_file: str = POSITIONS_FILE, asset: str = "BTC", initial_balance_usd: Optional[float] = None):
        self.paper_trading = paper_trading
        self.asset = asset.upper()
        self.simulated_balance = initial_balance_usd if initial_balance_usd is not None else config.starting_balance_usd
        self.state_file = state_file
        asset_suffix = "" if self.asset == "BTC" else f"_{self.asset.lower()}"
        self.fills_log_path = f"data/fills_log{asset_suffix}.jsonl"
        self.clob_client = None
        self.open_paper_positions: List[Dict[str, Any]] = []
        self.open_live_positions: List[Dict[str, Any]] = []
        self.live_positions_file = f"data/open_live_positions{asset_suffix}.json"
        self._default_balance = self.simulated_balance

        self._load_positions()
        self._load_live_positions()

        if not self.paper_trading:
            self._init_live_client()

    def _load_positions(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.open_paper_positions = data.get("positions", [])
                    self.simulated_balance = float(data.get("simulated_balance", self._default_balance))
                    logger.info(f"OrderExecutor [{self.asset}]: Restored {len(self.open_paper_positions)} open positions from disk. Balance: ${self.simulated_balance:.2f}")
            except Exception as e:
                logger.error(f"Failed to load positions from disk: {e}")

    def _load_live_positions(self):
        if os.path.exists(self.live_positions_file):
            try:
                with open(self.live_positions_file, "r", encoding="utf-8") as f:
                    self.open_live_positions = json.load(f).get("positions", [])
                    if self.open_live_positions:
                        logger.warning(
                            f"OrderExecutor [{self.asset}]: Restored {len(self.open_live_positions)} "
                            f"open LIVE (real-money) positions from disk -- these are not settled "
                            f"automatically yet, see settle_window_positions()."
                        )
            except Exception as e:
                logger.error(f"Failed to load live positions from disk: {e}")

    def _save_live_positions(self):
        os.makedirs(os.path.dirname(self.live_positions_file), exist_ok=True)
        try:
            tmp_file = f"{self.live_positions_file}.tmp"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump({"positions": self.open_live_positions, "updated_at": time.time()}, f, indent=2)
            os.replace(tmp_file, self.live_positions_file)
        except Exception as e:
            logger.error(f"Failed to persist live positions to disk: {e}")

    def _save_positions(self):
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        try:
            tmp_file = f"{self.state_file}.tmp"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump({
                    "positions": self.open_paper_positions,
                    "simulated_balance": self.simulated_balance,
                    "updated_at": time.time()
                }, f, indent=2)
            os.replace(tmp_file, self.state_file)
        except Exception as e:
            logger.error(f"Failed to persist positions to disk: {e}")

    def _log_fill(self, fill_event: Dict[str, Any]):
        os.makedirs(os.path.dirname(self.fills_log_path), exist_ok=True)
        try:
            with open(self.fills_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(fill_event) + "\n")
        except Exception as e:
            logger.error(f"Failed to append to fills log {self.fills_log_path}: {e}")

    def _init_live_client(self):
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds

            # Level 1 auth (wallet key only) is enough to build+sign orders and derive
            # API creds. Level 2 auth (API key/secret/passphrase) is required to actually
            # POST an order or query real balance/allowance -- derive it automatically
            # from the wallet key if it wasn't supplied via env, rather than requiring
            # POLY_CLOB_API_KEY/SECRET/PASSPHRASE to be manually provisioned first. This
            # is the standard Polymarket CLOB bootstrap flow.
            self.clob_client = ClobClient(
                host=config.clob_api_host,
                key=config.poly_private_key,
                chain_id=config.chain_id,
            )

            if config.poly_clob_api_key:
                creds = ApiCreds(
                    api_key=config.poly_clob_api_key,
                    api_secret=config.poly_clob_secret,
                    api_passphrase=config.poly_clob_passphrase,
                )
            else:
                creds = self.clob_client.create_or_derive_api_creds()
                logger.info(f"OrderExecutor [{self.asset}]: Auto-derived Level 2 API creds from wallet key.")
            self.clob_client.set_api_creds(creds)

            logger.warning(
                f"OrderExecutor [{self.asset}]: Initialized LIVE Polymarket CLOB client "
                f"(Level 2 auth ready) -- real orders will be submitted for real funds."
            )
        except Exception as e:
            logger.error(f"Failed to initialize live CLOB client: {e}. Defaulting to Paper Trading.")
            self.paper_trading = True
            self.clob_client = None

    def get_live_collateral_balance(self) -> Optional[float]:
        """
        Real on-chain USDC (collateral) balance + allowance from the CLOB API, in
        whole USD -- NOT the simulated_balance ledger. Read-only, safe to call anytime
        a live client is initialized. Returns None if unavailable (no live client, or
        the request failed) rather than a fake/stale number.
        """
        if self.clob_client is None:
            return None
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            resp = self.clob_client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            raw_balance = resp.get("balance") if isinstance(resp, dict) else None
            if raw_balance is None:
                return None
            # USDC is 6-decimal; the CLOB API returns raw base units as a string.
            return float(raw_balance) / 1_000_000.0
        except Exception as e:
            logger.error(f"OrderExecutor [{self.asset}]: Failed to fetch live collateral balance: {e}")
            return None

    async def execute_trade(
        self,
        window_id: int,
        slug: str,
        token_id: str,
        outcome: str,
        amount_usd: float,
        price: float
    ) -> Dict[str, Any]:
        if self.paper_trading:
            fee_paid = amount_usd * estimate_taker_fee_fraction(price)
            required_funds = amount_usd + fee_paid

            # Affordability guard: prevent simulated bankroll from falling into negative balance
            if required_funds > self.simulated_balance:
                logger.warning(
                    f"[PAPER EXEC BLOCKED] Insufficient balance for {self.asset}: "
                    f"Required ${required_funds:.2f} (${amount_usd:.2f} + ${fee_paid:.3f} fee) > "
                    f"Available ${self.simulated_balance:.2f}"
                )
                return {
                    "status": "BLOCKED_INSUFFICIENT_FUNDS",
                    "required": required_funds,
                    "available": self.simulated_balance
                }

            shares = amount_usd / max(price, 0.01)
            self.simulated_balance -= required_funds
            now_ts = time.time()
            position = {
                "window_id": window_id,
                "slug": slug,
                "token_id": token_id,
                "outcome": outcome,
                "amount_usd": amount_usd,
                "price": price,
                "shares": shares,
                "fee_paid": fee_paid,
                "timestamp": now_ts
            }
            self.open_paper_positions.append(position)
            self._save_positions()

            self._log_fill({
                "ts": now_ts,
                "asset": self.asset,
                "type": "BUY",
                "window_id": window_id,
                "slug": slug,
                "outcome": outcome,
                "price": price,
                "size_usd": amount_usd,
                "shares": shares,
                "fee_paid": fee_paid,
                "balance_after": self.simulated_balance
            })

            logger.info(
                f"[PAPER EXEC] BUY {outcome} (Win {window_id}) | "
                f"Size: ${amount_usd:.2f} @ {price:.3f} ({shares:.2f} shares) | Fee: ${fee_paid:.3f} | "
                f"Remaining Cash: ${self.simulated_balance:.2f}"
            )
            return {
                "status": "FILLED_PAPER",
                "position": position
            }
        else:
            if self.clob_client is None:
                logger.error(
                    f"[LIVE EXEC BLOCKED] No live CLOB client for {self.asset} -- "
                    f"cannot place real order (BUY {outcome} ${amount_usd:.2f})."
                )
                return {"status": "BLOCKED_NO_LIVE_CLIENT"}

            try:
                from py_clob_client.clob_types import MarketOrderArgs, OrderType
                from py_clob_client.order_builder.constants import BUY

                # FOK (Fill-Or-Kill): matches the strategy's own assumption -- it already
                # walked the book (simulate_walk_book in bot.py) to confirm this size fills
                # at this price before calling execute_trade at all, so either it fills now
                # at that confirmed price or the order is killed outright. No resting orders.
                order_args = MarketOrderArgs(
                    token_id=token_id,
                    amount=round(amount_usd, 2),
                    side=BUY,
                    price=price,
                    order_type=OrderType.FOK,
                )
                signed_order = self.clob_client.create_market_order(order_args)
                resp = self.clob_client.post_order(signed_order, OrderType.FOK)
            except Exception as e:
                logger.error(
                    f"[LIVE EXEC ERROR] Order placement failed for {self.asset} "
                    f"(BUY {outcome} ${amount_usd:.2f} @ {price:.3f}): {type(e).__name__}: {e}"
                )
                return {"status": "LIVE_EXEC_ERROR", "error": str(e)}

            success = bool(resp.get("success")) if isinstance(resp, dict) else False
            if not success:
                err = resp.get("errorMsg") if isinstance(resp, dict) else str(resp)
                logger.error(f"[LIVE EXEC REJECTED] {self.asset} order rejected: {err} | raw={resp}")
                return {"status": "LIVE_EXEC_REJECTED", "error": err, "raw_response": resp}

            order_id = resp.get("orderID") if isinstance(resp, dict) else None
            now_ts = time.time()
            position = {
                "window_id": window_id,
                "slug": slug,
                "token_id": token_id,
                "outcome": outcome,
                "amount_usd": amount_usd,
                "price": price,
                "order_id": order_id,
                "timestamp": now_ts
            }
            # Kept separate from open_paper_positions -- these track real money and
            # must never be settled/corrected by the paper-settlement math in
            # settle_window_positions(), which is a simulated-ledger credit, not a
            # real redemption. Real settlement (claiming payout for a resolved
            # position) is NOT implemented yet -- see settle_window_positions().
            self.open_live_positions.append(position)
            self._save_live_positions()

            self._log_fill({
                "ts": now_ts,
                "asset": self.asset,
                "type": "LIVE_BUY",
                "window_id": window_id,
                "slug": slug,
                "outcome": outcome,
                "price": price,
                "size_usd": amount_usd,
                "order_id": order_id,
                "raw_response": resp
            })

            logger.warning(
                f"[LIVE EXEC FILLED] REAL ORDER: BUY {outcome} (Win {window_id}) | "
                f"${amount_usd:.2f} @ {price:.3f} | orderID={order_id}"
            )
            return {
                "status": "FILLED_LIVE",
                "order_id": order_id,
                "position": position,
                "raw_response": resp
            }

    def settle_window_positions(
        self,
        window_id: int,
        realized_up: int,
        source: str = "POLYMARKET"
    ) -> float:
        if not self.paper_trading:
            # NOT a no-op by accident -- real settlement (claiming/redeeming a resolved
            # position for actual USDC) is genuinely unimplemented. Polymarket requires
            # an on-chain redeemPositions call against the CTF/NegRiskAdapter contract,
            # which is a raw web3 transaction, not a CLOB REST endpoint -- unbuilt.
            # Surface that loudly instead of silently pretending settlement happened.
            live_here = [p for p in self.open_live_positions if p["window_id"] == window_id]
            if live_here:
                logger.critical(
                    f"[LIVE SETTLEMENT UNIMPLEMENTED] {self.asset} window {window_id} resolved "
                    f"({'UP' if realized_up == 1 else 'DOWN'}) with {len(live_here)} open real-money "
                    f"position(s) still tracked -- these are NOT auto-redeemed. Check/claim them "
                    f"manually on polymarket.com until on-chain redemption is built."
                )
            return 0.0

        settling = [p for p in self.open_paper_positions if p["window_id"] == window_id]
        if not settling:
            return 0.0

        now_ts = time.time()
        total_window_pnl = 0.0
        for pos in settling:
            is_win = (pos["outcome"] == "YES" and realized_up == 1) or (pos["outcome"] == "NO" and realized_up == 0)
            pos_slug = pos.get("slug", "")
            if is_win:
                payout = pos["shares"] * 1.0
                net_profit = payout - pos["amount_usd"]
                self.simulated_balance += payout
                total_window_pnl += net_profit

                self._log_fill({
                    "ts": now_ts,
                    "asset": self.asset,
                    "type": "WIN",
                    "window_id": window_id,
                    "slug": pos_slug,
                    "outcome": pos["outcome"],
                    "cost": pos["amount_usd"],
                    "payout": payout,
                    "net_pnl": net_profit,
                    "source": source,
                    "balance_after": self.simulated_balance
                })

                logger.info(
                    f"🎉 [PAPER SETTLEMENT - WIN via {source}] {pos['outcome']} (Win {window_id}) | "
                    f"Cost: ${pos['amount_usd']:.2f} | Payout: ${payout:.2f} | Net: +${net_profit:.2f} | "
                    f"New Bankroll: ${self.simulated_balance:.2f}"
                )
            else:
                net_loss = -pos["amount_usd"]
                total_window_pnl += net_loss

                self._log_fill({
                    "ts": now_ts,
                    "asset": self.asset,
                    "type": "LOSS",
                    "window_id": window_id,
                    "slug": pos_slug,
                    "outcome": pos["outcome"],
                    "cost": pos["amount_usd"],
                    "payout": 0.0,
                    "net_pnl": net_loss,
                    "source": source,
                    "balance_after": self.simulated_balance
                })

                logger.info(
                    f"💀 [PAPER SETTLEMENT - LOSS via {source}] {pos['outcome']} (Win {window_id}) | "
                    f"Cost: ${pos['amount_usd']:.2f} | Payout: $0.00 | Net: -${pos['amount_usd']:.2f} | "
                    f"New Bankroll: ${self.simulated_balance:.2f}"
                )

        self.open_paper_positions = [p for p in self.open_paper_positions if p["window_id"] != window_id]
        self._save_positions()
        return total_window_pnl

    def correct_settlement(
        self,
        window_id: int,
        positions_snapshot: List[Dict[str, Any]],
        wrong_realized_up: int,
        correct_realized_up: int
    ) -> float:
        """
        Reverses and re-applies settlement for a window whose Binance-fallback outcome
        guess turned out to disagree with the real on-chain resolution (positions were
        already removed from open_paper_positions by the time fallback settled them, so
        we work from the snapshot taken at fallback time). Applies only the balance
        DELTA between what was actually credited (using wrong_realized_up) and what
        should have been credited (using correct_realized_up). Returns the delta.
        """
        if not positions_snapshot:
            return 0.0

        def total_payout(realized_up: int) -> float:
            total = 0.0
            for pos in positions_snapshot:
                is_win = (pos["outcome"] == "YES" and realized_up == 1) or (pos["outcome"] == "NO" and realized_up == 0)
                total += pos["shares"] * 1.0 if is_win else 0.0
            return total

        wrong_payout = total_payout(wrong_realized_up)
        correct_payout = total_payout(correct_realized_up)
        delta = correct_payout - wrong_payout

        if abs(delta) > 1e-9:
            self.simulated_balance += delta
            self._save_positions()
            self._log_fill({
                "ts": time.time(),
                "asset": self.asset,
                "type": "CORRECTION",
                "window_id": window_id,
                "delta": delta,
                "wrong_realized_up": wrong_realized_up,
                "correct_realized_up": correct_realized_up,
                "balance_after": self.simulated_balance
            })
            logger.warning(
                f"[FALLBACK CORRECTION] Window {window_id}: on-chain outcome ({correct_realized_up}) "
                f"disagreed with Binance fallback guess ({wrong_realized_up}). "
                f"Reversing incorrect payout: {delta:+.2f} USD | Corrected Bankroll: ${self.simulated_balance:.2f}"
            )
        return delta
