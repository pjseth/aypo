-- 0002_research.sql — the research loop: runs, iterations, queries, results,
-- narrative log, budget ledger, hypotheses and their evidence.
--
-- Naming rule used throughout this file, stated once so the divergences below
-- read as decisions rather than drift:
--   * ARCHITECTURE.md's canonical decisions D-1..D-42 are followed exactly.
--   * Where the live pydantic contract in aypo/models/ already spells something,
--     the code wins and the divergence is commented at the column.
--   * Tables and columns only a deferred subsystem writes are omitted and
--     recorded in the DEFERRED block at the foot of this file.

CREATE EXTENSION IF NOT EXISTS vector;
-- Trigram index on normalized_text: the loop's lexical near-duplicate check runs
-- before the semantic one because it is three orders of magnitude cheaper, and
-- most regenerated queries differ by a stopword.
CREATE EXTENSION IF NOT EXISTS pg_trgm;


-- ─────────────────────────────────────────────────────────────────────────────
-- Enums
--
-- Enum vs text is decided per column by whether the vocabulary can outlive a
-- provider. Closed process vocabularies (states, phases) are enums; anything
-- naming an external system (platform, provider, model) is text — see D-2.
-- ─────────────────────────────────────────────────────────────────────────────

-- D-1. The one spelling of the brief §8 query families. Doc 04's
-- `discovery_strategy` and doc 05's 'A'..'E' letters are deleted.
--
-- Divergence from aypo/models/provenance.py: SamplingStrategy also carries
-- SEED. A user-supplied seed is not a query family — it is a *reason* a query
-- was generated — so it is recorded as research_queries.triggered_by_kind='seed'
-- and the query still carries whichever of the five families it actually is.
-- Admitting 'seed' here would create a sixth stratum that no allocator weights
-- and that would silently leak operator assumptions into the corpus (brief §1).
CREATE TYPE research_strategy AS ENUM (
    'brand', 'category', 'competitor', 'theme', 'hypothesis'
);

CREATE TYPE research_depth AS ENUM ('quick', 'standard', 'deep');

CREATE TYPE research_run_state AS ENUM (
    'created', 'planning', 'iterating', 'focusing', 'stopping', 'synthesizing',
    'completed', 'failed', 'paused', 'cancelled'
);

CREATE TYPE research_iteration_phase AS ENUM (
    'allocate', 'propose', 'dedupe', 'discover', 'discover_wait', 'retrieve_wait',
    'analyze_wait', 'cluster', 'interpret', 'measure', 'decide', 'closed', 'abandoned'
);

CREATE TYPE research_query_status AS ENUM (
    'proposed', 'admitted', 'rejected_duplicate', 'rejected_guardrail',
    'rejected_low_yield', 'rejected_budget', 'queued', 'running', 'succeeded',
    'partial', 'failed', 'refused', 'skipped'
);

CREATE TYPE research_dedup_state AS ENUM (
    'new', 'dup_in_run', 'dup_cross_run', 'dup_in_batch'
);

CREATE TYPE research_item_state AS ENUM (
    'discovered', 'pending_retrieval', 'retrieved', 'analyzing', 'analyzed',
    'failed', 'skipped'
);

CREATE TYPE research_budget_kind AS ENUM (
    'social_unit', 'commerce_unit', 'llm', 'embedding', 'transcription',
    'provider_call', 'provider_result', 'storage', 'wall'
);

CREATE TYPE research_budget_status AS ENUM (
    'reserved', 'settled', 'orphaned', 'voided'
);

-- Brief §26 requires the stop reason be stored. The full set is declared now
-- even though the MVP loop can only reach a subset, because ALTER TYPE ... ADD
-- VALUE cannot be used by a statement in the same transaction that adds it, and
-- every migration here runs inside one transaction.
CREATE TYPE research_stop_reason AS ENUM (
    'user_cancelled', 'fatal_error', 'budget_hard_stop', 'wall_clock_exhausted',
    'wall_clock_pacing', 'budget_drain', 'content_target_reached',
    'user_depth_reached', 'max_iterations_reached', 'saturation',
    'rank_exhaustion', 'repetition', 'hypothesis_starvation',
    'insufficient_signal', 'source_exhaustion', 'repeated_iteration_failure'
);

CREATE TYPE research_log_event AS ENUM (
    'run_created', 'objective_set', 'capability_probe', 'source_unconfigured',
    'seed_probe_set', 'state_transition', 'iteration_opened', 'quota_allocated',
    'queries_proposed', 'query_admitted', 'query_rejected', 'source_searched',
    'results_admitted', 'source_degraded', 'provider_garbage', 'items_analyzed',
    'cluster_snapshot', 'pattern_detected', 'competitor_discovered',
    'competitor_promoted', 'hypothesis_created', 'hypothesis_updated',
    'hypothesis_resolved', 'commerce_probe_launched', 'social_commerce_link',
    'finding_drafted', 'finding_published', 'evidence_linked',
    'saturation_measured', 'focus_mode_entered', 'budget_threshold',
    'phase_deadline_partial', 'llm_fallback', 'llm_refused', 'iteration_closed',
    'iteration_abandoned', 'saturation_unavailable', 'diversification_probe',
    'concentration_cap', 'temporal_backfill', 'stop_evaluated', 'run_stopped',
    'run_failed'
);

