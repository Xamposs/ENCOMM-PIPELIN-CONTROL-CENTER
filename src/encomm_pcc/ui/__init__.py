"""Desktop UI layer (PySide6).

The UI reads and writes through :class:`~encomm_pcc.core.controller.PipelineController`
and never talks to a driver directly.
"""

from .generic_cli_dialog import GenericCliConfigDialog
from .history_panel import HistoryPanel
from .main_window import MainWindow
from .panels import BatchPanel, LogPanel, RolePanel, TaskPanel, WorkspacePanel
from .worker import ExecutorWorker, start_executor_worker

__all__ = [
    "BatchPanel",
    "ExecutorWorker",
    "GenericCliConfigDialog",
    "HistoryPanel",
    "LogPanel",
    "MainWindow",
    "RolePanel",
    "TaskPanel",
    "WorkspacePanel",
    "start_executor_worker",
]
