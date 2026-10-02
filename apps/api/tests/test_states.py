"""Run state machine.

The enums are aligned to the Postgres enums research_run_state and
research_iteration_phase; a value that drifts is a write that fails at runtime
having looked correct everywhere else, so the alignment itself is tested.
"""

from itertools import pairwise

import pytest

from aypo.models.research import RunPhase, RunState
from aypo.research.states import (
    PHASE_ORDER,
    can_transition,
    is_wait_phase,
    next_phase,
)

# Exactly the values of the Postgres enums, checked against the live database.
DB_RUN_STATES = {
    "created",
    "planning",
    "iterating",
    "focusing",
    "stopping",
    "synthesizing",
    "completed",
    "failed",
    "paused",
    "cancelled",
}
DB_PHASES = {
    "allocate",
    "propose",
    "dedupe",
    "discover",
    "discover_wait",
    "retrieve_wait",
    "analyze_wait",
    "cluster",
    "interpret",
    "measure",
    "decide",
    "closed",
    "abandoned",
}


def test_run_states_match_the_database_enum():
    assert {s.value for s in RunState} == DB_RUN_STATES


def test_phases_match_the_database_enum():
    assert {p.value for p in RunPhase} == DB_PHASES


def test_phase_order_covers_the_normal_path():
    assert PHASE_ORDER[0] is RunPhase.ALLOCATE
    assert PHASE_ORDER[-1] is RunPhase.CLOSED
    # abandoned is reachable from anywhere, so it is not part of the sequence.
    assert RunPhase.ABANDONED not in PHASE_ORDER


def test_next_phase_saturates_rather_than_wrapping():
    phase = PHASE_ORDER[0]
    for _ in range(len(PHASE_ORDER) + 3):
        phase = next_phase(phase)
    assert phase is RunPhase.CLOSED


def test_abandoned_is_absorbing():
    assert next_phase(RunPhase.ABANDONED) is RunPhase.ABANDONED


def test_discovery_precedes_analysis():
    # Resuming at the wrong phase would pay for the same collection twice.
    assert PHASE_ORDER.index(RunPhase.DISCOVER) < PHASE_ORDER.index(RunPhase.ANALYZE_WAIT)
    assert PHASE_ORDER.index(RunPhase.CLUSTER) < PHASE_ORDER.index(RunPhase.MEASURE)


def test_wait_phases_are_identified():
    waits = {p for p in RunPhase if is_wait_phase(p)}
    assert waits == {RunPhase.DISCOVER_WAIT, RunPhase.RETRIEVE_WAIT, RunPhase.ANALYZE_WAIT}


def test_happy_path_is_allowed():
    path = [
        RunState.CREATED,
        RunState.PLANNING,
        RunState.ITERATING,
        RunState.STOPPING,
        RunState.SYNTHESIZING,
        RunState.COMPLETED,
    ]
    for current, target in pairwise(path):
        assert can_transition(current, target), f"{current} -> {target}"


def test_a_run_cannot_skip_straight_to_completed():
    assert not can_transition(RunState.CREATED, RunState.COMPLETED)
    # The stop reason is settled in `stopping`, before synthesis begins.
    assert not can_transition(RunState.ITERATING, RunState.SYNTHESIZING)
    assert not can_transition(RunState.ITERATING, RunState.COMPLETED)


def test_focusing_and_iterating_are_mutually_reachable():
    # A promising lead narrows the work; exhausting it widens again.
    assert can_transition(RunState.ITERATING, RunState.FOCUSING)
    assert can_transition(RunState.FOCUSING, RunState.ITERATING)


def test_paused_run_can_resume():
    assert can_transition(RunState.ITERATING, RunState.PAUSED)
    assert can_transition(RunState.PAUSED, RunState.ITERATING)


@pytest.mark.parametrize("state", [RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED])
def test_terminal_states_are_terminal(state):
    assert state.is_terminal
    assert not any(can_transition(state, other) for other in RunState)


def test_every_live_state_can_fail_or_cancel():
    for state in (
        RunState.CREATED,
        RunState.PLANNING,
        RunState.ITERATING,
        RunState.FOCUSING,
        RunState.STOPPING,
        RunState.SYNTHESIZING,
        RunState.PAUSED,
    ):
        assert can_transition(state, RunState.CANCELLED), state
        assert can_transition(state, RunState.FAILED), state