CREATE TYPE hypothesis_status AS ENUM (
    'open', 'testing', 'supported', 'refuted', 'insufficient_evidence', 'abandoned'
);


-- ─────────────────────────────────────────────────────────────────────────────
-- research_runs
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE research_runs (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- D-31: single-tenant MVP. This is the tenancy key an RLS policy will gate
    -- on; no org_id exists.
    created_by           uuid,
    created_at           timestamptz NOT NULL DEFAULT now(),
    started_at           timestamptz,
    stopped_at           timestamptz,

    -- The objective and the optional seeds are the ONLY content-bearing inputs
    -- an operator may supply (brief §1). Everything else in the corpus is
    -- discovered, so these two columns are the entire seeding surface and are
    -- kept here, auditable, rather than being spread through config.
    objective            text NOT NULL CHECK (length(btrim(objective)) > 0),
    subject_brand        text NOT NULL CHECK (length(btrim(subject_brand)) > 0),
    subject_brand_id     uuid,          -- -> brands(id); FK added by the 0003 amend
    user_seeds           jsonb NOT NULL DEFAULT '{}'::jsonb,

    -- D-29: ISO-3166 alpha-2, the one spelling (doc 07's `market` is renamed to
    -- this). It keys config/market/<market_code>.yaml.
    market_code          text NOT NULL CHECK (market_code ~ '^[A-Z]{2}$'),
    language_codes       text[] NOT NULL DEFAULT '{}',
    depth                research_depth NOT NULL DEFAULT 'standard',

    -- State machine. Nullable caps below are deliberate: the row exists in
    -- state 'created' before PLANNING has resolved them.
    state                research_run_state NOT NULL DEFAULT 'created',
    state_entered_at     timestamptz NOT NULL DEFAULT now(),
    iteration_no         int NOT NULL DEFAULT 0,

    -- Single-flight leasing. next_tick_at makes the orchestrator a poller over
    -- an index rather than a long-lived process holding run state in memory, so
    -- a crash loses at most one tick.
    lease_owner          text,
    lease_expires_at     timestamptz,
    next_tick_at         timestamptz NOT NULL DEFAULT now(),

    -- Gap-free sequence for research_log, allocated by UPDATE ... RETURNING in
    -- the same transaction as the log row. The row lock serialises concurrent
    -- emitters and an aborted transaction rolls the increment back with the row
    -- it was for, so brief §32's narrative can never show a hole it cannot
    -- explain.
    log_seq              bigint NOT NULL DEFAULT 0,

    -- The four ceilings aypo/research/budget.py enforces, resolved at PLANNING
    -- from Settings x depth. Named to match that module, not doc 01's
    -- cap_social_units/cap_commerce_units split (see DEFERRED).
    cap_collection_usd   numeric(10,4),
    cap_llm_usd          numeric(10,4),
    cap_items            int,
    cap_wall_seconds     int,
    max_iterations       int,

    -- I-2: one embedding space per run. Pinned at PLANNING; a mid-run change is
    -- a fatal error rather than a silently incomparable cosine.
    embedding_model      text NOT NULL,
    -- pgvector's HNSW index tops out at 2000 dimensions, which is why the
    -- 3072-d native model output is requested at 1536 and why this is a CHECK
    -- and not a comment.
    embedding_dim        int NOT NULL CHECK (embedding_dim BETWEEN 1 AND 2000),

    config_resolved      jsonb,   -- provider availability, actor ids, resolved gates
    coverage_gaps        jsonb,   -- planned vs achieved units per platform/strategy/stratum
    sampling_design      jsonb,   -- realised rank/sort/page-depth mix, survivorship

    -- The gate the trend subsystem reads BEFORE it is permitted to emit any
    -- growth or decline claim (brief §22). A corpus whose dated items pile into
    -- one bucket cannot support a trajectory, however large it is.
    temporal_coverage    text NOT NULL DEFAULT 'unknown'
        CHECK (temporal_coverage IN ('unknown', 'insufficient', 'sufficient')),

    -- Brief §26. stop_detail must be sufficient to reproduce the decision
    -- without re-running any metric: every conjunct's value, its family's
    -- availability, and whether it held.
    stop_reason          research_stop_reason,
    stop_detail          jsonb,
    saturation_basis     text,    -- 'full' | 'uncalibrated' | '<family>_unavailable,...'

    degraded             boolean NOT NULL DEFAULT false,
    fatal_error          text,
    cancel_requested     boolean NOT NULL DEFAULT false,

    -- A stopped run must say why. Enforced here because "the run ended" with no
    -- reason is the single failure brief §26 names explicitly.
    CONSTRAINT research_runs_stop_reason_required CHECK (
        state NOT IN ('completed', 'failed', 'cancelled') OR stop_reason IS NOT NULL
    )
);

