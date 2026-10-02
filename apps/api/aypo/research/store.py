"""The persistence surface the research loop needs.

A Protocol rather than a concrete class, for two reasons. The orchestrator is
the most valuable thing to test and the least pleasant to test against a live
database, so it must be drivable by an in-memory double. And the loop should
depend on what it actually uses -- a dozen operations -- rather than on whatever
the full data-access layer happens to expose.

Everything here is a WRITE path. Reads for the UI live in aypo/api/routes.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from aypo.models.commerce import CommerceItem
from aypo.models.content import ContentItem
from aypo.research.queries import GeneratedQuery
from aypo.research.states import RunPhase, RunState


class RunStore(Protocol):
    """What the orchestrator needs to persist.

    Implementations must be idempotent on natural keys: the same item is
    routinely rediscovered by a later query, and counting it twice inflates
    every per-item statistic downstream.
    """

    async def create_run(
        self,
        *,
        objective: str,
        subject_brand: str,
        market_code: str,
        language_codes: list[str],
        caps: dict[str, Any],
    ) -> UUID: ...

    async def set_run_state(
        self, run_id: UUID, state: RunState, *, detail: str | None = None
    ) -> None: ...

    async def finish_run(
        self, run_id: UUID, *, stop_reason: str, stop_detail: str, saturation_basis: dict[str, Any]
    ) -> None: ...

    async def open_iteration(
        self, run_id: UUID, iteration_no: int, quota: dict[str, Any]
    ) -> None: ...

    async def set_phase(self, run_id: UUID, iteration_no: int, phase: RunPhase) -> None: ...

    async def close_iteration(
        self, run_id: UUID, iteration_no: int, stats: dict[str, Any]
    ) -> None: ...

    async def record_query(
        self, run_id: UUID, iteration_no: int, query: GeneratedQuery, *, rejected: str | None = None
    ) -> UUID | None: ...

    async def record_content(
        self, run_id: UUID, query_id: UUID, iteration_no: int, item: ContentItem, *, rank: int
    ) -> tuple[UUID, bool]:
        """Returns (content_item_id, was_new)."""
        ...

    async def record_commerce(
        self, run_id: UUID, query_id: UUID, iteration_no: int, item: CommerceItem, *, rank: int
    ) -> tuple[UUID, bool]: ...

    async def record_analysis(
        self,
        *,
        run_id: UUID,
        content_item_id: UUID,
        record: Any,
        media_fingerprint: str,
        transcript: Any | None = None,
        frames: list[Any] | None = None,
    ) -> UUID: ...

    async def record_query_results(
        self, query_id: UUID, *, returned: int, admitted: int, cost_usd: float = 0.0
    ) -> None: ...

    async def record_coverage(self, run_id: UUID, platform: str, stats: dict[str, Any]) -> None: ...

    async def log(
        self,
        run_id: UUID,
        *,
        event_type: str,
        summary: str,
        iteration_no: int | None = None,
        payload: dict[str, Any] | None = None,
        severity: str = "info",
    ) -> None: ...

    async def record_spend(
        self, run_id: UUID, *, kind: str, usd: float, units: int, detail: dict[str, Any]
    ) -> None: ...
