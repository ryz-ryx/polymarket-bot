from typing import Optional, Dict, Any
from scipy.stats import norm
import math
from loguru import logger
from src.strategies.base import BaseStrategy

SECONDS_PER_YEAR = 365.25 * 24 * 3600

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
        momentum_normalized: float = 0.0
    ) -> tuple[float, float]:
        if tau_seconds <= 0.5:
            p_up = 1.0 if S_t >= K else 0.0
            return p_up, 999.0 if S_t >= K else -999.0

        tau = tau_seconds / SECONDS_PER_YEAR
        sigma = max(min(annualized_vol, 2.50), 0.30)
        sigma_sq = sigma * sigma
        sqrt_tau = math.sqrt(tau)

        log_moneyness = math.log(max(S_t, 1e-6) / max(K, 1e-6))
        z_base = (log_moneyness - 0.5 * sigma_sq * tau) / (sigma * sqrt_tau)

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

        # Normalize momentum over [-1, 1] relative to typical $50 10s swing
        norm_momentum = max(min(momentum / 50.0, 1.0), -1.0)

        p_model_up, z = self.calculate_fair_probability(
            S_t=spot_price,
            K=strike_k,
            tau_seconds=time_remaining_sec,
            annualized_vol=annualized_vol,
            ofi_normalized=ofi,
            momentum_normalized=norm_momentum
        )

        # Skip near-50/50 noise chop
        if abs(z) < self.min_abs_z:
            return None

        yes_ask = market_info.get("yes_ask", 0.52)
        yes_bid = market_info.get("yes_bid", 0.48)
        spread = max(yes_ask - yes_bid, 0.01)

        # Edge hurdle properly respects min_edge
        friction = (spread / 2.0) + self.taker_fee + self.slippage_buffer
        hurdle = max(self.min_edge, friction)

        edge_yes = p_model_up - yes_ask
        p_model_down = 1.0 - p_model_up
        no_ask = market_info.get("no_ask", 1.0 - yes_bid)
        edge_no = p_model_down - no_ask

        is_terminal_window = time_remaining_sec <= 45.0

        if edge_yes > hurdle:
            return {
                "outcome": "YES",
                "direction": "UP",
                "estimated_prob": p_model_up,
                "market_price": yes_ask,
                "edge": edge_yes,
                "hurdle": hurdle,
                "z": z,
                "tau_sec": time_remaining_sec,
                "moneyness": spot_price / strike_k,
                "is_terminal_convexity": is_terminal_window,
                "reason": f"YES edge {edge_yes*100:.1f}% > hurdle {hurdle*100:.1f}% (z={z:.2f}, tau={time_remaining_sec:.0f}s)"
            }
        elif edge_no > hurdle:
            return {
                "outcome": "NO",
                "direction": "DOWN",
                "estimated_prob": p_model_down,
                "market_price": no_ask,
                "edge": edge_no,
                "hurdle": hurdle,
                "z": z,
                "tau_sec": time_remaining_sec,
                "moneyness": spot_price / strike_k,
                "is_terminal_convexity": is_terminal_window,
                "reason": f"NO edge {edge_no*100:.1f}% > hurdle {hurdle*100:.1f}% (z={z:.2f}, tau={time_remaining_sec:.0f}s)"
            }

        return None
