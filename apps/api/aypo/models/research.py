"""The research run and its parts (brief §9, §25, §26, §32).

These models are the in-process shape of the rows written by
``aypo/db/repository.py``. Field names match the column names in
``supabase/migrations/0002_research.sql`` one-for-one so a row can be validated
with ``Model.model_validate(dict(record))`` and no second naming convention
exists to drift from the schema.

Two deliberate reuses rather than redefinitions, because other modules and the
existing test suite already depend on the originals:

* ``StopReason`` comes from ``aypo/research/budget.py`` -- the module that
  actually decides a run is over. Its spellings do **not** match the
  ``research_stop_reason`` Postgres enum, so the two maps at the foot of this
  file are the single place that translation happens.
* ``SamplingStrategy`` and ``Platform`` come from ``aypo/models/provenance.py``.
  ``SamplingStrategy`` carries one extra member, ``SEED``, that the
  ``research_strategy`` enum deliberately refuses (0002 D-1: a seed is a
  *reason* a query was generated, not a query family, and admitting it would
  create a sixth stratum no allocator weights). ``ResearchQuery`` rejects it at
  the boundary instead of letting Postgres raise an opaque enum error.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from aypo.models.provenance import CollectionMethod, Platform, SamplingStrategy
from aypo.research.budget import StopReason

__all__ = [
    "BudgetKind",
    "BudgetLine",
    "BudgetLineStatus",
    "DedupState",
    "EvidenceStance",
    "Hypothesis",
    "HypothesisEvidence",
    "HypothesisStatus",
    "ItemState",
    "LogEvent",
    "LogEventType",
    "LogSeverity",
    "LogVisibility",
    "QueryStatus",
    "ResearchDepth",
    "ResearchIteration",
    "ResearchQuery",
    "ResearchResult",
    "ResearchRun",
    "RunPhase",
    "RunState",
    "StopReason",
    "db_stop_reason",
    "stop_reason_from_db",
]


# ─────────────────────────────────────────────────────────────────────────────
# Enums — one member per value of the matching Postgres enum.
#
# These exist so a wrong string fails in Python, at the call site that invented
# it, rather than as an `invalid input value for enum` 500 rows into a
# collection batch.
# ─────────────────────────────────────────────────────────────────────────────


class RunState(StrEnum):
    """``research_run_state``. The run-level state machine."""

    CREATED = "created"
    PLANNING = "planning"
    ITERATING = "iterating"
    FOCUSING = "focusing"
    STOPPING = "stopping"
    SYNTHESIZING = "synthesizing"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        """The three states ``research_runs_stop_reason_required`` gates on."""
        return self in _TERMINAL_STATES


_TERMINAL_STATES = frozenset({RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED})


class RunPhase(StrEnum):
    """``research_iteration_phase``. Where one iteration has got to."""

    ALLOCATE = "allocate"
    PROPOSE = "propose"
    DEDUPE = "dedupe"
    DISCOVER = "discover"
    DISCOVER_WAIT = "discover_wait"
    RETRIEVE_WAIT = "retrieve_wait"
    ANALYZE_WAIT = "analyze_wait"
    CLUSTER = "cluster"
    INTERPRET = "interpret"
    MEASURE = "measure"
    DECIDE = "decide"
    CLOSED = "closed"
    ABANDONED = "abandoned"


class ResearchDepth(StrEnum):
    """``research_depth``. Operator-chosen effort, resolved into caps at PLANNING."""

    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


class QueryStatus(StrEnum):
    """``research_query_status``.

    A rejected query is still recorded: the rejection rate per class is itself
    a saturation signal (brief §26), so "the loop stopped proposing" and "the
    guardrail ate the proposal" must stay distinguishable.
    """

    PROPOSED = "proposed"
    ADMITTED = "admitted"
    REJECTED_DUPLICATE = "rejected_duplicate"
    REJECTED_GUARDRAIL = "rejected_guardrail"
    REJECTED_LOW_YIELD = "rejected_low_yield"
    REJECTED_BUDGET = "rejected_budget"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    REFUSED = "refused"
    SKIPPED = "skipped"


# The statuses covered by rq_exact_dedup_idx. Restated here because ON CONFLICT
# on a partial index must name the same predicate, and the repository builds
# that clause from this tuple rather than a second hand-written literal.
EXECUTED_QUERY_STATUSES: tuple[QueryStatus, ...] = (
    QueryStatus.ADMITTED,
    QueryStatus.QUEUED,
    QueryStatus.RUNNING,
    QueryStatus.SUCCEEDED,
    QueryStatus.PARTIAL,
)


class DedupState(StrEnum):
    """``research_dedup_state``. Whether this discovery was already known."""

    NEW = "new"
    DUP_IN_RUN = "dup_in_run"
    DUP_CROSS_RUN = "dup_cross_run"
    DUP_IN_BATCH = "dup_in_batch"


class ItemState(StrEnum):
    """``research_item_state``. Discovery and retrieval progress, kept separate
    (brief §7) because finding a URL and obtaining its media fail independently."""

    DISCOVERED = "discovered"
    PENDING_RETRIEVAL = "pending_retrieval"
    RETRIEVED = "retrieved"
    ANALYZING = "analyzing"
    ANALYZED = "analyzed"
    FAILED = "failed"
    SKIPPED = "skipped"


class BudgetKind(StrEnum):
    """``research_budget_kind``. Which of the four ceilings a line charges."""

    SOCIAL_UNIT = "social_unit"
    COMMERCE_UNIT = "commerce_unit"
    LLM = "llm"
    EMBEDDING = "embedding"
    TRANSCRIPTION = "transcription"
    PROVIDER_CALL = "provider_call"
    PROVIDER_RESULT = "provider_result"
    STORAGE = "storage"
    WALL = "wall"


class BudgetLineStatus(StrEnum):
    """``research_budget_status``.

    ``orphaned`` is summed alongside ``settled`` everywhere: a provider call
    that may have billed must be assumed to have billed, or a hard monthly cap
    is overrun by exactly the spend we chose not to count.
    """

    RESERVED = "reserved"
    SETTLED = "settled"
    ORPHANED = "orphaned"
    VOIDED = "voided"


class HypothesisStatus(StrEnum):
    """``hypothesis_status``."""

    OPEN = "open"
    TESTING = "testing"
    SUPPORTED = "supported"
    REFUTED = "refuted"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    ABANDONED = "abandoned"


class EvidenceStance(StrEnum):
    """Refuting evidence is first-class: brief §25 requires a hypothesis to
    state what would refute it, which a support-only store cannot keep."""

    CONFIRMING = "confirming"
    REFUTING = "refuting"


class LogVisibility(StrEnum):
    USER = "user"
    DEBUG = "debug"


class LogSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class LogEventType(StrEnum):
    """``research_log_event``. The vocabulary brief §32's run view renders."""

    RUN_CREATED = "run_created"
    OBJECTIVE_SET = "objective_set"
    CAPABILITY_PROBE = "capability_probe"
    SOURCE_UNCONFIGURED = "source_unconfigured"
    SEED_PROBE_SET = "seed_probe_set"
    STATE_TRANSITION = "state_transition"
    ITERATION_OPENED = "iteration_opened"
    QUOTA_ALLOCATED = "quota_allocated"
    QUERIES_PROPOSED = "queries_proposed"
    QUERY_ADMITTED = "query_admitted"
    QUERY_REJECTED = "query_rejected"
    SOURCE_SEARCHED = "source_searched"
    RESULTS_ADMITTED = "results_admitted"
    SOURCE_DEGRADED = "source_degraded"
    PROVIDER_GARBAGE = "provider_garbage"
    ITEMS_ANALYZED = "items_analyzed"
    CLUSTER_SNAPSHOT = "cluster_snapshot"
    PATTERN_DETECTED = "pattern_detected"
    COMPETITOR_DISCOVERED = "competitor_discovered"
    COMPETITOR_PROMOTED = "competitor_promoted"
    HYPOTHESIS_CREATED = "hypothesis_created"
    HYPOTHESIS_UPDATED = "hypothesis_updated"
    HYPOTHESIS_RESOLVED = "hypothesis_resolved"
    COMMERCE_PROBE_LAUNCHED = "commerce_probe_launched"
    SOCIAL_COMMERCE_LINK = "social_commerce_link"
    FINDING_DRAFTED = "finding_drafted"
    FINDING_PUBLISHED = "finding_published"
    EVIDENCE_LINKED = "evidence_linked"
    SATURATION_MEASURED = "saturation_measured"
    FOCUS_MODE_ENTERED = "focus_mode_entered"
    BUDGET_THRESHOLD = "budget_threshold"
    PHASE_DEADLINE_PARTIAL = "phase_deadline_partial"
    LLM_FALLBACK = "llm_fallback"
    LLM_REFUSED = "llm_refused"
    ITERATION_CLOSED = "iteration_closed"
    ITERATION_ABANDONED = "iteration_abandoned"
    SATURATION_UNAVAILABLE = "saturation_unavailable"
    DIVERSIFICATION_PROBE = "diversification_probe"
    CONCENTRATION_CAP = "concentration_cap"
    TEMPORAL_BACKFILL = "temporal_backfill"
    STOP_EVALUATED = "stop_evaluated"
    RUN_STOPPED = "run_stopped"
    RUN_FAILED = "run_failed"


