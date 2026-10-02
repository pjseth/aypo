# 04 — Embeddings, Clustering, Cluster Identity & Statistically Honest Trend Detection

> Subsystem design. Covers brief §21 (content clustering) and §22 (trend detection), plus the
> embedding composition that both depend on. Normative source: `docs/BRIEF.md`.
> Brief §1 and §34 are binding on this document: nothing here names or presumes a consumer,
> occasion, competitor, theme, slang term or cultural association. All examples use
> `Brand A`, `Theme X`, `Cluster Y`.

---

## 0. Scope, ownership, non-goals

### 0.1 What this subsystem does

1. Composes a **canonical analysis document (CAD)** from the structured multimodal analysis and
   embeds it (one or more *views*).
2. Builds **clustering spaces** and runs HDBSCAN to discover thematic groups, with soft
   multi-membership and an explicit noise policy.
3. **Inspects then labels** clusters, in that order, with a prompt that can refuse to label.
4. Maintains **stable cluster identity across runs** (continuation / split / merge / birth /
   death / resurrection) so the UI can honestly say a theme is growing rather than report a
   re-clustering artefact.
5. Produces **theme time series** and a **trend classification** that is defensible under the
   sampling bias inherent to point-in-time collection, together with a `claim_guard` that
   refuses claims the data cannot support.

### 0.2 What it does not do

- It does not collect, retrieve, analyse or transcribe content (subsystems 01–03).
- It does not extract brands/products from content; it consumes those labels.
- It does not write findings prose or hypotheses (§25, §27 subsystems). It emits machine-checked
  `Claim` objects; the narrative layer may only paraphrase them.
- It does not own the job queue; it registers one job type.

### 0.3 Owned tables

`content_embeddings`, `cluster_runs`, `clusters`, `cluster_revisions`, `cluster_lineage`,
`cluster_members`, `theme_timeseries`, `theme_trend_tests`, `cluster_links`.

### 0.4 Owned modules

```
aypo/analysis/cad.py            canonical analysis document rendering
aypo/analysis/embedding.py      provider abstraction + batching + cost accounting
aypo/analysis/space.py          centring, PC removal, reduction, fingerprinting
aypo/analysis/clustering.py     HDBSCAN config, soft membership, noise recovery
aypo/analysis/labeling.py       member sampling, labelling prompt, verification
aypo/intelligence/identity.py   cross-run cluster identity + lineage
aypo/intelligence/trends.py     series construction, permutation inference, classification
aypo/intelligence/claim_guard.py  the only place a temporal claim may be minted
aypo/jobs/cluster_and_trend.py  the single idempotent job
aypo/models/clusters.py         pydantic/row models
```

### 0.5 Library reality check (verified against the installed environment)

`scikit-learn 1.9.1`, `scipy 1.18.1`, `numpy 2.5.3`, `pandas` present.
**`hdbscan` (contrib), `umap-learn` and `statsmodels` are NOT installed**; `umap-learn` is an
optional extra in `pyproject.toml`.

Consequences this design is built around, rather than against:

| Wanted | Available in `sklearn.cluster.HDBSCAN` | Design response |
|---|---|---|
| hard labels, membership strength | `labels_`, `probabilities_` | used |
| cluster centres | `store_centers="both"` → `centroids_`, `medoids_` | used |
| `all_points_membership_vectors` (soft clustering) | **no** | soft membership via a per-cluster adaptive centroid rule (§2.6) |
| `exemplars_` | **no** | exemplars := *k* nearest core members to the medoid |
| `condensed_tree_` (hierarchy) | **no** | roll-up hierarchy built by average-linkage agglomeration over cluster centroids (§2.7) |
| `relative_validity_` (DBCV proxy) | **no** | bootstrap ARI is the primary selection criterion; a simplified DBCV is computed in-house as a tiebreaker (§2.4) |
| logistic regression, sandwich SE | no `statsmodels` | 2-parameter **Firth-penalised** IRLS + creator-block permutation in ~80 lines of numpy (§5.6) |
| reproducible neighbour graph | `algorithm` defaults to `"auto"` | **pinned to `"brute"` (§2.1)** — `auto`/`kd_tree`/`ball_tree` disagree with the exact distance ordering |
| `max_cluster_size` under `leaf` | **silently ignored** (sklearn docstring: *"Has no effect if `cluster_selection_method='leaf'`"*) | enforced post hoc (§2.5) |

Not adding `hdbscan` or `statsmodels` is deliberate: the soft-membership and inference
mechanisms designed below must work for *incrementally arriving points* and for *per-creator
dependence*, which neither library gives us for free anyway.

---

## 1. What gets embedded: the Canonical Analysis Document

### 1.1 The decision

**We embed a deterministic, field-ordered, label-list rendering of the structured analysis
(brief §20) — not the caption, not the transcript, not the LLM's prose `summary`.**

Four *views* are rendered from the same analysis record. Each is embedded separately and
becomes its own vector space.

| View | Used for | Contains brand tokens? |
|---|---|---|
| `theme_v1` | primary clustering & theme identity | **no** |
| `facet_situation_v1`, `facet_expression_v1`, `facet_language_v1`, `facet_product_v1` | faceted clustering → multi-membership (§2.3) | **no** |
| `retrieval_v1` | semantic search in Content Explorer (§31.5), Ask the Data retrieval | **yes** |
| `commerce_theme_v1` | commerce clustering | **no** |

### 1.2 Why brands are excluded from every clustering view

This is the single most consequential composition choice. The product's core comparative
claim form (brief §24) is *"within the analysed sample, Brand A appears more frequently in
Theme X than the subject brand"*. If brand surface forms are inside the vector that defines
Theme X, then clusters partially **are** brand clusters, and that sentence degenerates into a
tautology: Brand A is over-represented in a cluster that Brand A's tokens helped create.

Excluding brand tokens makes brand-by-theme a *measurement over an independently derived
partition*. Brand presence is then a statistic computed on `cluster_members`, never an input.

Concretely excluded from clustering views: `visible_brands`, `competitors`, brand names
appearing inside product strings (replaced with the generic product type), creator handles,
hashtags containing brand tokens, and the raw caption (which is dense with brand tokens).
`retrieval_v1` deliberately keeps them, because a user typing "Brand A unboxing" needs them.

**The redaction applies to `QUOTE` as well, and this is not optional.** `QUOTE` (§1.3.9) is a
verbatim span of transcript, caption or OCR, so it is exactly as capable of carrying a brand
token as the caption we excluded two sentences ago. Every known brand surface form is replaced
with the literal token `<brand>` before the span is rendered; the analysis pass is additionally
required to *prefer* a representative span containing no brand surface form, and to emit none
rather than a branded one if no clean span exists (§11.2). An omitted `QUOTE` costs the vector a
little lexical grounding; a branded `QUOTE` costs the entire argument of this section.

*Residual, and not fully solvable:* redaction can only remove brands the analysis pass has
already named. A brand that has not yet been discovered is, by definition, not on the list, and
its surface form can sit inside `LEXICON`, `CULTURE` or a redacted-but-missed `QUOTE`. We cannot
fix this by enumerating brands in advance — brief §1 forbids exactly that — so we **detect** it
instead, without assuming what the brands are. After clustering, `brand_leakage_scan()` takes
every token and bigram appearing in members' `QUOTE`/`LEXICON`/`CULTURE` lines, tests each for
over-representation in one cluster against the corpus baseline (Fisher, BH `q ≤ 0.10`), and
flags any hit that is **not** already a known brand surface form as
`clusters.leakage_candidates`. That list is a review queue for a human, never an automatic
action: it answers "is this cluster secretly a brand cluster?" with evidence, which is the most
brief §1 permits.

### 1.3 The `theme_v1` template — exact field order and formatting

```
FORMAT: {content_format}
SITUATION: {occasions} | {social_context}
BEHAVIOUR: {behaviors} | {activities} | {product_usage}
CREATOR: {creator_archetype}
CULTURE: {cultural_signals}
HUMOUR: {humor_signals}
LEXICON: {slang_term} ({gloss}); {slang_term} ({gloss})
LANGUAGE: {languages}; code_switching={true|false}
PRODUCT: {product_types} | pairings={product_pairings} | prominence={product_prominence}
STANCE: sentiment={sentiment}; purchase_intent={purchase_intent}
UNEXPECTED: {unexpected_observations}
QUOTE: "{verbatim span, <= 30 words}"
```

Rendering rules (all deterministic — same record in, byte-identical text out):

1. **Lines are omitted entirely when the field is empty.** We never emit `FORMAT: none`. A
   literal `none` token is content the embedder attends to, and it makes items cluster by their
   *pattern of missing fields*. Omission shortens the document instead, and document length is
   handled by common-component removal (§1.6).
2. **No connectives, no sentences.** Values are `;`-separated inside a group and `|`-separated
   between groups. Stylistic variance in text lives mostly in function words and syntax; a label
   list has almost none. This is the primary defence against style clustering.
3. **Values are normalised**: NFKC, lower-cased, emoji and private-use code points stripped,
   internal whitespace collapsed, trailing punctuation removed, duplicates removed
   case-insensitively.
4. **Deterministic intra-field ordering**: sort by `(-conf_bucket, value)` where
   `conf_bucket = round(confidence / 0.02)` — i.e. confidence is quantised to a 0.02 grid, not
   rounded to 2 decimal places. The analysis model's own emission order is noise; quantising
   stops a 0.0001 wobble from permuting the list and changing the vector. It does **not** make
   the rendering insensitive to every perturbation below 0.02: a perturbation that crosses a
   bucket edge, or the 0.5 gate in (5), changes the document, and that is the correct behaviour.
   §10.2's property test is stated accordingly.
5. **Confidence gate**: a value is dropped if its stated confidence `< 0.5`. At the boundary
   (`== 0.5`) it is **kept**. Rationale: below 0.5 the model's own assessment makes the label a
   coin flip, and the clustering algorithm cannot distinguish injected noise from signal. The
   number of dropped values is stored as `cad_dropped_low_confidence` so a systematic
   confidence collapse in the analysis prompt is visible rather than silent.
6. **Confidences are never rendered as text.** Numerals tokenise poorly and add a shared
   numeric direction across all documents.
7. **Per-line token caps** (truncation drops **whole values** from the tail of the sorted list
   until the line fits — never a partial token, because a half-word is a new token the embedder
   attends to; `QUOTE`, which is a single value, is the one exception and is cut at a word
   boundary with `…`. Truncated field names are recorded in `CadDocument.truncated_fields`):
   `SITUATION` 60, `BEHAVIOUR` 70, `CULTURE` 50, `LEXICON` 40,
   `PRODUCT` 50, `UNEXPECTED` 60, `QUOTE` 40, others 25. Caps stop one verbose field from
   dominating the vector; the rank ordering of (5) means the cap drops the least confident
   values first.
8. **`LEXICON` keeps original-language terms verbatim** with a short English gloss. The corpus
   is code-switched (brief §18). `text-embedding-3-large` is multilingual but weaker on
   lower-resource languages, so the English gloss carries the semantics into a well-covered
   region of the space while the verbatim term supplies the lexical anchor that makes two items
   using the same local phrase near-neighbours. *Known residual risk*: a theme that exists only
   in local phrasing with no good gloss will be under-clustered. The null-label control (§3.6)
   and the field-ablation audit (§1.7) will surface it but cannot fix it.
9. **`QUOTE` is a verbatim span of source text** (transcript, caption or OCR), chosen by the
   analysis pass as the most representative ≤30-word span — *not model prose* — and
   **brand-redacted** (§1.2). It gives the vector a small amount of genuine lexical grounding. It
   is capped at 40 tokens (~10% of the document) precisely because creator style, not model
   style, is what it imports. If brand redaction would consume more than a third of the span,
   the line is omitted rather than rendered as a row of `<brand>` placeholders.
10. **The LLM `summary` field is NOT embedded in any clustering view.** It is the single largest
    style-collapse risk (§1.5). It appears in `retrieval_v1` only, where style homogeneity is
    harmless because retrieval is query-anchored.

Typical rendered length: ~320–420 tokens.

### 1.4 Why this composition rather than the caption or the transcript

- **The caption is recommender-facing copy**, not description: hashtag stacks, emoji runs,
  CTAs, trending-sound credits, cross-posting boilerplate. Embedding it yields clustering by
  caption convention and hashtag co-occurrence — i.e. a slightly fancier bag-of-hashtags. That
  is exactly the "traditional social listening" behaviour the brief's opening paragraph and §18
  reject.
- **The transcript is dominated by speech register, duration and language.** Items cluster by
  "long monologue in language L" vs "short voice-over". Code-switching makes lexical overlap an
  unreliable similarity signal. Worst of all, the transcript routinely fails to mention what is
  visually happening, and the product's entire premise (§18) is that the meaning is in the
  video, not the words.
- **The structured analysis is the fused multimodal representation** (§18–§20). Embedding its
  canonical rendering makes the vector space a space of *analysed meaning*: visual behaviour,
  setting and format sit in the same vector as language and culture, weighted by field caps we
  control rather than by how talkative the creator was.
- **Auditability.** Every token in the embedded text traces to a named analysis field with a
  recorded confidence. When a cluster looks odd, we can diff member CADs field by field and show
  a marketer *why* these items are together. A transcript embedding cannot be explained.
- **Determinism and caching.** `content_hash = sha256(view || template_version || text)` is the
  embedding cache key. Re-running a research run re-embeds nothing unless the analysis or the
  template changed.

### 1.5 The style-clustering risk, stated honestly

Every CAD is produced from output of one model, with one prompt, at one effort level. The
failure mode is real: **the dominant variance direction in a corpus of same-model text is often
the model's register**, not the content. If that direction survives into the clustering space,
HDBSCAN will partition by verbosity, hedging density and field-completeness, and the labeller —
reading those same documents — will happily invent a plausible theme for the partition. The
output would be confident, coherent, and about nothing.

Four mitigations, in order of strength:

**(a) Composition.** Label lists with no connectives (§1.3.2), caps per field (§1.3.7), no
`summary` (§1.3.10), bounded verbatim quote (§1.3.9). This removes most of the surface where
register lives.

**(b) Common-component removal.** Before reduction, centre the corpus and remove the top `k`
principal components:

```
X_c   = X - mean(X)                       # X is L2-normalised, n x 1536
U,S,V = svd(X_c, full_matrices=False)
X_d   = X_c - X_c @ V[:k].T @ V[:k]       # strip the top-k directions
X_hat = X_d / ||X_d||                     # re-normalise
```

`k` is chosen as the **smallest** `k ∈ {0,1,2,3}` that passes the style probe below without
reducing bootstrap ARI (§2.4) by more than 0.02.

*The search order matters, because `k`, the probe and the reducer harness are each defined in
terms of the other two.* It is fixed, and it is not a loop:

1. Fit the **default** reducer (`pca64`) for each `k ∈ {0,1,2,3}` and run the probe in each
   resulting space. Let `k*` be the smallest passing `k`, or `k*=3` if none passes.
2. With `k*` **held fixed**, run the §2.4 reducer harness over the candidate list.
3. Re-run the probe once in the winning candidate's space. If it now fails, fall back to the
   next-ranked candidate and repeat at most twice; if nothing passes, the run is `'preview'`.

Steps 1 and 3 cost no API calls — the paraphrase and replicate documents are generated and
embedded once per `(cad_template_version, analysis_prompt_version)` and cached, and the probe
itself is a cosine computation in whichever space is being tested. Only the *generation* is a
per-template cost (§9.3). PC1 of same-generator text is reliably a
common/boilerplate-plus-length direction; PC2 is frequently register or language. `k` is capped
at 3 because beyond that we start removing genuine topical axes, and the recorded `k` with its
explained-variance share goes into `cluster_runs.space_config`.

**(c) The invariance probe — a hard acceptance gate, not a vibe check.**

The first version of this design probed the space with an *LLM paraphrase* of the rendered CAD.
That probe measures the wrong invariance. The CAD is rendered **deterministically** from the
analysis record (§1.3), so paraphrase variation is a perturbation production never applies. The
variation that production actually exhibits is upstream: the analysis pass emitting a different
*set of values*, a different count of them, or different confidences for the same content. A
space can be perfectly paraphrase-invariant and still cluster by how many fields the analysis
pass happened to fill in. So the gate has two parts, and the first one is primary.

**(c1) Analysis-replicate probe (primary).** For a seeded random sample of `N = 150` analysed
items, ask subsystem 03 to re-run its analysis pass independently (same model, same prompt
version, independent sample) and render every clustering view deterministically from each
replicate record. This is the real production perturbation. Require, in the clustering space
under test:

- `P(nearest neighbour of item i is its own replicate) ≥ 0.90`
- `mean cos(item, own replicate) ≥ 0.88`
- `mean cos(item, own replicate) − mean cos(item, nearest other item) ≥ 0.05`

**(c2) Paraphrase probe (secondary).** The original probe, retained because the CAD does contain
two free-text subfields the analysis pass writes in prose — the `LEXICON` gloss and `QUOTE`. For
`N = 200` items a `claude-opus-5` call at `effort="low"` returns a *perturbed structured record*
(same values, same confidences, re-worded gloss, a different qualifying quote span) rather than
a paraphrased document, so all views re-render deterministically from it and the probe can be
evaluated per space. Require `top-1 ≥ 0.95`, `mean self-cos ≥ 0.93`, `margin ≥ 0.05`.

**The candidate set is part of the test and is specified, because it changes the bar by an order
of magnitude.** For item `i`, the nearest neighbour is taken over
`{all probe base vectors except i} ∪ {all probe variant vectors except i's own}` ∪ `i's own
variant` — i.e. 2N−1 candidates. The same rates against the **full corpus** are computed and
stored as `top1_rate_corpus` / `top1_rate_probe`; the gate is on the probe set, the corpus rate
is reported because it is the number a reader will assume we measured.

Thresholds for (c1) are looser than (c2) by design: an analysis replicate genuinely differs in
content, not only in wording, so demanding 0.95/0.93 there would reject every space, including
correct ones. 0.90/0.88 is the point at which replicate-to-replicate distance is still clearly
smaller than item-to-item distance.

If **either** probe fails, the space is **rejected**: the run proceeds with
`cluster_runs.status='preview'`, no cluster identities are published, and the failure is
surfaced as an engineering alert. At the boundary (a rate exactly at its threshold) the space
passes but `style_probe_marginal=true` is recorded and growth claims are downgraded to
`exploratory` for that run.

Cost and cadence: (c2) is 200 calls × (~500 in + ~400 out) at `effort="low"` ≈ **$2.51**, once
per `cad_template_version`. (c1) is 150 re-analyses billed against subsystem 03's own analysis
cost, once per `analysis_prompt_version` — at the analysis subsystem's stated per-item cost this
is the largest one-off line in the pipeline and is priced in §9.3. Both sets of vectors are
cached, so re-evaluating the probe in a new candidate space costs nothing. **This probe is a
hard dependency on subsystem 03 exposing a replicate mode (§11.12); if it does not, (c1) records
`unavailable`, (c2) alone gates, and every run carries `style_probe_partial=true`.**

**(d) Field-ablation audit, per cluster.** After clustering, fit a multinomial logistic from
one-hot field features (`has_FORMAT`, top value per field, token count bucket) to cluster
labels and compute permutation importance per feature. If a cluster's assignment is best
explained by `token_count_bucket` or by `QUOTE` features, it is **demoted to
`status='unlabelled'`** with `demotion_reason='style_artefact'` and excluded from trend
classification. This catches the per-cluster version of the failure that (b) and (c) only catch
globally.

### 1.6 Facet views (short templates)

Each facet view is a strict subset of `theme_v1` lines, rendered with identical rules:

| View | Lines |
|---|---|
| `facet_situation_v1` | `SITUATION`, `BEHAVIOUR` |
| `facet_expression_v1` | `FORMAT`, `CREATOR`, `HUMOUR` |
| `facet_language_v1` | `LEXICON`, `LANGUAGE`, `CULTURE` |
| `facet_product_v1` | `PRODUCT`, `STANCE` |

An item with no lines for a facet gets **no vector for that facet** and is simply absent from
that facet's clustering (recorded in `cluster_runs.n_items_embedded`). This is correct: an item
with no situational signal should not be forced into a situational theme.

Default enabled facets: `global`, `situation`, `expression`.
`language_culture` and `product_context` are behind `aypo_cluster_facets` config, because each
extra facet multiplies the labelling cost and the number of themes a human must read (§9.3).

### 1.7 `retrieval_v1` and `commerce_theme_v1`

`retrieval_v1` = `theme_v1` ∪ `BRANDS:` ∪ `SUMMARY:` (the LLM summary, ≤60 tokens) ∪
`CAPTION:` (≤80 tokens, hashtags preserved) ∪ `TRANSCRIPT:` (first ≤180 tokens). ~700 tokens.
Style homogeneity is acceptable here because retrieval scores against a user query, not against
other documents.

`commerce_theme_v1` (brand-free, from the commerce analysis record):

```
PRODUCT_TYPE: {product_type}
ATTRIBUTES: {flavour_or_variant_descriptors} | {pack_format} | {size_descriptors}
CLAIMS: {listing_claims}
USE_CONTEXT: {occasions_or_uses_stated_in_listing_copy}
REVIEW_THEMES: {recurring_review_topics}
PRICE_BAND: {quantile_band_within_corpus}
QUOTE: "{verbatim review or title span, <= 30 words}"
```

`PRICE_BAND` is a **within-corpus quintile label**, not an absolute price, so the vector is not
dominated by a numeral and remains comparable if currency or pack sizes shift.

**Social and commerce are clustered in separate spaces.** Jointly clustering them would make
"is this a listing or a video" the dominant axis — listing copy and video behaviour read
completely differently — and every cluster would be modality-pure anyway, just with worse
density estimates. They are linked afterwards by cross-space centroid similarity in
`cluster_links` (§7.9), which the social-to-commerce subsystem consumes as an *association*,
never as evidence of causation (brief §17).

### 1.8 Embedding provider, dimensions, dedup, cost

- `text-embedding-3-large`, `dimensions=1536` (pgvector HNSW caps at 2000; `aypo_embedding_dimensions`
  already defaults to 1536). Vectors are L2-normalised on write.
- Batch 128 texts/request, 4 concurrent requests, `tenacity` retry; token count estimated with
  `len(text)//4` for budgeting and reconciled against the API's reported usage into the
  `Ledger` as `Spend(kind="embedding", model=..., units=n_docs)`.
- **`content_hash` is a cache key, not an identity.** Two *different* items can render to
  byte-identical CADs — sparse analysis records make this routine, and it is precisely the
  signal §1.8's dedup step exists to act on. So `content_hash` must **not** carry a unique
  constraint across items: the hash is indexed for lookup, the per-item uniqueness lives on
  `(item, view, template_version, model)`, and identical CADs are resolved by the dedup rule
  below, not by an insert failure (§7.2).
- **Provider fallback is an identity epoch break.** Jina vectors are not comparable with OpenAI
  vectors. If the provider changes, `identity_epoch` changes, all cluster matching is refused
  across the boundary, and lineage records `relation='epoch_break'` so the UI explains a
  discontinuity instead of rendering a mass extinction. Never mix providers inside one space —
  partially-embedded corpora are handled by §9.2, not by silently swapping providers.
- **Near-duplicate collapse before clustering**: pairs with `cos ≥ 0.98` in the 1536-d identity
  space form a `dedup_group`; the earliest publication date is the representative and only the
  representative enters clustering and all denominators. 0.98 is the prior default; the actual
  threshold is set at the **99.9th percentile of the sampled pairwise cosine distribution** per
  run, because CAD rendering compresses the cosine range and a fixed constant will be wrong on a
  narrow corpus. Boundary: pairs in `[0.95, threshold)` stay separate but are flagged
  `near_dup_candidate=true` and capped at 2 per theme-period cell in trend tests (§5.7).
  Rationale: re-uploads and template-identical content otherwise masquerade as independent
  evidence of a theme, which is the cheapest possible way to manufacture a trend.

**Embedding cost at scale** (~1500 videos + ~2500 commerce records per run):

| View | Docs | ~tok/doc | Tokens |
|---|---|---|---|
| `theme_v1` | 1500 | 380 | 0.57M |
| `retrieval_v1` | 1500 | 700 | 1.05M |
| 3 default facets | 1500 | 240 total | 0.36M |
| `commerce_theme_v1` | 2500 | 220 | 0.55M |
| **total** | | | **~2.53M** |

At $0.13/Mtok → **≈ $0.33 per full run**, and near zero on re-runs thanks to the content-hash
cache. Embeddings are not a cost lever; labelling (§9.3) is.

---

## 2. Clustering

### 2.1 Metric: cosine, implemented as Euclidean on unit vectors

`sklearn.cluster.HDBSCAN` does not support a cosine metric on the fast tree paths. We do not
need it to. For L2-normalised `a, b`:

```
||a - b||^2 = 2 - 2*cos(a, b)        =>    cos = 1 - d^2/2
```

Euclidean distance on unit vectors is a strictly monotone transform of cosine distance. Mutual
reachability, the minimum spanning tree and the condensed hierarchy depend only on the
*ordering* of pairwise distances, so under `cluster_selection_method="leaf"` the clustering is
**identical** to cosine HDBSCAN. (Under `"eom"` it would not be exactly identical: excess of
mass integrates `λ = 1/d`, which is not invariant under a monotone reparameterisation of
distance. We do not use `eom` — §2.5 — so this is a footnote, but it is one more reason the
choice is the right one.)

**`algorithm` must be pinned to `"brute"`, and this is load-bearing.** Measured on
sklearn 1.9.1: with `algorithm="auto"` (which selects a tree), `metric="euclidean"` on unit
vectors gives **ARI 0.969** against `metric="precomputed"` on the cosine matrix — *not* 1.0 —
even though the two distance vectors are rank-identical (0 order violations in 114,960 pairs).
`algorithm="kd_tree"` and `"ball_tree"` reproduce the same 0.969. Only `algorithm="brute"`
returns labels identical to the cosine clustering. The discrepancy is the tree paths'
tie-breaking, not the metric — but the consequence is that the equivalence argument above, and
§2.9's determinism guarantee, are both false unless the algorithm is pinned. Measured cost of
pinning at n=4000, d=64: 0.25 s and ≈0.4 GB additional peak RSS, which is inside the §9.3
budget.

Therefore: always L2-normalise, always `metric="euclidean"`, always `algorithm="brute"`, and
convert thresholds with the identity above. `metric="precomputed"` is still avoided — `"brute"`
gets the same answer while letting sklearn chunk the distance computation.

