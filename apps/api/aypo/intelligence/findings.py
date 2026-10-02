"""Finding generation (brief §3, §27, §28).

This is what makes the product credible, and it is built on one rule:

    **Code computes every number. The model only writes prose, and may only
    cite identifiers it was handed.**

A model asked to both analyse and narrate will produce a number that sounds
right. So statistics are computed here, passed in as an immutable evidence
packet, and the generated text is then checked back against that packet: any
finding citing an unknown id, or stating a number that does not match a computed
statistic, is rejected rather than repaired into something plausible.

Brief §3 requires four kinds of claim be distinguished, because a measurement
and an interpretation are not the same thing and must not read the same way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

import structlog
from pydantic import BaseModel, Field

from aypo.intelligence.independence import Independence
from aypo.llm.client import LLMClient, LLMRefused, LLMSchemaError

log = structlog.get_logger(__name__)

# Tokens the model must use instead of writing numbers or naming sources itself.
STAT_TOKEN = re.compile(r"\{\{stat:(S\d+)\}\}")
EVIDENCE_TOKEN = re.compile(r"\[(E\d+)\]")
# A bare number in generated prose is almost always a statistic the model
# computed itself. Years and small ordinals are permitted.
BARE_NUMBER = re.compile(r"(?<![\w{:])(\d+(?:\.\d+)?)\s*%|(?<![\w{:])(\d{2,})(?![\w}])")


class ClaimType(StrEnum):
    """How a statement was arrived at (brief §3)."""

    OBSERVED = "observed"  # directly present in the collected data
    COMPUTED = "computed"  # calculated from the dataset by code
    INTERPRETED = "interpreted"  # a model's reading of the evidence
    HYPOTHESIS = "hypothesis"  # not established; needs investigation


class ConfidenceBand(StrEnum):
    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"
    INSUFFICIENT = "insufficient"


class RejectionReason(StrEnum):
    UNKNOWN_STATISTIC = "cited_unknown_statistic"
    UNKNOWN_EVIDENCE = "cited_unknown_evidence"
    UNCITED_NUMBER = "stated_an_uncomputed_number"
    NO_EVIDENCE = "cited_no_evidence_at_all"
    FORBIDDEN_LANGUAGE = "used_forbidden_language"
    BELOW_CONFIDENCE_FLOOR = "below_confidence_floor"


# Phrases a finding may never contain. Each corresponds to a guardrail in brief
# §28 that prose can quietly violate even when the statistics are correct.
FORBIDDEN_PATTERNS: list[tuple[str, str]] = [
    (r"\bmost (?:consumers|filipinos|people|shoppers)\b", "generalises the sample to a population"),
    (r"\b(?:proves|proven|definitively|conclusively)\b", "overstates what a sample can establish"),
    (r"\bcaus(?:es|ed|ing)\b", "asserts causation from co-occurrence"),
    (r"\bdrives? (?:sales|purchase|demand)\b", "treats visibility or engagement as sales"),
    (r"\bmarket (?:share|leader)\b", "claims a market-wide fact from a sample"),
    (r"\b(?:all|every) (?:consumer|shopper|viewer)s?\b", "universal claim from a sample"),
]


@dataclass
class ComputedStat:
    """A number computed by code. The model may reference it, never restate it."""

    ref: str  # "S1"
    label: str
    value: float
    rendered: str  # exactly how it must appear in prose
    numerator: int | None = None
    denominator: int | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    ci_method: str = ""
    formula: str = ""


@dataclass
class EvidenceRef:
    """A real item the model may cite."""

    ref: str  # "E1"
    kind: str  # content | commerce | cluster | review
    item_id: str
    excerpt: str = ""
    creator_id: str | None = None
    url: str | None = None


@dataclass
class EvidencePacket:
    """Everything the generator is allowed to use. Immutable once built."""

    population_frame: str
    statistics: list[ComputedStat] = field(default_factory=list)
    evidence: list[EvidenceRef] = field(default_factory=list)
    independence: Independence | None = None
    competing_evidence: list[EvidenceRef] = field(default_factory=list)
    scope_statement: str = ""

    @property
    def stat_refs(self) -> set[str]:
        return {s.ref for s in self.statistics}

    @property
    def evidence_refs(self) -> set[str]:
        return {e.ref for e in self.evidence} | {e.ref for e in self.competing_evidence}

    def render(self) -> str:
        lines = ["# POPULATION FRAME", f"  {self.population_frame}", ""]
        lines.append("# COMPUTED STATISTICS (reference by token; never restate a number)")
        for stat in self.statistics:
            ci = (
                f"  95% CI [{stat.ci_low:.3f}, {stat.ci_high:.3f}] ({stat.ci_method})"
                if stat.ci_low is not None
                else ""
            )
            lines.append(f"  {{{{stat:{stat.ref}}}}} = {stat.rendered}   {stat.label}")
            lines.append(f"      value={stat.value}  n={stat.denominator}{ci}")
        lines += ["", "# EVIDENCE (cite by token; these are the only ids that exist)"]
        for ev in self.evidence:
            lines.append(f"  [{ev.ref}] {ev.kind}  {ev.excerpt[:150]}")
        if self.competing_evidence:
            lines += ["", "# COMPETING EVIDENCE (items that do NOT fit the pattern)"]
            for ev in self.competing_evidence:
                lines.append(f"  [{ev.ref}] {ev.kind}  {ev.excerpt[:150]}")
        if self.independence:
            ind = self.independence
            lines += [
                "",
                "# INDEPENDENCE",
                f"  n={ind.n} effective_n={ind.n_effective:.1f} "
                f"creators={ind.n_creators} queries={ind.n_queries}",
                f"  largest single creator share={ind.max_creator_share:.0%}, "
                f"largest single query share={ind.max_query_share:.0%}",
            ]
        return "\n".join(lines)


class DraftFinding(BaseModel):
    claim_template: str = Field(
        description=(
            "the claim, using {{stat:S#}} for every number and [E#] for every cited item. "
            "Never write a number directly."
        )
    )
    claim_type: ClaimType
    scope_statement: str = Field(
        description="the sentence bounding this claim to the analysed sample"
    )
    interpretation: str = Field(description="why this might matter, labelled as interpretation")
    what_would_refute: str = Field(description="the observation that would overturn this")
    follow_up_queries: list[str] = Field(default_factory=list, max_length=5)
    contested: bool = Field(description="true when the competing evidence genuinely undercuts it")


class DraftBatch(BaseModel):
    findings: list[DraftFinding] = Field(max_length=8)


GENERATOR_SYSTEM = """You write findings for a market-intelligence product, from an evidence
packet that has already been computed for you.

