"""Worker thread for real execution.

The Qt event loop must never be blocked by an AI process: a Builder prompt runs
for seconds to minutes.  :class:`ExecutorWorker` runs the deterministic executor
on a :class:`QThread` and reports the resulting ``ExecutionReport`` back to the UI
thread through a queued signal.

Only the executor runs off the UI thread; every state change still goes through
the controller/executor (SQLite is serialised by the database lock, and the log
panel is fed by a queued connection), so the UI stays a pure view.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from ..core.executor import ExecutionOutcome, ExecutionReport, Executor, TaskSpec

__all__ = ["ExecutorWorker", "start_executor_worker"]


class ExecutorWorker(QObject):
    """Runs one dispatch; emits ``finished`` with the real report."""

    finished = Signal(object)

    def __init__(
        self,
        executor: Executor,
        spec: TaskSpec,
        timeout_s: float | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.executor = executor
        self.spec = spec
        self.timeout_s = timeout_s

    def run(self) -> None:
        """Slot connected to ``QThread.started``."""
        report: ExecutionReport | None = None
        try:
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
    parent: QObject | None = None,
) -> tuple[QThread, ExecutorWorker]:
    """Start ``executor`` on a fresh thread; returns ``(thread, worker)``.

    The caller keeps both references: dropping them would let Python garbage
    collect a running QThread.
    """
    thread = QThread(parent)
    worker = ExecutorWorker(executor, spec, timeout_s)
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