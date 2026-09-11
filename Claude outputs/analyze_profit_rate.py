#!/usr/bin/env python3
"""
Independent profit-rate analysis for Polymarket5mBot.
Run separately from Gemini's own analysis -- results are meant to be
cross-checked against each other, not shared methodology beforehand.

Inputs (read-only, from the bot's data/ directory):
  data/fills_log*.jsonl        -- real executed paper-trading fills (BUY/WIN/LOSS/CORRECTION)
  data/calibration_log*.csv    -- per-tick model probability vs realized outcome

Outputs:
  1. Empirical profit-rate (real trade history, no simulation)
  2. Monte Carlo bootstrap of the empirical per-trade PnL distribution
  3. Backtest-style calibration/edge validity check (model vs market Brier score)
"""
import json
import csv
import math
import random
from collections import defaultdict

DATA_DIR = "/mnt/user-data/uploads/bot/data"
ASSETS = ["BTC", "ETH", "SOL"]
SUFFIX = {"BTC": "", "ETH": "_eth", "SOL": "_sol"}

random.seed(42)  # reproducible Monte Carlo run; note the seed so Gemini's run can differ honestly


def load_fills(asset):
    path = f"{DATA_DIR}/fills_log{SUFFIX[asset]}.jsonl"
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    events.sort(key=lambda e: e["ts"])
    return events


def settled_trades(events):
    """Return list of dicts: {window_id, cost, net_pnl, type} for WIN/LOSS events only."""
    trades = []
    for e in events:
        if e["type"] in ("WIN", "LOSS"):
            trades.append({
                "window_id": e["window_id"],
                "cost": e["cost"],
                "net_pnl": e["net_pnl"],
                "type": e["type"],
            })
    return trades


def corrections(events):
    return [e for e in events if e["type"] == "CORRECTION"]


# ============================================================
# 1. EMPIRICAL PROFIT-RATE (real trade history)
# ============================================================
print("=" * 70)
print("1. EMPIRICAL PROFIT-RATE -- real executed paper trades")
print("=" * 70)

all_trades_pooled = []
per_asset_summary = {}

for asset in ASSETS:
    events = load_fills(asset)
    trades = settled_trades(events)
    corr = corrections(events)
    all_trades_pooled.extend(trades)

    n = len(trades)
    wins = [t for t in trades if t["type"] == "WIN"]
    losses = [t for t in trades if t["type"] == "LOSS"]
    total_cost = sum(t["cost"] for t in trades)
    total_pnl = sum(t["net_pnl"] for t in trades) + sum(c["delta"] for c in corr)
    win_rate = len(wins) / n if n else 0.0
    gross_win = sum(t["net_pnl"] for t in wins)
    gross_loss = -sum(t["net_pnl"] for t in losses)  # positive number
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    roi_on_capital_risked = (total_pnl / total_cost) if total_cost else 0.0
    avg_pnl_per_trade = total_pnl / n if n else 0.0

    returns = [t["net_pnl"] / t["cost"] for t in trades if t["cost"] > 0]
    mean_ret = sum(returns) / len(returns) if returns else 0.0
    if len(returns) > 1:
        var_ret = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
        std_ret = math.sqrt(var_ret)
    else:
        std_ret = 0.0
    sharpe_like = (mean_ret / std_ret) if std_ret > 0 else 0.0

    per_asset_summary[asset] = dict(
        n=n, wins=len(wins), losses=len(losses), win_rate=win_rate,
        total_cost=total_cost, total_pnl=total_pnl, roi=roi_on_capital_risked,
        profit_factor=profit_factor, avg_pnl_per_trade=avg_pnl_per_trade,
        mean_return_per_trade=mean_ret, std_return_per_trade=std_ret,
        sharpe_like=sharpe_like, corrections=len(corr),
    )

    print(f"\n[{asset}]")
    print(f"  Settled trades: {n}  (W {len(wins)} / L {len(losses)})  win rate: {win_rate*100:.1f}%")
    print(f"  Capital risked (sum of stakes): ${total_cost:.2f}")
    print(f"  Net PnL (incl. {len(corr)} correction(s)): ${total_pnl:+.2f}")
    print(f"  PROFIT RATE (net PnL / capital risked): {roi_on_capital_risked*100:+.2f}%")
    print(f"  Profit factor (gross win / gross loss): {profit_factor:.2f}")
    print(f"  Avg PnL per trade: ${avg_pnl_per_trade:+.2f}   Avg return per trade: {mean_ret*100:+.2f}%")
    print(f"  Per-trade return std dev: {std_ret*100:.2f}%   Sharpe-like (mean/std): {sharpe_like:.3f}")

