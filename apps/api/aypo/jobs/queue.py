"""Postgres-backed job queue.

Brief §30 allows "Celery + Redis, or another lightweight managed queue if
simpler". There is no Redis in the target environment and collection/analysis
work is coarse-grained and long-running rather than high-frequency, so the
database we already depend on is the simpler queue: one table, SKIP LOCKED for
safe concurrent claiming, and visibility timeouts so a crashed worker's job is
retried instead of lost.

This keeps deployment to two processes (api + worker) and keeps every job's
state auditable in SQL alongside the research run it belongs to, which the
research-run view in brief §32 needs anyway.
"""

from __future__ import annotations

import asyncio
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog

from aypo.db.pool import connection, transaction

log = structlog.get_logger(__name__)

WORKER_ID = f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"


@dataclass(slots=True)
class Job:
    id: uuid.UUID
    kind: str
    payload: dict[str, Any]
    research_run_id: uuid.UUID | None
    attempts: int
    max_attempts: int
    priority: int
    created_at: datetime


async def enqueue(
    kind: str,
    payload: dict[str, Any],
    *,
    research_run_id: uuid.UUID | None = None,
    priority: int = 100,
    max_attempts: int = 3,
    dedupe_key: str | None = None,
) -> uuid.UUID | None:
    """Enqueue a job. Returns None when dedupe_key collides with a live job.

    Deduplication matters because the research loop regenerates overlapping
    queries across iterations; re-collecting the same target wastes budget.
    """
    async with transaction() as conn:
        if dedupe_key is not None:
            existing = await conn.fetchval(
                """
                SELECT id FROM collection_jobs
                 WHERE dedupe_key = $1 AND status IN ('pending', 'running', 'succeeded')
                 LIMIT 1
                """,
                dedupe_key,
            )
            if existing is not None:
                return None
        return await conn.fetchval(
            """
            INSERT INTO collection_jobs
                (kind, payload, research_run_id, priority, max_attempts, dedupe_key, status)
            VALUES ($1, $2, $3, $4, $5, $6, 'pending')
            RETURNING id
            """,
            kind,
            payload,
            research_run_id,
            priority,
            max_attempts,
            dedupe_key,
        )


async def claim(kinds: list[str] | None = None, *, lease_seconds: int = 1800) -> Job | None:
    """Atomically claim one runnable job.

    FOR UPDATE SKIP LOCKED lets N workers pull from the same table without
    blocking each other or handing the same job to two workers.
    """
    kind_filter = "AND kind = ANY($1::text[])" if kinds else ""
    args: list[Any] = [kinds] if kinds else []
    async with transaction() as conn:
        row = await conn.fetchrow(
            f"""
            WITH claimed AS (
                SELECT id FROM collection_jobs
                 WHERE (
                        status = 'pending'
                        -- a lease that expired means the worker died mid-job
                        OR (status = 'running' AND lease_expires_at < now())
                       )
                   AND (run_after IS NULL OR run_after <= now())
                   AND attempts < max_attempts
                   {kind_filter}
                 ORDER BY priority ASC, created_at ASC
                 FOR UPDATE SKIP LOCKED
                 LIMIT 1
            )
            UPDATE collection_jobs j
               SET status = 'running',
                   attempts = j.attempts + 1,
                   claimed_by = '{WORKER_ID}',
                   started_at = now(),
                   lease_expires_at = now() + interval '{lease_seconds} seconds'
              FROM claimed
             WHERE j.id = claimed.id
            RETURNING j.id, j.kind, j.payload, j.research_run_id, j.attempts,
                      j.max_attempts, j.priority, j.created_at
            """,
            *args,
        )
    if row is None:
        return None
    return Job(
        id=row["id"],
        kind=row["kind"],
        payload=row["payload"] or {},
        research_run_id=row["research_run_id"],
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        priority=row["priority"],
        created_at=row["created_at"],
    )


async def heartbeat(job_id: uuid.UUID, *, lease_seconds: int = 1800) -> None:
    """Extend a lease. Long collection runs must not look crashed."""
    async with connection() as conn:
        await conn.execute(
            "UPDATE collection_jobs SET lease_expires_at = now() + "
            f"interval '{lease_seconds} seconds' WHERE id = $1",
            job_id,
        )


async def succeed(job_id: uuid.UUID, result: dict[str, Any] | None = None) -> None:
    async with connection() as conn:
        await conn.execute(
            """
            UPDATE collection_jobs
               SET status = 'succeeded', finished_at = now(), result = $2, last_error = NULL
             WHERE id = $1
            """,
            job_id,
            result or {},
        )


async def fail(job_id: uuid.UUID, error: str, *, retry_in_seconds: int | None = None) -> None:
    """Mark a job failed.

    A job below max_attempts goes back to pending with a backoff; at the cap it
    is terminal. Either way the error text is kept, because brief §7 requires a
    run to continue past a dead source and the UI must still show what broke.
    """
    async with connection() as conn:
        await conn.execute(
            """
            UPDATE collection_jobs
               SET status = CASE
                       WHEN attempts >= max_attempts THEN 'failed'
                       ELSE 'pending'
                   END,
                   last_error = $2,
                   finished_at = CASE WHEN attempts >= max_attempts THEN now() ELSE NULL END,
                   run_after = CASE
                       WHEN attempts >= max_attempts THEN NULL
                       ELSE now() + make_interval(secs => $3::double precision)
                   END,
                   lease_expires_at = NULL
             WHERE id = $1
            """,
            job_id,
            error[:4000],
            float(retry_in_seconds if retry_in_seconds is not None else 60),
        )


async def stats(research_run_id: uuid.UUID | None = None) -> dict[str, int]:
    clause = "WHERE research_run_id = $1" if research_run_id else ""
    args = [research_run_id] if research_run_id else []
    async with connection() as conn:
        rows = await conn.fetch(
            f"SELECT status, count(*) AS n FROM collection_jobs {clause} GROUP BY status", *args
        )
    return {row["status"]: row["n"] for row in rows}


async def drain(
    handlers: dict[str, Any],
    *,
    concurrency: int = 4,
    poll_interval: float = 2.0,
    stop_when_empty: bool = False,
) -> None:
    """Run the worker loop until cancelled (or until the queue empties)."""
    semaphore = asyncio.Semaphore(concurrency)
    running: set[asyncio.Task[None]] = set()

    async def run_one(job: Job) -> None:
        handler = handlers.get(job.kind)
        try:
            if handler is None:
                await fail(job.id, f"no handler registered for kind={job.kind}")
                return
            hb = asyncio.create_task(_heartbeat_loop(job.id))
            try:
                result = await handler(job)
            finally:
                hb.cancel()
            await succeed(job.id, result if isinstance(result, dict) else None)
        except asyncio.CancelledError:
            await fail(job.id, "worker cancelled", retry_in_seconds=10)
            raise
        except Exception as exc:
            log.warning("job.failed", job_id=str(job.id), kind=job.kind, error=str(exc))
            await fail(job.id, f"{type(exc).__name__}: {exc}", retry_in_seconds=60 * job.attempts)
        finally:
            semaphore.release()

    while True:
        await semaphore.acquire()
        job = await claim(list(handlers))
        if job is None:
            semaphore.release()
            if stop_when_empty and not running:
                return
            await asyncio.sleep(poll_interval)
            continue
        task = asyncio.create_task(run_one(job))
        running.add(task)
        task.add_done_callback(running.discard)


async def _heartbeat_loop(job_id: uuid.UUID, interval: float = 300.0) -> None:
    while True:
        await asyncio.sleep(interval)
        await heartbeat(job_id)
