"""Trend detection must refuse more often than it claims.

The corpus is collected at one moment, so publication-date COUNTS reflect
platform ranking rather than real-world volume. Counting items per period would
manufacture growth for every theme; these tests pin the defences.
"""

import pytest

from aypo.intelligence.independence import (
    assess,
    design_effect,
    wilson_interval,
)
from aypo.intelligence.trends import (
    MIN_PERIODS,
    Classification,
    PeriodPoint,
    RefusalReason,
    analyse_theme,
    benjamini_hochberg,
    refuse_volume_claim,
)


def point(period, n_theme, n_corpus, creators=10, max_share=0.15, deff=1.0):
    return PeriodPoint(period, n_theme, n_corpus, creators, max_share, deff)


# ── the central defence ───────────────────────────────────────────────────────


def test_volume_claims_are_always_refused():
    result = refuse_volume_claim("theme-x")
    assert result.classification is Classification.INSUFFICIENT
    assert result.refusal_reason is RefusalReason.VOLUME_CLAIM_UNIDENTIFIED


def test_rising_counts_under_flat_share_are_not_growth():
    # THE failure this module exists to prevent. Exposure grows ~1.5x/period, so
    # raw counts climb steeply while the theme's actual share is unchanged.
    points = [
        point("2026-01", 10, 100),
        point("2026-02", 15, 150),
        point("2026-03", 22, 220),
        point("2026-04", 34, 340),
    ]
    result = analyse_theme("theme-x", points)
    assert result.classification is not Classification.GROWING
    assert all(abs(p.share - 0.10) < 1e-9 for p in points)


def test_genuine_share_growth_is_detected():
    points = [
        point("2026-01", 5, 200),
        point("2026-02", 20, 200),
        point("2026-03", 40, 200),
        point("2026-04", 60, 200),
    ]
    assert analyse_theme("theme-x", points).classification is Classification.GROWING


def test_genuine_share_decline_is_detected():
    points = [
        point("2026-01", 60, 200),
        point("2026-02", 40, 200),
        point("2026-03", 20, 200),
        point("2026-04", 8, 200),
    ]
    assert analyse_theme("theme-x", points).classification is Classification.DECLINING


# ── coverage floors ───────────────────────────────────────────────────────────


def test_too_few_periods_is_refused():
    result = analyse_theme("theme-x", [point("2026-01", 10, 100), point("2026-02", 20, 100)])
    assert result.classification is Classification.INSUFFICIENT
    assert result.refusal_reason is RefusalReason.TOO_FEW_PERIODS
    assert MIN_PERIODS == 3


def test_thin_periods_are_refused():
    points = [point(f"2026-0{i}", 2, 8) for i in range(1, 5)]
    result = analyse_theme("theme-x", points)
    assert result.classification is Classification.INSUFFICIENT
    assert result.refusal_reason is RefusalReason.TOO_FEW_ITEMS


def test_single_creator_domination_is_refused():
    # A period dominated by one creator describes that creator, not the market.
    points = [point(f"2026-0{i}", 30, 100, creators=6, max_share=0.80) for i in range(1, 5)]
    result = analyse_theme("theme-x", points)
    assert result.refusal_reason is RefusalReason.CREATOR_CONCENTRATION


def test_too_few_creators_is_refused():
    points = [point(f"2026-0{i}", 30, 100, creators=2) for i in range(1, 5)]
    assert analyse_theme("theme-x", points).refusal_reason is RefusalReason.TOO_FEW_CREATORS


def test_design_effect_shrinks_effective_sample():
    # 100 items from few creators is not 100 independent observations.
    p = point("2026-01", 10, 100, deff=2.5)
    assert p.n_effective == 40.0


def test_claims_always_carry_a_scoping_caveat():
    points = [point(f"2026-0{i}", 5 * i, 200) for i in range(1, 5)]
    result = analyse_theme("theme-x", points)
    assert any("Not a market-wide" in c for c in result.caveats)
    assert any("Volume is not estimable" in c for c in result.caveats)
    assert "common exposure" in result.method


# ── multiple comparisons ──────────────────────────────────────────────────────


def test_multiple_testing_demotes_marginal_trends():
    # Testing many themes at alpha=0.05 yields 'significant' trends by chance.
    results = []
    for i in range(40):
        points = [point(f"2026-0{j}", 20 + (i % 3) * j, 200) for j in range(1, 5)]
        results.append(analyse_theme(f"theme-{i}", points))
    before = sum(1 for r in results if r.directional)
    benjamini_hochberg(results)
    after = sum(1 for r in results if r.directional)
    assert after <= before
    for r in results:
        if r.p_value is not None and r.q_value is not None:
            assert r.q_value >= r.p_value - 1e-12


def test_strong_trend_survives_correction():
    strong = [point(f"2026-0{i}", 5 + 25 * i, 200) for i in range(1, 6)]
    results = [analyse_theme("strong", strong)]
    benjamini_hochberg(results)
    assert results[0].classification is Classification.GROWING


# ── intervals and independence ────────────────────────────────────────────────


def test_wilson_interval_stays_inside_zero_one():
    low, high = wilson_interval(0, 30)
    assert low >= 0.0 and high <= 1.0
    low, high = wilson_interval(30, 30)
    assert low >= 0.0 and high <= 1.0


def test_design_effect_widens_the_interval():
    narrow = wilson_interval(20, 100, deff=1.0)
    wide = wilson_interval(20, 100, deff=3.0)
    assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])


def test_design_effect_grows_with_creator_concentration():
    spread = design_effect([f"c{i}" for i in range(50)])
    concentrated = design_effect(["c1"] * 25 + ["c2"] * 25)
    assert concentrated > spread


def test_design_effect_is_clamped():
    assert design_effect(["c1"] * 1000) <= 5.0
    assert design_effect([]) >= 1.0


def test_concentration_is_flagged():
    ind = assess(["c1"] * 8 + ["c2", "c3"], ["q1"] * 10)
    assert ind.concentrated is True
    assert ind.n_effective < ind.n


@pytest.mark.parametrize("n", [0, 1])
def test_degenerate_samples_do_not_crash(n):
    ind = assess(["c1"] * n, ["q1"] * n)
    assert ind.n == n