-- Partial: the poller only ever looks at runs that can still advance.
CREATE INDEX research_runs_tick_idx ON research_runs (next_tick_at)
    WHERE state NOT IN ('completed', 'failed', 'cancelled', 'paused');
CREATE INDEX research_runs_state_idx ON research_runs (state, created_at DESC);


-- ─────────────────────────────────────────────────────────────────────────────
-- research_iterations
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE research_iterations (
    run_id            uuid NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    iteration_no      int NOT NULL,
    phase             research_iteration_phase NOT NULL DEFAULT 'allocate',
    phase_entered_at  timestamptz NOT NULL DEFAULT now(),
    phase_deadline_at timestamptz,
    opened_at         timestamptz NOT NULL DEFAULT now(),
    closed_at         timestamptz,

    is_focus          boolean NOT NULL DEFAULT false,

    -- The allocation this iteration was given and the budget it was given it
    -- against, both frozen at ALLOCATE. Kept as jsonb because the allocator's
    -- shape (per-strategy weights, bandit state, which gates were open) is
    -- still moving and freezing it into columns would force a migration per
    -- tuning change for data that is only ever read back whole.
    quota             jsonb NOT NULL,
    budget_snapshot   jsonb NOT NULL,
    spend             jsonb,

    queries_proposed  int NOT NULL DEFAULT 0,
    queries_admitted  int NOT NULL DEFAULT 0,
    -- {duplicate: n, guardrail: n, low_yield: n, budget: n} — a count per
    -- rejection class, so "the loop stopped proposing" and "the loop proposed
    -- and the guardrail ate it" are distinguishable afterwards.
    queries_rejected  jsonb NOT NULL DEFAULT '{}'::jsonb,

    items_admitted    int NOT NULL DEFAULT 0,
    -- Spelled -ised per the wave assignment; doc 01 §8.2 spells it
    -- `items_analyzed`.
    items_analysed    int NOT NULL DEFAULT 0,

    degraded          boolean NOT NULL DEFAULT false,
    degraded_reasons  text[] NOT NULL DEFAULT '{}',

    PRIMARY KEY (run_id, iteration_no)
);


-- ─────────────────────────────────────────────────────────────────────────────
-- hypotheses  (brief §25)
--
-- Created before research_queries because a hypothesis-strategy query points
-- back at the hypothesis it tests.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE hypotheses (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                 uuid NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    created_iteration      int NOT NULL,
    resolved_iteration     int,
    status                 hypothesis_status NOT NULL DEFAULT 'open',

    -- The CONTENT of all four of these is generated from collected data and is
    -- never authored here, in a prompt, or in a fixture (brief §1, §25, §34).
    -- Placeholder shape only: "Theme X may be more strongly associated with
    -- Brand A than with the subject brand."
    statement              text NOT NULL,
    reason_generated       text NOT NULL,

    -- Brief §25 requires a hypothesis to state what would REFUTE it, not only
    -- what would support it. Both are arrays of observable descriptions; the
    -- typeof CHECKs exist so the generated `admissible` column below cannot be
    -- handed an object and raise at insert time.
    confirming_observables jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(confirming_observables) = 'array'),
    refuting_observables   jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(refuting_observables) = 'array'),

    -- Structured query specs: {platform, strategy, query_text,
    -- expected_observable, disconfirming}. Structured rather than prose so the
    -- loop turns a plan into queries without a second model call — which also
    -- means a follow-up query cannot drift from the hypothesis that justified
    -- it.
    follow_up_plan         jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(follow_up_plan) = 'array'),

    platforms              text[] NOT NULL DEFAULT '{}',

    -- Derived, not asserted: a hypothesis is admissible only if it is actually
    -- testable. Generated rather than written by the caller because
    -- admissibility feeds the hypothesis-productivity stop condition, and a
    -- stop condition must not depend on a flag the thing being measured sets
    -- about itself.
    admissible             boolean GENERATED ALWAYS AS (
        length(btrim(statement)) > 0
        AND length(btrim(reason_generated)) > 0
        AND jsonb_array_length(confirming_observables) >= 1
        AND jsonb_array_length(refuting_observables) >= 1
        AND jsonb_array_length(follow_up_plan) >= 1
    ) STORED,

    resolution_note        text,
    created_at             timestamptz NOT NULL DEFAULT now(),
    resolved_at            timestamptz,

    CONSTRAINT hypotheses_resolution_consistent CHECK (
        status IN ('open', 'testing')
        OR (resolved_iteration IS NOT NULL AND resolved_at IS NOT NULL)
    )
);

CREATE INDEX hypotheses_open_idx ON hypotheses (run_id, status)
    WHERE status IN ('open', 'testing');
