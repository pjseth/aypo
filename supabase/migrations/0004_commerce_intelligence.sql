-- 0004_commerce_intelligence.sql
--
-- Marketplace listings, their reviews, the product-group presence unit, the
-- theme -> commerce bridge, and the competitor relationship ledger.
--
-- Upstream tables this migration references, created by earlier files in
-- filename order (apps/api/aypo/db/migrate.py applies them in that order):
--   0002  research_runs, research_queries
--   0003  brands
-- Nothing here references the clustering track: a *theme* is a read-model over
-- a labelled cluster (ARCHITECTURE D-12), so `theme_id` is a plain uuid with no
-- foreign key, exactly as doc 05 declares it. That also lets this file land
-- before the clustering migration.
--
-- No CREATE EXTENSION is needed: gen_random_uuid() has been core Postgres since
-- 13, and no table here stores a vector (commerce embeddings live in
-- content_embeddings, keyed by item_kind, per ARCHITECTURE C2 -- deferred).
--
-- Statement order is binding (ARCHITECTURE D-44, doc 05 "Migration ordering"):
-- enums, then tables with no forward references, then commerce_product_groups,
-- then the ALTER that attaches commerce_items.product_group_id. Declaring that
-- FK inline would fail with "relation commerce_product_groups does not exist".


-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------

-- `platform` is deliberately NOT an enum anywhere below (ARCHITECTURE D-2): an
-- enum outlives the provider it names, and a value can only be removed by a
-- rewrite. The vocabulary is owned by collection_sources and validated by the
-- collectors. Note the live contract in aypo/models/provenance.py spells the
-- marketplaces `shopee` / `lazada`, not the architecture's `shopee_ph` /
-- `lazada_ph`; the existing code wins (104 tests depend on it) and text columns
-- make that divergence a one-line data fix rather than a type migration.

-- Mirrors aypo.models.commerce.ListingKind exactly. The architecture calls this
-- `listing_composition` with six values; the four-value enum already in the
-- pydantic contract wins, and the two discarded values ('assortment_unspecified',
-- 'non_category_or_accessory') fold into 'unclear'. Without this column a
-- multi-brand bundle is silently attributed to one brand and every
-- share-of-listings statistic is wrong.
create type listing_kind as enum ('single', 'bundle', 'multi_brand', 'unclear');

-- 'unknown' is the only honest default: Shopee returns a bucketed string
-- ("<100", "500+"), Lazada returns an integer, and neither documents whether the
-- figure is lifetime or windowed. Sold count is a visibility proxy, never a
-- sales figure (brief §28), and nothing downstream may sum or average it.
create type sold_semantics as enum (
  'unknown',
  'platform_lifetime_reported',
  'platform_window_reported',
  'provider_derived'
);

-- Distinguishes "the platform did not give us a price" from "we could not parse
-- what it gave us" from "we parsed it and it is implausible". Brief §28 requires
-- insufficient data to be identifiable rather than quietly coerced to null.
-- DIVERGENCE from doc 05 §1.1: its 'non_php_currency' is renamed
-- 'currency_mismatch'. A currency code belongs in the market config pack
-- (ARCHITECTURE D-29), not baked into a Postgres type; brief §34 forbids a
-- market assumption in the schema.
create type price_status as enum (
  'ok', 'ambiguous', 'unparseable', 'out_of_band', 'range_too_wide', 'currency_mismatch'
);

-- Two tiers only. A numeric match_score is stored alongside, so the tier is a
-- reading aid and never the thing a finding cites.
create type match_tier as enum ('supported', 'weak');

create type group_method as enum (
  'blocking_key_exact', 'similarity_merge', 'llm_adjudicated', 'singleton'
);

-- The three states that make a negative result usable (brief §17). Without the
-- middle one, "we looked properly and found nothing" is indistinguishable from
-- "we could not look", and the product would report absences it has not earned.
create type commerce_absence_state as enum (
  'absent_with_coverage', 'inconclusive_low_coverage', 'not_investigated'
);

create type competitor_relationship_type as enum (
  'direct_substitute', 'category_peer', 'adjacent_category',
  'co_consumed', 'same_shelf', 'unclear'
);

-- The eleven detectors. They are correlated by construction -- one comparison
-- video is a direct_comparison AND a co_appearance AND possibly a shared_cluster
-- -- which is why the evidence ledger below is keyed to prevent double counting.
create type competitor_signal_kind as enum (
  'direct_comparison', 'co_appearance', 'shared_cluster', 'category_search_coappearance',
  'marketplace_adjacency', 'recurring_mention', 'substitution_language', 'ranking_review',
  'positioning_similarity', 'shared_creator', 'shared_cultural'
);

create type evidence_unit as enum (
  'content_item', 'commerce_item', 'commerce_review', 'web_document',
  'cluster', 'creator', 'research_query', 'aggregate'
);

