# 02 — Open-Ended Label Discovery, Canonicalisation & Promotion to Taxonomy

**Subsystem owner:** `aypo.analysis.labels` (+ `aypo.api.routes.taxonomy`, `aypo.jobs.tasks.canonicalise_labels`)
**Normative upstream:** `docs/BRIEF.md` §1, §7, §19, §20, §21, §22, §28, §34
**Status:** design, pre-implementation. Thresholds marked **(prior)** are reasoned starting
values that MUST be recalibrated against run 1 data using the procedure in §2.10.
**Revised** after adversarial review — see §10 for what changed and what risk remains.

---

## 0. What this subsystem is, in one paragraph

Content analysis (subsystem 03) and commerce analysis emit *open-ended labels* — short
model-authored strings, with no predefined vocabulary. This subsystem is the only thing in
AYPO allowed to decide that two such strings are the same concept. It (a) stores every raw
emission immutably, (b) normalises and clusters surface forms into canonical labels while
keeping every original form retrievable, (c) feeds the discovered vocabulary back to the
extractor as *non-binding suggestions* and measures how much that biases discovery,
(d) promotes recurring canonical labels into normalised taxonomy entries under an explicit
evidential gate, and (e) serves facet vocabularies with counts, scope and trajectories to the
UI and the intelligence layer.

**Hard invariant (I1):** the entire contents of `observation_label_map`, `labels`, `label_merges`,
`concept_groups` and `taxonomy_entries` are a *pure function* of

```
label_observations (restricted to observation_seq <= as_of_seq)
  + canonicalisation_config[canon_version]
  + label_decisions (active at rebuild time)
  + brand_name_snapshot[canon_version]
  + seed
```

Nothing is destructively edited: a rebuild **recomputes the derived tier**, it never rewrites an
emission. Any threshold can be changed and the whole taxonomy rebuilt from the observation log.
This is what makes the numbers in §2 safe to be wrong on the first run.

Three consequences that the first draft of this design got wrong and that are now load-bearing:

1. **Derived values may not live on the append-only table.** `normalised_form`, `lang_bucket` and
   `label_id` are functions of `norm_pipeline_version` and the affinity table, so a recalibration
   must be able to recompute them. They therefore live in `observation_label_map`, keyed by
   `canon_version`, which is truncate-and-rebuild. `label_observations` holds *only* the verbatim
   emission and its provenance (§1.4).
2. **I1 holds for a full rebuild at a pinned observation snapshot, not for the in-run incremental
   pass.** The incremental pass is order-dependent on support counts that are still changing, so
   its output is explicitly *provisional* and is used only to drive the suggestion loop (§2.5).
   Every number a user sees comes from a rebuild at a pinned `as_of_seq`.
3. **Anything outside the five inputs above is a leak.** Two leaks existed and are closed: the
   promotion gate's brand-name check now reads a `brand_name_snapshot` pinned to the
   `canon_version` rather than the live `brands` table, and no derivation may read `current_date`
   (§4.2, §6.4 use `run_as_of_date = research_runs.started_at::date`).

---

## 1. The label object

### 1.1 Facets — the one closed set, and why it is allowed to be closed

There are exactly fourteen facets:

`occasion`, `behaviour`, `social_context`, `activity`, `cultural_signal`, `slang`, `humour`,
`narrative`, `creator_archetype`, `content_format`, `product_pairing`, `audience_signal`,
`commercial_signal`, `unexpected`.

A closed facet list does **not** violate §1/§34. A facet names *a kind of question*
("in what situation?", "what register of language?"), never *an answer*. What §1 forbids is a
closed list of *values* — a closed list of occasions, behaviours, competitors, themes, or slang
terms. There is none anywhere in this design.

**The facet set is not our invention, and the earlier eleven-facet set was wrong.** Brief §19B
names thirteen kinds of open-ended field and §20's JSON names nine open arrays; the two lists are
not identical, and the union is what the extractor in subsystem 03 actually emits. The previous
version of this section asserted that the brief "enumerates this set", which was false, and it
dropped `social_context` and `activities` (both named in §20 and both emitted by
`03-multimodal-analysis` §5.3) and `narrative` (named in §19B). Dropping them does not make those
observations go away — it forces them into `occasion`/`behaviour`/`unexpected`, which is exactly
the bending this design exists to prevent. The mapping is now explicit and total:

| Brief §19B / §20 field | 03 emission field | Facet here |
|---|---|---|
| consumer occasion | `occasions` | `occasion` |
| behaviour | `behaviors` | `behaviour` |
| social situation | `social_context` | `social_context` |
| (activities, §20) | `activities` | `activity` |
| cultural reference | `cultural_signals` | `cultural_signal` |
| slang | `slang` | `slang` |
| humour style | `humor_signals` | `humour` |
| narrative | (§19B; emitted under `unexpected` until 03 adds the field) | `narrative` |
| creator archetype | `creator_archetype` | `creator_archetype` |
| content format | `content_format` | `content_format` |
| product pairing | `product_pairings` | `product_pairing` |
| audience signal | (derived from 03 `audience`/addressivity cues) | `audience_signal` |
| commercial signal | (derived from 03 `purchase_intent` / `sponsorship` free-text cues) | `commercial_signal` |
| unexpected observation | `unexpected_observations` | `unexpected` |
| emerging theme | — | **not a facet.** Themes are clusters over content, owned by subsystem 04 (brief §21); a theme is not a label read off one item. |

A facet that subsystem 03 does not yet emit (`narrative`) is created in `label_facets` with
`accepts_emissions = false` so that it appears in the API as an explicitly empty facet rather
than silently not existing. Adding or retiring a facet is an `INSERT`/`UPDATE` on a lookup table
plus a `canonicalisation_config` row, never a DDL migration.

`unexpected` is the mandatory escape hatch: the extractor is instructed that if an observation
is interesting and fits no facet, it must be emitted as `unexpected` rather than bent into the
nearest facet. Facet mis-assignment is recoverable later via a `facet_reassign` decision
(§4.6); a discarded observation is not. Facets are stored as a lookup table, not a Postgres
ENUM, so a twelfth facet is an `INSERT` plus a config row rather than an `ALTER TYPE` under
lock.

### 1.2 The object model: one immutable tier, two derived tiers

The single most common way to get this wrong is to have one row per concept and mutate its
text as understanding improves. That loses the model's original output, which §28 ("preserve
provenance") forbids and which the evidence drill-down in §27 depends on.

Four levels. Exactly one of them is append-only; the other three are derived and rebuildable.

| Level | Table | Granularity | Mutability |
|---|---|---|---|
| **Emission** | `label_observations` | one row per (analysis pass × proposed label) | **append-only; insert-only, enforced by trigger** |
| Assignment | `observation_label_map` | one row per (observation, canon_version) | **derived**: truncate-and-rebuild per `canon_version` |
| Lexical identity | `labels` | one row per (facet, lang_bucket, normalised_form) | derived; `normalised_form` is the identity key and never changes within a `canon_version` |
| Concept | `taxonomy_entries`, `concept_groups` | one row per promoted concept / cross-language group | derived, rebuildable |

The assignment tier is the fix for a structural error in the first draft, which stored
`normalised_form`, `lang`, `norm_steps` and `label_id` **on the append-only table**. Those four
values are outputs of `norm_pipeline_version` and of the corpus-derived affinity table (§2.3), so
a recalibration must be able to recompute them — and the no-UPDATE rule made that impossible.
The consequence was not cosmetic: §2.2 step 8's claim that a late-arriving singular "self-heals on
the next pass" was simply false, because an observation could never be re-pointed at the unified
label, and I1's "any threshold can be changed and the whole taxonomy rebuilt" was unimplementable
for anything upstream of the embedding. With the split, a rebuild writes a new
`observation_label_map` generation and the emission log is untouched.

A merge therefore **never rewrites an observation and never deletes a label row**. The alias
keeps its own `labels` row with `canonical_id` set; the map keeps pointing at the alias.
"Un-merging" is nulling one column.

### 1.3 Counting: the measures, and the trap worth naming

After canonicalisation, one video that emitted three near-duplicate forms would triple-count if
you naively sum observations. Define precisely:

- **`observation_count`** — raw `label_observations` rows resolving here (incl. via aliases).
  Used only for diagnostics and prompt-echo detection.
- **`support_count`** — `COUNT(DISTINCT analysis_unit)` over resolved observations, where
  `analysis_unit = COALESCE(content_item_id::text, 'c:'||commerce_item_id::text)`. **This is
  the number used for promotion, ranking, evidence strength and every UI figure.** One analysis
  unit contributes at most 1 support to a canonical label no matter how many aliases it emitted.
- **`distinct_content`** — `COUNT(DISTINCT content_item_id)`. Social units only.
- **`distinct_commerce`** — `COUNT(DISTINCT commerce_item_id)`. Marketplace units only.
- **`support_suggested` / `holdout_support`** — the same distinct-unit count restricted to
  `vocab_mode = 'suggested'` / `'holdout'`. `support_suggested` is the **only** measure allowed to
  select suggestions (§3.3); using `support_count` there let the holdout arm build the vocabulary
  shown to the suggested arm, which silently broke both the §3.2 independence claim and bias
  metric 6.

Everything downstream that says "support" means `support_count`, and every count is a function of
the active `canon_version` (more aggressive merging raises support). **No support figure may be
rendered, exported or compared without its `canon_version`** — §6.1 rule 6.

The two namespaces are kept apart deliberately. `support_count` answers "how many analysed units
evidenced this", which is the right denominator-compatible number; `distinct_content` is what the
§4.1 social-diversity gates test, because one marketplace listing with forty reviews is not forty
independent observations of a culture.

### 1.4 DDL

