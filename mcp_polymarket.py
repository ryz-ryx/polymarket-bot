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
        {"name": "polymarket_recent_trades", "description": "Get recent executed trades for an asset.", "inputSchema": {"type": "object", "properties": {"asset": {"type": "string", "default": "BTC", "description": "Asset symbol"}, "status": {"type": "string", "default": "EXECUTED", "description": "Trade status filter"}, "limit": {"type": "integer", "default": 20, "description": "Max trades to return"}}}},
        {"name": "polymarket_logs", "description": "Get recent log lines from the bot.", "inputSchema": {"type": "object", "properties": {"lines": {"type": "integer", "default": 40, "description": "Number of lines (1-200)"}}}},
        {"name": "polymarket_control", "description": "Pause or resume the bot (requires CONTROL_SECRET). Prefer polymarket_pause / polymarket_start for clearer intent.", "inputSchema": {"type": "object", "properties": {"paused": {"type": "boolean", "description": "True to pause, False to resume"}, "updated_by": {"type": "string", "default": "hermes", "description": "Who triggered the change"}}, "required": ["paused"]}},
        {"name": "polymarket_pause", "description": "Pause the bot (stops opening new trades; requires CONTROL_SECRET).", "inputSchema": {"type": "object", "properties": {"updated_by": {"type": "string", "default": "hermes", "description": "Who triggered the change"}}}},
        {"name": "polymarket_start", "description": "Resume/start the bot after a pause (requires CONTROL_SECRET).", "inputSchema": {"type": "object", "properties": {"updated_by": {"type": "string", "default": "hermes", "description": "Who triggered the change"}}}},
    ]


def _call_tool(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    args = arguments or {}
    if name == "polymarket_state":
        return _get("/api/state")
    if name == "polymarket_pnl":
        state = _get("/api/state")
        if "error" in state:
            return state
        btc = state.get("assets", {}).get("BTC", {})
        return {
            "balance_usd": btc.get("positions", {}).get("simulated_balance"),
            "daily_pnl_usd": btc.get("risk", {}).get("daily_pnl"),
            "confidence_weight": btc.get("confidence_weight"),
            "circuit_breaker_triggered": btc.get("risk", {}).get("circuit_breaker_triggered"),
            "current_day": btc.get("risk", {}).get("current_day"),
        }
    if name == "polymarket_summary":
        state = _get("/api/state")
        calib = _get("/api/calibration", {"asset": "BTC"})
        drawdown = _get("/api/drawdown")
        if "error" in state:
            return state
        btc = state.get("assets", {}).get("BTC", {})
        return {
            "balance_usd": btc.get("positions", {}).get("simulated_balance"),
            "daily_pnl_usd": btc.get("risk", {}).get("daily_pnl"),
            "confidence_weight": btc.get("confidence_weight"),
            "daily_circuit_breaker_triggered": btc.get("risk", {}).get("circuit_breaker_triggered"),
            "drawdown_breaker_tripped": drawdown.get("tripped") if "error" not in drawdown else None,
            "calibration_maturity": calib.get("maturity") if "error" not in calib else None,
            "model_brier": calib.get("model_brier") if "error" not in calib else None,
            "market_brier": calib.get("market_brier") if "error" not in calib else None,
            "model_better_than_market": calib.get("model_better_than_market") if "error" not in calib else None,
            "recent_fills": state.get("fills", [])[:5],
        }
    if name == "polymarket_drawdown":
        return _get("/api/drawdown")
    if name == "polymarket_funnel":
        return _get("/api/funnel", {"asset": args.get("asset", "BTC")})
    if name == "polymarket_calibration":
        return _get("/api/calibration", {"asset": args.get("asset", "BTC")})
    if name == "polymarket_recent_trades":
        return _get("/api/recent_trades", {"asset": args.get("asset", "BTC"), "status": args.get("status", "EXECUTED"), "limit": str(args.get("limit", 20))})
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
    def polymarket_recent_trades(asset: str = "BTC", status: str = "EXECUTED", limit: int = 20) -> dict[str, Any]:
        """Get recent trades."""
        return _call_tool("polymarket_recent_trades", {"asset": asset, "status": status, "limit": limit})

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