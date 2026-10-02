"""Write paths for the research loop.

Thin async functions over ``aypo/db/pool.py``: one function per thing the loop
records, each returning a typed model so callers never hold an
``asyncpg.Record`` and never couple to the driver.

Three rules this module exists to enforce in one place:

* **Positional parameters only.** asyncpg speaks ``$1``, and no value is ever
  interpolated into SQL text.
* **Upserts are idempotent on the natural key the migration declares.** The
  same item is routinely rediscovered by a later query; inserting it twice
  would inflate every per-item statistic the product reports, and brief §28
  requires sample sizes to mean what they say.
* **First-discovery facts are immutable.** ``first_seen_query_id``,
  ``first_seen_at`` and ``cu_charged`` are written on INSERT and never touched
  by a conflict update, so per-query yield attribution and the budget ledger
  stay truthful across rediscovery.

Every function takes an optional ``conn``. Passing one lets the loop write
several rows in a single transaction, and lets a test roll everything back.
"""

from __future__ import annotations

import unicodedata
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
import structlog

from aypo.config import get_settings
from aypo.db.pool import transaction
from aypo.models.commerce import CommerceItem
from aypo.models.content import ContentItem, Creator
from aypo.models.provenance import CollectionMethod, Platform, SamplingStrategy
from aypo.models.research import (
    EXECUTED_QUERY_STATUSES,
    BudgetKind,
    BudgetLine,
    BudgetLineStatus,
    DedupState,
    ItemState,
    LogEvent,
    LogEventType,
    LogSeverity,
    LogVisibility,
    QueryStatus,
    ResearchDepth,
    ResearchIteration,
    ResearchQuery,
    ResearchResult,
    ResearchRun,
    RunPhase,
    RunState,
    StopReason,
    db_stop_reason,
)

log = structlog.get_logger(__name__)

Conn = asyncpg.Connection


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def _acquire(conn: Conn | None) -> AsyncIterator[Conn]:
    """Yield a connection with a transaction open around the caller's work.

    A supplied connection is wrapped in a nested transaction, which asyncpg
    implements as a savepoint. That keeps the multi-statement writes below
    atomic whether or not the caller already opened a transaction, and still
    lets the caller's rollback undo everything.
    """
    if conn is not None:
        async with conn.transaction():
            yield conn
        return
    async with transaction() as owned:
        yield owned


# The Platform enum spells the marketplaces `shopee` / `lazada`; the D-2
# vocabulary seeded into collection_sources spells them `shopee_ph` /
# `lazada_ph`. Only the three tables with an FK onto collection_sources
# (creators, content_items, collection_coverage) need the D-2 spelling --
# research_queries, research_results and commerce_items deliberately carry the
# pydantic spelling, because the pydantic enum is the live contract that 104
# tests depend on and the market is carried by research_runs.market_code.
_MARKETPLACE_PLATFORMS = frozenset({Platform.SHOPEE, Platform.LAZADA})


def db_platform(platform: Platform | str, market_code: str | None = None) -> str:
    """Map a Platform onto the collection_sources vocabulary (D-2).

    The market suffix is built from ``market_code`` rather than hardcoded,
    because a literal ``_ph`` in the data layer is exactly the market
    assumption brief §34 forbids.
    """
    value = Platform(platform)
    if value not in _MARKETPLACE_PLATFORMS:
        return value.value
    if not market_code:
        raise ValueError(
            f"platform {value.value!r} is market-scoped in collection_sources; "
            "pass the run's market_code"
        )
    return f"{value.value}_{market_code.lower()}"


def normalise_handle(handle: str) -> str:
    """NFKC + casefold, computed in Python because Postgres has no NFKC
    normaliser and doing half the normalisation in SQL is worse than none.

    The leading ``@`` is stripped: the same account arrives with and without it
    depending on the actor, and two creator rows for one account silently
    inflate n_eff in every statistic that treats creators as independent.
    """
    return unicodedata.normalize("NFKC", handle.strip().lstrip("@")).casefold()


# ISO 4217 minor-unit exponents for the currencies that do not use 2. Prices are
# stored as integer minor units because float cents compound through medians and
# per-100g normalisation into a visibly wrong price-positioning claim.
_MINOR_UNIT_EXPONENT: dict[str, int] = {"JPY": 0, "KRW": 0, "VND": 0, "CLP": 0, "ISK": 0}


def _to_minor(amount: float | None, currency: str | None) -> int | None:
    if amount is None:
        return None
    exponent = _MINOR_UNIT_EXPONENT.get((currency or "").upper(), 2)
    return round(amount * (10**exponent))


def _json_list(models: Sequence[Any]) -> list[dict[str, Any]]:
    return [m.model_dump(mode="json") for m in models]


# ─────────────────────────────────────────────────────────────────────────────
# Runs and iterations
# ─────────────────────────────────────────────────────────────────────────────


