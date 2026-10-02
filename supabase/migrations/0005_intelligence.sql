-- ============================================================================
-- 0005_intelligence.sql
--
-- Two subsystems that share one migration because they share one dependency
-- chain: the open label vocabulary (ARCHITECTURE §2.6) and cluster identity +
-- honest trends (ARCHITECTURE §2.8). Labels are read off items; clusters are
-- read off embeddings of those same items; trends are read off clusters. A
-- later wave that needs them split can split the FILES, not the keys.
--
-- Scope is deliberately narrower than ARCHITECTURE's 92 tables (brief §37:
-- "do not over-engineer"; §38: end-to-end flow first). The deferral register
-- at the bottom of this file names every table and column left out and the
-- architecture section it came from, so the gap is recorded rather than lost.
--
-- Canonical decisions honoured: D-1, D-2, D-6, D-8, D-9, D-11, D-12, D-13,
-- D-14, D-17, D-18, D-23, D-30, D-32, D-41, D-43, D-44.
-- ============================================================================

create extension if not exists vector;


-- ============================================================================
-- PART A — OPEN LABEL VOCABULARY  (ARCHITECTURE §2.6, design doc 02)
-- ============================================================================

-- A facet names a KIND OF QUESTION ("in what situation?", "what register of
-- language?"), never an answer, which is why a closed facet set does not
-- violate brief §1. The values are open; only the questions are fixed.
--
-- A LOOKUP TABLE, NEVER AN ENUM. Brief §19 says the discovered vocabulary may
-- grow and that recurring labels may later become taxonomy entries, so adding
-- or retiring a facet must be an INSERT, not an ALTER TYPE under lock.
create table label_facets (
    facet                 text primary key,
    display_name          text     not null,
    -- Shown to the extractor. Must never name a value, a brand, a category,
    -- an occasion or a market (brief §1, §34).
    question              text     not null,
    -- Posture group, not a per-facet threshold. The numeric thresholds live in
    -- canonicalisation_config (deferred) so there is exactly one source of
    -- truth for them; doc 02 §1.4 removed them from this table for that reason.
    merge_posture         text     not null
        check (merge_posture in ('liberal', 'moderate', 'conservative',
                                 'very_conservative', 'lexical')),
    -- true => never merge two forms on embedding similarity alone. For facets
    -- where the token IS the object of study, synonyms are distinct items.
    lexical_gate_required boolean  not null default false,
    -- false => the facet is rendered as explicitly EMPTY rather than silently
    -- missing, so "we looked and found nothing" stays distinguishable from
    -- "we never asked" (brief §28).
    accepts_emissions     boolean  not null default true,
    max_suggestions       smallint not null default 12,
    sort_order            smallint not null
);

-- Seeded from aypo/analysis/schema.py::Facet, which is the live extractor
-- contract and already has 104 passing tests against it.
--
-- DIVERGENCE FROM ARCHITECTURE D-13, deliberate: D-13 specifies 15 facets with
-- British spellings (`behaviour`, `humour`) and three facets the live schema
-- does not emit (`narrative`, `audience_signal`, `commercial_signal`). The
-- live enum has 18 values with American spellings and five the architecture
-- lacks (`language`, `sentiment`, `purchase_intent`, `stated_use`,
-- `format_feature`, `brand_co_appearance`). Per the standing rule, EXISTING
-- CODE WINS: renaming a facet here would silently orphan every emission the
-- extractor produces. The three architecture-only facets are listed in the
-- deferral register; they can be seeded with accepts_emissions = false the day
-- the extractor grows them, with no DDL change.
insert into label_facets
    (facet, display_name, question, merge_posture, lexical_gate_required, sort_order)
values
    -- stable core (brief §19A) — expected on most items
    ('language',            'Language',
     'Which languages or code-switching patterns are used?',          'moderate',          false,  1),
    ('content_format',      'Content format',
     'How is this piece of content structured or staged?',            'liberal',           false,  2),
    ('creator_archetype',   'Creator archetype',
     'What kind of creator does this appear to be, from the material only?', 'moderate',    false,  3),
    ('sentiment',           'Sentiment',
     'What attitude or tone is expressed?',                           'moderate',          false,  4),
    ('purchase_intent',     'Purchase intent',
     'Is any intent to buy, avoid or recommend expressed?',           'conservative',      false,  5),
    -- open-ended discovered (brief §19B)
    ('occasion',            'Occasion',
     'On what kind of occasion is this happening?',                   'liberal',           false,  6),
    ('behavior',            'Behaviour',
     'What is the person doing with the product or category?',        'liberal',           false,  7),
    ('social_context',      'Social context',
     'Who is present and what is the social situation?',              'liberal',           false,  8),
    ('cultural_signal',     'Cultural signal',
     'What cultural reference or practice is invoked?',               'very_conservative', false,  9),
    ('slang',               'Slang',
     'What informal or in-group terms are used?',                     'lexical',           true,  10),
    ('humor_signal',        'Humour',
     'What kind of humour is being used?',                            'conservative',      false, 11),
    ('activity',            'Activity',
     'What activity is taking place?',                                'liberal',           false, 12),
    ('product_pairing',     'Product pairing',
     'What is this product shown or described together with?',        'very_conservative', true,  13),
    ('setting',             'Setting',
     'Where does this appear to take place?',                         'liberal',           false, 14),
    ('stated_use',          'Stated use',
     'What use is explicitly stated, as opposed to shown?',           'moderate',          false, 15),
    ('format_feature',      'Format feature',
     'What recurring production or editing device is used?',          'liberal',           false, 16),
    ('brand_co_appearance', 'Brand co-appearance',
     'Which brands appear alongside one another?',                    'very_conservative', true,  17),
    -- the mandatory escape hatch: an interesting observation that fits no
    -- facet is emitted here rather than bent into the nearest one. Facet
    -- mis-assignment is recoverable; a discarded observation is not.
    ('unexpected',          'Unexpected observation',
     'What did you notice that none of the other questions asked for?', 'very_conservative', false, 18);


-- Label lifecycle. An ENUM is correct here and a lookup table is not: these
-- are states the CODE branches on, not vocabulary the data may grow.
-- Deliberately declared complete, including the states only the deferred
-- canonicaliser writes, so adding merging later is an INSERT and not an
-- ALTER TYPE under lock on a large table.
create type label_status as enum (
    'candidate',           -- observed, below the promotion gate
    'emerging_candidate',  -- weak-signal tier
    'canonical',           -- promoted taxonomy entry
    'alias',               -- merged into canonical_id
    'pending_merge',       -- in the review band, awaiting adjudication
    'blocked'              -- human-blocked (degenerate / PII / junk)
);


-- ----------------------------------------------------------------------------
-- A1. EMISSION LOG — append-only, the source of truth
-- ----------------------------------------------------------------------------
-- The single most common way to get an open vocabulary wrong is one row per
-- concept whose text is rewritten as understanding improves. That destroys the
-- model's original output, which brief §28 ("preserve provenance") forbids and
-- which the brief §27 evidence drill-down depends on. So: this table is
-- append-only, and EVERY derived value lives in observation_label_map keyed by
-- canon_version. Re-canonicalising with different thresholds rebuilds the
-- derived tier and never touches an emission.
create table label_observations (
    id                  uuid primary key default gen_random_uuid(),
    run_id              uuid        not null references research_runs (id),

    -- The analysis unit. Exactly one, so support_count's DISTINCT is well
    -- defined.
    content_item_id     uuid            null references content_items (id),
    commerce_item_id    uuid            null references commerce_items (id),

    -- D-18: review labels are NOT a second table. Two tables would have
    -- guaranteed two vocabularies. Exactly one of these identifies the pass
    -- that emitted the label.
    content_analysis_id uuid            null references content_analysis (id),
    commerce_review_id  uuid            null references commerce_reviews (id),

    source_kind         text        not null
        check (source_kind in ('video', 'post', 'commerce_listing',
                               'commerce_review', 'web_page')),
    facet               text        not null references label_facets (facet),

    -- VERBATIM model output. Never normalised, never corrected, never deleted.
    -- Normalisation is a function of a pipeline version, so it belongs in the
    -- rebuildable tier, not here.
    surface_form        text        not null
        check (length(btrim(surface_form)) between 1 and 160),
    -- REQUIRED (D-14). A label in the content's own language is unusable to a
    -- reader who does not speak it, and unmergeable across languages without a
    -- shared pivot. Making the gloss optional made both failures silent.
    gloss_en            text        not null
        check (length(btrim(gloss_en)) between 1 and 400),

    -- From aypo/analysis/schema.py::Label. `basis` and `cue` are not in the
    -- architecture's column set but are in the live contract, and they are what
    -- makes brief §3's observation/interpretation split checkable: `cue` is the
    -- concrete thing a second person could go to the anchor and fail to find.
    basis               text        not null
        check (basis in ('seen', 'heard', 'written', 'inferred')),
    cue                 text        not null,
    -- Raw anchor strings in the parse_anchor() micro-format. Kept whole rather
    -- than only anchors[0], because the drill-down shows every anchor.
    anchors             text[]      not null default '{}',

    -- The denormalised first anchor, for reads that must not parse an array.
    evidence_quote      text            null,
    evidence_timecode_s real            null
        check (evidence_timecode_s is null or evidence_timecode_s >= 0),

    -- Monotonic within the run: the pin that makes a rebuild reproducible. A
    -- rebuild is defined at an as_of_seq, not at a wall-clock time.
    observation_seq     bigint      not null,

    extractor_version   text        not null,
    prompt_version      text        not null,
    created_at          timestamptz not null default now(),

    constraint label_observations_one_unit
        check ((content_item_id is not null) <> (commerce_item_id is not null)),
    constraint label_observations_one_source
        check ((content_analysis_id is not null) <> (commerce_review_id is not null))
);

-- Discovery strategy is deliberately NOT a column here. An item is discovered
-- by one or more (query, platform) pairs in research_results, and collapsing
-- that to one value per item understates strategy diversity for any item two
-- query kinds found (D-3). The §4.1 diversity gates join research_results.

create index label_observations_run_facet on label_observations (run_id, facet);
create index label_observations_content   on label_observations (content_item_id)
    where content_item_id is not null;
create unique index label_observations_seq on label_observations (run_id, observation_seq);

-- Append-only by TRIGGER, never by RULE (D-44). A rule fails SILENTLY — a buggy
-- UPDATE reports success and changes nothing, which is the worst available
-- behaviour for the system's source of truth — and Postgres rejects
-- INSERT ... ON CONFLICT on any table carrying one, foreclosing idempotent
-- re-ingest of a retried analysis.
create or replace function label_observations_immutable() returns trigger
language plpgsql as $$
begin
    raise exception 'label_observations is append-only (attempted %)', tg_op
        using errcode = 'restrict_violation';
end; $$;

create trigger label_observations_no_mutate
    before update or delete or truncate on label_observations
    for each statement execute function label_observations_immutable();


-- ----------------------------------------------------------------------------
-- A2. LEXICAL IDENTITY — one row per distinct normalised form
-- ----------------------------------------------------------------------------
create table labels (
    id                 uuid        primary key default gen_random_uuid(),
    -- The generation this row belongs to. Every count below is a function of
    -- it (more aggressive merging raises support), so NO support figure may be
    -- rendered, exported or compared without its canon_version.
    canon_version      integer     not null,
    facet              text        not null references label_facets (facet),
    -- The canonicalisation partition key. Plain text, not an FK: the
    -- lang_buckets lookup is deferred (see the register). It is NOT an enum —
    -- a closed language list is a market assumption (brief §1).
    lang_bucket        text        not null,
    normalised_form    text        not null,  -- immutable identity within a canon_version
    display_form       text        not null,  -- most frequent raw surface form
    gloss_en           text            null,  -- modal gloss across observations

    status             label_status not null default 'candidate',
    -- An alias keeps its own row and its own observations; the merge is one
    -- nullable column, so un-merging is nulling it. Resolution everywhere is
    -- the one-hop coalesce(canonical_id, id).
    canonical_id       uuid            null references labels (id),

    embedding          vector(1536)    null,  -- 1536: pgvector HNSW caps at 2000
    embed_provider     text            null,  -- e.g. 'openai:text-embedding-3-large'

    -- ── cached counters: A CACHE ONLY. The authoritative queries are joins
    -- over observation_label_map; these exist so a facet listing is one scan.
    -- counters_at says how stale the cache is, so a stale number is visibly
    -- stale rather than quietly wrong.
    support_count      integer     not null default 0,  -- DISTINCT analysis units
    distinct_content   integer     not null default 0,  -- DISTINCT content_item_id
    -- The concentration counters exist because source concentration must be
    -- PENALISED, not ignored (brief §28). A label with support 40 from one
    -- creator, or found only by one query, is one source repeated — not forty
    -- independent observations of a culture, and the promotion gate must be
    -- able to see the difference.
    distinct_creators  integer     not null default 0,
    distinct_platforms smallint    not null default 0,
    distinct_queries   integer     not null default 0,
    top_creator_share  real        not null default 0,
    top_query_share    real        not null default 0,
    counters_at        timestamptz     null,

    first_seen_at      timestamptz not null default now(),
    last_seen_at       timestamptz not null default now(),

    unique (canon_version, facet, lang_bucket, normalised_form),
    constraint labels_no_self_alias   check (canonical_id is null or canonical_id <> id),
    constraint labels_alias_iff_canon check ((status = 'alias') = (canonical_id is not null))
);

create index labels_facet_status on labels (canon_version, facet, status, support_count desc);
create index labels_canonical    on labels (canonical_id) where canonical_id is not null;
-- Serves the semantic READ path only. Canonicalisation must NOT use it: an
-- approximate index is order-dependent and post-filters, which would make the
-- rebuild non-reproducible and silently lose recall. Those scans are exact.
create index labels_hnsw on labels
    using hnsw (embedding vector_cosine_ops) with (m = 16, ef_construction = 64);


-- ----------------------------------------------------------------------------
-- A3. ASSIGNMENT — derived, truncate-and-rebuild per canon_version
-- ----------------------------------------------------------------------------
-- This tier is the whole reason re-canonicalisation is safe. normalised_form,
-- lang_bucket and label_id are outputs of a pipeline version and of a
-- corpus-derived affinity table, so recalibration must be able to RECOMPUTE
-- them — impossible on an append-only table. Rebuild order is: delete this
-- table for the canon_version, then rebuild labels for it.
create table observation_label_map (
    observation_id  uuid    not null references label_observations (id),
    canon_version   integer not null,
    normalised_form text    not null,
    norm_steps      jsonb   not null default '[]'::jsonb,  -- which steps fired, for audit
    lang_bucket     text    not null,
    lang_method     text    not null,  -- how the bucket was decided, not what it is
    label_id        uuid    not null references labels (id),
    primary key (observation_id, canon_version)
);

create index observation_label_map_label on observation_label_map (canon_version, label_id);


-- ============================================================================
-- PART B — EMBEDDINGS, CLUSTER IDENTITY AND TRENDS
--          (ARCHITECTURE §2.8, design doc 04)
-- ============================================================================

create type cluster_corpus     as enum ('social', 'commerce');
create type cluster_space      as enum ('global', 'situation', 'expression',
                                        'language_culture', 'product_context');
create type cluster_status     as enum ('active', 'provisional', 'unlabelled', 'review',
                                        'dormant', 'split', 'merged', 'retired');
create type cluster_birth_kind as enum ('birth', 'split', 'merge', 'resurrect', 'epoch_break');
create type cluster_death_kind as enum ('dissolved', 'absorbed', 'corpus_churn', 'scattered');
create type lineage_relation   as enum ('continuation', 'split', 'merge', 'birth', 'resurrect',
                                        'absorbed', 'dissolved', 'epoch_break',
                                        'suspected_continuation', 'rollup_child');
create type membership_role    as enum ('core', 'secondary', 'noise');
create type trend_class        as enum ('established', 'growing', 'emerging', 'weak_signal',
                                        'stable', 'declining', 'insufficient_data');
create type replication_status as enum ('exploratory', 'unreplicated', 'replicated',
                                        'contradicted');
create type period_kind        as enum ('month', 'quarter');
create type run_status         as enum ('ok', 'degraded', 'preview', 'failed');
-- reference = the frozen (brand + category) query set; category = category-only,
-- which is the stratum a brand comparison must use; widened = plus theme and
-- hypothesis queries, which are theme-conditioned and therefore cannot carry a
-- temporal claim on their own.
create type stratum_kind       as enum ('reference', 'category', 'widened');

-- NOTE: doc 04's `discovery_strategy` enum is NOT created here. D-1 deletes it
-- as a duplicate; every reference uses `research_strategy` from 0002.


-- ----------------------------------------------------------------------------
-- B1. content_embeddings — both channels, one table
-- ----------------------------------------------------------------------------
-- D-C2: commerce vectors live here too, discriminated by item_kind, rather than
-- in a parallel commerce_item_embeddings. One table is what makes a
-- social↔commerce neighbour query expressible at all (brief §17).
create table content_embeddings (
    id                   uuid primary key default gen_random_uuid(),
    item_kind            text not null check (item_kind in ('content', 'commerce')),
    content_item_id      uuid references content_items (id)  on delete cascade,
    commerce_item_id     uuid references commerce_items (id) on delete cascade,

    -- theme_v1 | facet_*_v1 | retrieval_v1 | commerce_theme_v1
    view                 text not null,
    cad_template_version text not null,
    -- The Cluster Analysis Document: the rendered text that was embedded. Kept
    -- verbatim because a cluster is only interpretable if you can read what was
    -- actually compared, and because a template change must be detectable as a
    -- diff rather than inferred from drifting cosines.
    cad_text             text not null,
    cad_token_count      int  not null,
    -- D-23: a value enters a CAD iff its claim_status = 'published'. This counts
    -- what was EXCLUDED, so a CAD that is thin because the item was unverifiable
    -- is distinguishable from one that is thin because the item was dull.
    cad_dropped_low_confidence int    not null default 0,
    cad_truncated_fields       text[] not null default '{}',
    content_hash         text not null,  -- sha256(view || template_version || cad_text)

    embedding_provider   text not null,
    embedding_model      text not null,
    embedding_dims       int  not null check (embedding_dims between 1 and 2000),
    -- Changing the model, the dimension or the CAD template makes old and new
    -- vectors incomparable. The epoch is how a cluster identity knows not to
    -- match across that break and report the instrument change as growth.
    identity_epoch       text not null,
    -- L2-NORMALISED ON WRITE, so cosine and inner product agree and the HNSW
    -- distance operator below is the one the clustering code reasons about.
    embedding            vector(1536) not null,

    created_at           timestamptz not null default now(),

    constraint content_embeddings_one_item check (
        (item_kind = 'content'  and content_item_id is not null and commerce_item_id is null) or
        (item_kind = 'commerce' and commerce_item_id is not null and content_item_id is null))
);

-- NOT unique (D-44). Two DIFFERENT items routinely render byte-identical CADs
-- when their analyses are sparse, and a unique index rejects the second item
-- outright. This is a CACHE KEY; per-item uniqueness is the next index, and
-- identical CADs are resolved by the D-6 near-duplicate rule, not by a
-- constraint violation.
create index content_embeddings_cache
    on content_embeddings (content_hash, embedding_model, embedding_dims);
create unique index content_embeddings_item_view
    on content_embeddings (item_kind, coalesce(content_item_id, commerce_item_id), view,
                           cad_template_version, embedding_model);
-- PARTIAL ON PURPOSE. An unfiltered HNSW index spans every view; a retrieval
-- query filtering view = 'retrieval_v1' would post-filter roughly one in seven
-- returned neighbours and recall would collapse. Clustering never uses this
-- index — it reads vectors in bulk into numpy — so the retrieval view is the
-- only one that needs one.
create index content_embeddings_retrieval_ann
    on content_embeddings using hnsw (embedding vector_cosine_ops)
    with (m = 16, ef_construction = 64)
    where view = 'retrieval_v1';
create index content_embeddings_view on content_embeddings (view, identity_epoch);


-- ----------------------------------------------------------------------------
-- B2. cluster_runs — one clustering pass over one space
-- ----------------------------------------------------------------------------
create table cluster_runs (
    id                   uuid primary key default gen_random_uuid(),
    -- RESTRICT, not CASCADE (D-44). With CASCADE, deleting a research_run
    -- cascades here, and clusters.first_run_id (no action) then blocks the
    -- delete with a foreign_key_violation anyway. Cluster identities are
    -- deliberately longer-lived than any one run: retire a run, never delete it.
    research_run_id      uuid not null references research_runs (id) on delete restrict,
    -- D-8: clustering is re-run as the corpus grows within a run, so the key is
    -- per (run × space × iteration), not per (run × space).
    iteration_no         int  not null default 0,
    corpus               cluster_corpus not null,
    space                cluster_space  not null,
    -- NOT a generated column (D-44). `generated always as (corpus::text || ...)`
    -- fails with "generation expression is not immutable", because the
    -- enum-to-text cast is an I/O conversion Postgres treats as stable. A plain
    -- column plus a CHECK gives the same guarantee and does compile.
    space_key            text not null,
    constraint cluster_runs_space_key_matches
        check (space_key = corpus::text || '.' || space::text),

    identity_epoch       text not null,
    embedding_provider   text not null,
    embedding_model      text not null,
    embedding_dims       int  not null,
    cad_template_version text not null,

    n_items_analysed     int not null,
    n_items_embedded     int not null,
    n_items_clustered    int not null,   -- after the near-duplicate collapse
    -- Derived, so it cannot drift from its inputs.
    embedding_coverage   numeric generated always as
        (n_items_embedded::numeric / nullif(n_items_analysed, 0)) stored,
    n_clusters           int not null,
    -- Two noise fractions, and they are not interchangeable. The PRIMARY one is
    -- the first HDBSCAN pass and is what high_noise is judged on; the other is
    -- after noise recovery and is what a reader is shown. Reporting only the
    -- second would hide a degenerate clustering behind a repair pass.
    noise_fraction_primary numeric not null,
    noise_fraction         numeric not null,
    high_noise             boolean not null default false,

    space_config         jsonb not null,  -- reducer, dims, explained var, fingerprint
    space_stats          jsonb not null,  -- nn_dist quantiles, eps used, dedup threshold
    hdbscan_params       jsonb not null,
    identity_config      jsonb not null,  -- cos weight, tau_match, tau_ambiguous

    lib_versions         jsonb  not null,  -- numpy / sklearn / scipy / umap
    -- sha256 of the ORDERED input item ids. Two runs with the same digest, seed
    -- and params must produce the same clustering; without it, "the clusters
    -- changed" is indistinguishable from "the corpus changed".
    input_digest         text   not null,
    permutation_seed     bigint not null,

    status               run_status not null default 'ok',
    degraded_reason      text,
    created_at           timestamptz not null default now()
);

create unique index cluster_runs_one_per_space
    on cluster_runs (research_run_id, corpus, space, iteration_no);
create index cluster_runs_epoch on cluster_runs (identity_epoch, space_key, created_at desc);


-- ----------------------------------------------------------------------------
-- B3. clusters — THE STABLE IDENTITY (D-9)
-- ----------------------------------------------------------------------------
-- Clusters are recomputed every time the corpus grows. HDBSCAN's integer labels
-- are not stable across runs, so without an identity layer the UI would report
-- re-clustering artefacts as growth and decline. This table is the identity;
-- cluster_revisions below is the per-run snapshot of it.
create table clusters (
    id                 uuid primary key default gen_random_uuid(),
    -- Derived, not random: space_key || '.' || sha256(sorted first-revision
    -- member ids)[:8]. A human-safe reference that survives relabelling, so a
    -- finding written in run 3 still points somewhere in run 9.
    cluster_key        text not null,
    space_key          text not null,
    corpus             cluster_corpus not null,
    space              cluster_space  not null,
    identity_epoch     text not null,

    -- ── label: always produced AFTER the members were inspected (brief §21) ──
    status             cluster_status not null default 'active',
    label              text,
    description        text,
    label_confidence   text check (label_confidence in ('low', 'medium', 'high')),
    label_run_id       uuid references cluster_runs (id),
    -- D-12: a "theme" is a read-model, not a table. These two are written at
    -- labelling so ThemeRef is a projection rather than a recomputation.
    discriminative_terms   jsonb  not null default '[]'::jsonb,
    representative_snippets text[] not null default '{}',

    -- ── identity and lineage ────────────────────────────────────────────────
    birth_kind         cluster_birth_kind not null default 'birth',
    death_kind         cluster_death_kind,
    first_run_id       uuid not null references cluster_runs (id),
    last_run_id        uuid not null references cluster_runs (id),
    current_revision_id uuid,  -- FK added after cluster_revisions exists
    suspected_continuation_of uuid references clusters (id),
    parent_cluster_id  uuid references clusters (id),  -- roll-up parent
    is_rollup          boolean not null default false,
    provisional        boolean not null default false, -- recovered from noise
    trend_eligible     boolean not null default true,

    -- ── geometry: matching happens in 1536-d, never in the reduced space ────
    centroid_1536      vector(1536),
    inlier_radius_cos  numeric,
    mean_intra_cos     numeric,

    -- ── descriptive stats over CORE members only ────────────────────────────
    -- size_core is all strata and is the browse count. size_core_reference is
    -- the reference stratum and is the ONLY one a statistic, gate or guard may
    -- use: theme-level queries are theme-conditioned, so counting them would
    -- let the research loop manufacture the growth it went looking for.
    size_core          int not null default 0,
    size_core_reference int not null default 0,
    size_secondary     int not null default 0,
    undated_members    int not null default 0,
    first_observed_pub  date,
    latest_observed_pub date,
    max_creator_share  numeric,
    n_creator_blocks   int,
    -- Keyed by brand_id. A column named after the subject brand would hard-code
    -- the research subject into the schema (brief §34) and break on the second
    -- brand studied.
    brand_presence     jsonb not null default '{}'::jsonb,
    -- Medians and IQRs, NEVER means. Engagement is heavy-tailed; a mean is one
    -- viral item wearing a statistic's clothes.
    engagement_stats   jsonb not null default '{}'::jsonb,
    platform_breakdown jsonb not null default '{}'::jsonb,
    strategy_breakdown jsonb not null default '{}'::jsonb,

    -- ── current verdict, denormalised for the UI; the audit trail is in
    --    theme_trend_tests and is what a drill-down reads.
    current_classification trend_class not null default 'insufficient_data',
    current_replication    replication_status,
    current_refusal_reason text,
    series_unstable        boolean not null default false,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),

    constraint clusters_space_key_matches
        check (space_key = corpus::text || '.' || space::text),
    constraint clusters_size_core_reference_le_core
        check (size_core_reference <= size_core),
    -- A roll-up is an aggregate of other clusters; trending it double-counts
    -- its children.
    constraint clusters_rollup_not_trendable
        check (not (is_rollup and trend_eligible)),
    -- A cluster recovered from noise carries no direction of any kind,
    -- including 'emerging' — it was assembled from the points the clusterer
    -- could not place.
    constraint clusters_provisional_not_directional
        check (not (provisional and current_classification
                    in ('established', 'growing', 'declining', 'emerging')))
);

