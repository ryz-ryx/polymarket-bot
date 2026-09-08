import os
import csv
import time
from typing import Optional, List, Tuple, Dict, Any
import numpy as np
from sklearn.isotonic import IsotonicRegression
from loguru import logger

LOG_FILE = "data/calibration_log.csv"
OBSERVATIONS_FILE = "data/pending_window_observations.json"
import json

class EmpiricalCalibrator:
    """
    Tracks and calibrates fat-tailed empirical probabilities:
    1. Buffers ticks throughout active 5m window.
    2. At window settlement, retroactively labels realized_up and flushes to CSV.
    3. Fits IsotonicRegression gated strictly on DISTINCT SETTLED WINDOWS (not raw tick count),
       preventing 1-window degeneracies (e.g. mapping all probabilities to 1.0 or 0.0).
    4. Evaluates both classes (requires seeing at least one UP and one DOWN window) to avoid monotonic collapse.
    """
    def __init__(self, log_path: str = LOG_FILE, observations_path: str = OBSERVATIONS_FILE):
        self.log_path = log_path
        self.observations_path = observations_path
        self.pending_window_observations: List[Dict[str, Any]] = []
        self.isotonic_model: Optional[IsotonicRegression] = None
        self.is_fitted = False

        # pending_window_observations used to be in-memory only, which meant every
        # in-flight window's tick history was silently discarded on restart -- the
        # window's resolution metadata would survive (via bot.py's pending_resolutions
        # persistence) and settle fine financially, but resolve_window() would find zero
        # matching ticks and no-op with no warning, permanently losing that window's
        # calibration data. Verified live: window 5962894 had a real executed trade and
        # a confirmed on-chain resolution, but zero rows in calibration_log.csv, because
        # the bot was restarted mid-window and this buffer was never persisted.
        self._load_pending_observations()

        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        if not os.path.exists(self.log_path):
            with open(self.log_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp", "window_id", "tau_sec", "moneyness", "vol_annualized",
                    "ofi", "z", "p_model", "p_market", "realized_up"
                ])

    def _load_pending_observations(self):
        if os.path.exists(self.observations_path):
            try:
                with open(self.observations_path, "r", encoding="utf-8") as f:
                    self.pending_window_observations = json.load(f)
                    if self.pending_window_observations:
                        logger.info(
                            f"Calibrator: Restored {len(self.pending_window_observations)} buffered tick "
                            f"observations from disk (in-flight window data survives restart now)."
                        )
            except Exception as e:
                logger.error(f"Failed to load pending observations from disk: {e}")

    def _save_pending_observations(self):
        os.makedirs(os.path.dirname(self.observations_path), exist_ok=True)
        try:
            with open(self.observations_path, "w", encoding="utf-8") as f:
                json.dump(self.pending_window_observations, f)
        except Exception as e:
            logger.error(f"Failed to save pending observations to disk: {e}")

    def log_observation(
        self,
        window_id: int,
        tau_sec: float,
        moneyness: float,
        vol_ann: float,
        ofi: float,
        z: float,
        p_model: float,
        p_market: float
    ):
        obs = {
            "timestamp": time.time(),
            "window_id": window_id,
            "tau_sec": round(tau_sec, 2),
            "moneyness": round(moneyness, 6),
            "vol_annualized": round(vol_ann, 4),
            "ofi": round(ofi, 4),
            "z": round(z, 4),
            "p_model": round(p_model, 4),
            "p_market": round(p_market, 4),
        }
        self.pending_window_observations.append(obs)
        self._save_pending_observations()

    def resolve_window(self, window_id: int, strike_k: float, settlement_price: float):
        realized_up = 1 if settlement_price >= strike_k else 0
        matching = [o for o in self.pending_window_observations if o["window_id"] == window_id]

        if not matching:
            logger.warning(
                f"Calibrator: resolve_window({window_id}) found ZERO buffered observations -- "
                f"this window's calibration data is being lost (likely restarted mid-window before "
                f"this fix, or ticks never logged). No CSV row will be written for it."
            )
            return

        try:
            with open(self.log_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                for o in matching:
                    writer.writerow([
                        o["timestamp"],
                        o["window_id"],
                        o["tau_sec"],
                        o["moneyness"],
                        o["vol_annualized"],
                        o["ofi"],
                        o["z"],
                        o["p_model"],
                        o["p_market"],
                        realized_up
                    ])
            logger.info(f"Calibrator: Flushed {len(matching)} observations for window {window_id} (Outcome: {'UP (1)' if realized_up else 'DOWN (0)'})")
        except Exception as e:
            logger.error(f"Error flushing calibration records: {e}")

        self.pending_window_observations = [o for o in self.pending_window_observations if o["window_id"] != window_id]
        self._save_pending_observations()

    def correct_window_outcome(self, window_id: int, correct_realized_up: int) -> int:
        """
        Rewrites the realized_up label for every already-flushed row belonging to
        window_id. Needed because the Binance-fallback settlement estimate (used when
        on-chain resolution doesn't arrive within the poll timeout) can occasionally
        guess the wrong direction; once the real on-chain outcome does arrive, any
        flushed rows with the wrong label would otherwise silently poison the
        calibration training set forever. Returns the number of rows corrected.
        """
        if not os.path.exists(self.log_path):
            return 0

        with open(self.log_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            rows = list(reader)

        wid_idx = header.index("window_id")
        outcome_idx = header.index("realized_up")
        corrected = 0
        for row in rows:
            if row and int(row[wid_idx]) == window_id and row[outcome_idx] != str(correct_realized_up):
                row[outcome_idx] = str(correct_realized_up)
                corrected += 1

        if corrected:
            with open(self.log_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(header)
                writer.writerows(rows)
            logger.warning(
                f"Calibrator: CORRECTED {corrected} mislabeled rows for window {window_id} "
                f"-> realized_up={correct_realized_up} (fallback estimate was wrong)."
            )
        return corrected

    def fit_calibration_curve(self, min_distinct_windows: int = 30) -> bool:
        """
        Fits isotonic regression only when we have at least min_distinct_windows independent trials,
        and both classes (UP and DOWN) have been observed.
        Samples 1 representative mid-window observation (tau nearest 150s) per window
        to avoid high within-window autocorrelation.
        """
        if not os.path.exists(self.log_path):
            return False

        try:
            # Group rows by window_id
            window_groups: Dict[str, List[Dict[str, Any]]] = {}
            with open(self.log_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    r_up = row.get("realized_up")
                    if r_up in ("0", "1"):
                        wid = row.get("window_id")
                        if wid not in window_groups:
                            window_groups[wid] = []
                        window_groups[wid].append({
                            "p_model": float(row["p_model"]),
                            "tau_sec": float(row["tau_sec"]),
                            "realized_up": int(r_up)
                        })

            distinct_windows = len(window_groups)
            if distinct_windows < min_distinct_windows:
                logger.debug(f"Calibrator: {distinct_windows}/{min_distinct_windows} distinct settled windows observed. Isotonic fitting deferred.")
                return False

            # Select 1 representative sample per window (closest to midpoint tau = 150s)
            X_samples = []
            y_samples = []
            for wid, rows in window_groups.items():
                best_row = min(rows, key=lambda r: abs(r["tau_sec"] - 150.0))
                X_samples.append(best_row["p_model"])
                y_samples.append(best_row["realized_up"])

            # Require seeing at least one UP and one DOWN window to avoid degenerate flatlines
            unique_outcomes = set(y_samples)
            if len(unique_outcomes) < 2:
                logger.warning("Calibrator: All observed windows had the same outcome! Deferred to prevent degenerate curve.")
                return False

            X = np.array(X_samples)
            y = np.array(y_samples)
            
            self.isotonic_model = IsotonicRegression(y_min=0.05, y_max=0.95, out_of_bounds="clip")
            self.isotonic_model.fit(X, y)
            self.is_fitted = True
            logger.info(f"Isotonic calibration fitted on {distinct_windows} independent windows (Sample size: {len(X_samples)}).")
            return True
        except Exception as e:
            logger.error(f"Failed to fit isotonic calibrator: {e}")
            return False

    def calibrate(self, p_model: float) -> float:
        """Applies empirical isotonic correction only when safely fitted on 30+ distinct windows."""
        if self.is_fitted and self.isotonic_model:
            return float(self.isotonic_model.predict([p_model])[0])
        return p_model
