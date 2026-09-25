"""Public read-only API consumed by the portfolio frontend."""

from __future__ import annotations

import time
from collections import deque
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import APIRouter, Depends, Path, Query, Request, Response
from psycopg_pool import PoolTimeout

from app import db
from app.config import (
    DIGESTS_DEFAULT_LIMIT,
    DIGESTS_MAX_LIMIT,
    HISTORY_DEFAULT_DAYS,
    HISTORY_MAX_DAYS,
    RATE_LIMIT_REQUESTS,
    RATE_LIMIT_WINDOW_SECONDS,
    get_settings,
)
from app.errors import ApiError
from app.schemas import (
    DigestItemOut,
    DigestOut,
    DigestPage,
    ErrorResponse,
    InterestHistory,
    InterestPoint,
    InterestSeries,
    Stats,
)

STATS_WINDOW_DAYS = 30
CACHE_HEADERS = {"Cache-Control": "public, max-age=60"}


# --- rate limiting ------------------------------------------------------------------


class RateLimiter:
    """Sliding-window limit per key, in memory (one instance, one process)."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self.hits: dict[str, deque[float]] = {}

    def check(self, key: str, now: float | None = None) -> float | None:
        """Record a hit; return seconds to wait if over the limit, else None."""
        now = time.monotonic() if now is None else now
        q = self.hits.setdefault(key, deque())
        while q and q[0] <= now - self.window:
            q.popleft()
        if len(q) >= self.limit:
            return max(1.0, q[0] + self.window - now)
        q.append(now)
        if len(self.hits) > 10_000:
            self.hits = {k: v for k, v in self.hits.items() if v and v[-1] > now - self.window}
        return None


limiter = RateLimiter(RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)


def client_ip(request: Request) -> str:
    """Render's proxy puts the client address first in X-Forwarded-For."""
    for header in ("true-client-ip", "x-forwarded-for"):
        value = request.headers.get(header)
        if value:
            return value.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def rate_limit(request: Request) -> None:
    wait = limiter.check(client_ip(request))
    if wait is not None:
        raise ApiError(
            429,
            f"Too many requests; limit is {RATE_LIMIT_REQUESTS} per {RATE_LIMIT_WINDOW_SECONDS}s",
            headers={"Retry-After": str(int(wait + 0.999))},
        )


ERRORS = {
    404: {"model": ErrorResponse, "description": "Not found"},
    422: {"model": ErrorResponse, "description": "Invalid parameters"},
    429: {"model": ErrorResponse, "description": "Rate limited"},
    503: {"model": ErrorResponse, "description": "Database unavailable"},
}

router = APIRouter(tags=["public"], dependencies=[Depends(rate_limit)], responses=ERRORS)


# --- helpers ------------------------------------------------------------------------


