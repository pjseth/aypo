# 05 — Social-to-Commerce Linking, Marketplace Normalisation & Non-Causal Inference

> Subsystem design. Covers brief §15, §16, §17, §24, and the commerce half of §28.
> Normative parent: `docs/BRIEF.md`. §1 and §34 bind this document: **nothing in this
> design, its prompts, its lexicons, its thresholds, its fixtures or its defaults encodes
> any assumption about Piattos, its consumers, its occasions, its competitors, its
> category culture, or any flavour/slang/theme vocabulary.** All examples are
> `Brand A`, `Brand B`, `Theme X`, `Cluster Y`, `Term T`.

---

## 0. Scope, ownership, non-goals

### 0.1 What this subsystem does

1. Turns provider payloads from Shopee PH and Lazada PH (and a degraded web-search
   fallback) into one normalised `commerce_items` row set with per-field quality tracking.
2. Extracts **brand** and **product** structure from keyword-stuffed marketplace titles
   without silently collapsing multi-brand or bundle listings onto one brand.
3. Collapses duplicate listings of the same physical product into **product groups**, so
   "marketplace presence" is a product-level and seller-level statement, not a count of
   duplicate listings.
4. Links **discovered** social themes to marketplace records in both directions:
   theme → generated marketplace queries, and collected records → scored, stored match
   evidence. Produces a first-class **negative result** when a theme has no commerce
   expression.
5. Computes a closed set of within-sample comparative metrics, each one inseparable from a
   mandated caveat sentence.
6. Enforces non-causality mechanically: co-occurrence statistics with gates, a closed list
   of permitted claim templates, and a validator that rejects claims before persistence.
7. Mines marketplace review text through the **same open-label system** as social content.

### 0.2 What it explicitly does not do

- It does not adjudicate authenticity. It never emits the word "counterfeit", "fake" or
  "unauthorised" about a listing or a seller. It records signals and routes to human review.
- It does not estimate revenue, market share, sell-through, demand, or units sold per period.
- It does not FX-convert. Non-PHP payloads are quarantined, not converted.
- It does not infer causation from temporal ordering, and (§6.5) it reports that within a
  single research run the social↔commerce temporal test is **not computable at all**.
- It does not train models (brief §30).

### 0.3 Owned tables

`commerce_items`, `commerce_item_snapshots`, `commerce_item_embeddings`,
`commerce_item_brand_links`, `commerce_bundle_components`, `commerce_sellers`,
`commerce_product_groups`, `commerce_group_merge_evidence`, `commerce_group_links`,
`commerce_reviews`, `commerce_review_labels`, `commerce_theme_queries`,
`commerce_theme_matches`, `commerce_theme_absences`, `commerce_cooccurrence_stats`,
`commerce_price_reference`, `commerce_coverage`, `commerce_quarantine`,
plus the enums in §1.1.

Tables this subsystem **reads but does not own**: `research_runs`, `research_queries`,
`brands`, `clusters` / theme registry, `findings`, `finding_evidence`, `collection_jobs`.

### 0.4 Per-run budget (config, `aypo/config.py` → `CommerceBudget`)

| Knob | Default | Why |
|---|---|---|
| `max_items_per_run` | 2500 | Brief §6 target (750–1500 Shopee + 500–1000 Lazada). |
| `max_items_per_platform` | 1500 | Prevents one platform's actor from consuming the whole budget. |
| `max_items_per_query` | 60 | One provider result page; deeper paging returns rapidly degrading relevance. |
| `max_items_per_theme` | 120 | 12 themes × 120 = 1440, leaves ≥1000 for category-level sampling so the corpus is not theme-selected (brief §8). |
| `themes_investigated_per_run` | 12 | §4.2 selection rule. |
| `queries_per_theme_per_platform` | 6 | §4.2. |
| `max_reviews_per_item` | 20 | Provider-capped in practice; see open question OQ-4. |
| `max_reviews_labelled_per_run` | 3000 | §7.6 cost control. |
| `vision_corroboration_groups` | 150 | §2.5 cost control. |
| `llm_adjudication_pairs` | 300 | §3.4 cost control. |
| `absence_topk_adjudicated` | 20 | §4.6 gate 3: the nearest neighbours a theme must fail before an absence may be asserted. |
| `price_scale_min_cohort` | 200 | §1.4: rows needed before a cohort's price scale may be fitted. |

---

## 1. Marketplace normalisation

### 1.1 Enums

```sql
create type commerce_platform as enum ('shopee_ph', 'lazada_ph', 'web_snippet');

create type collection_mode as enum ('provider_actor', 'web_search_snippet');

create type listing_composition as enum (
  'single_brand_single_product',   -- one brand, one SKU
  'single_brand_multi_variant',    -- one brand, explicit multi-variant/assorted pack
  'multi_brand_bundle',            -- >=2 brands each with a quantity or pack spec
  'assortment_unspecified',        -- bundle/assorted markers, contents not enumerable
  'non_category_or_accessory',     -- matched the query but is not a category product
  'unresolved'                     -- extraction could not attribute safely
);

create type brand_attribution_type as enum (
  'primary',        -- the listing's subject brand, corroborated per §2.6
  'co_listed',      -- genuinely present alongside another brand
  'mentioned_only', -- appears in the title as a search keyword, not as the subject
  'ambiguous'       -- present, attribution not resolvable
);

create type price_status as enum (
  'ok', 'ambiguous', 'unparseable', 'out_of_band', 'range_too_wide', 'non_php_currency'
);

create type sold_semantics as enum (
  'unknown',                      -- DEFAULT, and the only honest value until OQ-1 closes
  'platform_lifetime_reported',
  'platform_window_reported',
  'provider_derived'
);

create type match_tier as enum ('supported', 'weak');

create type group_method as enum (
  'blocking_key_exact', 'similarity_merge', 'llm_adjudicated', 'singleton'
);

create type commerce_absence_state as enum (
  'absent_with_coverage', 'inconclusive_low_coverage', 'not_investigated'
);

create type field_status as enum ('present', 'missing', 'unparseable', 'implausible', 'provider_placeholder');
```

### 1.2 `commerce_items` DDL

**Migration ordering (hard).** All DDL in this document ships as one migration,
`supabase/migrations/0005_commerce.sql`, in this statement order: enums (§1.1) → tables with
no forward references → `commerce_product_groups` (§3.5) → the `alter table … add constraint`
statements at the end of §3.5 that attach the two FKs pointing at it. `commerce_items` and
`commerce_bundle_components` are defined *before* `commerce_product_groups` in this document
for readability, so their `product_group_id` / `component_group_id` columns are declared
without an inline `references` clause and are wired up by those ALTERs. Writing them inline in
document order would fail with `relation "commerce_product_groups" does not exist`.

```sql
create table commerce_sellers (
  id                        uuid primary key default gen_random_uuid(),
  platform                  commerce_platform not null,
  platform_seller_id        text,
  name_raw                  text,
  name_slug                 text,
  is_official_store_reported boolean,          -- provider-reported badge, NOT verified by us
  location_reported         text,
  rating_reported           numeric(3,2),
  follower_count_reported   int,
  raw_payload               jsonb,
  first_seen_at             timestamptz not null default now()
);
create unique index commerce_sellers_identity
  on commerce_sellers (platform, coalesce(platform_seller_id, name_slug, 'unknown'));

create table commerce_items (
  id                        uuid primary key default gen_random_uuid(),

  -- ---- provenance (brief §7: mandatory, never nullable) ----
  research_run_id           uuid not null references research_runs(id) on delete cascade,
  platform                  commerce_platform not null,
  platform_item_id          text not null,
  source_url                text not null,
  collection_mode           collection_mode not null default 'provider_actor',
  collection_provider       text,                -- apify actor id, 'serper', etc.
  discovery_query_id        uuid references research_queries(id),
  discovery_query_text      text,
  result_rank               int,                 -- 1-based position in the provider's RANKED
                                                 -- result page for that query. Never null for
                                                 -- provider_actor rows. See §3.7 ranking bias.
  sampling_strategy         text,                -- 'A'|'B'|'C'|'D'|'E' per brief §8
  hypothesis_id             uuid,
  theme_id_origin           uuid,                -- theme whose query found it, if any
  raw_payload               jsonb not null,
  raw_payload_sha256        bytea not null,
  normalisation_version     int  not null,
  observed_at               timestamptz not null,  -- when the PROVIDER observed it
  first_seen_at             timestamptz not null default now(),
  last_seen_at              timestamptz not null default now(),

  -- ---- text ----
  title_raw                 text,
  title_denoised            text,
  noise_spans               int4range[] not null default '{}',
  description_raw           text,

  -- ---- seller ----
  seller_id                 uuid references commerce_sellers(id),

  -- ---- price: PHP centavos, integer only, never float ----
  currency                  char(3),
  price_minor               bigint,
  price_min_minor           bigint,
  price_max_minor           bigint,
  price_is_range            boolean not null default false,
  original_price_minor      bigint,
  discount_pct              numeric(5,4),
  price_status              price_status not null default 'unparseable',
  price_scale_divisor       int,
  price_parse_confidence    numeric(3,2),

  -- ---- pack / weight ----
  pack_count                int,
  unit_net_weight_g         numeric(10,2),
  total_net_weight_g        numeric(10,2),
  weight_basis              text,                 -- 'g'|'ml_as_g'|'oz_converted'|null
  pack_parse_confidence     numeric(3,2),
  unit_price_per_100g_minor bigint,

  -- ---- platform-reported metrics: AS REPORTED, never recomputed ----
  rating_avg                numeric(3,2),
  rating_count              int,
  rating_histogram          jsonb,                -- {"1":n,"2":n,...,"5":n} when supplied
  review_count              int,
  sold_count_raw            bigint,
  sold_count_text           text,                 -- e.g. "1.2k sold" verbatim
  sold_count_band           text,                 -- §3 ordinal band
  sold_semantics            sold_semantics not null default 'unknown',
  metrics_as_of             timestamptz,

  -- ---- classification ----
  composition               listing_composition not null default 'unresolved',
  product_label             text,
  product_slug              text,
  variant_labels            text[] not null default '{}',
  variant_slug              text,
  variant_confidence        numeric(3,2),
  category_path             text[] not null default '{}',
  category_scope            text,                 -- §5.2 reference-distribution key
  extraction_version        text,

  -- ---- signals, quality ----
  listing_role_signals      jsonb not null default '{}'::jsonb,
  quality_flags             text[] not null default '{}',
  field_quality             jsonb not null default '{}'::jsonb,  -- {field: field_status}
  is_degraded               boolean not null default false,

  -- ---- media ----
  image_s3_key              text,
  image_url_raw             text,

  -- ---- grouping ----
  blocking_key              text,
  product_group_id          uuid,                -- FK added by ALTER in §3.5: this table is
                                                 -- created before commerce_product_groups

  created_at                timestamptz not null default now(),

  constraint commerce_items_identity
    unique (research_run_id, platform, platform_item_id),
  constraint price_nonneg
    check (price_minor is null or price_minor >= 0),
  constraint price_range_order
    check (price_min_minor is null or price_max_minor is null or price_min_minor <= price_max_minor),
  constraint discount_bounded
    check (discount_pct is null or (discount_pct >= 0 and discount_pct <= 0.95)),
  constraint rating_bounded
    check (rating_avg is null or (rating_avg >= 0 and rating_avg <= 5)),
  constraint counts_nonneg
    check (coalesce(rating_count,0) >= 0 and coalesce(review_count,0) >= 0
           and coalesce(sold_count_raw,0) >= 0)
);

create index commerce_items_run        on commerce_items (research_run_id);
create index commerce_items_block      on commerce_items (research_run_id, blocking_key);
create index commerce_items_group      on commerce_items (product_group_id);
create index commerce_items_platform   on commerce_items (research_run_id, platform);
create index commerce_items_scope      on commerce_items (research_run_id, platform, category_scope)
  where price_status = 'ok';
create index commerce_items_catpath    on commerce_items using gin (category_path);
create index commerce_items_flags      on commerce_items using gin (quality_flags);

-- append-only re-observation log; the current values live on commerce_items
create table commerce_item_snapshots (
  id                    bigserial primary key,
  item_id               uuid not null references commerce_items(id) on delete cascade,
  observed_at           timestamptz not null,
  price_minor           bigint,
  original_price_minor  bigint,
  rating_avg            numeric(3,2),
  rating_count          int,
  sold_count_raw        bigint,
  sold_count_text       text,
  raw_payload_sha256    bytea,
  unique (item_id, observed_at)
);

-- embeddings kept out of the hot row (brief §29 pattern)
create table commerce_item_embeddings (
  item_id     uuid not null references commerce_items(id) on delete cascade,
  kind        text not null,              -- 'listing_match_document' | 'title_denoised'
  model       text not null,              -- 'text-embedding-3-large'
  dim         int  not null,              -- 1536
  embedding   vector(1536) not null,
  created_at  timestamptz not null default now(),
  primary key (item_id, kind, model)
);
create index commerce_item_emb_hnsw
  on commerce_item_embeddings using hnsw (embedding vector_cosine_ops)
  with (m = 16, ef_construction = 64);

create table commerce_quarantine (
  id                    uuid primary key default gen_random_uuid(),
  research_run_id       uuid,
  platform              commerce_platform,
  raw_payload           jsonb not null,
  reason                text not null,     -- 'missing_required_field'|'non_php_currency'|...
  detail                jsonb,
  normalisation_version int not null,
  created_at            timestamptz not null default now()
);

create table commerce_coverage (
  research_run_id        uuid not null references research_runs(id) on delete cascade,
  platform               commerce_platform not null,
  available              boolean not null,
  unavailable_reason     text,
  queries_run            int not null default 0,
  items_collected        int not null default 0,
  items_normalised       int not null default 0,
  items_quarantined      int not null default 0,
  reviews_collected      int not null default 0,
  groups_formed          int not null default 0,
  collection_started_at  timestamptz,
  collection_ended_at    timestamptz,
  primary key (research_run_id, platform)
);
```

### 1.3 Safe handling of every optional field

**Hard rule.** `normalize()` has exactly **six** required inputs:
`platform`, `platform_item_id`, `source_url`, `raw_payload`, `observed_at`,
`research_run_id`. If any is absent the payload goes to `commerce_quarantine` and the job
continues. **Nothing else can fail normalisation.** Every other field resolves to
`(value | None, field_status)` and the status is written into `field_quality`.

```python
# aypo/models/commerce.py
FieldStatus = Literal["present", "missing", "unparseable", "implausible", "provider_placeholder"]

@dataclass(frozen=True, slots=True)
class Resolved[T]:
    value: T | None
    status: FieldStatus
    note: str | None = None
```

Per-field contract (all three platforms):

| Normalised field | Provider reality | Coercion rule | If `None`, what degrades |
|---|---|---|---|
| `title_raw` | near-always present | strip control chars, NFKC | brand/product extraction impossible → `composition='unresolved'`; item still counts for seller/price presence |
| `brand` (provider field) | present on Shopee, sparse on Lazada, seller-entered, often a placeholder | placeholder set `{"", "-", "n/a", "na", "no brand", "nobrand", "oem", "generic", "unbranded", "none", "assorted"}` (casefolded, non-alnum stripped) → `provider_placeholder` | one corroboration source lost in §2.6; needs ≥1 other source for `primary` |
| `price` | numeric or string; **scale is not stable** | §1.4 | item excluded from §5.2 price position and from `commerce_price_reference`; still counted in presence metrics |
| `original_price` | often absent, often a decoy | §1.4; discount derived, never trusted | `discount_pct = NULL`; no discount display |
| `rating_avg` | absent when `rating_count = 0` | if `rating_count` in `(0, NULL)` force `rating_avg = NULL` (a 0.0 rating is "no ratings", not "bad") | item excluded from §5.4 |
| `rating_histogram` | may be absent (OQ-3) | accept only if the five buckets sum to within ±2 of `rating_count` | §5.4 falls back to mean+count, which weakens it; caveat text changes accordingly |
| `review_count` | may differ from `rating_count` | keep both, never reconcile | review-volume context missing |
| `sold_count` | integer, or a rounded string like `"1.2k sold"` | parse both; `sold_count_raw` only when exact; keep `sold_count_text` always | §3 band is `NULL`; no sold display |
| `category_path` | Shopee gives a path, Lazada often a leaf only | store as array, longest-first; `category_scope` = §5.2 | `category_scope` falls back to the run's query-cluster anchor; price position may be suppressed |
| `variants` | absent, or a list of option names, or a nested option matrix | flatten to `variant_labels[]`, dedupe casefolded | `variant_slug=NULL` → excluded from §5.3 breadth numerator, counted in `variant_unknown_count` |
| `seller` | id may be absent; name usually present | upsert on `(platform, coalesce(id, name_slug))` | `distinct_seller_count` undercounts → §3.5 flag `seller_identity_incomplete` set on the group |
| `image_url` | may be expired or hotlink-protected | fetch best-effort, store S3 key; keep raw URL regardless | vision corroboration (§2.5) skipped for that item |
| `review text` | may be absent entirely | §7 | review-theme metric suppressed for that listing |
| `review_date` | day, month-only, or relative ("2 months ago") | store `review_date_raw` + `review_date_precision`; relative strings resolved against `collected_at` and marked `'relative_bucket'` | excluded from §7.7 window analyses |
| `currency` | implicit on PH storefronts | default `'PHP'` when the platform is a PH storefront and no currency is given; **any explicit non-PHP currency → quarantine** | — |

A field whose parse result is physically implausible is recorded `implausible` and the value
is dropped, not clamped. Implausibility bounds, deliberately wide so they catch parse errors
rather than real prices: `price_minor` outside `[50, 50_000_000]` (₱0.50 – ₱500,000);
`total_net_weight_g` outside `[1, 50_000]`; `pack_count` outside `[1, 500]`;
`rating_count > 5_000_000`. These bounds exist to catch **unit/scale errors**, which are the
dominant real failure, not to judge prices.

**These bounds, the currency, and the `per_100g` unit-price denominator are
objective-derived config, not constants** (`CommerceBounds`, keyed by research objective and
market). They are the one place in this subsystem where a category and market shape leaks into
the code, and leaving them as literals would mean a non-weight-denominated category, or a
market with a different currency magnitude, silently quarantining its whole corpus. The
defaults shipped for the first objective are a six-order-of-magnitude-wide parse-error net, not
a view about what anything should cost — and §1.4 explains why a net that wide cannot be used
to resolve price scale.

### 1.4 Currency and price parsing

Money is stored as `bigint` centavos. Floats never touch a price.

```python
# aypo/analysis/commerce_price.py
PLAUSIBLE_MINOR = (50, 50_000_000)          # ₱0.50 .. ₱500,000.00
CANDIDATE_DIVISORS: Final[tuple[int, ...]] = (1, 100, 1_000, 100_000)

@dataclass(frozen=True, slots=True)
class ParsedPrice:
    amount_minor: int | None
    min_minor: int | None
    max_minor: int | None
    is_range: bool
    currency: str
    scale_divisor: int | None
    status: PriceStatus
    confidence: float          # 0.0 .. 1.0
    note: str | None

@dataclass(frozen=True, slots=True)
class CohortScale:
    cohort: tuple[str, str, str]        # (platform, collection_provider, payload_field)
    divisor: int | None                 # None => unresolved, whole cohort is 'ambiguous'
    confidence: float
    n_items: int
    n_anchors: int                      # items with an unambiguous string price to check against
    objective: Mapping[int, float]      # score per candidate divisor, stored for audit
    margin: float                       # best − runner-up on the objective
    note: str | None

def parse_price_text(raw: str) -> tuple[int | None, int | None, str]: ...
def resolve_cohort_scale(
    rows: Sequence[Mapping[str, object]],   # every row of one cohort in this run
    *, platform: CommercePlatform, provider: str, field: str,
) -> CohortScale: ...
def parse_price(
    raw: object, *, platform: CommercePlatform, cohort_scale: CohortScale,
    siblings: Mapping[str, object] | None = None,
) -> ParsedPrice: ...
def compute_discount(price: ParsedPrice, original: ParsedPrice) -> Resolved[Decimal]: ...
```

**String prices (Lazada-style).** Accept `₱1,234.00`, `PHP 1,234`, `P1234.5`, `1.234,00`
(EU grouping), `₱99.00 - ₱199.00`, `₱99 – ₱199`. Algorithm: strip currency glyphs and
whitespace; detect a range by splitting on `[-–—~]` **only when both sides independently
parse as money** (a hyphen inside a model code must not split); decide the decimal
separator by taking the **last** `,` or `.` that is followed by exactly 1–2 digits as the
decimal mark, treating all other separators as grouping; reject if >2 decimal digits
remain. Unparseable → `status='unparseable'`, value `None`.

**Numeric prices and the scale problem (Shopee-style).** Shopee-family payloads have
historically expressed price as an integer scaled by 100,000, and managed collectors
sometimes pre-divide it. **Never trust a constant** — and, equally, **never try to resolve
the scale from one row.**

*Why per-row inference cannot work.* The obvious design — test each divisor `d` for
admissibility against `PLAUSIBLE_MINOR` plus the sibling ordering constraints, and accept the
unique admissible one — fails for two independent reasons, and an earlier draft of this
document shipped it:

1. **The ordering constraints are scale-invariant.** `price_min ≤ price ≤ price_max` and
   `price ≤ original_price` are preserved exactly when every sibling is divided by the same
   `d`. They therefore contribute **zero** discriminating power between divisors. The only
   live constraint is the plausibility band.
2. **The plausibility band is six orders of magnitude wide and the divisors span five**, so
   admissibility overlaps almost everywhere. With `PLAUSIBLE_MINOR = [50, 50_000_000]`,
   divisor `d` is admissible for `raw ∈ [0.5·d, 500_000·d]`:

   | `d` | admissible `raw` |
   |---|---|
   | 1 | `[0.5, 5×10⁵]` |
   | 100 | `[50, 5×10⁷]` |
   | 1 000 | `[500, 5×10⁸]` |
   | 100 000 | `[5×10⁴, 5×10¹⁰]` |

   `raw = 4990` is admissible under `d ∈ {1, 100, 1000}` (₱4,990.00 / ₱49.90 / ₱4.99);
   `raw = 499000` is admissible under **all four**. Essentially every real payload value
   admits ≥2 divisors, so a per-row rule returns `ambiguous` for nearly the whole corpus and
   §5.2, `unit_price_per_100g`, `discount_pct` and the §2.7 unit-price flag all die silently.

*What we do instead: resolve the scale once per cohort.* A **cohort** is
`(platform, collection_provider, payload_field)` — the unit over which a collector's encoding
is actually constant. `resolve_cohort_scale` runs after collection and before
`commerce.normalise`, over every row of the cohort in the run:

1. **Anchors.** Rows whose payload *also* carries a string-formatted price (parsed by the
   unambiguous string rules above) or an explicitly decimal numeric give a direct per-row
   divisor observation `d_i = round(raw_i · 100 / minor_i)` snapped to the nearest candidate.
   If the cohort has `≥ 20` anchors and `≥ 95%` of them agree on one divisor, that divisor is
   adopted with `confidence = 0.95` and steps 2–3 are recorded but not used.
2. **Objective.** Otherwise score each candidate `d` on the whole cohort:
   `J(d) = w₁·in_band_fraction(d) + w₂·integral_centavo_fraction(d) + w₃·(1 − normalised
   MAD of log unit price under d)`, with `w = (0.5, 0.2, 0.3)`.
   `integral_centavo_fraction` is the share of rows where `raw·100 % d == 0`; a collector that
   scales by 100 000 emits values that are multiples of 1 000, which is a genuine fingerprint.
   The log-unit-price dispersion term is the only one that uses cross-row structure: the right
   divisor produces a tight, unimodal unit-price distribution, the wrong one shifts it bodily
   but does not change its shape, so this term is **only** discriminating in combination with
   the band — which is why it carries the smallest weight and never decides alone.
