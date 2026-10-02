"""FastAPI application entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aypo.api.routes.reads import router as reads_router
from aypo.api.routes.runs import router as runs_router
from aypo.config import get_settings
from aypo.db.pool import close_pool, get_pool, healthcheck
from aypo.logging import configure_logging

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()
    caps = settings.capabilities()
    log.info(
        "api.starting",
        env=settings.aypo_env,
        reasoning_model=settings.aypo_llm_model,
        analysis_model=settings.aypo_analysis_model,
        available=sorted(k for k, v in caps.items() if v),
        unavailable=sorted(k for k, v in caps.items() if not v),
    )
    try:
        await get_pool()
    except Exception as exc:
        # The API still boots so /health can explain what is wrong, rather than
        # crash-looping with the reason buried in container logs.
        log.error("api.db_unavailable", error=str(exc))
    yield
    await close_pool()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AYPO",
        description=(
            "Social, cultural, competitive and commerce intelligence. "
            "Every finding is traceable to the content that supports it."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"]
        if settings.aypo_env == "development"
        else [],
        allow_origin_regex=r"https://.*\.vercel\.app"
        if settings.aypo_env != "development"
        else None,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, Any]:
        settings = get_settings()
        caps = settings.capabilities()
        db = await healthcheck()
        return {
            "status": "ok" if db.get("ok") else "degraded",
            "env": settings.aypo_env,
            "database": db,
            "models": {
                "reasoning": settings.aypo_llm_model,
                "analysis": settings.aypo_analysis_model,
                "embedding": (
                    f"{settings.aypo_embedding_model}@{settings.aypo_embedding_dimensions}"
                ),
            },
            # Surfaced so the UI can tell a marketer that a source was
            # unavailable for a run, instead of silently showing a gap
            # (brief §7, §28: identify insufficient data).
            "capabilities": caps,
            "missing_credentials": {
                cap: settings.missing_for(cap) for cap, ok in caps.items() if not ok
            },
        }

    # runs_router first: its literal paths (/runs/active, /runs/preview) must
    # be matched before reads_router's /runs/{run_id}, which would otherwise
    # swallow them and fail UUID parsing.
    app.include_router(runs_router, prefix="/api")
    app.include_router(reads_router, prefix="/api")

    return app


app = create_app()