create unique index clusters_key on clusters (cluster_key);
create index clusters_space  on clusters (space_key, identity_epoch, status);
create index clusters_active on clusters (space_key, status) where status = 'active';
-- Serves cross-run identity matching, which scans last run's centroids for each
-- of this run's.
create index clusters_centroid_ann
    on clusters using hnsw (centroid_1536 vector_cosine_ops);


-- ----------------------------------------------------------------------------
-- B4. cluster_revisions — the per-run snapshot and the matching evidence
-- ----------------------------------------------------------------------------
create table cluster_revisions (
    id                 uuid not null primary key default gen_random_uuid(),
    cluster_id         uuid not null references clusters (id)     on delete cascade,
    run_id             uuid not null references cluster_runs (id) on delete cascade,

    -- Stored and NEVER joined on. It exists so a human debugging a lineage
    -- decision can reproduce it; a view that joined on it would reintroduce
    -- exactly the cross-run instability this table exists to absorb.
    raw_hdbscan_label  int,
    size_core          int not null,
    size_secondary     int not null default 0,
    centroid_1536      vector(1536) not null,
    inlier_radius_cos  numeric not null,
    mean_intra_cos     numeric,
    persistence        numeric,
    medoid_item_id     uuid,
    exemplar_item_ids  uuid[] not null default '{}',

    -- ── cross-run identity evidence (D-9) ───────────────────────────────────
    -- score = 0.6 * cos(centroid, prev centroid) + 0.4 * jaccard(members).
    -- Continuation requires score >= 0.72 AND (cos >= 0.85 OR jaccard >= 0.30);
    -- [0.60, 0.72) is ambiguous and gets a new id with suspected_continuation_of
    -- set; below 0.60 is a birth. Both components are STORED, not just the
    -- score, because a verdict driven entirely by centroid drift and one driven
    -- entirely by membership overlap mean different things and a borderline
    -- call has to be re-judgeable without re-running the pass.
    matched_prev_cluster_id uuid references clusters (id),
    match_score        numeric,
    match_cos          numeric,
    match_jaccard      numeric,
    match_decision     text,  -- continuation | ambiguous | birth | split | merge | budding
    member_jaccard_vs_prev numeric,

    -- If this run's series disagrees with last run's over the shared periods,
    -- the series is an artefact of re-clustering and no growth claim may be made
    -- from it, however clean the trend test looks.
    series_stability_rho numeric,
    series_stability_mae numeric,
    series_unstable      boolean not null default false,

    created_at timestamptz not null default now()
);

