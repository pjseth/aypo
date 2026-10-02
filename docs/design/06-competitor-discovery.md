# 06 — Open-World Brand Entity Resolution & Multi-Signal Competitor Discovery

Normative source: `docs/BRIEF.md` §23 (competitor discovery), §24 (competitive analysis),
with binding constraints from §1 (discovery not assumption), §3 (signal/context/evidence),
§7 (graceful degradation), §27 (evidence system), §28 (guardrails), §34 (no seeded findings).

**§1 / §34 compliance statement for this subsystem.** Nothing in this design — no table,
column default, enum member, threshold, lexicon, prompt, fixture or UI string — names a real
brand, a product category, a consumption occasion, a cultural reference, a slang term or a
competitor. The only shipped lexicons are *structural* (morphological/syntactic comparative
and coordinating patterns, legal-entity suffixes, measurement units, marketplace listing
boilerplate, character-confusion classes). Every *lexical* cue the system uses to detect
substitution, co-consumption or product descriptors is **discovered at runtime from the
corpus and persisted into a bootstrapped table that ships empty**. All examples below use
`Brand A`, `Brand B`, `Theme X`, `Cluster Y`, `Label Z`.

---

## 0. Scope, ownership, non-goals

### 0.1 What this subsystem does

1. Turns free-text brand surface forms arriving from three noisy channels into stable brand
   entities in an **open world** (no fixed brand list, no closed vocabulary).
2. Runs eleven independent competitor-signal detectors over the analysed corpus.
3. Aggregates signal evidence into a **per-relationship confidence** that is explicitly not
   a competitor score.
4. Types each relationship, including the substitution-vs-co-consumption discrimination.
5. Maintains relationship lifecycle (first/last detected, decay, archival).
6. Handles optional user-supplied seeds as a recall hint with zero scoring privilege.
7. Produces the §24 comparison computations, scoped to the sample, through a closed set of
   phrasing templates.

### 0.2 Tables this subsystem owns (defines DDL for)

`brands`, `brand_aliases`, `brand_surface_observations`, `brand_alias_detachments`,
`brand_review_items`, `brand_seeds`, `substitution_cues`, `descriptor_tokens`,
`competitor_relationships`, `competitor_signal_evidence`, `competitor_signal_scores`,
`competitor_relationship_events`, `competitor_pair_enumeration`.

### 0.3 Tables it reads but does not own

`content_items`, `content_analysis` (incl. frame-level brand detections), `content_metadata`,
`content_embeddings`, `creators`, `commerce_items`, `commerce_reviews`, `clusters`,
`cluster_members`, `research_runs`, `research_queries`, `research_results`.

### 0.4 Non-goals

- No SKU/product catalogue. `brands` is an **identity registry**; `products` and
  `commerce_items` are owned elsewhere and carry a `brand_id` FK into this registry.
- No corporate-ownership ground truth. Parent-company structure is only asserted when
  supplied or human-confirmed (see §1.6, open question OQ-6).
- No market sizing, share-of-market, awareness or sales inference. The sample cannot support
  it and §28 forbids pretending otherwise.
- No "overall competitor score" of any kind (§24). See §3.6 for the enforcement mechanism.

### 0.5 Modules

```
apps/api/aypo/intelligence/brand_resolution/
  __init__.py  normalize.py  confusion.py  blocking.py  cascade.py
  registry.py  hierarchy.py  adjudicate.py  reversibility.py  config.py
apps/api/aypo/intelligence/competitors/
  __init__.py  base.py  context.py  aggregate.py  typing.py  discriminate.py
  decay.py  seeds.py  enumerate.py  comparisons.py  phrasing.py  service.py
  signals/
    __init__.py  s01_direct_comparison.py  s02_co_appearance.py  s03_shared_cluster.py
    s04_category_search.py  s05_marketplace_adjacency.py  s06_recurring_mention.py
    s07_substitution_language.py  s08_ranking_review.py  s09_positioning.py
    s10_shared_creator.py  s11_shared_cultural.py
apps/api/aypo/models/brand.py
apps/api/aypo/models/competitor.py
apps/api/aypo/llm/prompts/brand_adjudication.py
apps/api/aypo/llm/prompts/comparison_classify.py
apps/api/aypo/llm/prompts/relation_spans.py
apps/api/aypo/jobs/competitor_discovery.py
apps/api/aypo/api/routes/brands.py
apps/api/aypo/api/routes/competitors.py
```

### 0.6 Pipeline position and idempotency

```
analysis complete for run R
   → stage resolve_brands      (surfaces → brands; writes brand_surface_observations)
   → stage enumerate_pairs     (candidate pair set, capped and logged)
   → stage detect_signals      (11 detectors, parallel; writes competitor_signal_evidence)
   → stage aggregate           (per-pair confidence; writes competitor_relationships)
   → stage type                (relationship_type per pair)
   → stage decay               (lifecycle state machine)
   → stage comparisons         (§24 computations, cached for UI)
```

`aggregate` and `type` are **one transaction in two phases**, and the ordering matters because
of a genuine cycle: `type` needs the pre-cap `confidence` (R4 and R5 gate on `>= 0.35`), while
`confidence` needs the typing cap (§4.3) and `Q_contradiction` needs the substitution /
co-consumption split that typing computes. The resolution is fixed and must be implemented as
written:

1. `aggregate` computes `S`, `V`, `D` and `Q` and holds a `PairAggregate` **in memory**,
   with `Q_contradiction` computed directly from the S7 span verdicts (minority-relation item
   share), which does not require typing to have run. Nothing is written to
   `competitor_relationships` yet. `competitor_signal_scores` *is* written here.
2. `type` consumes the uncapped `confidence` from that `PairAggregate`, emits one
   `TypingDecision` per firing rule, applies each decision's cap, and performs **all**
   `competitor_relationships` upserts (the statement in §5.3 therefore belongs to the `type`
   stage, not to `aggregate`, and `type_evidence_count` is written there too).

Every stage is otherwise a pure function of (stored corpus, run id) plus the existing registry.
All writes are `INSERT ... ON CONFLICT DO UPDATE` against the unique indexes defined below, so
re-running a stage is idempotent. Stage ordering and single-flight are enforced with
`pg_advisory_xact_lock(hashtextextended('competitor:'||:run_id||':'||:stage, 0))` in the
Postgres-backed job queue (no Redis, no Celery).

---

## 1. OPEN-WORLD BRAND ENTITY RESOLUTION

### 1.0 Design principles (the ones that drive every threshold below)

**P1 — An unresolved surface is cheap; a wrong merge is catastrophic.** An unresolved surface
is visible in the UI, costs one missed observation, and is re-evaluated every run. A wrong
merge silently fuses two brands' entire evidence bodies, corrupts every competitor signal
that touches either, and is near-undetectable downstream. Every ambiguous case therefore
resolves *toward* "unresolved".

**P2 — Resolution is reversible.** The unit of record is the **observation**
(`brand_surface_observations`), not the item. A content item is never stamped with a brand id;
downstream code reads brands through the observation ledger. Re-pointing a mis-resolved alias
is an `UPDATE` plus a recompute, not a data-loss event.

**P3 — Recall mechanisms and merge authority are separate.** Embeddings, phonetic keys and
OCR skeletons are *candidate generators only*. None of them may authorise a merge on its own.
This is deliberate: in a single product domain, `text-embedding-3-large` cosine between two
genuinely different brand names routinely exceeds 0.95 because it encodes category semantics,
not identity.

**P4 — Noise never clusters onto noise.** Fuzzy auto-resolution is permitted only against a
`confirmed` brand. Two `provisional` entities may never be fuzzy-merged automatically.

### 1.1 Input contract: the three channels

The analysis subsystem must emit **raw surface strings, never brand ids** (cross-subsystem
requirement XS-2). One row per observed surface:

| channel | source | typical corruption |
|---|---|---|
| `packaging_vision` | vision model reading packaging off sampled frames | character confusion, partial occlusion, truncation, case noise, split/merged tokens, stylised glyphs |
| `spoken` | whisper-1 transcript span | phonetic spelling, code-switch orthography, elision, pluralisation |
| `written` | caption, on-screen text overlay, hashtag, comment | hashtag concatenation, leetspeak, emoji infix, deliberate misspelling to evade platform filters |
| `commerce_title` | marketplace listing title | descriptor flood, pack/size tokens, seller boilerplate, multiple brands in one string |
| `commerce_brand_field` | marketplace structured brand field | high precision, low coverage, sometimes the seller name |
| `web_text` | Serper result title/snippet/body | third-party, possibly off-market |

```python
# apps/api/aypo/models/brand.py
class AliasChannel(StrEnum):
    PACKAGING_VISION = "packaging_vision"
    SPOKEN = "spoken"
    WRITTEN = "written"
    COMMERCE_TITLE = "commerce_title"
    COMMERCE_BRAND_FIELD = "commerce_brand_field"
    WEB_TEXT = "web_text"
    SEED = "seed"
    MANUAL = "manual"

@dataclass(frozen=True, slots=True)
class SurfaceObservation:
    research_run_id: UUID
    source_kind: Literal["content_item", "commerce_item", "commerce_review", "web_document"]
    source_id: UUID
    channel: AliasChannel
    surface_raw: str
    extractor_confidence: float | None      # vision/LLM self-reported, [0,1]
    context_snippet: str | None             # <= 240 chars around the surface
    frame_ts_ms: int | None                 # packaging_vision only
    transcript_span: tuple[int, int] | None # (start_ms, end_ms), spoken only
    char_span: tuple[int, int] | None       # commerce_title / written
```

Channel trust weights, used as a prior in the cascade and as `weight` on downstream evidence:

```python
CHANNEL_PRIOR: Final[Mapping[AliasChannel, float]] = {
    AliasChannel.COMMERCE_BRAND_FIELD: 1.00,  # structured field, platform-validated
    AliasChannel.WRITTEN:              0.90,  # digital text, no OCR/ASR loss
    AliasChannel.COMMERCE_TITLE:       0.90,
    AliasChannel.PACKAGING_VISION:     0.75,  # real OCR loss, but *is* physical presence
    AliasChannel.SPOKEN:               0.70,  # ASR loss + code-switching
    AliasChannel.WEB_TEXT:             0.60,  # third party, locale-uncertain
    AliasChannel.SEED:                 0.50,  # user assertion, unverified by data
    AliasChannel.MANUAL:               1.00,
}
```
`SEED` at 0.50 is the mechanism by which a seed helps recall without carrying authority
(§6). It is *below* every data channel, so a seed string never out-votes an observation.

### 1.2 Normalisation

Two distinct normal forms, for two distinct purposes. Conflating them is the classic bug.

**`norm` — the canonical identity key** (used for exact match and uniqueness). Lossless enough
that two different brands keep different `norm` values.

```python
# apps/api/aypo/intelligence/brand_resolution/normalize.py
@dataclass(frozen=True, slots=True)
class NormalForm:
    raw: str
    norm: str                      # canonical identity key
    tokens: tuple[str, ...]
    core_tokens: tuple[str, ...]   # tokens minus runtime-discovered descriptors
    skeleton: str                  # OCR-collapsed blocking key (NOT an identity key)
    consonant_skeleton: str        # vowel-dropped blocking key
    phonetic: str | None           # double-metaphone primary code
    token_signature: str           # sorted unique core_tokens, '|'-joined
    stripped: tuple[str, ...]      # what normalisation removed, for audit
    segmented_from_hashtag: bool

def normalize_surface(raw: str, *, channel: AliasChannel,
                      locale: str) -> NormalForm: ...
# `locale` is REQUIRED and comes from the research run's configuration. It selects the
# phonetic confusion table only. It carries no default: a default market in a function
# signature is a market assumption living in code rather than in configuration, which is
# the shape of thing §1/§34 asks this subsystem not to accumulate.
```

`norm` pipeline, in order:

1. Unicode **NFKC**; strip zero-width (`U+200B-200F`, `U+FEFF`), variation selectors, emoji
   and symbol code points (`So`, `Sk`).
2. Strip combining marks: NFD → drop `Mn` → NFC. (Diacritics are rendered inconsistently by
   both OCR and users; they are never the sole discriminator between two brands in practice,
   and keeping them fragments one brand across accented/unaccented spellings.)
3. **Homoglyph fold**: map confusable non-Latin code points to their Latin skeleton using the
   Unicode confusables table (Cyrillic `а/е/о/с/р`, Greek `Α/Ε/Ο`, fullwidth forms). This is
   folded into `norm` because a homoglyph is never a deliberate brand distinction — it is
   either a rendering artefact or filter evasion.
4. Casefold (`str.casefold()`, not `lower()`, for correct non-ASCII behaviour).
5. **Hashtag/concatenation segmentation** (channel `WRITTEN` only, when the surface has no
   whitespace and length ≥ 8): split on camelCase and digit boundaries, then run max-match
   segmentation against (alias vocabulary ∪ `descriptor_tokens` ∪ a generic wordlist). If the
   segmentation leaves an unmatched residue > 50% of characters, keep the unsegmented form and
   set `segmented_from_hashtag=False`. (Over-segmentation invents token boundaries inside
   brand names; the 50% residue test is the guard.)
6. Separator collapse: `[-_–—·•/\\.,'’"“”()\[\]{}+&@#*~]` → single space; collapse runs of
   whitespace; trim.
7. **Legal-entity suffix strip** (trailing tokens only, and only while ≥1 token and ≥3
   characters remain): `inc incorporated corp corporation co company ltd limited plc llc llp
   lp pte pt tbk sdn bhd gmbh ag sa nv bv ab oy as aps kk kg srl spa holdings holding group
   intl international` + `the` as a leading token. Configured in
   `config/lexicons/legal_suffixes.yaml`. These are jurisdictional boilerplate, not brand
   identity; a company node and its brand node must not be split by `inc`.
8. **Marketplace/packaging boilerplate strip** (leading or trailing tokens only, iteratively,
   same ≥1-token guard): quantity and unit tokens (`g gm gr gram grams kg ml l litre liter
   oz lb pc pcs pack packs pk ct count x`), bare numerals and `\d+(\.\d+)?[a-z]{0,4}` quantity
   compounds, and listing boilerplate (`authentic original genuine official store shop seller
   free shipping cod onhand on hand ready stock instock preorder pre order bundle set promo
   sale discount new brandnew sealed imported local legit`).
   `config/lexicons/listing_boilerplate.yaml`.
   **Hard rule, enforced by a unit test:** this file may not contain any product-type,
   category, flavour, occasion or culture word. Product-type tokens are *not* stripped here;
   they are handled at runtime by §1.3.
9. If the result is empty, fall back to step-6 output.

`skeleton` pipeline (blocking only, never an identity key): run `norm`, then collapse each
character-confusion class to a single representative, collapse doubled characters
(`aa`→`a`), and remove spaces.

```python
# confusion.py — OCR/vision confusion classes (config/lexicons/confusions_ocr.yaml)
OCR_CLASSES: Final[tuple[frozenset[str], ...]] = (
    frozenset("0oOQD"), frozenset("1iIlL|!7"), frozenset("5sS"), frozenset("8B"),
    frozenset("2zZ"),   frozenset("6gG"),      frozenset("9qg"), frozenset("uvy"),
    frozenset("cC("),   frozenset("nh"),       frozenset("mM"),
)
OCR_BIGRAMS: Final[Mapping[str, str]] = {   # merged/split glyph pairs
    "rn": "m", "vv": "w", "cl": "d", "ii": "u", "nn": "m", "ri": "n", "li": "h",
}
# Phonetic/orthographic confusion, locale-configurable, no brand or culture content.
# config/lexicons/confusions_phonetic.<locale>.yaml
PHONETIC_CLASSES_DEFAULT: Final[tuple[frozenset[str], ...]] = (
    frozenset("bv"), frozenset("fp"), frozenset("cks"), frozenset("jy"),
    frozenset("dt"), frozenset("gh"), frozenset("mn"), frozenset("rl"), frozenset("zs"),
)
PHONETIC_BIGRAMS_DEFAULT: Final[Mapping[str, str]] = {
    "ph": "f", "ck": "k", "qu": "kw", "x": "ks", "ll": "l", "tion": "shun", "sch": "sk",
}
```
The phonetic table is selected by run locale and lives in config precisely so that market
orthography is a *configuration* fact, never a hardcoded assumption in code.

One honest caveat on the other phonetic mechanism: `phonetic_key` (blocking key **K5**) is
`fuzzystrmatch`'s double metaphone, which encodes **English** orthography-to-sound rules. For
code-switched or non-English speech it will under-group and occasionally mis-group. That is
tolerable *because K5 is a blocking key only* — a bad phonetic code costs recall, never a merge
(P3) — but it means K5's contribution should not be assumed uniform across locales, and its hit
rate is recorded per run so the assumption is checkable rather than invisible.

### 1.3 Runtime descriptor discovery (replaces a hardcoded product-word stoplist)

`core_tokens = tokens − descriptor_tokens`. A token becomes a descriptor when the data says
it describes rather than names:

```sql
-- refreshed at the end of each resolve_brands stage
create table descriptor_tokens (
  token              text primary key,
  brand_df           int     not null,   -- # distinct CONFIRMED brands whose resolved
                                         --   observations contain this token
  corpus_df          int     not null,   -- # distinct source items containing the token
  corpus_df_share    numeric(6,5) not null,
  -- document frequency over the BRAND-AGNOSTIC stratum only: items whose
  -- originating research_queries.names_known_brand = false. A token that is a
  -- brand name is frequent in the corpus largely BECAUSE the agent searched for
  -- it (brief §8 strategy A), so the raw share is a collection artefact.
  corpus_df_agnostic int     not null default 0,
  agnostic_df_share  numeric(6,5) not null default 0,
  is_descriptor      boolean not null,
  became_descriptor_at timestamptz,
  updated_at         timestamptz not null default now()
);
```
```
is_descriptor :=  brand_df >= 5
               OR (agnostic_df_share > 0.02 AND brand_df >= 3)
```
**Both routes require multi-brand attachment. This is not a refinement; it is a correctness
requirement.** The original form (`brand_df >= 5 OR corpus_df_share > 0.02`) had a route to
descriptor status that did not look at `brand_df` at all, and the token most certain to exceed
2% corpus document frequency is *the name of the brand the run is researching* — because brief
§8 strategy A deliberately issues brand-level queries for it. Under the original rule the
subject brand's own name becomes a descriptor on run 1, Tier 5's stopword guard then refuses to
create it, and the subsystem silently cannot see the one entity the run exists to study. The
`brand_df >= 3` conjunct makes the frequency route say what it was always meant to say:
*frequent **and** not discriminative*.

- **`brand_df >= 5`** — a token attached to five *different* brand identities is describing a
  product attribute, not naming one brand. Four is still plausibly coincidence in a few-hundred
  brand registry; five is the point where the token has demonstrably no discriminative power.
- **`agnostic_df_share > 0.02 AND brand_df >= 3`** — a token in >2% of items *that were not
  retrieved by a query naming a brand* is corpus-generic, provided it also attaches to at least
  three distinct confirmed brands. The share is computed over the brand-agnostic stratum
  (`research_queries.names_known_brand = false`, XS-6) precisely so that the agent's own search
  behaviour cannot promote a brand name into a stopword. With ~4000 items of which typically
  40–60% are brand-agnostic, 2% is ~30–50 items.
- **Never a descriptor:** a token equal to the sole `core_token` of any `confirmed` brand is
  excluded from descriptor status outright, whatever the counts say, and the attempt is logged
  to `brand_review_items(kind='ambiguous_surface')` for a human to look at. A one-token brand
  name cannot be a product word in the same registry without a human saying so.
- **Boundary behaviour:** crossing into descriptor status is *logged* (`became_descriptor_at`)
  and, critically, **does not delete any alias**. If a multi-token brand's name *contains* a
  token that becomes descriptor-like, the alias survives: the token leaves `core_tokens` only,
  and `norm` is untouched (see the last bullet). Where the alias would become non-discriminative
  on its own, it is marked `is_ambiguous = true` and routed through §1.7's ambiguity path rather
  than `requires_context = true` — `requires_context` requires non-empty `context_terms`
  (`alias_ctx_chk`), and at this point there is no learned distinguishing feature to put in them,
  so setting it here would fail the constraint and abort the write.
- Descriptors are **down-weighted, never stripped from `norm`**. Only `core_tokens` excludes
  them. This matters: `norm` must stay stable even as the descriptor set evolves.

Residual descriptor tokens left over after a brand-level match are recorded in
`brand_surface_observations.residual_tokens` and feed **variant discovery** (§1.6.3).

**Run 1 behaves differently and the difference is load-bearing.** `descriptor_tokens` is
refreshed at the *end* of `resolve_brands`, so the first run resolves with an empty descriptor
set: `core_tokens == tokens`, Tier 1's `coverage` denominator is the full token count (so
containment is *harder* to satisfy on descriptor-heavy commerce titles), and Tier 5's stopword
guard never fires. The consequences are all in the safe direction — more unresolved surfaces,
no extra merges — but run 1's resolution statistics are not comparable with later runs', and
`resolution_quality` will be lower. `brands.notes` records `descriptor_set_size` at resolution
time so the two regimes are distinguishable after the fact.

### 1.4 Candidate generation (blocking)

