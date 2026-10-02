-- 0006_findings.sql — the evidence layer (brief §3, §27, §28, §31.7).
--
-- This is the migration that makes the product credible rather than plausible.
-- Three ideas are encoded structurally, not left to good intentions:
--
--   1. Numbers are computed by code and handed to the model as opaque refs
--      ("S1"), never written by the model. `evidence_packets` freezes exactly
--      what the model was given, so "did it cite something it was never shown?"
--      is a query rather than a trust exercise.
--   2. Brief §3's four claim layers (observed / computed / interpreted /
--      hypothesis) are a NOT NULL column, so a model interpretation can never
--      be stored indistinguishably from an observation.
--   3. Brief §28's "allow conflicting evidence" needs contradicting evidence to
--      live on the finding — hence `finding_evidence.role` and
--      `finding_refutations`, not a filter that drops the inconvenient rows.
--
-- ARCHITECTURE.md calls this file `0010_findings.sql` (D-43). It is `0006` here
-- because this build implements a reduced table set; the ordering within the
-- cascade is unchanged.
--
-- Cross-table references that are deliberately NOT foreign keys are marked
-- individually. Two reasons recur: `finding_evidence.ref_id` is polymorphic
-- across six kinds, and the brand/cluster tracks are not part of this reduced
-- schema. A dangling-id check belongs in `intelligence/findings/validators.py`
-- (V-series) either way, because an FK cannot express "and it must be of the
-- kind this row claims".

-- ---------------------------------------------------------------------------
-- immutability guard
-- ---------------------------------------------------------------------------

-- UPDATE only. A packet may be deleted when its whole research run is deleted
-- (that is the cascade below), but its contents may never be edited after the
-- model has seen them — editing one retroactively would make every audit of
-- "what was this claim actually derived from" worthless.
create or replace function aypo_forbid_update() returns trigger
language plpgsql as $$
begin
    raise exception
        'relation % is append-only: row % may not be updated',
        tg_table_name, old.id
        using errcode = 'restrict_violation';
end;
$$;

-- ---------------------------------------------------------------------------
-- evidence_packets — the frozen input to finding generation
-- ---------------------------------------------------------------------------

