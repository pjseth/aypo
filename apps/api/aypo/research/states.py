"""Run and iteration state helpers.

The enums themselves live in ``aypo.models.research`` and are **aligned to the
Postgres enums** ``research_run_state`` and ``research_iteration_phase``. They
are re-exported here so callers have one import, but they are deliberately not
redefined: an enum that drifts from the database is a write that fails at
runtime, having looked correct everywhere else.

This module adds what the models do not: legal transitions, phase ordering, and
the mapping from a phase to whether its work is resumable.
"""

from __future__ import annotations

from aypo.models.research import RunPhase, RunState

__all__ = [
    "PHASE_ORDER",
    "InvalidTransition",
    "RunPhase",
    "RunState",
    "can_transition",
    "is_wait_phase",
    "next_phase",
]

# The order one iteration moves through. The *_wait phases exist because the
# work they cover is asynchronous and long: a run that dies during analysis must
# resume at analyze_wait rather than re-running discovery and paying for the
# same collection twice.
PHASE_ORDER: tuple[RunPhase, ...] = (
    RunPhase.ALLOCATE,
    RunPhase.PROPOSE,
    RunPhase.DEDUPE,
    RunPhase.DISCOVER,
    RunPhase.DISCOVER_WAIT,
    RunPhase.RETRIEVE_WAIT,
    RunPhase.ANALYZE_WAIT,
    RunPhase.CLUSTER,
    RunPhase.INTERPRET,
    RunPhase.MEASURE,
    RunPhase.DECIDE,
    RunPhase.CLOSED,
)

_WAIT_PHASES = frozenset({RunPhase.DISCOVER_WAIT, RunPhase.RETRIEVE_WAIT, RunPhase.ANALYZE_WAIT})


def is_wait_phase(phase: RunPhase) -> bool:
    """Whether the phase is waiting on outstanding background work."""
    return phase in _WAIT_PHASES


def next_phase(phase: RunPhase) -> RunPhase:
    """The next phase, saturating at CLOSED rather than wrapping."""
    if phase is RunPhase.ABANDONED:
        return RunPhase.ABANDONED
    index = PHASE_ORDER.index(phase)
    return PHASE_ORDER[min(index + 1, len(PHASE_ORDER) - 1)]


_ALLOWED: dict[RunState, set[RunState]] = {
    RunState.CREATED: {RunState.PLANNING, RunState.CANCELLED, RunState.FAILED},
    RunState.PLANNING: {RunState.ITERATING, RunState.FAILED, RunState.CANCELLED},
    # A run may move between iterating and focusing in either direction: a
    # promising lead narrows the work, and exhausting it widens again.
    RunState.ITERATING: {
        RunState.FOCUSING,
        RunState.STOPPING,
        RunState.PAUSED,
        RunState.FAILED,
        RunState.CANCELLED,
    },
    RunState.FOCUSING: {
        RunState.ITERATING,
        RunState.STOPPING,
        RunState.PAUSED,
        RunState.FAILED,
        RunState.CANCELLED,
    },
    RunState.STOPPING: {RunState.SYNTHESIZING, RunState.FAILED, RunState.CANCELLED},
    # Synthesis may complete with no findings rather than failing: a run that
    # collected and analysed real content has value even when nothing cleared
    # the evidence bar.
    RunState.SYNTHESIZING: {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED},
    RunState.PAUSED: {RunState.ITERATING, RunState.CANCELLED, RunState.FAILED},
    RunState.COMPLETED: set(),
    RunState.FAILED: set(),
    RunState.CANCELLED: set(),
}


def can_transition(current: RunState, target: RunState) -> bool:
    return target in _ALLOWED[current]


class InvalidTransition(RuntimeError):
    def __init__(self, current: RunState, target: RunState) -> None:
        super().__init__(f"cannot move a run from {current.value} to {target.value}")
        self.current = current
        self.target = target