### 2.2 Dimensionality reduction: PCA for structure, UMAP for pictures only

**The problem is real.** At d=1536 and n≈1500–4000, k-NN distances concentrate: the ratio
between the farthest and nearest neighbour distance approaches 1, so HDBSCAN's core distances
become nearly constant, the condensed hierarchy flattens, and the outcome is either one blob or
near-total noise. Running HDBSCAN on raw 1536-d embeddings is not a serious option.

**Why not UMAP as the default reducer**, despite it being the popular choice:

1. **UMAP manufactures apparent structure.** Run on uniform noise it produces crisp, convincing
   islands. A product whose core promise is evidence-first intelligence (§3, §28) cannot have a
   step in its pipeline whose characteristic failure mode is *inventing clusters*.
2. **It breaks density semantics.** Plain UMAP does not preserve density, so HDBSCAN's density
   model is being applied to a manifold where "dense" no longer means what HDBSCAN assumes.
3. **It is stochastic and non-reproducible in practice.** Even with a fixed `random_state`,
   numba parallel reductions are not bit-reproducible across machines, so the same corpus can
   yield different clusters — and therefore different *findings* — on two servers.
4. **`transform()` for new points is approximate and drifts.** Our corpus grows every run.
5. **It is an optional dependency** that pulls numba (the heaviest package in the project);
   `pyproject.toml` already declares the PCA fallback as the intended behaviour.

**Why PCA is enough here.** PCA is an orthogonal projection: it is a contraction that preserves
pairwise distances up to the discarded variance, so density semantics survive. It is
deterministic given deterministic SVD, it is *projective* (new points project through the stored
`components_` without refitting), and the stored `mean_`/`components_` make the space
reproducible and auditable. Crucially, the intrinsic dimensionality of a narrow research corpus
(one category, one market) is far below 1536; retaining 64 components typically captures the
great majority of the thematic variance.

**The honest part: we do not actually know which reducer wins on this corpus.** So the choice is
made by a harness, once per corpus version, not by this document.

### 2.3 Spaces built per run

| Space key | Source view | Purpose |
|---|---|---|
| `social.global` | `theme_v1` | headline themes, the primary partition |
| `social.situation` | `facet_situation_v1` | facet partition |
| `social.expression` | `facet_expression_v1` | facet partition |
| `social.language_culture` | `facet_language_v1` | optional |
| `social.product_context` | `facet_product_v1` | optional |
| `commerce.global` | `commerce_theme_v1` | commerce themes |

Each space is clustered independently and has its own `cluster_runs` row, its own stable
identities, and its own time series. Facet dimensions are `min(64, floor(n/20))` for `global`
and `min(32, floor(n/20))` for facets (facet documents are shorter and carry less variance;
asking for 64 components from a ~90-token document mostly retains noise).

### 2.4 The reducer-selection harness

Candidates:

| id | definition |
|---|---|
| `none` | centre + remove top-k PCs + renormalise, keep 1536 |
| `pca64` | …then project onto PCs `k+1 … k+64`, renormalise  **(default)** |
| `pca128` | …PCs `k+1 … k+128` |
| `umap15` | UMAP(`n_components=15`, `n_neighbors=30`, `min_dist=0.0`, `metric="cosine"`, `random_state=0`) — **only if `umap-learn` is installed** |

Scored on:

- **`ari_core_adj`** (primary): mean adjusted Rand index over `B=10` bootstrap draws. For each
  draw, sample 90% of points without replacement, refit the whole pipeline (reducer + HDBSCAN)
  on the subsample, and compute ARI against the full clustering restricted to those same points,
  using **only points that are non-noise in both** labelings. ARI with noise as a label is
  reported too (`ari_with_noise`) but not used for selection: a config that declares 60% of
  points noise scores spuriously high on it because the noise label agrees with itself.

  **But restricting to non-noise-in-both makes the evaluation set a function of the candidate,
  which biases the comparison toward whichever candidate declares the most noise** — it is
  scored on a smaller, easier, denser subset. A candidate at 5% noise and one at 34% noise are
  not being graded on the same exam. So the primary objective is the **retention-adjusted**
  score

  ```
  retained_fraction = |points non-noise in both| / |subsample|
  ari_core_adj      = ari_core * retained_fraction
  ```

  with `ari_core`, `retained_fraction` and `ari_with_noise` all stored per candidate in
  `selection_report` so the adjustment is visible rather than buried. Multiplying is the
  conservative choice: it says a partition that is stable on 60% of the corpus and silent about
  the rest is worth 0.6 of a partition that is equally stable on all of it, which is what a
  reader of the resulting themes is actually getting.
- **`noise_fraction`** — constraint, not objective.
- **`n_clusters`** — constraint.
- **`dbcv_simplified`** — tiebreaker only. Computed in-house: for each cluster, density
  separation = min over other clusters of the minimum mutual-reachability distance between
  clusters; density sparseness = max internal mutual-reachability edge in the cluster's MST;
  the validity index is the size-weighted mean of `(sep - sparse) / max(sep, sparse)`. Noted as
  an approximation, not the published DBCV.
- **`label_support_rate`** — fraction of clusters the labeller accepts (§3). Evaluated only for
  the top-2 candidates by `ari_core`, because it costs LLM calls.

**Selection rule**: maximise `ari_core_adj` subject to `noise_fraction ≤ 0.35` **and**
`8 ≤ n_clusters ≤ min(150, max(60, ceil(n/20)))`; ties within 0.02 broken by `dbcv_simplified`,
then by `label_support_rate`, then by the candidate order above (so `pca64` wins coin flips and
UMAP never wins by accident).

*The upper bound on `n_clusters` was `max(40, n/40)` in the first draft and that was a
self-inflicted wound.* `leaf` selection is chosen precisely because it returns many fine
clusters (§2.5); §6.6a assumes "30–80 themes per space per run" and §9.3 budgets for ~40 global
plus ~50 facet clusters. A bound of 40 at n=1500 would therefore have rejected every candidate
on most runs, forcing `status='degraded'`, which caps **every theme in the run** at
`weak_signal`. A constraint whose predictable effect is to silence the whole product is not a
constraint, it is a bug. The bound now tracks the design's own expected counts; "too many themes
for a human to read" is a navigation problem and is answered by the roll-up hierarchy (§2.7),
not by declaring the run degraded.

Boundary behaviour: if **no** candidate satisfies the constraints, use the candidate with the
highest `ari_core` and set `cluster_runs.status='degraded'`; in a degraded run clusters are
created and labelled but **no theme may be classified above `weak_signal`** (§6) and no new
stable identities are published — the run is comparison material, not a source of findings.

If `umap15` wins, it is used for clustering only. **Identity matching always happens in the
1536-d space (§4.2)**, so a UMAP win costs us nothing in identity stability, and
`cluster_runs.space_config.caveat` records that visual cluster crispness in the UI's projection
is not evidence of real separation.

The winner is pinned into `cluster_runs.space_config` (reducer id, `k`, dims, explained
variance, a `fingerprint = sha256(mean_ || components_)`) and **reused for subsequent runs of
the same `identity_epoch`** until the corpus grows by >50% or the CAD template changes, at which
point the harness re-runs. Re-selecting the reducer every run would make between-run cluster
comparison meaningless.

Harness cost: 4 candidates × 10 bootstraps × (fit + cluster) on ≤4000×1536. PCA SVD ≈ 1 s,
HDBSCAN on 4000×64 ≈ 1–2 s → well under 2 minutes of CPU, no GPU, no paid API except the
`label_support_rate` probe (≈$0.6).

### 2.5 HDBSCAN configuration

```python
HdbscanConfig(
    min_cluster_size          = max(8, ceil(0.005 * n)),
    min_samples               = clamp(round(min_cluster_size / 2), 3, 10),
    metric                    = "euclidean",          # on L2-normalised vectors
    algorithm                 = "brute",              # REQUIRED: see 2.1
    cluster_selection_method  = "leaf",
    cluster_selection_epsilon = calibrated,           # see below
    allow_single_cluster      = False,
    store_centers             = "both",
    copy                      = True,                 # sklearn may mutate X in place otherwise
    n_jobs                    = -1,
)
# NOTE: max_cluster_size is NOT passed. sklearn ignores it under leaf selection;
# the size cap is enforced post hoc (see below).
```

**`min_cluster_size = max(8, ceil(0.005·n))`** → 8 at n=1500, 20 at n=4000.
*Why 8*: 8 is the smallest member count at which a cluster can ever support a statistical
statement of the form the product makes. With 8 members, observing a brand in 6 of them against
a corpus baseline of 0.20 gives an exact binomial `P(X ≥ 6 | n=8, p=0.2) = 0.00123`; at 7
members the same relative effect gives `P(X ≥ 5 | n=7, p=0.2) = 0.0047`. Two caveats that the
first draft of this section elided and that bound how far the argument goes: (i) the family for
a brand-by-theme statement is *themes × brands*, not themes — at 60 themes × 5 brands the BH
`q = 0.10` critical value for the top-ranked test is `0.10/300 = 0.00033`, which `0.00123` does
**not** clear, so 8 members is the floor at which such a test is *runnable*, not one at which it
is automatically significant; and (ii) these are i.i.d. binomials, while §5.7 says the items are
creator-clustered, so 6-of-8 from three creators is weaker still. 8 therefore stands as the
floor at which "theme" rather than "anecdote" is defensible, and the gates in §6 — not this
binomial — are what decide whether anything may be said.
*Why also 0.005·n*: at n=4000 a floor of 8 would produce hundreds of micro-clusters that no
human can read (brief §33 warns against dashboard filler). 0.5% keeps the theme count in the
tens.
*Boundary*: a genuinely coherent group of 7 becomes **noise**. It is not lost — it enters the
noise pool (§2.8) and can be recovered as a `provisional` cluster that is permanently capped at
`weak_signal`. Nothing is thrown away; it is just not allowed to be called a theme.

**`min_samples = min_cluster_size/2`, clamped to [3, 10]** → 4 at n=1500.
`min_samples` is the `k` in the core-distance estimate; raising it declares more points noise and
makes the algorithm robust to thin "bridges" of points that otherwise merge two real themes into
one. The brief's guardrails (§28) prefer refusing a claim to manufacturing one, so we err
conservative. *Boundary / adaptive step*: if measured `noise_fraction > 0.45`, decrement
`min_samples` by 1 and re-run, at most **twice**, floor 2. If it is still >0.45 we accept the
result and set `high_noise=true`, which blocks the `established` classification for that run
(a corpus where half the content has no thematic density has not earned the word "established").
The number of adjustments is logged in `cluster_runs.params` — at most two, so this is a bounded
repair, not an optimisation loop that fishes for a pleasing answer.

**`cluster_selection_method="leaf"`, not `"eom"`.**
Excess-of-mass selects the most "persistent" and therefore the largest, most general clusters.
In a single-category corpus that reliably yields a handful of enormous groups whose only honest
label is something as useless as "people showing a product" — precisely the generic filler the
brief rejects. `leaf` selects the finest coherent clusters, which produces specific, labelable
themes. The real cost of `leaf` — fragmentation of one broad theme into near-duplicates — is paid
for twice: by `cluster_selection_epsilon` inside the algorithm, and by the label-aware post-merge
below. Generality is recovered by the roll-up hierarchy (§2.7), which is the right place for it:
the UI can show a parent group while the statistics are computed on well-defined leaves.

**`cluster_selection_epsilon`: calibrated, not constant.**
Default prior `0.25` in a unit-normalised space (≡ `cos = 1 − 0.25²/2 = 0.969`). But cosine
scale is **not transportable between spaces**: PCA projection and renormalisation inflate or
deflate typical cosines, and a 32-d facet space has a very different cosine distribution from a
64-d global space. So the operative value is the **5th percentile of the nearest-neighbour
distance distribution in the actual clustering space**, floored at 0.10 and capped at 0.40:

```
eps = clip(quantile(nn_dist, 0.05), 0.10, 0.40)
```

Every distance/similarity threshold in this document that lives in a clustering space is
calibrated this way, and the realised values are stored in `cluster_runs.space_stats`.
Thresholds that live in the **1536-d identity space** (§4) are *not* recalibrated per run — that
space is fixed by the embedding model, and recalibrating it would make identity thresholds move
underneath the lineage.

**Oversized-cluster cap, enforced post hoc.** A cluster holding more than a third of the corpus
is not a theme, it is the corpus; splitting it is more informative than labelling it. The
obvious implementation — passing `max_cluster_size = ceil(0.35 * n)` — **does nothing**:
sklearn's docstring states *"A limit to the size of clusters returned by the `eom` cluster
selection algorithm… Has no effect if `cluster_selection_method='leaf'`"*, and this was
confirmed empirically (with `leaf` and `max_cluster_size=50`, a 60-member cluster was returned
unchanged). Relying on it would have left a stated safety property that does not exist, in the
one place where its absence produces a corpus-sized "theme" that then gets labelled and can
reach `established`.

So the cap is applied after the fit:

```
for c in clusters where size(c) > ceil(0.35 * n):
    re-run HDBSCAN on c's members alone, with min_cluster_size recomputed on |c|
    if it yields >= 2 clusters:  replace c with them, record split_reason='oversized'
    else:                        keep c, status='unlabelled',
                                 demotion_reason='oversized', trend_eligible=false
```

Exactly one recursion level, so this is a bounded repair and not a search. A cluster that
refuses to split is kept and browsable but may never be labelled or carry a direction — the
honest outcome for a group whose only true description is "a third of everything we collected".

**Post-merge (label-aware).** Two clusters are merged iff
`cos(centroid_i, centroid_j) ≥ 0.93` **in the 1536-d identity space** *and* the labeller, shown
both labels plus 6 members of each, judges them the same theme. Geometry alone is not allowed to
merge, and the label alone is not allowed to merge: two different themes can get similar short
labels, and two phrasings of one theme can sit 0.90 apart. Both clusters must be in the same
space and both must be labelled; an `unlabelled` cluster is never a merge candidate, because
there is nothing to judge.

**Post-merge runs *before* cross-run identity matching, not after, and this ordering is not
negotiable.** The first draft of §9.1 ran identity matching at step 7, wrote lineage at step 8
and labelled at step 9 — which meant a label-aware merge at step 9 silently mutated the cluster
set that step 8 had already written lineage against. Combined with §3.7, which reuses a label
only on a *continuation* and therefore needs identity before labelling, the three steps were
circular. They are resolved by splitting identity into a cheap provisional pass and a single
authoritative one:

```
a. provisional match (geometry + membership only) -- used ONLY to decide which clusters
   can reuse an existing label; never persisted, never written to cluster_lineage
b. label the new and drifted clusters (§3)
c. label-aware post-merge (this rule); oversized-cluster repair has already run
d. AUTHORITATIVE identity match on the post-merge cluster set -> clusters,
   cluster_revisions, cluster_lineage
```

Merges are recorded as lineage at (d) (§4.5), so the UI never silently loses a theme a user saw
yesterday, and the lineage graph describes the cluster set that actually exists.

### 2.6 Multi-membership: the mechanism brief §21 requires

Brief §21: *"Allow content to belong to multiple thematic groups if appropriate."* HDBSCAN is a
hard partition. Two independent mechanisms together satisfy the requirement, and they answer two
genuinely different questions.

**Mechanism A — facet-parallel spaces (primary).**
An item has one core membership *per space*. An item is simultaneously in
`social.global:Cluster Y`, `social.situation:Cluster Y2` and `social.expression:Cluster Y7`.
This is multi-membership that is **structurally meaningful** rather than a fuzzy skirt around
one partition: the groups are about different things by construction, so cross-facet
co-occurrence (does situational theme Y2 travel with expressive theme Y7?) becomes a measurable
contingency table with a Fisher test and BH correction, rather than an embedding artefact. This
is the mechanism that actually serves the brief's list of cluster kinds in §21 — behaviours,
occasions, visual formats, creator styles, language, cultural signals are not one axis, and
forcing them into one partition is what makes single-space clustering vague.

**Mechanism B — soft membership within a space (secondary).**
`sklearn` gives no soft-clustering vectors, and we would not want them anyway: they cannot score
points that were not in the fit. Instead, each cluster carries an **adaptive inlier radius**
derived from its own core members:

```
mu_c   = normalise(mean(E[i] for i in core_members(c)))     # 1536-d identity space
tau_c  = quantile({cos(E[i], mu_c) : i in core_members(c)}, 0.20)
```

`tau_c` is the similarity that 80% of the cluster's own core members exceed. A point `x` gets a
*secondary* membership in cluster `c` iff

```
c is not x's core cluster
cos(x, mu_c) >= tau_c
w(x, c) = (cos(x, mu_c) - tau_c) / (1 - tau_c)      in (0, 1]
w(x, c) >= 0.15
```

capped at the **3 highest-weight secondary memberships** per item per space.

*Why a per-cluster adaptive radius rather than a global threshold*: clusters have genuinely
different tightness. A single global cosine cut-off would hand every point a membership in the
loose clusters and none in the tight ones — the memberships would encode cluster variance, not
item affinity. *Why the 20th percentile*: it makes "as close to this cluster's centre as a
typical member" the admission test, which is the plain-language meaning of belonging.
*Why `w ≥ 0.15`*: below that the point is in the cluster's outer 1% shell and adds noise to
every aggregate. *Why cap at 3*: an item in 8 themes is evidence for nothing, and the cap bounds
the size of `cluster_members`.
*Boundary*: a point exactly at `tau_c` gets `w = 0` and is therefore **excluded** by the
`w ≥ 0.15` rule. Noise points are scored by the same rule and can acquire secondary memberships
without ever acquiring a core one.

**The counting rule that keeps multi-membership honest.** Secondary memberships inflate totals.
Therefore, non-negotiably:

- **Every statistic, time series, trend test, denominator and sample size in this subsystem is
  computed on `membership_role='core'` only.**
- **`size_core` and `size_core_reference` are different numbers and must never be confused.**
  Clusters are *formed* from every item (all five discovery strategies), because a theme should
  be discoverable from whatever found it. But every *statistic* runs on the reference stratum
  (§5.2). So:
  - `size_core` = core members across all strata. This is the browse count, the number of items
    behind the "see the evidence" click, and the number next to a theme in the Content Explorer.
  - `size_core_reference` = core members with `discovery_strategy ∈ ('brand','category')`. This
    is `M` in §6.1 and it is what **every** gate, guard, denominator, power calculation and
    concentration test uses, without exception.
  The UI shows both, labelled, because a user who sees "214 items" next to a theme classified
  `insufficient_data` deserves to be told that only 31 of them are in the stratum the statistics
  are allowed to use. `undated_members` and `max_creator_share` are likewise computed on the
  reference stratum and stored alongside their all-strata counterparts.
- Secondary memberships exist for exploration, retrieval, cross-facet co-occurrence and
  "related content" — never for a count that feeds a claim.
- No UI surface may present per-theme shares as a partition of 100%, because with facets and
  secondary memberships they do not sum to 1. The API returns `share_of_period` with the
  denominator attached, and never a "share of themes" pie (also banned by §33).
- `evidence_count` for a finding is always `count(distinct item_id)`.

### 2.7 Roll-up hierarchy (replacing the unavailable condensed tree)

After clustering, build an average-linkage agglomeration over cluster centroids in the 1536-d
identity space (`scipy.cluster.hierarchy.linkage(method="average", metric="cosine")`) and cut it
at `cos = 0.80` to form **parent groups**. scipy works in *distance*, so the cut is
`fcluster(Z, t=1.0 - 0.80, criterion="distance")`; passing `0.80` straight to `fcluster` cuts at
cosine 0.20 and merges nearly every theme into one parent, which is a silent and very
plausible implementation error. Parents get their own `clusters` rows with
`is_rollup=true`, are labelled by the same pipeline (members = union of child core members,
sampled), and carry `parent_cluster_id` on their children. Parents are deliberately *not*
eligible for trend classification: their member sets change whenever a child is created or
dissolved, which would make their series an artefact of their children's lineage. They exist for
navigation (§31.2 filtering) only. This is stated in `clusters.trend_eligible = false`.

### 2.8 Noise policy

Typical noise fraction with this configuration: 0.20–0.35. Noise is **not discarded**, for three
reasons: the corpus denominator must include it (§5.5), the brief's "unusual signals" card (§31.1)
is partly built from it, and a user browsing the Content Explorer must never find an item that
the system claims not to know about.

1. **Recorded.** Noise points get a `cluster_members` row with `cluster_id IS NULL`,
   `membership_role='noise'`, their `probabilities_` value and their outlier score. The run's
   `noise_fraction` is a first-class, UI-visible number.
   **Each item holds exactly one of `core` or `noise` per run** — enforced by a partial unique
   index (§7.7), not by convention. A point recovered into a provisional cluster at step 3
   below has its `noise` row *replaced* by a `core` row; it is never both. Two noise fractions
   are therefore stored and they are not the same number: `noise_fraction_primary` from the
   first HDBSCAN pass (the one the §2.4 constraint and `high_noise` are evaluated against) and
   `noise_fraction` after recovery (the one the UI shows). Conflating them would have made
   §10.13's `n_core + n_noise == n_items_clustered` invariant unsatisfiable, since recovery
   moves points from one side of it to the other.
2. **Scored for secondary membership** by the §2.6 rule — a noise point near a cluster's inlier
   radius is still shown as related content.
