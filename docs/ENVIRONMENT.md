# Verified environment facts

Everything here was **measured on this machine**, not assumed. Implementers must
trust this file over any recollection, and over any design doc that conflicts
with it. Re-verify with the command shown if something stops working.

## Toolchain

| Thing | Version | Notes |
|---|---|---|
| Python | 3.13.9 | managed with `uv` (0.11.32); `uv sync --group dev` in `apps/api` |
| Node | 22.23.3 | pnpm 10.18.0, workspace root at repo root |
| Next.js | 16.3.8 | React 19.2.8, Tailwind 4, App Router, `src/` dir, `@/*` alias |
| Postgres | 17.11 (Homebrew) | local service: `brew services start postgresql@17` |
| pgvector | 0.8.7 | extension created in the `aypo` database |
| FFmpeg | 8.1.1 | `ffmpeg` and `ffprobe` on PATH |
| anthropic SDK | 1.11.0 | the 1.x major |
| openai SDK | 3.24.0 | embeddings + whisper |
| scikit-learn | 1.9.1 | **ships `sklearn.cluster.HDBSCAN`** — do NOT add the standalone `hdbscan` package |

`psql` is only on PATH via `/opt/homebrew/opt/postgresql@17/bin`; the app does
not need it (asyncpg connects over TCP).

## Database

```
DATABASE_URL=postgresql://ellaakirasy@localhost:5432/aypo
```

Local, trust auth, no password. Verified reachable from the app:
`{'ok': True, 'postgres': 'PostgreSQL 17.11 (Homebrew)', 'pgvector': True}`.

This is a **development** database. Deployment targets managed Supabase by
changing `DATABASE_URL` only; nothing in the code assumes local Postgres.

Deliberately NOT used: the `DATABASE_URL` and `SUPABASE_*` values in the
operator's `~/.env`. Those belong to an unrelated project and this project's
~20 tables must not be created there.

## Credentials actually present

| Capability | State | Env var |
|---|---|---|
| LLM (analysis, reasoning, findings) | **ON** | `ANTHROPIC_API_KEY` |
| Embeddings | **ON** | `OPENAI_API_KEY` |
| Transcription | **ON** | `OPENAI_API_KEY` |
| Web-search discovery | **ON** | `SERPER_API_KEY` |
| Storage | **ON** (local backend) | — |
| TikTok / Instagram / Facebook / Shopee / Lazada | **OFF** | `APIFY_TOKEN` |
| YouTube | **OFF** | `YOUTUBE_API_KEY` |

`Settings.capabilities()` computes this at runtime and
`Settings.missing_for(cap)` names the env vars needed to switch one on. The
research loop reads `capabilities()` when planning so it routes around a dead
source instead of discovering the gap mid-run (brief §7).

**Consequence for testing:** collectors for the Apify-backed sources must be
unit-tested against recorded/synthetic actor payloads, because they cannot be
exercised live until the token lands. Those tests must use generic placeholders
(`Brand A`, `Theme X`) per brief §34 — never invented Piattos data.

## Model configuration (operator-chosen)

| Stage | Model | Why |
|---|---|---|
| Research planning, hypotheses, competitor reasoning, finding generation, Ask-the-Data | `claude-opus-5` | judgement-heavy, low volume |
| Per-item multimodal extraction | `claude-sonnet-5` | ~1500 items/run; the operator selected this tier for the bulk pass |

Env: `AYPO_LLM_MODEL`, `AYPO_ANALYSIS_MODEL`, `AYPO_LLM_FALLBACK_MODEL`.

### Measured cost

Per-video analysis (8 frames ≈ 8.8k image tokens + ~1.2k text, ~900 out, system
prompt cached):

| Model | Per video | 1500 videos |
|---|---|---|
| `claude-opus-5` | $0.0725 | **$109** |
| `claude-sonnet-5` | $0.0295 | **$44** |

Default run budget is `AYPO_MAX_LLM_USD=120`, so the Opus bulk pass would
consume essentially all of it and starve the reasoning stages. Hence the split.

## Anthropic SDK — verified behaviour

