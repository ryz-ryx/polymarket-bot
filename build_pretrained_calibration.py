"""
Fits an isotonic regression correction per asset from the 365-day offline
backtest logs (data/backtest_log_{asset}.csv), and saves it to
data/pretrained_calibration_{asset}.pkl for EmpiricalCalibrator to use as a
warm-start prior before enough live windows exist to fit its own curve.

This corrects the raw model's p_model against realized_up using ~105k
historical windows per asset -- same idea as the live isotonic stage
(300+ distinct windows), just computed offline instead of waited-for.

Usage: python build_pretrained_calibration.py
"""
import pickle
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

ASSETS = ["btc", "eth", "sol"]


def fit_and_save(asset: str):
    path = f"data/backtest_log_{asset}.csv"
    df = pd.read_csv(path)
    X = df["p_model"].to_numpy()
    y = df["realized_up"].to_numpy()

    iso = IsotonicRegression(y_min=0.02, y_max=0.98, out_of_bounds="clip")
    iso.fit(X, y)

    out_path = f"data/pretrained_calibration_{asset}.pkl"
    with open(out_path, "wb") as f:
        pickle.dump(iso, f)

    # Report before/after Brier so the correction's actual effect is visible,
    # not just "a model got saved".
    raw_brier = float(np.mean((X - y) ** 2))
    corrected = iso.predict(X)
    corrected_brier = float(np.mean((corrected - y) ** 2))

    print(f"{asset.upper()}: raw Brier={raw_brier:.4f}  "
          f"isotonic-corrected Brier={corrected_brier:.4f}  "
          f"(delta={raw_brier - corrected_brier:+.4f})  -> saved {out_path}")


if __name__ == "__main__":
    for asset in ASSETS:
        fit_and_save(asset)
