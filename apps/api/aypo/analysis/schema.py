"""Structured output of per-item multimodal analysis (brief §19, §20).

Hybrid by design: a stable core that exists across items, plus open-ended
labels the model composes in its own words. No taxonomy is supplied and none is
implied -- brief §1 and §34 require categories be discovered from the material
rather than handed to the extractor.

Every claim carries three things that make it checkable:

* ``basis``   -- seen / heard / written / inferred, never mixed
* ``anchors`` -- where in the supplied material it can be verified
* ``cue``     -- the concrete observation a second person could go and look for

Shape is constrained by the structured-output API, which rejects schemas with
more than 24 optional parameters and refuses to compile an over-large grammar.
Both were hit while building this. Hence two deliberate choices:

1. Every facet lives in ONE ``labels`` list discriminated by a ``facet`` enum,
   rather than thirteen separate lists. This is also closer to brief §19B,
   which asks for proposed labels rather than fixed buckets.
2. Anchors are compact strings in a documented micro-format, not nested
   objects. Nothing is lost: anchors are validated by code against the material
   that was actually supplied, so a structured shape from the model would have
   been re-checked regardless.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, Field


class Basis(StrEnum):
    SEEN = "seen"
    HEARD = "heard"
    WRITTEN = "written"
    INFERRED = "inferred"


class Facet(StrEnum):
    """What kind of thing a label is about.

    The stable facets (brief §19A) and the open-ended ones (§19B) share one
    enum because they share one shape; the distinction is in which are expected
    to appear for most items, not in how they are represented.
    """

    # stable core
    LANGUAGE = "language"
    CONTENT_FORMAT = "content_format"
    CREATOR_ARCHETYPE = "creator_archetype"
    SENTIMENT = "sentiment"
    PURCHASE_INTENT = "purchase_intent"
    # open-ended discovered
    OCCASION = "occasion"
    BEHAVIOR = "behavior"
    SOCIAL_CONTEXT = "social_context"
    CULTURAL_SIGNAL = "cultural_signal"
    SLANG = "slang"
    HUMOR_SIGNAL = "humor_signal"
    ACTIVITY = "activity"
    PRODUCT_PAIRING = "product_pairing"
    SETTING = "setting"
    STATED_USE = "stated_use"
    FORMAT_FEATURE = "format_feature"
    BRAND_CO_APPEARANCE = "brand_co_appearance"
    UNEXPECTED = "unexpected"


# Facets describing something that unfolds over time. A single still cannot
# evidence any of them, so they are cleared for thumbnail-only items.
TEMPORAL_FACETS = frozenset({Facet.ACTIVITY, Facet.BEHAVIOR, Facet.STATED_USE})


class ConsumptionState(StrEnum):
    """How far a product's involvement actually goes.

    Brief §28 requires incidental appearance be distinguished from active use.
    ``PRESENT`` is both the lowest rung and the most common honest answer.
    """

    PRESENT = "present"
    HELD = "held"
    BEING_OPENED = "being_opened"
    SHOWN_TO_CAMERA = "shown_to_camera"
    IN_USE_OR_CONSUMED = "in_use_or_consumed"
    OFFERED_OR_SHARED = "offered_or_shared"
    UNCLEAR = "unclear"


class Legibility(StrEnum):
    FULL = "full"
    PARTIAL = "partial"
    ILLEGIBLE = "illegible"


class SponsorshipBucket(StrEnum):
    LIKELY_PAID = "likely_paid"
    POSSIBLY_PAID = "possibly_paid"
    LIKELY_ORGANIC = "likely_organic"
    UNDETERMINED = "undetermined"


class MediaTier(StrEnum):
    FULL_VIDEO = "full_video"
    THUMBNAIL_ONLY = "thumbnail_only"


class AnchorKind(StrEnum):
    FRAME = "frame"
    TRANSCRIPT = "transcript"
    CAPTION = "caption"
    OVERLAY = "overlay"
    COMMENTS = "comments"


@dataclass(frozen=True)
class Anchor:
    """A parsed anchor. Produced by code from the model's compact string."""

    kind: AnchorKind
    frame_index: int | None = None
    t_start_s: float | None = None
    t_end_s: float | None = None
    quote: str | None = None
    raw: str = ""