* `client.messages.parse(output_format=PydanticModel, ...)` returns
  `response.parsed_output` as a validated instance. **Verified by source
  inspection:** `parse` merges a caller-supplied `output_config` with the
  schema it generates (`{**output_config, "format": <generated>}`), so
  `output_config={"effort": ...}` and structured output compose correctly.
* `parse` is on the **non-beta** namespace and takes no `betas` / `fallbacks`
  argument. Refusals are therefore handled client-side in `aypo/llm/client.py`:
  on `stop_reason == "refusal"` the request is retried once on
  `AYPO_LLM_FALLBACK_MODEL`, so one declined item cannot kill a long run.
* Thinking is `{"type": "adaptive"}` everywhere and is **never disabled**;
  depth is controlled with `output_config={"effort": ...}`.
* Usage fields confirmed live: `input_tokens`, `output_tokens`,
  `cache_read_input_tokens`, `cache_creation_input_tokens`,
  `output_tokens_details.thinking_tokens`.
* Prompt caching confirmed working: a `cache_control` system block produced
  `cache_creation_input_tokens=520` on first call. Because the per-item
  analysis prompt is large and byte-identical across thousands of items,
  **keeping that prompt byte-stable is the single biggest cost lever** — any
  per-item interpolation into the system prompt destroys the cache.

Live smoke test passed: structured extraction returned a validated model and
correctly set `is_active_consumption: false` for an unopened packet, with an
observable cue — the brief §28 distinction between incidental appearance and
active usage.

## Embeddings — verified

`text-embedding-3-large` with `dimensions=1536`, confirmed live (1536-length
vectors; cosine 0.21 between two unrelated sentences).

**1536 is a hard constraint, not a preference:** pgvector's HNSW index supports
at most 2000 dimensions, so the model's native 3072 cannot be indexed.
`get_embedding_provider()` raises if `AYPO_EMBEDDING_DIMENSIONS > 2000`.

## FFmpeg scene detection — measured, and a scale correction

`scdet` on FFmpeg 8.1.1 emits exactly these metadata keys:

```
lavfi.scd.mafd     lavfi.scd.score     lavfi.scd.time
```

So `lavfi.scd.score` is correct and present. Parsing may still accept
`lavfi.scene_score` defensively, but it is not emitted by this build.

**Correction to design doc 03 — read this before implementing frame sampling.**
`scdet` scores are on a **0–100** scale (the filter's own `threshold` option
defaults to **10**). They are *not* on the 0–1 scale of the older
`select='gt(scene,0.3)'` idiom. Any "no meaningful cut in this bucket" threshold
expressed as `0.08` is mis-scaled by ~3 orders of magnitude and would
effectively never fire, causing the bucket argmax to be chosen even for a
static shot. Calibrate that threshold on the 0–100 scale.

Validation run — a synthetic clip with hard cuts at exactly t=4.0s and t=8.0s,
sampled at `fps=6`:

```
t= 3.833s  score= 35.323     <- cut 1 (within one 0.167s sample)
t= 7.833s  score= 28.686     <- cut 2 (within one 0.167s sample)
t= 8.000s  score=  1.993
t=11.667s  score=  0.798
```

Cuts separate from non-cuts by more than an order of magnitude, so ranking
frames by score within a bucket works as designed. Reproduce with:

```bash
ffmpeg -nostdin -hide_banner -loglevel info -i clip.mp4 -an \
  -vf "fps=6,scale=160:-2:flags=fast_bilinear,scdet=threshold=0,metadata=print:file=-" \
  -f null - 2>/dev/null | grep lavfi.scd.score
```

## Infrastructure choices and their reasons

* **No Redis, no Docker** on this machine. Background work therefore uses a
  **Postgres-backed queue** (`aypo/jobs/queue.py`) with `FOR UPDATE SKIP
  LOCKED` and lease-based visibility timeouts, not Celery. Brief §30 permits
  "another lightweight managed queue if simpler"; this keeps deployment to two
  processes and keeps every job auditable in SQL next to its research run,
  which brief §32 needs anyway.