3. **Acceptance.** The best divisor is adopted only when `J(d*) − J(d₂) ≥ 0.10` **and**
   `in_band_fraction(d*) ≥ 0.90` **and** the cohort has `≥ 200` rows. Confidence is
   `min(0.90, 0.5 + 2·margin)` — capped below the anchor path's 0.95, because a fitted scale
   is weaker evidence than an observed one.
4. **Unresolved cohorts fail loudly, not quietly.** If acceptance fails, **every** row in the
   cohort gets `price_status='ambiguous'`, `price_minor = NULL`, and the run records
   `commerce_coverage.price_scale_unresolved = true` with the per-divisor objective. §5.2,
   unit price and discount are then suppressed **for that cohort as a whole** with the reason
   surfaced in the Research Run view. A corpus-wide price blackout that the user can see is an
   acceptable outcome; a corpus-wide price blackout that looks like 2,500 individually
   unlucky rows is not.
5. **Per row, once the cohort divisor is fixed:** `m = round(raw · 100 / d)`. If `m` falls
   outside `PLAUSIBLE_MINOR`, that row is `out_of_band` — a parse error on that row, not a
   reason to re-scale it. If sibling ordering is violated after scaling, the row is
   `unparseable`. Rows are never individually re-scaled to make them plausible, because that
   is exactly how a mis-parsed pack price becomes a confident wrong price.
6. **Pinning.** The resolved divisor is written to config keyed by `(platform,
   collection_provider, payload_field)` with the run id and anchor count that produced it, and
   re-verified every run against the anchor path. A pin is an observation with provenance, not
   an assumption, and a pin that fails re-verification invalidates itself rather than
   overriding the data.

`CANDIDATE_DIVISORS` is config, not a constant: the `1000` entry is speculative and should be
dropped as soon as one real dataset shows no collector uses it (OQ-5). **A wrong price is
worse than a missing price**, and a cohort-level refusal is how that principle is kept
affordable.

**Ranges.** `price_is_range = true`; `price_minor := price_min_minor` **for display only**.
If `price_max_minor / price_min_minor > 3.0` → `status='range_too_wide'`. *Why 3.0:* a >3×
intra-listing spread means the listing's options span different pack sizes or product tiers,
so no single scalar can be attributed to the listing; below 3× the spread is typically
flavour/colour options at one price tier. *At the boundary* (exactly 3.0) the item keeps
`status='ok'` with `quality_flags += 'price_range_wide'`.

**Range listings never enter a price statistic, at any width.** Taking the minimum of a range
as the listing's price biases a brand downward in exact proportion to how often its sellers
list ranges, and range-listing propensity is a seller-behaviour variable that differs
systematically between official stores and resellers — so using the minimum would make §5.2
partly a measure of listing style. Every item with `price_is_range = true` is therefore
excluded from §5.2, from `commerce_price_reference`, from `unit_price_per_100g_minor` and from
the §2.7 unit-price flag, and is counted in `n_excluded_price`. The range is still shown in
the Commerce Explorer, as a range.

**Discount.** `discount_pct = 1 - price_minor / original_price_minor`, computed only when
both are `status='ok'`, same currency, and `original_price_minor > price_minor`.
Clamp domain `[0, 0.95]`; a computed value `> 0.95` sets `discount_pct = NULL` and
`quality_flags += 'implausible_discount'`. *Why 0.95:* above 95% off, a scale mismatch or
a decoy "original price" explains the number far more often than a real promotion, and the
cost of showing a fabricated 99% discount to a marketing team is high.

**Unit price.** `unit_price_per_100g_minor = round(price_minor / total_net_weight_g * 100)`,
computed only when `price_status='ok'`, `pack_parse_confidence ≥ 0.80`, and
`total_net_weight_g` is in band. Otherwise `NULL`.

### 1.5 Pack, weight and quantity parsing

```python
@dataclass(frozen=True, slots=True)
class ParsedPack:
    pack_count: int | None
    unit_net_weight_g: Decimal | None
    total_net_weight_g: Decimal | None
    basis: Literal["g", "ml_as_g", "oz_converted"] | None
    confidence: float
    spans: tuple[tuple[int, int], ...]

def parse_pack(title: str, *, provider_hints: Mapping[str, object] | None = None) -> ParsedPack: ...
```

Ordered regex cascade over the casefolded title, first match wins per slot:

1. `(?P<n>\d{1,3})\s*(?:x|×|\*)\s*(?P<w>\d{1,4}(?:\.\d{1,2})?)\s*(?P<u>g|gr|gram|grams|kg|ml|l|liter|litre|oz)\b`
2. the mirrored form `(?P<w>…)(?P<u>…)\s*(?:x|×|\*)\s*(?P<n>\d{1,3})\b`
3. `(?P<w>…)(?P<u>…)` alone → `pack_count = 1`
4. `(?P<n>\d{1,3})\s*(?:pcs?|pieces?|packs?|sachets?|bags?|boxes?|bundles?)\b` → count only,
   weight unknown
5. provider `weight`/`attributes` fields, same unit grammar

Unit conversion: `kg×1000`, `l/litre×1000` (as `ml`), `oz×28.3495`. `ml` is recorded as
`basis='ml_as_g'` and **never mixed** with gram-basis items in the same price distribution
(§5.2 keys the reference distribution on `basis`). Confidence: 0.95 for cases 1–2, 0.85 for
case 3, 0.50 for case 4 (count without weight), 0.80 for case 5. Multiple conflicting
matches → take the highest-confidence, set `quality_flags += 'pack_conflict'`, and multiply
confidence by 0.7.

**Gross vs net weight is not distinguishable from a title.** This is a known, unfixable
source of ±10% noise and is the justification for the tolerance in §3.2.

---

## 2. Brand and product extraction from noisy titles

Marketplace titles are SEO artefacts, not product names. Four observable pathologies:
keyword stuffing (many brand tokens appended for search reach), operational boilerplate
(logistics/stock/authenticity claims), genuine bundles, and reseller titles that lead with
a shop or platform term. The design assumption is that **a title is a bag of spans with
unequal authority**, and that the correct output for an unresolvable title is
`composition='unresolved'`, not a guess.

### 2.1 Pipeline

```
raw payload
  → S0  provider fields as *candidates only*
  → S1  deterministic normalisation + span segmentation + noise tagging + corpus df stats
  → S2  LLM span extraction (batched, structured output)   [aypo/analysis/commerce_titles.py]
  → S3  deterministic composition decision rules
  → S4  multi-source corroboration / arbitration (noisy-OR)
  → S5  persist: commerce_items.{composition, product_label, product_slug, variant_*}
        + commerce_item_brand_links (one row per brand per source)
        + commerce_bundle_components (when a bundle is enumerable)
```

Stages S1 and S3–S5 are deterministic and unit-testable. Only S2 uses a model, and S2 is
never allowed to decide attribution on its own (S3/S4 can override it to `unresolved`).

**S2 output is a proposal, and every part of it is verified deterministically before use
(S2.5, §2.3).** A model is reliable at *finding* a brand surface in a title and unreliable at
*reporting where it is*; nothing the model computes is persisted as a number.

### 2.2 S1 — deterministic normalisation and noise tagging

1. NFKC normalise; map full-width forms; strip zero-width and control characters; collapse
   whitespace. Maintain an offset map back to `title_raw` so every span retains original
   char offsets (needed for evidence display).
2. Segment on separator runs `[|/,\-–—()\[\]{}·•+＋@#]{1,}` and on the literal token
   `"&"` when flanked by spaces. Segments are candidate spans.
3. Tag noise spans against `COMMERCE_NOISE_LEXICON`.
4. Extract pack/weight/quantity spans (§1.5) and tag them as structured, not noise.
5. Compute, once per run, the document frequency of each normalised token across all
   collected titles. Tokens with `df > 0.35 * corpus_size` are **non-discriminative**:
   excluded from `product_slug`, down-weighted in §4.4 lexical scoring, but still shown in
   the display title. *Why 0.35:* the corpus is query-biased, so the category term we
   searched for and platform boilerplate will both be near-ubiquitous; a token in more than
   a third of a query-biased corpus cannot discriminate between its members. *At the
   boundary* a token at exactly 0.35 is kept (inclusive), because the threshold's purpose is
   to remove the long ubiquitous tail, not to trim marginal terms.

**`COMMERCE_NOISE_LEXICON` — §34 compliance rule (hard).** The lexicon is a closed list of
at most 120 entries, each ≤3 tokens, and each entry **must** carry a `reason` tag from this
closed enum:

```python
# aypo/analysis/commerce_noise.py
NoiseReason = Literal[
    "logistics",            # shipping, delivery, courier, pickup terms
    "pricing_mechanic",     # sale, promo, voucher, discount mechanics
    "stock_state",          # onhand, ready stock, restock, preorder
    "platform_term",        # marketplace/app names and surfaces
    "authenticity_claim",   # seller assertions about legitimacy
    "generic_superlative",  # best, hot, top, new, viral-as-marketing
    "quantity_promo",       # "buy 1 take 1", "bundle deal" style mechanics
]
NOISE_LEXICON: Final[Mapping[str, NoiseReason]] = {...}
```

No tag exists for a flavour, occasion, demographic, cultural reference, or category word —
so there is **no legal way to add one**. CI test `test_noise_lexicon_tags_are_closed`
asserts the tag set equals exactly the seven literals, that every entry has a tag, that no
entry exceeds 3 tokens, and that the list length ≤120. This is the mechanical enforcement
of brief §1/§34 at the lexicon layer.

Note that `authenticity_claim` tokens are tagged as *noise for product identity* while
being **retained as a signal** in `listing_role_signals` (§2.7) — removing them from the
product slug and recording them as a seller-behaviour signal are different operations.

### 2.3 S2 — LLM span extraction

Model `claude-opus-5`, `thinking={"type": "adaptive"}`,
`output_config={"effort": "low"}`, structured output via
`client.messages.parse(output_format=TitleExtractionBatch)` → `response.parsed_output`.
Batch size 25 titles per call (§9 cost).

```python
# aypo/analysis/commerce_titles.py
class BrandSpan(BaseModel):
    brand_surface: str                 # exactly as written in title_raw
    char_start: int
    char_end: int
    quantity_attached: str | None       # "10", "2 x 40g", None
    model_attribution: brand_attribution_type
    model_confidence: float = Field(ge=0.0, le=1.0)

class TitleExtraction(BaseModel):
    item_ref: str
    brands: list[BrandSpan]
    product_label: str | None           # the product/sub-brand name as written
    variant_labels: list[str]           # flavour/edition/size-name options as written
    bundle_markers: list[str]           # "bundle","assorted","set","pack of", as written
    pack_spans: list[str]
    denoised_title: str                 # title with noise spans removed, order preserved
    model_composition: listing_composition
    out_of_category: bool
    unresolved_reason: str | None

class TitleExtractionBatch(BaseModel):
    extractions: list[TitleExtraction]

async def extract_titles(
    inputs: Sequence[TitleInput],
    *,
    client: AsyncAnthropic,
    batch_size: int = 25,
    effort: Literal["low", "medium"] = "low",
    prompt_version: str,
) -> list[TitleExtraction]: ...
```

**Prompt skeleton** (`aypo/llm/prompts/commerce_title_extraction.py`). Placeholders only.

```text
SYSTEM
You parse e-commerce product titles into structured spans. Titles are search-optimised and
may contain several brand names, logistics phrases, promotional phrases and pack
specifications.

Rules, in priority order:
1. Only report text that is literally present in the title. Never add a brand, product or
   variant that is not written there.
2. Do not use outside knowledge of which brands are important, popular, or competitors in
   any category. Do not rank brands. Do not normalise spellings to brands you know.
   You may use general knowledge only to judge whether a written token is brand-like.
3. Return character offsets into the title exactly as given.
4. A brand is "primary" only if the title's grammar makes it the subject of the listing.
   A brand listed without a quantity, in a trailing run of other brand names, is
   "mentioned_only".
5. If two or more brands each carry a quantity or pack specification, the listing is a
   multi_brand_bundle.
6. If you cannot decide which brand is the subject, set model_composition="unresolved" and
   mark every brand "ambiguous". An honest "unresolved" is correct; a guess is not.
7. Never judge authenticity, legitimacy, or whether a seller is authorised.

USER
Parse each title. Return one extraction per input, keyed by item_ref.

Worked example (illustrative only, not category guidance):
  item_ref: example-1
  title: "Brand A Product P 40g x10 | Brand B Product Q 40g x10 | assorted bundle | COD legit"
  → brands: [{Brand A, quantity_attached:"x10", co_listed}, {Brand B, quantity_attached:"x10", co_listed}]
    bundle_markers: ["assorted bundle"]; model_composition: multi_brand_bundle
    denoised_title: "Brand A Product P 40g x10 | Brand B Product Q 40g x10 assorted bundle"

  item_ref: example-2
  title: "Product P 40g Brand A Brand B Brand C Brand D {category_anchor} best seller onhand"
  → brands: [{Brand A, primary?}, {Brand B, mentioned_only}, {Brand C, mentioned_only},
             {Brand D, mentioned_only}]
    model_composition: unresolved   (four brand names, no quantities: keyword stuffing)

TITLES
{item_ref}\t{title_raw}\t{provider_brand_or_EMPTY}\t{provider_category_leaf_or_EMPTY}
... x25
```

The worked examples use `Brand A..D`, `Product P/Q` and `Term T` only. The one category token
in example 2 is the `{category_anchor}` **placeholder**, interpolated at render time from the
research objective — an earlier draft hard-coded the literal word `snack` here, which told the
model the category under research and is exactly the class of seeding brief §1/§34 forbids in
a production prompt. `test_prompt_static_text_is_allowlisted` (§10.2) now fails on any content
word in a prompt file that is neither a structural/operational term nor a `{placeholder}`.

### 2.3.1 S2.5 — deterministic verification of model output (hard)

Every field S2 returns is re-derived or re-checked in code before anything is written:

| S2 field | Verification | On failure |
|---|---|---|
| `brand_surface` | must occur literally in `title_raw` under the §2.2 normalisation | span dropped; `quality_flags += 'brand_surface_not_in_title'` |
| `char_start` / `char_end` | **recomputed in code**, never trusted: the model's offsets are used only as a hint to disambiguate repeated surfaces, and the authoritative offsets come from a deterministic search for `brand_surface` mapped back through the S1 offset map | if `title_raw[start:end] != brand_surface` after recomputation, the span is dropped |
| `denoised_title` | **discarded**. `commerce_items.title_denoised` is built in code by removing the S1 `noise_spans` from `title_raw`; the model's version may only *propose additional* spans, each of which must be a literal substring and must carry a `NoiseReason` tag to be applied | model-only spans without a tag are ignored |
| `pack_spans` | ignored where §1.5's deterministic cascade matched; used only to flag a disagreement (`quality_flags += 'pack_conflict'`) | §1.5 wins |
| `variant_labels` | each must be a literal substring of `title_raw` | non-literal labels dropped |
| `model_confidence` | **binned** to `{0.25, 0.50, 0.75, 1.00}` before entering §2.6 arithmetic, because an uncalibrated self-reported float carries no more than ~2 bits of real information and feeding its decimals into a promotion threshold manufactures false precision | — |
| `model_composition` | advisory only; §2.4 decides | — |

Batch position is also checked: `item_ref` values must be exactly the set sent, and any
extraction whose `item_ref` was not in the batch is dropped with a logged error. This is the
failure mode that grows with batch size (§9), so it must be detected rather than assumed away.

### 2.4 S3 — deterministic composition decision rules

Applied to S2 output; the model's `model_composition` is advisory.

Let `B` = brand spans, `Q` = subset of `B` with `quantity_attached is not None`,
`M` = bundle markers, `P` = count of distinct pack specifications in the title.

| Condition (first match wins) | `composition` | Attribution |
|---|---|---|
| `out_of_category` | `non_category_or_accessory` | all `mentioned_only` |
| `|Q| ≥ 2` or (`|B| ≥ 2` and `P ≥ 2`) | `multi_brand_bundle` | all `co_listed`; enumerate `commerce_bundle_components` |
| `M` non-empty and brands not enumerable | `assortment_unspecified` | all `ambiguous` |
| `|B| ≥ 4` and `|Q| = 0` | `unresolved` unless §2.6 promotes exactly one to `primary`; then `single_brand_*` with the rest `mentioned_only` | — |
| `|B| in (2,3)` and `|Q| = 0` | `single_brand_*` **only if** §2.6 yields one `primary` with margin; else `unresolved`, all `ambiguous` | — |
| `|B| = 1` | `single_brand_multi_variant` if `len(variant_labels) ≥ 2` or `M` non-empty, else `single_brand_single_product` | that brand `primary` if §2.6 confidence ≥ 0.70, else `ambiguous` + `unresolved` |
| `|B| = 0` | `unresolved` | no brand links; `quality_flags += 'no_brand_detected'` |

*Why the `|B| ≥ 4` cut:* one to three brand tokens in a title is commonly legitimate —
a brand plus a sub-brand plus a line name, or a true two-brand co-listing. Four or more
distinct brand surfaces with **no** quantity markers is the signature of keyword stuffing,
where the trailing brands are search bait. *At the boundary* (exactly 4) the rule applies
and the listing is `unresolved` unless corroboration promotes one brand, which is the
conservative direction.

### 2.5 Optional vision corroboration

`vision_corroboration_groups = 150` product groups are corroborated by image. **Selection is
by attribution need, not by listing count.** Groups are ranked by (1) `composition =
'unresolved'` or brand `margin < 0.15`, then (2) brand `conf` just below the 0.70 promotion
threshold, then (3) listing count as a final tie-break. An earlier draft selected the top 150
groups *by listing count*, which spends the entire vision budget on the most heavily resold
products and therefore raises brand-attribution confidence preferentially for brands
distributed through many resellers — a selection effect that flows straight into the §5.1
brand share. Ranking by need also buys more resolved attributions per dollar.

One product image per selected group is sent to `claude-opus-5` as a base64 image block with
`output_config={"effort": "low"}` and a structured output asking only: which brand
surfaces are legible on the packaging, which product/variant text is legible, and how many
distinct packages are visible (a bundle check). It returns spans with a confidence; it is
instructed not to name brands it cannot read. It becomes one more corroboration source in
§2.6 with base confidence 0.55 (packaging is legible but photos are frequently stock
images, reused across listings, or of a different variant).

*Why 150 groups and not all items:* §9 shows vision at ~$1.90/run for 150 calls; at 2500
items it would be ~$31/run for a corroboration signal, which is poor value against the
other sources. Groups, not items, because corroboration applies at product identity level.
The budget being smaller than the number of groups is precisely why the selection rule above
has to be need-based: any popularity-ordered selection turns a cost control into a bias.

### 2.6 S4 — corroboration and arbitration

Sources, their base confidences, and — the part that matters — their **dependence family**:

| Source | Family | Base confidence | Rationale |
|---|---|---|---|
| `title_llm` (`model_attribution='primary'`) | seller text | `0.40 * model_confidence_binned + 0.20` → in `[0.30, 0.60]` | the strongest single signal but subject to stuffing |
| `provider_brand_field` | seller text | 0.45, or 0.0 if placeholder (§1.3) | seller-entered, frequently wrong, never a placeholder |
| `seller_name_slug` contains the brand slug | seller text | 0.35 | official-store naming is informative but resellers also brand-name their shops |
| `variant_field` contains the brand slug | seller text | 0.30 | weak; option names often repeat the brand |
| `image_vision` | packaging | 0.55 | packaging is direct evidence, photos are often reused |

**Sources are not independent, and noisy-OR assumes they are.** The title, the brand field,
the option names and often the shop name are *all written by the same seller in the same
listing form*. A seller who types the wrong brand in the title has usually typed it in the
brand field too. Treating four correlated restatements of one seller's claim as four
independent witnesses is the single easiest way to manufacture a confident wrong attribution,
which is the exact failure this stage exists to prevent. So:

```
# within the seller-text family, the strongest source counts in full and every
# further source is discounted by RHO, because it is largely the same evidence again
RHO = 0.6
c_eff(strongest in family)        = c
c_eff(each further family member) = RHO * c
conf(b) = min(0.95, 1 - Π_i (1 - c_eff_i))      # noisy-OR across the discounted set
```

`image_vision` sits in its own family and is never discounted: a photograph of packaging is
evidence from a different generating process than the seller's text.

Promotion rule — brand `b` becomes `primary` iff **all three** hold:

1. `conf(b) ≥ 0.70`;
2. `conf(b) − max(conf(b') for b' ≠ b) ≥ 0.15` (margin; a sole candidate has margin `conf(b)`);
3. `n_distinct_sources(b) ≥ 2`.

*Why condition 3 is stated separately rather than left to the arithmetic.* The design intent
is "no brand is primary on one source alone". An earlier draft tried to encode that in the
threshold and got it wrong twice: it justified 0.70 as "the noisy-OR of two independent 0.55
sources agreeing, `1 − 0.55·0.55 = 0.6975`", but `1 − 0.55·0.55` is the noisy-OR of two **0.45**
sources (two 0.55 sources give `1 − 0.45² = 0.7975`), and 0.6975 is *below* 0.70, so the
nominal justifying case actually failed — while `title_llm` at `model_confidence = 1.0` scored
exactly `0.45·1.0 + 0.25 = 0.70` and **passed on one source**. An invariant that matters is
asserted directly and unit-tested (`test_primary_requires_two_sources`), not inferred from
arithmetic that can drift. The per-source cap of 0.60 on `title_llm` now makes the single-source
case structurally unreachable as well, so the rule is enforced twice over.

*Why 0.70 at all:* with the caps and the correlation discount, 0.70 is the point reachable by a
confident title read plus one corroborating source (`0.60` with `provider_brand_field` at
`0.6·0.45 = 0.27` → `0.708`), or by any mid-strength source plus packaging
(`0.45` with `0.55` → `0.7525`), and not by restatements of the title alone. *Why the 0.15
margin:* the failure mode this subsystem exists to prevent is silent mis-attribution between
two near-equal candidates; without a margin a 0.71 vs 0.70 split would be reported as a
confident brand attribution. *At the boundary:* a tie, or a margin in `[0, 0.15)`, or a single
source at any confidence, yields `composition='unresolved'` and all candidates `ambiguous` —
the listing still exists in presence counts as an unattributed listing, which is visible in
the UI as `unattributed_groups`. *Cap at 0.95:* a single listing can never establish a brand
with certainty; the residual 0.05 keeps downstream confidence honest.

`RHO = 0.6` is a judgement about how much a second seller-written field adds, not a measured
quantity; it is config, it is recorded in `extraction_version`, and it belongs in the first
calibration pass alongside OQ-2 (new OQ-11).

Brand slug normalisation: casefold, NFKD, strip diacritics, strip non-alphanumerics, collapse
repeats. Resolution to `brands.id` goes through the brand-entity service
(`upsert_brand_by_surface`, §10 cross-subsystem requirement) which **has no seed list** —
brands are created on first observation with their surface form.

### 2.7 Resellers, bundles, and listings we cannot verify

**Bundles are first-class, not flattened.**

