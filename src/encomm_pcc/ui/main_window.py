"""Main application window.

Layout (top to bottom): WORKSPACE, the four role blocks in a 2x2 grid, BATCH,
then the LOG PANEL in a resizable splitter.  The window is intentionally plain:
this phase is about a correct, launchable shell over the real architecture.
"""

from __future__ import annotations

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

from ..core import APP_NAME, PipelineController
from ..domain import AgentRole, PipelinePhase
from ..drivers import PLANNED_DRIVERS
from .panels import BatchPanel, LogPanel, RolePanel, WorkspacePanel

__all__ = ["MainWindow"]


class MainWindow(QMainWindow):
    """The single application window."""

    def __init__(self, controller: PipelineController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller

        self.setWindowTitle(f"{APP_NAME} — v0.1 foundation")
        self.resize(1180, 860)

        # -- panels -------------------------------------------------------
        self.workspace_panel = WorkspacePanel(controller)
        self.role_panels: dict[AgentRole, RolePanel] = {
            role: RolePanel(role, controller, controller.registry) for role in AgentRole
        }
        self.batch_panel = BatchPanel(controller)
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
        splitter.addWidget(self.log_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.addWidget(splitter)
        self.setCentralWidget(central)

        self.statusBar().showMessage("Ready — executor not implemented in v0.1 foundation.")

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

    def _subscribe_to_events(self) -> None:
        self.controller.events.subscribe(self._on_event)

    def _on_event(self, record: object) -> None:
        # Marshalled onto the UI thread by the LogPanel's queued connection.
        self.log_panel.record_received.emit(record)

    def _log_startup_summary(self) -> None:
        events = self.controller.events
        events.info(f"{APP_NAME} v0.1 foundation started.", source="ui")
        for row in self.controller.driver_capabilities():
            events.info(
                f"Driver '{row['driver_id']}': implemented={row['implemented']}, "
                f"sessions={row['supports_sessions']}, binary_present={row['binary_present']}",
                source="ui",
            )
        for planned in PLANNED_DRIVERS:
            events.debug(f"Planned engine '{planned.driver_id}' — not implemented.", source="ui")

    # -- handlers ---------------------------------------------------------
    def _on_workspace_changed(self, name: str, repo_path: str) -> None:
        self.controller.set_workspace(name, repo_path)

    def _on_role_changed(self, role: AgentRole, values: dict) -> None:
        self.controller.set_role_config(role, **values)
        # "Same as orchestrator" affects the FINAL AUDITOR session field.
        self.role_panels[AgentRole.FINAL_AUDITOR].refresh_session_field()
        if role is AgentRole.ORCHESTRATOR:
            self.role_panels[AgentRole.FINAL_AUDITOR].refresh_session_field()

    def _on_start(self, size: int) -> None:
        result = self.controller.request_start(size)
        self._after_control(result.message, result.phase)
        if not result.executor_started:
            self.statusBar().showMessage(
                f"{result.phase.value} — executor not implemented in v0.1."
            )

    def _on_pause(self) -> None:
        result = self.controller.request_pause()
        self._after_control(result.message, result.phase)

    def _on_resume(self) -> None:
        result = self.controller.request_resume()
        self._after_control(result.message, result.phase)

    def _on_stop(self) -> None:
        result = self.controller.request_stop()
        self._after_control(result.message, result.phase)

    def _after_control(self, message: str, phase: PipelinePhase) -> None:
        if message:
            self.controller.events.info(message, source="ui")
        self.batch_panel.refresh()
        for panel in self.role_panels.values():
            panel.refresh_session_field()
        self.statusBar().showMessage(f"Phase: {phase.value}")

    # -- test helper --------------------------------------------------------
    def panel_titles(self) -> list[str]:
        """Every group-box title currently shown (used by UI smoke tests)."""
        titles: list[str] = []
        for widget in self.findChildren(QGroupBox):
            titles.append(widget.title())
        return titles
