"""Fetcher helpers: origins, URL canonicalization, feed parsing, HN items."""

import pytest

from app.sources.base import SourceError, SourceRow, canonical_url, clean_text, is_http_url, origin_for_url
from app.sources.hn import item_from_hn
from app.sources.rss import parse_feed


@pytest.mark.parametrize(
    "url,origin",
    [
        ("https://www.anthropic.com/claude-opus-5-5", "anthropic.com"),
        ("https://www.theverge.com/ai/123/x", "theverge.com"),
        ("https://claude-opus-5-5.riba2534.cn/", "riba2534.cn"),
        ("https://simonwillison.net/2026/Sep/22/x/", "simonwillison.net"),
        ("https://github.com/ninjahawk/livenerf", "github.com/ninjahawk"),
        ("https://github.com/anthropics/claude-code/releases/tag/v2.1", "github.com/anthropics"),
        ("https://twitter.com/cahidarda/status/1", "x.com/cahidarda"),
        ("https://x.com/CahidArda/status/1", "x.com/cahidarda"),
        ("https://medium.com/@someone/post-1", "medium.com/@someone"),
        ("https://someone.substack.com/p/post", "someone.substack.com"),
        ("https://user.github.io/blog/", "user.github.io"),
        ("https://www.reddit.com/r/ClaudeAI/comments/1/x/", "reddit.com/r/claudeai"),
        ("https://www.youtube.com/watch?v=abc", "youtube.com"),
        ("https://www.youtube.com/@TwoMinutePapers", "youtube.com/@twominutepapers"),
        ("https://youtu.be/abc", "youtube.com"),
        ("https://www.bbc.co.uk/news/x", "bbc.co.uk"),
    ],
)
def test_origin_for_url(url, origin):
    assert origin_for_url(url) == origin


def test_canonical_url_drops_tracking_and_fragment():
    assert (
        canonical_url("https://Example.com/a?utm_source=x&id=3&ref=hn#section")
        == "https://example.com/a?id=3"
    )


@pytest.mark.parametrize(
    "url,ok",
    [("https://a.com/x", True), ("http://a.com", True), ("javascript:alert(1)", False),
     ("ftp://a.com/x", False), ("", False), (None, False), ("https://", False)],
)
def test_is_http_url(url, ok):
    assert is_http_url(url) is ok


def test_clean_text_strips_tags_and_entities():
    assert clean_text("  <b>Hello</b>&amp; <i>world</i>\n ") == "Hello & world"


SOURCE = SourceRow(id=1, name="Blog", type="rss", url="https://blog.example/feed",
                   independence_weight=1.0, is_primary=True, origin=None)

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Releases</title>
  <entry>
    <title>v1.0.0</title>
    <link href="https://github.com/vllm-project/vllm/releases/tag/v1.0.0"/>
    <updated>2026-09-22T16:00:00Z</updated>
  </entry>
  <entry>
    <title>No link here</title>
    <updated>2026-09-22T16:00:00Z</updated>
  </entry>
  <entry>
    <title>Bad scheme</title>
    <link href="javascript:alert(1)"/>
  </entry>
</feed>"""

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>News</title>
<item><title>Anthropic ships &lt;b&gt;Claude Opus 5.5&lt;/b&gt;</title>
<link>https://news.example/opus?utm_source=rss</link>
<pubDate>Tue, 22 Sep 2026 17:03:54 GMT</pubDate></item>
</channel></rss>"""


def test_parse_atom_with_title_prefix_and_bad_links():
    src = SourceRow(**{**SOURCE.__dict__, "title_prefix": "vLLM"})
    items = parse_feed(ATOM, src)
    assert len(items) == 1
    it = items[0]
    assert it.title == "vLLM v1.0.0"
    assert it.origin == "github.com/vllm-project"
    assert it.published_at.isoformat() == "2026-09-22T16:00:00+00:00"


def test_parse_rss_strips_markup_and_tracking():
    [it] = parse_feed(RSS, SOURCE)
    assert it.title == "Anthropic ships Claude Opus 5.5"
    assert it.url == "https://news.example/opus"
    assert it.link_url == "https://news.example/opus?utm_source=rss"
    assert it.origin == "news.example"


def test_youtube_feed_origin_is_the_channel():
    src = SourceRow(**{**SOURCE.__dict__, "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCabc"})
    feed = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <title>Opus 5.5 review</title><link href="https://www.youtube.com/watch?v=1"/>
      <published>2026-09-23T00:00:00Z</published></entry></feed>"""
    [it] = parse_feed(feed, src)
    assert it.origin == "youtube.com/channel/UCabc"


def test_explicit_origin_override():
    src = SourceRow(**{**SOURCE.__dict__, "origin": "verge"})
    [it] = parse_feed(RSS, src)
    assert it.origin == "verge"


@pytest.mark.parametrize("content", [b"", b"<html><body>Not a feed</body></html>", b"\x00\x01garbage"])
def test_unparseable_feed_raises_source_error(content):
    with pytest.raises(SourceError):
        parse_feed(content, SOURCE)


def test_hn_item_with_external_link():
    it = item_from_hn({"id": 7, "type": "story", "title": "Claude Opus 5.5", "time": 1790008050,
                       "url": "https://www.anthropic.com/claude-opus-5-5"})
    assert it.url == "https://news.ycombinator.com/item?id=7"
    assert it.link_url == "https://www.anthropic.com/claude-opus-5-5"
    assert it.origin == "anthropic.com"


def test_hn_self_post_links_to_discussion():
    it = item_from_hn({"id": 8, "type": "story", "title": "Ask HN: Is Opus 5.5 good?", "time": 1790008050})
    assert it.link_url == it.url == "https://news.ycombinator.com/item?id=8"
    assert it.origin == "news.ycombinator.com"


@pytest.mark.parametrize(
    "story",
    [None, {"id": 1, "type": "job", "title": "Hiring"}, {"id": 2, "dead": True, "title": "x"},
     {"id": 3, "deleted": True}, {"id": 4, "type": "story", "title": ""},
     {"id": 5, "type": "story", "title": "Hi", "url": "javascript:alert(1)", "time": 1}],
)
def test_hn_skips_or_sanitizes(story):
    it = item_from_hn(story)
    if story and story.get("id") == 5:
        assert it.link_url == "https://news.ycombinator.com/item?id=5"
    else:
        assert it is None
