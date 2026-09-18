"""
Genuine backtested P&L: replays ClaudQuantBinaryOptionStrategy against REAL
Polymarket market data (data/real_market_history_btc.jsonl, pulled live via
Railway SSH from Gamma/CLOB APIs -- real resolutions, real yes-token prices,
real fees) instead of backtest.py's synthetic strike/price assumptions.

Model inputs (spot price, realized vol, momentum) still come from Binance
klines, same as backtest.py -- there's no historical order-flow/CBI feed
available, so those two drift inputs are 0.0 here (flagged in output, not
faked).
"""
import argparse
import json
import math
import pandas as pd
import numpy as np
from src.strategies.claud_quant import ClaudQuantBinaryOptionStrategy, estimate_taker_fee_fraction

VOL_WINDOW_MIN = 30
MINUTES_PER_YEAR = 365.25 * 24 * 60

# Strategy params actually deployed in src/bot.py, keyed by asset. BTC's are the
# production values; ETH/SOL fall back to BTC's until their own real-market
# replay/sweep justifies asset-specific ones.
LIVE_STRATEGY_PARAMS = {
    "BTC": dict(min_edge=0.03, min_abs_z=0.55, tail_dof=None, min_entry_price=0.25, max_entry_price=0.55),
    "ETH": dict(min_edge=0.03, min_abs_z=0.70, tail_dof=None, min_entry_price=0.15, max_entry_price=0.55),
    "SOL": dict(min_edge=0.03, min_abs_z=0.70, tail_dof=None, min_entry_price=0.15, max_entry_price=0.55),
}


def asset_paths(asset: str):
    asset = asset.upper()
    real_path = "data/real_market_history_btc.jsonl" if asset == "BTC" else f"data/real_market_history_{asset.lower()}.jsonl"
    spot_path = f"data/cache/{asset}USDT_1m_1095d.csv"
    return real_path, spot_path


def load_real_windows(path: str) -> pd.DataFrame:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("_meta"):
                continue
            try:
                outcome_prices = json.loads(d["outcome_prices"])
            except Exception:
                continue
            if d.get("yes_price_tau120") is None:
                continue
            # outcomePrices = [price_of_outcome0, price_of_outcome1]; Polymarket
            # convention for these markets is outcome index 0 = "Up"/Yes at
            # resolution (settled price is 1.0 for the winning side, 0.0 for the
            # loser) -- realized_up=1 iff the Yes/Up side paid out.
            realized_up = 1 if float(outcome_prices[0]) >= 0.5 else 0
            rows.append({
                "window_ts": d["window_ts"],
                "yes_ask_real": float(d["yes_price_tau120"]),
                "realized_up": realized_up,
                "volume": d.get("volume"),
            })
    return pd.DataFrame(rows)


