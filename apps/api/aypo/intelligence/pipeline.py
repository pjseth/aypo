"""Embed, cluster and label the analysed corpus (brief §21).

Runs after collection and analysis rather than inside the collection loop,
because clustering is only meaningful over a corpus: re-clustering after every
item would mostly measure its own instability.

The order is mandated and is the point: cluster first, **inspect the members**,
then label. A label proposed before looking at what is in the group is a
hypothesis about what it ought to contain, and it sticks whether or not the
members support it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import numpy as np
import sklearn
import structlog

from aypo.analysis.cad import CAD_TEMPLATE_VERSION, build_cad
from aypo.analysis.schema import ItemAnalysis
from aypo.config import get_settings
from aypo.db.pool import fetch, transaction
from aypo.intelligence.clustering import (
    MIN_CORPUS_FOR_CLUSTERING,
    ClusteringResult,
    cluster_items,
)
from aypo.intelligence.labeling import label_cluster
from aypo.llm.client import LLMClient, LLMUnavailable
from aypo.llm.pricing import Ledger
from aypo.providers.embeddings import EmbeddingsUnavailable, get_embedding_provider

log = structlog.get_logger(__name__)

# The retrieval view is the one the ANN index is built on; a second view would
# need its own index and its own justification.
EMBEDDING_VIEW = "retrieval_v1"


def _as_vector(value: Any) -> np.ndarray:
    """Coerce whatever the pgvector codec returns into a float array."""
    if hasattr(value, "to_numpy"):
        return np.asarray(value.to_numpy(), dtype=np.float64)
    if hasattr(value, "to_list"):
        return np.asarray(value.to_list(), dtype=np.float64)
    return np.asarray(value, dtype=np.float64)


@dataclass
class PipelineResult:
    run_id: UUID | None = None
    cluster_run_id: UUID | None = None
    analysed: int = 0
    embedded: int = 0
    clustered: int = 0
    n_clusters: int = 0
    labelled: int = 0
    incoherent: int = 0
    noise_fraction: float = 0.0
    skipped_reason: str | None = None
    ledger: Ledger = field(default_factory=Ledger)

    @property
    def ran(self) -> bool:
        return self.skipped_reason is None


async def load_analyses(run_id: UUID | None = None) -> list[tuple[UUID, ItemAnalysis]]:
    """Every stored analysis, as validated objects."""
    rows = await fetch(
        """
        SELECT ca.content_item_id, ca.payload
          FROM content_analysis ca
         WHERE ca.status = 'ok'
           AND ($1::uuid IS NULL OR ca.research_run_id = $1)
        """,
        run_id,
    )
    out: list[tuple[UUID, ItemAnalysis]] = []
    for row in rows:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        try:
            out.append((row["content_item_id"], ItemAnalysis.model_validate(payload)))
        except Exception as exc:
            # A payload written by an older prompt version may not validate
            # against the current schema; skipping it is better than failing the
            # whole pipeline over one row.
            log.warning("pipeline.analysis_unreadable", error=str(exc)[:160])
    return out


async def embed_corpus(
    analyses: list[tuple[UUID, ItemAnalysis]], ledger: Ledger
) -> dict[UUID, np.ndarray]:
    """Build a canonical analysis document per item and embed it.

    Embeddings are cached on the content hash of the document, so re-running
    over an unchanged corpus costs nothing.
    """
    settings = get_settings()
    provider = get_embedding_provider(ledger)

    documents: dict[UUID, Any] = {item_id: build_cad(a) for item_id, a in analyses}
    hashes = [cad.content_hash for cad in documents.values()]
    cached_rows = await fetch(
        "SELECT content_hash, embedding FROM content_embeddings "
        "WHERE view = $1 AND content_hash = ANY($2::text[])",
        EMBEDDING_VIEW,
        hashes,
    )
    # pgvector's asyncpg codec hands back a Vector object, not a sequence.
    cached = {
        r["content_hash"]: _as_vector(r["embedding"])
        for r in cached_rows
        if r["embedding"] is not None
    }

    pending = [(iid, cad) for iid, cad in documents.items() if cad.content_hash not in cached]
    if pending:
        vectors = await provider.embed([cad.text or " " for _, cad in pending])
        async with transaction() as conn:
            for (item_id, cad), vector in zip(pending, vectors, strict=True):
                array = np.asarray(vector, dtype=np.float64)
                norm = np.linalg.norm(array) or 1.0
                array = array / norm
                cached[cad.content_hash] = array
                await conn.execute(
                    """
                    INSERT INTO content_embeddings
                        (item_kind, content_item_id, view, cad_template_version, cad_text,
                         cad_token_count, content_hash, embedding_provider, embedding_model,
                         embedding_dims, identity_epoch, embedding)
                    VALUES ('content', $1, $2, $3, $4, $5, $6, $7, $8, $9, 1, $10)
                    ON CONFLICT DO NOTHING
                    """,
                    item_id,
                    EMBEDDING_VIEW,
                    CAD_TEMPLATE_VERSION,
                    cad.text,
                    cad.token_estimate,
                    cad.content_hash,
                    settings.aypo_embedding_provider,
                    settings.aypo_embedding_model,
                    settings.aypo_embedding_dimensions,
                    array.tolist(),
                )

    return {
        item_id: cached[cad.content_hash]
        for item_id, cad in documents.items()
        if cad.content_hash in cached
    }


async def run_pipeline(
    research_run_id: UUID | None = None, *, label: bool = True
) -> PipelineResult:
    """Embed, cluster and label the analysed corpus."""
    result = PipelineResult(run_id=research_run_id)
    analyses = await load_analyses(research_run_id)
    result.analysed = len(analyses)

    if len(analyses) < MIN_CORPUS_FOR_CLUSTERING:
        result.skipped_reason = (
            f"{len(analyses)} analysed items is below the "
            f"{MIN_CORPUS_FOR_CLUSTERING}-item floor; themes from fewer items "
            "would not be reportable"
        )
        log.info("pipeline.skipped", reason=result.skipped_reason)
        return result

    try:
        vectors = await embed_corpus(analyses, result.ledger)
    except EmbeddingsUnavailable as exc:
        result.skipped_reason = str(exc)
        return result
    result.embedded = len(vectors)

    item_ids = [iid for iid, _ in analyses if iid in vectors]
    matrix = np.vstack([vectors[iid] for iid in item_ids])
    clustering = cluster_items([str(i) for i in item_ids], matrix)
    result.clustered = clustering.n_items
    result.n_clusters = len(clustering.clusters)
    result.noise_fraction = clustering.noise_fraction

    documents = {str(iid): build_cad(a).text for iid, a in analyses}
    embeddings = {str(iid): vec for iid, vec in vectors.items()}

    if label and clustering.clusters:
        try:
            client = LLMClient(ledger=result.ledger)
            for cluster in clustering.clusters:
                verdict = await label_cluster(cluster, documents, embeddings, client=client)
                if verdict is None:
                    continue
                if verdict.coherent:
                    result.labelled += 1
                else:
                    # A clustering algorithm must assign every point somewhere,
                    # so some groups are artefacts. Saying so beats naming one.
                    result.incoherent += 1
        except LLMUnavailable as exc:
            log.warning("pipeline.labelling_unavailable", error=str(exc))

    result.cluster_run_id = await persist(research_run_id, clustering, result)
    log.info(
        "pipeline.done",
        analysed=result.analysed,
        clusters=result.n_clusters,
        labelled=result.labelled,
        incoherent=result.incoherent,
        noise=round(result.noise_fraction, 3),
        usd=round(result.ledger.total_usd, 4),
    )
    return result


async def persist(
    research_run_id: UUID | None, clustering: ClusteringResult, result: PipelineResult
) -> UUID | None:
    """Write the cluster run, its clusters and their members."""
    if research_run_id is None:
        return None
    settings = get_settings()
    digest = hashlib.sha256(
        ",".join(sorted(m.item_id for m in clustering.memberships)).encode()
    ).hexdigest()[:40]

    async with transaction() as conn:
        # Memberships are rebuilt wholesale; a stale membership would make a
        # cluster look larger than the corpus now supports.
        await conn.execute(
            "DELETE FROM cluster_members WHERE run_id IN "
            "(SELECT id FROM cluster_runs WHERE research_run_id = $1)",
            research_run_id,
        )
        cluster_run_id = await conn.fetchval(
            """
            INSERT INTO cluster_runs
                (research_run_id, iteration_no, corpus, space, space_key, identity_epoch,
                 embedding_provider, embedding_model, embedding_dims, cad_template_version,
                 n_items_analysed, n_items_embedded, n_items_clustered, n_clusters,
                 noise_fraction_primary, noise_fraction, space_config, space_stats,
                 hdbscan_params, identity_config, lib_versions, input_digest, permutation_seed)
            VALUES ($1, 0, 'social', 'global', 'social.global', 1,
                    $2, $3, $4, $5, $6, $7, $8, $9, $10, $10, $11, $12, $13, $14, $15, $16, 1)
            -- Re-running the pipeline for a run REPLACES its result rather than
            -- colliding with it: the corpus has grown, so the previous
            -- clustering is superseded, not a second opinion.
            ON CONFLICT (research_run_id, corpus, space, iteration_no) DO UPDATE
               SET n_items_analysed      = EXCLUDED.n_items_analysed,
                   n_items_embedded      = EXCLUDED.n_items_embedded,
                   n_items_clustered     = EXCLUDED.n_items_clustered,
                   n_clusters            = EXCLUDED.n_clusters,
                   noise_fraction_primary = EXCLUDED.noise_fraction_primary,
                   noise_fraction        = EXCLUDED.noise_fraction,
                   hdbscan_params        = EXCLUDED.hdbscan_params,
                   input_digest          = EXCLUDED.input_digest,
                   created_at            = now()
            RETURNING id
            """,
            research_run_id,
            settings.aypo_embedding_provider,
            settings.aypo_embedding_model,
            settings.aypo_embedding_dimensions,
            CAD_TEMPLATE_VERSION,
            result.analysed,
            result.embedded,
            result.clustered,
            result.n_clusters,
            result.noise_fraction,
            {"reducer": "none", "metric": "euclidean_on_l2_normalised"},
            {"n": clustering.n_items},
            {
                "min_cluster_size": clustering.min_cluster_size,
                "min_samples": max(3, clustering.min_cluster_size // 2),
                "cluster_selection_method": "eom",
            },
            {"method": "centroid+jaccard", "continuation_threshold": 0.72},
            {"sklearn": sklearn.__version__, "numpy": np.__version__},
            digest,
        )

        written: list[UUID] = []
        for cluster in clustering.clusters:
            cluster_id = await conn.fetchval(
                """
                INSERT INTO clusters
                    (cluster_key, space_key, corpus, space, identity_epoch,
                     first_run_id, last_run_id, current_revision_id, status, label,
                     description, size_core, size_secondary, birth_kind, trend_eligible,
                     current_classification)
                VALUES ($1, 'social.global', 'social', 'global', 1, $2, $2, NULL,
                        $3::cluster_status, $4, $5, $6, $7, 'birth', $8,
                        'insufficient_data')
                ON CONFLICT (cluster_key) DO UPDATE
                   SET last_run_id = EXCLUDED.last_run_id,
                       label = COALESCE(EXCLUDED.label, clusters.label),
                       size_core = EXCLUDED.size_core
                RETURNING id
                """,
                f"{digest[:12]}:{cluster.index}",
                cluster_run_id,
                "active" if cluster.label else "unlabelled",
                cluster.label,
                cluster.description,
                cluster.size,
                len(cluster.secondary_item_ids),
                # Trend claims need temporal coverage this corpus does not yet
                # have; marking it here keeps the Trends surface honest.
                False,
            )
            written.append(cluster_id)
            rows = [
                (cluster_run_id, cluster_id, UUID(item_id), "content", "core")
                for item_id in cluster.core_item_ids
            ] + [
                (cluster_run_id, cluster_id, UUID(item_id), "content", "secondary")
                for item_id in cluster.secondary_item_ids
            ]
            if rows:
                await conn.executemany(
                    """
                    INSERT INTO cluster_members
                        (run_id, cluster_id, content_item_id, item_kind, membership_role)
                    VALUES ($1, $2, $3, $4, $5::membership_role)
                    ON CONFLICT DO NOTHING
                    """,
                    rows,
                )

        # Re-clustering supersedes the previous result. Without this, each run
        # leaves its generation of the same themes behind as `active`, and the
        # Trends surface shows three versions of one theme and triple-counts the
        # content underneath them.
        retired = await conn.fetchval(
            """
            WITH superseded AS (
                UPDATE clusters
                   SET status = 'retired',
                       death_kind = 'dissolved',
                       updated_at = now()
                 WHERE corpus = 'social'
                   AND status <> 'retired'
                   AND NOT (id = ANY($1::uuid[]))
                   AND last_run_id IN (
                        SELECT id FROM cluster_runs WHERE research_run_id = $2
                   )
                RETURNING 1
            )
            SELECT count(*) FROM superseded
            """,
            written,
            research_run_id,
        )
        if retired:
            log.info("pipeline.retired_superseded_clusters", n=int(retired))
    return cluster_run_id
