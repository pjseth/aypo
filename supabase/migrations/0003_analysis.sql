-- 0003_analysis.sql
--
-- Per-item multimodal analysis (ARCHITECTURE.md §2.5, doc 03 §10) plus the
-- open-world brand registry (§2.4, doc 06 §5.1), merged into one file because
-- brand resolution reads analysis output and D-38 fixes that order
-- (analysis -> resolve_brands). D-43's one-file-per-track rule is relaxed only
-- here; nothing in this file touches another track's tables.
--
-- Two things in this file are load-bearing and easy to get wrong later:
--
--   * `analysis_configs` is what makes two analyses comparable. Without a row
--     pinning prompt, model, effort, frame policy and ASR policy, a corpus
--     silently mixes outputs from different instruments and every §28 sample
--     size is a figure about nothing.
--   * `brand_surface_observations` is the unit of record for brands. A brand id
--     is never stamped onto a content row; it is attached to the *observation*
--     of a surface form, so a wrong merge is undone by rewriting one column on
--     one observation rather than by re-analysing the corpus. Brief §23 and §1
--     require discovery to stay open, and an irreversible resolution closes it.
--
-- Shape follows aypo/analysis/schema.py (ItemAnalysis, AnalysisRecord) and
-- aypo/providers/transcription.py (Transcript) as they exist today, not the
-- richer VideoAnalysis in ARCHITECTURE.md §4.4. Divergences are flagged inline
-- and the deferral register at the foot of the file names everything omitted.


create extension if not exists vector;         -- 1536-d alias embeddings (HNSW caps at 2000)
create extension if not exists pg_trgm;        -- surface-form fuzzy match on OCR-mangled text
create extension if not exists fuzzystrmatch;  -- phonetic keys for spoken brand mentions


-- ---------------------------------------------------------------------------
-- analysis_configs
-- ---------------------------------------------------------------------------

create table analysis_configs (
  id                  uuid        primary key default gen_random_uuid(),
  prompt_version      text        not null,
  -- Hash over every input that can change an output: prompt bytes, model,
  -- effort, max_tokens, frame policy, ASR policy, validator version. It is the
  -- idempotency key for content_analysis, so two configs that would produce
  -- comparable results must hash the same and two that would not must not.
  config_fingerprint  char(64)    not null unique,
  model               text        not null,
  effort              text        not null check (effort in ('low','medium','high','xhigh','max')),
  max_tokens          integer     not null check (max_tokens between 1024 and 128000),
  frame_policy        jsonb       not null,
  frame_policy_hash   char(64)    not null,
  asr_config          jsonb       not null,
  asr_config_hash     char(64)    not null,
  -- Stored verbatim, not by reference. ENVIRONMENT.md measured that this
  -- prompt is the request's cache prefix and must stay byte-stable; keeping
  -- the exact bytes that produced an analysis is the only way to prove later
  -- that it did.
  system_prompt       text        not null,
  validator_version   text        not null,
  is_active           boolean     not null default false,
  created_at          timestamptz not null default now()
);

-- At most one active config, enforced by the database rather than by
-- convention: two active configs would make "the current analysis" ambiguous
-- and silently mix instruments inside a single run.
create unique index analysis_configs_single_active
  on analysis_configs ((is_active)) where is_active;
create index analysis_configs_prompt_version on analysis_configs (prompt_version);


-- ---------------------------------------------------------------------------
-- content_frames
-- ---------------------------------------------------------------------------

