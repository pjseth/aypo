"""Cluster labelling (brief §21).

Clusters are labelled only **after** their members have been inspected. The
order is the point: a label proposed before looking at the members is a
hypothesis about what the cluster ought to contain, and it will stick whether or
not the members support it.

Members shown to the labeller are sampled from the centre AND the periphery. A
centre-only sample makes every cluster look tighter than it is, and the label
that results describes the core while silently claiming the edges too.

The labeller is also allowed to answer "this is not a coherent group". A cluster
that only exists because the algorithm had to put those points somewhere should
be marked incoherent rather than given a name that implies a theme.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import structlog
from pydantic import BaseModel, Field

from aypo.config import get_settings
from aypo.intelligence.clustering import Cluster
from aypo.llm.client import LLMClient, LLMRefused, LLMSchemaError

log = structlog.get_logger(__name__)

# How many members the labeller sees. Enough to judge coherence, few enough that
# the prompt stays inside a sensible budget across hundreds of clusters.
N_CENTRAL = 8
N_PERIPHERAL = 4


class ClusterLabel(BaseModel):
    coherent: bool = Field(
        description=(
            "false when the shown members do not share anything specific enough to name; "
            "this is a legitimate and useful answer"
        )
    )
    label: str = Field(
        description=(
            "2-6 words naming ONLY what the shown members actually share; empty when incoherent"
        )
    )
    description: str = Field(
        description="one or two sentences stating what is common across the members"
    )
    shared_attributes: list[str] = Field(
        description="the specific observations present across most members", max_length=8
    )
    dissenting_members: list[int] = Field(
        description="indices of shown members that do NOT fit the label", max_length=12
    )
    confidence: float = Field(ge=0.0, le=1.0)


LABEL_SYSTEM = """You are shown a sample of members from one cluster of analysed content. Each
member is a structured description of what was observed in one item.

Your job is to name what these members ACTUALLY share, or to say that they share nothing nameable.

Rules:

1. Describe only what is present in the members shown. Do not reach for a category, occasion,
   audience or cultural concept that is not visible in them. If you find yourself drawing on what
   you know about this kind of product rather than on what is written in front of you, stop.
2. The sample deliberately includes members from the edge of the cluster as well as its centre.
   If the edge members do not fit, your label is too narrow, or the cluster is not coherent. List
   the ones that do not fit in `dissenting_members`.
3. `coherent: false` is a correct answer. Clustering algorithms must assign every point somewhere,
   so some groups are artefacts. Saying so is more useful than naming one.
4. A label is 2-6 words and names a pattern, not a topic. Prefer what the members are DOING or
   SHOWING over what they are ABOUT.
5. Do not name a brand in the label unless the shared attribute genuinely IS that brand's presence.
   A cluster of items that merely happen to contain the same brand is usually better described by
   what is happening in them.
6. Do not describe the cluster as growing, popular, emerging or important. You are shown a sample
   from one moment with no time information and no baseline.

Return only the structured object."""


@dataclass
class MemberSample:
    indices: list[int]
    documents: list[str]
    peripheral_indices: set[int]


def sample_members(
    cluster: Cluster,
    documents: dict[str, str],
    embeddings: dict[str, np.ndarray],
    *,
    n_central: int = N_CENTRAL,
    n_peripheral: int = N_PERIPHERAL,
) -> MemberSample:
    """Members from the centre and the edge.

    A centre-only sample flatters the cluster: it shows the most typical items
    and hides whatever the label will quietly over-claim.
    """
    members = [m for m in cluster.core_item_ids if m in documents and m in embeddings]
    if not members:
        return MemberSample([], [], set())

    sims = np.array([float(np.dot(embeddings[m], cluster.centroid)) for m in members])
    order = np.argsort(-sims)
    central = [members[i] for i in order[:n_central]]
    peripheral = [members[i] for i in order[-n_peripheral:] if members[i] not in central]

    chosen = central + peripheral
    peripheral_positions = {len(central) + i for i in range(len(peripheral))}
    return MemberSample(
        indices=list(range(len(chosen))),
        documents=[documents[m] for m in chosen],
        peripheral_indices=peripheral_positions,
    )


def render_members(sample: MemberSample) -> str:
    lines = [
        "# CLUSTER MEMBERS",
        "Members marked [edge] are from the periphery of the cluster. If they do not fit your",
        "label, the cluster is narrower than it looks or is not coherent.",
        "",
    ]
    for i, document in enumerate(sample.documents):
        tag = " [edge]" if i in sample.peripheral_indices else ""
        lines.append(f"--- member {i}{tag} ---")
        lines.append(document)
        lines.append("")
    return "\n".join(lines)


async def label_cluster(
    cluster: Cluster,
    documents: dict[str, str],
    embeddings: dict[str, np.ndarray],
    *,
    client: LLMClient | None = None,
) -> ClusterLabel | None:
    """Inspect members, then label. Returns None when labelling failed."""
    sample = sample_members(cluster, documents, embeddings)
    if not sample.documents:
        return None

    settings = get_settings()
    llm = client or LLMClient()
    try:
        result = await llm.structured(
            ClusterLabel,
            system=LABEL_SYSTEM,
            content=render_members(sample),
            effort=settings.aypo_reasoning_effort,
            max_tokens=2000,
            label="cluster_label",
        )
    except (LLMRefused, LLMSchemaError) as exc:
        log.warning("labeling.failed", cluster=cluster.index, error=str(exc)[:160])
        return None

    verdict = result.value
    # A cluster whose own sample largely dissents is not coherent, whatever the
    # model concluded about it.
    if verdict.coherent and len(verdict.dissenting_members) > len(sample.documents) / 2:
        verdict.coherent = False
        verdict.confidence = min(verdict.confidence, 0.3)

    if verdict.coherent:
        cluster.label = verdict.label
        cluster.description = verdict.description
    log.info(
        "labeling.done",
        cluster=cluster.index,
        coherent=verdict.coherent,
        label=verdict.label[:40],
        dissent=len(verdict.dissenting_members),
    )
    return verdict
