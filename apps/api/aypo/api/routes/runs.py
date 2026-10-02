"""Starting and cancelling research runs.

A run costs real money on two separate meters (managed collection and model
usage), so the plan and its estimated spend are returned by a preview endpoint
and the start endpoint refuses to exceed the caller's stated ceiling.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from aypo.collectors.registry import build_collectors, capability_map
from aypo.db.pool import execute, fetchrow
from aypo.db.run_store import PostgresRunStore
from aypo.research.budget import Budget
from aypo.research.orchestrator import Orchestrator, ResearchObjective

log = structlog.get_logger(__name__)

router = APIRouter(tags=["runs"])

# Runs are executed in-process for the MVP. One at a time, because the provider
# concurrency limit is account-wide: two runs would interleave their actor jobs
# and both would crawl.
_active: dict[UUID, asyncio.Task] = {}


class StartRunRequest(BaseModel):
    subject_brand: str = Field(min_length=1)
    category_terms: list[str] = Field(min_length=1)
    market_code: str = "PH"
    language_codes: list[str] = Field(default_factory=lambda: ["en", "tl"])
    objective: str = ""
    max_collection_usd: float = Field(default=5.0, gt=0)
    max_llm_usd: float | None = None
    max_iterations: int | None = Field(default=None, ge=1, le=20)
    max_items: int | None = Field(default=None, ge=10)
    queries_per_iteration: int = Field(default=16, ge=1, le=40)
    dry_run: bool = False


def _objective(req: StartRunRequest) -> ResearchObjective:
    return ResearchObjective(
        subject_brand=req.subject_brand,
        category_terms=req.category_terms,
        market_code=req.market_code,
        language_codes=req.language_codes,
        objective=req.objective,
    )


def _budget(req: StartRunRequest) -> Budget:
    budget = Budget.from_settings(max_collection_usd=req.max_collection_usd)
    if req.max_llm_usd is not None:
        budget.max_llm_usd = req.max_llm_usd
    if req.max_iterations is not None:
        budget.max_iterations = req.max_iterations
    if req.max_items is not None:
        budget.max_items = req.max_items
    return budget


@router.post("/runs/preview")
async def preview_run(req: StartRunRequest) -> dict[str, Any]:
    """What a run would cost and cover, without starting it."""
    budget = _budget(req)
    await budget.refresh_collection_usage()
    _, availability = build_collectors()
    plan = budget.plan(capability_map(availability), iteration=0)
    return {
        "plan": {
            "total_items": plan.total_items,
            "per_platform": {p.value: n for p, n in plan.per_platform.items()},
            "per_strategy": {s.value: n for s, n in plan.per_strategy.items()},
            "reference_items": plan.reference_items,
            "scale_factor": round(plan.scale_factor, 3),
            "estimated_usd": round(plan.estimated_usd, 4),
            "description": plan.describe(),
        },
        "budget": budget.snapshot(),
        "sources": [
            {
                "platform": a.platform.value,
                "available": a.available,
                "reason": a.reason,
                "missing_credentials": a.missing_credentials,
            }
            for a in availability
        ],
    }


@router.post("/runs")
async def start_run(req: StartRunRequest) -> dict[str, Any]:
    if _active:
        # Account-wide provider concurrency means a second run does not go
        # faster, it makes both slower and harder to attribute spend to.
        raise HTTPException(409, "a research run is already in progress")

    budget = _budget(req)
    await budget.refresh_collection_usage()
    if budget.usable_collection_usd <= 0 and not req.dry_run:
        raise HTTPException(
            402,
            f"collection budget exhausted for this cycle: "
            f"${budget.collection_usd_now:.2f} of ${budget.max_collection_usd:.2f} used",
        )

    orchestrator = Orchestrator(
        PostgresRunStore(),
        _objective(req),
        budget=budget,
        queries_per_iteration=req.queries_per_iteration,
        dry_run=req.dry_run,
    )

    started: asyncio.Future[UUID] = asyncio.get_running_loop().create_future()

    async def execute_run() -> None:
        try:
            outcome = await orchestrator.run()
            if not started.done():
                started.set_result(outcome.run_id)
            log.info("run.completed", run_id=str(outcome.run_id))
        except Exception as exc:
            log.error("run.crashed", error=str(exc))
            if not started.done():
                started.set_exception(exc)
        finally:
            for run_id, task in list(_active.items()):
                if task is asyncio.current_task():
                    _active.pop(run_id, None)

    task = asyncio.create_task(execute_run())
    # The run id is allocated inside run(); poll briefly so the caller gets it
    # rather than a handle it cannot look anything up with.
    for _ in range(50):
        row = await fetchrow("SELECT id FROM research_runs ORDER BY created_at DESC LIMIT 1")
        if row is not None and row["id"] not in _active:
            _active[row["id"]] = task
            return {"run_id": str(row["id"]), "status": "running", "dry_run": req.dry_run}
        await asyncio.sleep(0.1)
    return {"run_id": None, "status": "starting"}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: UUID) -> dict[str, Any]:
    """Request cancellation.

    A long autonomous loop that cannot be stopped is a liability, so the flag is
    persisted as well as signalled in-process: a run picked up by a worker that
    this process does not own still sees it.
    """
    await execute("UPDATE research_runs SET cancel_requested = true WHERE id = $1", run_id)
    task = _active.get(run_id)
    if task is not None:
        task.cancel()
        _active.pop(run_id, None)
        return {"run_id": str(run_id), "cancelled": True}
    return {"run_id": str(run_id), "cancelled": False, "note": "not running in this process"}


@router.get("/runs/active")
async def active_runs() -> dict[str, Any]:
    return {"active": [str(run_id) for run_id in _active]}
