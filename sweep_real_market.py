"""
Parameter sweep against REAL Polymarket historical data (same dataset as
replay_real_market.py) -- tests whether widening/narrowing the strategy's
entry filters finds more real edge without dropping win rate below breakeven,
instead of guessing at hyperparameters.
"""
import argparse
from replay_real_market import load_real_windows, load_binance_spot, asset_paths
from src.strategies.claud_quant import ClaudQuantBinaryOptionStrategy, estimate_taker_fee_fraction
import pandas as pd


def run_variant(real, spot_groups, **strategy_kwargs):
    strategy = ClaudQuantBinaryOptionStrategy(**strategy_kwargs)
    trades = []
    for _, row in real.iterrows():
        wts = row["window_ts"]
        group = spot_groups.get(wts)
        if group is None or len(group) != 5:
            continue
        strike_k = group.loc[0, "open"]
        mid_spot = group.loc[2, "close"]
        vol_ann = group.loc[2, "vol_ann"]
        if pd.isna(vol_ann) or vol_ann <= 0:
            continue
        mom_dollar = mid_spot - group.loc[1, "close"]
        norm_momentum = max(min(mom_dollar / 50.0, 1.0), -1.0)

        yes_ask_real = row["yes_ask_real"]
        no_ask_real = 1.0 - yes_ask_real
        market_info = {
            "strike_price": strike_k, "time_remaining_sec": 120.0,
            "annualized_vol": float(vol_ann), "ofi_normalized": 0.0,
            "known_avg_price": None, "twap_window_sec": 60.0, "regime_factor": 1.0,
            "yes_ask": yes_ask_real, "yes_bid": max(yes_ask_real - 0.02, 0.01),
            "no_ask": no_ask_real,
        }
        signal = strategy.evaluate(
            spot_price=mid_spot, momentum_normalized=norm_momentum, market_info=market_info,
            order_book={"cbi": 0.0}, confidence_weight=1.0,
        )
        if signal is None:
            continue
        entry_price = signal["market_price"]
        fee_frac = estimate_taker_fee_fraction(entry_price)
        shares = 1.0 / entry_price
        won = (signal["outcome"] == "YES" and row["realized_up"] == 1) or \
              (signal["outcome"] == "NO" and row["realized_up"] == 0)
        net_pnl = (shares if won else 0.0) - 1.0 - fee_frac
        trades.append({"won": won, "net_pnl": net_pnl})

    tdf = pd.DataFrame(trades)
    n = len(tdf)
    if n == 0:
        return {"n_trades": 0, "win_rate": None, "total_pnl": 0.0, "avg_pnl": None}
    return {
        "n_trades": n,
        "win_rate": tdf["won"].mean(),
        "total_pnl": tdf["net_pnl"].sum(),
        "avg_pnl": tdf["net_pnl"].mean(),
    }


def run_grid(real, spot_groups, min_trades=20):
    rows = []
    for min_edge in (0.02, 0.03, 0.05):
        for min_abs_z in (0.25, 0.30, 0.40, 0.55, 0.70):
            for lo, hi in ((0.15, 0.55), (0.20, 0.55), (0.25, 0.55), (0.333, 0.50), (0.30, 0.60), (0.35, 0.65)):
                r = run_variant(real, spot_groups, min_edge=min_edge, min_abs_z=min_abs_z,
                                 tail_dof=None, min_entry_price=lo, max_entry_price=hi)
                rows.append({"min_edge": min_edge, "min_abs_z": min_abs_z,
                             "min_entry_price": lo, "max_entry_price": hi, **r})
    df = pd.DataFrame(rows)
    viable = df[df["n_trades"] >= min_trades].sort_values("total_pnl", ascending=False)
    print(f"\nFull grid: {len(df)} combos ({min_trades}+ trade combos: {len(viable)})")
    print("\nTop 10 by total PnL:")
    print(viable.head(10).to_string(index=False))
    print("\nTop 10 by avg PnL/trade (min_trades filter still applied):")
    print(viable.sort_values("avg_pnl", ascending=False).head(10).to_string(index=False))
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", default="BTC")
    ap.add_argument("--grid", action="store_true", help="run full parameter grid search instead of the fixed named variants")
    args = ap.parse_args()
    asset = args.asset.upper()

    real_path, spot_path = asset_paths(asset)
    real = load_real_windows(real_path)
    spot = load_binance_spot(spot_path)
    spot_groups = {wts: g.sort_values("minute_idx").reset_index(drop=True) for wts, g in spot.groupby("window_ts")}
    print(f"Asset: {asset} ({len(real)} real windows loaded from {real_path})")

    if args.grid:
        run_grid(real, spot_groups)
        return

    variants = {
        "baseline (current live config)": dict(min_edge=0.03, min_abs_z=0.40, tail_dof=None, min_entry_price=0.333, max_entry_price=0.50),
        "wider entry band 0.25-0.55":       dict(min_edge=0.03, min_abs_z=0.40, tail_dof=None, min_entry_price=0.25, max_entry_price=0.55),
        "lower min_edge 0.02":              dict(min_edge=0.02, min_abs_z=0.40, tail_dof=None, min_entry_price=0.333, max_entry_price=0.50),
        "higher min_edge 0.05":             dict(min_edge=0.05, min_abs_z=0.40, tail_dof=None, min_entry_price=0.333, max_entry_price=0.50),
        "lower min_abs_z 0.25 (less chop filter)": dict(min_edge=0.03, min_abs_z=0.25, tail_dof=None, min_entry_price=0.333, max_entry_price=0.50),
        "higher min_abs_z 0.55 (stricter)": dict(min_edge=0.03, min_abs_z=0.55, tail_dof=None, min_entry_price=0.333, max_entry_price=0.50),
        "narrower band 0.35-0.45":          dict(min_edge=0.03, min_abs_z=0.40, tail_dof=None, min_entry_price=0.35, max_entry_price=0.45),
        "wide band 0.20-0.60":               dict(min_edge=0.03, min_abs_z=0.40, tail_dof=None, min_entry_price=0.20, max_entry_price=0.60),
        "wide band + stricter z":            dict(min_edge=0.03, min_abs_z=0.55, tail_dof=None, min_entry_price=0.25, max_entry_price=0.55),
        "full band no cap (0.05-0.95)":       dict(min_edge=0.03, min_abs_z=0.40, tail_dof=None, min_entry_price=0.05, max_entry_price=0.95),
    }

    print(f"{'variant':<42} {'n':>5} {'win%':>8} {'total_pnl':>10} {'avg_pnl':>9}")
    print("-" * 78)
    for name, kwargs in variants.items():
        r = run_variant(real, spot_groups, **kwargs)
        win_str = f"{r['win_rate']*100:.1f}%" if r["win_rate"] is not None else "n/a"
        avg_str = f"{r['avg_pnl']:.3f}" if r["avg_pnl"] is not None else "n/a"
        print(f"{name:<42} {r['n_trades']:>5} {win_str:>8} {r['total_pnl']:>10.3f} {avg_str:>9}")


if __name__ == "__main__":
    main()
