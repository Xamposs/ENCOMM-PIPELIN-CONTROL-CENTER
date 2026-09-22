"""Deterministic Orchestrator planning packet (Session 004).

The Control Center assembles exactly one planning prompt per batch.  It is
plain text assembled from durable batch state — the Project Brief and the
requested batch size — plus the workspace boundary.  The Orchestrator is told
it is planning-only (it must not edit the repository), and it is asked for one
strict JSON plan inside a delimited envelope that ``core/plan_parser.py``
parses as untrusted model output.
"""

from __future__ import annotations

from typing import Any, Sequence

from ..domain import PLAN_FOCUS_MAX, PLAN_ITEM_MAX, PLAN_MAX_TASKS, PLAN_PROMPT_MAX
from .plan_parser import PLAN_ENVELOPE_END, PLAN_ENVELOPE_START

__all__ = ["PLANNING_OUTPUT_SCHEMA", "render_planning_prompt"]

#: The exact schema the Orchestrator is asked to return (also the parser's
#: contract in ``core/plan_parser.py``).
PLANNING_OUTPUT_SCHEMA: dict[str, Any] = {
    "batch_title": "short title for the whole batch",
    "batch_objective": "one or two sentences describing what the batch achieves",
    "tasks": [
        {
            "index": 1,
            "title": "short unique task title",
            "implementation_prompt": (
                "self-contained instructions a fresh Builder session executes "
                "from this text alone against the actual repository"
            ),
            "acceptance_criteria": [
                "deterministic, machine-checkable requirement for this task"
            ],
            "audit_focus": [
                "what the Task Auditor must independently verify in the repo"
            ],
        }
    ],
}


def render_planning_prompt(
    *,
    project_brief: str,
    batch_size: int,
    workspace_path: str,
    extra_instructions: str = "",
) -> str:
    """Assemble the single planning call for one batch.

    ``batch_size`` must already be validated (1..``PLAN_MAX_TASKS``); the
    prompt hard-requires exactly that many tasks.
    """
    lines: list[str] = [
        "You are the ORCHESTRATOR in a supervised AI coding pipeline.",
        "",
        "YOUR ROLE IS PLANNING ONLY. You must NOT edit, create, delete or "
        "rename any file, and you must NOT run any command that changes the "
        "repository. You inspect the repository read-only and produce one "
        "structured plan as your ONLY output.",
        "",
        f"WORKSPACE (the ONLY path you may read): {workspace_path}",
        "",
        "PROJECT BRIEF:",
        (project_brief.strip() or "(no project brief supplied)"),
        "",
        f"REQUIRED TASK COUNT: exactly {batch_size} implementation tasks "
        f"(1..{PLAN_MAX_TASKS}). Return EXACTLY {batch_size} tasks — not one "
        "more, not one less. Each task index must be the contiguous numbers "
        "1..N with no gaps and no duplicates.",
        "",
        "PLAN ONLY — YOU DO NOT IMPLEMENT.",
        "- Inspect the ACTUAL repository state first (files, tests, structure, "
        "git log) so the plan matches reality.",
        "- Decompose the Project Brief into exactly N small, ordered, "
        "independent implementation tasks. Prefer deterministic, verifiable "
        "work over design debate.",
        "- Safe ordering: earlier tasks must not depend on later tasks' "
        "output, so each can be implemented and audited on its own.",
        "- Each 'implementation_prompt' must be SELF-CONTAINED: a brand-new "
        "Builder session with NO memory of this conversation must be able to "
        "execute task N from that text alone plus the real repository state.",
        "- Each 'acceptance_criteria' entry must be deterministic and "
        "machine-checkable (a specific test/command that passes, a specific "
        "behaviour assertable from the repo).",
        "- Each 'audit_focus' entry must tell the Task Auditor what to "
        "independently verify (inspect the source/diff, run the tests, read "
        "complete failure output).",
        "- Do NOT ask the Builder to do anything outside the workspace, and do "
        "not require network access or external services.",
        "",
        "CONSTRAINTS ON THE JSON:",
        f"- exactly {batch_size} tasks; indices contiguous from 1",
        "- non-empty, unique task titles",
        "- 'implementation_prompt' non-empty and concise (<= "
        f"{PLAN_PROMPT_MAX} chars per task)",
        f"- 'acceptance_criteria': non-empty list, each string <= {PLAN_ITEM_MAX} "
        "chars, at most 12 entries",
        f"- 'audit_focus': non-empty list, each string <= {PLAN_FOCUS_MAX} chars, "
        "at most 12 entries",
        f"- 'batch_title' and 'batch_objective' non-empty (title <= 120 chars)",
        "- nothing in this structure is a shell command; it is task text only",
        "- 'index' MUST be a plain JSON integer (1, 2, 3, …) — NEVER a quoted "
        "string ('1') and never text",
        "- the output MUST be strictly valid JSON: inside the markers, write "
        "all strings with correct JSON escaping — do NOT paste raw Windows "
        "backslash paths (like C:\\...) into any string; use forward slashes "
        "or no absolute paths at all. A single invalid escape rejects the "
        "whole plan and BLOCKS the batch.",
        "",
        "RETURN FORMAT — one JSON object and nothing else, delimited exactly:",
        PLAN_ENVELOPE_START,
        '{"batch_title": "...", "batch_objective": "...", "tasks": [...]}',
        PLAN_ENVELOPE_END,
        "",
        "Answers that are not exactly the required JSON inside the markers are "
        "rejected and the batch is BLOCKED — do not add prose, Markdown or "
        "explanations outside the markers.",
    ]
    if extra_instructions.strip():
        lines += ["", extra_instructions.strip()]
    return "\n".join(lines) + "\n"