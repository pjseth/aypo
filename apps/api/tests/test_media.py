"""Frame selection determinism and boundary behaviour.

Frame selection is the product's quality floor: whether a brand can be read at
all depends on landing a frame on the shot where the object is presented.
"""

from itertools import pairwise

import pytest

from aypo.analysis.media import (
    MediaProbe,
    SelectedFrame,
    frame_budget,
    midpoint_fraction,
    select_timestamps,
)


@pytest.mark.parametrize(
    "duration,expected",
    [
        (1.0, 3),
        (4.9, 3),
        (5.0, 6),
        (19.9, 6),
        (20.0, 8),
        (59.9, 8),
        (60.0, 12),
        (179.9, 12),
        (180.0, 16),
        (599.9, 16),
        (600.0, 20),
        (3600.0, 20),
    ],
)
def test_frame_budget_tier_boundaries(duration, expected):
    # Tiers use `<` on the upper bound, so exactly 60.0s gets 12, not 8.
    assert frame_budget(duration) == expected


def test_unknown_duration_falls_back_to_modal_tier():
    # Truncated downloads commonly report duration 0; failing the item would
    # discard content that is still analysable.
    assert frame_budget(0) == 8
    assert frame_budget(-1) == 8


def test_selection_picks_scene_peaks_and_midpoints_elsewhere():
    duration = 12.0
    # Two hard cuts against an otherwise static clip.
    scores = [(t / 6, 0.02) for t in range(72)]
    scores[24] = (4.0, 35.0)
    scores[48] = (8.0, 28.0)

    frames = select_timestamps(duration, scores)
    assert len(frames) == frame_budget(duration)

    peaks = [f for f in frames if f.selection_reason == "bucket_scene_max"]
    assert {round(f.timestamp_s, 2) for f in peaks} == {4.0, 8.0}
    # Everything else must fall back to the midpoint rather than chase noise.
    assert all(f.scene_score == 0.0 for f in frames if f.selection_reason == "bucket_midpoint")


def test_static_clip_takes_every_midpoint():
    # A single-take talking clip produces no cuts. Pure scene detection would
    # return zero frames here; the hybrid must still return a full budget.
    scores = [(t / 6, 0.01) for t in range(120)]
    frames = select_timestamps(20.0, scores)
    assert len(frames) == frame_budget(20.0)
    assert all(f.selection_reason == "bucket_midpoint" for f in frames)
    assert midpoint_fraction(frames) == 1.0


def test_selection_is_deterministic():
    # The frame set feeds an artefact cache key, so identical input must give
    # byte-identical timestamps.
    scores = [(t / 6, (t * 7) % 13 / 10) for t in range(120)]
    a = select_timestamps(20.0, scores)
    b = select_timestamps(20.0, scores)
    assert [f.timestamp_s for f in a] == [f.timestamp_s for f in b]
    assert [f.selection_reason for f in a] == [f.selection_reason for f in b]


def test_timestamps_are_ordered_and_inside_trimmed_window():
    duration = 60.0
    scores = [(t / 6, 20.0 if t % 17 == 0 else 0.01) for t in range(360)]
    frames = select_timestamps(duration, scores)
    ts = [f.timestamp_s for f in frames]
    assert ts == sorted(ts), "selection must stay ordered after gap enforcement"
    # Head/tail trimming keeps selection off the intro frame and the end card.
    assert ts[0] >= 0.20
    assert ts[-1] <= duration - 0.25


def test_minimum_gap_is_enforced_or_flagged():
    duration = 10.0
    # Every score is a peak, so every bucket wants its own max and they crowd.
    scores = [(t / 6, 50.0) for t in range(60)]
    frames = select_timestamps(duration, scores)
    min_gap = max(0.5, 0.25 * duration / frame_budget(duration))
    for prev, cur in pairwise(frames):
        gap = cur.timestamp_s - prev.timestamp_s
        assert gap >= min_gap - 1e-9 or cur.gap_violated


def test_very_short_clip_still_yields_frames():
    frames = select_timestamps(3.0, [(t / 6, 0.5) for t in range(18)])
    assert len(frames) == 3
    assert all(f.timestamp_s >= 0 for f in frames)


def test_rotation_is_applied_to_display_size():
    # Vertical platform video is often stored 1920x1080 with a 90-degree display
    # matrix; ignoring rotation produces wrongly-oriented crops.
    assert MediaProbe(width=1920, height=1080, rotation=90).display_size == (1080, 1920)
    assert MediaProbe(width=1920, height=1080, rotation=90).is_vertical is True
    assert MediaProbe(width=1920, height=1080, rotation=0).is_vertical is False


def test_midpoint_fraction_ignores_the_thumbnail():
    # The thumbnail is not a sampled frame and must not skew the production
    # style proxy.
    frames = [
        SelectedFrame(0, 1.0, 5.0, "bucket_scene_max"),
        SelectedFrame(1, 2.0, 0.0, "bucket_midpoint"),
        SelectedFrame(2, 0.0, 0.0, "thumbnail"),
    ]
    assert midpoint_fraction(frames) == 0.5
