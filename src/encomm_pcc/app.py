"""Application bootstrap: wire persistence, event log, controller and window.

Kept separate from :mod:`encomm_pcc.ui` so the whole stack can be assembled
headlessly in tests (``build_controller``) without importing Qt.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from typing import Sequence

from .core import (
    APP_NAME,
    AppPaths,
    EventLog,
    Executor,
    PipelineController,
    default_paths,
    discover_profiles,
)
from .domain import AgentRole, PipelinePhase, PipelineState, WorkspaceConfig
from .drivers import SubprocessRunner
from .persistence import Database

__all__ = [
    "attach_default_executor",
    "build_controller",
    "configure_logging",
    "discover_hermes_profiles",
    "run",
    "restore_state",
    "run_smoke_test",
    "SMOKE_TEST_FLAG",
]

logger = logging.getLogger(__name__)

#: Session 008 — packaged-application self-test switch (brief §11).
SMOKE_TEST_FLAG = "--smoke-test"

#: Session 011 (§17) — development-only flag that restores the full
#: debug/detailed UI.  The normal launch exposes the clean production
#: interface only (Simple Mode, no Advanced button).
DEBUG_UI_FLAG = "--debug-ui"

#: Session 008 — bounded on-disk bootstrap log (brief §15): per-user, outside
#: the installation directory, size-capped so it can never grow without bound.
BOOTSTRAP_LOG_MAX_BYTES = 1_000_000
BOOTSTRAP_LOG_BACKUPS = 2


def configure_logging(
    level: int = logging.INFO, paths: AppPaths | None = None
) -> None:
    """Send library diagnostics to stderr and, when a data dir is given, to a
    bounded per-user file log used for bootstrap/packaging failures."""
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stderr),
    ]
    if paths is not None:
        try:
            paths.ensure()
            file_handler = logging.handlers.RotatingFileHandler(
                paths.logs_dir / "bootstrap.log",
                maxBytes=BOOTSTRAP_LOG_MAX_BYTES,
                backupCount=BOOTSTRAP_LOG_BACKUPS,
                encoding="utf-8",
            )
            handlers.append(file_handler)
        except OSError:
            # A read-only data dir must not break startup; stderr still works.
            pass
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
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
    # Session 008 (found by the real acceptance run): restore the persisted
    # work phase from the batch row — the same contract
    # ``Database.load_pipeline_state`` implements.  Without this, a restart
    # at READY_FOR_FINAL_AUDIT bootstrapped the machine back to IDLE and the
    # operator could never run the Final Audit from the UI.
    if state.batch is not None and state.batch.phase:
        try:
            state.phase = PipelinePhase(str(state.batch.phase))
        except ValueError:  # pragma: no cover - stored data is ours
            pass
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
    """Launch the desktop application.  Returns the Qt exit code.

    This is the only place that wires the *real* process runner: the executor
    gets a :class:`SubprocessRunner`, so dispatched prompts start real Hermes
    processes.  Tests build controllers without it (see ``attach_default_executor``).

    Session 011 (§17): ``--debug-ui`` re-enables the developer/detailed
    surface; the flag is stripped before Qt sees the arguments.  Normal
    launches are production-only.
    """
    from PySide6.QtWidgets import QApplication

    from .ui import MainWindow

    argv_list = list(sys.argv[1:] if argv is None else argv)
    debug_ui = DEBUG_UI_FLAG in argv_list
    argv_list = [arg for arg in argv_list if arg != DEBUG_UI_FLAG]

    paths = default_paths()
    configure_logging(paths=paths)
    app = QApplication(["encomm-pcc"] + argv_list)
    app.setApplicationName(APP_NAME)

    controller = build_controller(paths=paths)
    attach_default_executor(controller)
    discovery = discover_hermes_profiles()
    window = MainWindow(
        controller,
        profiles=discovery.profiles if discovery.ok else (),
        profile_method=discovery.method if discovery.ok else "",
        debug_ui=debug_ui,
    )
    window.show()

    controller.events.info("Main window shown.", source="app")
    return app.exec()


def run_smoke_test() -> int:
    """Packaged-application self-test (brief §11).  Exits 0/1, never hangs.

    Proves, without contacting any model or engine:
    package imports, a writable app-data directory, SQLite open/create,
    schema compatibility, controller construction, driver registry
    availability, offscreen Qt application + main window construction,
    and clean shutdown.  Reuses the real bootstrap code — no parallel app.
    """
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from . import __version__
    from .drivers import IMPLEMENTED_DRIVERS
    from .persistence import SCHEMA_VERSION
    from .ui import MainWindow

    steps: list[str] = []
    controller: PipelineController | None = None

    def _emit(message: str) -> None:
        # A windowed packaged exe has no stdout; the bounded file log carries
        # the same evidence (brief §15).
        logger.info("%s", message)
        if sys.stdout is not None:
            print(message, flush=True)

    try:
        paths = default_paths().ensure()
        configure_logging(paths=paths)
        steps.append(f"app_data_writable={paths.data_dir}")

        db = Database(paths.database).open()
        try:
            db_version = db.schema_version()
            steps.append(f"sqlite_ok=schema_v{db_version}")
            if db_version != SCHEMA_VERSION:
                _emit(f"SMOKE FAIL: schema v{db_version} != v{SCHEMA_VERSION}")
                return 1

            controller = build_controller(paths=paths, database=db)
            steps.append("controller_constructed")

            attach_default_executor(controller)
            registry_names = ",".join(
                sorted(cls.driver_id for cls in IMPLEMENTED_DRIVERS)
            )
            steps.append(f"drivers={registry_names}")

            app = QApplication.instance() or QApplication(["ENCOMM-PCC-smoke"])
            app.setApplicationName(APP_NAME)
            window = MainWindow(controller, profiles=(), profile_method="")
            window.show()
            app.processEvents()
            # Session 018: prove the packaged global product selector —
            # exactly TWO production tabs (CODING / PROPOSAL), startup on
            # CODING MODE with the mode stack at index 0, PROPOSAL MODE on
            # stack index 2, and Advanced (index 1) present but tabless.
            # Zero model calls — construction only.
            tabs = [window.mode_tabs.tabText(i) for i in range(window.mode_tabs.count())]
            if tabs != ["CODING MODE", "PROPOSAL MODE"]:
                raise AssertionError(f"unexpected product tabs: {tabs!r}")
            if window.mode_tabs.currentIndex() != 0:
                raise AssertionError("startup product tab is not CODING MODE")
            if window.mode_stack.currentIndex() != 0:
                raise AssertionError("startup mode_stack index is not 0")
            window.mode_tabs.setCurrentIndex(1)
            app.processEvents()
            if window.mode_stack.currentIndex() != 2:
                raise AssertionError("PROPOSAL MODE tab did not select stack index 2")
            if window.mode_stack.widget(2) is not window.proposal_panel:
                raise AssertionError("stack index 2 is not the proposal panel")
            if window.mode_stack.widget(1) is not window.advanced_view:
                raise AssertionError("stack index 1 is not the advanced view")
            window.mode_tabs.setCurrentIndex(0)
            app.processEvents()
            if window.mode_stack.currentIndex() != 0:
                raise AssertionError("CODING MODE tab did not select stack index 0")
            window.mode_stack.setCurrentWidget(window.proposal_panel)
            window._show_simple_mode()
            app.processEvents()
            if (
                window.mode_stack.currentIndex() != 0
                or window.mode_tabs.currentIndex() != 0
            ):
                raise AssertionError("internal navigation desynced the product tabs")
            steps.append("mode_tabs=2(coding,proposal)")
            steps.append("main_window_constructed")
            window.close()
            app.processEvents()
        finally:
            if controller is not None:
                controller.database.close()
            steps.append("clean_shutdown")

        _emit(f"SMOKE OK version={__version__} " + " ".join(steps))
        return 0
    except Exception as exc:  # noqa: BLE001 - a smoke failure must be reported, not raised
        logger.exception("Smoke test failed")
        _emit(f"SMOKE FAIL: {exc}")
        return 1


def attach_default_executor(
    controller: PipelineController,
    *,
    runner: object | None = None,
) -> Executor:
    """Wire the real executor (and its process runner) onto ``controller``.

    Kept out of :func:`build_controller` on purpose: a test or a tool that builds
    a controller must never be one call away from launching an agent process.
    """
    executor = Executor(
        controller,
        runner=runner if runner is not None else SubprocessRunner(),
        database=controller.database,
        event_log=controller.events,
    )
    controller.attach_executor(executor)
    return executor


def discover_hermes_profiles():  # noqa: ANN201 - ProfileDiscoveryResult
    """Read-only profile discovery over the real runner (never raises)."""
    try:
        return discover_profiles(runner=SubprocessRunner(), timeout_s=60.0)
    except Exception as exc:  # noqa: BLE001 - discovery is advisory
        from .core import ProfileDiscoveryResult

        return ProfileDiscoveryResult(
            ok=False, method="none", detail="discovery failed", error=str(exc)
        )