```sql
-- ============================================================
-- 02-open-taxonomy :: schema
-- supabase/migrations/0002_open_taxonomy.sql
-- ============================================================
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ------------------------------------------------------------
-- Facets: structural question-dimensions, not answers (§1.1)
-- ------------------------------------------------------------
CREATE TABLE label_facets (
    facet               text PRIMARY KEY,
    display_name        text        NOT NULL,
    question            text        NOT NULL,   -- shown in prompts; never names a value
    merge_posture       text        NOT NULL
        CHECK (merge_posture IN ('liberal','moderate','conservative','very_conservative')),
    lexical_gate_required boolean   NOT NULL DEFAULT false,  -- true => never merge on
                                                             -- embedding similarity alone
    accepts_emissions   boolean     NOT NULL DEFAULT true,   -- false => shown as empty, not hidden
    max_suggestions     smallint    NOT NULL DEFAULT 12,
    sort_order          smallint    NOT NULL
);
-- NOTE: no tau columns. Thresholds live ONLY in canonicalisation_config.thresholds,
-- keyed (facet, lang_bucket). The first draft stored them here as well, giving two
-- sources of truth for the one input I1 names, so a config change could silently
-- disagree with the table the clustering code read.

-- ------------------------------------------------------------
-- Language buckets: the canonicalisation partition key (§5).
--
-- NOT an enum. `03-multimodal-analysis` §0.3 forbids a closed language enum
-- ("a market assumption"), and the first draft of this file shipped
-- ENUM ('en','tl','taglish','other','und') — a market-specific closed list in
-- the schema AND, via LabelProposal.language_observed, in the extractor prompt.
-- Buckets are now DATA, derived at run start from research_runs.language_codes
-- (plus 'other' and 'und'), so a second market is a seed row, not an ALTER TYPE.
-- ------------------------------------------------------------
CREATE TABLE lang_buckets (
    bucket          text PRIMARY KEY,         -- BCP-47-ish code, or 'mix:<a>+<b>', 'other', 'und'
    kind            text NOT NULL CHECK (kind IN ('monolingual','mixed','other','und')),
    components      text[] NOT NULL DEFAULT '{}',   -- for 'mixed': the component codes
    -- language-profile attributes that drive thresholds (§2.7). Set from configuration,
    -- never inferred from the content, and never naming a specific market in logic.
    rich_morphology boolean NOT NULL DEFAULT false, -- affixation/reduplication inflates edit distance
    embedding_calibrated boolean NOT NULL DEFAULT false, -- has this bucket passed §2.10?
    created_at      timestamptz NOT NULL DEFAULT now()
);
-- Seeded per run: one row per configured language code, one 'mix:' row per unordered
-- pair of configured codes, plus ('other','other') and ('und','und').

-- Lifecycle status and artefact FLAGS are separate, because they are orthogonal.
-- The first draft folded both into one column, which made §4.5's own promises
-- unrepresentable: a 'dormant' label could not also be 'canonical' (although §4.5
-- says dormancy is a finding, not a demotion in meaning), and flagging a promoted
-- label silently removed it from every default read filtered on status='canonical'.
CREATE TYPE label_status AS ENUM (
    'candidate',                  -- observed, below promotion gate
    'emerging_candidate',         -- weak-signal tier (§4.2)
    'canonical',                  -- promoted taxonomy entry
    'alias',                      -- merged into canonical_id
    'pending_merge',              -- in the review band, awaiting adjudication
    'blocked'                     -- human-blocked (degenerate / PII / junk)
);

CREATE TYPE label_flag AS ENUM (
    'suspected_prompt_echo',      -- §4.5 bias-driven flag
    'single_source_artefact',     -- §4.5
    'stale_extractor_artefact',   -- §4.5
    'dormant',                    -- no new support for >= 2 runs (declining is signal)
    'cross_facet_duplicate',      -- §4.6 candidate, never auto-applied
    'uncalibrated_bucket'         -- lang_buckets.embedding_calibrated = false (§2.10, §5.5)
);

CREATE TYPE canon_method AS ENUM (
    'exact_normalised',   -- collided after lexical normalisation; no threshold involved
    'embedding_lexical',  -- cos >= tau_merge AND lexical gate passed
    'embedding_strong',   -- cos >= tau_strong, no lexical overlap required
    'llm_adjudicated',    -- review band, resolved by batched LLM judgement
    'human_decision',     -- label_decisions override
    'lexical_only'        -- degraded mode: embeddings unavailable (§7.1)
);

-- ------------------------------------------------------------
-- 1) EMISSION LOG — append-only, the source of truth
-- ------------------------------------------------------------
CREATE TABLE label_observations (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id              uuid        NOT NULL REFERENCES research_runs(id),
    content_item_id     uuid            NULL REFERENCES content_items(id),
    commerce_item_id    uuid            NULL REFERENCES commerce_items(id),
    analysis_id         uuid        NOT NULL,   -- the analysis pass that emitted it
    source_kind         text        NOT NULL
        CHECK (source_kind IN ('video','post','commerce_listing','commerce_review','web_page')),

    facet               text        NOT NULL REFERENCES label_facets(facet),

    -- VERBATIM model output. Never normalised, never corrected, never deleted.
    surface_form        text        NOT NULL CHECK (length(btrim(surface_form)) BETWEEN 1 AND 160),
    gloss_en            text            NULL CHECK (gloss_en IS NULL OR length(gloss_en) <= 400),
    gloss_lang_ok       boolean         NULL,   -- py3langid check result, §7.1
    language_observed   text            NULL,   -- free text as reported by the extractor; NEVER
                                                -- an enum (03 §0.3) and never used as a bucket

    -- monotonic sequence within the run: the pin for a reproducible rebuild (I1)
    observation_seq     bigint      NOT NULL,

    -- NOTE: normalised_form / lang / norm_steps / label_id are NOT here. They are
    -- outputs of norm_pipeline_version and of the corpus-derived affinity table, so a
    -- recalibration must recompute them; on an append-only table that is impossible.
    -- They live in observation_label_map, keyed by canon_version (§1.2).

    -- evidence (§27: every claim traceable)
    evidence_quote      text            NULL,
    evidence_timecode_s real            NULL CHECK (evidence_timecode_s IS NULL
                                                    OR evidence_timecode_s >= 0),
    model_confidence    real            NULL CHECK (model_confidence IS NULL
                                                    OR model_confidence BETWEEN 0 AND 1),

    -- provenance (§7) + bias instrumentation (§3.5) — these columns are the experiment
    extractor_version   text        NOT NULL,
    prompt_version      text        NOT NULL,
    norm_pipeline_hint  text        NOT NULL,   -- version in force at ingest; audit only
    vocab_snapshot_id   uuid            NULL,   -- -> label_vocab_snapshots(id), deferred
    vocab_mode          text        NOT NULL
        CHECK (vocab_mode IN ('suggested','holdout')),
    was_suggested       boolean     NOT NULL DEFAULT false,  -- COMPUTED by us, not self-reported
    shown_rank          smallint        NULL,   -- rank in the rendered block; set for EVERY
                                                -- observation whose facet had a non-empty block,
                                                -- NULL only when nothing was shown. Bias metric 3
                                                -- needs the denominator, not just the hits.
    self_reported_reuse boolean         NULL,   -- model's claim; cross-check only

    created_at          timestamptz NOT NULL DEFAULT now(),

    -- exactly one unit, so `analysis_unit` is well defined (§1.3)
    CHECK ((content_item_id IS NOT NULL) <> (commerce_item_id IS NOT NULL))
);

-- Discovery strategy is NOT a column here. An item is discovered by one or more
-- (query, platform) pairs recorded in research_results, and research_queries.strategy
-- is an enum ('brand','category','competitor','theme','hypothesis') — not the 'A'..'E'
-- letters the first draft invented, and not single-valued per item. Collapsing it onto
-- the observation both mismatched the upstream schema and understated strategy diversity
-- for any item that two different query kinds found. §4.1's diversity gates join
-- research_results instead (§1.5).

CREATE INDEX obs_run_facet_idx ON label_observations (run_id, facet);
CREATE INDEX obs_content_idx   ON label_observations (content_item_id)
                                WHERE content_item_id IS NOT NULL;
CREATE INDEX obs_commerce_idx  ON label_observations (commerce_item_id)
                                WHERE commerce_item_id IS NOT NULL;
CREATE INDEX obs_mode_idx      ON label_observations (facet, vocab_mode, created_at);
CREATE INDEX obs_snapshot_idx  ON label_observations (vocab_snapshot_id)
                                WHERE vocab_snapshot_id IS NOT NULL;
CREATE UNIQUE INDEX obs_seq_idx ON label_observations (run_id, observation_seq);

-- Append-only enforcement by TRIGGER, not by RULE.
-- Two reasons the first draft's `CREATE RULE ... DO INSTEAD NOTHING` was wrong:
--   (a) it fails SILENTLY — a buggy UPDATE reports success and changes nothing, which is
--       the worst possible behaviour for the table that is the system's source of truth;
--   (b) Postgres rejects `INSERT ... ON CONFLICT` on any table carrying an INSERT or
--       UPDATE rule, which forecloses idempotent re-ingest of a retried analysis.
CREATE OR REPLACE FUNCTION label_observations_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'label_observations is append-only (attempted %)', TG_OP
        USING ERRCODE = 'restrict_violation';
END; $$;
CREATE TRIGGER label_observations_no_mutate
    BEFORE UPDATE OR DELETE OR TRUNCATE ON label_observations
    FOR EACH STATEMENT EXECUTE FUNCTION label_observations_immutable();

-- ------------------------------------------------------------
-- 1b) ASSIGNMENT — derived, rebuilt per canon_version (§1.2)
-- ------------------------------------------------------------
CREATE TABLE observation_label_map (
    observation_id   uuid    NOT NULL REFERENCES label_observations(id),
    canon_version    integer NOT NULL,   -- FK added in "deferred constraints" below
    normalised_form  text    NOT NULL,
    norm_steps       jsonb   NOT NULL DEFAULT '[]'::jsonb,
    lang_bucket      text    NOT NULL REFERENCES lang_buckets(bucket),
    lang_method      text    NOT NULL,
    label_id         uuid    NOT NULL,   -- FK added below
    PRIMARY KEY (observation_id, canon_version)
);
CREATE INDEX olm_label_idx ON observation_label_map (canon_version, label_id);

-- Proposals the emission schema accepted but validation rejected. They are kept, not
-- dropped: §1.1's whole argument is that a discarded observation is unrecoverable.
CREATE TABLE label_observation_rejects (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id        uuid NOT NULL REFERENCES research_runs(id),
    analysis_id   uuid NOT NULL,
    raw_proposal  jsonb NOT NULL,     -- exactly what the model emitted
    reject_reason text  NOT NULL,     -- 'gloss_too_short' | 'facet_unknown' | 'form_too_long' | ...
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 2) LEXICAL IDENTITY — one row per distinct normalised form
-- ------------------------------------------------------------
CREATE TABLE labels (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    canon_version       integer     NOT NULL,     -- the generation this row belongs to
    facet               text        NOT NULL REFERENCES label_facets(facet),
    lang_bucket         text        NOT NULL REFERENCES lang_buckets(bucket),
    normalised_form     text        NOT NULL,     -- IMMUTABLE identity within a canon_version
    display_form        text        NOT NULL,     -- most frequent raw surface form (real text only)
    gloss_en            text            NULL,     -- modal gloss across observations

    status              label_status NOT NULL DEFAULT 'candidate',
    flags               label_flag[] NOT NULL DEFAULT '{}',   -- orthogonal to status (§4.5)
    canonical_id        uuid            NULL REFERENCES labels(id),  -- NULL => this is a leader
    canon_method        canon_method    NULL,
    canon_similarity    real            NULL,     -- cosine to leader at merge time, 4dp
    canonicalised_at    timestamptz     NULL,
    needs_recanon       boolean     NOT NULL DEFAULT true,

    -- embedding of the glossed key (§2.4)
    embed_key           text            NULL,
    embed_key_sha       bytea           NULL,
    embedding           vector(1536)    NULL,     -- 1536 dims: pgvector HNSW caps at 2000
    embed_provider      text            NULL,     -- 'openai:text-embedding-3-large' | 'jina:...'
    embedded_at_support integer         NULL,     -- support when last embedded (§2.4 re-embed rule)

    -- denormalised counters: CACHE ONLY. Authoritative SQL in §1.5.
    observation_count   integer     NOT NULL DEFAULT 0,
    support_count       integer     NOT NULL DEFAULT 0,  -- distinct analysis units (§1.3)
    distinct_content    integer     NOT NULL DEFAULT 0,  -- distinct content_item_id
    distinct_commerce   integer     NOT NULL DEFAULT 0,  -- distinct commerce_item_id
    distinct_creators   integer     NOT NULL DEFAULT 0,
    distinct_platforms  smallint    NOT NULL DEFAULT 0,  -- via research_results, social + commerce
    distinct_strategies smallint    NOT NULL DEFAULT 0,  -- via research_queries.strategy
    distinct_queries    integer     NOT NULL DEFAULT 0,
    strategy_unknown_units integer  NOT NULL DEFAULT 0,  -- units with no provenance row (§4.1)
    top_creator_share   real        NOT NULL DEFAULT 0,
    top_query_share     real        NOT NULL DEFAULT 0,
    holdout_support     integer     NOT NULL DEFAULT 0,  -- support from the no-suggestion arm
    support_suggested   integer     NOT NULL DEFAULT 0,  -- support from the suggested arm
    suggested_share     real        NOT NULL DEFAULT 0,  -- UNIT-weighted, not observation-weighted
    counters_at         timestamptz     NULL,

    -- temporal: based on CONTENT PUBLICATION dates, not analysis dates (§4.2)
    first_pub_date      date            NULL,
    last_pub_date       date            NULL,
    distinct_pub_weeks  smallint    NOT NULL DEFAULT 0,
    first_seen_run      uuid            NULL REFERENCES research_runs(id),
    last_seen_run       uuid            NULL REFERENCES research_runs(id),
    first_seen_at       timestamptz NOT NULL DEFAULT now(),
    last_seen_at        timestamptz NOT NULL DEFAULT now(),

    evidence_strength   real            NULL CHECK (evidence_strength IS NULL
                                                    OR evidence_strength BETWEEN 0 AND 1),
    status_reason       text            NULL,
    status_changed_at   timestamptz     NULL,

    UNIQUE (canon_version, facet, lang_bucket, normalised_form),
    CHECK (canonical_id IS NULL OR canonical_id <> id),
    CHECK ((status = 'alias') = (canonical_id IS NOT NULL))
);

-- An alias must point at a LEADER, never at another alias. Without this, the one-hop
-- COALESCE(canonical_id, id) resolution used everywhere silently splits a cluster's
-- support the first time a must_link or a review-band verdict merges two leaders.
CREATE OR REPLACE FUNCTION labels_no_alias_chain() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.canonical_id IS NOT NULL
       AND EXISTS (SELECT 1 FROM labels p
                   WHERE p.id = NEW.canonical_id AND p.canonical_id IS NOT NULL) THEN
        RAISE EXCEPTION 'alias chain: % -> % which is itself an alias', NEW.id, NEW.canonical_id;
    END IF;
    IF EXISTS (SELECT 1 FROM labels c
               WHERE c.canonical_id = NEW.id AND NEW.canonical_id IS NOT NULL) THEN
        RAISE EXCEPTION 'label % has aliases and cannot become an alias without compression',
              NEW.id;
    END IF;
    RETURN NEW;
END; $$;
CREATE TRIGGER labels_alias_depth
    BEFORE INSERT OR UPDATE OF canonical_id ON labels
    FOR EACH ROW EXECUTE FUNCTION labels_no_alias_chain();

CREATE INDEX labels_facet_status_idx ON labels (canon_version, facet, status, support_count DESC);
CREATE INDEX labels_canonical_idx    ON labels (canonical_id) WHERE canonical_id IS NOT NULL;
CREATE INDEX labels_trgm_idx         ON labels USING gin (normalised_form gin_trgm_ops);
CREATE INDEX labels_recanon_idx      ON labels (facet) WHERE needs_recanon;
CREATE INDEX labels_flags_idx        ON labels USING gin (flags);
-- HNSW serves the READ path (§6.2 semantic_search) only. Canonicalisation does NOT use it:
-- an approximate index is order-dependent and post-filters, which breaks both I1 and recall
-- (§2.5). Partition scans there are exact.
CREATE INDEX labels_hnsw_idx ON labels
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

-- ------------------------------------------------------------
-- 3) MERGE / SPLIT AUDIT — reversible
-- ------------------------------------------------------------
CREATE TABLE label_merges (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    from_label_id   uuid NOT NULL REFERENCES labels(id),
    into_label_id   uuid NOT NULL REFERENCES labels(id),
    facet           text NOT NULL REFERENCES label_facets(facet),
    method          canon_method NOT NULL,
    similarity      real NULL,
    lexical_signal  jsonb NOT NULL DEFAULT '{}'::jsonb, -- {trigram, token_set, edit, substring}
    cooc_ratio      real NULL,                          -- anti-merge guard value (§2.6)
    adjudicator     text NULL,                          -- model id / user id
    rationale       text NULL,
    canon_version   integer NOT NULL,
    run_id          uuid NULL REFERENCES research_runs(id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    reversed_at     timestamptz NULL,
    reversed_reason text NULL,
    CHECK (from_label_id <> into_label_id)
);
CREATE INDEX merges_from_idx ON label_merges (from_label_id) WHERE reversed_at IS NULL;
CREATE INDEX merges_into_idx ON label_merges (into_label_id) WHERE reversed_at IS NULL;

-- ------------------------------------------------------------
-- 4) ADJUDICATION CACHE — makes threshold recalibration ~free (§7.3)
-- ------------------------------------------------------------
CREATE TABLE label_pair_judgements (
    pair_key        text PRIMARY KEY,   -- sha256(facet || '|' || least || '|' || greatest
                                        --        || '|' || prompt_version || '|' || model)
    facet           text NOT NULL REFERENCES label_facets(facet),
    a_label_id      uuid NOT NULL REFERENCES labels(id),   -- least(id), canonical order
    b_label_id      uuid NOT NULL REFERENCES labels(id),   -- greatest(id)
    -- Both presentation orders are asked and recorded. A merge requires agreement;
    -- disagreement is itself the signal that the pair is genuinely borderline.
    relation_ab     text NOT NULL,
    relation_ba     text NOT NULL,
    relation        text NOT NULL       -- agreed relation, or 'disputed'
        CHECK (relation IN ('same_concept','narrower','broader','sibling',
                            'related','unrelated','translation_equivalent','disputed')),
    judge_confidence_ab real NULL CHECK (judge_confidence_ab IS NULL
                                         OR judge_confidence_ab BETWEEN 0 AND 1),
    judge_confidence_ba real NULL CHECK (judge_confidence_ba IS NULL
                                         OR judge_confidence_ba BETWEEN 0 AND 1),
    rationale       text NULL,
    model           text NOT NULL,
    prompt_version  text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);
-- judge_confidence is recorded for audit. It is NOT a merge gate: a self-reported
-- float from the model is not an admissible licence for an irreversible operation
-- (§2.8). Order agreement is the gate.

-- Ground truth from §2.10. Separate table so a calibration pair is never confused
-- with a production verdict, and so precision can be recomputed per stratum.
CREATE TABLE label_pair_gold (
    pair_key        text PRIMARY KEY,
    facet           text NOT NULL REFERENCES label_facets(facet),
    lang_bucket     text NOT NULL REFERENCES lang_buckets(bucket),
    similarity      real NOT NULL,
    sim_band        text NOT NULL,                       -- '0.80-0.85', ...
    gold_same       boolean NOT NULL,
    gold_source     text NOT NULL CHECK (gold_source IN ('human','human_adjudicated_pair')),
    annotator       text NOT NULL,
    second_annotator text NULL,
    agreed          boolean NULL,                        -- inter-annotator agreement
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 5) CROSS-LANGUAGE / HIERARCHY LINKS — never merges (§5)
-- ------------------------------------------------------------
CREATE TYPE label_link_type AS ENUM (
    'translation_equivalent',  -- same referent, different language  -> forms concept groups
    'near_equivalent',         -- overlapping but not interchangeable -> does NOT group
    'code_switch_variant',     -- mixed-bucket form of a monolingual label
    'borrowing',               -- loanword relation
    'broader', 'narrower', 'related'
);

CREATE TABLE label_links (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    from_label_id uuid NOT NULL REFERENCES labels(id),
    to_label_id   uuid NOT NULL REFERENCES labels(id),
    link_type     label_link_type NOT NULL,
    similarity    real NULL,            -- gloss-only cosine
    method        text NOT NULL,        -- 'gloss_embedding+llm' | 'human'
    confidence    real NULL CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    created_at    timestamptz NOT NULL DEFAULT now(),
    retired_at    timestamptz NULL,
    UNIQUE (from_label_id, to_label_id, link_type),
    CHECK (from_label_id <> to_label_id)
);
CREATE INDEX links_from_idx ON label_links (from_label_id) WHERE retired_at IS NULL;
CREATE INDEX links_to_idx   ON label_links (to_label_id)   WHERE retired_at IS NULL;

-- Concept groups: connected components over translation_equivalent links ONLY.
-- Derived; rebuilt by the canonicalisation pass.
CREATE TABLE concept_groups (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    facet           text NOT NULL REFERENCES label_facets(facet),
    display_name    text NOT NULL,     -- the highest-support member's display_form
    primary_label_id uuid NOT NULL REFERENCES labels(id),
    gloss_en        text NULL,
    member_count    smallint NOT NULL DEFAULT 0,
    lang_breakdown  jsonb NOT NULL DEFAULT '{}'::jsonb,  -- {"<code_a>": 41, "<code_b>": 77,
                                                         --  "mix:<a>+<b>": 12}
    support_count   integer NOT NULL DEFAULT 0,          -- DISTINCT content across members
    canon_version   integer NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE concept_group_members (
    concept_group_id uuid NOT NULL REFERENCES concept_groups(id) ON DELETE CASCADE,
    label_id         uuid NOT NULL REFERENCES labels(id),
    PRIMARY KEY (concept_group_id, label_id)
);
CREATE UNIQUE INDEX cgm_label_unique ON concept_group_members (label_id);

-- ------------------------------------------------------------
-- 6) PROMOTED TAXONOMY
-- ------------------------------------------------------------
CREATE TABLE taxonomy_entries (
    label_id            uuid PRIMARY KEY REFERENCES labels(id),
    canon_version       integer NOT NULL,    -- support figures are tau-dependent (§1.3)
    facet               text NOT NULL REFERENCES label_facets(facet),
    entry_name          text NOT NULL,       -- == labels.display_form at promotion
    definition          text NULL,           -- LLM-written from observed evidence ONLY (§4.4)
    definition_model    text NULL,
    promoted_at         timestamptz NOT NULL DEFAULT now(),
    promoted_run_id     uuid NULL REFERENCES research_runs(id),
    promotion_support   integer NOT NULL,
    promotion_metrics   jsonb NOT NULL,      -- every gate value at promotion time
    promotion_caveats   text[] NOT NULL DEFAULT '{}',  -- 'single_platform','narrow_window',...
    reviewed_by         text NULL,
    reviewed_at         timestamptz NULL,
    retired_at          timestamptz NULL,
    retired_reason      text NULL,
    superseded_by       uuid NULL REFERENCES labels(id)
);
CREATE INDEX tax_facet_idx ON taxonomy_entries (facet) WHERE retired_at IS NULL;

-- ------------------------------------------------------------
-- 7) HUMAN OVERRIDES — applied as the final layer of every rebuild (§4.7)
-- ------------------------------------------------------------
CREATE TABLE label_decisions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    action        text NOT NULL CHECK (action IN
                    ('must_link','cannot_link','block','unblock','force_promote',
                     'force_demote','facet_reassign','set_display_form','set_definition')),
    label_id      uuid NOT NULL REFERENCES labels(id),
    target_id     uuid NULL REFERENCES labels(id),   -- for must_link / cannot_link
    payload       jsonb NOT NULL DEFAULT '{}'::jsonb,
    actor         text NOT NULL,
    reason        text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    revoked_at    timestamptz NULL
);
CREATE INDEX decisions_active_idx ON label_decisions (label_id) WHERE revoked_at IS NULL;

-- ------------------------------------------------------------
-- 8) SUGGESTION SNAPSHOTS — reproducibility + bias audit (§3.3)
-- ------------------------------------------------------------
CREATE TABLE label_vocab_snapshots (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          uuid NOT NULL REFERENCES research_runs(id),
    vocab_epoch     integer NOT NULL,          -- floor(sequence_index / 50)
    facet_items     jsonb NOT NULL,            -- {facet: [{label_id, form, gloss, support}]}
    selection_policy jsonb NOT NULL,           -- slot counts, epsilon, seed, caps
    seed            bigint NOT NULL,
    canon_version   integer NOT NULL,
    item_count      smallint NOT NULL,
    prompt_block_sha bytea NOT NULL,           -- lets us prove what the model saw
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, vocab_epoch)
);

-- ------------------------------------------------------------
-- 9) TRAJECTORY ROLLUP — keyed on leaf label; resolved at read time
-- ------------------------------------------------------------
-- Keyed on the RESOLVED label, not the leaf. The first draft keyed it on the leaf and
-- then SUM()ed distinct_content across aliases in the weekly view — reintroducing exactly
-- the alias multiple-counting §1.3 exists to prevent (one item emitting two aliases
-- counted twice in every sparkline and in the trajectory test's numerator).
CREATE TABLE label_support_daily (
    resolved_label_id uuid NOT NULL REFERENCES labels(id),
    canon_version   integer NOT NULL,
    pub_date        date NOT NULL,             -- content publication date
    observations    integer NOT NULL DEFAULT 0,
    distinct_content integer NOT NULL DEFAULT 0,   -- DISTINCT units, already alias-resolved
    distinct_creators integer NOT NULL DEFAULT 0,  -- the cluster-robust denominator (§6.4)
    platforms       jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (resolved_label_id, canon_version, pub_date)
);
-- Written by the `rollup_label_support` job (§7.2) as a full per-(label, canon_version)
-- recompute from observation_label_map. There is no incremental path: increments cannot
-- be made correct under alias changes.

-- ------------------------------------------------------------
-- 10) CONFIG — one row per canonicalisation generation; thresholds are DATA
-- ------------------------------------------------------------
CREATE TABLE canonicalisation_config (
    canon_version       integer PRIMARY KEY,
    thresholds          jsonb NOT NULL,   -- per-facet, per-lang_bucket taus + gates
    promotion_gate      jsonb NOT NULL,   -- S_min, C_min, diversity, temporal
    suggestion_policy   jsonb NOT NULL,   -- caps, slot mix, p_holdout, epsilon
    embed_model         text NOT NULL,
    norm_pipeline_version text NOT NULL,
    calibration_report  jsonb NULL,       -- §2.9 output that justified these numbers
    activated_at        timestamptz NOT NULL DEFAULT now(),
    activated_by        text NOT NULL,
    notes               text NULL
);

-- ------------------------------------------------------------
-- Deferred constraints: label_observations is created first (it is the source of
-- truth and everything else is derived from it), so its FKs into the derived
-- tables are attached last.
-- ------------------------------------------------------------
ALTER TABLE label_observations
    ADD CONSTRAINT obs_vocab_snapshot_fk
        FOREIGN KEY (vocab_snapshot_id) REFERENCES label_vocab_snapshots(id);

ALTER TABLE observation_label_map
    ADD CONSTRAINT olm_label_fk  FOREIGN KEY (label_id) REFERENCES labels(id),
    ADD CONSTRAINT olm_config_fk FOREIGN KEY (canon_version)
        REFERENCES canonicalisation_config(canon_version);

ALTER TABLE labels
    ADD CONSTRAINT labels_config_fk FOREIGN KEY (canon_version)
        REFERENCES canonicalisation_config(canon_version);

-- Brand names used by the §4.1 form-validity gate are pinned per generation, so the
-- gate cannot change answer because an unrelated subsystem discovered a brand between
-- two rebuilds. Populated from `brands` at the moment a canon_version is activated.
CREATE TABLE brand_name_snapshot (
    canon_version  integer NOT NULL REFERENCES canonicalisation_config(canon_version),
    normalised_name text   NOT NULL,
    PRIMARY KEY (canon_version, normalised_name)
);
```

### 1.5 Authoritative counter SQL

Denormalised counters on `labels` are a cache. This is the definition. Five defects in the
first draft are fixed here and each one changed a number a user would have seen.

```sql
-- Alias resolution is RECURSIVE with a depth cap, not a one-hop COALESCE. The
-- labels_alias_depth trigger makes chains impossible, and this makes a chain that
-- somehow exists loud rather than silently halving a cluster's support.
CREATE OR REPLACE VIEW label_resolved AS
WITH RECURSIVE r AS (
    SELECT m.observation_id, m.canon_version, m.label_id,
           m.label_id AS cur, 0 AS depth
    FROM   observation_label_map m
  UNION ALL
    SELECT r.observation_id, r.canon_version, r.label_id,
           l.canonical_id, r.depth + 1
    FROM   r JOIN labels l ON l.id = r.cur
    WHERE  l.canonical_id IS NOT NULL AND r.depth < 4
)
SELECT o.*, m.canon_version, m.label_id AS leaf_label_id,
       (SELECT cur FROM r
         WHERE r.observation_id = o.id AND r.canon_version = m.canon_version
         ORDER BY depth DESC LIMIT 1) AS resolved_label_id
FROM   label_observations o
JOIN   observation_label_map m ON m.observation_id = o.id;

CREATE OR REPLACE FUNCTION recompute_label_counters(p_label_id uuid, p_canon_version int)
RETURNS void LANGUAGE sql AS $$
WITH obs AS (
    SELECT r.*,
           COALESCE(r.content_item_id::text, 'c:' || r.commerce_item_id::text) AS unit,
           ci.creator_id,
           ci.published_at::date AS pub_date
    FROM   label_resolved r
    LEFT   JOIN content_items ci ON ci.id = r.content_item_id
    WHERE  r.resolved_label_id = p_label_id
      AND  r.canon_version     = p_canon_version
),
-- Platform / query / strategy diversity comes from research_results, the provenance
-- table (01 §8.4). Reading `platform` off content_items, as the first draft did, made
-- every commerce observation platform-NULL: a label seen on two marketplaces and one
-- video platform counted ONE platform, so the >= 2 platform gate silently punished
-- exactly the social-to-commerce pattern brief §17 calls the differentiating capability.
prov AS (
    SELECT DISTINCT o.unit, rr.platform, rr.query_id, rq.strategy
    FROM   obs o
    JOIN   research_results rr
           ON rr.content_item_id = o.content_item_id
           OR rr.commerce_item_id = o.commerce_item_id
    JOIN   research_queries rq ON rq.id = rr.query_id
),
per_creator AS (
    SELECT creator_id, COUNT(DISTINCT unit) AS n
    FROM obs WHERE creator_id IS NOT NULL GROUP BY 1
),
per_query AS (
    SELECT query_id, COUNT(DISTINCT unit) AS n FROM prov GROUP BY 1
),
-- suggested_share and holdout/suggested support are UNIT-weighted. Observation
-- weighting let one verbose item with six near-duplicate emissions dominate the
-- §4.5 prompt-echo trigger, which is a threshold on a share.
units AS (
    SELECT unit,
           bool_or(vocab_mode = 'suggested') AS any_suggested,
           bool_or(vocab_mode = 'holdout')   AS any_holdout,
           max(content_item_id::text)        AS cid
    FROM obs GROUP BY unit
),
agg AS (
    SELECT
      (SELECT COUNT(*) FROM obs)                                        AS observation_count,
      (SELECT COUNT(*) FROM units)                                      AS support_count,
      (SELECT COUNT(DISTINCT content_item_id)  FROM obs)                AS distinct_content,
      (SELECT COUNT(DISTINCT commerce_item_id) FROM obs)                AS distinct_commerce,
      (SELECT COUNT(DISTINCT creator_id)       FROM obs)                AS distinct_creators,
      (SELECT COUNT(DISTINCT platform)         FROM prov)               AS distinct_platforms,
      (SELECT COUNT(DISTINCT strategy)         FROM prov)               AS distinct_strategies,
      (SELECT COUNT(DISTINCT query_id)         FROM prov)               AS distinct_queries,
      (SELECT COUNT(*) FROM units u
         WHERE NOT EXISTS (SELECT 1 FROM prov p WHERE p.unit = u.unit)) AS strategy_unknown_units,
      (SELECT MIN(pub_date) FROM obs)                                   AS first_pub_date,
      (SELECT MAX(pub_date) FROM obs)                                   AS last_pub_date,
      (SELECT COUNT(DISTINCT date_trunc('week', pub_date)) FROM obs)    AS distinct_pub_weeks,
      (SELECT COUNT(*) FROM units WHERE any_holdout)                    AS holdout_support,
      (SELECT COUNT(*) FROM units WHERE any_suggested)                  AS support_suggested
)
UPDATE labels l SET
  observation_count  = agg.observation_count,
  support_count      = agg.support_count,
  distinct_content   = agg.distinct_content,
  distinct_commerce  = agg.distinct_commerce,
  distinct_creators  = agg.distinct_creators,
  distinct_platforms = agg.distinct_platforms,
  distinct_strategies= agg.distinct_strategies,
  distinct_queries   = agg.distinct_queries,
  strategy_unknown_units = agg.strategy_unknown_units,
  first_pub_date     = agg.first_pub_date,
  last_pub_date      = agg.last_pub_date,
  distinct_pub_weeks = agg.distinct_pub_weeks,
  holdout_support    = agg.holdout_support,
  support_suggested  = agg.support_suggested,
  -- COALESCE, because AVG/MAX over zero rows is NULL and these columns are NOT NULL.
  -- The first draft wrote AVG(...) straight into suggested_share, so recomputing a
  -- label whose observations had all been re-pointed raised a not-null violation.
  suggested_share    = COALESCE(
      agg.support_suggested::real / NULLIF(agg.support_count, 0), 0),
  top_creator_share  = COALESCE(
      (SELECT MAX(n)::real / NULLIF(agg.support_count,0) FROM per_creator), 0),
  top_query_share    = COALESCE(
      (SELECT MAX(n)::real / NULLIF(agg.support_count,0) FROM per_query), 0),
  counters_at        = now()
FROM agg WHERE l.id = p_label_id AND l.canon_version = p_canon_version;
$$;
```

