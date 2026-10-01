"""Proposal Mode state machine — a parallel domain contract.

This module is deliberately SEPARATE from the Coding Mode
``encomm_pcc.domain.state_machine``: it shares the declarative-graph
convention but no types, no edges and no semantics.  Nothing here may import
``PipelinePhase``, ``TRANSITIONS`` or any other Coding Mode symbol.

Lifecycle (deterministic):

    IDLE
      → SOURCE_VALIDATION
      → SCIENTIFIC_REVIEW
      → IMPLEMENTATION_REVIEW
      → RED_TEAM_REVIEW
      → INTEGRATION
      → HARD_GATE_VALIDATION
          → COMPLETE            (terminal, never reopens)
          → REVISION_REQUIRED   (begins a fresh review iteration)
          → BLOCKED / FAILED

REVISION_REQUIRED → SCIENTIFIC_REVIEW starts the next iteration without
re-creating application state.  PAUSED is entered only from active work and
resumes into the recorded phase.  Invalid transitions raise
:class:`InvalidProposalTransitionError` and leave the phase unchanged.
"""

from __future__ import annotations

from typing import Mapping

from .enums import ProposalPhase

__all__ = [
    "ACTIVE_PROPOSAL_PHASES",
    "PAUSABLE_PROPOSAL_PHASES",
    "PROPOSAL_TRANSITIONS",
    "REVIEW_ITERATION_ENTRY",
    "TERMINAL_PROPOSAL_PHASES",
    "InvalidProposalTransitionError",
    "ProposalStateMachine",
    "validate_proposal_graph",
]


class InvalidProposalTransitionError(ValueError):
    """Raised when a proposal phase transition is not permitted by the graph."""

    def __init__(self, source: ProposalPhase, target: ProposalPhase) -> None:
        self.source = source
        self.target = target
        super().__init__(
            f"Invalid proposal transition: {source.value} -> {target.value}"
        )


#: Phases in which the proposal loop is actively working (pausable work).
ACTIVE_PROPOSAL_PHASES: frozenset[ProposalPhase] = frozenset(
    {
        ProposalPhase.SOURCE_VALIDATION,
        ProposalPhase.SCIENTIFIC_REVIEW,
        ProposalPhase.IMPLEMENTATION_REVIEW,
        ProposalPhase.RED_TEAM_REVIEW,
        ProposalPhase.INTEGRATION,
        ProposalPhase.HARD_GATE_VALIDATION,
    }
)

#: Control/waiting phases that may also be paused (an operator may park a
#: revision before starting the next review iteration).
PAUSABLE_PROPOSAL_PHASES: frozenset[ProposalPhase] = (
    ACTIVE_PROPOSAL_PHASES | {ProposalPhase.REVISION_REQUIRED}
)

#: Terminal proposal phases.  ``COMPLETE`` has NO outgoing edges by
#: construction — a completed proposal never automatically reopens.
TERMINAL_PROPOSAL_PHASES: frozenset[ProposalPhase] = frozenset(
    {ProposalPhase.COMPLETE, ProposalPhase.FAILED}
)

#: The phase a fresh review iteration begins at after ``REVISION_REQUIRED``.
REVIEW_ITERATION_ENTRY: ProposalPhase = ProposalPhase.SCIENTIFIC_REVIEW

#: Legal transitions.  A phase absent from the mapping has no outgoing edges.
PROPOSAL_TRANSITIONS: Mapping[ProposalPhase, frozenset[ProposalPhase]] = {
    ProposalPhase.IDLE: frozenset({ProposalPhase.SOURCE_VALIDATION}),
    ProposalPhase.SOURCE_VALIDATION: frozenset(
        {
            ProposalPhase.SCIENTIFIC_REVIEW,
            ProposalPhase.PAUSED,
            ProposalPhase.BLOCKED,
            ProposalPhase.FAILED,
        }
    ),
    ProposalPhase.SCIENTIFIC_REVIEW: frozenset(
        {
            ProposalPhase.IMPLEMENTATION_REVIEW,
            ProposalPhase.PAUSED,
            ProposalPhase.BLOCKED,
            ProposalPhase.FAILED,
        }
    ),
    ProposalPhase.IMPLEMENTATION_REVIEW: frozenset(
        {
            ProposalPhase.RED_TEAM_REVIEW,
            ProposalPhase.PAUSED,
            ProposalPhase.BLOCKED,
            ProposalPhase.FAILED,
        }
    ),
    ProposalPhase.RED_TEAM_REVIEW: frozenset(
        {
            ProposalPhase.INTEGRATION,
            ProposalPhase.PAUSED,
            ProposalPhase.BLOCKED,
            ProposalPhase.FAILED,
        }
    ),
    ProposalPhase.INTEGRATION: frozenset(
        {
            ProposalPhase.HARD_GATE_VALIDATION,
            ProposalPhase.PAUSED,
            ProposalPhase.BLOCKED,
            ProposalPhase.FAILED,
        }
    ),
    ProposalPhase.HARD_GATE_VALIDATION: frozenset(
        {
            ProposalPhase.COMPLETE,
            ProposalPhase.REVISION_REQUIRED,
            ProposalPhase.BLOCKED,
            ProposalPhase.FAILED,
        }
    ),
    ProposalPhase.REVISION_REQUIRED: frozenset(
        {
            REVIEW_ITERATION_ENTRY,
            ProposalPhase.PAUSED,
            ProposalPhase.BLOCKED,
            ProposalPhase.FAILED,
        }
    ),
    # COMPLETE is terminal: no outgoing edges, ever.
    ProposalPhase.COMPLETE: frozenset(),
    ProposalPhase.PAUSED: PAUSABLE_PROPOSAL_PHASES | {ProposalPhase.FAILED},
    # BLOCKED recovery is an explicit operator decision back into the phase
    # where the block occurred (source re-check, review restart, or a re-run
    # of the hard gates) — never an automatic edge.
    ProposalPhase.BLOCKED: frozenset(
        {
            ProposalPhase.SOURCE_VALIDATION,
            REVIEW_ITERATION_ENTRY,
            ProposalPhase.HARD_GATE_VALIDATION,
            ProposalPhase.PAUSED,
            ProposalPhase.FAILED,
        }
    ),
    # FAILED is terminal: the operator starts a new proposal run (reset()).
    ProposalPhase.FAILED: frozenset(),
}


