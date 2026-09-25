"""Send pipeline against the test DB with a fake Telegram."""

from datetime import timedelta

import pytest

from app import collect, send
from app.telegram import TelegramError
from tests.helpers import SOURCES, fake_fetchers, item, run, utc

NOW = utc(2026, 9, 23, 11, 0, 5)  # 07:00:05 America/New_York
RELEASE = utc(2026, 9, 22, 16, 27)


class FakeTelegram:
    def __init__(self, fail_on: int | None = None):
        self.sent = []
        self.fail_on = fail_on

    async def send_message(self, chat_id, text, *, silent=False, reply_markup=None):
        if self.fail_on is not None and len(self.sent) == self.fail_on:
            raise TelegramError("sendMessage: Bad Request: chat not found")
        self.sent.append({"chat_id": chat_id, "text": text, "silent": silent, "markup": reply_markup})
        return {"message_id": len(self.sent)}


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(collect, "load_sources_file", lambda: [dict(s) for s in SOURCES])
    monkeypatch.setattr(collect, "load_aliases_file", lambda: {})
    monkeypatch.setattr(send, "load_primary_domains", lambda: {"anthropic.com"})
    monkeypatch.setattr(send.asyncio, "sleep", _nosleep)
    monkeypatch.setattr("app.scoring.SCORE_THRESHOLD", 0.5)


async def _nosleep(_):
    return None


def seed_runs(sql, days=15):
    """Successful hourly collect runs covering the baseline and window."""
    start = NOW.replace(minute=0, second=0) - timedelta(days=days)
    sql.execute(
        "insert into runs (kind, started_at, finished_at, status) "
        "select 'collect', h + interval '50 minutes', h + interval '51 minutes', 'success' "
        "from generate_series(%s::timestamptz, %s::timestamptz, interval '1 hour') h",
        (start, NOW - timedelta(hours=1)),
    )


def opus_items():
    titles = [
        ("https://www.anthropic.com/claude-opus-5-5", "Claude Opus 5.5", "anthropic.com"),
        ("https://artificialanalysis.ai/models/claude-opus-5-5", "Claude Opus 5.5 Intelligence, Performance and Price Analysis", "artificialanalysis.ai"),
        ("https://www.theverge.com/ai/998868/anthropic-claude-opus-5-5", "Anthropic launches Claude Opus 5.5 with stricter safeguards", "theverge.com"),
        ("https://simonwillison.net/2026/Sep/22/opus/", "Claude Opus 5.5, GPT-6 Sol, and a new price war", "simonwillison.net"),
        ("https://evil.example/x", "Opus 5.5 <script>alert(1)</script> & friends", "evil.example"),
    ]
    return [
        item(f"https://news.ycombinator.com/item?id={100 + n}", t, RELEASE + timedelta(minutes=10 * n), o, link=u)
        for n, (u, t, o) in enumerate(titles)
    ]


def collect_now(data, now=NOW):
    return run(collect.run_collect(fetchers=fake_fetchers(data), now=now))


def test_send_delivers_digest(sql):
    seed_runs(sql)
    sql.execute("insert into interests (term, weight, origin) values ('Claude', 1.0, 'manual')")
    collect_now({"HN": opus_items()})
    tg = FakeTelegram()
    result = run(send.run_send(now=NOW, tg=tg))
    assert result["status"] == "success" and result["picked"] >= 1

    digest = sql.execute("select * from digests").fetchone()
    assert digest["status"] == "sent" and str(digest["digest_date"]) == "2026-09-23"
    first = sql.execute("select * from digest_items order by rank").fetchone()
    assert first["entity_name"] == "Claude Opus 5.5"
    assert first["url"] == "https://www.anthropic.com/claude-opus-5-5"  # primary domain wins
    assert first["source_name"] == "anthropic.com"
    assert first["reason"].startswith("5 sources in ")
    assert "matches: Claude" in first["reason"]
    assert first["matched_terms"] == ["Claude"]

    header, *items = tg.sent
    assert header["text"].endswith("today") and header["silent"] is False
    assert all(m["silent"] for m in items)
    assert len(items) == result["picked"]
    cb = items[0]["markup"]["inline_keyboard"][0]
    assert cb[0]["callback_data"] == f"vote:{first['id']}:up"
    assert cb[1]["callback_data"] == f"vote:{first['id']}:down"

    hist = sql.execute("select term, weight, day from interest_history").fetchall()
    assert [(h["term"], h["weight"], str(h["day"])) for h in hist] == [("Claude", 1.0, "2026-09-23")]


