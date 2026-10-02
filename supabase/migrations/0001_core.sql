-- 0001_core — collection primitives.
--
-- Scope: the five tables the end-to-end flow in brief §38 cannot start without —
-- the platform vocabulary, the independence unit (creators), the run-independent
-- social corpus, the job queue, and the per-source coverage ledger that lets the
-- UI say "this source was unavailable" instead of showing a silent gap.
--
-- Deliberately omitted tables and columns are recorded in the comment block at
-- the end of this file rather than left undocumented.

create extension if not exists vector;

-- The research_strategy enum (D-1) is owned by 0002_research, which is its only
-- consumer. It is intentionally absent here so the two files cannot both define it.


-- ─────────────────────────────────────────────────────────────────────────────
-- collection_sources — the authoritative `platform` vocabulary (D-2)
-- ─────────────────────────────────────────────────────────────────────────────
-- `platform` is text validated by foreign key rather than a Postgres enum: an
-- enum would outlive a provider being removed, and every provider here is a
-- third-party actor that has already been renamed or deleted once
-- (ENVIRONMENT.md: two guessed actor ids returned 404).

create table collection_sources (
    platform        text primary key,
    -- 'apify' | 'youtube_api' | 'serper'. The transport, not the marketplace.
    provider        text not null,
    -- Registry default only. config.py's env-overridable actor settings win at
    -- runtime, because brief §11 requires an actor 404 to be a config change
    -- rather than a code change.
    actor_id        text,
    -- Bridges to Settings.capabilities(), whose keys do NOT all equal `platform`:
    -- platform 'web' is capability 'websearch', and 'shopee_ph'/'lazada_ph' are
    -- capabilities 'shopee'/'lazada'. Storing the mapping beats recomputing a
    -- string transform that happens to work for five of seven rows.
    capability_key  text not null,
    required_env    text[] not null default '{}',

    -- TRI-STATE, not boolean. A collector that accepts `published_after` and
    -- silently ignores it is strictly worse than one that rejects it: the items
    -- come back undated-but-claimed-dated, so the temporal plan believes a
    -- stratum was filled when it was not, and the resulting provenance is wrong
    -- rather than merely absent. 'unsupported' is therefore the conservative
    -- value for an unmeasured source — the allocator just declines to attach a
    -- date range — while 'accepted_but_ignored' is a measured indictment.
    supports_date_range text not null
        check (supports_date_range in ('honoured', 'accepted_but_ignored', 'unsupported')),
    -- When the above was last established by an actual request/response, not by
    -- reading a provider's documentation. NULL means "never measured", which the
    -- run report must disclose instead of presenting the value as a fact.
    supports_date_range_verified_at timestamptz,

    supports_sort   boolean not null default false,
    -- NULL = unknown/unbounded. A known shallow cap is a sampling-bias input
    -- (brief §28), not a performance note.
    max_page_depth  int,
    -- Whether the provider returns organically related queries we can expand on
    -- (TikTok's relatedSearchWords). This is how brief §8D theme expansion gets
    -- new vocabulary without anyone hardcoding a theme.
    supports_vocabulary_expansion boolean not null default false,

    enabled         boolean not null default true,
    notes           text,
    created_at      timestamptz not null default now()
);

-- The canonical D-2 vocabulary. Divergence worth knowing: the live
-- aypo.models.provenance.Platform enum spells the marketplaces `shopee` /
-- `lazada` and has no `web`-vs-`websearch` distinction. The pydantic enum is the
-- code contract 104 tests depend on, so it is not being changed here; the
-- collection layer must map Platform -> platform when it writes
-- collection_coverage for a marketplace, or the FK below will reject the row.
insert into collection_sources (
    platform, provider, actor_id, capability_key, required_env,
    supports_date_range, supports_date_range_verified_at,
    supports_sort, max_page_depth, supports_vocabulary_expansion, notes
) values
    ('tiktok', 'apify', 'clockworks~tiktok-scraper', 'tiktok', '{APIFY_TOKEN}',
     'unsupported', null, true, null, true,
     'Media is not returned by the actor; yt-dlp retrieves it from webVideoUrl.'),
    ('youtube', 'youtube_api', null, 'youtube', '{YOUTUBE_API_KEY}',
     'honoured', null, true, null, false,
     'Official API: publishedAfter/publishedBefore are part of the documented contract.'),
    ('instagram', 'apify', 'apify~instagram-scraper', 'instagram', '{APIFY_TOKEN}',
     'unsupported', null, false, null, false,
     'Non-critical source (brief §14): a failure must not block the run.'),
    ('facebook', 'apify', 'apify~facebook-posts-scraper', 'facebook', '{APIFY_TOKEN}',
     'unsupported', null, false, null, false,
     'Non-critical source (brief §14): a failure must not block the run.'),
    ('web', 'serper', null, 'websearch', '{SERPER_API_KEY}',
     'unsupported', null, false, null, false,
     'Discovery only (brief §7): snippets and URLs, retrieval happens separately.'),
    ('shopee_ph', 'apify', 'gio21~shopee-scraper', 'shopee', '{APIFY_TOKEN}',
     'unsupported', null, false, null, false,
     'country enum is UPPERCASE "PH"; review text needs a second actor.'),
    ('lazada_ph', 'apify', 'fatihtahta~lazada-scraper', 'lazada', '{APIFY_TOKEN}',
     'unsupported', null, false, null, false,
     'country enum is lowercase "ph" — not the same constant as Shopee''s.');


-- ─────────────────────────────────────────────────────────────────────────────
-- creators — D-5. The independence unit and permutation block.
-- ─────────────────────────────────────────────────────────────────────────────
-- This key is load-bearing for every statistic in the product. 40 items from 3
-- creators is 3 independent observations, not 40; if two rows here describe the
-- same account, n_eff silently inflates and a finding clears a support gate it
-- should have failed. Hence the identity is a stored generated column with a
-- unique constraint, not a convention enforced in Python.
--
-- Every column is platform-stated. Brief §28 forbids invented demographics, so
-- there is deliberately no age, gender, location or audience-profile column —
-- not even nullable, because a nullable column invites a model to fill it.

create table creators (
    id                  uuid primary key default gen_random_uuid(),
    platform            text not null references collection_sources (platform),
    platform_user_id    text,
    handle              text not null,
    -- NFKC + casefold, computed in Python: Postgres has no NFKC normaliser, and
    -- doing half the normalisation in SQL would be worse than doing none.
    handle_norm         text not null,

    -- coalesce() over the two identity sources, materialised so the unique
    -- constraint is a plain index rather than an expression index.
    identity_key        text generated always as (coalesce(platform_user_id, handle_norm)) stored,
    -- Which source that identity came from. A handle-based identity is weaker:
    -- a renamed account becomes a second creator and splits its own evidence, so
    -- §6.4's design-effect estimates are discounted for these rows.
    id_basis            text not null check (id_basis in ('platform_id', 'handle')),

    display_name        text,
    follower_count      int,
    following_count     int,
    content_count       int,
    verified            boolean,
    bio                 text,
    profile_url         text,
    avatar_url          text,

    first_seen_at       timestamptz not null default now(),
    updated_at          timestamptz not null default now(),

    constraint creators_identity_unique unique (platform, identity_key),
    -- A row claiming a platform id as its basis must actually have one.
    constraint creators_id_basis_consistent check (
        id_basis <> 'platform_id' or platform_user_id is not null
    )
);

create index creators_handle_norm_idx on creators (platform, handle_norm);


-- ─────────────────────────────────────────────────────────────────────────────
-- content_items — D-4 / D-7. Run-independent normalised social item.
-- ─────────────────────────────────────────────────────────────────────────────
-- Run-independent on purpose: if identity were run-scoped, the same video
-- re-collected next iteration would be a new row, cross-run cluster identity
-- would see total membership turnover, and every churn statistic would fire on
-- every theme. Per-(query × item) facts live in research_results (0002), which
-- is the single provenance join (I-1).
--
-- D-7: there is no content_metadata table. Mapped platform fields are columns
-- here; everything the normaliser did not map is kept verbatim in
-- platform_payload so a later schema change can recover it.

create table content_items (
    id                      uuid primary key default gen_random_uuid(),
    platform                text not null references collection_sources (platform),
    platform_item_id        text not null,
    source_url              text not null,
    -- Written by collectors/dedupe.py; nullable because a collector may insert
    -- before canonicalisation runs. ContentItem.url maps to source_url.
    url_canonical           text,
    media_kind              text not null default 'video'
        check (media_kind in ('video', 'image', 'text')),

    -- Mirrors ContentItem.dedupe_key exactly ("<platform>:<platform_item_id>")
    -- so SQL and Python cannot drift on what identity means.
    dedupe_key              text generated always as (platform || ':' || platform_item_id) stored,
    -- Near-duplicate cluster (D-6), assigned by collectors/dedupe.py. Distinct
    -- from dedupe_key: that is exact identity, this is "same content, reposted".
    dedupe_group_id         uuid,

    creator_id              uuid references creators (id),

    -- media
    media_url               text,
    thumbnail_url           text,
    duration_seconds        double precision,

    -- text
    caption                 text,
    hashtags                text[] not null default '{}',
    -- Platform-stated language hint only (TikTok textLanguage). Observed
    -- language claims come from the analysis pass, which anchors them.
    language_hint           text,

    published_at            timestamptz,
    -- Precision is carried separately because a month-precision date must not be
    -- allowed into a 30-day-bucket trend test as if it were a day.
    published_at_precision  text not null default 'unknown'
        check (published_at_precision in ('day', 'month', 'unknown')),

    -- Engagement. Attention, not preference (brief §28). `comments_count` is the
    -- platform counter; `comments` below is the sampled text.
    views                   bigint,
    likes                   bigint,
    comments_count          bigint,
    shares                  bigint,
    saves                   bigint,
    reposts                 bigint,
    engagement_measured_at  timestamptz,
    -- Lifetime counters cannot be differenced into a growth rate: a delta
    -- between two collection dates is an artefact of when we looked. Any trend
    -- code must check this flag before touching the numbers above.
    engagement_is_cumulative boolean not null default true,

    -- Sound. A shared sound is one of the strongest signals that a format is
    -- spreading, so it stays queryable rather than living in platform_payload.
    sound_platform_id       text,
    sound_title             text,
    sound_author            text,
    sound_is_original       boolean,
    sound_url               text,

    -- Sampled comment text, as produced by collectors (list[Comment]). jsonb
    -- because the per-comment shape is provider-dependent and no statistic keys
    -- on it. author_handle is stored for drill-down but must never be sent to
    -- the analysis model.
    comments                jsonb not null default '[]',

    -- Only a location the platform explicitly states. Inferring it from language
    -- or content would be exactly the invented demographic brief §28 forbids.
    stated_location         text,

    -- Platform-stated sponsorship, lifted out of Provenance.extras where the
    -- TikTok and Lazada collectors put it. These are OBSERVED facts and brief
    -- §19/§28 want them preferred over any model inference, which is only
    -- possible if they are columns the gate can read.
    platform_is_sponsored   boolean,
    -- The architecture calls this platform_paid_partnership_label; the live
    -- collectors emit TikTok's `isAd`, so the column is named for the field that
    -- actually fills it.
    platform_is_ad          boolean,
    -- 'platform_declared' today. Kept explicit so a future inferred value can
    -- never be mistaken for a platform declaration.
    sponsorship_source      text,

    -- Organically discovered expansion terms (TikTok relatedSearchWords). Feeds
    -- brief §8D without anyone predefining a theme.
    related_search_terms    text[] not null default '{}',

    -- Immutable, written only on insert: the query that first surfaced this item
    -- (D-3). An index-only denormalisation of research_results; the multi-valued
    -- truth stays there. FK added by 0002, which owns research_queries.
    first_seen_query_id     uuid,
    first_seen_at           timestamptz not null default now(),

    platform_payload        jsonb not null default '{}',
    created_at              timestamptz not null default now(),
    updated_at              timestamptz not null default now(),

    constraint content_items_platform_identity_unique unique (platform, platform_item_id)
);

create index content_items_creator_idx on content_items (creator_id);
-- Nullable until dedupe runs, and a scan over the whole corpus to find one
-- group would defeat the point.
create index content_items_dedupe_group_idx on content_items (dedupe_group_id)
    where dedupe_group_id is not null;
-- Temporal coverage checks and trend windows both filter on dated items only.
create index content_items_published_idx on content_items (platform, published_at)
    where published_at is not null;
create index content_items_hashtags_idx on content_items using gin (hashtags);


-- ─────────────────────────────────────────────────────────────────────────────
-- collection_jobs — the Postgres queue backing aypo/jobs/queue.py
-- ─────────────────────────────────────────────────────────────────────────────
-- Column names and types here are dictated by the already-written queue module,
-- not chosen: it selects and updates `status`, `lease_expires_at`, `run_after`,
-- `claimed_by` and `attempts` by name. Note the divergence from D-42, which
-- calls the state column `state`; the code says `status` and the code is the
-- live contract.
--
-- Terminal jobs are never deleted, because every *_WAIT completion predicate in
-- the research loop is a COUNT over this table and a vanished row reads as
-- "never enqueued".

create table collection_jobs (
    id              uuid primary key default gen_random_uuid(),
    kind            text not null,
    payload         jsonb not null default '{}',
    research_run_id uuid,
    -- D-41: indexed, not unpacked from payload. Marginal-evidence-yield per
    -- iteration is unanswerable if the iteration lives inside a jsonb blob.
    iteration_no    int,
    priority        int not null default 100,
    max_attempts    int not null default 3,
    attempts        int not null default 0,
    dedupe_key      text,
    status          text not null default 'pending'
        check (status in ('pending', 'running', 'succeeded', 'failed', 'cancelled')),
    claimed_by      text,
    started_at      timestamptz,
    finished_at     timestamptz,
    -- NULL while pending. A past value on a running job means the worker died,
    -- which is how claim() reclaims abandoned work.
    lease_expires_at timestamptz,
    -- NULL = runnable now; set by fail() to implement backoff.
    run_after       timestamptz,
    result          jsonb,
    last_error      text,
    created_at      timestamptz not null default now()
);

-- Partial, not total, and the predicate matches enqueue()'s liveness check
-- exactly. A total unique index on dedupe_key would make a permanently-failed
-- job block its own target from ever being re-collected in a later run, which
-- turns one transient actor failure into a permanent corpus hole. Being partial
-- also keeps D-42's `ON CONFLICT (dedupe_key) WHERE ... DO UPDATE ... RETURNING id`
-- available, since ON CONFLICT can name this predicate.
create unique index collection_jobs_dedupe_live_idx on collection_jobs (dedupe_key)
    where dedupe_key is not null and status in ('pending', 'running', 'succeeded');

-- The SKIP LOCKED claim query: it filters on status, then orders by
-- (priority, created_at). Partial so the index stays small as terminal rows
-- accumulate, which they are designed to do.
create index collection_jobs_claim_idx on collection_jobs (priority, created_at)
    where status in ('pending', 'running');
-- Same query with the `kind = ANY(...)` filter the worker passes.
create index collection_jobs_claim_kind_idx on collection_jobs (kind, priority, created_at)
    where status in ('pending', 'running');
-- D-42 item 2: counts_by_state() and cancel() are both (run, iteration, state).
create index collection_jobs_run_iteration_idx
    on collection_jobs (research_run_id, iteration_no, status);


-- ─────────────────────────────────────────────────────────────────────────────
-- collection_coverage — per (research_run_id, platform)
-- ─────────────────────────────────────────────────────────────────────────────
-- I-5: absence is a result, not a blank. This table is what lets the UI render
-- "Lazada PH: unavailable — missing APIFY_TOKEN" instead of a chart with a
-- quietly smaller denominator. It generalises the commerce-only coverage table
-- the commerce design proposed to both channels, so a marketplace really does
-- count as a platform (D-2).

create table collection_coverage (
    id                  uuid primary key default gen_random_uuid(),
    -- FK added by 0002, which owns research_runs.
    research_run_id     uuid not null,
    platform            text not null references collection_sources (platform),

    available           boolean not null default true,
    -- Required exactly when unavailable: an unexplained gap is the failure mode
    -- this table exists to prevent.
    unavailable_reason  text,

    queries_run         int not null default 0,
    items_discovered    int not null default 0,
    items_admitted      int not null default 0,
    items_quarantined   int not null default 0,

    -- Units, not items: one provider call can cost several units, and the budget
    -- guard spends in units.
    planned_units       int not null default 0,
    achieved_units      int not null default 0,
    -- Derived so the disclosure line on every read (§5.5) is one column lookup
    -- and cannot be computed two different ways in two different places.
    unmet_units         int generated always as (greatest(planned_units - achieved_units, 0)) stored,

    -- Why the plan was not met. A closed vocabulary because it is rendered as a
    -- reason code, and free text cannot be rendered consistently or counted.
    shortfall_cause     text check (shortfall_cause in (
        'none',
        'source_unavailable',        -- credentials absent or provider dead
        'provider_budget_exhausted', -- the $5-class cap, hit before the plan finished
        'run_budget_exhausted',
        'rate_limited',
        'low_yield',                 -- queries returned results, almost none admitted
        'date_range_unsupported',    -- the temporal stratum could not be requested
        'provider_error',
        'cancelled'
    )),

    collection_started_at timestamptz,
    collection_ended_at   timestamptz,
    updated_at            timestamptz not null default now(),

    constraint collection_coverage_run_platform_unique unique (research_run_id, platform),
    constraint collection_coverage_reason_required check (
        available or unavailable_reason is not null
    )
);

create index collection_coverage_run_idx on collection_coverage (research_run_id);


-- ═════════════════════════════════════════════════════════════════════════════
-- DEFERRED — recorded so the gap is visible rather than lost
-- ═════════════════════════════════════════════════════════════════════════════
--
-- Tables in ARCHITECTURE §2 that 0001 does not create (they belong to later
-- migrations and are listed here only where 0001 leaves a dangling reference):
--   * research_queries, research_runs (§2.3, 0002_research) — content_items
--     .first_seen_query_id, collection_jobs.research_run_id and
--     collection_coverage.research_run_id are plain uuid columns with no FK.
--     0002 must attach those constraints; D-43 forbids editing this file to do
--     it, and D-44's deferred `alter table … add constraint` is the pattern.
--   * research_strategy enum (§1.1 D-1) — created by 0002, its only consumer.
--     Not duplicated here.
--
-- Columns deliberately omitted from tables this file does create:
--
--   collection_sources (§2.2)
--     * No per-source rate-limit, cost-per-unit or circuit-breaker state. The
--       circuit breaker (§7.1) is in-process for the MVP; persisting it is only
--       needed once more than one worker host exists.
--
--   creators (§2.2, §6.4)
--     * No cached creator_share / deff / n_eff aggregates. §6.4 computes n_eff
--       per claim over the evidence set; a cached per-creator number would be a
--       second definition of the same quantity and I-4 forbids a single number
--       per entity in the schema.
--
--   content_items (§4.1, §2.2)
--     * overlay_text — the architecture's §4.1 contract has it, but it is
--       produced by the OCR step of the analysis pass (0004), not by any
--       collector, and the live models/content.py ContentItem has no such field.
--       It belongs on content_analysis.
--     * collection_provider, collection_method, discovery_query, raw_rank,
--       sort_order, page_depth, discovered_at, relevance_score — the §4.1
--       contract carries these on the in-memory object, but I-1 makes
--       research_results (0002) their authoritative home. Duplicating them here
--       would create a second, silently divergent provenance record.
--     * media_expires_at / re-resolution bookkeeping (§4.1 note that media_url
--       may expire). Retrieval re-resolves from source_url via yt-dlp
--       (ENVIRONMENT.md), so a cached expiry is not on the critical path.
--     * No per-item embedding column: content_embeddings (§2.8, 0007) owns
--       vectors for both channels via item_kind (D-C2).
--
--   collection_jobs (§4.7, D-42)
--     * No separate `state` column: the live queue module uses `status`
--       (divergence from D-42's prose, resolved in favour of the code).
--     * No worker-host heartbeat table; heartbeat() extends lease_expires_at in
--       place, which is sufficient for a single-host deployment.
--
--   collection_coverage (§2.2, §5.2 mechanism 8)
--     * Per-stratum temporal planned-vs-achieved rows, including the
--       `published_at IS NULL` share. §5.2 puts the monthly temporal strata
--       here; a run-level row cannot hold a per-month breakdown, so that needs
--       a child table (collection_coverage_strata) created alongside
--       research/allocation.py. Until it exists, `temporal_coverage` on
--       research_runs (0002) carries the single gating flag and growth/decline
--       claims stay blocked by it.
--     * garbage_rate / provider-quarantine counters (§7.1 garbage gate) — the
--       gate runs in-process per run for the MVP.
--
-- Known divergence from the architecture, kept deliberately:
--   * models/provenance.py Platform spells the marketplaces `shopee` / `lazada`
--     and has no `web`/`websearch` split, while D-2's canonical vocabulary
--     (seeded above) uses `shopee_ph` / `lazada_ph` / `web`. The pydantic enum
--     is the live contract for 104 passing tests and is not changed here;
--     collection_sources.capability_key carries the mapping, and the collection
--     layer must apply it before writing a platform value into any column with
--     the FK above.
