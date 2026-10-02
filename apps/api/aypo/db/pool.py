"""Postgres access.

One asyncpg pool per process. pgvector's codec is registered on every new
connection so embedding columns round-trip as numpy arrays rather than strings.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import structlog
from pgvector.asyncpg import register_vector

from aypo.config import get_settings

log = structlog.get_logger(__name__)

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    # JSONB is used wherever the taxonomy is intentionally open (brief §29), so
    # it is decoded to dict/list automatically instead of leaving callers to
    # json.loads every read.
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    try:
        await register_vector(conn)
    except Exception as exc:  # pragma: no cover - extension not yet installed
        # Migrations create the extension; a pool opened before that must still
        # work so the migration runner itself can connect.
        log.debug("db.pgvector_codec_unavailable", error=str(exc))


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=settings.aypo_db_pool_min,
            max_size=settings.aypo_db_pool_max,
            init=_init_connection,
            command_timeout=120,
        )
        log.info("db.pool_open", min=settings.aypo_db_pool_min, max=settings.aypo_db_pool_max)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        log.info("db.pool_closed")


@asynccontextmanager
async def connection() -> AsyncIterator[asyncpg.Connection]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn


@asynccontextmanager
async def transaction() -> AsyncIterator[asyncpg.Connection]:
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        yield conn


async def fetch(query: str, *args: Any) -> list[asyncpg.Record]:
    async with connection() as conn:
        return await conn.fetch(query, *args)


async def fetchrow(query: str, *args: Any) -> asyncpg.Record | None:
    async with connection() as conn:
        return await conn.fetchrow(query, *args)


async def fetchval(query: str, *args: Any) -> Any:
    async with connection() as conn:
        return await conn.fetchval(query, *args)


async def execute(query: str, *args: Any) -> str:
    async with connection() as conn:
        return await conn.execute(query, *args)


async def healthcheck() -> dict[str, Any]:
    """Reports whether the database is reachable and correctly provisioned."""
    try:
        async with connection() as conn:
            version = await conn.fetchval("SELECT version()")
            has_vector = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"
            )
            applied = await conn.fetchval(
                "SELECT count(*) FROM _aypo_migrations"
                if await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = '_aypo_migrations')"
                )
                else "SELECT 0"
            )
        return {
            "ok": True,
            "postgres": str(version).split(" on ")[0],
            "pgvector": bool(has_vector),
            "migrations_applied": int(applied or 0),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
