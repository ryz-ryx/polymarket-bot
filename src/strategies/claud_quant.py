from typing import Optional, Dict, Any
from scipy.stats import norm, t as student_t
import math
from loguru import logger
from src.strategies.base import BaseStrategy

SECONDS_PER_YEAR = 365.25 * 24 * 3600

# Polymarket's real taker-fee schedule for 5-min crypto Up/Down markets. Live-verified
# 2026-09-08 by querying Gamma API for the active btc-updown-5m-* market directly:
# feeType="crypto_fees_v2", feeSchedule={"exponent": 1, "rate": 0.07, "takerOnly": True,
# "rebateRate": 0.2}. Makers pay $0 and receive a rebate; only takers (which is all this
# bot ever does -- it crosses the resting book) pay this. Formula (Polymarket's published
# fee curve, confirmed against this schedule): fee = shares * rate * price * (1-price).
# Since shares = amount_usd / price, this simplifies to a clean fraction of notional:
#     fee_fraction_of_notional = rate * (1 - price)
# which is strictly monotonic: from ~7% near price -> 0 down to ~0.07% near price -> 1
# (at 50/50 odds, fee is rate/2 = 3.5% of notional). This replaces the old flat taker_fee=0.005 (0.5%) constant,
# which badly UNDERESTIMATED real costs across almost the entire price range (e.g. at p=0.50
# real fee is 3.5% vs the 0.5% assumed; even at p=0.90 it's still 0.7% vs 0.5% assumed) --
# verified by reconstructing the bot's own trade history: real fees consumed ~27% of the
# reported paper-trading edge on the first 19 settled trades ($11.50 of $41.79 gross PnL).
POLYMARKET_CRYPTO_TAKER_FEE_RATE = 0.07


def estimate_taker_fee_fraction(price: float) -> float:
    """
    Returns the taker fee as a fraction of trade notional for buying a token at `price`,
    under Polymarket's crypto_fees_v2 schedule (see POLYMARKET_CRYPTO_TAKER_FEE_RATE above).
    Clamped away from the exact 0/1 boundary to avoid degenerate negative/zero-division
    behavior from bad upstream price data.
    """
    p = max(min(price, 0.999), 0.001)
    return POLYMARKET_CRYPTO_TAKER_FEE_RATE * (1.0 - p)

