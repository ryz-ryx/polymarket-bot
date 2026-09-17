#!/usr/bin/env python3
"""MCP server for polymarket-bot API endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import urllib.request
import urllib.error
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("mcp.polymarket")

_MCP_AVAILABLE = False
MCPServer = None
try:
    from mcp.server import MCPServer
    _MCP_AVAILABLE = True
except ImportError:
    pass

BASE = "https://polymarketbot.up.railway.app"
CONTROL_SECRET = os.environ.get("CONTROL_SECRET", "")


def _get(path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    qs = ""
    if params:
        qs = "?" + "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    url = f"{BASE}{path}{qs}"
    log.info("GET %s", url)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        return {"error": f"HTTP {e.code}: {body}"}
    except Exception as e:
        return {"error": str(e)}


def _post(path: str, body: dict[str, Any], hdrs: dict[str, str] | None = None) -> dict[str, Any]:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if hdrs:
        headers.update(hdrs)
    log.info("POST %s body=%s", url, body)
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        b = e.read().decode() if e.fp else ""
        return {"error": f"HTTP {e.code}: {b}"}
    except Exception as e:
        return {"error": str(e)}


def _tools() -> list[dict[str, Any]]:
    return [
        {"name": "polymarket_state", "description": "Get current portfolio state, asset PnL, circuit breakers, and positions.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "polymarket_pnl", "description": "Get just the PnL numbers: balance, daily PnL, and confidence weight (how much the model's own probability is currently being trusted vs the market's).", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "polymarket_summary", "description": "One consolidated human-readable status digest: balance, PnL, confidence weight, calibration maturity, model-vs-market Brier, and whether any circuit breaker is tripped. Use this for a general 'how's it going' / 'give me a summary' request instead of calling state+calibration+drawdown separately.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "polymarket_drawdown", "description": "Check if the drawdown breaker is tripped.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "polymarket_funnel", "description": "Get funnel stats for an asset (phantom rate, blocked signals).", "inputSchema": {"type": "object", "properties": {"asset": {"type": "string", "default": "BTC", "description": "Asset symbol (BTC)"}, "window_hours": {"type": "integer", "default": 4, "description": "Window in hours"}, "baseline_hours": {"type": "integer", "default": 96, "description": "Baseline in hours"}}}},
        {"name": "polymarket_calibration", "description": "Get model calibration stats (Brier scores) for an asset.", "inputSchema": {"type": "object", "properties": {"asset": {"type": "string", "default": "BTC", "description": "Asset symbol"}}}},
        {"name": "polymarket_recent_trades", "description": "Get recent executed trades, including the reasoning behind each one (z-score, model probability, real edge, hurdle). Defaults to ALL assets combined, not just BTC.", "inputSchema": {"type": "object", "properties": {"asset": {"type": "string", "default": "ALL", "description": "Asset symbol (BTC/ETH/SOL) or ALL for every traded asset combined"}, "status": {"type": "string", "default": "EXECUTED", "description": "Trade status filter"}, "limit": {"type": "integer", "default": 50, "description": "Max trades to return (up to 500)"}}}},
        {"name": "polymarket_fills", "description": "Full settlement history (BUY/WIN/LOSS fills with net_pnl) across one or all assets, with real pagination -- unlike polymarket_summary's recent_fills which is capped at 5. Use this for 'show me all the trades' / 'how did each trade settle' / building a full win-loss record.", "inputSchema": {"type": "object", "properties": {"asset": {"type": "string", "default": "ALL", "description": "Asset symbol (BTC/ETH/SOL) or ALL for every traded asset combined"}, "type": {"type": "string", "default": "", "description": "Filter by fill type: BUY, WIN, or LOSS (blank = all types)"}, "limit": {"type": "integer", "default": 100, "description": "Max fills to return (up to 1000)"}}}},
        {"name": "polymarket_logs", "description": "Get recent log lines from the bot.", "inputSchema": {"type": "object", "properties": {"lines": {"type": "integer", "default": 40, "description": "Number of lines (1-200)"}}}},
        {"name": "polymarket_control", "description": "Pause or resume the bot (requires CONTROL_SECRET). Prefer polymarket_pause / polymarket_start for clearer intent.", "inputSchema": {"type": "object", "properties": {"paused": {"type": "boolean", "description": "True to pause, False to resume"}, "updated_by": {"type": "string", "default": "hermes", "description": "Who triggered the change"}}, "required": ["paused"]}},
        {"name": "polymarket_pause", "description": "Pause the bot (stops opening new trades; requires CONTROL_SECRET).", "inputSchema": {"type": "object", "properties": {"updated_by": {"type": "string", "default": "hermes", "description": "Who triggered the change"}}}},
        {"name": "polymarket_start", "description": "Resume/start the bot after a pause (requires CONTROL_SECRET).", "inputSchema": {"type": "object", "properties": {"updated_by": {"type": "string", "default": "hermes", "description": "Who triggered the change"}}}},
        {"name": "polymarket_version", "description": "Get the git commit/branch/deployment currently running in production, so you can confirm whether a given fix has actually deployed.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "polymarket_risk_config", "description": "Get the bot's current risk settings (max position size, max daily loss, Kelly fraction, drawdown limits, paper vs live trading).", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "polymarket_reset_daily_breaker", "description": "Clear a tripped daily-loss circuit breaker (requires CONTROL_SECRET). Only clears the breaker flag, never the underlying daily PnL -- if the day's loss is still past the configured floor, it re-trips on the very next trade check, so this can never mask a genuinely bad day. Use only when asked to un-stick a breaker the user believes tripped in error.", "inputSchema": {"type": "object", "properties": {"updated_by": {"type": "string", "default": "hermes", "description": "Who triggered the change"}}}},
        {"name": "polymarket_usage", "description": "List every available command/tool this server exposes, with a one-line description of each. Use this for 'what can you do' / 'usage' / 'help' requests.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "polymarket_edge_validation", "description": "The ground-truth 'is this bot actually profitable' check: lifetime win rate and net PnL AFTER real fees, per asset and portfolio-wide, plus a verdict (NO_DATA / INSUFFICIENT_SAMPLE / PROFITABLE_SO_FAR / LOSING_SO_FAR) based on whether there's even enough settled trades to trust the number yet. Trust this over any backtest claim. Use for 'is it working', 'is it profitable', 'should we fund it' questions.", "inputSchema": {"type": "object", "properties": {"min_sample": {"type": "integer", "default": 50, "description": "Minimum settled trades before a win rate is treated as meaningful rather than noise"}}}},
    ]


def _call_tool(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    args = arguments or {}
    if name == "polymarket_state":
        return _get("/api/state")
    if name == "polymarket_pnl":
        # Was hardcoded to BTC only -- with ETH/SOL now trading live alongside
        # BTC, this silently omitted them from every "what's my PnL" answer,
        # understating real portfolio state with no way for the caller to
        # even ask otherwise (no asset param existed). Report every active
        # asset plus the portfolio total.
        state = _get("/api/state")
        if "error" in state:
            return state
        per_asset = {}
        for asset, a in state.get("assets", {}).items():
            per_asset[asset] = {
                "balance_usd": a.get("positions", {}).get("simulated_balance"),
                "daily_pnl_usd": a.get("risk", {}).get("daily_pnl"),
                "confidence_weight": a.get("confidence_weight"),
                "circuit_breaker_triggered": a.get("risk", {}).get("circuit_breaker_triggered"),
                "current_day": a.get("risk", {}).get("current_day"),
            }
        portfolio = state.get("portfolio", {})
        return {
            "assets": per_asset,
            "portfolio_daily_pnl_usd": portfolio.get("daily_pnl"),
            "portfolio_circuit_breaker_triggered": portfolio.get("circuit_breaker_triggered"),
        }
    if name == "polymarket_summary":
        # Same BTC-only bug as polymarket_pnl above -- fixed the same way.
        state = _get("/api/state")
        drawdown = _get("/api/drawdown")
        if "error" in state:
            return state
        per_asset = {}
        for asset, a in state.get("assets", {}).items():
            calib = _get("/api/calibration", {"asset": asset})
            per_asset[asset] = {
                "balance_usd": a.get("positions", {}).get("simulated_balance"),
                "daily_pnl_usd": a.get("risk", {}).get("daily_pnl"),
                "confidence_weight": a.get("confidence_weight"),
                "daily_circuit_breaker_triggered": a.get("risk", {}).get("circuit_breaker_triggered"),
                "calibration_maturity": calib.get("maturity") if "error" not in calib else None,
                "model_brier": calib.get("model_brier") if "error" not in calib else None,
                "market_brier": calib.get("market_brier") if "error" not in calib else None,
                "model_better_than_market": calib.get("model_better_than_market") if "error" not in calib else None,
            }
        portfolio = state.get("portfolio", {})
        return {
            "assets": per_asset,
            "portfolio_daily_pnl_usd": portfolio.get("daily_pnl"),
            "portfolio_circuit_breaker_triggered": portfolio.get("circuit_breaker_triggered"),
            "drawdown_breaker_tripped": drawdown.get("tripped") if "error" not in drawdown else None,
            # Was state.get("fills", [])[:5] -- capped at 5 fills system-wide regardless
            # of what was asked for, and sourced from /api/state's own 100-fill cap on
            # top of that. Use polymarket_fills directly for anything beyond a quick
            # glance; this stays small on purpose since it's meant as a summary.
            "recent_fills": _get("/api/fills", {"limit": "5"}).get("fills", []),
        }
    if name == "polymarket_drawdown":
        return _get("/api/drawdown")
    if name == "polymarket_funnel":
        return _get("/api/funnel", {"asset": args.get("asset", "BTC")})
    if name == "polymarket_calibration":
        return _get("/api/calibration", {"asset": args.get("asset", "BTC")})
    if name == "polymarket_recent_trades":
        return _get("/api/recent_trades", {"asset": args.get("asset", "ALL"), "status": args.get("status", "EXECUTED"), "limit": str(args.get("limit", 50))})
    if name == "polymarket_fills":
        return _get("/api/fills", {"asset": args.get("asset", "ALL"), "type": args.get("type", ""), "limit": str(args.get("limit", 100))})
    if name == "polymarket_logs":
        return _get("/api/logs", {"lines": str(args.get("lines", 40))})
    if name == "polymarket_control":
        if not CONTROL_SECRET:
            return {"error": "CONTROL_SECRET not configured"}
        return _post("/api/control", {"paused": args.get("paused", False), "updated_by": args.get("updated_by", "hermes")}, hdrs={"X-Control-Secret": CONTROL_SECRET})
    if name == "polymarket_pause":
        if not CONTROL_SECRET:
            return {"error": "CONTROL_SECRET not configured"}
        return _post("/api/control", {"paused": True, "updated_by": args.get("updated_by", "hermes")}, hdrs={"X-Control-Secret": CONTROL_SECRET})
    if name == "polymarket_start":
        if not CONTROL_SECRET:
            return {"error": "CONTROL_SECRET not configured"}
        return _post("/api/control", {"paused": False, "updated_by": args.get("updated_by", "hermes")}, hdrs={"X-Control-Secret": CONTROL_SECRET})
    if name == "polymarket_version":
        return _get("/api/version")
    if name == "polymarket_risk_config":
        return _get("/api/risk_config")
    if name == "polymarket_reset_daily_breaker":
        if not CONTROL_SECRET:
            return {"error": "CONTROL_SECRET not configured"}
        return _post("/api/reset_daily_breaker", {"updated_by": args.get("updated_by", "hermes")}, hdrs={"X-Control-Secret": CONTROL_SECRET})
    if name == "polymarket_usage":
        return {"commands": [{"name": t["name"], "description": t["description"]} for t in _tools()]}
    if name == "polymarket_edge_validation":
        return _get("/api/edge_validation", {"min_sample": str(args.get("min_sample", 50))})
    return {"error": f"Unknown tool: {name}"}


def main() -> None:
    if not _MCP_AVAILABLE:
        log.error("mcp package not installed — run: pip install mcp")
        sys.exit(1)

    server = MCPServer("polymarket-tools", "1.0.0", "Polymarket bot API tools")

    @server.tool()
    def polymarket_state() -> dict[str, Any]:
        """Get current portfolio state, asset PnL, circuit breakers, and positions."""
        return _call_tool("polymarket_state", {})

    @server.tool()
    def polymarket_pnl() -> dict[str, Any]:
        """Get just the PnL numbers: balance, daily PnL, confidence weight."""
        return _call_tool("polymarket_pnl", {})

    @server.tool()
    def polymarket_summary() -> dict[str, Any]:
        """One consolidated status digest: balance, PnL, confidence weight, calibration maturity, model-vs-market Brier, breaker status."""
        return _call_tool("polymarket_summary", {})

    @server.tool()
    def polymarket_drawdown() -> dict[str, Any]:
        """Check if the drawdown breaker is tripped."""
        return _call_tool("polymarket_drawdown", {})

    @server.tool()
    def polymarket_funnel(asset: str = "BTC") -> dict[str, Any]:
        """Get funnel stats for an asset."""
        return _call_tool("polymarket_funnel", {"asset": asset})

    @server.tool()
    def polymarket_calibration(asset: str = "BTC") -> dict[str, Any]:
        """Get model calibration stats."""
        return _call_tool("polymarket_calibration", {"asset": asset})

    @server.tool()
    def polymarket_recent_trades(asset: str = "ALL", status: str = "EXECUTED", limit: int = 50) -> dict[str, Any]:
        """Get recent trades (all assets by default), including why each one was taken."""
        return _call_tool("polymarket_recent_trades", {"asset": asset, "status": status, "limit": limit})

    @server.tool()
    def polymarket_fills(asset: str = "ALL", type: str = "", limit: int = 100) -> dict[str, Any]:
        """Full settlement history (BUY/WIN/LOSS with net_pnl), all assets by default, real pagination."""
        return _call_tool("polymarket_fills", {"asset": asset, "type": type, "limit": limit})

    @server.tool()
    def polymarket_logs(lines: int = 40) -> dict[str, Any]:
        """Get recent log lines."""
        return _call_tool("polymarket_logs", {"lines": lines})

    @server.tool()
    def polymarket_control(paused: bool, updated_by: str = "hermes") -> dict[str, Any]:
        """Pause or resume the bot."""
        return _call_tool("polymarket_control", {"paused": paused, "updated_by": updated_by})

    @server.tool()
    def polymarket_pause(updated_by: str = "hermes") -> dict[str, Any]:
        """Pause the bot (stops opening new trades)."""
        return _call_tool("polymarket_pause", {"updated_by": updated_by})

    @server.tool()
    def polymarket_start(updated_by: str = "hermes") -> dict[str, Any]:
        """Resume/start the bot after a pause."""
        return _call_tool("polymarket_start", {"updated_by": updated_by})

    @server.tool()
    def polymarket_version() -> dict[str, Any]:
        """Get the git commit/branch/deployment currently running in production."""
        return _call_tool("polymarket_version", {})

    @server.tool()
    def polymarket_risk_config() -> dict[str, Any]:
        """Get the bot's current risk settings (position size, daily loss limit, Kelly fraction, etc)."""
        return _call_tool("polymarket_risk_config", {})

    @server.tool()
    def polymarket_reset_daily_breaker(updated_by: str = "hermes") -> dict[str, Any]:
        """Clear a tripped daily-loss circuit breaker. Only lifts the flag; re-trips immediately if the underlying loss is still past the floor."""
        return _call_tool("polymarket_reset_daily_breaker", {"updated_by": updated_by})

    @server.tool()
    def polymarket_usage() -> dict[str, Any]:
        """List every available command this server exposes, with a description of each."""
        return _call_tool("polymarket_usage", {})

    @server.tool()
    def polymarket_edge_validation(min_sample: int = 50) -> dict[str, Any]:
        """Ground-truth lifetime win rate and fee-adjusted net PnL per asset, with a PROFITABLE_SO_FAR/LOSING_SO_FAR/INSUFFICIENT_SAMPLE verdict. Trust this over any backtest claim."""
        return _call_tool("polymarket_edge_validation", {"min_sample": min_sample})

    log.info("polymarket MCP server starting — tools: %s", [t["name"] for t in _tools()])

    # Cloud deploys (Railway) run this over streamable-HTTP so a remote Hermes
    # instance can reach it via `url:` in mcp_servers config -- no local file/venv
    # needed, since every tool here is already just an HTTP proxy to the dashboard
    # API. Local desktop Hermes keeps using stdio (`command:` in config.yaml)
    # unchanged. Railway sets PORT automatically; MCP_TRANSPORT=http forces HTTP
    # mode even without PORT (e.g. local testing).
    port_env = os.environ.get("PORT")
    use_http = bool(port_env) or os.environ.get("MCP_TRANSPORT", "").lower() == "http"

    async def _run() -> None:
        if use_http:
            port = int(port_env or "8000")
            log.info("Running as streamable-HTTP MCP server on 0.0.0.0:%d/mcp", port)
            await server.run_streamable_http_async(host="0.0.0.0", port=port, stateless_http=True)
        else:
            await server.run_stdio_async()

    asyncio.run(_run())


if __name__ == "__main__":
    main()