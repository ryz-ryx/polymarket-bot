from typing import Optional, Dict, Any
from scipy.stats import norm
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
# which peaks at rate/2 = 3.5% of notional right at 50/50 odds and decays toward 0 as the
# price approaches the extremes. This replaces the old flat taker_fee=0.005 (0.5%) constant,
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
    - Incorporates momentum alongside OFI into normalized drift adjustment
    - Filter against near-50/50 noise chop (|z| < min_abs_z)
    """
    def __init__(
        self,
        min_edge: float = 0.04,
        taker_fee: float = 0.005,
        slippage_buffer: float = 0.01,
        ofi_drift_weight: float = 0.12,
        momentum_drift_weight: float = 0.08,
        min_abs_z: float = 0.35
    ):
        super().__init__(name="ClaudQuantBinaryOption")
        self.min_edge = min_edge
        self.taker_fee = taker_fee
        self.slippage_buffer = slippage_buffer
        self.ofi_drift_weight = ofi_drift_weight
        self.momentum_drift_weight = momentum_drift_weight
        self.min_abs_z = min_abs_z

    def calculate_fair_probability(
        self,
        S_t: float,
        K: float,
        tau_seconds: float,
        annualized_vol: float,
        ofi_normalized: float = 0.0,
        momentum_normalized: float = 0.0,
        known_avg_price: Optional[float] = None,
        twap_window_sec: float = 60.0
    ) -> tuple[float, float]:
        if tau_seconds <= 0.5:
            p_up = 1.0 if S_t >= K else 0.0
            return p_up, 999.0 if S_t >= K else -999.0

        tau = tau_seconds / SECONDS_PER_YEAR
        sigma = max(min(annualized_vol, 2.50), 0.30)
        sigma_sq = sigma * sigma
        sqrt_tau = math.sqrt(tau)

        # --- TWAP settlement adjustment (Asian-option variance collapse) ---
        # Polymarket settles on a trailing TWAP over the final `twap_window_sec` seconds, not
        # an instantaneous snapshot. Once tau < twap_window_sec, part of that averaging window
        # is already realized, so treating this as a plain point-price binary badly overstates
        # remaining uncertainty. Classical Asian-option variance reduction (Turnbull-Wakeman
        # moment matching) gives Var ~ sigma^2 * tau / 3 for a *fully unelapsed* continuous
        # average over a window -- a flat 3x reduction vs. the point-price sigma^2*tau. Here
        # part of the window is already known, so the remaining random contribution is further
        # scaled down by the (tau/W) weight it carries in the average: effective variance ~
        # (tau/W)^2 * sigma^2 * tau / 3, i.e. QUADRATIC decay in tau instead of linear. This
        # also means the correct reference price is a blend of the already-realized partial
        # average and current price, not just current price -- so a spike-and-revert earlier
        # in the averaging window still tilts the estimate correctly.
        # `twap_window_sec` (W) itself is still empirically unresolved (30s vs 60s per public
        # reporting) -- see the [ESTIMATOR SCORECARD] logging in bot.py, which is gathering the
        # live data to pin this down. Defaulting to 60s in the meantime.
        effective_S = S_t
        vol_time_term = sigma * sqrt_tau
        if known_avg_price is not None and known_avg_price > 0 and 0 < tau_seconds < twap_window_sec:
            f_realized = (twap_window_sec - tau_seconds) / twap_window_sec
            effective_S = f_realized * known_avg_price + (1.0 - f_realized) * S_t
            weight_remaining = tau_seconds / twap_window_sec
            vol_time_term = max(sigma * weight_remaining * math.sqrt(tau / 3.0), 1e-8)

        log_moneyness = math.log(max(effective_S, 1e-6) / max(K, 1e-6))
        z_base = (log_moneyness - 0.5 * sigma_sq * tau) / vol_time_term

        # Micro-drift from both Aggressor Flow and short-term momentum
        drift_adj = (self.ofi_drift_weight * ofi_normalized) + (self.momentum_drift_weight * momentum_normalized)
        z = z_base + drift_adj

        p_up = float(norm.cdf(z))
        return p_up, z

    def evaluate(
        self,
        spot_price: float,
        momentum: float,
        market_info: Dict[str, Any],
        order_book: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        strike_k = market_info.get("strike_price", spot_price)
        time_remaining_sec = market_info.get("time_remaining_sec", 150.0)
        annualized_vol = market_info.get("annualized_vol", 0.65)
        ofi = market_info.get("ofi_normalized", 0.0)
        known_avg_price = market_info.get("known_avg_price")
        twap_window_sec = market_info.get("twap_window_sec", 60.0)

        # Normalize momentum over [-1, 1] relative to typical $50 10s swing
        norm_momentum = max(min(momentum / 50.0, 1.0), -1.0)

        p_model_up, z = self.calculate_fair_probability(
            S_t=spot_price,
            K=strike_k,
            tau_seconds=time_remaining_sec,
            annualized_vol=annualized_vol,
            ofi_normalized=ofi,
            momentum_normalized=norm_momentum,
            known_avg_price=known_avg_price,
            twap_window_sec=twap_window_sec
        )

        # Skip near-50/50 noise chop
        if abs(z) < self.min_abs_z:
            return None

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

        is_terminal_window = time_remaining_sec <= 45.0

        if edge_yes > hurdle_yes:
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
