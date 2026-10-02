"""Saturation must fail closed.

Ending a run early with a corpus that merely looks complete is worse than
spending a little more budget, so every ambiguous case resolves to "keep going".
"""

from aypo.research.saturation import (
    FAMILIES_REQUIRED,
    Family,
    FamilyState,
    SaturationTracker,
    jaccard,
)


def saturate(tracker: SaturationTracker, iterations: int = 3) -> None:
    """Drive every family into its saturated range."""
    for _ in range(iterations):
        tracker.observe_novelty(admitted=1, already_seen=99)
        tracker.observe_competitors({"Brand A", "Brand B"})
        tracker.observe_yield(new_evidence=0, units_spent=100)
        tracker.observe_hypotheses(generated=10, admitted=0)
        tracker.observe_clusters({f"i{i}": "c1" for i in range(8)})


def test_fresh_tracker_is_not_saturated():
    saturated, reason, _ = SaturationTracker().is_saturated()
    assert saturated is False
    assert "computable" in reason


def test_single_iteration_is_never_enough():
    t = SaturationTracker()
    t.observe_novelty(admitted=0, already_seen=100)
    t.observe_yield(new_evidence=0, units_spent=100)
    saturated, _, readings = t.is_saturated()
    # One reading is noise; every family needs a window.
    assert saturated is False
    assert all(r.state is FamilyState.UNAVAILABLE for r in readings)


def test_saturation_requires_agreement_across_families():
    t = SaturationTracker()
    # Only one family driven into saturation.
    for _ in range(3):
        t.observe_novelty(admitted=1, already_seen=99)
    saturated, reason, _ = t.is_saturated()
    assert saturated is False
    assert str(FAMILIES_REQUIRED) in reason


def test_agreeing_families_do_saturate():
    t = SaturationTracker()
    saturate(t)
    saturated, reason, _ = t.is_saturated()
    assert saturated is True
    assert "families agree" in reason


def test_active_research_is_not_saturated():
    t = SaturationTracker()
    for i in range(3):
        t.observe_novelty(admitted=80, already_seen=20)
        t.observe_competitors({f"Brand {chr(65 + j)}" for j in range(i + 2)})
        t.observe_yield(new_evidence=50, units_spent=100)
        t.observe_hypotheses(generated=10, admitted=6)
    assert t.is_saturated()[0] is False


def test_rank_exhaustion_blocks_saturation():
    t = SaturationTracker()
    saturate(t)
    assert t.is_saturated()[0] is True
    # A probe found new material: the source's ranked results are used up, which
    # has the opposite remedy to saturation.
    t.rank_exhaustion_suspected = True
    saturated, reason, _ = t.is_saturated()
    assert saturated is False
    assert "rank_exhaustion" in reason


def test_cluster_stability_is_label_invariant():
    t = SaturationTracker()
    # Same partition, completely different cluster ids. Comparing ids directly
    # would report total churn; comparing co-assignment reports perfect
    # agreement, which is the truth.
    t.observe_clusters({"a": "c1", "b": "c1", "c": "c2", "d": "c2", "e": "c2"})
    value = t.observe_clusters({"a": "zz", "b": "zz", "c": "qq", "d": "qq", "e": "qq"})
    assert value == 1.0


def test_cluster_stability_detects_a_real_repartition():
    t = SaturationTracker()
    t.observe_clusters({"a": "c1", "b": "c1", "c": "c2", "d": "c2", "e": "c2"})
    value = t.observe_clusters({"a": "x", "b": "y", "c": "x", "d": "y", "e": "x"})
    assert value is not None and value < 0.7


def test_cluster_stability_needs_enough_shared_items():
    t = SaturationTracker()
    t.observe_clusters({"a": "c1", "b": "c1"})
    # Too few overlapping items to say anything.
    assert t.observe_clusters({"a": "c1", "b": "c1"}) is None


def test_marginal_yield_is_relative_to_the_runs_peak():
    t = SaturationTracker()
    t.observe_yield(new_evidence=100, units_spent=100)  # peak
    later = t.observe_yield(new_evidence=5, units_spent=100)
    # What matters is that the return on more collection collapsed, not that it
    # fell below an absolute number.
    assert later == 0.05


def test_competitor_stability_uses_jaccard():
    assert jaccard({"Brand A"}, {"Brand A"}) == 1.0
    assert jaccard(set(), set()) == 1.0
    assert jaccard({"Brand A"}, {"Brand B"}) == 0.0
    assert jaccard({"Brand A", "Brand B"}, {"Brand A"}) == 0.5


def test_snapshot_reports_every_family_with_its_reason():
    t = SaturationTracker()
    saturate(t)
    snap = t.snapshot()
    assert snap["saturated"] is True
    assert set(snap["families"]) == {f.value for f in Family}
    for payload in snap["families"].values():
        assert payload["reason"]
