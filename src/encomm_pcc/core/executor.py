"""Deterministic executor: the component that actually dispatches work.

**Session 002 scope.**  The executor drives **one** task through::

    IDLE → PLANNING_BATCH → RUNNING_TASK → AUDITING_TASK   (stop)

``AUDITING_TASK`` here means "the Builder finished and the task is ready for the
future Task Auditor" — there is no auditor, no fix loop and no multi-task batch
in this session.  Those are later phases (see ``docs/ROADMAP.md``).

Two invariants shape every line below:

* **The model never drives state.**  Transitions are chosen by this
  deterministic code from the state machine's legal edges.  Nothing an engine
  prints can move the pipeline.
* **Failure is never silent.**  A child process that exits non-zero, times out,
  or produces no verifiable result marks the task ``FAILED`` and the pipeline
  ``FAILED``; the run then stops.  There is no "continue anyway" path.

Everything the executor does is persisted **before and after** each significant
boundary, so a crash or a restart can be reconciled from SQLite instead of from
memory.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from ..domain import (
    AgentRole,
    AgentRoleConfig,
    BatchStatus,
    PipelinePhase,
    TaskState,
    TaskStateRecord,
    utc_now,
)
from ..drivers import (
    BaseDriver,
    DriverError,
    DriverNotImplementedError,
    DriverRegistry,
    DriverSession,
    ProcessRunner,
    PromptResult,
    SessionRequest,
    default_registry,
)
from ..persistence import Database
from .events import EventLog, NullEventLog
from .hermes_profiles import ProfileDiscoveryResult, discover_profiles
from .session_manager import SessionAction

__all__ = [
    "ExecutionOutcome",
    "ExecutionReport",
    "Executor",
    "TaskSpec",
]

#: The role the executor dispatches in Session 002.  The auditor roles arrive
#: with their own phases in later sessions.
DEFAULT_TASK_ROLE = AgentRole.BUILDER

#: Cap on how much real engine output is stored in the event log payload.  The
#: complete text stays on the returned :class:`PromptResult`; the log keeps a
#: bounded excerpt so the database cannot be filled by one verbose answer.
OUTPUT_EXCERPT_CHARS = 4000


class ExecutionOutcome(str, Enum):
    """How one dispatch ended."""

    #: The Builder completed and the task reached ``AUDITING_TASK``.
    COMPLETED = "COMPLETED"
    #: The child process failed; the task and the pipeline are FAILED.
    FAILED = "FAILED"
    #: Preflight refused to start (missing workspace/profile/engine/CLI).
    BLOCKED = "BLOCKED"
    #: The request was illegal for the current phase (no state was changed).
    REJECTED = "REJECTED"
    #: A stop request was honoured at the pre-dispatch boundary.
    STOPPED = "STOPPED"


@dataclass(slots=True)
class TaskSpec:
    """One manually supplied, controlled task (Session 002: exactly one)."""

    title: str
    prompt: str
    task_id: str = ""

    def materialise(self, *, index: int = 0) -> TaskStateRecord:
        """Build the persisted task record for this spec."""
        record = TaskStateRecord(
            index=index,
            title=self.title.strip() or "Untitled task",
            prompt=self.prompt,
        )
        if self.task_id:
            record.task_id = self.task_id
        return record


@dataclass(slots=True)
class ExecutionReport:
    """Result of one dispatch attempt — the machine-checkable record of it."""

    outcome: ExecutionOutcome
    phase: PipelinePhase
    message: str
    task_id: str = ""
    task_state: TaskState | None = None
    #: True **only** when a real child process was launched for this task.
    executor_started: bool = False
    session_id: str | None = None
    prompt_result: PromptResult | None = None
    stop_requested: bool = False
    started_at: str = field(default_factory=utc_now)
    finished_at: str | None = None

    @property
    def ok(self) -> bool:
        return self.outcome is ExecutionOutcome.COMPLETED

    def summary(self) -> str:
        """One-line, non-secret rendering for the UI and the event log."""
        parts = [f"{self.outcome.value}: {self.message}"]
        if self.task_id:
            parts.append(f"task={self.task_id}")
        if self.session_id:
            parts.append(f"session={self.session_id}")
        if self.prompt_result is not None and self.prompt_result.exit_code is not None:
            parts.append(f"exit_code={self.prompt_result.exit_code}")
        return " | ".join(parts)


class _LaunchRecorder:
    """Process-runner decorator that records every real process launch.

    ``ExecutionReport.executor_started`` is derived from this, never from
    intent: the flag is true only when a :class:`ProcessSpec` actually reached a
    runner.
    """

    def __init__(self, inner: ProcessRunner) -> None:
        self._inner = inner
        self.launched: list[list[str]] = []

    def run(self, spec):  # noqa: ANN001, ANN201 - ProcessSpec in, ProcessResult out
        self.launched.append(spec.argv_list())
        return self._inner.run(spec)


class Executor:
    """Dispatches tasks against the configured drivers.

    The executor holds no state of its own: phase and task state live in
    :class:`~encomm_pcc.core.controller.PipelineController`, session bookkeeping
    in :class:`~encomm_pcc.core.session_manager.SessionManager`, and everything
    durable in SQLite.  A restart therefore loses nothing that matters.
    """

    def __init__(
        self,
        controller,  # noqa: ANN001 - PipelineController (kept loose to avoid a cycle)
        *,
        registry: DriverRegistry | None = None,
        runner: ProcessRunner | None = None,
        database: Database | None = None,
        event_log: EventLog | None = None,
        role: AgentRole = DEFAULT_TASK_ROLE,
        profile_discovery: Callable[..., ProfileDiscoveryResult] | None = None,
    ) -> None:
        self.controller = controller
        self.registry = registry if registry is not None else default_registry()
        self.database = database if database is not None else getattr(controller, "database", None)
        self.events = (
            event_log
            if event_log is not None
            else getattr(controller, "events", None) or NullEventLog()
        )
        self.role = role
        self._discover_profiles = profile_discovery or discover_profiles
        self._runner_lock = threading.Lock()
        self._active = False
        self._stop_requested = False
        self._pause_requested = False
        self._recorder = _LaunchRecorder(runner) if runner is not None else None
        self._runner = runner
        # Drivers get the *recording* runner, so "a process really started" is
        # observed from the actual launch rather than inferred from intent.
        self._driver_runner = self._recorder if self._recorder is not None else None

    # -- control flags -----------------------------------------------------
    def request_stop(self) -> bool:
        """Ask the executor to stop at the next safe boundary.

        Mid-prompt termination is **not** implemented (the driver advertises
        ``supports_cancellation=False``): a running prompt finishes, its real
        result is recorded, and no further task is started.
        """
        with self._runner_lock:
            self._stop_requested = True
        self.events.warning(
            "Stop requested: the executor stops at the next safe task boundary. "
            "A prompt already running is allowed to finish — mid-prompt "
            "cancellation is not implemented.",
            source="executor",
        )
        return True

    def request_pause(self) -> bool:
        """Ask the executor to pause at the next safe task boundary."""
        with self._runner_lock:
            self._pause_requested = True
        self.events.info(
            "Pause requested: the executor pauses at the next safe task boundary.",
            source="executor",
        )
        return True

    def clear_control_flags(self) -> None:
        """Forget pause/stop requests (used when a batch is resumed)."""
        with self._runner_lock:
            self._stop_requested = False
            self._pause_requested = False

    @property
    def stop_requested(self) -> bool:
        with self._runner_lock:
            return self._stop_requested

    @property
    def pause_requested(self) -> bool:
        with self._runner_lock:
            return self._pause_requested

    @property
    def is_running(self) -> bool:
        with self._runner_lock:
            return self._active

    # -- task materialisation ----------------------------------------------
    def materialise_task(self, spec: TaskSpec, *, index: int | None = None) -> TaskStateRecord:
        """Create and persist the task record inside the current batch.

        Session 002 has no planner: one task is supplied by the operator and
        recorded through the existing ``tasks`` table (no migration needed for
        this part).
        """
        batch = self.controller.state.batch
        if batch is None:
            raise ValueError("No batch exists; request a start before materialising a task.")
        position = batch.current_index if index is None else index
        if position is None:
            position = len(batch.tasks)
        record = spec.materialise(index=position)
        batch.tasks.append(record)
        batch.updated_at = utc_now()
        self._persist()
        self.events.info(
            f"Task {record.task_id} materialised (index {record.index}): {record.title}",
            source="executor",
        )
        return record

    # -- dispatch -----------------------------------------------------------
    def dispatch_single_task(
        self, spec: TaskSpec, *, timeout_s: float | None = None
    ) -> ExecutionReport:
        """Run one controlled task end to end and return the honest report.

        Non-blocking for the caller only in the sense that it is the caller's
        job to run this off the UI thread; the call itself blocks on the child
        process (that is the point of a supervised run).
        """
        with self._runner_lock:
            if self._active:
                return ExecutionReport(
                    outcome=ExecutionOutcome.REJECTED,
                    phase=self.controller.machine.phase,
                    message="A dispatch is already running; Session 002 runs one task at a time.",
                )
            if self._stop_requested:
                self._stop_requested = False
                return ExecutionReport(
                    outcome=ExecutionOutcome.STOPPED,
                    phase=self.controller.machine.phase,
                    message="Stop was requested before dispatch; no task was started.",
                )
            if self._pause_requested:
                return ExecutionReport(
                    outcome=ExecutionOutcome.STOPPED,
                    phase=self.controller.machine.phase,
                    message="Pause was requested before dispatch; no task was started.",
                )
            self._active = True
        try:
            return self._dispatch(spec, timeout_s=timeout_s)
        except Exception as exc:  # noqa: BLE001 - an unexpected failure is a FAILED run
            self._fail_hard(exc)
            return ExecutionReport(
                outcome=ExecutionOutcome.FAILED,
                phase=self.controller.machine.phase,
                message=f"Executor error: {type(exc).__name__}: {exc}",
                started_at=utc_now(),
                finished_at=utc_now(),
            )
        finally:
            with self._runner_lock:
                self._active = False

    # -- internals ----------------------------------------------------------
    def _dispatch(self, spec: TaskSpec, *, timeout_s: float | None) -> ExecutionReport:
        machine = self.controller.machine

        # 1. Phase gate — read-only. Only IDLE (fresh) or PLANNING_BATCH (after
        #    Start) may dispatch; anything else is rejected without touching state.
        if machine.phase not in (PipelinePhase.IDLE, PipelinePhase.PLANNING_BATCH):
            return ExecutionReport(
                outcome=ExecutionOutcome.REJECTED,
                phase=machine.phase,
                message=(
                    f"Cannot dispatch from phase {machine.phase.value}; "
                    "stop the current batch first."
                ),
            )

        # 2. Preflight — no state changes and no processes before it passes, so a
        #    blocked dispatch leaves the pipeline exactly where it was.
        config = self.controller.state.config_for(self.role)
        engine = self.controller.state.resolved_engine_for(self.role)
        blocked = self._preflight(config, engine)
        if blocked is not None:
            self.events.error(blocked, source="executor")
            return ExecutionReport(
                outcome=ExecutionOutcome.BLOCKED,
                phase=machine.phase,
                message=blocked,
            )

        # 3. Create the batch (from IDLE) or reuse the one Start created.
        if machine.phase is PipelinePhase.IDLE:
            start = self.controller.request_start(1)
            if start.outcome.name == "REJECTED":  # pragma: no cover - guarded above
                return ExecutionReport(
                    outcome=ExecutionOutcome.REJECTED,
                    phase=machine.phase,
                    message=start.message,
                )

        batch = self.controller.state.batch
        if batch is None:  # pragma: no cover - request_start always creates one
            return ExecutionReport(
                outcome=ExecutionOutcome.BLOCKED,
                phase=machine.phase,
                message="No batch to dispatch into.",
            )
        if batch.tasks:
            return ExecutionReport(
                outcome=ExecutionOutcome.REJECTED,
                phase=machine.phase,
                message=(
                    "This batch already holds a task. Session 002 dispatches one task "
                    "per batch — stop the batch and start a new one."
                ),
            )

        capabilities = self.registry.capabilities(engine)
        driver: BaseDriver | None = None
        try:
            driver = self.registry.create(engine, runner=self._driver_runner)
            request = self._session_request(config)
            session = driver.start_session(request)
        except (DriverError, DriverNotImplementedError) as exc:
            message = f"Driver '{engine}' refused to start a session: {exc}"
            self.events.error(message, source="executor")
            return ExecutionReport(
                outcome=ExecutionOutcome.BLOCKED,
                phase=machine.phase,
                message=message,
            )

        # 4. Materialise + persist the task, then persist the RUNNING boundary.
        task = self.materialise_task(spec)
        task.state = TaskState.RUNNING
        task.attempts += 1
        task.updated_at = utc_now()
        batch.status = BatchStatus.RUNNING
        self._transition(PipelinePhase.RUNNING_TASK, f"Task {task.task_id} dispatched to {engine}.")
        self._persist()

        # 4. Session policy decides NEW / REUSE / NONE — through SessionManager.
        decision = self.controller.sessions.decide(self.role, config.session_policy, capabilities)
        if decision.action is SessionAction.REUSE and decision.session_id:
            try:
                session = driver.resume_session(decision.session_id, request)
            except (DriverError, DriverNotImplementedError) as exc:
                message = f"Session policy wanted to reuse {decision.session_id}, but: {exc}"
                return self._fail_task(task, message, session_id=decision.session_id)

        self.events.info(
            f"Session policy for {self.role.value}: {decision.action.value} — {decision.reason}",
            source="executor",
        )

        # 5. Dispatch the prompt and wait for the real process.
        handle = driver.send_prompt(session, spec.prompt)
        self.events.info(
            f"Task {task.task_id} prompt dispatched via driver '{engine}' "
            f"(profile '{config.project_profile or '(none)'}')"
            + (f", model '{config.model}'" if config.model else ""),
            source="executor",
        )
        result = driver.wait_for_completion(handle, timeout_s=timeout_s)

        started = bool(self._recorder and self._recorder.launched)
        session_id = result.session_id
        if session_id and not session_id.startswith("sess_"):
            session_id = str(session_id)

        # 6. Persist the real result and the terminal boundary.
        if not result.ok:
            report = self._fail_task(
                task,
                result.error or "The child process reported failure.",
                session_id=session_id,
                prompt_result=result,
                executor_started=started,
            )
        else:
            self._record_session(config, session, result, engine)
            task.state = TaskState.AUDITING
            task.last_error = None
            task.updated_at = utc_now()
            self._transition(
                PipelinePhase.AUDITING_TASK,
                f"Task {task.task_id} completed by the Builder and awaits the Task Auditor.",
            )
            self._persist()
            self.events.info(
                f"Task {task.task_id} result: exit code {result.exit_code}, "
                f"session {result.session_id or 'NOT_EXPOSED'}",
                source="executor",
                payload=self._result_payload(task, result, session_id),
            )
            report = ExecutionReport(
                outcome=ExecutionOutcome.COMPLETED,
                phase=self.controller.machine.phase,
                message=(
                    f"Task {task.task_id} completed and reached AUDITING_TASK "
                    "(the Task Auditor is a later session)."
                ),
                task_id=task.task_id,
                task_state=task.state,
                executor_started=started,
                session_id=session_id,
                prompt_result=result,
                stop_requested=self.stop_requested,
            )

        report.finished_at = utc_now()
        return report

    # -- preflight ----------------------------------------------------------
    def _preflight(self, config: AgentRoleConfig, engine: str) -> str | None:
        """Return a blocking reason, or ``None`` when dispatch may proceed."""
        workspace = self.controller.state.workspace
        if not workspace.repo_path.strip():
            return (
                "No workspace repository path is configured; the executor refuses to "
                "start an agent outside a supervised directory. Set WORKSPACE first."
            )
        if not workspace.exists():
            return f"Workspace path does not exist: {workspace.repo_path}"

        if not engine:
            return f"No engine is configured for {self.role.value}."
        if not self.registry.is_registered(engine):
            return f"Engine '{engine}' is not registered (registered: {self.registry.driver_ids()})."

        capabilities = self.registry.capabilities(engine)
        if not capabilities.implemented:
            return (
                f"Driver '{engine}' is a placeholder (implemented=False) and refuses real "
                "work. Configure an implemented engine for this role."
            )
        if not config.project_profile.strip():
            return (
                f"Driver '{engine}' needs a Hermes profile for {self.role.value}; "
                "the field is empty."
            )

        discovery = self._safe_discovery()
        if discovery is not None and discovery.ok and config.project_profile not in discovery.profiles:
            available = ", ".join(discovery.profiles) or "(none found)"
            return (
                f"Hermes profile '{config.project_profile}' was not found by "
                f"{discovery.method} discovery. Available profiles: {available}."
            )
        if discovery is not None and not discovery.ok:
            self.events.warning(
                "Hermes profile discovery failed "
                f"({discovery.error}); the profile name will be validated by the CLI itself.",
                source="executor",
            )
        return None

    def _safe_discovery(self) -> ProfileDiscoveryResult | None:
        """Run profile discovery, never letting it break a dispatch."""
        try:
            return self._discover_profiles(runner=self._runner)
        except Exception as exc:  # noqa: BLE001 - discovery is advisory
            self.events.warning(f"Hermes profile discovery error: {exc}", source="executor")
            return None

    def _session_request(self, config: AgentRoleConfig) -> SessionRequest:
        return SessionRequest(
            role=self.role,
            workspace_path=self.controller.state.workspace.repo_path,
            project_profile=config.project_profile,
            provider=config.provider,
            model=config.model,
            session_policy=config.session_policy,
            extra=dict(config.extra or {}),
        )

    # -- state helpers -------------------------------------------------------
    def _transition(self, phase: PipelinePhase, message: str) -> None:
        """Move the pipeline to ``phase`` through the controller, or fail loudly."""
        self.controller.transition(phase, message=message, source="executor")

    def _persist(self) -> None:
        self.controller.persist()

    def _record_session(
        self,
        config: AgentRoleConfig,
        session: DriverSession,
        result: PromptResult,
        engine: str,
    ) -> None:
        """Mirror a real external session id into SQLite (never a fake one)."""
        session_id = result.session_id
        if not session_id:
            return
        self.controller.sessions.register_session(
            self.role,
            session_id,
            engine,
            external=True,
            metadata={
                "profile": config.project_profile,
                "model": config.model,
                "provider": config.provider,
                "source": "hermes-cli",
                "batch_id": getattr(self.controller.state.batch, "batch_id", ""),
            },
        )
        # Keep the in-flight handle consistent with what actually ran.
        session.session_id = session_id
        session.external = True

    def _result_payload(
        self, task: TaskStateRecord, result: PromptResult, session_id: str | None
    ) -> dict[str, Any]:
        """Non-secret, bounded facts about a finished prompt."""
        text = result.text or ""
        excerpt = text if len(text) <= OUTPUT_EXCERPT_CHARS else text[:OUTPUT_EXCERPT_CHARS] + "…"
        stream = dict((result.metadata or {}).get("stream") or {})
        return {
            "task_id": task.task_id,
            "ok": result.ok,
            "exit_code": result.exit_code,
            "duration_s": round(float(result.duration_s), 3),
            "session_id": session_id,
            "simulated": bool(result.simulated),
            "text_excerpt": excerpt,
            "text_chars": len(text),
            "stream": stream,
        }

    def _fail_task(
        self,
        task: TaskStateRecord,
        message: str,
        *,
        session_id: str | None = None,
        prompt_result: PromptResult | None = None,
        executor_started: bool = False,
    ) -> ExecutionReport:
        """Mark the task and the pipeline FAILED and stop. Never silent."""
        task.state = TaskState.FAILED
        task.last_error = message
        task.updated_at = utc_now()
        batch = self.controller.state.batch
        if batch is not None:
            batch.status = BatchStatus.FAILED
        self._transition(PipelinePhase.FAILED, f"Task {task.task_id} failed: {message}")
        self._persist()
        payload = (
            self._result_payload(task, prompt_result, session_id)
            if prompt_result is not None
            else {"task_id": task.task_id, "ok": False}
        )
        payload["error"] = message
        self.events.error(
            f"Task {task.task_id} FAILED: {message}", source="executor", payload=payload
        )
        return ExecutionReport(
            outcome=ExecutionOutcome.FAILED,
            phase=self.controller.machine.phase,
            message=message,
            task_id=task.task_id,
            task_state=task.state,
            executor_started=executor_started,
            session_id=session_id,
            prompt_result=prompt_result,
            stop_requested=self.stop_requested,
            finished_at=utc_now(),
        )

    def _fail_hard(self, exc: BaseException) -> None:
        """Record an unexpected executor error without hiding it."""
        task_id = ""
        batch = self.controller.state.batch
        if batch is not None and batch.tasks:
            task = batch.tasks[-1]
            task.state = TaskState.FAILED
            task.last_error = f"{type(exc).__name__}: {exc}"
            task.updated_at = utc_now()
            task_id = task.task_id
            batch.status = BatchStatus.FAILED
        try:
            if self.controller.machine.can_go_to(PipelinePhase.FAILED):
                self._transition(PipelinePhase.FAILED, f"Executor error: {exc}")
            elif self.controller.machine.phase is not PipelinePhase.FAILED:
                # Illegal edge (e.g. already FAILED): fall back to the operator
                # escape hatch so the pipeline is not left mid-flight.
                self.controller.machine.reset()
                self.controller.state.phase = self.controller.machine.phase
        except Exception:  # noqa: BLE001 - never mask the original failure
            pass
        self._persist()
        self.events.error(
            f"Executor error: {type(exc).__name__}: {exc}",
            source="executor",
            payload={"task_id": task_id, "ok": False, "error": str(exc)},
        )

    # -- introspection -------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        """Non-secret executor facts for the UI."""
        return {
            "role": self.role.value,
            "runner": type(self._runner).__name__ if self._runner is not None else "none",
            "processes_launched": len(self._recorder.launched) if self._recorder else 0,
            "stop_requested": self.stop_requested,
            "pause_requested": self.pause_requested,
            "running": self.is_running,
        }