_FRAME_RE = re.compile(r"^frame:(\d+)$", re.I)
_TRANSCRIPT_RE = re.compile(r"^transcript:([\d.]+)-([\d.]+)(?::(.*))?$", re.I | re.S)
_TEXT_RE = re.compile(r"^(caption|overlay|comments):(.*)$", re.I | re.S)

ANCHOR_FORMAT_HELP = (
    "frame:<index> | transcript:<start>-<end>:<verbatim quote> | "
    "caption:<verbatim quote> | overlay:<verbatim quote> | comments:<verbatim quote>"
)


def parse_anchor(raw: str) -> Anchor | None:
    """Parse one anchor string. Returns None when it is malformed.

    A malformed anchor is treated exactly like a missing one: the claim it was
    meant to support loses its evidence.
    """
    text = (raw or "").strip()
    if m := _FRAME_RE.match(text):
        return Anchor(AnchorKind.FRAME, frame_index=int(m.group(1)), raw=text)
    if m := _TRANSCRIPT_RE.match(text):
        return Anchor(
            AnchorKind.TRANSCRIPT,
            t_start_s=float(m.group(1)),
            t_end_s=float(m.group(2)),
            quote=(m.group(3) or "").strip() or None,
            raw=text,
        )
    if m := _TEXT_RE.match(text):
        return Anchor(AnchorKind(m.group(1).lower()), quote=m.group(2).strip(), raw=text)
    return None


class Label(BaseModel):
    """One observation about the item."""

    facet: Facet
    value: str = Field(description="the label, verbatim or in your own words")
    basis: Basis
    cue: str = Field(
        description=(
            "the concrete thing observed, which a second person could look for at the "
            "anchor and find or fail to find; never a restatement of the claim; <=160 chars"
        )
    )
    anchors: list[str] = Field(description=ANCHOR_FORMAT_HELP)


class BrandSighting(BaseModel):
    """A brand read off the material.

    ``surface_form`` is verbatim: names are never translated, expanded,
    abbreviated or normalised here, because entity resolution needs the
    original string and a 'helpful' correction destroys evidence.
    """

    surface_form: str
    basis: Basis
    cue: str
    anchors: list[str] = Field(description=ANCHOR_FORMAT_HELP)
    legibility: Legibility
    brand_resolved: bool = Field(description="false when only colour, shape or layout suggested it")


class ProductSighting(BaseModel):
    description: str = Field(description="the object as observed, not a catalogue name")
    brand_surface_form: str | None
    consumption_state: ConsumptionState
    cue: str
    anchors: list[str] = Field(description=ANCHOR_FORMAT_HELP)
    has_legible_text_region: bool


class SponsorshipCue(BaseModel):
    kind: str
    cue: str
    anchors: list[str] = Field(description=ANCHOR_FORMAT_HELP)


class Sponsorship(BaseModel):
    """Observable-cue-based only.

    Never inferred from engagement, follower counts or how favourable the
    content is. ``undetermined`` is a frequent and correct answer.
    """

    bucket: SponsorshipBucket
    cues: list[SponsorshipCue]


class ItemAnalysis(BaseModel):
    """The model's output for one item. Only what the model produces."""

    media_tier: MediaTier
    visible_brands: list[BrandSighting]
    mentioned_brands: list[BrandSighting]
    visible_products: list[ProductSighting]
    labels: list[Label]
    sponsorship: Sponsorship
    evidence_insufficient_for: list[str] = Field(
        description=(
            "facets deliberately left empty because the material did not support them; "
            "a positive statement that the analyst looked and found nothing"
        )
    )
    summary: str

    def by_facet(self, facet: Facet) -> list[Label]:
        return [label for label in self.labels if label.facet is facet]

    def facets_present(self) -> set[Facet]:
        return {label.facet for label in self.labels}


class AnalysisRecord(BaseModel):
    """An analysis plus what the pipeline derived about it.

    Separate from ItemAnalysis so the model is never shown -- and never invited
    to populate -- fields that only code may write.
    """

    analysis: ItemAnalysis
    prompt_version: str
    dropped_claims: list[str] = Field(default_factory=list)
    demoted_claims: list[str] = Field(default_factory=list)
    midpoint_fraction: float | None = None
    frames_analysed: int = 0
    had_transcript: bool = False
