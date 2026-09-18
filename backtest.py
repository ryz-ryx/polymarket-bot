import os
import sys
import time
import math
import argparse
from typing import List, Dict, Any, Optional
import requests
import pandas as pd
import numpy as np
from loguru import logger

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from src.strategies.claud_quant import ClaudQuantBinaryOptionStrategy, SECONDS_PER_YEAR

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"

ASSET_CONFIGS = {
    "BTC": {
        "symbol": "BTCUSDT",
        "tail_dof": None,
        "min_edge": 0.03,
        "min_abs_z": 0.40,
        "min_entry_price": 0.333,
        "max_entry_price": 0.50,
    },
    "ETH": {
        "symbol": "ETHUSDT",
        "tail_dof": 4.5,
        "min_edge": 0.045,
        "min_abs_z": 0.50,
        "min_entry_price": None,
        "max_entry_price": None,
    },
    "SOL": {
        "symbol": "SOLUSDT",
        "tail_dof": 4.5,
        "min_edge": 0.03,
        "min_abs_z": 0.40,
        "min_entry_price": None,
        "max_entry_price": None,
    },
}

def fetch_binance_klines(symbol: str, days: int = 60, cache_dir: str = "data/cache") -> pd.DataFrame:
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f"{symbol}_1m_{days}d.parquet")
    
    if os.path.exists(cache_path):
        logger.info(f"Loading cached {symbol} 1m klines from {cache_path}")
        try:
            return pd.read_parquet(cache_path)
        except Exception:
            pass

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (days * 24 * 3600 * 1000)
    
    logger.info(f"Fetching {days} days of 1m klines for {symbol} from Binance API...")
    
    all_candles = []
    current_start = start_ms
    batch_limit = 1000
    
    while current_start < now_ms:
        params = {
            "symbol": symbol,
            "interval": "1m",
            "startTime": current_start,
            "limit": batch_limit
        }
        try:
            resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=10)
            if resp.status_code != 200:
                logger.error(f"Binance API returned HTTP {resp.status_code}: {resp.text}")
                time.sleep(2)
                continue
                
            data = resp.json()
            if not data or not isinstance(data, list):
                break
                
            all_candles.extend(data)
            last_open = data[-1][0]
            current_start = last_open + 60000
            
            if len(data) < batch_limit:
                break
                
            time.sleep(0.04)
        except Exception as e:
            logger.warning(f"Error fetching chunk at {current_start}: {e}. Retrying...")
            time.sleep(1)

    logger.info(f"Downloaded {len(all_candles)} candles for {symbol}.")
    
    records = []
    for c in all_candles:
        records.append({
            "open_time": int(c[0]),
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
            "volume": float(c[5]),
            "close_time": int(c[6])
        })
        
    df = pd.DataFrame(records)
    df.drop_duplicates(subset=["open_time"], inplace=True)
    df.sort_values("open_time", inplace=True)
    df.reset_index(drop=True, inplace=True)
    
    try:
        df.to_parquet(cache_path)
    except Exception:
        df.to_csv(cache_path.replace(".parquet", ".csv"), index=False)
        
    return df