```sql
create table commerce_bundle_components (
  id             uuid primary key default gen_random_uuid(),
  item_id        uuid not null references commerce_items(id) on delete cascade,
  brand_id       uuid references brands(id),
  brand_surface  text,
  product_label  text,
  variant_label  text,
  quantity       int,
  unit_net_weight_g numeric(10,2),
  component_group_id uuid,          -- resolved single-product group; FK added by ALTER in §3.5
  confidence     numeric(3,2) not null,
  source         text not null,      -- 'title_llm' | 'image_vision' | 'variant_field'
  created_at     timestamptz not null default now()
);

create table commerce_item_brand_links (
  id                uuid primary key default gen_random_uuid(),
  item_id           uuid not null references commerce_items(id) on delete cascade,
  brand_id          uuid references brands(id),
  brand_surface     text not null,
  brand_slug        text not null,
  attribution       brand_attribution_type not null,
  confidence        numeric(3,2) not null,
  char_start        int,
  char_end          int,
  source            text not null,
  extraction_version text not null,
  created_at        timestamptz not null default now(),
  unique (item_id, brand_slug, source)
);
create index commerce_item_brand_links_brand on commerce_item_brand_links (brand_id, attribution);

-- bundle group ↔ single-product group relations
create table commerce_group_links (
  parent_group_id uuid not null references commerce_product_groups(id) on delete cascade,
  child_group_id  uuid not null references commerce_product_groups(id) on delete cascade,
  relation        text not null,              -- 'contains' | 'variant_of'
  confidence      numeric(3,2) not null,
  primary key (parent_group_id, child_group_id, relation)
);
```

**Hard metric rule (enforced in `aypo/intelligence/commerce_metrics.py`).** A listing whose
`composition` is in `('multi_brand_bundle','assortment_unspecified','unresolved',
'non_category_or_accessory')` **never** contributes to a single-brand numerator or
denominator in §5. It is counted into one of `bundle_groups`, `unattributed_groups`,
`out_of_category_groups`, which the UI **must** render next to every brand share. A share
that quietly drops 18% of the sample is a lie by omission.

**Resellers.** We record, we do not judge. `listing_role_signals` keys:

```json
{
  "official_store_badge_reported": true|false|null,
  "seller_slug_matches_primary_brand": true|false|null,
  "authenticity_claim_tokens": ["<verbatim token>", "..."],
  "sellers_in_group": 37,
  "unit_price_vs_category_median_ratio": 0.31,
  "title_leads_with_seller_or_platform_term": true|false
}
```

Note the inversion we expose rather than resolve: an explicit authenticity claim in a title
is *weak evidence against* official-store status (official stores rarely need to assert it),
but it is also what a legitimate reseller writes. We present the signal and the count; we
never collapse it to a verdict.

**The flag that replaces counterfeit detection.** If
`unit_price_per_100g_minor < 0.40 * category_median_unit_price` →
`quality_flags += 'unit_price_far_below_category'`, and the item is excluded from §5.2 price
position while remaining in presence counts. Mandated caveat string:

> "This listing's unit price is far below the sample's category median. The most common
> explanations are a mis-parsed pack size, a bundle counted as a single unit, or a
> promotional price. AYPO does not assess product authenticity and this flag is not
> evidence about it."

*Why 0.40:* below 40% of the category median, a pack-size parse error is a more likely
explanation than a real price, so the flag's job is to route the row out of price
statistics and into review — not to accuse anyone. *At the boundary* (exactly 0.40) the
item is kept in the distribution.

---

## 3. Listing identity, product grouping, and platform-reported counters

### 3.1 The problem

The same physical product appears dozens of times across sellers, with different titles,
prices, images and sold counters. Counting listings therefore measures **seller listing
behaviour**, not product presence. A brand distributed through many small resellers will
out-"present" a brand sold mainly through one official store, with no difference in
assortment. Every presence metric in §5 is therefore computed over **product groups**, with
listing counts reported alongside as a distinct, explicitly-labelled quantity.

### 3.2 The product-grouping key

A two-stage design: a cheap deterministic **blocking key** that must agree exactly, then a
similarity merge **within** blocking neighbourhoods.

```python
# aypo/analysis/commerce_identity.py
WEIGHT_TOLERANCE: Final[float] = 0.075        # ±7.5%
_LOG_BASE: Final[float] = 1.0 + WEIGHT_TOLERANCE

def weight_bucket(total_net_weight_g: Decimal | None) -> int | None:
    """Log-spaced bucket; adjacent real SKUs differ by far more than one bucket."""
    if total_net_weight_g is None or total_net_weight_g <= 0:
        return None
    return round(math.log(float(total_net_weight_g)) / math.log(_LOG_BASE))

def blocking_key(
    *, brand_slug: str | None, product_slug: str | None, form_code: str | None,
    weight_bucket_: int | None, variant_slug: str | None,
) -> str:
    parts = [
        brand_slug or "~unbranded",
        product_slug or "~noproduct",
        form_code or "~noform",
        str(weight_bucket_) if weight_bucket_ is not None else "~noweight",
        variant_slug or "~novariant",
    ]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()
```

Component definitions:

- `brand_slug` — the `primary` brand's slug from §2.6, or `~unbranded` when `unresolved`.
  Bundles never enter single-product blocking: a `multi_brand_bundle` item gets
  `brand_slug = "~bundle:" + sha1(sorted component slugs)[:12]`, so bundles of the same
  composition group together and never with their components.
- `product_slug` — `product_label` normalised (casefold, strip diacritics/punctuation),
  with non-discriminative tokens (§2.2, `df > 0.35`) and pack/noise spans removed, then
  tokens sorted and joined. Sorting makes word order irrelevant, which is correct for
  keyword-stuffed titles.
- `form_code` — a coarse presentation code derived only from pack grammar:
  `single | multipack | box | sachet_pack | unknown`. Derived from the matched regex in
  §1.5, not from vocabulary.
- `weight_bucket_` — §3.2 above.
- `variant_slug` — the single highest-confidence entry in `variant_labels` normalised, or
  `~novariant`. Multi-variant listings slug as `~multivariant`.

*Why 7.5% buckets on weight:* net vs gross weight is not distinguishable from a title
(§1.5), sellers mistype, and providers round. Real adjacent SKUs in packaged goods are
typically ≥20% apart in net weight, so a 7.5%-wide bucket absorbs reporting noise with a wide
margin before it starts merging genuinely different pack sizes.

**A log-spaced bucket is not a ±7.5% tolerance, and an earlier draft described it as one.**
Bucket membership has arbitrary phase: 40 g and 43 g (7.5% apart) land in buckets 51 and 52,
while 45 g lands in 53. Two weights within 7.5% of each other may therefore be 0 or 1 buckets
apart, and the *blocking key alone* splits far more aggressively than the headline number
suggests. The actual tolerance is delivered by stage 2, whose neighbourhood spans ±1 bucket
(≈7.5%–15% in weight) and which merges across the edge when the rest of the key matches and
title similarity is high. 15% is still comfortably inside the ≥20% real-SKU separation, so
the design holds — but the tolerance lives in stage 2, not in the key.

**Items with no parsed weight** (`weight_bucket_ = None`, keyed `~noweight`) block together
and would otherwise never meet their weighted twins. They join the stage-2 neighbourhood of
every weight bucket sharing `(brand_slug, product_slug)`, and may merge into a weighted group
only at `sim ≥ 0.92` (above the normal 0.88, because the merge is asserting a weight the item
never reported). On merge the item adopts the group's representative weight and gains
`quality_flags += 'weight_imputed_from_group'`; it is excluded from `unit_price_per_100g_minor`
and therefore from §5.2 regardless. Two `~noweight` items merge with each other under the
ordinary rules and form a group with `weight_bucket = NULL`, flagged `weight_unknown`.

*Why weight is in the key at all, given it is noisy:* without it, a 40g single and a
500g multipack of the same product collapse into one "product", and then `§5.2` price
position becomes meaningless. Mis-splitting costs us a duplicate group; mis-merging costs
us a wrong price distribution. We prefer the former.

### 3.3 Stage 2 — similarity merge within neighbourhood

Neighbourhood of an item = all items sharing `(brand_slug, form_code, variant_slug)` with
`weight_bucket` within ±1, **or** sharing `(brand_slug, product_slug)` with any weight.

Pairwise score:

```
sim(a, b) = 0.50 * cosine(E_title(a), E_title(b))
          + 0.30 * token_set_ratio(product_slug(a), product_slug(b))     # RapidFuzz, /100
          + 0.20 * variant_agreement(a, b)                               # 1.0 equal, 0.5 one unknown, 0.0 differ
```

`E_title` is the `title_denoised` embedding (OpenAI `text-embedding-3-large`,
`dimensions=1536`), already stored in `commerce_item_embeddings`.

Decisions:

| `sim` | Action |
|---|---|
| `≥ 0.88` | candidate merge edge, `method='similarity_merge'` |
| `[0.78, 0.88)` | queue for LLM adjudication, up to `llm_adjudication_pairs = 300` per run, highest-`sim` first; unadjudicated pairs do **not** merge |
| `< 0.78` | separate groups |

**Transitive closure needs a cohesion guard.** Accepting every edge above 0.88 into a
union-find is *single linkage*, and single linkage chains: `a≈b` and `b≈c` merge `a` with `c`
even when `sim(a,c)` is far below threshold. Chaining produces exactly the false merge the
high operating point was chosen to avoid, and it does so more often precisely where listings
are dense — the popular products whose group counts and price medians matter most. So the
closure is computed and then **validated**:

1. Build components from edges `sim ≥ 0.88`, processing edges in descending
   `(sim, item_a_id, item_b_id)` order.
2. For each component, score every pair *within* it (pairs not already scored are scored now;
   components are small, this is cheap).
3. Accept the component only if `mean(sim) ≥ 0.85` **and** `min(sim) ≥ 0.78` over all internal
   pairs. Otherwise split it by repeatedly removing its lowest-`sim` edge and re-testing the
   resulting components, until every surviving component passes. Removed edges are recorded in
   `commerce_group_merge_evidence` with `decision='separate'` and `rationale='cohesion_guard'`.

This is average-linkage-with-a-floor rather than single linkage, and it preserves the stated
asymmetry: a split costs a duplicate group, a chain costs a corrupted price median.

**Determinism is a requirement, not a nicety.** `commerce.group` is idempotent on
`(run, method_version)` (§8.3), so re-running must reproduce the same groups bit for bit. Edge
processing order, component iteration order and medoid selection are therefore all fully
ordered by `(sim desc, item_id asc)`, with `item_id` as the final tie-break; nothing iterates a
Python `set` or a dict insertion order. `test_grouping_is_order_invariant` shuffles the input
item order and asserts identical `group_key` assignments.

*Why 0.88:* with these weights, 0.88 requires either near-identical denoised titles
(cosine ≳0.93 with matching variant) or a strong lexical identity plus variant agreement.
Empirically for duplicate-listing detection the expensive error is a false merge, which
silently deflates `distinct_group_count` and corrupts price medians, so the operating point
sits high. *Why the 0.78 grey band:* below 0.78 at least one of the three components must be
clearly disagreeing (e.g. differing variants), which is sufficient to keep products apart
without model spend. *At both boundaries:* inclusive lower bounds — exactly 0.88 merges,
exactly 0.78 is adjudicated. These two numbers are the pair most likely to need
recalibration on real payloads (OQ-6).

Merges are audited:

```sql
create table commerce_group_merge_evidence (
  id            uuid primary key default gen_random_uuid(),
  group_id      uuid not null references commerce_product_groups(id) on delete cascade,
  item_a        uuid not null references commerce_items(id) on delete cascade,
  item_b        uuid not null references commerce_items(id) on delete cascade,
  s_cosine      numeric(4,3),
  s_token       numeric(4,3),
  s_variant     numeric(4,3),
  sim           numeric(4,3) not null,
  decision      text not null,          -- 'merge' | 'separate'
  method        group_method not null,
  rationale     text,                   -- LLM rationale when adjudicated
  method_version text not null,
  created_at    timestamptz not null default now()
);
```

### 3.4 LLM adjudication (grey band only)

Batched 10 pairs per call, `claude-opus-5`, `effort="low"`, structured output
`list[{pair_ref, same_product: bool, confidence: float, rationale: str}]`. Prompt shows
both denoised titles, both variant lists, both pack parses, both category leaves, and asks
only: *"are these two listings offering the same physical product (same brand, same
product, same variant, same pack size)?"* with the instruction that different pack sizes or
different variants are **not** the same product, and that uncertainty must return
`same_product=false` with low confidence. Merge only when `same_product` and
`confidence ≥ 0.70`.

### 3.5 `commerce_product_groups` and presence metrics

```sql
create table commerce_product_groups (
  id                           uuid primary key default gen_random_uuid(),
  research_run_id              uuid not null references research_runs(id) on delete cascade,
  group_key                    text not null,          -- blocking key of the group's medoid
  brand_id                     uuid references brands(id),
  brand_slug                   text,
  product_label                text,                   -- medoid's label, for display
  product_slug                 text,
  variant_slug                 text,
  form_code                    text,
  weight_bucket                int,
  total_net_weight_g_repr      numeric(10,2),
  composition                  listing_composition not null,
  method                       group_method not null,
  confidence                   numeric(3,2) not null,
  medoid_item_id               uuid references commerce_items(id),
  listing_count                int not null default 0,
  distinct_seller_count        int not null default 0,
  platforms                    commerce_platform[] not null default '{}',
  median_price_minor           bigint,
  median_unit_price_per_100g_minor bigint,
  price_n                      int not null default 0,
  rating_count_sum             int,                     -- display only, never averaged
  quality_flags                text[] not null default '{}',
  method_version               text not null,
  created_at                   timestamptz not null default now(),
  unique (research_run_id, group_key, method_version)
);
create index commerce_groups_brand on commerce_product_groups (research_run_id, brand_id);

-- deferred FKs: these two columns are declared in §1.2 / §2.7, before this table exists
alter table commerce_items
  add constraint commerce_items_group_fk
  foreign key (product_group_id) references commerce_product_groups(id) on delete set null;
alter table commerce_bundle_components
  add constraint commerce_bundle_components_group_fk
  foreign key (component_group_id) references commerce_product_groups(id) on delete set null;

create table commerce_price_reference (
  id                   uuid primary key default gen_random_uuid(),
  research_run_id      uuid not null references research_runs(id) on delete cascade,
  platform             commerce_platform not null,
  category_scope       text not null,
  category_scope_source text not null,          -- 'category_path'|'query_cluster'|'unscoped'
  basis                text not null,           -- 'unit_price_per_100g'
  weight_basis         text not null,           -- 'g'|'ml_as_g'|'oz_converted'
  n_ref                int not null,            -- distinct product groups in the reference
  n_sellers            int not null,
  p25_minor            bigint,
  p50_minor            bigint,
  p75_minor            bigint,
  values_digest        bytea not null,          -- sha256 of the sorted value vector, for audit
  excluded_counts      jsonb not null default '{}'::jsonb,
  pass_no              smallint not null default 1,  -- 1 = pre-flag, 2 = post-flag (§2.7)
  method_version       text not null,
  as_of                timestamptz not null,
  created_at           timestamptz not null default now(),
  unique (research_run_id, platform, category_scope, basis, weight_basis, pass_no, method_version)
);
```

**Group-level `composition` and `brand_id` when members disagree (hard).** A group's members
are separate listings parsed independently, so they can disagree, and §5.1 filters on exactly
these two fields — an unstated rule here would be an unstated metric definition. The rules:

- **`composition`** is the **most conservative** value present among members, under the order
  `non_category_or_accessory` > `unresolved` > `assortment_unspecified` > `multi_brand_bundle`
  > `single_brand_multi_variant` > `single_brand_single_product`. If any member looks like a
  bundle or is unattributable, the group does not enter a single-brand share. Disagreement
  sets `quality_flags += 'composition_disputed'` and records the member histogram.
- **`brand_id`** is set only when members carrying a `primary` attribution hold **≥ 2/3** of
  the group's confidence mass for one brand slug *and* no other slug holds more than 1/6.
  Otherwise `brand_id = NULL`, `composition := 'unresolved'`, and the group is counted into
  `unattributed_groups`. A product whose listings disagree about its brand is not evidence
  about a brand.
- A group's `confidence` is the confidence-mass-weighted mean of its members' brand
  confidences, multiplied by 0.9 when `composition_disputed` is set.

Presence quantities, all per `(research_run_id, platform)` and never pooled across
platforms except where noted:

| Quantity | Definition | Status |
|---|---|---|
| `listing_count` | `count(commerce_items)` | **duplicate-inflated**; must always carry the label "listings (includes duplicate listings of the same product)" |
| `distinct_group_count` | `count(distinct product_group_id)` | the presence measure used by §5.1 |
| `distinct_seller_count` | `count(distinct seller_id)` excluding the synthetic unknown-identity row | seller breadth; see the identity caveat below |
| `group_seller_breadth` | sellers per group, median and max | distinguishes "one official store" from "many resellers" |
| `excluded_groups` | `bundle_groups + unattributed_groups + out_of_category_groups` | must be displayed with every brand share |

**UI contract (hard).** The API never returns `listing_count` for a brand or theme without
`distinct_group_count` and `distinct_seller_count` in the same object. The response model
makes them non-optional siblings so the frontend cannot render one alone.

**The unknown-seller row is one row, and that matters.** `commerce_sellers_identity` keys on
`coalesce(platform_seller_id, name_slug, 'unknown')`, so every listing with neither a seller id
nor a seller name collapses onto a *single* `commerce_sellers` row per platform. Two
consequences the design must not paper over: (a) `seller_id` is therefore never `NULL`, so a
flag conditioned on `seller_id IS NULL` would never fire — that column carries
`is_unknown_identity boolean not null default false`, set on the synthetic row, and
`seller_identity_incomplete` is set on a group when >5% of its items point at it; and
(b) many genuinely distinct anonymous sellers count as one, so `distinct_seller_count`
**understates** seller breadth whenever that flag is set, which the UI states rather than
hides. `group_seller_breadth` is suppressed entirely for a group with the flag set and more
than 20% unknown-identity items.

Cross-platform grouping: the *same* `group_key` can occur on both platforms, and
`platforms[]` records it. Group identity is shared; **metrics are computed per platform**
(§5), because price, rating and sold semantics are not comparable across platforms. The only
pooled figure permitted is `distinct_group_count` with a per-platform breakdown shown
beside it (OQ-8).

### 3.6 `sold_count` / `units_sold` — what may and may not be inferred

These are platform-rendered counters. Their period, their inclusion rules (cancelled
orders? multi-quantity orders? all variants?) and their refresh cadence are **not
documented by either platform**, differ between platform surfaces, and are further
transformed by whichever managed collector we use. The default and only honest value for
`sold_semantics` is `'unknown'` (OQ-1).

**Storage.** Keep `sold_count_raw` (integer, only when the payload gives an exact integer),
`sold_count_text` (verbatim, e.g. `"1.2k sold"`), `sold_count_band`, `sold_semantics`, and
`metrics_as_of`. Never overwrite one from the other.

**Bands** (`aypo/intelligence/commerce_metrics.py`):

```python
SOLD_BANDS: Final[tuple[tuple[int, int | None, str], ...]] = (
    (0, 0, "0"), (1, 9, "1–9"), (10, 49, "10–49"), (50, 99, "50–99"),
    (100, 499, "100–499"), (500, 999, "500–999"), (1_000, 4_999, "1k–4.9k"),
    (5_000, 9_999, "5k–9.9k"), (10_000, None, "10k+"),
)
```

*Why bands:* the platforms themselves round above 1,000 (`"1.2k sold"`), so storing or
comparing exact integers above that is false precision, and the upper band edges are placed at
the rounding boundaries the platform already applies. Below 1,000 banding **is** lossy — a
counter of 37 becomes `"10–49"` — and that is deliberate, not free: exact small integers invite
ratio comparisons ("3× as many") between counters whose periods and inclusion rules are
unknown, which is the error §3.6 exists to prevent. `sold_count_raw` retains the exact integer
for audit; the band is what any aggregation or display may use. *At the boundary:* bands are
inclusive on both ends and contiguous; a value of exactly 1,000 lands in `"1k–4.9k"`.

**Permitted inferences.** Within one platform, within this sample, at a point in time:

1. **Ordinal comparison.** "Listing L₁'s reported sold counter is in a higher band than
   L₂'s." Rank-based only (Spearman), never ratio-based.
2. **Band distribution.** The histogram of bands across a brand's matched groups.
3. **Presence of commercial activity.** A non-zero counter is evidence that *some*
   transactions have been reported by the platform for that listing — nothing about how
   many, when, or by whom.
4. **A group-level maximum.** `max(sold_count_band)` across a group's listings, as "the
   most-sold listing of this product in the sample is in band B".

`within_platform_spearman` is permitted for exactly one purpose: a rank correlation between a
listing's sold band and another **same-listing, same-platform, same-snapshot** ordinal
(`rating_count` band, `review_count` band, `result_rank`), reported as a diagnostic in the
Research Run view with its n and its own gate (`n ≥ 30`, both variables non-degenerate across
≥3 bands). It has no claim template and never reaches a finding; its purpose is to let us see,
for example, that the sold counter is largely a restatement of the platform's ranking. Any
other use is `SoldCountMisuse`.

**Forbidden operations, enforced by the type, not by a convention.** A guard function that
callers must remember to call is documentation with a stack trace; nothing stops
`sum(i.sold_count_raw for i in items)`. The counter is therefore not exposed as a number at
all outside the normaliser:

```python
class SoldCountMisuse(RuntimeError): ...

@final
class SoldCounter:
    """An ordinal platform counter. Deliberately not a number."""
    __slots__ = ("_band_index", "raw_text", "platform", "semantics", "as_of")

    @property
    def band(self) -> str: ...
    def __lt__(self, other: SoldCounter) -> bool: ...   # same platform only, else raises
    def __eq__(self, other: object) -> bool: ...

    def __add__(self, other): raise SoldCountMisuse("sold counters are not additive")
    __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = __add__
    __truediv__ = __rtruediv__ = __float__ = __int__ = __index__ = __add__

ALLOWED_SOLD_AGGREGATIONS: Final[frozenset[str]] = frozenset({
    "count_nonnull", "band_histogram", "within_platform_spearman", "max_band", "median_band",
})

def sold_count_guard(aggregation: str) -> None:
    if aggregation not in ALLOWED_SOLD_AGGREGATIONS:
        raise SoldCountMisuse(
            f"{aggregation!r} is not a permitted aggregation of a platform sold counter."
        )
```

`commerce_items.sold_count_raw` is readable only through the repository method that returns a
`SoldCounter`; `__lt__` raises across platforms, so a cross-platform comparison fails at
runtime rather than at review time. Three layers back this up: the dunder traps above, the
named-aggregation guard for the paths that legitimately take a string (API query params), and
a CI test `test_no_arithmetic_on_sold_columns` that greps the repo for arithmetic operators
adjacent to `sold_count` identifiers outside `commerce_metrics.py`.

Specifically forbidden, and why:

| Operation | Why forbidden |
|---|---|
| `sum(sold_count)` across listings or across a group | counters cover unknown and unequal periods; summing mixes incomparable windows and double-counts nothing coherently |
| `sold_count × price` | produces a revenue figure from an unaudited counter and a single promotional price observation |
| `sold_count / days_listed` | listing age is unknown; `first_seen_at` is our collection date, not listing creation |
| differencing `sold_count` across two snapshots | the counter's reset/window semantics are unknown, so a difference is an uninterpretable mixture (this is also why §6.5's commerce time series uses listing first-observation, not sold deltas) |
| comparing sold counters across platforms | different definitions, different surfaces, different collector transforms |
| market-share or sell-through derived from sold counters | requires a denominator we do not have and cannot estimate |
| calling it "sales", "units sold last month", "demand" | none of those is what the field is |

**Mandated UI display.** Any surface showing a sold counter must render all five of:

```
{sold_count_text or band}   ·   {platform label}
as reported {metrics_as_of:%-d %b %Y}
semantics: {sold_semantics}
```

plus this exact caveat (constant `CAVEATS["sold_count"]`):