create unique index cluster_revisions_one_per_run on cluster_revisions (cluster_id, run_id);
create index cluster_revisions_run on cluster_revisions (run_id);

alter table clusters
    add constraint clusters_current_revision_fk
    foreign key (current_revision_id) references cluster_revisions (id) on delete set null;


-- ----------------------------------------------------------------------------
-- B5. cluster_lineage — birth, death, split, merge
-- ----------------------------------------------------------------------------
-- A birth has a null parent; a dissolution has a null child. Both are rows, so
-- the graph is complete and a theme's full story — including the run in which
-- it stopped existing, and why — is renderable instead of inferred from absence.
create table cluster_lineage (
    id                uuid primary key default gen_random_uuid(),
    run_id            uuid not null references cluster_runs (id) on delete cascade,
    parent_cluster_id uuid references clusters (id) on delete cascade,
    child_cluster_id  uuid references clusters (id) on delete cascade,
    relation          lineage_relation not null,
    weight            numeric,  -- share of the parent's surviving members in the child
    match_score       numeric,
    match_cos         numeric,
    match_jaccard     numeric,
    n_parent_members_surviving int,
    n_shared_members  int,
    note              text,
    created_at        timestamptz not null default now(),

    constraint cluster_lineage_endpoints
        check (parent_cluster_id is not null or child_cluster_id is not null),
    constraint cluster_lineage_no_self
        check (parent_cluster_id is distinct from child_cluster_id)
);

