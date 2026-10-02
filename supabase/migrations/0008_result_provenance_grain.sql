-- Fix the grain of research_results.
--
-- `rr_item_unique_idx` was UNIQUE (run_id, item_key), which permits only one
-- provenance row per item per run. That silently collapses a real distinction:
-- an item discovered by a CATEGORY query and again by a COMPETITOR query is two
-- observations with different sampling strategies, and the second insert was
-- being dropped by ON CONFLICT DO NOTHING.
--
-- Why this matters beyond tidiness. Sampling strategy is a property of the
-- DISCOVERING QUERY, never of the item (D-3). The defence against collection-
-- time sampling bias depends on it: only items discovered by the reference
-- strata (brand, category) may enter a denominator or a trend test, because
-- competitor/theme/hypothesis queries are query-conditioned and would bias
-- every share they appear in. With the old index, whichever query happened to
-- reach the item first determined its stratum for the entire run, so a
-- denominator could silently include query-conditioned items -- or exclude
-- reference-stratum ones.
--
-- The correct grain is one row per (run, query, item). Cross-query duplicates
-- are then identified by dedup_state, not by being absent.

DROP INDEX IF EXISTS rr_item_unique_idx;

CREATE UNIQUE INDEX rr_query_item_unique_idx
    ON research_results (run_id, query_id, item_key);

-- Still needed for "which queries found this item", which the Content Explorer
-- renders as the item's provenance, and which the dedup bookkeeping reads.
CREATE INDEX IF NOT EXISTS rr_run_item_idx
    ON research_results (run_id, item_key);
