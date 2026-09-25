"""Register the Telegram webhook (with its secret token) and the command menu.

Usage:
    python -m scripts.set_webhook https://<your-service>.onrender.com
    python -m scripts.set_webhook            # uses RENDER_URL from env/.env
    python -m scripts.set_webhook --info     # show current webhook status

Reads TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET from the environment or
.env. The same TELEGRAM_WEBHOOK_SECRET must be set on Render.
"""

from __future__ import annotations

import os
import sys

import httpx

from app.config import get_settings

COMMANDS = [
    {"command": "interests", "description": "List interest terms and weights"},
    {"command": "add", "description": "/add <term> [weight 0-5]"},
    {"command": "remove", "description": "/remove <term>"},
    {"command": "missed", "description": "/missed <what Signal failed to surface>"},
    {"command": "help", "description": "Show commands"},
]


def main(argv: list[str]) -> int:
    s = get_settings()
    if not s.telegram_bot_token or not s.telegram_webhook_secret:
        print("TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET must be set (env or .env).")
        return 1
    api = f"https://api.telegram.org/bot{s.telegram_bot_token}"

    if "--info" in argv:
        info = httpx.get(f"{api}/getWebhookInfo", timeout=15).json().get("result", {})
        for key in ("url", "pending_update_count", "last_error_date", "last_error_message"):
            print(f"{key}: {info.get(key)}")
        return 0

    base = next((a for a in argv if a.startswith("https://")), None) or os.environ.get("RENDER_URL")
    if not base or not base.startswith("https://"):
        print("Pass the https:// Render URL, or set RENDER_URL.")
        return 1
    url = base.rstrip("/") + "/telegram/webhook"
    resp = httpx.post(
        f"{api}/setWebhook",
        json={
            "url": url,
            "secret_token": s.telegram_webhook_secret,
            "allowed_updates": ["message", "callback_query"],
        },
        timeout=15,
    ).json()
    print(f"setWebhook -> {url}: {resp.get('description')}")
    cmds = httpx.post(f"{api}/setMyCommands", json={"commands": COMMANDS}, timeout=15).json()
    print(f"setMyCommands: {'ok' if cmds.get('ok') else cmds.get('description')}")
    return 0 if resp.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
