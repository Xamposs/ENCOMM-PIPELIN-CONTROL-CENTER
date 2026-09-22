"""Reusable UI panels.

Every panel is a thin view over :class:`~encomm_pcc.core.controller.PipelineController`.
No panel owns business logic, and no panel starts an engine session.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core import (
    DEFAULT_BATCH_SIZE,
    MAX_BATCH_SIZE,
    MIN_BATCH_SIZE,
    ROLE_SESSION_POLICY_DESCRIPTION,
    ExecutionReport,
    LogRecord,
    PipelineController,
)
from ..domain import AgentRole, PipelinePhase, SessionPolicy, TaskState
from ..drivers import PLANNED_DRIVERS, DriverRegistry

__all__ = [
    "BatchPanel",
    "LogPanel",
    "RolePanel",
    "TaskPanel",
    "WorkspacePanel",
]

#: Fields shown per role, per the project brief.
_ROLE_FIELDS: Mapping[AgentRole, tuple[str, ...]] = {
    AgentRole.ORCHESTRATOR: ("engine", "profile", "session"),
    AgentRole.FINAL_AUDITOR: ("engine", "profile", "session", "same_as"),
    AgentRole.TASK_AUDITOR: ("engine", "profile", "provider", "model", "policy"),
    AgentRole.BUILDER: ("engine", "profile", "provider", "model", "policy"),
}

#: Label for the profile field, per role.
_PROFILE_LABEL: Mapping[AgentRole, str] = {
    AgentRole.ORCHESTRATOR: "Project / Profile",
    AgentRole.FINAL_AUDITOR: "Project / Profile",
    AgentRole.TASK_AUDITOR: "Hermes profile",
    AgentRole.BUILDER: "Hermes profile",
}

def _monospace(widget: QWidget, point_size: int = 9) -> None:
    font = QFont("Consolas")
    font.setStyleHint(QFont.Monospace)
    font.setPointSize(point_size)
    widget.setFont(font)


#: How much engine output the TASK panel renders.  The full text stays in the
#: report and in the (bounded) event payload; the panel shows a working excerpt.
OUTPUT_VIEW_CHARS = 4000


class WorkspacePanel(QGroupBox):
    """WORKSPACE section: name + repository path."""

    changed = Signal(str, str)

    def __init__(self, controller: PipelineController, parent: QWidget | None = None) -> None:
        super().__init__("WORKSPACE", parent)
        self._controller = controller

        self.name_edit = QLineEdit(controller.state.workspace.name)
        self.path_edit = QLineEdit(controller.state.workspace.repo_path)
        self.path_edit.setPlaceholderText(r"C:\path\to\supervised\repository")
        self.path_label = QLabel()
        self.path_label.setWordWrap(True)

        form = QFormLayout(self)
        form.addRow("Workspace name", self.name_edit)
        form.addRow("Repository path", self.path_edit)
        form.addRow("Path status", self.path_label)

        self.name_edit.editingFinished.connect(self._emit_changed)
        self.path_edit.editingFinished.connect(self._emit_changed)
        self.refresh()

    def _emit_changed(self) -> None:
        self.changed.emit(self.name_edit.text(), self.path_edit.text())
        self.refresh()

    def refresh(self) -> None:
        path = self.path_edit.text().strip()
        if not path:
            self.path_label.setText("No repository selected.")
        else:
            from pathlib import Path

            exists = Path(path).expanduser().exists()
            self.path_label.setText("Exists on disk." if exists else "Path does not exist yet.")

    def values(self) -> tuple[str, str]:
        return self.name_edit.text(), self.path_edit.text()


class RolePanel(QGroupBox):
    """One role's configuration block (ORCHESTRATOR / FINAL AUDITOR / TASK AUDITOR / BUILDER)."""

    changed = Signal(object, dict)

    def __init__(
        self,
        role: AgentRole,
        controller: PipelineController,
        registry: DriverRegistry,
        profiles: Sequence[str] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(role.value.replace("_", " "), parent)
        self.role = role
        self._controller = controller
        self._registry = registry
        self._fields = _ROLE_FIELDS[role]
        self._loading = False
        self._profiles: tuple[str, ...] = tuple(profiles)

        form = QFormLayout(self)

        # -- engine ------------------------------------------------------
        self.engine_combo = QComboBox()
        for driver_id in registry.driver_ids():
            caps = registry.capabilities(driver_id)
            suffix = "" if caps.implemented else "  (placeholder)"
            self.engine_combo.addItem(f"{caps.display_name}{suffix}", driver_id)
        self.engine_combo.addItem("(none)", "")
        form.addRow("Engine", self.engine_combo)

        # -- profile ------------------------------------------------------
        self.profile_edit = QLineEdit()
        self.profile_edit.setPlaceholderText("profile / project name")
        if self._profiles:
            completer = QCompleter(list(self._profiles), self.profile_edit)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.setFilterMode(Qt.MatchContains)
            self.profile_edit.setCompleter(completer)
            self.profile_edit.setToolTip(
                "Discovered Hermes profiles: " + ", ".join(self._profiles)
            )
        form.addRow(_PROFILE_LABEL[role], self.profile_edit)

        # -- provider / model ----------------------------------------------
        self.provider_edit: QLineEdit | None = None
        self.model_edit: QLineEdit | None = None
        if "provider" in self._fields:
            self.provider_edit = QLineEdit()
            self.provider_edit.setPlaceholderText("e.g. openrouter")
            form.addRow("Provider", self.provider_edit)
        if "model" in self._fields:
            self.model_edit = QLineEdit()
            self.model_edit.setPlaceholderText("e.g. deepseek/deepseek-v4.1-flash")
            form.addRow("Model", self.model_edit)

        # -- session ----------------------------------------------------------
        self.session_combo: QComboBox | None = None
        self.new_session_button: QPushButton | None = None
        if "session" in self._fields:
            self.session_combo = QComboBox()
            self.session_combo.setEditable(False)
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(self.session_combo, 1)
            if role is AgentRole.ORCHESTRATOR:
                self.new_session_button = QPushButton("New Session")
                self.new_session_button.setToolTip(
                    "Placeholder: records the request only. No engine session is started in v0.1."
                )
                self.new_session_button.clicked.connect(self._on_new_session_clicked)
                row_layout.addWidget(self.new_session_button)
            form.addRow("Session", row)

        # -- same as orchestrator ------------------------------------------------
        self.same_as_check: QCheckBox | None = None
        if "same_as" in self._fields:
            self.same_as_check = QCheckBox("Same as Orchestrator")
            form.addRow("", self.same_as_check)

        # -- session policy label ---------------------------------------------------
        self.policy_label: QLabel | None = None
        if "policy" in self._fields:
            self.policy_label = QLabel()
            form.addRow("Session policy", self.policy_label)

        # -- capability hint -----------------------------------------------------------
        self.capability_label = QLabel()
        self.capability_label.setWordWrap(True)
        self.capability_label.setStyleSheet("color: palette(mid);")
        form.addRow("", self.capability_label)

        self._connect_signals()
        self.refresh_from_controller()

    # -- wiring ----------------------------------------------------------
    def _connect_signals(self) -> None:
        self.engine_combo.currentIndexChanged.connect(self._emit_changed)
        self.profile_edit.editingFinished.connect(self._emit_changed)
        if self.provider_edit is not None:
            self.provider_edit.editingFinished.connect(self._emit_changed)
        if self.model_edit is not None:
            self.model_edit.editingFinished.connect(self._emit_changed)
        if self.same_as_check is not None:
            self.same_as_check.toggled.connect(self._emit_changed)

    def _on_new_session_clicked(self) -> None:
        self._controller.events.info(
            f"New Session requested for {self.role.value} — placeholder only; "
            "no engine session is started in the v0.1 foundation.",
            source="ui",
        )

    def _emit_changed(self, *_args: Any) -> None:
        if self._loading:
            return
        self.changed.emit(self.role, self.values())
        self._update_capability_hint()

    # -- data -------------------------------------------------------------
    def values(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "engine": self.engine_combo.currentData() or "",
            "project_profile": self.profile_edit.text().strip(),
        }
        if self.provider_edit is not None:
            data["provider"] = self.provider_edit.text().strip()
        if self.model_edit is not None:
            data["model"] = self.model_edit.text().strip()
        if self.same_as_check is not None:
            data["same_as_orchestrator"] = self.same_as_check.isChecked()
        return data

    def apply_values(self, data: Mapping[str, Any]) -> None:
        """Push values into the widgets without emitting ``changed``."""
        self._loading = True
        try:
            engine = str(data.get("engine", ""))
            index = self.engine_combo.findData(engine)
            self.engine_combo.setCurrentIndex(index if index >= 0 else self.engine_combo.count() - 1)
            self.profile_edit.setText(str(data.get("project_profile", "")))
            if self.provider_edit is not None:
                self.provider_edit.setText(str(data.get("provider", "")))
            if self.model_edit is not None:
                self.model_edit.setText(str(data.get("model", "")))
            if self.same_as_check is not None:
                self.same_as_check.setChecked(bool(data.get("same_as_orchestrator", False)))
        finally:
            self._loading = False
        self._update_capability_hint()

    def refresh_from_controller(self) -> None:
        """Reload this panel from the controller's current state."""
        config = self._controller.role_config(self.role)
        self.apply_values(config.to_dict())

        if self.policy_label is not None:
            description = ROLE_SESSION_POLICY_DESCRIPTION[self.role]
            self.policy_label.setText(f"{description}  ({config.session_policy.value})")

        if self.session_combo is not None:
            self.session_combo.clear()
            session_id = self._controller.sessions.current_session_id(self.role)
            self.session_combo.addItem(session_id or "(none)", session_id or "")
            if self.same_as_check is not None and self.same_as_check.isChecked():
                self.session_combo.setEnabled(False)
            else:
                self.session_combo.setEnabled(True)

    def _update_capability_hint(self) -> None:
        engine = self.engine_combo.currentData() or ""
        if not engine:
            self.capability_label.setText("No engine selected.")
            return
        caps = self._registry.capabilities(engine)
        state = "placeholder — not implemented" if not caps.implemented else "implemented"
        binary = "binary found on PATH" if self._registry.get_class(engine).probe_availability() else "binary not found on PATH"
        sessions = "sessions supported" if caps.supports_sessions else "stateless engine"
        self.capability_label.setText(f"{state}; {sessions}; {binary}.")

    def refresh_session_field(self) -> None:
        if self.session_combo is None:
            return
        session_id = self._controller.sessions.current_session_id(self.role)
        self.session_combo.clear()
        self.session_combo.addItem(session_id or "(none)", session_id or "")
        if self.same_as_check is not None:
            self.session_combo.setEnabled(not self.same_as_check.isChecked())


class BatchPanel(QGroupBox):
    """BATCH section: size, status, Start / Pause / Stop."""

    start_requested = Signal(int)
    pause_requested = Signal()
    resume_requested = Signal()
    stop_requested = Signal()

    def __init__(self, controller: PipelineController, parent: QWidget | None = None) -> None:
        super().__init__("BATCH", parent)
        self._controller = controller

        self.size_spin = QSpinBox()
        self.size_spin.setRange(MIN_BATCH_SIZE, MAX_BATCH_SIZE)
        self.size_spin.setValue(
            controller.state.batch.size if controller.state.batch else DEFAULT_BATCH_SIZE
        )

        self.status_label = QLabel()
        self.phase_label = QLabel()

        self.start_button = QPushButton("Start")
        self.pause_button = QPushButton("Pause")
        self.resume_button = QPushButton("Resume")
        self.stop_button = QPushButton("Stop")

        self.start_button.clicked.connect(lambda: self.start_requested.emit(self.size_spin.value()))
        self.pause_button.clicked.connect(self.pause_requested.emit)
        self.resume_button.clicked.connect(self.resume_requested.emit)
        self.stop_button.clicked.connect(self.stop_requested.emit)

        form = QFormLayout(self)
        form.addRow("Batch size", self.size_spin)
        form.addRow("Current phase", self.phase_label)
        form.addRow("Current status", self.status_label)

        buttons = QWidget()
        button_row = QHBoxLayout(buttons)
        button_row.setContentsMargins(0, 0, 0, 0)
        for button in (self.start_button, self.pause_button, self.resume_button, self.stop_button):
            button_row.addWidget(button)
        form.addRow("Controls", buttons)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: palette(mid);")
        form.addRow("", self.hint)

        self.refresh()

    def refresh(self) -> None:
        controller = self._controller
        phase = controller.machine.phase
        self.phase_label.setText(phase.value)

        batch = controller.state.batch
        if batch is None:
            self.status_label.setText("No batch yet.")
        else:
            self.status_label.setText(
                f"{batch.status.value} — {batch.batch_id} — {batch.progress_label()}"
            )

        if controller.executor_attached:
            self.hint.setText(
                "Executor attached — Start creates the batch; task dispatch runs "
                "through the executor (see the TASK section)."
            )
        else:
            self.hint.setText(
                "No executor attached — Start records batch state only."
            )

        self.start_button.setEnabled(controller.machine.can_go_to(PipelinePhase.PLANNING_BATCH))
        self.pause_button.setEnabled(controller.machine.can_go_to(PipelinePhase.PAUSED))
        self.resume_button.setEnabled(phase is PipelinePhase.PAUSED)
        self.stop_button.setEnabled(phase is not PipelinePhase.IDLE)


class LogPanel(QGroupBox):
    """LOG PANEL: timestamped local event log."""

    record_received = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("LOG PANEL", parent)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(5000)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        _monospace(self.view)

        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.view.clear)
        self.count_label = QLabel("0 events")

        header = QWidget()
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.addWidget(self.count_label)
        header_row.addStretch(1)
        header_row.addWidget(self.clear_button)

        layout = QVBoxLayout(self)
        layout.addWidget(header)
        layout.addWidget(self.view, 1)

        # Thread-safe: signals marshal the record onto the UI thread.
        self.record_received.connect(self.append_record, Qt.QueuedConnection)
        self._count = 0

    def append_record(self, record: LogRecord) -> None:
        self.view.appendPlainText(record.format_line())
        self._count += 1
        self.count_label.setText(f"{self._count} events")

    def append_lines(self, lines: Iterable[str]) -> None:
        for line in lines:
            self.view.appendPlainText(line)
            self._count += 1
        self.count_label.setText(f"{self._count} events")


