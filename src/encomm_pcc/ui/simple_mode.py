"""SIMPLE MODE — the daily-use operator facade (Session 009, corrected Session 011).

One screen: PROJECT → GOAL → ARCHITECT → CODER → AUDITOR → tasks-per-batch →
CONTINUOUS RUN → START/PAUSE/STOP → STATUS, plus the RECOVERY affordance.

Session 011 (production UI finalisation) adds the missing **real engine
selector** per role, engine-specific field layout, an explicit **Coder session
mode** (Automatic — NEW session per task/fix, or Resume selected session
ONCE through the proven one-shot recovery override), profile-scoped
auto-refresh, an operator-friendly status panel, and removes the
``Advanced / Details`` button from the normal production launch (it returns
behind the development-only ``--debug-ui`` flag).

Design invariants:

* **One source of truth (§34).**  Every control writes straight into the SAME
  durable role configs through ``PipelineController`` — no second config
  model.  ARCHITECT maps onto the existing ORCHESTRATOR + FINAL_AUDITOR roles
  (``same_as_orchestrator``): ONE thread serves planning and every Final
  Audit (§8/§9 — the executor resolves that shared thread, this panel
  binds/clears the Architect binding the same way).
* **Engines come from the real registry.**  The engine dropdowns are
  populated from ``DriverRegistry.driver_ids()`` (implemented engines only);
  the field layout reacts to the selected engine (Hermes → profile/provider/
  model/session; Codex → session; stateless engines → none).
* **Session semantics stay proven (§16, §17).**  The Coder is ALWAYS a fresh
  session per task/fix (``always_new``); the Auditor is one persistent
  session per batch (``persistent_per_batch``), optionally bound to an
  existing discovered session; the Coder *Resume selected session ONCE* mode
  arms the existing one-shot recovery override (D-050) — never a durable
  policy change.
* **Power-loss safety (§20, §21, §22).**  Construction never runs an AI
  operation; when durable state shows interrupted work, the panel shows
  RECOVERY and waits for CONTINUE.
* **Session discovery stays read-only.**  Refresh lists existing sessions per
  the selected profile; zero model calls, zero transcripts, zero credentials.
* **Batch work never runs on the UI thread** — everything goes through
  ``start_executor_worker`` exactly like the advanced window.
"""

from __future__ import annotations

from dataclasses import dataclass
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

#: Engine → which field rows are meaningful for that engine.  Session
#: capability (``supports_sessions``) additionally gates the session row.
#: This is the engine-specific layout the brief describes (§5); the engine
#: list itself still comes from the real DriverRegistry, never hardcoded.
_ENGINE_FIELD_LAYOUT: dict[str, tuple[str, ...]] = {
    "hermes": ("profile", "provider", "model", "session"),
    "codex": ("session",),
    "generic_cli": (),
}

_AUTOMATIC_SESSION_MODE = "Automatic — NEW session per task/fix"
_RESUME_ONCE_SESSION_MODE = "Resume selected session ONCE"


@dataclass(slots=True)
class _RoleFields:
    """Widget group for one role block, engine-driven visibility included."""

    engine: QComboBox
    profile: QComboBox | None = None
    provider: QLineEdit | None = None
    model: QLineEdit | None = None
    profile_row: QWidget | None = None  # Profile + Provider + Model (Hermes)
    session_row: QWidget | None = None  # Session combo + Refresh
    session: QComboBox | None = None
    session_refresh: QPushButton | None = None
    hint: QLabel | None = None

    def apply_engine(self, engine: str, supports_sessions: bool) -> None:
        """Show/hide the engine-specific rows for ``engine``."""
        layout = _ENGINE_FIELD_LAYOUT.get(engine, ())
        profile_visible = "profile" in layout
        session_visible = "session" in layout and supports_sessions
        if self.profile_row is not None:
            self.profile_row.setVisible(profile_visible)
        if self.session_row is not None:
            self.session_row.setVisible(session_visible)
        if self.hint is not None:
            self.hint.setVisible(not session_visible and not profile_visible)


