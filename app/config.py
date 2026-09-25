"""Settings from environment variables, plus every tunable constant.

Secrets are read from the environment only (Render env vars in production,
a gitignored .env locally). Nothing here should ever be logged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Load ROOT/.env for local runs. Real env vars always win."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    database_url: str | None
    telegram_bot_token: str | None
    telegram_chat_id: int | None
    telegram_webhook_secret: str | None
    cron_token: str | None
    portfolio_origin: str | None
    timezone: str

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache
def get_settings() -> Settings:
    if os.environ.get("SIGNAL_SKIP_DOTENV") != "1":
        _load_dotenv()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or None
    return Settings(
        database_url=os.environ.get("DATABASE_URL") or None,
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=int(chat_id) if chat_id else None,
        telegram_webhook_secret=os.environ.get("TELEGRAM_WEBHOOK_SECRET") or None,
        cron_token=os.environ.get("CRON_TOKEN") or None,
        portfolio_origin=(os.environ.get("PORTFOLIO_ORIGIN") or "").rstrip("/") or None,
        timezone=os.environ.get("TIMEZONE") or "America/New_York",
    )


# --- Collect -----------------------------------------------------------------

USER_AGENT = "SignalBot/1.0 (personal morning brief; +https://github.com/ebeetles/Signal)"
HTTP_TIMEOUT_SECONDS = 15.0
FETCH_CONCURRENCY = 10  # max simultaneous HN item requests
HN_TOP_LIMIT = 100  # top stories considered per collect
HN_NEW_LIMIT = 200  # newest stories considered per collect (~3-4h of submissions)
RETENTION_DAYS = 60  # items and mentions older than this are deleted
MAX_TITLE_CHARS = 300
SOURCES_FILE = ROOT / "seeds" / "sources.yaml"
ALIASES_FILE = ROOT / "seeds" / "aliases.yaml"

# --- Scoring -----------------------------------------------------------------

WINDOW_HOURS = 24  # "recent" window compared against the baseline
BASELINE_DAYS = 14  # days of history that define normal
MIN_COVERED_HOURS_PER_DAY = 12  # baseline days with fewer covered hours are skipped
MIN_DISTINCT_SOURCES = 3  # independent origins required in the window
INTEREST_FLOOR = 0.1  # interest score for topics matching no interest term
MATCH_MIN = 0.05  # weighted similarity needed to list a term in "matches:"
SCORE_THRESHOLD = 1.5  # final score needed to be sent
TOP_N = 5  # max items per digest
NO_REPEAT_DAYS = 3  # don't resend an entity within this many days...
REPEAT_SPIKE_MULTIPLIER = 2.0  # ...unless its spike is at least this many times higher
OVERLAP_MAX = 0.5  # skip a pick sharing more than this share of items with a better pick
CANDIDATE_LIMIT = 300  # max entities scored per run (by recent weighted count)

# --- Interests and learning --------------------------------------------------

DEFAULT_TERM_WEIGHT = 1.0
MIN_WEIGHT = 0.0
WEIGHT_CAP = 5.0
MAX_TERMS = 100
MAX_TERM_CHARS = 60
LEARNING_RATE = 0.2  # weight change per vote on a matched learned term
LEARNED_INITIAL_WEIGHT = 0.5  # weight of a term created by a thumbs up
MAX_MISSED_CHARS = 500

# --- Public API --------------------------------------------------------------

DIGESTS_DEFAULT_LIMIT = 10
DIGESTS_MAX_LIMIT = 30
HISTORY_DEFAULT_DAYS = 30
HISTORY_MAX_DAYS = 365
RATE_LIMIT_REQUESTS = 60  # per client IP...
RATE_LIMIT_WINDOW_SECONDS = 60  # ...per this many seconds

# --- Postgres advisory lock keys ---------------------------------------------

COLLECT_LOCK_KEY = 7_311_001
SEND_LOCK_KEY = 7_311_002
MIGRATE_LOCK_KEY = 7_311_003
