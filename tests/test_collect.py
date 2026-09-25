"""Collect pipeline: idempotency, advisory lock, failures, retention, rollups."""

import math
from datetime import timedelta

import psycopg
import pytest

from app import collect
from app.config import COLLECT_LOCK_KEY
from tests.helpers import SOURCES, SourceError, fake_fetchers, item, run, utc

NOW = utc(2026, 9, 23, 10, 30)
H = utc(2026, 9, 23, 9)  # an hour bucket


@pytest.fixture(autouse=True)
def small_source_list(monkeypatch):
    monkeypatch.setattr(collect, "load_sources_file", lambda: [dict(s) for s in SOURCES])
    monkeypatch.setattr(collect, "load_aliases_file", lambda: {})


def canned():
    return {
        "HN": [
            item("https://news.ycombinator.com/item?id=1", "Claude Opus 5.5", H + timedelta(minutes=5),
                 "anthropic.com", link="https://www.anthropic.com/claude-opus-5-5"),
            item("https://news.ycombinator.com/item?id=2", "Anthropic launches Claude Opus 5.5 with safeguards",
                 H + timedelta(minutes=20), "theverge.com"),
            item("https://news.ycombinator.com/item?id=3", "Opus 5.5 is good at explainer videos",
                 H + timedelta(minutes=40), "theverge.com"),
        ],
        "Blog": [item("https://blog.example/opus", "Introducing Claude Opus 5.5", H + timedelta(minutes=1), "blog.example")],
        "News": [item("https://news.example/old", "Ancient Rome story", NOW - timedelta(days=90), "news.example")],
    }


def counts(sql):
    return {
        t: sql.execute(f"select count(*) as n from {t}").fetchone()["n"]
        for t in ("items", "mentions", "entities", "entity_counts")
    }


def test_collect_stores_items_mentions_and_counts(sql):
    result = run(collect.run_collect(fetchers=fake_fetchers(canned()), now=NOW))
    assert result["status"] == "success"
    assert result["items_new"] == 4  # the 90-day-old item is outside retention
    opus = sql.execute("select id, name from entities where norm = 'opus 5.5'").fetchone()
    assert opus is not None
    n = sql.execute("select count(*) as n from mentions where entity_id = %s", (opus["id"],)).fetchone()["n"]
    assert n == 4


def test_collect_twice_adds_nothing(sql):
    run(collect.run_collect(fetchers=fake_fetchers(canned()), now=NOW))
    before = counts(sql)
    snapshot = sql.execute("select * from entity_counts order by entity_id, hour_bucket").fetchall()
    result = run(collect.run_collect(fetchers=fake_fetchers(canned()), now=NOW))
    assert result["items_new"] == 0 and result["mentions"] == 0
    assert counts(sql) == before
    assert sql.execute("select * from entity_counts order by entity_id, hour_bucket").fetchall() == snapshot
    runs = sql.execute("select status from runs where kind = 'collect' order by id").fetchall()
    assert [r["status"] for r in runs] == ["success", "success"]


def test_weighted_count_dampens_repeat_origins(sql):
    run(collect.run_collect(fetchers=fake_fetchers(canned()), now=NOW))
    row = sql.execute(
        "select weighted_count, distinct_sources from entity_counts ec join entities e on e.id = ec.entity_id "
        "where e.norm = 'opus 5.5' and hour_bucket = %s",
        (H,),
    ).fetchone()
    # origins in hour H: anthropic.com (1 item), theverge.com (2 items), blog.example (1 item)
    assert row["distinct_sources"] == 3
    assert row["weighted_count"] == pytest.approx(1 + (1 + math.log(2)) + 1, rel=1e-5)


def test_failing_source_does_not_fail_run(sql):
    data = canned()
    data["Blog"] = SourceError("HTTP 404")
    result = run(collect.run_collect(fetchers=fake_fetchers(data), now=NOW))
    assert result["status"] == "partial"
    assert result["items_new"] == 3
    r = sql.execute("select status, error from runs order by id desc limit 1").fetchone()
    assert r["status"] == "partial" and "Blog: HTTP 404" in r["error"]


