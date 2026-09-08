import os
import pandas as pd
import numpy as np
from sklearn.isotonic import IsotonicRegression
from src.strategies.claud_quant import estimate_taker_fee_fraction

CALIB_LOG = "data/calibration_log.csv"
EVENT_LOG = "data/trade_events.csv"

def analyze_calibration(calib_path=CALIB_LOG, event_path=EVENT_LOG, min_windows=30):
    print("=" * 70)
    print("POLYMARKET 5-MINUTE QUANT QUANTITATIVE AUDIT & FUNNEL REPORT")
    print("=" * 70)

    # -------------------------------------------------------------
    # 1. TRADE FUNNEL & GUARD CONVERSION ANALYSIS
    # -------------------------------------------------------------
    if os.path.exists(event_path):
        edf = pd.read_csv(event_path)
        total_evals = len(edf)
        if total_evals > 0:
            counts = edf["status"].value_counts()
            pcts = (counts / total_evals) * 100.0
            funnel_df = pd.DataFrame({"Count": counts, "Percentage": pcts.round(2)})
            print("\n--- Trade Funnel Conversion & Guard Breakdown ---")
            print(funnel_df.to_string())
            
            # Actionable signal conversion
            actionable = edf[~edf["status"].isin(["NO_SIGNAL", "BLOCKED_NOISE"])]
            if len(actionable) > 0:
                print(f"\nTotal Actionable Signals Generated: {len(actionable)}")
                act_counts = actionable["status"].value_counts()
                for status, cnt in act_counts.items():
                    print(f"  -> {status}: {cnt} ({cnt/len(actionable)*100:.1f}%)")
    else:
        print("\nNo trade_events.csv found yet (funnel logger newly active).")

    # -------------------------------------------------------------
    # 2. INDEPENDENT WINDOW CALIBRATION ANALYSIS (DEDUPLICATED)
    # -------------------------------------------------------------
    if not os.path.exists(calib_path):
        print(f"\nNo calibration log found at {calib_path}")
        return

    df = pd.read_csv(calib_path)
    settled = df[df["realized_up"].isin([0, 1, "0", "1"])].copy()
    settled["realized_up"] = settled["realized_up"].astype(int)

    num_windows = settled["window_id"].nunique()
    total_ticks = len(df)
    settled_ticks = len(settled)

    print(f"\n--- Ground-Truth Calibration ({settled_ticks} ticks across {num_windows} distinct windows) ---")

    if num_windows == 0:
        print("0 settled windows available yet.")
        return

    # Subsample exactly 1 representative observation per distinct window (at midpoint tau ~ 150s)
    # to eliminate oversampling and within-window correlation artifacts
    idx_mid = settled.groupby("window_id")["tau_sec"].apply(lambda s: (s - 150.0).abs().idxmin())
    window_df = settled.loc[idx_mid].copy()

    # Bucket by P_model across INDEPENDENT WINDOWS
    window_df["p_bucket"] = pd.cut(
        window_df["p_model"],
        bins=[0.0, 0.35, 0.45, 0.55, 0.65, 1.0],
        labels=["<0.35 (Strong DOWN)", "0.35-0.45 (Mild DOWN)", "0.45-0.55 (Neutral)", "0.55-0.65 (Mild UP)", ">0.65 (Strong UP)"]
    )

    bucket_stats = window_df.groupby("p_bucket", observed=False).agg(
        windows=("realized_up", "count"),
        mean_p_model=("p_model", "mean"),
        realized_win_rate=("realized_up", "mean")
    )
    bucket_stats["brier_diff"] = bucket_stats["realized_win_rate"] - bucket_stats["mean_p_model"]
    print("\n--- Independent Window Calibration Table (1 Sample per Window) ---")
    print(bucket_stats.to_string())

    # Raw tick frequency for reference
    print(f"\nNote: Tick-level oversampling is {settled_ticks / max(num_windows, 1):.1f}x per window. Deduplicated table above reflects true trial counts.")

    # 3. Gated Isotonic Shrinkage
    if num_windows >= min_windows:
        unique_outcomes = window_df["realized_up"].nunique()
        if unique_outcomes >= 2:
            iso = IsotonicRegression(y_min=0.05, y_max=0.95, out_of_bounds="clip")
            X = window_df["p_model"].values
            y = window_df["realized_up"].values
            iso.fit(X, y)
            test_points = np.array([0.2, 0.35, 0.5, 0.65, 0.8])
            calibrated = iso.predict(test_points)
            print(f"\n--- Isotonic Shrinkage Test ({num_windows} Independent Windows Sampled) ---")
            for raw, cal in zip(test_points, calibrated):
                print(f"Raw P_model: {raw:.2f} -> Calibrated: {cal:.2f} (Shift: {cal - raw:+.2f})")
        else:
            print(f"\nAll {num_windows} windows had identical outcome ({window_df['realized_up'].iloc[0]}). Skipping isotonic fit.")
    else:
        print(f"\nDeferred Isotonic Fit: Need >= {min_windows} distinct windows (currently {num_windows} distinct windows).")

    # -------------------------------------------------------------
    # 4. FEE-ADJUSTED TRADE PROFITABILITY (the actual go-live gate)
    # -------------------------------------------------------------
    # Joins EXECUTED rows in trade_events.csv to their settled outcome in calibration_log.csv
    # and applies Polymarket's REAL crypto_fees_v2 taker fee (rate * (1-price), takerOnly --
    # see estimate_taker_fee_fraction()) rather than reporting gross/fee-free numbers. Paper
    # trading's own P&L now deducts this fee at execution too (src/executor.py), but this
    # section lets you re-derive the same figure independently from the raw logs, and is what
    # "profit factor" should mean when deciding whether to go live with real capital.
    print("\n--- Fee-Adjusted Trade Profitability (EXECUTED trades joined to settlements) ---")
    if not os.path.exists(event_path):
        print("No trade_events.csv found -- cannot reconstruct trade-level PnL.")
        return

    edf_full = pd.read_csv(event_path)
    exec_df = edf_full[edf_full["status"] == "EXECUTED"].copy()
    if exec_df.empty:
        print("No EXECUTED trades logged yet.")
        return

    realized_map = settled.drop_duplicates("window_id", keep="last").set_index("window_id")["realized_up"].to_dict()

    matched = 0
    unmatched = 0
    wins = 0
    losses = 0
    gross_pnl = 0.0
    total_fees = 0.0
    net_pnl = 0.0
    gross_win_net_of_fee = 0.0
    gross_loss_net_of_fee = 0.0

    for _, row in exec_df.iterrows():
        wid = row["window_id"]
        price = row.get("direct_ask")
        size = row.get("size_usd")
        side = row.get("outcome")
        if wid not in realized_map or pd.isna(price) or pd.isna(size) or size <= 0:
            unmatched += 1
            continue
        realized_up = int(realized_map[wid])
        is_win = (side == "YES" and realized_up == 1) or (side == "NO" and realized_up == 0)
        shares = size / max(price, 0.01)
        pnl_gross = (shares * 1.0 - size) if is_win else -size
        fee = size * estimate_taker_fee_fraction(price)
        pnl_net = pnl_gross - fee

        matched += 1
        gross_pnl += pnl_gross
        total_fees += fee
        net_pnl += pnl_net
        if pnl_net > 0:
            wins += 1
            gross_win_net_of_fee += pnl_net
        else:
            losses += 1
            gross_loss_net_of_fee += -pnl_net

    print(f"Matched trades: {matched}  (Unmatched/unresolved: {unmatched})")
    if matched > 0:
        profit_factor = (gross_win_net_of_fee / gross_loss_net_of_fee) if gross_loss_net_of_fee > 0 else float("inf")
        print(f"Wins/Losses (net of fee): {wins}/{losses}  Win rate: {wins/matched*100:.1f}%")
        print(f"Gross PnL (fee-free, misleading): ${gross_pnl:+.2f}")
        print(f"Total real taker fees: ${total_fees:.2f}")
        print(f"Net PnL (fee-adjusted, the number that matters): ${net_pnl:+.2f}")
        print(f"Profit Factor (net-of-fee gross-win / gross-loss): {profit_factor:.3f}")
        gate = "PASS" if (num_windows >= min_windows and profit_factor > 1.20) else "NOT YET"
        print(f"\nGo-live gate (>= {min_windows} settled windows AND net-of-fee profit factor > 1.20): {gate}")

if __name__ == "__main__":
    analyze_calibration()