# ─────────────────────────────────────────────────────────────────────────────
# Stop-reason translation
#
# budget.py's StopReason names the ceiling that bound; the research_stop_reason
# enum names the *class* of stop the run view renders. The map is not
# injective -- both budget ceilings land on 'budget_hard_stop' -- so finish_run
# also writes the exact StopReason value into stop_detail and the reverse map
# prefers it. Without that, "which budget ran out?" is unanswerable after the
# fact, and brief §26 asks precisely that question.
# ─────────────────────────────────────────────────────────────────────────────

_STOP_REASON_TO_DB: dict[StopReason, str] = {
    StopReason.COLLECTION_BUDGET: "budget_hard_stop",
    StopReason.LLM_BUDGET: "budget_hard_stop",
    StopReason.ITEM_TARGET: "content_target_reached",
    StopReason.WALLCLOCK: "wall_clock_exhausted",
    StopReason.MAX_ITERATIONS: "max_iterations_reached",
    StopReason.SATURATED: "saturation",
    StopReason.NO_HYPOTHESES: "hypothesis_starvation",
    StopReason.OPERATOR: "user_cancelled",
    StopReason.SOURCES_UNAVAILABLE: "source_exhaustion",
}

# The lossy direction. 'budget_hard_stop' resolves to the collection ceiling
# only as a fallback; stop_detail['stop_reason_code'] is authoritative.
_STOP_REASON_FROM_DB: dict[str, StopReason] = {
    "budget_hard_stop": StopReason.COLLECTION_BUDGET,
    "budget_drain": StopReason.COLLECTION_BUDGET,
    "content_target_reached": StopReason.ITEM_TARGET,
    "wall_clock_exhausted": StopReason.WALLCLOCK,
    "wall_clock_pacing": StopReason.WALLCLOCK,
    "max_iterations_reached": StopReason.MAX_ITERATIONS,
    "saturation": StopReason.SATURATED,
    "repetition": StopReason.SATURATED,
    "hypothesis_starvation": StopReason.NO_HYPOTHESES,
    "user_cancelled": StopReason.OPERATOR,
    "user_depth_reached": StopReason.OPERATOR,
    "source_exhaustion": StopReason.SOURCES_UNAVAILABLE,
}