create index cluster_lineage_child  on cluster_lineage (child_cluster_id);
create index cluster_lineage_parent on cluster_lineage (parent_cluster_id);
-- The all-zero uuid stands in for "no endpoint" so a birth and a dissolution in
-- the same run cannot each be written twice.
create unique index cluster_lineage_unique on cluster_lineage (
    run_id,
    coalesce(parent_cluster_id, '00000000-0000-0000-0000-000000000000'::uuid),
    coalesce(child_cluster_id,  '00000000-0000-0000-0000-000000000000'::uuid),
    relation);


-- ----------------------------------------------------------------------------
-- B6. cluster_members
-- ----------------------------------------------------------------------------
create table cluster_members (
    id                  uuid primary key default gen_random_uuid(),
    cluster_id          uuid references clusters (id) on delete cascade,  -- null for noise
    cluster_revision_id uuid references cluster_revisions (id) on delete cascade,
    run_id              uuid not null references cluster_runs (id) on delete cascade,

    item_kind           text not null check (item_kind in ('content', 'commerce')),
    content_item_id     uuid references content_items (id)  on delete cascade,
    commerce_item_id    uuid references commerce_items (id) on delete cascade,

    membership_role     membership_role not null,
    membership_weight   numeric,  -- 1.0 for core; w in (0,1] for a secondary
    hdbscan_probability numeric,
    outlier_score       numeric,
    dist_to_centroid_cos numeric,
    is_exemplar         boolean not null default false,
    is_medoid           boolean not null default false,

    -- ── denormalised so series, stratification and the robustness checks are
    --    index-only scans. These are copies; research_results and content_items
    --    remain authoritative.
    publication_date    date,
    -- First day of the MARKET-TIMEZONE bucket (D-30). Not UTC: a UTC truncation
    -- moves items across period boundaries by up to a day in a way that
    -- correlates with posting time of day, which is itself platform-dependent.
    publication_period  date,
    date_precision      text check (date_precision in ('day', 'month', 'unknown')),
    platform            text,
    -- The permutation block and the independence unit for all social evidence:
    -- forty videos by one creator are not forty independent observations.
    creator_id          uuid references creators (id) on delete set null,
    -- D-1/D-3: the strategy of the item's first_seen_query_id, denormalised for
    -- index-only stratum filters. research_strategy is owned by 0002; doc 04's
    -- duplicate discovery_strategy enum is deleted.
    discovery_strategy  research_strategy,
    dedup_group_id      uuid,
    is_dedup_representative boolean not null default true,

    created_at timestamptz not null default now(),

    constraint cluster_members_one_item check (
        (item_kind = 'content'  and content_item_id is not null and commerce_item_id is null) or
        (item_kind = 'commerce' and commerce_item_id is not null and content_item_id is null)),
    -- A biconditional, so a noise row attached to a cluster and a clustered row
    -- marked noise are both unwritable. Those are the two ways noise accounting
    -- drifts silently.
    constraint cluster_members_noise_has_no_cluster
        check ((membership_role = 'noise') = (cluster_id is null)),
    constraint cluster_members_core_weight
        check (membership_role <> 'core' or coalesce(membership_weight, 1.0) = 1.0)
);

