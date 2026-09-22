"""AuditPacket / fix-prompt determinism: the auditor and fix Builder receive
everything they need to inspect the REAL repository, and nothing model-shaped.
"""

from __future__ import annotations

from encomm_pcc.core.audit_packet import AuditPacket, render_audit_prompt, render_fix_prompt
from encomm_pcc.domain import AuditFinding, AuditVerdict, AuditVerdictResult, FindingSeverity
from encomm_pcc.core.verdict_parser import AUDIT_ENVELOPE_END, AUDIT_ENVELOPE_START


def sample_packet() -> AuditPacket:
    return AuditPacket(
        task_id="task_1234",
        title="Fix the calculator",
        implementation_prompt="Make the tests pass.",
        workspace_path=r"C:\scratch\simple-project",
        attempt=1,
        audit_round=1,
        batch_id="batch_abcd",
        auditor_session_id="auditor_session_9",
    )


def sample_verdict() -> AuditVerdictResult:
    return AuditVerdictResult(
        verdict=AuditVerdict.NEEDS_FIX,
        summary="The add function returns the wrong result.",
        findings=[
            AuditFinding(
                severity=FindingSeverity.CRITICAL,
                message="add(2, 3) returns -1 instead of 5",
                evidence="tests/test_calculator.py:7",
            )
        ],
        fix_prompt="In calculator.py, change add() to return a + b, then run the tests.",
    )


# -- AuditPacket ----------------------------------------------------------------
def test_packet_contains_task_identity() -> None:
    prompt = sample_packet().render()
    assert "task_1234" in prompt
    assert "Fix the calculator" in prompt
    assert "Make the tests pass." in prompt


def test_packet_contains_workspace_boundary() -> None:
    prompt = sample_packet().render()
    assert r"C:\scratch\simple-project" in prompt
    assert "ONLY path you may touch" in prompt


def test_packet_contains_round_and_session_context() -> None:
    prompt = sample_packet().render()
    assert "AUDIT ROUND: 1" in prompt
    assert "auditor_session_9" in prompt
    assert "batch_abcd" in prompt


def test_packet_instructs_real_inspection_not_self_report() -> None:
    prompt = sample_packet().render()
    assert "Inspect the ACTUAL repository state" in prompt
    assert "Run the relevant tests/checks" in prompt
    assert "not a report" in prompt or "not trust" in prompt


def test_packet_contains_strict_output_contract() -> None:
    prompt = sample_packet().render()
    assert AUDIT_ENVELOPE_START in prompt
    assert AUDIT_ENVELOPE_END in prompt
    assert '"verdict"' in prompt
    assert "PASS | NEEDS_FIX | BLOCKED" in prompt
    assert "fix_prompt must be EMPTY for PASS" in prompt
    assert "NON-EMPTY for NEEDS_FIX" in prompt


def test_packet_includes_previous_round_context_when_present() -> None:
    previous = sample_verdict()
    packet = AuditPacket(
        task_id="task_1234",
        title="Fix the calculator",
        implementation_prompt="Make the tests pass.",
        workspace_path=r"C:\scratch\simple-project",
        attempt=2,
        audit_round=2,
        previous=previous,
    )
    prompt = packet.render()
    assert "PREVIOUS AUDIT" in prompt
    assert "NEEDS_FIX" in prompt
    assert "add(2, 3) returns -1 instead of 5" in prompt
    assert "change add() to return a + b" in prompt


def test_packet_rendering_is_deterministic() -> None:
    assert sample_packet().render() == sample_packet().render()


# -- fix prompt -----------------------------------------------------------------
def test_fix_prompt_carries_original_task_and_verdict() -> None:
    prompt = render_fix_prompt(
        task_id="task_1234",
        title="Fix the calculator",
        implementation_prompt="Make the tests pass.",
        workspace_path=r"C:\scratch\simple-project",
        verdict=sample_verdict(),
    )
    assert "task_1234" in prompt
    assert "Make the tests pass." in prompt
    assert "add(2, 3) returns -1 instead of 5" in prompt
    assert "change add() to return a + b" in prompt
    assert r"C:\scratch\simple-project" in prompt


def test_fix_prompt_is_self_contained_for_a_fresh_session() -> None:
    prompt = render_fix_prompt(
        task_id="t1",
        title="T",
        implementation_prompt="p",
        workspace_path=r"C:\scratch\s",
        verdict=sample_verdict(),
    )
    assert "BRAND-NEW" in prompt and "NO memory" in prompt
    assert "everything you need is in this packet" in prompt
    assert "Re-read the CURRENT state of the repository first" in prompt
    assert "Run the relevant tests/checks" in prompt