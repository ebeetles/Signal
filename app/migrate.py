"""Apply migrations/*.sql in filename order, once each.

Run with `python -m app.migrate` (Render runs it in the build command).
Each file is applied in its own transaction and recorded in
schema_migrations. An advisory lock stops two runners from racing.
"""

from __future__ import annotations

import logging
import sys

import psycopg

from app.config import MIGRATE_LOCK_KEY, ROOT, get_settings

log = logging.getLogger("signal.migrate")

MIGRATIONS_DIR = ROOT / "migrations"


def apply_migrations(database_url: str) -> list[str]:
    applied: list[str] = []
    with psycopg.connect(database_url, prepare_threshold=None, autocommit=True) as conn:
        conn.execute("select pg_advisory_lock(%s)", (MIGRATE_LOCK_KEY,))
        try:
            conn.execute(
                "create table if not exists schema_migrations ("
                " filename text primary key,"
                " applied_at timestamptz not null default now())"
            )
            done = {r[0] for r in conn.execute("select filename from schema_migrations")}
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if path.name in done:
                    continue
                with conn.transaction():
                    conn.execute(path.read_text())
                    conn.execute(
                        "insert into schema_migrations (filename) values (%s)", (path.name,)
                    )
                applied.append(path.name)
                log.info("applied %s", path.name)
        finally:
            conn.execute("select pg_advisory_unlock(%s)", (MIGRATE_LOCK_KEY,))
    return applied


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    url = get_settings().database_url
    if not url:
        log.warning("DATABASE_URL is not set; skipping migrations")
        return 0
    applied = apply_migrations(url)
    log.info("migrations up to date (%d applied now)", len(applied))
    return 0


if __name__ == "__main__":
    sys.exit(main())