create unique index cluster_members_unique on cluster_members (
    run_id, item_kind, coalesce(content_item_id, commerce_item_id),
    coalesce(cluster_id, '00000000-0000-0000-0000-000000000000'::uuid),
    membership_role);

-- THE COUNTING RULE, at the only level that can enforce it. The index above
-- keys on cluster_id, so without this one an item could hold two 'core' rows in
-- the same run, or be 'core' and 'noise' at once — both accepted by Postgres
-- otherwise. Either silently breaks every denominator and makes
-- n_core + n_noise = n_items_clustered unsatisfiable.
create unique index cluster_members_one_primary
    on cluster_members (run_id, item_kind, coalesce(content_item_id, commerce_item_id))
    where membership_role in ('core', 'noise');

create index cluster_members_cluster on cluster_members (cluster_id, membership_role);
-- The NUMERATOR index: one cluster's core members by stratum and period.
create index cluster_members_series
    on cluster_members (cluster_id, membership_role, discovery_strategy, publication_period)
    where membership_role = 'core' and is_dedup_representative;
-- The DENOMINATOR index, and it is NOT the same one. The per-period corpus
-- count has no cluster_id in its predicate at all, so an index leading with
-- cluster_id cannot serve it.
create index cluster_members_corpus
    on cluster_members (run_id, publication_period, discovery_strategy, platform)
    where membership_role in ('core', 'noise') and is_dedup_representative;
