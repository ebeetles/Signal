"""RSS and Atom feeds (blogs, changelogs, GitHub releases, YouTube channels)."""

from __future__ import annotations

import calendar
import re
from datetime import datetime, timezone

import feedparser
import httpx

from app.sources.base import FetchContext, FetchedItem, SourceError, SourceRow, canonical_url, clean_text, is_http_url, origin_for_url

_YT_CHANNEL = re.compile(r"[?&]channel_id=([\w-]+)")


def _published(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key)
        if value:
            try:
                return datetime.fromtimestamp(calendar.timegm(value), timezone.utc)
            except (OverflowError, ValueError, TypeError):
                continue
    return None


def source_origin(source: SourceRow) -> str | None:
    if source.origin:
        return source.origin
    m = _YT_CHANNEL.search(source.url)
    if m:
        return f"youtube.com/channel/{m.group(1)}"
    return None


def parse_feed(content: bytes, source: SourceRow) -> list[FetchedItem]:
    feed = feedparser.parse(content)
    if not feed.entries:
        reason = type(feed.get("bozo_exception")).__name__ if feed.bozo else "no entries"
        raise SourceError(f"unparseable feed ({reason})")
    fixed_origin = source_origin(source)
    items: list[FetchedItem] = []
    for entry in feed.entries:
        link = (entry.get("link") or "").strip()
        title = clean_text(entry.get("title"))
        if not title or not is_http_url(link):
            continue
        if source.title_prefix and source.title_prefix.lower() not in title.lower():
            title = f"{source.title_prefix} {title}"
        url = canonical_url(link)
        items.append(
            FetchedItem(
                url=url,
                link_url=link,
                title=title,
                published_at=_published(entry),
                origin=fixed_origin or origin_for_url(link),
            )
        )
    return items


async def fetch(client: httpx.AsyncClient, source: SourceRow, ctx: FetchContext) -> list[FetchedItem]:
    try:
        resp = await client.get(source.url)
    except httpx.HTTPError as exc:
        raise SourceError(f"request failed ({type(exc).__name__})") from exc
    if resp.status_code != 200:
        raise SourceError(f"HTTP {resp.status_code}")
    return parse_feed(resp.content, source)