create table content_frames (
  id                 bigserial   primary key,
  content_item_id    uuid        not null references content_items(id) on delete cascade,
  -- Part of the key, not metadata: changing the frame policy produces a
  -- different sample of the same video, and both samples must be able to
  -- coexist while an old analysis is still being read.
  frame_policy_hash  char(64)    not null,
  frame_index        smallint    not null check (frame_index >= 0),
  t_s                numeric(10,3),
  scene_score        real,
  -- Narrowed to the three reasons analysis/media.py actually emits; the two
  -- further reasons in doc 03 §10.2 belong to deferred selectors (see register).
  selection_reason   text        not null check (selection_reason in
                       ('bucket_scene_max','bucket_midpoint','thumbnail')),
  -- Set when the minimum-gap pass could not separate this frame from its
  -- predecessor, i.e. the pair is near-identical. Recorded because it is a
  -- direct discount on how much independent visual evidence the item carries.
  gap_violated       boolean     not null default false,
  width              smallint    not null,
  height             smallint    not null,
  image_tokens       smallint    not null,
  -- Named for the pluggable backend in providers/storage.py (local or S3),
  -- not `s3_key` as in doc 03 §10.2: the development backend is a filesystem
  -- and a column name that lies about that invites an S3 assumption in code.
  storage_key        text        not null,
  bytes              integer     not null,
  sha256             char(64)    not null,
  created_at         timestamptz not null default now(),
  unique (content_item_id, frame_policy_hash, frame_index)
);
create index content_frames_item on content_frames (content_item_id);


-- ---------------------------------------------------------------------------
-- content_transcripts
-- ---------------------------------------------------------------------------

create table content_transcripts (
  content_item_id   uuid          not null references content_items(id) on delete cascade,
  asr_config_hash   char(64)      not null,
  asr_model         text          not null,
  has_speech        boolean       not null,
  -- FREE TEXT, deliberately never an enum. ASR auto-detected a language the
  -- pipeline had never named, on a clip whose speech was code-switched mid
  -- utterance. Constraining this column to a language list would force a lossy
  -- cast of exactly the signal brief §18 asks the product to surface, and would
  -- also pin a market the system is supposed to discover rather than assume.
  language_detected text,
  duration_s        numeric(10,3),
  text              text          not null default '',
  -- [{start, end, text}] from Transcript.segments. Timestamped segments are
  -- what let a visual claim be anchored to what was being said at that moment,
  -- so they are stored rather than being collapsed into `text`.
  segments          jsonb         not null default '[]'::jsonb,
  -- D-37. NULL means the speech/lyrics discriminator has not run, which is a
  -- different state from 'none' (no transcript at all). A consumer seeing NULL
  -- must apply D-37's restrictions conservatively: music lyrics are sound
  -- context, not a creator claim, and are third-party copyrighted text that
  -- may never be surfaced verbatim.
  transcript_kind   text          check (transcript_kind in
                      ('speech','music_lyrics','mixed','none')),
  cost_usd          numeric(10,6),
  created_at        timestamptz   not null default now(),
  primary key (content_item_id, asr_config_hash)
);


-- ---------------------------------------------------------------------------
-- content_analysis
-- ---------------------------------------------------------------------------