create index cluster_members_creator on cluster_members (cluster_id, creator_id)
    where membership_role = 'core';
-- "Which clusters is this item in?" — the brief §31.5 drill-down.
create index cluster_members_item
    on cluster_members (item_kind, coalesce(content_item_id, commerce_item_id));


-- ----------------------------------------------------------------------------
-- B7. theme_timeseries — THE SAMPLING-BIAS DEFENCE
-- ----------------------------------------------------------------------------
-- The corpus is collected at one moment, so which publication dates are present
-- is shaped by platform ranking, not by what was published. Absolute counts per
-- period would therefore manufacture a growth curve for EVERY theme, including
-- ones that are flat or dying. What makes a temporal claim sayable is
-- WITHIN-PERIOD SHARE against a corpus denominator collected by the same
-- instrument in the same pass: whatever recency bias the platform applied, it
-- applied to numerator and denominator alike and divides out.
create table theme_timeseries (
    id                  uuid primary key default gen_random_uuid(),
    cluster_id          uuid not null references clusters (id) on delete cascade,
    cluster_revision_id uuid not null references cluster_revisions (id) on delete cascade,
    space_key           text not null,

    period_kind         period_kind not null,
    period_start        date not null,  -- market-timezone bucket start (D-30)
    stratum             stratum_kind not null,
    unit                text not null default 'item',  -- 'item' social, 'review' commerce

    n_theme             int not null,  -- core, dedup-representative, in-stratum, dated
    -- SAME filters, and two halves that are each load-bearing. Noise points are
    -- IN: excluding them would let a run's noise fraction — a clustering
    -- parameter — mechanically inflate every theme's share. Items that failed
    -- analysis, failed embedding or have no usable date are OUT: including them
    -- deflates every share by the pipeline-failure rate, which varies by
    -- platform and by period, i.e. reintroduces this exact bias in the
    -- denominator.
    n_corpus            int not null,
    -- Design effect. Clustered sampling (several items per creator) makes the
    -- effective sample smaller than the nominal one, and a Wilson interval on
    -- the nominal n is too narrow.
    deff_hat            numeric not null default 1.0,
    n_corpus_eff        numeric generated always as
        (n_corpus::numeric / nullif(deff_hat, 0)) stored,
    share               numeric generated always as
        (n_theme::numeric / nullif(n_corpus, 0)) stored,
    share_ci_low        numeric,  -- Wilson 95%, computed on n_corpus_eff
    share_ci_high       numeric,
    n_unique_creators   int not null default 0,
    max_creator_share   numeric,
    n_dedup_groups      int,

    is_qualifying       boolean not null,
    -- A period is dropped for a REASON that is recorded, so "the series starts
    -- in March" is answerable rather than mysterious.
    exclusion_reason    text,  -- underpowered_period | incomplete_period
                               -- | outside_window | dormancy_gap
    -- Needed for the platform and strategy sign-agreement falsifications: if the
    -- direction only exists in one platform's stratum, it is a mix shift.
    platform_breakdown  jsonb not null default '{}'::jsonb,
    strategy_breakdown  jsonb not null default '{}'::jsonb,

    computed_at         timestamptz not null default now()
);

