"""Pydantic models. The public API models are the frontend contract:
renaming or removing a field here breaks tests/test_contract.py on purpose."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Accepted(BaseModel):
    status: Literal["accepted"]
    job: Literal["collect", "send"]


# --- Telegram webhook input (only the fields Signal reads) ---------------------

from pydantic import ConfigDict, Field, field_validator  # noqa: E402

from app.config import MAX_MISSED_CHARS, MAX_TERM_CHARS, MIN_WEIGHT, WEIGHT_CAP  # noqa: E402


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class TgUser(_Lenient):
    id: int


class TgChat(_Lenient):
    id: int


class TgMessage(_Lenient):
    message_id: int
    chat: TgChat
    from_: TgUser | None = Field(default=None, alias="from")
    text: str | None = None


class TgCallbackQuery(_Lenient):
    id: str
    from_: TgUser = Field(alias="from")
    message: TgMessage | None = None
    data: str | None = Field(default=None, max_length=64)


class TgUpdate(_Lenient):
    update_id: int
    message: TgMessage | None = None
    callback_query: TgCallbackQuery | None = None


class AddInterest(BaseModel):
    term: str = Field(min_length=1, max_length=MAX_TERM_CHARS)
    weight: float = Field(ge=MIN_WEIGHT, le=WEIGHT_CAP)

    @field_validator("term")
    @classmethod
    def _clean(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("term is empty")
        return v


class RemoveInterest(BaseModel):
    term: str = Field(min_length=1, max_length=MAX_TERM_CHARS)


class MissedReport(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_MISSED_CHARS)