def load_binance_spot(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["log_ret"] = np.log(df["close"] / df["close"].shift(1))
    rolling_var = df["log_ret"].rolling(window=VOL_WINDOW_MIN).var()
    df["vol_ann"] = np.sqrt(rolling_var * MINUTES_PER_YEAR).clip(lower=0.30, upper=2.50)
    df["open_sec"] = df["open_time"] // 1000
    df["window_ts"] = (df["open_sec"] // 300) * 300
    df["minute_idx"] = (df["open_sec"] - df["window_ts"]) // 60
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", default="BTC")
    args = ap.parse_args()
    asset = args.asset.upper()

    real_path, spot_path = asset_paths(asset)
    real = load_real_windows(real_path)
    spot = load_binance_spot(spot_path)

    strategy = ClaudQuantBinaryOptionStrategy(**LIVE_STRATEGY_PARAMS.get(asset, LIVE_STRATEGY_PARAMS["BTC"]))

    spot_groups = {wts: g.sort_values("minute_idx").reset_index(drop=True) for wts, g in spot.groupby("window_ts")}

    trades = []
    skipped_no_spot = 0
    for _, row in real.iterrows():
        wts = row["window_ts"]
        group = spot_groups.get(wts)
        if group is None or len(group) != 5:
            skipped_no_spot += 1
            continue

        strike_k = group.loc[0, "open"]
        mid_spot = group.loc[2, "close"]
        vol_ann = group.loc[2, "vol_ann"]
        if pd.isna(vol_ann) or vol_ann <= 0:
            continue
        mom_dollar = mid_spot - group.loc[0, "open"]  # 180s lookback, matches live
        norm_momentum = max(min((mom_dollar / mid_spot) / 0.006, 1.0), -1.0)  # relative return, as in src/bot.py

        yes_ask_real = row["yes_ask_real"]
        no_ask_real = 1.0 - yes_ask_real  # complement, since these are binary complementary tokens
        market_info = {
            "strike_price": strike_k,
            "time_remaining_sec": 120.0,
            "annualized_vol": float(vol_ann),
            "ofi_normalized": 0.0,
            "known_avg_price": None,
            "twap_window_sec": 60.0,
            "regime_factor": 1.0,
            "yes_ask": yes_ask_real,
            "yes_bid": max(yes_ask_real - 0.02, 0.01),  # real bid unavailable; assume 2c spread (conservative floor)
            "no_ask": no_ask_real,
        }
        signal = strategy.evaluate(
            spot_price=mid_spot,
            momentum_normalized=norm_momentum,
            market_info=market_info,
            order_book={"cbi": 0.0},
            confidence_weight=1.0,
        )
        if signal is None:
            continue

        entry_price = signal["market_price"]
        fee_frac = estimate_taker_fee_fraction(entry_price)
        stake = 1.0  # normalize to $1 stake; scale later
        shares = stake / entry_price
        fee_paid = stake * fee_frac
        won = (signal["outcome"] == "YES" and row["realized_up"] == 1) or \
              (signal["outcome"] == "NO" and row["realized_up"] == 0)
        payout = shares if won else 0.0
        net_pnl = payout - stake - fee_paid

        trades.append({
            "window_ts": wts,
            "outcome": signal["outcome"],
            "entry_price": entry_price,
            "model_prob": signal["estimated_prob"],
            "edge": signal["edge"],
            "won": won,
            "net_pnl": net_pnl,
        })

    tdf = pd.DataFrame(trades)
    print("=" * 55)
    print(f"REAL-MARKET REPLAY [{asset}]: {len(real)} real Polymarket windows loaded")
    print(f"Spot data matched: {len(real) - skipped_no_spot}/{len(real)}")
    print(f"NOTE: OFI and CBI drift inputs = 0.0 (no historical order-flow feed available) -- real spot/vol/momentum, real market prices, real fees, real resolutions.")
    print("=" * 55)
    if tdf.empty:
        print("No trades fired -- model's edge never cleared the fee-aware hurdle against real market prices in this window.")
        return

    win_rate = tdf["won"].mean()
    total_pnl_per_dollar = tdf["net_pnl"].sum()
    avg_pnl_per_trade = tdf["net_pnl"].mean()
    print(f"Trades fired: {len(tdf)} / {len(real)} windows ({len(tdf)/len(real)*100:.2f}%)")
    print(f"Real win rate: {win_rate*100:.2f}%")
    print(f"Total net PnL per $1 staked/trade: ${total_pnl_per_dollar:.4f}")
    print(f"Avg net PnL per trade (per $1 staked): ${avg_pnl_per_trade:.4f}")
    print(f"Brier (model_prob vs realized outcome, trades only): {((tdf['model_prob'] - tdf['won'].astype(int))**2).mean():.4f}")
    print("=" * 55)
    out_suffix = "" if asset == "BTC" else f"_{asset.lower()}"
    tdf.to_csv(f"data/real_market_replay_trades{out_suffix}.csv", index=False)


if __name__ == "__main__":
    main()