`distinct_pub_weeks` uses `date_trunc('week', ...)`, which is ISO (Monday-start) in
Postgres, so it matches the "`>= 2` ISO weeks" wording in §4.1. Commerce units have no
publication date in general, so they contribute to `support_count` but not to the temporal
gate — which is correct, and is now stated rather than implied.

---

## 2. Canonicalisation — the vocabulary-drift machine

### 2.1 The size of the problem, quantified

Per run (~1500 videos + ~2500 commerce records):

| Quantity | Estimate | Basis |
|---|---|---|
| Observations | ~18,000 video + ~12,500 commerce ≈ **30,000** | ~12 labels/video, ~5/commerce record |
| Distinct **raw** surface forms | **7,000–11,000** | open-vocabulary extraction typically yields distinct-form ≈ 23–37% of emissions |
| Distinct forms after lexical normalisation (§2.2) | **5,500–8,500** | expect 15–25% collapse |
| Canonical labels after §2.5 | **800–1,600** | |
| Promoted taxonomy entries (§4) | **250–600** across 14 facets | |

So roughly **6–10× redundancy**: the same concept arrives under 6–10 spellings/phrasings
because each of 1500 LLM calls invents its wording independently. These are *expectations to be
validated on run 1*, not measurements; §9 lists the measurement that confirms or refutes them.

### 2.2 Stage S1 — lexical normalisation (deterministic, logged, reversible)

Applied at ingest to produce `normalised_form`. Each step appends to `norm_steps` so any form
can be explained. Order matters; this is the exact order:

1. **NFKC** Unicode normalisation.
2. **Strip invisibles**: zero-width (U+200B–U+200D, U+FEFF), variation selectors, control chars.
3. **Emoji handling**: remove emoji/pictographs **only if ≥1 word character remains**. If the
   form is purely emoji, keep it verbatim — an emoji can be the label (notably in `humour` and
   `slang`). Boundary: a form that is one word + one emoji loses the emoji; a form that is one
   emoji keeps it.
4. **Character-run collapse**: runs of ≥3 identical letters → 2 (`"sooooo"` → `"soo"`).
   Language-neutral, motivated by social-media orthography (emphatic lengthening). Threshold 3,
   not 2, because genuine doubled letters are common and genuine triples essentially are not.
5. **Punctuation**: hyphens/underscores/slashes → space; strip quotes, brackets and trailing
   `.,!?;:`; collapse whitespace; trim.
6. **Casefold** (`str.casefold()`, not `.lower()`).
7. **Leading-determiner strip** — *only at position 0*, from a per-language closed-class
   function-word list held in `canonicalisation_config.norm_pipeline.determiners`, keyed by the
   language codes in `research_runs.language_codes`. **Not in code, and not in this document.**
   The first draft hard-coded one market's determiner list into the pipeline, three paragraphs
   before §2.3 declared that shipping a hand-written word list for that language "would be
   encoding cultural assumptions" and §8 claimed no such list existed anywhere in the subsystem.
   Three constraints make the config form legitimate rather than the same violation relocated:
   (a) entries must be **closed-class determiners/articles** — a build-time test rejects any
   entry that appears as a content token in the run's own corpus above a low frequency floor;
   (b) the list is market configuration recorded in provenance, never an example, hint or value
   in any prompt (`03-multimodal-analysis` §0.3 rule 6); (c) an empty list is valid and simply
   means no stripping, so an unconfigured market degrades to "slightly more duplicates", never
   to corrupted forms. Internal occurrences are untouched — removing a determiner inside a
   phrase can change the predication.
8. **Lookup-conditioned singularisation**, for language buckets configured with
   `simple_suffix_plural = true`: if the form ends in `s`/`es` and the de-pluralised candidate
   **already exists in the same (facet, lang_bucket) with support ≥ 2**, use the singular.
   Otherwise keep the plural. It never needs an irregular-plural lexicon and never mangles a
   word whose singular is not a real label in this corpus. Boundary: a plural whose singular
   first appears *later* is unified at the next **rebuild**, not merely the next incremental
   pass. This distinction is the reason §1.2 exists: the step is order-dependent, so the
   normalised form of an observation is a function of corpus state, and only a rebuild that
   rewrites `observation_label_map` can re-point the earlier observations. In the first draft
   the normalised form lived on the append-only row and the stated self-healing could not
   happen; the plural and the singular stayed two labels forever.
9. **No morphological stemming for buckets flagged `rich_morphology`.** A language that inflects
   by prefix, suffix *and* infix, or that reduplicates, is not served by an English-shaped
   stemmer: it corrupts forms silently and the corruption is invisible downstream. Where no
   reliable lemmatiser exists for a configured language, the pipeline does nothing, and variant
   unification is handled by embeddings + the trigram channel + the review band instead. Which
   buckets get this treatment is configuration, not a judgement encoded here.
   **Stated as an open question (§9.2).**

Forms colliding after S1 are merged with `canon_method='exact_normalised'` — no threshold, no
embedding call, no risk. This is the free portion of the win.

```python
# aypo/analysis/labels/normalise.py
NORM_PIPELINE_VERSION = "norm-1"

class NormResult(BaseModel):
    normalised_form: str
    steps: list[str]

def normalise_surface_form(
    surface_form: str,
    *,
    facet: str,
    lang_bucket: LangBucket,
    singular_lookup: Callable[[str], bool],   # exists in vocab with support >= 2
) -> NormResult: ...
```

### 2.3 Stage S2 — language tagging without a hardcoded lexicon

A per-label language tag is needed because it is the clustering partition key (§5). Off-the-
shelf language ID is unreliable on 1–3 token strings, and we are forbidden from shipping a
hand-written word list for any market language — that would be encoding cultural assumptions
(brief §1/§34, `03-multimodal-analysis` §0.3), and it would be wrong for code-switched forms
anyway, which is where most of the interesting signal lives.

Instead, derive a **token language-affinity table from the run's own corpus**. The procedure is
written for the general case of `k` configured language codes `L = research_runs.language_codes`;
with `k = 2` (the common case: a market language and a lingua franca) it reduces to the binary
form below.

Subsystem 03 does **not** emit a two-valued document language. It emits free-text
`language_detected` from ASR plus a `languages[]` field with anchors, and it explicitly refuses a
closed language enum. The input here is therefore: for each document, the **set** of codes in
`languages[]` whose anchors cover ≥ 20% of the item's spoken+written text, mapped onto `L` by
exact code match; documents that map to exactly one code in `L` are the only ones that vote.
Documents that map to two or more are code-switched and are excluded from the affinity counts —
they are the thing being measured, not evidence about a token.

For token `t` and language `a ∈ L`, let `d_a(t)` = the number of **distinct documents** whose
single mapped language is `a` and whose transcript+caption text contains `t`:

```
affinity_a(t) = (d_a(t) + 1) / (sum_b d_b(t) + k)        # Laplace, alpha = 1
```

- `t` **leans** to `a` if `affinity_a(t) >= 0.70` and `sum_b d_b(t) >= 5`
- otherwise **shared/unknown**

**Distinct documents, not occurrences.** The first draft counted occurrences and then claimed
"min 5 stops one transcript deciding a token's language" — which it does not: a single video
saying a word five times satisfies an occurrence threshold of 5 on its own, and repetition within
one transcript is exactly the non-independence that makes short-text language ID fragile. Five
distinct documents is a real, if small, independence requirement.

Label-level rule:

| Token composition | `lang_bucket` |
|---|---|
| all content tokens lean to the same `a` | `a` |
| content tokens lean to ≥ 2 different languages | `mix:<a>+<b>` (components sorted) |
| all shared/unknown | fall back to `py3langid` on `surface_form + " " + gloss_en`; if its probability < 0.60 → `und`; if its top code is not in `L` → `other` |
| script not Latin and no `L` member uses that script | `other` |

**Why 0.70 and not 0.5:** the middle band is deliberately wide and is where brand names,
numerals, platform jargon and established loanwords live. Those tokens genuinely belong to both
languages; forcing them to a side would mislabel most code-switched forms. At exactly 5 documents
the token is admitted; at 4 it is `shared/unknown` and contributes no vote.

**Known circularity, stated:** the affinity table is derived from 03's own language attribution,
so a systematic error there (notably 03's OQ-2: `whisper-1` sometimes *translating* rather than
transcribing code-switched speech, which would relabel mixed content as monolingual) propagates
into every bucket here, and the bucket is the clustering partition key. The mitigation is
containment, not correction: a mis-bucketed label produces a visible duplicate across buckets,
never a silent cross-language merge, and the §5.3 link detector will surface the pair for
adjudication. The cross-bucket near-duplicate rate is reported per run as a monitor on this.

The affinity table is recomputed at the start of each canonicalisation pass and versioned, so
`lang` is as re-derivable as everything else.

```python
# aypo/analysis/labels/langid.py
# A bucket key is a plain string loaded from lang_buckets, NOT a Literal or an enum:
# a closed language type is a market assumption (03 §0.3). The alias exists for
# readability only and is validated against the table at the boundary.
LangBucket = str

class TokenAffinity(BaseModel):
    token: str
    doc_counts: dict[str, int]          # distinct documents per configured language code
    affinity: dict[str, float]
    verdict: str                        # a language code, or "shared"

async def build_affinity_table(session, run_id: UUID) -> dict[str, TokenAffinity]: ...
def tag_label_language(
    surface_form: str, gloss_en: str | None, affinity: Mapping[str, TokenAffinity]
) -> tuple[LangBucket, str]: ...
```

### 2.4 Stage S3 — the embedding key (what actually gets embedded)

Embedding the bare surface form is the second classic mistake: a 1–3 word phrase is too
ambiguous, and short-string cosine is noisy. Embed a **glossed key**:

```
embed_key = f"[{facet}] {normalised_form} — {gloss_en}"
```

The extractor is therefore **required** to supply a one-line English gloss per proposed label
(§3.4). The gloss is the disambiguator *and* the cross-language pivot (§5.3).

**This is a cross-subsystem dependency, and it is not yet satisfied.** `03-multimodal-analysis`
§5.3 emits `DiscoveredLabel(label, label_kind?, elaboration?)`, where `elaboration` is optional,
free-text, length-capped at 240 and **not required to be English**; there is no `facet` field and
no per-label language field either. Two load-bearing pieces of this design — the glossed
embedding key and the cross-language pivot — rest on a field the producer does not guarantee.
The resolution is a contract change in 03, not an assumption here:

| Field this subsystem needs | 03 today | Required change |
|---|---|---|
| `gloss_en` (8–20 words, English) | `elaboration: str \| None`, any language | rename/annotate as `gloss_en`, prompt-required, language-checked post hoc (§7.1) |
| `facet` | implied by which array the label sits in | emit the array name as `facet`; the §1.1 mapping table makes this mechanical |
| per-label evidence | `Claim.anchors[]` (1–4) | first anchor → `evidence_quote` / `evidence_timecode_s` |

Until 03 ships it, this subsystem runs in the documented no-gloss degradation (§7.1): the key
falls back to `[facet] form`, the label is excluded from §5.3 cross-language linking, and the
`uncalibrated_bucket`-style caveat is attached. **The vocabulary is usable in that mode; the
cross-language deliverable is not.** That is the honest statement of the dependency, and it is
tracked as a blocking integration item rather than an assumption.

- **Gloss selection**: the *modal* gloss across the label's observations (ties → the gloss of
  the lowest-`created_at` observation). Deterministic.
- **Re-embed rule**: embed at first sight; re-embed when `support_count` crosses 3, and
  thereafter at every 3× growth (3 → 9 → 27 → 81). Rationale: the modal gloss is unstable at
  support 1–2 and stabilises quickly; 3× growth bounds re-embeds at `O(log₃ n)` per label. At
  the boundary (support exactly 3) the re-embed happens; at 2 it does not.
- **Provider**: `openai/text-embedding-3-large`, `dimensions=1536` (fits pgvector HNSW's 2000
  cap), L2-normalised, cosine distance. Jina as fallback provider. Embeddings from different
  providers are **never compared**; `embed_provider` is recorded and a provider change forces
  `needs_recanon = true` on all affected rows.
- **Cost**: ~8,000 distinct keys × ~30 tokens = 240k tokens, plus ~1,500 re-embeds ≈ 45k →
  **~285k tokens ≈ $0.037 per run** at $0.13/Mtok. Embeddings are cached forever by
  `embed_key_sha`, so re-runs and threshold recalibration cost **$0**.

### 2.5 Stage S4 — leader-based incremental clustering

**Not HDBSCAN, not connected components.** Both are wrong here:

- *Connected components over a similarity threshold* **chains**: A~B, B~C, A≁C still merges
  A and C. With 8,000 short forms at τ≈0.86 you reliably get one giant blob per facet.
- *HDBSCAN* is batch-only, non-deterministic under reordering, and gives no stable
  representative — but new labels arrive continuously *during* a run, and the suggestion
  feedback loop (§3) needs a current canonical set every ~50 analyses.

Use **leader clustering with an explicit representative comparison**:

```
# A block is one (facet, lang_bucket) partition at a pinned observation snapshot.
# Support is computed ONCE, at as_of_seq, and does not move during the pass.
Process labels in a block in descending (support_count, then normalised_form, then id) order.
leaders := []
for L in ordered_labels:
    # EXACT scan over the current leader set of this block, not an ANN query.
    cands := [c in leaders if not cannot_link(L, c)]
    cands := cands ∪ trgm_candidates(L.normalised_form, leaders, sim >= 0.45)
    best  := argmax cosine(L, c) over cands          # ties -> smallest c.id
    s     := round(cosine(L, best), 4)

    if must_link(L, best):           attach(L, best, 'human_decision'); continue
    if anti_merge_guard(L, best):    route_to_review(L, best); continue    # §2.6
    if s >= tau_strong(facet, lang) and not lexical_gate_required(facet):
                                     attach(L, best, 'embedding_strong')
    elif s >= tau_merge(facet, lang) and lexical_gate(L, best):
                                     attach(L, best, 'embedding_lexical')
    elif s >= tau_review(facet, lang):
                                     route_to_review(L, best)              # status pending_merge
    else:                            leaders.append(L)
```

**Why an exact scan rather than the `pgvector_knn(..., k=25)` of the first draft.** Three
separate problems, all of which produced a wrong result rather than a slow one:

1. **Recall.** Taking the 25 nearest *labels* and then filtering to leaders can return zero
   leaders when the true nearest leader sits at rank 26 — most likely precisely in a dense
   cluster of aliases, which is where merging matters most. The filter has to be inside the
   search, and with HNSW it is a post-filter unless `hnsw.iterative_scan` is enabled.
2. **Determinism.** HNSW is approximate and its graph depends on insertion order. A rebuild
   from scratch builds a different graph and can return different neighbours, so the claim
   that "re-running produces byte-identical results" was false, and with it I1.
3. **It was never needed.** A block is at most a few thousand rows of 1536 floats — the whole
   label table is ~8,000 × 1536 × 4 B ≈ 49 MB. An exact cosine scan of a block is milliseconds.
   The index was buying nothing and costing correctness.

`O(n·k)` becomes `O(n · |leaders|)` per block, which at these sizes is the same wall clock.
HNSW remains in the schema for the read path (`semantic_search`), where approximation is fine.

Properties that matter:
- **No chaining**: every member is compared to a *leader*, never to a sibling. Cluster radius
  is bounded by `1 − τ_merge`. This holds only while aliases point at leaders — enforced by the
  `labels_alias_depth` trigger (§1.4) and by path compression whenever adjudication or a
  `must_link` merges two leaders: the absorbed leader's aliases are re-pointed in the same
  transaction, and the merge rows record the original parent so the reversal is exact.
- **High-support forms become leaders first**, which is the behaviour you want: the canonical
  representative is a form the models actually produced often.
- **Deterministic — with a precisely stated scope.** Processing order is
  `(support_count DESC, normalised_form, id)`, where support is evaluated at the pinned
  `as_of_seq`. Two consequences the first draft elided: support changes continuously during a
  run, so the **in-run incremental pass is order-dependent on a moving quantity and is NOT
  equal to a rebuild**; and the incremental pass compares new labels only against the leader set
  that existed when they arrived. The incremental result is therefore explicitly **provisional**
  — it exists to keep the suggestion loop (§3) fed with a current canonical set, is marked
  `canon_provisional`, and is **never** the source of a number shown to a user or used by a
  promotion gate. Every user-visible figure comes from the deterministic rebuild at the run's
  final `as_of_seq`. A reconciliation at run end re-derives the whole generation and logs the
  provisional-to-final delta (how many labels changed leader) as a run-quality metric: a large
  delta means the suggestion loop was being fed a materially wrong vocabulary and the cadence
  in §7.2 needs to shorten.

### 2.6 The two guards that stop embeddings from lying

**(a) Lexical gate.** Sentence embeddings place *antonyms and co-hyponyms* very close —
"morning" vs "evening", "indoors" vs "outdoors", "solo" vs "group" routinely exceed 0.80
cosine. Merging those destroys the exact distinctions the product exists to report. So a merge
in `[τ_merge, τ_strong)` additionally requires **at least one** of:

| Signal | Threshold | Note |
|---|---|---|
| Jaccard token-set overlap | `>= 0.50` | after S1 |
| Trigram similarity (`pg_trgm`) | `>= 0.60`, or `>= 0.50` where `lang_buckets.rich_morphology` | affixation and reduplication inflate character distance between true variants; the attribute is set per bucket in configuration, never keyed on a specific language in code |
| Word-boundary substring containment | either direction | `"theme x"` ⊂ `"theme x at home"` |
| Levenshtein distance | `<= 2` **and** `max(len) <= 12` | typo channel only, short forms |

For facets with `lexical_gate_required = true` (`slang`, `product_pairing`) the gate is
mandatory **at every similarity**, including above `τ_strong`: two different slang tokens
meaning roughly the same thing are two different lexical items and must stay distinct, because
in that facet *the token is the object of study*.

**(b) Co-occurrence anti-merge guard.** A single LLM call de-duplicates within its own output —
it does not usually emit two near-synonyms for the same video. So if two labels *frequently
co-occur on the same analysis unit*, that is positive evidence they are **distinct**:

The first draft expressed this as a raw ratio with a fixed threshold:

```
cooc_ratio(A,B) = |units(A) ∩ units(B)| / min(support(A), support(B))
if min(support(A), support(B)) >= 4 and cooc_ratio >= 0.25:  block
```

**That rule is not scale-free, and the justification given for it was arithmetically wrong.**
Under independence with `N` analysed units, `E|A ∩ B| = s_A·s_B/N`, so

```
E[cooc_ratio] = s_A·s_B / (N · min(s_A, s_B)) = max(s_A, s_B) / N
```

The expected ratio does not sit "in low single-digit percents" — it grows **linearly with the
larger label's support**. At `N = 1500`: two labels at support 200/300 have an expected ratio of
0.20 under pure independence, and at 400/400 it is 0.27, *above the threshold*. The rule would
have blocked essentially every merge between frequent labels and routed the head of each facet's
vocabulary to adjudication every run. At the other end it is too trigger-happy: at min support 4,
one shared item gives exactly 0.25 and fires, and a single shared item at that support has a
one-sided probability of ≈ 0.011 under independence — i.e. it fires on noise about 1% of the
time, which across ~1,000 candidate pairs is ~10 spurious blocks per run.