class TaskPanel(QGroupBox):
    """TASK section — one controlled task, dispatched through the executor.

    Session 003 keeps the Session 002 dispatch surface and adds the audit/fix
    loop readouts: the deterministic next action (AUDIT / FIX / RE-AUDIT /
    COMPLETE / BLOCKED), the current audit round, the latest verdict, findings
    summary and the real auditor / fix-Builder session ids.  A fix is always
    labelled as running in a NEW Builder session.
    """

    #: title, prompt
    dispatch_requested = Signal(str, str)
    #: run the Task Auditor (initial audit or re-audit via resume)
    audit_requested = Signal()
    #: run a fix in a brand-new Builder session
    fix_requested = Signal()

    DEFAULT_TITLE = "Session 003 controlled task"

    def __init__(
        self,
        controller: PipelineController,
        profiles: Sequence[str] = (),
        profile_method: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("TASK (Session 003 — one controlled audit/fix loop)", parent)
        self._controller = controller
        self._profiles: tuple[str, ...] = tuple(profiles)
        self._profile_method = profile_method
        self._busy = False

        self.driver_label = QLabel()
        self.driver_label.setWordWrap(True)
        self.profiles_label = QLabel()
        self.profiles_label.setWordWrap(True)
        self.profiles_label.setStyleSheet("color: palette(mid);")
        self.profile_label = QLabel()

        self.title_edit = QLineEdit(self.DEFAULT_TITLE)
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText(
            "Implementation prompt handed to the Builder (one task per batch in this session)."
        )
        self.prompt_edit.setMaximumHeight(90)

        self.dispatch_button = QPushButton("Dispatch task")
        self.dispatch_button.clicked.connect(self._emit_dispatch)

        # -- Session 003: audit/fix surface --------------------------------
        self.audit_button = QPushButton("Run Task Auditor")
        self.audit_button.setToolTip(
            "Starts a REAL Task Auditor session (persistent per batch); "
            "a re-audit resumes the same auditor session."
        )
        self.audit_button.clicked.connect(self.audit_requested.emit)
        self.fix_button = QPushButton("Run fix (NEW Builder session)")
        self.fix_button.setToolTip(
            "Runs the fix in a BRAND-NEW Builder session — never the previous "
            "Builder conversation."
        )
        self.fix_button.clicked.connect(self.fix_requested.emit)

        self.next_action_label = QLabel("Idle — no task materialised.")
        self.next_action_label.setWordWrap(True)
        self.audit_info_label = QLabel()
        self.audit_info_label.setWordWrap(True)
        self.audit_info_label.setStyleSheet("color: palette(mid);")

        self.task_state_label = QLabel()
        self.status_label = QLabel("Idle — no task dispatched.")
        self.status_label.setWordWrap(True)
        self.failure_label = QLabel()
        self.failure_label.setWordWrap(True)
        self.failure_label.setStyleSheet("color: palette(bright-text);")

        self.result_view = QPlainTextEdit()
        self.result_view.setReadOnly(True)
        self.result_view.setMaximumHeight(150)
        self.result_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        _monospace(self.result_view, point_size=8)

        form = QFormLayout(self)
        form.addRow("Engine", self.driver_label)
        form.addRow("Profiles", self.profiles_label)
        form.addRow("Builder profile", self.profile_label)
        form.addRow("Task title", self.title_edit)
        form.addRow("Task prompt", self.prompt_edit)
        form.addRow("", self.dispatch_button)
        form.addRow("Next action", self.next_action_label)
        form.addRow("", self.audit_button)
        form.addRow("", self.fix_button)
        form.addRow("Audit loop", self.audit_info_label)
        form.addRow("Task 1 state", self.task_state_label)
        form.addRow("Status", self.status_label)
        form.addRow("Failure", self.failure_label)
        form.addRow("Result", self.result_view)

        self.refresh()

    # -- data -----------------------------------------------------------------
    def values(self) -> tuple[str, str]:
        return self.title_edit.text().strip() or self.DEFAULT_TITLE, self.prompt_edit.toPlainText()

    def set_profiles(self, profiles: Sequence[str], method: str = "") -> None:
        self._profiles = tuple(profiles)
        self._profile_method = method
        self.refresh()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.refresh()

    def _emit_dispatch(self) -> None:
        title, prompt = self.values()
        if not prompt.strip():
            self.status_label.setText("Refused locally: the task prompt is empty.")
            return
        self.dispatch_requested.emit(title, prompt)

    def refresh(self) -> None:
        from ..core.executor import MAX_AUDIT_ROUNDS, TaskNextAction, next_task_action

        controller = self._controller
        engine = controller.state.resolved_engine_for(AgentRole.BUILDER)

        # -- engine / driver availability ---------------------------------
        if engine and controller.registry.is_registered(engine):
            caps = controller.registry.capabilities(engine)
            resolver = getattr(controller.registry.get_class(engine), "resolve_executable", None)
            executable = resolver() if callable(resolver) else None
            where = f"CLI found: {executable}" if executable else "CLI NOT found on PATH"
            state = "implemented" if caps.implemented else "placeholder (refuses real work)"
            self.driver_label.setText(
                f"'{caps.display_name}' ({engine}) — {state}; {where}; "
                f"sessions={caps.supports_sessions}, resume={caps.supports_resume}, "
                f"cancel={caps.supports_cancellation}."
            )
        else:
            self.driver_label.setText(f"No registered engine for BUILDER (configured: '{engine}').")

        # -- profile discovery ---------------------------------------------
        if self._profiles:
            self.profiles_label.setText(
                f"{len(self._profiles)} Hermes profiles discovered via "
                f"{self._profile_method or 'discovery'}: {', '.join(self._profiles)}"
            )
        else:
            self.profiles_label.setText(
                "Hermes profiles not discovered yet (discovery is read-only and advisory)."
            )
        self.profile_label.setText(controller.role_config(AgentRole.BUILDER).project_profile or "(none)")

        # -- task + phase ----------------------------------------------------
        batch = controller.state.batch
        task = batch.tasks[0] if batch is not None and batch.tasks else None
        if task is None:
            self.task_state_label.setText("(no task materialised)")
        else:
            self.task_state_label.setText(
                f"{task.state.value} — {task.task_id} — attempts {task.attempts} — "
                f"audit rounds {task.audit_rounds}/{MAX_AUDIT_ROUNDS}"
            )

        phase = controller.machine.phase
        can_dispatch = (
            controller.executor_attached
            and not self._busy
            and phase in (PipelinePhase.IDLE, PipelinePhase.PLANNING_BATCH)
            and (batch is None or not batch.tasks)
        )
        self.dispatch_button.setEnabled(can_dispatch)

        # -- Session 003: next action + audit loop -------------------------
        action = next_task_action(batch=batch, phase=phase)
        self.next_action_label.setText(
            f"{action.value}  ({phase.value}"
            + (f", batch {batch.status.value}" if batch is not None else "")
            + ")"
        )
        can_audit = (
            controller.executor_attached
            and not self._busy
            and action in (TaskNextAction.AUDIT, TaskNextAction.RE_AUDIT)
        )
        can_fix = (
            controller.executor_attached
            and not self._busy
            and action is TaskNextAction.FIX
        )
        self.audit_button.setEnabled(can_audit)
        self.fix_button.setEnabled(can_fix)
        if self._busy:
            self.dispatch_button.setText("Running…")
            self.audit_button.setText("Running…" if can_audit else "Run Task Auditor")
            self.fix_button.setText("Running…" if can_fix else "Run fix (NEW Builder session)")
        else:
            self.dispatch_button.setText("Dispatch task")
            self.audit_button.setText("Run Task Auditor")
            self.fix_button.setText("Run fix (NEW Builder session)")

        if task is None:
            self.audit_info_label.setText(
                "(no audit yet — dispatch the task first, or prepare one directly)"
            )
        else:
            auditor_sid = (
                task.auditor_session_id
                or controller.sessions.current_session_id(AgentRole.TASK_AUDITOR)
            )
            fix_sid = task.fix_session_id or task.builder_session_id
            summary = self._findings_summary(task)
            self.audit_info_label.setText(
                "verdict: "
                f"{task.latest_verdict or '(none)'} | auditor session: "
                f"{auditor_sid or 'NOT_EXPOSED'} | fix/builder session: "
                f"{fix_sid or 'NOT_EXPOSED'}"
                + (f" | findings: {summary}" if summary else "")
            )
            if task.state is TaskState.FIX_REQUIRED and task.fix_prompt:
                self.fix_button.setToolTip(
                    "Runs the fix in a BRAND-NEW Builder session — never the previous "
                    "Builder conversation.\nFix prompt: "
                    + task.fix_prompt[:120].replace("\n", " ")
                    + ("…" if len(task.fix_prompt) > 120 else "")
                )

    @staticmethod
    def _findings_summary(task: Any) -> str:
        """One-line, bounded rendering of the latest verdict's findings."""
        if not task.verdict_json:
            return ""
        try:
            import json as _json

            payload = _json.loads(task.verdict_json)
        except (ValueError, TypeError):
            return "(unparseable stored verdict)"
        findings = payload.get("findings") or []
        if not findings:
            return ""
        counts: dict[str, int] = {}
        for finding in findings:
            severity = str(finding.get("severity") or "unknown")
            counts[severity] = counts.get(severity, 0) + 1
        parts = [f"{sev}={count}" for sev, count in sorted(counts.items())]
        return ", ".join(parts) if parts else ""

    # -- results --------------------------------------------------------------
    def show_report(self, report: ExecutionReport | None) -> None:
        """Display the executor's real report — never a fabricated success."""
        if report is None:
            self.status_label.setText("No report was returned by the worker.")
            return
        lines = [
            f"outcome        : {report.outcome.value}",
            f"phase          : {report.phase.value}",
            f"message        : {report.message}",
        ]
        result = report.prompt_result
        if result is not None:
            lines += [
                f"ok             : {result.ok}",
                f"exit_code      : {result.exit_code}",
                f"session_id     : {report.session_id or 'NOT_EXPOSED'}",
                f"duration_s     : {result.duration_s:.2f}",
                f"simulated      : {result.simulated}",
            ]
            if result.metadata.get("argv"):
                lines.append(f"argv           : {result.metadata['argv']}")
            text = result.text or ""
            lines.append("")
            lines.append("--- output ---")
            lines.append(text[:OUTPUT_VIEW_CHARS] if text else "(no output)")
            if len(text) > OUTPUT_VIEW_CHARS:
                lines.append(f"... [{len(text) - OUTPUT_VIEW_CHARS} more characters]")
        verdict = report.audit_verdict
        if verdict is not None:
            lines.append("")
            lines.append("--- strict audit verdict ---")
            lines.append(f"verdict        : {verdict.verdict.value}")
            lines.append(f"summary        : {verdict.summary}")
            lines.append(f"findings       : {len(verdict.findings)}")
            lines.append(f"fix_prompt     : {len(verdict.fix_prompt)} chars")
            for finding in verdict.findings[:5]:
                lines.append(f"  [{finding.severity.value}] {finding.message}")
        self.result_view.setPlainText("\n".join(lines))
        self.failure_label.setText(
            (result.error if result is not None and result.error else "")
            or ("(none)" if report.ok else "failure reported without an error string")
        )
        self.status_label.setText(
            f"{report.outcome.value} — task {report.task_id or '(none)'} — "
            f"executor_started={report.executor_started}"
        )
        self.refresh()
