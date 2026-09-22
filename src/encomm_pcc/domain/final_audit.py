"""Final-audit contract (Session 005).

Two plain-data structures, no parsing and no I/O:

* :class:`FinalAuditPacket` — the deterministic, durable-facts packet handed to
  the FINAL_AUDITOR.  It is assembled from SQLite + the repository state only
  (never from conversation memory) and carries the explicit instruction to
  inspect the ACTUAL repository, run the tests, compare them against every
  task contract, and NOT edit anything.
* :class:`FinalAuditResult` — the strict shape of the auditor's one-call
  answer: the cumulative final verdict AND the next batch plan in the SAME
  object (the cost contract: one real model call closes a batch and plans the
  next one).

``core/final_audit_parser.py`` is the only place that turns raw model text
into a :class:`FinalAuditResult`, and it fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from .audit import AuditFinding, FindingSeverity
from .batch_plan import BatchPlan

__all__ = [
    "FINAL_VERDICTS",
    "FinalAuditPacket",
    "FinalAuditResult",
    "FinalVerdict",
]


class FinalVerdict(str, Enum):
    """Exact cumulative verdict a Final Auditor may return."""

    PASS = "PASS"
    NEEDS_FIX = "NEEDS_FIX"
    BLOCKED = "BLOCKED"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


#: Strict whitelist for the parser (mirrors ``audit.VERDICTS``).
FINAL_VERDICTS: frozenset[str] = frozenset(v.value for v in FinalVerdict)


@dataclass(frozen=True, slots=True)
class FinalAuditPacket:
    """Everything the Final Auditor needs, from durable facts only.

    Assembled by the executor at ``READY_FOR_FINAL_AUDIT`` from the batch row,
    the plan record, the task rows and the repository fingerprint — never from
    a live conversation.  The packet explicitly instructs the auditor that the
    Batch Summary is *evidence*, the repository/diff/tests are the authority,
    and that it must NOT modify the repository (a read-only audit; any
    modification BLOCKS the final audit).
    """

    batch_id: str
    batch_title: str
    batch_objective: str
    project_brief: str
    #: Task titles exactly as planned (the original BatchPlan).
    planned_titles: Sequence[str]
    #: Per-task durable facts (title, prompt, criteria, focus, attempts,
    #: audit rounds, final verdict, builder/fix/auditor session ids).
    tasks: Sequence[Mapping[str, Any]]
    batch_summary_json: str
    builder_session_ids: Sequence[str]
    shared_task_auditor_session_id: str | None
    orchestrator_session_id: str | None
    baseline_head: str
    current_head: str
    workspace_path: str
    #: The exact number of next-batch tasks the auditor MUST return on PASS
    #: (4 or 5; the strict parser rejects any other count).
    next_batch_size: int
    extra_instructions: str = ""

    def render(self) -> str:
        from ..core.final_audit_packet import render_final_audit_prompt

        return render_final_audit_prompt(self)


@dataclass(slots=True)
class FinalAuditResult:
    """The one-call answer: cumulative verdict + (on PASS) the next plan.

    Constructed only by :func:`core.final_audit_parser.parse_final_audit`;
    every field is validated there before this object exists.
    """

    verdict: FinalVerdict
    summary: str
    findings: list[AuditFinding] = field(default_factory=list)
    #: ``batch_assessment`` from the contract — evidence flags recorded as-is
    #: (the parser requires them on every verdict).
    tests_verified: bool = False
    diff_verified: bool = False
    #: Present ONLY on PASS (exactly ``next_batch_size`` tasks).
    next_batch: BatchPlan | None = None

    @property
    def has_unresolved_defect(self) -> bool:
        return any(
            f.severity in (FindingSeverity.CRITICAL, FindingSeverity.HIGH)
            for f in self.findings
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "final_verdict": self.verdict.value,
            "summary": self.summary,
            "findings": [f.to_dict() for f in self.findings],
            "batch_assessment": {
                "tests_verified": self.tests_verified,
                "diff_verified": self.diff_verified,
            },
            "next_batch": (
                self.next_batch.to_dict() if self.next_batch is not None else None
            ),
        }

    def as_json(self) -> str:
        return json_dumps(self.to_dict())


def json_dumps(data: Mapping[str, Any]) -> str:
    """Local serialiser (keeps this module import-cycle-free)."""
    import json

    return json.dumps(data, ensure_ascii=False, sort_keys=True)
