import os
import csv
import json
import asyncio
import time
import math
import datetime
from typing import Dict, Any, List, Optional
from loguru import logger
from config import config
from src.spot_feed import SpotFeed
from src.market_feed import PolymarketFeed
from src.risk_manager import RiskManager
from src.executor import OrderExecutor
from src.calibrator import EmpiricalCalibrator
from src.event_logger import TradeEventLogger, EVENT_LOG
from src.arbitrage_scanner import ArbitrageScanner
from src.deribit_feed import DeribitFeed
from src.book_ws import PolymarketBookWS
from src.strategies.claud_quant import ClaudQuantBinaryOptionStrategy, estimate_taker_fee_fraction
from src.control_state import is_paused
from src import notifier

RECONCILIATION_MAX_AGE_SEC = 7200.0
RESOLUTION_FALLBACK_TIMEOUT_SEC = 900.0
TWAP_SETTLEMENT_WINDOW_SEC = 60.0
ROLLUP_WINDOW_COUNT = 10

class AssetTradingEngine:
    def __init__(self, asset: str, shared_book_ws: PolymarketBookWS, strategy: Optional[ClaudQuantBinaryOptionStrategy] = None):
        self.asset = asset.upper()
        self.book_ws = shared_book_ws
        self.portfolio_circuit_breaker: bool = False

        self.risk_manager = RiskManager(
            max_position_usd=config.max_position_usd,
            max_daily_loss_usd=config.max_daily_loss_usd,
            kelly_fraction=config.kelly_fraction,
            state_file="data/risk_state.json"
        )

        self.executor = OrderExecutor(paper_trading=config.paper_trading, state_file="data/open_positions.json", asset=self.asset)

        # Bias entries toward higher-conviction, skewed prices (away from expensive 50/50 fee zone).
        # Entry band widened 0.333-0.50 -> 0.25-0.55 and min_abs_z raised 0.40 -> 0.55 after
        # replaying this exact strategy against 1996 REAL historical Polymarket BTC 5m windows
        # (real resolutions, real CLOB prices, real fees -- data/real_market_history_btc.jsonl):
        # baseline fired 81 trades (72.8% win, $0.694 avg PnL/$1, $56.21 total); this config fired
        # 120 trades (75.0% win, $0.811 avg PnL/$1, $97.34 total) on the same data -- more volume
        # AND better per-trade edge, not a volume/quality tradeoff. max_entry_price stays <= 0.55,
        # the research-backed safety band above which breakeven win rate climbs into the "need 85%+
        # accuracy to survive one bad streak" trap (see ClaudQuantBinaryOptionStrategy.__init__).
        self.strategy = strategy or ClaudQuantBinaryOptionStrategy(
            min_edge=0.03,
            slippage_buffer=config.slippage_tolerance,
            min_abs_z=0.55,
            min_strike_distance_pct=0.0003,
            tail_dof=None,
            min_entry_price=0.25,
            max_entry_price=0.55
        )

        self.market_feed = PolymarketFeed(asset=self.asset, book_ws=self.book_ws)
        self.spot_feed = SpotFeed(symbol=f"{self.asset}USDT")
        self.deribit_feed = DeribitFeed(currency=self.asset)

        pretrained_calib_path = f"data/pretrained_calibration_{self.asset.lower()}.pkl"
        backtest_log_path = f"data/backtest_log_{self.asset.lower()}.csv"
        self.calibrator = EmpiricalCalibrator(
            log_path="data/calibration_log.csv",
            observations_path="data/pending_window_observations.json",
            pretrained_path=pretrained_calib_path if os.path.exists(pretrained_calib_path) else None,
            backtest_log_path=backtest_log_path if os.path.exists(backtest_log_path) else None,
        )
        self.arbitrage_scanner = ArbitrageScanner(log_path="data/arbitrage_scan.csv")

        self.event_log_path = "data/trade_events.csv"
        self.event_logger = TradeEventLogger(log_path=self.event_log_path)
        self.last_trade_time = 0.0

        self.rollup_history: List[Dict[str, Any]] = []
        self._funnel_tally: Dict[str, int] = {}

        self.current_window_id = int(time.time() // 300)
        self.current_window_start = self.current_window_id * 300
        self.strike_price = None

        self.resolutions_file = "data/pending_resolutions.json"
        self.pending_resolutions: List[Dict[str, Any]] = []
        self._load_pending_resolutions()

        self.reconciliation_file = "data/fallback_reconciliation.json"
        self.fallback_reconciliation: List[Dict[str, Any]] = []
        self._load_fallback_reconciliation()

        self.spot_task: Optional[asyncio.Task] = None
        self.deribit_task: Optional[asyncio.Task] = None
    def _load_pending_resolutions(self):
        if os.path.exists(self.resolutions_file):
            try:
                with open(self.resolutions_file, "r", encoding="utf-8") as f:
                    self.pending_resolutions = json.load(f)
                    logger.info(f"Bot: Restored {len(self.pending_resolutions)} pending on-chain resolutions from disk.")
            except Exception as e:
                logger.error(f"Failed to load pending resolutions from disk: {e}")

    def _save_pending_resolutions(self):
        os.makedirs(os.path.dirname(self.resolutions_file), exist_ok=True)
        try:
            with open(self.resolutions_file, "w", encoding="utf-8") as f:
                json.dump(self.pending_resolutions, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save pending resolutions to disk: {e}")

    def _load_fallback_reconciliation(self):
        if os.path.exists(self.reconciliation_file):
            try:
                with open(self.reconciliation_file, "r", encoding="utf-8") as f:
                    self.fallback_reconciliation = json.load(f)
                    if self.fallback_reconciliation:
                        logger.info(f"Bot: Restored {len(self.fallback_reconciliation)} pending fallback reconciliations from disk.")
            except Exception as e:
                logger.error(f"Failed to load fallback reconciliation queue from disk: {e}")

    def _save_fallback_reconciliation(self):
        os.makedirs(os.path.dirname(self.reconciliation_file), exist_ok=True)
        try:
            with open(self.reconciliation_file, "w", encoding="utf-8") as f:
                json.dump(self.fallback_reconciliation, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save fallback reconciliation queue to disk: {e}")

    async def _poll_deferred_resolutions(self):
        now = time.time()
        still_pending = []
        changed = False

        for item in self.pending_resolutions:
            slug = item["slug"]
            window_id = item["window_id"]
            strike_k = item["strike_k"]
            binance_estimate = item["binance_estimate"]
            queued_at = item["queued_at"]
            last_checked = item.get("last_checked", 0.0)

            if now - last_checked < 15.0:
                still_pending.append(item)
                continue

            item["last_checked"] = now
            try:
                outcome = await self.market_feed.get_market_resolution(slug)
            except Exception as e:
                logger.warning(f"Resolution poll notice for {slug}: {type(e).__name__}: {e}")
                outcome = None
            
            if outcome is not None:
                divergence_msg = ""
                if outcome != binance_estimate:
                    divergence_msg = f" [ORACLE DIVERGENCE! Binance predicted {binance_estimate}, Polymarket resolved {outcome}]"

                # Empirical estimator scoring: which trailing-price proxy actually matched
                # Polymarket's real (Chainlink TWAP) settlement for this window? Accumulating
                # this across many windows tells us whether the settlement price behaves like
                # an instantaneous snapshot, a 30s TWAP, or a 60s TWAP -- since Chainlink hasn't
                # published the exact sampling spec.
                snap = item.get("snapshot_price")
                t30 = item.get("twap_30")
                t60 = item.get("twap_60")
                scorecard = []
                for label, val in (("snapshot", snap), ("twap30", t30), ("twap60", t60)):
                    if val is None:
                        continue
                    predicted = 1 if val >= strike_k else 0
                    scorecard.append(f"{label}={'OK' if predicted == outcome else 'MISS'}")
                if scorecard:
                    logger.info(f"[ESTIMATOR SCORECARD] Window {window_id}: {' '.join(scorecard)}")

                logger.info(f"[POLYMARKET RESOLUTION CONFIRMED] Window {window_id} ({slug}) -> Outcome: {'UP (1)' if outcome == 1 else 'DOWN (0)'}{divergence_msg}")

                try:
                    self.calibrator.resolve_window(
                        window_id=window_id,
                        strike_k=strike_k,
                        settlement_price=strike_k + (1.0 if outcome == 1 else -1.0)
                    )
                    self.calibrator.fit_calibration_curve(min_distinct_windows=30)
                except Exception as e:
                    logger.error(f"Error resolving calibrator window: {e}")

                try:
                    had_position = any(p["window_id"] == window_id for p in self.executor.open_paper_positions)
                    window_pnl = self.executor.settle_window_positions(
                        window_id=window_id,
                        realized_up=outcome,
                        source="POLYMARKET_ONCHAIN"
                    )
                    self.risk_manager.record_trade_result(pnl=window_pnl)
                    self._record_and_maybe_log_rollup(window_id, window_pnl, had_position)
                    if had_position and config.notify_on_settlement:
                        result_emoji = "\U0001F7E2" if window_pnl > 0 else ("\U0001F534" if window_pnl < 0 else "⚪")
                        notifier.alert(
                            f"{result_emoji} SETTLED (on-chain): window {window_id} -> "
                            f"{'UP' if outcome == 1 else 'DOWN'}{divergence_msg} | PnL: ${window_pnl:+.2f} "
                            f"| cash=${self.executor.simulated_balance:.2f}"
                        )
                except Exception as e:
                    logger.error(f"Error settling window positions: {e}")
                changed = True

            elif now - queued_at > RESOLUTION_FALLBACK_TIMEOUT_SEC:
                logger.warning(f"Resolution timeout for {slug}. Falling back to Binance snapshot estimate: {binance_estimate}")
                try:
                    self.calibrator.resolve_window(
                        window_id=window_id,
                        strike_k=strike_k,
                        settlement_price=item["settlement_price"]
                    )
                    self.calibrator.fit_calibration_curve(min_distinct_windows=30)

                    # Snapshot positions BEFORE settling -- settle_window_positions() removes
                    # them from open_paper_positions, but we need this snapshot later to
                    # reverse/correct the payout if the fallback guess turns out to be wrong
                    # once the real on-chain resolution eventually arrives (it can take well
                    # over 240s -- verified live: window 5962892 resolved on-chain ~9+ minutes
                    # after close, and the fallback guess for it was wrong).
                    positions_snapshot = [
                        dict(p) for p in self.executor.open_paper_positions if p["window_id"] == window_id
                    ]

                    window_pnl = self.executor.settle_window_positions(
                        window_id=window_id,
                        realized_up=binance_estimate,
                        source="BINANCE_FALLBACK"
                    )
                    self.risk_manager.record_trade_result(pnl=window_pnl)
                    self._record_and_maybe_log_rollup(window_id, window_pnl, len(positions_snapshot) > 0)
                    if positions_snapshot and config.notify_on_settlement:
                        result_emoji = "\U0001F7E2" if window_pnl > 0 else ("\U0001F534" if window_pnl < 0 else "⚪")
                        notifier.alert(
                            f"{result_emoji} SETTLED (Binance fallback, unconfirmed): window {window_id} -> "
                            f"{'UP' if binance_estimate == 1 else 'DOWN'} | PnL: ${window_pnl:+.2f} "
                            f"| cash=${self.executor.simulated_balance:.2f}"
                        )

                    # Queue for reconciliation regardless of whether there were open
                    # positions -- the calibration label itself still needs verifying.
                    self.fallback_reconciliation.append({
                        "window_id": window_id,
                        "slug": slug,
                        "binance_estimate": binance_estimate,
                        "positions_snapshot": positions_snapshot,
                        "queued_at": now,
                        "last_checked": 0.0
                    })
                    self._save_fallback_reconciliation()
                except Exception as e:
                    logger.error(f"Error executing fallback resolution: {e}")
                changed = True
            else:
                still_pending.append(item)

        self.pending_resolutions = still_pending
        if changed:
            self._save_pending_resolutions()

    def _record_and_maybe_log_rollup(self, window_id: int, window_pnl: float, had_position: bool):
        """
        Proposal 4: periodic profitability rollup. Records this settled window's outcome
        into a bounded in-memory history and, every ROLLUP_WINDOW_COUNT settled windows,
        logs a rolling summary: win rate, average predicted edge (model-vs-market
        probability gap at entry, from trade_events.csv EXECUTED rows), average realized
        $ return per trade, net PnL over the window, and cumulative funnel block counts
        (NO_SIGNAL / BLOCKED_* / EXECUTED). This is a lightweight diagnostic layered on
        top of the durable CSV logs -- it resets on restart and does not retroactively
        adjust for later fallback-reconciliation PnL corrections (those still flow into
        risk_manager.daily_pnl and are logged separately via [FALLBACK CORRECTION]).
        "Predicted edge" and "realized return" are reported on their own native scales
        (probability-space vs dollars) rather than forced into one number, since that's
        what the underlying logs actually give us.
        """
        predicted_edge = None
        try:
            if os.path.exists(self.event_log_path):
                with open(self.event_log_path, "r", newline="", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    window_rows = [r for r in reader if r.get("window_id") == str(window_id)]
                for r in window_rows:
                    status = r.get("status", "UNKNOWN")
                    self._funnel_tally[status] = self._funnel_tally.get(status, 0) + 1
                exec_rows = [r for r in window_rows if r.get("status") == "EXECUTED" and r.get("real_edge")]
                if exec_rows:
                    edges = [float(r["real_edge"]) for r in exec_rows]
                    predicted_edge = sum(edges) / len(edges)
        except Exception as e:
            logger.debug(f"Rollup: funnel/edge tally read failed for window {window_id}: {e}")

        self.rollup_history.append({
            "window_id": window_id,
            "had_position": had_position,
            "pnl": window_pnl,
            "predicted_edge": predicted_edge,
        })
        max_history = ROLLUP_WINDOW_COUNT * 5
        if len(self.rollup_history) > max_history:
            self.rollup_history = self.rollup_history[-max_history:]

        if len(self.rollup_history) % ROLLUP_WINDOW_COUNT == 0:
            recent = self.rollup_history[-ROLLUP_WINDOW_COUNT:]
            traded = [r for r in recent if r["had_position"]]
            wins = [r for r in traded if r["pnl"] > 0]
            win_rate = (len(wins) / len(traded) * 100.0) if traded else 0.0
            net_pnl = sum(r["pnl"] for r in recent)
            pred_edges = [r["predicted_edge"] for r in traded if r["predicted_edge"] is not None]
            avg_pred_edge = (sum(pred_edges) / len(pred_edges)) if pred_edges else None
            avg_realized_return = (sum(r["pnl"] for r in traded) / len(traded)) if traded else None

            pred_str = f"{avg_pred_edge * 100:+.2f}%" if avg_pred_edge is not None else "N/A"
            real_str = f"${avg_realized_return:+.2f}/trade" if avg_realized_return is not None else "N/A"
            funnel_str = " ".join(f"{k}:{v}" for k, v in sorted(self._funnel_tally.items())) or "N/A"

            logger.info(
                f"[ROLLUP last {ROLLUP_WINDOW_COUNT} windows] Trades: {len(traded)}/{len(recent)} | "
                f"Win Rate: {win_rate:.0f}% | Avg Predicted Edge: {pred_str} | "
                f"Avg Realized: {real_str} | Net PnL: ${net_pnl:+.2f} | "
                f"Funnel (cumulative): {funnel_str}"
            )
            if config.notify_on_rollup:
                notifier.alert(
                    f"\U0001F4CA ROLLUP (last {ROLLUP_WINDOW_COUNT} windows): "
                    f"Trades {len(traded)}/{len(recent)} | Win Rate {win_rate:.0f}% | "
                    f"Net PnL ${net_pnl:+.2f} | Cash ${self.executor.simulated_balance:.2f}"
                )
            self._funnel_tally = {}

    async def _poll_fallback_reconciliation(self):
        """
        Re-checks windows that were provisionally settled via Binance-fallback (because
        on-chain resolution hadn't posted within the poll timeout) against the real
        on-chain outcome once it eventually shows up. If the fallback guess disagrees
        with the confirmed outcome, corrects both the calibration_log.csv label and the
        paper-trading bankroll -- otherwise a wrong fallback guess would silently and
        permanently poison the calibration data, which defeats the entire point of
        collecting it. Verified live: window 5962892's Binance-fallback guess was UP,
        but the real on-chain resolution (confirmed independently ~9 min later) was DOWN.
        """
        now = time.time()
        still_pending = []
        changed = False

        for item in self.fallback_reconciliation:
            slug = item["slug"]
            window_id = item["window_id"]
            binance_estimate = item["binance_estimate"]
            positions_snapshot = item.get("positions_snapshot", [])
            queued_at = item["queued_at"]
            last_checked = item.get("last_checked", 0.0)

            if now - queued_at > RECONCILIATION_MAX_AGE_SEC:
                logger.warning(
                    f"[FALLBACK RECONCILIATION GIVE UP] Window {window_id} ({slug}) never confirmed on-chain "
                    f"within {RECONCILIATION_MAX_AGE_SEC:.0f}s. Fallback guess ({binance_estimate}) stands unverified."
                )
                changed = True
                continue

            if now - last_checked < 60.0:
                still_pending.append(item)
                continue

            item["last_checked"] = now
            try:
                outcome = await self.market_feed.get_market_resolution(slug)
            except Exception as e:
                logger.warning(f"Reconciliation poll notice for {slug}: {type(e).__name__}: {e}")
                outcome = None

            if outcome is None:
                still_pending.append(item)
                continue

            if outcome == binance_estimate:
                logger.info(f"[FALLBACK CONFIRMED CORRECT] Window {window_id}: on-chain outcome matches Binance-fallback guess ({outcome}).")
            else:
                logger.warning(
                    f"[FALLBACK WAS WRONG] Window {window_id}: Binance-fallback guessed {binance_estimate}, "
                    f"real on-chain outcome is {outcome}. Correcting calibration label and PnL now."
                )
                try:
                    self.calibrator.correct_window_outcome(window_id=window_id, correct_realized_up=outcome)
                except Exception as e:
                    logger.error(f"Error correcting calibrator label for window {window_id}: {e}")
                try:
                    delta = self.executor.correct_settlement(
                        window_id=window_id,
                        positions_snapshot=positions_snapshot,
                        wrong_realized_up=binance_estimate,
                        correct_realized_up=outcome
                    )
                    if abs(delta) > 1e-9:
                        self.risk_manager.record_trade_result(pnl=delta)
                except Exception as e:
                    logger.error(f"Error correcting PnL for window {window_id}: {e}")
            changed = True

        self.fallback_reconciliation = still_pending
        if changed:
            self._save_fallback_reconciliation()

    async def _recover_orphaned_windows(self):
        """
        Handles a gap the persistence fixes didn't cover: if the bot is offline across
        an ENTIRE window's close (crashes mid-window, restarts much later, possibly
        several windows after), __init__ sets self.current_window_id to whatever window
        is live *now* -- the rollover-detection block in run() only fires the "queue for
        resolution" logic when it personally observes a live window_id change, so a
        window that closed while nothing was running never gets queued at all. Its
        buffered ticks (correctly persisted to pending_window_observations.json) then
        sit there forever: never resolved, never flushed to calibration_log.csv, never
        cleaned up. Verified live: window 5962900 got 15 ticks logged, then the process
        went down and didn't come back until window 5962908 -- 8 windows (40 minutes)
        later -- leaving those 15 ticks permanently stuck with no path to resolution.

        Fix: on startup, diff the window_ids present in the observation buffer against
        the windows we actually know about (current live window + anything already
        queued for resolution). Anything left over is orphaned -- recover its strike
        and an approximate close price via Binance klines (the same REST recovery used
        for the live strike) and queue it for resolution like any other closed window.
        """
        known_ids = {self.current_window_id}
        known_ids.update(item["window_id"] for item in self.pending_resolutions)
        known_ids.update(item["window_id"] for item in self.fallback_reconciliation)

        orphaned_ids = sorted({
            o["window_id"] for o in self.calibrator.pending_window_observations
        } - known_ids)

        if not orphaned_ids:
            return

        for window_id in orphaned_ids:
            window_start = window_id * 300
            window_close = window_start + 300
            n_ticks = sum(1 for o in self.calibrator.pending_window_observations if o["window_id"] == window_id)

            strike_k = await self.spot_feed.get_exact_window_open_price(window_start)
            close_price = await self.spot_feed.get_exact_window_open_price(window_close)

            if strike_k is None or close_price is None:
                logger.error(
                    f"[ORPHAN RECOVERY] Window {window_id} has {n_ticks} orphaned observations from a restart "
                    f"gap that skipped its close entirely, but Binance klines couldn't recover its prices. "
                    f"These ticks will remain unresolved."
                )
                continue

            binance_estimate = 1 if close_price >= strike_k else 0
            slug = self.market_feed.get_slug_for_window(window_start)
            logger.warning(
                f"[ORPHAN RECOVERY] Window {window_id} had {n_ticks} orphaned observations (restart gap skipped "
                f"its close). Recovered K=${strike_k:.2f}, close~${close_price:.2f} via Binance klines. "
                f"Queuing for resolution (Slug: {slug})."
            )
            self.pending_resolutions.append({
                "window_id": window_id,
                "slug": slug,
                "strike_k": strike_k,
                "settlement_price": close_price,
                "binance_estimate": binance_estimate,
                "snapshot_price": close_price,
                "twap_30": None,
                "twap_60": None,
                "queued_at": time.time(),
                "last_checked": 0.0
            })

    async def tick(self):
        if self.spot_feed.latest_price is None:
            return

        spot_price = self.spot_feed.latest_price
        now = time.time()

        # Check for 5-minute interval rollover (300 seconds)
        window_id = int(now // 300)
        window_start = window_id * 300

        if window_id != self.current_window_id or self.strike_price is None:
            if self.strike_price is not None:
                # Polymarket's crypto Up/Down markets settle via a Chainlink TWAP
                # Data Stream (as of the Aug 2026 upgrade), not a single instantaneous
                # price snapshot -- so an instantaneous Binance spot read is a biased
                # proxy for the real settlement price. Compute trailing 30s/60s TWAPs
                # too (public sources disagree on the exact window) and keep all three
                # so post-hoc comparison against the confirmed on-chain outcome can
                # tell us empirically which one actually tracks Polymarket's resolution.
                twap_30 = self.spot_feed.get_trailing_twap(30.0)
                twap_60 = self.spot_feed.get_trailing_twap(60.0)
                best_estimate_price = twap_60 if twap_60 is not None else spot_price

                realized_up_estimate = 1 if best_estimate_price >= self.strike_price else 0
                closing_window_ts = self.current_window_start
                closing_slug = self.market_feed.get_slug_for_window(closing_window_ts)

                self.pending_resolutions.append({
                    "window_id": self.current_window_id,
                    "slug": closing_slug,
                    "strike_k": self.strike_price,
                    "settlement_price": best_estimate_price,
                    "binance_estimate": realized_up_estimate,
                    "snapshot_price": spot_price,
                    "twap_30": twap_30,
                    "twap_60": twap_60,
                    "queued_at": now,
                    "last_checked": 0.0
                })
                self._save_pending_resolutions()
                logger.info(
                    f"Window {self.current_window_id} closed. Queued for Polymarket on-chain resolution "
                    f"(Slug: {closing_slug}) | snapshot=${spot_price:.2f} twap30={twap_30} twap60={twap_60}"
                )

            self.current_window_id = window_id
            self.current_window_start = window_start
            
            # RECOVER EXACT WINDOW OPEN STRIKE PRICE (NO DRIFT ON RESTART)
            exact_open = await self.spot_feed.get_exact_window_open_price(window_start)
            self.strike_price = exact_open if exact_open is not None else spot_price
            logger.info(f"[NEW 5-MIN WINDOW] Window {window_id} Strike K initialized at ${self.strike_price:.2f}")

        try:
            await self._poll_deferred_resolutions()
        except Exception as e:
            logger.error(f"Unexpected error in _poll_deferred_resolutions: {e}")

        try:
            await self._poll_fallback_reconciliation()
        except Exception as e:
            logger.error(f"Unexpected error in _poll_fallback_reconciliation: {e}")

        time_remaining_sec = max(300.0 - (now - self.current_window_start), 1.0)
        raw_vol_ann = self.spot_feed.annualized_vol
        ofi = self.spot_feed.get_ofi_normalized()
        momentum = self.spot_feed.get_momentum(lookback_seconds=10.0)

        # Proposal 2: Deribit DVOL implied volatility blending
        # DVOL is forward-looking 30-day implied vol (decimal, e.g. 0.39 for 39%).
        # Blend 20% DVOL prior with 80% realized trailing vol when available,
        # stabilizing vol estimation against startup noise or sudden regime shifts.
        dvol_ann = self.deribit_feed.get_dvol() if self.deribit_feed else None
        if dvol_ann is not None:
            vol_ann = 0.80 * raw_vol_ann + 0.20 * dvol_ann
        else:
            vol_ann = raw_vol_ann

        # Proposal 3: Cross-exchange composite reference price tracking
        # Compares Binance mid against Deribit's multi-exchange composite index
        composite_idx = self.deribit_feed.get_composite_index() if self.deribit_feed else None
        basis_spread = (spot_price - composite_idx) if composite_idx is not None else None

        # Microprice (Stoikov): size-weighted mid that leans toward the thinner side of
        # the book, i.e. the side more likely to get run through next. Used ONLY as the
        # pricing input (S_t) for the strategy's edge calculation -- realized vol,
        # window rollover, and settlement estimation all deliberately keep using the
        # plain mid (spot_price) since microprice is a short-horizon directional signal,
        # not a "true price" reference.
        pricing_spot = self.spot_feed.microprice if self.spot_feed.microprice is not None else spot_price

        # Trailing partial-TWAP of the current closing window's averaging period, for
        # the Asian-option-style variance adjustment in calculate_fair_probability.
        known_avg_price = None
        if time_remaining_sec < TWAP_SETTLEMENT_WINDOW_SEC:
            elapsed_in_twap_window = TWAP_SETTLEMENT_WINDOW_SEC - time_remaining_sec
            known_avg_price = self.spot_feed.get_trailing_twap(elapsed_in_twap_window)

        await self.market_feed.find_active_5min_market()
        live_quotes = await self.market_feed.get_live_market_prices()

        # Shadow-only: does YES+NO < $1.00 ever actually occur on this market? Detection
        # only, no execution -- answers the empirical question before any capital is risked.
        self.arbitrage_scanner.check(
            window_id=self.current_window_id,
            yes_ask=live_quotes.get("yes_ask"),
            no_ask=live_quotes.get("no_ask"),
        )

        # Pillar A: Contract Book Imbalance (CBI) from Polymarket's resting YES depth ladder
        yes_bid_depth = sum(float(l["size"]) for l in live_quotes.get("yes_bids", []) if isinstance(l, dict) and "size" in l)
        yes_ask_depth = sum(float(l["size"]) for l in live_quotes.get("yes_asks", []) if isinstance(l, dict) and "size" in l)
        total_depth = yes_bid_depth + yes_ask_depth
        cbi = (yes_bid_depth - yes_ask_depth) / total_depth if total_depth > 0 else 0.0

        norm_momentum = max(min(momentum / 50.0, 1.0), -1.0)
        regime_factor = self.spot_feed.get_regime_factor()
        raw_p_model, z = self.strategy.calculate_fair_probability(
            S_t=pricing_spot,
            K=self.strike_price,
            tau_seconds=time_remaining_sec,
            annualized_vol=vol_ann,
            ofi_normalized=ofi,
            momentum_normalized=norm_momentum,
            cbi_normalized=cbi,
            known_avg_price=known_avg_price,
            twap_window_sec=TWAP_SETTLEMENT_WINDOW_SEC,
            regime_factor=regime_factor
        )

        # Proposal 1: shadow/counterfactual logging. Recompute fair probability using
        # ONLY the pre-upgrade inputs -- plain mid instead of microprice, plain trailing
        # realized vol instead of the DVOL blend, cbi_normalized=0.0 (no CBI leakage), and
        # known_avg_price=None to disable the TWAP/Asian-option variance adjustment -- so
        # calibration_log.csv carries both the live model's prediction and what the bot would
        # have priced before any upgrades.
        p_model_shadow, _z_shadow = self.strategy.calculate_fair_probability(
            S_t=spot_price,
            K=self.strike_price,
            tau_seconds=time_remaining_sec,
            annualized_vol=raw_vol_ann,
            ofi_normalized=ofi,
            momentum_normalized=norm_momentum,
            cbi_normalized=0.0,
            known_avg_price=None,
            twap_window_sec=TWAP_SETTLEMENT_WINDOW_SEC
        )

        calibrated_p_up = self.calibrator.calibrate(raw_p_model)

        # Quantitative Edge Research: Phase A shadow feature computation
        # 1. Lead-lag return: 3s spot return on Binance vs Polymarket contract mid
        spot_lead_lag = 0.0
        if len(self.spot_feed.second_buckets) >= 4:
            p_curr = self.spot_feed.second_buckets[-1][1]
            p_prev = self.spot_feed.second_buckets[-4][1]
            if p_prev > 0:
                spot_lead_lag = (p_curr - p_prev) / p_prev

        # 2. TWAP settlement deviation: divergence of current spot from known trailing TWAP
        twap_dev = 0.0
        if known_avg_price is not None and spot_price > 0:
            twap_dev = (spot_price - known_avg_price) / spot_price

        # 3. Multilevel book depth skew across top bids/asks
        bids_depth = sum(float(l["size"]) for l in live_quotes.get("yes_bids", [])[:3] if isinstance(l, dict) and "size" in l)
        asks_depth = sum(float(l["size"]) for l in live_quotes.get("yes_asks", [])[:3] if isinstance(l, dict) and "size" in l)
        tot_depth = bids_depth + asks_depth
        book_depth_skew = (bids_depth - asks_depth) / tot_depth if tot_depth > 0 else 0.0

        self.calibrator.log_observation(
            window_id=self.current_window_id,
            tau_sec=time_remaining_sec,
            moneyness=spot_price / self.strike_price,
            vol_ann=vol_ann,
            ofi=ofi,
            z=z,
            p_model=raw_p_model,
            p_market=live_quotes["yes_ask"],
            p_model_shadow=p_model_shadow,
            cbi=cbi,
            spot_lead_lag=spot_lead_lag,
            twap_dev=twap_dev,
            book_depth_skew=book_depth_skew
        )

        market_info = {
            "strike_price": self.strike_price,
            "time_remaining_sec": time_remaining_sec,
            "annualized_vol": vol_ann,
            "ofi_normalized": ofi,
            "yes_ask": live_quotes["yes_ask"],
            "yes_bid": live_quotes["yes_bid"],
            "no_ask": live_quotes["no_ask"],
            "no_bid": live_quotes["no_bid"],
            "known_avg_price": known_avg_price,
            "twap_window_sec": TWAP_SETTLEMENT_WINDOW_SEC,
            "regime_factor": regime_factor,
        }

        confidence_weight = self.calibrator.get_confidence_weight()

        signal = self.strategy.evaluate(
            spot_price=pricing_spot,
            momentum=momentum,
            market_info=market_info,
            order_book={"cbi": cbi, **live_quotes},
            confidence_weight=confidence_weight
        )

        # TRADE FUNNEL & GUARDS
        if not signal:
            status = "BLOCKED_NOISE" if abs(z) < self.strategy.min_abs_z else "NO_SIGNAL"
            self.event_logger.log_event(
                window_id=self.current_window_id,
                tau_sec=time_remaining_sec,
                outcome="NONE",
                z=z,
                p_model=calibrated_p_up,
                direct_ask=0.0,
                direct_spread=0.0,
                real_edge=0.0,
                hurdle=0.0,
                status=status
            )
        else:
            # Shrink the calibrated probability toward the market price using the same confidence weight
            is_yes = (signal["outcome"] == "YES")
            market_prob = live_quotes["yes_ask"] if is_yes else (1.0 - live_quotes["yes_bid"])
            base_prob = calibrated_p_up if is_yes else (1.0 - calibrated_p_up)
            if confidence_weight < 1.0:
                signal["estimated_prob"] = confidence_weight * base_prob + (1.0 - confidence_weight) * market_prob
            else:
                signal["estimated_prob"] = base_prob
            direct_ask = live_quotes["direct_yes_ask"] if is_yes else live_quotes["direct_no_ask"]
            direct_bid = live_quotes["direct_yes_bid"] if is_yes else live_quotes["direct_no_bid"]
            can_take_trades = self.risk_manager.can_trade()

            has_position_in_window = any(
                p["window_id"] == self.current_window_id 
                for p in self.executor.open_paper_positions
            )

            if has_position_in_window:
                self.event_logger.log_event(
                    window_id=self.current_window_id,
                    tau_sec=time_remaining_sec,
                    outcome=signal["outcome"],
                    z=z,
                    p_model=signal["estimated_prob"],
                    direct_ask=direct_ask if direct_ask else 0.0,
                    direct_spread=0.0,
                    real_edge=0.0,
                    hurdle=signal["hurdle"],
                    status="BLOCKED_WINDOW_MAX_POS"
                )
            elif direct_ask is None:
                ws_connected = self.book_ws.connected if self.book_ws else False
                target_token = self.market_feed.token_id_yes if is_yes else self.market_feed.token_id_no
                has_fresh, age_s, has_asks = self.book_ws.get_book_meta(target_token) if self.book_ws else (False, None, False)
                rest_backoff_active = (time.time() < self.market_feed._book_backoff_until)
                age_str = f"{age_s:.1f}s" if age_s is not None else "no_entry"
                logger.debug(
                    f"[{self.asset}] BLOCKED_PHANTOM trigger: token={target_token[:12] if target_token else 'None'} "
                    f"ws_conn={ws_connected} fresh={has_fresh} age={age_str} has_asks={has_asks} rest_backoff={rest_backoff_active}"
                )
                self.event_logger.log_event(
                    window_id=self.current_window_id,
                    tau_sec=time_remaining_sec,
                    outcome=signal["outcome"],
                    z=z,
                    p_model=signal["estimated_prob"],
                    direct_ask=0.0,
                    direct_spread=0.0,
                    real_edge=0.0,
                    hurdle=signal["hurdle"],
                    status="BLOCKED_PHANTOM"
                )
            else:
                exec_price = direct_ask
                real_edge = signal["estimated_prob"] - exec_price
                direct_spread = (direct_ask - direct_bid) if (direct_bid is not None and direct_ask >= direct_bid) else 0.02
                # Fee-aware hurdle using the REAL Polymarket crypto taker-fee curve
                # (rate * (1 - price), peaks at 3.5% near 50/50) rather than the old
                # flat 0.5% assumption -- see estimate_taker_fee_fraction() for the
                # live-verified fee schedule this is based on.
                direct_hurdle = max(
                    self.strategy.min_edge,
                    (direct_spread / 2.0) + estimate_taker_fee_fraction(exec_price) + self.strategy.slippage_buffer
                )

                if real_edge <= direct_hurdle:
                    self.event_logger.log_event(
                        window_id=self.current_window_id,
                        tau_sec=time_remaining_sec,
                        outcome=signal["outcome"],
                        z=z,
                        p_model=signal["estimated_prob"],
                        direct_ask=exec_price,
                        direct_spread=direct_spread,
                        real_edge=real_edge,
                        hurdle=direct_hurdle,
                        status="BLOCKED_HURDLE"
                    )
                elif self.strategy.min_entry_price is not None and exec_price < self.strategy.min_entry_price:
                    self.event_logger.log_event(
                        window_id=self.current_window_id,
                        tau_sec=time_remaining_sec,
                        outcome=signal["outcome"],
                        z=z,
                        p_model=signal["estimated_prob"],
                        direct_ask=exec_price,
                        direct_spread=direct_spread,
                        real_edge=real_edge,
                        hurdle=direct_hurdle,
                        status="BLOCKED_PAYOUT_RATIO"
                    )
                elif self.strategy.max_entry_price is not None and exec_price > self.strategy.max_entry_price:
                    self.event_logger.log_event(
                        window_id=self.current_window_id,
                        tau_sec=time_remaining_sec,
                        outcome=signal["outcome"],
                        z=z,
                        p_model=signal["estimated_prob"],
                        direct_ask=exec_price,
                        direct_spread=direct_spread,
                        real_edge=real_edge,
                        hurdle=direct_hurdle,
                        status="BLOCKED_PAYOUT_RATIO"
                    )
                elif confidence_weight < 0.35:
                    self.event_logger.log_event(
                        window_id=self.current_window_id,
                        tau_sec=time_remaining_sec,
                        outcome=signal["outcome"],
                        z=z,
                        p_model=signal["estimated_prob"],
                        direct_ask=exec_price,
                        direct_spread=direct_spread,
                        real_edge=real_edge,
                        hurdle=direct_hurdle,
                        status="BLOCKED_CONFIDENCE"
                    )
                elif self.portfolio_circuit_breaker:
                    self.event_logger.log_event(
                        window_id=self.current_window_id,
                        tau_sec=time_remaining_sec,
                        outcome=signal["outcome"],
                        z=z,
                        p_model=signal["estimated_prob"],
                        direct_ask=exec_price,
                        direct_spread=direct_spread,
                        real_edge=real_edge,
                        hurdle=direct_hurdle,
                        status="BLOCKED_PORTFOLIO_RISK"
                    )
                elif not can_take_trades:
                    self.event_logger.log_event(
                        window_id=self.current_window_id,
                        tau_sec=time_remaining_sec,
                        outcome=signal["outcome"],
                        z=z,
                        p_model=signal["estimated_prob"],
                        direct_ask=exec_price,
                        direct_spread=direct_spread,
                        real_edge=real_edge,
                        hurdle=direct_hurdle,
                        status="BLOCKED_RISK"
                    )
                elif (now - self.last_trade_time <= 15):
                    self.event_logger.log_event(
                        window_id=self.current_window_id,
                        tau_sec=time_remaining_sec,
                        outcome=signal["outcome"],
                        z=z,
                        p_model=signal["estimated_prob"],
                        direct_ask=exec_price,
                        direct_spread=direct_spread,
                        real_edge=real_edge,
                        hurdle=direct_hurdle,
                        status="BLOCKED_COOLDOWN"
                    )
                else:
                    bankroll = self.executor.simulated_balance if config.paper_trading else 500.0
                    odds = 1.0 / max(exec_price, 0.05)
                    size = self.risk_manager.calculate_position_size(
                        win_probability=signal["estimated_prob"],
                        odds=odds,
                        bankroll=bankroll,
                        confidence_weight=confidence_weight
                    )

                    if size > 0:
                        # Proposal 3: Order-Book Depth & Realistic Book Walking
                        # Walk the direct ask order book to obtain the true VWAP fill price across depth
                        target_asks = live_quotes.get("yes_asks" if is_yes else "no_asks", [])
                        vwap_price, total_cost, total_shares = self.market_feed.simulate_walk_book(target_asks, size)

                        if vwap_price is None:
                            # Book lacks depth to fill requested size; avoid phantom fills.
                            # NOTE: deliberately no `continue` here -- this sits inside the main
                            # 1s tick loop, and a bare `continue` would skip straight back to
                            # `while True`, silently swallowing the periodic status-line /
                            # DVOL telemetry block below for that tick. That's exactly
                            # the wrong failure mode: thin resting depth (which triggers this
                            # branch) is precisely when that telemetry is most useful to see.
                            # Verified live: a bare `continue` here made the status line vanish
                            # for stretches whenever signals kept hitting BLOCKED_PHANTOM/HURDLE.
                            self.event_logger.log_event(
                                window_id=self.current_window_id,
                                tau_sec=time_remaining_sec,
                                outcome=signal["outcome"],
                                z=z,
                                p_model=signal["estimated_prob"],
                                direct_ask=exec_price,
                                direct_spread=direct_spread,
                                real_edge=real_edge,
                                hurdle=direct_hurdle,
                                status="BLOCKED_PHANTOM",
                                size_usd=0.0
                            )
                        else:
                            # Re-verify edge against VWAP fill price after slippage.
                            # Recompute the fee-aware hurdle at the ACTUAL vwap fill
                            # price too -- walking deeper into the book to fill size
                            # can land at a materially different price than direct_ask,
                            # and the real taker fee (rate * (1-price)) moves with it.
                            vwap_edge = signal["estimated_prob"] - vwap_price
                            vwap_hurdle = max(
                                self.strategy.min_edge,
                                (direct_spread / 2.0) + estimate_taker_fee_fraction(vwap_price) + self.strategy.slippage_buffer
                            )
                            if vwap_edge <= vwap_hurdle:
                                self.event_logger.log_event(
                                    window_id=self.current_window_id,
                                    tau_sec=time_remaining_sec,
                                    outcome=signal["outcome"],
                                    z=z,
                                    p_model=signal["estimated_prob"],
                                    direct_ask=vwap_price,
                                    direct_spread=direct_spread,
                                    real_edge=vwap_edge,
                                    hurdle=vwap_hurdle,
                                    status="BLOCKED_HURDLE",
                                    size_usd=0.0
                                )
                            else:
                                token_target = self.market_feed.token_id_yes if is_yes else (self.market_feed.token_id_no or "token_no")
                                active_slug = self.market_feed.get_slug_for_window(self.current_window_start)
                                exec_result = await self.executor.execute_trade(
                                    window_id=self.current_window_id,
                                    slug=active_slug,
                                    token_id=token_target or "clob_token_default",
                                    outcome=signal["outcome"],
                                    amount_usd=size,
                                    price=vwap_price
                                )

                                if exec_result.get("status") == "BLOCKED_INSUFFICIENT_FUNDS":
                                    self.event_logger.log_event(
                                        window_id=self.current_window_id,
                                        tau_sec=time_remaining_sec,
                                        outcome=signal["outcome"],
                                        z=z,
                                        p_model=signal["estimated_prob"],
                                        direct_ask=vwap_price,
                                        direct_spread=direct_spread,
                                        real_edge=vwap_edge,
                                        hurdle=vwap_hurdle,
                                        status="BLOCKED_FUNDS",
                                        size_usd=0.0
                                    )
                                else:
                                    self.last_trade_time = now

                                    if config.notify_on_trade:
                                        notifier.alert(
                                            f"\U0001F4C8 TRADE [{self.asset}]: {signal['outcome']} on window {self.current_window_id} "
                                            f"({active_slug}) | ${size:.2f} @ {vwap_price:.3f} | "
                                            f"model p={signal['estimated_prob']*100:.1f}% edge={vwap_edge*100:+.2f}% "
                                            f"| cash=${self.executor.simulated_balance:.2f}"
                                        )

                                    self.event_logger.log_event(
                                        window_id=self.current_window_id,
                                        tau_sec=time_remaining_sec,
                                        outcome=signal["outcome"],
                                        z=z,
                                        p_model=signal["estimated_prob"],
                                        direct_ask=vwap_price,
                                        direct_spread=direct_spread,
                                        real_edge=vwap_edge,
                                        hurdle=vwap_hurdle,
                                        status="EXECUTED",
                                        size_usd=size
                                    )
                    else:
                        self.event_logger.log_event(
                            window_id=self.current_window_id,
                            tau_sec=time_remaining_sec,
                            outcome=signal["outcome"],
                            z=z,
                            p_model=signal["estimated_prob"],
                            direct_ask=exec_price,
                            direct_spread=direct_spread,
                            real_edge=real_edge,
                            hurdle=direct_hurdle,
                            status="BLOCKED_KELLY_ZERO",
                            size_usd=0.0
                        )

        pending_count = len(self.pending_resolutions)
        can_trade_now = self.risk_manager.can_trade() and not self.portfolio_circuit_breaker
        if self.portfolio_circuit_breaker:
            status_str = 'PORTFOLIO_HALTED'
        elif not self.risk_manager.can_trade():
            status_str = 'HALTED'
        elif confidence_weight < 0.35:
            status_str = 'LOW_CONFIDENCE_PAUSED'
        else:
            status_str = 'ACTIVE'
        basis_str = f"Basis: {basis_spread:+.2f}" if basis_spread is not None else "Basis: N/A"
        dvol_str = f"DVOL: {dvol_ann*100:.1f}%" if dvol_ann is not None else "DVOL: N/A"

        logger.info(
            f"[{self.asset}] Spot: ${spot_price:.2f} | K: ${self.strike_price:.2f} | "
            f"Tau: {time_remaining_sec:.0f}s | Vol: {vol_ann*100:.1f}% ({dvol_str}) | "
            f"{basis_str} | OFI: {ofi:+.2f} | z: {z:+.2f} | P(up): {calibrated_p_up*100:.1f}% | "
            f"Conf: {confidence_weight:.2f} | "
            f"Trade: [{status_str}] | Cash: ${self.executor.simulated_balance:.2f} | Day PnL: {self.risk_manager.daily_pnl:+.2f} USD | Pending Res: {pending_count}"
        )

class Polymarket5mBot:
    def __init__(self):
        self.book_ws = PolymarketBookWS()
        self.target_asset = config.target_asset
        self.engine = AssetTradingEngine(asset=self.target_asset, shared_book_ws=self.book_ws)
        self.portfolio_state_file = "data/risk_state_portfolio.json"
        self.max_portfolio_daily_loss_usd = config.max_portfolio_daily_loss_usd
        self.portfolio_circuit_breaker = False
        self.current_portfolio_day = str(datetime.datetime.now(datetime.timezone.utc).date())

        # Phase 1: Portfolio Max-Drawdown Breaker (Hard stop at starting_balance * (1 - max_portfolio_drawdown_pct))
        # Unlike daily circuit breaker, this persists permanently until manually cleared.
        self.drawdown_state_file = "data/risk_state_drawdown.json"
        self.starting_balance_usd = config.starting_balance_usd
        self.max_drawdown_floor_usd = self.starting_balance_usd * (1.0 - config.max_portfolio_drawdown_pct)
        self.drawdown_breaker_triggered = False

        self._load_portfolio_state()
        self._load_drawdown_state()

    def _load_drawdown_state(self):
        if os.path.exists(self.drawdown_state_file):
            try:
                with open(self.drawdown_state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.drawdown_breaker_triggered = bool(data.get("circuit_breaker_triggered", False))
                if self.drawdown_breaker_triggered:
                    self.engine.portfolio_circuit_breaker = True
                    logger.critical(
                        f"RiskManager [DRAWDOWN]: Restored PERMANENT TRIPPED state from {self.drawdown_state_file}. "
                        f"Portfolio balance breached drawdown floor (${self.max_drawdown_floor_usd:.2f}). "
                        f"All new trade entries halted until manual reset."
                    )
            except Exception as e:
                logger.warning(f"RiskManager [DRAWDOWN]: Failed to load state ({e}).")

    def _save_drawdown_state(self, current_balance: float):
        try:
            os.makedirs(os.path.dirname(self.drawdown_state_file), exist_ok=True)
            tmp_file = f"{self.drawdown_state_file}.tmp"
            payload = {
                "starting_balance_usd": self.starting_balance_usd,
                "drawdown_floor_usd": self.max_drawdown_floor_usd,
                "current_simulated_balance": current_balance,
                "circuit_breaker_triggered": self.drawdown_breaker_triggered,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_file, self.drawdown_state_file)
        except Exception as e:
            logger.error(f"RiskManager [DRAWDOWN]: Failed to atomically save state: {e}")

    def _load_portfolio_state(self):
        if os.path.exists(self.portfolio_state_file):
            try:
                with open(self.portfolio_state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                today_str = str(datetime.datetime.now(datetime.timezone.utc).date())
                saved_day = data.get("current_day")
                if saved_day == today_str:
                    self.current_portfolio_day = today_str
                    self.portfolio_circuit_breaker = bool(data.get("circuit_breaker_triggered", False))
                    self.engine.portfolio_circuit_breaker = self.portfolio_circuit_breaker or self.drawdown_breaker_triggered
                    if self.portfolio_circuit_breaker:
                        logger.critical(
                            f"RiskManager [PORTFOLIO]: Restored TRIPPED state from {self.portfolio_state_file}. "
                            f"All new trade entries remain halted."
                        )
                else:
                    self.current_portfolio_day = today_str
                    self.portfolio_circuit_breaker = False
                    self.engine.portfolio_circuit_breaker = self.drawdown_breaker_triggered
                    self._save_portfolio_state()
            except Exception as e:
                logger.warning(f"RiskManager [PORTFOLIO]: Failed to load state ({e}). Starting fresh.")
                self._save_portfolio_state()
        else:
            self._save_portfolio_state()

    def _save_portfolio_state(self):
        try:
            os.makedirs(os.path.dirname(self.portfolio_state_file), exist_ok=True)
            tmp_file = f"{self.portfolio_state_file}.tmp"
            total_pnl = self.engine.risk_manager.daily_pnl
            payload = {
                "current_day": self.current_portfolio_day,
                "daily_pnl": total_pnl,
                "max_portfolio_daily_loss_usd": self.max_portfolio_daily_loss_usd,
                "circuit_breaker_triggered": self.portfolio_circuit_breaker,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_file, self.portfolio_state_file)
        except Exception as e:
            logger.error(f"RiskManager [PORTFOLIO]: Failed to atomically save state: {e}")

    def check_portfolio_risk(self):
        # 1. Check Permanent Max Drawdown Breaker (25% of starting capital)
        current_balance = self.engine.executor.simulated_balance

        if not self.drawdown_breaker_triggered:
            if current_balance <= self.max_drawdown_floor_usd:
                self.drawdown_breaker_triggered = True
                self.engine.portfolio_circuit_breaker = True
                logger.critical(
                    f"🚨 [PORTFOLIO MAX DRAWDOWN BREAKER TRIPPED] Balance ${current_balance:.2f} "
                    f"breached -25% capital floor (${self.max_drawdown_floor_usd:.2f} of ${self.starting_balance_usd:.2f}). "
                    f"PERMANENTLY HALTING ALL TRADES. Requires manual clear."
                )
                if config.notify_on_trade:
                    notifier.alert(
                        f"🚨 PORTFOLIO MAX DRAWDOWN BREAKER TRIPPED! Cash: ${current_balance:.2f} <= floor ${self.max_drawdown_floor_usd:.2f}."
                    )
                self._save_drawdown_state(current_balance)

        today_str = str(datetime.datetime.now(datetime.timezone.utc).date())
        
        # Day rollover check mirroring RiskManager._check_day_rollover()
        if today_str != self.current_portfolio_day:
            logger.info(
                f"RiskManager [PORTFOLIO]: Day rollover from {self.current_portfolio_day} to {today_str}. "
                f"Resetting daily portfolio circuit breaker."
            )
            self.current_portfolio_day = today_str
            self.portfolio_circuit_breaker = False
            self.engine.portfolio_circuit_breaker = self.drawdown_breaker_triggered
            self._save_portfolio_state()
            return

        total_pnl = self.engine.risk_manager.daily_pnl

        if not self.portfolio_circuit_breaker:
            if total_pnl <= -self.max_portfolio_daily_loss_usd:
                self.portfolio_circuit_breaker = True
                self.engine.portfolio_circuit_breaker = True
                logger.critical(
                    f"🚨 [PORTFOLIO CIRCUIT BREAKER TRIPPED] Daily loss "
                    f"reached {total_pnl:.2f} USD (limit: -${self.max_portfolio_daily_loss_usd:.2f}). "
                    f"HALTING ALL NEW TRADES FOR TODAY."
                )
                if config.notify_on_trade:
                    notifier.alert(
                        f"🚨 PORTFOLIO CIRCUIT BREAKER TRIPPED! Loss: ${total_pnl:.2f} USD."
                    )
                self._save_portfolio_state()

    async def run(self):
        logger.info(f"Starting Polymarket 5-Minute Bot for: {self.target_asset}")
        logger.info(f"Mode: {'[PAPER TRADING]' if config.paper_trading else '[LIVE EXECUTION]'}")

        all_ok = True
        try:
            reachable = await self.engine.market_feed.check_connectivity()
            if not reachable:
                all_ok = False
                logger.warning(f"Polymarket API connectivity check FAILED for {self.target_asset}.")
        except Exception as e:
            logger.error(f"Error during connectivity check for {self.target_asset}: {e}")
            all_ok = False

        if not all_ok:
            if config.notify_on_connectivity_failure:
                notifier.alert(
                    "🔴 Polymarket5mBot: Connectivity check failed."
                )
        elif config.notify_on_startup:
            notifier.alert(
                f"✅ Polymarket5mBot started ({'PAPER TRADING' if config.paper_trading else 'LIVE'}) "
                f"| Asset: {self.target_asset} | Polymarket connectivity OK."
            )

        self.book_ws.start()
        self.engine.spot_task = asyncio.create_task(self.engine.spot_feed.start())
        if self.engine.deribit_feed is not None:
            self.engine.deribit_task = asyncio.create_task(self.engine.deribit_feed.start())
        await self.engine._recover_orphaned_windows()

        ticker_task = asyncio.create_task(self._console_ticker())
        portfolio_risk_task = asyncio.create_task(self._portfolio_risk_loop())
        engine_task = asyncio.create_task(self._run_engine_loop(self.engine))

        try:
            await engine_task
        except asyncio.CancelledError:
            logger.info("Bot shutting down...")
        finally:
            ticker_task.cancel()
            portfolio_risk_task.cancel()
            engine_task.cancel()
            self.engine.spot_feed.stop()
            if self.engine.spot_task is not None:
                self.engine.spot_task.cancel()
            if self.engine.deribit_feed is not None:
                await self.engine.deribit_feed.stop()
            if self.engine.deribit_task is not None:
                self.engine.deribit_task.cancel()
            await self.engine.market_feed.close()
            await self.book_ws.stop()
            await notifier.close()

    async def _portfolio_risk_loop(self):
        while True:
            try:
                self.check_portfolio_risk()
            except Exception as e:
                logger.error(f"Error in portfolio risk monitor: {e}")
            await asyncio.sleep(1)

    async def _run_engine_loop(self, engine: AssetTradingEngine):
        was_paused = False
        while True:
            try:
                if is_paused():
                    if not was_paused:
                        logger.warning(f"[{engine.asset}] PAUSED via /api/control -- skipping new trade evaluation until resumed.")
                    was_paused = True
                else:
                    if was_paused:
                        logger.info(f"[{engine.asset}] RESUMED via /api/control -- trade evaluation active again.")
                    was_paused = False
                    await engine.tick()
            except Exception as e:
                logger.error(f"Unexpected error in engine loop [{engine.asset}]: {type(e).__name__}: {e}")
            await asyncio.sleep(1)

    async def _console_ticker(self):
        while True:
            await asyncio.sleep(0.1)
            try:
                now = time.time()
                engine = self.engine
                spot = engine.spot_feed.latest_price
                k = engine.strike_price
                if spot is not None and k is not None:
                    tau = max(0.0, engine.current_window_start + 300 - now)
                    yes_book = self.book_ws.get_book(engine.market_feed.token_id_yes)
                    no_book = self.book_ws.get_book(engine.market_feed.token_id_no)
                    yes_ask = f"{yes_book['best_ask']:.2f}" if yes_book and yes_book.get('best_ask') is not None else "..."
                    no_ask = f"{no_book['best_ask']:.2f}" if no_book and no_book.get('best_ask') is not None else "..."
                    line = f"  [live] {engine.asset}: ${spot:,.1f} (K:${k:,.1f}|t:{tau:.0f}s|Y:{yes_ask}|N:{no_ask})   "
                    print(f"\r{line}", end="", flush=True)
            except Exception:
                continue

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    logger.remove()
    logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>", level="INFO")
    logger.add("logs/bot_{time:YYYY-MM-DD}.log", rotation="00:00", retention="14 days", level="DEBUG", encoding="utf-8")

    bot = Polymarket5mBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
    except Exception as e:
        logger.error(f"Bot crashed: {type(e).__name__}: {e}")
        if config.notify_on_crash:
            try:
                asyncio.run(notifier.alert_and_wait(f"\U0001F480 Polymarket5mBot CRASHED: {type(e).__name__}: {e}"))
            except Exception:
                pass
        raise