def validate_proposal_graph() -> list[str]:
    """Return a list of graph defects (empty when the graph is consistent).

    Called by tests; kept as a public helper so a future session extending
    the graph can re-check it without re-deriving the invariants.
    """
    defects: list[str] = []
    known = set(ProposalPhase)
    for source, targets in PROPOSAL_TRANSITIONS.items():
        if source not in known:
            defects.append(f"unknown source phase: {source}")
        for target in targets:
            if target not in known:
                defects.append(f"unknown target phase: {source} -> {target}")
    for phase in known:
        if phase not in PROPOSAL_TRANSITIONS:
            defects.append(f"phase missing from graph: {phase}")
    if ProposalPhase.COMPLETE not in TERMINAL_PROPOSAL_PHASES:
        defects.append("COMPLETE must be terminal")
    if PROPOSAL_TRANSITIONS[ProposalPhase.COMPLETE]:
        defects.append("COMPLETE must have no outgoing edges")
    # Reachability from IDLE.
    reachable = {ProposalPhase.IDLE}
    changed = True
    while changed:
        changed = False
        for source in list(reachable):
            for target in PROPOSAL_TRANSITIONS.get(source, frozenset()):
                if target not in reachable:
                    reachable.add(target)
                    changed = True
    for phase in known - reachable:
        defects.append(f"phase unreachable from IDLE: {phase}")
    return defects


class ProposalStateMachine:
    """Validates and tracks :class:`ProposalPhase` transitions.

    Mirrors the Coding Mode machine's operator semantics (recorded pause
    resume target, explicit escape-hatch reset) without sharing any of its
    types or edges.
    """

    def __init__(self, phase: ProposalPhase = ProposalPhase.IDLE) -> None:
        if not isinstance(phase, ProposalPhase):
            phase = ProposalPhase(str(phase))
        self._phase = phase
        #: Phase to resume into after a pause (``None`` when not paused).
        self._resume_phase: ProposalPhase | None = None

    # -- queries ---------------------------------------------------------
    @property
    def phase(self) -> ProposalPhase:
        return self._phase

    @property
    def resume_phase(self) -> ProposalPhase | None:
        return self._resume_phase

    @staticmethod
    def is_terminal(phase: ProposalPhase) -> bool:
        return phase in TERMINAL_PROPOSAL_PHASES

    @staticmethod
    def allowed_from(phase: ProposalPhase) -> frozenset[ProposalPhase]:
        return PROPOSAL_TRANSITIONS.get(phase, frozenset())

    @staticmethod
    def can_transition(source: ProposalPhase, target: ProposalPhase) -> bool:
        """True when ``source -> target`` is a legal edge."""
        if not isinstance(source, ProposalPhase):
            source = ProposalPhase(str(source))
        if not isinstance(target, ProposalPhase):
            target = ProposalPhase(str(target))
        return target in PROPOSAL_TRANSITIONS.get(source, frozenset())

    def can_go_to(self, target: ProposalPhase) -> bool:
        return self.can_transition(self._phase, target)

    # -- mutations -------------------------------------------------------
    def transition_to(self, target: ProposalPhase) -> ProposalPhase:
        """Move to ``target``, raising on an illegal edge.

        On an illegal edge the phase is left unchanged and
        :class:`InvalidProposalTransitionError` is raised — invalid
        transitions fail explicitly, never silently.
        """
        if not isinstance(target, ProposalPhase):
            target = ProposalPhase(str(target))
        if not self.can_transition(self._phase, target):
            raise InvalidProposalTransitionError(self._phase, target)

        if target is ProposalPhase.PAUSED:
            # Remember where to come back to.
            self._resume_phase = self._phase
        elif self._phase is ProposalPhase.PAUSED:
            self._resume_phase = None

        self._phase = target
        return self._phase

    def resume_target(self) -> ProposalPhase:
        """Phase a paused proposal loop should return to.

        Falls back to ``IDLE`` when nothing was recorded, so a resumed loop
        always has a defined starting point.
        """
        if self._resume_phase is not None:
            return self._resume_phase
        return ProposalPhase.IDLE

    def reset(self) -> ProposalPhase:
        """Return to ``IDLE`` from any phase (explicit operator escape hatch).

        This is the ONLY way out of ``FAILED``; ``COMPLETE`` is likewise not
        special-cased here — a completed proposal is reopened by constructing
        a new run, never by transitioning.
        """
        self._phase = ProposalPhase.IDLE
        self._resume_phase = None
        return self._phase

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"ProposalStateMachine(phase={self._phase.value!r}, resume={self._resume_phase})"
