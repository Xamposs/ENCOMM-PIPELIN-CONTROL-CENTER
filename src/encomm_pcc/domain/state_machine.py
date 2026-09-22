"""Pipeline phase state machine.

The executor that drives these transitions is **not implemented in v0.1**.
What exists here is the contract: the legal transition graph, a validator, and
a small helper that remembers where a pause should resume to.

Keeping this declarative means the future executor cannot silently invent
transitions, and the UI can grey out invalid controls from the same table.
"""

from __future__ import annotations

from typing import Mapping

from .enums import TERMINAL_PIPELINE_PHASES, PipelinePhase

__all__ = [
    "ACTIVE_PIPELINE_PHASES",
    "PAUSE_RESUME_TARGETS",
    "TRANSITIONS",
    "InvalidTransitionError",
    "StateMachine",
]


class InvalidTransitionError(ValueError):
    """Raised when a phase transition is not permitted by the graph."""

    def __init__(self, source: PipelinePhase, target: PipelinePhase) -> None:
        self.source = source
        self.target = target
        super().__init__(
            f"Invalid pipeline transition: {source.value} -> {target.value}"
        )


#: Phases in which the pipeline is actively doing work (i.e. pausable work).
ACTIVE_PIPELINE_PHASES: frozenset[PipelinePhase] = frozenset(
    {
        PipelinePhase.PLANNING_BATCH,
        PipelinePhase.RUNNING_TASK,
        PipelinePhase.AUDITING_TASK,
        PipelinePhase.FIX_REQUIRED,
        PipelinePhase.RUNNING_FIX,
        PipelinePhase.READY_FOR_FINAL_AUDIT,
        PipelinePhase.FINAL_AUDIT_RUNNING,
    }
)

#: A paused pipeline may resume into any active phase; which one is decided by
#: ``StateMachine.resume_target`` using the phase recorded before the pause.
PAUSE_RESUME_TARGETS: frozenset[PipelinePhase] = ACTIVE_PIPELINE_PHASES

#: Legal transitions.  A phase absent from the mapping has no outgoing edges.
TRANSITIONS: Mapping[PipelinePhase, frozenset[PipelinePhase]] = {
    PipelinePhase.IDLE: frozenset({PipelinePhase.PLANNING_BATCH}),
    PipelinePhase.PLANNING_BATCH: frozenset(
        {
            PipelinePhase.RUNNING_TASK,
            PipelinePhase.BATCH_COMPLETE,
            PipelinePhase.PAUSED,
            PipelinePhase.BLOCKED,
            PipelinePhase.FAILED,
        }
    ),
    PipelinePhase.RUNNING_TASK: frozenset(
        {
            PipelinePhase.AUDITING_TASK,
            PipelinePhase.PAUSED,
            PipelinePhase.BLOCKED,
            PipelinePhase.FAILED,
        }
    ),
    PipelinePhase.AUDITING_TASK: frozenset(
        {
            PipelinePhase.FIX_REQUIRED,
            PipelinePhase.RUNNING_TASK,
            PipelinePhase.READY_FOR_FINAL_AUDIT,
            PipelinePhase.PAUSED,
            PipelinePhase.BLOCKED,
            PipelinePhase.FAILED,
        }
    ),
    PipelinePhase.FIX_REQUIRED: frozenset(
        {
            PipelinePhase.RUNNING_FIX,
            PipelinePhase.PAUSED,
            PipelinePhase.BLOCKED,
            PipelinePhase.FAILED,
        }
    ),
    PipelinePhase.RUNNING_FIX: frozenset(
        {
            PipelinePhase.AUDITING_TASK,
            PipelinePhase.PAUSED,
            PipelinePhase.BLOCKED,
            PipelinePhase.FAILED,
        }
    ),
    PipelinePhase.READY_FOR_FINAL_AUDIT: frozenset(
        {
            PipelinePhase.FINAL_AUDIT_RUNNING,
            PipelinePhase.PAUSED,
            PipelinePhase.BLOCKED,
            PipelinePhase.FAILED,
        }
    ),
    PipelinePhase.FINAL_AUDIT_RUNNING: frozenset(
        {
            PipelinePhase.BATCH_COMPLETE,
            PipelinePhase.FIX_REQUIRED,
            PipelinePhase.PAUSED,
            PipelinePhase.BLOCKED,
            PipelinePhase.FAILED,
        }
    ),
    PipelinePhase.BATCH_COMPLETE: frozenset(
        {
            PipelinePhase.IDLE,
            PipelinePhase.PLANNING_BATCH,
        }
    ),
    PipelinePhase.PAUSED: PAUSE_RESUME_TARGETS | {PipelinePhase.FAILED},
    PipelinePhase.BLOCKED: frozenset(
        {
            PipelinePhase.IDLE,
            PipelinePhase.PLANNING_BATCH,
            PipelinePhase.PAUSED,
            PipelinePhase.FAILED,
        }
    ),
    PipelinePhase.FAILED: frozenset({PipelinePhase.IDLE}),
}


class StateMachine:
    """Validates and tracks :class:`PipelinePhase` transitions."""

    def __init__(self, phase: PipelinePhase = PipelinePhase.IDLE) -> None:
        if not isinstance(phase, PipelinePhase):
            phase = PipelinePhase(str(phase))
        self._phase = phase
        #: Phase to resume into after a pause (``None`` when not paused).
        self._resume_phase: PipelinePhase | None = None

    # -- queries ---------------------------------------------------------
    @property
    def phase(self) -> PipelinePhase:
        return self._phase

    @property
    def resume_phase(self) -> PipelinePhase | None:
        return self._resume_phase

    @staticmethod
    def is_terminal(phase: PipelinePhase) -> bool:
        return phase in TERMINAL_PIPELINE_PHASES

    @staticmethod
    def allowed_from(phase: PipelinePhase) -> frozenset[PipelinePhase]:
        return TRANSITIONS.get(phase, frozenset())

    @staticmethod
    def can_transition(source: PipelinePhase, target: PipelinePhase) -> bool:
        """True when ``source -> target`` is a legal edge."""
        if not isinstance(source, PipelinePhase):
            source = PipelinePhase(str(source))
        if not isinstance(target, PipelinePhase):
            target = PipelinePhase(str(target))
        return target in TRANSITIONS.get(source, frozenset())

    def can_go_to(self, target: PipelinePhase) -> bool:
        return self.can_transition(self._phase, target)

    # -- mutations -------------------------------------------------------
    def transition_to(self, target: PipelinePhase) -> PipelinePhase:
        """Move to ``target``, raising on an illegal edge."""
        if not isinstance(target, PipelinePhase):
            target = PipelinePhase(str(target))
        if not self.can_transition(self._phase, target):
            raise InvalidTransitionError(self._phase, target)

        if target is PipelinePhase.PAUSED:
            # Remember where to come back to.
            self._resume_phase = self._phase
        elif self._phase is PipelinePhase.PAUSED:
            self._resume_phase = None

        self._phase = target
        return self._phase

    def resume_target(self) -> PipelinePhase:
        """Phase a paused pipeline should return to.

        Falls back to ``IDLE`` when nothing was recorded, so a resumed pipeline
        always has a defined starting point.
        """
        if self._resume_phase is not None:
            return self._resume_phase
        return PipelinePhase.IDLE

    def reset(self) -> PipelinePhase:
        """Return to ``IDLE`` from any phase (operator escape hatch)."""
        self._phase = PipelinePhase.IDLE
        self._resume_phase = None
        return self._phase

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"StateMachine(phase={self._phase.value!r}, resume={self._resume_phase})"