3. **Noise recovery, second pass.** Re-run HDBSCAN *on the noise points alone* with
   `min_cluster_size=5`, `min_samples=3`, same space. Groups found here were suppressed by the
   global density threshold (HDBSCAN's single density level is its best-known limitation); they
   become clusters with `provisional=true`.
   *Why 5*: the smallest size where a 4/5 or 5/5 concordance is worth a human look.
   **Provisional clusters are permanently capped at `weak_signal`** (§6) — they can never be
   called growing, declining or established, and findings built on them must carry the
   provisional flag. Exactly one recovery pass; recovered noise is not recursively re-mined.
4. **Atypicality flag.** Compute a GLOSH-style outlier score per point — since sklearn does not
   expose one, use `outlier_score = 1 − probabilities_` for clustered points and, for noise,
   `1 − max_c w(x, c)` from the §2.6 rule, where the maximum over an empty set is **0** (a noise
   point that clears no cluster's inlier radius scores 1.0, the most atypical value, which is
   the correct reading). Points above the 0.90 quantile are
   `is_atypical=true`. These feed "unusual signals" and are labelled in the UI as **outliers,
   not trends** — an outlier is one item, and the product's guardrails (§28) forbid dressing one
   item as a pattern.

### 2.9 Determinism and reproducibility

`cluster_runs` stores: `space_config.fingerprint`, HDBSCAN params, `numpy` and `sklearn`
versions, the sort seed used for random member sampling (`seed = crc32(cluster_key)` so sampling
is reproducible per cluster rather than per process), and the ordered list of input `item_id`s'
hash.

**`cluster_key` is derived, not random**, because it is the reproducibility anchor for member
sampling and therefore for every label:

```
cluster_key = f"{space_key}.{sha256(sorted(member_item_ids of the FIRST revision))[:8]}"
```

It is minted once, at birth, and never changes for the life of the stable identity — including
through label changes, growth and resurrection — so sampling is reproducible across runs rather
than merely within one. Collisions are resolved by appending `-1`, `-2`.

**The determinism guarantee, stated at the strength it actually holds:**

- *Same machine, same `lib_versions`, same inputs, same fingerprint* ⇒ byte-identical labels,
  centroids and lineage decisions. This holds only because `algorithm="brute"` is pinned
  (§2.1); with sklearn's default `"auto"` it does not, and the first draft claimed it anyway.
- *Across machines* ⇒ ARI ≥ 0.98 against the reference labelling, not equality. LAPACK SVD sign
  conventions and float32 tie-breaking differ by BLAS build, and promising bit-identity across
  machines is a promise we cannot keep.
- Component signs are canonicalised (`svd_flip`: the largest-magnitude entry of each component
  is forced positive) **before** the fingerprint is taken. Without this a BLAS change silently
  changes `fingerprint`, which would invalidate the pinned reducer and trigger a spurious
  re-selection — a re-cluster disguised as a configuration check.
- A **reducer refit** (corpus growth > 50%, or a template change) moves cluster boundaries. The
  identity space is unaffected (§4.2), but member sets are not, so a refit run should be
  expected to set `series_unstable` on many themes and therefore to publish few or no
  growth claims. `cluster_runs.reducer_refit=true` records it so this reads as a known
  consequence rather than an anomaly.

---

## 3. Labelling — cluster, inspect, *then* label

Brief §21 fixes the order and §21 repeats it: **"Do not name clusters before analysing their
members."** The pipeline enforces the order structurally: `label_cluster()` takes a
`MemberSample` and has no access to the embedding, the centroid, the query that collected the
items, the research hypotheses, or any cluster from a previous run. It cannot name a cluster
before it has the members because the members are its only input.

### 3.1 Member sampling for inspection

For a cluster with `m` core members:

| stratum | count | selection |
|---|---|---|
| `centroid` | `min(6, m)` | 6 nearest to the **medoid** by cosine |
| `periphery` | `min(4, …)` | 4 largest cosine distance to centroid among core members |
| `random` | `min(5, …)` | uniform without replacement from the remainder, `seed = crc32(cluster_key)` |

Total ≤ 15; if `m ≤ 15`, all members are shown and the strata collapse.
A disjoint **holdout** of up to 8 further members (same strata proportions, different seed
offset) is reserved for verification (§3.5). If `m ≤ 15` the holdout is empty and verification
is skipped with `verification='not_possible'` recorded.

*Why 6 near the centre*: fewer than ~5 and one atypical item dominates the impression; more than
~8 and the label gets over-fitted to the core, missing what the cluster actually spans.
*Why 4 from the periphery*: the periphery is where the label either holds or breaks. Labelling
from the centroid alone reliably produces labels that are true of 60% of the cluster and
quietly false of the rest — and it is the rest that a marketer will click on first.
*Why 5 random*: the centroid+periphery pair is a biased sample by construction; the random
stratum is what makes the "≥60% of inspected members" support test (§3.4) meaningful.
*Why 15 total*: 15 × ~380 CAD tokens ≈ 5.7k input tokens per cluster — large enough to see the
cluster, small enough that labelling stays a single-digit-dollar line item (§9.3).
*Medoid rather than centroid for the near stratum*: the medoid is an actual member, so "nearest
to the medoid" returns real items; nearest-to-centroid can be dominated by one dense pocket.

The sample passed to the model is **exactly the embedded text** (`theme_v1` or the facet view) —
already brand-free by construction — plus an opaque `item_ref` (`m01`…`m15`) for citation. It
is **not** given: engagement metrics, platform, creator, publication date, brand fields, the
discovery query, or the cluster's size. Each of those is a route to a label that is about
popularity, platform convention or the subject brand rather than about content, and each of them
is something we later want to *measure* against the cluster.

### 3.2 Output schema

```python
class ClusterLabel(BaseModel):
    label_supported: bool
    label: str | None                  # <= 6 words, descriptive, extractive
    description: str | None            # 1-2 sentences, <= 50 words
    shared_attributes: list[SharedAttribute]   # 2-5
    supporting_refs: list[str]         # item_refs that exhibit ALL shared_attributes
    dissenting_refs: list[str]         # inspected items the label does NOT describe
    unsupported_reason: Literal["incoherent","multiple_themes","too_generic_to_be_useful"] | None
    split_recommended: bool
    split_sketch: list[str] | None     # <= 3 candidate sub-theme descriptions
    confidence: Literal["low","medium","high"]

class SharedAttribute(BaseModel):
    field: str          # the CAD field name the attribute comes from
    attribute: str      # verbatim or near-verbatim value as it appears in members
    refs: list[str]     # >= 2 item_refs where it literally appears
```

Requiring `field` + `refs` per attribute is the mechanism that makes invention expensive: the
model must point at the line of the member document where the attribute appears, and a
post-check verifies by string containment that the cited `attribute` really occurs in the cited
member's CAD (fuzzy-matched at `rapidfuzz` partial ratio ≥ 85 to tolerate inflection). Attributes
failing that check are dropped; if fewer than 2 survive, the label is rejected.

**No number that gates a label may come from the model.** `support_rate` and `dissent_rate`
decide whether a label is accepted (§3.4.3–4), so taking them from `supporting_refs` /
`dissenting_refs` as emitted would let the model grade its own exam. Every ref list is therefore
normalised in code before it is used:

1. **Ref validation.** Any ref not in this sample's `m01…m15` is dropped and counted in
   `invalid_refs`. A hallucinated `m17` must not inflate a denominator. More than 2 invalid refs
   rejects the label outright (`label_status='invalid_refs'`) — a model inventing item ids is
   not a model whose attribute citations can be trusted either.
2. **Disjointness.** `supporting_refs ∩ dissenting_refs` must be empty; a non-empty intersection
   rejects the label (`label_status='inconsistent_refs'`) rather than being silently resolved.
3. **Support is recomputed, not accepted.** `supporting_refs` is *defined* as the items
   exhibiting **all** surviving `shared_attributes`, and that is checkable: an item counts as
   supporting only if every accepted attribute passes the same `rapidfuzz ≥ 85` containment test
   against that item's CAD. `support_rate = |recomputed supporting| / n_inspected`. The model's
   own list is kept as `model_claimed_supporting_refs` and the gap between the two is logged as
   `support_overclaim` — a batch with a consistently large gap is as diagnostic as the null
   control (§3.6).
4. `dissent_rate = |valid dissenting_refs| / n_inspected`, and the two rates are *not* assumed
   to be complements (§3.4.4 checks both).

### 3.3 The labelling prompt skeleton

System prompt (cached — identical across all clusters, so it bills at the 0.1× cache-read rate):

```
You are labelling a cluster of consumer content items that an unsupervised algorithm
grouped together. You did not choose the grouping and you cannot change it.

Each item is given as a structured analysis document with named fields. This is the
ONLY evidence you have. You have no other knowledge of these items, this category,
this market or this brand, and you must not supply any.

YOUR TASK
1. Read all items before writing anything.
2. Identify attributes that RECUR ACROSS ITEMS. For each, cite the field it comes
   from and at least two items where it literally appears.
3. Decide whether a single short label describes the cluster.
4. Only then write the label.

HARD RULES
- Every shared attribute must be present in the item documents. If you cannot cite
  two items by reference id, the attribute does not exist. Do not infer it.
- The label must describe what the items ARE, using words grounded in the documents.
  Do not describe what they MEAN, IMPLY, or what the audience FEELS.
- Do not name any brand, company, product line, celebrity, platform or place unless
  that exact token appears in the item documents.
- Do not invent or assume demographics (age, gender, income, class, region,
  occupation, household role). Nothing in these documents establishes who anyone is.
- Do not use: trend, viral, emerging, growing, popular, Gen Z, aesthetic (as a noun),
  vibe, core, era, or any superlative. You cannot observe popularity or change from a
  cluster; another part of the system measures that.
- Do not state or imply a cause.
- The label is at most 6 words and contains no punctuation beyond hyphens.

WHEN TO REFUSE
Set label_supported=false if any of these is true:
- the items do not share 2 or more citable attributes  -> "incoherent"
- the items split into 2 or more clearly different groups -> "multiple_themes"
  (also set split_recommended=true and sketch the groups)
- the only honest label would be so broad it describes most consumer content,
  e.g. a label that is just the category or just "people using a product"
  -> "too_generic_to_be_useful"

Refusing is a correct and expected outcome. A wrong label is far more damaging than
a missing one, because a human reader will trust it and build a decision on it.

List in dissenting_refs every inspected item your label does not describe. If more
than 40% of inspected items are dissenting, set label_supported=false.
```

User content: `[CLUSTER <opaque id>]` then `m01 … m15`, each item as its CAD text verbatim.

Call: `LLMClient.structured(ClusterLabel, system=LABEL_SYSTEM, content=…, effort="high",
max_tokens=8000, cache_system=True, label="cluster_label")`. `max_tokens=8000`, not 4000:
`claude-opus-5` runs adaptive thinking by default and those tokens count against the ceiling, so
4000 risks truncating the structured response at `effort="high"` (§3.5).

### 3.4 Acceptance checks applied to the returned label (in code, not by the model)

A label is accepted iff **all** hold:

1. `label_supported is True`.
2. `len(shared_attributes) ≥ 2` after the containment check (§3.2).
3. `len(supporting_refs) / n_inspected ≥ 0.60`. *Why 60%*: a label true of fewer than three
   fifths of the items a human just looked at will not survive the first click-through in the
   Content Explorer, which destroys trust in every other number on the page. It is a deliberately
   low bar — we are checking for "mostly right", not purity.
4. `len(dissenting_refs) / n_inspected ≤ 0.40` (the complement; both are checked because the
   model can return sets that do not partition).
5. `label` passes a banned-token regex (the §3.3 forbidden list, plus the run's configured
   subject-brand and seed-competitor strings from `aypo_competitor_seeds` — a seeded brand name
   must never become a *cluster label*, only a measured attribute).
6. The cluster passed the §1.5(d) field-ablation audit.

Failure ⇒ `clusters.status='unlabelled'`, `label_status` records which check failed. Unlabelled
clusters still exist, still have members, still appear in the Content Explorer as
"Cluster Y (unlabelled)", and are **excluded from trend classification and from findings** —
a theme we cannot describe is not a theme we may make claims about.

### 3.5 Verification on held-out members

Second call, `effort="medium"`, different system prompt, given the accepted label +
`description` + the **holdout** members only (never the inspected ones):

```
Below is a label produced from a different sample of the same cluster, and a new set
of items from that cluster that were NOT used to produce it.

For each item, answer only: does the label accurately describe this item? yes / no / partly.
Then list the items for which the answer is no.

Do not defend the label and do not improve it. If it does not fit, say so. A label
that fits the items it was written from but not these items is a bad label, and
saying so is the purpose of this step.
```

`no_rate` is **computed in code from `per_ref`**, never read from the model's own `no_rate`
field (which exists in the schema only so a mismatch can be logged as a calibration signal).
`per_ref` keys are validated against the holdout's ref set first; a ref the model did not answer
for counts as `partly`, and an unknown key is dropped and counted.

If `no_rate > 0.40` on the holdout: demote to `status='unlabelled'`,
`label_status='failed_verification'`. If `0.25 < no_rate ≤ 0.40`: keep the label but set
`label_confidence='low'`, which the UI renders as a caveat and which bars the theme from the
Overview's headline cards. *Why 0.40 again*: the same bar as §3.4.3, applied out of sample, so a
label cannot pass by over-fitting the inspection sample.

**Call hygiene.** Both labelling calls run on `claude-opus-5` with adaptive thinking on by
default, so thinking tokens count against `max_tokens`. `max_tokens=4000` is too tight to be
safe at `effort="high"`: set `max_tokens=8000` and treat `stop_reason == "max_tokens"` as
`label_status='truncated'` — an unlabelled cluster — rather than letting a truncated structured
response surface as a parse error or, worse, a partially-parsed label.

### 3.6 The null-label control

Each labelling batch includes **one synthetic cluster** of 15 items drawn uniformly at random
**from the whole corpus** — not from the noise pool — presented indistinguishably from a real
cluster. The correct answer is `label_supported=false`, with
`unsupported_reason ∈ {"incoherent", "too_generic_to_be_useful"}`.

*The first draft drew the control from noise points and justified it by claiming noise points
"are by construction the least mutually similar items in the corpus". That is not what HDBSCAN
noise is.* Noise is points in low-**density** regions; a noise set routinely contains several
small, genuinely coherent groups — which is the entire premise of the recovery pass in §2.8.3,
two sections earlier. A control drawn from noise can therefore be legitimately labelable, and
the test would fire on a correct labeller. A uniform random draw from the corpus is the right
negative control for the question being asked: *given an arbitrary grouping of this category's
content, will the labeller refuse?* Items that noise recovery placed in a provisional cluster
are excluded from the draw, so the control cannot accidentally be a real theme.

Failure criterion: **any** `label_supported=true` on the control fails. The first draft allowed
a `confidence="low"` label to pass, but a fabricated label at low confidence is still a
fabricated label and still reaches the UI. A `true` at `confidence != "low"` flags the batch
`calibration_suspect=true` and holds the run for review; a `true` at `confidence == "low"`
flags `calibration_marginal=true` and downgrades every label in that batch to
`label_confidence='low'`.

Honest limit: at ~4 batches per run this is **one Bernoulli trial per batch**. It is a tripwire,
not an estimate of the false-label rate, and must not be reported as one; a single pass means
very little, a pattern of failures across runs means a great deal. Cost: 1 extra call per batch,
≈$0.15 per run. It remains the cheapest continuous test of the thing most likely to silently
break.

### 3.7 Re-labelling policy across runs

Labels belong to the **stable** cluster (§4), not to the run. On a continuation match, the
existing label is **kept** unless one of:

- member-set Jaccard against the previous revision `< 0.60` (the cluster is no longer the same
  set of things), or
- `size_core` changed by more than 2× in either direction, or
- the cluster was involved in a split or merge, or
- `cad_template_version` or `identity_epoch` changed.

Otherwise the label is reused and no LLM call is made. This is both a cost control and an
honesty control: a theme whose name rewords itself every week reads as instability to a user and
makes the week-to-week comparison they are trying to do impossible. Every label change is
appended to `clusters.label_history` with the run id and reason, so the rename is auditable.

---

## 4. Cluster identity across runs

### 4.1 The problem, precisely

HDBSCAN label integers are an artefact of the MST construction order. Between runs the corpus
grows, the reducer may be refit, and densities shift; the same conceptual theme can be
`label 7` then `label 23`, can absorb a neighbour, can shed a sub-group, or can disappear
entirely because its members no longer pass the density threshold. Any UI statement of the form
"Theme X is growing" that is built on raw labels is a statement about the clustering algorithm,
not about the world. This section exists so that the UI can distinguish *the theme got bigger*
from *the clusterer drew the line somewhere else this time*.

### 4.2 Identity lives in the 1536-d embedding space, not the clustering space

**Centroids used for matching are computed in the full L2-normalised 1536-d embedding space**,
as the normalised mean of the cluster's core members' item vectors. They are *not* computed in
the PCA/UMAP clustering space, because that space is refit and is therefore not comparable
between runs — matching there would compare coordinates in two different bases and produce
nonsense that looks like numbers.

Identity matching is **refused across an `identity_epoch` boundary**. The epoch key is

```
identity_epoch = sha256(embedding_provider || embedding_model || dims || cad_template_version || view)[:16]
```

On an epoch change every cluster is reborn with `birth_kind='epoch_break'` and lineage edges
`relation='epoch_break'` to the best-matching predecessor (recorded for navigation, explicitly
**not** treated as continuation). The UI shows a visible discontinuity marker and all series
restart. This is the honest outcome: we changed the measuring instrument, so we cannot claim
continuity through the change. A `theme_timeseries` recomputation across the break is allowed
(§5.4) but the theme's `first_run_id` resets and `replication_status` resets to `unreplicated`.

### 4.3 The matching score

For each new run cluster `i` and each previously-active stable cluster `j` in the same space and
epoch:

```
cos_ij = cosine(centroid_1536_i, centroid_1536_j)

               |members(i) ∩ members(j)|
jac_ij  = ----------------------------------------      over the INTERSECTION of the two corpora
          |members(i) ∪ members(j)| restricted to items present in BOTH runs

score_ij = 0.6 * cos_ij + 0.4 * jac_ij
```

The Jaccard is computed only over items that existed in **both** corpora. Without that
restriction, a theme that doubles in size by acquiring new items would show a collapsing Jaccard
purely because the corpus grew — i.e. growth, the thing we are trying to measure, would look
like a loss of identity. Items dropped from the corpus (deleted content, dedup changes) are
excluded from both sides.

*Why 0.6/0.4*: member overlap is the stronger evidence of sameness but is structurally
attenuated by corpus growth, which is the expected condition of this system; centroid cosine is
growth-invariant but will happily confuse two genuinely distinct neighbouring themes. Weighting
geometry slightly higher reflects that growth is by design and conflation is caught by the
mutual-best-match and the split/merge rules below. **These weights are a prior, to be calibrated
by the subsample experiment in §4.8.**

### 4.4 Assignment and thresholds

**Precedence is fixed and explicit, because the rules in §4.5 can fire on the same pair that a
one-to-one assignment would call a continuation.** The first draft listed Hungarian assignment
first and split/merge afterwards, leaving undefined what happens when a predecessor is assigned
to one child at 0.80 *and* satisfies the split condition against two children. The resolution:

```
0. detect SPLITS and MERGES from the FULL score matrix (§4.5), before any assignment,
   applying the 85/15 budding exception first so a budding is not mis-read as a split
1. remove every cluster consumed by a split or a merge from both sides of the matrix
2. Hungarian-assign what remains
3. apply the continuation / ambiguous / birth thresholds to the assigned pairs
```

A cluster that qualifies as both a split child and a merge child (it draws ≥60% of one parent
and ≥25% of another) is resolved as a **merge**, since a merge already mints a new id and
records every contributing parent, which is the more complete lineage. The competing split edge
is still written, with `relation='split'` and a `note`, so nothing is lost from the graph.

1. Build the score matrix, solve a maximum-weight bipartite assignment
   (`scipy.optimize.linear_sum_assignment` on `-score`) over the residual. A one-to-one
   assignment prevents the greedy failure where one dominant new cluster claims three
   predecessors.
2. An assigned pair is a **continuation** iff `score ≥ 0.72` **and** (`cos ≥ 0.85` **or**
   `jac ≥ 0.30`). The second condition is a floor test: it blocks a "match" carried entirely by
   one term being in a plausible range while the other is near zero — e.g. `cos = 0.95,
   jac = 0.02` means the centroids are close but the membership turned over completely, which is
   two different things that happen to sit near each other, not one thing persisting.
3. `0.60 ≤ score < 0.72` ⇒ **ambiguous**. The new cluster receives a **new stable id**, with
   `suspected_continuation_of = <old id>` and a lineage edge `relation='suspected_continuation'`,
   status `'review'`. The UI must render it as a new theme with a "possibly related to …" link
   and **must not** plot a continuous history across the gap or make any growth claim.
4. `score < 0.60` against every predecessor ⇒ **birth** (`birth_kind='birth'`).
5. Predecessors left unassigned ⇒ **death handling** (§4.6).

*Why 0.72 / 0.60*: these are the priors implied by what a persisting theme looks like —
`cos ≈ 0.90` with roughly half its overlapping members retained (`jac ≈ 0.45`) gives
`0.6(0.90) + 0.4(0.45) = 0.72`. The ambiguous floor at 0.60 corresponds to roughly
`cos = 0.85, jac = 0.26`: still recognisable, no longer trustworthy. **Both numbers are replaced
by the empirically calibrated values from §4.8 as soon as a real corpus exists**; the design
commitment is the *structure* (mutual best match, a hard band, an explicit ambiguous zone that
suppresses claims), not the specific decimals.
*Boundary behaviour*: a pair at exactly 0.72 is a continuation; at exactly 0.60 it is ambiguous.
Ambiguity is never resolved in favour of continuity, because the cost asymmetry is severe: a
false continuation produces a confident, wrong growth story, while a false birth merely makes
the system look forgetful.

### 4.5 Split and merge

**Split.** A predecessor `p` is split when ≥2 new clusters score ≥ 0.60 against it and, taken
together, they contain ≥ 60% of `p`'s surviving members.

- **All children receive new stable ids.** The parent is closed: `status='split'`,
  `end_run_id` set. Lineage edges `(p → child, relation='split', weight = share of p's
  surviving members in that child)`.
- *Why no child inherits the parent id*: inheritance is the classic way this goes wrong. The
  inheriting child keeps the parent's history while having lost half its members, so its
  recomputed series shows a cliff and the UI reports a **decline that is purely an artefact of
  re-clustering**. Rebirth costs us a continuity marker in the UI; inheritance costs us a false
  finding.
- **Exception (budding, not splitting)**: if one child holds ≥85% of `p`'s surviving members and
  every other child holds <15%, this is treated as `continuation` for the big child plus
  `birth` for the small one(s). *Why 85/15*: below 85% the "continuing" child has lost enough of
  the parent that its history is no longer about the same set of items; above 85% the parent's
  series is substantively unchanged and forcing a rebirth would discard real, usable history.
  At exactly 85/15 the budding rule applies (inclusive).

**Merge.** ≥2 predecessors score ≥ 0.60 against the same new cluster, each contributing ≥25% of
its own surviving members to it.

- The merged cluster receives a **new stable id**, `birth_kind='merge'`; predecessors get
  `status='merged'`. Lineage edges `(parent → child, relation='merge', weight = contributed
  share)`.
- *Why 25%*: below that the "parent" is not merging, it is leaking a few members, and the real
  relationship is continuation-of-the-dominant-parent plus absorption of a fragment.
- The merged theme's series is **recomputed from its own member set** (§5.4), which is legitimate
  — it is not a concatenation of the parents' histories, which would be a lineage artefact. The
  UI shows the parents as predecessors with a join marker and never implies the merged theme's
  history was observed as such.

### 4.6 Death, dormancy and resurrection

An unassigned predecessor is **not** deleted. `status='dormant'`, `last_seen_run_id` set, and the
cause is diagnosed by looking at where its former members went in the new run.

**The denominators differ by row and must be stated, or the four cases do not partition.**
`corpus_churn` is evaluated **first**, against *all* former members; the other three are
evaluated against **surviving** former members only (those still in the corpus snapshot). Within
those three the table order breaks ties, so the rule is total and deterministic:

| condition | `death_kind` | meaning shown in UI |
|---|---|---|
| >50% of former members are now noise | `dissolved` | "no longer forms a distinct group" |
| ≥50% landed in one new cluster but below merge thresholds | `absorbed` (+ lineage edge to that cluster) | "absorbed into …" |
| >50% of former members are no longer in the corpus at all | `corpus_churn` | "its supporting content is no longer in the sample" |
| otherwise | `scattered` | "its content dispersed across several groups" |

`corpus_churn` matters: if deduplication, a relevance-filter change or a collection failure
removed the items, the theme did not decline — **our sample changed**. Reporting that as a
decline would be a straightforward falsehood, and it is the easiest one to ship by accident.

**Resurrection.** A dormant cluster that matches at `score ≥ 0.72` in a later run is
*reactivated under the same stable id*, with `relation='resurrect'` and `gap_runs` recorded. The
series shows the gap explicitly; it is never interpolated across, and the gap periods are not
eligible periods for trend tests.

### 4.7 Series stability — the check that makes "growing" trustworthy

Because `theme_timeseries` is recomputed from the current member set each run (§5.4), a theme's
history can change retroactively even when its identity is stable. A theme can look like it has
been growing for six months, acquire 20 new members next run, and have its past months rewritten.

So every revision computes, over periods present in both the current and previous revision:

```
series_stability_rho = spearman(share_current[p], share_previous[p])
series_stability_mae = mean |share_current[p] - share_previous[p]|
```

A theme's **first** revision has no previous revision to compare against: `series_stability_rho`
and `_mae` are `NULL` and `series_unstable` is `false`. That is deliberate and it is safe, not
an oversight — a first-revision theme is `replication_status='unreplicated'` by construction
(§6.6b), which the UI renders as "first detected this run; not yet replicated". The stability
gate is about *retroactive rewriting*, and nothing has been rewritten yet.

If `rho < 0.70` or `mae > 0.03` (3 share points), set `series_unstable=true`, which **blocks any
growth or decline claim for that theme in that run** (§6, gate 1). The theme is still shown, with
the explicit note that its historical composition changed materially with the new sample.
*Why 0.70*: below that the shape of the history is no longer the same shape, so a slope fitted to
it is fitting this run's sample, not a persistent pattern. *Why 3 points*: at the sample sizes
here (§5.8) a 3-point share shift is of the same order as the effects we are trying to detect, so
a history that moves by that much between runs cannot be used to measure them.

### 4.8 Calibrating the identity thresholds (the experiment that replaces the priors)

Run once on the first real corpus, then whenever the CAD template changes:

1. Cluster the full corpus → reference partition.
2. For `b = 1..30`: draw a 90% subsample, re-run the full pipeline, and match its clusters to
   the reference using the §4.3 score. Ground truth is known: a subsample cluster "is" the
   reference cluster containing the plurality of its members (when that plurality ≥ 50%).
3. Sweep the weight `w ∈ {0.4, 0.5, 0.6, 0.7, 0.8}` on `cos` and the threshold
   `τ ∈ [0.50, 0.90]`. Choose `(w, τ)` maximising matching F1, then set the ambiguous floor at
   the value of `τ` where precision first drops below 0.95 — i.e. the ambiguous band is defined
   as *the score range where we start being wrong more than 5% of the time*, which is a
   defensible definition rather than a chosen decimal.
4. Additionally simulate corpus **growth** (not just subsampling) by holding out the most
   recently collected 20% and adding it back, since growth, not subsampling, is the real
   between-run transformation. Report both; use the growth-simulation thresholds in production
   and keep the subsample numbers as a sanity bound.
5. Persist to `cluster_runs.identity_config` and record the F1/precision achieved, so the UI can
   state the identity-matching accuracy alongside themes if asked.

---

## 5. Trend detection and the sampling-bias trap

This is the most important correctness problem in the subsystem. Everything else in this
document can be slightly wrong and the product is still useful. Get this wrong and the product
confidently tells a marketing team that things are growing when they are not — for every theme,
in the same direction, with no internal signal that anything is amiss.

### 5.1 The mechanism

The corpus is assembled at a single collection time `T`. Write `t` for an item's publication
date. An item enters the corpus only if it is *discoverable*, then *ranked into the top-N of
some query's result page*, then *retained by relevance filtering*:

```
P(in corpus | t) = P(discoverable at T | t) * P(ranked into retrieved top-N | t) * P(retained | t)
                 ≡ g(t)
```

so the corpus's publication-date density is

```
f_corpus(t) = f_world(t) * g(t) / Z
```

and for a theme `θ` the observed per-period count is

```
n_θ(p) = N_world(θ, p) * g(p) * r_θ(p) / Z
```

where `r_θ(p)` is a theme-specific ranking/retention factor.

`g` is strongly increasing toward `T`, for several independent and compounding reasons:

- **Ranker recency bias.** Platform search and recommendation surfaces upweight recent content;
  this is the dominant term.
- **Top-N truncation.** Every collector returns a bounded page/dataset per query. We see the
  head of a recency-skewed ranking, not a sample of the population.
- **Survivorship.** Older content is deleted, set private, demoted, or has its creator's account
  removed. Attrition is monotone in age.
- **Metadata attrition.** Older items more often lack a resolvable publication date and are
  dropped from the series entirely.

**Therefore: counting items per month measures `f_world · g`, not `f_world`.** And because `g`
is increasing, *every* theme shows growth. Quantitatively: if `g` grows at 1.5× per month —
entirely plausible for a search-ranked sample — then a theme with genuinely flat real-world
prevalence shows **+50% per month**, and a theme genuinely declining at −20% per month still
shows **+20% per month**. A naive count-per-month chart over a point-in-time corpus is not a
noisy estimate of a trend; it is a near-deterministic manufacturer of upward trends, and its
output looks exactly like the chart a marketer expects to see, which is why it survives review.

### 5.2 The second trap: query-conditioned oversampling by our own research loop

This one is specific to AYPO's architecture and is, if anything, worse. Brief §8 and §25 require
the agent to generate **theme-level (D) and hypothesis-testing (E) follow-up queries from themes
it has already discovered.** So:

1. Theme X is discovered in iteration 2.
2. The agent generates queries for Theme X.
3. Those queries return content — ranked by recency, like everything else.
4. Theme X now has extra items concentrated in recent periods.
5. Theme X shows growth.

The growth is a measurement of **our own curiosity**, applied through a recency-biased ranker.
Any theme the agent found interesting will appear to be growing, and the more interesting the
agent found it, the more it will appear to be growing. This failure is self-reinforcing with the
hypothesis engine and would be essentially undetectable from the chart.

**Mitigation: the reference stratum.** Brief §8 already requires recording which sampling
strategy generated every item. We use it as a design variable:

- **Reference stratum = items collected by strategy A (brand-level) and B (category-level) only.**
  These queries are *not conditioned on any discovered theme*, so `r_θ(p)` does not depend on
  whether the agent happened to notice `θ`.
- **All primary time series and all trend tests are computed on the reference stratum only.**
- Strategy C/D/E items are **confirmatory evidence**: they populate the Content Explorer, they
  support findings as evidence, they contribute to cluster formation and labelling — but they
  **never enter a denominator or a trend test**.
- For **brand-comparison** statistics within a theme, the stratum narrows further to **B only**:
  strategy A is brand-conditioned, so the subject brand's share inside any theme is inflated by
  construction in the A stratum. This is a hard requirement on the competitive-analysis
  subsystem, and `cluster_members.discovery_strategy` is denormalised specifically so the query
  is a one-line filter.

**The reference stratum must also be frozen across runs, which §5.2 as first drafted did not
require, and without it no between-run statement is defensible.** Removing theme-conditioning
fixes the within-run bias; it does nothing about the *instrument changing between runs*. The
share estimator cancels `g(p)`, the exposure function — it does **not** cancel `r_θ(p)`, the
theme-specific ranking/retention factor, and `r_θ` is a property of *which queries we ran*. If
run 2 adds three new category-level queries, every theme's share moves for a reason that has
nothing to do with the world, and `series_stability`, `replicated` and the whole §6.6b
replication ladder are comparing two different measuring devices.

So: the A/B query set is a **versioned, frozen artefact** (`reference_query_set_version`),
re-executed verbatim each run. Collection may add A/B queries, but doing so bumps the version,
and a version bump is treated exactly like an epoch break for temporal purposes:
`replication_status` resets to `unreplicated`, cross-run growth comparisons are suppressed for
one run, and the UI shows a discontinuity marker. New exploratory queries that the agent wants
to run anyway are strategy C/D/E and never touch the stratum. This is a hard requirement on
subsystem 01 (§11.6b).

*Boundary*: the widening trigger is stated on **qualifying periods**, not on a raw item count.
The stratum widens to A∪B∪C, with `stratum_widened=true` and every resulting growth claim
capped at `replication_status='exploratory'`, iff

```
|{ p in window : N_eff_ref(p) >= 40 }| < 4
```

The first draft used "fewer than 400 dated items across the analysis window" and justified it as
"4 periods × ~100" — but the window is 12 periods, not 4, so 400 dated items is ~33/month and
fails `N_eff(p) ≥ 40` in most of them. The trigger was set *below* the level at which any test can
run, which is the one place it does no good. Stating it on qualifying periods makes the trigger
mean what it was supposed to mean; the raw 400 is retained as an informational floor on the
run's coverage report.
Additionally, when both strata are estimable, the series is computed on both and a `growing`
classification requires **sign agreement** between them; disagreement sets
`refusal_reason='stratum_disagreement'`. This is cheap and it directly tests the failure mode.

### 5.3 The four candidate mitigations, evaluated

**(A) Within-period composition share.** `share_θ(p) = n_θ(p) / N(p)`, where `N(p)` is every
dated, deduplicated, reference-stratum item **that entered the clustering** for this space and
was published in `p` — noise points emphatically included, items that failed analysis or
embedding emphatically excluded. See §7.8 for why both halves of that sentence matter.

```
            N_world(θ,p) * g(p) * r_θ(p) / Z         N_world(θ,p) * r_θ(p)
share_θ(p) = ------------------------------- = ----------------------------------
             Σ_θ' N_world(θ',p) * g(p) * r_θ'(p)/Z   Σ_θ' N_world(θ',p) * r_θ'(p)
```

**`g(p)` cancels exactly.** This is the algebraic heart of the whole section: composition is
identified even when absolute volume is not, provided the exposure function is
theme-independent within a period (the **common-exposure assumption**). Residual risk: if
`r_θ(p)` is theme-dependent and time-varying — a theme the recommender starts favouring — share
change still partly reflects differential ranking rather than prevalence. That residual is
partially testable (§5.9) and is not eliminated.
*Verdict: adopt as the primary estimator.* It is the only option that is both identified under a
stateable assumption and computable from data we actually have.

**(B) Corpus-level exposure/denominator model per period.** Estimate `g(p)` and divide it out to
recover absolute volume.
*Verdict: reject for headline numbers; adopt as an assumption diagnostic.*
To estimate `g` we would need either platform-published totals per period ("how many items in
this category were published in month m") — **no available API provides this, for any of our
sources** — or an independent second sample drawn by a different, non-recency-biased mechanism,
which we do not have. A partial version is feasible: a small fixed set of **sentinel queries**
with explicit date windows. YouTube's Data API supports `publishedAfter`/`publishedBefore`, so
running ~10 fixed sentinel queries across each of the last 12 one-month windows and recording
yield gives a crude relative-exposure curve — **for YouTube only, and only once
`YOUTUBE_API_KEY` exists** (it does not today). Apify TikTok actors generally expose no reliable
publication-date filter, so there is no TikTok equivalent. Dividing headline numbers by a noisy
estimate of `g` with unquantified error would convert estimation noise into apparent trends,
which is the exact failure we are trying to avoid. So the sentinel curve is used only to
**validate or invalidate the common-exposure assumption** and to parameterise (C).
Cost: ~120 YouTube `search.list` calls = 12,000 quota units of the default 10,000/day → needs
either a quota increase or a 2-day spread. Recorded as a known constraint.

**(C) Stratified collection quotas by publication period.**
*Verdict: adopt as a collection contract, with correct expectations about what it buys.*
Ask the collection subsystem for a target of **≥80 dated items per month for each of the last 12
complete months** in the reference stratum. Feasibility is source-dependent: YouTube can be
queried per window directly; TikTok via Apify generally cannot, so stratification there is
*post-hoc* — collect broadly, bucket by publication date, and accept whatever coverage results.
The honest framing matters: stratification **cannot** reconstruct absolute real-world prevalence
(we would need to know `f_world` to weight back to it, and we do not). What it buys is
(i) adequate `N(p)` in older periods so their share estimates have usable confidence intervals,
and (ii) informative *absence* — a theme's 0% in an old period only means something if that
period has enough items for 0% to be surprising (§6.2). Post-hoc downsampling of
over-represented recent periods is **not** done: it discards real data and does not improve
identification of share, which is already per-period conditional.

**(D) Restrict growth claims to between-theme comparisons within one period.**
*Verdict: adopt as the fallback, not as the policy.* "Within period p, Theme X is a larger share
than Theme Y" is bulletproof and assumption-free, but it abandons brief §22's explicit
requirement to classify growing / emerging / declining. It is what the system falls back to for
every theme that fails the §6 gates, and it is what the `claim_guard` emits instead of a
refusal where possible — a weaker true statement beats a blank.

### 5.4 Chosen design

> **Primary estimator: within-period composition share on the reference stratum (A).
> Collection contract: period-stratified quotas (C). Assumption check: sentinel exposure curve
> where available (B). Fallback for everything that fails the gates: within-period between-theme
> comparison (D).**
>
> **Consequence stated once, enforced everywhere: this subsystem never reports a claim about
> volume. Every temporal statement is about composition — "share of analysed content published
> in period p" — and always carries `N(p)`.**

`theme_timeseries` is therefore **recomputed from the current member set over item publication
dates on every run**, not accumulated from per-run observations. This has a large benefit and one
cost. The benefit: history is re-derivable, so a merged or relabelled theme gets a correct
history rather than a stitched one, and improvements to analysis retroactively improve the
series. The cost: history can change retroactively, which is why §4.7's `series_stability` gate
exists and why every row is stamped with `cluster_revision_id` and `computed_at`.

### 5.5 Period construction

- **Bucket by publication date in the run's market timezone** (`settings.aypo_market_timezone`;
  the Philippine deployment configures `Asia/Manila`, UTC+8 — the constant belongs in
  deployment config, not in a module default, because a second market makes a hard-coded zone
  silently wrong). Platform timestamps are UTC; posting behaviour is local.
  *The size of this effect, stated correctly:* bucketing in UTC+8 rather than UTC moves posts
  made between 00:00 and 08:00 **local** into the previous UTC day. For monthly buckets that
  only matters on the month boundary, so it mis-buckets on the order of 1% of items — not, as
  the first draft of this bullet claimed, "an 8-hour slice of every day", and the affected
  window is the local small hours, the posting *trough*, not "each evening", the peak. The fix
  is still right and still free; the justification was overstated by roughly thirty-fold and a
  reader checking it would rightly have doubted the rest of the section.
- **Monthly by default.** Weekly is unusable: at ~1500 items over 12 months, a week holds ~29
  reference-stratum items and a theme-week cell holds 1–3, so every series is noise.
  **Auto-switch to quarterly** when the median `N(p)` over the window is `< 60`; record
  `period_kind` on every row so the UI can never mix them.
  **Switching to quarterly changes two other numbers with it, and in the first draft it changed
  neither — which made the system *readier* to announce a trend exactly where the data was
  thinnest.** (i) The window extends to the 18-month cap automatically, because 12 months is 4
  quarters and dropping the incomplete one leaves 3, below gate 5's `|P_q| ≥ 4`; at 18 months
  there are 6, 5 after the drop. (ii) The effect floor is **per period**, so `OR ≥ 1.25` would
  have meant 1.25 per *quarter* — equivalent to 1.077 per month, a far weaker bar than the
  monthly rule it replaces. The floor is therefore period-normalised: `1.25^3 = 1.95` and
  `0.80^3 = 0.51` for quarterly, so "a trend" means the same real rate of change in both
  regimes. `R` becomes the 2 most recent quarters rather than 3.
- **Window: the trailing 12 complete months**, extendable to 18 if coverage allows, hard-capped
  at 18. *Why cap*: beyond ~18 months survivorship attrition is both large and
  **theme-dependent** — ephemeral content is deleted and demoted faster than evergreen content —
  which breaks the common-exposure assumption that makes share identified at all. An old period
  that survives is not a sample of that period.
- **Drop the current, incomplete period.** Exclude any period not fully elapsed at collection
  time, **and** additionally exclude the trailing 7 days to allow for indexing lag. There is a
  *second, opposite* bias at the very recent edge: brand-new items have accrued little
  engagement, so engagement-ordered collection and engagement-based relevance filtering
  under-represent them. The two biases do not cancel; they just make the newest period
  uninterpretable. *Boundary*: if dropping it leaves fewer than the required number of
  qualifying periods, the theme is classified `insufficient_data` — we do not reinstate the
  partial period to reach a verdict.
- **Items with no publication date** are excluded from the series and counted in
  `undated_members`. If `undated_fraction > 0.20` for a theme, temporal classification is
  refused (`insufficient_data`). *Why 20%*: date missingness is not random — it correlates with
  platform and with age — so above a fifth of the members the series is a sample of the
  well-dated subpopulation, not of the theme.
- **Date precision** is recorded per item (`day`/`month`/`unknown`). Month-precision items are
  assigned to that month; `unknown` is treated as missing.

### 5.6 The statistical test

**Null hypothesis**: theme membership is independent of publication period, conditional on the
realised per-period corpus sizes `N(p)`.

That conditioning is the point. A test that conditions on the observed `N(p)` is **immune to
`g`** under the common-exposure assumption, because `g` only affects `N(p)` — which is held
fixed — and not the conditional composition. This is the same cancellation as §5.3(A), expressed
as an inference procedure rather than an estimator.

**Test statistic — Cochran–Armitage trend in proportions.** For periods `p` with calendar index
`t_p` (actual calendar offsets, so gaps from non-qualifying periods are respected rather than
silently closed):

```
p_bar = Σ_p n_θ(p) / Σ_p N(p)
T     = Σ_p t_p * ( n_θ(p) - N(p) * p_bar )
```

**Null distribution — creator-block permutation, stratified by platform.** Items are not
independent: one creator posts many items, a template or sound produces near-identical items,
and a single collection batch arrives together. Treating items as i.i.d. inflates significance
by the design effect, typically 1.5–3×. So:

1. Group reference-stratum items into blocks by `creator_id` (fallback `discovery_query_id` when
   creator is unknown, which is a weaker proxy and is recorded as `block_kind='query'`).
2. The null is exchangeability of **the theme-membership indicator across blocks**: a block's
   membership vector is reassigned to another block.
   **This requires blocks of *exactly* equal size, not "the same size class".** The first draft
   permuted within size classes `{1, 2–3, 4–7, 8+}`, which is not a well-defined operation — a
   length-2 indicator vector cannot be assigned to a 3-item block — and it is also what
   §8.7's `PermutationPlan.permutations` (an `(R, n)` index matrix) silently assumes away, since
   an index permutation can only encode a block swap between equal-size blocks. Every p-value in
   this subsystem rested on a procedure that could not be implemented as written.
   The corrected scheme: **exact-size exchangeability strata**. Blocks are grouped by
   `(platform, exact block size)`; a permutation draws an independent random permutation of the
   blocks *within each stratum* and composes them into one item-level index map. Blocks that are
   alone in their stratum are fixed points — they contribute no randomisation, which shrinks the
   permutation space but keeps the test exact. `n_exchangeable_blocks` (blocks in strata of size
   ≥ 2) is stored, and the §5.7 `G ≥ 15` guard is stated on it rather than on the raw block
   count, because a fixed point is not an exchangeable unit.
3. **Platform is an exchangeability stratum, not merely a diagnostic**, and the Cochran–Armitage
   statistic is summed over platform strata: `T = Σ_platform T_platform`, each computed on that
   platform's own `n_θ(p)` and `N(p)`. This is the Mantel-style extension and it removes the
   Simpson's-paradox confound by construction rather than detecting it afterwards. §5.3C is
   explicit that only YouTube can be date-stratified, so older periods are structurally
   YouTube-heavy and recent periods TikTok-heavy — meaning the `platform_mix_shift` guard of
   §5.7 would have fired on nearly every run, downgrading nearly every claim to `exploratory`.
   Detecting a confound you have built into the collection design is strictly worse than
   conditioning on it. Platforms with fewer than 2 qualifying periods are dropped from the
   stratified statistic and the dropped share of the corpus is reported;
   `platform_mix_shift` survives as a *diagnostic* only (§5.7).
4. `R = 10,000` permutations, defined **once per run and shared across all themes**, so one
   permuted membership matrix serves every theme and the whole thing is a few matrix products.
   The plan stores the **seed and the stratum structure, not the materialised index matrix**: an
   `(R, n)` int32 array at R=10,000 and n=4000 is 160 MB held for the entire run, which
   contradicts the "chunked" claim it was offered alongside. Chunks of 1,000 permutations are
   regenerated deterministically from the seed (~16 MB resident), and sharing is guaranteed by
   the seed rather than by keeping the array alive.
5. `p_perm = (1 + #{|T*| ≥ |T_obs|}) / (R + 1)` (two-sided, add-one so `p > 0` always).

*Why permutation rather than a parametric GLM*: themes with many zero cells cause quasi-complete
separation in logistic regression, where the MLE diverges and the Wald p-value is meaningless
precisely for the newest themes we most want to assess. Permutation needs no asymptotics, no
Firth correction, no `statsmodels`, and it handles the creator dependence structure directly.
Small cells are where it is *most* valuable.

**Effect size — Firth-penalised logistic slope, fitted separately.** A 2-parameter logistic
`logit(share) = β0 + β1·t` fitted by IRLS on item-level Bernoulli rows (the Hessian is 2×2), with
a **Jeffreys/Firth penalty** added to the score equation. The penalty is ~10 extra lines and it
matters: without it, a theme whose members all fall in the last two periods produces
quasi-complete separation, the MLE diverges, and `β1` is infinite — and that theme is exactly
the newest, most interesting, most left-truncation-prone case. Firth keeps `β1` finite and
shrinks it toward zero, which is the conservative direction. `OR_per_period = exp(β1)` is the
human-readable effect and feeds the effect-size floor in §6. Its CI comes from creator-block
resampling (percentile bootstrap, 2000 draws), not from the model SE.

**When the effect is still not estimable, the theme may not be called `growing` or
`declining`.** The first draft said that on separation "the floor test is skipped, and the
permutation p-value still governs significance" — which hands a `growing` verdict, with no
effect floor at all, to precisely the themes the floor exists to catch. Corrected: Firth makes
separation rare; in the residual cases `effect_estimable=false` routes the theme to gate 4
(`weak_signal`) with `refusal_reason='effect_not_estimable'`, and the onset path (gate 3)
remains open to it, since onset is the right question for a theme that only exists recently.

**Direction must agree between the test and the effect.** Significance comes from the
Cochran–Armitage statistic `T` and direction from `β1`; these are two different fits and with
zero cells they can disagree in sign. A gate that takes its p-value from one and its direction
from the other can announce growth on the strength of a decline. So: if
`sign(T) ≠ sign(β1)`, the theme is refused with `refusal_reason='direction_inconsistent'` and
falls through to gate 7/8.

**Onset test (for emergence).** Fisher's exact test on a 2×2 of pooled pre-onset vs post-onset
periods, with the block structure respected by reducing to **one item per creator per period
half** (so a single prolific creator cannot create an onset). Fisher is used rather than the
trend test because onset is a step, not a slope.

**Both margins are creator-deduplicated, not just the theme row.** Reducing the theme count to
one-per-creator while leaving the corpus denominator as raw items compares a deduplicated
numerator against an un-deduplicated denominator and understates the theme's share by the mean
block size. The 2×2 is therefore built entirely in block-units: `N_half` is the number of
*distinct creator blocks* with at least one item in that half, and `n_θ,half` the number of
those blocks with at least one theme member. The left-truncation guard in §6.3 gate 3 is
restated in the same units for the same reason.

### 5.7 Dependence and concentration guards

- **Creator concentration.** `max_creator_share = max over creators of (their items in θ) /
  size_core(θ)`. If `> 0.40`, growth and decline claims are refused
  (`refusal_reason='creator_concentrated'`) and the theme is capped at `weak_signal`. *Why 0.40*:
  above that the "theme" is substantially one person's output, and the brief's guardrails (§28)
  forbid presenting that as a consumer pattern. *Boundary*: exactly 0.40 passes.
- **Minimum blocks for the permutation test**: `G ≥ 15` **exchangeable** blocks among the
  theme's dated members — blocks that sit in a `(platform, exact size)` stratum containing at
  least two blocks (§5.6.2). A block that is a fixed point of every permutation is not an
  exchangeable unit and counting it would overstate the test's resolution. Below that the
  permutation null is built from too few exchangeable units to have resolution (the smallest
  attainable p-value is ~1/number of distinct rearrangements). Below 15 the slope test is
  refused and only the onset test (which pools) may run.
- **Effective sample size.** Every count-based threshold in §6 is stated on `N_eff`, not `N`
  (see §5.8). `deff_hat(p) = 1 + (m̄_p − 1)·ρ̂`, where `m̄_p` is the mean block size among that
  period's reference-stratum items and `ρ̂` is the intra-block correlation of the
  theme-membership indicator, estimated pooled across all themes in the space by the one-way
  ANOVA estimator and clamped to `[0, 1]`; `deff_hat` is clamped to `[1, 5]`. `N_eff(p) =
  N(p) / deff_hat(p)`. When blocks are mostly singletons `deff_hat ≈ 1` and every number in this
  document reverts to its original value, which is the right degenerate behaviour.
- **Near-duplicate cap**: at most 2 items from one `dedup_group` or `near_dup_candidate` group
  per theme-period cell.
- **Platform mix**: `platform_breakdown` is stored per period and `platform_mix_shift=true` is
  recorded when the *corpus* mix changes by more than 15 percentage points between the first and
  last qualifying period. Since §5.6.3 now stratifies the statistic and the permutation by
  platform, the shift is **conditioned on rather than merely flagged**, so this is a
  diagnostic — surfaced on the run and shown next to the series — and no longer downgrades
  claims on its own. It still downgrades when a platform had to be dropped from the stratified
  statistic for want of qualifying periods, because then the conditioning is incomplete:
  `platform_coverage_incomplete=true` ⇒ `exploratory`.

### 5.8 Statistical power — stated, because silence here is dishonest

With a reference stratum of ~1500 items over 12 months, `N(p) ≈ 125`. Two-proportion power at
α = 0.05 two-sided, comparing one period against another:

| true share change | z | power at `N = 125` (deff = 1) | power at `N_eff = 62` (deff = 2) |
|---|---|---|---|
| 10% → 20% (2×) | 2.21 | 61% | **34%** |
| 5% → 10% (2×) | 1.50 | 32% | **18%** |
| 10% → 15% | 1.20 | 22% | ~12% |
| 5% → 15% (3×) | 2.64 | 75% | **46%** |

**The second column is the one that counts, and the first draft of this section printed only
the first.** §5.6 and §5.7 assert a creator design effect of 1.5–3×; a power table computed on
raw item counts therefore contradicts the document two sections earlier. At `deff = 2` the
system's headline capability — "powered to detect roughly a doubling of share" — holds only at
34% power, which is to say it misses two such doublings in three. (The 5%→15% row also had an
arithmetic error: `z = 2.64`, power 75%, not `2.78` / 79%.)

Reaching 80% power for a 10%→20% change needs `N_eff(p) ≈ 199`, i.e. **≈ 199 × deff** raw items
per period: ~2,390 dated reference-stratum items over 12 months at `deff = 1`, and **~4,800 at
`deff = 2`**, which is the regime we should plan for. The multi-period trend test is somewhat
more powerful than this pairwise comparison because it uses all periods; the order of magnitude
stands.

**A consequence for the product that follows directly and should not be discovered later:** at
these power levels, the Westfall–Young FWER-0.05 filter on headline cards (§6.6a) will pass
almost nothing. The Overview's trend cards will frequently be empty, and that is the correct
behaviour, not a bug to be fixed by loosening the filter. The Overview must therefore be built
primarily out of §5.3(D) within-period composition facts — which are assumption-free, always
available, and genuinely useful — with trend verdicts as an occasional addition rather than the
page's spine.

**Two consequences that must be visible in the product:**

1. At `deff ≈ 2` the system detects roughly a **doubling of share for themes already at ≥10%
   share, and then only about a third of the time**; it is not powered for subtler movement.
   That is the honest capability statement.
2. Therefore **"no detectable change" must never be rendered as "stable" or "flat".** Every
   `stable` verdict carries a computed **minimum detectable effect**, `mde_or_80`: the smallest
   per-period odds ratio that this theme's actual cell sizes and block structure would have
   detected with 80% power, found by parametric bootstrap — simulate membership under a grid of
   candidate ORs `{1.1, 1.15, 1.25, 1.4, 1.6, 2.0, 2.5, 3.0}`, 200 simulations each, reusing the
   observed `N(p)` and block structure (so the design effect is inherited from the data rather
   than assumed), and take the smallest OR with ≥80% rejection. The grid runs
   `{1.1, 1.15, 1.25, 1.4, 1.6, 2.0, 2.5, 3.0, 4.0, 6.0}`. **If no grid value reaches 80%
   power, `mde_or_80` is `NULL`** — a case the first draft left undefined while requiring the UI
   to print a number — and the UI string becomes *"no change detected; this theme's sample could
   not have reliably detected any change up to 6× per {period}"*, which is a more useful
   admission than a fabricated bound. Otherwise the string is *"no change detected; this theme's
   sample could only have detected a change of ≥ {mde} per {period}"*. Cost: 10 ORs × 200 sims ×
   ~60 themes of vectorised CA statistics — a few seconds.

### 5.9 Testing the common-exposure assumption

Three cheap internal checks, all stored on the run:

1. **Per-platform sign agreement.** Recompute each theme's series separately per platform. If
   the slope signs disagree across platforms that each have adequate `N(p)`, the shared-exposure
   story is weaker; record `platform_sign_agreement ∈ {agree, disagree, insufficient}` and
   downgrade disagreeing themes to `exploratory`.
2. **Per-strategy sign agreement** between strata A and B (§5.2).
3. **Sentinel exposure curve** (B), when `YOUTUBE_API_KEY` exists: compare the sentinel-derived
   `ĝ(p)` against the corpus `N(p)` profile. If they are proportional, our denominators behave
   like a pure exposure effect, which is what the common-exposure assumption predicts. A material
   divergence is informative and is surfaced to engineering; it does not change a published
   number, because we do not divide by `ĝ`.

None of these *proves* the assumption. They are falsification attempts, and the run records
whether each was attempted, passed, failed, or was impossible.

### 5.10 What becomes sayable, and what is refused

Enforced by `aypo/intelligence/claim_guard.py`. Every temporal statement in the product is
produced by a function returning either a `Claim` (with its scope sentence, sample sizes and
method pre-rendered) or a `Refusal` (with a reason code and, where possible, a weaker true
statement to use instead). **The narrative LLM receives `Claim` objects and may only paraphrase
them; it never sees raw series and never computes a trend.** A prompt instruction would not be
enough — a model given a table of monthly counts will describe a trend, because that is what
tables of monthly counts look like.

**Sayable:**

| claim form | example (generic placeholders) |
|---|---|
| within-period share, with denominator | "Theme X accounted for 12% of analysed content published in 2026-Q2 (18 of 150 items, effective sample 82) and 5% in 2026-Q1 (7 of 140, effective sample 76)." |
| share change, when §6 gates pass | "Theme X's share of analysed content rose over the last 6 months (odds ratio 1.6 per month, 95% CI 1.2–2.2; platform-stratified creator-block permutation p=0.004, BH q=0.03, family of 47 themes; 118 items from 41 creators)." |
| between-theme ordering in one period | "In the most recent complete month, Theme X was the third largest theme by share of analysed content." |
| onset, when the absence is informative | "No items matching Theme X appear in the 4 analysed months before 2026-03, which together contain 420 items from 147 distinct creators; from 2026-03 onward it appears in every month." |
| brand composition within a theme, category stratum | "Within Theme X in the category-level sample, Brand A appears in 22% of items (11/50 items from 9 creators, 95% CI 9–41% with creator clustering) and the subject brand in 4% (2/50, 95% CI 1–17%). The two shares are measured on the same 50 items and are not independent; no difference between them is claimed." |
| no-change with MDE | "No change detected in Theme X's share; this sample could only have detected a change of ≥1.6× per month." |
| cluster facts | size, first/last observed date, representative examples, engagement distribution within the sample, cross-facet co-occurrence with a Fisher q-value. |

**Refused — hard-blocked in code:**

| refused claim | reason code |
|---|---|
| any absolute-volume trend ("posts about Theme X doubled") | `volume_claim_unidentified` |
| any market-wide prevalence ("N% of consumers…") | `out_of_scope_population` |
| any claim about a period with `N_eff(p) < 40` (§6.2) | `period_underpowered` |
| growth/decline with `< 4` qualifying periods or `< 15` dated core members | `insufficient_coverage` |
| "Theme X is new" without ≥2 adequately-covered prior periods | `left_truncation_unresolved` |
| growth where `series_unstable`, `creator_concentrated`, `platform_mix_shift`, `stratum_disagreement`, `provisional`, `high_noise` or `degraded` | the matching flag name |
| any forecast or extrapolation beyond the last complete period | `no_extrapolation` |
| any causal statement | `no_causal_inference` |
| any ranking of competitors by a composite score (brief §24) | `no_composite_scores` |
| anything about an `unlabelled`, `ambiguous` or `rollup` cluster | `cluster_not_claimable` |

**Brand-composition claims carry three requirements that the sayable table above now states
explicitly, because a bare Wilson interval on clustered data is an overconfident number dressed
as a careful one:**

1. **Intervals are creator-clustered.** The Wilson interval assumes i.i.d. items; a theme's items
   are creator-blocked like everything else here. Intervals are widened by the same
   `deff_hat` of §5.7 (equivalently, computed on `N_eff`), and the number of distinct creators
   is printed next to the item count so a reader can see the real sample.
2. **The family is declared.** "Brand A is over-represented in Theme X" is one of
   *themes × brands* tests. Any claim of *over-* or *under-*representation — as opposed to a
   bare descriptive share — requires BH at `q = 0.10` over that declared family, with
   `family_size` shown. A descriptive share with an interval needs no correction and is always
   sayable.
3. **No comparison is read off two intervals.** Two shares measured on the same items are
   negatively dependent, so non-overlapping intervals are not a test and overlapping ones are
   not a null result. A difference between two brands within a theme is only claimable through
   an explicit paired test (McNemar on the within-item 2×2, creator-blocked); otherwise the
   claim text says the shares are reported side by side and no difference is asserted. This is
   a hard requirement on subsystem 05 (§11.13).

Every `Refusal` is itself displayed where a user would expect a number, with its reason in plain
language, because brief §28 requires the system to say when evidence is weak — and a blank space
reads as "nothing is happening", which is a claim.

---

## 6. Classification: established / growing / emerging / weak signal / stable / declining

### 6.1 Notation

| symbol | meaning |
|---|---|
| `M` | `size_core_reference` — core members in the reference stratum (§2.6). **Not** `size_core`. |
| `M_d` | `M` with a usable publication date |
| `P_q` | qualifying periods (§6.2) |
| `R` | the 3 most recent qualifying periods (2 when `period_kind='quarter'`, §5.5) |
| `s(p)` | `n_θ(p) / N(p)` |
| `q` | BH-adjusted permutation p-value for the trend test |
| `OR` | `exp(β1)`, Firth-penalised odds ratio per period |
| `OR_up`, `OR_dn` | the period-normalised effect floors: `1.25`/`0.80` monthly, `1.95`/`0.51` quarterly (§5.5) |
| `G` | distinct **exchangeable** creator blocks in `M_d` (§5.7) |
| `N_eff(p)` | `N(p) / deff_hat(p)` — the effective per-period sample size (§5.7) |

### 6.2 Qualifying period

A period `p` qualifies iff:

- `p` is fully elapsed, ends ≥ 7 days before collection time, and lies within the window (§5.5);
- `N_eff(p) ≥ 40` — i.e. `N(p) ≥ 40 · deff_hat(p)` reference-stratum, dated, deduplicated
  items that entered the clustering (§5.3A). At `deff_hat = 1` this is the original 40; at
  `deff_hat = 2` it is 80 raw items;
- `p` is not inside a dormancy gap for this theme (§4.6).

*Why 40, and why on `N_eff` rather than `N`*: 40 is the point at which an **observed absence
becomes informative**. If a theme truly held 10% share in `p`, the probability of seeing zero of
it in 40 *independent* items is `0.9^40 = 1.5%`, so a 0% cell rules out a ≥10% share at ~98.5%
confidence and the 95% Wilson interval for 0/40 is `[0, 8.8%]`.

**That calculation only holds for independent items, and §5.6/§5.7 say ours are not.** At a
creator design effect of 2, 40 raw items carry the information of 20: `0.9^20 = 12.2%`, which
rules out nothing. The first draft stated the i.i.d. number and the design effect two sections
apart and never reconciled them, so the single most load-bearing threshold in the classification
rule — the one that decides whether "the theme was absent" means anything — was justified by a
calculation the rest of the document contradicts. Stating the gate on `N_eff` restores the
guarantee at whatever dependence the corpus actually exhibits, and collapses to the original 40
when blocks are singletons.

*Boundary*: `N_eff(p) = 40` qualifies; `39.9` does not, and the period is recorded with
`exclusion_reason='underpowered_period'` rather than silently dropped, so the UI can show a gap
instead of a continuous line. Both `N(p)` and `N_eff(p)` are stored and both are shown, because
a reader told "40 items" when the effective sample was 20 has been misled by omission.

### 6.3 The decision rule — ordered, first match wins

Note on ordering: the *hard disqualifiers* come before `emerging`, and the *slope-power*
requirement comes after it. That is deliberate. `emerging` is established by an **onset** test —
a pooled 2x2 Fisher exact comparing a pre-onset window against a post-onset window — which is
viable at a sample size where a *slope* is not. So a theme with 8-14 dated members can be
`emerging` but can never be `growing` or `declining`. A theme that is disqualified outright
(one creator's output, noise-recovered, degraded run) cannot be any of them.

```
GATE 1  insufficient_data            -- nothing temporal may be said at all
  |M_d| < 8
  or |P_q| < 2
  or undated_fraction > 0.20
  or series_unstable                       (§4.7)
  or cluster status in {unlabelled, ambiguous, review}
  or is_rollup
  -> theme still shown with size, examples and members; no temporal claim

GATE 2  weak_signal  -- hard disqualifiers: apply to EVERY direction, onset included
  provisional                              (noise-recovered, §2.8)
  or max_creator_share > 0.40              (§5.7)
  or run high_noise or run degraded        (§2.5, §2.4)
  or platform_mix_shift and stratum_widened   (both at once: §5.7, §5.2)
  -> size and examples only; DIRECTION IS NEVER ASSERTED

GATE 3  emerging     -- onset, not slope; reachable at 8 dated members
  |M_d| >= 8
  and n_creator_blocks >= 5
  and s(p) == 0 for every qualifying period before the first appearance period p0
  and >= 2 qualifying periods exist before p0, with combined >= 60 DISTINCT CREATOR
      BLOCKS (the left-truncation guard, restated in block units -- see §6.4)
  and s(p_last) >= max(0.02, 5/N(p_last))
  and Fisher exact on pre-p0 vs post-p0, BOTH MARGINS in block units (§5.6),
      BH-adjusted q <= 0.10
  -> emerging

GATE 4  weak_signal  -- enough to exist, not enough to fit a slope
  |M| < 15  or  |M_d| < 15  or  G < 15     (§5.7)
  or not effect_estimable                  (Firth still separated: §5.6)
  or sign(T) != sign(beta1)                (direction_inconsistent: §5.6)
  -> size and examples only; DIRECTION IS NEVER ASSERTED

GATE 5  growing
  |P_q| >= 4 and |P_q ∩ R| >= 2
  and q <= 0.10 and T > 0 and beta1 > 0 and OR >= OR_up
  and mean s over R >= 0.02
  and not (series_unstable or stratum_disagreement)      -- already gated, re-asserted
  -> growing

GATE 6  declining
  same coverage conditions as GATE 5
  and q <= 0.10 and T < 0 and beta1 < 0 and OR <= OR_dn
  -> declining

GATE 7  established
  not significant (or |effect| below the floor)
  and |M_d| >= 30
  and mean s over R >= s_high                (adaptive, §6.5)
  and n_theta(p) >= 1 in >= 80% of qualifying periods
  -> established

GATE 8  stable
  everything else that reached this point
  -> stable, ALWAYS accompanied by mde_or_80 (§5.8)
```

`platform_coverage_incomplete` or `stratum_disagreement` alone do not disqualify; they set
`replication_status='exploratory'` (§6.6b). Both firing together is treated as a disqualifier
because the platform conditioning is incomplete *and* the two strata disagree, which leaves
nothing to anchor a direction to. (`platform_mix_shift` on its own is now only a diagnostic,
since §5.6.3 conditions on platform rather than merely detecting the shift.)

### 6.4 Justification of each number, and the boundary behaviour

**`|M_d| ≥ 8` (gates 1 and 3)** — matches `min_cluster_size`; below it there is no cluster. At
exactly 8 the theme can be classified `emerging` (gate 3, onset test) but never `growing` or
`declining` (gate 5/6 need 15).

**`|P_q| ≥ 2` (gate 1)** — a single period is a snapshot; you cannot even plot it.

**`|M| < 15 ⇒ weak_signal` (gate 4)** — with 15 dated members spread over 4 periods, the average
cell is 3.75. One item moving between periods changes a cell by 27%, and the bootstrap CI on
`OR` spans more than an order of magnitude. 15 is the point where the slope stops being
determined by individual items. *Boundary*: a theme with 8–14 dated members is reported with its
size and examples and the explicit label "weak signal — too few items to assess direction".
This is the main reason `min_cluster_size=8` is safe: small clusters exist and are browsable, but
the rule forbids them from carrying a direction.

**`|P_q| ≥ 4` and `≥2` of them recent (gate 5/6)** — three points are dominated by one noisy
point, and four gives the trend statistic some residual structure to work against. Requiring at
least two of the four to be among the three most recent periods stops a "growing" verdict that
is driven entirely by old data and says nothing about now. *Boundary*: a theme with 4 qualifying
periods, all older than `R`, falls through to gate 7/8 and can be `established` or `stable` but
never `growing`.

**`OR ≥ OR_up` / `≤ OR_dn` effect floor (gate 5/6)** — 1.25 per month compounds to 3.8× over 6
months and 14.6× over 12: unambiguously what a marketer means by a trend. The floor is
**period-normalised** (§5.5): quarterly it is `1.25³ = 1.95` and `0.80³ = 0.51`, so the same
real rate of change is required in both regimes. A flat `1.25` would have meant 1.077/month in
the quarterly regime — a *weaker* bar in the data-poor case, which is the wrong way round. Below 1.25 a
statistically significant slope at these sample sizes is nearly always carried by one unusually
large cell, so the floor is a robustness device as much as a relevance device. 0.80 = 1/1.25
keeps the rule symmetric, which matters because an asymmetric rule would make the system
systematically readier to announce growth than decline — the exact bias §5.1 warns about,
reintroduced through the back door. *Boundary*: `q ≤ 0.10` with `OR = 1.2` falls through to gate
7/8 and is reported as `stable` with a note that a small positive slope was detected but is
below the reporting floor; the raw numbers are in `theme_trend_tests` for anyone who looks.

**`s ≥ 0.02` relevance floor (gate 5) and `≥ max(0.02, 5/N)` (gate 3)** — at `N(p) ≈ 125`, 2% is
2–3 items. Below that the share estimate is one item wide and any slope through it is noise. The
`5/N` term in gate 3 ensures an emerging theme is backed by at least 5 items in its latest
period, not by the 2 that 2% of a small period would allow. *Boundary*: a theme at 1.9% with a
significant positive slope is reported as `emerging` if it passes gate 3, else `stable`.

**`≥2` prior periods with combined `≥ 60` distinct creator blocks (gate 3)** — this is the
**left-truncation guard**, and it is the difference between "this theme is new" and "we have no
old content". The first draft stated it as "combined `N ≥ 80` items" and justified it with
`0.95^80 = 1.7%`. That is the i.i.d. calculation again: at a design effect of 2 the effective
sample is 40 and `0.95^40 = 12.9%`, which does not rule out a 5% prior share at all — and this
guard is the only thing standing between the product and calling every theme `emerging`,
because coverage of old periods is thin by construction (§5.1). Restating it in **block units**
makes it robust without needing `deff_hat` at all: a creator block contributes at most one
independent observation, so 60 distinct blocks gives `0.95^60 = 4.6%`, i.e. the absence rules
out a ≥5% prior share at ~95% confidence under the most pessimistic dependence assumption
available. This is the same unit the onset Fisher table now uses on both margins (§5.6), so the
guard and the test finally agree about what they are counting. *Boundary*: a
theme absent from earlier periods that do **not** meet the coverage bar is classified
`insufficient_data` with `refusal_reason='left_truncation_unresolved'`, and the UI says we cannot
tell whether it is new or whether our sample is.

**`|M_d| ≥ 30` for `established` (gate 7)** — "established" is the strongest word in the
vocabulary and the only one a reader will treat as settled fact. 30 dated members across ≥80% of
qualifying periods gives ~2.5 items per period minimum: enough to say the theme is continuously
present rather than intermittently sampled.

**`G ≥ 15` blocks (gate 4)** — see §5.7. `G` is the number of **exchangeable** permutation
blocks (those sitting in a `(platform, exact size)` stratum of at least two), so this is a
requirement on the *inference*, not on the theme: below 15 the permutation null has too few
exchangeable units to resolve a small p-value at all. A block that is a fixed point of every
permutation does not count toward it.

**`n_creator_blocks ≥ 5` for `emerging` (gate 3)** — the onset test pools periods, so the
creator-concentration guard at gate 2 (≤40% from one creator) is not sufficient on its own: with
8 members it permits 3 creators, and a step appearing in 3 creators' output is a coincidence
waiting to happen. 5 distinct creators is the minimum at which "this started appearing" is about
the corpus rather than about a few accounts. *Boundary*: a theme with 4 creator blocks and a
clean onset is reported as `weak_signal` with its onset date shown as a cluster fact, which is
sayable (§5.10) without being a trend claim.

### 6.5 `s_high`: an adaptive threshold, because an absolute one is meaningless

A fixed "≥10% share = established" is nonsense: share depends entirely on how many themes the
clusterer found. A corpus partitioned into 60 themes has a mean share of 1.7%, so no theme would
ever be established; one partitioned into 6 themes would have all of them established.

```
s_high = quantile({ mean share over R : themes in this space that are trend-eligible,
                    labelled, non-provisional and have >= 1 qualifying period in R }, 0.80)
```

i.e. `established` means **top quintile by share within its own space**, which is a statement
about relative prominence in the analysed sample and is exactly as strong as the data supports.
The realised `s_high` is stored on the run and shown on hover, so the threshold is never a
hidden constant. *Boundary*: with fewer than 10 trend-eligible themes in a space, `s_high` falls
back to `0.10` and `established` additionally requires `|M_d| ≥ 40`, because an 80th percentile
over 6 values is not a quantile.

### 6.6 Multiple comparisons

Three distinct multiplicity problems, handled separately rather than with one correction
pretending to cover all of them.

**(a) Across themes within a run.** 30–80 themes are tested per space per run. Uncorrected
`p < 0.05` over 60 themes yields ~3 false "growing" verdicts **per run, guaranteed**, which would
mean roughly every Overview page contains a fabricated trend.

- **Family definition**: all trend-eligible themes tested in the same `(space, run)` with the
  same test kind. Family size is stored on every test row (`family_size`), because a q-value
  without its family size is uninterpretable.
- **Screening list: Benjamini–Hochberg at `q = 0.10`.** *Why 0.10 and not 0.05*: these outputs
  feed a human research loop whose explicit purpose (brief §2 Q10, §31.4) is deciding what to
  investigate further. A 10% false-discovery rate among flagged themes is a reasonable screening
  cost, and it is dramatically more honest than the uncorrected alternative. The threshold is
  stated on the UI surface, not buried.
- **Headline claims: Westfall–Young minP**, computed from the **shared permutation set**
  (§5.6.4), controlling FWER at 0.05. *Why this in addition to BH*: theme shares within a period
  sum to (at most) 1, so the tests are **negatively correlated by construction** and BH's
  independence/PRDS assumption is not satisfied. Westfall–Young is valid under arbitrary
  dependence and gets the dependence structure for free, because we already permute the whole
  membership matrix once for every theme.

  **Two corrections to how the first draft described this.** (i) The algorithm it gave — "for
  each permutation record `min_p` across all themes; a theme's adjusted p-value is the fraction
  of permutations whose `min_p` ≤ its observed `p`" — is **single-step** minP, not step-down.
  Both are valid FWER procedures; step-down is uniformly more powerful and, at the power levels
  of §5.8, that difference is not academic. We use step-down: order the observed p-values
  `p_(1) ≤ … ≤ p_(J)`, and for the `j`-th take the per-permutation minimum over the *remaining*
  hypotheses `{(j), …, (J)}` only, then enforce monotonicity by a running maximum. (ii) Taking a
  minimum across themes requires **p-values**, not statistics: `T` is not comparable across
  themes because its scale depends on `p̄` and the `N(p)`. So the full `(R × J)` statistic matrix
  is retained (10,000 × ~115 float32 ≈ 4.6 MB) and each theme's column is converted to
  permutation p-values by within-column ranking before any minimum is taken. Neither step is
  expensive; both are easy to omit and silently wrong if omitted.

  Only themes surviving WY step-down minP may appear on the Overview's headline cards; BH
  survivors populate the Trends section with their q-values visible. See §5.8 for the expected
  consequence: this filter will usually pass nothing.

**(b) Across runs (sequential testing).** The same theme is tested every run; BH does not fix
re-testing. Handled by **replication status** rather than by pretending to a formal sequential
procedure:

| `replication_status` | meaning |
|---|---|
| `exploratory` | passed with a downgrade flag (§5.7, §5.9) or `stratum_widened` |
| `unreplicated` | first run in which the theme met the gates and passed |
| `replicated` | passed in this run **and** the immediately previous run in which it was eligible, with the same sign, **and** across a boundary where the instrument did not change — same `identity_epoch`, same `reference_query_set_version` (§5.2), same pinned reducer fingerprint. Any of those changing resets to `unreplicated`: the theme may have replicated, but not on the same measuring device. |
| `contradicted` | passed previously with the opposite sign |

The UI sorts `replicated` above `unreplicated`, and an unreplicated verdict renders as "first
detected this run; not yet replicated". `contradicted` is shown, not hidden — a theme that
reversed is interesting and concealing it would be the dishonest option.

**(c) Across facets.** The same item is in several spaces, so facet themes are not independent of
global themes. Families are **per-space**, and the UI must not present a global theme and a facet
theme as two independent pieces of evidence for the same finding. `clusters.space_key` makes this
checkable, and the findings subsystem is required to deduplicate evidence by `item_id` across
spaces (§11 cross-subsystem contract).

### 6.7 Commerce themes

The same machinery, with one hard difference: **commerce records have no reliable publication
date.** Listing creation dates are generally unavailable from marketplace collectors; review
dates are available. Therefore:

- Commerce theme series are built from **review dates** of reviews attached to the theme's member
  listings, with `period_kind='month'` and the same gates. **The unit of analysis changes from
  listing to review here, and that must be stated rather than inherited silently.** A theme's
  members are listings; its series counts *reviews*. So: `n_θ(p)` is reviews in period `p`
  attached to the theme's member listings, `N(p)` is all reviews in `p` attached to any
  in-stratum listing that entered the commerce clustering, and `unit='review'` is stored on
  every `theme_timeseries` row. Reviews are massively clustered within listings — one listing can
  contribute hundreds — so **the permutation and Fisher blocks for commerce are listings**
  (seller as a secondary block where available), never reviews. Without that, a single
  well-reviewed listing manufactures an onset on its own, and `emerging` is the one directional
  verdict commerce is still allowed.
- Review visibility is itself sorted by helpfulness/recency, so the exposure problem is *more*
  severe than for social. Consequently commerce themes may be classified `emerging` or `stable`
  but **growth and decline claims on commerce themes are refused outright** —
  `refusal_reason='commerce_temporal_unidentified'` — until a source with a trustworthy date
  denominator exists.
- Listing-level cross-sectional claims are fully sayable: share of listings in a theme, price
  band distribution, review-count distribution, brand composition within a theme, all scoped to
  the analysed sample.

---

## 7. DDL

Target: Supabase Postgres + pgvector. Migration file:
`supabase/migrations/0004_clustering_and_trends.sql`.
**This DDL has been executed against a live Postgres with `vector` and `pg_trgm` installed and
applies cleanly**; the four defects found by doing so (a generated column that is rejected
outright, a unique index that rejects legitimate rows, a missing uniqueness guard, and a
delete path that deadlocks on an FK) are called out inline below rather than left for the
migration to discover.
Tables referenced but **not owned** here: `research_runs`, `content_items`, `commerce_items`,
`creators`, `brands`. Their FKs are written as declared; if another subsystem names them
differently, only the FK lines change.

### 7.1 Extensions and enums

```sql
create extension if not exists vector;
create extension if not exists pg_trgm;

create type cluster_corpus       as enum ('social', 'commerce');
create type cluster_space        as enum ('global', 'situation', 'expression',
                                          'language_culture', 'product_context');
create type cluster_status       as enum ('active', 'provisional', 'unlabelled', 'review',
                                          'dormant', 'split', 'merged', 'retired');
create type cluster_birth_kind   as enum ('birth', 'split', 'merge', 'resurrect', 'epoch_break');
create type cluster_death_kind   as enum ('dissolved', 'absorbed', 'corpus_churn', 'scattered');
create type lineage_relation     as enum ('continuation', 'split', 'merge', 'birth', 'resurrect',
                                          'absorbed', 'dissolved', 'epoch_break',
                                          'suspected_continuation', 'rollup_child');
create type membership_role      as enum ('core', 'secondary', 'noise');
create type trend_class          as enum ('established', 'growing', 'emerging', 'weak_signal',
                                          'stable', 'declining', 'insufficient_data');
create type replication_status   as enum ('exploratory', 'unreplicated', 'replicated',
                                          'contradicted');
create type period_kind          as enum ('month', 'quarter');
create type discovery_strategy   as enum ('brand', 'category', 'competitor', 'theme',
                                          'hypothesis');  -- brief §8 A..E
create type run_status           as enum ('ok', 'degraded', 'preview', 'failed');
create type stratum_kind         as enum ('reference', 'category', 'widened');
```

`discovery_strategy` is the enum that makes the reference stratum (§5.2) a one-line filter:
reference = `('brand','category')`, brand-comparison stratum = `('category')`.

### 7.2 `content_embeddings`

```sql
create table content_embeddings (
  id                uuid primary key default gen_random_uuid(),
  item_kind         text not null check (item_kind in ('content','commerce')),
  content_item_id   uuid references content_items(id)  on delete cascade,
  commerce_item_id  uuid references commerce_items(id) on delete cascade,

  view              text not null,              -- theme_v1 | facet_*_v1 | retrieval_v1 | commerce_theme_v1
  cad_template_version text not null,
  cad_text          text not null,
  cad_token_count   int  not null,
  cad_dropped_low_confidence int not null default 0,
  cad_truncated_fields text[] not null default '{}',
  content_hash      text not null,              -- sha256(view||template_version||cad_text)

  embedding_provider text not null,             -- openai | jina
  embedding_model    text not null,
  embedding_dims     int  not null check (embedding_dims between 1 and 2000),
  identity_epoch     text not null,
  embedding          vector(1536) not null,     -- L2-normalised on write

  created_at timestamptz not null default now(),

  constraint one_item check (
    (item_kind = 'content'  and content_item_id is not null and commerce_item_id is null) or
    (item_kind = 'commerce' and commerce_item_id is not null and content_item_id is null))
);

-- NOT unique. Two DIFFERENT items can render byte-identical CADs (sparse analysis records
-- make this routine), and a unique index here rejects the second one outright -- verified
-- against Postgres: the insert fails with unique_violation. The hash is a CACHE KEY; per-item
-- uniqueness lives on content_embeddings_item_view below, and identical CADs are resolved by
-- the §1.8 dedup rule, not by a constraint violation.
create index content_embeddings_cache
  on content_embeddings (content_hash, embedding_model, embedding_dims);
create unique index content_embeddings_item_view
  on content_embeddings (item_kind, coalesce(content_item_id, commerce_item_id), view,
                         cad_template_version, embedding_model);
-- PARTIAL on purpose. An unfiltered HNSW index spans all seven views; a retrieval query that
-- filters `view = 'retrieval_v1'` would then post-filter ~1/7 of the returned neighbours and
-- recall collapses. The partial index searches only the vectors the query can use.
create index content_embeddings_retrieval_ann
  on content_embeddings using hnsw (embedding vector_cosine_ops)
  with (m = 16, ef_construction = 64)
  where view = 'retrieval_v1';
create index content_embeddings_view on content_embeddings (view, identity_epoch);
```

The HNSW index exists for `retrieval_v1` semantic search (§31.5) only. Clustering and the §2.6
secondary-membership scan read vectors in bulk into numpy and never touch the index — the
earlier claim that the index served §2.6 was wrong and would have justified making it
unfiltered.
`vector(1536)` is fixed by `aypo_embedding_dimensions`; a dimension change is an epoch break and
a new migration, which is the correct amount of friction.

### 7.3 `cluster_runs`

```sql
create table cluster_runs (
  id                 uuid primary key default gen_random_uuid(),
  -- RESTRICT, not CASCADE. With `on delete cascade` here, deleting a research_run cascades to
  -- cluster_runs, which clusters.first_run_id / last_run_id reference with no action -- so the
  -- delete fails with a foreign_key_violation (verified). Stable cluster identities are
  -- deliberately longer-lived than any single run, so the correct resolution is that a run with
  -- clusters cannot be deleted; retire it instead.
  research_run_id    uuid not null references research_runs(id) on delete restrict,
  corpus             cluster_corpus not null,
  space              cluster_space  not null,
  -- NOT a generated column. `create table ... (space_key text generated always as
  -- (corpus::text || '.' || space::text) stored)` fails with
  --   ERROR: generation expression is not immutable
  -- because the enum-to-text cast is an I/O conversion, which Postgres treats as stable. This
  -- was the first statement in the original migration that referenced cluster_runs, so the
  -- whole file cascade-failed from it. A plain column plus a CHECK is accepted (CHECK does not
  -- impose the immutability requirement) and gives the same guarantee.
  space_key          text not null,
  constraint cluster_runs_space_key_matches
    check (space_key = corpus::text || '.' || space::text),

  identity_epoch     text not null,
  embedding_provider text not null,
  embedding_model    text not null,
  embedding_dims     int  not null,
  cad_template_version text not null,

  n_items_analysed   int not null,
  n_items_embedded   int not null,
  n_items_clustered  int not null,          -- after dedup collapse
  embedding_coverage numeric,               -- n_items_embedded / nullif(n_items_analysed,0)
  n_clusters         int not null,
  n_provisional      int not null default 0,
  noise_fraction_primary numeric not null,   -- first HDBSCAN pass; what high_noise is judged on
  noise_fraction     numeric not null,         -- after noise recovery (§2.8); what the UI shows
  high_noise         boolean not null default false,
  reducer_refit      boolean not null default false,   -- §2.9: expect series_unstable this run

  space_config       jsonb not null,  -- {reducer, k_pcs_removed, dims, explained_var, fingerprint, caveat}
  space_stats        jsonb not null,  -- {nn_dist_q05, eps_used, cos_quantiles, dedup_threshold}
  hdbscan_params     jsonb not null,
  identity_config    jsonb not null,  -- {cos_weight, tau_match, tau_ambiguous, calibration_f1}
  selection_report   jsonb not null,  -- per-candidate ari_core, ari_with_noise, dbcv, noise, n_clusters
  style_probe        jsonb,           -- {replicate:{top1_rate, mean_self_cos, margin},
                                      --  paraphrase:{...}, top1_rate_corpus, marginal, partial}
  exposure_checks    jsonb,           -- {platform_sign_agreement, strategy_sign_agreement, sentinel}

  lib_versions       jsonb not null,  -- {numpy, sklearn, scipy, umap}
  input_digest       text not null,   -- sha256 of the ordered input item ids
  permutation_seed   bigint not null,

  status             run_status not null default 'ok',
  degraded_reason    text,
  wallclock_ms       int,
  created_at         timestamptz not null default now()
);

create unique index cluster_runs_one_per_space
  on cluster_runs (research_run_id, corpus, space);
create index cluster_runs_epoch on cluster_runs (identity_epoch, space_key, created_at desc);
```

### 7.4 `clusters` — the stable identity

```sql
create table clusters (
  id                 uuid primary key default gen_random_uuid(),
  -- stable, human-safe reference that never changes, even when the label is refined.
  -- DERIVED, not random (§2.9): space_key || '.' || sha256(sorted first-revision member ids)[:8]
  cluster_key        text not null,          -- e.g. "social.global.7f3a2b91"
  space_key          text not null,
  corpus             cluster_corpus not null,
  space              cluster_space  not null,
  identity_epoch     text not null,

  -- ── label (always produced AFTER members were inspected: §3) ──────────────
  status             cluster_status not null default 'active',
  label              text,
  description        text,
  label_status       text,                   -- accepted | failed_support | failed_verification
                                             -- | failed_ablation | refused_<reason>
  label_confidence   text,                   -- low | medium | high
  label_run_id       uuid references cluster_runs(id),
  label_history      jsonb not null default '[]',   -- [{run_id, label, reason, at}]
  shared_attributes  jsonb not null default '[]',   -- [{field, attribute, refs}]
  demotion_reason    text,                   -- style_artefact | oversized | ...
  leakage_candidates jsonb not null default '[]',  -- §1.2 brand-leakage scan: review queue only

  -- ── identity & lineage ────────────────────────────────────────────────────
  birth_kind         cluster_birth_kind not null default 'birth',
  death_kind         cluster_death_kind,
  first_run_id       uuid not null references cluster_runs(id),
  last_run_id        uuid not null references cluster_runs(id),
  current_revision_id uuid,                  -- FK added after cluster_revisions exists
  suspected_continuation_of uuid references clusters(id),
  parent_cluster_id  uuid references clusters(id),   -- roll-up parent (§2.7)
  is_rollup          boolean not null default false,
  provisional        boolean not null default false, -- noise-recovered (§2.8)
  trend_eligible     boolean not null default true,
  resurrect_gap_runs int,

  -- ── geometry (1536-d identity space: §4.2) ────────────────────────────────
  centroid_1536      vector(1536),
  inlier_radius_cos  numeric,                -- tau_c (§2.6)
  mean_intra_cos     numeric,

  -- ── descriptive stats (core members only: §2.6) ───────────────────────────
  -- size_core is ALL strata and is the browse count; size_core_reference is the
  -- reference stratum and is the ONLY one any statistic, gate or guard may use.
  size_core          int not null default 0,
  size_core_reference int not null default 0,
  size_secondary     int not null default 0,
  undated_members    int not null default 0,
  undated_members_reference int not null default 0,
  first_observed_pub date,
  latest_observed_pub date,
  max_creator_share  numeric,                -- reference stratum, dated
  n_creator_blocks   int,                    -- exchangeable blocks (§5.7)
  brand_presence     jsonb not null default '{}',  -- {brand_id: {n, share, wilson_low, wilson_high}}
  engagement_stats   jsonb not null default '{}',  -- medians/IQR, never a mean
  platform_breakdown jsonb not null default '{}',
  strategy_breakdown jsonb not null default '{}',

  -- ── current verdict (denormalised for the UI; audit trail in theme_trend_tests) ──
  current_classification trend_class not null default 'insufficient_data',
  current_replication    replication_status,
  current_refusal_reason text,
  series_unstable        boolean not null default false,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  constraint clusters_space_key_matches
    check (space_key = corpus::text || '.' || space::text),
  constraint size_core_reference_le_core check (size_core_reference <= size_core),
  constraint rollup_not_trendable check (not (is_rollup and trend_eligible)),
  constraint provisional_not_directional check (
    not (provisional and current_classification
         in ('established','growing','declining','emerging')))
);

create unique index clusters_key on clusters (cluster_key);
create index clusters_space  on clusters (space_key, identity_epoch, status);
create index clusters_active on clusters (space_key, status) where status = 'active';
create index clusters_centroid_ann
  on clusters using hnsw (centroid_1536 vector_cosine_ops);   -- cross-run matching + cluster_links
```

**No column names the subject brand.** `brand_presence` is keyed by `brand_id`; a column such as
`piattos_presence` would hard-code the research subject into the schema, which brief §34 forbids,
and would also break the moment a second brand is studied. `engagement_stats` stores medians and
IQRs, never means — engagement is heavy-tailed and a mean is one viral item.

The two CHECK constraints are the database-level expression of two rules this document makes:
roll-ups are never trend-eligible (§2.7), and provisional (noise-recovered) clusters never carry
a direction of any kind — including `emerging`, since gate 2 disqualifies them before gate 3
(§2.8, §6.3).

### 7.5 `cluster_revisions` — the per-run snapshot

```sql
create table cluster_revisions (
  id                 uuid primary key default gen_random_uuid(),
  cluster_id         uuid not null references clusters(id) on delete cascade,
  run_id             uuid not null references cluster_runs(id) on delete cascade,

  raw_hdbscan_label  int,                 -- the unstable integer, kept for debugging only
  size_core          int not null,
  size_secondary     int not null default 0,
  centroid_1536      vector(1536) not null,
  centroid_run_space double precision[],  -- clustering-space centroid; not a pgvector type
                                          -- because the reducer dimension varies by space
  inlier_radius_cos  numeric not null,
  mean_intra_cos     numeric,
  persistence        numeric,             -- mean probabilities_ of core members
  medoid_item_id     uuid,
  exemplar_item_ids  uuid[] not null default '{}',

  -- cross-run identity evidence
  matched_prev_cluster_id uuid references clusters(id),
  match_score        numeric,
  match_cos          numeric,
  match_jaccard      numeric,
  match_decision     text,               -- continuation | ambiguous | birth | split | merge | budding
  member_jaccard_vs_prev numeric,

  -- series honesty (§4.7)
  series_stability_rho numeric,
  series_stability_mae numeric,
  series_unstable      boolean not null default false,

  created_at timestamptz not null default now()
);

create unique index cluster_revisions_one_per_run on cluster_revisions (cluster_id, run_id);
create index cluster_revisions_run on cluster_revisions (run_id);

alter table clusters
  add constraint clusters_current_revision_fk
  foreign key (current_revision_id) references cluster_revisions(id) on delete set null;
```

`raw_hdbscan_label` is stored and **never joined on**. It exists so that a human debugging a
weird lineage decision can reproduce it; a view that joins on it would reintroduce exactly the
instability §4.1 describes.

### 7.6 `cluster_lineage`

```sql
create table cluster_lineage (
  id          uuid primary key default gen_random_uuid(),
  run_id      uuid not null references cluster_runs(id) on delete cascade,
  parent_cluster_id uuid references clusters(id) on delete cascade,
  child_cluster_id  uuid references clusters(id) on delete cascade,
  relation    lineage_relation not null,
  weight      numeric,              -- share of the parent's surviving members in the child
  match_score numeric,
  match_cos   numeric,
  match_jaccard numeric,
  n_parent_members_surviving int,
  n_shared_members int,
  note        text,
  created_at  timestamptz not null default now(),

  constraint lineage_endpoints check (parent_cluster_id is not null or child_cluster_id is not null),
  constraint lineage_no_self   check (parent_cluster_id is distinct from child_cluster_id)
);

create index cluster_lineage_child  on cluster_lineage (child_cluster_id);
create index cluster_lineage_parent on cluster_lineage (parent_cluster_id);
create unique index cluster_lineage_unique
  on cluster_lineage (run_id, coalesce(parent_cluster_id,'00000000-0000-0000-0000-000000000000'::uuid),
                      coalesce(child_cluster_id,'00000000-0000-0000-0000-000000000000'::uuid), relation);
```

A `birth` has a null parent; a `dissolved` death has a null child. Both are recorded as rows so
the lineage graph is complete and the UI can render a theme's full story, including the run in
which it stopped existing and why.

### 7.7 `cluster_members`

```sql
create table cluster_members (
  id                 uuid primary key default gen_random_uuid(),
  cluster_id         uuid references clusters(id) on delete cascade,     -- NULL for noise
  cluster_revision_id uuid references cluster_revisions(id) on delete cascade,
  run_id             uuid not null references cluster_runs(id) on delete cascade,

  item_kind          text not null check (item_kind in ('content','commerce')),
  content_item_id    uuid references content_items(id)  on delete cascade,
  commerce_item_id   uuid references commerce_items(id) on delete cascade,

  membership_role    membership_role not null,
  membership_weight  numeric,            -- 1.0 for core; w(x,c) in (0,1] for secondary
  hdbscan_probability numeric,
  outlier_score      numeric,
  dist_to_centroid_cos numeric,
  is_exemplar        boolean not null default false,
  is_medoid          boolean not null default false,
  is_atypical        boolean not null default false,

  -- denormalised so series, stratification and robust inference are index-only scans
  publication_date   date,
  publication_period date,               -- first day of the market-timezone bucket (§5.5)
  date_precision     text,               -- day | month | unknown
  platform           text,
  creator_id         uuid references creators(id) on delete set null,
  discovery_strategy discovery_strategy,
  dedup_group_id     uuid,
  near_dup_candidate boolean not null default false,
  is_dedup_representative boolean not null default true,

  created_at timestamptz not null default now(),

  constraint one_item check (
    (item_kind = 'content'  and content_item_id is not null and commerce_item_id is null) or
    (item_kind = 'commerce' and commerce_item_id is not null and content_item_id is null)),
  constraint noise_has_no_cluster check (
    (membership_role = 'noise') = (cluster_id is null)),
  constraint core_weight check (
    membership_role <> 'core' or coalesce(membership_weight, 1.0) = 1.0)
);

create unique index cluster_members_unique
  on cluster_members (run_id, item_kind, coalesce(content_item_id, commerce_item_id),
                      coalesce(cluster_id, '00000000-0000-0000-0000-000000000000'::uuid),
                      membership_role);

-- THE counting rule, at the only level that can actually enforce it. The index above keys on
-- cluster_id, so without this one an item could hold two 'core' rows in the same run, or be
-- 'core' and 'noise' simultaneously -- both verified against Postgres as accepted inserts.
-- Either silently breaks every denominator and makes §10.13's
-- `n_core + n_noise == n_items_clustered` unsatisfiable. §2.6 calls the core-only counting rule
-- non-negotiable; this is where that becomes true rather than aspirational.
create unique index cluster_members_one_primary
  on cluster_members (run_id, item_kind, coalesce(content_item_id, commerce_item_id))
  where membership_role in ('core', 'noise');
create index cluster_members_cluster on cluster_members (cluster_id, membership_role);
create index cluster_members_series
  on cluster_members (cluster_id, membership_role, discovery_strategy, publication_period)
  where membership_role = 'core' and is_dedup_representative;
create index cluster_members_item
  on cluster_members (item_kind, coalesce(content_item_id, commerce_item_id));
-- The DENOMINATOR index. cluster_members_series leads with cluster_id and therefore cannot
-- serve the per-period corpus count, which has no cluster_id in its predicate at all.
create index cluster_members_corpus
  on cluster_members (run_id, publication_period, discovery_strategy, platform)
  where membership_role in ('core', 'noise') and is_dedup_representative;
create index cluster_members_creator on cluster_members (cluster_id, creator_id)
  where membership_role = 'core';
```

Two partial indexes carry the series work, and they are not interchangeable.
`cluster_members_series` serves the **numerator** (`core` + `is_dedup_representative` + strategy
+ period, for one cluster); `cluster_members_corpus` serves the **denominator**, which has no
`cluster_id` in its predicate and so cannot use an index that leads with one. The first draft
claimed one index covered both.

The `noise_has_no_cluster` constraint is a biconditional, so it is impossible to write a noise
row attached to a cluster, or a clustered row marked noise — the two ways the noise accounting
could silently drift.

### 7.8 `theme_timeseries` and `theme_trend_tests`

```sql
create table theme_timeseries (
  id                 uuid primary key default gen_random_uuid(),
  cluster_id         uuid not null references clusters(id) on delete cascade,
  cluster_revision_id uuid not null references cluster_revisions(id) on delete cascade,
  space_key          text not null,

  period_kind        period_kind not null,
  period_start       date not null,          -- market-timezone bucket start (§5.5)
  stratum            stratum_kind not null,  -- reference (A+B) | category (B) | widened (A+B+C)
  unit               text not null default 'item',   -- 'item' (social) | 'review' (commerce, §6.7)

  n_theme            int not null,           -- core, dedup-representative, in-stratum, dated
  n_corpus           int not null,           -- SAME filters; entered clustering, noise INCLUDED
  deff_hat           numeric not null default 1.0,   -- §5.7
  n_corpus_eff       numeric,                -- n_corpus / deff_hat
  share              numeric,                -- n_theme / nullif(n_corpus, 0)
  share_ci_low       numeric,                -- Wilson 95%
  share_ci_high      numeric,
  n_unique_creators  int not null default 0,
  max_creator_share  numeric,
  n_dedup_groups     int,

  is_qualifying      boolean not null,
  exclusion_reason   text,                   -- underpowered_period | incomplete_period
                                             -- | outside_window | dormancy_gap
  platform_breakdown jsonb not null default '{}',
  strategy_breakdown jsonb not null default '{}',

  computed_at        timestamptz not null default now()
);

create unique index theme_timeseries_unique
  on theme_timeseries (cluster_revision_id, period_kind, stratum, period_start);
create index theme_timeseries_cluster on theme_timeseries (cluster_id, period_kind, period_start);


create table theme_trend_tests (
  id                 uuid primary key default gen_random_uuid(),
  cluster_id         uuid not null references clusters(id) on delete cascade,
  cluster_revision_id uuid not null references cluster_revisions(id) on delete cascade,
  run_id             uuid not null references cluster_runs(id) on delete cascade,

  test_kind          text not null,          -- ca_trend_permutation | onset_fisher
  stratum            text not null,
  period_kind        period_kind not null,

  n_periods_qualifying int not null,
  n_periods_recent     int not null,
  n_dated_members      int not null,
  n_creator_blocks     int not null,

  statistic          numeric,                -- Cochran-Armitage T
  beta1              numeric,                -- logistic slope (null when non-estimable)
  or_per_period      numeric,
  or_ci_low          numeric,                -- creator-block bootstrap percentile
  or_ci_high         numeric,
  effect_estimable   boolean not null default true,

  p_permutation      numeric,
  q_bh               numeric,
  p_westfall_young   numeric,
  family_size        int not null,
  n_permutations     int not null,
  permutation_seed   bigint not null,

  mde_or_80          numeric,                -- minimum detectable effect (§5.8)
  s_high_used        numeric,
  classification     trend_class not null,
  replication_status replication_status,
  refusal_reason     text,
  guard_flags        jsonb not null default '{}',   -- {series_unstable, creator_concentrated,
                                                    --  platform_mix_shift, stratum_widened,
                                                    --  stratum_disagreement, high_noise, degraded}
  computed_at        timestamptz not null default now()
);

create unique index theme_trend_tests_unique
  on theme_trend_tests (cluster_revision_id, test_kind, stratum);
create index theme_trend_tests_cluster on theme_trend_tests (cluster_id, computed_at desc);
```

Two details that carry real weight:

- **`n_corpus` includes noise points and excludes items that never entered the clustering.**
  Both halves are load-bearing and the first draft got the second one backwards by saying the
  denominator is "not restricted to clustered items". *Noise must be in*: excluding it would let
  a run's noise fraction mechanically inflate every theme's share, manufacturing a trend out of
  a clustering parameter. *Items that failed analysis, failed embedding, or lack a usable date
  must be out*: including them deflates every share by a factor equal to the per-period
  pipeline-failure rate — and that rate varies by platform and by period, which is the exact
  class of bias this whole section exists to eliminate, reintroduced in the denominator. The
  per-period count of analysed-but-not-embedded items is stored separately on the run so a
  drift in it is visible instead of silently rescaling the series.
- **`stratum` is part of the unique key**, so the reference and category strata coexist as
  parallel series and the sign-agreement check (§5.2) is a self-join rather than a recomputation.

### 7.9 `cluster_links` (cross-space association)

```sql
create table cluster_links (
  id            uuid primary key default gen_random_uuid(),
  run_id        uuid not null references cluster_runs(id) on delete cascade,
  from_cluster_id uuid not null references clusters(id) on delete cascade,
  to_cluster_id   uuid not null references clusters(id) on delete cascade,
  link_kind     text not null,     -- centroid_similarity | member_cooccurrence | cross_facet
  score         numeric not null,
  n_shared_items int,
  fisher_p      numeric,
  fisher_q      numeric,
  created_at    timestamptz not null default now(),
  constraint links_distinct check (from_cluster_id <> to_cluster_id)
);
create unique index cluster_links_unique
  on cluster_links (run_id, from_cluster_id, to_cluster_id, link_kind);
```

`centroid_similarity` links social↔commerce themes whose 1536-d centroids exceed `cos ≥ 0.55`
(a deliberately permissive bar, since the two corpora use different CAD templates and will never
be close in absolute cosine terms; the link is a *candidate for human inspection*, which is what
brief §17 asks for, not an inference). `cross_facet` links carry Fisher p/q from the
co-occurrence contingency table. **No `cluster_links` row licenses a causal statement**; the
`claim_guard` refuses `no_causal_inference` on any claim whose evidence is a link.

---

## 8. Python interfaces

Type-annotated signatures only; no implementations. `FloatArray = npt.NDArray[np.float32]`.

### 8.1 `aypo/analysis/cad.py`

```python
CAD_TEMPLATE_VERSION: Final[str] = "cad-2026.10.1"

class CadView(StrEnum):
    THEME             = "theme_v1"
    FACET_SITUATION   = "facet_situation_v1"
    FACET_EXPRESSION  = "facet_expression_v1"
    FACET_LANGUAGE    = "facet_language_v1"
    FACET_PRODUCT     = "facet_product_v1"
    RETRIEVAL         = "retrieval_v1"
    COMMERCE_THEME    = "commerce_theme_v1"

CLUSTERING_VIEWS: Final[frozenset[CadView]]   # every view except RETRIEVAL

@dataclass(frozen=True, slots=True)
class CadDocument:
    text: str
    view: CadView
    template_version: str
    token_estimate: int
    content_hash: str                       # sha256(view || template_version || text)
    dropped_low_confidence: int
    truncated_fields: tuple[str, ...]
    is_empty: bool                          # no lines rendered -> item skipped for this view

def render_cad(analysis: ContentAnalysis, view: CadView) -> CadDocument: ...
def render_commerce_cad(analysis: CommerceAnalysis, *, price_band: str) -> CadDocument: ...
def normalise_value(raw: str) -> str: ...
def redact_brand_tokens(text: str, brand_surface_forms: Sequence[str]) -> str: ...
async def render_paraphrase_variant(                       # style probe (§1.5c)
    analysis: ContentAnalysis, view: CadView, *, llm: LLMClient
) -> CadDocument: ...
```

### 8.2 `aypo/analysis/embedding.py`

```python
class EmbeddingProvider(Protocol):
    name: str
    model: str
    dims: int
    @property
    def available(self) -> bool: ...
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

class OpenAIEmbeddingProvider(EmbeddingProvider): ...   # text-embedding-3-large, dimensions=1536
class JinaEmbeddingProvider(EmbeddingProvider): ...      # fallback; DIFFERENT identity_epoch

def resolve_provider(settings: Settings) -> EmbeddingProvider: ...
def identity_epoch(provider: EmbeddingProvider, view: CadView, template_version: str) -> str: ...

@dataclass(frozen=True, slots=True)
class EmbeddingRecord:
    item_kind: Literal["content", "commerce"]
    item_id: UUID
    view: CadView
    document: CadDocument
    vector: FloatArray          # L2-normalised, float32, len == provider.dims
    identity_epoch: str
    from_cache: bool

async def embed_documents(
    docs: Sequence[tuple[Literal["content", "commerce"], UUID, CadDocument]],
    *,
    provider: EmbeddingProvider,
    conn: Connection,
    ledger: Ledger,
    batch_size: int = 128,
    concurrency: int = 4,
) -> EmbeddingBatchResult: ...               # .records, .n_cached, .n_failed, .usd

@dataclass(frozen=True, slots=True)
class EmbeddingBatchResult:
    records: list[EmbeddingRecord]
    n_cached: int
    n_failed: int
    failed_item_ids: list[UUID]
    usd: float
```

### 8.3 `aypo/analysis/space.py`

```python
@dataclass(frozen=True, slots=True)
class ClusteringSpace:
    reducer: Literal["none", "pca64", "pca128", "umap15"]
    mean_: FloatArray                       # (1536,)
    components_: FloatArray | None          # (dims, 1536) for PCA; None for none/umap
    removed_pcs: int                        # k in all-but-the-top
    dims: int
    explained_variance_ratio: float
    fingerprint: str                        # sha256(mean_ || components_ || reducer || k)
    umap_state: bytes | None                # pickled fitted UMAP, run-local only; never reused
    nn_dist_q05: float
    eps_used: float

def fit_space(
    X: FloatArray, *, reducer: str, remove_top_pcs: int, target_dims: int, seed: int = 0
) -> ClusteringSpace: ...
def project(space: ClusteringSpace, X: FloatArray) -> FloatArray: ...   # renormalised output

@dataclass(frozen=True, slots=True)
class StyleProbeResult:
    top1_rate: float
    mean_self_cos: float
    margin: float
    passed: bool
    marginal: bool

def run_style_probe(
    space: ClusteringSpace, base: FloatArray, variants: FloatArray
) -> StyleProbeResult: ...

@dataclass(frozen=True, slots=True)
class ReducerChoice:
    space: ClusteringSpace
    report: list[CandidateScore]            # ari_core, ari_with_noise, dbcv, noise_fraction, n_clusters
    degraded: bool
    degraded_reason: str | None

def select_reducer(
    X: FloatArray, *, n_items: int, candidates: Sequence[str], bootstraps: int = 10, seed: int = 0
) -> ReducerChoice: ...

def choose_removed_pcs(
    X: FloatArray, base: FloatArray, variants: FloatArray, *, max_k: int = 3
) -> int: ...
```

### 8.4 `aypo/analysis/clustering.py`

```python
@dataclass(frozen=True, slots=True)
class HdbscanConfig:
    min_cluster_size: int
    min_samples: int
    cluster_selection_epsilon: float
    oversize_cap: int                              # enforced post hoc; NOT max_cluster_size
    metric: str = "euclidean"                      # on L2-normalised vectors == cosine
    algorithm: str = "brute"                       # REQUIRED for determinism (§2.1)
    cluster_selection_method: Literal["leaf", "eom"] = "leaf"
    allow_single_cluster: bool = False
    copy: bool = True

def choose_config(n_items: int, space: ClusteringSpace) -> HdbscanConfig: ...

@dataclass(frozen=True, slots=True)
class RunCluster:
    raw_label: int
    member_indices: tuple[int, ...]                # into the run's item array
    centroid_1536: FloatArray
    centroid_run_space: FloatArray
    medoid_index: int
    exemplar_indices: tuple[int, ...]
    inlier_radius_cos: float
    mean_intra_cos: float
    persistence: float
    provisional: bool = False

@dataclass(frozen=True, slots=True)
class ClusterResult:
    clusters: tuple[RunCluster, ...]
    labels: npt.NDArray[np.int32]                  # -1 for noise
    probabilities: FloatArray
    outlier_scores: FloatArray
    noise_indices: tuple[int, ...]
    noise_fraction_primary: float                  # first pass (§2.8)
    noise_fraction: float                          # after recovery
    config: HdbscanConfig
    adjustments: tuple[str, ...]                   # the <=2 min_samples repairs (§2.5)
    high_noise: bool

def run_clustering(Xr: FloatArray, X1536: FloatArray, cfg: HdbscanConfig) -> ClusterResult: ...
def recover_noise(Xr: FloatArray, X1536: FloatArray, noise_idx: Sequence[int]) -> tuple[RunCluster, ...]: ...

@dataclass(frozen=True, slots=True)
class SoftMembership:
    item_index: int
    cluster_raw_label: int
    weight: float
    cos_to_centroid: float

def soft_memberships(
    X1536: FloatArray, clusters: Sequence[RunCluster], labels: npt.NDArray[np.int32],
    *, min_weight: float = 0.15, max_per_item: int = 3,
) -> list[SoftMembership]: ...

def build_rollups(clusters: Sequence[RunCluster], *, cut_cos: float = 0.80) -> list[tuple[int, ...]]: ...
    # scipy works in DISTANCE: linkage(..., metric="cosine") then
    # fcluster(Z, t=1.0 - cut_cos, criterion="distance"). Passing cut_cos to fcluster
    # directly cuts at cosine distance 0.80 (cosine 0.20) and merges nearly everything.
def enforce_oversize_cap(result: ClusterResult, Xr: FloatArray, X1536: FloatArray,
                         *, cap: int) -> ClusterResult: ...     # §2.5, one recursion level
def brand_leakage_scan(cads: Sequence[CadDocument], labels: npt.NDArray[np.int32],
                       known_brand_forms: Sequence[str]) -> dict[int, list[str]]: ...  # §1.2
def collapse_near_duplicates(X1536: FloatArray, *, quantile: float = 0.999,
                             prior: float = 0.98) -> DedupResult: ...
def field_ablation_audit(cads: Sequence[CadDocument],
                         labels: npt.NDArray[np.int32]) -> dict[int, AblationReport]: ...
```

### 8.5 `aypo/analysis/labeling.py`

```python
@dataclass(frozen=True, slots=True)
class InspectionMember:
    ref: str                     # "m01" .. "m15" — opaque, so the model cannot cite an item id
    item_id: UUID
    cad_text: str
    stratum: Literal["centroid", "periphery", "random"]

@dataclass(frozen=True, slots=True)
class MemberSample:
    cluster_key: str
    inspected: tuple[InspectionMember, ...]       # <= 15
    holdout: tuple[InspectionMember, ...]         # <= 8, disjoint
    is_null_control: bool = False

def sample_members_for_inspection(
    cluster: RunCluster, X1536: FloatArray, cads: Sequence[CadDocument],
    item_ids: Sequence[UUID], *, n_centroid: int = 6, n_periphery: int = 4,
    n_random: int = 5, n_holdout: int = 8, seed: int | None = None,
) -> MemberSample: ...

class SharedAttribute(BaseModel): ...        # field / attribute / refs   (§3.2)
class ClusterLabel(BaseModel): ...           # full schema in §3.2
class LabelVerdict(BaseModel):
    per_ref: dict[str, Literal["yes", "no", "partly"]]
    failing_refs: list[str]
    no_rate: float

async def label_cluster(sample: MemberSample, *, llm: LLMClient,
                        effort: Effort = "high") -> Result[ClusterLabel]: ...
async def verify_label(label: ClusterLabel, sample: MemberSample, *, llm: LLMClient,
                       effort: Effort = "medium") -> Result[LabelVerdict]: ...

@dataclass(frozen=True, slots=True)
class LabelDecision:
    accepted: bool
    label: str | None
    description: str | None
    label_status: str
    label_confidence: Literal["low", "medium", "high"] | None
    support_rate: float
    dissent_rate: float
    verification_no_rate: float | None
    split_recommended: bool

def accept_label(raw: ClusterLabel, verdict: LabelVerdict | None, sample: MemberSample,
                 *, banned_tokens: Sequence[str],
                 ablation: AblationReport) -> LabelDecision: ...

async def label_clusters(samples: Sequence[MemberSample], *, llm: LLMClient,
                         banned_tokens: Sequence[str],
                         concurrency: int = 4) -> LabelBatchResult: ...
                         # injects exactly one null control per batch (§3.6)
```

### 8.6 `aypo/intelligence/identity.py`

```python
@dataclass(frozen=True, slots=True)
class StableCluster:
    cluster_id: UUID
    cluster_key: str
    centroid_1536: FloatArray
    member_item_ids: frozenset[UUID]
    status: str
    label: str | None

@dataclass(frozen=True, slots=True)
class MatchScore:
    cos: float
    jaccard: float
    score: float
    n_shared: int
    n_parent_surviving: int

@dataclass(frozen=True, slots=True)
class IdentityDecision:
    run_cluster_raw_label: int
    cluster_id: UUID | None                 # None -> caller mints a new stable id
    decision: Literal["continuation", "ambiguous", "birth", "split_child",
                      "merge_child", "budding_continuation", "epoch_break"]
    matched: MatchScore | None
    lineage: tuple[LineageEdge, ...]

@dataclass(frozen=True, slots=True)
class LineageEdge:
    parent_cluster_id: UUID | None
    relation: str
    weight: float | None
    score: MatchScore | None
    note: str | None

@dataclass(frozen=True, slots=True)
class IdentityResolution:
    decisions: tuple[IdentityDecision, ...]
    deaths: tuple[tuple[UUID, str], ...]    # (cluster_id, death_kind)
    resurrections: tuple[tuple[UUID, int], ...]

def score_pair(new: RunCluster, prev: StableCluster, *,
               corpus_now: frozenset[UUID], corpus_prev: frozenset[UUID],
               cos_weight: float = 0.60) -> MatchScore: ...

def match_clusters(
    new: Sequence[RunCluster], prev: Sequence[StableCluster], *,
    new_member_ids: Sequence[frozenset[UUID]],
    corpus_now: frozenset[UUID], corpus_prev: frozenset[UUID],
    identity_epoch_now: str, identity_epoch_prev: str,
    tau_match: float = 0.72, tau_ambiguous: float = 0.60,
    cos_floor: float = 0.85, jaccard_floor: float = 0.30,
    split_coverage: float = 0.60, budding_dominant: float = 0.85,
    merge_contribution: float = 0.25, cos_weight: float = 0.60,
) -> IdentityResolution: ...

def diagnose_death(prev: StableCluster, result: ClusterResult,
                   corpus_now: frozenset[UUID]) -> str: ...        # -> cluster_death_kind

def series_stability(current: Sequence[ThemePeriod],
                     previous: Sequence[ThemePeriod]) -> tuple[float, float, bool]: ...

def calibrate_identity_thresholds(X1536: FloatArray, cfg: HdbscanConfig,
                                  space: ClusteringSpace, *, bootstraps: int = 30,
                                  growth_holdout: float = 0.20) -> IdentityCalibration: ...
```

### 8.7 `aypo/intelligence/trends.py`

```python
Stratum = Literal["reference", "category", "widened"]
REFERENCE_STRATEGIES: Final = ("brand", "category")
CATEGORY_STRATEGIES:  Final = ("category",)

@dataclass(frozen=True, slots=True)
class ThemePeriod:
    period_start: date
    n_theme: int
    n_corpus: int
    share: float | None
    ci_low: float | None
    ci_high: float | None
    n_unique_creators: int
    max_creator_share: float | None
    is_qualifying: bool
    exclusion_reason: str | None
    platform_breakdown: Mapping[str, int]

def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]: ...

def build_timeseries(
    members: Sequence[MemberRow], corpus: Sequence[CorpusRow], *,
    stratum: Stratum, period_kind: Literal["month", "quarter"],
    collected_at: datetime, tz: str,          # settings.aypo_market_timezone -- no default,
                                              # so a second market cannot silently inherit one
    window_months: int = 12, max_window_months: int = 18,
    min_period_n: int = 40, lag_days: int = 7,
) -> list[ThemePeriod]: ...

def choose_period_kind(corpus: Sequence[CorpusRow], *, median_floor: int = 60) -> Literal["month", "quarter"]: ...

# ── inference ─────────────────────────────────────────────────────────────────
def cochran_armitage(n_theme: FloatArray, n_corpus: FloatArray, t: FloatArray) -> float: ...

@dataclass(frozen=True, slots=True)
class PermutationPlan:
    """Built once per (run, space, stratum); shared across all themes (§5.6, §6.6).

    The permutation INDEX MATRIX is deliberately not a field: (10_000, 4000) int32 is 160 MB
    held for the whole run, which contradicts the chunked-execution claim it was paired with.
    Chunks are regenerated deterministically from `seed`; sharing across themes is guaranteed
    by the seed, not by keeping an array alive.
    """
    block_ids: npt.NDArray[np.int64]
    block_kind: Literal["creator", "query", "listing"]     # listing for commerce (§6.7)
    exchange_stratum: npt.NDArray[np.int64]  # (platform, exact block size) -> stratum id (§5.6)
    n_exchangeable_blocks: int               # blocks in strata of size >= 2; this is `G`
    platform_index: npt.NDArray[np.int32]    # CA statistic is summed over platform strata
    period_index: npt.NDArray[np.int32]
    seed: int
    n_permutations: int

    def chunks(self, size: int = 1000) -> Iterator[npt.NDArray[np.int32]]: ...

def build_permutation_plan(corpus: Sequence[CorpusRow], *, n_permutations: int = 10_000,
                           seed: int) -> PermutationPlan: ...

@dataclass(frozen=True, slots=True)
class TrendStats:
    statistic: float
    p_permutation: float
    beta1: float | None
    or_per_period: float | None
    or_ci: tuple[float, float] | None
    effect_estimable: bool                   # False -> gate 4, never growing/declining (§5.6)
    direction_consistent: bool               # sign(statistic) == sign(beta1)
    n_blocks: int                            # exchangeable blocks only
    null_p: FloatArray                       # (R,) THIS theme's per-permutation p-values,
                                             # obtained by within-column ranking of the (R, J)
                                             # statistic matrix. min-p is a FAMILY quantity and
                                             # is computed by westfall_young over all themes.

def trend_test(periods: Sequence[ThemePeriod], membership: npt.NDArray[np.bool_],
               plan: PermutationPlan) -> TrendStats: ...
def onset_test(periods: Sequence[ThemePeriod], *, onset_period: date) -> OnsetStats: ...
def logistic_slope_firth(y: npt.NDArray[np.bool_], t: FloatArray,
                         max_iter: int = 50, tol: float = 1e-8) -> tuple[float, float, bool]: ...
    # Jeffreys-penalised IRLS: keeps beta1 finite under quasi-complete separation and
    # shrinks toward zero, which is the conservative direction (§5.6).
def design_effect(blocks: npt.NDArray[np.int64],
                  membership: npt.NDArray[np.bool_]) -> float: ...      # deff_hat (§5.7)
def bootstrap_or_ci(y: npt.NDArray[np.bool_], t: FloatArray, blocks: npt.NDArray[np.int64],
                    *, draws: int = 2000, seed: int) -> tuple[float, float]: ...
def mde_or_80(periods: Sequence[ThemePeriod], plan: PermutationPlan, *,
              grid: Sequence[float] = (1.1, 1.15, 1.25, 1.4, 1.6, 2.0, 2.5, 3.0),
              sims: int = 200, alpha: float = 0.05, power: float = 0.80) -> float | None: ...

def bh_adjust(p: Sequence[float], *, q: float = 0.10) -> list[float]: ...
def westfall_young_stepdown(p_obs: Sequence[float],
                            null_p: Sequence[FloatArray]) -> list[float]: ...
    # STEP-DOWN minP, not single-step: the j-th ordered hypothesis takes the per-permutation
    # minimum over the REMAINING hypotheses only, then a running maximum enforces monotonicity.

# ── classification ────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class ThemeFacts:
    cluster_id: UUID
    size_core: int
    n_dated: int
    undated_fraction: float
    periods: tuple[ThemePeriod, ...]
    max_creator_share: float | None
    n_creator_blocks: int
    provisional: bool
    is_rollup: bool
    status: str
    series_unstable: bool
    run_degraded: bool
    run_high_noise: bool
    stratum: Stratum
    stratum_disagreement: bool
    platform_mix_shift: bool

@dataclass(frozen=True, slots=True)
class TrendVerdict:
    classification: TrendClass
    replication_status: ReplicationStatus | None
    stats: TrendStats | None
    q_bh: float | None
    p_wy: float | None
    family_size: int
    mde_or_80: float | None
    s_high_used: float | None
    refusal_reason: str | None
    guard_flags: Mapping[str, bool]

def classify_theme(facts: ThemeFacts, stats: TrendStats | None, onset: OnsetStats | None,
                   *, q_bh: float | None, p_wy: float | None, family_size: int,
                   s_high: float, prior_verdict: TrendVerdict | None,
                   min_members_for_direction: int = 15,
                   min_qualifying_periods: int = 4,
                   min_recent_periods: int = 2,
                   or_floor_up: float = 1.25, or_floor_down: float = 0.80,   # monthly;
                   # quarterly callers pass 1.95 / 0.51 -- period-normalised (§5.5)
                   share_floor: float = 0.02, fdr_q: float = 0.10,
                   established_min_members: int = 30) -> TrendVerdict: ...

def compute_s_high(verdicts: Mapping[UUID, Sequence[ThemePeriod]],
                   *, quantile: float = 0.80, fallback: float = 0.10,
                   min_themes: int = 10) -> float: ...
```

### 8.8 `aypo/intelligence/claim_guard.py`

```python
class RefusalReason(StrEnum):
    VOLUME_CLAIM_UNIDENTIFIED   = "volume_claim_unidentified"
    OUT_OF_SCOPE_POPULATION     = "out_of_scope_population"
    PERIOD_UNDERPOWERED         = "period_underpowered"
    INSUFFICIENT_COVERAGE       = "insufficient_coverage"
    LEFT_TRUNCATION_UNRESOLVED  = "left_truncation_unresolved"
    SERIES_UNSTABLE             = "series_unstable"
    CREATOR_CONCENTRATED        = "creator_concentrated"
    PLATFORM_MIX_SHIFT          = "platform_mix_shift"
    STRATUM_DISAGREEMENT        = "stratum_disagreement"
    CLUSTER_NOT_CLAIMABLE       = "cluster_not_claimable"
    COMMERCE_TEMPORAL_UNIDENTIFIED = "commerce_temporal_unidentified"
    NO_EXTRAPOLATION            = "no_extrapolation"
    NO_CAUSAL_INFERENCE         = "no_causal_inference"
    NO_COMPOSITE_SCORES         = "no_composite_scores"

@dataclass(frozen=True, slots=True)
class Claim:
    kind: Literal["share_point", "share_change", "within_period_ranking",
                  "onset", "brand_composition", "no_change", "cluster_fact"]
    text: str                       # fully rendered, scope sentence included
    cluster_id: UUID
    period_start: date | None
    sample_sizes: Mapping[str, int]
    method: str                     # human-readable: test, family size, q, stratum
    evidence_item_ids: tuple[UUID, ...]
    confidence: Literal["low", "medium", "high"]

@dataclass(frozen=True, slots=True)
class Refusal:
    reason: RefusalReason
    explanation: str                        # plain language, shown in the UI
    weaker_alternative: Claim | None        # prefer a weaker true claim over a blank

def share_point_claim(cluster: ClusterRow, period: ThemePeriod) -> Claim | Refusal: ...
def share_change_claim(cluster: ClusterRow, verdict: TrendVerdict,
                       periods: Sequence[ThemePeriod]) -> Claim | Refusal: ...
def onset_claim(cluster: ClusterRow, verdict: TrendVerdict,
                periods: Sequence[ThemePeriod]) -> Claim | Refusal: ...
def no_change_claim(cluster: ClusterRow, verdict: TrendVerdict) -> Claim | Refusal: ...
def within_period_ranking(period_start: date,
                          themes: Sequence[tuple[ClusterRow, ThemePeriod]]) -> list[Claim]: ...
def brand_composition_claim(cluster: ClusterRow, brand_id: UUID,
                            period: ThemePeriod | None) -> Claim | Refusal: ...
```

The narrative layer's contract: `render_narrative(claims: Sequence[Claim]) -> str`. It receives
`Claim.text` and may rephrase; it never receives `ThemePeriod` rows. A model handed a table of
monthly counts will describe a trend, because that is what a table of monthly counts looks like
— so it is not handed one.

---

## 9. Job orchestration, degradation and cost

### 9.1 One job, idempotent

`aypo/jobs/cluster_and_trend.py` registers job type `cluster_and_trend` on the Postgres-backed
queue (no Redis, no Celery). Payload: `{research_run_id, corpus, space, force}`.

```
pg_advisory_xact_lock(hashtext('cluster:' || research_run_id || ':' || space_key))
  --- OUTSIDE the transaction (network-bound, idempotent, cache-keyed) ---
  1. load analysed items for the space
  2. render + embed any missing CADs (cache by content_hash)
  3. select / load the clustering space; run the §1.5 probes if the template, the analysis
     prompt version or the reducer pin changed
  --- CPU only, no network ---
  4. dedup collapse
  5. HDBSCAN (+ <=2 min_samples repairs) + oversize-cap repair + noise recovery
     + soft membership + rollups
  6. field-ablation audit + brand-leakage scan
  7. PROVISIONAL identity match (geometry + membership) -- label-reuse decisions ONLY,
     never persisted, never written to cluster_lineage               (§2.5)
  --- OUTSIDE the transaction (LLM) ---
  8. label new and drifted clusters (+ null control); verify on holdout
  9. label-aware post-merge                                          (§2.5)
  --- TRANSACTION: steps 10-14 ---
 10. AUTHORITATIVE identity match on the post-merge cluster set
 11. upsert clusters / cluster_revisions / cluster_lineage / cluster_members
 12. build timeseries for each stratum; series-stability check
 13. permutation plan -> stratified trend tests -> BH + Westfall-Young step-down -> classify
 14. write theme_timeseries / theme_trend_tests; update clusters denormalised verdicts;
     cluster_links; mark cluster_runs status; write the ledger
```

**The ordering of 7 / 8 / 9 / 10 is the fix for a circularity in the first draft**, which ran
identity at step 7 and wrote lineage at step 8 but labelled at step 9 — so the label-aware
post-merge mutated the cluster set that lineage had already been written against, while §3.7's
label reuse needed identity to exist *before* labelling. Splitting identity into a throwaway
provisional pass and one authoritative pass over the final cluster set resolves it without
weakening either rule.

The advisory lock is taken for the whole job and prevents two runs re-clustering the same space
concurrently, which would otherwise interleave identity matching against a half-written previous
run and corrupt lineage. Every write is an upsert keyed on `(research_run_id, space)`, so a
retried job is a no-op on already-completed steps. **Only steps 10–14 are inside the database
transaction.** The first draft put steps 1–8 in one transaction, which would have held an
advisory lock and an open transaction across thousands of OpenAI embedding calls and the LLM
probe — minutes of idle-in-transaction on a managed Postgres. Steps 1–3 are cache-keyed and
idempotent, so re-running them after a crash costs nothing; labelling is outside because it is
slow and labels are nullable by design (an unlabelled cluster is a valid state, §3.4).

### 9.2 Graceful degradation (brief §7)

| failure | behaviour |
|---|---|
| OpenAI embeddings unavailable | if Jina is configured: embed the **whole** space with Jina under a new `identity_epoch`, lineage `epoch_break`. If neither: the job exits `status='failed'` without writing clusters; the previous run's clusters remain current and the UI shows their `computed_at`. Never a mixed-provider space. |
| partial embedding failure | cluster on what exists and record `embedding_coverage`. If coverage `< 0.70`, write `status='preview'`: clusters are stored for inspection, **no stable identities are published, no lineage is written, no trend tests run.** A partial corpus distorts density and would fabricate births and deaths. |
| a collector was unavailable this run (no `APIFY_TOKEN`, platform down) | clustering proceeds. `platform_mix_shift` will likely fire (§5.7), downgrading growth claims to `exploratory`, which is the honest response to the corpus composition having changed for reasons unrelated to the world. |
| `YOUTUBE_API_KEY` absent | sentinel exposure curve is skipped; `exposure_checks.sentinel='unavailable'`. Nothing else changes, because we never divide by `ĝ` (§5.3B). |
| LLM refusal / unavailable during labelling | affected clusters stay `unlabelled` and are excluded from classification. The run still completes and still produces a valid partition. |
| `umap-learn` not installed | `umap15` is dropped from the candidate list; `pca64` is the default winner. Already the documented intent in `pyproject.toml`. |
| subsystem 03 cannot produce analysis replicates | probe (c1) records `unavailable`, (c2) alone gates, `style_probe_partial=true` on every run (§1.5c) |
| `reference_query_set_version` changed since the last run | clustering and within-run statistics are unaffected; `replication_status` resets to `unreplicated` and cross-run growth comparison is suppressed for one run (§5.2) |
| `creator_id` missing for most items | blocks fall back to `discovery_query_id` (`block_kind='query'`), `deff_hat` is estimated on the weaker blocking, and `max_creator_share` is recorded as `null` rather than 0 — an unmeasurable concentration guard must not read as a passed one (§12.4) |
| run budget exhausted mid-labelling (`aypo_max_llm_usd`) | stop labelling, leave the rest `unlabelled`, record `degraded_reason='budget'`. Clustering and trends are unaffected because they cost no LLM tokens. |

### 9.3 Cost at scale (~1500 videos + ~2500 commerce records)

| line | volume | cost |
|---|---|---|
| embeddings, all views (§1.8) | ~2.53M tokens @ $0.13/M | **$0.33** |
| paraphrase probe c2 (once per CAD template, not per run) | 200 perturbed records @ `effort="low"` + 400 embeddings | **$2.51** |
| replicate probe c1 (once per **analysis prompt** version) | 150 re-analyses, billed to subsystem 03's analysis cost | **see note** |
| labelling, global space | ~40 clusters × 15 × 380 tok in ≈ 0.23M; out ~1.75k/cluster (incl. adaptive thinking at `effort="high"`) ≈ 0.07M | $1.14 in + $1.75 out = **$2.89** |
| labelling, 2 facet spaces | ~50 clusters × 15 × 100 tok in ≈ 0.08M; out ≈ 0.09M | $0.40 + $2.25 = **$2.65** |
| labelling, commerce space | ~25 clusters × 15 × 220 tok ≈ 0.08M in; out ≈ 0.04M | $0.40 + $1.00 = **$1.40** |
| label verification (`effort="medium"`) | ~115 clusters × ~3k in, ~0.7k out | $1.73 + $2.01 = **$3.74** |
| null-label controls | 4 batches | **≈$0.15** |
| reducer-selection `label_support_rate` probe | 2 candidates × 8 clusters | **≈$0.60** |
| **total LLM + embeddings per full run** | | **≈ $11.7** (≈$14.2 including the one-off style probe) |

The replicate probe (c1) is deliberately not folded into that total: it is 150 re-runs of
subsystem 03's analysis pass, which is by far the most expensive per-item operation in the
pipeline, and pricing it here would understate it. At an analysis cost of $0.05–0.15 per item it
is **$8–23, once per `analysis_prompt_version`** — comfortably affordable against the run
budget, but it is a real line that must be approved rather than discovered. If it is not
approved, (c1) is skipped and every run carries `style_probe_partial=true`, which is a weaker
guarantee that the space is not clustering by analysis-pass artefacts.

Against `aypo_max_llm_usd = 120` the recurring total is ~10% of the run budget. Levers, in order: drop label
verification to `effort="low"` (−$2), disable the optional facets (−$1.3 each), reuse labels on
continuation (already the default, §3.7, and the dominant saving on re-runs — a second run over a
slowly-growing corpus labels only new and drifted clusters, typically 20–30% of them, bringing
labelling to ~$3).

**Compute** (CPU only, no GPU, no Docker), with the two figures that changed after measurement:
PCA SVD on 4000×1536 ≈ 1 s; **HDBSCAN on 4000×64 with `algorithm="brute"` ≈ 0.25 s but ≈0.4 GB
additional peak RSS** (measured; the tree algorithms are no faster here and are not
reproducible, §2.1); reducer harness (4 candidates × 10 bootstraps) ≈ 60–100 s; soft membership
4000×~40 centroids ≈ instant; 10,000 permutations regenerated from the seed in 1000-permutation
chunks ≈ 5–15 s at **≈16 MB resident** (the earlier "≈160 MB peak in chunks" figure was the cost
of *not* chunking); the retained (R × J) statistic matrix for Westfall–Young ≈ 4.6 MB; MDE
bootstrap ≈ 15 s. Peak memory is dominated by `brute` HDBSCAN at well under 1 GB; whole job
under 5 minutes, which fits a single Postgres-queue worker.

---

## 10. Validation and acceptance tests

Tests that must exist, because each one guards a specific way this subsystem can be
quietly wrong. All fixtures use generic placeholders (`Brand A`, `Theme X`, `Cluster Y`) and
synthetic or procedurally generated content — **no fixture encodes any real consumer, occasion,
competitor, slang term or category assumption** (brief §34).

1. **Determinism.** Same inputs + same `space_config.fingerprint` ⇒ byte-identical labels,
   centroids and lineage decisions. Run twice in one process and once in a subprocess.
2. **CAD stability.** Permuting the order of values inside an analysis field produces an
   identical `content_hash`. Perturbing confidences produces an identical hash **iff the
   perturbation crosses neither a 0.02 bucket edge (§1.3.4) nor the 0.5 gate (§1.3.5)**; a
   bucket-crossing perturbation must change the hash, and that direction is asserted too. The
   first draft asserted invariance to any perturbation "<0.02", which is false for a
   quantisation scheme and would have been fixed by weakening the quantisation.
3. **Brand leakage.** Property test: injecting an arbitrary brand surface form into every
   analysis record must not change any clustering-view CAD. Guards §1.2.
4. **Cosine/Euclidean equivalence, with `algorithm` pinned.** On random unit vectors, HDBSCAN
   labels under `metric="euclidean", algorithm="brute"` equal labels under a precomputed
   cosine-distance matrix. The test must pin the algorithm: measured on sklearn 1.9.1, the
   default `algorithm="auto"` gives **ARI 0.969, not 1.0**, so this test as first written
   *fails against a correct implementation* and would have been "fixed" by weakening the
   assertion rather than by pinning the algorithm. A companion test asserts the regression
   directly — `auto` disagrees, `brute` does not — so the pin cannot be dropped silently.
5. **The sampling-bias regression test — the most important test in the subsystem.**
   Generate a synthetic corpus where every theme has *constant* real-world prevalence, then apply
   an exponential exposure function `g(t) = 1.5^(months_from_oldest)` when sampling. Assert:
   (a) a naive per-month count shows positive slope for ≥90% of themes; (b) **the share-based
   permutation test rejects for no more than 10% of themes at `q = 0.10`** — i.e. our estimator
   does not inherit the bias the naive one does. Then repeat with one theme given genuine 1.6×/
   month growth and assert it is detected.
6. **Query-conditioned oversampling test.** Same generator, but one theme additionally receives
   strategy-`theme` items concentrated in recent periods. Assert the reference-stratum series
   does not flag it, and the widened stratum does — which is the precise justification for §5.2.
7. **Left truncation.** A theme present throughout, in a corpus whose early periods have
   `N_eff(p) < 40`, must classify `insufficient_data` with `left_truncation_unresolved`, never
   `emerging`. A companion case holds `N(p) = 60` fixed and raises `deff_hat` from 1 to 2: the
   same corpus must flip from `emerging` to `insufficient_data`.
8. **Identity: split does not become decline.** Construct a cluster, then split it in the next
   run; assert both children are new ids, the parent is `status='split'`, and **no theme in that
   space receives a `declining` verdict**.
9. **Identity: corpus churn does not become decline.** Delete 60% of a theme's members from the
   corpus; assert `death_kind='corpus_churn'` and no decline claim anywhere.
10. **Identity: budding.** 90/10 split ⇒ continuation + birth, not split.
11. **Resurrection.** Dormant then re-matching ⇒ same stable id, gap recorded, gap periods
    non-qualifying.
12. **Multiplicity.** Two assertions, because the first draft's single one was vacuous — under
    the *complete* null BH makes any discovery with probability ≤ q, so "no more than 6 flagged
    themes in expectation over 100 seeds" is satisfied by an implementation that never flags
    anything and by several that are badly broken.
    (a) *Complete null*: with 60 null themes, `P(at least one BH discovery) ≤ 0.10` and
    `P(at least one WY discovery) ≤ 0.05`, each within Monte-Carlo error over 500 seeds; and the
    uncorrected permutation p-values pass a Kolmogorov–Smirnov test for uniformity.
    (b) *Mixed null*: with 50 null and 10 truly-trending themes, the realised FDR among BH
    discoveries is ≤ 0.10 averaged over seeds — which is the property BH actually claims and
    the only one that distinguishes a working implementation from a silent one.
13. **Noise accounting.** `n_core + n_noise == n_items_clustered` **after** noise recovery has
    converted recovered points from `noise` to `core` (§2.8.1) — the invariant is unsatisfiable
    if a recovered point keeps both rows. Asserted alongside
    `noise_fraction_primary >= noise_fraction`. The `noise_has_no_cluster` constraint rejects
    both malformed directions, and `cluster_members_one_primary` rejects double-core and
    core-plus-noise (both of which a live Postgres accepted before that index existed).
14. **Multi-membership counting.** With secondary memberships present, every series, test and
    sample size must be unchanged from the core-only computation. Guards §2.6's counting rule.
15. **Claim guard.** Every `RefusalReason` has a test producing it, and a fuzz test asserts that
    no `Claim.text` from any code path contains volume-trend phrasing, a population claim, a
    causal connective, or a bare percentage without its denominator.
16. **Null-label control.** A cluster assembled from randomly drawn noise points must be refused
    by the labeller (run against the live model, marked as an external-dependency test).
17. **Degradation.** Embedding coverage 0.5 ⇒ `status='preview'`, zero rows in
    `cluster_lineage` and `theme_trend_tests`.
18. **DDL applies.** The migration runs end to end against a Postgres with `vector` and
    `pg_trgm`, and the four behavioural guards are asserted by inserting the rows they must
    reject: two core memberships for one item, core-plus-noise, a `space_key` that disagrees
    with `(corpus, space)`, and a `research_runs` delete while clusters reference the run. A
    fifth asserts the row that must be *accepted* — two different items with byte-identical CAD
    text.
19. **Design effect propagates.** On a synthetic corpus with a known intra-creator correlation,
    `design_effect()` recovers `deff` within 20%, and a period with `N = 60, deff_hat = 2` is
    non-qualifying while the same period with singleton blocks qualifies. Guards the §6.2 /
    §6.4 restatement onto `N_eff`.
20. **Period-kind normalisation.** The same synthetic series bucketed monthly and quarterly
    yields the same verdict: a theme at a true 1.25×/month rate is `growing` in both regimes,
    and a theme at 1.10×/month is `stable` in both. Guards the §5.5 effect-floor
    normalisation — without it the quarterly path announces trends the monthly path refuses.
21. **Oversized cluster.** A corpus containing one blob holding 50% of the items must not emit a
    labelled cluster of that size: it is either split or demoted with
    `demotion_reason='oversized'` and `trend_eligible=false`. Guards the §2.5 repair, which
    exists because `max_cluster_size` is silently ignored under `leaf`.
22. **Label gates are computed, not reported.** Given a `ClusterLabel` whose `supporting_refs`
    include an id not in the sample, overlap `dissenting_refs`, or cite items that do not
    contain the accepted attributes, `accept_label()` must reject or recompute — and the
    resulting `support_rate` must match an independent recomputation. Guards §3.2.
23. **Brand leakage.** Injecting a synthetic brand token into the `QUOTE` field of one cluster's
    members must (a) not appear in any clustering-view CAD, and (b) if redaction is disabled,
    be caught by `brand_leakage_scan` at `q ≤ 0.10`. Guards §1.2 in both directions.

---

## 11. Cross-subsystem contracts

Phrased as requirements on other subsystems, because this design does not work without them.

**From analysis (02/03) — per analysed item:**

1. A structured analysis record matching brief §20, with **per-value confidence** for every
   open-ended field. Without per-value confidence the §1.3.5 gate cannot run and the CAD carries
   the model's low-confidence guesses into the geometry.
2. A **`representative_quote`**: a ≤30-word verbatim span of source text (transcript, caption or
   OCR) chosen as most representative. It must be verbatim source text, not model prose
   (§1.3.9), and the analysis pass must **prefer a span containing no brand surface form**,
   emitting none rather than a branded one if no clean span exists. Brand redaction is applied
   on top regardless (§1.2) — the preference reduces how often `QUOTE` has to be dropped, it is
   not the guarantee.
3. **Brand surface forms** as a separate list (`visible_brands`, `competitors`, plus the surface
   forms as they appeared) so the redaction in §1.2 can be exact rather than fuzzy.
4. **Slang/lexicon values preserved in the original language with a short English gloss**
   (§1.3.8).
5. `analysis_status='complete'` as a hard gate; partially analysed items must never be embedded,
   because a half-filled CAD clusters by its missingness pattern.

**From collection (01) — per item:**

6. `discovery_strategy ∈ {brand, category, competitor, theme, hypothesis}` (brief §8 A–E) on
   **every** item. This is the single most load-bearing field in the trend design (§5.2); without
   it the reference stratum cannot be formed and *no* growth claim is defensible.
6b. A **frozen, versioned reference query set**: the strategy-A and strategy-B queries are a
   stored artefact with a `reference_query_set_version`, re-executed verbatim every run, and any
   addition or edit bumps the version. Theme-conditioning is only half the problem (§5.2); if
   the A/B query set drifts between runs then `r_θ` drifts with it, and every between-run
   comparison — `series_stability`, `replicated`, the whole replication ladder — is comparing
   two different instruments. Collection must report the version on every run.
7. `publication_date` with explicit `date_precision ∈ {day, month, unknown}`. Do not impute a
   date; do not fall back to the collection date. An imputed date lands every undated item in the
   most recent period, which is the single fastest way to manufacture growth.
8. `creator_id` (or a stable per-platform creator key) wherever the platform exposes it, plus
   `discovery_query_id` always. These are the permutation blocks (§5.6); without at least one the
   p-values are anti-conservative by the design effect. Collection must also report
   **creator-id coverage per platform**, because a block structure that is good on one platform
   and absent on another yields a `deff_hat` that is right on average and wrong everywhere.
9. A **period-stratified collection target** where the source allows it: ≥80 reference-stratum
   dated items per month for each of the last 12 complete months (§5.3C). Where the source cannot
   filter by date, report that so `stratum_widened` / coverage can be recorded honestly.
10. A stable `dedup_group_id` when the collector can detect reposts/re-uploads; otherwise this
    subsystem falls back to embedding-cosine dedup (§1.8).
11. A **corpus snapshot** per research run: the exact set of item ids in scope, so §4.3's
    "intersection of the two corpora" is well defined and `corpus_churn` is distinguishable from
    decline.

**From the job queue (infrastructure):**

12. Postgres-backed queue with at-least-once delivery, a job type `cluster_and_trend`, and
    support for transactional advisory locks (§9.1).

**From analysis (03) — one capability, not a per-item field:**

12b. An **analysis replicate mode**: the ability to re-run the analysis pass over a named set of
    ~150 items and return the second structured record without overwriting the first. This is
    the primary invariance probe (§1.5c1) and the only check that tests the perturbation
    production actually exhibits. If it cannot be provided, say so explicitly — the design
    degrades to the paraphrase probe alone and records `style_probe_partial` on every run,
    which is a materially weaker guarantee that clusters are not analysis-pass artefacts.

**To competitive analysis (05):**

13. Brand-by-theme comparisons must be computed on `membership_role='core'` **and**
    `discovery_strategy='category'` only (§5.2), per period, with **creator-clustered**
    intervals (Wilson widened by `deff_hat`, §5.7) and the distinct-creator count displayed
    beside the item count. Any *over-* or *under-*representation claim declares its
    themes × brands family and is BH-corrected at `q = 0.10`; any claim that one brand's share
    differs from another's within a theme requires a creator-blocked paired test (McNemar on the
    within-item 2×2) and may never be read off two overlapping or non-overlapping intervals
    (§5.10). Whole-theme brand shares that pool periods are a Simpson's-paradox trap and must
    not be surfaced.
14. Composite competitor scores are refused by `claim_guard` (brief §24). Use within-theme,
    within-period comparisons.

**To findings/hypotheses (06) and Ask the Data (07):**

15. Temporal statements may only come from `claim_guard`. The narrative layer receives `Claim`
    objects, never `theme_timeseries` rows (§5.10). **And the narrative layer's output is
    checked, not merely instructed**: every numeral in the rendered prose must appear in the
    input `Claim.text` set, and the output is scanned for causal connectives and comparative
    trend vocabulary absent from the input. "May only paraphrase" is a hope until something
    verifies it, and a paraphrase is exactly where a hedge gets dropped and a number gets
    rounded into a stronger statement.
16. Evidence must be deduplicated by `item_id` across spaces: a global theme and a facet theme
    containing the same items are **not** two independent pieces of evidence (§6.6c).
17. `Refusal` objects must be rendered, not dropped. A blank where a number is expected reads as
    "nothing is happening", which is itself a claim (brief §28).

**To the UI (31–33):**

18. Never present theme shares as a partition summing to 100% (facets and secondary memberships
    break it, and brief §33 bans pie-chart filler anyway).
19. `stable` must render as "no change detected" **with `mde_or_80`**, never as "flat" (§5.8).
20. Ambiguous and epoch-break discontinuities must be drawn as discontinuities, not interpolated
    (§4.4, §4.2).
21. Every chart axis is "share of analysed content published in period p", never "number of
    posts" (§5.4).
22. Both `size_core` and `size_core_reference` are shown wherever a theme's size is shown
    (§2.6). A user who sees one number next to a verdict computed from the other has been misled
    by omission.
23. Where `N(p)` is displayed, `N_eff(p)` is displayed with it (§6.2). "40 items" and "an
    effective sample of 20" are different statements and the second is the operative one.
24. When the Overview has no trend card that survives Westfall–Young, it says so and falls back
    to within-period composition facts. It must not promote a BH-only survivor into the headline
    slot to avoid an empty page (§5.8, §6.6a).

---

## 12. Open questions — genuinely unresolved

These are not placeholders for decisions I avoided; they are things that cannot honestly be
settled before a real corpus exists.

1. **PCA vs UMAP.** §2.2 argues hard for PCA and §2.4 defines a harness that can overrule it. I
   do not know which will win on this corpus, and the harness's own criterion (bootstrap ARI,
   now retention-adjusted) is still only a proxy for "clusters a human finds coherent". If UMAP
   wins decisively on `ari_core_adj` but `label_support_rate` prefers PCA, the tiebreak order in
   §2.4 picks the ARI, and I am not fully confident that is the right priority. What I *am* now
   confident of is that the two candidates are being scored on comparable evaluation sets, which
   the unadjusted `ari_core` did not guarantee — a high-noise candidate was previously graded on
   a smaller, easier subset.
