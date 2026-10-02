"""Content clustering and stable cluster identity (brief §21).

Order matters and is mandated: cluster first, **inspect the members, then**
label. Naming a cluster before looking at what is in it is how a plausible label
gets attached to an incoherent group.

Two problems this module exists to solve:

1. ``sklearn.cluster.HDBSCAN`` is a hard partition with no soft-membership
   output, but brief §21 requires an item to be able to belong to several
   thematic groups. Secondary membership is therefore derived from distance to
   other cluster centroids.
2. Clusters are **recomputed** as the corpus grows, so raw HDBSCAN labels are
   not comparable between iterations. Without stable identity the UI would
   report re-clustering churn as if it were a theme growing or dying.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
import structlog
from sklearn.cluster import HDBSCAN
from sklearn.decomposition import PCA

log = structlog.get_logger(__name__)

# A cluster must be large enough that a theme claim about it is not a claim
# about three videos. Scales with the corpus so a large run does not fragment.
MIN_CLUSTER_FLOOR = 8
MIN_CLUSTER_FRACTION = 0.005

# Below this the corpus cannot support clustering at all; reporting themes from
# fewer items would be theatre.
MIN_CORPUS_FOR_CLUSTERING = 60

# Lower bound on min_samples. Below 3 a pair of near-identical items becomes
# its own "theme".
MIN_SAMPLES_FLOOR = 3

# Dimensions to reduce to before clustering.
#
# HDBSCAN estimates density from distances, and in 1536 dimensions those
# distances flatten enough that most points look equally isolated. Measured on a
# real 101-item corpus at an unchanged theme floor: clustering the raw vectors
# left 73% of items as noise, while reducing to 40 components left 49% -- the
# same themes, with far more of their genuine members correctly attached.
#
# PCA rather than UMAP: UMAP would likely separate better, but it pulls in numba
# (the heaviest dependency in the project) and is an optional extra here, so the
# default path must work without it. PCA is linear, so it can flatten a
# distinction a nonlinear method would keep -- a real cost, accepted knowingly.
REDUCED_DIMS = 40

# Cross-run identity. Jaccard alone is fooled by growth (a cluster that doubles
# shares only half its members); centroid cosine alone is fooled by two genuinely
# distinct themes that sit close together. Both, weighted, is more robust.
IDENTITY_COS_WEIGHT = 0.6
IDENTITY_JACCARD_WEIGHT = 0.4
CONTINUATION_THRESHOLD = 0.72
CONTINUATION_MIN_COS = 0.85
CONTINUATION_MIN_JACCARD = 0.30

# An item within this cosine distance of another cluster's centroid is also a
# secondary member of it.
SECONDARY_MEMBERSHIP_COS = 0.80


class MembershipKind(StrEnum):
    CORE = "core"
    SECONDARY = "secondary"
    NOISE = "noise"


class LineageKind(StrEnum):
    CONTINUATION = "continuation"
    BIRTH = "birth"
    DEATH = "death"
    SPLIT = "split"
    MERGE = "merge"


@dataclass
class Membership:
    item_id: str
    cluster_index: int
    kind: MembershipKind
    probability: float = 0.0


@dataclass
class Cluster:
    index: int
    centroid: np.ndarray
    core_item_ids: list[str] = field(default_factory=list)
    secondary_item_ids: list[str] = field(default_factory=list)
    exemplar_item_ids: list[str] = field(default_factory=list)
    # Assigned only after the members are inspected (brief §21).
    label: str | None = None
    description: str | None = None
    stable_key: str | None = None

    @property
    def size(self) -> int:
        return len(self.core_item_ids)

    @property
    def member_set(self) -> set[str]:
        return set(self.core_item_ids)


@dataclass
class ClusteringResult:
    clusters: list[Cluster]
    memberships: list[Membership]
    noise_item_ids: list[str]
    n_items: int
    min_cluster_size: int
    skipped_reason: str | None = None

    @property
    def noise_fraction(self) -> float:
        return len(self.noise_item_ids) / self.n_items if self.n_items else 0.0

    @property
    def ran(self) -> bool:
        return self.skipped_reason is None


def min_cluster_size_for(n: int) -> int:
    return max(MIN_CLUSTER_FLOOR, int(np.ceil(MIN_CLUSTER_FRACTION * n)))


def _l2_normalise(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def cluster_items(
    item_ids: list[str], embeddings: list[list[float]] | np.ndarray
) -> ClusteringResult:
    """Partition the corpus, then derive secondary memberships.

    Embeddings are L2-normalised so Euclidean distance is a monotone function of
    cosine distance; HDBSCAN then runs on euclidean, which is the metric its
    implementation is fastest and best-behaved on.
    """
    n = len(item_ids)
    if n < MIN_CORPUS_FOR_CLUSTERING:
        return ClusteringResult(
            clusters=[],
            memberships=[],
            noise_item_ids=list(item_ids),
            n_items=n,
            min_cluster_size=min_cluster_size_for(n),
            skipped_reason=(
                f"corpus of {n} items is below the {MIN_CORPUS_FOR_CLUSTERING}-item floor; "
                "themes from fewer items would not be reportable"
            ),
        )

    matrix = _l2_normalise(np.asarray(embeddings, dtype=np.float64))
    # Centroids and secondary membership are computed in the ORIGINAL space, so
    # cross-run identity stays comparable even if REDUCED_DIMS changes; only the
    # density estimate runs on the reduced view.
    clustering_space = matrix
    n_components = min(REDUCED_DIMS, n - 1, matrix.shape[1])
    if n_components >= 2 and matrix.shape[1] > n_components:
        reduced = PCA(n_components=n_components, random_state=0).fit_transform(matrix)
        clustering_space = _l2_normalise(reduced)

    min_size = min_cluster_size_for(n)
    model = HDBSCAN(
        min_cluster_size=min_size,
        # min_samples is decoupled from min_cluster_size. Setting them equal is
        # the conservative-looking choice and it is too strict: measured on a
        # real 67-item corpus it produced ZERO clusters and 100% noise, while
        # min_samples=4 found two. It controls how dense a neighbourhood must be
        # before a point counts as core, so tying it to the reportable-theme
        # floor conflates two unrelated questions.
        #
        # Half the cluster size, floored at 3, keeps the bias toward calling an
        # ambiguous point noise without refusing to cluster at all.
        min_samples=max(MIN_SAMPLES_FLOOR, min_size // 2),
        metric="euclidean",
        cluster_selection_method="eom",
        store_centers="both",
        # Explicit: sklearn 1.10 flips this default. Copying keeps the caller's
        # matrix intact, which matters because the same embeddings are reused
        # for secondary membership and for cross-run centroid matching.
        copy=True,
    )
    labels = model.fit_predict(clustering_space)
    probabilities = getattr(model, "probabilities_", np.ones(n))

    clusters: list[Cluster] = []
    memberships: list[Membership] = []
    noise: list[str] = []

    for cluster_index in sorted({int(x) for x in labels if x >= 0}):
        mask = labels == cluster_index
        members = [item_ids[i] for i in np.flatnonzero(mask)]
        centroid = _l2_normalise(matrix[mask].mean(axis=0, keepdims=True))[0]
        cluster = Cluster(index=cluster_index, centroid=centroid, core_item_ids=members)
        # Exemplars are the members closest to the centroid; they are what a
        # labelling pass is shown first.
        similarities = matrix[mask] @ centroid
        order = np.argsort(-similarities)
        cluster.exemplar_item_ids = [members[i] for i in order[:5]]
        clusters.append(cluster)
        for i in np.flatnonzero(mask):
            memberships.append(
                Membership(item_ids[i], cluster_index, MembershipKind.CORE, float(probabilities[i]))
            )

    for i in np.flatnonzero(labels < 0):
        noise.append(item_ids[i])
        memberships.append(Membership(item_ids[i], -1, MembershipKind.NOISE, 0.0))

    # Secondary membership: HDBSCAN gives a hard partition, but brief §21 allows
    # an item to belong to several thematic groups.
    if clusters:
        centroids = np.vstack([c.centroid for c in clusters])
        similarity = matrix @ centroids.T
        for row, item_id in enumerate(item_ids):
            primary = int(labels[row])
            for col, cluster in enumerate(clusters):
                if cluster.index == primary:
                    continue
                if similarity[row, col] >= SECONDARY_MEMBERSHIP_COS:
                    cluster.secondary_item_ids.append(item_id)
                    memberships.append(
                        Membership(
                            item_id,
                            cluster.index,
                            MembershipKind.SECONDARY,
                            float(similarity[row, col]),
                        )
                    )

    result = ClusteringResult(
        clusters=clusters,
        memberships=memberships,
        noise_item_ids=noise,
        n_items=n,
        min_cluster_size=min_size,
    )
    log.info(
        "clustering.done",
        n=n,
        clusters=len(clusters),
        min_cluster_size=min_size,
        noise_fraction=round(result.noise_fraction, 3),
    )
    return result


@dataclass
class IdentityMatch:
    current_index: int
    previous_key: str | None
    kind: LineageKind
    score: float
    cosine: float
    jaccard: float


def match_clusters(
    current: list[Cluster],
    previous: list[tuple[str, np.ndarray, set[str]]],
) -> list[IdentityMatch]:
    """Assign stable keys across iterations.

    Without this, a cluster recomputed under a different HDBSCAN label looks
    like a death plus a birth, and the UI would report a theme appearing or
    vanishing that never did either.
    """
    matches: list[IdentityMatch] = []
    claimed: set[str] = set()

    for cluster in current:
        best: tuple[float, float, float, str | None] = (0.0, 0.0, 0.0, None)
        for key, centroid, members in previous:
            if key in claimed:
                continue
            cosine = float(np.dot(cluster.centroid, centroid))
            union = cluster.member_set | members
            jac = len(cluster.member_set & members) / len(union) if union else 0.0
            score = IDENTITY_COS_WEIGHT * cosine + IDENTITY_JACCARD_WEIGHT * jac
            if score > best[0]:
                best = (score, cosine, jac, key)

        score, cosine, jac, key = best
        continues = (
            key is not None
            and score >= CONTINUATION_THRESHOLD
            and (cosine >= CONTINUATION_MIN_COS or jac >= CONTINUATION_MIN_JACCARD)
        )
        if continues and key is not None:
            claimed.add(key)
            cluster.stable_key = key
            matches.append(
                IdentityMatch(cluster.index, key, LineageKind.CONTINUATION, score, cosine, jac)
            )
        else:
            matches.append(
                IdentityMatch(cluster.index, None, LineageKind.BIRTH, score, cosine, jac)
            )

    for key, _, _ in previous:
        if key not in claimed:
            matches.append(IdentityMatch(-1, key, LineageKind.DEATH, 0.0, 0.0, 0.0))
    return matches
