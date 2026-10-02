# 07 — Hallucination-Proof Finding Generation, Confidence Model, and Grounded Ask-the-Data

> **Normative parents:** `docs/BRIEF.md` §3 (signal/context/evidence + the four claim layers),
> §27 (evidence system), §28 (data quality guardrails), §31.7 (Ask the Data), §32 (research run view),
> §33 (no arbitrary AI scores), §1 and §34 (discovery not assumption; do not seed the subject brand's findings).
>
> **Scope of this document.** This subsystem turns *computed candidate patterns* into *published findings*,
> assigns them confidence, defends them against an adversarial referee, stores them with complete evidence
> linkage, and serves a grounded question-answering agent over the same dataset. It owns the `findings`,
> `finding_evidence` and related tables, and the Ask-the-Data agent.
>
> **Out of scope (owned elsewhere):** collection and provenance capture, multimodal per-item analysis,
> embeddings, clustering, trend math on the corpus, competitor discovery, the research/hypothesis loop.
> Those subsystems feed this one through the contracts in §12.
>
> **Anti-seeding compliance.** No prompt, lexicon, taxonomy, threshold, fixture or default in this document
> names the subject brand, its presumed consumers, occasions, competitors, themes, slang, or any category
> assumption. The subject brand arrives at runtime as `{subject_brand}`. All examples use `Brand A`,
> `Brand B`, `Theme X`, `Cluster Y`, `Platform P1`. The vocabulary guard (§4.3 V9) and the Ask-the-Data
> label-resolution rule (§11.6) are the *mechanical* enforcement of §1 and §34: the generator and the QA
> agent cannot name a theme, brand, occasion or slang term that does not already exist verbatim in the
> collected data.
>
> **One carve-out, stated rather than hidden.** The *guardrail* lexicons in §2.3 must match the language the
> content is actually written in, and the stat formatter must know a currency. Those are **market
> configuration**, not findings: they are loaded from a `MarketLocalePack` keyed by `research_runs.market`,
> never hard-coded in `lexicons.py`. No locale pack may contain a category, brand, occasion, theme or
> behaviour term — only demonyms, function words, causal connectives, demographic descriptors and a currency
> code. An earlier draft of this document inlined Philippine-market tokens (`filipinos`, `dahil`, `sanhi`,
> `class a`/`class c`, `currency_php`) directly into the lexicons and the `ComputedStat` unit enum; that was
> a §1/§34 violation of the letter if not the spirit, and it is corrected below.
>
> **Scale honesty.** Every threshold in this document is calibrated against the §6 target corpus
> (~1,500 analysed videos). `docs/ENVIRONMENT.md` records that the Apify plan is free-tier (\$5/month) and
> `YOUTUBE_API_KEY` is absent, so the realised corpus will initially be far smaller. The correct response is
> **not** to lower the thresholds — it is to label the outcome, which §6.7, §6.10 and §8 now do explicitly.

---

## 1. The one-paragraph summary of how this works

Code — never the model — computes every number and assembles an **evidence packet** containing real
database ids, pre-formatted statistics with their formulas and confidence intervals, the population frame,
the supporting items, **and the contradicting items**. The language model receives that packet and writes
prose in which *every number is a token* (`{{stat:S3}}`) and *every assertive clause carries citations*
(`[E12]`). The model's output schema contains no numeric fields at all, and a validator rejects any digit
in its output. A battery of mechanical validators then rejects unknown ids, uncited quantifiers, claims
whose cited items do not actually exhibit the asserted property, invented vocabulary, invented
demographics, visibility-as-endorsement, engagement-as-preference, causal language, and trend language
without temporal coverage. Survivors go to an **independent adversarial referee** that tries to kill them
and defaults to *refuted* when uncertain. Survivors of that are scored by an explicit confidence formula
whose sample-size term is deflated for source concentration, so forty items from three creators found by
one query score like a handful of observations, not forty. Findings below the floor are stored and shown
only as *insufficient data*, with a statement of what would be needed. The renderer re-validates at serve
time, so nothing reaches a marketer's screen that has not passed every check.

---

## 2. Claim typology (BRIEF §3)

### 2.1 The enum

```sql
create type claim_type as enum ('observed', 'calculated', 'interpreted', 'hypothesis');
```

```python
class ClaimType(StrEnum):
    OBSERVED     = "observed"      # directly observed data
    CALCULATED   = "calculated"    # calculated pattern
    INTERPRETED  = "interpreted"   # model interpretation
    HYPOTHESIS   = "hypothesis"    # requires more investigation
```

A *published finding* in the UI is normally a **composite**: an `observed` or `calculated` claim is the
headline, and `interpreted` / `hypothesis` claims hang off it as children via `findings.parent_finding_id`.
This is the §3 SIGNAL / CONTEXT / EVIDENCE structure made structural rather than stylistic — the
interpretation is a separate row with a separate confidence treatment, so it can never be read as the
observation.

### 2.2 What each type may assert

| Type | May assert | May **not** assert | Mandatory fields | Hedging |
|---|---|---|---|---|
| `observed` | That specific rows exist in the dataset and carry specific analysed attributes. Counts and identities, scoped to the sample. "N items in the analysed sample show Brand A and Theme X together." | Rates, comparisons, deltas, growth, mechanism, cause, generalisation beyond the sample. | `sample_size`, ≥1 `ComputedStat` of domain `corpus_composition`, ≥`min_citations` evidence rows | None required; the claim is a statement of record |
| `calculated` | Arithmetic over observed rows: proportions, rates, ratios, lift, share-of-presence, index, Mann-Kendall τ, deltas between periods — **with the denominator named and a CI attached**. | Mechanism, cause, intent, motivation, audience psychology, market-wide fact, any number not present as a `ComputedStat`. | Everything `observed` requires, **plus** `effect_metric`, `effect_value`, CI bounds, `population_frame_size` | None required, but the denominator clause is forced by the renderer |
| `interpreted` | A reading of a parent `calculated`/`observed` claim: what it might mean, why it might matter, what it resembles. Must be explicitly framed as a reading. | Any new number. Any new entity, theme or label. Cause. Demographics. Anything the parent did not establish. It may not stand alone. | `parent_finding_id` (NOT NULL enforced by CHECK), ≥1 hedge marker, `what_would_refute` | **Required.** Must contain a hedge from `HEDGE_LEXICON` ("appears to", "is consistent with", "one reading is", "may indicate") |
| `hypothesis` | A statement that is *not yet supported* and a plan to test it. | Anything asserted as true. It gets **no confidence score and no confidence band at all** (DB CHECK), only a `prior_support_strength` label, so it cannot be mistaken for a conclusion. | `what_would_refute`, `what_would_confirm`, `follow_up_queries` (≥2), optional `hypothesis_id` link into the research loop | **Required.** Must be phrased as a question or with a modal ("may", "might", "could") |

### 2.3 The grammar is mechanically enforced

Each type maps to a lexicon gate in `aypo/intelligence/findings/lexicons.py` (validator **V8**, §4.3).
Lexicons are matched on normalised, lemmatised tokens and are **entirely structural** — they contain no
category, brand, theme, occasion or behaviour terms, so they do not seed anything.

The sets below are the **market-neutral core**. Language coverage beyond English is supplied by a
`MarketLocalePack` resolved from `research_runs.market` at load time and merged into the core:

```python
@dataclass(frozen=True)
class MarketLocalePack:
    market: str                     # ISO-3166 alpha-2, from research_runs.market
    currency_code: str              # ISO-4217, used for ComputedStat.unit='currency'
    causal_terms: frozenset[str]    # causal connectives in the market's languages
    population_terms: frozenset[str]  # demonyms and collective nouns for the market's people
    demographic_terms: frozenset[str] # locally used age/gender/class/income descriptors
    hedge_terms: frozenset[str]

def load_lexicons(market: str) -> Lexicons: ...   # core | MarketLocalePack(market)
```

A locale pack that contains a category, brand, product, occasion, flavour or behaviour term fails
`test_locale_pack_is_structural` in CI. The pack is the *only* place a market-specific string may live, and
it exists so that a pattern cannot be smuggled in as a guardrail.

```python
CAUSAL_LEXICON        = {"because", "causes", "caused", "drives", "driven by", "due to",
                         "leads to", "results in", "as a result of"}   # + locale.causal_terms
GROWTH_LEXICON        = {"growing", "rising", "declining", "falling", "emerging", "surging",
                         "momentum", "accelerating", "trending up", "trending down", "shift"}
POPULATION_LEXICON    = {"consumers", "shoppers", "the market", "people", "everyone",
                         "the category", "audiences"}   # + locale.population_terms (demonyms).
                                                        # banned unqualified; allowed only inside
                                                        # the phrase "in the analysed sample"
DEMOGRAPHIC_LEXICON   = {"gen z", "millennial", "boomer", "teen", "teenager", "young", "older",
                         "men", "women", "male", "female", "mothers", "students",
                         "income", "household", "ages", "year-olds"}  # + locale.demographic_terms
ENDORSEMENT_LEXICON   = {"prefers", "prefer", "loves", "favourite", "favorite", "endorses",
                         "chooses", "recommends", "loyal", "trusts", "advocates"}
PREFERENCE_LEXICON    = ENDORSEMENT_LEXICON | {"likes", "wants", "demands", "desires"}
ENGAGEMENT_LEXICON    = {"views", "likes", "comments", "shares", "saves", "engagement",
                         "viral", "reach", "impressions"}
PURCHASE_LEXICON      = {"buys", "buying", "purchases", "sales", "sold", "selling",
                         "revenue", "demand", "basket", "conversion"}
HEDGE_LEXICON         = {"appears to", "appear to", "is consistent with", "one reading is",
                         "may indicate", "may reflect", "suggests", "could indicate",
                         "is compatible with"}
QUANTIFIER_LEXICON    = {"most", "many", "few", "majority", "minority", "half", "almost all",
                         "nearly all", "a handful", "dozens", "hundreds", "often", "rarely",
                         "frequently", "usually", "typically", "one", "two", "three", "four",
                         "five", "six", "seven", "eight", "nine", "ten"}
```

| Type | Forbidden lexicons | Required markers |
|---|---|---|
| `observed` | `CAUSAL`, `GROWTH`, `POPULATION` (unqualified), `DEMOGRAPHIC`, `ENDORSEMENT`, `PREFERENCE` | — |
| `calculated` | `CAUSAL`, `POPULATION` (unqualified), `DEMOGRAPHIC`; `GROWTH` only if the trend gate passes (V13) | denominator clause |
| `interpreted` | `CAUSAL`, `DEMOGRAPHIC`, any stat token not already on the parent | ≥1 `HEDGE_LEXICON` hit |
| `hypothesis` | assertive mood (no hedge/modal/question) | modal or question form + test plan |

Any token from `QUANTIFIER_LEXICON` is a failure **unless** the same sentence contains a `{{stat:...}}`
token. That is what stops "most items show Theme X" from slipping past the digit ban.

### 2.4 How the UI must visually distinguish them (BRIEF §33, §31)

Colour alone is never the distinction (accessibility, and a screenshot pasted into a deck loses the legend).
Each type carries **four** simultaneous signals: a word chip, an icon, a border treatment, and a required
scope line.

| Type | Chip text (always rendered) | Icon | Card treatment | Required line under the claim | Where it may appear |
|---|---|---|---|---|---|
| `observed` | `OBSERVED` | filled square | solid 1px left rail, neutral surface | `Observed in N of M analysed items.` | anywhere |
| `calculated` | `CALCULATED` | sigma glyph | solid 1px left rail + a stat strip showing every `ComputedStat` as `label · display_value · [CI] · n/denominator · formula-on-hover` | `Calculated over the analysed sample only (M items, D days).` | anywhere |
| `interpreted` | `INTERPRETATION — NOT A MEASUREMENT` | open circle | **inset** block nested inside its parent card, different surface tone, 2px dashed left rail; never a top-level card | `Reading of the finding above. No additional data supports this wording.` + a back-link to the parent | only nested under its parent; never in the Overview hero |
| `hypothesis` | `HYPOTHESIS — TO INVESTIGATE` | question glyph | separate lane titled *To investigate*, dashed border on all sides, no confidence band rendered | `Not a conclusion. Confirm by: <what_would_confirm>. Refute by: <what_would_refute>.` | only in the *To investigate* lane and in the Research Run view |

Hard rendering rules, enforced in `aypo/intelligence/findings/renderer.py` and re-asserted in the API
serializer (§10.4):

1. A card with no visible `claim_type` chip cannot be rendered — `RenderBlocked` is raised.
2. An `interpreted` row whose parent is not also being rendered in the same viewport is not rendered.
3. A `hypothesis` row is never rendered inside a list whose heading contains "finding", "insight" or "result".
4. No card renders without `sample_size` **and** `effective_sample_size` both visible (§28 "show sample sizes").
5. Export (PDF/CSV/clipboard) carries the chip text and the scope line as literal text, so the distinction
   survives leaving the product.

---

## 3. Hallucination-proof generation pipeline

### 3.1 Stages

```
S0  candidate mining            (code, owned by the intelligence subsystem)  -> StatCandidate
S1  evidence packet assembly    (code, this subsystem)                       -> EvidencePacket  [persisted]
S2  prose drafting              (LLM, tokens only, no digits)                -> FindingDraft
S3  mechanical validation       (code + bounded LLM entailment tier)         -> ValidationReport
S4  bounded repair              (LLM, max 1 attempt)                         -> FindingDraft'
S5  adversarial verification    (independent LLM referee, defaults refuted)  -> Refutation
S6  confidence scoring          (code, deterministic)                        -> ConfidenceResult
S7  banding + gating            (code)                                       -> provisional decision
S7b run-level finalisation      (code, once per run, after ALL candidates)   -> publish | insufficient
S8  render-time re-validation   (code, on every read)                        -> FindingViewModel
```

S7b exists because the multiplicity correction in §6.9 is a property of the *family*, not of any single
candidate: a candidate's adjusted q-value cannot be known until its siblings have been scored. Candidates
therefore rest at `status='validating'` after S7 and are flipped to `published` or
`insufficient_evidence` in one transaction by `finalise_run()`.

Stages S1, S3 (mechanical tier), S6, S7 and S8 are pure deterministic code. The model participates only in
S2, the entailment tier of S3, S4 and S5 — and in all four of those it is writing or judging *text*, never
producing a quantity that reaches the user.

### 3.2 The non-negotiable rule: the LLM must not compute numbers

This is enforced three independent ways, so that defeating it requires three simultaneous failures.

1. **The output schema has no numeric fields.** `FindingDraft` (§3.5) contains only strings and lists of
   string refs. There is literally nowhere for a model-produced number to go. Numbers enter the rendered
   text only through `{{stat:S#}}` token substitution performed by `renderer.interpolate()`, which reads
   `ComputedStat.display_value` out of the packet.
2. **Digits are banned in model output** (validator **V3**). Any `[0-9]` outside a `{{stat:...}}` or
   `[E...]` token, in any field of `FindingDraft`, is an integrity failure. Spelled-out numbers and vague
   quantifiers are banned by the same validator via `QUANTIFIER_LEXICON` unless backed by a stat token in
   the same sentence.
3. **The packet is not countable.** The model receives pre-aggregated `ComputedStat` rows plus terse item
   summaries. Item summaries deliberately omit any field that would let the model derive a new statistic the
   packet does not already contain (no raw engagement integers, no raw prices — those arrive only as
   `display_value` inside a stat). The packet also carries an explicit `items_are_a_sample_of` note so the
   model cannot treat the listed items as the full population.

Stated plainly in the system prompt and repeated in the packet preamble: *you cannot count, you cannot
divide, you cannot compare magnitudes, you cannot round. If a number is needed and no stat token provides
it, you must decline.*

### 3.3 The evidence packet (S1)

Assembled by `PacketBuilder` from a `StatCandidate`. **All ids are real**; the builder reads them from the
database and fails loudly rather than fabricating. The packet is persisted to `evidence_packets` *before*
any model call, and `finding_evidence` rows for contradicting items are written *before* generation too
(§7.3) — so the model cannot cause counter-evidence to disappear.

```python
# aypo/models/findings.py

AssertionDomain = Literal[
    "corpus_composition",   # what the analysed corpus contains
    "content_performance",  # views/likes/comments/shares on content
    "commerce_listing",     # price, discount, variant, listing presence
    "commerce_review",      # review counts, ratings, review text
    "temporal",             # change over time within the corpus
    "cross_corpus",         # social<->commerce joint statistics
]

class ComputedStat(BaseModel):
    model_config = ConfigDict(frozen=True)
    stat_ref: str                       # "S1" .. "S20"; the only handle the LLM gets
    name: str                           # machine name, e.g. "theme_share_brand_a"
    label: str                          # human label for the stat strip, e.g. "Share within Cluster Y"
    value: float
    display_value: str                  # PRE-FORMATTED. The only string ever rendered. e.g. "31%"
    unit: Literal["count","proportion","ratio","index","days","tau","rho","currency"]
    currency_code: str | None           # ISO-4217 from MarketLocalePack when unit='currency'
    n: int                              # numerator / observations behind the figure
    denominator: int | None
    denominator_desc: str               # e.g. "analysed items in Cluster Y"
    n_eff: float | None                 # independence-deflated count behind this figure (§6.2)
    design_effect: float | None         # n / n_eff for the units behind this figure
    ci_low: float | None
    ci_high: float | None
    ci_method: Literal["wilson_deff","cluster_bootstrap","mk_variance_blocked","none"]
    formula: str                        # human-readable, shown on hover. e.g. "n_A / n_frame"
    assertion_domain: AssertionDomain
    subject_refs: tuple[str, ...]       # the vocabulary strings this stat is ABOUT. Code-supplied.
                                        #   V18 binds the prose to this: a sentence carrying {{stat:S#}}
                                        #   may name only these entities. Closes the label-swap hole.
    is_cumulative: bool = False         # platform lifetime counter (sold_count, review_count):
                                        #   never valid in a `trend`, never sufficient for a purchase claim
    selected_from_k: int = 1            # how many comparable groups/slices this value was chosen from (§6.9)
    recompute_key: str                  # registry key for deterministic recomputation (V4)
    recompute_params: dict[str, Any]

class EvidenceItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    ev_ref: str                         # "E1" .. "E120"
    kind: Literal["content","commerce_item","commerce_review","cluster","creator","research_query"]
    role: Literal["supporting","contradicting","non_instance","confounded","illustrative"]
    ref_id: UUID                        # real primary key in the owning table
    platform: str | None
    creator_ref: str | None             # opaque creator key for concentration math (content only)
    independence_unit: str              # THE grouping key for stage-1 capping (§6.2). Never null.
                                        #   content         -> creator_ref, else 'unknown_creator'
                                        #   commerce_item   -> 'seller:' + seller key
                                        #   commerce_review -> 'listing:' + commerce_item_id
                                        #   comment-derived -> 'thread:' + parent content_item_id
                                        #   near-duplicate group members share one value (§6.2a)
    published_at: datetime | None
    sampling_strategy: Literal["brand","category","competitor","theme","hypothesis"]
    discovery_query_id: UUID | None
    collection_provider: str | None
    analysis_confidence: float | None   # per-item confidence for THIS property, from content_analysis
    supports_properties: tuple[str, ...]  # machine-derived property keys this item exhibits
    sponsored_likelihood: float | None
    usage_type: str | None              # DERIVED by code from content_analysis_claims.consumption_state
                                        #   via the fixed mapping in §12.2. Not an upstream field.
    excerpt: str | None                 # verbatim, with offsets, for the support check and the UI
    excerpt_field: str | None           # "transcript" | "caption" | "ocr" | "review_text" | ...
    excerpt_start: int | None
    excerpt_end: int | None

class PopulationFrame(BaseModel):
    description: str                    # "analysed content items in Cluster Y"
    size: int
    filter_spec: dict[str, Any]         # machine-reproducible
    date_from: datetime
    date_to: datetime

class PropertyPredicate(BaseModel):
    key: str                            # "co_occurs(brand=<id>, theme=<label>)"
    description: str
    structured_sql: str | None          # non-null => V6 runs the cheap structured tier
    # Counter-evidence comes in exactly one of two shapes, chosen by finding_kind (§7.1a).
    negation_sql: str | None            # ITEM-LEVEL kinds only: rows asserting the opposite property
    counter_analysis: "CounterAnalysis | None" = None   # FRAME-LEVEL kinds: the competing computation

class CounterAnalysis(BaseModel):
    """For claims whose negation is not a property of any single item (rate_comparison, trend,
    social_commerce_link, competitive_gap), the competing evidence is a *recomputation*, not a row set."""
    kind: Literal["reversed_orientation", "subgroup_split", "complement_frame"]
    partition_by: tuple[str, ...]       # e.g. ("platform",), ("sampling_strategy",)
    recompute_key: str                  # same registry as ComputedStat; code-only
    recompute_params: dict[str, Any]

class PacketDiagnostics(BaseModel):
    n_supporting: int
    n_contradicting: int
    n_confounded: int
    inverse_simpson: dict[str, float]   # {"creator":.., "query":.., "platform":.., "strategy":.., "week":..}
    top_creator_share: float
    top_query_share: float
    span_days: int
    sponsored_share: float
    unknown_confidence_share: float
    unknown_independence_share: float   # share of items whose independence_unit is 'unknown_*' (§6.2)
    near_duplicate_groups_collapsed: int  # §6.2a
    sources_unavailable: tuple[str, ...]  # platforms that failed this run (BRIEF §7 graceful degradation)
    # --- selection / multiplicity bookkeeping (§6.9). Code-supplied by the miner. ---
    candidates_in_family: int           # how many comparable candidates the miner tested in this family
    selected_from_k: int                # how many slices this candidate's headline stat was max/min of
    # --- trend-artefact controls (§6.3a). Populated only for finding_kind='trend'. ---
    bucket_series: tuple[dict[str, Any], ...] = ()   # per bucket: numerator, denominator, query vintages
    query_vintage_tau: float | None = None           # Kendall tau(bucket index, late-vintage share)
    trailing_bucket_excluded: bool = False

class EvidencePacket(BaseModel):
    model_config = ConfigDict(frozen=True)
    packet_id: UUID
    research_run_id: UUID
    candidate_key: str                  # stable dedup key from the miner
    finding_kind: FindingKind
    subject_brand_id: UUID | None
    population_frame: PopulationFrame
    property_predicate: PropertyPredicate
    stats: tuple[ComputedStat, ...]
    items: tuple[EvidenceItem, ...]
    vocabulary: tuple[str, ...]         # the ONLY label strings the LLM may use (§4.3 V9)
    items_are_a_sample_of: int          # |supporting| in the frame, if items were truncated
    diagnostics: PacketDiagnostics
    evidence_design: Literal["observational_snapshot"] = "observational_snapshot"
                                        # read by V12. This system produces exactly one design and it
                                        # never licenses a causal claim; the field exists so V12 is a
                                        # real field read rather than a dangling attribute reference.
    market: str                         # drives MarketLocalePack + currency formatting
    builder_version: str
```

**Vocabulary assembly** (the anti-seeding mechanism). `vocabulary` is built *only* from strings **attested in
collected data** for this run: admitted leader rows in the open-taxonomy `labels` table (`display_form` where
`canonical_id IS NULL`, matched on `normalised_form` — see §12.3a), `clusters.name`, `brands.name`,
`products.name`, and `commerce_items.category`. Nothing is authored. If the generator needs a word for a
concept and that word is not in the data, it cannot say it — it must decline. That is the intended behaviour.

> **`research_queries.query_text` is deliberately NOT a vocabulary source.** An earlier draft included it.
> That was a direct §1/§34 hole: a `hypothesis`- or `theme`-strategy query is a sentence *we* wrote to go
> looking for something, so admitting its words to the vocabulary would let the generator name a concept that
> exists only in our own search box and never in the corpus. Query text still reaches the UI — through
> `v_finding_provenance` and the Research Run view, rendered by code — but it never reaches the generator's
> allowed-word list. The only exception is a label that independently appears in `labels` because the
> analysis subsystem actually observed it.

**Item truncation.** `items` is capped at 120 entries to bound cost: up to 60 `supporting` (stratified
sample across creator, platform, week and sampling strategy so the model does not see a creator-skewed
slice), up to 40 `contradicting`, up to 10 `confounded`, up to 10 `illustrative`. `non_instance` items are
**never** enumerated (they are the denominator, and enumerating them would cost a fortune) — they are
represented only by `population_frame.size` and the stats. Concentration math in §6 runs over the **full**
supporting set from the database, not the truncated sample, so truncation cannot inflate `n_eff`.

### 3.4 Generator system prompt skeleton (S2)

Model `claude-opus-5`, `thinking={"type":"adaptive"}`, `output_config={"effort":"medium"}`,
`client.messages.parse(output_format=FindingDraft)` → `response.parsed_output`. The static block below is
sent first with `cache_control: {"type":"ephemeral"}`; the packet varies per call. (`parse` merges a
caller-supplied `output_config` with its generated schema, so `effort` and structured output compose —
verified in `docs/ENVIRONMENT.md`. `budget_tokens` is rejected on this model; depth is `effort` only.)

**Refusal is not an error.** `parse` is on the non-beta namespace and takes no server-side `fallbacks`
argument, so a `stop_reason == "refusal"` surfaces client-side. For *this* stage it is handled as
`decline=true` with `decline_reason='model_refusal'` and the candidate is closed as `declined` — retrying a
refused finding on a fallback model to get a finding out of it is precisely the behaviour §3.2 rule 9
exists to prevent. (The project-wide fallback retry in `aypo/llm/client.py` is correct for the bulk
analysis pass and wrong here; the findings gateway opts out.) A refusal in S5 is covered by §5.4 rule 1 and
yields `refuted`.

```text
SYSTEM
You are the finding writer for an evidence-first market-research platform. You convert a pre-computed
evidence packet into careful prose. You are not an analyst and not an advisor.

ABSOLUTE RULES
1. You cannot compute. You may not count, sum, divide, average, compare magnitudes, estimate, round, or
   infer any quantity. Every quantity in your output MUST be the token {{stat:S#}} taken verbatim from the
   STATISTICS block. If the quantity you want does not exist as a stat, you may not state it.
2. Digits are forbidden in your output. So are vague quantifiers (most, many, few, majority, half, often,
   usually, dozens, and spelled-out numbers) unless the same sentence also contains a {{stat:S#}} token.
3. Every clause that asserts something about the data MUST carry one or more citations in the form [E#],
   using only ev_refs present in the EVIDENCE block. Never invent an id. Never cite an id you were not given.
4. You may only use entity, theme, label, slang, occasion, behaviour and format strings that appear verbatim
   in the VOCABULARY block, plus the subject brand name given in SUBJECT. You may not introduce any concept,
   audience, motivation, demographic, cultural reference or occasion that is not in VOCABULARY, even if it
   seems obviously true. If the right word is missing, decline.
5. You may not explain why anything happens. No causal language.
6. You may not describe people. No ages, genders, income, class, household composition, or life stage.
7. Scope every claim to the analysed sample. Never write as if the sample were the market.
8. You see CONTRADICTING evidence. You may not ignore it. If it materially undercuts the claim, say so in
   the context, or decline.
9. If the packet does not support a defensible claim, set decline=true with a decline_reason. Declining is a
   correct and expected outcome. Never manufacture a finding to fill the slot.

CLAIM TYPE
Choose the weakest type that the packet supports:
  observed    - rows exist with these attributes. No rates, no comparisons, no change over time.
  calculated  - a rate, ratio, share, lift or trend that exists as a stat, with its denominator named.
  interpreted - a hedged reading of the parent claim. Requires a hedge phrase. Introduces no new number
                and no new entity.
  hypothesis  - a modal or interrogative statement plus how it could be confirmed and refuted.
When in doubt, choose the weaker type.

OUTPUT
claim_template     one or two sentences, tokens and citations only
context_template   two to four sentences: where it appears, how concentrated the sources are, what the
                   contradicting evidence shows, what the claim does not establish
cited_stat_refs    every S# you used
cited_evidence_refs every E# you used
asserted_properties the machine property keys your claim asserts of the cited items
what_would_refute  one sentence
follow_up_queries  only for claim_type=hypothesis, at least two, phrased as search queries

USER
SUBJECT: {subject_brand_name}
FINDING KIND: {finding_kind}
POPULATION FRAME: {frame.description} | size={{stat:SFRAME}} | {frame.date_from}..{frame.date_to}
PROPERTY UNDER TEST: {predicate.description}
STATISTICS:
  S1 | {label} | {display_value} | 95% CI [{ci_low}, {ci_high}] | n={n} of {denominator} {denominator_desc}
     | formula: {formula} | domain: {assertion_domain}
  ...
SOURCE CONCENTRATION (read this before writing):
  effective creators {d_creator} of {n_creators} | effective queries {d_query} of {n_queries}
  | platforms {n_platforms} | largest single creator share {top_creator_share}
  | sampling strategies present: {strategies}
  | temporal span {span_days} days
  | sources unavailable this run: {sources_unavailable}
SUPPORTING EVIDENCE ({items_are_a_sample_of} total; up to 60 shown, stratified):
  E1 | content | {platform} | {published_at:%Y-%m} | creator {creator_ref} | strategy {sampling_strategy}
     | properties {supports_properties} | "{excerpt}"
  ...
CONTRADICTING EVIDENCE (items in the frame that assert the opposite):
  E61 | ... | "{excerpt}"
  ...
CONFOUNDED (satisfy the property but carry a disqualifying attribute, e.g. sponsored or duplicate):
  E101 | ... | reason {confound_reason}
VOCABULARY (the only labels you may use):
  {vocabulary}
```

### 3.5 What the model is allowed to return

```python
class FindingDraft(BaseModel):
    """The ONLY shape the generator may return. Note the complete absence of numeric fields."""
    decline: bool = False
    decline_reason: str | None = None
    claim_type: ClaimType | None = None
    claim_template: str | None = None          # contains {{stat:S#}} and [E#] tokens
    context_template: str | None = None
    cited_stat_refs: list[str] = Field(default_factory=list)
    cited_evidence_refs: list[str] = Field(default_factory=list)
    asserted_properties: list[str] = Field(default_factory=list)
    # ^ CLOSED SET. Every entry must be `packet.property_predicate.key` or one of the structured
    #   property keys enumerated in the packet. V6 verifies `packet.property_predicate.key`
    #   UNCONDITIONALLY, whatever the model declares here — otherwise the model chooses what gets
    #   checked and can defeat V6 by declaring something trivially true (see §4.4).
    what_would_refute: str | None = None
    what_would_confirm: str | None = None
    follow_up_queries: list[str] = Field(default_factory=list)
```

### 3.6 Generator interface

```python
# aypo/intelligence/findings/generator.py

class FindingGenerator:
    def __init__(
        self,
        llm: AnthropicGateway,                 # aypo/llm/gateway.py
        lexicons: Lexicons,
        model: str = "claude-opus-5",
        effort: Literal["low","medium","high","xhigh","max"] = "medium",
    ) -> None: ...

    async def draft(self, packet: EvidencePacket) -> FindingDraft: ...

    async def repair(
        self,
        packet: EvidencePacket,
        draft: FindingDraft,
        failures: Sequence[ValidationFailure],
    ) -> FindingDraft: ...

    async def narrow(
        self,
        packet: EvidencePacket,
        draft: FindingDraft,
        narrower_claim: str,                   # supplied verbatim by the referee
    ) -> FindingDraft: ...
```

---

## 4. Validation (S3) — the exact checks

### 4.1 Types

```python
# aypo/intelligence/findings/validators.py

Severity = Literal["integrity", "grammar", "scope", "power", "duplication"]
Action   = Literal["repair", "drop", "quarantine", "demote",
                   "strip_citation", "force_clause", "recompute", "merge"]

@dataclass(frozen=True)
class ValidationFailure:
    check_code: str                 # "V2"
    severity: Severity
    action: Action
    message: str                    # fed back to the repair prompt verbatim
    offending: tuple[str, ...]      # tokens, ev_refs, or sentence indices
    detail: dict[str, Any]

@dataclass
class ValidationContext:
    session: AsyncSession
    packet: EvidencePacket
    attempt: int
    stat_recomputer: StatRecomputer
    entailment: EntailmentChecker
    settings: FindingSettings

class Validator(Protocol):
    code: str
    severity: Severity
    async def __call__(
        self, draft: FindingDraft, ctx: ValidationContext
    ) -> list[ValidationFailure]: ...

@dataclass
class ValidationReport:
    failures: list[ValidationFailure]
    stripped_citations: list[str]
    forced_clauses: list[str]
    demoted_to: ClaimType | None
    outcome: Literal["pass", "repair", "drop", "quarantine", "merge", "recompute"]

async def run_validators(
    draft: FindingDraft,
    ctx: ValidationContext,
    validators: Sequence[Validator] = ALL_VALIDATORS,
) -> ValidationReport: ...
```

### 4.2 Failure dispositions

| Disposition | Meaning | Where it goes |
|---|---|---|
| `repair` | One bounded retry with the failure messages appended to the prompt. **Max 1 attempt** (`FindingSettings.max_repair_attempts = 1`). Why 1: a second repair on the same packet empirically converges to the model restating the same defect, and each attempt costs a full packet of input tokens. A model that cannot satisfy a mechanical rule in one correction is signalling that the packet does not support the claim. | back to S2 |
| `drop` | The candidate is abandoned. A row is written to `finding_quarantine` with `reason_code` so the run report can count it, but no `findings` row is created. | `finding_quarantine` |
| `quarantine` | Same storage as `drop`, but flagged `integrity=true` and counted toward the run's integrity rate. Reserved for failures that indicate the generator is *fabricating* rather than merely overreaching: V1, V2, V6-contradict, V9, V10. | `finding_quarantine`, run banner |
| `demote` | The claim is reclassified one step weaker (`calculated`→`interpreted`→`hypothesis`) and re-validated under the weaker grammar. Demotion happens at most once per candidate; a second demotion trigger is a `drop`. | re-run S3 |
| `strip_citation` | The specific `[E#]` is removed from the text and its `finding_evidence` row is re-roled. Then V5 (citation density) re-runs; if it now fails, the disposition escalates to `drop`. | in place |
| `force_clause` | The renderer injects a mandatory clause (scope line, sponsored-share disclosure, denominator). No model call. | in place |
| `recompute` | The underlying data changed since packet assembly. The packet is rebuilt and the candidate restarts at S1. Max 1 rebuild per candidate per run. | back to S1 |
| `merge` | A near-duplicate published finding exists; evidence sets are unioned onto the existing row and the new candidate is discarded. | existing `findings` row |

**Integrity-rate circuit breaker.** `quarantine_rate = integrity_quarantines / candidates_processed`.
It is evaluated only once **`candidates_processed >= breaker_min_denominator = 20`** — before that the rate
is pure noise (one integrity failure in the first three candidates is 0.33 and means nothing), and a false
banner that fires on every run teaches the reader to ignore it. From candidate 20 onward the rate is
re-evaluated after every candidate and the breaker trips at `>= 0.15` (inclusive — the prose and Appendix C
previously disagreed; `>=` is normative).

When it trips the run is marked `research_runs.generation_integrity_degraded = true`, the Overview renders a
banner ("finding generation was unreliable in this run; N candidates were rejected for citing evidence that
did not support them"), and the remaining candidates still run but are capped at band `indicative`.

**The cap applies to findings already published in the same run, and it is applied at render time, not by
rewriting rows.** Findings published before the breaker tripped came out of the same degraded process, so
exempting them would publish exactly the biased subset the breaker exists to catch; but `findings` rows are
immutable once published (§10.2), so the band cannot be rewritten. `render_finding` therefore reads the run
flag and emits `display_band = min(stored_band, 'indicative')` with the banner text attached, leaving
`confidence_band` on the row as the audit record of what the pipeline computed. `FindingViewModel` carries
both `confidence_band` (stored) and `display_band` (served), and exports use `display_band`.
*Why 0.15:* at ~60 candidates per run with one repair attempt, 0.15 is ~9 integrity rejections. Below that
level the rejections are ordinary single-sample model noise that the validators exist to absorb. Above it,
the defect is systemic — a malformed packet, a vocabulary gap, or a model/prompt regression — and silently
publishing the survivors would be publishing a biased subset. *At exactly 0.15:* treated as degraded
(inclusive), because the cost of a false banner is a cautious marketer and the cost of a missed banner is an
uncaught regression.

### 4.3 The checks

Run in order. Cheap deterministic checks first so the expensive entailment tier (V6) only sees drafts that
are already structurally sound.

| # | Check | Exact rule | Failure action |
|---|---|---|---|
| **V0** | `decline_handled` | If `draft.decline` is true, the candidate is closed as `declined` with the reason stored. Not a failure. | — |
| **V1** | `unknown_stat_token` | Every `{{stat:X}}` in `claim_template` + `context_template` must have `X ∈ {s.stat_ref for s in packet.stats}`. Every ref in `cited_stat_refs` must appear as a token in the text, and vice versa (bidirectional). | `repair` → `quarantine` |
| **V2** | `unknown_evidence_id` | Every `[E#]` must have `# ∈ {i.ev_ref for i in packet.items}`. Every ref in `cited_evidence_refs` must appear in the text and vice versa. A `[E#]` whose packet role is `non_instance` or `confounded` may not be cited as support. | `repair` → `quarantine` |
| **V3** | `no_model_numerals` | `re.search(r"\d", text_outside_tokens)` must not match, in **every** string field of `FindingDraft`. Additionally, any `QUANTIFIER_LEXICON` token must co-occur in the same sentence with ≥1 `{{stat:...}}` token. Sentence splitting uses a deterministic splitter so the check is reproducible. | `repair` → `drop` |
| **V4** | `stat_value_integrity` | For each cited stat, call `stat_recomputer.recompute(recompute_key, recompute_params)` against the live DB. Integer-valued stats must match exactly. Float-valued stats must match within `rel_tol=1e-9`. A mismatch means the corpus changed mid-run (new items landed), **not** that the model lied — stats are code-produced. | `recompute` (rebuild packet, restart S1); second mismatch → `drop` with `reason_code='corpus_unstable'` |
| **V5** | `citation_density` | (a) Every sentence in `claim_template` that contains an assertive main verb must carry ≥1 `[E#]`, except sentences that are purely definitional or purely a scope statement. (b) `len(set(cited_evidence_refs)) >= min_citations` where `min_citations = min(5, n_supporting)`. *Why 5:* five independently-citable exemplars is the minimum at which a drill-down is persuasive rather than anecdotal, and it matches the `indicative` floor of `n_eff ≥ 5`, so a finding can never be published with fewer exemplars than its own confidence floor requires. When `n_supporting < 5` the finding is already below the `indicative` gate and will be withheld by §6.6 regardless. | `repair` → `drop` |
| **V6** | `item_supports_claim` | Runs against **`packet.property_predicate`**, never against `draft.asserted_properties` (see 4.4). Two tiers, per cited supporting item. **Tier A (structured, free):** if `predicate.structured_sql` is non-null, execute it with the item id; a false result means the item does not exhibit the property. **Tier B (entailment, LLM):** for open-ended discovered properties with no structured predicate, batch 10 items per call and ask a 4-way classification (`supports` / `contradicts` / `unrelated` / `insufficient`) given the predicate description and the item's verbatim excerpt + structured labels. **Bar:** `supports_share >= 0.80` over cited supporting items **and** zero `contradicts`. | See 4.4 below |
| **V7** | `scope_guard` | `POPULATION_LEXICON` tokens must be immediately qualified by one of the scope phrases (`"in the analysed sample"`, `"within this dataset"`, `"among the analysed items"`). The rendered finding must carry `scope_statement`; if absent, the renderer injects it. | lexicon hit → `repair` → `drop`; missing scope line → `force_clause` |
| **V8** | `type_grammar` | Apply the §2.3 lexicon matrix for the declared `claim_type`. | `demote` (once) → `repair` → `drop` |
| **V9** | `vocabulary_guard` | Extract candidate entity/label spans from the text (proper-noun runs, quoted strings, and any span matching an entry in a fuzzy index of `packet.vocabulary`). Every extracted span must match a `vocabulary` entry or `subject_brand_name` after Unicode NFKC normalisation, casefolding and whitespace collapse. A span that matches nothing is an invented label. | `repair` → `quarantine` |
| **V10** | `demographics_guard` | Any `DEMOGRAPHIC_LEXICON` hit is a failure **unless** every cited item carries a non-null `declared_demographic` field sourced from the platform itself (which, in practice, we never have). | `quarantine` — **never repaired**. §28 forbids invented demographics outright; offering a repair would invite a reworded version of the same violation. |
| **V11** | `endorsement_and_usage_guard` | If the claim's predicate hits `ENDORSEMENT_LEXICON` or `PREFERENCE_LEXICON`: (a) ≥80% of cited items must have `usage_type == "active_use"` (not `incidental`, not `unknown`); (b) if `sponsored_share > 0.20` the claim must state the sponsored share, else the renderer forces the clause `"<stat> of the cited items are likely sponsored."`; (c) if `sponsored_share > 0.50` the claim is demoted to `interpreted` regardless. *Why 0.20:* one in five sponsored is the point at which a reader's read of "organic behaviour" is materially wrong. *Why 0.50:* at a sponsored majority the pattern is a media-buy artefact, not consumer behaviour, so it cannot be a `calculated` consumer claim. | (a) fail → `demote` → `drop`; (b) → `force_clause`; (c) → `demote` |
| **V12** | `causality_guard` | Any `CAUSAL_LEXICON` hit requires `packet.evidence_design` to be a design that can carry a causal claim. `evidence_design` is a real field on `EvidencePacket` (§3.3) whose only inhabited value is `"observational_snapshot"`, which never can. In practice this is unconditional: **every causal claim is demoted to `hypothesis`.** The field exists so the rule is a field read rather than a dangling attribute, and so a future interventional design has somewhere to declare itself. | `demote` to `hypothesis` |
| **V13** | `trend_guard` | Any `GROWTH_LEXICON` hit requires **all seven** conditions in §6.3a. In summary: the series must be a **within-bucket rate**, never a raw count; buckets are ISO weeks and the trailing partial bucket is dropped; `span_days >= 28`; ≥2 retained buckets each with `n >= 10`; Mann-Kendall on the rate series with **BH-adjusted** `q < 0.10` (§6.9) **or** a period-over-period CI excluding zero; the query-vintage artefact test must pass; and the series may not rest on a `is_cumulative` stat. *Why 28 days:* four weeks is the shortest window containing at least two full weekly content cycles, below which weekday/weekend and single-campaign effects dominate. *Why n ≥ 10 per bucket:* with fewer than ten items a bucket's proportion has a Wilson half-width above ±0.30, so any "change" is inside the noise. *Why `q < 0.10` rather than `p < 0.05`:* this is an exploratory discovery product and the output is explicitly labelled as a direction to investigate, not a test result; but the alpha is now applied **after** multiplicity correction across the run's trend family, because 60 candidates at an uncorrected 0.10 manufacture ~6 trends out of pure noise. At exactly `q = 0.10` the claim fails (strict inequality). | fail → strip `GROWTH` tokens via `repair`; if the claim is nothing but the trend, `demote` to `hypothesis`; if `span_days < 28` or fewer than 2 retained buckets, set `status='insufficient_evidence'` with `withheld_reason='temporal_coverage'`; if the vintage test fails, `withheld_reason='retrieval_recency_artefact'` |
| **V14** | `assertion_domain_match` | Each cited stat's `assertion_domain` must be permitted for the claim's predicate class, per the matrix in §9.2. Example: an `ENGAGEMENT_LEXICON`/`PREFERENCE_LEXICON` claim backed only by `content_performance` stats is a violation (§28 "avoid equating engagement with consumer preference"); a `PURCHASE_LEXICON` claim backed only by `content_performance` or `commerce_review` stats is a violation (§28 "avoid treating positive comments as sales"). **Additionally, a stat with `is_cumulative = True` can never satisfy a requirement on its own.** Platform `sold_count` and `review_count` are lifetime counters on listings *we retrieved with our own queries*: they are confounded by listing age, by marketplace ranking (which is itself partly sales- and ad-weighted) and by our query set, so a sum of them is a statement about the retrieval, not about demand. A purchase-class claim must additionally cite a non-cumulative `commerce_listing` or `cross_corpus` stat, and the renderer forces the clause "platform-reported cumulative figures for the listings retrieved in this run". Cumulative stats are rejected outright in `finding_kind='trend'` (a cumulative series is monotone by construction). | `demote` → `drop`; cumulative-in-trend → `drop` with `reason_code='cumulative_series'` |
| **V15** | `absence_power_guard` | For `finding_kind == 'absence'` (e.g. "`{subject_brand}` does not appear in Cluster Y") **both** a power condition and a **retrievability** condition must hold. **(a) Power:** `frame_n_eff >= 30`, where `frame_n_eff` is the independence-deflated size of the *population frame* (§6.2b) — not `effective_sample_size`, which is zero for an absence claim by construction — and the one-sided 95% exact-binomial upper bound on the presence rate must fall below the stated comparison rate (which the candidate must supply as a `ComputedStat`; an absence claim with no comparison rate is rejected at S1). *Why 30:* at 30 independent observations with zero occurrences the 95% upper bound is ≈0.095, i.e. "if it happens at all in this frame it happens in under one in ten" — actionable. **(b) Retrievability — the condition sample size cannot buy:** at least one `research_queries` row contributing to the frame must have been *capable of surfacing the absent entity* — a query naming it, or a query flagged `disconfirming = true` for this predicate. Absence in a frame that was never probed for the thing is a fact about our query set, not about the world, and no n fixes that. The probing queries are stored as `finding_evidence` rows with `kind='research_query'`, which is also what satisfies V5 for an absence claim (whose supporting-item count is zero). | (a) fail → `status='insufficient_evidence'`, `withheld_reason='absence_underpowered'`; (b) fail → `withheld_reason='absence_unprobed'` |
| **V16** | `near_duplicate` | Embed `claim_rendered` with `text-embedding-3-large` (`dimensions=1536`) and compare against published findings in the same `research_run_id`. Cosine `>= 0.92` → merge: union the `finding_evidence` sets, recompute confidence on the union, keep the earlier row, link `duplicate_of`. *Why 0.92:* above it the two claims are paraphrases, below it they are usually genuinely different slices of the same theme and both deserve a card. **This number is a prior and is flagged in §13 as needing calibration.** At exactly 0.92: merge (inclusive), because a duplicated card costs more credibility than a merged one costs nuance. | `merge` |
| **V17** | `basis_guard` | Every cited supporting item must resolve to a `content_analysis_claims` row with `claim_status='published'` and `cue_check='pass'`. For `claim_type ∈ {observed, calculated}`, that row's `basis` must be in `{seen, heard, written}`; a `basis='inferred'` analysis claim may support only an `interpreted` finding. *Why:* the analysis subsystem already distinguishes what it saw/heard/read from what it inferred (§12.2); a finding typed `observed` that rests on the model's inference is a category error, and it is the exact confusion BRIEF §3 exists to prevent. *At the boundary:* an `observed` claim with a mix of bases keeps only the non-`inferred` citations and re-runs V5; if density then fails, the claim demotes to `interpreted` rather than dropping, because the inference may well be sound — it just is not an observation. | `strip_citation` → `demote` → `drop` |
| **V18** | `stat_subject_binding` | Every sentence containing a `{{stat:S#}}` token may name **only** entities in that stat's `subject_refs`, plus the frame description's entities and `subject_brand_name`; and it must name at least one of them. *Why this check has to exist:* V9 proves every entity the model wrote is a real string from the data, and V14 proves the stat's domain fits the predicate class — but **nothing before V18 proved the entity in the sentence is the entity the number is about**. A model that writes "`Brand B` appears in `{{stat:S1}}` of items" when `S1` is `Brand A`'s share passes V1, V2, V3, V5, V9 and V14 and publishes a false number attached to the wrong name. `subject_refs` is populated by the stat registry from `recompute_params`, never by the caller and never by the model. | `repair` → `quarantine` (a swapped subject is fabrication-shaped) |
| **V19** | `subgroup_reversal` | For `rate_comparison`, `share_of_presence` and `competitive_gap`, code recomputes the headline statistic split by `platform` and by `sampling_strategy` (the two partitions most likely to carry Simpson reversal in a corpus assembled from several retrievals). If the sign reverses in any partition holding ≥20% of `n_eff`, the finding is marked `contested = TRUE` with `contested_reason='subgroup_reversal'`, the reversal is written to `finding_statistics` and rendered, and the band is capped at `moderate`. *Why mechanical:* §5.2's `smaller_true_effect` vector asks the referee to look for exactly this, but a model eyeballing excerpts cannot reliably detect a reversal that is arithmetic. Compute it. | `force_clause` + band cap |
| **V20** | `selection_disclosure` | If `max(stat.selected_from_k) > 1` for any cited stat, the renderer appends the mandatory clause "`<k>` comparable slices were examined; this is the one that stood out." *Why:* a share reported for the single most extreme of 40 groups is not the same evidence as the same share reported for a pre-specified group, and the reader cannot discount what they are not told. See §6.9. | `force_clause` |

### 4.4 V6 in detail — the check that does the real work

V6 is the check that makes "the cited items actually exhibit the asserted property" true rather than hoped
for. It is also the only place where a model judges evidence, so it is deliberately constrained to a 4-way
classification with the verbatim excerpt in front of it, and it is never allowed to *add* support.

```python
class EntailmentVerdict(BaseModel):
    ev_ref: str
    verdict: Literal["supports", "contradicts", "unrelated", "insufficient"]
    rationale: str                 # <= 200 chars, must quote from the excerpt
    quoted_span: str | None        # must be a literal substring of the excerpt

class EntailmentBatch(BaseModel):
    verdicts: list[EntailmentVerdict]

class EntailmentChecker:
    async def check(
        self,
        asserted_property: str,
        items: Sequence[EvidenceItem],     # batched 10 at a time
        *,
        model: str = "claude-opus-5",
        effort: Literal["low","medium"] = "low",
    ) -> list[EntailmentVerdict]: ...
```

```text
SYSTEM
You decide, for each item, whether the item's own content demonstrates the stated property. You are not
deciding whether the property is plausible, interesting, or likely. Only whether THIS item shows it.

Return exactly one verdict per item:
  supports     - the excerpt or the structured labels directly show the property. You MUST return a
                 quoted_span that is a literal substring of the excerpt.
  contradicts  - the item shows the opposite of the property.
  unrelated    - the item neither shows nor contradicts it.
  insufficient - the excerpt is too short, too garbled, or in a language fragment you cannot read well
                 enough to judge.
Default to "insufficient" when unsure. Do not reason about what the item probably contains beyond the
excerpt. Do not use world knowledge about brands, categories or culture.

USER
ASSERTED PROPERTY: {asserted_property}
ITEMS:
  E12 | labels: {structured_labels} | field: {excerpt_field} | excerpt: "{excerpt}"
  ...
```

**What V6 checks is not negotiable by the model.** The `asserted_property` handed to Tier A and Tier B is
`packet.property_predicate.description`, built by code at S1. `draft.asserted_properties` is validated as a
subset of the packet's closed property set and is used only to *add* further checks, never to replace the
packet predicate. The earlier design let the model name the property that V6 then verified, which meant the
model chose its own exam: declaring a trivially-true property ("item is in Cluster Y") while writing a much
stronger claim would have passed V6 with every citation intact. A declared property outside the closed set
is a V6 failure in its own right, disposition `quarantine`.

Post-processing (code, not the model):

- A `supports` verdict whose `quoted_span` is **not** a literal substring of the excerpt is downgraded to
  `insufficient`. This catches the entailment checker hallucinating its own evidence.
- `supports_share = supports / (supports + contradicts + unrelated + insufficient)` over **cited supporting**
  items only.
- **Bar: `supports_share >= 0.80` and `contradicts == 0`.** *Why 0.80:* with `min_citations = 5`, 0.80
  tolerates exactly one bad exemplar in five, which `strip_citation` can repair cleanly; a second bad
  exemplar means the model's mental model of the property differs from the data's, which is not a citation
  bug but a claim bug. *At exactly 0.80:* keep the finding, strip the non-`supports` citations, re-run V5;
  if V5 then fails for density, escalate to `drop`.
- `contradicts >= 1` among cited *supporting* items → **`quarantine`**, not drop. The generator cited an item
  that says the opposite; that is fabrication-shaped and must be counted toward the integrity rate.
- `supports_share < 0.80` → `drop` the candidate (not merely the citations).
- Every verdict is persisted on `finding_evidence.support_check` so the drill-down can show *why* each
  exemplar is there, and so the quoted span can be highlighted in the UI.

Cost control: Tier B only runs for properties with no `structured_sql`, batches 10 items, uses
`effort="low"`, and caches the instruction block. See §8.

---

## 5. Adversarial verification (S5)

### 5.1 Why it is a separate, independent pass

The generator has an incentive problem: it was asked to write a finding, so it will write one. The referee
is asked to kill it, sees the same evidence, and has **no access to the generator's reasoning, prompt, or
thinking trace**. It is a fresh `client.messages.parse(output_format=Refutation)` call with its own system
prompt and its own conversation, run at `effort="high"` because the task is genuinely analytic. (It must be
`parse`, not `create`: `Refutation` is a structured return and the kill rule depends on getting exactly five
typed judgements back — see rule 1 in §5.4, where a parse failure is itself a kill.) It receives the *rendered* claim (numbers already
substituted), the full stat list with formulas and CIs, the concentration diagnostics, the cited supporting
items, **the contradicting items**, the confounded items, and the sampling provenance.

### 5.2 The five attack vectors

```python
RefutationVector = Literal[
    "confound",                 # a third factor explains the pattern (platform recommender,
                                #   sponsorship, a single campaign, a duplicated/reposted asset,
                                #   a creator's content habit, a seasonal artefact)
    "smaller_true_effect",      # the direction may hold but the magnitude is overstated:
                                #   CI overlap, denominator choice, base-rate neglect, Simpson reversal
    "alternative_explanation",  # a different claim fits the same evidence at least as well
    "insufficient_sample",      # n_eff, diversity, or temporal coverage cannot carry the claim
    "sampling_artefact",        # the pattern is a property of how we searched, not of the world:
                                #   the query set, the sampling strategy mix, a platform outage
]

class VectorJudgement(BaseModel):
    vector: RefutationVector
    verdict: Literal["not_applicable", "weak", "serious", "fatal"]
    rationale: str                       # must cite S# and/or E# from what it was shown
    cited_stat_refs: list[str]
    cited_evidence_refs: list[str]

class Refutation(BaseModel):
    judgements: list[VectorJudgement]    # exactly 5, one per vector
    overall: Literal["survives", "survives_narrowed", "refuted"]
                                         # ADVISORY ONLY. Logged to finding_refutations for calibration
                                         # analysis; `apply_kill_rule` never reads it. The outcome is a
                                         # pure function of the five per-vector verdicts. If this field
                                         # disagrees with the computed outcome that disagreement is a
                                         # calibration signal, not a decision.
    verdict_confidence: float            # 0..1. Used ONLY as a kill floor (rule 1): a low value refutes,
                                         # a high value never promotes. A systematically overconfident
                                         # referee therefore buys no leniency, only the loss of this guard.
    narrower_claim: str | None           # a claim the referee believes the evidence DOES carry
    unseen_evidence_needed: str | None   # what would settle it
```

Verdict semantics, given to the referee verbatim so the scale is not improvised:

- `not_applicable` — this attack does not apply to this claim shape at all.
- `weak` — the attack is conceivable but the evidence already addresses it. **Persisted and rendered as a
  caveat on the published finding**, so the user sees the doubt even when the finding survives.
- `serious` — the attack would change what a marketer should do about this finding.
- `fatal` — the claim as written is not supportable at all.

### 5.3 Referee prompt skeleton

```text
SYSTEM
You are an adversarial reviewer. Your job is to try to destroy a research finding using only the evidence
that was used to produce it. You are the last line of defence before this finding is shown to a marketing
team that will spend money on it.

For each of the five attack vectors, judge: not_applicable, weak, serious, fatal.
  confound                - a third factor explains the pattern: a single campaign or sponsorship wave, one
                            platform's recommender, a reposted or duplicated asset counted more than once,
                            one creator's content habit, a seasonal or calendar artefact.
  smaller_true_effect     - the direction may hold but the stated magnitude is overstated. Check CI overlap,
                            whether the denominator was chosen favourably, whether the base rate was
                            ignored, and whether a subgroup split would reverse the sign.
  alternative_explanation - a different claim fits this same evidence at least as well.
  insufficient_sample     - the effective sample size, source diversity, or time span cannot carry a claim
                            of this strength. Look at EFFECTIVE creators and queries, not raw counts.
  sampling_artefact       - the pattern describes how the data was searched rather than anything about the
                            world. Look at the query set, the sampling-strategy mix, the per-bucket query
                            vintages, and SELECTION: you are told how many comparable candidates were
                            tested in this run and how many slices this statistic was the extreme of. A
                            statistic selected as the largest of many is weaker than the same statistic
                            specified in advance, and that is a legitimate basis for `serious`.

RULES
- Use only the evidence shown. Do not use world knowledge about brands, categories, markets or culture to
  either attack or defend. An attack grounded in "brands like this usually..." is not admissible.
- Every rationale must cite the S# or E# it relies on.
- You are NOT required to find a flaw. "not_applicable" and "weak" are correct answers when the evidence is
  genuinely sound. Do not manufacture objections.
- If you cannot tell, say so by lowering verdict_confidence. Do not guess.
- If you judge any vector serious or fatal, and you believe a narrower claim IS carried by this evidence,
  write that narrower claim in narrower_claim, using the same statistics and the same vocabulary. Otherwise
  leave it null.

USER
CLAIM ({claim_type}): {claim_rendered}
CONTEXT: {context_rendered}
SCOPE: {scope_statement}
STATISTICS: (label | value | 95% CI | n of denominator | formula | domain)
  ...
SELECTION CONTEXT:
  candidates tested in this run {candidates_in_family} | this statistic was the extreme of
  {selected_from_k} comparable slices | multiplicity-adjusted q {adjusted_q}
SAMPLE STRUCTURE:
  raw n {n} | effective n {n_eff} | effective creators {d_creator} of {n_creators}
  | effective queries {d_query} of {n_queries} | platforms {platforms}
  | largest single creator share {top_creator_share} | sampling strategies {strategy_mix}
  | temporal span {span_days} days across {n_weeks} weeks | week concentration {d_week}
  | sponsored share {sponsored_share} | items with unknown analysis confidence {unknown_confidence_share}
  | sources unavailable this run {sources_unavailable}
CITED SUPPORTING EVIDENCE:
  E1 | {platform} | {published_at:%Y-%m-%d} | creator {creator_ref} | query {query_text}
     | strategy {sampling_strategy} | sponsored_likelihood {sponsored_likelihood}
     | usage {usage_type} | "{excerpt}"
  ...
CONTRADICTING EVIDENCE IN THE SAME FRAME:
  E61 | ... | "{excerpt}"
CONFOUNDED ITEMS (excluded from the statistics, listed so you can judge the confound vector):
  E101 | ... | reason {confound_reason}
```

### 5.4 Kill / keep rule

Deterministic, in `apply_kill_rule`. No model judgement enters the decision beyond the per-vector verdicts.

```python
@dataclass(frozen=True)
class RefereeDecision:
    outcome: Literal["survives", "narrow_and_retry", "refuted"]
    s_adv: float                       # the confidence multiplier (§6.5)
    caveats: tuple[VectorJudgement, ...]   # the 'weak' judgements, rendered on the card
    reason_code: str
    narrower_claim: str | None

def apply_kill_rule(
    refutations: Sequence[Refutation],
    *,
    min_verdict_confidence: float = 0.50,
) -> RefereeDecision: ...
```

Rules, evaluated in order:

1. **Non-answer is a kill.** If the call errored after its retries, the response was truncated, the parse
   failed, `len(judgements) != 5`, or `verdict_confidence < 0.50` → `refuted`, `reason_code="indeterminate"`.
   The finding is stored with `status='refuted'` and `adversarial_verdict='indeterminate'` and is never
   published. *Why default to refuted:* the asymmetry is the whole point. A finding we failed to verify is
   indistinguishable, from the user's seat, from a finding we verified and were wrong about. *Why 0.50:*
   below even coin-flip self-confidence the referee's verdict carries no information, so the pre-referee
   state (unverified) stands, and unverified means unpublished.
2. Any vector `fatal` → `refuted`.
3. Two or more vectors `serious` → `refuted`. *Why 2:* a single serious objection is frequently a scope
   problem that narrowing fixes. Two independent serious objections mean the claim is wrong in more than one
   way, and narrowing to satisfy both reliably produces a claim too small to be worth showing.
4. Exactly one vector `serious`, `narrower_claim` present and non-empty → `narrow_and_retry`. The generator
   is called with `narrow()`, the narrowed draft re-runs **all** validators (§4) and then a **fresh referee
   pass** (new call, no memory of the first). That second `Refutation` is fed back through
   `apply_kill_rule` — *not* read off its `overall` field — and anything other than a computed `survives`
   makes the finding `refuted`. Narrowing happens at most once.
5. Exactly one vector `serious`, no workable `narrower_claim` → `refuted`.
6. All vectors `weak` or `not_applicable` → `survives`. Every `weak` judgement is persisted to
   `finding_refutations` and **rendered on the card** under "Reviewer caveats".

**Two-referee rule for the top band.** A finding whose provisional confidence (computed with `s_adv = 1.0`)
would land in band `strong` gets a **second independent referee pass**, with a different stratified sample
of the contradicting items and a different item ordering. The harsher of the two verdicts governs; a
disagreement on any vector resolves to the harsher verdict. *Why:* `strong` is the band a marketer will
quote in a deck without reading the drill-down, so it should be the only band that has survived two
independent attempts to kill it. Cost is bounded because `strong` candidates are a minority (§8).

**Referee calibration monitor.** Track `referee_kill_rate` per run. If it exceeds **0.60**, the run is
flagged `referee_miscalibrated` for human review and the prompt is treated as suspect — but the kills still
stand. The system never auto-loosens the referee, because a product that relaxes its own verification when
verification gets inconvenient has no verification. Symmetrically, a kill rate under **0.05** is flagged as
*suspiciously permissive* and sampled for human audit.

---

## 6. The confidence model

### 6.1 Design stance

Three commitments shape the formula:

1. **No single opaque score as the headline.** BRIEF §33 bans arbitrary AI scores. The UI headline is a
   *band word* plus `n_eff / n`; the numeric confidence and its full decomposition are available on
   drill-down. The number exists so the system can gate and rank deterministically, not so a marketer can
   treat 0.71 as meaningfully different from 0.68.
2. **Strengths are substitutable; independence is not.** Terms that trade off against one another are
   combined **additively** with weights. Terms that are *necessary conditions* (independence of sources,
   absence of overwhelming counter-evidence, surviving the referee) are applied **multiplicatively**, so
   that no amount of volume can buy its way past them.
3. **Concentration is penalised twice, on purpose.** Once inside `n_eff` (which deflates the sample) and
   again in `S_div` (which scores the diversity directly). The brief's own example — forty items from three
   creators found by one query — must not merely score lower; it must be structurally incapable of reaching
   the top band.

### 6.2 Effective sample size — the concentration penalty

Raw `n` is never used for gating. The deflation is a two-stage cap, applied over the **full** supporting
item set from the database (not the truncated packet sample).

```python
# aypo/intelligence/findings/confidence.py

def inverse_simpson(counts: Sequence[int]) -> float:
    """Effective number of groups = 1 / sum((n_i/n)^2). Equals k for k equal groups, 1.0
    when everything is in one group. Returns 0.0 for an empty input."""
    total = sum(counts)
    if total == 0:
        return 0.0
    return 1.0 / sum((c / total) ** 2 for c in counts)

@dataclass(frozen=True)
class EffectiveSample:
    n: int
    n_eff: float
    n_unit_capped: float
    n_query_capped: float
    inv_simpson: dict[str, float]     # independence_unit, query, platform, strategy, week
                                      #   (the "creator" key is retained as an alias of
                                      #    independence_unit for content-only frames, for UI copy)
    independence_unit_kind: Literal["creator","seller","listing","thread","mixed"]
    unknown_independence_share: float
    top_unit_share: float
    top_query_share: float

def effective_sample_size(
    items: Sequence[EvidenceItem],
    *,
    unit_cap: int = 3,          # was creator_cap; the grouping key is independence_unit
    query_cap: int = 8,
) -> EffectiveSample: ...
```

**Stage 0 — the independence unit.** Stage 1 groups by `EvidenceItem.independence_unit`, not by
`creator_ref`, and that field is **never null**. Three things made the original `creator_ref`-only design
unsafe, and all three are failures in the *permissive* direction, which is the direction that matters:

| Situation | Wrong behaviour the old spec permitted | Normative rule |
|---|---|---|
| `creator_ref` is null (no `creators` table exists yet in any sibling design — §12.3) | each null item forms its own group, so unknown-creator items read as maximally independent and **inflate both `n_eff` and `inv_simpson(creator)`** — defeating the central protection exactly where provenance is weakest | all items with no resolvable creator share the single unit `'unknown_creator'`, which is then capped like any other unit. `PacketDiagnostics.unknown_independence_share` is surfaced, and `> 0.30` caps the band at `indicative` |
| commerce evidence (`commerce_item`, `commerce_review`) has no creator at all | either every row is its own group (40 reviews on one listing = 40 observations) or they all collapse to one null group (no commerce finding can ever clear a creator gate) — the old spec did not say which, and both are wrong | `commerce_item -> 'seller:<seller key>'`, `commerce_review -> 'listing:<commerce_item_id>'`. The diversity gates read `inv_simpson(independence_unit)`, not `inv_simpson(creator)`, so a commerce finding is gated on effective **sellers/listings** |
| comment-derived evidence | fifty comments under one video are not fifty observations | `'thread:<parent content_item_id>'`, so a thread contributes at most `unit_cap` whatever its length. This replaces open question §13.5's "collapse to one", which was too harsh for a large genuine thread |

The gate thresholds in §6.6 are stated against `inv_simpson(independence_unit)` and are unchanged in value;
"effective creators" in the UI copy means "effective independent sources" and the card names which kind.

**Stage 0a — near-duplicate collapse.** A reposted or re-uploaded asset is correlated *by construction* and
is the most common silent inflation of `n`. Until a sibling design owns perceptual-hash repost detection
(§13.12), this subsystem performs its own conservative interim collapse at S1 using the embeddings it
already has (§12.4): within the supporting set of a single candidate, items whose content embeddings have
cosine `>= 0.97` **and** whose published dates are within 30 days form one group and share a single
`independence_unit`. 0.97 is deliberately high — the aim is to catch re-uploads, not similar content — and
`near_duplicate_groups_collapsed` is reported in the run report so the rate is visible. This is an interim
mitigation with known recall limits, not a solution; it is strictly better than the previous behaviour,
which was to count every repost as an independent observation.

**Stage 1 — unit capping.** Group supporting items by `independence_unit`. A unit contributes
`min(count_i, unit_cap)` with `unit_cap = 3`.

> *Why 3.* One creator's second and third posts do carry new information: different framings, different
> uploads, different dates, often a different audience slice. The fourth through twelfth do not — the
> creator's style, audience and intent are fixed, so marginal information about the *population* is close to
> zero. Capping at 3 keeps genuine creator-led phenomena visible (a creator-driven format still registers as
> a real observation) while making twelve items from one account worth three, not twelve. Capping at 1 would
> erase creator-led phenomena, which are real and are exactly the kind of thing this product should find.
> *At the boundary:* a creator with exactly 3 items contributes 3; the 4th contributes 0, and this is shown
> in the UI as "3 of 12 items from this creator counted".

**Stage 2 — query capping.** Distribute the creator-capped weights across `discovery_query_id`, then cap
each query's total contribution at `query_cap = 8`.

> *Why 8.* A single search query returns a ranked, algorithmically correlated slice: the platform's model of
> that query, not a sample of the world. Its results share phrasing, recency bias and recommender lineage. 8
> is the point at which, in the yield data we expect to collect, one query stops contributing new creators —
> but **this is a prior, not a measurement** (see §13). It is deliberately looser than the creator cap
> because a well-chosen query legitimately surfaces many genuinely different creators, whereas one creator
> is always one voice. *At the boundary:* the 9th creator-capped unit from a single query contributes 0.

```
n_eff = min( n_query_capped, n )
```

**Worked example (the brief's own counterexample).** 40 items, 3 creators (18/14/8), 1 query, 1 platform.
Stage 1: `min(18,3) + min(14,3) + min(8,3) = 9`. Stage 2: a single query, `min(9, 8) = 8`.
**`n_eff = 8` out of `n = 40`.** `inv_simpson(creator) = 1/((18/40)² + (14/40)² + (8/40)²) = 1/0.3650 = 2.74`;
`inv_simpson(query) = 1.0`; `inv_simpson(platform) = 1.0`.

So `S_size = ln(9)/ln(31) = 0.640`, and with `d_creator = 2.74/8 = 0.342`, `d_query = 1/4 = 0.25`,
`d_platform = 1/2 = 0.5`, `S_div = 0.5^0.25 × 0.342^0.45 × 0.25^0.30 = 0.841 × 0.617 × 0.660 = 0.343`.

Now walk the gates. `strong` needs `n_eff ≥ 20` — fails at 8. `moderate` needs `n_eff ≥ 10` — fails at 8.
`indicative` needs `n_eff ≥ 5` (passes) **and** `inv_simpson(independence_unit) ≥ 3` — which for this
all-content frame is `inv_simpson(creator)` — fails at 2.74. The finding
therefore cascades all the way to **`insufficient`** and is never shown as a finding at all, regardless of
how large its effect size is. It appears only in the Insufficient-data drawer, which reports: *"40 items, but only 8
effective observations and 2.74 effective creators against the 3 required, with a single query behind every
item. Would need roughly 1 more independent creator to be shown as an early signal."* That is exactly the
outcome the brief's counterexample demands.

### 6.2b Frame-level effective size, and the design effect

`n_eff` above is defined over *supporting* items. Three things need the same quantity for the **population
frame**, and the original design had no name for it:

```python
@dataclass(frozen=True)
class FrameEffective:
    frame_n: int
    frame_n_eff: float          # same two-stage capping over every item in the frame
    design_effect: float        # frame_n / frame_n_eff, floored at 1.0
```

- **V15** (`absence`) gates on `frame_n_eff`, because an absence claim has zero supporting items and
  therefore `effective_sample_size == 0` by construction. Gating it on `n_eff` was incoherent, and combined
  with `ck_published_has_sample` it made absence findings structurally unpublishable (§10.1 now fixes both).
- **Every proportion CI** is computed with the design effect, not as if items were independent (§6.3).
- **`S_counter`** uses it to keep both sides of `ρ` on the same footing.

### 6.3 The six additive sub-scores

All in `[0, 1]`.

**(1) `S_size` — sample adequacy.**
```
S_size = clip( ln(1 + n_eff) / ln(1 + n_target), 0, 1 )      n_target = 30
```
> *Why `n_target = 30`.* With 30 independent observations, a sample proportion near 0.5 has a 95% Wilson
> half-width of about ±0.18 — enough to say "this appears more often than that in the analysed sample", and
> not enough to say anything stronger, which is exactly the strength of claim this product is allowed to
> make. Attainability, stated honestly: `n_eff = 30` means **thirty effective observations, not thirty
> items**. At the design effects we expect (the §6.2 worked examples run at 1.5–2×, and far worse when a
> theme is creator-led) a theme needs roughly 45–90 raw items, i.e. **3–6% of a 1,500-video corpus**, not
> the 2% an earlier draft claimed by silently equating `n` with `n_eff`. Against the corpus the current
> credentials actually permit (`docs/ENVIRONMENT.md`: free-tier Apify, no YouTube key) almost nothing will
> clear it, and the honest consequence is a full Insufficient-data drawer, not a lower `n_target`.
> *Why logarithmic.* The 5th independent observation changes our belief enormously; the 35th barely at all.
> Linear scaling would reward brute volume, which is the behaviour we are trying to prevent.
> Values: `n_eff=1 → 0.20`, `3 → 0.40`, `5 → 0.52`, `10 → 0.70`, `20 → 0.89`, `30 → 1.00`, `>30 → 1.00`.

**(2) `S_effect` — effect size**, metric chosen by `finding_kind`:

| `finding_kind` | Metric | Normalisation | Why that scale point |
|---|---|---|---|
| `rate_comparison`, `share_of_presence`, `competitive_gap` | Cohen's *h* on arcsine-transformed proportions: `h = 2·asin(√p₁) − 2·asin(√p₂)` | `S_effect = min(1, \|h\| / 0.8)` | 0.8 is the conventional "large effect" for *h*. Full credit should require a difference a marketer would notice without a chart. Arcsine rather than raw difference because proportions near 0 or 1 have compressed variance, and most discovered themes are rare. |
| `association` | Lift `L = P(A∧B) / (P(A)·P(B))` | `S_effect = min(1, \|log₂ L\| / 2)` | Full credit at 4× (or ¼×) lift. Log base 2 so a doubling is a unit; 4× is the point at which a co-occurrence is not plausibly incidental. |
| `trend` | Mann-Kendall τ | `S_effect = min(1, \|τ\| / 0.5)` | τ = 0.5 is a strongly monotone series. Rank-based because item counts per bucket are small, noisy and non-normal. |
| `social_commerce_link` | Spearman ρ between the social series and the commerce series across time buckets | `S_effect = min(1, \|ρ\| / 0.6)` | Rank correlation, not Pearson, because both series are small-n and heavy-tailed. 0.6 is the point at which the two series genuinely co-move rather than share a trend. **Correlation here never licenses causal language — V12 enforces that.** |
| `existence`, `absence` | none | `S_effect = 0.5`, `effect_metric = 'none'`, and the finding is **hard-capped at band `moderate`** | An "X exists in the sample" claim has no effect size to measure. Assigning 0.5 is neutral; the cap prevents a pure existence claim reaching `strong` on volume alone. |

**Every interval in this subsystem is computed against the dependence structure, not against raw `n`.**
This is a correction to an earlier draft that mandated a plain Wilson interval on proportions while the rest
of the design existed precisely because items are *not* independent. A card that says "31% [0.26, 0.36]" and
"49 effective observations from 97 items" in the same breath is internally contradictory, and the narrower
number is the one the reader believes.

| Statistic | Interval | Rule |
|---|---|---|
| proportion / share | `ci_method='wilson_deff'` | Wilson evaluated at `n_eff` (equivalently, Wilson at `n` with the variance inflated by `design_effect` from §6.2b). The DDL CHECK on `finding_statistics` requires `ci_method = 'wilson_deff'` for `unit='proportion'`; plain `'wilson'` is no longer an accepted value |
| Cohen's *h*, lift | `ci_method='cluster_bootstrap'` | BCa bootstrap **resampling independence units, not items** (1,000 resamples) — but only when the number of independence units is `>= 10`. BCa's acceleration estimate is unstable below that and silently produces intervals that are too narrow, which is the worst possible failure for this product |
| same, with `< 10` units | `ci_method='cluster_bootstrap'`, widened | fall back to a cluster-robust *t* interval on `units − 1` degrees of freedom, which is conservative at small cluster counts, and set `caps_applied += ['few_independence_units']`, capping the band at `moderate` |
| Mann-Kendall τ | `ci_method='mk_variance_blocked'` | variance computed with ties correction and blocked by independence unit, so one prolific unit's run of items cannot manufacture monotonicity |
| any statistic with `< 5` independence units | — | no interval is published; the finding is withheld as `insufficient` regardless of its point estimate |

**(3) `S_div` — source diversity.** A geometric mean, so a zero on any dimension craters the term. This is
the necessary-condition structure applied *within* an additive term.

```
d_unit     = min(1, inv_simpson(independence_unit) / 8.0)   # creators / sellers / listings / threads
d_query    = min(1, inv_simpson(query)             / 4.0)
d_platform = min(1, inv_simpson(platform)          / 2.0)

S_div = d_platform**0.25 * d_unit**0.45 * d_query**0.30
```
> *Why 8 effective sources (`d_unit`, written `d_creator` elsewhere for content-only frames).* Below roughly eight independent voices you cannot separate "a behaviour present
> in the category" from "a handful of accounts' content habit". Eight is also small enough to be reachable
> inside a ~1,500-video corpus for a genuine theme.
> *Why 4 effective queries.* With fewer than four differently-phrased queries behind a pattern, the pattern
> is substantially a description of the retrieval. Four is the smallest number that can contain a
> brand-level, a category-level and a theme-level query plus one more.
> *Why only 2 platforms.* This is a deliberate accommodation of BRIEF §7: with `APIFY_TOKEN` and
> `YOUTUBE_API_KEY` missing, a run may have only web-search discovery plus one marketplace. Demanding four
> platforms would make every finding permanently `insufficient`, which is a worse failure than a
> platform-concentrated finding that is clearly labelled as such. Single-platform findings are therefore
> *capped* (they cannot reach `strong`) rather than killed, and the card states which platforms were
> unavailable.
> *Why those exponents.* They sum to 1.0 and rank the three threats to independence: creator dependence is
> the dominant one (0.45) because one account's output is one observation however often it posts; retrieval
> bias is next (0.30); platform concentration is last (0.25) because it is partly outside our control given
> missing credentials.

**(4) `S_time` — temporal coverage.**
```
S_time = 0.5 * min(1, span_days / 56.0)
       + 0.5 * min(1, inv_simpson(week) / 4.0)
```
> *Why 56 days.* Eight weeks is the shortest span that contains two full monthly content cycles and can
> therefore distinguish "a pattern" from "a fortnight". It is also realistic for a corpus collected in a
> single run from recency-biased platforms.
> *Why the second half.* Span alone is gameable: two items eight weeks apart and thirty-eight in one week
> would score 1.0 on span. The week-concentration term requires the observations to be *spread*, with full
> credit at four effective weeks.
> Trend claims additionally face the **hard gate** in V13 (28 days, ≥2 buckets with n ≥ 10, p < 0.10);
> `S_time` is the graded term, V13 is the pass/fail.

#### 6.3a Why a `trend` in this corpus is guilty until proven innocent

This is the single most dangerous claim shape the product can emit, and the original V13 did not defend
against its main failure mode. The corpus is a **single-snapshot retrieval**, assembled during one run, from
platforms that rank by recency and engagement, using queries that were themselves generated *during* the
run. Bucketing those items by publication date and running Mann-Kendall over the bucket counts measures at
least four things before it measures anything about the world:

1. **Retrieval recency bias.** Recommenders and search rank recent content higher, so a fixed query returns
   monotonically more items the closer you get to the collection date. Every theme "grows".
2. **Query vintage.** Follow-up `theme`- and `hypothesis`-strategy queries are issued *later* in the run,
   after the theme was discovered, and they return the recent slice the platform is serving that week. A
   theme discovered on day 2 and queried on day 3 acquires a late-bucket bulge that is pure process.
3. **Survivorship.** Deleted, privated and removed content is missing from the old end of the series only.
   Attrition alone produces an upward slope.
4. **Accumulation.** Engagement counts, `sold_count` and `review_count` are lifetime-to-date, so older items
   have had longer to accrue. Any series built on them is monotone by construction.

V13 therefore requires **all seven** of the following, and every one is computed by code:

| # | Condition | Why |
|---|---|---|
| T1 | the series is a **within-bucket rate** `numerator_b / denominator_b`, where the denominator is the corpus's own item count in that bucket **within the same frame**. Raw-count series are rejected outright | corpus volume by publication date is dominated by (1) and (3); putting it in the denominator cancels it to first order |
| T2 | buckets are ISO weeks; the **trailing partial week is dropped** | a half-finished week always reads as a collapse or a spike |
| T3 | `span_days >= 28` and ≥2 retained buckets with `n >= 10` | as before |
| T4 | **query-vintage test:** `\|kendall_tau(bucket_index, share_of_bucket_found_by_queries_issued_in_the_run's_later_half)\| < 0.50`, and every retained bucket contains items from ≥2 distinct query vintages | directly tests (2). A rate whose late buckets are populated mostly by late queries is describing our own search schedule |
| T5 | no cited stat has `is_cumulative = True` | (4) |
| T6 | Mann-Kendall on the **rate** series, blocked by independence unit, with **BH-adjusted `q < 0.10`** across the run's trend family (§6.9) | an uncorrected per-claim alpha over ~60 candidates manufactures trends |
| T7 | the direction survives dropping the single highest-weight independence unit (leave-one-unit-out) | one prolific creator ramping up their own posting is not a category trend |

Failing T1, T2, T3 or T5 is a construction error → `drop`. Failing T4 →
`withheld_reason='retrieval_recency_artefact'`. Failing T6 or T7 → the growth language is stripped and the
direction may only be restated as a `hypothesis`.

**What this costs.** Most candidate trends in an MVP corpus will fail T3 or T4 and never publish. That is
the correct outcome: BRIEF §22 says "only make growth or decline claims when temporal coverage is
sufficient", and a single-snapshot run collected over days genuinely cannot see a trend over months. The
drawer entry says so in those words.

**(5) `S_strategy` — sampling-strategy diversity.** The anti-self-fulfilment term.
```
S_strategy = min(1, inv_simpson(sampling_strategy) / 2.5)
```
> *Why 2.5.* BRIEF §8 defines five deliberately different sampling strategies (brand, category, competitor,
> theme, hypothesis-test). A pattern that only ever appears under one strategy is suspect in a specific way:
> if it only appears under `brand`, the corpus was deliberately brand-skewed there; if it only appears under
> `hypothesis`, we arguably went looking for it and found what we went looking for. Requiring ~2–3
> effective strategies for full credit means a finding must survive being approached from more than one
> direction. 2.5 rather than 3 because two genuinely different strategies (say `category` and `theme`) is
> already a meaningful cross-check, and demanding three would penalise findings in a run where competitor
> discovery has not yet fired.
> **Additional hard cap:** if the `hypothesis` strategy accounts for >70% of the effective weight, the band is capped
> at `moderate` and the card states "this pattern was found mostly by queries written to look for it". This
> is the §1/§34 compliance term expressed numerically.

**(6) `S_item` — per-item analysis confidence.**
```
S_item = Σ_i (w_i · c_i) / Σ_i w_i
```
where `w_i` is the item's creator/query-capped weight and `c_i` is `content_analysis.confidence` for the
specific asserted property (not a global per-item confidence). Null `c_i` is imputed as 0.5 and counted into
`unknown_confidence_share`.
> *Why only 0.10 weight.* Per-item analysis quality already filters through V6: an item whose analysis was
> wrong usually fails the entailment check and never gets cited. Weighting it heavily would double-count.
> **Cap:** `unknown_confidence_share > 0.40` → band capped at `moderate`, because at that point the finding
> rests substantially on items whose analysis quality is unknown.

### 6.4 Competing-evidence multiplier

```
ρ = n_contra_eff / (n_eff + n_contra_eff)          # both sides concentration-deflated identically
S_counter = 1 - min(1, ρ / 0.5)
```

> **`ρ` is direction-dependent, so the claim's direction must be fixed by code, not by phrasing.** Negating
> a claim swaps the supporting and contradicting sets, which maps `ρ → 1 − ρ`. Phrased one way a candidate
> is `ρ = 0.13` and publishes; phrased the other way it is `ρ = 0.87`, auto-reframed and scored zero. Left
> to the miner's choice of wording, the entire competing-evidence apparatus would be label-dependent —
> exactly the kind of measure that must be invariant and was not.
> **Normative rule:** `PacketBuilder` imposes a **canonical orientation** at S1 — the predicate is oriented
> so that `|supporting| >= |contradicting|` by raw count, and the orientation actually used is persisted on
> `findings.claim_orientation` and shown in the drill-down. `ρ <= 0.5` then holds by construction, the
> `0.15` / `0.40` thresholds mean one thing, and a candidate arriving in the minority orientation is
> flipped rather than penalised. A tie (`|supporting| == |contradicting|`) skips orientation and goes
> straight to the §7.4 co-existence template.
> *Why 0.5 as the zero point.* At equal effective weight on both sides, the directional claim is not
> supported at all — the honest finding is the *co-existence* of both patterns, which is what §7 rewrites it
> into. Linear between 0 and 0.5 so the penalty is felt early: `ρ=0.10 → 0.80`, `ρ=0.15 → 0.70`,
> `ρ=0.25 → 0.50`, `ρ≥0.50 → 0.00`.
> `n_contra_eff` uses the **same** capping function as `n_eff`, so a single hostile creator posting fifteen
> contradicting videos cannot kill a well-sourced finding either.

### 6.5 Adversarial multiplier

```
S_adv = base_adv * Π over weak caveats (0.95), floored at 0.80
  where base_adv = 1.00  if outcome == "survives"
                   0.85  if outcome == "survives_narrowed"
```
> *Why 0.85 for a narrowed claim.* A claim that had to be narrowed demonstrably overreached on its first
> attempt; the narrowed version is real but the generator's calibration on this packet is suspect.
> *Why 0.95 per weak caveat, floored at 0.80.* Weak caveats are doubts the referee could not substantiate.
> Each should shade confidence slightly. The 0.80 floor prevents five pro-forma caveats from silently
> demoting a sound finding — at that point the right response is a human looking at the referee prompt, which
> the §5.4 calibration monitor triggers.

### 6.6 Combination, bands and gates

```
base       = 0.30*S_size + 0.22*S_div + 0.16*S_effect
           + 0.12*S_time + 0.10*S_strategy + 0.10*S_item          # weights sum to 1.00
confidence = base * S_counter * S_adv * G_type

G_type: observed 1.00 | calculated 1.00 | interpreted 0.90 | hypothesis -> no score at all
```

Weight justification, term by term:

| Term | w | Why this weight |
|---|---|---|
| `S_size` | 0.30 | The most common reason a social-listening claim is wrong is too few independent observations. It earns the largest weight — and because it is already concentration-deflated, the weight cannot be bought with volume. |
| `S_div` | 0.22 | The term that distinguishes a real pattern from a creator- or query-artefact. Second largest deliberately; combined with its effect inside `n_eff`, concentration is the most heavily penalised property in the model. |
| `S_effect` | 0.16 | A real-but-small effect is still real and still worth reporting with its CI. It should cost confidence, not dominate it — and over-weighting it would bias the product toward dramatic claims. |
| `S_time` | 0.12 | Decisive for trend claims, largely irrelevant for existence claims. Handled as a modest graded weight plus a hard gate (V13) where it actually matters, rather than a large weight everywhere. |
| `S_strategy` | 0.10 | Small, because for many legitimate findings two strategies is all that is available. Its real force is the >70% hypothesis-test cap, not the weight. |
| `S_item` | 0.10 | Partly redundant with V6; a modest weight avoids double-counting analysis quality. |

`G_type` for `interpreted` is 0.90 because an interpretation inherits all of its parent's evidential
strength and then adds a layer of model judgement on top, which can only subtract reliability. Hypotheses
get no score: a hypothesis with a confidence number attached will be read as a weak finding, which is
precisely the confusion §3 of the brief exists to prevent.

**Bands.**

| Band | Numeric range | Additional hard gates (ALL must pass) | UI treatment |
|---|---|---|---|
| `strong` | `>= 0.70` | `n_eff >= 20`; `inv_simpson(independence_unit) >= 8`; `inv_simpson(query) >= 4`; `inv_simpson(platform) >= 2`; `adversarial_verdict == 'survives'`; two independent referee passes; `ρ < 0.15`; `finding_kind not in ('existence','absence')`; `unknown_confidence_share <= 0.40`; `unknown_independence_share <= 0.30`; `hypothesis`-strategy weight share `<= 0.70`; `adjusted_q < 0.10` where a test applies; ≥1 supporting item from a `disconfirming` query | Eligible for the Overview hero area. Band word + `n_eff/n` + CI always visible. |
| `moderate` | `0.50 – 0.6999` | `n_eff >= 10`; `inv_simpson(independence_unit) >= 5`; `inv_simpson(query) >= 2`; `adjusted_q < 0.10` where a test applies | Normal finding card. |
| `indicative` | `0.35 – 0.4999` | `n_eff >= 5`; `inv_simpson(independence_unit) >= 3`; `adjusted_q < 0.10` where a test applies | Rendered with an `EARLY SIGNAL` chip and the line "Not a conclusion. Based on few independent sources." **Excluded from the Overview hero area** and from any summary export. |
| `insufficient` | `< 0.35` **or any gate above failed at every band** | — | **Not shown as a finding.** Appears only in the *Insufficient data* drawer (§6.7). |

A finding that clears the numeric range for a band but fails that band's gates **falls to the next band
down** and is re-tested against its gates, cascading to `insufficient`. So a 0.78-confidence finding sourced
from four effective creators is published as `moderate`, not `strong`, and the card says why.

> *Why 0.35 is the withhold line.* Work out what 0.35 buys: roughly `n_eff ≈ 5` with mediocre diversity and
> a modest effect — i.e. "five loosely independent items pointing somewhere". Published as a finding, that is
> exactly the failure §28 names ("avoid hallucinating absent evidence", "identify insufficient data").
> Withheld entirely, it would violate §28's "identify insufficient data" in the other direction — the
> marketer should know we looked and came up short. Hence the drawer.
> *At exactly 0.35:* resolves **upward** to `indicative`, provided the `indicative` gates pass. Boundary
> resolution is upward here and downward at the `strong`/`moderate` boundary (`0.70` exactly → `strong` only
> if its gates pass, which is the binding constraint anyway).

### 6.7 The Insufficient-data drawer

An `insufficient` finding is stored, linked to its run, fully inspectable, and **never quoted as a
conclusion**. The drawer states the shortfall mechanically:

```python
required_to_promote = {
    "n_eff_needed":        max(0, 5 - n_eff),
    "sources_needed":      max(0, 3 - inv_simpson_independence_unit),   # creators/sellers/listings
    "source_kind":         independence_unit_kind,
    "queries_needed":      max(0, 2 - inv_simpson_query),
    "days_needed":         max(0, 28 - span_days),
    "blocked_by":          ["temporal_coverage", "source_concentration",
                            "retrieval_recency_artefact", "absence_unprobed",
                            "multiplicity_adjusted_q", "provenance_unknown", ...],
    "sources_unavailable": [...],   # BRIEF §7: say when a missing credential caused this
    "corpus_size":         frame_n, # so "we withheld everything" reads as "the corpus is small",
                                    # which is the true cause on a credential-limited run (§8)
}
```

Rendered as: *"Possible pattern around `Theme X`, withheld. Based on 4 effective observations from 2
effective creators across 11 days. Would need roughly 1 more independent observation and 1 more independent
creator to be shown as an early signal. `Platform P2` was unavailable during this run."*

That last clause is the §7 graceful-degradation contract surfacing in the UI: a finding withheld because a
source was down must say so, otherwise the absence reads as a finding about the world.

### 6.8 Interface

```python
@dataclass(frozen=True)
class ConfidenceInput:
    claim_type: ClaimType
    finding_kind: FindingKind
    supporting: Sequence[EvidenceItem]       # FULL set from DB, not the packet sample
    contradicting: Sequence[EvidenceItem]
    effect_metric: str
    effect_value: float | None
    effect_ci: tuple[float, float] | None
    span_days: int
    trend_p_value: float | None
    sponsored_share: float
    unknown_confidence_share: float
    referee: RefereeDecision
    sources_unavailable: Sequence[str]

@dataclass(frozen=True)
class ConfidenceResult:
    confidence: float | None                 # None for hypothesis
    band: ConfidenceBand
    components: dict[str, float]             # all six sub-scores + S_counter + S_adv + G_type
    effective: EffectiveSample
    contradiction_ratio: float
    gates_failed: tuple[str, ...]
    caps_applied: tuple[str, ...]
    withheld_reason: str | None
    required_to_promote: dict[str, float]

def score_confidence(inp: ConfidenceInput) -> ConfidenceResult: ...
```

`score_confidence` is pure, synchronous and deterministic. It is unit-tested against a fixture table of
hand-computed cases (all generic: `Brand A` / `Theme X` / `Cluster Y`), including the brief's
40-items/3-creators/1-query counterexample, every band boundary, and every gate.

---

### 6.9 Multiplicity — the correction the original design was missing entirely

A run mines ~60 candidates. Each is a slice of the same corpus chosen because it looked interesting, and
many carry a significance test or a CI-excludes-zero condition. Applying a per-claim `alpha = 0.10` sixty
times produces roughly **six** findings that clear the bar on noise alone, and the adversarial referee
cannot catch them: it is shown one finding at a time and explicitly instructed to use only the evidence in
front of it, so it has no way to know that fifty-nine siblings were also tested. Nothing in the original
design addressed this. Volume of hypotheses is the oldest way to manufacture a confident wrong answer, and
a product whose whole premise is "mine many candidate patterns" is maximally exposed to it.

**Two mechanisms, both code.**

**(a) Benjamini–Hochberg across the run, as a run-level gate.** Candidates whose publication depends on a
test — `trend` (Mann-Kendall), `rate_comparison`, `competitive_gap`, `social_commerce_link` — form a
**family** keyed by `(research_run_id, finding_kind)`. BH is applied within each family at `q = 0.10`.
`findings` stores `raw_p`, `adjusted_q` and `n_tests_in_family`, and both are rendered on the card next to
the band. V13's threshold is `adjusted_q < 0.10`, not `raw_p < 0.10`.

This makes publication a **two-phase commit**, which is a real change to Appendix A's flow: a candidate
cannot be published the moment it clears S7, because its q-value depends on the other members of its
family. The pipeline therefore scores every candidate to a provisional decision, persists it at
`status='validating'`, and runs a single run-level `finalise_run()` that computes BH per family, applies
the gate, and flips survivors to `published` in one transaction. Per-candidate checkpointing (§12.5) is
unaffected; the advisory lock already serialises the finalisation.

**(b) Selection disclosure for the untested kinds.** `existence`, `association`, `share_of_presence` and
`absence` carry no p-value, but they are still *selected*: the miner picked the most striking of many
slices. For those the miner must report `ComputedStat.selected_from_k` and
`PacketDiagnostics.candidates_in_family`; V20 forces the clause "`<k>` comparable slices were examined; this
is the one that stood out", and the referee is shown both numbers (§5.3) so `sampling_artefact` and
`smaller_true_effect` can act on them. Disclosure is weaker than correction. It is what is available for a
selection process with no well-defined null, and saying nothing was not an option.

**What this does not fix.** BH assumes the tests within a family are not strongly positively dependent;
candidates mined from overlapping frames violate that, so BH here is an improvement over nothing rather
than a guarantee. The honest framing on the card is "direction to investigate, adjusted for the number of
things we looked at", and that is the wording the renderer uses.

### 6.10 The hero area must never be silently empty

§12.6 restricts the Overview hero to `strong`. Work out what `strong` costs: `confidence >= 0.70` after
multiplication by `S_counter` and `S_adv`. The §5.4 referee at `effort="high"` returns `weak` judgements
readily — the worked example in Appendix B, a textbook-quality finding, collects three, giving
`S_adv = 0.857`. At that multiplier, `strong` needs `base × S_counter >= 0.817`, i.e. a near-perfect base
*and* essentially zero contradicting evidence. On a realistic MVP corpus the expected number of `strong`
findings per run is **close to zero**, which means an empty hero.

An empty hero is not a neutral outcome. A marketer reading a blank "most important findings" panel
concludes that nothing is happening in their category — an unlabelled claim about the world produced by a
threshold. That is the same failure §6.7 exists to prevent for a single withheld finding.

**Normative rule.** `GET /runs/{id}/findings?hero=true` returns, in order of preference: all `strong`
findings; else the top-ranked `moderate` findings, each carrying `hero_fallback = true`; and in both cases
a `hero_note` the UI must render — *"No finding in this run met the strong bar. Showing the strongest
moderate findings. N findings were withheld for insufficient evidence."* If there are no publishable
findings at all, the hero renders the run's collection summary and the withheld count, never blank space.
The two-referee rule and the `strong` gates are unchanged; what changes is that their consequence is stated
rather than rendered as absence.

## 7. Competing evidence and contested findings (BRIEF §28 "allow conflicting evidence")

### 7.1 Counter-evidence is produced by code, before the model runs

Every `StatCandidate` must arrive with a **population frame** (the denominator set) and a **property
predicate** with an explicit **negation**. From those, `PacketBuilder` derives four disjoint roles without
any model involvement:

| Role | Definition | Enumerated? | Counted in |
|---|---|---|---|
| `supporting` | in frame, satisfies `property_predicate` | yes (up to 60 in the packet; all in the DB) | `n_eff` |
| `contradicting` | in frame, satisfies `negation_sql` — asserts the opposite, not merely the absence | yes (up to 40) | `n_contra_eff`, `ρ` |
| `non_instance` | in frame, property simply absent | **no** — represented by `population_frame.size` and the stats | denominators only |
| `confounded` | satisfies the property but carries a disqualifying attribute: sponsored above threshold, near-duplicate/repost of another cited item, engagement anomaly, analysis confidence below 0.3 | yes (up to 10) | excluded from `n_eff`, shown to the referee |

#### 7.1a Counter-evidence has two shapes, because negation does

An item-level `negation_sql` only makes sense when the claim is a property of items. For
`existence`, `association` and `share_of_presence` it is exactly right: "six items show Theme X with the
opposite brand association" is a competing observation. For `rate_comparison`, `trend`,
`social_commerce_link` and `competitive_gap` **the claim is a property of the frame, not of any item**, and
there is no row that "asserts the opposite" of "Brand A appears in 31% of the frame versus 11%". Demanding
`negation_sql` for those kinds produced a set of items that is *related to* the claim but is not its
negation — and then fed that set into `ρ`, so `S_counter` penalised the claim for evidence that was not
counter-evidence at all. (Appendix B is the live example: its fourteen "contradicting" items are
co-occurrences, not reversals, and they cost the finding a full band.)

**Normative rule, by kind:**

| `finding_kind` | Counter-evidence is… | Feeds |
|---|---|---|
| `existence`, `association`, `share_of_presence` | `negation_sql` rows — items in the frame asserting the opposite property | `ρ`, the *Evidence against* column |
| `rate_comparison`, `competitive_gap` | `CounterAnalysis(kind='reversed_orientation' \| 'subgroup_split')` — the same statistic recomputed in the complementary orientation and within each partition (V19) | `contested`, the two-column block renders the partitions, **not** `ρ` |
| `trend` | `CounterAnalysis(kind='subgroup_split')` over `platform` and query vintage (§6.3a T4/T7) | `contested`, V13 |
| `social_commerce_link` | `CounterAnalysis(kind='complement_frame')` — the same correlation on the complement of the social frame | `contested` |

For a frame-level kind, `ρ` is defined as `0` and `S_counter = 1`; the competing-evidence pressure arrives
through V19's reversal check and the band cap instead. A candidate of an item-level kind arriving without
`negation_sql`, or of a frame-level kind arriving without `counter_analysis`, is rejected at S1 as a
contract bug — the rule "every candidate must carry its own negation" is unchanged, only its shape is now
correct per kind.

The distinction between `contradicting` and `non_instance` matters. "Thirty items in Cluster Y do not show
Theme X" is a denominator fact and belongs in a rate. "Six items in Cluster Y show Theme X with the opposite
brand association" is a *competing observation* and must appear on the card next to the supporting items,
with the same drill-down affordance.

### 7.2 Suppression is structurally impossible

1. `finding_evidence` rows with `role='contradicting'` are **written before** the generator is called
   (`PacketBuilder.persist_counter_evidence()` runs in S1). The model has no write path to the database, so
   it cannot remove them.
2. `ρ` is computed by code in S6 from those rows.
3. The renderer (§10.4) raises `RenderBlocked` if `findings.contradiction_ratio > 0` and the view model has
   no `competing_evidence` block. A finding cannot be served with its counter-evidence hidden.
4. The referee sees the contradicting items whether or not the generator mentioned them.

### 7.3 The contested flag

```
contested = TRUE  when any of:
  (a) ρ >= 0.15
  (b) the referee returned 'serious' on `alternative_explanation` and the finding was narrowed
  (c) another published finding in the same run is linked `contradicts` (see finding_links)
```

> *Why ρ ≥ 0.15.* Roughly one effective observation in seven pointing the other way is enough that a
> marketer planning spend must see it before acting. Below 0.15 the contradicting count is still shown as a
> number on the card (always), but the claim is not re-framed. 0.15 also lines up with the `strong` band
> gate, so no finding can be both `strong` and `contested`. *At exactly 0.15:* contested (inclusive).

Consequences of `contested = TRUE`:

- The card renders a two-column *Evidence for / Evidence against* block, both columns drillable to the
  actual videos and listings.
- Band is capped at `moderate`.
- The renderer forces the prefix `Mixed:` onto `claim_rendered` if the claim is directional and does not
  already contain a hedge.
- The Overview hero area excludes contested findings; they appear in the body with the `CONTESTED` chip.

### 7.4 Automatic re-framing at ρ ≥ 0.40

At `ρ >= 0.40` the directional claim is not the honest claim. Code (not the model) converts the finding to
`finding_kind='association'` with a **co-existence** template:

```
"Within the analysed sample, {{stat:S1}} of items in {frame} show {property}, while {{stat:S2}} show
 {negation}. Both patterns are present; the sample does not separate them. [E#...] [E#...]"
```

The original directional claim is retained as a `hypothesis` child with `what_would_refute` populated and
follow-up queries generated by the research subsystem. *Why 0.40 and not 0.50:* by the time the two sides
are equal, a directional claim has already been shown as directional for a while; 0.40 catches it before the
split is a coin flip, and leaves the 0.40–0.50 band (where `S_counter` is still non-zero) as the zone where
a co-existence claim can itself be published with reduced confidence. *At exactly 0.40:* re-frame.

### 7.5 Cross-finding contradiction

`finding_links` with `relation='contradicts'` is written by code when two published findings in the same run
have (a) cosine similarity of their claim embeddings in `[0.70, 0.92)` — i.e. about the same subject but not
duplicates — **and** (b) opposite signs on comparable effect metrics over overlapping population frames.
Both findings get `contested = TRUE` with `contested_reason = 'cross_finding_post_publication'`, and each
card renders a "This finding is contradicted by: …" link.

> **This is the one path where contesting happens after publication, and it collides with two other rules
> unless handled explicitly.** The earlier design asserted that "no finding can be both `strong` and
> contested" because `ρ >= 0.15` fails the `strong` gate — true for ρ-driven and referee-driven contesting,
> false here, because the contradicting finding may not exist until later in the run. Writing
> `contested = TRUE` onto a published `strong` row would then violate `ck_contested_capped`, and lowering
> its band to satisfy that CHECK would violate the immutability trigger. Deadlock.
> **Resolution:** `ck_contested_capped` exempts `contested_reason = 'cross_finding_post_publication'`; the
> stored `confidence_band` is left untouched as the audit record of what the pipeline computed; and
> `render_finding` caps `display_band` at `moderate` with
> `display_band_reason = 'contradicted_by_later_finding'`. The reader sees the demotion, the audit trail
> sees what was computed and why it changed, and nothing is rewritten.
Neither is deleted. The research subsystem receives both as a hypothesis-generation input, because a genuine
contradiction between two slices of the same corpus is one of the most valuable things this product can
hand a researcher.

---

## 8. Cost at scale

Assumptions for one research run: ~1,500 analysed videos + ~2,500 commerce records; ~60 mined candidates;
~20–30 published findings. Anthropic pricing `claude-opus-5` at **$5 / Mtok input, $25 / Mtok output**
(verified against the current model table, and corroborated by the measured per-video figure in
`docs/ENVIRONMENT.md`); cached input **reads** at **$0.50 / Mtok** (~10% of input).
`thinking={"type":"adaptive"}` means thinking tokens bill as output, so output budgets below include them.

Three caveats on the table that follows, none of which change the order of magnitude but all of which make
it a floor rather than a point estimate:

- **Cache *writes* are omitted** and bill at ~1.25× input ($6.25 / Mtok). With a 5-minute ephemeral TTL and
  60 serialised candidates the static generator block is rewritten several times per run; budget ~$0.20.
- **The referee runs at `effort="high"` with adaptive thinking**, where 4k output per call is optimistic.
  `output_config.effort` is the knob that moves this line, and the only honest way to size it is to measure
  `output_tokens_details.thinking_tokens` on the first real run. Treat the S5 rows as a lower bound.
- **The ~8 second-referee calls assume ~8 strong-band candidates.** §6.10 shows the realistic number is
  closer to zero, so this line will usually cost less than shown — and the fact that it does is itself the
  signal that the hero area is empty.

**Corpus-scale reality check.** This table prices the *findings* pipeline against the §6 target corpus.
`docs/ENVIRONMENT.md` records that `YOUTUBE_API_KEY` is absent and the Apify plan is free-tier ($5/month),
so a full-scale 1,500-video / 2,500-record corpus **cannot be collected today**. A small-corpus run costs
less here (fewer candidates clear S0) and publishes far less, because the thresholds in §6 are absolute,
not relative to corpus size. That is the designed behaviour. §6.7, §6.10 and §11.5 are what make the
resulting emptiness legible instead of mute.

| Stage | Calls per run | In / out per call | Input $ | Output $ | Subtotal |
|---|---|---|---|---|---|
| S2 generation (`effort=medium`) | 60 | 10k in (≈6k cached static) / 1.2k out | 60 × (4k×$5 + 6k×$0.50)/1M = $1.38 | 72k × $25/1M = $1.80 | **$3.18** |
| S3 V6 tier B entailment (`effort=low`, 10 items/call, ~50% of candidates need it) | ~120 | 2.5k in (1.2k cached) / 0.4k out | 120 × (1.3k×$5 + 1.2k×$0.50)/1M = $0.85 | 48k × $25/1M = $1.20 | **$2.05** |
| S4 repair (~25% of candidates) | 15 | 11k in / 1.2k out | $0.38 | $0.45 | **$0.83** |
| S5 adversarial, first pass (`effort=high`) | 55 | 12k in (3k cached) / 4k out incl. thinking | 55 × (9k×$5 + 3k×$0.50)/1M = $2.56 | 220k × $25/1M = $5.50 | **$8.06** |
| S5 second referee (strong-band candidates only, ~8) | 8 | 12k in / 4k out | $0.37 | $0.80 | **$1.17** |
| S5 narrow + re-verify (~10) | 10 gen + 10 referee | as above | $0.70 | $1.25 | **$1.95** |
| V16 claim embeddings (`text-embedding-3-large`, $0.13/Mtok) | 60 | ~100 tok each | — | — | **< $0.01** |
| **Findings pipeline total** | | | | | **≈ $17–19 per run** |

Sensitivity: the dominant line is adversarial verification at ~55% of the total. Reducing it to
`effort="medium"` saves roughly $3/run and is **not recommended** — the referee is the credibility layer and
it is already cheap relative to per-item multimodal analysis of 1,500 videos, which is the run's real cost
centre. If budget pressure appears, cut candidate count (mine fewer, better candidates), not verification
depth.

Headroom note: a run that mines 150 candidates instead of 60 costs ~$45. The pipeline is linear in candidate
count, so `FindingBudget.max_candidates` is the single knob.

**Ask the Data, per question:**

| Component | Tokens | $ |
|---|---|---|
| Question audit (`effort=low`) | 1.2k in / 0.3k out | $0.014 |
| Agent loop: ~5 tool turns, system+tools ~3.5k cached, growing results context | ≈45k in total (≈18k uncached, ≈27k cache reads) / 3.5k out | $0.09 + $0.014 + $0.088 = $0.19 |
| Answer validation (code only) | — | $0.00 |
| Embedding for `search_content` queries (~3 calls) | ~60 tok | < $0.001 |
| **Per question** | | **≈ $0.20** |

A ten-question analyst session is ~$2. This is cheap enough that the insufficiency rule (§11.5) — which
makes the agent run `coverage_report` before answering, adding a turn — is affordable and should not be
optimised away.

---

## 9. BRIEF §28 guardrails as mechanical checks

### 9.1 The checklist, each mapped to where it is enforced

| # | §28 requirement | Mechanical check | Enforced in | Failure action | Visible in UI as |
|---|---|---|---|---|---|
| 1 | show sample sizes | `findings.sample_size` and `effective_sample_size` are `NOT NULL`; the view model refuses to build without both; every `ComputedStat` carries `n` and `denominator` | DDL CHECK + `renderer.render_finding` + `api/serializers/findings.py` | `RenderBlocked` | `n_eff / n` on every card; `n of denominator` on every stat |
| 2 | distinguish sample findings from market-wide facts | V7 `scope_guard` + mandatory `scope_statement` injected by the renderer; `POPULATION_LEXICON` banned unqualified | `validators.V7`, `renderer` | `repair`→`drop`; missing line → `force_clause` | the scope line under every claim; `Within the analysed sample` prefix in exports |
| 3 | avoid invented demographics | V10 `demographics_guard`; `DEMOGRAPHIC_LEXICON` is an absolute ban absent a platform-declared field we do not have | `validators.V10` | `quarantine`, **never repaired** | — (such findings never exist) |
| 4 | avoid treating visibility as endorsement | V11(a): endorsement/preference predicates require ≥80% of cited items at `usage_type='active_use'` | `validators.V11` | `demote`→`drop` | the `usage_type` badge on every evidence thumbnail |
| 5 | distinguish incidental appearance from active usage | `usage_type ∈ {active_use, incidental, mentioned_only, unknown}` is a required field on `finding_evidence`; findings about *usage* may not cite `incidental` or `unknown` items; findings about *visibility* may, and the renderer forces the word "appears" rather than "is used" | `PacketBuilder`, `validators.V11`, `renderer` | `strip_citation`→`drop` | per-item badge + a usage breakdown bar on the card |
| 6 | distinguish sponsored and organic | `sponsored_share` computed per finding from `sponsored_likelihood >= 0.5`; V11(b) forces disclosure above 0.20; V11(c) demotes above 0.50 | `confidence.py`, `validators.V11`, `renderer` | `force_clause` / `demote` | sponsored-share pill on the card; per-item sponsored badge; a filter toggle |
| 7 | avoid equating engagement with consumer preference | V14 `assertion_domain_match`: a `PREFERENCE_LEXICON` predicate may not be backed solely by `content_performance` stats | `validators.V14` + the §9.2 matrix | `demote`→`drop` | the stat strip labels each stat's domain |
| 8 | avoid treating positive comments as sales | V14: a `PURCHASE_LEXICON` predicate requires ≥1 stat of domain `commerce_listing` or `cross_corpus`; `commerce_review` and `content_performance` alone are insufficient | `validators.V14` | `demote`→`drop` | same |
| 9 | show uncertainty | every proportion stat must carry a **design-effect-corrected** Wilson CI (`ci_method = 'wilson_deff'` enforced by DDL CHECK for `unit='proportion'`; plain `'wilson'` is rejected because it would understate the interval on clustered data — §6.3); band word + decomposition on drill-down; referee `weak` caveats rendered; `adjusted_q` and `n_tests_in_family` rendered where a test applies (§6.9) | `PacketBuilder`, DDL CHECK, `renderer` | `RenderBlocked` | CI on every proportion; "Reviewer caveats" block; confidence decomposition panel; "k comparable slices examined" |
| 10 | preserve provenance | `finding_evidence` carries `discovery_query_id`, `sampling_strategy`, `collection_provider`, `platform`, `published_at`; `v_finding_provenance` joins through to `research_queries` and `research_runs` | DDL + §10.3 view | FK violation | the drill-down's "How this item was found" row; the Research Run view (§32) |
| 11 | allow conflicting evidence | counter-evidence rows written pre-generation (§7.2); `RenderBlocked` if `contradiction_ratio > 0` and no competing-evidence block | `PacketBuilder`, `renderer` | `RenderBlocked` | *Evidence against* column; `CONTESTED` chip |
| 12 | identify insufficient data | band `insufficient` + `withheld_reason` + `required_to_promote`; `sources_unavailable` surfaced | `confidence.score_confidence` | n/a — this *is* the action | the *Insufficient data* drawer (§6.7) |
| 13 | avoid hallucinating absent evidence | V1, V2, V3, V6, V9 + the token-only numeric path (§3.2) | `validators` | `quarantine` | — (such findings never exist); the run report shows the quarantine count |
| — | *(implied by §3)* distinguish observation from model inference | V17 `basis_guard`: `observed`/`calculated` findings may cite only `basis ∈ {seen, heard, written}` analysis claims; `inferred` analysis may support only an `interpreted` finding | `validators.V17` + `ck_supporting_content_has_claim` | `strip_citation`→`demote`→`drop` | the `basis` badge on every evidence item; the `INTERPRETATION` chip |
| — | *(implied by §1/§34)* do not seed findings | V9 `vocabulary_guard` restricts every label to strings **attested in collected data** (query text explicitly excluded, §3.3); `S_strategy` penalises patterns found only by one sampling strategy; the >70% `hypothesis`-strategy cap; Ask-the-Data label resolution **and** the `search_content` similarity floor (§11.6) | `validators.V9`, `confidence.py`, `ask/tools.py` | `quarantine` / band cap | "found mostly by queries written to look for it" note on the card |
| — | *(implied by §22, §24)* a claim must not be an artefact of how we looked | §6.3a trend controls T1–T7; §6.9 BH correction + selection disclosure (V20); V19 subgroup-reversal; V15(b) absence retrievability probe | `validators.V13/V15/V19/V20`, `confidence.py`, `finalise_run` | `insufficient_evidence` / `force_clause` / band cap | `withheld_reason='retrieval_recency_artefact'`; "k comparable slices examined"; the reversal panel |
| — | *(implied by §28.9)* the interval must match the dependence | `ci_method='wilson_deff'` / `'cluster_bootstrap'` resampling independence units; no interval published below 5 independence units; `few_independence_units` cap below 10 | `PacketBuilder`, `confidence.py`, DDL CHECK | `RenderBlocked` | the CI on every stat, with `n_eff` beside `n` |

### 9.2 Assertion-domain matrix (V14)

Each `ComputedStat` declares an `assertion_domain`. Each claim predicate is classified into a *predicate
class* by lexicon match. The matrix says which domains may back which class. A claim whose cited stats do
not include at least one stat from an allowed domain fails V14.

| Predicate class (lexicon) | `corpus_composition` | `content_performance` | `commerce_listing` | `commerce_review` | `temporal` | `cross_corpus` |
|---|---|---|---|---|---|---|
| **presence / visibility** (appears, visible, shown, co-occurs) | ✅ required | ➖ allowed as support | ➖ | ➖ | ➖ | ➖ |
| **usage / behaviour** (used, eaten, prepared, carried, paired) | ✅ required | ➖ | ➖ | ➖ | ➖ | ➖ |
| **content performance** (viewed, shared, engaged, viral) | ➖ | ✅ required | ➖ | ➖ | ➖ | ➖ |
| **preference / endorsement** (`PREFERENCE_LEXICON`) | ✅ required | ❌ **insufficient alone** | ➖ | ➖ | ➖ | ➖ |
| **purchase / sales** (`PURCHASE_LEXICON`) | ❌ insufficient alone | ❌ insufficient alone | ✅ required | ➖ | ➖ | ✅ satisfies |
| **price / assortment** | ➖ | ➖ | ✅ required | ➖ | ➖ | ➖ |
| **review sentiment** | ➖ | ➖ | ➖ | ✅ required | ➖ | ➖ |
| **change over time** (`GROWTH_LEXICON`) | ➖ | ➖ | ➖ | ➖ | ✅ required + V13 gate | ➖ |
| **social↔commerce relationship** | ➖ | ➖ | ➖ | ➖ | ➖ | ✅ required |

✅ = at least one cited stat must have this domain. ❌ = this domain alone cannot back the class.
➖ = permitted as additional support, cannot satisfy the requirement.

**Orthogonal to the domain, a stat with `is_cumulative = True` never satisfies a ✅ on its own.** Platform
`sold_count` and `review_count` are lifetime counters attached to listings our own queries retrieved, so a
sum over them is confounded by listing age, marketplace ranking and query selection simultaneously. They
remain useful as *support* and as a `commerce_listing` exemplar; they cannot be the thing that makes a
purchase claim admissible, and they are rejected outright inside `finding_kind='trend'` (§4.3 V14).

This single table is what makes §28 items 7 and 8 mechanical rather than aspirational: "engagement is not
preference" becomes "a `preference` predicate with only `content_performance` stats fails V14", and
"positive comments are not sales" becomes "a `purchase` predicate with only `commerce_review` stats fails
V14".

---

## 10. Schema

### 10.1 Enums and the `findings` table

```sql
-- supabase/migrations/0007_findings.sql
create extension if not exists vector;
create extension if not exists pgcrypto;

create type claim_type         as enum ('observed','calculated','interpreted','hypothesis');
create type finding_kind       as enum ('existence','rate_comparison','association','share_of_presence',
                                        'trend','absence','social_commerce_link','competitive_gap');
create type confidence_band    as enum ('strong','moderate','indicative','insufficient');
create type finding_status     as enum ('draft','validating','refuted','quarantined','published',
                                        'insufficient_evidence','superseded','merged','declined');
create type evidence_role      as enum ('supporting','contradicting','non_instance','confounded','illustrative');
create type evidence_kind      as enum ('content','commerce_item','commerce_review','cluster',
                                        'creator','research_query');
create type assertion_domain   as enum ('corpus_composition','content_performance','commerce_listing',
                                        'commerce_review','temporal','cross_corpus');
create type adversarial_verdict as enum ('survives','survives_narrowed','refuted','indeterminate');
-- DERIVED from 03-multimodal-analysis's `consumption_state` ladder by the fixed mapping in §12.2.
-- `handled` exists because that design states explicitly that `shown_to_camera` is a presentation act
-- and "not by itself use"; folding it into `active_use` would break BRIEF §28 item 4.
create type usage_type         as enum ('active_use','handled','incidental','mentioned_only','unknown');
-- NOTE: the strategy enum is OWNED BY 01-research-loop as `research_strategy`
--   ('brand','category','competitor','theme','hypothesis'). Do not redeclare it here; this
--   subsystem references that type directly so the two can never drift.
-- MIGRATION ORDER: 0007 therefore has a hard dependency on the research-loop migration that
--   creates `research_strategy`, `research_runs`, `research_queries`, and on the migrations
--   creating `content_items`, `brands`, `products`, `clusters`, `creators`, `commerce_items`,
--   `commerce_reviews`, `hypotheses`, `content_analysis_claims`. It must not be applied first.
create type support_verdict    as enum ('supports','contradicts','unrelated','insufficient','structural_pass');

create table evidence_packets (
  id                  uuid primary key default gen_random_uuid(),
  research_run_id     uuid not null references research_runs(id) on delete cascade,
  candidate_key       text not null,
  finding_kind        finding_kind not null,
  subject_brand_id    uuid references brands(id),
  population_frame    jsonb not null,          -- PopulationFrame
  property_predicate  jsonb not null,          -- PropertyPredicate incl. structured_sql / negation_sql
  payload             jsonb not null,          -- the full immutable EvidencePacket as sent to the model
  vocabulary          text[] not null default '{}',
  diagnostics         jsonb not null default '{}',
  builder_version     text not null,
  build_attempt       smallint not null default 0,   -- V4 'recompute' rebuilds the packet; without this
                                                     -- the rebuild collides with the unique key below
  market              text not null,                 -- drives MarketLocalePack + currency formatting
  evidence_design     text not null default 'observational_snapshot',
  built_at            timestamptz not null default now(),
  unique (research_run_id, candidate_key, build_attempt)
);

create table findings (
  id                      uuid primary key default gen_random_uuid(),
  research_run_id         uuid not null references research_runs(id) on delete cascade,
  evidence_packet_id      uuid references evidence_packets(id) on delete set null,
  subject_brand_id        uuid references brands(id),
  parent_finding_id       uuid references findings(id) on delete cascade,
  hypothesis_id           uuid references hypotheses(id) on delete set null,
  supersedes_id           uuid references findings(id) on delete set null,

  claim_type              claim_type   not null,
  finding_kind            finding_kind not null,
  status                  finding_status not null default 'draft',

  -- text. *_template holds {{stat:S#}} / [E#] tokens exactly as the model wrote them (audit trail);
  -- *_rendered holds the interpolated text and is IMMUTABLE once published.
  claim_template          text not null,
  claim_rendered          text not null,
  context_template        text,
  context_rendered        text,
  scope_statement         text not null default 'Within the analysed sample.',
  what_would_refute       text,
  what_would_confirm      text,
  follow_up_queries       text[] not null default '{}',
  prior_support_strength  text,                      -- hypothesis only: 'none'|'weak'|'moderate'

  -- sample
  sample_size             integer not null,          -- raw n of supporting items
  effective_sample_size   numeric(10,2) not null,    -- n_eff after creator/query capping
  population_frame_size   integer,
  temporal_coverage_days  integer,
  concentration           jsonb not null default '{}',  -- inverse_simpson per dimension + top shares
  sponsored_share         numeric(4,3),
  unknown_confidence_share numeric(4,3),

  -- effect
  effect_metric           text,                      -- 'cohens_h'|'lift'|'mann_kendall_tau'|'spearman_rho'|'none'
  effect_value            numeric,
  effect_ci_low           numeric,
  effect_ci_high          numeric,
  effect_ci_method        text,                      -- 'cluster_bootstrap'|'cluster_t'|'mk_variance_blocked'
  trend_p_value           numeric,                   -- RAW p. Never the publication gate on its own.

  -- multiplicity (§6.9). NULL only for kinds that carry no test.
  raw_p                   numeric,
  adjusted_q              numeric,                   -- Benjamini-Hochberg within (run, finding_kind)
  n_tests_in_family       integer,
  selected_from_k         integer not null default 1,
  claim_orientation       text,                      -- canonical orientation imposed at S1 (§6.4)

  -- confidence
  confidence              numeric(4,3),              -- NULL for hypothesis
  confidence_band         confidence_band,
  confidence_components   jsonb not null default '{}',
  gates_failed            text[] not null default '{}',
  caps_applied            text[] not null default '{}',
  withheld_reason         text,
  required_to_promote     jsonb,

  -- competing evidence
  contested               boolean not null default false,
  contested_reason        text,
  contradiction_ratio     numeric(4,3) not null default 0,

  -- verification
  adversarial_verdict     adversarial_verdict,
  adversarial_caveats     jsonb not null default '[]',
  referee_passes          smallint not null default 0,
  validation_summary      jsonb not null default '{}',

  -- snapshot of every stat exactly as rendered at publish time (audit; survives corpus change)
  stat_snapshot           jsonb not null default '{}',

  -- provenance of the generation itself
  generator_model         text,
  generator_effort        text,
  referee_model           text,
  packet_builder_version  text,

  claim_embedding         vector(1536),

  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now(),
  published_at            timestamptz,

  constraint ck_hypothesis_has_no_score check (
    claim_type <> 'hypothesis' or (confidence is null and confidence_band is null)),
  constraint ck_scored_types_have_score check (
    claim_type = 'hypothesis' or status not in ('published') or
    (confidence is not null and confidence_band is not null)),
  constraint ck_interpretation_has_parent check (
    claim_type <> 'interpreted' or parent_finding_id is not null),
  -- Written explicitly rather than relying on three-valued logic: the previous form passed a NULL
  -- band only because `confidence_band in (...)` evaluates to NULL and a CHECK accepts NULL.
  constraint ck_published_band check (
    status <> 'published'
    or (claim_type = 'hypothesis' and confidence_band is null)
    or confidence_band in ('strong','moderate','indicative')),
  -- `absence` has zero supporting items by construction, and `interpreted`/`hypothesis` children have
  -- no sample of their own; the old `sample_size > 0` made all three unpublishable. Children inherit
  -- their parent's sample fields (§10.1a) and `absence` carries the FRAME's size (§6.2b).
  constraint ck_published_has_sample check (
    status <> 'published'
    or finding_kind = 'absence'
    or (sample_size > 0 and effective_sample_size > 0)),
  constraint ck_absence_has_frame check (
    finding_kind <> 'absence' or status <> 'published'
    or (population_frame_size is not null and population_frame_size > 0)),
  constraint ck_trend_needs_coverage check (
    finding_kind <> 'trend' or status <> 'published' or coalesce(temporal_coverage_days,0) >= 28),
  constraint ck_trend_not_cumulative check (
    finding_kind <> 'trend' or status <> 'published' or effect_metric <> 'cumulative_count'),
  -- `contested` can be set by §7.5 AFTER publication, when a later finding contradicts this one, so
  -- this may not be a hard CHECK against 'strong' -- it would make that write impossible (see §7.5).
  -- Band capping for post-publication contesting happens at render time instead.
  constraint ck_contested_capped check (
    not contested or confidence_band is null or confidence_band <> 'strong'
    or contested_reason = 'cross_finding_post_publication'),
  constraint ck_ratio_range check (contradiction_ratio >= 0 and contradiction_ratio <= 1),
  constraint ck_confidence_range check (confidence is null or (confidence >= 0 and confidence <= 1))
);

create index ix_findings_run_status  on findings (research_run_id, status, confidence_band);
create index ix_findings_parent      on findings (parent_finding_id) where parent_finding_id is not null;
create index ix_findings_kind        on findings (research_run_id, finding_kind);
create index ix_findings_published   on findings (research_run_id, published_at desc)
                                     where status = 'published';
create index ix_findings_embedding   on findings using hnsw (claim_embedding vector_cosine_ops)
                                     with (m = 16, ef_construction = 64);
```

#### 10.1a What `interpreted` and `hypothesis` children carry

Both child types are published rows served through the same API, so they must satisfy the same NOT NULLs,
and the original design never said what they contain. Normative:

| Column | `interpreted` child | `hypothesis` child |
|---|---|---|
| `sample_size`, `effective_sample_size`, `population_frame_size`, `temporal_coverage_days`, `concentration` | **inherited verbatim from the parent** — an interpretation rests on exactly its parent's evidence and no more | inherited verbatim, and rendered only as context for what would need testing |
| `confidence`, `confidence_band` | parent's `confidence × G_type`, re-tested against the parent's own gates; **a cap or gate failure on the parent cascades to the child** (an interpretation can never be better evidenced than what it interprets) | NULL, both, enforced by `ck_hypothesis_has_no_score` |
| `caps_applied`, `gates_failed`, `contested` | inherited | inherited |
| `finding_evidence` rows | none of its own; the drill-down renders the parent's | none of its own |

### 10.2 `finding_evidence`, statistics, validations, refutations, links, quarantine

```sql
create table finding_evidence (
  id                  uuid primary key default gen_random_uuid(),
  finding_id          uuid not null references findings(id) on delete cascade,
  ev_ref              text not null,                    -- 'E12', the token the model cited
  role                evidence_role not null,
  kind                evidence_kind not null,

  -- exactly one typed FK is non-null, matching `kind`. Typed columns (not a polymorphic id) so the
  -- database enforces that every cited id actually exists -- the single most important integrity
  -- guarantee in this subsystem.
  content_item_id     uuid references content_items(id)    on delete cascade,
  commerce_item_id    uuid references commerce_items(id)   on delete cascade,
  commerce_review_id  uuid references commerce_reviews(id) on delete cascade,
  cluster_id          uuid references clusters(id)         on delete cascade,
  creator_id          uuid references creators(id)         on delete cascade,
  query_ref_id        uuid references research_queries(id) on delete set null,  -- when the EVIDENCE
                                                                   -- itself is a query (kind='research_query');
                                                                   -- distinct from discovery_query_id below

  -- why this item is here
  supports_properties text[] not null default '{}',
  analysis_claim_id   bigint references content_analysis_claims(id) on delete set null,
                                                 -- the exact analysis claim that makes this item
                                                 -- evidence (03-multimodal-analysis). Drives V17 and
                                                 -- supplies the per-property calibrated confidence.
  analysis_basis      text,                      -- mirror of content_analysis_claims.basis:
                                                 --   seen|heard|written|inferred (V17)
  support_check       support_verdict,
  support_quoted_span text,
  support_model       text,
  excerpt             text,
  excerpt_field       text,
  excerpt_start       integer,
  excerpt_end         integer,
  confound_reason     text,

  -- weighting and provenance (BRIEF §7, §28.10)
  weight              numeric(6,3) not null default 1.0,  -- post-capping contribution to n_eff
  analysis_confidence numeric(4,3),
  usage_type          usage_type not null default 'unknown',   -- derived from consumption_state (§12.2)
  sponsored_likelihood numeric(4,3),
  platform            text,
  creator_ref         text,                                -- may be null; NOT the grouping key
  independence_unit   text not null,                       -- §6.2 stage-0 grouping key. Never null:
                                                           -- 'unknown_creator' when unresolvable, so
                                                           -- unknown-provenance items collapse together
                                                           -- instead of each counting as independent.
  near_duplicate_group text,                               -- §6.2a; shared by re-uploads of one asset
  published_at        timestamptz,
  discovery_query_id  uuid references research_queries(id) on delete set null,
  sampling_strategy   research_strategy,                -- type owned by 01-research-loop
  collection_provider text,
  rank                integer,
  created_at          timestamptz not null default now(),

  unique (finding_id, ev_ref),
  constraint ck_exactly_one_ref check (
    (content_item_id    is not null)::int + (commerce_item_id   is not null)::int +
    (commerce_review_id is not null)::int + (cluster_id         is not null)::int +
    (creator_id         is not null)::int + (query_ref_id       is not null and kind='research_query')::int = 1
  ),
  -- V17 at the storage layer: an item cited as SUPPORTING content evidence must name the analysis
  -- claim that makes it evidence, so 'which inference is this resting on' is always answerable.
  constraint ck_supporting_content_has_claim check (
    not (role = 'supporting' and kind = 'content') or analysis_claim_id is not null),
  constraint ck_kind_matches_ref check (
    (kind='content'         and content_item_id    is not null) or
    (kind='commerce_item'   and commerce_item_id   is not null) or
    (kind='commerce_review' and commerce_review_id is not null) or
    (kind='cluster'         and cluster_id         is not null) or
    (kind='creator'         and creator_id         is not null) or
    (kind='research_query'  and query_ref_id       is not null)
  )
);

create index ix_fe_finding_role on finding_evidence (finding_id, role, rank);
create index ix_fe_content      on finding_evidence (content_item_id)   where content_item_id is not null;
create index ix_fe_commerce     on finding_evidence (commerce_item_id)  where commerce_item_id is not null;
create index ix_fe_query        on finding_evidence (discovery_query_id);
create index ix_fe_props        on finding_evidence using gin (supports_properties);

create table finding_statistics (
  id                uuid primary key default gen_random_uuid(),
  finding_id        uuid not null references findings(id) on delete cascade,
  stat_ref          text not null,                 -- 'S1'
  name              text not null,
  label             text not null,
  value             numeric not null,
  display_value     text    not null,              -- the ONLY string ever rendered
  unit              text    not null,
  n                 integer not null,
  denominator       integer,
  denominator_desc  text    not null,
  n_eff             numeric(10,2),
  design_effect     numeric(6,3),
  ci_low            numeric,
  ci_high           numeric,
  ci_method         text    not null default 'none',
  formula           text    not null,
  domain            assertion_domain not null,
  subject_refs      text[]  not null default '{}',   -- V18 binds prose entities to this
  is_cumulative     boolean not null default false,  -- V14: never sufficient alone, never in a trend
  selected_from_k   integer not null default 1,      -- V20 disclosure
  currency_code     text,                            -- required when unit='currency'
  recompute_key     text    not null,
  recompute_params  jsonb   not null default '{}',
  computed_at       timestamptz not null default now(),
  unique (finding_id, stat_ref),
  -- A plain Wilson interval on clustered data understates uncertainty on exactly the number the
  -- reader trusts most, so the design-effect-corrected form is the only accepted method (§6.3).
  constraint ck_proportion_has_ci check (
    unit <> 'proportion'
    or (ci_low is not null and ci_high is not null
        and ci_method = 'wilson_deff' and n_eff is not null)),
  constraint ck_currency_has_code check (unit <> 'currency' or currency_code is not null)
);

create table finding_validations (
  id          uuid primary key default gen_random_uuid(),
  finding_id  uuid references findings(id) on delete cascade,
  packet_id   uuid references evidence_packets(id) on delete cascade,
  attempt     smallint not null default 0,
  check_code  text not null,
  passed      boolean not null,
  severity    text,
  action      text,
  message     text,
  offending   text[] not null default '{}',
  detail      jsonb not null default '{}',
  created_at  timestamptz not null default now()
);
create index ix_fv_finding on finding_validations (finding_id, attempt, check_code);

create table finding_refutations (
  id                uuid primary key default gen_random_uuid(),
  finding_id        uuid not null references findings(id) on delete cascade,
  referee_index     smallint not null default 0,
  vector            text not null,                  -- RefutationVector
  verdict           text not null,                  -- not_applicable|weak|serious|fatal
  rationale         text not null,
  cited_stat_refs   text[] not null default '{}',
  cited_ev_refs     text[] not null default '{}',
  narrower_claim    text,
  verdict_confidence numeric(4,3),
  model             text,
  created_at        timestamptz not null default now(),
  unique (finding_id, referee_index, vector)
);

create table finding_links (
  id          uuid primary key default gen_random_uuid(),
  from_finding_id uuid not null references findings(id) on delete cascade,
  to_finding_id   uuid not null references findings(id) on delete cascade,
  relation    text not null check (relation in
                ('contradicts','corroborates','narrows','duplicate_of','supersedes','derived_from')),
  rationale   text,
  created_by  text not null default 'code',          -- 'code' | 'referee'
  created_at  timestamptz not null default now(),
  unique (from_finding_id, to_finding_id, relation),
  constraint ck_no_self_link check (from_finding_id <> to_finding_id)
);

create table finding_quarantine (
  id               uuid primary key default gen_random_uuid(),
  research_run_id  uuid not null references research_runs(id) on delete cascade,
  packet_id        uuid references evidence_packets(id) on delete set null,
  candidate_key    text not null,
  claim_type       claim_type,
  claim_template   text,
  reason_code      text not null,                    -- 'V2_unknown_evidence_id', 'referee_fatal', ...
  integrity        boolean not null default false,   -- counts toward the circuit breaker
  reason_detail    jsonb not null default '{}',
  raw_model_output jsonb,
  created_at       timestamptz not null default now()
);
create index ix_fq_run on finding_quarantine (research_run_id, integrity, reason_code);
```

**Published findings are immutable.** A marketer who screenshots a card must not find a different number
there next week.

```sql
create or replace function findings_guard_immutable() returns trigger
language plpgsql as $$
begin
  if old.status = 'published' then
    -- The whole served payload is frozen, not just the headline numbers. An earlier version left
    -- context_rendered, scope_statement, claim_type, effect_* and contradiction_ratio mutable, which
    -- meant the sentence under the number could change after a screenshot was taken.
    if new.claim_rendered        is distinct from old.claim_rendered
       or new.context_rendered      is distinct from old.context_rendered
       or new.scope_statement       is distinct from old.scope_statement
       or new.claim_type            is distinct from old.claim_type
       or new.finding_kind          is distinct from old.finding_kind
       or new.stat_snapshot         is distinct from old.stat_snapshot
       or new.sample_size           is distinct from old.sample_size
       or new.effective_sample_size is distinct from old.effective_sample_size
       or new.population_frame_size is distinct from old.population_frame_size
       or new.effect_metric         is distinct from old.effect_metric
       or new.effect_value          is distinct from old.effect_value
       or new.contradiction_ratio   is distinct from old.contradiction_ratio
       or new.adjusted_q            is distinct from old.adjusted_q
       or new.confidence            is distinct from old.confidence
       or new.confidence_band       is distinct from old.confidence_band then
      raise exception
        'published finding % is immutable; create a new row with supersedes_id instead', old.id;
    end if;
  end if;
  new.updated_at := now();
  return new;
end $$;

-- Deliberately still mutable after publication: `status`, `contested`, `contested_reason`,
-- `adversarial_caveats`, `published_at`, `updated_at`. `contested` must be settable later because
-- §7.5 can discover a contradiction only once the SECOND finding exists, and the honest response to
-- "we later found evidence against this" is to annotate the card, not to rewrite or silently retire
-- it. The band is NOT lowered on that path (the trigger forbids it and so does the audit trail);
-- `render_finding` applies the cap to `display_band` instead, which is what the reader sees.

create trigger trg_findings_immutable before update on findings
  for each row execute function findings_guard_immutable();
```

Updating a published finding means inserting a new row with `supersedes_id` pointing at the old one and
setting the old one's `status='superseded'` (the trigger permits status transitions, which is the one field
excluded from the immutability set). The UI shows the supersession chain.

**RLS.** Enable row-level security on all tables in this migration and gate on run ownership:

```sql
alter table findings enable row level security;
create policy findings_read on findings for select
  using (research_run_id in (select id from research_runs where org_id = auth_org_id()));
```
`auth_org_id()` and the `research_runs.org_id` column are owned by the infrastructure subsystem (§12).
Identical policies apply to `finding_evidence`, `finding_statistics`, `finding_refutations`,
`finding_validations`, `finding_links`, `finding_quarantine`, `evidence_packets` (joined through
`finding_id` / `research_run_id`). Writes are service-role only; the pipeline is the sole writer.

### 10.3 The drill-down view (BRIEF §27, §32)

A Postgres view runs with its owner's privileges unless it is declared otherwise, so a view over
RLS-protected tables **bypasses RLS** and would serve every org's findings to every caller. `security_invoker`
is mandatory here, not optional hardening.

```sql
create view v_finding_provenance with (security_invoker = true) as
select
  f.id                       as finding_id,
  f.research_run_id,
  f.claim_type, f.finding_kind, f.confidence_band, f.contested,
  f.sample_size, f.effective_sample_size,
  fe.ev_ref, fe.role, fe.kind, fe.support_check, fe.usage_type, fe.weight,
  fe.platform, fe.creator_ref, fe.published_at, fe.sampling_strategy, fe.collection_provider,
  rq.id                      as query_id,
  rq.query_text              as discovery_query,
  rq.rationale               as why_this_query_ran,   -- 01-research-loop column names
  rq.strategy                as query_strategy,
  rq.triggered_by_kind,                               -- objective|cluster|competitor|hypothesis|
  rq.triggered_by_id,                                 --   finding|discovered_label|seed|none
  rq.hypothesis_id           as query_hypothesis_id,
  rq.disconfirming           as query_was_disconfirming,
  ci.url                     as content_url,
  ci.thumbnail_url,
  cm.product_url             as commerce_url
from findings f
join finding_evidence fe        on fe.finding_id = f.id
left join research_queries rq   on rq.id = fe.discovery_query_id
left join content_items ci      on ci.id = fe.content_item_id
left join commerce_items cm     on cm.id = fe.commerce_item_id;
```

This single view powers the full §27 chain: *finding → supporting evidence → the actual video/listing*, and
backwards: *finding → the query that found this item → why that query was generated → the hypothesis or
parent finding that triggered it*, which is the §32 Research Run narrative.

### 10.4 Render-time re-validation — the last line

Nothing is served from `findings` directly. Every read goes through `render_finding`, which re-asserts the
invariants against the row it is about to serve. This is deliberate belt-and-braces: a row could in
principle be written by a future migration, a backfill, or a bug, and the renderer is the only code path
between the table and a human.

```python
# aypo/intelligence/findings/renderer.py

class RenderBlocked(Exception):
    def __init__(self, finding_id: UUID, rule: str, detail: str) -> None: ...

@dataclass(frozen=True)
class FindingViewModel:
    id: UUID
    claim_type: ClaimType
    claim_chip: str                      # 'OBSERVED' | 'CALCULATED' | 'INTERPRETATION — NOT A MEASUREMENT' | ...
    claim_text: str                      # interpolated, immutable
    scope_line: str
    context_text: str | None
    stats: tuple[StatView, ...]          # label, display_value, ci, n/denominator, formula, domain
    sample_line: str                     # "8 effective observations from 40 items"
    confidence_band: ConfidenceBand | None
    confidence_breakdown: dict[str, float] | None
    supporting: tuple[EvidenceView, ...]
    competing: tuple[EvidenceView, ...]  # MUST be non-empty when contradiction_ratio > 0
    confounded: tuple[EvidenceView, ...]
    caveats: tuple[str, ...]             # referee 'weak' judgements
    contested: bool
    children: tuple[FindingViewModel, ...]   # interpretations + hypotheses
    insufficient: InsufficiencyView | None
    sources_unavailable: tuple[str, ...]
    display_band: ConfidenceBand | None  # what is SERVED. == confidence_band unless a run-level or
                                         # post-publication cap applies (integrity breaker §4.2,
                                         # cross-finding contradiction §7.5). Exports use this.
    display_band_reason: str | None
    selection_note: str | None           # "k comparable slices were examined" (V20)
    multiplicity: dict[str, float] | None  # raw_p, adjusted_q, n_tests_in_family (§6.9)

def render_finding(
    row: FindingRow,
    stats: Sequence[FindingStatRow],
    evidence: Sequence[FindingEvidenceRow],
    refutations: Sequence[FindingRefutationRow],
) -> FindingViewModel:
    """Raises RenderBlocked on ANY of:
      R1  claim_type chip missing or claim_type null
      R2  sample_size or effective_sample_size null/zero on a published finding
      R3  an unresolved {{stat:...}} or [E#] token remains after interpolation
      R4  a token resolved to a stat/evidence row that is not attached to this finding
      R5  contradiction_ratio > 0 and competing is empty
      R6  a proportion stat with no CI
      R7  claim_type == 'interpreted' and parent not present in the same render tree
      R8  claim_type == 'hypothesis' and a confidence band is set
      R9  status == 'published' and confidence_band == 'insufficient'
      R10 scope_statement empty
      R11 a sponsored_share > 0.20 finding whose stored claim_rendered lacks the disclosure clause.
          NOTE the ordering this fixes: V11(b) is a `force_clause`, and the clause is MATERIALISED
          INTO claim_rendered at S7, before publication and before the immutability trigger can
          freeze it. The renderer never injects it at read time -- if it did, every such finding
          would be either blocked by R11 or served without the disclosure. R11 is the audit that
          S7 did its job.
      R12 a test-bearing finding_kind published with adjusted_q null or >= 0.10 (§6.9)
      R13 a proportion stat whose ci_method is not 'wilson_deff', or whose n_eff is null (§6.3)
      R14 any stat whose subject_refs is empty while the claim text names an entity (V18 invariant)
    """
```

The API serializer (`aypo/api/routers/findings.py`) catches `RenderBlocked`, omits the finding from the
response, logs it at ERROR with the rule code, and increments a metric. A blocked render is a bug, not a
user-facing condition — but the user sees a missing card rather than an unverified claim, which is the
correct trade.

---

## 11. Ask the Data (BRIEF §31.7) — grounded QA over the real dataset

### 11.1 Design stance

Ask the Data is the most dangerous surface in the product, because a chat box invites the model's priors. The
design answers that with four structural commitments:

1. **No SQL, no free-form retrieval.** The agent gets a fixed, typed tool surface. It cannot read a table it
   was not given a tool for, cannot write, and cannot escape the current `research_run_id`.
2. **The same numeric-token discipline as findings.** The answer schema has no numeric fields. Every number
   is a `{{stat:k}}` token that resolves to a value **recorded in this session's tool-call log**. The agent
   physically cannot state a number it did not obtain from a tool.
3. **Insufficiency is a first-class answer**, produced by a mechanical rule on a coverage check, not by the
   model's judgement about whether it knows enough.
4. **World knowledge is fenced.** It is permitted only in a separate `method_note` field (explaining what a
   confidence interval is, say) and is stripped from the answer body.

### 11.2 The tool surface

All tools are scoped to a `research_run_id` injected server-side from the session; the agent cannot pass it.
All tools return `provenance` on every row and `concentration` on every aggregate, so the agent is always
looking at effective counts, not raw ones.

```python
# aypo/research/ask/tools.py

Corpus  = Literal["content", "commerce_items", "commerce_reviews"]
GroupBy = Literal["platform","creator","cluster","week","month","brand","product",
                  "content_format","creator_archetype","language","sponsored",
                  "sampling_strategy","discovery_query","marketplace","category",
                  "discovered_label","usage_type","price_band"]
Metric  = Literal["item_count","share_of_frame","mean_rating","review_count","median_price",
                  "mean_price","sold_count_sum","co_occurrence_rate","distinct_creators",
                  "distinct_queries","effective_units","engagement_median","label_frequency"]
# `review_count` and `sold_count_sum` are platform LIFETIME counters: they carry is_cumulative=True,
# can never alone satisfy a purchase-class assertion (§9.2), and are rejected inside a trend.

class ContentFilters(BaseModel):
    platforms: list[str] | None = None
    date_from: date | None = None
    date_to: date | None = None
    brand_ids: list[UUID] | None = None
    cluster_ids: list[UUID] | None = None
    discovered_labels: list[str] | None = None     # must resolve -- see §11.6
    languages: list[str] | None = None
    sponsored: Literal["only","exclude","any"] = "any"
    usage_types: list[UsageType] | None = None
    content_formats: list[str] | None = None
    creator_archetypes: list[str] | None = None
    sampling_strategies: list[ResearchStrategy] | None = None
    min_analysis_confidence: float | None = None

class ProvenanceRow(BaseModel):
    ev_ref: str                      # session-scoped stable handle the agent cites, e.g. "E7"
    kind: EvidenceKind
    ref_id: UUID
    url: str | None
    thumbnail_url: str | None
    platform: str | None
    creator_ref: str | None
    published_at: datetime | None
    discovery_query: str | None            # research_queries.query_text
    sampling_strategy: ResearchStrategy | None
    analysis_confidence: float | None
    usage_type: UsageType | None
    sponsored_likelihood: float | None
    excerpt: str | None

class SearchResult(BaseModel):
    rows: list[ProvenanceRow]        # each row carries `similarity` when the search was semantic
    returned: int
    total_matching: int              # computed by code, NOT by counting rows
    concentration: dict[str, float]  # inverse_simpson for creator/query/platform over total_matching
    truncated: bool
    # --- semantic-search honesty (§11.6c). Nearest-neighbour search ALWAYS returns something. ---
    min_similarity_applied: float    # rows below this are not returned at all
    no_match_above_threshold: bool   # true when the floor removed everything: "the dataset has
                                     # nothing like this", which is a real and citable answer
    resolved_to: list[str]           # the run-vocabulary labels / clusters the free-text query mapped
                                     # to, so the agent reports what it actually searched for

class AggregateRow(BaseModel):
    group: dict[str, str | None]
    stat_ref: str                    # "S3" -- session-scoped; the agent may only cite these
    value: float
    display_value: str               # pre-formatted
    unit: str
    n: int
    denominator: int | None
    denominator_desc: str
    ci_low: float | None
    ci_high: float | None
    ci_method: str
    formula: str
    assertion_domain: AssertionDomain
    effective_units: float           # concentration diagnostics PER GROUP, over independence_unit
    effective_creators: float        # == effective_units for corpus='content'; kept for UI copy
    effective_queries: float
    distinct_platforms: int
    suppressed: bool                 # true when n < min_group_n; value AND exemplars are withheld
    selected_from_k: int             # how many groups this call returned, stamped on every row, so a
                                     # single extreme group cannot be quoted without its denominator
    exemplars: list[ProvenanceRow]   # up to 5 citable items per group; EMPTY when suppressed -- the
                                     # whole point of suppressing n=3 is undone if the agent can
                                     # count three exemplars and infer the group size

class AggregateResult(BaseModel):
    rows: list[AggregateRow]
    frame_description: str
    frame_size: int
    suppressed_groups: int

class CoverageReport(BaseModel):
    frame_description: str
    n: int
    n_eff: float
    effective_units: float               # over independence_unit; the gate in §11.5 reads this
    independence_unit_kind: Literal["creator","seller","listing","thread","mixed"]
    effective_creators: float            # content corpora only; kept for UI copy
    unknown_independence_share: float
    effective_queries: float
    platforms: list[str]
    sampling_strategies: dict[str, int]  # research_strategy -> count
    date_from: date | None
    date_to: date | None
    span_days: int
    effective_weeks: float
    unknown_confidence_share: float
    sources_unavailable: list[str]         # BRIEF §7
    sufficient_for_answer: bool            # computed by the RULE in §11.5, not by the model
    insufficiency_reasons: list[str]
```

| Tool | Signature | What it is for | Hard limits |
|---|---|---|---|
| `coverage_report` | `async def coverage_report(corpus: Corpus, filters: ContentFilters) -> CoverageReport` | **Must be called before any substantive answer.** Tells the agent, and the validator, whether the slice can carry an answer. | — |
| `search_content` | `async def search_content(query: str, filters: ContentFilters \| None = None, limit: int = 25, min_similarity: float = 0.30) -> SearchResult` | Semantic search over `content_embeddings` (pgvector, cosine) with structured pre-filters. **The free-text `query` is itself run through the §11.6c label resolver and the resolved labels are echoed back in `resolved_to`.** | `limit <= 50`; `total_matching` returned separately so the agent never infers volume from row count; **rows below `min_similarity` are not returned** and `no_match_above_threshold` is set. Without this floor, nearest-neighbour search returns 25 plausible-looking rows for a concept the corpus does not contain, and the agent can cite them — which is the exact seeding failure §11.6c exists to stop, reached through the back door |
| `filter_content` | `async def filter_content(filters: ContentFilters, limit: int = 50, order_by: str = "published_at.desc") -> SearchResult` | Structured retrieval with no semantic component. | `limit <= 50`; `order_by` from a whitelist |
| `aggregate` | `async def aggregate(corpus: Corpus, metric: Metric, group_by: list[GroupBy] \| None = None, filters: ContentFilters \| None = None, min_group_n: int = 5) -> AggregateResult` | **The only source of numbers.** Returns **design-effect-corrected** Wilson CIs on proportions (plain Wilson would understate them on clustered rows, §6.3), per-group concentration diagnostics over `independence_unit`, and exemplars. | ≤2 `group_by` dims; ≤40 groups returned; groups with `n < min_group_n` are returned `suppressed=True` with **both `value` and `exemplars` withheld**. *Why suppress below 5:* a group of 3 produces a proportion whose CI spans most of the range, and a model shown "100%" on n=3 will report it. **Every row carries `selected_from_k`** = the number of groups returned, and A13 forces the answer to disclose it whenever it quotes a single extreme group — the chat surface is exposed to the same multiplicity problem as §6.9 and had no defence at all |
| `search_commerce` | `async def search_commerce(query: str, filters: CommerceFilters \| None = None, limit: int = 25) -> SearchResult` | Semantic + structured over `commerce_items` / `commerce_reviews`. | as `search_content` |
| `get_clusters` | `async def get_clusters(query: str \| None = None, cluster_ids: list[UUID] \| None = None, limit: int = 20) -> ClusterResult` | Cluster lookup by semantic match or id: name, description, size, member brands, date range, confidence. | returns `label_is_post_hoc: bool` so the agent knows the label was assigned after inspection (BRIEF §21) |
| `get_cluster_members` | `async def get_cluster_members(cluster_id: UUID, limit: int = 50) -> SearchResult` | Drill into a cluster. | `limit <= 50` |
| `get_findings` | `async def get_findings(query: str \| None = None, filters: FindingFilters \| None = None, limit: int = 20) -> FindingResult` | **Preferred first stop.** Returns already-validated, already-refereed findings with band, `n_eff`, contested flag and evidence refs. | returns `insufficient`-band findings only when `include_withheld=True`, and then clearly flagged |
| `get_items` | `async def get_items(ev_refs: list[str]) -> list[ProvenanceRow]` | Hydrate specific session-scoped refs for citation rendering. | ≤50 refs |

Deliberately **absent**: anything that returns an unbounded row set, anything that executes model-authored
SQL, anything that reaches outside `research_run_id`, and any web search. The agent answers *from the
dataset*; if the dataset is silent, the answer is that the dataset is silent.

### 11.3 Answer contract

```python
class AskStat(BaseModel):
    stat_ref: str                 # must match a stat_ref returned by an `aggregate` call this session
    tool_call_seq: int            # which call produced it

class AskParagraph(BaseModel):
    text: str                     # {{stat:S#}} tokens and [E#] citations only; no digits
    cited_ev_refs: list[str]
    cited_stat_refs: list[str]

class AskAnswer(BaseModel):
    sufficient: bool
    insufficiency_reasons: list[str] = Field(default_factory=list)
    direct_answer: str | None = None            # one sentence, tokens + citations
    paragraphs: list[AskParagraph] = Field(default_factory=list)
    observation_vs_interpretation: dict[str, list[int]] = Field(default_factory=dict)
                                                # {"observation":[0,1], "interpretation":[2]}
    uncertainty_note: str | None = None         # what the data cannot settle
    presupposition_checks: list[PresuppositionCheck] = Field(default_factory=list)
    method_note: str | None = None              # the ONLY place general knowledge is allowed
    follow_up_suggestions: list[str] = Field(default_factory=list)
```

### 11.4 Answer validator (code, post-hoc)

Mirrors the finding validators; reuses the same functions.

| Check | Rule | On failure |
|---|---|---|
| `A1 no_digits` | No `[0-9]` outside `{{stat:...}}`/`[E#]` tokens in `direct_answer`, `paragraphs[].text`, `uncertainty_note`. `QUANTIFIER_LEXICON` requires a stat token in the same sentence. | regenerate once → insufficiency template |
| `A2 stat_provenance` | Every `{{stat:S#}}` resolves to a `stat_ref` in `ask_tool_calls.returned_stats` for **this message**, and the value substituted equals the recorded value exactly. | regenerate once → insufficiency template |
| `A3 citation_provenance` | Every `[E#]` resolves to an `ev_ref` in `ask_tool_calls.returned_ids` for this message. | regenerate once → insufficiency template |
| `A4 grounding_ratio` | `grounding_ratio = cited_assertive_sentences / assertive_sentences >= 0.90`. Sentences in `method_note` are exempt; sentences in `paragraphs` are not. | regenerate once → insufficiency template |
| `A5 sample_size_present` | Every paragraph containing a `{{stat:...}}` must also surface that stat's `n` and `denominator_desc` — the renderer appends `(n of denominator)` automatically if the text omits it. | auto-append |
| `A6 suppressed_group` | No cited stat may come from an `AggregateRow` with `suppressed=True`. | strip the sentence; then A4 |
| `A7 domain_match` | §9.2 matrix applied to the answer's predicates. | strip the sentence; then A4 |
| `A8 world_knowledge` | A paragraph sentence with zero citations and an assertive main verb is stripped. If stripping empties the answer, the insufficiency template is used. | strip → A4 |
| `A9 label_resolution` | Every discovered-label string in the answer must exist verbatim in the run's vocabulary (same index as V9). | strip → A4 |
| `A10 coverage_called` | `coverage_report` must appear in this message's tool log before any `aggregate`. | force the call, re-run the turn |
| `A11 obs_interp_split` | `observation_vs_interpretation` is model-assigned and drives a UI label, so it is checked rather than trusted: every paragraph indexed `observation` must be fully cited (ratio 1.0) and contain no `HEDGE_LEXICON` or modal token; every paragraph indexed `interpretation` must contain ≥1 hedge and introduce no `{{stat:...}}` token absent from the observation paragraphs. Unindexed paragraphs default to `interpretation`. | reindex mechanically; a paragraph that fits neither is stripped → A4 |
| `A12 no_unmatched_semantic` | A `[E#]` whose originating tool call returned `no_match_above_threshold = True`, or whose row similarity is below `min_similarity_applied`, may not be cited. | strip → A4 |
| `A13 selection_disclosed` | Any sentence quoting a stat from an `AggregateRow` with `selected_from_k > 1` must also state the group count; the renderer appends "(of k groups examined)" if the text omits it. | auto-append |

Each answer stores its `grounding_ratio`, its validator outcomes, and the full tool log. The UI exposes a
collapsible **"How I got this"** panel listing every tool call with its arguments and the number of rows
returned — the §27 "never a black box" rule applied to chat.

### 11.5 The insufficiency rule (mechanical, not discretionary)

After `coverage_report`, code — not the model — sets `sufficient_for_answer`:

```python
def is_sufficient(c: CoverageReport, *, comparative: bool) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if c.n < 10:                                   reasons.append("fewer_than_10_items_in_frame")
    if c.n_eff < 5:                                reasons.append("fewer_than_5_effective_observations")
    # independence units, NOT creators: a commerce frame has no creators at all, and gating it on
    # `effective_creators < 3` meant Ask the Data could never answer a commerce or social-to-commerce
    # question -- i.e. the chat box was structurally blind to BRIEF 17, the differentiating capability.
    # The unit is sellers for commerce_items, listings for commerce_reviews, creators for content.
    if c.effective_units < 3:                      reasons.append("fewer_than_3_effective_sources")
    if comparative and c.effective_queries < 2:    reasons.append("single_query_behind_comparison")
    if c.unknown_independence_share > 0.30:        reasons.append("provenance_too_weak_to_judge_independence")
    if c.sources_unavailable:                      reasons.append("sources_unavailable")  # advisory only
    hard = [r for r in reasons if r != "sources_unavailable"]
    return (not hard), reasons
```

> *Why `n < 10` and `n_eff < 5`.* These are the `indicative` band floors from §6.6. Ask the Data must never
> be able to assert something more confidently than the findings pipeline would publish it; otherwise the
> chat box becomes a bypass around the entire verification stack. Tying the two together by construction is
> the point.
> *Why `effective_units < 3`.* Two independent sources is an anecdote regardless of item count — and the
> unit must be whatever "independent source" means for that corpus, or the floor either never fires
> (40 reviews of one listing) or always fires (any commerce question at all).
> *Why the comparative clause.* A comparison drawn from one query is a comparison between two slices of the
> same retrieval, which is a statement about the search engine.
> *`sources_unavailable` is advisory*, not blocking (BRIEF §7): a run missing `APIFY_TOKEN` still has a
> dataset worth asking about; the answer must simply say which platforms are absent, every time.

When `sufficient = False` the agent **must** use the insufficiency template and may not answer
substantively:

> *"The collected dataset cannot answer this. For `<frame description>` it contains N items from E
> effective `<creators|sellers|listings>` across D days, below the threshold this product uses before
> stating anything (10 items and 5 effective observations). `Platform P2` was unavailable during this run.
> What the dataset does contain nearby: `<nearest resolvable labels and clusters, with their counts>`. To
> answer this, the run would need: `<specific follow-up queries>`."*

The template is rendered by code, so the numbers in it are not subject to the token discipline the model's
prose is — and that is the point: the one number the model may never produce is the one that says how
little we have.

The last clause hands the gap back to the research loop: the agent emits `follow_up_suggestions` which the
research subsystem can ingest as candidate queries (BRIEF §8 E).

### 11.6 Leading-question guardrail (BRIEF §1, §34)

A leading question is the main vector by which a user can seed the answer the product is supposed to
discover. Three mechanisms, all pre-answer.

**(a) Question audit.** A cheap pre-pass before any tool call.

```python
class PresuppositionCheck(BaseModel):
    presupposition: str
    resolves_to: str | None        # the vocabulary entry it resolved to, or None
    n_in_frame: int
    n_eff_in_frame: float
    holds: bool
    note: str

class QuestionAudit(BaseModel):
    neutral_restatement: str
    presuppositions: list[str]
    is_leading: bool
    leading_reason: str | None
    unresolved_labels: list[str]
    symmetric_tests: list[str]     # the queries needed to avoid a one-sided search
    comparative: bool
```

```text
SYSTEM
You audit an analyst's question before a grounded data agent answers it. You do not answer it. You do not
use world knowledge about brands, categories, markets or culture.

Return:
  neutral_restatement - the question rewritten so it does not assume its own answer. Turn "why does X
                        drive Y" into "is there a relationship between X and Y in this dataset, and in
                        which direction".
  presuppositions     - every entity, theme, label, behaviour, audience or relationship the question
                        assumes already exists or is already true. One per line, verbatim where possible.
  is_leading          - true if the question's phrasing would make a one-sided search produce an
                        apparently confirming answer.
  unresolved_labels   - any label-like phrase in the question that an analyst may have invented rather
                        than taken from the data.
  symmetric_tests     - for any comparison, the queries needed on BOTH sides, plus the base rate. For
                        "is Theme X bigger for Brand A", the symmetric tests are Theme X for the subject
                        brand, Theme X for other discovered brands, and the overall rate of Theme X.
  comparative         - true if answering requires comparing two or more groups.

USER
QUESTION: {user_question}
RUN VOCABULARY (the labels this dataset actually contains): {vocabulary_sample}
```

> **The audit is model-produced, so it cannot be the only gate.** (b) and (d) below are genuinely
> mechanical *given the list*, but the list itself comes from a model, and its failure mode is the
> permissive one: a presupposition the auditor fails to name is a presupposition nothing tests, and the
> leading question gets its yes. Code therefore supplies an independent floor that does not depend on the
> auditor at all:
>
> - every label-like span in the question is extracted by the §11.6c resolver and **each resolved label is
>   forced into `presuppositions`** whether or not the auditor named it;
> - `comparative` is set by code as `audit.comparative OR (>=2 distinct resolved entities) OR (a token
>   from a fixed comparison lexicon: "more", "less", "than", "compared", "versus", "vs", "better",
>   "worse", "bigger", "smaller")` — the model can only turn it on, never off;
> - the **base rate for every resolved label over the whole run frame is computed unconditionally**, before
>   any other aggregate, and rendered whether or not it helps the question.
>
> The model's audit adds recall on top of that floor. It cannot subtract from it.

**(b) Presupposition-first answering.** If `presuppositions` is non-empty, the agent **must** resolve and
test each one with `coverage_report` / `aggregate` before addressing the rest, and the answer must open with
`presupposition_checks`. If a presupposition does not hold (`n_eff < 5` for the presupposed entity, theme or
relationship), the answer says so plainly and **does not proceed as though it held**. Enforced in the
orchestration loop, not left to the model: `AskAgent.run()` refuses to emit a final answer while any
`presuppositions` entry has no corresponding check.

**(c) Label resolution — the hard anti-seeding gate.** Any label-like phrase from the user's question is run
through the same vocabulary index as V9 (NFKC + casefold + whitespace collapse, then an embedding nearest-
neighbour fallback). The tools **refuse** an unresolved label: `ContentFilters.discovered_labels` raises
`UnresolvedLabel` with the five nearest actual labels and their counts. The agent then reports:

> *"The dataset has no label `<user phrase>`. The nearest labels it does contain are `<L1>` (n=…), `<L2>`
> (n=…) … Would you like the answer for one of those, or should the research run collect for `<user
> phrase>`?"*

This is the mechanism that stops a user from asking "how big is `<invented theme>`?" and getting a number.

> **It only works if semantic search is covered too.** An earlier draft claimed the agent "physically
> cannot search for a concept the data does not contain". That was false: `UnresolvedLabel` guards
> `ContentFilters.discovered_labels`, but `search_content(query=...)` and `search_commerce(query=...)` take
> free text, and a pgvector nearest-neighbour search **always returns its k nearest rows** however far away
> they are. The agent could therefore search for an invented concept, receive 25 superficially plausible
> items, and cite them — reaching the same bad outcome through a different tool.
> **Fix, in three parts:** (i) the free-text query is run through this same resolver and the resolved
> labels are returned in `SearchResult.resolved_to`, so the answer must say what it actually searched for;
> (ii) a `min_similarity` floor (default 0.30, configurable, calibrated per §13.7) drops rows below it, and
> an empty result sets `no_match_above_threshold = True`, which is a *citable finding of absence in the
> corpus* rather than a silent nearest-match; (iii) validator **A12** forbids citing any row from a search
> that returned `no_match_above_threshold`. The agent can still *look*; it can no longer come back with
> evidence for something that is not there.

**(d) Symmetric search enforcement.** For `comparative = True` questions, `AskAgent.run()` requires that the
tool log contains an `aggregate` call covering **each** of `symmetric_tests` before an answer is emitted. A
question phrased to invite a yes therefore cannot get a yes-only search: the base rate and the other side
are always computed, and the answer renders them whether or not they support the question's premise.

### 11.7 Ask the Data system prompt

```text
SYSTEM
You are the data analyst for an evidence-first market-research platform. You answer questions strictly
about one collected research dataset. You have tools that query that dataset. You have no other source of
knowledge, and you must behave as though you have none.

WHAT YOU MAY NOT DO
1. You may not answer from general knowledge about brands, categories, markets, consumers or culture. If
   the dataset does not contain it, you do not know it. This holds even when you are confident, and even
   when the answer seems obvious.
2. You may not compute. Every number must be a {{stat:S#}} token taken from an `aggregate` result in this
   conversation. Do not count rows. Do not estimate. Do not round. Digits are forbidden in your prose, and
   so are vague quantifiers (most, many, few, majority, often, usually) unless the same sentence carries a
   stat token.
3. You may not cite an id a tool did not return to you in this conversation.
4. You may not use a label, theme, occasion, behaviour, slang term or brand name that is not in the run
   vocabulary or returned by a tool. If the analyst used a label the dataset does not contain, say so and
   offer the nearest labels that do exist.
5. You may not state or imply causation. Co-occurrence and correlation are not causes.
6. You may not describe people's age, gender, income, class or household. The dataset does not contain
   reliable demographics.
7. You may not treat engagement as preference, visibility as endorsement, positive comments as sales, or
   incidental product appearance as product usage.

WHAT YOU MUST DO
1. Call `coverage_report` for the relevant slice before any substantive answer. If the result says the
   slice is insufficient, say the dataset cannot answer, state what it does contain, state what is missing,
   and stop. Insufficient is a correct and expected answer. It is better than a careful-sounding guess.
2. If the question presupposes anything, test each presupposition against the data first and report whether
   it holds, before answering the rest.
3. For any comparison, compute every side AND the base rate, even when the question only asks about one
   side.
4. Cite every assertive sentence with [E#] refs, and carry the sample size with every number.
5. Separate what you observed from what you read into it, using observation_vs_interpretation. Interpretation
   is allowed, must be hedged, and must introduce no new number and no new entity.
6. Say what the data cannot settle, in uncertainty_note. Every answer has one.
7. Prefer `get_findings` first: a validated finding has already passed statistical verification and
   adversarial review. Re-deriving a number yourself is weaker evidence than citing a verified finding.
8. Scope every statement to the analysed dataset. Never phrase anything as a fact about the market.
9. Note any platform that was unavailable for this run whenever it bears on the answer.

PUT GENERAL METHODOLOGICAL EXPLANATION IN `method_note` ONLY. Never in the answer body.
```

### 11.8 Agent loop and storage

```python
# aypo/research/ask/agent.py

class AskAgent:
    def __init__(
        self,
        llm: AnthropicGateway,
        tools: AskToolRegistry,
        auditor: QuestionAuditor,
        validator: AnswerValidator,
        model: str = "claude-opus-5",
        effort: Literal["low","medium","high"] = "medium",
        max_tool_turns: int = 8,
    ) -> None: ...

    async def run(
        self,
        session_id: UUID,
        question: str,
        *,
        research_run_id: UUID,
    ) -> AskAnswer:
        """Orchestration invariants, enforced in code, not by the prompt:
          1. QuestionAudit runs first and its result is stored.
          2. coverage_report is called before any aggregate (A10).
          3. No final answer while any audited presupposition lacks a PresuppositionCheck.
          4. No final answer while any symmetric_test lacks a corresponding aggregate call.
          5. is_sufficient() decides sufficiency; the model does not.
          6. AnswerValidator runs on the parsed answer; one regeneration, then the
             insufficiency template.
          7. Every tool call is persisted to ask_tool_calls before the next turn.
          8. max_tool_turns caps cost; hitting the cap yields the insufficiency template with
             reason 'tool_budget_exhausted' rather than a partially-grounded answer.
        """
```

```sql
create table ask_sessions (
  id               uuid primary key default gen_random_uuid(),
  org_id           uuid not null,
  user_id          uuid not null,
  research_run_id  uuid not null references research_runs(id) on delete cascade,
  title            text,
  created_at       timestamptz not null default now()
);

create table ask_messages (
  id               uuid primary key default gen_random_uuid(),
  session_id       uuid not null references ask_sessions(id) on delete cascade,
  seq              integer not null,
  question_text    text not null,
  question_audit   jsonb not null default '{}',     -- QuestionAudit
  coverage         jsonb not null default '{}',     -- CoverageReport
  answer           jsonb,                           -- AskAnswer (post-validation, rendered)
  sufficient       boolean not null default false,
  insufficiency_reasons text[] not null default '{}',
  grounding_ratio  numeric(4,3),
  validator_log    jsonb not null default '[]',
  regenerated      boolean not null default false,
  model            text,
  effort           text,
  input_tokens     integer,
  output_tokens    integer,
  cost_usd         numeric(10,5),
  latency_ms       integer,
  created_at       timestamptz not null default now(),
  unique (session_id, seq)
);

create table ask_tool_calls (
  id               uuid primary key default gen_random_uuid(),
  message_id       uuid not null references ask_messages(id) on delete cascade,
  seq              integer not null,
  tool_name        text not null,
  arguments        jsonb not null,
  returned_ids     text[] not null default '{}',    -- ev_refs the agent may now cite (A3)
  returned_stats   jsonb not null default '{}',     -- stat_ref -> {value, display_value, n, denominator,
                                                    --   ci, domain} -- the ONLY citable numbers (A2)
  row_count        integer,
  total_matching   integer,
  suppressed_groups integer,
  error            text,
  latency_ms       integer,
  created_at       timestamptz not null default now(),
  unique (message_id, seq)
);
create index ix_atc_message on ask_tool_calls (message_id, seq);
```

`ask_tool_calls.returned_ids` and `returned_stats` are the allowlists A2/A3 validate against. They are
written by the tool layer, not by the model, which is what makes "you may not cite an id you were not given"
a checkable property rather than an instruction.

---

## 12. Cross-subsystem contracts

This subsystem cannot be built unless the following hold. Each is phrased as a contract another design must
satisfy.

### 12.1 From the candidate miners (intelligence subsystem: clustering, trends, competitive, commerce)

Every candidate must arrive as a `StatCandidate`. Pattern *detection* stays with the miners; this subsystem
refuses anything that does not carry its own denominator and its own negation.

```python
# contract type, consumed by PacketBuilder
class StatCandidate(BaseModel):
    candidate_key: str                       # stable across runs for dedup/merging
    research_run_id: UUID
    finding_kind: FindingKind
    population_frame: PopulationFrame        # description, size, machine-reproducible filter_spec, dates
    property_predicate: PropertyPredicate    # key, description, structured_sql, negation_sql
    supporting_ids: list[EvidenceRef]        # real ids; MUST be the complete set, not a sample
    contradicting_ids: list[EvidenceRef]     # items satisfying negation_sql
    confounded_ids: list[tuple[EvidenceRef, str]]
    stats: list[ComputedStat]                # computed by the miner, with formula + CI + domain +
                                             #   recompute_key, which MUST be registered
    effect_metric: str
    effect_value: float | None
    effect_ci: tuple[float, float] | None
    trend_p_value: float | None
    vocabulary_keys: list[str]               # which label strings this candidate may use
```

Requirements:

1. **`supporting_ids` is the complete set**, not a sample. `n_eff` is computed from it; a sampled list would
   silently inflate confidence.
2. **`negation_sql` must exist** for every `finding_kind` other than `existence`. Without it there is no
   `contradicting` set, no `ρ`, and no contested detection. A candidate with no negation is rejected at S1.
3. **Every `recompute_key` must be registered** in `StatRecomputer`'s registry so V4 can re-derive the value
   deterministically. An unregistered key fails the candidate at S1.
4. **`assertion_domain` must be set correctly** on every stat. V14's entire force depends on it. Mislabelling
   a `content_performance` stat as `corpus_composition` would defeat the engagement-is-not-preference guard,
   so the registry asserts the domain per `recompute_key` rather than trusting the caller.
5. Miners must not author label strings. Labels come from the data; `vocabulary_keys` references them.

### 12.2 From the per-item analysis subsystem (`03-multimodal-analysis`)

That design's `content_analysis_claims` table is exactly the right substrate, and this subsystem binds to it
rather than to a looser contract. Required bindings:

| What this subsystem needs | The column it reads | Why |
|---|---|---|
| per-**property** confidence, not one scalar per item | `content_analysis_claims.calibrated_confidence`, selected by `(content_item_id, field, value_norm)` | `S_item` (§6.3) must use the confidence for the property actually being asserted. `model_confidence` is explicitly never displayed by that design and this subsystem must not display it either. |
| eligibility of an item as citable evidence | `claim_status = 'published'` **and** `cue_check = 'pass'` | A claim the analysis subsystem itself marked `unverified`/`rejected`, or whose cue was `vacuous`/`unquotable`/`anchor_invalid`, may not be cited as supporting evidence. `PacketBuilder` filters on this at S1. |
| verbatim excerpt with offsets for V6's substring check | `content_analysis_claims.anchors` (jsonb) + `cue` | V6 downgrades a `supports` verdict whose `quoted_span` is not a literal substring. Without real anchors that check is unenforceable. |
| the observed/inferred distinction | `basis ∈ {seen, heard, written, inferred}` | **This maps directly onto §2.2.** A claim of type `observed` may cite only items whose supporting analysis claim has `basis ∈ {seen, heard, written}`. A `basis='inferred'` analysis claim may support an `interpreted` finding but **never** an `observed` or `calculated` one. This is added as validator **V17 `basis_guard`** (action: `strip_citation` → `demote` → `drop`). |
| the usage / visibility distinction (§28 items 4–5) | `field_group ∈ {brand_identity, product_use, sponsorship, discovered_label, descriptor}` plus **`consumption_state`** within `product_use` | V11. A finding about *usage* may cite only `field_group='product_use'` claims; a finding about *visibility* may cite `brand_identity` and the renderer forces "appears" rather than "is used". **`usage_type` is not an upstream field** — an earlier draft of this document invented it. The upstream field is the seven-rung ordinal `consumption_state`, and this subsystem derives `usage_type` from it by the fixed mapping below. |

**The `consumption_state` → `usage_type` mapping (normative).** `03-multimodal-analysis` defines
`consumption_state` as an ordinal over human–object interaction and states explicitly that
`shown_to_camera` is "a presentation act, which is not by itself use". Folding it into `active_use` would
break BRIEF §28 item 4 at the first hop, so the derived enum has a fifth value:

| `consumption_state` | derived `usage_type` | V11(a) counts it as active use? |
|---|---|---|
| `in_use_or_consumed`, `offered_or_shared` | `active_use` | **yes** |
| `held`, `being_opened`, `shown_to_camera` | `handled` | **no** — handling and presenting are not consuming |
| `present` | `incidental` | no |
| no `product_use` claim, only a `MentionedBrand` | `mentioned_only` | no |
| `unclear`, or no claim at all | `unknown` | no |

The derivation runs in `PacketBuilder`, is stored on `finding_evidence.usage_type`, and is covered by a
fixture test that fails if `03`'s ladder gains a rung this table does not map.
| sponsorship likelihood | `field_group='sponsorship'` claims | `sponsored_share`; V11(b)/(c). |
| a near-duplicate / repost grouping per content item | **not present in any sibling design yet** | Reposts are the most common silent inflation of `n` and must land in `confounded`, not `supporting`. **This is an outstanding gap** — see §13.12. |

### 12.3 From the research-loop and collection subsystems (`01-research-loop`)

| What this subsystem needs | Status in `01-research-loop` | Note |
|---|---|---|
| `research_queries.id` on every content item (not just the query text) | ✅ `research_queries` exists with `run_id`, `strategy`, `query_text`, `rationale`, `triggered_by_kind`, `triggered_by_id`, `hypothesis_id`, `disconfirming` | `content_items.discovery_query_id` must FK to it. Query-concentration math and the §32 chain both depend on the id, not the text. |
| the five §8 strategies as a typed enum | ✅ `research_strategy` = `brand \| category \| competitor \| theme \| hypothesis` | This subsystem references that type; it does not declare its own. `S_strategy` and the >70% cap read it. |
| why a query ran | ✅ `rationale`, `triggered_by_kind`, `triggered_by_id` | Powers `v_finding_provenance.why_this_query_ran`. |
| disconfirming-query marking | ✅ `research_queries.disconfirming` | Valuable beyond provenance: evidence found by a *disconfirming* query is stronger than evidence found by a confirming one. **Proposed refinement:** weight disconfirming-query evidence at 1.0 and confirming-query evidence at 1.0, but require that a `strong`-band finding have ≥1 supporting item discovered by a `disconfirming=true` query. Flagged in §13.13 because the right weighting is not yet knowable. |
| a stable opaque `creator_ref` per creator per platform | ⚠️ `creators` table not defined in any sibling design (verified: no `create table creators` anywhere in `docs/design/`) | Source-concentration math is the single most load-bearing input to `n_eff`, and it cannot be left blocked on a table nobody owns. **Interim derivation, owned here until a `creators` table lands:** `creator_ref = lower(platform) \|\| ':' \|\| nfkc_casefold(content_items.creator_handle)` when a handle exists; otherwise the item takes `independence_unit = 'unknown_creator'` and joins the single shared unknown group (§6.2). This is deliberately conservative — one real creator appearing under two handles is counted twice (diversity over-stated, which `unknown_independence_share` surfaces), but unknown-provenance items are never counted as independent, which was the dangerous direction. |
| `content_items`, `brands`, `products`, `hypotheses` | ⚠️ referenced by siblings (`subject_brand_id uuid, -- FK brands(id), added later`) but not yet declared | My FKs in §10 assume these exist with `uuid` primary keys. |
| which platforms were unavailable this run | ⚠️ `research_runs.config_resolved jsonb` holds "provider availability" | §6.7 and §11.5 must name the missing source. **Contract:** `config_resolved` must contain a stable key such as `{"sources_unavailable": ["..."]}`, or a dedicated `research_runs.sources_unavailable text[]` should be added. Reading provider availability out of an unstructured jsonb blob is not safe enough for a user-facing claim about absence. |
| tenancy for RLS | ⚠️ `research_runs` has `created_by uuid` but no `org_id` | §10.2's policies assume `research_runs.org_id` and an `auth_org_id()` helper. If the product stays single-tenant for the MVP, the policies should gate on `created_by = auth.uid()` instead. **This must be decided before the migration lands**, because the policy text differs. Note this document is currently self-inconsistent on the point: `ask_sessions.org_id uuid not null` (§11.8) presumes the multi-tenant answer. Whichever way it goes, `ask_sessions` must be changed in the same migration, and the `v_finding_provenance` view must keep `security_invoker = true` either way. |
| the market, for the locale pack | ⚠️ not declared | `research_runs.market text not null` (ISO-3166 alpha-2) drives `MarketLocalePack` and currency formatting (§2.3). Without it the guardrail lexicons have no language and the only alternative is hard-coding a market, which is the §1/§34 violation this document had. |

### 12.3a From the open-taxonomy subsystem (`02-open-taxonomy`)

The `labels` table is the canonical vocabulary source, and this subsystem reads it rather than assembling
label strings itself:

| Binding | Column | Use |
|---|---|---|
| V9 `vocabulary_guard` index and §11.6 label resolution | `labels.normalised_form` (immutable identity), `display_form`, `embedding vector(1536)` | `packet.vocabulary` is built from `display_form` for admitted leader labels (`canonical_id IS NULL` and `status` admitted); matching uses `normalised_form`; the nearest-label fallback uses `embedding`, which is already the same 1536-dim space (§12.4). |
| pre-computed concentration per label | `distinct_creators`, `distinct_queries`, `distinct_platforms`, `distinct_strategies`, `top_creator_share`, `support_count` | A cheap pre-filter: a candidate whose central label already has `distinct_creators < 3` cannot clear the `indicative` gate, so it can be rejected at S0 before any model call. Note these are documented as **cache only** in that design, so the authoritative values must still be recomputed at S1 — the cache is used for cheap rejection, never for the published `n_eff`. |
| `labels.holdout_support` | support from the no-suggestion arm | **Directly valuable for §1/§34.** A label with `holdout_support = 0` was only ever observed when the analysis was prompted with a suggestion, which is the clearest available signal of a self-fulfilled label. **Proposed rule:** a finding whose central label has `holdout_support = 0` is capped at `indicative` and the card states that the label has not been observed without prompting. Flagged in §13.14 as needing agreement with that subsystem's owner. |

### 12.4 From the embeddings subsystem

A reusable `embed_texts(texts, *, dimensions=1536) -> list[list[float]]` using OpenAI
`text-embedding-3-large` (verified live at 1536 dims in `docs/ENVIRONMENT.md`; 1536 is a hard constraint
because pgvector's HNSW tops out at 2000 and the model's native 3072 cannot be indexed), with a fallback
provider, for V16 claim dedup, §6.2a near-duplicate collapse, §7.5 contradiction detection and the §11.6
nearest-label fallback.

**Every stored vector must carry an `embedding_model` tag, and cosine thresholds apply only within one
tag.** A fallback provider produces a *different* vector space, so a 0.92 cosine from one provider and a
0.92 from another are not the same quantity — silently comparing across them would make dedup, contradiction
detection and the near-duplicate collapse behave arbitrarily on exactly the runs where the primary provider
failed. When a claim's embedding tag differs from a candidate neighbour's, V16 and §7.5 **skip the
comparison and log `reason='embedding_space_mismatch'`** rather than comparing. Skipping costs a duplicate
card; comparing costs a wrong merge.

### 12.5 From the jobs subsystem

The pipeline runs as a Postgres-backed queue job (no Redis, no Celery):
`aypo/jobs/findings_jobs.py::generate_findings_job(run_id)`, with per-candidate checkpointing so a crash
resumes rather than restarts, and an advisory lock on `(research_run_id, 'findings')` so two workers cannot
publish the same candidate. Required: at-least-once delivery with idempotency on `candidate_key`, and a
`FindingBudget` (max candidates, max USD, max wall-clock) enforced per run.

**What happens when the budget is hit was unspecified, and silence here is a §7 failure.** Normative: on
exhaustion the pipeline stops *mining* new candidates, finishes the candidates already in flight, still
runs `finalise_run()` (the §6.9 BH gate must see a complete family or its q-values are wrong — and the
family is recorded as truncated), and records `stopped_reason ∈ {budget_usd, budget_candidates,
budget_wall_clock}` on the run. The Overview must state that the run was truncated and how many candidates
were never evaluated, because a run that stopped early looks exactly like a run that found nothing.
Note also that `FindingBudget.max_usd = 30` and the §8 headroom note ("150 candidates ≈ $45") are
inconsistent: raising `max_candidates` without raising `max_usd` truncates the run rather than widening it.
Both are in `FindingSettings`/`FindingBudget` and must be raised together.

### 12.6 What this subsystem provides to others

| Consumer | Contract |
|---|---|
| UI Overview (§31.1) | `GET /runs/{id}/findings?hero=true` — published, non-contested, non-`existence`; `strong` when any exists, otherwise the top-ranked `moderate` with `hero_fallback=true`; always with `hero_note` and the withheld count, so an empty hero is never served as blank space (§6.10) |
| UI sections 2–4 | `GET /runs/{id}/findings` with filters on `finding_kind`, `claim_type`, cluster, brand, platform, date |
| UI drill-down (§27) | `GET /findings/{id}` → `FindingViewModel` including `competing`, `confounded`, `caveats`, `confidence_breakdown` |
| Research Run view (§32) | `GET /runs/{id}/provenance` over `v_finding_provenance` |
| Research loop (§25) | `GET /runs/{id}/findings?claim_type=hypothesis` and the `follow_up_queries` array; plus Ask-the-Data `follow_up_suggestions` as candidate queries |
| Run report | quarantine counts, integrity rate, referee kill rate, declined count, insufficient count |

---

## 13. Open questions — genuine uncertainty, not placeholders

1. **`unit_cap = 3` and `query_cap = 8` are priors, not measurements.** The right way to set them is
   empirical: for findings with ≥2 creators' worth of spare evidence, hold out all items from a random
   subset of creators, re-derive the finding from the remainder, and measure how often the effect replicates
   at each cap. The cap should be set where the replication rate stops improving. That requires several
   completed runs' worth of data, which does not exist yet. Until then, both numbers are configurable in
   `FindingSettings` and surfaced in the run report so their effect is visible.
2. **Referee calibration has no ground truth.** `apply_kill_rule` is a defensible decision procedure but
   nobody has verified that its kills are the *right* kills. The proposed remedy is a human-labelled set of
   ~40 candidate findings from the first two runs, labelled by someone with category knowledge as
   sound/overreaching/wrong, and a measured false-kill and false-keep rate. Until that exists, the kill rate
   monitors in §5.4 detect gross miscalibration but not subtle bias.
3. **Should `interpreted` findings carry a confidence band at all?** The current design gives them
   `G_type = 0.90` and a band. The argument against: a band on an interpretation invites reading the
   interpretation as a measurement, which §3 of the brief exists to prevent, and `0.90 ×` is an invented
   number. The argument for: without a band, interpretations cannot be ranked or filtered, and the UI needs
   an ordering. This needs a product decision, not an engineering one.
4. **The V6 entailment tier is itself a hallucination surface.** It is tightly constrained (4-way
   classification, verbatim quoted-span check, defaults to `insufficient`), but it is a model judging
   evidence. A two-pass version with disagreement resolving to not-supporting would be stricter and roughly
   doubles V6's cost (+$2/run, affordable). Whether that is worth it depends on measured V6 error, which
   requires item-level human labels. Recommend shipping single-pass and measuring.
5. **Comment-derived evidence independence — now specified, but the cap is still a prior.** §6.2 gives
   comments the `independence_unit` `'thread:<parent content_item_id>'`, so one thread contributes at most
   `unit_cap = 3` however long it is. That is a deliberate middle position between the two wrong answers
   (fifty independent observations, or exactly one). Whether 3 is right for a 500-comment thread is
   unmeasured and shares a calibration path with item 1.
6. **Effect size for multi-category associations** (a theme spread over k > 2 brands) is under-specified.
   Cramér's V is the obvious candidate with `S_effect = min(1, V/0.5)`, but V's interpretation depends on
   table dimensions, so the normalisation constant should vary with `min(rows,cols)−1`. Left unspecified
   rather than guessed; such candidates currently fall back to pairwise `rate_comparison`.
7. **Four cosine thresholds are untested on this corpus:** the 0.92 dedup bar, the `[0.70, 0.92)`
   contradiction window, the 0.97 near-duplicate collapse (§6.2a) and the 0.30 Ask-the-Data similarity
   floor (§11.6c). All four should be set from a labelled set after the first run — claim pairs for the
   first two, known re-uploads for the third, and a set of in-corpus/out-of-corpus query phrases for the
   fourth. The 0.30 floor is the one with asymmetric cost: too high and the agent says "nothing like this"
   about things the corpus contains; too low and the bypass it exists to close reopens. Err high.
8. **`p < 0.10` for trends is a judgement call.** It is justified above as appropriate for an exploratory
   product with explicit labelling, but a marketer who reads "growing" will not read the p-value. An
   alternative is to drop the word "growing" entirely from `calculated` claims and only ever express
   direction as a `hypothesis`. That is more honest and less useful; it needs a product decision.
9. **Verbatim excerpt retention** (length, duration, redaction) has ToS and privacy dimensions that this
   design assumes are settled elsewhere. V6's quoted-span check and the UI highlight both require storing
   excerpts; if retention must shrink, V6 loses its substring verification and gets materially weaker.
10. **Whether `insufficient` findings should appear in the Overview at all.** §6.7 puts them in a drawer.
    The §28 requirement to "identify insufficient data" is satisfied either way; a drawer that nobody opens
    satisfies the letter and not the spirit. Needs a design decision in the UI subsystem.
11. **Cross-run finding continuity** is out of scope here and will be needed. `supersedes_id` supports it
    structurally, but "is this the same finding as last week, now with more evidence?" requires a stable
    cross-run `candidate_key`, which the miners would have to guarantee.
12. **Repost / near-duplicate detection still has no owner, and §6.2a is only a stopgap.** The interim
    embedding-cosine collapse (0.97 within 30 days, computed inside this subsystem) catches re-uploads of
    the same asset but will miss a repost that was re-cut, re-captioned or re-encoded enough to move the
    text embedding — and content embeddings are not the right signal for this; a perceptual hash over
    frames is. Until a sibling owns it, `n_eff` remains inflated by an unknown amount in exactly the
    direction that matters. `near_duplicate_groups_collapsed` is in the run report so at least the rate is
    visible.
13. **How to weight evidence found by a disconfirming query.** *(The floor is now normative — the `strong`
    gate in §6.6 requires ≥1 supporting item from a `disconfirming` query, which is what holds the
    Appendix B example at `moderate`. What remains open is the weighting.)* `01-research-loop` marks
    `research_queries.disconfirming`, which is a genuinely valuable signal: a pattern that survives a query
    written to disprove it is better evidenced than one found only by confirming queries. The proposal in
    §12.3 (require ≥1 disconfirming-query item for the `strong` band) is a floor, not a weighting. An actual
    weight — say counting disconfirming-query evidence at 1.5× — has no empirical basis yet and would be an
    invented constant, so it is deliberately not specified.
14. **Whether `labels.holdout_support = 0` should cap a finding at `indicative`.** The signal is the best
    available mechanical proxy for a self-fulfilled label (§12.3a), but the holdout arm is small by design,
    so `holdout_support = 0` will also fire on genuinely rare labels. The cap may be too blunt; an
    alternative is to surface it as a card annotation without a cap. Needs agreement with the taxonomy
    subsystem's owner.
15. **Tenancy is undecided** (§12.3). `research_runs` currently has `created_by` and no `org_id`, so the RLS
    policies in §10.2 cannot be written as given, and `ask_sessions.org_id not null` (§11.8) presumes an
    answer. Single-tenant MVP and multi-tenant produce different policy text and different index choices;
    this must be settled before the migration lands, and both tables changed together.
16. **Benjamini–Hochberg assumes the tests in a family are not strongly positively dependent, and ours
    are.** Candidates mined from overlapping population frames share items, so BH's guarantee is
    approximate here. Benjamini–Yekutieli would be valid under arbitrary dependence but costs a
    `ln(m)` factor (~3.4× at m=30) and would withhold nearly everything on an MVP corpus. BH plus honest
    rendering of `n_tests_in_family` is the chosen trade; the right long-run answer is a permutation null
    over the miners' own frame-selection procedure, which nobody has specified.
17. **Selection disclosure is weaker than selection correction, and the untested finding kinds get only
    disclosure.** `existence`, `association` and `share_of_presence` have no well-defined null, so §6.9(b)
    reports `selected_from_k` rather than adjusting anything. A reader who ignores the clause gets an
    uncorrected extreme. The alternative — a split-sample design where the miner selects on one half and
    the statistic is re-estimated on the held-out half — is the statistically correct answer and is
    unaffordable at MVP corpus sizes. Revisit when the corpus can support it.
18. **The §6.3a trend controls may make `trend` unpublishable in practice.** T1–T7 are each individually
    defensible, and their conjunction is strict: on a corpus collected in a single run over days, almost no
    candidate will clear T3 (28 days, two buckets of ≥10) and T4 (query vintage) together. That is the
    honest outcome, but it means BRIEF §22's trend classification will initially be delivered entirely
    through the Insufficient-data drawer and the `hypothesis` lane. Whether that is acceptable, or whether
    trend detection needs a different data strategy (repeated runs over weeks, with cross-run continuity
    per item 11), is a product decision this subsystem cannot make alone.
19. **`display_band` creates a second source of truth.** Three mechanisms now cap what is served without
    changing what is stored: the integrity breaker (§4.2), post-publication cross-finding contradiction
    (§7.5), and the hero fallback (§6.10). That is the right resolution of the immutability conflict, but
    it means a CSV export and a database query can legitimately disagree. Exports use `display_band`; any
    analytics built directly on `findings.confidence_band` will overstate. This needs to be documented
    wherever the table is consumed, and ideally enforced by only ever exposing the table through
    `v_findings_served`.
20. **The V18 subject-binding check depends on `subject_refs` being populated correctly by the stat
    registry.** V18 closes the label-swap hole only to the extent that the registry knows which entities
    each `recompute_key` is about. A registry entry that returns an empty or over-broad `subject_refs` makes
    V18 vacuous, silently. R14 catches the empty case at render time; the over-broad case needs a review of
    each registry entry when it is written, and a fixture test per `recompute_key`.

---

## Appendix A — Pipeline orchestration

```python
# aypo/intelligence/findings/pipeline.py

@dataclass(frozen=True)
class FindingSettings:
    max_repair_attempts: int = 1
    unit_cap: int = 3                       # was creator_cap; groups by independence_unit (§6.2)
    query_cap: int = 8
    n_target: int = 30
    min_citations_cap: int = 5
    support_share_bar: float = 0.80
    dedup_cosine: float = 0.92
    near_duplicate_cosine: float = 0.97     # §6.2a interim repost collapse
    near_duplicate_window_days: int = 30
    min_units_for_bca: int = 10             # below this, conservative cluster-t interval (§6.3)
    min_units_for_any_ci: int = 5           # below this, no interval is published at all
    contested_ratio: float = 0.15
    reframe_ratio: float = 0.40
    withhold_confidence: float = 0.35
    integrity_rate_alarm: float = 0.15
    breaker_min_denominator: int = 20       # §4.2: the breaker may not fire before this many candidates
    unknown_independence_cap: float = 0.30  # band cap when provenance is too weak to judge independence
    referee_kill_rate_alarm: float = 0.60
    min_verdict_confidence: float = 0.50
    trend_min_span_days: int = 28
    trend_min_bucket_n: int = 10
    trend_bucket: Literal["iso_week"] = "iso_week"
    trend_drop_trailing_bucket: bool = True
    trend_vintage_tau_max: float = 0.50     # §6.3a T4
    fdr_q: float = 0.10                     # §6.9 Benjamini-Hochberg, replaces a per-claim alpha
    hypothesis_strategy_cap: float = 0.70
    ask_min_similarity: float = 0.30        # §11.6c semantic-search floor

@dataclass(frozen=True)
class FindingBudget:
    max_candidates: int = 60
    max_usd: float = 30.0
    max_wall_clock_s: int = 1800

@dataclass
class RunFindingReport:
    run_id: UUID
    candidates_in: int
    declined: int
    dropped: dict[str, int]              # reason_code -> count
    quarantined: int
    integrity_quarantined: int
    integrity_rate: float
    referee_kills: int
    referee_kill_rate: float
    narrowed: int
    merged: int
    published: dict[str, int]            # band -> count
    insufficient: int
    usd_spent: float
    degraded_flags: list[str]            # 'generation_integrity_degraded', 'referee_miscalibrated'

async def generate_findings_for_run(
    run_id: UUID,
    *,
    session: AsyncSession,
    settings: FindingSettings = FindingSettings(),
    budget: FindingBudget = FindingBudget(),
) -> RunFindingReport: ...

async def process_candidate(
    candidate: StatCandidate,
    *,
    ctx: PipelineContext,
) -> CandidateOutcome: ...
```

Per-candidate flow, with every exit accounted for:

```
candidate -> S1 build packet (+persist counter-evidence)
          |- unregistered recompute_key / missing negation -> reject (not a quarantine; a contract bug)
          -> S2 draft
          |- decline -> status 'declined'
          -> S3 validate
          |- integrity failure -> repair x1 -> quarantine
          |- grammar/scope failure -> repair x1 -> demote x1 -> drop
          |- V4 mismatch -> rebuild packet x1 -> drop ('corpus_unstable')
          |- V16 duplicate -> merge into existing finding
          -> S5 referee
          |- fatal / 2+ serious / indeterminate -> status 'refuted' (+ quarantine row)
          |- 1 serious + narrower_claim -> narrow -> re-validate -> fresh referee -> survives or refuted
          -> S6 score
          |- provisional band 'strong' -> second referee -> harsher verdict governs
          -> S7 gate
          |- confidence < 0.35 or gates fail at every band -> status 'insufficient_evidence'
          -> status 'validating'  (NOT published yet)

after every candidate in the run has reached a provisional decision:

finalise_run(run_id)
          -> group test-bearing candidates into families (run_id, finding_kind)
          -> Benjamini-Hochberg at q = 0.10 within each family (§6.9)
          |- adjusted_q >= 0.10 -> status 'insufficient_evidence', withheld_reason='multiplicity'
          -> V19 subgroup-reversal recomputation; V20 selection clause
          -> materialise forced clauses into claim_rendered (sponsored disclosure, scope, selection)
          -> flip survivors to 'published' in ONE transaction (+ interpretation child, + hypothesis
             children, inheriting the parent's sample fields and caps per §10.1a)
          -> write the run report, including stopped_reason and the withheld counts
```

---

## Appendix B — Worked end-to-end example (fully generic)

**S1 packet** (abbreviated). `finding_kind='rate_comparison'`; frame = *analysed content items in Cluster Y*,
size 312, 2026-07-01..2026-09-02.

```
STATISTICS
 S1 | Share of Cluster Y items showing Brand A | "31%" | 95% CI [0.25, 0.39] | n=97 of 312 analysed items
    | n_eff 158 (deff 1.98) | ci_method wilson_deff | subject_refs ["Brand A","Cluster Y"]
    | formula: n_brand_a / n_frame | domain: corpus_composition | recompute_key: brand_share_in_cluster
 S2 | Share of Cluster Y items showing the subject brand | "11%" | 95% CI [0.07, 0.16] | n=34 of 312
    | n_eff 158 (deff 1.98) | ci_method wilson_deff | subject_refs ["<subject_brand>","Cluster Y"]
    | formula: n_subject / n_frame | domain: corpus_composition | recompute_key: brand_share_in_cluster
 S3 | Difference, arcsine effect size (Cohen's h) | "0.51" | 95% CI [0.32, 0.68] | n=312
    | ci_method cluster_bootstrap (23 units, >= min_units_for_bca)
    | formula: 2*asin(sqrt(p1)) - 2*asin(sqrt(p2)) | domain: corpus_composition
MULTIPLICITY
 family (run, rate_comparison) n_tests 14 | raw_p 0.004 | adjusted_q 0.028 | selected_from_k 6
SAMPLE STRUCTURE
 raw n 97 | effective n 49.0 | effective creators 15.6 of 23 | effective queries 7.5 of 9
 | platforms 2 | largest single creator share 0.11 | strategies {category: 58, theme: 31, brand: 8}
 | span 63 days across 9 weeks | week concentration 6.2 | sponsored share 0.09
 | unknown analysis confidence 0.12 | sources unavailable: ['Platform P3']
```

**S2 draft** (what the model returns — note: no digits anywhere):

```json
{
  "claim_type": "calculated",
  "claim_template": "Within the analysed sample, Brand A appears in {{stat:S1}} of items in Cluster Y, compared with {{stat:S2}} for the subject brand [E3][E11][E18][E26][E41][E57].",
  "context_template": "The pattern holds across both available platforms and is not concentrated in a single creator or query [E3][E11][E57]. A minority of items in the frame show the subject brand alongside Brand A rather than instead of it [E64][E71]. Platform P3 was unavailable during this run, so this frame does not cover it. This does not establish why either brand appears at this rate, and visibility here is not product usage.",
  "cited_stat_refs": ["S1", "S2"],
  "cited_evidence_refs": ["E3","E11","E18","E26","E41","E57","E64","E71"],
  "asserted_properties": ["brand_visible(Brand A)", "in_cluster(Cluster Y)"],
  "what_would_refute": "A larger sample of Cluster Y in which the two shares overlap within their confidence intervals."
}
```

Note what the intervals now say. A plain Wilson interval on S1 would be `[0.26, 0.36]`, computed as if the
312 items were independent — but the same packet reports 49 effective observations behind 97 raw supporting
items, a design effect near 2. Carrying that through widens S1 to `[0.25, 0.39]`. The narrow interval is the
one the reader believes, so publishing it would have been the most consequential small error in the
document.

**S3 validation.** V1/V2 pass. V3 passes (no digits; "A minority" is a `QUANTIFIER_LEXICON` hit — the
sentence contains no stat token, so **V3 fails** → one repair, which rewrites it as "Some items in the frame
show the subject brand alongside Brand A [E64][E71]", citing the contradicting items without quantifying
them). V5: 6 supporting citations ≥ `min(5, 97)` = 5. V6 tier A: `structured_sql` exists
(`brand_visible` is a structured field) so all cited items are checked for free; 6/6 `structural_pass`.
V7: "Within the analysed sample" present. V8: `calculated` grammar — no causal, no growth, no demographic.
V9: `Brand A`, `Cluster Y` both in vocabulary. V14: predicate class *presence/visibility*, stats are
`corpus_composition` → required domain satisfied; no stat is `is_cumulative`. V18: the sentence carrying
`{{stat:S1}}` names `Brand A`, which is in `S1.subject_refs`; the clause carrying `{{stat:S2}}` names the
subject brand, which is in `S2.subject_refs` — had the model swapped them, V18 would quarantine.
V19: recomputing the comparison split by platform keeps the sign in both partitions → no reversal.
V20: `selected_from_k = 6`, so the renderer appends "6 comparable slices were examined; this is the one
that stood out." At `finalise_run`, BH over the 14 rate-comparison candidates gives `adjusted_q = 0.028`,
which clears `fdr_q = 0.10`.

**S5 referee.** `confound`: *weak* — "sponsored share is low (0.09) and no single creator exceeds 0.11 of
the weight [E3][E57], so a single campaign is unlikely". `smaller_true_effect`: *weak* — "the CIs on S1 and
S2 do not overlap, but the effect CI's lower bound (0.32) is well below the point estimate; the difference
may be smaller than the headline suggests". `alternative_explanation`: *not_applicable*.
`insufficient_sample`: *not_applicable* — "49 effective observations from 15.6 effective creators".
`sampling_artefact`: *weak* — "58 of the supporting items came from category-strategy queries [E…]; the
pattern may partly reflect which category terms were searched". Overall `survives`,
`verdict_confidence = 0.81`. → `s_adv = 1.0 × 0.95³ = 0.857`.

**S6 scoring.** Creator capping over the 23 creators (top creator 11 items, `top_creator_share = 0.11`)
gives 57 capped units; query capping over the 9 queries (cap 8) gives **`n_eff = 49.0`** from `n = 97`.
`S_size = min(1, ln(50)/ln(31)) = 1.0` (saturated — 49 effective observations is well past `n_target = 30`).
`S_effect = min(1, 0.505/0.8) = 0.631`.
`d_creator = min(1, 15.6/8) = 1.0`; `d_query = min(1, 7.5/4) = 1.0`; `d_platform = 2/2 = 1.0` → `S_div = 1.0`.
`S_time = 0.5·min(1, 63/56) + 0.5·min(1, 6.2/4) = 0.5 + 0.5 = 1.0`.
`S_strategy = min(1, inv_simpson({58,31,8})/2.5) = min(1, 2.144/2.5) = 0.858`.
`S_item = 0.86`.
`base = 0.30(1.0) + 0.22(1.0) + 0.16(0.631) + 0.12(1.0) + 0.10(0.858) + 0.10(0.86)
      = 0.300 + 0.220 + 0.101 + 0.120 + 0.086 + 0.086 = 0.913`.
Counter-evidence, per §7.1a. `rate_comparison` is a **frame-level** kind, so its counter-evidence is a
`CounterAnalysis`, not a row set, and `ρ = 0`, `S_counter = 1.0`. The fourteen items an earlier version of
this example counted as "contradicting" were co-occurrences (items showing both brands) — related to the
claim but not its negation — and charging `ρ = 0.134` for them penalised the finding for evidence that was
not against it. They are now `confounded`/`illustrative`, still shown on the card and still shown to the
referee, and the competing-evidence pressure arrives through V19's reversal recomputation instead (which
passes here).
`s_adv = 1.0 × 0.95³ = 0.857`.
`confidence = 0.913 × 1.00 × 0.857 × 1.00 = 0.782` → numeric range `strong`.
Now the `strong` gates: `n_eff = 49 ≥ 20` ✓; `inv_simpson(independence_unit) = 15.6 ≥ 8` ✓;
`inv_simpson(query) = 7.5 ≥ 4` ✓; `inv_simpson(platform) = 2 ≥ 2` ✓; `adjusted_q = 0.028 < 0.10` ✓;
`unknown_confidence_share = 0.12 ≤ 0.40` ✓; hypothesis-strategy share 0 ✓; `adversarial_verdict`
`survives` ✓ — **but** the second-referee requirement applies (provisional band is `strong`), and the
`strong` gate also requires ≥1 supporting item found by a `disconfirming` query. In this example none of
the nine queries was marked disconfirming, so the finding **cascades down and is published as
`moderate`**, with `gates_failed = ['no_disconfirming_evidence']` rendered on the card.

Note what the machinery did, and did not do. On the additive terms alone this finding scores 0.913. The
referee's three unsubstantiated doubts take it to 0.782 — still `strong` numerically. What actually holds
it at `moderate` is a **gate**, not the score: nobody ever searched for evidence against it. That is the
right reason to withhold the top band, and it is a better reason than the `ρ = 0.134` an earlier version of
this example used, which was arithmetic applied to the wrong set.

**Interpretation child** (`claim_type='interpreted'`, `parent_finding_id` set, `G_type = 0.90` →
`confidence = 0.704`, capped to the parent's band `moderate` per §10.1a — an interpretation can never be
better evidenced than what it interprets, so the parent's `no_disconfirming_evidence` gate failure
cascades):

> *"One reading is that Cluster Y is more strongly associated with Brand A than with the subject brand
> within this sample. Reading of the finding above. No additional data supports this wording."*

**Hypothesis child** (no confidence, no band):

> *"Might the gap in Cluster Y also appear in marketplace listings for the same product theme?"*
> `what_would_confirm`: a commerce frame for the same theme in which Brand A's listing presence exceeds the
> subject brand's by a comparable margin. `follow_up_queries`: two marketplace queries + one social query,
> handed to the research loop.

---

## Appendix C — Every threshold in one table

| Constant | Value | Where | Why that number | At the boundary |
|---|---|---|---|---|
| `unit_cap` (was `creator_cap`) | 3 | `effective_sample_size` | 2nd/3rd item from one independence unit carry real new information; the 4th+ carry almost none about the population. Capping at 1 would erase genuine creator-led phenomena. Applies to `independence_unit`, not `creator_ref`, so commerce, review and comment evidence are covered too (§6.2). | 3rd item counts; 4th contributes 0 |
| `near_duplicate_cosine` | 0.97 / 30 days | §6.2a | Interim repost collapse using existing embeddings, until a sibling owns perceptual-hash detection (§13.12). Deliberately high: catch re-uploads, not similar content. | `>= 0.97` within 30 days → one unit |
| `unknown_independence_cap` | 0.30 | band cap | Above it the finding rests mostly on items whose source cannot be identified, so independence cannot be judged at all. | `> 0.30` → capped at `indicative` |
| `min_units_for_bca` / `min_units_for_any_ci` | 10 / 5 | §6.3 | BCa's acceleration estimate is unstable below ~10 clusters and silently produces intervals that are too narrow; below 5 no interval should be shown at all. | `<10` → conservative cluster-t + `moderate` cap; `<5` → withheld |
| `fdr_q` | 0.10 | §6.9 | Benjamini–Hochberg within `(run, finding_kind)`. Replaces a per-claim alpha, which over ~60 mined candidates manufactures ~6 findings from noise. | `adjusted_q >= 0.10` → `insufficient_evidence` |
| `trend_vintage_tau_max` | 0.50 | §6.3a T4 | Tests whether later buckets are populated by later-issued queries — i.e. whether the "trend" is our own search schedule. | `>= 0.50` → `retrieval_recency_artefact` |
| `breaker_min_denominator` | 20 | §4.2 | Below 20 candidates the integrity rate is noise; a breaker that fires every run is a breaker nobody reads. | `< 20` → breaker cannot fire |
| `ask_min_similarity` | 0.30 | §11.6c | Nearest-neighbour search always returns something; without a floor the agent can cite 25 plausible rows for a concept the corpus does not contain. **Prior, needs calibration (§13.7).** | nothing above the floor → `no_match_above_threshold` |
| `query_cap` | 8 | `effective_sample_size` | One query returns one recommender's ranked slice; ~8 is where a single query stops adding new creators. **Prior, needs calibration (§13.1).** | 9th capped unit contributes 0 |
| `n_target` | 30 | `S_size` | 30 independent observations give a ±0.18 Wilson half-width at p≈0.5 — exactly the strength of claim this product may make. Attainability: 30 **effective** observations is roughly 45–90 raw items at expected design effects, i.e. 3–6% of a 1,500-video corpus — not the 2% an earlier draft claimed by equating `n` with `n_eff`. | `n_eff ≥ 30` → `S_size = 1.0` |
| `S_div` creator target | 8 | `d_creator` | Below ~8 voices you cannot separate a category behaviour from a few accounts' content habit. | ≥8 → full credit |
| `S_div` query target | 4 | `d_query` | Smallest set that can hold brand + category + theme + one more differently-motivated query. | ≥4 → full credit |
| `S_div` platform target | 2 | `d_platform` | Deliberately low: with `APIFY_TOKEN`/`YOUTUBE_API_KEY` missing a run may have two sources, and permanently withholding every finding is worse than labelling concentration. | ≥2 → full credit; 1 platform cannot reach `strong` |
| `S_time` span target | 56 days | `S_time` | Two monthly content cycles — the shortest span that distinguishes a pattern from a fortnight. | ≥56 → full credit on the span half |
| `S_time` week target | 4 | `S_time` | Stops "two items eight weeks apart" scoring as spread. | ≥4 effective weeks → full credit |
| `S_strategy` target | 2.5 | `S_strategy` | Requires a pattern to survive being approached from ~2–3 of the five §8 strategies. 2.5 not 3, so a run whose competitor discovery has not fired is not penalised. | ≥2.5 → full credit |
| hypothesis-test share cap | 0.70 | band cap | Above it the pattern was mostly found by queries written to look for it — the §1/§34 self-fulfilment risk. | >0.70 → capped at `moderate` |
| `unknown_confidence_share` cap | 0.40 | band cap | Above it the finding rests mostly on items of unknown analysis quality. | >0.40 → capped at `moderate` |
| additive weights | .30/.22/.16/.12/.10/.10 | `base` | Size and diversity dominate because insufficient independent evidence is the most common failure; effect, time, strategy and item quality shade. Sum = 1.00. | — |
| `G_type` interpreted | 0.90 | `confidence` | An interpretation inherits its parent's evidence and adds model judgement, which can only subtract. **Flagged as an invented constant (§13.3).** | — |
| `ρ` zero point | 0.50 | `S_counter` | At equal effective weight both ways, the directional claim is unsupported. | `ρ ≥ 0.50` → `S_counter = 0` |
| `contested_ratio` | 0.15 | `contested` | ~1 effective observation in 7 against is enough that a marketer must see it. Aligns with the `strong` gate for ρ-driven and referee-driven contesting. It does **not** cover §7.5 cross-finding contradiction, which is discovered after publication and is handled by a render-time `display_band` cap instead. | `= 0.15` → contested |
| `reframe_ratio` | 0.40 | auto-reframe | Catches a directional claim before the split is a coin flip. | `= 0.40` → reframe to co-existence |
| band `strong` | ≥0.70 + 8 gates | `score_confidence` | The band a marketer quotes without reading the drill-down; must have survived two independent referees. | `= 0.70` → `strong` iff gates pass, else cascade down |
| band `moderate` | 0.50–0.6999 | `score_confidence` | — | — |
| band `indicative` | 0.35–0.4999 | `score_confidence` | — | — |
| `withhold_confidence` | 0.35 | band `insufficient` | 0.35 buys ~`n_eff = 5` with weak diversity — "five loosely independent items", which §28 forbids presenting as a finding. | `= 0.35` → `indicative` (resolves **up**) iff its gates pass |
| `min_citations` | `min(5, n)` | V5 | Five exemplars is the minimum for a persuasive drill-down, and matches the `indicative` `n_eff` floor so a finding never has fewer exemplars than its own floor. | `n < 5` → already withheld by band |
| `support_share_bar` | 0.80 | V6 | Tolerates exactly one bad exemplar in five (repairable by stripping); two means the claim is wrong, not the citation. | `= 0.80` → keep, strip non-supporting citations, re-run V5 |
| V6 contradicting among cited | ≥1 | V6 | Citing an item that says the opposite is fabrication-shaped. | ≥1 → **quarantine** |
| sponsored disclosure | 0.20 | V11(b) | One in five sponsored makes a read of "organic behaviour" materially wrong. | >0.20 → forced disclosure clause |
| sponsored demotion | 0.50 | V11(c) | A sponsored majority is a media-buy artefact, not consumer behaviour. | >0.50 → demote to `interpreted` |
| trend min span | 28 days | V13 | Four weeks = two weekly content cycles; below it weekday and single-campaign effects dominate. | <28 → `insufficient_evidence` |
| trend min bucket n | 10 | V13 | Below 10 a bucket proportion's Wilson half-width exceeds ±0.30, so any "change" is noise. | <10 in any bucket → fail |
| trend alpha | `adjusted_q < 0.10` | V13 + §6.9 | Exploratory product, direction labelled as a direction to investigate and both `raw_p` and `adjusted_q` rendered — but applied **after** BH correction across the run's trend family, never per-claim. **Still a judgement call (§13.8).** | `q = 0.10` exactly → fail (strict `<`) |
| trend bucket | ISO week, trailing dropped | §6.3a T2 | A half-finished week always reads as a collapse or a spike. | partial trailing bucket excluded |
| trend series shape | within-bucket **rate** | §6.3a T1 | Raw per-bucket counts are dominated by platform recency bias and content attrition, both of which cancel to first order in a rate with a same-bucket denominator. | count series → `drop` |
| absence power | `frame_n_eff ≥ 30` | V15(a) | At 30 independent observations with zero occurrences the 95% upper bound is ≈0.095 — "under one in ten if it happens at all", which is actionable. Computed on the **frame** (§6.2b): an absence claim has no supporting items, so `effective_sample_size` is 0 by construction. | <30 → `insufficient_evidence` |
| absence probe | ≥1 capable query | V15(b) | The condition sample size cannot buy: absence in a frame that was never probed for the thing is a fact about our query set. | no capable query → `absence_unprobed` |
| dedup cosine | 0.92 | V16 | Above it, paraphrases; below it, genuinely different slices. **Prior (§13.7).** | `= 0.92` → merge |
| contradiction window | [0.70, 0.92) | §7.5 | Same subject, not a duplicate. **Prior (§13.7).** | — |
| `max_repair_attempts` | 1 | S4 | A second repair converges to restating the defect and costs a full packet. One failure to satisfy a mechanical rule signals the packet does not support the claim. | 2nd failure → drop/quarantine |
| `min_verdict_confidence` | 0.50 | §5.4 rule 1 | Below coin-flip self-confidence the referee's verdict carries no information, so the unverified state stands — and unverified means unpublished. | <0.50 → refuted |
| referee serious-kill count | 2 | §5.4 rule 3 | One serious objection is usually a scope problem narrowing fixes; two means the claim is wrong in more than one way. | ≥2 → refuted |
| narrowing attempts | 1 | §5.4 rule 4 | — | 2nd trigger → refuted |
| `integrity_rate_alarm` | 0.15 | circuit breaker | ~9 integrity rejections in 60 candidates. Below: ordinary model noise the validators exist to absorb. Above: a systemic packet/prompt/model defect, and publishing the survivors would publish a biased subset. | `= 0.15` → degraded (inclusive) |
| `referee_kill_rate_alarm` | 0.60 / 0.05 | §5.4 monitor | Above 0.60 the referee is probably manufacturing objections; below 0.05 it is probably rubber-stamping. Either way: human review, never auto-adjust. | flags only; kills stand |
| Ask: `n < 10`, `n_eff < 5`, eff. creators `< 3` | — | §11.5 | The `indicative` band floors. Ask the Data must never assert more confidently than the pipeline would publish, or the chat box becomes a bypass around verification. | any hard reason → insufficiency template |
| Ask: `min_group_n` | 5 | `aggregate` | A group of 3 yields a CI spanning most of the range, and a model shown "100%" on n=3 will report it. | `n < 5` → `suppressed=True`, value withheld |
| Ask: `grounding_ratio` | 0.90 | A4 | Allows a small number of connective/framing sentences without citations while making an uncited assertion the exception. | <0.90 → regenerate once → insufficiency template |
| Ask: `max_tool_turns` | 8 | agent loop | Bounds cost at ~$0.20/question; 8 turns covers coverage + presupposition tests + symmetric sides + exemplar hydration. | hit → insufficiency template (`tool_budget_exhausted`), never a partial answer |

---

## Review notes (adversarial review, 2026-10-02)

This section records what an adversarial review changed and what it deliberately left standing. It is part
of the design, not a changelog appendix: the reasoning for a removed mechanism is as load-bearing as the
reasoning for a kept one.

### What survived unchanged, and why

The core architecture is sound and most of it was not touched. Specifically: numbers never originating in
the model (schema with no numeric fields + V3 digit ban + a non-countable packet) is a genuinely
three-layer defence; the four claim types as separate rows with DB CHECKs rather than as styling; the
adversarial referee as an independent pass that defaults to refuted on any non-answer; counter-evidence
written to the database *before* the generator runs; render-time re-validation as the last line; and the
Ask-the-Data allowlist design where `ask_tool_calls.returned_ids` / `returned_stats` are written by the
tool layer, which converts "you may not cite an id you were not given" from an instruction into a
checkable property. The threshold justifications are unusually honest and most boundary cases were already
specified. Fifteen open questions were already flagged and were, on verification, real.

### Fatal — fixed

1. **Trend claims measured the collection process, not the world (§6.3a, V13).** The corpus is a
   single-snapshot retrieval from recency-biased platforms, assembled using queries generated *during* the
   run. Mann-Kendall over per-bucket item counts therefore measures platform recency bias, query vintage,
   content attrition and engagement accumulation before it measures any change in the category — and every
   one of those biases points the same way, upward. The old V13 gated on span, bucket size and a p-value,
   none of which touch the artefact. Replaced with seven conditions: rate series not count series, ISO
   weeks with the trailing partial bucket dropped, a query-vintage Kendall test, a ban on cumulative
   counters, blocked Mann-Kendall, leave-one-unit-out stability, and a multiplicity-adjusted threshold.
2. **No multiple-comparisons control anywhere (§6.9).** ~60 mined candidates × a per-claim `alpha = 0.10`
   manufactures roughly six findings from noise, and the referee structurally cannot catch it — it sees one
   finding at a time and is instructed to use only the evidence in front of it. Added Benjamini–Hochberg
   within `(run, finding_kind)` as a run-level gate, which makes publication a two-phase commit
   (`finalise_run`), plus `selected_from_k` disclosure for the kinds that carry no test, plus the selection
   context in the referee prompt.
3. **`absence` findings were simultaneously specified and structurally unpublishable, and their gate was
   the wrong gate (V15, §10.1).** `ck_published_has_sample` requires `sample_size > 0`; an absence claim
   has zero supporting items by construction. V15 gated on "`population_frame` `n_eff`", a quantity that
   did not exist (`n_eff` is defined over supporting items). And the deeper problem: sample size cannot
   establish absence if nothing we searched could have surfaced the thing. Added frame-level `n_eff`
   (§6.2b), fixed the CHECK, and added a **retrievability probe** requirement.

### Serious — fixed

4. **`creator_ref` null handling was undefined, with a dangerous default (§6.2).** No sibling design
   defines a `creators` table (verified). If unresolvable creators each formed their own group, unknown-
   provenance items would read as maximally independent and inflate both `n_eff` and `inv_simpson` — the
   protection failing exactly where provenance is weakest. Replaced `creator_ref` with a never-null
   `independence_unit`; unknowns collapse into one shared capped group and are surfaced as
   `unknown_independence_share`.
5. **Commerce evidence had no independence key at all (§6.2, §11.5).** Forty reviews of one listing either
   counted as forty observations or made every commerce finding fail a creator gate — the document did not
   say which, and both are wrong. Worse, `is_sufficient()` gated Ask-the-Data on `effective_creators < 3`,
   which means the chat box was structurally unable to answer any commerce or social-to-commerce question:
   BRIEF §17's differentiating capability, silently disabled by a floor. Independence units are now
   per-corpus (creator / seller / listing / thread).
6. **Proportion CIs ignored the clustering the rest of the design exists to model (§6.3).** §9.1 mandated
   plain Wilson while the same card reports `n_eff` far below `n`. The narrow interval is the one a reader
   believes. Now `wilson_deff` (DDL-enforced), cluster-resampled bootstrap for effect sizes with a minimum
   cluster count for BCa, and no interval published below five independence units. (The document's own
   worked example already used a design-effect-corrected interval while the normative text said Wilson —
   the fix makes the text agree with the example.)
7. **`ρ` was not label-invariant, and item-level negation is incoherent for frame-level claims (§6.4,
   §7.1a).** Negating a claim maps `ρ → 1 − ρ`, so phrasing alone decided whether a candidate published or
   was auto-reframed. And `rate_comparison` / `trend` / `social_commerce_link` have no item that "asserts
   the opposite" — the old spec demanded one anyway and fed the resulting unrelated set into `S_counter`.
   Appendix B was the live casualty: fourteen co-occurrence items cost it a full band. Fixed with a
   canonical orientation imposed by code and a `CounterAnalysis` shape for frame-level kinds.
8. **The model chose what V6 verified (§3.5, §4.4).** V6 ran against `draft.asserted_properties`, which the
   generator writes freely. A model declaring a trivially-true property while writing a strong claim passed
   V6 with every citation intact — defeating the one check described as doing the real work. V6 now runs
   against `packet.property_predicate` unconditionally.
9. **Nothing bound a number to the entity it was about (V18).** V9 proves the entity string is real, V14
   proves the stat's domain fits — but "Brand B appears in `{{stat:S1}}`" where S1 is Brand A's share
   passed every check and published a true number under a false name. Added `ComputedStat.subject_refs`
   (registry-populated) and V18.
10. **`research_queries.query_text` was a vocabulary source (§3.3).** A hypothesis query is a sentence we
    wrote to go looking for something; admitting its words to the allowed vocabulary let the generator name
    a concept that exists only in our search box. This was a direct §1/§34 hole inside the mechanism built
    to enforce §1/§34. Removed.
11. **Semantic search bypassed label resolution (§11.6c).** `UnresolvedLabel` guards structured filters,
    but `search_content(query=...)` takes free text and pgvector always returns its k nearest rows. The
    claim that the agent "physically cannot search for a concept the data does not contain" was false.
    Added a similarity floor, `no_match_above_threshold`, `resolved_to` echo, and validator A12.
12. **The leading-question guard rested on a model-produced list (§11.6).** The enforcement loop is
    mechanical *given* `presuppositions` and `comparative`, but both come from the auditor model, and its
    failure mode is permissive. Added a code-side floor: resolved labels are forced into presuppositions,
    `comparative` can be turned on but never off by the model, and base rates are computed unconditionally.
13. **`usage_type` did not exist upstream (§12.2).** The real field is `consumption_state`, a seven-rung
    ordinal, and `03-multimodal-analysis` states explicitly that `shown_to_camera` is "not by itself use".
    The four-value enum had nowhere correct to put handling, so V11(a) would have miscounted presentation
    as active use — breaking BRIEF §28 item 4 at the first hop. Added the mapping and a `handled` value.
14. **Three impossible writes (§4.2, §7.5, §10.2, R11).** The integrity breaker capped bands on published
    rows the immutability trigger forbids changing; cross-finding contradiction set `contested` on a
    published `strong` row, violating `ck_contested_capped` and then the trigger when the band was lowered
    to satisfy it; and V11(b) told the renderer to inject a disclosure clause that R11 blocks the render
    for lacking. Resolved with `display_band` (served, capped) alongside `confidence_band` (stored, audit),
    a `contested_reason` exemption, and materialising forced clauses at S7 before publication.
15. **`v_finding_provenance` bypassed RLS.** A Postgres view runs as its owner unless declared otherwise.
    Added `security_invoker = true`.
16. **Cumulative platform counters could back a purchase claim (V14, §9.2).** `sold_count_sum` is a
    lifetime counter on listings our own queries retrieved — confounded by listing age, marketplace ranking
    and query selection at once. Now never sufficient alone and rejected outright inside `trend`.
17. **Multiple comparisons on the chat surface (§11.2, A13).** `aggregate` returns up to 40 groups and the
    agent will quote the extreme one. Added `selected_from_k` on every row, forced disclosure, and
    suppression of exemplars for suppressed groups (returning three exemplars for an `n=3` group undoes
    the suppression it is paired with).
18. **Hypothesis and interpreted children could not satisfy the publish CHECKs (§10.1a).** Neither has a
    sample of its own and `ck_published_band` only passed by accident of three-valued logic. Specified
    inheritance, including that a parent's caps and gate failures cascade to the child.
19. **The hero area will usually be empty, silently (§6.10).** At a realistic `S_adv ≈ 0.857` (the
    document's own worked example collects three `weak` caveats), `strong` requires a near-perfect base and
    essentially zero counter-evidence. A blank "most important findings" panel is an unlabelled claim that
    nothing is happening. Added a labelled fallback; the gates themselves are unchanged.
20. **Seeding violations in the guardrails (§0, §2.3).** `POPULATION_LEXICON` contained `filipinos`,
    `CAUSAL_LEXICON` contained `dahil`/`sanhi`, `DEMOGRAPHIC_LEXICON` contained the Philippine `class a`/
    `class c` SEC labels, and `ComputedStat.unit` hard-coded `currency_php` — while §0 claimed no lexicon
    named a market assumption. These are guardrail terms rather than seeded findings, so the behavioural
    harm is low, but the document asserted a compliance it did not have and market #2 would require a code
    change. Moved to a `MarketLocalePack` keyed by `research_runs.market`, with a CI test that a pack
    contains no category, brand, occasion or behaviour term.

### Minor — fixed inline

`evidence_design` was read by V12 but absent from `EvidencePacket` (dangling attribute); the referee was
described as `messages.create` while returning a Pydantic model; `Refutation.overall` existed as a model
verdict in a decision the text calls purely deterministic (now explicitly advisory, and rule 4 no longer
reads it); the integrity breaker said "exceeds" in prose and "inclusive" in Appendix C, and could fire on
a denominator of three; `0.342^0.45` was 0.624 rather than 0.617; the `n_target` attainability argument
equated `n` with `n_eff` and so understated the required corpus share by about half; `evidence_packets`'
unique key blocked the V4 rebuild it mandates; budget exhaustion had no specified behaviour; the
immutability trigger left `context_rendered`, `scope_statement` and the effect fields mutable after
publication; cache-write cost was omitted from §8 and the referee's `effort="high"` output budget is
optimistic; migration ordering against `research_strategy` was unstated; and the generator had no stated
handling for a `refusal` stop reason (now `declined`, explicitly *not* retried on the fallback model).

### Residual risk — not fixed, and why

- **The trend controls may make `trend` effectively unpublishable on an MVP corpus** (§13.18). Each
  condition is individually justified; their conjunction is strict. This is the honest reading of BRIEF
  §22 but it means trend classification initially arrives only through the drawer and the hypothesis lane.
- **BH assumes weak dependence; our candidates share items** (§13.16). The correction is an improvement
  over nothing, not a guarantee, and the valid-under-dependence alternative would withhold nearly
  everything at MVP scale.
- **Selection disclosure is not selection correction** (§13.17). For `existence` / `association` /
  `share_of_presence` there is no null to correct against, so the reader is told the denominator and must
  do the discounting.
- **The near-duplicate collapse is a stopgap using the wrong signal** (§13.12). Text embeddings will miss a
  re-cut repost; a perceptual hash is the right instrument and still has no owner. `n_eff` remains
  inflated by an unknown amount.
- **V6's entailment tier, `verdict_confidence`, and the question auditor remain model judgements.** All
  three are constrained to fail safe (default `insufficient`, kill-only floor, code-side floor), so their
  failure mode is lost protection rather than false publication — but none has measured error.
- **Referee and cap calibration still have no ground truth** (§13.1, §13.2). Unchanged by this review.
- **`display_band` creates two legitimate answers to "what band is this finding?"** (§13.19). The right
  resolution of the immutability conflict, and a documentation hazard.
- **Tenancy remains undecided** (§13.15) and now blocks two tables rather than one.
- **The thresholds assume a corpus the current credentials cannot collect** (§8). Deliberately not
  relaxed: the correct response to a small corpus is a full Insufficient-data drawer and a labelled
  Overview, not a lower bar.