You do not have access to the dataset. You have exactly what is in the packet, and you may use
nothing else.

Absolute rules:

1. NEVER write a number. Every quantity must appear as its token, {{stat:S1}} and so on. If a
   number you want to state is not in the packet, you cannot state it. This is checked
   mechanically and a finding that states an uncomputed number is discarded.
2. NEVER cite an item that is not in the packet. Cite as [E1], [E2]. Invented ids are checked and
   discarded.
3. Every finding must carry at least one evidence citation.
4. Label the claim type honestly:
   - `observed` for something directly present in the data
   - `computed` for something the statistics establish
   - `interpreted` for your reading of what it might mean
   - `hypothesis` for something not established that needs investigating
   Do not label an interpretation as observed because it feels solid.
5. Scope every claim to the analysed sample. This is a non-probability sample of public content,
   not a survey. Phrases like "most consumers", "proves", "market share" and "drives sales" are
   forbidden and are checked.
6. Do not assert causation. Co-occurrence and temporal order do not establish it here.
7. If competing evidence is supplied, address it. Set `contested` when it genuinely undercuts the
   claim; do not quietly omit it.
8. Say what would REFUTE the finding. A claim with no refutation condition is not a finding.
9. Fewer, better-supported findings. An empty list is a valid answer when the packet does not
   support anything.

