"""Run/batch history panel (Session 007).

A read-only surface over :mod:`encomm_pcc.core.history`: a list of batches
(newest first) and the durable detail for the selected batch.  It renders
bounded operational metadata — never transcripts — and triggers nothing: no
engine contact, no model calls, no state changes.
"""

from __future__ import annotations

from typing import Any, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import PipelineController
from ..core.history import batch_history_detail, batch_history_rows

__all__ = ["HistoryPanel"]

_DETAIL_LIMIT = 6000


def _monospace(widget: QWidget) -> None:
    from PySide6.QtGui import QFont

    font = QFont("Consolas")
    font.setStyleHint(QFont.Monospace)
    font.setPointSize(8)
    widget.setFont(font)


class HistoryPanel(QGroupBox):
    """HISTORY section: batch list + bounded detail view."""

    def __init__(
        self,
        controller: PipelineController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("HISTORY (completed and in-flight batches)", parent)
        self._controller = controller
        self._rows: list[dict[str, Any]] = []

        self.batch_list = QListWidget()
        self.batch_list.setMaximumHeight(160)
        self.batch_list.itemSelectionChanged.connect(self._on_selection_changed)

        self.refresh_button = QPushButton("Refresh history")
        self.refresh_button.setToolTip(
            "Re-read the durable batch records from the local database "
            "(read-only; no engine contact)."
        )
        self.refresh_button.clicked.connect(self.refresh)

        self.empty_label = QLabel("No batches recorded yet.")
        self.empty_label.setWordWrap(True)
        self.empty_label.setStyleSheet("color: palette(mid);")

        list_row = QHBoxLayout()
        list_row.addWidget(self.batch_list, 1)
        list_row.addWidget(self.refresh_button, 0, Qt.AlignTop)

        self.detail_view = QPlainTextEdit()
        self.detail_view.setReadOnly(True)
        self.detail_view.setMaximumHeight(220)
        self.detail_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        _monospace(self.detail_view)

        layout = QVBoxLayout(self)
        layout.addLayout(list_row)
        layout.addWidget(self.empty_label)
        layout.addWidget(self.detail_view)

        self.refresh()

    # -- data ------------------------------------------------------------------
    def refresh(self) -> None:
        """Reload rows from the read model and keep the selection stable."""
        database = self._controller.database
        if database is None:
            self._rows = []
        else:
            self._rows = batch_history_rows(database, limit=50)
        self.batch_list.blockSignals(True)
        try:
            self.batch_list.clear()
            selected_id = self._selected_batch_id()
            for row in self._rows:
                self.batch_list.addItem(self._render_row(row))
                if row["batch_id"] == selected_id:
                    self.batch_list.setCurrentRow(self.batch_list.count() - 1)
        finally:
            self.batch_list.blockSignals(False)
        self.empty_label.setText(
            "No batches recorded yet." if not self._rows else f"{len(self._rows)} batch(es) recorded (newest first)."
        )
        self.empty_label.setVisible(not self._rows)
        self._on_selection_changed()

    def _render_row(self, row: dict[str, Any]) -> str:
        verdict = row.get("final_verdict") or "—"
        plan = "next plan READY" if row.get("next_plan_open") else (
            "next plan consumed" if row.get("next_plan_id") else ""
        )
        parts = [
            str(row.get("created_at", ""))[:19],
            row.get("batch_title") or "(untitled)",
            f"[{row.get('status', '')}]",
            f"{row.get('approved_count', 0)}/{row.get('task_count', 0)} approved",
            f"final: {verdict}",
        ]
        if plan:
            parts.append(f"({plan})")
        line = "  |  ".join(p for p in parts if p)
        return line[:160]

    # -- selection ---------------------------------------------------------------
    def _selected_batch_id(self) -> str:
        item = self.batch_list.currentItem()
        if item is None:
            return ""
        row_index = self.batch_list.currentRow()
        if 0 <= row_index < len(self._rows):
            return str(self._rows[row_index]["batch_id"])
        return ""

    def _on_selection_changed(self) -> None:
        batch_id = self._selected_batch_id()
        if not batch_id:
            self.detail_view.setPlainText(
                "(select a batch to see its durable detail)"
            )
            return
        database = self._controller.database
        detail = batch_history_detail(database, batch_id) if database else None
        if detail is None:
            self.detail_view.setPlainText(f"(no durable detail found for {batch_id})")
            return
        self.detail_view.setPlainText(self._render_detail(detail)[:_DETAIL_LIMIT])

    @staticmethod
    def _render_detail(detail: dict[str, Any]) -> str:
        lines = [
            f"batch id      : {detail.get('batch_id', '')}",
            f"workspace     : {detail.get('workspace_name', '')} ({detail.get('workspace_path', '')})",
            f"status/phase  : {detail.get('status', '')} / {detail.get('phase', '')}",
            f"created       : {detail.get('created_at', '')}",
            f"title         : {detail.get('batch_title') or '(untitled)'}",
            f"objective     : {(detail.get('batch_objective') or '')[:200]}",
            f"brief         : {(detail.get('project_brief') or '')[:200]}",
            f"orchestrator  : session {detail.get('orchestrator_session_id') or 'NOT_EXPOSED'}"
            f" (planned {detail.get('planned_at') or '—'}, {detail.get('plan_status') or '—'})",
            "",
            "--- tasks ---",
        ]
        tasks = detail.get("tasks") or []
        if not tasks:
            lines.append("(no tasks materialised)")
        for task in tasks:
            lines.append(
                f"  #{task.get('index')} [{task.get('state')}] {task.get('title')} — "
                f"attempts {task.get('attempts')}, audit rounds {task.get('audit_rounds')}, "
                f"verdict {task.get('latest_verdict') or '—'}"
            )
            builder = task.get("builder_session_id") or task.get("fix_session_id")
            if builder:
                lines.append(f"      builder session : {builder}")
            if task.get("auditor_session_id"):
                lines.append(f"      auditor session : {task.get('auditor_session_id')}")
            if task.get("last_error"):
                lines.append(f"      last error      : {str(task.get('last_error'))[:160]}")
        lines += [
            "",
            "--- final audit ---",
            f"verdict       : {detail.get('final_verdict') or '(not audited)'}",
            f"auditor       : session {detail.get('final_auditor_session_id') or 'NOT_EXPOSED'}"
            f" (at {detail.get('final_audited_at') or '—'})",
            f"summary       : {(detail.get('final_summary') or '')[:400]}",
        ]
        plan = detail.get("next_plan")
        lines += ["", "--- next batch plan ---"]
        if plan is None:
            lines.append("(none generated)")
        else:
            consumed = plan.get("consumed_at")
            lines.append(
                f"plan {plan.get('plan_id')} — {plan.get('requested_size')} tasks — "
                + ("CONSUMED" if consumed else "READY (waiting for the operator)")
            )
        return "\n".join(lines)
