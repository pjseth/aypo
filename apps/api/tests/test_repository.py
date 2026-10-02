"""Repository behaviour against the real local Postgres.

These are the assertions a unit test cannot make. Every one of them is about
something the schema enforces and Python cannot: a generated identity column,
an ON CONFLICT target, a Postgres enum, a gap-free sequence allocated under a
row lock. Mocking any of that would test the mock.

Every test runs inside one transaction that is rolled back at the end, so the
suite is repeatable and leaves no residue in a developer's database. The
transaction is handed to the repository functions explicitly, which is also the
shape the research loop uses when it writes several rows atomically.

Skipped when the migrations have not been applied, so the suite stays runnable
on a fresh checkout.

Generic placeholders only -- `Brand A`, `Theme X`, `Creator One` (brief §34).
No real brand, category or cultural vocabulary appears here, because a fixture
is one of the places a presumed finding most easily becomes a default.
"""

import os
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from aypo.config import get_settings
from aypo.db import repository as repo
from aypo.models.commerce import CommerceItem, ListingKind, Marketplace, Seller, Variant
from aypo.models.content import ContentItem, Creator, Engagement, MediaKind
from aypo.models.provenance import CollectionMethod, Platform, Provenance, SamplingStrategy
from aypo.models.research import (
    BudgetKind,
    BudgetLineStatus,
    DedupState,
    ItemState,
    LogEventType,
    QueryStatus,
    ResearchDepth,
    RunPhase,
    RunState,
    StopReason,
)

pytestmark = pytest.mark.asyncio


async def _dsn() -> str | None:
    dsn = os.environ.get("DATABASE_URL") or get_settings().database_url
    try:
        conn = await asyncpg.connect(dsn, timeout=3)
    except Exception:
        return None
    try:
        exists = await conn.fetchval(
            "select exists (select 1 from information_schema.tables "
            "where table_name = 'research_runs')"
        )
    finally:
        await conn.close()
    return dsn if exists else None


@pytest.fixture
async def db():
    """One connection, one transaction, rolled back.

    The connection is yielded rather than a pool: the repository takes an
    optional `conn` precisely so a caller can scope a batch of writes, and a
    test is the simplest such caller.
    """
    dsn = await _dsn()
    if dsn is None:
        pytest.skip("research_runs not present; run `pnpm db:migrate` to enable")
    conn = await asyncpg.connect(dsn)
    # The pool registers this codec on every connection; without it jsonb reads
    # back as a string and every payload assertion below would be vacuous.
    import json

    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    tx = conn.transaction()
    await tx.start()
    try:
        yield conn
    finally:
        await tx.rollback()
        await conn.close()


async def _run(conn, **overrides):
    kwargs = {
        "objective": "Establish how the category behaves around Brand A",
        "subject_brand": "Brand A",
        "market_code": "PH",
        "embedding_model": "text-embedding-3-large",
        "embedding_dim": 1536,
        "conn": conn,
    }
    kwargs.update(overrides)
    return await repo.create_run(**kwargs)


async def _query(conn, run, *, strategy=SamplingStrategy.CATEGORY, text="theme x", **overrides):
    kwargs = {
        "run_id": run.id,
        "iteration_no": 0,
        "strategy": strategy,
        "platform": Platform.TIKTOK,
        "query_text": text,
        "normalized_text": text,
        "rationale": "Broad opening query; nothing has been discovered yet.",
        "conn": conn,
    }
    kwargs.update(overrides)
    return await repo.record_query(**kwargs)


