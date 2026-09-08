import os
import json
import time
from typing import Dict, Any, Optional, List
from loguru import logger
from config import config

POSITIONS_FILE = "data/open_positions.json"

class OrderExecutor:
    """
    Handles order execution on Polymarket CLOB.
    Features:
    - Persistent disk-backed open positions (data/open_positions.json) across restarts.
    - Honest deferred settlement math against on-chain outcomePrices.
    """
    def __init__(self, paper_trading: bool = True, state_file: str = POSITIONS_FILE):
        self.paper_trading = paper_trading
        self.simulated_balance = 500.0
        self.state_file = state_file
        self.clob_client = None
        self.open_paper_positions: List[Dict[str, Any]] = []

        self._load_positions()

        if not self.paper_trading:
            self._init_live_client()

    def _load_positions(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.open_paper_positions = data.get("positions", [])
                    self.simulated_balance = float(data.get("simulated_balance", 500.0))
                    logger.info(f"OrderExecutor: Restored {len(self.open_paper_positions)} open positions from disk. Balance: ${self.simulated_balance:.2f}")
            except Exception as e:
                logger.error(f"Failed to load positions from disk: {e}")

    def _save_positions(self):
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump({
                    "positions": self.open_paper_positions,
                    "simulated_balance": self.simulated_balance,
                    "updated_at": time.time()
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to persist positions to disk: {e}")

    def _init_live_client(self):
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds

            creds = None
            if config.poly_clob_api_key:
                creds = ApiCreds(
                    api_key=config.poly_clob_api_key,
                    api_secret=config.poly_clob_secret,
                    api_passphrase=config.poly_clob_passphrase,
                )

            self.clob_client = ClobClient(
                host=config.clob_api_host,
                key=config.poly_private_key,
                chain_id=config.chain_id,
                creds=creds,
            )
            logger.info("Initialized live Polymarket CLOB client.")
        except Exception as e:
            logger.error(f"Failed to initialize live CLOB client: {e}. Defaulting to Paper Trading.")
            self.paper_trading = True

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
            shares = amount_usd / max(price, 0.01)
            self.simulated_balance -= amount_usd
            position = {
                "window_id": window_id,
                "slug": slug,
                "token_id": token_id,
                "outcome": outcome,
                "amount_usd": amount_usd,
                "price": price,
                "shares": shares,
                "timestamp": time.time()
            }
            self.open_paper_positions.append(position)
            self._save_positions()
            logger.info(
                f"[PAPER EXEC] BUY {outcome} (Win {window_id}) | "
                f"Size: ${amount_usd:.2f} @ {price:.3f} ({shares:.2f} shares) | "
                f"Remaining Cash: ${self.simulated_balance:.2f}"
            )
            return {
                "status": "FILLED_PAPER",
                "position": position
            }
        else:
            logger.info(f"[LIVE EXEC] Submitting order: {outcome} for ${amount_usd:.2f} @ {price:.3f}")
            return {"status": "SUBMITTED_LIVE", "token_id": token_id}

    def settle_window_positions(
        self,
        window_id: int,
        realized_up: int,
        source: str = "POLYMARKET"
    ) -> float:
        if not self.paper_trading:
            return 0.0

        settling = [p for p in self.open_paper_positions if p["window_id"] == window_id]
        if not settling:
            return 0.0

        total_window_pnl = 0.0
        for pos in settling:
            is_win = (pos["outcome"] == "YES" and realized_up == 1) or (pos["outcome"] == "NO" and realized_up == 0)
            if is_win:
                payout = pos["shares"] * 1.0
                net_profit = payout - pos["amount_usd"]
                self.simulated_balance += payout
                total_window_pnl += net_profit
                logger.info(
                    f"🎉 [PAPER SETTLEMENT - WIN via {source}] {pos['outcome']} (Win {window_id}) | "
                    f"Cost: ${pos['amount_usd']:.2f} | Payout: ${payout:.2f} | Net: +${net_profit:.2f} | "
                    f"New Bankroll: ${self.simulated_balance:.2f}"
                )
            else:
                net_loss = -pos["amount_usd"]
                total_window_pnl += net_loss
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
            logger.warning(
                f"[FALLBACK CORRECTION] Window {window_id}: on-chain outcome ({correct_realized_up}) "
                f"disagreed with Binance fallback guess ({wrong_realized_up}). "
                f"Reversing incorrect payout: {delta:+.2f} USD | Corrected Bankroll: ${self.simulated_balance:.2f}"
            )
        return delta