CREATE INDEX hypotheses_iteration_idx ON hypotheses (run_id, created_iteration);


-- ─────────────────────────────────────────────────────────────────────────────
-- research_queries
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE research_queries (
    id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                    uuid NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    iteration_no              int NOT NULL,

    strategy                  research_strategy NOT NULL,
    -- D-2: text, not an enum, with collection_sources (0001) as the
    -- authoritative vocabulary. An enum would outlive a provider being removed,
    -- and a marketplace must be able to count as a platform. Canonical values
    -- follow aypo/models/provenance.py::Platform — tiktok, youtube, instagram,
    -- facebook, web, shopee, lazada — NOT D-2's shopee_ph/lazada_ph, because
    -- the pydantic Platform enum is the live contract and 104 tests depend on
    -- it; the market is carried by research_runs.market_code instead.
    platform                  text NOT NULL,
    query_text                text NOT NULL,
    normalized_text           text NOT NULL,
    language_code             text,
    filters                   jsonb NOT NULL DEFAULT '{}'::jsonb,
    query_embedding           vector(1536),

    -- Brief §9: WHY this query was generated, and WHAT triggered it. These five
    -- columns are the auditable chain the brief §32 run view renders as a tree.
    rationale                 text NOT NULL CHECK (length(btrim(rationale)) > 0),
    -- Polymorphic trigger for the cases with no dedicated column (cluster,
    -- competitor, discovered label, seed, objective).
    triggered_by_kind         text NOT NULL DEFAULT 'none' CHECK (
        triggered_by_kind IN ('objective', 'seed', 'cluster', 'competitor',
                              'hypothesis', 'finding', 'discovered_label', 'none')
    ),
    triggered_by_id           text,
    -- The two triggers that have real tables get real FKs, because they are the
    -- two the loop follows programmatically rather than only displays.
    triggered_by_hypothesis_id uuid REFERENCES hypotheses(id) ON DELETE SET NULL,
    triggered_by_finding_id   uuid,    -- -> findings(id); FK added by the 0010 amend
    parent_query_id           uuid REFERENCES research_queries(id) ON DELETE SET NULL,

    expected_observable       text,
    -- Brief §25/§8E: a query that can only confirm is not a test. Marked so the
    -- FOCUSING mode can admit disconfirming queries only.
    is_disconfirming          boolean NOT NULL DEFAULT false,
    -- D-45: part of the frozen reference set, re-executed verbatim every run so
    -- that between-run comparisons compare the same instrument.
    is_reference_query        boolean NOT NULL DEFAULT false,

    status                    research_query_status NOT NULL DEFAULT 'proposed',
    reject_reason             text,
    dup_of_query_id           uuid REFERENCES research_queries(id) ON DELETE SET NULL,
    dup_similarity            numeric(5,4),
    dup_method                text CHECK (
        dup_method IS NULL
        OR dup_method IN ('exact', 'token_set_ratio', 'jaccard', 'semantic')
    ),

    job_id                    uuid,    -- -> collection_jobs(id); FK added by the 0001 amend
    -- Assigned by the quota allocator after DEDUPE, never by the planner: a
    -- model that could size its own request could spend the whole budget on one
    -- query.
    requested_units           int NOT NULL DEFAULT 0,
    results_total             int NOT NULL DEFAULT 0,
    results_new               int NOT NULL DEFAULT 0,
    results_admitted          int NOT NULL DEFAULT 0,
    results_relevant          int NOT NULL DEFAULT 0,
    cost_usd                  numeric(10,6) NOT NULL DEFAULT 0,

    proposed_at               timestamptz NOT NULL DEFAULT now(),
    started_at                timestamptz,
    finished_at               timestamptz,

    -- A hypothesis-strategy query without a hypothesis is unauditable, and a
    -- hypothesis-triggered query that silently loses its link breaks brief §9's
    -- chain exactly where it matters most.
    CONSTRAINT research_queries_hypothesis_link CHECK (
        (strategy <> 'hypothesis' AND triggered_by_kind <> 'hypothesis')
        OR triggered_by_hypothesis_id IS NOT NULL
    )
);

CREATE INDEX rq_run_iter_idx  ON research_queries (run_id, iteration_no, status);
CREATE INDEX rq_strategy_idx  ON research_queries (run_id, strategy, status);
CREATE INDEX rq_trigger_idx   ON research_queries (run_id, triggered_by_kind, triggered_by_id);
CREATE INDEX rq_hypothesis_idx ON research_queries (triggered_by_hypothesis_id)
    WHERE triggered_by_hypothesis_id IS NOT NULL;
CREATE INDEX rq_norm_trgm_idx ON research_queries USING gin (normalized_text gin_trgm_ops);
CREATE INDEX rq_embedding_idx ON research_queries USING hnsw (query_embedding vector_cosine_ops)
    WHERE status IN ('admitted', 'queued', 'running', 'succeeded', 'partial');

