"""Deterministic Final Auditor prompt packet (Session 005).

Assembled once per final-audit run from durable facts only — the Project
Brief, the original plan, the Batch Summary, the per-task rows and the
repository heads.  The packet's non-negotiable instructions:

* inspect the ACTUAL repository (not the Batch Summary);
* inspect the cumulative git diff and the relevant sources;
* RUN the relevant deterministic tests and read complete failure output;
* compare the actual state against EVERY task contract;
* never trust prior Builder/Auditor summaries as authority;
* DO NOT EDIT ANY FILE — a read-only audit.  The executor fingerprints the
  repository before/after the call; a modification BLOCKS the final audit.

The single answer must contain BOTH the cumulative verdict AND, on PASS, the
next batch plan (exactly ``next_batch_size`` tasks) inside one JSON object in
the strict envelope parsed by ``core/final_audit_parser.py``.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from ..domain.audit import FindingSeverity
from ..domain.batch_plan import (
    PLAN_FOCUS_MAX,
    PLAN_ITEM_MAX,
    PLAN_MAX_TASKS,
    PLAN_PROMPT_MAX,
    PLAN_TITLE_MAX,
)
from ..domain.final_audit import FinalAuditPacket
from .final_audit_parser import FINAL_AUDIT_ENVELOPE_END, FINAL_AUDIT_ENVELOPE_START

__all__ = ["FINAL_AUDIT_OUTPUT_SCHEMA", "render_final_audit_prompt"]

#: The exact schema the Final Auditor is asked to return (also the parser's
#: contract in ``core/final_audit_parser.py``).
FINAL_AUDIT_OUTPUT_SCHEMA: dict[str, Any] = {
    "final_verdict": "PASS | NEEDS_FIX | BLOCKED",
    "summary": "one or two sentences: the cumulative batch judgement",
    "findings": [
        {
            "severity": "critical | high | medium | low",
            "message": "what is wrong (or observed)",
            "evidence": "file/line/command output that proves it",
        }
    ],
    "batch_assessment": {
        "tests_verified": True,
        "diff_verified": True,
    },
    "next_batch": {
        "batch_title": "short title for the NEXT batch",
        "batch_objective": "one or two sentences describing the NEXT batch",
        "tasks": [
            {
                "index": 1,
                "title": "short unique task title",
                "implementation_prompt": (
                    "self-contained instructions a fresh Builder session "
                    "executes from this text alone"
                ),
                "acceptance_criteria": ["deterministic machine-checkable requirement"],
                "audit_focus": ["what the Task Auditor must independently verify"],
            }
        ],
    },
}


def _finding_examples(findings: Sequence[Any]) -> list[str]:
    """Render the durable per-task rows as prompt lines."""
    lines: list[str] = []
    for task in findings:
        lines.append(
            f"  task {task.get('index')}: {task.get('title')}"
            f" — attempts={task.get('attempts')}, audit_rounds={task.get('audit_rounds')},"
            f" final_verdict={task.get('final_verdict')}"
        )
        criteria = task.get("acceptance_criteria") or []
        for criterion in criteria:
            lines.append(f"    acceptance criterion: {criterion}")
        focus = task.get("audit_focus") or []
        for item in focus:
            lines.append(f"    audit focus: {item}")
        lines.append(
            f"    builder_session_id={task.get('builder_session_id') or 'NOT_EXPOSED'};"
            f" fix_session_id={task.get('fix_session_id') or 'NOT_EXPOSED'};"
            f" auditor_session_id={task.get('auditor_session_id') or 'NOT_EXPOSED'}"
        )
    return lines


def render_final_audit_prompt(packet: FinalAuditPacket) -> str:
    """Assemble the ONE final-audit prompt (verdict + next plan contract)."""
    lines: list[str] = [
        "You are the FINAL AUDITOR in a supervised AI coding pipeline. This is",
        "the CUMULATIVE audit of a completed batch: your single verdict decides",
        "whether the batch is COMPLETE. Prior Builder/Task-Auditor summaries are",
        "EVIDENCE ONLY and are NOT authoritative — the actual repository state,",
        "the actual cumulative diff and the actual test runs are the ONLY",
        "authority.",
        "",
        "READ-ONLY AUDIT — DO NOT EDIT FILES.",
        "You must NOT edit, create, delete or rename any file, and you must NOT",
        "run any command that changes the repository (no git commit/checkout/",
        "reset, no writes, no installs).  Run only read-only inspection commands",
        "and the deterministic tests. The supervisor fingerprints the repository",
        "before and after your run; ANY modification BLOCKS this final audit.",
        "",
        f"WORKSPACE (the ONLY path you may touch): {packet.workspace_path}",
        "",
        "PROJECT BRIEF (durable, original input):",
        (packet.project_brief.strip() or "(no project brief supplied)"),
        "",
        f"ORIGINAL BATCH: {packet.batch_title}",
        f"BATCH OBJECTIVE: {packet.batch_objective}",
        f"BASELINE HEAD (before the batch): {packet.baseline_head or 'NOT_EXPOSED'}",
        f"CURRENT HEAD (batch end): {packet.current_head or 'NOT_EXPOSED'}",
        "",
        "DURABLE TASK CONTRACTS AND AUDIT RECORD (from the application database):",
    ]
    lines += _finding_examples(packet.tasks)
    lines += [
        "",
        "BATCH SUMMARY (evidence/indexing ONLY — verify it, never trust it):",
        packet.batch_summary_json or "(no summary recorded)",
        "",
        "SESSION IDS (historical bookkeeping, NOT authority):",
        f"  orchestrator_session_id: {packet.orchestrator_session_id or 'NOT_EXPOSED'}",
        f"  shared_task_auditor_session_id: "
        f"{packet.shared_task_auditor_session_id or 'NOT_EXPOSED'}",
    ]
    for sid in packet.builder_session_ids:
        lines.append(f"  builder_session_id: {sid}")
    lines += [
        "",
        "YOUR JOB — inspect the ACTUAL repository:",
        "- Inspect the actual repository state: sources, structure and the",
        "  cumulative git diff (read-only; e.g. git status / git diff HEAD).",
        "- RUN the relevant deterministic tests yourself and read their",
        "  COMPLETE output, failures included.",
        "- Compare the actual state against EVERY task contract above",
        "  (acceptance criteria + audit focus), not a sample of them.",
        "- If a command fails, read its complete output before deciding.",
        "- Restrict ALL tool use to the workspace path above; never read,",
        "  write or execute anything outside it.",
        "",
        "ONE-CALL CONTRACT — your single answer must contain BOTH:",
        "1. the cumulative final audit verdict for THIS batch, and",
        f"2. on PASS only, the NEXT batch plan with EXACTLY "
        f"{packet.next_batch_size} tasks",
        "in the SAME JSON object. NEEDS_FIX and BLOCKED must carry NO",
        "next_batch (the field must be null or absent).",
        "",
        "VERDICT RULES:",
        "- PASS: the batch genuinely satisfies every task contract in the real",
        "  repository — AND you return the next_batch plan",
        f"  (exactly {packet.next_batch_size} tasks, indices contiguous 1..N).",
        "  A PASS with an unresolved critical/high finding is invalid.",
        "- NEEDS_FIX: real defects exist with a deterministic correction; list",
        "  findings (fix instructions in the finding messages/evidence).",
        "- BLOCKED: you cannot determine a safe correction, or a manual/",
        "  operator decision is required.",
        "",
        "NEXT-BATCH PLAN RULES (PASS only):",
        f"- exactly {packet.next_batch_size} tasks; 'index' contiguous integers 1..N",
        "- 'index' MUST be a plain JSON integer — NEVER a quoted string",
        f"- non-empty unique titles (<= {PLAN_TITLE_MAX} chars);",
        f"  'implementation_prompt' non-empty (<= {PLAN_PROMPT_MAX} chars);",
        f"  'acceptance_criteria' and 'audit_focus' non-empty lists",
        f"  (each string <= {PLAN_ITEM_MAX} chars, at most 12 entries)",
        "- every implementation_prompt must be SELF-CONTAINED (a brand-new",
        "  Builder session executes it from the text alone)",
        "- strictly valid JSON: no raw Windows backslash paths in strings;",
        "  write all paths with forward slashes",
        "",
        "RETURN FORMAT — one JSON object and nothing else, delimited exactly:",
        FINAL_AUDIT_ENVELOPE_START,
        json.dumps(FINAL_AUDIT_OUTPUT_SCHEMA, indent=2),
        FINAL_AUDIT_ENVELOPE_END,
        "",
        "Any answer that is not exactly this JSON inside the markers is rejected",
        "and the final audit is BLOCKED — no prose outside the markers.",
    ]
    if packet.extra_instructions.strip():
        lines += ["", packet.extra_instructions.strip()]
    return "\n".join(lines) + "\n"