def db_stop_reason(reason: StopReason) -> str:
    """The ``research_stop_reason`` value that stores this stop."""
    return _STOP_REASON_TO_DB[reason]


def stop_reason_from_db(
    code: str | None, detail: dict[str, Any] | None = None
) -> StopReason | None:
    """Recover the exact ceiling, preferring the value finish_run preserved."""
    if code is None:
        return None
    exact = (detail or {}).get("stop_reason_code")
    if isinstance(exact, str):
        try:
            return StopReason(exact)
        except ValueError:
            pass
    return _STOP_REASON_FROM_DB.get(code)


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────


class ResearchRun(BaseModel):
    """One autonomous investigation (brief §9).

    ``objective``, ``subject_brand`` and ``user_seeds`` are the *entire*
    operator-supplied surface. Everything else in the corpus is discovered, so
    keeping the seeding surface to three auditable fields is what makes brief
    §1 checkable rather than aspirational.
    """

    id: UUID
    created_by: UUID | None = None
    created_at: datetime
    started_at: datetime | None = None
    stopped_at: datetime | None = None

    objective: str
    subject_brand: str
    subject_brand_id: UUID | None = None
    user_seeds: dict[str, Any] = Field(default_factory=dict)

    market_code: str
    language_codes: list[str] = Field(default_factory=list)
    depth: ResearchDepth = ResearchDepth.STANDARD

    state: RunState = RunState.CREATED
    state_entered_at: datetime | None = None
    iteration_no: int = 0

    cap_collection_usd: float | None = None
    cap_llm_usd: float | None = None
    cap_items: int | None = None
    cap_wall_seconds: int | None = None
    max_iterations: int | None = None

    # I-2: one embedding space per run. A mid-run change is a fatal error, not
    # a silently incomparable cosine, which is only enforceable if the run
    # carries the space it was planned against.
    embedding_model: str
    embedding_dim: int

    config_resolved: dict[str, Any] | None = None
    coverage_gaps: dict[str, Any] | None = None
    sampling_design: dict[str, Any] | None = None

    # The gate the trend subsystem reads BEFORE emitting any growth or decline
    # claim (brief §22).
    temporal_coverage: str = "unknown"

    # The ceiling that actually bound, and the research_stop_reason value it was
    # stored as. Both are kept because the map to the enum is lossy.
    stop_reason: StopReason | None = None
    stop_reason_code: str | None = None
    stop_detail: dict[str, Any] | None = None
    saturation_basis: str | None = None

    degraded: bool = False
    fatal_error: str | None = None
    cancel_requested: bool = False
    log_seq: int = 0

    @classmethod
    def from_row(cls, row: Any) -> ResearchRun:
        data = dict(row)
        code = data.get("stop_reason")
        data["stop_reason_code"] = code
        data["stop_reason"] = stop_reason_from_db(code, data.get("stop_detail"))
        return cls.model_validate(data)


