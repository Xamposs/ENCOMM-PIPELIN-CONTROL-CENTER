"""Deterministic multi-task batch runner (Session 004).

This is the *autonomous* chain the brief requires: one ``START BATCH`` (or
``RESUME BATCH``) click drives planning, every task's build/audit/fix loop,
and the transition to ``READY_FOR_FINAL_AUDIT`` with no operator clicks in
between.

Design contract:

* **The state machine, not the model, owns sequencing.**  AI outputs are
  *data*: the runner reads ``next_task_action()`` (persisted task states), the
  executor applies each verdict/plan, and the runner just keeps stepping until
  a terminal batch outcome.  A malformed plan or verdict blocks the batch —
  it can never become the next step.
* **Session lifecycles are enforced by the policies + executor** (fresh Builder
  per build/fix, ONE auditor session per batch via ``persistent_per_batch``);
  this runner never touches SessionManager directly.
* **Boundary-safe control.**  Pause/stop are honoured *between* AI operations.
  An operation already running always finishes, its real result is persisted,
  and no further operation starts.  The batch runner itself runs off the UI
  thread (through the executor worker); it never blocks the Qt event loop.
* **Restart-safe.**  ``run_batch(resume=True)`` is idempotent: it re-reads the
  persisted batch and continues at the first task that still needs work —
  APPROVED tasks are never re-run, and a completed batch stays
  ``READY_FOR_FINAL_AUDIT``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from ..domain import BatchStatus, PipelinePhase, TaskState, utc_now
from .executor import (
    ExecutionOutcome,
    ExecutionReport,
    Executor,
    PlanOutcome,
    PlanReport,
    TaskNextAction,
    next_task_action,
)

__all__ = [
    "BatchOutcome",
    "BatchRunReport",
    "BatchRunner",
    "StepRecord",
]


#: Raw output excerpt cap kept on StepRecords (failure diagnosis only).
OUTPUT_EXCERPT_CHARS = 3000


class BatchOutcome(str, Enum):
    """Terminal outcome of one autonomous batch run."""

    #: Every task APPROVED; the batch awaits the Final Auditor (Session 005).
    READY_FOR_FINAL_AUDIT = "READY_FOR_FINAL_AUDIT"
    #: A plan/verdict violation, cap exhaustion or an auditor BLOCKED.
    BLOCKED = "BLOCKED"
    #: A child process failed (planning/build/audit/fix).
    FAILED = "FAILED"
    #: A stop request was honoured at a safe boundary.
    STOPPED = "STOPPED"
    #: A pause request was honoured at a safe boundary.
    PAUSED = "PAUSED"
    #: The request was not applicable to the current state.
    REJECTED = "REJECTED"


@dataclass(slots=True)
class StepRecord:
    """One real AI operation inside a batch run (evidence for the report)."""

    kind: str  # PLAN | BUILD | AUDIT | FIX
    index: int | None = None
    outcome: str = ""
    message: str = ""
    session_id: str | None = None
    duration_s: float = 0.0
    tokens: dict[str, int] = field(default_factory=dict)
    verdict: str | None = None
    guard_violation: bool = False
    #: Bounded raw model output for evidence-driven diagnosis of failures
    #: (never persisted; lives only on the in-memory report).
    output_excerpt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "index": self.index,
            "outcome": self.outcome,
            "message": self.message,
            "session_id": self.session_id,
            "duration_s": round(self.duration_s, 3),
            "tokens": dict(self.tokens),
            "verdict": self.verdict,
            "guard_violation": self.guard_violation,
        }


@dataclass(slots=True)
class BatchRunReport:
    """The machine-checkable record of one autonomous batch run."""

    outcome: BatchOutcome
    phase: PipelinePhase
    message: str
    batch_id: str = ""
    batch_title: str = ""
    requested_size: int = 0
    completed_tasks: int = 0
    total_tasks: int = 0
    steps: list[StepRecord] = field(default_factory=list)
    started_at: str = field(default_factory=utc_now)
    finished_at: str | None = None

    @property
    def ok(self) -> bool:
        return self.outcome is BatchOutcome.READY_FOR_FINAL_AUDIT

    def summary(self) -> str:
        parts = [f"{self.outcome.value}: {self.message}"]
        if self.batch_id:
            parts.append(f"batch={self.batch_id}")
        parts.append(f"tasks={self.completed_tasks}/{self.total_tasks}")
        return " | ".join(parts)

    def token_totals(self) -> dict[str, dict[str, int]]:
        """Aggregate the real token counts per operation kind (evidence)."""
        totals: dict[str, dict[str, int]] = {}
        for step in self.steps:
            bucket = totals.setdefault(step.kind, {"input": 0, "output": 0, "total": 0})
            for key in ("input", "output", "total"):
                bucket[key] = bucket.get(key, 0) + int(step.tokens.get(key, 0) or 0)
        return totals

    def operation_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for step in self.steps:
            counts[step.kind] = counts.get(step.kind, 0) + 1
        return counts


class BatchRunner:
    """Runs one batch autonomously to a terminal outcome."""

    def __init__(self, executor: Executor) -> None:
        self.executor = executor

    # -- public API --------------------------------------------------------
    def run_batch(
        self,
        *,
        project_brief: str = "",
        batch_size: int | None = None,
        resume: bool = False,
        timeout_s: float | None = None,
    ) -> BatchRunReport:
        """Run (or resume) the batch until a terminal outcome.

        ``resume=True`` makes the run idempotent: the batch continues from its
        persisted state (in a PAUSED phase it is resumed first); APPROVED tasks
        are never re-run.
        """
        executor = self.executor
        controller = executor.controller
        if executor.is_running:
            return self._terminal(
                BatchOutcome.REJECTED,
                "The executor is already running a unit of work.",
            )

        if controller.state.batch is None and controller.machine.phase is PipelinePhase.IDLE:
            size = int(batch_size or 5)
            start = controller.request_start(size)
            if start.outcome.name == "REJECTED":
                return self._terminal(BatchOutcome.REJECTED, start.message)
            # Set the brief AFTER request_start so no phantom CREATED batch row
            # is left behind (request_start builds the real batch record).
            if project_brief:
                controller.set_project_brief(project_brief)
        if resume and controller.machine.phase is PipelinePhase.PAUSED:
            resumed = controller.request_resume()
            if resumed.outcome.name == "REJECTED":
                return self._terminal(BatchOutcome.REJECTED, resumed.message)

        batch = controller.state.batch
        if batch is None:
            return self._terminal(
                BatchOutcome.REJECTED, "No batch exists; press PLAN + START BATCH."
            )

        steps: list[StepRecord] = []
        started_at = utc_now()
        while True:
            machine = controller.machine
            batch = controller.state.batch
            if batch is None:
                return self._terminal(
                    BatchOutcome.REJECTED, "The batch disappeared mid-run.", steps, started_at
                )

            # -- boundary control (checked BEFORE the next AI operation) ----
            if machine.phase is PipelinePhase.PAUSED:
                return self._terminal(
                    BatchOutcome.PAUSED,
                    "Paused at a safe boundary; press RESUME BATCH to continue.",
                    steps, started_at,
                )
            if batch.status is BatchStatus.STOPPED:
                return self._terminal(
                    BatchOutcome.STOPPED, "Batch stopped at a safe boundary.", steps, started_at
                )
            if executor.stop_requested:
                controller.request_stop()
                executor.clear_control_flags()
                return self._terminal(
                    BatchOutcome.STOPPED,
                    "Stop honoured at a safe boundary; the batch was stopped.",
                    steps, started_at,
                )
            if executor.pause_requested:
                if controller.machine.can_go_to(PipelinePhase.PAUSED):
                    controller.request_pause()
                    executor.clear_control_flags()
                    return self._terminal(
                        BatchOutcome.PAUSED,
                        "Pause honoured at a safe boundary; press RESUME BATCH to continue.",
                        steps, started_at,
                    )
                executor.clear_control_flags()

            action = next_task_action(batch=batch, phase=machine.phase)

            # -- deterministic dispatch -------------------------------------
            if action is TaskNextAction.PLAN:
                plan = executor.plan_batch(
                    project_brief=batch.project_brief or project_brief,
                    batch_size=batch.size,
                    timeout_s=timeout_s,
                )
                steps.append(self._plan_step(plan))
                if plan.outcome is not PlanOutcome.PLANNED:
                    return self._map_plan_failure(plan, steps, started_at)
                continue

            if action is TaskNextAction.BUILD:
                report = executor.run_task_build(
                    index=batch.first_undone_index(), timeout_s=timeout_s
                )
                steps.append(self._step("BUILD", batch.first_undone_index(), report))
                if not self._step_ok(report):
                    return self._map_execution_failure(report, steps, started_at)
                continue

            if action in (TaskNextAction.AUDIT, TaskNextAction.RE_AUDIT):
                report = executor.run_task_audit(
                    index=batch.first_undone_index(), timeout_s=timeout_s
                )
                steps.append(self._step("AUDIT", batch.first_undone_index(), report))
                if report.outcome is ExecutionOutcome.NEEDS_FIX:
                    continue  # the loop decides FIX next
                if not self._step_ok(report):
                    return self._map_execution_failure(report, steps, started_at)
                continue

            if action is TaskNextAction.FIX:
                report = executor.run_task_fix(
                    index=batch.first_undone_index(), timeout_s=timeout_s
                )
                steps.append(self._step("FIX", batch.first_undone_index(), report))
                if not self._step_ok(report):
                    return self._map_execution_failure(report, steps, started_at)
                continue

            if action is TaskNextAction.COMPLETE:
                return self._terminal(
                    BatchOutcome.READY_FOR_FINAL_AUDIT,
                    "All tasks APPROVED — the batch is READY FOR FINAL AUDIT. STOP.",
                    steps, started_at,
                )
            if action is TaskNextAction.BLOCKED:
                return self._terminal(
                    BatchOutcome.BLOCKED,
                    "The batch is BLOCKED (audit cap / malformed verdict / "
                    "plan violation); a human must intervene.",
                    steps, started_at,
                )
            if action is TaskNextAction.FAILED:
                return self._terminal(
                    BatchOutcome.FAILED,
                    "The batch is FAILED (a child process failed).",
                    steps, started_at,
                )
            return self._terminal(
                BatchOutcome.REJECTED,
                f"Nothing actionable in this state (next action: {action.value}).",
                steps, started_at,
            )

    # -- step builders ------------------------------------------------------
    @staticmethod
    def _step(kind: str, index: int | None, report: ExecutionReport) -> StepRecord:
        result = report.prompt_result
        stream = dict((result.metadata or {}).get("stream") or {}) if result is not None else {}
        return StepRecord(
            kind=kind,
            index=index,
            outcome=report.outcome.value,
            message=report.message,
            session_id=report.session_id,
            duration_s=(result.duration_s if result is not None else 0.0),
            tokens={str(k): int(v) for k, v in (stream.get("tokens") or {}).items()},
            verdict=(
                report.audit_verdict.verdict.value
                if report.audit_verdict is not None
                else None
            ),
            output_excerpt=(
                (result.text or "")[:OUTPUT_EXCERPT_CHARS]
                if result is not None and not report.ok
                else ""
            ),
        )

    @staticmethod
    def _plan_step(report: PlanReport) -> StepRecord:
        raw_text = report.prompt_result.text if report.prompt_result is not None else ""
        return StepRecord(
            kind="PLAN",
            index=None,
            outcome=report.outcome.value,
            message=report.message,
            session_id=report.session_id,
            duration_s=(
                report.prompt_result.duration_s if report.prompt_result is not None else 0.0
            ),
            tokens=(
                {
                    str(k): int(v)
                    for k, v in (
                        ((report.prompt_result.metadata or {}).get("stream") or {}).get("tokens") or {}
                    ).items()
                }
                if report.prompt_result is not None
                else {}
            ),
            guard_violation=report.guard_violation,
            output_excerpt=raw_text[:OUTPUT_EXCERPT_CHARS] if not report.ok else "",
        )

    @staticmethod
    def _step_ok(report: ExecutionReport) -> bool:
        return report.outcome in (ExecutionOutcome.COMPLETED,)

    # -- terminal mapping ---------------------------------------------------
    def _map_plan_failure(
        self, report: PlanReport, steps: list[StepRecord], started_at: str
    ) -> BatchRunReport:
        if report.outcome is PlanOutcome.BLOCKED:
            outcome = BatchOutcome.BLOCKED
        elif report.outcome is PlanOutcome.FAILED:
            outcome = BatchOutcome.FAILED
        else:
            outcome = BatchOutcome.REJECTED
        return self._terminal(outcome, report.message, steps, started_at)

    def _map_execution_failure(
        self, report: ExecutionReport, steps: list[StepRecord], started_at: str
    ) -> BatchRunReport:
        if report.outcome is ExecutionOutcome.BLOCKED:
            return self._terminal(BatchOutcome.BLOCKED, report.message, steps, started_at)
        return self._terminal(BatchOutcome.FAILED, report.message, steps, started_at)

    def _terminal(
        self,
        outcome: BatchOutcome,
        message: str,
        steps: list[StepRecord] | None = None,
        started_at: str | None = None,
    ) -> BatchRunReport:
        batch = self.executor.controller.state.batch
        approved = 0
        total = 0
        title = ""
        size = 0
        if batch is not None:
            approved = sum(1 for t in batch.tasks if t.state.value == "APPROVED")
            total = len(batch.tasks)
            title = batch.batch_title
            size = batch.size
        return BatchRunReport(
            outcome=outcome,
            phase=self.executor.controller.machine.phase,
            message=message,
            batch_id=batch.batch_id if batch is not None else "",
            batch_title=title,
            requested_size=size,
            completed_tasks=approved,
            total_tasks=total,
            steps=list(steps or []),
            started_at=started_at or utc_now(),
            finished_at=utc_now(),
        )