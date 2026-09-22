"""Pipeline phase state machine: legal edges, pause bookkeeping, reset."""

from __future__ import annotations

import pytest

from encomm_pcc.domain import PipelinePhase, StateMachine
from encomm_pcc.domain.state_machine import (
    ACTIVE_PIPELINE_PHASES,
    TRANSITIONS,
    InvalidTransitionError,
)


def test_starts_idle() -> None:
    assert StateMachine().phase is PipelinePhase.IDLE


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (PipelinePhase.IDLE, PipelinePhase.PLANNING_BATCH),
        (PipelinePhase.PLANNING_BATCH, PipelinePhase.RUNNING_TASK),
        (PipelinePhase.RUNNING_TASK, PipelinePhase.AUDITING_TASK),
        (PipelinePhase.AUDITING_TASK, PipelinePhase.FIX_REQUIRED),
        (PipelinePhase.FIX_REQUIRED, PipelinePhase.RUNNING_FIX),
        (PipelinePhase.RUNNING_FIX, PipelinePhase.AUDITING_TASK),
        (PipelinePhase.AUDITING_TASK, PipelinePhase.READY_FOR_FINAL_AUDIT),
        (PipelinePhase.AUDITING_TASK, PipelinePhase.RUNNING_TASK),
        (PipelinePhase.READY_FOR_FINAL_AUDIT, PipelinePhase.FINAL_AUDIT_RUNNING),
        (PipelinePhase.FINAL_AUDIT_RUNNING, PipelinePhase.BATCH_COMPLETE),
        (PipelinePhase.BATCH_COMPLETE, PipelinePhase.IDLE),
    ],
)
def test_nominal_path_edges_are_legal(source: PipelinePhase, target: PipelinePhase) -> None:
    assert StateMachine.can_transition(source, target)


def test_batch_complete_is_reachable_only_via_the_final_auditor() -> None:
    """Session 004: no successful path may reach BATCH_COMPLETE without the
    Final Auditor; the only incoming edge is FINAL_AUDIT_RUNNING."""
    sources = [s for s, targets in TRANSITIONS.items() if PipelinePhase.BATCH_COMPLETE in targets]
    assert sources == [PipelinePhase.FINAL_AUDIT_RUNNING]
    assert not StateMachine.can_transition(
        PipelinePhase.AUDITING_TASK, PipelinePhase.BATCH_COMPLETE
    )


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (PipelinePhase.IDLE, PipelinePhase.RUNNING_TASK),
        (PipelinePhase.IDLE, PipelinePhase.BATCH_COMPLETE),
        (PipelinePhase.RUNNING_TASK, PipelinePhase.BATCH_COMPLETE),
        (PipelinePhase.FAILED, PipelinePhase.RUNNING_TASK),
        (PipelinePhase.BATCH_COMPLETE, PipelinePhase.RUNNING_TASK),
    ],
)
def test_illegal_edges_are_rejected(source: PipelinePhase, target: PipelinePhase) -> None:
    assert not StateMachine.can_transition(source, target)


def test_transition_to_raises_on_illegal_edge() -> None:
    machine = StateMachine()
    with pytest.raises(InvalidTransitionError) as excinfo:
        machine.transition_to(PipelinePhase.BATCH_COMPLETE)
    assert excinfo.value.source is PipelinePhase.IDLE
    assert excinfo.value.target is PipelinePhase.BATCH_COMPLETE
    assert machine.phase is PipelinePhase.IDLE  # unchanged


def test_every_phase_has_an_entry_in_the_graph() -> None:
    assert set(TRANSITIONS) == set(PipelinePhase)


def test_every_target_is_a_real_phase_and_reachable() -> None:
    for source, targets in TRANSITIONS.items():
        for target in targets:
            assert isinstance(target, PipelinePhase), f"{source} -> {target!r}"
    # Every phase must be reachable from IDLE.
    seen: set[PipelinePhase] = set()
    frontier = [PipelinePhase.IDLE]
    while frontier:
        phase = frontier.pop()
        if phase in seen:
            continue
        seen.add(phase)
        frontier.extend(TRANSITIONS.get(phase, frozenset()))
    assert seen == set(PipelinePhase), f"unreachable: {set(PipelinePhase) - seen}"


def test_pause_remembers_resume_target() -> None:
    machine = StateMachine(PipelinePhase.RUNNING_TASK)
    machine.transition_to(PipelinePhase.PAUSED)
    assert machine.phase is PipelinePhase.PAUSED
    assert machine.resume_target() is PipelinePhase.RUNNING_TASK

    machine.transition_to(PipelinePhase.RUNNING_TASK)
    assert machine.resume_target() is PipelinePhase.IDLE  # cleared on resume


def test_pause_resume_targets_are_exactly_the_active_phases() -> None:
    assert StateMachine.allowed_from(PipelinePhase.PAUSED) == ACTIVE_PIPELINE_PHASES | {
        PipelinePhase.FAILED
    }


def test_terminal_phases() -> None:
    assert StateMachine.is_terminal(PipelinePhase.BATCH_COMPLETE)
    assert StateMachine.is_terminal(PipelinePhase.FAILED)
    assert not StateMachine.is_terminal(PipelinePhase.RUNNING_TASK)


def test_reset_returns_to_idle_from_anywhere() -> None:
    for phase in PipelinePhase:
        machine = StateMachine(phase)
        assert machine.reset() is PipelinePhase.IDLE
        assert machine.resume_target() is PipelinePhase.IDLE