Use the standard scale-free pair: **lift against the independence expectation, plus an exact
test**, and require more than one shared unit.

```
inter   = |units(A) ∩ units(B)|
lift    = inter · N / (support(A) · support(B))
p_hyper = P[X >= inter],  X ~ Hypergeometric(N, support(A), support(B))   # one-sided

if inter >= 2 and lift >= 3.0 and p_hyper <= 0.01:
    block the auto-merge; route to review band
```

Behaviour at the cases that matter (`N = 1500`):

| `s_A` / `s_B` | shared | old ratio rule | lift | `p_hyper` | new rule |
|---|---|---|---|---|---|
| 4 / 4 | 1 | **fires** (0.25) | 94 | 0.011 | no — one shared item is not evidence |
| 4 / 4 | 2 | fires (0.50) | 188 | 3e-5 | **fires** |
| 10 / 10 | 3 | fires (0.30) | 45 | 2e-5 | **fires** |
| 100 / 100 | 25 | fires (0.25) | 3.8 | 9e-10 | **fires** |
| 200 / 200 | 50 | fires (0.25) | 1.9 | 1e-6 | no — 1.9× is not distinctness evidence |
| 300 / 400 | 100 | **fires** (0.33) | 1.2 | 2.5e-3 | no — barely above chance |

Note that the exact test alone is not enough: at large supports a tiny relative excess is
overwhelmingly "significant", which is why the lift floor carries the semantics and the p-value
only suppresses small-count noise. **Why lift 3 and p 0.01:** three times the chance rate is the
smallest excess that survives the clustering in the data (items from one creator or one query
are not independent draws, so the effective `N` is smaller than the nominal one and all lifts are
biased slightly upward); 0.01 keeps expected spurious blocks under ~10 per 1,000 pairs. Both are
in `canonicalisation_config` and both are **(prior)**.

Direction of error is unchanged and deliberate: the cost of a wrong block is a duplicate in the
UI that a user can report; the cost of a wrong merge is a destroyed finding. One further caveat
the first draft did not state — the guard's premise ("a single call de-duplicates its own
output") is weakened in the suggested arm, where the model may emit both a suggested form and its
own near-synonym for the same item. The guard is therefore computed on **holdout-arm units only
when the label has `holdout_support >= 4`**, and on all units otherwise, with which basis was
used recorded in `label_merges.lexical_signal`.

### 2.7 Thresholds by merge posture — a semantic decision, not a tuning knob

A uniform τ is wrong because facets differ in *what the label refers to*. If the referent is a
situation, phrasing is incidental and you should merge freely. If the referent is a specific
token, song, show or product pair, near-misses are **different referents** and merging is
destruction.

Five **posture groups**, not fourteen independent tunings. This matters for §2.10: a per-facet
threshold is a per-facet precision claim, and no affordable calibration set can support fourteen
of those. The group carries the measurement; the per-facet offset within a group is a reasoned
prior that calibration may collapse but is not asked to establish.

| Posture group | τ_review | τ_merge | τ_strong | Lex gate req. | Facets | Reason |
|---|---|---|---|---|---|---|
| **liberal** | 0.76 | 0.84 | 0.93 | no | `occasion`, `behaviour`, `social_context`, `activity`, `content_format` | the referent is a situation or a convention; wording is incidental |
| **moderate** | 0.78 | 0.86 | 0.93 | no | `creator_archetype`, `audience_signal`, `narrative` | archetypes and arcs shade into each other |
| **conservative** | 0.80 | 0.88 | 0.94 | no | `commercial_signal`, `humour` | pricing vs promo vs availability are different claims; humour *styles* merge, specific jokes do not |
| **very conservative** | 0.82 | 0.90 | 0.95 | `product_pairing` only | `cultural_signal`, `product_pairing`, `unexpected` | a specific referent (a song, a show, a practice, `A+B` vs `A+C`) is the object; a near-miss is a *different referent*, and `unexpected` is heterogeneous by construction |
| **lexical** | 0.86 | 0.94 | 0.97 | **yes** | `slang` | the token *is* the object of study; synonyms are distinct lexical items |

Within a group, two per-facet offsets remain as declared priors: `unexpected` takes
`τ_merge + 0.02` (merging destroys the surprise) and `cultural_signal` takes `τ_strong + 0.01`.
All values live in `canonicalisation_config.thresholds`, keyed `(facet, lang_bucket)`, and
`label_facets` carries none of them (§1.4).

**Boundary consistency.** Every group satisfies `τ_review < τ_merge <= τ_strong`. The first
draft asserted a uniform `τ_review = τ_merge − 0.08` in its rationale while the table used −0.10
for three facets; the groups make the relationship explicit rather than nominal.

**Language-bucket adjustment — keyed on an attribute, not a language.** For any bucket with
`lang_buckets.embedding_calibrated = false`: `τ_merge += 0.02`, `τ_review −= 0.04` — i.e. **raise
the auto-merge bar and widen the review band**. Additionally, for any bucket of
`kind = 'mixed'`, the lexical gate is mandatory at every similarity. Rationale: the embedding
model's calibration on short phrases in that bucket is unmeasured, so the confident zone should
be narrower and more decisions should reach adjudication. The asymmetry is intentional:
adjudication costs ~$0.003 per pair under the two-order protocol of §2.8; a wrong silent merge
costs a finding. Buckets flip to `embedding_calibrated = true` only by passing §2.10 **for that
bucket**, which also clears the `uncalibrated_bucket` flag on their labels.

**Cost consequence, stated because it is not small.** A widened band on an uncalibrated bucket
multiplies review-band volume for that bucket by roughly `(0.06 + 0.04) / 0.08 ≈ 1.75×`. If most
of the corpus lands in uncalibrated buckets on run 1, the adjudication line in §7.3 should be
budgeted at ~1.75× its stated value until calibration lands. That is the price of not merging
silently in a space we have not measured, and it is the right price to pay, but it is a real
line item rather than a rounding error.

**Boundary semantics (uniform, so the edge is auditable):** all comparisons are `>=`. A
similarity of *exactly* `τ_merge` merges (given the gate). Exactly `τ_review` enters the review
band. Similarities are stored to 4 decimal places and the active τ values live in
`canonicalisation_config.thresholds`, so any merge can be re-derived and challenged.

### 2.8 The review band — batched LLM adjudication

Pairs in `[τ_review, τ_merge)`, plus everything the anti-merge guard blocked, become
`status='pending_merge'`. They are resolved by batched adjudication, **cached by pair key** so
recalibration is free.

```
Role: You are a lexicographer building a vocabulary from analyst output.
For each numbered pair, decide the relation between the two labels.

FACET: {facet} — {facet_question}

Both labels were proposed independently by analysts describing different pieces of
content. Decide whether they name THE SAME THING or DIFFERENT THINGS.

Rules:
- "same_concept" only if an analyst using one would be fully satisfied by the other,
  with no loss of meaning, specificity, register or language.
- If one is strictly more specific, answer "narrower"/"broader" — NOT same_concept.
- If both are kinds of a shared parent but neither contains the other, answer "sibling".
- If they are the same meaning expressed in DIFFERENT LANGUAGES, answer
  "translation_equivalent", never "same_concept".
- Differences in tone, formality, slang register or in-group marking mean NOT
  same_concept, even when the literal meaning matches.
- Do not reason about what the labels "probably" refer to in the world. Use only the
  label text and its gloss.

PAIRS:
[pair_id: 7f3a1c] A: "{a_form}" (gloss: {a_gloss}; language: {a_lang})
                  B: "{b_form}" (gloss: {b_gloss}; language: {b_lang})
[pair_id: 2b90de] ...

Return one verdict per pair_id above, echoing the pair_id exactly. Do not invent,
merge, reorder or omit pair_ids.
```

**Why opaque ids and no support counts.** The first draft keyed verdicts on a model-authored
`pair_index` integer and showed the model each label's support. Both are avoidable failure
surfaces: an off-by-one or a skipped index silently attaches a verdict to the wrong pair, and an
irreversible merge is the worst place to accept a model-authored join key. The ids are generated
by code (first 6 hex of `pair_key`), echoed back, and validated — **the returned id set must
equal the sent id set exactly, or the batch is re-asked once and then split**; a second failure
marks the pairs `pending_merge` and leaves them distinct. Support counts are removed because
they are an anchoring vector ("seen on 140 items" invites agreement with the frequent form) and
contribute nothing to a question about meaning.

```python
# aypo/analysis/labels/adjudicate.py
class PairVerdict(BaseModel):
    pair_index: int
    relation: Literal["same_concept","narrower","broader","sibling",
                      "related","unrelated","translation_equivalent"]
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(max_length=240)

class PairBatchVerdict(BaseModel):
    verdicts: list[PairVerdict]

async def adjudicate_pairs(
    pairs: Sequence[CandidatePair], *, facet: str, batch_size: int = 40,
    effort: Literal["low","medium"] = "low",
) -> list[PairVerdict]: ...
```

Call shape: `claude-opus-5`, `thinking={"type":"adaptive"}`,
`output_config={"effort":"low"}`, `client.messages.parse(output_format=PairBatchVerdict)` →
`response.parsed_output`.

**Mapping — agreement, not self-reported confidence.** Every band pair is asked **twice, in both
presentation orders** (A/B and B/A), in different batches so the model does not see its own
earlier answer:

- `same_concept` in **both** orders → merge (`llm_adjudicated`).
- `translation_equivalent` in both orders → **no merge**, write a `label_links` row (§5).
- `narrower`/`broader` agreeing in both orders (with the direction correctly inverted on the
  swap) → `label_links` hierarchy row, both stay canonical.
- any disagreement between the two orders → `relation = 'disputed'`, stay distinct, surface in
  the curation queue. Order sensitivity on a symmetric question is direct evidence the pair is
  not clearly same-or-different, which is exactly what we want to know.
- `sibling`/`related`/`unrelated` → stay distinct.

Verdicts are cached by `pair_key` so a pair is never re-asked. `judge_confidence` is stored for
audit and used to prioritise the human review queue; **it is not a gate**. The first draft
merged on `same_concept AND confidence >= 0.70`, which makes an unverifiable model-authored
float the licence for the one operation in this subsystem that destroys information. A model
that is confidently wrong — the actual failure mode for near-synonyms — passes that gate every
time. Order agreement costs one extra call per pair and tests something the model cannot
self-report.

`max_tokens` is set explicitly to `40 × 80 = 3,200` with `effort="low"`; a `max_tokens` stop is
treated as a batch failure and re-split, never as a partial result.

**Cost:** expect 600–1,200 band pairs/run, now asked twice. At 40 pairs/call → 30–60 calls,
~2.6k input and ~1.8k output each → ~160k in ($0.80) + ~110k out ($2.75) ≈ **$3.55 per run**, and
~$0 on reruns (both orders are cached under one `pair_key`). This is roughly double the first
draft's $1.80 and is the single largest cost increase from this review; it buys the removal of a
model-authored merge licence.

### 2.9 Choosing the canonical representative

Over a cluster's members (all competing to be the display form):

```
rep_score = 0.45 * support_norm          # support / max_support_in_cluster
          + 0.20 * centrality            # support-weighted mean cosine to other members
          + 0.15 * source_diversity_norm # distinct_creators / max in cluster
          + 0.10 * brevity               # 1.0 at 2-4 tokens, 0.6 at 1 or 5, 0.2 at >=6
          + 0.10 * lexical_typicality    # token overlap with the cluster's support-weighted
                                         # token distribution
          - 0.25 * contains_minority_proper_noun   # a brand/handle not shared by >50% of
                                                   # cluster support
          - 0.15 * is_sentence_like      # >6 tokens or contains a finite clause marker
```

Every term is normalised to [0,1] before weighting, and the two penalties are applied after,
so `rep_score ∈ [−0.40, 1.00]`. Two terms the first draft left undefined:

- **`contains_minority_proper_noun`** needs no NER and no language model. A token counts as a
  proper noun if it is in `brand_name_snapshot`, or matches a known creator handle, or its raw
  surface occurrences across the corpus are capitalised in ≥ 80% of non-initial positions
  (≥ 5 occurrences required). "Minority" means the token appears in members holding ≤ 50% of
  the cluster's support. All three tests are corpus-derived and language-neutral.
- **`is_sentence_like`** is `>6 tokens` OR the form contains a token that the corpus shows in
  verb-like position ≥ 80% of the time; where no such statistic exists, token count alone.
  No grammar list, in any language.
- **`lexical_typicality`** is the support-weighted mean of per-token inverse-rank within the
  cluster's token distribution, scaled to [0,1].

Hard constraints:
1. The representative **must be a surface form actually observed**. Never a model-synthesised
   or human-prettified string. The UI shows words the data actually contained.
2. `display_form` = the most frequent **raw** surface form of the winning member (original
   casing and diacritics preserved); `normalised_form` stays the identity key.
3. **Hysteresis:** the incumbent is replaced only if a challenger's `rep_score` exceeds it by
   **≥ 0.10**. Without this, labels flap between runs as support shifts by one observation,
   which breaks saved UI filters and makes week-on-week screenshots incomparable.

### 2.10 Threshold calibration (how these numbers stop being priors)

Every τ above is **(prior)**. The first draft's calibration plan could not establish what it
claimed, and this is the most consequential correction in this review: it was the single named
remedy for every threshold in the subsystem.

**Why 300 pairs across 11 facets cannot support "precision ≥ 0.95 per facet."** Precision is a
binomial proportion. With zero observed errors in `n` labelled merges, the one-sided 95%
Clopper–Pearson lower bound is `0.05^(1/n)`:

| `n` (error-free pairs in the stratum) | 95% lower bound on precision |
|---|---|
| 27 (= 300 / 11 facets) | 0.895 |
| 40 | 0.928 |
| **59** | **0.951** |
| 120 | 0.975 |

So ~59 consecutive error-free pairs are needed **per stratum** just to claim 0.95 with a 95%
lower bound — and that is the best case, with zero errors. 300 pairs spread over 11 facets × 8
similarity bands gives ~3 pairs per cell. Any τ chosen that way is noise.

Two further defects: the human subsample was **the 100 the model was least confident about**,
which cannot bound the precision of *confident* merges — and a confidently-wrong verdict on a
near-synonym is the exact failure mode. And the same model, with the same prompt family, both
produced the "ground truth" and adjudicates production pairs, so agreement between them measures
nothing.

**The corrected procedure.**

1. **Stratify by posture group (5), not facet (14), and by bucket calibration status.** Per
   §2.7 the group carries the threshold; per-facet offsets stay declared priors.
2. **Concentrate the sample in the decision region.** 60% of pairs drawn from
   `[τ_merge − 0.06, τ_merge + 0.04]`, 40% spread over `[0.70, 0.99]` to shape the curve.
   **≥ 120 pairs per posture group per language bucket in use** — so ~600 pairs for a
   single-bucket run, ~1,200 where two buckets carry real volume. This is 2–4× the first
   draft's budget and it is the minimum that buys a statement.
3. **Human ground truth on a *random* stratified subsample**, not the model's uncertain tail:
   100 pairs per group drawn uniformly at random from the group's sample, labelled by two
   annotators, with Cohen's κ reported. The LLM labels the remainder **only if** it agrees with
   the humans on ≥ 95% of that random subsample; otherwise the LLM labels are discarded and the
   budget goes entirely to human labelling at reduced coverage. Stored in `label_pair_gold`.
4. **Pick τ_merge per group** as the smallest threshold whose precision **lower confidence
   bound** ≥ 0.90, and report the point estimate and the interval. Precision, not F1: a false
   merge is unrecoverable from the user's point of view; a false split is a visible duplicate
   they can report. **0.90, not 0.95** — at `n = 120` with 2 errors the lower bound is ~0.93, so
   0.95 with evidence is reachable only at several hundred pairs per stratum. Claiming 0.95 off
   120 pairs would be the same error in a new costume. If a group cannot reach an LCB of 0.90
   at any τ ≤ 0.95, that group's facets become **lexical-gate-mandatory** (treated like `slang`)
   rather than taking a higher τ.
5. **Set `τ_review = τ_merge − 0.08`**; widen to `−0.12` for any group whose precision curve is
   still below the bound at 0.92.
6. Write the result into a new `canonicalisation_config` row with the full
   `calibration_report` (per-stratum n, errors, point estimate, Clopper–Pearson interval, κ),
   bump `canon_version`, rebuild from `label_observations` (free: embeddings and pair verdicts
   are cached). Set `lang_buckets.embedding_calibrated = true` only for buckets that actually
   met the bar, and clear `uncalibrated_bucket` on their labels.

**Cost.** ~600–1,200 pairs, both orders, at `effort="high"`: ~$8–16 of model time, plus roughly
10–20 person-hours of annotation. That is the real price of the sentence "our merges are 90%
precise", and it is small against the cost of being wrong about it in front of the customer.

**This calibration is a required deliverable, not an optimisation.** Until it runs, the promoted
vocabulary is provisional, every bucket carries `uncalibrated_bucket`, and the API says so
(§6.1 rule 7). Shipping the priors unvalidated remains the single largest quality risk in this
subsystem (§9.1).

---

## 3. Why not just give the extractor a fixed list — and what we do instead

### 3.1 The argument against a closed label list

A closed list would make drift vanish. It is still forbidden, and would be wrong even if it
weren't.

1. **It is a prior over answers, which §1 and §34 forbid outright.** A list of allowed
   occasions, behaviours, cultural signals or slang terms *is* "predefined findings" and *is*
   "taxonomy encoding presumed consumer associations". §1's acceptance test — "capable of
   discovering something the URC marketing team did not already tell it" — becomes
   structurally unsatisfiable: the system's ceiling is the list author's imagination, and the
   list author is us.
2. **It converts discovery into classification, and hides the error.** Given a closed list, an
   LLM will map a genuinely novel observation onto the nearest allowed label rather than
   refuse. The output then looks *more* confident and *more* complete while being wrong, and
   the error leaves no trace — there is no "none of these" signal to count. An open extractor's
   failure mode (a messy duplicate) is visible and fixable; a closed extractor's failure mode
   (a silently miscategorised novelty) is invisible.
3. **It corrupts measurement, which breaks trend detection.** With a fixed list, label
   frequency measures *list design*, not culture. §22's growth/decline classes would then be
   detecting changes in our own vocabulary choices. Open labels make frequency a property of
   the data.
4. **It cannot represent the language findings.** `slang` and the code-switching signal (§5)
   are about *which words people actually use*. A predefined slang list answers that question
   before asking it.

### 3.2 What we do instead: controlled-vocabulary **feedback**

Drift is solved **after** extraction (§2), not before it. Extraction stays open. To get
*consistency* without constraining discovery, each extractor call is shown a small, rotating
sample of vocabulary already discovered **in this same dataset**, explicitly as
non-binding suggestions.

Three properties make this feedback rather than control:
- Suggestions are **derived from the data**, never authored by us. On the first ~50 analyses
  the list is empty and the system is purely open.
- Suggestions are **capped, rotated and partly random**, so no fixed frame is ever applied.
- A **holdout arm** of calls sees no suggestions at all, giving an uncontaminated control
  against which the bias is measured (§3.5). This is the part that makes the honesty claim
  checkable rather than rhetorical.

### 3.3 Suggestion selection — exact policy

**Cap: 12 per facet, ≤ 60 total per call.**
*Why 12:* anchoring grows with list length, and above ~15 items per facet models begin to treat
a list as an enumeration to be *covered* (they start assigning something from every facet).
12 is enough to consolidate the head of each facet's vocabulary. *Boundary:* a facet with fewer
than 12 canonicals shows all of them; a facet with **0** shows nothing and the prompt says that
facet is **unexplored so far** (which, usefully, invites exploration rather than silence).
*Why ≤ 60 total:* a 60-item block is ~600 tokens, which keeps the suggestion block a small
fraction of a multimodal prompt. Facets are allocated their 12 in `sort_order` until 60 is
reached, cycling, so no facet is systematically starved.

**Slot mix per facet (12 slots).** Every pool below ranks and filters on **`support_suggested`**
— support from the suggested arm only — never on `support_count`.

| Slots | Pool | Purpose |
|---|---|---|
| 6 | highest `support_suggested` canonicals with `support_suggested >= S_min` (§4.1) | stability anchor — the forms worth converging on |
| 3 | highest `support_suggested` in the last 7 publication days **or** last 300 analysed units | keeps feedback current; lets a late-emerging concept consolidate |
| 3 | **ε-random** uniform sample of canonicals with `support_suggested ∈ [3, S_min)` | gives the mid-tail a chance to consolidate, **and** makes the block vary across epochs, which both dilutes and *exposes* anchoring |

**Why `support_suggested` and not `support_count`.** §3.2 claims the holdout arm "never
contributes to building" the suggestions, and the first draft did not implement that: selection
read `support_count`, which aggregates both arms, so a concept discovered only in the holdout arm
could be promoted into the block shown to the suggested arm. That breaks two things at once — the
suggested arm's apparent consistency is partly borrowed from the control, and bias metric 6
("canonicals reaching `emerging_candidate` in holdout with zero suggested-arm observations")
becomes self-defeating, since the mechanism it measures actively erases its own evidence. One
column, and the claim becomes true.

