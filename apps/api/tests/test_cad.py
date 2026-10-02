"""The Canonical Analysis Document.

What gets embedded decides what clusters together. The obvious choice — the
model's prose summary — is the wrong one: summaries written by one model in one
voice cluster by writing style as much as by content.
"""

from aypo.analysis.cad import CAD_TEMPLATE_VERSION, build_cad
from aypo.analysis.schema import (
    Basis,
    BrandSighting,
    ConsumptionState,
    Facet,
    ItemAnalysis,
    Label,
    Legibility,
    MediaTier,
    ProductSighting,
    Sponsorship,
    SponsorshipBucket,
)


def analysis(**kw) -> ItemAnalysis:
    base = dict(
        media_tier=MediaTier.FULL_VIDEO,
        visible_brands=[
            BrandSighting(
                surface_form="Brand A",
                basis=Basis.SEEN,
                cue="wordmark legible on the pack",
                anchors=["frame:1"],
                legibility=Legibility.FULL,
                brand_resolved=True,
            )
        ],
        mentioned_brands=[],
        visible_products=[
            ProductSighting(
                description="a foil pack",
                brand_surface_form="Brand A",
                consumption_state=ConsumptionState.PRESENT,
                cue="pack resting on the surface",
                anchors=["frame:1"],
                has_legible_text_region=False,
            )
        ],
        labels=[
            Label(
                facet=Facet.OCCASION,
                value="Theme X",
                basis=Basis.SEEN,
                cue="several people seated together",
                anchors=["frame:1"],
            )
        ],
        sponsorship=Sponsorship(bucket=SponsorshipBucket.UNDETERMINED, cues=[]),
        evidence_insufficient_for=[],
        summary="A long piece of model prose that must never reach the embedding.",
    )
    base.update(kw)
    return ItemAnalysis(**base)


def test_prose_summary_is_never_embedded():
    # Embedding the summary clusters by writing style rather than content.
    cad = build_cad(analysis())
    assert "model prose" not in cad.text
    assert "Theme X" in cad.text


def test_rendering_is_deterministic():
    # The document hash is the embedding cache key, so any non-determinism here
    # silently re-embeds the whole corpus.
    a = analysis()
    assert build_cad(a).content_hash == build_cad(a).content_hash


def test_label_order_does_not_change_the_document():
    first = analysis(
        labels=[
            Label(
                facet=Facet.OCCASION, value="B", basis=Basis.SEEN, cue="cue b", anchors=["frame:1"]
            ),
            Label(
                facet=Facet.OCCASION, value="A", basis=Basis.SEEN, cue="cue a", anchors=["frame:1"]
            ),
        ]
    )
    second = analysis(
        labels=[
            Label(
                facet=Facet.OCCASION, value="A", basis=Basis.SEEN, cue="cue a", anchors=["frame:1"]
            ),
            Label(
                facet=Facet.OCCASION, value="B", basis=Basis.SEEN, cue="cue b", anchors=["frame:1"]
            ),
        ]
    )
    assert build_cad(first).content_hash == build_cad(second).content_hash


def test_consumption_state_travels_with_the_product():
    # "a pack is on the table" and "a pack is being eaten" are different
    # observations and must not collapse to the same point in the space.
    present = build_cad(analysis()).text
    assert "[present]" in present

    used = analysis(
        visible_products=[
            ProductSighting(
                description="a foil pack",
                brand_surface_form="Brand A",
                consumption_state=ConsumptionState.IN_USE_OR_CONSUMED,
                cue="contents tipped toward the mouth",
                anchors=["frame:1", "frame:2"],
                has_legible_text_region=False,
            )
        ]
    )
    assert "[in_use_or_consumed]" in build_cad(used).text
    assert build_cad(used).content_hash != build_cad(analysis()).content_hash


def test_brand_redaction_changes_the_document():
    # Clustering with brands removed is how you tell a behavioural theme from
    # "items that happen to mention Brand A".
    full = build_cad(analysis())
    redacted = build_cad(analysis(), redact_brands={"Brand A"})
    assert "visible_brands" not in redacted.text
    assert redacted.content_hash != full.content_hash


def test_empty_facets_are_reported_as_dropped():
    cad = build_cad(analysis())
    assert "slang" in cad.dropped_fields
    assert cad.template_version == CAD_TEMPLATE_VERSION


def test_an_analysis_with_nothing_in_it_still_renders():
    bare = analysis(visible_brands=[], visible_products=[], labels=[])
    cad = build_cad(bare)
    assert cad.content_hash
    assert cad.truncated is False