def test_crashing_fetcher_is_contained(sql):
    data = canned()
    data["News"] = RuntimeError("boom")
    result = run(collect.run_collect(fetchers=fake_fetchers(data), now=NOW))
    assert result["status"] == "partial"
    err = sql.execute("select error from runs order by id desc limit 1").fetchone()["error"]
    assert "News: error (RuntimeError)" in err and "boom" not in err


def test_all_sources_failing_marks_run_failed(sql):
    data = {name: SourceError("down") for name in ("HN", "Blog", "News")}
    result = run(collect.run_collect(fetchers=fake_fetchers(data), now=NOW))
    assert result["status"] == "failed"


def test_advisory_lock_skips_overlapping_run(sql, clean_db):
    with psycopg.connect(clean_db, autocommit=True) as holder:
        holder.execute("select pg_advisory_lock(%s)", (COLLECT_LOCK_KEY,))
        result = run(collect.run_collect(fetchers=fake_fetchers(canned()), now=NOW))
        holder.execute("select pg_advisory_unlock(%s)", (COLLECT_LOCK_KEY,))
    assert result == {"status": "skipped"}
    assert counts(sql)["items"] == 0
    r = sql.execute("select kind, status, error from runs").fetchone()
    assert (r["kind"], r["status"]) == ("collect", "skipped")
    # and the lock is released afterwards
    assert run(collect.run_collect(fetchers=fake_fetchers(canned()), now=NOW))["status"] == "success"


def test_prune_deletes_old_items_and_mentions(sql):
    run(collect.run_collect(fetchers=fake_fetchers(canned()), now=NOW))
    later = NOW + timedelta(days=61)
    run(collect.run_collect(fetchers=fake_fetchers({}), now=later))
    assert counts(sql)["items"] == 0
    assert counts(sql)["mentions"] == 0
    assert counts(sql)["entity_counts"] > 0  # rollups are kept


def test_future_dates_are_clamped(sql):
    data = {"Blog": [item("https://blog.example/f", "Gemini 4 preview", NOW + timedelta(days=3), "blog.example")]}
    run(collect.run_collect(fetchers=fake_fetchers(data), now=NOW))
    published = sql.execute("select published_at from items").fetchone()["published_at"]
    assert published == NOW


def test_sources_sync_deactivates_removed_sources(sql, monkeypatch):
    run(collect.run_collect(fetchers=fake_fetchers({}), now=NOW))
    monkeypatch.setattr(collect, "load_sources_file", lambda: [dict(SOURCES[0])])
    run(collect.run_collect(fetchers=fake_fetchers({}), now=NOW))
    active = {r["name"]: r["active"] for r in sql.execute("select name, active from sources")}
    assert active == {"HN": True, "Blog": False, "News": False}


def test_weak_candidates_never_create_entities(sql):
    data = {"HN": [item("https://news.ycombinator.com/item?id=9", "Thieves Stole Trailers Of Sand",
                        H, "example.com")]}
    run(collect.run_collect(fetchers=fake_fetchers(data), now=NOW))
    assert counts(sql)["entities"] == 0


def test_weak_candidate_links_to_existing_entity(sql):
    sql.execute("insert into entities (name, norm) values ('Nvidia', 'nvidia')")
    data = {"HN": [item("https://news.ycombinator.com/item?id=9", "Thieves Stole Nvidia Trailers", H, "example.com")]}
    run(collect.run_collect(fetchers=fake_fetchers(data), now=NOW))
    assert counts(sql)["mentions"] == 1


def test_aliases_file_merges_names(sql, monkeypatch):
    monkeypatch.setattr(collect, "load_aliases_file", lambda: {"Claude Code": ["claude-code"]})
    data = {"HN": [
        item("https://news.ycombinator.com/item?id=5", "Tips for claude-code users", H, "a.com"),
        item("https://news.ycombinator.com/item?id=6", "Why I like Claude Code", H, "b.com"),
    ]}
    run(collect.run_collect(fetchers=fake_fetchers(data), now=NOW))
    rows = sql.execute(
        "select e.norm, count(*) as n from mentions m join entities e on e.id = m.entity_id group by 1"
    ).fetchall()
    assert {r["norm"]: r["n"] for r in rows} == {"claude code": 2}
