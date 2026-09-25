"""Request authentication helpers. Comparisons are constant-time."""

from __future__ import annotations

import hmac

from fastapi import Request

from app.config import get_settings
from app.errors import ApiError


def require_cron_token(request: Request) -> None:
    expected = get_settings().cron_token
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if not expected or scheme.lower() != "bearer" or not hmac.compare_digest(token.strip(), expected):
        raise ApiError(401, "Missing or invalid bearer token", headers={"WWW-Authenticate": "Bearer"})


def telegram_secret_ok(request: Request) -> bool:
    expected = get_settings().telegram_webhook_secret
    got = request.headers.get("x-telegram-bot-api-secret-token", "")
    return bool(expected) and hmac.compare_digest(got, expected)
