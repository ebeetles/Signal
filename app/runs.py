"""The runs table: one row per collect/send attempt, including skipped ones."""

from __future__ import annotations

import json

import psycopg


async def start_run(conn: psycopg.AsyncConnection, kind: str) -> int:
    row = await (
        await conn.execute("insert into runs (kind) values (%s) returning id", (kind,))
    ).fetchone()
    await conn.commit()
    return row["id"]


async def finish_run(
    conn: psycopg.AsyncConnection, run_id: int, status: str, error: str | None = None, stats: dict | None = None
) -> None:
    await conn.execute(
        "update runs set finished_at = now(), status = %s, error = %s, stats = %s::jsonb where id = %s",
        (status, (error or None) and error[:2000], json.dumps(stats or {}, default=str), run_id),
    )
    await conn.commit()


async def record_skipped(conn: psycopg.AsyncConnection, kind: str, reason: str) -> None:
    await conn.execute(
        "insert into runs (kind, finished_at, status, error) values (%s, now(), 'skipped', %s)",
        (kind, reason),
    )
    await conn.commit()


async def expire_stale_runs(conn: psycopg.AsyncConnection, kind: str) -> None:
    """Runs left 'running' by a crash or restart are marked failed."""
    await conn.execute(
        "update runs set status = 'failed', finished_at = now(), error = 'interrupted' "
        "where kind = %s and status = 'running' and started_at < now() - interval '1 hour'",
        (kind,),
    )
    await conn.commit()
