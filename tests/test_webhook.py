"""Telegram webhook: auth, chat filter, vote validation, learning, commands."""

import pytest
from fastapi.testclient import TestClient

from app import feedback, main
from app.config import LEARNED_INITIAL_WEIGHT, LEARNING_RATE, MAX_TERMS

SECRET = {"X-Telegram-Bot-Api-Secret-Token": "test-webhook-secret"}
CHAT = 4242


class FakeTelegram:
    calls: list = []

    def __init__(self, *a, **k):
        pass

    async def send_message(self, chat_id, text, *, silent=False, reply_markup=None):
        FakeTelegram.calls.append(("send", chat_id, text))
        return {"message_id": 1}

    async def answer_callback(self, callback_id, text):
        FakeTelegram.calls.append(("answer", callback_id, text))

    async def edit_markup(self, chat_id, message_id, reply_markup):
        FakeTelegram.calls.append(("edit", message_id, reply_markup))


@pytest.fixture
def client(sql, monkeypatch):
    FakeTelegram.calls = []
    monkeypatch.setattr(feedback, "Telegram", FakeTelegram)
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def digest_item(sql):
    d = sql.execute("insert into digests (digest_date, status) values ('2026-09-23', 'sent') returning id").fetchone()
    row = sql.execute(
        """insert into digest_items (digest_id, rank, score, spike, reason, headline, url, source_name,
               entity_name, matched_terms)
           values (%s, 1, 3.2, 8, '5 sources in 12h (usually 0), matches: Claude', 'Claude Opus 5.5',
               'https://www.anthropic.com/claude-opus-5-5', 'anthropic.com', 'Claude Opus 5.5',
               '{Claude,Anthropic}') returning id""",
        (d["id"],),
    ).fetchone()
    sql.execute("insert into interests (term, weight, origin) values ('Claude', 2, 'manual'), ('Anthropic', 1, 'learned')")
    return row["id"]


def vote(client, item_id, v, chat=CHAT, data=None):
    body = {
        "update_id": 1,
        "callback_query": {
            "id": "cb1",
            "from": {"id": chat, "is_bot": False, "first_name": "E"},
            "message": {"message_id": 77, "chat": {"id": chat, "type": "private"}, "date": 0},
            "data": data if data is not None else f"vote:{item_id}:{v}",
        },
    }
    return client.post("/telegram/webhook", json=body, headers=SECRET)


def message(client, text, chat=CHAT):
    body = {"update_id": 2, "message": {"message_id": 5, "date": 0, "chat": {"id": chat, "type": "private"},
                                          "from": {"id": chat, "is_bot": False, "first_name": "E"}, "text": text}}
    return client.post("/telegram/webhook", json=body, headers=SECRET)


def weights(sql):
    return {r["term"]: (round(r["weight"], 4), r["origin"]) for r in sql.execute("select * from interests")}


def last_reply():
    return [c for c in FakeTelegram.calls if c[0] == "send"][-1][2]


# --- auth and filtering -----------------------------------------------------------


@pytest.mark.parametrize("headers", [{}, {"X-Telegram-Bot-Api-Secret-Token": "nope"}])
def test_rejects_missing_or_wrong_secret(client, headers, digest_item, sql):
    r = client.post("/telegram/webhook", json={"update_id": 1}, headers=headers)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_ignores_other_chats(client, digest_item, sql):
    r = vote(client, digest_item, "up", chat=999)
    assert r.json() == {"ok": True, "result": "ignored"}
    r = message(client, "/add Evil 5", chat=999)
    assert r.json()["result"] == "ignored"
    assert FakeTelegram.calls == []
    assert sql.execute("select feedback from digest_items").fetchone()["feedback"] is None
    assert "Evil" not in weights(sql)


def test_malformed_body_is_ignored(client):
    r = client.post("/telegram/webhook", content=b"not json", headers={**SECRET, "Content-Type": "application/json"})
    assert r.status_code == 200 and r.json()["result"] == "ignored"
    r = client.post("/telegram/webhook", json={"no": "update_id"}, headers=SECRET)
    assert r.status_code == 200 and r.json()["result"] == "ignored"


# --- votes ---------------------------------------------------------------------------


def test_vote_up_records_and_learns(client, digest_item, sql):
    r = vote(client, digest_item, "up")
    assert r.json() == {"ok": True, "result": "vote"}
    row = sql.execute("select feedback, feedback_at from digest_items").fetchone()
    assert row["feedback"] == "up" and row["feedback_at"] is not None
    w = weights(sql)
    assert w["Claude"] == (2.0, "manual")  # manual terms never change
    assert w["Anthropic"] == (1.0 + LEARNING_RATE, "learned")
    assert w["Claude Opus"] == (LEARNED_INITIAL_WEIGHT, "learned")  # new learned term
    assert ("answer", "cb1", "Saved 👍") in FakeTelegram.calls
    edits = [c for c in FakeTelegram.calls if c[0] == "edit"]
    assert edits and edits[0][2]["inline_keyboard"][0][0]["text"] == "👍 ✓"