2. **The common-exposure assumption is not testable with the data we have.** §5.3 states it,
   §5.9 attempts three falsifications, and none of them is a proof. If platform ranking favours
   some themes over others in a time-varying way, share trends are partly a measurement of the
   recommender. The honest position is that this is a *stated assumption with named consequences*,
   not a solved problem. If it matters commercially, the fix is a licensed data source with a
   date-complete denominator, not a cleverer statistic.
3. **Whether faceted clustering produces labelable clusters at this corpus size.** 90-token facet
   documents may not carry enough variance for HDBSCAN at n=1500, in which case facets fragment
   or collapse and the multi-membership mechanism falls back to soft membership alone (§2.6B).
   Decidable only empirically; the facets are config-gated for exactly this reason.
4. **`creator_id` availability across platforms.** Apify actor outputs vary by actor and by
   version. If creator identity is unreliable, permutation blocks degrade to `discovery_query_id`,
   which is a weaker dependence proxy, and the creator-concentration guard (§5.7) loses teeth. I
   do not know today how good this field will be. **This question got heavier in review**: every
   count threshold in §6 is now stated on `N_eff = N / deff_hat`, and `deff_hat` is estimated
   from the block structure. Bad blocking therefore no longer merely weakens one guard — it
   mis-states the effective sample size that every gate depends on, in the anti-conservative
   direction (missing blocks look like independence). §9.2 records `max_creator_share` as `null`
   rather than 0 when it is unmeasurable, so an unmeasured guard cannot read as a passed one,
   but there is no substitute for the field actually being there.
