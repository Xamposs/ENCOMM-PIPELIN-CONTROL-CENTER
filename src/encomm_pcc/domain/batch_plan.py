"""Strict structured batch plan contract (Session 004).

The Orchestrator's answer is **untrusted model output**.  This module defines
what a *valid* plan looks like — the shape, the bounds, and the durable record
that wraps it — and ``core/plan_parser.py`` is the only place that turns raw
model text into a :class:`BatchPlan`.  A plan that violates the contract is
rejected (fails closed) before any task is ever materialised.

Schema (mirrored in the Orchestrator prompt packet):

.. code-block:: json

    {
      "batch_title": "...",
      "batch_objective": "...",
      "tasks": [
        {
          "index": 1,
          "title": "...",
          "implementation_prompt": "...",
          "acceptance_criteria": ["..."],
          "audit_focus": ["..."]
        }
      ]
    }

Structural rules enforced by the parser (never by convention):

* exactly the requested number of tasks
* task indices contiguous ``1..N``
* unique non-empty titles
* ``implementation_prompt`` required and non-empty
* ``acceptance_criteria`` / ``audit_focus`` required, non-empty, bounded
* every string and list bounded; task count bounded
* nothing in this structure is ever executed or shell-evaluated
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

__all__ = [
    "PLAN_FOCUS_MAX",
    "PLAN_ITEM_MAX",
    "PLAN_LIST_MAX",
    "PLAN_MAX_TASKS",
    "PLAN_PROMPT_MAX",
    "PLAN_RAW_MAX",
    "PLAN_TITLE_MAX",
    "BatchPlan",
    "BatchPlanRecord",
    "PlannedTask",
    "build_batch_summary",
]

#: The batch may plan at most this many tasks (matches the UI batch-size
#: bounds; the parser fails closed if the model exceeds it).
PLAN_MAX_TASKS = 5

#: Bounds for the planning answer (the parser refuses anything beyond these).
PLAN_RAW_MAX = 200_000        # the whole model answer we are willing to read
PLAN_TITLE_MAX = 120          # batch title / task title
PLAN_PROMPT_MAX = 20_000      # one implementation prompt
PLAN_ITEM_MAX = 500          # one acceptance criterion / audit focus string
PLAN_LIMIT_UPPER = PLAN_ITEM_MAX
PLAN_FOCUS_MAX = PLAN_ITEM_MAX  # per audit-focus string (same bound as criteria)
PLAN_LIST_MAX = 12            # criteria / focus list length per task


def _utc_now() -> str:
    """ISO-8601 UTC timestamp (second precision, ``Z`` suffix), local copy.

    Defined here (not imported from ``.models``) so this module stays a leaf —
    ``models`` imports ``BatchPlanRecord`` from it and a cycle would break
    every import.
    """
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


@dataclass(slots=True)
class PlannedTask:
    """One task exactly as the Orchestrator planned it (plain data)."""

    index: int
    title: str
    implementation_prompt: str
    acceptance_criteria: list[str] = field(default_factory=list)
    audit_focus: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": int(self.index),
            "title": self.title,
            "implementation_prompt": self.implementation_prompt,
            "acceptance_criteria": list(self.acceptance_criteria),
            "audit_focus": list(self.audit_focus),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PlannedTask":
        return cls(
            index=int(data.get("index", 0)),
            title=str(data.get("title", "")),
            implementation_prompt=str(data.get("implementation_prompt") or ""),
            acceptance_criteria=[str(x) for x in (data.get("acceptance_criteria") or [])],
            audit_focus=[str(x) for x in (data.get("audit_focus") or [])],
        )


@dataclass(slots=True)
class BatchPlan:
    """The strict plan: batch metadata + the ordered, validated task list."""

    batch_title: str
    batch_objective: str
    tasks: list[PlannedTask] = field(default_factory=list)

    @property
    def task_count(self) -> int:
        return len(self.tasks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_title": self.batch_title,
            "batch_objective": self.batch_objective,
            "tasks": [t.to_dict() for t in self.tasks],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BatchPlan":
        return cls(
            batch_title=str(data.get("batch_title", "")),
            batch_objective=str(data.get("batch_objective", "")),
            tasks=[PlannedTask.from_dict(t) for t in (data.get("tasks") or [])],
        )


@dataclass(slots=True)
class BatchPlanRecord:
    """The durable planning truth for one batch (persisted in ``batch_plans``).

    Wraps the strict :class:`BatchPlan` with everything a restart needs to
    reconstruct the batch: the Project Brief, the requested size, the real
    Orchestrator session id (when the engine exposed one), planning
    status/timestamp, the repository baseline, and — once the batch finishes —
    the final phase and the durable Batch Summary for the Final Auditor.
    """

    plan: BatchPlan
    project_brief: str = ""
    requested_size: int = 0
    #: ``PLANNED`` | ``FAILED`` | ``BLOCKED`` — the outcome of the planning call.
    plan_status: str = "PLANNED"
    #: Real external Orchestrator session id, or None when the engine exposed
    #: none (never fabricated).
    orchestrator_session_id: str | None = None
    planned_at: str = field(default_factory=_utc_now)
    #: Repository baseline captured before the planning call (read-only).
    baseline_head: str = ""
    baseline_fingerprint_json: str | None = None
    #: Repository HEAD when the batch reached READY_FOR_FINAL_AUDIT.
    current_head: str = ""
    #: The batch's durable terminal phase (``READY_FOR_FINAL_AUDIT`` once all
    #: tasks pass).  ``BATCH_COMPLETE`` is never written by this session.
    final_phase: str = ""
    finalized_at: str | None = None
    #: Durable structured Batch Summary — evidence for the Final Auditor.
    batch_summary_json: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "project_brief": self.project_brief,
            "requested_size": int(self.requested_size),
            "plan_status": self.plan_status,
            "orchestrator_session_id": self.orchestrator_session_id,
            "planned_at": self.planned_at,
            "baseline_head": self.baseline_head,
            "baseline_fingerprint_json": self.baseline_fingerprint_json,
            "current_head": self.current_head,
            "final_phase": self.final_phase,
            "finalized_at": self.finalized_at,
            "batch_summary_json": self.batch_summary_json,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BatchPlanRecord":
        return cls(
            plan=BatchPlan.from_dict(data.get("plan") or {}),
            project_brief=str(data.get("project_brief", "")),
            requested_size=int(data.get("requested_size", 0)),
            plan_status=str(data.get("plan_status", "PLANNED")),
            orchestrator_session_id=(
                str(data["orchestrator_session_id"])
                if data.get("orchestrator_session_id")
                else None
            ),
            planned_at=str(data.get("planned_at") or utc_now()),
            baseline_head=str(data.get("baseline_head", "")),
            baseline_fingerprint_json=(
                str(data["baseline_fingerprint_json"])
                if data.get("baseline_fingerprint_json")
                else None
            ),
            current_head=str(data.get("current_head", "")),
            final_phase=str(data.get("final_phase", "")),
            finalized_at=(
                str(data["finalized_at"]) if data.get("finalized_at") else None
            ),
            batch_summary_json=(
                str(data["batch_summary_json"]) if data.get("batch_summary_json") else None
            ),
        )

    def as_json(self) -> str:
        """Serialise the record (for events / diagnostics)."""
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


def build_batch_summary(
    *,
    batch: Any,  # noqa: ANN001 - BatchState (kept loose to avoid a cycle)
    plan: BatchPlanRecord | None,
) -> dict[str, Any]:
    """Durable structured Batch Summary — evidence for the Final Auditor.

    Generated from the batch's own state (never from model memory).  This is
    **not** the Final Audit; it is the handoff packet Session 005's Final
    Auditor will consume.
    """
    approved = sum(1 for t in batch.tasks if t.state.value == "APPROVED")
    task_rows: list[dict[str, Any]] = []
    for task in batch.tasks:
        task_rows.append(
            {
                "index": task.index,
                "title": task.title,
                "attempts": task.attempts,
                "audit_rounds": task.audit_rounds,
                "final_verdict": task.latest_verdict,
                "builder_session_id": task.builder_session_id,
                "fix_session_id": task.fix_session_id,
                "auditor_session_id": task.auditor_session_id,
            }
        )
    return {
        "batch_id": batch.batch_id,
        "batch_title": (plan.plan.batch_title if plan else "") or batch.batch_title,
        "batch_objective": (plan.plan.batch_objective if plan else "") or batch.batch_objective,
        "requested_task_count": batch.size,
        "completed_task_count": approved,
        "tasks": task_rows,
        "shared_auditor_session_id": (
            task_rows[-1].get("auditor_session_id")
            if task_rows
            else None
        ),
        "orchestrator_session_id": (plan.orchestrator_session_id if plan else None),
        "baseline_head": (plan.baseline_head if plan else ""),
        "current_head": (plan.current_head if plan else batch.current_head),
        "batch_status": batch.status.value,
        "final_phase": (plan.final_phase if plan else ""),
    }