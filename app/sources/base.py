"""Shared types and URL helpers for fetchers."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.config import MAX_TITLE_CHARS


@dataclass(frozen=True)
class SourceRow:
    id: int
    name: str
    type: str
    url: str
    independence_weight: float
    is_primary: bool
    origin: str | None
    title_prefix: str | None = None


@dataclass(frozen=True)
class FetchedItem:
    url: str  # dedupe key
    link_url: str  # where a reader should go
    title: str
    published_at: datetime | None
    origin: str  # independence key


@dataclass
class FetchContext:
    # Given candidate item URLs, returns the subset already stored (lets HN
    # skip re-fetching item details it has seen).
    known_urls: Callable[[list[str]], Awaitable[set[str]]]


class SourceError(Exception):
    """A source could not be fetched or parsed. Message is safe to store."""


# Hosts where many independent authors share one domain; the first path
# segment identifies the outlet (github.com/anthropics, x.com/someone).
_MULTI_TENANT = {"github.com", "gitlab.com", "medium.com", "x.com", "dev.to", "bsky.app", "linkedin.com", "reddit.com", "youtube.com"}
# Suffixes under which each subdomain is a separate site.
_SHARED_SUFFIXES = {
    "github.io", "substack.com", "vercel.app", "netlify.app", "pages.dev", "blogspot.com",
    "wordpress.com", "herokuapp.com", "fly.dev", "onrender.com", "medium.com", "bearblog.dev",
    "neocities.org", "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "co.jp", "com.br",
    "co.in", "co.nz", "com.cn", "co.kr",
}
_TRACKING_PARAMS = re.compile(r"^(utm_\w+|fbclid|gclid|mc_cid|mc_eid|ref|ref_src|source)$", re.I)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def is_http_url(url: str | None) -> bool:
    if not url:
        return False
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def canonical_url(url: str) -> str:
    """Drop fragments and tracking parameters so reposts dedupe."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING_PARAMS.match(k)]
    return urlunsplit((parts.scheme.lower(), (parts.netloc or "").lower(), parts.path or "/", urlencode(query), ""))


def origin_for_url(url: str) -> str:
    """Independence key for a link: 'theverge.com', 'github.com/vllm-project'."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    for prefix in ("www.", "m.", "mobile.", "amp."):
        if host.startswith(prefix):
            host = host[len(prefix) :]
    labels = host.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in _SHARED_SUFFIXES:
        base = ".".join(labels[-3:])
    else:
        base = ".".join(labels[-2:])
    if base == "twitter.com":
        base = "x.com"
    if base == "youtu.be":
        base = "youtube.com"
    if base in _MULTI_TENANT:
        segs = [s for s in parts.path.split("/") if s]
        if base == "reddit.com" and len(segs) >= 2 and segs[0] == "r":
            return f"{base}/r/{segs[1].lower()}"
        if base == "youtube.com":
            if segs and segs[0].startswith("@"):
                return f"{base}/{segs[0].lower()}"
            if len(segs) >= 2 and segs[0] in ("channel", "c", "user"):
                return f"{base}/{segs[0]}/{segs[1]}"
            return base
        if base in ("bsky.app", "linkedin.com") and len(segs) >= 2:
            return f"{base}/{segs[0]}/{segs[1].lower()}"
        if segs and base not in ("bsky.app", "linkedin.com", "reddit.com"):
            return f"{base}/{segs[0].lower()}"
    return base or "unknown"


def clean_text(text: str | None) -> str:
    text = html.unescape(_TAG.sub(" ", text or ""))
    text = _SPACE.sub(" ", text).strip()
    return text[:MAX_TITLE_CHARS]