Six cheap, indexed blocking keys. Union of their hits is the candidate set, capped at 40
(ordered by the best key's own score). All are recall devices; none decides.

| key | index | catches |
|---|---|---|
| K1 `surface_norm` equality | btree | clean repeats |
| K2 `ocr_skeleton` equality | btree | character-confusion corruption |
| K3 trigram similarity ≥ 0.45 on `surface_norm` | `gin_trgm_ops` | insertions/deletions, reordering |
| K4 `token_signature` equality | btree | word-order variation in multiword names |
| K5 `phonetic_key` equality | btree | spoken/ASR spellings |
| K6 pgvector HNSW top-20 on alias embedding | `hnsw (vector_cosine_ops)` | semantically close spellings nothing else catches |

K3's 0.45 trigram floor: PostgreSQL trigram similarity between two 8–12 character strings
differing by 2 edits lands around 0.5–0.6; 0.45 keeps those while excluding most unrelated
pairs. It is a *recall* floor only — false positives cost one scoring pass each, so it is set
generously on purpose.

K6 is skipped (saving an embedding call) whenever K1–K5 already produced ≥1 candidate from a
`confirmed` brand. In a warmed registry this skips ~88–92% of surfaces; see §8 for cost.

### 1.5 The matching cascade

```python
# cascade.py
class MatchTier(StrEnum):
    EXACT = "exact"; CONTAINMENT = "containment"; FUZZY = "fuzzy"
    EMBEDDING = "embedding"; LLM = "llm"; NEW = "new"; NONE = "none"

@dataclass(frozen=True, slots=True)
class Candidate:
    brand_id: UUID
    alias_id: UUID | None
    tier: MatchTier
    score: float                   # comparable within a tier only
    detail: Mapping[str, object]

@dataclass(frozen=True, slots=True)
class Resolution:
    state: SurfaceResolutionState
    brand_id: UUID | None
    alias_id: UUID | None
    tier: MatchTier
    score: float
    runner_up: Candidate | None
    margin: float | None
    residual_tokens: tuple[str, ...]
    created_brand: bool
    provisional_merge: bool        # see §1.8 reversibility
    reason: str                    # human-readable, stored verbatim

class BrandResolver:
    def __init__(self, repo: BrandRepository, embedder: Embedder,
                 adjudicator: BrandAdjudicator | None,
                 cfg: ResolutionConfig) -> None: ...

    async def resolve_batch(
        self, observations: Sequence[SurfaceObservation], *, run_id: UUID
    ) -> list[Resolution]: ...

    async def _blocking_candidates(
        self, nf: NormalForm, channel: AliasChannel
    ) -> list[Candidate]: ...

    def _score_candidates(
        self, nf: NormalForm, channel: AliasChannel, cands: Sequence[Candidate]
    ) -> list[Candidate]: ...

    async def _decide(
        self, nf: NormalForm, channel: AliasChannel, scored: Sequence[Candidate]
    ) -> Resolution: ...
```

#### Tier 0 — EXACT
`surface_norm` equals an alias's `surface_norm`, alias not `blocked`.
- exactly one distinct `brand_id` → **resolve**, score `1.000`.
- more than one distinct `brand_id` (legitimate homonyms) → do **not** resolve on this tier;
  fall through to context disambiguation (§1.7), and if that fails → `ambiguous`.

#### Tier 1 — CONTAINMENT (multiword surfaces, mainly commerce titles)
An alias's token sequence appears as a **contiguous token subsequence** of the surface tokens
(token-boundary match, never substring — substring matching inside words is the single most
common source of spurious brand hits).

Accept when all hold:
- alias token count ≥ 2, **or** (alias token count = 1 **and** that token's length ≥ 4 **and**
  `descriptor_tokens.is_descriptor` is false for it);
- `coverage = alias_core_token_count / max(1, len(surface.core_tokens)) >= 0.34`.

Score `0.950`. Residual = surface core tokens not consumed by the alias.

`coverage ≥ 0.34`: at least a third of the surface's *non-descriptor* content must be the
brand name. Below that the alias is an incidental mention inside a title that is mostly about
something else (e.g. a multi-brand bundle listing), which is handled by multi-hit extraction
instead. **Boundary:** at exactly 0.34 it accepts; at 0.33 the hit is recorded as a
`secondary_hit` in `detail` and contributes to the multi-brand-title path (§1.9) rather than
resolving the primary brand of the listing.

A commerce title may produce **multiple** containment hits; each becomes its own observation
with its own `char_span`. The title's "primary" brand is the hit with the highest coverage,
tie-broken by leftmost `char_span`.

#### Tier 2 — FUZZY (bounded edit distance, the workhorse for OCR-mangled surfaces)

Weighted Damerau–Levenshtein over `norm`:

```python
def weighted_edit_cost(a: str, b: str, *, classes: Sequence[frozenset[str]],
                       bigrams: Mapping[str, str]) -> float:
    """Costs:
      substitution within a confusion class .......... 0.50
      substitution outside any confusion class ....... 1.00
      insert/delete of a character equal to its
        immediate neighbour (gemination) ............. 0.50
      other insert/delete ............................ 1.00
      adjacent transposition ......................... 1.00
      bigram rewrite from `bigrams` (rn->m etc.) ..... 0.50 for the whole pair
    """
```
Class table is `OCR_*` for `PACKAGING_VISION`, `PHONETIC_*` for `SPOKEN`, the **union** for
`WRITTEN`/`COMMERCE_TITLE` (users both mistype and mis-hear), and `OCR_*` only for `WEB_TEXT`.

Length-dependent hard bound on the *unweighted* edit distance, applied before scoring as a
cheap cutoff:

```python
def max_edits_for_length(n: int) -> int:
    """n is min(len(surface_norm), len(alias_norm)) -- see note below."""
    if n <= 3:  return 0
    return min(3, 1 + (n - 4) // 5)     # 4-8 -> 1, 9-13 -> 2, 14+ -> 3
```
**`n = min(len(a), len(b))`, not `len(surface)` and not `len(alias)`.** This is not a detail:
the bound must be *symmetric in its two arguments*, or the same pair of strings admits a
different number of edits depending on which side the cascade happens to be holding, and two
runs that differ only in insertion order produce different registries. `min` rather than `max`
is also the conservative choice — it is the shorter string that determines how much of the name
is actually in evidence.
- **`n <= 3` → 0 edits.** One edit on a 3-character name is a 33% change. Short brand names
  that differ by one character are routinely *different brands*; allowing any edit here is a
  guaranteed wrong-merge generator.
- **~1 edit per 5 characters** keeps relative similarity ≥ 0.80 at every length, so the
  similarity score below is never dominated by the bound rather than by the evidence.
- **Cap at 3** because beyond three edits the candidate is better served by Tier 3/4, where
  context is available.
- **Boundary:** a surface exceeding the bound is simply not a fuzzy candidate; it can still
  reach the brand via K6/Tier 3/Tier 4 or become a new brand. Nothing is lost.

Similarity:
```
fuzzy_sim = 1 - weighted_edit_cost(a, b) / max(len(a), len(b))
```

Two structural guards, both of which exist because they kill the dominant wrong-merge modes:

- **G-INIT (initial-character guard).** The first character must be equal, or substituted
  within the active confusion class, or part of a bigram rewrite. Rationale: initial glyphs
  are the most reliably rendered by OCR and the most reliably perceived in speech, and they
  carry disproportionate brand-discriminative load. Free initial substitution is the single
  largest source of false merges.
- **G-PREFIX (discriminative-prefix guard).** For `min(len) >= 4`, the first two characters
  may differ by at most one confusion-class substitution in total. Reject otherwise.

Decision bands:

| `fuzzy_sim` | additional requirement | outcome |
|---|---|---|
| ≥ 0.88 | Jaro–Winkler ≥ 0.90, target brand `status='confirmed'`, margin rule passes | **auto-resolve** (`provisional_merge=true`, see §1.8) |
| 0.80 – 0.8799 | — | **candidate only**; requires Tier 4 adjudication |
| < 0.80 | — | not a candidate |

- **0.88 auto bar:** at length 10 this permits one non-class substitution (sim 0.90) or two
  class substitutions (sim 0.90); it excludes two non-class substitutions (0.80). That is the
  intended line: class-plausible corruption resolves, arbitrary double divergence does not.
- **Requiring Jaro–Winkler ≥ 0.90 in addition** is a second, structurally different metric.
  Weighted edit distance can be "gamed" by a chain of confusion-class substitutions that
  Jaro–Winkler (prefix-weighted, transposition-aware) scores poorly. Two independent metrics
  agreeing is a materially stronger condition than either alone.
- **`confirmed` targets only (P4):** fuzzy matching a provisional entity against another
  provisional entity lets OCR noise aggregate into a phantom brand. Provisional↔provisional
  fuzzy pairs go to `brand_review_items(kind='merge')` instead.
- **Boundary at exactly 0.88** with JW exactly 0.90: resolves, but `provisional_merge` is set,
  so the decision is re-examined at the 3rd distinct item (§1.8).

#### Tier 3 — EMBEDDING (recall and tie-break only, never sufficient)

Embed `f"{surface_raw} | {context_snippet[:160]}"` with `text-embedding-3-large`,
`dimensions=1536` (pgvector HNSW is unusable above 2000 dims). Compare against
`brand_aliases.embedding`.

- cosine ≥ **0.93** → candidate, score `0.93 + (cos - 0.93) * (0.06 / 0.07)` mapped into
  `[0.93, 0.99]` for ordering only.
- **Never auto-resolves.** Any embedding-only candidate must pass Tier 4.
- cosine < 0.93 → discarded.

Why 0.93 and why never sufficient: within a single product domain, embeddings of two
different brand names are compressed into roughly `[0.85, 0.98]` cosine because the model
encodes "this is a brand name in category C", which is exactly the information we must *not*
use for identity. 0.93 is set to admit near-spellings while keeping the candidate list small;
the hard "never sufficient" rule is what makes the threshold safe rather than the number.
Jina embeddings are the configured fallback provider; because this tier cannot decide, a
provider swap changes recall only, never a merge decision — a deliberate robustness property.

#### Tier 4 — LLM ADJUDICATION (the ambiguous band only)

Invoked when: candidates exist but none auto-resolved; or Tier 0 produced a homonym conflict;
or `provisional_merge` is being re-examined at its 3rd distinct item; or a would-be new brand
has a candidate in `[0.80, 0.88)`.

Model `claude-opus-5`, `thinking={"type":"adaptive"}`,
`output_config={"effort":"low"}` (raised to `"medium"` when ≥3 candidates or when either
candidate brand already has ≥20 distinct items, i.e. when a wrong answer is expensive).
Batched **40 surfaces per call**, instruction block prompt-cached.

```python
# apps/api/aypo/llm/prompts/brand_adjudication.py
class AdjudicationDecision(StrEnum):
    SAME_AS = "same_as"; VARIANT_OF = "variant_of"; SUB_BRAND_OF = "sub_brand_of"
    NEW_BRAND = "new_brand"; NOT_A_BRAND = "not_a_brand"; UNRESOLVED = "unresolved"

class SurfaceVerdict(BaseModel):
    surface_index: int
    decision: AdjudicationDecision
    candidate_key: str | None = None      # echoes the candidate key we supplied
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = Field(max_length=400) # must quote the supplied context
    distinguishing_feature: str | None = None

class AdjudicationBatch(BaseModel):
    verdicts: list[SurfaceVerdict]

# `output_format=` is the DEPRECATED structured-output parameter. The current shape puts
# both the schema and the effort level inside `output_config`:
#
# response = await client.messages.parse(
#     model=settings.aypo_llm_model,          # NOT a literal; brief §11 / apps/api/aypo/config.py
#     max_tokens=16000,                       # required; thinking tokens bill as output
#     thinking={"type": "adaptive"},
#     output_config={"format": AdjudicationBatch, "effort": effort},
#     messages=[...],
# )
# (Take the parsed-result accessor from the installed SDK version rather than from this
#  document; the REQUEST shape above is the part that must not be copied wrong.)
```
Three further bindings this subsystem must respect rather than hardcode:
- **Model and effort come from `Settings`** (`aypo_llm_model`, `aypo_analysis_model`,
  `aypo_reasoning_effort`), not from literals in prompt modules. `apps/api/aypo/config.py`
  already states that every credential and tunable is read from the environment; literal
  `"claude-opus-5"` strings in `llm/prompts/` quietly opt this subsystem out of the project's
  own cost controls, including the `AYPO_ANALYSIS_MODEL` tier that exists for high-volume
  passes, and out of `AYPO_MAX_LLM_USD`.
- **These three classifiers are not latency-sensitive within a run**, so they should dispatch
  through the **Message Batches API at 50% of the per-token rate**, as doc 03 already does.
  That halves the §8 figures, which are quoted at the interactive rate and are therefore an
  upper bound.
- **A refusal fallback** (`aypo_llm_fallback_model`) must be configured on these calls, or a
  single policy decline aborts a stage mid-run.

Prompt skeleton (placeholders only; no brand, category or culture content anywhere):

```
SYSTEM
You decide whether a noisy text fragment refers to the same commercial brand as a
candidate already in a registry. The fragment was extracted automatically and may be
corrupted by image recognition, speech recognition, or user typing.

Rules you must follow:
1. Answer SAME_AS only if the fragment is a corrupted or alternative rendering of the
   candidate's name. Similar-sounding or similar-looking names belonging to different
   commercial entities are NOT the same brand.
2. Answer VARIANT_OF when the fragment names the candidate plus a product qualifier, and
   SUB_BRAND_OF when the fragment names a distinct line owned by the candidate.
3. Answer NEW_BRAND only if the fragment is a plausible brand name that none of the
   candidates covers.
4. Answer NOT_A_BRAND for descriptive text, quantities, seller names, platform boilerplate,
   or recognition garbage.
5. Answer UNRESOLVED whenever the supplied context is insufficient to choose. UNRESOLVED is
   the correct and preferred answer under uncertainty. A wrong SAME_AS is far more damaging
   than an UNRESOLVED.
6. Base your answer only on the supplied fragments and contexts. Do not use outside
   knowledge about which brands exist, who owns them, or what markets they are sold in.
7. Quote the supplied context in `evidence`. If you cannot quote it, answer UNRESOLVED.

USER
## Fragment {{i}}
raw: "{{SURFACE_RAW}}"
normalised: "{{SURFACE_NORM}}"
channel: {{CHANNEL}}          (packaging image | speech | typed text | listing title)
extractor confidence: {{EXTRACTOR_CONFIDENCE}}
contexts ({{N_CTX}} of {{N_TOTAL}} occurrences):
  - "{{CTX_1}}"
  - "{{CTX_2}}"
candidates:
  - key: {{CAND_KEY}}  name: "{{CANON_NAME}}"  level: {{BRAND_LEVEL}}
    known renderings: "{{ALIAS_1}}", "{{ALIAS_2}}", "{{ALIAS_3}}"
    example contexts: "{{CAND_CTX_1}}", "{{CAND_CTX_2}}"
    distinct items in sample: {{CAND_ITEM_COUNT}}
  - key: ...
```
`candidate_key` is an opaque per-call token, not a UUID, so a hallucinated identifier cannot
address an arbitrary brand row. Verdicts whose `candidate_key` is unrecognised are coerced to
`UNRESOLVED` and logged.

Mapping verdict → outcome:

| verdict | `llm.confidence` | outcome |
|---|---|---|
| `SAME_AS` | ≥ 0.75 | resolve; attach alias with `resolved_by='llm'`, `provisional_merge=true` |
| `SAME_AS` | < 0.75 | `ambiguous` + `brand_review_items(kind='merge')` |
| `VARIANT_OF` / `SUB_BRAND_OF` | ≥ 0.75 | resolve to the **parent**, record `residual_tokens`, open `brand_review_items(kind='variant')`. Child nodes are never auto-created (§1.6.3). |
| `NEW_BRAND` | ≥ 0.60 | proceed to Tier 5 |
| `NOT_A_BRAND` | ≥ 0.60 | `rejected_noise` |
| `UNRESOLVED` | any | `ambiguous` |

0.75 for a merge vs 0.60 for a create: a merge is the irreversible-feeling direction (P1), so
it carries the higher bar; creating a provisional brand is cheap and self-correcting.

#### Tier 5 — NEW BRAND CREATION

Reached when no candidate scored ≥ 0.80 fuzzy / ≥ 0.93 cosine, or Tier 4 said `NEW_BRAND`.

Creation gate — a new `brands` row is written only when **any** of:
- the surface appears on ≥ **2 distinct source items** (after the collection subsystem's
  near-duplicate collapse, XS-13); or
- it appears ≥ 1 time on channel `COMMERCE_BRAND_FIELD` (a structured, platform-validated
  field); or
- it appears ≥ 1 time with `extractor_confidence >= 0.85` **and** ≥ 4 characters **and** its
  `norm` is not in `descriptor_tokens`.

Otherwise the observation is parked as `pending_support` with `brand_id = NULL` and is
re-evaluated on every subsequent run.

- **Why ≥2 distinct items:** a surface seen exactly once is overwhelmingly recognition noise,
  a one-off seller string, or a hallucinated frame read. Requiring two distinct items removes
  nearly all of that while costing a genuinely rare brand only a delay — it is created the
  moment it recurs. **Boundary at exactly 1 item:** parked, not discarded, and visible in the
  "unresolved surfaces" audit list with its item link.
- **Stopword guard:** never create a brand whose `norm` is a single token already marked
  `is_descriptor` **by the rule in §1.3** (which requires `brand_df >= 3`). The guard keys on
  `is_descriptor` only; it must **not** re-test a bare `corpus_df_share > 0.02`, because on run 1
  the subject brand's own name is the single most frequent brand-like token in the corpus by
  construction and a bare frequency test would refuse to create it. Such surfaces are parked with
  reason `generic_surface` regardless of count, and a parked surface whose `brand_df` is 1 is
  raised as `brand_review_items(kind='ambiguous_surface')` rather than parked silently.

Promotion `provisional → confirmed` (evaluated at the end of each run) when **any**:
- `distinct_item_count >= 5`; or
- `distinct_item_count >= 2` **and** `distinct_channel_count >= 2`; or
- `commerce_brand_field` observations ≥ 3.

Two independent channels at 2 items is treated as equivalent to 5 items on one channel
because cross-channel agreement (e.g. seen on packaging *and* spoken) is much harder to
produce by correlated extractor error than repetition within one channel.

Demotion: a `provisional` brand with `distinct_item_count = 1` and no new observations for
**3 consecutive runs with coverage** (§5.3) is set `status='rejected'` with its observations
re-opened as `pending_support`. Rows are never deleted (§27 auditability).

#### Resolution thresholds, collected

```python
# apps/api/aypo/intelligence/brand_resolution/config.py
class ResolutionConfig(BaseModel):
    trgm_floor: float = 0.45
    fuzzy_auto: float = 0.88
    fuzzy_candidate: float = 0.80
    jaro_winkler_min: float = 0.90
    embed_candidate_cos: float = 0.93
    margin_min: float = 0.06
    containment_coverage_min: float = 0.34
    llm_merge_min_conf: float = 0.75
    llm_create_min_conf: float = 0.60
    new_brand_min_items: int = 2
    confirm_min_items: int = 5
    confirm_min_items_multichannel: int = 2
    descriptor_brand_df: int = 5
    descriptor_corpus_share: float = 0.02
    provisional_merge_review_at_items: int = 3
    candidate_cap: int = 40
    llm_batch_size: int = 40
```

### 1.6 Parent company / brand / sub-brand / variant

#### 1.6.1 Representation

Single self-referencing table with an explicit level and a maintained materialised ancestry.

```sql
create type brand_level  as enum ('company','brand','sub_brand','variant');
create type brand_status as enum ('provisional','confirmed','rejected','merged');
create type brand_origin as enum ('discovered','seed','manual');
```

- `company` — legal/corporate entity.
- `brand` — the **default analysis level**. Competitor relationships are stored between
  `brand`-level nodes unless configured otherwise.
- `sub_brand` — a named line owned by a brand, distinct enough to have its own identity.
- `variant` — a qualifier on a brand/sub-brand (an attribute, format or size line, whatever
  dimension the data turns out to use). Variants exist to
  stop qualifier text from fragmenting brand-level evidence; they are never the unit of
  competitor analysis by default.

Rules:
- `depth` ≤ 3 (company→brand→sub_brand→variant). Deeper proposals are rejected to the review
  queue; arbitrary depth makes roll-up semantics unpredictable and buys nothing.
- `ancestor_ids uuid[]` and `root_company_id` are maintained by trigger (§5.1) so roll-up is a
  single GIN-indexed containment query rather than a recursive CTE per request.
- Observations resolve to the **most specific node the surface actually supports**; aggregation
  then rolls up to `analysis_level` (default `brand`) via `ancestor_ids`. A surface that
  supports only the brand resolves to the brand — it is never pushed down to a guessed variant.

#### 1.6.2 Roll-up and the intra-family guard

Any competitor pair where `a.id = ANY(b.ancestor_ids)` or `b.id = ANY(a.ancestor_ids)` or
`a.root_company_id = b.root_company_id` is written with
`competitor_relationships.same_corporate_family = true` and is **excluded from every
competitor-facing view and from all §24 comparisons**, while remaining visible in the audit
view. It is still typed normally (two stablemates can genuinely be substitutes on shelf), but
presenting an in-portfolio pair as a "discovered competitor" is a credibility failure.

Note honestly: `same_corporate_family` can only be true when ownership is *known* — i.e.
supplied, human-confirmed, or evidenced by a corporate endorsement visible on packaging. The
sample alone cannot reveal ownership. See **OQ-6**.

#### 1.6.3 Variant discovery (proposed, never auto-created)

After each run, for each brand B, cluster B's `residual_tokens` by exact `token_signature`.
A residual signature with `distinct_item_count >= 5` **and** appearing on ≥ 2 channels becomes
`brand_review_items(kind='variant')` carrying the proposed name and its evidence. Auto-creating
variants from residual text is how a registry acquires hundreds of junk nodes (every listing
descriptor becomes a "variant"), so creation requires acceptance. Five items on two channels is
the same evidence bar as brand confirmation, applied one level down.

#### 1.6.4 Hierarchy edits

Attaching a parent, splitting, or merging confirmed brands is **never automatic**:
- `brand_review_items(kind='merge')` when two `confirmed` brands both have ≥3 distinct items
  and a cascade/LLM match between them.
- `brand_review_items(kind='split')` when one brand's alias set divides into two clusters with
  no shared item and inter-cluster `fuzzy_sim < 0.80` — usually the trace of a past bad merge.
- `brand_review_items(kind='promote')` when a `sub_brand` accumulates more distinct items than
  its parent, which usually means the hierarchy was assigned backwards.

Auto-merge of two confirmed, well-evidenced brands is prohibited outright (P1).

### 1.7 Ambiguity policy

Let `best`, `second` be the top two scored candidates **after collapsing candidates that
resolve to the same analysis-level ancestor** (two aliases of one brand are not a conflict).

```
margin = best.score - second.score          (same tier, or 0 if tiers differ and best's
                                             tier is not strictly higher priority)
```

Auto-resolve requires **all** of:
1. `best.score` ≥ that tier's auto threshold;
2. `margin >= 0.06`, or `second` does not exist;
3. `best.brand.status == 'confirmed'` for Tier 2 (fuzzy);
4. no active `brand_review_items(kind='merge')` open between `best` and `second`.

**Why `margin >= 0.06`:** the fuzzy decision band runs 0.80→0.88, width 0.08. Requiring the
margin to exceed three quarters of that band width guarantees the runner-up is not in the same
decision band as the winner. A smaller margin means the metric cannot tell them apart, and
"the metric cannot tell" must not become "pick one".
**Boundary at exactly 0.06:** resolves (inclusive), with `margin` persisted so the decision is
auditable and the threshold re-tunable from stored data.

On failure the observation is written with `resolution_state = 'ambiguous'`, both
`brand_id = NULL` and the two contenders in `brand_id`-adjacent audit columns
(`runner_up_brand_id`, `runner_up_score`), plus a `brand_review_items(kind='ambiguous_surface')`
row aggregated per `surface_norm` (one review item per surface, not per observation).

**Context-gated aliases (the homonym case).** When one `surface_norm` legitimately maps to two
brands, Tier 4 is asked for a `distinguishing_feature`; the alias rows are marked
`is_ambiguous = true, requires_context = true` with `context_terms` populated from that
feature. A `requires_context` alias matches only when ≥1 `context_terms` entry appears in the
observation's `context_snippet` (case-insensitive token match). With no context match, the
surface is `ambiguous`. Learned, auditable, per-brand — and it ships empty.

**Unresolved surfaces are a product surface, not a swept-under failure.** The UI exposes
*Unresolved brand surfaces* with raw text, item count, channel mix, candidate contenders and
links to the items, plus a one-click "these are the same" / "these are different" action that
writes `brand_review_items`. §28 requires identifying insufficient data; this is that, for
entity resolution.

### 1.8 Reversibility of fuzzy and LLM merges

A fuzzy or LLM auto-resolve writes `provisional_merge = true` on the alias. When that alias
reaches `distinct_item_count = 3`, it is re-adjudicated once at `effort="medium"` with three
fresh contexts. If the verdict is not `SAME_AS`:

1. a new brand is created from the alias's surface (Tier 5 gate is waived — it already has 3
   items);
2. all observations carrying that `alias_id` are re-pointed by `UPDATE`;
3. `brand_alias_detachments` records the before/after for audit;
4. the affected pairs are marked for recompute (`competitor_pair_enumeration.dirty = true`).

This is only affordable because of **P2**: items are never stamped with brand ids, so a
re-point is one UPDATE plus a recompute of the affected pairs, not a reprocessing of video.

```sql
create table brand_alias_detachments (
  id uuid primary key default gen_random_uuid(),
  alias_id uuid not null,
  surface_norm text not null,
  from_brand_id uuid not null references brands(id),
  to_brand_id uuid not null references brands(id),
  observations_repointed int not null,
  reason text not null,
  research_run_id uuid,
  detached_at timestamptz not null default now()
);
```

### 1.9 Multi-brand surfaces and assortment items

- **Commerce title with ≥3 distinct brand hits** → `bundle_or_assortment = true`. Such items
  may contribute to `same_shelf` evidence at `weight = 0.5` and are **excluded from
  substitution and co-appearance signals**. A multi-pack or comparison listing is retail
  adjacency, not a consumption or choice observation.
- **Social item with ≥6 distinct resolved brands** → `assortment_item = true`. Excluded from
  S2's co-occurrence statistics (one such item would otherwise manufacture
  `C(6,2) = 15` spurious pairs) and routed to `same_shelf` at `weight = 0.4`.
  **Why 6:** below six, a scene with several products is a plausible genuine consumption or
  comparison setting; at six and above the item is enumerating an assortment (shelf, haul,
  inventory). The exclusion count is written to `competitor_pair_enumeration` so the audit view
  can show how many items were set aside and why.

### 1.10 Resolution quality, exported to downstream confidence

An `ambiguous` observation has `brand_id IS NULL`, so it cannot be attributed to a brand through
`brand_id`; it is attributed through the contenders instead (`runner_up_brand_id` and the audit
columns written in §1.7). The statement is therefore explicit about its population:

```sql
-- maintained per brand at the end of resolve_brands
with attributed as (
  select b.id as brand_id,
         o.resolution_state, o.match_tier, o.match_score
  from brands b
  join brand_surface_observations o
    on  o.brand_id = b.id                        -- resolved to b
    or  o.runner_up_brand_id = b.id              -- b was a losing contender
  where b.status in ('provisional','confirmed')
)
update brands b set resolution_quality = q.rq
from (
  select brand_id,
         greatest(0.500,
           1.0 - (count(*) filter (
                    where resolution_state = 'ambiguous'
                       or (match_tier = 'fuzzy' and match_score < 0.92)
                 )::numeric / greatest(count(*), 1))) as rq
  from attributed group by brand_id
) q
where q.brand_id = b.id;
```
A brand with no observations at all keeps the `1.000` default, which is correct: it has no
resolution evidence against it, and `V` (volume) is what will be zero for it.

Floored at 0.5 so a shaky entity degrades but never annihilates a relationship. This value
enters `Q_resolution` in §3.5 — a relationship cannot be more confident than the entities it
connects.

---

## 2. THE ELEVEN COMPETITOR SIGNALS

### 2.0 Common detector contract

```python
# apps/api/aypo/intelligence/competitors/base.py
class SignalKind(StrEnum):
    DIRECT_COMPARISON        = "direct_comparison"
    CO_APPEARANCE            = "co_appearance"
    SHARED_CLUSTER           = "shared_cluster"
    CATEGORY_SEARCH          = "category_search_coappearance"
    MARKETPLACE_ADJACENCY    = "marketplace_adjacency"
    RECURRING_MENTION        = "recurring_mention"
    SUBSTITUTION_LANGUAGE    = "substitution_language"
    RANKING_REVIEW           = "ranking_review"
    POSITIONING_SIMILARITY   = "positioning_similarity"
    SHARED_CREATOR           = "shared_creator"
    SHARED_CULTURAL          = "shared_cultural"

class CorrelationGroup(StrEnum):
    CO_PRESENCE      = "co_presence"        # S2, S6
    EXPLICIT_COMPARE = "explicit_compare"   # S1, S7, S8
    DISTRIBUTIONAL   = "distributional"     # S3, S10, S11
    COMMERCE_SURFACE = "commerce_surface"   # S5, S9
    DISCOVERY_SURFACE= "discovery_surface"  # S4

class EvidenceUnit(StrEnum):
    CONTENT_ITEM = "content_item"; COMMERCE_ITEM = "commerce_item"
    COMMERCE_REVIEW = "commerce_review"; WEB_DOCUMENT = "web_document"
    CLUSTER = "cluster"; CREATOR = "creator"
    RESEARCH_QUERY = "research_query"; AGGREGATE = "aggregate"

@dataclass(frozen=True, slots=True)
class PairEvidence:
    brand_id: UUID                 # canonically ordered: brand_id < competitor_brand_id
    competitor_brand_id: UUID
    signal: SignalKind
    strength: float                # per-evidence-item strength in [0,1]
    unit: EvidenceUnit
    evidence_id: UUID | None       # None only for unit == AGGREGATE
    weight: float = 1.0            # evidence-tier weight (see 2.0.2)
    direction: int = 0             # -1 / 0 / +1 relative to canonical order
    is_aggregate: bool = False
    creator_id: UUID | None = None
    published_at: datetime | None = None
    sponsored_likelihood: float | None = None
    canonical_item_ids: tuple[UUID, ...] = ()   # underlying items, for the §3 walk
    detail: Mapping[str, object] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class PairScale:
    """Pair-level normalisation emitted alongside item evidence; applied in aggregation."""
    brand_id: UUID
    competitor_brand_id: UUID
    signal: SignalKind
    scale: float                   # [0,1]
    detail: Mapping[str, object]

class SignalDetector(Protocol):
    kind: ClassVar[SignalKind]
    group: ClassVar[CorrelationGroup]
    min_support: ClassVar[int]          # minimum evidence units for non-zero output
    requires: ClassVar[frozenset[str]]  # capability keys, e.g. {"commerce", "clusters"}

    async def detect(self, ctx: DetectionContext
                     ) -> AsyncIterator[PairEvidence | PairScale]: ...
```

**2.0.1 Why strength is split into per-item strength and a pair scale.** A detector knows two
different things: how good *this* piece of evidence is (prominence, cue explicitness, source
channel) and how surprising the pair's association is *given the corpus* (lift, breadth,
distribution shape). Mixing them into one number makes it impossible to audit which part of a
score came from evidence quality versus from corpus statistics, and makes recency weighting
(which applies to items, not to corpus statistics) incoherent. They are therefore emitted
separately and combined in §3.2.

**2.0.2 Evidence-tier weights** (`PairEvidence.weight`):

```python
TIER_WEIGHT: Final[Mapping[EvidenceUnit, float]] = {
    EvidenceUnit.CONTENT_ITEM: 1.00,     # first-party multimodal analysis, richest
    EvidenceUnit.COMMERCE_ITEM: 0.60,    # structured but shallow; one listing is thin
    EvidenceUnit.COMMERCE_REVIEW: 0.60,
    EvidenceUnit.RESEARCH_QUERY: 0.80,   # a whole query's result set, not one item
    EvidenceUnit.CLUSTER: 0.80,
    EvidenceUnit.CREATOR: 0.70,
    EvidenceUnit.WEB_DOCUMENT: 0.50,     # third-party, locale-uncertain (see S8)
    EvidenceUnit.AGGREGATE: 0.50,
}
```
Within-channel modifiers (`source_weight`) used by S6/S7:
```python
MENTION_SOURCE_WEIGHT: Final[Mapping[str, float]] = {
    "creator_speech": 1.00, "caption": 0.90, "on_screen_text": 0.85,
    "hashtag": 0.60, "comment": 0.40,
}
```
Comments at 0.40: a commenter naming another brand is a third party's association, not the
content's own; comment sections also carry spam and bot text. Hashtags at 0.60: hashtag
stuffing manufactures co-occurrence. **Hashtag-only evidence may never, by itself, satisfy a
relationship-typing rule** (§4).

**2.0.3 Prominence mapping** (used wherever "how present is the brand" matters):
```python
PROMINENCE_W: Final[Mapping[str, float]] = {"primary": 1.0, "secondary": 0.8, "incidental": 0.5}
```
`incidental` at 0.5 enforces §28's "distinguish incidental appearances from active usage": a
brand glimpsed in the background is half the evidence of one being held and used.

**2.0.4 What an LLM is allowed to produce, and what code must compute.** Three of this
subsystem's structured-output schemas contain fields that are *numbers* or *identifiers*. Those
are the fields where a model's fluency turns into a figure with decimal places, so each one is
constrained or verified in code before it can affect anything:

| field | risk | containment |
|---|---|---|
| `candidate_key` (brand adjudication) | a hallucinated id addresses an arbitrary brand row | already opaque and per-call; unrecognised keys coerce to `UNRESOLVED` (§1.5 Tier 4) |
| `brand_key_a/b`, `brand_key_from/to` (S1, S7) | a key not present in the item | **validated against the keys supplied for that item**; any unknown key demotes the verdict to `UNRELATED` / `is_comparison=false` and is counted in `detail.key_validation_failures` |
| `quote` (S1, S7) | paraphrase presented as verbatim | **verified in code** as an exact substring of the supplied span after Unicode NFKC + whitespace collapse; a non-matching quote discards the verdict entirely. D1's whole claim to being "the only human-checkable discriminator" rests on this check existing, not on the prompt asking nicely |
| `rank_a`, `rank_b`, `list_length` (S1 → S8) | invented ordinals driving `adjacency` | validated against a deterministic ordinal scan of the document (§S8) |
| `confidence` (all three schemas) | an uncalibrated float used as a continuous multiplier | **bucketed before use**: `conf_w = 1.00 if c >= 0.85 else 0.75 if c >= 0.70 else 0.50`, and the raw value is stored for later calibration. Self-reported LLM confidence is not calibrated to anything; multiplying by it to three decimals asserts a precision that does not exist. Where `confidence` is used as a *threshold* (0.75 merge / 0.60 create in Tier 4) the raw value is used, because a threshold only needs monotonicity |
| `discovered_cue_phrase` (S7) | a fabricated cue entering the lexicon | required to be a substring of the verified `quote`; `substitution_cues` is a pre-filter only and never contributes to strength (§5.1) |

The rule behind the table: **an LLM may classify, quote and point; code computes every number
that reaches a score.**

---

### S1 — `direct_comparison`  (group: EXPLICIT_COMPARE)

**Input.** Content items with ≥2 distinct resolved brands at the analysis level. Per item:
transcript (with spans), caption, on-screen text, per-brand prominence, frame-level detections.

**Pre-filter (cost control, not correctness).** Keep an item for LLM classification when it has
≥2 distinct brands **and** at least one of:
- a hit from `config/lexicons/comparative_structural.yaml` — *structural* patterns only:
  `X vs Y` / `X or Y` adjacency around two resolved brand spans; comparative or superlative
  morphology adjacent to a brand span; interrogative + two brand spans; ordinal/enumeration
  markers (`#1`, `1.`, `top N`) with ≥2 brand spans;
- ≥2 brands at prominence ≥ `secondary`.

**Computation.** One batched LLM pass (`claude-opus-5`, `effort="low"`, 10 items/call):

```python
class ComparisonKind(StrEnum):
    HEAD_TO_HEAD = "head_to_head"; RANKING = "ranking"; TASTE_TEST = "taste_test"
    BLIND_TEST = "blind_test"; MULTI_REVIEW = "multi_review"; NONE = "none"

class ComparedPair(BaseModel):
    brand_key_a: str
    brand_key_b: str
    quote: str = Field(max_length=300)
    verdict_for: str | None = None      # brand_key or None
    rank_a: int | None = None
    rank_b: int | None = None
    list_length: int | None = None

class ComparisonVerdict(BaseModel):
    item_index: int
    kind: ComparisonKind
    is_comparison: bool
    pairs: list[ComparedPair]
    confidence: float = Field(ge=0.0, le=1.0)

class ComparisonBatch(BaseModel):
    verdicts: list[ComparisonVerdict]
```

Per-item strength:
```
base = {head_to_head: 1.00, ranking: 0.90, taste_test: 0.70,
        blind_test: 0.70, multi_review: 0.50}[kind]
conf_w   = 1.00 if verdict_conf >= 0.85 else 0.75 if verdict_conf >= 0.70 else 0.50   # §2.0.4
strength = base * min(prom_w[a], prom_w[b]) * conf_w
```
`min` of the two prominences, not the mean: a "comparison" in which one brand is only
incidental is a weak comparison, and the weaker side must govern.

`PairScale.scale = 1.0` (S1 makes no corpus-level claim; its evidence is self-contained).

**Output.** `unit=CONTENT_ITEM`, `canonical_item_ids=(item_id,)`, `direction` set from
`verdict_for`, `detail = {kind, quote, verdict_for, rank_a, rank_b, list_length}`.
`min_support = 1` for evidence, but typing `direct_substitute` from S1 needs ≥2 items (§4).

