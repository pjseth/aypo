# 01 — The Agentic Research Loop

**Subsystem:** orchestration engine — control algorithm, budget accounting, saturation and stopping.
**Normative source:** `docs/BRIEF.md` §§1, 7, 8, 9, 25, 26, 32, 34.
**Owner of:** `research_runs`, `research_iterations`, `research_queries`, `research_results`,
`research_log`, `research_budget_ledger`, `research_saturation`, `research_run_transitions`.
**Python package:** `apps/api/aypo/research/`.

> **Reference convention.** `§N` alone means a section of *this* document. `brief §N` means a
> section of `docs/BRIEF.md`.

### Anti-seeding compliance statement (brief §1, brief §34)

Nothing in this design encodes a consumer, occasion, competitor, theme, slang term, cultural
reference or category assumption about the research subject. Every content-bearing string the loop
uses at runtime comes from exactly three places, all auditable:

1. the run's `objective` and `subject_brand` / `market_code` / `language_codes` config, supplied by
   the operator when the run is created;
2. optional, explicitly flagged `user_seeds` (brief §23) which widen discovery and never gate it;
3. vocabulary **observed in already-collected data** — cluster labels, discovered open-ended labels
   (brief §19B), detected brand names, transcript/caption terms.

§3.4 below specifies a mechanical guardrail (`rejected_guardrail`) that rejects any proposed query
introducing a token traceable to none of those three sources, so the prohibition is enforced by the
code path rather than by prompt discipline alone. Placeholders in this document are `Brand S`
(the subject), `Brand A`/`Brand B` (discovered others), `Theme X`, `Cluster Y`.

---

## 0. What the engine is, in one page

A run is a **durable finite state machine advanced by short, idempotent ticks**. There is no
long-lived in-memory loop: every tick claims a lease on one run row, advances at most one phase,
writes its results, and returns. This is forced by the environment (no Redis, no Celery, no Docker;
background work must ride a Postgres-backed queue) and it is also what makes brief §9's "the research
process must be auditable" and resumability true by construction rather than by effort.

```
                    ┌──────────── tick (worker) ─────────────┐
                    │  1. claim lease on one research_run    │
                    │  2. read state + iteration phase       │
                    │  3. run ONE phase handler (idempotent) │
                    │  4. persist rows + log + ledger        │
                    │  5. advance phase/state, release lease │
                    └────────────────────────────────────────┘
```

The engine's currency is the **collection unit (CU)**, not the dollar and not the token. §7 below
shows why: at the brief §6 target dataset the loop's own reasoning spend is ~6% of run cost, while
~94% is caused downstream by how many items the loop decided to collect. Controlling CU controls
cost. Dollars and wall clock are *guards* on top of CU, not the allocation currency.

---

## 1. The run state machine

### 1.1 Two levels

Run-level state is coarse and user-meaningful (it is what §32's header renders). Iteration-level
*phase* is fine-grained and is what makes a crash recoverable to within one phase.

```python
# aypo/research/states.py
from enum import StrEnum

class RunState(StrEnum):
    CREATED      = "created"       # row exists, nothing spent
    PLANNING     = "planning"      # capability probe + objective decomposition + seed probes
    ITERATING    = "iterating"     # normal iteration loop
    FOCUSING     = "focusing"      # partial saturation: narrowed, shrunken iterations (§5.7)
    STOPPING     = "stopping"      # graceful drain: no new discovery, finish in-flight analysis
    SYNTHESIZING = "synthesizing"  # final findings/evidence pass + run report
    COMPLETED    = "completed"     # terminal, stop_reason set
    FAILED       = "failed"        # terminal, no usable output
    PAUSED       = "paused"        # operator hold or global circuit breaker
    CANCELLED    = "cancelled"     # terminal, operator cancelled

class IterationPhase(StrEnum):
    ALLOCATE      = "allocate"       # compute quota vector
    PROPOSE       = "propose"        # LLM proposes queries
    DEDUPE        = "dedupe"         # lexical + semantic dedup + guardrail
    DISCOVER      = "discover"       # enqueue search jobs
    DISCOVER_WAIT = "discover_wait"  # await search job completion
    RETRIEVE_WAIT = "retrieve_wait"  # await media/metadata retrieval
    ANALYZE_WAIT  = "analyze_wait"   # await multimodal analysis + embeddings
    CLUSTER       = "cluster"        # recompute cluster snapshot for this iteration
    INTERPRET     = "interpret"      # patterns, competitors, hypotheses, draft findings
    MEASURE       = "measure"        # saturation metrics
    DECIDE        = "decide"         # stop predicate evaluation
    CLOSED        = "closed"         # iteration finished cleanly
    ABANDONED     = "abandoned"      # iteration failed 3 attempts; corpus kept, iteration skipped
```

### 1.2 Run-level transitions

| From | To | Guard |
|---|---|---|
| CREATED | PLANNING | operator starts run; `objective` non-empty; LLM capability present |
| CREATED | FAILED | `capabilities()["llm"]` false — the loop cannot reason at all |
| PLANNING | ITERATING | ≥1 discovery provider available **and** ≥1 admitted seed query |
| PLANNING | FAILED | zero discovery providers available (`source_exhaustion` at planning time) |
| ITERATING | ITERATING | `DECIDE` returned `continue` and `iteration_no+1 < max_iterations` |
| ITERATING | FOCUSING | `partial_saturation` (3 or 4 of 5 metric families satisfied), §5.7, and `focus_iterations_used < 2` |
| FOCUSING | ITERATING | the focus iteration produced ≥1 new admissible hypothesis, ≥1 competitor entrant, or ≥1 disconfirming observation (§5.7 — **not** a metric threshold, which a focus iteration cannot move) |
| ITERATING / FOCUSING | STOPPING | any stop predicate fired; or budget guard ≥ 0.95 |
| STOPPING | SYNTHESIZING | all in-flight analysis jobs terminal, or drain deadline hit |
| SYNTHESIZING | COMPLETED | run report written |
| any non-terminal | PAUSED | operator pause, or global provider circuit open for all providers |
| PAUSED | previous state | operator resume; lease re-acquired |
| any non-terminal | CANCELLED | operator cancel; outstanding jobs cancelled |
| any non-terminal | FAILED | unrecoverable: DB unavailable, embedding space mismatch, 2 consecutive `ABANDONED` iterations **with zero admitted items in the run** |

Two consecutive abandoned iterations when the run *does* have admitted items go to STOPPING with
`stop_reason='repeated_iteration_failure'`, not FAILED: §28 requires the system to report what it
does know and label the gap, not to throw the corpus away.

### 1.3 Iteration-level transitions

Linear, with three retry edges and three skip edges:

```
ALLOCATE → PROPOSE → DEDUPE → DISCOVER → DISCOVER_WAIT → RETRIEVE_WAIT
         → ANALYZE_WAIT → CLUSTER → INTERPRET → MEASURE → DECIDE → CLOSED

retry edges   : PROPOSE→PROPOSE (LLM), DISCOVER_WAIT→DISCOVER (all searches failed),
                any phase → itself while attempt < 3
skip edges    : DEDUPE→MEASURE      when 0 queries admitted (nothing to collect)
                ANALYZE_WAIT→MEASURE when 0 items analysed (cluster/interpret have no input)
                CLUSTER→INTERPRET    when corpus < min_corpus_for_clustering (default 60)
abandon edge  : any phase → ABANDONED at attempt == 3
```

`CLUSTER` is skipped, not failed, below 60 analysed items: HDBSCAN with `min_cluster_size=8` on
fewer than ~60 points returns either one blob or all-noise, and a cluster snapshot built from that
would poison the iteration-to-iteration stability comparison in §5.3 with a meaningless baseline.
60 = 7.5 × `min_cluster_size`, the point at which more than one non-trivial cluster is
geometrically possible.

### 1.4 Leasing and single-flight

```sql
-- CLAIM: one worker, one run, one phase at a time.
-- `pause_requested = false` is what lets a PAUSED run be resumed: the resume
-- endpoint clears the flag, and the next tick may then re-claim the row. An
-- unconditional `state <> 'paused'` filter would make PAUSED terminal.
UPDATE research_runs
   SET lease_owner      = $worker_id,
       lease_expires_at = now() + interval '120 seconds',
       lease_epoch      = lease_epoch + 1
 WHERE id = $run_id
   AND next_tick_at <= now()
   AND state NOT IN ('completed','failed','cancelled')
   AND (state <> 'paused' OR pause_requested = false)
   AND (lease_expires_at IS NULL OR lease_expires_at < now())
RETURNING *;

-- HEARTBEAT: extends the lease and MUST NOT touch lease_epoch. Bumping the
-- epoch here would invalidate the running worker's own in-memory epoch and
-- abort every subsequent write it makes.
UPDATE research_runs
   SET lease_expires_at = now() + interval '120 seconds'
 WHERE id = $run_id AND lease_owner = $worker_id AND lease_epoch = $epoch;
```

The undirected `tick()` (claim *any* due run) wraps the same UPDATE around
`SELECT id FROM research_runs WHERE next_tick_at <= now() AND <claimable> ORDER BY next_tick_at
FOR UPDATE SKIP LOCKED LIMIT 1`, so N workers never contend for the same row.

- **TTL 120 s, heartbeat every 30 s.** One `effort="high"` Opus call with adaptive thinking on a
  25k-token context routinely runs 45–90 s, so a TTL below ~110 s would let a healthy worker lose
  its own lease mid-call. 120 s tolerates three missed heartbeats and bounds crash recovery to
  two minutes.
- `lease_epoch` is carried into every write the phase makes (`research_log.lease_epoch`). A
  zombie worker that wakes up after its lease expired finds its epoch stale and aborts before
  writing, so a split brain cannot double-charge the ledger.
- **The heartbeat is a cancellation signal, not just a keepalive.** It runs as a separate task
  alongside the awaited call. If it updates 0 rows the lease has been lost (clock skew, a
  pathologically long call, an operator force-release); the handler must then abort *before its
  next write* rather than finish and write with a stale epoch. Phase handlers therefore check
  `ctx.lease_live` immediately before every persisting statement.
- **`phase_attempt` is scoped to the phase, not the iteration.** It resets to 0 on every phase
  transition. A single counter spanning phases would abandon an iteration whose PROPOSE, CLUSTER
  and INTERPRET each needed one retry — three ordinary, independent hiccups.
- Long waits (`*_WAIT`) do **not** hold the lease across the wait. The phase handler polls once,
  and if the completion predicate is unmet it releases the lease and schedules the next tick at
  `now() + poll_interval` (10 s for DISCOVER_WAIT, 20 s for RETRIEVE_WAIT, 30 s for ANALYZE_WAIT).
  Polling cost is a few hundred cheap count queries per run.

### 1.5 What is persisted at each step

Every phase is defined by three things: the rows it writes, a **completion predicate computable
purely from rows** (never from worker memory), and an **idempotency key** that makes a replay
after a crash a no-op rather than a double charge.

| Phase | Rows written | Completion predicate (SQL-computable) | Idempotency key |
|---|---|---|---|
| PLANNING | `research_runs.config_resolved`, `provider_health`, `research_log(capability_probe, seed_probe_set)` | `config_resolved IS NOT NULL` | `(run_id,'planning')` |
| ALLOCATE | `research_iterations` row with `quota jsonb`, `budget_snapshot jsonb` | iteration row exists for `iteration_no` | `(run_id,i,'allocate')` |
| PROPOSE | `research_queries` rows `status='proposed'` + `research_log(queries_proposed)` | `count(*) WHERE status<>'proposed'` is irrelevant; `exists(proposed_at IS NOT NULL)` on the iteration | `(run_id,i,'propose',attempt)` |
| DEDUPE | `research_queries.status` → `admitted` / `rejected_*`, `dup_of_query_id`, `dup_similarity`, embeddings | no `status='proposed'` rows remain for `(run_id,i)` | per-query row update (naturally idempotent) |
| DISCOVER | queue jobs (`kind='search'`), `research_queries.status='queued'`, `job_id` | every admitted query has `job_id IS NOT NULL` | `sha256(run_id,i,query_id,'search')` as queue dedupe key |
| DISCOVER_WAIT | `research_results` rows (by the search workers) | `0 = count(jobs WHERE run_id,i,kind='search' AND state IN ('queued','running'))` OR phase deadline | job-level |
| RETRIEVE_WAIT | `content_items` / `commerce_items` (collection subsystem), `research_results.content_item_id` | `0 = count(research_results WHERE admitted AND analysis_state='pending_retrieval')` OR deadline | job-level |
| ANALYZE_WAIT | `content_analysis`, `content_embeddings` (analysis subsystem) | `0 = count(research_results WHERE admitted AND analysis_state IN ('retrieved','analyzing'))` OR deadline | job-level |
| CLUSTER | `clusters(run_id,iteration_no)`, `cluster_members(run_id,iteration_no)` snapshot | a snapshot row set exists for `(run_id,i)` | `(run_id,i,'cluster')` |
| INTERPRET | `hypotheses`, `competitor_relationships`, draft `findings`, `finding_evidence`, `research_log(pattern_detected, hypothesis_created, competitor_discovered)` | `research_iterations.interpreted_at IS NOT NULL` | `(run_id,i,'interpret',attempt)` |
| MEASURE | `research_saturation` row for `(run_id,i)` | row exists | `(run_id,i,'measure')` |
| DECIDE | `research_runs.state/stop_reason`, `research_run_transitions`, `research_log(stop_evaluated)` | `research_iterations.decided_at IS NOT NULL` | `(run_id,i,'decide')` |

**Guards are evaluated at every phase entry, not only at DECIDE.** `BudgetState.guard_level()`
is read at the top of every phase handler. `hard_stop` (utilisation ≥ 1.00) short-circuits the
phase, cancels outstanding jobs for `(run_id, iteration_no)` and transitions straight to
SYNTHESIZING with `degraded=true`; `drain` (≥ 0.95) forbids opening DISCOVER but lets the
in-flight analysis phases finish. Evaluating guards only at DECIDE would let a single iteration
overshoot the cap by a whole iteration's worth of collection, which at iteration 0 is ~18% of the
social budget.

**Ledger-before-call rule.** Any phase that makes a billable external call writes a
`research_budget_ledger` row with `status='reserved'` and the idempotency key *before* the call,
then updates it to `status='settled'` with actual quantities after. A replay that finds a
`reserved` row with its own key and an age under the phase deadline treats the call as in flight
and waits; past the deadline it marks the row `orphaned` (keeping the reservation charged, because
a call that may have billed must be assumed to have billed) and retries with `attempt+1`. This is
the only correct option without provider-side idempotency, and it errs toward stopping early
rather than overspending silently — the same bias as `pricing.rates_for()`.

---

## 2. One iteration

### 2.1 Shape

```
ALLOCATE ── compute (social_units_i, commerce_units_i) and the strategy weight vector w
PROPOSE  ── one LLM call: given the evidence digest and the quota, propose N queries with rationale
DEDUPE   ── normalise, embed, reject lexical/semantic duplicates, enforce anti-seeding guardrail
DISCOVER ── fan out searches to available collectors, honouring per-strategy unit caps
  …WAITs ── retrieval + multimodal analysis + embedding (other subsystems, Postgres queue)
CLUSTER  ── recompute a full-corpus cluster snapshot stamped with iteration_no
INTERPRET── one LLM call: patterns, competitor updates, hypothesis create/resolve, draft findings
MEASURE  ── compute the five saturation metrics and write the row
DECIDE   ── evaluate the stop predicate stack; continue / focus / stop
```

Query count per iteration is derived, not configured, and is derived **per channel** because the
two channels' quotas move in opposite directions (§2.4):

```
n_social_i   = social_units_i   / ŷ_social      ŷ_* = EWMA(α=0.4) of admitted items per
n_commerce_i = commerce_units_i / ŷ_commerce          executed query, measured separately
n_queries_i  = clamp(round(n_social_i + n_commerce_i), 8, 60)
```

Seeds: `ŷ_social = 12`, `ŷ_commerce = 30`. A single social-only formula (the earlier version of
this spec) is wrong at the tail of the run: at `i = 7` the quota is 68 social and 387 commerce,
and `round(68/12) = 6 → clamp → 8` queries would have to carry 387 commerce records, i.e. ~48
records per query against a measured marketplace page size well below that. Deriving the two
separately gives `6 + 13 = 19` queries, which is the number the quota actually implies.

Seed rationale: a keyword search on any of the brief §4 sources returns tens of results of which a
minority survive relevance gating; the seeds are deliberately pessimistic so iteration 0
over-proposes rather than under-collects, and they self-correct after one iteration. Clamp floor 8
keeps strategy diversity possible (five strategies cannot be represented by fewer than ~8
queries); ceiling 60 bounds the PROPOSE output token count and the DEDUPE embedding batch.

### 2.2 The collection-unit vector

```python
# aypo/research/budget.py
@dataclass(frozen=True, slots=True)
class Units:
    social: int = 0      # one analysed social/video item
    commerce: int = 0    # one marketplace product/listing/review record
```

One social unit = one analysed social/video item. One commerce unit = one marketplace
product/listing/review record. **Both are charged 1.0 in their own dimension**; the channels are
never fused into one scalar, because they cost roughly 5× differently and because brief §6 sets
independent targets for them. A single fused unit would let cheap commerce records satisfy a
budget that was meant to buy video understanding.

The one place a *cost-equivalence* ratio is needed is §5.5's marginal-evidence-yield denominator,
where links bought with cheap records must not look more efficient than links bought with videos.
That ratio is **derived from the run's own ledger**, not hard-coded:

```
ρ_commerce = mean settled USD per commerce unit / mean settled USD per social unit
```

seeded at **0.18** from the §7 envelope (social all-in ≈ $0.040 = analysis $0.0295 + transcription
$0.0045 + collection $0.004 + embedding; commerce all-in ≈ $0.0072 = analysis $0.0032 + collection
$0.004), recomputed at each MEASURE from `v_research_spend_by_iteration` once ≥200 units of each
kind have settled, and clamped to [0.05, 0.50]. Hard-coding it was a defect in an earlier draft of
this spec: three different per-video costs ($0.085, $0.0313, the implied 1/17) appeared in three
sections, and the stop decision depended on which one you read.

### 2.3 Strategy allocation — gates, schedule, bandit, caps

The five brief §8 strategies are `brand | category | competitor | theme | hypothesis`.

**Step 1 — gates.** A strategy can only receive quota when the data to drive it exists. These are
not tuning knobs; they are definitional (you cannot write a competitor query before a competitor
has been discovered).

| Strategy | Gate |
|---|---|
| `brand` | always open (the run has a subject) |
| `category` | always open (category descriptor comes from run config) |
| `competitor` | ≥1 row in `competitor_relationships` for the run with `confidence ≥ 0.50` and `evidence_count ≥ 3` |
| `theme` | ≥3 clusters in the latest snapshot with `size ≥ 8` and a label assigned post-hoc (§21) |
| `hypothesis` | ≥1 `hypotheses` row with `status='open'` and `admissible = true` (§5.6) |

Gate thresholds: `confidence ≥ 0.50` + `evidence_count ≥ 3`. `0.50` must be read against the
competitor subsystem's own bands (`docs/design/06-competitor-discovery.md` §3.6: write threshold
0.15, "strong" at ≥0.55), so this gate sits just below their "strong" band — deliberately, because
the loop is deciding where to *spend*, not what to *claim*. If that subsystem recalibrates its
confidence formula, this gate must move with it; it is read from
`config_resolved.competitor_gate`, not hard-coded. `evidence_count ≥ 3` because a single
co-appearance is
indistinguishable from coincidence — three independent items is the smallest set where a
relationship can be stated with any sample-size honesty (§28), and spending a whole strategy's
quota chasing a one-item artefact is the most expensive failure mode available to this loop.
`≥3 clusters of ≥8` because theme-level querying needs to be able to *choose* between themes;
with one or two clusters, theme queries degenerate into category queries.

**Step 2 — scheduled prior.** Early broad, later targeted (brief §8). Raw prior as a function of
iteration index `i` (clamped at `i ≥ 7`):

| i | brand | category | competitor | theme | hypothesis |
|---|---|---|---|---|---|
| 0 | 0.25 | 0.75 | — | — | — |
| 1 | 0.19 | 0.56 | 0.15 | 0.10 | — |
| 2 | 0.15 | 0.43 | 0.20 | 0.11 | 0.11 |
| 3 | 0.11 | 0.35 | 0.23 | 0.15 | 0.16 |
| 4 | 0.09 | 0.27 | 0.23 | 0.20 | 0.21 |
| 5 | 0.08 | 0.25 | 0.22 | 0.20 | 0.25 |
| 6 | 0.08 | 0.24 | 0.21 | 0.19 | 0.28 |
| ≥7 | 0.08 | 0.24 | 0.21 | 0.19 | 0.28 |