Explicitly **not** pure top-support: that is a rich-get-richer loop in which the first 50
videos' phrasing becomes the permanent vocabulary of the dataset.

**Determinism and prompt caching.** Seed = `hash(run_id, vocab_epoch)` where
`vocab_epoch = sequence_index // 50`. One snapshot per (run, epoch) is stored in
`label_vocab_snapshots`, so:
- a re-run reproduces the exact block the model saw (reproducibility),
- the block is **stable across 50 consecutive calls**, so it can sit inside the cached prompt
  prefix. Cost of suggestions with caching: 30 epochs × 600 tok cache-write at $6.25/Mtok
  ≈ $0.11, plus ~1,470 cache reads × 600 tok at $0.50/Mtok ≈ $0.44 → **~$0.55/run**, versus
  **$4.50/run** uncached. (Claude Opus 5: $5.00/Mtok input, cache write 1.25×, cache read 0.1× —
  the figures above are correct for that model.)

  **Three preconditions, which the first draft left implicit and which the saving depends on
  entirely.** (a) The prompt needs **two cache breakpoints**: one after subsystem 03's stable
  instruction block, one after the suggestion block. With a single breakpoint at the end, the
  epoch rotation changes the prefix and the *whole* prefix is re-written each epoch — at a 4k
  instruction block that is 30 × 4k ≈ 120k cache-write tokens ≈ $0.75, not $0.11, and it also
  wipes 03's own cache benefit. (b) The suggestion block must sit **after** everything stable and
  **before** all per-item content; anything volatile above it (a timestamp, an item id) makes the
  cache read rate zero. (c) 5-minute TTL is refreshed by each read, so it holds while consecutive
  analyses start less than 5 minutes apart; a slower or bursty pipeline should take the 1-hour TTL
  (2× write) instead. Verify with `usage.cache_read_input_tokens` — a zero there means the saving
  is not happening, and the line should be re-costed at $4.50.

  This is a coupling with subsystem 03, which owns the prompt assembly. It belongs in the
  integration contract, not in a cost table.

**Holdout arm: `p_holdout = 0.15`**, assigned by `hash(content_item_id) % 100 < 15` — stable
per item, so the same item is always in the same arm across reruns. *Why 15%:* at 1500 videos
that is ~225 control units ≈ ~2,700 observations, enough to compare facet-level head
distributions and estimate Heaps' β, while costing only a 15% loss of consistency benefit.
*Boundary honesty:* 225 units is **not** enough to compare the tails of two distributions; §3.5
says so explicitly and the UI must not imply otherwise.

**Support is never rendered.** `SuggestionItem.support_suggested` exists for selection and for
the reproducibility record; the rendered block (§3.4b) shows forms only. Showing counts would
both anchor the model toward frequent forms and hand it numbers it might restate.

```python
# aypo/analysis/labels/vocab_feedback.py
class SuggestionItem(BaseModel):
    label_id: UUID; facet: str; form: str; gloss_en: str | None
    support_suggested: int                      # selection + audit only; NEVER rendered
    shown_rank: int                             # post-shuffle position, recorded for metric 3
    slot: Literal["anchor","recent","explore"]

class SuggestionBlock(BaseModel):
    snapshot_id: UUID | None     # None in the holdout arm
    vocab_mode: Literal["suggested","holdout"]
    items_by_facet: dict[str, list[SuggestionItem]]
    prompt_text: str             # rendered block, ready to paste into the extractor prompt
    block_sha: bytes

async def build_suggestion_block(
    session, *, run_id: UUID, content_item_id: UUID, sequence_index: int,
    per_facet_cap: int = 12, total_cap: int = 60, p_holdout: float = 0.15,
) -> SuggestionBlock: ...
```

### 3.4 Exact prompt wording

This subsystem owns the **label-emission contract** inside the extractor prompt (subsystem 03
owns the surrounding multimodal prompt). Two blocks.

**(a) The emission instruction — present in BOTH arms, identical:**

```
LABELS — OPEN VOCABULARY

For each facet below, propose zero or more short labels describing what you actually
observed in THIS item. There is no list of allowed labels. You are describing data, not
filling in a form.

Rules:
- Propose a label ONLY if this item contains direct evidence for it. Zero labels for a
  facet is a correct and common answer. Do not reach.
- Keep each label 1-5 words. Name the thing, not a sentence about the thing.
- Write the label in the LANGUAGE ACTUALLY USED in the content. Do not translate it into
  English. If the content code-switches within the phrase, keep the code-switched form.
- Write the gloss in English, 8-20 words, so a reader who does not speak that language
  knows what the label means.
- Give one short verbatim quote or a timecode as evidence for each label.
- If you observed something notable that fits none of the facets, emit it under
  "unexpected" rather than forcing it into the nearest facet.
- Do not infer demographics, income, or intent that is not directly evidenced.
- Rate your own confidence 0.0-1.0 per label.

FACETS:
  occasion           — in what situation or moment is this happening?
  behaviour          — what are people actually doing?
  social_context     — who is present, and in what relation to each other?
  activity           — what activity is under way around or alongside the item?
  cultural_signal    — what specific cultural reference, practice or shared touchstone appears?
  slang              — what informal or in-group words or phrases are used? (the words themselves)
  humour             — what kind of humour, if any, is operating?
  narrative          — what story or structure does the content tell?
  creator_archetype  — what kind of creator or account does this appear to be?
  content_format     — what recognisable content format or structure is this?
  product_pairing    — what products or items are shown or described being used together?
  audience_signal    — what does the content tell you about who it is addressed to?
  commercial_signal  — what commercial or purchase-related signal appears?
  unexpected         — anything notable that none of the above captures.
```

