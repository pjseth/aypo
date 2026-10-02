# AYPO — Unified Architecture

> **This file replaces the seven subsystem designs as the implementer's source of truth.**
> `docs/design/01..07` remain as the reasoning record. Where this file and a design doc disagree,
> **this file wins**. Where this file and `docs/ENVIRONMENT.md` disagree, **ENVIRONMENT.md wins**
> (it records measurements; the designs record intentions). `docs/BRIEF.md` §1 and §34 bind
> everything below: no table, column, enum, default, prompt, lexicon, fixture or threshold names a
> consumer, occasion, competitor, theme, slang term or category assumption about the research
> subject. All examples use `Brand A`, `Brand B`, `Theme X`, `Cluster Y`, `Label Z`.

Reading order for an implementer: §1 (decisions) → §2 (tables) → §3 (modules) → §4 (contracts) →
§8 (build order). §5–§7 are the three places the seven docs disagreed most and must be read before
writing any statistics code.

---

## 0. The shape of the system in one page

```
                  research_runs (FSM, advanced by short idempotent ticks)
                           │
   ALLOCATE → PROPOSE → DEDUPE → DISCOVER ─┐
        ▲                                   │  collectors/  (provider-independent)
        │                                   ▼
        │                           research_results  ← the one provenance join
        │                                   │
        │                     ┌─────────────┼─────────────┐
        │                     ▼                           ▼
        │              content_items                commerce_items
        │                     │                           │
        │        analysis/ (one Claude call/item)   analysis/commerce_*
        │                     ▼                           ▼
        │           content_analysis_claims      commerce_item_brand_links
        │                     │                  commerce_product_groups
        │                     ▼                           │
        │        intelligence/brand_resolution  ← ────────┘   (must run BEFORE CAD)
        │                     │
        │                     ▼
        │          analysis/labels/  (open taxonomy, canonicalisation)
        │                     │
        │                     ▼
        │   analysis/cad.py → content_embeddings → clustering → clusters
        │                     │                                    │
        │        ┌────────────┼──────────────┬─────────────────────┤
        │        ▼            ▼              ▼                     ▼
        │  trends.py   theme_commerce.py  competitors/      comparisons/
        │        └────────────┴──────────────┴─────────────────────┘
        │                     │  StatCandidate
        │                     ▼
        │          intelligence/findings/  (packet → generate → validate → referee → score)
        │                     │
        └──── INTERPRET ◄─────┴────► findings / finding_evidence ───► UI, Ask the Data
```

Five invariants hold everywhere and are the reason most of §1 reads the way it does:

- **I-1. One provenance join.** `research_results` is the only authoritative (query × item) record,
  for both social and commerce. Nothing derives strategy, rank or platform from anywhere else.
- **I-2. One embedding space per run.** `research_runs.embedding_model` / `embedding_dim` pin it. A
  mid-run change is `fatal_error`; a between-run change is an `identity_epoch` break.
- **I-3. Nothing that gates a number is produced by a language model.** Every threshold, count,
  denominator, interval and classification is computed in Python or SQL. Models write prose, propose
  labels, adjudicate pairs and answer questions — always inside a mechanical validator.
- **I-4. No single number per brand, per competitor or per theme exists in the schema.** There is no
  column, view or endpoint from which a ranking could be computed (brief §24, §33).
- **I-5. Absence is a result, not a blank.** Every refusal is rendered with its reason code.

---

## 1. Resolved conflicts and canonical decisions

Each decision below is numbered `D-n` and is binding. Where a decision overrides a named design doc,
the doc and section are cited so an implementer reading that doc knows it has been superseded.

### 1.1 Identity, provenance and strategy

**D-1 — One strategy enum, one spelling.**
`create type research_strategy as enum ('brand','category','competitor','theme','hypothesis')`,
owned by migration `0002`. Doc 04's duplicate `discovery_strategy` enum is **deleted**; doc 05's
`'A'|'B'|'C'|'D'|'E'` letters are **deleted** (doc 02 §1.4 was right that they mismatch upstream).
Every reference uses `research_strategy`.
*Conflict resolved:* 01/04/07 enum vs 05 letters vs 02's objection.

**D-2 — `platform` is `text`, not an enum, validated against `collection_sources`.**
Canonical values: `tiktok`, `youtube`, `instagram`, `facebook`, `web`, `shopee_ph`, `lazada_ph`.
Doc 05's `commerce_platform` enum is **deleted** (it mixed a platform with a collection mode:
`web_snippet` is `platform='web'` + `collection_mode='web_search_snippet'`). Doc 01 §3.2's reasoning
is adopted globally: an enum must not outlive a provider being removed.
*Consequence:* doc 02 §1.5's `distinct_platforms` count and doc 04's `cluster_members.platform`
become one vocabulary, so a marketplace genuinely counts as a platform.

**D-3 — Strategy is a property of the discovering query, never of the item.**
`sampling_strategy` is **removed from `commerce_items`** and never added to `content_items`. An
item's strategy set is `research_results → research_queries.strategy`. The single-valued
denormalisations that survive are:
`research_results.first_seen_query_id` (immutable, written only on insert) and
`cluster_members.discovery_strategy` (= the strategy of `first_seen_query_id`), both for index-only
scans. Doc 02 §1.5's multi-valued treatment is correct and is the semantics everywhere else.

**D-4 — Commerce items are run-independent, like content items.**
Doc 05's `commerce_items.research_run_id NOT NULL` + `unique (research_run_id, platform,
platform_item_id)` is **replaced** by `unique (platform, platform_item_id)`. Per-run and per-query
values (`discovery_query_id`, `result_rank`, `theme_id_origin`, `sampling_strategy`) move to
`research_results`; time-varying values stay in `commerce_item_snapshots`.
*Why this had to change:* with run-scoped ids, the same listing re-collected next run is a new row,
so doc 04's cross-run cluster identity sees total membership turnover and `corpus_churn` fires on
every commerce theme. Making both channels run-independent is the only way doc 04 §4.6 means
anything for commerce.

**D-5 — `creators` is a real table, owned by collection.**
Doc 07 §12.3 correctly observed that no design defines it while three depend on it. It is defined in
migration `0001` and populated by `collectors/creators.py::upsert_creator()`. Doc 07's interim
`creator_ref = platform || ':' || casefold(handle)` derivation is retained **only** as the fallback
when the platform exposes no stable user id, and it is recorded as `creators.id_basis='handle'` so
`deff` estimates can be discounted for it.

**D-6 — One near-duplicate owner.**
`collectors/dedupe.py` assigns `dedupe_group_id` to both `content_items` and `commerce_items`.
Threshold: cosine `>= max(0.97, 99.9th percentile of the run's sampled pairwise cosine)` **and**
publication dates within 30 days. Doc 04 §1.8's 0.98 and doc 07 §6.2a's interim 0.97 collapse are
**merged into this one rule**; doc 07's own collapse is deleted and it consumes `dedupe_group_id`.
Doc 01's `tau_novelty = 0.86` is a **different question** (is this item conceptually new to the
corpus?) and is kept, renamed `tau_novelty` to stop it being read as a dedup threshold.

**D-7 — `content_metadata` is not a table.** Brief §29 lists it; no design defines it and doc 03
puts platform metadata on `content_items`. Platform fields live on `content_items` with a
`platform_payload jsonb` for anything unmapped. Noted as a deliberate deviation from §29.

### 1.2 Clustering, themes and identity

**D-8 — `cluster_runs` is per (research run × space × iteration).**
Add `iteration_no int not null default 0`; widen `cluster_runs_one_per_space` to
`unique (research_run_id, corpus, space, iteration_no)`. Doc 01 §11.2 asked for exactly this and it
is adopted.
*Consequence:* doc 01's `research_cluster_snapshots` and `research_cluster_centroids` are
**deleted**. §5.3 of doc 01 (cluster-structure stability) reads `cluster_members` and
`cluster_revisions` directly, and consumes doc 04's calibrated `match_cos` / `match_jaccard` /
`match_decision` rather than re-deriving them. Doc 01's own `cos >= 0.80 ∧ jaccard >= 0.40`
constants are **deleted** (D-20).

**D-9 — One cluster identity mechanism.** Doc 04 §4 owns it: 1536-d centroids, score
`0.6·cos + 0.4·jac` over the corpus intersection, continuation at `>= 0.72` **and**
(`cos >= 0.85` or `jac >= 0.30`), ambiguous `[0.60, 0.72)`, birth `< 0.60`, new ids for all split and
merge children, budding exception at 85/15. Calibrated by doc 04 §4.8 before anyone relies on a
borderline verdict.

**D-10 — `min_cluster_size` is doc 04's formula, and doc 01's gate follows it.**
`min_cluster_size = max(8, ceil(0.005·n))`. Doc 01's "skip CLUSTER below 60 items" becomes
`min_corpus_for_clustering = max(60, ceil(7.5 × min_cluster_size(n)))`, evaluated at current corpus
size. Doc 01's hard-coded 60 assumed `min_cluster_size` was always 8.

**D-11 — `cluster_links` loses the social↔commerce kind.**
Doc 04 §7.9's `centroid_similarity` link at `cos >= 0.55` (an admittedly guessed threshold on two
incomparable CAD templates) is **deleted**. The authoritative social↔commerce link is doc 05's
`commerce_theme_matches`, which is item-level, evidence-bearing, component-decomposed and is what
brief §17 and §31.6 render. `cluster_links` keeps only `cross_facet` and `member_cooccurrence`
(social↔social, with Fisher p/q). Doc 04 §12.10 is closed by deletion.

**D-12 — "Theme" is a read-model, not a table.** A *theme* is a labelled, trend-eligible, non-rollup
cluster. `ThemeRef` (doc 05 §4.1) is produced by `aypo/intelligence/themes.py`, which doc 04's
package owns. `clusters` gains `discriminative_terms jsonb` and `representative_snippets text[]`,
written at labelling, so `ThemeRef` is a projection and not a recomputation.

### 1.3 Labels and the extractor prompt

**D-13 — 15 facets, a total 1:1 mapping, and every facet is emitted.**
Doc 02's 14 facets plus `setting`. Doc 03 adds the three arrays doc 02 had to mark
`accepts_emissions=false` or derive, and `format_features` maps onto `content_format`:

| doc 03 `VideoAnalysis` field | facet |
|---|---|
| `occasions` | `occasion` |
| `behaviors` | `behaviour` |
| `social_context` | `social_context` |
| `activities` | `activity` |
| `cultural_signals` | `cultural_signal` |
| `slang` | `slang` |
| `humor_signals` | `humour` |
| `narrative` *(new)* | `narrative` |
| `creator_archetype` *(singleton)* | `creator_archetype` |
| `content_format` *(singleton)* + `format_features` | `content_format` |
| `product_pairings` | `product_pairing` |
| `audience_signals` *(new)* | `audience_signal` |
| `commercial_signals` *(new)* | `commercial_signal` |
| `setting_observations` | `setting` *(new facet)* |
| `unexpected_observations` | `unexpected` |

Facets remain a lookup table (`label_facets`), never a Postgres enum.

**D-14 — One label schema, owned by doc 03, satisfying doc 02's contract.**
Doc 02's `LabelProposal` / `LabelProposalSet` are **deleted**. `DiscoveredLabel` (doc 03 §5.3)
becomes:

```python
class DiscoveredLabel(Claim):
    label: ShortLabel                                   # 1-5 words, content's own language
    gloss_en: Annotated[str, Field(min_length=1, max_length=240)]   # REQUIRED, English
    label_kind: ShortLabel | None = None
```

`elaboration` is renamed `gloss_en` and made **required**; `facet` is derived mechanically from the
array name; `evidence_quote` / `evidence_timecode_s` come from `Claim.anchors[0]`. Doc 02's
`language_observed` free-text field is **deleted** — per-label language was only ever a weak
cross-check and `VideoAnalysis.languages[]` already carries anchored language observations.
*This closes doc 02 §9.9, which it called its highest-priority integration item.*
Length bounds are enforced **per proposal at ingest**, never in the wire schema (doc 02 §3.4c's
reasoning is correct and generalises: a single bad proposal must not discard its siblings).

**D-15 — The controlled-vocabulary suggestion loop is adopted, with doc 03's constraints binding it.**
Doc 03 §0.3/§4.1 forbids content vocabulary in the prompt; doc 02 §3 puts discovered vocabulary
there. Doc 02 wins on the mechanism, doc 03 wins on the constraints:
1. Only labels **attested in this run's own collected data** may appear. Never an authored string.
2. `p_holdout = 0.15`, assigned `hash(content_item_id) % 100 < 15`. The holdout arm is a **release
   gate**: the §3.5 bias report must be produced before any taxonomy is shown to a customer.
3. Selection reads `support_suggested`, never `support_count` (doc 02 §3.3) — otherwise the control
   arm builds the vocabulary it is the control for.
4. The block never appears for `vocab_epoch = 0`.
5. The *policy* (caps, slot mix, `p_holdout`, seed) is hashed into
   `analysis_configs.config_fingerprint`; the block's *content* is not, so the prompt cache survives
   epoch rotation. The content is recorded per item on `label_observations.vocab_snapshot_id`.

**D-16 — Two cache breakpoints, and the suggestion block lives in `system`.**
Doc 03 §3.4's "one breakpoint at the end of the system block" is **superseded**. The system array is
`[stable_instructions (cache_control), suggestion_block (cache_control)]`. With one breakpoint the
epoch rotation rewrites the whole prefix every 50 analyses, which costs ~$0.75 instead of ~$0.11
*and* destroys doc 03's own 2,400-token cache benefit. TTL: `5m` interactive, `1h` batch (doc 03
§3.4's corrected reasoning). Verification is mandatory: alert when run-level mean
`usage.cache_read_input_tokens < 1500`.

**D-17 — Doc 02 owns all label canonicalisation.** Doc 01 §5.2(b)'s ad-hoc `cos >= 0.92` label merge
is **deleted**. `label_novelty` and the Good–Turing label-coverage statistic read
`observation_label_map` joined to `labels` at the run's active `canon_version`. The merge thresholds
are doc 02 §2.7's posture groups, nothing else.

**D-18 — Review labels go through the same tables.** Doc 05's `commerce_review_labels` is
**deleted**; review labels are `label_observations` rows with `source_kind='commerce_review'`. Doc 05
§7.3 and doc 02 both asked for this; two tables would have guaranteed two vocabularies.
`label_observations.analysis_id` (which doc 02 §10 admits referenced nothing) is replaced by two
nullable FKs, `content_analysis_id` and `commerce_review_id`, with a CHECK that exactly one is set.

**D-19 — `PairVerdict` keys on an opaque string id.** Doc 02 §2.8's prose says code-generated 6-hex
ids validated as an exact set; its code block said `pair_index: int`. The string id wins.

### 1.4 Thresholds that were defined twice

**D-20 — Cross-iteration cluster stability uses doc 04's matcher.** See D-8. Doc 01's 0.80/0.40 are
deleted; `S_match` is `Σ min(size_core_prev, size_core_curr)` over `match_decision='continuation'`
rows, divided by `|I_core|`. AMI and noise churn are still computed by doc 01 from
`cluster_members.membership_role` across the two revisions, with `average_method='max'`.

**D-21 — The competitor gate is stated on a band, not a decimal.**
Doc 01's `confidence >= 0.50` sits inside doc 06's `moderate` band and would silently move if doc 06
recalibrates. Replaced by:
```
competitor_gate := confidence_band IN ('moderate','strong','very_strong')  -- i.e. confidence >= 0.35
                   AND evidence_count >= 3
                   AND NOT same_corporate_family
                   AND decay_state IN ('active','stale')
```
recorded in `research_runs.config_resolved.competitor_gate` and read from there, never hard-coded.
The same predicate defines `K_i` for doc 01 §5.4's competitor-set stability. This closes doc 01
§13.7's stated residual coupling.

**D-22 — Sponsorship is a bucket; the float is a fixed mapping.**
Doc 03 emits `SponsorshipAssessment.bucket`; doc 06 §3.5 and doc 07 §V11 consume a
`sponsored_likelihood` float that doc 03 never produces. `aypo/analysis/sponsorship.py`:
```
likely_paid -> 0.85   possibly_paid -> 0.50   undetermined -> 0.25   likely_organic -> 0.10
```
Doc 06's `>= 0.60` therefore means exactly `bucket = 'likely_paid'`, which is what its own prose
said it intended. Doc 07's 0.20/0.50 share thresholds are computed on the mapped values.