-- stratum is part of the key, so the reference and category series coexist and
-- the sign-agreement check is a self-join rather than a recomputation.
create unique index theme_timeseries_unique
    on theme_timeseries (cluster_revision_id, period_kind, stratum, period_start);
create index theme_timeseries_cluster
    on theme_timeseries (cluster_id, period_kind, period_start);


-- ----------------------------------------------------------------------------
-- B8. theme_trend_tests — a refusal is a first-class outcome
-- ----------------------------------------------------------------------------
-- Brief §22 permits growth and decline claims ONLY where temporal coverage is
-- sufficient. That makes "we will not say" a RESULT to be stored, not an error
-- to be swallowed: refusal_reason is why the system declined, and it is as
-- renderable as a p-value. family_size travels with every test because a q
-- value is meaningless without the family it was corrected within.
create table theme_trend_tests (
    id                  uuid primary key default gen_random_uuid(),
    cluster_id          uuid not null references clusters (id) on delete cascade,
    cluster_revision_id uuid not null references cluster_revisions (id) on delete cascade,
    run_id              uuid not null references cluster_runs (id) on delete cascade,

    test_kind           text not null,  -- ca_trend_permutation | onset_fisher
    stratum             text not null,
    period_kind         period_kind not null,

    n_periods_qualifying int not null,
    n_periods_recent     int not null,
    n_dated_members      int not null,
    -- Creators, not items. The permutation null exchanges whole creator blocks,
    -- because items by one creator are not independent draws.
    n_creator_blocks     int not null,

    statistic           numeric,  -- Cochran-Armitage T, summed over platform strata
    beta1               numeric,  -- Firth-penalised logistic slope; null when non-estimable
    or_per_period       numeric,  -- exp(beta1)
    or_ci_low           numeric,  -- creator-block bootstrap percentile
    or_ci_high          numeric,
    effect_estimable    boolean not null default true,

    p_permutation       numeric,
    q_bh                numeric,            -- BH within (space, run, test_kind)
    p_westfall_young    numeric,            -- step-down minP, for headline cards only
    family_size         int    not null,
    n_permutations      int    not null,
    permutation_seed    bigint not null,

    -- Minimum detectable effect at 80% power. Without it an insignificant
    -- result cannot be told apart from an underpowered one — and most of them
    -- are underpowered, which is the honest thing to be able to say.
    mde_or_80           numeric,
    s_high_used         numeric,
    classification      trend_class not null,
    replication_status  replication_status,
    refusal_reason      text,
    guard_flags         jsonb not null default '{}'::jsonb,

    computed_at         timestamptz not null default now()
);

