"""Query generation (brief §8).

The five query families exist so the corpus is deliberately mixed rather than
dominated by subject-branded content:

* **brand**      — the research subject by name
* **category**   — the surrounding category, naming no brand
* **competitor** — generated only AFTER competitors begin to emerge
* **theme**      — generated from themes discovered in the data
* **hypothesis** — generated to test whether an observed pattern holds elsewhere

Only the first two can exist before anything has been discovered. The other
three are produced from what the corpus actually contained, which is why nothing
in this module contains a theme, occasion, competitor or cultural reference: the
seed vocabulary is the subject and the category words the operator supplied, and
everything after that is read back out of the data.

Deduplication is lexical AND semantic. The model will otherwise re-propose the
same search in slightly different words every iteration and burn collection
budget re-collecting the same items.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import structlog
from pydantic import BaseModel, Field

from aypo.config import Effort, get_settings
from aypo.llm.client import LLMClient, LLMRefused, LLMSchemaError
from aypo.models.provenance import Platform, SamplingStrategy

log = structlog.get_logger(__name__)

# Two queries are the same question when their embeddings are this close. Set
# from observed near-duplicate pairs rather than theory; it is deliberately
# strict, because wrongly merging two genuinely different searches costs
# coverage while wrongly keeping both only costs a little budget.
SEMANTIC_DUPLICATE_THRESHOLD = 0.93


class GeneratedQuery(BaseModel):
    text: str = Field(description="the search string exactly as it should be issued")
    strategy: SamplingStrategy
    platform: Platform | None = Field(
        default=None, description="null means issue on every available platform"
    )
    rationale: str = Field(
        description="why this query was generated and what it is expected to surface"
    )
    is_disconfirming: bool = Field(
        default=False,
        description=(
            "true when the query is designed to surface evidence AGAINST a current "
            "hypothesis rather than for it"
        ),
    )


class QueryBatch(BaseModel):
    queries: list[GeneratedQuery] = Field(max_length=24)
    reasoning: str = Field(default="", max_length=800)


SEED_SYSTEM = """You generate search queries for an automated market-research system.

You are given a research subject, a market, and the category words the operator used to describe
what is being researched. You know nothing else about this subject, this category, or this market,
and you must not pretend otherwise.

Produce two kinds of query:

- `brand`    — queries naming the research subject.
- `category` — queries about the surrounding product category that DO NOT name the subject or any
               other brand. These exist so the corpus shows what the category looks like
               independently of the subject.

Rules:

1. Do not invent brand names. You have not been given a competitor list, and guessing one would
   plant an answer the system is supposed to discover. Only the subject may be named, and only in
   `brand` queries.
2. Do not encode assumptions about who consumes this category, on what occasions, in what social
   situations, or for what reasons. "<category> for students", "<category> party", "<category> at
   work" are all forbidden: each asserts a consumption context that is the system's job to find.
3. Do not use slang, local idiom or cultural references unless the operator's own words contained
   them. You do not know which ones apply here.
4. Write queries as a person would actually type them into a search box. Short. No boolean syntax.
5. Vary the angle: the product itself, the category generally, how people talk about it, what
   people buy, what people say about buying it. Do not vary merely by synonym.
6. Prefer queries in the market's majority languages. If the operator supplied language codes,
   write some queries in each.

Return only the structured object."""


FOLLOWUP_SYSTEM = """You generate follow-up search queries for an automated market-research system
that has already collected and analysed a first sample.

You are given: what has been discovered so far (labels the analyst composed from the material,
with how often each was observed), the brands that were actually seen, open hypotheses, and the
queries already run.

Produce queries of these kinds:

- `category`   — broad category queries that keep the reference sample growing. Still name no brand.
- `competitor` — queries about brands that were ACTUALLY OBSERVED in the collected material.
- `theme`      — queries built from labels that were ACTUALLY DISCOVERED, to test whether a pattern
                 seen in a few items appears more widely.
- `hypothesis` — queries that would confirm or REFUTE a specific open hypothesis.

Rules:

1. Every `competitor` query must name a brand from the observed-brands list, spelled exactly as
   given. Never introduce a brand that is not in that list.
2. Every `theme` query must be built from a discovered label in the list supplied. Never introduce
   a theme, occasion, behaviour or cultural reference that is not there. If you think of a related
   concept that was not observed, that is exactly the kind of assumption this system exists to
   avoid making.
3. At least one quarter of your queries must be disconfirming: designed to surface material that
   would WEAKEN a current hypothesis, not support it. Mark them `is_disconfirming`. A loop that
   only looks for confirmation will find it whether or not it is there.
4. Do not repeat a query already run, or a trivial rewording of one. You are shown the list.
5. A label observed only once or twice is weak evidence. Prefer testing recurring patterns, and say
   in the rationale how often the label was seen.
6. Write queries as a person would type them. Short, no boolean syntax.

Return only the structured object."""


def normalise_query(text: str) -> str:
    """Lexical key for exact-duplicate detection."""
    lowered = re.sub(r"[^\w\s]", " ", text.lower())
    return " ".join(sorted(lowered.split()))


@dataclass
class QueryDeduplicator:
    """Rejects a query that repeats one already run.

    Lexical first (free), then semantic (costs an embedding). The research agent
    reliably re-proposes the same search in new words across iterations, and
    every duplicate is collection budget spent re-collecting known items.
    """

    seen_normalised: set[str] = field(default_factory=set)
    seen_embeddings: list[list[float]] = field(default_factory=list)
    seen_texts: list[str] = field(default_factory=list)
    threshold: float = SEMANTIC_DUPLICATE_THRESHOLD

    def add(self, text: str, embedding: list[float] | None = None) -> None:
        self.seen_normalised.add(normalise_query(text))
        if embedding is not None:
            self.seen_embeddings.append(embedding)
            self.seen_texts.append(text)

    def is_lexical_duplicate(self, text: str) -> bool:
        return normalise_query(text) in self.seen_normalised

    def nearest(self, embedding: list[float]) -> tuple[float, str | None]:
        """Cosine similarity to the closest query already run."""
        if not self.seen_embeddings:
            return 0.0, None
        best, best_text = 0.0, None
        for candidate, text in zip(self.seen_embeddings, self.seen_texts, strict=True):
            dot = sum(a * b for a, b in zip(embedding, candidate, strict=True))
            if dot > best:
                best, best_text = dot, text
        return best, best_text

    def is_semantic_duplicate(self, embedding: list[float]) -> tuple[bool, str | None]:
        score, text = self.nearest(embedding)
        return score >= self.threshold, text


@dataclass
class DiscoveredContext:
    """What the corpus has actually yielded so far.

    Everything here is read out of collected data. Nothing is supplied by the
    operator or assumed by the system.
    """

    labels: list[tuple[str, str, int]] = field(default_factory=list)  # (facet, value, support)
    observed_brands: list[tuple[str, int]] = field(default_factory=list)  # (surface_form, count)
    open_hypotheses: list[tuple[str, str]] = field(default_factory=list)  # (id, statement)
    queries_run: list[str] = field(default_factory=list)

    def render(self, *, max_labels: int = 40, max_brands: int = 25) -> str:
        lines: list[str] = []
        lines.append("# DISCOVERED LABELS (facet | label | times observed)")
        if self.labels:
            for facet, value, support in sorted(self.labels, key=lambda x: -x[2])[:max_labels]:
                lines.append(f"  {facet} | {value} | {support}")
        else:
            lines.append("  (nothing discovered yet)")

        lines.append("")
        lines.append("# BRANDS ACTUALLY OBSERVED (surface form | times seen)")
        if self.observed_brands:
            for brand, count in sorted(self.observed_brands, key=lambda x: -x[1])[:max_brands]:
                lines.append(f"  {brand} | {count}")
        else:
            lines.append("  (none observed yet)")

        lines.append("")
        lines.append("# OPEN HYPOTHESES")
        if self.open_hypotheses:
            for hid, statement in self.open_hypotheses:
                lines.append(f"  [{hid}] {statement}")
        else:
            lines.append("  (none)")

        lines.append("")
        lines.append("# QUERIES ALREADY RUN (do not repeat or reword)")
        for q in self.queries_run[-80:]:
            lines.append(f"  {q}")
        return "\n".join(lines)


class QueryGenerator:
    def __init__(self, client: LLMClient | None = None, effort: Effort | None = None) -> None:
        settings = get_settings()
        self._client = client or LLMClient()
        self._effort = effort or settings.aypo_reasoning_effort

    async def seed_queries(
        self,
        *,
        subject: str,
        category_terms: list[str],
        market: str,
        languages: list[str],
        n: int = 16,
    ) -> QueryBatch:
        """The first iteration's queries, before anything has been discovered."""
        prompt = "\n".join(
            [
                f"RESEARCH SUBJECT: {subject}",
                f"MARKET: {market}",
                f"LANGUAGES: {', '.join(languages) if languages else 'unspecified'}",
                "CATEGORY WORDS THE OPERATOR USED: " + ", ".join(category_terms),
                "",
                f"Generate about {n} queries, roughly two category queries for every brand query.",
                "The category queries must name no brand at all.",
            ]
        )
        return await self._generate(SEED_SYSTEM, prompt, label="seed_queries")

    async def followup_queries(
        self, context: DiscoveredContext, *, subject: str, market: str, n: int = 16
    ) -> QueryBatch:
        """Queries derived from what the corpus actually contained."""
        prompt = "\n".join(
            [
                f"RESEARCH SUBJECT: {subject}",
                f"MARKET: {market}",
                "",
                context.render(),
                "",
                f"Generate about {n} follow-up queries. At least a quarter must be disconfirming.",
            ]
        )
        return await self._generate(FOLLOWUP_SYSTEM, prompt, label="followup_queries")

    async def _generate(self, system: str, prompt: str, *, label: str) -> QueryBatch:
        try:
            result = await self._client.structured(
                QueryBatch,
                system=system,
                content=prompt,
                effort=self._effort,
                max_tokens=6000,
                label=label,
            )
        except (LLMRefused, LLMSchemaError) as exc:
            # A failed generation must not kill the run; the caller falls back
            # to the queries it already has (brief §7).
            log.warning("queries.generation_failed", label=label, error=str(exc)[:200])
            return QueryBatch(queries=[], reasoning=f"generation failed: {exc}")
        log.info("queries.generated", label=label, n=len(result.value.queries))
        return result.value


