"""Desktop UI layer (PySide6).

The UI reads and writes through :class:`~encomm_pcc.core.controller.PipelineController`
and never talks to a driver directly.
"""

from .main_window import MainWindow
from .panels import BatchPanel, LogPanel, RolePanel, WorkspacePanel

__all__ = ["BatchPanel", "LogPanel", "MainWindow", "RolePanel", "WorkspacePanel"]
