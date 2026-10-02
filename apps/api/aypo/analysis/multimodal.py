"""Per-item multimodal analysis (brief §18-20).

Visual, audio, text and context are fused into ONE model call. Separate calls
per modality would prevent exactly the cross-modal judgements the product needs
-- "the packet is visible at frame 3 while the speaker says X at 0:12" -- and
would cost more.

After the call, every claim is validated **programmatically** against the
material that was actually supplied. The model is capable of inventing a frame
index or paraphrasing a quote, and brief §28 forbids hallucinated evidence
reaching a finding, so anchors are checked rather than trusted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher

import structlog

from aypo.analysis.media import SelectedFrame
from aypo.analysis.prompt import PROMPT_VERSION, SYSTEM_PROMPT
from aypo.analysis.schema import (
    TEMPORAL_FACETS,
    AnalysisRecord,
    Anchor,
    AnchorKind,
    Basis,
    ConsumptionState,
    ItemAnalysis,
    MediaTier,
    SponsorshipBucket,
    parse_anchor,
)
from aypo.config import get_settings
from aypo.llm.client import LLMClient, LLMRefused, LLMSchemaError, LLMTruncated
from aypo.llm.pricing import Ledger
from aypo.models.content import ContentItem
from aypo.providers.transcription import Transcript

log = structlog.get_logger(__name__)

MAX_CUE_CHARS = 160

# Rungs that describe something happening, rather than merely being present.
_ABOVE_PRESENT = {
    ConsumptionState.HELD,
    ConsumptionState.BEING_OPENED,
    ConsumptionState.SHOWN_TO_CAMERA,
    ConsumptionState.IN_USE_OR_CONSUMED,
    ConsumptionState.OFFERED_OR_SHARED,
}

_PAID_CUE_KINDS = {
    "disclosure_text_on_screen",
    "disclosure_in_caption_or_hashtag",
    "spoken_disclosure",
    "platform_paid_partnership_label",
    "discount_code_or_affiliate_link",
    "pinned_or_authored_brand_comment",
}
_ORGANIC_CUE_KINDS = {
    "no_disclosure_with_multi_brand_incidental_presence",
    "self_acquisition_statement",
}


@dataclass
class AnalysisMaterial:
    """Exactly what the model was shown. The validator checks against this."""

    frames: list[SelectedFrame]
    transcript: Transcript | None = None
    caption: str = ""
    overlay_text: str = ""
    comments_text: str = ""
    media_tier: MediaTier = MediaTier.FULL_VIDEO

    @property
    def frame_indices(self) -> set[int]:
        return {f.index for f in self.frames}

    def text_for(self, kind: AnchorKind) -> str:
        return {
            AnchorKind.CAPTION: self.caption,
            AnchorKind.OVERLAY: self.overlay_text,
            AnchorKind.COMMENTS: self.comments_text,
        }.get(kind, "")


@dataclass
class ValidationReport:
    dropped: list[str] = field(default_factory=list)
    demoted: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.dropped and not self.demoted


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


def _quote_is_verbatim(quote: str, haystack: str) -> bool:
    """Verbatim substring, tolerant only of whitespace and case.

    Deliberately not fuzzy: a paraphrase that merely resembles the source is
    exactly the failure this exists to catch.
    """
    if not quote or not haystack:
        return False
    return _normalise(quote) in _normalise(haystack)


def _cue_restates_claim(cue: str, value: str) -> bool:
    """A cue that just rewords the claim is not evidence for it."""
    a, b = _normalise(cue), _normalise(value)
    if not a or not b:
        return True
    if a == b or (b in a and len(a) - len(b) < 12):
        return True
    return SequenceMatcher(None, a, b).ratio() > 0.85


def validate_anchor(anchor: Anchor, material: AnalysisMaterial) -> bool:
    if anchor.kind is AnchorKind.FRAME:
        return anchor.frame_index in material.frame_indices
    if anchor.kind is AnchorKind.TRANSCRIPT:
        transcript = material.transcript
        if transcript is None or anchor.t_start_s is None:
            return False
        segment = next(
            (s for s in transcript.segments if s.start - 0.5 <= anchor.t_start_s <= s.end + 0.5),
            None,
        )
        if segment is None:
            return False
        return anchor.quote is None or _quote_is_verbatim(anchor.quote, segment.text)
    return _quote_is_verbatim(anchor.quote or "", material.text_for(anchor.kind))


def valid_anchors(raw_anchors: list[str], material: AnalysisMaterial) -> list[Anchor]:
    parsed = [a for a in (parse_anchor(r) for r in raw_anchors) if a is not None]
    return [a for a in parsed if validate_anchor(a, material)]


def _supported(
    value: str, cue: str, raw_anchors: list[str], basis: Basis, material: AnalysisMaterial
) -> tuple[list[Anchor], str]:
    """Return (valid anchors, reason-to-drop). Empty reason means keep."""
    anchors = valid_anchors(raw_anchors, material)
    if not anchors:
        return [], "no valid anchor"
    if len(cue) > MAX_CUE_CHARS:
        return anchors, "cue exceeds length limit"
    if _cue_restates_claim(cue, value):
        return anchors, "cue restates claim"
    if basis is Basis.SEEN and not any(a.kind is AnchorKind.FRAME for a in anchors):
        return anchors, "basis=seen without a frame anchor"
    if basis is Basis.HEARD and not any(a.kind is AnchorKind.TRANSCRIPT for a in anchors):
        return anchors, "basis=heard without a transcript anchor"
    return anchors, ""


def validate(analysis: ItemAnalysis, material: AnalysisMaterial) -> ValidationReport:
    """Drop unsupported claims in place and report what was removed.

    The model can invent a frame index or paraphrase a quote. Brief §28 forbids
    hallucinated evidence reaching a finding, so anchors are checked against the
    supplied material rather than trusted.
    """
    report = ValidationReport()

    kept_labels = []
    for label in analysis.labels:
        _, why = _supported(label.value, label.cue, label.anchors, label.basis, material)
        if why:
            report.dropped.append(f"{label.facet.value}:{label.value[:40]} ({why})")
            continue
        kept_labels.append(label)
    analysis.labels = kept_labels

    for field_name in ("visible_brands", "mentioned_brands"):
        kept = []
        for sighting in getattr(analysis, field_name):
            _, why = _supported(
                sighting.surface_form, sighting.cue, sighting.anchors, sighting.basis, material
            )
            if why:
                report.dropped.append(f"{field_name}:{sighting.surface_form[:40]} ({why})")
                continue
            kept.append(sighting)
        setattr(analysis, field_name, kept)

    kept_products = []
    for product in analysis.visible_products:
        anchors = valid_anchors(product.anchors, material)
        if not anchors:
            report.dropped.append(f"visible_products:{product.description[:40]} (no valid anchor)")
            continue
        frames = [a for a in anchors if a.kind is AnchorKind.FRAME]
        # Every rung above `present` describes something visual happening over
        # time, so it needs at least two frames. One still cannot evidence an
        # action, and speech cannot evidence a visual act at all -- an item
        # anchored only to the transcript falls all the way back to `present`.
        # The rung is demoted rather than the claim dropped: the object really
        # was there.
        if product.consumption_state in _ABOVE_PRESENT and len(frames) < 2:
            why = "single frame" if frames else "speech alone"
            report.demoted.append(
                f"{product.description[:30]}: {product.consumption_state.value} -> present "
                f"({why} cannot evidence an action)"
            )
            product.consumption_state = ConsumptionState.PRESENT
        kept_products.append(product)
    analysis.visible_products = kept_products

    # Sponsorship buckets have hard cue requirements; an unsupported bucket
    # falls back to undetermined, which is a legitimate answer.
    kinds = {c.kind for c in analysis.sponsorship.cues}
    bucket = analysis.sponsorship.bucket
    if bucket is SponsorshipBucket.LIKELY_PAID and not (kinds & _PAID_CUE_KINDS):
        report.demoted.append("sponsorship: likely_paid -> undetermined (no admissible paid cue)")
        analysis.sponsorship.bucket = SponsorshipBucket.UNDETERMINED
    elif bucket is SponsorshipBucket.LIKELY_ORGANIC and (
        not (kinds & _ORGANIC_CUE_KINDS) or (kinds & _PAID_CUE_KINDS)
    ):
        report.demoted.append(
            "sponsorship: likely_organic -> undetermined (missing organic cue or paid cue present)"
        )
        analysis.sponsorship.bucket = SponsorshipBucket.UNDETERMINED

    if material.media_tier is MediaTier.THUMBNAIL_ONLY:
        # One still has no timeline, so nothing unfolding over time is
        # observable and no product can be shown doing anything.
        before = len(analysis.labels)
        analysis.labels = [lab for lab in analysis.labels if lab.facet not in TEMPORAL_FACETS]
        if removed := before - len(analysis.labels):
            report.dropped.append(
                f"{removed} temporal labels (thumbnail-only item has no timeline)"
            )
        for product in analysis.visible_products:
            product.consumption_state = ConsumptionState.PRESENT

    return report


def build_manifest(item: ContentItem, material: AnalysisMaterial) -> str:
    """The per-item text block. Everything volatile lives here, after the
    cached system prompt."""
    lines = [
        "# MEDIA MANIFEST",
        f"media_tier: {material.media_tier.value}",
        f"frames_supplied: {len(material.frames)}",
    ]
    for i, frame in enumerate(material.frames):
        source = "platform_thumbnail" if frame.selection_reason == "thumbnail" else "sampled_frame"
        lines.append(
            f"  frame_index={frame.index} order={i} t={frame.timestamp_s:.2f}s source={source}"
        )

    lines += ["", "# TRANSCRIPT"]
    transcript = material.transcript
    if transcript and transcript.has_speech:
        lines.append(f"detected_language: {transcript.detected_language or 'unknown'}")
        for segment in transcript.segments:
            lines.append(f"  [{segment.start:.2f}-{segment.end:.2f}] {segment.text.strip()}")
    else:
        lines.append("  (no speech detected in this item)")

    lines += ["", "# CAPTION", material.caption or "  (none)"]
    if material.overlay_text:
        lines += ["", "# ON-SCREEN TEXT", material.overlay_text]
    if material.comments_text:
        lines += ["", "# COMMENTS", material.comments_text]

    # Platform facts only. Nothing here is a content claim, and the prompt
    # forbids using engagement as evidence about sentiment or preference.
    lines += [
        "",
        "# ITEM CONTEXT (platform-stated facts; not evidence about content)",
        f"  platform: {item.platform.value}",
        f"  published_at: {item.published_at.isoformat() if item.published_at else 'unknown'}",
        f"  duration_seconds: {item.duration_seconds or 'unknown'}",
        f"  hashtags: {', '.join(item.hashtags) if item.hashtags else '(none)'}",
    ]
    if item.sound and item.sound.title:
        lines.append(f"  attached_sound: {item.sound.title!r} original={item.sound.original}")
    lines += [
        "",
        "# ANCHOR FORMAT (every anchor string must match one of these exactly)",
        "  frame:<index>                                  e.g. frame:3",
        "  transcript:<start>-<end>:<verbatim quote>       e.g. transcript:6.00-7.30:exact words",
        "  caption:<verbatim quote>",
        "  overlay:<verbatim quote>",
        "  comments:<verbatim quote>",
        "Only frame indices listed above exist. Quotes are checked character by character.",
        "",
        "Analyse this item and return the structured object.",
    ]
    return "\n".join(lines)


async def analyze_item(
    item: ContentItem,
    material: AnalysisMaterial,
    *,
    client: LLMClient | None = None,
    ledger: Ledger | None = None,
) -> tuple[AnalysisRecord | None, ValidationReport, str | None]:
    """Analyse one item. Returns (record, validation report, error)."""
    from aypo.llm.client import image_block, text_block

    settings = get_settings()
    llm = client or LLMClient(ledger=ledger, model=settings.aypo_analysis_model)

    content: list[dict] = []
    for frame in material.frames:
        if frame.data:
            content.append(image_block(frame.data))
    if not content and material.media_tier is MediaTier.FULL_VIDEO:
        return None, ValidationReport(), "no frames available to analyse"
    # Frames first, then the manifest: the text refers to frame indices by
    # position, so the model must have seen them already.
    content.append(text_block(build_manifest(item, material)))

    try:
        result = await llm.structured(
            ItemAnalysis,
            system=SYSTEM_PROMPT,
            content=content,
            effort=settings.aypo_analysis_effort,
            # A brand-dense item (one live haul video resolved 15 brands, each
            # with a cue and anchors) overran 8000 and truncated mid-JSON, which
            # fails schema validation and loses the whole analysis.
            max_tokens=16000,
            cache_system=True,
            label="item_analysis",
            model=settings.aypo_analysis_model,
        )
    except (LLMRefused, LLMTruncated, LLMSchemaError) as exc:
        log.warning("analysis.failed", item=item.dedupe_key, error=str(exc)[:200])
        return None, ValidationReport(), f"{type(exc).__name__}: {exc}"

    analysis = result.value
    # The model reports media_tier, but the pipeline knows it for certain.
    analysis.media_tier = material.media_tier
    report = validate(analysis, material)
    record = AnalysisRecord(
        analysis=analysis,
        prompt_version=PROMPT_VERSION,
        dropped_claims=report.dropped,
        demoted_claims=report.demoted,
        frames_analysed=len(material.frames),
        had_transcript=bool(material.transcript and material.transcript.has_speech),
    )
    log.info(
        "analysis.ok",
        item=item.dedupe_key,
        brands=len(analysis.visible_brands),
        products=len(analysis.visible_products),
        dropped=len(report.dropped),
        demoted=len(report.demoted),
        usd=round(result.spend.usd, 4),
    )
    return record, report, None
