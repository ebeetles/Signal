"""Public API: pagination, validation, errors, CORS, rate limit, stats, history."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import api, main
from tests.test_contract import seed

ORIGIN = "https://ebeetles.github.io"


@pytest.fixture
def client(sql):
    api.limiter.hits.clear()
    with TestClient(main.app) as c:
        yield c


def many_digests(sql, n=7):
    ids = []
    for k in range(n):
        d = sql.execute(
            "insert into digests (digest_date, status, sent_at) values (%s, 'sent', now()) returning id",
            (datetime(2026, 9, 1).date() + timedelta(days=k),),
        ).fetchone()["id"]
        sql.execute(
            """insert into digest_items (digest_id, rank, score, spike, reason, headline, url, source_name,
                                         entity_name, feedback)
               values (%s, 1, 1, 1, 'r', %s, 'https://x.com/a', 's', 'e', %s)""",
            (d, f"h{k}", "up" if k % 3 else None),  # every third digest has no approved item
        )
        ids.append(d)
    return ids


def test_pagination_reaches_end_without_duplicates(client, sql):
    ids = many_digests(sql)
    approved = [d for k, d in enumerate(ids) if k % 3]
    seen, before, pages = [], None, 0
    while True:
        url = "/digests?approved=true&limit=2" + (f"&before={before}" if before else "")
        body = client.get(url).json()
        seen += [d["id"] for d in body["digests"]]
        pages += 1
        before = body["next_before"]
        if before is None:
            break
        assert before == body["digests"][-1]["id"]
    assert seen == sorted(approved, reverse=True)
    assert len(seen) == len(set(seen)) and pages == 2


def test_unfiltered_includes_quiet_days_and_all_votes(client, sql):
    d1, d2, d3 = seed(sql)
    body = client.get("/digests").json()
    assert [d["id"] for d in body["digests"]] == [d3, d2, d1]
    assert [len(d["items"]) for d in body["digests"]] == [1, 0, 3]
    assert body["digests"][1]["status"] == "quiet"


def test_pending_and_failed_digests_are_hidden(client, sql):
    sql.execute("insert into digests (digest_date, status) values ('2026-09-20', 'pending'), ('2026-09-21', 'failed')")
    assert client.get("/digests").json() == {"digests": [], "next_before": None}


def test_get_one_digest_and_approved_filter(client, sql):
    d1, _, _ = seed(sql)
    assert len(client.get(f"/digests/{d1}").json()["items"]) == 3
    assert [i["vote"] for i in client.get(f"/digests/{d1}?approved=true").json()["items"]] == ["up", "up"]


@pytest.mark.parametrize(
    "url,field",
    [
        ("/digests?limit=0", "limit"),
        ("/digests?limit=31", "limit"),
        ("/digests?limit=abc", "limit"),
        ("/digests?before=0", "before"),
        ("/digests?before=-5", "before"),
        ("/digests?approved=maybe", "approved"),
        ("/digests/abc", "digest_id"),
        ("/interests/history?days=0", "days"),
        ("/interests/history?days=366", "days"),
    ],
)
def test_invalid_params_give_json_422(client, url, field):
    r = client.get(url)
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "invalid_request"
    assert body["error"]["message"] == "Invalid request parameters"
    assert body["error"]["details"][0]["field"] == field


def test_unknown_digest_is_404(client, sql):
    r = client.get("/digests/999999")
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "not_found", "message": "Digest 999999 not found"}}


def test_unknown_route_is_json_404(client):
    r = client.get("/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_cors_allows_portfolio_origin_only(client, sql):
    ok = client.get("/digests?approved=true", headers={"Origin": ORIGIN})
    assert ok.headers.get("access-control-allow-origin") == ORIGIN
    bad = client.get("/digests?approved=true", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in bad.headers
    pre = client.options("/digests", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET"})
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == ORIGIN
    post = client.options("/digests", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"})
    assert post.status_code == 400
    evil_pre = client.options("/digests", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert evil_pre.status_code == 400 and "access-control-allow-origin" not in evil_pre.headers


def test_rate_limit(client, sql, monkeypatch):
    monkeypatch.setattr(api.limiter, "limit", 3)
    for _ in range(3):
        assert client.get("/digests").status_code == 200
    r = client.get("/digests")
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"
    assert int(r.headers["retry-after"]) >= 1
    # other clients are unaffected
    assert client.get("/digests", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 200
    # health is not rate limited
    assert client.get("/health").status_code == 200


def test_rate_limiter_window():
    rl = api.RateLimiter(2, 10)
    assert rl.check("a", 0) is None and rl.check("a", 1) is None
    assert rl.check("a", 2) == pytest.approx(8)
    assert rl.check("a", 10.5) is None


def test_cache_header(client, sql):
    assert client.get("/digests").headers["cache-control"] == "public, max-age=60"


def test_database_unavailable_is_503(client, monkeypatch):
    from app import db

    async def down():
        raise db.DatabaseUnavailable("x")

    monkeypatch.setattr(db, "get_pool", down)
    r = client.get("/digests?approved=true")
    assert r.status_code == 503
    assert r.json() == {"error": {"code": "unavailable", "message": "Service temporarily unavailable"}}


def test_interest_history(client, sql):
    today = datetime.now(timezone.utc).astimezone(main.get_settings().tz).date()
    sql.execute("insert into interests (term, weight, origin) values ('Claude', 2.2, 'manual'), ('Opus', 0.5, 'learned')")
    for k, (c, o) in enumerate([(2.0, None), (2.0, 0.5), (2.2, 0.5)]):
        day = today - timedelta(days=2 - k)
        sql.execute("insert into interest_history (term, weight, day) values ('Claude', %s, %s)", (c, day))
        if o is not None:
            sql.execute("insert into interest_history (term, weight, day) values ('Opus', %s, %s)", (o, day))
    sql.execute("insert into interest_history (term, weight, day) values ('Gone', 1, %s)", (today - timedelta(days=1),))
    sql.execute("insert into interest_history (term, weight, day) values ('Old', 1, %s)", (today - timedelta(days=40),))
    body = client.get("/interests/history?days=7").json()
    assert body["end"] == str(today) and body["start"] == str(today - timedelta(days=6))
    assert [t["term"] for t in body["terms"]] == ["Claude", "Opus", "Gone"]
    claude = body["terms"][0]
    assert claude["origin"] == "manual" and claude["current_weight"] == 2.2
    assert [p["weight"] for p in claude["points"]] == [2.0, 2.0, 2.2]
    assert body["terms"][2]["origin"] is None and body["terms"][2]["current_weight"] is None


def test_stats(client, sql):
    seed(sql)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    for h in range(1, 11):  # 10 hours, one failed run
        status = "failed" if h == 5 else "success"
        sql.execute(
            "insert into runs (kind, started_at, finished_at, status) values ('collect', %s, %s, %s)",
            (now - timedelta(hours=h) + timedelta(minutes=50), now, status),
        )
    sql.execute("insert into missed (text) values ('a'), ('b')")
    s = client.get("/stats").json()
    assert s["digests_sent"] == 2 and s["quiet_days"] == 1
    assert s["quiet_day_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert s["items_sent"] == 4 and s["items_voted_up"] == 2 and s["items_voted_down"] == 1
    assert s["hit_rate"] == 0.5
    assert s["collect_hours_expected"] == 10 and s["collect_hours_ok"] == 9
    assert s["collect_reliability"] == 0.9
    assert s["missed_reports"] == 2


def test_stats_empty(client, sql):
    s = client.get("/stats").json()
    assert s["hit_rate"] is None and s["quiet_day_rate"] is None and s["collect_reliability"] is None


def test_responses_never_include_secrets(client, sql):
    seed(sql)
    for url in ("/digests", "/digests?approved=true", "/stats", "/interests/history", "/openapi.json", "/nope"):
        text = client.get(url).text
        for secret in ("test-cron-token", "test-webhook-secret", "123:test-bot-token", "postgresql://"):
            assert secret not in text, (url, secret)