create table content_analysis (
  id                     uuid        primary key default gen_random_uuid(),
  content_item_id        uuid        not null references content_items(id) on delete cascade,
  analysis_config_id     uuid        not null references analysis_configs(id),
  -- The FIRST run that produced this row. An analysis is reused when a later
  -- run re-collects the same item, so this is provenance, NOT a per-run
  -- denominator; see the register for the junction that makes those correct.
  research_run_id        uuid        references research_runs(id),
  media_tier             text        not null check (media_tier in
                           ('full_video','audio_only','thumbnail_only','metadata_only')),
  status                 text        not null check (status in
                           ('ok','refused','refused_terminal','truncated',
                            'schema_error','media_error','skipped','deferred')),
  skip_reason            text,
  stop_reason            text,
  refusal_explanation    text,
  frame_policy_hash      char(64),
  asr_config_hash        char(64),
  -- Over SOURCE bytes, so re-encoding the same download does not invalidate an
  -- analysis while a genuinely different upload does.
  media_fingerprint      char(64),
  frame_count            smallint,                 -- AnalysisRecord.frames_analysed
  had_transcript         boolean     not null default false,
  -- Share of frame buckets that fell back to the midpoint: the cheapest proxy
  -- for static single-take versus cut-heavy production, which bounds how
  -- reliably a brand can be read off the frames. Kept so comparisons can
  -- stratify on it instead of pooling two different evidence regimes.
  midpoint_fraction      real,
  payload                jsonb,                    -- the full validated ItemAnalysis
  summary                text,
  evidence_insufficient_for text[]   not null default '{}',
  -- The validator's two outcomes, as emitted by AnalysisRecord. Brief §28
  -- requires insufficient evidence be stated rather than assumed, and that is
  -- only true if a dropped claim leaves a trace: without these, a clean-looking
  -- analysis and a heavily-pruned one are indistinguishable.
  dropped_claims         text[]      not null default '{}',
  demoted_claims         text[]      not null default '{}',
  input_tokens           integer,
  -- `cache_creation_tokens` follows the Anthropic usage field name; the Spend
  -- dataclass in llm/pricing.py calls the same quantity `cache_write_tokens`.
  cache_read_tokens      integer,
  cache_creation_tokens  integer,
  output_tokens          integer,
  image_tokens           integer,
  cost_usd               numeric(10,6),
  latency_ms             integer,
  anthropic_message_id   text,
  created_at             timestamptz not null default now()
);

-- Idempotency. coalesce() rather than a plain unique so items with no
-- retrievable media (thumbnail-only, metadata-only) still collapse to one row
-- per config instead of inserting a duplicate on every retry.
create unique index content_analysis_idem
  on content_analysis (content_item_id, analysis_config_id, coalesce(media_fingerprint, ''));
create index content_analysis_item   on content_analysis (content_item_id);
create index content_analysis_run    on content_analysis (research_run_id, status);
create index content_analysis_status on content_analysis (analysis_config_id, status);
create index content_analysis_payload_gin on content_analysis using gin (payload jsonb_path_ops);

-- Reading this instead of the base table is what keeps a figure from silently
-- averaging over two prompt versions.
create view content_analysis_current with (security_invoker = true) as
select ca.*
  from content_analysis ca
  join analysis_configs ac on ac.id = ca.analysis_config_id
 where ac.is_active;


-- ---------------------------------------------------------------------------
-- content_analysis_claims
-- ---------------------------------------------------------------------------
--
-- The §27 drill-down substrate: finding -> evidence -> the frame or timecode a
-- marketer can go and check. One row per claim so label frequency, brand
-- co-appearance and consumption-state counts are index scans rather than JSONB
-- unpacking over the whole corpus.

