"""
Lightweight sidecar liveness monitor for cloud Hermes (runs in the same
container as the gateway, launched detached from cont-init.d/03-start-healthcheck
so it survives independently of s6-rc's own supervision tree).

Every CHECK_INTERVAL_SEC, does a read-only liveness check against both
platform APIs using the same token env vars Hermes itself reads
(TELEGRAM_BOT_TOKEN, DISCORD_BOT_TOKEN -- see gateway/config.py's
PLATFORM_TOKEN_ENV_NAMES):
  - Telegram: getMe (does not consume updates, so it can never trigger the
    "polling conflict" error a second getUpdates caller would cause).
  - Discord: GET /users/@me (proves the bot token itself is valid and
    Discord's REST API is reachable; it does not prove the gateway
    websocket is currently connected, since that needs presence data this
    check deliberately avoids requesting).

Sends a Telegram message to TELEGRAM_CHAT_ID only on a state TRANSITION
(healthy -> unhealthy, or back), not on every check, to avoid alert spam.
If Telegram itself is the platform that just went unhealthy, there's no
push channel left to alert through -- this logs CRITICAL to stdout instead,
which Railway's log stream still captures.
"""
import json
import os
import time
import urllib.error
import urllib.request

CHECK_INTERVAL_SEC = 600
TIMEOUT_SEC = 15

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")


def log(level: str, msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {level:<8} healthcheck: {msg}", flush=True)


def check_telegram() -> tuple[bool, str]:
    if not TELEGRAM_BOT_TOKEN:
        return True, "TELEGRAM_BOT_TOKEN not set -- skipping (platform likely disabled)"
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SEC) as resp:
            data = json.loads(resp.read().decode())
        if data.get("ok"):
            return True, "getMe OK"
        return False, f"getMe returned ok=false: {data}"
    except Exception as e:
        return False, f"getMe failed: {e}"


def check_discord() -> tuple[bool, str]:
    if not DISCORD_BOT_TOKEN:
        return True, "DISCORD_BOT_TOKEN not set -- skipping (platform likely disabled)"
    # Discord's API documentation requires a descriptive User-Agent on all
    # requests; omitting one (urllib's default is a bare "Python-urllib/x.y")
    # gets 403'd by Discord's edge as bot-protection, unrelated to whether
    # the token itself is valid -- confirmed live: this returned HTTP 403
    # here while Discord was simultaneously and verifiably connected and
    # processing real sessions.
    req = urllib.request.Request(
        "https://discord.com/api/v10/users/@me",
        headers={
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "User-Agent": "HermesHealthcheck (https://github.com/ryz-ryx/polymarket-bot, 1.0)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            data = json.loads(resp.read().decode())
        return True, f"users/@me OK ({data.get('username', '?')})"
    except urllib.error.HTTPError as e:
        return False, f"users/@me HTTP {e.code}"
    except Exception as e:
        return False, f"users/@me failed: {e}"


def send_telegram_alert(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN:
        log("CRITICAL", f"cannot alert (no TELEGRAM_BOT_TOKEN): {text}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    body = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": text}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=TIMEOUT_SEC)
    except Exception as e:
        log("CRITICAL", f"failed to send alert ({e}): {text}")


def main() -> None:
    log("INFO", "Hermes platform healthcheck sidecar started "
                f"(interval={CHECK_INTERVAL_SEC}s)")
    # Baseline assumed healthy (not "unknown") so a failure present from the
    # very first check counts as a transition and actually alerts -- the
    # opposite assumption would silently swallow any failure that was
    # already there at container boot, only ever reporting later changes.
    last_state: dict[str, bool] = {"telegram": True, "discord": True}

    while True:
        for name, check_fn in (("telegram", check_telegram), ("discord", check_discord)):
            try:
                healthy, detail = check_fn()
            except Exception as e:
                healthy, detail = False, f"check crashed: {e}"

            prev = last_state[name]
            if healthy:
                log("INFO", f"{name}: healthy ({detail})")
            else:
                log("WARNING", f"{name}: UNHEALTHY ({detail})")

            if prev != healthy:
                if healthy:
                    msg = f"[Hermes monitor] {name.capitalize()} recovered: {detail}"
                    if name == "telegram":
                        send_telegram_alert(msg)
                    else:
                        send_telegram_alert(msg)
                else:
                    msg = f"[Hermes monitor] {name.capitalize()} went UNHEALTHY: {detail}"
                    if name == "telegram":
                        log("CRITICAL", f"Telegram itself is down -- no push channel available: {detail}")
                    else:
                        send_telegram_alert(msg)
            last_state[name] = healthy

        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()
