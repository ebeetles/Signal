"""Telegram Bot API client and message formatting.

The bot token is part of every API URL, so errors raised here never include
the URL or the underlying httpx exception text.
"""

from __future__ import annotations

import asyncio
import html
import logging

import httpx

from app.config import get_settings

log = logging.getLogger("signal.telegram")

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(Exception):
    """Safe to log: contains the method name and Telegram's description only."""


class Telegram:
    def __init__(self, token: str | None = None, client: httpx.AsyncClient | None = None):
        self.token = token or get_settings().telegram_bot_token
        self._client = client

    async def call(self, method: str, payload: dict) -> dict:
        if not self.token:
            raise TelegramError(f"{method}: TELEGRAM_BOT_TOKEN is not set")
        url = API.format(token=self.token, method=method)
        client = self._client or httpx.AsyncClient(timeout=15)
        try:
            for attempt in range(3):
                try:
                    resp = await client.post(url, json=payload)
                except httpx.HTTPError as exc:
                    if attempt == 2:
                        raise TelegramError(f"{method}: network error ({type(exc).__name__})") from None
                    await asyncio.sleep(1 + attempt)
                    continue
                try:
                    data = resp.json()
                except ValueError:
                    raise TelegramError(f"{method}: HTTP {resp.status_code}") from None
                if data.get("ok"):
                    return data.get("result") or {}
                retry = (data.get("parameters") or {}).get("retry_after")
                if resp.status_code == 429 and retry and attempt < 2:
                    await asyncio.sleep(min(float(retry), 10))
                    continue
                raise TelegramError(f"{method}: {data.get('description', resp.status_code)}")
            raise TelegramError(f"{method}: gave up")
        finally:
            if self._client is None:
                await client.aclose()

    async def send_message(self, chat_id: int, text: str, *, silent: bool = False, reply_markup: dict | None = None) -> dict:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_notification": silent,
            "link_preview_options": {"is_disabled": True},
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return await self.call("sendMessage", payload)

    async def answer_callback(self, callback_id: str, text: str) -> None:
        await self.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})

    async def edit_markup(self, chat_id: int, message_id: int, reply_markup: dict) -> None:
        await self.call(
            "editMessageReplyMarkup",
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup},
        )


# --- formatting -----------------------------------------------------------------

def esc(text: str) -> str:
    """Escape untrusted text for Telegram's HTML parse mode."""
    return html.escape(text or "", quote=True)


def header_text(n: int) -> str:
    return f"☀️ {n} thing{'s' if n != 1 else ''} today"


QUIET_TEXT = "Nothing big today."


def item_text(headline: str, url: str, reason: str, source: str) -> str:
    return f'<b><a href="{esc(url)}">{esc(headline)}</a></b>\n{esc(source)} · {esc(reason)}'


def vote_keyboard(digest_item_id: int, current: str | None = None) -> dict:
    up = "👍 ✓" if current == "up" else "👍"
    down = "👎 ✓" if current == "down" else "👎"
    return {
        "inline_keyboard": [
            [
                {"text": up, "callback_data": f"vote:{digest_item_id}:up"},
                {"text": down, "callback_data": f"vote:{digest_item_id}:down"},
            ]
        ]
    }
