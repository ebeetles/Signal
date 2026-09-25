"""Collect pipeline: fetch sources, store new items, extract entities, roll up counts.

Idempotent: item URLs are unique, mentions and counts are recomputed from
stored rows, so running twice over the same feeds adds nothing.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

import httpx
import psycopg
import yaml

from app import db
from app.config import (
    ALIASES_FILE,
    COLLECT_LOCK_KEY,
    HTTP_TIMEOUT_SECONDS,
    RETENTION_DAYS,
    SOURCES_FILE,
    USER_AGENT,
)
from app.extract import AliasTable, extract
from app.runs import expire_stale_runs, finish_run, record_skipped, start_run
from app.sources import FETCHERS
from app.sources.base import FetchContext, FetchedItem, SourceError, SourceRow

log = logging.getLogger("signal.collect")

Fetcher = Callable[[httpx.AsyncClient, SourceRow, FetchContext], Awaitable[list[FetchedItem]]]
SOURCE_TIMEOUT_SECONDS = 90


# --- seeds -------------------------------------------------------------------


def load_sources_file(path=SOURCES_FILE) -> list[dict]:
    data = yaml.safe_load(path.read_text()) or {}
    out = []
    for raw in data.get("sources") or []:
        weight = float(raw.get("independence_weight", 1.0))
        if not raw.get("name") or raw.get("type") not in FETCHERS or not raw.get("url") or not 0 < weight <= 5:
            log.warning("skipping invalid source entry: %r", raw.get("name"))
            continue
        out.append(
            {
                "name": str(raw["name"]).strip(),
                "type": raw["type"],
                "url": str(raw["url"]).strip(),
                "independence_weight": weight,
                "is_primary": bool(raw.get("primary", False)),
                "origin": raw.get("origin") or None,
                "title_prefix": raw.get("title_prefix") or None,
                "active": bool(raw.get("active", True)),
            }
        )
    return out


def load_aliases_file(path=ALIASES_FILE) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    return {str(k): [str(a) for a in (v or [])] for k, v in (data.get("aliases") or {}).items()}


async def sync_sources(conn: psycopg.AsyncConnection, entries: list[dict]) -> None:
    for e in entries:
        await conn.execute(
            """
            insert into sources (name, type, url, independence_weight, is_primary, origin, title_prefix, active)
            values (%(name)s, %(type)s, %(url)s, %(independence_weight)s, %(is_primary)s, %(origin)s,
                    %(title_prefix)s, %(active)s)
            on conflict (name) do update set
                type = excluded.type, url = excluded.url,
                independence_weight = excluded.independence_weight,
                is_primary = excluded.is_primary, origin = excluded.origin,
                title_prefix = excluded.title_prefix, active = excluded.active,
                updated_at = now()
            """,
            e,
        )
    names = [e["name"] for e in entries]
    await conn.execute(
        "update sources set active = false, updated_at = now() where active and not (name = any(%s))",
        (names,),
    )
    await conn.commit()


async def sync_aliases(conn: psycopg.AsyncConnection, mapping: dict[str, list[str]]) -> AliasTable:
    """Write file aliases onto entities, then build the table from the DB
    (so aliases added directly in SQL work too)."""
    from app.extract import canonical_key_for_phrase, normalize_phrase

    for canonical, aliases in mapping.items():
        key = canonical_key_for_phrase(canonical)
        if not key:
            continue
        normalized = sorted({normalize_phrase(a) for a in aliases if normalize_phrase(a)} | {normalize_phrase(canonical)})
        await conn.execute(
            """
            insert into entities (name, norm, aliases) values (%s, %s, %s)
            on conflict (norm) do update set aliases = (
                select array(select distinct unnest(entities.aliases || excluded.aliases) order by 1))
            """,
            (canonical, key, normalized),
        )
    await conn.commit()
    rows = await (
        await conn.execute("select norm, aliases from entities where cardinality(aliases) > 0")
    ).fetchall()
    table = AliasTable()
    for row in rows:
        for alias in row["aliases"]:
            table.add(alias, row["norm"])
    return table


# --- fetching -----------------------------------------------------------------


async def load_active_sources(conn: psycopg.AsyncConnection) -> list[SourceRow]:
    rows = await (
        await conn.execute(
            "select id, name, type, url, independence_weight, is_primary, origin, title_prefix "
            "from sources where active order by id"
        )
    ).fetchall()
    return [SourceRow(**r) for r in rows]


async def fetch_all(
    sources: list[SourceRow], fetchers: dict[str, Fetcher], ctx: FetchContext
) -> list[tuple[SourceRow, list[FetchedItem], str | None]]:
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=HTTP_TIMEOUT_SECONDS,
        follow_redirects=True,
        limits=httpx.Limits(max_connections=20),
    ) as client:

        async def one(source: SourceRow):
            fetcher = fetchers.get(source.type)
            if fetcher is None:
                return source, [], f"no fetcher for type {source.type!r}"
            try:
                items = await asyncio.wait_for(fetcher(client, source, ctx), SOURCE_TIMEOUT_SECONDS)
                return source, items, None
            except SourceError as exc:
                return source, [], str(exc)
            except asyncio.TimeoutError:
                return source, [], "timed out"
            except Exception as exc:  # one bad source must not fail the run
                log.exception("source %s crashed", source.name)
                return source, [], f"error ({type(exc).__name__})"

        return await asyncio.gather(*(one(s) for s in sources))


# --- storing ------------------------------------------------------------------


async def insert_items(
    conn: psycopg.AsyncConnection, source: SourceRow, items: list[FetchedItem], now: datetime
) -> list[dict]:
    """Insert new items; returns the rows actually inserted."""
    cutoff = now - timedelta(days=RETENTION_DAYS)
    rows = []
    seen = set()
    for it in items:
        published = it.published_at or now
        if published > now + timedelta(minutes=5):
            published = now  # feeds sometimes post-date entries
        if published < cutoff or it.url in seen:
            continue
        seen.add(it.url)
        rows.append((it.url, it.link_url, it.origin, it.title, published))
    if not rows:
        return []
    urls, links, origins, titles, published = map(list, zip(*rows))
    cur = await conn.execute(
        """
        insert into items (source_id, url, link_url, origin, title, published_at, fetched_at)
        select %s, u, l, o, t, p, %s
        from unnest(%s::text[], %s::text[], %s::text[], %s::text[], %s::timestamptz[]) as x(u, l, o, t, p)
        on conflict (url) do nothing
        returning id, title, published_at
        """,
        (source.id, now, urls, links, origins, titles, published),
    )
    return await cur.fetchall()


async def link_entities(conn: psycopg.AsyncConnection, new_items: list[dict], aliases: AliasTable) -> dict:
    """Extract entities from new item titles and insert mentions."""
    per_item: list[tuple[int, list]] = []
    strong: dict[str, str] = {}
    all_keys: set[str] = set()
    for item in new_items:
        cands = extract(item["title"], aliases)
        per_item.append((item["id"], cands))
        for c in cands:
            all_keys.add(c.key)
            if not c.weak:
                strong.setdefault(c.key, c.display)
    if not all_keys:
        return {"mentions": 0, "entities_new": 0}

    created = await (
        await conn.execute(
            """
            insert into entities (name, norm)
            select n, k from unnest(%s::text[], %s::text[]) as x(k, n)
            on conflict (norm) do nothing
            returning id
            """,
            (list(strong.keys()), list(strong.values())),
        )
    ).fetchall()
    rows = await (
        await conn.execute("select id, norm from entities where norm = any(%s)", (list(all_keys),))
    ).fetchall()
    ids = {r["norm"]: r["id"] for r in rows}

    pairs = {(item_id, ids[c.key]) for item_id, cands in per_item for c in cands if c.key in ids}
    if pairs:
        item_ids, entity_ids = map(list, zip(*pairs))
        await conn.execute(
            """
            insert into mentions (item_id, entity_id)
            select * from unnest(%s::bigint[], %s::bigint[])
            on conflict do nothing
            """,
            (item_ids, entity_ids),
        )
    return {"mentions": len(pairs), "entities_new": len(created)}


async def refresh_counts(conn: psycopg.AsyncConnection, item_ids: list[int]) -> int:
    """Recompute entity_counts for every (entity, hour) touched by these items.

    weighted_count = sum over origins in that hour of w * (1 + ln n), where n
    is the number of that origin's items and w its source's independence
    weight, so ten posts from one outlet count less than one each from five.
    """
    if not item_ids:
        return 0
    cur = await conn.execute(
        """
        with affected as (
            select distinct m.entity_id, date_trunc('hour', i.published_at) as hour_bucket
            from mentions m join items i on i.id = m.item_id
            where m.item_id = any(%s)
        ),
        per_origin as (
            select a.entity_id, a.hour_bucket, i.origin,
                   count(*) as n, max(s.independence_weight) as w
            from affected a
            join mentions m on m.entity_id = a.entity_id
            join items i on i.id = m.item_id
                and i.published_at >= a.hour_bucket
                and i.published_at < a.hour_bucket + interval '1 hour'
            join sources s on s.id = i.source_id
            group by a.entity_id, a.hour_bucket, i.origin
        )
        insert into entity_counts (entity_id, hour_bucket, weighted_count, distinct_sources)
        select entity_id, hour_bucket, sum(w * (1 + ln(n))), count(*)
        from per_origin
        group by entity_id, hour_bucket
        on conflict (entity_id, hour_bucket) do update
            set weighted_count = excluded.weighted_count,
                distinct_sources = excluded.distinct_sources
        """,
        (item_ids,),
    )
    return cur.rowcount


async def prune(conn: psycopg.AsyncConnection, now: datetime) -> int:
    cur = await conn.execute(
        "delete from items where published_at < %s", (now - timedelta(days=RETENTION_DAYS),)
    )
    return cur.rowcount  # mentions go with them (on delete cascade)


# --- the pipeline -----------------------------------------------------------------


async def collect_once(
    conn: psycopg.AsyncConnection,
    fetchers: dict[str, Fetcher] | None = None,
    now: datetime | None = None,
) -> tuple[str, str | None, dict]:
    """One collect pass on an open connection. Returns (status, error, stats)."""
    now = now or datetime.now(timezone.utc)
    fetchers = fetchers or FETCHERS

    await sync_sources(conn, load_sources_file())
    aliases = await sync_aliases(conn, load_aliases_file())
    sources = await load_active_sources(conn)

    async def known_urls(urls: list[str]) -> set[str]:
        async with db.connection() as c2:
            rows = await (await c2.execute("select url from items where url = any(%s)", (urls,))).fetchall()
        return {r["url"] for r in rows}

    results = await fetch_all(sources, fetchers, FetchContext(known_urls=known_urls))

    stats = {"sources": len(sources), "sources_failed": 0, "fetched": 0, "items_new": 0}
    errors: list[str] = []
    new_items: list[dict] = []
    for source, items, err in results:
        if err:
            stats["sources_failed"] += 1
            errors.append(f"{source.name}: {err}")
            log.warning("source %s failed: %s", source.name, err)
            continue
        stats["fetched"] += len(items)
        inserted = await insert_items(conn, source, items, now)
        new_items.extend(inserted)
    await conn.commit()
    stats["items_new"] = len(new_items)

    linked = await link_entities(conn, new_items, aliases)
    stats.update(linked)
    stats["count_rows"] = await refresh_counts(conn, [i["id"] for i in new_items])
    await conn.commit()
    stats["pruned"] = await prune(conn, now)
    await conn.commit()

    if sources and stats["sources_failed"] == len(sources):
        status = "failed"
    elif stats["sources_failed"]:
        status = "partial"
    else:
        status = "success"
    return status, "; ".join(errors) or None, stats


async def run_collect(fetchers: dict[str, Fetcher] | None = None, now: datetime | None = None) -> dict:
    """Entry point for POST /collect's background task. Never raises."""
    try:
        async with db.advisory_lock(COLLECT_LOCK_KEY) as acquired:
            async with db.connection() as conn:
                if not acquired:
                    await record_skipped(conn, "collect", "another collect is running")
                    log.info("collect skipped: lock held")
                    return {"status": "skipped"}
                await expire_stale_runs(conn, "collect")
                run_id = await start_run(conn, "collect")
                try:
                    status, error, stats = await collect_once(conn, fetchers, now)
                except Exception as exc:
                    await conn.rollback()
                    log.exception("collect failed")
                    await finish_run(conn, run_id, "failed", f"{type(exc).__name__}")
                    return {"status": "failed"}
                await finish_run(conn, run_id, status, error, stats)
                log.info("collect %s: %s", status, stats)
                return {"status": status, **stats}
    except Exception:
        log.exception("collect could not start")
        return {"status": "failed"}