class SimpleModePanel(QWidget):
    """The default operator screen."""

    def __init__(
        self,
        controller: PipelineController,
        profiles: Sequence[str] = (),
        *,
        parent: QWidget | None = None,
        debug_ui: bool = False,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self._profiles: tuple[str, ...] = tuple(profiles)
        #: Session 011 (§17): the Advanced / Details escape hatch is a
        #: development tool — created only behind ``--debug-ui``.
        self.debug_ui = bool(debug_ui)
        # Set up by MainWindow: callables receiving no arguments.
        self.request_start: Any = None
        self.request_continue: Any = None
        self.request_pause: Any = None
        self.request_stop: Any = None
        self.request_advanced: Any = None
        #: Set by MainWindow while a Continuous Run worker is active (§19).
        self.continuous_active = False

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

        # -- role blocks -----------------------------------------------------
        self._arch_fields = self._build_architect_block(layout)
        self._coder_fields = self._build_coder_block(layout)
        self._auditor_fields = self._build_auditor_block(layout)
        self._role_fields: dict[AgentRole, _RoleFields] = {
            AgentRole.ORCHESTRATOR: self._arch_fields,
            AgentRole.BUILDER: self._coder_fields,
            AgentRole.TASK_AUDITOR: self._auditor_fields,
        }

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
        self.status_label = QLabel("Idle — configure the pipeline and press START.")
        self.status_label.setWordWrap(True)
        self.task_table = QTableWidget(0, 2)
        self.task_table.setHorizontalHeaderLabels(["Task", "State"])
        self.task_table.verticalHeader().setVisible(False)
        self.task_table.setEditTriggers(self.task_table.EditTrigger.NoEditTriggers)
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.task_table)
        layout.addWidget(status_box, 1)

        # Session 011 (§17): the Advanced / Details button exists only behind
        # --debug-ui; the normal packaged launch is production-only.
        if self.debug_ui:
            advanced = QPushButton("Advanced / Details")
            advanced.clicked.connect(self._on_advanced)
            layout.addWidget(advanced)

        # -- Coder recovery override (Session 010, §14/§15) --------------------
        # One-shot: applies ONLY to the current interrupted task/fix, then the
        # automatic NEW-session-per-task policy resumes.  Hidden unless the
        # executor reports a pending override (armed by the recovery handler).
        self.coder_recovery_box = QGroupBox("CODER RECOVERY OVERRIDE (one-shot)")
        coder_recovery_form = QHBoxLayout(self.coder_recovery_box)
        coder_recovery_form.addWidget(
            QLabel("Interrupted Coder session:")
        )
        self.coder_recovery_session = QComboBox()
        coder_recovery_form.addWidget(self.coder_recovery_session, 1)
        coder_recovery_refresh = QPushButton("Refresh")
        coder_recovery_refresh.clicked.connect(self._on_refresh_coder_recovery_sessions)
        coder_recovery_form.addWidget(coder_recovery_refresh)
        self.coder_recovery_apply = QPushButton("Resume saved session")
        self.coder_recovery_apply.clicked.connect(self._on_apply_coder_recovery)
        coder_recovery_form.addWidget(self.coder_recovery_apply)
        self.coder_recovery_clear = QPushButton("Restart in NEW session")
        self.coder_recovery_clear.clicked.connect(self._on_clear_coder_recovery)
        coder_recovery_form.addWidget(self.coder_recovery_clear)
        coder_recovery_hint = QLabel(
            "Applies to the CURRENT task/fix only — afterwards new sessions resume as normal."
        )
        coder_recovery_hint.setStyleSheet("color: palette(mid);")
        coder_recovery_hint.setWordWrap(True)
        coder_recovery_form.addWidget(coder_recovery_hint, 1)
        self.coder_recovery_box.setVisible(False)
        layout.addWidget(self.coder_recovery_box)

        self._sync_from_controller()
        self._refresh_recovery()

    # -- role block builders -------------------------------------------------
    def _build_architect_block(self, layout: QVBoxLayout) -> _RoleFields:
        box = QGroupBox("ARCHITECT")
        form = QVBoxLayout(box)
        engine_row = QHBoxLayout()
        engine_row.addWidget(QLabel("Engine:"))
        self.architect_engine = self._make_engine_combo()
        engine_row.addWidget(self.architect_engine, 1)
        form.addLayout(engine_row)

        profile_combo = self._make_profile_combo()
        provider_edit, model_edit = self._make_provider_model_edits()
        profile_row = self._make_profile_row(
            "Profile:", profile_combo, provider_edit, model_edit
        )
        form.addWidget(profile_row)

        session_row, session, refresh = self._make_session_row(
            "Session:", "New session (next run creates one)", self._on_refresh_architect_sessions
        )
        form.addWidget(session_row)

        hint = QLabel("This engine is stateless — no profile or session applies.")
        hint.setStyleSheet("color: palette(mid);")
        hint.setWordWrap(True)
        form.addWidget(hint)

        box.setLayout(form)
        layout.addWidget(box)

        fields = _RoleFields(
            engine=self.architect_engine,
            profile=profile_combo,
            provider=provider_edit,
            model=model_edit,
            profile_row=profile_row,
            session_row=session_row,
            session=session,
            session_refresh=refresh,
            hint=hint,
        )
        self.architect_engine.activated.connect(
            lambda _i: self._on_engine_selected(AgentRole.ORCHESTRATOR, fields)
        )
        session.activated.connect(self._on_architect_session_selected)
        # Backward-compatible attribute aliases (Session 010 tests/API).
        self.architect_session = session
        return fields

    def _build_coder_block(self, layout: QVBoxLayout) -> _RoleFields:
        box = QGroupBox("CODER")
        form = QVBoxLayout(box)
        engine_row = QHBoxLayout()
        engine_row.addWidget(QLabel("Engine:"))
        self.coder_engine = self._make_engine_combo()
        engine_row.addWidget(self.coder_engine, 1)
        form.addLayout(engine_row)

        # Session 010 (§10): Provider + Model ride the SAME durable
        # AgentRoleConfig fields — no parallel configuration.
        self.coder_profile = self._make_profile_combo()
        self.coder_provider, self.coder_model = self._make_provider_model_edits()
        profile_row = self._make_profile_row(
            "Profile:", self.coder_profile, self.coder_provider, self.coder_model
        )
        form.addWidget(profile_row)

        # Session 011 (§10): explicit session mode — Automatic (default) or
        # Resume selected session ONCE (the proven one-shot override, D-050).
        mode_row = QWidget()
        mode_form = QHBoxLayout(mode_row)
        mode_form.addWidget(QLabel("Session mode:"))
        self.coder_session_mode = QComboBox()
        self.coder_session_mode.addItem(_AUTOMATIC_SESSION_MODE, "automatic")
        self.coder_session_mode.addItem(_RESUME_ONCE_SESSION_MODE, "resume_once")
        self.coder_session_mode.setToolTip(
            "Automatic: every task/fix runs in a brand-new session (the normal "
            "policy). Resume selected session ONCE: the session you pick below is "
            "resumed for exactly the next Builder operation, then the automatic "
            "policy returns."
        )
        mode_form.addWidget(self.coder_session_mode, 1)
        form.addWidget(mode_row)

        # The one-shot resume selector — visible only in Resume mode (§10/§11).
        session_row, session, refresh = self._make_session_row(
            "Session:", "Select a session to resume once…", self._on_refresh_coder_sessions
        )
        session_row.setVisible(False)
        form.addWidget(session_row)

        self._coder_mode_row = mode_row

        hint = QLabel("This engine is stateless — no profile or session applies.")
        hint.setStyleSheet("color: palette(mid);")
        hint.setWordWrap(True)
        form.addWidget(hint)

        box.setLayout(form)
        layout.addWidget(box)

        fields = _RoleFields(
            engine=self.coder_engine,
            profile=self.coder_profile,
            provider=self.coder_provider,
            model=self.coder_model,
            profile_row=profile_row,
            session_row=session_row,
            session=session,
            session_refresh=refresh,
            hint=hint,
        )
        self.coder_engine.activated.connect(
            lambda _i: self._on_engine_selected(AgentRole.BUILDER, fields)
        )
        self.coder_session_mode.activated.connect(self._on_coder_session_mode_changed)
        # Coder profile change → durable config + auto-refresh (§15) / stale
        # binding invalidation (§11).
        self.coder_profile.activated.connect(
            lambda _i: self._on_profile_changed(AgentRole.BUILDER, fields)
        )
        self.coder_provider.editingFinished.connect(self._apply_coder_config)
        self.coder_model.editingFinished.connect(self._apply_coder_config)
        session.activated.connect(self._on_coder_resume_session_selected)
        # Backward-compatible attribute aliases (Session 010 tests/API).
        self.coder_session = session
        return fields

    def _build_auditor_block(self, layout: QVBoxLayout) -> _RoleFields:
        box = QGroupBox("AUDITOR")
        form = QVBoxLayout(box)
        engine_row = QHBoxLayout()
        engine_row.addWidget(QLabel("Engine:"))
        self.auditor_engine = self._make_engine_combo()
        engine_row.addWidget(self.auditor_engine, 1)
        form.addLayout(engine_row)

        # Session 010 (§11): Provider + Model on the SAME durable config.
        self.auditor_profile = self._make_profile_combo()
        self.auditor_provider, self.auditor_model = self._make_provider_model_edits()
        profile_row = self._make_profile_row(
            "Profile:", self.auditor_profile, self.auditor_provider, self.auditor_model
        )
        form.addWidget(profile_row)

        session_row, session, refresh = self._make_session_row(
            "Session:", "New session for this batch", self._on_refresh_auditor_sessions
        )
        form.addWidget(session_row)

        hint = QLabel("This engine is stateless — no profile or session applies.")
        hint.setStyleSheet("color: palette(mid);")
        hint.setWordWrap(True)
        form.addWidget(hint)

        box.setLayout(form)
        layout.addWidget(box)

        fields = _RoleFields(
            engine=self.auditor_engine,
            profile=self.auditor_profile,
            provider=self.auditor_provider,
            model=self.auditor_model,
            profile_row=profile_row,
            session_row=session_row,
            session=session,
            session_refresh=refresh,
            hint=hint,
        )
        self.auditor_engine.activated.connect(
            lambda _i: self._on_engine_selected(AgentRole.TASK_AUDITOR, fields)
        )
        self.auditor_profile.activated.connect(
            lambda _i: self._on_profile_changed(AgentRole.TASK_AUDITOR, fields)
        )
        self.auditor_provider.editingFinished.connect(self._apply_auditor_config)
        self.auditor_model.editingFinished.connect(self._apply_auditor_config)
        session.activated.connect(self._on_auditor_session_selected)
        # Backward-compatible attribute aliases (Session 010 tests/API).
        self.auditor_session = session
        return fields

    # -- setup helpers -------------------------------------------------------
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

    def _make_engine_combo(self) -> QComboBox:
        """Engine dropdown from the REAL DriverRegistry (implemented only)."""
        combo = QComboBox()
        for driver_id in self.controller.registry.driver_ids():
            combo.addItem(self.controller.registry.display_name(driver_id), driver_id)
        if combo.count() == 0:  # pragma: no cover - a registry with no drivers
            combo.setEditable(True)
        return combo

    def _make_profile_combo(self) -> QComboBox:
        combo = QComboBox()
        if self._profiles:
            for name in self._profiles:
                combo.addItem(name)
        else:
            combo.setEditable(True)
        return combo

    def _make_provider_model_edits(self) -> tuple[QLineEdit, QLineEdit]:
        provider_edit = QLineEdit()
        provider_edit.setPlaceholderText("e.g. openrouter")
        provider_edit.setMaximumWidth(120)
        model_edit = QLineEdit()
        model_edit.setPlaceholderText("e.g. deepseek/deepseek-v4.1-flash")
        model_edit.setMaximumWidth(180)
        return provider_edit, model_edit

    @staticmethod
    def _make_profile_row(
        label: str,
        combo: QComboBox,
        provider_edit: QLineEdit,
        model_edit: QLineEdit,
    ) -> QWidget:
        """Profile + Provider + Model row (Hermes layout)."""
        row = QWidget()
        form = QHBoxLayout(row)
        form.addWidget(QLabel(label))
        form.addWidget(combo, 1)
        form.addWidget(QLabel("Provider:"))
        form.addWidget(provider_edit)
        form.addWidget(QLabel("Model:"))
        form.addWidget(model_edit)
        return row

    def _make_session_row(
        self,
        label: str,
        placeholder: str,
        on_refresh: Any,
    ) -> tuple[QWidget, QComboBox, QPushButton]:
        row = QWidget()
        form = QHBoxLayout(row)
        form.addWidget(QLabel(label))
        session = QComboBox()
        session.addItem(placeholder, "")
        form.addWidget(session, 1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(on_refresh)
        form.addWidget(refresh)
        return row, session, refresh

    # -- config → controller (§34) ------------------------------------------
    def _apply_role_config(
        self,
        role: AgentRole,
        profile_combo: QComboBox,
        provider_edit: QLineEdit | None = None,
        model_edit: QLineEdit | None = None,
    ) -> None:
        """Write profile AND provider/model into the SAME durable role config."""
        profile = profile_combo.currentText().strip()
        fields: dict[str, Any] = {"engine": self._engine_for(role), "project_profile": profile}
        if provider_edit is not None:
            fields["provider"] = provider_edit.text().strip()
        if model_edit is not None:
            fields["model"] = model_edit.text().strip()
        self.controller.set_role_config(role, **fields)

    def _engine_for(self, role: AgentRole) -> str:
        fields = self._role_fields.get(role)
        if fields is not None:
            engine = fields.engine.currentData() or ""
            if engine:
                return engine
        return self.controller.state.resolved_engine_for(role) or ""

    def _apply_coder_config(self) -> None:
        self._apply_role_config(
            AgentRole.BUILDER, self.coder_profile,
            self.coder_provider, self.coder_model,
        )

    def _apply_auditor_config(self) -> None:
        self._apply_role_config(
            AgentRole.TASK_AUDITOR, self.auditor_profile,
            self.auditor_provider, self.auditor_model,
        )

    # -- engine / profile change handlers (Session 011 §5/§13/§15) ------------
    def _on_engine_selected(self, role: AgentRole, fields: _RoleFields) -> None:
        engine = fields.engine.currentData() or ""
        if not engine:
            return
        self.controller.set_role_config(role, engine=engine)
        # An engine change invalidates any external binding by construction:
        # a session recorded under another driver must never be resumed.
        self.controller.clear_external_session(role)
        capabilities = self._capabilities(engine)
        fields.apply_engine(engine, bool(capabilities and capabilities.supports_sessions))
        self._refresh_sessions(role, fields.session)
        if role is AgentRole.BUILDER:
            self._on_coder_session_mode_changed(0)
        self.show_message(f"{role.value}: engine set to '{engine}'.")

    def _on_profile_changed(self, role: AgentRole, fields: _RoleFields) -> None:
        self._apply_role_config(role, fields.profile, fields.provider, fields.model)
        # Session 009 (§19): a binding recorded under another profile is
        # inactive — never silently resumed (Session 011 §15: auto-clear).
        config = self.controller.state.config_for(role)
        if self.controller.binding_profile_mismatch(config):
            self.controller.clear_external_session(role)
        self._refresh_sessions(role, fields.session)

    def _on_coder_session_mode_changed(self, _index: int = 0) -> None:
        resume = self.coder_session_mode.currentData() == "resume_once"
        self._coder_fields.session_row.setVisible(resume)
        executor = self._coder_recovery_executor()
        if not resume:
            if executor is not None and executor.coder_recovery_armed():
                executor.clear_coder_recovery()
                self.show_message(
                    "Session mode: Automatic — new sessions per task/fix, as normal."
                )
            return
        self._refresh_sessions(AgentRole.BUILDER, self._coder_fields.session)

    def _on_coder_resume_session_selected(self, index: int) -> None:
        session_id = str(self._coder_fields.session.itemData(index) or "").strip()
        executor = self._coder_recovery_executor()
        if not session_id:
            if executor is not None and executor.coder_recovery_armed():
                executor.clear_coder_recovery()
            return
        if executor is None:
            self.show_message("No executor attached — the session choice cannot be armed.")
            return
        executor.arm_coder_recovery(session_id)
        self.show_message(
            f"Coder one-shot resume armed: {session_id} will be resumed for the "
            "next Builder operation only, then new sessions resume as normal."
        )

    # -- session refresh / binding (Session 006, 009, 011) ---------------------
    def _capabilities(self, engine: str):
        try:
            return self.controller.registry.capabilities(engine)
        except KeyError:
            return None

    def _on_refresh_architect_sessions(self) -> None:
        self._refresh_sessions(AgentRole.ORCHESTRATOR, self._arch_fields.session)

    def _on_refresh_auditor_sessions(self) -> None:
        self._refresh_sessions(AgentRole.TASK_AUDITOR, self._auditor_fields.session)

    def _on_refresh_coder_sessions(self) -> None:
        self._refresh_sessions(AgentRole.BUILDER, self._coder_fields.session)

    def _refresh_sessions(self, role: AgentRole, combo: QComboBox | None) -> None:
        if combo is None:
            return
        engine = self.controller.state.resolved_engine_for(role)
        driver = None
        if engine and self.controller.registry.is_registered(engine):
            driver = self.controller.registry.create(engine)
        discoverer = getattr(driver, "discover_sessions", None) if driver else None
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem(self._session_placeholder(role), "")
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

    @staticmethod
    def _session_placeholder(role: AgentRole) -> str:
        if role is AgentRole.TASK_AUDITOR:
            return "New session for this batch"
        if role is AgentRole.BUILDER:
            return "Select a session to resume once…"
        return "New session (next run creates one)"

    def _on_architect_session_selected(self, index: int) -> None:
        self._bind_selected(AgentRole.ORCHESTRATOR, self._arch_fields.session, index)

    def _on_auditor_session_selected(self, index: int) -> None:
        self._bind_selected(AgentRole.TASK_AUDITOR, self._auditor_fields.session, index)

    # -- Coder recovery override (Session 010, §14/§15) ----------------------
    def _coder_recovery_executor(self):
        executor = self.controller.executor
        if executor is None or not hasattr(executor, "coder_recovery_armed"):
            return None
        return executor

    def _on_refresh_coder_recovery_sessions(self) -> None:
        """Profile-scoped session list for the CODER profile (§14)."""
        self._refresh_sessions(AgentRole.BUILDER, self.coder_recovery_session)

    def _on_apply_coder_recovery(self) -> None:
        """Arm the ONE-SHOT override with the selected saved session.

        The next Builder operation resumes this session for the CURRENT
        interrupted task/fix only; afterwards new sessions resume as normal.
        """
        executor = self._coder_recovery_executor()
        if executor is None:
            return
        session_id = str(self.coder_recovery_session.currentData() or "").strip()
        if not session_id:
            self.show_message(
                "Select a saved Coder session first (or restart in a NEW session)."
            )
            return
        executor.arm_coder_recovery(session_id)
        self.coder_recovery_box.setVisible(False)
        self.show_message(
            f"Coder recovery armed: {session_id} will be resumed for the current "
            "task/fix only, then new sessions resume as normal."
        )

    def _on_clear_coder_recovery(self) -> None:
        """Drop the override: the interrupted task restarts in a NEW session."""
        executor = self._coder_recovery_executor()
        if executor is None:
            return
        executor.clear_coder_recovery()
        self.coder_recovery_box.setVisible(False)
        self.show_message(
            "Coder recovery cleared — the interrupted task restarts in a NEW "
            "session (the normal automatic policy)."
        )

    def show_coder_recovery(self, interrupted_session_id: str | None = None) -> None:
        """Expose the one-shot override (called by the recovery affordance)."""
        if interrupted_session_id:
            self.coder_recovery_session.blockSignals(True)
            if self.coder_recovery_session.findText(interrupted_session_id) < 0:
                self.coder_recovery_session.addItem(interrupted_session_id, interrupted_session_id)
            self.coder_recovery_session.setCurrentText(interrupted_session_id)
            self.coder_recovery_session.blockSignals(False)
        self.coder_recovery_box.setVisible(True)

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
        self._select_combo_data(self._arch_fields.engine, orchestrator.engine)
        self._arch_fields.apply_engine(
            orchestrator.engine or "codex", self._supports_sessions(orchestrator.engine)
        )
        self._render_bound_session(
            self._arch_fields.session, orchestrator.external_session_binding()
        )
        coder = state.config_for(AgentRole.BUILDER)
        self._select_combo_data(self._coder_fields.engine, coder.engine)
        self._coder_fields.apply_engine(
            coder.engine or "hermes", self._supports_sessions(coder.engine)
        )
        if coder.project_profile:
            self._select_combo_text(self.coder_profile, coder.project_profile)
        if coder.provider:
            self.coder_provider.setText(coder.provider)
        if coder.model:
            self.coder_model.setText(coder.model)
        executor = self.controller.executor
        armed = bool(
            executor is not None
            and getattr(executor, "coder_recovery_armed", lambda: False)()
        )
        if armed:
            self._select_combo_data(self.coder_session_mode, "resume_once")
            self._coder_fields.session_row.setVisible(True)
        else:
            self._select_combo_data(self.coder_session_mode, "automatic")
            self._coder_fields.session_row.setVisible(False)
        auditor = state.config_for(AgentRole.TASK_AUDITOR)
        self._select_combo_data(self._auditor_fields.engine, auditor.engine)
        self._auditor_fields.apply_engine(
            auditor.engine or "hermes", self._supports_sessions(auditor.engine)
        )
        if auditor.project_profile:
            self._select_combo_text(self.auditor_profile, auditor.project_profile)
        if auditor.provider:
            self.auditor_provider.setText(auditor.provider)
        if auditor.model:
            self.auditor_model.setText(auditor.model)
        self._render_bound_session(
            self._auditor_fields.session, auditor.external_session_binding()
        )
        if state.batch is not None:
            self.goal_edit.setPlainText(state.batch.project_brief or "")

    def _supports_sessions(self, engine: str) -> bool:
        capabilities = self._capabilities(engine)
        return bool(capabilities and capabilities.supports_sessions)

    @staticmethod
    def _select_combo_text(combo: QComboBox, value: str) -> None:
        index = combo.findText(value)
        if index >= 0:
            combo.setCurrentIndex(index)
        elif combo.isEditable():
            combo.setEditText(value)

    @staticmethod
    def _select_combo_data(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    @staticmethod
    def _render_bound_session(
        combo: QComboBox | None, binding: Any
    ) -> None:
        """Show the durable binding as the selected row (Session 006)."""
        if combo is None:
            return
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem("New session (next run creates one)", "")
            if binding is not None:
                label = f"[bound] {binding.external_session_id}"
                combo.addItem(label, binding.external_session_id)
                combo.setCurrentIndex(combo.count() - 1)
        finally:
            combo.blockSignals(False)

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
            # Session 010 (§14): a Builder operation was interrupted (task left
            # RUNNING / RUNNING_FIX by a crash or power loss) — expose the
            # one-shot Coder recovery override with the saved session id when
            # it is known.  Hidden while a run is actively using the executor.
            executor = self.controller.executor
            busy = bool(executor is not None and getattr(executor, "is_running", False))
            interrupted_builder = (
                not busy
                and current is not None
                and current.state.value in {"RUNNING", "RUNNING_FIX"}
            )
            if interrupted_builder and current is not None:
                saved = current.fix_session_id or current.builder_session_id
                self.show_coder_recovery(saved or None)
            elif not (
                executor is not None
                and getattr(executor, "coder_recovery_armed", lambda: False)()
            ):
                self.coder_recovery_box.setVisible(False)

    def refresh(self) -> None:
        """Re-read controller state into the status widgets."""
        self._refresh_recovery()
        self._revert_consumed_coder_resume()
        state = self.controller.state
        batch = state.batch
        if batch is None:
            self.status_label.setText("Idle — configure the pipeline and press START.")
            self.task_table.setRowCount(0)
            return
        tasks = sorted(batch.tasks, key=lambda t: t.index)
        approved = sum(1 for t in tasks if t.state.value == "APPROVED")
        current = next(
            (t for t in tasks if t.state.value not in {"APPROVED", "PENDING"}),
            None,
        )
        if current is not None:
            task_display = f"Task {current.index} / {batch.size}"
        elif approved < len(tasks):
            task_display = f"Task {approved + 1} / {batch.size}"
        else:
            task_display = f"{approved} / {batch.size} tasks approved"
        lines = [f"Batch {batch.batch_id} — {task_display} — phase {state.phase.value}"]
        agent = self._current_agent_text(tasks)
        if agent:
            lines.append(agent)
        lines.append(f"Architect session: {self._architect_session_text()}")
        lines.append(f"Auditor session: {self._auditor_session_text()}")
        lines.append(
            "Continuous Run: ON"
            if getattr(self, "continuous_active", False)
            else "Continuous Run: OFF"
        )
        self.status_label.setText("\n".join(lines))
        rows = [
            (t.title or f"Task {t.index}", t.state.value)
            for t in tasks
        ]
        self.task_table.setRowCount(len(rows))
        for row, (title, status) in enumerate(rows):
            self.task_table.setItem(row, 0, QTableWidgetItem(str(title)))
            self.task_table.setItem(row, 1, QTableWidgetItem(str(status)))

    def _current_agent_text(self, tasks: Sequence[Any]) -> str:
        """The agent acting on the in-flight task, if any (§19)."""
        state = self.controller.state
        for task in tasks:
            if task.state.value in {"RUNNING", "RUNNING_FIX"}:
                config = state.config_for(AgentRole.BUILDER)
                return (
                    f"Current agent: {config.engine or '—'} / "
                    f"{config.project_profile or '—'}"
                )
            if task.state.value in {"AUDITING", "FIX_REQUIRED"}:
                config = state.config_for(AgentRole.TASK_AUDITOR)
                return (
                    f"Current agent: {config.engine or '—'} / "
                    f"{config.project_profile or '—'}"
                )
        return ""

    def _architect_session_text(self) -> str:
        state = self.controller.state
        binding = state.config_for(AgentRole.ORCHESTRATOR).external_session_binding()
        if binding is not None:
            return self._short_session(binding.external_session_id)
        batch = state.batch
        if batch is not None and batch.plan is not None and batch.plan.orchestrator_session_id:
            return self._short_session(batch.plan.orchestrator_session_id)
        live = self.controller.sessions.current_session_id(AgentRole.ORCHESTRATOR)
        if live:
            return self._short_session(live)
        return "New session (next run creates one)"

    def _auditor_session_text(self) -> str:
        state = self.controller.state
        binding = state.config_for(AgentRole.TASK_AUDITOR).external_session_binding()
        if binding is not None:
            return self._short_session(binding.external_session_id)
        batch = state.batch
        if batch is not None:
            ids = {t.auditor_session_id for t in batch.tasks if t.auditor_session_id}
            if len(ids) == 1:
                return self._short_session(next(iter(ids)))
        live = self.controller.sessions.current_session_id(AgentRole.TASK_AUDITOR)
        if live:
            return self._short_session(live)
        return "New session for this batch"

    @staticmethod
    def _short_session(session_id: str) -> str:
        value = str(session_id or "").strip()
        if not value:
            return "—"
        return value if len(value) <= 24 else value[:11] + "…" + value[-8:]

    def _revert_consumed_coder_resume(self) -> None:
        """After the one-shot Coder override is consumed, return to Automatic."""
        if self.coder_session_mode.currentData() != "resume_once":
            return
        executor = self.controller.executor
        if executor is None or not getattr(executor, "coder_recovery_armed", lambda: False)():
            self.coder_session_mode.blockSignals(True)
            try:
                self._select_combo_data(self.coder_session_mode, "automatic")
                self._coder_fields.session_row.setVisible(False)
            finally:
                self.coder_session_mode.blockSignals(False)

    # -- wired by MainWindow ------------------------------------------------
    def show_message(self, text: str) -> None:
        self.statusBar_message = text

    def confirm_stop(self) -> bool:  # pragma: no cover - future use
        return (
            QMessageBox.question(self, "Stop", "Stop after the current operation?")
            == QMessageBox.StandardButton.Yes
        )