**D-23 — The CAD confidence gate reads calibrated status, not model confidence.**
Doc 04 §1.3.5 drops CAD values with "stated confidence < 0.5" — but that is `model_confidence`,
which doc 03 §6.1 says is stored and never used for gating. Replaced: a value enters a CAD iff its
`content_analysis_claims.claim_status = 'published'`. `cad_dropped_low_confidence` counts the
`unverified` and `rejected` claims that were excluded. The `0.5` constant is deleted; doc 03's τ is
the only publication threshold in the system.
*Consequence:* doc 04's `conf_bucket` intra-field sort key becomes
`(-round(calibrated_confidence/0.02), value)`.

**D-24 — One per-item cost model, and it is sonnet-5.**
`docs/ENVIRONMENT.md` records the operator's choice: `aypo_analysis_model = claude-sonnet-5` for the
~1,500-item bulk pass (measured $0.0295/video) and `claude-opus-5` for judgement-heavy stages.
Doc 03's §7 cost model was built on opus-5 ($0.0894/item, ≈$113 batched) and is **superseded**:
per-item $0.0295 measured, ≈$44 interactive / ≈$25 batched for 1,500 videos. Doc 03's `effort`,
`max_tokens=32_000` and frame policy are unchanged (they are ceilings and pixel budgets, not model
choices).
*Consequence an implementer must carry:* at sonnet-5 the output-token share of per-item cost falls,
so doc 03 §7.5's lever ranking (output tokens dominate, frames third) no longer holds and must be
recomputed on the pilot wave before anyone cuts frames.

**D-25 — Budgets that sum under the cap.**

| Line | Model | Budget (USD/run) | Source |
|---|---|---|---|
| Per-item multimodal analysis | sonnet-5 | 50 | D-24, batched + repairs |
| Commerce record analysis + title extraction | sonnet-5 | 12 | doc 05 §9 |
| Open-taxonomy adjudication + definitions | opus-5 | 14 | doc 02 §7.3 run-1 figure |
| Cluster labelling + verification + probes | opus-5 | 15 | doc 04 §9.3 |
| Competitor adjudication + span extraction | opus-5 | 8 | doc 06 §8 |
| Findings generation + referee | opus-5 | 45 | doc 07 §12.5, raised from 30 |
| Research orchestration (PROPOSE/INTERPRET/SYNTHESIS) | opus-5 | 7 | doc 01 §7 |
| Ask the Data (per session, not per run) | opus-5 | — | capped at 8 tool turns |
| **Total** | | **151** | |

`aypo_max_llm_usd = 180` (1.19× headroom). Doc 07's `FindingBudget.max_usd` is raised 30 → 45 so it
no longer contradicts its own §8 "150 candidates ≈ $45". Each budget is enforced through
`DurableLedger` by `Spend.label` prefix (`analysis.*`, `taxonomy.*`, `cluster.*`, `competitor.*`,
`findings.*`, `research.*`), and exhausting one line degrades that subsystem only — it never fails
the run.

**D-26 — Wall clock and concurrency.**
`aypo_max_wallclock_minutes = 300` and `aypo_worker_concurrency = 16`. Doc 01 §7.2 shows 180 min at
concurrency 4 buys ~617 items, 41% of target. The pacer still sizes down honestly when throughput
disappoints; this makes the nominal configuration capable of the nominal corpus. See risk R-2.

### 1.5 Claim gating, metrics and the API surface

**D-27 — One claim-gate package, three layers.**
`aypo/intelligence/claims/` replaces doc 04's `claim_guard.py`, doc 05's `noncausal.py` +
`commerce_caveats.py`, and doc 06's `phrasing.py` as separate modules:
- `claims/guard.py` — mints `Claim | Refusal` for anything derived from `theme_timeseries` /
  `theme_trend_tests`. The narrative layer receives `Claim` objects and never a series.
- `claims/templates.py` — the single closed registry of permitted claim templates and their
  mandatory caveats (doc 05's `ALLOWED_CLAIM_TEMPLATES` + caveat registry + doc 06's phrasing
  registry, merged). Caveat text is snapshot-tested, never run through the blocklist (doc 05's
  reasoning: caveats necessarily contain the words the blocklist bans).
- `claims/validators.py` — doc 07's V1–V18 plus `assert_non_causal` and
  `assert_no_composite_score`, run on **rendered prose** for every published statement regardless of
  which subsystem produced it.
- `claims/lexicons.py` — structural lexicons only, merged with the market pack (D-29).

**D-28 — One `MetricResult` type, three producers.**
Doc 05 §5.0's `MetricResult[T]` (with non-optional `caveat`, `n`, `n_label`, `n_effective`,
`excluded`, `family_size`, `suppressed`) is the shared return type for:
`comparisons/social.py` (brand-by-theme, doc 04 §5.10/§11.13),
`comparisons/commerce.py` (doc 05 §5), `comparisons/competitive.py` (doc 06 §7).
No subsystem invents its own metric envelope.

**D-29 — One market configuration pack.** Four separate per-market mechanisms (doc 05's
`market_population_nouns`, doc 06's `population_terms.<locale>.yaml` and
`confusions_phonetic.<locale>.yaml`, doc 02's `norm_pipeline.determiners`, doc 07's
`MarketLocalePack`) collapse into `config/market/<market_code>.yaml`, keyed by
`research_runs.market_code` (ISO-3166 alpha-2, the one spelling — doc 07's `market` is renamed to
it). Contents: `language_codes`, `timezone`, `currency_code`, `population_nouns`,
`causal_connectives`, `hedges`, `demographic_terms`, `determiners`, `phonetic_confusions`.
It **ships empty for every list except `timezone` and `currency_code`**, and a build-time test
rejects any entry that appears as a content token in the run's own corpus above a low frequency
floor. No category, brand, occasion, theme or behaviour term may ever enter it.

**D-30 — One period grid.** `aypo/intelligence/periods.py::bucket(ts, kind)` applies
`settings.aypo_market_timezone` (from the market pack, no module default) before truncating.
Monthly by default; auto-switch to quarterly when median `N(p) < 60`, which also extends the window
to 18 months and period-normalises the effect floor (doc 04 §5.5). Doc 02 §6.4's raw UTC
`date_trunc('week', …)` is **deleted**; `label_support_daily.pub_date` is a market-timezone date.
ISO-week bucketing (doc 07 §6.3a T2) is **deleted** in favour of this grid — see D-32.

**D-31 — Single-tenant MVP.** `research_runs.created_by uuid`; RLS gates on
`created_by = auth.uid()`. Doc 07's `research_runs.org_id` / `auth_org_id()` and
`ask_sessions.org_id NOT NULL` are **deleted** (doc 07 §12.3 flagged its own inconsistency here).
Views keep `security_invoker = true`.

**D-32 — One trend classifier.** Three existed. The canonical one is doc 04's:
- **Series:** within-period composition share on the reference stratum, recomputed from the current
  member set every run. Never a volume series.
- **Periods:** D-30's grid, trailing incomplete period dropped plus 7 days' indexing lag.
- **Statistic:** Cochran–Armitage trend in proportions, **summed over platform strata**, with a
  creator-block permutation null using exact-size exchangeability strata, `R = 10,000` shared across
  all themes, regenerated from a stored seed in 1,000-permutation chunks.
- **Effect:** Firth-penalised logistic slope; `OR_per_period = exp(β1)`; floor `1.25` monthly /
  `1.95` quarterly (and `0.80` / `0.51` down), with CI from creator-block percentile bootstrap.
- **Correction:** BH `q <= 0.10` within `(space, run, test_kind)` for the Trends section;
  Westfall–Young **step-down** minP at FWER 0.05 for Overview headline cards.
- **Classes:** `established | growing | emerging | weak_signal | stable | declining |
  insufficient_data`, by doc 04 §6.3's ordered gates.

Deleted: doc 02 §6.4's independent binomial classifier (`r >= 1.50 / <= 0.67`) and doc 07's
Mann-Kendall. **Adopted from them:** doc 02's overdispersion correction is already present as
`deff_hat`; doc 07's three genuinely additive guards are folded into doc 04 §5.7 as hard conditions:
- **T4 query-vintage:** `|kendall_tau(bucket_index, share of bucket found by queries issued in the
  run's later half)| < 0.50`, and every retained bucket contains ≥2 distinct query vintages.
  Failure ⇒ `refusal_reason='retrieval_recency_artefact'`.
- **T5 no cumulative counters:** no stat with `is_cumulative=True` may appear in a trend.
- **T7 leave-one-unit-out:** the direction must survive dropping the single highest-weight
  independence unit.

Doc 02's label trajectory is **not** a second classifier: `trends.py` is called with a
label-presence indicator instead of a cluster-membership indicator, over the same periods, the same
reference stratum, the same `N_eff(p) >= 40` qualifying rule and the same effect floor. One
implementation, two indicators.

**D-33 — Commerce growth and decline remain refused.** Doc 04 §6.7 is binding: commerce series are
built from review dates with `unit='review'` and listing-level permutation blocks; `emerging` and
`stable` are permitted, growth and decline are refused with
`commerce_temporal_unidentified` until a source with a trustworthy date denominator exists.

**D-34 — The Anthropic call shape is ENVIRONMENT.md's.**
```python
resp = await client.messages.parse(
    model=settings.aypo_llm_model,          # or aypo_analysis_model
    max_tokens=...,
    thinking={"type": "adaptive"},          # never disabled
    output_config={"effort": "low|medium|high|xhigh|max"},
    output_format=PydanticModel,
    system=[...],                           # cache_control breakpoints per D-16
    messages=[...],
)
obj = resp.parsed_output
```
Doc 03 §3.2's `betas=["server-side-fallback-2026-07-01"], fallbacks="default"` is **deleted**:
ENVIRONMENT.md records that `parse` is on the non-beta namespace and takes neither argument, and
that refusals are handled client-side in `aypo/llm/client.py` (one retry on
`aypo_llm_fallback_model`). Doc 03 §6b.1's ladder loses its step 1 for both paths; the
frames-dropped text-only pass becomes step 1 and `refused_terminal` step 2. Doc 06 §1.5's note that
`output_format=` is deprecated is **wrong and deleted**. Every call goes through
`LLMClient.structured()`.

**D-35 — FFmpeg `scdet` scores are 0–100.** ENVIRONMENT.md measured it. Doc 03 §1.3 step 5's
`SCENE_FLAT_ABS = 0.08` is mis-scaled by ~3 orders of magnitude. Canonical:
`flat_threshold = max(SCENE_ABSOLUTE_FLOOR, SCENE_FLAT_REL × median(scores))` with
`SCENE_ABSOLUTE_FLOOR = 1.0` and `SCENE_FLAT_REL = 3.0`, on the 0–100 scale. Parse
`lavfi.scd.score`; `lavfi.scene_score` is accepted defensively but is not emitted by this build.

**D-36 — Media retrieval is `yt-dlp`, not Apify.** ENVIRONMENT.md measured that `mediaUrls` comes
back empty, `videoMeta.coverUrl` 403s without the token, and `shouldDownloadVideos` is a paid
add-on. Apify does discovery and metadata; `analysis/retrieval.py` fetches bytes with `yt-dlp`
against `webVideoUrl`. This is the brief §7 discovery/retrieval split and it costs nothing.
Platform-stated `isSponsored` / `isAd` are **observed facts** and take precedence over doc 03's
inferred `SponsorshipAssessment.bucket`; when present they force `likely_paid` /
`no_platform_label` respectively and are recorded as `SponsorCueKind.platform_paid_partnership_label`.

**D-37 — Music transcripts are sound context, not speech.** ENVIRONMENT.md's TikTok validation
found Whisper transcribing song lyrics. `analysis/transcribe.py` sets
`transcript_kind ∈ {speech, music_lyrics, mixed, none}` from `musicMeta.musicOriginal = false` plus
transcript-to-track agreement. `music_lyrics` transcripts may not anchor a `StatedUse`, a
`MentionedBrand` with `speaker_role != 'unclear'`, or any sentiment claim, and are never surfaced
verbatim in a finding or the UI (third-party copyrighted text).

### 1.6 Pipeline ordering

**D-38 — Brand resolution runs before CAD rendering; competitor signals run after clustering.**
Neither doc states this and getting it wrong is silent. Doc 04 §1.2 needs exact brand surface forms
to redact the clustering views; doc 03 may not know brands (CSR-6 / §0.3); doc 06 owns the alias
registry; doc 06's S3 `shared_cluster` detector needs clusters. The order is therefore:

```
analysis (03) → resolve_brands (06 phase 1) → taxonomy ingest (02)
   → cad + embed + cluster + label (04) → trends (04) + theme_commerce (05)
   → enumerate_pairs → detect_signals → aggregate → type → decay (06 phase 2)
   → comparisons → findings (07)
```

Doc 04's `cad.py` reads redaction surfaces from
`intelligence/brand_resolution/registry.py::surface_forms_for_run(run_id)`, not from doc 03. Doc 04
§11.3's request to the analysis subsystem is **withdrawn**.

**D-39 — `representative_quote` is a doc 03 field.** Doc 04 §11.2 asks for a ≤30-word verbatim span
preferring brand-free text; doc 03 has no such field and cannot be told to prefer brand-free text
without being told what a brand is. Resolution: `VideoAnalysis.representative_quote: Anchor | None`
— verbatim, with modality and offsets, verified by Check B like any other quote. The
brand-preference instruction is **not** given; doc 04's redaction plus its "omit rather than render
a row of `<brand>` placeholders" rule carries it alone.

**D-40 — Relevance scoring is owned by collection.** `collectors/relevance.py` writes
`research_results.relevance_score` and `relevance_source ∈ {heuristic, llm, provider}` at admission.
Doc 01 §11.14's audit requirement stands: where `relevance_source='llm'`, the run report carries the
model-vs-heuristic agreement rate on a held-out sample of ≥100 items, because an LLM opinion feeding
a stop predicate must be distinguishable from a measurement.

**D-41 — Iteration columns exist wherever a per-iteration number is read.**
Added: `collection_jobs.iteration_no`, `finding_evidence.created_iteration`,
`findings.created_iteration`, `competitor_relationships.first_detected_iteration` /
`last_detected_iteration`, `competitor_signal_evidence.iteration_no`, `cluster_runs.iteration_no`.
This makes doc 01 §5.5's marginal-evidence-yield computable from **real** links; the
`candidate_evidence` substitute is retained as a documented fallback with
`research_saturation.metric_basis.yield` recording which basis was used.

**D-42 — Job queue amendments (doc 01 §11.1, binding).**
1. `enqueue()` returns the **existing** job id on a `dedupe_key` collision, not `None`
   (`ON CONFLICT … DO UPDATE SET kind = excluded.kind RETURNING id`). Today's `None` makes
   DISCOVER's completion predicate permanently unsatisfiable after a crash.
2. `collection_jobs.iteration_no int` with an index on `(research_run_id, iteration_no, state)`.
3. First-class `cancel(run_id, iteration_no) -> int` and
   `counts_by_state(run_id, iteration_no, kind) -> dict[str,int]`.
4. Terminal jobs stay as rows so the orchestrator can `COUNT` them.

**D-43 — Migration numbering.** One file per track, FK-ordered, nobody edits another track's file;
a cross-track column addition goes in a later `00NN_<track>_amend.sql`.

```
0001_core.sql           collection_sources, creators, content_items, collection_jobs,
                        collection_coverage
0002_research.sql       research_strategy enum, research_runs, research_iterations,
                        research_queries, research_results, research_log,
                        research_budget_ledger, research_saturation,
                        research_run_transitions, hypotheses, hypothesis_evidence
0003_brands.sql         brands, brand_aliases, brand_surface_observations,
                        brand_alias_detachments, brand_review_items, brand_seeds,
                        substitution_cues, descriptor_tokens
0004_analysis.sql       analysis_configs, content_frames, content_transcripts,
                        content_analysis, content_analysis_runs, content_analysis_claims,
                        analysis_attempts, analysis_calls, calibration_labels,
                        calibration_models
0005_taxonomy.sql       label_facets, lang_buckets, canonicalisation_config,
                        brand_name_snapshot, label_observations,
                        label_observation_rejects, observation_label_map, labels,
                        label_merges, label_pair_judgements, label_pair_gold,
                        label_links, concept_groups, concept_group_members,
                        taxonomy_entries, label_decisions, label_vocab_snapshots,
                        label_support_daily
0006_commerce.sql       commerce_sellers, commerce_items, commerce_item_snapshots,
                        commerce_item_brand_links, commerce_bundle_components,
                        commerce_product_groups, commerce_group_links,
                        commerce_group_merge_evidence, commerce_price_reference,
                        commerce_reviews, commerce_quarantine, products
0007_clustering.sql     content_embeddings, cluster_runs, clusters, cluster_revisions,
                        cluster_lineage, cluster_members, theme_timeseries,
                        theme_trend_tests, cluster_links
0008_theme_commerce.sql commerce_theme_queries, commerce_theme_matches,
                        commerce_theme_absences, commerce_cooccurrence_stats
0009_competitors.sql    competitor_relationships, competitor_signal_evidence,
                        competitor_signal_scores, competitor_relationship_events,
                        competitor_pair_enumeration
0010_findings.sql       evidence_packets, findings, finding_evidence, finding_statistics,
                        finding_validations, finding_refutations, finding_links,
                        finding_quarantine, ask_sessions, ask_messages, ask_tool_calls
```