def test_repeat_vote_changes_nothing(client, digest_item, sql):
    vote(client, digest_item, "up")
    before = weights(sql)
    vote(client, digest_item, "up")
    assert weights(sql) == before
    assert ("answer", "cb1", "Already 👍") in FakeTelegram.calls


def test_later_vote_overwrites_and_reverses(client, digest_item, sql):
    vote(client, digest_item, "up")
    vote(client, digest_item, "down")
    assert sql.execute("select feedback from digest_items").fetchone()["feedback"] == "down"
    w = weights(sql)
    assert w["Anthropic"][0] == pytest.approx(1.0 - LEARNING_RATE)  # +rate then -2*rate
    assert w["Claude Opus"][0] == pytest.approx(max(0.0, LEARNED_INITIAL_WEIGHT - 2 * LEARNING_RATE))
    assert w["Claude"] == (2.0, "manual")


def test_weights_are_clamped(client, digest_item, sql):
    sql.execute("update interests set weight = 5 where term = 'Anthropic'")
    vote(client, digest_item, "up")
    assert weights(sql)["Anthropic"][0] == 5.0
    sql.execute("update interests set weight = 0.05 where term = 'Anthropic'")
    vote(client, digest_item, "down")
    assert weights(sql)["Anthropic"][0] == 0.0


def test_vote_for_unknown_item(client, digest_item, sql):
    before = weights(sql)
    vote(client, 999999, "up")
    assert ("answer", "cb1", "That item no longer exists") in FakeTelegram.calls
    assert weights(sql) == before


@pytest.mark.parametrize("data", ["vote:abc:up", "vote:1:maybe", "drop table digests", "", "vote:1:up:extra"])
def test_malformed_callback_data(client, digest_item, sql, data):
    vote(client, digest_item, "up", data=data)
    assert ("answer", "cb1", "Unknown button") in FakeTelegram.calls
    assert sql.execute("select feedback from digest_items").fetchone()["feedback"] is None


# --- commands --------------------------------------------------------------------------


def test_add_and_update_term(client, sql):
    message(client, "/add Rust")
    assert weights(sql)["Rust"] == (1.0, "manual")
    assert "Added <b>Rust</b> (1)" in last_reply()
    message(client, "/add rust 3.5")
    assert weights(sql) == {"Rust": (3.5, "manual")}
    message(client, "/add@elwin_signal_bot Claude Code 2")
    assert weights(sql)["Claude Code"] == (2.0, "manual")


def test_add_turns_learned_term_manual(client, sql):
    sql.execute("insert into interests (term, weight, origin) values ('Gemini', 0.7, 'learned')")
    message(client, "/add gemini 2")
    assert weights(sql) == {"Gemini": (2.0, "manual")}


@pytest.mark.parametrize("text", ["/add", "/add   ", "/add Rust 6", "/add Rust -1", "/add " + "x" * 61])
def test_add_validation(client, sql, text):
    message(client, text)
    assert weights(sql) == {}
    assert "Usage: /add" in last_reply()


def test_add_limit(client, sql):
    sql.execute(
        "insert into interests (term, weight, origin) select 't' || g, 1, 'manual' from generate_series(1, %s) g",
        (MAX_TERMS,),
    )
    message(client, "/add OneTooMany")
    assert "OneTooMany" not in weights(sql)
    assert "already have 100 terms" in last_reply()
    message(client, "/add t1 4")  # updating an existing term is still allowed
    assert weights(sql)["t1"] == (4.0, "manual")


def test_add_escapes_term_in_reply(client, sql):
    message(client, "/add <b>x</b>")
    assert "&lt;b&gt;x&lt;/b&gt;" in last_reply()


def test_remove(client, sql):
    message(client, "/add Rust")
    message(client, "/remove RUST")
    assert weights(sql) == {}
    assert "Removed" in last_reply()
    message(client, "/remove Nothing")
    assert "No term named" in last_reply()


def test_interests_lists_terms(client, digest_item, sql):
    message(client, "/interests")
    reply = last_reply()
    assert "Claude: 2" in reply and "Anthropic: 1 (learned)" in reply and "(2/100)" in reply


def test_missed(client, sql):
    message(client, "/missed The Opus 5.5 launch")
    assert sql.execute("select text from missed").fetchone()["text"] == "The Opus 5.5 launch"
    assert "1 missed report so far" in last_reply()
    message(client, "/missed")
    assert "Usage: /missed" in last_reply()
    message(client, "/missed " + "y" * 501)
    assert sql.execute("select count(*) as n from missed").fetchone()["n"] == 1


def test_unknown_command_shows_help(client, sql):
    message(client, "hello")
    assert "/interests" in last_reply()