# Combined portfolio
n_all = len(all_trades_pooled)
total_cost_all = sum(t["cost"] for t in all_trades_pooled)
total_pnl_all = sum(t["net_pnl"] for t in all_trades_pooled)
wins_all = sum(1 for t in all_trades_pooled if t["type"] == "WIN")
win_rate_all = wins_all / n_all if n_all else 0.0
roi_all = total_pnl_all / total_cost_all if total_cost_all else 0.0

print(f"\n[COMBINED PORTFOLIO -- BTC+ETH+SOL]")
print(f"  Settled trades: {n_all}   Win rate: {win_rate_all*100:.1f}%")
print(f"  Capital risked: ${total_cost_all:.2f}   Net PnL: ${total_pnl_all:+.2f}")
print(f"  PROFIT RATE: {roi_all*100:+.2f}%")

# ------------------------------------------------------------
# Reconciliation against risk_state*.json (RiskManager's own persisted
# daily_pnl, which accumulates across restarts independent of fills_log).
# Flags any gap -- e.g. if fills_log doesn't cover the FULL calendar day
# because file-based logging was added partway through today's trading.
# ------------------------------------------------------------
print(f"\n[RECONCILIATION -- fills_log-derived PnL vs risk_state.json daily_pnl]")
for asset in ASSETS:
    rs_path = f"{DATA_DIR}/risk_state{SUFFIX[asset]}.json"
    with open(rs_path) as f:
        rs = json.load(f)
    official_pnl = rs.get("daily_pnl", 0.0)
    fills_pnl = per_asset_summary[asset]["total_pnl"]
    gap = official_pnl - fills_pnl
    flag = "  <-- GAP: fills_log is missing trades from before it existed" if abs(gap) > 1.0 else ""
    print(f"  [{asset}] risk_state.daily_pnl: ${official_pnl:+.2f}   fills_log net_pnl: ${fills_pnl:+.2f}   gap: ${gap:+.2f}{flag}")

# ============================================================
# 2. MONTE CARLO -- bootstrap resample of empirical per-trade PnL
# ============================================================
print("\n" + "=" * 70)
print("2. MONTE CARLO SIMULATION (bootstrap resample, seed=42, 10,000 paths)")
print("=" * 70)
print("Method: resample WITH REPLACEMENT from the pool of actually-realized")
print("(stake, net_pnl) trade outcomes -- preserves the real joint distribution")
print("of bet size and win/loss, rather than assuming a synthetic edge.")

N_SIMS = 10000


def monte_carlo(trade_pool, path_length, n_sims=N_SIMS):
    pnls = [t["net_pnl"] for t in trade_pool]
    results = []
    for _ in range(n_sims):
        path = random.choices(pnls, k=path_length)
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in path:
            cum += p
            peak = max(peak, cum)
            max_dd = max(max_dd, peak - cum)
        results.append((cum, max_dd))
    totals = sorted(r[0] for r in results)
    drawdowns = sorted(r[1] for r in results)
    return totals, drawdowns


def pct(sorted_list, p):
    idx = min(len(sorted_list) - 1, max(0, int(round(p * (len(sorted_list) - 1)))))
    return sorted_list[idx]


for asset in ASSETS:
    trades = settled_trades(load_fills(asset))
    if len(trades) < 5:
        print(f"\n[{asset}] too few settled trades ({len(trades)}) for a meaningful bootstrap -- skipped.")
        continue
    L = len(trades)  # simulate a path as long as the actual observed history
    totals, drawdowns = monte_carlo(trades, L)
    prob_negative = sum(1 for t in totals if t < 0) / len(totals)
    prob_breaker = sum(1 for t in totals if t <= -50.0) / len(totals)  # per-asset circuit breaker level
    print(f"\n[{asset}] simulated {L}-trade paths (matching historical sample size):")
    print(f"  Median total PnL: ${pct(totals,0.50):+.2f}   "
          f"5th pct: ${pct(totals,0.05):+.2f}   95th pct: ${pct(totals,0.95):+.2f}")
    print(f"  P(path ends net negative): {prob_negative*100:.1f}%")
    print(f"  P(path hits -$50 loss, i.e. would trip the per-asset circuit breaker): {prob_breaker*100:.1f}%")
    print(f"  Median max drawdown within path: ${pct(drawdowns,0.50):.2f}   95th pct max drawdown: ${pct(drawdowns,0.95):.2f}")

