-- Deferred foreign keys (D-43/D-44).
--
-- Migrations 0001..0006 are written as independently creatable tracks, so
-- cross-track references were left as plain uuid columns with the target named
-- only in a comment. That is a sound way to keep the tracks independent, but it
-- is not a sound place to stop: without these constraints a deleted run leaves
-- orphaned coverage, job and evidence rows behind, and those orphans are then
-- counted into per-run denominators that brief §28 makes load-bearing.
--
-- Orphans are removed before each constraint is added, because an ALTER TABLE
-- that fails validation would leave the whole amendment unapplied.

-- ── collection_coverage ─────────────────────────────────────────────────────
DELETE FROM collection_coverage cc
 WHERE NOT EXISTS (SELECT 1 FROM research_runs r WHERE r.id = cc.research_run_id);

ALTER TABLE collection_coverage
  ADD CONSTRAINT collection_coverage_run_fk
  FOREIGN KEY (research_run_id) REFERENCES research_runs(id) ON DELETE CASCADE;

-- ── collection_jobs ─────────────────────────────────────────────────────────
-- SET NULL rather than CASCADE: a job row is an execution record, and losing
-- the history of what ran because its run was deleted would make a failure
-- impossible to investigate after the fact.
UPDATE collection_jobs j
   SET research_run_id = NULL
 WHERE research_run_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM research_runs r WHERE r.id = j.research_run_id);

ALTER TABLE collection_jobs
  ADD CONSTRAINT collection_jobs_run_fk
  FOREIGN KEY (research_run_id) REFERENCES research_runs(id) ON DELETE SET NULL;

-- ── brand provenance ────────────────────────────────────────────────────────
-- A surface observation is the unit of record that keeps brand resolution
-- reversible, so it is scoped to the run that observed it.
DELETE FROM brand_surface_observations o
 WHERE o.research_run_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM research_runs r WHERE r.id = o.research_run_id);

ALTER TABLE brand_surface_observations
  ADD CONSTRAINT brand_surface_observations_run_fk
  FOREIGN KEY (research_run_id) REFERENCES research_runs(id) ON DELETE CASCADE;

DELETE FROM brand_seeds s
 WHERE s.research_run_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM research_runs r WHERE r.id = s.research_run_id);

ALTER TABLE brand_seeds
  ADD CONSTRAINT brand_seeds_run_fk
  FOREIGN KEY (research_run_id) REFERENCES research_runs(id) ON DELETE CASCADE;

-- ── competitor evidence ─────────────────────────────────────────────────────
-- Evidence must not outlive the run that produced it: a competitor confidence
-- recomputed over orphaned evidence would count observations whose provenance
-- no longer exists.
DELETE FROM competitor_signal_evidence e
 WHERE e.research_run_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM research_runs r WHERE r.id = e.research_run_id);

ALTER TABLE competitor_signal_evidence
  ADD CONSTRAINT competitor_signal_evidence_run_fk
  FOREIGN KEY (research_run_id) REFERENCES research_runs(id) ON DELETE CASCADE;
