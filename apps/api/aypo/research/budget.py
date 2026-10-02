"""Research run budget (brief §26).

A run is bounded by four independent ceilings -- managed-collection spend, LLM
spend, item count and wall clock -- and stops on whichever binds first, always
recording which one it was.

The collection ceiling is enforced against **live provider usage**, not against
our own estimate. Apify bills pay-per-event with reporting lag, so a local
tally drifts from reality; on a hard monthly cap that drift is the difference
between a run that completes and one that dies mid-way with a corpus too
lopsided to analyse.

Scaling is proportional, not uniform. Brief §6 gives target volumes per source;
a smaller budget keeps those *proportions* so the corpus composition stays
balanced, rather than exhausting the budget on whichever source ran first.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum

import httpx
import structlog

from aypo.config import get_settings
from aypo.llm.pricing import Ledger
from aypo.models.provenance import Platform, SamplingStrategy

log = structlog.get_logger(__name__)

# Brief §6 research targets, as the midpoint of each stated range. Used only as
# proportions; absolute volume comes from the budget.
TARGET_MIX: dict[Platform, int] = {
    Platform.TIKTOK: 1000,
    Platform.YOUTUBE: 400,
    Platform.INSTAGRAM: 150,
    Platform.FACEBOOK: 150,
    Platform.SHOPEE: 1125,
    Platform.LAZADA: 750,
}

# Strategy mix per iteration (brief §8). Early iterations are deliberately broad
# and later ones targeted, but the reference strata never fall below what a
# denominator needs.
#
# Two hard constraints come from the architecture's sampling-bias answer and are
# NOT tunable preferences:
#
#   * Only `brand` and `category` items may enter a denominator or a trend test.
#     Items found by competitor/theme/hypothesis queries are query-conditioned,
#     so including them would bias every share they appear in. They still enter
#     the Content Explorer, findings evidence and cluster formation.
#   * Cumulative `brand`-strategy share of admitted social items stays at or
#     below 0.30. Brief §8 forbids a corpus dominated by subject-branded
#     content, and a mere allocation weight bounds what is *requested*, not what
#     is *admitted* -- so this is enforced at the admission gate as well.
STRATEGY_MIX: dict[int, dict[SamplingStrategy, float]] = {
    # iteration 0: nothing has been discovered yet, so there is nothing to
    # target; the split is broad by necessity as well as by design.
    0: {SamplingStrategy.CATEGORY: 0.65, SamplingStrategy.BRAND: 0.35},
    1: {
        SamplingStrategy.CATEGORY: 0.50,
        SamplingStrategy.BRAND: 0.20,
        SamplingStrategy.COMPETITOR: 0.15,
        SamplingStrategy.THEME: 0.15,
    },
}
# Every later iteration. The reference strata (category + brand) stay at 45% so
# the denominator keeps growing; without that, trend tests stop gaining power
# exactly when the loop gets interesting.
STRATEGY_MIX_LATE: dict[SamplingStrategy, float] = {
    SamplingStrategy.CATEGORY: 0.30,
    SamplingStrategy.BRAND: 0.15,
    SamplingStrategy.COMPETITOR: 0.20,
    SamplingStrategy.THEME: 0.20,
    SamplingStrategy.HYPOTHESIS: 0.15,
}

# Strategies whose items may enter a denominator or a trend test.
REFERENCE_STRATA = frozenset({SamplingStrategy.CATEGORY, SamplingStrategy.BRAND})
# Comparisons between brands use category-strategy items only: brand-strategy
# items are brand-conditioned, so the subject's share inside any theme is
# inflated there by construction.
COMPARISON_STRATUM = SamplingStrategy.CATEGORY

MAX_BRAND_CORPUS_SHARE = 0.30
MAX_QUERY_SHARE_PER_ITERATION = 0.25
MAX_CREATOR_SHARE_PER_ITERATION = 0.10

# Measured on this account: ~$0.06 of reported usage across ~22 collected items
# and 5 actor runs. Rounded up, because under-estimating overruns a hard cap
# while over-estimating only collects less than it could have.
ESTIMATED_USD_PER_ITEM = 0.005

# Both marketplace actors reject a per-run item count below 10, so no
# allocation to them can be smaller than this.
MARKETPLACE_MIN_ITEMS = 10


class StopReason(StrEnum):
    COLLECTION_BUDGET = "collection_budget_exhausted"
    LLM_BUDGET = "llm_budget_exhausted"
    ITEM_TARGET = "item_target_reached"
    WALLCLOCK = "wallclock_exhausted"
    MAX_ITERATIONS = "max_iterations_reached"
    SATURATED = "no_new_information"
    NO_HYPOTHESES = "no_new_hypotheses"
    OPERATOR = "stopped_by_operator"
    SOURCES_UNAVAILABLE = "all_sources_unavailable"


@dataclass
class CollectionPlan:
    """Per-source and per-strategy item allocation for one iteration."""

    per_platform: dict[Platform, int] = field(default_factory=dict)
    per_strategy: dict[SamplingStrategy, int] = field(default_factory=dict)
    estimated_usd: float = 0.0
    usable_usd: float = 0.0
    scale_factor: float = 1.0
    excluded: dict[Platform, str] = field(default_factory=dict)

    @property
    def total_items(self) -> int:
        return sum(self.per_platform.values())

    @property
    def reference_items(self) -> int:
        """Items eligible to enter a denominator or a trend test."""
        return sum(n for s, n in self.per_strategy.items() if s in REFERENCE_STRATA)

    def describe(self) -> str:
        parts = [f"{p.value}={n}" for p, n in sorted(self.per_platform.items()) if n]
        skipped = [f"{p.value}({why})" for p, why in sorted(self.excluded.items())]
        return (
            f"{self.total_items} items at {self.scale_factor:.0%} of target mix, "
            f"~${self.estimated_usd:.2f} of ${self.usable_usd:.2f} usable"
            + (f" | planned: {', '.join(parts)}" if parts else "")
            + (f" | unavailable: {', '.join(skipped)}" if skipped else "")
        )


@dataclass
class Budget:
    max_collection_usd: float
    max_llm_usd: float
    max_items: int
    max_wallclock_seconds: float
    max_iterations: int
    # Fraction held back so a lagging usage report cannot push the account past
    # a hard cap.
    reserve_fraction: float = 0.2

    started_at: float = field(default_factory=time.monotonic)
    collection_usd_at_start: float = 0.0
    collection_usd_now: float = 0.0
    items_collected: int = 0
    iterations: int = 0
    ledger: Ledger = field(default_factory=Ledger)

    @classmethod
    def from_settings(cls, *, max_collection_usd: float | None = None) -> Budget:
        s = get_settings()
        return cls(
            max_collection_usd=(max_collection_usd if max_collection_usd is not None else 5.0),
            max_llm_usd=s.aypo_max_llm_usd,
            max_items=s.aypo_max_collection_units,
            max_wallclock_seconds=s.aypo_max_wallclock_minutes * 60,
            max_iterations=s.aypo_max_iterations,
        )

    # ── live accounting ──────────────────────────────────────────────────────

    async def refresh_collection_usage(self) -> float:
        """Read actual provider usage. Falls back to the local estimate."""
        token = get_settings().secret("apify_token")
        if not token:
            return self.collection_usd_now
        try:
            async with httpx.AsyncClient(
                timeout=20.0, headers={"Authorization": f"Bearer {token}"}
            ) as client:
                r = await client.get("https://api.apify.com/v2/users/me/usage/monthly")
            if r.status_code == 200:
                data = r.json()["data"]
                self.collection_usd_now = float(
                    data.get("totalUsageCreditsUsdAfterVolumeDiscount")
                    or data.get("monthlyUsageUsd")
                    or 0.0
                )
        except Exception as exc:
            # Never let a usage check abort a run; the local estimate stands in.
            log.debug("budget.usage_check_failed", error=str(exc))
        return self.collection_usd_now

    @property
    def collection_spent(self) -> float:
        """Spend attributable to this run, not the whole billing cycle."""
        return max(0.0, self.collection_usd_now - self.collection_usd_at_start)

    @property
    def usable_collection_usd(self) -> float:
        """What this run may spend, after the cycle's prior usage and reserve.

        The cap is monthly and shared, so anything already consumed this cycle
        is not available to this run.
        """
        remaining_in_cycle = max(0.0, self.max_collection_usd - self.collection_usd_now)
        return remaining_in_cycle * (1.0 - self.reserve_fraction)

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    # ── planning ─────────────────────────────────────────────────────────────

    def plan(self, available: dict[str, bool], *, iteration: int = 0) -> CollectionPlan:
        """Allocate items across the sources that are actually usable.

        Budget freed by an unavailable source is redistributed across the
        others rather than forfeited, so a missing credential shrinks coverage
        but not volume.
        """
        plan = CollectionPlan(usable_usd=self.usable_collection_usd)
        usable_platforms = {}
        for platform, weight in TARGET_MIX.items():
            if available.get(platform.value):
                usable_platforms[platform] = weight
            else:
                plan.excluded[platform] = "no credential"

        if not usable_platforms:
            return plan

        affordable_items = int(plan.usable_usd / ESTIMATED_USD_PER_ITEM)
        budget_items = min(affordable_items, self.max_items)
        target_total = sum(usable_platforms.values())
        plan.scale_factor = min(1.0, budget_items / target_total) if target_total else 0.0

        for platform, weight in usable_platforms.items():
            allocation = int(weight * plan.scale_factor)
            if platform in (Platform.SHOPEE, Platform.LAZADA):
                # Below the actor minimum, allocating nothing is more honest
                # than a run that will be rejected.
                allocation = (
                    max(MARKETPLACE_MIN_ITEMS, allocation)
                    if allocation >= MARKETPLACE_MIN_ITEMS // 2
                    else 0
                )
            plan.per_platform[platform] = allocation

        plan.estimated_usd = plan.total_items * ESTIMATED_USD_PER_ITEM
        plan.per_strategy = self._strategy_allocation(plan.total_items, iteration)
        return plan

    @staticmethod
    def _strategy_allocation(total: int, iteration: int) -> dict[SamplingStrategy, int]:
        mix = STRATEGY_MIX.get(iteration, STRATEGY_MIX_LATE)
        allocation = {s: int(total * w) for s, w in mix.items()}
        # Brand share is capped on the plan as well as at admission, so an
        # early broad iteration cannot quietly set up a brand-heavy corpus.
        cap = int(total * MAX_BRAND_CORPUS_SHARE)
        if allocation.get(SamplingStrategy.BRAND, 0) > cap:
            spill = allocation[SamplingStrategy.BRAND] - cap
            allocation[SamplingStrategy.BRAND] = cap
            allocation[SamplingStrategy.CATEGORY] = (
                allocation.get(SamplingStrategy.CATEGORY, 0) + spill
            )
        return allocation

    # ── stopping ─────────────────────────────────────────────────────────────

    def exceeded(self) -> StopReason | None:
        """The first binding ceiling, or None."""
        if self.collection_usd_now >= self.max_collection_usd * (1 - self.reserve_fraction):
            return StopReason.COLLECTION_BUDGET
        if self.ledger.total_usd >= self.max_llm_usd:
            return StopReason.LLM_BUDGET
        if self.items_collected >= self.max_items:
            return StopReason.ITEM_TARGET
        if self.elapsed_seconds >= self.max_wallclock_seconds:
            return StopReason.WALLCLOCK
        if self.iterations >= self.max_iterations:
            return StopReason.MAX_ITERATIONS
        return None

    def can_afford(self, items: int) -> bool:
        return (
            self.collection_usd_now + items * ESTIMATED_USD_PER_ITEM
            <= self.max_collection_usd * (1 - self.reserve_fraction)
        )

    def snapshot(self) -> dict[str, object]:
        return {
            "collection_usd_cycle": round(self.collection_usd_now, 4),
            "collection_usd_this_run": round(self.collection_spent, 4),
            "collection_cap_usd": self.max_collection_usd,
            "usable_collection_usd": round(self.usable_collection_usd, 4),
            "llm_usd": round(self.ledger.total_usd, 4),
            "llm_cap_usd": self.max_llm_usd,
            "items_collected": self.items_collected,
            "iterations": self.iterations,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "stop_reason": (r.value if (r := self.exceeded()) else None),
        }