5. **Publication-date semantics.** Upload date vs. re-upload date vs. "posted" date differ by
   platform and are sometimes silently the latter. A systematic platform-specific offset would
   distort period bucketing in a way none of the checks in §5.9 would catch.
6. **Identity thresholds.** 0.72 / 0.60 / 0.6-0.4 weighting are reasoned priors, not measured
   values. §4.8 replaces them. Until it has run, between-run lineage should be treated as
   provisional, and the UI should lean on the ambiguous band.
7. **Minimum detectable effect vs. product expectation — and it is worse than first stated.**
   §5.8 originally computed power on raw item counts and concluded the system detects roughly a
   doubling of share at 61% power. Recomputed on the effective sample size that the document's
   own design-effect argument implies (`deff ≈ 2`), that is **34%**: the system misses two such
   doublings in three. Most *real* movement in a category is subtler still. So the product must
   present honest non-detection far more often than a trend dashboard normally does — and,
   concretely, the Overview's Westfall–Young-filtered headline trend cards will usually be
   empty, which §11.24 now requires the UI to state rather than paper over. Raising power means
   **~4,800** dated reference-stratum items per 12 months at `deff = 2`, roughly double the
   figure first quoted. Whether that collection budget is worth buying is a product decision; it
   must be made explicitly and must not be absorbed by loosening thresholds.
