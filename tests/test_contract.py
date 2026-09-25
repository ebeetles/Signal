"""API contract test (required by the brief).

Calls GET /digests?approved=true against seeded data and asserts the exact
response shape: field names and JSON types. If a change renames, removes or
retypes a field the frontend relies on, this test must fail. Update
FRONTEND_HANDOFF.md before changing these expectations.
"""

import re

import pytest
from fastapi.testclient import TestClient

from app import api, main

PAGE_FIELDS = {"digests": list, "next_before": (int, type(None))}
DIGEST_FIELDS = {"id": int, "date": str, "sent_at": (str, type(None)), "status": str, "items": list}
ITEM_FIELDS = {
    "id": int,
    "headline": str,
    "url": str,
    "source": str,
    "reason": str,
    "entity": str,
    "digest_date": str,
    "vote": (str, type(None)),
}
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ISO_WITH_OFFSET = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:\d{2})$")


def seed(sql):
    """Three digests: one with two approved items, one quiet, one with only a downvote."""
    d1 = sql.execute(
        "insert into digests (digest_date, status, sent_at) values ('2026-09-23', 'sent', '2026-09-23 11:00:04+00') returning id"
    ).fetchone()["id"]
    d2 = sql.execute(
        "insert into digests (digest_date, status, sent_at) values ('2026-09-24', 'quiet', '2026-09-24 11:00:03+00') returning id"
    ).fetchone()["id"]
    d3 = sql.execute(
        "insert into digests (digest_date, status, sent_at) values ('2026-09-25', 'sent', '2026-09-25 11:00:02+00') returning id"
    ).fetchone()["id"]
    rows = [
        (d1, 1, "Claude Opus 5.5", "https://www.anthropic.com/claude-opus-5-5", "anthropic.com",
         "8 sources in 15h (usually 0), matches: Claude", "Claude Opus 5.5", "up"),
        (d1, 2, "GPT-6 Sol and GPT-6 Luna", "https://openai.com/index/gpt-6", "OpenAI News",
         "4 sources in 20h (usually 1)", "GPT-6", "up"),
        (d1, 3, "Something meh", "https://example.com/meh", "example.com", "3 sources in 9h (usually 0)", "Meh", None),
        (d3, 1, "Rust 1.91", "https://blog.rust-lang.org/x", "Rust Blog", "3 sources in 6h (usually 0), matches: Rust",
         "Rust 1.91", "down"),
    ]
    for d, rank, headline, url, source, reason, entity, vote in rows:
        sql.execute(
            """insert into digest_items (digest_id, rank, score, spike, reason, headline, url, source_name,
                                         entity_name, feedback)
               values (%s, %s, 2.5, 7, %s, %s, %s, %s, %s, %s)""",
            (d, rank, reason, headline, url, source, entity, vote),
        )
    return d1, d2, d3


@pytest.fixture
def client(sql):
    api.limiter.hits.clear()
    with TestClient(main.app) as c:
        yield c


def assert_fields(obj: dict, spec: dict, where: str):
    assert set(obj) == set(spec), f"{where}: fields {sorted(obj)} != {sorted(spec)}"
    for key, typ in spec.items():
        assert isinstance(obj[key], typ), f"{where}.{key}: {type(obj[key]).__name__}"
        assert not (typ is int and isinstance(obj[key], bool)), f"{where}.{key} is a bool"


def test_approved_digests_contract(client, sql):
    d1, _, _ = seed(sql)
    r = client.get("/digests?approved=true")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/json"
    body = r.json()
    assert_fields(body, PAGE_FIELDS, "page")
    assert body["next_before"] is None

    # only the digest with approved items; quiet and downvote-only digests omitted
    assert [d["id"] for d in body["digests"]] == [d1]
    digest = body["digests"][0]
    assert_fields(digest, DIGEST_FIELDS, "digest")
    assert DATE.match(digest["date"]) and digest["date"] == "2026-09-23"
    assert ISO_WITH_OFFSET.match(digest["sent_at"]) and digest["sent_at"] == "2026-09-23T07:00:04-04:00"
    assert digest["status"] == "sent"

    assert len(digest["items"]) == 2
    for it in digest["items"]:
        assert_fields(it, ITEM_FIELDS, "item")
        assert it["vote"] == "up"
        assert it["digest_date"] == digest["date"]
        assert it["url"].startswith("https://")
    first = digest["items"][0]
    assert first == {
        "id": first["id"],
        "headline": "Claude Opus 5.5",
        "url": "https://www.anthropic.com/claude-opus-5-5",
        "source": "anthropic.com",
        "reason": "8 sources in 15h (usually 0), matches: Claude",
        "entity": "Claude Opus 5.5",
        "digest_date": "2026-09-23",
        "vote": "up",
    }


def test_openapi_documents_contract(client):
    spec = client.get("/openapi.json").json()
    item = spec["components"]["schemas"]["DigestItemOut"]
    assert set(item["properties"]) == set(ITEM_FIELDS)
    page = spec["components"]["schemas"]["DigestPage"]
    assert set(page["properties"]) == set(PAGE_FIELDS)
