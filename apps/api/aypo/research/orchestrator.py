"""The research loop (brief §9, §26, §38).

One iteration is: generate queries -> collect -> retrieve and analyse -> embed
-> cluster -> discover (labels, brands, competitors) -> link commerce -> assess.
The loop repeats until a stopping condition fires, and **always records why it
stopped** (brief §26).

Three properties this is built around:

* **It degrades rather than fails.** A dead provider, an LLM refusal or a
  malformed payload costs the run that item, never the run. Brief §7 requires a
  run to continue past an unavailable source, so every stage is written to
  return a partial result with the gap recorded.
* **It paces itself.** Collection spend is checked against live provider usage
  before each iteration, not estimated locally, because a hard monthly cap plus
  billing lag is how a run dies mid-way with a lopsided corpus.
* **It is auditable.** Every query carries why it was generated and what
  triggered it, and every phase transition is persisted, so brief §32 can
  replay the chain from objective to finding.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog

from aypo.analysis.media import extract_audio, midpoint_fraction, sample_video
from aypo.analysis.multimodal import AnalysisMaterial, analyze_item
from aypo.analysis.retrieval import RetrievalTier, retrieve_video
from aypo.analysis.schema import AnalysisRecord, MediaTier
from aypo.collectors.base import SearchFilters
from aypo.collectors.registry import (
    COMMERCE_PLATFORMS,
    Availability,
    build_collectors,
    capability_map,
)
from aypo.config import get_settings
from aypo.intelligence.brand_resolution import ResolutionReport
from aypo.intelligence.brand_resolution import resolve as resolve_brands
from aypo.llm.client import LLMClient, LLMUnavailable
from aypo.models.commerce import CommerceItem
from aypo.models.content import ContentItem
from aypo.models.provenance import Platform
from aypo.providers.transcription import TranscriptionUnavailable, get_transcriber
from aypo.research.budget import Budget, StopReason
from aypo.research.queries import (
    DiscoveredContext,
    QueryDeduplicator,
    QueryGenerator,
    validate_batch,
)
from aypo.research.saturation import SaturationTracker
from aypo.research.states import RunPhase, RunState
from aypo.research.store import RunStore

log = structlog.get_logger(__name__)


@dataclass
class ResearchObjective:
    subject_brand: str
    category_terms: list[str]
    market_code: str = "PH"
    language_codes: list[str] = field(default_factory=lambda: ["en", "tl"])
    objective: str = ""
    # Optional operator hint only. Brief §23: a seed must never prevent or
    # replace discovery of other competitors, and seeded brands are flagged in
    # the UI so a reader can tell the difference.
    competitor_seeds: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return self.objective or (
            f"Investigate {self.subject_brand} and its category in {self.market_code}"
        )


@dataclass
class IterationOutcome:
    iteration_no: int
    queries_admitted: int = 0
    queries_rejected: int = 0
    items_collected: int = 0
    items_new: int = 0
    items_analysed: int = 0
    analysis_failures: int = 0
    commerce_collected: int = 0
    degraded: list[str] = field(default_factory=list)
    labels_discovered: int = 0
    brands_observed: int = 0


@dataclass
class RunOutcome:
    run_id: UUID
    stop_reason: StopReason
    stop_detail: str
    iterations: list[IterationOutcome] = field(default_factory=list)
    availability: list[Availability] = field(default_factory=list)
    budget: dict[str, Any] = field(default_factory=dict)
    saturation: dict[str, Any] = field(default_factory=dict)

    @property
    def total_items(self) -> int:
        return sum(i.items_collected for i in self.iterations)

    @property
    def total_analysed(self) -> int:
        return sum(i.items_analysed for i in self.iterations)


class Orchestrator:
    def __init__(
        self,
        store: RunStore,
        objective: ResearchObjective,
        *,
        budget: Budget | None = None,
        analysis_concurrency: int = 4,
        collect_concurrency: int = 4,
        queries_per_iteration: int = 16,
        dry_run: bool = False,
    ) -> None:
        self.store = store
        self.objective = objective
        self.settings = get_settings()
        self.budget = budget or Budget.from_settings()
        self.collectors, self.availability = build_collectors()
        self.tracker = SaturationTracker()
        self.dedupe = QueryDeduplicator()
        self.context = DiscoveredContext()
        self._analysis_gate = asyncio.Semaphore(analysis_concurrency)
        # Bounded below the account's concurrent-job limit so a run leaves
        # headroom rather than queueing behind itself.
        self._collect_concurrency = collect_concurrency
        # 每 query fans out across every available platform, so this is the
        # dominant driver of how many provider calls an iteration makes.
        self._queries_per_iteration = queries_per_iteration
        # dry_run exercises the full control flow without spending provider
        # budget: useful for checking the loop's own logic end to end.
        self.dry_run = dry_run
        self._seen_item_keys: set[str] = set()
        self._analyses: dict[str, AnalysisRecord] = {}
        # dedupe_key -> the persisted content_items row, so an analysis can
        # be attached to the item it describes.
        self._content_ids: dict[str, UUID] = {}
        self.brands: ResolutionReport = ResolutionReport()

    # ── run ──────────────────────────────────────────────────────────────────

    async def run(self) -> RunOutcome:
        run_id = await self.store.create_run(
            objective=self.objective.describe(),
            subject_brand=self.objective.subject_brand,
            market_code=self.objective.market_code,
            language_codes=self.objective.language_codes,
            caps={
                "collection_usd": self.budget.max_collection_usd,
                "llm_usd": self.budget.max_llm_usd,
                "items": self.budget.max_items,
                "wall_seconds": self.budget.max_wallclock_seconds,
                "max_iterations": self.budget.max_iterations,
            },
        )
        outcome = RunOutcome(run_id, StopReason.MAX_ITERATIONS, "", availability=self.availability)

        await self.store.set_run_state(run_id, RunState.PLANNING)
        for availability in self.availability:
            await self.store.record_coverage(
                run_id,
                availability.platform.value,
                {
                    "available": availability.available,
                    "unavailable_reason": availability.reason,
                },
            )

        if not self.collectors:
            # Nothing to collect from. Failing fast is more honest than running
            # an empty loop to its iteration ceiling.
            await self.store.set_run_state(run_id, RunState.FAILED, detail="no sources available")
            outcome.stop_reason = StopReason.SOURCES_UNAVAILABLE
            outcome.stop_detail = "no collector has the credentials it needs"
            return outcome

        await self.budget.refresh_collection_usage()
        self.budget.collection_usd_at_start = self.budget.collection_usd_now
        await self.store.set_run_state(run_id, RunState.ITERATING)

        stop_reason: StopReason | None = None
        stop_detail = ""

        for iteration_no in range(self.budget.max_iterations):
            self.budget.iterations = iteration_no
            if (ceiling := self.budget.exceeded()) is not None:
                stop_reason, stop_detail = ceiling, f"budget ceiling reached: {ceiling.value}"
                break

            result = await self._iteration(run_id, iteration_no)
            outcome.iterations.append(result)

            saturated, reason, _ = self.tracker.is_saturated()
            if saturated:
                stop_reason, stop_detail = StopReason.SATURATED, reason
                break
            # No admitted queries twice running means the generator has nothing
            # left to ask, which is a different failure from saturation.
            if result.queries_admitted == 0 and iteration_no > 0:
                stop_reason = StopReason.NO_HYPOTHESES
                stop_detail = "query generation produced nothing admissible"
                break

        if stop_reason is None:
            stop_reason = StopReason.MAX_ITERATIONS
            stop_detail = f"completed {self.budget.max_iterations} iterations"

        # stopping -> synthesizing: the stop reason is settled before any
        # synthesis begins, so a finding can never be attributed to an
        # iteration the run had already decided to abandon.
        await self.store.set_run_state(run_id, RunState.STOPPING, detail=stop_detail)
        await self.store.set_run_state(run_id, RunState.SYNTHESIZING)
        outcome.stop_reason = stop_reason
        outcome.stop_detail = stop_detail
        outcome.budget = self.budget.snapshot()
        outcome.saturation = self.tracker.snapshot()

        await self.store.finish_run(
            run_id,
            stop_reason=stop_reason.value,
            stop_detail=stop_detail,
            saturation_basis=outcome.saturation,
        )
        await self.store.set_run_state(run_id, RunState.COMPLETED)
        log.info(
            "run.finished",
            run_id=str(run_id),
            stop_reason=stop_reason.value,
            items=outcome.total_items,
            analysed=outcome.total_analysed,
        )
        return outcome

    # ── one iteration ────────────────────────────────────────────────────────

    async def _iteration(self, run_id: UUID, iteration_no: int) -> IterationOutcome:
        outcome = IterationOutcome(iteration_no)
        plan = self.budget.plan(capability_map(self.availability), iteration=iteration_no)
        await self.store.open_iteration(
            run_id,
            iteration_no,
            {
                "per_platform": {p.value: n for p, n in plan.per_platform.items()},
                "per_strategy": {s.value: n for s, n in plan.per_strategy.items()},
                "estimated_usd": plan.estimated_usd,
            },
        )
        await self.store.log(
            run_id,
            event_type="iteration.opened",
            summary=plan.describe(),
            iteration_no=iteration_no,
        )

        # 1. queries
        await self.store.set_phase(run_id, iteration_no, RunPhase.PROPOSE)
        queries = await self._generate_queries(run_id, iteration_no, outcome)
        if not queries:
            await self.store.close_iteration(run_id, iteration_no, {"queries_admitted": 0})
            return outcome

        # 2. collect
        await self.store.set_phase(run_id, iteration_no, RunPhase.DISCOVER)
        content, commerce = await self._collect(run_id, iteration_no, queries, plan, outcome)

        # 3. retrieve + analyse
        await self.store.set_phase(run_id, iteration_no, RunPhase.ANALYZE_WAIT)
        await self._analyse(run_id, iteration_no, content, outcome)

        # 4. assess
        await self.store.set_phase(run_id, iteration_no, RunPhase.MEASURE)
        await self._assess(run_id, iteration_no, outcome)

        await self.store.set_phase(run_id, iteration_no, RunPhase.CLOSED)
        await self.store.close_iteration(
            run_id,
            iteration_no,
            {
                "queries_admitted": outcome.queries_admitted,
                "queries_rejected": outcome.queries_rejected,
                "items_admitted": outcome.items_collected,
                "items_analysed": outcome.items_analysed,
                "degraded": bool(outcome.degraded),
                "degraded_reasons": outcome.degraded,
            },
        )
        _ = commerce
        return outcome

    # ── phases ───────────────────────────────────────────────────────────────

    async def _generate_queries(
        self, run_id: UUID, iteration_no: int, outcome: IterationOutcome
    ) -> list[tuple[UUID, Any]]:
        try:
            generator = QueryGenerator()
        except LLMUnavailable as exc:
            outcome.degraded.append(f"query generation unavailable: {exc}")
            return []

        if iteration_no == 0:
            batch = await generator.seed_queries(
                subject=self.objective.subject_brand,
                category_terms=self.objective.category_terms,
                market=self.objective.market_code,
                languages=self.objective.language_codes,
                n=self._queries_per_iteration,
            )
        else:
            self.context.queries_run = list(self.dedupe.seen_texts) or [
                q for q in self.dedupe.seen_normalised
            ]
            batch = await generator.followup_queries(
                self.context,
                subject=self.objective.subject_brand,
                market=self.objective.market_code,
                n=self._queries_per_iteration,
            )

        kept, rejected = validate_batch(
            batch,
            observed_brands={b for b, _ in self.context.observed_brands},
            discovered_labels={value for _, value, _ in self.context.labels},
            # Targeted strategies require something to have been discovered.
            allow_targeted=iteration_no > 0,
        )

        admitted: list[tuple[UUID, Any]] = []
        for query in kept:
            if self.dedupe.is_lexical_duplicate(query.text):
                rejected.append(f"{query.text!r}: already run")
                continue
            query_id = await self.store.record_query(run_id, iteration_no, query)
            if query_id is None:
                continue
            self.dedupe.add(query.text)
            admitted.append((query_id, query))

        outcome.queries_admitted = len(admitted)
        outcome.queries_rejected = len(rejected)
        for reason in rejected:
            await self.store.log(
                run_id,
                event_type="query.rejected",
                summary=reason,
                iteration_no=iteration_no,
                severity="warning",
            )
        await self.store.log(
            run_id,
            event_type="queries.generated",
            summary=f"{len(admitted)} admitted, {len(rejected)} rejected",
            iteration_no=iteration_no,
            payload={"reasoning": batch.reasoning[:500]},
        )
        return admitted

    async def _collect(
        self,
        run_id: UUID,
        iteration_no: int,
        queries: list[tuple[UUID, Any]],
        plan: Any,
        outcome: IterationOutcome,
    ) -> tuple[list[ContentItem], list[CommerceItem]]:
        """Search every (platform, query) pair concurrently.

        Each managed-collector search is a separate actor run taking tens of
        seconds, so issuing them in sequence makes a realistic iteration take
        close to an hour. They are fanned out instead and bounded by the
        provider's own concurrency gate, which is what the account's job limit
        actually constrains.
        """
        content: list[ContentItem] = []
        commerce: list[CommerceItem] = []
        if self.dry_run:
            return content, commerce

        @dataclass
        class _Task:
            platform: Platform
            collector: Any
            query_id: UUID
            query: Any
            limit: int

        tasks: list[_Task] = []
        planned: dict[Platform, int] = {}
        for platform, collector in self.collectors.items():
            allocation = plan.per_platform.get(platform, 0)
            if allocation <= 0:
                continue
            applicable = [
                (qid, q) for qid, q in queries if q.platform is None or q.platform == platform
            ]
            if not applicable:
                continue
            planned[platform] = allocation
            per_query = max(1, allocation // len(applicable))
            tasks.extend(_Task(platform, collector, qid, q, per_query) for qid, q in applicable)

        collected: dict[Platform, int] = dict.fromkeys(planned, 0)
        queries_run: dict[Platform, int] = dict.fromkeys(planned, 0)
        gate = asyncio.Semaphore(self._collect_concurrency)

        async def run_one(task: _Task) -> None:
            # Checked inside the task, not when the list was built: earlier
            # tasks may already have consumed the remaining budget.
            if not self.budget.can_afford(task.limit):
                outcome.degraded.append(f"{task.platform.value}: stopped on collection budget")
                return
            filters = SearchFilters(
                limit=task.limit,
                country=(
                    self.settings.apify_shopee_country
                    if task.platform is Platform.SHOPEE
                    else self.objective.market_code
                ),
                sampling_strategy=task.query.strategy,
                research_run_id=str(run_id),
                research_query_id=str(task.query_id),
            )
            async with gate:
                try:
                    result = await task.collector.search(task.query.text, filters)
                except Exception as exc:
                    outcome.degraded.append(f"{task.platform.value}: {type(exc).__name__}: {exc}")
                    return
            queries_run[task.platform] = queries_run.get(task.platform, 0) + 1

            if not result.ok:
                await self.store.record_query_results(task.query_id, returned=0, admitted=0)
                outcome.degraded.append(f"{task.platform.value}: {result.error}")
                await self.store.log(
                    run_id,
                    event_type="collection.degraded",
                    summary=f"{task.platform.value}: {result.error}",
                    iteration_no=iteration_no,
                    severity="warning",
                )
                return

            is_commerce = task.platform in COMMERCE_PLATFORMS
            for rank, item in enumerate(result.items, start=1):
                key = item.dedupe_key
                was_new = key not in self._seen_item_keys
                self._seen_item_keys.add(key)
                try:
                    if is_commerce:
                        await self.store.record_commerce(
                            run_id, task.query_id, iteration_no, item, rank=rank
                        )
                        commerce.append(item)
                        outcome.commerce_collected += 1
                    else:
                        content_id, _ = await self.store.record_content(
                            run_id, task.query_id, iteration_no, item, rank=rank
                        )
                        self._content_ids[key] = content_id
                        content.append(item)
                except Exception as exc:
                    # One malformed record must not lose the rest of the batch.
                    log.warning("collect.persist_failed", error=str(exc)[:160])
                    continue
                outcome.items_collected += 1
                outcome.items_new += int(was_new)
                collected[task.platform] = collected.get(task.platform, 0) + 1

            await self.store.record_query_results(
                task.query_id,
                returned=result.raw_count,
                admitted=len(result.items),
            )
            self.budget.items_collected += len(result.items)
            if result.units_consumed:
                await self.store.record_spend(
                    run_id,
                    kind="collection",
                    usd=0.0,
                    units=result.units_consumed,
                    detail={
                        "platform": task.platform.value,
                        "query": task.query.text,
                        "provider": "apify",
                    },
                )
            # Collector-surfaced vocabulary is organically discovered query
            # material, feeding brief §8D without any theme being assumed.
            for term in getattr(task.collector, "discovered_search_terms", set()):
                if term not in self.dedupe.seen_normalised:
                    self.context.queries_run.append(term)

        await asyncio.gather(*(run_one(t) for t in tasks), return_exceptions=True)

        for platform, allocation in planned.items():
            await self.store.record_coverage(
                run_id,
                platform.value,
                {
                    "available": True,
                    "queries_run": queries_run.get(platform, 0),
                    "items_discovered": collected.get(platform, 0),
                    "items_admitted": collected.get(platform, 0),
                    "planned_units": allocation,
                    "achieved_units": collected.get(platform, 0),
                },
            )

        # Live usage, not an estimate: provider billing lags and a local tally
        # drifts away from the cap that actually binds.
        await self.budget.refresh_collection_usage()
        return content, commerce

    async def _analyse(
        self,
        run_id: UUID,
        iteration_no: int,
        content: list[ContentItem],
        outcome: IterationOutcome,
    ) -> None:
        if not content or self.dry_run:
            return
        try:
            client = LLMClient(ledger=self.budget.ledger, model=self.settings.aypo_analysis_model)
        except LLMUnavailable as exc:
            outcome.degraded.append(f"analysis unavailable: {exc}")
            return

        # An item rediscovered by a later query is the same item. Analysing it
        # again costs a full retrieval, transcription and model call to produce
        # a row the idempotent upsert then collapses -- the storage layer saved
        # the data, not the money.
        fresh = [item for item in content if item.dedupe_key not in self._analyses]
        if skipped := len(content) - len(fresh):
            log.info("analysis.already_done", skipped=skipped)
        if not fresh:
            return

        async def one(item: ContentItem) -> None:
            async with self._analysis_gate:
                if self.budget.ledger.total_usd >= self.budget.max_llm_usd:
                    return
                record, error, transcript, fingerprint = await self._analyse_item(item, client)
                if record is None:
                    outcome.analysis_failures += 1
                    if error:
                        await self.store.log(
                            run_id,
                            event_type="analysis.failed",
                            summary=f"{item.dedupe_key}: {error}",
                            iteration_no=iteration_no,
                            severity="warning",
                        )
                    return
                self._analyses[item.dedupe_key] = record
                outcome.items_analysed += 1
                content_id = self._content_ids.get(item.dedupe_key)
                if content_id is not None:
                    try:
                        await self.store.record_analysis(
                            run_id=run_id,
                            content_item_id=content_id,
                            record=record,
                            media_fingerprint=fingerprint,
                            transcript=transcript,
                        )
                    except Exception as exc:
                        # An analysis that cannot be stored is still a real
                        # analysis; losing the run over it would be worse.
                        log.warning(
                            "analysis.persist_failed",
                            item=item.dedupe_key,
                            error=str(exc)[:200],
                        )

        await asyncio.gather(*(one(item) for item in fresh), return_exceptions=True)
        self._absorb_discoveries(outcome)

    async def _analyse_item(
        self, item: ContentItem, client: LLMClient
    ) -> tuple[AnalysisRecord | None, str | None, Any | None, str]:
        """Retrieve, sample, transcribe and analyse one item.

        Returns (record, error, transcript, media fingerprint). The fingerprint
        keys the analysis to the bytes it was computed from, so re-analysing
        unchanged media is a no-op rather than a duplicate row.
        """
        media = await retrieve_video(item.url)
        if media.tier is not RetrievalTier.FULL_VIDEO or media.path is None:
            # A caption-only "analysis" is exactly the caption-driven listening
            # the brief rejects, so the item is skipped rather than guessed at.
            return None, media.error or "media not retrievable", None, ""

        fingerprint = hashlib.sha256(
            f"{item.dedupe_key}:{media.bytes_downloaded}".encode()
        ).hexdigest()[:40]
        try:
            _, frames = await sample_video(str(media.path))
        except Exception as exc:
            return None, f"frame sampling failed: {exc}", None, fingerprint
        if not frames:
            return None, "no frames extracted", None, fingerprint

        transcript = None
        try:
            audio = await extract_audio(str(media.path))
            if audio:
                transcript = await get_transcriber(self.budget.ledger).transcribe(audio)
        except TranscriptionUnavailable:
            pass
        except Exception as exc:
            log.debug("analysis.transcription_failed", error=str(exc)[:120])

        material = AnalysisMaterial(
            frames=frames,
            transcript=transcript,
            caption=item.caption or "",
            comments_text="\n".join(c.text for c in item.comments[:20]),
            media_tier=MediaTier.FULL_VIDEO,
        )
        record, _report, error = await analyze_item(item, material, client=client)
        if record is not None:
            record.midpoint_fraction = midpoint_fraction(frames)
        return record, error, transcript, fingerprint

    def _absorb_discoveries(self, outcome: IterationOutcome) -> None:
        """Feed what was discovered back into the next round's query context.

        This is the loop closing: the next iteration's competitor and theme
        queries are built only from brands and labels that were actually
        observed here.
        """
        label_counts: dict[tuple[str, str], int] = {}
        brand_counts: dict[str, int] = {}
        for record in self._analyses.values():
            for label in record.analysis.labels:
                key = (label.facet.value, label.value)
                label_counts[key] = label_counts.get(key, 0) + 1
            for sighting in record.analysis.visible_brands:
                if sighting.brand_resolved:
                    brand_counts[sighting.surface_form] = (
                        brand_counts.get(sighting.surface_form, 0) + 1
                    )

        self.context.labels = [(facet, value, n) for (facet, value), n in label_counts.items()]

        # Resolve surface forms into entities before anything downstream counts
        # them. A live run produced JACK'N'JILL, JACK'nJILL and JACK'N JILL from
        # three videos: left unresolved they are three competitors with a third
        # of the evidence each, and the next iteration would generate three
        # near-duplicate competitor queries.
        self.brands = resolve_brands(brand_counts, seeds=self.objective.competitor_seeds)
        self.context.observed_brands = [
            (entity.canonical_name, entity.observation_count) for entity in self.brands.entities
        ]
        outcome.labels_discovered = len(label_counts)
        outcome.brands_observed = self.brands.entity_count
        if self.brands.entity_count < len(brand_counts):
            log.info(
                "brands.collapsed",
                surface_forms=len(brand_counts),
                entities=self.brands.entity_count,
            )

    async def _assess(self, run_id: UUID, iteration_no: int, outcome: IterationOutcome) -> None:
        self.tracker.observe_novelty(
            admitted=outcome.items_new,
            already_seen=max(0, outcome.items_collected - outcome.items_new),
        )
        self.tracker.observe_competitors({b for b, _ in self.context.observed_brands})
        self.tracker.observe_yield(
            new_evidence=outcome.labels_discovered, units_spent=max(outcome.items_collected, 1)
        )
        snapshot = self.tracker.snapshot()
        await self.store.log(
            run_id,
            event_type="iteration.assessed",
            summary=str(snapshot["reason"]),
            iteration_no=iteration_no,
            payload={"saturation": snapshot, "budget": self.budget.snapshot()},
        )
