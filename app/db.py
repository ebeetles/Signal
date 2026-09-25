"""Postgres connection pool (psycopg 3, async).

The pool opens lazily so /health and app startup never depend on the
database. Every session runs in UTC so hour buckets are unambiguous.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import get_settings

log = logging.getLogger(__name__)

_pool: AsyncConnectionPool | None = None


class DatabaseUnavailable(RuntimeError):
    pass


async def _configure(conn: psycopg.AsyncConnection) -> None:
    await conn.execute("set time zone 'UTC'")
    await conn.commit()


def conninfo_kwargs() -> dict:
    # prepare_threshold=None: no server-side prepared statements, so the
    # Supabase poolers (session or transaction mode) never trip over them.
    return {"prepare_threshold": None, "row_factory": dict_row}


async def get_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        url = get_settings().database_url
        if not url:
            raise DatabaseUnavailable("DATABASE_URL is not set")
        _pool = AsyncConnectionPool(
            url,
            min_size=1,
            max_size=4,
            kwargs=conninfo_kwargs(),
            configure=_configure,
            open=False,
            timeout=10,
            max_idle=300,
        )
        await _pool.open(wait=False)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def connection() -> AsyncIterator[psycopg.AsyncConnection]:
    pool = await get_pool()
    async with pool.connection() as conn:
        yield conn
