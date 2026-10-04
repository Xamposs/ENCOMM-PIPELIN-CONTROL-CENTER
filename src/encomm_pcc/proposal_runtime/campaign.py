"""AUTONOMOUS PANEL CAMPAIGN — bounded, restart-safe loop (Session 019,
briefs §24–§28).

The campaign is a CONTROLLED driver over the existing executors — it owns
NO new model-call logic::

    (master empty?)
      → run_initial_generation()                     [4 calls]
    while the campaign may continue:
      → run_panel_chair_iteration()                  [7 calls / 6 clean-pass]
      → hard gates when review-current at HARD_GATE_VALIDATION
      → compute stop conditions; checkpoint durable state

Durable state (atomic writes, workspace-bound):
    05_CONTROL/CAMPAIGN_CONFIG.json   operator bounds + per-role config
    05_CONTROL/CAMPAIGN_STATE.json    campaign id, iteration, stage,
                                      accumulated seconds, model-call count,
                                      readiness history, stop condition

Stop conditions (NEVER an infinite loop):
    COMPLETE           gates resolved COMPLETE
    WAITING_FOR_OPERATOR   gates BLOCKED on operator evidence (no further
                           model calls are burned)
    CONVERGED          no meaningful improvement for ``no_improvement_limit``
                       consecutive iterations
    BOUND_REACHED      max hours / max iterations / max model calls
    PAUSED             operator pause honoured at the next safe boundary
    STOPPED            operator stop honoured at the next safe boundary
    FAILED             operational failure

Restart semantics: state recovery is READ-ONLY — ``load_campaign_state``
rebuilds the campaign view from durable artifacts; the application NEVER
auto-resumes AI on launch.  ``resume=True`` on an explicit operator action
continues at the next safe checkpoint (the executors' own artifact/resume
contracts prevent duplicated model-call stages).
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional

from ..drivers.base import BaseDriver
from ..domain.enums import SessionPolicy
from ..proposal.enums import ProposalPhase
from ..proposal.fingerprint import proposal_fingerprint
from ..proposal.models import ProposalAgentConfig
from ..proposal.source_budget import SourceBudget
from ..proposal.state_machine import ProposalStateMachine
from .hard_gate_runner import ProposalHardGateRunOutcome, run_hard_gates
from .initial_generation import (
    InitialGenerationOutcome,
    run_initial_generation,
)
from .panel_chair import (
    PanelChairOutcome,
    run_panel_chair_iteration,
)

__all__ = [
    "CAMPAIGN_CONFIG_SCHEMA",
    "CAMPAIGN_STATE_SCHEMA",
    "CAMPAIGN_CONFIG_FILENAME",
    "CAMPAIGN_STATE_FILENAME",
    "CampaignStatus",
    "CampaignStopCondition",
    "CampaignConfig",
    "CampaignState",
    "CampaignReport",
    "load_campaign_config",
    "load_campaign_state",
    "run_campaign",
]


CAMPAIGN_CONFIG_FILENAME = "CAMPAIGN_CONFIG.json"
CAMPAIGN_STATE_FILENAME = "CAMPAIGN_STATE.json"
CAMPAIGN_CONFIG_SCHEMA = "encomm-pcc.campaign-config/v1"
CAMPAIGN_STATE_SCHEMA = "encomm-pcc.campaign-state/v1"

#: Hard caps (the brief's bounds; the operator configures within them).
MAX_HOURS_LIMIT = 120
MAX_ITERATIONS_LIMIT = 50
MAX_MODEL_CALLS_LIMIT = 100_000
DEFAULT_NO_IMPROVEMENT_LIMIT = 3


class CampaignStatus(str, Enum):
    """Operator-visible campaign status (brief §24)."""

    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    WAITING_FOR_OPERATOR = "WAITING_FOR_OPERATOR"
    CONVERGED = "CONVERGED"
    COMPLETE = "COMPLETE"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    BOUND_REACHED = "BOUND_REACHED"
    NOT_STARTED = "NOT_STARTED"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


class CampaignStopCondition(str, Enum):
    """WHY the campaign left its loop (machine-readable)."""

    COMPLETE = "COMPLETE"
    WAITING_FOR_OPERATOR = "WAITING_FOR_OPERATOR"
    CONVERGED = "CONVERGED"
    MAX_HOURS = "MAX_HOURS"
    MAX_ITERATIONS = "MAX_ITERATIONS"
    MAX_MODEL_CALLS = "MAX_MODEL_CALLS"
    OPERATOR_PAUSE = "OPERATOR_PAUSE"
    OPERATOR_STOP = "OPERATOR_STOP"
    FAILED = "FAILED"
    NONE = "NONE"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


@dataclass(slots=True)
class CampaignConfig:
    """Operator-configured bounds (validated, persisted)."""

    max_hours: float = 24.0
    max_iterations: int = 10
    target_readiness: float = 92.0
    max_model_calls: int = 1_000
    no_improvement_limit: int = DEFAULT_NO_IMPROVEMENT_LIMIT

    def __post_init__(self) -> None:
        if not isinstance(self.max_hours, (int, float)) or isinstance(
            self.max_hours, bool
        ) or not (0 < self.max_hours <= MAX_HOURS_LIMIT):
            raise ValueError(
                f"max_hours must be in (0, {MAX_HOURS_LIMIT}] hours."
            )
        if not isinstance(self.max_iterations, int) or isinstance(
            self.max_iterations, bool
        ) or not (1 <= self.max_iterations <= MAX_ITERATIONS_LIMIT):
            raise ValueError(
                f"max_iterations must be 1..{MAX_ITERATIONS_LIMIT}."
            )
        if not isinstance(self.target_readiness, (int, float)) or isinstance(
            self.target_readiness, bool
        ) or not (0 < self.target_readiness <= 100):
            raise ValueError("target_readiness must be in (0, 100].")
        if not isinstance(self.max_model_calls, int) or isinstance(
            self.max_model_calls, bool
        ) or not (1 <= self.max_model_calls <= MAX_MODEL_CALLS_LIMIT):
            raise ValueError(
                f"max_model_calls must be 1..{MAX_MODEL_CALLS_LIMIT}."
            )
        if not isinstance(self.no_improvement_limit, int) or isinstance(
            self.no_improvement_limit, bool
        ) or not (1 <= self.no_improvement_limit <= 10):
            raise ValueError("no_improvement_limit must be 1..10.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CAMPAIGN_CONFIG_SCHEMA,
            "max_hours": self.max_hours,
            "max_iterations": self.max_iterations,
            "target_readiness": self.target_readiness,
            "max_model_calls": self.max_model_calls,
            "no_improvement_limit": self.no_improvement_limit,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CampaignConfig":
        if data.get("schema") not in (CAMPAIGN_CONFIG_SCHEMA,):
            raise ValueError(
                f"campaign config schema {data.get('schema')!r} != "
                f"{CAMPAIGN_CONFIG_SCHEMA!r}"
            )
        return cls(
            max_hours=data.get("max_hours", 24.0),
            max_iterations=int(data.get("max_iterations", 10)),
            target_readiness=data.get("target_readiness", 92.0),
            max_model_calls=int(data.get("max_model_calls", 1_000)),
            no_improvement_limit=int(
                data.get("no_improvement_limit", DEFAULT_NO_IMPROVEMENT_LIMIT)
            ),
        )


@dataclass(slots=True)
class CampaignState:
    """Durable, restart-safe campaign state."""

    campaign_id: str = ""
    status: CampaignStatus = CampaignStatus.NOT_STARTED
    stop_condition: CampaignStopCondition = CampaignStopCondition.NONE
    iteration: int = 0
    stage: str = ""
    start_epoch_s: float = 0.0
    accumulated_run_s: float = 0.0
    model_calls_used: int = 0
    last_readiness: Optional[float] = None
    readiness_history: list[dict[str, Any]] = field(default_factory=list)
    current_proposal_hash: str = ""
    last_good_artifact: str = ""
    error: str = ""
    #: Operator control flags (armed in-process by the UI; consumed at the
    #: next safe stage boundary — never mid-stage).
    pause_requested: bool = False
    stop_requested: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CAMPAIGN_STATE_SCHEMA,
            "campaign_id": self.campaign_id,
            "status": self.status.value,
            "stop_condition": self.stop_condition.value,
            "iteration": self.iteration,
            "stage": self.stage,
            "start_epoch_s": self.start_epoch_s,
            "accumulated_run_s": round(self.accumulated_run_s, 3),
            "model_calls_used": self.model_calls_used,
            "last_readiness": self.last_readiness,
            "readiness_history": list(self.readiness_history),
            "current_proposal_hash": self.current_proposal_hash,
            "last_good_artifact": self.last_good_artifact,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CampaignState":
        if data.get("schema") not in (CAMPAIGN_STATE_SCHEMA,):
            raise ValueError(
                f"campaign state schema {data.get('schema')!r} != "
                f"{CAMPAIGN_STATE_SCHEMA!r}"
            )
        readiness_history = data.get("readiness_history") or []
        last_readiness_raw = data.get("last_readiness")
        return cls(
            campaign_id=str(data.get("campaign_id") or ""),
            status=CampaignStatus(str(data.get("status") or "NOT_STARTED")),
            stop_condition=CampaignStopCondition(
                str(data.get("stop_condition") or "NONE")
            ),
            iteration=int(data.get("iteration") or 0),
            stage=str(data.get("stage") or ""),
            start_epoch_s=float(data.get("start_epoch_s") or 0.0),
            accumulated_run_s=float(data.get("accumulated_run_s") or 0.0),
            model_calls_used=int(data.get("model_calls_used") or 0),
            last_readiness=(
                float(last_readiness_raw)
                if last_readiness_raw is not None
                else None
            ),
            readiness_history=[dict(h) for h in readiness_history],
            current_proposal_hash=str(data.get("current_proposal_hash") or ""),
            last_good_artifact=str(data.get("last_good_artifact") or ""),
            error=str(data.get("error") or ""),
        )


def _atomic_write_json(target: Path, payload: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    data = (text + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp-camp-")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise


def _config_path(workspace: Path) -> Path:
    return workspace / "05_CONTROL" / CAMPAIGN_CONFIG_FILENAME


def _state_path(workspace: Path) -> Path:
    return workspace / "05_CONTROL" / CAMPAIGN_STATE_FILENAME


def load_campaign_config(workspace: Path) -> CampaignConfig | None:
    """Load the persisted campaign config (None when absent)."""
    path = _config_path(Path(workspace))
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"campaign config unreadable: {exc}") from exc
    return CampaignConfig.from_dict(data)


def load_campaign_state(workspace: Path) -> CampaignState | None:
    """Load the persisted campaign state (None when absent; read-only)."""
    path = _state_path(Path(workspace))
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"campaign state unreadable: {exc}") from exc
    return CampaignState.from_dict(data)


def save_campaign_config(workspace: Path, config: CampaignConfig) -> None:
    _atomic_write_json(_config_path(Path(workspace)), config.to_dict())


def _save_state(workspace: Path, state: CampaignState) -> None:
    _atomic_write_json(_state_path(workspace), state.to_dict())


@dataclass(slots=True)
class CampaignReport:
    """JSON-friendly report of ONE campaign run (to a stop boundary)."""

    status: CampaignStatus
    stop_condition: CampaignStopCondition
    iterations_completed: int
    model_calls_used: int
    accumulated_run_s: float
    last_readiness: Optional[float] = None
    current_proposal_hash: str = ""
    last_iteration_outcome: str = ""
    gate_outcome: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "stop_condition": self.stop_condition.value,
            "iterations_completed": self.iterations_completed,
            "model_calls_used": self.model_calls_used,
            "accumulated_run_s": round(self.accumulated_run_s, 3),
            "last_readiness": self.last_readiness,
            "current_proposal_hash": self.current_proposal_hash,
            "last_iteration_outcome": self.last_iteration_outcome,
            "gate_outcome": self.gate_outcome,
            "error": self.error,
        }


def run_campaign(
    *,
    workspace: Path,
    state_machine: ProposalStateMachine,
    config: CampaignConfig,
    specialist_drivers: Optional[Mapping[Any, BaseDriver]] = None,
    reviewer_drivers: Optional[Mapping[Any, BaseDriver]] = None,
    orchestrator_driver: Optional[BaseDriver] = None,
    specialist_agent_configs: Optional[Mapping[Any, ProposalAgentConfig]] = None,
    reviewer_agent_configs: Optional[Mapping[Any, ProposalAgentConfig]] = None,
    orchestrator_agent_config: Optional[ProposalAgentConfig] = None,
    timeout_s: Optional[float] = None,
    source_budget: SourceBudget | None = None,
    state: CampaignState | None = None,
    control: Optional[Any] = None,
) -> CampaignReport:
    """Run the bounded campaign loop — honouring every stop condition.

    ``control`` is an optional object with ``pause_requested`` /
    ``stop_requested`` booleans (the UI's live control surface); flags are
    sampled at stage boundaries ONLY — a running model call always
    finishes first (boundary-only semantics, D-018 heritage).
    """
    started = time.monotonic()
    workspace = Path(workspace)
    master_path = workspace / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
    campaign_state = state or CampaignState()
    campaign_state.status = CampaignStatus.RUNNING
    campaign_state.stop_condition = CampaignStopCondition.NONE
    campaign_state.error = ""
    if not campaign_state.campaign_id:
        campaign_state.campaign_id = f"campaign-{int(time.time())}"
    if not campaign_state.start_epoch_s:
        campaign_state.start_epoch_s = time.time()

    def _elapsed_s() -> float:
        return campaign_state.accumulated_run_s + (time.monotonic() - started)

    def _checkpoint(stage: str) -> None:
        campaign_state.stage = stage
        campaign_state.accumulated_run_s = _elapsed_s()
        _save_state(workspace, campaign_state)

    def _controls() -> tuple[bool, bool]:
        if control is None:
            return False, False
        return (
            bool(getattr(control, "pause_requested", False)),
            bool(getattr(control, "stop_requested", False)),
        )

    def _finish(
        status: CampaignStatus,
        stop: CampaignStopCondition,
        *,
        gate_outcome: str = "",
        last_iteration_outcome: str = "",
    ) -> CampaignReport:
        campaign_state.status = status
        campaign_state.stop_condition = stop
        campaign_state.accumulated_run_s = _elapsed_s()
        _save_state(workspace, campaign_state)
        return CampaignReport(
            status=status,
            stop_condition=stop,
            iterations_completed=campaign_state.iteration,
            model_calls_used=campaign_state.model_calls_used,
            accumulated_run_s=campaign_state.accumulated_run_s,
            last_readiness=campaign_state.last_readiness,
            current_proposal_hash=campaign_state.current_proposal_hash,
            last_iteration_outcome=last_iteration_outcome,
            gate_outcome=gate_outcome,
            error=campaign_state.error,
        )

    def _bounds_breached() -> CampaignStopCondition | None:
        if _elapsed_s() >= config.max_hours * 3600:
            return CampaignStopCondition.MAX_HOURS
        if campaign_state.iteration >= config.max_iterations:
            return CampaignStopCondition.MAX_ITERATIONS
        if campaign_state.model_calls_used >= config.max_model_calls:
            return CampaignStopCondition.MAX_MODEL_CALLS
        return None

    # -- resume consistency: never auto-run on a terminal campaign ---------
    if campaign_state.status in (
        CampaignStatus.COMPLETE,
        CampaignStatus.FAILED,
    ):
        return _finish(campaign_state.status, campaign_state.stop_condition)

    # -- STEP 0: initial generation when the master is empty ----------------
    if not master_path.exists() or not master_path.read_bytes().strip():
        _checkpoint("initial_generation")
        if specialist_drivers is None or orchestrator_driver is None:
            campaign_state.error = (
                "MASTER_PROPOSAL is empty: initial generation requires "
                "specialist drivers and the ORCHESTRATOR driver."
            )
            return _finish(CampaignStatus.FAILED, CampaignStopCondition.FAILED)
        try:
            gen = run_initial_generation(
                workspace=workspace,
                state_machine=state_machine,
                proposal_revision=f"campaign-{campaign_state.iteration + 1}",
                specialist_drivers=specialist_drivers,
                orchestrator_driver=orchestrator_driver,
                specialist_agent_configs=specialist_agent_configs,
                orchestrator_agent_config=orchestrator_agent_config,
                timeout_s=timeout_s,
                source_budget=source_budget,
            )
        except Exception as exc:  # noqa: BLE001 - fail the campaign closed
            campaign_state.error = f"initial generation raised: {exc}"
            return _finish(CampaignStatus.FAILED, CampaignStopCondition.FAILED)
        campaign_state.model_calls_used += 4
        if gen.outcome is not InitialGenerationOutcome.COMPLETED:
            campaign_state.error = f"initial generation failed: {gen.error}"
            return _finish(CampaignStatus.FAILED, CampaignStopCondition.FAILED)
        campaign_state.current_proposal_hash = gen.proposal_hash
        campaign_state.last_good_artifact = str(
            Path(gen.generation_dir) / "initial_generation.json"
        )
        _checkpoint("initial_generation_done")
        pause, stop = _controls()
        if stop:
            return _finish(CampaignStatus.STOPPED, CampaignStopCondition.OPERATOR_STOP)
        if pause:
            return _finish(CampaignStatus.PAUSED, CampaignStopCondition.OPERATOR_PAUSE)

    # -- the bounded iteration loop ------------------------------------------
    no_improvement_count = 0
    while True:
        breach = _bounds_breached()
        if breach is not None:
            return _finish(CampaignStatus.BOUND_REACHED, breach)

        next_iteration = campaign_state.iteration + 1
        _checkpoint(f"iteration_{next_iteration}_panel")
        chair = run_panel_chair_iteration(
            workspace=workspace,
            state_machine=state_machine,
            iteration_number=next_iteration,
            proposal_revision=f"campaign-{next_iteration}",
            reviewer_drivers=reviewer_drivers or {},
            reviewer_agent_configs=reviewer_agent_configs,
            orchestrator_driver=orchestrator_driver,
            orchestrator_agent_config=orchestrator_agent_config,
            timeout_s=timeout_s,
            source_budget=source_budget,
        )
        campaign_state.model_calls_used += chair.model_calls_used
        campaign_state.iteration = next_iteration
        campaign_state.current_proposal_hash = chair.current_proposal_hash

        if chair.readiness is not None:
            readiness_value = chair.readiness.get("readiness")
            campaign_state.last_readiness = (
                float(readiness_value)
                if readiness_value is not None
                else campaign_state.last_readiness
            )
            campaign_state.readiness_history.append(
                {
                    "iteration": next_iteration,
                    "readiness": campaign_state.last_readiness,
                    "proposal_hash": chair.current_proposal_hash,
                }
            )

        # -- terminal / blocked outcomes ------------------------------------
        if chair.outcome is PanelChairOutcome.REVIEW_BLOCKED:
            _checkpoint("blocked_review")
            campaign_state.error = chair.error
            return _finish(
                CampaignStatus.WAITING_FOR_OPERATOR,
                CampaignStopCondition.OPERATOR_STOP,
                last_iteration_outcome=chair.outcome.value,
            )
        if chair.outcome in (
            PanelChairOutcome.SOURCE_VALIDATION_FAILED,
            PanelChairOutcome.SOURCE_BUDGET_EXCEEDED,
            PanelChairOutcome.STALE_PROPOSAL,
            PanelChairOutcome.ARTIFACT_CONFLICT,
            PanelChairOutcome.PANEL_FAILED,
            PanelChairOutcome.CONSENSUS_FAILED,
            PanelChairOutcome.INTEGRATION_FAILED,
        ):
            _checkpoint("failed_iteration")
            campaign_state.error = chair.error
            return _finish(
                CampaignStatus.FAILED,
                CampaignStopCondition.FAILED,
                last_iteration_outcome=chair.outcome.value,
            )

        last_iteration_outcome = (
            chair.outcome.value
            if hasattr(chair.outcome, "value")
            else str(chair.outcome)
        )

        # -- improvement accounting (readiness delta) ------------------------
        if len(campaign_state.readiness_history) >= 2:
            previous = campaign_state.readiness_history[-2]["readiness"]
            current = campaign_state.readiness_history[-1]["readiness"]
            if (
                previous is not None
                and current is not None
                and current - previous < 0.5
            ):
                no_improvement_count += 1
            else:
                no_improvement_count = 0
        else:
            no_improvement_count = 0

        # -- hard gates when review-current at HARD_GATE_VALIDATION ----------
        gate_outcome = ""
        if state_machine.phase is ProposalPhase.HARD_GATE_VALIDATION:
            _checkpoint(f"iteration_{next_iteration}_hard_gates")
            gate = run_hard_gates(
                workspace=workspace,
                state_machine=state_machine,
                iteration_number=next_iteration,
            )
            gate_outcome = gate.outcome.value
            if gate.outcome is ProposalHardGateRunOutcome.COMPLETE:
                return _finish(
                    CampaignStatus.COMPLETE,
                    CampaignStopCondition.COMPLETE,
                    gate_outcome=gate_outcome,
                    last_iteration_outcome=last_iteration_outcome,
                )
            if gate.outcome is ProposalHardGateRunOutcome.BLOCKED:
                # Operator evidence is missing/invalid: do NOT burn calls.
                return _finish(
                    CampaignStatus.WAITING_FOR_OPERATOR,
                    CampaignStopCondition.WAITING_FOR_OPERATOR,
                    gate_outcome=gate_outcome,
                    last_iteration_outcome=last_iteration_outcome,
                )
            # REVISION_REQUIRED / INCOMPLETE / STALE_REVIEW: keep looping
            # within bounds (the proposal-content failures iterate).

        _checkpoint(f"iteration_{next_iteration}_done")

        # -- readiness target reached but gates not COMPLETE: stop and let
        # the operator run gates/act (never fake completeness) --------------
        if (
            campaign_state.last_readiness is not None
            and campaign_state.last_readiness >= config.target_readiness
            and state_machine.phase is ProposalPhase.HARD_GATE_VALIDATION
        ):
            return _finish(
                CampaignStatus.WAITING_FOR_OPERATOR,
                CampaignStopCondition.OPERATOR_STOP,
                gate_outcome=gate_outcome,
                last_iteration_outcome=last_iteration_outcome,
            )

        # -- convergence / controls / bounds ---------------------------------
        if no_improvement_count >= config.no_improvement_limit:
            return _finish(
                CampaignStatus.CONVERGED,
                CampaignStopCondition.CONVERGED,
                gate_outcome=gate_outcome,
                last_iteration_outcome=last_iteration_outcome,
            )
        pause, stop = _controls()
        if stop:
            return _finish(
                CampaignStatus.STOPPED,
                CampaignStopCondition.OPERATOR_STOP,
                gate_outcome=gate_outcome,
                last_iteration_outcome=last_iteration_outcome,
            )
        if pause:
            return _finish(
                CampaignStatus.PAUSED,
                CampaignStopCondition.OPERATOR_PAUSE,
                gate_outcome=gate_outcome,
                last_iteration_outcome=last_iteration_outcome,
            )