async def create_run(
    *,
    objective: str,
    subject_brand: str,
    market_code: str,
    language_codes: Sequence[str] = (),
    depth: ResearchDepth = ResearchDepth.STANDARD,
    user_seeds: dict[str, Any] | None = None,
    embedding_model: str | None = None,
    embedding_dim: int | None = None,
    cap_collection_usd: float | None = None,
    cap_llm_usd: float | None = None,
    cap_items: int | None = None,
    cap_wall_seconds: int | None = None,
    max_iterations: int | None = None,
    created_by: UUID | None = None,
    conn: Conn | None = None,
) -> ResearchRun:
    """Open a run in state ``created``.

    The embedding space defaults from settings and is pinned on the row (I-2):
    cosines computed in two different spaces are not comparable, so the space a
    run was planned against has to be recoverable from the run itself rather
    than from whatever the config happens to say when someone reads it back.
    """
    settings = get_settings()
    model = embedding_model or settings.aypo_embedding_model
    dim = embedding_dim if embedding_dim is not None else settings.aypo_embedding_dimensions

    async with _acquire(conn) as c:
        row = await c.fetchrow(
            """
            INSERT INTO research_runs (
                created_by, objective, subject_brand, user_seeds, market_code,
                language_codes, depth, embedding_model, embedding_dim,
                cap_collection_usd, cap_llm_usd, cap_items, cap_wall_seconds, max_iterations
            ) VALUES ($1, $2, $3, $4, $5, $6, $7::research_depth, $8, $9,
                      $10, $11, $12, $13, $14)
            RETURNING *
            """,
            created_by,
            objective,
            subject_brand,
            user_seeds or {},
            market_code.upper(),
            list(language_codes),
            ResearchDepth(depth).value,
            model,
            dim,
            cap_collection_usd,
            cap_llm_usd,
            cap_items,
            cap_wall_seconds,
            max_iterations,
        )
    return ResearchRun.from_row(row)


async def get_run(run_id: UUID, *, conn: Conn | None = None) -> ResearchRun | None:
    async with _acquire(conn) as c:
        row = await c.fetchrow("SELECT * FROM research_runs WHERE id = $1", run_id)
    return ResearchRun.from_row(row) if row is not None else None


async def start_iteration(
    run_id: UUID,
    iteration_no: int,
    *,
    quota: dict[str, Any],
    budget_snapshot: dict[str, Any],
    is_focus: bool = False,
    phase_deadline_at: datetime | None = None,
    conn: Conn | None = None,
) -> ResearchIteration:
    """Open iteration ``iteration_no``, or return the one already open.

    Re-entry is a normal path, not an error: the orchestrator is a poller and a
    crashed tick is replayed. DO NOTHING rather than DO UPDATE because ``quota``
    and ``budget_snapshot`` are frozen at ALLOCATE and the counters on the row
    are cumulative -- a second call must not reset either.
    """
    async with _acquire(conn) as c:
        row = await c.fetchrow(
            """
            INSERT INTO research_iterations (
                run_id, iteration_no, quota, budget_snapshot, is_focus, phase_deadline_at
            ) VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (run_id, iteration_no) DO NOTHING
            RETURNING *
            """,
            run_id,
            iteration_no,
            quota,
            budget_snapshot,
            is_focus,
            phase_deadline_at,
        )
        if row is None:
            row = await c.fetchrow(
                "SELECT * FROM research_iterations WHERE run_id = $1 AND iteration_no = $2",
                run_id,
                iteration_no,
            )
        # The run's cursor has to follow the iteration that actually opened, or
        # the run view and the iteration table disagree about where the loop is.
        await c.execute(
            """
            UPDATE research_runs
               SET iteration_no = greatest(iteration_no, $2),
                   started_at = coalesce(started_at, now())
             WHERE id = $1
            """,
            run_id,
            iteration_no,
        )
    return ResearchIteration.model_validate(dict(row))


# ─────────────────────────────────────────────────────────────────────────────
# Queries and results
# ─────────────────────────────────────────────────────────────────────────────

# ON CONFLICT infers a partial index only when the predicate matches, so
# rq_exact_dedup_idx's WHERE clause is rebuilt from the same tuple the model
# layer declares rather than hand-copied into a literal that can drift. These
# are enum literals -- schema, not caller values -- so composing them is safe.
_RQ_EXECUTED_SQL = ", ".join(f"'{s.value}'" for s in EXECUTED_QUERY_STATUSES)
_RQ_DEDUP_PREDICATE = f"WHERE status IN ({_RQ_EXECUTED_SQL})"


