"""Send pipeline: score, select, store the digest, deliver it on Telegram.

Idempotent per local date: a digest that was sent (or was a quiet day) is
never sent again that day. A failed digest can be retried by calling
/send again the same day.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone

import psycopg
import yaml

from app import db
from app.config import SEND_LOCK_KEY, SOURCES_FILE, get_settings
from app.runs import expire_stale_runs, finish_run, record_skipped, start_run
from app.scoring import Scored, recent_spikes, score_all, select
from app.telegram import QUIET_TEXT, Telegram, TelegramError, header_text, item_text, vote_keyboard

log = logging.getLogger("signal.send")


def load_primary_domains(path=SOURCES_FILE) -> set[str]:
    data = yaml.safe_load(path.read_text()) or {}
    return {str(d).lower().removeprefix("www.") for d in data.get("primary_domains") or []}


async def snapshot_interests(conn: psycopg.AsyncConnection, day: date) -> int:
    cur = await conn.execute(
        """
        insert into interest_history (term, weight, day)
        select term, weight, %s from interests
        on conflict (term, day) do update set weight = excluded.weight, recorded_at = now()
        """,
        (day,),
    )
    return cur.rowcount


async def store_digest(conn: psycopg.AsyncConnection, digest_date: date, picks: list[Scored]) -> tuple[int, list[int]]:
    """Create (or reset a failed/pending) digest row and its items."""
    row = await (
        await conn.execute(
            """
            insert into digests (digest_date, status) values (%s, 'pending')
            on conflict (digest_date) do update set status = 'pending'
            returning id
            """,
            (digest_date,),
        )
    ).fetchone()
    digest_id = row["id"]
    await conn.execute("delete from digest_items where digest_id = %s", (digest_id,))
    ids = []
    for rank, s in enumerate(picks, start=1):
        best = s.best
        r = await (
            await conn.execute(
                """
                insert into digest_items (digest_id, entity_id, item_id, rank, score, spike, reason,
                                          headline, url, source_name, entity_name, matched_terms)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                returning id
                """,
                (digest_id, s.entity_id, best.id, rank, s.score, s.spike, s.reason, best.title,
                 best.link_url, best.display_source, s.name, s.matched),
            )
        ).fetchone()
        ids.append(r["id"])
    await conn.commit()
    return digest_id, ids


async def deliver(tg: Telegram, chat_id: int, picks: list[Scored], item_ids: list[int]) -> None:
    if not picks:
        await tg.send_message(chat_id, QUIET_TEXT)
        return
    await tg.send_message(chat_id, header_text(len(picks)))
    for s, item_id in zip(picks, item_ids):
        await asyncio.sleep(0.3)  # stay well under Telegram's per-chat rate limit
        await tg.send_message(
            chat_id,
            item_text(s.best.title, s.best.link_url, s.reason, s.best.display_source),
            silent=True,
            reply_markup=vote_keyboard(item_id),
        )


async def send_once(conn: psycopg.AsyncConnection, now: datetime, tg: Telegram) -> tuple[str, str | None, dict]:
    settings = get_settings()
    digest_date = now.astimezone(settings.tz).date()
    existing = await (
        await conn.execute("select id, status from digests where digest_date = %s", (digest_date,))
    ).fetchone()
    if existing and existing["status"] in ("sent", "quiet"):
        return "skipped", f"digest for {digest_date} already {existing['status']}", {}

    scored, _ = await score_all(conn, now, primary_domains=load_primary_domains())
    picks = select(scored, await recent_spikes(conn, digest_date))
    digest_id, item_ids = await store_digest(conn, digest_date, picks)
    stats = {
        "digest_id": digest_id,
        "date": str(digest_date),
        "candidates": len(scored),
        "picked": len(picks),
        "top": [{"entity": s.name, "score": round(s.score, 3), "spike": round(s.spike, 3)} for s in scored[:10]],
    }
    try:
        if settings.telegram_chat_id is None:
            raise TelegramError("TELEGRAM_CHAT_ID is not set")
        await deliver(tg, settings.telegram_chat_id, picks, item_ids)
    except TelegramError as exc:
        await conn.execute("update digests set status = 'failed' where id = %s", (digest_id,))
        await conn.commit()
        return "failed", str(exc), stats

    status = "sent" if picks else "quiet"
    await conn.execute(
        "update digests set status = %s, sent_at = %s where id = %s", (status, now, digest_id)
    )
    stats["interest_terms"] = await snapshot_interests(conn, digest_date)
    await conn.commit()
    return "success", None, stats


async def run_send(now: datetime | None = None, tg: Telegram | None = None) -> dict:
    """Entry point for POST /send's background task. Never raises."""
    now = now or datetime.now(timezone.utc)
    tg = tg or Telegram()
    try:
        async with db.advisory_lock(SEND_LOCK_KEY) as acquired:
            async with db.connection() as conn:
                if not acquired:
                    await record_skipped(conn, "send", "another send is running")
                    return {"status": "skipped"}
                await expire_stale_runs(conn, "send")
                run_id = await start_run(conn, "send")
                try:
                    status, error, stats = await send_once(conn, now, tg)
                except Exception as exc:
                    await conn.rollback()
                    log.exception("send failed")
                    await finish_run(conn, run_id, "failed", type(exc).__name__)
                    return {"status": "failed"}
                await finish_run(conn, run_id, status, error, stats)
                log.info("send %s: %s", status, error or stats.get("picked"))
                return {"status": status, "error": error, **stats}
    except Exception:
        log.exception("send could not start")
        return {"status": "failed"}
