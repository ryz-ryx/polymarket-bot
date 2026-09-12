import os
import sys
import json
import csv
import glob
import time
import hmac
import glob as _glob
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import config
from src.control_state import read_control_state, write_control_state
from src.breaker_reset import request_reset

ASSET_SUFFIX = {"BTC": ""}


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def tail_read_csv_rows(filepath, tail_bytes):
    """Read the last `tail_bytes` of a growing CSV log and parse it against the
    file's own header line, without loading the whole (potentially many-MB,
    ever-growing) file into memory on every request."""
    if not os.path.exists(filepath):
        return []
    with open(filepath, "rb") as f:
        header_line = f.readline().decode("utf-8", errors="replace").strip()
        header_len = f.tell()
        size = os.fstat(f.fileno()).st_size
        read_from_header = size - header_len <= tail_bytes
        if read_from_header:
            f.seek(header_len)
        else:
            f.seek(size - tail_bytes)
        data = f.read()
    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if not read_from_header and len(lines) > 1:
        # Only a genuine partial-tail read can start mid-line -- drop that
        # leading fragment. When we seeked exactly to header_len (the whole
        # remaining file fit in tail_bytes), line 0 is already a complete
        # row and must be kept, or the oldest row in any small/fresh log
        # file is silently lost on every read.
        lines = lines[1:]
    reader = csv.DictReader([header_line] + [l for l in lines if l.strip()])
    return list(reader)


def read_rows_covering_window(filepath, since_seconds, max_bytes_cap=32_000_000):
    """Grow the tail read until every row within the last `since_seconds` is
    captured (or the whole file / a size cap is hit). Returns
    (rows_in_window_oldest_first, fully_covered: bool)."""
    if not os.path.exists(filepath):
        return [], True
    full_size = os.path.getsize(filepath)
    cutoff = time.time() - since_seconds
    tail_bytes = min(1_000_000, full_size) or 1_000_000
    while True:
        rows = tail_read_csv_rows(filepath, tail_bytes)
        oldest_ts = _safe_float(rows[0].get("timestamp")) if rows else None
        covered_whole = tail_bytes >= full_size
        window_fully_covered = covered_whole or (oldest_ts is not None and oldest_ts <= cutoff)
        if window_fully_covered or tail_bytes >= max_bytes_cap:
            windowed = [r for r in rows if _safe_float(r.get("timestamp")) >= cutoff]
            return windowed, window_fully_covered
        tail_bytes *= 4

PORT = int(os.getenv("PORT", "5000"))
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

def read_json_file(filepath):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def read_jsonl_fills(asset_suffix=""):
    filepath = os.path.join(BASE_DIR, "data", f"fills_log{asset_suffix}.jsonl")
    entries = []
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except Exception:
                            continue
        except Exception:
            pass
    return entries