* **UMAP is an optional extra** (`uv sync --extra umap`). It pulls numba, the
  heaviest dependency in the project; the clustering pipeline must fall back to
  PCA when it is absent.

## Apify — token, plan limit, and corrected actor ids

Token validated live: `GET /v2/users/me` → 200, username `ellaxsy`.

### Hard constraint: FREE plan, $5/month

```
plan: FREE        maxMonthlyUsageUsd: 5
```

Every chosen actor is **PAY_PER_EVENT**. The brief §6 research targets
(800–1,200 TikTok videos, 750–1,500 Shopee records, 500–1,000 Lazada records)
**cannot be collected inside $5**. This is a billing limit, not a code problem:
the collectors are built to the full targets and the run budget
(`AYPO_MAX_COLLECTION_UNITS`) throttles them, but a full-scale run needs a paid
Apify plan. Until then, run with small `maxItems` to validate the pipeline.

### Verified actor ids

My initial guesses were wrong — `easyapi~shopee-product-scraper` and
`easyapi~lazada-product-scraper` both **404**. These were verified to exist via
`GET /v2/acts/{id}` and their input schemas were read from the latest build:

| Role | Actor | Keyword search input | PH support |
|---|---|---|---|
| TikTok | `clockworks~tiktok-scraper` | — | — |
| Instagram | `apify~instagram-scraper` | — | — |
| Facebook | `apify~facebook-posts-scraper` | — | — |
| Shopee listings | `gio21~shopee-scraper` | `keywords: string[]`, `maxItems` | `country` enum has **`PH`** |
| Shopee reviews | `zen-studio~shopee-product-reviews-scraper` | per-product | — |
| Lazada | `fatihtahta~lazada-scraper` | `queries: string[]`, `limit`, `getReviews`, `maxReviews`, `enrich_data` | `country` enum has **`ph`** |

**Case trap — do not share one country constant.** Shopee's enum is
`['BR','ID','TH','MY','SG','PH','VN','MX']` (uppercase) and Lazada's is
`['sg','my','ph','th','vn','id']` (lowercase). Passing the wrong case is the
kind of thing an actor accepts and then silently returns the wrong
marketplace for. They are separate settings: `apify_shopee_country` /
`apify_lazada_country`.

**Shopee needs two actors.** No single Shopee actor covers both keyword search
and review text, so listings and reviews are separate collectors — which maps
cleanly onto the discovery/retrieval split brief §7 already requires.

Actor ids remain configuration (brief §11) precisely because this happened:
third-party actors get renamed and removed without notice, so a 404 must be a
config change, not a code change.

## TikTok collection — measured against the live actor

One minimal run (`searchQueries: ["filipino snack haul"]`, `resultsPerPage: 2`,
category-level per brief §8B) cost **0.019 compute units** and established the
real payload shape. Findings that change the design:

### Media is NOT returned by default — retrieve it ourselves

`mediaUrls` came back **empty** and `videoMeta.coverUrl` is an Apify key-value
URL that returns **403** without the token. Video bytes require
`shouldDownloadVideos: true`, a **paid add-on**.

**Decision: use `yt-dlp` (2026.06.09, installed) against `webVideoUrl` for
retrieval.** Verified end to end — it fetched the real 1080p clip (2.09 MiB).
This costs nothing, and it is the cleaner architecture anyway: Apify does
*discovery and metadata*, our own retrieval gets the media, which is exactly the
discovery/retrieval split brief §7 mandates.

### Fields that are observed, not inferred

| Field | Why it matters |
|---|---|
| `isSponsored`, `isAd` | Brief §19/§28 want sponsored-vs-organic *distinguished*. These are platform-stated booleans — an **observed** fact, far stronger than a model guess. Prefer them; fall back to inference only when absent. |
| `relatedSearchWords` | Organically discovered related queries (e.g. "FoodTok Philippines"). Feeds brief §8D theme-level expansion **without hardcoding themes**. Set `scrapeRelatedSearchWords: true`. |
| `searchQuery` | The query that found the item — provenance, free. |
| `textLanguage`, `createTimeISO`, `hashtags[].name` | language hint, publication date, tags. |
| `videoMeta.aiVideoDescription` / `aiVideoSummary` | **TikTok's own model output.** Store raw if present, but never treat as observed evidence — it is a third party's interpretation, and citing it as observation would violate brief §3. |

