"""
Telegram command/query interface for the running bot.

Runs as a daemon thread inside the same process as the trading bot and the
dashboard (see railway_entrypoint.py), long-polling Telegram's getUpdates and
answering questions / executing commands by calling the dashboard's own HTTP
API on localhost -- the exact same data a human would see on the web
dashboard, no separate code path to drift out of sync.

Access control: only TELEGRAM_CHAT_ID (from config) is ever acted on. Every
other chat is silently ignored (logged at DEBUG only). No confirmation step
for actions -- pause/resume executes immediately, per explicit instruction.

Safe by construction: any exception in a single update is caught and logged;
it never kills the polling loop, and it never touches the trading loop
directly (it only calls the dashboard's HTTP endpoints, same as the web UI).
"""
import os
import time
import traceback

import requests
from loguru import logger

from config import config

PORT = int(os.getenv("PORT", "5000"))
BASE_URL = f"http://127.0.0.1:{PORT}"
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

HELP_TEXT = (
    "Commands:\n"
    "  status / what is happening - full state summary\n"
    "  pnl - per-asset + portfolio daily PnL\n"
    "  balance - per-asset paper cash balance\n"
    "  breaker / circuit breaker - circuit breaker status\n"
    "  drawdown - portfolio drawdown breaker status\n"
    "  funnel - signal funnel counts (executed/blocked/no-signal)\n"
    "  calibration - model vs market Brier score\n"
    "  trades / recent trades - recent trade outcomes\n"
    "  logs - last 30 log lines\n"
    "  pause - pause new trade entries\n"
    "  resume - resume trading\n"
)


def _tg_call(method, **params):
    url = TELEGRAM_API.format(token=config.telegram_bot_token, method=method)
    resp = requests.post(url, json=params, timeout=35)
    resp.raise_for_status()
    return resp.json()


def _send(chat_id, text):
    try:
        _tg_call("sendMessage", chat_id=chat_id, text=text[:4000], disable_web_page_preview=True)
    except Exception as e:
        logger.warning(f"ChatBot: failed to send Telegram reply: {type(e).__name__}: {e}")


def _get(path, **params):
    r = requests.get(f"{BASE_URL}{path}", params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def _post_control(paused: bool):
    if not config.control_secret:
        return {"error": "CONTROL_SECRET is not set on the server -- pause/resume is disabled."}
    r = requests.post(
        f"{BASE_URL}/api/control",
        json={"paused": paused, "updated_by": "telegram"},
        headers={"X-Control-Secret": config.control_secret},
        timeout=15,
    )
    try:
        return r.json()
    except Exception:
        return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}


def _fmt_money(v):
    try:
        return f"${float(v):+.2f}"
    except Exception:
        return str(v)


def _handle_status():
    state = _get("/api/state")
    lines = []
    for asset, a in state.get("assets", {}).items():
        risk = a.get("risk", {})
        pos = a.get("positions", {})
        breaker = "TRIPPED" if risk.get("circuit_breaker_triggered") else "ok"
        lines.append(
            f"[{asset}] cash={_fmt_money(pos.get('simulated_balance', 0))[1:]} "
            f"day_pnl={_fmt_money(risk.get('daily_pnl', 0))} "
            f"breaker={breaker} open_pos={len(pos.get('positions', []))}"
        )
    portfolio = state.get("portfolio", {})
    lines.append(
        f"[PORTFOLIO] day_pnl={_fmt_money(portfolio.get('daily_pnl', 0))} "
        f"breaker={'TRIPPED' if portfolio.get('circuit_breaker_triggered') else 'ok'}"
    )
    return "\n".join(lines)


def _handle_pnl():
    state = _get("/api/state")
    lines = []
    for asset, a in state.get("assets", {}).items():
        lines.append(f"{asset}: {_fmt_money(a.get('risk', {}).get('daily_pnl', 0))}")
    lines.append(f"Portfolio: {_fmt_money(state.get('portfolio', {}).get('daily_pnl', 0))}")
    return "\n".join(lines)


def _handle_balance():
    state = _get("/api/state")
    lines = []
    for asset, a in state.get("assets", {}).items():
        bal = a.get("positions", {}).get("simulated_balance", 0)
        lines.append(f"{asset}: ${float(bal):.2f}")
    return "\n".join(lines)