def run_backtest_for_asset(
    asset: str,
    days: int = 60,
    vol_window_minutes: int = 30
) -> pd.DataFrame:
    cfg = ASSET_CONFIGS[asset]
    symbol = cfg["symbol"]
    
    df_1m = fetch_binance_klines(symbol=symbol, days=days)
    if len(df_1m) < vol_window_minutes + 10:
        raise ValueError(f"Insufficient kline data for {symbol}: {len(df_1m)} rows.")

    strategy = ClaudQuantBinaryOptionStrategy(
        min_edge=cfg["min_edge"],
        min_abs_z=cfg["min_abs_z"],
        tail_dof=cfg["tail_dof"],
        min_entry_price=cfg["min_entry_price"],
        max_entry_price=cfg["max_entry_price"]
    )
    
    MINUTES_PER_YEAR = 365.25 * 24 * 60
    df_1m["log_ret"] = np.log(df_1m["close"] / df_1m["close"].shift(1))
    rolling_var_1m = df_1m["log_ret"].rolling(window=vol_window_minutes).var()
    raw_ann_vol = np.sqrt(rolling_var_1m * MINUTES_PER_YEAR)
    df_1m["vol_ann"] = raw_ann_vol.clip(lower=0.30, upper=2.50)
    
    df_1m["open_sec"] = df_1m["open_time"] // 1000
    df_1m["window_ts"] = (df_1m["open_sec"] // 300) * 300
    df_1m["minute_idx"] = (df_1m["open_sec"] - df_1m["window_ts"]) // 60
    
    windows = df_1m.groupby("window_ts")
    results = []
    
    logger.info(f"Simulating 5m windows for {asset}...")
    for window_ts, group in windows:
        if len(group) != 5:
            continue
            
        group = group.sort_values("minute_idx").reset_index(drop=True)
        
        strike_k = group.loc[0, "open"]
        close_price = group.loc[4, "close"]
        realized_up = 1 if close_price >= strike_k else 0
        mid_spot = group.loc[2, "close"]
        
        vol_ann = group.loc[2, "vol_ann"]
        if pd.isna(vol_ann) or vol_ann <= 0:
            continue
            
        # Spot price at minute 2 close: exactly 180s elapsed into the 300s window -> tau_seconds = 120.0
        # Dollar change over the last 180s (window open to minute 2 close)
        mom_dollar = mid_spot - group.loc[0, "open"]  # 180s lookback, matches live
        # Production normalization from src/bot.py: (180s momentum / spot) / 0.006
        norm_momentum = max(min((mom_dollar / mid_spot) / 0.006, 1.0), -1.0)  # relative return, as in src/bot.py
        
        p_model, z = strategy.calculate_fair_probability(
            S_t=mid_spot,
            K=strike_k,
            tau_seconds=120.0,
            annualized_vol=float(vol_ann),
            ofi_normalized=0.0,
            momentum_normalized=float(norm_momentum),
            cbi_normalized=0.0,
            known_avg_price=None,
            twap_window_sec=60.0
        )
        
        moneyness = mid_spot / strike_k
        
        results.append({
            "window_id": int(window_ts // 300),
            "timestamp": int(window_ts),
            "strike": round(float(strike_k), 4),
            "mid_spot": round(float(mid_spot), 4),
            "close": round(float(close_price), 4),
            "vol_ann": round(float(vol_ann), 4),
            "moneyness": round(float(moneyness), 6),
            "z": round(float(z), 4),
            "p_model": round(float(p_model), 4),
            "realized_up": int(realized_up)
        })

    res_df = pd.DataFrame(results)
    out_path = f"data/backtest_log_{asset.lower()}.csv"
    res_df.to_csv(out_path, index=False)
    logger.info(f"Saved {len(res_df)} 5m windows to {out_path}")
    
    brier_model = ((res_df["p_model"] - res_df["realized_up"]) ** 2).mean()
    brier_50 = ((0.50 - res_df["realized_up"]) ** 2).mean()
    up_freq = res_df["realized_up"].mean()
    
    res_df["prob_bucket"] = pd.cut(res_df["p_model"], bins=np.linspace(0, 1, 11))
    bucket_summary = res_df.groupby("prob_bucket", observed=False).agg(
        n=("realized_up", "count"),
        pred_p=("p_model", "mean"),
        actual_up=("realized_up", "mean")
    )
    
    print(f"\n{'='*50}\nBACKTEST RESULTS: {asset} ({len(res_df)} windows / {days} days)")
    print(f"Outcome Realized UP rate: {up_freq*100:.2f}%")
    print(f"Model Brier Score:       {brier_model:.4f}")
    print(f"50/50 Baseline Brier:    {brier_50:.4f}")
    print(f"Brier Improvement vs 50: {(brier_50 - brier_model) / brier_50 * 100:+.2f}%")
    print("\nCalibration by Decile:")
    print(bucket_summary)
    print(f"{'='*50}\n")
    
    return res_df

def main():
    parser = argparse.ArgumentParser(description="Polymarket 5m Bot Offline Historical Backtest")
    parser.add_argument("--days", type=int, default=60, help="Days of historical data to backtest")
    parser.add_argument("--asset", type=str, default="ALL", choices=["ALL", "BTC", "ETH", "SOL"], help="Asset to backtest")
    args = parser.parse_args()

    assets = ["BTC", "ETH", "SOL"] if args.asset == "ALL" else [args.asset]
    
    for asset in assets:
        run_backtest_for_asset(asset=asset, days=args.days)

if __name__ == "__main__":
    main()
