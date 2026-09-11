import os
import csv
import time
from typing import Optional, List, Tuple, Dict, Any
import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from scipy.special import logit, expit
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
        self.platt_model: Optional[LogisticRegression] = None
        self.calibration_method: str = "none"
        self.is_fitted = False
        self._last_obs_save_time: float = 0.0

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
                    "ofi", "cbi", "z", "p_model", "p_model_shadow", "p_market", "realized_up"
                ])
        else:
            self._migrate_add_shadow_column()
            self._migrate_add_cbi_column()

    def _migrate_add_shadow_column(self):
        """
        Proposal 1 (shadow/counterfactual logging): adds the p_model_shadow column to an
        already-existing calibration_log.csv that predates it, so every row keeps a
        consistent column count going forward. Existing rows get p_model_shadow=""
        (unknown -- the shadow model wasn't computed for them), which
        fit_calibration_curve/correct_window_outcome safely ignore since neither of them
        reference this column.
        """
        try:
            with open(self.log_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                rows = list(reader)
            if not rows:
                return
            header = rows[0]
            if "p_model_shadow" in header:
                return
            p_model_idx = header.index("p_model") if "p_model" in header else len(header) - 2
            new_header = header[:p_model_idx + 1] + ["p_model_shadow"] + header[p_model_idx + 1:]
            new_rows = [new_header]
            for row in rows[1:]:
                if not row:
                    continue
                new_rows.append(row[:p_model_idx + 1] + [""] + row[p_model_idx + 1:])
            with open(self.log_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerows(new_rows)
            logger.info(
                f"Calibrator: Migrated calibration_log.csv to include p_model_shadow column "
                f"({len(new_rows) - 1} existing rows backfilled with empty shadow value)."
            )
        except Exception as e:
            logger.error(f"Failed to migrate calibration_log.csv for p_model_shadow column: {e}")

    def _migrate_add_cbi_column(self):
        """
        Adds the cbi (Contract Book Imbalance) column to calibration_log.csv if missing.
        """
        try:
            with open(self.log_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                rows = list(reader)
            if not rows:
                return
            header = rows[0]
            if "cbi" in header:
                return
            ofi_idx = header.index("ofi") if "ofi" in header else 5
            new_header = header[:ofi_idx + 1] + ["cbi"] + header[ofi_idx + 1:]
            new_rows = [new_header]
            for row in rows[1:]:
                if not row:
                    continue
                new_rows.append(row[:ofi_idx + 1] + ["0.0"] + row[ofi_idx + 1:])
            with open(self.log_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerows(new_rows)
            logger.info(
                f"Calibrator: Migrated calibration_log.csv to include cbi column "
                f"({len(new_rows) - 1} existing rows backfilled with 0.0)."
            )
        except Exception as e:
            logger.error(f"Failed to migrate calibration_log.csv for cbi column: {e}")

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
        p_market: float,
        p_model_shadow: Optional[float] = None,
        cbi: float = 0.0
    ):
        obs = {
            "timestamp": time.time(),
            "window_id": window_id,
            "tau_sec": round(tau_sec, 2),
            "moneyness": round(moneyness, 6),
            "vol_annualized": round(vol_ann, 4),
            "ofi": round(ofi, 4),
            "cbi": round(cbi, 4),
            "z": round(z, 4),
            "p_model": round(p_model, 4),
            "p_model_shadow": round(p_model_shadow, 4) if p_model_shadow is not None else "",
            "p_market": round(p_market, 4),
        }
        self.pending_window_observations.append(obs)
        now = time.time()
        if now - self._last_obs_save_time >= 5.0:
            self._save_pending_observations()
            self._last_obs_save_time = now

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
                        o.get("cbi", 0.0),
                        o["z"],
                        o["p_model"],
                        o.get("p_model_shadow", ""),
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
            
            # Platt (logistic/sigmoid, 2-parameter) scaling prevents overfitting on small datasets (<300 windows).
            # Switch to non-parametric IsotonicRegression once sample size is sufficiently large (>=300 windows).
            if distinct_windows < 300:
                p_clamped = np.clip(X, 1e-4, 1.0 - 1e-4)
                X_logit = logit(p_clamped).reshape(-1, 1)
                clf = LogisticRegression(C=1.0, solver="lbfgs")
                clf.fit(X_logit, y)
                self.platt_model = clf
                self.isotonic_model = None
                self.calibration_method = "platt"
                self.is_fitted = True
                logger.info(f"Platt (sigmoid) calibration fitted on {distinct_windows} independent windows (Sample size: {len(X_samples)}).")
            else:
                self.isotonic_model = IsotonicRegression(y_min=0.05, y_max=0.95, out_of_bounds="clip")
                self.isotonic_model.fit(X, y)
                self.platt_model = None
                self.calibration_method = "isotonic"
                self.is_fitted = True
                logger.info(f"Isotonic calibration fitted on {distinct_windows} independent windows (Sample size: {len(X_samples)}).")
            return True
        except Exception as e:
            logger.error(f"Failed to fit calibrator: {e}")
            return False

    def calibrate(self, p_model: float) -> float:
        """Applies empirical Platt or isotonic correction only when safely fitted on 30+ distinct windows."""
        if not self.is_fitted:
            return p_model
        try:
            if self.calibration_method == "platt" and self.platt_model is not None:
                p_clamped = max(min(p_model, 1.0 - 1e-4), 1e-4)
                x_val = logit(np.array([[p_clamped]]))
                prob_up = float(self.platt_model.predict_proba(x_val)[0, 1])
                return max(min(prob_up, 0.95), 0.05)
            elif self.calibration_method == "isotonic" and self.isotonic_model is not None:
                return float(self.isotonic_model.predict([p_model])[0])
        except Exception as e:
            logger.warning(f"Calibration predict failed, using raw p_model: {e}")
        return p_model

    def get_confidence_weight(self, min_windows: int = 30, rolling_window: int = 100, k: float = 8.0) -> float:
        """
        Compares model Brier score vs market Brier score over the most recent
        `rolling_window` distinct settled windows (all available if fewer).
        Returns a weight in [0,1]: 1.0 = fully trust the model's own probability,
        0.0 = fully defer to the market's own implied probability. Requires at
        least `min_windows` distinct settled windows before shrinking at all --
        returns 1.0 below that threshold so early noise can't neuter the model.
        Uses the SAME representative-row-per-window selection (closest tau to
        150s) as fit_calibration_curve(), for consistency.
        """
        if not os.path.exists(self.log_path):
            return 1.0
        try:
            window_groups: Dict[str, List[Dict[str, Any]]] = {}
            with open(self.log_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    r_up = row.get("realized_up")
                    if r_up in ("0", "1"):
                        wid = row.get("window_id")
                        if wid not in window_groups:
                            window_groups[wid] = []
                        window_groups[wid].append(row)
            if len(window_groups) < min_windows:
                return 1.0

            window_items = []
            for wid, rows in window_groups.items():
                ts = max(float(r["timestamp"]) for r in rows)
                window_items.append((ts, rows))
            window_items.sort(key=lambda x: x[0], reverse=True)
            window_items = window_items[:rolling_window]

            model_sq_err, market_sq_err = [], []
            for _, rows in window_items:
                best = min(rows, key=lambda r: abs(float(r["tau_sec"]) - 150.0))
                y = int(best["realized_up"])
                model_sq_err.append((float(best["p_model"]) - y) ** 2)
                try:
                    market_sq_err.append((float(best["p_market"]) - y) ** 2)
                except (ValueError, KeyError, TypeError):
                    pass

            if not model_sq_err or not market_sq_err:
                return 1.0
            model_brier = sum(model_sq_err) / len(model_sq_err)
            market_brier = sum(market_sq_err) / len(market_sq_err)
            weight = 0.5 + (market_brier - model_brier) * k   # market worse than model -> weight toward 1
            return max(0.0, min(1.0, weight))
        except Exception as e:
            logger.warning(f"Calibrator: error computing confidence weight: {e}")
            return 1.0