create table content_analysis_claims (
  id                    bigserial   primary key,
  analysis_id           uuid        not null references content_analysis(id) on delete cascade,
  -- Denormalised from content_analysis so the common aggregations never join.
  content_item_id       uuid        not null,
  analysis_config_id    uuid        not null,
  -- Which part of ItemAnalysis the row came from: 'visible_brands',
  -- 'mentioned_brands', 'visible_products', 'sponsorship', or a Facet value.
  field                 text        not null,
  field_group           text        not null check (field_group in
                          ('brand_identity','product_use','sponsorship',
                           'discovered_label','descriptor')),
  -- Set only for discovered-label rows, carrying Label.facet verbatim. Our
  -- ItemAnalysis puts every label in ONE list discriminated by this enum
  -- (ENVIRONMENT.md: thirteen per-facet lists blew the structured-output
  -- grammar limit), so `field` repeats it for those rows. It is kept as its own
  -- nullable column because taxonomy ingest validates facets against a lookup
  -- table and must not have to parse `field` to tell a label from a brand.
  facet                 text,
  ordinal               smallint    not null,
  value                 text        not null,      -- verbatim; never normalised on the way in
  value_norm            text        generated always as (lower(btrim(value))) stored,
  basis                 text        not null check (basis in ('seen','heard','written','inferred')),
  cue                   text        not null,
  -- Parsed anchors, as produced by parse_anchor() from the model's compact
  -- strings. Stored parsed rather than raw because the raw form is a wire
  -- encoding, and every stored anchor has already been checked against the
  -- material that was actually supplied.
  anchors               jsonb       not null,
  anchor_count          smallint    not null,
  modality_count        smallint    not null,
  first_frame_index     smallint,
  t_start_s             numeric(10,3),
  -- Check A on a frame anchor is a bounds check only -- the index exists, which
  -- is not the same as the object being there. Aggregation must be able to
  -- discount a claim whose only support is a frame index.
  frame_anchor_only       boolean   not null default false,
  -- Anchored solely to the platform cover image, which is chosen for clicks and
  -- is not in-video evidence.
  thumbnail_only_anchored boolean   not null default false,
  -- Brief §28's incidental-appearance-versus-active-use distinction, on
  -- product rows. A real column rather than a key in `extra` because it is one
  -- of the few things the whole product turns on, and burying it in JSONB makes
  -- "how often is this actually consumed" an expensive question.
  consumption_state     text        check (consumption_state in
                          ('present','held','being_opened','shown_to_camera',
                           'in_use_or_consumed','offered_or_shared','unclear')),
  -- The validator demotes rather than drops when the object really was present
  -- but the action was not evidenced (one frame cannot show a process; speech
  -- cannot evidence a visual act), and it demotes an unsupported sponsorship
  -- bucket to undetermined. Both are recorded: a demotion is a measured
  -- insufficient-evidence event, and §28 counts are only real if it survives.
  demoted_from          text,
  demotion_reason       text,
  -- Set when a claim was kept as a row but failed validation, with the
  -- validator's own reason ('no valid anchor', 'cue restates claim', ...).
  -- Today's validator discards the object and keeps only the reason string on
  -- content_analysis.dropped_claims; this is the slot for the claim-level
  -- writer, and 'unverified' is reserved for the deferred calibrator.
  claim_status          text        not null check (claim_status in
                          ('published','unverified','rejected')),
  rejection_reason      text,
  extra                 jsonb       not null default '{}'::jsonb,
  created_at            timestamptz not null default now(),
  unique (analysis_id, field, ordinal)
);

create index claims_published_value
  on content_analysis_claims (field, value_norm) where claim_status = 'published';
create index claims_item         on content_analysis_claims (content_item_id);
create index claims_group_status on content_analysis_claims (field_group, claim_status);
create index claims_facet
  on content_analysis_claims (facet, value_norm)
  where facet is not null and claim_status = 'published';
create index claims_consumption
  on content_analysis_claims (consumption_state)
  where consumption_state is not null and claim_status = 'published';


-- ---------------------------------------------------------------------------
-- Brand registry
-- ---------------------------------------------------------------------------
--
-- Open-world by construction. NO seed brand list ships in this migration or
-- anywhere else: brief §1 and §34 forbid encoding the answer, and brief §23
-- allows an operator-supplied seed only as a recall hint with zero scoring
-- privilege. Every row here originates in an observed surface form.

create type brand_level  as enum ('company','brand','sub_brand','variant');
create type brand_status as enum ('provisional','confirmed','rejected','merged');
create type brand_origin as enum ('discovered','seed','manual');
create type alias_channel as enum ('packaging_vision','spoken','written','commerce_title',
                                   'commerce_brand_field','web_text','seed','manual');
create type surface_resolution_state as enum ('resolved','pending_support','ambiguous',
                                              'rejected_noise','new_brand_created');

