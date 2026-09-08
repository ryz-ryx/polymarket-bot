import os
import pandas as pd
import numpy as np
from sklearn.isotonic import IsotonicRegression

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

if __name__ == "__main__":
    analyze_calibration()
