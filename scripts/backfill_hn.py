"""Backfill Hacker News stories from the Algolia HN Search API, for evaluation.

Usage:
    python -m scripts.backfill_hn --start 2026-09-05 --end 2026-09-25 [--database-url URL] [--mark-covered]

Fetches every story created in [start, end) (UTC dates) through
https://hn.algolia.com/api/v1/search_by_date filtered by created_at_i, then
runs the normal collect storage path: items, entity extraction, mentions,
hourly counts.

--mark-covered records one 'backfill' run per hour in the range, so the
scorer treats those hours as observed baseline. Use it on an evaluation
database. On production it would make HN-only history look complete, so
it is off by default.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx
import psycopg
from psycopg.rows import dict_row

from app.collect import (
    insert_items,
    link_entities,
    load_active_sources,
    load_aliases_file,
    load_sources_file,
    refresh_counts,
    sync_aliases,
    sync_sources,
)
from app.config import USER_AGENT
from app.sources.hn import item_from_hn

ALGOLIA = "https://hn.algolia.com/api/v1/search_by_date"


def fetch_stories(start: datetime, end: datetime) -> list[dict]:
    """All stories with start <= created_at < end, walking backwards in time."""
    lo, hi = int(start.timestamp()), int(end.timestamp())
    seen: dict[str, dict] = {}
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30) as client:
        while hi > lo:
            params = {
                "tags": "story",
                "numericFilters": f"created_at_i>={lo},created_at_i<{hi}",
                "hitsPerPage": 1000,
            }
            for attempt in range(4):
                resp = client.get(ALGOLIA, params=params)
                if resp.status_code == 200:
                    break
                time.sleep(2**attempt)
            resp.raise_for_status()
            hits = resp.json().get("hits", [])
            if not hits:
                break
            for h in hits:
                seen[h["objectID"]] = h
            oldest = min(h["created_at_i"] for h in hits)
            print(f"  {datetime.fromtimestamp(oldest, timezone.utc):%Y-%m-%d %H:%M} ... {len(seen)} stories", flush=True)
            if len(hits) < 1000:
                break
            hi = oldest if oldest < hi else hi - 1
            time.sleep(0.2)
    return list(seen.values())


def to_api_story(hit: dict) -> dict:
    return {
        "id": int(hit["objectID"]),
        "type": "story",
        "title": hit.get("title") or "",
        "url": hit.get("url"),
        "time": hit.get("created_at_i"),
    }


async def store(database_url: str, stories: list[dict], start: datetime, end: datetime, mark_covered: bool) -> dict:
    now = datetime.now(timezone.utc)
    async with await psycopg.AsyncConnection.connect(
        database_url, prepare_threshold=None, row_factory=dict_row
    ) as conn:
        await conn.execute("set time zone 'UTC'")
        await sync_sources(conn, load_sources_file())
        aliases = await sync_aliases(conn, load_aliases_file())
        hn = next(s for s in await load_active_sources(conn) if s.type == "hn")
        items = [it for it in (item_from_hn(to_api_story(s)) for s in stories) if it]
        inserted = []
        for i in range(0, len(items), 2000):
            inserted += await insert_items(conn, hn, items[i : i + 2000], now)
        await conn.commit()
        linked = await link_entities(conn, inserted, aliases)
        await conn.commit()
        rows = 0
        ids = [i["id"] for i in inserted]
        for i in range(0, len(ids), 2000):
            rows += await refresh_counts(conn, ids[i : i + 2000])
        await conn.commit()
        covered = 0
        if mark_covered:
            cur = await conn.execute(
                """
                insert into runs (kind, started_at, finished_at, status, stats)
                select 'backfill', h, h, 'success', '{"source": "algolia"}'::jsonb
                from generate_series(%s::timestamptz, %s::timestamptz - interval '1 hour', interval '1 hour') h
                where not exists (
                    select 1 from runs r where r.kind in ('collect', 'backfill')
                      and r.status in ('success', 'partial') and date_trunc('hour', r.started_at) = h)
                """,
                (start, end),
            )
            covered = cur.rowcount
            await conn.commit()
    return {"stories": len(stories), "items_new": len(inserted), **linked, "count_rows": rows, "hours_marked": covered}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", required=True, help="UTC date, inclusive (YYYY-MM-DD)")
    ap.add_argument("--end", required=True, help="UTC date, exclusive (YYYY-MM-DD)")
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--mark-covered", action="store_true", help="record hourly 'backfill' runs (evaluation DBs only)")
    args = ap.parse_args(argv)
    if not args.database_url:
        print("Set DATABASE_URL or pass --database-url")
        return 1
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    print(f"Fetching HN stories {start:%Y-%m-%d} .. {end:%Y-%m-%d} from Algolia")
    stories = fetch_stories(start, end)
    print(f"Storing {len(stories)} stories")
    result = asyncio.run(store(args.database_url, stories, start, end - timedelta(0), args.mark_covered))
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
