"""Source fetchers, keyed by sources.type. Adding a source type (e.g. Reddit)
means adding one module with an async `fetch(client, source, ctx)` and one
entry here, plus the type in the sources table check constraint."""

from app.sources import hn, rss

FETCHERS = {
    "hn": hn.fetch,
    "rss": rss.fetch,
}
