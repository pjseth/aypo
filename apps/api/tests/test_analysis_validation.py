"""The validator is the anti-hallucination mechanism.

A model can invent a frame index or paraphrase a quote. Brief §28 forbids
hallucinated evidence reaching a finding, so anchors are checked against the
material that was actually supplied rather than trusted.
"""

import pytest

from aypo.analysis.media import SelectedFrame
from aypo.analysis.multimodal import AnalysisMaterial, validate, validate_anchor
from aypo.analysis.schema import (
    AnchorKind,
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
    SponsorshipCue,
    parse_anchor,
)
from aypo.providers.transcription import Segment, Transcript


def material(**kw) -> AnalysisMaterial:
    base = dict(
        frames=[SelectedFrame(i, float(i), 0.0, "bucket_midpoint") for i in range(4)],
        transcript=Transcript(
            text="this is the spoken line",
            segments=[Segment(5.0, 8.0, "this is the spoken line")],
        ),
        caption="a caption mentioning Brand A",
        overlay_text="",
        comments_text="",
        media_tier=MediaTier.FULL_VIDEO,
    )
    base.update(kw)
    return AnalysisMaterial(**base)


def analysis(**kw) -> ItemAnalysis:
    base = dict(
        media_tier=MediaTier.FULL_VIDEO,
        visible_brands=[],
        mentioned_brands=[],
        visible_products=[],
        labels=[],
        sponsorship=Sponsorship(bucket=SponsorshipBucket.UNDETERMINED, cues=[]),
        evidence_insufficient_for=[],
        summary="",
    )
    base.update(kw)
    return ItemAnalysis(**base)


def label(**kw) -> Label:
    base = dict(
        facet=Facet.OCCASION,
        value="Theme X",
        basis=Basis.SEEN,
        cue="a distinctive object is on the table at the left edge",
        anchors=["frame:1"],
    )
    base.update(kw)
    return Label(**base)


# ── anchor parsing and checking ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,kind",
    [
        ("frame:2", AnchorKind.FRAME),
        ("transcript:5.0-8.0:this is the spoken line", AnchorKind.TRANSCRIPT),
        ("caption:a caption mentioning Brand A", AnchorKind.CAPTION),
    ],
)
def test_valid_anchors_are_accepted(raw, kind):
    anchor = parse_anchor(raw)
    assert anchor is not None and anchor.kind is kind
    assert validate_anchor(anchor, material()) is True


def test_invented_frame_index_is_rejected():
    # Only indices listed in the manifest exist.
    assert validate_anchor(parse_anchor("frame:99"), material()) is False


def test_timestamp_outside_any_segment_is_rejected():
    assert validate_anchor(parse_anchor("transcript:40.0-41.0:whatever"), material()) is False


def test_paraphrased_quote_is_rejected():
    # This is the central failure mode: a quote that merely resembles the source.
    a = parse_anchor("transcript:5.0-8.0:this is roughly the spoken line")
    assert validate_anchor(a, material()) is False


def test_verbatim_substring_is_accepted_ignoring_case_and_spacing():
    a = parse_anchor("transcript:5.0-8.0:THIS IS   the spoken")
    assert validate_anchor(a, material()) is True


def test_malformed_anchor_strings_do_not_crash():
    for raw in ("", "nonsense", "frame:", "frame:abc", "transcript:bad"):
        assert parse_anchor(raw) is None


# ── claim validation ──────────────────────────────────────────────────────────


def test_claim_without_a_valid_anchor_is_dropped():
    a = analysis(labels=[label(anchors=["frame:99"])])
    report = validate(a, material())
    assert a.labels == []
    assert any("no valid anchor" in d for d in report.dropped)


def test_cue_that_restates_the_claim_is_dropped():
    # If the only cue is a reword of the claim, the claim has no evidence.
    a = analysis(labels=[label(value="Theme X", cue="Theme X")])
    validate(a, material())
    assert a.labels == []


def test_basis_seen_requires_a_frame_anchor():
    a = analysis(labels=[label(basis=Basis.SEEN, anchors=["caption:a caption mentioning Brand A"])])
    validate(a, material())
    assert a.labels == []


def test_basis_heard_requires_a_transcript_anchor():
    a = analysis(labels=[label(basis=Basis.HEARD, anchors=["frame:1"])])
    validate(a, material())
    assert a.labels == []


def test_well_anchored_claim_survives():
    a = analysis(labels=[label()])
    report = validate(a, material())
    assert len(a.labels) == 1
    assert report.clean