`videoMeta.transcriptionLink` and `subtitleLinks` were **null**, so free
platform transcripts cannot be relied on. Whisper is required.

Engagement maps as: `playCount`→views, `diggCount`→likes, `collectCount`→saves,
plus `shareCount`, `commentCount`, `repostCount`.

### Frame selection validated on real content

On the retrieved clip (14.5 s, 1080×1920 vertical, HEVC):

```
trimmed window = [0.43, 13.92]
top scene scores:
  t= 0.17s  score=10.93   TRIMMED  <- opening fade-in artefact
  t= 0.33s  score= 1.28   TRIMMED
  t=10.67s  score= 0.91   inside window
```

All six buckets correctly fell back to `bucket_midpoint`: the only large peak is
the **opening-frame artefact**, which the head trim exists to remove, and the
largest in-window score (0.91) sits just under the `SCENE_ABSOLUTE_FLOOR` of
1.0. This is both the trim and the floor working as designed on a genuinely
cut-free single-take video — and it shows the floor constant is load-bearing at
exactly this margin, so it is worth measuring rather than leaving as a prior.

### Transcription validated

Whisper auto-detected **`tagalog`** with no language hint, returning timestamped
segments. This is the concrete payoff of never pinning a language: the target
market's speech is routinely Taglish, and a forced `en` would have destroyed the
code-switching signal brief §18 asks for. Cost: **$0.0015 per 15 s clip**.

**Caveat the analysis layer must handle:** this clip's audio was *music, not
speech*, so the "transcript" is song lyrics. Two consequences — a lyric
transcript is **sound context**, not creator speech, and must not be read as the
creator making a product claim; and song lyrics are third-party copyrighted
text, so they should not be surfaced verbatim in findings or UI. Detect the
music case (platform `musicMeta.musicOriginal` false plus a transcript that
tracks the track) and label it.

## Structured-output limits — hit in practice, design around them

Two hard API constraints surfaced while building the analysis schema. Both
produce a 400, neither is documented in the SDK surface:

1. **At most 24 optional parameters** across a structured-output schema:
   *"Schemas contains too many optional parameters (28), which would make
   grammar compilation inefficient."*
2. **The compiled grammar must not be too large:** *"The compiled grammar is
   too large, which would cause performance issues."* This fired even at 20
   optional parameters, because 13 separate `list[Claim]` facets each
   containing nested `Anchor` objects multiply out.

Consequences baked into `aypo/analysis/schema.py`:

* **Every field is required.** An empty list is the correct answer when the
  material supports nothing, and is meaningfully different from a facet being
  absent — so requiring the key costs nothing and removes the optional-count
  problem entirely (now **0 optional**, 8 top-level fields).
* **One `labels` list discriminated by a `facet` enum**, not one list per
  facet. This also sits closer to brief §19B, which asks for *proposed* labels
  rather than fixed buckets.
* **Anchors are compact strings**, not nested objects:
  `frame:3` · `transcript:6.00-7.30:verbatim quote` · `caption:…` ·
  `overlay:…` · `comments:…`, parsed by `parse_anchor()`. Nothing is lost:
  anchors are validated by code against the material actually supplied, so a
  structured shape from the model would have been re-checked anyway.

## End-to-end analysis — verified on real content

Full pipeline run: Apify discovery → yt-dlp retrieval → FFmpeg frame selection →
Whisper → one fused Claude call → programmatic validation.

On a real Filipino "pasalubong haul" TikTok found by the **category-level**
query `filipino snack haul` (which never names any brand), the analysis read
**15 brands off packaging**, including Piattos co-appearing with 14 others.

**Independently verified by inspecting the frames.** Piattos, Nagaraya, Ding
Dong, Boy Bawang, Chippy, Nova, Oishi and Rosquillos are all genuinely visible
on packaging in frame 1. No brand was hallucinated.

Guardrails held without intervention:

| Guardrail | Observed behaviour |
|---|---|
| Appearance ≠ use (§28) | every product `present`; nothing claimed as consumed, which is correct for a flat-lay |
| Open-world discovery (§1) | surfaced `pasalubong` as an occasion — a real cultural concept, discovered, never hardcoded |
| Sponsorship from cues only (§19) | `likely_organic`, carrying exactly the two admissible organic cues |
| Say when evidence is missing (§28) | `evidence_insufficient_for` named 10 facets it looked for and did not find |
| Anchors checked, not trusted | validator dropped 0 / demoted 0 — every claim carried a real anchor |

This is the competitor-discovery signal brief §23 asks for (product
co-appearance), arrived at by discovery rather than by being told.

### Measured cost, and where it actually goes

That item cost **$0.0964** — above the $0.0295 estimate, because **output
tokens dominate**: 7,055 out vs 4,845 in. It was an unusually brand-dense item
(15 brands, each with a cue and anchors). A typical 2–4 brand item lands nearer
**$0.03**.

First call wrote 6,447 cache tokens; every subsequent item reads that prefix at
0.1×, saving roughly $0.013/item — which is why the system prompt must stay
byte-identical (`prompt.py` is exempt from the line-length rule for exactly
this reason).

**Budget implication:** LLM spend is billed to Anthropic/OpenAI and is
independent of the $5 Apify cap. Size the two separately.

## Schema: a naming inconsistency to know about

The six migrations were written in parallel and **do not agree on the
run foreign-key name**:

| Column name | Tables |
|---|---|
| `run_id` | `research_iterations`, `research_log`, `research_queries`, `research_results` |
| `research_run_id` | `collection_coverage`, `content_analysis`, `findings` |

Both are live and the API is written against the real names. This is worth a
normalising migration before the schema grows further, but it is not worth
rewriting applied migrations for now — renaming would invalidate every
content-hash the migration runner has recorded.

Other shapes that differ from what a reader might assume, all confirmed against
the live database:

* `research_queries.query_text`, not `text`. Result counters
  (`results_total`, `results_admitted`, `results_relevant`) are **denormalised
  onto the query**, so counting `research_results` to get them is unnecessary.
* `research_iterations` uses `opened_at` / `closed_at`, not started/finished.
* `research_log` uses `occurred_at`, and carries both `detail` and `payload`.
* `content_items` exposes `source_url` and `url_canonical`; there is **no**
  `url` column.
* `finding_evidence` is generic: a `ref_id` discriminated by `kind`, with no
  `content_item_id`. Join `content_items` only `WHERE kind = 'content'` and
  `commerce_items` only `WHERE kind = 'commerce'`.
* `clusters.status` is the enum `cluster_status`
  (`active, provisional, unlabelled, review, dormant, split, merged, retired`).
  There is no `dead` value; superseded clusters are `retired` or `merged`.
* `brands` has no `resolution_quality` column.

All eight read endpoints return 200 against this schema.

## Wiring the orchestrator to the schema — what the database actually enforces

The schema is enum-heavy and constraint-heavy, which is good: every one of these
was found by a write failing loudly rather than by data quietly going wrong.
`aypo/db/run_store.py` holds the mappings in one place.

| Expectation | Reality |
|---|---|
| `platform` is the `Platform` enum value | Canonical vocabulary is `collection_sources`: marketplaces are **`shopee_ph`/`lazada_ph`**, not `shopee`/`lazada` |
| `research_log.event_type` is free text | It is the enum `research_log_event` (42 values) |
| `research_runs.stop_reason` is free text | It is `research_stop_reason`, and more specific than the loop's own ceilings — it separates `saturation` from `rank_exhaustion`, whose remedies are opposite |
| `research_budget_kind` has one "collection" bucket | It splits **`social_unit`** and **`commerce_unit`**, so spend is attributable to the side of the run that caused it |
| `research_query_status` has a bare `rejected` | Rejections must name the gate: `rejected_duplicate`, `rejected_guardrail`, `rejected_low_yield`, `rejected_budget` |
| `research_strategy` covers every `SamplingStrategy` | It has five values; `seed` and `hypothesis` have no DB equivalent and are mapped (a hypothesis query also needs a linked hypothesis row, enforced by a CHECK) |
| `stop_detail` is text, `saturation_basis` is json | **The reverse**: `stop_detail` is `jsonb`, `saturation_basis` is `text` |
| `research_budget_ledger` uses `amount_usd`/`units` | It uses `usd`/`qty`, plus token columns for LLM lines |
| `collection_coverage.planned_units` is nullable | NOT NULL — an explicit `NULL` overrides the default |
| `commerce_sellers` is unique on (platform, seller_id) | Unique on `(platform, COALESCE(platform_seller_id, name_slug, 'unknown'))`, so the conflict target must name the expression |