**Failure mode.** The `vs` format is an engagement device; creators compare things that are not
alternatives. Sponsored content names a competitor to disparage it (still a real competitor
signal, so this is not filtered — but `sponsored_likelihood` is recorded and enters
`Q_sponsored`). Comparisons *between variants of the same brand* are the most common false
positive and are eliminated by the intra-family guard (§1.6.2), not by the model. Finally the
comparison can be purely rhetorical with no substitutive content, which is why S1 alone cannot
type `direct_substitute` from a single item.

---

### S2 — `co_appearance`  (group: CO_PRESENCE)

**Input.** Frame-level brand detections with timestamps and per-detection prominence
(cross-subsystem requirement **XS-1**), per content item. Items flagged `assortment_item`
(§1.9) are excluded.

**Computation.**
1. Item-level strength: `strength = min(prom_w[a], prom_w[b])`.
2. Corpus lift via normalised PMI, computed **only over the brand-agnostic stratum** (see the
   sampling-bias note below). With `N` = analysed items in the stratum having ≥1 resolved brand,
   `n_a`, `n_b`, `n_ab` the distinct-item counts within the stratum:
```
p_ab = n_ab / N
lift = (n_ab * N) / (n_a * n_b)
npmi = ln(lift) / (-ln(p_ab))          in [-1, 1]      # undefined at p_ab = 1; see guard
```

**Sampling-bias guard (XS-6), and why it is not optional.** The corpus is *built by queries*.
Brief §8 strategy A issues brand-level queries for the subject and strategy C issues
competitor-level queries for brands the agent has already noticed, so `n_a` is in large part a
record of *how often the agent searched for a*, not of how often `a` occurs. npmi is
monotonically decreasing in `n_a` and `n_b`, so the most-searched brand — which is always the
research subject — has its lift against every partner systematically deflated, while two brands
that were never searched for can post a high npmi off three incidental co-appearances. That is
collection-time sampling bias entering a statistic that is then read as a property of the
category.

The fix is the one S4 already uses: restrict the statistic to items whose originating query did
not name a brand.
- `N`, `n_a`, `n_b`, `n_ab` are computed over items for which **every** originating
  `research_queries` row has `names_known_brand = false` (XS-6). An item discovered by both a
  brand-named and a brand-agnostic query is excluded from the statistic (it is still evidence;
  it just does not get a vote on corpus surprise).
- If the brand-agnostic stratum holds fewer than **200** items with ≥1 resolved brand, npmi is
  not computed at all: `PairScale.scale = 0`, `detail.npmi_suppressed = 'stratum_too_small'`,
  and S2 contributes evidence rows but no corpus-surprise claim. 200 is where a 3-item joint
  count stops being within sampling noise of the marginals it is being compared against; below
  it the estimate is not worth the bias it would carry.
- The stratum size, the excluded-item count and the full-corpus npmi are both stored in
  `competitor_signal_scores.detail` so the difference between the two is auditable. The
  full-corpus value is **never** used in scoring; it is recorded so a reviewer can see how much
  the query mix was moving the number.
- `p_ab = 1` (every stratum item contains both brands) makes `-ln(p_ab) = 0`; npmi is then set
  to `1.0` by definition rather than dividing by zero.
3. Frame simultaneity:
```
simultaneity = (# items where a and b are co-detected within the same +/-2.0s window)/ n_ab
sim_factor   = 0.60 + 0.40 * simultaneity
```
4. `PairScale.scale = clamp(npmi, 0, 1) * sim_factor`, emitted only when `n_ab >= 3`.

**Thresholds and why.**
- **npmi instead of raw lift.** Raw lift explodes for rare pairs: two brands each appearing
  twice that co-appear once produce an enormous lift with no information. npmi is bounded in
  `[-1,1]` and penalises low joint support by construction (the denominator `-ln(p_ab)` grows
  as the pair gets rarer).
- **`n_ab >= 3`.** At `n_ab <= 2` the npmi estimate's standard error is comparable to the
  estimate. **Boundary at `n_ab = 2`:** evidence rows *are still written* (so `first_detected`
  and drill-down exist and the pair can cross the bar next run) but the pair scale is `0`, so
  the signal contributes nothing to confidence. Nothing is deleted; the claim is simply
  withheld. `detail.support_insufficient = true`.
- **Co-presence window.** Primary implementation uses **frame index set intersection**
  (`F_a ∩ F_b ≠ ∅` over `VisibleBrand.frame_indices`, §12.1), which is exact and independent of
  sampling rate. Note what that intersection is actually over: doc 03 samples a **frame budget of
  3/6/8/12/16/20 frames by duration tier** (`max_frames = 32`), *not* ~1 fps — an earlier draft
  of this section asserted 1 fps and the ±2s rationale that followed from it, and both were
  wrong. At 3–20 frames for a whole video the sampled frames are seconds to tens of seconds
  apart, so "same sampled frame" is a strong condition (genuinely simultaneous) while "different
  sampled frames" is a weak one (says almost nothing). That asymmetry is why `simultaneity` only
  ever *raises* the scale from a 0.60 floor and never pushes it toward 0.
  The ±2.0s timestamp window remains the fallback when only timestamps are available, but it is
  now justified as a tolerance on timestamp precision, not as a one-sample gap.
- **`0.60 + 0.40 * simultaneity`.** Non-simultaneous co-appearance is real evidence (both
  brands are in this content's world) so the floor is 0.60 rather than 0, but same-frame
  presence is materially stronger and earns up to 1.0.

```sql
-- co-occurrence base, computed once per run into a temp table.
-- `assortment_items` is NOT a table (an earlier draft referenced one that was never
-- defined); it is derived here from the >= 6 distinct-brand rule in §1.9.
with resolved as (
  select o.source_id as item_id, b.id as brand_id
  from brand_surface_observations o
  join brands b on b.id = o.brand_id
  where o.source_kind = 'content_item'
    and o.resolution_state = 'resolved'
    and b.status = 'confirmed'
  group by 1, 2
), assortment as (                       -- §1.9: >= 6 distinct resolved brands in one item
  select item_id from resolved group by item_id having count(*) >= 6
), agnostic as (                         -- XS-6: every originating query was brand-agnostic
  select rr.content_item_id as item_id
  from research_results rr
  join research_queries rq on rq.id = rr.research_query_id
  group by rr.content_item_id
  having bool_and(rq.names_known_brand = false)
), item_brands as (
  select r.item_id, r.brand_id
  from resolved r
  join agnostic a   on a.item_id = r.item_id
  left join assortment x on x.item_id = r.item_id
  where x.item_id is null
), totals as (select count(distinct item_id)::numeric as n from item_brands),
marg as (select brand_id, count(distinct item_id)::numeric as n_x from item_brands group by 1),
joint as (
  select least(x.brand_id, y.brand_id)    as a,
         greatest(x.brand_id, y.brand_id) as b,
         count(distinct x.item_id)::numeric as n_ab,
         array_agg(distinct x.item_id)      as item_ids
  from item_brands x join item_brands y
    on x.item_id = y.item_id and x.brand_id < y.brand_id
  group by 1, 2
)
select j.a, j.b, j.n_ab, ma.n_x as n_a, mb.n_x as n_b, t.n,
       case when j.n_ab >= t.n then 1.0            -- p_ab = 1: npmi is 1 by definition
            else ln((j.n_ab * t.n) / (ma.n_x * mb.n_x)) / (-ln(j.n_ab / t.n))
       end as npmi,
       j.item_ids
from joint j cross join totals t
join marg ma on ma.brand_id = j.a
join marg mb on mb.brand_id = j.b
where j.n_ab >= 2
  and t.n >= 200;                                  -- stratum floor, see the guard above
```

**Failure mode.** This is the most abundant and the least interpretable signal.
Co-appearance in a retail-shelf, haul or inventory scene means "same distribution channel",
not competition. Worse, **co-consumption and substitution are indistinguishable in raw
co-appearance data** — that is precisely why S2 may never type a relationship on its own and
why §4.2 exists. Also vulnerable to vision false positives on partially occluded packaging,
partly mitigated by the prominence weighting.

---

### S3 — `shared_cluster`  (group: DISTRIBUTIONAL)

**Input.** `clusters`, `cluster_members`, and item→brand from the observation ledger.
**Hard contract (XS-4): brand identity must not be an input feature to clustering.** If brands
are embedded into the clustering features, S3 becomes circular — brands co-cluster because
they were clustered on brand — and the signal is worthless. The clustering subsystem must
cluster on content semantics only.

**Computation.** Eligible clusters: `size >= 12` and `size <= 0.25 * N`.
- **`size >= 12`:** below 12 members, one item moves a brand's presence share by >8pp, so the
  presence profile is dominated by sampling noise.
- **`size <= 0.25 * N`:** a cluster containing a quarter of the corpus is a generic catch-all
  in which every brand appears; co-membership there carries no discriminative information.
  Excluded clusters are listed in `detail.excluded_clusters` with their sizes.

Per eligible cluster `c`: `p_a(c) = distinct items in c containing a / size(c)`. Require
`p_a(c) >= 0.05` and ≥2 distinct items of `c` for each brand. Weights `w_c = size(c) / Σ size`.

```
overlap = Σ_c w_c * min(p_a(c), p_b(c))
norm    = max( Σ_c w_c * p_a(c), Σ_c w_c * p_b(c) )
ruzicka = overlap / norm                      # weighted min/max similarity, in [0,1]
scale   = ruzicka * (0.5 if shared_clusters == 1 else 1.0)
```
One shared cluster is halved because a single cluster is one latent theme; two or more shared
clusters means the brands' content profiles overlap across *distinct* themes, which is a
qualitatively different claim.

**Output.** One `PairEvidence` per shared cluster: `unit=CLUSTER`, `evidence_id=cluster_id`,
`strength = min(p_a(c), p_b(c)) / max(p_a(c), p_b(c))`,
`canonical_item_ids` = the cluster's member items containing **both** brands (possibly empty —
and that is meaningful: a shared cluster with no co-present item is independent of S2).

**Failure mode.** Heavily derived from the same items that drive S2 — handled structurally in
§3.3, not here. Cluster ids are not guaranteed stable across runs under HDBSCAN (**OQ-9**),
which makes `first_detected` on cluster-unit evidence drift. Mitigation in the interim: store
the cluster's centroid-nearest 5 item ids in `detail.anchor_item_ids` so a cluster can be
re-identified across runs by anchor overlap.

---

### S4 — `category_search_coappearance`  (group: DISCOVERY_SURFACE)

**Input.** `research_queries` with `sampling_strategy = 'category'` (brief §8 strategy B),
their `research_results`, and the resolved brands in those results.
**Contract XS-6:** each query row must carry `sampling_strategy`, a boolean
`names_known_brand` (whether the query string matched any alias at the time of issue), and
`generated_from` provenance (the finding/hypothesis id that produced it).

**Eligibility — two guards that make this signal meaningful at all:**
1. `names_known_brand = false`. If the query names a brand, that brand's presence in the
   results is caused by the query, not by the category.
2. `generated_from` is not a brand-scoped hypothesis. Otherwise the agent's own interest in
   brand A biases which category queries exist.
Both filters are recorded per pair in `detail.queries_excluded` so the audit view can show how
many queries were set aside.

**Computation.** For eligible query `q` with result set `R(q)`:
```
share_x(q) = distinct items in R(q) containing x / |R(q)|
k          = # eligible queries where both a and b appear
breadth    = 1 - exp(-k / 3)
coverage   = clamp( sqrt(mean_q share_a(q) * mean_q share_b(q)) / 0.05 , 0, 1)
scale      = breadth * coverage
```
- **`1 - exp(-k/3)`:** saturating and smooth. k=1 → 0.28, k=3 → 0.63, k=6 → 0.86, k=10 → 0.96.
  Scale 3 because three *independent brand-agnostic entry points* is where "both are in this
  category" stops being a sampling accident of one query's ranking.
- **`coverage` with a 0.05 full-credit point:** a geometric-mean result share of 5% earns full
  credit. With ~60 results per query that is 3 items — the same support floor used throughout.
  A brand appearing in 1 of 200 category results is weak category membership and is scaled
  down accordingly. **Boundary:** at exactly 5% coverage = 1.0; below it, linear decay to 0.

**Output.** One `PairEvidence` per eligible query: `unit=RESEARCH_QUERY`,
`evidence_id=query_id`, `strength = min(share_a, share_b) / max(share_a, share_b)`,
`canonical_item_ids` = result items containing both brands.

**Failure mode.** Search engines personalise and recency-bias; a category query's result set is
not a sample of the category. Platform search ranking also rewards engagement, so high-volume
brands over-appear. The `coverage` term partly compensates by rewarding *consistent* presence
over a single lucky ranking. If `APIFY_TOKEN` is absent, this signal runs on Serper web results
and YouTube/web discovery only, with reduced breadth — recorded in run coverage (§7).

---

### S5 — `marketplace_adjacency`  (group: COMMERCE_SURFACE)

**Input.** `commerce_items` (title, resolved brand, leaf category path, price, pack/unit where
extractable, seller id, originating search query), `commerce_reviews`, and — when the actor
exposes them — "similar products" / "frequently bought together" blocks (**XS-7**, nullable).

**Computation.** Three sub-components combined by noisy-OR (they are near-independent channels;
noisy-OR is bounded, monotone and saturates rather than exceeding 1):

a) **Category co-listing.** Over leaf categories with ≥5 listings in the sample, build each
   brand's listing-count distribution and take Ruzicka similarity:
   `cat_sim = Σ_c min(l_a(c), l_b(c)) / Σ_c max(l_a(c), l_b(c))`.
   The ≥5-listings floor exists because marketplace categories are seller-assigned and noisy;
   a single mis-categorised listing must not link two unrelated brands.

b) **Same-search-result adjacency.** Over brand-agnostic marketplace queries only (same guard
   as S4), counting both brands within the first `min(|R|, 60)` results:
   `q_adj = 1 - exp(-k / 3)`. The 60-result cut-off reflects that beyond roughly three screens
   of results, co-presence reflects catalogue size rather than adjacency.

c) **Seller carriage.** `carry = 2 * |S_a ∩ S_b| / (|S_a| + |S_b|)` over sellers with ≥2
   listings each. Contribution capped: `seller_term = min(carry, 1.0) * 0.30`. Capped because a
   high-volume reseller carries everything; seller carriage measures distribution breadth, not
   competitive relationship.

```
scale = 1 - (1 - cat_sim) * (1 - q_adj) * (1 - seller_term)
```

d) **Conditional bonus.** If a "similar products" block for a lists b (or vice versa), add
   evidence rows with `detail.block = 'similar'`; if a "bought together"/bundle block does,
   `detail.block = 'together'`. These feed §4.2's discriminator D5 and are *conditional* on
   collector capability — the detector must run correctly when the fields are absent
   (**OQ-4**).

**Output.** `unit=COMMERCE_ITEM` per contributing listing (`weight 0.60`), plus
`unit=RESEARCH_QUERY` per marketplace query.

**Failure mode.** Marketplace taxonomies are coarse and seller-assigned; sellers mis-categorise
deliberately for visibility. "Same shelf" on a marketplace is literally the `same_shelf`
relationship, which is *not* substitution — S5 therefore types `same_shelf` readily and
`direct_substitute` never. If `APIFY_TOKEN` is missing, this entire signal is unavailable and
the `COMMERCE_SURFACE` group drops out of the available set (§7).

---

### S6 — `recurring_mention`  (group: CO_PRESENCE)

**Input.** Text and audio mention spans with resolved brands: transcript spans (with ms
offsets, **XS-3**), caption, on-screen text, hashtags, comments.

**Computation.**
1. **Mention window.** Two mentions co-occur when they are in the same sentence, or ≤25 tokens
   apart, or ≤8s apart in the transcript. 25 tokens ≈ one or two spoken sentences; 8s is
   roughly two sentences of speech. Beyond that the two brands are being discussed separately
   in the same content, which is S2's claim, not S6's.
2. Item strength: `max over co-mention windows in the item of`
   `source_weight(span_a) * source_weight(span_b)` (max, not sum — one item, one observation).
3. Corpus normalisation: the same npmi as S2 but computed over *mention* co-occurrence, with
   `n_ab >= 3`. **The brand-agnostic stratum restriction and the 200-item stratum floor from S2
   apply here unchanged** — S6's marginals are biased by query provenance in exactly the same
   way, and more strongly, because a query naming a brand all but guarantees that brand is
   mentioned in the result.
4. **Creator breadth:** `breadth = 1 - exp(-distinct_creators / 4)`.
   `scale = clamp(npmi, 0, 1) * breadth`.
   **Why 4:** a verbal pattern on 1 creator is that creator's habit (breadth 0.22, deliberately
   too low to reach any typing threshold); 4 independent creators is where the pattern belongs
   to the discourse rather than to a person. 4 creators → 0.63, 8 → 0.86.

**Output.** `unit=CONTENT_ITEM`, `detail = {spans, sources, quote}`. Hashtag-only items carry
`detail.hashtag_only = true`, strength capped at 0.60, and are excluded from typing rules.

**Failure mode.** Hashtag stuffing and comment spam manufacture co-mention; handled by
`MENTION_SOURCE_WEIGHT` and the hashtag-only exclusion. ASR mis-transcription creates phantom
mentions, partly absorbed by the resolution cascade's phonetic handling. A creator naming a
competitor in a disclaimer ("not sponsored by …") is a genuine category association but not a
competitive behaviour — it is kept, and S7 is what distinguishes its *relation*.

---

### S7 — `substitution_language`  (group: EXPLICIT_COMPARE)