create type relationship_decay_state as enum ('active', 'stale', 'dormant', 'archived');


-- ---------------------------------------------------------------------------
-- commerce_sellers
-- ---------------------------------------------------------------------------

create table commerce_sellers (
  id                 uuid primary key default gen_random_uuid(),
  platform           text not null,
  platform_seller_id text,
  name               text,
  -- Casefolded/punctuation-stripped name, written by the collector. It is the
  -- identity fallback when a platform exposes no stable seller id, so it must be
  -- a stored column and not a function of `name` evaluated at index time.
  name_slug          text,
  url                text,
  location           text,
  -- The provider's own badge, copied verbatim. We do not verify it, and no
  -- finding may describe a seller as official on the strength of this alone.
  is_official_store  boolean,
  rating             numeric(3,2),
  follower_count     int,
  first_seen_at      timestamptz not null default now()
);

-- The literal 'unknown' keeps the index total: a seller with neither an id nor a
-- parseable name collapses into one row per platform rather than one per
-- listing, which stops commerce_product_groups.distinct_seller_count counting
-- anonymity as diversity.
create unique index commerce_sellers_identity
  on commerce_sellers (platform, coalesce(platform_seller_id, name_slug, 'unknown'));


-- ---------------------------------------------------------------------------
-- commerce_items
-- ---------------------------------------------------------------------------
-- Run-independent (ARCHITECTURE D-4). There is deliberately no research_run_id:
-- with a run-scoped key the same listing re-collected next run becomes a new
-- row, every cross-run commerce group sees total membership turnover, and churn
-- detection fires on every theme. Which run saw which listing is recorded in
-- research_results.commerce_item_id, the single provenance join for both
-- channels.

