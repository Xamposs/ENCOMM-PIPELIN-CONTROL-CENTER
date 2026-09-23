"""Run/batch history read model (Session 007).

Exposes the durable operational records the database ALREADY keeps — no new
tables, no transcripts — as bounded dictionaries for the UI:

* ``batch_history_rows``: one bounded row per batch, newest first.
* ``batch_history_detail``: the durable facts about one batch (tasks, audit
  rounds, session ids, final verdict, next-plan status).

This module is a pure read model: it never mutates state, never contacts an
engine, and renders nothing unbounded (texts are capped here, and the UI caps
them again for display).
"""

from __future__ import annotations

from typing import Any

from ..domain import TaskState
from ..persistence import Database

__all__ = ["batch_history_rows", "batch_history_detail", "HISTORY_TEXT_CAP"]

#: Upper bound for rendered text fields in history (durable metadata, not
#: conversation playback).
HISTORY_TEXT_CAP = 2000


def _clip(text: str | None, cap: int = HISTORY_TEXT_CAP) -> str:
    value = str(text or "")
    if len(value) <= cap:
        return value
    return value[:cap] + f"… [{len(value) - cap} chars omitted]"


def batch_history_rows(database: Database, limit: int = 50) -> list[dict[str, Any]]:
    """One summary row per batch, newest first.

    Rows carry exactly the §13 columns: batch id, created date, workspace,
    status, task count, final verdict, orchestrator and final-auditor session
    ids, plus the batch title and pipeline phase for display.
    """
    rows = database.connection.execute(
        """
        SELECT b.batch_id, b.created_at, b.updated_at, b.status, b.phase,
               b.size, b.project_brief, w.name AS workspace_name,
               w.repo_path AS workspace_path,
               p.batch_title, p.final_verdict,
               p.orchestrator_session_id, p.final_auditor_session_id,
               p.final_audited_at, p.final_summary
        FROM batches b
        JOIN workspaces w ON w.workspace_id = b.workspace_id
        LEFT JOIN batch_plans p ON p.batch_id = b.batch_id
        ORDER BY b.created_at DESC, b.rowid DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    history: list[dict[str, Any]] = []
    for row in rows:
        task_rows = database.connection.execute(
            "SELECT state, attempts, audit_rounds FROM tasks WHERE batch_id = ?",
            (row["batch_id"],),
        ).fetchall()
        pending = database.connection.execute(
            """
            SELECT plan_id, created_at, consumed_at FROM pending_next_plans
            WHERE source_batch_id = ? ORDER BY created_at DESC LIMIT 1
            """,
            (row["batch_id"],),
        ).fetchone()
        next_plan_id = str(pending["plan_id"]) if pending is not None else ""
        next_plan_open = bool(pending is not None and not pending["consumed_at"])
        history.append(
            {
                "batch_id": str(row["batch_id"]),
                "created_at": str(row["created_at"] or ""),
                "updated_at": str(row["updated_at"] or ""),
                "status": str(row["status"] or ""),
                "phase": str(row["phase"] or ""),
                "requested_size": int(row["size"] or 0),
                "task_count": len(task_rows),
                "approved_count": sum(
                    1 for t in task_rows if t["state"] == TaskState.APPROVED.value
                ),
                "workspace_name": str(row["workspace_name"] or ""),
                "workspace_path": str(row["workspace_path"] or ""),
                "batch_title": _clip(str(row["batch_title"] or ""), 120)
                or _clip(str(row["project_brief"] or ""), 120),
                "final_verdict": str(row["final_verdict"] or "") or None,
                "orchestrator_session_id": str(row["orchestrator_session_id"] or "")
                or None,
                "final_auditor_session_id": str(row["final_auditor_session_id"] or "")
                or None,
                "final_audited_at": str(row["final_audited_at"] or "") or None,
                "next_plan_id": next_plan_id or None,
                "next_plan_open": next_plan_open,
            }
        )
    return history


def batch_history_detail(database: Database, batch_id: str) -> dict[str, Any] | None:
    """The durable detail for one batch, or ``None`` when the id is unknown.

    Includes the per-task contracts and states, real session ids, the final
    verdict/findings/summary and the next-plan status — bounded, no raw
    transcripts (the §13 "selecting one reveals useful detail" surface).
    """
    head = database.connection.execute(
        """
        SELECT b.batch_id, b.created_at, b.updated_at, b.status, b.phase,
               b.size, b.project_brief, b.current_head,
               w.name AS workspace_name, w.repo_path AS workspace_path,
               p.batch_title, p.batch_objective, p.plan_status, p.planned_at,
               p.final_verdict, p.final_summary, p.final_findings_json,
               p.final_audit_json, p.final_auditor_session_id, p.final_audited_at,
               p.orchestrator_session_id, p.batch_summary_json, p.final_phase
        FROM batches b
        JOIN workspaces w ON w.workspace_id = b.workspace_id
        LEFT JOIN batch_plans p ON p.batch_id = b.batch_id
        WHERE b.batch_id = ?
        """,
        (str(batch_id),),
    ).fetchone()
    if head is None:
        return None

    task_rows = database.connection.execute(
        """
        SELECT task_id, task_index, title, state, attempts, audit_rounds,
               latest_verdict, last_error,
               auditor_session_id, builder_session_id, fix_session_id
        FROM tasks WHERE batch_id = ? ORDER BY task_index ASC
        """,
        (str(batch_id),),
    ).fetchall()
    tasks = [
        {
            "task_id": str(t["task_id"]),
            "index": int(t["task_index"] or 0),
            "title": _clip(str(t["title"] or ""), 200),
            "state": str(t["state"] or ""),
            "attempts": int(t["attempts"] or 0),
            "audit_rounds": int(t["audit_rounds"] or 0),
            "latest_verdict": str(t["latest_verdict"] or "") or None,
            "last_error": _clip(str(t["last_error"] or "")) or None,
            "auditor_session_id": str(t["auditor_session_id"] or "") or None,
            "builder_session_id": str(t["builder_session_id"] or "") or None,
            "fix_session_id": str(t["fix_session_id"] or "") or None,
        }
        for t in task_rows
    ]

    pending = database.connection.execute(
        """
        SELECT plan_id, requested_size, created_at, consumed_at, consumed_batch_id
        FROM pending_next_plans WHERE source_batch_id = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (str(batch_id),),
    ).fetchone()

    return {
        "batch_id": str(head["batch_id"]),
        "created_at": str(head["created_at"] or ""),
        "updated_at": str(head["updated_at"] or ""),
        "status": str(head["status"] or ""),
        "phase": str(head["phase"] or ""),
        "requested_size": int(head["size"] or 0),
        "workspace_name": str(head["workspace_name"] or ""),
        "workspace_path": str(head["workspace_path"] or ""),
        "batch_title": _clip(str(head["batch_title"] or "")),
        "batch_objective": _clip(str(head["batch_objective"] or "")),
        "project_brief": _clip(str(head["project_brief"] or "")),
        "current_head": str(head["current_head"] or ""),
        "plan_status": str(head["plan_status"] or "") or None,
        "planned_at": str(head["planned_at"] or "") or None,
        "orchestrator_session_id": str(head["orchestrator_session_id"] or "") or None,
        "final_phase": str(head["final_phase"] or "") or None,
        "final_verdict": str(head["final_verdict"] or "") or None,
        "final_summary": _clip(str(head["final_summary"] or "")) or None,
        "final_findings_json": _clip(str(head["final_findings_json"] or "")) or None,
        "final_audit_json": _clip(str(head["final_audit_json"] or "")) or None,
        "final_auditor_session_id": str(head["final_auditor_session_id"] or "")
        or None,
        "final_audited_at": str(head["final_audited_at"] or "") or None,
        "tasks": tasks,
        "next_plan": (
            {
                "plan_id": str(pending["plan_id"]),
                "requested_size": int(pending["requested_size"] or 0),
                "created_at": str(pending["created_at"] or ""),
                "consumed_at": str(pending["consumed_at"] or "") or None,
                "consumed_batch_id": str(pending["consumed_batch_id"] or "") or None,
            }
            if pending is not None
            else None
        ),
    }