def _handle_breaker():
    state = _get("/api/state")
    lines = []
    for asset, a in state.get("assets", {}).items():
        risk = a.get("risk", {})
        lines.append(f"{asset}: {'TRIPPED' if risk.get('circuit_breaker_triggered') else 'ok'}")
    portfolio = state.get("portfolio", {})
    lines.append(f"Portfolio: {'TRIPPED' if portfolio.get('circuit_breaker_triggered') else 'ok'}")
    return "\n".join(lines)


def _handle_drawdown():
    d = _get("/api/drawdown")
    return "\n".join(f"{k}: {v}" for k, v in d.items())


def _handle_funnel():
    out = []
    for asset in config.target_assets:
        d = _get("/api/funnel", asset=asset)
        out.append(
            f"[{asset}] last {d.get('window_hours')}h: {d.get('window_counts')}"
        )
    return "\n".join(out)


def _handle_calibration():
    out = []
    for asset in config.target_assets:
        d = _get("/api/calibration", asset=asset)
        beats = "beats market" if d.get("model_better_than_market") else "worse than market"
        out.append(
            f"[{asset}] {d.get('resolved_windows')} windows / {d.get('days')}d -- "
            f"model_brier={d.get('model_brier')} market_brier={d.get('market_brier')} ({beats})"
        )
    return "\n".join(out)


def _handle_trades():
    out = []
    for asset in config.target_assets:
        d = _get("/api/recent_trades", asset=asset)
        out.append(f"[{asset}] {d}")
    text = "\n".join(out)
    return text[:3800]


def _handle_logs():
    d = _get("/api/logs", lines=30)
    lines = d.get("lines", [])
    if not lines:
        return "(no log lines available)"
    return "\n".join(lines)[-3800:]


ROUTES = [
    (("status", "what is happening", "what's happening", "whats happening"), _handle_status),
    (("pnl", "what is btc pnl today", "pnl today"), _handle_pnl),
    (("balance", "cash"), _handle_balance),
    (("breaker", "circuit breaker", "is the circuit breaker tripped"), _handle_breaker),
    (("drawdown",), _handle_drawdown),
    (("funnel",), _handle_funnel),
    (("calibration",), _handle_calibration),
    (("trades", "recent trades"), _handle_trades),
    (("logs",), _handle_logs),
]


def _route(text: str) -> str:
    t = text.strip().lower()
    if t in ("pause",):
        r = _post_control(True)
        return f"Paused: {r}" if "error" not in r else f"Error: {r['error']}"
    if t in ("resume",):
        r = _post_control(False)
        return f"Resumed: {r}" if "error" not in r else f"Error: {r['error']}"
    if t in ("help", "?", "commands"):
        return HELP_TEXT
    for keys, handler in ROUTES:
        if t in keys or any(k in t for k in keys):
            try:
                return handler()
            except Exception as e:
                logger.warning(f"ChatBot: handler failed for '{text}': {type(e).__name__}: {e}")
                return f"Error fetching data: {type(e).__name__}: {e}"
    return "Didn't recognize that.\n\n" + HELP_TEXT


def run():
    if not config.telegram_bot_token or not config.telegram_chat_id:
        logger.info("ChatBot: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set -- chat interface disabled.")
        return

    allowed_chat_id = str(config.telegram_chat_id)
    logger.info(f"ChatBot: Telegram long-polling started (allowed_chat_id={allowed_chat_id}).")

    offset = None
    while True:
        try:
            params = {"timeout": 25}
            if offset is not None:
                params["offset"] = offset
            resp = _tg_call("getUpdates", **params)
            for update in resp.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message") or update.get("edited_message")
                if not msg:
                    continue
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "")
                if chat_id != allowed_chat_id:
                    logger.debug(f"ChatBot: ignoring message from unauthorized chat_id={chat_id}")
                    continue
                if not text:
                    continue
                logger.info(f"ChatBot: received '{text}' from allowed chat")
                try:
                    reply = _route(text)
                except Exception as e:
                    reply = f"Internal error: {type(e).__name__}: {e}"
                    logger.warning(f"ChatBot: {traceback.format_exc()}")
                _send(chat_id, reply)
        except requests.exceptions.RequestException as e:
            logger.warning(f"ChatBot: Telegram poll error: {type(e).__name__}: {e}")
            time.sleep(5)
        except Exception as e:
            logger.warning(f"ChatBot: unexpected error in poll loop: {type(e).__name__}: {e}")
            time.sleep(5)
