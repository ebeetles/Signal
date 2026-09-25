"""Print the chat ids that have messaged your bot.

Usage (after sending your bot any message in Telegram):
    python -m scripts.get_chat_id

Reads TELEGRAM_BOT_TOKEN from the environment or .env. Works only while no
webhook is set (Telegram disables getUpdates once a webhook exists), so run
it before scripts/set_webhook.py.
"""

from __future__ import annotations

import sys

import httpx

from app.config import get_settings


def main() -> int:
    token = get_settings().telegram_bot_token
    if not token:
        print("TELEGRAM_BOT_TOKEN is not set (env or .env).")
        return 1
    resp = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15)
    data = resp.json()
    if not data.get("ok"):
        print(f"Telegram error: {data.get('description')}")
        return 1
    seen = {}
    for update in data.get("result", []):
        msg = update.get("message") or update.get("edited_message") or {}
        chat = msg.get("chat") or {}
        if "id" in chat:
            seen[chat["id"]] = chat.get("username") or chat.get("first_name") or chat.get("title")
    if not seen:
        print("No messages yet. Send your bot any message in Telegram, then run this again.")
        return 1
    for chat_id, who in seen.items():
        print(f"TELEGRAM_CHAT_ID={chat_id}   ({who})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
