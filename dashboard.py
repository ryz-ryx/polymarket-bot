import os
import sys
import json
import csv
import glob
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

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
        assets = ["BTC", "ETH", "SOL"]
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
                "simulated_balance": 50.0
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
