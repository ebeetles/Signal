"""Telegram webhook handling: votes, weight learning, and interest commands.

Learning: a vote moves every *learned* interest term the item matched by
LEARNING_RATE (up) or -LEARNING_RATE (down), clamped to [MIN_WEIGHT,
WEIGHT_CAP]. Changing a vote applies the difference (up -> down moves
-2 * rate), and repeating a vote changes nothing. A first thumbs up also
adds the item's entity (without its version, e.g. "Claude Opus") as a
learned term at LEARNED_INITIAL_WEIGHT, if there's room. Manual terms (from
/add) are never changed by learning.
"""

from __future__ import annotations

import logging
import re

import psycopg
from pydantic import ValidationError

from app import db
from app.config import (
    DEFAULT_TERM_WEIGHT,
    LEARNED_INITIAL_WEIGHT,
    LEARNING_RATE,
    MAX_TERM_CHARS,
    MAX_TERMS,
    MIN_WEIGHT,
    WEIGHT_CAP,
    get_settings,
)
from app.extract import base_name
from app.schemas import AddInterest, MissedReport, RemoveInterest, TgCallbackQuery, TgMessage, TgUpdate
from app.telegram import Telegram, TelegramError, esc, vote_keyboard

log = logging.getLogger("signal.feedback")

VOTE_DATA = re.compile(r"^vote:(\d{1,18}):(up|down)$")
_VALUE = {"up": 1, "down": -1, None: 0}

HELP = (
    "<b>Signal</b> commands\n"
    "/interests: list terms and weights\n"
    "/add &lt;term&gt; [weight 0-5]: add or update a term (default weight 1)\n"
    "/remove &lt;term&gt;: remove a term\n"
    "/missed &lt;text&gt;: report something Signal should have surfaced"
)


# --- votes and learning ------------------------------------------------------------------


async def apply_learning(
    conn: psycopg.AsyncConnection, matched: list[str], entity_name: str, old: str | None, new: str
) -> list[str]:
    """Adjust learned weights for a vote change. Returns the terms touched."""
    delta = _VALUE[new] - _VALUE[old]
    if delta == 0:
        return []
    touched: list[str] = []
    entity_term = base_name(entity_name)[:MAX_TERM_CHARS].strip()
    terms = {t.lower() for t in matched} | ({entity_term.lower()} if entity_term else set())

    created = False
    if new == "up" and old != "up" and entity_term:
        exists = await (
            await conn.execute("select 1 from interests where lower(term) = lower(%s)", (entity_term,))
        ).fetchone()
        count = (await (await conn.execute("select count(*) as n from interests")).fetchone())["n"]
        if not exists and count < MAX_TERMS:
            await conn.execute(
                "insert into interests (term, weight, origin) values (%s, %s, 'learned')",
                (entity_term, LEARNED_INITIAL_WEIGHT),
            )
            touched.append(entity_term)
            created = True

    rows = await (
        await conn.execute(
            """
            update interests
               set weight = least(%(cap)s, greatest(%(min)s, weight + %(step)s)), updated_at = now()
             where origin = 'learned' and lower(term) = any(%(terms)s)
               and not (%(skip)s::text is not null and lower(term) = lower(%(skip)s::text))
            returning term
            """,
            {
                "cap": WEIGHT_CAP,
                "min": MIN_WEIGHT,
                "step": LEARNING_RATE * delta,
                "terms": sorted(terms),
                "skip": entity_term if created else None,
            },
        )
    ).fetchall()
    touched.extend(r["term"] for r in rows)
    return touched


async def record_vote(conn: psycopg.AsyncConnection, digest_item_id: int, vote: str) -> dict | None:
    row = await (
        await conn.execute(
            "select id, feedback, matched_terms, entity_name from digest_items where id = %s for update",
            (digest_item_id,),
        )
    ).fetchone()
    if row is None:
        return None
    old = row["feedback"]
    await conn.execute(
        "update digest_items set feedback = %s, feedback_at = now() where id = %s", (vote, digest_item_id)
    )
    touched = await apply_learning(conn, list(row["matched_terms"] or []), row["entity_name"], old, vote)
    await conn.commit()
    return {"old": old, "new": vote, "touched": touched}


async def handle_callback(cq: TgCallbackQuery, tg: Telegram) -> None:
    m = VOTE_DATA.match(cq.data or "")
    if not m:
        await tg.answer_callback(cq.id, "Unknown button")
        return
    item_id, vote = int(m.group(1)), m.group(2)
    async with db.connection() as conn:
        result = await record_vote(conn, item_id, vote)
    if result is None:
        await tg.answer_callback(cq.id, "That item no longer exists")
        return
    emoji = "👍" if vote == "up" else "👎"
    await tg.answer_callback(cq.id, f"Already {emoji}" if result["old"] == vote else f"Saved {emoji}")
    if cq.message and result["old"] != vote:
        try:
            await tg.edit_markup(cq.message.chat.id, cq.message.message_id, vote_keyboard(item_id, vote))
        except TelegramError as exc:  # cosmetic only
            log.info("could not update buttons: %s", exc)