Return only the structured object."""


@dataclass
class ValidationOutcome:
    accepted: bool
    reasons: list[RejectionReason] = field(default_factory=list)
    detail: list[str] = field(default_factory=list)


def validate_draft(draft: DraftFinding, packet: EvidencePacket) -> ValidationOutcome:
    """Check a generated finding back against the packet it was built from."""
    reasons: list[RejectionReason] = []
    detail: list[str] = []
    text = f"{draft.claim_template} {draft.interpretation}"

    cited_stats = set(STAT_TOKEN.findall(draft.claim_template))
    unknown_stats = cited_stats - packet.stat_refs
    if unknown_stats:
        reasons.append(RejectionReason.UNKNOWN_STATISTIC)
        detail.append(f"cited statistics that do not exist: {sorted(unknown_stats)}")

    cited_evidence = set(EVIDENCE_TOKEN.findall(text))
    unknown_evidence = cited_evidence - packet.evidence_refs
    if unknown_evidence:
        reasons.append(RejectionReason.UNKNOWN_EVIDENCE)
        detail.append(f"cited evidence that does not exist: {sorted(unknown_evidence)}")

    if not cited_evidence:
        reasons.append(RejectionReason.NO_EVIDENCE)
        detail.append("no evidence cited")

    # A bare number in the prose is a number the model produced itself.
    without_tokens = STAT_TOKEN.sub("", draft.claim_template)
    if BARE_NUMBER.search(without_tokens):
        reasons.append(RejectionReason.UNCITED_NUMBER)
        detail.append("stated a number that was not computed")

    for pattern, why in FORBIDDEN_PATTERNS:
        if re.search(pattern, text, re.I):
            reasons.append(RejectionReason.FORBIDDEN_LANGUAGE)
            detail.append(f"{why}: /{pattern}/")

    return ValidationOutcome(not reasons, reasons, detail)


def render_claim(draft: DraftFinding, packet: EvidencePacket) -> str:
    """Substitute computed values into the template for display."""
    lookup = {s.ref: s.rendered for s in packet.statistics}
    return STAT_TOKEN.sub(lambda m: lookup.get(m.group(1), m.group(0)), draft.claim_template)


def score_confidence(
    packet: EvidencePacket, *, effect_size: float | None = None
) -> tuple[float, ConfidenceBand]:
    """Confidence from sample size, source diversity and concentration.

    Source concentration is penalised hard and deliberately: forty items from
    three creators found by one query is not forty independent observations, and
    a confidence score that ignores that is the single easiest way to make a
    weak finding look strong.
    """
    ind = packet.independence
    if ind is None or ind.n == 0:
        return 0.0, ConfidenceBand.INSUFFICIENT

    # Saturating in effective sample size: returns diminish, and n_eff already
    # carries the design effect.
    size_term = min(1.0, ind.n_effective / 60.0)
    creator_term = min(1.0, ind.n_creators / 20.0)
    query_term = min(1.0, ind.n_queries / 6.0)
    concentration_penalty = 1.0 - max(
        max(0.0, ind.max_creator_share - 0.15) * 1.5,
        max(0.0, ind.max_query_share - 0.30) * 1.0,
    )
    effect_term = 1.0 if effect_size is None else min(1.0, abs(effect_size) / 0.2)

    score = (0.35 * size_term + 0.25 * creator_term + 0.15 * query_term + 0.25 * effect_term) * max(
        0.0, concentration_penalty
    )

    if ind.n_effective < 15 or ind.n_creators < 4:
        return score, ConfidenceBand.INSUFFICIENT
    if score >= 0.70:
        band = ConfidenceBand.HIGH
    elif score >= 0.45:
        band = ConfidenceBand.MODERATE
    elif score >= 0.25:
        band = ConfidenceBand.LOW
    else:
        band = ConfidenceBand.INSUFFICIENT
    return score, band


async def generate_findings(
    packet: EvidencePacket, *, client: LLMClient | None = None, max_findings: int = 5
) -> tuple[list[tuple[DraftFinding, str]], list[tuple[DraftFinding, ValidationOutcome]]]:
    """Generate and validate. Returns (accepted, rejected)."""
    from aypo.config import get_settings

    llm = client or LLMClient()
    try:
        result = await llm.structured(
            DraftBatch,
            system=GENERATOR_SYSTEM,
            content=packet.render() + f"\n\nWrite at most {max_findings} findings.",
            effort=get_settings().aypo_reasoning_effort,
            max_tokens=8000,
            label="findings",
        )
    except (LLMRefused, LLMSchemaError) as exc:
        log.warning("findings.generation_failed", error=str(exc)[:200])
        return [], []

    accepted: list[tuple[DraftFinding, str]] = []
    rejected: list[tuple[DraftFinding, ValidationOutcome]] = []
    for draft in result.value.findings:
        outcome = validate_draft(draft, packet)
        if outcome.accepted:
            accepted.append((draft, render_claim(draft, packet)))
        else:
            rejected.append((draft, outcome))
            log.info("findings.rejected", reasons=[r.value for r in outcome.reasons])
    log.info("findings.generated", accepted=len(accepted), rejected=len(rejected))
    return accepted, rejected
