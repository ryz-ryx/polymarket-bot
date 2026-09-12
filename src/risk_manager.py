import os
import json
import datetime
from typing import Optional
from loguru import logger
from src.breaker_reset import read_reset_request

class RiskManager:
    """
    Capital preservation and Kelly sizing.
    Supports asymmetric positive-expectation bets where win_probability <= 0.50
    as long as odds provide positive mathematical expectation (b * p - q > 0).
    """
    def __init__(
        self,
        max_position_usd: float = 25.0,
        max_daily_loss_usd: float = 150.0,
        kelly_fraction: float = 0.25,
        state_file: Optional[str] = None
    ):
        self.max_position_usd = max_position_usd
        self.max_daily_loss_usd = max_daily_loss_usd
        self.kelly_fraction = kelly_fraction
        self.state_file = state_file
        
        self.daily_pnl = 0.0
        self.circuit_breaker_triggered = False
        self.current_day = datetime.datetime.now(datetime.timezone.utc).date()
        self.open_positions: dict[str, dict] = {}
        self._last_breaker_reset_ack = 0.0

        if self.state_file:
            self._load_state()

    def _load_state(self):
        if not self.state_file or not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            saved_day_str = data.get("current_day")
            today_str = str(self.current_day)
            if saved_day_str == today_str:
                self.daily_pnl = float(data.get("daily_pnl", 0.0))
                self.circuit_breaker_triggered = bool(data.get("circuit_breaker_triggered", False))
                logger.info(
                    f"RiskManager: Restored state from {self.state_file} | "
                    f"Day: {saved_day_str} | Daily PnL: ${self.daily_pnl:+.2f} | "
                    f"Circuit Breaker: {'TRIPPED' if self.circuit_breaker_triggered else 'ACTIVE'}"
                )
            else:
                logger.info(
                    f"RiskManager: State in {self.state_file} was from previous day ({saved_day_str} vs {today_str}). "
                    f"Starting fresh for today."
                )
                self._save_state()
        except Exception as e:
            logger.warning(f"RiskManager: Failed to load state from {self.state_file} ({e}). Starting with fresh state.")

    def _save_state(self):
        if not self.state_file:
            return
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            tmp_file = f"{self.state_file}.tmp"
            payload = {
                "current_day": str(self.current_day),
                "daily_pnl": self.daily_pnl,
                "circuit_breaker_triggered": self.circuit_breaker_triggered,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_file, self.state_file)
        except Exception as e:
            logger.error(f"RiskManager: Failed to atomically save state to {self.state_file}: {e}")

    def _check_day_rollover(self):
        today = datetime.datetime.now(datetime.timezone.utc).date()
        if today != self.current_day:
            logger.info(f"RiskManager: Day rollover from {self.current_day} to {today}. Resetting daily PnL and circuit breaker.")
            self.current_day = today
            self.daily_pnl = 0.0
            self.circuit_breaker_triggered = False
            self._save_state()

    def _check_breaker_reset_request(self):
        if not self.circuit_breaker_triggered:
            return
        req = read_reset_request()
        if req.get("requested_at", 0.0) > self._last_breaker_reset_ack:
            self._last_breaker_reset_ack = req["requested_at"]
            self.circuit_breaker_triggered = False
            self._save_state()
            logger.warning(
                f"RiskManager: Daily circuit breaker manually cleared by request from "
                f"{req.get('updated_by')!r}. Daily PnL still ${self.daily_pnl:+.2f} -- "
                f"will re-trip immediately on the next check if still past the loss floor."
            )

    def can_trade(self) -> bool:
        self._check_day_rollover()
        self._check_breaker_reset_request()
        if self.circuit_breaker_triggered:
            return False
        if self.daily_pnl <= -self.max_daily_loss_usd:
            self.circuit_breaker_triggered = True
            self._save_state()
            logger.critical(f"Circuit breaker tripped! Daily loss reached {self.daily_pnl:.2f} USD.")
            return False
        return True

    def calculate_position_size(self, win_probability: float, odds: float, bankroll: float, confidence_weight: float = 1.0) -> float:
        """
        Full general Kelly criterion:
        f* = (b * p - q) / b
        Where:
          b = net odds = (payout / stake) - 1
          p = win_probability
          q = 1 - p
        Allows cheap longshots (p < 0.50) if expected value is positive (b*p > q).

        `confidence_weight` (from EmpiricalCalibrator.get_confidence_weight(), 0-1) scales
        kelly_fraction directly. The calibrator already shrinks win_probability itself toward
        the market price when the model hasn't proven it beats the market -- but that alone
        leaves bet SIZE unchanged even during a cold streak. Scaling kelly_fraction too means
        stakes shrink automatically right when the model's edge is least trustworthy, instead
        of only the probability estimate becoming more conservative.
        """
        self._check_day_rollover()
        if not self.can_trade() or win_probability <= 0.01:
            return 0.0

        b = odds - 1.0
        if b <= 0:
            return 0.0

        q = 1.0 - win_probability
        # Kelly %: positive edge exists if b * p > q
        kelly_pct = (b * win_probability - q) / b
        if kelly_pct <= 0:
            return 0.0

        effective_kelly_fraction = self.kelly_fraction * max(0.0, min(1.0, confidence_weight))
        suggested_size = bankroll * (kelly_pct * effective_kelly_fraction)
        final_size = min(suggested_size, self.max_position_usd)
        logger.info(
            f"Risk Check: Kelly % = {kelly_pct*100:.1f}%, Confidence = {confidence_weight:.2f}, "
            f"Sized: ${final_size:.2f} USD"
        )
        return round(final_size, 2)

    def record_trade_result(self, pnl: float):
        self._check_day_rollover()
        self.daily_pnl += pnl
        logger.info(f"Trade result: PnL {pnl:+.2f} USD | Total Day PnL: {self.daily_pnl:+.2f} USD")
        if not self.circuit_breaker_triggered and self.daily_pnl <= -self.max_daily_loss_usd:
            self.circuit_breaker_triggered = True
            logger.critical("Circuit breaker tripped! New trade entries halted for today.")
        self._save_state()