class ClaudQuantBinaryOptionStrategy(BaseStrategy):
    """
    Continuous Realized-Vol Black-Scholes Binary Option Strategy.
    Fixes:
    - Binds min_edge directly into hurdle formula: hurdle = max(min_edge, spread/2 + fee + slippage)
    - Incorporates momentum alongside OFI and Polymarket Contract Book Imbalance (CBI) into drift
    - Filter against near-50/50 noise chop (|z| < min_abs_z)
    - Optional Student-t fat-tailed distribution (tail_dof) for jumpy assets (ETH/SOL)
    """
    def __init__(
        self,
        min_edge: float = 0.04,
        slippage_buffer: float = 0.01,
        ofi_drift_weight: float = 0.12,
        momentum_drift_weight: float = 0.08,
        cbi_drift_weight: float = 0.08,
        min_abs_z: float = 0.35,
        min_strike_distance_pct: float = 0.0003,
        late_window_min_prob: float = 0.75,
        tail_dof: Optional[float] = None,
        min_entry_price: Optional[float] = None,
        max_entry_price: Optional[float] = None
    ):
        super().__init__(name="ClaudQuantBinaryOption")

        # Guardrail, not a hard stop: research on binary prediction markets is unambiguous
        # that breakeven win rate equals entry price, so a $0.85 entry needs 85% accuracy
        # just to survive, with one loss wiping out ~5.7 wins at that price. Widening the
        # entry-price band or dropping min_edge to chase more trade volume recreates exactly
        # that trap. This just makes it loud if it ever happens (by hand-edit or future
        # refactor) instead of silently drifting into worse risk/reward.
        if max_entry_price is not None and max_entry_price > 0.55:
            logger.warning(
                f"ClaudQuantBinaryOptionStrategy: max_entry_price={max_entry_price} exceeds the "
                f"0.55 research-backed safety band -- breakeven win rate rises with entry price; "
                f"this increases risk of the 'need 85%+ accuracy to survive' trap."
            )
        if min_edge < 0.02:
            logger.warning(
                f"ClaudQuantBinaryOptionStrategy: min_edge={min_edge} is below the 0.02 floor -- "
                f"after fees and slippage this may not leave a real edge once live execution "
                f"degrades ~10-15% versus backtest."
            )

        self.min_edge = min_edge
        self.slippage_buffer = slippage_buffer
        self.ofi_drift_weight = ofi_drift_weight
        self.momentum_drift_weight = momentum_drift_weight
        self.cbi_drift_weight = cbi_drift_weight
        self.min_abs_z = min_abs_z
        self.min_strike_distance_pct = min_strike_distance_pct
        self.late_window_min_prob = late_window_min_prob
        self.tail_dof = tail_dof
        self.min_entry_price = min_entry_price
        self.max_entry_price = max_entry_price

    def calculate_fair_probability(
        self,
        S_t: float,
        K: float,
        tau_seconds: float,
        annualized_vol: float,
        ofi_normalized: float = 0.0,
        momentum_normalized: float = 0.0,
        cbi_normalized: float = 0.0,
        known_avg_price: Optional[float] = None,
        twap_window_sec: float = 60.0,
        regime_factor: float = 1.0
    ) -> tuple[float, float]:
        if tau_seconds <= 0.5:
            p_up = 1.0 if S_t >= K else 0.0
            return p_up, 999.0 if S_t >= K else -999.0

        tau = tau_seconds / SECONDS_PER_YEAR
        sigma = max(min(annualized_vol, 2.50), 0.30)
        sigma_sq = sigma * sigma
        sqrt_tau = math.sqrt(tau)

        # --- TWAP settlement adjustment (Asian-option variance collapse) ---
        # Polymarket settles on a trailing TWAP over the final `twap_window_sec` seconds (officially 60s),
        # not an instantaneous snapshot. Once tau < twap_window_sec, part of that averaging window
        # is already realized. Classical Asian-option variance reduction (Turnbull-Wakeman
        # moment matching) scales down variance quadratically as the window elapses.
        effective_S = S_t
        vol_time_term = sigma * sqrt_tau
        if known_avg_price is not None and known_avg_price > 0 and 0 < tau_seconds < twap_window_sec:
            f_realized = (twap_window_sec - tau_seconds) / twap_window_sec
            effective_S = f_realized * known_avg_price + (1.0 - f_realized) * S_t
            weight_remaining = tau_seconds / twap_window_sec
            vol_time_term = max(sigma * weight_remaining * math.sqrt(tau / 3.0), 1e-8)

        log_moneyness = math.log(max(effective_S, 1e-6) / max(K, 1e-6))
        z_base = (log_moneyness - 0.5 * sigma_sq * tau) / vol_time_term

        # Micro-drift from Aggressor Flow, short-term momentum, and Polymarket Contract Book Imbalance (CBI).
        # Momentum's weight is scaled by regime_factor (variance-ratio regime detector, see
        # SpotFeed.get_regime_factor): >1 in trending regimes where momentum is informative,
        # <1 in mean-reverting chop where a recent move is more likely to snap back.
        drift_adj = (
            (self.ofi_drift_weight * ofi_normalized) +
            (self.momentum_drift_weight * regime_factor * momentum_normalized) +
            (self.cbi_drift_weight * cbi_normalized)
        )
        z = z_base + drift_adj

        # Probability calculation: Student-t for fat-tailed jump assets (ETH/SOL) or Gaussian for BTC
        if self.tail_dof is not None and self.tail_dof > 2.0:
            z_std = z * math.sqrt((self.tail_dof - 2.0) / self.tail_dof)
            p_up = float(student_t.cdf(z_std, df=self.tail_dof))
        else:
            p_up = float(norm.cdf(z))

        return p_up, z

    def evaluate(
        self,
        spot_price: float,
        momentum: float,
        market_info: Dict[str, Any],
        order_book: Dict[str, Any],
        confidence_weight: float = 1.0
    ) -> Optional[Dict[str, Any]]:
        strike_k = market_info.get("strike_price", spot_price)
        time_remaining_sec = market_info.get("time_remaining_sec", 150.0)
        annualized_vol = market_info.get("annualized_vol", 0.65)
        ofi = market_info.get("ofi_normalized", 0.0)
        known_avg_price = market_info.get("known_avg_price")
        twap_window_sec = market_info.get("twap_window_sec", 60.0)
        regime_factor = market_info.get("regime_factor", 1.0)

        # Extract Contract Book Imbalance (CBI) from Polymarket depth ladder
        cbi = float(order_book.get("cbi", 0.0))

        # Normalize momentum over [-1, 1] relative to typical $50 10s swing
        norm_momentum = max(min(momentum / 50.0, 1.0), -1.0)

        p_model_up, z = self.calculate_fair_probability(
            S_t=spot_price,
            K=strike_k,
            tau_seconds=time_remaining_sec,
            annualized_vol=annualized_vol,
            ofi_normalized=ofi,
            momentum_normalized=norm_momentum,
            cbi_normalized=cbi,
            known_avg_price=known_avg_price,
            twap_window_sec=twap_window_sec,
            regime_factor=regime_factor
        )

        # Skip near-50/50 noise chop
        if abs(z) < self.min_abs_z:
            return None

        # Shrink toward the market's own implied probability when this asset's
        # model hasn't proven it beats the market -- confidence_weight comes from
        # EmpiricalCalibrator.get_confidence_weight(), 1.0 = no shrinkage.
        if confidence_weight < 1.0:
            yes_ask_for_blend = market_info.get("yes_ask", 0.52)
            p_model_up = confidence_weight * p_model_up + (1.0 - confidence_weight) * yes_ask_for_blend

        yes_ask = market_info.get("yes_ask", 0.52)
        yes_bid = market_info.get("yes_bid", 0.48)
        spread = max(yes_ask - yes_bid, 0.01)
        no_ask = market_info.get("no_ask", 1.0 - yes_bid)

        # Fee-aware hurdle, computed PER SIDE since Polymarket's real taker fee depends on
        # the exact price of the token being bought (fee_fraction = rate * (1 - price)), not
        # a flat assumption. This is real money: at p=0.50 the true fee is 3.5% of notional,
        # 7x the old flat 0.5% estimate -- using one shared hurdle for both sides would let
        # trades through that don't actually clear their own real transaction cost.
        hurdle_yes = max(self.min_edge, (spread / 2.0) + estimate_taker_fee_fraction(yes_ask) + self.slippage_buffer)
        hurdle_no = max(self.min_edge, (spread / 2.0) + estimate_taker_fee_fraction(no_ask) + self.slippage_buffer)

        edge_yes = p_model_up - yes_ask
        p_model_down = 1.0 - p_model_up
        edge_no = p_model_down - no_ask

        # Minimum strike distance filter: avoid trading inside random tick chop when spot is hugging K
        strike_dist_pct = abs(spot_price - strike_k) / max(strike_k, 1e-6)
        if strike_dist_pct < self.min_strike_distance_pct:
            return None

        is_terminal_window = time_remaining_sec <= 45.0

        if edge_yes > hurdle_yes:
            # Payout-ratio / entry-price filter: enforce bounding on 1/price
            if self.min_entry_price is not None and yes_ask < self.min_entry_price:
                return None
            if self.max_entry_price is not None and yes_ask > self.max_entry_price:
                return None

            # Late-window conviction filter: at tau <= 30s, only enter high-probability lock-in trades
            if time_remaining_sec <= 30.0 and p_model_up < self.late_window_min_prob:
                return None

            return {
                "outcome": "YES",
                "direction": "UP",
                "estimated_prob": p_model_up,
                "market_price": yes_ask,
                "edge": edge_yes,
                "hurdle": hurdle_yes,
                "z": z,
                "tau_sec": time_remaining_sec,
                "moneyness": spot_price / strike_k,
                "is_terminal_convexity": is_terminal_window,
                "reason": f"YES edge {edge_yes*100:.1f}% > hurdle {hurdle_yes*100:.1f}% (z={z:.2f}, tau={time_remaining_sec:.0f}s)"
            }
        elif edge_no > hurdle_no:
            # Payout-ratio / entry-price filter: enforce bounding on 1/price
            if self.min_entry_price is not None and no_ask < self.min_entry_price:
                return None
            if self.max_entry_price is not None and no_ask > self.max_entry_price:
                return None

            # Late-window conviction filter: at tau <= 30s, only enter high-probability lock-in trades
            if time_remaining_sec <= 30.0 and p_model_down < self.late_window_min_prob:
                return None

            return {
                "outcome": "NO",
                "direction": "DOWN",
                "estimated_prob": p_model_down,
                "market_price": no_ask,
                "edge": edge_no,
                "hurdle": hurdle_no,
                "z": z,
                "tau_sec": time_remaining_sec,
                "moneyness": spot_price / strike_k,
                "is_terminal_convexity": is_terminal_window,
                "reason": f"NO edge {edge_no*100:.1f}% > hurdle {hurdle_no*100:.1f}% (z={z:.2f}, tau={time_remaining_sec:.0f}s)"
            }

        return None