8. **Sequential testing across runs** is handled by replication status (§6.6b), which is
   pragmatic rather than formal. A proper treatment (alpha-investing, e-values, or always-valid
   confidence sequences) would be more rigorous and is a reasonable later upgrade; it is overkill
   for the MVP, but the shortcut should be known.
9. **Roll-up labelling.** Parents are labelled from the union of children's members, which risks
   exactly the "too generic to be useful" label that `leaf` selection was chosen to avoid (§2.5).
   It may turn out that parents should not be labelled at all and should be named by enumerating
   their children.
10. **`cluster_links` social↔commerce threshold (`cos ≥ 0.55`).** The two corpora use different
    CAD templates, so their absolute cosines are not calibrated against each other, and 0.55 is a
    guess. It should become a quantile of the observed cross-space cosine distribution once that
    distribution exists; until then these links are explicitly inspection candidates only.
11. **Whether `theme_v1` should exclude the category's generic *product-type* vocabulary too.**
    Excluding brand names is clearly right (§1.2). But if that vocabulary is dominated by one
    brand's format, the theme space may still partially encode brand. Review produced a partial
    answer that does not violate §1: `brand_leakage_scan` (§1.2) finds tokens over-represented
    in a single cluster and flags those that are *not* on the known-brand list, which detects the
    symptom without presupposing the brands. It is a review queue, not a fix — it cannot tell a
    genuinely brand-specific theme from a brand-encoded artefact, and that judgement stays with
    a human. The deeper question, whether product-type vocabulary should be redacted wholesale,
    remains open and probably cannot be settled without seeing the corpus.
