"""Open-world brand entity resolution (brief §23).

Brands arrive as free text from three noisy sources -- packaging read off video
frames, spoken or written mentions, and marketplace titles -- and there is no
pre-existing brand list. A live run produced ``JACK'N'JILL``, ``JACK'nJILL`` and
``JACK'N JILL`` from three videos: one brand, three surface forms. Counting them
as three would inflate the competitor set and split that brand's evidence three
ways.

The extractor deliberately does **not** normalise: brief §18 tells it to report
the characters it can actually resolve, verbatim, because a "helpful" correction
at read time destroys the evidence. Normalisation happens here instead, where it
is reversible and auditable.

**The governing bias is anti-merge.** Two genuinely different brands collapsed
into one is an error that silently corrupts every competitor comparison
downstream and is nearly invisible once made. A brand left unresolved is merely
incomplete, and shows up as a low-confidence entity a human can fix. So every
threshold here is set to prefer leaving an entity unresolved.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import structlog
from rapidfuzz import fuzz

log = structlog.get_logger(__name__)

# Fuzzy match floor. Set high on purpose: at 90 the OCR-mangled variants of one
# wordmark still match, while genuinely different short brand names do not.
FUZZY_THRESHOLD = 90.0
# Below this length a fuzzy match is not trustworthy -- three- and four-letter
# brand names differ from each other by one character as often as an OCR error
# does.
MIN_FUZZY_LENGTH = 6

# Characters OCR and stylised packaging routinely interchange. Applied only to
# build a comparison skeleton; the surface form is never altered.
_OCR_EQUIVALENTS = str.maketrans({"0": "o", "1": "l", "5": "s", "8": "b", "|": "l"})

# Marketplace-title and corporate noise, stripped before comparison. Kept
# deliberately narrow: a token that could plausibly be part of a real wordmark
# does not belong here. "brand" and "the" were both removed after they mangled
# legitimate names ("Brand A" collapsed to "a", and "The Body Shop" would lose
# its article). Over-stripping is not a cosmetic problem -- it shortens the
# comparison key, and shorter keys collide.
_DESCRIPTOR_TOKENS = frozenset(
    {"co", "inc", "ltd", "corp", "official", "store", "ph", "philippines"}
)


def normalise(surface: str) -> str:
    """Casefold, strip accents and punctuation, collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", surface)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    lowered = stripped.casefold()
    cleaned = re.sub(r"[^\w\s]", " ", lowered)
    tokens = [t for t in cleaned.split() if t]
    kept = [t for t in tokens if t not in _DESCRIPTOR_TOKENS]
    # If stripping descriptors would leave nothing, the "descriptors" were the
    # name. Falling back to the unstripped form keeps two such names distinct
    # instead of collapsing both to an empty key.
    return " ".join(kept or tokens)


def skeleton(surface: str) -> str:
    """A comparison key robust to spacing, punctuation and OCR confusions.

    ``JACK'N'JILL``, ``JACK'nJILL`` and ``JACK'N JILL`` all reduce to
    ``jacknjill``. This is the key that solves the observed case, and it is
    exact-match only -- no fuzziness, so it cannot merge two different names.
    """
    return normalise(surface).replace(" ", "").translate(_OCR_EQUIVALENTS)


@dataclass
class BrandEntity:
    """A resolved brand and every surface form seen for it."""

    canonical_name: str
    skeleton_key: str
    surface_forms: dict[str, int] = field(default_factory=dict)
    # True when the operator supplied it as a seed. Brief §23: a seed is a
    # recall hint with no scoring privilege, and the UI must be able to tell a
    # seeded brand from a discovered one.
    seeded: bool = False

    @property
    def observation_count(self) -> int:
        return sum(self.surface_forms.values())

    @property
    def variant_count(self) -> int:
        return len(self.surface_forms)

    def add(self, surface: str, count: int = 1) -> None:
        self.surface_forms[surface] = self.surface_forms.get(surface, 0) + count
        # The canonical display name is the most frequently observed spelling,
        # not the first seen and not an invented "correct" form.
        self.canonical_name = max(self.surface_forms.items(), key=lambda kv: (kv[1], -len(kv[0])))[
            0
        ]


@dataclass
class ResolutionReport:
    entities: list[BrandEntity] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    merges: list[tuple[str, str, str]] = field(default_factory=list)  # (a, b, why)

    @property
    def entity_count(self) -> int:
        return len(self.entities)


def resolve(
    observations: dict[str, int],
    *,
    seeds: list[str] | None = None,
    fuzzy_threshold: float = FUZZY_THRESHOLD,
) -> ResolutionReport:
    """Group surface forms into brand entities.

    ``observations`` maps a verbatim surface form to how many times it was seen.
    Resolution runs as a cascade, strongest evidence first: exact match, then
    skeleton match, then bounded fuzzy match. Anything that clears none of them
    stays its own entity rather than being forced into the nearest neighbour.
    """
    report = ResolutionReport()
    by_skeleton: dict[str, BrandEntity] = {}

    for surface in sorted(observations, key=lambda s: (-observations[s], s)):
        count = observations[surface]
        key = skeleton(surface)
        if not key:
            # Nothing survived normalisation -- punctuation or descriptors only.
            report.unresolved.append(surface)
            continue

        if key in by_skeleton:
            by_skeleton[key].add(surface, count)
            continue

        matched: BrandEntity | None = None
        if len(key) >= MIN_FUZZY_LENGTH:
            best_score = 0.0
            for existing_key, entity in by_skeleton.items():
                if len(existing_key) < MIN_FUZZY_LENGTH:
                    continue
                score = fuzz.ratio(key, existing_key)
                if score > best_score:
                    best_score, matched = score, entity
            if best_score < fuzzy_threshold:
                matched = None
            elif matched is not None:
                report.merges.append(
                    (surface, matched.canonical_name, f"fuzzy {best_score:.0f} on skeleton")
                )

        if matched is not None:
            matched.add(surface, count)
        else:
            entity = BrandEntity(canonical_name=surface, skeleton_key=key)
            entity.add(surface, count)
            by_skeleton[key] = entity

    seed_skeletons = {skeleton(s) for s in (seeds or [])}
    for entity in by_skeleton.values():
        if entity.skeleton_key in seed_skeletons:
            entity.seeded = True

    report.entities = sorted(
        by_skeleton.values(), key=lambda e: (-e.observation_count, e.canonical_name)
    )
    log.info(
        "brands.resolved",
        surfaces=len(observations),
        entities=len(report.entities),
        merges=len(report.merges),
        unresolved=len(report.unresolved),
    )
    return report