create table evidence_packets (
    id                      uuid primary key default gen_random_uuid(),
    research_run_id         uuid not null
                                references research_runs (id) on delete cascade,

    -- Stable identity of the candidate claim ("theme X share, subject vs
    -- category"), so the same question re-asked next iteration is recognisable
    -- as the same question rather than as a new finding.
    candidate_key           text not null,

    finding_kind            text not null check (finding_kind in (
                                'existence', 'rate_comparison', 'association',
                                'share_of_presence', 'trend', 'absence',
                                'social_commerce_link', 'competitive_gap')),

    -- No FK: brand resolution is a separate track and is not in this schema.
    subject_brand_id        uuid,

    -- PopulationFrame: {description, size, frame_n_eff, design_effect,
    -- filter_spec, date_from, date_to}. Stored whole because a finding's
    -- denominator is only meaningful alongside the filter that produced it,
    -- and splitting them invites reading one without the other.
    population_frame        jsonb not null,

    -- PropertyPredicate: {key, description, structured_sql, negation_sql,
    -- counter_analysis}. The negation is what makes a "non_instance" evidence
    -- row checkable.
    property_predicate      jsonb not null,

    -- ComputedStat[] and EvidenceItem[] exactly as serialised into the prompt.
    -- `finding_statistics` below holds the same numbers per published finding
    -- in queryable form; these two are not redundant — this one is the
    -- tamper-evident record of what was sent, that one is the read model.
    stats                   jsonb not null,
    items                   jsonb not null,

    -- The ONLY label strings the generator may use. Assembled solely from
    -- strings attested in collected data; query text is excluded on purpose, so
    -- the generator cannot name a concept that exists only in our search box.
    vocabulary              text[] not null default '{}',

    -- `items` is a stratified sample capped at ~120; concentration maths runs
    -- over the full set. Keeping the full size separate stops a reader treating
    -- the sample as the population.
    items_are_a_sample_of   integer not null check (items_are_a_sample_of >= 0),

    diagnostics             jsonb not null,

    -- SamplingFrame (ARCHITECTURE §5.5): the disclosure that must ride along
    -- with every count, share or comparison derived from this packet.
    sampling_frame          jsonb not null,

    evidence_design         text not null default 'observational_snapshot'
                                check (evidence_design = 'observational_snapshot'),
    market_code             text not null,
    builder_version         text not null,

    -- sha256 over the canonical serialisation the model received. Two packets
    -- with the same hash are the same question asked twice, so rebuilding an
    -- unchanged candidate reuses the row instead of forking the audit trail.
    packet_sha256           text not null check (packet_sha256 ~ '^[0-9a-f]{64}$'),

    created_at              timestamptz not null default now(),

    unique (research_run_id, packet_sha256)
);

create index evidence_packets_candidate_idx
    on evidence_packets (research_run_id, candidate_key);

create trigger evidence_packets_immutable
    before update on evidence_packets
    for each row execute function aypo_forbid_update();

-- ---------------------------------------------------------------------------
-- findings
-- ---------------------------------------------------------------------------

create table findings (
    id                      uuid primary key default gen_random_uuid(),
    research_run_id         uuid not null
                                references research_runs (id) on delete cascade,
    -- D-41: the iteration that produced it, so marginal evidence yield per
    -- iteration is computable from real links rather than a substitute.
    created_iteration       integer not null default 0,

    -- restrict, not cascade: a published finding without the packet it came
    -- from is an unfalsifiable assertion, which is the thing this schema exists
    -- to prevent.
    packet_id               uuid not null
                                references evidence_packets (id) on delete restrict,
    candidate_key           text not null,

    -- Brief §3: directly observed data, calculated patterns, model
    -- interpretation and hypotheses-needing-investigation must never be
    -- presented as the same kind of statement.
    claim_type              text not null check (claim_type in (
                                'observed', 'computed', 'interpreted', 'hypothesis')),
    finding_kind            text not null check (finding_kind in (
                                'existence', 'rate_comparison', 'association',
                                'share_of_presence', 'trend', 'absence',
                                'social_commerce_link', 'competitive_gap')),
    status                  text not null default 'draft' check (status in (
                                'draft', 'published', 'withheld', 'quarantined', 'refuted')),

    -- What the model wrote: prose with {{stat:S1}} and [E3] tokens where
    -- numbers and citations go. The model never emits a digit; code substitutes
    -- pre-formatted `display_value` strings and verifies every [E#] resolves.
    claim_template          text not null,
    claim_rendered          text not null
                                check (claim_rendered not like '%{{stat:%'),

    -- Brief §24/§28: a sample statement is not a market fact. The scope
    -- sentence is a column rather than a UI string so it cannot be dropped by a
    -- consumer that forgot about it, and §25 wants the refutation condition
    -- stated up front rather than reconstructed later.
    scope_statement         text not null,
    what_would_refute       text not null,
    caveats                 text[] not null default '{}',

    sample_size             integer not null check (sample_size >= 0),

    -- Separate from sample_size on purpose: 40 items from 3 creators found by
    -- 1 query is not 40 independent observations. Two-stage capping (unit 3,
    -- query 8) makes that explicit, and the gates in ARCHITECTURE §6.3 are
    -- stated on this value, never on sample_size.
    effective_sample_size   numeric not null check (effective_sample_size >= 0),
    design_effect           numeric check (design_effect >= 1),
    independence_ratio      numeric generated always as (
                                effective_sample_size / nullif(sample_size, 0)
                            ) stored,

    -- inverse-Simpson diversity per independence_unit / query / platform /
    -- strategy plus top-share values. jsonb because the band gates read a fixed
    -- handful of keys but the drill-down renders whatever was measured.
    concentration           jsonb not null default '{}',

    effect_metric           text,
    effect_value            numeric,
    effect_ci_low           numeric,
    effect_ci_high          numeric,
    effect_ci_method        text,

    raw_p                   numeric check (raw_p between 0 and 1),
    -- BH-adjusted within (run, finding_kind). Stored next to raw_p so a reader
    -- can see that multiplicity was corrected and over which family.
    adjusted_q              numeric check (adjusted_q between 0 and 1),

    confidence              numeric check (confidence between 0 and 1),
    confidence_band         text not null default 'insufficient' check (confidence_band in (
                                'strong', 'moderate', 'indicative', 'insufficient')),
    -- A recalibration must be distinguishable from a real movement, so a past
    -- claim re-reads under the scheme it was generated with.
    band_scheme_version     text not null default 'v1',

    -- Why this is not on screen: 'cluster_not_claimable',
    -- 'volume_claim_unidentified', 'commerce_temporal_unidentified',
    -- 'stratum_disagreement', 'retrieval_recency_artefact', ... Open text
    -- because refusal reasons are discovered as the pipeline meets new ways to
    -- be wrong.
    withheld_reason         text,

    -- Brief §28: conflicting evidence is surfaced on the finding. This is the
    -- flag the card reads; the detail is in finding_evidence(role) and
    -- finding_refutations.
    contested               boolean not null default false,
    adversarial_verdict     text not null default 'not_run' check (adversarial_verdict in (
                                'not_run', 'survives', 'narrowed', 'refuted')),

    generator_model         text,
    generator_version       text,

    created_at              timestamptz not null default now(),
    updated_at              timestamptz not null default now(),

    unique (research_run_id, candidate_key),

    -- n_eff can equal n but never exceed it; a violation means the independence
    -- computation was bypassed.
    constraint findings_n_eff_bounded
        check (effective_sample_size <= sample_size),
    -- ARCHITECTURE §6.1 L5: hypotheses get no publication score at all, because
    -- scoring one invites it being read as an established finding.
    constraint findings_hypotheses_unscored
        check (claim_type <> 'hypothesis' or confidence is null),
    -- Suppressing something silently is the failure mode brief §28 names; if it
    -- is not shown, the reason is on the row.
    constraint findings_suppression_explained
        check (status in ('draft', 'published') or withheld_reason is not null),
    -- 'insufficient' is the Insufficient-data drawer, not a finding card.
    constraint findings_published_band
        check (status <> 'published' or confidence_band <> 'insufficient'),
    constraint findings_effect_ci_paired
        check ((effect_ci_low is null) = (effect_ci_high is null))
);

create index findings_run_status_idx
    on findings (research_run_id, status, confidence_band);
create index findings_run_kind_idx
    on findings (research_run_id, finding_kind);
create index findings_packet_idx
    on findings (packet_id);
-- Brief §28's "allow conflicting evidence" is only honoured if contested
-- findings are cheap to list rather than cheap to forget.
create index findings_contested_idx
    on findings (research_run_id) where contested;

-- ---------------------------------------------------------------------------
-- finding_evidence — brief §27's drill-down substrate
-- ---------------------------------------------------------------------------
-- finding -> evidence -> the actual video / listing / review. One row per [E#]
-- token in the claim template.

create table finding_evidence (
    id                      uuid primary key default gen_random_uuid(),
    finding_id              uuid not null references findings (id) on delete cascade,

    -- The citation token the claim text carries. Validators resolve every [E#]
    -- in `claim_rendered` against this column; an unresolvable one is a
    -- fabricated citation and the finding is rejected before display.
    ev_ref                  text not null check (ev_ref ~ '^E[0-9]+$'),

    kind                    text not null check (kind in (
                                'content', 'commerce_item', 'commerce_review',
                                'cluster', 'creator', 'research_query')),

    -- Brief §28: contradicting and non-instance evidence is cited alongside
    -- supporting evidence rather than filtered out. 'confounded' marks an item
    -- that matches but cannot settle the claim (e.g. sponsored content in an
    -- organic-preference claim).
    role                    text not null check (role in (
                                'supporting', 'contradicting', 'non_instance',
                                'confounded', 'illustrative')),

    -- Polymorphic over `kind`; no FK is possible and none is wanted — the
    -- referential check belongs with the kind check, in the validators.
    ref_id                  uuid not null,
    -- Set for kind='cluster' so a cluster-backed number stays re-derivable
    -- after the cluster is re-cut. No FK: the clustering track owns that table.
    cluster_revision_id     uuid,

    -- Brief §28: an incidental appearance is not product usage. Carried per
    -- evidence row because one finding may legitimately mix the two, and the
    -- card must be able to say which.
    usage_type              text not null check (usage_type in (
                                'active_use', 'handled', 'incidental',
                                'mentioned_only', 'unknown')),

    -- NEVER null. Items with unresolvable provenance all share the single
    -- 'unknown_creator' unit; giving each its own would make unknown provenance
    -- read as maximal independence, which is the dangerous direction.
    independence_unit       text not null,

    platform                text,
    creator_ref             text,
    published_at            timestamptz,

    -- Six values, not D-1's five: `aypo/models/provenance.py::SamplingStrategy`
    -- also defines 'seed' and the live pydantic contract wins over the
    -- architecture's enum (divergence recorded here rather than resolved by
    -- breaking 104 passing tests). A text CHECK rather than the `0002`
    -- research_strategy enum for the same reason.
    sampling_strategy       text not null check (sampling_strategy in (
                                'brand', 'category', 'competitor',
                                'theme', 'hypothesis', 'seed')),
    discovery_query_id      uuid,

    -- The calibrated L1 confidence for the specific claim being cited, not for
    -- the item overall. Null where the kind has no per-claim calibration.
    analysis_confidence     numeric check (analysis_confidence between 0 and 1),
    sponsored_likelihood    numeric check (sponsored_likelihood between 0 and 1),

    -- The span the UI highlights. Offsets are into `excerpt_field` of the
    -- referenced row, so the drill-down can show the quote in place instead of
    -- asking the reader to find it.
    excerpt                 text,
    excerpt_field           text,
    excerpt_start           integer,
    excerpt_end             integer,

    created_iteration       integer not null default 0,
    created_at              timestamptz not null default now(),

    unique (finding_id, ev_ref),
    constraint finding_evidence_span_ordered
        check (excerpt_start is null or excerpt_end is null or excerpt_start <= excerpt_end)
);

create index finding_evidence_finding_role_idx
    on finding_evidence (finding_id, role);
-- The reverse drill-down: "which findings rest on this video?" — needed by the
-- Content Explorer and by any retraction that follows an item being withdrawn.
create index finding_evidence_ref_idx
    on finding_evidence (kind, ref_id);

-- ---------------------------------------------------------------------------
-- finding_statistics — every number as rendered
-- ---------------------------------------------------------------------------
-- One row per {{stat:S#}} token. `display_value` is the only string that ever
-- reaches a screen: formatting a float at render time is how a 0.5% rounds
-- itself into a different claim on a different surface.

create table finding_statistics (
    id                      uuid primary key default gen_random_uuid(),
    finding_id              uuid not null references findings (id) on delete cascade,

    stat_ref                text not null check (stat_ref ~ '^S[0-9]+$'),
    name                    text not null,
    label                   text not null,
    value                   double precision not null,
    display_value           text not null,

    unit                    text not null check (unit in (
                                'count', 'proportion', 'ratio', 'index',
                                'days', 'tau', 'rho', 'currency')),
    currency_code           text,

    n                       integer not null check (n >= 0),
    denominator             integer check (denominator >= 0),
    -- Brief §28 wants sample findings distinguished from market-wide facts; a
    -- denominator nobody can describe in a sentence is how that line gets
    -- crossed, so the description is NOT NULL.
    denominator_desc        text not null,

    n_eff                   double precision check (n_eff >= 0),
    design_effect           double precision check (design_effect >= 1),

    ci_low                  double precision,
    ci_high                 double precision,
    ci_method               text not null check (ci_method in (
                                'wilson_deff', 'cluster_bootstrap', 'cluster_t',
                                'mk_variance_blocked', 'permutation', 'none')),

    -- The arithmetic, in readable form, so the drill-down can show how the
    -- number was reached rather than asserting it.
    formula                 text not null,
    assertion_domain        text not null check (assertion_domain in (
                                'corpus_composition', 'content_performance',
                                'commerce_listing', 'commerce_review',
                                'temporal', 'cross_corpus')),

    -- The only entities a sentence citing this stat may name. A stat computed
    -- over the category stratum may not be used to name a single brand.
    subject_refs            text[] not null default '{}',

    -- A lifetime counter (views, total sold) can only go up, so it is never a
    -- valid input to a trend; the flag travels with the stat rather than with
    -- the caller that happens to remember.
    is_cumulative           boolean not null default false,
    -- How many candidate stats this one was chosen from — the multiplicity the
    -- BH correction on `findings.adjusted_q` has to account for.
    selected_from_k         integer not null default 1 check (selected_from_k >= 1),

    recompute_key           text not null,
    recompute_params        jsonb not null default '{}',

    created_at              timestamptz not null default now(),

    unique (finding_id, stat_ref),
    constraint finding_statistics_currency_present
        check (unit <> 'currency' or currency_code is not null),
    constraint finding_statistics_ci_paired
        check ((ci_low is null) = (ci_high is null)),
    constraint finding_statistics_ci_none_empty
        check (ci_method <> 'none' or ci_low is null),
    constraint finding_statistics_ci_ordered
        check (ci_low is null or ci_low <= ci_high),
    -- A plain Wilson interval on clustered data is too narrow and reads as more
    -- certainty than the sample supports, so a proportion's interval must come
    -- from a design-effect-corrected method.
    constraint finding_statistics_proportion_interval
        check (unit <> 'proportion'
               or ci_method in ('wilson_deff', 'cluster_bootstrap', 'none'))
);

-- ---------------------------------------------------------------------------
-- finding_validations — the validator trail
-- ---------------------------------------------------------------------------
-- Keyed on the PACKET, not the finding: a candidate killed by V6 never becomes
-- a finding, and the reason it was killed is exactly what has to survive.

create table finding_validations (
    id                      uuid primary key default gen_random_uuid(),
    packet_id               uuid not null
                                references evidence_packets (id) on delete cascade,
    -- Null while the candidate is still a candidate.
    finding_id              uuid references findings (id) on delete cascade,

    check_code              text not null,
    check_name              text not null,
    outcome                 text not null check (outcome in (
                                'pass', 'fail', 'warn', 'skipped', 'not_applicable')),
    -- 'blocking' failures stop publication; 'advisory' ones become caveats.
    -- Recording which checks merely *ran* matters as much as which failed: a
    -- check that silently stopped running is indistinguishable from a passing
    -- one otherwise.
    severity                text not null default 'advisory'
                                check (severity in ('blocking', 'advisory')),

    detail                  text,
    -- The ev_ref / stat_ref / sentence the check tripped on.
    offending_ref           text,
    validator_version       text not null,
    ran_at                  timestamptz not null default now()
);

create index finding_validations_packet_idx
    on finding_validations (packet_id);
create index finding_validations_finding_idx
    on finding_validations (finding_id);
create index finding_validations_failures_idx
    on finding_validations (packet_id, check_code) where outcome = 'fail';

-- ---------------------------------------------------------------------------
-- finding_refutations — the adversarial pass
-- ---------------------------------------------------------------------------
-- Brief §28: conflicting evidence is surfaced on the finding, not suppressed.
-- An objection that was raised and then overruled is still shown in the
-- drill-down, because "we considered this and why we kept the claim" is the
-- honest form of that.

create table finding_refutations (
    id                      uuid primary key default gen_random_uuid(),
    finding_id              uuid not null references findings (id) on delete cascade,

    -- Two independent passes are required before a finding may reach the
    -- 'strong' band; the number distinguishes them.
    pass_no                 smallint not null default 1 check (pass_no >= 1),

    objection               text not null,
    -- Free text, no CHECK: an enum here would quietly bound what a referee is
    -- allowed to notice, which is the opposite of the point.
    objection_kind          text,
    severity                text not null check (severity in ('minor', 'serious', 'fatal')),
    verdict                 text not null check (verdict in ('survives', 'narrow', 'refute')),

    -- The referee cites packet evidence ids under the same rules as the
    -- generator; an objection resting on nothing is not an objection.
    cites_ev_refs           text[] not null default '{}',
    verdict_confidence      numeric check (verdict_confidence between 0 and 1),

    -- What the pipeline did about it. 'open' means it is displayed as an
    -- unresolved caveat rather than silently dropped.
    resolution              text not null default 'open' check (resolution in (
                                'accepted', 'rejected', 'narrowed', 'open')),

    referee_model           text,
    created_at              timestamptz not null default now()
);

create index finding_refutations_finding_idx
    on finding_refutations (finding_id, pass_no);

-- ---------------------------------------------------------------------------
-- Ask the Data (brief §31.7)
-- ---------------------------------------------------------------------------

create table ask_sessions (
    id                      uuid primary key default gen_random_uuid(),
    -- An answer is only meaningful against a specific corpus, so a session is
    -- bound to one run rather than to "the data" in general.
    research_run_id         uuid not null
                                references research_runs (id) on delete cascade,
    -- D-31 single-tenant MVP: the tenancy key is the creating user; there is no
    -- org_id anywhere in this schema.
    created_by              uuid,

    title                   text,
    status                  text not null default 'active'
                                check (status in ('active', 'closed', 'failed')),
    -- Per-session ceiling on tool turns, stored rather than read from config so
    -- a past session's behaviour stays explicable after the config changes.
    max_tool_turns          integer not null default 8 check (max_tool_turns >= 1),
    total_cost_usd          numeric not null default 0,

    created_at              timestamptz not null default now(),
    updated_at              timestamptz not null default now()
);

create index ask_sessions_run_idx on ask_sessions (research_run_id, created_at desc);

create table ask_messages (
    id                      uuid primary key default gen_random_uuid(),
    session_id              uuid not null references ask_sessions (id) on delete cascade,
    seq                     integer not null check (seq >= 0),
    role                    text not null check (role in ('user', 'assistant', 'system')),
    content                 text not null,

    -- Share of the answer's assertions that resolve to a row actually returned
    -- by a tool call in this turn. The whole point of measuring it is that an
    -- answer drifting into general world knowledge about the market is exactly
    -- the failure brief §1 forbids, and it is invisible unless counted.
    grounding_ratio         numeric check (grounding_ratio between 0 and 1),

    -- The agent must be able to say the data is insufficient instead of
    -- answering from general knowledge. Making that a value of answered_from —
    -- rather than a turn of phrase inside `content` — is what lets us measure
    -- how often it happens and lets the UI render it as a distinct state.
    answered_from           text not null default 'n_a' check (answered_from in (
                                'dataset', 'insufficient_data', 'refused', 'n_a')),
    insufficiency_reason    text,

    -- [{kind, ref_id, label}] — what the answer points at. jsonb rather than a
    -- fourth table: a citation here is a pointer for the reader, not an
    -- evidence row with a role and an independence unit.
    citations               jsonb not null default '[]',

    -- Brief §31.7 requires an answer to carry its sample size; n_eff is the
    -- operative one for the same reason it is on `findings`.
    n                       integer check (n >= 0),
    n_eff                   numeric check (n_eff >= 0),

    model                   text,
    input_tokens            integer,
    output_tokens           integer,
    cost_usd                numeric,
    created_at              timestamptz not null default now(),

    unique (session_id, seq),
    -- Grounding is a property of a generated answer; a user's question has no
    -- grounding ratio and storing 0 for one would poison the aggregate.
    constraint ask_messages_grounding_is_assistant
        check (role = 'assistant' or grounding_ratio is null),
    constraint ask_messages_insufficiency_explained
        check (answered_from <> 'insufficient_data' or insufficiency_reason is not null)
);

create index ask_messages_session_idx on ask_messages (session_id, seq);

create table ask_tool_calls (
    id                      uuid primary key default gen_random_uuid(),
    message_id              uuid not null references ask_messages (id) on delete cascade,
    turn_no                 integer not null check (turn_no >= 1),

    tool_name               text not null,
    arguments               jsonb not null default '{}',

    -- The statement actually executed. An answer nobody can re-run is an
    -- opinion, and brief §27's "never a black box" applies to Ask the Data as
    -- much as to a finding card.
    sql_text                text,
    result_summary          text,
    row_count               integer,

    -- 'blocked' is a first-class outcome: a tool call refused by the guard
    -- (unsafe SQL, a group below min_group_n) is information about the answer,
    -- not an error to swallow.
    status                  text not null check (status in ('ok', 'error', 'blocked')),
    error                   text,
    duration_ms             integer,
    created_at              timestamptz not null default now(),

    unique (message_id, turn_no)
);

create index ask_tool_calls_message_idx on ask_tool_calls (message_id, turn_no);

-- ===========================================================================
-- DELIBERATELY DEFERRED
-- ===========================================================================
-- Recorded so the gap is visible rather than lost. Brief §37 ranks speed to a
-- usable MVP above modular architecture, and §38 wants the end-to-end flow
-- before the sophistication; everything below is written by a subsystem this
-- wave does not build. Nothing here is renamed or re-keyed — adding it later is
-- an additive migration.
--
-- TABLES
--   finding_links         (ARCHITECTURE §2.11) — finding-to-finding relations
--                         (supports / contradicts / supersedes / refines).
--                         Consequence: `findings.status` has no 'superseded'
--                         value in this wave, because there would be nothing to
--                         point at. Adding the table adds the status.
--   finding_quarantine    (ARCHITECTURE §2.11) — the holding area for candidates
--                         tripped by V6 (a contradicting item among the cited).
--                         Interim behaviour: `findings.status='quarantined'`
--                         plus the failing row in `finding_validations`, which
--                         carries the same information without the review
--                         workflow.
--   v_finding_provenance  (ARCHITECTURE §2.11) — the security_invoker view
--                         joining finding -> evidence -> item -> query. The API
--                         joins these four tables directly for now.
--   calibration_labels / calibration_models (ARCHITECTURE §2.5) — owned by the
--                         analysis track, not by this file. Until they exist,
--                         §6.5's claim-level calibration is uncalibrated and
--                         `band_scheme_version` is the only version marker here.
--
-- COLUMNS
--   evidence_packets      no per-iteration column. D-41 enumerates iteration
--                         columns for `findings` and `finding_evidence` only;
--                         a packet's iteration is reachable through either.
--   findings              no human-review columns (reviewer, review_state,
--                         review_note — ARCHITECTURE §2.12 "findings
--                         machinery"): nothing in this wave presents a review
--                         queue. No `replication_status` (§6.1 / D-45) because
--                         cross-run comparison needs two runs and a frozen
--                         reference query set, neither of which exists yet.
--                         No per-gate pass/fail columns for §6.3's twelve
--                         `strong` gates: the inputs live in `concentration`
--                         and the verdicts in `finding_validations`, so adding
--                         twelve booleans would be a third copy.
--   finding_evidence      no `query_vintage_half` (D-32 T4) and no
--                         `collection_provider` / `supports_properties`: all
--                         three are consumed only by the trend artefact guards
--                         and the packet-level diagnostics, both of which read
--                         `evidence_packets.items` where they are already
--                         stored verbatim.
--   finding_statistics    no recompute audit row (recomputed_at, recomputed_value,
--                         drift) — StatRecomputer is a release-gate tool, not
--                         part of the first end-to-end flow. `recompute_key`
--                         and `recompute_params` are kept so a later recompute
--                         needs no backfill.
--   ask_messages          no embedding column for semantic search over past
--                         sessions (brief §31.5 semantic search is over content,
--                         not over the chat log).
--
-- POLICY
--   Row-level security    D-31 specifies RLS gating on `created_by = auth.uid()`.
--                         `auth.uid()` is a Supabase function and does not exist
--                         on the local Postgres 17 this build runs against
--                         (docs/ENVIRONMENT.md), so enabling it here would make
--                         the migration un-appliable in development. It belongs
--                         in the deployment migration that also enables RLS on
--                         `research_runs`; the tenancy key (`ask_sessions.created_by`)
--                         is already in place for it.
--
-- DIVERGENCES FROM ARCHITECTURE.md, taken deliberately
--   File number           ARCHITECTURE D-43 calls this `0010_findings.sql`;
--                         this reduced build numbers it `0006`.
--   sampling_strategy     D-1's `research_strategy` enum has five values;
--                         `aypo/models/provenance.py::SamplingStrategy` has six
--                         ('seed' as well) and is the live contract that
--                         existing code and tests depend on. The CHECK above
--                         follows the code. A text CHECK is also used in place
--                         of the enum so this file does not depend on migration
--                         `0002` creating a type.
--   platform              D-2 makes `platform` free text validated against
--                         `collection_sources`; `finding_evidence.platform`
--                         therefore carries no CHECK here, and note that the
--                         existing `Platform` enum spells the marketplaces
--                         `shopee` / `lazada` rather than D-2's `shopee_ph` /
--                         `lazada_ph`. Reconciling those two spellings is the
--                         collection track's call, not this file's.
