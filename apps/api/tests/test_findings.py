"""Finding validation is the anti-fabrication boundary.

Code computes every number; the model only writes prose and may cite only the
identifiers it was handed. These tests pin that boundary.
"""

import pytest

from aypo.intelligence.findings import (
    ClaimType,
    ComputedStat,
    ConfidenceBand,
    DraftFinding,
    EvidencePacket,
    EvidenceRef,
    RejectionReason,
    render_claim,
    score_confidence,
    validate_draft,
)
from aypo.intelligence.independence import Independence


def packet(**kw) -> EvidencePacket:
    base = dict(
        population_frame="items analysed in this run",
        statistics=[
            ComputedStat(
                "S1",
                "share of cluster Y",
                0.34,
                "34%",
                17,
                50,
                0.22,
                0.48,
                "wilson_deff",
                "n_theme/n_corpus",
            ),
        ],
        evidence=[
            EvidenceRef("E1", "content", "tiktok:1", "a packet on a table"),
            EvidenceRef("E2", "content", "tiktok:2", "a packet held up"),
        ],
        independence=Independence(50, 40.0, 1.25, 22, 0.10, 7, 0.20),
    )
    base.update(kw)
    return EvidencePacket(**base)


def draft(**kw) -> DraftFinding:
    base = dict(
        claim_template="Theme X appears in {{stat:S1}} of analysed items [E1][E2].",
        claim_type=ClaimType.COMPUTED,
        scope_statement="Within the analysed sample.",
        interpretation="May be worth investigating further.",
        what_would_refute="A larger sample where the share falls below the interval.",
        follow_up_queries=[],
        contested=False,
    )
    base.update(kw)
    return DraftFinding(**base)


def test_well_formed_finding_is_accepted():
    assert validate_draft(draft(), packet()).accepted


def test_citing_an_unknown_statistic_is_rejected():
    d = draft(claim_template="Theme X appears in {{stat:S9}} of items [E1].")
    outcome = validate_draft(d, packet())
    assert not outcome.accepted
    assert RejectionReason.UNKNOWN_STATISTIC in outcome.reasons


def test_citing_unknown_evidence_is_rejected():
    d = draft(claim_template="Theme X appears in {{stat:S1}} of items [E99].")
    outcome = validate_draft(d, packet())
    assert RejectionReason.UNKNOWN_EVIDENCE in outcome.reasons


def test_a_finding_with_no_citation_is_rejected():
    d = draft(claim_template="Theme X appears in {{stat:S1}} of analysed items.")
    assert RejectionReason.NO_EVIDENCE in validate_draft(d, packet()).reasons


def test_a_number_the_model_wrote_itself_is_rejected():
    # THE central failure: a plausible number that nothing computed.
    d = draft(claim_template="Theme X appears in 42% of analysed items [E1].")
    outcome = validate_draft(d, packet())
    assert not outcome.accepted
    assert RejectionReason.UNCITED_NUMBER in outcome.reasons


@pytest.mark.parametrize(
    "text",
    [
        "Most consumers prefer Theme X [E1].",
        "This proves Theme X is dominant [E1].",
        "Theme X causes higher purchase intent [E1].",
        "Theme X drives sales for Brand A [E1].",
        "Brand A leads on market share [E1].",
        "All shoppers encounter Theme X [E1].",
    ],
)
def test_forbidden_language_is_rejected(text):
    outcome = validate_draft(draft(claim_template=text), packet())
    assert RejectionReason.FORBIDDEN_LANGUAGE in outcome.reasons


def test_rendering_substitutes_computed_values():
    rendered = render_claim(draft(), packet())
    assert "34%" in rendered
    assert "{{stat:" not in rendered


# ── confidence ────────────────────────────────────────────────────────────────


def test_concentrated_sample_scores_lower_than_diverse_one():
    diverse = packet(independence=Independence(50, 45.0, 1.1, 30, 0.08, 8, 0.15))
    concentrated = packet(independence=Independence(50, 20.0, 2.5, 3, 0.70, 1, 0.95))
    # 50 items from 3 creators found by 1 query is not 50 independent observations.
    assert score_confidence(diverse)[0] > score_confidence(concentrated)[0]


def test_tiny_sample_is_insufficient_whatever_the_effect():
    p = packet(independence=Independence(6, 5.0, 1.2, 2, 0.4, 1, 1.0))
    assert score_confidence(p, effect_size=0.9)[1] is ConfidenceBand.INSUFFICIENT


def test_empty_packet_is_insufficient():
    assert score_confidence(packet(independence=None))[1] is ConfidenceBand.INSUFFICIENT


def test_bands_are_ordered():
    strong = packet(independence=Independence(200, 160.0, 1.25, 60, 0.05, 10, 0.12))
    weak = packet(independence=Independence(30, 20.0, 1.5, 5, 0.22, 2, 0.60))
    strong_score, strong_band = score_confidence(strong, effect_size=0.4)
    weak_score, _ = score_confidence(weak, effect_size=0.05)
    assert strong_score > weak_score
    assert strong_band in (ConfidenceBand.HIGH, ConfidenceBand.MODERATE)


def test_packet_render_lists_only_real_refs():
    rendered = packet().render()
    assert "{{stat:S1}}" in rendered and "[E1]" in rendered
    assert "S9" not in rendered and "E99" not in rendered


def test_competing_evidence_is_shown_to_the_generator():
    p = packet(competing_evidence=[EvidenceRef("E3", "content", "tiktok:3", "does not fit")])
    rendered = p.render()
    # Brief §28: conflicting evidence is surfaced, not suppressed.
    assert "COMPETING EVIDENCE" in rendered
    assert "E3" in p.evidence_refs
