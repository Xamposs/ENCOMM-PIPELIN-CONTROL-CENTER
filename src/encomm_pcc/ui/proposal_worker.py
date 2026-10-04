"""Proposal worker thread — Proposal AI operations NEVER run on the UI thread.

Session 017 (brief §7).  Mirrors the proven Coding Mode pattern
(``ui/worker.py``): a :class:`QObject` moved onto a fresh :class:`QThread`,
one bounded action per run, the report back to the UI thread through a
queued ``finished`` signal.

The worker is handed ALREADY-CONFIGURED runtime pieces (workspace path,
state machine, drivers, iteration metadata) and calls the EXISTING
proposal_runtime executors.  It duplicates no orchestration logic:

* ``RUN_ITERATION``    — ONE composed iteration (review cycle → integration)
  through ``proposal_runtime.run_iteration`` (the legacy sequential path,
  kept for partial-phase resume compatibility);
* ``RUN_HARD_GATES``   — the deterministic zero-AI gate engine through
  ``proposal_runtime.run_hard_gates``;
* ``GENERATE_INITIAL`` — Session 020: the REAL initial generation
  (three specialists in parallel → ASTRA synthesis) through
  ``run_initial_generation``;
* ``RUN_PANEL``        — Session 020: ONE production V2 panel-chair
  iteration (3 parallel evaluators → 3 parallel consensus calls → ASTRA
  chair) through ``run_panel_chair_iteration`` — NEVER the legacy
  sequential cycle;
* ``START_CAMPAIGN``   — Session 020: the bounded autonomous campaign
  through ``run_campaign`` (fresh state);
* ``RESUME_CAMPAIGN``  — Session 020: the REAL campaign recovery path —
  ``load_campaign_state`` (read-only) then ``run_campaign`` continuing at
  the persisted checkpoint.  Nothing auto-runs; this action is started
  ONLY by an explicit operator click.

There is deliberately no "run forever" action: the campaign loop itself is
bounded by ``campaign.CampaignConfig`` and checkpoints durable state.

PAUSE / STOP are BOUNDARY requests (Session 020, D-018 heritage): the UI
arms :class:`CampaignControl` flags which ``run_campaign`` samples at
stage boundaries ONLY — a running model call always finishes first.  The
worker never pretends an in-flight Hermes process was cancelled instantly.
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
from ..proposal.source_budget import SourceBudget
from ..proposal.state_machine import ProposalStateMachine
from ..proposal_runtime import run_hard_gates, run_iteration
from ..proposal_runtime.campaign import (
    CampaignConfig,
    CampaignState,
    load_campaign_state,
    run_campaign,
    save_campaign_config,
)
from ..proposal_runtime.initial_generation import run_initial_generation
from ..proposal_runtime.panel_chair import run_panel_chair_iteration

__all__ = [
    "ProposalWorkerAction",
    "CampaignControl",
    "ProposalWorker",
    "ProposalRunSpec",
    "start_proposal_worker",
]


class ProposalWorkerAction(str, Enum):
    """The bounded Proposal Mode operations a worker may perform."""

    RUN_ITERATION = "RUN_ITERATION"
    RUN_HARD_GATES = "RUN_HARD_GATES"
    #: Session 020 — Proposal Factory V2 production actions.
    GENERATE_INITIAL = "GENERATE_INITIAL"
    RUN_PANEL = "RUN_PANEL"
    START_CAMPAIGN = "START_CAMPAIGN"
    RESUME_CAMPAIGN = "RESUME_CAMPAIGN"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


class CampaignControl:
    """Live boundary control surface handed to ``run_campaign(control=...)``.

    The UI arms a flag; the campaign samples it at the NEXT safe stage
    boundary and honours it there.  There is NO instant-cancel path —
    Hermes has no guaranteed mid-prompt cancellation (Session 020 §13).
    """

    def __init__(self) -> None:
        self.pause_requested: bool = False
        self.stop_requested: bool = False


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
    #: RUN_ITERATION / GENERATE_INITIAL / RUN_PANEL / campaign actions: the
    #: three evaluator roles' drivers.  For GENERATE_INITIAL the SAME three
    #: role drivers serve as the generation specialists (the backend
    #: requires its OWN instance per role and refuses sharing).
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
    #: Session 020: the operator-configured source budget (blocks BEFORE any
    #: model call when the canonical blueprint exceeds it).
    source_budget: Optional[SourceBudget] = None
    #: Session 020: campaign bounds (START_CAMPAIGN / RESUME_CAMPAIGN).
    campaign_config: Optional[CampaignConfig] = None
    #: Session 020: the live boundary control surface (pause/stop flags).
    campaign_control: Optional[CampaignControl] = None
    #: Session 020: RESUME_CAMPAIGN re-enters at the persisted checkpoint.
    resume_campaign: bool = False


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
            elif self.spec.action is ProposalWorkerAction.GENERATE_INITIAL:
                report = self._run_generate_initial(machine)
            elif self.spec.action is ProposalWorkerAction.RUN_PANEL:
                report = self._run_panel(machine)
            elif self.spec.action is ProposalWorkerAction.START_CAMPAIGN:
                report = self._run_campaign(machine, resume=False)
            elif self.spec.action is ProposalWorkerAction.RESUME_CAMPAIGN:
                report = self._run_campaign(machine, resume=True)
            else:
                raise ValueError(f"Unknown proposal action: {self.spec.action!r}")
        except Exception as exc:  # noqa: BLE001 - the UI must never lose a failure
            report = {
                "worker_error": f"{type(exc).__name__}: {exc}",
                "action": self.spec.action.value,
            }
        self.finished.emit(report)

    def _reviewer_map(self) -> dict[Any, BaseDriver]:
        if not self.spec.reviewer_drivers:
            raise ValueError(
                f"{self.spec.action.value} requires reviewer_drivers."
            )
        return dict(self.spec.reviewer_drivers)

    def _run_iteration(self, machine: ProposalStateMachine) -> object:
        report = run_iteration(
            workspace=Path(self.spec.workspace),
            state_machine=machine,
            iteration_number=int(self.spec.iteration_number),
            proposal_revision=str(self.spec.proposal_revision),
            reviewer_drivers=self._reviewer_map(),
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

    def _run_generate_initial(self, machine: ProposalStateMachine) -> object:
        if self.spec.orchestrator_driver is None:
            raise ValueError(
                "GENERATE_INITIAL requires the ORCHESTRATOR (ASTRA) driver."
            )
        report = run_initial_generation(
            workspace=Path(self.spec.workspace),
            state_machine=machine,
            proposal_revision=str(self.spec.proposal_revision),
            specialist_drivers=self._reviewer_map(),
            orchestrator_driver=self.spec.orchestrator_driver,
            specialist_agent_configs=(
                dict(self.spec.reviewer_agent_configs)
                if self.spec.reviewer_agent_configs is not None
                else None
            ),
            orchestrator_agent_config=self.spec.orchestrator_agent_config,
            timeout_s=self.spec.timeout_s,
            source_budget=self.spec.source_budget,
        )
        return report.to_dict()

    def _run_panel(self, machine: ProposalStateMachine) -> object:
        if self.spec.orchestrator_driver is None:
            raise ValueError(
                "RUN_PANEL requires the ORCHESTRATOR (panel chair) driver."
            )
        # The production V2 button path: the panel-chair composition (3
        # parallel evaluators → 3 parallel consensus calls → ASTRA chair).
        # NEVER the legacy sequential run_review_cycle (Session 020 §19).
        report = run_panel_chair_iteration(
            workspace=Path(self.spec.workspace),
            state_machine=machine,
            iteration_number=int(self.spec.iteration_number),
            proposal_revision=str(self.spec.proposal_revision),
            reviewer_drivers=self._reviewer_map(),
            orchestrator_driver=self.spec.orchestrator_driver,
            reviewer_agent_configs=(
                dict(self.spec.reviewer_agent_configs)
                if self.spec.reviewer_agent_configs is not None
                else None
            ),
            orchestrator_agent_config=self.spec.orchestrator_agent_config,
            timeout_s=self.spec.timeout_s,
            source_budget=self.spec.source_budget,
        )
        return report.to_dict()

    def _run_campaign(
        self, machine: ProposalStateMachine, *, resume: bool
    ) -> object:
        workspace = Path(self.spec.workspace)
        if self.spec.campaign_config is None:
            raise ValueError(
                f"{self.spec.action.value} requires campaign_config."
            )
        config = self.spec.campaign_config
        # The bounds are persisted BEFORE the run so a restart recovers the
        # SAME contract (campaign.py owns the durable state file).
        save_campaign_config(workspace, config)
        state: CampaignState | None = None
        if resume:
            # Read-only recovery: re-enter at the persisted checkpoint.  A
            # terminal campaign (COMPLETE / FAILED) re-reports and runs
            # nothing (the runtime's own consistency guard).
            state = load_campaign_state(workspace)
        report = run_campaign(
            workspace=workspace,
            state_machine=machine,
            config=config,
            specialist_drivers=self._reviewer_map(),
            reviewer_drivers=self._reviewer_map(),
            orchestrator_driver=self.spec.orchestrator_driver,
            specialist_agent_configs=(
                dict(self.spec.reviewer_agent_configs)
                if self.spec.reviewer_agent_configs is not None
                else None
            ),
            reviewer_agent_configs=(
                dict(self.spec.reviewer_agent_configs)
                if self.spec.reviewer_agent_configs is not None
                else None
            ),
            orchestrator_agent_config=self.spec.orchestrator_agent_config,
            timeout_s=self.spec.timeout_s,
            source_budget=self.spec.source_budget,
            state=state,
            control=self.spec.campaign_control,
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