def test_second_send_same_day_is_skipped(sql):
    seed_runs(sql)
    collect_now({"HN": opus_items()})
    tg = FakeTelegram()
    run(send.run_send(now=NOW, tg=tg))
    n = len(tg.sent)
    again = run(send.run_send(now=NOW + timedelta(minutes=5), tg=tg))
    assert again["status"] == "skipped"
    assert len(tg.sent) == n
    assert sql.execute("select count(*) as n from digests").fetchone()["n"] == 1
    statuses = [r["status"] for r in sql.execute("select status from runs where kind='send' order by id")]
    assert statuses == ["success", "skipped"]


def test_quiet_day(sql):
    seed_runs(sql)
    collect_now({"HN": opus_items()[:2]})  # only 2 origins: below MIN_DISTINCT_SOURCES
    tg = FakeTelegram()
    result = run(send.run_send(now=NOW, tg=tg))
    assert result["status"] == "success" and result["picked"] == 0
    assert [m["text"] for m in tg.sent] == ["Nothing big today."]
    assert sql.execute("select status from digests").fetchone()["status"] == "quiet"


def test_telegram_failure_marks_failed_and_retry_succeeds(sql):
    seed_runs(sql)
    collect_now({"HN": opus_items()})
    bad = FakeTelegram(fail_on=0)
    result = run(send.run_send(now=NOW, tg=bad))
    assert result["status"] == "failed" and "chat not found" in result["error"]
    assert sql.execute("select status from digests").fetchone()["status"] == "failed"
    good = FakeTelegram()
    result = run(send.run_send(now=NOW + timedelta(minutes=10), tg=good))
    assert result["status"] == "success"
    assert sql.execute("select count(*) as n from digests").fetchone()["n"] == 1
    assert sql.execute("select status from digests").fetchone()["status"] == "sent"
    n_items = sql.execute("select count(*) as n from digest_items").fetchone()["n"]
    assert n_items == len(good.sent) - 1  # no leftovers from the failed attempt


def test_fetched_text_is_escaped(sql):
    seed_runs(sql)
    sql.execute("insert into interests (term, weight, origin) values ('Opus', 1.0, 'manual')")
    evil = [
        item(f"https://news.ycombinator.com/item?id={n}", f'Opus 5.5 <b>bold</b> "quoted" & more #{n}',
             RELEASE + timedelta(minutes=n), f"site{n}.com", link=f"https://site{n}.com/a?x=1&y=\"2\"")
        for n in range(4)
    ]
    collect_now({"HN": evil})
    tg = FakeTelegram()
    run(send.run_send(now=NOW, tg=tg))
    body = tg.sent[1]["text"]
    assert "<b>bold</b>" not in body and "&lt;b&gt;bold&lt;/b&gt;" in body
    assert "&quot;quoted&quot; &amp; more" in body
    assert 'href="https://site' in body and "&amp;y=&quot;2&quot;" in body


def test_no_repeat_next_day(sql):
    seed_runs(sql, days=16)
    collect_now({"HN": opus_items()})
    run(send.run_send(now=NOW - timedelta(days=0), tg=FakeTelegram()))
    # next morning: same entity, a few more mentions but spike not doubled
    more = [
        item(f"https://news.ycombinator.com/item?id={200 + n}", "Opus 5.5 follow-up", NOW + timedelta(hours=2 + n),
             f"later{n}.com")
        for n in range(3)
    ]
    sql.execute(
        "insert into runs (kind, started_at, finished_at, status) "
        "select 'collect', h, h, 'success' from generate_series(%s::timestamptz, %s::timestamptz, interval '1 hour') h",
        (NOW, NOW + timedelta(hours=23)),
    )
    collect_now({"HN": more}, now=NOW + timedelta(days=1))
    tg = FakeTelegram()
    run(send.run_send(now=NOW + timedelta(days=1), tg=tg))
    names = [r["entity_name"] for r in sql.execute(
        "select entity_name from digest_items di join digests d on d.id = di.digest_id where d.digest_date = '2026-09-24'"
    )]
    assert "Claude Opus 5.5" not in names
