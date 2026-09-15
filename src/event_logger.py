import os
import csv
import time
from typing import Dict, Any

EVENT_LOG = "data/trade_events.csv"

class TradeEventLogger:
    """
    Logs every signal evaluation funnel event:
    status in:
      - 'NO_SIGNAL'
      - 'BLOCKED_NOISE' (|z| < min_abs_z)
      - 'BLOCKED_COOLDOWN' (trade within last 15s)
      - 'BLOCKED_RISK' (daily loss breaker active)
      - 'BLOCKED_PHANTOM' (direct book has 0 resting asks)
      - 'BLOCKED_HURDLE' (real edge < direct book spread hurdle)
      - 'EXECUTED' (order placed on direct resting book)
    """
    HEADER = [
        "timestamp", "window_id", "tau_sec", "outcome",
        "z", "p_model", "direct_ask", "direct_spread",
        "real_edge", "hurdle", "status", "size_usd"
    ]

    def __init__(self, log_path: str = EVENT_LOG):
        self.log_path = log_path
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        if not os.path.exists(self.log_path):
            with open(self.log_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(self.HEADER)
        else:
            self._ensure_header()

    def _ensure_header(self):
        """
        See EmpiricalCalibrator._ensure_header() for why this matters: if this log
        path gets deleted while a process still holds this logger alive, the next
        append-mode log_event() call silently recreates the file with pure data rows
        and no header -- every downstream reader (dashboard.py's tail-read parsing)
        then mistakes the first data row for the header and misparses every field.
        """
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

    def log_event(
        self,
        window_id: int,
        tau_sec: float,
        outcome: str,
        z: float,
        p_model: float,
        direct_ask: float,
        direct_spread: float,
        real_edge: float,
        hurdle: float,
        status: str,
        size_usd: float = 0.0
    ):
        try:
            with open(self.log_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    time.time(),
                    window_id,
                    round(tau_sec, 2),
                    outcome,
                    round(z, 4) if z is not None else "",
                    round(p_model, 4) if p_model is not None else "",
                    round(direct_ask, 4) if direct_ask is not None else "",
                    round(direct_spread, 4) if direct_spread is not None else "",
                    round(real_edge, 4) if real_edge is not None else "",
                    round(hurdle, 4) if hurdle is not None else "",
                    status,
                    round(size_usd, 2)
                ])
        except Exception:
            pass
