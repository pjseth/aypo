"""The research loop.

Driven against an in-memory store with collection and analysis disabled, so
these cover the loop's own control flow -- budget ceilings, stopping, coverage
reporting and degradation -- rather than the providers underneath it.
"""

import pytest

from aypo.llm.pricing import Spend
from aypo.research.budget import Budget, StopReason
from aypo.research.orchestrator import IterationOutcome, Orchestrator, ResearchObjective
from aypo.research.states import RunState
from tests.fake_store import FakeRunStore

pytestmark = pytest.mark.asyncio


def objective() -> ResearchObjective:
    # Generic placeholders only (brief §34).
    return ResearchObjective(
        subject_brand="Brand A",
        category_terms=["category x", "category y"],
        market_code="PH",
        language_codes=["en"],
    )


def budget(**kw) -> Budget:
    b = Budget(
        max_collection_usd=5.0,
        max_llm_usd=120.0,
        max_items=100,
        max_wallclock_seconds=3600,
        max_iterations=2,
    )
    for key, value in kw.items():
        setattr(b, key, value)
    return b


async def test_run_without_any_source_fails_fast(monkeypatch):
    from aypo.research import orchestrator as mod

    monkeypatch.setattr(mod, "build_collectors", lambda: ({}, []))
    store = FakeRunStore()
    outcome = await Orchestrator(store, objective(), budget=budget(), dry_run=True).run()
    # An empty loop running to its iteration ceiling would waste time and
    # produce a run that looks like it researched something.
    assert outcome.stop_reason is StopReason.SOURCES_UNAVAILABLE
    assert store.finished is None


async def test_exhausted_collection_budget_stops_before_any_work(monkeypatch):
    from aypo.research import orchestrator as mod

    monkeypatch.setattr(mod, "build_collectors", lambda: ({"tiktok": object()}, []))
    b = budget()
    b.collection_usd_now = 10.0  # already over the cap
    orch = Orchestrator(FakeRunStore(), objective(), budget=b, dry_run=True)

    async def no_refresh() -> float:
        return b.collection_usd_now

    monkeypatch.setattr(b, "refresh_collection_usage", no_refresh)
    outcome = await orch.run()
    assert outcome.stop_reason is StopReason.COLLECTION_BUDGET
    assert outcome.iterations == []


async def test_llm_ceiling_stops_the_run(monkeypatch):
    from aypo.research import orchestrator as mod

    monkeypatch.setattr(mod, "build_collectors", lambda: ({"tiktok": object()}, []))
    b = budget(max_llm_usd=1.0)
    b.ledger.record(Spend("llm", "claude-sonnet-5", 2.0))
    orch = Orchestrator(FakeRunStore(), objective(), budget=b, dry_run=True)

    async def no_refresh() -> float:
        return 0.0

    monkeypatch.setattr(b, "refresh_collection_usage", no_refresh)
    outcome = await orch.run()
    assert outcome.stop_reason is StopReason.LLM_BUDGET


async def test_unavailable_sources_are_recorded_as_coverage(monkeypatch):
    from aypo.collectors.registry import Availability
    from aypo.models.provenance import Platform
    from aypo.research import orchestrator as mod

    availability = [
        Availability(Platform.TIKTOK, True),
        Availability(Platform.YOUTUBE, False, reason="missing credential: YOUTUBE_API_KEY"),
    ]
    monkeypatch.setattr(
        mod, "build_collectors", lambda: ({Platform.TIKTOK: object()}, availability)
    )
    store = FakeRunStore()
    b = budget()

    orch = Orchestrator(store, objective(), budget=b, dry_run=True)

    async def no_refresh() -> float:
        return 0.0

    monkeypatch.setattr(b, "refresh_collection_usage", no_refresh)
    await orch.run()

    # Brief §7/§28: a source that could not be used must be visible, so the UI
    # shows a gap rather than a silently smaller sample.
    assert store.coverage["youtube"]["available"] is False
    assert "YOUTUBE_API_KEY" in store.coverage["youtube"]["unavailable_reason"]
    assert store.coverage["tiktok"]["available"] is True


async def test_run_moves_through_stopping_before_synthesising(monkeypatch):
    from aypo.collectors.registry import Availability
    from aypo.models.provenance import Platform
    from aypo.research import orchestrator as mod

    monkeypatch.setattr(
        mod,
        "build_collectors",
        lambda: ({Platform.TIKTOK: object()}, [Availability(Platform.TIKTOK, True)]),
    )
    store = FakeRunStore()
    b = budget(max_iterations=1)
    orch = Orchestrator(store, objective(), budget=b, dry_run=True)

    async def no_refresh() -> float:
        return 0.0

    monkeypatch.setattr(b, "refresh_collection_usage", no_refresh)
    await orch.run()

    visited = [s for _, s in store.states]
    assert RunState.PLANNING.value in visited
    # The stop reason is settled in `stopping`, before synthesis begins.
    assert visited.index(RunState.STOPPING.value) < visited.index(RunState.SYNTHESIZING.value)
    assert visited[-1] == RunState.COMPLETED.value


async def test_stop_reason_is_always_recorded(monkeypatch):
    from aypo.collectors.registry import Availability
    from aypo.models.provenance import Platform
    from aypo.research import orchestrator as mod

    monkeypatch.setattr(
        mod,
        "build_collectors",
        lambda: ({Platform.TIKTOK: object()}, [Availability(Platform.TIKTOK, True)]),
    )
    store = FakeRunStore()
    b = budget(max_iterations=1)
    orch = Orchestrator(store, objective(), budget=b, dry_run=True)

    async def no_refresh() -> float:
        return 0.0

    monkeypatch.setattr(b, "refresh_collection_usage", no_refresh)
    outcome = await orch.run()

    # Brief §26 requires the reason a run stopped be stored, not just a halt.
    assert store.finished is not None
    assert store.finished["stop_reason"] == outcome.stop_reason.value
    assert store.finished["saturation_basis"]


async def test_objective_defaults_to_a_readable_description():
    assert "Brand A" in objective().describe()


async def test_brand_surface_forms_are_resolved_before_they_become_competitors(monkeypatch):
    """The loop must not treat three spellings of one brand as three brands.

    Measured in a live run: JACK'N'JILL, JACK'nJILL and JACK'N JILL arrived from
    three different videos. Unresolved, the next iteration would generate three
    near-duplicate competitor queries and split that brand's evidence three ways.
    """
    from aypo.collectors.registry import Availability
    from aypo.models.provenance import Platform
    from aypo.research import orchestrator as mod

    monkeypatch.setattr(
        mod,
        "build_collectors",
        lambda: ({Platform.TIKTOK: object()}, [Availability(Platform.TIKTOK, True)]),
    )
    orch = Orchestrator(FakeRunStore(), objective(), budget=budget(), dry_run=True)

    class _Sighting:
        def __init__(self, surface):
            self.surface_form, self.brand_resolved = surface, True

    class _Analysis:
        labels: list = []

        def __init__(self, brands):
            self.visible_brands = brands

    class _Record:
        def __init__(self, brands):
            self.analysis = _Analysis(brands)

    orch._analyses = {
        "a": _Record([_Sighting("JACK'N'JILL")]),
        "b": _Record([_Sighting("JACK'nJILL")]),
        "c": _Record([_Sighting("JACK'N JILL")]),
        "d": _Record([_Sighting("Brand B")]),
    }
    result = IterationOutcome(0)
    orch._absorb_discoveries(result)

    assert result.brands_observed == 2, orch.context.observed_brands
    names = {name for name, _ in orch.context.observed_brands}
    assert "Brand B" in names