create table brands (
  id                      uuid         primary key default gen_random_uuid(),
  canonical_name          text         not null,
  norm_name               text         not null,
  -- Case- and separator-folded form with confusable glyphs collapsed. Packaging
  -- read off a frame is the highest-volume brand channel and it arrives
  -- mangled, so the skeleton is the join key that actually fires.
  ocr_skeleton            text         not null,
  -- Spoken mentions are mangled differently from printed ones: Whisper returns
  -- a phonetic approximation, which no OCR skeleton will match.
  phonetic_key            text,
  brand_level             brand_level  not null default 'brand',
  parent_brand_id         uuid         references brands(id) on delete restrict,
  root_company_id         uuid         references brands(id) on delete restrict,
  ancestor_ids            uuid[]       not null default '{}',
  depth                   smallint     not null default 0 check (depth between 0 and 3),
  status                  brand_status not null default 'provisional',
  origin                  brand_origin not null default 'discovered',
  -- The research subject, set from run configuration rather than by name in
  -- code, so comparisons can say "subject versus discovered others" without a
  -- brand name appearing anywhere in the source.
  is_subject              boolean      not null default false,
  distinct_item_count     int          not null default 0,
  first_seen_at           timestamptz  not null default now(),
  last_seen_at            timestamptz  not null default now(),
  created_in_run_id       uuid,
  merged_into_brand_id    uuid         references brands(id),
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

-- NULLS NOT DISTINCT so a NULL parent means "top level" exactly once per name,
-- rather than admitting unlimited duplicate top-level brands.
create unique index brands_norm_parent_uq
  on brands (norm_name, parent_brand_id) nulls not distinct
  where status <> 'merged';
create index brands_skeleton_idx  on brands (ocr_skeleton) where status = 'confirmed';
create index brands_ancestors_idx on brands using gin (ancestor_ids);
create index brands_root_idx      on brands (root_company_id) where status = 'confirmed';
create index brands_status_idx    on brands (status, distinct_item_count desc);

-- Ancestry is derived in a trigger so the denormalised path can never disagree
-- with parent_brand_id. The cycle check is here rather than in application code
-- because a cycle makes the roll-up view recurse forever.
create function brands_sync_ancestry() returns trigger
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


create table brand_aliases (
  id                  uuid          primary key default gen_random_uuid(),
  brand_id            uuid          not null references brands(id) on delete cascade,
  surface_raw         text          not null,
  surface_norm        text          not null,
  ocr_skeleton        text          not null,
  phonetic_key        text,
  alias_kind          text          not null check (alias_kind in
                        ('canonical','variant_spelling','ocr_form','abbreviation',
                         'misspelling','translit','segmented_hashtag','seed')),
  channel             alias_channel not null,
  -- An ambiguous or blocked alias stays on the record instead of being deleted:
  -- knowing that a surface form was seen and deliberately not resolved is the
  -- difference between "no evidence" and "evidence we could not attribute".
  is_ambiguous        boolean       not null default false,
  blocked             boolean       not null default false,
  embedding           vector(1536),
  confidence          numeric(4,3)  not null default 0.500,
  resolved_by         text          not null check (resolved_by in
                        ('exact','containment','fuzzy','embedding_llm','llm','seed','manual')),
  first_seen_at       timestamptz   not null default now(),
  last_seen_at        timestamptz   not null default now()
);
create unique index brand_aliases_uq   on brand_aliases (surface_norm, brand_id);
create index brand_aliases_norm_idx    on brand_aliases (surface_norm)  where not blocked;
create index brand_aliases_skel_idx    on brand_aliases (ocr_skeleton)  where not blocked;
create index brand_aliases_phon_idx    on brand_aliases (phonetic_key)  where not blocked;
create index brand_aliases_trgm_idx    on brand_aliases using gin (surface_norm gin_trgm_ops);
create index brand_aliases_emb_idx     on brand_aliases using hnsw (embedding vector_cosine_ops)
                                          with (m = 16, ef_construction = 64);


create table brand_surface_observations (
  id                  uuid          primary key default gen_random_uuid(),
  research_run_id     uuid          not null,
  -- Polymorphic by design and without an FK: the same registry serves content,
  -- commerce and web sources, which are owned by three different migrations.
  source_kind         text          not null check (source_kind in
                        ('content_item','commerce_item','commerce_review','web_document')),
  source_id           uuid          not null,
  channel             alias_channel not null,
  surface_raw         text          not null,   -- exactly the characters observed
  surface_norm        text          not null,
  ocr_skeleton        text          not null,
  -- From BrandSighting.legibility. doc 06 has no such column and ARCHITECTURE
  -- §4.4 spells the top value 'clear'; aypo/analysis/schema.py spells it
  -- 'full', and the live code wins. It is here because a partially resolved
  -- wordmark must be visibly weaker evidence than a fully legible one --
  -- otherwise the cascade cannot prefer leaving it unresolved.
  legibility          text          check (legibility in ('full','partial','illegible')),
  context_snippet     text,
  frame_ts_ms         int,
  transcript_span_ms  int4range,
  char_span           int4range,
  -- The resolution decision lives HERE, on the observation, never on the
  -- content row. Reversing a wrong merge is then an update of brand_id on the
  -- affected observations; nothing downstream has to be re-analysed.
  resolution_state    surface_resolution_state not null default 'pending_support',
  brand_id            uuid          references brands(id) on delete set null,
  alias_id            uuid          references brand_aliases(id) on delete set null,
  match_tier          text,
  match_score         numeric(4,3),
  -- Distance to the runner-up. A high score with a thin margin is a coin flip
  -- between two real brands, and brief §28 prefers an admitted ambiguity to a
  -- confident mis-attribution, so the loser is kept rather than discarded.
  margin              numeric(4,3),
  runner_up_brand_id  uuid          references brands(id) on delete set null,
  runner_up_score     numeric(4,3),
  prominence          text          check (prominence in ('primary','secondary','incidental')),
  mention_source      text          check (mention_source in
                        ('creator_speech','caption','on_screen_text','hashtag','comment',
                         'packaging','listing_title','listing_brand_field','web_body')),
  reason              text,
  decided_by          text          check (decided_by in ('cascade','llm','human')),
  decided_at          timestamptz,
  created_at          timestamptz   not null default now()
);

-- One observation per (source, channel, surface, position). Re-running analysis
-- on an item must not inflate the evidence count for a brand.
create unique index bso_dedupe_uq on brand_surface_observations
  (source_kind, source_id, channel, surface_norm, coalesce(frame_ts_ms, -1),
   coalesce(lower(char_span), -1));
create index bso_norm_idx  on brand_surface_observations (surface_norm);
create index bso_open_idx  on brand_surface_observations (resolution_state, surface_norm)
                              where resolution_state <> 'resolved';
create index bso_brand_idx on brand_surface_observations (brand_id, source_kind, source_id)
                              where resolution_state = 'resolved';
create index bso_run_idx   on brand_surface_observations (research_run_id);

-- The only sanctioned way for another subsystem to read resolved brands. It
-- rolls each observation up to the analysis level, so a variant sighting counts
-- toward its brand-level ancestor instead of fragmenting the brand's evidence
-- across nodes. `resolved_brand_id` is kept beside it so drill-down can still
-- show the exact node that was observed.
create view v_item_brands with (security_invoker = true) as
with lvl as (select 'brand'::brand_level as analysis_level)
select o.source_kind,
       o.source_id,
       coalesce(anc.id, b.id)                           as brand_id,
       b.id                                             as resolved_brand_id,
       coalesce(anc.root_company_id, b.root_company_id) as root_company_id,
       max(case o.prominence when 'primary' then 3
                             when 'secondary' then 2 else 1 end) as prominence_rank,
       array_agg(distinct o.channel)                    as channels
  from brand_surface_observations o
  join brands b on b.id = o.brand_id
 cross join lvl
  left join lateral (
       select a.id, a.root_company_id
         from brands a
        where a.id = any(b.ancestor_ids)
          and a.brand_level = lvl.analysis_level
          and a.status = 'confirmed'
        order by a.depth desc
        limit 1
  ) anc on true
 where o.resolution_state = 'resolved'
   and b.status = 'confirmed'
 group by 1, 2, 3, 4, 5;


-- Ships EMPTY and must stay empty in source control. A seed is an operator's
-- guess supplied at run time; brief §23 permits it only as a recall hint, and
-- brief §1/§34 forbid it from ever becoming an answer. The three permitted uses
-- are alias bootstrap at a channel prior below every observed channel, a
-- rate-limited query hint that may not run before category-level discovery, and
-- the recall audit below. A seed never earns a relationship, a confidence floor
-- or enumeration preference.
create table brand_seeds (
  id                uuid        primary key default gen_random_uuid(),
  research_run_id   uuid        not null,
  supplied_name     text        not null,
  supplied_norm     text        not null,
  seed_role         text        not null check (seed_role in
                      ('subject','competitor_hint','exclude')),
  resolved_brand_id uuid        references brands(id) on delete set null,
  -- 'observed_zero' is the point of this table: a supplied competitor that
  -- never appears in the collected corpus is reported as "supplied but not
  -- observed (n=0)", which is a genuine finding about the operator's
  -- assumptions rather than a miss to be hidden.
  resolution_state  text        not null default 'unmatched'
                    check (resolution_state in ('unmatched','matched_existing','created',
                                                'ambiguous','observed_zero')),
  confirmed_by_evidence boolean not null default false,
  evidence_count    int         not null default 0,
  queries_generated int         not null default 0,
  created_at        timestamptz not null default now()
);
create unique index brand_seeds_uq on brand_seeds (research_run_id, supplied_norm, seed_role);


-- ===========================================================================
-- DEFERRAL REGISTER
-- ===========================================================================
-- Recorded so the gap is visible rather than lost. Brief §37 ranks speed to a
-- usable MVP above modular architecture and infrastructure sophistication, and
-- §38 wants the end-to-end flow first; everything below is owned by a subsystem
-- that flow does not yet contain. Nothing here is renamed or re-keyed, so each
-- item is an additive migration later.
--
-- TABLES DEFERRED
--   content_analysis_runs      (ARCHITECTURE §2.5, doc 03 §10.4) -- the
--       (analysis_id, research_run_id) junction. Needed BEFORE any cross-run
--       figure is published: an analysis is created once per (item, config,
--       media) and reused, so content_analysis.research_run_id names only the
--       first run and per-run denominators computed from it under-count every
--       run that reused an item. Brief §28 makes those denominators
--       load-bearing, so this is the first thing to add back.
--   analysis_attempts, analysis_calls   (doc 03 §10.6) -- per-stage retry trail
--       and per-call cost ledger. The aggregate token and cost columns on
--       content_analysis cover the MVP's spend reporting.
--   calibration_labels, calibration_models  (doc 03 §10.7) -- human labels and
--       the fitted calibrator. Until they exist there is no calibrated
--       confidence to display, and the validator's pass/drop/demote outcome is
--       the whole gate.
--   brand_alias_detachments, brand_review_items  (ARCHITECTURE §2.4, doc 06
--       §5.1) -- alias detachment history and the human review queue.
--   substitution_cues, descriptor_tokens  (ARCHITECTURE §2.4, doc 06 §5.1,
--       §1.x) -- both ship empty anyway and are written by deferred detectors.
--
-- COLUMNS DEFERRED
--   analysis_configs (doc 03 §10.1): tau, unverified_floor (calibration);
--       thinking (D-34 fixes it to adaptive everywhere); schema_variant,
--       list_cap, inclusions, detail_pass (detail-pass and schema-variant
--       ladders); user_template, task_tail (today the per-item block is built
--       by multimodal.py::build_manifest(), not stored as a template);
--       output_json_schema; cache_ttl_interactive, cache_ttl_batch (D-16, batch
--       path deferred); notes; vocab_policy (D-15, taxonomy suggestion loop in
--       0005).
--   content_frames (doc 03 §10.2): selection_reason values 'relevance_window'
--       and 'zoom_crop', owned by the deferred relevance-window and zoom-crop
--       selectors.
--   content_transcripts (doc 03 §10.3): asr_quality, speech_ratio (the §7.2 ASR
--       skip and low-confidence gates); code_switch_suspected (brief §18 wants
--       this signal -- it is deferred only because its detector is, and
--       language_detected already carries the raw evidence); asr_review_queue
--       (translate-not-transcribe review); words (word-level timestamps);
--       dropped_segment_count, chunk_count.
--   content_analysis (doc 03 §10.4): degraded_from, repaired_fields, repaired,
--       attempt_count (the retry/repair ladder); platform_thumbnail_present
--       (derivable from a content_frames row with selection_reason='thumbnail',
--       and it must be restored before any CROSS-PLATFORM visibility comparison,
--       which it confounds); summary_dropped; model_field_confidence;
--       refusal_category; wall_clock_ms; batch_id, raw_response.
--   content_analysis_claims (doc 03 §10.5): cue_check and support_features (the
--       Check B/C scoring surface -- today's validator drops rather than grades,
--       so every stored row would read 'pass'); model_confidence (our
--       ItemAnalysis deliberately has NO model_confidence field: see
--       ENVIRONMENT.md on the structured-output optional-parameter limit);
--       calibrated_confidence and calibration_model_id (plus the deferred FK to
--       calibration_models); audience_authored (claims anchored only in
--       `comments` are the audience talking, not the creator -- derivable from
--       `anchors` until the column returns).
--   brands (doc 06 §5.1): seed_id (brand_seeds.resolved_brand_id already links
--       the pair); resolution_quality; distinct_channel_count; notes.
--   brand_aliases (doc 06 §5.1): consonant_skeleton, token_signature and their
--       indexes (extra matching tiers); requires_context, context_terms and
--       alias_ctx_chk (the context-gated matcher); provisional_merge;
--       observation_count, distinct_item_count (derivable from
--       brand_surface_observations).
--   brand_surface_observations (doc 06 §5.1): extractor_confidence (nothing
--       emits it -- see the model_confidence note above); residual_tokens
--       (descriptor_tokens subsystem); bundle_or_assortment (commerce bundles,
--       0006).
--
-- VIEWS DEFERRED
--   content_product_prominence (doc 03 §10.4) -- reconstructs the brief §20
--       flat product_prominence dict from extra->>'prominence'. Our
--       ProductSighting carries no prominence or screen_share field, so the
--       view would return nothing but NULLs.
--
-- DIVERGENCES FROM ARCHITECTURE.md, taken deliberately
--   * Table shape follows aypo/analysis/schema.py (ItemAnalysis: one `labels`
--     list discriminated by Facet, compact string anchors, no model_confidence)
--     rather than §4.4's VideoAnalysis (one array per facet, nested Anchor
--     objects, model_confidence on every Claim). The live models and 104
--     passing tests are the contract.
--   * `storage_key` replaces doc 03's `s3_key`; `language_detected` keeps the
--     doc's spelling although Transcript calls the field `detected_language`.
--   * `analysis_config_id` keeps the architecture's full name throughout.
--   * media_tier permits four values although MediaTier currently emits two;
--     widening later would be a breaking change, narrowing now is not.
--   * §7.2 pins max_tokens at 32000 for this stage; multimodal.py passes 8000.
--     The column records whichever was used, so the two stay distinguishable.
--   * No `representative_quote` column (D-39). Our ItemAnalysis emits no such
--     field, so one would only ever be NULL. Clustering (0007) wants a <=30-word
--     verbatim span for cluster snippets; adding it means changing the extractor
--     schema first, and the column should land in the same migration that does.