create table commerce_items (
  id                    uuid primary key default gen_random_uuid(),

  -- identity and provenance (brief §7)
  platform              text not null,
  platform_item_id      text not null,
  source_url            text not null,
  collection_provider   text,
  collection_mode       text not null default 'provider_actor'
                        check (collection_mode in ('provider_actor', 'web_search_snippet')),
  raw_payload           jsonb not null default '{}'::jsonb,
  raw_payload_sha256    bytea,
  normalisation_version int not null default 1,
  observed_at           timestamptz,   -- when the PROVIDER observed it, not when we stored it
  first_seen_at         timestamptz not null default now(),
  last_seen_at          timestamptz not null default now(),

  -- text
  title_raw             text,
  -- Marketplace titles are keyword-stuffed; the denoised form is what gets
  -- embedded, because raw-title vectors are dominated by tokens shared across
  -- the whole corpus and compress cosine toward an uninformative baseline.
  title_denoised        text,
  description_raw       text,

  -- Brand as it literally appears, plus how sure the extractor is. Held
  -- unresolved on purpose: resolution to a brands row is reversible and happens
  -- through the brand-resolution track, so a wrong guess here never becomes a
  -- stamped fact (brief §28 prefers identifying insufficient data).
  brand_surface_form    text,
  brand_confidence      numeric(3,2) check (brand_confidence is null
                                            or brand_confidence between 0 and 1),

  seller_id             uuid references commerce_sellers(id) on delete set null,

  -- Price in integer minor units. Floats cannot represent a decimal price
  -- exactly and the error compounds through medians and unit-price
  -- normalisation; `currency` has no default because defaulting it would encode
  -- a market assumption (brief §34, ARCHITECTURE D-29).
  currency              char(3),
  price_minor           bigint,
  price_min_minor       bigint,
  price_max_minor       bigint,
  price_is_range        boolean not null default false,
  original_price_minor  bigint,
  -- A FRACTION (0.25 = 25% off), as the architecture keys it. The pydantic
  -- CommerceItem.discount_percent is a percentage, so the writer divides by 100.
  discount_pct          numeric(5,4),
  price_status          price_status not null default 'unparseable',

  -- Marketplaces routinely advertise a discount against an inflated "original"
  -- price, so the computed figure is preferred over the platform's claim -- the
  -- same rule as CommerceItem.effective_discount, kept here as a generated
  -- column so a writer cannot forget to recompute it after a price refresh.
  discount_pct_computed numeric(5,4) generated always as (
    case
      when price_minor is not null and original_price_minor is not null
           and original_price_minor > 0 and original_price_minor > price_minor
        then round((original_price_minor - price_minor)::numeric / original_price_minor, 4)
      when price_minor is not null and original_price_minor is not null
           and original_price_minor > 0
        then 0
      else null
    end
  ) stored,

  -- Pack and weight. Comparing headline prices across pack sizes is one of the
  -- easiest ways to produce a confidently wrong price-positioning claim, so the
  -- comparable figure is derived here rather than at read time.
  pack_count            int,
  unit_net_weight_g     numeric(10,2),
  total_net_weight_g    numeric(10,2),
  weight_basis          text check (weight_basis is null
                                    or weight_basis in ('g', 'ml_as_g', 'oz_converted')),
  unit_price_per_100g_minor bigint generated always as (
    case
      when price_minor is not null and total_net_weight_g is not null
           and total_net_weight_g > 0
        then (price_minor * 100.0 / total_net_weight_g)::bigint
      else null
    end
  ) stored,

  -- Platform-reported metrics, stored AS REPORTED and never recomputed.
  rating_avg            numeric(3,2),
  rating_count          int,
  review_count          int,
  -- Only set when the payload carried an exact integer (Lazada does; Shopee
  -- usually does not).
  sold_count_raw        bigint,
  -- The platform's own string, verbatim: "<100", "500+", "1.2k sold". Keeping it
  -- is what makes the bucketing visible instead of silently parsed into a number
  -- that looks precise.
  sold_count_text       text,
  sold_count_band       text,
  sold_semantics        sold_semantics not null default 'unknown',
  metrics_as_of         timestamptz,

  -- classification
  listing_kind          listing_kind not null default 'unclear',
  product_label         text,
  product_slug          text,
  variant_labels        text[] not null default '{}',
  -- Round-trips aypo.models.commerce.Variant (name/price/stock/sku). The
  -- architecture keeps only variant_labels because per-variant prices belong on
  -- commerce_item_snapshots, which is deferred; holding them as jsonb here loses
  -- nothing and avoids discarding collected data.
  variants              jsonb not null default '[]'::jsonb,
  category_path         text[] not null default '{}',
  -- The reference-distribution key for any price comparison: comparing a listing
  -- against the whole corpus rather than its own category scope is meaningless.
  category_scope        text,

  -- quality (brief §15/§16: missing fields must not break analysis)
  field_quality         jsonb not null default '{}'::jsonb,  -- {field: present|missing|...}
  quality_flags         text[] not null default '{}',
  is_degraded           boolean not null default false,

  image_url             text,

  -- grouping
  blocking_key          text,
  -- Near-duplicate group across the corpus (ARCHITECTURE D-6, owned by
  -- collectors/dedupe.py). Distinct from product_group_id: this is "the same
  -- record twice", that is "the same physical product sold by many resellers".
  dedupe_group_id       uuid,
  -- FK attached by ALTER below; commerce_product_groups does not exist yet.
  product_group_id      uuid,

  created_at            timestamptz not null default now(),

  constraint commerce_items_identity unique (platform, platform_item_id),
  constraint commerce_items_price_nonneg
    check (price_minor is null or price_minor >= 0),
  constraint commerce_items_price_range_order
    check (price_min_minor is null or price_max_minor is null
           or price_min_minor <= price_max_minor),
  -- Above ~95% the "original" price is almost always fabricated, so the row is
  -- quarantined by the normaliser rather than stored as a real discount.
  constraint commerce_items_discount_bounded
    check (discount_pct is null or (discount_pct >= 0 and discount_pct <= 0.95)),
  constraint commerce_items_rating_bounded
    check (rating_avg is null or (rating_avg >= 0 and rating_avg <= 5)),
  constraint commerce_items_counts_nonneg
    check (coalesce(rating_count, 0) >= 0 and coalesce(review_count, 0) >= 0
           and coalesce(sold_count_raw, 0) >= 0),
  -- A sold figure of any kind must carry its semantics explicitly. The default
  -- 'unknown' satisfies this; the constraint exists so a future writer cannot
  -- store a number with the column left out of the INSERT list by accident.
  constraint commerce_items_sold_needs_semantics
    check (sold_count_raw is null or sold_semantics is not null)
);

create index commerce_items_platform on commerce_items (platform);
create index commerce_items_block    on commerce_items (platform, blocking_key);
create index commerce_items_group    on commerce_items (product_group_id);
create index commerce_items_dedupe   on commerce_items (dedupe_group_id)
  where dedupe_group_id is not null;
-- Partial: only priced rows participate in a price reference distribution.
create index commerce_items_scope    on commerce_items (platform, category_scope)
  where price_status = 'ok';
create index commerce_items_catpath  on commerce_items using gin (category_path);
create index commerce_items_flags    on commerce_items using gin (quality_flags);


-- ---------------------------------------------------------------------------
-- commerce_reviews
-- ---------------------------------------------------------------------------
-- Reviews arrive as the provider's top-N page in an undocumented order. That is
-- a ranked subset, never a sample, and review_page_index is what lets a caveat
-- say so instead of implying randomness.

