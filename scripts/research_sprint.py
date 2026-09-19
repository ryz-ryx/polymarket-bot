"""
Pre-registered offline research sprint. Analysis only: touches no bot code, trades nothing.

PRE-REGISTRATION (committed before this script was run on real data).
Four ideas are tried, so every test uses a Bonferroni-corrected two-sided CI (alpha = 0.05/4,
z = 2.5). Only tests A and B are implemented here; C and D need the tick log to grow.

  A  Shrink-to-market. Blend p = w*p_model + (1-w)*p_market, w in 0..1 step 0.05. Fit w on the
     first 60% of windows (chronological), score on the last 40%. Statistic: mean per-window
     Brier difference, blend minus pure market, with a paired bootstrap CI.
     KILL A (model adds nothing) unless the CI upper bound is below 0.
  B  Price-bucket calibration on real history (yes_price_tau120 vs outcome), no model involved.
     For each 0.1-wide price bucket, take the better of buying YES at p or NO at 1-p and compute
     mean net return per $ after the taker fee 0.07*(1-price), with a normal-approx CI.
     A bucket SURVIVES only if the CI lower bound is above 0 in BOTH chronological halves.
     KILL B if no bucket survives.
  C  Lead-lag on data/tick_log.jsonl: not implemented (needs >= 2000 ticks; log started 2026-09-19).
  D  Final-90s TWAP mispricing and maker fills with a trade-through rule: not implemented.

Anything that survives goes to a separate shadow logger (>= 100 shadow trades) before any real
judgment. Nothing here changes the frozen bot.

Usage: python scripts/research_sprint.py [--calib-file PATH | --url URL] [--days 90] [--history PATH]
"""
import argparse
import json
import math
import os
import random
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.strategies.claud_quant import estimate_taker_fee_fraction  # noqa: E402

DEFAULT_URL = "https://polymarketbot.up.railway.app"
Z = 2.5  # two-sided z at alpha = 0.05 / 4 (Bonferroni over four ideas)
N_BOOT = 5000
W_GRID = [i / 20 for i in range(21)]


def brier(ps, ys):
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ys)


def blend(w, p_model, p_market):
    return [w * a + (1 - w) * b for a, b in zip(p_model, p_market)]


def fit_w(p_model, p_market, ys):
    """w in W_GRID with the lowest Brier on the given (train) slice."""
    return min(W_GRID, key=lambda w: brier(blend(w, p_model, p_market), ys))


def shrink_test(rows, seed=42):
    """rows: dicts with timestamp, p_model, p_market, y, any order."""
    rows = sorted(rows, key=lambda r: r["timestamp"])
    cut = int(len(rows) * 0.6)
    tr, te = rows[:cut], rows[cut:]
    cols = lambda rs: ([r["p_model"] for r in rs], [r["p_market"] for r in rs], [r["y"] for r in rs])
    w = fit_w(*cols(tr))
    pm, pk, ys = cols(te)
    bl = blend(w, pm, pk)
    diffs = [(a - y) ** 2 - (b - y) ** 2 for a, b, y in zip(bl, pk, ys)]
    rng = random.Random(seed)
    n = len(diffs)
    boots = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(N_BOOT))
    lo, hi = boots[int(0.00625 * N_BOOT)], boots[int(0.99375 * N_BOOT)]
    return {"n_train": len(tr), "n_test": n, "w": w, "brier_market": brier(pk, ys),
            "brier_blend": brier(bl, ys), "brier_model": brier(pm, ys),
            "diff_mean": sum(diffs) / n, "ci": (lo, hi), "survives": hi < 0}


def bucket_returns(price, up, fee=estimate_taker_fee_fraction):
    """Net return per $ staked for each side of one window at market YES price `price`."""
    yes_ret = (1.0 / price if up else 0.0) - 1.0 - fee(price)
    no_p = 1.0 - price
    no_ret = (1.0 / no_p if not up else 0.0) - 1.0 - fee(no_p)
    return yes_ret, no_ret


def mean_ci(xs):
    n = len(xs)
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / max(n - 1, 1))
    se = sd / math.sqrt(n)
    return m, m - Z * se, m + Z * se


