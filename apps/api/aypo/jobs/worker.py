"""Worker process entrypoint.

Run with:  uv run python -m aypo.jobs.worker

Handlers are registered here rather than discovered, so the set of job kinds a
deployment can execute is explicit and greppable.
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any

import structlog

from aypo.config import get_settings
from aypo.db.pool import close_pool
from aypo.jobs.queue import drain
from aypo.logging import configure_logging

log = structlog.get_logger(__name__)


def build_handlers() -> dict[str, Any]:
    """Job kind -> coroutine handler.

    Imported lazily so a worker still starts when an optional subsystem's
    dependencies are missing; the affected kinds simply go unregistered and
    their jobs report "no handler registered" rather than crashing the worker.
    """
    handlers: dict[str, Any] = {}
    try:
        from aypo.collectors.handlers import COLLECTION_HANDLERS

        handlers.update(COLLECTION_HANDLERS)
    except Exception as exc:
        log.warning("worker.collection_handlers_unavailable", error=str(exc))
    try:
        from aypo.analysis.handlers import ANALYSIS_HANDLERS

        handlers.update(ANALYSIS_HANDLERS)
    except Exception as exc:
        log.warning("worker.analysis_handlers_unavailable", error=str(exc))
    try:
        from aypo.intelligence.handlers import INTELLIGENCE_HANDLERS

        handlers.update(INTELLIGENCE_HANDLERS)
    except Exception as exc:
        log.warning("worker.intelligence_handlers_unavailable", error=str(exc))
    try:
        from aypo.research.handlers import RESEARCH_HANDLERS

        handlers.update(RESEARCH_HANDLERS)
    except Exception as exc:
        log.warning("worker.research_handlers_unavailable", error=str(exc))
    return handlers


async def main() -> None:
    configure_logging()
    settings = get_settings()
    handlers = build_handlers()
    log.info(
        "worker.starting",
        concurrency=settings.aypo_worker_concurrency,
        kinds=sorted(handlers),
    )
    if not handlers:
        log.error("worker.no_handlers_registered")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    task = asyncio.create_task(drain(handlers, concurrency=settings.aypo_worker_concurrency))
    await stop.wait()
    log.info("worker.stopping")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
