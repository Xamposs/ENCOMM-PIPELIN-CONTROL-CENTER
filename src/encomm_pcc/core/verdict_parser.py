"""Strict parser for the structured audit verdict.

The Task Auditor's answer is **untrusted model output**.  This module is the
only place that turns that text into an :class:`AuditVerdictResult`, and it
fails closed: anything malformed raises :class:`VerdictParseError`, and the
executor maps every parse failure to ``BLOCKED`` — a malformed response can
never become ``PASS``.

Envelope.  The auditor prompt requires exactly one delimited JSON object::

    <<<AUDIT_VERDICT_START>>>
    { "verdict": "...", ... }
    <<<AUDIT_VERDICT_END>>>

The parser accepts that envelope, and as a fallback scans the text for the
first balanced ``{...}`` JSON object (models occasionally wrap the object in
prose even when told not to).  Surrounding Markdown is never trusted.

Hard rules implemented here (``docs/DECISIONS.md`` D-019):

* bounded input size before any parsing
* ``json.loads`` only — **no eval(), no exec(), no YAML**
* verdict must be exactly ``PASS``, ``NEEDS_FIX`` or ``BLOCKED``
* unexpected types are rejected; unknown extra keys are ignored
* findings count and every string length are bounded
* ``PASS``: ``fix_prompt`` must be empty AND no ``critical``/``high`` finding
* ``NEEDS_FIX``: ``fix_prompt`` must be present and non-empty
* ``BLOCKED``: whatever the auditor says (it is an explicit non-green state)
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from ..domain import (
    UNRESOLVED_SEVERITIES,
    VERDICTS,
    AuditFinding,
    AuditVerdict,
    AuditVerdictResult,
    FindingSeverity,
)

__all__ = [
    "AUDIT_ENVELOPE_END",
    "AUDIT_ENVELOPE_START",
    "MAX_FINDINGS",
    "MAX_FIX_PROMPT_CHARS",
    "MAX_RAW_CHARS",
    "MAX_SUMMARY_CHARS",
    "MAX_TEXT_CHARS",
    "VerdictParseError",
    "parse_audit_verdict",
]

#: Delimiter pair the auditor prompt instructs the model to use.
AUDIT_ENVELOPE_START = "<<<AUDIT_VERDICT_START>>>"
AUDIT_ENVELOPE_END = "<<<AUDIT_VERDICT_END>>>"

#: Bounds (PASS criteria say nothing may be unbounded in this path).
MAX_RAW_CHARS = 200_000      # the whole model answer we are willing to read
MAX_FINDINGS = 50           # findings per verdict
MAX_TEXT_CHARS = 20_000     # per finding message/evidence
MAX_SUMMARY_CHARS = 10_000  # verdict summary
MAX_FIX_PROMPT_CHARS = 50_000  # the deterministic correction handed to a Builder


class VerdictParseError(ValueError):
    """Raised when a model answer cannot become a strict audit verdict.

    ``reason`` is a short stable machine tag (used in tests and event logs);
    ``message`` is a bounded human-readable detail.
    """

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"[{reason}] {message}")


# --------------------------------------------------------------------------
# envelope extraction
# --------------------------------------------------------------------------
def _candidate_objects(raw: str) -> list[str]:
    """Return balanced-brace JSON candidates from ``raw``.

    Yields the marker-delimited payload first (when present), then every
    balanced ``{...}`` region in document order (first match wins in
    :func:`parse_audit_verdict`).  The scan is linear and bounded.
    """
    if AUDIT_ENVELOPE_START in raw and AUDIT_ENVELOPE_END in raw:
        inner = raw.split(AUDIT_ENVELOPE_START, 1)[1].split(AUDIT_ENVELOPE_END, 1)[0]
        # Strip an optional ```json fence around the payload.
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


# --------------------------------------------------------------------------
# schema validation (strict)
# --------------------------------------------------------------------------
def _bounded_str(value: Any, *, limit: int, field: str, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise VerdictParseError("unexpected_type", f"'{field}' must be a string")
    if not allow_empty and not value.strip():
        raise VerdictParseError("empty_field", f"'{field}' must not be empty")
    if len(value) > limit:
        raise VerdictParseError(
            "oversized_field",
            f"'{field}' exceeds {limit} characters ({len(value)})",
        )
    return value


def _validate_findings(raw: Any) -> list[AuditFinding]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise VerdictParseError(
            "unexpected_type", f"'findings' must be a list, got {type(raw).__name__}"
        )
    if len(raw) > MAX_FINDINGS:
        raise VerdictParseError(
            "oversized_field", f"'findings' exceeds {MAX_FINDINGS} entries ({len(raw)})"
        )
    findings: list[AuditFinding] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise VerdictParseError(
                "unexpected_type", f"each finding must be an object, got {type(item).__name__}"
            )
        severity_raw = item.get("severity")
        if not isinstance(severity_raw, str):
            raise VerdictParseError("unexpected_type", "finding 'severity' must be a string")
        severity_value = severity_raw.strip().lower()
        try:
            severity = FindingSeverity(severity_value)
        except ValueError as exc:
            raise VerdictParseError(
                "invalid_severity",
                f"finding severity must be one of critical/high/medium/low, got {severity_raw!r}",
            ) from exc
        message = _bounded_str(
            item.get("message"), limit=MAX_TEXT_CHARS, field="finding.message", allow_empty=False
        )
        evidence = _bounded_str(
            item.get("evidence", ""), limit=MAX_TEXT_CHARS, field="finding.evidence", allow_empty=True
        )
        findings.append(AuditFinding(severity=severity, message=message, evidence=evidence))
    return findings


def _validate(raw: Mapping[str, Any]) -> AuditVerdictResult:
    """Validate one parsed JSON object into a strict verdict."""
    verdict_raw = raw.get("verdict")
    if not isinstance(verdict_raw, str):
        raise VerdictParseError(
            "missing_verdict" if verdict_raw is None else "unexpected_type",
            f"'verdict' must be a string, got {type(verdict_raw).__name__}",
        )
    verdict_value = verdict_raw.strip().upper()
    if verdict_value not in VERDICTS:
        raise VerdictParseError(
            "invalid_verdict",
            f"'verdict' must be one of PASS/NEEDS_FIX/BLOCKED, got {verdict_raw!r}",
        )
    verdict = AuditVerdict(verdict_value)

    summary = _bounded_str(
        raw.get("summary", ""), limit=MAX_SUMMARY_CHARS, field="summary", allow_empty=True
    )
    findings = _validate_findings(raw.get("findings"))

    fix_prompt_raw = raw.get("fix_prompt")
    fix_prompt = ""
    if fix_prompt_raw is not None:
        fix_prompt = _bounded_str(
            fix_prompt_raw, limit=MAX_FIX_PROMPT_CHARS, field="fix_prompt", allow_empty=True
        )

    result = AuditVerdictResult(
        verdict=verdict, summary=summary, findings=findings, fix_prompt=fix_prompt
    )

    # Semantic rules — a technically-JSON payload can still be unacceptable.
    if verdict is AuditVerdict.PASS:
        if result.fix_prompt.strip():
            raise VerdictParseError(
                "pass_with_fix_prompt",
                "A PASS verdict must carry an empty fix_prompt.",
            )
        if result.has_unresolved_defect:
            raise VerdictParseError(
                "pass_with_unresolved_defect",
                "A PASS verdict cannot carry unresolved critical/high findings.",
            )
    if verdict is AuditVerdict.NEEDS_FIX and not result.fix_prompt.strip():
        raise VerdictParseError(
            "needs_fix_without_prompt",
            "A NEEDS_FIX verdict must carry a non-empty fix_prompt.",
        )
    return result


def parse_audit_verdict(raw: str) -> AuditVerdictResult:
    """Parse untrusted auditor output into a strict :class:`AuditVerdictResult`.

    Raises :class:`VerdictParseError` on any malformed input.  It never falls
    back to a lenient interpretation and never fabricates a verdict.
    """
    if raw is None:
        raise VerdictParseError("empty_output", "The auditor produced no output.")
    if not isinstance(raw, str):
        raise VerdictParseError(
            "unexpected_type", f"auditor output must be text, got {type(raw).__name__}"
        )
    if len(raw) > MAX_RAW_CHARS:
        raise VerdictParseError(
            "oversized_payload",
            f"auditor output exceeds {MAX_RAW_CHARS} characters ({len(raw)}); refusing to parse.",
        )

    last_error: VerdictParseError | None = None
    for candidate in _candidate_objects(raw):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            # Keep scanning — prose may contain partial objects; only a fully
            # valid AND schema-conformant object is accepted.
            last_error = VerdictParseError(
                "malformed_json", f"unparseable JSON object: {exc.msg}"
            )
            continue
        if not isinstance(parsed, Mapping):
            raise VerdictParseError(
                "unexpected_type",
                f"the verified JSON object must be an object, got {type(parsed).__name__}",
            )
        try:
            return _validate(parsed)
        except VerdictParseError as exc:
            last_error = exc
            # A dict that failed schema validation might be prose chatter; the
            # next candidate (if any) gets a chance, otherwise we fail closed.

    if last_error is not None:
        raise last_error
    raise VerdictParseError(
        "no_json_object",
        "No valid JSON object was found in the auditor output.",
    )