-- Last line of defence for deduplication: even with a bug in the application
-- check, the same normalised query cannot be EXECUTED twice on one platform in
-- one run. Scoped to executed statuses so a rejected duplicate is still
-- recorded as having been proposed — the rejection rate is itself a saturation
-- signal.
CREATE UNIQUE INDEX rq_exact_dedup_idx
    ON research_queries (run_id, platform, normalized_text)
    WHERE status IN ('admitted', 'queued', 'running', 'succeeded', 'partial');


-- ─────────────────────────────────────────────────────────────────────────────
-- research_results — D-3 / I-1
--
-- The single authoritative (query x item) provenance record, for BOTH social
-- and commerce. Nothing anywhere derives strategy, rank or platform from
-- another table.
--
-- D-3: sampling strategy is a property of the DISCOVERING QUERY, never a
-- single value on the item. An item found by a category query and again by a
-- competitor query has two rows here and belongs to two strata; collapsing that
-- to one column on the item would make every share computed from it wrong, and
-- is why commerce_items has no sampling_strategy column.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE research_results (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                 uuid NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    query_id               uuid NOT NULL REFERENCES research_queries(id) ON DELETE CASCADE,
    iteration_no           int NOT NULL,

    platform               text NOT NULL,   -- D-2, as on research_queries
    provider               text NOT NULL,   -- apify:<actor_id> | youtube_api | serper | ...
    -- Matches aypo/models/provenance.py::CollectionMethod exactly. Doc 01 §8.4
    -- used this column name for a different axis (search/hashtag/creator/...),
    -- which is recorded separately below as discovery_mode so neither meaning
    -- is lost.
    collection_method      text NOT NULL CHECK (
        collection_method IN ('official_api', 'managed_collector', 'web_search')
    ),
    discovery_mode         text CHECK (
        discovery_mode IS NULL
        OR discovery_mode IN ('search', 'hashtag', 'creator', 'sound', 'related', 'marketplace')
    ),

    external_id            text CHECK (external_id IS NULL OR length(external_id) <= 512),
    url                    text NOT NULL,
    url_canonical          text NOT NULL CHECK (length(url_canonical) <= 2048),
    -- ONE dedup key, because ON CONFLICT takes exactly one conflict target; two
    -- unique indexes cannot both be named by a single INSERT, so a conflict on
    -- the unnamed one would raise instead of recording the rediscovery.
    --
    -- Plain coalesce, not the sha256(convert_to(...)) of doc 01 §8.4: convert_to
    -- is STABLE, not IMMUTABLE, so that expression will not compile as a
    -- generated column on PG17 (verified). The length CHECKs above keep the key
    -- inside the btree tuple limit, which is what the hash was really buying.
    item_key               text GENERATED ALWAYS AS (
        coalesce(external_id, url_canonical)
    ) STORED,

    -- The sampling design, recorded per item (brief §28 "preserve provenance").
    -- Without rank and page depth, a share computed over this corpus cannot be
    -- distinguished from a share over whatever the provider chose to rank first.
    raw_rank               int,
    sort_order             text,
    page_depth             int NOT NULL DEFAULT 1,
    published_at           timestamptz,
    published_at_precision text CHECK (
        published_at_precision IS NULL
        OR published_at_precision IN ('exact', 'day', 'month', 'unknown')
    ),
    discovered_at          timestamptz NOT NULL DEFAULT now(),

    dedup_state            research_dedup_state NOT NULL DEFAULT 'new',
    -- Immutable: written on INSERT only, and ON CONFLICT ... DO UPDATE never
    -- touches it. That makes "which query first found this" a database property
    -- rather than an application convention, which is what per-query yield
    -- attribution depends on.
    first_seen_query_id    uuid REFERENCES research_queries(id) ON DELETE SET NULL,
    first_seen_run_id      uuid REFERENCES research_runs(id) ON DELETE SET NULL,

    -- D-40: written by collectors/relevance.py at admission. relevance_source
    -- is mandatory alongside the score because an LLM opinion feeding a stop
    -- predicate must stay distinguishable from a measurement.
    relevance_score        numeric(5,4),
    relevance_source       text CHECK (
        relevance_source IS NULL
        OR relevance_source IN ('heuristic', 'llm', 'provider')
    ),
    admitted               boolean NOT NULL DEFAULT false,
    admit_reason           text,
    reject_reason          text,

    content_item_id        uuid,   -- -> content_items(id);  FK added by the 0001 amend
    commerce_item_id       uuid,   -- -> commerce_items(id); FK added by the 0006 amend
    item_state             research_item_state NOT NULL DEFAULT 'discovered',

    -- 1.0 per admitted item IN ITS OWN DIMENSION (social or commerce); 0 for
    -- duplicates and for items the admission gate rejected. Charging commerce
    -- at a fraction would let a commerce cap buy many times its nominal records.
    cu_charged             numeric(6,3) NOT NULL DEFAULT 0,
    raw                    jsonb,

    -- A result points at a social item or a commerce item, never both. It
    -- points at neither until retrieval resolves it, which is why this is
    -- at-most-one rather than exactly-one.
    CONSTRAINT research_results_one_item CHECK (
        num_nonnulls(content_item_id, commerce_item_id) <= 1
    ),
    CONSTRAINT research_results_rejection_explained CHECK (
        admitted OR item_state = 'discovered' OR reject_reason IS NOT NULL
    )
);

