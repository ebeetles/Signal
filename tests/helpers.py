import asyncio
from datetime import datetime, timezone

from app import db
from app.sources.base import FetchedItem, SourceError


def run(coro):
    """Run a coroutine, then close the pool on the same event loop."""

    async def wrapper():
        try:
            return await coro
        finally:
            await db.close_pool()

    return asyncio.run(wrapper())


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def item(url, title, published, origin, link=None) -> FetchedItem:
    return FetchedItem(url=url, link_url=link or url, title=title, published_at=published, origin=origin)


def fake_fetchers(by_source: dict):
    """Fetchers keyed by type that look up canned results by source name.
    A value that is an Exception is raised instead."""

    async def fetch(client, source, ctx):
        result = by_source.get(source.name, [])
        if isinstance(result, Exception):
            raise result
        return list(result)

    return {"hn": fetch, "rss": fetch}


SOURCES = [
    {"name": "HN", "type": "hn", "url": "https://hn.example/", "independence_weight": 1.0,
     "is_primary": False, "origin": None, "title_prefix": None, "active": True},
    {"name": "Blog", "type": "rss", "url": "https://blog.example/feed", "independence_weight": 1.0,
     "is_primary": True, "origin": None, "title_prefix": None, "active": True},
    {"name": "News", "type": "rss", "url": "https://news.example/feed", "independence_weight": 0.5,
     "is_primary": False, "origin": None, "title_prefix": None, "active": True},
]

__all__ = ["run", "utc", "item", "fake_fetchers", "SOURCES", "SourceError"]
