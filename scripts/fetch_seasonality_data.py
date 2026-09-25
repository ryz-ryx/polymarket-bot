"""Download Binance 1s klines for tests S (docs/PREREG_seasonality.md) and W (docs/PREREG_vol_timing.md).

Usage: python scripts/fetch_seasonality_data.py
Writes data/seasonality/{SYMBOL}_1s.npy, columns [open_time_ms, close]. Idempotent: skips symbols already cached.
"""
import json
import time
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "seasonality"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
URL = "https://data-api.binance.vision/api/v3/klines?symbol={s}&interval=1s&limit=1000&startTime={t}"
DAYS = 30


def fetch(symbol):
    path = CACHE / f"{symbol}_1s.npy"
    if path.exists():
        print(f"{symbol}: cached, {len(np.load(path))} rows")
        return
    now = int(time.time() * 1000)
    t = now - DAYS * 86_400_000
    rows = []
    while t < now:
        with urllib.request.urlopen(URL.format(s=symbol, t=t), timeout=30) as r:
            batch = json.loads(r.read())
        if not batch:
            break
        rows += [[int(b[0]), float(b[4])] for b in batch if int(b[6]) < now]
        t = batch[-1][0] + 1_000
    arr = np.array(rows)
    CACHE.mkdir(parents=True, exist_ok=True)
    np.save(path, arr)
    print(f"{symbol}: fetched {len(arr)} rows")


def main():
    for s in SYMBOLS:
        fetch(s)


if __name__ == "__main__":
    main()
