"""Pipeline controller: the seam the future executor will plug into.

**v0.1 scope.**  This controller owns pipeline *state* — phase transitions,
batch records, session bookkeeping and event logging — and it deliberately does
**not** execute tasks.  Every control call reports ``executor_started=False``
and writes a warning to the event log, so the UI can never imply that agents
are running when they are not.

The future executor will implement ``dispatch_batch()`` against the same state,
the same drivers and the same session policies.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from ..domain import (
    AgentRole,
    AgentRoleConfig,
    BatchState,
    BatchStatus,
    PipelinePhase,
    PipelineState,
    SessionPolicy,
    StateMachine,
    WorkspaceConfig,
)
from ..domain.state_machine import InvalidTransitionError
from ..drivers import DriverRegistry, default_registry
from ..persistence import Database
from .config import DEFAULT_BATCH_SIZE, MAX_BATCH_SIZE, MIN_BATCH_SIZE, placeholder_role_config
from .events import EventLog, NullEventLog
from .session_manager import SessionManager

__all__ = ["ControlOutcome", "ControlResult", "PipelineController"]


class ControlOutcome(str, Enum):
    """How a control request was handled."""

    OK = "OK"
    REJECTED = "REJECTED"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


@dataclass(frozen=True, slots=True)
class ControlResult:
    """Outcome of a control request, including whether work actually started."""

    outcome: ControlOutcome
    phase: PipelinePhase
    message: str
    #: True only when a real executor began dispatching work.  Always False in v0.1.
    executor_started: bool = False

    @property
    def ok(self) -> bool:
        return self.outcome is not ControlOutcome.REJECTED


class PipelineController:
    """Owns pipeline state; does not run agents (v0.1 foundation)."""

    #: Message used everywhere the missing executor must be stated plainly.
    EXECUTOR_NOT_IMPLEMENTED = (
        "Executor is not implemented in the v0.1 foundation — no tasks were dispatched."
    )

    def __init__(
        self,
        *,
        database: Database | None = None,
        event_log: EventLog | None = None,
        registry: DriverRegistry | None = None,
        state: PipelineState | None = None,
    ) -> None:
        self.database = database
        # NOTE: explicit None checks. EventLog defines __len__, so an empty log
        # is falsy and `event_log or NullEventLog()` would silently discard it.
        self.events = event_log if event_log is not None else NullEventLog()
        self.registry = registry if registry is not None else default_registry()
        self.state = state if state is not None else PipelineState.bootstrap()
        self.machine = StateMachine(self.state.phase)
        self.sessions = SessionManager(
            database=database,
            workspace_id=self.state.workspace.workspace_id,
        )
        #: Set by :meth:`attach_executor`.  The controller never dispatches by
        #: itself; this only tells it whether a real dispatcher is wired, so
        #: Start can stop claiming that nothing exists.
        self._executor: Any = None
        self._ensure_role_configs()

    # -- construction helpers ---------------------------------------------
    def _ensure_role_configs(self) -> None:
        """Fill roles that are missing or still unconfigured.

        ``PipelineState.bootstrap`` creates one config per role with empty
        fields.  A role in that state is replaced by the documented placeholder
        configuration; a role the operator has actually configured is never
        touched.
        """
        for role in AgentRole:
            existing = self.state.role_configs.get(role)
            if existing is None or self._is_unconfigured(existing):
                self.state.role_configs[role] = placeholder_role_config(role)

    @staticmethod
    def _is_unconfigured(config: AgentRoleConfig) -> bool:
        """True when a config carries no operator-supplied identity at all."""
        return not (config.engine or config.project_profile or config.session_id)

    def apply_placeholders(self) -> None:
        """Reset every role to the documented placeholder configuration."""
        for role in AgentRole:
            self.state.role_configs[role] = placeholder_role_config(role)

    # -- executor wiring -----------------------------------------------------
    def attach_executor(self, executor: Any) -> None:
        """Register (or clear) the component that actually dispatches work.

        The controller stays the owner of *state*: the executor asks it for
        transitions through :meth:`transition`.  Attaching an executor does not
        start anything — it only stops ``request_start`` from claiming that no
        dispatcher exists.
        """
        self._executor = executor
        if executor is None:
            self.events.info(
                "Executor detached — Start records batch state only.", source="controller"
            )
            return
        role = getattr(getattr(executor, "role", None), "value", "?")
        self.events.info(
            f"Executor attached ({type(executor).__name__}, role {role}): "
            "Start creates the batch and the executor dispatches tasks.",
            source="controller",
        )

    @property
    def executor_attached(self) -> bool:
        """True when a dispatcher is wired (the UI shows this honestly)."""
        return self._executor is not None

    @property
    def executor(self) -> Any:
        """The attached dispatcher, or ``None`` when the foundation runs alone."""
        return self._executor

    def transition(
        self,
        phase: PipelinePhase,
        *,
        message: str = "",
        source: str = "controller",
    ) -> PipelinePhase:
        """Move the pipeline to ``phase`` over a legal edge, then persist.

        The transition graph stays the single authority — a caller (the
        executor) decides *when*, never *whether*.  Illegal edges raise
        :class:`InvalidTransitionError` and leave the state untouched.
        """
        target = phase if isinstance(phase, PipelinePhase) else PipelinePhase(str(phase))
        if not self.machine.can_go_to(target):
            raise InvalidTransitionError(self.machine.phase, target)
        self.machine.transition_to(target)
        self.state.phase = self.machine.phase
        if message:
            self.events.info(message, source=source)
        self._persist()
        return self.state.phase

    def persist(self) -> None:
        """Persist the current aggregate (the executor's explicit seam)."""
        self._persist()

    # -- workspace ---------------------------------------------------------
    def set_workspace(self, name: str, repo_path: str) -> WorkspaceConfig:
        """Set the supervised workspace and rebind session bookkeeping."""
        workspace = self.state.workspace
        workspace.name = name.strip() or "New Workspace"
        workspace.repo_path = repo_path.strip()
        self.sessions.set_workspace(workspace.workspace_id)
        self.events.info(
            f"Workspace set to '{workspace.name}' ({workspace.repo_path or 'no path'})",
            source="controller",
        )
        self._persist()
        return workspace

    # -- role configuration -------------------------------------------------
    def set_role_config(self, role: AgentRole, **fields: Any) -> AgentRoleConfig:
        """Update selected fields of a role config and persist it."""
        config = self.state.config_for(role)
        allowed = {
            "engine",
            "project_profile",
            "provider",
            "model",
            "session_policy",
            "session_id",
            "same_as_orchestrator",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unknown role config field(s): {sorted(unknown)}")
        for key, value in fields.items():
            if key == "session_policy" and not isinstance(value, SessionPolicy):
                value = SessionPolicy(str(value))
            setattr(config, key, value)
        self._persist()
        return config

    def role_config(self, role: AgentRole) -> AgentRoleConfig:
        return self.state.config_for(role)

    # -- driver introspection ------------------------------------------------
    def driver_capabilities(self) -> list[Mapping[str, Any]]:
        """Capability rows for every registered driver (UI + diagnostics)."""
        return self.registry.describe_all()

    def probe_session_decision(self, role: AgentRole) -> Any:
        """Ask the session policy what it *would* do for ``role``.

        Read-only: this never creates a session.
        """
        config = self.state.config_for(role)
        engine = self.state.resolved_engine_for(role)
        if not engine or not self.registry.is_registered(engine):
            return None
        capabilities = self.registry.capabilities(engine)
        return self.sessions.decide(role, config.session_policy, capabilities)

    # -- control surface ------------------------------------------------------
    def request_start(self, batch_size: int = DEFAULT_BATCH_SIZE) -> ControlResult:
        """Request a new batch.  Records state only; does not execute."""
        size = self._clamp_size(batch_size)
        if not self.machine.can_go_to(PipelinePhase.PLANNING_BATCH):
            return ControlResult(
                ControlOutcome.REJECTED,
                self.machine.phase,
                f"Cannot start from phase {self.machine.phase.value}.",
            )

        try:
            self.machine.transition_to(PipelinePhase.PLANNING_BATCH)
        except InvalidTransitionError as exc:  # pragma: no cover - guarded above
            return ControlResult(ControlOutcome.REJECTED, self.machine.phase, str(exc))

        self.state.phase = self.machine.phase
        self.sessions.begin_new_batch()
        self.state.batch = BatchState(
            workspace_id=self.state.workspace.workspace_id,
            size=size,
            status=BatchStatus.CREATED,
            tasks=[],
        )
        self.events.info(
            f"Batch {self.state.batch.batch_id} requested with size {size} "
            f"(workspace '{self.state.workspace.name}')",
            source="controller",
        )
        if self._executor is None:
            # No dispatcher exists: say so, every time (Session 001 invariant).
            self.events.warning(self.EXECUTOR_NOT_IMPLEMENTED, source="controller")
            message = f"Batch {self.state.batch.batch_id} created (state only)."
        else:
            # A real dispatcher is wired.  Requesting a batch still starts no
            # process, so `executor_started` stays False here — the field that
            # becomes True is ExecutionReport.executor_started, set from the
            # actual process launch.
            self.events.info(
                f"Batch {self.state.batch.batch_id} created; dispatch is owned by "
                "the attached executor.",
                source="controller",
            )
            message = (
                f"Batch {self.state.batch.batch_id} created; the executor owns dispatch."
            )
        self._persist()
        return ControlResult(
            ControlOutcome.OK,
            self.machine.phase,
            message,
            executor_started=False,
        )

    def request_pause(self) -> ControlResult:
        """Pause the pipeline if the current phase allows it."""
        if not self.machine.can_go_to(PipelinePhase.PAUSED):
            return ControlResult(
                ControlOutcome.REJECTED,
                self.machine.phase,
                f"Phase {self.machine.phase.value} cannot be paused.",
            )
        self.machine.transition_to(PipelinePhase.PAUSED)
        self.state.phase = self.machine.phase
        if self.state.batch is not None:
            self.state.batch.status = BatchStatus.PAUSED
        self.events.info(
            f"Pipeline paused (resume target: {self.machine.resume_target().value})",
            source="controller",
        )
        self._persist()
        return ControlResult(
            ControlOutcome.OK, self.machine.phase, "Pipeline paused.", executor_started=False
        )

    def request_resume(self) -> ControlResult:
        """Resume a paused pipeline into the phase recorded before the pause."""
        if self.machine.phase is not PipelinePhase.PAUSED:
            return ControlResult(
                ControlOutcome.REJECTED,
                self.machine.phase,
                "Pipeline is not paused.",
            )
        target = self.machine.resume_target()
        self.machine.transition_to(target)
        self.state.phase = self.machine.phase
        if self.state.batch is not None:
            self.state.batch.status = BatchStatus.RUNNING
        self.events.info(f"Pipeline resumed into {target.value}", source="controller")
        self._persist()
        return ControlResult(
            ControlOutcome.OK,
            self.machine.phase,
            f"Resumed into {target.value}.",
            executor_started=False,
        )

    def request_stop(self) -> ControlResult:
        """Stop the current batch and return to ``IDLE``."""
        forced = False
        if self.machine.can_go_to(PipelinePhase.IDLE):
            self.machine.transition_to(PipelinePhase.IDLE)
        else:
            self.machine.reset()
            forced = True
        self.state.phase = self.machine.phase
        if self.state.batch is not None:
            self.state.batch.status = BatchStatus.STOPPED
        message = "Pipeline stopped and returned to IDLE."
        if forced:
            message += " (Forced reset: IDLE is not a normal successor of the current phase.)"
        self.events.info(message, source="controller")
        self._persist()
        return ControlResult(
            ControlOutcome.OK, self.machine.phase, message, executor_started=False
        )

    def set_batch_size(self, size: int) -> int:
        """Store the requested batch size (clamped to the supported range)."""
        clamped = self._clamp_size(size)
        if self.state.batch is None:
            self.state.batch = BatchState(
                workspace_id=self.state.workspace.workspace_id,
                size=clamped,
                status=BatchStatus.CREATED,
                tasks=[],
            )
        else:
            self.state.batch.size = clamped
        return clamped

    # -- persistence -----------------------------------------------------------
    def _persist(self) -> None:
        if self.database is None:
            return
        try:
            self.database.save_pipeline_state(self.state)
        except Exception as exc:  # pragma: no cover - surfaced through the log
            self.events.error(f"Failed to persist pipeline state: {exc}", source="controller")

    # -- introspection ----------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        """Everything the UI needs to render the current state."""
        return {
            "phase": self.state.phase.value,
            "workspace": self.state.workspace.to_dict(),
            "role_configs": {
                role.value: config.to_dict()
                for role, config in self.state.role_config_items()
            },
            "batch": self.state.batch.to_dict() if self.state.batch else None,
            "sessions": self.sessions.snapshot(),
            "drivers": list(self.driver_capabilities()),
        }

    @staticmethod
    def _clamp_size(size: int) -> int:
        try:
            value = int(size)
        except (TypeError, ValueError):
            value = DEFAULT_BATCH_SIZE
        return max(MIN_BATCH_SIZE, min(MAX_BATCH_SIZE, value))
