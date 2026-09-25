"""Hacker News via the official Firebase API (top + new stories)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from app.config import FETCH_CONCURRENCY, HN_NEW_LIMIT, HN_TOP_LIMIT
from app.sources.base import FetchContext, FetchedItem, SourceError, SourceRow, clean_text, is_http_url, origin_for_url

HN_ITEM_URL = "https://news.ycombinator.com/item?id={}"
HN_ORIGIN = "news.ycombinator.com"


def item_from_hn(story: dict) -> FetchedItem | None:
    """Convert one HN API/Algolia story to a FetchedItem, or None to skip it."""
    if not story or story.get("dead") or story.get("deleted") or story.get("type", "story") != "story":
        return None
    title = clean_text(story.get("title"))
    if not title or "id" not in story:
        return None
    permalink = HN_ITEM_URL.format(story["id"])
    link = story.get("url")
    external = is_http_url(link)
    ts = story.get("time")
    return FetchedItem(
        url=permalink,
        link_url=link.strip() if external else permalink,
        title=title,
        published_at=datetime.fromtimestamp(ts, timezone.utc) if ts else None,
        origin=origin_for_url(link) if external else HN_ORIGIN,
    )


async def fetch(client: httpx.AsyncClient, source: SourceRow, ctx: FetchContext) -> list[FetchedItem]:
    base = source.url.rstrip("/") + "/"
    try:
        top_resp, new_resp = await asyncio.gather(
            client.get(base + "topstories.json"), client.get(base + "newstories.json")
        )
        top_resp.raise_for_status()
        new_resp.raise_for_status()
        ids = list(dict.fromkeys(top_resp.json()[:HN_TOP_LIMIT] + new_resp.json()[:HN_NEW_LIMIT]))
    except (httpx.HTTPError, ValueError) as exc:
        raise SourceError(f"story lists: {type(exc).__name__}") from exc

    known = await ctx.known_urls([HN_ITEM_URL.format(i) for i in ids])
    todo = [i for i in ids if HN_ITEM_URL.format(i) not in known]
    sem = asyncio.Semaphore(FETCH_CONCURRENCY)

    async def one(item_id: int) -> FetchedItem | None:
        async with sem:
            try:
                resp = await client.get(f"{base}item/{item_id}.json")
                resp.raise_for_status()
                return item_from_hn(resp.json())
            except (httpx.HTTPError, ValueError):
                return None

    results = await asyncio.gather(*(one(i) for i in todo))
    return [r for r in results if r is not None]
