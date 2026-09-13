"""
Runs on Railway (the only network in this setup that can reach Polymarket's
APIs -- this dev sandbox's direct connection to gamma-api.polymarket.com is
refused). Walks backwards from the current 5-min window, pulling REAL closed
market data per window:
  - Gamma API: resolution (actual Up/Down outcome Polymarket paid out on),
    clobTokenIds, close time.
  - CLOB API: the real yes-token price at approximately tau=120s remaining
    (matches backtest.py's own tau=120s sampling point), via prices-history.

Stops after `--max-windows` or after `--max-missing` consecutive slugs that
don't resolve to a closed market (walked past the point where the 5-min
BTC Up/Down product existed).

Output: JSON lines to stdout, one per window, so the caller (over SSH) can
redirect straight to a local file.
"""
import argparse
import json
import sys
import time
import urllib.request
import urllib.error

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def http_get_json(url: str, timeout: float = 10.0):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def get_market_for_window(asset: str, window_ts: int):
    slug = f"{asset.lower()}-updown-5m-{window_ts}"
    url = f"{GAMMA_API}/events?slug={slug}"
    try:
        events = http_get_json(url)
    except Exception as e:
        return None, f"gamma_error: {e}"
    if not events or not isinstance(events, list):
        return None, "not_found"
    ev = events[0]
    if not ev.get("closed"):
        return None, "not_closed"
    markets = ev.get("markets") or []
    if not markets:
        return None, "no_markets"
    m = markets[0]
    return {
        "slug": slug,
        "window_ts": window_ts,
        "closed": True,
        "outcome_prices": m.get("outcomePrices"),
        "clob_token_ids": m.get("clobTokenIds"),
        "volume": ev.get("volume"),
    }, None


def get_price_near_tau120(clob_token_ids, window_ts: int):
    """Real yes-token price ~120s into the window (2 min elapsed, matches
    backtest.py's tau_seconds=120.0 sampling point) via CLOB prices-history."""
    if not clob_token_ids:
        return None
    try:
        ids = json.loads(clob_token_ids) if isinstance(clob_token_ids, str) else clob_token_ids
    except Exception:
        return None
    if not ids:
        return None
    yes_token_id = ids[0]
    target_ts = window_ts + 120
    url = f"{CLOB_API}/prices-history?market={yes_token_id}&startTs={window_ts}&endTs={window_ts+300}&fidelity=1"
    try:
        data = http_get_json(url)
    except Exception:
        return None
    history = data.get("history") if isinstance(data, dict) else None
    if not history:
        return None
    best = min(history, key=lambda pt: abs(pt.get("t", 0) - target_ts))
    return best.get("p")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", default="BTC")
    ap.add_argument("--max-windows", type=int, default=2000)
    ap.add_argument("--max-missing", type=int, default=20)
    ap.add_argument("--sleep", type=float, default=0.15)
    ap.add_argument("--start-window-ts", type=int, default=None,
                     help="Resume walking backward from just before this window (e.g. the oldest window already fetched), instead of from now.")
    args = ap.parse_args()

    if args.start_window_ts is not None:
        window_ts = args.start_window_ts - 300
    else:
        now_ts = int(time.time())
        window_ts = (now_ts // 300) * 300 - 300  # last fully-closed window

    fetched = 0
    consecutive_missing = 0

    while fetched < args.max_windows and consecutive_missing < args.max_missing:
        market, err = get_market_for_window(args.asset, window_ts)
        if market is None:
            consecutive_missing += 1
            window_ts -= 300
            time.sleep(args.sleep)
            continue

        consecutive_missing = 0
        yes_price_t120 = get_price_near_tau120(market["clob_token_ids"], window_ts)
        market["yes_price_tau120"] = yes_price_t120
        print(json.dumps(market), flush=True)

        fetched += 1
        window_ts -= 300
        time.sleep(args.sleep)

    print(json.dumps({"_meta": True, "fetched": fetched, "stopped_reason": "max_missing" if consecutive_missing >= args.max_missing else "max_windows"}), file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