def validate_batch(
    batch: QueryBatch,
    *,
    observed_brands: set[str],
    discovered_labels: set[str],
    allow_targeted: bool,
) -> tuple[list[GeneratedQuery], list[str]]:
    """Reject queries that invent what they were supposed to discover.

    The prompt forbids this, but a prompt is not an enforcement mechanism.
    Brief §1 and §34 are the product's core promise, so they are checked.
    """
    kept: list[GeneratedQuery] = []
    rejected: list[str] = []
    brands_lower = {b.lower() for b in observed_brands}
    labels_lower = {label.lower() for label in discovered_labels}

    for query in batch.queries:
        strategy = query.strategy
        if not allow_targeted and strategy in (
            SamplingStrategy.COMPETITOR,
            SamplingStrategy.THEME,
            SamplingStrategy.HYPOTHESIS,
        ):
            rejected.append(
                f"{query.text!r}: {strategy.value} query before anything was discovered"
            )
            continue
        lowered = query.text.lower()
        if strategy is SamplingStrategy.COMPETITOR and not any(
            brand in lowered for brand in brands_lower
        ):
            rejected.append(f"{query.text!r}: competitor query naming no observed brand")
            continue
        if strategy is SamplingStrategy.THEME and not any(
            # A theme query must reuse a discovered label, though not verbatim:
            # the useful query is often a phrase from inside the label.
            any(token in lowered for token in label.split() if len(token) > 3)
            for label in labels_lower
        ):
            rejected.append(f"{query.text!r}: theme query built on no discovered label")
            continue
        kept.append(query)
    return kept, rejected