**D-44 — DDL defects found by execution are binding.** Doc 04 §13.2's executed findings carry over
verbatim: `space_key` is a plain column plus a CHECK, never `generated always as`
(enum→text casts are not immutable and the whole migration cascade-failed on it);
`content_embeddings` hash index is **not** unique; `cluster_members` needs the partial unique index
`(run_id, item_kind, item_id) where membership_role in ('core','noise')`; `cluster_runs` FKs
`research_runs` with `on delete restrict`. Doc 05's migration ordering (enums → tables without
forward refs → `commerce_product_groups` → the deferred `alter table … add constraint`) is binding.
Doc 02's append-only enforcement is a raising **trigger**, never a `RULE`.

---

## 2. Unified table inventory

92 tables. Every table appears exactly once with a single owner and a single agreed column set.
"Owner" is the module that writes it; everything else reads.

### 2.1 Reconciliation against brief §29

| Brief §29 table | Status |
|---|---|
| `brands` | ✅ `0003`, owner `intelligence/brand_resolution` |
| `products` | ✅ `0006`, **narrowed**: commerce-derived only in the MVP (see note) |
| `creators` | ✅ `0001`, owner `collectors/creators.py` (no design owned it; D-5) |
| `content_items` | ✅ `0001` |
| `content_metadata` | ❌ **dropped** (D-7) — fields live on `content_items` + `platform_payload jsonb` |
| `content_analysis` | ✅ `0004`, plus `content_analysis_claims` (the normalised claim table §27 drill-down needs) |
| `content_embeddings` | ✅ `0007`, **extended** to carry commerce vectors via `item_kind` (D-C2) |
| `commerce_items` | ✅ `0006`, run-independent (D-4) |
| `commerce_reviews` | ✅ `0006` |
| `clusters` | ✅ `0007` |
| `cluster_members` | ✅ `0007` |
| `research_runs` | ✅ `0002` |
| `research_queries` | ✅ `0002` |
| `research_results` | ✅ `0002` — the single provenance join for both channels (I-1) |
| `hypotheses` | ✅ `0002` (no design owned the DDL; D-C5) |
| `findings` | ✅ `0010` |
| `finding_evidence` | ✅ `0010` |
| `competitor_relationships` | ✅ `0009` |
| `collection_jobs` | ✅ `0001` |
| `collection_sources` | ✅ `0001` (no design owned it) |

`products` is deliberately thin: `(id, brand_id, canonical_label, product_slug, first_seen_run_id,
created_at)`, populated only from `commerce_product_groups`. Social `visible_products.description`
strings are verbatim and unnormalised by design (doc 03 §5.2) and are better served by the label
system than by a product catalogue; building a social product catalogue would require exactly the
closed vocabulary brief §1 forbids.

### 2.2 `0001_core` — 5 tables

| Table | Owner | Purpose / key columns |
|---|---|---|
| `collection_sources` | `collectors/capabilities.py` | static registry: `platform`, `provider`, `actor_id`, `supports_date_range`, `supports_sort`, `max_page_depth`, `supports_vocabulary_expansion`, `required_env`, `enabled`. The authoritative `platform` vocabulary (D-2) and the backing store for `Settings.capabilities()`. |
| `creators` | `collectors/creators.py` | `id`, `platform`, `platform_user_id`, `handle`, `handle_norm`, `display_name`, `follower_count`, `id_basis ∈ {platform_id, handle}`, `first_seen_at`. `unique (platform, coalesce(platform_user_id, handle_norm))`. The permutation block and independence unit for all social evidence. |
| `content_items` | `collectors/*` | normalised social item (contract §4.1). `unique (platform, platform_item_id)`; `dedupe_group_id`, `first_seen_query_id` (immutable), `creator_id`, `published_at`, `published_at_precision`, `platform_payload jsonb`. Run-independent. |
| `collection_jobs` | `jobs/queue.py` | the Postgres queue. `+ iteration_no`, `+ research_run_id` index, lease/visibility timeout, `dedupe_key` unique (D-42). |
| `collection_coverage` | `collectors/*` | per `(research_run_id, platform)`: `available`, `unavailable_reason`, `queries_run`, `items_discovered`, `items_admitted`, `items_quarantined`, `planned_units`, `achieved_units`, `shortfall_cause`, `collection_started_at/ended_at`. **Generalises doc 05's `commerce_coverage`** to both channels and gives doc 01's `research_runs.coverage_gaps` jsonb a queryable form. |

### 2.3 `0002_research` — 11 tables

Doc 01 §8 is adopted essentially unchanged. Changes: `research_cluster_snapshots` and
`research_cluster_centroids` deleted (D-8); `hypotheses` and `hypothesis_evidence` added here.