async def record_query(
    *,
    run_id: UUID,
    iteration_no: int,
    strategy: SamplingStrategy,
    platform: Platform,
    query_text: str,
    normalized_text: str,
    rationale: str,
    language_code: str | None = None,
    filters: dict[str, Any] | None = None,
    status: QueryStatus = QueryStatus.PROPOSED,
    triggered_by_kind: str = "none",
    triggered_by_id: str | None = None,
    triggered_by_hypothesis_id: UUID | None = None,
    parent_query_id: UUID | None = None,
    expected_observable: str | None = None,
    is_disconfirming: bool = False,
    is_reference_query: bool = False,
    requested_units: int = 0,
    reject_reason: str | None = None,
    dup_of_query_id: UUID | None = None,
    dup_similarity: float | None = None,
    dup_method: str | None = None,
    conn: Conn | None = None,
) -> ResearchQuery:
    """Record one query and the reason it was generated (brief §9).

    ``rationale`` is required by the schema and by the product: a query with no
    stated reason breaks the chain brief §32 renders, exactly where a marketer
    asks "why did you search that?".

    Idempotent against ``rq_exact_dedup_idx``, the last line of defence that
    stops the same normalised query being *executed* twice on one platform in
    one run. A proposal that was rejected is still inserted, because the
    rejection rate per class is itself a saturation signal.
    """
    # A SEED strategy would create a sixth stratum the allocator does not weight
    # (D-1). ResearchQuery rejects it too; failing here saves a round trip and
    # an opaque Postgres enum error.
    if strategy is SamplingStrategy.SEED:
        raise ValueError(
            "SamplingStrategy.SEED is not a research_strategy; record it as "
            "triggered_by_kind='seed' and give the query its real family"
        )

    async with _acquire(conn) as c:
        row = await c.fetchrow(
            f"""
            INSERT INTO research_queries (
                run_id, iteration_no, strategy, platform, query_text, normalized_text,
                language_code, filters, rationale, triggered_by_kind, triggered_by_id,
                triggered_by_hypothesis_id, parent_query_id, expected_observable,
                is_disconfirming, is_reference_query, status, reject_reason,
                dup_of_query_id, dup_similarity, dup_method, requested_units
            ) VALUES (
                $1, $2, $3::research_strategy, $4, $5, $6,
                $7, $8, $9, $10, $11,
                $12, $13, $14,
                $15, $16, $17::research_query_status, $18,
                $19, $20, $21, $22
            )
            ON CONFLICT (run_id, platform, normalized_text) {_RQ_DEDUP_PREDICATE}
            DO NOTHING
            RETURNING *
            """,
            run_id,
            iteration_no,
            SamplingStrategy(strategy).value,
            Platform(platform).value,
            query_text,
            normalized_text,
            language_code,
            filters or {},
            rationale,
            triggered_by_kind,
            triggered_by_id,
            triggered_by_hypothesis_id,
            parent_query_id,
            expected_observable,
            is_disconfirming,
            is_reference_query,
            QueryStatus(status).value,
            reject_reason,
            dup_of_query_id,
            dup_similarity,
            dup_method,
            requested_units,
        )
        if row is None:
            row = await c.fetchrow(
                f"""
                SELECT * FROM research_queries
                 WHERE run_id = $1 AND platform = $2 AND normalized_text = $3
                   AND status IN ({_RQ_EXECUTED_SQL})
                """,
                run_id,
                Platform(platform).value,
                normalized_text,
            )
    return ResearchQuery.model_validate(dict(row))