create table commerce_reviews (
  id                    uuid primary key default gen_random_uuid(),
  item_id               uuid not null references commerce_items(id) on delete cascade,
  -- The listing is run-independent but a review page is a per-run observation.
  research_run_id       uuid not null references research_runs(id) on delete cascade,
  platform_review_id    text,

  rating                int check (rating is null or rating between 1 and 5),
  text_raw              text,
  text_sha256           bytea,
  -- Generated rather than written: a review with a rating and no text is common
  -- and must not be counted in any text-derived denominator.
  has_text              boolean generated always as (
                          text_raw is not null and length(btrim(text_raw)) > 0
                        ) stored,
  languages             text[] not null default '{}',

  review_date           date,
  -- Marketplaces report "2 months ago" as often as a date. Recording the
  -- precision is what keeps a coarse bucket out of a temporal claim.
  review_date_precision text not null default 'unknown'
                        check (review_date_precision
                               in ('day', 'month', 'relative_bucket', 'unknown')),
  review_date_raw       text,

  variant_label         text,
  helpful_count         int,
  author_handle         text,
  media_count           int,
  review_page_index     int,
  collected_at          timestamptz not null default now()
);

-- Keys on the provider's review id FIRST, so two genuinely distinct reviews that
-- happen to share text are not collapsed.
create unique index commerce_reviews_identity on commerce_reviews
  (item_id, coalesce(platform_review_id, encode(text_sha256, 'hex'), id::text));
-- ...which means exact-text duplication needs its own index: some platforms
-- issue a fresh id per copy of the same text, and counting those as independent
-- inflates every review-based denominator.
create unique index commerce_reviews_text_identity on commerce_reviews (item_id, text_sha256)
  where text_sha256 is not null;
create index commerce_reviews_item on commerce_reviews (item_id);
create index commerce_reviews_run  on commerce_reviews (research_run_id);


-- ---------------------------------------------------------------------------
-- commerce_product_groups
-- ---------------------------------------------------------------------------
-- The presence unit. Counting listings would make marketplace presence a
-- function of how many resellers list the same packet, which varies by brand and
-- has nothing to do with the brand's actual shelf. Every presence statistic and
-- every coverage denominator counts groups.

create table commerce_product_groups (
  id                     uuid primary key default gen_random_uuid(),
  research_run_id        uuid not null references research_runs(id) on delete cascade,
  group_key              text not null,            -- blocking key of the group's medoid
  brand_id               uuid references brands(id) on delete set null,
  brand_slug             text,
  product_label          text,                     -- medoid's label, for display
  product_slug           text,
  listing_kind           listing_kind not null default 'unclear',
  method                 group_method not null,
  confidence             numeric(3,2) not null check (confidence between 0 and 1),
  medoid_item_id         uuid references commerce_items(id) on delete set null,

  listing_count          int not null default 0,
  distinct_seller_count  int not null default 0,
  platforms              text[] not null default '{}',

  median_price_minor               bigint,
  median_unit_price_per_100g_minor bigint,
  price_n                int not null default 0,   -- the denominator behind the medians
  -- Display only. Summing review counts across resellers of the same product is
  -- legitimate; averaging their star ratings is not, and nothing may do it.
  rating_count_sum       int,

  quality_flags          text[] not null default '{}',
  -- Grouping thresholds will be recalibrated. Stamping the version makes two
  -- runs' group counts comparable only when they were produced the same way.
  method_version         text not null,
  created_at             timestamptz not null default now(),

  unique (research_run_id, group_key, method_version)
);

create index commerce_groups_brand on commerce_product_groups (research_run_id, brand_id);

-- Deferred FK (see the ordering note at the top of this file).
alter table commerce_items
  add constraint commerce_items_group_fk
  foreign key (product_group_id) references commerce_product_groups(id) on delete set null;


-- ---------------------------------------------------------------------------
-- commerce_theme_queries
-- ---------------------------------------------------------------------------
-- Marketplace queries GENERATED FROM a discovered theme (brief §8D, §17). The
-- rationale / source_terms / generated_by columns are the audit that a query
-- came out of the data: without them there is no way to tell a discovered query
-- from a hardcoded one, which is exactly what brief §1 forbids.

create table commerce_theme_queries (
  id                 uuid primary key default gen_random_uuid(),
  research_run_id    uuid not null references research_runs(id) on delete cascade,
  theme_id           uuid not null,   -- read-model id over a labelled cluster (D-12); no FK
  research_query_id  uuid references research_queries(id) on delete set null,
  platform           text not null,
  query_text         text not null,
  query_kind         text not null,
  rationale          text not null,
  -- The theme's own discriminative terms this query was built from. A term that
  -- is not in the theme is drift by construction.
  source_terms       text[] not null default '{}',
  generated_by       text not null,   -- model id
  prompt_version     text not null,
  validity_rejected  boolean not null default false,
  rejection_reason   text,
  results_returned   int,
  results_kept       int,
  status             text not null default 'pending'
                     check (status in ('pending', 'running', 'done', 'failed', 'skipped')),
  error              text,
  created_at         timestamptz not null default now(),

  unique (research_run_id, theme_id, platform, query_text),
  constraint ctq_rejection_has_reason
    check (not validity_rejected or rejection_reason is not null)
);

