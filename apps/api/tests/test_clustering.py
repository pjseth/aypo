"""Clustering, soft membership and cross-run identity.

Cluster identity is the load-bearing part: clusters are recomputed as the corpus
grows, so without stable keys the UI would report re-clustering churn as a theme
growing or dying.
"""

import numpy as np
import pytest

from aypo.intelligence.clustering import (
    CONTINUATION_THRESHOLD,
    MIN_CORPUS_FOR_CLUSTERING,
    LineageKind,
    MembershipKind,
    cluster_items,
    match_clusters,
    min_cluster_size_for,
)


def blobs(n_per: int = 40, dims: int = 32, seed: int = 0) -> tuple[list[str], np.ndarray]:
    """Three well-separated groups in a unit-norm space."""
    rng = np.random.default_rng(seed)
    centres = np.eye(3, dims) * 10.0
    rows, ids = [], []
    for g, centre in enumerate(centres):
        for i in range(n_per):
            rows.append(centre + rng.normal(0, 0.35, dims))
            ids.append(f"g{g}_i{i}")
    return ids, np.vstack(rows)


def test_small_corpus_is_skipped_with_a_reason():
    ids = [f"i{i}" for i in range(20)]
    result = cluster_items(ids, np.random.default_rng(0).normal(size=(20, 16)))
    # Themes from 20 items would not be reportable; saying so is better than
    # producing clusters nobody may cite.
    assert result.ran is False
    assert str(MIN_CORPUS_FOR_CLUSTERING) in result.skipped_reason
    assert result.noise_item_ids == ids


def test_min_cluster_size_scales_with_corpus():
    assert min_cluster_size_for(100) == 8  # floor applies
    assert min_cluster_size_for(4000) == 20  # 0.5% of corpus


def test_separated_groups_are_recovered():
    ids, matrix = blobs()
    result = cluster_items(ids, matrix)
    assert result.ran
    assert len(result.clusters) == 3
    # Each recovered cluster should be dominated by one planted group.
    for cluster in result.clusters:
        prefixes = {m.split("_")[0] for m in cluster.core_item_ids}
        assert len(prefixes) == 1


def test_every_item_is_accounted_for():
    ids, matrix = blobs()
    result = cluster_items(ids, matrix)
    core = {m.item_id for m in result.memberships if m.kind is MembershipKind.CORE}
    assert core | set(result.noise_item_ids) == set(ids)


def test_exemplars_are_drawn_from_the_cluster():
    ids, matrix = blobs()
    result = cluster_items(ids, matrix)
    for cluster in result.clusters:
        assert cluster.exemplar_item_ids
        assert set(cluster.exemplar_item_ids) <= set(cluster.core_item_ids)


def test_clusters_are_unlabelled_until_members_are_inspected():
    # Brief §21 mandates the order: cluster, inspect, then label.
    ids, matrix = blobs()
    for cluster in cluster_items(ids, matrix).clusters:
        assert cluster.label is None
        assert cluster.description is None


def test_noise_fraction_is_reported():
    ids, matrix = blobs()
    result = cluster_items(ids, matrix)
    assert 0.0 <= result.noise_fraction <= 1.0


# ── cross-run identity ────────────────────────────────────────────────────────


def test_same_cluster_across_runs_is_recognised_as_a_continuation():
    ids, matrix = blobs()
    result = cluster_items(ids, matrix)
    cluster = result.clusters[0]
    previous = [("stable-key-1", cluster.centroid.copy(), cluster.member_set)]

    matches = match_clusters([cluster], previous)
    continuation = next(m for m in matches if m.kind is LineageKind.CONTINUATION)
    assert continuation.previous_key == "stable-key-1"
    assert cluster.stable_key == "stable-key-1"


def test_unrelated_cluster_is_a_birth_not_a_continuation():
    ids, matrix = blobs()
    result = cluster_items(ids, matrix)
    cluster = result.clusters[0]
    far = np.zeros_like(cluster.centroid)
    far[-1] = 1.0
    previous = [("other-key", far, {"completely", "different", "members"})]

    matches = match_clusters([cluster], previous)
    assert matches[0].kind is LineageKind.BIRTH
    assert cluster.stable_key is None


def test_disappeared_cluster_is_recorded_as_a_death():
    ids, matrix = blobs()
    cluster = cluster_items(ids, matrix).clusters[0]
    gone = np.zeros_like(cluster.centroid)
    gone[-1] = 1.0
    matches = match_clusters([cluster], [("vanished", gone, {"x", "y"})])
    assert any(m.kind is LineageKind.DEATH and m.previous_key == "vanished" for m in matches)


def test_one_previous_cluster_cannot_be_claimed_twice():
    ids, matrix = blobs()
    clusters = cluster_items(ids, matrix).clusters
    shared = clusters[0]
    previous = [("only-key", shared.centroid.copy(), shared.member_set)]
    matches = match_clusters(clusters, previous)
    continuations = [m for m in matches if m.kind is LineageKind.CONTINUATION]
    assert len(continuations) <= 1


def test_growth_alone_does_not_break_continuity():
    # A cluster that doubles shares only half its members, so jaccard drops --
    # but the centroid barely moves. Weighting both is what keeps identity.
    ids, matrix = blobs()
    result = cluster_items(ids, matrix)
    cluster = result.clusters[0]
    half = set(list(cluster.member_set)[: max(1, len(cluster.member_set) // 2)])
    matches = match_clusters([cluster], [("k", cluster.centroid.copy(), half)])
    assert matches[0].kind is LineageKind.CONTINUATION
    assert matches[0].score >= CONTINUATION_THRESHOLD


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_clustering_is_deterministic(seed):
    ids, matrix = blobs(seed=seed)
    a = cluster_items(ids, matrix)
    b = cluster_items(ids, matrix)
    assert [c.core_item_ids for c in a.clusters] == [c.core_item_ids for c in b.clusters]