12. **Whether the invariance probe's replicate thresholds (0.90 / 0.88) are right.** They were
    chosen so that a correct space passes and a style-collapsed one does not, but unlike the
    paraphrase thresholds they have no calibration behind them at all: an analysis replicate
    differs in *content*, not only wording, so there is no a-priori level at which
    replicate-to-replicate similarity "should" sit. The first corpus will show whether 0.90 is
    a real bar or a formality, and the probe records the full distribution so that can be read
    off rather than guessed at a second time.
13. **Whether `deff_hat` is estimable well enough to carry the weight now placed on it.** Moving
    §6's thresholds onto `N_eff` is the right correction — the alternative was thresholds
    justified by arithmetic the document contradicted elsewhere — but it makes a pooled ANOVA
    estimate of the intra-creator correlation load-bearing for every gate. It is clamped to
    `[1, 5]` and degrades to 1 when blocks are singletons, which is the safe direction only if
    blocks are genuinely singletons rather than merely unobserved (see 4). A sensitivity report
    showing each theme's verdict at `deff ∈ {1, 1.5, 2, 3}` should ship alongside the first real
    run, before anyone relies on a borderline verdict.

---

## 13. Review notes

An adversarial review of the first draft, attacking statistical validity, seeding, hallucination
surface, feasibility, under-specification and internal contradiction. Claims that could be
checked were checked: the DDL was executed against a live Postgres with `vector` and `pg_trgm`,
the sklearn behaviour was measured against the project's own venv (sklearn 1.9.1, scipy 1.18.1,
numpy 2.5.3 — the §0.5 reality check was accurate), and every arithmetic claim in §5.8, §6.2 and
§6.4 was recomputed.