create index commerce_theme_queries_theme on commerce_theme_queries (research_run_id, theme_id);


-- ---------------------------------------------------------------------------
-- commerce_theme_matches
-- ---------------------------------------------------------------------------
-- Match strength is STORED AS EVIDENCE, never asserted. The API returns the
-- components, not a boolean, so the drill-down can show that a link rests mainly
-- on buyer language (s_rev) rather than on the product itself (s_emb/s_lex).

create table commerce_theme_matches (
  id                 uuid primary key default gen_random_uuid(),
  research_run_id    uuid not null references research_runs(id) on delete cascade,
  theme_id           uuid not null,
  item_id            uuid not null references commerce_items(id) on delete cascade,
  product_group_id   uuid references commerce_product_groups(id) on delete set null,

  match_score        numeric(4,3) not null check (match_score between 0 and 1),
  tier               match_tier not null,

  -- Components, each stored separately so the composite is always decomposable.
  s_emb              numeric(5,4) not null,   -- absolute cosine, kept for audit
  s_emb_cal          numeric(4,3) not null,   -- rank within this run's corpus, NOT presence
  s_lex              numeric(4,3) not null,   -- literal theme terms in the match document
  s_rev              numeric(4,3) not null,   -- review language, deliberately separable
  s_prov             numeric(4,3) not null,   -- which query family retrieved the listing
  weights            jsonb not null,

  -- The calibration background for s_emb_cal. Stored per row because the
  -- transform is relative to the corpus: without q50/q99/n the number is not
  -- reproducible and not comparable across runs.
  background_q50     numeric(5,4),
  background_q99     numeric(5,4),
  background_n       int,

  semantic_gate_passed  boolean not null,
  -- How many components were materially non-zero. One strong component is a
  -- coincidence; the gate exists so a single lexical hit cannot carry a match.
  n_material_components smallint not null default 0,
  material_components   text[] not null default '{}',
  pass_no            smallint not null default 1
                     check (pass_no in (1, 2)),   -- 1 = corpus screen, 2 = targeted
  matched_on         text[] not null default '{}',
  evidence_terms     text[] not null default '{}',
  evidence_review_ids uuid[] not null default '{}',

  embedding_model    text not null,
  method_version     text not null,
  created_at         timestamptz not null default now(),

  unique (theme_id, item_id, method_version)
);

create index commerce_theme_matches_theme
  on commerce_theme_matches (theme_id, tier, match_score desc);
create index commerce_theme_matches_group
  on commerce_theme_matches (product_group_id, theme_id);
create index commerce_theme_matches_item on commerce_theme_matches (item_id);


-- ---------------------------------------------------------------------------
-- commerce_theme_absences
-- ---------------------------------------------------------------------------
-- A theme with no commerce expression is a valid and interesting result
-- (brief §17) -- but only if it is distinguishable from "we could not look".
-- Every column below exists to make that distinction checkable by a reader.

create table commerce_theme_absences (
  id                    uuid primary key default gen_random_uuid(),
  research_run_id       uuid not null references research_runs(id) on delete cascade,
  theme_id              uuid not null,
  state                 commerce_absence_state not null,

  -- what we attempted
  queries_attempted     int not null default 0,
  -- A theme with no product vocabulary legitimately generates zero queries. That
  -- is a fact about the theme, not a collection failure, so it is recorded with
  -- its rationale rather than treated as low coverage.
  zero_valid_queries    boolean not null default false,
  zero_query_rationale  text,
  platforms_attempted   text[] not null default '{}',
  platforms_unavailable text[] not null default '{}',
  unavailable_reason    text,
  selection_reason      text not null default 'ranked',

  -- MEASURED (docs/ENVIRONMENT.md): on the free Apify plan the Shopee actor
  -- serves a shared cache and returns a `_notice` on a miss. An empty result set
  -- from that path is the cache saying nothing, not the marketplace saying
  -- nothing, and it may never support an absence claim.
  provider_cache_notice boolean not null default false,

  -- what we screened. groups_screened, not listings_screened, is the denominator
  -- for the prevalence bound: the same physical product appears dozens of times
  -- across resellers, so listings are not independent draws and a bound computed
  -- on them overstates coverage by roughly the duplication factor.
  listings_screened     int not null default 0,
  groups_screened       int not null default 0,
  -- Groups retrieved by one query are still correlated through the platform's
  -- ranker, so the bound is computed on a design-effect-adjusted count.
  n_effective           numeric(8,2) not null default 0,
  intra_query_rho       numeric(4,3) not null default 0.300,
  n_distinct_queries    int not null default 0,
  n_category_level      int not null default 0,   -- groups found by queries not naming the theme

  -- what we found, or did not
  max_s_lex             numeric(4,3) not null default 0,
  topk_adjudicated      int not null default 0,
  topk_positive         int not null default 0,
  -- Diagnostic shown beside the absence, never a gate: s_emb_cal clips the
  -- corpus maximum to 1.0 for every theme, so a score threshold here could never
  -- fire and would make absent_with_coverage unreachable.
  max_match_score       numeric(4,3),
  max_match_item_id     uuid references commerce_items(id) on delete set null,

  prevalence_upper_bound numeric(6,5),            -- 1 - (1 - confidence_level)^(1/n_effective)
  confidence_level      numeric(3,2) not null default 0.95,

  narrative             text not null,
  method_version        text not null,
  created_at            timestamptz not null default now(),

  unique (research_run_id, theme_id, method_version),

  -- The structural half of the absence gates. The calibratable numbers
  -- (groups_screened >= 150, queries_attempted >= 3, K = 20) stay in the
  -- resolved run config so recalibration is not a schema migration; what cannot
  -- be allowed to drift is the shape of the claim -- an absence must have
  -- screened something, must not rest on a provider cache miss, must have found
  -- no literal term, and must have had its nearest neighbours individually
  -- rejected.
  constraint cta_absence_requires_coverage check (
    state <> 'absent_with_coverage' or (
      groups_screened > 0
      and not provider_cache_notice
      and max_s_lex = 0
      and topk_adjudicated > 0
      and topk_positive = 0
      and (queries_attempted > 0 or zero_valid_queries)
    )
  ),
  constraint cta_zero_queries_has_rationale
    check (not zero_valid_queries or zero_query_rationale is not null)
);