CREATE UNIQUE INDEX rr_item_unique_idx ON research_results (run_id, item_key);
CREATE INDEX rr_url_idx      ON research_results (run_id, url_canonical);
CREATE INDEX rr_query_idx    ON research_results (query_id);
CREATE INDEX rr_pending_idx  ON research_results (run_id, iteration_no, item_state)
    WHERE admitted;
CREATE INDEX rr_content_idx  ON research_results (content_item_id)
    WHERE content_item_id IS NOT NULL;
CREATE INDEX rr_commerce_idx ON research_results (commerce_item_id)
    WHERE commerce_item_id IS NOT NULL;


-- ─────────────────────────────────────────────────────────────────────────────
-- research_log — the narrative tree brief §32 renders
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE research_log (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          uuid NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    iteration_no    int,
    seq             bigint NOT NULL,
    occurred_at     timestamptz NOT NULL DEFAULT now(),

    event_type      research_log_event NOT NULL,
    phase           research_iteration_phase,

    -- The narrative edge, and the reason this is a tree rather than a feed:
    -- a follow-up query's node hangs off the hypothesis node that caused it, so
    -- "why did you search this?" is a tree walk instead of a join puzzle.
    parent_event_id uuid REFERENCES research_log(id) ON DELETE SET NULL,

    -- Polymorphic pointer to the domain row the event is about. Deliberately
    -- untyped text: the log must be able to reference rows in tracks that do
    -- not exist yet without this migration gaining a dependency on them.
    subject_kind    text CHECK (
        subject_kind IS NULL
        OR subject_kind IN ('query', 'result', 'cluster', 'competitor', 'hypothesis',
                            'finding', 'provider', 'budget', 'run', 'iteration')
    ),
    subject_id      text,

    -- One sentence, written for a marketer. This is rendered verbatim in the
    -- run view, so it is a product surface and not a debug string.
    summary         text NOT NULL CHECK (length(btrim(summary)) > 0),
    detail          text,
    payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
    visibility      text NOT NULL DEFAULT 'user' CHECK (visibility IN ('user', 'debug')),
    severity        text NOT NULL DEFAULT 'info' CHECK (severity IN ('info', 'warning', 'error'))
);

CREATE UNIQUE INDEX rl_seq_idx   ON research_log (run_id, seq);
CREATE INDEX rl_run_time_idx     ON research_log (run_id, occurred_at);
CREATE INDEX rl_parent_idx       ON research_log (parent_event_id);
CREATE INDEX rl_subject_idx      ON research_log (run_id, subject_kind, subject_id);
CREATE INDEX rl_user_idx         ON research_log (run_id, iteration_no, seq)
    WHERE visibility = 'user';


-- ─────────────────────────────────────────────────────────────────────────────
-- research_budget_ledger
--
-- Reserve-then-settle, so a call that may have billed is never invisible. The
-- four ceilings aypo/research/budget.py enforces map onto `kind` as:
--
--   collection USD  -> provider_call, provider_result  (sum usd)
--   LLM USD         -> llm, embedding, transcription   (sum usd)
--   items           -> social_unit, commerce_unit      (sum qty)
--   wallclock       -> wall                            (sum qty, seconds)
--
-- Keeping them in one ledger rather than four counters is what makes the stop
-- reason reproducible after the fact: the row that pushed a ceiling over is
-- still there, with the query and iteration that caused it.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE research_budget_ledger (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id             uuid NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    iteration_no       int,
    phase              research_iteration_phase,
    strategy           research_strategy,
    query_id           uuid REFERENCES research_queries(id) ON DELETE SET NULL,
    content_item_id    uuid,
    commerce_item_id   uuid,

    kind               research_budget_kind NOT NULL,
    status             research_budget_status NOT NULL DEFAULT 'reserved',

    -- D-25: which budget line this spend belongs to ('analysis', 'taxonomy',
    -- 'cluster', 'competitor', 'findings', 'research'). Required, with no
    -- default, because exhausting one line must degrade that subsystem only —
    -- unattributed spend would have to fail the whole run instead.
    subsystem          text NOT NULL,

    -- Idempotency is per run: the same logical charge retried after a crash
    -- must not be counted twice, and a retry is exactly when a budget is most
    -- likely to be exceeded.
    idem_key           text NOT NULL,

    provider           text,   -- anthropic | openai | apify | serper | local
    model              text,

    qty                numeric(14,4) NOT NULL DEFAULT 0,  -- tokens | seconds | units | calls
    unit_cost_usd      numeric(14,10),
    usd                numeric(12,6) NOT NULL DEFAULT 0,
    input_tokens       bigint NOT NULL DEFAULT 0,
    output_tokens      bigint NOT NULL DEFAULT 0,
    cache_read_tokens  bigint NOT NULL DEFAULT 0,
    cache_write_tokens bigint NOT NULL DEFAULT 0,

    detail             jsonb NOT NULL DEFAULT '{}'::jsonb,
    reserved_at        timestamptz NOT NULL DEFAULT now(),
    -- Set when the line leaves 'reserved', whichever terminal state it reaches.
    settled_at         timestamptz,

    CONSTRAINT rbl_terminal_timestamped CHECK (
        status = 'reserved' OR settled_at IS NOT NULL
    )
);