# --- commands -------------------------------------------------------------------------------


def parse_command(text: str) -> tuple[str, str]:
    """'/add@signal_bot Claude Code 2' -> ('add', 'Claude Code 2')."""
    head, _, rest = text.strip().partition(" ")
    cmd = head[1:].split("@", 1)[0].lower() if head.startswith("/") else ""
    return cmd, rest.strip()


def parse_add(args: str) -> AddInterest:
    parts = args.rsplit(" ", 1)
    weight = DEFAULT_TERM_WEIGHT
    term = args
    if len(parts) == 2:
        try:
            weight = float(parts[1].replace(",", "."))
            term = parts[0]
        except ValueError:
            pass
    return AddInterest(term=term, weight=weight)


def _fmt_weight(w: float) -> str:
    return f"{w:.2f}".rstrip("0").rstrip(".")


async def cmd_add(conn, args: str) -> str:
    try:
        req = parse_add(args)
    except ValidationError:
        return f"Usage: /add &lt;term&gt; [weight]. Term 1-{MAX_TERM_CHARS} characters, weight {_fmt_weight(MIN_WEIGHT)}-{_fmt_weight(WEIGHT_CAP)}."
    updated = await (
        await conn.execute(
            "update interests set weight = %s, origin = 'manual', updated_at = now() "
            "where lower(term) = lower(%s) returning term",
            (req.weight, req.term),
        )
    ).fetchone()
    if updated:
        await conn.commit()
        return f"Updated <b>{esc(updated['term'])}</b> → {_fmt_weight(req.weight)} (manual)"
    count = (await (await conn.execute("select count(*) as n from interests")).fetchone())["n"]
    if count >= MAX_TERMS:
        return f"You already have {MAX_TERMS} terms. /remove one first."
    await conn.execute(
        "insert into interests (term, weight, origin) values (%s, %s, 'manual')", (req.term, req.weight)
    )
    await conn.commit()
    return f"Added <b>{esc(req.term)}</b> ({_fmt_weight(req.weight)})"


async def cmd_remove(conn, args: str) -> str:
    try:
        req = RemoveInterest(term=" ".join(args.split()))
    except ValidationError:
        return "Usage: /remove &lt;term&gt;"
    row = await (
        await conn.execute("delete from interests where lower(term) = lower(%s) returning term", (req.term,))
    ).fetchone()
    await conn.commit()
    return f"Removed <b>{esc(row['term'])}</b>" if row else f"No term named <b>{esc(req.term)}</b>. See /interests."


async def cmd_interests(conn) -> str:
    rows = await (
        await conn.execute("select term, weight, origin from interests order by weight desc, lower(term)")
    ).fetchall()
    if not rows:
        return "No interests yet. Add one with /add &lt;term&gt; [weight]."
    lines = [f"{esc(r['term'])}: {_fmt_weight(r['weight'])}" + (" (learned)" if r["origin"] == "learned" else "")
             for r in rows]
    return f"<b>Interests</b> ({len(rows)}/{MAX_TERMS})\n" + "\n".join(lines)


async def cmd_missed(conn, args: str) -> str:
    try:
        req = MissedReport(text=args.strip())
    except ValidationError:
        return "Usage: /missed &lt;what Signal should have surfaced&gt; (up to 500 characters)"
    await conn.execute("insert into missed (text) values (%s)", (req.text,))
    n = (await (await conn.execute("select count(*) as n from missed")).fetchone())["n"]
    await conn.commit()
    return f"Noted. {n} missed report{'s' if n != 1 else ''} so far."


async def handle_message(msg: TgMessage, tg: Telegram) -> None:
    cmd, args = parse_command(msg.text or "")
    async with db.connection() as conn:
        if cmd == "add":
            reply = await cmd_add(conn, args)
        elif cmd == "remove":
            reply = await cmd_remove(conn, args)
        elif cmd == "interests":
            reply = await cmd_interests(conn)
        elif cmd == "missed":
            reply = await cmd_missed(conn, args)
        else:
            reply = HELP
    await tg.send_message(msg.chat.id, reply)


async def handle_update(update: TgUpdate, tg: Telegram | None = None) -> str:
    """Process one update. Returns what happened (for logs and tests)."""
    tg = tg or Telegram()
    chat_id = get_settings().telegram_chat_id
    if update.callback_query is not None:
        cq = update.callback_query
        if chat_id is None or cq.message is None or cq.message.chat.id != chat_id:
            return "ignored"
        await handle_callback(cq, tg)
        return "vote"
    if update.message is not None:
        if chat_id is None or update.message.chat.id != chat_id or not update.message.text:
            return "ignored"
        await handle_message(update.message, tg)
        return "command"
    return "ignored"