create index commerce_theme_absences_theme
  on commerce_theme_absences (research_run_id, theme_id);


-- ---------------------------------------------------------------------------
-- competitor_relationships
-- ---------------------------------------------------------------------------
-- Brief §24 forbids a simplistic overall competitor score. `confidence` here is
-- per relationship -- how sure we are that THIS pair stands in THIS relation --
-- and is never comparable across pairs as a ranking. There is no column for an
-- aggregate competitor score and none may be added.
--
-- Pairs are canonical: one row in uuid order, never two mirror rows. Mirror rows
-- drift out of sync and get double counted by every aggregate. The brief's
-- two-orientation read shape is restored by the view below.

create table competitor_relationships (
  id                    uuid primary key default gen_random_uuid(),
  brand_id              uuid not null references brands(id) on delete cascade,
  competitor_brand_id   uuid not null references brands(id) on delete cascade,
  relationship_type     competitor_relationship_type not null,

  -- DISTINCT evidence items for the pair, unweighted. The weighted, correlated
  -- view of the same evidence is `strength`; keeping both visible stops a
  -- confidence number being read as a count.
  evidence_count        int not null default 0,
  evidence_count_social   int not null default 0,
  evidence_count_commerce int not null default 0,
  evidence_count_web      int not null default 0,
  -- Creators are the independence unit for social evidence: thirty videos from
  -- one creator are not thirty observations.
  distinct_creator_count  int not null default 0,
  n_eff                 numeric(8,3) not null default 0,

  strength              numeric(4,3) not null default 0,
  volume                numeric(4,3) not null default 0,
  diversity             numeric(4,3) not null default 0,
  signal_strengths      jsonb not null default '{}'::jsonb,

  confidence            numeric(4,3) not null default 0.000
                        check (confidence between 0 and 1),
  -- Generated, so the band and the number can never disagree. Gates are stated
  -- on the band, not on a decimal (ARCHITECTURE D-21), so that recalibrating the
  -- boundaries moves every consumer at once instead of silently moving one.
  confidence_band       text generated always as (
                          case when confidence >= 0.75 then 'very_strong'
                               when confidence >= 0.55 then 'strong'
                               when confidence >= 0.35 then 'moderate'
                               else 'low' end
                        ) stored,
  -- Which boundary set produced the band. A recalibration must be
  -- distinguishable from an actual movement in the evidence.
  band_scheme_version   text not null default 'fixed-2026-10-02',

  -- Signed: which brand the comparison evidence favours as the referent. This is
  -- what carries directionality now that the pair is stored once.
  direction_balance     numeric(5,3),
  -- Two brands owned by the same company are not competitors; the gate excludes
  -- them rather than the UI hiding them after the fact.
  same_corporate_family boolean not null default false,

  first_detected        timestamptz not null default now(),
  last_detected         timestamptz not null default now(),
  first_detected_run_id uuid,
  last_evidence_run_id  uuid,
  decay_state           relationship_decay_state not null default 'active',

  -- A seed may confirm a relationship but must never be the only reason one
  -- exists (brief §23: seeds must not prevent discovery). Keeping origin and
  -- confirmation separate is what makes "we found this ourselves" checkable.
  -- DIVERGENCE: the architecture types this as the `brand_origin` enum owned by
  -- the brands track. Text with a CHECK keeps this file independent of that
  -- file's type; the value domain is identical.
  discovery_origin      text not null default 'discovered'
                        check (discovery_origin in ('discovered', 'seed', 'manual')),
  seed_confirmed        boolean not null default false,

  -- Capped at 50 by the writer, ordered by strength * weight * recency. An
  -- unbounded array TOASTs and bloats every row read; the full set lives in
  -- competitor_signal_evidence and is what the drill-down queries.
  supporting_content_ids uuid[] not null default '{}',

  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),

  constraint cr_distinct  check (brand_id <> competitor_brand_id),
  constraint cr_canonical check (brand_id < competitor_brand_id)
);