def get_calibration_maturity(fp, platt_threshold=30, isotonic_threshold=300):
    """
    All-time (not lookback-windowed) count of distinct settled windows, so growth-stage
    decisions can be judged against real progress toward the calibrator's trust
    thresholds instead of guesswork. Below platt_threshold, EmpiricalCalibrator.calibrate()
    is a no-op (raw model probabilities pass through unshrunk) -- results before that
    point shouldn't be used to judge the strategy.
    """
    distinct_windows = 0
    if os.path.exists(fp):
        try:
            window_ids = set()
            with open(fp, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("realized_up") in ("0", "1"):
                        window_ids.add(row.get("window_id"))
            distinct_windows = len(window_ids)
        except Exception:
            pass

    if distinct_windows < platt_threshold:
        stage = "uncalibrated"
    elif distinct_windows < isotonic_threshold:
        stage = "platt"
    else:
        stage = "isotonic"

    return {
        "distinct_settled_windows": distinct_windows,
        "stage": stage,
        "windows_until_platt_ready": max(0, platt_threshold - distinct_windows),
        "windows_until_isotonic_ready": max(0, isotonic_threshold - distinct_windows),
        "platt_progress_pct": round(min(100.0, 100.0 * distinct_windows / platt_threshold), 1),
        "isotonic_progress_pct": round(min(100.0, 100.0 * distinct_windows / isotonic_threshold), 1),
    }


def get_asset_confidence_weight(asset_suffix="", min_windows=30, rolling_window=100, k=8.0):
    log_path = os.path.join(BASE_DIR, "data", f"calibration_log{asset_suffix}.csv")
    if not os.path.exists(log_path):
        return 1.0
    try:
        window_groups = {}
        with open(log_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                r_up = row.get("realized_up")
                if r_up in ("0", "1"):
                    wid = row.get("window_id")
                    if wid not in window_groups:
                        window_groups[wid] = []
                    window_groups[wid].append(row)
        if len(window_groups) < min_windows:
            return 1.0

        window_items = []
        for wid, rows in window_groups.items():
            ts = max(float(r["timestamp"]) for r in rows)
            window_items.append((ts, rows))
        window_items.sort(key=lambda x: x[0], reverse=True)
        window_items = window_items[:rolling_window]

        model_sq_err, market_sq_err = [], []
        for _, rows in window_items:
            best = min(rows, key=lambda r: abs(float(r["tau_sec"]) - 150.0))
            y = int(best["realized_up"])
            model_sq_err.append((float(best["p_model"]) - y) ** 2)
            try:
                market_sq_err.append((float(best["p_market"]) - y) ** 2)
            except Exception:
                pass

        if not model_sq_err or not market_sq_err:
            return 1.0
        model_brier = sum(model_sq_err) / len(model_sq_err)
        market_brier = sum(market_sq_err) / len(market_sq_err)
        weight = 0.5 + (market_brier - model_brier) * k
        return max(0.0, min(1.0, weight))
    except Exception:
        return 1.0

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path

            if path in ("/", "/index.html"):
                self.serve_html()
            elif path == "/api/state":
                self.serve_api_state()
            elif path == "/healthz":
                self.serve_healthz()
            elif path == "/api/drawdown":
                self.serve_api_drawdown()
            elif path == "/api/recent_trades":
                self.serve_api_recent_trades(parse_qs(parsed.query))
            elif path == "/api/funnel":
                self.serve_api_funnel(parse_qs(parsed.query))
            elif path == "/api/calibration":
                self.serve_api_calibration(parse_qs(parsed.query))
            elif path == "/api/control":
                self.serve_api_control_get()
            elif path == "/api/logs":
                self.serve_api_logs(parse_qs(parsed.query))
            elif path == "/api/calibration_raw":
                self.serve_api_calibration_raw(parse_qs(parsed.query))
            elif path == "/api/version":
                self.serve_api_version()
            elif path == "/api/risk_config":
                self.serve_api_risk_config()
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not Found")
        except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/api/control":
                self.serve_api_control_post()
            elif parsed.path == "/api/reset_daily_breaker":
                self.serve_api_reset_daily_breaker()
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not Found")
        except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
            pass

    def serve_html(self):
        html_path = os.path.join(BASE_DIR, "templates", "dashboard.html")
        if os.path.exists(html_path):
            with open(html_path, "r", encoding="utf-8") as f:
                content = f.read().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"dashboard.html not found")

    def serve_api_state(self):
        assets = ["BTC"]
        assets_data = {}
        all_fills = []

        for asset in assets:
            suffix = "" if asset == "BTC" else f"_{asset.lower()}"
            risk_file = os.path.join(BASE_DIR, "data", f"risk_state{suffix}.json")
            pos_file = os.path.join(BASE_DIR, "data", f"open_positions{suffix}.json")

            risk_state = read_json_file(risk_file) or {
                "current_day": "N/A",
                "daily_pnl": 0.0,
                "circuit_breaker_triggered": False
            }
            positions_data = read_json_file(pos_file) or {
                "positions": [],
                "simulated_balance": config.starting_balance_usd
            }

            conf_weight = get_asset_confidence_weight(suffix)

            assets_data[asset] = {
                "risk": risk_state,
                "positions": positions_data,
                "confidence_weight": conf_weight
            }

            fills = read_jsonl_fills(suffix)
            all_fills.extend(fills)

        # Sort all fills chronologically, newest first
        all_fills.sort(key=lambda x: x.get("ts", 0), reverse=True)

        portfolio_file_state = read_json_file(os.path.join(BASE_DIR, "data", "risk_state_portfolio.json")) or {}
        portfolio_risk = {
            "current_day": portfolio_file_state.get("current_day", "N/A"),
            "daily_pnl": sum(assets_data[a]["risk"].get("daily_pnl", 0.0) for a in assets),
            "circuit_breaker_triggered": portfolio_file_state.get("circuit_breaker_triggered", False),
        }

        payload = {
            "status": "OK",
            "assets": assets_data,
            "portfolio": portfolio_risk,
            "fills": all_fills[:100]
        }

        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def serve_api_drawdown(self):
        # Phase-1 permanent portfolio drawdown breaker (src/bot.py). Unlike the
        # daily circuit breakers, this file only exists once the breaker has
        # tripped -- absence means healthy, not "unknown".
        fp = os.path.join(BASE_DIR, "data", "risk_state_drawdown.json")
        data = read_json_file(fp)
        if data is None:
            self._send_json({"tripped": False, "file_exists": False, "note": "Breaker has never tripped."})
            return
        self._send_json({
            "tripped": bool(data.get("circuit_breaker_triggered", False)),
            "file_exists": True,
            "starting_balance_usd": data.get("starting_balance_usd"),
            "drawdown_floor_usd": data.get("drawdown_floor_usd"),
            "current_simulated_balance": data.get("current_simulated_balance"),
            "updated_at": data.get("updated_at"),
        })

    def serve_api_recent_trades(self, query):
        asset = (query.get("asset", ["BTC"])[0] or "BTC").upper()
        if asset not in ASSET_SUFFIX:
            self._send_json({"error": f"unknown asset '{asset}', expected BTC"}, status=400)
            return
        status_filter = query.get("status", ["EXECUTED"])[0]
        limit = min(max(int(_safe_float(query.get("limit", ["20"])[0], 20)), 1), 200)
        fp = os.path.join(BASE_DIR, "data", f"trade_events{ASSET_SUFFIX[asset]}.csv")

        # Grow the tail read until `limit` matching rows are found or the
        # whole file has been scanned -- EXECUTED rows are a small fraction
        # of all funnel events, so a small fixed tail often isn't enough.
        tail_bytes = 1_000_000
        full_size = os.path.getsize(fp) if os.path.exists(fp) else 0
        matched = []
        fully_scanned = full_size == 0
        while True:
            rows = tail_read_csv_rows(fp, tail_bytes)
            matched = [r for r in rows if (status_filter == "" or r.get("status") == status_filter)]
            fully_scanned = tail_bytes >= full_size
            if len(matched) >= limit or fully_scanned or tail_bytes >= 32_000_000:
                break
            tail_bytes *= 4

        recent = matched[-limit:]
        trades = [{
            "timestamp": _safe_float(r.get("timestamp")),
            "outcome": r.get("outcome"),
            "entry_price": _safe_float(r.get("direct_ask")),
            "size_usd": _safe_float(r.get("size_usd")),
            "status": r.get("status"),
        } for r in recent]

        self._send_json({
            "asset": asset,
            "status_filter": status_filter,
            "count": len(trades),
            "requested_limit": limit,
            "fully_scanned_available_history": fully_scanned,
            "trades": trades,
        })

    def serve_api_funnel(self, query):
        asset = (query.get("asset", ["BTC"])[0] or "BTC").upper()
        if asset not in ASSET_SUFFIX:
            self._send_json({"error": f"unknown asset '{asset}', expected BTC"}, status=400)
            return
        window_hours = _safe_float(query.get("window_hours", ["4"])[0], 4.0)
        baseline_hours = _safe_float(query.get("baseline_hours", ["96"])[0], 96.0)
        fp = os.path.join(BASE_DIR, "data", f"trade_events{ASSET_SUFFIX[asset]}.csv")

        def counts_for(hours):
            rows, covered = read_rows_covering_window(fp, hours * 3600)
            c = {}
            for r in rows:
                s = r.get("status", "UNKNOWN")
                c[s] = c.get(s, 0) + 1
            return c, len(rows), covered

        window_counts, window_total, window_covered = counts_for(window_hours)
        baseline_counts, baseline_total, baseline_covered = counts_for(baseline_hours)

        def phantom_rate(counts, total):
            return (counts.get("BLOCKED_PHANTOM", 0) / total) if total else 0.0

        self._send_json({
            "asset": asset,
            "window_hours": window_hours,
            "window_counts": window_counts,
            "window_total_events": window_total,
            "window_phantom_rate": round(phantom_rate(window_counts, window_total), 4),
            "window_fully_covered": window_covered,
            "baseline_hours": baseline_hours,
            "baseline_counts": baseline_counts,
            "baseline_total_events": baseline_total,
            "baseline_phantom_rate": round(phantom_rate(baseline_counts, baseline_total), 4),
            "baseline_fully_covered": baseline_covered,
        })

    def serve_api_calibration(self, query):
        asset = (query.get("asset", ["BTC"])[0] or "BTC").upper()
        if asset not in ASSET_SUFFIX:
            self._send_json({"error": f"unknown asset '{asset}', expected BTC"}, status=400)
            return
        days = _safe_float(query.get("days", ["7"])[0], 7.0)
        fp = os.path.join(BASE_DIR, "data", f"calibration_log{ASSET_SUFFIX[asset]}.csv")

        rows, covered = read_rows_covering_window(fp, days * 86400)

        # Dedupe to one row per window_id, picking the sample closest to
        # tau_sec == 150 (mirrors dashboard's get_asset_confidence_weight),
        # then score only resolved windows (realized_up in {0,1}).
        window_groups = {}
        for r in rows:
            if r.get("realized_up") not in ("0", "1"):
                continue
            wid = r.get("window_id")
            window_groups.setdefault(wid, []).append(r)

        model_sq_err, market_sq_err = [], []
        for wid, wrows in window_groups.items():
            try:
                best = min(wrows, key=lambda r: abs(_safe_float(r.get("tau_sec"), 999) - 150.0))
                y = int(best["realized_up"])
                model_sq_err.append((_safe_float(best.get("p_model")) - y) ** 2)
                market_sq_err.append((_safe_float(best.get("p_market")) - y) ** 2)
            except Exception:
                continue

        model_brier = (sum(model_sq_err) / len(model_sq_err)) if model_sq_err else None
        market_brier = (sum(market_sq_err) / len(market_sq_err)) if market_sq_err else None

        self._send_json({
            "asset": asset,
            "days": days,
            "resolved_windows": len(model_sq_err),
            "model_brier": round(model_brier, 4) if model_brier is not None else None,
            "market_brier": round(market_brier, 4) if market_brier is not None else None,
            "model_better_than_market": (model_brier < market_brier) if (model_brier is not None and market_brier is not None) else None,
            "window_fully_covered": covered,
            "maturity": get_calibration_maturity(fp),
        })

    def serve_api_calibration_raw(self, query):
        """
        Dump one row per distinct settled window, including the newer shadow
        research columns (spot_lead_lag, twap_dev, book_depth_skew) when present.
        Read-only, no secrets involved -- exists so real quant analysis on the
        actual historical data can be done outside this container.

        Two different ticks are sampled per window, because the two families of
        columns are only meaningful at different points in the window's life:
          - "best" = the tick closest to tau_sec==150 (mid-window), same dedupe
            rule as serve_api_calibration -- used for moneyness/vol/ofi/cbi/z/
            p_model/p_market, so these stay comparable with the existing
            Brier-score endpoint.
          - "settle" = the tick closest to tau_sec==0 (last tick before
            resolution) -- used for spot_lead_lag/twap_dev/book_depth_skew.
            twap_dev in particular is computed in bot.py as
            (spot_price - known_avg_price) / spot_price, and known_avg_price is
            only populated once the trailing-TWAP settlement tracking kicks in
            near expiry; every tick before that carries twap_dev's 0.0 default.
            Sampling those columns at tau_sec==150 (mid-window) would read back
            0.0 for essentially every window regardless of any real TWAP
            mispricing, silently making Hypothesis 2 (TWAP-averaging bias)
            untestable from this endpoint's output.
        """
        asset = (query.get("asset", ["BTC"])[0] or "BTC").upper()
        if asset not in ASSET_SUFFIX:
            self._send_json({"error": f"unknown asset '{asset}', expected BTC"}, status=400)
            return
        days = _safe_float(query.get("days", ["30"])[0], 30.0)
        fp = os.path.join(BASE_DIR, "data", f"calibration_log{ASSET_SUFFIX[asset]}.csv")

        rows, covered = read_rows_covering_window(fp, days * 86400, max_bytes_cap=64_000_000)

        window_groups = {}
        for r in rows:
            wid = r.get("window_id")
            window_groups.setdefault(wid, []).append(r)

        def _shadow_val(row, key):
            v = row.get(key)
            return _safe_float(v) if v not in (None, "") else None

        out = []
        for wid, wrows in window_groups.items():
            try:
                best = min(wrows, key=lambda r: abs(_safe_float(r.get("tau_sec"), 999) - 150.0))
                settle = min(wrows, key=lambda r: abs(_safe_float(r.get("tau_sec"), 999) - 0.0))
            except Exception:
                continue
            out.append({
                "window_id": wid,
                "timestamp": _safe_float(best.get("timestamp")),
                "tau_sec": _safe_float(best.get("tau_sec")),
                "moneyness": _safe_float(best.get("moneyness")),
                "vol_annualized": _safe_float(best.get("vol_annualized")),
                "ofi": _safe_float(best.get("ofi")),
                "cbi": _safe_float(best.get("cbi")),
                "z": _safe_float(best.get("z")),
                "p_model": _safe_float(best.get("p_model")),
                "p_model_shadow": _safe_float(best.get("p_model_shadow")) if best.get("p_model_shadow") not in (None, "") else None,
                "p_market": _safe_float(best.get("p_market")),
                "realized_up": best.get("realized_up") if best.get("realized_up") in ("0", "1") else None,
                "settle_tau_sec": _safe_float(settle.get("tau_sec")),
                "spot_lead_lag": _shadow_val(settle, "spot_lead_lag"),
                "twap_dev": _shadow_val(settle, "twap_dev"),
                "book_depth_skew": _shadow_val(settle, "book_depth_skew"),
                "spot_lead_lag_mid": _shadow_val(best, "spot_lead_lag"),
                "book_depth_skew_mid": _shadow_val(best, "book_depth_skew"),
            })
        out.sort(key=lambda r: r["timestamp"])

        self._send_json({
            "asset": asset,
            "days": days,
            "window_fully_covered": covered,
            "count": len(out),
            "windows": out,
        })

    def serve_api_control_get(self):
        state = read_control_state(force=True)
        self._send_json({
            "paused": state.get("paused", False),
            "updated_at": state.get("updated_at"),
            "updated_by": state.get("updated_by"),
            "secret_configured": bool(config.control_secret),
        })

    def serve_api_control_post(self):
        if not config.control_secret:
            self._send_json({"error": "CONTROL_SECRET is not set on the server -- POST /api/control is disabled (fail closed)."}, status=503)
            return
        supplied = self.headers.get("X-Control-Secret", "")
        if not hmac.compare_digest(supplied, config.control_secret):
            self._send_json({"error": "invalid or missing X-Control-Secret header"}, status=401)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            body = json.loads(raw or b"{}")
        except Exception:
            self._send_json({"error": "invalid JSON body"}, status=400)
            return
        if "paused" not in body or not isinstance(body["paused"], bool):
            self._send_json({"error": "body must be a JSON object with a boolean 'paused' field"}, status=400)
            return
        updated_by = str(body.get("updated_by", "unknown"))[:100]
        state = write_control_state(bool(body["paused"]), updated_by=updated_by)
        self._send_json({"ok": True, **state})

    def serve_api_reset_daily_breaker(self):
        if not config.control_secret:
            self._send_json({"error": "CONTROL_SECRET is not set on the server -- POST /api/reset_daily_breaker is disabled (fail closed)."}, status=503)
            return
        supplied = self.headers.get("X-Control-Secret", "")
        if not hmac.compare_digest(supplied, config.control_secret):
            self._send_json({"error": "invalid or missing X-Control-Secret header"}, status=401)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            body = json.loads(raw or b"{}")
        except Exception:
            self._send_json({"error": "invalid JSON body"}, status=400)
            return
        updated_by = str(body.get("updated_by", "unknown"))[:100]
        # Only clears circuit_breaker_triggered, never daily_pnl -- if the
        # underlying loss is still past max_daily_loss_usd, RiskManager
        # re-trips it on the very next can_trade() check.
        req = request_reset(updated_by=updated_by)
        self._send_json({"ok": True, **req, "note": "Breaker flag cleared. If daily PnL is still past the loss floor, it will re-trip on the next trade check."})

    def serve_api_version(self):
        self._send_json({
            "git_commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "unknown")[:12],
            "git_branch": os.environ.get("RAILWAY_GIT_BRANCH", "unknown"),
            "deployment_id": os.environ.get("RAILWAY_DEPLOYMENT_ID", "unknown"),
            "environment": os.environ.get("RAILWAY_ENVIRONMENT_NAME", "local"),
        })

    def serve_api_risk_config(self):
        self._send_json({
            "paper_trading": config.paper_trading,
            "target_asset": config.target_asset,
            "starting_balance_usd": config.starting_balance_usd,
            "max_position_usd": config.max_position_usd,
            "max_daily_loss_usd": config.max_daily_loss_usd,
            "max_portfolio_daily_loss_usd": config.max_portfolio_daily_loss_usd,
            "max_portfolio_drawdown_pct": config.max_portfolio_drawdown_pct,
            "kelly_fraction": config.kelly_fraction,
            "slippage_tolerance": config.slippage_tolerance,
            "note": "Strategy entry thresholds (min_edge, min_abs_z, entry price band) are set in src/bot.py's AssetTradingEngine, not here.",
        })

    def serve_api_logs(self, query):
        lines_wanted = min(max(int(_safe_float(query.get("lines", ["200"])[0], 200)), 1), 2000)
        log_files = sorted(_glob.glob(os.path.join(BASE_DIR, "logs", "bot_*.log")))
        if not log_files:
            self._send_json({"error": "no log files found yet", "lines": []}, status=404)
            return
        latest = log_files[-1]
        try:
            # Tail-read: grow the read window until enough lines are captured.
            tail_bytes = 200_000
            full_size = os.path.getsize(latest)
            while True:
                with open(latest, "rb") as f:
                    if tail_bytes >= full_size:
                        f.seek(0)
                    else:
                        f.seek(full_size - tail_bytes)
                    data = f.read()
                text = data.decode("utf-8", errors="replace")
                lines = text.split("\n")
                if len(lines) > 1:
                    lines = lines[1:]
                lines = [l for l in lines if l.strip()]
                if len(lines) >= lines_wanted or tail_bytes >= full_size or tail_bytes >= 8_000_000:
                    break
                tail_bytes *= 4
            self._send_json({"file": os.path.basename(latest), "lines": lines[-lines_wanted:]})
        except Exception as e:
            self._send_json({"error": f"failed to read log file: {e}"}, status=500)

    def serve_healthz(self):
        now = time.time()
        freshest = None
        for suffix in ("", "_eth", "_sol"):
            fp = os.path.join(BASE_DIR, "data", f"trade_events{suffix}.csv")
            if os.path.exists(fp):
                mtime = os.path.getmtime(fp)
                freshest = mtime if freshest is None else max(freshest, mtime)
        age = (now - freshest) if freshest else None
        healthy = age is not None and age < 30
        body = json.dumps({"healthy": healthy, "age_sec": age}).encode()
        self.send_response(200 if healthy else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Silence routine access logs so stdout remains clean
        pass

from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import socket

class DualStackServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

def run():
    host = os.getenv("HOST", "0.0.0.0")
    server = DualStackServer((host, PORT), DashboardHandler)
    print(f"============================================================")
    print(f"  POLYMARKET 5M TERMINAL DASHBOARD RUNNING")
    print(f"  Local:    http://127.0.0.1:{PORT}")
    print(f"  Network:  http://localhost:{PORT}")
    print(f"  Press Ctrl+C to stop.")
    print(f"============================================================")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
        server.server_close()

if __name__ == "__main__":
    run()
