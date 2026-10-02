"""Queue behaviour against a real Postgres schema.

Unit tests cannot catch the failure this suite exists for: the pool registers a
jsonb codec that already serialises, so a caller that also serialises
double-encodes, and the payload reads back as a string instead of a dict. That
only shows up against a real database.

Skipped when migrations have not been applied, so the suite stays runnable on a
fresh checkout.
"""

import os
import uuid

import asyncpg
import pytest

from aypo.config import get_settings

pytestmark = pytest.mark.asyncio


async def _dsn() -> str | None:
    dsn = os.environ.get("DATABASE_URL") or get_settings().database_url
    try:
        conn = await asyncpg.connect(dsn, timeout=3)
    except Exception:
        return None
    try:
        exists = await conn.fetchval(
            "select exists (select 1 from information_schema.tables "
            "where table_name = 'collection_jobs')"
        )
    finally:
        await conn.close()
    return dsn if exists else None


@pytest.fixture
async def db(monkeypatch):
    dsn = await _dsn()
    if dsn is None:
        pytest.skip("collection_jobs not present; run `pnpm db:migrate` to enable")
    from aypo import config

    settings = config.Settings(_env_file=None, database_url=dsn, aypo_storage_backend="local")
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    from aypo.db import pool as pool_mod

    monkeypatch.setattr(pool_mod, "get_settings", lambda: settings)
    await pool_mod.close_pool()
    yield settings
    await pool_mod.close_pool()


async def test_payload_round_trips_as_a_dict(db):
    from aypo.jobs import queue

    key = f"test-{uuid.uuid4().hex}"
    await queue.enqueue("test.roundtrip", {"query": "Theme X", "limit": 25}, dedupe_key=key)
    job = await queue.claim(["test.roundtrip"])
    assert job is not None
    # The regression: a double-encoded payload reads back as str.
    assert isinstance(job.payload, dict)
    assert job.payload["limit"] == 25
    await queue.succeed(job.id, {"items": 3})


async def test_dedupe_key_suppresses_a_repeat(db):
    from aypo.jobs import queue

    key = f"test-{uuid.uuid4().hex}"
    first = await queue.enqueue("test.dedupe", {"q": "Theme X"}, dedupe_key=key)
    second = await queue.enqueue("test.dedupe", {"q": "Theme X"}, dedupe_key=key)
    assert first is not None
    # The loop regenerates overlapping queries across iterations; re-collecting
    # the same target is budget spent for nothing.
    assert second is None
    job = await queue.claim(["test.dedupe"])
    if job:
        await queue.succeed(job.id)


async def test_a_claimed_job_is_not_handed_to_a_second_worker(db):
    from aypo.jobs import queue

    await queue.enqueue("test.lease", {"q": "Theme X"}, dedupe_key=f"test-{uuid.uuid4().hex}")
    first = await queue.claim(["test.lease"])
    second = await queue.claim(["test.lease"])
    assert first is not None
    assert second is None
    await queue.succeed(first.id)


async def test_failure_retries_then_becomes_terminal(db):
    from aypo.jobs import queue

    kind = f"test.retry.{uuid.uuid4().hex[:8]}"
    await queue.enqueue(kind, {"q": "Theme X"}, max_attempts=2)

    first = await queue.claim([kind])
    await queue.fail(first.id, "provider timed out", retry_in_seconds=0)
    retried = await queue.claim([kind])
    assert retried is not None and retried.attempts == 2

    await queue.fail(retried.id, "provider timed out again")
    assert await queue.claim([kind]) is None
    stats = await queue.stats()
    assert stats.get("failed", 0) >= 1