def test_brand_sighting_with_invented_anchor_is_dropped():
    a = analysis(
        visible_brands=[
            BrandSighting(
                surface_form="Brand A",
                basis=Basis.SEEN,
                cue="wordmark readable on the pack at centre frame",
                anchors=["frame:77"],
                legibility=Legibility.FULL,
                brand_resolved=True,
            )
        ]
    )
    validate(a, material())
    assert a.visible_brands == []


# ── consumption-state escalation (brief §28) ──────────────────────────────────


def product(state, anchors):
    return ProductSighting(
        description="a packaged item",
        brand_surface_form=None,
        consumption_state=state,
        cue="the pack is on the surface near the left edge",
        anchors=anchors,
        has_legible_text_region=False,
    )


def test_single_frame_cannot_evidence_an_action():
    a = analysis(visible_products=[product(ConsumptionState.IN_USE_OR_CONSUMED, ["frame:1"])])
    report = validate(a, material())
    # Demoted, not dropped: the object really was present.
    assert a.visible_products[0].consumption_state is ConsumptionState.PRESENT
    assert any("single frame" in d for d in report.demoted)


def test_two_frames_can_support_an_action():
    a = analysis(
        visible_products=[product(ConsumptionState.IN_USE_OR_CONSUMED, ["frame:1", "frame:2"])]
    )
    validate(a, material())
    assert a.visible_products[0].consumption_state is ConsumptionState.IN_USE_OR_CONSUMED


def test_speech_alone_cannot_evidence_use():
    a = analysis(
        visible_products=[
            product(
                ConsumptionState.IN_USE_OR_CONSUMED,
                ["transcript:5.0-8.0:this is the spoken line"],
            )
        ]
    )
    report = validate(a, material())
    # All the way back to `present`, not to shown_to_camera: showing something
    # to camera is itself a visual act, which audio cannot establish.
    assert a.visible_products[0].consumption_state is ConsumptionState.PRESENT
    assert any("speech alone" in d for d in report.demoted)


# ── sponsorship hard rules (brief §19) ────────────────────────────────────────


def test_likely_paid_without_an_admissible_cue_falls_back():
    a = analysis(
        sponsorship=Sponsorship(
            bucket=SponsorshipBucket.LIKELY_PAID,
            cues=[
                SponsorshipCue(
                    kind="studio_grade_product_presentation",
                    cue="even lighting",
                    anchors=[],
                )
            ],
        )
    )
    report = validate(a, material())
    # Production quality alone cannot exceed possibly_paid.
    assert a.sponsorship.bucket is SponsorshipBucket.UNDETERMINED
    assert any("likely_paid" in d for d in report.demoted)


def test_likely_paid_with_a_disclosure_cue_is_kept():
    a = analysis(
        sponsorship=Sponsorship(
            bucket=SponsorshipBucket.LIKELY_PAID,
            cues=[
                SponsorshipCue(
                    kind="disclosure_text_on_screen",
                    cue="'ad' rendered top-left",
                    anchors=["frame:1"],
                )
            ],
        )
    )
    validate(a, material())
    assert a.sponsorship.bucket is SponsorshipBucket.LIKELY_PAID


def test_likely_organic_requires_an_organic_cue():
    a = analysis(sponsorship=Sponsorship(bucket=SponsorshipBucket.LIKELY_ORGANIC, cues=[]))
    validate(a, material())
    assert a.sponsorship.bucket is SponsorshipBucket.UNDETERMINED


def test_organic_claim_is_rejected_when_a_paid_cue_is_present():
    a = analysis(
        sponsorship=Sponsorship(
            bucket=SponsorshipBucket.LIKELY_ORGANIC,
            cues=[
                SponsorshipCue(
                    kind="self_acquisition_statement",
                    cue="says they bought it",
                    anchors=[],
                ),
                SponsorshipCue(
                    kind="discount_code_or_affiliate_link",
                    cue="code on screen",
                    anchors=[],
                ),
            ],
        )
    )
    validate(a, material())
    assert a.sponsorship.bucket is SponsorshipBucket.UNDETERMINED


# ── thumbnail-only items ──────────────────────────────────────────────────────


def test_thumbnail_only_item_loses_temporal_claims():
    a = analysis(
        labels=[label(facet=Facet.ACTIVITY), label(facet=Facet.SETTING)],
        visible_products=[product(ConsumptionState.HELD, ["frame:1", "frame:2"])],
    )
    validate(a, material(media_tier=MediaTier.THUMBNAIL_ONLY))
    # One still has no timeline, so nothing unfolding over time is observable.
    assert [lab.facet for lab in a.labels] == [Facet.SETTING]
    assert a.visible_products[0].consumption_state is ConsumptionState.PRESENT
