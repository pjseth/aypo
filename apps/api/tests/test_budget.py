"""Budget allocation and stopping.

A hard monthly cap means an over-estimate collects less than it could, while an
under-estimate kills the run mid-way with a lopsided corpus. These tests pin the
asymmetry in the safe direction.
"""

import pytest

from aypo.models.provenance import Platform
from aypo.research.budget import (
    ESTIMATED_USD_PER_ITEM,
    MARKETPLACE_MIN_ITEMS,
    TARGET_MIX,
    Budget,
    StopReason,
)

ALL_SOURCES = {p.value: True for p in TARGET_MIX}


def make_budget(**kw) -> Budget:
    defaults = dict(
        max_collection_usd=5.0,
        max_llm_usd=120.0,
        max_items=4000,
        max_wallclock_seconds=3600,
        max_iterations=8,
    )
    defaults.update(kw)
    return Budget(**defaults)


def test_reserve_is_held_back_from_a_hard_cap():
    b = make_budget()
    # 20% reserve absorbs the provider's billing-report lag.
    assert b.usable_collection_usd == pytest.approx(4.0)


def test_prior_cycle_usage_reduces_what_this_run_may_spend():
    b = make_budget()
    b.collection_usd_now = 2.0
    assert b.usable_collection_usd == pytest.approx(2.4)


def test_plan_preserves_target_proportions():
    b = make_budget()
    plan = b.plan(ALL_SOURCES)
    # Brief §6 proportions must survive scaling, or the corpus skews toward
    # whichever source happened to run first.
    tiktok = plan.per_platform[Platform.TIKTOK]
    shopee = plan.per_platform[Platform.SHOPEE]
    assert shopee / tiktok == pytest.approx(
        TARGET_MIX[Platform.SHOPEE] / TARGET_MIX[Platform.TIKTOK], rel=0.1
    )


def test_plan_fits_inside_the_usable_budget():
    b = make_budget()
    plan = b.plan(ALL_SOURCES)
    assert plan.estimated_usd <= plan.usable_usd * 1.01
    assert plan.total_items > 0


def test_unavailable_source_is_excluded_and_its_budget_redistributed():
    b = make_budget()
    full = b.plan(ALL_SOURCES)
    without_youtube = b.plan({**ALL_SOURCES, "youtube": False})

    assert Platform.YOUTUBE in without_youtube.excluded
    assert Platform.YOUTUBE not in without_youtube.per_platform
    # A missing credential must shrink coverage, not total volume.
    assert without_youtube.per_platform[Platform.TIKTOK] > full.per_platform[Platform.TIKTOK]


def test_marketplace_allocation_respects_actor_minimum():
    # Both marketplace actors reject a run below 10 items.
    b = make_budget(max_collection_usd=0.2)
    plan = b.plan(ALL_SOURCES)
    for marketplace in (Platform.SHOPEE, Platform.LAZADA):
        allocated = plan.per_platform.get(marketplace, 0)
        assert allocated == 0 or allocated >= MARKETPLACE_MIN_ITEMS


def test_no_available_sources_yields_empty_plan():
    plan = make_budget().plan({p.value: False for p in TARGET_MIX})
    assert plan.total_items == 0
    assert len(plan.excluded) == len(TARGET_MIX)


def test_tiny_budget_still_produces_a_valid_plan_or_nothing():
    plan = make_budget(max_collection_usd=0.01).plan(ALL_SOURCES)
    assert plan.estimated_usd <= max(plan.usable_usd * 1.01, ESTIMATED_USD_PER_ITEM * 20)


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("collection_usd_now", 4.5, StopReason.COLLECTION_BUDGET),
        ("items_collected", 4000, StopReason.ITEM_TARGET),
        ("iterations", 8, StopReason.MAX_ITERATIONS),
    ],
)
def test_each_ceiling_stops_the_run_with_its_own_reason(field, value, expected):
    b = make_budget()
    setattr(b, field, value)
    # Brief §26 requires the reason a run stopped be recorded, not just a halt.
    assert b.exceeded() is expected


def test_llm_ceiling_is_independent_of_collection_ceiling():
    from aypo.llm.pricing import Spend

    b = make_budget()
    b.ledger.record(Spend("llm", "claude-sonnet-5", 121.0))
    assert b.exceeded() is StopReason.LLM_BUDGET


def test_fresh_budget_has_not_exceeded_anything():
    assert make_budget().exceeded() is None


def test_can_afford_respects_the_reserve():
    b = make_budget()
    b.collection_usd_now = 3.9
    assert b.can_afford(1) is True
    assert b.can_afford(100_000) is False


def test_snapshot_reports_both_cycle_and_run_spend():
    b = make_budget()
    b.collection_usd_at_start = 1.0
    b.collection_usd_now = 1.4
    snap = b.snapshot()
    # The cap is monthly and shared, so both numbers are needed to interpret it.
    assert snap["collection_usd_cycle"] == 1.4
    assert snap["collection_usd_this_run"] == pytest.approx(0.4)


# ── strategy mix (brief §8, and the sampling-bias mechanisms) ─────────────────


def test_early_iteration_is_broad_not_brand_led():
    # Brief §8: the corpus must not consist mostly of subject-branded content.
    plan = make_budget().plan(ALL_SOURCES, iteration=0)
    from aypo.models.provenance import SamplingStrategy

    assert plan.per_strategy[SamplingStrategy.CATEGORY] > plan.per_strategy[SamplingStrategy.BRAND]


def test_brand_share_is_capped_at_thirty_percent():
    from aypo.models.provenance import SamplingStrategy
    from aypo.research.budget import MAX_BRAND_CORPUS_SHARE

    for iteration in (0, 1, 2, 5):
        plan = make_budget().plan(ALL_SOURCES, iteration=iteration)
        brand = plan.per_strategy.get(SamplingStrategy.BRAND, 0)
        assert brand / plan.total_items <= MAX_BRAND_CORPUS_SHARE + 0.01


def test_targeted_strategies_only_appear_after_discovery_has_happened():
    from aypo.models.provenance import SamplingStrategy

    # Nothing has been discovered at iteration 0, so there is nothing to target.
    first = make_budget().plan(ALL_SOURCES, iteration=0).per_strategy
    assert SamplingStrategy.COMPETITOR not in first
    assert SamplingStrategy.THEME not in first
    assert SamplingStrategy.HYPOTHESIS not in first

    later = make_budget().plan(ALL_SOURCES, iteration=3).per_strategy
    assert later.get(SamplingStrategy.COMPETITOR, 0) > 0
    assert later.get(SamplingStrategy.HYPOTHESIS, 0) > 0


def test_reference_stratum_stays_substantial_in_late_iterations():
    # Only category and brand items may enter a denominator or a trend test, so
    # if the reference stratum stops growing, trend power stops growing with it.
    plan = make_budget().plan(ALL_SOURCES, iteration=5)
    assert plan.reference_items / plan.total_items >= 0.4


def test_reference_strata_excludes_query_conditioned_strategies():
    from aypo.models.provenance import SamplingStrategy
    from aypo.research.budget import REFERENCE_STRATA

    # competitor/theme/hypothesis items are query-conditioned: including them in
    # a denominator would bias every share they appear in.
    assert SamplingStrategy.COMPETITOR not in REFERENCE_STRATA
    assert SamplingStrategy.THEME not in REFERENCE_STRATA
    assert SamplingStrategy.HYPOTHESIS not in REFERENCE_STRATA
    assert REFERENCE_STRATA == {SamplingStrategy.CATEGORY, SamplingStrategy.BRAND}
