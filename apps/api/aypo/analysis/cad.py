"""The Canonical Analysis Document (CAD).

What gets embedded for clustering. **Not** the caption, not the transcript, and
deliberately **not** the model's prose `summary`.

Embedding the prose summary is the obvious choice and the wrong one: summaries
written by one model in one voice cluster by writing style and sentence shape as
much as by content, so two items described in similar prose land together even
when what they show is unrelated. A field-ordered rendering of the *structured*
analysis has no such free variation -- every item is rendered by the same
template, so the only thing that differs between two documents is what was
actually observed in them.

Rendering is deterministic: same analysis in, byte-identical document out. The
document's hash is the embedding cache key, so any non-determinism here would
silently re-embed the whole corpus.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from aypo.analysis.schema import Facet, ItemAnalysis

CAD_TEMPLATE_VERSION = "cad-v1"

# Facet order is fixed, not alphabetical: related facets sit together so a
# truncated document loses the least informative tail first.
FACET_ORDER: tuple[Facet, ...] = (
    Facet.CONTENT_FORMAT,
    Facet.CREATOR_ARCHETYPE,
    Facet.FORMAT_FEATURE,
    Facet.OCCASION,
    Facet.SOCIAL_CONTEXT,
    Facet.SETTING,
    Facet.ACTIVITY,
    Facet.BEHAVIOR,
    Facet.STATED_USE,
    Facet.PRODUCT_PAIRING,
    Facet.CULTURAL_SIGNAL,
    Facet.SLANG,
    Facet.HUMOR_SIGNAL,
    Facet.LANGUAGE,
    Facet.SENTIMENT,
    Facet.PURCHASE_INTENT,
    Facet.BRAND_CO_APPEARANCE,
    Facet.UNEXPECTED,
)

# Roughly 8000 characters keeps a document inside a single embedding request
# with headroom, and almost no honest analysis reaches it.
MAX_CAD_CHARS = 8000


@dataclass
class CAD:
    text: str
    content_hash: str
    template_version: str
    dropped_fields: list[str]
    truncated: bool

    @property
    def token_estimate(self) -> int:
        return len(self.text) // 4


def _render_values(values: list[str]) -> str:
    # Sorted so two analyses that found the same things in a different order
    # produce the same document.
    return "; ".join(sorted(dict.fromkeys(v.strip() for v in values if v.strip())))


def build_cad(
    analysis: ItemAnalysis,
    *,
    include_brands: bool = True,
    redact_brands: set[str] | None = None,
) -> CAD:
    """Render one analysis to its canonical document.

    ``redact_brands`` exists for a specific experiment: clustering with brand
    names removed shows whether a cluster is a genuine behavioural theme or
    merely "items that mention Brand A". A theme that evaporates under redaction
    was never a theme.
    """
    redact = {b.lower() for b in (redact_brands or set())}
    lines: list[str] = []
    dropped: list[str] = []

    if include_brands:
        brands = [
            b.surface_form
            for b in analysis.visible_brands
            if b.brand_resolved and b.surface_form.lower() not in redact
        ]
        mentioned = [
            b.surface_form
            for b in analysis.mentioned_brands
            if b.brand_resolved and b.surface_form.lower() not in redact
        ]
        if brands:
            lines.append(f"visible_brands: {_render_values(brands)}")
        if mentioned:
            lines.append(f"mentioned_brands: {_render_values(mentioned)}")

    # Products carry their consumption state, because "a pack is on the table"
    # and "a pack is being eaten" are different observations that must not
    # collapse to the same point in the space.
    products = [f"{p.description} [{p.consumption_state.value}]" for p in analysis.visible_products]
    if products:
        lines.append(f"products: {_render_values(products)}")

    for facet in FACET_ORDER:
        values = [label.value for label in analysis.by_facet(facet)]
        if values:
            lines.append(f"{facet.value}: {_render_values(values)}")
        else:
            dropped.append(facet.value)

    if analysis.sponsorship.bucket.value != "undetermined":
        lines.append(f"sponsorship: {analysis.sponsorship.bucket.value}")

    text = "\n".join(lines)
    truncated = len(text) > MAX_CAD_CHARS
    if truncated:
        text = text[:MAX_CAD_CHARS]

    return CAD(
        text=text,
        content_hash=hashlib.sha256(f"{CAD_TEMPLATE_VERSION}\n{text}".encode()).hexdigest(),
        template_version=CAD_TEMPLATE_VERSION,
        dropped_fields=dropped,
        truncated=truncated,
    )