class ResearchIteration(BaseModel):
    """One pass of the loop. Keyed ``(run_id, iteration_no)``, not a surrogate
    id, so re-entry after a crash addresses the same iteration."""

    run_id: UUID
    iteration_no: int
    phase: RunPhase = RunPhase.ALLOCATE
    phase_entered_at: datetime | None = None
    phase_deadline_at: datetime | None = None
    opened_at: datetime | None = None
    closed_at: datetime | None = None

    is_focus: bool = False

    # Frozen at ALLOCATE. jsonb rather than columns because the allocator's
    # shape is still moving and these are only ever read back whole.
    quota: dict[str, Any] = Field(default_factory=dict)
    budget_snapshot: dict[str, Any] = Field(default_factory=dict)
    spend: dict[str, Any] | None = None

    queries_proposed: int = 0
    queries_admitted: int = 0
    queries_rejected: dict[str, Any] = Field(default_factory=dict)

    items_admitted: int = 0
    items_analysed: int = 0

    degraded: bool = False
    degraded_reasons: list[str] = Field(default_factory=list)


class ResearchQuery(BaseModel):
    """One search, and the auditable reason it exists (brief §9).

    ``rationale`` and the ``triggered_by_*`` columns are the chain brief §32
    renders as a tree: a follow-up query hangs off the hypothesis that caused
    it. They are not debug fields.
    """

    id: UUID
    run_id: UUID
    iteration_no: int

    strategy: SamplingStrategy
    platform: Platform
    query_text: str
    normalized_text: str
    language_code: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)

    rationale: str
    triggered_by_kind: str = "none"
    triggered_by_id: str | None = None
    triggered_by_hypothesis_id: UUID | None = None
    triggered_by_finding_id: UUID | None = None
    parent_query_id: UUID | None = None

    expected_observable: str | None = None
    is_disconfirming: bool = False
    is_reference_query: bool = False

    status: QueryStatus = QueryStatus.PROPOSED
    reject_reason: str | None = None
    dup_of_query_id: UUID | None = None
    dup_similarity: float | None = None
    dup_method: str | None = None

    job_id: UUID | None = None
    requested_units: int = 0
    results_total: int = 0
    results_new: int = 0
    results_admitted: int = 0
    results_relevant: int = 0
    cost_usd: float = 0.0

    proposed_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @field_validator("strategy")
    @classmethod
    def _reject_seed_strategy(cls, value: SamplingStrategy) -> SamplingStrategy:
        # 0002 D-1: a user-supplied seed is a *reason* a query was generated,
        # not a query family. Letting it through would create a sixth stratum
        # no allocator weights, which is how operator assumptions leak into the
        # corpus the system is supposed to discover (brief §1).
        if value is SamplingStrategy.SEED:
            raise ValueError(
                "SamplingStrategy.SEED is not a research_strategy; record it as "
                "triggered_by_kind='seed' and give the query its real family"
            )
        return value