create unique index theme_trend_tests_unique
    on theme_trend_tests (cluster_revision_id, test_kind, stratum);
create index theme_trend_tests_cluster on theme_trend_tests (cluster_id, computed_at desc);


-- ============================================================================
-- DEFERRAL REGISTER
--
-- Everything below is specified by the architecture and deliberately NOT built
-- here, because brief §37 ranks speed to a usable MVP above modularity and
-- infrastructure sophistication, and §38 wants the end-to-end flow first.
-- Nothing here is renamed or re-keyed, so each item is an additive migration.
-- ============================================================================
--
-- TABLES DEFERRED — §2.6 taxonomy (14 of 18):
--   lang_buckets             the lang_bucket partition as DATA rather than an
--                            enum. Until it exists, labels.lang_bucket and
--                            observation_label_map.lang_bucket are plain text
--                            with no FK. ADD THE FK with the table.
--   canonicalisation_config  the (facet, lang_bucket) thresholds and the
--                            canon_version registry. Until it exists,
--                            canon_version is an unconstrained integer and the
--                            only canonicalisation performed is the exact
--                            collapse the labels unique key already gives.
--   brand_name_snapshot      pins the promotion gate's brand check to a
--                            canon_version instead of the live brands table.
--   label_observation_rejects proposals the schema accepted and validation
--                            rejected; doc 02 §1.1 argues they must be kept.
--   label_merges, label_pair_judgements, label_pair_gold, label_links,
--   concept_groups, concept_group_members, taxonomy_entries, label_decisions
--                            the merge / adjudication / promotion pipeline
--                            (D-17). Its hooks are present: labels.status,
--                            labels.canonical_id, label_facets.merge_posture.
--   label_vocab_snapshots    the D-15 suggestion loop's per-item snapshot.
--   label_support_daily      the D-30 market-timezone daily support rollup.
--   views label_resolved, mv_label_support_weekly.
--
-- TABLES DEFERRED — §2.8 clustering (1 of 9):
--   cluster_links            cross-space association. D-11 already deleted its
--                            social<->commerce kind; what remains (cross_facet,
--                            member_cooccurrence with Fisher p/q) is a second
--                            pass over clusters that exist.
--
-- COLUMNS DEFERRED:
--   label_observations (doc 02 §1.4) — vocab_snapshot_id, vocab_mode,
--     was_suggested, shown_rank, self_reported_reuse: the D-15 suggestion-loop
--     A/B instrumentation, which only exists once suggestions are rendered.
--     gloss_lang_ok, norm_pipeline_hint, model_confidence: audit fields.
--     language_observed is NOT deferred — D-14 DELETES it.
--   labels (doc 02 §1.4) — canon_method, canon_similarity, canonicalised_at,
--     needs_recanon, embed_key, embed_key_sha, embedded_at_support: all written
--     by the deferred canonicaliser. flags (label_flag[]) and the whole
--     label_flag type: artefact flags raised by the deferred bias report.
--     observation_count, distinct_commerce, distinct_strategies,
--     strategy_unknown_units, holdout_support, support_suggested,
--     suggested_share, evidence_strength, status_reason, status_changed_at,
--     first_pub_date, last_pub_date, distinct_pub_weeks, first_seen_run,
--     last_seen_run: counters for the deferred promotion gate and bias report.
--     NOTE the promotion gate ALSO needs distinct_platforms, distinct_queries,
--     top_query_share and top_creator_share, which ARE built above.
--   labels — the labels_no_alias_chain trigger (doc 02 §1.4). canonical_id
--     exists but nothing writes it yet. ADD THE TRIGGER WITH THE MERGER: the
--     one-hop coalesce(canonical_id, id) used everywhere silently splits a
--     label's support the first time a merge points an alias at an alias.
--   labels — the pg_trgm index on normalised_form, and the extension. Needed by
--     doc 02 §2.7's lexical gate, which is part of the deferred canonicaliser.
--   cluster_runs (doc 04 §7.3) — n_provisional, reducer_refit, selection_report,
--     style_probe, exposure_checks, wallclock_ms: reducer-selection and
--     style-probe calibration output (doc 04 §1.5c, §2.9).
--   clusters (doc 04 §7.4) — label_status, label_history, shared_attributes,
--     demotion_reason, leakage_candidates, resurrect_gap_runs,
--     undated_members_reference: label verification, the brand-leakage review
--     queue and resurrection bookkeeping. label_confidence IS built, because
--     brief §21 names confidence per cluster.
--   cluster_revisions (doc 04 §7.5) — centroid_run_space: the reduced-space
--     centroid, kept only to reproduce a run-space diagnostic. Identity
--     matching is in 1536-d (D-9) and does not read it.
--   cluster_members (doc 04 §7.7) — is_atypical, near_dup_candidate: review
--     flags; the authoritative dedup decision is is_dedup_representative, which
--     IS built.
--
-- CROSS-TRACK FOREIGN KEYS: none deferred. This file FKs content_items and
--   creators (0001), research_runs and research_strategy (0002),
--   content_analysis (0003) and commerce_items / commerce_reviews (0004), all
--   of which sort before it. That ordering is now a real dependency: this file
--   must not be renumbered below 0004.
-- ============================================================================
