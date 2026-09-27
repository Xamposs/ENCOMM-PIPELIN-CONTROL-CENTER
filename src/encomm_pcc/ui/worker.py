"""Worker thread for real execution.

The Qt event loop must never be blocked by an AI process: a Builder prompt runs
for seconds to minutes.  :class:`ExecutorWorker` runs the deterministic executor
on a :class:`QThread` and reports the resulting report object back to the UI
thread through a queued signal.

Only the executor runs off the UI thread; every state change still goes through
the controller/executor (SQLite is serialised by the database lock, and the log
panel is fed by a queued connection), so the UI stays a pure view.

Session 010: the worker carries EVERY run parameter the advanced window passes
(``resume`` / ``project_brief`` / ``batch_size`` / ``next_batch_size`` used to
be silently dropped by :func:`start_executor_worker`), and gains the two
continuous actions (``continuous`` / ``continuous_resume``) that hand Simple
Mode's Continuous Run to the core :class:`ContinuousRunner` — the UI never
re-implements the loop and never simulates clicks.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from ..core import (
    BatchRunReport,
    BatchRunner,
    ContinuousRunReport,
    ContinuousRunner,
    FinalAuditReport,
)
from ..core.executor import ExecutionOutcome, ExecutionReport, Executor, TaskSpec

__all__ = ["ExecutorWorker", "start_executor_worker"]


class ExecutorWorker(QObject):
    """Runs one executor action; emits ``finished`` with the real report."""

    finished = Signal(object)

    def __init__(
        self,
        executor: Executor,
        spec: TaskSpec,
        timeout_s: float | None = None,
        action: str = "dispatch",
        resume: bool = False,
        project_brief: str = "",
        batch_size: int | None = None,
        next_batch_size: int | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.executor = executor
        self.spec = spec
        self.timeout_s = timeout_s
        #: ``dispatch`` (Builder run) | ``audit`` (Task Auditor) | ``fix`` |
        #: ``batch`` (autonomous multi-task batch run, Session 004) |
        #: ``final_audit`` (ONE Final Auditor call, Session 005) |
        #: ``continuous`` (batch-after-batch loop, Session 010) |
        #: ``continuous_resume`` (continuous loop from durable state).
        self.action = action
        self.resume = bool(resume)
        self.project_brief = project_brief
        self.batch_size = batch_size
        self.next_batch_size = next_batch_size

    def run(self) -> None:
        """Slot connected to ``QThread.started``."""
        report: (
            ExecutionReport | BatchRunReport | FinalAuditReport | ContinuousRunReport | None
        ) = None
        try:
            if self.action == "batch":
                report = BatchRunner(self.executor).run_batch(
                    project_brief=self.project_brief,
                    batch_size=self.batch_size,
                    resume=self.resume,
                    timeout_s=self.timeout_s,
                )
            elif self.action == "continuous":
                report = ContinuousRunner(self.executor).run_continuous(
                    project_brief=self.project_brief,
                    batch_size=int(self.batch_size or 5),
                    next_batch_size=int(self.next_batch_size or 5),
                    timeout_s=self.timeout_s,
                )
            elif self.action == "continuous_resume":
                report = ContinuousRunner(self.executor).resume_continuous(
                    next_batch_size=int(self.next_batch_size or 5),
                    timeout_s=self.timeout_s,
                )
            elif self.action == "final_audit":
                report = self.executor.run_final_audit(
                    next_batch_size=int(self.next_batch_size or 5),
                    timeout_s=self.timeout_s,
                )
            elif self.action == "audit":
                report = self.executor.run_task_audit(timeout_s=self.timeout_s)
            elif self.action == "fix":
                report = self.executor.run_task_fix(timeout_s=self.timeout_s)
            else:
                report = self.executor.dispatch_single_task(self.spec, timeout_s=self.timeout_s)
        except Exception as exc:  # noqa: BLE001 - the UI must never lose the failure
            report = ExecutionReport(
                outcome=ExecutionOutcome.FAILED,
                phase=self.executor.controller.machine.phase,
                message=f"Worker error: {type(exc).__name__}: {exc}",
            )
        finally:
            self.finished.emit(report)


def start_executor_worker(
    executor: Executor,
    spec: TaskSpec,
    *,
    timeout_s: float | None = None,
    action: str = "dispatch",
    resume: bool = False,
    project_brief: str = "",
    batch_size: int | None = None,
    next_batch_size: int | None = None,
    parent: QObject | None = None,
) -> tuple[QThread, ExecutorWorker]:
    """Start ``executor`` on a fresh thread; returns ``(thread, worker)``.

    The caller keeps both references: dropping them would let Python garbage
    collect a running QThread.

    ``action`` selects the executor method: ``dispatch`` (Builder run),
    ``audit`` (Task Auditor), ``fix`` (Builder fix in a NEW session),
    ``batch`` (autonomous batch), ``final_audit`` (one Final Auditor call),
    ``continuous`` / ``continuous_resume`` (the core ContinuousRunner loop).

    Every run parameter is forwarded to the worker — a dropped keyword here
    used to silently change what the executor actually ran.
    """
    thread = QThread(parent)
    worker = ExecutorWorker(
        executor,
        spec,
        timeout_s,
        action=action,
        resume=resume,
        project_brief=project_brief,
        batch_size=batch_size,
        next_batch_size=next_batch_size,
    )
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    return thread, worker


def describe_report(report: Any) -> str:  # noqa: ANN401 - ExecutionReport | None
    """Human-readable one-liner for the UI status line."""
    if report is None:
        return "No report."
    if isinstance(report, ExecutionReport):
        return report.summary()
    return str(report)