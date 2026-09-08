import os
import json
import asyncio
import time
import math
from typing import Dict, Any, List
from loguru import logger
from config import config
from src.spot_feed import SpotFeed
from src.market_feed import PolymarketFeed
from src.risk_manager import RiskManager
from src.executor import OrderExecutor
from src.calibrator import EmpiricalCalibrator
from src.event_logger import TradeEventLogger
from src.strategies.claud_quant import ClaudQuantBinaryOptionStrategy

RESOLUTIONS_FILE = "data/pending_resolutions.json"
RECONCILIATION_FILE = "data/fallback_reconciliation.json"
RECONCILIATION_MAX_AGE_SEC = 3600.0  # give up chasing on-chain confirmation after 1hr

class Polymarket5mBot:
    def __init__(self):
        self.risk_manager = RiskManager(
            max_position_usd=config.max_position_usd,
            max_daily_loss_usd=config.max_daily_loss_usd,
            kelly_fraction=config.kelly_fraction
        )
        self.executor = OrderExecutor(paper_trading=config.paper_trading)
        self.strategy = ClaudQuantBinaryOptionStrategy(
            min_edge=0.03,
            taker_fee=0.005,
            slippage_buffer=config.slippage_tolerance,
            min_abs_z=0.35
        )
        self.market_feed = PolymarketFeed(asset=config.target_asset)
        self.spot_feed = SpotFeed(symbol=f"{config.target_asset}USDT")
        self.calibrator = EmpiricalCalibrator()
        self.event_logger = TradeEventLogger()
        self.last_trade_time = 0.0

        self.current_window_id = int(time.time() // 300)
        self.current_window_start = self.current_window_id * 300
        self.strike_price = None

        # Persistent disk-backed pending resolution queue
        self.resolutions_file = RESOLUTIONS_FILE
        self.pending_resolutions: List[Dict[str, Any]] = []
        self._load_pending_resolutions()

        # Persistent disk-backed reconciliation queue: windows settled provisionally via
        # Binance-fallback whose calibration label / paper PnL may still need correcting
        # once (if) the real on-chain outcome eventually arrives.
        self.reconciliation_file = RECONCILIATION_FILE
        self.fallback_reconciliation: List[Dict[str, Any]] = []
        self._load_fallback_reconciliation()

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
                logger.debug(f"Resolution poll notice for {slug}: {e}")
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
                    window_pnl = self.executor.settle_window_positions(
                        window_id=window_id,
                        realized_up=outcome,
                        source="POLYMARKET_ONCHAIN"
                    )
                    self.risk_manager.record_trade_result(pnl=window_pnl)
                except Exception as e:
                    logger.error(f"Error settling window positions: {e}")
                changed = True

            elif now - queued_at > 240:
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
                logger.debug(f"Reconciliation poll notice for {slug}: {e}")
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

    async def run(self):
        logger.info(f"Starting Polymarket 5-Minute Claud-Quant Bot for {config.target_asset}")
        logger.info(f"Mode: {'[PAPER TRADING]' if config.paper_trading else '[LIVE EXECUTION]'}")
        
        spot_task = asyncio.create_task(self.spot_feed.start())

        try:
            while True:
                await asyncio.sleep(1)

                if self.spot_feed.latest_price is None:
                    continue

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
                vol_ann = self.spot_feed.annualized_vol
                ofi = self.spot_feed.get_ofi_normalized()
                momentum = self.spot_feed.get_momentum(lookback_seconds=10.0)

                await self.market_feed.find_active_5min_market()
                live_quotes = await self.market_feed.get_live_market_prices()

                norm_momentum = max(min(momentum / 50.0, 1.0), -1.0)
                raw_p_model, z = self.strategy.calculate_fair_probability(
                    S_t=spot_price,
                    K=self.strike_price,
                    tau_seconds=time_remaining_sec,
                    annualized_vol=vol_ann,
                    ofi_normalized=ofi,
                    momentum_normalized=norm_momentum
                )

                calibrated_p_up = self.calibrator.calibrate(raw_p_model)

                self.calibrator.log_observation(
                    window_id=self.current_window_id,
                    tau_sec=time_remaining_sec,
                    moneyness=spot_price / self.strike_price,
                    vol_ann=vol_ann,
                    ofi=ofi,
                    z=z,
                    p_model=raw_p_model,
                    p_market=live_quotes["yes_ask"]
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
                }

                signal = self.strategy.evaluate(
                    spot_price=spot_price,
                    momentum=momentum,
                    market_info=market_info,
                    order_book={}
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
                    signal["estimated_prob"] = calibrated_p_up if signal["outcome"] == "YES" else (1.0 - calibrated_p_up)
                    is_yes = (signal["outcome"] == "YES")
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
                        direct_hurdle = max(
                            self.strategy.min_edge,
                            (direct_spread / 2.0) + self.strategy.taker_fee + self.strategy.slippage_buffer
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
                                bankroll=bankroll
                            )

                            if size > 0:
                                token_target = self.market_feed.token_id_yes if is_yes else (self.market_feed.token_id_no or "token_no")
                                active_slug = self.market_feed.get_slug_for_window(self.current_window_start)
                                await self.executor.execute_trade(
                                    window_id=self.current_window_id,
                                    slug=active_slug,
                                    token_id=token_target or "clob_token_default",
                                    outcome=signal["outcome"],
                                    amount_usd=size,
                                    price=exec_price
                                )
                                self.last_trade_time = now

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

                if int(now) % 10 == 0:
                    book_type = "LIVE" if live_quotes.get("is_live_book") else "SYNTH"
                    dir_y = f"{live_quotes['direct_yes_ask']:.2f}" if live_quotes['direct_yes_ask'] else "None"
                    dir_n = f"{live_quotes['direct_no_ask']:.2f}" if live_quotes['direct_no_ask'] else "None"
                    pending_count = len(self.pending_resolutions)
                    can_trade_now = self.risk_manager.can_trade()
                    status_str = "ACTIVE" if can_trade_now else "HALTED (RISK)"
                    logger.info(
                        f"Spot: ${spot_price:.2f} | K: ${self.strike_price:.2f} | "
                        f"Tau: {time_remaining_sec:.0f}s | Vol: {vol_ann*100:.1f}% | "
                        f"OFI: {ofi:+.2f} | z: {z:+.2f} | P(up): {calibrated_p_up*100:.1f}% | "
                        f"Trade: [{status_str}] | Cash: ${self.executor.simulated_balance:.2f} | Day PnL: {self.risk_manager.daily_pnl:+.2f} USD | Pending Res: {pending_count}"
                    )

        except asyncio.CancelledError:
            logger.info("Bot shutting down...")
        finally:
            self.spot_feed.stop()
            spot_task.cancel()
            await self.market_feed.close()
