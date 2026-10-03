"""Proposal worker thread — Proposal AI operations NEVER run on the UI thread.

Session 017 (brief §7).  Mirrors the proven Coding Mode pattern
(``ui/worker.py``): a :class:`QObject` moved onto a fresh :class:`QThread`,
one bounded action per run, the report back to the UI thread through a
queued ``finished`` signal.

The worker is handed ALREADY-CONFIGURED runtime pieces (workspace path,
state machine, drivers, iteration metadata) and calls the EXISTING
proposal_runtime executors — ``run_iteration()`` / ``run_hard_gates()``.
It duplicates no orchestration logic and supports NO unbounded loop:

* ``RUN_ITERATION``  — ONE composed iteration (review cycle → integration)
  through ``proposal_runtime.run_iteration``;
* ``RUN_HARD_GATES`` — the deterministic zero-AI gate engine through
  ``proposal_runtime.run_hard_gates``.

There is deliberately no "run forever" action: the operator presses the
next button when the report lands.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional

from PySide6.QtCore import QObject, QThread, Signal

from ..drivers.base import BaseDriver
from ..domain.enums import SessionPolicy
from ..proposal.models import ProposalAgentConfig
from ..proposal.state_machine import ProposalStateMachine
from ..proposal_runtime import run_hard_gates, run_iteration

__all__ = ["ProposalWorkerAction", "ProposalWorker", "start_proposal_worker"]


class ProposalWorkerAction(str, Enum):
    """The bounded Proposal Mode operations a worker may perform."""

    RUN_ITERATION = "RUN_ITERATION"
    RUN_HARD_GATES = "RUN_HARD_GATES"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


@dataclass(slots=True)
class ProposalRunSpec:
    """Everything ONE bounded proposal operation needs.

    Built by the panel from the operator's configuration BEFORE the thread
    starts; the worker touches no widgets.
    """

    action: ProposalWorkerAction
    workspace: Path
    iteration_number: int
    proposal_revision: str
    #: RUN_ITERATION only: drivers per reviewer role (all three required by
    #: the review loop) plus the optional ORCHESTRATOR integration driver.
    reviewer_drivers: Mapping[Any, BaseDriver] | None = None
    orchestrator_driver: Optional[BaseDriver] = None
    reviewer_session_policies: Mapping[Any, SessionPolicy] | None = None
    orchestrator_session_policy: SessionPolicy = SessionPolicy.ALWAYS_NEW
    timeout_s: Optional[float] = None
    #: State machine shared with the panel (rebuilt per run by the panel).
    state_machine: ProposalStateMachine | None = None
    #: Session 017A: the operator's per-role agent configs.  These EXACT
    #: objects reach the runtime executors, so the profile/provider/model in
    #: each driver SessionRequest is what the operator sees in AGENTS.
    reviewer_agent_configs: Mapping[Any, ProposalAgentConfig] | None = None
    orchestrator_agent_config: Optional[ProposalAgentConfig] = None


class ProposalWorker(QObject):
    """Runs ONE bounded proposal action; emits ``finished`` with the report."""

    finished = Signal(object)

    def __init__(self, spec: ProposalRunSpec, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.spec = spec

    def run(self) -> None:
        """Slot connected to ``QThread.started`` — never touches widgets."""
        machine = self.spec.state_machine
        try:
            if machine is None:
                raise ValueError("ProposalRunSpec.state_machine is required.")
            if self.spec.action is ProposalWorkerAction.RUN_ITERATION:
                report: object = self._run_iteration(machine)
            elif self.spec.action is ProposalWorkerAction.RUN_HARD_GATES:
                report = self._run_hard_gates(machine)
            else:
                raise ValueError(f"Unknown proposal action: {self.spec.action!r}")
        except Exception as exc:  # noqa: BLE001 - the UI must never lose a failure
            report = {
                "worker_error": f"{type(exc).__name__}: {exc}",
                "action": self.spec.action.value,
            }
        self.finished.emit(report)

    def _run_iteration(self, machine: ProposalStateMachine) -> object:
        if not self.spec.reviewer_drivers:
            raise ValueError("RUN_ITERATION requires reviewer_drivers.")
        report = run_iteration(
            workspace=Path(self.spec.workspace),
            state_machine=machine,
            iteration_number=int(self.spec.iteration_number),
            proposal_revision=str(self.spec.proposal_revision),
            reviewer_drivers=dict(self.spec.reviewer_drivers),
            orchestrator_driver=self.spec.orchestrator_driver,
            reviewer_session_policies=(
                dict(self.spec.reviewer_session_policies)
                if self.spec.reviewer_session_policies is not None
                else None
            ),
            orchestrator_session_policy=self.spec.orchestrator_session_policy,
            timeout_s=self.spec.timeout_s,
            reviewer_agent_configs=(
                dict(self.spec.reviewer_agent_configs)
                if self.spec.reviewer_agent_configs is not None
                else None
            ),
            orchestrator_agent_config=self.spec.orchestrator_agent_config,
        )
        return report.to_dict()

    def _run_hard_gates(self, machine: ProposalStateMachine) -> object:
        report = run_hard_gates(
            workspace=Path(self.spec.workspace),
            state_machine=machine,
            iteration_number=int(self.spec.iteration_number),
        )
        return report.to_dict()


def start_proposal_worker(
    spec: ProposalRunSpec,
    parent: QObject | None = None,
) -> tuple[QThread, ProposalWorker]:
    """Start ONE bounded proposal action on a fresh thread.

    The caller MUST keep both references (a running QThread is garbage
    collected otherwise) and connect ``worker.finished`` before starting.
    """
    thread = QThread(parent)
    # NO parent on the worker: a parented QObject cannot move to another
    # thread — moveToThread would silently fail and the "background" work
    # would run on the UI thread (the exact defect this file forbids).
    worker = ProposalWorker(spec)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    return thread, worker
