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
        {"name": "polymarket_drawdown", "description": "Check if the drawdown breaker is tripped.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "polymarket_funnel", "description": "Get funnel stats for an asset (phantom rate, blocked signals).", "inputSchema": {"type": "object", "properties": {"asset": {"type": "string", "default": "BTC", "description": "Asset symbol (BTC)"}, "window_hours": {"type": "integer", "default": 4, "description": "Window in hours"}, "baseline_hours": {"type": "integer", "default": 96, "description": "Baseline in hours"}}}},
        {"name": "polymarket_calibration", "description": "Get model calibration stats (Brier scores) for an asset.", "inputSchema": {"type": "object", "properties": {"asset": {"type": "string", "default": "BTC", "description": "Asset symbol"}}}},
        {"name": "polymarket_recent_trades", "description": "Get recent executed trades for an asset.", "inputSchema": {"type": "object", "properties": {"asset": {"type": "string", "default": "BTC", "description": "Asset symbol"}, "status": {"type": "string", "default": "EXECUTED", "description": "Trade status filter"}, "limit": {"type": "integer", "default": 20, "description": "Max trades to return"}}}},
        {"name": "polymarket_logs", "description": "Get recent log lines from the bot.", "inputSchema": {"type": "object", "properties": {"lines": {"type": "integer", "default": 40, "description": "Number of lines (1-200)"}}}},
        {"name": "polymarket_control", "description": "Pause or resume the bot (requires CONTROL_SECRET).", "inputSchema": {"type": "object", "properties": {"paused": {"type": "boolean", "description": "True to pause, False to resume"}, "updated_by": {"type": "string", "default": "hermes", "description": "Who triggered the change"}}, "required": ["paused"]}},
    ]


def _call_tool(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    args = arguments or {}
    if name == "polymarket_state":
        return _get("/api/state")
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

    log.info("polymarket MCP server starting — tools: %s", [t["name"] for t in _tools()])

    async def _run() -> None:
        try:
            await server.run_stdio_async()
        finally:
            pass

    asyncio.run(_run())


if __name__ == "__main__":
    main()