"""
Runs on Railway (only network that reaches Polymarket's APIs from this setup).
For each real historical BTC 5m window already in data/real_market_history_btc*.jsonl,
pulls the real trade-by-trade fills (data-api.polymarket.com/trades?market=<conditionId>)
and writes out a small PER-WALLET AGGREGATE per window (not the raw trade list) so we
can identify likely-bot wallets and test whether their positioning correlates with the
real outcome -- NOT to copy/mirror their trades, just as an optional extra signal
alongside OFI/momentum/CBI.

v2: the first version stored the full raw trade list per window and filled the 434MB
Railway volume after ~1100 windows. This version aggregates each window down to one
compact record per wallet (side totals + timing), which is orders of magnitude smaller,
and adds a disk-space guard that stops writing before the volume fills again.

Output: JSON lines to stdout -- one line per window:
  {"slug", "window_ts", "condition_id", "wallets": [
      {"wallet", "yes_size", "no_size", "trade_count", "first_ts", "last_ts"}, ...
  ]}
"""
import argparse
import json
import shutil
import sys
import time
import urllib.request

GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
HEADERS = {"User-Agent": "Mozilla/5.0"}
MIN_FREE_BYTES = 50 * 1024 * 1024  # abort before writing if less than 50MB free


def http_get_json(url: str, timeout: float = 10.0):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def get_condition_id(slug: str):
    url = f"{GAMMA_API}/events?slug={slug}"
    try:
        events = http_get_json(url)
    except Exception:
        return None
    if not events or not isinstance(events, list):
        return None
    markets = events[0].get("markets") or []
    if not markets:
        return None
    return markets[0].get("conditionId")


def get_trades(condition_id: str, limit: int = 500):
    url = f"{DATA_API}/trades?market={condition_id}&limit={limit}"
    try:
        return http_get_json(url)
    except Exception:
        return []


def aggregate_trades(trades):
    """Collapse a raw trade list down to one record per wallet."""
    by_wallet = {}
    for t in trades:
        wallet = t.get("proxyWallet")
        if not wallet:
            continue
        side = (t.get("outcome") or "").upper()
        size = float(t.get("size") or 0.0)
        ts = t.get("timestamp")
        rec = by_wallet.setdefault(wallet, {
            "wallet": wallet, "yes_size": 0.0, "no_size": 0.0,
            "trade_count": 0, "first_ts": ts, "last_ts": ts,
        })
        if side == "YES":
            rec["yes_size"] += size
        elif side == "NO":
            rec["no_size"] += size
        rec["trade_count"] += 1
        if ts is not None:
            if rec["first_ts"] is None or ts < rec["first_ts"]:
                rec["first_ts"] = ts
            if rec["last_ts"] is None or ts > rec["last_ts"]:
                rec["last_ts"] = ts
    return list(by_wallet.values())


def free_bytes(path: str) -> int:
    return shutil.disk_usage(path).free


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows-file", required=True, help="jsonl file of windows (slug field required)")
    ap.add_argument("--sleep", type=float, default=0.15)
    ap.add_argument("--limit", type=int, default=1000, help="max windows to process this run")
    ap.add_argument("--disk-check-path", default="/app/data", help="path whose free space is monitored")
    args = ap.parse_args()

    slugs = []
    with open(args.windows_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("_meta"):
                continue
            slugs.append((d["slug"], d.get("window_ts")))

    processed = 0
    stopped_reason = "limit_reached"
    for slug, window_ts in slugs:
        if processed >= args.limit:
            break
        if free_bytes(args.disk_check_path) < MIN_FREE_BYTES:
            stopped_reason = "low_disk_space"
            break
        cond = get_condition_id(slug)
        if not cond:
            time.sleep(args.sleep)
            continue
        trades = get_trades(cond)
        wallets = aggregate_trades(trades)
        print(json.dumps({
            "slug": slug, "window_ts": window_ts, "condition_id": cond, "wallets": wallets,
        }), flush=True)
        processed += 1
        time.sleep(args.sleep)
    else:
        stopped_reason = "exhausted_windows"

    print(json.dumps({"_meta": True, "processed": processed, "stopped_reason": stopped_reason}), file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