`TRUNCATE` on most tables fails: `label_observations` has an append-only
trigger that refuses it, and the cascade reaches it. Use `DELETE` when resetting
a development database. That trigger is the design working — the model's raw
output is never destroyed by canonicalisation.

## Two correctness fixes found by wiring it up

**Migration 0007 — deferred foreign keys.** Six tables carried
`research_run_id` as a plain uuid with no FK, because the migration tracks were
written to be independently creatable. Nine orphaned coverage rows had already
accumulated, and orphans feed straight into per-run denominators that brief §28
makes load-bearing. All now constrained; `collection_jobs` uses `SET NULL`
rather than `CASCADE`, because a job row is an execution record and losing the
history of what ran would make a failure uninvestigable.

**Migration 0008 — the grain of `research_results`.** The unique index was
`(run_id, item_key)`, which allows only **one provenance row per item per run**.
That silently collapses a real distinction: an item discovered by a `category`
query and again by a `competitor` query is two observations with different
sampling strategies, and the second insert was being dropped.

This is not tidiness. Sampling strategy is a property of the discovering query
(D-3), and the sampling-bias defence depends on it — only reference-strata
(brand, category) items may enter a denominator, because query-conditioned
items would bias every share they appear in. Under the old index, **whichever
query reached an item first silently decided whether it could be counted**. The
index is now `(run_id, query_id, item_key)`.

A rediscovery keeps its own provenance row but is charged **zero** collection
units: units measure content obtained, and obtaining the same item twice is not
two items. Charging both would let one item consume two units of the ceiling.

## Security: Apify auth moved to a header

The token was passed as `?token=...`, which httpx logs verbatim on every
request — and from there into log aggregation, proxies and crash reports. It now
goes in an `Authorization: Bearer` header (verified against `users/me`,
`usage/monthly` and actor lookup), and `httpx`/`httpcore`/SDK client loggers are
pinned to WARNING so provider URLs never fill a run log.

## Collection concurrency

Each managed-collector search is a separate actor run taking 30–90 s. Issuing
them in sequence made a realistic iteration (17 queries × 3 platforms ≈ 51 actor
runs) take close to an hour. Collection now fans out across every
(platform, query) pair, bounded by the provider gate; the account allows 5
concurrent jobs and the default is 4, leaving headroom. Measured: 4 actor runs
starting within 0.1 s of each other instead of serially.

### A hung actor starves every other source

Measured during a live run: one `clockworks~tiktok-scraper` run stayed `RUNNING`
for 15 minutes. Because it held one of the four provider concurrency slots for
the whole of `APIFY_DEFAULT_TIMEOUT_SECS`, the Lazada searches queued behind it
never started at all — the run collected TikTok and Shopee and silently got zero
commerce data from a source that was working.

The timeout is now **300 s**. A keyword-search actor that has not finished in
five minutes is stuck, and waiting fifteen for it costs an entire platform's
coverage. The failure is also visible rather than silent: the run aborts that
actor run and records `source_degraded` against the platform.

## Brand entity resolution — the case the first run produced

A live run read these brands off packaging across three different videos:

```
JACK'N'JILL · JACK'nJILL · JACK'N JILL
```

One brand, three surface forms. The extractor is **right** to store them
verbatim — brief §18 tells it to report the characters it can actually resolve,
because "correcting" a name at read time destroys the evidence. Resolution
therefore happens afterwards, in `intelligence/brand_resolution.py`, where it is
reversible and auditable.

