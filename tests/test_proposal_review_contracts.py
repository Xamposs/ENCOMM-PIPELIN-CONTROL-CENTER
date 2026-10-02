"""Session 013 — Proposal review CONTRACT tests (packet / fingerprint / parser).

Covers the mandated matrix sections:

* REVIEW PACKET (1-5): deterministic rendering, role-specific instructions,
  reviewer read-only rule, source-of-truth protection, honest official-
  requirements unavailability.
* FINGERPRINT (6-8): identical bytes → identical SHA-256; one byte change →
  different hash; missing proposal → explicit failure.
* PARSER (9-22): valid PASS / NEEDS_REVISION / BLOCKED; malformed JSON;
  missing envelope; duplicate envelope; invalid role; role mismatch;
  iteration mismatch; bad severity; malformed patch; PASS with HIGH/CRITICAL
  finding; PASS with unverified claim; oversized arrays/strings — every
  rejection is a TYPED failure, never a fabricated result.

All tests are OFFLINE and deterministic: no network, no engine, no provider.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import encomm_pcc.proposal as pp

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"

PROPOSAL_TEXT = (
    "# MASTER PROPOSAL\n\n"
    "## 1. Excellence\n\nWe propose a novel quantum-adjacent methodology.\n"
    "## 2. Impact\n\nThe results will be disseminated widely.\n"
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def make_inputs(**overrides: object) -> pp.ProposalReviewInputs:
    defaults: dict[str, object] = {
        "proposal_text": PROPOSAL_TEXT,
        "iteration_number": 1,
        "proposal_revision": "rev-1",
        "proposal_hash": "a" * 64,
    }
    defaults.update(overrides)
    return pp.ProposalReviewInputs(**defaults)


def make_packet(
    role: pp.ProposalRole = pp.ProposalRole.SCIENTIFIC_REVIEWER,
    **overrides: object,
) -> pp.ProposalReviewPacket:
    return pp.build_review_packet(role, make_inputs(**overrides))


def payload(
    *,
    role: str = "SCIENTIFIC_REVIEWER",
    verdict: str = "PASS",
    iteration: int = 1,
    findings: list | None = None,
    patches: list | None = None,
    claims: list | None = None,
    summary: str = "Clean review.",
) -> dict:
    return {
        "reviewer_role": role,
        "verdict": verdict,
        "summary": summary,
        "findings": findings or [],
        "proposed_patches": patches or [],
        "unverified_claims": claims or [],
        "iteration_number": iteration,
    }


def finding(
    severity: str = "medium",
    category: str = "weak_wording",
    message: str = "Section 2 overstates the impact.",
) -> dict:
    return {
        "severity": severity,
        "category": category,
        "section": "2. Impact",
        "message": message,
        "evidence": "",
        "source_refs": [],
        "suggested_change": "",
    }


def envelope(body: str) -> str:
    return (
        f"{pp.PROPOSAL_REVIEW_ENVELOPE_START}\n{body}\n"
        f"{pp.PROPOSAL_REVIEW_ENVELOPE_END}"
    )


def parse(raw: str, *, role=pp.ProposalRole.SCIENTIFIC_REVIEWER, iteration: int = 1):
    return pp.parse_proposal_review(raw, expected_role=role, expected_iteration=iteration)


# ---------------------------------------------------------------------------
# REVIEW PACKET
# ---------------------------------------------------------------------------
class TestReviewPacket:
    def test_1_rendering_is_deterministic(self) -> None:
        first = make_packet()
        second = make_packet()
        assert first == second
        assert first.prompt_text == second.prompt_text
        # Identical inputs, byte-identical prompt.
        assert hash(first) == hash(second)

    def test_1_changed_inputs_change_the_prompt(self) -> None:
        base = make_packet()
        other = make_packet(iteration_number=2)
        assert base.prompt_text != other.prompt_text

    def test_role_specific_instructions_are_embedded(self) -> None:
        scientific = make_packet(pp.ProposalRole.SCIENTIFIC_REVIEWER)
        engineer = make_packet(pp.ProposalRole.PROPOSAL_ENGINEER)
        red_team = make_packet(pp.ProposalRole.RED_TEAM_REVIEWER)
        assert "SCIENTIFIC_REVIEWER" in scientific.prompt_text
        assert "novelty" in scientific.prompt_text
        assert "PROPOSAL_ENGINEER" in engineer.prompt_text
        assert "person-month" in engineer.prompt_text
        assert "RED_TEAM_REVIEWER" in red_team.prompt_text
        assert "hostile" in red_team.prompt_text
        # Each prompt carries exactly its own focus.
        assert "ROLE FOCUS — SCIENTIFIC_REVIEWER" not in engineer.prompt_text
        assert "ROLE FOCUS — PROPOSAL_ENGINEER" not in scientific.prompt_text

    def test_3_reviewer_read_only_rule_is_present(self) -> None:
        prompt = make_packet().prompt_text
        assert "READ-ONLY" in prompt
        assert "MUST NOT edit 03_PROPOSAL/MASTER_PROPOSAL.md" in prompt
        assert "NO integration authority" in prompt
        assert "patch PROPOSALS only" in prompt

    def test_4_source_of_truth_protection_is_present(self) -> None:
        prompt = make_packet().prompt_text
        assert "must not be invented or silently changed" in prompt
        assert "marked as unverified" in prompt
        assert "never be invented" in prompt  # citations rule
        for category in (
            "factual_contradiction",
            "missing_evidence",
            "weak_wording",
            "structural_issue",
            "recommendation",
        ):
            assert category in prompt

    def test_5_official_requirements_unavailable_is_represented_honestly(self) -> None:
        prompt = make_packet().prompt_text  # defaults: official requirements absent
        assert "[UNAVAILABLE" in prompt
        assert "CANNOT claim full official/Challenge compliance" in prompt

    def test_official_requirements_available_is_rendered(self) -> None:
        prompt = make_packet(
            official_requirements_text="# Challenge\nMax page count: 40.",
            official_requirements_available=True,
        ).prompt_text
        assert "OFFICIAL REQUIREMENTS (provided)" in prompt
        assert "Max page count: 40." in prompt

    def test_available_flag_requires_text(self) -> None:
        with pytest.raises(ValueError):
            make_packet(official_requirements_available=True)

    def test_orchestrator_is_rejected_as_reviewer(self) -> None:
        with pytest.raises(ValueError, match="integration authority"):
            make_packet(pp.ProposalRole.ORCHESTRATOR)

    def test_missing_proposal_text_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="proposal_text"):
            make_packet(proposal_text="   ")

    def test_iteration_number_must_be_positive_int(self) -> None:
        for bad in (0, -1, "1", True, 1.5):
            with pytest.raises(ValueError):
                make_packet(iteration_number=bad)  # type: ignore[arg-type]

    def test_oversized_section_is_refused(self) -> None:
        with pytest.raises(ValueError, match="exceeds"):
            make_packet(
                project_facts_text="x" * (pp.review_packet.MAX_PACKET_SECTION_CHARS + 1)
            )

    def test_previous_findings_and_extra_instructions_render(self) -> None:
        prompt = make_packet(
            previous_findings=("Iteration 1: fix section 2.",),
            extra_instructions="Be extra strict about the budget table.",
        ).prompt_text
        assert "Iteration 1: fix section 2." in prompt
        assert "Be extra strict about the budget table." in prompt

    def test_envelope_markers_are_part_of_the_prompt_contract(self) -> None:
        prompt = make_packet().prompt_text
        assert pp.PROPOSAL_REVIEW_ENVELOPE_START in prompt
        assert pp.PROPOSAL_REVIEW_ENVELOPE_END in prompt
        assert '"iteration_number"' in prompt

    def test_packet_carries_metadata(self) -> None:
        packet = make_packet(iteration_number=3, proposal_hash="b" * 64)
        assert packet.iteration_number == 3
        assert packet.proposal_hash == "b" * 64
        assert packet.role is pp.ProposalRole.SCIENTIFIC_REVIEWER


# ---------------------------------------------------------------------------
# FINGERPRINT
# ---------------------------------------------------------------------------
class TestProposalFingerprint:
    def test_6_identical_bytes_produce_identical_hash(self, tmp_path: Path) -> None:
        target = tmp_path / "MASTER_PROPOSAL.md"
        target.write_bytes(PROPOSAL_TEXT.encode("utf-8"))
        first = pp.proposal_fingerprint(target)
        second = pp.proposal_fingerprint(target)
        assert first == second
        assert first == hashlib.sha256(PROPOSAL_TEXT.encode("utf-8")).hexdigest()

    def test_hash_is_lowercase_hex_sha256(self, tmp_path: Path) -> None:
        target = tmp_path / "MASTER_PROPOSAL.md"
        target.write_bytes(b"determinism check")
        digest = pp.proposal_fingerprint(target)
        assert re.fullmatch(r"[0-9a-f]{64}", digest)

    def test_7_single_byte_change_changes_the_hash(self, tmp_path: Path) -> None:
        target = tmp_path / "MASTER_PROPOSAL.md"
        target.write_bytes(b"proposal body v1")
        before = pp.proposal_fingerprint(target)
        target.write_bytes(b"proposal body v2")  # one byte differs
        after = pp.proposal_fingerprint(target)
        assert before != after

    def test_no_newline_normalisation(self, tmp_path: Path) -> None:
        lf = tmp_path / "lf.md"
        crlf = tmp_path / "crlf.md"
        lf.write_bytes(b"line one\nline two\n")
        crlf.write_bytes(b"line one\r\nline two\r\n")
        assert pp.proposal_fingerprint(lf) != pp.proposal_fingerprint(crlf)

    def test_8_missing_proposal_fails_explicitly(self, tmp_path: Path) -> None:
        with pytest.raises(pp.ProposalFingerprintError) as excinfo:
            pp.proposal_fingerprint(tmp_path / "absent.md")
        assert excinfo.value.reason == "missing_file"

    def test_directory_path_fails_explicitly(self, tmp_path: Path) -> None:
        with pytest.raises(pp.ProposalFingerprintError) as excinfo:
            pp.proposal_fingerprint(tmp_path)
        assert excinfo.value.reason == "not_a_file"

    def test_oversized_file_is_refused(self, tmp_path: Path) -> None:
        target = tmp_path / "huge.md"
        target.write_bytes(b"x" * (pp.fingerprint.MAX_PROPOSAL_BYTES + 1))
        with pytest.raises(pp.ProposalFingerprintError) as excinfo:
            pp.proposal_fingerprint(target)
        assert excinfo.value.reason == "oversized_file"

    def test_algorithm_identifier_is_stable(self) -> None:
        assert pp.PROPOSAL_HASH_ALGORITHM == "sha256-exact-bytes-v1"


# ---------------------------------------------------------------------------
# PARSER — valid reviews
# ---------------------------------------------------------------------------
class TestParserValid:
    def test_9_valid_pass(self) -> None:
        result = parse(envelope(json.dumps(payload(verdict="PASS"))))
        assert result.verdict is pp.ProposalReviewVerdict.PASS
        assert result.reviewer_role is pp.ProposalRole.SCIENTIFIC_REVIEWER
        assert result.iteration_number == 1
        assert result.findings == []
        assert result.proposed_patches == []
        assert result.unverified_claims == []
        assert not result.has_blocking_findings

    def test_10_valid_needs_revision(self) -> None:
        body = payload(
            verdict="NEEDS_REVISION",
            findings=[finding(severity="medium")],
        )
        result = parse(envelope(json.dumps(body)))
        assert result.verdict is pp.ProposalReviewVerdict.NEEDS_REVISION
        assert len(result.findings) == 1
        assert result.findings[0].category == "weak_wording"

    def test_11_valid_blocked(self) -> None:
        body = payload(
            verdict="BLOCKED",
            summary="Official requirements missing; compliance cannot be assessed.",
        )
        result = parse(envelope(json.dumps(body)))
        assert result.verdict is pp.ProposalReviewVerdict.BLOCKED

    def test_blocked_via_finding_reason(self) -> None:
        body = payload(
            verdict="BLOCKED",
            summary="",
            findings=[finding(severity="high", category="missing_evidence",
                              message="No evidence base at all.")],
        )
        assert parse(envelope(json.dumps(body))).verdict is pp.ProposalReviewVerdict.BLOCKED

    def test_3_prose_around_correct_envelope_is_accepted(self) -> None:
        # Session 013A: prose before/after a CORRECT envelope stays tolerated
        # — the one exact envelope is the authoritative payload.  This is NOT
        # a brace rescue: nothing outside the envelope is ever scanned.
        raw = (
            "Here is my review:\n\n"
            + envelope(json.dumps(payload(verdict="PASS")))
            + "\nThanks."
        )
        assert parse(raw).verdict is pp.ProposalReviewVerdict.PASS

    def test_verdict_is_case_normalized_but_role_and_iteration_strict(self) -> None:
        body = payload(verdict="pass")
        assert parse(envelope(json.dumps(body))).verdict is pp.ProposalReviewVerdict.PASS

    def test_full_finding_and_patch_fields_round_trip(self) -> None:
        body = payload(
            verdict="NEEDS_REVISION",
            findings=[
                {
                    "severity": "high",
                    "category": "factual_contradiction",
                    "section": "3. Budget",
                    "message": "Person-months contradict TEAM.md.",
                    "evidence": "12 PM vs 8 PM",
                    "source_refs": ["00_SOURCE_OF_TRUTH/TEAM.md"],
                    "suggested_change": "Reconcile to 8 PM.",
                }
            ],
            patches=[
                {
                    "target_section": "3. Budget",
                    "rationale": "Consistency with TEAM.md",
                    "replacement_text": "Total: 8 person-months.",
                    "patch_instructions": "",
                    "source_refs": ["00_SOURCE_OF_TRUTH/TEAM.md"],
                    "confidence": 0.9,
                }
            ],
            claims=["Novel method TRL-9 proven."],
        )
        result = parse(envelope(json.dumps(body)))
        assert result.findings[0].severity is pp.ProposalFindingSeverity.HIGH
        assert result.proposed_patches[0].confidence == pytest.approx(0.9)
        assert result.unverified_claims == ["Novel method TRL-9 proven."]
        # to_dict round-trip (JSON-friendly contract).
        assert pp.ProposalReviewResult.from_dict(result.to_dict()) == result


# ---------------------------------------------------------------------------
# PARSER — fail-closed rejections
# ---------------------------------------------------------------------------
class TestParserFailClosed:
    def test_12_malformed_json_rejected(self) -> None:
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(envelope("{not json}"))
        assert excinfo.value.reason == "malformed_json"

    def test_13_prose_without_envelope_rejected(self) -> None:
        # Session 013A: plain prose with zero envelope markers is
        # `missing_envelope` — there is no balanced-brace rescue anymore.
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse("I reviewed the proposal and it looks great, PASS!")
        assert excinfo.value.reason == "missing_envelope"

    def test_empty_output_rejected(self) -> None:
        # An empty answer carries no envelope → missing_envelope.
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse("")
        assert excinfo.value.reason == "missing_envelope"

    def test_non_object_root_rejected(self) -> None:
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(envelope(json.dumps(["PASS"])))
        assert excinfo.value.reason == "unexpected_type"

    def test_14_duplicate_envelope_rejected(self) -> None:
        good = envelope(json.dumps(payload(verdict="PASS")))
        duplicated = good + "\n" + good
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(duplicated)
        assert excinfo.value.reason == "multiple_envelopes"

    def test_13_unterminated_envelope_rejected(self) -> None:
        body = json.dumps(payload(verdict="PASS"))
        start_only = f"{pp.PROPOSAL_REVIEW_ENVELOPE_START}\n{body}\n"
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(start_only)
        assert excinfo.value.reason == "unterminated_envelope"
        end_only = f"{body}\n{pp.PROPOSAL_REVIEW_ENVELOPE_END}"
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(end_only)
        assert excinfo.value.reason == "unterminated_envelope"

    def test_15_invalid_role_rejected(self) -> None:
        body = payload(role="ORCHESTRATOR")
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(envelope(json.dumps(body)))
        assert excinfo.value.reason == "invalid_reviewer_role"

    def test_15_unknown_role_rejected(self) -> None:
        body = payload(role="SUPER_REVIEWER")
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(envelope(json.dumps(body)))
        assert excinfo.value.reason == "invalid_reviewer_role"

    def test_16_role_mismatch_rejected(self) -> None:
        body = payload(role="PROPOSAL_ENGINEER")
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(envelope(json.dumps(body)))  # expected: SCIENTIFIC_REVIEWER
        assert excinfo.value.reason == "reviewer_role_mismatch"

    def test_17_iteration_mismatch_rejected(self) -> None:
        body = payload(iteration=2)
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(envelope(json.dumps(body)))
        assert excinfo.value.reason == "iteration_mismatch"

    def test_17_quoted_iteration_rejected(self) -> None:
        body = payload(iteration="1")  # type: ignore[dict-item]
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(envelope(json.dumps(body)))
        assert excinfo.value.reason == "unexpected_type"

    def test_18_bad_severity_rejected(self) -> None:
        body = payload(
            verdict="NEEDS_REVISION",
            findings=[finding(severity="catastrophic")],
        )
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(envelope(json.dumps(body)))
        assert excinfo.value.reason == "invalid_severity"

    def test_invalid_category_rejected(self) -> None:
        body = payload(
            verdict="NEEDS_REVISION",
            findings=[finding(category="vibes")],
        )
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(envelope(json.dumps(body)))
        assert excinfo.value.reason == "invalid_category"

    def test_finding_without_message_rejected(self) -> None:
        body = payload(
            verdict="NEEDS_REVISION",
            findings=[{"severity": "low", "category": "weak_wording"}],
        )
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(envelope(json.dumps(body)))
        assert excinfo.value.reason == "unexpected_type"

    def test_19_malformed_patch_rejected(self) -> None:
        body = payload(
            verdict="NEEDS_REVISION",
            findings=[finding()],
            patches=[{"target_section": "2", "rationale": "why"}],  # no content
        )
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(envelope(json.dumps(body)))
        assert excinfo.value.reason == "ambiguous_patch"

    def test_19_patch_with_both_channels_rejected(self) -> None:
        body = payload(
            verdict="NEEDS_REVISION",
            findings=[finding()],
            patches=[
                {
                    "target_section": "2",
                    "rationale": "why",
                    "replacement_text": "new text",
                    "patch_instructions": "also do this",
                }
            ],
        )
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(envelope(json.dumps(body)))
        assert excinfo.value.reason == "ambiguous_patch"

    def test_19_patch_without_target_rejected(self) -> None:
        body = payload(
            verdict="NEEDS_REVISION",
            findings=[finding()],
            patches=[{"rationale": "why", "patch_instructions": "do X"}],
        )
        # ABSENT key → unexpected_type (repo convention: missing_field/absent
        # keys are unexpected_type, never silently coerced to empty).
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(envelope(json.dumps(body)))
        assert excinfo.value.reason == "unexpected_type"

    def test_19_patch_with_empty_target_rejected(self) -> None:
        body = payload(
            verdict="NEEDS_REVISION",
            findings=[finding()],
            patches=[{"target_section": "  ", "rationale": "why", "patch_instructions": "do X"}],
        )
        # PRESENT but blank → empty_field.
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(envelope(json.dumps(body)))
        assert excinfo.value.reason == "empty_field"

    def test_patch_confidence_out_of_range_rejected(self) -> None:
        body = payload(
            verdict="NEEDS_REVISION",
            findings=[finding()],
            patches=[
                {
                    "target_section": "2",
                    "rationale": "why",
                    "patch_instructions": "do X",
                    "confidence": 1.5,
                }
            ],
        )
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(envelope(json.dumps(body)))
        assert excinfo.value.reason == "invalid_confidence"

    def test_20_pass_with_high_finding_rejected(self) -> None:
        body = payload(verdict="PASS", findings=[finding(severity="high")])
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(envelope(json.dumps(body)))
        assert excinfo.value.reason == "pass_with_blocking_findings"

    def test_20_pass_with_critical_finding_rejected(self) -> None:
        body = payload(verdict="PASS", findings=[finding(severity="critical")])
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(envelope(json.dumps(body)))
        assert excinfo.value.reason == "pass_with_blocking_findings"

    def test_21_pass_with_unverified_claim_rejected(self) -> None:
        body = payload(verdict="PASS", claims=["TRL-9 achieved."])
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(envelope(json.dumps(body)))
        assert excinfo.value.reason == "pass_with_unverified_claims"

    def test_needs_revision_without_actionables_rejected(self) -> None:
        body = payload(verdict="NEEDS_REVISION")
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(envelope(json.dumps(body)))
        assert excinfo.value.reason == "needs_revision_without_actionables"

    def test_blocked_without_reason_rejected(self) -> None:
        body = payload(verdict="BLOCKED", summary="")
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(envelope(json.dumps(body)))
        assert excinfo.value.reason == "blocked_without_reason"

    def test_22_oversized_payload_rejected(self) -> None:
        raw = "x" * (pp.review_parser.MAX_RAW_CHARS + 1)
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(raw)
        assert excinfo.value.reason == "oversized_payload"

    def test_22_oversized_findings_list_rejected(self) -> None:
        body = payload(
            verdict="NEEDS_REVISION",
            findings=[finding() for _ in range(pp.review_parser.MAX_FINDINGS + 1)],
        )
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(envelope(json.dumps(body)))
        assert excinfo.value.reason == "oversized_list"

    def test_22_oversized_patches_list_rejected(self) -> None:
        patch = {
            "target_section": "s",
            "rationale": "r",
            "patch_instructions": "i",
        }
        body = payload(
            verdict="NEEDS_REVISION",
            findings=[finding()],
            patches=[patch] * (pp.review_parser.MAX_PATCHES + 1),
        )
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(envelope(json.dumps(body)))
        assert excinfo.value.reason == "oversized_list"

    def test_22_oversized_string_rejected(self) -> None:
        body = payload(
            verdict="NEEDS_REVISION",
            findings=[finding(message="x" * (pp.review_parser.MAX_STRING_CHARS + 1))],
        )
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(envelope(json.dumps(body)))
        assert excinfo.value.reason == "oversized_field"

    def test_malformed_answer_is_never_coerced_to_pass(self) -> None:
        # The exact live-failure shape: a green-sounding prose answer without
        # any valid JSON must be a typed failure, never a PASS result.
        with pytest.raises(pp.ProposalReviewParseError):
            parse("All good — verdict: PASS, no findings, ship it.")


# ---------------------------------------------------------------------------
# Session 013A — the envelope is MANDATORY (no balanced-brace rescue)
# ---------------------------------------------------------------------------
class TestEnvelopeStrictness:
    """Regression pins for the strict-envelope correction (Session 013A).

    The core regression: a syntactically PERFECT review JSON object without
    the envelope must be REJECTED with ``missing_envelope`` — never rescued
    out of bare braces, never accepted from prose.
    """

    def test_1_bare_valid_json_without_envelope_rejected(self) -> None:
        body = json.dumps(payload(verdict="PASS"))
        assert json.loads(body)  # the payload itself IS valid JSON
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(body)
        assert excinfo.value.reason == "missing_envelope"

    def test_2_prose_wrapped_bare_valid_json_rejected(self) -> None:
        body = json.dumps(payload(verdict="PASS"))
        raw = f"Here is my review:\n\n{body}\n\nThanks — looks great."
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(raw)
        assert excinfo.value.reason == "missing_envelope"

    def test_bare_valid_json_in_markdown_fence_rejected(self) -> None:
        body = json.dumps(payload(verdict="PASS"))
        raw = f"```json\n{body}\n```"
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(raw)
        assert excinfo.value.reason == "missing_envelope"

    def test_4_plain_prose_without_envelope_rejected(self) -> None:
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse("All good — verdict: PASS, no findings, ship it.")
        assert excinfo.value.reason == "missing_envelope"

    def test_bare_valid_json_of_the_other_verdict_rejected(self) -> None:
        body = json.dumps(payload(verdict="NEEDS_REVISION", findings=[finding()]))
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(body)
        assert excinfo.value.reason == "missing_envelope"

    def test_envelope_pair_present_but_empty_reports_no_json_object(self) -> None:
        raw = f"{pp.PROPOSAL_REVIEW_ENVELOPE_START}\n{pp.PROPOSAL_REVIEW_ENVELOPE_END}"
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            parse(raw)
        assert excinfo.value.reason == "no_json_object"


# ---------------------------------------------------------------------------
# shared constants / expected-role guards
# ---------------------------------------------------------------------------
class TestParserContract:
    def test_prompt_and_parser_share_the_envelope_constants(self) -> None:
        assert (
            pp.review_parser.PROPOSAL_REVIEW_ENVELOPE_START
            is pp.review_packet.PROPOSAL_REVIEW_ENVELOPE_START
        )
        assert (
            pp.review_parser.PROPOSAL_REVIEW_ENVELOPE_END
            is pp.review_packet.PROPOSAL_REVIEW_ENVELOPE_END
        )

    def test_orchestrator_cannot_be_an_expected_role(self) -> None:
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            pp.parse_proposal_review(
                envelope(json.dumps(payload(role="ORCHESTRATOR"))),
                expected_role=pp.ProposalRole.ORCHESTRATOR,
                expected_iteration=1,
            )
        assert excinfo.value.reason == "invalid_expected_role"

    def test_invalid_expected_iteration_rejected(self) -> None:
        with pytest.raises(pp.ProposalReviewParseError) as excinfo:
            pp.parse_proposal_review(
                envelope(json.dumps(payload())),
                expected_role=pp.ProposalRole.SCIENTIFIC_REVIEWER,
                expected_iteration=0,
            )
        assert excinfo.value.reason == "invalid_expected_iteration"

    def test_reviewer_roles_whitelist_excludes_orchestrator(self) -> None:
        assert "ORCHESTRATOR" not in pp.review_parser.REVIEWER_ROLES
        assert pp.review_parser.REVIEWER_ROLES == frozenset(
            {"SCIENTIFIC_REVIEWER", "PROPOSAL_ENGINEER", "RED_TEAM_REVIEWER"}
        )