CREATE UNIQUE INDEX rbl_idem_idx  ON research_budget_ledger (run_id, idem_key);
CREATE INDEX rbl_run_iter_idx     ON research_budget_ledger (run_id, iteration_no, kind);
CREATE INDEX rbl_subsystem_idx    ON research_budget_ledger (run_id, subsystem, status);
CREATE INDEX rbl_open_idx         ON research_budget_ledger (run_id, status)
    WHERE status = 'reserved';

-- Plain views, not materialised. A materialised view here would be global
-- rather than per-run, so refreshing it at every MEASURE recomputes the
-- aggregate across every run in the database, and a non-concurrent refresh
-- takes ACCESS EXCLUSIVE and blocks readers. The ledger is a few thousand rows
-- per run, so this is an index scan.
--
-- 'orphaned' is summed alongside 'settled' on purpose: a call that may have
-- billed must be assumed to have billed.
CREATE VIEW v_research_spend_by_iteration AS
SELECT run_id,
       iteration_no,
       sum(usd) FILTER (WHERE provider = 'anthropic')                      AS llm_usd,
       sum(usd) FILTER (WHERE provider <> 'anthropic' OR provider IS NULL) AS other_usd,
       sum(qty) FILTER (WHERE kind = 'social_unit')                        AS social_units,
       sum(qty) FILTER (WHERE kind = 'commerce_unit')                      AS commerce_units,
       sum(input_tokens)  AS input_tokens,
       sum(output_tokens) AS output_tokens
  FROM research_budget_ledger
 WHERE status IN ('settled', 'orphaned')
 GROUP BY run_id, iteration_no;

-- Read by the allocator: strategy weights are only honest if spend per strategy
-- is observable, otherwise the mix that was requested is mistaken for the mix
-- that was bought.
CREATE VIEW v_research_spend_by_strategy AS
SELECT run_id,
       iteration_no,
       strategy,
       sum(usd) AS usd,
       sum(qty) FILTER (WHERE kind = 'social_unit')   AS social_units,
       sum(qty) FILTER (WHERE kind = 'commerce_unit') AS commerce_units
  FROM research_budget_ledger
 WHERE status IN ('settled', 'orphaned') AND strategy IS NOT NULL
 GROUP BY run_id, iteration_no, strategy;


-- ─────────────────────────────────────────────────────────────────────────────
-- hypothesis_evidence  (brief §25)
--
-- Refuting evidence is first-class and sits in the same table as confirming
-- evidence: brief §25 requires a hypothesis to say what would refute it, and a
-- schema that could only store support would make that promise unkeepable.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE hypothesis_evidence (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    hypothesis_id     uuid NOT NULL REFERENCES hypotheses(id) ON DELETE CASCADE,
    content_item_id   uuid,   -- -> content_items(id);  FK added by the 0001 amend
    commerce_item_id  uuid,   -- -> commerce_items(id); FK added by the 0006 amend
    stance            text NOT NULL CHECK (stance IN ('confirming', 'refuting')),
    created_iteration int NOT NULL,
    note              text,
    created_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT hypothesis_evidence_one_item CHECK (
        num_nonnulls(content_item_id, commerce_item_id) = 1
    )
);

-- One stance per (hypothesis, item): the same item cannot be counted twice, in
-- the same direction or in both. Two partial indexes rather than one composite,
-- because NULLs in a composite unique index do not conflict.
CREATE UNIQUE INDEX he_content_unique_idx
    ON hypothesis_evidence (hypothesis_id, content_item_id)
    WHERE content_item_id IS NOT NULL;
CREATE UNIQUE INDEX he_commerce_unique_idx
    ON hypothesis_evidence (hypothesis_id, commerce_item_id)
    WHERE commerce_item_id IS NOT NULL;
CREATE INDEX he_stance_idx ON hypothesis_evidence (hypothesis_id, stance);