### 13.1 What the review found that would have shipped a wrong number

- **Thresholds justified by i.i.d. arithmetic the document itself contradicts.** `N(p) ≥ 40`
  (`0.9^40 = 1.5%`), the left-truncation guard (`0.95^80 = 1.7%`) and the whole §5.8 power table
  assumed independent items, while §5.6–5.7 assert a creator design effect of 1.5–3×. At
  `deff = 2` those become 12.2%, 12.9%, and 34% power instead of 61%. The "informative absence"
  guarantee — the thing that stops every theme being called `emerging` — did not hold.
  *Fixed*: all count thresholds restated on `N_eff = N / deff_hat` (§5.7, §6.2), the
  left-truncation guard restated in creator-block units (§6.4), §5.8 recomputed with both
  columns shown, and the stated collection budget for 80% power doubled to ~4,800 items/12
  months.
- **The permutation scheme could not be implemented as written.** It reassigned block membership
  vectors "to another block of the same size class (1, 2–3, 4–7, 8+)" — undefined for unequal
  sizes, and contradicted by §8.7's `(R, n)` index matrix, which only encodes equal-size swaps.
  Every p-value rested on it. *Fixed*: exact-size exchangeability strata, `G` restated on
  exchangeable blocks only.
- **`effect_estimable=false` bypassed the effect floor**, handing `growing` with no effect-size
  requirement to exactly the separated, newest, most left-truncation-prone themes the floor
  exists to catch. *Fixed*: Firth penalty so separation is rare; the residual cases route to
  `weak_signal`.
- **The effect floor was not period-normalised**, so switching to quarterly turned `OR ≥ 1.25`
  into 1.077/month — a *weaker* bar in the data-poor regime. *Fixed*: `1.25³` / `0.80³` for
  quarters, plus the window extension that makes `|P_q| ≥ 4` attainable there at all.
- **Significance and direction came from two different fits with no agreement check.** *Fixed*:
  `sign(T) ≠ sign(β1)` ⇒ `direction_inconsistent`.
- **The onset Fisher test deduplicated one margin and not the other.** *Fixed*: both margins in
  block units.
- **`n_corpus` was defined as "not restricted to clustered items"**, which lets a per-period
  pipeline-failure rate rescale every share — the section's own failure mode, in the
  denominator. *Fixed* (§5.3A, §7.8).
- **The reference stratum removed theme-conditioning but was never frozen across runs**, so
  `series_stability` and the whole replication ladder compared different instruments. *Fixed*:
  `reference_query_set_version`, treated like an epoch break (§5.2, §11.6b).
- **`platform_mix_shift` would have fired on nearly every run by construction** — only YouTube
  can be date-stratified, so old periods are structurally YouTube-heavy — downgrading everything
  to `exploratory`. Detecting a confound you built into collection is worse than conditioning on
  it. *Fixed*: platform is now an exchangeability stratum and the CA statistic is summed over
  platforms; the flag survives as a diagnostic.
- **Westfall–Young was described as step-down but specified as single-step**, with no account of
  how per-theme per-permutation p-values are obtained (statistics are not comparable across
  themes). *Fixed*, and §5.8 now states the consequence the first draft left unsaid: this filter
  will usually pass nothing, so the Overview cannot be built on trend verdicts.
- **Brand-composition claims carried unclustered Wilson intervals, no family, and two
  side-by-side CIs inviting the overlap fallacy.** *Fixed* (§5.10, §11.13).
- **`QUOTE` imported brand tokens into the "brand-free" clustering views** — §1.2 excluded the
  caption for exactly that reason, then §1.3.9 embedded a verbatim caption span. *Fixed*:
  redaction extended to `QUOTE`, plus a `brand_leakage_scan` that detects undiscovered-brand
  encoding without presupposing the brands (which §1 forbids).

### 13.2 What the review found that would not have run

Verified by execution, not by reading:

- `space_key text generated always as (corpus::text || '.' || space::text) stored` →
  `ERROR: generation expression is not immutable`. It was the first statement referencing
  `cluster_runs`, so **the entire migration cascade-failed from it**. Replaced by a plain column
  plus a CHECK, which Postgres accepts; the revised DDL now applies end to end.
- `max_cluster_size` is **silently ignored under `cluster_selection_method="leaf"`** (sklearn
  docstring, confirmed empirically: a 60-member cluster returned under a cap of 50). The stated
  "no cluster may exceed a third of the corpus" guard did not exist. Replaced by a bounded
  post-hoc repair.
- `metric="euclidean"` with sklearn's default `algorithm="auto"` gives **ARI 0.969** against
  precomputed cosine, not 1.0, although the distance orderings are rank-identical (0 violations
  in 114,960 pairs). Only `algorithm="brute"` reproduces it. So §2.1's equivalence argument,
  §2.9's determinism guarantee **and acceptance test 4** were all false as written; the test
  would have failed against a correct implementation and been "fixed" by weakening it.
  `algorithm="brute"` is now pinned (measured: 0.25 s, ~0.4 GB at n=4000, d=64).
- `content_embeddings_dedup` as a unique index **rejects two different items with identical CAD
  text** (verified `unique_violation`) — routine for sparse records. Now a plain cache index.
- Nothing prevented an item holding **two `core` rows, or `core` and `noise` simultaneously**,
  in one run (both verified as accepted inserts). §2.6 calls the core-only counting rule
  "non-negotiable"; a partial unique index now makes that true. Related: §10.13's
  `n_core + n_noise == n_items_clustered` was unsatisfiable once noise recovery promoted points,
  so recovery now replaces the noise row and two noise fractions are stored.
- **Deleting a `research_run` failed** on `clusters.first_run_id` (verified). Changed to
  `on delete restrict`, which matches the intent that identities outlive runs.
- `8 ≤ n_clusters ≤ max(40, n/40)` contradicted `leaf` selection and the document's own counts
  (§6.6a "30–80 themes", §9.3 "~40 + ~50"). At n=1500 it would have forced `status='degraded'`
  on most runs, capping **every** theme at `weak_signal`. Bound widened.

### 13.3 Hallucination surface closed

- `support_rate` / `dissent_rate` / `no_rate` were model-emitted numbers used as hard acceptance
  gates — the model grading its own exam. All three are now recomputed in code, refs are
  validated against the sample, disjointness is enforced, and `supporting_refs` is re-derived
  from the containment test rather than accepted (§3.2, §3.5).
- The null-label control was drawn from HDBSCAN noise and justified by the claim that noise
  points are "the least mutually similar items in the corpus" — which is not what noise is, and
  is directly contradicted by §2.8.3, which mines coherent groups out of it. Now drawn uniformly
  from the corpus, with any `label_supported=true` failing (§3.6).
- The narrative layer was instructed to paraphrase only, with nothing checking it. Its output is
  now verified against the input claims' numerals and vocabulary (§11.15).

### 13.4 The invariance probe was measuring the wrong thing

The style probe paraphrased the *rendered CAD*. But the CAD is rendered deterministically from
the analysis record, so phrasing variation never occurs in production; the variation that does
occur is the analysis pass emitting a different set of values. A space could pass the probe and
still cluster by field-completeness. The probe is now primarily an **analysis-replicate** probe
(§1.5c1), with the paraphrase probe retained for the two free-text subfields, the candidate set
specified (it changes the bar by an order of magnitude), and the circular dependency between
`k`, the probe and the reducer harness resolved into a stated three-step order.

### 13.5 Seeding (brief §1 / §34)

**The design was clean.** A scan for the research subject, its market, its category vocabulary
and plausible pre-judged themes found nothing: no consumer, occasion, competitor, slang term or
cultural association is named anywhere, all examples are `Brand A` / `Theme X` / `Cluster Y`,
and §7.4 explicitly reasons about why a `piattos_presence` column would be a violation. One item
was changed: `Asia/Manila` appeared as a hard-coded default in §5.5, two DDL comments and the
`build_timeseries` signature. It is a market constant rather than a seeded finding — the brief
names the first market as a given — but it is a Philippine specific baked into a module default
and will be silently wrong for a second market, so it is now `settings.aypo_market_timezone`
with **no default**, configured per deployment. §5.5's justification for local-time bucketing
was also factually backwards (the mis-bucketed window is 00:00–08:00 local, the posting trough,
not "each evening", the peak) and overstated by roughly thirty-fold (~1% of items for monthly
buckets, not "an 8-hour slice of every day"); the conclusion survives, the argument was
corrected.

### 13.6 What was deliberately left alone

Most of the document. The core judgements are sound and several are better than the
state of the practice for this kind of product: embedding the structured analysis rather than
the caption or transcript; excluding brands from every clustering view so brand-by-theme is a
measurement rather than a tautology; `leaf` over `eom`; identity in the 1536-d space rather than
the refit clustering space; new ids for all split and merge children; `corpus_churn` as a
distinct death kind; within-period composition share as the only identified estimator; the
reference stratum against query-conditioned oversampling; recomputing the series each run and
gating on its stability; and minting claims in code with a refusal path. §5.1–5.4 in particular
is the part of this design most worth keeping exactly as it is.

### 13.7 Residual risk

1. **The common-exposure assumption is still an assumption.** Platform stratification removes the
   between-platform confound; it does nothing about a recommender that favours one theme over
   another *within* a platform over time. §5.9's three falsifications remain attempts, not
   proofs. This is the single largest threat to every temporal claim and the real fix is a
   licensed date-complete denominator, not a better statistic.
2. **`deff_hat` now carries every count threshold** and is itself estimated, from a block
   structure whose quality is unknown (§12.4, §12.13). Missing creator ids look like
   independence, which is the anti-conservative direction. Ship the `deff ∈ {1, 1.5, 2, 3}`
   sensitivity report before anyone acts on a borderline verdict.
3. **Power is low enough to be a product problem.** 34% for a doubling of share. Headline trend
   cards will usually be empty. That is honest, and it may not be commercially acceptable.
4. **Identity thresholds are still priors** until §4.8 runs, and the §4.4/§4.5 precedence rules
   fixed here are logically consistent but untested against a real re-clustering.
5. **Publication-date semantics by platform** (§12.5) remain unaddressed and none of the §5.9
   checks would catch a systematic offset.
6. **Cross-run comparability now depends on three artefacts staying pinned** — epoch, reducer
   fingerprint, reference query set. Three things that can drift is three ways for the
   replication ladder to quietly become meaningless; all three are recorded on the run, but
   nothing yet alerts when they change.
7. **Facet viability, roll-up labelling and the `cluster_links` threshold** are unchanged open
   questions (§12.3, §12.9, §12.10) and remain empirically decidable only.
