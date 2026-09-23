"""Main application window.

Layout (top to bottom): WORKSPACE, the four role blocks in a 2x2 grid, BATCH,
TASK (the Session 002 one-task dispatch control), then the LOG PANEL in a
resizable splitter.

Threading rule: **no AI process ever runs on the UI thread.**  Dispatch goes
through :func:`~encomm_pcc.ui.worker.start_executor_worker`, and the resulting
``ExecutionReport`` arrives back on the UI thread through a queued signal.
"""

from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QLabel,
    QMainWindow,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..core import APP_NAME, ExecutionReport, FinalAuditReport, PipelineController, TaskSpec
from ..core.executor import StartNextBatchReport
from ..domain import AgentRole, PipelinePhase
from ..drivers import PLANNED_DRIVERS
from .history_panel import HistoryPanel
from .panels import BatchPanel, FinalAuditPanel, LogPanel, RolePanel, TaskPanel, WorkspacePanel
from .worker import start_executor_worker

__all__ = ["MainWindow"]


class MainWindow(QMainWindow):
    """The single application window."""

    def __init__(
        self,
        controller: PipelineController,
        profiles: Sequence[str] = (),
        profile_method: str = "",
        dispatch_timeout_s: float | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self._profiles: tuple[str, ...] = tuple(profiles)
        self._profile_method = profile_method
        self._dispatch_timeout_s = dispatch_timeout_s
        self._thread = None
        self._worker = None

        self.setWindowTitle(f"{APP_NAME} — v{__version__}")
        self.resize(1180, 920)

        # -- panels -------------------------------------------------------
        self.workspace_panel = WorkspacePanel(controller)
        self.role_panels: dict[AgentRole, RolePanel] = {
            role: RolePanel(role, controller, controller.registry, profiles=self._profiles)
            for role in AgentRole
        }
        self.batch_panel = BatchPanel(controller)
        self.final_audit_panel = FinalAuditPanel(controller)
        self.task_panel = TaskPanel(
            controller, profiles=self._profiles, profile_method=self._profile_method
        )
        self.history_panel = HistoryPanel(controller)
        self.log_panel = LogPanel()

        # -- configuration area -------------------------------------------
        config_body = QWidget()
        config_layout = QVBoxLayout(config_body)
        config_layout.setContentsMargins(0, 0, 0, 0)
        config_layout.addWidget(self.workspace_panel)

        roles_group = QGroupBox("ROLES")
        roles_layout = QGridLayout(roles_group)
        roles_layout.addWidget(self.role_panels[AgentRole.ORCHESTRATOR], 0, 0)
        roles_layout.addWidget(self.role_panels[AgentRole.FINAL_AUDITOR], 0, 1)
        roles_layout.addWidget(self.role_panels[AgentRole.TASK_AUDITOR], 1, 0)
        roles_layout.addWidget(self.role_panels[AgentRole.BUILDER], 1, 1)
        config_layout.addWidget(roles_group)

        config_layout.addWidget(self.batch_panel)
        config_layout.addWidget(self.final_audit_panel)
        config_layout.addWidget(self.task_panel)

        planned = QLabel(
            "Planned engines (not implemented): "
            + ", ".join(f"{p.display_name}" for p in PLANNED_DRIVERS)
        )
        planned.setWordWrap(True)
        planned.setStyleSheet("color: palette(mid);")
        config_layout.addWidget(planned)
        config_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(config_body)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(scroll)
        splitter.addWidget(self.history_panel)
        splitter.addWidget(self.log_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 2)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.addWidget(splitter)
        self.setCentralWidget(central)

        self.statusBar().showMessage(self._idle_status())

        self._connect_signals()
        self._subscribe_to_events()
        self._log_startup_summary()

    # -- wiring ----------------------------------------------------------
    def _connect_signals(self) -> None:
        self.workspace_panel.changed.connect(self._on_workspace_changed)
        for panel in self.role_panels.values():
            panel.changed.connect(self._on_role_changed)

        self.batch_panel.start_requested.connect(self._on_start)
        self.batch_panel.pause_requested.connect(self._on_pause)
        self.batch_panel.resume_requested.connect(self._on_resume)
        self.batch_panel.stop_requested.connect(self._on_stop)
        self.batch_panel.plan_and_start_requested.connect(self._on_plan_and_start)
        self.batch_panel.resume_batch_requested.connect(self._on_resume_batch)
        self.final_audit_panel.final_audit_requested.connect(self._on_run_final_audit)
        self.final_audit_panel.start_next_batch_requested.connect(self._on_start_next_batch)
        self.task_panel.dispatch_requested.connect(self._on_dispatch_requested)
        self.task_panel.audit_requested.connect(self._on_audit_requested)
        self.task_panel.fix_requested.connect(self._on_fix_requested)
        for panel in self.role_panels.values():
            panel.session_selected.connect(self._on_session_selected)
            panel.sessions_refresh_requested.connect(self._on_sessions_refresh)

    def _subscribe_to_events(self) -> None:
        self.controller.events.subscribe(self._on_event)

    def _on_event(self, record: object) -> None:
        # Marshalled onto the UI thread by the LogPanel's queued connection.
        self.log_panel.record_received.emit(record)

    def _idle_status(self) -> str:
        if self.controller.executor_attached:
            return (
                "Ready — executor attached; Start creates a batch, TASK dispatches "
                "one controlled task."
            )
        return "Ready — no executor attached; Start records batch state only."

    def _log_startup_summary(self) -> None:
        events = self.controller.events
        events.info(f"{APP_NAME} v{__version__} started.", source="ui")
        for row in self.controller.driver_capabilities():
            events.info(
                f"Driver '{row['driver_id']}': implemented={row['implemented']}, "
                f"sessions={row['supports_sessions']}, binary_present={row['binary_present']}",
                source="ui",
            )
        if self._profiles:
            events.info(
                f"Hermes profile discovery found {len(self._profiles)} profiles "
                f"({self._profile_method}): {', '.join(self._profiles)}",
                source="ui",
            )
        else:
            events.warning(
                "Hermes profile discovery found no profiles; the profile field must be "
                "typed manually.",
                source="ui",
            )
        executor = self.controller.executor
        if executor is None:
            events.warning("No executor attached: Start records batch state only.", source="ui")
        else:
            events.info(
                f"Executor attached for role {executor.role.value}; dispatch runs off the "
                "UI thread.",
                source="ui",
            )
        for planned in PLANNED_DRIVERS:
            events.debug(f"Planned engine '{planned.driver_id}' — not implemented.", source="ui")

    # -- handlers ---------------------------------------------------------
    def _on_workspace_changed(self, name: str, repo_path: str) -> None:
        self.controller.set_workspace(name, repo_path)
        self.task_panel.refresh()

    def _on_role_changed(self, role: AgentRole, values: dict) -> None:
        self.controller.set_role_config(role, **values)
        # "Same as orchestrator" affects the FINAL AUDITOR session field.
        self.role_panels[AgentRole.FINAL_AUDITOR].refresh_session_field()
        if role is AgentRole.ORCHESTRATOR:
            self.role_panels[AgentRole.FINAL_AUDITOR].refresh_session_field()
        self.task_panel.refresh()

    # -- external session selector (Session 006) ---------------------------
    def _on_sessions_refresh(self, role: AgentRole) -> None:
        """Run driver discovery for ``role``'s engine and render the options.

        Read-only and offline: discovery never contacts the engine's model and
        never launches a process.
        """
        panel = self.role_panels[role]
        engine = self.controller.state.resolved_engine_for(role)
        driver = None
        if engine and self.controller.registry.is_registered(engine):
            driver = self.controller.registry.create(engine)
        discoverer = getattr(driver, "discover_sessions", None) if driver else None
        if not callable(discoverer):
            panel.update_session_options(None, [])
            self.controller.events.warning(
                f"{role.value}: engine '{engine or '(none)'}' does not support "
                "session discovery.",
                source="ui",
            )
            return
        workspace = self.controller.state.workspace.repo_path or None
        try:
            result = discoverer(workspace_path=workspace)
        except Exception as exc:  # noqa: BLE001 - discovery must never break the UI
            self.controller.events.error(
                f"{role.value}: session discovery failed: {exc}", source="ui"
            )
            panel.update_session_options(None, [])
            return
        panel.update_session_options(result, result.sessions)
        self.controller.events.info(
            f"{role.value}: session discovery via {result.mechanism or engine} "
            f"found {len(result.sessions)} session(s).",
            source="ui",
        )

    def _on_session_selected(self, role: AgentRole, external_session_id: str) -> None:
        """Bind a discovered session — or clear the binding (NEW).  Zero AI calls."""
        panel = self.role_panels[role]
        session_id = str(external_session_id or "").strip()
        if not session_id:
            self.controller.clear_external_session(role)
            panel._rebuild_session_combo()  # noqa: SLF001 - render the cleared binding
            self.statusBar().showMessage(
                f"{role.value}: New session — the next real execution creates one."
            )
            return
        engine = self.controller.state.resolved_engine_for(role)
        if not engine or not self.controller.registry.is_registered(engine):
            self.controller.events.error(
                f"{role.value}: cannot bind a session without a configured engine.",
                source="ui",
            )
            return
        descriptor = next(
            (
                d
                for d in panel._session_descriptors  # noqa: SLF001 - window-owned state
                if d.session_id == session_id
            ),
            None,
        )
        self.controller.bind_external_session(
            role,
            engine,
            session_id,
            title=descriptor.title if descriptor else None,
            workspace_path=(
                descriptor.workspace_path if descriptor
                else self.controller.state.workspace.repo_path or None
            ),
        )
        panel._rebuild_session_combo()  # noqa: SLF001
        self.statusBar().showMessage(
            f"{role.value}: external session {session_id} bound to '{engine}'."
        )

    def _on_start(self, size: int) -> None:
        result = self.controller.request_start(size)
        self._after_control(result.message, result.phase)
        if self.controller.executor_attached:
            self.statusBar().showMessage(
                f"{result.phase.value} — use TASK → Dispatch task to run the task."
            )
        else:
            self.statusBar().showMessage(
                f"{result.phase.value} — no executor attached; state only."
            )

    def _on_pause(self) -> None:
        executor = self.controller.executor
        if executor is not None and executor.is_running:
            executor.request_pause()
            self.controller.events.warning(
                "Pause requested while a task is running: it takes effect at the next "
                "safe task boundary — mid-prompt suspension is not attempted.",
                source="ui",
            )
            self.statusBar().showMessage("Pause requested — effective at the next task boundary.")
            return
        result = self.controller.request_pause()
        self._after_control(result.message, result.phase)

    def _on_resume(self) -> None:
        result = self.controller.request_resume()
        executor = self.controller.executor
        if executor is not None:
            executor.clear_control_flags()
        self._after_control(result.message, result.phase)

    def _on_stop(self) -> None:
        executor = self.controller.executor
        if executor is not None and executor.is_running:
            executor.request_stop()
            self.statusBar().showMessage(
                "Stop requested — the running prompt finishes, then the executor stops."
            )
            return
        result = self.controller.request_stop()
        self._after_control(result.message, result.phase)

    def _on_plan_and_start(self, brief: str, size: int) -> None:
        """Session 004: ONE autonomous batch run, off the UI thread."""
        executor = self.controller.executor
        if executor is None:
            self.controller.events.error(
                "Plan + Start requested, but no executor is attached.", source="ui"
            )
            self.statusBar().showMessage("No executor attached — nothing was started.")
            return
        if executor.is_running or (self._thread is not None and self._thread.isRunning()):
            self.statusBar().showMessage("A batch is already running.")
            return
        self._start_batch_worker(brief=brief, size=size, resume=False)

    def _on_resume_batch(self) -> None:
        """RESUME BATCH: continue from durable state (no duplicate work)."""
        executor = self.controller.executor
        if executor is None:
            self.controller.events.error(
                "Resume requested, but no executor is attached.", source="ui"
            )
            self.statusBar().showMessage("No executor attached — nothing was resumed.")
            return
        if executor.is_running or (self._thread is not None and self._thread.isRunning()):
            self.statusBar().showMessage("A batch is already running.")
            return
        brief = (
            self.controller.state.batch.project_brief
            if self.controller.state.batch is not None
            else ""
        )
        size = (
            self.controller.state.batch.size
            if self.controller.state.batch is not None
            else 5
        )
        self._start_batch_worker(brief=brief, size=size, resume=True)

    def _on_run_final_audit(self, next_batch_size: int) -> None:
        """RUN FINAL AUDIT: ONE real Final Auditor call, off the UI thread."""
        executor = self.controller.executor
        if executor is None:
            self.controller.events.error(
                "Final audit requested, but no executor is attached.", source="ui"
            )
            self.statusBar().showMessage("No executor attached — nothing was started.")
            return
        if executor.is_running or (self._thread is not None and self._thread.isRunning()):
            self.statusBar().showMessage("An executor action is already running.")
            return
        self.final_audit_panel.run_button.setEnabled(False)
        self.controller.events.info(
            f"FINAL AUDIT requested (next batch size {next_batch_size}) — one real "
            "Final Auditor call, running off the UI thread.",
            source="ui",
        )
        self.statusBar().showMessage(
            "FINAL AUDIT running — one call: cumulative verdict + next plan…"
        )
        thread, worker = start_executor_worker(
            executor,
            TaskSpec(title="", prompt=""),
            timeout_s=self._dispatch_timeout_s,
            action="final_audit",
            next_batch_size=next_batch_size,
            parent=self,
        )
        worker.finished.connect(self._on_final_audit_finished)
        thread.finished.connect(self._on_dispatch_thread_finished)
        self._thread, self._worker = thread, worker
        thread.start()

    def _on_final_audit_finished(self, report: object) -> None:
        self._after_control("", self.controller.machine.phase)
        if isinstance(report, FinalAuditReport):
            self.controller.events.info(
                f"Final audit finished — {report.summary()}", source="ui"
            )
            if report.outcome.value == "PASSED":
                self.statusBar().showMessage(
                    "FINAL AUDIT: PASS — batch COMPLETE. The next batch plan is "
                    "persisted; press START NEXT BATCH when ready (nothing auto-runs)."
                )
            elif report.outcome.value == "NEEDS_FIX":
                self.statusBar().showMessage(
                    "FINAL AUDIT: NEEDS_FIX — findings persisted; operator handling "
                    "required."
                )
            else:
                self.statusBar().showMessage(
                    f"FINAL AUDIT: {report.outcome.value} — {report.message}"
                )
            return
        self.statusBar().showMessage("Final audit finished without a report.")

    def _on_start_next_batch(self) -> None:
        """START NEXT BATCH: deterministic handoff — never an AI call."""
        executor = self.controller.executor
        if executor is None:
            self.controller.events.error(
                "START NEXT BATCH requested, but no executor is attached.", source="ui"
            )
            return
        if executor.is_running or (self._thread is not None and self._thread.isRunning()):
            self.statusBar().showMessage("An executor action is already running.")
            return
        report = executor.start_next_batch()
        if isinstance(report, StartNextBatchReport):
            self.controller.events.info(
                f"START NEXT BATCH — {report.message}", source="ui"
            )
            self.statusBar().showMessage(
                f"Next batch ready: {report.task_count} tasks "
                f"({report.batch_id}) — press the normal START controls."
                if report.ok
                else f"START NEXT BATCH refused: {report.message}"
            )
        self._after_control("", self.controller.machine.phase)

    def _start_batch_worker(self, *, brief: str, size: int, resume: bool) -> None:
        self.controller.events.info(
            f"Batch {'resumed' if resume else 'started'} off the UI thread "
            f"(brief {len(brief)} chars, size {size}).",
            source="ui",
        )
        self.batch_panel.plan_start_button.setEnabled(False)
        self.batch_panel.resume_batch_button.setEnabled(False)
        self.statusBar().showMessage(
            "Batch running… (PLAN → tasks → audit → fix loop → READY_FOR_FINAL_AUDIT)"
        )
        thread, worker = start_executor_worker(
            self.controller.executor,
            TaskSpec(title="", prompt=""),
            timeout_s=self._dispatch_timeout_s,
            action="batch",
            resume=resume,
            project_brief=brief,
            batch_size=size,
            parent=self,
        )
        worker.finished.connect(self._on_batch_finished)
        thread.finished.connect(self._on_dispatch_thread_finished)
        self._thread, self._worker = thread, worker
        thread.start()

    def _on_batch_finished(self, report: object) -> None:
        """Terminal batch report: refresh everything, show the honest state."""
        from ..core import BatchRunReport

        self._after_control("", self.controller.machine.phase)
        if isinstance(report, BatchRunReport):
            self.controller.events.info(
                f"Batch run finished — {report.summary()}", source="ui"
            )
            self.statusBar().showMessage(
                f"Batch: {report.outcome.value} — {report.message}"
            )
            if report.outcome.value == "READY_FOR_FINAL_AUDIT":
                self.statusBar().showMessage(
                    "READY FOR FINAL AUDIT — the batch stops here. "
                    "The Final Auditor arrives in a later session."
                )
            return
        if isinstance(report, ExecutionReport):
            self.controller.events.info(
                f"Batch step finished — {report.summary()}", source="ui"
            )
            self.task_panel.show_report(report)
        self.statusBar().showMessage("Batch run finished without a terminal report.")

    def _after_control(self, message: str, phase: PipelinePhase) -> None:
        if message:
            self.controller.events.info(message, source="ui")
        self.batch_panel.refresh()
        self.task_panel.refresh()
        self.history_panel.refresh()
        for panel in self.role_panels.values():
            panel.refresh_session_field()
        self.statusBar().showMessage(f"Phase: {phase.value}")

    # -- dispatch (off the UI thread) --------------------------------------
    def _on_dispatch_requested(self, title: str, prompt: str) -> None:
        executor = self.controller.executor
        if executor is None:
            self.controller.events.error(
                "Dispatch requested, but no executor is attached.", source="ui"
            )
            self.statusBar().showMessage("No executor attached — nothing was dispatched.")
            return
        if executor.is_running:
            self.statusBar().showMessage("A dispatch is already running.")
            return

        self.task_panel.set_busy(True)
        self.controller.events.info(
            f"Dispatching task '{title}' to {executor.role.value} off the UI thread.",
            source="ui",
        )
        self.statusBar().showMessage(f"Dispatching '{title}' — Hermes runs off the UI thread…")

        thread, worker = start_executor_worker(
            executor,
            TaskSpec(title=title, prompt=prompt),
            timeout_s=self._dispatch_timeout_s,
            parent=self,
        )
        worker.finished.connect(self._on_dispatch_finished)
        thread.finished.connect(self._on_dispatch_thread_finished)
        self._thread, self._worker = thread, worker
        thread.start()

    def _on_dispatch_finished(self, report: object) -> None:
        self.task_panel.set_busy(False)
        if isinstance(report, ExecutionReport):
            self.task_panel.show_report(report)
            self.batch_panel.refresh()
            self.history_panel.refresh()
            for panel in self.role_panels.values():
                panel.refresh_session_field()
            self.controller.events.info(f"Dispatch finished — {report.summary()}", source="ui")
            self.statusBar().showMessage(f"Phase: {report.phase.value} — {report.outcome.value}")
            return
        self.task_panel.show_report(None)
        self.statusBar().showMessage("Dispatch finished without a report.")

    # -- audit / fix (off the UI thread) -------------------------------------
    def _on_audit_requested(self) -> None:
        self._run_loop_action("audit", "Task Auditor run")

    def _on_fix_requested(self) -> None:
        self._run_loop_action("fix", "fix run (NEW Builder session)")

    def _run_loop_action(self, action: str, label: str) -> None:
        executor = self.controller.executor
        if executor is None:
            self.controller.events.error(
                "Audit/fix requested, but no executor is attached.", source="ui"
            )
            self.statusBar().showMessage("No executor attached — nothing was dispatched.")
            return
        if executor.is_running:
            self.statusBar().showMessage("An executor action is already running.")
            return
        self.task_panel.set_busy(True)
        self.controller.events.info(
            f"{label} requested — running off the UI thread.",
            source="ui",
        )
        self.statusBar().showMessage(f"{label} — running off the UI thread…")
        thread, worker = start_executor_worker(
            executor,
            TaskSpec(title="", prompt=""),
            timeout_s=self._dispatch_timeout_s,
            action=action,
            parent=self,
        )
        worker.finished.connect(self._on_dispatch_finished)
        thread.finished.connect(self._on_dispatch_thread_finished)
        self._thread, self._worker = thread, worker
        thread.start()

    def _on_dispatch_thread_finished(self) -> None:
        self._thread = None
        self._worker = None

    # -- lifecycle ----------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: ANN001 - Qt signature
        """Never leave a worker thread behind when the window closes."""
        thread = self._thread
        if thread is not None and thread.isRunning():
            self.controller.events.warning(
                "Window closed while a dispatch was running; waiting briefly for the "
                "worker thread to finish.",
                source="ui",
            )
            thread.quit()
            thread.wait(10000)
        super().closeEvent(event)

    # -- test helper --------------------------------------------------------
    def panel_titles(self) -> list[str]:
        """Every group-box title currently shown (used by UI smoke tests)."""
        titles: list[str] = []
        for widget in self.findChildren(QGroupBox):
            titles.append(widget.title())
        return titles