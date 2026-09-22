"""Strict audit-verdict parser: model output is untrusted, parsing fails closed.

Every test here is offline.  The point of the module is that *no* malformed
response — malformed JSON, a missing/invalid verdict, an oversize payload, a
NEEDS_FIX without a fix prompt, a PASS carrying an unresolved defect — can
produce a verdict object.  Anything wrong raises ``VerdictParseError`` and the
executor maps that to BLOCKED, never PASS.
"""

from __future__ import annotations

import json

import pytest

from encomm_pcc.core import (
    AUDIT_ENVELOPE_END,
    AUDIT_ENVELOPE_START,
    VerdictParseError,
    parse_audit_verdict,
)
from encomm_pcc.core.verdict_parser import (
    MAX_FINDINGS,
    MAX_FIX_PROMPT_CHARS,
    MAX_RAW_CHARS,
    MAX_SUMMARY_CHARS,
    MAX_TEXT_CHARS,
)
from encomm_pcc.domain import AuditVerdict, FindingSeverity


def envelope(payload: dict) -> str:
    return f"{AUDIT_ENVELOPE_START}\n{json.dumps(payload)}\n{AUDIT_ENVELOPE_END}"


def verdict(
    value: str = "PASS",
    *,
    summary: str = "ok",
    findings: list | None = None,
    fix_prompt: str = "",
) -> dict:
    return {
        "verdict": value,
        "summary": summary,
        "findings": findings or [],
        "fix_prompt": fix_prompt,
    }


# -- happy paths ----------------------------------------------------------------
def test_pass_verdict_parses() -> None:
    result = parse_audit_verdict(envelope(verdict("PASS")))
    assert result.verdict is AuditVerdict.PASS
    assert result.fix_prompt == ""
    assert result.findings == []


def test_needs_fix_verdict_parses() -> None:
    result = parse_audit_verdict(
        envelope(verdict("NEEDS_FIX", summary="calculator wrong", fix_prompt="fix add()"))
    )
    assert result.verdict is AuditVerdict.NEEDS_FIX
    assert result.fix_prompt == "fix add()"


def test_blocked_verdict_parses() -> None:
    result = parse_audit_verdict(envelope(verdict("BLOCKED", summary="manual decision needed")))
    assert result.verdict is AuditVerdict.BLOCKED


def test_findings_are_structured_and_bounded() -> None:
    payload = verdict(
        "NEEDS_FIX",
        summary="tests fail",
        findings=[
            {"severity": "critical", "message": "add returns the wrong result",
             "evidence": "calc_test.py:3"},
            {"severity": "low", "message": "style nit", "evidence": ""},
        ],
        fix_prompt="rewrite add()",
    )
    result = parse_audit_verdict(envelope(payload))
    assert len(result.findings) == 2
    assert result.findings[0].severity is FindingSeverity.CRITICAL
    assert result.findings[1].severity is FindingSeverity.LOW
    assert result.findings[1].evidence == ""


def test_balanced_braces_fallback_without_markers() -> None:
    """Prose chatter around ONE balanced object is tolerated (models do this)."""
    text = "Sure, here is the audit:\n" + json.dumps(verdict("PASS")) + "\nHope this helps."
    result = parse_audit_verdict(text)
    assert result.verdict is AuditVerdict.PASS


def test_json_fenced_payload_inside_markers() -> None:
    text = (
        f"{AUDIT_ENVELOPE_START}\n```json\n{json.dumps(verdict('PASS'))}\n```\n"
        f"{AUDIT_ENVELOPE_END}"
    )
    result = parse_audit_verdict(text)
    assert result.verdict is AuditVerdict.PASS


def test_extra_unknown_keys_are_ignored() -> None:
    payload = verdict("PASS")
    payload["extra_commentary"] = "this is ignored"
    result = parse_audit_verdict(envelope(payload))
    assert result.verdict is AuditVerdict.PASS


# -- malformed: fail closed, never PASS ------------------------------------------
def test_empty_output_raises() -> None:
    with pytest.raises(VerdictParseError) as exc:
        parse_audit_verdict("")
    assert exc.value.reason == "no_json_object"


def test_none_output_raises() -> None:
    with pytest.raises(VerdictParseError) as exc:
        parse_audit_verdict(None)  # type: ignore[arg-type]
    assert exc.value.reason == "empty_output"


def test_malformed_json_raises() -> None:
    text = (
        f"{AUDIT_ENVELOPE_START}\n"
        '{"verdict": "PASS", broken\n'
        f"{AUDIT_ENVELOPE_END}"
    )
    with pytest.raises(VerdictParseError) as exc:
        parse_audit_verdict(text)
    assert exc.value.reason in ("malformed_json", "no_json_object")


def test_missing_verdict_raises() -> None:
    with pytest.raises(VerdictParseError) as exc:
        parse_audit_verdict(envelope({"summary": "no verdict here"}))
    assert exc.value.reason == "missing_verdict"


def test_invalid_verdict_value_raises() -> None:
    with pytest.raises(VerdictParseError) as exc:
        parse_audit_verdict(envelope(verdict("MAYBE")))
    assert exc.value.reason == "invalid_verdict"


def test_verdict_as_number_raises() -> None:
    with pytest.raises(VerdictParseError) as exc:
        parse_audit_verdict(envelope({"verdict": 1}))
    assert exc.value.reason == "unexpected_type"