> "Platform-reported counter. Neither platform publishes the period it covers or how it is
> calculated, and it is not audited. It is not sales, not revenue, and not comparable
> across platforms. AYPO does not sum, scale, annualise, or difference it."

The API response model for any object containing a sold counter has non-optional
`sold_semantics`, `metrics_as_of` and `caveat` fields, so the value cannot be serialised
without them.

### 3.7 The commerce analogue of brief §28

Brief §28 forbids treating visibility as endorsement and equating engagement with
preference. The commerce translations, each with the AYPO rule that implements it:

| Fallacy | Commerce form | AYPO rule |
|---|---|---|
| visibility = endorsement | "Brand A is everywhere on the marketplace" from listing counts | presence is reported as groups + sellers; listing counts always labelled as duplicate-inflated (§3.5) |
| engagement = preference | review counts or sold counters read as preference | review count scales with listing age and platform prompting; sold counter semantics unknown (§3.6) |
| positive comments = sales | review sentiment read as commercial performance | review labels are text observations only (§7); never joined to any sold figure in a claim |
| price = positioning | an observed price read as a brand's price strategy | observed prices are promotional point-in-time; §5.2 caveat states it |
| sample = market | assortment in our sample read as marketplace assortment | every metric scoped to the sample with n (§5) |
| absence = non-existence | "Brand A does not sell X" | absence is reported as a bounded prevalence statement, never as non-existence (§4.6) |
| rating = quality | star averages read as product quality | §5.4 forbids a bare mean, requires histogram + n + Wilson bound |
| matched listing = theme-built product | a lexical match read as a product designed for the theme | match strength stored as evidence with components (§4.5), never asserted as a fact |
| bundle presence = brand presence | a brand inside a bundle counted as its own listing | bundles excluded from single-brand shares, counted separately (§2.7) |
| many sellers = demand | reseller proliferation read as consumer pull | `group_seller_breadth` labelled as a seller-behaviour measure (§3.5) |
| **retrieved = assorted** | the collected corpus read as the platform's assortment | see §3.8: every item carries `result_rank`, every §5 caveat says the sample is the platform's *ranked* response to our queries |

### 3.8 The bias that conditions every number in §5: platform result ranking

Reviews are a ranked subset and the design says so in four places (§7.1, §7.7, §5.4, §5.5).
**Listings are a ranked subset too, and that is the larger effect**, because it conditions
*every* commerce metric rather than only the review ones. `max_items_per_query = 60` is one
provider result page, so the corpus is the platform's top-60 response to each query, ordered
by an undisclosed ranker that is known to weight sales velocity, ad spend, seller tier,
conversion and recency.

What that does to each metric, stated once here and referenced from each caveat:

| Metric | Direction of the bias |
|---|---|
| §5.1 brand share | over-weights brands the ranker surfaces: high-velocity, ad-buying and official-store sellers. It is a share of *retrieved* assortment, not of assortment. |
| §5.2 price position | the ranker's conversion weighting favours competitively-priced listings, so the reference distribution is compressed toward the middle and the tails are under-sampled. |
| §5.3 variant breadth | a brand's long-tail variants are exactly what the ranker omits, so breadth is systematically understated, and understated *more* for brands with many SKUs. |
| §5.4 rating distribution | rating is an input to most marketplace rankers, so collected listings are upward-biased on rating by construction. The `p4plus` bound is a bound on *retrieved* listings' ratings. |
| §4.6 absence | the prevalence bound is a bound on the retrieved set, not on assortment; a theme's products could exist and simply never rank for our queries. |

Three mitigations, none of which eliminates it:

1. **Record it.** `result_rank` is stored per item (§1.2) and `commerce_coverage` records
   `queries_run` and the rank depth reached. Any metric may be recomputed on a rank-truncated
   subsample (e.g. top-20 only) to show how rank-sensitive it is; `rank_sensitivity` is
   reported alongside §5.1 as the absolute change in a brand's share between the top-20 and
   full-60 subsamples. A share that moves >10 points is flagged `rank_sensitive`.
2. **Dilute it.** The strategy-B category-level component (brief §8) uses broader queries whose
   top-60 is a wider slice of assortment, and §5.1's denominator is restricted to the
   corpus-screened set (§5.1) rather than theme-targeted retrievals.
3. **Say it.** The phrase "what this run's queries retrieved" already appears in every §5
   caveat; it now reads "what this run's queries retrieved, in the order the platform chose to
   return them".

There is no defensible way to turn a ranked top-60 into a random sample of assortment without
a sampling frame we do not have, so this is a limitation to state, not a problem to solve.

---

## 4. Theme-to-commerce linking

### 4.1 The input contract (not owned here)

A theme is produced by the clustering/trend subsystem **from data** (brief §21: inspect the
cluster, then label it). This subsystem consumes it through a frozen contract and must
never construct one:

```python
# consumed from aypo/intelligence/themes.py (owned elsewhere)
class ThemeRef(BaseModel):
    theme_id: UUID
    research_run_id: UUID
    parent_cluster_id: UUID | None           # required by the s_prov sibling tier (§4.4)
    label: str                               # generated AFTER inspection, not before
    description: str
    discriminative_terms: list[TermWeight]   # >= 5, surface form + weight, data-derived
    representative_snippets: list[str]       # >= 3, verbatim from member content
    member_count: int
    first_observed: date | None
    last_observed: date | None
    centroid: list[float]                    # 1536-d, text-embedding-3-large
    embedding_model: str
```

If `embedding_model` differs from the model used for commerce embeddings, matching raises
rather than silently comparing incomparable vectors.

### 4.2 Two passes, and why the order matters

The naive design — "generate marketplace queries from the theme, then treat the results as
the theme's commerce expression" — is **selection on the outcome**. Every record the
queries return matches the theme by construction, so share, prevalence and correlation
figures computed on it are artefacts of our own query generator.

So matching runs in two passes with different jobs:

**Pass 1 — corpus-wide screen (canonical, free).** Every theme is scored against **every**
commerce item collected in the run, regardless of which query found it. The run's commerce
corpus includes a deliberate category-level component (brief §8 strategy B) that was collected
without reference to any theme, so this screen has a denominator that is not selected by *this*
theme. Cost: `12 themes × 2500 items = 30,000` cosine operations on stored vectors —
negligible, done in Postgres.

Two honest qualifications on the word "unbiased", which an earlier draft used here:

- The screened corpus is still **query-generated and rank-truncated** (§3.8). It is not a
  sample of assortment; it is unbiased only with respect to *this* theme's own queries.
- It also contains items retrieved by *other* themes' pass-2 queries from earlier in the run,
  and sibling themes can be highly correlated (this is why `s_prov` has a 0.4 sibling tier at
  all). Every pass-1 screen therefore records its denominator composition —
  `n_category_level` (strategy B), `n_brand_level` (A/C), `n_other_theme` (D/E) — and §5
  renders it. Where a metric needs a denominator uncontaminated by theme targeting, it uses
  the strategy-B subcorpus, and says `n_label = "category-level product groups"`.

```sql
-- pass 1, per theme
with bg as (
  select e.embedding from commerce_item_embeddings e
  join commerce_items i on i.id = e.item_id
  where i.research_run_id = $run and e.kind = 'listing_match_document' and e.model = $model
  order by random() limit 500
),
q as (
  select percentile_cont(0.50) within group (order by 1 - (embedding <=> $theme_vec)) as q50,
         percentile_cont(0.99) within group (order by 1 - (embedding <=> $theme_vec)) as q99,
         count(*) as n
  from bg
)
select i.id, 1 - (e.embedding <=> $theme_vec) as s_emb, q.q50, q.q99, q.n
from commerce_item_embeddings e
join commerce_items i on i.id = e.item_id
cross join q
where i.research_run_id = $run and e.kind = 'listing_match_document' and e.model = $model;
```

**Pass 2 — targeted expansion (collection, not measurement).** For every theme selected
below, generate marketplace queries to *collect more* evidence. Records collected this way are
scored by the same function, are stored and shown as evidence, and are **excluded from every
§5 numerator and denominator** (§5.1); `s_prov` is recorded on them but is not a term in the
score (§4.4). §5 metrics computed over a theme always report `n_corpus_screened` alongside
`n_matched` so the reader can see the denominator.

**Truncation to `max_items_per_theme = 120` must itself be specified**, because a theme's
12 queries (6 per platform × 2) can each return 60 records — up to 720 — and keeping "the
first 120" means keeping the top of the platform's ranking for whichever query happened to run
first. Records are kept **round-robin across (query, platform) in rank order**: rank 1 of every
query, then rank 2 of every query, and so on until the cap, with queries visited in a fixed
order. That keeps every query represented, keeps the rank distribution flat rather than
truncated, and is deterministic. The realised rank depth is written to
`commerce_theme_queries.results_kept` so §3.8's rank-sensitivity recomputation knows what it
is working with.

**Theme selection for pass 2** (`themes_investigated_per_run = 12`), ranked by
`0.5·z(member_count) + 0.5·z(novelty)`, where `novelty` is `1 − max cosine to any theme
investigated in a prior run of the same objective`. Ties broken by larger `member_count`.

**`pass1_max_score` is deliberately *not* a selection input.** An earlier draft gave it weight
0.3, which conditions the investigated set on the outcome being measured: themes that already
look commercially expressed get investigated, so the population over which §4.6 reports
absence is pre-filtered toward themes that have a commerce expression, and "we investigated 12
themes and 2 were absent" becomes an artefact of the ranker. Selection now uses only
properties of the *social* theme — how large it is and how new it is to this objective.

To protect the absence result further, **3 of the 12 slots are reserved** for themes in the
bottom tercile of `pass1_max_score` (filled by the same ranking within that tercile, and
released to the general pool if the tercile is empty). The reservation is recorded on
`commerce_theme_absences.selection_reason ∈ ('ranked','reserved_low_expression')` so a reader
can see which absences came from reserved slots. A theme with **no** apparent commerce
expression is exactly the case §4.6 exists to characterise; the design now guarantees some
get looked at rather than hoping the ranking lets them through.

### 4.3 Query generation

```python
# aypo/intelligence/theme_commerce.py
class GeneratedMarketplaceQuery(BaseModel):
    query_text: str
    query_kind: Literal[
        "theme_literal", "theme_term", "category_anchor",
        "brand_x_theme", "attribute_paraphrase",
    ]
    rationale: str                 # why this query, for the research log (brief §9)
    source_terms: list[str]        # which theme terms it came from
    platform: CommercePlatform

async def generate_marketplace_queries(
    theme: ThemeRef,
    *,
    client: AsyncAnthropic,
    category_anchor: str,                  # from the research objective, NOT a constant
    discovered_brands: Sequence[str],      # brands already observed in THIS run only
    platforms: Sequence[CommercePlatform],
    max_per_platform: int = 6,
    prompt_version: str,
) -> list[GeneratedMarketplaceQuery]: ...
```

**Prompt skeleton** (`aypo/llm/prompts/commerce_query_generation.py`),
`effort="medium"` (this is a generative judgement, unlike title parsing):

```text
SYSTEM
You translate a discovered research theme into marketplace search queries for an
e-commerce site. Marketplace search engines are literal keyword matchers: they match
product titles, not concepts.

Rules:
1. Use only the theme's own words and the terms supplied. Do not introduce category
   vocabulary, brand names, occasions, or cultural references that are not given to you.
2. The brands you may use are exactly the ones supplied in DISCOVERED_BRANDS. If the list
   is empty, generate no brand_x_theme queries.
3. A marketplace query is 1–4 words. Long natural-language phrases return nothing.
4. Some themes are behavioural or cultural and have no product vocabulary. If a theme
   cannot be expressed as a product search, return fewer queries and say so in rationale.
   Returning zero queries is an acceptable and informative answer.
5. Every query needs a rationale naming which theme term it came from.

USER
THEME_LABEL: {theme.label}
THEME_DESCRIPTION: {theme.description}
DISCRIMINATIVE_TERMS: {term: weight, ... up to 10}
REPRESENTATIVE_SNIPPETS: {3 verbatim snippets, each truncated to 200 chars}
CATEGORY_ANCHOR: {category_anchor}
DISCOVERED_BRANDS: {brand surfaces discovered in this run, may be empty}
PLATFORM: {platform}
MAX_QUERIES: {max_per_platform}

Example shape only (generic placeholders, not guidance):
  theme "Theme X" with term "Term T" → {"query_text": "Term T {category_anchor}",
    "query_kind": "category_anchor", "rationale": "Term T is the highest-weight
    discriminative term of Theme X; paired with the category anchor to keep results in
    category.", "source_terms": ["Term T"]}
```

**Deterministic validity filter** applied to every generated query before it runs:

1. Reject length `< 2` characters or `> 60` characters.
2. Reject if, after noise-lexicon removal, no token remains.
3. Reject if the query has drifted off-theme. §4.4 argues at length that **absolute cosines
   are not comparable across corpora**, so an absolute threshold here would contradict the
   rest of the design (an earlier draft used a bare `cosine < 0.50`, which for a 1–4 word
   query against a long theme document is in the range where almost anything can land
   depending on the embedding model's length behaviour). The test is instead relative: embed
   the query, and reject it if `cosine(query, TMD)` is below the **median** `cosine(term, TMD)`
   over the theme's own `discriminative_terms` embedded individually. A query that is less
   theme-like than the theme's own median term is drift by construction, and the reference is
   built from the theme itself, so it needs no corpus. *At the boundary* the query is kept.
4. Reject exact duplicates of any `research_queries.query_text` already run in this run.
5. Reject any query containing a brand surface not in `discovered_brands` (§34 guard: the
   generator must not invent brands).

```sql
create table commerce_theme_queries (
  id                 uuid primary key default gen_random_uuid(),
  research_run_id    uuid not null references research_runs(id) on delete cascade,
  theme_id           uuid not null,
  research_query_id  uuid references research_queries(id),
  platform           commerce_platform not null,
  query_text         text not null,
  query_kind         text not null,
  rationale          text not null,
  source_terms       text[] not null default '{}',
  generated_by       text not null,          -- model id
  prompt_version     text not null,
  validity_rejected  boolean not null default false,
  rejection_reason   text,
  results_returned   int,
  results_kept       int,
  status             text not null default 'pending',   -- pending|running|done|failed|skipped
  error              text,
  created_at         timestamptz not null default now(),
  unique (research_run_id, theme_id, platform, query_text)
);
```

A theme that produces **zero valid queries** is not an error. It is recorded with
`results_returned = 0` and feeds directly into §4.6; "this theme has no product vocabulary"
is a finding about the theme.

### 4.4 Matching: what text, what signals, what weights

**Listing match document (LMD)** — what we embed for a listing. Not the raw title:
keyword stuffing makes raw-title embeddings dominated by tokens shared across the whole
corpus, which compresses cosine toward a high, uninformative baseline.

```python
def build_listing_match_document(
    item: CommerceItem, group: ProductGroup | None, reviews: Sequence[CommerceReview],
) -> str:
    return "\n".join([
        f"brand: {item.primary_brand_surface or 'unknown'}",
        f"product: {item.product_label or ''}",
        f"variants: {', '.join(item.variant_labels)}",
        f"pack: {item.pack_count or ''} x {item.unit_net_weight_g or ''}{item.weight_basis or ''}",
        f"category: {' > '.join(item.category_path)}",
        f"title: {item.title_denoised or item.title_raw or ''}",
    ])
```

Review text is **deliberately excluded** from the LMD and enters as a separate scored
component `s_rev` (below), so that a match driven by buyer language is distinguishable in
the evidence record from a match driven by the product itself.

**Theme match document (TMD):**

```python
def build_theme_match_document(theme: ThemeRef) -> str:
    terms = ", ".join(f"{t.surface}" for t in theme.discriminative_terms[:10])
    snips = " / ".join(s[:200] for s in theme.representative_snippets[:3])
    return f"theme: {theme.label}\ndescription: {theme.description}\nterms: {terms}\nexamples: {snips}"
```

**Components.**

- `s_emb = cosine(LMD, TMD)` — absolute cosine, stored for audit.
- `s_emb_cal = clip((s_emb − q50) / (q99 − q50), 0, 1)` where `q50`/`q99` are quantiles of
  `s_emb` over **the full screened corpus for this theme** (pass 1 computes every cosine
  anyway, so there is no reason to estimate a q99 from a 500-row subsample — the 99th
  percentile of 500 draws is the ~5th order statistic and is noisy enough to move every
  downstream score). `q50`, `q99` and `n` are stored on every match row so the number is
  reproducible. If `q99 − q50 < 0.02` the corpus is degenerate for this theme; `s_emb_cal` is
  set to 0 and `gate_failures += 'degenerate_background'`.

  **`s_emb_cal` is a within-run rank measure, not evidence of presence.** This is the most
  important sentence in §4. The transform is *relative to the corpus*, so the top-scoring
  items of **every** theme calibrate to ≈1.0 — including a theme with no commerce expression
  whatsoever, whose best listing is merely the least-unrelated one. An earlier draft missed
  this and let `s_emb_cal` carry half the fused score and the entire absence test, with the
  consequence that `max_match_score ≥ 0.50` for every theme by construction and the
  `absent_with_coverage` state in §4.6 — the subsystem's flagship negative result — was
  **mathematically unreachable**. `s_emb_cal` therefore ranks candidates and gates them; it
  can never on its own establish that a theme *is* expressed, and it is never consulted to
  establish that a theme is *not*.
- `s_lex ∈ [0,1]` — **theme-term mass coverage**, not BM25. For the theme's discriminative
  terms `T` with weights `w_t`:

  ```
  s_lex = Σ_{t ∈ T, t matches LMD} w_t · (1 − df_t)  /  Σ_{t ∈ T} w_t · (1 − df_t)
  ```

  with `df_t` the §2.2 corpus document frequency, matching on normalised token n-grams.
  An earlier draft specified BM25 "normalised by the theme's self-score", which is not bounded
  in `[0,1]` as the type claims — BM25 is unbounded above and its self-score is not a
  meaningful normaliser. The coverage form is bounded by construction, monotone in the
  evidence, interpretable ("68% of the theme's discriminative mass is literally present in this
  listing"), and — the property the absence test depends on — **genuinely zero** when none of
  the theme's terms appears anywhere.
- `s_rev ∈ [0,1]` — `cosine(review_label_document, TMD)` for the listing's single review-label
  document (§7.5 builds one per listing, not one per review), put through the same calibration
  transform. **`NULL`, not `0.0`, when the listing has no labelled reviews.** An earlier draft
  set it to `0.0` and justified it as "this component only ever adds" — which is false under a
  fixed-weight convex combination: a zero in a 0.15-weighted slot *subtracts* up to 0.15 from
  the score relative to an otherwise identical listing that has reviews. That penalises
  listings without reviews, and review presence tracks listing age, seller follow-up and
  platform rank, so the penalty would land systematically on the same brands every time.
  `NULL` means "component unavailable" and triggers weight renormalisation below.
- `s_prov ∈ {0.0, 0.4, 1.0}` — `1.0` if the item was discovered by a query generated from
  **this** theme, `0.4` if from a sibling theme sharing a cluster parent (requires
  `ThemeRef.parent_cluster_id`, added to the §4.1 contract), `0.0` otherwise.
  **`s_prov` is metadata and is not a term in the score** — see below.

**Fusion:**

```python
BASE_MATCH_WEIGHTS: Final[Mapping[str, float]] = {
    "s_emb_cal": 0.55, "s_lex": 0.28, "s_rev": 0.17,     # sums to 1.00
}
MATERIALITY: Final[Mapping[str, float]] = {"s_emb_cal": 0.35, "s_lex": 0.25, "s_rev": 0.25}

def fuse(components: Mapping[str, float | None]) -> tuple[float, int, Mapping[str, float]]:
    avail = {k: v for k, v in components.items() if v is not None}
    total = sum(BASE_MATCH_WEIGHTS[k] for k in avail)
    weights = {k: BASE_MATCH_WEIGHTS[k] / total for k in avail}      # renormalised
    score = sum(weights[k] * avail[k] for k in avail)
    n_material = sum(1 for k, v in avail.items() if v >= MATERIALITY[k])
    return score, n_material, weights
```

The realised `weights` are stored on every match row (the column already exists), so a score is
always reproducible from its components.

**Why `s_prov` was removed from the score.** It is endogenous: a pass-2 item is in the corpus
*because* a query generated from this theme retrieved it, so provenance is a record of our own
pipeline's execution, not evidence about the listing. Giving it weight 0.10 shrinks the
circularity without removing it, and worse, `s_prov` and `s_lex` are strongly correlated for
pass-2 items — the query was built from the theme's own discriminative terms, so a listing that
matched the query necessarily contains some of them. Two correlated endogenous components were
jointly able to carry an item over the line while appearing to be "two agreeing signals".
`s_prov` is still computed, still stored, still shown in the evidence drill-down, and is used
to *partition* matches (§5.1 counts only pass-1-screened matches); it is not used to score
them.

**Gates (hard), in order.** A match is `supported` only if **all three** hold:

1. **Semantic gate** — `s_emb_cal ≥ 0.35`. *At the boundary* the gate passes.
2. **Score** — `match_score ≥ 0.60`.
3. **Two material components** — `n_material ≥ 2`, where a component is material at the
   `MATERIALITY` level above.

| outcome | `tier` | Treatment |
|---|---|---|
| all three gates pass | `supported` | counted in §5 metrics, shown as evidence |
| `match_score ≥ 0.45` but any gate fails | `weak` | shown under "weak signals" only, never counted |
| `match_score < 0.45` | not stored | per-theme maximum and per-theme `max(s_lex)` retained, for §4.6 |

*Why condition 3 is a separate hard gate rather than an emergent property of the weights.* The
design intent — **no single signal can produce a supported match** — was previously left to
arithmetic ("`s_emb_cal = 1.0` alone gives 0.50 < 0.60"), and arithmetic of that kind does not
survive a weight change, a renormalisation, or a component becoming unavailable. Under
renormalisation it in fact breaks immediately: with `s_rev` absent the weights become
`0.66 / 0.34`, and `s_emb_cal = 1.0` alone scores 0.66 ≥ 0.60. Stating the invariant directly
makes it survive recalibration, and it is what `test_semantic_gate` and
`test_supported_requires_two_material_components` assert. It also does the work the removal of
`s_prov` requires: because `s_emb_cal` is a *relative* measure that always maxes out somewhere
in the corpus, the second material component must come from `s_lex` or `s_rev`, both of which
can be genuinely zero for a theme with no commerce expression.

*Why 0.60:* with the base weights it is the point at which strong semantic rank plus moderate
literal support, or moderate semantic rank plus strong literal support plus buyer language,
are both reachable while neither alone is. *Why 0.45 for the weak floor:* below 0.45 not even
two components can be jointly moderate, so the pair is noise and storing it would flood the
evidence table. *At all boundaries:* inclusive lower bounds. All four numbers (0.60, 0.45,
0.35, the materiality levels) are config and are governed by OQ-2.

**Calibration procedure (must run before the thresholds are trusted — OQ-2).** Sample 200
`(theme, listing)` pairs stratified across deciles of `match_score`; two independent human
labels each with a third for disagreements; choose the `supported` threshold as the lowest
score at which precision `≥ 0.80` on a held-out half. The same exercise must report precision
**separately for pass-1 and pass-2 items**, since the two have different selection mechanisms
and a threshold calibrated on the pooled set would be wrong for both. Until that has been run,
0.60 is a reasoned default, `method_version` records which calibration (if any) produced it,
and the UI copy for match strength says "evidence score", never "confidence".

### 4.5 Match strength stored as evidence, never asserted

```sql
create table commerce_theme_matches (
  id                 uuid primary key default gen_random_uuid(),
  research_run_id    uuid not null references research_runs(id) on delete cascade,
  theme_id           uuid not null,
  item_id            uuid not null references commerce_items(id) on delete cascade,
  product_group_id   uuid references commerce_product_groups(id) on delete set null,
  match_score        numeric(4,3) not null,
  tier               match_tier not null,
  s_emb              numeric(5,4) not null,
  s_emb_cal          numeric(4,3) not null,
  s_lex              numeric(4,3) not null,
  s_rev              numeric(4,3) not null,
  s_prov             numeric(4,3) not null,
  weights            jsonb not null,
  background_q50     numeric(5,4),
  background_q99     numeric(5,4),
  background_n       int,
  semantic_gate_passed boolean not null,
  n_material_components smallint not null,         -- §4.4 gate 3
  material_components text[] not null default '{}',
  pass_no            smallint not null,            -- 1 = corpus screen, 2 = targeted
  matched_on         text[] not null default '{}', -- which components contributed > 0
  evidence_terms     text[] not null default '{}', -- theme terms that fired in s_lex
  evidence_review_ids uuid[] not null default '{}',
  adjudicated        boolean not null default false,
  adjudication_rationale text,
  embedding_model    text not null,
  method_version     text not null,
  created_at         timestamptz not null default now(),
  unique (theme_id, item_id, method_version)
);
create index commerce_theme_matches_theme on commerce_theme_matches (theme_id, tier, match_score desc);
create index commerce_theme_matches_group on commerce_theme_matches (product_group_id, theme_id);
```

Every row is a *measurement with its components visible*. The API never returns a boolean
`matches: true`; it returns `{tier, match_score, components, evidence_terms,
evidence_review_ids, method_version}`, and the Commerce Explorer (brief §31.6) renders the
component breakdown on the evidence drill-down so a marketer can see **why** a listing was
linked to a theme — including when the link rests mainly on review language rather than on
the product.

### 4.6 When a theme has no commerce expression

This is a result, not a failure. Three states, and the distinction between them is the
whole point:

| State | Meaning | Precondition |
|---|---|---|
| `absent_with_coverage` | we looked properly and found nothing | all five gates below pass |
| `inconclusive_low_coverage` | we looked, but not enough to say anything | any gate fails |
| `not_investigated` | we did not look (e.g. no marketplace provider available) | commerce collection unavailable or theme outside the top 12 |

**Gates for `absent_with_coverage`:**

1. `groups_screened ≥ 150` — distinct **product groups** from the corpus-wide pass-1 screen,
   not listings, and not from the theme's own queries. See the unit discussion below.
2. **No supported match** (§4.4), **and** `max(s_lex) = 0` across the screened corpus — no
   discriminative term of the theme appears literally in any listing's match document — **and**
   no review-label slug of any screened listing overlaps the theme's label set.
3. **Top-K adjudication.** The `K = 20` highest-`s_emb` groups for the theme are sent to
   `claude-opus-5` (batched 10 per call, `effort="low"`, structured output) with the question
   *"does this listing's product express this theme?"*, and **all 20 must come back negative**.
4. `queries_attempted ≥ 3` distinct valid queries, **or** `zero_valid_queries = true` with a
   recorded rationale (a theme with no product vocabulary legitimately produces none).
5. `≥ 2` platforms attempted, or the unavailable ones recorded in `platforms_unavailable`
   with a reason.

**Why gates 2 and 3 replaced `max_match_score < 0.45`.** That gate could never fire. Because
`s_emb_cal` is calibrated against the run's own corpus (§4.4), the corpus-maximum listing
clips to `s_emb_cal = 1.0` for *every* theme, giving `max_match_score ≥ 0.55·1.0 = 0.55 > 0.45`
unconditionally — so `absent_with_coverage` was unreachable and every theme would have been
reported as `inconclusive_low_coverage` forever. The replacement rests on two quantities that
can genuinely be zero (`s_lex`, review-label overlap) plus a cheap bounded adjudication of the
nearest neighbours, which is the right division of labour: the embedding is good at *ranking*
candidates and bad at absolute thresholds, and a model asked 20 direct questions is cheap
(≈24 calls ≈ \$0.6/run across 12 themes) and auditable. `max_match_score` is still computed and
stored, as a diagnostic shown next to the absence, never as a gate.

**Why the bound is computed on groups, not listings.** The prevalence bound
`p_ub = 1 − 0.05^(1/n)` (the exact one-sided 95% bound; ≈ `3/n` by the rule of three) assumes
`n` **independent** draws. Screened *listings* are emphatically not independent: §3.1 is an
entire section about the same physical product appearing dozens of times, and the duplicate
factor is both large and brand-dependent. Computing the bound on 2,500 listings that represent
perhaps 700 products would overstate the coverage by roughly the duplication factor and produce
a confidently tiny `p_ub` — the exact "non-independent observations treated as independent"
error. Distinct product groups are the closest thing in this data to one observation per
opportunity, so `n := groups_screened`. At `n = 150`, `p_ub ≈ 2.0%`; below that the bound
degrades fast (`n = 50 → p_ub ≈ 5.8%`), which is too loose to be interesting, so that is where
the state flips to `inconclusive_low_coverage`. *At the boundary* (exactly 150) the gate passes.

**Groups are still not independent, and the bound is still conservative-sounding rather than
conservative.** Groups retrieved by the same query are correlated through the platform's
ranker (§3.8) and through the query itself; the effective sample size is smaller than the group
count. The design response is to report, beside `p_ub`, the number of **distinct queries** and
**distinct category scopes** the screened groups came from, and to apply a design-effect
inflation `n_eff = groups_screened / (1 + (m̄ − 1)·ρ̂)` where `m̄` is the mean groups per query
and `ρ̂` the intra-query correlation of the match indicator (estimated from the run; defaults
to a conservative 0.3 when it cannot be estimated). `prevalence_upper_bound` is computed on
`n_eff`, and both `n_eff` and `ρ̂` are stored. This does not make the sample random — nothing
can — but it stops the headline number from being a function of how aggressively we paged.

```sql
create table commerce_theme_absences (
  id                    uuid primary key default gen_random_uuid(),
  research_run_id       uuid not null references research_runs(id) on delete cascade,
  theme_id              uuid not null,
  state                 commerce_absence_state not null,
  queries_attempted     int not null default 0,
  zero_valid_queries    boolean not null default false,
  zero_query_rationale  text,
  platforms_attempted   commerce_platform[] not null default '{}',
  platforms_unavailable commerce_platform[] not null default '{}',
  unavailable_reason    text,
  listings_screened     int not null,
  groups_screened       int not null,
  n_effective           numeric(8,2) not null,  -- design-effect adjusted group count
  intra_query_rho       numeric(4,3) not null,
  n_distinct_queries    int not null,
  n_category_level      int not null,           -- strategy-B groups in the denominator
  selection_reason      text not null default 'ranked',  -- 'ranked'|'reserved_low_expression'
  max_s_lex             numeric(4,3) not null default 0,
  topk_adjudicated      int not null default 0,
  topk_positive         int not null default 0,
  max_match_score       numeric(4,3),
  max_match_item_id     uuid references commerce_items(id),
  prevalence_upper_bound numeric(6,5),         -- 1 - 0.05^(1/n_effective)
  confidence_level      numeric(3,2) not null default 0.95,
  narrative             text not null,
  method_version        text not null,
  created_at            timestamptz not null default now(),
  unique (research_run_id, theme_id, method_version)
);
```

**Narrative templates** (exact, selected by state; these are claim templates and go through
the §6 validator like any other):

- `absent_with_coverage`:
  > "No marketplace listing in this sample matched {theme.label} above the evidence
  > threshold ({threshold}). {groups_screened} distinct product groups ({listings_screened}
  > listings) across {platforms} were screened, including {n_category_level} collected by
  > category-level queries that did not reference this theme; none of the theme's
  > discriminative terms appeared in any listing, and the {topk_adjudicated} closest listings
  > were individually reviewed and rejected. If {p_ub}% or more of this sample's assortment
  > expressed this theme, at least one matching listing would be expected 95% of the time
  > (effective sample {n_effective} after adjusting for products retrieved by the same query).
  > This describes the collected sample, in the order the platform chose to return it, not
  > marketplace assortment."
- `inconclusive_low_coverage`:
  > "Marketplace coverage for {theme.label} is insufficient to say whether it has a
  > commerce expression: {listings_screened} listings screened ({gate_failures}). AYPO is
  > not reporting an absence here."
- `not_investigated`:
  > "{theme.label} was not investigated on marketplace sources in this run
  > ({unavailable_reason})."

A theme that is strong socially and absent in commerce is one of the more interesting
outputs this subsystem can produce, and it is surfaced in Trends & Opportunities
(brief §31.4) as a labelled hypothesis — an *investigation area*, with no implied
recommendation.

---

## 5. Comparative metrics that are legitimate within a sample

Brief §24 forbids overall competitor scores and requires sample-scoped statements. These
five metrics are the complete permitted set for commerce. Nothing else is computed, and no
metric is combined into a composite index.

### 5.0 The metric contract

```python
# aypo/intelligence/commerce_metrics.py
class MetricResult[T](BaseModel):
    metric: str
    scope: MetricScope                 # run, platform, category_scope, theme, brand
    value: T | None
    n: int                             # the denominator the reader must see
    n_label: str                       # what n counts: "product groups", "priced listings", ...
    ci: tuple[float, float] | None
    ci_method: str                     # 'wilson' | 'cluster_bootstrap_groups' | 'none'
    n_effective: float | None          # design-effect adjusted n where clustering applies
    denominator_composition: dict[str, int]   # {"category_level": 180, "other_theme": 40, ...}
    excluded: dict[str, int]           # {"bundle_groups": 7, "unattributed_groups": 12, ...}
    family_size: int                   # comparisons computed in the same family this run
    rank_sensitivity: float | None     # §3.8: |value(top-20 subsample) − value|
    suppressed: bool
    suppression_reason: str | None
    counting_policy: str
    as_of: datetime
    caveat: str = Field(min_length=40)   # NON-OPTIONAL, min_length makes "" invalid
    method_version: str
```

Requires Python ≥3.12 (PEP 695 generics) and Pydantic ≥2.6.

`caveat` is a required field with a minimum length, so a metric cannot be serialised
without it. The frontend contract (brief §28, §33) is: **a `MetricResult` renders its
`caveat` and its `n` in the same visual block as its `value`.** Caveat strings live in one
module, `aypo/intelligence/commerce_caveats.py`, as a `Final[dict[str, str]]` of
`str.format` templates, so copy changes are reviewable in one diff.

All five metrics are computed **per platform**. Pooling across platforms is permitted only
for `distinct_group_count` (§3.5).

### 5.1 Share of matched product groups by brand

```python
def share_of_matched_groups(
    *, theme_id: UUID, platform: CommercePlatform,
    policy: Literal["primary_only_exclusive", "co_listed_fractional"] = "primary_only_exclusive",
    denominator: Literal["pass1_screened", "category_level_only"] = "pass1_screened",
) -> list[MetricResult[float]]: ...
```

**The denominator is restricted to pass-1-screened groups. This is the whole point of §4.2 and
an earlier draft did not enforce it.** `G_T` was previously "product groups with at least one
`tier='supported'` match", with no pass restriction — which silently readmits exactly the
selection-on-the-outcome that §4.2 exists to prevent, and does so in the worst possible place.
Pass-2 items are in the corpus *because* queries generated from theme `T` retrieved them, and
one of the five query kinds is `brand_x_theme`, built from `discovered_brands`. A brand the
query generator happened to use therefore gets extra listings collected *for this theme*, and
those listings land in both the numerator and the denominator of that brand's share. The
measured share would partly be a readout of our own query generator's brand choices.

So:

```
G_T = { product groups g on this platform
        : ∃ a tier='supported' match (g, T) with pass_no = 1 }
```

Pass-2 matches are stored, shown as evidence, and counted in `n_pass2_supported` beside the
metric — they are how a marketer gets to *see* theme-relevant listings — but they never enter a
numerator or a denominator. With `denominator='category_level_only'` the set is further
restricted to groups whose listings were all retrieved by strategy-B category queries, giving a
denominator untouched by any theme targeting at all; it is the conservative variant, reported
whenever it has `≥ 10` groups.

Default policy `primary_only_exclusive`:

```
D   = { g ∈ G_T : g.composition ∈ ('single_brand_single_product','single_brand_multi_variant')
                  and g.brand_id is not null }
n_b = | { g ∈ D : g.brand_id = b } |
share_b = n_b / |D|
excluded = { bundle_groups, unattributed_groups, out_of_category_groups }   # |G_T| − |D|
```

95% Wilson interval on `share_b` with `n = |D|`:

```
centre = (p̂ + z²/2n) / (1 + z²/n)
half   = z·√( p̂(1−p̂)/n + z²/4n² ) / (1 + z²/n)        z = 1.96
```

Alternative named policy `co_listed_fractional`: a group with `k` `co_listed` brands
contributes `1/k` to each, and bundles are included. It is never the default and the
response's `counting_policy` field states which one produced the number. Both are offered
because neither is objectively right: exclusive counting understates brands that mainly
appear in bundles, fractional counting invents a weight. Making the choice explicit and
visible is the honest resolution.

**Suppression:** `|D| < 10` → `suppressed=true`,
`suppression_reason='insufficient_matched_groups'`. *Why 10:* with fewer than 10 groups a
single listing moves share by ≥10 points and the Wilson half-width exceeds ±0.25 (0.263 at
`n = 10, p̂ = 0.5`), so the number carries no information a reader could act on. *At the
boundary* (exactly 10) the metric is computed and the interval, which will be wide, is shown.

**Multiplicity.** A run computes this metric for every (theme × brand × platform) cell —
12 themes × however many brands were discovered × 2 platforms is routinely several hundred
intervals, and at 95% nominal coverage roughly one in twenty excludes the truth. Shares are
**descriptive** and are presented as such, with no "significantly higher" language available in
any claim template. Where an *ordering or difference* between two brands is asserted, it goes
through §6.1, which is family-wise controlled by BH-FDR. `MetricResult` carries
`family_size` (the number of cells computed in the same family) and the UI renders
"1 of {family_size} comparisons in this run" next to any brand-vs-brand reading. The one thing
the design does not do is let a reader treat a non-overlapping pair of Wilson intervals as a
test; the caveat says so.

**Rank sensitivity.** Per §3.8, each share is recomputed on the top-20-by-`result_rank`
subsample and the absolute difference is reported as `rank_sensitivity`; a difference >10
points sets `quality_flags += 'rank_sensitive'` on the result.

**Caveat (exact):**

> "Share of product groups in this sample that matched {theme} and are attributed to a
> single brand: {n} groups from {m} listings and {s} sellers, collected {date_from}–
> {date_to} from {platform}, of which {n_category_level} were retrieved by category-level
> queries that did not reference this theme. {excluded_total} further matched groups were
> excluded as multi-brand bundles ({bundle_groups}), unattributable titles
> ({unattributed_groups}) or out of category ({out_of_category_groups}); {n_pass2_supported}
> groups retrieved by this theme's own queries are shown as evidence but excluded from this
> share. Counting policy: {counting_policy}. This is a property of what this run's queries
> retrieved, in the order the platform chose to return them — not of marketplace assortment,
> category sales, or consumer preference. It is one of {family_size} such comparisons in this
> run and the intervals are descriptive, not a test of difference."

### 5.2 Price position relative to the category distribution

```python
def price_position(
    *, brand_id: UUID, platform: CommercePlatform, category_scope: str,
    basis: Literal["unit_price_per_100g"] = "unit_price_per_100g",
) -> MetricResult[int]: ...
```

**`listing_price` is not an available basis, and an earlier draft offered it.** A percentile of
raw listing prices taken across a reference distribution that mixes 40 g singles with 500 g
multipacks measures *pack size*, not price position, and would rank a brand as "premium"
precisely for selling larger packs. Only a size-normalised basis can place two listings on one
axis. Listing price remains visible in the Commerce Explorer as an observed price; it is not a
positioning statistic. `unit_price_per_100g` is itself a category-shaped choice (it assumes a
weight-denominated product); the denominator is config, keyed by the research objective, with
`per_100ml` and `per_piece` as the other admissible bases — never two of them mixed in one
distribution.

`category_scope` resolution, first available wins:
1. `category_path[0:2]` joined by `>` (platform category, two levels — specific enough to be
   comparable, coarse enough to have members);
2. else the run's category-level query cluster id (strategy-B queries, brief §8);
3. else `'unscoped'`, which forces suppression.

Reference distribution `D` = all **groups** (not listings — one observation per product, so
reseller proliferation cannot shift the distribution) on this platform in this
`category_scope` with `price_status='ok'`, a defined value on the chosen `basis`, matching
`weight_basis` (gram-basis and `ml_as_g` never mixed), and without
`'unit_price_far_below_category'` or `'price_range_wide'` flags.

Range listings (`price_is_range`) are excluded at source (§1.4), as are `is_degraded` rows.

**Two-pass construction, because the flag depends on the median it feeds.** The §2.7
`unit_price_far_below_category` flag is defined relative to the category median, and the
category median is computed over the groups the flag has not excluded — a circularity that must
be resolved by stating an order rather than left to implementation. Pass 1 computes the
reference over all eligible groups with no unit-price flag applied and persists it with
`pass_no = 1`; the flag is then evaluated against that pass-1 median; pass 2 recomputes the
reference excluding flagged groups and persists it with `pass_no = 2`. **Pass 2 is the
reference used for reporting**, pass 1 is retained for audit, and the flag is never re-evaluated
against pass 2 (one iteration only, so the result is deterministic).

Brand value: `v_b = median(basis over b's groups in scope)`.

Percentile by midrank, **against the reference excluding the brand's own groups**:

```
D_b   = D \ { groups attributed to b }
pct_b = ( |{x ∈ D_b : x < v_b}| + 0.5·|{x ∈ D_b : x = v_b}| ) / |D_b|
```

Excluding the brand's own groups matters whenever a brand holds a large share of the scope: a
brand that *is* 40% of the reference distribution is mechanically pulled toward the 50th
percentile by its own contribution, so a dominant brand would always look mid-market. `|D_b|`
is what `n_ref` reports.

Reported as an integer percentile, never with decimals, together with `D`'s
`p25 / p50 / p75` and `n_ref = |D|`. Cached in `commerce_price_reference`.

**Suppression:** `n_ref < 12` → suppressed. *Why 12:* the percentile grid is `1/n_ref`, so
below 12 each observation moves the percentile by more than 8 points, and each quartile of
the reference rests on fewer than 3 observations — the IQR we display beside it would be
noise. *At the boundary* (exactly 12) the metric is computed. Additionally
`price_n < 3` for the brand → suppressed with `'insufficient_brand_prices'`, since a median
of one or two promotional observations is not a price position. And `n_sellers < 5` in the
reference → suppressed with `'reference_seller_concentrated'`: a reference distribution drawn
from a handful of sellers describes those sellers' pricing, not the scope's.

**Caveat (exact):**

> "{brand}'s median {basis_label} in this sample sits at the {pct}th percentile of the
> {n_ref} priced product groups in {category_scope} on {platform} (reference quartiles
> {p25} / {p50} / {p75}). Each price is a single observation captured {as_of} and includes
> whatever platform promotion was active; these are not list prices, not trade prices, and
> not a price index. {n_excluded_price} groups were excluded for unparseable, ambiguous or
> implausible prices."

### 5.3 Variant breadth

```python
def variant_breadth(*, brand_id: UUID, platform: CommercePlatform, scope: MetricScope) -> MetricResult[int]: ...
```

```
breadth_b = | { g.variant_slug : g ∈ groups(b, scope),
                g.variant_slug not in (NULL, '~novariant', '~multivariant'),
                g.confidence ≥ 0.60 } |
variant_unknown_count = | { g ∈ groups(b, scope) : g.variant_slug in (NULL,'~novariant') } |
```

Display caps at the 25 most-listed variants with "+k more". *Why confidence ≥ 0.60:* the
variant slug comes from the same extraction as the brand; below 0.60 a "variant" is as
likely to be a stray title token, and inflating breadth with parse noise flatters whichever
brand has the noisiest titles. *At the boundary* it is counted. No suppression threshold —
a breadth of 1 is a legitimate observation — but `variant_unknown_count` is always rendered
beside it.

**Caveat (exact):**

> "{breadth} distinct product variants of {brand} were observed in this sample on
> {platform}; {variant_unknown_count} of its {total} matched groups had no parseable
> variant. Observed variant breadth reflects what sellers listed and what this run's
> queries retrieved. It is not {brand}'s product range and not a measure of distribution."

### 5.4 Rating distribution

A bare mean star rating is forbidden. `rating_summary` returns, per brand and scope:

1. the pooled 1–5 histogram where `rating_histogram` is available (§1.3 validation), else
   `histogram_available=false`;
2. `n_ratings` (sum of `rating_count`);
3. `mean` to one decimal place, only alongside 1 and 2, never alone;
4. `p4plus` = share of ratings ≥ 4, with its 95% **Wilson lower bound** used for any
   ordering between brands:

```
LB = ( p̂ + z²/2n − z·√( p̂(1−p̂)/n + z²/4n² ) ) / (1 + z²/n)      z = 1.96
```

Ordering between brands is by `LB`, which penalises small samples automatically.

**Ratings are clustered within listings, and a pooled Wilson interval ignores that.** A brand
whose 4,000 ratings come from three listings has nothing like 4,000 independent observations:
ratings share a product, a seller, a photo set and a review-solicitation practice, and the
listing is the unit that repeats across the population. Pooling them and taking `z·√(p̂q̂/n)`
produces an interval that can be an order of magnitude too narrow, and since the ordering rule
is "by `LB`", too-narrow intervals convert directly into confident wrong orderings. The bound
is therefore computed by a **cluster bootstrap over product groups**: resample groups with
replacement 2,000 times, recompute `p4plus` from the resampled groups' pooled histograms, and
take the 2.5th percentile as `LB`. The analytic Wilson bound is retained and displayed beside
it as `LB_naive`, and the design effect `deff = (LB_naive − p̂) / (LB − p̂)` is stored — when
`deff > 3` the UI says the ratings are concentrated in few listings.

**Suppression of ordering:** a brand is excluded from any ordering unless `n_ratings ≥ 20`
**and** `n_groups ≥ 5`. *Why 20 ratings:* at `n = 20, p̂ = 0.90`, `LB ≈ 0.70` — still
informative; below 20 the bound collapses far enough that ordering is governed by review volume
rather than by rating, which would be a visibility metric masquerading as a quality metric.
*Why 5 groups:* with fewer, the cluster bootstrap has too few clusters to resample and the
brand's rating profile is really one or two products' profile. *At both boundaries* ordering
includes the brand.

When `histogram_available=false`, the mean is shown with `n_ratings` and the caveat gains
the sentence "The platform did not supply a star breakdown, so the distribution's shape is
unknown."

**Caveat (exact):**

> "Based on {n_ratings} platform ratings across {n_groups} product groups on {platform},
> captured {as_of}; the interval is bootstrapped over product groups because ratings within a
> listing are not independent observations. Marketplace ratings are a self-selected minority
> of buyers, are moderated by the platform, are affected by listing age and by seller
> follow-up, and in some listings show signals of templated reviewing. Rating is also an input
> to the platform's own result ranking, so the listings AYPO retrieved are rated more highly
> than the assortment they were drawn from. Differences within this sample do not measure
> product quality or consumer preference."

### 5.5 Review-text themes

Open-label aggregation over `commerce_review_labels` (§7). For each discovered label `L`
within a scope:

```
reviews(L)  = count of reviews carrying L with confidence ≥ 0.60
listings(L) = count of distinct items whose reviews carry L
groups(L)   = count of distinct product groups
share(L)    = reviews(L) / reviews_total_in_scope       # share of collected review TEXT
```

`share(L)` is reported **without a confidence interval**, deliberately: reviews cluster within
listings and listings within sellers, the collected set is a ranked subset of a self-selected
population, and an interval computed as though the reviews were independent draws would be the
most misleading number on the page. `reviews(L)`, `listings(L)` and `groups(L)` are shown
together so a reader can see when a share rests on one listing's review page.

**Suppression:** a label with `listings(L) < 3` is not reported. *Why 3:* a label confined
to one listing may be an artefact of a single product page, one seller's follow-up
campaign, or one review-bombing episode; two still permits a single near-duplicate cluster
spanning two listings. Three distinct listings is the minimum at which "recurring" is a
defensible word. *At the boundary* it is reported, with its low support visible.

Labels are never ranked against social labels by raw frequency, because the two samples
have different and unknown selection mechanisms. They are joined only through the shared
label vocabulary (§7.3), which lets the UI say "this label appears in both samples" without
comparing magnitudes.

**Caveat (exact):**

> "Labels generated from the text of {n_reviews} reviews across {n_listings} listings on
> {platform}, with no predefined label list. Reviews are written by a self-selected
> minority of buyers, and the reviews a platform returns are ranked rather than sampled, so
> these frequencies describe this text sample only — not buyers, not purchases, and not the
> category. {n_flagged} reviews carry templated- or incentivised-review signals and are
> labelled as such."

---

## 6. No causality

### 6.1 What is computed

Only co-occurrence, with gates. Stored in `commerce_cooccurrence_stats`.

```sql
create table commerce_cooccurrence_stats (
  id              uuid primary key default gen_random_uuid(),
  research_run_id uuid not null references research_runs(id) on delete cascade,
  unit            text not null,         -- 'product_group' | 'brand_theme' | 'weekly_bucket'
  left_kind       text not null,         -- 'theme' | 'brand'
  left_id         uuid, left_label  text,
  right_kind      text not null,         -- 'brand' | 'theme'
  right_id        uuid, right_label text,
  platform        commerce_platform,
  n_total         int not null,
  n_left          int not null,
  n_right         int not null,
  n_both          int not null,
  lift            numeric(8,4),
  pmi             numeric(8,4),
  jaccard         numeric(5,4),
  wilson_low      numeric(5,4),
  wilson_high     numeric(5,4),
  lead_lag_rho    numeric(5,4),
  lead_lag_weeks  int,
  lead_lag_p      numeric(7,6),
  n_buckets       int,
  n_permutations  int,
  fdr_q           numeric(4,3),
  fdr_significant boolean,
  gate_passed     boolean not null,
  gate_failures   text[] not null default '{}',
  method_version  text not null,
  created_at      timestamptz not null default now()
);
```

**Unit of analysis is always stated.** Two units are used:

- `product_group` — rows are matched product groups; left = theme membership, right = brand
  attribution.
- `brand_theme` — rows are `(brand, theme)` cells across the whole run; left = social
  presence of the brand in the theme, right = commerce presence.

```python
# aypo/intelligence/commerce_cooccurrence.py
@dataclass(frozen=True, slots=True)
class Contingency:
    n_total: int; n_left: int; n_right: int; n_both: int

def lift(c: Contingency) -> float:          # (n_both/n_left) / (n_right/n_total)
def pmi(c: Contingency) -> float:           # log2( (n_both·n_total) / (n_left·n_right) )
def jaccard(c: Contingency) -> float:       # n_both / (n_left + n_right - n_both)
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]: ...
```

**Orientation invariance (hard).** `lift`, `pmi` and `jaccard` are all symmetric in
left/right, but the fourth gate below — `wilson_low(n_both, n_left) > n_right/n_total` — is
**not**: swapping which entity is "left" can flip a pair from reported to suppressed. A
relationship whose existence depends on an arbitrary labelling choice is not a finding. Two
rules fix it: (a) the canonical orientation for storage is always `left_kind='theme'`,
`right_kind='brand'`; and (b) the Wilson gate must pass in **both** orientations —
`wilson_low(n_both, n_left) > n_right/n_total` **and** `wilson_low(n_both, n_right) >
n_left/n_total` — before `gate_passed` is true. Requiring both is also the conservative
direction, and `test_cooccurrence_is_orientation_invariant` asserts that swapping the inputs
yields an identical `gate_passed` and `lift`.

**Minimum-cell gates** (all must pass or `gate_passed=false` and the statistic is stored but
never rendered as a relationship):

| Gate | Threshold | Why |
|---|---|---|
| `n_both ≥ 5` | 5 | with 4 or fewer co-occurrences, lift is dominated by integer granularity: one additional group changes it by ≥20% |
| `n_left ≥ 10` and `n_right ≥ 10` | 10 | marginal below 10 makes the expected cell `< 1`, where lift is unbounded and meaningless |
| `n_total ≥ 30` | 30 | below 30 units the Wilson interval on `n_both/n_left` spans more than half the unit interval |
| `wilson_low(n_both, n_left) > n_right/n_total` **and** the mirrored form | — | lift is only reported when its 95% lower bound still exceeds the base rate in both orientations; this is what makes "more often than its overall share would suggest" a defensible sentence |
| `fdr_significant` | BH at `q = 0.10` across the whole co-occurrence family in the run | see below |

**Multiplicity is controlled across the whole co-occurrence family, not just the lead-lag
test.** A run tests every (theme × brand × platform × unit) cell that clears the minimum-cell
gates — commonly 200–600 tests. At a per-test 5% screen that is 10–30 pairs passing the Wilson
gate on noise alone, and those are precisely the "surprising" pairs a reader's eye lands on. An
earlier draft applied BH-FDR only to `lead_lag`, a test that §6.2 shows can never run, leaving
the tests that *do* run entirely uncontrolled. The exact one-sided p-value for each cell is
computed from the hypergeometric distribution (Fisher's exact, `P(X ≥ n_both)` under fixed
margins), BH is applied at `q = 0.10` across all cells in the run, and `fdr_q`,
`fdr_significant` and the family size are stored in the columns that already exist. **Only
`fdr_significant` rows may fill the `relative_cooccurrence` template**; the rest are visible in
the Research Run view as screened-but-not-significant, which is itself informative.

**The `brand_theme` unit needs a margin-preserving null.** For `unit='product_group'` the rows
are groups and the hypergeometric null is appropriate. For `unit='brand_theme'` the rows are
`(brand, theme)` cells, and those are badly non-independent: a brand appears in as many rows as
there are themes, a theme in as many as there are brands, and large brands are present
everywhere. Against an independence null, lift there mostly measures brand size. The null for
this unit is therefore a **degree-preserving permutation** of the bipartite social-presence
matrix (2,000 curveball/swap randomisations preserving both the per-brand and per-theme
marginals), with the p-value the share of randomisations reaching `n_both`. `n_permutations`
and the null type are stored. A pair that is significant against an independence null but not
against the degree-preserving null is reported as **not** significant, with
`gate_failures += 'explained_by_marginals'` — that difference is the single most common way a
co-occurrence table produces a false insight.

### 6.2 Temporal statistics — and why they will usually be suppressed

The lead-lag statistic is specified, and the specification's main output in the MVP is a
**refusal**.

```python
@dataclass(frozen=True, slots=True)
class LeadLagResult:
    rho: float; lag_weeks: int; p_value: float
    n_buckets: int; n_permutations: int
    gate_failures: tuple[str, ...]

def lead_lag(
    social_weekly: Sequence[int],
    commerce_weekly: Sequence[int],
    *, max_lag: int = 4, min_buckets: int = 8, n_permutations: int = 2000,
    rng: np.random.Generator,
) -> LeadLagResult | None: ...

def bh_fdr(p_values: Sequence[float], q: float = 0.10) -> list[bool]: ...
```

Series definitions: `social_weekly` = count of analysed social items associated with the
theme by publication week; `commerce_weekly` = count of matched listings **first observed**
in that week. Spearman rho at integer lags `−4..+4`, p-value by 2,000 circular shifts of
the commerce series, multiplicity controlled by Benjamini–Hochberg at `q = 0.10` across all
tested pairs in the run.

*Why 2,000 permutations:* p-value resolution `1/2000 = 5×10⁻⁴`, which is finer than the BH
threshold for any realistic number of tested pairs (≲200), so the permutation grid is not
the limiting factor. *Why `min_buckets = 8`:* Spearman on fewer than 8 points cannot reach
`p < 0.05` at the smallest achievable two-sided p for `n = 7` under permutation while also
tolerating a lag shift that consumes buckets; 8 is the floor at which a lagged correlation
is even expressible. *Why `max_lag = 4` weeks:* beyond a month of lag, the number of
plausible intervening common causes grows faster than the information in the series.

**The decisive point: within a single research run, `commerce_weekly` is degenerate.**
`first_seen_at` is *our collection timestamp*, so every item collected in one run falls in
one or two weeks. Neither platform reliably exposes a listing creation date, and
`sold_count` cannot be differenced (§3.6). Therefore the `min_buckets = 8` gate **fails by
construction in a single-run MVP**, `lead_lag` returns `None`, and the row is stored with
`gate_failures = ['insufficient_temporal_coverage']`. The UI must render
*"insufficient temporal coverage to test any time relationship between social and
marketplace signals in this run"* — not a correlation, not a trend arrow, not a "momentum"
badge. This satisfies brief §22's requirement that growth and decline claims require
sufficient temporal coverage, and it is a deliberate design output rather than a gap:
the honest answer at MVP scale is that the test is not available.

**The longitudinal path is not an automatic escape hatch, and specifying it carelessly would
re-create the exact artefact this section refuses.** It is true that ≥8 weekly runs against the
same objective populate `commerce_item_snapshots` and give `min_buckets` something to work
with. But "first observed in week *w*" across runs is **not** a listing-creation series: a
listing enters the corpus the first week it either (a) rises into the platform's top-60 for one
of our queries, or (b) is retrieved by a *newly generated* query. And follow-up queries are
generated from social themes (§4.3). So a theme rising socially in week *w* causes new queries
in week *w*, which retrieve "newly first-observed" listings in week *w* — manufacturing a
lagged social→commerce correlation out of nothing but our own query scheduler. Running
`lead_lag` on that series would produce precisely the spurious result §6.5 exists to refuse,
with a p-value attached.

The gate passing on bucket count is therefore necessary and not sufficient. `lead_lag` stays
disabled until **all** of the following hold, each recorded on the stats row:

1. **A frozen query panel.** A fixed set of queries, chosen once and re-run identically every
   period, with identical paging depth and `max_items_per_query`. The commerce series is
   computed from the panel only; theme-generated queries continue for evidence collection and
   are excluded from the series.
2. **Rank-controlled membership.** An item counts as newly observed in week *w* only if it was
   absent from the panel's results in weeks *w−1* and *w−2*, so a listing oscillating around the
   rank-60 boundary does not generate repeated "first" observations.
3. **≥8 panel periods** and a panel whose composition did not change during them
   (`panel_version` constant).
4. The result is labelled, in the claim and in the UI, as a relationship between a social
   series and a **collection-conditioned** commerce series, never as a market relationship.

Absent any of these, the stats row carries `gate_failures += 'no_frozen_panel'` and the UI
renders the `temporal_unavailable` template. Review dates are explicitly **rejected** as a
substitute commerce timeline (§7.7).

### 6.3 Permitted claim templates

`aypo/intelligence/noncausal.py` holds the closed set. A commerce finding may only be
persisted using one of these `claim_template_id`s, and `findings.claim_template_id` is
non-nullable for commerce findings.

**Every number in a claim is filled by code from a stored `MetricResult` or `Contingency`, and
a model may never supply one.** The validator in §6.4 reads words; it cannot tell a correct
`{lift}` from a fabricated one, so a design in which a model writes the sentence would leave
the numbers completely unguarded — the highest-value hallucination surface in the subsystem.
The division of labour is therefore:

- **Code** renders the template. `render_claim(template_id, metric_result)` pulls each
  placeholder from a typed source: `{share}`, `{ci_low}`, `{ci_high}`, `{n}` from the
  `MetricResult`; `{lift}`, `{n_both}`, `{n_left}`, `{wilson_low}`, `{base_rate}` from the
  `commerce_cooccurrence_stats` row; `{brand}`, `{theme}` resolved from ids. A missing or
  unresolvable placeholder raises; it is never left blank or guessed.
- **The model** may only (a) select a `template_id`, (b) name entity **ids** (never labels —
  ids are validated against the database and an unknown id is a rejection, which also removes
  the invented-entity surface), and (c) write the free-text `{statement}`,
  `{confirming_evidence}` and `{refuting_evidence}` of the `hypothesis` template, which carry
  no numbers and are fully exposed to the blocklist.
- **A post-render check**, `assert_numbers_match_source`, re-reads every numeric placeholder out
  of the rendered string and asserts equality with the source value to the rendered precision.
  It runs inside `assert_non_causal` and raises `NonCausalViolation(code='fabricated_number')`
  on any mismatch. This catches a retry loop that edits a number while fixing a phrasing
  violation.

```python
ALLOWED_CLAIM_TEMPLATES: Final[Mapping[str, str]] = {
 "share_within_sample":
  "Within the {n} single-brand product groups in this sample that matched {theme}, "
  "{brand} accounts for {share}% (95% CI {ci_low}–{ci_high}%).",

 "relative_cooccurrence":
  "In this sample, {theme} co-occurs with {brand} more often than {brand}'s overall share "
  "of the sampled assortment would suggest (lift {lift}, {n_both} of {n_left} matched "
  "groups, 95% lower bound {wilson_low} against a base rate of {base_rate}).",

 "parallel_presence":
  "{brand} appears in {n_social} analysed social items associated with {theme} and in "
  "{n_commerce} marketplace product groups matched to {theme}. These are two separately "
  "collected samples, reported side by side; AYPO is not relating one to the other.",

 "absence_bounded":
  "No marketplace listing in this sample matched {theme} above the evidence threshold "
  "({threshold}), across {n_screened} screened listings on {platforms}. If {p_ub}% or more "
  "of this sample's assortment expressed {theme}, at least one match would be expected 95% "
  "of the time.",

 "price_position":
  "{brand}'s median {basis_label} in this sample sits at the {pct}th percentile of the "
  "{n_ref} priced product groups in {category_scope}.",

 "variant_breadth":
  "{breadth} distinct variants of {brand} were observed in this sample, against "
  "{comparator_breadth} for {comparator_brand}, within {scope}.",

 "rating_distribution":
  "Across {n_ratings} platform ratings in this sample, {brand}'s share of ratings at 4 "
  "stars or above has a 95% lower bound of {lb}.",

 "label_overlap":
  "The label {label} appears in both the social sample ({n_social_items} items) and the "
  "marketplace review sample ({n_reviews} reviews across {n_listings} listings). The two "
  "samples have different and unknown selection mechanisms, so their frequencies are not "
  "compared.",

 "temporal_unavailable":
  "There is insufficient temporal coverage in this run to test any time relationship "
  "between {theme}'s social signal and its marketplace signal ({reason}).",

 "hypothesis":
  "HYPOTHESIS (not a finding), raised from this sample: {statement}. In this sample this would "
  "be supported by {confirming_evidence} and weakened by {refuting_evidence}. It has not been "
  "tested.",
}
```

The `hypothesis` wording carries "in this sample" explicitly: as originally written it had
neither a scope marker nor an `n`, so it failed the §6.4 scope check and
`test_allowed_claims_pass` — the one template whose body is model-written was the one template
that could never be persisted.

### 6.4 Forbidden templates and the validator

The guardrail is a validator, not a prompt instruction. Prompts can be drifted around;
`assert_non_causal` runs on every claim string before it is written to `findings`.

```python
class NonCausalViolation(ValueError):
    code: Literal[
        "causal_verb", "market_projection", "forecast", "unscoped",
        "sold_count_as_sales", "preference_claim", "missing_hedge", "unknown_template",
        "overclaim", "fabricated_number",
    ]
    matched: str

FORBIDDEN_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
 ("causal_verb", re.compile(r"\b(caus(e|ed|es|ing|al)|because of|due to|led to|leads to|"
                            r"result(ed|ing|s)? (in|from)|driv(e|es|en|ing)|fuel(l?ed|s|ling)?|"
                            r"spark(ed|s|ing)?|triggered|boost(ed|s|ing)?|lift(ed|ing)\b|"
                            r"translat(e|ed|es|ing) into|in response to|thanks to|"
                            r"as a result)\b", re.I)),
 # NOTE: the bare word "lift" is deliberately NOT matched — see the statistic-name exemption
 # below. Only "lifted"/"lifting" are causal in this corpus.
 ("market_projection", re.compile(r"\b(nationwide|market[- ]wide|category[- ]wide|across the "
                                  r"(country|market|category)|all (consumers|shoppers|buyers)|"
                                  rf"{MARKET_DEMONYM_PATTERN}|"
                                  r"the market (is|has|shows))\b", re.I)),
 ("forecast", re.compile(r"\b(will (be|become|grow|decline|increase|drop)|is set to|"
                         r"expect(ed)? to|forecast|project(ed|ion)|next (quarter|year)"
                         r"|going to)\b", re.I)),
 ("sold_count_as_sales", re.compile(r"\b(sales (of|for|rose|fell|grew)|revenue|units sold "
                                    r"(per|in|during)|sell[- ]through|demand for|"
                                    r"turnover)\b", re.I)),
 ("preference_claim", re.compile(r"\b(consumers? (prefer|favour|favor|choose|are choosing|"
                                 r"are switching|are buying)|preferred by|most popular|"
                                 r"endorse[sd]?|winning|dominat(e|es|ing))\b", re.I)),
 ("overclaim", re.compile(r"\b(prov(e|es|en|ing)|confirm(s|ed)|demonstrates that|"
                          r"clearly shows|definitely|undoubtedly)\b", re.I)),
)

# The population noun for the configured market is NOT hard-coded. An earlier draft had the
# literal "Filipinos" in the pattern above, which both bakes the first market into the guard
# and silently stops guarding the moment the objective moves to another market. It is built
# at import time from the run's market configuration:
#   MARKET_DEMONYM_PATTERN = "|".join(re.escape(w) for w in settings.market_population_nouns)
# with a non-empty default of generic population nouns ("consumers", "shoppers", "households",
# "the population"), and a startup assertion that the list is non-empty.

SCOPE_MARKERS: Final[tuple[str, ...]] = (
    "in this sample", "within this sample", "in the analysed sample",
    "in the collected sample", "of this sample", "in this run",
)

# Fixed caveat and narrative constants are NOT run through FORBIDDEN_PATTERNS. They exist to
# say what the data is not, so they necessarily contain the words the blocklist bans — the
# sold-counter caveat contains "revenue", which `sold_count_as_sales` matches. Running the
# blocklist over them would reject the very text that protects the reader. They are instead
# protected by `test_caveats_are_unchanged`, a snapshot test over
# `aypo/intelligence/commerce_caveats.py`, so every edit to a caveat is a reviewed diff.
CAVEAT_CONSTANTS_EXEMPT: Final[bool] = True

def assert_non_causal(
    claim: str, *, template_id: str, has_social_and_commerce: bool,
    metrics_used: frozenset[str],
) -> None:
    """Raise NonCausalViolation. Called by the findings writer; no commerce finding is
    persisted without passing."""
```

Five checks, all hard:

1. **Template membership** — `template_id in ALLOWED_CLAIM_TEMPLATES`, else
   `unknown_template`.
2. **Pattern blocklist** — any `FORBIDDEN_PATTERNS` hit raises with that code. The claim must
   be the code-rendered English string; verbatim quotes and non-English labels live in
   evidence fields, never inside the claim, because a blocklist of English regexes cannot
   guard text in another language.
3. **Scope check** — the claim must contain a `SCOPE_MARKERS` phrase **or** an explicit
   `n = …` / `{n} …` count. Else `unscoped`.
4. **Hedge check** — when `has_social_and_commerce` is true (the claim spans a social
   entity and a commerce metric), the claim must contain one of the mandated hedge clauses
   ("reported side by side", "AYPO is not relating one to the other", "two separately
   collected samples", "co-occurs"). Else `missing_hedge`.
5. **Number check** — `assert_numbers_match_source` (§6.3): every numeric token in the
   rendered claim must equal the stored source value at the rendered precision, else
   `fabricated_number`.

Failure handling depends on which check failed, because only some of a claim is model-written:

- A failure of 2/3/4 on a template whose body is fixed is a **bug in the template**, not a
  model error; it raises at import time via `test_allowed_claims_pass` and can never reach
  production. (This is how the `relative_cooccurrence` template, whose body contains the
  statistic name "lift", was caught: the `causal_verb` pattern matched the bare word, so every
  co-occurrence finding in the subsystem would have been rejected.)
- A failure on the model-written portion (the `hypothesis` body, or template/entity selection)
  → the generating call is retried **once** with the violation code and matched span injected
  into the prompt; second failure → the finding is written with
  `status='rejected_non_causal'`, the violation recorded, and it is **not** surfaced in any UI
  section.
- A `fabricated_number` failure is **never retried**: it means the rendering path disagreed
  with its source, which is a defect to be fixed, not a prompt to be re-rolled.

Rejections are visible in the Research Run view (brief §32) so the failure mode is auditable
rather than invisible.

Illustrative forbidden claims (each one a real temptation this data creates):

| Forbidden | Code | Why it is wrong here |
|---|---|---|
| "Theme X is driving sales of Brand A." | `causal_verb`, `sold_count_as_sales` | causal verb plus a sales figure that only exists as an undefined counter |
| "Brand A gained marketplace share because of its social momentum." | `causal_verb` | no share denominator exists; no mechanism observed |
| "Social interest in Theme X translated into marketplace demand." | `causal_verb`, `sold_count_as_sales` | both series are collection artefacts (§6.5) |
| "Consumers prefer Brand A in Theme X." | `preference_claim` | listings and ratings are seller and reviewer behaviour, not preference |
| "Brand A is winning Theme X nationwide." | `preference_claim`, `market_projection` | a 2,500-record convenience sample cannot speak for a market |
| "Expect Theme X listings to grow next quarter." | `forecast` | no temporal coverage at all (§6.2) |
| "Brand A's 12,000 units sold prove category leadership." | `sold_count_as_sales`, `overclaim` | the counter is unaudited, undated and non-additive |

### 6.5 Why temporal ordering in this data cannot establish causation

Six concrete reasons, all specific to this architecture rather than generic caution:

1. **Endogenous sampling.** Pass-2 marketplace queries are generated *from* the social
   theme. Any listing those queries return was selected because it resembles the theme, so
   "the theme appeared, then matching listings appeared" is a description of our pipeline's
   execution order. Pass 1 (§4.2) exists precisely to provide a denominator that is not
   generated this way, and it still cannot supply a timeline.
2. **Commerce timestamps are not event times.** `first_seen_at` is when AYPO collected the
   listing. Neither platform reliably publishes listing creation dates, so the commerce
   series is a function of the collector's schedule, not of the market's.
3. **The one commerce counter that moves cannot be differenced.** `sold_count` has
   undocumented window and reset semantics (§3.6), so a snapshot difference is an
   uninterpretable mixture of new orders, window roll-off and platform recalculation.
4. **Social publication dates are a ranking artefact.** Platform search surfaces recent and
   high-engagement content, so the social date distribution reflects what the platform chose
   to show the collector — the same sampling-bias problem the social subsystem carries, and
   it does not cancel when correlated with anything.
5. **No untreated comparison unit.** Every brand in the sample is simultaneously exposed to
   platform campaign calendars, payday cycles, seasonal and weather effects, and each
   brand's own paid media. None of these is observed in our data, all of them move social
   and commerce signals together, and with no unexposed unit there is no counterfactual to
   compare against.
6. **Resolution mismatch.** Even in a longitudinal deployment, social signal moves at daily
   resolution while commerce observation moves at the run cadence (weekly at best). A lag
   estimated across a one-observation-per-week commerce series cannot distinguish a 2-day
   from a 10-day relationship, which is the range where an actual mechanism would live.

The subsystem's position is therefore: co-occurrence within a sample is reportable with
gates and bounds; direction and cause are not reportable at all; and where a reader expects
a time relationship, the product says the test is unavailable and why.

---

## 7. Review-text mining

### 7.1 Ingestion and storage

Reviews arrive attached to a listing, as a provider-determined top-N page. The ordering is
usually the platform's own relevance ranking and is **not** documented, so the set is a
ranked subset, never a sample. That fact is recorded on every row and propagated into every
caveat.

```sql
create table commerce_reviews (
  id                   uuid primary key default gen_random_uuid(),
  item_id              uuid not null references commerce_items(id) on delete cascade,
  research_run_id      uuid not null references research_runs(id) on delete cascade,
  platform_review_id   text,
  rating               int check (rating is null or rating between 1 and 5),
  text_raw             text,
  text_normalised      text,
  text_sha256          bytea,
  duplicate_of         uuid references commerce_reviews(id) on delete set null,
  near_dup_cluster_id  uuid,
  languages            text[] not null default '{}',
  has_text             boolean not null default false,
  token_count          int,
  review_date          date,
  review_date_precision text,     -- 'day'|'month'|'relative_bucket'|'unknown'
  review_date_raw      text,
  variant_purchased    text,
  seller_reply         boolean,
  media_count          int,
  review_sort_hint     text,      -- provider-reported ordering, often null
  review_page_index    int,
  authenticity_flags   jsonb not null default '{}'::jsonb,
  collected_at         timestamptz not null default now(),
  raw_payload          jsonb,
  field_quality        jsonb not null default '{}'::jsonb
);
create unique index commerce_reviews_identity on commerce_reviews
  (item_id, coalesce(platform_review_id, encode(text_sha256, 'hex'), id::text));
-- the identity index above keys on the provider's review id FIRST, so it does NOT collapse
-- two rows with the same text and different provider ids. Exact-text dedup needs its own:
create unique index commerce_reviews_text_identity on commerce_reviews (item_id, text_sha256)
  where text_sha256 is not null;
create index commerce_reviews_item on commerce_reviews (item_id);
create index commerce_reviews_dupe on commerce_reviews (near_dup_cluster_id)
  where near_dup_cluster_id is not null;

create table commerce_review_labels (
  id              uuid primary key default gen_random_uuid(),
  review_id       uuid not null references commerce_reviews(id) on delete cascade,
  item_id         uuid not null references commerce_items(id) on delete cascade,
  label_type      text not null,         -- SAME vocabulary as social open labels (§7.3)
  label           text not null,         -- verbatim as the model proposed it
  label_slug      text not null,
  evidence_quote  text,                  -- verbatim span from the review
  language        text,
  gloss_en        text,                  -- short English gloss; NOT a translation of the review
  confidence      numeric(3,2) not null,
  model           text not null,
  prompt_version  text not null,
  created_at      timestamptz not null default now(),
  unique (review_id, label_type, label_slug)
);
create index commerce_review_labels_slug on commerce_review_labels (label_slug, item_id);
```

### 7.2 De-duplication, low-information filtering, templating signals

Order of operations, all deterministic:

1. **Exact duplicates** — same `(item_id, text_sha256)` collapse on
   `commerce_reviews_text_identity`. (The main identity index cannot do this: it keys on
   `platform_review_id` first, so a platform that issues a distinct id per copy of the same
   text would have produced duplicate rows that the design then counted as independent
   reviews.)
2. **Cross-listing near duplicates** — normalise (casefold, strip punctuation/emoji,
   collapse whitespace), then trigram similarity (`pg_trgm similarity`) `≥ 0.90` within a
   blocking bucket of `(first 12 normalised chars, token_count ±1)`. Members get a shared
   `near_dup_cluster_id`; the earliest-collected row is canonical and the others get
   `duplicate_of`. *Why 0.90:* sellers and campaigns copy review text across listings
   verbatim or with a single word changed; genuinely independent short reviews
   ("good product") also collide, which is why near-duplicates are **labelled, not
   deleted**, and only the canonical member enters label counting. *At the boundary* it is
   treated as a duplicate.
3. **Low-information** — `token_count < 4` after normalisation, or text consisting only of
   punctuation/emoji, or matching the platform's own placeholder for a rating without text
   → `has_text = false`. These rows are **kept** (they are valid ratings for §5.4) and
   excluded from label extraction.
4. **Templated / incentivised signals** — recorded in `authenticity_flags`, never acted on
   as a verdict:

```json
{
  "near_dup_cluster_size_across_listings": 7,
  "burst_48h_same_listing": 23,
  "mentions_review_incentive_tokens": ["<verbatim token>"],
  "identical_rating_and_length_run": true,
  "seller_reply_present": true
}
```

Gate for surfacing the flag: `near_dup_cluster_size_across_listings ≥ 5` **or**
`burst_48h_same_listing ≥ 20` **computed only over day-precise dates**
(`review_date_precision = 'day'`), and only when ≥90% of the listing's collected reviews are
day-precise. A 48-hour window cannot be evaluated against month-precision or
relative-bucket dates; where it cannot be evaluated the signal is recorded as
`burst_48h_same_listing = null` (unknown), never as absent. *Why 5 clusters:* four or fewer identical short reviews
across listings is well within the collision rate of genuine short praise; five across
*distinct* listings is a pattern. *Why 20 in 48h:* on a listing whose total collected review
count is capped at 20, a 48-hour burst filling the whole page is the detectable shape; the
threshold is deliberately tied to the collection cap and must be re-derived if the cap
changes. The surfaced wording is fixed:

> "This listing's reviews show signals of templated or incentivised reviewing
> ({signals}). AYPO does not determine whether individual reviews are genuine."

### 7.3 Open labels — the same system as social content

Review text goes through the **same open-label contract** as social analysis (brief §19B),
not a commerce-specific taxonomy. Concretely:

- The same `label_type` vocabulary is used: `occasion`, `behaviour`, `social_context`,
  `cultural_signal`, `slang`, `humour_signal`, `product_pairing`, `audience_signal`,
  `commercial_signal`, `unexpected_observation`, plus the review-only additions
  `product_attribute` and `purchase_context`. The list is a *starting set of types*, not a
  list of values — values are always generated from the text (brief §19, §34).
- Labels are stored verbatim with a slug, exactly as social labels are, so the
  frequently-recurring-label promotion pipeline (brief §19: discovered labels may later
  become normalised taxonomy entries) sees social and review labels in one vocabulary.
- Shared label slugs are what permit the `label_overlap` claim template (§6.3) — the only
  legitimate social↔commerce text join, and explicitly a co-presence statement rather than
  a magnitude comparison.

**Prompt skeleton** (`aypo/llm/prompts/commerce_review_labels.py`), `claude-opus-5`,
`effort="low"`, batched 40 reviews per call, structured output:

```text
SYSTEM
You read marketplace product reviews and propose open-ended labels describing what the
reviewer is actually talking about. There is no fixed list of labels — you generate them
from the text.

Rules:
1. Propose a label only when the review text supports it. Quote the exact span you used.
2. Do not infer who the reviewer is, their age, gender, income, household or location.
3. Do not infer purchase volume, sales, or popularity from a review.
4. Keep the label in the reviewer's own words where they used a distinctive term,
   including non-English and mixed-language terms. Do not translate the label; add a short
   English gloss in a separate field.
5. If a review carries no usable content ("ok", "nice"), return no labels for it.
   Returning nothing is correct and expected for many reviews.
6. Allowed label_type values: {LABEL_TYPES}. If nothing fits, return no label rather than
   forcing one.

USER
Label each review. One record per review_ref.
Example shape only: review_ref R1 → [{"label_type":"product_attribute","label":"Term T",
  "evidence_quote":"...Term T...","confidence":0.8,"gloss_en":"..."}]

REVIEWS
{review_ref}\t{rating or ''}\t{text_raw}
... x40
```

Rule 2 implements brief §28's "avoid invented demographics" at the review layer, where the
temptation is strongest.

### 7.4 Language and code-switching

Marketplace reviews are frequently multilingual and code-switched within a single sentence.
AYPO does not assume *which* languages a market's reviewers mix — that is itself a finding the
system should be able to discover, and an earlier draft asserted a specific language pair for
the first market, which is the kind of market assumption brief §1 rules out of the design.
`languages[]` comes from per-review detection over the normalised text, stored as a list
because single-label detection is wrong for code-switched text, and the observed language
distribution is reported as data. **Text is never translated
before labelling** — translation flattens exactly the slang and distinctive phrasing that
brief §18/§19 want preserved. The model receives the original and emits
`label` (original wording) plus `gloss_en` (a short English gloss, stored separately and
never substituted for the label in the vocabulary).

### 7.5 Review labels feeding the match score

```python
def build_review_label_document(review_labels: Sequence[CommerceReviewLabel]) -> str:
    return "; ".join(f"{l.label_type}: {l.label}" for l in review_labels)
```

Embedded once per listing (concatenating its canonical reviews' labels, capped at 60
labels) and used for `s_rev` in §4.4 — one document and one cosine per listing, not a maximum
over reviews. Where a listing has no canonical labelled review the document does not exist and
`s_rev` is `NULL` (component unavailable), which §4.4 handles by renormalising the weights. A theme match carried mainly by `s_rev` is a
different kind of evidence from one carried by the product itself — that the *buyers* talk
about the theme, not that the product is built for it — and the `matched_on` array plus the
component breakdown in the UI makes that distinction visible rather than collapsing it.

### 7.6 Volume and cost control

`max_reviews_per_item = 20`, `max_reviews_labelled_per_run = 3000`. Selection when the
run exceeds the cap, in this order: (1) canonical rows only (no `duplicate_of`),
(2) `has_text = true`, (3) stratified by product group so no single popular group consumes
the budget — round-robin across groups, up to 6 per group per pass, until the cap is
reached. Round-robin rather than top-N by length, because length-ranking would
systematically over-sample whichever listings attract verbose reviewers. **The round-robin
order is deterministic and recorded**: groups are visited in ascending
`sha256(run_id || group_id)` order, so a re-run labels the same reviews and §5.5's denominator
is reproducible; `commerce_coverage.groups_label_covered` records how many groups the budget
actually reached, and §5.5 reports it, because with 3,000 labels and a round-robin cap of 6 the
budget covers at most 500 groups and which 500 must not be an accident of dict ordering.

Unlabelled reviews still count in §5.4 rating statistics and are visible in the Commerce
Explorer; §5.5 reports `n_reviews` as the labelled subset and states it.

### 7.7 Review dates — the same sampling-bias discipline as publication dates

Review dates look like a timeline and are not one. Three independent problems: the
platform returns a *ranked* subset, so the date distribution of collected reviews is a
ranking artefact; review counts scale with listing age, which we do not know; and dates are
often month-precision or relative strings resolved against our collection time.

**Permitted uses:**

1. **Observation bounds** — `min(review_date)` / `max(review_date)` per listing or scope,
   reported as "reviews collected span {from}–{to}", describing the collected set.
2. **Presence within a window** — "label `L` appears in reviews dated within {window}",
   as a co-presence statement with its own n.
3. **Ordinal recency** — whether a listing's collected reviews are recent or old relative
   to other listings in the same scope, used for sorting and filtering in the UI only,
   never in a claim.
4. **Date-precision reporting** — the share of reviews whose date is day-precise, shown
   whenever any date-based filter is applied.

**Forbidden uses:**

1. A review-volume time series presented as a demand, interest or trend curve.
2. Any growth, decline, momentum or acceleration claim from review dates (`forecast` and
   `causal_verb` patterns in §6.4 catch the usual phrasings).
3. Treating a review date as a purchase date.
4. Comparing review-date distributions across listings of different or unknown age.
5. Substituting review dates for the missing commerce timeline in §6.2's lead-lag test —
   explicitly rejected: the resulting series would measure the platform's review ranking,
   not marketplace activity, and would manufacture exactly the spurious social→commerce
   correlation this subsystem is designed to refuse.

**The one window-based statistic permitted,** `label_share_in_window`, with hard gates:
`≥ 50` labelled reviews in the window **and** `≥ 10` distinct listings contributing
**and** `≥ 70%` of those reviews day-precise. *Why 50 and 10:* 50 reviews bound a label share's Wilson half-width at roughly ±0.134 at
`p̂ = 0.5`, which is the loosest interval still worth rendering; 10 distinct listings prevents
one listing's review page from defining the window. That Wilson figure assumes 50 independent
reviews and they are not independent — they arrive in blocks of up to 20 per listing — so the
interval actually reported is a **cluster bootstrap over listings** (2,000 resamples of the
contributing listings), which is why the `≥10 distinct listings` gate is a gate on *clusters*
and not a nicety. The analytic figure is shown as `half_width_naive` beside it. *Why 70% day-precise:* with more than a third of dates imputed from relative
strings or month precision, window membership itself becomes the dominant uncertainty.
Failing any gate suppresses the statistic with the reason stated. The rendered caveat is
fixed:

> "Share of the reviews AYPO collected in {window} that carry {label} ({n} reviews,
> {n_listings} listings). Marketplaces return a ranked subset of reviews rather than a
> sample, and review dates are not purchase dates, so this is a statement about collected
> review text in that window and not about buyers, purchases, or any change over time."

---

## 8. Python interfaces, job pipeline, and degradation

### 8.1 Owned modules

```
apps/api/aypo/
  models/commerce.py                     # Pydantic models + Resolved[T], enums mirror
  providers/marketplace.py               # MarketplaceProvider protocol, ProviderHealth
  providers/apify_marketplace.py         # ApifyProvider-backed marketplace provider
  providers/serper_commerce.py           # degraded web-search marketplace provider
  collectors/commerce_base.py            # shared normalise(), provenance, quarantine
  collectors/shopee.py                   # ShopeeCollector
  collectors/lazada.py                   # LazadaCollector
  analysis/commerce_price.py             # §1.4, §1.5
  analysis/commerce_noise.py             # §2.2 lexicon + df stats
  analysis/commerce_titles.py            # §2.3–§2.6
  analysis/commerce_identity.py          # §3.2–§3.5
  analysis/commerce_reviews.py           # §7.1–§7.5
  intelligence/theme_commerce.py         # §4
  intelligence/commerce_metrics.py       # §5 + sold_count_guard
  intelligence/commerce_caveats.py       # §5.0 caveat template registry
  intelligence/commerce_cooccurrence.py  # §6.1–§6.2
  intelligence/noncausal.py              # §6.3–§6.4
  llm/prompts/commerce_title_extraction.py
  llm/prompts/commerce_query_generation.py
  llm/prompts/commerce_review_labels.py
  jobs/commerce_collect.py
  jobs/commerce_normalise.py
  jobs/commerce_group.py
  jobs/commerce_theme_link.py
  jobs/commerce_reviews_label.py
  api/commerce.py                        # Commerce Explorer + evidence drill-down endpoints
supabase/migrations/0005_commerce.sql    # all DDL in this document
```

### 8.2 Provider and collector interfaces

```python
# aypo/providers/marketplace.py
class ProviderHealth(BaseModel):
    available: bool
    reason: str | None
    checked_at: datetime

class RawCommerceRecord(BaseModel):
    platform: CommercePlatform
    platform_item_id: str
    source_url: str
    payload: dict[str, Any]
    observed_at: datetime
    provider: str

class ProviderPage(BaseModel):
    records: list[RawCommerceRecord]
    next_cursor: str | None
    returned: int
    provider_warnings: list[str]

class MarketplaceProvider(Protocol):
    name: str
    platform: CommercePlatform

    async def health(self) -> ProviderHealth: ...
    async def search(
        self, query: str, *, limit: int = 60, cursor: str | None = None,
    ) -> ProviderPage: ...
    async def fetch_item(self, platform_item_id: str) -> RawCommerceRecord | None: ...
    async def fetch_reviews(
        self, platform_item_id: str, *, limit: int = 20,
    ) -> list[RawCommerceReview]: ...

# aypo/collectors/commerce_base.py
class NormalisationIssue(BaseModel):
    field: str
    status: FieldStatus
    detail: str | None

class NormalisationResult(BaseModel):
    item: NormalisedCommerceItem | None     # None only when quarantined
    quarantine_reason: str | None
    issues: list[NormalisationIssue]

class MarketplaceCollector(abc.ABC):
    platform: ClassVar[CommercePlatform]
    normalisation_version: ClassVar[int]

    def __init__(self, provider: MarketplaceProvider) -> None: ...

    async def search(self, query: str, filters: CommerceFilters | None = None
                     ) -> list[RawCommerceRecord]: ...
    async def fetch(self, record: RawCommerceRecord) -> RawCommerceRecord: ...
    def normalize(self, raw: RawCommerceRecord, *, run_id: UUID,
                  query_ctx: QueryContext) -> NormalisationResult: ...
```

`normalize` is synchronous, pure, and **never raises** for anything other than the six
required fields — a property covered by a property-based test that feeds it randomly
field-deleted and type-mutated payloads (§10.2).

### 8.3 Job pipeline (Postgres-backed queue, no Redis, no Celery)

Jobs are rows in the `collection_jobs` table owned by the jobs subsystem, claimed with
`select ... for update skip locked`. Every stage here is **idempotent** keyed by
`(research_run_id, stage, input_hash)`, because the queue is at-least-once.

| Stage | Input | Output | Idempotency key |
|---|---|---|---|
| `commerce.collect` | query | `commerce_items` raw rows | `(run, platform, query_text, cursor)` |
| `commerce.normalise` | item ids | normalised fields, `field_quality` | `(item_id, normalisation_version)` |
| `commerce.titles` | item ids (batch 25) | `commerce_item_brand_links`, composition | `(item_id, extraction_version)` |
| `commerce.embed` | item ids | `commerce_item_embeddings` | `(item_id, kind, model)` |
| `commerce.group` | run | `commerce_product_groups` | `(run, method_version)` |
| `commerce.reviews.collect` | item ids | `commerce_reviews` | `(item_id, page_index)` |
| `commerce.reviews.label` | review ids (batch 40) | `commerce_review_labels` | `(review_id, prompt_version)` |
| `commerce.theme.screen` | (run, theme) | pass-1 `commerce_theme_matches` | `(theme_id, method_version)` |
| `commerce.theme.queries` | (run, theme) | `commerce_theme_queries` | `(run, theme_id, platform)` |
| `commerce.theme.link` | (run, theme) | pass-2 matches, `commerce_theme_absences` | `(theme_id, method_version)` |
| `commerce.metrics` | run | `MetricResult` payloads, `commerce_price_reference` | `(run, method_version)` |
| `commerce.cooccurrence` | run | `commerce_cooccurrence_stats` | `(run, method_version)` |

`commerce.group` must complete before `commerce.metrics`; `commerce.theme.screen` requires
`commerce.embed`; everything else is independently parallelisable. A failed stage marks the
affected items with a `quality_flags` entry and the run continues (brief §7).

### 8.4 Degradation matrix

`APIFY_TOKEN` is currently absent. The subsystem must run anyway.

| Condition | Behaviour | What is suppressed | What still works |
|---|---|---|---|
| `APIFY_TOKEN` missing, `SERPER_API_KEY` present | `apify_marketplace` health `available=false` with reason; fall back to `serper_commerce`; `commerce_coverage.available=true, degraded=true` | all §5 metrics on provider-only fields (price position, rating, sold, variant breadth), all §6 statistics | existence-of-listing evidence; theme→commerce absence reported as **`inconclusive_low_coverage`**, never `absent_with_coverage` (a web-snippet corpus cannot support a prevalence bound) and never `not_investigated` (we did look) |
| `APIFY_TOKEN` **and** `SERPER_API_KEY` both missing | no marketplace provider; `commerce_coverage.available=false` with reason | all of §1–§7 for this run | the run proceeds; every theme gets `state='not_investigated'` with the reason, and the Research Run view shows commerce as unavailable rather than empty. This is the terminal degradation state and it must not raise. |
| `serper_commerce` fallback active | rows written with `collection_mode='web_search_snippet'`, `is_degraded=true`, `quality_flags += 'degraded_source'` | price position, rating distribution, sold counters, review mining, co-occurrence, **and any prevalence bound** | existence of listings for a query, brand/product extraction from the snippet title, group formation **flagged `degraded_grouping` and excluded from group-count metrics** (rather than carrying a `confidence ×0.6` fudge: multiplying a confidence by a constant silently reinteracts with every threshold that reads it — §2.6's 0.70, §5.3's 0.60 — so degraded rows are excluded explicitly instead), presence counts with the degraded caveat |
| one platform's actor fails mid-run | that platform's `commerce_coverage` records `collection_ended_at` and the error; items already collected are kept | per-platform metrics for that platform; cross-platform pooled group counts gain a coverage note | the other platform's full metric set |
| reviews unavailable | `s_rev = 0.0` everywhere | §5.4 (if ratings also absent), §5.5, §7.7 | §5.1–§5.3, §4 matching on product text alone |
| embeddings provider fails | retry with backoff; the Jina fallback is usable **only if the theme vectors can be re-embedded with the same fallback model in the same run** (12 themes, trivial cost) and the whole commerce corpus re-embedded too; otherwise the stage blocks and `commerce.theme.*` is skipped | matching across models is refused rather than approximated — a mixed-model run yields `gate_failures=['embedding_model_mismatch']` and no matches | lexical-only screening is **not** substituted (§4.4's semantic gate forbids it). Note a fallback that leaves social and commerce on different models makes §4 unusable, so "fall back to Jina" is a whole-run decision taken before embedding starts, not a per-call retry |
| theme registry empty (clustering not yet run) | `commerce.theme.*` stages skip | all of §4, §6 | §1–§3, §5.2–§5.4 at category level |

Degraded rows carry their own mandated caveat:

> "This record was reconstructed from a public web search result rather than collected from
> the marketplace, so its price, rating, review and sold fields are absent or unverified.
> It is used only to record that a listing matching the query exists."

---

## 9. Cost at scale

Assumptions: 2,500 commerce records per run, 12 themes investigated, ~20,000 reviews
available of which 3,000 are labelled, 150 groups vision-corroborated. Anthropic
`claude-opus-5` at $5 / $25 per Mtok input/output. (The 1,500-video social pipeline is
costed in its own design.)

| Stage | Calls | Input tok | Output tok | Cost |
|---|---|---|---|---|
| Title extraction (§2.3) — 25/call, `effort=low` | 100 | ~190k | ~300k | **$8.45** |
| Theme query generation (§4.3) — 1/theme, `effort=medium` | 12 | ~18k | ~5k | **$0.22** |
| Group adjudication (§3.4) — 10 pairs/call, `effort=low` | 30 | ~63k | ~18k | **$0.77** |
| Review labelling (§7.3) — 40/call, `effort=low` | 75 | ~240k | ~180k | **$5.70** |
| Vision corroboration (§2.5) — 1 image/group, optional | 150 | ~270k | ~23k | **$1.92** |
| Match adjudication (grey-band spot checks, optional) | 20 | ~42k | ~12k | **$0.51** |
| **Anthropic subtotal** | 387 | ~823k | ~538k | **~$17.6** |

Embeddings — OpenAI `text-embedding-3-large`, `dimensions=1536`:

| Stage | Tokens | Cost at $0.13/Mtok (list price, **verify at build time**) |
|---|---|---|
| 2,500 listing match documents (~80 tok each) | 200k | $0.026 |
| 2,500 denoised titles (for §3.3) | 100k | $0.013 |
| 3,000 review label documents | 120k | $0.016 |
| ~150 generated queries + 12 theme documents | 10k | $0.001 |
| **Embeddings subtotal** | ~430k | **~$0.06** |

Serper web search (degraded path only): ~150 queries per run, well inside typical plan
quotas; cost is plan-based, not per-run marginal.

Two stages added by this revision: §4.6's top-K absence adjudication (12 themes × 20 groups,
batched 10/call = 24 calls, ~50k in / ~15k out ≈ **$0.63**) and §3.3's cohesion-guard rescoring
(no model calls; embeddings already stored). Revised Anthropic subtotal ≈ **$18.2**, total
≈ **$18.3**.

**Total marginal AI cost per commerce run: ≈ $18.3**, of which ~77% is title extraction plus
review labelling. The two levers if that needs to come down: drop vision corroboration
(−$1.9, costs one brand signal — but note §2.5 now spends it where attribution is actually
unresolved, so it buys more than it did), and raise the title batch from 25 to 50. The batch
lever is smaller than an earlier draft claimed: the per-call overhead is the ~700-token system
prompt × 100 calls ≈ 70k tokens ≈ $0.35, so halving the call count saves ≈ **$0.18**, not $0.5
— against a measurable increase in `item_ref` mismatches and offset errors on long batches,
which §2.3.1 now detects rather than absorbs. Batch 25 stays the conservative default, and the
batch size is config, not a constant.

Prompt caching is worth enabling on the static prompt prefixes if those prefixes exceed the
provider's minimum cacheable prefix length; the title-extraction system prompt is ~700
tokens, which may fall below it, so caching is an optimisation to measure rather than to
assume. Non-time-critical stages (title extraction, review labelling) are also candidates
for the Batch API if a ~50% discount is available for them; neither is on the interactive
path.

Storage: ~8,000 stored vectors (2,500 items × 2 kinds, plus ~3,000 review-label documents)
at 1536-dim `float4` ≈ 49 MB plus HNSW overhead — trivial for Supabase Postgres. Review
text at 20,000 rows × ~200 bytes ≈ 4 MB.

---

## 10. Contracts, tests, and open questions

### 10.1 Cross-subsystem requirements (phrased as contracts)

1. **Theme registry (clustering/trend subsystem)** must expose `ThemeRef` exactly as in
   §4.1, including `discriminative_terms` with weights, ≥3 verbatim
   `representative_snippets`, and `centroid` with its `embedding_model` recorded. Labels
   must be generated after cluster inspection (brief §21); this subsystem will not accept a
   theme whose label was supplied by a human seed list.
2. **Embedding service** must expose one `embed_documents(texts, *, kind) -> list[Vector]`
   that records `model` and `dim` on every stored vector, and must use the **same** model
   for social content, themes, listings and reviews. Mixed models must raise, not silently
   produce incomparable cosines.
3. **Brand entity service** must expose
   `upsert_brand_by_surface(surface: str) -> tuple[UUID, float]` that creates brands on
   first observation with no seed list, and must never merge two surfaces without storing
   the merge evidence.
4. **Research engine** must own `research_runs` / `research_queries`, accept generated
   marketplace query objects carrying `rationale` + `theme_id` + `query_kind`, and expose
   the query→item provenance join so `sampling_strategy` and `theme_id_origin` can be
   populated. It must also record the commerce half of the stopping conditions (brief §26).
5. **Findings subsystem** must make `claim_template_id` and `caveat` non-nullable for
   commerce findings, must call `assert_non_causal` before persisting, and must store
   `status='rejected_non_causal'` rows without surfacing them.
6. **Jobs subsystem** must provide a Postgres-backed at-least-once queue with
   `for update skip locked` claiming and support the idempotency keys in §8.3.
7. **Open-label vocabulary** must be shared between social analysis and review labelling,
   with one promotion pipeline for recurring labels (brief §19).
8. **Social analysis** must expose, per theme, the count of analysed social items and the
   publication-date coverage statistics, so §6.2's gate can be evaluated and the
   `parallel_presence` template can be filled.
9. **UI** must honour the `MetricResult` contract: `value`, `n`, `n_label`, `excluded` and
   `caveat` render together; `listing_count` never renders without `distinct_group_count`;
   sold counters never render without `sold_semantics` and `metrics_as_of`.
10. **Storage** must accept product images and return an S3 key; this subsystem stores only
    the key and the original URL.

### 10.2 Acceptance tests that encode the guardrails

| Test | Asserts |
|---|---|
| `test_noise_lexicon_tags_are_closed` | the seven `NoiseReason` tags, every entry tagged, ≤120 entries, ≤3 tokens each |
| `test_prompt_static_text_is_allowlisted` | every content word in the static text of every prompt file is either in a reviewed allowlist of structural/operational vocabulary or is a `{placeholder}`. This is the implementable form of "no seeded vocabulary": a denylist of flavours and occasions cannot be written without writing one, whereas an allowlist fails closed on any new content word and forces the author to justify it in review. It is what catches a literal category token such as the one §2.3 used to hard-code |
| `test_fixtures_use_placeholders_only` | fixtures and worked examples use only `Brand A..D`, `Product P/Q`, `Theme X`, `Term T`, `Cluster Y` |
| `test_normalize_never_raises` | property-based: random field deletion and type mutation over sample payloads; either a valid item or a quarantine row, never an exception |
| `test_price_scale_is_cohort_resolved` | a cohort of single-field numeric payloads cannot be resolved per row (every candidate value admits ≥2 divisors), and `resolve_cohort_scale` either returns one divisor with its margin or marks the **whole cohort** ambiguous with `price_scale_unresolved` surfaced on the run |
| `test_price_ranges_excluded_from_price_stats` | `price_is_range` rows never enter `commerce_price_reference` or §5.2 |
| `test_primary_requires_two_sources` | a single corroboration source at any confidence, including `title_llm` at `model_confidence = 1.0`, cannot produce `attribution='primary'` |
| `test_brand_span_offsets_are_recomputed` | a model-returned `char_start/char_end` that does not slice to `brand_surface` is corrected or dropped, never persisted |
| `test_grouping_is_order_invariant` | shuffling input item order yields identical `group_key` assignments |
| `test_cohesion_guard_blocks_chaining` | `a≈b≈c` with `sim(a,c) = 0.60` does not produce one group |
| `test_bundle_never_counted_as_single_brand` | a `multi_brand_bundle` group contributes to no brand share numerator or denominator |
| `test_group_brand_requires_supermajority` | members disagreeing on the primary brand yield `brand_id = NULL` and `unattributed_groups` |
| `test_metric_result_requires_caveat` | `MetricResult` with empty `caveat` fails validation |
| `test_sold_count_guard` | every forbidden aggregation in §3.6 raises `SoldCountMisuse`, and `SoldCounter.__add__` / `__mul__` / `__int__` raise |
| `test_no_arithmetic_on_sold_columns` | static scan: no arithmetic operator adjacent to a `sold_count` identifier outside `commerce_metrics.py` |
| `test_forbidden_claims_rejected` | each illustrative claim in §6.4 raises with the expected code |
| `test_allowed_claims_pass` | every template in `ALLOWED_CLAIM_TEMPLATES`, rendered with placeholder values, passes `assert_non_causal` — including `relative_cooccurrence`, whose body contains the statistic name "lift", and `hypothesis`, whose body must carry its own scope marker |
| `test_claim_numbers_match_source` | mutating a number in a rendered claim raises `fabricated_number` |
| `test_caveats_are_unchanged` | snapshot over `commerce_caveats.py`; caveats are exempt from the blocklist and their text is therefore review-gated instead |
| `test_lead_lag_suppressed_single_run` | a single-run dataset yields `None` with `insufficient_temporal_coverage` |
| `test_lead_lag_requires_frozen_panel` | 8+ buckets assembled from theme-generated queries still refuse, with `no_frozen_panel` |
| `test_cooccurrence_is_orientation_invariant` | swapping left/right yields identical `gate_passed` and `lift` |
| `test_cooccurrence_fdr_applied` | a family of 300 cells reports `fdr_significant` for strictly fewer than the Wilson-gate-passing count |
| `test_absence_requires_coverage` | 149 screened **groups** → `inconclusive_low_coverage`; 150 → `absent_with_coverage` with `p_ub ≈ 0.0198` at `ρ̂ = 0` |
| `test_absence_is_reachable` | a theme whose terms appear nowhere, with no review overlap and 20 negative top-K adjudications, reaches `absent_with_coverage` **despite** `s_emb_cal = 1.0` on its nearest listing — the regression test for the calibration bug that made this state unreachable |
| `test_semantic_gate` | `s_lex = 1.0, s_rev = 1.0, s_emb_cal = 0.0` cannot reach `supported` |
| `test_supported_requires_two_material_components` | one material component cannot reach `supported` under any weight renormalisation, including when `s_rev` is `NULL` |
| `test_s_rev_absence_does_not_penalise` | two listings identical but for review presence receive the same `match_score` |
| `test_pass2_items_excluded_from_shares` | a pass-2-only matched group appears as evidence and in no §5.1 numerator or denominator |
| `test_degraded_rows_excluded_from_price_metrics` | `is_degraded` rows never enter `commerce_price_reference` |
| `test_terminal_degradation_does_not_raise` | neither `APIFY_TOKEN` nor `SERPER_API_KEY` present → run completes, all themes `not_investigated` |

### 10.3 Open questions (genuine, not placeholders)

- **OQ-1 — `sold_count` semantics.** Unresolvable without `APIFY_TOKEN` and real payloads.
  Until a sample is inspected, `sold_semantics='unknown'` is the only honest value and the
  §3.6 forbidden-operations list is the mitigation. Resolution: inspect ≥50 payloads per
  platform, compare the integer field against the rendered string, and if a definition can
  be established, record it per `(platform, collection_provider)` in config — never in code.
- **OQ-2 — match threshold calibration.** `0.60 / 0.45` are reasoned from the weight
  structure (no single component can reach `supported`), not measured. The §4.4 labelling
  procedure must run on the first real corpus. Until it does, the UI says "evidence score",
  not "confidence", and `method_version` distinguishes pre- from post-calibration matches.
- **OQ-3 — per-star rating histograms.** Whether either provider exposes the 1–5 breakdown
  is unknown. If not, §5.4 degrades to mean + n + Wilson bound on an unobservable `p4plus`,
  which would have to be dropped — leaving only mean and n, a materially weaker metric.
- **OQ-4 — review volume and ordering.** Whether 20 reviews per listing is achievable, and
  whether any provider reports the sort order, is unknown. If the ordering is always
  unknown, §7.7's gates stand as specified; if a `most_recent` ordering is available, a
  genuinely chronological (though still ranked-subset) window analysis becomes possible and
  §7.7 should be revisited.
- **OQ-5 — Shopee price scale.** The `infer_price_scale` hedge works, but a confirmed
  per-actor constant would eliminate the `ambiguous` class entirely. Needs one real dataset.
- **OQ-6 — grouping thresholds.** `0.88 / 0.78` and the ±7.5% weight tolerance are
  reasoned from failure asymmetry (false merges are worse than false splits) and from
  expected reporting noise, not from measured distributions. Re-estimate from the first
  run's observed intra-product weight and title-similarity distributions.
- **OQ-7 — review-text retention.** Storing review text verbatim at volume is a
  legal/ToS/product decision, not a technical one. The schema supports storing hashes and
  labels without `text_raw` if that decision goes the other way; `evidence_quote` in
  `commerce_review_labels` would then have to be truncated or dropped, which weakens
  evidence drill-down (brief §27).
- **OQ-8 — cross-platform pooling.** Whether the headline presence figure should dedupe a
  product that appears on both platforms, or report per platform only. Design leans to
  per-platform with a pooled group count beside it; this is a product decision about what
  "marketplace presence" should mean to a marketer.
- **OQ-9 — second within-run snapshot.** Collecting a second price/sold snapshot late in a
  run would double commerce collection cost and still yield exactly one difference, which
  §3.6 forbids interpreting. Recommendation: no for MVP; revisit only when the longitudinal
  path in §6.2 is actually being used.
- **OQ-10 — `category_scope` stability.** Platform category paths are seller-assigned and
  inconsistent, so the §5.2 reference distribution may be unstable across runs. If observed
  instability is high, the fallback (the run's category-level query cluster) should become
  the primary scope, which makes the price distribution a property of our sampling rather
  than of the platform's taxonomy — a trade-off worth measuring before choosing.
- **OQ-11 — source correlation in §2.6.** `RHO = 0.6` encodes a guess about how much a second
  seller-written field adds once the title has been read. It is the parameter that decides how
  many listings get an attributed brand at all, and therefore how large §5.1's denominator is.
  Estimate it from the first corpus by measuring the agreement rate between `provider_brand_field`
  and the title read on listings where vision independently confirms the brand; `RHO` is then
  `1 − (conditional information of the second source)`, which is measurable rather than asserted.
- **OQ-12 — is the absence top-K adjudication (§4.6 gate 3) sufficient at `K = 20`?** The gate
  asserts that none of a theme's 20 nearest listings expresses it. If real expressions routinely
  sit outside the embedding's top 20 — plausible when a theme is behavioural and the product
  vocabulary is oblique — the absence state will be wrong in the dangerous direction (claiming
  absence where there is presence). Measure recall@20 during the OQ-2 labelling exercise; if it
  is below ~0.9, `K` rises or the gate also requires a lexical sweep over a broader candidate
  set.
- **OQ-13 — intra-query correlation `ρ̂` for the prevalence bound.** The design effect in §4.6
  defaults to a conservative `ρ̂ = 0.3` when it cannot be estimated. That default is a guess and
  it directly scales the headline `p_ub`. Estimate it from the first run as the intra-cluster
  correlation of the match indicator across queries, and report the sensitivity of `p_ub` to it
  rather than hiding the dependence.
- **OQ-14 — does the §3.3 cohesion guard over-split?** Average-linkage-with-a-floor fixes
  chaining but may fragment legitimate groups whose titles vary a lot across sellers. Measure
  the group-size distribution and manually audit the 20 largest components that the guard split,
  on the first real corpus. The asymmetry argument (a split costs a duplicate group, a chain
  corrupts a price median) says to start strict, but it is not a reason never to look.

---

## 11. Review notes

Adversarial review of the preceding design, 2026-10-02. The document's architecture survived:
the group-level presence model (§3), the sold-counter discipline (§3.6), the
normalisation-cannot-fail contract (§1.3), the string-price grammar (§1.4), the closed noise
lexicon with its seven-tag enum (§2.2), the metric-with-mandatory-caveat contract (§5.0), the
six reasons temporal ordering cannot establish causation (§6.5) and the review-date discipline
(§7.7) were checked and left substantially intact. The Anthropic API shapes
(`messages.parse(output_format=…)` → `parsed_output`, adaptive thinking, `output_config.effort`)
and the $5/$25 per-Mtok Opus-5 rates in §9 were verified against `aypo/llm/client.py` and
`tests/test_pricing.py` and are correct, as is the `text-embedding-3-large` list price and the
49 MB vector-storage estimate. The Wilson figures quoted at `n = 10`, `n = 20` and `n = 50`, and
the prevalence bounds at `n = 50` and `n = 150`, were recomputed and are right.

What changed, and why.

**Four defects were fatal — each one silently disabled a headline capability.**

1. **§1.4, price scale.** The per-row divisor test could not work. Sibling ordering constraints
   are invariant under division by a common divisor, so they contributed no discriminating
   power at all, leaving a plausibility band six orders of magnitude wide against divisors
   spanning five: `raw = 4990` admits `d ∈ {1, 100, 1000}` and `raw = 499000` admits all four.
   Nearly every payload would have returned `ambiguous`, killing §5.2, unit price, discount and
   the §2.7 flag. (The worked boundary example was also wrong by a factor of 100 in both
   directions and concluded the opposite of the truth.) Replaced with cohort-level resolution
   using string-price anchors, an in-band/integral-centavo/dispersion objective, an acceptance
   margin, and a **visible run-level refusal** when a cohort cannot be resolved.
2. **§4.4 + §4.6, relative calibration vs absolute thresholds.** `s_emb_cal` is calibrated
   against the run's own corpus, so the top-scoring listing clips to 1.0 for *every* theme —
   including one with no commerce expression. `max_match_score ≥ 0.50` was therefore
   unconditional, and the `absent_with_coverage` gate (`max_match_score < 0.45`) was
   mathematically unreachable: the subsystem's flagship negative result could never fire, and
   every theme would have produced "matches" regardless of reality. `s_emb_cal` is now
   explicitly a ranking measure; absence rests on quantities that can be zero (`s_lex`,
   review-label overlap) plus a bounded top-K adjudication; `test_absence_is_reachable` is the
   regression.
3. **§6.3/§6.4, the validator rejects its own template.** The `causal_verb` pattern matches the
   bare word `lift`, which appears in the `relative_cooccurrence` template — so no co-occurrence
   finding could ever be persisted and `test_allowed_claims_pass` fails by construction. The
   `hypothesis` template separately carried neither a scope marker nor an `n` and failed the
   scope check. Both fixed; the failure-handling text now distinguishes a template bug (fails at
   import) from a model error (retried once).
4. **§5.1, selection on the outcome.** `G_T` was "groups with a supported match", with no pass
   restriction — readmitting precisely the bias §4.2 exists to prevent, and in the worst place.
   Pass-2 items are collected *because* theme queries retrieved them, and one query kind is
   `brand_x_theme` built from already-discovered brands, so a brand the generator used gained
   listings in both numerator and denominator. Denominator is now pass-1-screened, with a
   stricter `category_level_only` variant.

**Serious defects fixed.** Noisy-OR let a single `title_llm` source at `model_confidence = 1.0`
reach exactly 0.70 and promote a brand on one source, while the stated justification
(`1 − 0.55·0.55 = 0.6975` as "two 0.55 sources") was arithmetically mislabelled — that value is
two 0.45 sources and it *fails* the threshold; per-source cap lowered to 0.60, a correlation
discount added for the seller-text family (title, brand field, variant, shop name are one
seller's claim restated, not independent witnesses), and `n_distinct_sources ≥ 2` is now an
explicit gate rather than an emergent property. `s_rev = 0.0` for review-less listings was
described as "only ever adds" but under fixed weights penalises them by up to 0.15, biasing
`supported` toward older, better-ranked listings; it is now `NULL` with weight renormalisation,
and the two-material-component rule makes the "no single signal" invariant survive
renormalisation (which the old arithmetic did not: with `s_rev` absent, `s_emb_cal = 1.0` alone
scored 0.66). The prevalence bound treated duplicate-inflated listings as i.i.d. draws and is
now computed on product groups with a design-effect adjustment. Pooled rating Wilson intervals
ignored clustering within listings and are now cluster-bootstrapped with a `n_groups ≥ 5`
ordering gate. Multiplicity was controlled only for a test that can never run; BH-FDR now
governs the whole co-occurrence family, and the `brand_theme` unit gets a degree-preserving
null because an independence null there mostly measures brand size. The Wilson co-occurrence
gate was orientation-dependent — a label-invariance defect — and must now pass in both
orientations. Single-linkage union-find could chain `a≈b≈c` into one group, the exact false
merge the high threshold was chosen to prevent; a cohesion guard and deterministic ordering
were added (the latter also restores the idempotency §8.3 claims). The longitudinal lead-lag
escape hatch would have manufactured the spurious correlation §6.5 refuses, because cross-run
"first observation" is driven by query-set churn and follow-up queries come from social themes;
it now requires a frozen query panel. Theme selection conditioned on `pass1_max_score`,
pre-filtering the population over which absence is reported; removed, with 3 of 12 slots
reserved for low-expression themes. Platform **result ranking** — the largest selection effect
on every commerce metric — was unnamed; §3.8 now states it per metric, `result_rank` is stored
and `rank_sensitivity` reported. LLM character offsets, `denoised_title`, pack spans and
`model_confidence` were trusted unverified; §2.3.1 re-derives all of them in code. Claim numbers
had no stated provenance; they are now code-rendered from stored metrics with a post-render
equality check. Vision corroboration was allocated by listing count, concentrating confidence on
heavily-resold brands; it is now allocated by attribution need. `basis='listing_price'` would
have measured pack size and was removed; range listings no longer enter price statistics at
their minimum; the brand's own groups are excluded from its reference distribution; the
flag/median circularity is resolved by a stated two-pass order. Group-level `composition` and
`brand_id` resolution on member disagreement was unspecified although §5.1 filters on both.
`sold_count_guard` was advisory and is now enforced by a non-arithmetic value type.
`commerce_price_reference` was an owned table with no DDL; it has one. `commerce_items` and
`commerce_bundle_components` forward-referenced `commerce_product_groups` in a single migration,
which would fail; deferred ALTERs added. The seller-identity coalesce meant `seller_id IS NULL`
never fires, so `seller_identity_incomplete` never set. The review identity index does not
collapse exact duplicates as §7.2 claimed; a second index does. The degradation matrix
simultaneously fell back to Serper and reported `not_investigated`, and the Jina embedding
fallback was useless because mixed models are refused; both resolved, and a terminal
no-credential state added. §8.2's `s_lex` BM25 normalisation was not bounded in `[0,1]` as typed
and is now a coverage measure — which is also what makes the absence test possible.

**Seeding violations (brief §1, §34), all removed.** The literal word `snack` was hard-coded in
the §2.3 production prompt as a category token; it is now the `{category_anchor}` placeholder.
`Filipinos` was hard-coded in the `market_projection` blocklist, which both bakes in the first
market and silently stops guarding for any other; it is now built from market config with a
generic default. §7.4 asserted that reviews mix English and Tagalog — a stated assumption about
the market rather than a discovered one; rewritten market-neutrally with the language
distribution reported as data. The `per_100g` unit-price basis, the gram weight bounds and the
PHP price band are category- and market-shaped and were code constants; they are now
objective-derived config with the leak acknowledged. `test_no_seeded_vocabulary` as written was
not implementable (a denylist of flavours cannot be written without writing one) and is replaced
by an allowlist test over prompt static text, which fails closed and is what catches a token
like `snack`.

**Residual risk — what this document still cannot promise.**

- **Every number here describes a ranked, query-generated convenience sample.** §3.8 states it
  and no mitigation removes it; there is no sampling frame for marketplace assortment.
- **Almost every threshold remains reasoned, not measured** (OQ-2, OQ-6, OQ-11, OQ-12, OQ-13).
  The invariants are now asserted directly, so recalibration should not break them — but the
  operating points will move on real data, and §5.1's denominator size is particularly sensitive
  to `RHO`.
- **The absence result now depends on `s_lex` being genuinely zero and on top-K recall.** If a
  theme's commerce expression uses vocabulary unrelated to the theme's discriminative terms and
  sits outside the embedding's top 20, the design will report a bounded absence where there is
  presence — the dangerous direction. OQ-12 exists to measure exactly this before the state is
  trusted.
- **Cohort price-scale resolution is unvalidated** and may refuse on the first real dataset,
  blacking out §5.2 run-wide. That is the intended failure mode, but it is a failure mode.
- **`sold_semantics` stays `'unknown'`** and the forbidden-operations list is the only
  mitigation (OQ-1).
- **`absent_with_coverage` and the co-occurrence FDR family interact**: a run reporting many
  absences and many screened pairs is making many implicit comparisons, and only the latter is
  family-controlled.
- **Scale.** 18 tables and ~20 thresholds for one subsystem is a lot against brief §37's "do not
  over-engineer". The guardrail machinery is justified by the subsystem's purpose, but the
  implementation order should be §1–§3 first and §4–§6 only once real payloads exist; most of the
  open questions cannot be closed any other way.