-- One row per (canonical pair, type): a pair can legitimately be both a
-- category_peer and co_consumed, and collapsing those would lose the typing.
create unique index cr_pair_type_uq on competitor_relationships
  (brand_id, competitor_brand_id, relationship_type);
create index cr_band_idx on competitor_relationships (confidence_band, evidence_count desc)
  where decay_state in ('active', 'stale') and not same_corporate_family;
create index cr_brand_idx on competitor_relationships (brand_id, decay_state);
create index cr_comp_idx  on competitor_relationships (competitor_brand_id, decay_state);

-- The brief §23 read shape over the canonical rows. Readers must go through
-- this rather than hand-rolling the union, because the mirrored orientation has
-- to flip the sign of direction_balance and a hand-rolled copy will forget.
create view v_competitor_relationships
  with (security_invoker = true) as
  select id, brand_id, competitor_brand_id, relationship_type, evidence_count, confidence,
         confidence_band, first_detected, last_detected, supporting_content_ids,
         discovery_origin, seed_confirmed, decay_state, direction_balance,
         same_corporate_family, 1 as orientation
    from competitor_relationships
  union all
  select id, competitor_brand_id as brand_id, brand_id as competitor_brand_id,
         relationship_type, evidence_count, confidence,
         confidence_band, first_detected, last_detected, supporting_content_ids,
         discovery_origin, seed_confirmed, decay_state, -direction_balance,
         same_corporate_family, -1 as orientation
    from competitor_relationships;


-- ---------------------------------------------------------------------------
-- competitor_signal_evidence
-- ---------------------------------------------------------------------------
-- The ledger. The eleven signals are correlated by construction -- one
-- comparison video is a direct_comparison AND a co_appearance AND often a
-- shared_cluster -- so without a key that forbids re-inserting the same
-- (pair, signal, evidence) triple, the same video inflates evidence_count three
-- times and the relationship looks better supported than it is.

create table competitor_signal_evidence (
  id                   uuid primary key default gen_random_uuid(),
  brand_id             uuid not null references brands(id) on delete cascade,
  competitor_brand_id  uuid not null references brands(id) on delete cascade,
  signal               competitor_signal_kind not null,

  -- Polymorphic by design and deliberately without an FK: evidence_id points at
  -- whichever table evidence_unit names, and some of those tables land in later
  -- migrations. evidence_unit is the discriminator.
  evidence_unit        evidence_unit not null,
  evidence_id          uuid,

  strength             numeric(4,3) not null check (strength between 0 and 1),
  weight               numeric(4,3) not null default 1.000,
  direction            smallint not null default 0 check (direction in (-1, 0, 1)),
  is_aggregate         boolean not null default false,

  -- The permutation block and independence unit for social evidence. No FK: the
  -- creators table is owned by the collection track and this column is also set
  -- for commerce evidence, where it is null.
  creator_id           uuid,
  published_at         timestamptz,
  -- Mapped from the sponsorship bucket, so paid co-appearance can be weighted
  -- down rather than read as organic category association.
  sponsored_likelihood numeric(4,3),

  -- The near-duplicate-collapsed item ids behind this row. Two reuploads of one
  -- video are one piece of evidence.
  canonical_item_ids   uuid[] not null default '{}',
  detail               jsonb not null default '{}'::jsonb,
  research_run_id      uuid not null,
  detected_at          timestamptz not null default now(),

  constraint cse_canonical check (brand_id < competitor_brand_id),
  -- An aggregate signal (e.g. shared_cluster) has no single evidence row; an
  -- item-level signal must have one. Either way, exactly one shape is valid.
  constraint cse_agg_chk  check ((evidence_unit = 'aggregate') = (evidence_id is null))
);

-- The anti-double-count key. `nulls not distinct` (PG15+) is load-bearing: both
-- evidence_id and span_id are null for aggregate signals, and under the default
-- NULLS DISTINCT every re-run would insert another copy of the same aggregate.
-- span_id separates two substitution phrases inside one transcript, which are
-- genuinely two observations.
create unique index cse_uq on competitor_signal_evidence
  (brand_id, competitor_brand_id, signal, evidence_unit, evidence_id, (detail ->> 'span_id'))
  nulls not distinct;