**Input.** Mention windows containing ≥2 distinct resolved brands (from S6's windowing), across
transcript, caption, on-screen text and comments; plus `commerce_reviews` text where a review
on brand a's listing mentions brand b.

**Pre-filter.** Keep a span when it has ≥2 brands **and** (a structural pattern hit from
`config/lexicons/relation_structural.yaml` **or** a hit in the bootstrapped `substitution_cues`
table). Structural patterns are morphological/syntactic only: comparative/superlative
morphology; adversative and alternative coordination; negation followed by an alternative;
verbs of change-of-state adjacent to two brand spans; coordinating conjunction directly between
two brand spans. **No lexical content ships.**

**Computation.** Batched LLM span classifier (`claude-opus-5`, `effort="low"`, 25 spans/call):

```python
class SpanRelation(StrEnum):
    SUBSTITUTION = "substitution"            # one replaces/stands in for the other
    PREFERENCE_OVER = "preference_over"      # one chosen over the other
    CO_CONSUMPTION = "co_consumption"        # both used/consumed together
    MERE_LISTING = "mere_listing"            # enumerated without relation
    COMPARISON_NEUTRAL = "comparison_neutral"
    UNRELATED = "unrelated"

class SpanVerdict(BaseModel):
    span_index: int
    relation: SpanRelation
    brand_key_from: str | None = None   # replaced / dispreferred side
    brand_key_to: str | None = None     # replacement / preferred side
    cue: SpanRelation | None = None
    strength_cue: Literal["explicit", "hedged", "implied"]
    quote: str = Field(max_length=240)  # VERBATIM from the supplied span
    discovered_cue_phrase: str | None = Field(default=None, max_length=60)
    confidence: float = Field(ge=0.0, le=1.0)

class SpanBatch(BaseModel):
    verdicts: list[SpanVerdict]
```

Prompt skeleton:

```
SYSTEM
You classify the relationship a speaker or writer expresses between two commercial brands
inside a short text span. The text may mix languages within a sentence; interpret it as
written, and never translate the quote.

Return exactly one relation per span:
- SUBSTITUTION: one brand takes the place of the other, or replaces it.
- PREFERENCE_OVER: one is chosen over the other in a single-slot choice.
- CO_CONSUMPTION: both are used, consumed or combined together in the same episode.
- MERE_LISTING: both named with no expressed relation.
- COMPARISON_NEUTRAL: compared with no stated outcome.
- UNRELATED: the two brands are not in relation in this span.

Hard rules:
1. `quote` must be copied verbatim from the span. If you cannot copy a span of text that
   carries the relation, return UNRELATED.
2. Do not infer a relation from outside knowledge about these brands, their categories,
   their markets or their consumers. Use only the span and its context.
3. SUBSTITUTION and CO_CONSUMPTION are opposites. If the span supports both readings,
   return COMPARISON_NEUTRAL and say so in `quote` selection.
4. Set `strength_cue` to `explicit` only when the relation is stated, `hedged` when it is
   qualified or conditional, `implied` when it rests on inference from phrasing alone.
5. In `discovered_cue_phrase`, copy the single short phrase (<= 60 chars, verbatim, any
   language) that carried the relation, so it can be reused later. Leave null if none.

USER
## Span {{i}}
platform: {{PLATFORM}}   source: {{SPAN_SOURCE}}   sponsored estimate: {{SPONSORED}}
brands present: [{{BRAND_KEY_A}}, {{BRAND_KEY_B}}]
text: "{{SPAN_TEXT}}"
surrounding context: "{{CONTEXT}}"
```

Note what the prompt does **not** contain: no cue word list, no example phrasing, no category,
no locale assumption beyond "may mix languages". The cue lexicon is *produced* by this prompt
(`discovered_cue_phrase` → `substitution_cues`) and is then reusable as a cheap pre-filter on
later runs. That is the §34-safe way to have a lexicon at all.

Item strength:
```
conf_w   = 1.00 if confidence >= 0.85 else 0.75 if confidence >= 0.70 else 0.50   # §2.0.4
cue_w    = {"explicit": 1.00, "hedged": 0.70, "implied": 0.45}[strength_cue]
strength = cue_w * source_weight * conf_w
         * (0.5 if sponsored_likelihood >= 0.60 else 1.0)
```
A verdict whose `quote` fails the verbatim check (§2.0.4) produces no evidence row at all,
regardless of `relation` or `confidence`.
Sponsored halving: a paid switch narrative is marketing, not observed consumer behaviour.
Kept rather than dropped (it *is* competitive activity) but at half weight, and flagged so
`Q_sponsored` can also act.

`direction`: `+1` if `brand_key_to` is the canonically-second brand, `-1` otherwise, `0` for
symmetric relations. Aggregated into
`direction_balance = (Σ dir) / (Σ |dir|)` when `Σ |dir| > 0`, and **NULL** otherwise — a pair
whose every relation span is symmetric has no direction, which is not the same as a balanced
one, and the column is nullable for exactly this reason.

`PairScale.scale = 1.0`.

**Failure mode.** Sarcasm and irony invert the stated relation and the model will sometimes
take them literally — mitigated only partially by requiring a verbatim quote (a human can
check it in one glance, which is the real mitigation). Hedges in code-switched speech are
easy to over-read as explicit. Comment-section claims are third-party. And sponsored
"switch" narratives are manufactured. This is nonetheless the **single strongest
discriminator** between substitution and co-consumption, because it is the only signal that
observes the *relation* rather than the *co-presence*.

---

### S8 — `ranking_review`  (group: EXPLICIT_COMPARE)

**Input.** (i) S1 verdicts with `kind = RANKING` and populated `rank_a/rank_b/list_length`;
(ii) Serper web results that are listicle/review documents mentioning ≥2 resolved brands;
(iii) `commerce_reviews` on brand a's listing that mention brand b.

**Computation.** For each ranked list `L` containing both brands:
```
adjacency = 1 - |rank_a - rank_b| / max(1, |L| - 1)
strength  = adjacency                      # NOT adjacency * tier_weight(unit)
```
`strength` carries evidence quality only. The evidence-tier weight is applied once, by §3.2's
`raw_s = Σ e.strength * e.weight * recency(e)`, from `PairEvidence.weight`. An earlier draft
folded `tier_weight` into `strength` as well, which squared it — a web document at tier 0.50
would have contributed `0.25×`, and no other detector did this, so the same evidence quality
scored differently depending on which detector emitted it.
Eligibility: `|L| <= 25`. **Why 25:** beyond ~25 entries the document is enumerating a whole
category for SEO rather than presenting a considered set, so co-membership carries almost no
discriminative information. **Boundary:** a list of exactly 25 is kept; 26+ is recorded with
`detail.list_too_long = true` and strength 0.

**`rank_a`, `rank_b` and `list_length` are numbers an LLM produced, and they go straight into a
score — so code validates them before they are used** (see also §2.0.4). A rank triple is
accepted only when all hold: `1 <= rank_a, rank_b <= list_length`; `rank_a != rank_b`;
`list_length` is within ±1 of the count of ordinal markers a deterministic scan
(`^\s*\d+[.)]`, `#\d+`, `No\.?\s*\d+`) finds in the supplied document text; and the quote
carrying each rank is an exact substring of that text. A triple failing any check is discarded,
the verdict is demoted to `kind = multi_review` (which has no rank term), and the rejection is
counted in `detail.rank_validation_failures`. Without this the adjacency term is a model
hallucination with two decimal places.

```
scale = 1.0   if shared_lists >= 2
      = 0.55  if shared_lists == 1
```
Why: a single listicle is one author's framing; two independent lists placing both brands in a
short considered set is a genuine signal about the category's consideration structure.

Web documents carry `weight = 0.50` and `detail.evidence_tier = 'third_party_web'` so the UI
can disclose that the evidence is not first-party observed content.

**Failure mode.** SEO and affiliate content fabricates adjacency wholesale and is often
machine-generated, cross-market, or years old. Locale/recency filtering is applied by the web
collector, but residual contamination is likely; the 0.50 weight and the explicit
`third_party_web` disclosure are the honest responses, not a claim that the problem is solved.
Marketplace reviews that name another brand are often seller-planted.

---

### S9 — `positioning_similarity`  (group: COMMERCE_SURFACE)  — AGGREGATE ONLY

**Input.** Per brand, assembled from **its own** records (never from the competitor's, so the
measure is not circular): marketplace price distribution, discount depth, rating distribution,
observed variant count, pack-size distribution, and a positioning-text centroid built from that
brand's own listing titles/descriptions plus the descriptor text of its own social content.

**Computation.** Components, each `NULL` when its data requirement is unmet; weights are
renormalised over the available components rather than imputing values.

```
price_sim  = exp( -|ln(p_a / p_b)| / 0.35 )           requires >= 8 priced listings per brand
embed_sim  = max(0, (cos(c_a, c_b) - FLOOR) / (1 - FLOOR))
struct_sim = 1 - mean(|z_a(f) - z_b(f)|) over available f in
             {discount_depth, variant_count, mean_rating}, clipped to [0,1]

scale = (0.45*price_sim + 0.35*embed_sim + 0.20*struct_sim) / (sum of available weights)
```
- `p_x` = median price **per normalised unit** within the brand's modal leaf category; when the
  unit cannot be extracted from the title, the within-category price *percentile* is used
  instead and `detail.price_basis = 'percentile'`.
- **`0.35` in log space:** a 35% price gap gives `price_sim ≈ 0.37`; ±10% gives ≈ 0.75. Price
  tiers within a category are typically separated by more than 30%, so 0.35 places the
  half-point near the tier boundary rather than inside a tier.
- **`>= 8 priced listings`:** a median over fewer than 8 listings moves by more than a tier
  when one listing changes; below 8 the component is dropped, not estimated.
- **`FLOOR` for the embedding rescale:** `text-embedding-3-large` cosines *within one product
  domain* compress into roughly `[0.75, 0.98]`, so an unrescaled cosine scores every pair ~0.9
  and carries no information. `FLOOR` is **calibrated per corpus** as the 20th percentile of
  same-leaf-category brand-pair cosines, recomputed each run and stored in
  `competitor_signal_scores.detail`. The shipped default of 0.80 is a placeholder — **OQ-1**.

**Output.** One row, `unit=AGGREGATE`, `evidence_id=NULL`, `is_aggregate=true`,
`canonical_item_ids=()`. Consequences, by design: S9 contributes **zero** to the distinct-item
evidence count, **zero** to the diversity walk, and its strength is **halved** when entering
its group (§3.2). It can therefore never create a relationship by itself.

**Failure mode.** In a tight category every brand looks similarly positioned, so the measure
has low discriminative power and high ceiling-hugging. It is also symmetric and
corpus-coverage-dependent. Its legitimate use is corroboration and the §24 price comparison,
not relationship detection — which the aggregate treatment enforces mechanically rather than
by convention.

---

### S10 — `shared_creator`  (group: DISTRIBUTIONAL)

**Input.** `creators`, their content items, resolved brands at prominence ≥ `secondary`,
`sponsored_likelihood` per item, creator follower percentile within the sample.

**Computation.**
1. `C_x` = creators with ≥1 item featuring brand x at prominence ≥ secondary.
2. **Decorrelation at the detector level** — the important move. Partition shared creators:
   - *same-item* sharers (the creator featured both brands in one item) are already counted by
     S2 and are **excluded here**;
   - *independent* sharers (the creator's a-items and b-items are disjoint) are the evidence.
   This is a deliberate removal of the S2↔S10 overlap at source, so the §3 machinery is not
   asked to fix something the detector can avoid creating.
3. `shared = |independent sharers|`; require `min(|C_a|, |C_b|) >= 5` — below that the overlap
   ratio is unstable (1 shared creator out of 2 reads as 0.5).
4. `overlap_coef = shared / min(|C_a|, |C_b|)`.
5. `organic_share` = share of the shared creators' relevant items with
   `sponsored_likelihood < 0.60`.
```
scale = overlap_coef * (1 - exp(-shared / 3)) * (0.5 + 0.5 * organic_share)
```
6. **Mega-creator exclusion:** creators whose distinct-brand count exceeds the 95th percentile
   of the sample's creator brand-breadth distribution are dropped (they touch everything). The
   excluded count goes in `detail.creators_excluded`.

**Output.** `unit=CREATOR` per independent sharer, `strength = min(1.0, items_a * items_b / 4)`
capped at 1.0, `canonical_item_ids` = that creator's a-items and b-items.

**Failure mode.** Agency rosters, MCN networks and "brand-safe creator" lists make unrelated
brands share creators for commercial reasons; the `organic_share` term and the mega-creator
exclusion blunt but do not remove this. Cross-platform creator identity is unresolved
(**OQ-5**), so one person on two platforms counts twice and inflates `shared`.

---

### S11 — `shared_cultural`  (group: DISTRIBUTIONAL)  — AGGREGATE ONLY

**Input.** The open-ended discovered labels per item (occasions, behaviours, cultural signals,
humour signals, slang, social context, activities, product pairings) normalised to canonical
label ids by the taxonomy subsystem (**XS-5**, which must also expose each label's corpus
prevalence).

**Computation.** Build each brand's label distribution over distinct items. Eligibility:
- labels with ≥5 distinct items corpus-wide (below that the label is not yet a stable concept);
- labels with corpus prevalence < 0.40 (a near-universal label carries no information — an
  IDF-style filter);
- each brand must have ≥10 labelled items, else the signal is `NULL`.

```
P_a, P_b  = label distributions, renormalised over eligible labels
scale     = 1 - sqrt( JSD(P_a || P_b) )        # Jensen-Shannon distance, already in [0,1]
```
**Why Jensen–Shannon rather than cosine:** JSD is bounded, symmetric, and sensitive to
distribution *shape* rather than magnitude, so a high-volume brand and a low-volume brand with
the same cultural profile score high — which is the intended semantics ("they are associated
with the same things") and not "they have similar volume".

**Output.** One `unit=AGGREGATE` row, `is_aggregate=true`, with
`detail = {top_shared_labels: [...], per_label_item_ids: {...}, jsd: ...}` for drill-down.
Same aggregate consequences as S9: no item count, no diversity credit, strength halved.

**Failure mode.** Labels are model-generated, so S11 measures agreement between two model
outputs as much as agreement in the world. It also overlaps conceptually with S3 (clusters are
built from the same content) and semantically with `co_consumed` (sharing an occasion is close
to being consumed together) — which is why it sits in the DISTRIBUTIONAL group with S3 and why
it cannot type a relationship.

### 2.12 Signal summary table

Read the last column as *"which typing rule this signal can satisfy the evidence clause of"* —
no signal types a relationship on its own, because every rule in §4.1 additionally requires the
pair to clear a confidence gate computed from all five groups (`>= 0.15` for R1–R3,
`>= 0.35` for R4/R5).

| # | signal | group | unit | min support | pair scale from | satisfies the evidence clause of |
|---|---|---|---|---|---|---|
| S1 | direct_comparison | EXPLICIT_COMPARE | content_item | 1 | — | `direct_substitute` with ≥2 items |
| S2 | co_appearance | CO_PRESENCE | content_item | 3 | npmi × simultaneity | no |
| S3 | shared_cluster | DISTRIBUTIONAL | cluster | 1 (2 for full) | Ruzicka | `category_peer` with ≥2 clusters |
| S4 | category_search | DISCOVERY_SURFACE | research_query | 1 | breadth × coverage | `category_peer` at ≥3 queries |
| S5 | marketplace_adjacency | COMMERCE_SURFACE | commerce_item | 3 | noisy-OR of 3 parts | `same_shelf` |
| S6 | recurring_mention | CO_PRESENCE | content_item | 3 | npmi × creator breadth | no |
| S7 | substitution_language | EXPLICIT_COMPARE | content_item | 1 | — | `direct_substitute` / `co_consumed` |
| S8 | ranking_review | EXPLICIT_COMPARE | content_item / web_document | 1 | lists ≥2 | `category_peer` at ≥2 lists |
| S9 | positioning_similarity | COMMERCE_SURFACE | aggregate | — | component mix | **never** |
| S10 | shared_creator | DISTRIBUTIONAL | creator | 5 per brand | overlap × breadth × organic | no |
| S11 | shared_cultural | DISTRIBUTIONAL | aggregate | 10 items/brand | 1 − √JSD | **never** |

---

## 3. EVIDENCE AGGREGATION WITHOUT DOUBLE COUNTING

### 3.0 The problem, stated precisely

Three distinct kinds of double counting exist here, and they need three distinct remedies.

| kind | example | remedy |
|---|---|---|
| **Same evidence written twice** | the same video reaches S2 twice via two frame detections | a unique index on the evidence ledger (§3.1) |
| **Correlated signals from the same items** | S2 co-appearance and S3 shared-cluster derive from the same five videos | correlation groups with within-group `max`, plus the item-disjointness walk (§3.2–3.3) |
| **Volume mistaken for independence** | 40 evidence rows that are 40 facts about 6 videos | evidence counted over **distinct content items**, with diversity measured separately (§3.3–3.4) |

### 3.1 Structural guarantee: the evidence ledger is the unit of truth

Every non-aggregate detector writes to `competitor_signal_evidence`, one row per
(pair, signal, unit, evidence_id, span). The unique index makes a duplicate write a no-op
update rather than a second count:

```sql
create unique index cse_uq on competitor_signal_evidence
  (brand_id, competitor_brand_id, signal, evidence_unit, evidence_id, (detail->>'span_id'))
  nulls not distinct;
```

The brief's `evidence_count` is then computed, never accumulated. `canonical_item_ids` is a
first-class `uuid[]` **column** on the ledger (§5.2), not a key inside `detail` — an earlier
draft read it out of `detail`, which would have returned `NULL` for every row and silently
reported `evidence_count = 0` for every cluster-, creator- and query-unit pair:

```sql
select count(distinct canon.item_id) as evidence_count
from competitor_signal_evidence e
cross join lateral unnest(
    case when e.evidence_unit in ('content_item','commerce_item','commerce_review','web_document')
         then array[e.evidence_id]
         else e.canonical_item_ids
    end) as canon(item_id)
where e.brand_id = $1 and e.competitor_brand_id = $2 and canon.item_id is not null;
```

**`evidence_count` is a property of the pair; a typed claim needs the count behind *that
type*.** `competitor_signal_evidence` carries no `relationship_type` (correctly — one piece of
evidence can support more than one reading), so the query above is pair-scoped. But
`competitor_relationships` has one row per `(pair, type)`, and T14.a renders
*"Relationship read as {relationship_type_phrase} … across {evidence_count} distinct items"*.
Writing the pair-wide count there would put a 40-item sample size next to a claim that rests on
5 items, which is precisely the §28 failure the field exists to prevent. Two columns, therefore:

- `evidence_count` — distinct items behind the **pair**, as computed above. This is the brief's
  §23 field and it is unchanged.
- `type_evidence_count` — distinct items in `TypingDecision.required_evidence_ids`, i.e. the
  items that actually satisfied the rule that produced this type. Written by the `type` stage.

**Every rendered statement about a relationship's *type* uses `type_evidence_count`**; statements
about the pair's existence use `evidence_count`. The phrasing registry (§7.3) enforces this by
requiring the slot name to match the template, and the two are shown together in the UI
("5 items support this reading, of 40 items linking the two brands").

Note the two deliberate decisions: `evidence_count` counts **distinct items, not signal rows**,
and it is **unweighted** (no recency, no tier weighting). It is the honest "how many real
pieces of content is this based on" number that the UI must show next to every claim. The
*decayed, weighted* quantity is `n_eff` and is a separate column — so the UI can say
"17 items; strongest evidence is four months old" rather than conflating the two
(**OQ-8** notes this as a deliberate reading of the brief's field).

### 3.2 Per-signal strength, then correlation groups

**Step 1 — per-signal strength from item evidence.**
```
raw_s = Σ_{e in E_s} e.strength * e.weight * recency(e)
sig_s = pair_scale_s * (1 - exp(-raw_s / 3))
```
- `recency(e) = 0.5 ** (age_days(e) / H)`, `H = 90` days (§5.3).
- `1 - exp(-raw/3)`: saturating and monotone in **both** per-item quality and item count.
  Scale 3 means "three solid items express this signal to ~63%". A plain noisy-OR
  (`1 - Π(1-s_i)`) was rejected here: with per-item strengths around 0.6 it reaches 0.99 by the
  fifth item, making the signal strength degenerate and pushing all discrimination into the
  volume term. The exponential form keeps strength informative across the 1–15 item range,
  which is where most real pairs live in a ~1500-item sample.

`recency(e)` reads `published_at` where the unit has one. For `cluster`, `creator`,
`research_query` and `aggregate` units there is no publication date; they take the **median
`recency` of their `canonical_item_ids`**, and 0 when that set is empty. A content or commerce
item with a *missing* `published_at` takes the **10th percentile recency of the dated items in
the same run**, not `recency = 1.0` via `detected_at`. Falling back to `detected_at` gives every
undated item full weight, which systematically over-weights exactly the platforms and actors
that do not expose dates and lets a pair's recency be improved by re-collecting it.

For aggregate signals (S9, S11): `sig_s = 0.5 * detector_strength`. The 0.5 is not a fudge — it
encodes that a distributional measurement is a *property of the two brands* rather than
*evidence about their relationship*, so it can corroborate but must not carry a channel on its
own.

**Corroboration clamp on aggregate signals.** The halving alone does not deliver that property,
because step 2 takes a `max` within the group: an aggregate signal with *no item evidence at
all* could still set its group's strength, and unlike every item-based signal it is subject to
neither the `1 - exp(-raw/3)` saturation nor recency weighting. S9 at `detector_strength = 0.9`
would hand `COMMERCE_SURFACE` a 0.45 off zero items. So, within a group `g`:
```
item_based_max = max(sig_s for item-based s in g)        # 0 if none fired
agg_max        = max(sig_s for aggregate s in g)         # already halved
g = 0.0                              if item_based_max == 0
  = min(agg_max, 1.5 * item_based_max) if agg_max > item_based_max
  = item_based_max                   otherwise
```
An aggregate may raise its group by at most half again over the best item-based signal, and may
never create a non-zero group on its own. In practice `V = 0` would also have annihilated such a
pair (aggregates contribute no distinct items), but relying on that is relying on a side effect
of another term; this makes the stated property — *S9 and S11 can corroborate, never create* —
mechanical in the place it is claimed.

**Step 2 — group strength.**
```
g = max over signals s in group g of sig_s
```
**`max`, not sum.** Within a group the signals are near-duplicates of each other's cause
(S2 and S6 are both "same item co-presence"; S3, S10 and S11 are all "similar distribution
over the same content"; S1, S7 and S8 all read comparative language). Summing them would count
one underlying fact up to three times. Taking the maximum means a group contributes exactly as
much as its best-evidenced member, which is the correct reading of correlated evidence.

Group membership, with the shared cause that makes each group internally correlated:

| group | signals | shared cause |
|---|---|---|
| `CO_PRESENCE` | S2, S6 | the two brands appear in the same analysed item |
| `EXPLICIT_COMPARE` | S1, S7, S8 | someone stated a comparative/substitutive relation in text |
| `DISTRIBUTIONAL` | S3, S10, S11 | the two brands' content profiles resemble each other |
| `COMMERCE_SURFACE` | S5, S9 | the two brands occupy the same marketplace surface |
| `DISCOVERY_SURFACE` | S4 | the two brands surface for the same brand-agnostic entry point |

Across groups the causes are materially different: a marketplace category assignment, a
creator's spoken sentence, a search engine's ranking, and an embedding cluster are produced by
different processes. Treating them as approximately independent is defensible; treating two
members of one group as independent is not.

**Step 3 — overall strength.** Quadratic power mean over the **available** groups:
```
S = sqrt( Σ_g g² / n_available_groups )
```
- Bounded in `[0,1]`, monotone in every `g`, and `S = g` when all groups equal `g`.
- `p = 2` makes strong channels count more than weak ones **without** a single channel
  saturating the result. Worked contrast: groups `(0.9, 0, 0, 0, 0)` → `S = 0.402`, whereas
  noisy-OR would give `0.900`. One channel, however strong, is deliberately not "strong
  overall" — exactly the brief's posture. Groups `(0.5, 0.5, 0.5, 0.5, 0.5)` → `S = 0.500`.
- Dividing by `n_available_groups` (not by 5) means a run that *cannot* reach a channel — no
  `APIFY_TOKEN`, so no commerce — is not punished twice. The source-coverage penalty is applied
  once, explicitly and attributably, in `Q_coverage` (§3.5).

### 3.3 Diversity: the item-disjointness walk

Diversity must answer "how many *genuinely different* lines of evidence are there?", which is
not "how many signals fired". Five groups firing off the same two videos is one line of
evidence wearing five hats.

```python
# aggregate.py
def diversity(rows: Sequence[EvidenceRow], group_strengths: Mapping[CorrelationGroup, float],
              *, n_available: int, min_group_strength: float = 0.25) -> float:
    used: set[UUID] = set()
    total = 0.0
    for g, gs in sorted(group_strengths.items(), key=lambda kv: (-kv[1], kv[0].value)):
        if gs < min_group_strength:
            continue
        items = canonical_items(rows, group=g)      # resolves cluster/creator/query -> items
        new = items - used
        total += min(1.0, len(new) / 2.0)
        used |= items
    return max(1.0 / n_available, total / n_available)
```

Three mechanics, each doing specific work:

1. **`canonical_items` resolves every unit down to underlying item ids.** A `cluster` unit
   expands to its member items containing both brands; a `creator` unit to that creator's
   relevant items; a `research_query` unit to its result items containing both brands;
   `aggregate` units expand to the empty set. Without this, the walk is vacuous: cluster ids
   and commerce ids never collide with content ids, so every group would always look novel.
2. **Greedy walk in descending group strength, ties broken by group name.** The strongest group
   claims its items first; later groups are credited only for what they add. The explicit
   `(-strength, group.value)` sort key matters: with a bare `-strength` sort, two groups of equal
   strength are ordered by dict insertion, so the same inputs can yield different `D` on
   different runs, and `D` feeds a stored confidence. Greedy-by-strength is otherwise the
   auditable choice: the claimed/novel split is reproducible and is stored in `group_strengths`
   and `detail` for the audit view.
   `min_group_strength = 0.25` excludes groups too weak to be a *line of evidence*; 0.25 is the
   point at which a group's own evidence could not on its own clear the 0.15 write threshold.
   It is a hard cut-off and therefore discontinuous at the boundary — a group at 0.249 earns
   nothing and one at 0.251 earns up to `1/n_available` — which is visible in `detail` and is
   listed in §11 as a calibration item (OQ-13).
3. **`min(1, |new| / 2)`.** A group needs **2 novel items** for full diversity credit, 1 novel
   item earns 0.5, 0 earns nothing. **Why 2:** one novel item is as likely to be an incidental
   extra appearance as a second line of evidence; two novel items is the minimum that makes the
   group's claim independently checkable. **Boundary:** at exactly 1 novel item the group counts
   half and the UI can show "partly overlapping evidence".
4. **Floor at `1/n_available`.** A relationship that exists at all has at least one channel.
   Without the floor, `D = 0` would annihilate confidence through the geometric mean and a
   genuine single-channel relationship would score exactly zero — wrong, because single-channel
   evidence is weak, not absent.

### 3.4 Volume

```
n_eff = Σ over distinct items i of  tier_weight(i) * recency(i)
      where tier_weight: social content item 1.00, commerce item/review 0.60, web document 0.50
V     = 1 - exp(-n_eff / 8)
```
- Counted over **distinct items** (an item contributes once no matter how many signals used it).
- **Scale 8.** `n_eff = 3` → 0.31, `8` → 0.63, `16` → 0.86, `24` → 0.95. Eight distinct pieces
  of evidence is roughly 0.5% of a ~1500-item social sample; below that a reader should not
  treat the pair as a pattern, and §28 requires the system to behave that way rather than
  merely annotate it. Above ~24 items, more evidence does not make the relationship's
  *existence* more certain, so saturation is correct — additional volume shows up in
  `evidence_count`, which is displayed unsaturated.
- This is the single tunable most likely to need recalibration once real corpus statistics
  exist; it is exposed as `AggregationConfig.volume_scale`.

### 3.5 Quality factors

```python
@dataclass(frozen=True, slots=True)
class QualityFactors:
    resolution: float      # min(resolution_quality of the two brands), floored 0.50
    temporal: float        # 1.00 if evidence spans >= 2 distinct publication months;
                           # 0.85 otherwise, INCLUDING when publication dates are
                           # unavailable. Collection days are not a substitute.
    contradiction: float   # 1 - 0.5 * contradiction_share
    sponsored: float       # 1 - 0.3 * sponsored_only_share
    coverage: float        # sqrt(n_available_groups / 5)
    def product(self) -> float: ...
```

- **`resolution`** — a relationship cannot be more trustworthy than the entities it connects
  (§1.10). Floored at 0.5 so a shaky entity degrades the claim without erasing it.
- **`temporal` 0.85 for single-burst evidence** — a single viral moment can manufacture
  co-appearance across many items that are really one event. Only a 15% penalty because a burst
  is still real evidence of association; the stronger statement (growth/decline) is forbidden
  elsewhere by §22 anyway. An earlier draft also granted full credit for *"≥2 distinct collection
  days"*. That is a property of when **we** ran the collector, not of when the content was
  published: a run that straddles midnight would have given every pair in the corpus
  `temporal = 1.00`. Only publication dates count, and an undated evidence set takes the 0.85
  penalty rather than being assumed temporally spread.
- **`contradiction`** — `contradiction_share` = minority-relation span items / all
  relation-typed span items for the pair. §28 requires conflicting evidence to be preserved and
  shown, not resolved away; this term makes contradiction *reduce confidence* rather than be
  hidden, and the minority evidence is still displayed.
- **`sponsored`** — `sponsored_only_share` = share of evidence items with
  `sponsored_likelihood >= 0.60` and no organic counterpart for the same pair. Capped at a 30%
  penalty because sponsored content is genuine competitive activity, just not consumer
  behaviour.
- **`coverage`** — `sqrt(available/5)`: 5/5 → 1.00, 4/5 → 0.89, 3/5 → 0.77, 2/5 → 0.63. This is
  how a missing source (`APIFY_TOKEN` absent → no commerce surface) honestly lowers confidence
  without distorting `S` or `D`. With 3 of 5 groups reachable, confidence is capped around 0.77
  before any other factor, so a degraded run **cannot** reach the `very_strong` band. That is
  the intended behaviour, and the UI names the unavailable sources.

### 3.6 The confidence formula

```
confidence = S^0.50 * V^0.30 * D^0.20 * Q
             where Q = Q_resolution * Q_temporal * Q_contradiction * Q_sponsored * Q_coverage
confidence = min(confidence, typing_cap)      # §4.3; VISUAL_ONLY/COMMERCE cap = 0.549
```
**The cap is 0.549, not 0.55.** The band column (§3.9) is `confidence >= 0.55 -> 'strong'`, so a
cap of exactly 0.55 clamps a capped relationship *into* the band the cap exists to keep it out
of — the stated intent ("capped at the top of the moderate band … can never be displayed as
strong") was defeated by an off-by-one on an inclusive boundary. `TypingConfig.visual_cap =
0.549` and `commerce_cap = 0.549`, both strictly below 0.55 and representable in
`numeric(4,3)`. Elsewhere in this document the cap is written 0.55 for readability; the constant
is 0.549 and there is a unit test asserting `band(min(x, visual_cap)) != 'strong'` for all x.

**Why a weighted geometric mean of the three bounded terms (exponents summing to 1.0).** The
product form means any term near zero drags confidence down, which is the required behaviour: a
pair with enormous volume but one correlated channel must not reach high confidence, and
neither must a pair with five channels and three items. An arithmetic mean would let a single
large term compensate for a near-zero one, which is precisely the failure the brief's
guardrails are about. Exponents summing to 1 keep the result in `[0,1]` and make it
scale-interpretable: if all three terms equal `t`, confidence is `t · Q`.

Exponent choice:
- **`S` at 0.50** — strength encodes the detectors' own calibrated beliefs plus corpus
  surprise, and is the term closest to "is this relationship real".
- **`V` at 0.30** — volume is necessary but saturating; over-weighting it would reward
  high-frequency brand pairs for being frequent.
- **`D` at 0.20** — diversity's floor is `1/5 = 0.2`, so `D^0.20 = 0.725`: a single-channel
  relationship is penalised by about 27% rather than annihilated. `D = 0.4 → 0.833`,
  `0.6 → 0.903`, `1.0 → 1.0`. Diversity is deliberately the smallest exponent because `S`'s
  power-mean form **already** penalises having few channels; `D` adds the distinct penalty for
  channels that are not *item-independent*. Two different failure modes, two terms.

**Write threshold.** A pair with `confidence < 0.15` is **not** written to
`competitor_relationships`; its evidence rows remain and it appears in the audit view as
"weak co-occurrence, not reported". **Why 0.15:** below that the pair is indistinguishable from
background co-occurrence in the corpus, and writing the long tail would turn the Competitive
Landscape into exactly the vanity-count/word-cloud surface §33 forbids. **Boundary:** at
exactly 0.15 the relationship is written with band `low` and appears only under an explicitly
labelled "weak signals" disclosure.

### 3.7 Worked example A — a well-evidenced pair (Brand A ↔ Brand B)

Corpus: `N = 1500` social items with ≥1 resolved brand (of which 640 fall in the brand-agnostic
stratum used for npmi), 2500 commerce records, all five groups available.

Every signal below goes through the **same** §3.2 step 1 — `raw_s = Σ strength·weight·recency`,
then `sig_s = pair_scale · (1 − exp(−raw_s/3))`. An earlier version of this example skipped the
saturation term for S3/S4/S5/S8/S10 and applied `recency = 1.0` inside `raw_s` while using 0.85
in `n_eff`; the result overstated confidence by about 20 points and made the §3.9 band
descriptions unreachable. Recencies here: social 0.85, commerce 0.90, web 0.70.

| signal | evidence | `Σ strength` | `weight` | `rec` | `raw_s` | sat | `pair_scale` | `sig_s` |
|---|---|---|---|---|---|---|---|---|
| S2 co_appearance | 9 items @ 0.6 | 5.40 | 1.00 | 0.85 | 4.59 | 0.783 | npmi 0.42 × sim 0.88 = 0.370 | **0.290** |
| S6 recurring_mention | 4 items (3 speech, 1 comment) | 3.05 | 1.00 | 0.85 | 2.59 | 0.579 | npmi 0.55 × breadth(3 cr.) 0.528 = 0.290 | **0.168** |
| S7 substitution_language | 3 items (explicit/hedged/implied) | 1.81 | 1.00 | 0.85 | 1.54 | 0.401 | 1.00 | **0.401** |
| S1 direct_comparison | 2 head-to-head items @ 0.90 | 1.80 | 1.00 | 0.85 | 1.53 | 0.400 | 1.00 | **0.400** |
| S8 ranking_review | 2 web lists, adjacency 0.90 | 1.80 | 0.50 | 0.70 | 0.63 | 0.189 | 1.00 (lists ≥ 2) | **0.189** |
| S3 shared_cluster | 3 shared clusters @ 0.70 | 2.10 | 0.80 | 0.85 | 1.43 | 0.379 | Ruzicka 0.38 | **0.144** |
| S10 shared_creator | 2 independent sharers (1.00, 0.50) | 1.50 | 0.70 | 0.85 | 0.89 | 0.257 | 0.22 | **0.057** |
| S11 shared_cultural | aggregate, 1−√JSD = 0.61 | — | — | — | — | — | — | 0.5 × 0.61 = **0.305** |
| S5 marketplace_adjacency | 22 listings @ 0.50 | 11.00 | 0.60 | 0.90 | 5.94 | 0.862 | noisy-OR 0.718 | **0.619** |
| S9 positioning | aggregate 0.70 | — | — | — | — | — | — | 0.5 × 0.70 = **0.350** |
| S4 category_search | 4 eligible queries @ 0.70 | 2.80 | 0.80 | 0.90 | 2.02 | 0.489 | breadth 0.736 | **0.360** |

Group strengths (`max` within group, then the §3.2 aggregate corroboration clamp):
- `CO_PRESENCE` = max(0.290, 0.168) = **0.290**
- `EXPLICIT_COMPARE` = max(0.401, 0.400, 0.189) = **0.401**
- `DISTRIBUTIONAL`: item-based max = max(0.144, 0.057) = 0.144; aggregate S11 = 0.305 exceeds it,
  so it is clamped to `1.5 × 0.144` = **0.216**. This is the clamp doing its job: a JSD between
  two label distributions should not out-vote the three cluster memberships and two shared
  creators that are the group's actual evidence.
- `COMMERCE_SURFACE`: item-based max = 0.619; aggregate S9 = 0.350 is lower, so **0.619**
- `DISCOVERY_SURFACE` = **0.360**

```
S = sqrt((0.290² + 0.401² + 0.216² + 0.619² + 0.360²) / 5)
  = sqrt(0.8043 / 5) = sqrt(0.16086) = 0.401          -> S^0.50 = 0.633
```

Disjointness walk, descending group strength, ties by group name, groups below 0.25 skipped
(`DISTRIBUTIONAL` at 0.216 is skipped and earns nothing):
`COMMERCE_SURFACE` 22 listings → 22 novel → **1.0**; `EXPLICIT_COMPARE` 5 items → 5 novel →
**1.0**; `DISCOVERY_SURFACE` 7 items, 3 claimed → 4 novel → **1.0**;
`CO_PRESENCE` 9 items, 7 claimed → 2 novel → **1.0**.
```
D = 4.0 / 5 = 0.800                                   -> D^0.20 = 0.956
```

Volume: 16 distinct social items (rec 0.85), 22 commerce listings (0.60 × 0.90), 2 web docs
(0.50 × 0.70):
```
n_eff = 13.60 + 11.88 + 0.70 = 26.18
V     = 1 - exp(-26.18/8) = 0.962                     -> V^0.30 = 0.988
```

Quality: `resolution min(0.95, 0.92) = 0.92`; `temporal 1.00` (evidence spans ≥2 publication
months); 1 co-consumption span against 3 substitution spans → `contradiction_share 0.25` →
`0.875`; 2 of 16 sponsored-only → `0.9625`; `coverage sqrt(5/5) = 1.00`.
```
Q = 0.92 * 1.00 * 0.875 * 0.9625 * 1.00 = 0.7748
confidence = 0.633 * 0.988 * 0.956 * 0.7748 = 0.464   -> band "moderate" (0.35 <= x < 0.55)
```
A pair with eleven signals firing across five channels, 40 distinct pieces of evidence and no
missing sources lands in **moderate**. That is the design working, not failing: `S` is held down
by the power mean (three of five groups are below 0.41), and `Q` strips a further 22% for an
imperfect entity resolution and a genuine contradiction in the evidence. What the reader is
shown is the decomposition, not the number — *"40 items across 5 channels; conflicting: 3 items
read as substitution, 1 as co-consumption; brand resolution quality 0.92"* — which is the
presentation §28 asks for. It also means the `strong` and `very_strong` bands are demanding; see
§3.9.4, where the boundaries are now flagged for calibration against run 1 rather than asserted.

### 3.8 Worked example B — a thin pair (Brand A ↔ Brand C)

Only S2 fires: 3 items, item strength 0.6, recency 0.90, npmi 0.30.
```
raw   = 3 * 0.6 * 1.00 * 0.90 = 1.62
sig_S2 = 0.30 * (1 - exp(-1.62/3)) = 0.30 * 0.417 = 0.125
groups: CO_PRESENCE 0.125 (< 0.25 -> no diversity credit); all others 0
S = sqrt(0.125² / 5) = 0.0559                         -> S^0.50 = 0.236
D = max(1/5, 0/5) = 0.200                             -> D^0.20 = 0.725
n_eff = 0.90 * 3 = 2.70 ; V = 1 - exp(-0.3375) = 0.286 -> V^0.30 = 0.687
Q = 0.95 (resolution) * 0.85 (single burst) * 1.0 * 1.0 * 1.0 = 0.8075
confidence = 0.236 * 0.687 * 0.725 * 0.8075 = 0.0949  -> below 0.15, NOT WRITTEN
```
The pair stays in the ledger and appears in the audit view as weak co-occurrence. That is the
right outcome: three background co-appearances is not a competitor finding.

### 3.9 Why this is a per-relationship confidence and not a competitor score

§24 forbids simplistic overall competitor scores and §31.3 forbids ranking competitors with
arbitrary scores. The distinction is not cosmetic, so it is enforced structurally.

**What `confidence` is.** An epistemic statement about *evidence*: "given this sample, how much
do we believe that this specific relationship, of this specific type, between these two brands,
exists?" It is monotone in evidence quantity, quality, independence and corroboration. It is
comparable across pairs **only** in the sense "better evidenced in this sample".

**What `confidence` is not, and cannot be.** It is not competitive threat, market share,
similarity magnitude, importance, momentum or priority. Those require share, sales, awareness,
distribution or pricing data that this sample does not contain. Any of them would be an
invented number, which §24 and §28 prohibit.

**Enforcement, not convention:**

1. **No single number per competitor exists in the schema.** `confidence` is keyed on
   `(brand_id, competitor_brand_id, relationship_type)`. A pair with three detected
   relationship types has three rows and three confidences. There is deliberately no
   `competitor_score` column, no per-competitor aggregate, and no view that produces one.
   **Stated precisely, because the weaker reading is the tempting one:** the *evidence belief*
   `S^0.5 V^0.3 D^0.2 Q` is computed once per pair; what differs between the three rows is the
   typing cap (§4.3) and, more importantly, `type_evidence_count` — the items that actually
   satisfied that type's rule. So the three rows are not three independent beliefs about three
   propositions; they are one belief about the pair, capped by how checkable each reading is,
   each displayed against its own sample size. That is a weaker guarantee than "three
   confidences" implies and it is written down here rather than left for a reader to infer.
   What it does guarantee is the thing §24 asks for: there is no row, column or view holding one
   number per competitor, so no ranking can be computed from the schema.
2. **The API never returns a rank.** `GET /brands/{brand_id}/competitors` (§10) returns rows with
   `evidence_count`, `evidence_count_social/commerce/web`, `confidence_band`,
   `relationship_type`, `signals_present`, `first_detected`, `last_detected` and
   `discovery_origin`. There is no `rank`, no `score`, no `position`. `confidence_raw` is served
   only on `?audit=true` and is documented as "for sort stability and debugging".
3. **Default ordering is by evidence, not by confidence.** `ORDER BY evidence_count DESC,
   first_detected ASC`. The UI is permitted to order the list — some order is unavoidable — but
   the ordering axis must be labelled *"ordered by amount of supporting evidence in this
   sample (n items)"*, and `n` must be visible on every row. Ordering by observed evidence is a
   statement about the dataset, which §24 explicitly permits; ordering by a composite
   "competitiveness" number is not.
4. **Confidence is displayed as a band with its basis, never as a number or a bar.**
   `low < 0.35 ≤ moderate < 0.55 ≤ strong < 0.75 ≤ very_strong`, computed as a generated column
   so the UI cannot invent its own cut-offs.
   **What the boundaries actually correspond to, under the corrected arithmetic of §3.7:**
   **0.35** is reachable by a *single* strong channel only when volume is substantial and no
   quality factor bites (one group at ≈0.8 with `n_eff ≈ 20` and `Q ≈ 0.9` gives ≈0.38); it is
   the anecdote line. **0.55** needs roughly `S ≳ 0.47` — several channels at ≈0.5, or two
   channels near 0.8 — together with clean quality. **0.75** needs essentially *every available
   group* well evidenced (all five at ≈0.74 with `Q ≈ 0.9`); it should be rare.
   **These boundaries are provisional and are a calibration item, not a result.** They were
   previously justified against a worked example that did not follow §3.2's own formula, and
   with the arithmetic corrected the earlier descriptions ("0.75 requires three-plus channels
   with `n_eff ≳ 16`") are simply not what the formula produces. The requirement on run 1 is
   therefore: compute the confidence distribution over all enumerated pairs, check that each
   band is non-degenerate (no band holding >70% or <2% of written pairs), and either keep the
   fixed cut-offs or move them to fixed quantiles of that distribution — recording which, with a
   `band_scheme_version` stored on every relationship so a past finding can be re-read under the
   scheme it was generated with. See **OQ-11** and **OQ-14**.
   The UI copy changes register at each boundary: below 0.35 the phrasing must be hypothesis
   language ("may", "appears in a small number of items"); at or above 0.55 observation language
   scoped to the sample is permitted.
5. **Forbidden output contract** (unit-tested against the phrasing registry, §7.3): no field,
   label or rendered string may contain a 0–100 score, a star rating, a "threat level", a
   "competitiveness index", a letter grade, or the word "score" applied to a competitor.

### 3.10 The selection effect across the pair set, and the null that calibrates it

Up to **2000** pairs are enumerated per run (§5.2) and those clearing `confidence >= 0.15` are
reported. Reporting the survivors of 2000 selections on a noisy statistic is a
multiple-comparisons problem whether or not the statistic is a p-value, and nothing elsewhere in
this design controls it: §7's rules govern *comparison statements about a pair*, and the §7.2
BH correction governs *labels within a subset*. Neither touches *which pairs get reported at
all*. Left alone, a run with few real relationships still emits a tidy list of them.

The fix is not a correction factor — `confidence` is not a test statistic and pretending
otherwise would be worse than the problem. It is an explicit null, computed from the run's own
data:

**Label-permutation null.** Hold every item, cluster, creator, query and listing fixed and
permute the *brand labels* attached to them, within strata defined by
`(source_kind, platform, brand marginal-count decile)` so a brand keeps its frequency and its
platform mix and loses only its partners. Re-run `enumerate_pairs` → `detect_signals` →
`aggregate` over the permuted assignment. Repeat `B = 50` times (the pipeline after resolution
is pure SQL plus numpy; §8 measures the whole stage in seconds, so 50 replicates is minutes,
not dollars — no LLM call is repeated, because every per-item verdict is already persisted).

Three numbers are stored on `research_runs` and shown in the research-run view (§32) and at the
head of the Competitive Landscape:

| quantity | meaning |
|---|---|
| `null_pairs_written_mean` | mean number of pairs clearing 0.15 **under the null** |
| `observed_pairs_written` | the real count |
| `pair_fdr_estimate` | `min(1, null_pairs_written_mean / max(1, observed_pairs_written))` |

- The UI statement is one sentence and it is **mandatory** whenever `pair_fdr_estimate >= 0.10`:
  *"Of the {observed} competitor relationships reported, about {expected} would be expected from
  chance co-occurrence alone in a sample of this shape."* There is no silent version of this.
- `pair_fdr_estimate >= 0.50` suppresses the competitor list entirely in favour of the
  insufficient-evidence surface. A list in which half the entries are expected under
  independence is not a finding.
- The same replicates give a null distribution of `confidence`, which is the honest basis for
  the band-boundary calibration asked for in §3.9.4: the `low/moderate` boundary should sit at
  or above the null's 95th percentile.
- Permuting labels rather than items is what keeps the null faithful: it preserves the corpus's
  query-driven, engagement-ranked, duplicate-prone structure — all the things that manufacture
  spurious co-occurrence — and destroys only the thing being claimed.

This is listed as **OQ-15** because `B = 50` and the stratification variables are reasoned, not
measured; the requirement that *some* null be computed and displayed is not optional.

---

## 4. RELATIONSHIP TYPING

### 4.0 Interfaces

```python
# typing.py
class RelationshipType(StrEnum):
    DIRECT_SUBSTITUTE = "direct_substitute"
    CATEGORY_PEER     = "category_peer"
    ADJACENT_CATEGORY = "adjacent_category"
    CO_CONSUMED       = "co_consumed"
    SAME_SHELF        = "same_shelf"
    UNCLEAR           = "unclear"

class TypingBasis(StrEnum):
    LANGUAGE = "language"; VISUAL_ONLY = "visual_only"
    COMMERCE = "commerce"; DISCOVERY = "discovery"; MIXED = "mixed"

@dataclass(frozen=True, slots=True)
class TypingDecision:
    relationship_type: RelationshipType
    basis: TypingBasis
    rule_id: str                       # e.g. "R1a", for the audit view
    competing: tuple[RelationshipType, ...]
    contradiction_share: float
    direction_balance: float | None
    confidence_cap: float | None
    required_evidence_ids: tuple[UUID, ...]

def type_relationship(agg: PairAggregate, rows: Sequence[EvidenceRow],
                      *, cfg: TypingConfig) -> list[TypingDecision]: ...
```

**Multiple types are allowed per pair.** The DDL keys on
`(brand_id, competitor_brand_id, relationship_type)`, so a pair can legitimately be both
`same_shelf` (confidence 0.62) and `direct_substitute` (confidence 0.41). Forcing one label
would discard true information; the UI shows the highest-confidence type as the headline and
lists the others. `UNCLEAR` is emitted only when **no** typed rule fires.

### 4.1 Typing rules

Evaluated independently (not first-match), each emitting its own `TypingDecision`. All rules
additionally require `same_corporate_family = false` (§1.6.2) and
`confidence >= 0.15` (§3.6). **Hashtag-only and `mere_listing` evidence may never satisfy any
rule.**

**R1 — `direct_substitute`.** Requires (a) **or** (b):
- **R1a**: ≥2 distinct items carrying S7 spans with `relation ∈ {SUBSTITUTION,
  PREFERENCE_OVER}` and `strength_cue ∈ {explicit, hedged}`, from **≥2 distinct creators**.
  `basis = LANGUAGE`, no cap.
- **R1b**: ≥2 distinct items of S1 with `kind ∈ {head_to_head, taste_test, blind_test}`
  (an explicit single-slot-choice frame) **and** S7 co-consumption item share < 0.25.
  `basis = LANGUAGE`, no cap.

**Why ≥2 items and ≥2 creators.** One creator's statement is an individual's preference; the
*type* is a claim about the category's consideration structure, and two independent sources is
the minimum meaning of "recurring" (brief §23 lists "recurring brand mentions", not "a
mention"). **Boundary at exactly 1 item or 1 creator:** the type is withheld, the pair falls
through to R4 (`category_peer`) if that bar is met and otherwise to `UNCLEAR`, **and the span
is still stored and displayed as evidence**. Only the label is withheld, never the evidence.

**R2 — `co_consumed`.** Requires (a) **or** (b):
- **R2a**: ≥3 distinct items with S7 `relation = CO_CONSUMPTION`, `strength_cue ∈ {explicit,
  hedged}`, from ≥2 creators. `basis = LANGUAGE`.
- **R2b**: ≥5 distinct items with S2 co-appearance at `simultaneity = 1.0` on those items
  **and** both brands in an active-use/consumption state in the same scene (the video
  subsystem's per-brand usage state, **XS-1**) **and** S7 substitution item share < 0.25.
  `basis = VISUAL_ONLY`, `confidence_cap = 0.55`.

**Why 3 for language and 5 for vision.** Language states the relation; vision implies it. The
visual route needs more items because co-presence in an active-use scene is still consistent
with a taste test. 5 distinct items out of ~1500 is the point at which the pattern is not a
single setting or a single creator's format. **Why the 0.55 cap on the visual route:** a
language claim is checkable by a human in one glance (the verbatim quote is stored); a visual
inference is not. Vision-only typing is therefore not permitted to reach the `strong` band.

**R3 — `same_shelf`.** Requires (a) **or** (b):
- **R3a**: ≥3 distinct commerce evidence units from S5 with `cat_sim ≥ 0.5` or `q_adj ≥ 0.5`.
  `basis = COMMERCE`.
- **R3b**: ≥3 distinct social items where both brands appear, **neither brand is in an
  attributable consumption state**, and the item carries an assortment indicator — a
  *discovered* setting label canonicalised by the taxonomy subsystem (**XS-5**) that the
  taxonomy has grouped as a retail/assortment setting, or `quantity_visible='many'`. No
  retail-context enum is predefined here or in doc 03; the grouping is discovered (§12.1).
  `basis = VISUAL_ONLY`, `confidence_cap = 0.55`.

R3b is the deliberate "retail adjacency, nothing more" bucket. It exists so shelf and haul
content stops being misread as either substitution or co-consumption — which is the most
common structural error in co-appearance analysis.

**R4 — `category_peer`.** Requires `confidence >= 0.35` **and** any of:
- S4 pair scale ≥ 0.50 (≥3 eligible brand-agnostic category queries returning both); or
- S3 pair scale ≥ 0.40 with ≥2 shared clusters; or
- S8 pair scale ≥ 0.50 with ≥2 ranked lists.
`basis = DISCOVERY` or `MIXED`. Semantics: *the data treats these as members of the same
consideration set*, with no evidence of substitution or co-consumption. This is the honest
default for most discovered competitors and should be expected to be the majority type.
The `confidence >= 0.35` gate means a `category_peer` claim is never made from anecdote-level
evidence.

**R5 — `adjacent_category`.** Requires `confidence >= 0.35` **and** S5 `cat_sim < 0.25` **and**
(S3 pair scale ≥ 0.30 **or** S11 pair scale ≥ 0.40). Semantics: they meet in content and
culture but not on the same shelf.
**Why `cat_sim < 0.25`:** Ruzicka ≥ 0.25 already means a quarter of listing mass is shared,
which is same-category by any reasonable reading. **Boundary:** with `cat_sim` in `[0.25, 0.5)`
neither R5 nor R3a fires from commerce, and the pair falls to R4/`UNCLEAR` — the correct
outcome, since the commerce data is genuinely equivocal there. If commerce is unavailable
entirely, `cat_sim` is `NULL`, R5 cannot fire, and that is recorded in the run's coverage note.

**R6 — `unclear`.** Emitted when `confidence >= 0.15` and no rule above fired. A first-class,
displayed outcome: *"relationship not yet characterised — N supporting items"*, with the
evidence listed. §28 requires identifying insufficient data; `unclear` is that, for typing.

**Conflict resolution between R1 and R2** (they are opposites, see §4.2): if both fire, let
`n_sub` and `n_co` be the distinct-item counts behind each.
- if `max(n_sub, n_co) >= 2 * min(n_sub, n_co)` → the larger side is emitted, the smaller is
  recorded in `competing_types`, and `contradiction_share = min/(min+max)` feeds
  `Q_contradiction`;
- otherwise → **both are suppressed** and the pair is typed `UNCLEAR` with
  `competing_types = (direct_substitute, co_consumed)`.
**Why the 2× dominance rule:** it is simple, auditable and symmetric. Below 2× the sample
genuinely contains both behaviours and asserting either one would misrepresent it. The brief
permits conflicting evidence; it does not permit picking a winner from a coin flip.

### 4.2 The substitution vs co-consumption discriminator

These are opposite relationships that produce **identical co-appearance data**: in both cases
two brands are in one video, often in one frame, often both opened. Co-appearance alone cannot
tell them apart, and any design that tries will systematically mislabel. Five discriminators,
in descending reliability.

**D1 — Linguistic relation of the two mentions (strongest, and the only human-checkable one).**
Substitution frames the brands as **alternatives competing for one slot**; co-consumption frames
them as **simultaneous members of one set**. Structurally: alternative/adversative coordination
and comparative morphology versus additive coordination and together-adverbs; change-of-state
verbs versus combination verbs; a single-slot choice versus a pair. This is exactly S7's output
enum, and S7 is required to return the **verbatim quote**, so a marketer can verify the
discrimination in one glance. The cue phrases themselves are discovered, never shipped.
*Weight: decisive when ≥2 explicit/hedged items exist on one side.*

**D2 — Temporal and physical state within the video.** From per-brand frame index sets and
brand-attributable consumption states (**XS-1**, bound to doc 03's fields in §12.1):
```
overlap_frac     = |F_a ∩ F_b| / |F_a ∪ F_b|
both_active_frac = co-detected frames in which BOTH brands have an attributable
                   consumption state / |F_a ∩ F_b|
```
`F_x` is `VisibleBrand.frame_indices` for brand x; a consumption state is attributable to a
brand only through a `VisibleProduct` with `brand_resolved = true`.
**Resolution floor — D2 produces no verdict on a coarse frame set.** Doc 03's frame budget is
**3/6/8/12/16/20 frames by duration tier**. With `|F_a ∪ F_b| = 3` the attainable values of
`overlap_frac` are {0, ⅓, ½, ⅔, 1}: the "deliberately undecided" band `[0.35, 0.50)` contains
*no attainable value at all*, one frame moves the ratio by 0.33, and the cut-offs become a
coin-flip dressed as a threshold. D2 therefore requires `|F_a ∪ F_b| >= 6` **and**
`|F_a ∩ F_b| >= 2`; below that it returns `no visual verdict` with
`detail.d2_suppressed = 'frame_resolution'`, and the pair falls to D1/D4 like any other
undecided case. `both_active_frac` carries the same floor on its own denominator
(`|F_a ∩ F_b| >= 3`), since it is a ratio over the intersection.

- substitution-leaning: `overlap_frac < 0.35` **or** `both_active_frac < 0.20`
- co-consumption-leaning: `overlap_frac >= 0.50` **and** `both_active_frac >= 0.40`
- `overlap_frac ∈ [0.35, 0.50)` → **no visual verdict**; fall back to D1/D4.

**Why 0.35 and 0.50, with a deliberate gap.** Below roughly one third, the brands largely
occupy *different parts* of the video — sequential treatment, which is the signature of
comparison. Above one half they are genuinely on screen together for most of the time either is
present, which is the signature of joint consumption. The 0.35–0.50 band is left explicitly
undecided rather than split at an arbitrary midpoint, because that is where the data genuinely
does not distinguish the two and inventing a cut-off there would manufacture labels. These two
numbers are reasoned from frame geometry, not measured — **OQ-3**. Note also that
`frame_indices` is a model-reported set over sampled frames, so `overlap_frac` is a Jaccard over
*reported visibility*, not over presence; that is a second reason the thresholds cannot be
trusted before OQ-3's labelling exercise.

**D3 — Simultaneous in-use instances.** Co-consumption normally means both items are in the
consumption set at once (two open packs, two items in one serving context). **Weak on its own,
because taste tests also open both.** Its real value is the **negative**: if across *all*
evidence items only one of the two brands is ever observed in an in-use state, co-consumption is
ruled out outright. Used as a veto, not as positive evidence.

**D4 — Evaluative asymmetry.** Comparison and substitution content produces a **verdict**: a
preferred side, a superlative, a rank, a score. Co-consumption content produces no verdict.
Operationalised as the presence of `verdict_for` (S1) or `brand_key_to` (S7) on ≥2 items.
*Weight: substitution-leaning, cap 0.55.*

**D5 — Marketplace basket proxy (weak, conditional).** If the collector exposes them: a
"frequently bought together"/bundle block containing both brands is co-consumption evidence; a
"similar products"/"you may also like" block is substitution/peer evidence. **Conditional on
actor capability** — see **OQ-4**; the discriminator must run correctly when the fields are
absent, in which case D5 simply does not contribute.

**Decision table** (`discriminate.py`), evaluated top to bottom:

```python
@dataclass(frozen=True, slots=True)
class Discrimination:
    leaning: Literal["substitution", "co_consumption", "neither"]
    basis: TypingBasis
    confidence_cap: float | None
    discriminators_used: tuple[str, ...]
    n_sub_items: int
    n_co_items: int
```

| # | condition | outcome |
|---|---|---|
| 1 | D1 has ≥2 explicit/hedged items on one side and <2 on the other | that side wins, `basis=LANGUAGE`, no cap |
| 2 | D1 silent or balanced; D2 gives a consistent verdict on ≥3 items | that side wins, `basis=VISUAL_ONLY`, cap **0.55** |
| 3 | D3 veto fires (only one brand ever in use) | co-consumption ruled out; substitution allowed only via rule 1 or 4 |
| 4 | D4 verdict present on ≥2 items | substitution-leaning, `basis=VISUAL_ONLY`, cap **0.55** |
| 5 | D5 available and unambiguous on ≥3 listings | that side, `basis=COMMERCE`, cap **0.55** |
| 6 | otherwise | `same_shelf` if R3 holds, else `UNCLEAR` |

The ordering is not arbitrary: it is strictly by **human checkability**. A stored verbatim quote
can be verified by a marketer in seconds; a frame-overlap fraction cannot. That is why only D1
permits an uncapped claim, and why the most abundant signal (co-appearance) sits lowest.

### 4.3 Typing caps feed back into confidence

`confidence = min(confidence, typing_cap)` is applied **after** §3.6. A `co_consumed`
relationship typed only from vision is therefore capped at the top of the `moderate` band and
can never be displayed as `strong`. The cap is stored in
`competitor_relationships.quality_factors->'typing_cap'` so the audit view can show why a
well-evidenced pair did not reach a higher band.

---

## 5. DDL AND RELATIONSHIP LIFECYCLE

### 5.1 Brand registry

```sql
create extension if not exists pg_trgm;
create extension if not exists vector;
create extension if not exists fuzzystrmatch;      -- levenshtein, metaphone helpers

create type brand_level  as enum ('company','brand','sub_brand','variant');
create type brand_status as enum ('provisional','confirmed','rejected','merged');
create type brand_origin as enum ('discovered','seed','manual');
create type alias_channel as enum ('packaging_vision','spoken','written','commerce_title',
                                   'commerce_brand_field','web_text','seed','manual');
create type surface_resolution_state as enum ('resolved','pending_support','ambiguous',
                                              'rejected_noise','new_brand_created');

create table brands (
  id                      uuid primary key default gen_random_uuid(),
  canonical_name          text not null,
  norm_name               text not null,
  ocr_skeleton            text not null,
  phonetic_key            text,
  brand_level             brand_level  not null default 'brand',
  parent_brand_id         uuid references brands(id) on delete restrict,
  root_company_id         uuid references brands(id) on delete restrict,
  ancestor_ids            uuid[]       not null default '{}',
  depth                   smallint     not null default 0 check (depth between 0 and 3),
  status                  brand_status not null default 'provisional',
  origin                  brand_origin not null default 'discovered',
  seed_id                 uuid,
  is_subject              boolean      not null default false,
  distinct_item_count     int          not null default 0,
  distinct_channel_count  smallint     not null default 0,
  resolution_quality      numeric(4,3) not null default 1.000
                          check (resolution_quality between 0 and 1),
  first_seen_at           timestamptz  not null default now(),
  last_seen_at            timestamptz  not null default now(),
  created_in_run_id       uuid,
  merged_into_brand_id    uuid references brands(id),
  notes                   jsonb        not null default '{}'::jsonb,
  created_at              timestamptz  not null default now(),
  updated_at              timestamptz  not null default now(),
  constraint brands_merged_chk  check ((status = 'merged') = (merged_into_brand_id is not null)),
  constraint brands_no_self     check (parent_brand_id is distinct from id),
  constraint brands_level_depth check (
      (brand_level = 'company'   and depth = 0) or
      (brand_level = 'brand'     and depth <= 1) or
      (brand_level = 'sub_brand' and depth <= 2) or
      (brand_level = 'variant'   and depth <= 3))
);

-- Postgres 15+ NULLS NOT DISTINCT: one norm_name per parent (NULL parent = top level)
create unique index brands_norm_parent_uq
  on brands (norm_name, parent_brand_id) nulls not distinct
  where status <> 'merged';
create index brands_skeleton_idx on brands (ocr_skeleton) where status = 'confirmed';
create index brands_ancestors_idx on brands using gin (ancestor_ids);
create index brands_root_idx on brands (root_company_id) where status = 'confirmed';
create index brands_status_idx on brands (status, distinct_item_count desc);

-- ancestry maintenance
create or replace function brands_sync_ancestry() returns trigger
language plpgsql as $$
declare p record;
begin
  if new.parent_brand_id is null then
    new.ancestor_ids := '{}'::uuid[];
    new.depth := 0;
    new.root_company_id := new.id;
  else
    select ancestor_ids, depth, root_company_id into p from brands where id = new.parent_brand_id;
    if new.id = any(p.ancestor_ids) then
      raise exception 'brand hierarchy cycle via %', new.parent_brand_id;
    end if;
    new.ancestor_ids := p.ancestor_ids || new.parent_brand_id;
    new.depth := p.depth + 1;
    new.root_company_id := coalesce(p.root_company_id, new.parent_brand_id);
  end if;
  new.updated_at := now();
  return new;
end $$;
create trigger brands_ancestry before insert or update of parent_brand_id on brands
  for each row execute function brands_sync_ancestry();
```
A descendant re-parent requires re-running the trigger over the subtree; done in a
`recursive` statement inside `hierarchy.py::reparent()` under a transaction, because
re-parenting is rare (review-queue driven) and correctness beats trigger cleverness.

```sql
create table brand_aliases (
  id                  uuid primary key default gen_random_uuid(),
  brand_id            uuid not null references brands(id) on delete cascade,
  surface_raw         text not null,
  surface_norm        text not null,
  ocr_skeleton        text not null,
  consonant_skeleton  text,
  phonetic_key        text,
  token_signature     text,
  alias_kind          text not null check (alias_kind in
                        ('canonical','variant_spelling','ocr_form','abbreviation',
                         'misspelling','translit','segmented_hashtag','seed')),
  channel             alias_channel not null,
  is_ambiguous        boolean not null default false,
  requires_context    boolean not null default false,
  context_terms       text[]  not null default '{}',
  blocked             boolean not null default false,
  provisional_merge   boolean not null default false,
  observation_count   int not null default 0,
  distinct_item_count int not null default 0,
  embedding           vector(1536),
  confidence          numeric(4,3) not null default 0.500,
  resolved_by         text not null check (resolved_by in
                        ('exact','containment','fuzzy','embedding_llm','llm','seed','manual')),
  first_seen_at       timestamptz not null default now(),
  last_seen_at        timestamptz not null default now(),
  constraint alias_ctx_chk check (not requires_context or cardinality(context_terms) > 0)
);
create unique index brand_aliases_uq      on brand_aliases (surface_norm, brand_id);
create index brand_aliases_norm_idx       on brand_aliases (surface_norm) where not blocked;
create index brand_aliases_skel_idx       on brand_aliases (ocr_skeleton) where not blocked;
create index brand_aliases_phon_idx       on brand_aliases (phonetic_key) where not blocked;
create index brand_aliases_tokensig_idx   on brand_aliases (token_signature) where not blocked;
create index brand_aliases_trgm_idx       on brand_aliases using gin (surface_norm gin_trgm_ops);
create index brand_aliases_emb_idx        on brand_aliases using hnsw (embedding vector_cosine_ops)
                                             with (m = 16, ef_construction = 64);

create table brand_surface_observations (
  id                  uuid primary key default gen_random_uuid(),
  research_run_id     uuid not null,
  source_kind         text not null check (source_kind in
                        ('content_item','commerce_item','commerce_review','web_document')),
  source_id           uuid not null,
  channel             alias_channel not null,
  surface_raw         text not null,
  surface_norm        text not null,
  ocr_skeleton        text not null,
  extractor_confidence numeric(4,3),
  context_snippet     text,
  frame_ts_ms         int,
  transcript_span_ms  int4range,
  char_span           int4range,
  resolution_state    surface_resolution_state not null default 'pending_support',
  brand_id            uuid references brands(id) on delete set null,
  alias_id            uuid references brand_aliases(id) on delete set null,
  match_tier          text,
  match_score         numeric(4,3),
  margin              numeric(4,3),
  runner_up_brand_id  uuid references brands(id) on delete set null,
  runner_up_score     numeric(4,3),
  residual_tokens     text[] not null default '{}',
  prominence          text check (prominence in ('primary','secondary','incidental')),
  mention_source      text check (mention_source in
                        ('creator_speech','caption','on_screen_text','hashtag','comment',
                         'packaging','listing_title','listing_brand_field','web_body')),
  bundle_or_assortment boolean not null default false,
  reason              text,
  decided_by          text check (decided_by in ('cascade','llm','human')),
  decided_at          timestamptz,
  created_at          timestamptz not null default now()
);
create unique index bso_dedupe_uq on brand_surface_observations
  (source_kind, source_id, channel, surface_norm, coalesce(frame_ts_ms, -1),
   coalesce(lower(char_span), -1));
create index bso_norm_idx   on brand_surface_observations (surface_norm);
create index bso_open_idx   on brand_surface_observations (resolution_state, surface_norm)
                               where resolution_state <> 'resolved';
create index bso_brand_idx  on brand_surface_observations (brand_id, source_kind, source_id)
                               where resolution_state = 'resolved';
create index bso_run_idx    on brand_surface_observations (research_run_id);

-- the view every other subsystem should read brands through (P2).
-- It rolls each observation UP to the configured analysis level (default 'brand',
-- §1.6.1): an observation resolved to a sub_brand or variant node must appear to
-- downstream code as its brand-level ancestor, or every count in §2 and §7 is
-- computed at a level the design does not claim. `resolved_brand_id` is kept
-- alongside so drill-down can still show the specific node that was observed.
create view v_item_brands as
with lvl as (select 'brand'::brand_level as analysis_level)   -- run config, see §1.6.1
select o.source_kind,
       o.source_id,
       coalesce(anc.id, b.id)            as brand_id,          -- analysis-level node
       b.id                              as resolved_brand_id, -- node actually observed
       coalesce(anc.root_company_id, b.root_company_id) as root_company_id,
       max(case o.prominence when 'primary' then 3
                             when 'secondary' then 2 else 1 end) as prominence_rank,
       array_agg(distinct o.channel)     as channels,
       bool_or(o.bundle_or_assortment)   as bundle_or_assortment
from brand_surface_observations o
join brands b on b.id = o.brand_id
cross join lvl
left join lateral (
    select a.id, a.root_company_id
    from brands a
    where a.id = any(b.ancestor_ids) and a.brand_level = lvl.analysis_level
      and a.status = 'confirmed'
    order by a.depth desc limit 1
) anc on true
where o.resolution_state = 'resolved'
  and b.status = 'confirmed'
group by 1, 2, 3, 4, 5;
```

```sql
create type brand_review_kind as enum
  ('merge','split','variant','promote','reparent','ambiguous_surface','seed_unconfirmed');

create table brand_review_items (
  id               uuid primary key default gen_random_uuid(),
  kind             brand_review_kind not null,
  status           text not null default 'open'
                   check (status in ('open','accepted','rejected','auto_expired')),
  brand_id         uuid references brands(id) on delete cascade,
  other_brand_id   uuid references brands(id) on delete cascade,
  surface_norm     text,
  rationale        text not null,
  machine_confidence numeric(4,3),
  evidence         jsonb not null default '{}'::jsonb,
  research_run_id  uuid,
  created_at       timestamptz not null default now(),
  resolved_at      timestamptz,
  resolved_by      text
);
create unique index bri_open_uq on brand_review_items
  (kind, coalesce(brand_id,'00000000-0000-0000-0000-000000000000'::uuid),
         coalesce(other_brand_id,'00000000-0000-0000-0000-000000000000'::uuid),
         coalesce(surface_norm,''))
  where status = 'open';
```

```sql
create table substitution_cues (            -- SHIPS EMPTY; populated only from S7 output
  id                  uuid primary key default gen_random_uuid(),
  cue_norm            text not null unique,
  cue_text            text not null,
  language_guess      text,
  relation            text not null check (relation in
                        ('substitution','preference_over','co_consumption','comparison_neutral')),
  observed_count      int not null default 1,
  distinct_item_count int not null default 1,
  precision_estimate  numeric(4,3),          -- agreement with the final span verdict
  first_seen_at       timestamptz not null default now(),
  last_seen_at        timestamptz not null default now(),
  source              text not null default 'llm_span_extraction'
);
```
A cue is used as a pre-filter only once `distinct_item_count >= 3`, and is retired
(`precision_estimate` recomputed each run) if its agreement with the final verdict falls below
0.50 — i.e. the lexicon prunes itself rather than accumulating noise. It is a pre-filter only;
it never contributes to strength, so a bad cue costs LLM calls, never a wrong finding.

### 5.2 `competitor_relationships` and the evidence ledger

```sql
create type competitor_relationship_type as enum
  ('direct_substitute','category_peer','adjacent_category','co_consumed','same_shelf','unclear');
create type competitor_signal_kind as enum
  ('direct_comparison','co_appearance','shared_cluster','category_search_coappearance',
   'marketplace_adjacency','recurring_mention','substitution_language','ranking_review',
   'positioning_similarity','shared_creator','shared_cultural');
create type evidence_unit as enum
  ('content_item','commerce_item','commerce_review','web_document','cluster','creator',
   'research_query','aggregate');
create type relationship_decay_state as enum ('active','stale','dormant','archived');

create table competitor_relationships (
  id                    uuid primary key default gen_random_uuid(),
  -- brief §23 fields -------------------------------------------------------
  brand_id              uuid not null references brands(id) on delete cascade,
  competitor_brand_id   uuid not null references brands(id) on delete cascade,
  relationship_type     competitor_relationship_type not null,
  evidence_count        int  not null default 0,     -- DISTINCT items for the PAIR, unweighted
  -- DISTINCT items behind THIS TYPE's typing rule (TypingDecision.required_evidence_ids).
  -- Every rendered statement about the relationship's type cites this, not evidence_count
  -- (§3.1). Written by the `type` stage.
  type_evidence_count   int  not null default 0,
  confidence            numeric(4,3) not null default 0.000
                        check (confidence between 0 and 1),
  first_detected        timestamptz not null default now(),
  last_detected         timestamptz not null default now(),
  supporting_content_ids uuid[] not null default '{}', -- capped at 50, see note
  -- decomposition (auditability, §27/§32) ----------------------------------
  evidence_count_social   int not null default 0,
  evidence_count_commerce int not null default 0,
  evidence_count_web      int not null default 0,
  distinct_creator_count  int not null default 0,
  n_eff                 numeric(8,3) not null default 0,
  strength              numeric(4,3) not null default 0,
  volume                numeric(4,3) not null default 0,
  diversity             numeric(4,3) not null default 0,
  signal_strengths      jsonb not null default '{}'::jsonb,
  group_strengths       jsonb not null default '{}'::jsonb,
  quality_factors       jsonb not null default '{}'::jsonb,
  groups_available      smallint not null default 5,
  confidence_band       text generated always as (
                          case when confidence >= 0.75 then 'very_strong'
                               when confidence >= 0.55 then 'strong'
                               when confidence >= 0.35 then 'moderate'
                               else 'low' end) stored,
  -- which boundary set produced confidence_band (§3.9.4): a recalibration must be
  -- distinguishable from a movement, both in the audit view and in §5.3's event causes
  band_scheme_version   text not null default 'fixed-2026-10-02',
  -- typing -----------------------------------------------------------------
  typing_basis          text,
  typing_rule_id        text,
  competing_types       competitor_relationship_type[] not null default '{}',
  contradiction_share   numeric(4,3) not null default 0,
  direction_balance     numeric(5,3),
  same_corporate_family boolean not null default false,
  -- provenance & lifecycle --------------------------------------------------
  discovery_origin      brand_origin not null default 'discovered',
  seed_id               uuid,
  seed_confirmed        boolean not null default false,
  first_detected_run_id uuid,
  last_evidence_run_id  uuid,
  runs_without_new_evidence int not null default 0,
  decay_state           relationship_decay_state not null default 'active',
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  constraint cr_distinct check (brand_id <> competitor_brand_id),
  constraint cr_canonical check (brand_id < competitor_brand_id)
);
create unique index cr_pair_type_uq on competitor_relationships
  (brand_id, competitor_brand_id, relationship_type);
create index cr_band_idx  on competitor_relationships (confidence_band, evidence_count desc)
                             where decay_state in ('active','stale') and not same_corporate_family;
create index cr_brand_idx on competitor_relationships (brand_id, decay_state);
create index cr_comp_idx  on competitor_relationships (competitor_brand_id, decay_state);
```

**Two deliberate deviations from the brief's field list, both for correctness:**

1. **`cr_canonical check (brand_id < competitor_brand_id)`** — pairs are stored once, in UUID
   order, instead of twice (once from each brand's perspective). Two mirror rows inevitably
   drift out of sync and would be double counted by any aggregate query. Directionality, which
   genuinely matters for `direct_substitute`, is carried by `direction_balance` plus
   per-evidence `direction`. The brief's read shape is restored exactly by a view:

```sql
create view v_competitor_relationships as
  select id, brand_id, competitor_brand_id, relationship_type, evidence_count, confidence,
         first_detected, last_detected, supporting_content_ids, confidence_band,
         discovery_origin, seed_confirmed, decay_state, direction_balance,
         same_corporate_family, 1 as orientation
  from competitor_relationships
union all
  select id, competitor_brand_id as brand_id, brand_id as competitor_brand_id,
         relationship_type, evidence_count, confidence, first_detected, last_detected,
         supporting_content_ids, confidence_band, discovery_origin, seed_confirmed,
         decay_state, -direction_balance, same_corporate_family, -1 as orientation
  from competitor_relationships;
```

2. **`supporting_content_ids` is capped at 50** — the most representative ids, ordered by
   `strength * weight * recency` desc then `published_at` desc. An unbounded array TOASTs,
   bloats every row read, and duplicates the ledger. The full evidence set lives in
   `competitor_signal_evidence` and is what the drill-down queries; the array is a UI
   convenience for the first page of evidence cards.

```sql
create table competitor_signal_evidence (
  id                  uuid primary key default gen_random_uuid(),
  brand_id            uuid not null references brands(id) on delete cascade,
  competitor_brand_id uuid not null references brands(id) on delete cascade,
  signal              competitor_signal_kind not null,
  evidence_unit       evidence_unit not null,
  evidence_id         uuid,
  strength            numeric(4,3) not null check (strength between 0 and 1),
  weight              numeric(4,3) not null default 1.000,
  direction           smallint not null default 0 check (direction in (-1,0,1)),
  is_aggregate        boolean not null default false,
  creator_id          uuid,
  published_at        timestamptz,
  sponsored_likelihood numeric(4,3),
  canonical_item_ids  uuid[] not null default '{}',
  detail              jsonb not null default '{}'::jsonb,
  research_run_id     uuid not null,
  detected_at         timestamptz not null default now(),
  constraint cse_canonical check (brand_id < competitor_brand_id),
  constraint cse_agg_chk  check ((evidence_unit = 'aggregate') = (evidence_id is null))
);
create unique index cse_uq on competitor_signal_evidence
  (brand_id, competitor_brand_id, signal, evidence_unit, evidence_id, (detail->>'span_id'))
  nulls not distinct;
create index cse_pair_idx on competitor_signal_evidence (brand_id, competitor_brand_id, signal);
create index cse_unit_idx on competitor_signal_evidence (evidence_unit, evidence_id);
create index cse_items_idx on competitor_signal_evidence using gin (canonical_item_ids);
create index cse_run_idx  on competitor_signal_evidence (research_run_id);

create table competitor_signal_scores (     -- per run per pair per signal; the audit trail
  id uuid primary key default gen_random_uuid(),
  research_run_id uuid not null,
  brand_id uuid not null references brands(id) on delete cascade,
  competitor_brand_id uuid not null references brands(id) on delete cascade,
  signal competitor_signal_kind not null,
  pair_scale numeric(5,4) not null,
  sig_strength numeric(5,4) not null,
  support_units int not null,
  detail jsonb not null default '{}'::jsonb,   -- npmi, lift, cat_sim, FLOOR, exclusions
  computed_at timestamptz not null default now(),
  constraint css_canonical check (brand_id < competitor_brand_id)
);
create unique index css_uq on competitor_signal_scores
  (research_run_id, brand_id, competitor_brand_id, signal);

create table competitor_relationship_events (
  id bigserial primary key,
  relationship_id uuid not null references competitor_relationships(id) on delete cascade,
  research_run_id uuid not null,
  event_kind text not null check (event_kind in
    ('first_detected','type_changed','band_changed','state_changed',
     'evidence_added','seed_confirmed','suppressed_same_family')),
  -- §5.3: only cause='new_evidence' rows may be rendered as competitive momentum
  cause text not null check (cause in
    ('new_evidence','decay_only','recollection_variance','recalibration','human_edit')),
  from_value text, to_value text, evidence_delta int,
  occurred_at timestamptz not null default now()
);
create index cre_rel_idx on competitor_relationship_events (relationship_id, occurred_at desc);
create index cre_run_idx on competitor_relationship_events (research_run_id, event_kind);
create index cre_cause_idx on competitor_relationship_events (cause, occurred_at desc)
                               where cause = 'new_evidence';

create table competitor_pair_enumeration (  -- which pairs were considered, and what was skipped
  research_run_id uuid not null,
  brand_id uuid not null,
  competitor_brand_id uuid not null,
  reason text not null,          -- 'co_occurrence' | 'category_query' | 'shared_category'
                                 -- | 'subject_pair' | 'capped_out' | 'assortment_excluded'
  raw_cooccurrence int not null default 0,
  dirty boolean not null default false,
  primary key (research_run_id, brand_id, competitor_brand_id)
);
```

**Pair enumeration and its cap.** Candidate pairs are enumerated where both brands are
`confirmed` and at least one holds: they co-occur in ≥1 non-assortment item; both appear in ≥1
eligible brand-agnostic category query's results; or both have ≥5 listings in a shared leaf
category. The set is capped at **2000 pairs** ordered by raw co-occurrence, with every pair
excluded by the cap recorded as `reason='capped_out'`. Pairs involving `is_subject` brands are
**never** dropped by the cap — a scope decision (the subject is what the run is researching),
not an evidence privilege, and it is recorded as `reason='subject_pair'`. With a few hundred
confirmed brands per run the cap is rarely binding; it exists so a pathological run cannot
produce a quadratic blow-up.

### 5.3 `first_detected` / `last_detected` maintenance and decay

**Maintenance** (inside the **`type`** stage, one statement per `(pair, type)` — see §0.6 for
why this cannot run in `aggregate`: the row's `confidence` is the capped value and the cap is a
typing output):

```sql
insert into competitor_relationships as cr (
  brand_id, competitor_brand_id, relationship_type,
  evidence_count, type_evidence_count, band_scheme_version, confidence,
  first_detected, last_detected, supporting_content_ids, n_eff, strength, volume, diversity,
  signal_strengths, group_strengths, quality_factors, groups_available, typing_basis,
  typing_rule_id, competing_types, contradiction_share, direction_balance,
  same_corporate_family, discovery_origin, seed_id, seed_confirmed,
  first_detected_run_id, last_evidence_run_id, runs_without_new_evidence, decay_state,
  evidence_count_social, evidence_count_commerce, evidence_count_web, distinct_creator_count)
values (...)
on conflict (brand_id, competitor_brand_id, relationship_type) do update set
  evidence_count      = excluded.evidence_count,
  type_evidence_count = excluded.type_evidence_count,
  band_scheme_version = excluded.band_scheme_version,
  confidence          = excluded.confidence,
  -- first_detected is the EARLIEST evidence we have ever held for this pair+type:
  first_detected      = least(cr.first_detected, excluded.first_detected),
  -- last_detected moves only when NEW evidence arrived this run:
  last_detected       = case when excluded.evidence_count > cr.evidence_count
                               or excluded.last_evidence_run_id is distinct from cr.last_evidence_run_id
                             then excluded.last_detected else cr.last_detected end,
  supporting_content_ids = excluded.supporting_content_ids,
  n_eff = excluded.n_eff, strength = excluded.strength,
  volume = excluded.volume, diversity = excluded.diversity,
  signal_strengths = excluded.signal_strengths, group_strengths = excluded.group_strengths,
  quality_factors = excluded.quality_factors, groups_available = excluded.groups_available,
  typing_basis = excluded.typing_basis, typing_rule_id = excluded.typing_rule_id,
  competing_types = excluded.competing_types,
  contradiction_share = excluded.contradiction_share,
  direction_balance = excluded.direction_balance,
  seed_confirmed = cr.seed_confirmed or excluded.seed_confirmed,
  evidence_count_social = excluded.evidence_count_social,
  evidence_count_commerce = excluded.evidence_count_commerce,
  evidence_count_web = excluded.evidence_count_web,
  distinct_creator_count = excluded.distinct_creator_count,
  last_evidence_run_id = excluded.last_evidence_run_id,
  runs_without_new_evidence = case when excluded.evidence_count > cr.evidence_count
                                   then 0 else cr.runs_without_new_evidence end,
  updated_at = now();
```
`first_detected` is `least(...)` rather than "set once" so that a back-fill of older content
can legitimately move it earlier — it means *earliest evidence held*, not *first time we
noticed*. The latter is recoverable from `competitor_relationship_events(first_detected)`.

**Decay has two independent mechanisms. They do different jobs and both are needed.**

**(a) Continuous recency weighting inside the confidence.** Each evidence row carries
`recency = 0.5 ** (age_days / H)` with `H = 90` days on `published_at`, falling back to
`detected_at`. This feeds `raw_s` (§3.2) and `n_eff` (§3.4), so a relationship whose evidence is
all old recomputes to a lower confidence automatically, every run, with no state machine.
- **Why H = 90 days:** a quarter matches the brief's quarterly marketing-review cadence (§2), so
  evidence keeps most of its weight within the review window it informs and halves by the next
  one. **Boundary:** at exactly 90 days an item counts half; at 180 days a quarter.
- `evidence_count` is **not** decayed, so the UI can show "17 items, median age 112 days" — the
  honest pairing of an undecayed count with a decayed belief.
- **OQ-7**: H = 90 assumes runs are months apart. For weekly runs it is far too slow to show
  movement, so it is exposed as a per-project config (`AggregationConfig.recency_half_life_days`).

**(b) A discrete lifecycle state machine on `runs_without_new_evidence`.** Counted in **runs
with coverage**, not in days — because if no new collection has happened in the pair's
neighbourhood, the absence of new evidence is not informative.

```python
# decay.py
def has_coverage(run: ResearchRun, pair: Pair) -> bool:
    """A run 'covers' a pair when it added >= 5 new analysed items containing either brand,
    OR >= 10 new commerce listings in a leaf category where either brand has listings."""
```

| `runs_without_new_evidence` (coverage runs) | `decay_state` | behaviour |
|---|---|---|
| 0 | `active` | shown normally |
| 1–2 | `stale` | shown, annotated "no new evidence in the last N runs" |
| 3–5 | `dormant` | hidden from the default competitor list; available under "previously detected" |
| ≥6, **or** `confidence < 0.20` after recency decay | `archived` | excluded from current intelligence; retained in the DB and in the research-run view |

- **Why ≥3 before demotion:** a relationship must survive at least three *opportunities* to be
  re-observed before absence becomes a statement. One or two misses are easily sampling.
- **Why ≥6 for archival:** double the dormancy bar, so archival requires sustained absence.
- **Boundary at exactly 3:** moves to `dormant` and emits a
  `competitor_relationship_events(state_changed)` row, so the Competitive Landscape can show
  "declining evidence" as a *movement* rather than silently dropping a competitor.
- **Nothing is ever deleted.** §27 requires every finding to remain traceable to evidence;
  deleting a relationship would orphan past findings that cite it.
- **Decay never silently re-types a relationship.** Typing is recomputed from the
  recency-weighted evidence each run; any change writes
  `competitor_relationship_events(type_changed)` with from/to values. The same applies to band
  changes.

**Not every event row may support a momentum claim, and the ones that may not are the majority.**
§31.3's "emerging competitive momentum" is a growth/decline statement, and §22 permits those only
when temporal coverage supports them. A `band_changed` row can be produced by at least four
mechanisms, only one of which is movement in the world:

| mechanism | is it movement? |
|---|---|
| new evidence arrived in a coverage run | **yes** |
| nothing arrived; continuous recency decay lowered `raw_s` and `n_eff` | no — it is *absence of new observation*, which is a statement about collection, not the category |
| the same content was re-collected and the platform surfaced a different result set | no — re-collection variance |
| a per-run recalibration moved a threshold (S9's `FLOOR`, §3.9.4's band scheme, a config change) | no — an instrument change |

Every event row therefore carries a required `cause` column:

```sql
-- column on competitor_relationship_events; the full DDL is in §5.2
cause text not null check (cause in
  ('new_evidence','decay_only','recollection_variance','recalibration','human_edit'))
```
`cause` is derived mechanically, not asserted: `new_evidence` requires
`excluded.evidence_count > cr.evidence_count` in a run with coverage for the pair;
`recalibration` is set whenever `band_scheme_version`, `AggregationConfig` or S9's stored `FLOOR`
differs from the previous run's; `decay_only` is the remainder when `evidence_count` is
unchanged. **Only `cause = 'new_evidence'` rows, and only across runs with coverage for that
pair, may be rendered as a momentum statement.** `decay_only` renders as *"no new evidence in
the last N runs"* — which is the honest reading and is already what `stale` says. A
`recalibration` run writes its events and is excluded from momentum entirely, with the
recalibration noted in the run view.

---

## 6. SEED HANDLING

§23 permits an optional competitor seed list but requires it not to prevent discovery of
additional competitors. §1 and §34 go further: the system must be *capable of discovering
something the marketing team did not already tell it*, and must not encode presumed answers.
A seed list is therefore treated as a **recall hint with zero scoring authority**.

### 6.1 Schema

```sql
create table brand_seeds (
  id                uuid primary key default gen_random_uuid(),
  research_run_id   uuid not null,
  supplied_name     text not null,
  supplied_norm     text not null,
  seed_role         text not null check (seed_role in ('subject','competitor_hint','exclude')),
  resolved_brand_id uuid references brands(id) on delete set null,
  resolution_state  text not null default 'unmatched'
                    check (resolution_state in ('unmatched','matched_existing','created',
                                                'ambiguous','observed_zero')),
  confirmed_by_evidence boolean not null default false,
  evidence_count    int not null default 0,
  queries_generated int not null default 0,
  created_at        timestamptz not null default now()
);
create unique index brand_seeds_uq on brand_seeds (research_run_id, supplied_norm, seed_role);
```

### 6.2 The three permitted uses of a seed

**U1 — Alias bootstrap (improves resolution recall only).** Each seed string is normalised and
written as a `brand_aliases` row with `channel='seed'`, `alias_kind='seed'`,
`confidence=0.500`. This helps attach an OCR-mangled surface to the right brand *when that
brand turns out to be in the data*. It creates no relationship and no evidence.
The `CHANNEL_PRIOR` for `SEED` is 0.50, **below every data channel**, so a seed string can never
out-vote an observed surface in the cascade. If a seed string matches nothing in the corpus, the
alias simply never fires.

**U2 — Query generation hint, rate-limited and logged.** Seed names may be used to generate
competitor-level queries (brief §8 strategy C) subject to two hard constraints:
- **Ordering:** no seeded query may be issued until the first brand-agnostic (category-level)
  query batch has been collected and analysed. Discovery goes first, by construction.
- **Budget cap:** seeded queries may consume at most **25%** of the competitor-level query
  budget for the run. **Why 25%:** §8 requires a deliberately mixed corpus; capping seeded
  queries at a quarter keeps three quarters of competitor-level discovery driven by what the
  data surfaced. **Boundary:** the 26th percent is refused and the refusal is written to the
  research log, so the audit view can *prove* the cap held. `brand_seeds.queries_generated`
  plus the run's query budget make the ratio checkable after the fact.

**U3 — Recall audit, surfaced as a finding.** A seed that accumulates zero evidence is set
`resolution_state='observed_zero'` and reported as *"supplied but not observed in this sample
(n=0)"*. This is a genuinely useful output — it tells the marketing team their assumed
competitor set does not appear in the content the run collected — and it is the opposite of
hiding the miss.

### 6.3 The hard non-privileges (each one testable)

| # | rule | test |
|---|---|---|
| N1 | A seed never receives a `competitor_relationships` row without passing the identical signal, typing and threshold rules as a discovered brand. | Insert a seed with no corpus evidence; assert zero relationship rows. |
| N2 | A seed's `confidence` is never floored, boosted or clamped upward. | Compute confidence for an identical evidence set under `origin='seed'` and `origin='discovered'`; assert exact equality. |
| N3 | Seeds get no preference in pair enumeration. Enumeration is driven by corpus co-occurrence only (§5.2); `origin` is not an input. | Assert `enumerate_pairs` output is byte-identical with the seed table emptied. |
| N4 | Seeds are excluded from every LLM prompt used for comparison or relation classification. Only the brand surfaces actually present in the item are passed. | Snapshot-test the rendered prompts; assert no seed string appears unless it was observed in that item. |
| N5 | A seed brand is promoted `provisional → confirmed` by the §1.5 evidence rules only. A seed is never born confirmed. | Assert `status='provisional'` immediately after seeding. |
| N6 | `seed_role='exclude'` suppresses a brand from competitor views but the suppression is logged and the brand remains in the audit view with its evidence. | Assert the excluded brand is absent from `/competitors` and present in `/competitors?audit=true` with `suppressed_by_seed=true`. |
| N7 | Default competitor ordering must not place seeds first. Ordering is `evidence_count DESC, first_detected ASC`; `discovery_origin` is not a sort key, and is not used as a tie-break either (that would reintroduce the bias at the margin). | Assert the sort key list contains no origin column. |

N4 deserves emphasis. Telling the comparison classifier "these are the competitors" is the most
tempting and most corrosive use of a seed list: the model would then find comparisons involving
seeded brands and miss everything else, and the system would confidently report back exactly
what it was told. The prompts in §2 (S1, S7) are therefore constructed from the item's own
resolved brand surfaces only.

### 6.4 UI distinction — preserving credibility

Every competitor row carries `discovery_origin` and `seed_confirmed`. Four display states, with
the exact strings the UI may use:

| state | condition | UI string |
|---|---|---|
| discovered | `origin='discovered'` | **"Discovered in this sample"** |
| seed confirmed | `origin='seed'` and `evidence_count >= 1` and confidence ≥ 0.35 | **"You supplied this — found in {evidence_count} items in this sample"** |
| seed weak | `origin='seed'` and `1 ≤ evidence_count` and confidence < 0.35 | **"You supplied this — weak evidence in this sample ({evidence_count} items, below the reporting threshold)"** |
| seed absent | `origin='seed'` and `evidence_count = 0` | **"You supplied this — not observed in this sample"** |

Layout rules, not suggestions:
- Seed-absent and seed-weak rows render in a **separate, visually distinct block** below the
  discovered competitors. They are never interleaved with discovered rows, because adjacency
  implies equivalent evidentiary standing.
- The Competitive Landscape header carries the credibility counter:
  **"{n_discovered} competitors discovered from the data; {n_not_seeded} of them were not in
  your seed list. {n_seed_absent} of your {n_seed} supplied names were not observed in this
  sample."**
  That single line is the strongest honest statement the subsystem can make about its own
  value, and it is computable directly from `brand_seeds` + `competitor_relationships`.
- The detection reason is always shown per row (§31.3 "why each was detected"): the signal
  groups that fired, with counts — e.g. *"explicit comparison (2 items), marketplace adjacency
  (14 listings), category search (4 queries)"*. A seeded competitor with no detection reason
  cannot be displayed as a competitor at all.

---

## 7. COMPETITOR COMPARISON COMPUTATIONS (§24)

### 7.1 Universal rules

Every comparison below obeys all of these. They are enforced in `comparisons.py` and
`phrasing.py`, not left to the caller.

**R-SCOPE.** Every statement names the sample: a subset description and `n`. A rendered string
without `n` and `subset_n` slots filled is a programming error and raises.

**R-WILSON.** Every proportion is reported with a 95% Wilson score interval:
```python
def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0: return (0.0, 1.0)
    p = k / n; d = 1 + z*z/n
    centre = (p + z*z/(2*n)) / d
    half = (z / d) * sqrt(p*(1-p)/n + z*z/(4*n*n))
    return (max(0.0, centre - half), min(1.0, centre + half))
```
Wilson rather than the normal approximation because the normal interval produces impossible
bounds (negative, or above 1) exactly where this data lives — small subsets and shares near 0.

**R-PAIRED.** The two proportions in a C1/C8/C11-style comparison are measured **on the same
items** — "share of {Cluster Y} items containing A" and "share of {Cluster Y} items containing
Subject" share a denominator, and an item can contain both brands, one, or neither. They are
paired, not independent samples. This matters in the dangerous direction: when two brands rarely
co-occur (the normal case), `Cov(1_A, 1_B) < 0`, so
`Var(p̂_A − p̂_B) = [Var_A + Var_B − 2Cov]/n` is **larger** than the independent-sample variance,
and a procedure built on independent intervals understates the uncertainty of the difference.
The claim in an earlier draft that interval non-overlap is "strictly more conservative than a
two-proportion z-test" is therefore false for this data: it is conservative against the
*unpaired* test, and the unpaired test is the wrong one.

Difference statements are consequently decided by a **paired** criterion on the discordant
counts: with `b` = items containing A but not Subject, `c` = items containing Subject but not A,
a difference may be stated only when the **Agresti–Min / Wilson-type score interval for the
paired difference** (equivalently, an exact binomial test on `b` of `b+c` at the corrected
level in R-MULTIPLICITY) excludes 0, and `b + c >= 10`. Both marginal Wilson intervals are still
**displayed**, because a reader can check them at a glance; they are no longer what decides.

**R-NONOVERLAP (display rule).** The two marginal Wilson intervals are always shown. When the
paired criterion does not fire, the system renders the "both present, difference not
distinguishable at this sample size" template rather than staying silent.

**R-MULTIPLICITY.** Every comparison surface runs many tests at once — brands × subsets ×
labels × platforms — and a per-comparison criterion, however conservative, does not control the
family. For each rendered surface the system (a) counts the comparisons it computed, (b) applies
Benjamini–Hochberg at `q = 0.10` **across all comparisons on that surface**, not only across the
labels of one subset, and (c) displays the count and the surviving count in the audit panel:
*"{k} of {m} comparisons on this view separate at the stated level."* Where the test statistics
are plainly dependent (labels co-occur; the same items appear in several subsets) BH is
approximate and the run-1 permutation null of **§3.10** is reused to check it — see **OQ-10**.

**R-CLUSTER.** Items are **not** independent observations. One creator may contribute ten items
to a subset; one viral format may be reposted; one query may supply a block of results. A Wilson
interval over `n` items assumes `n` independent Bernoulli draws and is too narrow by roughly
`sqrt(DEFF)` when they are clustered. Every proportion in §7 is therefore computed with a
**creator-clustered effective sample size**:
```
DEFF  = 1 + (m̄ - 1) * rho_icc        # m̄ = mean items per creator in the subset
n_eff_stat = n / max(1.0, DEFF)       # used in place of n in every Wilson / paired interval
```
`rho_icc` is estimated per subset by one-way ANOVA on the presence indicator across creators and
floored at 0; when a subset has fewer than 5 distinct creators no interval is computed at all and
the insufficient-data template renders. `n` (the raw item count) is still what the template
displays as the sample size, with `n_eff_stat` shown in the audit panel — the reader is told how
many items there are and how much independent information they carry, which are different
numbers and §28 wants both.

**R-MINN.** `subset_n >= 30` for any share comparison; `>= 5` distinct **creators** in the
subset; and `>= 5` items **per brand** within the subset for that brand to appear in a
difference statement.
**Why 30:** at n=30 a 50% share has a Wilson half-width of about ±18pp. Below 30, almost nothing
separates, so the system would be computing statements it cannot support; above 30 the intervals
start to separate for real differences. **Boundary at exactly 30:** comparison is computed, and
the interval width is displayed so the reader sees how wide it is.
**Why ≥5 per brand:** with 4 or fewer items the point estimate moves by ≥20pp per item.
C3's label comparison additionally requires ≥30 labelled items per brand (T3.d); that is a
*stricter* requirement layered on top of R-MINN's ≥5, not a contradiction of it — an earlier
draft stated the two side by side with no indication of which governed.

**R-NOCAUSE.** No rendered string may contain a causal verb, a market-wide quantifier, a
prediction, or a recommendation. Enforced by a substring/regex check over the rendered output:
```python
FORBIDDEN_PATTERNS: Final[tuple[str, ...]] = (
    r"\bbecause\b", r"\bcauses?\b", r"\bdrives?\b", r"\bleads? to\b", r"\bdue to\b",
    r"\bwill\b", r"\bshould\b", r"\bmust\b", r"\brecommend", r"\bwe advise\b",
    r"\bmarket leader\b", r"\bdominat", r"\bconsumers prefer\b",   # + POPULATION_TERMS, below
    r"\ball consumers\b", r"\bthe market\b", r"\bnationwide\b", r"\bshare of market\b",
    r"\bscore\b", r"\brank(ed|ing)? #?\d", r"\bthreat level\b", r"\bindex\b",
)
# Matched case-insensitively (re.IGNORECASE); a case-sensitive check is trivially evaded.
# Population nouns are NOT a hardcoded literal list. An earlier draft shipped a single
# nationality demonym in this tuple, which is both a market assumption in code (§1/§34) and
# a false sense of safety: a one-word denylist blocks one spelling and misses every other way
# to over-generalise from the sample. They are loaded instead from
# config/lexicons/population_terms.<locale>.yaml, which SHIPS EMPTY, and the structural rule
# does the real work -- every rendered string must contain a scope clause naming the sample
# (R-SCOPE), so a market-wide generalisation cannot be phrased at all.
POPULATION_TERMS_FILE: Final[str] = "config/lexicons/population_terms.{locale}.yaml"
```
A rendered string matching any pattern raises `ForbiddenPhrasingError` in tests and is replaced
by the hedged template at runtime, with the incident logged. Strategic implications are the
hypothesis engine's job (§25) and must be labelled as hypotheses there, never embedded in a
comparison statement.

**R-PROVENANCE.** This is the single largest threat to validity in §7 and it was previously
unaddressed. The corpus is query-built: brief §8 strategy A issues brand-level queries for the
subject and strategy C issues competitor-level queries for brands already noticed, so
`n_Subject` and `n_{Brand A}` are in substantial part a record of *what the agent searched for*.
A statement of the form *"Brand A appears in 18% of {Cluster Y} items and Subject in 41%"* then
measures the query mix at least as much as the category.

Every §7 comparison is therefore computed on the **brand-agnostic stratum**: items all of whose
originating `research_queries` rows have `names_known_brand = false` (XS-6). Specifically:
- The subset and both denominators are restricted to that stratum. `subset_n` is the stratum
  count, and the template's `n` is that number.
- If the stratum is too small for R-MINN, the comparison renders its insufficient-data template.
  It does **not** fall back to the full corpus.
- The full-corpus figures are computed and stored alongside, shown only in the audit panel and
  labelled *"includes items retrieved by brand-targeted searches; not comparable across
  brands"*. Keeping them visible is useful; letting them be compared is not.
- C2 (visual share), C5 (creator mix), C6 (sponsorship) and C8 (marketplace presence) obey the
  same restriction. C9 (price) does not, because a price distribution is a property of the
  listings themselves rather than of their relative frequency — but C9's listing *counts* are
  not reported as share.

**R-FAMILY.** Pairs with `same_corporate_family = true` are excluded from every comparison.

**R-SUBJECT-NEUTRAL.** Comparisons are computed symmetrically. The subject brand is the *focus*
of the view, never a baseline that other brands are scored against.

### 7.2 The computations

Each has: definition, formula, minimum n, and the exact templates. `{Subject}`, `{Brand A}`,
`{Theme X}`, `{Cluster Y}`, `{Label Z}` are slot names. Every template has an
insufficient-data counterpart.

---

**C1 — Theme / cluster presence share.**
`share(x, subset) = distinct items in subset containing x / subset_n`. Min: R-MINN.

- **T1.a (both present, separated):** *"Within the analysed sample, {Brand A} appears in {pa}% of
  {Cluster Y} items ({ka}/{subset_n}, 95% CI {pa_lo}–{pa_hi}%) and {Subject} in {ps}%
  ({ks}/{subset_n}, CI {ps_lo}–{ps_hi}%)."*
- **T1.b (difference statement, only when R-PAIRED fires and the comparison survives
  R-MULTIPLICITY):** *"Within the analysed sample, {Brand A}
  appears more frequently in {Cluster Y} than {Subject} ({ka}/{subset_n} vs {ks}/{subset_n})."*
- **T1.c (overlapping):** *"Within the analysed sample, both {Brand A} ({ka}/{subset_n}) and
  {Subject} ({ks}/{subset_n}) appear in {Cluster Y}; the difference is not distinguishable at
  this sample size."*
- **T1.d (insufficient):** *"{Cluster Y} contains {subset_n} analysed items — too few to compare
  brand presence (minimum 30)."*

---

**C2 — Visual share within the analysed sample.**
`visual_share(x) = Σ on-screen seconds for x / Σ on-screen seconds for all resolved brands`,
computed from frame-level detections, per platform, never pooled across platforms. Secondary
form: share of items where the brand is at `primary` prominence. Min: ≥20 items with detections
and ≥120s of total identifiable brand presence; R-PROVENANCE and R-CLUSTER apply.

**The denominator is "resolved and confirmed", which is not "all brand presence".** `v_item_brands`
exposes only `status='confirmed'` brands, so unresolved surfaces, `ambiguous` observations and
`provisional` brands contribute no seconds — and every share computed over that denominator is
inflated by exactly the amount of presence that was dropped, by an amount that moves as
resolution quality changes between runs. The unresolved share is therefore **computed and
displayed as a term of the same breakdown**, never silently excluded:
`unresolved_share = Σ seconds on surfaces not resolved to a confirmed brand / Σ all seconds with
any brand-like surface`. T2.a carries it, and a comparison is suppressed when
`unresolved_share > 0.30` (at that point the denominator is not measuring what the sentence
says).

- **T2.a:** *"Of the {total_sec}s of on-screen brand presence across {n} analysed {platform}
  videos, {Brand A} accounts for {x}% and {Subject} for {y}%. A further {u}% of that time is on
  packaging that could not be resolved to a confirmed brand. This measures presence within the
  analysed sample only."* (`{u}` slot is mandatory.)
- **T2.b:** *"{Brand A} is the most prominent brand (prominence: primary) in {ka} of {n}
  analysed {platform} videos; {Subject} in {ks}."*
- **T2.c (insufficient):** *"Identifiable on-screen brand presence totals only {total_sec}s
  across {n} videos — too little to compare visual share."*

---

**C3 — Consumer contexts, occasions, behaviours, cultural themes, category associations.**
These share one computation. Raw share differences are dominated by frequent labels, so the
differentiating set uses **log-odds with an informative Dirichlet prior** (Monroe/Colaresi/Quinn),
with the corpus label distribution as the prior:

```python
def differentiating_labels(a: LabelCounts, b: LabelCounts, corpus: LabelCounts,
                           *, alpha0: float = 500.0, z_min: float = 1.96,
                           min_count: int = 5) -> list[LabelDelta]:
    """delta_l = log((y_al + a_l)/(n_a + alpha0 - y_al - a_l))
               - log((y_bl + a_l)/(n_b + alpha0 - y_bl - a_l))
       var_l   = 1/(y_al + a_l) + 1/(y_bl + a_l)
       z_l     = delta_l / sqrt(var_l)
       where a_l = alpha0 * corpus_share(l).
       Keep labels with |z_l| >= z_min and count >= min_count in the higher-scoring brand."""
```
- **`alpha0 = 500`** sets the prior's strength at roughly a third of a 1500-item sample: strong
  enough to shrink labels that occur a handful of times, weak enough that a genuinely
  brand-specific label with 15+ items still separates. It is the main tunable here.
- **`z >= 1.96`** is the conventional 95% two-sided threshold on the z-scored log-odds.
- **`min_count >= 5`** in the higher brand: a label on 4 items is not a brand association.
- **Which criterion governs C3.** C3 difference statements (T3.a) are decided by the
  **log-odds z-score plus BH**, *not* by R-PAIRED's discordant-count test — the log-odds
  statistic is the one that shrinks rare labels toward the corpus prior, which is the whole
  point of using it here. R-PAIRED governs the raw share comparisons (C1, C8, C11); R-CLUSTER,
  R-PROVENANCE, R-MULTIPLICITY and R-MINN govern both. Earlier drafts stated R-NONOVERLAP as a
  universal rule and then used a different criterion in C3 without saying so.
- **Multiple comparisons:** the label grid (brands × subsets × labels) is large. Per
  R-MULTIPLICITY, Benjamini–Hochberg at `q = 0.10` is applied **across every comparison on the
  surface**, not per subset in isolation, and the computed/surviving counts plus the BH cut-off
  are reported in the audit view. Flagged as **OQ-10** because the tests are not independent
  (labels co-occur), so BH is approximate here; §3.10's permutation machinery is reused on the
  label grid to check it.

- **T3.a:** *"In the analysed sample, {Label Z} is associated with {Brand A} more than with
  {Subject} ({ka}/{na} vs {ks}/{ns} items; n_total={n})."*
- **T3.b (shared):** *"{Label Z} appears with both {Brand A} ({ka}/{na}) and {Subject}
  ({ks}/{ns}) at rates that are not distinguishable in this sample."*
- **T3.c (absent):** *"{Label Z} appears in {ka} {Brand A} items and no {Subject} items in this
  sample ({ns} {Subject} items analysed)."*
- **T3.d (insufficient):** *"{Subject} has {ns} labelled items in this sample — too few to
  compare {Label Z} associations (minimum 30 per brand for label comparison)."*

Applied verbatim to: consumer contexts, consumption behaviours, social contexts, cultural
themes, category associations, product pairings, and discovered emerging themes.

---

**C4 — Creative / content-format mix.** Same machinery as C3 over `content_format`.
- **T4.a:** *"Among {na} analysed {Brand A} items, {f1} is the most frequent content format
  ({ka}/{na}); among {ns} {Subject} items it is {f2} ({ks}/{ns})."*
- **T4.b:** *"{Format F} accounts for a larger share of {Brand A} items than of {Subject} items
  in this sample ({ka}/{na} vs {ks}/{ns})."*

---

**C5 — Creator-type mix.** Same machinery over `creator_archetype`, plus
`distinct_creators(x)`.
- **T5.a:** *"{Brand A} appears across {ca} distinct creators in this sample and {Subject}
  across {cs}."*
- **T5.b:** *"{Archetype A} creators account for {ka}/{na} of {Brand A} items and {ks}/{ns} of
  {Subject} items in this sample."*

---

**C6 — Organic / sponsored presence.** Two proportions with Wilson, using
`sponsored_likelihood >= 0.60`. The estimate's nature must be stated.
- **T6.a:** *"{ka} of {na} analysed {Brand A} items are estimated sponsored (CI {lo}–{hi}%),
  against {ks} of {ns} for {Subject}. Sponsorship is a model estimate from content signals, not
  a disclosed status."*
- **T6.b (insufficient):** *"Too few items with a reliable sponsorship estimate to compare
  ({na} and {ns})."*

---

**C7 — Engagement.** Median and IQR only, **per platform**, never pooled, never a mean.
A difference is stated only when **all** hold: `n_a, n_s >= 10`; **≥5 distinct creators per
brand** (engagement is clustered within creators far harder than presence is — one creator's
audience size drives every one of their items, so item-level tests on a few creators' content
are tests of those creators, not of the brands); a **cluster-bootstrap over creators** (2000
resamples, resampling creators not items) gives a 95% interval on the **Hodges–Lehmann shift**
that excludes 0; and the comparison survives R-MULTIPLICITY across the platform × metric × pair
grid.
**Mann–Whitney U is not used as the decision rule and the earlier draft's use of it was a
misstatement**: U tests stochastic dominance, so a significant U licenses *"items of one brand
tend to rank higher"*, not *"the median is higher"* — the median claim needs a location-shift
assumption the data does not supply. The Hodges–Lehmann shift with a creator-clustered bootstrap
estimates the quantity the sentence actually names, and degrades to T7.a (two medians, no
difference claim) whenever the interval covers 0.
- **T7.a:** *"Median {metric} for {Brand A} items on {platform} in this sample: {ma}
  (IQR {qa1}–{qa3}, n={na}); for {Subject}: {ms} (IQR {qs1}–{qs3}, n={ns}). Engagement reflects
  content distribution and performance, not consumer preference."*
- **T7.b (separated):** *"{Brand A} items on {platform} have a higher median {metric} than
  {Subject} items in this sample ({ma} vs {ms}, n={na}/{ns} items from {ca}/{cs} creators;
  estimated shift {hl}, 95% CI {hl_lo}–{hl_hi}). This is a property of the analysed content, not
  a measure of brand preference."*
- **T7.c (insufficient):** *"Fewer than 10 items, or fewer than 5 distinct creators, per brand on
  {platform} — median {metric} not compared."*
The trailing caveat in T7.a/T7.b is **mandatory and not removable**, per §28's prohibition on
equating engagement with preference.

---

**C8 — Marketplace presence.** Listing counts and share **within each leaf category, per
marketplace**, never summed across marketplaces (their collection coverage differs, so a sum is
an artefact of the run). Min: ≥20 listings in the category on that marketplace.
- **T8.a:** *"Among the {n} {marketplace} listings collected for {Category C} in this run,
  {Brand A} accounts for {ka} and {Subject} for {ks}. This reflects the listings this run
  collected, not marketplace-wide assortment."*
- **T8.b (insufficient):** *"Only {n} {marketplace} listings were collected for {Category C} —
  too few to compare brand presence."*
The second sentence of T8.a is **mandatory**: listing counts are the single most tempting number
to over-read as market share.

---

**C9 — Price positioning.** Median price **per normalised unit** with IQR, within a leaf
category, per marketplace. Min: ≥8 priced listings per brand with an extractable unit.
- **T9.a:** *"Within {Category C} on {marketplace} ({n} listings), the median collected price is
  {pa} per {unit} for {Brand A} (IQR {qa1}–{qa3}, {na} listings) and {ps} per {unit} for
  {Subject} (IQR {qs1}–{qs3}, {ns} listings)."*
- **T9.b (no unit):** *"Pack sizes could not be normalised for {na}/{ns} of the collected
  listings; price positioning is reported as a within-category percentile instead: {Brand A} at
  the {pca}th percentile, {Subject} at the {pcs}th."*
- **T9.c (insufficient):** *"Fewer than 8 priced listings per brand in {Category C} — price
  positioning not compared."*
Never compare raw listing prices across different pack sizes. T9.b exists so the comparison
degrades to a weaker statement instead of a wrong one.

---

**C10 — Product variants.** Count of distinct observed variants (from §1.6.3 residual
signatures and marketplace variant fields), explicitly observation-limited.
- **T10.a:** *"{ka} distinct {Brand A} variants and {ks} distinct {Subject} variants appear in
  this sample. Variants not present in the collected content or listings are not counted."*
Second sentence mandatory.

---

**C11 — Trend participation.** Share of each brand's items that fall in themes the trend
subsystem classified `emerging` or `growing`, computed **only** for themes where that subsystem
reports sufficient temporal coverage (§22). Min: R-MINN on the theme subset.
- **T11.a:** *"{Brand A} appears in {ka}/{na_theme} of the items in themes currently classified
  as growing or emerging; {Subject} in {ks}/{ns_theme}."*
- **T11.b (insufficient coverage):** *"Temporal coverage for {Theme X} is insufficient to
  classify it as growing or declining, so trend participation is not compared."*

---

**C12 — Content-cluster presence profile and the absence statement.** The brief's own example.
Reported only when `cluster_n >= 30` and the subject's Wilson **upper** bound lies below the
**median of the other brands' Wilson lower bounds** in that cluster (a conservative
"clearly below the pack" criterion rather than a bare point comparison).
- **T12.a:** *"{Subject} has relatively low presence within {Cluster Y} ({ks}/{subset_n} items)
  compared with {k} other brands present in that cluster (their presence ranges from {lo} to
  {hi} items)."*
- **T12.b (not separated):** *"{Subject} appears in {ks}/{subset_n} {Cluster Y} items, within
  the range observed for the other {k} brands present ({lo}–{hi})."*

---

**C13 — Shared versus divergent cultural themes.** From S11's label distributions: the
top labels where both brands are present above their respective Wilson lower bounds (shared),
and the top C3-differentiating labels for each side (divergent).
- **T13.a:** *"{Brand A} and {Subject} share association with {Label Z1} and {Label Z2} in this
  sample; {Label Z3} is associated with {Brand A} only ({ka} items, no {Subject} items)."*

---

**C14 — Detection-reason statement (required on every competitor row, §31.3).**
- **T14.a:** *"Detected from {signal_group_phrase}, across {evidence_count} distinct items
  ({n_social} social, {n_commerce} marketplace, {n_web} web). Relationship read as
  {relationship_type_phrase}; evidence strength: {confidence_band}."*
  where `signal_group_phrase` enumerates only the groups that actually fired, with their counts,
  e.g. *"explicit comparison (2 items), marketplace adjacency (14 listings), category search
  (4 queries)"*.
- **T14.b (unclear type):** *"Detected from {signal_group_phrase}, across {evidence_count}
  distinct items. The nature of the relationship is not yet characterised by the available
  evidence."*
- **T14.c (conflicting):** *"Detected from {signal_group_phrase}. Evidence conflicts:
  {n_sub} items read as substitution and {n_co} as joint consumption; the relationship is
  reported as unclear."*

### 7.3 The phrasing registry

```python
# phrasing.py
class TemplateId(StrEnum):
    T1A = "T1.a"; T1B = "T1.b"; T1C = "T1.c"; T1D = "T1.d"
    T2A = "T2.a"; T2B = "T2.b"; T2C = "T2.c"
    # ... one member per template above
    T14A = "T14.a"; T14B = "T14.b"; T14C = "T14.c"

TEMPLATES: Final[Mapping[TemplateId, str]] = {...}
REQUIRED_SLOTS: Final[Mapping[TemplateId, frozenset[str]]] = {...}
TEMPLATE_VERSION: Final[str] = "2026-10-02.1"

class MissingScopeError(ValueError): ...
class ForbiddenPhrasingError(ValueError): ...

def render(tid: TemplateId, **slots: object) -> str:
    """Renders a template. Raises MissingScopeError if any required slot (always including
    the sample-size slots) is absent or None, and ForbiddenPhrasingError if the rendered
    string matches FORBIDDEN_PATTERNS. Returns the rendered string only."""
```

Properties that make this more than a string table:
- **Closed set.** The UI and the findings subsystem may only emit comparison text produced by
  `render`. Free-text comparison strings are not permitted anywhere in this subsystem's output,
  because a free-text string is exactly where an unscoped or causal claim gets in.
- **Scope slots are mandatory.** A template cannot be rendered without its `n` / `subset_n`.
- **Version-stamped.** `TEMPLATE_VERSION` is stored on each cached comparison so a past finding
  can be re-read with the wording it was generated under (§27 auditability).
- **Tested against the forbidden-pattern list**, over every template with representative slot
  values, as a unit test — so a careless wording change fails CI rather than shipping.

---

## 8. COST AT SCALE

Per full research run, assuming ~1500 analysed videos and ~2500 commerce records. Prices:
`claude-opus-5` $5/Mtok in, $25/Mtok out; `text-embedding-3-large` $0.13/Mtok.

| component | volume | tokens | cost |
|---|---|---|---|
| **Surface embeddings** (Tier 3 recall) | ~15k surfaces extracted; K6 skipped when K1–K5 hit a confirmed brand (~88–92% in a warmed registry) → ~1.8k embedded at ~52 tok each | ~94k in | **$0.012** |
| **Alias embeddings** (one-off per new alias) | ~1.5k new aliases/run × ~15 tok | ~23k in | **$0.003** |
| **Tier 4 brand adjudication** | ~3% of 15k surfaces ambiguous ≈ 450; batches of 40 → 12 calls; ~13k in / ~2.5k out per call | 156k in / 30k out | **$1.53** |
| **S1 comparison classifier** | ~400 multi-brand videos → pre-filter keeps ~45% ≈ 180; batches of 10 → 18 calls; ~7.4k in / ~1.5k out | 133k in / 27k out | **$1.34** |
| **S7 relation span classifier** | ~600 multi-brand spans → pre-filter keeps ~55% ≈ 330; batches of 25 → 14 calls; ~5.2k in / ~2k out | 73k in / 28k out | **$1.07** |
| **S8 web ranking extraction** | ~40 Serper queries + ~40 listicle docs → 4 calls of 10 docs | 80k in / 12k out | **$0.70** |
| **Serper calls** | ~40 queries | — | **~$0.12** |
| **Total, typical** | | | **≈ $4.8** |
| **Total, pessimistic** (ambiguity rate 3×, pre-filters half as selective) | | | **≈ $11** |

Notes that matter for planning:
- **Prompt caching** on the three instruction blocks (each ~700–900 tokens, re-sent on every
  call) removes roughly 10–15% of input cost at these volumes. Worth enabling, not load-bearing
  — and **check that it actually caches**: the minimum cacheable prefix is model-dependent and
  can be above 1024 tokens, in which case a 700–900-token block silently does not cache at all.
  Verify with `usage.cache_read_input_tokens` on the second call rather than assuming the saving.
- **The output-token estimates below exclude thinking.** `thinking={"type":"adaptive"}` bills
  reasoning tokens as output at $25/Mtok, and 2.5k output tokens for a 40-verdict batch is an
  estimate of the *answer*, not of the answer plus its reasoning. Treat the typical figure as a
  floor and the pessimistic figure as the planning number until the first run reports real
  `usage`.
- **The pre-filters are a latency optimisation, not a cost necessity.** Running S1 over all ~400
  multi-brand videos with full transcripts is ≈ $6, and S7 over all ~600 spans ≈ $2. Both remain
  affordable, which is the right posture: if a pre-filter is suspected of hurting recall, it can
  be disabled for a diagnostic run without a budget conversation.
- **Effort levels.** All three classifiers run at `effort="low"`; brand adjudication escalates to
  `"medium"` when ≥3 candidates or either candidate already carries ≥20 distinct items (≈8% of
  adjudications, adding ≈$0.4).
- **Non-LLM compute is negligible.** All pair mathematics is SQL aggregation plus numpy over a
  few hundred to a few thousand pairs; the HNSW index over ~5k alias vectors answers ~1.8k ANN
  queries in well under a second. No GPU, no Redis, no external queue.
- **Re-run cost.** Because Tier-4 decisions and S1/S7 verdicts are persisted, a repeat run over
  an unchanged corpus costs **$0** in LLM calls — the aggregate/type/decay stages are pure SQL.
  Only genuinely new content is classified.
  **The cache key is not (item, span, surface).** A Tier-4 verdict answers *"is this surface the
  same brand as **these candidates**"*, and `candidate_key` is explicitly an opaque per-call
  token (§1.5). Replaying a verdict against a later run's candidate set — which has grown,
  because the registry grows every run — maps the stored key onto a *different brand row*, and
  the result is a silent wrong merge: precisely the catastrophic, near-undetectable failure that
  P1 and the whole cascade exist to prevent, arriving through the cost optimisation. The cache
  key is therefore
  `(surface_norm, channel, sha256(sorted candidate brand_ids), prompt_version, model, effort)`,
  and the stored verdict records the **resolved `brand_id`**, never the candidate key. Any
  change to the candidate set is a cache miss and costs one adjudication, which is the correct
  price. The same applies to S1/S7 verdicts, whose key includes the item's resolved brand-id set
  and the prompt version. With this key a genuinely unchanged corpus still costs $0; a corpus
  whose registry moved pays only for what moved.

---

## 9. GRACEFUL DEGRADATION (§7)

Capability keys are resolved once per run into `DetectionContext.capabilities`; each detector
declares `requires` and is skipped (not failed) when a key is missing. The run records which
groups were available; `Q_coverage = sqrt(available/5)` applies the honest confidence penalty.

| missing | immediate effect | consequences |
|---|---|---|
| `APIFY_TOKEN` | no TikTok / Instagram / Facebook / Shopee / Lazada | S5 and S9 unavailable → `COMMERCE_SURFACE` group drops → `available = 4`, `Q_coverage = 0.89`. `same_shelf` becomes assignable only by R3b (retail-scene social items). `adjacent_category` (R5) cannot fire, since `cat_sim` is NULL. C8/C9 render their insufficient-data templates. The corpus shrinks to YouTube + public web, so S2/S6/S3/S10 all operate on fewer items and `V` falls naturally. |
| `YOUTUBE_API_KEY` | no official YouTube search | YouTube contributes only via `WebSearchCollector` discovery; no group is lost, but coverage and `V` fall. S8's web tier is unaffected. |
| both missing | web/Serper discovery only | `available = 4`, and social volume is low enough that most pairs sit below the 0.15 write threshold. This is the correct outcome: the run reports few competitor relationships and says why, rather than reporting weak ones confidently. |
| clustering subsystem not yet run | S3 unavailable | `DISTRIBUTIONAL` group still available via S10/S11, so `available` stays 5; S3 is simply absent from `signal_strengths` and the audit view says so. |
| taxonomy/labels unavailable | S11 unavailable, C3/C13 unavailable | as above; `DISTRIBUTIONAL` survives on S3/S10. |
| no commerce **and** no clusters/labels | `available = 4`, `Q_coverage = 0.89` | S10 (`shared_creator`) needs neither clusters nor labels, so `DISTRIBUTIONAL` survives and only `COMMERCE_SURFACE` is lost. An earlier draft said `available = 3` here, contradicting the two rows above it. `available` drops to 3 only when commerce **and** creator identity are both unavailable (no `creators` table populated), which also removes S10. |
| `SERPER_API_KEY` missing | no web search / web discovery | S8 loses its web-document tier and S4 loses its non-platform fallback. `EXPLICIT_COMPARE` survives on S1/S7 and `DISCOVERY_SURFACE` on platform queries, so no group is lost, but `C14`'s web counts go to zero and the run view must say so. With `APIFY_TOKEN` **also** missing, discovery has no source at all and the run cannot proceed past collection — this is the one case the subsystem reports as a failed run rather than a degraded one. |
| no embedding provider (`OPENAI_API_KEY` unset and `JINA_API_KEY` unset) | K6 blocking and Tier 3 unavailable; S9's `embed_sim` component `NULL` | Resolution loses only its *last-resort recall* key — K1–K5 are string-based and unaffected — so the effect is more unresolved surfaces, not wrong merges (Tier 3 could never authorise a merge anyway, P3). S9 renormalises over its remaining components. `resolution_quality` falls, which flows into `Q_resolution` honestly. The run records `embedding_provider = none`. |
| no canonical retail/assortment scene label (doc 03 has no `scene_context` enum, by design) | R3b weakened | `same_shelf` from social content keys on *discovered* setting labels (XS-5) plus `quantity_visible='many'` with no brand in a consumption state. This is a weaker test than a dedicated enum, so `same_shelf` typed by R3b alone keeps its 0.55 cap and the UI shows the setting labels that triggered it. If the taxonomy subsystem has not yet canonicalised setting labels, R3b does not fire and `same_shelf` rests on R3a (commerce) only. |
| vision frame-level detections absent (only flat `visible_brands`) | S2 loses `simultaneity` (falls to the 0.60 floor), D2 cannot be computed | `co_consumed` can then only be typed via R2a (language), and the discriminator loses its second-strongest cue. The run records `frame_level_detections=false`, which the UI must surface on any `co_consumed` row. |

Every skipped detector writes a `competitor_signal_scores` row with
`detail = {"skipped": true, "reason": "capability_missing:<key>"}`, so the research-run view
(§32) can show exactly which signals were and were not available — the absence is data, not a
gap in the record.

---

## 10. SERVICE AND API SURFACE

```python
# service.py
class CompetitorDiscoveryService:
    async def resolve_run_surfaces(self, run_id: UUID) -> ResolutionReport: ...
    async def enumerate_pairs(self, run_id: UUID) -> PairSet: ...
    async def detect_signals(self, run_id: UUID, pairs: PairSet) -> SignalReport: ...
    async def aggregate_and_type(self, run_id: UUID) -> list[RelationshipUpsert]: ...
    async def apply_decay(self, run_id: UUID) -> DecayReport: ...
    async def build_comparisons(self, run_id: UUID, subject_brand_id: UUID,
                                ) -> list[RenderedComparison]: ...

@dataclass(frozen=True, slots=True)
class RenderedComparison:
    computation: Literal["C1","C2","C3","C4","C5","C6","C7","C8","C9","C10","C11","C12","C13","C14"]
    template_id: TemplateId
    template_version: str
    text: str
    subset: SubsetSpec
    n: int
    subset_n: int
    brands: tuple[UUID, ...]
    evidence_ids: tuple[UUID, ...]
    statistics: Mapping[str, float]      # k, p, wilson bounds, z, p_value
    is_difference_claim: bool
```

Routes (`api/routes/competitors.py`):
```
GET  /brands/{brand_id}/competitors          ?state=active|all&type=&band=&audit=false
     -> rows ordered by evidence_count DESC, first_detected ASC; no rank/score fields.
        Each row carries BOTH evidence_count (pair) and type_evidence_count (this reading),
        plus band_scheme_version. The response envelope carries the run's
        pair_fdr_estimate (§3.10); the client MUST render the chance-expectation sentence
        when it is >= 0.10 and MUST render the insufficient-evidence surface instead of the
        list when it is >= 0.50.
GET  /competitors/{relationship_id}/evidence ?signal=&unit=&page=
     -> the full ledger, with quotes, frame windows, listing links, item thumbnails
GET  /competitors/{relationship_id}/audit
     -> signal_strengths, group_strengths, diversity walk, quality_factors, typing rule id,
        skipped detectors with reasons, template version
GET  /brands/{brand_id}/comparisons          ?subset=cluster:<id>|theme:<id>|category:<path>
     -> RenderedComparison[]
GET  /brands/unresolved-surfaces             ?run_id=   (§1.7 product surface)
POST /brands/review/{review_item_id}          {accept|reject}  (§1.6.4)
GET  /runs/{run_id}/competitor-discovery      (§32 run view: pairs enumerated, capped,
                                               detectors run/skipped, seeds confirmed/absent)
```

---

## 11. OPEN QUESTIONS

These are genuine uncertainties, not placeholders. Each states what would resolve it.

**OQ-1 — S9 embedding-similarity floor.** `FLOOR = 0.80` for the positioning-cosine rescale is
a placeholder. The design already specifies the calibration (20th percentile of same-leaf-category
brand-pair cosines, recomputed per run and stored), but the shipped default is unvalidated and
the *shape* of the within-domain cosine distribution for this corpus is unknown. Resolution:
compute the distribution on run 1 and inspect it before trusting any S9 number.

**OQ-2 — Fuzzy thresholds against real OCR/ASR corruption.** `fuzzy_auto = 0.88`, the confusion
classes, and G-INIT/G-PREFIX are reasoned from how OCR and ASR fail in general, not measured on
this corpus's packaging and code-switched speech. Resolution: hand-label ~300 surface→brand pairs
from run 1 (stratified by channel) and tune `fuzzy_auto`, `margin_min` and the class tables
against precision at fixed recall. Until then the ambiguous band is deliberately wide, which
costs LLM calls rather than correctness.

**OQ-3 — Visual discrimination of co-consumption vs substitution.** D2's `overlap_frac` cut-offs
(0.35 / 0.50 with an undecided gap) and `both_active_frac` (0.20 / 0.40) are reasoned from frame
sampling geometry, not measured. It is genuinely unclear whether vision alone can separate these
two relationships at acceptable precision. Resolution: label ~80 two-brand videos for the true
relation and measure. The 0.55 confidence cap on visual-only typing is the hedge in the meantime.

**OQ-4 — Marketplace relational blocks.** Whether the configured Apify actors expose
"similar products" and "frequently bought together" is unknown. If they do not, discriminator D5
is permanently absent and `same_shelf` versus `category_peer` rests on seller-assigned category
paths alone — a materially weaker basis.
**Correction to an earlier draft: `APIFY_TOKEN` is present in this project's environment, and
the actors are already chosen** (`apify_shopee_actor`, `apify_shopee_reviews_actor`,
`apify_lazada_actor` in `apps/api/aypo/config.py`). This is therefore not an open research
question but a one-afternoon capability probe: run each actor once against a single listing and
record which relational blocks appear in the dataset. Until that probe runs, D5 must be
implemented as strictly conditional — which it already is — and `same_shelf` must not be
presented as better evidenced than R3a's category test supports.
The credential that **is** absent in this environment is `YOUTUBE_API_KEY`; its degradation path
is the second row of §9 and no group is lost by it.

**OQ-5 — Cross-platform creator identity.** The same person on TikTok and YouTube currently
counts as two creators, which **inflates** S10's `shared`, S6's `creator_breadth` and C5's
`distinct_creators` alike — every one of them over-reports independence, and the new R-CLUSTER
rule (§7.1) is built on the same creator counts, so its design-effect correction is
correspondingly too weak. The error is in the anti-conservative direction throughout, which is
why this is the open question most likely to require a rendered-statement retraction.
Resolution requires a creator-resolution capability (handle/name/avatar matching) that this
subsystem does not own; until it exists, C5 and T7.b must say "distinct creator accounts", not
"distinct creators".

**OQ-6 — Corporate ownership.** `same_corporate_family` can only be true when ownership is
supplied, human-confirmed, or visible as a corporate endorsement on packaging. The sample cannot
reveal it. The live risk is presenting a brand's own stablemate as a discovered competitor.
Options: (a) accept a Serper ownership lookup as third-party evidence with explicit uncertainty;
(b) require human confirmation via `brand_review_items(kind='reparent')`. This design does (b)
and never auto-asserts; (a) is a product decision, not a technical one.

**OQ-7 — Recency half-life versus run cadence.** `H = 90` days assumes quarterly-ish runs. At
weekly cadence it is far too slow for `competitor_relationship_events` to show movement, and the
decay state machine (counted in runs) and the continuous weighting (counted in days) would drift
apart. Needs a per-project cadence setting and probably `H` derived from the median inter-run
interval.

**OQ-8 — Reading of the brief's `evidence_count`.** Interpreted here as *distinct, unweighted
content items*, with the weighted/decayed quantity exposed separately as `n_eff`. The alternative
(a signal-weighted count) would be a single number that is neither honest as a sample size nor
usable as a belief. Flagged because it is an interpretation, not a given.

**OQ-9 — Cluster id stability across runs.** HDBSCAN does not guarantee stable cluster ids, so
cluster-unit evidence can appear to be "new" when the cluster is merely renumbered, inflating
`last_detected`. The interim mitigation (anchor-item overlap via `detail.anchor_item_ids`) is in
this design, but a proper fix belongs to the clustering subsystem.

**OQ-10 — Multiple comparisons across the comparison grid.** This revision replaced the
interval-non-overlap decision rule with R-PAIRED (a discordant-count test, because the two
proportions share a denominator and are negatively correlated, which made the unpaired rule
anti-conservative rather than conservative) and added R-MULTIPLICITY (BH at `q = 0.10` across
the whole surface rather than per subset). What remains open is whether BH is adequate: the
tests are not independent — labels co-occur, the same items appear in several subsets, and
R-CLUSTER's design-effect correction is itself estimated from the same data. Resolution: reuse
§3.10's permutation machinery on run 1's label grid to get an empirical null, and either keep BH
or raise `z_min` to whatever that null says. A second, smaller question: R-CLUSTER's `rho_icc`
is estimated per subset by one-way ANOVA, which is itself unstable below ~10 creators; the floor
at 5 distinct creators is a guess.

**OQ-11 — Volume scale.** `V`'s scale of 8 effective items is the single most consequential
magic number in §3 and is reasoned from "0.5% of a 1500-item corpus", not measured. If real runs
produce far fewer or far more items per pair than assumed, every band boundary shifts with it.

**OQ-13 — The diversity walk's `min_group_strength = 0.25` cut-off.** It is a hard threshold on
a continuous quantity: a group at 0.249 earns no diversity credit and one at 0.251 earns up to
`1/n_available`, and in §3.7's recomputed example it is what costs the pair 0.1 of `D`. The
justification (0.25 is roughly where a group alone could not clear the 0.15 write threshold) is
reasoned, not measured. A smooth ramp over `[0.15, 0.35]` is the obvious alternative and should
be compared on run 1's distribution.

**OQ-14 — Band boundaries after the §3.7 correction.** With the saturation term correctly
applied to every signal, a pair with eleven signals and 40 items lands at 0.46. The fixed
cut-offs 0.35 / 0.55 / 0.75 were justified against the uncorrected arithmetic and may now be too
high, concentrating almost everything in `moderate`. §3.9.4 specifies the run-1 check (no band
holding >70% or <2% of written pairs) and the `band_scheme_version` column that makes a
boundary change auditable rather than silent. The decision cannot be made before run 1.

**OQ-15 — The permutation null's parameters.** §3.10 requires a label-permutation null to
quantify the selection effect across up to 2000 enumerated pairs. `B = 50` replicates and the
stratification variables `(source_kind, platform, marginal-count decile)` are reasoned, not
measured: too few replicates and `pair_fdr_estimate` is itself noisy; strata too fine and the
permutation stops destroying the structure it is meant to destroy. The requirement that *a* null
is computed and displayed is not in question; its calibration is.

**OQ-12 — Within-signal saturation shape.** `1 - exp(-raw/3)` was chosen over noisy-OR to keep
strength informative across 1–15 items. Both are defensible; the choice visibly moves band
assignment for mid-evidence pairs and deserves a sensitivity check once real distributions exist.

---

## 12. CROSS-SUBSYSTEM CONTRACTS

| id | contract |
|---|---|
| **XS-1** | Video analysis emits per-brand **frame index sets** (`VisibleBrand.frame_indices`), `prominence`, `screen_share`, `legibility` and `identification_basis`, plus `VisibleProduct.consumption_state` with `brand_text_as_seen` so a consumption state is attributable to a brand. Satisfied by doc 03 as written — see §12.1. A flat brand list with no frame indices would break S2's simultaneity, R2b, R3b and discriminator D2. |
| **XS-2** | All brand-like text is emitted as **raw, unnormalised surface strings** (`brand_text_as_seen`, `brand_text_as_said_or_written`) with channel, confidence, context and position, never pre-resolved to brand ids or names. Resolution belongs to this subsystem alone. Doc 03's CSR-6 states the same contract from the other side, including the reason: fuzzy matching inside the analysis prompt is precisely the mechanism that would make the model "see" expected brands. |
| **XS-3** | Transcripts carry token/ms offsets so mention windows and relation spans are addressable and quotable. |
| **XS-4** | Clustering **must not** use brand identity as an input feature, or S3 becomes circular. Clusters expose stable ids (or anchor items), size and member item ids. |
| **XS-5** | The open-label taxonomy exposes canonical label ids, per-label corpus prevalence and per-item label assignments. |
| **XS-6** | Every `research_queries` row carries `sampling_strategy`, `names_known_brand` (did the query string match any alias at issue time) and `generated_from` provenance; `research_results` carries `(research_query_id, content_item_id | commerce_item_id)` so the query→item mapping is a join, not an array. S4 and S5(b) are unusable without these, and after this revision so are **S2/S6's npmi** (brand-agnostic stratum, §S2) and **every §7 comparison** (R-PROVENANCE). An item with no `research_results` row cannot be placed in a stratum and is excluded from all stratified statistics, so collection must never drop the link — this contract moved from "needed by two detectors" to "needed by most of the numbers in this document". |
| **XS-7** | Commerce collectors store the originating search query, leaf category path, seller id, raw title, extractable pack/unit, and — when the actor provides them — the "similar products" and "bought together" blocks (nullable). |
| **XS-8** | Creator identity is stable and deduped at least within a platform, with a follower percentile computed within the sample. |
| **XS-9** | Each content item carries a `sponsored_likelihood` estimate in [0,1]. |
| **XS-10** | The findings subsystem consumes per-relationship confidence, band and `RenderedComparison` objects, and must **not** re-aggregate them into any composite competitor score or ranking. |
| **XS-11** | The Postgres-backed job queue supports per-run stage ordering with advisory locks on `(run_id, stage)`; no Redis, no Celery. |
| **XS-12** | Other subsystems read resolved brands through the `v_item_brands` view, never by stamping a `brand_id` onto content rows — this is what makes resolution reversible (P2). |
| **XS-13** | Collection performs near-duplicate collapse and exposes a stable `content_items.id` per distinct item, so re-uploads of the same video cannot inflate `evidence_count`. |
| **XS-14** | The research engine exposes its competitor-level query budget so §6.2's 25% seeded-query cap is enforceable and auditable. |

### 12.1 Binding to the analysis subsystem's actual output (doc 03)

Doc 03 (`multimodal analysis`) deliberately stops short of competitor semantics (its CSR-5:
"competitor discovery owns the word competitor") and hands this subsystem raw material. Its
field names are the real contract, so the detectors bind to them directly:

| this design | doc 03 field | binding notes |
|---|---|---|
| surface (`packaging_vision`) | `VisibleBrand.brand_text_as_seen` | unnormalised by construction (CSR-6), exactly what §1.2 expects |
| surface (`spoken` / `written`) | `MentionedBrand.brand_text_as_said_or_written` | `mention_kind ∈ {spoken, caption, hashtag, on_screen_text, comment}` maps 1:1 onto `MENTION_SOURCE_WEIGHT`; `speaker_role='commenter'` reinforces the 0.40 comment weight |
| frame co-presence (S2 `simultaneity`, D2 `overlap_frac`) | `VisibleBrand.frame_indices` (3–20 frames per video by duration tier, `max_frames = 32`) | **computed over frame index sets, not timestamps**: `overlap_frac = \|F_a ∩ F_b\| / \|F_a ∪ F_b\|`, and `simultaneity` counts items where `F_a ∩ F_b ≠ ∅`. This replaces the ±2.0s window with an exact set intersection over the frames actually sampled — strictly better, and it removes the dependence on a fixed sampling rate. The ±2s rationale survives as the fallback when only timestamps are available. |
| prominence weighting | `VisibleBrand.prominence`, `screen_share` | `PROMINENCE_W` keys off `prominence`; `screen_share ∈ {dominant, moderate, minor}` is used for C2 visual share when per-frame seconds are unavailable |
| resolution difficulty prior | `VisibleBrand.legibility`, `identification_basis` | **used to modulate the cascade**: `legibility='partial'` raises `max_edits_for_length` by 1 (a partially legible wordmark is a known-truncated string, so the bound should be looser) while *also* requiring Tier 4 adjudication rather than fuzzy auto-resolve; `identification_basis='logo_mark'` means no text was read, so the surface is routed to embedding/LLM only and never to fuzzy; `legibility='illegible'` surfaces are not resolved at all |
| D2 `both_active_frac`, R2b, R3b | `VisibleProduct.consumption_state` + `VisibleProduct.brand_text_as_seen` + `quantity_visible` | consumption state is attributable to a brand only when `brand_resolved` is true on the product; D3's veto reads `consumption_state` across all evidence items |
| R3b retail/assortment context | `setting_observations`, `format_features`, `quantity_visible='many'` | doc 03 has **no** `scene_context` enum (by design — no predefined taxonomy). R3b therefore keys on *discovered* setting labels canonicalised by the taxonomy subsystem (XS-5) plus `quantity_visible='many'` with no brand in a consumption state. This is weaker than a dedicated enum would be and is the honest implementation; it is noted in the §9 degradation table. |
| S1 pre-filter and D1/D4 prior | `brand_co_appearance[].framing` | `framing ∈ {side_by_side_comparison, ranking_or_ordering, substitution_discussed, incidental_co_presence, unclear}` is a **free, deterministic pre-signal**. S1's LLM pre-filter admits any item with `framing ≠ incidental_co_presence`, which is cheaper and better targeted than the structural lexicon alone; `substitution_discussed` additionally routes the item's spans to S7 regardless of cue hits. `incidental_co_presence` contributes to S2 only. This measurably cuts S1/S7 call volume below the §8 estimate. |
| sponsorship | `SponsorshipAssessment` | supplies `sponsored_likelihood`; doc 03's rule that framing cues alone cannot exceed `possibly_paid` means the 0.60 threshold in S7/`Q_sponsored` sits above cue-only assessments, which is the intended conservatism |
| claim filtering | `content_analysis_claims.claim_status`, `analysis_config_id` | **every** query in this subsystem filters `claim_status='published'` and scopes to one `analysis_config_id` (doc 03 CSR-7 — the governing contract is CSR-7 on denominators/statuses; CSR-4 is about embeddings reading from claims and was cited here in error). Mixing prompt generations inside one pair's evidence would make `evidence_count` incomparable across items. The `analysis_config_id` is recorded on `competitor_signal_scores.detail` so a relationship can be re-derived under the generation it was computed from. |
| denominators | doc 03 CSR-7 | `N` in S2/S6's npmi and `subset_n` in every §7 computation use doc 03's published denominators and its exclusion counts, not a raw `count(*)` over `content_items`. |

One consequence worth stating plainly: because `brand_co_appearance.framing` already separates
comparison from incidental co-presence at analysis time, discriminator D1 gets a cheap prior
*before* any S7 call, and D4's verdict detection has a deterministic trigger. This does not
change any threshold in §4.2 — the decision table still orders by human checkability, and
`framing` is a model judgement with no stored quote, so it may **gate** an LLM call but may
never by itself satisfy a typing rule.

---

## 13. REVIEW NOTES (adversarial review, 2026-10-02)

This document was reviewed against `docs/BRIEF.md` (§1, §3, §7, §22, §23, §24, §27, §28, §34),
against `docs/design/03-multimodal-analysis.md` (the contracts in §12.1), and against the
repository's actual configuration (`apps/api/aypo/config.py`, `.env`). The design's core
architecture survived: the observation ledger as the unit of record, the six-tier cascade with
embeddings barred from authorising merges, correlation groups with within-group `max`, the
item-disjointness walk, the aggregate-only treatment of S9/S11, the five discriminators ordered
by human checkability, the seed non-privilege rules, and the closed phrasing registry are all
sound and are unchanged. The §1/§34 compliance is genuinely strong — the only two market-specific
artefacts found in the whole document were a default argument and one word in a regex, both now
removed. What follows is what was wrong.

### 13.1 Fatal — fixed

1. **The descriptor rule could erase the research subject.** `is_descriptor := brand_df >= 5 OR
   corpus_df_share > 0.02` had a route to descriptor status that never looked at `brand_df`, and
   the token certain to exceed 2% corpus frequency is the name of the brand the run is
   researching, because §8 strategy A searches for it on purpose. Tier 5's stopword guard then
   re-tested the same bare frequency. On run 1 the subject brand would have been marked a
   product word and refused creation. Compounding it, the stated remedy
   (`requires_context = true` with no learned `context_terms`) violates `alias_ctx_chk` and
   would have aborted the write. §1.3 now requires `brand_df >= 3` on the frequency route,
   computes that frequency over the **brand-agnostic stratum**, never marks the sole core token
   of a confirmed brand, and degrades through §1.7's ambiguity path instead of an impossible
   constraint. §1.5's guard now keys on `is_descriptor` only.
2. **The LLM-verdict cache could manufacture the catastrophic wrong merge.** §8 claimed a $0
   re-run because Tier-4 decisions are persisted "keyed on (item, span, surface)". But a Tier-4
   verdict answers a question about *a specific candidate set*, and `candidate_key` is an opaque
   per-call token; replaying it against a later run's larger candidate set maps the key onto a
   different brand. The cost optimisation re-introduced exactly the silent fusion of two brands'
   evidence bodies that P1, P3, P4 and the entire cascade exist to prevent. The key is now
   `(surface_norm, channel, hash(candidate brand_ids), prompt_version, model, effort)` and the
   stored verdict records the resolved `brand_id`, never the key.

### 13.2 Serious — fixed

3. **Collection-time sampling bias inside the corpus statistics.** S2 and S6's npmi marginals
   `n_a`, `n_b` are substantially a record of *what the agent searched for*; npmi falls as `n_a`
   rises, so the most-searched brand — always the research subject — had its lift deflated
   against every partner, while two never-searched brands could post a high npmi off three
   incidental co-appearances. The design had already invented the correct guard for S4
   (`names_known_brand = false`) and not applied it anywhere else. npmi is now computed on the
   brand-agnostic stratum with a 200-item floor, both values stored for audit.
4. **The same bias ran through every §24 comparison**, where it matters more because the output
   is a sentence a marketer reads. New rule **R-PROVENANCE** restricts C1–C8 and C11's subsets
   and denominators to the brand-agnostic stratum; full-corpus figures survive in the audit
   panel, labelled not-comparable.
5. **Non-independence treated as independence.** Wilson intervals over `n` items assume `n`
   independent draws; items cluster hard within creators. New rule **R-CLUSTER** applies a
   creator design-effect correction, requires ≥5 distinct creators, and displays raw `n`
   alongside `n_eff_stat`.
6. **R-NONOVERLAP's justification was backwards.** The two proportions in C1 are measured on the
   same items, so they are paired; when brands rarely co-occur the covariance is negative and the
   independent-sample procedure *understates* the variance of the difference. The claim that
   non-overlap is "strictly more conservative than a two-proportion z-test" was false for this
   data, and the claim that it "needs no multiple-comparison correction" was false outright.
   Replaced by **R-PAIRED** (discordant-count criterion, `b + c >= 10`) plus **R-MULTIPLICITY**
   (BH at `q = 0.10` across the whole surface, with computed/surviving counts displayed).
7. **No control for selection across the pair set.** Up to 2000 pairs are enumerated and the
   survivors reported; nothing anywhere estimated how many would survive by chance. New §3.10
   specifies a stratified label-permutation null, a stored `pair_fdr_estimate`, a mandatory
   chance-expectation sentence at ≥0.10 and suppression of the whole list at ≥0.50.
8. **The worked examples did not follow §3.2.** S3, S4, S5, S8 and S10 were scored at their pair
   scale with the `1 - exp(-raw/3)` saturation silently omitted, and `recency` was 1.0 inside
   `raw_s` but 0.85 inside `n_eff`. The headline example overstated confidence by about 20 points
   and promoted the pair from `moderate` to `strong`. Both examples are recomputed; §3.9.4's band
   descriptions, which were calibrated against the wrong arithmetic, are replaced by what the
   formula actually yields plus a run-1 calibration requirement and a `band_scheme_version`
   column.
9. **Aggregate signals could carry a group alone.** The halving did not deliver "corroborate,
   never create", because the group `max` let an aggregate with zero item evidence — and no
   saturation and no recency — set the group strength. A corroboration clamp now caps an
   aggregate at 1.5× the best item-based signal in its group and forbids it from creating a
   non-zero group.
10. **`evidence_count` is a pair quantity rendered next to a type claim.** T14.a would have shown
    a 40-item sample size beside a reading that rested on 5 items. Added `type_evidence_count`,
    written by the `type` stage from `required_evidence_ids`, and required by every type-scoped
    template.
11. **Momentum claims were contaminated.** `band_changed` rows are produced by new evidence, by
    pure recency decay, by re-collection variance, and by recalibration (S9's per-run `FLOOR`,
    the band scheme). §5.3 now carries a derived `cause` column and permits momentum rendering
    only from `cause = 'new_evidence'` in runs with coverage.
12. **D2's thresholds were applied to a 3-element denominator.** Doc 03's frame budget is 3–20
    frames per video, so `overlap_frac` is coarsely quantised and the "deliberately undecided"
    band `[0.35, 0.50)` contains no attainable value at `|F| = 3`. D2 now requires
    `|F_a ∪ F_b| >= 6` and `|F_a ∩ F_b| >= 2`. The related claim that "frame sampling runs at
    ~1 fps" was simply wrong and is corrected.
13. **Numbers an LLM invented were reaching scores.** New §2.0.4 fixes the boundary: ranks are
    validated against a deterministic ordinal scan, brand keys against the keys supplied for that
    item, `quote` against exact-substring containment (D1's entire human-checkability claim
    rested on a prompt instruction, not a check), and self-reported `confidence` is bucketed
    before being used as a multiplier.
14. **S8 squared its tier weight** (`strength = adjacency * tier_weight` and then `× e.weight` in
    §3.2). Fixed; `strength` carries evidence quality only.
15. **The typing cap landed on the wrong side of the band boundary.** `min(conf, 0.55)` with
    `confidence >= 0.55 -> 'strong'` clamped capped relationships *into* the band the cap existed
    to exclude. The constant is now 0.549, with a unit test.
16. **`aggregate` and `type` were circular.** The §5.3 upsert carried typing columns and a capped
    confidence while running in the stage before typing. §0.6 now specifies the two-phase
    transaction and moves the upsert to `type`.
17. **Broken SQL.** `evidence_count` read `canonical_item_ids` out of `detail` when it is a
    column (every cluster/creator/query pair would have counted zero); S2's base query selected
    from a table `assortment_items` that is never defined anywhere in the design;
    `resolution_quality` was an expression with no `FROM`, and could not attribute `ambiguous`
    observations at all because those have `brand_id IS NULL`; `v_item_brands` contained
    `coalesce(b.id, b.id)` and, more seriously, did not roll up to the analysis level despite
    §1.6.1 and XS-12 promising that it does; npmi divided by zero at `p_ab = 1`.
18. **`Q_temporal` could be satisfied by a clock.** "≥2 distinct collection days" is a property of
    when the collector ran — a run straddling midnight would have given every pair full temporal
    credit. Publication dates only; undated evidence takes the penalty. Relatedly, `recency`
    falling back to `detected_at` gave undated items full weight and let a pair be freshened by
    re-collection; undated items now take the 10th-percentile recency of dated items.
19. **`max_edits_for_length(n)` never said what `n` was**, making the fuzzy bound asymmetric in
    its two arguments: the same pair admitted different edit budgets depending on which side the
    cascade held. Now `min(len(a), len(b))`, with the reason stated.
20. **§9 contradicted itself** ("no commerce and no clusters/labels → available = 3" while two
    rows above establish that S10 keeps `DISTRIBUTIONAL` alive), and had no row for a missing
    web-search credential or a missing embedding provider. Fixed and extended.
21. **API shape and cost.** `output_format=` is the deprecated structured-output parameter
    (current: `output_config={"format": ..., "effort": ...}`); `max_tokens` was absent; adaptive
    thinking bills reasoning as output, so the 2.5k-output estimate is a floor; 700–900-token
    instruction blocks may fall below the minimum cacheable prefix and silently not cache. Model
    and effort are now required to come from `Settings` rather than three literal
    `"claude-opus-5"` strings, which had opted this subsystem out of `AYPO_ANALYSIS_MODEL` and
    `AYPO_MAX_LLM_USD`; Message Batches (50%) is noted as available. Model id and pricing
    ($5/$25 per Mtok) were verified and are correct.
22. **OQ-4 was factually wrong about this repository.** `APIFY_TOKEN` is present and the Shopee
    and Lazada actors are already named in `config.py`; the missing credential is
    `YOUTUBE_API_KEY`, whose path §9 already handles. The relational-block question is a
    capability probe, not an open research question. OQ-5 also described its own error backwards
    (duplicate creator identities *inflate* independence measures, which is the
    anti-conservative direction, and R-CLUSTER now depends on the same counts).
23. **C7 misused Mann–Whitney.** U tests stochastic dominance, so it cannot license a statement
    about medians without a location-shift assumption, and engagement is clustered within
    creators harder than presence is. Replaced with a creator-clustered bootstrap on the
    Hodges–Lehmann shift plus a ≥5-creator floor.
24. **C2's denominator silently excluded what it could not resolve**, so visual share was inflated
    by an amount that moves with resolution quality. The unresolved share is now a mandatory slot
    and the comparison is suppressed above 30%.

### 13.3 Minor — fixed

Deterministic tie-breaking in the diversity walk (equal group strengths previously ordered by
dict insertion, so stored confidence was not reproducible); `direction_balance` divided by zero
when every span is symmetric, and is now NULL; `min_group_strength = 0.25` was unjustified and is
now justified and logged as OQ-13; run 1's empty `descriptor_tokens` changes resolution behaviour
and is now documented; `phonetic_key` is English-orthography double metaphone and its weakness on
code-switched speech is now stated (tolerable only because K5 is a blocking key); §2.12's "can
type alone" column overstated what a single signal does; CSR-4 was cited where CSR-7 governs;
§3.9 and §10 disagreed about the competitors route; §3.9's "three confidences" claim is now stated
precisely rather than flatteringly; XS-6 was promoted from "two detectors need this" to "most of
the numbers in this document need this".

### 13.4 §1 / §34 seeding review

No brand, competitor, occasion, cultural reference, slang term, flavour, product category or
Philippine-specific claim appears in any table, enum, default, threshold, prompt, template or
fixture. Two artefacts were market-specific and both are removed:

- `normalize_surface(..., locale: str = "en-PH")` — a default market living in a function
  signature rather than in run configuration. `locale` is now required.
- `r"\bFilipinos\b"` in `FORBIDDEN_PATTERNS` — a hardcoded demonym. It was a *guardrail*, not a
  seeded finding, so it was not a §34 violation in substance, but it was both a market assumption
  in code and poor protection: a one-word denylist blocks one spelling and misses every other
  over-generalisation. Replaced by an empty-shipped, locale-loaded population-term file plus the
  structural R-SCOPE requirement, which is what actually prevents a market-wide claim.

Everything else held up. The runtime-discovered `substitution_cues` and `descriptor_tokens`
tables, the structural-only shipped lexicons, the exclusion of seeds from the S1/S7 prompts (N4),
and the "do not use outside knowledge" rules in both prompts are the right mechanisms and are a
better §34 implementation than most designs achieve.

### 13.5 Residual risk after this revision

- **Nothing here is calibrated.** OQ-1, OQ-2, OQ-3 and OQ-11 remain open, and OQ-13, OQ-14 and
  OQ-15 were added by this review. The confidence bands in particular are now known to be
  uncalibrated rather than merely unvalidated: the corrected §3.7 example lands at 0.46 with
  eleven signals firing, so `strong` and `very_strong` may be nearly unreachable. This is safe
  (it under-claims) but it is not right, and run 1 must settle it.
- **R-PROVENANCE will shrink the comparison surface, possibly severely.** If most collected items
  come from brand-named queries, the brand-agnostic stratum may fail R-MINN for most subsets and
  §7 will mostly render insufficient-data templates. That is the honest outcome, but it is a
  product consequence the team should see before run 1, and it argues for weighting the query mix
  (§8 strategies B and D) more heavily than a minimum.
- **R-CLUSTER depends on creator identity, which OQ-5 says is broken across platforms.** The
  design-effect correction is therefore too weak by an unknown amount, in the anti-conservative
  direction. Until creator resolution exists, every creator-count statement must say "creator
  accounts".
- **The permutation null in §3.10 is specified but unvalidated**, and its cost assumption (pure
  SQL plus numpy, no LLM calls replayed) depends on the verdict cache from fix 2 behaving as
  specified.
- **`same_corporate_family` remains the live credibility risk** (OQ-6, unchanged). Presenting a
  stablemate as a discovered competitor is the failure a marketing audience will notice first,
  and no amount of statistics in this document addresses it; only the human review queue does.
- **`competitor_pair_enumeration.dirty` has no specified lifecycle** — §1.8 sets it, nothing
  documented clears it, and its primary key includes `research_run_id` so it is unclear which
  run's row a mid-run detachment should mark. Left as an implementation detail to settle, flagged
  here so it is not discovered as a bug.
- **S9's per-run `FLOOR` recalibration is in-sample by construction** (the 20th percentile of the
  pairs being scored), so roughly a fifth of pairs score zero on `embed_sim` whatever the truth
  is, and the floor moves between runs. The new `cause='recalibration'` event prevents that
  movement being read as momentum, but it does not make the component meaningful; OQ-1's
  distribution inspection should decide whether S9 keeps `embed_sim` at all.