# Combined portfolio Monte Carlo (pool all 3 assets' trades, simulate a path per asset jointly)
print(f"\n[COMBINED PORTFOLIO] simulated {n_all}-trade joint paths:")
totals_all, drawdowns_all = monte_carlo(all_trades_pooled, n_all)
prob_negative_all = sum(1 for t in totals_all if t < 0) / len(totals_all)
prob_portfolio_breaker = sum(1 for t in totals_all if t <= -100.0) / len(totals_all)
print(f"  Median total PnL: ${pct(totals_all,0.50):+.2f}   "
      f"5th pct: ${pct(totals_all,0.05):+.2f}   95th pct: ${pct(totals_all,0.95):+.2f}")
print(f"  P(portfolio path ends net negative): {prob_negative_all*100:.1f}%")
print(f"  P(portfolio path hits -$100, i.e. would trip the portfolio circuit breaker): {prob_portfolio_breaker*100:.1f}%")

# ============================================================
# 3. BACKTEST-STYLE VALIDITY CHECK -- is the model's edge real?
# ============================================================
print("\n" + "=" * 70)
print("3. MODEL CALIBRATION / EDGE VALIDITY (backtest against calibration_log.csv)")
print("=" * 70)
print("Note: no persisted historical order-book means a full re-execution")
print("backtest (replaying exact fills) isn't possible from logged data alone.")
print("This instead tests whether the strategy's own probability estimates")
print("(p_model) are BETTER CALIBRATED than the market's own implied")
print("probability (p_market) against what actually happened -- i.e. does")
print("the model have real statistical edge, independent of execution/fees.")


def brier_and_calibration(rows, key):
    """rows: list of (p, realized_up). Returns Brier score + 10-bucket calibration table."""
    n = len(rows)
    if n == 0:
        return None, []
    brier = sum((p - y) ** 2 for p, y in rows) / n
    buckets = defaultdict(list)
    for p, y in rows:
        b = min(9, int(p * 10))
        buckets[b].append(y)
    table = []
    for b in sorted(buckets):
        ys = buckets[b]
        table.append((f"{b/10:.1f}-{(b+1)/10:.1f}", len(ys), sum(ys) / len(ys)))
    return brier, table


for asset in ASSETS:
    path = f"{DATA_DIR}/calibration_log{SUFFIX[asset]}.csv"
    window_groups = defaultdict(list)
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            r_up = row.get("realized_up")
            if r_up in ("0", "1"):
                window_groups[row["window_id"]].append(row)

    model_rows, market_rows = [], []
    for wid, rows in window_groups.items():
        best = min(rows, key=lambda r: abs(float(r["tau_sec"]) - 150.0))
        y = int(best["realized_up"])
        model_rows.append((float(best["p_model"]), y))
        try:
            market_rows.append((float(best["p_market"]), y))
        except (ValueError, KeyError):
            pass

    n_windows = len(window_groups)
    model_brier, model_table = brier_and_calibration(model_rows, "p_model")
    market_brier, _ = brier_and_calibration(market_rows, "p_market") if market_rows else (None, [])

    print(f"\n[{asset}] {n_windows} distinct settled windows with a labeled outcome")
    if model_brier is not None:
        print(f"  Model Brier score  (p_model vs realized):  {model_brier:.4f}  (lower is better; 0.25 = coin-flip baseline)")
    if market_brier is not None:
        print(f"  Market Brier score (p_market vs realized): {market_brier:.4f}")
        if model_brier is not None:
            edge = market_brier - model_brier
            verdict = "MODEL BEATS MARKET" if edge > 0.005 else ("ROUGHLY EQUAL" if abs(edge) <= 0.005 else "MARKET BEATS MODEL")
            print(f"  Model vs market edge: {edge:+.4f}  -> {verdict}")
    print(f"  Calibration table (p_model bucket -> n obs -> actual UP freq):")
    for bucket, n_obs, actual_freq in model_table:
        print(f"    {bucket}  n={n_obs:4d}  actual_up_freq={actual_freq:.3f}")

print("\n" + "=" * 70)
print("END OF INDEPENDENT ANALYSIS")
print("=" * 70)
