"""Strict parser for the Final Auditor's one-call answer (Session 005).

The Final Auditor's output is **untrusted model input**.  This module is the
only place that turns raw model text into a
:class:`~encomm_pcc.domain.final_audit.FinalAuditResult`, and it fails closed:
any malformed answer can NEVER become a PASS.

Envelope.  The final-audit prompt requires exactly one delimited object::

    <<<FINAL_AUDIT_START>>>
    { "final_verdict": "...", ..., "next_batch": {...} }
    <<<FINAL_AUDIT_END>>>

with a balanced-brace fallback (mirrors the verdict/plan parsers).

Hard rules:

* bounded raw input before any parsing
* ``json.loads`` only — no eval/exec/YAML, nothing executed
* ``final_verdict`` must be exactly PASS / NEEDS_FIX / BLOCKED
* ``batch_assessment.tests_verified`` / ``diff_verified`` must be JSON
  booleans on every verdict
* findings bounded and strictly shaped (severity whitelist, bounded strings)
* PASS: no unresolved critical/high finding AND a next_batch plan that
  validates against the SAME strict BatchPlan rules as the Orchestrator's
  plans (reuse of ``core/plan_parser._validate`` — no second, weaker parser)
  with EXACTLY ``expected_next_tasks`` tasks
* NEEDS_FIX: findings required; any next_batch is rejected
* BLOCKED: any next_batch is rejected
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from ..domain.audit import AuditFinding, FindingSeverity
from ..domain.batch_plan import PLAN_MAX_TASKS
from ..domain.final_audit import FINAL_VERDICTS, FinalAuditResult, FinalVerdict
from .plan_parser import PlanParseError, _validate as _validate_plan

__all__ = [
    "FINAL_AUDIT_ENVELOPE_END",
    "FINAL_AUDIT_ENVELOPE_START",
    "MAX_FINDINGS",
    "MAX_RAW_CHARS",
    "MAX_SUMMARY_CHARS",
    "MAX_TEXT_CHARS",
    "FinalAuditParseError",
    "parse_final_audit",
]

#: Delimiter pair the final-audit prompt instructs the model to use.
FINAL_AUDIT_ENVELOPE_START = "<<<FINAL_AUDIT_START>>>"
FINAL_AUDIT_ENVELOPE_END = "<<<FINAL_AUDIT_END>>>"

#: Bounds (same discipline as the verdict/plan parsers).
MAX_RAW_CHARS = 200_000      # the whole model answer we are willing to read
MAX_FINDINGS = 50            # findings per final verdict
MAX_TEXT_CHARS = 20_000      # per finding message/evidence
MAX_SUMMARY_CHARS = 10_000   # verdict summary


class FinalAuditParseError(ValueError):
    """Raised when a final-audit answer cannot become a strict result.

    ``reason`` is a short stable machine tag; the message is bounded human
    detail.  Every failure maps to a BLOCKED (never PASS) final audit.
    """

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"[{reason}] {message}")


def _candidate_objects(raw: str):
    """Envelope payload first, then every balanced ``{...}`` region."""
    if FINAL_AUDIT_ENVELOPE_START in raw and FINAL_AUDIT_ENVELOPE_END in raw:
        inner = raw.split(FINAL_AUDIT_ENVELOPE_START, 1)[1].split(
            FINAL_AUDIT_ENVELOPE_END, 1
        )[0]
        inner = inner.strip()
        if inner.startswith("```"):
            inner = inner.strip("`").strip()
            if inner.lower().startswith("json"):
                inner = inner[4:].strip()
        if inner:
            yield inner

    depth = 0
    start = -1
    for index, char in enumerate(raw):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                yield raw[start : index + 1]
                start = -1


def _bounded_str(value: Any, *, limit: int, field: str, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise FinalAuditParseError(
            "unexpected_type", f"'{field}' must be a string, got {type(value).__name__}"
        )
    if not allow_empty and not value.strip():
        raise FinalAuditParseError("empty_field", f"'{field}' must not be empty")
    if len(value) > limit:
        raise FinalAuditParseError(
            "oversized_field",
            f"'{field}' exceeds {limit} characters ({len(value)})",
        )
    return value


def _validate_findings(raw: Any) -> list[AuditFinding]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise FinalAuditParseError(
            "unexpected_type", f"'findings' must be a list, got {type(raw).__name__}"
        )
    if len(raw) > MAX_FINDINGS:
        raise FinalAuditParseError(
            "oversized_field", f"'findings' exceeds {MAX_FINDINGS} entries ({len(raw)})"
        )
    findings: list[AuditFinding] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise FinalAuditParseError(
                "unexpected_type",
                f"each finding must be an object, got {type(item).__name__}",
            )
        severity_raw = item.get("severity")
        if not isinstance(severity_raw, str):
            raise FinalAuditParseError(
                "unexpected_type", "finding 'severity' must be a string"
            )
        try:
            severity = FindingSeverity(severity_raw.strip().lower())
        except ValueError as exc:
            raise FinalAuditParseError(
                "invalid_severity",
                "finding severity must be one of critical/high/medium/low, "
                f"got {severity_raw!r}",
            ) from exc
        message = _bounded_str(
            item.get("message"),
            limit=MAX_TEXT_CHARS,
            field="finding.message",
            allow_empty=False,
        )
        evidence = _bounded_str(
            item.get("evidence", ""),
            limit=MAX_TEXT_CHARS,
            field="finding.evidence",
            allow_empty=True,
        )
        findings.append(AuditFinding(severity=severity, message=message, evidence=evidence))
    return findings


def _validate_assessment(raw: Any) -> tuple[bool, bool]:
    """``batch_assessment`` must carry two real booleans."""
    if not isinstance(raw, Mapping):
        raise FinalAuditParseError(
            "unexpected_type",
            f"'batch_assessment' must be an object, got {type(raw).__name__}",
        )
    flags: list[bool] = []
    for key in ("tests_verified", "diff_verified"):
        value = raw.get(key)
        if not isinstance(value, bool):
            raise FinalAuditParseError(
                "unexpected_type",
                f"'batch_assessment.{key}' must be a boolean, got {type(value).__name__}",
            )
        flags.append(value)
    return flags[0], flags[1]


def _validate(
    raw: Mapping[str, Any], *, expected_next_tasks: int
) -> FinalAuditResult:
    verdict_raw = raw.get("final_verdict")
    if not isinstance(verdict_raw, str):
        raise FinalAuditParseError(
            "missing_verdict" if verdict_raw is None else "unexpected_type",
            f"'final_verdict' must be a string, got {type(verdict_raw).__name__}",
        )
    verdict_value = verdict_raw.strip().upper()
    if verdict_value not in FINAL_VERDICTS:
        raise FinalAuditParseError(
            "invalid_final_verdict",
            f"'final_verdict' must be one of PASS/NEEDS_FIX/BLOCKED, got {verdict_raw!r}",
        )
    verdict = FinalVerdict(verdict_value)

    summary = _bounded_str(
        raw.get("summary", ""),
        limit=MAX_SUMMARY_CHARS,
        field="summary",
        allow_empty=True,
    )
    findings = _validate_findings(raw.get("findings"))
    tests_verified, diff_verified = _validate_assessment(raw.get("batch_assessment"))

    next_batch_raw = raw.get("next_batch")

    if verdict is FinalVerdict.PASS:
        result = FinalAuditResult(
            verdict=verdict,
            summary=summary,
            findings=findings,
            tests_verified=tests_verified,
            diff_verified=diff_verified,
        )
        if result.has_unresolved_defect:
            raise FinalAuditParseError(
                "pass_with_unresolved_defect",
                "A PASS final verdict cannot carry unresolved critical/high findings.",
            )
        # PASS REQUIRES a next_batch plan of EXACTLY the requested size,
        # validated by the SAME strict rules as an Orchestrator plan.
        if not isinstance(next_batch_raw, Mapping):
            raise FinalAuditParseError(
                "pass_without_next_batch",
                "A PASS final verdict must carry a next_batch plan object.",
            )
        if expected_next_tasks < 1 or expected_next_tasks > PLAN_MAX_TASKS:
            raise FinalAuditParseError(
                "invalid_next_batch_size",
                f"next batch size must be 1..{PLAN_MAX_TASKS}, got {expected_next_tasks}",
            )
        try:
            result.next_batch = _validate_plan(
                dict(next_batch_raw), expected_count=expected_next_tasks
            )
        except PlanParseError as exc:
            raise FinalAuditParseError(
                f"malformed_next_batch_{exc.reason}", f"next_batch plan rejected: {exc}"
            ) from exc
        return result

    # NEEDS_FIX: findings required; next_batch forbidden.
    if verdict is FinalVerdict.NEEDS_FIX:
        if not findings:
            raise FinalAuditParseError(
                "needs_fix_without_findings",
                "A NEEDS_FIX final verdict must carry at least one finding.",
            )
        if next_batch_raw is not None:
            raise FinalAuditParseError(
                "needs_fix_with_next_batch",
                "A NEEDS_FIX final verdict must NOT carry a next_batch plan.",
            )
        return FinalAuditResult(
            verdict=verdict,
            summary=summary,
            findings=findings,
            tests_verified=tests_verified,
            diff_verified=diff_verified,
        )

    # BLOCKED: no next batch, operator decision required.
    if next_batch_raw is not None:
        raise FinalAuditParseError(
            "blocked_with_next_batch",
            "A BLOCKED final verdict must NOT carry a next_batch plan.",
        )
    return FinalAuditResult(
        verdict=verdict,
        summary=summary,
        findings=findings,
        tests_verified=tests_verified,
        diff_verified=diff_verified,
    )


def parse_final_audit(
    raw: str, *, expected_next_tasks: int
) -> FinalAuditResult:
    """Parse untrusted Final Auditor output into a strict result.

    ``expected_next_tasks`` is the operator's next-batch size (4 or 5); a PASS
    whose plan has any other task count is rejected.  Raises
    :class:`FinalAuditParseError` on any malformed input — a malformed result
    can never become PASS.
    """
    if raw is None:
        raise FinalAuditParseError("empty_output", "The Final Auditor produced no output.")
    if not isinstance(raw, str):
        raise FinalAuditParseError(
            "unexpected_type", f"final-audit output must be text, got {type(raw).__name__}"
        )
    if len(raw) > MAX_RAW_CHARS:
        raise FinalAuditParseError(
            "oversized_payload",
            f"final-audit output exceeds {MAX_RAW_CHARS} characters ({len(raw)}); "
            "refusing to parse.",
        )

    last_error: FinalAuditParseError | None = None
    for candidate in _candidate_objects(raw):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = FinalAuditParseError(
                "malformed_json", f"unparseable JSON object: {exc.msg}"
            )
            continue
        if not isinstance(parsed, Mapping):
            raise FinalAuditParseError(
                "unexpected_type",
                f"the verified JSON object must be an object, got {type(parsed).__name__}",
            )
        try:
            return _validate(parsed, expected_next_tasks=expected_next_tasks)
        except FinalAuditParseError as exc:
            last_error = exc
            # A dict that failed validation might be prose chatter; the next
            # candidate (if any) gets a chance, otherwise fail closed.

    if last_error is not None:
        raise last_error
    raise FinalAuditParseError(
        "no_json_object",
        "No valid JSON object was found in the Final Auditor output.",
    )
