"""Read endpoints backing the eight product surfaces (brief §31, §32).

Every endpoint that returns a proportion also returns its denominator, and every
endpoint that returns a finding also returns its confidence band and sample
size. That is not presentation detail -- brief §28 forbids a sample proportion
reading as a market-wide fact, and the only reliable way to guarantee that is to
make the qualifier impossible to omit at the API boundary.

Nothing here decides WHAT appears on a surface. Brief §31 forbids hard-coding
which cards show up: the content is whatever the research produced.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from aypo.db.pool import fetch, fetchrow
from aypo.intelligence.brand_resolution import resolve as resolve_brands

router = APIRouter(tags=["intelligence"])


def _rows(records) -> list[dict[str, Any]]:
    return [dict(r) for r in records]


@router.get("/overview")
async def overview() -> dict[str, Any]:
    """What the dataset currently contains.

    Counts only. Brief §31 forbids hard-coding which cards appear on the
    Cultural Pulse surface, so this reports what was collected and discovered
    and lets the page render whatever is actually there.
    """
    counts = await fetchrow(
        """
        SELECT (SELECT count(*) FROM research_runs)            AS runs,
               (SELECT count(*) FROM content_items)            AS content,
               (SELECT count(*) FROM commerce_items)           AS commerce,
               (SELECT count(*) FROM content_analysis)         AS analyses,
               (SELECT count(*) FROM content_analysis_claims)  AS claims,
               (SELECT count(*) FROM findings)                 AS findings,
               (SELECT count(*) FROM clusters)                 AS clusters,
               (SELECT count(DISTINCT creator_id) FROM content_items
                 WHERE creator_id IS NOT NULL)                 AS creators
        """
    )

    # Brand surface forms as the analyst read them, verbatim. Resolution into
    # entities happens in the research loop; this is the raw observation.
    brands = await fetch(
        """
        SELECT value AS surface_form, count(*) AS observations
          FROM content_analysis_claims
         WHERE field = 'visible_brands'
         GROUP BY value
         ORDER BY count(*) DESC, value
         LIMIT 20
        """
    )
    facets = await fetch(
        """
        SELECT field AS facet, count(*) AS n
          FROM content_analysis_claims
         WHERE field_group = 'discovered_label'
         GROUP BY field
         ORDER BY count(*) DESC
        """
    )
    labels = await fetch(
        """
        SELECT field AS facet, value, cue
          FROM content_analysis_claims
         WHERE field_group = 'discovered_label'
         ORDER BY id DESC
         LIMIT 24
        """
    )
    # Brief §28: incidental appearance must be distinguishable from active use.
    consumption = await fetch(
        """
        SELECT consumption_state, count(*) AS n
          FROM content_analysis_claims
         WHERE consumption_state IS NOT NULL
         GROUP BY consumption_state
         ORDER BY count(*) DESC
        """
    )
    latest = await fetchrow(
        "SELECT id, objective, subject_brand, market_code, state, stop_reason, "
        "created_at FROM research_runs ORDER BY created_at DESC LIMIT 1"
    )
    # Resolve surface forms into entities for display. The raw forms are kept
    # alongside, because the verbatim reading is the evidence and the resolution
    # is an interpretation of it -- a reader should be able to see both.
    resolution = resolve_brands({r["surface_form"]: r["observations"] for r in brands})
    resolved = [
        {
            "canonical_name": entity.canonical_name,
            "observations": entity.observation_count,
            "variants": sorted(entity.surface_forms),
            "seeded": entity.seeded,
        }
        for entity in resolution.entities
    ]

    return {
        "counts": dict(counts) if counts else {},
        "latest_run": dict(latest) if latest else None,
        "brands": resolved,
        "brand_surface_forms": _rows(brands),
        "facets": _rows(facets),
        "labels": _rows(labels),
        "consumption_states": _rows(consumption),
    }


@router.get("/landscape")
async def landscape() -> dict[str, Any]:
    """Discovered behaviours, occasions and cultural signals (brief §31.2).

    Every label carries how many distinct creators and items it came from,
    because a label seen ten times from one creator describes that creator, not
    a pattern in the category.
    """
    rows = await fetch(
        """
        SELECT c.field                              AS facet,
               c.value,
               c.cue,
               c.basis,
               count(*)                             AS observations,
               count(DISTINCT c.content_item_id)    AS distinct_items,
               count(DISTINCT ci.creator_id)        AS distinct_creators
          FROM content_analysis_claims c
          JOIN content_items ci ON ci.id = c.content_item_id
         WHERE c.field_group = 'discovered_label'
         GROUP BY c.field, c.value, c.cue, c.basis
         ORDER BY count(DISTINCT ci.creator_id) DESC, count(*) DESC
        """
    )
    facets: dict[str, list[dict[str, Any]]] = {}
    for row in _rows(rows):
        facets.setdefault(row["facet"], []).append(row)
    total_creators = await fetchrow(
        "SELECT count(DISTINCT creator_id) AS n FROM content_items WHERE creator_id IS NOT NULL"
    )
    return {
        "facets": facets,
        "total_creators": int(total_creators["n"]) if total_creators else 0,
        "caveat": (
            "Labels are composed by the analyst from what each item showed, never "
            "drawn from a fixed list. A label from a single creator describes that "
            "creator, not the category."
        ),
    }


@router.get("/competition")
async def competition() -> dict[str, Any]:
    """Brands discovered in the corpus, and where they co-appear (brief §31.3).

    No overall competitor score: brief §24 forbids one. What is reported is how
    often each brand was actually observed, how many distinct creators and items
    it came from, and which brands were seen in the same item.
    """
    brand_rows = await fetch(
        """
        SELECT c.value                            AS surface_form,
               count(*)                           AS observations,
               count(DISTINCT c.content_item_id)  AS distinct_items,
               count(DISTINCT ci.creator_id)      AS distinct_creators
          FROM content_analysis_claims c
          JOIN content_items ci ON ci.id = c.content_item_id
         WHERE c.field = 'visible_brands'
         GROUP BY c.value
        """
    )
    raw = _rows(brand_rows)
    resolution = resolve_brands({r["surface_form"]: r["observations"] for r in raw})

    # Map each surface form to its resolved entity so per-item co-appearance is
    # computed over entities, not spellings.
    canonical_of = {
        surface: entity.canonical_name
        for entity in resolution.entities
        for surface in entity.surface_forms
    }
    per_item = await fetch(
        """
        SELECT content_item_id, array_agg(DISTINCT value) AS brands
          FROM content_analysis_claims
         WHERE field = 'visible_brands'
         GROUP BY content_item_id
        """
    )
    pairs: dict[tuple[str, str], int] = {}
    for row in per_item:
        names = sorted({canonical_of.get(b, b) for b in row["brands"]})
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                pairs[(a, b)] = pairs.get((a, b), 0) + 1

    stats = {r["surface_form"]: r for r in raw}
    brands = []
    for entity in resolution.entities:
        items = sum(stats[s]["distinct_items"] for s in entity.surface_forms if s in stats)
        creators = max(
            (stats[s]["distinct_creators"] for s in entity.surface_forms if s in stats),
            default=0,
        )
        brands.append(
            {
                "canonical_name": entity.canonical_name,
                "variants": sorted(entity.surface_forms),
                "observations": entity.observation_count,
                "distinct_items": items,
                "distinct_creators": creators,
                "seeded": entity.seeded,
            }
        )

    return {
        "brands": brands,
        "co_appearance": [
            {"a": a, "b": b, "items": n}
            for (a, b), n in sorted(pairs.items(), key=lambda kv: -kv[1])
        ],
        "caveat": (
            "Counts are within the analysed sample and reflect how often a brand was "
            "visible, which is not the same as how often it is bought or preferred. "
            "Co-appearance records that two brands were seen in the same item; it does "
            "not establish substitution or competition."
        ),
    }


@router.get("/trends")
async def trends() -> dict[str, Any]:
    """Clusters and their trend status (brief §31.4)."""
    clusters = await fetch(
        """
        SELECT id, cluster_key, label, description, status, size_core, size_secondary,
               current_classification, current_refusal_reason, trend_eligible
          FROM clusters
         WHERE status NOT IN ('retired', 'merged')
         ORDER BY size_core DESC
        """
    )
    latest = await fetchrow(
        """
        SELECT n_items_clustered, n_clusters, noise_fraction, created_at
          FROM cluster_runs ORDER BY created_at DESC LIMIT 1
        """
    )
    return {
        "clusters": _rows(clusters),
        "latest_cluster_run": dict(latest) if latest else None,
        "caveat": (
            "Growth and decline are only claimed with enough temporal coverage to rule "
            "out an artefact of when the content happened to be collected. Until then a "
            "cluster is reported by size and composition only."
        ),
    }


# ── runs (brief §32) ─────────────────────────────────────────────────────────


@router.get("/runs")
async def list_runs(limit: int = Query(20, le=100)) -> list[dict[str, Any]]:
    return _rows(
        await fetch(
            """
            SELECT id, objective, subject_brand, market_code, state, iteration_no,
                   stop_reason, stop_detail, degraded, created_at, started_at, stopped_at
              FROM research_runs
             ORDER BY created_at DESC
             LIMIT $1
            """,
            limit,
        )
    )


@router.get("/runs/{run_id}")
async def get_run(run_id: UUID) -> dict[str, Any]:
    row = await fetchrow("SELECT * FROM research_runs WHERE id = $1", run_id)
    if row is None:
        raise HTTPException(404, "run not found")
    run = dict(row)

    # Coverage is returned with the run, never separately: a surface that shows
    # results without showing which sources were unavailable misrepresents the
    # sample (brief §7, §28).
    run["coverage"] = _rows(
        await fetch(
            """
            SELECT platform, available, unavailable_reason, queries_run,
                   items_discovered, items_admitted, items_quarantined,
                   planned_units, achieved_units, unmet_units, shortfall_cause
              FROM collection_coverage
             WHERE research_run_id = $1
             ORDER BY platform
            """,
            run_id,
        )
    )
    run["iterations"] = _rows(
        await fetch(
            """
            SELECT iteration_no, phase, queries_proposed, queries_admitted,
                   queries_rejected, items_admitted, items_analysed, degraded,
                   degraded_reasons, opened_at, closed_at
              FROM research_iterations
             WHERE run_id = $1
             ORDER BY iteration_no
            """,
            run_id,
        )
    )
    return run


@router.get("/runs/{run_id}/log")
async def get_run_log(run_id: UUID, limit: int = Query(500, le=2000)) -> list[dict[str, Any]]:
    """The narrative chain the §32 view renders: query -> why -> what it found."""
    return _rows(
        await fetch(
            """
            SELECT seq, parent_event_id, event_type, phase, subject_kind, subject_id,
                   summary, detail, payload, severity, occurred_at
              FROM research_log
             WHERE run_id = $1
             ORDER BY seq
             LIMIT $2
            """,
            run_id,
            limit,
        )
    )


@router.get("/runs/{run_id}/queries")
async def get_run_queries(run_id: UUID) -> list[dict[str, Any]]:
    return _rows(
        await fetch(
            """
            SELECT id, iteration_no, strategy, platform, query_text, rationale,
                   triggered_by_kind, triggered_by_hypothesis_id, triggered_by_finding_id,
                   parent_query_id, is_disconfirming, is_reference_query, status,
                   reject_reason, results_total, results_new, results_admitted,
                   results_relevant, cost_usd, proposed_at
              FROM research_queries
             WHERE run_id = $1
             ORDER BY iteration_no, proposed_at
            """,
            run_id,
        )
    )


# ── findings (brief §27) ─────────────────────────────────────────────────────


@router.get("/findings")
async def list_findings(
    run_id: UUID | None = None,
    include_withheld: bool = False,
    limit: int = Query(50, le=200),
) -> list[dict[str, Any]]:
    """Findings, strongest evidence first.

    Withheld findings are excluded by default but retrievable: brief §28 wants
    insufficient data identified rather than hidden, so an analyst can ask to
    see what did not clear the bar.
    """
    return _rows(
        await fetch(
            """
            SELECT id, research_run_id, claim_type, finding_kind, status,
                   claim_rendered, scope_statement, what_would_refute, caveats,
                   sample_size, effective_sample_size, design_effect, concentration,
                   effect_metric, effect_value, effect_ci_low, effect_ci_high,
                   confidence, confidence_band, contested, withheld_reason,
                   adversarial_verdict, created_at
              FROM findings
             WHERE ($1::uuid IS NULL OR research_run_id = $1)
               AND ($2::bool OR withheld_reason IS NULL)
             ORDER BY confidence DESC NULLS LAST, created_at DESC
             LIMIT $3
            """,
            run_id,
            include_withheld,
            limit,
        )
    )


@router.get("/findings/{finding_id}")
async def get_finding(finding_id: UUID) -> dict[str, Any]:
    """A finding with everything needed to audit it.

    Brief §27: the product is never a black box, so the drill-down returns the
    computed statistics, the evidence, and any refutation raised against it.
    """
    row = await fetchrow("SELECT * FROM findings WHERE id = $1", finding_id)
    if row is None:
        raise HTTPException(404, "finding not found")
    finding = dict(row)
    finding["statistics"] = _rows(
        await fetch("SELECT * FROM finding_statistics WHERE finding_id = $1", finding_id)
    )
    finding["evidence"] = _rows(
        await fetch(
            """
            SELECT fe.*, ci.source_url, ci.caption, ci.thumbnail_url,
                   ci.published_at, ci.views, ci.likes,
                   cm.source_url AS commerce_url, cm.title_raw AS commerce_title,
                   cm.image_url  AS commerce_image
              FROM finding_evidence fe
              -- ref_id is a generic reference discriminated by `kind`, so each
              -- side is joined only for the kind it applies to.
              LEFT JOIN content_items  ci ON fe.kind = 'content'  AND ci.id = fe.ref_id
              LEFT JOIN commerce_items cm ON fe.kind = 'commerce' AND cm.id = fe.ref_id
             WHERE fe.finding_id = $1
             ORDER BY fe.ev_ref
            """,
            finding_id,
        )
    )
    finding["refutations"] = _rows(
        await fetch("SELECT * FROM finding_refutations WHERE finding_id = $1", finding_id)
    )
    return finding


# ── content explorer (brief §31.5) ───────────────────────────────────────────


@router.get("/content")
async def list_content(
    run_id: UUID | None = None,
    platform: str | None = None,
    sponsored: bool | None = None,
    limit: int = Query(60, le=200),
    offset: int = 0,
) -> dict[str, Any]:
    items = _rows(
        await fetch(
            """
            SELECT DISTINCT ci.id, ci.platform, ci.source_url, ci.caption, ci.thumbnail_url,
                   ci.published_at, ci.duration_seconds, ci.views, ci.likes,
                   ci.comments_count, ci.shares, ci.saves, ci.hashtags,
                   ci.language_hint, ci.platform_is_sponsored, ci.sponsorship_source,
                   cr.handle AS creator_handle, cr.follower_count
              FROM content_items ci
              LEFT JOIN creators cr ON cr.id = ci.creator_id
              LEFT JOIN research_results rr ON rr.content_item_id = ci.id
             WHERE ($1::uuid IS NULL OR rr.run_id = $1)
               AND ($2::text IS NULL OR ci.platform = $2)
               AND ($3::bool IS NULL OR ci.platform_is_sponsored = $3)
             ORDER BY ci.published_at DESC NULLS LAST
             LIMIT $4 OFFSET $5
            """,
            run_id,
            platform,
            sponsored,
            limit,
            offset,
        )
    )
    total = await fetchrow(
        """
        SELECT count(DISTINCT ci.id) AS n
          FROM content_items ci
          LEFT JOIN research_results rr ON rr.content_item_id = ci.id
         WHERE ($1::uuid IS NULL OR rr.run_id = $1)
           AND ($2::text IS NULL OR ci.platform = $2)
        """,
        run_id,
        platform,
    )
    # The denominator travels with the page, so a share computed in the UI can
    # never be rendered without one.
    return {"items": items, "total": int(total["n"]) if total else 0, "offset": offset}


@router.get("/content/{content_id}")
async def get_content(content_id: UUID) -> dict[str, Any]:
    row = await fetchrow(
        """
        SELECT ci.*, cr.handle AS creator_handle, cr.display_name AS creator_name,
               cr.follower_count
          FROM content_items ci
          LEFT JOIN creators cr ON cr.id = ci.creator_id
         WHERE ci.id = $1
        """,
        content_id,
    )
    if row is None:
        raise HTTPException(404, "content item not found")
    item = dict(row)
    item["analysis"] = _rows(
        await fetch(
            """
            SELECT id, media_tier, status, summary, evidence_insufficient_for,
                   midpoint_fraction, payload, created_at
              FROM content_analysis
             WHERE content_item_id = $1
             ORDER BY created_at DESC
             LIMIT 1
            """,
            content_id,
        )
    )
    item["transcript"] = _rows(
        await fetch(
            "SELECT has_speech, language_detected, segments FROM content_transcripts "
            "WHERE content_item_id = $1 LIMIT 1",
            content_id,
        )
    )
    # How this item entered the corpus, and which query found it.
    item["provenance"] = _rows(
        await fetch(
            """
            SELECT rr.run_id, rr.admitted, rr.relevance_score, rr.raw_rank,
                   rr.reject_reason, q.query_text, q.strategy, q.rationale
              FROM research_results rr
              JOIN research_queries q ON q.id = rr.query_id
             WHERE rr.content_item_id = $1
            """,
            content_id,
        )
    )
    return item


# ── taxonomy, clusters, trends (brief §31.2, §31.4) ──────────────────────────


@router.get("/labels")
async def list_labels(
    facet: str | None = None, min_support: int = 2, limit: int = Query(200, le=1000)
) -> list[dict[str, Any]]:
    """Discovered vocabulary with its concentration counters.

    distinct_creators and top_creator_share travel with every label because a
    label seen 40 times from 3 creators is not a widespread pattern.
    """
    return _rows(
        await fetch(
            """
            SELECT id, facet, display_form, gloss_en, status, support_count,
                   distinct_content, distinct_creators, distinct_platforms,
                   distinct_queries, top_creator_share, top_query_share,
                   first_seen_at, last_seen_at
              FROM labels
             WHERE ($1::text IS NULL OR facet = $1)
               AND support_count >= $2
             ORDER BY support_count DESC
             LIMIT $3
            """,
            facet,
            min_support,
            limit,
        )
    )


@router.get("/clusters")
async def list_clusters(limit: int = Query(100, le=500)) -> list[dict[str, Any]]:
    return _rows(
        await fetch(
            """
            SELECT id, cluster_key, label, description, status, size_core,
                   size_secondary, brand_presence, platform_breakdown,
                   strategy_breakdown, max_creator_share, n_creator_blocks,
                   current_classification, current_refusal_reason, series_unstable,
                   trend_eligible, first_observed_pub, latest_observed_pub
              FROM clusters
             -- retired and merged clusters are superseded; showing them would
             -- double-count a theme that was absorbed into another.
             WHERE status NOT IN ('retired', 'merged')
             ORDER BY size_core DESC
             LIMIT $1
            """,
            limit,
        )
    )


# ── commerce (brief §31.6) ───────────────────────────────────────────────────


@router.get("/commerce")
async def list_commerce(
    platform: str | None = None, limit: int = Query(60, le=200), offset: int = 0
) -> dict[str, Any]:
    items = _rows(
        await fetch(
            """
            SELECT ci.id, ci.platform, ci.source_url, ci.title_raw, ci.title_denoised,
                   ci.brand_surface_form, ci.brand_confidence, ci.listing_kind,
                   ci.currency, ci.price_minor, ci.original_price_minor, ci.discount_pct,
                   ci.rating_avg, ci.rating_count, ci.review_count,
                   ci.sold_count_raw, ci.sold_count_text, ci.sold_semantics,
                   ci.image_url, ci.category_path, s.name AS seller_name
              FROM commerce_items ci
              LEFT JOIN commerce_sellers s ON s.id = ci.seller_id
             WHERE ($1::text IS NULL OR ci.platform = $1)
             ORDER BY ci.first_seen_at DESC
             LIMIT $2 OFFSET $3
            """,
            platform,
            limit,
            offset,
        )
    )
    total = await fetchrow(
        "SELECT count(*) AS n FROM commerce_items WHERE ($1::text IS NULL OR platform = $1)",
        platform,
    )
    return {
        "items": items,
        "total": int(total["n"]) if total else 0,
        "offset": offset,
        # Rendered next to any sold-count figure in the UI. The platforms
        # disagree about what the number even means, so it must never be shown
        # as a sales figure (brief §28).
        "sold_count_caveat": (
            "Platform-reported and inconsistently defined between marketplaces: a "
            "visibility proxy, not a sales figure."
        ),
    }


# ── competitors (brief §31.3) ────────────────────────────────────────────────


@router.get("/competitors")
async def list_competitors(limit: int = Query(50, le=200)) -> list[dict[str, Any]]:
    """Discovered competitor relationships.

    Returned ordered by evidence, and each row carries WHY it was detected.
    Brief §24 forbids an overall competitor score, so there is none: a
    per-relationship confidence is not a ranking of brands.
    """
    return _rows(
        await fetch(
            """
            SELECT cr.id, cr.relationship_type, cr.evidence_count,
                   cr.evidence_count_social, cr.evidence_count_commerce,
                   cr.distinct_creator_count, cr.n_eff, cr.confidence,
                   cr.confidence_band, cr.signal_strengths, cr.discovery_origin,
                   cr.seed_confirmed, cr.decay_state,
                   cr.first_detected, cr.last_detected,
                   b.canonical_name  AS brand_name,
                   c.canonical_name  AS competitor_name
              FROM competitor_relationships cr
              JOIN brands b ON b.id = cr.brand_id
              JOIN brands c ON c.id = cr.competitor_brand_id
             ORDER BY cr.evidence_count DESC, cr.confidence DESC NULLS LAST
             LIMIT $1
            """,
            limit,
        )
    )


@router.get("/brands")
async def list_brands(limit: int = Query(100, le=500)) -> list[dict[str, Any]]:
    return _rows(
        await fetch(
            """
            SELECT id, canonical_name, brand_level, status, origin, is_subject,
                   created_at
              FROM brands
             WHERE merged_into_brand_id IS NULL
             ORDER BY canonical_name
             LIMIT $1
            """,
            limit,
        )
    )
