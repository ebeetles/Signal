"""Pydantic models.

The public API models are the frontend contract: tests/test_contract.py
asserts their exact JSON shape, so renaming or removing a field fails it.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import MAX_MISSED_CHARS, MAX_TERM_CHARS, MIN_WEIGHT, WEIGHT_CAP

# --- public API (frontend contract) -----------------------------------------------


class DigestItemOut(BaseModel):
    id: int = Field(description="Stable id of this digest item")
    headline: str = Field(description="Headline of the chosen link (untrusted text: render as text)")
    url: str = Field(description="Best link for the item (http/https)")
    source: str = Field(description="Where the link is from, e.g. 'anthropic.com' or 'Simon Willison'")
    reason: str = Field(description="Why it was picked, e.g. '5 sources in 12h (usually 0), matches: Claude'")
    entity: str = Field(description="The thing that spiked, e.g. 'Claude Opus 5.5'")
    digest_date: dt.date = Field(description="Local date (America/New_York) of the morning digest")
    vote: Literal["up", "down"] | None = Field(description="Elwin's vote; always 'up' when approved=true")


class DigestOut(BaseModel):
    id: int
    date: dt.date = Field(description="Local date (America/New_York) of the digest")
    sent_at: dt.datetime | None = Field(description="ISO 8601 with UTC offset")
    status: Literal["sent", "quiet"]
    items: list[DigestItemOut]


class DigestPage(BaseModel):
    digests: list[DigestOut] = Field(description="Newest first")
    next_before: int | None = Field(
        description="Pass as ?before= to get the next (older) page; null when there are no more"
    )


class InterestPoint(BaseModel):
    date: dt.date
    weight: float


class InterestSeries(BaseModel):
    term: str
    origin: Literal["manual", "learned"] | None = Field(description="null if the term was removed")
    current_weight: float | None = Field(description="null if the term was removed")
    points: list[InterestPoint] = Field(description="One point per day with a snapshot, oldest first")


class InterestHistory(BaseModel):
    start: dt.date
    end: dt.date
    terms: list[InterestSeries]


class Stats(BaseModel):
    window_days: int = Field(description="Collect reliability is measured over at most this many days")
    digests_sent: int
    quiet_days: int
    quiet_day_rate: float | None = Field(description="quiet / (sent + quiet); null before the first digest")
    items_sent: int
    items_voted_up: int
    items_voted_down: int
    hit_rate: float | None = Field(description="Share of sent items voted up; null before any item was sent")
    collect_hours_expected: int
    collect_hours_ok: int
    collect_reliability: float | None = Field(
        description="Share of expected hourly collect runs that succeeded; null before the first run"
    )
    missed_reports: int


class ErrorDetail(BaseModel):
    field: str | None
    message: str


class ErrorInfo(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] | None = None


class ErrorResponse(BaseModel):
    error: ErrorInfo


class Health(BaseModel):
    status: Literal["ok"]


class Accepted(BaseModel):
    status: Literal["accepted"]
    job: Literal["collect", "send"]


# --- Telegram webhook input (only the fields Signal reads) -----------------------------


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


# --- bot command input ---------------------------------------------------------------


class AddInterest(BaseModel):
    term: str = Field(min_length=1, max_length=MAX_TERM_CHARS)
    weight: float = Field(ge=MIN_WEIGHT, le=WEIGHT_CAP)

    @field_validator("term", mode="before")
    @classmethod
    def _clean(cls, v: str) -> str:
        return " ".join(str(v).split())


class RemoveInterest(BaseModel):
    term: str = Field(min_length=1, max_length=MAX_TERM_CHARS)


class MissedReport(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_MISSED_CHARS)
