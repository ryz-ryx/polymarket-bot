import os
import csv
import time
from typing import Optional
from loguru import logger

ARBITRAGE_LOG = "data/arbitrage_scan.csv"


class ArbitrageScanner:
    """
    Shadow-only detector for YES+NO < $1.00 mispricings on Polymarket's binary
    markets. This is the one strategy type prediction-market research identifies
    as riskless rather than probabilistically edged (buying both sides when their
    combined cost is under the guaranteed $1.00 payout). It does NOT place any
    trades -- it only logs whether/how often the opportunity actually exists here,
    so that's answered empirically before any execution logic gets built on top of it.
    """

    HEADER = ["timestamp", "window_id", "yes_ask", "no_ask", "combined", "gross_edge"]

    def __init__(self, log_path: str = ARBITRAGE_LOG):
        self.log_path = log_path
        self._checks = 0
        self._hits = 0
        self._last_summary_log = 0.0
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        if not os.path.exists(self.log_path):
            with open(self.log_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(self.HEADER)
        else:
            self._ensure_header()

    def _ensure_header(self):
        # See EmpiricalCalibrator._ensure_header() -- same class of bug: a file
        # deleted out from under a still-running process gets silently recreated
        # headerless on the next append.
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                first_line = f.readline()
            if first_line.startswith("timestamp,"):
                return
            with open(self.log_path, "r", encoding="utf-8") as f:
                rest = f.read()
            with open(self.log_path, "w", newline="", encoding="utf-8") as f:
                f.write(",".join(self.HEADER) + "\n")
                f.write(rest)
        except Exception:
            pass

    def check(self, window_id: int, yes_ask: Optional[float], no_ask: Optional[float]) -> Optional[float]:
        """
        Returns the gross edge (1.0 - combined) if a mispricing is found, else None.
        Only writes a CSV row when an opportunity is actually found, to avoid
        flooding the log with a row every second for the (expected) common case
        of no opportunity.
        """
        self._checks += 1
        now = time.time()
        if now - self._last_summary_log >= 3600.0:
            hit_rate = (self._hits / self._checks * 100.0) if self._checks else 0.0
            logger.info(
                f"ArbitrageScanner: {self._hits}/{self._checks} checks found YES+NO < $1.00 "
                f"({hit_rate:.3f}%) in the last hour."
            )
            self._checks = 0
            self._hits = 0
            self._last_summary_log = now

        if yes_ask is None or no_ask is None:
            return None

        combined = yes_ask + no_ask
        if combined >= 1.0:
            return None

        self._hits += 1
        gross_edge = 1.0 - combined
        try:
            with open(self.log_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([now, window_id, round(yes_ask, 4), round(no_ask, 4), round(combined, 4), round(gross_edge, 4)])
            logger.info(
                f"ArbitrageScanner: FOUND window {window_id} YES={yes_ask:.4f} + NO={no_ask:.4f} "
                f"= {combined:.4f} < $1.00 (gross edge {gross_edge*100:.2f}%, before fees/slippage)"
            )
        except Exception as e:
            logger.error(f"ArbitrageScanner: failed to log opportunity: {e}")

        return gross_edge
