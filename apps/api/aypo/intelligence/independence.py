"""Effective sample size (brief §28).

Forty items from three creators found by one query is not forty independent
observations, and treating it as such is how a confident claim gets made from
almost nothing. Every count threshold in the system is stated on the effective
sample size, and every proportion interval is widened by the design effect.

``deff = 1 + (m_bar - 1) * rho`` is the standard cluster-sampling design effect:
``m_bar`` is the mean cluster (creator) size and ``rho`` the intra-cluster
correlation. It is clamped, because both terms are estimated from few clusters
and an unclamped estimate swings wildly on small samples -- in the direction
that would *overstate* independence.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

DEFF_MIN = 1.0
DEFF_MAX = 5.0
RHO_DEFAULT = 0.3  # used when there is too little data to estimate rho


@dataclass
class Independence:
    n: int
    n_effective: float
    design_effect: float
    n_creators: int
    max_creator_share: float
    n_queries: int
    max_query_share: float

    @property
    def concentrated(self) -> bool:
        """True when the sample is dominated by one creator or one query."""
        return self.max_creator_share > 0.25 or self.max_query_share > 0.40


def design_effect(creator_ids: list[str | None], rho: float = RHO_DEFAULT) -> float:
    """Design effect from the creator-block structure of a sample."""
    known = [c for c in creator_ids if c]
    if len(known) < 2:
        return DEFF_MIN
    sizes = Counter(known)
    m_bar = len(known) / len(sizes)
    deff = 1.0 + (m_bar - 1.0) * rho
    return min(max(deff, DEFF_MIN), DEFF_MAX)


def assess(creator_ids: list[str | None], query_ids: list[str | None]) -> Independence:
    n = len(creator_ids)
    if n == 0:
        return Independence(0, 0.0, DEFF_MIN, 0, 0.0, 0, 0.0)

    creators = Counter(c for c in creator_ids if c)
    queries = Counter(q for q in query_ids if q)
    deff = design_effect(creator_ids)
    return Independence(
        n=n,
        n_effective=n / deff,
        design_effect=deff,
        n_creators=len(creators),
        max_creator_share=(creators.most_common(1)[0][1] / n) if creators else 0.0,
        n_queries=len(queries),
        max_query_share=(queries.most_common(1)[0][1] / n) if queries else 0.0,
    )


def wilson_interval(
    successes: int, total: int, *, deff: float = 1.0, z: float = 1.96
) -> tuple[float, float]:
    """Wilson score interval on the EFFECTIVE sample size.

    Wilson rather than normal-approximation because shares here are often small
    and samples modest, where the normal interval famously produces bounds
    outside [0, 1] and badly wrong coverage.
    """
    if total <= 0:
        return 0.0, 1.0
    p = successes / total
    n_eff = max(total / max(deff, 1.0), 1.0)
    denom = 1.0 + z * z / n_eff
    centre = (p + z * z / (2 * n_eff)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / n_eff + z * z / (4 * n_eff * n_eff))
    return max(0.0, centre - margin), min(1.0, centre + margin)
