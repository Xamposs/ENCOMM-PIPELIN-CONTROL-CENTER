"""Session 008 — the DIAGNOSTICS section (brief §12).

A pure view over :mod:`encomm_pcc.core.diagnostics`: version, app data
location, database health, workspace readiness and engine availability.
It never launches an engine and never shows credentials.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import PipelineController, collect_diagnostics, format_diagnostics

__all__ = ["DiagnosticsPanel"]


class DiagnosticsPanel(QGroupBox):
    """Read-only readiness/diagnostics view with an explicit REFRESH."""

    def __init__(
        self, controller: PipelineController, parent: QWidget | None = None
    ) -> None:
        super().__init__("DIAGNOSTICS", parent)
        self._controller = controller

        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.refresh_button = QPushButton("REFRESH DIAGNOSTICS")
        self.refresh_button.setToolTip(
            "Re-check the app data, database, workspace and engine availability "
            "(read-only; never launches an engine)."
        )
        header.addWidget(self.refresh_button)
        header.addStretch(1)
        layout.addLayout(header)

        self.readout = QPlainTextEdit()
        self.readout.setReadOnly(True)
        self.readout.setMaximumHeight(240)
        self.readout.setPlaceholderText(
            "Press REFRESH DIAGNOSTICS to check version, data location, database, "
            "workspace readiness and engine availability."
        )
        layout.addWidget(self.readout)

        self.refresh_button.clicked.connect(self.refresh)

    def refresh(self) -> None:
        try:
            report = collect_diagnostics(self._controller)
            self.readout.setPlainText(format_diagnostics(report))
        except Exception as exc:  # noqa: BLE001 - diagnostics must never crash the UI
            self.readout.setPlainText(
                f"Diagnostics failed (the application keeps running):\n{exc}"
            )