| Table | Notes vs doc 01 |
|---|---|
| `research_runs` | `market_code` replaces doc 07's `market` (D-29). `created_by` is the tenancy key (D-31). `embedding_model`/`embedding_dim` pin I-2. `reference_query_set_version text not null` added (D-45, doc 04 §11.6b). `temporal_coverage text` added (doc 01 §3.7d) because the trend subsystem gates on it. |
| `research_iterations` | unchanged |
| `research_queries` | `strategy research_strategy`. `+ names_known_brand boolean` (doc 06 XS-6), `+ is_reference_query boolean` (part of the frozen A/B set, D-45), `+ query_vintage_half smallint` (0 = first half of the run, 1 = second; doc 07 T4 needs it). |
| `research_results` | **the single provenance join, for content and commerce.** `+ commerce_item_id` already present; `+ iteration_no` already present. Carries `raw_rank`, `sort_order`, `page_depth`, `published_at`, `published_at_precision`, `relevance_score`, `relevance_source`, `dedup_state`, `first_seen_query_id`, `cu_charged`, `admitted`, `reject_reason`. `cu_charged` is **1.0 in its own dimension**, never 0.15. |
| `research_log` | unchanged; the `parent_event_id` tree is what brief §32 renders |
| `research_budget_ledger` | `+ subsystem text not null` (the D-25 budget line) |
| `research_saturation` | unchanged, minus the deleted snapshot dependency |
| `research_run_transitions` | unchanged |
| `hypotheses` | **newly owned here** (doc 01 §11.7's shape): `id, run_id, created_iteration, resolved_iteration, status ∈ {open,testing,supported,refuted,insufficient_evidence,abandoned}, admissible bool, statement, reason, confirming_observables jsonb, refuting_observables jsonb, follow_up_plan jsonb`. `follow_up_plan` items are **structured query specs** `{platform, strategy, query_text, expected_observable, disconfirming}` so the loop turns them into queries without a second model call. |
| `hypothesis_evidence` | **new**: `(hypothesis_id, content_item_id | commerce_item_id, stance ∈ {confirming,refuting}, created_iteration, note)`. Doc 01 §5.5 references it; no doc defined it. |
| *(enum)* `research_strategy`, `research_run_state`, `research_iteration_phase`, `research_query_kind`, `research_query_status`, `research_dedup_state`, `research_item_state`, `research_budget_kind`, `research_budget_status`, `research_stop_reason`, `research_depth`, `research_log_event` | doc 01 §8 |

**D-45 — The reference query set is a frozen, versioned artefact.** The strategy-`brand` and
strategy-`category` queries are stored with `is_reference_query = true` and re-executed verbatim
every run. Adding or editing one bumps `research_runs.reference_query_set_version`, which is treated
exactly like an `identity_epoch` break for temporal purposes: `replication_status` resets to
`unreplicated` and cross-run growth comparisons are suppressed for one run. Without this, removing
theme-conditioning fixes only half the bias (doc 04 §5.2) and every between-run comparison compares
two different instruments.

### 2.4 `0003_brands` — 8 tables

Doc 06 §5.1 adopted unchanged, with D-21's gate columns and D-41's iteration columns.
`brands`, `brand_aliases`, `brand_surface_observations`, `brand_alias_detachments`,
`brand_review_items`, `brand_seeds`, `substitution_cues` (ships empty), `descriptor_tokens`
(ships empty).
Plus the view `v_item_brands` — doc 06 XS-12: other subsystems read resolved brands **through this
view only**, never by stamping a `brand_id` onto a content row. That is what makes resolution
reversible.
Doc 06's duplicate declaration of `brand_level`/`brand_status`/`brand_origin` (its §1.5 and §5.1)
collapses to one declaration in `0003`.

### 2.5 `0004_analysis` — 10 tables

Doc 03 §10 adopted. Changes: D-14 (gloss required, three new arrays, `setting` facet), D-23
(`claim_status` is the CAD gate), D-24 (`model` default `claude-sonnet-5`), D-34 (no `betas`),
D-35 (scene scale), D-37 (`transcript_kind`), D-39 (`representative_quote`).

| Table | Notes |
|---|---|
| `analysis_configs` | `+ vocab_policy jsonb` (D-15 item 5: the policy is fingerprinted, the block's content is not). `frame_policy` includes `ffmpeg_build_id` and `relevance_lexicon_hash`. |
| `content_frames` | unchanged |
| `content_transcripts` | `+ transcript_kind text check in ('speech','music_lyrics','mixed','none')` (D-37) |
| `content_analysis` | unchanged; `media_fingerprint` is over **source** bytes |
| `content_analysis_runs` | the junction that makes per-run denominators correct when an item is reused |
| `content_analysis_claims` | the drill-down substrate. `claim_status`, `cue_check`, `frame_anchor_only`, `thumbnail_only_anchored`, `audience_authored`, `calibrated_confidence`, `model_confidence` (stored, never displayed) |
| `analysis_attempts`, `analysis_calls` | unchanged |
| `calibration_labels`, `calibration_models` | unchanged |

### 2.6 `0005_taxonomy` — 18 tables

Doc 02 §1.4 adopted, with D-13 (15 facets), D-14 (schema), D-17 (sole owner of merging), D-18
(review labels here, polymorphic FK fixed), D-19 (string pair ids), D-30 (market-timezone dates).

`label_facets`, `lang_buckets`, `canonicalisation_config`, `brand_name_snapshot`,
`label_observations`, `label_observation_rejects`, `observation_label_map`, `labels`,
`label_merges`, `label_pair_judgements`, `label_pair_gold`, `label_links`, `concept_groups`,
`concept_group_members`, `taxonomy_entries`, `label_decisions`, `label_vocab_snapshots`,
`label_support_daily`.

Plus the view `label_resolved` (recursive alias resolution with a depth cap) and the materialised
view `mv_label_support_weekly`.

### 2.7 `0006_commerce` — 12 tables

Doc 05 §1–§3 adopted, with D-2 (`platform text`), D-4 (run-independent), D-18
(`commerce_review_labels` deleted), C2 (`commerce_item_embeddings` deleted → `content_embeddings`),
`commerce_coverage` → `collection_coverage`.

`commerce_sellers`, `commerce_items`, `commerce_item_snapshots`, `commerce_item_brand_links`,
`commerce_bundle_components`, `commerce_product_groups`, `commerce_group_links`,
`commerce_group_merge_evidence`, `commerce_price_reference`, `commerce_reviews`,
`commerce_quarantine`, `products`.

`sold_semantics` defaults to `'unknown'` and the forbidden-operation guard (`SoldCounter` raising on
`__add__`/`__mul__`/`__int__`, plus the static scan) is binding.

### 2.8 `0007_clustering` — 9 tables

Doc 04 §7 adopted, with D-8 (`iteration_no`), D-11 (no social↔commerce links), D-12
(`discriminative_terms`, `representative_snippets`), D-44 (executed-DDL fixes), C2
(`content_embeddings` carries commerce views `listing_match_document` and `title_denoised`).

`content_embeddings`, `cluster_runs`, `clusters`, `cluster_revisions`, `cluster_lineage`,
`cluster_members`, `theme_timeseries`, `theme_trend_tests`, `cluster_links`.

### 2.9 `0008_theme_commerce` — 4 tables

Doc 05 §4 and §6: `commerce_theme_queries`, `commerce_theme_matches`, `commerce_theme_absences`,
`commerce_cooccurrence_stats`. Separated from `0006` because they FK `clusters`.

### 2.10 `0009_competitors` — 5 tables

Doc 06 §5.2 adopted, plus D-21 and D-41:
`competitor_relationships` (`+ first_detected_iteration`, `+ last_detected_iteration`),
`competitor_signal_evidence` (`+ iteration_no`), `competitor_signal_scores`,
`competitor_relationship_events`, `competitor_pair_enumeration`.
Plus views `v_competitor_relationships` (the brief's two-orientation read shape over a single
canonical `brand_id < competitor_brand_id` row) and `v_competitor_set_at(run_id, iteration_no)`
(doc 01 §11.8 needs a point-in-time set, not a current one).

### 2.11 `0010_findings` — 11 tables

Doc 07 §10 and §11.8 adopted, plus D-31 (no `org_id`), D-41 (`created_iteration`), and
`finding_evidence.cluster_revision_id` so a cluster-backed finding's numbers are re-derivable.

`evidence_packets`, `findings`, `finding_evidence`, `finding_statistics`, `finding_validations`,
`finding_refutations`, `finding_links`, `finding_quarantine`, `ask_sessions`, `ask_messages`,
`ask_tool_calls`. Plus the view `v_finding_provenance` (`security_invoker = true`).

### 2.12 Tables added beyond brief §29, and why

72 of the 92 tables are not in brief §29. They fall into six groups:

| Group | Count | Why the brief's list is insufficient |
|---|---|---|
| Research-loop auditability (`research_iterations`, `research_log`, `research_budget_ledger`, `research_saturation`, `research_run_transitions`, `hypothesis_evidence`) | 6 | brief §9 "the research process must be auditable" and §32's run view need the narrative tree, the per-iteration metrics and the spend ledger as rows, not as prose |
| Open taxonomy (18) | 18 | brief §19B demands an open vocabulary and §19's "frequently recurring discovered labels may later become normalised taxonomy entries" demands a promotion pipeline. An immutable emission log plus a rebuildable derived tier is the only shape that keeps §28's "preserve provenance" true while allowing thresholds to change |
| Evidence-grade analysis (`analysis_configs`, `content_frames`, `content_transcripts`, `content_analysis_claims`, `content_analysis_runs`, `analysis_attempts`, `analysis_calls`, `calibration_*`) | 10 | brief §27's "finding → evidence → actual video" needs a claim-level table with anchors; §28's sample sizes need per-run denominators; §33's ban on arbitrary scores needs a calibrator fitted on human labels |
| Cluster identity and honest trends (`cluster_runs`, `cluster_revisions`, `cluster_lineage`, `theme_timeseries`, `theme_trend_tests`, `cluster_links`) | 6 | brief §22's "only make growth or decline claims when temporal coverage is sufficient" is impossible without stable identity across runs and a stored test with its family size |
| Commerce structure (16, incl. `products` and the theme-link tables) | 16 | brief §15/§16's "do not assume every field exists" needs per-field quality; §24's "do not create simplistic scores" needs product-group-level presence rather than listing counts; §17 needs a first-class negative result |
| Brand resolution and competitor evidence (13) | 13 | brief §23's open-ended discovery needs an observation ledger (reversible), eleven signal detectors with a ledger that cannot double-count, and a per-relationship confidence that is not a competitor score |
| Findings machinery (9, excl. `findings`/`finding_evidence`) | 9 | brief §3's four claim layers, §28's "allow conflicting evidence" and §27's drill-down need the packet, the per-stat snapshot, the validator trail and the quarantine |

---

## 3. Unified Python module inventory

Root: `apps/api/aypo/`. Every responsibility has exactly one home. `[E]` = exists today.

```
config.py                   [E] Settings, capabilities(), missing_for(), budgets (D-25), market pack loader
logging.py                  [E]
main.py                     [E] FastAPI app
db/pool.py db/migrate.py    [E]

llm/
  client.py                 [E] LLMClient.structured/text, refusal+retry (D-34). THE only Anthropic caller.
  pricing.py                [E] rates, Spend, Ledger
  prompts/                  all prompt text, byte-stable, one module per prompt, version string at top
    mma_v1.py                 doc 03 §4: SYSTEM_PROMPT, USER_TEMPLATE, TASK_TAIL, DETAIL_SYSTEM
    label_vocab_v1.py         doc 02 §3.4a/b: emission instruction + suggestion block
    label_adjudicate_v1.py    doc 02 §2.8
    label_define_v1.py        doc 02 §4.4
    cluster_label_v1.py       doc 04 §3.3
    brand_adjudication.py     doc 06 §1.5
    relation_spans.py         doc 06 S7
    comparison_classify.py    doc 06 S1
    commerce_title_v1.py      doc 05 §2.3
    commerce_query_v1.py      doc 05 §4.3
    research_propose.py       doc 01 §3.2
    research_interpret.py     doc 01 INTERPRET
    research_synthesize.py    doc 01 SYNTHESIS
    finding_generate_v1.py    doc 07 §3.4
    finding_referee_v1.py     doc 07 §5.3
    ask_v1.py                 doc 07 §11.7

models/                     pure data; no I/O, no SQL
  provenance.py             [E] Platform, Strategy, CollectionMethod, Provenance, SamplingFrame
  content.py                [E] NormalizedContentItem, Creator, Engagement, Comment, SoundTrack
  commerce.py               [E] NormalizedCommerceItem, Resolved[T], FieldStatus, enums
  analysis.py               VideoAnalysis + Claim/Anchor/VisibleBrand/... (was analysis/schema.py)
  labels.py                 Facet, LangBucket, LabelObservationIn, SuggestionBlock
  clusters.py               CadDocument, ClusterRow, ThemeRef, MemberSample
  brand.py                  AliasChannel, SurfaceObservation, BrandRef
  competitor.py             EvidenceRow, PairAggregate, TypingDecision, RelationshipType
  findings.py               EvidencePacket, ComputedStat, EvidenceItem, StatCandidate,
                            MetricResult, Claim/Refusal, FindingDraft
  jobs.py                   Job, JobKind, JobResult

providers/                  one module per external service; no business logic
  apify.py embeddings.py storage.py transcription.py websearch.py   [E]
  marketplace.py            MarketplaceProvider protocol, ProviderPage, RawCommerceRecord
  serper_commerce.py        degraded web-snippet marketplace provider

collectors/                 brief §10. ONE Collector protocol (D-M6).
  base.py                   [E, amended] Collector protocol + CollectorCapabilities + DiscoveryResult
  capabilities.py           collection_sources registry, availability(), expand_vocabulary()
  relevance.py              relevance_score + relevance_source (D-40)
  creators.py               upsert_creator (D-5)
  dedupe.py                 dedupe_group_id for both channels (D-6)
  tiktok.py youtube.py      [E]
  instagram.py facebook.py websearch.py
  commerce_base.py          CommerceCollector(Collector[NormalizedCommerceItem]) + normalize()
  shopee.py shopee_reviews.py lazada.py

analysis/                   per-item work. Knows nothing about brands, competitors or the market.
  media.py retrieval.py     [E] probe, scene scoring, frame selection, yt-dlp retrieval (D-36)
  transcribe.py             whisper-1, no language, no prompt, transcript_kind (D-37)
  fusion.py                 MultimodalAnalyzer: build_blocks, analyze, submit_batch, collect_batch
  validators.py             doc 03 §6.4 hard gates (tier caps, ASR gates, summary provenance check)
  calibration.py            check_cue, support_features, fit_calibrator, classify_claim
  sponsorship.py            bucket -> likelihood map (D-22); platform-label precedence (D-36)
  idempotency.py            config_fingerprint, media_fingerprint, canonical_json
  costing.py pipeline.py repo.py
  cad.py                    canonical analysis document; reads redaction from brand registry (D-38)
  embed_cache.py            batching, content_hash cache, ledger, over providers/embeddings
  space.py                  centring, PC removal, reducer harness, fingerprint
  clustering.py             HDBSCAN config, oversize repair, noise recovery, soft membership, rollups
  labeling.py               member sampling, label prompt, acceptance checks, null control
  labels/                   doc 02 — the ONLY owner of label canonicalisation (D-17)
    facets.py               the D-13 mapping, loaded from label_facets
    normalise.py langid.py
    cluster.py              leader clustering, exact in-partition scans
    guards.py               lexical gate + hypergeometric anti-merge guard
    adjudicate.py           two-order batched adjudication (D-19)
    vocab_feedback.py       suggestion block (D-15)
    promote.py              promotion gate, demotion, flags
    bias_metrics.py         rarefied arm comparison, BH-FDR
    service.py repo.py
  commerce_price.py commerce_noise.py commerce_titles.py
  commerce_identity.py commerce_reviews.py

intelligence/               cross-item reasoning. Everything here may know about brands and the market.
  periods.py                market-timezone bucketing, monthly/quarterly switch (D-30)
  independence.py           independence_unit, design_effect, effective_sample_size (D-T8)
  themes.py                 ThemeRef read-model over clusters (D-12)
  identity.py               cross-run cluster identity + lineage (D-9)
  trends.py                 THE trend classifier (D-32); called with either indicator
  comparisons/
    social.py               brand-by-theme, category stratum, McNemar, creator-clustered CIs
    commerce.py             doc 05 §5's five metrics
    competitive.py          doc 06 §7's §24 computations
  claims/                   the single claim gate (D-27)
    guard.py templates.py validators.py lexicons.py
  theme_commerce.py         doc 05 §4 two-pass matching + absence states
  commerce_cooccurrence.py  doc 05 §6.1-6.2
  brand_resolution/
    normalize.py confusion.py blocking.py cascade.py registry.py
    hierarchy.py adjudicate.py reversibility.py config.py
  competitors/
    base.py context.py aggregate.py typing.py discriminate.py decay.py
    seeds.py enumerate.py service.py
    signals/s01..s11_*.py
  findings/
    packet.py               PacketBuilder (S1)
    generator.py            S2
    validators.py           S3 V1..V18  (imports intelligence/claims/validators.py)
    referee.py              S5 adversarial pass
    confidence.py           S6 scoring, bands, gates
    pipeline.py renderer.py repo.py
  ask/
    tools.py agent.py       Ask the Data (moved out of research/, D-M8)

research/                   orchestration ONLY. No analytics.
  states.py models.py orchestrator.py iteration.py allocation.py budget.py
  stopping.py failure.py log.py repository.py
  queries/normalize.py proposer.py dedupe.py guardrail.py fallback.py
  saturation/metrics.py stability.py view.py

jobs/                       flat, one module per job kind
  queue.py worker.py        [E, queue amended per D-42]
  research_tick.py analyze_item.py canonicalise_labels.py cluster_and_trend.py
  commerce_collect.py commerce_normalise.py commerce_group.py
  commerce_theme_link.py commerce_reviews.py
  competitor_discovery.py findings.py

api/routes/
  health.py research.py content.py taxonomy.py clusters.py
  commerce.py brands.py competitors.py findings.py ask.py
```

Module-ownership conflicts resolved: doc 05's `MarketplaceCollector` becomes a subclass of the one
`Collector` protocol; doc 02's `jobs/tasks/` becomes flat `jobs/`; doc 02's `api/routes/` spelling
wins over doc 01's and doc 05's `api/<name>.py`; doc 04's `analysis/embedding.py` becomes
`analysis/embed_cache.py` to distinguish it from the existing `providers/embeddings.py`; doc 07's
`research/ask/` becomes `intelligence/ask/`; doc 03's `analysis/schema.py` becomes
`models/analysis.py` so every Pydantic contract lives under `models/`.

---

## 4. Cross-module contracts

These are what parallel implementers code against. Type-annotated, complete, and unambiguous.
All are in `apps/api/aypo/models/`.

### 4.1 The normalised content object

```python
# aypo/models/content.py
from __future__ import annotations
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID
from pydantic import BaseModel, Field


class DatePrecision(StrEnum):
    DAY = "day"; MONTH = "month"; UNKNOWN = "unknown"


class MediaKind(StrEnum):
    VIDEO = "video"; IMAGE = "image"; TEXT = "text"


class Creator(BaseModel):
    """Platform-stated only. Brief §28 forbids invented demographics, so nothing here
    is inferred. `id_basis` records whether identity is a real platform id or a handle,
    because every design effect in §6 is weaker when it is a handle."""
    platform: str                                   # D-2 vocabulary
    handle: str
    handle_norm: str                                # NFKC + casefold; the dedupe key
    platform_user_id: str | None = None
    display_name: str | None = None
    follower_count: int | None = None
    verified: bool | None = None
    id_basis: Literal["platform_id", "handle"] = "handle"
    profile_url: str | None = None


class Engagement(BaseModel):
    """Attention, not preference. Brief §28: nothing downstream may read a high view
    count as approval. Every field is optional; actors change output without notice."""
    views: int | None = None
    likes: int | None = None
    comments_count: int | None = None
    shares: int | None = None
    saves: int | None = None
    reposts: int | None = None
    measured_at: datetime | None = None
    is_cumulative: bool = True                      # lifetime counters; never valid in a trend


class Comment(BaseModel):
    text: str
    author_handle: str | None = None                # stored; NEVER sent to the analysis model
    likes: int | None = None
    published_at: datetime | None = None


class SoundTrack(BaseModel):
    name: str | None = None
    author: str | None = None
    platform_sound_id: str | None = None
    is_original: bool | None = None


class NormalizedContentItem(BaseModel):
    """What collectors produce and analysis consumes. Downstream NEVER sees a provider
    payload (brief §10). Every field but `platform`, `platform_item_id` and `source_url`
    is optional, and a missing field must degrade, never raise (brief §15/§16)."""

    # identity — run-independent (D-4 symmetry)
    platform: str
    platform_item_id: str
    source_url: str
    url_canonical: str
    media_kind: MediaKind = MediaKind.VIDEO

    # media
    media_url: str | None = None                    # may expire; re_resolve_media() refreshes
    thumbnail_url: str | None = None
    duration_s: float | None = None

    # text
    caption: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    overlay_text: str | None = None
    comments: list[Comment] = Field(default_factory=list)

    # context
    creator: Creator | None = None
    sound: SoundTrack | None = None
    published_at: datetime | None = None
    published_at_precision: DatePrecision = DatePrecision.UNKNOWN
    engagement: Engagement = Field(default_factory=Engagement)

    # platform-stated sponsorship — an OBSERVED fact, stronger than any model guess (D-36)
    platform_is_sponsored: bool | None = None
    platform_paid_partnership_label: bool | None = None

    # provenance (brief §7) — these mirror the research_results row, they do not replace it
    research_run_id: UUID
    discovery_query_id: UUID
    collection_provider: str                        # 'apify:<actor>' | 'youtube_api' | 'serper'
    collection_method: str                          # search|hashtag|creator|sound|related
    raw_rank: int | None = None                     # MANDATORY for provider_actor rows (§5)
    sort_order: str | None = None                   # relevance|recent|top, as REQUESTED
    page_depth: int = 1
    discovered_at: datetime

    # organically discovered expansion terms (brief §8D, ENVIRONMENT.md)
    related_search_terms: list[str] = Field(default_factory=list)

    platform_payload: dict[str, Any] = Field(default_factory=dict)

    def dedupe_key(self) -> str:
        """sha256 of (platform, platform_item_id) or of url_canonical. Stable across runs."""
```

### 4.2 The normalised commerce object

```python
# aypo/models/commerce.py
from decimal import Decimal
from typing import Literal

FieldStatus = Literal["present", "missing", "unparseable", "implausible", "provider_placeholder"]
PriceStatus = Literal["ok", "ambiguous", "unparseable", "out_of_band",
                      "range_too_wide", "non_php_currency"]
ListingComposition = Literal[
    "single_brand_single_product", "single_brand_multi_variant", "multi_brand_bundle",
    "assortment_unspecified", "non_category_or_accessory", "unresolved",
]
SoldSemantics = Literal["unknown", "platform_lifetime_reported",
                        "platform_window_reported", "provider_derived"]


class Resolved[T](BaseModel):
    """Every optional marketplace field resolves to a value plus a status. `normalize()`
    has exactly six required inputs and NEVER raises for anything else (doc 05 §1.3)."""
    value: T | None
    status: FieldStatus
    note: str | None = None


class Seller(BaseModel):
    platform: str
    platform_seller_id: str | None = None
    name_raw: str | None = None
    name_slug: str | None = None
    is_official_store_reported: bool | None = None  # provider badge, NOT verified by us
    is_unknown_identity: bool = False               # the single synthetic row per platform
    rating_reported: Decimal | None = None
    follower_count_reported: int | None = None


class CommerceReviewIn(BaseModel):
    platform_review_id: str | None = None
    text_raw: str | None = None
    rating: int | None = None
    review_date_raw: str | None = None
    review_date: date | None = None
    review_date_precision: Literal["day", "month", "relative_bucket", "unknown"] = "unknown"
    variant_label: str | None = None
    helpful_count: int | None = None


class NormalizedCommerceItem(BaseModel):
    """Run-independent (D-4). Per-run facts live in research_results; time-varying
    metrics live in commerce_item_snapshots. Money is integer minor units, never float."""

    # identity
    platform: str                                   # 'shopee_ph' | 'lazada_ph' | 'web'
    platform_item_id: str
    source_url: str
    collection_mode: Literal["provider_actor", "web_search_snippet"] = "provider_actor"
    collection_provider: str
    observed_at: datetime                           # when the PROVIDER observed it
    raw_payload: dict[str, Any]
    raw_payload_sha256: bytes
    normalisation_version: int

    # text
    title_raw: str | None = None
    title_denoised: str | None = None
    noise_spans: list[tuple[int, int]] = Field(default_factory=list)
    description_raw: str | None = None

    seller: Seller | None = None

    # price — PHP centavos as int; scale resolved per cohort, never per row
    currency: str | None = None
    price_minor: int | None = None
    price_min_minor: int | None = None
    price_max_minor: int | None = None
    price_is_range: bool = False
    original_price_minor: int | None = None
    discount_pct: Decimal | None = None
    price_status: PriceStatus = "unparseable"
    price_scale_divisor: int | None = None
    price_parse_confidence: Decimal | None = None

    # pack / weight
    pack_count: int | None = None
    unit_net_weight_g: Decimal | None = None
    total_net_weight_g: Decimal | None = None
    weight_basis: Literal["g", "ml_as_g", "oz_converted"] | None = None
    unit_price_per_100g_minor: int | None = None

    # platform-reported metrics — AS REPORTED, never recomputed, never summed
    rating_avg: Decimal | None = None
    rating_count: int | None = None
    rating_histogram: dict[str, int] | None = None
    review_count: int | None = None
    sold_count_raw: int | None = None               # only when the payload gives an exact int
    sold_count_text: str | None = None              # verbatim, e.g. "1.2k sold"
    sold_count_band: str | None = None
    sold_semantics: SoldSemantics = "unknown"       # the only honest default
    metrics_as_of: datetime | None = None

    # classification
    composition: ListingComposition = "unresolved"
    product_label: str | None = None
    product_slug: str | None = None
    variant_labels: list[str] = Field(default_factory=list)
    category_path: list[str] = Field(default_factory=list)
    category_scope: str | None = None

    # quality
    field_quality: dict[str, FieldStatus] = Field(default_factory=dict)
    quality_flags: list[str] = Field(default_factory=list)
    is_degraded: bool = False

    image_s3_key: str | None = None
    image_url_raw: str | None = None
    blocking_key: str | None = None

    reviews: list[CommerceReviewIn] = Field(default_factory=list)
```

### 4.3 The collector interface

```python
# aypo/collectors/base.py   (brief §10, provider-independent)
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class CollectorCapabilities:
    """Declared, not discovered mid-run. The loop must know what it may ask for rather
    than find out from a provenance record that a filter was silently ignored."""
    supports_date_range: bool = False
    supports_sort: tuple[str, ...] = ()             # subset of ('relevance','recent','top')
    max_page_depth: int = 1
    supports_vocabulary_expansion: bool = False     # brief §1: retrieve vocab, don't guess it
    supports_reviews: bool = False
    max_items_per_search: int = 60


@dataclass(frozen=True, slots=True)
class SearchFilters:
    limit: int = 50
    published_after: date | None = None
    published_before: date | None = None
    sort: Literal["relevance", "recent", "top"] | None = None
    page_depth: int = 1
    min_engagement: int | None = None
    research_query_id: UUID | None = None           # echoed back on every result


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """One discovered item, before retrieval. raw_rank and the applied sort are
    MANDATORY, not best-effort: they are the only record of the ranking bias in the
    corpus, and without them no prevalence statement can be qualified honestly (§5)."""
    platform: str
    external_id: str | None
    url: str
    url_canonical: str
    raw_rank: int
    sort_applied: str
    page_depth: int
    published_at: datetime | None
    published_at_precision: DatePrecision
    research_query_id: UUID
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    available: bool
    reason: str | None
    missing_credentials: tuple[str, ...]
    checked_at: datetime


@dataclass(frozen=True, slots=True)
class CollectionResult[TItem]:
    query: str
    items: list[TItem]
    discovered: list[DiscoveryResult]
    provider_calls: int = 0
    provider_results: int = 0
    unusable_records: int = 0                       # feeds the §9.3 garbage gate
    provider_cost_units: float = 0.0
    quarantined: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    available: bool = True
    unavailable_reason: str | None = None

    @property
    def garbage_rate(self) -> float: ...


class Collector[TItem](ABC):
    """Three responsibilities, deliberately separated (brief §7, §10).
    A collector NEVER raises into the research loop over a missing credential or a dead
    provider: it reports itself unavailable and the run continues on what works."""

    platform: ClassVar[str]
    normalisation_version: ClassVar[int]

    @abstractmethod
    def capabilities(self) -> CollectorCapabilities: ...

    @abstractmethod
    async def availability(self) -> ProviderHealth:
        """Must not make a billable call."""

    @abstractmethod
    async def search(self, query: str, filters: SearchFilters) -> CollectionResult[TItem]: ...

    @abstractmethod
    async def fetch(self, discovered: DiscoveryResult) -> TItem | None: ...

    @abstractmethod
    def normalize(self, raw: Mapping[str, Any], *, discovered: DiscoveryResult,
                  run_id: UUID) -> TItem | None:
        """Pure, synchronous, and never raises except on the required-field set.
        Returns None for a quarantined payload; the reason goes in CollectionResult."""

    async def expand_vocabulary(self, seed_terms: Sequence[str]) -> list[str]:
        """Brief §1's most valuable affordance: RETRIEVE category vocabulary (search-engine
        related queries, marketplace category trees, hashtag co-occurrence) instead of
        letting a model guess it. Default [] — the loop then degrades to the capped
        prior_derived path and flags seed_dependence='high'."""
        return []

    async def re_resolve_media(self, item: TItem) -> str | None:
        """Refresh an expired CDN URL once (doc 03 CSR-2). Default None."""
        return None


class CommerceCollector(Collector[NormalizedCommerceItem]):
    @abstractmethod
    async def fetch_reviews(self, platform_item_id: str, *, limit: int = 20
                            ) -> list[CommerceReviewIn]: ...
```

### 4.4 The analysis output object

```python
# aypo/models/analysis.py   (doc 03 §5, with D-13/D-14/D-39 applied)
Modality = Literal["frame", "transcript", "caption", "overlay_text", "comments", "metadata"]
Basis    = Literal["seen", "heard", "written", "inferred"]
GridCell = Literal["top_left","top_center","top_right","mid_left","center","mid_right",
                   "bottom_left","bottom_center","bottom_right"]
ShortLabel = Annotated[str, Field(min_length=1, max_length=60)]
Cue        = Annotated[str, Field(min_length=8, max_length=160)]

ConsumptionState = Literal["present", "held", "being_opened", "shown_to_camera",
                           "in_use_or_consumed", "offered_or_shared", "unclear"]
Prominence  = Literal["focal", "secondary", "background", "fleeting"]
Legibility  = Literal["clear", "partial", "illegible"]
IdBasis     = Literal["packaging_wordmark", "logo_mark", "shelf_or_signage_label",
                      "other_on_screen_text", "product_form_only"]


class Anchor(BaseModel):
    model_config = {"extra": "forbid"}
    modality: Modality
    frame_index: int | None = Field(default=None, ge=0)
    t_start_s: float | None = Field(default=None, ge=0)
    t_end_s: float | None = Field(default=None, ge=0)
    quote: str | None = Field(default=None, max_length=240)
    # validators: frame -> frame_index; transcript -> t_start_s + quote;
    #             caption/overlay_text/comments -> quote; t_end_s >= t_start_s


class Claim(BaseModel):
    """Everything the model may assert inherits this, so anchors and cues are
    structurally unavoidable rather than politely requested."""
    model_config = {"extra": "forbid"}
    cue: Cue
    basis: Basis
    anchors: list[Anchor] = Field(min_length=1, max_length=4)
    model_confidence: float = Field(ge=0.0, le=1.0)   # stored for audit, NEVER displayed


class DiscoveredLabel(Claim):                        # D-14
    label: ShortLabel
    gloss_en: Annotated[str, Field(min_length=1, max_length=240)]
    label_kind: ShortLabel | None = None


class VisibleBrand(Claim):
    brand_text_as_seen: ShortLabel                   # verbatim, unnormalised (CSR-6)
    identification_basis: IdBasis                    # product_form_only is REJECTED here
    legibility: Legibility
    prominence: Prominence
    screen_share: Literal["dominant", "moderate", "minor"] | None = None
    first_seen_frame_index: int = Field(ge=0)        # == min(frame_indices)
    frame_indices: list[int] = Field(min_length=1, max_length=32)


class VisibleProduct(Claim):
    description: ShortLabel
    brand_resolved: bool
    brand_text_as_seen: ShortLabel | None = None
    consumption_state: ConsumptionState
    prominence: Prominence
    has_legible_text_region: bool = False
    frame_indices: list[int] = Field(min_length=1, max_length=32)
    quantity_visible: Literal["one", "few", "many", "unclear"] = "unclear"


class MentionedBrand(Claim):
    brand_text_as_said_or_written: ShortLabel
    mention_kind: Literal["spoken","caption","hashtag","on_screen_text","comment"]
    speaker_role: Literal["on_camera_person","voice_over","caption_author","commenter","unclear"]
    # may NOT carry a frame anchor; basis must be heard|written


class StatedUse(Claim):
    statement: ShortLabel
    subject_brand_text: ShortLabel | None = None


class SponsorshipAssessment(BaseModel):
    model_config = {"extra": "forbid"}
    bucket: Literal["likely_paid", "possibly_paid", "likely_organic", "undetermined"]
    cues: list[SponsorCue] = Field(default_factory=list, max_length=6)
    subject_brand_text: ShortLabel | None = None
    # gates: likely_paid needs a STRONG cue; likely_organic needs an ORGANIC cue and no
    # strong paid cue; any non-undetermined bucket needs >=1 cue


class CoAppearance(Claim):
    """NEVER a competitive relationship. §23 derives that downstream from many items."""
    brand_texts: list[ShortLabel] = Field(min_length=2, max_length=6)
    framing: Literal["side_by_side_comparison","ranking_or_ordering",
                     "substitution_discussed","incidental_co_presence","unclear"]


LIST_CAP = 6     # stated numerically in the prompt; enforced post-hoc by Pydantic


class VideoAnalysis(BaseModel):
    model_config = {"extra": "forbid"}

    visible_brands:   list[VisibleBrand]   = Field(default_factory=list, max_length=12)
    visible_products: list[VisibleProduct] = Field(default_factory=list, max_length=12)
    mentioned_brands: list[MentionedBrand] = Field(default_factory=list, max_length=12)
    stated_use:       list[StatedUse]      = Field(default_factory=list, max_length=LIST_CAP)

    content_format:    DiscoveredLabel | None = None
    format_features:   list[DiscoveredLabel] = Field(default_factory=list, max_length=LIST_CAP)
    creator_archetype: DiscoveredLabel | None = None
    languages:         list[LanguageObservation] = Field(default_factory=list, max_length=6)
    sentiment:         list[SentimentObservation] = Field(default_factory=list, max_length=LIST_CAP)
    purchase_intent:   list[PurchaseOrAcquisitionIntent] = Field(default_factory=list, max_length=LIST_CAP)
    sponsorship:       SponsorshipAssessment

    # brief §19B — fifteen facets, a total mapping (D-13)
    occasions:               list[DiscoveredLabel] = Field(default_factory=list, max_length=LIST_CAP)
    behaviors:               list[DiscoveredLabel] = Field(default_factory=list, max_length=LIST_CAP)
    social_context:          list[DiscoveredLabel] = Field(default_factory=list, max_length=LIST_CAP)
    activities:              list[DiscoveredLabel] = Field(default_factory=list, max_length=LIST_CAP)
    cultural_signals:        list[DiscoveredLabel] = Field(default_factory=list, max_length=LIST_CAP)
    slang:                   list[DiscoveredLabel] = Field(default_factory=list, max_length=LIST_CAP)
    humor_signals:           list[DiscoveredLabel] = Field(default_factory=list, max_length=LIST_CAP)
    narrative:               list[DiscoveredLabel] = Field(default_factory=list, max_length=LIST_CAP)
    product_pairings:        list[DiscoveredLabel] = Field(default_factory=list, max_length=LIST_CAP)
    audience_signals:        list[DiscoveredLabel] = Field(default_factory=list, max_length=LIST_CAP)
    commercial_signals:      list[DiscoveredLabel] = Field(default_factory=list, max_length=LIST_CAP)
    setting_observations:    list[DiscoveredLabel] = Field(default_factory=list, max_length=LIST_CAP)
    unexpected_observations: list[DiscoveredLabel] = Field(default_factory=list, max_length=4)

    brand_co_appearance: list[CoAppearance] = Field(default_factory=list, max_length=4)

    representative_quote: Anchor | None = None       # D-39: verbatim source span, <=30 words
    evidence_insufficient_for: list[ShortLabel] = Field(default_factory=list, max_length=32)
    summary: Annotated[str, Field(max_length=600)]
    field_confidence: list[FieldConfidence] = Field(default_factory=list, max_length=32)
    detail_request: DetailRequest | None = None
```

### 4.5 The label object

```python
# aypo/models/labels.py
Facet = str          # validated against label_facets at the boundary; never an enum (D-13)
LangBucket = str     # validated against lang_buckets; never an enum (doc 03 §0.3)


class LabelObservationIn(BaseModel):
    """One verbatim model emission. APPEND-ONLY once written. Derived values
    (normalised_form, lang_bucket, label_id) live in observation_label_map keyed by
    canon_version, so a recalibration can recompute them (doc 02 I1)."""
    research_run_id: UUID
    content_item_id: UUID | None = None
    commerce_item_id: UUID | None = None
    content_analysis_id: UUID | None = None          # exactly one of these two (D-18)
    commerce_review_id: UUID | None = None
    source_kind: Literal["video","post","commerce_listing","commerce_review","web_page"]

    facet: Facet
    surface_form: Annotated[str, Field(min_length=1, max_length=160)]
    gloss_en: Annotated[str, Field(min_length=1, max_length=400)]
    evidence_quote: str | None = None
    evidence_timecode_s: float | None = None
    model_confidence: float | None = None

    extractor_version: str
    prompt_version: str
    norm_pipeline_hint: str
    vocab_snapshot_id: UUID | None = None
    vocab_mode: Literal["suggested", "holdout"]
    was_suggested: bool = False                      # COMPUTED by us, never self-reported
    shown_rank: int | None = None                    # set whenever the facet's block was non-empty
    observation_seq: int                             # monotonic within run; the rebuild pin


class LabelRef(BaseModel):
    """The resolved, canonical label other subsystems consume. Always carries its
    canon_version: support is a function of the merge thresholds, so a figure without
    its generation is not a figure (doc 02 §6.1 rule 6)."""
    label_id: UUID
    canon_version: int
    facet: Facet
    lang_bucket: LangBucket
    normalised_form: str                             # immutable identity within a canon_version
    display_form: str                                # most frequent RAW surface form
    gloss_en: str | None
    status: Literal["candidate","emerging_candidate","canonical","alias","pending_merge","blocked"]
    flags: list[str]                                 # orthogonal to status (doc 02 §4.5)
    support_count: int                               # DISTINCT analysis units
    distinct_content: int
    distinct_commerce: int
    distinct_creators: int
    distinct_platforms: int
    distinct_strategies: int
    holdout_support: int                             # 0 => the label was never seen unprompted
    evidence_strength: float | None                  # NOT a probability (§6 layer L2)
    concept_group_id: UUID | None


class TaxonomyService(Protocol):
    """In-process read API. Intelligence consumers use this, not HTTP."""
    async def resolved_label_ids(self, label_ids: Sequence[UUID]) -> dict[UUID, UUID]: ...
    async def labels_for_content(
        self, content_item_ids: Sequence[UUID], *,
        facets: Sequence[Facet] | None = None,
        status: Sequence[str] = ("canonical", "emerging_candidate"),
    ) -> dict[UUID, list[LabelRef]]: ...
    async def vocabulary_for_run(self, run_id: UUID) -> tuple[str, ...]: ...
```

### 4.6 The evidence packet

```python
# aypo/models/findings.py   (doc 07 §3.3, unchanged in substance)
AssertionDomain = Literal["corpus_composition","content_performance","commerce_listing",
                          "commerce_review","temporal","cross_corpus"]
FindingKind = Literal["existence","rate_comparison","association","share_of_presence",
                      "trend","absence","social_commerce_link","competitive_gap"]


class ComputedStat(BaseModel):
    """Computed by CODE. The model receives only `stat_ref` and `display_value`; its
    output schema contains no numeric fields and a validator rejects any digit in it."""
    model_config = ConfigDict(frozen=True)
    stat_ref: str                      # "S1".."S20" — the only handle the model gets
    name: str
    label: str
    value: float
    display_value: str                 # PRE-FORMATTED; the only string ever rendered
    unit: Literal["count","proportion","ratio","index","days","tau","rho","currency"]
    currency_code: str | None          # from the market pack when unit='currency' (D-29)
    n: int
    denominator: int | None
    denominator_desc: str
    n_eff: float | None
    design_effect: float | None
    ci_low: float | None
    ci_high: float | None
    ci_method: Literal["wilson_deff","cluster_bootstrap","cluster_t",
                       "mk_variance_blocked","permutation","none"]
    formula: str
    assertion_domain: AssertionDomain
    subject_refs: tuple[str, ...]      # the ONLY entities a sentence citing this may name
    is_cumulative: bool = False        # lifetime counter: never valid in a trend (D-32 T5)
    selected_from_k: int = 1
    recompute_key: str                 # must be registered in StatRecomputer
    recompute_params: dict[str, Any]


class EvidenceItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    ev_ref: str                        # "E1".."E120"
    kind: Literal["content","commerce_item","commerce_review","cluster","creator","research_query"]
    role: Literal["supporting","contradicting","non_instance","confounded","illustrative"]
    ref_id: UUID
    cluster_revision_id: UUID | None = None          # for kind='cluster'; makes numbers re-derivable
    platform: str | None
    creator_ref: str | None
    independence_unit: str             # NEVER null. §6 / independence.py defines it.
    published_at: datetime | None
    sampling_strategy: Literal["brand","category","competitor","theme","hypothesis"]
    discovery_query_id: UUID | None
    query_vintage_half: int | None     # D-32 T4
    collection_provider: str | None
    analysis_confidence: float | None  # calibrated_confidence for THIS property
    supports_properties: tuple[str, ...]
    sponsored_likelihood: float | None # from the D-22 mapping
    usage_type: Literal["active_use","handled","incidental","mentioned_only","unknown"]
    excerpt: str | None
    excerpt_field: str | None
    excerpt_start: int | None
    excerpt_end: int | None


class PopulationFrame(BaseModel):
    description: str
    size: int
    frame_n_eff: float                 # same two-stage capping over the whole frame (§6.2b)
    design_effect: float
    filter_spec: dict[str, Any]
    date_from: datetime
    date_to: datetime


class PropertyPredicate(BaseModel):
    key: str
    description: str
    structured_sql: str | None
    negation_sql: str | None                         # item-level kinds
    counter_analysis: "CounterAnalysis | None" = None  # frame-level kinds


class PacketDiagnostics(BaseModel):
    n_supporting: int
    n_contradicting: int
    n_confounded: int
    inverse_simpson: dict[str, float]  # independence_unit, query, platform, strategy, week
    top_unit_share: float
    top_query_share: float
    span_days: int
    sponsored_share: float
    unknown_confidence_share: float
    unknown_independence_share: float
    near_duplicate_groups_collapsed: int
    sources_unavailable: tuple[str, ...]
    candidates_in_family: int
    selected_from_k: int
    bucket_series: tuple[dict[str, Any], ...] = ()
    query_vintage_tau: float | None = None
    trailing_bucket_excluded: bool = False


class EvidencePacket(BaseModel):
    model_config = ConfigDict(frozen=True)
    packet_id: UUID
    research_run_id: UUID
    candidate_key: str
    finding_kind: FindingKind
    subject_brand_id: UUID | None
    population_frame: PopulationFrame
    property_predicate: PropertyPredicate
    stats: tuple[ComputedStat, ...]
    items: tuple[EvidenceItem, ...]                  # <=120, stratified; concentration math
                                                     #   runs over the FULL set, not this sample
    vocabulary: tuple[str, ...]                      # the ONLY label strings the model may use
    items_are_a_sample_of: int
    diagnostics: PacketDiagnostics
    sampling_frame: "SamplingFrame"                  # §5's disclosure contract, on every packet
    evidence_design: Literal["observational_snapshot"] = "observational_snapshot"
    market_code: str
    builder_version: str
```

`vocabulary` is assembled **only** from strings attested in collected data:
`labels.display_form` for admitted leaders, `clusters.label`, `brands.canonical_name`,
`products.canonical_label`, `commerce_items.category_path`. `research_queries.query_text` is
**excluded** — a theme- or hypothesis-strategy query is a sentence we wrote, and admitting its words
would let the generator name a concept that exists only in our own search box.

### 4.7 The job / queue interface

```python
# aypo/jobs/queue.py   (Postgres-backed; no Redis, no Celery)
JobKind = Literal[
    "research.tick",
    "collect.search", "collect.retrieve",
    "analyze.item", "analyze.detail",
    "taxonomy.canonicalise", "taxonomy.adjudicate", "taxonomy.promote",
    "taxonomy.rollup", "taxonomy.bias_report",
    "cluster.cluster_and_trend",
    "commerce.collect", "commerce.normalise", "commerce.titles", "commerce.embed",
    "commerce.group", "commerce.reviews.collect", "commerce.reviews.label",
    "commerce.theme.screen", "commerce.theme.queries", "commerce.theme.link",
    "commerce.metrics", "commerce.cooccurrence",
    "competitor.resolve_brands", "competitor.enumerate_pairs", "competitor.detect_signals",
    "competitor.aggregate_and_type", "competitor.decay", "competitor.comparisons",
    "findings.generate", "findings.finalise",
]


@dataclass(slots=True)
class Job:
    id: UUID
    kind: JobKind
    payload: dict[str, Any]
    research_run_id: UUID | None
    iteration_no: int | None                         # D-42: indexed, not unpacked from payload
    attempts: int
    max_attempts: int
    priority: int
    created_at: datetime


async def enqueue(
    kind: JobKind, payload: dict[str, Any], *,
    research_run_id: UUID | None = None,
    iteration_no: int | None = None,
    priority: int = 100,
    max_attempts: int = 3,
    dedupe_key: str | None = None,
) -> UUID:
    """ALWAYS returns a job id. On a dedupe_key collision it returns the EXISTING job's
    id, never None (D-42 item 1): the research loop's DISCOVER completion predicate is
    'every admitted query has job_id IS NOT NULL', and returning None after a crash makes
    that predicate permanently unsatisfiable and abandons the iteration."""


async def claim(kinds: Sequence[JobKind] | None = None, *,
                lease_seconds: int = 1800) -> Job | None:
    """SELECT ... FOR UPDATE SKIP LOCKED. At-least-once delivery; every handler is
    idempotent on its own key, so at-least-once is sufficient."""

async def heartbeat(job_id: UUID, *, lease_seconds: int = 1800) -> None: ...
async def succeed(job_id: UUID, result: dict[str, Any] | None = None) -> None: ...
async def fail(job_id: UUID, error: str, *, retry_in_seconds: int | None = None) -> None: ...

async def cancel(research_run_id: UUID, iteration_no: int | None = None) -> int:
    """Bulk cancellation by (run, iteration). Needed by the hard_stop budget guard and by
    phase-deadline handling. Returns the number cancelled."""

async def counts_by_state(research_run_id: UUID, iteration_no: int | None = None,
                          kind: JobKind | None = None) -> dict[str, int]:
    """Index-only. Every *_WAIT completion predicate in the research loop is
    counts_by_state(...)['pending'] + [...]['running'] == 0."""

async def advisory_lock(key: str) -> AbstractAsyncContextManager[None]:
    """pg_advisory_xact_lock(hashtextextended(key, 0)). Single-flight for the stage
    orderings in cluster_and_trend, competitor_discovery and findings."""
```

**Handler contract.** Every handler is a pure function of (stored rows, payload); it writes with
`INSERT … ON CONFLICT DO UPDATE` against a declared idempotency key; it never raises for a
domain-level failure (it records the failure and returns), only for infrastructure faults the queue
should retry. Long-running waits release the lease and re-schedule rather than holding it.

---

## 5. The consolidated answer to collection-time sampling bias

**One sentence.** The corpus is a non-probability, ranked, survivorship-filtered,
query-conditioned sample; the system never corrects for that, it **identifies what is estimable,
refuses what is not, records the design it actually used, and attempts three falsifications.**

Every subsystem implements this single answer. Nothing else is permitted.

### 5.1 What is estimable, and what is not

For a theme `θ` observed in period `p`, the corpus count is
`n_θ(p) = N_world(θ,p) · g(p) · r_θ(p) / Z`, where `g` is the exposure function (ranker recency
bias × top-N truncation × survivorship × metadata attrition) and `r_θ` is a theme-specific
ranking/retention factor. `g` is strongly increasing toward collection time, so **a per-period count
series is a near-deterministic manufacturer of upward trends**: at `g` growing 1.5×/month, a flat
theme reads +50%/month and a theme declining 20%/month still reads +20%/month.

Within-period composition share cancels `g` exactly:

```
share_θ(p) = n_θ(p) / N(p)   ->   N_world(θ,p)·r_θ(p) / Σ_θ' N_world(θ',p)·r_θ'(p)
```

So: **composition is identified; volume is not.** This subsystem therefore never reports a claim
about volume. Every temporal statement is "share of analysed content published in period p" and
always carries `N(p)` and `N_eff(p)`. Absolute-volume trends are hard-refused with
`volume_claim_unidentified`.

The residual is `r_θ(p)` — a recommender that starts favouring one theme. That is **not**
eliminated; it is stated as the **common-exposure assumption**, falsified three ways (§5.4), and
named in every trend claim's method string.

### 5.2 Eight mechanisms, each with one owner

| # | Mechanism | Owner | Binding rule |
|---|---|---|---|
| 1 | **Record the design per item** | `collectors/*` → `research_results` | `raw_rank`, `sort_order`, `page_depth`, `provider`, `collection_method`, `published_at_precision`, `first_seen_query_id` are **mandatory**, not best-effort. A collector that cannot supply `raw_rank` declares it in `CollectorCapabilities` and its items are excluded from every prevalence statement. |
| 2 | **Frozen reference stratum** | `research/allocation.py` + `intelligence/trends.py` | Every denominator and every trend test uses `strategy ∈ {brand, category}` only. Every brand-comparison uses `strategy = category` only (strategy `brand` is brand-conditioned, so the subject's share inside any theme is inflated there by construction). The A/B query set is a versioned artefact (`reference_query_set_version`, D-45), re-executed verbatim each run; a bump resets `replication_status` and suppresses cross-run growth for one run. Strategy `competitor`/`theme`/`hypothesis` items populate the Content Explorer, support findings as evidence and contribute to cluster formation — and **never enter a denominator or a trend test**. |
| 3 | **Composition, never volume** | `intelligence/trends.py`, `claims/guard.py` | §5.1. `n_corpus` includes noise points (excluding them lets the clustering parameter rescale every share) and excludes items that failed analysis, failed embedding or lack a usable date (including them lets a per-period pipeline-failure rate rescale every share). |
| 4 | **Admission-time concentration caps** | `research/iteration.py` | No single query may contribute >25% and no single creator >10% of an iteration's admitted social items. Overflow is recorded `reject_reason='concentration_cap'`, `cu_charged=0`, and counted into `coverage_gaps`. This bounds the design effect mechanically rather than measuring it after the fact, and directly serves brief §6's "prioritise diversity". |
| 5 | **Brand-share corpus guard** | `research/allocation.py` | Cumulative share of social items whose `first_seen_query_id → strategy = 'brand'` stays ≤ **0.30**, enforced at the **admission gate** (`(brand_admitted+1)/(corpus+1) <= 0.30`), not only as an allocation weight. Brief §8 forbids a corpus dominated by subject-branded content, and a weight cap bounds requested units, not admitted items. |
| 6 | **Design effect everywhere** | `intelligence/independence.py` | Every count threshold in the system is stated on `N_eff`, never `N`. Every proportion interval is Wilson at `n_eff` (equivalently Wilson at `n` with variance inflated by `deff`). Every permutation null is creator-block, platform-stratified, over **exact-size exchangeability strata**. `deff_hat = 1 + (m̄−1)·ρ̂`, `ρ̂` estimated by one-way ANOVA pooled across themes, clamped `[0,1]`, `deff_hat` clamped `[1,5]`. |
| 7 | **Diversification probe and `rank_exhaustion`** | `research/stopping.py` | Before `saturation` may fire, ≥1 iteration in the stop window must have executed ≥3 queries carrying a filter envelope not previously used for that stem (deeper `page_depth`, different `sort`, non-overlapping date window), and those queries' own novelty must also be low. A novel probe against a stale main stream diagnoses **`rank_exhaustion`** — "this source's ranked results are used up", whose remedy (go deeper, change source) is the opposite of saturation's (stop). |
| 8 | **Planned temporal coverage** | `research/allocation.py` → `collection_coverage` | `temporal_plan` divides the run's window (default 12 months) into monthly strata with a uniform target (a non-uniform prior would be an assumption about the subject). A stratum under 50% of target attaches `published_after`/`published_before` to a share of the next iteration's `category` queries. A collector that cannot honour a date range **declares it and is not retried**. `temporal_coverage='insufficient'` when >70% of dated items fall in one 30-day bucket, **and that flag blocks every growth and decline claim.** The per-stratum planned-vs-achieved record, including the `published_at IS NULL` share, is in `collection_coverage`. |

### 5.3 Three biases that are disclosed, not corrected

- **Platform ranking.** Recorded per item (mechanism 1) and carried into every prevalence statement.
  `sampling_design.ranking = 'uncorrected'`.
- **Survivorship.** Deleted, privatised and geo-blocked items are invisible to search, so the corpus
  is a sample of surviving, currently-public content. Not correctable.
  `sampling_design.survivorship = 'uncorrected'`, and a standing `survivorship_window` caveat is
  rendered next to every temporal badge.
- **Engagement-ordered recency.** Brand-new items have accrued little engagement, so
  engagement-ordered collection under-represents them. This bias runs opposite to `g` and the two do
  **not** cancel; the newest period is simply uninterpretable, which is why it plus the trailing
  7 days are dropped.

### 5.4 Three falsifications, run every time

Stored on `cluster_runs.exposure_checks`, each recorded as attempted / passed / failed / impossible:

1. **Per-platform sign agreement.** Recompute each theme's series per platform. Disagreeing signs
   across platforms with adequate `N(p)` ⇒ `platform_sign_agreement='disagree'` ⇒ the theme is
   downgraded to `replication_status='exploratory'`.
2. **Per-strategy sign agreement** between the `brand` and `category` strata. Disagreement ⇒
   `refusal_reason='stratum_disagreement'`; a `growing` classification requires sign agreement.
3. **Sentinel exposure curve.** ~10 fixed queries with explicit date windows across each of the last
   12 months, YouTube only (it is the only source with a reliable publication-date filter), when
   `YOUTUBE_API_KEY` exists. Compared against the corpus `N(p)` profile. **We never divide by `ĝ`** —
   dividing headline numbers by a noisy exposure estimate converts estimation noise into apparent
   trends, which is the exact failure being avoided. The curve validates or invalidates the
   common-exposure assumption and nothing more.

Additionally, three artefact guards adopted from doc 07 into the one trend classifier (D-32):
**T4** query-vintage `|kendall_tau| < 0.50` with ≥2 vintages per bucket; **T5** no cumulative
counters in a trend; **T7** direction survives leave-one-independence-unit-out.

### 5.5 The disclosure contract on every read

Every API response that returns a count, a share or a comparison carries:

```python
class SamplingFrame(BaseModel):
    frame: Literal["non_probability"] = "non_probability"
    strategy_mix: dict[str, float]          # share of in-scope units per strategy
    platform_mix: dict[str, float]
    reference_query_set_version: str
    temporal_coverage: Literal["ok", "insufficient"]
    caveats: list[str]                      # 'ranking_bias','survivorship_window',
                                            # 'single_platform','degraded_source',...
```

plus `corpus_denominator`, `denominator_definition`, `n_eff`, and `canon_version` where labels are
involved. `share` is documented end to end as *"the fraction of the units we analysed that evidenced
this"*, never a prevalence estimate. A comparison endpoint returns `frame_comparable = false` with a
reason when two scopes' strategy mixes differ by more than 0.10 total-variation distance, and the UI
may not draw a delta across a non-comparable boundary.

### 5.6 What this costs, stated plainly

At a reference stratum of ~1,500 items over 12 months, `N(p) ≈ 125`, and at `deff ≈ 2` the system
detects a doubling of a theme's share at **34% power** — it misses two such doublings in three.
80% power for a 10%→20% change needs ~4,800 dated reference-stratum items per 12 months. The
consequence is that the Westfall–Young-filtered headline trend cards **will usually be empty**, and
the Overview must therefore be built primarily out of within-period composition facts (which are
assumption-free and always available) with trend verdicts as an occasional addition. That is the
correct behaviour, not a bug to be fixed by loosening a threshold.

---

## 6. The consolidated confidence model

Five layers. Each has a distinct name, a distinct definition, a distinct consumer and an explicit
composition rule. **No layer is ever summed into, multiplied by, or substituted for another except
through the rules in §6.2.** There is exactly one calibrated probability in the system (L1);
everything else is a bounded, monotone, auditable summary and is labelled as such.

### 6.1 The five layers

| Layer | Name | Scope | Definition | Is it a probability? |
|---|---|---|---|---|
| **L1** | `content_analysis_claims.calibrated_confidence` | one analysis claim | `σ(w·f + b)` from a per-field-group L2 logistic on **verified-evidence features** (anchor validity, quote containment, cue non-vacuity, modality count, corroboration, legibility, ASR quality, frame-only flag, duration tier), fitted on adjudicated human labels with `GroupKFold` on `content_item_id`, τ selected on a **validation** split and reported once on an untouched test split at precision ≥ 0.90. | **Yes** — P(an adjudicator marks this claim supported). The only one. |
| **L2** | `labels.evidence_strength` | one canonical label | `0.40·ln(1+support)/ln(26) + 0.25·min(1,creators/8) + 0.15·min(1,platforms/3) + 0.20·min(1,strategies/3) − 0.30·max(0,(top_creator_share−0.5)/0.5) − 0.20·max(0,(top_query_share−0.5)/0.5)`, clamped `[0,1]`. The model's self-assessment is **excluded** and returned separately. | No. A bounded monotone summary of independent support. |
| **L3** | `brands.resolution_quality` | one brand entity | How reliably surfaces resolved to this entity (cascade tier mix, channel mix, ambiguity rate). Floored at 0.50. | No. |
| **L4** | `competitor_relationships.confidence` | one (brand, competitor, relationship_type) | `S^0.50 · V^0.30 · D^0.20 · Q`, where `S = sqrt(Σ_g g²/n_available_groups)` over five correlation groups with within-group `max`; `V = 1−exp(−n_eff/8)` over distinct items; `D` is the item-disjointness walk floored at `1/n_available`; `Q = Q_resolution·Q_temporal·Q_contradiction·Q_sponsored·Q_coverage`. Capped at **0.549** for `VISUAL_ONLY`/`COMMERCE` typing bases. Written only at `>= 0.15`. | No. An epistemic statement about evidence for one typed relationship in this sample. |
| **L5** | `findings.confidence` | one published finding | `base = 0.30·S_size + 0.22·S_div + 0.16·S_effect + 0.12·S_time + 0.10·S_strategy + 0.10·S_item`, then `× S_counter × S_adv × G_type`. Hypotheses get **no score at all**. | No. A publication score with hard gates. |

Two further quantities are **not** confidences and must never be rendered as one:
`commerce_theme_matches.match_score` (an *evidence score* with visible components) and
`clusters.label_confidence` / `replication_status` (display qualifiers).
`content_analysis_claims.model_confidence` is stored for audit and **never displayed anywhere**.

### 6.2 Composition rules — how layers may touch each other

These are the rules the seven docs never wrote down, and they are where double-counting would
otherwise enter.

1. **L1 → L5 only twice.** As the eligibility filter (`claim_status='published'` **and**
   `cue_check='pass'`; `basis='inferred'` claims may support an `interpreted` finding but never an
   `observed` or `calculated` one) and as the `S_item` term at weight 0.10. It is **never**
   multiplied into L4 and never into L2.
2. **L2 → L5 as a pre-filter and a cap, never as a term.** Pre-filter: a candidate whose central
   label has `distinct_creators < 3` cannot clear the `indicative` gate and is rejected before any
   model call. Cap: `labels.holdout_support = 0` means the label was only ever observed when the
   extractor was shown a suggestion — the clearest available self-fulfilment signal — so the finding
   is capped at `indicative` and the card says the label has not been observed without prompting.
3. **L3 → L4 only, as `Q_resolution`.** A relationship cannot be more trustworthy than the entities
   it connects. Floored at 0.50 so a shaky entity degrades a claim without erasing it.
4. **L4 → L5 as a gate and a band ceiling, never re-aggregated.** A finding whose central evidence
   is a competitor relationship may not exceed that relationship's band. Doc 06 XS-10 is binding:
   nothing may re-aggregate L4 into a composite competitor score.
5. **Cluster status → L5 as a hard refusal.** A finding resting on a cluster with
   `status ∈ {unlabelled, ambiguous, review}`, `is_rollup = true`, or `label_status != 'accepted'`
   is refused with `cluster_not_claimable`. A `provisional` (noise-recovered) cluster is permanently
   capped at `weak_signal` and can never carry a direction.
6. **Trend verdict → L5 as an eligibility gate.** A `trend` finding may be published only when a
   `theme_trend_tests` row classifies the theme `growing` or `declining` with `q <= 0.10`; L5 does
   not re-run the test and may not strengthen it.
7. **`match_score` → L5 as an eligibility filter.** Only `tier='supported'` commerce matches are
   citable; the components are rendered in the drill-down.
8. **Nothing ever produces a single number per brand, per competitor or per theme.** Enforced three
   ways: no such column, no such view, and `claims/validators.py::assert_no_composite_score` run on
   every rendered string.

### 6.3 Bands, and the one shared vocabulary

| Band | L4 (relationship) | L5 (finding) | Display rule |
|---|---|---|---|
| `very_strong` | `>= 0.75` | — | L4 only |
| `strong` | `>= 0.55` | `>= 0.70` + all gates | band word + `n_eff/n` + CI, never a number or a bar |
| `moderate` | `>= 0.35` | `0.50 – 0.6999` + gates | normal card |
| `indicative` | — | `0.35 – 0.4999` + gates | `EARLY SIGNAL` chip, excluded from the hero area and from exports |
| `low` | `< 0.35` | — | behind an explicit "weak signals" disclosure |
| `insufficient` | — | `< 0.35` or any gate failed at every band | **not shown as a finding**; the Insufficient-data drawer only |

L5's hard gates (ALL must pass for `strong`): `n_eff >= 20`;
`inv_simpson(independence_unit) >= 8`; `inv_simpson(query) >= 4`; `inv_simpson(platform) >= 2`;
`adversarial_verdict == 'survives'` after two independent referee passes; `ρ < 0.15`;
`finding_kind ∉ {existence, absence}`; `unknown_confidence_share <= 0.40`;
`unknown_independence_share <= 0.30`; hypothesis-strategy weight share `<= 0.70`;
`adjusted_q < 0.10` where a test applies; ≥1 supporting item found by a `disconfirming` query.
`moderate`: `n_eff >= 10`, `inv_simpson(unit) >= 5`, `inv_simpson(query) >= 2`, `q < 0.10`.
`indicative`: `n_eff >= 5`, `inv_simpson(unit) >= 3`, `q < 0.10`.
A finding clearing a band's numeric range but failing its gates **falls to the next band down** and
is re-tested, cascading to `insufficient`.

Every band carries a `*_band_scheme_version`, so a past claim re-reads under the scheme it was
generated with and a recalibration is distinguishable from a real movement.

### 6.4 The one `n_eff`, and the two forms it takes

`aypo/intelligence/independence.py` is the only place either form is implemented.

**The independence unit** (one definition, used by everything):

```
content item         -> creators.id            (or 'unknown_creator' when unresolvable)
commerce item        -> 'seller:' + seller_id
commerce review      -> 'listing:' + commerce_item_id
comment-derived      -> 'thread:' + parent content_item_id
members of a dedupe_group_id share ONE unit, whatever their kind
```

It is **never null**. Unknown-provenance items all share the single `'unknown_creator'` unit and are
capped like any other unit — the alternative (each null its own group) makes unknown provenance read
as maximal independence, which is the dangerous direction.
`unknown_independence_share > 0.30` caps the band at `indicative`.

**Form A — `design_effect(items, blocks) -> float`.** `1 + (m̄−1)·ρ̂`, `ρ̂` by one-way ANOVA,
clamped `[1, 5]`. Used for *interval widening* and *period qualification*: `N_eff(p) = N(p)/deff`,
and every count threshold in trend detection and in the research loop's saturation metrics is stated
on `N_eff`.

**Form B — `effective_sample_size(items, unit_cap=3, query_cap=8) -> EffectiveSample`.** Two-stage
capping: a unit contributes `min(count, 3)`; the unit-capped weights are then distributed across
`discovery_query_id` and each query is capped at 8. Used for *gating a published claim* (L5).

Both forms are reported side by side wherever both apply, and the UI shows `N` and `N_eff` together
— "40 items" and "an effective sample of 20" are different statements and the second is the
operative one.

The brief's own counterexample, worked: 40 items, 3 creators (18/14/8), 1 query, 1 platform.
Form B gives `min(18,3)+min(14,3)+min(8,3) = 9`, then `min(9,8) = 8`. `n_eff = 8`.
`inv_simpson(unit) = 2.74`. `strong` fails (`n_eff >= 20`); `moderate` fails (`n_eff >= 10`);
`indicative` fails (`inv_simpson(unit) >= 3`). The finding cascades to `insufficient` **regardless
of its effect size** and appears only in the drawer, which states: *"40 items, but only 8 effective
observations and 2.74 effective independent sources against the 3 required, with a single query
behind every item."*

### 6.5 Calibration is a release gate, not an optimisation

Every threshold above is an unvalidated prior until the matching experiment runs. Three are
**blocking**:

| Experiment | Owner | Cost | What it gates |
|---|---|---|---|
| Claim-level calibration: 150 stratified videos, double-labelled + adjudicated, grouped three-way split, per-field-group fit | doc 03 §6.6 | ~30–50 annotator-hours | L1. Until it runs, L1 is a rule table clamped at 0.85 with an "uncalibrated" badge, τ = 0.45 / floor 0.30. |
| Label-merge calibration: ≥120 human-anchored pairs per posture group per active language bucket, Clopper–Pearson lower bound ≥ 0.90 | doc 02 §2.10 | ~$8–16 + 10–20 annotator-hours | L2 and the whole taxonomy. Until it runs every bucket carries `uncalibrated_bucket` and the API says the vocabulary is provisional. |
| Threshold calibration job at the end of iteration 0 (pairwise cosine percentiles for query dedup, content novelty, centroid match) + AMI jitter floor by re-clustering under `min_samples ± 1` | doc 01 §3.3, §12.2 | negligible | The composite `saturation` stop. **Fails closed**: while `research_runs.calibration->>'status' <> 'calibrated'` the composite stop is disabled entirely and `saturation_basis='uncalibrated'` is reported. A run must never claim it stopped because it had stopped learning on the strength of numbers nobody measured. |

Two further calibrations are strongly recommended before anyone acts on a borderline verdict:
doc 04 §4.8 (cluster identity thresholds, by 30 subsample re-runs plus a growth simulation) and
doc 06 §3.9.4 (confidence band boundaries, by checking the realised distribution is non-degenerate
and either keeping the fixed cut-offs or moving to fixed quantiles, recording which in
`band_scheme_version`).

---

## 7. Resolved threshold register

Every number an implementer needs, with its single owner. Anything not here is owned by the cited
design doc and unchanged.

### 7.1 Collection and the research loop

| Constant | Value | Owner |
|---|---|---|
| `aypo_max_social_units` / `aypo_max_commerce_units` | 1500 / 2500 | `config.py` |
| `aypo_max_llm_usd` | **180** (D-25) | `config.py` |
| `aypo_max_wallclock_minutes` / `aypo_worker_concurrency` | **300 / 16** (D-26) | `config.py` |
| `aypo_provider_usd_ceiling` | the collection plan's monthly cap (ENVIRONMENT.md: **$5**), global and cross-run, enforced at DISCOVER | `config.py` |
| brand corpus share | ≤ 0.30, enforced at admission | `research/allocation.py` |
| category weight floor | ≥ 0.25, enforced as a step-4 projection constraint (not in the closed form) | `research/allocation.py` |
| exploration floor per open strategy | 0.05 | `research/allocation.py` |
| bandit η / shrinkage `n_0` / `B_s=0` below | 0.6 (0.9 at `advisory`) / 30 / `n_eff < 15` | `research/allocation.py` |
| pacing γ_social / γ_commerce | solved per run, ≤0.82 / 1.15 | `research/allocation.py` |
| contingency reserve | 20% of each cap | `research/allocation.py` |
| per-query / per-creator concentration caps | 25% / 10% of an iteration's admitted social items | `research/iteration.py` |
| query dedup: trgm / jaccard / cosine / hard cosine | 92 / 0.80 / 0.93 / 0.955 | `research/queries/dedupe.py` |
| exploratory-probe cap | 20% of proposed queries and 10% of units at iteration 0; 15% after; 25% in FOCUSING; `min(focus_cap, guard_cap)` under `constrain` | `research/queries/guardrail.py` |
| low-yield suppression | `results_total >= 20 and admitted/total < 0.10` | `research/queries/dedupe.py` |
| `tau_novelty` (content novelty) | 0.86 | `research/saturation/metrics.py` |
| novelty composite / sentinel share | `N = 0.40·NR + 0.60·LN` / 0.08 of social units | `research/saturation/metrics.py` |
| stop conjuncts | sentinel novelty ≤0.15; cluster stability min2 ≥0.75; noise churn max2 ≤0.15; entrants==0 ∧ removals ≤1 ∧ RBO min2 ≥0.90; MEY_rel median3 ≤0.20; G max2 ≤0.50; `n_valid >= 5`; **all five families `OK`**; **`calibration_status == 'calibrated'`** | `research/stopping.py` |
| `valid_reading` | `n_eff >= 15` ∧ `items >= max(40, 0.5·quota)` ∧ not degraded ∧ `analysis_completion_rate >= 0.60` | `research/saturation/view.py` |
| circuit breaker | 5 consecutive failures or error rate ≥0.50 over ≥10 calls in 10 min; open `min(120·2^n, 900)s`; 429/503/529 and timeouts are **not** failures | `research/failure.py` |
| garbage gate | `garbage_rate > 0.30` twice ⇒ quarantine that provider for the run | `research/failure.py` |
| lease TTL / heartbeat | 120 s / 30 s | `research/orchestrator.py` |

### 7.2 Analysis

| Constant | Value | Owner |
|---|---|---|
| model / effort / max_tokens / thinking | `claude-sonnet-5` / `medium` / 32000 / adaptive (D-24, D-34) | `analysis/fusion.py` |
| frame budget by duration | 3 / 6 / 8 / 12 / 16 / 20 (+1 thumbnail, additive) | `analysis/media.py` |
| long edge / square long edge / JPEG q | 960 / 832 / 3 | `analysis/media.py` |
| scene flat threshold | `max(1.0, 3.0 × median)` on the **0–100** scale (D-35) | `analysis/media.py` |
| lead offset / min gap | 0.30 s / `max(0.5, 0.25·duration/N)` | `analysis/media.py` |
| keyframe-only scoring above / single-pass max duration | 180 s / 120 s (CFR only) | `analysis/media.py` |
| ASR skip | `speech_ratio < 0.08` **and** `speech_seconds < 4.0` | `analysis/transcribe.py` |
| ASR low-confidence band | `speech_seconds ∈ [4,8)` or `speech_ratio ∈ [0.08,0.15)` | `analysis/transcribe.py` |
| segment filter | `no_speech_prob > 0.6` **and** `avg_logprob < −0.8`; `compression_ratio > 2.4`; ≥4 identical consecutive | `analysis/transcribe.py` |
| Check B quote containment | pass ≥0.85 / `low_conf` 0.70–0.85 / reject <0.70; exact substring below 12 chars | `analysis/calibration.py` |
| Check C cue non-vacuity | ≥3 content tokens after removing structural hedges and the label's own tokens | `analysis/calibration.py` |
| τ / `unverified_floor` | uncalibrated **0.45 / 0.30**; calibrated per group at precision ≥0.90 / 0.40 | `analysis/calibration.py` |
| calibrator gate | <200 labels in a group ⇒ rule table; 200–500 ⇒ pooled 5-feature fit; >500 ⇒ full 13-feature fit | `analysis/calibration.py` |
| `LIST_CAP` | 6 (12 for brands/products, 4 for unexpected/co-appearance), **stated numerically in the prompt** | `models/analysis.py` |
| repair budget | 1 repair turn, 12% of calls | `analysis/pipeline.py` |
| batch sealing | 200 MB or 500 requests; `custom_id = f"{item.hex}:{fingerprint[:16]}"` | `analysis/fusion.py` |
| cache TTL | 5m interactive / 1h batch, **two breakpoints** (D-16) | `analysis/fusion.py` |

### 7.3 Taxonomy

| Constant | Value |
|---|---|
| merge postures (τ_review / τ_merge / τ_strong) | liberal 0.76/0.84/0.93 · moderate 0.78/0.86/0.93 · conservative 0.80/0.88/0.94 · very conservative 0.82/0.90/0.95 · lexical 0.86/0.94/0.97 |
| uncalibrated bucket adjustment | `τ_merge += 0.02`, `τ_review −= 0.04`; lexical gate mandatory in `mixed` buckets |
| lexical gate | jaccard ≥0.50 **or** trigram ≥0.60 (0.50 where `rich_morphology`) **or** word-boundary containment **or** Levenshtein ≤2 with `max(len) <= 12` |
| anti-merge guard | `inter >= 2` **and** `lift >= 3.0` **and** hypergeometric `p <= 0.01` |
| cross-language link | gloss-only cosine ≥0.88, **mutual** top-10, LLM-confirmed `translation_equivalent` in both orders |
| promotion gate | support ≥8 distinct units · distinct content ≥4 (or commerce ≥8 with `commerce_only`) · creators ≥4 · platforms ≥2 · strategies ≥2 · `top_query_share < 0.60` · span ≥14 days and ≥2 ISO weeks · not a near-duplicate · form valid · no artefact flag |
| weak-signal tier | support ≥4 ∧ distinct content ≥3 ∧ creators ≥2 ∧ `first_pub_date >= run_as_of_date − 21` |
| suggestions | 12/facet, ≤60/call, slots 6 anchor / 3 recent / 3 ε-random, `p_holdout = 0.15`, `vocab_epoch = seq // 50` |
| prompt-echo flag | `suggested_share >= 0.90` ∧ `holdout_support = 0` ∧ `support_count < 20` |
| bias report | rarefied to `n_holdout`, B=200 bootstrap, permutation nulls, BH-FDR `q <= 0.10` over the 6×15 family |

### 7.4 Clustering and trends

| Constant | Value |
|---|---|
| `min_cluster_size` / `min_samples` | `max(8, ceil(0.005n))` / `clamp(mcs/2, 3, 10)` |
| HDBSCAN | `metric='euclidean'` on unit vectors, **`algorithm='brute'` (required)**, `cluster_selection_method='leaf'`, `allow_single_cluster=False`, `store_centers='both'`, `copy=True` |
| `cluster_selection_epsilon` | `clip(quantile(nn_dist, 0.05), 0.10, 0.40)` per space |
| noise repair | `min_samples −1` at most twice, floor 2; still >0.45 ⇒ `high_noise=true` (blocks `established`) |
| oversize cap | `> 0.35n` ⇒ one re-cluster; if it will not split, `unlabelled` + `trend_eligible=false` |
| noise recovery | one pass, `min_cluster_size=5`, `min_samples=3`; results are `provisional`, capped at `weak_signal` |
| soft membership | `tau_c = quantile(cos to centroid, 0.20)`, `w >= 0.15`, ≤3 secondaries per item per space |
| post-merge | centroid `cos >= 0.93` in 1536-d **and** labeller agreement; both must be labelled |
| roll-up cut | average linkage at `cos = 0.80`, i.e. `fcluster(Z, t=0.20, criterion='distance')` |
| reducer selection | maximise `ari_core_adj = ari_core × retained_fraction` subject to `noise <= 0.35` and `8 <= k <= min(150, max(60, n/20))` |
| common-component removal | smallest `k ∈ {0,1,2,3}` passing the invariance probe without costing >0.02 ARI |
| invariance probe | replicate: top-1 ≥0.90, self-cos ≥0.88, margin ≥0.05 · paraphrase: 0.95 / 0.93 / 0.05. Failure ⇒ `status='preview'`, no identities published |
| near-duplicate | `cos >= max(0.97, p99.9)` within 30 days (D-6) |
| identity | `score = 0.6cos + 0.4jac`; continuation ≥0.72 ∧ (`cos >= 0.85` ∨ `jac >= 0.30`); ambiguous `[0.60,0.72)`; birth <0.60; split ≥2 children ≥0.60 holding ≥60%; budding 85/15; merge ≥2 parents each ≥25% |
| series stability | `rho < 0.70` or `mae > 0.03` ⇒ `series_unstable` ⇒ no growth claim |
| qualifying period | `N_eff(p) >= 40`, fully elapsed, ends ≥7 days before collection, in-window, not in a dormancy gap |
| trend gates | see D-32; `|M_d| >= 8` for `emerging`, `>= 15` and `G >= 15` for a slope, `>= 30` for `established`; `max_creator_share <= 0.40`; `undated_fraction <= 0.20`; left-truncation guard = ≥2 prior qualifying periods with ≥60 distinct creator blocks |
| `s_high` | 80th percentile of trend-eligible themes' mean share over `R`; fallback 0.10 with `|M_d| >= 40` when <10 themes |
| window | trailing 12 complete months, extendable to 18, hard-capped at 18 |

### 7.5 Commerce, competitors, findings

| Constant | Value |
|---|---|
| commerce budget | 2500 items/run · 1500/platform · 60/query · 120/theme · 12 themes · 6 queries/theme/platform · 20 reviews/item · 3000 labelled reviews |
| price plausibility | `price_minor ∈ [50, 50_000_000]`; weight `[1, 50_000] g`; `pack_count [1,500]`; implausible ⇒ dropped, never clamped |
| brand attribution | `primary` needs ≥2 corroborating sources; group `brand_id` needs ≥2/3 confidence mass and no rival >1/6 |
| grouping | `0.88 / 0.78` similarity with ±7.5% weight tolerance; average-linkage-with-a-floor cohesion guard |
| theme match | `supported >= 0.60`, `weak >= 0.45`, semantic gate mandatory, ≥2 material components |
| absence | `absent_with_coverage` needs ≥150 screened **groups**, ≥20 negative top-K adjudications, and the design-effect-adjusted prevalence bound |
| co-occurrence | Wilson gate + BH-FDR over the family; orientation-invariant |
| competitor write threshold / bands | 0.15 / 0.35 / 0.55 / 0.75; typing caps **0.549** |
| competitor typing | R1 ≥2 items ∧ ≥2 creators · R2a ≥3 items ∧ ≥2 creators · R2b ≥5 items (cap 0.549) · R3 ≥3 units (R3b cap 0.549) · R4/R5 need `confidence >= 0.35` · 2× dominance rule between R1 and R2 |
| pair enumeration cap | 2000 pairs by raw co-occurrence; `is_subject` pairs never dropped |
| finding bands and gates | §6.3 |
| finding multiplicity | BH `q <= 0.10` within `(run, finding_kind)` |
| finding budget | 45 USD, max candidates and wall clock in `FindingBudget` (D-25) |
| referee | 2 independent passes for `strong`; `min_verdict_confidence 0.50`; ≥2 serious objections ⇒ refuted; 1 narrowing attempt |
| V6 | `support_share_bar = 0.80`; ≥1 contradicting item among the cited ⇒ **quarantine** |
| Ask the Data | `max_tool_turns = 8`; `grounding_ratio >= 0.90`; `min_group_n = 5`; `ask_min_similarity = 0.30`; insufficiency template on `n < 10` / `n_eff < 5` / effective sources `< 3` |

---

## 8. Build order

Dependencies are on **files**, not on ideas: two tracks may run concurrently iff they write disjoint
files and disjoint migrations. The migrations are the main shared artefact, so there is exactly one
file per track (D-43) and nobody edits another track's file; a cross-track column addition goes in a
later-numbered `00NN_<track>_amend.sql`.

### Wave 0 — foundations (sequential, one owner, blocks everything)

| Step | Files | Why first |
|---|---|---|
| **F1** | `config.py` (settings, D-25 budgets, market-pack loader), `models/provenance.py`, `intelligence/periods.py`, `config/market/<code>.yaml` | Everything imports these. The market pack (D-29) must exist before any lexicon or timezone is read. |
| **F2** | `supabase/migrations/0001_core.sql`, `0002_research.sql`; `db/migrate.py`; `jobs/queue.py` amendments (D-42); `jobs/worker.py`; `models/jobs.py` | Every other track enqueues jobs and FKs `research_runs` / `research_results`. The `enqueue()` return-value fix is load-bearing for crash recovery. |
| **F3** | `intelligence/independence.py`, `intelligence/claims/{lexicons,templates,validators}.py` (skeleton + the three `assert_*` functions), `models/findings.py::MetricResult` + `SamplingFrame` | Four tracks produce statistics; they must all import one `n_eff`, one caveat registry and one metric envelope from day one, or they will each grow their own. |

### Wave 1 — four tracks, fully parallel

| Track | Files | Depends on |
|---|---|---|
| **A — Collection** | `collectors/{base,capabilities,relevance,creators,dedupe,tiktok,youtube,instagram,facebook,websearch,commerce_base,shopee,shopee_reviews,lazada}.py`, `providers/{marketplace,serper_commerce}.py`, `models/commerce.py`, `analysis/{commerce_price,commerce_noise}.py`, `jobs/{commerce_collect,commerce_normalise}.py`, `0006_commerce.sql` | F1, F2 |
| **B — Per-item analysis** | `models/analysis.py`, `analysis/{media,retrieval,transcribe,fusion,validators,calibration,sponsorship,idempotency,costing,pipeline,repo}.py`, `llm/prompts/mma_v1.py`, `jobs/analyze_item.py`, `0004_analysis.sql` | F1, F2 |
| **C — Brand resolution** | `models/brand.py`, `intelligence/brand_resolution/*`, `llm/prompts/brand_adjudication.py`, `0003_brands.sql` | F1, F2 |
| **D — Research loop core** | `research/{states,models,budget,allocation,log,repository}.py`, `research/queries/*`, `research/prompts/propose.py` | F1, F2, F3 |

Track A writes `0006` and track B writes `0004`; they never touch each other's tables. Track C's
`0003` is referenced by nothing in wave 1.

### Wave 2 — four tracks, parallel

| Track | Files | Depends on |
|---|---|---|
| **E — Open taxonomy** | `models/labels.py`, `analysis/labels/*`, `llm/prompts/{label_vocab_v1,label_adjudicate_v1,label_define_v1}.py`, `jobs/canonicalise_labels.py`, `0005_taxonomy.sql` | B (`VideoAnalysis`, D-14), C (`brand_name_snapshot`) |
| **F — Embeddings, CAD, clustering** | `analysis/{embed_cache,cad,space,clustering,labeling}.py`, `models/clusters.py`, `intelligence/{identity,themes}.py`, `llm/prompts/cluster_label_v1.py`, `jobs/cluster_and_trend.py` (steps 1–11), `0007_clustering.sql` | B, C (redaction, D-38), E (`LEXICON` glosses) |
| **G — Commerce structure** | `analysis/{commerce_titles,commerce_identity,commerce_reviews}.py`, `llm/prompts/commerce_title_v1.py`, `jobs/{commerce_group,commerce_reviews}.py` | A, C, E (review labels go to `label_observations`, D-18) |
| **H — Orchestrator** | `research/{orchestrator,iteration,failure,stopping}.py`, `research/saturation/*`, `research/prompts/{interpret,synthesize}.py`, `jobs/research_tick.py`, `api/routes/research.py` | D, A, B |

### Wave 3 — three tracks, parallel

| Track | Files | Depends on |
|---|---|---|
| **I — Trends and social comparisons** | `intelligence/trends.py`, `intelligence/claims/guard.py`, `intelligence/comparisons/social.py`, `jobs/cluster_and_trend.py` (steps 12–14) | F |
| **J — Social↔commerce** | `intelligence/{theme_commerce,commerce_cooccurrence}.py`, `intelligence/comparisons/commerce.py`, `llm/prompts/commerce_query_v1.py`, `jobs/commerce_theme_link.py`, `0008_theme_commerce.sql` | F (themes), G |
| **K — Competitor discovery** | `models/competitor.py`, `intelligence/competitors/*`, `intelligence/comparisons/competitive.py`, `llm/prompts/{relation_spans,comparison_classify}.py`, `jobs/competitor_discovery.py`, `0009_competitors.sql` | C, F (S3), G (S5), A |

Tracks I and K both touch `intelligence/claims/`: I writes `guard.py`, K writes only into
`templates.py`'s registry. Assign `templates.py` to one owner (track I) and have K submit entries
through it rather than editing it concurrently.

### Wave 4 — two tracks, parallel; then the UI

| Track | Files | Depends on |
|---|---|---|
| **L — Findings** | `intelligence/findings/*`, `jobs/findings.py`, `api/routes/findings.py`, `0010_findings.sql` | I, J, K, E |
| **N — Read API** | `api/routes/{content,taxonomy,clusters,commerce,brands,competitors,health}.py` | E, F, G, K |
| **M — Ask the Data** | `intelligence/ask/*`, `llm/prompts/ask_v1.py`, `api/routes/ask.py` | L |
| **O — Frontend** | `apps/web/*` (brief §31's seven sections + §32's run view) | N, L |

### Wave 5 — quality, and it is not optional

The three blocking calibrations in §6.5, plus: the bias report (doc 02 §3.5), the acceptance-test
suites (doc 02 §8, doc 04 §10, doc 05 §10.2, doc 06 §7.3), the `deff ∈ {1, 1.5, 2, 3}` sensitivity
report, and the §5.4 falsification wiring. Nothing in waves 0–4 may be shown to a customer before
wave 5's three gates have run, because until then the system has an uncalibrated confidence layer, a
provisional vocabulary and a disabled saturation stop — all three of which it is designed to
**declare**, and the declaration is only honest if someone reads it.

### Critical path

`F1 → F2 → B → C → F → I → L → O`. Eight serialised steps. Tracks A, D, E, G, J, K and N all hang
off that spine and can absorb additional people without additional coordination. The two places
where adding people does **not** help are B (one prompt, one schema, one cost model) and F (one
embedding space, one clustering pipeline, one identity mechanism) — both are single-owner by nature.

---

## 9. Top risks

1. **Collection economics.** `docs/ENVIRONMENT.md` records the Apify account as FREE with
   `maxMonthlyUsageUsd: 5`, every chosen actor on PAY_PER_EVENT, and states outright that the brief
   §6 targets cannot be collected inside $5. Projected collection spend is ~$21/run — 4× a hard
   external cap. **The default profile on this machine is therefore the degraded Serper-only one**,
   with Apify-backed platforms contributing a deliberately small, ceiling-bounded sample. Everything
   downstream that needs `n` — trends, findings, competitor confidence, taxonomy promotion — is
   capped by this, and the guards prevent a wrong number but cannot buy data the plan does not
   cover. This is a procurement decision, not a design one.

2. **Wall clock is the real constraint, and the fix has its own ceiling.** 180 min at concurrency 4
   buys ~617 items (41% of target), which is why D-26 raises them to 300/16. But concurrency 16
   against a free Apify plan, OpenAI embedding and Whisper rate limits, and `apify_max_concurrent_runs = 3`
   (global, not per research run) is untested. The throughput-aware pacer sizes the run down honestly
   and stops at `wall_clock_pacing`, so the failure mode is a smaller corpus with an accurate
   `coverage_gaps` record rather than a crash — but it should be a deliberate configuration choice,
   not a surprise at iteration 4.

3. **Every threshold is a prior, and three calibrations are release gates.** §6.5. The composite
   saturation stop fails closed until calibration runs, which converts a correctness risk into a
   capability gap: in practice the first runs will end on budget, clock or `max_iterations`, and the
   saturation machinery will be unexercised. The taxonomy is provisional and every language bucket
   carries `uncalibrated_bucket`. L1 is a hand-written rule table clamped at 0.85. All three are
   honest, all three are visible in the UI, and all three mean the first customer deliverable rests
   on numbers nobody has measured.

4. **Statistical power is low enough to be a product problem.** 34% for a doubling of theme share at
   `deff ≈ 2`. The Westfall–Young-filtered headline trend cards will usually be empty, and §11.24 of
   doc 04 requires the UI to say so rather than promote a BH-only survivor into the hero slot. The
   honest capability statement — "we detect roughly a doubling of share for themes already at ≥10%,
   about a third of the time" — may not be commercially acceptable, and the fix is ~4,800 dated
   reference-stratum items per 12 months, i.e. roughly double the collection budget.

5. **`creator_id` coverage is unknown and carries every count threshold.** `deff_hat`, every
   permutation block, `max_creator_share`, `inv_simpson(independence_unit)` and both forms of
   `n_eff` depend on it. Apify actor output varies by actor and version. **Missing creator ids look
   like independence**, which is the anti-conservative direction. Mitigations in place:
   `creators.id_basis` records handle-vs-platform-id; `max_creator_share` is `NULL` rather than 0
   when unmeasurable, so an unmeasured guard cannot read as a passed one; all unknown-provenance
   items share one capped unit. None of that substitutes for the field being there.

6. **The 02↔03 prompt coupling is the most fragile integration in the system.** D-14 (required
   English gloss, facet from the array, three new arrays), D-15 (suggestion loop with a measured
   holdout arm) and D-16 (two cache breakpoints, block in `system`, policy fingerprinted but content
   not) all land in one prompt owned by one subsystem and consumed by another. Getting the
   breakpoint wrong is a silent ~8× cost increase on the suggestion block *and* destroys doc 03's own
   2,400-token cache; getting the fingerprint wrong either breaks reproducibility or invalidates the
   cache every epoch. The `usage.cache_read_input_tokens < 1500` alert is the only thing that will
   catch it.

7. **Retention of personal data is a release blocker, not an open question.** The pipeline stores
   frames, audio, transcripts, handles and comment text — images of identifiable people, their voices
   and their words — indefinitely, because every §27 evidence view depends on them, under whatever
   regime governs `research_runs.market_code`. There is no retention period, no deletion job and no
   erasure path. The design's guardrails cover *inference* well (no demographic attribution) and say
   nothing about *storage*. Someone outside engineering must set a retention period, decide whether
   comment author identifiers are stored at all (the prompt payload already omits them — make that a
   stated requirement rather than an accident of the template), and define a takedown path, before
   the first production run.

8. **Pipeline ordering is implicit in every doc and explicit in none.** D-38: brand resolution must
   precede CAD rendering (otherwise undiscovered brands leak into the "brand-free" theme space and
   brand-by-theme becomes a tautology), while competitor signal S3 needs clusters (otherwise it is
   circular). The two-phase split of doc 06's pipeline around clustering is the resolution, and an
   implementer who runs `competitor_discovery` as one stage will produce a design that works and is
   quietly wrong. `brand_leakage_scan` is the detector of last resort, and it is a review queue, not
   a fix.

9. **Two structural changes to doc 05 that an implementer may not notice.** D-4 makes
   `commerce_items` run-independent (identity `unique (platform, platform_item_id)`, per-run facts in
   `research_results`) and D-2 deletes the `commerce_platform` enum. Keeping doc 05's
   `unique (research_run_id, platform, platform_item_id)` silently breaks cross-run commerce theme
   identity and makes `corpus_churn` fire on every commerce theme, which reads as "the market moved"
   when it means "we re-collected".

10. **The budget fits only because the per-item pass is sonnet-5.** D-24. Any operator raising
    `aypo_analysis_model` to `claude-opus-5` takes per-item analysis from $0.0295 to $0.0725
    (both measured), projected Anthropic spend from ~$131 to ~$196, and trips the `drain` guard at
    0.95 × 180 = $171 somewhere around video 1,250 of 1,500 — a `budget_drain` stop at ~83% of the
    brief §6 target that a reasonable operator would mistake for a product bug. The loop's obligation
    (doc 01 §7.1) is to **project before it spends**: at PLANNING it computes `projected_llm_usd`
    from the resolved analysis model and writes a `budget_threshold` warning naming the shortfall and
    the three levers, before iteration 0.

---

*End of unified architecture. Seven design documents, 92 tables, one decision per conflict.*