async def record_result(
    *,
    run_id: UUID,
    query_id: UUID,
    iteration_no: int,
    platform: Platform,
    provider: str,
    collection_method: CollectionMethod,
    url: str,
    url_canonical: str,
    external_id: str | None = None,
    discovery_mode: str | None = None,
    raw_rank: int | None = None,
    sort_order: str | None = None,
    page_depth: int = 1,
    published_at: datetime | None = None,
    published_at_precision: str | None = None,
    relevance_score: float | None = None,
    relevance_source: str | None = None,
    admitted: bool = False,
    admit_reason: str | None = None,
    reject_reason: str | None = None,
    content_item_id: UUID | None = None,
    commerce_item_id: UUID | None = None,
    item_state: ItemState = ItemState.DISCOVERED,
    cu_charged: float | None = None,
    raw: dict[str, Any] | None = None,
    conn: Conn | None = None,
) -> ResearchResult:
    """Record one (query x item) discovery -- the single provenance join (I-1).

    The natural key is ``(run_id, query_id, item_key)``: D-3 makes sampling
    strategy a property of the *discovering query*, so an item found by a
    category query and again by a competitor query has two rows and belongs to
    two strata. Collapsing that onto the item would make every share computed
    from it wrong.

    SCHEMA GAP, deliberate and recorded rather than worked around: the live
    index is ``rr_item_unique_idx UNIQUE (run_id, item_key)``, which physically
    permits only one row per item per run. Until that index is widened to
    ``(run_id, query_id, item_key)``, the second discovering query cannot get
    its own row; this function falls back to marking the surviving row
    ``dup_in_run`` so the rediscovery is at least visible and is charged
    nothing. The code below is written against the D-3 key and starts producing
    the second row the moment the index allows it -- no change here.
    """
    item_key = external_id or url_canonical
    charge = cu_charged if cu_charged is not None else (1.0 if admitted else 0.0)
    dedup_state = DedupState.NEW

    async with _acquire(conn) as c:
        # A rediscovery by a DIFFERENT query keeps its own provenance row -- the
        # strategy belongs to the query, and both strata are real -- but it is
        # charged nothing. Collection units measure how much content was
        # obtained, and obtaining the same item twice is not two items; charging
        # both would let one item consume two units of the collection ceiling.
        if charge and item_key:
            seen_in_run = await c.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM research_results
                     WHERE run_id = $1 AND item_key = $2 AND query_id <> $3
                )
                """,
                run_id,
                item_key,
                query_id,
            )
            if seen_in_run:
                charge = 0.0
                dedup_state = DedupState.DUP_IN_RUN

        existing = await c.fetchrow(
            """
            SELECT id FROM research_results
             WHERE run_id = $1 AND query_id = $2 AND item_key = $3
            """,
            run_id,
            query_id,
            item_key,
        )
        if existing is not None:
            row = await c.fetchrow(
                """
                UPDATE research_results
                   SET content_item_id  = coalesce($2, content_item_id),
                       commerce_item_id = coalesce($3, commerce_item_id),
                       item_state       = $4::research_item_state,
                       admitted         = admitted OR $5,
                       admit_reason     = coalesce($6, admit_reason),
                       reject_reason    = coalesce($7, reject_reason),
                       relevance_score  = coalesce($8, relevance_score),
                       relevance_source = coalesce($9, relevance_source)
                 WHERE id = $1
                RETURNING *
                """,
                existing["id"],
                content_item_id,
                commerce_item_id,
                ItemState(item_state).value,
                admitted,
                admit_reason,
                reject_reason,
                relevance_score,
                relevance_source,
            )
            return ResearchResult.model_validate(dict(row))

        try:
            # Its own savepoint: a unique violation poisons the surrounding
            # transaction, and the caller's other writes must survive a
            # rediscovery that is an ordinary event, not an error.
            async with c.transaction():
                row = await c.fetchrow(
                    """
                    INSERT INTO research_results (
                        run_id, query_id, iteration_no, platform, provider, collection_method,
                        discovery_mode, external_id, url, url_canonical, raw_rank, sort_order,
                        page_depth, published_at, published_at_precision, relevance_score,
                        relevance_source, admitted, admit_reason, reject_reason,
                        content_item_id, commerce_item_id, item_state, cu_charged, raw,
                        dedup_state, first_seen_query_id, first_seen_run_id
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6,
                        $7, $8, $9, $10, $11, $12,
                        $13, $14, $15, $16,
                        $17, $18, $19, $20,
                        $21, $22, $23::research_item_state, $24, $25,
                        $26::research_dedup_state, $2, $1
                    )
                    RETURNING *
                    """,
                    run_id,
                    query_id,
                    iteration_no,
                    Platform(platform).value,
                    provider,
                    CollectionMethod(collection_method).value,
                    discovery_mode,
                    external_id,
                    url,
                    url_canonical,
                    raw_rank,
                    sort_order,
                    page_depth,
                    published_at,
                    published_at_precision,
                    relevance_score,
                    relevance_source,
                    admitted,
                    admit_reason,
                    reject_reason,
                    content_item_id,
                    commerce_item_id,
                    ItemState(item_state).value,
                    charge,
                    raw,
                    dedup_state.value,
                )
        except asyncpg.UniqueViolationError:
            # Another query in this run already holds the only row the live
            # index permits. Charge nothing -- the item is already paid for --
            # and record that it was rediscovered.
            row = await c.fetchrow(
                """
                UPDATE research_results
                   SET dedup_state      = $3::research_dedup_state,
                       content_item_id  = coalesce($4, content_item_id),
                       commerce_item_id = coalesce($5, commerce_item_id),
                       item_state       = $6::research_item_state
                 WHERE run_id = $1 AND item_key = $2
                RETURNING *
                """,
                run_id,
                item_key,
                DedupState.DUP_IN_RUN.value,
                content_item_id,
                commerce_item_id,
                ItemState(item_state).value,
            )
            log.debug(
                "repository.result_collapsed_by_rr_item_unique_idx",
                run_id=str(run_id),
                query_id=str(query_id),
                item_key=item_key,
            )
    return ResearchResult.model_validate(dict(row))


# ─────────────────────────────────────────────────────────────────────────────
# Corpus upserts
# ─────────────────────────────────────────────────────────────────────────────


async def upsert_creator(
    creator: Creator,
    *,
    market_code: str | None = None,
    conn: Conn | None = None,
) -> UUID:
    """Insert or refresh a creator, keyed on ``(platform, identity_key)``.

    This key is load-bearing for every statistic in the product: 40 items from
    3 creators is 3 independent observations, not 40. Two rows for one account
    inflate n_eff and let a finding clear a support gate it should have failed,
    so identity is settled here rather than by convention upstream.
    """
    handle_norm = normalise_handle(creator.handle)
    # A handle-based identity is weaker -- a renamed account becomes a second
    # creator and splits its own evidence -- so the basis is recorded for the
    # design-effect estimate to discount.
    id_basis = "platform_id" if creator.platform_user_id else "handle"

    async with _acquire(conn) as c:
        return await c.fetchval(
            """
            INSERT INTO creators (
                platform, platform_user_id, handle, handle_norm, id_basis, display_name,
                follower_count, following_count, content_count, verified, bio,
                profile_url, avatar_url
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            ON CONFLICT (platform, identity_key) DO UPDATE SET
                handle         = excluded.handle,
                handle_norm    = excluded.handle_norm,
                display_name   = coalesce(excluded.display_name, creators.display_name),
                follower_count = coalesce(excluded.follower_count, creators.follower_count),
                following_count= coalesce(excluded.following_count, creators.following_count),
                content_count  = coalesce(excluded.content_count, creators.content_count),
                verified       = coalesce(excluded.verified, creators.verified),
                bio            = coalesce(excluded.bio, creators.bio),
                profile_url    = coalesce(excluded.profile_url, creators.profile_url),
                avatar_url     = coalesce(excluded.avatar_url, creators.avatar_url),
                updated_at     = now()
            RETURNING id
            """,
            db_platform(creator.platform, market_code),
            creator.platform_user_id,
            creator.handle,
            handle_norm,
            id_basis,
            creator.display_name,
            creator.follower_count,
            creator.following_count,
            creator.content_count,
            creator.verified,
            creator.bio,
            creator.profile_url,
            creator.avatar_url,
        )


async def upsert_content_item(
    item: ContentItem,
    *,
    market_code: str | None = None,
    creator_id: UUID | None = None,
    first_seen_query_id: UUID | None = None,
    related_search_terms: Sequence[str] = (),
    conn: Conn | None = None,
) -> UUID:
    """Insert or refresh one social item, keyed on ``(platform, platform_item_id)``.

    Run-independent by design (D-4/D-7): a run-scoped key would make the same
    video a new row every iteration, so cross-run cluster identity would see
    total membership turnover and every churn statistic would fire on every
    theme. Which run saw it is recorded in ``research_results``.

    On conflict, fields are refreshed with ``coalesce(excluded, existing)`` so a
    later, thinner observation cannot blank out something an earlier one saw.
    ``first_seen_query_id`` and ``first_seen_at`` are never updated: the query
    that *first* surfaced an item is what per-query yield attribution reads.
    """
    async with _acquire(conn) as c:
        if creator_id is None and item.creator is not None:
            creator_id = await upsert_creator(item.creator, market_code=market_code, conn=c)

        engagement = item.engagement
        sound = item.sound
        # Platform-stated sponsorship is an OBSERVED fact and brief §19/§28 want
        # it preferred over any model inference, which is only possible if the
        # gate can read it as a column rather than out of a jsonb blob.
        extras = item.provenance.extras or {}
        is_sponsored = extras.get("is_sponsored")
        is_ad = extras.get("is_ad")
        # Kept explicit so a future inferred value can never be mistaken for a
        # platform declaration.
        sponsorship_source = (
            extras.get("sponsorship_source")
            if isinstance(is_sponsored, bool) or isinstance(is_ad, bool)
            else None
        )

        return await c.fetchval(
            """
            INSERT INTO content_items (
                platform, platform_item_id, source_url, url_canonical, media_kind,
                creator_id, media_url, thumbnail_url, duration_seconds,
                caption, hashtags, language_hint, published_at,
                views, likes, comments_count, shares, saves, engagement_measured_at,
                sound_platform_id, sound_title, sound_author, sound_is_original, sound_url,
                comments, stated_location,
                platform_is_sponsored, platform_is_ad, sponsorship_source,
                related_search_terms, first_seen_query_id, platform_payload
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8, $9,
                $10, $11, $12, $13,
                $14, $15, $16, $17, $18, $19,
                $20, $21, $22, $23, $24,
                $25, $26,
                $27, $28, $29,
                $30, $31, $32
            )
            ON CONFLICT (platform, platform_item_id) DO UPDATE SET
                source_url       = excluded.source_url,
                url_canonical    = coalesce(excluded.url_canonical, content_items.url_canonical),
                creator_id       = coalesce(excluded.creator_id, content_items.creator_id),
                media_url        = coalesce(excluded.media_url, content_items.media_url),
                thumbnail_url    = coalesce(excluded.thumbnail_url, content_items.thumbnail_url),
                duration_seconds = coalesce(excluded.duration_seconds,
                                            content_items.duration_seconds),
                caption          = coalesce(excluded.caption, content_items.caption),
                hashtags         = case when excluded.hashtags = '{}'
                                        then content_items.hashtags else excluded.hashtags end,
                language_hint    = coalesce(excluded.language_hint, content_items.language_hint),
                published_at     = coalesce(excluded.published_at, content_items.published_at),
                views            = coalesce(excluded.views, content_items.views),
                likes            = coalesce(excluded.likes, content_items.likes),
                comments_count   = coalesce(excluded.comments_count, content_items.comments_count),
                shares           = coalesce(excluded.shares, content_items.shares),
                saves            = coalesce(excluded.saves, content_items.saves),
                engagement_measured_at = coalesce(excluded.engagement_measured_at,
                                                  content_items.engagement_measured_at),
                sound_platform_id = coalesce(excluded.sound_platform_id,
                                             content_items.sound_platform_id),
                sound_title      = coalesce(excluded.sound_title, content_items.sound_title),
                sound_author     = coalesce(excluded.sound_author, content_items.sound_author),
                sound_is_original = coalesce(excluded.sound_is_original,
                                             content_items.sound_is_original),
                sound_url        = coalesce(excluded.sound_url, content_items.sound_url),
                comments         = case when excluded.comments = '[]'::jsonb
                                        then content_items.comments else excluded.comments end,
                stated_location  = coalesce(excluded.stated_location,
                                            content_items.stated_location),
                platform_is_sponsored = coalesce(excluded.platform_is_sponsored,
                                                 content_items.platform_is_sponsored),
                platform_is_ad   = coalesce(excluded.platform_is_ad,
                                            content_items.platform_is_ad),
                sponsorship_source = coalesce(excluded.sponsorship_source,
                                              content_items.sponsorship_source),
                related_search_terms = case when excluded.related_search_terms = '{}'
                                            then content_items.related_search_terms
                                            else excluded.related_search_terms end,
                platform_payload = case when excluded.platform_payload = '{}'::jsonb
                                        then content_items.platform_payload
                                        else excluded.platform_payload end,
                updated_at       = now()
            RETURNING id
            """,
            db_platform(item.platform, market_code),
            item.platform_id,
            item.url,
            None,
            item.media_kind.value,
            creator_id,
            item.media_url,
            item.thumbnail_url,
            item.duration_seconds,
            item.caption,
            list(item.hashtags),
            item.language_hint,
            item.published_at,
            engagement.views,
            engagement.likes,
            engagement.comments,
            engagement.shares,
            engagement.saves,
            engagement.collected_at,
            sound.id if sound else None,
            sound.title if sound else None,
            sound.author if sound else None,
            sound.original if sound else None,
            sound.url if sound else None,
            _json_list(item.comments),
            item.stated_location,
            is_sponsored if isinstance(is_sponsored, bool) else None,
            is_ad if isinstance(is_ad, bool) else None,
            sponsorship_source,
            list(related_search_terms),
            first_seen_query_id,
            item.raw or {},
        )


# Fields brief §15/§16 name as collectable. Recording which were absent is what
# lets the UI say "insufficient data" instead of quietly shrinking a denominator.
_COMMERCE_TRACKED_FIELDS = (
    "title",
    "brand_surface_form",
    "price",
    "original_price",
    "rating",
    "review_count",
    "sold_count",
    "seller",
    "category_path",
    "image_url",
)


async def _upsert_seller(item: CommerceItem, conn: Conn) -> UUID | None:
    seller = item.seller
    if seller is None or not (seller.seller_id or seller.name):
        return None
    name_slug = normalise_handle(seller.name) if seller.name else None
    return await conn.fetchval(
        """
        INSERT INTO commerce_sellers (
            platform, platform_seller_id, name, name_slug, url, location,
            is_official_store, rating, follower_count
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (platform, coalesce(platform_seller_id, name_slug, 'unknown')) DO UPDATE SET
            name              = coalesce(excluded.name, commerce_sellers.name),
            url               = coalesce(excluded.url, commerce_sellers.url),
            location          = coalesce(excluded.location, commerce_sellers.location),
            is_official_store = coalesce(excluded.is_official_store,
                                         commerce_sellers.is_official_store),
            rating            = coalesce(excluded.rating, commerce_sellers.rating),
            follower_count    = coalesce(excluded.follower_count,
                                         commerce_sellers.follower_count)
        RETURNING id
        """,
        item.marketplace.value,
        seller.seller_id,
        seller.name,
        name_slug,
        seller.url,
        seller.location,
        seller.is_official_store,
        seller.rating,
        seller.follower_count,
    )


async def upsert_commerce_item(
    item: CommerceItem,
    *,
    conn: Conn | None = None,
) -> UUID:
    """Insert or refresh one listing, keyed on ``(platform, platform_item_id)``.

    Run-independent for the same reason content items are (D-4). ``platform``
    carries the pydantic spelling (``shopee`` / ``lazada``) because
    ``commerce_items`` has no FK onto ``collection_sources`` and
    ``research_results.platform`` uses the same spelling -- the two must match
    or the provenance join silently returns nothing.

    Prices are converted to integer minor units here, once, rather than at every
    read: a float price error compounds through medians and per-100g
    normalisation into a price-positioning claim that is confidently wrong.
    """
    currency = (item.currency or "").upper() or None
    price_minor = _to_minor(item.price, currency)
    original_minor = _to_minor(item.original_price, currency)

    quality_flags: list[str] = []

    # The column holds a FRACTION; CommerceItem.discount_percent is a
    # percentage. Out-of-range values are dropped rather than clamped: a 99%
    # "discount" is a fabricated reference price, and storing 0.95 for it would
    # launder the fabrication into a real-looking number (the CHECK would
    # reject it anyway).
    discount_pct: float | None = None
    if item.discount_percent is not None:
        fraction = item.discount_percent / 100.0
        if 0.0 <= fraction <= 0.95:
            discount_pct = round(fraction, 4)
        else:
            quality_flags.append("discount_out_of_range")

    rating_avg = item.rating
    if rating_avg is not None and not (0.0 <= rating_avg <= 5.0):
        rating_avg = None
        quality_flags.append("rating_out_of_range")

    if price_minor is None:
        quality_flags.append("price_missing")

    field_quality = {
        name: ("present" if _commerce_field_present(item, name) else "missing")
        for name in _COMMERCE_TRACKED_FIELDS
    }

    collection_mode = (
        "web_search_snippet"
        if item.provenance.collection_method is CollectionMethod.WEB_SEARCH
        else "provider_actor"
    )

    async with _acquire(conn) as c:
        seller_id = await _upsert_seller(item, c)
        return await c.fetchval(
            """
            INSERT INTO commerce_items (
                platform, platform_item_id, source_url, collection_provider, collection_mode,
                raw_payload, title_raw, description_raw, brand_surface_form, brand_confidence,
                seller_id, currency, price_minor, original_price_minor, discount_pct,
                price_status, rating_avg, rating_count, review_count, sold_count_raw,
                sold_semantics, listing_kind, variants, category_path, image_url,
                field_quality, quality_flags, is_degraded
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8, $9, $10,
                $11, $12, $13, $14, $15,
                $16::price_status, $17, $18, $19, $20,
                $21::sold_semantics, $22::listing_kind, $23, $24, $25,
                $26, $27, $28
            )
            ON CONFLICT (platform, platform_item_id) DO UPDATE SET
                source_url         = excluded.source_url,
                collection_provider = coalesce(excluded.collection_provider,
                                               commerce_items.collection_provider),
                raw_payload        = case when excluded.raw_payload = '{}'::jsonb
                                          then commerce_items.raw_payload
                                          else excluded.raw_payload end,
                title_raw          = coalesce(excluded.title_raw, commerce_items.title_raw),
                description_raw    = coalesce(excluded.description_raw,
                                              commerce_items.description_raw),
                brand_surface_form = coalesce(excluded.brand_surface_form,
                                              commerce_items.brand_surface_form),
                brand_confidence   = coalesce(excluded.brand_confidence,
                                              commerce_items.brand_confidence),
                seller_id          = coalesce(excluded.seller_id, commerce_items.seller_id),
                currency           = coalesce(excluded.currency, commerce_items.currency),
                price_minor        = coalesce(excluded.price_minor, commerce_items.price_minor),
                original_price_minor = coalesce(excluded.original_price_minor,
                                                commerce_items.original_price_minor),
                discount_pct       = coalesce(excluded.discount_pct, commerce_items.discount_pct),
                price_status       = excluded.price_status,
                rating_avg         = coalesce(excluded.rating_avg, commerce_items.rating_avg),
                rating_count       = coalesce(excluded.rating_count, commerce_items.rating_count),
                review_count       = coalesce(excluded.review_count, commerce_items.review_count),
                sold_count_raw     = coalesce(excluded.sold_count_raw,
                                              commerce_items.sold_count_raw),
                listing_kind       = excluded.listing_kind,
                variants           = case when excluded.variants = '[]'::jsonb
                                          then commerce_items.variants
                                          else excluded.variants end,
                category_path      = case when excluded.category_path = '{}'
                                          then commerce_items.category_path
                                          else excluded.category_path end,
                image_url          = coalesce(excluded.image_url, commerce_items.image_url),
                field_quality      = excluded.field_quality,
                quality_flags      = excluded.quality_flags,
                is_degraded        = excluded.is_degraded,
                last_seen_at       = now()
            RETURNING id
            """,
            item.marketplace.value,
            item.platform_id,
            item.url,
            item.provenance.collection_provider,
            collection_mode,
            item.raw or {},
            item.title,
            None,
            item.brand_surface_form,
            item.brand_confidence,
            seller_id,
            currency,
            price_minor,
            original_minor,
            discount_pct,
            "ok" if price_minor is not None else "unparseable",
            rating_avg,
            # rating_count and review_count are different denominators on every
            # marketplace; CommerceItem only carries the latter, so claiming the
            # former would invent a sample size (brief §28).
            None,
            item.review_count,
            item.sold_count,
            # Sold count is a visibility proxy, never a sales figure (brief
            # §28). Neither marketplace documents whether the figure is lifetime
            # or windowed, so 'unknown' is the only honest semantics we can
            # assert from a collector payload.
            "unknown",
            item.listing_kind.value,
            _json_list(item.variants),
            list(item.category_path),
            item.image_url,
            field_quality,
            quality_flags,
            bool(quality_flags),
        )


def _commerce_field_present(item: CommerceItem, name: str) -> bool:
    if name == "seller":
        return item.seller is not None
    if name == "category_path":
        return bool(item.category_path)
    if name == "title":
        return bool(item.title)
    return getattr(item, name, None) is not None


# ─────────────────────────────────────────────────────────────────────────────
# Log and ledger
# ─────────────────────────────────────────────────────────────────────────────


async def record_log_event(
    *,
    run_id: UUID,
    event_type: LogEventType,
    summary: str,
    iteration_no: int | None = None,
    phase: RunPhase | None = None,
    parent_event_id: UUID | None = None,
    subject_kind: str | None = None,
    subject_id: str | None = None,
    detail: str | None = None,
    payload: dict[str, Any] | None = None,
    visibility: LogVisibility = LogVisibility.USER,
    severity: LogSeverity = LogSeverity.INFO,
    conn: Conn | None = None,
) -> LogEvent:
    """Append one node to the run narrative (brief §9, §32).

    ``seq`` is allocated by bumping ``research_runs.log_seq`` in the same
    transaction as the insert. The row lock serialises concurrent emitters and
    an aborted transaction rolls the increment back together with the row it
    was for, so the narrative can never show a hole it cannot explain.
    """
    async with _acquire(conn) as c:
        seq = await c.fetchval(
            "UPDATE research_runs SET log_seq = log_seq + 1 WHERE id = $1 RETURNING log_seq",
            run_id,
        )
        if seq is None:
            raise ValueError(f"no research run {run_id}")
        row = await c.fetchrow(
            """
            INSERT INTO research_log (
                run_id, iteration_no, seq, event_type, phase, parent_event_id,
                subject_kind, subject_id, summary, detail, payload, visibility, severity
            ) VALUES (
                $1, $2, $3, $4::research_log_event, $5::research_iteration_phase, $6,
                $7, $8, $9, $10, $11, $12, $13
            )
            RETURNING *
            """,
            run_id,
            iteration_no,
            seq,
            LogEventType(event_type).value,
            RunPhase(phase).value if phase is not None else None,
            parent_event_id,
            subject_kind,
            subject_id,
            summary,
            detail,
            payload or {},
            LogVisibility(visibility).value,
            LogSeverity(severity).value,
        )
    return LogEvent.model_validate(dict(row))


async def record_budget_line(
    *,
    run_id: UUID,
    kind: BudgetKind,
    subsystem: str,
    idem_key: str,
    status: BudgetLineStatus = BudgetLineStatus.RESERVED,
    iteration_no: int | None = None,
    phase: RunPhase | None = None,
    strategy: SamplingStrategy | None = None,
    query_id: UUID | None = None,
    content_item_id: UUID | None = None,
    commerce_item_id: UUID | None = None,
    provider: str | None = None,
    model: str | None = None,
    qty: float = 0.0,
    unit_cost_usd: float | None = None,
    usd: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    detail: dict[str, Any] | None = None,
    conn: Conn | None = None,
) -> BudgetLine:
    """Reserve or settle one spend line (brief §26).

    Idempotent on ``(run_id, idem_key)``, and asymmetrically so on purpose: a
    line that is already terminal is returned untouched, while a line still
    ``reserved`` may be settled exactly once. A retry after a crash is precisely
    when a budget is most likely to be exceeded, so the same logical charge must
    never be counted twice.

    ``subsystem`` has no default because exhausting one budget line must degrade
    that subsystem only (D-25); unattributed spend would have to fail the whole
    run instead.
    """
    status = BudgetLineStatus(status)
    async with _acquire(conn) as c:
        row = await c.fetchrow(
            """
            INSERT INTO research_budget_ledger (
                run_id, iteration_no, phase, strategy, query_id, content_item_id,
                commerce_item_id, kind, status, subsystem, idem_key, provider, model,
                qty, unit_cost_usd, usd, input_tokens, output_tokens,
                cache_read_tokens, cache_write_tokens, detail, settled_at
            ) VALUES (
                $1, $2, $3::research_iteration_phase, $4::research_strategy, $5, $6,
                $7, $8::research_budget_kind, $9::research_budget_status, $10, $11, $12, $13,
                $14, $15, $16, $17, $18,
                $19, $20, $21,
                case when $9 <> 'reserved' then now() end
            )
            ON CONFLICT (run_id, idem_key) DO UPDATE SET
                status            = excluded.status,
                qty               = excluded.qty,
                unit_cost_usd     = coalesce(excluded.unit_cost_usd,
                                             research_budget_ledger.unit_cost_usd),
                usd               = excluded.usd,
                input_tokens      = excluded.input_tokens,
                output_tokens     = excluded.output_tokens,
                cache_read_tokens = excluded.cache_read_tokens,
                cache_write_tokens= excluded.cache_write_tokens,
                detail            = excluded.detail,
                settled_at        = now()
              WHERE research_budget_ledger.status = 'reserved'
                AND excluded.status <> 'reserved'
            RETURNING *
            """,
            run_id,
            iteration_no,
            RunPhase(phase).value if phase is not None else None,
            SamplingStrategy(strategy).value if strategy is not None else None,
            query_id,
            content_item_id,
            commerce_item_id,
            BudgetKind(kind).value,
            status.value,
            subsystem,
            idem_key,
            provider,
            model,
            qty,
            unit_cost_usd,
            usd,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_write_tokens,
            detail or {},
        )
        if row is None:
            # The DO UPDATE's WHERE did not match: the line is already terminal
            # and must not be re-charged.
            row = await c.fetchrow(
                "SELECT * FROM research_budget_ledger WHERE run_id = $1 AND idem_key = $2",
                run_id,
                idem_key,
            )
    return BudgetLine.model_validate(dict(row))


# ─────────────────────────────────────────────────────────────────────────────
# Finishing
# ─────────────────────────────────────────────────────────────────────────────


async def finish_run(
    run_id: UUID,
    *,
    state: RunState,
    stop_reason: StopReason,
    stop_detail: dict[str, Any] | None = None,
    saturation_basis: str | None = None,
    fatal_error: str | None = None,
    degraded: bool | None = None,
    conn: Conn | None = None,
) -> ResearchRun:
    """Close a run, recording why it stopped (brief §26).

    ``research_runs_stop_reason_required`` makes a terminal state without a
    reason impossible, which is the single failure brief §26 names explicitly.

    The exact ``StopReason`` is written into ``stop_detail.stop_reason_code``
    as well as mapped onto the ``research_stop_reason`` enum, because the map is
    not injective: both budget ceilings land on ``budget_hard_stop``, and
    "which budget ran out?" has to stay answerable afterwards.
    """
    if not RunState(state).is_terminal:
        raise ValueError(
            f"finish_run closes a run; {state!r} is not completed, failed or cancelled"
        )
    detail = dict(stop_detail or {})
    detail["stop_reason_code"] = StopReason(stop_reason).value

    async with _acquire(conn) as c:
        row = await c.fetchrow(
            """
            UPDATE research_runs
               SET state            = $2::research_run_state,
                   state_entered_at = now(),
                   stopped_at       = coalesce(stopped_at, now()),
                   stop_reason      = $3::research_stop_reason,
                   stop_detail      = $4,
                   saturation_basis = coalesce($5, saturation_basis),
                   fatal_error      = coalesce($6, fatal_error),
                   degraded         = coalesce($7, degraded)
             WHERE id = $1
            RETURNING *
            """,
            run_id,
            RunState(state).value,
            db_stop_reason(StopReason(stop_reason)),
            detail,
            saturation_basis,
            fatal_error,
            degraded,
        )
    if row is None:
        raise ValueError(f"no research run {run_id}")
    return ResearchRun.from_row(row)
