"""Deterministic audit/fix prompt packets.

The Control Center assembles every prompt the Task Auditor and the fix Builder
receive.  Nothing here depends on a model, provider or driver: the packets are
plain text assembled from stored task state, and they instruct the agent to
inspect the **actual repository** rather than trust any prior narrative.

Two builders:

* :func:`render_audit_prompt` — the ``AuditPacket`` handed to ``TASK_AUDITOR``.
* :func:`render_fix_prompt` — handed to a **brand-new** ``BUILDER`` session;
  it carries the original task, the auditor's findings and the auditor's own
  ``fix_prompt``, and explicitly tells the Builder it must not rely on any
  prior Builder conversation.

Both packets include a hard workspace boundary: the agent may use tools only
inside the supervised repository path (the Session 003 smoke relies on this to
keep real model runs out of everything else on the machine).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..domain import AuditFinding, AuditVerdictResult
from .verdict_parser import (
    AUDIT_ENVELOPE_END,
    AUDIT_ENVELOPE_START,
    MAX_FINDINGS,
    MAX_FIX_PROMPT_CHARS,
    MAX_SUMMARY_CHARS,
    MAX_TEXT_CHARS,
)

__all__ = ["AuditPacket", "render_audit_prompt", "render_fix_prompt"]

#: The exact schema the auditor is asked to return (also the parser's contract).
AUDIT_OUTPUT_SCHEMA = {
    "verdict": "PASS | NEEDS_FIX | BLOCKED",
    "summary": "one or two sentences explaining the verdict",
    "findings": [
        {
            "severity": "critical | high | medium | low",
            "message": "what is wrong",
            "evidence": "file/line/test name or command output that proves it",
        }
    ],
    "fix_prompt": (
        "deterministic correction instructions for a fresh Builder session; "
        "MUST be non-empty for NEEDS_FIX, MUST be empty for PASS"
    ),
}

_COMMON_AGENT_RULES = (
    "- Inspect the ACTUAL repository state (files, git diff, tests) before you "
    "decide anything. Do not rely on anyone's summary of the work.\n"
    "- Restrict ALL tool use (file reads, edits, commands) to the workspace "
    "path given below. Never read, write or execute anything outside it.\n"
    "- Do not touch Git metadata outside the workspace, the Control Center "
    "source, any other repository on this machine, or Hermes configuration.\n"
    "- If a command fails, read its complete output and include the relevant "
    "error text in your evidence.\n"
    "- Answer only with the required output — no extra commentary."
)


@dataclass(frozen=True, slots=True)
class AuditPacket:
    """Everything a Task Auditor needs to audit ONE real task."""

    task_id: str
    title: str
    implementation_prompt: str
    workspace_path: str
    attempt: int = 0
    audit_round: int = 0
    batch_id: str = ""
    auditor_session_id: str | None = None
    #: Previous audit context (round > 1): lets the re-audit focus on the fix.
    previous: AuditVerdictResult | None = None
    #: Session 004: the Orchestrator's acceptance criteria / audit focus for
    #: THIS task, the task index, and the batch-level objective — so a fresh
    #: auditor session verifies the plan's contract, not a prior narrative.
    acceptance_criteria: tuple[str, ...] = ()
    audit_focus: tuple[str, ...] = ()
    task_index: int = 0
    batch_title: str = ""
    batch_objective: str = ""
    extra_instructions: str = ""

    def render(self) -> str:
        return render_audit_prompt(self)


def render_audit_prompt(packet: AuditPacket) -> str:
    """Assemble the deterministic Task Auditor prompt for ``packet``."""
    lines: list[str] = [
        "You are the TASK AUDITOR in a supervised pipeline. Your verdict is the "
        "only machine-checkable gate between this task and completion.",
        "",
        f"TASK ID: {packet.task_id}",
        f"TASK TITLE: {packet.title}",
        f"TASK INDEX: {packet.task_index or packet.task_id}",
        f"ATTEMPT: {packet.attempt or 1}",
        f"AUDIT ROUND: {packet.audit_round or 1}",
        f"BATCH: {packet.batch_id or '(none)'}",
    ]
    if packet.batch_title:
        lines.append(f"BATCH TITLE: {packet.batch_title}")
    if packet.batch_objective:
        lines.append(f"BATCH OBJECTIVE: {packet.batch_objective}")
    lines += [
        "",
        f"WORKSPACE (the ONLY path you may touch): {packet.workspace_path}",
        "",
        "ORIGINAL IMPLEMENTATION PROMPT:",
        packet.implementation_prompt.rstrip(),
        "",
    ]
    if packet.acceptance_criteria:
        lines += [
            "ACCEPTANCE CRITERIA (the Orchestrator's deterministic contract for "
            "this task — verify each one against the real repository):",
        ]
        for criterion in packet.acceptance_criteria:
            lines.append(f"- {criterion}")
        lines.append("")
    if packet.audit_focus:
        lines += [
            "AUDIT FOCUS (what this audit must independently check):",
        ]
        for focus in packet.audit_focus:
            lines.append(f"- {focus}")
        lines.append("")
    if packet.auditor_session_id:
        lines += [
            f"AUDITOR SESSION ID (this session): {packet.auditor_session_id}",
            "",
        ]
    if packet.previous is not None:
        lines += [
            "PREVIOUS AUDIT (same task, previous round):",
            f"  verdict  : {packet.previous.verdict.value}",
            f"  summary  : {packet.previous.summary}",
        ]
        for finding in packet.previous.findings:
            lines.append(
                f"  - [{finding.severity.value}] {finding.message}"
                + (f"  (evidence: {finding.evidence})" if finding.evidence else "")
            )
        if packet.previous.fix_prompt:
            lines.append("  fix_prompt given to the Builder:")
            lines.append("    " + packet.previous.fix_prompt.replace("\n", "\n    "))
        lines.append("")
    if packet.extra_instructions.strip():
        lines += [packet.extra_instructions.strip(), ""]

    lines += [
        "YOUR JOB — verify the REAL repository, not a report:",
        _COMMON_AGENT_RULES,
        "- Inspect the actual repository state: the source, the diff since the "
        "last audit, and the tests.",
        "- Run the relevant tests/checks yourself if you can, and read their "
        "complete output (failure output included).",
        "- Decide PASS only when the implementation genuinely satisfies the "
        "original implementation prompt (e.g. the tests deterministically pass).",
        "- Decide NEEDS_FIX when a safe, deterministic correction exists: write "
        "it in fix_prompt so a NEW Builder session with NO memory of previous "
        "sessions can perform it from this text alone (exact file, exact "
        "change, how to verify).",
        "- Decide BLOCKED when you cannot determine a safe correction or an "
        "external/manual decision is required.",
        "",
        "RETURN FORMAT — one JSON object and nothing else, delimited exactly:",
        AUDIT_ENVELOPE_START,
        json.dumps(AUDIT_OUTPUT_SCHEMA, indent=2),
        AUDIT_ENVELOPE_END,
        "",
        "RULES FOR THE VERDICT OBJECT:",
        f"- 'verdict' must be exactly: PASS | NEEDS_FIX | BLOCKED",
        "- 'findings': at most {MAX_FINDINGS} entries, each with severity one of "
        "critical/high/medium/low and non-empty 'message'",
        "- fix_prompt must be EMPTY for PASS; NON-EMPTY for NEEDS_FIX",
        "- a PASS with unresolved critical/high findings is invalid",
        f"- keep every string within the byte/time budget (summary <= "
        f"{MAX_SUMMARY_CHARS} chars, finding text <= {MAX_TEXT_CHARS} chars, "
        f"fix_prompt <= {MAX_FIX_PROMPT_CHARS} chars)",
        "- output the JSON object ONLY between the two markers; nothing before, "
        "nothing after.",
    ]
    return "\n".join(lines)


def render_fix_prompt(
    *,
    task_id: str,
    title: str,
    implementation_prompt: str,
    workspace_path: str,
    verdict: AuditVerdictResult,
    acceptance_criteria: Sequence[str] = (),
    extra_instructions: str = "",
) -> str:
    """Assemble the prompt for a BRAND-NEW Builder session performing the fix.

    The Builder must be able to perform the correction from this packet alone.
    """
    lines: list[str] = [
        "You are the BUILDER in a supervised pipeline, starting a BRAND-NEW "
        "session to correct one task. You have NO memory of any earlier "
        "session and you must not assume one: everything you need is in this "
        "packet and in the repository itself.",
        "",
        f"TASK ID: {task_id}",
        f"TASK TITLE: {title}",
        "",
        f"WORKSPACE (the ONLY path you may touch): {workspace_path}",
        "",
        "ORIGINAL IMPLEMENTATION PROMPT:",
        implementation_prompt.rstrip(),
        "",
    ]
    if acceptance_criteria:
        lines += ["ACCEPTANCE CRITERIA (the fix must satisfy all of these):"]
        for criterion in acceptance_criteria:
            lines.append(f"- {criterion}")
        lines.append("")
    lines += [
        "AUDITOR FINDINGS (why the task needs a fix):",
        f"  summary: {verdict.summary}",
    ]
    for finding in verdict.findings:
        lines.append(
            f"  - [{finding.severity.value}] {finding.message}"
            + (f"  (evidence: {finding.evidence})" if finding.evidence else "")
        )
    lines += [
        "",
        "REQUIRED CORRECTION (auditor-generated fix_prompt):",
        verdict.fix_prompt,
        "",
        "YOUR JOB:",
        _COMMON_AGENT_RULES,
        "- Re-read the CURRENT state of the repository first: the actual files "
        "may differ from the findings above.",
        "- Perform the required correction; adjust to reality if the repository "
        "changed, but do not change behaviour the original prompt did not ask for.",
        "- Run the relevant tests/checks and confirm they pass before you finish.",
        "- Report what you changed and the test result.",
    ]
    if extra_instructions.strip():
        lines += ["", extra_instructions.strip()]
    return "\n".join(lines) + "\n"