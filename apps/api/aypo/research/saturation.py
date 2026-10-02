"""Saturation metrics and stopping conditions (brief §26).

A run must stop, and it must record WHY. The hard ceilings (budget, items,
clock, iterations) live in aypo/research/budget.py. This module answers the
harder question: has the research stopped learning anything?

Five independent families are tracked. The design constraint that shapes all of
them is that **a single iteration's reading is noise**. Each metric is smoothed
over a window, and saturation requires agreement across families -- because any
one of them can go quiet for a reason that is not saturation at all.

The most important distinction here is between *saturation* and
*rank_exhaustion*. Both look identical from inside the loop: new queries stop
returning new material. But they have opposite remedies -- saturation means
stop, rank exhaustion means the platform's ranked results for these queries are
used up and the loop should go deeper or change source. Treating the second as
the first ends a run early with a corpus that merely looks complete.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from statistics import mean

import structlog

log = structlog.get_logger(__name__)

# A metric is only trustworthy once it has this many iterations behind it.
MIN_WINDOW = 2
WINDOW = 3

# Family thresholds. Each is a prior, not a measurement -- they are recorded
# here so they can be recalibrated against a real corpus rather than rediscovered.
NOVELTY_FLOOR = 0.15  # share of admitted items that are new
CLUSTER_STABILITY_FLOOR = 0.85  # label-invariant agreement between iterations
COMPETITOR_STABILITY_FLOOR = 0.90
MARGINAL_YIELD_FLOOR = 0.10  # new evidence per unit collected, vs the run's peak
HYPOTHESIS_PRODUCTIVITY_FLOOR = 0.10

# Families that must agree before saturation may be declared. Requiring three of
# the available families stops one quiet signal from ending a run on its own.
FAMILIES_REQUIRED = 3


class Family(StrEnum):
    NOVELTY = "novelty"
    CLUSTER = "cluster_structure"
    COMPETITOR = "competitor_set"
    YIELD = "marginal_evidence"
    HYPOTHESIS = "hypothesis_productivity"


class FamilyState(StrEnum):
    SATURATED = "saturated"
    ACTIVE = "active"
    UNAVAILABLE = "unavailable"  # not computable yet (too few iterations, or missing input)


@dataclass
class FamilyReading:
    family: Family
    state: FamilyState
    value: float | None = None
    smoothed: float | None = None
    threshold: float | None = None
    reason: str = ""


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 1.0


@dataclass
class SaturationTracker:
    """Accumulates per-iteration readings and decides whether to stop."""

    novelty: deque[float] = field(default_factory=lambda: deque(maxlen=WINDOW))
    cluster_stability: deque[float] = field(default_factory=lambda: deque(maxlen=WINDOW))
    competitor_stability: deque[float] = field(default_factory=lambda: deque(maxlen=WINDOW))
    marginal_yield: deque[float] = field(default_factory=lambda: deque(maxlen=WINDOW))
    hypothesis_productivity: deque[float] = field(default_factory=lambda: deque(maxlen=WINDOW))

    peak_yield: float = 0.0
    _prev_cluster_assignment: dict[str, str] | None = None
    _prev_competitors: set[str] | None = None
    # Set when a diversification probe showed the source still has unseen
    # material. Saturation is then not an admissible conclusion.
    rank_exhaustion_suspected: bool = False

    # ── per-iteration observation ────────────────────────────────────────────

    def observe_novelty(self, *, admitted: int, already_seen: int) -> float:
        """Share of admitted items not already in the corpus."""
        total = admitted + already_seen
        value = (admitted / total) if total else 0.0
        self.novelty.append(value)
        return value

    def observe_clusters(self, assignment: dict[str, str]) -> float | None:
        """Label-invariant agreement between consecutive cluster assignments.

        Cluster ids are recomputed each iteration and are not comparable, so
        agreement is measured over PAIRS of co-assigned items (a Rand-style
        measure) rather than over labels. Comparing ids directly would report
        churn that is purely an artefact of re-clustering.
        """
        previous = self._prev_cluster_assignment
        self._prev_cluster_assignment = dict(assignment)
        if previous is None:
            return None
        shared = sorted(set(previous) & set(assignment))
        if len(shared) < 4:
            return None
        agree = total = 0
        for i, a in enumerate(shared):
            for b in shared[i + 1 :]:
                same_before = previous[a] == previous[b]
                same_now = assignment[a] == assignment[b]
                agree += same_before == same_now
                total += 1
        value = agree / total if total else 1.0
        self.cluster_stability.append(value)
        return value

    def observe_competitors(self, competitors: set[str]) -> float | None:
        previous = self._prev_competitors
        self._prev_competitors = set(competitors)
        if previous is None:
            return None
        value = jaccard(previous, competitors)
        self.competitor_stability.append(value)
        return value

    def observe_yield(self, *, new_evidence: int, units_spent: int) -> float:
        """New evidence per unit collected, relative to the run's best iteration.

        Relative rather than absolute: what matters is that the return on
        further collection has collapsed, not that it is below some number.
        """
        raw = (new_evidence / units_spent) if units_spent else 0.0
        self.peak_yield = max(self.peak_yield, raw)
        value = (raw / self.peak_yield) if self.peak_yield else 0.0
        self.marginal_yield.append(value)
        return value

    def observe_hypotheses(self, *, generated: int, admitted: int) -> float:
        value = (admitted / generated) if generated else 0.0
        self.hypothesis_productivity.append(value)
        return value

    # ── assessment ───────────────────────────────────────────────────────────

    def _read(
        self, family: Family, series: deque[float], threshold: float, *, below_is_saturated: bool
    ) -> FamilyReading:
        if len(series) < MIN_WINDOW:
            return FamilyReading(
                family,
                FamilyState.UNAVAILABLE,
                reason=f"only {len(series)} of {MIN_WINDOW} iterations observed",
            )
        smoothed = mean(series)
        saturated = smoothed < threshold if below_is_saturated else smoothed > threshold
        return FamilyReading(
            family,
            FamilyState.SATURATED if saturated else FamilyState.ACTIVE,
            value=series[-1],
            smoothed=smoothed,
            threshold=threshold,
            reason=(
                f"smoothed {smoothed:.3f} {'<' if below_is_saturated else '>'} {threshold:.3f}"
            ),
        )

    def readings(self) -> list[FamilyReading]:
        return [
            self._read(Family.NOVELTY, self.novelty, NOVELTY_FLOOR, below_is_saturated=True),
            self._read(
                Family.CLUSTER,
                self.cluster_stability,
                CLUSTER_STABILITY_FLOOR,
                below_is_saturated=False,
            ),
            self._read(
                Family.COMPETITOR,
                self.competitor_stability,
                COMPETITOR_STABILITY_FLOOR,
                below_is_saturated=False,
            ),
            self._read(
                Family.YIELD, self.marginal_yield, MARGINAL_YIELD_FLOOR, below_is_saturated=True
            ),
            self._read(
                Family.HYPOTHESIS,
                self.hypothesis_productivity,
                HYPOTHESIS_PRODUCTIVITY_FLOOR,
                below_is_saturated=True,
            ),
        ]

    def is_saturated(self) -> tuple[bool, str, list[FamilyReading]]:
        """Whether the run has stopped learning, and why.

        Fails closed: with too few computable families, the honest answer is
        that saturation cannot be assessed, not that it has been reached.
        """
        readings = self.readings()
        available = [r for r in readings if r.state is not FamilyState.UNAVAILABLE]
        saturated = [r for r in available if r.state is FamilyState.SATURATED]

        if self.rank_exhaustion_suspected:
            return (
                False,
                "rank_exhaustion suspected: a diversification probe still returned new material, "
                "so quiet metrics mean this source's ranked results are used up, not that the "
                "research is complete",
                readings,
            )
        if len(available) < FAMILIES_REQUIRED:
            return (
                False,
                f"only {len(available)} of {FAMILIES_REQUIRED} required families are computable",
                readings,
            )
        if len(saturated) >= FAMILIES_REQUIRED:
            names = ", ".join(r.family.value for r in saturated)
            return True, f"{len(saturated)} families agree: {names}", readings
        return (
            False,
            f"{len(saturated)} of {FAMILIES_REQUIRED} families saturated",
            readings,
        )

    def snapshot(self) -> dict[str, object]:
        saturated, reason, readings = self.is_saturated()
        return {
            "saturated": saturated,
            "reason": reason,
            "rank_exhaustion_suspected": self.rank_exhaustion_suspected,
            "families": {
                r.family.value: {
                    "state": r.state.value,
                    "value": None if r.value is None else round(r.value, 4),
                    "smoothed": None if r.smoothed is None else round(r.smoothed, 4),
                    "threshold": r.threshold,
                    "reason": r.reason,
                }
                for r in readings
            },
        }
