"""Open-world brand entity resolution.

The governing bias is anti-merge. Two different brands collapsed into one
silently corrupts every competitor comparison and is nearly invisible once made;
a brand left unresolved is merely incomplete and shows up for a human to fix.
Most of these tests therefore assert that something does NOT merge.
"""

import pytest

from aypo.intelligence.brand_resolution import (
    MIN_FUZZY_LENGTH,
    normalise,
    resolve,
    skeleton,
)


def test_the_observed_case_collapses():
    # Measured in a live run: one brand, three spellings, across three videos.
    observed = {"JACK'N'JILL": 1, "JACK'nJILL": 1, "JACK'N JILL": 1}
    report = resolve(observed)
    assert report.entity_count == 1
    assert report.entities[0].variant_count == 3
    assert report.entities[0].observation_count == 3


def test_case_variants_collapse():
    report = resolve({"Piattos": 3, "piattos": 2, "PIATTOS": 1})
    assert report.entity_count == 1
    # The canonical display name is the most-observed spelling, never invented.
    assert report.entities[0].canonical_name == "Piattos"
    assert report.entities[0].observation_count == 6


def test_distinct_brands_stay_distinct():
    report = resolve({"Nova": 2, "Chippy": 2, "CHEEZY": 1, "REGENT": 1, "Piattos": 3})
    assert report.entity_count == 5


def test_surface_forms_are_never_altered():
    # Brief §18 has the extractor report characters verbatim; normalisation here
    # must stay reversible, so the original strings are retained.
    report = resolve({"JACK'N'JILL": 1, "JACK'nJILL": 1})
    assert set(report.entities[0].surface_forms) == {"JACK'N'JILL", "JACK'nJILL"}


# ── anti-merge ────────────────────────────────────────────────────────────────


def test_short_names_never_fuzzy_merge():
    # Three- and four-letter brands differ from each other by one character as
    # often as an OCR error does, so fuzzy matching is not applied to them.
    report = resolve({"Nova": 2, "Nove": 2, "Novo": 2})
    assert report.entity_count == 3


@pytest.mark.parametrize(
    "a,b",
    [
        ("Piattos", "Pillows"),
        ("Chippy", "Chimpy"),
        ("Oishi Prawn", "Oishi Ridges"),  # same family, different products
    ],
)
def test_similar_but_different_names_do_not_merge(a, b):
    report = resolve({a: 2, b: 2})
    assert report.entity_count == 2, f"{a} and {b} were merged"


def test_raising_the_threshold_only_ever_splits():
    observed = {"Marketside Crisps": 3, "Marketsid Crisps": 1}
    permissive = resolve(observed, fuzzy_threshold=85.0).entity_count
    strict = resolve(observed, fuzzy_threshold=99.9).entity_count
    assert strict >= permissive


def test_merges_are_recorded_for_audit():
    report = resolve({"Marketside Crisps": 3, "Marketsid Crisps": 1}, fuzzy_threshold=85.0)
    if report.entity_count == 1:
        assert report.merges, "a fuzzy merge must be auditable"
        surface, canonical, why = report.merges[0]
        # The audit record names what merged into what, and on what evidence.
        assert surface and canonical and "fuzzy" in why


# ── normalisation ─────────────────────────────────────────────────────────────


def test_skeleton_is_stable_across_punctuation_and_spacing():
    assert skeleton("JACK'N'JILL") == skeleton("JACK'nJILL") == skeleton("JACK'N JILL")


def test_skeleton_folds_common_ocr_confusions():
    # A wordmark read off a frame routinely yields 0/O and 1/l confusions.
    assert skeleton("N0VA") == skeleton("NOVA")
    assert skeleton("PIATT0S") == skeleton("PIATTOS")


def test_normalisation_drops_corporate_and_locale_descriptors():
    # Otherwise two unrelated brands both suffixed "Philippines" look similar.
    assert normalise("Brand A Inc") == "brand a"
    assert normalise("Brand A Philippines") == "brand a"


def test_accents_are_folded():
    assert skeleton("Café Crisps") == skeleton("Cafe Crisps")


def test_punctuation_only_surface_is_left_unresolved():
    report = resolve({"---": 1, "Brand A": 2})
    assert "---" in report.unresolved
    assert report.entity_count == 1


# ── seeds (brief §23) ─────────────────────────────────────────────────────────


def test_a_seed_is_flagged_but_given_no_privilege():
    report = resolve({"Brand A": 1, "Brand B": 5}, seeds=["Brand A"])
    by_name = {e.canonical_name: e for e in report.entities}
    assert by_name["Brand A"].seeded is True
    assert by_name["Brand B"].seeded is False
    # A seed must not outrank a brand the data actually found more often.
    assert report.entities[0].canonical_name == "Brand B"


def test_seeds_do_not_prevent_discovery():
    # Brief §23: a seed list is a recall hint, never a limit on what is found.
    report = resolve({"Brand A": 1, "Brand C": 2, "Brand D": 1}, seeds=["Brand A"])
    assert report.entity_count == 3


def test_empty_input_is_handled():
    report = resolve({})
    assert report.entity_count == 0 and not report.unresolved


def test_min_fuzzy_length_is_meaningfully_above_short_names():
    assert MIN_FUZZY_LENGTH >= 5
