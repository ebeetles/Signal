"""Ops endpoints: /health, /collect (and /send) auth and 202 behavior."""

import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture
def client(monkeypatch):
    calls = []

    async def fake_collect():
        calls.append("collect")

    monkeypatch.setattr(main, "run_collect", fake_collect)
    with TestClient(main.app) as c:
        c.calls = calls
        yield c


def test_health_is_fast_and_dbless(client, monkeypatch):
    async def boom():
        raise AssertionError("health must not touch the database")

    monkeypatch.setattr(main.db, "get_pool", boom)
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "test-cron-token"},
     {"Authorization": "Basic test-cron-token"}, {"Authorization": "Bearer "}],
)
def test_collect_rejects_bad_tokens(client, headers):
    r = client.post("/collect", headers=headers)
    assert r.status_code == 401
    assert r.json() == {"error": {"code": "unauthorized", "message": "Missing or invalid bearer token"}}
    assert client.calls == []


def test_collect_accepts_and_runs_in_background(client):
    r = client.post("/collect", headers={"Authorization": "Bearer test-cron-token"})
    assert r.status_code == 202
    assert r.json() == {"status": "accepted", "job": "collect"}
    assert client.calls == ["collect"]


def test_collect_get_not_allowed(client):
    r = client.get("/collect")
    assert r.status_code == 405
    assert r.json()["error"]["code"] == "method_not_allowed"
