"""Application bootstrap: wire persistence, event log, controller and window.

Kept separate from :mod:`encomm_pcc.ui` so the whole stack can be assembled
headlessly in tests (``build_controller``) without importing Qt.
"""

from __future__ import annotations

import logging
import sys
from typing import Sequence

from .core import APP_NAME, AppPaths, EventLog, PipelineController, default_paths
from .domain import AgentRole, PipelineState, WorkspaceConfig
from .persistence import Database

__all__ = ["build_controller", "configure_logging", "run", "restore_state"]

logger = logging.getLogger(__name__)


def configure_logging(level: int = logging.INFO) -> None:
    """Send library diagnostics to stderr; the app's own log is the event log."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def restore_state(database: Database) -> PipelineState | None:
    """Rebuild the last workspace and its role configuration, if any."""
    workspaces = database.list_workspaces()
    if not workspaces:
        return None
    workspace = workspaces[-1]
    role_configs = database.load_role_configs(workspace.workspace_id)
    state = PipelineState.bootstrap(workspace)
    for role in AgentRole:
        if role in role_configs:
            state.role_configs[role] = role_configs[role]
    state.batch = database.load_active_batch(workspace.workspace_id)
    return state


def build_controller(
    *,
    paths: AppPaths | None = None,
    database: Database | None = None,
    in_memory: bool = False,
) -> PipelineController:
    """Assemble a controller over a real (or in-memory) database."""
    resolved = (paths or default_paths()).ensure()
    db = database
    if db is None:
        db = Database(":memory:" if in_memory else resolved.database)
        db.open()

    events = EventLog(db, echo=False)
    state = None if in_memory else restore_state(db)
    controller = PipelineController(database=db, event_log=events, state=state)

    if state is None:
        # Fresh install: the controller constructor already applied the
        # documented placeholder configuration to every role.
        controller.state.workspace = WorkspaceConfig(name="New Workspace", repo_path="")
    controller.events.info(
        f"Database ready at {db.path}", source="app"
    )
    return controller


def run(argv: Sequence[str] | None = None) -> int:
    """Launch the desktop application.  Returns the Qt exit code."""
    from PySide6.QtWidgets import QApplication

    from .ui import MainWindow

    configure_logging()
    app = QApplication(list(argv) if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)

    controller = build_controller()
    window = MainWindow(controller)
    window.show()

    controller.events.info("Main window shown.", source="app")
    return app.exec()
