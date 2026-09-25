import os

# Tests never read .env and never touch production.
os.environ["SIGNAL_SKIP_DOTENV"] = "1"
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "postgresql://signal@localhost:54329/signal_test")
os.environ["CRON_TOKEN"] = "test-cron-token"
os.environ["TELEGRAM_WEBHOOK_SECRET"] = "test-webhook-secret"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test-bot-token"
os.environ["TELEGRAM_CHAT_ID"] = "4242"
os.environ["PORTFOLIO_ORIGIN"] = "https://ebeetles.github.io"
os.environ["TIMEZONE"] = "America/New_York"

import psycopg
import pytest

from app.migrate import apply_migrations

TABLES = [
    "digest_items", "digests", "mentions", "entity_counts", "items", "entities",
    "runs", "interests", "interest_history", "missed", "sources",
]


@pytest.fixture(scope="session")
def database_url():
    url = os.environ["DATABASE_URL"]
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("drop schema public cascade; create schema public;")
    apply_migrations(url)
    return url


@pytest.fixture
def clean_db(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("truncate " + ", ".join(TABLES) + " restart identity cascade")
    yield database_url


@pytest.fixture
def sql(clean_db):
    """Synchronous helper connection for arranging and asserting DB state."""
    from psycopg.rows import dict_row

    with psycopg.connect(clean_db, autocommit=True, row_factory=dict_row) as conn:
        conn.execute("set time zone 'UTC'")
        yield conn
