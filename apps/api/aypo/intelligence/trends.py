"""Trend detection (brief §22).

**The single most important correctness rule in the system.**

The corpus is collected at one moment in time, so the distribution of
publication dates is shaped by platform ranking, recency bias, survivorship and
metadata attrition -- not by real-world prevalence. Write the corpus count for
theme t in period p as

    n_t(p) = N_world(t,p) * g(p) * r_t(p) / Z

where ``g`` is the exposure function. ``g`` increases steeply toward collection
time, so **a per-period count series is a near-deterministic manufacturer of
upward trends**: at g growing 1.5x per month, a perfectly flat theme reads
+50%/month, and a theme genuinely declining 20%/month still reads +20%.

Within-period composition share cancels ``g`` exactly:

    share_t(p) = n_t(p) / N(p)  ->  N_world(t,p)*r_t(p) / sum_t' N_world(t',p)*r_t'(p)

So **composition is identified; volume is not**. This module therefore never
reports a claim about volume. Every temporal statement is "share of analysed
content published in period p", and absolute-volume trends are hard-refused.

The residual ``r_t(p)`` -- a recommender that starts favouring one theme -- is
not eliminated. It is stated as the common-exposure assumption and named in
every claim's method string.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

import structlog

from aypo.intelligence.independence import wilson_interval

log = structlog.get_logger(__name__)

# A trend claim needs enough periods to be more than two points joined by a line.
MIN_PERIODS = 3
# Per-period floors, on the EFFECTIVE sample size.
MIN_ITEMS_PER_PERIOD = 15
MIN_CREATORS_PER_PERIOD = 5
# A period dominated by one creator describes that creator, not the market.
MAX_CREATOR_SHARE = 0.35
# Significance after multiple-comparison control.
ALPHA = 0.05


class Classification(StrEnum):
    ESTABLISHED = "established"
    GROWING = "growing"
    EMERGING = "emerging"
    WEAK_SIGNAL = "weak_signal"
    STABLE = "stable"
    DECLINING = "declining"
    # Not a classification but a refusal: the data cannot support any of the above.
    INSUFFICIENT = "insufficient_data"


class RefusalReason(StrEnum):
    VOLUME_CLAIM_UNIDENTIFIED = "volume_claim_unidentified"
    TOO_FEW_PERIODS = "too_few_periods"
    TOO_FEW_ITEMS = "too_few_items_per_period"
    TOO_FEW_CREATORS = "too_few_creators_per_period"
    CREATOR_CONCENTRATION = "creator_concentration"
    NO_REFERENCE_STRATUM = "no_reference_stratum_items"


@dataclass
class PeriodPoint:
    """One period's observation for one theme.

    ``n_corpus`` is the denominator and MUST include noise points (excluding
    them lets the clustering parameter rescale every share) and MUST exclude
    items that failed analysis or lack a usable date (including them lets a
    per-period pipeline failure rate rescale every share).
    """

    period: str
    n_theme: int
    n_corpus: int
    n_creators: int
    max_creator_share: float
    design_effect: float = 1.0

    @property
    def share(self) -> float:
        return self.n_theme / self.n_corpus if self.n_corpus else 0.0

    @property
    def n_effective(self) -> float:
        return self.n_corpus / max(self.design_effect, 1.0)

    @property
    def qualifies(self) -> tuple[bool, RefusalReason | None]:
        if self.n_effective < MIN_ITEMS_PER_PERIOD:
            return False, RefusalReason.TOO_FEW_ITEMS
        if self.n_creators < MIN_CREATORS_PER_PERIOD:
            return False, RefusalReason.TOO_FEW_CREATORS
        if self.max_creator_share > MAX_CREATOR_SHARE:
            return False, RefusalReason.CREATOR_CONCENTRATION
        return True, None

    def interval(self) -> tuple[float, float]:
        return wilson_interval(self.n_theme, self.n_corpus, deff=self.design_effect)


@dataclass
class TrendResult:
    theme_id: str
    classification: Classification
    points: list[PeriodPoint] = field(default_factory=list)
    qualifying_periods: int = 0
    slope: float | None = None
    effect: float | None = None  # odds ratio per period
    effect_ci: tuple[float, float] | None = None
    p_value: float | None = None
    q_value: float | None = None
    refusal_reason: RefusalReason | None = None
    method: str = ""
    caveats: list[str] = field(default_factory=list)

    @property
    def is_claim(self) -> bool:
        """Whether a temporal claim may be made at all."""
        return self.classification not in (Classification.INSUFFICIENT,)

    @property
    def directional(self) -> bool:
        return self.classification in (Classification.GROWING, Classification.DECLINING)


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def analyse_theme(theme_id: str, points: list[PeriodPoint]) -> TrendResult:
    """Classify one theme's trajectory, or refuse.

    Refusal is a first-class outcome. Brief §22 permits growth and decline
    claims only with sufficient temporal coverage, so "we cannot say" must be
    representable rather than rounded to "stable".
    """
    method = (
        "within-period composition share; weighted least squares on logit(share) "
        "against period index; Wilson intervals at effective sample size; "
        "assumes common exposure across themes within a period"
    )
    ordered = sorted(points, key=lambda p: p.period)
    qualifying = [p for p in ordered if p.qualifies[0]]

    if len(ordered) < MIN_PERIODS:
        return TrendResult(
            theme_id,
            Classification.INSUFFICIENT,
            ordered,
            len(qualifying),
            refusal_reason=RefusalReason.TOO_FEW_PERIODS,
            method=method,
        )
    if len(qualifying) < MIN_PERIODS:
        reason = next((p.qualifies[1] for p in ordered if not p.qualifies[0]), None)
        return TrendResult(
            theme_id,
            Classification.INSUFFICIENT,
            ordered,
            len(qualifying),
            refusal_reason=reason,
            method=method,
        )

    # Weighted least squares on logit(share).
    #
    # The weight is the INVERSE VARIANCE OF THE LOGIT, n_eff * p * (1 - p), not
    # simply n_eff. That is the statistically correct weight here: a share near
    # 0 or 1 is estimated far less precisely on the logit scale than one near
    # 0.5 at the same sample size, and weighting by n_eff alone would let a
    # precise-looking extreme period dominate the slope. With this weight the
    # slope's standard error is exactly 1/sqrt(Sxx).
    xs = list(range(len(qualifying)))
    ys = [_logit(p.share) for p in qualifying]
    ws = []
    for point_ in qualifying:
        share = min(max(point_.share, 1e-6), 1 - 1e-6)
        ws.append(max(point_.n_effective * share * (1 - share), 1e-6))

    sw = sum(ws)
    mx = sum(w * x for w, x in zip(ws, xs, strict=True)) / sw
    my = sum(w * y for w, y in zip(ws, ys, strict=True)) / sw
    sxx = sum(w * (x - mx) ** 2 for w, x in zip(ws, xs, strict=True))
    sxy = sum(w * (x - mx) * (y - my) for w, x, y in zip(ws, xs, ys, strict=True))
    slope = sxy / sxx if sxx else 0.0

    # Overdispersion: real period-to-period scatter routinely exceeds the
    # binomial ideal, so the interval is widened by the observed Pearson
    # dispersion. Never narrowed -- a dispersion below 1 is treated as 1, so a
    # lucky-looking fit cannot manufacture significance.
    residuals = [y - (my + slope * (x - mx)) for x, y in zip(xs, ys, strict=True)]
    dof = max(len(qualifying) - 2, 1)
    dispersion = max(1.0, sum(w * r * r for w, r in zip(ws, residuals, strict=True)) / dof)
    se = math.sqrt(dispersion / sxx) if sxx > 0 else float("inf")

    z = slope / se if se > 0 and math.isfinite(se) else 0.0
    p_value = math.erfc(abs(z) / math.sqrt(2))
    odds_ratio = math.exp(slope)
    ci = (math.exp(slope - 1.96 * se), math.exp(slope + 1.96 * se)) if math.isfinite(se) else None

    mean_share = sum(p.share for p in qualifying) / len(qualifying)
    latest = qualifying[-1]

    if p_value < ALPHA and slope > 0:
        classification = Classification.GROWING
    elif p_value < ALPHA and slope < 0:
        classification = Classification.DECLINING
    elif mean_share >= 0.10:
        classification = Classification.ESTABLISHED
    elif latest.share >= 0.03 and latest.share > mean_share:
        classification = Classification.EMERGING
    elif mean_share < 0.01:
        classification = Classification.WEAK_SIGNAL
    else:
        classification = Classification.STABLE

    caveats = [
        "Share of analysed content published in each period, within this sample. "
        "Not a market-wide measurement.",
        "Volume is not estimable from this corpus; only composition is.",
    ]
    if len(qualifying) < len(ordered):
        caveats.append(
            f"{len(ordered) - len(qualifying)} period(s) excluded for insufficient coverage."
        )

    return TrendResult(
        theme_id,
        classification,
        ordered,
        len(qualifying),
        slope=slope,
        effect=odds_ratio,
        effect_ci=ci,
        p_value=p_value,
        method=method,
        caveats=caveats,
    )


def benjamini_hochberg(results: list[TrendResult], alpha: float = ALPHA) -> list[TrendResult]:
    """Control the false discovery rate across every theme tested.

    Testing dozens of themes at alpha=0.05 produces several 'significant'
    trends by chance alone. Without this, the Trends surface fills with noise
    that looks exactly like signal.
    """
    testable = [r for r in results if r.p_value is not None and r.directional]
    m = len(testable)
    if m == 0:
        return results
    for rank, result in enumerate(sorted(testable, key=lambda r: r.p_value or 1.0), start=1):
        result.q_value = min(1.0, (result.p_value or 1.0) * m / rank)
    # Enforce monotonicity, then demote anything that no longer survives.
    running = 1.0
    for result in sorted(testable, key=lambda r: -(r.p_value or 1.0)):
        running = min(running, result.q_value or 1.0)
        result.q_value = running
        if running > alpha:
            result.classification = Classification.STABLE
            result.caveats.append(
                f"Direction not significant after controlling for {m} simultaneous theme tests."
            )
    return results


def refuse_volume_claim(theme_id: str) -> TrendResult:
    """Volume trends are never estimable from this corpus."""
    return TrendResult(
        theme_id,
        Classification.INSUFFICIENT,
        refusal_reason=RefusalReason.VOLUME_CLAIM_UNIDENTIFIED,
        method="refused: corpus exposure is unknown, so absolute volume is unidentified",
        caveats=[
            "The corpus was collected at one moment, so publication-date counts reflect "
            "platform ranking rather than real-world volume."
        ],
    )