def _content_item(platform_id: str) -> ContentItem:
    return ContentItem(
        platform_id=platform_id,
        platform=Platform.TIKTOK,
        url=f"https://example.invalid/v/{platform_id}",
        media_kind=MediaKind.VIDEO,
        caption="Theme X clip",
        hashtags=["themex"],
        creator=Creator(
            platform=Platform.TIKTOK,
            handle="@CreatorOne",
            display_name="Creator One",
            platform_user_id="creator-1",
            follower_count=1000,
        ),
        published_at=datetime(2026, 1, 15, tzinfo=UTC),
        engagement=Engagement(views=100, likes=10, collected_at=datetime(2026, 2, 1, tzinfo=UTC)),
        provenance=Provenance(
            platform=Platform.TIKTOK,
            source_url=f"https://example.invalid/v/{platform_id}",
            discovery_query="theme x",
            sampling_strategy=SamplingStrategy.CATEGORY,
            collection_provider="apify:clockworks~tiktok-scraper",
            collection_method=CollectionMethod.MANAGED_COLLECTOR,
            discovered_at=datetime(2026, 2, 1, tzinfo=UTC),
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Runs
# ─────────────────────────────────────────────────────────────────────────────


async def test_a_run_round_trips(db):
    run = await _run(db)
    assert run.state is RunState.CREATED
    assert run.subject_brand == "Brand A"
    assert run.market_code == "PH"
    # I-2: the embedding space is pinned on the run, not looked up later.
    assert run.embedding_dim == 1536

    again = await repo.get_run(run.id, conn=db)
    assert again is not None
    assert again.id == run.id
    assert again.objective == run.objective
    assert again.depth is ResearchDepth.STANDARD


async def test_an_iteration_reopens_without_resetting_its_frozen_quota(db):
    run = await _run(db)
    first = await repo.start_iteration(
        run.id, 0, quota={"category": 20}, budget_snapshot={"llm_usd": 0.0}, conn=db
    )
    assert first.phase is RunPhase.ALLOCATE
    assert first.quota == {"category": 20}

    # A crashed tick is replayed. The quota was frozen at ALLOCATE and the
    # counters are cumulative, so re-entry must not overwrite either.
    await db.execute(
        "update research_iterations set items_admitted = 7 where run_id = $1 and iteration_no = 0",
        run.id,
    )
    again = await repo.start_iteration(
        run.id, 0, quota={"category": 999}, budget_snapshot={}, conn=db
    )
    assert again.quota == {"category": 20}
    assert again.items_admitted == 7

    reread = await repo.get_run(run.id, conn=db)
    assert reread.iteration_no == 0
    assert reread.started_at is not None


# ─────────────────────────────────────────────────────────────────────────────
# Items
# ─────────────────────────────────────────────────────────────────────────────


async def test_re_recording_the_same_item_does_not_duplicate_it(db):
    run = await _run(db)
    query = await _query(db, run)
    item = _content_item("vid-" + uuid.uuid4().hex[:8])

    first = await repo.upsert_content_item(
        item, market_code=run.market_code, first_seen_query_id=query.id, conn=db
    )
    second = await repo.upsert_content_item(
        item, market_code=run.market_code, first_seen_query_id=query.id, conn=db
    )
    assert first == second

    rows = await db.fetch(
        "select id, first_seen_query_id from content_items where platform_item_id = $1",
        item.platform_id,
    )
    assert len(rows) == 1
    # The query that FIRST surfaced an item is what per-query yield attribution
    # reads; a rediscovery must never rewrite it.
    assert rows[0]["first_seen_query_id"] == query.id


async def test_a_thinner_second_observation_does_not_blank_a_field(db):
    run = await _run(db)
    rich = _content_item("vid-" + uuid.uuid4().hex[:8])
    await repo.upsert_content_item(rich, market_code=run.market_code, conn=db)

    thin = rich.model_copy(update={"caption": None, "engagement": Engagement()})
    await repo.upsert_content_item(thin, market_code=run.market_code, conn=db)

    row = await db.fetchrow(
        "select caption, views from content_items where platform_item_id = $1", rich.platform_id
    )
    assert row["caption"] == "Theme X clip"
    assert row["views"] == 100


async def test_one_creator_row_per_account_however_the_handle_is_spelled(db):
    # 40 items from 3 creators is 3 independent observations, not 40. Two rows
    # for one account inflate n_eff and let a finding clear a support gate it
    # should have failed.
    uid = "creator-" + uuid.uuid4().hex[:8]
    first = await repo.upsert_creator(
        Creator(platform=Platform.TIKTOK, handle="@MixedCase", platform_user_id=uid), conn=db
    )
    second = await repo.upsert_creator(
        Creator(
            platform=Platform.TIKTOK,
            handle="mixedcase",
            platform_user_id=uid,
            follower_count=2500,
        ),
        conn=db,
    )
    assert first == second

    row = await db.fetchrow(
        "select handle_norm, id_basis, follower_count from creators where id = $1", first
    )
    assert row["handle_norm"] == "mixedcase"
    assert row["id_basis"] == "platform_id"
    assert row["follower_count"] == 2500


async def test_a_handle_only_creator_records_the_weaker_identity_basis(db):
    handle = "handleonly" + uuid.uuid4().hex[:8]
    creator_id = await repo.upsert_creator(
        Creator(platform=Platform.TIKTOK, handle=handle), conn=db
    )
    basis = await db.fetchval("select id_basis from creators where id = $1", creator_id)
    # A renamed account becomes a second creator and splits its own evidence,
    # so the design-effect estimate has to know this row is weaker.
    assert basis == "handle"


async def test_a_commerce_listing_round_trips_into_minor_units(db):
    platform_id = "sku-" + uuid.uuid4().hex[:8]
    item = CommerceItem(
        platform_id=platform_id,
        marketplace=Marketplace.SHOPEE,
        url=f"https://example.invalid/p/{platform_id}",
        title="Brand A product, Theme X variant",
        brand_surface_form="Brand A",
        brand_confidence=0.8,
        listing_kind=ListingKind.SINGLE,
        seller=Seller(name="Seller One", seller_id="seller-1", is_official_store=False),
        category_path=["Category", "Subcategory"],
        price=123.45,
        original_price=200.00,
        currency="PHP",
        discount_percent=38.0,
        rating=4.5,
        review_count=12,
        sold_count=300,
        variants=[Variant(name="Theme X", price=123.45)],
        provenance=Provenance(
            platform=Platform.SHOPEE,
            source_url=f"https://example.invalid/p/{platform_id}",
            discovery_query="theme x",
            sampling_strategy=SamplingStrategy.CATEGORY,
            collection_provider="apify:gio21~shopee-scraper",
            collection_method=CollectionMethod.MANAGED_COLLECTOR,
            discovered_at=datetime(2026, 2, 1, tzinfo=UTC),
        ),
    )
    first = await repo.upsert_commerce_item(item, conn=db)
    second = await repo.upsert_commerce_item(item, conn=db)
    assert first == second

    row = await db.fetchrow("select * from commerce_items where id = $1", first)
    assert row["price_minor"] == 12345
    assert row["original_price_minor"] == 20000
    # The column holds a fraction; CommerceItem.discount_percent is a percentage.
    assert float(row["discount_pct"]) == pytest.approx(0.38)
    # Marketplaces advertise against inflated "original" prices, so the
    # computed figure is what any price claim must read.
    assert float(row["discount_pct_computed"]) == pytest.approx(0.3828, abs=1e-4)
    assert row["price_status"] == "ok"
    # Sold count is a visibility proxy, never a sales figure (brief §28).
    assert row["sold_semantics"] == "unknown"
    assert row["seller_id"] is not None
    assert row["field_quality"]["price"] == "present"


async def test_a_fabricated_discount_is_dropped_and_flagged_not_clamped(db):
    platform_id = "sku-" + uuid.uuid4().hex[:8]
    item = CommerceItem(
        platform_id=platform_id,
        marketplace=Marketplace.LAZADA,
        url=f"https://example.invalid/p/{platform_id}",
        title="Brand A product",
        currency="PHP",
        price=10.0,
        discount_percent=99.0,
        provenance=Provenance(
            platform=Platform.LAZADA,
            source_url=f"https://example.invalid/p/{platform_id}",
            discovery_query="theme x",
            sampling_strategy=SamplingStrategy.CATEGORY,
            collection_provider="apify:fatihtahta~lazada-scraper",
            collection_method=CollectionMethod.MANAGED_COLLECTOR,
            discovered_at=datetime(2026, 2, 1, tzinfo=UTC),
        ),
    )
    item_id = await repo.upsert_commerce_item(item, conn=db)
    row = await db.fetchrow(
        "select discount_pct, quality_flags, is_degraded from commerce_items where id = $1",
        item_id,
    )
    # Storing 0.95 would launder a fabricated reference price into a number
    # that looks real; brief §28 wants the gap identified instead.
    assert row["discount_pct"] is None
    assert "discount_out_of_range" in row["quality_flags"]
    assert row["is_degraded"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Provenance
# ─────────────────────────────────────────────────────────────────────────────


async def test_two_queries_finding_one_item_keep_one_content_item(db):
    run = await _run(db)
    broad = await _query(db, run, strategy=SamplingStrategy.CATEGORY, text="theme x")
    targeted = await _query(db, run, strategy=SamplingStrategy.COMPETITOR, text="brand a theme x")
    item = _content_item("vid-" + uuid.uuid4().hex[:8])

    content_ids = set()
    for query in (broad, targeted):
        content_id = await repo.upsert_content_item(
            item, market_code=run.market_code, first_seen_query_id=broad.id, conn=db
        )
        content_ids.add(content_id)
        await repo.record_result(
            run_id=run.id,
            query_id=query.id,
            iteration_no=0,
            platform=Platform.TIKTOK,
            provider="apify:clockworks~tiktok-scraper",
            collection_method=CollectionMethod.MANAGED_COLLECTOR,
            url=item.url,
            url_canonical=item.url,
            external_id=item.platform_id,
            admitted=True,
            content_item_id=content_id,
            item_state=ItemState.RETRIEVED,
            conn=db,
        )

    # The item is run-independent and found twice: exactly one row.
    assert len(content_ids) == 1
    assert (
        await db.fetchval(
            "select count(*) from content_items where platform_item_id = $1", item.platform_id
        )
        == 1
    )

    results = await db.fetch(
        "select query_id, cu_charged, dedup_state from research_results where run_id = $1", run.id
    )
    # The rediscovery is visible and is charged nothing: charging twice would
    # let one item consume two units of the collection ceiling.
    assert sum(float(r["cu_charged"]) for r in results) == pytest.approx(1.0)
    assert (
        any(r["dedup_state"] == DedupState.DUP_IN_RUN.value for r in results) or len(results) == 2
    )


async def test_d3_two_queries_produce_two_research_results_rows(db):
    """D-3: sampling strategy belongs to the discovering query, not the item.

    This was an xfail against a schema whose unique index was
    (run_id, item_key), which physically collapsed the two strata into one row.
    Migration 0008 widened it to (run_id, query_id, item_key); the collapse
    mattered because only the reference strata may enter a denominator, so
    whichever query reached the item first was silently deciding whether it
    could be counted.
    """
    run = await _run(db)
    broad = await _query(db, run, strategy=SamplingStrategy.CATEGORY, text="theme x")
    targeted = await _query(db, run, strategy=SamplingStrategy.COMPETITOR, text="brand a theme x")
    item = _content_item("vid-" + uuid.uuid4().hex[:8])
    content_id = await repo.upsert_content_item(item, market_code=run.market_code, conn=db)

    for query in (broad, targeted):
        await repo.record_result(
            run_id=run.id,
            query_id=query.id,
            iteration_no=0,
            platform=Platform.TIKTOK,
            provider="apify:clockworks~tiktok-scraper",
            collection_method=CollectionMethod.MANAGED_COLLECTOR,
            url=item.url,
            url_canonical=item.url,
            external_id=item.platform_id,
            admitted=True,
            content_item_id=content_id,
            conn=db,
        )

    strategies = await db.fetch(
        """
        select q.strategy from research_results r
          join research_queries q on q.id = r.query_id
         where r.run_id = $1
        """,
        run.id,
    )
    assert {row["strategy"] for row in strategies} == {"category", "competitor"}


async def test_re_recording_one_result_does_not_double_charge_or_lose_attribution(db):
    run = await _run(db)
    query = await _query(db, run)
    item = _content_item("vid-" + uuid.uuid4().hex[:8])
    content_id = await repo.upsert_content_item(item, market_code=run.market_code, conn=db)

    common = {
        "run_id": run.id,
        "query_id": query.id,
        "iteration_no": 0,
        "platform": Platform.TIKTOK,
        "provider": "apify:clockworks~tiktok-scraper",
        "collection_method": CollectionMethod.MANAGED_COLLECTOR,
        "url": item.url,
        "url_canonical": item.url,
        "external_id": item.platform_id,
        "admitted": True,
        "conn": db,
    }
    first = await repo.record_result(raw_rank=3, **common)
    second = await repo.record_result(
        content_item_id=content_id, item_state=ItemState.ANALYZED, **common
    )

    assert first.id == second.id
    assert second.content_item_id == content_id
    assert second.item_state is ItemState.ANALYZED
    # Written on insert only: without this, "which query first found this" is an
    # application convention rather than a database property.
    assert second.first_seen_query_id == query.id
    assert second.cu_charged == pytest.approx(1.0)
    assert second.raw_rank == 3

    total = await db.fetchval(
        "select sum(cu_charged) from research_results where run_id = $1", run.id
    )
    assert float(total) == pytest.approx(1.0)


async def test_a_seed_strategy_is_refused_as_a_query_family(db):
    run = await _run(db)
    # D-1: a seed is a reason a query was generated, not a sixth stratum. Letting
    # it through is how operator assumptions leak into a corpus the system is
    # supposed to discover (brief §1).
    with pytest.raises(ValueError, match="SEED"):
        await _query(db, run, strategy=SamplingStrategy.SEED)


async def test_an_executed_query_is_not_recorded_twice(db):
    run = await _run(db)
    first = await _query(db, run, text="theme x", status=QueryStatus.ADMITTED)
    second = await _query(db, run, text="theme x", status=QueryStatus.ADMITTED)
    assert first.id == second.id
    assert await db.fetchval("select count(*) from research_queries where run_id = $1", run.id) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Log, ledger, stopping
# ─────────────────────────────────────────────────────────────────────────────


async def test_log_events_get_a_gap_free_sequence(db):
    run = await _run(db)
    events = [
        await repo.record_log_event(
            run_id=run.id,
            event_type=LogEventType.RUN_CREATED,
            summary="Run opened for Brand A.",
            conn=db,
        ),
        await repo.record_log_event(
            run_id=run.id,
            event_type=LogEventType.QUERIES_PROPOSED,
            summary="Proposed 4 opening queries.",
            iteration_no=0,
            phase=RunPhase.PROPOSE,
            payload={"proposed": 4},
            conn=db,
        ),
    ]
    # Brief §32 renders this as a narrative; a hole it cannot explain is worse
    # than no narrative at all.
    assert [e.seq for e in events] == [1, 2]
    assert events[1].payload == {"proposed": 4}
    assert events[1].phase is RunPhase.PROPOSE
    assert await db.fetchval("select log_seq from research_runs where id = $1", run.id) == 2


async def test_a_budget_line_is_recorded_and_settled_exactly_once(db):
    run = await _run(db)
    key = "llm:analysis:" + uuid.uuid4().hex[:8]

    reserved = await repo.record_budget_line(
        run_id=run.id,
        kind=BudgetKind.LLM,
        subsystem="analysis",
        idem_key=key,
        provider="anthropic",
        model="claude-sonnet-5",
        usd=0.03,
        input_tokens=4800,
        output_tokens=900,
        conn=db,
    )
    assert reserved.status is BudgetLineStatus.RESERVED
    assert reserved.settled_at is None

    settled = await repo.record_budget_line(
        run_id=run.id,
        kind=BudgetKind.LLM,
        subsystem="analysis",
        idem_key=key,
        status=BudgetLineStatus.SETTLED,
        usd=0.0296,
        conn=db,
    )
    assert settled.id == reserved.id
    assert settled.settled_at is not None

    # A retry after a crash is exactly when a budget is most likely to be
    # exceeded, so a settled line must never be re-charged.
    replay = await repo.record_budget_line(
        run_id=run.id,
        kind=BudgetKind.LLM,
        subsystem="analysis",
        idem_key=key,
        status=BudgetLineStatus.SETTLED,
        usd=99.0,
        conn=db,
    )
    assert replay.id == reserved.id
    assert replay.usd == pytest.approx(0.0296)

    total = await db.fetchval(
        "select sum(usd) from research_budget_ledger where run_id = $1", run.id
    )
    assert float(total) == pytest.approx(0.0296)


async def test_the_stop_reason_persists_and_names_the_ceiling_that_bound(db):
    run = await _run(db)
    finished = await repo.finish_run(
        run.id,
        state=RunState.COMPLETED,
        stop_reason=StopReason.LLM_BUDGET,
        stop_detail={"llm_usd": 120.0, "cap": 120.0},
        conn=db,
    )
    assert finished.state is RunState.COMPLETED
    assert finished.stopped_at is not None
    # Both budget ceilings collapse onto one research_stop_reason value, so the
    # exact ceiling is preserved alongside it -- brief §26 asks which one bound.
    assert finished.stop_reason_code == "budget_hard_stop"
    assert finished.stop_reason is StopReason.LLM_BUDGET

    reread = await repo.get_run(run.id, conn=db)
    assert reread.stop_reason is StopReason.LLM_BUDGET
    assert reread.stop_detail["llm_usd"] == 120.0


async def test_finish_run_refuses_a_non_terminal_state(db):
    run = await _run(db)
    # "The run ended" with no reason is the one failure brief §26 names, and the
    # CHECK that prevents it only fires on terminal states.
    with pytest.raises(ValueError, match="not completed"):
        await repo.finish_run(
            run.id, state=RunState.ITERATING, stop_reason=StopReason.OPERATOR, conn=db
        )


# ─────────────────────────────────────────────────────────────────────────────
# Platform vocabulary
# ─────────────────────────────────────────────────────────────────────────────


async def test_marketplace_platforms_are_mapped_onto_the_collection_sources_vocabulary(db):
    assert repo.db_platform(Platform.TIKTOK) == "tiktok"
    assert repo.db_platform(Platform.SHOPEE, "PH") == "shopee_ph"
    assert repo.db_platform(Platform.LAZADA, "ph") == "lazada_ph"
    # content_items.platform has an FK onto this vocabulary, so an unmapped
    # marketplace value would be rejected at insert; failing in Python names the
    # missing market instead.
    with pytest.raises(ValueError, match="market_code"):
        repo.db_platform(Platform.SHOPEE)

    known = {r["platform"] for r in await db.fetch("select platform from collection_sources")}
    assert {"shopee_ph", "lazada_ph", "tiktok"} <= known