The facet block is rendered from `label_facets.question`, so it cannot drift from the database,
and no facet line may name a value. `language_observed` is requested as free text ("name the
language in your own words, or leave it blank") — never as a choice from a list.

**(b) The suggestion block — `suggested` arm only, appended after (a), in the cached prefix:**

```
VOCABULARY OBSERVED ELSEWHERE IN THIS DATASET (SUGGESTIONS, NOT A LIST OF OPTIONS)

Other analysts, looking at DIFFERENT items in this dataset, have used the labels below.
They are shown only so that when you mean the same thing, you can use the same words.

How to use them:
- If one of these says exactly what you observed, reuse it verbatim. That is helpful.
- If one is only APPROXIMATELY right, DO NOT use it. Write your own label instead.
  A precise new label is more valuable to us than an approximate reused one.
- These are not categories and not a checklist. Covering them is NOT a goal. Most of
  them will be irrelevant to this item, and ignoring all of them is a perfectly good
  outcome.
- The absence of something from this list means nothing. It is a sample of what has been
  seen so far, not a definition of what exists.

  occasion:         {12 forms, shuffled}
  behaviour:        {...}
  cultural_signal:  (nothing observed for this facet yet)
  ...

For each label you emit, set reused_suggestion = true only if you took it verbatim
from the list above.
```

Deliberate wording choices: "not a list of options"; the explicit *preference for a new label
over an approximate reuse*; "covering them is NOT a goal"; "the absence of something from this
list means nothing" (counters the implicit-negation inference that lists create); and facets
with no vocabulary are shown as explicitly empty rather than omitted, so an empty facet does
not read as a facet that does not apply.

Order within each facet is **shuffled** by the epoch seed, which converts rank effects into
measurable noise rather than a permanent bias (§3.5, metric 3).

**(c) The emission schema** (used with `client.messages.parse`):

```python
# aypo/analysis/labels/schemas.py
# The facet list is loaded from label_facets at startup, not frozen in a Literal,
# so adding a facet is a seed row (§1.1). The Literal below is generated.
Facet = Literal["occasion","behaviour","social_context","activity","cultural_signal",
                "slang","humour","narrative","creator_archetype","content_format",
                "product_pairing","audience_signal","commercial_signal","unexpected"]

class LabelProposal(BaseModel):
    """MODEL-FACING schema: deliberately permissive. See the two notes below."""
    facet: Facet
    surface_form: str
    gloss_en: str | None = None
    language_observed: str | None = None   # FREE TEXT. Never an enum, never a bucket.
    evidence_quote: str | None = None
    evidence_timecode_s: float | None = None
    confidence: float | None = None
    reused_suggestion: bool = False      # self-report: cross-check only, never trusted

class LabelProposalSet(BaseModel):
    proposals: list[LabelProposal]
```

**Note 1 — `language_observed` must not be an enum.** The first draft declared it
`Literal["en","tl","taglish","other","unclear"]`. That is a closed list of the first market's
languages placed directly into the extractor's prompt schema, which violates brief §34 and three
separate clauses of `03-multimodal-analysis` §0.3 ("A closed language enum is a market
assumption and is forbidden"; "Any language ... including as an example, a hint ... in prompt
text"; "Any market, country or locale string in the analysis prompt"). It also told the model
which languages to expect before it had looked at anything. It is now free text, used only as a
weak cross-check against the corpus-derived bucket of §2.3, and the bucket itself is data.

**Note 2 — the length and range constraints are gone, deliberately.** Anthropic's structured
outputs do **not** support `minLength`/`maxLength`, numeric `minimum`/`maximum`, or complex array
constraints; the SDK strips them from the schema it sends and validates them **client-side**.
With `messages.parse()`, a single 9-character gloss or one over-long rationale therefore raises a
`ValidationError` that discards **the entire `LabelProposalSet`** — every label for that item,
including the good ones. For the ingest path of a subsystem whose §1.1 argument is that "a
discarded observation is not [recoverable]", that is the worst possible failure mode, and it is
invisible: the item simply has no labels. The constraints move to the prompt text (where they
belong, as instructions) and to **per-proposal** validation at ingest:

```python
def ingest(raw: LabelProposalSet) -> tuple[list[LabelProposal], list[dict]]:
    good, rejected = [], []
    for p in raw.proposals[:60]:                      # cap applied here, not in the schema
        why = validate_proposal(p)                    # form length, facet known, gloss present
        (good.append(p) if why is None
         else rejected.append({"raw": p.model_dump(), "reason": why}))
    return good, rejected                             # rejected -> label_observation_rejects
```

One bad proposal costs one proposal. Rejects are stored, counted per
`(extractor_version, prompt_version, reason)`, and a rising reject rate is a prompt-quality
alarm — which is strictly more information than a swallowed exception.

`was_suggested` is **computed by us** (normalised-form match against the stored snapshot), not
taken from `reused_suggestion`. Models over- and under-report reuse; the self-report is kept
only as a cheap sanity cross-check, and a persistent gap between the two is itself a prompt-
quality signal worth alerting on.

### 3.5 Honest analysis: does this bias discovery? Yes. Here is how we measure it.

**It does.** Showing a model any vocabulary makes that vocabulary more likely. There is no
version of this that is bias-free. The design position is: accept a *bounded and measured*
bias in exchange for a tractable vocabulary, keep an uncontaminated control arm, and **publish
the bias measurement next to the vocabulary** so a user can discount it. All metrics are
computed per facet on the **post-canonicalisation resolved** distribution, because pre-
canonicalisation counts confound bias with drift.

**Every arm comparison is rarefied to a matched sample size before it is read.** The arms are
~1,275 and ~225 units. Vocabulary statistics are strongly sample-size dependent — Heaps' `V`
grows sublinearly in `n`, hapax-per-1,000 *falls* with `n` by construction, and JSD between two
multinomials is positively biased when one is small. As specified in the first draft, metrics 1,
4 and 5 would have fired on the size difference alone, in the alarming direction, on a perfectly
unbiased system. The protocol is therefore: draw `B = 200` bootstrap subsamples of
`n = n_holdout` units from the suggested arm, compute the statistic on each, and compare the
holdout value against that reference distribution. Report the point estimate **and the 90%
interval**; an alarm fires only when the interval excludes the no-effect value.

| # | Metric | Definition (all rarefied to `n_holdout`, 200 resamples) | Healthy | Action if breached |
|---|---|---|---|---|
| 1 | **New-label rate / Heaps' β** | Distinct canonicals `V(n)` vs units `n`; compare `V_hold(n_holdout)` against the bootstrap distribution of `V_sug(n_holdout)`. Report `β` from a log-log fit for description only — never compare two `β`s fitted over different `n` ranges. | `V_sug(n_hold)` 90% interval **overlaps or exceeds** `V_hold` | otherwise: cap 12→8, `p_holdout` 0.15→0.25, explore slots 3→5; re-measure after 300 units |
| 2 | **Suggestion uptake** | suggested-arm emissions whose normalised form matches a shown item, over all suggested-arm emissions in facets with a non-empty block | **0.35–0.65** *(prior, unmeasured — see §9.4)* | `<0.35`: feedback ineffective — glosses likely poor; inspect. `>0.80`: extractor behaving as a classifier — apply the metric-1 action immediately |
| 3 | **Positional bias** | Per shown item: uptake rate = (times reused) / (times shown). Spearman ρ between `shown_rank` and uptake rate, computed over items, with a per-epoch block permutation null (999 permutations). | 90% of the permutation null contains the observed ρ | a surviving effect means the model reads the list as ranked — move to an unordered bulleted render and re-measure |
| 4 | **Arm divergence** | Jensen–Shannon divergence (log₂) between arms' canonical distributions, per facet. Compared against a **permutation null**: reassign arm labels across units 999 times and recompute. | observed JSD inside the null's 95th percentile | flag the facet in the UI as *"vocabulary may be anchored"* and show both arms' top-20 side by side |
| 5 | **Tail richness** | hapax (support-1) canonicals at matched `n`, suggested-arm bootstrap vs holdout | holdout hapax count inside the bootstrap 90% interval | anchoring compresses the tail first; same action as metric 1 |
| 6 | **Suppressed discovery** | canonicals reaching `emerging_candidate` in the holdout arm with **zero** suggested-arm observations, listed by name | report always; no threshold | direct candidates for "things the suggestion loop hid"; force-surfaced in the UI as *holdout-only discoveries* |

**Multiplicity.** This is 6 metrics × 14 facets ≈ 84 comparisons per run. At a nominal 10% false
alarm rate that is ~8 spurious facet flags every run, which would make the bias report noise and
train everyone to ignore it. Metrics 1, 3, 4 and 5 are therefore evaluated with
**Benjamini–Hochberg FDR control at q ≤ 0.10 across the whole family**, and the report prints the
raw p-value, the q-value and the family size. Metrics 2 and 6 are descriptive and are never
presented as tests.

**Label-invariance.** Every metric here is computed on the canonical distribution, which is a
function of τ. A threshold change moves all of them. `FacetBiasReport` therefore carries
`canon_version`, and **bias reports from different `canon_version`s must not be compared** — the
API refuses to put them on one axis.

Honest limitations, to be stated in the bias report itself, not buried here:
- With `p_holdout = 0.15` the holdout arm has ~225 video units. Metrics 1, 2, 4 and 5 are
  adequately powered at the facet-head level. Metric 6 and any tail comparison are
  **underpowered**; a zero count is not evidence of no suppression.
- The two arms are not independent of the vocabulary: the suggested arm *built* the vocabulary
  the suggested arm sees. β_hold is therefore the cleaner discovery estimate and should be the
  number quoted when anyone asks "how much is the system discovering?"
- A sufficiently strong anchoring effect would *also* suppress the labels needed to detect it.
  The only real protection is that the holdout arm never receives suggestions and never
  contributes to building them — so it cannot be captured by the loop. That second half is true
  only because selection reads `support_suggested` (§3.3); with `support_count` it was false,
  and the protection was rhetorical.
- The arms are not independent *samples*. Assignment is by `hash(content_item_id) % 100`, which
  is a random split of the analysed corpus — not of the category — so both arms inherit the
  same query-selection, ranking and survivorship bias (§6.1 rule 2). The holdout arm is a valid
  control for *the suggestion loop's effect on labelling*. It is not a sample of the world, and
  no holdout figure should be presented as one.
- Everything here is measured per facet on the final canonical distribution, so a facet with few
  canonicals has wide intervals on every metric. The report prints `n_matched` beside each
  number for exactly this reason.

```python
# aypo/analysis/labels/bias_metrics.py
class Rarefied(BaseModel):
    observed_holdout: float
    suggested_at_matched_n: float            # bootstrap mean
    ci90: tuple[float, float]
    p_value: float
    q_value: float                           # Benjamini-Hochberg, family = all facets x metrics

class FacetBiasReport(BaseModel):
    facet: str
    canon_version: int                       # metrics are tau-dependent; never compare across
    n_suggested: int; n_holdout: int; n_matched: int
    vocabulary_size: Rarefied                # metric 1
    hapax: Rarefied                          # metric 5
    arm_jsd: Rarefied                        # metric 4, permutation null
    rank_spearman: Rarefied                  # metric 3, block permutation null
    uptake_rate: float                       # metric 2, descriptive
    heaps_beta_suggested: float; heaps_beta_holdout: float   # descriptive only, NOT compared
    holdout_only_labels: list[UUID]
    verdict: Literal["ok","watch","anchored"]
    caveats: list[str]

async def compute_bias_report(session, run_id: UUID) -> list[FacetBiasReport]: ...
```

---

## 4. Promotion to normalised taxonomy, and demotion

### 4.1 The promotion gate

A canonical label becomes a `taxonomy_entries` row only when **all** of the following hold.
"Support" is `support_count` per §1.3.

| Gate | Value | Why this number / boundary |
|---|---|---|
| Support | `>= 8` distinct analysis units | Expected ~1,600 observations per facet. Under a Zipf-ish distribution the 60th-ranked label in a facet lands in single digits, so 8 sits just above the floor where single-creator catchphrases and per-call phrasing tics live, while still admitting genuinely niche concepts. At exactly 8 it promotes. |
| Distinct **content** items | `>= 4` | A real gate, unlike the first draft's `>= 6`, which was **vacuous**: it was tested against the same distinct-unit count as the `>= 8` support gate, so it could never bind, and its stated justification ("one listing can produce several records") contradicted the counting rule it cited. The binding version is a *social* floor: a label supported entirely by marketplace listings is a commerce finding, not a cultural one, and must not enter a vocabulary the UI uses to describe consumer behaviour. A label with `distinct_content < 4` can still promote **if** `distinct_commerce >= 8`, with `promotion_caveats = ['commerce_only']`. |
| Distinct creators | `>= 4` | Four independent creators is the smallest number at which "several unrelated people said this" is defensible. At 3, one creator network or one trend-participation chain explains it. Commerce units have no creator and do not count toward it. |
| Distinct platforms | `>= 2` | A pattern visible on one platform may be a platform artefact (ranking, format, audience). Computed from `research_results`, so marketplaces count as platforms. **Degradation (§7.1):** where the run reached only one platform, substitute `distinct_creators >= 7` **and** `distinct_queries >= 3`, and set `promotion_caveats = ['single_platform']`. |
| Distinct discovery strategies | `>= 2` distinct `research_queries.strategy` | **The product-specific gate.** A label appearing only under `theme` queries may be an echo of the query phrasing that found the content — we searched for a thing and then "discovered" it. Two strategies means the label survived at least one sampling frame it was not selected for. At exactly 2 it promotes. **If `strategy_unknown_units > 0.2 × support_count`** the provenance is too incomplete to evaluate the gate: it is skipped and `promotion_caveats = ['strategy_unknown']` is set, rather than silently failing every label (a `COUNT(DISTINCT)` over missing provenance returns 0 and would block the entire taxonomy). |
| Single-query dominance | `top_query_share < 0.60` | The sharper form of the same concern: a label that arrives almost entirely through one query is a property of that query. Cheap, and it does not depend on strategy labelling being complete. |
| Temporal spread | `last_pub_date − first_pub_date >= 14 days` **and** `distinct_pub_weeks >= 2` | Uses **content publication dates**, never analysis timestamps — all analysis timestamps are "today", so analysis-time spread measures our crawl schedule, not culture. 14 days is the shortest window in which a pattern can be shown to survive a single news/trend cycle. **Boundary:** if the corpus itself spans < 14 days, substitute `>= 3 distinct publication days` and set `promotion_caveats = ['narrow_window']`. If `first_pub_date IS NULL` for ≥ 50% of units (common for web pages and marketplace records, where publication dates are often absent), the gate is skipped with `promotion_caveats = ['no_publication_dates']` and the label is **barred from any trajectory claim** (§6.4). |
| Not a near-duplicate | `max cosine to any canonical in the same (facet, lang_bucket) < τ_merge`, **excluding** any pair with an active `cannot_link`, a `disputed`/non-`same_concept` adjudication, or an anti-merge guard block | Without the exclusion this gate contradicts §2.6 outright: the guard exists to keep deliberately-distinct high-similarity pairs apart, and the unqualified rule would then refuse to promote **either** member of such a pair, permanently. Deciding a pair is distinct must not make both halves unpromotable. |
| Form validity | 1–6 tokens; not pure punctuation/numeral; not equal to the facet name; not sentence-like; not a bare brand name in `brand_name_snapshot[canon_version]` | A bare brand name is not an occasion or a behaviour — it belongs in the stable `visible_brands` field (§19A), and admitting it here would let the taxonomy restate the subject back to the user as a finding. The check reads a **snapshot pinned to the generation**, not the live `brands` table: reading live data would put a continuously-changing input inside I1, so the same rebuild could promote a label today and refuse it tomorrow with no recorded cause. Still no brand name in code or config. Degenerate forms become `status='blocked'`, never deleted. |
| Not currently flagged | `'suspected_prompt_echo' / 'single_source_artefact' / 'stale_extractor_artefact' ∉ flags` and `status <> 'blocked'` | §4.5. `dormant` and `uncalibrated_bucket` do **not** block promotion — the first is a finding (§22's "declining"), the second is a caveat. |

**Sensitivity, stated plainly.** `S_min = 5` roughly doubles promoted count and admits
noticeably more artefacts. `S_min = 12` cuts promotions ~40% and systematically removes
*emerging* concepts — which are low-support by definition, and which §22 explicitly asks for.
That tension is resolved by a tier, not by moving the number:

### 4.2 The weak-signal tier (resolves §4.1's tension with §22)

```
status = 'emerging_candidate'  when
    support_count >= 4  AND  distinct_content >= 3  AND  distinct_creators >= 2
    AND NOT blocked AND no artefact flag
    AND (first_pub_date >= run_as_of_date - 21  OR  first_seen_run = current run)
```

`run_as_of_date = research_runs.started_at::date`, **never `current_date`**. The first draft used
wall-clock time here and in §6.4, which silently violated I1: the same observations, the same
config and the same seed produced different statuses on different days, so a rebuild could not
reproduce the taxonomy it was rebuilding and nothing could be audited against a stored result.

`emerging_candidate` labels are **surfaced** (they are exactly what §22's "weak signal" class
and §31.4's trends view need) but are **not taxonomy entries**, are never used as filter
dimensions by default, and the API returns them only with an explicit `status` filter or
`include_weak=true`, always carrying `support_count` and `distinct_creators` so a reader sees
the sample size. Brief §28: "if the evidence is weak, the system must say so."

### 4.3 Evidence strength (renamed, and the model's self-assessment removed)

```
evidence_strength = clamp(0, 1,
       0.40 * min(1, ln(1 + support) / ln(1 + 25))     # 25 = S_target, saturation point
     + 0.25 * min(1, distinct_creators / 8)
     + 0.15 * min(1, distinct_platforms / 3)
     + 0.20 * min(1, distinct_strategies / 3)
     - 0.30 * max(0, (top_creator_share - 0.5) / 0.5)
     - 0.20 * max(0, (top_query_share   - 0.5) / 0.5))
```

Three changes from the first draft's `confidence`, each for a stated reason.

1. **The model's own confidence is out.** It carried weight 0.25 in a number shown to a user.
   `03-multimodal-analysis` documents the same field as *"Stored for audit; the system does not
   display it"*, and brief §33 lists "arbitrary AI scores" among the things to avoid. A
   self-reported float from the component being evaluated is the least verifiable input
   available, and mixing it into the displayed figure made the figure partly unfalsifiable.
   `mean(model_confidence)` is still computed and still returned — in
   `LabelDetail.confidence_breakdown`, labelled as the model's self-assessment, next to the
   evidence terms, never summed with them.
2. **It is clamped.** The old expression could evaluate negative (support 1, one creator, one
   platform, `model_confidence` 0 → ≈ −0.13), which violates the column's own
   `CHECK (BETWEEN 0 AND 1)` and would have raised at insert. It could also evaluate NULL
   whenever `model_confidence` was NULL, which it is whenever the extractor omits it.
3. **It is renamed.** "Confidence" invites reading as a probability that the label is correct.
   It is not one — it is a bounded monotone summary of how much independent evidence exists.
   `evidence_strength` says that, and §6.2 returns the terms individually so the composite is
   never the only thing on screen. The remaining weights are a declared editorial judgement,
   not a measurement, and the API labels them as such.

`S_target = 25` is where additional support stops being informative — the difference between 25
and 250 items matters for trend analysis, not for "is this real". The concentration penalties
are linear from 0 at 50% share to their floor at 100%: a label that is one creator's verbal tic,
or one query's echo, cannot score above 0.70 however often it appears.

### 4.4 Definition writing on promotion

On promotion, write a definition **grounded only in that label's own observed evidence** — the
one place where we must be most careful not to import outside knowledge (§34).

```
You are writing a dictionary entry for a label that emerged from analysis of a content
dataset. Below are the ONLY things known about it: the label, the glosses analysts wrote,
and verbatim evidence quotes from the content.

Write 1-2 sentences defining what this label refers to AS USED IN THIS DATASET.

Constraints:
- Use ONLY the evidence below. Add no world knowledge, no cultural background, no
  explanation of why it might be popular, no demographic inference.
- If the evidence is internally inconsistent, say so in the definition.
- Do not mention any brand unless a brand appears in the evidence.
- Do not speculate about marketing implications.

LABEL: {display_form}   (facet: {facet}; language bucket: {lang})
ANALYST GLOSSES: {up to 8 distinct glosses}
EVIDENCE QUOTES: {up to 12 verbatim quotes}
ALSO RECORDED AS: {up to 10 alias surface forms}

- Do NOT state how many items, creators, videos or listings anything appears in,
  and do not use words like "often", "frequently", "most" or "rare". Counts are
  computed and rendered by the system next to your definition.
```

Sample sizes are deliberately **not** in the prompt. The first draft passed `{support}` and
`{creators}` into it, which invites the model to restate them inside prose that is then shown as
a definition — a number the system owns, laundered through a model and into a sentence nobody
re-checks. Every count a user sees is rendered by code from §1.5.

Batched 10 labels/call, `effort="low"`. ~400 promotions → ~40 calls → ~240k in ($1.20) +
~80k out ($2.00) ≈ **$3.20 per run** (first run only; definitions persist and are rewritten
only when support grows 3× or the display form changes).

### 4.5 Demotion, merge-away and artefact detection

Demotion **never deletes**. Only `status` moves, `status_reason` is set, and for taxonomy
entries `retired_at`/`retired_reason` (and `superseded_by` on a merge) are written.

Everything in this table except **blocked** sets a **flag**, not a status. The first draft moved
`status` instead, which quietly contradicted two of its own promises: a `dormant` label was no
longer `canonical`, although the row below insists dormancy "is not demoted in meaning", and a
flagged label disappeared from every default read (`status=('canonical',)`) although the table
says it "stays visible". Flags are a `label_flag[]`; status keeps meaning position in the
lifecycle.

| Trigger | Condition | Effect |
|---|---|---|
| **Merge-away** | a later pass finds `cos >= τ_merge` (+ gate) to a canonical with higher support | `status = 'alias'`; taxonomy entry gets `retired_reason='merged'`, `superseded_by=<leader>`; the absorbed label's own aliases are re-pointed in the same transaction (no chains, §1.4); counts recomputed, never summed blindly |
| **Single-source artefact** | after promotion, `top_creator_share >= 0.70` **and** `distinct_creators <= 3`; or `top_query_share >= 0.80` | `+ single_source_artefact` — stays `canonical` and visible, excluded from trend claims |
| **Suspected prompt echo** | `suggested_share >= 0.90` **and** `holdout_support = 0` **and** `support_count < 20` | `+ suspected_prompt_echo` — visible, labelled in the UI as possibly an artefact of the suggestion loop, excluded from trend claims. `suggested_share` is **unit-weighted** (§1.5); observation weighting let one verbose item push a label over a 0.90 threshold on its own. *Why support < 20:* above that, absence from a ~225-unit holdout arm is weak evidence — under the arm split a label on 20 units has ≈ 0.85^20 ≈ 3.9% chance of missing the holdout entirely, so below 20 the absence is suggestive and above it is not. |
| **Stale extractor artefact** | `>= 0.95` of observations come from a single superseded `extractor_version`, and the label does not reappear in ≥ 200 units analysed by the current version | `+ stale_extractor_artefact` |
| **Dormant** | no new observation across ≥ 2 completed runs | `+ dormant` — **not** a demotion in meaning: §22 wants "declining" as a class, so dormancy is a finding, the label stays `canonical`, and the trajectory API keeps serving its series |
| **Human block** | `label_decisions.action='block'` | `status = 'blocked'` |

**Flag clearing** is allowed: a flag is removed once its condition no longer holds, the label
is re-evaluated against the full gate, and the flag history stays in `status_reason` +
`promotion_metrics` so the oscillation is visible. Because flags no longer displace `status`,
clearing a flag is not a re-promotion and does not reset `promoted_at` — a label's promotion
date should not move because a creator concentration fell below 0.70.

### 4.6 Facet reassignment

A label emitted under `unexpected` (or the wrong facet) can be moved by a `facet_reassign`
decision. Because `UNIQUE (canon_version, facet, lang_bucket, normalised_form)` makes facet part
of identity, reassignment creates/locates the row in the target facet and records a `must_link`,
rather than updating `facet` in place. Observations are untouched; the resolution layer handles
it. A periodic job proposes candidates: `unexpected` labels whose embedding is within `τ_review`
of a non-`unexpected` canonical, flagged `cross_facet_duplicate`. Note the measurement this
depends on: cross-facet comparison uses the same embedding space as within-facet comparison, but
the `[facet]` prefix is part of the embed key (§2.4), so cross-facet cosines are systematically
depressed relative to within-facet ones and `τ_review` is **not** the right threshold for them.
The proposal job therefore recomputes those similarities on a facet-stripped key and reports its
own rate against the §9.6 10% trigger; it is a monitor, not a gate. Proposals are **never** auto-applied — `unexpected` is where
genuine novelty lives, and auto-filing it into an existing facet is exactly how a discovery
system quietly stops discovering.

### 4.7 Human decisions as hard constraints

Rebuilds apply `label_decisions` **last**, as constraints on clustering:
- `must_link(A,B)` → forced merge regardless of similarity (constrained clustering must-link)
- `cannot_link(A,B)` → the pair is removed from candidate generation at every similarity,
  including above `τ_strong`
- `force_promote` / `force_demote` → override the gate, with `promotion_caveats` recording
  `'human_override'`
- `set_display_form` → pins the representative, disabling the §2.9 election for that cluster

This keeps I1 intact: the table is still a pure function of (observations, config, decisions,
seed).

---

## 5. Multilingual handling

### 5.1 The decision: keep distinct, link explicitly. Do **not** merge.

A label in one language and its near-equivalent in another become **two canonical labels** in two
`lang_bucket`s, joined by a typed `translation_equivalent` link, optionally rolled up into a
`concept_group` **only on explicit request** and **never without a per-language breakdown**.
(The argument below is written with two languages for readability; nothing in it, and nothing in
the implementation, names a particular language — the buckets come from
`research_runs.language_codes`, per §1.4 and §2.3.)

Three reasons, in order of force:

1. **Merging destroys the product's own deliverable.** §18 requires analysis of "language;
   code-switching; slang"; §31.2 lists "language/slang" as a landscape dimension. If two
   language forms collapse into one row, the system can no longer answer *which audience,
   register or platform uses which form*, nor *whether one form is growing while the other
   declines* — and that last question is a genuine creative and competitive insight (it tells a
   marketer which language to write a brief in). The merge deletes precisely the finding.
2. **"Equivalence" across languages is lossy in the direction that matters.** The payload of an
   informal term is register, affect and in-group marking. A gloss can carry the referent
   across; it cannot carry the register. Two forms with the same referent and different
   register are different observations about speakers.
3. **Merging corrupts the counts.** Using one form rather than another is a *behaviour of
   speakers*. Adding the supports of two language forms produces a number that answers no
   question anyone asked, while hiding the two numbers that do.

### 5.2 Mechanics: `lang_bucket` is the partition key

- Canonicalisation blocks are **(facet, lang_bucket)**. A form in one bucket therefore *cannot*
  auto-merge into another bucket at any similarity — it is never a candidate.
- Within a `mixed` bucket, merging proceeds normally: two code-switched variants of each other
  are the same form and should unify.
- A `mixed` bucket does **not** merge into either of its monolingual components. The code-switch
  *is* the observation (§18 names code-switching explicitly), so a mixed form is a distinct
  linguistic object, not a misspelling of a monolingual one. It gets `code_switch_variant` links
  to its component-language counterparts instead.
- `und` and `other` buckets never auto-link and never roll up.
- Buckets with `rich_morphology` take the lower trigram gate (0.50 vs 0.60); buckets with
  `embedding_calibrated = false` take the §2.7 threshold adjustment (higher `τ_merge`, wider
  review band) and carry the `uncalibrated_bucket` flag into the API.

### 5.3 Cross-language link detection: the gloss is the pivot

Because the extractor writes the **label in the content's language and the gloss in English**
(§3.4), cross-language equivalence can be detected on the glosses alone:

```
For each canonical A, same facet, exact top-k (k=10) over canonicals in OTHER buckets:
    s_gloss = cosine(embed(A.gloss_en), embed(B.gloss_en))     # gloss-only embedding
    if s_gloss >= 0.88 and mutual (B is also in A's top-k by this measure):
        -> candidate; confirm by batched LLM adjudication in BOTH orders (§2.8)
```

Top-k rather than all-pairs (all-pairs is ~10^6 comparisons at 1,600 canonicals and buys
nothing above the threshold), and **mutual** top-k because a one-sided nearest neighbour is the
classic way a hub gloss — a vague one like "a kind of social situation" — attracts half a facet.
Labels with no gloss, or a gloss flagged `gloss_not_en`, are excluded entirely (§7.1).

**Why 0.88, higher than most `τ_merge`:** a false translation link is *worse* than a false
split, because concept-group roll-ups consume these links and a wrong link silently fuses two
different concepts' numbers across languages. The asymmetric cost justifies the asymmetric
threshold. Every candidate is LLM-confirmed; the adjudicator's `translation_equivalent` verdict
is required, and `same_concept` is explicitly **not** accepted as a cross-language merge (the
prompt in §2.8 instructs exactly this).

A second gloss-only embedding is needed per label (the main key is `[facet] form — gloss`).
~8,000 extra embeddings × ~18 tokens = 144k tokens ≈ **$0.019 per run**. Cross-language
adjudication: ~150–400 candidate pairs → ~10 calls ≈ **$0.60 per run**.

### 5.4 Concept groups and roll-up semantics

`concept_groups` = connected components over **`translation_equivalent` links only** (not
`near_equivalent`, not `code_switch_variant`). Group support is `COUNT(DISTINCT analysis_unit)`
across members — never a sum, since one bilingual item can be labelled in both languages.

**Connected components chain, and §2.5 rejected them for exactly that reason.** The first draft
presented CC here as conservative. It is not: if `A_en ~ B_other` and `B_other ~ C_en` are both
accepted links, then `A_en` and `C_en` land in one group even though no link between them was
ever asserted — fusing two distinct same-language concepts' numbers behind a roll-up that the
intelligence layer consumes. `cgm_label_unique` then makes it unrecoverable without a rebuild.
Three guards, all cheap:

1. **At most one label per `lang_bucket` per group.** A component containing two labels from the
   same bucket is, by construction, a chain: two distinct canonicals in one bucket were not
   merged by §2.5, so they are not the same concept, so they cannot both be the same concept as
   a third. Such a component is **not** emitted as a group; its links are marked
   `needs_review` and surfaced in the curation queue.
2. **Component size cap** = number of buckets in use. Anything larger is rejected by (1) anyway;
   the cap makes the failure explicit in the job rather than implicit in a query.
3. **Mutual-link requirement**: a `translation_equivalent` link is only group-forming if both
   adjudication orders agreed (§2.8). Single-order links still exist and are shown on the label
   detail page; they just do not fuse numbers.

A rejected component is a *finding*, not an error state: three forms that each look like
translations of each other but cannot all be the same concept is precisely the sort of thing a
language-focused deliverable should put in front of a human.

Read API contract (enforced, not advisory):

- Default `roll_up='language'` → one row per label, language visible.
- `roll_up='concept'` → one row per concept group, and the response **must** include
  `lang_breakdown` for every row. A concept-rolled response without a language breakdown is a
  schema violation. There is no mode that silently hides the language signal.

### 5.5 Open question, stated rather than papered over

Short-phrase embedding quality in `text-embedding-3-large` is **unmeasured for every bucket**,
and for morphologically rich languages there may be no usable lemmatiser (prefix, suffix *and*
infix affixation plus reduplication defeat an English-shaped stemmer, which is why §2.2 step 9
does no stemming there). Until §2.10 has been run **separately for each bucket**, every bucket
has `embedding_calibrated = false`, runs with the widened review band and a higher share of
adjudicated decisions — more cost, less silent error — and every label in it carries the
`uncalibrated_bucket` flag through to the API. If calibration shows a bucket's precision cannot
reach a 0.90 lower bound at any threshold ≤ 0.95, the fallback is **lexical-gate-mandatory for
all merges in that bucket** (treat it like `slang`), accepting visible duplicates over invisible
destruction. This is a real risk, not a formality, and on run 1 it applies to everything.

---

## 6. Read / query API

### 6.1 Rules every response obeys

Derived from §24 ("always scope conclusions to the dataset") and §28 ("show sample sizes"):

1. **No bare counts.** Every vocabulary row returns `support_count`, `distinct_content`,
   `distinct_creators`, `corpus_denominator` (analysed units in scope) and
   `share = support_count / corpus_denominator`, with `denominator_definition` naming exactly
   what was counted. Share, not count, is the comparable figure *within one scope*.
2. **Share is a sample share, and the API says so.** Every response carries
   `sampling: {frame: "non_probability", caveats: [...]}`. This is the honesty rule the first
   draft was missing, and it matters more than any threshold in this document. The corpus is not
   a random sample of anything: items enter because a query we wrote found them, in an order the
   platform's ranking chose, and older items are present only if they still rank — three
   distinct biases (query selection, ranking, survivorship) stacked on top of each other.
   `share` is therefore "the fraction of **the units we analysed** that evidenced this label",
   never an estimate of prevalence in the category, and the field is named and documented that
   way end to end. Brief §24's "always scope conclusions to the dataset" is this rule.
3. **Shares are not comparable across scopes with different sampling frames.** Two runs, or two
   date windows within a run, generally have different query mixes, so a share difference can be
   produced entirely by what we searched for. Every response carries
   `frame: {strategy_mix: {...}, platform_mix: {...}}`, and a comparison endpoint returns
   `frame_comparable: false` with the reason when the strategy mixes differ by more than 0.10
   total variation distance. The UI must not draw a cross-scope delta that is marked
   not comparable.
4. **Scope is always echoed**: `run_id`, `canon_version`, publication-date range, platforms,
   arm filter, `n`.
5. **Status and flags are always present.** A caller cannot accidentally mix `canonical` with
   `emerging_candidate`, or miss that a canonical row is flagged `suspected_prompt_echo` — weak
   tiers require an explicit opt-in and flags ride on every row.
6. **Every count carries its `canon_version`.** Support is a function of the merge thresholds,
   so a figure without its generation is not a figure. Cross-version comparisons are refused.
7. **Uncalibrated buckets are declared.** Until §2.10 runs, every row from an uncalibrated
   bucket carries `uncalibrated_bucket` and the facet summary says the vocabulary is
   provisional.
8. **Trajectory claims carry their own sufficiency verdict** (§6.4); `insufficient_data` is a
   first-class value, never silently rendered as "stable".
9. **Concept roll-ups always carry `lang_breakdown`** (§5.4).

### 6.2 Service layer

```python
# aypo/analysis/labels/service.py
class SamplingFrame(BaseModel):
    frame: Literal["non_probability"] = "non_probability"
    strategy_mix: dict[str, float]               # share of analysed units per query strategy
    platform_mix: dict[str, float]
    caveats: list[str]                           # 'ranking_bias', 'survivorship_window', ...

class Scope(BaseModel):
    run_id: UUID | None = None
    canon_version: int | None = None             # defaults to the run's active generation
    brand_id: UUID | None = None
    platforms: list[str] | None = None
    pub_date_from: date | None = None
    pub_date_to: date | None = None
    arm: Literal["all","suggested","holdout"] = "all"
    corpus_denominator: int                      # filled by the service, never by the caller
    sampling: SamplingFrame                      # filled by the service; §6.1 rules 2-3

class VocabularyRow(BaseModel):
    label_id: UUID
    canon_version: int                           # every count is tau-dependent (§6.1 rule 6)
    facet: str
    display_form: str
    normalised_form: str
    gloss_en: str | None
    definition: str | None
    lang_bucket: str                             # a lang_buckets key, not an enum
    status: LabelStatus
    flags: list[LabelFlag]                       # orthogonal to status (§4.5)
    promotion_caveats: list[str]
    support_count: int
    distinct_content: int
    distinct_commerce: int
    distinct_creators: int
    distinct_platforms: int
    corpus_denominator: int
    denominator_definition: str                  # exactly what the denominator counted
    share: float                                 # share of ANALYSED units, never prevalence
    evidence_strength: float | None              # §4.3; model self-report excluded
    first_pub_date: date | None
    last_pub_date: date | None
    alias_count: int
    trajectory: Trajectory                       # see §6.4
    sparkline: list[tuple[date, int]]            # weekly distinct_content
    concept_group_id: UUID | None
    lang_breakdown: dict[str, int] | None        # populated when roll_up='concept'

class FacetSummary(BaseModel):
    facet: str
    display_name: str
    question: str
    accepts_emissions: bool                      # false => shown as empty, never hidden (§1.1)
    canonical_count: int
    emerging_count: int
    alias_count: int
    flagged_count: int
    observation_count: int
    coverage: float                              # share of analysed units with >=1 label here
    bias_verdict: Literal["ok","watch","anchored"]
    vocabulary_provisional: bool                 # any uncalibrated bucket present (§6.1 rule 7)
    sources_available: list[str]                 # which collectors actually ran (§7.1)

class LabelDetail(BaseModel):
    row: VocabularyRow
    aliases: list[AliasRow]                      # surface_form, support, merge method, similarity
    raw_surface_forms: list[tuple[str, int]]     # verbatim model output + count (§1.2)
    links: list[LinkRow]
    merge_history: list[MergeRow]
    evidence_breakdown: dict[str, float]         # each §4.3 term, shown separately
    model_self_confidence: float | None          # mean model_confidence; shown LABELLED as the
                                                 # model's own, never folded into the composite
    promotion_metrics: dict[str, Any] | None
    evidence_preview: list[EvidenceRef]          # content/commerce ids + quote + timecode
    arm_split: dict[str, int]                    # {"suggested": n, "holdout": n}

class TaxonomyService:
    async def list_facets(self, scope: Scope) -> list[FacetSummary]: ...
    async def facet_vocabulary(
        self, facet: str, scope: Scope, *,
        status: Sequence[LabelStatus] = ("canonical",),
        lang: Sequence[LangBucket] | None = None,
        roll_up: Literal["language","concept"] = "language",
        min_support: int = 1,
        sort: Literal["support","share","confidence","recency","momentum","alpha"] = "support",
        limit: int = 100, offset: int = 0,
    ) -> Page[VocabularyRow]: ...
    async def label_detail(self, label_id: UUID, scope: Scope) -> LabelDetail: ...
    async def label_evidence(
        self, label_id: UUID, scope: Scope, *, limit: int = 50, offset: int = 0,
        include_aliases: bool = True,
    ) -> Page[EvidenceRef]: ...
    async def label_trajectory(
        self, label_id: UUID, scope: Scope, *,
        granularity: Literal["day","week"] = "week",
        metric: Literal["distinct_content","observations","share"] = "distinct_content",
    ) -> TrajectorySeries: ...
    async def semantic_search(
        self, query: str, scope: Scope, *, facet: str | None = None,
        lang: Sequence[LangBucket] | None = None, limit: int = 25,
    ) -> list[VocabularyRow]: ...
    async def concept_group(self, group_id: UUID, scope: Scope) -> ConceptGroupDetail: ...
    async def bias_report(self, run_id: UUID) -> list[FacetBiasReport]: ...
    async def pending_merges(self, facet: str | None, limit: int = 100) -> Page[CandidatePair]: ...
    async def record_decision(self, decision: LabelDecisionIn, actor: str) -> UUID: ...
```

### 6.3 HTTP routes (`aypo/api/routes/taxonomy.py`)

| Method & path | Purpose | Consumer |
|---|---|---|
| `GET /api/taxonomy/facets` | facet summaries + coverage + bias verdict | §31.2 landing |
| `GET /api/taxonomy/facets/{facet}` | paged vocabulary (filters per §6.2) | §31.2 panels, filter builders |
| `GET /api/taxonomy/labels/{label_id}` | full detail incl. aliases, raw forms, merge history | label drill-down |
| `GET /api/taxonomy/labels/{label_id}/evidence` | paged evidence refs → content/commerce | §27 drill-down |
| `GET /api/taxonomy/labels/{label_id}/trajectory` | time series + sufficiency verdict | §31.4 trends |
| `POST /api/taxonomy/search` | semantic search over labels (body: `{query, facet?, lang?}`) | §31.5 search, Ask-the-Data |
| `GET /api/taxonomy/concepts/{group_id}` | cross-language roll-up + lang breakdown | §31.2 language view |
| `GET /api/taxonomy/runs/{run_id}/bias-report` | §3.5 metrics | §32 research-run view |
| `GET /api/taxonomy/pending-merges` | review queue | internal curation UI |
| `POST /api/taxonomy/labels/{label_id}/decisions` | human merge/split/block/promote | internal curation UI |
| `GET /api/taxonomy/export?format=csv\|json` | full vocabulary dump with provenance | analyst export |

Intelligence-layer consumers (clustering §21, trends §22, competitive §24, Ask-the-Data §31.7)
use `TaxonomyService` **in-process**, not HTTP. They are additionally given one read helper
that does not belong in the HTTP surface:

```python
async def resolved_label_ids(self, label_ids: Sequence[UUID]) -> dict[UUID, UUID]: ...
async def labels_for_content(
    self, content_item_ids: Sequence[UUID], *, facets: Sequence[str] | None = None,
    status: Sequence[LabelStatus] = ("canonical","emerging_candidate"),
) -> dict[UUID, list[UUID]]: ...   # the feature matrix other subsystems build on
```

### 6.4 Trajectory: the series is mine, the verdict is provisional

Served from `label_support_daily`, resolved through aliases, bucketed by **content publication
date**. Materialised for speed:

```sql
-- label_support_daily is ALREADY keyed on the resolved label and already holds
-- alias-resolved distinct counts (§1.4), so the weekly roll-up is a plain re-bucket.
-- The first draft keyed the daily table on the LEAF label and then SUM()ed
-- distinct_content across aliases here, which double-counts any item that emitted
-- two aliases of the same concept -- the exact trap §1.3 was written to prevent,
-- reintroduced in the one place the number is plotted. distinct_content must be
-- recomputed with COUNT(DISTINCT ...) over observations, never summed over leaves.
CREATE MATERIALIZED VIEW mv_label_support_weekly AS
SELECT d.resolved_label_id,
       d.canon_version,
       date_trunc('week', d.pub_date)::date     AS week,
       SUM(d.observations)                      AS observations,
       SUM(d.distinct_content)                  AS distinct_content,
       SUM(d.distinct_creators)                 AS creator_days
FROM   label_support_daily d
GROUP  BY 1, 2, 3;
CREATE UNIQUE INDEX ON mv_label_support_weekly (resolved_label_id, canon_version, week);
```

(Summing `distinct_content` across *days* is safe — a content item has exactly one publication
date, so it appears in exactly one day bucket. Summing across *aliases* is not, which is why
the daily table is keyed on the resolved label.)

Provisional classification (§22's authoritative classifier is the trend subsystem's, not mine;
this exists so the vocabulary list can show an honest sparkline badge):

```
Let W1 = last 2 complete weeks before run_as_of_date, W0 = the 2 weeks before that.
# Preconditions. Any failure -> 'insufficient_data'; none of them is negotiable.
if 'no_publication_dates' in promotion_caveats:            -> 'insufficient_data'
if support_count < S_min or count(W0) < 3 or count(W1) < 3 or total < 10:
                                                           -> 'insufficient_data'
if frame_unstable(W0, W1):                                 -> 'insufficient_data'
      # TV distance between the two windows' strategy mixes > 0.10, or their
      # platform mixes differ, or either window's denominator < 30 analysed units.

r = rate(W1) / rate(W0)          # rates normalised by analysed units published per window
# Conditional binomial (exact Poisson two-sample) test WITH the exposure ratio,
# then an overdispersion correction: counts cluster by creator and by query, so a
# Poisson variance assumption is optimistic. phi = max(1, Pearson dispersion of the
# per-creator counts in the two windows); the test statistic is divided by phi.
p_raw = binom_test(count(W1), count(W0)+count(W1), p = d1/(d0+d1)) adjusted by phi
q     = benjamini_hochberg(p_raw, family = all labels classified in this run)

if r >= 1.50 and q <= 0.10: 'growing'
elif r <= 0.67 and q <= 0.10: 'declining'
elif first_pub_date >= run_as_of_date - 21 and support_count >= 4: 'emerging'
elif support_count < S_min: 'weak_signal'
else: 'stable'
```

**Why 1.50 / 0.67:** at the counts involved (often 5–40 per window) a 20% change is
indistinguishable from sampling noise, so a ±50% effect size is the smallest honest one.

**Why FDR and not a raw p < 0.10.** The first draft applied `p < 0.10` per label with no
multiplicity control across 250–600 promoted labels. On a corpus where nothing at all was
changing, that produces on the order of **25–60 spurious "growing"/"declining" badges per run** —
badges that appear next to evidence links, in the section brief §31.4 calls Trends &
Opportunities. Benjamini–Hochberg at `q <= 0.10` over the run's whole tested family keeps the
expected proportion of false badges among the badges shown at 10%, and the API returns both
`p_raw` and `q` so the correction is visible rather than implied.

**Why overdispersion.** Items are not independent draws: one creator posts five videos, one
query returns twenty similar ones. Poisson variance therefore understates the true variance and
inflates significance in exactly the direction that creates false trends. Dividing by a Pearson
dispersion estimated from per-creator counts is the cheapest honest correction; where
`distinct_creators < 4` in a window, no trend claim is made at all.

**What normalisation does *not* fix, and must be said in the UI.** Normalising by analysed items
published per window removes crawl *volume* recency bias. It does not remove **survivorship**:
a video published eight weeks ago is in the corpus only if it still ranked when we searched,
while a video published last week enters on recency. The older window is therefore a filtered,
survivor-biased sample and the newer one is not, which biases ratios toward "growing" for
anything fashionable and toward "declining" for anything whose older instances have since been
buried. The `frame_unstable` precondition catches the crudest version of this; the rest is a
**standing caveat attached to every trajectory response** (`caveats: ['survivorship_window']`)
and rendered next to the badge. Authoritative trend classification is the trend subsystem's
(§22); this badge exists so a vocabulary list can show an honest sparkline, and "honest" here
means it declines to classify far more often than the first draft's version did.

**Boundary:** exactly 3 items in a window passes; 2 yields `insufficient_data`.
`insufficient_data` is rendered as "not enough data", never as "stable".

---

## 7. Operations, degradation and cost

### 7.1 Degradation matrix (§7: keep running when something is unavailable)

| Failure | Behaviour | Marker |
|---|---|---|
| OpenAI embeddings down | switch to Jina; never mix providers in one comparison | `embed_provider`; `needs_recanon=true` on provider change |
| All embedding providers down | **lexical-only canonicalisation**: S1 exact-match merges + trigram `>= 0.72` (raised from 0.60 because there is no semantic check behind it); labels stay `candidate`, nothing is promoted on lexical evidence alone | `canon_method='lexical_only'`, `needs_recanon=true` |
| LLM adjudication unavailable | review-band pairs stay `pending_merge`; **labels remain distinct** (never auto-merge without evidence); UI shows "N possible duplicates pending" | `status='pending_merge'` |
| **`APIFY_TOKEN` absent** | Not "one platform missing". Brief §5/§11 route TikTok, Instagram, Facebook, Shopee and Lazada through Apify, so an absent token removes *most* sources and leaves YouTube (`YOUTUBE_API_KEY`) plus web search. Behaviour: the platform gate degrades as below, **and** the facet summaries declare `sources_available` so a reader is never shown a "category" vocabulary built from one platform without knowing it. | `promotion_caveats=['single_platform']`, `FacetSummary.sources_available` |
| **Both `APIFY_TOKEN` and `YOUTUBE_API_KEY` absent** | Web/search discovery only. Publication dates are frequently missing there, so the temporal gate also degrades, and *nothing is promoted to `taxonomy_entries`* — the vocabulary is served at `emerging_candidate` and below, explicitly as a provisional reading of a thin corpus. A one-source taxonomy presented as a taxonomy is the failure brief §28 exists to prevent. | `promotion_caveats=['web_only']`; promotion suppressed |
| One platform missing | promotion substitutes `distinct_creators >= 7` **and** `distinct_queries >= 3` (§4.1) | `promotion_caveats=['single_platform']` |
| Corpus spans < 14 days | promotion substitutes the narrow-window temporal gate | `promotion_caveats=['narrow_window']` |
| Publication dates missing for ≥ 50% of a label's units | temporal gate skipped; label barred from any trajectory claim | `promotion_caveats=['no_publication_dates']` |
| §2.10 calibration not yet run for a bucket | widened review band, more adjudication, every label flagged | `uncalibrated_bucket` |
| Extractor returns no gloss | label still ingested; `embed_key` falls back to `[facet] form`; label cannot enter a cross-language link (gloss is the pivot) and is excluded from §5.3 | `norm_steps` records `no_gloss` |
| Gloss in the wrong language | detected when `py3langid(gloss) != 'en'` with p ≥ 0.8; the gloss is kept but flagged and excluded from the gloss-only embedding | `norm_steps` records `gloss_not_en` |

Nothing in this matrix blocks a research run. Worst case the vocabulary is noisier and
promotion is more conservative, which is the correct direction to fail in.

### 7.2 Jobs (Postgres-backed queue; no Redis, no Celery)

| Job | Trigger | Notes |
|---|---|---|
| `ingest_label_proposals` | synchronous, called by analysis | S1–S3 + `labels` upsert + observation insert; must be fast and non-blocking |
| `canonicalise_labels(facet)` | every **200** new observations in that facet; at run end; on config change | `pg_advisory_xact_lock(hashtext('canon:' || facet))` prevents races; debounced to at most one pass per facet per 60s |
| `adjudicate_pending_merges` | after each canonicalisation pass, if `pending_merge` count ≥ 20 or at run end | batch 40/call; cache-first |
| `detect_cross_language_links` | at run end, and on every 500 new canonicals | §5.3 |
| `evaluate_promotions` | after each canonicalisation pass | applies §4.1 gate + §4.5 demotions |
| `write_taxonomy_definitions` | after promotions, batched 10/call | §4.4 |
| `rollup_label_support` | after the run-end rebuild, and after any `canon_version` activation | full recompute of `label_support_daily` per (resolved label, canon_version) from `observation_label_map`. **No incremental path** — increments cannot be made correct across alias changes. The first draft had no job writing this table at all, so the trajectory endpoint had no data source. |
| `reconcile_provisional` | at run end, before promotion | re-derives the whole generation deterministically at the final `as_of_seq`, replacing the provisional in-run assignment, and logs the provisional→final delta (§2.5) |
| `compute_bias_report` | at run end, and on demand | §3.5; must run **after** `reconcile_provisional`, since every metric is computed on the final canonical distribution |
| `refresh_vocab_views` | after `rollup_label_support` | `REFRESH MATERIALIZED VIEW CONCURRENTLY` (needs the unique index, and cannot run inside a transaction block) |

**Why 200 observations:** at ~12 labels/video that is ~17 videos, so the canonical set is at
most ~17 videos behind whenever a snapshot is taken. Note what this does and does not bound: a
*snapshot* is built once per 50-analysis epoch and is therefore up to 50 analyses old by
construction (that staleness is the price of prompt-cache stability, §3.3). The 200-observation
cadence bounds only the freshness of the canonical set *at the moment the snapshot is built*.
The first draft conflated the two. Lower values add pass overhead without changing what the
extractor sees.

Incremental passes consider only labels with `needs_recanon = true` (new, or support changed
since the last pass) compared against existing leaders. A `canon_version` bump forces a full
rebuild: all alias edges get `reversed_at`, `needs_recanon` is set everywhere, and the pass
re-derives from the observation log. Free in API terms (§7.3).

### 7.3 Cost at scale (per run: ~1,500 videos + ~2,500 commerce records)

Model prices used: `claude-opus-5` at **$5.00 / $25.00 per Mtok** (cache write 1.25× = $6.25,
cache read 0.1× = $0.50); `text-embedding-3-large` at **$0.13/Mtok**. Both verified current.

| Item | Tokens | Cost |
|---|---|---|
| Label-key embeddings (~8,000 keys + ~1,500 re-embeds, ~35 tok) | ~330k | **$0.043** |
| Gloss-only embeddings for cross-language (~8,000 × 18 tok) | ~144k | **$0.019** |
| Commerce-path embeddings (~3,000 × 35 tok) | ~105k | **$0.014** |
| Merge adjudication, **both orders** (~600–1,200 pairs, 40/call, effort=low) | ~160k in / ~110k out | **~$3.55** |
| ×1.75 band widening while buckets are uncalibrated (§2.7) | | **+~$2.65** (run 1 only) |
| Cross-language adjudication, both orders (~150–400 pairs) | ~56k in / ~36k out | **~$1.20** |
| Taxonomy definitions (~400 labels, 10/call, effort=low) — **first run only**; rewritten only on 3× support growth or display-form change | ~240k in / ~80k out | **~$3.20** |
| Suggestion-block overhead on the extractor, with epoch-stable caching **and two breakpoints** (§3.3) | 30 epoch writes + ~1,470 reads × 600 tok | **~$0.55** (vs **$4.50** uncached, or ~$0.75 extra if the second breakpoint is missed) |
| **Total, run 1 (uncalibrated, definitions written)** | | **≈ $11.30** |
| **Total, steady state (calibrated buckets, definitions cached)** | | **≈ $5.40** |

The first draft's **$6.20** understated this in three places: adjudication was single-order,
the uncalibrated-bucket band widening was not costed, and cross-language adjudication was
single-order. It also folded a first-run-only definition cost into a per-run total. The
corrected figures are still small — the point of the table is that nothing here is close to the
multimodal analysis budget, and that remains true.

Re-runs and threshold recalibration: embeddings cached by `embed_key_sha`, pair verdicts cached
by `pair_key` → **≈ $0** for a full rebuild. This is what makes §2.10's calibration loop and
I1's "any threshold can be changed" promise economically real rather than aspirational.

Two honest qualifications. A rebuild is free in *API* terms only: it still recomputes
`observation_label_map`, every counter and every roll-up, which is minutes of database work, not
zero. And "free" holds only while the cache keys are stable — a change to the embedding model, to
`norm_pipeline_version` (which changes `embed_key`), or to the adjudication `prompt_version`
(which is part of `pair_key`) invalidates the cache and the rebuild costs full price. Lowering
a τ is free; changing how forms are normalised or how pairs are asked is not.

---

## 8. Testing without seeding findings

Testing a canonicaliser needs ground truth about which forms are the same concept — and
writing that ground truth is exactly how cultural assumptions get smuggled into a codebase.
So fixtures are **programmatically generated from placeholder concepts**:

1. Define `N` synthetic concepts `C1..CN` with synthetic multi-token names drawn from a
   nonsense-but-pronounceable token pool (`theme-x`, `cluster-y`, `brand-a`, `zarep molin`).
2. For each concept generate `k` surface variants by applying *programmatic* perturbations
   whose correct outcome is known by construction: case changes, punctuation, emoji suffix,
   pluralisation, determiner prefix, token reordering, character-run lengthening, synonym-token
   substitution from a synthetic synonym map, and a synthetic "translation" into a fake second
   language with its own token pool.
3. Inject **hard negatives** by construction: synthetic antonym pairs and sibling pairs that
   share tokens (the §2.6 failure mode), plus pairs that co-occur on the same synthetic item
   (the anti-merge guard's target case). Co-occurrence is generated at **three support scales**
   (4/4, 100/100, 400/400) and in two conditions — genuinely dependent, and independent at the
   same marginals — because the guard's defect was visible only at scale.
4. Generate the arm split with the real `p_holdout` and the real arm sizes, under a **null**
   condition where suggestions have no effect, so the §3.5 suite can be checked for false
   alarms before it is trusted to raise true ones.
5. Score the pipeline with pairwise precision/recall, B-cubed and V-measure against the known
   partition, **and** report where each metric lands relative to a random-partition baseline;
   a V-measure with no baseline is not interpretable at these cluster counts.

No real slang from any market, no real category terms, no real brand names other than generic
placeholders appear in any fixture, default, or seed file — §34. The synthetic languages (two
monolingual pools plus a generated mixed pool) exercise the §5 partitioning logic without
asserting anything about any real language. Note that this is a deliberate limitation as well as
a safeguard: synthetic variants test the *mechanism* and cannot tell us whether the thresholds
are right for real short-phrase embeddings. Only §2.10 does that.

Invariant tests that must pass on every build:
- **I1 (rebuildability):** rebuilding at a pinned `as_of_seq` reproduces `observation_label_map`
  and `labels` byte-for-byte given the same config, decisions, brand snapshot and seed. Run
  twice with shuffled physical row order to catch any residual order dependence.
- **No wall-clock:** a rebuild executed with the system clock advanced by 90 days produces an
  identical result. This is the regression test for the `current_date` class of bug.
- **No-loss:** every `surface_form` ever ingested is retrievable via
  `LabelDetail.raw_surface_forms`, **and** every rejected proposal is retrievable from
  `label_observation_rejects`. A malformed proposal must never cost its siblings: a set of 10
  proposals with one invalid gloss ingests 9 and records 1.
- **Append-only:** `UPDATE`/`DELETE`/`TRUNCATE` on `label_observations` **raise**, not silently
  no-op.
- **No alias chains:** no `labels` row's `canonical_id` points at a row that itself has a
  `canonical_id`; after a leader-into-leader merge, the absorbed leader's aliases point at the
  survivor.
- **Resolution soundness:** for a synthetic item emitting three aliases of one concept,
  `support_count`, `label_support_daily.distinct_content` and `mv_label_support_weekly` all
  report **1**, not 3. (Direct regression test for the roll-up double count.)
- **No cross-bucket merge:** no `labels` row has a `canonical_id` pointing at a different
  `lang_bucket`.
- **Concept-group soundness:** no emitted `concept_group` contains two members from the same
  `lang_bucket`; a synthetic A–B–C translation chain across two buckets produces zero groups and
  one review item.
- **Gate-required lexical facets:** no `slang` or `product_pairing` merge exists without a
  recorded `lexical_signal`.
- **Guard scale-invariance:** generated pairs at supports 4/4, 100/100 and 400/400 with
  co-occurrence drawn *under independence* must not trigger the anti-merge guard at a rate above
  ~1%. (Regression test for the ratio-rule defect in §2.6.)
- **Reversibility:** reversing any merge restores the pre-merge counters exactly.
- **No market strings:** a repository scan asserts that no language code, determiner, or
  market-specific token appears in any `.py` file, prompt template, enum, or default of this
  subsystem — only in configuration and migration seed data. This is the executable form of
  §1/§34 and of `03-multimodal-analysis` §0.3.
- **Bias-metric null calibration:** run the §3.5 metrics on a synthetic corpus generated with
  *no* anchoring and the two real arm sizes. The expected number of FDR-controlled alarms is
  ≤ 0.10 × 14 facets. A suite that alarms on an unbiased corpus is measuring sample size.

---

## 9. Genuine open questions

These are unresolved, not rhetorical. Each names the measurement that would resolve it.

1. **All τ values are reasoned priors, not measurements.** `text-embedding-3-large` cosine
   calibration on 1–5 token glossed phrases is not something we have measured. §2.10's
   calibration is a required deliverable before any taxonomy is shown to a user; until it runs,
   the promoted vocabulary is provisional and every bucket carries `uncalibrated_bucket`.
   *Resolution: the per-posture-group calibration set of §2.10 — ≥ 120 human-anchored pairs per
   group per active bucket, reporting a Clopper–Pearson lower bound, not a point estimate.
   The first draft's 300-pair plan could not have supported its own claim and has been
   replaced.*
2. **Morphologically rich buckets have no lemmatiser**, and short-phrase embedding quality in
   them is unmeasured. §2.2 step 9 deliberately does no morphological normalisation there, so
   those buckets will carry more duplicates. *Resolution: per-bucket precision/recall from the
   calibration set; fallback (lexical-gate-mandatory for the whole bucket) stated in §5.5.*
3. **The redundancy estimates in §2.1 (6–10×, 7k–11k raw forms) are extrapolations.** If actual
   distinct-form count exceeds ~15,000 the leader pass stays fast but the review band could
   exceed 3,000 pairs (~$18 at the two-order protocol, not ~$3.55) and `S_min = 8` becomes too
   permissive relative to a longer tail. *Resolution: measure distinct-form count at 300
   analysed items and extrapolate before committing thresholds. This is the cheapest measurement
   in the whole design and it gates two others.*
4. **Is the 0.35–0.65 healthy uptake band right?** It is reasoned from how list length changes
   model behaviour, not measured. The band's upper edge is the one that matters (it is the
   anchoring alarm), and we have no empirical anchoring curve for `claude-opus-5` on this task.
   *Resolution: a deliberate A/B at caps 6 / 12 / 24 on the first 600 items, reading β gap and
   JSD per §3.5.*
5. **The holdout arm is underpowered for tail comparison.** ~225 units cannot detect
   suppression of low-support concepts, which is precisely where suppression would occur first.
   Raising `p_holdout` to 0.30 would fix the power and halve the consistency benefit.
   *Unresolved tradeoff; recommend starting at 0.15, measuring metric 6, and raising it if any
   holdout-only label reaches `emerging_candidate`.*
6. **Facet assignment may be the real drift problem.** Vocabulary drift is well handled here,
   but the *same concept arriving under different facets* across calls (an occasion labelled
   as a behaviour) is a second, structurally similar problem that §4.6 only partially handles —
   and it is deliberately not auto-resolved, since auto-filing `unexpected` is how discovery
   dies. *Resolution: measure cross-facet near-duplicate rate (pairs within `τ_review` across
   facets) at run 1; if it exceeds ~10% of canonicals, facet assignment needs its own
   consolidation pass.*
7. **Whether promotion should require human review at all.** The current gate is fully
   automatic with a curation UI available but optional. For a first customer deliverable,
   requiring sign-off on the first ~100 promotions per facet may be the right call; it is a
   product decision, not a technical one.
8. **A mixed bucket is one bucket**, but "language A sentence with one language B word" and its
   mirror are arguably different registers that a marketer would distinguish. Splitting by
   majority-token language is cheap to add. *No evidence yet on whether the distinction is used;
   deferred rather than guessed.*

9. **The gloss contract with subsystem 03 is unsigned.** §2.4's embedding key and §5.3's
   cross-language pivot both require a mandatory English gloss and a `facet` per label; 03 emits
   neither today. *Resolution: a contract change in 03 (the table in §2.4). Until then this
   subsystem runs glossless and the cross-language deliverable does not exist. This is the
   highest-priority integration item, ahead of any threshold work, because calibrating a key we
   are not actually embedding would waste the calibration.*

10. **Canonical identity is not stable across a `canon_version` rebuild.** The §2.9 hysteresis
    keeps display forms from flapping between runs, but nothing preserves label *identity* when a
    recalibration legitimately re-partitions a cluster — which breaks saved UI filters, any
    externally stored `label_id`, and any trend series a user was watching. Options are an
    identity-continuity mapping table (`label_id_old → label_id_new` with a match rule based on
    shared observations), or accepting churn and versioning saved filters. *Unresolved. It needs
    deciding before the first customer saves a filter, not after.*

11. **Whether `evidence_strength` should exist at all.** §4.3 now excludes the model's
    self-assessment and returns its terms separately, which removes the worst objection. It is
    still a weighted composite with editorially chosen weights, and brief §33 warns against
    exactly that shape. The alternative is to publish the four evidence counts and no composite,
    and let sorting be explicit. *Product decision; the composite is retained for now only
    because a sortable single column is what makes a 600-row vocabulary usable.*

---

## 10. Review notes

Adversarial review, 2026-10-02. The design's core architecture survived: the immutable emission
log, the two-count discipline (`observation_count` vs `support_count`), leader clustering over
connected components, the lexical gate against antonym/co-hyponym merges, keeping languages
distinct rather than merging them, publication-date rather than analysis-date temporal gates,
the holdout arm, and the refusal of a closed label list are all correct and are kept with their
arguments intact. The API shapes were checked and are right: `claude-opus-5`,
`thinking={"type":"adaptive"}`, `output_config={"effort": ...}`, `client.messages.parse(
output_format=...) -> response.parsed_output`, $5/$25 per Mtok with 1.25×/0.1× cache multipliers,
`text-embedding-3-large` at `dimensions=1536` and $0.13/Mtok, and pgvector's 2000-dimension HNSW
cap. What follows is what was wrong.

### Fatal — fixed

1. **I1 was unimplementable.** `normalised_form`, `lang`, `norm_steps` and `label_id` were stored
   on the append-only `label_observations` while being outputs of `norm_pipeline_version` and of
   the corpus-derived affinity table. A recalibration could never re-point an observation, so
   "any threshold can be changed and the whole taxonomy rebuilt" was false upstream of the
   embedding, and §2.2 step 8's "self-heals on the next pass" could not happen. **Fix:** derived
   assignment moved to `observation_label_map`, keyed by `canon_version`, truncate-and-rebuild;
   I1 restated with its exact input set and a pinned `as_of_seq` (§0, §1.2, §1.4).
2. **The calibration could not establish its own claim.** 300 pairs over 11 facets × 8 bands is
   ~3 pairs per cell; a 95% Clopper–Pearson lower bound of 0.95 needs ≥ 59 error-free pairs per
   stratum (27 pairs gives 0.895 even with zero errors). The human subsample was the model's
   *least* confident pairs, which cannot bound the precision of confident merges, and the same
   model produced both the ground truth and the production verdicts. Since this was the stated
   remedy for every (prior) threshold in the document, the whole threshold story rested on it.
   **Fix:** posture-group strata (5, not 14), ≥ 120 pairs per group per active bucket
   concentrated in the decision band, random human subsample with two annotators and κ, a target
   of lower-bound ≥ 0.90 stated honestly instead of an unreachable 0.95 (§2.10, §2.7).
3. **The weekly roll-up re-introduced alias multiple-counting.** `mv_label_support_weekly` summed
   per-leaf `distinct_content` across aliases — one item emitting two aliases counted twice in
   every sparkline and in the trajectory test's numerator, which is precisely the trap §1.3
   exists to prevent. `label_support_daily` also had no job writing it. **Fix:** daily table
   keyed on the resolved label with alias-resolved distinct counts, view becomes a plain
   re-bucket, `rollup_label_support` job added (§1.4, §6.4, §7.2).
4. **The trajectory badge had no multiplicity control and the wrong variance model.** `p < 0.10`
   per label across 250–600 promoted labels yields ~25–60 spurious growing/declining badges per
   run on a static corpus; counts cluster by creator and query, so Poisson variance is
   optimistic; and rate normalisation does not remove survivorship (older windows contain only
   still-ranking content). **Fix:** BH-FDR at q ≤ 0.10 over the run's family, overdispersion
   correction, a `frame_unstable` precondition, and a standing `survivorship_window` caveat
   rendered next to every badge (§6.4).
5. **Structured-output constraints silently discarded whole items.** `min_length`/`max_length`/
   `ge`/`le` and list caps are stripped from the schema sent to the API and validated
   client-side, so one short gloss raised a `ValidationError` that threw away every label for
   that item — invisible data loss on the ingest path of a subsystem whose central argument is
   that discarded observations are unrecoverable. **Fix:** permissive model-facing schema,
   per-proposal validation, `label_observation_rejects` quarantine, constraints moved into the
   prompt text (§3.4c).

### Serious — fixed

6. **Alias chains.** One-hop `COALESCE(canonical_id, id)` silently splits support the first time
   a `must_link` or an adjudication merges two leaders. Added a depth trigger, recursive
   resolution, path compression on merge-away, and a test (§1.4, §1.5, §4.5).
7. **The anti-merge guard was not scale-free and its justification was arithmetically wrong.**
   Under independence `E[cooc_ratio] = max(support)/N`, so at 1,500 units two labels at 300/400
   support exceed the 0.25 threshold *by chance*, blocking every merge among frequent labels;
   at min support 4 a single shared item fired it (p ≈ 0.011). Replaced with
   lift ≥ 3 ∧ hypergeometric p ≤ 0.01 ∧ ≥ 2 shared units, with the behaviour table (§2.6).
8. **Bias metrics compared arms of unequal size with size-dependent statistics.** Heaps' β fitted
   over different `n` ranges, hapax-per-1,000 (which falls with `n` by construction) and JSD
   (positively biased at small `n`) would all have alarmed on 1,275-vs-225 alone. Replaced with
   bootstrap rarefaction to matched `n`, permutation nulls for JSD and rank effects, BH-FDR over
   the 84-comparison family, and `canon_version` on the report because every metric is
   τ-dependent (§3.5).
9. **The holdout arm fed the suggestions it was the control for.** Selection read `support_count`
   (both arms), so holdout-only discoveries were promoted into the suggested arm's block —
   falsifying §3.2's independence claim and letting bias metric 6 erase its own evidence. Now
   `support_suggested` (§1.3, §3.3).
10. **Counter SQL defects, each changing a user-visible number.** `suggested_share` was set from
    `AVG(...)`, which is NULL over zero rows into a NOT NULL column; platform/strategy diversity
    was read from `content_items`, so marketplaces never counted as platforms and the ≥ 2
    platform gate punished the social-to-commerce pattern brief §17 calls the differentiator;
    `distinct_queries` was used by two gates and never computed; `suggested_share` was
    observation-weighted so one verbose item could trip a 0.90 threshold alone;
    `discovery_strategy` was a single `char(1)` on the observation although `research_queries.
    strategy` is an enum of names and an item is found by several queries. All rewritten against
    `research_results` (§1.5).
11. **Promotion-gate contradictions.** The "≥ 6 distinct content items" gate was vacuous and its
    justification contradicted §1.3; the near-duplicate gate made both halves of any
    deliberately-blocked pair permanently unpromotable; the brand check read the live `brands`
    table, putting a moving input inside I1; a NULL strategy would have failed the gate silently
    for every label. Fixed, with a `brand_name_snapshot` pinned per generation (§4.1).
12. **`status` could not hold a lifecycle state and a flag at once**, so §4.5's own promises —
    "dormancy is a finding, not a demotion", "flagged labels stay visible" — were
    unrepresentable, and flagging a canonical silently removed it from default reads. Split into
    `status` + `flags label_flag[]` (§1.4, §4.5).
13. **Determinism claims were false.** HNSW is approximate and order-dependent, `k=25` was taken
    *before* the leader filter (so a true nearest leader at rank 26 was missed), and the
    processing order used support, which moves during a run. Replaced with exact in-partition
    scans (49 MB total; the index bought nothing), an explicit provisional/final split, and a
    `reconcile_provisional` job (§2.5, §7.2).
14. **`current_date` in §4.2 and §6.4** made status and trajectory functions of wall-clock,
    violating I1. Now `run_as_of_date`, with a clock-advance regression test (§4.2, §6.4, §8).
15. **`share` was presented as "the comparable figure" with no sampling statement.** The corpus
    is a non-probability sample shaped by query selection, platform ranking and survivorship;
    cross-scope share deltas can be produced entirely by query-mix changes. Added the
    `SamplingFrame` contract, `denominator_definition`, and `frame_comparable` (§6.1, §6.2).
16. **LLM-authored numbers and ids gated irreversible operations.** A merge was licensed by the
    model's own `confidence >= 0.70`, and verdicts were joined to pairs by a model-authored
    `pair_index`. Replaced with code-generated pair ids validated as an exact set, and
    **two-order agreement** as the merge gate; confidence is retained for audit and queue
    ordering only (§2.8). Support counts were removed from both the adjudication prompt and the
    definition prompt so the model cannot restate our numbers back to us (§2.8, §4.4).
17. **`concept_groups` used connected components** — the pathology §2.5 rejects — so two
    same-language concepts could fuse through a shared cross-language link, behind a roll-up the
    intelligence layer consumes. Added the one-label-per-bucket rule, a size cap, and a
    mutual-link requirement, with rejected components surfaced as findings (§5.4).
18. **The facet set was wrong and its justification was false.** The claim that the brief
    "enumerates this set in §19B and §20" does not hold — §19B names thirteen kinds, §20 nine
    arrays — and the eleven-facet list dropped `social_context`, `activities` and `narrative`,
    all of which subsystem 03 emits or the brief names. Those observations would have been bent
    into `occasion`/`behaviour`/`unexpected`, the exact failure the facet argument exists to
    prevent. Now fourteen facets with a total mapping table (§1.1).
19. **§4.3 `confidence` could evaluate negative** (violating its own CHECK) **or NULL**, and gave
    25% weight to the model's self-assessment in a displayed figure —
    `03-multimodal-analysis` documents that same field as "the system does not display it", and
    brief §33 warns against arbitrary AI scores. Clamped, self-assessment removed and shown
    separately, renamed `evidence_strength` (§4.3).
20. **The gloss contract with subsystem 03 does not exist.** §2.4 and §5.3 both require a
    mandatory English gloss and a per-label `facet`; 03 emits an optional, any-language
    `elaboration` and no facet. Stated as a blocking integration item with the required contract
    change, and the glossless degradation made explicit (§2.4, §9.9).

### Seeding violations — removed

21. **A hand-written Tagalog determiner list** (`ang, ang mga, mga, si, sina`) sat in the
    normalisation pipeline three paragraphs before §2.3 declared such a list forbidden and §8
    claimed none existed. Replaced with per-language closed-class function words in
    configuration, with a build-time test that rejects any content word (§2.2 step 7).
22. **`CREATE TYPE lang_bucket AS ENUM ('en','tl','taglish','other','und')`** and
    **`language_observed: Literal["en","tl","taglish","other","unclear"]`** put a closed list of
    one market's languages into the schema and, worse, into the extractor's prompt-facing
    contract. `03-multimodal-analysis` §0.3 forbids exactly this ("A closed language enum is a
    market assumption"; no language name "as an example, a hint ... in prompt text"; no locale
    string in the analysis prompt), and its own `language_detected` is deliberately free text.
    Replaced with a `lang_buckets` lookup table seeded from `research_runs.language_codes`, and
    free-text `language_observed` (§1.4, §3.4c).
23. **Per-facet thresholds keyed on `'tl'`/`'taglish'`** and a trigram gate justified by one
    language's morphology are now keyed on bucket attributes (`rich_morphology`,
    `embedding_calibrated`) set in configuration (§2.6, §2.7, §5.2).
24. **§2.3 presumed a two-language market** with a document-level tl/en tag that 03 does not
    provide. Generalised to `k` configured codes with an explicit mapping from 03's free-text
    `languages[]`, and the circularity stated (§2.3). §5's prose was de-seeded; the argument is
    unchanged because it never depended on which languages they were.

### Smaller corrections

25. `CREATE RULE ... DO INSTEAD NOTHING` fails silently and makes `INSERT ... ON CONFLICT`
    illegal on the table; replaced with a raising trigger (§1.4).
26. Thresholds lived in both `label_facets` and `canonicalisation_config`; `label_facets` now
    carries none (§1.4, §2.7).
27. §2.3's "min 5 occurrences stops one transcript deciding a token's language" was false —
    occurrences, not documents. Now distinct documents (§2.3).
28. §2.1's "30–40% of emissions" contradicted its own 7k–11k range; corrected to 23–37%.
29. §7.2's cadence rationale conflated canonical-set freshness with snapshot staleness (a
    snapshot is up to 50 analyses old by construction).
30. Prompt-cache preconditions (two breakpoints, placement, TTL, the 512-token minimum on Opus
    5) were implicit; the claimed $0.55 depends on them entirely (§3.3).
31. §2.9's `contains_minority_proper_noun`, `is_sentence_like` and `lexical_typicality` were
    undefined; all three now have corpus-derived, language-neutral definitions (§2.9).
32. `max_tokens` unspecified on batched calls; `analysis_id` had no referenced table; the
    `commerce_review` source kind had no review-level identifier (§1.4, §2.8).
33. Cost table corrected to **≈ $11.30 run 1 / ≈ $5.40 steady state** (two-order adjudication,
    uncalibrated-band widening, definitions marked first-run-only) (§7.3).

### Claims in the design that were checked and are correct

The §3.1 case against a closed label list, the §5.1 case for keeping languages distinct, the
support/observation split, the publication-date temporal gate, the lexical gate's
antonym/co-hyponym rationale, the choice of leader clustering over HDBSCAN and connected
components, fixture generation from synthetic concepts, and all Anthropic/OpenAI API shapes and
prices. Three self-reported decisions did **not** match the document and are noted here so the
record is accurate: `τ_review` was not uniformly `τ_merge − 0.08` (three facets used −0.10);
"concept_groups = connected components" was presented as a virtue two sections after connected
components were rejected; and "support_count is the ONLY number used for promotion, ranking,
confidence and UI" was not true of §4.1 or §4.3.

### Residual risk

- **Every threshold in this document is still a prior.** Nothing is measured until §2.10 runs,
  and §2.10 is now more expensive than the version it replaces (~$8–16 of model time plus 10–20
  annotation hours). If that budget is refused, the honest fallback is lexical-gate-mandatory
  everywhere, not shipping the priors.
- **The §2.6 lift floor of 3 and p of 0.01 are themselves priors**, chosen to be scale-free
  rather than to be right. They should be read off the run-1 co-occurrence distribution.
- **The holdout arm remains underpowered for the tail** (~225 units). This is unchanged by the
  review; rarefaction makes the comparison *valid*, not *powerful*. A zero suppression count is
  still not evidence of no suppression, and the report must keep saying so.
- **Survivorship bias in trajectory is mitigated, not solved.** `frame_unstable` and the standing
  caveat stop the worst claims; genuinely correcting it needs crawl-time rank data per item and
  a model of what fell out of the ranking, which is out of MVP scope.
- **The 03 gloss contract is unsigned.** Until it lands, cross-language concept grouping — a
  named deliverable — does not exist, and the embedding key is weaker than designed.
- **Label identity across a `canon_version` rebuild is unresolved** (§9.10). The first customer
  who saves a filter will find out before we do.
- **Facet assignment drift** (§9.6) is measured but not fixed, deliberately.
- **Exact scans replace ANN at ~8k forms.** That is comfortable now; at ~50k forms per partition
  the pass needs revisiting, and the measurement in §9.3 is what would warn us.
