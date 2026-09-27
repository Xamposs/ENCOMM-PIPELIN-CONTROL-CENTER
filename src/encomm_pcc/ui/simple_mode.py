"""SIMPLE MODE — the daily-use operator facade (Session 009).

One screen, per the brief (§2, §7, §8): PROJECT → GOAL → ARCHITECT → CODER →
AUDITOR → tasks-per-batch → CONTINUOUS RUN → START/PAUSE/STOP → STATUS, and an
``Advanced / Details`` escape hatch to the existing full window.

Design invariants:

* **One source of truth (§34).**  Every control writes straight into the SAME
  durable role configs through ``PipelineController`` — no second config
  model.  ARCHITECT maps onto the existing ORCHESTRATOR + FINAL_AUDITOR roles
  (``same_as_orchestrator``), so ONE Codex thread serves planning and every
  Final Audit (§6).
* **Session semantics stay proven (§16, §17).**  The Coder is ALWAYS a fresh
  session per task/fix (``always_new``); the Auditor is one persistent
  session per batch (``persistent_per_batch``), optionally bound to an
  existing discovered session for the CURRENT batch.
* **Power-loss safety (§20, §21, §22).**  Construction never runs an AI
  operation; when durable state shows interrupted work, the panel shows
  RECOVERY and waits for CONTINUE.
* **Batch work never runs on the UI thread** — everything goes through
  ``start_executor_worker`` exactly like the advanced window.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Sequence

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.controller import PipelineController
from ..domain import AgentRole

if TYPE_CHECKING:  # pragma: no cover
    from .main_window import MainWindow

__all__ = ["SimpleModePanel"]


class SimpleModePanel(QWidget):
    """The default operator screen."""

    def __init__(
        self,
        controller: PipelineController,
        profiles: Sequence[str] = (),
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self._profiles: tuple[str, ...] = tuple(profiles)
        # Set up by MainWindow: callables receiving no arguments.
        self.request_start: Any = None
        self.request_continue: Any = None
        self.request_pause: Any = None
        self.request_stop: Any = None

        layout = QVBoxLayout(self)

        # -- PROJECT --------------------------------------------------------
        project_box = QGroupBox("PROJECT")
        project_layout = QHBoxLayout(project_box)
        self.repo_edit = QLineEdit()
        self.repo_edit.setPlaceholderText(r"C:\projects\my-project")
        repo = self.controller.state.workspace.repo_path or ""
        self.repo_edit.setText(repo)
        browse = QPushButton("Browse")
        browse.clicked.connect(self._on_browse)
        project_layout.addWidget(QLabel("Repository:"))
        project_layout.addWidget(self.repo_edit, 1)
        project_layout.addWidget(browse)
        self.repo_edit.editingFinished.connect(self._on_repo_changed)
        layout.addWidget(project_box)

        # -- GOAL -------------------------------------------------------------
        goal_box = QGroupBox("GOAL")
        goal_layout = QVBoxLayout(goal_box)
        self.goal_edit = QPlainTextEdit()
        self.goal_edit.setPlaceholderText("What do you want the agents to build?")
        self.goal_edit.setFixedHeight(72)
        batch = self.controller.state.batch
        if batch is not None and batch.project_brief:
            self.goal_edit.setPlainText(batch.project_brief)
        goal_layout.addWidget(self.goal_edit)
        layout.addWidget(goal_box)

        # -- ARCHITECT (Codex) ------------------------------------------------
        arch_box = QGroupBox("ARCHITECT")
        arch_form = QHBoxLayout(arch_box)
        arch_form.addWidget(QLabel("Engine:"))
        self.architect_engine_label = QLabel("Codex")
        arch_form.addWidget(self.architect_engine_label)
        arch_form.addWidget(QLabel("Session:"))
        self.architect_session = QComboBox()
        self.architect_session.addItem("New session (next run creates one)", "")
        arch_refresh = QPushButton("Refresh")
        arch_refresh.clicked.connect(self._on_refresh_architect_sessions)
        arch_form.addWidget(self.architect_session, 1)
        arch_form.addWidget(arch_refresh)
        self.architect_session.activated.connect(self._on_architect_session_selected)
        layout.addWidget(arch_box)

        # -- CODER (Hermes, always fresh) --------------------------------------
        coder_box = QGroupBox("CODER")
        coder_form = QHBoxLayout(coder_box)
        coder_form.addWidget(QLabel("Engine:"))
        coder_form.addWidget(QLabel("Hermes"))
        coder_form.addWidget(QLabel("Profile:"))
        self.coder_profile = self._make_profile_combo()
        coder_form.addWidget(self.coder_profile, 1)
        coder_hint = QLabel("Session mode: Automatic — NEW session per task/fix")
        coder_hint.setStyleSheet("color: palette(mid);")
        coder_form.addWidget(coder_hint)
        self.coder_profile.activated.connect(
            lambda _i: self._apply_role_profile(AgentRole.BUILDER, self.coder_profile)
        )
        layout.addWidget(coder_box)

        # -- AUDITOR (Hermes, persistent per batch) -----------------------------
        auditor_box = QGroupBox("AUDITOR")
        auditor_form = QHBoxLayout(auditor_box)
        auditor_form.addWidget(QLabel("Engine:"))
        auditor_form.addWidget(QLabel("Hermes"))
        auditor_form.addWidget(QLabel("Profile:"))
        self.auditor_profile = self._make_profile_combo()
        auditor_form.addWidget(self.auditor_profile, 1)
        auditor_form.addWidget(QLabel("Session:"))
        self.auditor_session = QComboBox()
        self.auditor_session.addItem("New session for this batch", "")
        auditor_refresh = QPushButton("Refresh")
        auditor_refresh.clicked.connect(self._on_refresh_auditor_sessions)
        auditor_form.addWidget(self.auditor_session, 1)
        auditor_form.addWidget(auditor_refresh)
        self.auditor_session.activated.connect(self._on_auditor_session_selected)
        self.auditor_profile.activated.connect(
            lambda _i: self._apply_role_profile(AgentRole.TASK_AUDITOR, self.auditor_profile)
        )
        layout.addWidget(auditor_box)

        # -- run controls ---------------------------------------------------
        run_box = QGroupBox("RUN")
        run_form = QHBoxLayout(run_box)
        run_form.addWidget(QLabel("Tasks per batch:"))
        self.batch_size = QSpinBox()
        self.batch_size.setRange(1, 5)
        self.batch_size.setValue(5)
        run_form.addWidget(self.batch_size)
        self.continuous_check = QCheckBox("Continuous Run")
        self.continuous_check.setToolTip(
            "Opt-in: after each Final PASS the next batch is materialised from the "
            "already-returned plan (ZERO planning calls) and run automatically, "
            "until you press STOP."
        )
        run_form.addWidget(self.continuous_check)
        self.start_button = QPushButton("START")
        self.pause_button = QPushButton("PAUSE")
        self.stop_button = QPushButton("STOP")
        self.start_button.clicked.connect(self._on_start)
        self.pause_button.clicked.connect(self._on_pause)
        self.stop_button.clicked.connect(self._on_stop)
        run_form.addWidget(self.start_button)
        run_form.addWidget(self.pause_button)
        run_form.addWidget(self.stop_button)
        layout.addWidget(run_box)

        # -- recovery (§20–§22) ------------------------------------------------
        self.recovery_box = QGroupBox("RECOVERY")
        recovery_layout = QHBoxLayout(self.recovery_box)
        self.recovery_label = QLabel("")
        self.recovery_label.setWordWrap(True)
        self.continue_button = QPushButton("CONTINUE")
        self.continue_button.clicked.connect(self._on_continue)
        recovery_layout.addWidget(self.recovery_label, 1)
        recovery_layout.addWidget(self.continue_button)
        layout.addWidget(self.recovery_box)

        # -- STATUS ----------------------------------------------------------
        status_box = QGroupBox("STATUS")
        status_layout = QVBoxLayout(status_box)
        self.status_label = QLabel("Idle.")
        self.task_table = QTableWidget(0, 2)
        self.task_table.setHorizontalHeaderLabels(["Task", "State"])
        self.task_table.verticalHeader().setVisible(False)
        self.task_table.setEditTriggers(self.task_table.EditTrigger.NoEditTriggers)
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.task_table)
        layout.addWidget(status_box, 1)

        advanced = QPushButton("Advanced / Details")
        advanced.clicked.connect(self._on_advanced)
        layout.addWidget(advanced)

        self._sync_from_controller()
        self._refresh_recovery()

    # -- setup helpers -------------------------------------------------------
    def _make_profile_combo(self) -> QComboBox:
        combo = QComboBox()
        if self._profiles:
            for name in self._profiles:
                combo.addItem(name)
        else:
            combo.setEditable(True)
        return combo

    # -- config → controller (§34) ------------------------------------------
    def _apply_role_profile(self, role: AgentRole, combo: QComboBox) -> None:
        profile = combo.currentText().strip()
        engine = "hermes"
        self.controller.set_role_config(role, engine=engine, project_profile=profile)

    def _on_repo_changed(self) -> None:
        path = self.repo_edit.text().strip()
        if not path:
            return
        from pathlib import Path as _P

        name = _P(path).name or path
        self.controller.set_workspace(name, path)

    def _on_browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Select repository")
        if not chosen:
            return
        self.repo_edit.setText(chosen)
        self._on_repo_changed()

    # -- architect session (§6: ONE Codex thread for planning + final audit) --
    def _on_refresh_architect_sessions(self) -> None:
        self._refresh_sessions(AgentRole.ORCHESTRATOR, self.architect_session)

    def _on_refresh_auditor_sessions(self) -> None:
        self._refresh_sessions(AgentRole.TASK_AUDITOR, self.auditor_session)

    def _refresh_sessions(self, role: AgentRole, combo: QComboBox) -> None:
        engine = self.controller.state.resolved_engine_for(role)
        driver = None
        if engine and self.controller.registry.is_registered(engine):
            driver = self.controller.registry.create(engine)
        discoverer = getattr(driver, "discover_sessions", None) if driver else None
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem("New session (next run creates one)", "")
        finally:
            combo.blockSignals(False)
        if not callable(discoverer):
            return
        profile = str(self.controller.state.config_for(role).project_profile or "").strip()
        result: Any
        try:
            result = discoverer(workspace_path=None, profile=profile)
        except TypeError:
            result = discoverer(workspace_path=None)
        except Exception:  # noqa: BLE001 - discovery must never break the UI
            return
        if result is None or not result.ok:
            return
        combo.blockSignals(True)
        try:
            for descriptor in result.sessions:
                combo.addItem(descriptor.label(70), descriptor.session_id)
        finally:
            combo.blockSignals(False)

    def _on_architect_session_selected(self, index: int) -> None:
        self._bind_selected(AgentRole.ORCHESTRATOR, self.architect_session, index)

    def _on_auditor_session_selected(self, index: int) -> None:
        self._bind_selected(AgentRole.TASK_AUDITOR, self.auditor_session, index)

    def _bind_selected(self, role: AgentRole, combo: QComboBox, index: int) -> None:
        session_id = str(combo.itemData(index) or "")
        engine = self.controller.state.resolved_engine_for(role) or (
            "codex" if role is AgentRole.ORCHESTRATOR else "hermes"
        )
        if not session_id:
            self.controller.clear_external_session(role)
            return
        profile = str(self.controller.state.config_for(role).project_profile or "").strip()
        self.controller.bind_external_session(
            role, engine, session_id, profile=profile or None
        )

    # -- run controls -----------------------------------------------------
    def _on_start(self) -> None:
        self._apply_goal()
        if self.request_start is not None:
            self.request_start(self.goal_text(), self.batch_size.value(), self.continuous_check.isChecked())

    def _on_continue(self) -> None:
        if self.request_continue is not None:
            self.request_continue(self.continuous_check.isChecked())

    def _on_pause(self) -> None:
        if self.request_pause is not None:
            self.request_pause()

    def _on_stop(self) -> None:
        if self.request_stop is not None:
            self.request_stop()

    def _on_advanced(self) -> None:
        if self.request_advanced is not None:
            self.request_advanced()

    # -- goal/brief ---------------------------------------------------------
    def goal_text(self) -> str:
        return self.goal_edit.toPlainText().strip()

    def _apply_goal(self) -> None:
        brief = self.goal_text()
        if brief and self.controller.state.batch is not None:
            self.controller.state.batch.project_brief = brief

    # -- status refresh ----------------------------------------------------
    def _sync_from_controller(self) -> None:
        """Push persisted role state into the Simple controls (§34)."""
        state = self.controller.state
        orchestrator = state.config_for(AgentRole.ORCHESTRATOR)
        if orchestrator.engine:
            self.architect_engine_label.setText(orchestrator.engine)
        coder = state.config_for(AgentRole.BUILDER)
        if coder.project_profile:
            self._select_combo_text(self.coder_profile, coder.project_profile)
        auditor = state.config_for(AgentRole.TASK_AUDITOR)
        if auditor.project_profile:
            self._select_combo_text(self.auditor_profile, auditor.project_profile)
        if state.batch is not None:
            self.goal_edit.setPlainText(state.batch.project_brief or "")

    @staticmethod
    def _select_combo_text(combo: QComboBox, value: str) -> None:
        index = combo.findText(value)
        if index >= 0:
            combo.setCurrentIndex(index)
        elif combo.isEditable():
            combo.setEditText(value)

    def _refresh_recovery(self) -> None:
        """Show RECOVERY only when durable state holds interrupted work (§22)."""
        state = self.controller.state
        batch = state.batch
        active = False
        if batch is not None:
            phase = state.phase
            active = phase.value not in {
                "IDLE",
                "BATCH_COMPLETE",
                "FAILED",
                "BLOCKED",
            }
        self.recovery_box.setVisible(active)
        if active and batch is not None:
            current = next(
                (t for t in batch.tasks if t.state.value not in {"APPROVED", "PENDING"}),
                None,
            )
            done = sum(1 for t in batch.tasks if t.state.value == "APPROVED")
            self.recovery_label.setText(
                f"RECOVERY AVAILABLE — batch in progress ({done}/{batch.size} tasks "
                f"approved, phase {state.phase.value}). No AI operation has been "
                "started. Press CONTINUE to resume from durable state."
                + (f" Interrupted task: {current.title}" if current is not None else "")
            )

    def refresh(self) -> None:
        """Re-read controller state into the status widgets."""
        self._refresh_recovery()
        state = self.controller.state
        batch = state.batch
        if batch is None:
            self.status_label.setText("Idle.")
            self.task_table.setRowCount(0)
            return
        approved = sum(1 for t in batch.tasks if t.state.value == "APPROVED")
        self.status_label.setText(
            f"Batch {batch.batch_id} — {approved}/{batch.size} tasks approved — "
            f"phase {state.phase.value}"
        )
        rows = [
            (t.title or f"Task {t.index}", t.state.value)
            for t in sorted(batch.tasks, key=lambda t: t.index)
        ]
        self.task_table.setRowCount(len(rows))
        for row, (title, status) in enumerate(rows):
            self.task_table.setItem(row, 0, QTableWidgetItem(str(title)))
            self.task_table.setItem(row, 1, QTableWidgetItem(str(status)))

    # -- wired by MainWindow ------------------------------------------------
    def request_advanced(self) -> None:  # pragma: no cover - replaced by window
        pass

    def show_message(self, text: str) -> None:
        self.statusBar_message = text

    def confirm_stop(self) -> bool:  # pragma: no cover - future use
        return (
            QMessageBox.question(self, "Stop", "Stop after the current operation?")
            == QMessageBox.StandardButton.Yes
        )