create index cse_pair_idx  on competitor_signal_evidence (brand_id, competitor_brand_id, signal);
create index cse_unit_idx  on competitor_signal_evidence (evidence_unit, evidence_id);
create index cse_items_idx on competitor_signal_evidence using gin (canonical_item_ids);
create index cse_run_idx   on competitor_signal_evidence (research_run_id);
create index cse_creator_idx on competitor_signal_evidence (creator_id)
  where creator_id is not null;


-- ===========================================================================
-- DELIBERATELY DEFERRED
-- ===========================================================================
-- Brief §37 ranks "speed to usable MVP" above modular architecture, and §38
-- requires the end-to-end flow before any added sophistication. Everything below
-- is specified in ARCHITECTURE.md and is NOT implemented here. Nothing is
-- renamed or re-keyed, so each arrives as a plain additive migration
-- (ARCHITECTURE D-43: a later 00NN_<track>_amend.sql).
--
-- TABLES, from ARCHITECTURE §2.7 (0006_commerce):
--   commerce_item_snapshots        re-observation log for price/rating drift over time.
--                                  Needs >1 observation per listing; a single-run MVP
--                                  has none. Current values live on commerce_items.
--   commerce_item_brand_links      resolved brand attribution with brand_attribution_type
--                                  (primary|co_listed|mentioned_only|ambiguous). The MVP
--                                  carries the unresolved surface form on
--                                  commerce_items.brand_surface_form + brand_confidence
--                                  and the resolved brand on commerce_product_groups.brand_id.
--   commerce_bundle_components     per-component decomposition of a multi_brand bundle.
--                                  listing_kind already prevents misattribution, which is
--                                  the correctness requirement; the decomposition is extra.
--   commerce_group_links           cross-platform links between product groups.
--   commerce_group_merge_evidence  audit trail for LLM-adjudicated group merges.
--   commerce_price_reference       per-scope p25/p50/p75 reference distributions for price
--                                  positioning. Needs the §1.4 cohort of 200 priced rows.
--   commerce_quarantine            rejected payloads. The MVP drops them with a structured
--                                  log line; this is the replayable form.
--   products                       the thin catalogue projected from commerce_product_groups.
--   commerce_item_embeddings       already deleted upstream by ARCHITECTURE C2 in favour of
--                                  content_embeddings keyed by item_kind (clustering track).
--   commerce_coverage              already generalised upstream to collection_coverage (0001).
--
-- TABLES, from ARCHITECTURE §2.9 (0008_theme_commerce):
--   commerce_cooccurrence_stats    Fisher-exact theme x brand co-occurrence with family size.
--                                  Depends on a stable multi-run corpus.
--
-- TABLES, from ARCHITECTURE §2.10 (0009_competitors):
--   competitor_signal_scores       per run x pair x signal audit trail of the aggregation.
--   competitor_relationship_events appear/strengthen/weaken/retype/decay event log.
--   competitor_pair_enumeration    which pairs were considered and why a pair was skipped.
--   v_competitor_set_at(run, iter) point-in-time competitor set; needs the iteration columns
--                                  below, which are deferred with it.
--
-- COLUMNS:
--   commerce_sellers (§2.7)        raw_payload; is_unknown_identity (the one synthetic row
--                                  per platform -- the coalesce(...,'unknown') identity index
--                                  above achieves the same collapse without the flag).
--   commerce_items (§2.7)          rating_histogram; price_scale_divisor;
--                                  price_parse_confidence; pack_parse_confidence;
--                                  variant_slug; variant_confidence; extraction_version;
--                                  listing_role_signals; image_s3_key; noise_spans.
--                                  All written by the calibration / media-archival passes.
--   commerce_reviews (§7.1)        text_normalised; token_count; duplicate_of;
--                                  near_dup_cluster_id; seller_reply; review_sort_hint;
--                                  authenticity_flags; raw_payload; field_quality.
--                                  Review labels are NOT deferred-by-omission: ARCHITECTURE
--                                  D-18 deletes commerce_review_labels outright and routes
--                                  them to label_observations (taxonomy track).
--   commerce_product_groups (§3.5) variant_slug; form_code; weight_bucket;
--                                  total_net_weight_g_repr.
--   commerce_theme_matches (§4.5)  adjudicated; adjudication_rationale -- written only by the
--                                  LLM adjudication pass.
--   competitor_relationships (§5.2) type_evidence_count; group_strengths; quality_factors;
--                                  groups_available; typing_basis; typing_rule_id;
--                                  competing_types; contradiction_share; seed_id;
--                                  runs_without_new_evidence; and the ARCHITECTURE D-41
--                                  iteration columns first_detected_iteration /
--                                  last_detected_iteration, read only by research_saturation's
--                                  marginal-evidence-yield metric.
--   competitor_signal_evidence (§5.2) the D-41 iteration_no column, same reason.
--
-- NOT deferred but worth naming: nothing here stores a composite or overall
-- competitor score, and brief §24 means nothing here ever should.
