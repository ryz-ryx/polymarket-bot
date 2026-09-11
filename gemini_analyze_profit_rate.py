import json
import csv
import math
import random
import os
from collections import defaultdict

DATA_DIR = "data"
ASSETS = ["BTC", "ETH", "SOL"]
SUFFIX = {"BTC": "", "ETH": "_eth", "SOL": "_sol"}

print("=" * 80)
print("GEMINI INDEPENDENT ANALYSIS: PROFIT-RATE, MONTE CARLO & CALIBRATION BACKTEST")
print("=" * 80)

# -------------------------------------------------------------
# 1. RISK STATE (Full Day Ground Truth)
# -------------------------------------------------------------
print("\n--- 1. OFFICIAL RISK STATE (Full Day Tracking) ---")
total_official_pnl = 0.0
for asset in ASSETS:
    path = os.path.join(DATA_DIR, f"risk_state{SUFFIX[asset]}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        pnl = data.get("daily_pnl", 0.0)
        tripped = data.get("circuit_breaker_triggered", False)
        total_official_pnl += pnl
        print(f"  {asset}: PnL = ${pnl:+.2f} USD | Circuit Breaker: {'TRIPPED' if tripped else 'ACTIVE'}")
print(f"  TOTAL PORTFOLIO PnL (risk_state): ${total_official_pnl:+.2f} USD")

# -------------------------------------------------------------
# 2. REALIZED TRADE ANALYSIS (fills_log*.jsonl)
# -------------------------------------------------------------
print("\n--- 2. GRANULAR REALIZED TRADE BREAKDOWN (fills_log*.jsonl) ---")
pooled_trades = []
asset_trades = {}

for asset in ASSETS:
    path = os.path.join(DATA_DIR, f"fills_log{SUFFIX[asset]}.jsonl")
    trades = []
    corrections = []
    buys = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                ev = json.loads(line)
                ev_type = ev.get("type")
                if ev_type in ("WIN", "LOSS"):
                    trades.append(ev)
                elif ev_type == "CORRECTION":
                    corrections.append(ev)
                elif ev_type == "BUY":
                    buys.append(ev)

    asset_trades[asset] = trades
    for t in trades:
        pooled_trades.append({**t, "asset": asset})

    n = len(trades)
    wins = [t for t in trades if t["type"] == "WIN"]
    losses = [t for t in trades if t["type"] == "LOSS"]
    total_staked = sum(t["cost"] for t in trades)
    pnl_settled = sum(t["net_pnl"] for t in trades)
    corr_delta = sum(c.get("delta", 0.0) for c in corrections)
    net_pnl = pnl_settled + corr_delta
    win_rate = (len(wins) / n) if n > 0 else 0.0
    profit_rate = (net_pnl / total_staked) if total_staked > 0 else 0.0

    gross_profit = sum(t["net_pnl"] for t in wins)
    gross_loss = abs(sum(t["net_pnl"] for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

    trade_rois = [t["net_pnl"] / t["cost"] for t in trades if t["cost"] > 0]
    mean_roi = sum(trade_rois) / len(trade_rois) if trade_rois else 0.0
    if len(trade_rois) > 1:
        var_roi = sum((r - mean_roi) ** 2 for r in trade_rois) / (len(trade_rois) - 1)
        std_roi = math.sqrt(var_roi)
    else:
        std_roi = 0.0
    sharpe_trade = (mean_roi / std_roi) if std_roi > 0 else 0.0

    print(f"[{asset}]")
    print(f"  Settled Trades: {n} (Wins: {len(wins)}, Losses: {len(losses)}) -> Win Rate: {win_rate*100:.2f}%")
    print(f"  Capital Staked: ${total_staked:.2f} | Net Realized PnL: ${net_pnl:+.2f}")
    print(f"  PROFIT RATE (Return on Capital Staked): {profit_rate*100:+.2f}%")
    print(f"  Profit Factor: {profit_factor:.2f} | Avg PnL / Trade: ${net_pnl/n if n else 0:+.2f}")
    print(f"  Avg Return / Trade: {mean_roi*100:+.2f}% ± {std_roi*100:.2f}% (Sharpe-like: {sharpe_trade:.3f})")
    print(f"  Corrections: {len(corrections)} (net delta: ${corr_delta:+.2f}) | Buys Logged: {len(buys)}")

# Combined
n_total = len(pooled_trades)
wins_total = sum(1 for t in pooled_trades if t["type"] == "WIN")
staked_total = sum(t["cost"] for t in pooled_trades)
pnl_total = sum(t["net_pnl"] for t in pooled_trades)
wr_total = wins_total / n_total if n_total else 0.0
pr_total = pnl_total / staked_total if staked_total else 0.0

print(f"\n[PORTFOLIO COMBINED (Tracked Trades)]")
print(f"  Total Trades: {n_total} (Wins: {wins_total}, Losses: {n_total - wins_total}) -> Win Rate: {wr_total*100:.2f}%")
print(f"  Total Staked: ${staked_total:.2f} | Net PnL: ${pnl_total:+.2f}")
print(f"  PORTFOLIO PROFIT RATE: {pr_total*100:+.2f}%")

# -------------------------------------------------------------
# 3. MONTE CARLO SIMULATION (Bootstrap Analysis)
# -------------------------------------------------------------
print("\n" + "=" * 80)
print("3. MONTE CARLO BOOTSTRAP RESAMPLING (10,000 Iterations)")
print("=" * 80)

def run_mc(trade_list, n_steps, n_sims=10000, seed=42):
    random.seed(seed)
    pnls = [t["net_pnl"] for t in trade_list]
    finals = []
    max_dds = []
    for _ in range(n_sims):
        sampled = random.choices(pnls, k=n_steps)
        cum = 0.0
        peak = 0.0
        mdd = 0.0
        for p in sampled:
            cum += p
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > mdd:
                mdd = dd
        finals.append(cum)
        max_dds.append(mdd)
    finals.sort()
    max_dds.sort()
    return finals, max_dds

def get_percentile(arr, p):
    k = int(round(p * (len(arr) - 1)))
    return arr[max(0, min(len(arr) - 1, k))]

for asset in ASSETS:
    trades = asset_trades[asset]
    if not trades:
        continue
    finals, mdds = run_mc(trades, len(trades), 10000, seed=42)
    p_neg = sum(1 for x in finals if x < 0) / len(finals)
    p_breaker = sum(1 for x in finals if x <= -50.0) / len(finals)

    print(f"\n[{asset}] (N={len(trades)} trades resampled):")
    print(f"  Median PnL: ${get_percentile(finals, 0.50):+.2f}")
    print(f"  5th %ile: ${get_percentile(finals, 0.05):+.2f} | 95th %ile: ${get_percentile(finals, 0.95):+.2f}")
    print(f"  P(Loss < $0): {p_neg*100:.1f}% | P(Tripping $50 Breaker): {p_breaker*100:.1f}%")
    print(f"  Median Max Drawdown: ${get_percentile(mdds, 0.50):.2f} | 95th %ile MDD: ${get_percentile(mdds, 0.95):.2f}")

# Portfolio MC
finals_pf, mdds_pf = run_mc(pooled_trades, len(pooled_trades), 10000, seed=42)
p_neg_pf = sum(1 for x in finals_pf if x < 0) / len(finals_pf)
p_breaker_pf = sum(1 for x in finals_pf if x <= -100.0) / len(finals_pf)
print(f"\n[PORTFOLIO COMBINED] (N={len(pooled_trades)} trades resampled):")
print(f"  Median PnL: ${get_percentile(finals_pf, 0.50):+.2f}")
print(f"  5th %ile: ${get_percentile(finals_pf, 0.05):+.2f} | 95th %ile: ${get_percentile(finals_pf, 0.95):+.2f}")
print(f"  P(Loss < $0): {p_neg_pf*100:.1f}% | P(Tripping $100 Breaker): {p_breaker_pf*100:.1f}%")
print(f"  Median Max Drawdown: ${get_percentile(mdds_pf, 0.50):.2f} | 95th %ile MDD: ${get_percentile(mdds_pf, 0.95):.2f}")

# -------------------------------------------------------------
# 4. CALIBRATION BACKTEST & BRIER SCORE
# -------------------------------------------------------------
print("\n" + "=" * 80)
print("4. CALIBRATION BACKTEST & BRIER SCORES (calibration_log*.csv)")
print("=" * 80)

for asset in ASSETS:
    c_path = os.path.join(DATA_DIR, f"calibration_log{SUFFIX[asset]}.csv")
    if not os.path.exists(c_path):
        continue
    by_window = defaultdict(list)
    with open(c_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("realized_up") in ("0", "1"):
                by_window[row["window_id"]].append(row)

    model_preds = []
    market_preds = []
    for wid, rows in by_window.items():
        mid = min(rows, key=lambda r: abs(float(r["tau_sec"]) - 150.0))
        y = int(mid["realized_up"])
        model_preds.append((float(mid["p_model"]), y))
        try:
            market_preds.append((float(mid["p_market"]), y))
        except (ValueError, KeyError):
            pass

    brier_model = sum((p - y) ** 2 for p, y in model_preds) / len(model_preds) if model_preds else 0.0
    brier_market = sum((p - y) ** 2 for p, y in market_preds) / len(market_preds) if market_preds else 0.0

    print(f"\n[{asset}] (Windows evaluated: {len(by_window)})")
    print(f"  Model Brier Score  : {brier_model:.4f}")
    print(f"  Market Brier Score : {brier_market:.4f}")
    diff = brier_market - brier_model
    if diff > 0.005:
        verdict = f"MODEL OUTPERFORMS MARKET (+{diff:.4f} edge)"
    elif diff < -0.005:
        verdict = f"MARKET OUTPERFORMS MODEL ({diff:.4f} lag)"
    else:
        verdict = f"NEUTRAL / COMPARABLE ({diff:+.4f})"
    print(f"  Result: {verdict}")

print("\n" + "=" * 80)
print("GEMINI INDEPENDENT RUN COMPLETE")
print("=" * 80)