def _local(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    return ts.astimezone(get_settings().tz).replace(microsecond=0)


async def _fetch_digests(conn, where: str, params: dict, approved: bool, limit: int) -> list[DigestOut]:
    rows = await (
        await conn.execute(
            f"""
            select d.id, d.digest_date, d.sent_at, d.status
            from digests d
            where d.status in ('sent', 'quiet') {where}
              and (not %(approved)s or exists (
                    select 1 from digest_items di where di.digest_id = d.id and di.feedback = 'up'))
            order by d.id desc
            limit %(limit)s
            """,
            {**params, "approved": approved, "limit": limit},
        )
    ).fetchall()
    if not rows:
        return []
    items = await (
        await conn.execute(
            """
            select di.id, di.digest_id, di.headline, di.url, di.source_name, di.reason,
                   di.entity_name, di.feedback
            from digest_items di
            where di.digest_id = any(%s) and (not %s or di.feedback = 'up')
            order by di.digest_id desc, di.rank
            """,
            ([r["id"] for r in rows], approved),
        )
    ).fetchall()
    by_digest: dict[int, list[DigestItemOut]] = {}
    dates = {r["id"]: r["digest_date"] for r in rows}
    for it in items:
        by_digest.setdefault(it["digest_id"], []).append(
            DigestItemOut(
                id=it["id"],
                headline=it["headline"],
                url=it["url"],
                source=it["source_name"],
                reason=it["reason"],
                entity=it["entity_name"],
                digest_date=dates[it["digest_id"]],
                vote=it["feedback"],
            )
        )
    return [
        DigestOut(
            id=r["id"],
            date=r["digest_date"],
            sent_at=_local(r["sent_at"]),
            status=r["status"],
            items=by_digest.get(r["id"], []),
        )
        for r in rows
    ]


async def _conn():
    try:
        async with db.connection(timeout=5) as conn:
            yield conn
    except (db.DatabaseUnavailable, PoolTimeout, psycopg.OperationalError):
        raise ApiError(503, "Service temporarily unavailable") from None


# --- endpoints -----------------------------------------------------------------------


@router.get("/digests", response_model=DigestPage, summary="Recent digests, newest first")
async def list_digests(
    response: Response,
    approved: bool = Query(False, description="Only items voted 👍, and only digests that have one"),
    limit: int = Query(DIGESTS_DEFAULT_LIMIT, ge=1, le=DIGESTS_MAX_LIMIT, description="Digests per page"),
    before: int | None = Query(
        None, ge=1, le=2**62, description="Cursor: the next_before value from the previous page"
    ),
    conn=Depends(_conn),
) -> DigestPage:
    where = "and d.id < %(before)s" if before is not None else ""
    digests = await _fetch_digests(conn, where, {"before": before}, approved, limit + 1)
    more = len(digests) > limit
    digests = digests[:limit]
    response.headers.update(CACHE_HEADERS)
    return DigestPage(digests=digests, next_before=digests[-1].id if more else None)


@router.get("/digests/{digest_id}", response_model=DigestOut, summary="One digest")
async def get_digest(
    response: Response,
    digest_id: int = Path(ge=1, le=2**62),
    approved: bool = Query(False, description="Only items voted 👍"),
    conn=Depends(_conn),
) -> DigestOut:
    found = await _fetch_digests(conn, "and d.id = %(id)s", {"id": digest_id}, False, 1)
    if not found:
        raise ApiError(404, f"Digest {digest_id} not found")
    digest = found[0]
    if approved:
        digest.items = [i for i in digest.items if i.vote == "up"]
    response.headers.update(CACHE_HEADERS)
    return digest


@router.get("/interests/history", response_model=InterestHistory, summary="Interest weights over time")
async def interest_history(
    response: Response,
    days: int = Query(HISTORY_DEFAULT_DAYS, ge=1, le=HISTORY_MAX_DAYS, description="How many days back"),
    conn=Depends(_conn),
) -> InterestHistory:
    end = datetime.now(get_settings().tz).date()
    start = end - timedelta(days=days - 1)
    rows = await (
        await conn.execute(
            """
            select h.term, h.day, h.weight, i.origin, i.weight as current_weight
            from interest_history h
            left join interests i on lower(i.term) = lower(h.term)
            where h.day >= %s and h.day <= %s
            order by h.term, h.day
            """,
            (start, end),
        )
    ).fetchall()
    series: dict[str, InterestSeries] = {}
    for r in rows:
        s = series.get(r["term"])
        if s is None:
            s = series[r["term"]] = InterestSeries(
                term=r["term"],
                origin=r["origin"],
                current_weight=None if r["current_weight"] is None else round(float(r["current_weight"]), 4),
                points=[],
            )
        s.points.append(InterestPoint(date=r["day"], weight=round(float(r["weight"]), 4)))
    terms = sorted(series.values(), key=lambda s: (-(s.current_weight or -1), s.term.lower()))
    response.headers.update(CACHE_HEADERS)
    return InterestHistory(start=start, end=end, terms=terms)


@router.get("/stats", response_model=Stats, summary="Hit rate, quiet-day rate, collect reliability, missed reports")
async def stats(response: Response, conn=Depends(_conn)) -> Stats:
    d = await (
        await conn.execute(
            """
            select count(*) filter (where status = 'sent') as sent,
                   count(*) filter (where status = 'quiet') as quiet
            from digests
            """
        )
    ).fetchone()
    i = await (
        await conn.execute(
            """
            select count(*) as sent,
                   count(*) filter (where di.feedback = 'up') as up,
                   count(*) filter (where di.feedback = 'down') as down
            from digest_items di join digests d on d.id = di.digest_id
            where d.status = 'sent'
            """
        )
    ).fetchone()
    now = datetime.now(timezone.utc)
    last_full_hour = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    first = await (
        await conn.execute("select min(date_trunc('hour', started_at)) as h from runs where kind = 'collect'")
    ).fetchone()
    expected = ok = 0
    if first["h"] is not None:
        start = max(first["h"], last_full_hour - timedelta(days=STATS_WINDOW_DAYS) + timedelta(hours=1))
        expected = max(0, int((last_full_hour - start) / timedelta(hours=1)) + 1)
        ok = (
            await (
                await conn.execute(
                    """
                    select count(distinct date_trunc('hour', started_at)) as n from runs
                    where kind = 'collect' and status in ('success', 'partial')
                      and started_at >= %s and started_at < %s
                    """,
                    (start, last_full_hour + timedelta(hours=1)),
                )
            ).fetchone()
        )["n"]
    missed = (await (await conn.execute("select count(*) as n from missed")).fetchone())["n"]
    days_total = d["sent"] + d["quiet"]
    response.headers.update(CACHE_HEADERS)
    return Stats(
        window_days=STATS_WINDOW_DAYS,
        digests_sent=d["sent"],
        quiet_days=d["quiet"],
        quiet_day_rate=round(d["quiet"] / days_total, 4) if days_total else None,
        items_sent=i["sent"],
        items_voted_up=i["up"],
        items_voted_down=i["down"],
        hit_rate=round(i["up"] / i["sent"], 4) if i["sent"] else None,
        collect_hours_expected=expected,
        collect_hours_ok=ok,
        collect_reliability=round(ok / expected, 4) if expected else None,
        missed_reports=missed,
    )