(Table values are the closed form below, renormalised over gated-open strategies and rounded;
the closed form is the implementable spec and the table is derived from it, not vice versa.
Note that renormalisation puts `category` at 0.24 from `i = 6`, **below** its nominal 0.25 floor:
the floor is a step-4 projection constraint, not a property of this table, and these values are
the bandit's input rather than the final weights.)

Closed form: `brand = max(0.08, 0.25·0.75^i)`, `category = max(0.25, 0.75·0.75^i)`,
`competitor = min(0.22, 0.15 + 0.035·(i−1))`, `theme = min(0.20, 0.10 + 0.045·(i−2))`,
`hypothesis = min(0.29, 0.10 + 0.05·(i−2))`, then renormalise over *gated-open* strategies only.

Justification of the two floors, which are the load-bearing numbers:
- **`category` floor 0.25.** Every comparative claim the product makes has the form "within the
  analysed sample, Brand A appears more often in Theme X than Brand S" (§24). That requires a
  category baseline that was *not* collected by brand- or competitor-targeted queries, otherwise
  the denominator is contaminated by the sampling design. Holding ≥25% of every iteration on
  untargeted category sampling keeps a clean baseline growing for the whole run.
  **The floor in the closed form does not survive renormalisation and is therefore not the
  enforcement point.** At `i ≥ 6` the raw weights sum to 1.04 and renormalisation puts category at
  0.240; the bandit can push it lower still. The floor is enforced as a *projection constraint*
  in step 4, after the bandit, and only there. The table below shows the renormalised prior, which
  is the bandit's input, not the final weight.
- **`brand` floor 0.08, ceiling 0.25.** Brief §8 forbids a corpus that is mostly subject-branded.
  The decaying prior biases brand share downward, but it does **not** bound it: the step-3 bandit
  multiplies the prior by up to ×3.3, so a productive brand strategy at `i = 1` could be allocated
  well above its 0.19 prior. The cumulative ≤0.30 bound is produced by the step-4 projection *and*
  by the admission-time gate below, not by the shape of the prior. Brand cannot be zero, because
  subject-vs-category comparison needs subject-side observations.

**Step 3 — yield bandit.** Multiplicative weights over the prior, so the allocator actually learns
which strategies are paying (§8E and §37's "ability to discover unexpected patterns"):

```
ŷ_s  = EWMA(α=0.5) of per-strategy marginal evidence yield MEY_{i,s}   (§5.5)
z_s  = (ŷ_s − mean_s' ŷ_s') / max(std_s' ŷ_s', 0.05)
m_s  = exp(η · z_s),  η = 0.6
w_s  = normalise(prior_s(i) · m_s)
```

- **η = 0.6.** With `z ∈ [−2, 2]` the multiplier spans ×0.30 to ×3.3. That is enough to roughly
  triple a productive strategy's share inside a 6–10 iteration run, and too little to collapse the
  whole allocation onto one strategy in a single step.
- **std floor 0.05** stops the exponent exploding when all five strategies yield almost identically
  (early iterations), where the z-scores are pure noise.
- **Shrinkage, because taking a z-score over five noisy arms is a selection machine.** The largest
  of five independent noisy estimates is an upward-biased estimate of the largest true value
  (winner's curse); feeding it raw into an exponential would make the allocator chase sampling
  noise and then justify the chase with the spend it caused. Each strategy's yield is therefore
  shrunk toward the cross-strategy mean in proportion to its own precision before the z-score is
  taken:

  ```
  ŷ_s^shrunk = ȳ + B_s · (ŷ_s − ȳ) ,  B_s = n_eff,s / (n_eff,s + n_0) ,  n_0 = 30
  ```

  `n_eff,s` is the **effective** sample size of §5.8, not the raw item count. A strategy with
  `n_eff,s < 15` is given `B_s = 0` — it contributes nothing and receives the prior unmodified,
  because a yield estimated from fewer than 15 effective observations has a standard error
  comparable to the whole between-strategy spread. `n_0 = 30` is the shrinkage half-point: at
  `n_eff = 30` an arm gets half its observed deviation, at `n_eff = 120` it gets 80%.
  `n_0` is a configured prior, not a measurement, and is listed in §12 as a calibration target.

**Step 4 — projection onto the feasible set.** This is where every structural guarantee in the
design is actually enforced. Applied in this order, iterated to a fixed point (at most 5 passes;
the constraints are mutually feasible because `0.30 + 0.25 + 3 × 0.05 < 1`):

1. **Brand ceiling.** `w_brand ← min(w_brand, brand_headroom)` where
   `brand_headroom = max(0, 0.30·(corpus_social + social_units_i) − brand_social_admitted) / social_units_i`.
   Target: cumulative share of social items whose first admitting query had `strategy='brand'`
   stays ≤ **0.30** (brief §8).
   Why 0.30: "not mostly" means strictly <0.50; 0.30 leaves real headroom because brand queries
   also legitimately surface category content, and it guarantees ≥70% non-brand, which is what
   gives the category baseline enough `n` for cluster-level comparative statements. At the
   boundary the brand strategy's quota is truncated to exactly the headroom, the displaced mass
   flows to `category` (not proportionally to all strategies — the point of the guard is baseline
   breadth), and a `research_log(event_type='quota_allocated', payload.brand_guard_active=true)`
   row is written so the UI can show that the constraint bound.
2. **Category floor.** `w_category ← max(w_category, 0.25)`, with the excess taken pro-rata from
   the other open strategies down to their 0.05 floors. This, not the closed form, is what makes
   the baseline guarantee real.
3. **Exploration floor.** `w_s ← max(w_s, 0.05)` for every gated-open strategy. A strategy the
   bandit has written off can then recover: with ~200 units in a mid-run iteration, 5% is ~10
   items, enough to re-estimate its yield next iteration.
4. Renormalise and repeat from (1) until no constraint is violated.

**Step 4b — admission-time enforcement of the brand bound.** A weight cap bounds *requested*
units; it does not bound *admitted* items, and the §8 guarantee is about the corpus. Collectors
over- and under-return, and a brand query that happens to hit a prolific creator can admit far
more than its quota. The invariant is therefore also enforced at the admission gate: before
admitting a social item whose `first_seen_query_id → strategy = 'brand'`, the gate checks

```
(brand_social_admitted + 1) / (corpus_social + 1) <= 0.30
```

and on failure records the item with `admitted = false`, `reject_reason = 'brand_share_cap'`,
`cu_charged = 0`. The rejections are counted in `coverage_gaps` so the UI can show that brand
coverage was deliberately truncated rather than unavailable. Without this gate the "hard
corpus-level guard" is an aspiration, not a guarantee — which is how an earlier draft of this
spec had it.

**Step 5 — commerce allocation.** Commerce-eligible strategies are
`{category, competitor, theme, brand}`; `hypothesis` becomes eligible only when the hypothesis's
`follow_up_plan` explicitly contains a commerce probe (§17's social→commerce chain is driven by
discovered themes, so most commerce spend should be theme/competitor-driven). Commerce weights =
`normalise(w_s restricted to eligible)`, with `theme` and `competitor` receiving a ×1.3 tilt before
renormalisation, because §17 is explicitly the differentiating capability and its input is
discovered themes and discovered competitors, not the brand name.

### 2.4 Iteration pacing — decaying social, *growing* commerce

Reserve **20%** of each cap as a contingency pool, released only in FOCUSING/STOPPING or when a
hypothesis test needs a decisive sample it cannot otherwise afford. Allocatable = 0.80 × cap.

```
social   : a_i = allocatable_social   · γ_s^i / Σ_{j=i}^{I−1} γ_s^j ,  γ_s = 0.82  (decaying)
commerce : c_i = allocatable_commerce · γ_c^i / Σ_{j=i}^{I−1} γ_c^j ,  γ_c = 1.15  (growing)
```

Recomputed every iteration against *remaining* allocatable and *remaining* iterations, so
under/overspend self-corrects without a separate rebalancing pass.

With `aypo_max_iterations = 8` and the brief §6-consistent split of `aypo_max_collection_units = 4000`
into 1500 social / 2500 commerce:

| i | social | commerce |
|---|---|---|
| 0 | 271 | 146 |
| 1 | 223 | 168 |
| 2 | 183 | 193 |
| 3 | 150 | 221 |
| 4 | 123 | 255 |
| 5 | 100 | 293 |
| 6 | 82 | 337 |
| 7 | 68 | 387 |

- **γ_s = 0.82 (social decays).** Iteration 0 must stand up a cluster structure at all; with
  `min_cluster_size = 8`, ~270 items is roughly 34 × `min_cluster_size`, which is the scale at
  which a non-trivial partition is *geometrically possible* rather than one blob. **How many
  clusters it will actually produce is unknown and must not be assumed** — a prior on the number
  of themes in this subject's content would be a brief §1 violation dressed as a tuning note.
  The claim here is only about resolution, not about structure. Later iterations are *targeted*: 60–100 items is enough to decide a hypothesis
  about a theme that occurs in, say, 15% of content (a 70-item targeted sample yields ~10
  on-theme observations, enough to say "present / not present" with sample size disclosed, which
  is all §28 allows anyway).
- **γ_c = 1.15 (commerce grows).** §17's chain runs *social theme → commerce probe*. At iteration 0
  there are no discovered themes, so commerce querying can only be brand + category baseline;
  146 records is adequate for a price/assortment/brand-presence baseline. Commerce spend should
  ramp exactly as the social side produces things worth checking in commerce.
- **γ_s is depth-dependent, chosen so the last iteration still clears the validity floor.** The
  values above are for `I = 8`. A fixed `γ_s = 0.82` at `I = 14` would put the final iteration at
  23 social units, below the §5.8 floor of 40, so the last three iterations of a `deep` run would
  produce no usable saturation reading at all — the run would spend its deepest iterations unable
  to decide anything. γ_s is therefore solved per run at PLANNING as the largest γ ≤ 0.82
  satisfying `allocatable_social · γ^{I−1} / Σ_{j=0}^{I−1} γ^j ≥ 40`, and if no such γ exists
  (`allocatable_social / I < 40`), `I` is reduced until one does and the reduction is logged as
  `budget_threshold`. For `I = 8` at 1,200 allocatable units the constraint is slack and γ stays
  0.82; for `I = 14` it solves to ≈0.88.
- **Floor:** if remaining allocatable social < 40, do not open a new iteration (go to STOPPING,
  `stop_reason='budget_drain'`). 40 is the §5.8 `valid_reading` floor — an iteration that cannot
  produce a trustworthy saturation reading cannot inform the stop decision, so it is pure spend.
- **Unspent contingency.** On a saturation or target stop the 20% contingency is simply not spent;
  that is intended (the run stopped because more collection had no value). On a pacing or drain
  stop §4.4 releases it into one final targeted iteration instead.

---
## 3. Query generation

### 3.1 The evidence digest (the only content input)

PROPOSE receives a digest assembled from rows, never from prior LLM output held in memory. This is
what makes the step auditable and what keeps the model's priors out of the corpus: the digest is
the *entire* content-bearing context.

```python
# aypo/research/models.py
class EvidenceDigest(BaseModel):
    objective: str                          # operator-supplied
    subject_brand: str                      # operator-supplied
    market_code: str                        # operator-supplied, e.g. ISO country code
    language_codes: list[str]               # operator-supplied
    iteration_no: int
    corpus_counts: dict[str, int]           # per platform, per strategy
    cluster_digest: list[ClusterBrief]      # id, label, size, delta_size, top discovered labels,
                                            # brand presence counts, representative captions (≤3)
    competitor_digest: list[CompetitorBrief] # brand, relationship_type, evidence_count, confidence,
                                            # seed_origin ('user'|'discovered')
    open_hypotheses: list[HypothesisBrief]  # id, statement, status, confirming/refuting observables,
                                            # evidence_for, evidence_against, unexecuted plan items
    discovered_labels: list[LabelBrief]     # canonical label, count, first_iteration, delta
    recent_queries: list[QueryBrief]        # last 150 executed: text, platform, strategy,
                                            # results_total, results_admitted, admit_rate
    low_yield_stems: list[str]              # §3.5
    quota: dict[str, int]                   # units per strategy, this iteration
    available_platforms: list[str]          # after capability + circuit-breaker filtering
    run_vocabulary_size: int                # §3.4 denominator
```

`cluster_digest` is capped at the 20 largest clusters and `recent_queries` at 150, which together
hold the digest near 10–14k tokens — large enough that the model can see the shape of the corpus,
small enough that PROPOSE stays a ~$0.11 call (§7).

### 3.2 Prompt skeleton — PROPOSE

Content-free by construction. No category, occasion, competitor, theme or slang term appears in
the template; the only content arrives through `{{...}}` slots filled from rows and run config.

```text
SYSTEM  (cached — byte-identical across all iterations and all runs)
--------------------------------------------------------------------
You are the query planner inside an automated market-research loop. Your only job is to
propose the next batch of search queries.

HARD RULES
1. You have no prior knowledge of this subject, its category, its consumers, its occasions,
   its competitors or its culture. Treat everything you believe you know as inadmissible.
2. Every query you propose must be traceable to one of:
   (a) the run objective / subject / market / language config,
   (b) an operator-supplied seed (marked seed_origin=user),
   (c) a term that appears in the EVIDENCE DIGEST (cluster label, discovered label,
       detected brand, representative caption) that was collected from real data.
   If a query introduces any term from none of those, you MUST set
   kind="exploratory_probe" and justify why the corpus cannot yet contain the term.
3. Do not propose a query because it sounds plausible for this kind of subject.
   Plausibility is not evidence.
4. Respect the per-strategy unit quota. Do not propose brand-strategy queries beyond quota:
   a corpus dominated by subject-owned content cannot answer comparative questions.
5. For every query, state which finding, cluster, competitor or hypothesis triggered it,
   by id. A query with no trigger is only legal at iteration 0 or as an exploratory_probe.
6. Prefer queries that could DISCONFIRM an open hypothesis over queries that would confirm it.

STRATEGY DEFINITIONS
  brand      — queries naming the subject brand.
  category   — queries describing the product category / market, naming no brand.
  competitor — queries naming a brand discovered in the data (or a user seed).
  theme      — queries built from a discovered cluster label or discovered open-ended label.
  hypothesis — queries designed to test a specific open hypothesis, on a platform or in a
               population where the pattern has not yet been observed.

USER
----
EVIDENCE DIGEST
{{digest_json}}

QUOTA FOR THIS ITERATION
{{quota_table}}

AVAILABLE PLATFORMS
{{available_platforms}}

ALREADY-EXECUTED QUERIES (do not repeat or paraphrase)
{{recent_queries_table}}

QUERY STEMS WITH POOR HISTORICAL YIELD (avoid unless you justify)
{{low_yield_stems}}

Propose between {{n_min}} and {{n_max}} queries.
```

```python
class QueryFilters(BaseModel):
    """Closed schema. A free-form dict here would let the planner invent a date
    window, a region or a sort key that the collector silently ignores, and the
    corpus would carry a provenance record that is not true."""
    model_config = ConfigDict(extra="forbid")
    published_after:  date | None = None   # validated against the run's temporal plan (§3.7)
    published_before: date | None = None
    sort: Literal["relevance","recent","top"] | None = None
    page_depth: int = Field(1, ge=1, le=5)  # §3.7: which slice of the ranking to take
    min_engagement: int | None = Field(None, ge=0)

class ProposedQuery(BaseModel):
    strategy: Literal["brand","category","competitor","theme","hypothesis"]
    kind: Literal["standard","exploratory_probe","replication"]
    platform: str                         # validated against available_platforms, not a Literal:
                                          # the enum must not outlive a provider being removed
    query_text: str
    language_code: str                    # validated ∈ run.language_codes
    rationale: str                        # WHY this query, in plain language, for §32
    triggered_by_kind: Literal["objective","cluster","competitor","hypothesis","finding",
                               "discovered_label","seed","none"]
    triggered_by_ref: str | None          # a DIGEST ORDINAL ("C3", "H7", "K2"), never a UUID
    expected_observable: str | None       # what result would move the trigger's confidence
    disconfirming: bool                   # true if designed to refute, not confirm
    filters: QueryFilters = QueryFilters()

class QueryBatch(BaseModel):
    queries: list[ProposedQuery]
    reasoning_summary: str                # one paragraph, rendered in §32's narrative
```

**What the model is not allowed to produce.** Three fields were removed from an earlier version
of this schema because they asked a language model for a value that code owns:

| Removed | Why | Who computes it now |
|---|---|---|
| `requested_units: int` | the allocator owns the quota; a model-chosen unit count would silently overwrite §2.3's projection, including the brand ceiling | `QuotaAllocator` splits `social_units_i` across the admitted queries of each strategy, evenly, after DEDUPE |
| `triggered_by_id: str` (a UUID) | models transcribe 36-character identifiers wrongly, and a wrong-but-valid id attaches a query to the wrong hypothesis in the §32 audit tree — a silent provenance corruption, which is the one failure brief §9 cannot tolerate | the digest numbers its entities `C1…Cn` (clusters), `H1…Hn` (hypotheses), `K1…Kn` (competitors), `F1…Fn` (findings), `L1…Ln` (labels); DEDUPE maps the ordinal back to the real UUID and **rejects the query** (`reject_reason='bad_trigger_ref'`) if the ordinal is not in the digest it was given |
| free-form `filters: dict` | an invented date window becomes a provenance record that no collector honoured | `QueryFilters`, with `extra="forbid"`, and a post-validation pass that clips the window to the run's temporal plan |

**`strategy` is verified, not trusted.** The strategy label drives budget attribution, the brand
ceiling and every per-strategy statistic in §5. A planner that labels a subject-brand query
`category` defeats the brief §8 guard and contaminates the comparison baseline — the single most
consequential mislabel available. DEDUPE therefore re-derives it mechanically from the query
tokens: a query whose `token_set` contains a `subject_brand` surface form is forced to `brand`; one
containing a known competitor surface form (from `competitor_relationships`, including user seeds)
is forced to `competitor`; otherwise the model's label stands. Overrides are counted in
`research_iterations.queries_rejected.strategy_corrected` and are a planner-quality signal in their
own right.

Called via the existing client: `LLMClient.structured(QueryBatch, system=PROPOSE_SYSTEM,
content=[text_block(user_body)], effort="medium", max_tokens=8000, cache_system=True,
label="research.propose")`. `effort="medium"`: PROPOSE is a planning task over a pre-digested
context, not an analytical one; `high` raised output tokens ~2× in the cost model for no
observable change in query diversity, and the step runs once per iteration so the saving is small
but free. INTERPRET keeps `high`.

### 3.3 Deduplication — lexical AND semantic

Both must pass. Order matters: lexical is free, semantic costs an embedding call.

**Stage 1 — normalisation** (`aypo/research/queries/normalize.py`):
NFKC → casefold → strip diacritics → strip punctuation except `#` and `@` (hashtag and handle
semantics are meaningful on these platforms) → collapse whitespace → drop a stop-word list derived
from the run's `language_codes` → emit `normalized_text` and `token_set` (sorted unique tokens).

**Stage 2 — lexical rejection.** Reject if any of:
- `normalized_text` exactly equals an existing query's, same platform;
- `rapidfuzz.fuzz.token_set_ratio(a, b) ≥ 92` against any existing query on the same platform;
- `jaccard(token_set_a, token_set_b) ≥ 0.80`.

`token_set_ratio ≥ 92` catches reorderings and minor morphological variants; below ~88 it starts
rejecting genuinely different queries that share the category vocabulary, which in a single-domain
corpus is most of the string. Jaccard 0.80 is the independent token-level check that catches cases
where fuzzy ratio is depressed by one long token.

**Stage 3 — semantic rejection.** Embed `normalized_text` with the run's embedding model
(`text-embedding-3-large`, 1536 dims). Reject if
`max cosine against executed queries on the same platform ≥ 0.93`.

**Why 0.93 and not something lower.** Query strings are 3–8 tokens in a single domain, so the
domain vocabulary dominates the vector: two *genuinely different* queries in the same category
are *expected* to sit at cosine 0.80–0.90, while true paraphrases are expected at 0.94–0.98 —
an expectation about short single-domain text, not a measurement (§3.3). A threshold of 0.88
would therefore reject most of the useful variation the loop depends on. 0.93 is deliberately
permissive on the semantic axis because stage 2 already caught the cheap duplicates.
**At the boundary** (0.93 ≤ sim < 0.955) the query is admitted if it carries a *different filter
envelope* — different platform, non-overlapping date window, different sort order — or
`kind="replication"`, since "same probe, different population" is exactly the §8E hypothesis-test
move and must not be dedup'd away. `dup_override_reason` records which exemption applied.
At `sim ≥ 0.955` no exemption applies. (Comparison is same-platform throughout, so cross-platform
replication is never dedup'd away and needs no exemption.)

**Calibration (required before trusting these numbers), and fail-closed until it has run.** At
the end of iteration 0 a job computes the full pairwise cosine matrix over admitted queries and
over the admitted-item embedding matrix, and writes the percentile curves to
`research_runs.calibration jsonb`. Recommended operating point: set the query threshold at the
98th percentile of the observed same-platform pairwise distribution, floored at 0.90 and capped
at 0.96. Until ≥50 queries exist the default 0.93 is used.

**The four embedding thresholds in this document (0.93 query dup, 0.86 content novelty, 0.92 label
merge, 0.80 centroid match) are *unvalidated priors*, not measurements.** No corpus exists yet;
every cosine range quoted as a justification below is an expectation about the geometry of short
single-domain text, and should be read as a hypothesis that the calibration job tests. The
consequence is a hard rule, not a caveat:

> While `research_runs.calibration->>'status'` is not `'calibrated'`, the composite `saturation`
> stop (§6.2 #12) is **disabled**. The run may still stop on budget, clock, operator intent,
> `insufficient_signal`, `source_exhaustion` or `max_iterations`, and its report carries
> `saturation_basis = 'uncalibrated'`. A run must never claim it stopped because it had stopped
> learning on the strength of four numbers nobody has measured.

The same rule applies to any threshold whose calibrated value the job writes back: the stop
predicate reads the calibrated value from `research_runs.calibration`, never the literal in this
document.

**Cost.** ~40 queries/iteration × 8 iterations × ~12 tokens = ~4k embedding tokens per run,
i.e. $0.0005. Dedup is free; do it unsparingly.

### 3.4 Anti-seeding guardrail (`rejected_guardrail`)

Mechanical enforcement of brief §1 / brief §34, run after dedup:

```python
def guardrail(q: ProposedQuery, vocab: RunVocabulary) -> GuardrailVerdict:
    """vocab = tokens from objective, subject, market, language names, user_seeds,
    cluster labels, discovered labels, detected brand names, and the top-20k term
    vocabulary of collected captions/transcripts for this run."""
    novel = {t for t in q.token_set if t not in vocab}
    if not novel:
        return ADMIT
    if q.kind != "exploratory_probe":
        return REJECT("introduces untraceable terms: %s" % sorted(novel))
    if probe_count_this_iteration >= probe_cap(iteration_no, n_admitted):
        return REJECT("exploratory probe cap reached")
    return ADMIT_AS_PROBE
```

`probe_cap(i, n_proposed) = ceil(0.20 · n_proposed) if i == 0 else max(1, floor(0.15 · n_proposed))`.

The denominator is the number of queries **proposed** in this iteration, which is known when
DEDUPE runs. An earlier version of this spec used the number *admitted*, which is not known until
DEDUPE finishes — the cap could not be evaluated while the decisions it governs were being made.

**Iteration 0 is where this design is most exposed to brief §1, and the honest answer is not
"100% probe".** Iteration 0 buys ~271 social items, 23% of the whole social corpus, and its
vocabulary becomes `vocab` for every later iteration — so a model prior injected at iteration 0
is not one query's worth of contamination, it is the seed of the run's entire term space. An
unrestricted licence to invent iteration-0 terms is exactly the failure brief §1 names: the model
would propose the plausible-sounding occasions and contexts it already believes about this kind of
subject, the loop would collect content matching them, and the system would then "discover" them.
Auditability is a mitigation, not a defence — a reviewer reading a list of 23 plausible guesses
cannot un-collect the corpus they produced.

Iteration 0 is therefore constrained, not exempted:

| Iteration-0 query source | Allowed | Counts against the probe cap |
|---|---|---|
| `objective` / `subject_brand` / `market_code` / `language_codes`, verbatim | yes | no |
| translations and transliterations of those into `language_codes` (deterministic, from a lexicon, not invented) | yes | no |
| operator `user_seeds` (brief §23) | yes | no |
| **non-model vocabulary discovery** — marketplace category-tree nodes containing the subject brand, search-engine related-query expansion, platform hashtag co-occurrence on subject-brand results | yes, preferred | no |
| anything else the planner proposes | yes, flagged `prior_derived = true` | **yes, ≤20% of proposed queries and ≤10% of iteration-0 social units** |

The fourth row is the important one: it replaces *guessing* category vocabulary with *retrieving*
it, using affordances the collectors already have (brief §5's "web search and indexed public-page
discovery", and marketplace category pages). It is contracted in §11.3. If no provider exposes any
vocabulary-discovery affordance, the run still proceeds under the 20%/10% cap and the report sets
`seed_dependence = 'high'`, which propagates onto every finding whose evidence chain traces back
to a `prior_derived` query — so a reader can see which conclusions rest on a guess.

Every iteration-0 query is logged with `event_type='seed_probe_set'` and carries
`prior_derived` and its source row above, which makes the *complete set of priors that entered the
run* a single auditable list, separated from the set of terms that were derived from the operator's
own input. That list is the thing a §34 reviewer should read, and the UI (§32) renders it first.

The cap thereafter:

- **15% thereafter.** On a ~40-query iteration that is 6 probes: enough to open genuinely new
  ground, while 85% of the corpus remains data-derived. Uncapped, the model's priors would
  progressively dominate the corpus and the system would "discover" what it already assumed —
  precisely the failure brief §1 names. At the boundary the 7th probe is rejected with
  `reject_reason='probe_cap'` and surfaced in the log, so a reviewer can see the loop *wanted* to
  guess and was stopped.
- **The cap is a cap on *units*, not only on queries.** A probe that happens to be allocated a
  large share of the iteration would defeat a per-query cap. Probe queries share at most
  `probe_cap_fraction` of the iteration's social units, assigned before the even split in §3.2.
- User seeds are in `vocab` and carry `seed_origin='user'` through to
  `competitor_relationships` and findings, so the UI can always distinguish a competitor that was
  handed to the system from one it found (§23).

### 3.5 Low-yield suppression

A *stem* is the 2-token prefix of a `normalized_text` token set. For each stem, maintain
`(results_total, results_admitted)` across the run. A proposed query whose stem has
`results_total ≥ 20 and admitted/total < 0.10` is rejected with `rejected_low_yield` unless the
proposal names a trigger id that is new since the stem's last execution.
`≥20` because an admit rate estimated from fewer results has a standard error over 0.10 and would
suppress a stem on noise; `<0.10` because at that rate a 200-unit quota buys 20 items for a full
search's cost, which is worse than any alternative strategy's observed floor.

### 3.6 Query provenance (why it was generated)

Every `research_queries` row carries, durably:
`strategy`, `kind`, `rationale` (free text, from the model, rendered verbatim in §32),
`triggered_by_kind` + `triggered_by_id` (polymorphic pointer to cluster / competitor / hypothesis /
finding / discovered label), `hypothesis_id` (denormalised FK for the common case),
`parent_query_id` (the query whose results produced the trigger — this is what makes the §32
narrative a *chain* rather than a flat list), `expected_observable`, `disconfirming`,
`log_event_id` (the `research_log` node for this query, which has `parent_event_id` set to the
trigger's log node). The log tree and the query graph are kept consistent by writing both in the
same transaction during DEDUPE.

### 3.7 The sampling design, and the three biases it cannot remove

Nothing below makes this corpus a probability sample. The point of this section is that the loop
must **record the design it actually used**, so that no downstream stage can mistake an artefact of
collection for a property of the market. Three biases are structural:

**(a) Platform-ranking bias.** Every discovery source returns a *ranked* list — by relevance or
by engagement, never at random — and the loop admits from the head of that ranking. The corpus is
therefore systematically over-weighted toward content the platform already promotes. Every
`research_results` row records `raw_rank`, the provider's `sort` and the `page_depth` it came
from; those three fields must be carried into any prevalence statement downstream, and §24's
"within the analysed sample" wording is doing real work.

**(b) Survivorship bias.** Deleted, privatised and geo-blocked items are invisible to search, so
the corpus is a sample of *surviving, currently-public* content. This cannot be corrected, only
disclosed: `sampling_design.survivorship = 'uncorrected'` is written to every run.

**(c) Ranking-head exhaustion masquerading as saturation.** This one the loop *can* and must
address, because it directly corrupts a stop decision. Repeatedly querying one platform with
similar queries re-hits the same highly-ranked items; `dedup_rejection_rate` rises and novelty
falls. Both are exactly the signature of genuine saturation, but the cause is that the loop has
exhausted the *head of a ranking*, not the category. Stopping there and reporting "saturation"
would tell the user the market has no more to show when in fact page 2 was never opened.

The loop separates them with a **diversification probe**, which is a precondition on the composite
stop, not a diagnostic:

```
Before `saturation` may fire, at least one iteration within the stop window must have
executed ≥3 queries carrying a filter envelope not previously used for that query stem —
a deeper page_depth, a different sort, or a non-overlapping date window — and those
queries' own novelty N_probe must also satisfy the novelty threshold.
```

If the diversification probe has not run, the composite stop is withheld and the next iteration's
allocator schedules it (this is cheap: 3 queries out of ~40). If the probe *does* return high
novelty while the main stream returns low novelty, the diagnosis is `rank_exhaustion`, not
`saturation` — a distinct stop reason (§6.1) whose user-facing summary is "this source's ranked
results are used up", which is a true and actionable sentence, where "saturated" would be a false
one.

**(d) Temporal coverage is planned, not hoped for.** Brief §22 permits growth/decline claims only
with sufficient temporal coverage, and brief §6 asks for recency *and* coverage. A corpus
collected entirely from recency-sorted endpoints lands in one narrow window and supports neither.
The loop therefore maintains an explicit temporal plan:

- At PLANNING, `temporal_plan` divides the run's window of interest (default: 12 months back from
  `started_at`, configurable) into monthly strata and sets a target share per stratum — uniform by
  default, because a non-uniform prior would be an assumption about the subject.
- At each ALLOCATE, the realised distribution of `research_results.published_at` over strata is
  compared with the plan. Any stratum under 50% of its target makes the allocator attach
  `published_after` / `published_before` to a share of the next iteration's `category` queries.
- `research_runs.coverage_gaps.temporal` records planned vs achieved per stratum, **including the
  share of items with `published_at IS NULL`**, which on some sources is substantial.
- **If a collector cannot honour a date-ranged search, the gap is recorded and the loop does not
  retry it** — it is a provider capability, not a failure (§11.3 requires collectors to declare
  `supports_date_range`). A run whose corpus concentrates >70% of dated items in a single 30-day
  bucket sets `temporal_coverage = 'insufficient'`, and that flag is what the trend subsystem
  reads before it is permitted to emit a growth or decline claim at all.

None of (a)–(d) is a statistical correction. They are a disclosure contract: the loop knows how it
sampled, and it writes that down so no later stage has to guess.

---

## 4. Budget accounting

### 4.1 The vector

Four dimensions, because they are exhausted independently and the loop must react differently to
each.

```python
# aypo/research/budget.py
@dataclass(frozen=True, slots=True)
class BudgetVector:
    social_units: int = 0
    commerce_units: int = 0
    llm_usd: float = 0.0            # Anthropic only — maps to settings.aypo_max_llm_usd
    other_usd: float = 0.0          # embeddings + transcription + provider results
    wall_seconds: float = 0.0
    # observability detail, not separately capped
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_cache_read_tokens: int = 0
    llm_cache_write_tokens: int = 0
    embedding_tokens: int = 0
    transcription_seconds: float = 0.0
    provider_calls: int = 0
    provider_results: int = 0

@dataclass(frozen=True, slots=True)
class BudgetCaps:
    social_units: int
    commerce_units: int
    llm_usd: float
    other_usd: float
    wall_seconds: float

    @classmethod
    def from_settings(cls, s: Settings, depth: Depth) -> BudgetCaps: ...

@dataclass(frozen=True, slots=True)
class BudgetState:
    caps: BudgetCaps
    spent: BudgetVector
    reserved: BudgetVector          # 'reserved' ledger rows: in-flight, not yet settled

    def utilisation(self) -> dict[str, float]:
        """spent+reserved over cap, per dimension."""
    def pressure(self) -> float:
        """max(utilisation.values()) — the scalar the pacer and guard act on."""
    def guard_level(self) -> GuardLevel: ...
```

`pressure()` is a max, not a mean: a run that has exhausted its wall clock is finished regardless
of how many dollars remain.

**A dimension priced from a guess has no stop authority.** `other_usd` is dominated by the managed
collection line, whose per-result price is not known (§12.1). If a placeholder rate feeds
`pressure()`, a run can be stopped — and the stop reported to the user as `budget_drain` — on a
dollar figure nobody measured. `BudgetState` therefore partitions dimensions:

```python
@property
def binding(self) -> set[str]:
    """Dimensions that may trigger drain/hard_stop. A dimension whose rate source is
    'placeholder' is advisory: it is logged and shown, never acted on."""
```

`social_units`, `commerce_units`, `llm_usd` and `wall_seconds` are always binding.
`other_usd` is binding only when every rate feeding it has `rate_source IN ('measured','invoiced')`.
This loses nothing, because the collection-unit caps already bound the number of provider results
— CU *is* the quantity that drives provider spend — so the real constraint is enforced in the unit
whose value is known. What it buys is that the loop never reports a fictitious reason for
stopping. Separately, a hard **provider spend ceiling** (`aypo_provider_usd_ceiling`, global and
cross-run, defaulting to the collection plan's own monthly cap) is enforced at DISCOVER as a
refusal to enqueue, independent of the per-run budget: that ceiling is a real external limit
rather than an estimate, and exceeding it fails the search, not the run.

### 4.2 Durable ledger

`aypo/llm/pricing.Ledger` already exists and already models cache-read/write rates correctly, but
it is explicitly in-memory and per-run ("not thread-safe by design"). A run that survives a worker
crash cannot rely on it. `aypo/research/budget.py` therefore defines:

```python
class DurableLedger:
    """Wraps pricing.Ledger and persists every line to research_budget_ledger."""
    def __init__(self, run_id: UUID, pool: asyncpg.Pool) -> None: ...

    async def reserve(self, *, iteration_no: int, phase: str, kind: BudgetKind,
                      qty: float, idem_key: str, strategy: str | None = None,
                      query_id: UUID | None = None) -> UUID: ...
    async def settle(self, ledger_id: UUID, *, qty: float, usd: float,
                     detail: dict[str, Any] | None = None) -> None: ...
    async def record(self, spend: Spend, *, iteration_no: int, phase: str,
                     strategy: str | None = None, query_id: UUID | None = None) -> None:
        """Adapter for pricing.Spend lines emitted by LLMClient."""
    async def state(self) -> BudgetState: ...
```

Integration note: `LLMClient.__init__` takes `ledger: Ledger | None` and calls
`self.ledger.record(spend)` **synchronously** from inside `_call`. `DurableLedger` must therefore
*subclass* `pricing.Ledger` and override `record()` — a separate `record_sync` method would never
be called and every LLM line would be lost from the durable ledger. The override appends to the
in-memory `lines` (so `Ledger`'s own properties keep working) and pushes onto an `asyncio.Queue`
drained by a flusher task, with a **mandatory flush at every phase boundary** before the phase's
completion marker is written:

```python
class DurableLedger(Ledger):                      # subclass, not wrapper
    def record(self, spend: Spend) -> Spend:      # sync, called by LLMClient
        super().record(spend)
        self._pending.put_nowait((self._ctx, spend))
        return spend
    async def flush(self) -> None: ...            # awaited at every phase boundary
```

`self._ctx` (run, iteration, phase, strategy, query_id) is set by the phase handler before any
call, because `Spend` itself carries no attribution beyond `label`. A crash can thus lose at most
one phase's worth of LLM attribution *detail*, never any `reserved` row (those are written
synchronously before the call).

### 4.3 Per-iteration attribution

Every ledger row carries `(run_id, iteration_no, phase, strategy, query_id, kind, qty, unit_cost_usd, usd)`.

- **Direct attribution:** search calls and LLM calls made inside a phase carry that phase and, for
  per-query work, the `query_id` and therefore the strategy.
- **Derived attribution (the important one):** retrieval, transcription, vision analysis and
  embedding of an item are charged to the item, and the item's strategy is resolved as
  `research_results.first_seen_query_id → research_queries.strategy`. `first_seen_query_id` is
  immutable: if a later query rediscovers the same item, the item's cost stays attributed to the
  query that actually caused the spend, and the later query's row gets
  `dedup_state='dup_in_run'` with no CU charge. Without this rule, per-strategy yield (§5.5) —
  which drives the bandit — would be systematically biased toward whichever strategy ran last.
- Aggregates come from two plain views, `v_research_spend_by_iteration` and
  `v_research_spend_by_strategy` (§8.6). They are not materialised: the ledger is small, the
  aggregate is an index scan, and a materialised view would have to be refreshed globally at
  every MEASURE.

### 4.4 Pacing — throughput-aware, not nominal

The nominal caps are not the real constraint. **Measured throughput is.** The pacer therefore
derives an *effective* cap each iteration and sizes quotas from that:

```
rate_hat        = EWMA(α=0.4) of items_analysed / analyze_phase_seconds     [items/s]
per_iter_overhead = EWMA(α=0.4) of observed (propose+dedupe+cluster+interpret+measure+decide)
                    seconds, seeded at 360 s        # SEED, not a measurement — see below
usable_wall     = wall_cap − planning_elapsed − synthesis_reserve
                              − (iterations_remaining · per_iter_overhead)
effective_social_cap = min(configured_social_cap,
                           social_spent + floor(rate_hat · usable_wall))
```

Both `rate_hat` and `per_iter_overhead` are **measured by the run, from the run**. The constants
in this document are seeds for the first reading, not observations: no run has executed, so any
number here described as "measured" in an earlier draft was an estimate wearing the wrong label.
They self-correct after iteration 0 and are persisted per iteration so the pacer's own accuracy is
auditable after the fact.

`rate_hat` is seeded by a **calibration probe**: the first 24 retrieved items of iteration 0 are
analysed and timed before the rest of the iteration's analysis is enqueued. Why 24: with
`aypo_worker_concurrency = 4` that is six full waves, enough for queueing transients to average
out. If per-item latency were i.i.d. with mean ~70 s and sd ~40 s, the standard error of the mean
of 24 draws would be ≈8 s (±12% on the rate). It is **not** i.i.d. — items arrive in per-query
batches that share a platform, a media size distribution and a provider queue, so the realised
precision is worse by the design effect of §5.8 and the probe should be read as "the right order
of magnitude", not "±12%". The pacer is robust to that: it is an EWMA that re-estimates every
iteration, and `shrink` is a coarse decision. The probe costs ~$2.

`synthesis_reserve = 720 s (12 min)`. Synthesis is one `effort="max"` pass over cluster and
finding digests plus evidence-link writes; the §7 cost model *projects* ~$1.0 and a few minutes,
so 12 minutes is roughly 3× the projected need with retry margin. Like every other duration here
it is a seed: the first completed synthesis writes its actual duration to
`research_runs.calibration.synthesis_seconds` and subsequent runs use `max(720, 2 × observed_p95)`.

**Graceful finish, not a cut-off.** At DECIDE the pacer projects completion:

```
projected_finish = now + Σ_{j=i+1}^{I−1} (a_j / rate_hat + per_iter_overhead) + synthesis_reserve
if projected_finish > deadline:
    shrink = (deadline − now − synthesis_reserve) / (projected_finish − now − synthesis_reserve)
    if shrink >= 0.50:  scale all remaining a_j by shrink      # keep iterating, smaller
    else:               → STOPPING, stop_reason='wall_clock_pacing'
```

`shrink < 0.50` means every remaining iteration would be less than half its planned size; those
iterations would fall under the §5.8 `valid_reading` floor and so could neither inform the stop
decision nor support a finding. Spending on them is strictly worse than going to synthesis early
with the budget's remaining 20% contingency released into one final targeted iteration.

### 4.5 Guard levels

| Utilisation (max over dimensions) | Level | Action |
|---|---|---|
| < 0.70 | `normal` | — |
| ≥ 0.70 | `advisory` | log `budget_threshold`; raise bandit η 0.6 → 0.9 (exploit known-productive strategies); drop optional platforms (instagram/facebook, §14) from allocation |
| ≥ 0.85 | `constrain` | next-iteration quota ← min(planned, remaining × 0.5); lower INTERPRET effort `high → medium`; cut the `exploratory_probe` cap to 0.05 (not to zero — see below); stop enqueueing comment-level retrieval |
| ≥ 0.95 | `drain` | → STOPPING. No new discovery. Finish analysis only for items already retrieved. |
| ≥ 1.00 | `hard_stop` | cancel outstanding jobs by `(run_id, iteration_no)`; → SYNTHESIZING with `degraded=true` |

**`constrain` vs FOCUSING.** §5.7 raises the probe cap to 0.25 while `constrain` cuts it; by
construction a focus iteration usually happens late, when utilisation is high, so the two
collide. **`constrain` wins on budget, FOCUSING wins on composition**: the effective probe cap is
`min(focus_cap, guard_cap)`, so a focus iteration at `constrain` runs with a 0.05 probe cap and
its forced weight vector intact. Zeroing probes entirely was the earlier rule and it is wrong —
the probe is the cheapest query the loop issues and the only one that can surface something the
corpus has never contained.

**Why drain at 0.95.** The 5% residue must cover synthesis. Against the default
`aypo_max_llm_usd = 120`, 5% is $6 versus a measured synthesis need of ~$1 — a 6× margin. Against
`aypo_max_wallclock_minutes = 180`, 5% is 9 min versus a 12 min reserve, which is why wall clock is
guarded by the *explicit* `synthesis_reserve` subtraction in §4.4 rather than by the percentage
guard alone. At the 0.95 boundary the loop never abandons retrieved-but-unanalysed items: an
analysed item is evidence, an unanalysed one is sunk cost.

---
## 5. Saturation metrics

### 5.1 The object

MEASURE computes one row per iteration. Everything the stop predicate reads comes from this row
and its two predecessors — the predicate never recomputes anything, so a stop decision is exactly
reproducible from the database.

```python
# aypo/research/saturation/view.py
class Avail(StrEnum):
    """Per-family availability. A family that is UNAVAILABLE can never be 'satisfied';
    it is not a zero, not a NULL to be coerced, and not a reason to stop."""
    OK           = "ok"
    INSUFFICIENT = "insufficient"   # computable in principle, not enough data this iteration
    UNAVAILABLE  = "unavailable"    # structurally uncomputable in this run (see §6.2)

@dataclass(frozen=True, slots=True)
class SaturationRow:
    iteration_no: int
    is_focus: bool                   # §5.7 — focus rows feed only the hypothesis family
    valid: bool                      # §5.8
    invalid_reason: str | None
    # per-family availability — the field the stop predicate reads FIRST
    avail: dict[str, Avail]          # keys: novelty, cluster, competitor, yield, hypothesis
    # sampling quality
    items_admitted: int
    n_eff: float                     # §5.8 effective sample size after the design effect
    design_effect: float             # §5.8 deff
    relevant_admit_rate: float | None   # results_relevant / results_total over the iteration
    diversification_probe_ran: bool     # §3.7(c)
    novelty_probe: float | None         # novelty of the diversification probe's own items
    # novelty
    content_novelty: float | None     # NR_i
    label_novelty: float | None       # LN_i
    novelty_index: float | None       # N_i
    novelty_index_mixadj: float | None# N̄_i
    novelty_sentinel: float | None    # §5.2(e) fixed-composition category stream
    novelty_by_strategy: dict[str, float]
    n_eff_by_strategy: dict[str, float]
    label_coverage_gt: float | None   # §5.2(f) Good–Turing: P(next item yields an unseen label)
    dedup_rejection_rate: float       # fraction of discovered results already in corpus
    # cluster structure
    cluster_ami: float | None
    cluster_match_stability: float | None
    cluster_noise_churn: float | None
    cluster_stability: float | None   # CS_i
    n_clusters: int
    n_core_compared: int
    core_fraction: float | None       # |I_core| / |I_both| — §5.3 validity
    # competitor set
    competitor_jaccard: float | None
    competitor_rbo: float | None      # NORMALISED RBO, §5.4 — bounded [0,1], 1.0 for equal lists
    competitor_removals: int          # members of K_{i−1} absent from K_i
    competitor_new_entrants: int      # |K_i \ K_{<i}| — a COUNT, not the rate E_i
    competitor_entrant_rate: float | None   # E_i, diagnostic
    competitor_coverage_gt: float | None    # Good–Turing P(next item reveals an unseen brand)
    competitor_set_size: int
    # yield
    evidence_links_new: int
    units_spent: int
    marginal_evidence_yield: float | None        # MEY_i
    marginal_evidence_yield_rel: float | None    # MEY_i / baseline
    yield_by_strategy: dict[str, float]
    # hypotheses
    hypotheses_new_admissible: int
    hypotheses_resolved: int
    hypotheses_open: int
    hypothesis_generation_rate: float | None     # G_i, per 100 admitted items
    hypothesis_resolution_rate: float | None     # R_i
```

### 5.2 Novelty rate of incoming content

Two notions, because they saturate at very different speeds, and the one that matters for a
research objective is the second.

**(a) Content novelty `NR_i`.** For each item `x` admitted in iteration `i` with embedding `e_x`,
against the corpus `C_{<i}` of items admitted in earlier iterations of the same run:

```
novel(x) = 1  if  max_{y ∈ C_{<i}} cos(e_x, e_y) < τ_novel   else 0
NR_i     = Σ_x novel(x) / |A_i|
τ_novel  = 0.86
```

At `i = 0` the prior corpus is empty, the max over an empty set is undefined, and
`NR_0 = LN_0 = 1.0` **by definition** (everything in the first iteration is new). Stating this
matters: the SQL form below returns no row, and a `None` silently coerced to 0.0 would make
iteration 0 read as perfectly saturated.

**Compute it in process, not with one indexed query per item.** The obvious form —

```sql
-- WRONG at this scale and shape: the join + run filter prevents HNSW from being used,
-- so pgvector post-filters and this degrades to a scan, once per admitted item.
SELECT 1 - (ce.embedding <=> $target) AS sim
  FROM content_embeddings ce
  JOIN research_results rr ON rr.content_item_id = ce.content_item_id
 WHERE rr.run_id = $run_id AND rr.iteration_no < $i AND rr.admitted
 ORDER BY ce.embedding <=> $target
 LIMIT 1;
```

— issues up to 270 filtered ANN queries per iteration and cannot use the index for any of them,
because an HNSW scan cannot be constrained to a run. The corpus is small enough that the honest
implementation is one fetch and one matrix multiply: 1,500 × 1,536 float32 is 9 MB, and
`A @ C.T` for `|A| = 270` is ~0.6 GFLOP — well under a second in numpy, versus hundreds of
round trips.

```python
# one SELECT: embeddings of C_{<i}, pinned to run.embedding_model (§11.6)
C = fetch_matrix(run_id, upto_iteration=i-1)      # (m, d) float32, L2-normalised
A = fetch_matrix(run_id, iteration=i)             # (n, d) float32, L2-normalised
nr = float((A @ C.T).max(axis=1).__lt__(TAU_NOVEL).mean()) if len(C) else 1.0
```

The join must also pin `content_embeddings` to one embedding space; an item with embeddings in
two spaces would otherwise contribute two rows and silently double-count (§11.6).

**Why τ = 0.86.** On `text-embedding-3-large@1536` over the concatenation of caption + transcript +
analysis summary, three regimes are *hypothesised* (not observed — no corpus exists):
exact reposts / stitches / mirrored uploads ≥0.93;
same-template-different-content (a recurring creator format, a duet chain) 0.86–0.92;
substantively different content <0.86. 0.86 therefore counts a template clone as *not novel*,
which is the right call for a research corpus — the fiftieth instance of a format teaches nothing
new. **At the boundary** an item at exactly 0.86 is counted novel (strict `<` for non-novelty),
biasing the metric toward "keep going", which is the safer error: stopping too early loses
discovery, stopping too late only costs money that the budget guard caps anyway. This threshold
is a calibration target (§3.3).

**(b) Label novelty `LN_i`.** Let `U_i` be the set of canonical discovered labels (brief §19B open-ended
fields) emitted by analysis of iteration `i`'s items, after canonicalisation: casefold + strip +
collapse, then semantic merge into an existing canonical label when
`cos(embed(label), embed(canonical)) ≥ 0.92`.

```
LN_i = |U_i \ U_{<i}| / |U_i|        ,  undefined (not 0) when |U_i| = 0
```

`|U_i| = 0` means analysis emitted no discovered labels at all for this iteration — an analysis
failure, not a saturation signal. The novelty family is then `INSUFFICIENT`, never 0.0.

**Why 0.92 for label merge, higher than the 0.86 used for content.** Labels are 1–4 tokens, so
their embeddings have far higher variance per token and sit generally closer together than long
documents; a 0.86 merge threshold would collapse genuinely distinct concepts. 0.92 merges phrasing
and morphological variants while keeping distinct concepts apart. At the boundary a label at 0.92
is merged (so the metric is conservative: merging makes a label look *not* new, pushing toward
saturation) — note this is the opposite bias to τ_novel, and deliberately so: over-merging labels
makes the system under-claim novel concepts, which is the §28-compatible direction.

**(c) Composite.**

```
N_i = 0.40 · NR_i + 0.60 · LN_i
```

Label novelty gets the larger weight because the objective is *conceptual* coverage. You can
always find one more video; you cannot always find one more theme. Content novelty decays slowly
and would keep a saturated run alive; label novelty is the quantity that actually tracks "we have
stopped learning".

**(d) Mix adjustment `N̄_i` — the correction that makes the metric trustworthy.** An iteration
deliberately composed of narrow hypothesis-testing queries *should* return low-novelty content;
that is the query design working, not the corpus being exhausted. So compute `N_{i,s}` per
strategy and recombine using the iteration's **target** weights, not its realised composition:

```
N̄_i = Σ_s p_s(i) · N_{i,s} / Σ_s p_s(i)     over strategies s with n_{i,s} ≥ 15
```

Below `n_eff,{i,s} = 15` a per-strategy novelty estimate is too noisy to recombine (§5.8: at
`n_eff = 15` the standard error of a proportion is ≈0.13 even before the design effect, which is
larger than the gap between the stop threshold and its neighbourhood), so such strategies are
dropped from the recombination rather than allowed to dominate it. **The gate is on effective
sample size `n_eff,{i,s}`, not on the raw item count** (§5.8) — items arrive in per-query batches
and are not independent, so a raw count of 15 can be worth 4 independent observations.
If fewer than two strategies clear it, the novelty family is `INSUFFICIENT` for this iteration.

**What mix adjustment does *not* fix, and why there is a sentinel.** Holding the strategy weights
fixed removes the between-strategy component of the confound. It does not remove the
within-strategy component: a `hypothesis` query at `i = 6` is narrower than a `hypothesis` query
at `i = 2`, because the hypothesis it tests is more specific. Novelty therefore declines partly
because the loop *aimed* more narrowly, and a declining-novelty stop would be reading its own
query design back to itself. That is collection-time selection masquerading as a property of the
corpus, and it is the most dangerous single failure mode in this metric family.

### 5.2 (e) The category sentinel — a fixed-composition stream

A fraction of the `category` quota (**the sentinel share, 0.08 of the iteration's social units,
drawn from inside the 0.25 category floor**) is spent every iteration on queries built by a
**fixed, non-adaptive policy**: deterministic expansion over the run's category vocabulary as it
stood at the end of iteration 0, same platform mix, same sort, same page depth, round-robin over
the stems. The policy does not change as the run learns, so the novelty measured on the
sentinel's items is not confounded by the loop's own narrowing.

```
N_sentinel,i = novelty_index computed over sentinel items only
```

The sentinel is the conjunct that carries the saturation claim; `N̄_i` is retained as the
broader, confounded reading and as a diagnostic. If `n_eff(sentinel) < 15` the novelty family is
`INSUFFICIENT` — the loop may not report saturation on a measurement it did not fund. At ~100
social units per mid-run iteration the sentinel is ~8 items, which does **not** clear 15, so in
practice the sentinel reading is accumulated over a 2-iteration window before use; this is stated
explicitly in §5.8's window table.

### 5.2 (f) Good–Turing label coverage — the statistic that actually answers the question

"Are we still finding new themes?" is a species-discovery question, and `LN_i` is a poor estimator
of it: it measures what fraction of *this* iteration's labels are new, which depends on how many
labels this iteration happened to emit. The quantity the stop decision needs is the probability
that the **next** item reveals a label never seen before, and the Good–Turing coverage estimator
gives it directly from the label frequency spectrum of the run:

```
U_gt,i = f_1 / n_labels          f_1 = canonical labels observed exactly once across the run
                                 n_labels = total label observations across the run
```

`U_gt` is sample-size aware, needs no threshold calibration, and falls monotonically as discovery
genuinely exhausts. It is reported on every row and is the preferred reading once
`n_labels ≥ 200`; below that it is unstable and `N_sentinel` carries the family. Its own
threshold is a calibration target (§12.2), not a number asserted here.

### 5.3 Cluster-structure stability (label-invariant)

Clusters are recomputed over the full corpus each iteration (brief §21: inspect, then label). Labels and
cluster ids are therefore not comparable across iterations; cluster *count* changes too. The
comparison must be label-invariant and must tolerate a growing item set.

**This subsystem owns its own snapshot.** §5.3 needs iteration `N` and `N−1` to coexist. The
clustering subsystem's `cluster_runs_one_per_space` unique index means its own rows do not
(§11.2), and an external one-line index change is not a dependency a stop predicate should rest
on. At CLUSTER, this loop therefore copies the minimum it needs into a table it owns,
`research_cluster_snapshots` (§8.8): `(run_id, iteration_no, item_id, cluster_key,
membership_role)` plus the per-cluster centroid. That is ~1,500 narrow rows and ~12 vectors per
iteration — about 12k rows and 100 vectors for a whole run. Where the clustering subsystem *does*
retain comparable revisions, §5.3 consumes its calibrated `match_cos` / `match_jaccard` /
`match_decision` instead of recomputing them (§11.2); the snapshot is the floor, not a
replacement.

**Restrict to the shared item set.** `I_both` = items present in both the `i−1` and `i` snapshots
(items never leave a corpus, so `I_both` = the `i−1` corpus). `I_core` = items assigned to a
non-noise cluster in **both** snapshots. Items admitted in iteration `i` are intentionally excluded:
this metric asks "did the new evidence reorganise what we already understood?", and new items are
unmatchable by construction. Their contribution is measured by novelty (§5.2) instead, so the two
metrics are complementary rather than redundant.

**Component 1 — AMI.**

```python
from sklearn.metrics import adjusted_mutual_info_score
ami = adjusted_mutual_info_score(labels_prev_core, labels_curr_core, average_method="max")
```

`average_method="max"` (not sklearn's default `"arithmetic"`) because max-normalisation is the most
conservative of the normalisations and reads systematically *lower* on unbalanced cluster sizes,
which is the normal case here. AMI is preferred over ARI because AMI is better behaved when the
two partitions have different cluster counts — HDBSCAN's `k` moves as the corpus grows — whereas
ARI is biased toward partitions with similarly sized clusters.

**Component 2 — noise churn (reported, never folded in).**

```
ν_i = |{x ∈ I_both : noise in exactly one of the two snapshots}| / |I_both|
```

Kept separate and asserted as its own conjunct (`ν_i ≤ 0.15`) rather than averaged into the
composite. Folding a large NOISE pseudo-cluster into AMI would let it dominate the statistic; and a
corpus that is quietly reorganising *through* the noise bucket must not be able to report itself
stable. 0.15 because HDBSCAN noise assignment near a cluster boundary is genuinely unstable for a
small fraction of points when 20–30% new data arrives; a shift above 15% of the shared corpus
means the density structure itself moved.

**Component 3 — centroid matching stability (label-invariant, and it is what §32 renders).**

```python
# aypo/research/saturation/stability.py
from scipy.optimize import linear_sum_assignment
import numpy as np

def match_clusters(prev: Snapshot, curr: Snapshot, core: set[int]
                   ) -> tuple[list[ClusterMatch], float]:
    # cost matrix over centroids (unit-normalised vectors → cosine = dot)
    cos = prev.centroids @ curr.centroids.T            # (P, Q)
    rows, cols = linear_sum_assignment(1.0 - cos)      # optimal, handles P != Q
    matches, carried = [], 0
    for a, b in zip(rows, cols, strict=False):
        jac = jaccard(prev.members[a] & core, curr.members[b] & core)
        accepted = cos[a, b] >= 0.80 and jac >= 0.40
        matches.append(ClusterMatch(a, b, float(cos[a, b]), jac, accepted))
        if accepted:
            carried += min(len(prev.members[a] & core), len(curr.members[b] & core))
    return matches, carried / max(len(core), 1)
```

- **cos ≥ 0.80.** The reasoning is that centroids of *distinct* clusters inside a single-domain
  corpus sit around 0.55–0.75 while the *same* theme's centroid moves by well under 0.15 cosine
  when 20–30% new members are added, so 0.80 falls inside the gap. Both figures are expectations
  about embedding geometry, not measurements, and the calibration job (§3.3) is specified to
  replace them with the observed centroid-distance distribution. Where the clustering subsystem
  has its own calibrated `tau_match`, that value wins (§11.2). At the boundary the match is rejected, i.e. the cluster is
  treated as not carried forward — again the "keep going" bias.
- **Jaccard ≥ 0.40.** With ~25% corpus growth per iteration, a genuinely persisting cluster retains
  60%+ of its old members. 0.40 tolerates a cluster splitting roughly in half while still counting
  the larger half as continuous — which is the correct reading: a split theme is an *evolving*
  structure, not a stable one, but it is not a new one either.
- The returned `ClusterMatch` list is persisted (`research_saturation.cluster_matches jsonb`) and is
  exactly what lets the §32 view draw "Cluster Y persisted / split / merged / dissolved" arrows.

**Composite.**

```
CS_i = 0.5 · AMI_i + 0.5 · S_match,i
```

Equal weights because the two measure different failure modes and neither dominates: AMI catches
fine-grained reassignment that leaves cluster shapes intact, `S_match` catches wholesale
splitting/merging that AMI can score surprisingly well.

**Validity.** The cluster family is `INSUFFICIENT` unless **all** of:

- `|I_core| ≥ 50` — below 50 co-assigned points AMI's sampling variability is comparable to its
  own usable range;
- both snapshots have `≥2` non-noise clusters — with one cluster the statistic is degenerate;
- **`core_fraction = |I_core| / |I_both| ≥ 0.50`.** This one is a selection guard, not a power
  guard. `I_core` is defined by conditioning on membership of a cluster in *both* snapshots, i.e.
  on the very stability being measured. If only a third of the shared corpus is clustered in both,
  AMI is computed on a hand-picked stable core and will read high almost regardless of what
  happened to the other two thirds. `noise_churn` catches items that moved *between* noise and a
  cluster; `core_fraction` catches the complementary case of a corpus that is largely noise in
  both snapshots, where neither statistic has anything to say.
- the iteration is not a focus iteration (§5.7).

The conditioning bias above is intrinsic even when `core_fraction` is high: `CS_i` is the
stability *of the part of the corpus that stayed clustered*, and should be read that way. It is
the correct question for "did the new evidence reorganise what we already understood?", and it is
not a statement about the corpus as a whole.

### 5.4 Competitor-set stability

`K_i` = brands with `confidence ≥ 0.50 ∧ evidence_count ≥ 3` in `competitor_relationships` as of
iteration `i`, ordered descending by `evidence_count`.

```
J_i      = |K_i ∩ K_{i−1}| / |K_i ∪ K_{i−1}|
A_d      = |K_i[:d] ∩ K_{i−1}[:d]| / d                    # agreement at depth d
RBO_i    = Σ_{d=1}^{D} p^{d−1} · A_d / Σ_{d=1}^{D} p^{d−1} ,  p = 0.90,
           D = max(|K_i|, |K_{i−1}|)                      # DEPTH-NORMALISED
entrants_i = |K_i \ K_{<i}|                               # count
removals_i = |K_{i−1} \ K_i|                              # count
E_i      = entrants_i / max(|K_i|, 1)                     # rate, diagnostic only
```

**The normalisation is not cosmetic — without it the predicate is unsatisfiable.** The textbook
truncated form, `(1 − p) · Σ_{d=1}^{D} p^{d−1} A_d`, is the prefix of a series that converges to 1
only as `D → ∞`. At finite `D` it is bounded by `1 − p^D`, so for two **identical** lists of 8
brands it returns 0.5695, not 1.0. An earlier version of this spec used exactly that form together
with `CompS = 0.5·J + 0.5·RBO ≥ 0.85`, which caps `CompS` at `0.5 + 0.5·0.5695 = 0.785` for a set
of 8. The conjunct could never be satisfied for any competitor set of 11 brands or fewer — i.e.
for the whole range this design says to expect — and therefore the **composite saturation stop
could never fire at all**. Dividing by `Σ p^{d−1}` makes RBO a geometrically weighted mean of the
agreements, which is 1.0 for equal lists, bounded in [0,1], and still top-weighted.

- **RBO rather than Spearman** because the two lists are non-conjoint (brands enter and leave),
  which rank correlations handle badly, and because it is top-weighted — the identity of the
  leading competitors matters far more than the tail ordering. **p = 0.90** concentrates ~86% of
  the (infinite-series) weight on the top 10 ranks and ~97% on the top 22. An earlier draft said
  65%/90%; those figures are wrong.
- **The predicate is stated directly, not encoded in a scalar threshold.** What this design means
  by a stable competitor set is "nothing new appeared, at most one marginal brand dropped out, and
  the leading order did not move". A single `CompS ≥ θ` cannot express that, because the value of
  a one-removal change depends on `|K|`. With the corrected RBO, `CompS` for one removal from a
  set of 8 is 0.932 and for two removals 0.858; from a set of 5, *one* removal already scores
  0.884; from a set of 12, *two* removals score 0.911. A threshold of 0.85 therefore permits two
  removals from a set of 8 and three from a set of 12, while a threshold of 0.90 forbids even one
  from a set of 5. No fixed θ means "at most one removal". The conjunct is therefore stated as
  counts, with RBO retained only for the case the counts cannot see (same members, new order —
  two removals from a set of 8 leave RBO at 0.966, so RBO alone would not have caught them
  either):

  ```
  competitor_stable_i  ⇔  entrants_i == 0  ∧  removals_i <= 1  ∧  RBO_i >= 0.90
  ```

  `entrants == 0` is the substantive condition (brief §23 makes competitor discovery open-ended, so
  any entrant means discovery is still live). `removals <= 1` tolerates exactly one marginal brand
  falling back below the confidence gate. `RBO ≥ 0.90` catches the case the counts miss: the same
  brands, reordered. `J_i` and `CompS_i = 0.5·J_i + 0.5·RBO_i` are still computed and persisted as
  human-readable diagnostics; they are not stop inputs.
- `entrants_i` uses `K_{<i}` (ever-seen), not `K_{i−1}`, so a brand that flickers out and back is
  not counted as a new discovery.
- **A cumulative set is nearly stable by construction, so "no entrants" needs an exposure
  denominator.** `competitor_relationships.evidence_count` only grows, so membership of `K` is
  close to monotone and `J`/`RBO` drift toward 1 whether or not discovery is finished. Two
  iterations with no entrants after a 60-item iteration is a far weaker statement than the same
  after a 270-item iteration. The row therefore also carries the Good–Turing coverage of the brand
  frequency spectrum —

  ```
  U_brand,i = (brands detected exactly once across the run) / (total brand detections)
  ```

  — which estimates the probability that the next admitted item reveals a brand never seen before.
  It is sample-size aware where an entrant count is not, and it is the figure the §32 view should
  show next to "competitor set stable". Its threshold is a calibration target (§12.2).

### 5.5 Marginal evidence yield

The quantity the loop is really optimising: how much *usable evidence* each collection unit buys.

**Evidence link.** A row in `finding_evidence`, or a `hypothesis_evidence` row, created in
iteration `i` and pointing at a `content_item` or `commerce_item`.

```
# cohort-lagged: numerator and denominator describe the SAME items
MEY_i     = |{items admitted in iteration i−1 that are evidence-linked by the end of i}|
            / units_spent_{i−1}
              where units_spent_j = social_j + ρ_commerce · commerce_j      (§2.2)
MEY_rel,i = MEY_i / baseline,   baseline = median(MEY over the first 3 computable iterations)
```

- **Why cohort-lagged.** The earlier form counted *links created during iteration i* over *units
  spent during iteration i*. Those are different populations: a link created now usually points at
  an item bought two iterations ago, so the ratio mixes a numerator drawn from the whole corpus
  with a denominator drawn from one iteration. As the corpus grows the numerator grows with it
  and the measure stops describing marginal productivity at all; it can also exceed 1 and move
  non-monotonically for reasons that have nothing to do with collection. Attributing a cohort's
  links to that cohort's spend, with one iteration of lag for the links to be created, makes the
  numerator and denominator the same items. The one-iteration lag means `MEY` is first computable
  at `i = 1` and the stop window shifts accordingly (§5.8).
- **Why the baseline is a median, not a max.** `max(MEY_0, MEY_1, MEY_2)` is the maximum of three
  noisy readings and is therefore an upward-biased estimate of early yield, which makes `MEY_rel`
  systematically *small* and the `≤ 0.20` conjunct systematically *easy* — a bias toward stopping,
  and the opposite of the "keep going" bias this document claims everywhere else. The median of
  the first three computable readings is unbiased under symmetric noise and still discards a
  pathological iteration-0 reading.
- **Why relative, not absolute.** The absolute evidence density of a market is unknowable in
  advance and brief §1 forbids assuming it. Normalising to the run's own early iterations makes the
  metric self-calibrating.
- **Degenerate case.** If the baseline is 0 after three computable iterations, `MEY_rel` is
  undefined; the yield family is `UNAVAILABLE` and the run raises `insufficient_signal` (§6)
  rather than reporting saturation — a different and more honest diagnosis.
- **If per-iteration evidence links do not exist, the family is UNAVAILABLE — not zero.** Doc 07
  (`docs/design/07-evidence-and-findings.md`) describes finding generation as a validation
  pipeline and never mentions an iteration; `finding_evidence` carries no `created_iteration`
  today. If the intelligence subsystem does not expose §11.9's per-iteration link, `MEY` cannot be
  computed, and the one thing the loop must not do is read "no links yet" as "spending has stopped
  producing evidence" and stop. §11.10 therefore defines a weaker but *available* substitute —
  **candidate evidence**: an admitted item that is relevant, assigned to a non-noise cluster, and
  contributed at least one discovered label that survived canonicalisation. That substitute is the
  default numerator; `metric_basis='candidate_evidence'` is recorded on the row and surfaced in
  `stop_detail`, because a stop justified by candidate evidence is a weaker claim than one
  justified by real finding links and the report must say which it was.
- **Two attributions, both kept.** `MEY_i` as above drives the stop decision. A separate
  **retrospective cohort yield** — of items admitted in iteration `i`, the fraction *ever*
  evidence-linked — is recomputed at SYNTHESIZING, because an item collected in iteration 2 that
  only becomes evidence in iteration 5 should credit the strategy that found it.
- **Per-strategy:** `MEY_{i,s}` restricts the cohort to items whose
  `first_seen_query_id → strategy = s`, over that strategy's units. This is the bandit's input,
  shrunk by §2.3 step 3 before use.

### 5.6 Hypothesis productivity

A hypothesis counts only if **admissible**: it has a `statement`, a `reason`, ≥1 confirming
observable, ≥1 refuting observable, and ≥1 `follow_up_plan` item that survives §3.3 dedup and
§3.4 guardrail. Without admissibility the metric is trivially gameable — a model asked "are you
still productive?" will always produce more sentences.

```
G_i = admissible_hypotheses_created_i / max(items_admitted_i / 100, 0.01)   # per 100 items
R_i = hypotheses_resolved_i / max(hypotheses_open_{i−1}, 1)                 # terminal transitions
```

- **Unproductive ⇔ `G_i ≤ 0.5` for two consecutive valid iterations.** A healthy iteration 0
  typically yields 4–10 admissible hypotheses from ~270 items, i.e. `G ≈ 1.5–3.7`. A fall to ≤0.5
  is a ≥3× decline and, in absolute terms, means a 100-item iteration produces at most half a
  question worth asking — the model is looking at new data and finding nothing to investigate.
- **`hypothesis_starvation`** (`hypotheses_open = 0 ∧ G_i = 0`) is a separate, stronger condition:
  the loop has nothing to test and is not generating anything to test. **It requires at least
  three completed valid iterations and must hold on two consecutive valid readings.** Without
  those guards the condition fires at the end of iteration 0 of a perfectly healthy run — a first
  INTERPRET pass over a 270-item corpus that has not yet been clustered into ≥3 themes may
  legitimately open no hypothesis, and `hypotheses_open = 0 ∧ G_0 = 0` would then stop the run
  after a single iteration with the reason "nothing left to investigate". That was a live defect
  in an earlier version of this spec and it is the cheapest possible way to destroy a run.
- `R_i` is diagnostic, not a stop input. A low `R_i` with a healthy `G_i` means the loop is
  generating faster than it can test, which the §2.3 prior already corrects by ramping the
  `hypothesis` strategy; using `R_i` as a stop input would reward a loop that stops asking
  questions.
- **If INTERPRET was degraded** (LLM refusal/fallback, §9.4), `G_i` is written as **NULL**, not 0,
  and the iteration is excluded from the two-iteration window. An LLM outage must not be able to
  manufacture a saturation stop.

### 5.7 Partial saturation → FOCUSING

If 3 or 4 of the 5 metric families satisfy their thresholds, the run enters FOCUSING rather than
stopping. One focus iteration:

- quota ← 0.6 × the scheduled `a_i`, drawn from the 20% contingency pool if the regular allocation
  is spent;
- strategy weights forced to `hypothesis 0.45 / competitor 0.25 / theme 0.20 / category 0.10 /
  brand 0.00`, bypassing the bandit;
- `exploratory_probe` cap raised from 15% to 25%, because the purpose of a focus iteration is to
  make one last attempt to find the thing that is missing;
- only `disconfirming=true` queries are admitted for the `hypothesis` strategy.

**A focus iteration is not a measurement of the corpus and must not be treated as one.** Its
composition is forced and narrow by design, so it mechanically depresses novelty and inflates
stability — exactly the confound §5.2(d) exists to prevent. At 0.6 × a late quota it also cannot
clear the per-strategy `n_eff ≥ 15` gate for more than one strategy, so its novelty reading would
be `INSUFFICIENT` anyway. An earlier version of this spec said the run "returns to ITERATING if
the focus iteration breaks saturation", which it could never do: the metrics that would have to
move are not computable on a focus iteration. The rule is therefore:

- a focus row is written with `is_focus = true` and is **excluded from the novelty, cluster and
  yield windows** (§5.8). It contributes only to the hypothesis and competitor families, which are
  the things it was funded to move;
- the run returns to ITERATING if the focus iteration produced **≥1 new admissible hypothesis, or
  ≥1 competitor entrant, or ≥1 disconfirming observation against an open hypothesis** — concrete
  products, observable on the focus row itself, rather than a threshold crossing on a metric the
  iteration cannot measure;
- otherwise the next DECIDE stops with the composite reason, evaluated on the pre-focus window;
- **at most 2 focus iterations per run** (`research_runs.focus_iterations_used`). Without a cap,
  focus → ITERATING → partial saturation → focus is a cycle that spends the contingency pool one
  0.6× iteration at a time and ends on `budget_drain` with a confusing history.

This is the "finishes gracefully instead of being cut off" behaviour: the run spends its last
units on its highest-value question rather than on more breadth it has already shown it does not
need.

### 5.8 Smoothing, windows, and why single readings are untrustworthy

A single-iteration reading of any of these metrics is unreliable for four independent reasons:

1. **Sampling error, against an effective `n` that is much smaller than the item count.** This is
   the correction that matters most, and an earlier version of this spec got it wrong by quoting
   plain binomial standard errors. The items in an iteration are **not independent draws**: they
   arrive in per-query batches from a single ranked result list, they repeat creators, and they
   repeat formats (duets, stitches, re-uploads of one original). Novelty, relevance and
   label-emission are all strongly correlated inside a query and inside a creator. The right
   quantity is the effective sample size under a one-stage cluster design:

   ```
   deff_i   = 1 + (m̄_i − 1) · ρ̂_i        m̄ = mean items per cluster (query, then creator)
   n_eff,i  = |A_i| / deff_i              ρ̂   = intra-class correlation of the measured
                                                indicator, estimated from this iteration's
                                                own one-way ANOVA across its query batches
   ```

   `ρ̂` is **estimated, not assumed** — it is computable from the data the iteration already has,
   and it is persisted on the row so a reader can see how much the nominal `n` was discounted.
   A worked intuition for the scale of the problem: 70 items from 6 queries with `ρ̂ = 0.3` give
   `deff = 1 + (11.7 − 1)(0.3) = 4.2` and `n_eff ≈ 17`, so the standard error of a proportion is
   0.12, not the 0.06 a naive binomial calculation would give. **Every `n ≥ 15`, `n ≥ 40` and
   `n ≥ 50` threshold in §5 is a threshold on `n_eff`.**

   Two mechanical caps keep `deff` bounded rather than merely measured, because an unbounded
   design effect cannot be fixed after the fact: **no single query may contribute more than 25% of
   an iteration's admitted social items, and no single creator more than 10%.** Items beyond the
   cap are recorded with `reject_reason='concentration_cap'` and `cu_charged = 0`. This also
   directly serves brief §6's "prioritise diversity".
2. **Query-mix confounding.** An iteration's composition is chosen by the allocator, so a narrow
   iteration mechanically depresses novelty and inflates stability. §5.2(d)'s mix adjustment
   removes the between-strategy part, §5.2(e)'s sentinel removes the within-strategy part, and
   windowing covers the residue.
3. **Repeated evaluation.** The stop stack is evaluated at every iteration, so a conjunction of
   seven noisy conditions gets up to eight chances to be satisfied by luck, and
   `satisfied_families() ≥ 3` gets eight chances to trigger a focus iteration. The windows below
   (min-of-2, median-of-3, "must hold in each of the last 2") are the multiplicity control: each
   one requires a condition to persist rather than to occur, which is what turns eight
   opportunities into roughly two independent ones. This is a blunt instrument and it is
   deliberately paired with `n_valid ≥ 4`; no correction here is exact, and the honest reading of
   a composite saturation stop is "four independent families agreed, twice each", not a p-value.
4. **Algorithmic jitter.** HDBSCAN is deterministic for fixed inputs and parameters, but the
   parameters are re-selected per run and the input set grows, so the partition can move for
   reasons unrelated to the corpus. The jitter floor is **not known** for this corpus; §12.2 lists
   measuring it (re-run the iteration's clustering under `min_samples ± 1` and record the AMI
   spread into `research_runs.calibration.ami_jitter`) as a prerequisite for trusting the 0.75
   threshold. Until it is measured, treat any stability reading within 0.05 of its threshold as
   uninformative.

Focus rows (`is_focus = true`) are excluded from the novelty, cluster and yield windows (§5.7).

| Metric | Smoothing | Window | Why this window |
|---|---|---|---|
| `N_sentinel` novelty (the stop input) | pooled over the last 2 valid iterations, then EWMA α = 0.5 | 2 valid iterations pooled, ≥3 before actionable | the sentinel is ~8 items per iteration, so a single iteration cannot clear `n_eff ≥ 15`; pooling two iterations of a *fixed-composition* stream is legitimate precisely because its composition does not change |
| `N̄` mix-adjusted novelty (diagnostic) | EWMA α = 0.5 | ≥3 valid iterations | α = 0.5 gives a half-life of one iteration — responsive enough to notice a genuinely novel iteration, damped enough that one unlucky sample cannot trip the stop |
| `CS` cluster stability | **min of last 2** (not averaged) | 2 | Stability is already a differential quantity; averaging differentials hides oscillation. `min` means both of the last two comparisons must be stable |
| `ν` noise churn | max of last 2 | 2 | Same reasoning, worst-case direction |
| `RBO` competitor order | min of last 2 | 2 | A set that was stable, churned, and re-stabilised is not stable |
| `entrants`, `removals` | counts; `entrants == 0` and `removals ≤ 1` in **each** of last 2 | 2 | Discrete counts; averaging is meaningless |
| `MEY_rel` | **median of last 3** | 3, offset by the §5.5 cohort lag | Yield is the noisiest of the five (it depends on downstream finding generation, which is lumpy); a median of 3 discards a single outlier in either direction |
| `dedup_rejection_rate` | min of last 2 | 2 | Used only by `repetition`; `min` means both of the last two iterations must have been highly repetitive |
| `G` hypothesis rate | raw, must hold in each of last 2 | 2 | Small integer counts; NULLs excluded, not zero-filled |

**`valid_reading` predicate.** A row has `valid = false` (and is excluded from every window) when
any of:

```python
valid = (
    n_eff_i >= 15                       # §5.8(1): EFFECTIVE, after the design effect
    and items_admitted_i >= max(40, 0.5 * quota_social_i)
    and not iteration_degraded          # phase deadline partial, provider quarantine, LLM fallback
    and analysis_completion_rate >= 0.60
)
```

`valid` is a property of the *iteration*. It is necessary but not sufficient for any family: a
valid iteration can still have an `INSUFFICIENT` or `UNAVAILABLE` family (§5.1 `avail`), and the
stop predicate reads `avail` first. Conflating the two was how an earlier version of this spec
could have let a NULL become an implicit zero.

- **40 items minimum.** Below ~40 new items against a 400+ corpus, new data is under 10% of the
  corpus and structural change is below the AMI jitter floor. Such an iteration cannot *in
  principle* detect instability, so reporting it as stable is a false negative by construction.
- **≥50% of quota.** An iteration that collected a third of what it planned did so because
  something failed; its composition is biased toward whatever still worked.
- **`analysis_completion_rate ≥ 0.60`.** When a phase deadline truncates analysis, the completed
  subset is biased toward *fast* items — short videos, cached pages, small files. Measuring
  novelty or stability on that subset measures the bias, not the corpus.

Minimum iterations before the composite can fire: **5**, raised from 4. Three of the five
families need a 2-iteration window; iteration 0 produces no stability reading at all; and
`MEY_rel` now needs a 3-reading median *plus* a 3-reading baseline *plus* the §5.5 one-iteration
cohort lag, so its first usable median lands one iteration later than the earlier spec assumed.
Five completed valid iterations is the earliest point at which all five families have a
trustworthy value. At `max_iterations = 8` that leaves room for a composite stop at iterations
5–7, which is the intended behaviour: with eight iterations available, a saturation stop should be
possible but should not be the common case.

---

## 6. Stopping conditions

### 6.1 The enum

```sql
CREATE TYPE research_stop_reason AS ENUM (
  'user_cancelled',
  'fatal_error',
  'budget_hard_stop',
  'wall_clock_exhausted',
  'wall_clock_pacing',
  'budget_drain',
  'content_target_reached',
  'user_depth_reached',
  'max_iterations_reached',
  'saturation',
  'rank_exhaustion',            -- §3.7(c): the ranked head is used up, the category is not
  'repetition',
  'hypothesis_starvation',
  'insufficient_signal',
  'source_exhaustion',
  'repeated_iteration_failure'
);
```

`research_runs.saturation_basis text` accompanies the reason and records *why the composite was
or was not available*: `'full'` (all five families OK and calibrated), `'uncalibrated'`
(§3.3 fail-closed), or a comma-separated list of the families that were `UNAVAILABLE`. A run that
ends on `max_iterations_reached` with
`saturation_basis = 'cluster_unavailable,yield_unavailable'` tells the operator something true and
useful; one that ends on `max_iterations_reached` with no explanation does not.

Every one of brief §26's listed conditions maps onto this set: *research budget exhausted* →
`budget_hard_stop` / `budget_drain`; *configured content count reached* →
`content_target_reached`; *new searches produce highly repetitive results* → `repetition`;
*cluster structure stabilises* and *competitor set stabilises* and *no meaningful new hypotheses*
and *marginal new evidence becomes low* → conjuncts of `saturation`, each also observable
individually in `research_saturation`; *user-specified research depth reached* →
`user_depth_reached`.

### 6.2 Predicates, in precedence order

Evaluated top to bottom at DECIDE; **first match wins** and is persisted. `sat` is a
`SaturationWindow` holding the last three **valid, non-focus** rows with the §5.8 smoothing
already applied; `bud.pressure()` is the max over the *binding* dimensions only (§4.1).

**The NULL policy, stated before the code because it is the thing most likely to be got wrong.**
Every smoothed value below is `float | None`. Python's `None <= 0.15` raises `TypeError`, and any
coercion of `None` to a number is a silent lie in one direction or the other. The rule is:

```python
def holds(value: float | None, cmp: Callable[[float], bool]) -> bool:
    """An unavailable reading NEVER satisfies a stop conjunct."""
    return value is not None and cmp(value)
```

and, above it, the family rule: a family whose `avail` is `INSUFFICIENT` or `UNAVAILABLE` is not
satisfied, is not counted by `satisfied_families()`, and blocks the composite outright. The
composite requires **all five families `OK`**. This is the direct consequence of §9.4's governing
rule — a reasoning failure, a provider outage or a missing upstream contract must never be able to
manufacture a saturation stop — extended from the LLM case to every source of a missing number.

`satisfied_families()` maps conjuncts to families explicitly, so the 3-of-5 focus trigger is not
counting conjuncts:

| Family | Satisfied when |
|---|---|
| `novelty` | `avail=OK` ∧ `sentinel_novelty_ewma ≤ 0.15` ∧ `diversification_probe_ran` ∧ `novelty_probe ≤ 0.15` |
| `cluster` | `avail=OK` ∧ `cluster_stability_min2 ≥ 0.75` ∧ `noise_churn_max2 ≤ 0.15` |
| `competitor` | `avail=OK` ∧ `entrants == 0` ∧ `removals ≤ 1` ∧ `rbo_min2 ≥ 0.90`, in each of the last 2 |
| `yield` | `avail=OK` ∧ `marginal_yield_rel_median3 ≤ 0.20` |
| `hypothesis` | `avail=OK` ∧ `hypothesis_generation_max2 ≤ 0.50` |

```python
# aypo/research/stopping.py
def evaluate(run: Run, bud: BudgetState, sat: SaturationWindow,
             health: ProviderHealth) -> StopDecision:

    #  1 — operator intent, always first
    if run.cancel_requested:
        return stop("user_cancelled")

    #  2 — unrecoverable
    if run.fatal_error is not None:
        return stop("fatal_error")
    if run.consecutive_abandoned_iterations >= 2:
        return stop("repeated_iteration_failure")

    #  3/4/5 — budget and clock, before any quality reasoning:
    #          a stop you cannot afford to postpone is not a decision
    if bud.pressure() >= 1.00:
        return stop("budget_hard_stop")
    if bud.wall_remaining() <= SYNTHESIS_RESERVE_S:
        return stop("wall_clock_exhausted")
    if bud.pressure() >= 0.95:
        return stop("budget_drain")
    if bud.projected_shrink() < 0.50:
        return stop("wall_clock_pacing")

    #  6 — the operator asked for N items and got them.
    #      Targets are the PLANNING-rescaled ones (§9.2): a channel with no configured
    #      provider has target 0, so an unreachable commerce target cannot make this
    #      condition permanently false.
    if (run.corpus_social >= run.target_social_effective
            and run.corpus_commerce >= run.target_commerce_effective):
        return stop("content_target_reached")

    #  7 — depth / iteration ceiling. ONE number, resolved at PLANNING:
    #      run.max_iterations = min(settings.aypo_max_iterations, DEPTH_ITERATIONS[run.depth])
    #      and run.iteration_ceiling_source records which bound won, so the reason is honest.
    if run.iterations_completed >= run.max_iterations:
        return stop("user_depth_reached" if run.iteration_ceiling_source == "depth"
                    else "max_iterations_reached")

    #  8 — the run cannot work at all (early, honest failure)
    if (run.iterations_completed >= 3
            and holds(sat.relevant_admit_rate_mean3, lambda v: v < 0.15)
            and sat.latest.n_clusters < 2):
        return stop("insufficient_signal")

    #  9 — every discovery provider has been unusable for 2 iterations
    if health.all_discovery_down_for_iterations >= 2:
        return stop("source_exhaustion")

    # 10 — nothing left to investigate. Guarded: see §5.6. Without the guards this
    #      fires at the end of iteration 0 of a healthy run.
    if (sat.n_valid >= 3
            and sat.hypotheses_open_max2 == 0
            and sat.hypothesis_generation_all2_zero):
        return stop("hypothesis_starvation")

    # 11 — the ranked head is used up, which is NOT the same as the category being
    #      exhausted (§3.7c). Checked BEFORE repetition and saturation because it is
    #      the more specific and more actionable diagnosis of the same symptoms.
    if (sat.n_valid >= 2
            and sat.dedup_rejection_rate_min2 >= 0.70
            and sat.diversification_probe_ran
            and holds(sat.novelty_probe_latest, lambda v: v > 0.30)):
        return stop("rank_exhaustion")

    # 12 — the corpus has gone circular (brief §26 "highly repetitive results")
    if (sat.n_valid >= 2
            and sat.families["novelty"] is Avail.OK
            and holds(sat.sentinel_novelty_ewma, lambda v: v <= 0.08)
            and holds(sat.dedup_rejection_rate_min2, lambda v: v >= 0.70)):
        return stop("repetition")

    # 13 — the composite. Every family must be OK; no NULL may pass as a zero.
    if (sat.n_valid >= 5
            and run.calibration_status == "calibrated"          # §3.3 fail-closed
            and sat.all_families_available()
            and sat.satisfied_families() == 5):
        return stop("saturation")

    # 14 — partial saturation: one focused iteration instead of stopping.
    #      Capped at 2 per run (§5.7); a family that is UNAVAILABLE is not satisfied,
    #      so an unavailable family cannot push the count to 3.
    if (sat.n_valid >= 5
            and run.focus_iterations_used < 2
            and 3 <= sat.satisfied_families() <= 4):
        return focus()

    return cont()
```

**When the composite can never become available.** If a family is `UNAVAILABLE` for structural
reasons — no per-iteration evidence links, no coexisting cluster snapshots, calibration never run
— the run simply never reaches #13 and ends at #7 on `max_iterations_reached`. That is the correct
behaviour (do not claim saturation you cannot measure) but it is silent, and silence here is a
product failure: the user asked why the run ended and "it ran out of iterations" is not the whole
answer. `saturation_basis` (§6.1) is therefore **mandatory** on every stop, and the §32 header
renders it next to the stop reason. The loop additionally emits a
`research_log(event_type='saturation_unavailable', severity='warning')` row the first time a
family is found structurally unavailable, so the gap appears in the narrative at the point it
arose rather than only in the final report.

**Why this precedence.** Operator intent precedes everything. Unrecoverable failure precedes
budget, because continuing to charge a broken run is strictly wrong. Budget and clock precede all
quality predicates because they are not judgements — a run that cannot afford another iteration has
no decision left to make, and evaluating saturation first risks spending the synthesis reserve on
a `continue`. Explicit operator targets (`content_target_reached`, `user_depth_reached`) precede
the automatic ones so that a run that did what it was asked reports *that*, not an inferred
reason — and they are now one predicate with a recorded source, because having both
`max_iterations_reached` and `user_depth_reached` as independent tests meant whichever bound was
lower shadowed the other and the reported reason depended on an accident of configuration.
`insufficient_signal` precedes `saturation` because a run with no clusters and a 10% relevance
rate is not saturated; calling it saturated would be the single most misleading possible stop
reason. `rank_exhaustion` and `repetition` precede `saturation` because they are the narrower,
more specific diagnoses of the same symptoms — and in the `rank_exhaustion` case a *different*
one: the symptoms look identical, but the remedy ("go deeper in the ranking, or change source") is
the opposite of the remedy for saturation ("stop").

### 6.3 Thresholds in the composite, and their boundaries

**Every threshold in this table is an unvalidated prior** (§3.3). None was measured; each is a
reasoned starting point whose calibrated replacement the §3.3 job is specified to produce, and
until `calibration_status = 'calibrated'` the composite does not fire at all. Read the "why"
column as an argument, not as evidence.

| Conjunct | Value | Why that number | At the boundary |
|---|---|---|---|
| all five families `avail = OK` | — | a missing number is not a zero (§6.2) | any `INSUFFICIENT`/`UNAVAILABLE` family blocks the stop outright |
| `n_valid` | ≥5 | §5.8: earliest point where all five families have a trustworthy value, given the §5.5 cohort lag | 4 valid iterations → `continue` even if every metric looks saturated |
| `sentinel_novelty_ewma` | ≤0.15 | ≤15% of the *fixed-composition* stream's content/concepts is new; at 0.15 an iteration of 100 items yields ~15 novel observations, which is still enough to matter, so this is a deliberately *late* threshold paired with the other four conjuncts | exactly 0.15 → satisfied |
| `diversification_probe_ran ∧ novelty_probe ≤ 0.15` | — | §3.7(c): without it, "saturated" and "we never opened page 2" are indistinguishable | probe not run → novelty family not satisfied; probe run and novel → `rank_exhaustion`, not `saturation` |
| `cluster_stability_min2` | ≥0.75 | expected to sit above the AMI jitter floor; **the jitter floor is unmeasured** (§5.8.4), so this is the least defensible number in the table and §12.2 makes measuring it a prerequisite | 0.75 satisfied; a single reading of 0.76 with a prior 0.74 does **not** satisfy (min of 2) |
| `noise_churn_max2` | ≤0.15 | above this the density structure is moving through the noise bucket | max of last 2, so one churny iteration blocks the stop |
| `core_fraction` | ≥0.50 | §5.3: below this, AMI is computed on a self-selected stable core | validity condition, not a conjunct: below 0.50 the cluster family is `INSUFFICIENT` |
| `entrants == 0 ∧ removals ≤ 1` | counts | states directly what a scalar threshold cannot (§5.4): the set-size dependence of `CompS` means no fixed θ expresses "one removal" | any entrant, or two removals, in either of the last two iterations blocks the stop |
| `rbo_min2` | ≥0.90 | catches the same brands in a different order; on the **normalised** RBO one removal from a set of 8 scores 0.990 and from a set of 5 scores 0.968, so 0.90 is clear of the one-removal case at every plausible set size | min of last 2 |
| `marginal_yield_rel_median3` | ≤0.20 | a 5× degradation: five collection units now buy what one bought at the start | median of 3, so one lucky iteration cannot block and one unlucky one cannot force |
| `hypothesis_generation_max2` | ≤0.50 | §5.6: ≥3× decline, ≤½ a question per 100 items | NULL readings excluded, not zero-filled |

### 6.4 What is persisted on stop

`research_runs`: `state`, `stop_reason`, `stopped_at`, `stop_detail jsonb` (the full predicate
evaluation — **every conjunct's value, its family's `avail`, and whether it held**, so the
decision is re-readable without re-deriving it), `saturation_basis text`, `degraded bool`,
`coverage_gaps jsonb` (planned vs achieved units per platform, per strategy and per temporal
stratum, with the cause of each shortfall), `sampling_design jsonb` (§3.7: realised rank/sort/page
depth distribution, survivorship disclosure, concentration caps hit, `temporal_coverage`), and
`calibration jsonb`. Plus a `research_log(event_type='run_stopped')` row whose `summary` is one
sentence a marketer can read.

`stop_detail` must be sufficient to reproduce the decision without re-running any metric. The
acceptance test for this subsystem is literally that: re-running `evaluate()` over the persisted
rows reproduces `stop_reason` byte-for-byte.

---
## 7. Cost at scale

At the brief §6 target dataset — **~1,500 social videos + ~2,500 commerce records per run** — with
`aypo_analysis_model = "claude-sonnet-5"` ($2/$10 per Mtok) for the high-volume per-item pass and
`aypo_llm_model = "claude-opus-5"` ($5/$25) for every judgement-heavy stage, which is the shipped
configuration.

Per-item analysis rates are the **measured** ones from `docs/ENVIRONMENT.md` ($0.0295/video on
sonnet-5, $0.0725 on opus-5), which that file requires be trusted over any design doc. Everything
else in this table is projected.

| Line | Model | Basis | Qty | Unit | USD |
|---|---|---|---|---|---|
| Video multimodal analysis | sonnet-5 | 8 frames (~8.8k img tok) + ~1.2k text + cached system; ~900 output — **measured**, ENVIRONMENT.md | 1,500 | $0.0295 | **44.25** |
| Commerce record analysis | sonnet-5 | batches of 25 → 100 calls × (15k in / 5k out) — projected | 2,500 | $0.0032 | **8.00** |
| Transcription | whisper-1 | 45 s mean duration → 1,125 min | 1,500 | $0.006/min | **6.75** |
| Embeddings | text-embedding-3-large@1536 | 1.2M (content) + 0.5M (commerce) + 0.05M (labels, queries) | 1.75M tok | $0.13/Mtok | **0.23** |
| Web search discovery | Serper | — | ~250 calls | ~$0.002 | **0.50** |
| Managed collection | Apify | ~5,270 returned results at a 70%/80% admit rate | 5,270 | **$0.004 (PLACEHOLDER)** | **21.10** |
| **Orchestration LLM (this subsystem)** | opus-5 | PLANNING $0.140 + 8 × (PROPOSE $0.108 + INTERPRET $0.248 + narrate $0.041) + SYNTHESIS $0.998, ×1.2 retry margin | — | — | **5.17** |
| **Total** | | | | | **≈ $86** |

Split across the two capped USD dimensions:

- **Anthropic (`llm_usd`): $57.4** against `aypo_max_llm_usd = 120.0` — **2.1× headroom.** The
  `advisory` guard (0.70 → $84) is not reached in a nominal run, so budget pressure should not be
  the usual stop reason.
- **Everything else (`other_usd`): $28.6**, of which $21.1 is the Apify placeholder.

**The managed-collection line is not merely uncertain — it is known to exceed the plan.**
`docs/ENVIRONMENT.md` records the Apify account as `plan: FREE, maxMonthlyUsageUsd: 5`, with every
chosen actor on PAY_PER_EVENT, and states outright that the brief §6 targets cannot be collected
inside $5. A projected $21.10 of collection spend is therefore **4× the entire monthly ceiling**,
not a soft estimate. Three consequences for this subsystem, all mandatory:

1. `aypo_provider_usd_ceiling` (§4.1) defaults to the plan cap and is enforced **globally and
   cross-run** at DISCOVER, before enqueue. Hitting it fails the search with
   `error_class='provider_quota'`, degrades that platform exactly as §9.1 degrades an open
   breaker, and records a `coverage_gaps` row — it does not fail the run.
2. At PLANNING the loop projects collection spend from `aypo_apify_usd_per_result × target
   results` and, when the projection exceeds the remaining ceiling, **scales the Apify-backed
   platforms' targets down to what the ceiling buys** and writes a `budget_threshold` warning
   naming the shortfall, rather than discovering it at iteration 4.
3. The default run profile on this machine is therefore the §9.2 degraded profile —
   Serper discovery plus public-page retrieval — with Apify-backed platforms contributing a
   deliberately small, ceiling-bounded sample. That is a configuration fact, not a failure, and
   `coverage_gaps` is what carries it onto every finding.

Orchestration is **~6% of run cost**. That is the central budgeting fact of this design: the loop
barely costs anything to *think*, and almost everything to *decide to collect*. It is why the
allocation currency is the collection unit (§2.2) and why dollars are a guard rather than a dial.

### 7.1 The analysis-tier sensitivity the loop must police

If an operator raises `aypo_analysis_model` to `claude-opus-5`, per-item analysis goes from
$0.0295 to $0.0725 per video (both measured, ENVIRONMENT.md) and projected Anthropic spend jumps
from **$57.4 to $134.1** — **above** the `aypo_max_llm_usd = 120.0` cap. The `drain` guard would
trip at 0.95 × 120 = $114, i.e. around video 1,230 of 1,500: a `budget_drain` stop at ~82% of the
brief §6 target that an operator would reasonably mistake for a product bug.

The loop's obligation is therefore to **project before it spends**: at PLANNING it computes
`projected_llm_usd` from the resolved analysis model, the target corpus and the per-item token
model, and writes `research_log(event_type='budget_threshold', severity='warning')` naming the
shortfall and the three levers — raise the cap to ~$170, drop analysis frames 8 → 6 (≈ −$16 at the
opus tier), or reduce the target corpus — *before* iteration 0. Choosing between them belongs to
the analysis subsystem and the operator; detecting and reporting it belongs here.

### 7.2 Wall clock, not dollars, is the binding constraint

`aypo_max_wallclock_minutes = 180` with `aypo_worker_concurrency = 4` cannot deliver 1,500 videos.
At an *estimated* p95 of ~70 s per item (download + FFmpeg frame extraction + whisper + vision —
estimated, not measured; the §4.4 calibration probe measures it on the first run and the pacer
uses that value thereafter),
throughput is 4 / 70 = 0.057 items/s = **206 items/h**, so 180 min buys **~617 items** — 41% of the
brief §6 target. Moving to the Sonnet tier barely helps, because the per-item latency is dominated by
download, FFmpeg and transcription, not by the vision call.

For the full target: concurrency **≥16** gives 1,500 × 70 / 16 ≈ 109 min of social analysis, plus
~25 min for 2,500 commerce records (batched, far cheaper per record) and ~15 min of unavoidable
DISCOVER_WAIT that does not overlap analysis, plus 8 × 6 min of iteration overhead + 10 min
planning + 12 min synthesis ≈ **219 min**, so a wall cap of **≥240 min** leaves ~10% margin. (An
earlier version of this section quoted ≥240 min while its own arithmetic summed to 179; the
missing terms were commerce and discovery.) Until both are raised, §4.4's throughput-aware pacer sizes the run down correctly and
stops at `wall_clock_pacing` with a smaller corpus and an honest `coverage_gaps` record — the right
behaviour, but it should be a deliberate configuration choice rather than a surprise. The pacer
writes the same `budget_threshold` warning at PLANNING when
`projected_items_from_throughput < 0.6 × target_social`.

### 7.3 Cost controls inside the loop

- **Prompt caching.** The PROPOSE and INTERPRET system prompts are byte-identical across every
  iteration and every run, so `cache_system=True` bills them at 0.10×. Saves ~$0.03/run — trivial
  in absolute terms, listed because `LLMClient` already does it and the prompts must be kept
  byte-stable for it to keep working (no timestamps, no run ids, no counts in the system prompt).
- **Effort ladder.** `low` for narration, `medium` for PROPOSE, `high` for INTERPRET, `max` for
  SYNTHESIS only. The `constrain` guard (§4.5) demotes INTERPRET to `medium`, saving ~40% of its
  output tokens.
- **Digest caps.** 20 clusters / 150 recent queries / 1.2k-token cluster briefs hold the INTERPRET
  context near 25k tokens even at full corpus. Without caps, a 1,500-item corpus would push
  INTERPRET past 200k tokens and ~$1.20 per iteration.
- **The orchestration stages stay on opus-5.** Query planning, pattern interpretation, hypothesis
  formation and the stop narrative are the judgement-heavy, low-volume steps; at ~6% of run cost
  there is no case for downgrading them, and a weaker planner degrades corpus quality in a way that
  no downstream stage can repair.

---

## 8. Schemas I own

Dimension `1536` is templated from `settings.aypo_embedding_dimensions` at migration-generation
time. Soft references to tables owned elsewhere (`brands`, `content_items`, `commerce_items`,
`clusters`, `hypotheses`, `findings`) are declared as FKs and added by a later migration so these
tables can be created in any order.

```sql
-- supabase/migrations/0002_research_loop.sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TYPE research_depth           AS ENUM ('quick','standard','deep');
CREATE TYPE research_run_state       AS ENUM ('created','planning','iterating','focusing',
                                              'stopping','synthesizing','completed','failed',
                                              'paused','cancelled');
CREATE TYPE research_iteration_phase AS ENUM ('allocate','propose','dedupe','discover',
                                              'discover_wait','retrieve_wait','analyze_wait',
                                              'cluster','interpret','measure','decide',
                                              'closed','abandoned');
CREATE TYPE research_strategy        AS ENUM ('brand','category','competitor','theme','hypothesis');
CREATE TYPE research_query_kind      AS ENUM ('standard','exploratory_probe','replication');
CREATE TYPE research_query_status    AS ENUM ('proposed','admitted','rejected_duplicate',
                                              'rejected_guardrail','rejected_low_yield',
                                              'rejected_budget','queued','running','succeeded',
                                              'partial','failed','refused','skipped');
CREATE TYPE research_dedup_state     AS ENUM ('new','dup_in_run','dup_cross_run','dup_in_batch');
CREATE TYPE research_item_state      AS ENUM ('discovered','pending_retrieval','retrieved',
                                              'analyzing','analyzed','failed','skipped');
CREATE TYPE research_budget_kind     AS ENUM ('social_unit','commerce_unit','llm','embedding',
                                              'transcription','provider_call','provider_result',
                                              'storage','wall');
CREATE TYPE research_budget_status   AS ENUM ('reserved','settled','orphaned','voided');
-- research_stop_reason: see §6.1
```

### 8.1 `research_runs`

```sql
CREATE TABLE research_runs (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_by          uuid,
  created_at          timestamptz NOT NULL DEFAULT now(),
  started_at          timestamptz,
  stopped_at          timestamptz,

  -- objective: the only operator-supplied content-bearing input (brief §1)
  objective           text        NOT NULL,
  subject_brand       text        NOT NULL,
  subject_brand_id    uuid,                   -- FK brands(id), added later
  market_code         text        NOT NULL,
  language_codes      text[]      NOT NULL DEFAULT '{}',
  user_seeds          jsonb       NOT NULL DEFAULT '{}'::jsonb,  -- flagged seed_origin='user'
  depth               research_depth NOT NULL DEFAULT 'standard',

  -- state machine
  state               research_run_state NOT NULL DEFAULT 'created',
  state_entered_at    timestamptz NOT NULL DEFAULT now(),
  prev_state          research_run_state,
  iteration_no        int         NOT NULL DEFAULT 0,
  iterations_completed int        NOT NULL DEFAULT 0,
  consecutive_abandoned_iterations int NOT NULL DEFAULT 0,

  -- leasing (§1.4)
  lease_owner         text,
  lease_expires_at    timestamptz,
  lease_epoch         bigint      NOT NULL DEFAULT 0,
  next_tick_at        timestamptz NOT NULL DEFAULT now(),

  -- caps resolved at PLANNING from Settings × depth: NULLable, because the row exists
  -- in state 'created' before PLANNING has run. A NOT NULL column here (as an earlier
  -- draft had) contradicts the state machine.
  cap_social_units    int,
  cap_commerce_units  int,
  cap_llm_usd         numeric(10,4),
  cap_other_usd       numeric(10,4),
  cap_wall_seconds    int,
  max_iterations      int,
  iteration_ceiling_source text,          -- 'settings' | 'depth'  (§6.2 #7)
  gamma_social        numeric(6,4),       -- solved at PLANNING (§2.4)
  target_social       int,
  target_commerce     int,
  -- targets rescaled over platforms that actually have credentials (§9.2); the
  -- originals are kept so coverage_gaps can state what was given up
  target_social_effective   int,
  target_commerce_effective int,
  focus_iterations_used int       NOT NULL DEFAULT 0,
  log_seq             bigint      NOT NULL DEFAULT 0,   -- §8.5 gap-free log sequence

  -- the embedding space is pinned: §5.2/§5.3 comparisons must never cross spaces
  embedding_model     text        NOT NULL,
  embedding_dim       int         NOT NULL,

  config_resolved     jsonb,       -- provider availability, actor ids, resolved thresholds
  calibration         jsonb,       -- §3.3 percentile curves and chosen thresholds
  coverage_gaps       jsonb,       -- planned vs achieved units per platform/strategy + cause
  sampling_design     jsonb,       -- §3.7 rank/sort/depth mix, survivorship, temporal coverage
  temporal_plan       jsonb,       -- §3.7(d) strata and target shares
  stop_reason         research_stop_reason,
  saturation_basis    text,        -- §6.1 'full' | 'uncalibrated' | '<family>_unavailable,...'
  stop_detail         jsonb,       -- every conjunct's value AND its family avail at the
                                   -- deciding evaluation
  seed_dependence     text,        -- §3.4 'low' | 'high'
  degraded            boolean     NOT NULL DEFAULT false,
  fatal_error         text,
  cancel_requested    boolean     NOT NULL DEFAULT false,
  pause_requested     boolean     NOT NULL DEFAULT false
);

CREATE INDEX research_runs_tick_idx ON research_runs (next_tick_at)
  WHERE state NOT IN ('completed','failed','cancelled','paused');
CREATE INDEX research_runs_state_idx ON research_runs (state, created_at DESC);
```

### 8.2 `research_iterations`

```sql
CREATE TABLE research_iterations (
  run_id            uuid NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
  iteration_no      int  NOT NULL,
  phase             research_iteration_phase NOT NULL DEFAULT 'allocate',
  phase_attempt     int  NOT NULL DEFAULT 0,
  phase_entered_at  timestamptz NOT NULL DEFAULT now(),
  phase_deadline_at timestamptz,

  opened_at         timestamptz NOT NULL DEFAULT now(),
  proposed_at       timestamptz,
  discovered_at     timestamptz,
  analyzed_at       timestamptz,
  clustered_at      timestamptz,
  interpreted_at    timestamptz,
  measured_at       timestamptz,
  decided_at        timestamptz,
  closed_at         timestamptz,

  is_focus          boolean NOT NULL DEFAULT false,
  quota             jsonb  NOT NULL,   -- {social:int, commerce:int, by_strategy:{...}, weights:{...},
                                       --  prior:{...}, bandit_z:{...}, brand_guard_active:bool}
  budget_snapshot   jsonb  NOT NULL,   -- BudgetState at ALLOCATE
  spend             jsonb,             -- BudgetVector settled during this iteration
  queries_proposed  int NOT NULL DEFAULT 0,
  queries_admitted  int NOT NULL DEFAULT 0,
  queries_rejected  jsonb NOT NULL DEFAULT '{}'::jsonb,  -- {duplicate:n, guardrail:n, low_yield:n,...}
  items_admitted    int NOT NULL DEFAULT 0,
  items_analyzed    int NOT NULL DEFAULT 0,
  analysis_completion_rate numeric(5,4),
  degraded          boolean NOT NULL DEFAULT false,
  degraded_reasons  text[] NOT NULL DEFAULT '{}',
  PRIMARY KEY (run_id, iteration_no)
);
```

### 8.3 `research_queries`

```sql
CREATE TABLE research_queries (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id            uuid NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
  iteration_no      int  NOT NULL,

  strategy          research_strategy   NOT NULL,
  kind              research_query_kind NOT NULL DEFAULT 'standard',
  platform          text NOT NULL,            -- tiktok|youtube|instagram|facebook|web|shopee|lazada
  query_text        text NOT NULL,
  normalized_text   text NOT NULL,
  token_set         text[] NOT NULL,
  stem              text NOT NULL,            -- 2-token prefix, §3.5
  language_code     text,
  filters           jsonb NOT NULL DEFAULT '{}'::jsonb,   -- date window, sort, region, min engagement
  query_embedding   vector(1536),

  -- WHY this query exists (brief §9, §32)
  rationale         text NOT NULL,
  triggered_by_kind text NOT NULL,            -- objective|cluster|competitor|hypothesis|finding|
                                              -- discovered_label|seed|none
  triggered_by_id   text,                     -- the RESOLVED id; the planner supplies a
                                              -- digest ordinal, DEDUPE resolves it (§3.2)
  triggered_by_ref  text,                     -- the ordinal as proposed, kept for audit
  hypothesis_id     uuid,                     -- FK hypotheses(id), added later
  parent_query_id   uuid REFERENCES research_queries(id),
  expected_observable text,
  disconfirming     boolean NOT NULL DEFAULT false,
  log_event_id      uuid,                     -- FK research_log(id), added later

  -- admission
  status            research_query_status NOT NULL DEFAULT 'proposed',
  reject_reason     text,
  dup_of_query_id   uuid REFERENCES research_queries(id),
  dup_similarity    numeric(5,4),
  dup_method        text,                     -- exact|token_set_ratio|jaccard|semantic
  dup_override_reason text,                   -- §3.3 filter-envelope / replication exemption
  guardrail_novel_tokens text[],
  prior_derived     boolean NOT NULL DEFAULT false,  -- §3.4: term came from the model, not data
  is_sentinel       boolean NOT NULL DEFAULT false,  -- §5.2(e) fixed-composition stream
  strategy_asserted research_strategy,               -- what the planner said, before §3.2
                                                     -- mechanical correction

  -- execution
  job_id            uuid,
  requested_units   int NOT NULL DEFAULT 0,   -- assigned by QuotaAllocator after DEDUPE,
                                              -- never by the planner (§3.2)
  results_total     int NOT NULL DEFAULT 0,
  results_new       int NOT NULL DEFAULT 0,
  results_admitted  int NOT NULL DEFAULT 0,
  results_relevant  int NOT NULL DEFAULT 0,
  garbage_count     int NOT NULL DEFAULT 0,
  attempt           int NOT NULL DEFAULT 0,
  error_class       text,
  error_detail      text,
  cost_usd          numeric(10,6) NOT NULL DEFAULT 0,
  proposed_at       timestamptz NOT NULL DEFAULT now(),
  started_at        timestamptz,
  finished_at       timestamptz
);

CREATE INDEX rq_run_iter_idx    ON research_queries (run_id, iteration_no, status);
CREATE INDEX rq_strategy_idx    ON research_queries (run_id, strategy, status);
CREATE INDEX rq_stem_idx        ON research_queries (run_id, stem);
CREATE INDEX rq_norm_trgm_idx   ON research_queries USING gin (normalized_text gin_trgm_ops);
CREATE INDEX rq_embedding_idx   ON research_queries USING hnsw (query_embedding vector_cosine_ops)
  WHERE status IN ('admitted','queued','running','succeeded','partial');
CREATE INDEX rq_trigger_idx     ON research_queries (run_id, triggered_by_kind, triggered_by_id);
CREATE UNIQUE INDEX rq_exact_dedup_idx
  ON research_queries (run_id, platform, normalized_text)
  WHERE status IN ('admitted','queued','running','succeeded','partial');
```

The partial unique index is the last line of defence: even if the application-level dedup has a
bug, the same normalised query cannot be *executed* twice on the same platform in one run.

### 8.4 `research_results`

One row per (query, discovered item). This is the provenance table brief §7 requires.

```sql
CREATE TABLE research_results (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id            uuid NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
  query_id          uuid NOT NULL REFERENCES research_queries(id) ON DELETE CASCADE,
  iteration_no      int  NOT NULL,
  platform          text NOT NULL,
  provider          text NOT NULL,            -- apify:<actor_id> | youtube_api | serper | ...
  collection_method text NOT NULL,            -- search|hashtag|creator|sound|related|marketplace

  external_id       text,
  url               text NOT NULL,
  url_canonical     text NOT NULL,
  url_hash          bytea NOT NULL,           -- sha256(url_canonical)
  -- ONE dedup key, because ON CONFLICT takes one conflict target (see below)
  item_key          bytea NOT NULL
    GENERATED ALWAYS AS (coalesce(sha256(convert_to(external_id,'UTF8')), url_hash)) STORED,
  -- §3.7(a): the sampling design, recorded per item
  raw_rank          int,
  sort_order        text,                     -- relevance|recent|top, as requested
  page_depth        int NOT NULL DEFAULT 1,
  published_at      timestamptz,
  published_at_precision text,                -- day|month|unknown (NULL-heavy on some sources)
  discovered_at     timestamptz NOT NULL DEFAULT now(),

  dedup_state       research_dedup_state NOT NULL DEFAULT 'new',
  first_seen_query_id uuid REFERENCES research_queries(id),   -- immutable, §4.3
  first_seen_run_id uuid,

  relevance_score   numeric(5,4),
  relevance_source  text,                     -- heuristic|llm|provider
  admitted          boolean NOT NULL DEFAULT false,
  admit_reason      text,
  reject_reason     text,

  content_item_id   uuid,                     -- FK content_items(id), added later
  commerce_item_id  uuid,                     -- FK commerce_items(id), added later
  item_state        research_item_state NOT NULL DEFAULT 'discovered',
  -- 1.0 per admitted item IN ITS OWN DIMENSION (social or commerce), 0 for dups and for
  -- items rejected by the §2.3 brand gate or the §5.8 concentration caps. An earlier draft
  -- charged commerce at 0.15 here, which contradicts §2.2's two-vector definition and would
  -- have let the 2,500-unit commerce cap buy 16,667 records.
  cu_charged        numeric(6,3) NOT NULL DEFAULT 0,
  raw               jsonb                     -- provider payload, for replay/debug
);

-- ONE unique index, so `ON CONFLICT (run_id, item_key)` is expressible. Two unique
-- indexes (the earlier draft's external_id and url_hash) cannot both be named as the
-- conflict target of a single INSERT, so a conflict on the unnamed one raises instead
-- of recording the rediscovery.
CREATE UNIQUE INDEX rr_item_unique_idx ON research_results (run_id, item_key);
CREATE INDEX rr_url_idx ON research_results (run_id, url_hash);
CREATE INDEX rr_query_idx     ON research_results (query_id);
CREATE INDEX rr_pending_idx   ON research_results (run_id, iteration_no, item_state)
  WHERE admitted;
CREATE INDEX rr_content_idx   ON research_results (content_item_id)  WHERE content_item_id IS NOT NULL;
CREATE INDEX rr_commerce_idx  ON research_results (commerce_item_id) WHERE commerce_item_id IS NOT NULL;
```

`rr_item_unique_idx` is the mechanism for `dedup_state`: the insert uses
`ON CONFLICT (run_id, item_key) DO UPDATE SET dedup_state='dup_in_run'` and leaves
`cu_charged = 0`, so re-discovery is recorded (it is itself a signal — `dedup_rejection_rate`
reads it) without being charged twice. `first_seen_query_id` is written only on insert
(`ON CONFLICT` does not touch it), which is what makes §4.3's immutability a database property
rather than an application convention.

**Cross-run dedup is recorded but not charged differently.** `dedup_state='dup_cross_run'` and
`first_seen_run_id` mark an item this *account* has analysed before. The item is still charged
1.0 CU in this run — the run's own corpus and its own per-strategy yield statistics must be
complete and self-contained, and sharing analysis across runs is a collection/analysis-subsystem
caching decision, not a research-loop accounting one.

### 8.5 `research_log` — the narrative chain for brief §32

```sql
CREATE TYPE research_log_event AS ENUM (
  'run_created','objective_set','capability_probe','source_unconfigured','seed_probe_set',
  'state_transition','iteration_opened','quota_allocated','queries_proposed','query_admitted',
  'query_rejected','source_searched','results_admitted','source_degraded','provider_garbage',
  'items_analyzed','cluster_snapshot','pattern_detected','competitor_discovered',
  'competitor_promoted','hypothesis_created','hypothesis_updated','hypothesis_resolved',
  'commerce_probe_launched','social_commerce_link','finding_drafted','finding_published',
  'evidence_linked','saturation_measured','focus_mode_entered','budget_threshold',
  'phase_deadline_partial','llm_fallback','llm_refused','iteration_closed','iteration_abandoned',
  'saturation_unavailable','diversification_probe','concentration_cap','temporal_backfill',
  'stop_evaluated','run_stopped','run_failed'
);

CREATE TABLE research_log (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id          uuid NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
  iteration_no    int,
  seq             bigint NOT NULL,                     -- monotonic within run
  occurred_at     timestamptz NOT NULL DEFAULT now(),
  lease_epoch     bigint NOT NULL,

  event_type      research_log_event NOT NULL,
  phase           research_iteration_phase,
  -- the narrative edge: query ← hypothesis ← pattern ← cluster ← results ← query ...
  parent_event_id uuid REFERENCES research_log(id) ON DELETE SET NULL,
  -- polymorphic pointer to the domain row this event is about
  subject_kind    text,      -- query|result|cluster|competitor|hypothesis|finding|provider|budget
  subject_id      text,

  summary         text NOT NULL,          -- one sentence, written for a marketer, rendered in §32
  detail          text,                   -- optional longer prose (model rationale verbatim)
  payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
  visibility      text NOT NULL DEFAULT 'user',   -- 'user' | 'debug'
  severity        text NOT NULL DEFAULT 'info'    -- info | warning | error
);

CREATE UNIQUE INDEX rl_seq_idx ON research_log (run_id, seq);
CREATE INDEX rl_run_time_idx   ON research_log (run_id, occurred_at);
CREATE INDEX rl_parent_idx     ON research_log (parent_event_id);
CREATE INDEX rl_subject_idx    ON research_log (run_id, subject_kind, subject_id);
CREATE INDEX rl_user_idx       ON research_log (run_id, iteration_no, seq) WHERE visibility = 'user';
```

`seq` comes from a counter on the run row, allocated in the same transaction as the log row:

```sql
UPDATE research_runs SET log_seq = log_seq + 1 WHERE id = $1 RETURNING log_seq;
```

The row-level lock the UPDATE takes serialises concurrent emitters, and the counter is gap-free
because an aborted transaction rolls the increment back with the log row it was for. An earlier
draft used `SELECT coalesce(max(seq),0)+1 FROM research_log WHERE run_id=$1 FOR UPDATE`, which
is **not valid Postgres** — `FOR UPDATE` cannot be applied to an aggregate query ("FOR UPDATE is
not allowed with aggregate functions") — and, had it parsed, would have locked nothing and raced.

**The §32 query.** The whole narrative chain, parent-first, in one round trip:

```sql
WITH RECURSIVE chain AS (
  SELECT l.*, 0 AS depth, ARRAY[l.seq] AS path
    FROM research_log l
   WHERE l.run_id = $1 AND l.parent_event_id IS NULL AND l.visibility = 'user'
  UNION ALL
  SELECT c2.*, chain.depth + 1, chain.path || c2.seq
    FROM research_log c2
    JOIN chain ON c2.parent_event_id = chain.id
   WHERE c2.visibility = 'user'
)
SELECT id, iteration_no, depth, event_type, summary, detail, subject_kind, subject_id, payload
  FROM chain
 ORDER BY path;
```

Rendering guidance for §32's seven stages: `objective_set` → `seed_probe_set` → per iteration
(`quota_allocated`, `queries_proposed` with each `query_admitted` child carrying its `rationale`,
`source_searched` → `results_admitted`, `pattern_detected` → `hypothesis_created` →
the follow-up `query_admitted` children of that hypothesis node, `competitor_discovered`,
`commerce_probe_launched` → `social_commerce_link`, `finding_drafted` → `evidence_linked`),
then `saturation_measured` → `stop_evaluated` → `run_stopped`. Because every follow-up query's log
node has the triggering hypothesis's node as its parent, the "why did you search this?" chain is a
tree walk, not a join puzzle.

### 8.6 `research_budget_ledger`

```sql
CREATE TABLE research_budget_ledger (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id         uuid NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
  iteration_no   int,
  phase          research_iteration_phase,
  strategy       research_strategy,
  query_id       uuid REFERENCES research_queries(id) ON DELETE SET NULL,
  content_item_id  uuid,
  commerce_item_id uuid,

  kind           research_budget_kind   NOT NULL,
  status         research_budget_status NOT NULL DEFAULT 'reserved',
  idem_key       text NOT NULL,
  provider       text,                 -- anthropic|openai|apify|serper|s3
  model          text,

  qty            numeric(14,4) NOT NULL DEFAULT 0,   -- tokens | seconds | units | calls
  unit_cost_usd  numeric(14,10),
  usd            numeric(12,6) NOT NULL DEFAULT 0,
  input_tokens         bigint NOT NULL DEFAULT 0,
  output_tokens        bigint NOT NULL DEFAULT 0,
  cache_read_tokens    bigint NOT NULL DEFAULT 0,
  cache_write_tokens   bigint NOT NULL DEFAULT 0,
  detail         jsonb NOT NULL DEFAULT '{}'::jsonb,
  reserved_at    timestamptz NOT NULL DEFAULT now(),
  settled_at     timestamptz
);

CREATE UNIQUE INDEX rbl_idem_idx ON research_budget_ledger (run_id, idem_key);
CREATE INDEX rbl_run_iter_idx    ON research_budget_ledger (run_id, iteration_no, kind);
CREATE INDEX rbl_open_idx        ON research_budget_ledger (run_id, status) WHERE status = 'reserved';

-- Plain views, not materialised. An MV here is both wrong and unnecessary: it is global
-- rather than per-run, so REFRESH at every MEASURE recomputes the aggregate across every
-- run in the database; a non-CONCURRENT refresh takes ACCESS EXCLUSIVE and blocks readers;
-- and a CONCURRENT refresh needs a unique index these aggregates do not have. The
-- underlying ledger is a few thousand rows per run, so the aggregate is a sub-millisecond
-- index scan on (run_id, iteration_no, kind).
CREATE VIEW v_research_spend_by_iteration AS
SELECT run_id, iteration_no,
       sum(usd) FILTER (WHERE provider = 'anthropic')                       AS llm_usd,
       sum(usd) FILTER (WHERE provider <> 'anthropic' OR provider IS NULL)  AS other_usd,
       sum(qty) FILTER (WHERE kind = 'social_unit')                         AS social_units,
       sum(qty) FILTER (WHERE kind = 'commerce_unit')                       AS commerce_units,
       sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens
  FROM research_budget_ledger
 WHERE status IN ('settled','orphaned')
 GROUP BY run_id, iteration_no;

CREATE VIEW v_research_spend_by_strategy AS
SELECT run_id, iteration_no, strategy,
       sum(usd) AS usd,
       sum(qty) FILTER (WHERE kind = 'social_unit')   AS social_units,
       sum(qty) FILTER (WHERE kind = 'commerce_unit') AS commerce_units
  FROM research_budget_ledger
 WHERE status IN ('settled','orphaned') AND strategy IS NOT NULL
 GROUP BY run_id, iteration_no, strategy;
```

`orphaned` rows are included in the aggregates on purpose: a call that may have billed must be
assumed to have billed (§1.5).

### 8.7 `research_saturation`

```sql
CREATE TABLE research_saturation (
  run_id       uuid NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
  iteration_no int  NOT NULL,
  computed_at  timestamptz NOT NULL DEFAULT now(),

  is_focus       boolean NOT NULL DEFAULT false,
  valid          boolean NOT NULL,
  invalid_reason text,
  -- per-family availability: 'ok' | 'insufficient' | 'unavailable'  (§5.1, §6.2)
  avail          jsonb   NOT NULL DEFAULT '{}'::jsonb,
  metric_basis   jsonb   NOT NULL DEFAULT '{}'::jsonb,  -- e.g. {"yield":"candidate_evidence"}

  items_admitted         int,
  n_eff                  numeric(10,3),   -- §5.8 effective sample size
  design_effect          numeric(8,4),
  icc_estimate           numeric(6,4),
  relevant_admit_rate    numeric(6,4),
  diversification_probe_ran boolean NOT NULL DEFAULT false,
  novelty_probe          numeric(6,4),

  content_novelty        numeric(6,4),
  label_novelty          numeric(6,4),
  novelty_index          numeric(6,4),
  novelty_index_mixadj   numeric(6,4),
  novelty_sentinel       numeric(6,4),
  label_coverage_gt      numeric(6,4),
  novelty_by_strategy    jsonb NOT NULL DEFAULT '{}'::jsonb,
  n_eff_by_strategy      jsonb NOT NULL DEFAULT '{}'::jsonb,
  dedup_rejection_rate   numeric(6,4),

  cluster_ami            numeric(6,4),
  cluster_match_stability numeric(6,4),
  cluster_noise_churn    numeric(6,4),
  cluster_stability      numeric(6,4),
  cluster_matches        jsonb NOT NULL DEFAULT '[]'::jsonb,  -- persisted/split/merged/dissolved
  n_clusters             int,
  n_core_compared        int,
  core_fraction          numeric(6,4),

  competitor_jaccard     numeric(6,4),
  competitor_rbo         numeric(6,4),    -- NORMALISED (§5.4); 1.0 for identical lists
  competitor_stability   numeric(6,4),    -- 0.5·J + 0.5·RBO — diagnostic only, not a stop input
  competitor_new_entrants int,            -- count
  competitor_removals    int,
  competitor_entrant_rate numeric(6,4),
  competitor_coverage_gt numeric(6,4),
  competitor_set_size    int,
  competitor_set         text[] NOT NULL DEFAULT '{}',

  evidence_links_new     int,
  units_spent            numeric(10,3),
  marginal_evidence_yield      numeric(10,6),
  marginal_evidence_yield_rel  numeric(8,4),
  yield_by_strategy      jsonb NOT NULL DEFAULT '{}'::jsonb,

  hypotheses_new_admissible int,
  hypotheses_resolved       int,
  hypotheses_open           int,
  hypothesis_generation_rate numeric(8,4),   -- NULL when INTERPRET was degraded (§5.6)
  hypothesis_resolution_rate numeric(8,4),

  smoothed               jsonb NOT NULL DEFAULT '{}'::jsonb,  -- the windowed values used at DECIDE
  PRIMARY KEY (run_id, iteration_no)
);
```

### 8.8 `research_cluster_snapshots`

The minimum per-iteration cluster state §5.3 needs, owned here so the stability metric does not
depend on another subsystem's retention policy (§11.2). Written at CLUSTER from whatever the
clustering subsystem produced for this iteration, before it can be superseded.

```sql
CREATE TABLE research_cluster_snapshots (
  run_id         uuid NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
  iteration_no   int  NOT NULL,
  item_kind      text NOT NULL CHECK (item_kind IN ('content','commerce')),
  item_id        uuid NOT NULL,
  cluster_key    text,                     -- NULL ⇔ noise
  membership_role text NOT NULL,           -- core | secondary | noise
  PRIMARY KEY (run_id, iteration_no, item_kind, item_id)
);
CREATE INDEX rcs_cluster_idx ON research_cluster_snapshots (run_id, iteration_no, cluster_key);

CREATE TABLE research_cluster_centroids (
  run_id         uuid NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
  iteration_no   int  NOT NULL,
  cluster_key    text NOT NULL,
  size_core      int  NOT NULL,
  centroid       vector(1536) NOT NULL,    -- unit-normalised at write time
  PRIMARY KEY (run_id, iteration_no, cluster_key)
);
```

~1,500 narrow rows and ~12 vectors per iteration; ~12k rows and ~100 vectors for a whole run.
Retention: snapshots older than `iteration_no − 2` may be pruned at STOPPING, except the first
and last, which the §32 view renders.

### 8.9 `research_run_transitions`

```sql
CREATE TABLE research_run_transitions (
  id            bigserial PRIMARY KEY,
  run_id        uuid NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
  occurred_at   timestamptz NOT NULL DEFAULT now(),
  lease_epoch   bigint NOT NULL,
  lease_owner   text,
  from_state    research_run_state,
  to_state      research_run_state NOT NULL,
  iteration_no  int,
  from_phase    research_iteration_phase,
  to_phase      research_iteration_phase,
  trigger       text NOT NULL,     -- phase_complete|deadline|guard|operator|error|lease_recovered
  detail        jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX rrt_run_idx ON research_run_transitions (run_id, occurred_at);
```

Kept separate from `research_log` because it is machine-facing: crash recovery replays it to
reconstruct "what was this run doing when the worker died", and it must stay free of the narrative
noise that makes `research_log` useful to a human.

---
## 9. Failure semantics

The governing rule, from brief §7 and §14: **a source failing is a coverage gap to be recorded, not a
run to be aborted.** The only failures that end a run are "cannot reason at all" (no Anthropic
credential) and "cannot persist at all" (database unreachable).

### 9.1 A provider is down

Per-provider circuit breaker, keyed by `(run_id, provider)` and persisted so it survives worker
restarts.

```python
# aypo/research/failure.py
class BreakerState(StrEnum):
    CLOSED = "closed"; OPEN = "open"; HALF_OPEN = "half_open"; QUARANTINED = "quarantined"
    UNCONFIGURED = "unconfigured"
```

- **Trip** on 5 consecutive failures, or an error rate ≥0.50 over ≥10 calls inside a 10-minute
  window. Five consecutive is the smaller trigger for a hard outage; the rate test catches a
  flapping provider that never gets five in a row. ≥10 calls because a rate from fewer has a
  standard error over 0.15.
- **Open** for `min(120 s · 2^n, 900 s)` where `n` is the consecutive trip count. 120 s base
  matches the lease TTL, so at most one tick is wasted on a provider that just tripped; the 15 min
  ceiling means a provider that is down for an hour is probed four times rather than endlessly.
- **Half-open** admits exactly one probe search. Success → CLOSED and `n` resets; failure → OPEN
  with `n+1`.
- **429 / 503 / 529 and timeouts do not count as failures** for the breaker. They are backed off
  with full jitter (base 2 s, cap 60 s, max 6 attempts, honouring `Retry-After`) and charged to
  wall clock, which the §4.4 pacer already reacts to. Counting rate limits as outages would take a
  healthy-but-throttled provider offline exactly when the loop is working hardest.

**What the loop does with the quota.** At ALLOCATE, platforms whose breaker is OPEN or QUARANTINED
are removed from `available_platforms` (so PROPOSE never proposes for them) and their share is
redistributed to available providers **in proportion to each provider's observed admit rate**,
capped at **+50% of that provider's own original quota**. The cap prevents a single-platform
monoculture: a corpus that is 90% one platform cannot support a cross-platform claim, and brief §28
forbids presenting it as if it could. Any shortfall that cannot be redistributed inside the cap is
written to `research_runs.coverage_gaps` as `{platform, planned, achieved, cause:'provider_open'}`
and surfaced in the §32 view and on every finding whose evidence is affected.

If an open breaker closes mid-run, the platform re-enters allocation at its scheduled weight; the
coverage gap row stays (it is a historical fact about the corpus, not a current status).

### 9.2 A credential is missing (the current state of this repo)

`APIFY_TOKEN` and `YOUTUBE_API_KEY` are absent. At PLANNING the loop reads
`Settings.capabilities()`, marks each unavailable platform `UNCONFIGURED`, **zeroes its quota before
allocation**, and writes one `research_log(event_type='source_unconfigured')` row per platform with
`Settings.missing_for(cap)` in the payload so the UI can tell the operator exactly which env var
to set. An unconfigured provider is **not** a failure: it never trips a breaker, never counts
toward `source_exhaustion`, and never marks an iteration `degraded`.

**Targets are rescaled, not left unreachable.** At PLANNING, `target_social_effective` and
`target_commerce_effective` are recomputed over the platforms that actually have credentials and
an open breaker, and the originals are kept. Without this, `content_target_reached` (§6.2 #6)
requires *both* channels to hit target and can never fire on a machine with no `APIFY_TOKEN` —
the commerce corpus stays at zero forever. Rescaling also keeps the §2.4 commerce schedule from
allocating units no provider can spend. The difference between original and effective targets is
the first row of `coverage_gaps`.

**Embeddings are a harder dependency than discovery.** `capabilities()["embeddings"]` gates
§3.3's semantic dedup, §5.2's novelty, §5.2(b)'s label merge and all of §5.3. Without it the run
is not merely narrower, it is unable to measure itself: PLANNING marks the novelty and cluster
families permanently `UNAVAILABLE`, `saturation_basis` records it, the composite can never fire,
and the run proceeds on lexical dedup alone and stops on budget, clock or `max_iterations`. That
is a legitimate degraded run; what it must never do is report `saturation`.

With only `SERPER_API_KEY` (plus `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`) present, the run is
still viable: `WebSearchCollector` provides
discovery, public-page retrieval provides text and thumbnails, the LLM analyses what can be
retrieved, clustering and the full saturation/stopping machinery run unchanged on a smaller corpus,
and `coverage_gaps` records that TikTok / YouTube / Shopee / Lazada contributed zero. Findings
generated from such a run carry the gap, which is precisely brief §28's "identify insufficient
data". The run stops at `source_exhaustion` only if *every* discovery provider is unavailable —
and if that is already true at PLANNING, the run goes `CREATED → FAILED` without spending anything.

### 9.3 An actor returns garbage

Validation happens at the collector's `normalize()` boundary; the loop sees only counts.
A raw record is **unusable** if it fails schema validation or the minimal-viability check
(`url` present AND a platform id or canonical URL AND at least one of caption/title).

```
garbage_rate = unusable_records / returned_records
```

| Condition | Action |
|---|---|
| `garbage_rate ≤ 0.30` | keep valid records; log `provider_garbage` at `severity='info'`; charge **only valid records** as CU; `research_queries.status='partial'`, `garbage_count` recorded |
| `garbage_rate > 0.30`, attempt 1 | discard the whole batch; charge `provider_call` and `usd` but **zero CU**; retry once with page size halved |
| `garbage_rate > 0.30`, attempt 2 | `QUARANTINE` that provider for the rest of the run; log `provider_garbage` at `severity='error'` with the first 3 offending payload shapes; redistribute quota per §9.1; mark the iteration `degraded` |
| returned_records = 0 on a query that should match | not garbage — `status='succeeded'`, `results_total=0`; feeds §3.5 low-yield suppression |

**Why 0.30.** Individual malformed records are *normal* on these sources: deleted posts, private
accounts, geo-blocked items, listings pulled mid-crawl. A third of a batch failing is a different
kind of event — an actor schema change, a wrong `APIFY_*_ACTOR` id, or an actor that silently
started returning a different entity type. 0.30 is high enough not to quarantine on ordinary
attrition and low enough that a schema break is caught on its first batch. **At the boundary**
(exactly 0.30) the batch is kept, because the cheaper error is to admit a slightly dirty batch that
downstream relevance gating will filter anyway.

Quarantine is per-run, not global: a later run re-probes, because the usual cause is a transient
actor deployment.

### 9.4 The LLM refuses or cannot produce the schema

`LLMClient` already does part of this: it retries the retryable errors with full jitter, retries a
`ValidationError` once then raises `LLMSchemaError`, and on `stop_reason == "refusal"` retries the
same request once on `aypo_llm_fallback_model` before raising `LLMRefused`. The loop layers
phase-specific degradation on top, and the key invariant is: **a reasoning failure must never be
able to manufacture a saturation stop.**

| Exception | PROPOSE | INTERPRET | MEASURE / narrate | SYNTHESIS |
|---|---|---|---|---|
| `LLMSchemaError` | retry once at `effort="high"`; then retry with a reduced schema (drop `expected_observable`, `filters`); then **deterministic fallback** (§9.5) | retry once at `high`; then split the digest into halves and call per half (the usual cause is one pathological cluster brief); then skip hypothesis generation, keep pattern detection | retry once; then write the row with `summary` composed from the metric values by template — narration is cosmetic | retry once at `xhigh`; then emit a findings-only report with no narrative |
| `LLMRefused` (both models declined) | do **not** retry the same input; isolate by bisection once over the digest to find the offending brief, drop it, retry; then deterministic fallback | same bisection; then skip generation, `hypothesis_generation_rate = NULL` | template | drop the offending evidence group, note it in the report |
| `LLMTruncated` | raise `max_tokens` 8k → 16k and retry once; then cut `n_max` in half | raise to 16k, retry once; then halve the digest | raise, retry | raise to 32k, retry once |
| `LLMUnavailable` (no credential, at construction) | run → FAILED | run → FAILED | — | — |
| Persistent `APIConnectionError` / 5xx after the client's own retries | run → **PAUSED** (`pause_reason='llm_circuit_open'`), re-tick with backoff 60 s → 15 min | same | same | same |

The second row matters: `LLMUnavailable` means *no credential is configured*, which is a
permanent, operator-fixable condition and correctly fatal. A transient provider outage is a
different event and must not destroy a run that has already spent most of its budget collecting a
corpus — §1.2 already provides PAUSED for exactly this ("global provider circuit open"), and the
earlier version of this table contradicted it by failing the run. A paused run resumes
automatically when a probe succeeds, and after 6 hours paused it transitions to STOPPING so the
corpus is at least synthesised.

Every degradation writes `research_log(event_type='llm_fallback' or 'llm_refused')` and sets
`research_iterations.degraded = true` with the reason appended to `degraded_reasons`. Because
`degraded` makes `valid = false` in §5.8, a degraded iteration is **excluded from every saturation
window** rather than contributing a zero. This is the single most important failure rule in the
design: without it, an Anthropic outage during INTERPRET would read as "no new hypotheses" and
"no novel labels", and the loop would confidently stop with `stop_reason='saturation'` — the worst
possible lie this product can tell.

If refusals exceed **20%** of a phase's calls, the loop logs `llm_refusal_rate_high`, halves the
batch size and bisects once to isolate the offending item, then continues with that item marked
`refused`. 20% because a genuine content-policy collision usually comes from one or two items in a
batch; a fifth of calls declining means something systemic about the prompt or the corpus, which
needs a human.

### 9.5 Deterministic query fallback

When PROPOSE cannot be used at all, the loop must still make progress without inventing content.
`aypo/research/queries/fallback.py` generates queries purely by **template expansion over
vocabulary already in the database** — no model, no priors:

```
category  : objective key-phrases × {market_code} × {language_codes}
theme     : each cluster label with size ≥ 8, taken verbatim, × {market_code}
competitor: each discovered brand name (confidence ≥ 0.5) × top-2 cluster labels
hypothesis: for each open hypothesis, its unexecuted follow_up_plan items verbatim
brand     : subject_brand × top-2 cluster labels
```

Allocation uses the §2.3 weights unchanged; dedup and the §3.4 guardrail run unchanged (the
guardrail passes trivially because every token came from the corpus). `rationale` is set to
`"deterministic fallback: LLM planner unavailable"` and `kind='standard'`, so the §32 narrative
shows honestly that these queries were generated mechanically. The iteration is marked `degraded`.

### 9.6 The orchestrator crashes

- The lease expires after 120 s; any worker may then claim the run. `research_run_transitions` gets
  a row with `trigger='lease_recovered'`.
- The recovering worker re-reads `research_iterations.phase` and re-runs that phase's handler. Every
  handler is idempotent via its §1.5 completion predicate — it first checks whether the predicate
  already holds and, if so, advances without acting.
- `phase_attempt` is incremented on recovery. At `phase_attempt = 3` the iteration moves to
  `ABANDONED`: whatever it admitted stays in the corpus (those items are real evidence),
  `iteration_no` advances, and `consecutive_abandoned_iterations` increments. Two in a row →
  STOPPING with `repeated_iteration_failure` (or FAILED if the run has no admitted items at all).
- **Reserved ledger rows** are the one thing that cannot be made perfectly safe without
  provider-side idempotency. On recovery, a `reserved` row older than its phase deadline becomes
  `orphaned` and stays counted in the aggregates (§8.6). A duplicate charge is therefore possible
  in the worst case; an *un*counted charge is not. Erring toward over-counting matches
  `pricing.rates_for()`'s "err toward stopping early rather than overspending silently".

### 9.7 A phase deadline is hit

Deadlines, set at phase entry on `research_iterations.phase_deadline_at`:

| Phase | Deadline |
|---|---|
| PLANNING | 600 s |
| PROPOSE / DEDUPE | 300 s |
| DISCOVER_WAIT | 1,500 s (25 min) |
| RETRIEVE_WAIT | `max(900, items · 12 s / concurrency · 1.5)` |
| ANALYZE_WAIT | `max(900, items · p95_item_seconds / concurrency · 1.5)` |
| CLUSTER | 600 s |
| INTERPRET | 900 s |
| MEASURE / DECIDE | 300 s |

The 1.5 multiplier on the derived deadlines absorbs queue contention and retries without being so
loose that a stuck job holds the run for an hour. `phase_deadline_at` is recomputed on every
phase entry, and `phase_attempt` resets to 0 on every phase transition (§1.4) — a counter shared
across phases would abandon an iteration whose PROPOSE, CLUSTER and INTERPRET each needed one
ordinary retry.

On a deadline:
- completion ≥ **0.60** → proceed with what completed; log `phase_deadline_partial`; set
  `analysis_completion_rate` so §5.8 can mark the reading invalid if it is below 0.60.
- completion < 0.60 → **one** extension of 0.5 × the original deadline; then proceed regardless,
  marking the iteration `degraded`. Incomplete jobs are cancelled by `(run_id, iteration_no)`;
  items left in `pending_retrieval` / `retrieved` are **not** discarded — they are re-enqueued in
  the *next* iteration at the front of the queue, so a slow item is eventually analysed rather than
  thrown away.
- 0.60 is the §5.8 validity floor, and for the same reason: a truncated analysis set is biased
  toward fast items, so proceeding is only safe if the bias is small or the reading is discarded.

### 9.8 Summary table

| Failure | Blast radius | Run continues? | Recorded as |
|---|---|---|---|
| Provider 5xx / outage | that platform | yes | breaker OPEN, `coverage_gaps` |
| Provider rate limit | that platform, temporarily | yes | backoff, wall-clock spend |
| Credential missing | that platform, whole run | yes | `source_unconfigured`, `coverage_gaps` |
| Actor garbage ≤30% | that batch | yes | `provider_garbage` info, `status='partial'` |
| Actor garbage >30% twice | that platform | yes | `QUARANTINED`, iteration `degraded` |
| LLM schema error | that phase | yes | reduced schema → deterministic fallback |
| LLM refusal | that phase / item | yes | `llm_refused`, iteration `degraded`, metric NULL |
| No Anthropic credential | whole run | **no** | `FAILED`, `fatal_error` |
| DB unreachable | whole run | **no** | `FAILED` (no rows to record it in; emitted to logs) |
| Worker crash | one phase | yes | lease recovery, `phase_attempt+1` |
| Phase deadline | one iteration's completeness | yes | `phase_deadline_partial`, maybe `valid=false` |
| 3 phase attempts | one iteration | yes | `ABANDONED`, corpus kept |
| 2 abandoned iterations | whole run | no (but synthesises) | `repeated_iteration_failure` |
| All discovery providers down 2 iterations | whole run | no (but synthesises) | `source_exhaustion` |

---

## 10. Python interfaces

```python
# aypo/research/orchestrator.py
class ResearchOrchestrator:
    def __init__(self, *, pool: asyncpg.Pool, llm: LLMClient, ledger: DurableLedger,
                 queue: JobQueue, collectors: CollectorRegistry,
                 embedder: Embedder, clock: Clock) -> None: ...

    async def create_run(self, spec: RunSpec) -> UUID: ...
    async def tick(self, worker_id: str) -> TickOutcome:
        """Claim one due run, advance exactly one phase, release. Idempotent."""
    async def tick_run(self, run_id: UUID, worker_id: str) -> TickOutcome: ...
    async def request_pause(self, run_id: UUID) -> None: ...
    async def request_resume(self, run_id: UUID) -> None: ...
    async def request_cancel(self, run_id: UUID) -> None: ...

@dataclass(frozen=True, slots=True)
class RunSpec:
    objective: str
    subject_brand: str
    market_code: str
    language_codes: tuple[str, ...]
    depth: Depth = Depth.STANDARD
    user_seeds: Mapping[str, Sequence[str]] = field(default_factory=dict)
    target_social: int | None = None          # defaults from depth
    target_commerce: int | None = None
    caps: BudgetCaps | None = None            # defaults from Settings × depth

@dataclass(frozen=True, slots=True)
class TickOutcome:
    run_id: UUID | None
    state: RunState
    phase: IterationPhase | None
    advanced: bool
    next_tick_at: datetime | None
    note: str
```

```python
# aypo/research/iteration.py
class PhaseHandler(Protocol):
    phase: ClassVar[IterationPhase]
    async def is_complete(self, ctx: IterationContext) -> bool: ...
    async def run(self, ctx: IterationContext) -> PhaseResult: ...
    def deadline(self, ctx: IterationContext) -> timedelta: ...

@dataclass(frozen=True, slots=True)
class PhaseResult:
    next_phase: IterationPhase
    wait_for: timedelta | None = None     # set → release lease, re-tick later
    degraded_reason: str | None = None
```

```python
# aypo/research/allocation.py
class QuotaAllocator:
    def gates(self, ctx: AllocationContext) -> dict[Strategy, bool]: ...
    def prior(self, iteration_no: int, open_: Set[Strategy]) -> dict[Strategy, float]: ...
    def bandit_multiplier(self, yields: Mapping[Strategy, float], eta: float
                          ) -> dict[Strategy, float]: ...
    def project(self, weights: Mapping[Strategy, float], brand_headroom: float
                ) -> dict[Strategy, float]: ...
    def iteration_units(self, ctx: AllocationContext) -> Units: ...
    def allocate(self, ctx: AllocationContext) -> IterationPlan: ...

@dataclass(frozen=True, slots=True)
class IterationPlan:
    iteration_no: int
    units: Units
    weights: dict[Strategy, float]
    prior: dict[Strategy, float]
    bandit_z: dict[Strategy, float]
    social_by_strategy: dict[Strategy, int]
    commerce_by_strategy: dict[Strategy, int]
    n_queries: int
    available_platforms: tuple[str, ...]
    brand_guard_active: bool
    is_focus: bool
```

```python
# aypo/research/queries/proposer.py
class QueryProposer:
    async def propose(self, digest: EvidenceDigest, plan: IterationPlan) -> QueryBatch: ...

# aypo/research/queries/dedupe.py
class QueryDeduplicator:
    def __init__(self, *, trgm_threshold: int = 92, jaccard_threshold: float = 0.80,
                 cosine_threshold: float = 0.93, hard_cosine: float = 0.955) -> None:
        """The cosine thresholds are DEFAULTS. When research_runs.calibration holds a
        calibrated value, the orchestrator passes that instead (§3.3)."""
    async def classify(self, run_id: UUID, q: ProposedQuery) -> DedupVerdict: ...
    def resolve_trigger(self, q: ProposedQuery, digest: EvidenceDigest) -> UUID | None:
        """Maps the model's digest ordinal ('H7') to a real id, or rejects (§3.2)."""
    def correct_strategy(self, q: ProposedQuery, vocab: RunVocabulary) -> Strategy:
        """Mechanically re-derives `strategy` from the query tokens (§3.2)."""

@dataclass(frozen=True, slots=True)
class DedupVerdict:
    admitted: bool
    status: QueryStatus
    dup_of_query_id: UUID | None
    similarity: float | None
    method: str | None
    override_reason: str | None

# aypo/research/queries/guardrail.py
class AntiSeedingGuardrail:
    async def vocabulary(self, run_id: UUID) -> RunVocabulary: ...
    def check(self, q: ProposedQuery, vocab: RunVocabulary,
              probes_used: int, probe_cap: int) -> GuardrailVerdict: ...
```

```python
# aypo/research/saturation/metrics.py
class SaturationEvaluator:
    async def novelty(self, run_id: UUID, i: int, plan: IterationPlan) -> NoveltyResult: ...
    async def cluster_stability(self, run_id: UUID, i: int) -> ClusterStabilityResult: ...
    async def competitor_stability(self, run_id: UUID, i: int) -> CompetitorStabilityResult: ...
    async def marginal_yield(self, run_id: UUID, i: int) -> YieldResult: ...
    async def hypothesis_productivity(self, run_id: UUID, i: int) -> HypothesisResult: ...
    async def measure(self, run_id: UUID, i: int, plan: IterationPlan) -> SaturationRow: ...

# aypo/research/saturation/stability.py
def adjusted_mutual_information(prev: Sequence[int], curr: Sequence[int]) -> float: ...
def noise_churn(prev: Mapping[int, int], curr: Mapping[int, int]) -> float: ...
def match_clusters(prev: Snapshot, curr: Snapshot, core: Set[int],
                   *, cos_threshold: float = 0.80, jaccard_threshold: float = 0.40
                   ) -> tuple[list[ClusterMatch], float]: ...
def rank_biased_overlap(a: Sequence[str], b: Sequence[str], p: float = 0.90) -> float:
    """DEPTH-NORMALISED RBO (§5.4): sum(p^(d-1)*A_d) / sum(p^(d-1)) over d = 1..max(|a|,|b|).
    Returns exactly 1.0 for equal lists. The un-normalised truncated form returns 0.57 for
    two identical lists of 8 and makes the §6.3 threshold unsatisfiable."""

def effective_n(values: Sequence[float], groups: Sequence[int]) -> tuple[float, float, float]:
    """(n_eff, deff, icc) under a one-stage cluster design, groups = query batch ids (§5.8)."""

def good_turing_coverage(counts: Mapping[str, int]) -> float:
    """f_1 / n: P(the next observation is of an unseen type). §5.2(f), §5.4."""

# aypo/research/stopping.py
class SaturationWindow:
    @classmethod
    async def load(cls, pool: asyncpg.Pool, run_id: UUID, upto: int) -> SaturationWindow:
        """Loads the last three VALID, NON-FOCUS rows and applies §5.8's per-metric
        smoothing. Every field below is None when its family is not OK; nothing is
        zero-filled, and `holds()` (§6.2) is the only legal way to compare one."""
    families: dict[str, Avail]
    # novelty
    sentinel_novelty_ewma: float | None
    novelty_mixadj_ewma: float | None          # diagnostic
    diversification_probe_ran: bool
    novelty_probe_latest: float | None
    # cluster
    cluster_stability_min2: float | None
    noise_churn_max2: float | None
    # competitor
    competitor_entrants_max2: int | None
    competitor_removals_max2: int | None
    rbo_min2: float | None
    # yield / hypotheses
    marginal_yield_rel_median3: float | None
    hypothesis_generation_max2: float | None
    hypotheses_open_max2: int | None
    hypothesis_generation_all2_zero: bool
    # shared
    dedup_rejection_rate_min2: float | None
    relevant_admit_rate_mean3: float | None
    latest: SaturationRow            # the most recent valid, non-focus row
    n_valid: int
    def all_families_available(self) -> bool: ...
    def satisfied_families(self) -> int:
        """Counts FAMILIES (§6.2's mapping table), not conjuncts. A family whose
        avail is not OK is never counted."""

def evaluate(run: Run, bud: BudgetState, sat: SaturationWindow,
             health: ProviderHealth) -> StopDecision: ...

@dataclass(frozen=True, slots=True)
class StopDecision:
    action: Literal["continue", "focus", "stop"]
    reason: StopReason | None
    detail: dict[str, Any]       # every conjunct's value → research_runs.stop_detail
```

```python
# aypo/research/log.py
class ResearchLog:
    async def emit(self, *, run_id: UUID, event_type: LogEvent, summary: str,
                   iteration_no: int | None = None, phase: IterationPhase | None = None,
                   parent_event_id: UUID | None = None, subject_kind: str | None = None,
                   subject_id: str | None = None, detail: str | None = None,
                   payload: Mapping[str, Any] | None = None,
                   visibility: Literal["user","debug"] = "user",
                   severity: Literal["info","warning","error"] = "info") -> UUID: ...
    async def narrative(self, run_id: UUID) -> list[LogNode]: ...   # the §8.5 recursive query
```

### 10.1 Modules

```
apps/api/aypo/research/
  __init__.py
  states.py                 RunState, IterationPhase, Strategy, StopReason, Depth
  models.py                 RunSpec, EvidenceDigest, ProposedQuery, QueryBatch, briefs
  orchestrator.py           ResearchOrchestrator, tick loop, leasing, transitions
  iteration.py              PhaseHandler protocol + the 13 handlers
  allocation.py             QuotaAllocator, priors, bandit, projection, pacing
  budget.py                 Units, BudgetVector, BudgetCaps, BudgetState, DurableLedger
  stopping.py               SaturationWindow, evaluate(), StopDecision
  failure.py                CircuitBreaker, error classification, degradation policy
  log.py                    ResearchLog
  repository.py             all SQL for the eight owned tables
  queries/
    normalize.py            NFKC/casefold/diacritics/stopwords, token_set, stem
    proposer.py             QueryProposer (LLM)
    dedupe.py               QueryDeduplicator (lexical + semantic)
    guardrail.py            AntiSeedingGuardrail
    fallback.py             deterministic template expansion (§9.5)
  saturation/
    metrics.py              SaturationEvaluator
    stability.py            AMI, noise churn, centroid matching, RBO
    view.py                 SaturationRow
  prompts/
    propose.py              PROPOSE system + user builder
    interpret.py            INTERPRET system + user builder
    synthesize.py           SYNTHESIS system + user builder
apps/api/aypo/jobs/
  research_tick.py          queue worker entrypoint; calls ResearchOrchestrator.tick()
apps/api/aypo/api/
  research.py               POST /runs, GET /runs/{id}, /narrative, /saturation, pause/resume/cancel
supabase/migrations/
  0002_research_loop.sql    §8 DDL
```

---

## 11. What this design needs from other subsystems

These are contracts, not suggestions; each one is load-bearing for a specific algorithm above.

1. **Postgres job queue** (`aypo/jobs/queue.py`) — three concrete changes to what exists today,
   each load-bearing:
   - **`enqueue()` must return the existing job's id on a dedupe collision, not `None`.** Today it
     returns `None`, which breaks crash recovery outright: DISCOVER's completion predicate is
     "every admitted query has `job_id IS NOT NULL`", a replayed DISCOVER re-enqueues with the
     same `dedupe_key`, gets `None`, cannot record a `job_id`, and the predicate becomes
     permanently unsatisfiable — the iteration retries three times and is ABANDONED. The fix is
     one line: `ON CONFLICT … RETURNING id`, or a lookup on the collision path.
   - **`collection_jobs` needs an `iteration_no int` column** (indexed with `research_run_id`).
     Every `*_WAIT` completion predicate is `COUNT(*) WHERE run_id = … AND iteration_no = … AND
     state IN ('pending','running')`, and a predicate that has to unpack `payload` jsonb to find
     the iteration cannot be an index scan.
   - **`cancel(run_id, iteration_no) -> int`** and **`counts_by_state(run_id, iteration_no, kind)`**
     must exist as first-class functions; §4.5's `hard_stop` and §9.7's deadline handling both
     need bulk cancellation by iteration.

   Visibility timeout with at-least-once delivery and `dedupe_key` uniqueness are already correct.
   Terminal states must stay rows the orchestrator can `COUNT`.
2. **Per-iteration cluster snapshots** (clustering subsystem, `docs/design/04-clustering-and-trends.md`)
   — **no longer a hard blocker, because §8.8 takes a copy.** This loop now writes its own
   `research_cluster_snapshots` / `research_cluster_centroids` at CLUSTER, so §5.3 is computable
   whatever the clustering subsystem's retention policy turns out to be. The conflict below is
   still worth resolving — resolving it lets §5.3 consume their *calibrated* matcher instead of
   re-deriving one, and lets the §32 UI show structural evolution — but it is now an improvement,
   not a prerequisite. That design defines
   `cluster_runs(research_run_id, corpus, space, …)`, `cluster_revisions(cluster_id, run_id,
   centroid_1536, match_cos, match_jaccard, match_decision, member_jaccard_vs_prev, …)` and
   `cluster_members(run_id, cluster_revision_id, content_item_id, membership_role, …)`, which is
   almost exactly what §5.3 needs — **except** that
   `create unique index cluster_runs_one_per_space on cluster_runs (research_run_id, corpus, space)`
   permits only **one** cluster run per research run per space. Iteration `N` therefore overwrites
   iteration `N−1`, and §5.3 cannot compare two coexisting snapshots.

   Resolution required, either:
   (a) add `iteration_no int not null` to `cluster_runs` and widen the unique index to
       `(research_run_id, corpus, space, iteration_no)`; or
   (b) keep the one-row-per-space contract but retain the superseded rows under a
       `superseded_by` pointer so both the current and previous revision remain queryable.

   (a) is strongly preferred: it also lets the §32 UI show how the structure evolved iteration by
   iteration, which brief §32 asks for directly. Note also that `cluster_members.run_id` and
   `cluster_revisions.run_id` reference `cluster_runs(id)` with `ON DELETE CASCADE`, so replacing
   the one permitted `cluster_runs` row deletes the previous iteration's members and revisions
   outright — the data is not merely unreachable, it is gone. That is why §8.8 copies rather than
   joins.

   Given the fix, §5.3 should **consume rather than duplicate** that subsystem's work:
   `cluster_revisions.match_cos`, `match_jaccard` and `match_decision` already implement the
   label-invariant centroid+membership match (with their own calibrated `tau_match`), so
   `S_match` is computed from accepted `match_decision = 'continuation'` rows weighted by
   `min(size_core_prev, size_core_curr)`, and the thresholds in §5.3 (cos ≥ 0.80, Jaccard ≥ 0.40)
   defer to their calibrated values where those exist. Only AMI and noise churn are computed here,
   from `cluster_members.membership_role` across the two revisions. Note that their
   `selection_report.ari_core` is ARI used for *candidate selection within one clustering run* —
   a different purpose from §5.3's cross-iteration AMI, and the two should not be conflated.
   Centroids (`centroid_1536`) must be unit-normalised or §5.3 normalises them itself.
   Their `cluster_members.discovery_strategy` denormalisation is exactly what §5.2(d)'s
   per-strategy novelty and §5.5's per-strategy yield need — keep it.
3. **Collectors** — `search(query, filters) -> AsyncIterator[DiscoveryResult]` where
   `DiscoveryResult` carries `platform, external_id, url, published_at, published_at_precision,
   raw_rank, raw`; must accept and echo `research_query_id`; must report per-search
   `provider_calls`, `provider_results`, `unusable_records` and `provider_cost_units` so §9.3's
   garbage gate and the ledger are accurate; must expose `availability() -> ProviderHealth`
   without making a billable call. Additionally, for §3.7:
   - **`raw_rank` and the sort actually applied are mandatory, not best-effort.** They are the
     only record of the ranking bias in the corpus, and without them no downstream prevalence
     statement can be qualified honestly.
   - **`capabilities() -> CollectorCapabilities`** declaring `supports_date_range`,
     `supports_sort`, `max_page_depth`. The loop must know what it can ask for rather than
     discovering silently-ignored filters in the provenance record.
   - **`expand_vocabulary(seed_terms) -> list[str]` where the provider supports it** (search-engine
     related queries, marketplace category-tree nodes, platform hashtag co-occurrence). This is the
     §3.4 affordance that lets iteration 0 *retrieve* category vocabulary instead of guessing it,
     and it is the single most valuable thing collectors can expose for brief §1 compliance. A
     collector that does not implement it returns `[]`; the loop degrades to the capped
     `prior_derived` path and flags `seed_dependence='high'`.
4. **Immutable first-seen attribution** — whoever inserts `research_results` must set
   `first_seen_query_id` on first insert and never overwrite it (§4.3). Per-strategy yield, and
   therefore the bandit, is wrong without this.
5. **Analysis subsystem** — must write `content_analysis.discovered_labels jsonb` as a flat list of
   label strings plus their embeddings (or a canonical label table the loop can join), must set
   `research_results.item_state`, and must emit per-item token usage tagged with the item id so
   §4.3's derived attribution works.
6. **One embedding space per run** — all content, label and query embeddings in a run must use
   `research_runs.embedding_model` / `embedding_dim`. A mid-run model or dimension change
   invalidates §5.2 and §5.3 and must raise `fatal_error`, not silently proceed.
7. **Hypotheses** — `hypotheses(id, run_id, created_iteration, resolved_iteration, status
   ∈ {open, testing, supported, refuted, insufficient_evidence, abandoned}, admissible bool,
   statement, reason, confirming_observables jsonb, refuting_observables jsonb,
   follow_up_plan jsonb)`. `follow_up_plan` items must be **structured query specs**
   `{platform, strategy, query_text, expected_observable, disconfirming}` so the loop can turn them
   into queries without a second LLM call, and `admissible` must be set by the hypothesis engine
   per §5.6's criteria.
8. **Competitor set** — `competitor_relationships` must carry `confidence`, `evidence_count`,
   `first_detected_iteration`, `last_detected_iteration` and `seed_origin ∈ {user, discovered}`,
   plus a view `competitor_set_at(run_id, iteration_no)`. §5.4 needs a *point-in-time* set, not a
   current one.
9. **Evidence links** — `finding_evidence` and `hypothesis_evidence` must carry
   `created_iteration` and point at `content_item_id` / `commerce_item_id`. **Doc 07 as written
   contains no `iteration` anywhere**, so this does not exist today and `MEY` is currently
   uncomputable from real links.
10. **Candidate evidence is the default numerator, and it is this subsystem's own.** Rather than
    block on (9), §5.5 computes its numerator from **candidate evidence**: an admitted item that
    is (a) `relevance_score ≥ run.relevance_floor`, (b) assigned `membership_role IN
    ('core','secondary')` in this iteration's `research_cluster_snapshots`, and (c) the source of
    ≥1 discovered label that survived canonicalisation. All three are rows this loop already has,
    so the yield family is `OK` without any upstream change. When real per-iteration links do
    exist they take precedence and `metric_basis.yield` records which was used — a stop justified
    by candidate evidence is a weaker claim and the report says so.
11. **Analysis model projection** — the analysis subsystem must expose
    `estimate_cost_per_item(model, frames, effort) -> float` so §7.1's PLANNING-time projection is
    computed from the real token model rather than a hard-coded constant that drifts.
12. **`brands` table** — `research_runs.subject_brand_id` and the competitor set need stable brand
    ids; the loop only reads them.
13. **Settings additions** needed (owned by config, proposed here):
    `aypo_max_social_units` / `aypo_max_commerce_units` (splitting the existing
    `aypo_max_collection_units = 4000` into the 1500/2500 the brief §6 targets imply),
    `aypo_max_other_usd`, `aypo_research_poll_seconds`, `aypo_apify_usd_per_result`,
    `aypo_provider_usd_ceiling` (§4.1 — global, cross-run, defaults to the collection plan's
    monthly cap, which `docs/ENVIRONMENT.md` records as **$5** on this account),
    `aypo_research_sentinel_share` (§5.2e, default 0.08), and
    `aypo_research_window_months` (§3.7d, default 12). Every one of these must keep its existing
    `Settings` field style; none may be read from anywhere but `get_settings()`.
14. **Per-item relevance** — `research_results.relevance_score` and `relevance_source` drive the
    low-yield suppressor (§3.5), the `insufficient_signal` predicate (§6.2 #8) and the candidate
    evidence numerator (§11.10). Whichever subsystem produces them must record **which** source
    produced each score, because an LLM-produced relevance judgement feeding a stop predicate is a
    model opinion dressed as a measurement. Where `relevance_source='llm'`, the run report must
    carry the agreement rate between the model and a deterministic heuristic on a held-out sample
    of ≥100 items, so a reader can see how much of the admit rate is a model's opinion.

---

## 12. Open questions

Genuine uncertainties, not placeholders for work I skipped.

1. **Apify per-result cost is unknown, and the plan ceiling is known and binding.** Actor pricing
   is per-actor and partly usage-based; the $0.004/result in §7 is a placeholder and 24% of the
   projected run cost. `docs/ENVIRONMENT.md` additionally records the account as FREE with
   `maxMonthlyUsageUsd: 5`, against a projected $21.10 of collection spend — so this is not only
   an unknown rate but a known 4× overshoot of a hard external cap.
   **Resolved in this revision as far as it can be:** the placeholder rate no longer has stop
   authority (§4.1 — `other_usd` is advisory while its rate source is a placeholder, and the CU
   caps carry the real bound), and a hard cross-run `aypo_provider_usd_ceiling` is enforced at
   DISCOVER (§4.1, §7). **Still open:** the rate itself, and whether the first milestone runs on
   a paid plan or on the Serper-only degraded profile. That is a procurement decision, not a
   design one.
2. **Threshold calibration — the list is longer than four, and the gate is now hard.** The four
   embedding thresholds (0.93 query dup, 0.86 content novelty, 0.92 label merge, 0.80 centroid
   match) are reasoned from the geometry of short single-domain text and measured on nothing. So
   are the AMI jitter floor that §6.3's 0.75 depends on (§5.8.4), the bandit shrinkage point
   `n_0 = 30` (§2.3), and the Good–Turing coverage thresholds (§5.2f, §5.4).
   **Resolved in this revision:** §3.3 now fails closed — while `calibration_status <>
   'calibrated'` the composite `saturation` stop is disabled entirely and `saturation_basis =
   'uncalibrated'` is reported, so the system cannot claim it stopped learning on the strength of
   numbers nobody measured. **Still open:** the calibration job must additionally measure the AMI
   jitter floor by re-clustering one iteration under `min_samples ± 1`, which is not yet
   specified in the clustering subsystem's scope.
3. **Full recompute vs incremental clustering.** §5.3 assumes a full-corpus HDBSCAN recompute per
   iteration — that is what makes a label-invariant comparison necessary, and at 1,500 points it
   costs seconds. If the clustering owner chooses incremental assignment instead, cluster ids
   become stable, AMI becomes trivially ~1.0 **and the cluster family would silently report
   perfect stability**, which is worse than reporting nothing. Mitigation now in place: the
   snapshot (§8.8) records `membership_role` and centroids, so an incremental regime is
   detectable (AMI ≥ 0.99 with non-trivial membership change) and the loop can mark the family
   `UNAVAILABLE` rather than believe it. **Still open:** the replacement measure for the
   incremental case (centroid drift against a fixed reference), and the decision itself.
4. **Does MEY have a numerator early enough?** **Answered, and the answer was no.** Doc 07
   contains no notion of an iteration and `finding_evidence` carries no `created_iteration`, so
   real per-iteration links do not exist today. §5.5 and §11.10 therefore make **candidate
   evidence** the default numerator — computable entirely from rows this loop already owns — with
   `metric_basis.yield` recording which basis was used so the weaker claim is never presented as
   the stronger one. **Still open:** whether the intelligence subsystem should expose real
   per-iteration draft links, which would be better; and the fact that candidate evidence
   correlates with clustering quality, so the yield family is not fully independent of the
   cluster family — the composite's "five independent families" framing is an overstatement to
   that extent.
5. **Temporal coverage.** **Specified in this revision** rather than left open: §3.7(d) defines
   an explicit `temporal_plan`, per-stratum achieved-vs-planned accounting in `coverage_gaps`,
   date-window backfill on `category` queries when a stratum falls under half its target, and a
   `temporal_coverage = 'insufficient'` flag that the trend subsystem must read before emitting
   any growth or decline claim. **Still open:** which collectors can actually honour date-ranged
   search — §11.3 now requires them to *declare* `supports_date_range`, but on managed actors the
   declaration may be optimistic, and a filter that is accepted and ignored produces a provenance
   record that is wrong rather than absent. The only real defence is to check the returned
   `published_at` distribution against the requested window and quarantine the filter if it was
   not honoured; that check belongs to collection and is not yet specified there.
6. **Concurrent runs sharing provider rate limits.** `apify_max_concurrent_runs = 3` is global, not
   per research run. Two simultaneous research runs will contend, and each run's throughput
   estimate (§4.4) will be wrong in a way that makes both pace badly. A global token bucket in
   Postgres is the likely answer; it is out of scope here and nothing in the MVP forces the issue
   until a second run is started.
7. **Is `max_iterations = 8` the right ceiling?** **Resolved in this revision:** §2.4 solves
   `γ_s` per run as the largest γ ≤ 0.82 that keeps the final iteration above the validity floor,
   and reduces `I` if no such γ exists; §6.2 #7 collapses `max_iterations` and `DEPTH_ITERATIONS`
   into one resolved ceiling with a recorded source, so the two can no longer shadow each other
   and report an arbitrary reason. **Still open:** what `DEPTH_ITERATIONS` should actually be for
   `quick`/`standard`/`deep`, which is a product decision.
8. **Degraded-iteration starvation.** If provider instability makes most iterations `degraded`,
   `n_valid` never reaches 5 and the composite can never fire. **Partly resolved:**
   `saturation_basis` is now mandatory on every stop (§6.1, §6.4) and a
   `saturation_unavailable` log event is emitted at the moment a family first becomes structurally
   unavailable, so the gap is visible in the narrative rather than only inferable from silence.
   **Still open, and deliberately so:** no relaxed predicate exists for degraded runs. Claiming
   saturation on an unreliable corpus is the one error this design refuses to make, so a degraded
   run ends on budget, clock or iterations with the basis stated. Whether the product wants a
   softer story for that case is a product decision, not a statistical one.
9. **Clustering snapshot retention** (the §11.2 conflict above). **No longer a blocker:** §8.8
   gives this subsystem its own per-iteration snapshot, taken at CLUSTER before anything can
   supersede it, so `cluster_stability_min2` and `noise_churn_max2` are computable regardless of
   the other subsystem's retention policy. **Still worth doing:** widening
   `cluster_runs_one_per_space` to include `iteration_no` would let §5.3 consume their *calibrated*
   matcher rather than re-deriving one, and would let the §32 UI render structural evolution
   directly. That is now an improvement with a clear payoff, not a prerequisite.
10. **The sentinel costs breadth, and nobody has measured whether it is worth it.** §5.2(e)
    reserves 8% of social units for a deliberately non-adaptive stream whose only purpose is to
    make the novelty reading interpretable. On a 1,200-unit run that is ~96 items that could have
    been spent on discovery. The argument for it is that a saturation stop with no
    fixed-composition reference is not interpretable at all; the argument against is that at
    mid-run quotas the sentinel needs two iterations pooled to clear `n_eff ≥ 15`, which makes it
    sluggish. 0.08 is a guess, and whether a sentinel or a larger diversification probe is the
    better use of the same units is an empirical question for the first real run.
11. **Design-effect estimation is itself noisy.** §5.8 estimates the intra-class correlation from
    each iteration's own query batches, but with ~6–15 batches the ICC estimate has wide
    uncertainty, and `n_eff` inherits it. The concentration caps bound the damage (they bound
    `m̄`, the term `deff` is most sensitive to), but a conservative floor — e.g. never credit
    `n_eff` above `0.5 · n` until ICC has been measured across several runs — may be the safer
    operating rule. Not yet decided.

---

## 13. Review notes (adversarial pass, 2026-10-02)

This section records what an adversarial review changed and what it deliberately left standing.
It is part of the document so that a later reader can see which numbers were questioned and why.

### 13.1 Fatal defects found and fixed

1. **The composite saturation stop could never fire — RBO arithmetic (§5.4).** The truncated form
   `(1−p)·Σ_{d=1}^{D} p^{d−1}·A_d` is bounded by `1 − p^D`. At `p = 0.90` and `D = 8` it returns
   **0.5695 for two identical lists**, so `CompS = 0.5·J + 0.5·RBO` was capped at 0.785 against a
   required 0.85 — unsatisfiable for any competitor set of ≤11 brands, i.e. for the entire range
   the document says to expect. Fixed by depth-normalising RBO and by replacing the scalar
   threshold with the predicate it was trying to express (`entrants == 0 ∧ removals ≤ 1 ∧
   RBO ≥ 0.90`), because the value of a one-removal change is set-size dependent and no fixed θ
   expresses "one removal" across sizes. The claimed weight distribution was also wrong: at
   `p = 0.90` the top 10 ranks carry ~86% of the weight, not 65%.
2. **`evaluate()` raised `TypeError` on any NULL conjunct, and no NULL policy existed (§6.2).**
   Three families could be structurally unavailable (no cluster snapshots, no per-iteration
   evidence links, uncalibrated thresholds) and the document never said what the predicate did
   with them. Fixed with explicit per-family `avail`, a `holds()` helper, and the rule that the
   composite requires all five families `OK`. This is the generalisation of the document's own
   stated top rule — a failure must never manufacture a saturation stop — from the LLM case to
   every missing number.
3. **`hypothesis_starvation` could stop a healthy run after one iteration (§5.6, §6.2 #10).**
   `hypotheses_open == 0 ∧ G == 0` is the normal state at the end of iteration 0. Guarded with
   three valid iterations and two consecutive readings.
4. **The category floor of 0.25 — described as load-bearing for every comparative claim — was
   not enforced (§2.3).** Renormalisation puts it at 0.240 from `i = 6` in the document's own
   table, and the bandit could push it lower. Moved into the step-4 projection.
5. **The brand ≤0.30 corpus guard was a weight cap, not a corpus guarantee (§2.3).** Weights
   bound requested units; the invariant is about admitted items. Added an admission-time gate.
6. **A PAUSED run could never be resumed (§1.4).** The lease `WHERE` excluded `'paused'`
   unconditionally.
7. **`research_log.seq` used invalid SQL (§8.5).** `SELECT max(seq) … FOR UPDATE` is rejected by
   Postgres ("FOR UPDATE is not allowed with aggregate functions"). Replaced with a counter
   column on the run row.
8. **`cu_charged = 0.15` for commerce (§8.4) contradicted the two-vector CU definition (§2.2)**
   and would have let a 2,500-unit commerce cap buy 16,667 records.
9. **FOCUSING was self-defeating (§5.7).** A 0.6× forced-narrow iteration cannot clear the
   per-strategy `n_eff` gate, so the metrics whose fall-back was supposed to return the run to
   ITERATING are not computable on it. Replaced the exit condition with observable products, and
   capped focus iterations at 2 to close an unbounded cycle.

### 13.2 Statistical-validity corrections

- **Non-independence (§5.8).** Every threshold was justified by a plain binomial standard error
  on the item count. Items arrive in per-query ranked batches, repeat creators and repeat
  formats; the design effect is substantial. All `n ≥ 15 / 40 / 50` gates are now gates on
  `n_eff`, the ICC is estimated from each iteration's own batches and persisted, and
  per-query (25%) and per-creator (10%) concentration caps bound the design effect mechanically.
- **Collection-time composition masquerading as saturation (§5.2d–e).** Mix adjustment on target
  weights removes only the between-strategy confound; within-strategy narrowing remains, and
  declining novelty would partly be the loop reading its own query design back to itself. Added
  a fixed-composition **category sentinel** as the stream that actually carries the novelty
  conjunct.
- **Platform ranking and survivorship (§3.7).** The corpus is a ranked, survivorship-filtered,
  non-probability sample and nothing recorded that. `raw_rank`, `sort_order` and `page_depth` are
  now mandatory per item, and a **diversification probe** is a precondition on the composite stop,
  with `rank_exhaustion` as a distinct stop reason — "we used up the ranked head" and "the
  category has nothing more to show" produce identical symptoms and opposite remedies.
- **MEY numerator/denominator mismatch and a biased baseline (§5.5).** Links created in iteration
  `i` mostly point at items bought earlier, so the ratio did not measure marginal productivity;
  made cohort-lagged. `baseline = max(MEY_0..2)` is upward-biased, making the `≤0.20` conjunct
  systematically easy — a bias toward stopping, opposite to the document's stated bias
  everywhere else; changed to a median.
- **Bandit selection bias (§2.3).** A z-score over five noisy arms with a floored std is a
  winner's-curse machine. Added precision-weighted shrinkage and an `n_eff ≥ 15` gate.
- **Conditioning bias in AMI (§5.3).** `I_core` is selected on being clustered in *both*
  snapshots, which is the stability being measured. Added `core_fraction ≥ 0.50` and an explicit
  statement of what `CS_i` is and is not a claim about.
- **Repeated evaluation (§5.8.3).** Seven conjuncts evaluated at up to eight iterations is
  multiplicity; the windows are the (blunt) control and the document now says so rather than
  implying a precision it does not have.
- **Fabricated measurements.** Numbers labelled "measured" (`per_iter_overhead = 360 s`, "a
  measured p95 of ~70 s", "the cost model puts it at", "no observable change in query diversity",
  "HDBSCAN … moves AMI by an expected 0.02–0.05", "reliably yields 5–12 clusters") describe
  experiments that have not been run. Relabelled as seeds or hypotheses, and §3.3 now **fails
  closed**: the composite saturation stop is disabled until calibration has run.

### 13.3 Anti-seeding (brief §1, §34)

No literal assumption about the research subject, its consumers, occasions, competitors or
category appears anywhere in this document — a term-level check for subject-specific or
market-specific vocabulary finds nothing, and the three-source vocabulary rule in the compliance
statement is accurate. One **structural** violation was found and fixed:

> **Iteration 0 was a 100% licence for model priors**, and iteration 0 buys ~23% of the social
> corpus whose vocabulary then becomes `vocab` for every subsequent iteration. A prior injected
> there is not one contaminated query, it is the seed of the run's entire term space — precisely
> the "discover what it already assumed" failure brief §1 names. Auditability is a mitigation,
> not a defence: a reviewer reading a list of 23 plausible guesses cannot un-collect the corpus
> they produced.

§3.4 now restricts iteration 0 to operator config, seeds, deterministic translation, and
**retrieved** category vocabulary (marketplace category trees, search-engine related queries,
hashtag co-occurrence — contracted in §11.3), with free-invention probes capped at 20% of queries
and 10% of units, flagged `prior_derived`, and propagated to a `seed_dependence` flag on findings.

A second, softer case was reframed rather than removed: "~270 items is where HDBSCAN reliably
yields 5–12 non-trivial clusters" is a prior about how many themes this subject's content
contains. The justification is now about resolution only.

### 13.4 Feasibility and environment conflicts

- **`docs/ENVIRONMENT.md` records the Apify account as FREE with a $5/month ceiling** and states
  that the brief §6 targets cannot be collected inside it. §7 projected $21.10 of collection
  spend — 4× a hard external cap — and §12.1 described the cost merely as "unknown". That file is
  normative over design docs. §4.1/§7 now add a global cross-run `aypo_provider_usd_ceiling`
  enforced at DISCOVER, PLANNING-time target scaling, and an explicit statement that the default
  profile on this machine is the degraded Serper-only one.
- **A placeholder price had stop authority.** `other_usd` fed `pressure()`, so a guessed rate
  could stop a run and report `budget_drain`. It is now advisory until its rate source is
  measured; the CU caps carry the real bound.
- **Per-item costs reconciled** with the measured values in ENVIRONMENT.md ($0.0295 / $0.0725),
  and the three mutually inconsistent per-video figures ($0.085, $0.0313, the implied 1/17)
  collapsed into one ledger-derived ratio.
- **`enqueue()` returns `None` on a dedupe collision**, which makes DISCOVER's completion
  predicate unsatisfiable after a crash and abandons the iteration. §11.1 now states the required
  one-line change, plus the `iteration_no` column every `*_WAIT` count query needs.
- **`ON CONFLICT` with two unique indexes** is not expressible in one statement; replaced with a
  single generated `item_key`.
- **Materialised views refreshed at every MEASURE** are global, lock readers, and cannot be
  refreshed concurrently without a unique index. Replaced with plain views.
- **`DurableLedger`** exposed `record_sync`, which `LLMClient` never calls; it must subclass
  `Ledger` and override `record()`.
- **Transient LLM unavailability sent the run to FAILED**, contradicting §1.2 and §9's governing
  rule; now PAUSED with backoff.
- **Heartbeat/epoch and `phase_attempt` semantics** were unspecified in ways that are silent
  correctness bugs (a heartbeat reusing the claim UPDATE invalidates the running worker's own
  epoch; a phase-spanning attempt counter abandons an iteration after three unrelated hiccups).
- **The §5.2 novelty query** cannot use an HNSW index through a join and would have issued ~270
  filtered scans per iteration; replaced with one fetch and a numpy matmul.
- **§7.2's own arithmetic summed to 179 min** while concluding "≥240 min"; the missing commerce
  and discovery terms are now shown.

### 13.5 Hallucination surface closed

`requested_units` (the allocator's number), `triggered_by_id` (a UUID the model had to
transcribe, whose corruption silently misattributes provenance in the §32 audit tree), and
free-form `filters` were removed from the planner's output schema. `strategy` is now mechanically
re-derived from query tokens rather than trusted, because a subject-brand query mislabelled
`category` defeats the brief §8 guard and contaminates the comparison baseline. `language_code`
and `platform` are validated against run config and live availability. §11.14 additionally
requires that LLM-produced relevance scores — which feed a stop predicate — be distinguishable
from heuristic ones and audited against a held-out sample.

### 13.6 What was left alone, deliberately

Most of the design is sound and was not touched. The durable-FSM-advanced-by-ticks shape, the
lease/epoch model, the ledger-before-call rule with `orphaned` rows staying counted, the
collection unit as the allocation currency, the two-level state machine, the `research_log` tree
with `parent_event_id`, immutable `first_seen_query_id`, per-metric rather than uniform smoothing,
AMI with `average_method='max'` (correctly the most conservative normalisation), separating noise
churn from the stability composite, the precedence ordering of the stop stack, and the rule that a
degraded INTERPRET writes NULL rather than 0 are all right, and several of them are the reason the
remaining defects were findable at all.

### 13.7 Residual risk

1. **Every threshold is still a prior.** The fail-closed gate means an uncalibrated run cannot
   claim saturation, which converts a correctness risk into a capability gap: until a real
   corpus exists, in practice runs will end on budget, clock or iterations. That is the honest
   outcome, but it means the saturation machinery is unexercised until the first real run.
2. **The five families are not fully independent.** Candidate evidence (the default MEY
   numerator) depends on clustering, which is also the cluster family's input. "Five independent
   families agreed" overstates it by one.
3. **The design effect is estimated from few batches**, so `n_eff` is itself noisy (open
   question 11). The concentration caps bound the damage but do not remove it.
4. **The sentinel costs 8% of social units** and is slow to clear `n_eff ≥ 15` at mid-run quotas
   (open question 10). Whether it or a larger diversification probe is the better use of those
   units is empirical.
5. **Collectors may accept and silently ignore date filters**, producing provenance that is wrong
   rather than absent (open question 5). The check belongs to the collection subsystem.
6. **Apify economics are unresolved** (open question 1). The guards now prevent a wrong number
   from causing a wrong stop, but they cannot buy data the plan does not cover.
7. **Competitor confidence is produced by another subsystem's formula** and used here as a gate.
   If that formula is recalibrated, this loop's `competitor` strategy gate and its `K_i`
   definition both move. The gate is read from `config_resolved`, but the coupling is real.