def bucket_test(windows, min_n=200):
    """windows: [(window_ts, yes_price, realized_up)]. Returns per-bucket dicts, chronological halves."""
    windows = sorted(windows)
    half = len(windows) // 2
    out = []
    for b in range(10):
        lo_p, hi_p = b / 10, (b + 1) / 10
        cell = {"bucket": f"{lo_p:.1f}-{hi_p:.1f}"}
        for name, part in (("h1", windows[:half]), ("h2", windows[half:])):
            xs_yes, xs_no, n_up = [], [], 0
            for _, p, up in part:
                if lo_p <= p < hi_p and 0.02 <= p <= 0.98:
                    y, nn = bucket_returns(p, up)
                    xs_yes.append(y)
                    xs_no.append(nn)
                    n_up += up
            cell[name] = {"n": len(xs_yes)}
            if len(xs_yes) >= min_n:
                res = {s: mean_ci(x) for s, x in (("YES", xs_yes), ("NO", xs_no))}
                side = max(res, key=lambda s: res[s][0])
                cell[name].update({"side": side, "mean": res[side][0], "lo": res[side][1], "hi": res[side][2],
                                   "freq_up": n_up / len(xs_yes)})
        both = all("lo" in cell[h] for h in ("h1", "h2"))
        cell["survives"] = bool(both and cell["h1"]["side"] == cell["h2"]["side"]
                                and cell["h1"]["lo"] > 0 and cell["h2"]["lo"] > 0)
        out.append(cell)
    return out


def load_calibration(url, days, path):
    if path:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        with urllib.request.urlopen(f"{url.rstrip('/')}/api/calibration_raw?days={days}", timeout=60) as r:
            data = json.load(r)
    rows = []
    for w in data.get("windows", []):
        try:
            rows.append({"timestamp": float(w["timestamp"]), "p_model": float(w["p_model"]),
                         "p_market": float(w["p_market"]), "y": int(w["realized_up"])})
        except (KeyError, TypeError, ValueError):
            continue
    return rows, data


def load_history(path):
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("_meta") or d.get("yes_price_tau120") is None:
                continue
            try:
                up = 1 if float(json.loads(d["outcome_prices"])[0]) >= 0.5 else 0
            except Exception:
                continue
            out.append((d["window_ts"], float(d["yes_price_tau120"]), up))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--calib-file", default=None)
    ap.add_argument("--days", type=float, default=90)
    ap.add_argument("--history", default="data/real_market_history_btc_extended.jsonl")
    args = ap.parse_args()

    rows, meta = load_calibration(args.url, args.days, args.calib_file)
    print(f"== A shrink-to-market: {len(rows)} windows (fully covered: {meta.get('window_fully_covered')}) ==")
    if len(rows) < 100:
        print("too few windows for A")
    else:
        a = shrink_test(rows)
        print(f"train {a['n_train']} / test {a['n_test']}   fitted w = {a['w']:.2f}")
        print(f"test Brier: market {a['brier_market']:.4f}  blend {a['brier_blend']:.4f}  model {a['brier_model']:.4f}")
        print(f"blend - market = {a['diff_mean']:+.5f}   corrected CI [{a['ci'][0]:+.5f}, {a['ci'][1]:+.5f}]")
        print("A:", "SURVIVES (model adds information)" if a["survives"] else "KILL (model adds nothing over the market)")

    hist = load_history(args.history)
    print(f"\n== B price-bucket calibration: {len(hist)} windows ==")
    b = bucket_test(hist)
    for c in b:
        h1, h2 = c["h1"], c["h2"]
        f = lambda h: (f"n={h['n']:>5} {h['side']} {h['mean']:+.1%} [{h['lo']:+.1%},{h['hi']:+.1%}]"
                       if "lo" in h else f"n={h['n']:>5} (below min n)")
        print(f"{c['bucket']}  h1 {f(h1)}   h2 {f(h2)}   {'SURVIVES' if c['survives'] else ''}")
    print("B:", "SURVIVES: " + ", ".join(c["bucket"] for c in b if c["survives"])
          if any(c["survives"] for c in b) else "KILL (no bucket beats fees in both halves)")


if __name__ == "__main__":
    main()