def test_top_level_array_raises() -> None:
    with pytest.raises(VerdictParseError) as exc:
        parse_audit_verdict(envelope([{"verdict": "PASS"}]))
    assert exc.value.reason == "unexpected_type"


def test_oversized_payload_raises() -> None:
    big = envelope(verdict("PASS")) + ("x" * (MAX_RAW_CHARS + 1))
    with pytest.raises(VerdictParseError) as exc:
        parse_audit_verdict(big)
    assert exc.value.reason == "oversized_payload"


def test_needs_fix_without_fix_prompt_raises() -> None:
    with pytest.raises(VerdictParseError) as exc:
        parse_audit_verdict(envelope(verdict("NEEDS_FIX")))
    assert exc.value.reason == "needs_fix_without_prompt"


def test_needs_fix_with_blank_fix_prompt_raises() -> None:
    with pytest.raises(VerdictParseError) as exc:
        parse_audit_verdict(envelope(verdict("NEEDS_FIX", fix_prompt="   ")))
    assert exc.value.reason == "needs_fix_without_prompt"


def test_pass_with_fix_prompt_raises() -> None:
    with pytest.raises(VerdictParseError) as exc:
        parse_audit_verdict(envelope(verdict("PASS", fix_prompt="unexpected fix text")))
    assert exc.value.reason == "pass_with_fix_prompt"


def test_pass_with_critical_finding_raises() -> None:
    payload = verdict("PASS", findings=[{"severity": "critical", "message": "still broken"}])
    with pytest.raises(VerdictParseError) as exc:
        parse_audit_verdict(envelope(payload))
    assert exc.value.reason == "pass_with_unresolved_defect"


def test_pass_with_high_finding_raises() -> None:
    payload = verdict("PASS", findings=[{"severity": "high", "message": "still broken"}])
    with pytest.raises(VerdictParseError) as exc:
        parse_audit_verdict(envelope(payload))
    assert exc.value.reason == "pass_with_unresolved_defect"


def test_pass_with_only_low_finding_is_accepted() -> None:
    payload = verdict("PASS", findings=[{"severity": "low", "message": "cosmetic"}])
    result = parse_audit_verdict(envelope(payload))
    assert result.verdict is AuditVerdict.PASS


# -- type / bound enforcement ----------------------------------------------------
def test_invalid_severity_raises() -> None:
    payload = verdict(
        "NEEDS_FIX",
        findings=[{"severity": "urgent", "message": "x"}],
        fix_prompt="fix",
    )
    with pytest.raises(VerdictParseError) as exc:
        parse_audit_verdict(envelope(payload))
    assert exc.value.reason == "invalid_severity"


def test_finding_without_message_raises() -> None:
    payload = verdict(
        "NEEDS_FIX", findings=[{"severity": "high"}], fix_prompt="fix"
    )
    with pytest.raises(VerdictParseError) as exc:
        parse_audit_verdict(envelope(payload))
    assert exc.value.reason in ("unexpected_type", "empty_field")


def test_findings_count_is_bounded() -> None:
    findings = [{"severity": "low", "message": f"f{i}"} for i in range(MAX_FINDINGS + 1)]
    with pytest.raises(VerdictParseError) as exc:
        parse_audit_verdict(envelope(verdict("NEEDS_FIX", findings=findings, fix_prompt="f")))
    assert exc.value.reason == "oversized_field"


def test_finding_message_length_is_bounded() -> None:
    payload = verdict(
        "NEEDS_FIX",
        findings=[{"severity": "low", "message": "m" * (MAX_TEXT_CHARS + 1)}],
        fix_prompt="fix",
    )
    with pytest.raises(VerdictParseError) as exc:
        parse_audit_verdict(envelope(payload))
    assert exc.value.reason == "oversized_field"


def test_summary_length_is_bounded() -> None:
    payload = verdict("PASS", summary="s" * (MAX_SUMMARY_CHARS + 1))
    with pytest.raises(VerdictParseError) as exc:
        parse_audit_verdict(envelope(payload))
    assert exc.value.reason == "oversized_field"


def test_fix_prompt_length_is_bounded() -> None:
    payload = verdict("NEEDS_FIX", fix_prompt="f" * (MAX_FIX_PROMPT_CHARS + 1))
    with pytest.raises(VerdictParseError) as exc:
        parse_audit_verdict(envelope(payload))
    assert exc.value.reason == "oversized_field"


# -- envelope hygiene -------------------------------------------------------------
def test_no_json_object_anywhere_raises() -> None:
    with pytest.raises(VerdictParseError) as exc:
        parse_audit_verdict("This is just prose. No verdict here. I failed.")
    assert exc.value.reason == "no_json_object"


def test_prose_before_markers_is_ignored() -> None:
    text = "Summary:\n" + envelope(verdict("PASS"))
    result = parse_audit_verdict(text)
    assert result.verdict is AuditVerdict.PASS


def test_partial_object_before_real_object_is_skipped() -> None:
    """A prose '{' fragment must not shadow the real verdict object."""
    text = (
        "the result was {\"verdict\": \"NEEDS_FIX\", \"summary\": \"old\"} "
        "but actually " + envelope(verdict("PASS"))
    )
    result = parse_audit_verdict(text)
    assert result.verdict is AuditVerdict.PASS