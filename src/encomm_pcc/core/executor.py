"""Deterministic executor: the component that actually dispatches work.

**Session 002 scope.**  The executor drives **one** task through::

    IDLE → PLANNING_BATCH → RUNNING_TASK → AUDITING_TASK   (stop)

**Session 003 scope.**  The ``AUDITING_TASK`` stop is lifted for a *single
task*: the Task Auditor runs through the same generic role/driver path, its
structured verdict is parsed by a strict parser, and a capped fix loop
closes the audit::

    AUDITING_TASK ──PASS──▶ BATCH_COMPLETE
        │
        ├──NEEDS_FIX──▶ FIX_REQUIRED ──▶ RUNNING_FIX ──▶ AUDITING_TASK (re-audit)
        │                   ▲                                  │
        │                   └────── cap (MAX_AUDIT_ROUNDS) ────┴──▶ BLOCKED
        │
        └──BLOCKED / malformed──▶ BLOCKED

The loop is **hard-capped** (:data:`MAX_AUDIT_ROUNDS`, initially 3): the
initial audit is round 1; Fix 1 → Audit 2; Fix 2 → Audit 3; a NEEDS_FIX on
round 3 escalates to ``BLOCKED`` — a third fix is never started beyond the
cap.  No infinite loop is reachable by construction.

Two invariants shape every line below:

* **The model never drives state.**  Transitions are chosen by this
  deterministic code from the state machine's legal edges.  Nothing an engine
  prints can move the pipeline — a verdict is *data* the executor validates
  (via ``core/verdict_parser.py``) and only then applies.
* **Failure is never silent.**  A child process that exits non-zero, times out,
  or produces no verifiable result marks the task ``FAILED`` and the pipeline
  ``FAILED``.  A malformed auditor verdict, a missing fix prompt and a
  round-cap exhaustion mark the task ``BLOCKED``.  None of these can ever
  become a green task.

Everything the executor does is persisted **before and after** each significant
boundary, so a crash or a restart can be reconciled from SQLite instead of from
memory.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from ..domain import (
    AgentRole,
    AgentRoleConfig,
    AuditVerdict,
    AuditVerdictResult,
    BatchPlan,
    BatchPlanRecord,
    BatchStatus,
    FinalAuditPacket,
    PipelinePhase,
    TaskState,
    TaskStateRecord,
    build_batch_summary,
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
from .audit_packet import AuditPacket, render_audit_prompt, render_fix_prompt
from .config import MAX_BATCH_SIZE
from .events import EventLog, NullEventLog
from .final_audit_parser import (
    FinalAuditParseError,
    parse_final_audit,
)
from .hermes_profiles import ProfileDiscoveryResult, discover_profiles
from .plan_packet import render_planning_prompt
from .plan_parser import PLAN_ENVELOPE_END, PLAN_ENVELOPE_START, PlanParseError, parse_batch_plan
from .repo_fingerprint import (
    RepoFingerprint,
    capture_repo_fingerprint,
    fingerprints_equal,
)
from .session_manager import SessionAction
from .verdict_parser import VerdictParseError, parse_audit_verdict

__all__ = [
    "AUDITOR_ROLE",
    "ExecutionOutcome",
    "ExecutionReport",
    "Executor",
    "FinalAuditOutcome",
    "FinalAuditReport",
    "MAX_AUDIT_ROUNDS",
    "MAX_NEXT_BATCH_SIZE",
    "MIN_NEXT_BATCH_SIZE",
    "ORCHESTRATOR_ROLE",
    "PlanOutcome",
    "PlanReport",
    "StartNextBatchOutcome",
    "StartNextBatchReport",
    "TaskNextAction",
    "TaskSpec",
    "next_task_action",
]

#: The role the executor dispatches in Session 002.  The auditor roles arrive
#: with their own phases in later sessions.
DEFAULT_TASK_ROLE = AgentRole.BUILDER

#: The role that audits each task (Session 003).  Resolved through the same
#: role config → driver registry → SessionManager path as the Builder.
AUDITOR_ROLE = AgentRole.TASK_AUDITOR

#: The role that plans each batch (Session 004).  Resolved through the SAME
#: generic path — no orchestrator-specific engine code exists anywhere.
ORCHESTRATOR_ROLE = AgentRole.ORCHESTRATOR

#: The role that audits a completed batch (Session 005).  Resolved through the
#: SAME generic role → config → registry → SessionManager path; nothing in the
#: final-audit logic mentions a specific engine.
FINAL_AUDITOR_ROLE = AgentRole.FINAL_AUDITOR

#: Next-batch size bounds (brief §6): a PASSing Final Auditor must return
#: EXACTLY this many next tasks; the strict parser enforces the count.
MIN_NEXT_BATCH_SIZE = 4
MAX_NEXT_BATCH_SIZE = 5
DEFAULT_NEXT_BATCH_SIZE = 5

#: Hard cap on audit/fix rounds for ONE task (brief semantics): Audit 1 is the
#: initial audit; Fix 1 → Audit 2; Fix 2 → Audit 3.  A NEEDS_FIX returned by
#: round 3 escalates to BLOCKED — no fix is ever started beyond this cap.
#: For a fix to be allowed, ``task.audit_rounds`` must be < this value; for an
#: audit to be allowed, the same check applies before incrementing.
MAX_AUDIT_ROUNDS = 3

#: Cap on how much real engine output is stored in the event log payload.  The
#: complete text stays on the returned :class:`PromptResult`; the log keeps a
#: bounded excerpt so the database cannot be filled by one verbose answer.
OUTPUT_EXCERPT_CHARS = 4000

#: Cap on the serialised structured verdict stored on ``tasks.verdict_json``.
#: The parser already bounds the input; this is a second defence for storage.
VERDICT_STORE_CHARS = 80_000


def _fingerprint_violation(before: RepoFingerprint, after: RepoFingerprint) -> str:
    """Human-readable description of what changed between two fingerprints."""
    parts: list[str] = []
    if before.head != after.head:
        parts.append(f"HEAD changed: {before.head} -> {after.head}")
    if before.status_hash != after.status_hash:
        parts.append(
            f"worktree status changed ({before.status_lines} -> "
            f"{after.status_lines} porcelain line(s))"
        )
    return "; ".join(parts) or "unexpected repository change"


class ExecutionOutcome(str, Enum):
    """How one dispatch ended."""

    #: The unit of work completed and produced a verifiable result: a Builder
    #: run reaching AUDITING_TASK, a fix run returning the task to audit, or
    #: an audit that returned PASS and approved the task.
    COMPLETED = "COMPLETED"
    #: The child process failed; the task and the pipeline are FAILED.
    FAILED = "FAILED"
    #: An audit returned NEEDS_FIX; the task moved to FIX_REQUIRED.  Not a
    #: green outcome (`ok` is False): the loop must continue with a fix.
    NEEDS_FIX = "NEEDS_FIX"
    #: Preflight refused, the auditor verdict was BLOCKED, the verdict was
    #: malformed, or the round cap was exhausted.  Non-green.
    BLOCKED = "BLOCKED"
    #: The request was illegal for the current phase (no state was changed).
    REJECTED = "REJECTED"
    #: A stop request was honoured at the pre-dispatch boundary.
    STOPPED = "STOPPED"


class TaskNextAction(str, Enum):
    """The deterministic next step for the current task (drives UI + recovery)."""

    #: No task materialised / nothing to do.
    IDLE = "IDLE"
    #: The batch needs its Orchestrator plan (no plan materialised yet).
    PLAN = "PLAN"
    #: Run the Builder for the next planned task (a brand-new session).
    BUILD = "BUILD"
    #: Run the initial audit (audit_rounds == 0).
    AUDIT = "AUDIT"
    #: Run a fix (FIX_REQUIRED) — always in a brand-new Builder session.
    FIX = "FIX"
    #: Re-audit after a fix (resume the same auditor session).
    RE_AUDIT = "RE_AUDIT"
    #: Every task passed its audit; the batch is READY_FOR_FINAL_AUDIT.
    COMPLETE = "COMPLETE"
    #: Cap exhausted, malformed verdict, or auditor BLOCKED.  Requires a human.
    BLOCKED = "BLOCKED"
    #: A child process failed; the task/pipeline are FAILED.
    FAILED = "FAILED"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


def next_task_action(
    *,
    batch: Any,  # BatchState | None
    phase: PipelinePhase,
) -> TaskNextAction:
    """Decide the single deterministic next step from persisted state.

    The task rows are the primary signal — after a restart only SQLite
    survives, and the persisted task states (plus the recovered batch phase)
    must be enough to decide PLAN / BUILD / AUDIT / FIX / RE-AUDIT / COMPLETE /
    BLOCKED.  The pipeline phase is a secondary signal for the edges where no
    task exists yet.

    Multi-task aware since Session 004: the *first* task (in index order) that
    still needs work is the current task; approved/blocked/failed tasks are
    never revisited, so restart recovery and the batch runner agree without
    any in-memory state.

    Pure and offline: recovery and the UI both call this, and it never starts
    anything — it only reports what the next legal action is.
    """
    if batch is None:
        return TaskNextAction.IDLE
    if not batch.tasks:
        # A created batch with no plan needs the Orchestrator.
        if phase is PipelinePhase.PLANNING_BATCH or batch.status.value == "CREATED":
            return TaskNextAction.PLAN
        return TaskNextAction.IDLE

    # A blocked or failed task stops the batch deterministically.
    for task in batch.tasks:
        if task.state is TaskState.BLOCKED:
            return TaskNextAction.BLOCKED
        if task.state is TaskState.FAILED:
            return TaskNextAction.FAILED

    if all(task.state is TaskState.APPROVED for task in batch.tasks):
        return TaskNextAction.COMPLETE

    task = batch.first_undone_task()
    if task is None:
        return TaskNextAction.IDLE
    if task.state is TaskState.PENDING:
        return TaskNextAction.BUILD
    if task.state is TaskState.RUNNING:
        # A previous build did not finish (restart); the Builder re-runs in a
        # fresh session — the half-finished attempt is never trusted.
        return TaskNextAction.BUILD
    if task.state is TaskState.FIX_REQUIRED:
        return TaskNextAction.FIX
    if task.state is TaskState.RUNNING_FIX:
        return TaskNextAction.FIX
    if task.state is TaskState.AUDITING:
        if task.audit_rounds >= MAX_AUDIT_ROUNDS and not task.latest_verdict:
            return TaskNextAction.BLOCKED
        if task.audit_rounds == 0 or not task.latest_verdict:
            return TaskNextAction.AUDIT
        if task.latest_verdict == AuditVerdict.NEEDS_FIX.value:
            return TaskNextAction.RE_AUDIT
        if task.latest_verdict == AuditVerdict.BLOCKED.value:
            return TaskNextAction.BLOCKED
        return TaskNextAction.AUDIT
    if phase is PipelinePhase.BLOCKED:
        return TaskNextAction.BLOCKED
    if phase is PipelinePhase.FAILED:
        return TaskNextAction.FAILED
    if phase is PipelinePhase.READY_FOR_FINAL_AUDIT:
        return TaskNextAction.COMPLETE
    if phase is PipelinePhase.BATCH_COMPLETE:
        return TaskNextAction.COMPLETE
    return TaskNextAction.IDLE


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
    #: The strict verdict when this report is the result of an audit run.
    audit_verdict: AuditVerdictResult | None = None
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


class PlanOutcome(str, Enum):
    """How one Orchestrator planning call ended (Session 004)."""

    #: The plan parsed strictly, matched the requested task count, and the
    #: read-only guard passed; tasks are materialised and persisted.
    PLANNED = "PLANNED"
    #: The child process failed; the batch is FAILED.
    FAILED = "FAILED"
    #: A malformed plan, an exact-count violation, or a worktree-modification
    #: guard violation — the batch is BLOCKED and nothing was materialised.
    BLOCKED = "BLOCKED"
    #: The request was illegal for the current phase / state (no change).
    REJECTED = "REJECTED"
    #: A stop/pause request was honoured before the planning call started.
    STOPPED = "STOPPED"


@dataclass(slots=True)
class PlanReport:
    """Result of one planning attempt — the machine-checkable record of it."""

    outcome: PlanOutcome
    phase: PipelinePhase
    message: str
    #: The parsed plan (never None on PLANNED).
    plan: BatchPlan | None = None
    #: The durable planning record, when one was persisted.
    plan_record: BatchPlanRecord | None = None
    #: Real external Orchestrator session id when the engine exposed one.
    session_id: str | None = None
    #: True when the planning call modified the repository (guard violation).
    guard_violation: bool = False
    #: True only when a real process was launched for the planning call.
    executor_started: bool = False
    prompt_result: PromptResult | None = None
    #: Repository HEAD before the planning call (read-only capture).
    baseline_head: str | None = None
    stop_requested: bool = False
    started_at: str = field(default_factory=utc_now)
    finished_at: str | None = None

    @property
    def ok(self) -> bool:
        return self.outcome is PlanOutcome.PLANNED

    def summary(self) -> str:
        """One-line, non-secret rendering for the UI and the event log."""
        parts = [f"{self.outcome.value}: {self.message}"]
        if self.plan is not None:
            parts.append(f"tasks={self.plan.task_count}")
        if self.session_id:
            parts.append(f"session={self.session_id}")
        if self.prompt_result is not None and self.prompt_result.exit_code is not None:
            parts.append(f"exit_code={self.prompt_result.exit_code}")
        return " | ".join(parts)


class FinalAuditOutcome(str, Enum):
    """How one Final Auditor call ended (Session 005)."""

    #: PASS: the batch is BATCH_COMPLETE and the next plan is persisted,
    #: waiting for the operator.  Nothing auto-starts.
    PASSED = "PASSED"
    #: NEEDS_FIX: findings persisted; the batch needs operator/supervisor
    #: handling (no uncontrolled global fix loop is entered).
    NEEDS_FIX = "NEEDS_FIX"
    #: BLOCKED: malformed answer, guard violation, or an explicit BLOCKED
    #: verdict — operator/manual decision required.
    BLOCKED = "BLOCKED"
    #: The child process failed; the pipeline is FAILED.
    FAILED = "FAILED"
    #: The request was illegal for the current phase/state (no change).
    REJECTED = "REJECTED"
    #: A stop/pause request was honoured before the call started.
    STOPPED = "STOPPED"


@dataclass(slots=True)
class FinalAuditReport:
    """Machine-checkable record of one Final Auditor call (Session 005)."""

    outcome: FinalAuditOutcome
    phase: PipelinePhase
    message: str
    #: The strict parsed result (never None on PASSED; the raw JSON is
    #: persisted separately — see ``structured_json``).
    result: Any = None  # FinalAuditResult (kept loose to avoid an import cycle)
    #: Real external Final Auditor session id when the engine exposed one.
    session_id: str | None = None
    #: True only when a real process was launched for this call.
    executor_started: bool = False
    prompt_result: PromptResult | None = None
    #: True when the auditor modified the supervised repository (guard).
    guard_violation: bool = False
    #: The persisted next-plan id (PASSED only).
    next_plan_id: str | None = None
    #: Bounded raw model output kept on the in-memory report for diagnosis.
    output_excerpt: str = ""
    stop_requested: bool = False
    started_at: str = field(default_factory=utc_now)
    finished_at: str | None = None

    @property
    def ok(self) -> bool:
        return self.outcome is FinalAuditOutcome.PASSED

    def summary(self) -> str:
        parts = [f"{self.outcome.value}: {self.message}"]
        if self.session_id:
            parts.append(f"session={self.session_id}")
        if self.prompt_result is not None and self.prompt_result.exit_code is not None:
            parts.append(f"exit_code={self.prompt_result.exit_code}")
        return " | ".join(parts)


class StartNextBatchOutcome(str, Enum):
    """How one START NEXT BATCH request ended (Session 005)."""

    #: The persisted next plan was materialised into a new batch; the
    #: operator now presses the normal START controls to run it.
    READY = "READY"
    #: No open pending next plan exists (or the phase forbids it).
    REJECTED = "REJECTED"


@dataclass(slots=True)
class StartNextBatchReport:
    """Result of materialising the persisted next plan (no AI involved)."""

    outcome: StartNextBatchOutcome
    phase: PipelinePhase
    message: str
    #: The new batch id (READY only).
    batch_id: str = ""
    #: How many PENDING tasks were materialised from the persisted plan.
    task_count: int = 0

    @property
    def ok(self) -> bool:
        return self.outcome is StartNextBatchOutcome.READY


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

    # -- audit / fix loop (Session 003) ------------------------------------
    def prepare_task_for_audit(self, spec: TaskSpec, *, index: int | None = None) -> TaskStateRecord:
        """Materialise a task and place it directly into ``AUDITING_TASK``.

        Used when a task must reach the auditor **without** an initial Builder
        run — the Session 003 smoke seeds a deliberately defective scratch
        repository this way.  Walks ``IDLE/PLANNING_BATCH → RUNNING_TASK →
        AUDITING_TASK`` over legal edges only.
        """
        machine = self.controller.machine
        if machine.phase not in (PipelinePhase.IDLE, PipelinePhase.PLANNING_BATCH):
            raise ValueError(
                f"Cannot prepare a task for audit from phase {machine.phase.value}."
            )
        if machine.phase is PipelinePhase.IDLE:
            start = self.controller.request_start(1)
            if start.outcome.name == "REJECTED":  # pragma: no cover - guarded above
                raise ValueError(start.message)
        batch = self.controller.state.batch
        if batch is None:  # pragma: no cover - request_start always creates one
            raise ValueError("No batch exists; cannot prepare a task for audit.")
        if batch.tasks:
            raise ValueError(
                "This batch already holds a task; prepare one task per batch."
            )
        task = self.materialise_task(spec, index=index)
        task.state = TaskState.RUNNING
        self._transition(
            PipelinePhase.RUNNING_TASK, f"Task {task.task_id} prepared for audit."
        )
        task.state = TaskState.AUDITING
        batch.status = BatchStatus.RUNNING
        self._transition(
            PipelinePhase.AUDITING_TASK,
            f"Task {task.task_id} is ready for the Task Auditor.",
        )
        self._persist()
        self.events.info(
            f"Task {task.task_id} prepared for audit (no Builder run); awaiting the auditor.",
            source="executor",
        )
        return task

    # -- Orchestrator planning (Session 004) -------------------------------
    def plan_batch(
        self,
        *,
        project_brief: str,
        batch_size: int,
        timeout_s: float | None = None,
    ) -> PlanReport:
        """Run exactly one Orchestrator planning call and materialise the plan.

        Generic role path only: ``AgentRole.ORCHESTRATOR`` → role config →
        driver registry → ``SessionManager`` — no orchestrator-specific engine
        code exists anywhere, so a future Codex/Claude Code/etc. driver fills
        the role by configuration alone.

        The repository is fingerprinted read-only BEFORE the call and verified
        AFTER it: a planning call that modified the workspace BLOCKS the plan
        (the modifications are surfaced to the operator and never discarded,
        and no task is materialised from the violating answer).
        """
        guard = self._claim_unit("planning")
        if guard is not None:
            return PlanReport(
                outcome=PlanOutcome.STOPPED,
                phase=self.controller.machine.phase,
                message=guard.message,
            )
        try:
            return self._plan(
                project_brief=project_brief,
                batch_size=batch_size,
                timeout_s=timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 - an unexpected failure is a FAILED run
            self._fail_hard(exc)
            return PlanReport(
                outcome=PlanOutcome.FAILED,
                phase=self.controller.machine.phase,
                message=f"Executor error: {type(exc).__name__}: {exc}",
                started_at=utc_now(),
                finished_at=utc_now(),
            )
        finally:
            with self._runner_lock:
                self._active = False

    def _plan(
        self, *, project_brief: str, batch_size: int, timeout_s: float | None
    ) -> PlanReport:
        machine = self.controller.machine
        if machine.phase not in (PipelinePhase.IDLE, PipelinePhase.PLANNING_BATCH):
            return PlanReport(
                outcome=PlanOutcome.REJECTED,
                phase=machine.phase,
                message=f"Cannot plan from phase {machine.phase.value}.",
            )

        if batch_size < 1 or batch_size > MAX_BATCH_SIZE:
            return PlanReport(
                outcome=PlanOutcome.BLOCKED,
                phase=machine.phase,
                message=(
                    f"Batch size must be 1..{MAX_BATCH_SIZE} (the Orchestrator "
                    f"must return exactly that many tasks); got {batch_size}."
                ),
            )

        batch = self.controller.state.batch
        if batch is None:
            start = self.controller.request_start(batch_size)
            if start.outcome.name == "REJECTED":  # pragma: no cover - guarded above
                return PlanReport(
                    outcome=PlanOutcome.REJECTED, phase=machine.phase, message=start.message
                )
            batch = self.controller.state.batch
        if batch is None:  # pragma: no cover - request_start always creates one
            return PlanReport(
                outcome=PlanOutcome.BLOCKED, phase=machine.phase, message="No batch to plan."
            )
        if batch.plan is not None:
            return PlanReport(
                outcome=PlanOutcome.REJECTED,
                phase=machine.phase,
                message=f"Batch {batch.batch_id} is already planned.",
            )
        batch.project_brief = (project_brief or "").strip()
        batch.status = BatchStatus.PLANNING
        self._persist()
        self.events.info(
            f"Planning batch {batch.batch_id} (size {batch_size}) from "
            f"project brief ({len(batch.project_brief)} chars).",
            source="executor",
        )

        role, config, engine = self._resolve_role(ORCHESTRATOR_ROLE)
        blocked = self._preflight(config, engine, role=role)
        if blocked is not None:
            self.events.error(blocked, source="executor")
            return PlanReport(outcome=PlanOutcome.BLOCKED, phase=machine.phase, message=blocked)

        # Read-only repository baseline BEFORE the planning call.
        baseline = capture_repo_fingerprint(self.controller.state.workspace.repo_path)
        self.events.info(
            f"Planning baseline: head={baseline.head or '(no git)'}, "
            f"branch={baseline.branch or '(none)'}, dirty_lines={baseline.status_lines} "
            "(read-only capture).",
            source="executor",
        )

        capabilities = self.registry.capabilities(engine)
        driver: BaseDriver | None = None
        try:
            driver = self.registry.create(engine, runner=self._driver_runner)
            request = self._session_request(config, role=role)
            session = driver.start_session(request)
        except (DriverError, DriverNotImplementedError) as exc:
            message = f"Driver '{engine}' refused to start an orchestrator session: {exc}"
            self.events.error(message, source="executor")
            return PlanReport(outcome=PlanOutcome.BLOCKED, phase=machine.phase, message=message)

        decision = self.controller.sessions.decide(role, config.session_policy, capabilities)
        if decision.action is SessionAction.REUSE and decision.session_id:
            try:
                session = driver.resume_session(decision.session_id, request)
            except (DriverError, DriverNotImplementedError) as exc:
                message = (
                    f"Session policy wanted to resume orchestrator session "
                    f"{decision.session_id}, but: {exc}"
                )
                self.events.error(message, source="executor")
                return PlanReport(
                    outcome=PlanOutcome.FAILED, phase=machine.phase, message=message
                )
        self.events.info(
            f"Session policy for {role.value}: {decision.action.value} — {decision.reason}",
            source="executor",
        )

        prompt = render_planning_prompt(
            project_brief=batch.project_brief,
            batch_size=batch_size,
            workspace_path=self.controller.state.workspace.repo_path,
        )
        handle = driver.send_prompt(session, prompt)
        self.events.info(
            f"Planning prompt dispatched via driver '{engine}' "
            f"(profile '{config.project_profile or '(none)'}'"
            + (f", model '{config.model}'" if config.model else "")
            + f") — requesting exactly {batch_size} tasks.",
            source="executor",
        )
        result = driver.wait_for_completion(handle, timeout_s=timeout_s)

        started = bool(self._recorder and self._recorder.launched)
        session_id = result.session_id
        if not result.ok:
            batch.status = BatchStatus.FAILED
            if machine.can_go_to(PipelinePhase.FAILED):  # pragma: no cover - guarded
                self._transition(
                    PipelinePhase.FAILED,
                    f"Orchestrator planning failed: {result.error}",
                )
            self._persist()
            self.events.error(
                f"Orchestrator planning FAILED: {result.error}",
                source="executor",
                payload={"ok": False, "error": result.error},
            )
            return PlanReport(
                outcome=PlanOutcome.FAILED,
                phase=machine.phase,
                message=result.error or "The Orchestrator process reported failure.",
                session_id=session_id,
                executor_started=started,
                prompt_result=result,
                baseline_head=baseline.head,
                finished_at=utc_now(),
            )

        self._record_session(config, session, result, engine, role=role)

        # The plan is untrusted model output: parse strictly, fail closed.
        try:
            plan = parse_batch_plan(result.text, expected_count=batch_size)
        except PlanParseError as exc:
            detail = f"{exc.reason}: {str(exc)}"[:400]
            message = f"Malformed Orchestrator plan: {detail}"
            batch.status = BatchStatus.BLOCKED
            if machine.can_go_to(PipelinePhase.BLOCKED):
                self._transition(PipelinePhase.BLOCKED, f"Planning blocked: {message}")
            self._persist()
            self.events.error(
                message,
                source="executor",
                payload={"ok": False, "error": message, "malformed": True},
            )
            return PlanReport(
                outcome=PlanOutcome.BLOCKED,
                phase=machine.phase,
                message=message,
                session_id=session_id,
                executor_started=started,
                prompt_result=result,
                baseline_head=baseline.head,
                finished_at=utc_now(),
            )

        # Read-only guard: planning must NOT modify the supervised repository.
        after = capture_repo_fingerprint(self.controller.state.workspace.repo_path)
        if not fingerprints_equal(baseline, after):
            detail = _fingerprint_violation(baseline, after)
            message = (
                "ORCHESTRATOR READ-ONLY GUARD VIOLATION: the planning call "
                f"modified the supervised repository ({detail}). The plan is "
                "BLOCKED; the modifications are left untouched for the operator "
                "— they are not discarded, and no task was materialised."
            )
            batch.status = BatchStatus.BLOCKED
            if machine.can_go_to(PipelinePhase.BLOCKED):
                self._transition(PipelinePhase.BLOCKED, f"Planning blocked: {message}")
            self._persist()
            self.events.error(
                message,
                source="executor",
                payload={"ok": False, "guard_violation": True, "error": message},
            )
            return PlanReport(
                outcome=PlanOutcome.BLOCKED,
                phase=machine.phase,
                message=message,
                session_id=session_id,
                executor_started=started,
                prompt_result=result,
                baseline_head=baseline.head,
                guard_violation=True,
                finished_at=utc_now(),
            )

        # Plan accepted: materialise exactly the planned tasks, all PENDING.
        record = BatchPlanRecord(
            plan=plan,
            project_brief=batch.project_brief,
            requested_size=batch_size,
            plan_status="PLANNED",
            orchestrator_session_id=session_id,
            planned_at=utc_now(),
            baseline_head=baseline.head or "",
            baseline_fingerprint_json=baseline.to_json(),
        )
        for planned in plan.tasks:
            batch.tasks.append(
                TaskStateRecord(
                    index=planned.index,
                    title=planned.title,
                    prompt=planned.implementation_prompt,
                    acceptance_criteria=list(planned.acceptance_criteria),
                    audit_focus=list(planned.audit_focus),
                    state=TaskState.PENDING,
                )
            )
        batch.plan = record
        # Tasks are persisted before any implementation starts; the runner
        # flips the batch to RUNNING when the first build begins.
        self._persist()
        self.events.info(
            f"Batch {batch.batch_id} planned: exactly {plan.task_count} tasks "
            f"(title '{plan.batch_title}'); orchestrator session "
            f"{session_id or 'NOT_EXPOSED'}; plan persisted before any build.",
            source="executor",
            payload={
                "batch_id": batch.batch_id,
                "plan_status": "PLANNED",
                "task_count": plan.task_count,
                "requested_size": batch_size,
                "orchestrator_session_id": session_id,
            },
        )
        return PlanReport(
            outcome=PlanOutcome.PLANNED,
            phase=self.controller.machine.phase,
            message=f"Batch planned with exactly {plan.task_count} tasks.",
            plan=plan,
            plan_record=record,
            session_id=session_id,
            executor_started=started,
            prompt_result=result,
            baseline_head=baseline.head,
            finished_at=utc_now(),
        )

    # -- planned-task build (Session 004) ----------------------------------
    def run_task_build(
        self, *, index: int | None = None, timeout_s: float | None = None
    ) -> ExecutionReport:
        """Run the Builder for one planned task in a BRAND-NEW session.

        Session 004's batch runner calls this for every planned task.  The
        Builder's ``always_new`` policy guarantees a fresh session per
        implementation (and per fix), so no conversation history is ever
        assumed — every prompt must be self-contained.
        """
        guard = self._claim_unit("a build")
        if guard is not None:
            return guard
        try:
            return self._build(index=index, timeout_s=timeout_s)
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

    def _build(
        self, *, index: int | None = None, timeout_s: float | None
    ) -> ExecutionReport:
        machine = self.controller.machine
        if machine.phase not in (
            PipelinePhase.IDLE,
            PipelinePhase.PLANNING_BATCH,
            PipelinePhase.RUNNING_TASK,
        ):
            if not self._ensure_work_phase(PipelinePhase.RUNNING_TASK):
                return ExecutionReport(
                    outcome=ExecutionOutcome.REJECTED,
                    phase=machine.phase,
                    message=(
                        "A build only runs from PLANNING_BATCH / RUNNING_TASK "
                        f"(or IDLE for recovery); current phase is {machine.phase.value}."
                    ),
                )
        task = self._task_at(index)
        if task is None:
            return ExecutionReport(
                outcome=ExecutionOutcome.BLOCKED,
                phase=machine.phase,
                message="No task in this batch to build.",
            )
        if task.state not in (TaskState.PENDING, TaskState.RUNNING):
            return ExecutionReport(
                outcome=ExecutionOutcome.REJECTED,
                phase=machine.phase,
                message=(
                    f"Task {task.task_id} is in state {task.state.value}, not "
                    "PENDING/RUNNING; nothing to build."
                ),
            )

        role, config, engine = self._resolve_role(self.role)
        blocked = self._preflight(config, engine, role=role)
        if blocked is not None:
            self.events.error(blocked, source="executor")
            return ExecutionReport(
                outcome=ExecutionOutcome.BLOCKED,
                phase=machine.phase,
                message=blocked,
            )

        capabilities = self.registry.capabilities(engine)
        driver: BaseDriver | None = None
        try:
            driver = self.registry.create(engine, runner=self._driver_runner)
            request = self._session_request(config, role=role)
            session = driver.start_session(request)
        except (DriverError, DriverNotImplementedError) as exc:
            message = f"Driver '{engine}' refused to start a Builder session: {exc}"
            self.events.error(message, source="executor")
            return ExecutionReport(
                outcome=ExecutionOutcome.BLOCKED,
                phase=machine.phase,
                message=message,
            )

        # BUILDER's `always_new` policy: the decision must be NEW — a build
        # never reuses a Builder session (session isolation is a hard contract).
        decision = self.controller.sessions.decide(role, config.session_policy, capabilities)
        if decision.action is SessionAction.REUSE:
            self.events.error(
                f"Build run for task {task.task_id}: session policy returned REUSE "
                f"({decision.session_id}); the policy must be 'always_new' — refusing.",
                source="executor",
            )
            return self._block_task(
                task,
                "Cannot start a build: the Builder session policy must be "
                "'always_new' so every implementation runs in a NEW session.",
                phase=PipelinePhase.BLOCKED,
            )
        self.events.info(
            f"Session policy for {role.value}: {decision.action.value} — {decision.reason}",
            source="executor",
        )

        # Move the pipeline into RUNNING_TASK over legal edges only.
        if machine.phase is PipelinePhase.PLANNING_BATCH:
            self._transition(
                PipelinePhase.RUNNING_TASK,
                f"Batch is running: task {task.task_id} dispatched to {engine}.",
            )
        elif machine.phase is PipelinePhase.IDLE:
            self._ensure_work_phase(PipelinePhase.RUNNING_TASK)

        # Persist the boundary BEFORE the prompt: a crash mid-build must not
        # be indistinguishable from a never-started attempt.
        task.state = TaskState.RUNNING
        task.attempts += 1
        task.updated_at = utc_now()
        batch = self.controller.state.batch
        if batch is not None:
            batch.status = BatchStatus.RUNNING
        self._persist()
        self.events.info(
            f"Task {task.task_id} (attempt {task.attempts}) build dispatched via "
            f"driver '{engine}' (profile '{config.project_profile or '(none)'}'"
            + (f", model '{config.model}'" if config.model else "")
            + ").",
            source="executor",
        )

        handle = driver.send_prompt(session, task.prompt)
        result = driver.wait_for_completion(handle, timeout_s=timeout_s)

        started = bool(self._recorder and self._recorder.launched)
        session_id = result.session_id
        if not result.ok:
            return self._fail_task(
                task,
                result.error or "The Builder process reported failure.",
                session_id=session_id,
                prompt_result=result,
                executor_started=started,
            )

        self._record_session(config, session, result, engine, role=role)
        if session_id:
            task.builder_session_id = session_id
        task.state = TaskState.AUDITING
        task.last_error = None
        task.updated_at = utc_now()
        self._transition(
            PipelinePhase.AUDITING_TASK,
            f"Task {task.task_id} completed by the Builder and awaits the Task Auditor.",
        )
        self._persist()
        self.events.info(
            f"Task {task.task_id} build completed (attempt {task.attempts}); "
            f"session {session_id or 'NOT_EXPOSED'}; awaiting audit.",
            source="executor",
            payload=self._result_payload(task, result, session_id),
        )
        return ExecutionReport(
            outcome=ExecutionOutcome.COMPLETED,
            phase=self.controller.machine.phase,
            message=(
                f"Task {task.task_id} build completed in a NEW Builder session; "
                "the task awaits the Task Auditor."
            ),
            task_id=task.task_id,
            task_state=task.state,
            executor_started=started,
            session_id=session_id,
            prompt_result=result,
            stop_requested=self.stop_requested,
            finished_at=utc_now(),
        )

    # -- final audit (Session 005) -------------------------------------------
    def run_final_audit(
        self,
        *,
        next_batch_size: int = DEFAULT_NEXT_BATCH_SIZE,
        timeout_s: float | None = None,
    ) -> FinalAuditReport:
        """Run ONE Final Auditor call over the completed batch.

        Generic role path only: ``AgentRole.FINAL_AUDITOR`` → role config
        (honouring ``same_as_orchestrator``) → driver registry →
        ``SessionManager``.  The packet is built from durable facts; the
        repository is fingerprinted before/after and ANY modification by the
        auditor BLOCKS the final audit.  The one answer carries BOTH the
        cumulative verdict and (on PASS) the next batch plan; on PASS the
        batch becomes BATCH_COMPLETE and the next plan is persisted — never
        started.
        """
        guard = self._claim_unit("the final audit")
        if guard is not None:
            return FinalAuditReport(
                outcome=FinalAuditOutcome.STOPPED,
                phase=self.controller.machine.phase,
                message=guard.message,
            )
        try:
            return self._final_audit(
                next_batch_size=next_batch_size, timeout_s=timeout_s
            )
        except Exception as exc:  # noqa: BLE001 - an unexpected failure is a FAILED run
            self._fail_hard(exc)
            return FinalAuditReport(
                outcome=FinalAuditOutcome.FAILED,
                phase=self.controller.machine.phase,
                message=f"Executor error: {type(exc).__name__}: {exc}",
                started_at=utc_now(),
                finished_at=utc_now(),
            )
        finally:
            with self._runner_lock:
                self._active = False

    def _build_final_audit_packet(self, batch: Any, *, next_batch_size: int) -> FinalAuditPacket:
        """Assemble the packet from durable facts only (batch row + plan + tasks)."""
        plan_record = batch.plan
        plan = plan_record.plan if plan_record is not None else None
        task_rows: list[dict[str, Any]] = []
        for task in batch.tasks:
            task_rows.append(
                {
                    "index": task.index,
                    "title": task.title,
                    "implementation_prompt": task.prompt,
                    "acceptance_criteria": list(task.acceptance_criteria or []),
                    "audit_focus": list(task.audit_focus or []),
                    "attempts": task.attempts,
                    "audit_rounds": task.audit_rounds,
                    "final_verdict": task.latest_verdict,
                    "builder_session_id": task.builder_session_id,
                    "fix_session_id": task.fix_session_id,
                    "auditor_session_id": task.auditor_session_id,
                }
            )
        builder_ids = [t.builder_session_id for t in batch.tasks if t.builder_session_id]
        auditor_ids = {t.auditor_session_id for t in batch.tasks if t.auditor_session_id}
        shared_auditor = next(iter(auditor_ids)) if len(auditor_ids) == 1 else None
        return FinalAuditPacket(
            batch_id=batch.batch_id,
            batch_title=batch.batch_title,
            batch_objective=batch.batch_objective,
            project_brief=batch.project_brief,
            planned_titles=[t.title for t in (plan.tasks if plan else [])],
            tasks=task_rows,
            batch_summary_json=(
                plan_record.batch_summary_json if plan_record is not None else ""
            ),
            builder_session_ids=builder_ids,
            shared_task_auditor_session_id=shared_auditor,
            orchestrator_session_id=(
                plan_record.orchestrator_session_id if plan_record is not None else None
            ),
            baseline_head=(plan_record.baseline_head if plan_record is not None else ""),
            current_head=batch.current_head,
            workspace_path=self.controller.state.workspace.repo_path,
            next_batch_size=int(next_batch_size),
        )

    def _final_audit(
        self, *, next_batch_size: int, timeout_s: float | None
    ) -> FinalAuditReport:
        machine = self.controller.machine
        if machine.phase is not PipelinePhase.READY_FOR_FINAL_AUDIT:
            return FinalAuditReport(
                outcome=FinalAuditOutcome.REJECTED,
                phase=machine.phase,
                message=(
                    "The final audit only runs from READY_FOR_FINAL_AUDIT; "
                    f"current phase is {machine.phase.value}."
                ),
            )
        if next_batch_size < MIN_NEXT_BATCH_SIZE or next_batch_size > MAX_NEXT_BATCH_SIZE:
            return FinalAuditReport(
                outcome=FinalAuditOutcome.REJECTED,
                phase=machine.phase,
                message=(
                    f"Next batch size must be {MIN_NEXT_BATCH_SIZE}.."
                    f"{MAX_NEXT_BATCH_SIZE}; got {next_batch_size}."
                ),
            )
        batch = self.controller.state.batch
        if batch is None or not batch.tasks:
            return FinalAuditReport(
                outcome=FinalAuditOutcome.REJECTED,
                phase=machine.phase,
                message="No completed batch exists to final-audit.",
            )
        if not all(t.state is TaskState.APPROVED for t in batch.tasks):
            return FinalAuditReport(
                outcome=FinalAuditOutcome.REJECTED,
                phase=machine.phase,
                message="Not every task is APPROVED; the batch is not ready for the final audit.",
            )

        role, config, engine = self._resolve_role(FINAL_AUDITOR_ROLE)
        blocked = self._preflight(config, engine, role=role)
        if blocked is not None:
            self.events.error(blocked, source="executor")
            return FinalAuditReport(
                outcome=FinalAuditOutcome.BLOCKED, phase=machine.phase, message=blocked
            )

        capabilities = self.registry.capabilities(engine)
        driver: BaseDriver | None = None
        try:
            driver = self.registry.create(engine, runner=self._driver_runner)
            request = self._session_request(config, role=role)
            session = driver.start_session(request)
        except (DriverError, DriverNotImplementedError) as exc:
            message = f"Driver '{engine}' refused to start a Final Auditor session: {exc}"
            self.events.error(message, source="executor")
            return FinalAuditReport(
                outcome=FinalAuditOutcome.BLOCKED, phase=machine.phase, message=message
            )

        # FINAL_AUDITOR policy: configurable → continuity preferred but never
        # required; a REUSE resume failure fails honestly (never fabricated).
        decision = self.controller.sessions.decide(role, config.session_policy, capabilities)
        if decision.action is SessionAction.REUSE and decision.session_id:
            try:
                session = driver.resume_session(decision.session_id, request)
            except (DriverError, DriverNotImplementedError) as exc:
                message = (
                    f"Session policy wanted to resume Final Auditor session "
                    f"{decision.session_id}, but: {exc}"
                )
                self.events.error(message, source="executor")
                return FinalAuditReport(
                    outcome=FinalAuditOutcome.FAILED, phase=machine.phase, message=message
                )
        self.events.info(
            f"Session policy for {role.value}: {decision.action.value} — {decision.reason}",
            source="executor",
        )

        # Read-only repository fingerprint BEFORE the call.
        before = capture_repo_fingerprint(self.controller.state.workspace.repo_path)

        packet = self._build_final_audit_packet(batch, next_batch_size=next_batch_size)
        handle = driver.send_prompt(session, packet.render())
        self.events.info(
            f"Final audit dispatched via driver '{engine}' "
            f"(profile '{config.project_profile or '(none)'}'"
            + (f", model '{config.model}'" if config.model else "")
            + f") — requesting verdict + next {next_batch_size} tasks in ONE call.",
            source="executor",
        )
        result = driver.wait_for_completion(handle, timeout_s=timeout_s)

        started = bool(self._recorder and self._recorder.launched)
        session_id = result.session_id
        raw_text = result.text or ""

        if not result.ok:
            if machine.can_go_to(PipelinePhase.FAILED):
                self._transition(
                    PipelinePhase.FAILED,
                    f"Final audit failed: {result.error}",
                )
            self._persist()
            self.events.error(
                f"Final audit FAILED: {result.error}",
                source="executor",
                payload={"ok": False, "error": result.error},
            )
            return FinalAuditReport(
                outcome=FinalAuditOutcome.FAILED,
                phase=machine.phase,
                message=result.error or "The Final Auditor process reported failure.",
                session_id=session_id,
                executor_started=started,
                prompt_result=result,
                output_excerpt=raw_text[:OUTPUT_EXCERPT_CHARS],
                finished_at=utc_now(),
            )

        self._record_session(config, session, result, engine, role=role)

        # Guard: the final audit is READ/TEST-ONLY.  A worktree modification
        # BLOCKS the audit; the modifications are surfaced, never discarded.
        after = capture_repo_fingerprint(self.controller.state.workspace.repo_path)
        if not fingerprints_equal(before, after):
            detail = _fingerprint_violation(before, after)
            message = (
                "FINAL AUDITOR READ-ONLY GUARD VIOLATION: the final-audit call "
                f"modified the supervised repository ({detail}). The final audit "
                "is BLOCKED; the modifications are left untouched for the operator."
            )
            batch.status = BatchStatus.BLOCKED
            if machine.can_go_to(PipelinePhase.BLOCKED):
                self._transition(PipelinePhase.BLOCKED, f"Final audit blocked: {message}")
            self._persist()
            self.events.error(
                message,
                source="executor",
                payload={"ok": False, "guard_violation": True, "error": message},
            )
            return FinalAuditReport(
                outcome=FinalAuditOutcome.BLOCKED,
                phase=machine.phase,
                message=message,
                session_id=session_id,
                executor_started=started,
                prompt_result=result,
                guard_violation=True,
                output_excerpt=raw_text[:OUTPUT_EXCERPT_CHARS],
                finished_at=utc_now(),
            )

        # The answer is untrusted model output: parse strictly, fail closed.
        try:
            parsed = parse_final_audit(raw_text, expected_next_tasks=next_batch_size)
        except FinalAuditParseError as exc:
            detail = f"{exc.reason}: {str(exc)}"[:400]
            message = f"Malformed Final Auditor answer: {detail}"
            batch.status = BatchStatus.BLOCKED
            if machine.can_go_to(PipelinePhase.BLOCKED):
                self._transition(PipelinePhase.BLOCKED, f"Final audit blocked: {message}")
            self._persist()
            self.events.error(
                message,
                source="executor",
                payload={"ok": False, "malformed": True, "error": message},
            )
            return FinalAuditReport(
                outcome=FinalAuditOutcome.BLOCKED,
                phase=machine.phase,
                message=message,
                session_id=session_id,
                executor_started=started,
                prompt_result=result,
                output_excerpt=raw_text[:OUTPUT_EXCERPT_CHARS],
                finished_at=utc_now(),
            )

        structured_json = json.dumps(parsed.to_dict(), ensure_ascii=False, sort_keys=True)
        findings_json = json.dumps(
            [f.to_dict() for f in parsed.findings], ensure_ascii=False, sort_keys=True
        )

        if parsed.verdict.value == "NEEDS_FIX":
            # Persist the findings and stop in an explicit operator state —
            # no uncontrolled global fix loop is entered (brief §8).
            batch.status = BatchStatus.BLOCKED
            if machine.can_go_to(PipelinePhase.BLOCKED):
                self._transition(
                    PipelinePhase.BLOCKED,
                    "Final audit NEEDS_FIX: operator/supervisor handling required "
                    "(findings persisted; no automatic batch-wide fix runs).",
                )
            if self.database is not None:
                self.database.save_final_audit(
                    batch.batch_id,
                    verdict="NEEDS_FIX",
                    summary=parsed.summary,
                    findings_json=findings_json,
                    audit_json=structured_json,
                    auditor_session_id=session_id,
                    next_plan_id=None,
                )
            self._persist()
            self.events.error(
                f"Final audit NEEDS_FIX: {parsed.summary or '(no summary)'}",
                source="executor",
                payload={"final_verdict": "NEEDS_FIX", "findings": len(parsed.findings)},
            )
            return FinalAuditReport(
                outcome=FinalAuditOutcome.NEEDS_FIX,
                phase=machine.phase,
                message=(
                    f"Final audit NEEDS_FIX ({len(parsed.findings)} finding(s)); "
                    "operator handling required."
                ),
                result=parsed,
                session_id=session_id,
                executor_started=started,
                prompt_result=result,
                output_excerpt=raw_text[:OUTPUT_EXCERPT_CHARS],
                finished_at=utc_now(),
            )

        if parsed.verdict.value == "BLOCKED":
            batch.status = BatchStatus.BLOCKED
            if machine.can_go_to(PipelinePhase.BLOCKED):
                self._transition(
                    PipelinePhase.BLOCKED,
                    "Final audit BLOCKED by the auditor: operator/manual decision "
                    "required.",
                )
            if self.database is not None:
                self.database.save_final_audit(
                    batch.batch_id,
                    verdict="BLOCKED",
                    summary=parsed.summary,
                    findings_json=findings_json,
                    audit_json=structured_json,
                    auditor_session_id=session_id,
                    next_plan_id=None,
                )
            self._persist()
            self.events.error(
                f"Final audit BLOCKED: {parsed.summary or '(no summary)'}",
                source="executor",
                payload={"final_verdict": "BLOCKED"},
            )
            return FinalAuditReport(
                outcome=FinalAuditOutcome.BLOCKED,
                phase=machine.phase,
                message="Final audit BLOCKED by the auditor.",
                result=parsed,
                session_id=session_id,
                executor_started=started,
                prompt_result=result,
                output_excerpt=raw_text[:OUTPUT_EXCERPT_CHARS],
                finished_at=utc_now(),
            )

        # -- PASS: persist next plan, complete the batch, start nothing -------
        plan_id = f"nextplan_{batch.batch_id}"
        if self.database is not None:
            self.database.save_pending_next_plan(
                plan_id=plan_id,
                source_batch_id=batch.batch_id,
                requested_size=next_batch_size,
                plan_json=json.dumps(
                    parsed.next_batch.to_dict(), ensure_ascii=False, sort_keys=True
                ),
            )
            self.database.save_final_audit(
                batch.batch_id,
                verdict="PASS",
                summary=parsed.summary,
                findings_json=findings_json,
                audit_json=structured_json,
                auditor_session_id=session_id,
                next_plan_id=plan_id,
            )
        if batch.plan is not None:
            batch.plan.final_phase = PipelinePhase.BATCH_COMPLETE.value
        batch.status = BatchStatus.COMPLETE
        self._transition(
            PipelinePhase.BATCH_COMPLETE,
            f"FINAL AUDIT PASS: batch {batch.batch_id} is COMPLETE; the next "
            f"batch plan ({next_batch_size} tasks) is persisted — press "
            "START NEXT BATCH to run it. Nothing auto-starts.",
        )
        self._persist()
        self.events.info(
            f"Final audit PASS; batch {batch.batch_id} COMPLETE; next plan "
            f"{plan_id} persisted ({next_batch_size} tasks).",
            source="executor",
            payload={
                "final_verdict": "PASS",
                "next_plan_id": plan_id,
                "next_batch_size": next_batch_size,
                "final_auditor_session_id": session_id,
            },
        )
        return FinalAuditReport(
            outcome=FinalAuditOutcome.PASSED,
            phase=self.controller.machine.phase,
            message=(
                f"FINAL AUDIT PASS; batch COMPLETE; next batch plan persisted "
                f"({next_batch_size} tasks), waiting for the operator."
            ),
            result=parsed,
            session_id=session_id,
            executor_started=started,
            prompt_result=result,
            next_plan_id=plan_id,
            output_excerpt=raw_text[:OUTPUT_EXCERPT_CHARS],
            stop_requested=self.stop_requested,
            finished_at=utc_now(),
        )

    # -- next-batch handoff (Session 005) --------------------------------------
    def start_next_batch(self) -> StartNextBatchReport:
        """Materialise the persisted next plan into a new batch — NO AI calls.

        The plan was generated by the PASSing Final Audit and persisted; this
        deterministic handoff creates a NEW batch generation, materialises the
        already-planned tasks as PENDING, preserves the completed batch's
        history, and never contacts the Orchestrator or the Final Auditor.
        The new batch then waits for the operator's normal START controls.
        """
        machine = self.controller.machine
        if machine.phase not in (PipelinePhase.IDLE, PipelinePhase.BATCH_COMPLETE):
            return StartNextBatchReport(
                outcome=StartNextBatchOutcome.REJECTED,
                phase=machine.phase,
                message=(
                    "START NEXT BATCH is only available from IDLE or "
                    f"BATCH_COMPLETE; current phase is {machine.phase.value}."
                ),
            )
        if self.database is None:
            return StartNextBatchReport(
                outcome=StartNextBatchOutcome.REJECTED,
                phase=machine.phase,
                message="No database is attached; the persisted next plan is unreadable.",
            )
        pending = self.database.load_open_pending_next_plan()
        if pending is None:
            return StartNextBatchReport(
                outcome=StartNextBatchOutcome.REJECTED,
                phase=machine.phase,
                message="No pending next-batch plan is persisted.",
            )
        try:
            plan_payload = json.loads(pending["plan_json"])
            plan = BatchPlan.from_dict(plan_payload)
        except (ValueError, TypeError) as exc:
            return StartNextBatchReport(
                outcome=StartNextBatchOutcome.REJECTED,
                phase=machine.phase,
                message=f"The persisted next plan is unreadable: {exc}",
            )
        if not plan.tasks:
            return StartNextBatchReport(
                outcome=StartNextBatchOutcome.REJECTED,
                phase=machine.phase,
                message="The persisted next plan holds no tasks.",
            )
        source_batch_id = str(pending["source_batch_id"])

        # Leave BATCH_COMPLETE over its legal edge (IDLE), then start a new
        # batch generation — the same request_start the normal flow uses.
        if machine.phase is PipelinePhase.BATCH_COMPLETE:
            self._transition(
                PipelinePhase.IDLE,
                "START NEXT BATCH: leaving the completed batch.",
            )
        # Preserve the previous batch's Project Brief? No — the new batch
        # carries the NEXT plan's objective; the completed batch row keeps its
        # own brief and history untouched.
        start = self.controller.request_start(len(plan.tasks))
        if start.outcome.name == "REJECTED":
            return StartNextBatchReport(
                outcome=StartNextBatchOutcome.REJECTED,
                phase=machine.phase,
                message=start.message,
            )
        batch = self.controller.state.batch
        if batch is None:  # pragma: no cover - request_start always creates one
            return StartNextBatchReport(
                outcome=StartNextBatchOutcome.REJECTED,
                phase=machine.phase,
                message="No batch was created for the persisted next plan.",
            )
        batch.project_brief = plan.batch_objective or plan.batch_title
        for planned in plan.tasks:
            batch.tasks.append(
                TaskStateRecord(
                    index=planned.index,
                    title=planned.title,
                    prompt=planned.implementation_prompt,
                    acceptance_criteria=list(planned.acceptance_criteria),
                    audit_focus=list(planned.audit_focus),
                    state=TaskState.PENDING,
                )
            )
        record = BatchPlanRecord(
            plan=plan,
            project_brief=batch.project_brief,
            requested_size=len(plan.tasks),
            plan_status="PLANNED",
            orchestrator_session_id=None,
            planned_at=utc_now(),
        )
        batch.plan = record
        # The new batch's own HEAD is captured by the fingerprint guard when
        # its tasks run; the source batch keeps its own history untouched.
        batch.current_head = ""
        self.database.mark_pending_next_plan_consumed(str(pending["plan_id"]), batch.batch_id)
        self._persist()
        self.events.info(
            f"START NEXT BATCH: batch {batch.batch_id} materialised from persisted "
            f"plan {pending['plan_id']} ({len(plan.tasks)} PENDING tasks; source "
            f"batch {source_batch_id} preserved). No AI call was made — press "
            "the normal START controls to run it.",
            source="executor",
            payload={
                "batch_id": batch.batch_id,
                "task_count": len(plan.tasks),
                "source_plan_id": str(pending["plan_id"]),
            },
        )
        return StartNextBatchReport(
            outcome=StartNextBatchOutcome.READY,
            phase=self.controller.machine.phase,
            message=(
                f"Next batch {batch.batch_id} ready with {len(plan.tasks)} tasks "
                "(from the persisted plan); no AI call was made."
            ),
            batch_id=batch.batch_id,
            task_count=len(plan.tasks),
        )

    def _finalize_batch(self, batch: Any) -> None:
        """Write the durable Batch Summary and final phase when every task passes.

        This is **not** the Final Audit: it only records that the batch
        reached ``READY_FOR_FINAL_AUDIT`` with all tasks APPROVED, plus the
        evidence Session 005's Final Auditor will consume.
        """
        batch.status = BatchStatus.READY_FOR_FINAL_AUDIT
        fingerprint = capture_repo_fingerprint(self.controller.state.workspace.repo_path)
        batch.current_head = fingerprint.head or batch.current_head
        if batch.plan is not None:
            batch.plan.final_phase = PipelinePhase.READY_FOR_FINAL_AUDIT.value
            batch.plan.current_head = batch.current_head
            batch.plan.finalized_at = utc_now()
            batch.plan.batch_summary_json = json.dumps(
                build_batch_summary(batch=batch, plan=batch.plan),
                ensure_ascii=False,
                sort_keys=True,
            )[:80_000]
        self.events.info(
            f"Batch {batch.batch_id} finalized: all {len(batch.tasks)} tasks APPROVED; "
            f"READY_FOR_FINAL_AUDIT (current HEAD {batch.current_head or 'NOT_EXPOSED'}).",
            source="executor",
        )

    def run_task_audit(
        self, *, index: int | None = None, timeout_s: float | None = None
    ) -> ExecutionReport:
        """Dispatch the Task Auditor for the current task and apply its verdict.

        The auditor is resolved through the same generic path as the Builder:
        ``AgentRole.TASK_AUDITOR`` → role config → driver registry →
        ``SessionManager``.  Its session policy is ``persistent_per_batch``:
        the first audit of a batch opens a NEW auditor session; a re-audit
        after a fix resumes the SAME session (across the whole batch, so
        Task 1 … Task N audits all share one auditor session).
        """
        guard = self._claim_unit("an audit")
        if guard is not None:
            return guard
        try:
            return self._audit(index=index, timeout_s=timeout_s)
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

    def run_task_fix(
        self, *, index: int | None = None, timeout_s: float | None = None
    ) -> ExecutionReport:
        """Run a corrective Builder pass for the current task.

        The fix **must** use a brand-new Builder session (the role's
        ``always_new`` policy guarantees a NEW decision; the driver starts a
        fresh session).  The fix Builder receives the original task, the
        auditor's findings and the auditor's ``fix_prompt`` — never the prior
        Builder conversation.
        """
        guard = self._claim_unit("a fix")
        if guard is not None:
            return guard
        try:
            return self._fix(index=index, timeout_s=timeout_s)
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

    def next_action(self) -> TaskNextAction:
        """The deterministic next step for the current task (UI + recovery)."""
        return next_task_action(
            batch=self.controller.state.batch,
            phase=self.controller.machine.phase,
        )

    # -- audit loop internals ----------------------------------------------
    def _claim_unit(self, operation: str) -> ExecutionReport | None:
        """Claim the executor for one unit of work; error report when busy/stopped."""
        with self._runner_lock:
            phase = self.controller.machine.phase
            if self._active:
                return ExecutionReport(
                    outcome=ExecutionOutcome.REJECTED,
                    phase=phase,
                    message=(
                        f"A {operation} is already running; the executor runs "
                        "one unit of work at a time."
                    ),
                )
            if self._stop_requested:
                self._stop_requested = False
                return ExecutionReport(
                    outcome=ExecutionOutcome.STOPPED,
                    phase=phase,
                    message=(
                        "Stop was requested before the run; nothing was started."
                    ),
                )
            if self._pause_requested:
                return ExecutionReport(
                    outcome=ExecutionOutcome.STOPPED,
                    phase=phase,
                    message=(
                        "Pause was requested before the run; nothing was started."
                    ),
                )
            self._active = True
        return None

    def _task_at(self, index: int | None = None) -> TaskStateRecord | None:
        """Return the task at ``index`` (1-based), or the first undone task.

        With no index the first task that still needs work is used — that is
        how the single-task flow and restart recovery both agree on "the"
        task, and how the multi-task batch progresses in index order.
        """
        batch = self.controller.state.batch
        if batch is None or not batch.tasks:
            return None
        if index is None:
            return batch.first_undone_task()
        for task in batch.tasks:
            if task.index == int(index):
                return task
        return None

    def _current_task(self, index: int | None = None) -> TaskStateRecord | None:
        """Compatibility alias: the task under work (see :meth:`_task_at`)."""
        return self._task_at(index)

    def _ensure_work_phase(self, target: PipelinePhase) -> bool:
        """Advance a freshly-restored machine (IDLE) to an in-flight work phase.

        A restart normally restores the phase from the batch row, so this is a
        no-op.  When the machine is still IDLE but a task exists in an
        in-flight state, walk the legal edges to ``target`` — bookkeeping only,
        never starting a process (the operator still triggered this run).
        Returns False when ``target`` is not reachable from the current phase.
        """
        machine = self.controller.machine
        if machine.phase is target:
            return True
        if machine.phase is not PipelinePhase.IDLE:
            return False
        batch = self.controller.state.batch
        if batch is None or not batch.tasks:
            return False
        chain = (
            PipelinePhase.PLANNING_BATCH,
            PipelinePhase.RUNNING_TASK,
            PipelinePhase.AUDITING_TASK,
            PipelinePhase.FIX_REQUIRED,
        )
        if target not in chain:
            return False
        for step in chain:
            if machine.phase is target:
                return True
            if machine.can_go_to(step):
                self._transition(
                    step,
                    f"Recovered after restart: pipeline advanced to {step.value}.",
                )
        return machine.phase is target

    def _restore_auditor_session(self, task: TaskStateRecord) -> None:
        """Rebind the persisted auditor session id after a restart.

        Sessions are bookkeeping (never the source of truth): this re-populates
        ``SessionManager`` from a value the database already holds so the
        ``persistent_per_batch`` decision can REUSE the same external session
        for a re-audit without contacting the engine beforehand.
        """
        sessions = self.controller.sessions
        if task.auditor_session_id and sessions.current_session_id(AUDITOR_ROLE) is None:
            sessions.restore_session(AUDITOR_ROLE, task.auditor_session_id)

    def _audit(
        self, *, index: int | None = None, timeout_s: float | None
    ) -> ExecutionReport:
        machine = self.controller.machine
        if not self._ensure_work_phase(PipelinePhase.AUDITING_TASK):
            return ExecutionReport(
                outcome=ExecutionOutcome.REJECTED,
                phase=machine.phase,
                message=(
                    "The auditor only runs from AUDITING_TASK; current phase is "
                    f"{machine.phase.value}."
                ),
            )
        task = self._task_at(index)
        if task is None:
            return ExecutionReport(
                outcome=ExecutionOutcome.BLOCKED,
                phase=machine.phase,
                message="No task in this batch to audit.",
            )
        if task.audit_rounds >= MAX_AUDIT_ROUNDS:
            return self._block_task(
                task,
                f"MAX_AUDIT_ROUNDS={MAX_AUDIT_ROUNDS} reached; a further audit is "
                "outside the configured cap. The task is BLOCKED.",
                phase=PipelinePhase.BLOCKED,
            )
        if task.state is not TaskState.AUDITING:
            return ExecutionReport(
                outcome=ExecutionOutcome.REJECTED,
                phase=machine.phase,
                message=(
                    f"Task {task.task_id} is in state {task.state.value}, not "
                    "AUDITING; nothing to audit."
                ),
            )

        role, config, engine = self._resolve_role(AUDITOR_ROLE)
        blocked = self._preflight(config, engine, role=role)
        if blocked is not None:
            self.events.error(blocked, source="executor")
            return ExecutionReport(
                outcome=ExecutionOutcome.BLOCKED,
                phase=machine.phase,
                message=blocked,
            )

        capabilities = self.registry.capabilities(engine)
        driver: BaseDriver | None = None
        try:
            driver = self.registry.create(engine, runner=self._driver_runner)
            request = self._session_request(config, role=role)
            session = driver.start_session(request)
        except (DriverError, DriverNotImplementedError) as exc:
            message = f"Driver '{engine}' refused to start an auditor session: {exc}"
            self.events.error(message, source="executor")
            return ExecutionReport(
                outcome=ExecutionOutcome.BLOCKED,
                phase=machine.phase,
                message=message,
            )

        # Restart recovery: reuse the persisted auditor session for a re-audit.
        self._restore_auditor_session(task)
        decision = self.controller.sessions.decide(role, config.session_policy, capabilities)
        if decision.action is SessionAction.REUSE and decision.session_id:
            try:
                session = driver.resume_session(decision.session_id, request)
            except (DriverError, DriverNotImplementedError) as exc:
                message = (
                    f"Session policy wanted to resume auditor session "
                    f"{decision.session_id}, but: {exc}"
                )
                return self._fail_task(task, message, session_id=decision.session_id)
        self.events.info(
            f"Session policy for {role.value}: {decision.action.value} — {decision.reason}",
            source="executor",
        )

        # Persist the audit boundary BEFORE the prompt: a crash mid-audit must
        # not be indistinguishable from a never-audited task.
        task.audit_rounds += 1
        task.state = TaskState.AUDITING
        task.updated_at = utc_now()
        self._persist()
        self.events.info(
            f"Audit round {task.audit_rounds}/{MAX_AUDIT_ROUNDS} starting for "
            f"task {task.task_id} via driver '{engine}' "
            f"(profile '{config.project_profile or '(none)'}').",
            source="executor",
        )

        previous = self._stored_verdict(task) if task.audit_rounds > 1 else None
        batch = self.controller.state.batch
        packet = AuditPacket(
            task_id=task.task_id,
            title=task.title,
            implementation_prompt=task.prompt,
            workspace_path=self.controller.state.workspace.repo_path,
            attempt=task.attempts,
            audit_round=task.audit_rounds,
            batch_id=getattr(batch, "batch_id", ""),
            auditor_session_id=decision.session_id or session.session_id,
            previous=previous,
            # Session 004: the plan's contract travels with the packet so a
            # fresh auditor session verifies the real criteria and focus.
            acceptance_criteria=tuple(task.acceptance_criteria or ()),
            audit_focus=tuple(task.audit_focus or ()),
            task_index=task.index,
            batch_title=getattr(batch, "batch_title", ""),
            batch_objective=getattr(batch, "batch_objective", ""),
        )
        handle = driver.send_prompt(session, packet.render())
        result = driver.wait_for_completion(handle, timeout_s=timeout_s)

        started = bool(self._recorder and self._recorder.launched)
        session_id = result.session_id
        if not result.ok:
            return self._fail_task(
                task,
                result.error or "The auditor process reported failure.",
                session_id=session_id,
                prompt_result=result,
                executor_started=started,
            )

        self._record_session(config, session, result, engine, role=role)
        if session_id:
            task.auditor_session_id = session_id

        # The verdict is untrusted model output: parse strictly, fail closed.
        try:
            verdict = parse_audit_verdict(result.text)
        except VerdictParseError as exc:
            detail = f"{exc.reason}: {str(exc)}"[:400]
            return self._block_task(
                task,
                f"Malformed auditor output: {detail}",
                phase=PipelinePhase.BLOCKED,
                session_id=session_id,
                prompt_result=result,
                executor_started=started,
            )

        task.latest_verdict = verdict.verdict.value
        task.verdict_json = json.dumps(
            verdict.to_dict(), ensure_ascii=False, sort_keys=True
        )[:VERDICT_STORE_CHARS]
        task.fix_prompt = verdict.fix_prompt.strip() or None
        task.updated_at = utc_now()

        # -- apply the strict verdict --------------------------------------
        if verdict.verdict is AuditVerdict.PASS:
            task.state = TaskState.APPROVED
            task.last_error = None
            batch = self.controller.state.batch
            self._persist()
            self.events.info(
                f"Task {task.task_id} PASSED audit (round {task.audit_rounds}); "
                f"session {session_id or 'NOT_EXPOSED'}.",
                source="executor",
                payload={"task_id": task.task_id, "verdict": "PASS",
                         "audit_round": task.audit_rounds},
            )
            next_task = batch.first_undone_task() if batch is not None else None
            if next_task is not None:
                # More tasks remain: the deterministic loop advances to the
                # next task's build phase (AUDITING_TASK → RUNNING_TASK).
                self._transition(
                    PipelinePhase.RUNNING_TASK,
                    f"Task {task.task_id} PASSED audit round "
                    f"{task.audit_rounds}; task {next_task.index} "
                    f"({next_task.title}) is next.",
                )
                self._persist()
                return ExecutionReport(
                    outcome=ExecutionOutcome.COMPLETED,
                    phase=self.controller.machine.phase,
                    message=(
                        f"Task {task.task_id} PASSED audit; next task "
                        f"(index {next_task.index}) is ready."
                    ),
                    task_id=task.task_id,
                    task_state=task.state,
                    executor_started=started,
                    session_id=session_id,
                    prompt_result=result,
                    audit_verdict=verdict,
                    stop_requested=self.stop_requested,
                    finished_at=utc_now(),
                )

            # No tasks remain: the batch is complete up to the Final Auditor.
            if batch is not None:
                self._finalize_batch(batch)
            self._transition(
                PipelinePhase.READY_FOR_FINAL_AUDIT,
                f"All tasks approved: batch {batch.batch_id if batch else ''} "
                "is READY FOR FINAL AUDIT.",
            )
            self._persist()
            self.events.info(
                f"All tasks approved; batch "
                f"{batch.batch_id if batch else ''} is READY_FOR_FINAL_AUDIT.",
                source="executor",
                payload={"verdict": "PASS", "final_phase": "READY_FOR_FINAL_AUDIT"},
            )
            return ExecutionReport(
                outcome=ExecutionOutcome.COMPLETED,
                phase=self.controller.machine.phase,
                message="All tasks approved; the batch is READY_FOR_FINAL_AUDIT.",
                task_id=task.task_id,
                task_state=task.state,
                executor_started=started,
                session_id=session_id,
                prompt_result=result,
                audit_verdict=verdict,
                stop_requested=self.stop_requested,
                finished_at=utc_now(),
            )

        if verdict.verdict is AuditVerdict.NEEDS_FIX:
            if task.audit_rounds >= MAX_AUDIT_ROUNDS:
                self.events.error(
                    f"Audit round {task.audit_rounds} returned NEEDS_FIX, which "
                    f"exhausts MAX_AUDIT_ROUNDS={MAX_AUDIT_ROUNDS}; escalating to BLOCKED.",
                    source="executor",
                    payload={"task_id": task.task_id, "verdict": "NEEDS_FIX"},
                )
                return self._block_task(
                    task,
                    f"Max audit rounds ({MAX_AUDIT_ROUNDS}) reached and the audit "
                    "still returns NEEDS_FIX; no further fix is allowed.",
                    phase=PipelinePhase.BLOCKED,
                    session_id=session_id,
                    prompt_result=result,
                    executor_started=started,
                    verdict=verdict,
                )
            task.state = TaskState.FIX_REQUIRED
            task.last_error = None
            self._transition(
                PipelinePhase.FIX_REQUIRED,
                f"Task {task.task_id} NEEDS_FIX after audit round "
                f"{task.audit_rounds}; a fix runs in a NEW Builder session.",
            )
            self._persist()
            self.events.info(
                f"Task {task.task_id} NEEDS_FIX (round {task.audit_rounds}); "
                f"fix_prompt={len(verdict.fix_prompt)} chars, "
                f"findings={len(verdict.findings)}.",
                source="executor",
                payload={"task_id": task.task_id, "verdict": "NEEDS_FIX",
                         "audit_round": task.audit_rounds,
                         "fix_prompt_chars": len(verdict.fix_prompt)},
            )
            return ExecutionReport(
                outcome=ExecutionOutcome.NEEDS_FIX,
                phase=self.controller.machine.phase,
                message=(
                    f"Task {task.task_id} NEEDS_FIX; a fix runs next in a "
                    "brand-new Builder session."
                ),
                task_id=task.task_id,
                task_state=task.state,
                executor_started=started,
                session_id=session_id,
                prompt_result=result,
                audit_verdict=verdict,
                stop_requested=self.stop_requested,
                finished_at=utc_now(),
            )

        # audit verdict is BLOCKED
        self.events.error(
            f"Task {task.task_id} was BLOCKED by the auditor: "
            f"{(verdict.summary or '(no summary)')[:300]}",
            source="executor",
            payload={"task_id": task.task_id, "verdict": "BLOCKED",
                     "audit_round": task.audit_rounds},
        )
        return self._block_task(
            task,
            f"Auditor verdict BLOCKED: {verdict.summary or 'no summary provided'}",
            phase=PipelinePhase.BLOCKED,
            session_id=session_id,
            prompt_result=result,
            executor_started=started,
            verdict=verdict,
        )

    def _fix(self, *, index: int | None = None, timeout_s: float | None) -> ExecutionReport:
        machine = self.controller.machine
        if not self._ensure_work_phase(PipelinePhase.FIX_REQUIRED):
            return ExecutionReport(
                outcome=ExecutionOutcome.REJECTED,
                phase=machine.phase,
                message=(
                    "A fix only runs from FIX_REQUIRED; current phase is "
                    f"{machine.phase.value}."
                ),
            )
        task = self._task_at(index)
        if task is None:
            return ExecutionReport(
                outcome=ExecutionOutcome.BLOCKED,
                phase=machine.phase,
                message="No task in this batch to fix.",
            )
        if task.state is not TaskState.FIX_REQUIRED:
            return ExecutionReport(
                outcome=ExecutionOutcome.REJECTED,
                phase=machine.phase,
                message=f"Task {task.task_id} is {task.state.value}, not FIX_REQUIRED.",
            )
        if task.audit_rounds >= MAX_AUDIT_ROUNDS:
            return self._block_task(
                task,
                f"MAX_AUDIT_ROUNDS={MAX_AUDIT_ROUNDS} reached; a fix is outside "
                "the configured cap. The task is BLOCKED.",
                phase=PipelinePhase.BLOCKED,
            )
        if not (task.fix_prompt or "").strip():
            return self._block_task(
                task,
                "A fix was requested but no fix_prompt was persisted; the task "
                "cannot be corrected deterministically.",
                phase=PipelinePhase.BLOCKED,
            )

        role, config, engine = self._resolve_role(self.role)
        blocked = self._preflight(config, engine, role=role)
        if blocked is not None:
            self.events.error(blocked, source="executor")
            return ExecutionReport(
                outcome=ExecutionOutcome.BLOCKED,
                phase=machine.phase,
                message=blocked,
            )

        capabilities = self.registry.capabilities(engine)
        driver: BaseDriver | None = None
        try:
            driver = self.registry.create(engine, runner=self._driver_runner)
            request = self._session_request(config, role=role)
            session = driver.start_session(request)
        except (DriverError, DriverNotImplementedError) as exc:
            message = f"Driver '{engine}' refused to start a fix session: {exc}"
            self.events.error(message, source="executor")
            return ExecutionReport(
                outcome=ExecutionOutcome.BLOCKED,
                phase=machine.phase,
                message=message,
            )

        # BUILDER's `always_new` policy: the decision must be NEW — the fix
        # never reuses a Builder session (session isolation is a hard contract).
        decision = self.controller.sessions.decide(role, config.session_policy, capabilities)
        if decision.action is SessionAction.REUSE:
            self.events.error(
                f"Fix run for task {task.task_id}: session policy returned REUSE "
                f"({decision.session_id}); the policy must be 'always_new' — "
                "refusing to reuse a Builder session for a fix.",
                source="executor",
            )
            return self._block_task(
                task,
                "Cannot start a fix: the Builder session policy must be "
                "'always_new' so every fix runs in a NEW session.",
                phase=PipelinePhase.BLOCKED,
            )
        self.events.info(
            f"Session policy for {role.value} (fix): {decision.action.value} — "
            f"{decision.reason}",
            source="executor",
        )

        # Persist the fix boundary BEFORE the prompt.
        task.state = TaskState.RUNNING_FIX
        task.attempts += 1
        task.updated_at = utc_now()
        batch = self.controller.state.batch
        if batch is not None:
            batch.status = BatchStatus.RUNNING
        self._transition(
            PipelinePhase.RUNNING_FIX,
            f"Task {task.task_id} fix #{(task.attempts - 1)} running in a NEW "
            "Builder session.",
        )
        self._persist()

        verdict = self._stored_verdict(task) or self._minimal_verdict(task)
        prompt = render_fix_prompt(
            task_id=task.task_id,
            title=task.title,
            implementation_prompt=task.prompt,
            workspace_path=self.controller.state.workspace.repo_path,
            verdict=verdict,
            acceptance_criteria=tuple(task.acceptance_criteria or ()),
        )
        handle = driver.send_prompt(session, prompt)
        self.events.info(
            f"Fix prompt dispatched via driver '{engine}' "
            f"(profile '{config.project_profile or '(none)'}'"
            + (f", model '{config.model}'" if config.model else "")
            + ").",
            source="executor",
        )
        result = driver.wait_for_completion(handle, timeout_s=timeout_s)

        started = bool(self._recorder and self._recorder.launched)
        session_id = result.session_id
        if not result.ok:
            return self._fail_task(
                task,
                result.error or "The fix Builder process reported failure.",
                session_id=session_id,
                prompt_result=result,
                executor_started=started,
            )

        self._record_session(config, session, result, engine, role=role)
        if session_id:
            task.fix_session_id = session_id

        task.state = TaskState.AUDITING
        task.last_error = None
        task.updated_at = utc_now()
        self._transition(
            PipelinePhase.AUDITING_TASK,
            f"Task {task.task_id} fix finished; returning to the SAME Task "
            "Auditor session for re-audit.",
        )
        self._persist()
        self.events.info(
            f"Task {task.task_id} fix completed (attempt {task.attempts}); "
            f"fix session {session_id or 'NOT_EXPOSED'}; awaiting re-audit.",
            source="executor",
            payload={"task_id": task.task_id, "attempts": task.attempts,
                     "audit_rounds": task.audit_rounds},
        )
        return ExecutionReport(
            outcome=ExecutionOutcome.COMPLETED,
            phase=self.controller.machine.phase,
            message=(
                f"Task {task.task_id} fix completed in a NEW Builder session; "
                "the task returns to the Task Auditor for re-audit."
            ),
            task_id=task.task_id,
            task_state=task.state,
            executor_started=started,
            session_id=session_id,
            prompt_result=result,
            stop_requested=self.stop_requested,
            finished_at=utc_now(),
        )

    def _block_task(
        self,
        task: TaskStateRecord,
        message: str,
        *,
        phase: PipelinePhase = PipelinePhase.BLOCKED,
        session_id: str | None = None,
        prompt_result: PromptResult | None = None,
        executor_started: bool = False,
        verdict: AuditVerdictResult | None = None,
    ) -> ExecutionReport:
        """Mark the task (and pipeline + batch) BLOCKED.  Never a green outcome."""
        task.state = TaskState.BLOCKED
        task.last_error = message
        task.updated_at = utc_now()
        batch = self.controller.state.batch
        if batch is not None:
            batch.status = BatchStatus.BLOCKED
        if self.controller.machine.can_go_to(phase):
            self._transition(phase, f"Task {task.task_id} BLOCKED: {message}")
        self._persist()
        payload = {"task_id": task.task_id, "ok": False, "error": message}
        if verdict is not None:
            payload["verdict"] = verdict.verdict.value
        self.events.error(f"Task {task.task_id} BLOCKED: {message}", source="executor", payload=payload)
        return ExecutionReport(
            outcome=ExecutionOutcome.BLOCKED,
            phase=self.controller.machine.phase,
            message=message,
            task_id=task.task_id,
            task_state=task.state,
            executor_started=executor_started,
            session_id=session_id,
            prompt_result=prompt_result,
            audit_verdict=verdict,
            stop_requested=self.stop_requested,
            finished_at=utc_now(),
        )

    def _resolve_role(self, role: AgentRole) -> tuple[AgentRole, AgentRoleConfig, str]:
        """Resolve role config + engine through the generic role path."""
        config = self.controller.state.config_for(role)
        engine = self.controller.state.resolved_engine_for(role)
        return role, config, engine

    def _stored_verdict(self, task: TaskStateRecord) -> AuditVerdictResult | None:
        """Reconstruct the last strict verdict from its persisted JSON."""
        if not task.verdict_json:
            return None
        try:
            return AuditVerdictResult.from_dict(json.loads(task.verdict_json))
        except (ValueError, TypeError):  # pragma: no cover - stored data is ours
            return None

    def _minimal_verdict(self, task: TaskStateRecord) -> AuditVerdictResult:
        """Fallback verdict for a fix when only ``fix_prompt`` was persisted."""
        return AuditVerdictResult(
            verdict=AuditVerdict.NEEDS_FIX,
            summary="See fix_prompt (persisted without a full verdict payload).",
            fix_prompt=task.fix_prompt or "",
        )

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
            if session_id:
                task.builder_session_id = session_id
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
    def _preflight(
        self, config: AgentRoleConfig, engine: str, role: AgentRole | None = None
    ) -> str | None:
        """Return a blocking reason, or ``None`` when dispatch may proceed.

        Role-agnostic since Session 003: the same checks guard Builder,
        Auditor and fix-Builder dispatches.
        """
        role = role or self.role
        workspace = self.controller.state.workspace
        if not workspace.repo_path.strip():
            return (
                "No workspace repository path is configured; the executor refuses to "
                "start an agent outside a supervised directory. Set WORKSPACE first."
            )
        if not workspace.exists():
            return f"Workspace path does not exist: {workspace.repo_path}"

        if not engine:
            return f"No engine is configured for {role.value}."
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
                f"Driver '{engine}' needs a Hermes profile for {role.value}; "
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

    def _session_request(self, config: AgentRoleConfig, role: AgentRole | None = None) -> SessionRequest:
        return SessionRequest(
            role=role or self.role,
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
        role: AgentRole | None = None,
    ) -> None:
        """Mirror a real external session id into SQLite (never a fake one)."""
        role = role or self.role
        session_id = result.session_id
        if not session_id:
            return
        self.controller.sessions.register_session(
            role,
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
            "max_audit_rounds": MAX_AUDIT_ROUNDS,
            "stop_requested": self.stop_requested,
            "pause_requested": self.pause_requested,
            "running": self.is_running,
        }