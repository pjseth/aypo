"""In-memory RunStore for testing the loop without a database."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4


@dataclass
class FakeRunStore:
    runs: dict[UUID, dict[str, Any]] = field(default_factory=dict)
    states: list[tuple[UUID, str]] = field(default_factory=list)
    iterations: list[dict[str, Any]] = field(default_factory=list)
    phases: list[str] = field(default_factory=list)
    queries: list[dict[str, Any]] = field(default_factory=list)
    content: list[Any] = field(default_factory=list)
    commerce: list[Any] = field(default_factory=list)
    coverage: dict[str, dict[str, Any]] = field(default_factory=dict)
    logs: list[dict[str, Any]] = field(default_factory=list)
    spend: list[dict[str, Any]] = field(default_factory=list)
    analyses: list[dict[str, Any]] = field(default_factory=list)
    query_results: list[dict[str, Any]] = field(default_factory=list)
    finished: dict[str, Any] | None = None
    _seen: set[str] = field(default_factory=set)

    async def create_run(self, **kw: Any) -> UUID:
        run_id = uuid4()
        self.runs[run_id] = dict(kw)
        return run_id

    async def set_run_state(self, run_id: UUID, state: Any, *, detail: str | None = None) -> None:
        self.states.append((run_id, state.value))

    async def finish_run(self, run_id: UUID, **kw: Any) -> None:
        self.finished = dict(kw)

    async def open_iteration(self, run_id: UUID, iteration_no: int, quota: dict[str, Any]) -> None:
        self.iterations.append({"iteration_no": iteration_no, "quota": quota})

    async def set_phase(self, run_id: UUID, iteration_no: int, phase: Any) -> None:
        self.phases.append(phase.value)

    async def close_iteration(self, run_id: UUID, iteration_no: int, stats: dict[str, Any]) -> None:
        self.iterations[-1]["stats"] = stats

    async def record_query(
        self, run_id: UUID, iteration_no: int, query: Any, *, rejected: str | None = None
    ) -> UUID | None:
        query_id = uuid4()
        self.queries.append(
            {
                "id": query_id,
                "iteration_no": iteration_no,
                "text": query.text,
                "strategy": query.strategy.value,
                "rejected": rejected,
            }
        )
        return query_id

    async def record_content(
        self, run_id: UUID, query_id: UUID, iteration_no: int, item: Any, *, rank: int
    ) -> tuple[UUID, bool]:
        was_new = item.dedupe_key not in self._seen
        self._seen.add(item.dedupe_key)
        self.content.append(item)
        return uuid4(), was_new

    async def record_commerce(
        self, run_id: UUID, query_id: UUID, iteration_no: int, item: Any, *, rank: int
    ) -> tuple[UUID, bool]:
        was_new = item.dedupe_key not in self._seen
        self._seen.add(item.dedupe_key)
        self.commerce.append(item)
        return uuid4(), was_new

    async def record_analysis(self, **kw: Any) -> UUID:
        self.analyses.append(dict(kw))
        return uuid4()

    async def record_query_results(self, query_id: UUID, **kw: Any) -> None:
        self.query_results.append({"query_id": query_id, **kw})

    async def record_coverage(self, run_id: UUID, platform: str, stats: dict[str, Any]) -> None:
        self.coverage.setdefault(platform, {}).update(stats)

    async def log(self, run_id: UUID, *, event_type: str, summary: str, **kw: Any) -> None:
        self.logs.append({"event_type": event_type, "summary": summary, **kw})

    async def record_spend(self, run_id: UUID, **kw: Any) -> None:
        self.spend.append(dict(kw))