-- ═════════════════════════════════════════════════════════════════════════════
-- DEFERRED — recorded so the gap is visible rather than lost.
--
-- Brief §37 ranks speed to a usable MVP above modular architecture, and §38
-- says build the end-to-end flow first. Everything below is specified in
-- ARCHITECTURE.md but is written only by a subsystem this wave does not build.
-- A later migration adds it as 00NN_research_amend.sql (D-43); nothing here is
-- renamed or re-keyed when it lands.
--
-- TABLES
--   research_saturation          ARCHITECTURE §2.3 / doc 01 §8.7. The per-iteration
--                                novelty / cluster-stability / competitor-stability /
--                                yield / hypothesis metric row. Needs clustering
--                                (0007) and competitors (0009) to be meaningful; the
--                                MVP stops on the four budget.py ceilings plus
--                                max_iterations, all of which are computable from
--                                research_runs and research_budget_ledger alone.
--   research_run_transitions     ARCHITECTURE §2.3 / doc 01 §8.9. State-machine audit
--                                trail; research_log(event_type='state_transition')
--                                carries the same information for the MVP.
--
-- ENUM
--   research_query_kind          doc 01 §8 ('standard','exploratory_probe',
--                                'replication'). No column in this file uses it:
--                                exploratory probes and replication arms belong to
--                                the saturation subsystem.
--
-- research_runs  (doc 01 §8.1 unless noted)
--   cap_social_units,            the two-vector collection budget of doc 01 §2.2.
--   cap_commerce_units           aypo/research/budget.py enforces a single item
--                                ceiling today, so one cap_items column matches the
--                                code that actually spends it.
--   cap_other_usd                non-LLM, non-collection spend; nothing bills it yet.
--   gamma_social, target_social, iteration pacing solved at PLANNING (doc 01 §2.4).
--   target_commerce,
--   target_social_effective,
--   target_commerce_effective
--   prev_state, iterations_completed, consecutive_abandoned_iterations,
--   focus_iterations_used        FOCUSING-mode bookkeeping (doc 01 §5.7).
--   lease_epoch                  fencing token for concurrent orchestrators; the MVP
--                                runs one worker and leases by expiry alone.
--   calibration                  doc 01 §3.3 percentile curves for the dedup
--                                thresholds; the MVP uses the fixed D-6 rule.
--   temporal_plan                doc 01 §3.7(d) strata and target shares.
--   seed_dependence              doc 01 §3.4 'low' | 'high'.
--   pause_requested              pause/resume; cancel_requested is kept because a
--                                runaway autonomous loop must always be stoppable.
--   reference_query_set_version  D-45. Written once the frozen reference set exists
--                                and cross-run comparison is actually performed;
--                                is_reference_query is kept now so the set is
--                                identifiable from run one.
--   RLS policy on created_by     D-31. auth.uid() does not exist on the local
--                                Postgres this runs against; the policy lands with
--                                the Supabase deployment.
--
-- research_iterations  (doc 01 §8.2)
--   proposed_at, discovered_at, analyzed_at, clustered_at, interpreted_at,
--   measured_at, decided_at      per-phase timestamps; phase + phase_entered_at plus
--                                research_log give the same timeline for the MVP.
--   phase_attempt                retry counter for a stalled phase.
--   analysis_completion_rate     derivable from items_admitted / items_analysed.
--
-- research_queries  (doc 01 §8.3)
--   token_set, stem              lexical dedup and low-yield suppression inputs
--                                (doc 01 §3.3, §3.5); the pg_trgm index plus the
--                                partial unique index cover MVP dedup.
--   kind                         see research_query_kind above.
--   is_sentinel                  doc 01 §5.2(e) fixed-composition stream, a
--                                saturation input.
--   strategy_asserted            doc 01 §3.2: what the planner claimed before
--                                mechanical correction. An anti-seeding audit column.
--   dup_override_reason,         doc 01 §3.3/§3.4 dedup and guardrail audit detail.
--   guardrail_novel_tokens,
--   prior_derived
--   names_known_brand            D-21 / doc 06 XS-6; needs the brand registry (0003).
--   query_vintage_half           D-32 T4 query-vintage guard; needs trends (0007).
--   log_event_id                 denormalised pointer into research_log; the reverse
--                                direction (subject_kind='query', subject_id) is
--                                indexed here instead.
--   attempt, error_class,        per-query execution failure detail; collection_jobs
--   error_detail, garbage_count  (0001) already records attempts and last_error.
--
-- research_results  (doc 01 §8.4)
--   url_hash                     replaced by url_canonical with a length CHECK; see
--                                the item_key comment for why the hash went away.
--
-- research_log  (doc 01 §8.5)
--   lease_epoch                  see research_runs.lease_epoch.
--
-- hypotheses  (ARCHITECTURE §2.3 D-C5 / brief §25)
--   confidence                   brief §25 lists it, but a stated confidence on a
--                                hypothesis is a model opinion, and I-3 forbids a
--                                model-produced number gating anything. It lands
--                                when the calibrator (0004) can turn it into a
--                                calibrated value with a band.
-- ═════════════════════════════════════════════════════════════════════════════
