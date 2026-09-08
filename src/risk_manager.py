import datetime
from loguru import logger

class RiskManager:
    """
    Capital preservation and Kelly sizing.
    Supports asymmetric positive-expectation bets where win_probability <= 0.50
    as long as odds provide positive mathematical expectation (b * p - q > 0).
    """
    def __init__(self, max_position_usd: float = 25.0, max_daily_loss_usd: float = 150.0, kelly_fraction: float = 0.25):
        self.max_position_usd = max_position_usd
        self.max_daily_loss_usd = max_daily_loss_usd
        self.kelly_fraction = kelly_fraction
        
        self.daily_pnl = 0.0
        self.circuit_breaker_triggered = False
        self.current_day = datetime.datetime.now(datetime.timezone.utc).date()
        self.open_positions: dict[str, dict] = {}

    def _check_day_rollover(self):
        today = datetime.datetime.now(datetime.timezone.utc).date()
        if today != self.current_day:
            logger.info(f"RiskManager: Day rollover from {self.current_day} to {today}. Resetting daily PnL and circuit breaker.")
            self.current_day = today
            self.daily_pnl = 0.0
            self.circuit_breaker_triggered = False

    def can_trade(self) -> bool:
        self._check_day_rollover()
        if self.circuit_breaker_triggered:
            return False
        if self.daily_pnl <= -self.max_daily_loss_usd:
            self.circuit_breaker_triggered = True
            logger.critical(f"Circuit breaker tripped! Daily loss reached {self.daily_pnl:.2f} USD.")
            return False
        return True

    def calculate_position_size(self, win_probability: float, odds: float, bankroll: float) -> float:
        """
        Full general Kelly criterion:
        f* = (b * p - q) / b
        Where:
          b = net odds = (payout / stake) - 1
          p = win_probability
          q = 1 - p
        Allows cheap longshots (p < 0.50) if expected value is positive (b*p > q).
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

        suggested_size = bankroll * (kelly_pct * self.kelly_fraction)
        final_size = min(suggested_size, self.max_position_usd)
        logger.info(f"Risk Check: Kelly % = {kelly_pct*100:.1f}%, Sized: ${final_size:.2f} USD")
        return round(final_size, 2)

    def record_trade_result(self, pnl: float):
        self._check_day_rollover()
        self.daily_pnl += pnl
        logger.info(f"Trade result: PnL {pnl:+.2f} USD | Total Day PnL: {self.daily_pnl:+.2f} USD")
        if self.daily_pnl <= -self.max_daily_loss_usd:
            self.circuit_breaker_triggered = True
            logger.critical("Circuit breaker tripped! New trade entries halted for today.")