All three reduce to the skeleton `jacknjill` by an **exact** match — casefold,
strip accents and punctuation, collapse spacing, fold the OCR confusions
(`0`→`o`, `1`→`l`, `5`→`s`). No fuzzy matching was needed, which is the safest
possible outcome.

**The governing bias is anti-merge.** Two different brands collapsed into one
silently corrupts every competitor comparison and is nearly invisible once made;
a brand left unresolved is merely incomplete and surfaces for a human to fix.
So: fuzzy matching is bounded at ratio ≥ 90 and is **not applied at all** below
6 characters, because short brand names differ from each other by one character
as often as an OCR error does (`Nova` / `Nove` / `Novo` stay three brands).

Two bugs caught while building it:

* `"brand"` and `"the"` were in the descriptor stoplist, so `Brand A` normalised
  to `a` and `The Body Shop` would have lost its article. Over-stripping is not
  cosmetic — it shortens the comparison key, and shorter keys collide. The list
  is now narrow marketplace/corporate noise only.
* If stripping descriptors leaves nothing, the descriptors **were** the name;
  it now falls back to the unstripped form so `Official Store` and
  `Store Official` stay distinct instead of both collapsing to an empty key.

Wired into the orchestrator's discovery feedback: competitor queries for the
next iteration are built from resolved entities, so the loop cannot generate
three near-duplicate queries for one brand or split its evidence three ways.

## Clustering: two parameter findings from a real corpus

Both measured on the live 101-item corpus, and both would have been invisible
without running it.

### `min_samples` must not be tied to `min_cluster_size`

Setting them equal looks like the conservative choice. It is too strict: it
produced **zero clusters and 100% noise**. At `min_samples=4` the same corpus
yields two. The parameter controls how dense a neighbourhood must be before a
point counts as *core*; tying it to the reportable-theme floor conflates two
unrelated questions. Now `max(3, min_cluster_size // 2)`.

Before changing anything, the distance distribution was checked —
`min=0.362 max=1.124 ratio=3.10` — which showed no severe concentration and
ruled out dimensionality as the cause.

### Reduce dimensions before estimating density

HDBSCAN estimates density from distances, and in 1536 dimensions those
distances flatten enough that most points look equally isolated. At an unchanged
theme floor:

| Space | Clusters | Noise |
|---|---|---|
| raw 1536-d | 2 | **73%** |
| PCA 40-d | 2 | **49%** |

Same themes, far more of their genuine members correctly attached. PCA rather
than UMAP because UMAP pulls in numba — the heaviest dependency in the project —
and is an optional extra, so the default path must work without it. PCA is
linear and can flatten a distinction a nonlinear method would keep; that cost is
accepted knowingly.

**Centroids and secondary membership are computed in the original space**, so
cross-run cluster identity stays comparable even if `REDUCED_DIMS` changes. Only
the density estimate runs on the reduced view.

## Retrieval: TikTok photo carousels have no video stream

**23 of 30 analysis failures in the first full run were one cause.** TikTok photo
carousels are published with only an audio track, and `yt-dlp -f "mp4/best"`
falls back to returning the mp3. The failure then surfaced deep inside scene
scoring as `scene scoring failed: [in#0/mp3 @ ...]`, which reads like a bug in
frame sampling rather than a property of the post.

Two changes: the format selector now requires a video stream
(`mp4[vcodec!=none]/b[vcodec!=none]/bv*+ba/best`), and retrieval **probes the
downloaded file** before returning, because format selection can still fall back.
A post with no video stream returns the tier `no_video_stream` with a plain
explanation, and `analysable` is false so it never reaches the vision model.

## Duplicate analysis was being paid for twice

An item rediscovered by a later query was analysed again: a full retrieval,
transcription and model call producing a row the idempotent upsert then
collapsed. The storage layer saved the data, not the money — 20 duplicate
analyses in one run, roughly $1.20. The loop now skips items already analysed in
that run.

Related: one analysis exceeded `max_tokens=8000` and truncated mid-JSON, failing
schema validation and losing the whole result. Raised to 16000; a brand-dense
item can legitimately need it.