class ResearchResult(BaseModel):
    """One (query x item) discovery -- the single provenance join (I-1).

    Everything about *how* an item was sampled lives here, never on the item:
    an item found by a category query and again by a competitor query belongs
    to two strata, and collapsing that onto the item makes every share computed
    from it wrong (D-3).
    """

    id: UUID
    run_id: UUID
    query_id: UUID
    iteration_no: int

    platform: Platform
    provider: str
    collection_method: CollectionMethod
    discovery_mode: str | None = None

    external_id: str | None = None
    url: str
    url_canonical: str
    item_key: str | None = None

    # The sampling design, per item. Without rank and page depth a share over
    # this corpus is indistinguishable from a share over whatever the provider
    # chose to rank first (brief §28).
    raw_rank: int | None = None
    sort_order: str | None = None
    page_depth: int = 1
    published_at: datetime | None = None
    published_at_precision: str | None = None
    discovered_at: datetime | None = None

    dedup_state: DedupState = DedupState.NEW
    first_seen_query_id: UUID | None = None
    first_seen_run_id: UUID | None = None

    # relevance_source is mandatory alongside the score (D-40): an LLM opinion
    # that feeds a stop predicate must stay distinguishable from a measurement.
    relevance_score: float | None = None
    relevance_source: str | None = None
    admitted: bool = False
    admit_reason: str | None = None
    reject_reason: str | None = None

    content_item_id: UUID | None = None
    commerce_item_id: UUID | None = None
    item_state: ItemState = ItemState.DISCOVERED
    cu_charged: float = 0.0

    @model_validator(mode="after")
    def _at_most_one_item(self) -> ResearchResult:
        # Mirrors research_results_one_item. At most, not exactly: a result
        # points at nothing until retrieval resolves it.
        if self.content_item_id is not None and self.commerce_item_id is not None:
            raise ValueError("a result resolves to a social item or a commerce item, never both")
        return self


class Hypothesis(BaseModel):
    """A testable claim generated from collected data (brief §25).

    The content of every text field here is generated from the corpus and is
    never authored in a prompt, a fixture or a default (brief §1, §34).
    """

    id: UUID
    run_id: UUID
    created_iteration: int
    resolved_iteration: int | None = None
    status: HypothesisStatus = HypothesisStatus.OPEN

    statement: str
    reason_generated: str

    confirming_observables: list[Any] = Field(default_factory=list)
    refuting_observables: list[Any] = Field(default_factory=list)
    follow_up_plan: list[Any] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)

    # Derived in Postgres, never asserted by the caller: admissibility feeds the
    # hypothesis-productivity stop condition, and a stop condition must not
    # depend on a flag the thing being measured sets about itself.
    admissible: bool = False

    resolution_note: str | None = None
    created_at: datetime | None = None
    resolved_at: datetime | None = None


class HypothesisEvidence(BaseModel):
    """One item cited for or against a hypothesis."""

    id: UUID
    hypothesis_id: UUID
    content_item_id: UUID | None = None
    commerce_item_id: UUID | None = None
    stance: EvidenceStance
    created_iteration: int
    note: str | None = None
    created_at: datetime | None = None

    @model_validator(mode="after")
    def _exactly_one_item(self) -> HypothesisEvidence:
        # Mirrors hypothesis_evidence_one_item. Evidence that cites nothing is
        # the black box brief §27 forbids.
        if (self.content_item_id is None) == (self.commerce_item_id is None):
            raise ValueError("evidence cites exactly one social item or one commerce item")
        return self


class LogEvent(BaseModel):
    """One node of the research narrative (brief §9, §32).

    ``summary`` is rendered verbatim in the run view, so it is a product
    surface written for a marketer, not a debug string.
    """

    id: UUID
    run_id: UUID
    iteration_no: int | None = None
    seq: int
    occurred_at: datetime | None = None

    event_type: LogEventType
    phase: RunPhase | None = None
    parent_event_id: UUID | None = None

    subject_kind: str | None = None
    subject_id: str | None = None

    summary: str
    detail: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    visibility: LogVisibility = LogVisibility.USER
    severity: LogSeverity = LogSeverity.INFO


class BudgetLine(BaseModel):
    """One reserve-or-settle entry in the run's ledger (brief §26).

    Not named in brief §29's table list, but ``record_budget_line`` has to
    return something typed, and the ledger row is what makes a stop reason
    reproducible after the fact: the line that pushed a ceiling over is still
    there with the query and iteration that caused it.
    """

    id: UUID
    run_id: UUID
    iteration_no: int | None = None
    phase: RunPhase | None = None
    strategy: SamplingStrategy | None = None
    query_id: UUID | None = None
    content_item_id: UUID | None = None
    commerce_item_id: UUID | None = None

    kind: BudgetKind
    status: BudgetLineStatus = BudgetLineStatus.RESERVED
    subsystem: str
    idem_key: str

    provider: str | None = None
    model: str | None = None

    qty: float = 0.0
    unit_cost_usd: float | None = None
    usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    detail: dict[str, Any] = Field(default_factory=dict)
    reserved_at: datetime | None = None
    settled_at: datetime | None = None
