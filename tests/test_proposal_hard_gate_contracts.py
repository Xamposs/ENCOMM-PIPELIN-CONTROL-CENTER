"""Session 016 — hard-gate PURE engine contract tests.

Covers the mandated BASE CONTRACT and per-validator matrix sections
(brief §25, items 1–41):

* BASE: all 14 canonical gates evaluated in canonical order; a
  duplicate/missing gate set is impossible; evidence hash/iteration
  mismatch rejected; malformed/zero-byte evidence blocked; explicit N/A
  accepted with its reason preserved; implicit N/A rejected.
* Every REAL validator: pass and fail directions, including the exact
  failure classification (PROPOSAL_ISSUE vs EVIDENCE_MISSING vs
  EVIDENCE_INVALID) and the PAGE_LIMIT estimate WARN.

All tests are OFFLINE and deterministic: real files in ``tmp_path`` only,
zero model calls, zero network.

Session 016A additions: CLAIM_LEDGER claims carry the CANONICAL
``claim_id`` only (legacy ``id`` never substitutes), and every
:class:`HardGateEvaluation` is status/failure-class coherent so an
inconsistent evaluation can never launder a FAIL into COMPLETE
(constructor + engine-level defense in depth).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import encomm_pcc.proposal as pp
from encomm_pcc.proposal.enums import HARD_GATE_IDS_TUPLE, ProposalHardGateStatus
from encomm_pcc.proposal.hard_gates import (
    HARD_GATE_EVIDENCE_SCHEMA,
    HardGateDisposition,
    HardGateEngineError,
    HardGateEvaluation,
    HardGateEvidenceDocument,
    HardGateEvidenceError,
    HardGateFailureClass,
    HardGateRunResult,
    evaluate_hard_gates,
    load_hard_gate_evidence,
    validate_gate_registry,
)
from encomm_pcc.proposal.hard_gate_validators import GATE_VALIDATORS

ITERATION = 2

GOOD_TEXT = "# 1. Excellence\n\n## 2. Impact\n\nKALHAS drives the architecture.\n"

GOOD_SECTIONS: dict = {
    "mandatory_sections": {
        "required_headings": ["1. Excellence", "2. Impact"],
    },
    "challenge_mapping": {
        "required_markers": ["C1", "C2"],
        "mappings": [
            {
                "marker": "C1",
                "proposal_section": "1. Excellence",
                "evidence_text": "section 1 addresses C1 directly.",
            },
            {
                "marker": "C2",
                "proposal_section": "2. Impact",
                "evidence_reference": "IMPACT-MATRIX.xlsx#C2",
            },
        ],
    },
    "terminology": {
        "rules": [
            {"canonical": "KALHAS", "forbidden_aliases": ["Kalchas", "Calhas"]},
        ],
    },
    "workplan": {
        "work_packages": [
            {
                "id": "WP1",
                "tasks": [
                    {"id": "T1.1", "owner": "AAI", "person_months": 12},
                    {"id": "T1.2", "owner": "BBU", "person_months": 8},
                ],
                "person_months": 20,
                "deliverables": [{"id": "D1.1", "task_ids": ["T1.1", "T1.2"]}],
                "milestones": [{"id": "MS1", "wp_ref": "WP1", "task_ref": "T1.1"}],
            },
            {
                "id": "WP2",
                "tasks": [{"id": "T2.1", "owner": "CCO", "person_months": 10}],
                "person_months": 10,
            },
        ],
        "total_person_months": 30,
    },
    "budget": {
        "categories": [
            {"name": "personnel", "amount": 100},
            {"name": "equipment", "amount": 10.5},
            {"name": "dissemination", "amount": 0.25},
        ],
        "participant_total": 110.75,
        "declared_total": 110.75,
    },
    "subcontracting": {"entries": []},
}


# ---------------------------------------------------------------------------
# seed helpers (real files in tmp_path)
# ---------------------------------------------------------------------------
def build_workspace(
    tmp_path: Path,
    *,
    text: str = GOOD_TEXT,
    iteration: int = ITERATION,
    applicability: dict | None = None,
    sections: dict | None = None,
    page_method: str = "pdf-layout-exact",
    with_bundle: bool = True,
) -> tuple[Path, str]:
    """Seed a fully valid workspace; return ``(workspace, proposal_hash)``."""
    ws = pp.ProposalWorkspace(tmp_path)
    ws.initialize()
    raw = text.encode("utf-8")
    ws.master_proposal_path().write_bytes(raw)
    proposal_hash = pp.proposal_fingerprint(ws.master_proposal_path())

    evidence: dict = {
        "schema": HARD_GATE_EVIDENCE_SCHEMA,
        "iteration_number": iteration,
        "proposal_hash": proposal_hash,
        "applicability": applicability or {},
    }
    evidence.update(copy.deepcopy(sections or GOOD_SECTIONS))
    control = tmp_path / "05_CONTROL"
    (control / "HARD_GATE_EVIDENCE.json").write_text(
        json.dumps(evidence, indent=2), encoding="utf-8", newline="\n"
    )
    (control / "UNVERIFIED_CLAIMS.json").write_text(
        '{"unresolved": []}', encoding="utf-8", newline="\n"
    )
    (control / "CONTRADICTIONS.json").write_text(
        '{"unresolved": []}', encoding="utf-8", newline="\n"
    )
    (control / "PAGE_BUDGET.json").write_text(
        json.dumps(
            {
                "measurement_method": page_method,
                "measured_pages": 28,
                "max_pages": 30,
            }
        ),
        encoding="utf-8",
        newline="\n",
    )
    evidence_dir = tmp_path / "02_EVIDENCE"
    (evidence_dir / "SOURCE_REGISTRY.json").write_text(
        '{"sources": [{"id": "SRC-1", "title": "WP Paper"}]}',
        encoding="utf-8",
        newline="\n",
    )
    (evidence_dir / "CLAIM_LEDGER.json").write_text(
        json.dumps(
            {
                "claims": [
                    {
                        "claim_id": "CL-1",
                        "source_ref": "SRC-1",
                        "requires_citation": True,
                        "citation_verified": True,
                        "source_refs": ["SRC-1"],
                    }
                ]
            }
        ),
        encoding="utf-8",
        newline="\n",
    )
    if with_bundle:
        bundle_dir = tmp_path / "04_REVIEWS" / f"iteration_{iteration:03d}"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        reviewer = {
            "reviewer_role": "SCIENTIFIC_REVIEWER",
            "verdict": "PASS",
            "summary": "clean",
            "findings": [],
            "proposed_patches": [],
            "unverified_claims": [],
            "iteration_number": iteration,
        }
        bundle = {
            "iteration_number": iteration,
            "proposal_revision": f"rev-{iteration}",
            "proposal_hash": proposal_hash,
            "scientific_review": reviewer,
            "implementation_review": reviewer,
            "red_team_review": reviewer,
            "aggregate_verdict": "PASS",
            "findings": [],
            "proposed_patches": [],
            "unverified_claims": [],
            "counts_by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0},
        }
        (bundle_dir / "review_bundle.json").write_text(
            json.dumps(bundle, indent=2), encoding="utf-8", newline="\n"
        )
    return tmp_path, proposal_hash


def evaluate(tmp_path: Path, **kwargs) -> HardGateRunResult:
    ws, ws_hash = build_workspace(tmp_path, **kwargs)
    return evaluate_hard_gates(
        evidence=load_hard_gate_evidence(
            ws / "05_CONTROL" / "HARD_GATE_EVIDENCE.json",
            expected_iteration_number=kwargs.get("iteration", ITERATION),
            expected_proposal_hash=ws_hash,
        ),
        master_proposal_text=(ws / "03_PROPOSAL" / "MASTER_PROPOSAL.md").read_text(
            encoding="utf-8"
        ),
        workspace=ws,
    )


def gate(result, gate_id: str):
    return next(e for e in result.evaluations if e.gate_id == gate_id)


def mutate_sections(tmp_path: Path, key: str, mutation) -> "object":
    """Evaluate with ONE evidence section replaced by ``mutation``."""
    sections = copy.deepcopy(GOOD_SECTIONS)
    sections[key] = mutation
    return evaluate(tmp_path, sections=sections)


# ---------------------------------------------------------------------------
# BASE CONTRACT (items 1–8)
# ---------------------------------------------------------------------------
class TestBaseContract:
    def test_01_all_14_canonical_gates_evaluated_in_canonical_order(self, tmp_path):
        result = evaluate(tmp_path)
        assert [e.gate_id for e in result.evaluations] == list(HARD_GATE_IDS_TUPLE)
        assert len(result.evaluations) == 14

    def test_02_duplicate_or_missing_gate_impossible(self):
        # A registry missing a gate, carrying an extra id, or with duplicated
        # keys can never pass the structural check.
        incomplete = dict(list(GATE_VALIDATORS.items())[:13])
        with pytest.raises(HardGateEngineError):
            validate_gate_registry(incomplete)
        extra = dict(GATE_VALIDATORS)
        extra["NOT_A_GATE"] = GATE_VALIDATORS["MANDATORY_SECTIONS"]
        with pytest.raises(HardGateEngineError):
            validate_gate_registry(extra)

        class DupRegistry(dict):
            def keys(self):  # noqa: D102 - simulate duplicate keys
                return list(GATE_VALIDATORS.keys()) + ["MANDATORY_SECTIONS"]

        with pytest.raises(HardGateEngineError):
            validate_gate_registry(DupRegistry(GATE_VALIDATORS))
        # A run result built from an incomplete evaluation set is refused.
        full = [
            HardGateEvaluation(gate_id=g, status=ProposalHardGateStatus.PASS)
            for g in HARD_GATE_IDS_TUPLE
        ]
        with pytest.raises(HardGateEngineError):
            HardGateRunResult.from_evaluations(
                iteration_number=1, proposal_hash="a", evaluations=full[:13]
            )
        # A HardGateRunResult constructed directly with a wrong set also fails.
        with pytest.raises(HardGateEngineError):
            HardGateRunResult(
                iteration_number=1,
                proposal_hash="a",
                evaluations=full[:13],
            )

    def test_03_evidence_hash_mismatch_rejected(self, tmp_path):
        ws, ws_hash = build_workspace(tmp_path)
        evidence_path = ws / "05_CONTROL" / "HARD_GATE_EVIDENCE.json"
        with pytest.raises(HardGateEvidenceError) as excinfo:
            load_hard_gate_evidence(
                evidence_path,
                expected_iteration_number=ITERATION,
                expected_proposal_hash="b" * 64,
            )
        assert excinfo.value.reason == "evidence_hash_mismatch"

    def test_04_evidence_iteration_mismatch_rejected(self, tmp_path):
        ws, ws_hash = build_workspace(tmp_path)
        evidence_path = ws / "05_CONTROL" / "HARD_GATE_EVIDENCE.json"
        with pytest.raises(HardGateEvidenceError) as excinfo:
            load_hard_gate_evidence(
                evidence_path,
                expected_iteration_number=ITERATION + 1,
                expected_proposal_hash=ws_hash,
            )
        assert excinfo.value.reason == "evidence_iteration_mismatch"

    def test_05_malformed_evidence_blocked(self, tmp_path):
        ws, ws_hash = build_workspace(tmp_path)
        evidence_path = ws / "05_CONTROL" / "HARD_GATE_EVIDENCE.json"
        # Not JSON at all.
        evidence_path.write_text("{not json", encoding="utf-8")
        with pytest.raises(HardGateEvidenceError) as excinfo:
            load_hard_gate_evidence(
                evidence_path,
                expected_iteration_number=ITERATION,
                expected_proposal_hash=ws_hash,
            )
        assert excinfo.value.reason == "evidence_corrupt"
        # A JSON array instead of an object.
        evidence_path.write_text("[]", encoding="utf-8")
        with pytest.raises(HardGateEvidenceError) as excinfo:
            load_hard_gate_evidence(
                evidence_path,
                expected_iteration_number=ITERATION,
                expected_proposal_hash=ws_hash,
            )
        assert excinfo.value.reason == "evidence_corrupt"
        # Wrong schema.
        evidence_path.write_text(
            json.dumps({"schema": "other/v9", "iteration_number": ITERATION,
                        "proposal_hash": ws_hash}),
            encoding="utf-8",
        )
        with pytest.raises(HardGateEvidenceError) as excinfo:
            load_hard_gate_evidence(
                evidence_path,
                expected_iteration_number=ITERATION,
                expected_proposal_hash=ws_hash,
            )
        assert excinfo.value.reason == "evidence_schema_invalid"
        # Non-canonical gate id in applicability.
        evidence_path.write_text(
            json.dumps({
                "schema": HARD_GATE_EVIDENCE_SCHEMA,
                "iteration_number": ITERATION,
                "proposal_hash": ws_hash,
                "applicability": {"MADE_UP_GATE": {"applicable": False, "reason": "x"}},
            }),
            encoding="utf-8",
        )
        with pytest.raises(HardGateEvidenceError) as excinfo:
            load_hard_gate_evidence(
                evidence_path,
                expected_iteration_number=ITERATION,
                expected_proposal_hash=ws_hash,
            )
        assert excinfo.value.reason == "evidence_applicability_invalid"
        # A non-object evidence section.
        evidence_path.write_text(
            json.dumps({
                "schema": HARD_GATE_EVIDENCE_SCHEMA,
                "iteration_number": ITERATION,
                "proposal_hash": ws_hash,
                "mandatory_sections": ["not", "an", "object"],
            }),
            encoding="utf-8",
        )
        with pytest.raises(HardGateEvidenceError) as excinfo:
            load_hard_gate_evidence(
                evidence_path,
                expected_iteration_number=ITERATION,
                expected_proposal_hash=ws_hash,
            )
        assert excinfo.value.reason == "evidence_section_invalid"

    def test_06_zero_byte_evidence_blocked(self, tmp_path):
        ws, ws_hash = build_workspace(tmp_path)
        evidence_path = ws / "05_CONTROL" / "HARD_GATE_EVIDENCE.json"
        evidence_path.write_bytes(b"")
        with pytest.raises(HardGateEvidenceError) as excinfo:
            load_hard_gate_evidence(
                evidence_path,
                expected_iteration_number=ITERATION,
                expected_proposal_hash=ws_hash,
            )
        assert excinfo.value.reason == "evidence_zero_byte"

    def test_07_explicit_na_with_reason_accepted(self, tmp_path):
        result = evaluate(
            tmp_path,
            applicability={
                "SUBCONTRACTING_CORE_TASKS": {
                    "applicable": False,
                    "reason": "no subcontracting exists in this programme",
                }
            },
        )
        entry = gate(result, "SUBCONTRACTING_CORE_TASKS")
        assert entry.status is ProposalHardGateStatus.NOT_APPLICABLE
        assert "no subcontracting exists" in entry.message
        assert result.disposition is HardGateDisposition.COMPLETE

    def test_08_implicit_na_rejected(self, tmp_path):
        # (a) an N/A entry without a reason is refused by the loader.
        ws, ws_hash = build_workspace(
            tmp_path,
            applicability={
                "PAGE_LIMIT": {"applicable": False, "reason": ""},
            },
        )
        with pytest.raises(HardGateEvidenceError) as excinfo:
            load_hard_gate_evidence(
                ws / "05_CONTROL" / "HARD_GATE_EVIDENCE.json",
                expected_iteration_number=ITERATION,
                expected_proposal_hash=ws_hash,
            )
        assert excinfo.value.reason == "evidence_applicability_invalid"
        # (b) a gate with NO applicability entry RUNS — a missing section is
        # EVIDENCE_MISSING, never an implicit NOT_APPLICABLE.
        sections = copy.deepcopy(GOOD_SECTIONS)
        del sections["subcontracting"]
        result = evaluate(tmp_path, sections=sections)
        entry = gate(result, "SUBCONTRACTING_CORE_TASKS")
        assert entry.status is ProposalHardGateStatus.FAIL
        assert entry.failure_class is HardGateFailureClass.EVIDENCE_MISSING
        assert result.disposition is HardGateDisposition.BLOCKED


# ---------------------------------------------------------------------------
# MANDATORY_SECTIONS (items 9–11)
# ---------------------------------------------------------------------------
class TestMandatorySections:
    def test_09_all_headings_pass_with_normalisation(self, tmp_path):
        text = (
            "#   1.   Excellence  \n"     # extra whitespace
            "## 2. impact\n"              # case difference
            "### 2.1 Sub\n"
        )
        result = evaluate(tmp_path, text=text)
        entry = gate(result, "MANDATORY_SECTIONS")
        assert entry.status is ProposalHardGateStatus.PASS

    def test_10_missing_heading_fails_as_proposal_issue(self, tmp_path):
        result = mutate_sections(
            tmp_path,
            "mandatory_sections",
            {"required_headings": ["1. Excellence", "3. Quality"]},
        )
        entry = gate(result, "MANDATORY_SECTIONS")
        assert entry.status is ProposalHardGateStatus.FAIL
        assert entry.failure_class is HardGateFailureClass.PROPOSAL_ISSUE
        assert result.disposition is HardGateDisposition.REVISION_REQUIRED

    def test_11_no_fuzzy_semantic_matching(self, tmp_path):
        # A required heading that is only a SUBSTRING/NEAR-MISS of a real one
        # must NOT match: no fuzzy or semantic matching exists.
        result = evaluate(
            tmp_path,
            text="# 1. Excellence and beyond\n",
            sections={
                **copy.deepcopy(GOOD_SECTIONS),
                "mandatory_sections": {"required_headings": ["1. Excellence"]},
            },
        )
        entry = gate(result, "MANDATORY_SECTIONS")
        assert entry.status is ProposalHardGateStatus.FAIL
        # Missing required_headings evidence itself is EVIDENCE_INVALID.
        result2 = mutate_sections(
            tmp_path, "mandatory_sections", {"required_headings": []}
        )
        entry2 = gate(result2, "MANDATORY_SECTIONS")
        assert entry2.failure_class is HardGateFailureClass.EVIDENCE_INVALID
        result3 = mutate_sections(
            tmp_path, "mandatory_sections", {"required_headings": "1. Excellence"}
        )
        entry3 = gate(result3, "MANDATORY_SECTIONS")
        assert entry3.failure_class is HardGateFailureClass.EVIDENCE_INVALID


# ---------------------------------------------------------------------------
# CHALLENGE_MAPPING (items 12–13)
# ---------------------------------------------------------------------------
class TestChallengeMapping:
    def test_12_all_explicit_markers_mapped_pass(self, tmp_path):
        result = evaluate(tmp_path)
        assert gate(result, "CHALLENGE_MAPPING").status is ProposalHardGateStatus.PASS

    def test_13_missing_marker_fails(self, tmp_path):
        sections = copy.deepcopy(GOOD_SECTIONS)
        sections["challenge_mapping"]["mappings"] = [
            sections["challenge_mapping"]["mappings"][0]
        ]
        result = evaluate(tmp_path, sections=sections)
        entry = gate(result, "CHALLENGE_MAPPING")
        assert entry.status is ProposalHardGateStatus.FAIL
        assert entry.failure_class is HardGateFailureClass.PROPOSAL_ISSUE
        assert "C2" in entry.message
        # A mapping record without evidence text/reference is invalid.
        broken = copy.deepcopy(GOOD_SECTIONS)
        broken["challenge_mapping"]["mappings"] = [
            {"marker": "C1", "proposal_section": "1. Excellence"}
        ]
        result2 = evaluate(tmp_path, sections=broken)
        assert (
            gate(result2, "CHALLENGE_MAPPING").failure_class
            is HardGateFailureClass.EVIDENCE_INVALID
        )


# ---------------------------------------------------------------------------
# SOURCE/CLAIMS (items 14–20)
# ---------------------------------------------------------------------------
class TestSourceAndClaims:
    def test_14_valid_source_registry_and_claims_pass(self, tmp_path):
        result = evaluate(tmp_path)
        assert (
            gate(result, "SOURCE_OF_TRUTH_INTEGRITY").status
            is ProposalHardGateStatus.PASS
        )
        assert gate(result, "CITATION_VERIFICATION").status is ProposalHardGateStatus.PASS

    def test_15_unresolved_source_ref_fails(self, tmp_path):
        ledger = {
            "claims": [{"claim_id": "CL-1", "source_ref": "GHOST", "source_refs": []}]
        }
        (tmp_path / "02_EVIDENCE").mkdir(parents=True, exist_ok=True)
        # build_workspace re-seeds; write AFTER via evaluate kwargs is complex —
        # run the full flow then overwrite the ledger is NOT re-read.  Instead:
        # seed manually here.
        ws, ws_hash = build_workspace(tmp_path)
        (ws / "02_EVIDENCE" / "CLAIM_LEDGER.json").write_text(
            json.dumps(ledger), encoding="utf-8"
        )
        result = evaluate_hard_gates(
            evidence=load_hard_gate_evidence(
                ws / "05_CONTROL" / "HARD_GATE_EVIDENCE.json",
                expected_iteration_number=ITERATION,
                expected_proposal_hash=ws_hash,
            ),
            master_proposal_text=GOOD_TEXT,
            workspace=ws,
        )
        entry = gate(result, "SOURCE_OF_TRUTH_INTEGRITY")
        assert entry.status is ProposalHardGateStatus.FAIL
        assert entry.failure_class is HardGateFailureClass.EVIDENCE_MISSING

    def test_16_conflicting_claim_fails_as_proposal_issue(self, tmp_path):
        ws, ws_hash = build_workspace(tmp_path)
        (ws / "02_EVIDENCE" / "CLAIM_LEDGER.json").write_text(
            json.dumps(
                {
                    "claims": [
                        {
                            "claim_id": "CL-1",
                            "source_ref": "SRC-1",
                            "conflicting": True,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        result = evaluate_hard_gates(
            evidence=load_hard_gate_evidence(
                ws / "05_CONTROL" / "HARD_GATE_EVIDENCE.json",
                expected_iteration_number=ITERATION,
                expected_proposal_hash=ws_hash,
            ),
            master_proposal_text=GOOD_TEXT,
            workspace=ws,
        )
        entry = gate(result, "SOURCE_OF_TRUTH_INTEGRITY")
        assert entry.failure_class is HardGateFailureClass.PROPOSAL_ISSUE
        assert result.disposition is HardGateDisposition.REVISION_REQUIRED

    def test_17_unverified_claims_control_file_fails(self, tmp_path):
        ws, ws_hash = build_workspace(tmp_path)
        (ws / "05_CONTROL" / "UNVERIFIED_CLAIMS.json").write_text(
            json.dumps({"unresolved": ["The 40% uplift figure is unsourced."]}),
            encoding="utf-8",
        )
        result = evaluate_hard_gates(
            evidence=load_hard_gate_evidence(
                ws / "05_CONTROL" / "HARD_GATE_EVIDENCE.json",
                expected_iteration_number=ITERATION,
                expected_proposal_hash=ws_hash,
            ),
            master_proposal_text=GOOD_TEXT,
            workspace=ws,
        )
        entry = gate(result, "UNVERIFIED_CLAIMS")
        assert entry.failure_class is HardGateFailureClass.PROPOSAL_ISSUE

    def test_18_same_hash_review_unverified_claim_fails(self, tmp_path):
        ws, ws_hash = build_workspace(tmp_path)
        bundle_path = ws / "04_REVIEWS" / f"iteration_{ITERATION:03d}" / "review_bundle.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        bundle["unverified_claims"] = ["Reviewer flagged an unsourced number."]
        bundle_path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
        result = evaluate_hard_gates(
            evidence=load_hard_gate_evidence(
                ws / "05_CONTROL" / "HARD_GATE_EVIDENCE.json",
                expected_iteration_number=ITERATION,
                expected_proposal_hash=ws_hash,
            ),
            master_proposal_text=GOOD_TEXT,
            workspace=ws,
        )
        entry = gate(result, "UNVERIFIED_CLAIMS")
        assert entry.status is ProposalHardGateStatus.FAIL
        assert entry.failure_class is HardGateFailureClass.PROPOSAL_ISSUE

    def test_19_citation_required_verified_claim_passes(self, tmp_path):
        result = evaluate(tmp_path)
        entry = gate(result, "CITATION_VERIFICATION")
        assert entry.status is ProposalHardGateStatus.PASS
        assert "1 citation-requiring claim(s)" in entry.message

    def test_20_unverified_citation_fails(self, tmp_path):
        ws, ws_hash = build_workspace(tmp_path)
        (ws / "02_EVIDENCE" / "CLAIM_LEDGER.json").write_text(
            json.dumps(
                {
                    "claims": [
                        {
                            "claim_id": "CL-1",
                            "source_ref": "SRC-1",
                            "requires_citation": True,
                            "citation_verified": False,
                            "source_refs": ["SRC-1"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        result = evaluate_hard_gates(
            evidence=load_hard_gate_evidence(
                ws / "05_CONTROL" / "HARD_GATE_EVIDENCE.json",
                expected_iteration_number=ITERATION,
                expected_proposal_hash=ws_hash,
            ),
            master_proposal_text=GOOD_TEXT,
            workspace=ws,
        )
        entry = gate(result, "CITATION_VERIFICATION")
        assert entry.failure_class is HardGateFailureClass.PROPOSAL_ISSUE


# ---------------------------------------------------------------------------
# TERMINOLOGY (items 21–22)
# ---------------------------------------------------------------------------
class TestTerminology:
    def test_21_canonical_only_passes(self, tmp_path):
        result = evaluate(tmp_path)
        assert gate(result, "TERMINOLOGY_CONSISTENCY").status is ProposalHardGateStatus.PASS

    def test_22_forbidden_alias_fails(self, tmp_path):
        result = evaluate(
            tmp_path,
            text="# 1. Excellence\n\nKalchas drives the architecture.\n",
        )
        entry = gate(result, "TERMINOLOGY_CONSISTENCY")
        assert entry.status is ProposalHardGateStatus.FAIL
        assert entry.failure_class is HardGateFailureClass.PROPOSAL_ISSUE
        assert "Kalchas" in entry.message
        # No semantic synonym detection: a DIFFERENT wrong name that is not a
        # configured alias never fails the gate.
        result2 = evaluate(
            tmp_path,
            text="# 1. Excellence\n\nA completely unrelated name appears.\n",
        )
        assert (
            gate(result2, "TERMINOLOGY_CONSISTENCY").status
            is ProposalHardGateStatus.PASS
        )


# ---------------------------------------------------------------------------
# WORKPLAN (items 23–28)
# ---------------------------------------------------------------------------
class TestWorkplan:
    def test_23_valid_tasks_pass(self, tmp_path):
        result = evaluate(tmp_path)
        assert gate(result, "WP_TASK_CONSISTENCY").status is ProposalHardGateStatus.PASS
        assert (
            gate(result, "WP_DELIVERABLE_CONSISTENCY").status
            is ProposalHardGateStatus.PASS
        )
        assert (
            gate(result, "WP_MILESTONE_CONSISTENCY").status is ProposalHardGateStatus.PASS
        )
        assert (
            gate(result, "PERSON_MONTH_CONSISTENCY").status is ProposalHardGateStatus.PASS
        )

    def test_24_duplicate_task_relation_fails(self, tmp_path):
        # The same task id in two WPs.
        broken = copy.deepcopy(GOOD_SECTIONS)
        broken["workplan"]["work_packages"][1]["tasks"].append(
            {"id": "T1.1", "owner": "ZZZ"}
        )
        result = evaluate(tmp_path, sections=broken)
        entry = gate(result, "WP_TASK_CONSISTENCY")
        assert entry.status is ProposalHardGateStatus.FAIL
        assert entry.failure_class is HardGateFailureClass.PROPOSAL_ISSUE
        # Missing required owner reference field.
        broken2 = copy.deepcopy(GOOD_SECTIONS)
        del broken2["workplan"]["work_packages"][0]["tasks"][0]["owner"]
        result2 = evaluate(tmp_path, sections=broken2)
        entry2 = gate(result2, "WP_TASK_CONSISTENCY")
        assert entry2.failure_class is HardGateFailureClass.PROPOSAL_ISSUE

    def test_25_deliverable_invalid_references_fail(self, tmp_path):
        broken = copy.deepcopy(GOOD_SECTIONS)
        broken["workplan"]["work_packages"][0]["deliverables"] = [
            {"id": "D1.1", "task_ids": ["GHOST_TASK"]}
        ]
        result = evaluate(tmp_path, sections=broken)
        entry = gate(result, "WP_DELIVERABLE_CONSISTENCY")
        assert entry.failure_class is HardGateFailureClass.PROPOSAL_ISSUE
        # Duplicate deliverable id across WPs is invalid evidence.
        broken2 = copy.deepcopy(GOOD_SECTIONS)
        broken2["workplan"]["work_packages"][1]["deliverables"] = [{"id": "D1.1"}]
        result2 = evaluate(tmp_path, sections=broken2)
        entry2 = gate(result2, "WP_DELIVERABLE_CONSISTENCY")
        assert entry2.failure_class is HardGateFailureClass.EVIDENCE_INVALID

    def test_26_milestone_invalid_relation_fails(self, tmp_path):
        broken = copy.deepcopy(GOOD_SECTIONS)
        broken["workplan"]["work_packages"][0]["milestones"] = [
            {"id": "MS1", "wp_ref": "WP_GHOST"}
        ]
        result = evaluate(tmp_path, sections=broken)
        entry = gate(result, "WP_MILESTONE_CONSISTENCY")
        assert entry.status is ProposalHardGateStatus.FAIL
        assert entry.failure_class is HardGateFailureClass.PROPOSAL_ISSUE

    def test_27_person_month_correct_sums_pass(self, tmp_path):
        result = evaluate(tmp_path)
        assert (
            gate(result, "PERSON_MONTH_CONSISTENCY").status is ProposalHardGateStatus.PASS
        )

    def test_28_person_month_mismatch_fails(self, tmp_path):
        broken = copy.deepcopy(GOOD_SECTIONS)
        broken["workplan"]["work_packages"][0]["person_months"] = 21
        result = evaluate(tmp_path, sections=broken)
        entry = gate(result, "PERSON_MONTH_CONSISTENCY")
        assert entry.status is ProposalHardGateStatus.FAIL
        assert entry.failure_class is HardGateFailureClass.PROPOSAL_ISSUE
        # Global total mismatch.
        broken2 = copy.deepcopy(GOOD_SECTIONS)
        broken2["workplan"]["total_person_months"] = 31
        result2 = evaluate(tmp_path, sections=broken2)
        entry2 = gate(result2, "PERSON_MONTH_CONSISTENCY")
        assert entry2.failure_class is HardGateFailureClass.PROPOSAL_ISSUE
        # Negative PM is invalid evidence.
        broken3 = copy.deepcopy(GOOD_SECTIONS)
        broken3["workplan"]["work_packages"][0]["person_months"] = -1
        result3 = evaluate(tmp_path, sections=broken3)
        entry3 = gate(result3, "PERSON_MONTH_CONSISTENCY")
        assert entry3.failure_class is HardGateFailureClass.EVIDENCE_INVALID


# ---------------------------------------------------------------------------
# BUDGET (items 29–31)
# ---------------------------------------------------------------------------
class TestBudget:
    def test_29_exact_decimal_arithmetic_passes(self, tmp_path):
        # 0.1 + 0.2 == 0.3 EXACTLY under Decimal (binary float would fail).
        sections = copy.deepcopy(GOOD_SECTIONS)
        sections["budget"] = {
            "categories": [
                {"name": "a", "amount": 0.1},
                {"name": "b", "amount": 0.2},
            ],
            "participant_total": 0.3,
            "declared_total": 0.3,
        }
        result = evaluate(tmp_path, sections=sections)
        entry = gate(result, "BUDGET_CONSISTENCY")
        assert entry.status is ProposalHardGateStatus.PASS

    def test_30_subtotal_mismatch_fails(self, tmp_path):
        broken = copy.deepcopy(GOOD_SECTIONS)
        broken["budget"]["participant_total"] = 999
        result = evaluate(tmp_path, sections=broken)
        entry = gate(result, "BUDGET_CONSISTENCY")
        assert entry.status is ProposalHardGateStatus.FAIL
        assert entry.failure_class is HardGateFailureClass.PROPOSAL_ISSUE
        # Declared total mismatch too.
        broken2 = copy.deepcopy(GOOD_SECTIONS)
        broken2["budget"]["declared_total"] = 110.76
        result2 = evaluate(tmp_path, sections=broken2)
        entry2 = gate(result2, "BUDGET_CONSISTENCY")
        assert entry2.failure_class is HardGateFailureClass.PROPOSAL_ISSUE

    def test_31_negative_amount_fails(self, tmp_path):
        broken = copy.deepcopy(GOOD_SECTIONS)
        broken["budget"]["categories"][1]["amount"] = -5
        result = evaluate(tmp_path, sections=broken)
        entry = gate(result, "BUDGET_CONSISTENCY")
        assert entry.status is ProposalHardGateStatus.FAIL
        assert entry.failure_class is HardGateFailureClass.EVIDENCE_INVALID


# ---------------------------------------------------------------------------
# SUBCONTRACTING (items 32–35)
# ---------------------------------------------------------------------------
class TestSubcontracting:
    def test_32_explicit_empty_entries_pass(self, tmp_path):
        result = evaluate(tmp_path)
        assert gate(result, "SUBCONTRACTING_CORE_TASKS").status is ProposalHardGateStatus.PASS

    def test_33_non_core_justified_subcontract_pass(self, tmp_path):
        sections = copy.deepcopy(GOOD_SECTIONS)
        sections["subcontracting"] = {
            "entries": [
                {
                    "task_id": "T1.2",
                    "subcontracted": True,
                    "core_task": False,
                    "justification": "Specialised lab access required.",
                }
            ]
        }
        result = evaluate(tmp_path, sections=sections)
        assert gate(result, "SUBCONTRACTING_CORE_TASKS").status is ProposalHardGateStatus.PASS

    def test_34_core_subcontract_fails_as_proposal_issue(self, tmp_path):
        sections = copy.deepcopy(GOOD_SECTIONS)
        sections["subcontracting"] = {
            "entries": [
                {
                    "task_id": "T1.1",
                    "subcontracted": True,
                    "core_task": True,
                    "justification": "outsourced",
                }
            ]
        }
        result = evaluate(tmp_path, sections=sections)
        entry = gate(result, "SUBCONTRACTING_CORE_TASKS")
        assert entry.status is ProposalHardGateStatus.FAIL
        assert entry.failure_class is HardGateFailureClass.PROPOSAL_ISSUE

    def test_35_missing_justification_fails(self, tmp_path):
        sections = copy.deepcopy(GOOD_SECTIONS)
        sections["subcontracting"] = {
            "entries": [
                {"task_id": "T1.2", "subcontracted": True, "core_task": False}
            ]
        }
        result = evaluate(tmp_path, sections=sections)
        entry = gate(result, "SUBCONTRACTING_CORE_TASKS")
        assert entry.failure_class is HardGateFailureClass.PROPOSAL_ISSUE


# ---------------------------------------------------------------------------
# PAGE_LIMIT (items 36–39)
# ---------------------------------------------------------------------------
class TestPageLimit:
    def test_36_authoritative_within_limit_passes(self, tmp_path):
        result = evaluate(tmp_path)
        assert gate(result, "PAGE_LIMIT").status is ProposalHardGateStatus.PASS

    def test_37_authoritative_over_limit_fails(self, tmp_path):
        ws, ws_hash = build_workspace(tmp_path)
        (ws / "05_CONTROL" / "PAGE_BUDGET.json").write_text(
            json.dumps(
                {
                    "measurement_method": "pdf-layout-exact",
                    "measured_pages": 31,
                    "max_pages": 30,
                }
            ),
            encoding="utf-8",
        )
        result = evaluate_hard_gates(
            evidence=load_hard_gate_evidence(
                ws / "05_CONTROL" / "HARD_GATE_EVIDENCE.json",
                expected_iteration_number=ITERATION,
                expected_proposal_hash=ws_hash,
            ),
            master_proposal_text=GOOD_TEXT,
            workspace=ws,
        )
        entry = gate(result, "PAGE_LIMIT")
        assert entry.status is ProposalHardGateStatus.FAIL
        assert entry.failure_class is HardGateFailureClass.PROPOSAL_ISSUE

    def test_38_estimate_returns_warn_never_pass(self, tmp_path):
        result = evaluate(tmp_path, page_method="operator estimate (word count)")
        entry = gate(result, "PAGE_LIMIT")
        assert entry.status is ProposalHardGateStatus.WARN
        assert "ESTIMATE" in entry.message
        assert entry.failure_class is HardGateFailureClass.NONE
        # WARN blocks COMPLETE and the run reports INCOMPLETE.
        assert result.disposition is HardGateDisposition.INCOMPLETE

    def test_39_missing_page_evidence_blocks(self, tmp_path):
        ws, ws_hash = build_workspace(tmp_path)
        (ws / "05_CONTROL" / "PAGE_BUDGET.json").unlink()
        result = evaluate_hard_gates(
            evidence=load_hard_gate_evidence(
                ws / "05_CONTROL" / "HARD_GATE_EVIDENCE.json",
                expected_iteration_number=ITERATION,
                expected_proposal_hash=ws_hash,
            ),
            master_proposal_text=GOOD_TEXT,
            workspace=ws,
        )
        entry = gate(result, "PAGE_LIMIT")
        assert entry.failure_class is HardGateFailureClass.EVIDENCE_MISSING
        assert result.disposition is HardGateDisposition.BLOCKED
        # A zero-byte seed file is equally unusable.
        ws2, ws_hash2 = build_workspace(Path(str(tmp_path) + "_2"))
        (ws2 / "05_CONTROL" / "PAGE_BUDGET.json").write_bytes(b"")
        result2 = evaluate_hard_gates(
            evidence=load_hard_gate_evidence(
                ws2 / "05_CONTROL" / "HARD_GATE_EVIDENCE.json",
                expected_iteration_number=ITERATION,
                expected_proposal_hash=ws_hash2,
            ),
            master_proposal_text=GOOD_TEXT,
            workspace=ws2,
        )
        entry2 = gate(result2, "PAGE_LIMIT")
        assert entry2.failure_class is HardGateFailureClass.EVIDENCE_MISSING


# ---------------------------------------------------------------------------
# INTERNAL_CONTRADICTIONS (items 40–41)
# ---------------------------------------------------------------------------
class TestContradictions:
    def test_40_empty_unresolved_passes(self, tmp_path):
        result = evaluate(tmp_path)
        assert (
            gate(result, "INTERNAL_CONTRADICTIONS").status is ProposalHardGateStatus.PASS
        )

    def test_41_unresolved_entries_fail(self, tmp_path):
        ws, ws_hash = build_workspace(tmp_path)
        (ws / "05_CONTROL" / "CONTRADICTIONS.json").write_text(
            json.dumps(
                {"unresolved": ["Section 2 says 12 PM; the work plan says 20 PM."]}
            ),
            encoding="utf-8",
        )
        result = evaluate_hard_gates(
            evidence=load_hard_gate_evidence(
                ws / "05_CONTROL" / "HARD_GATE_EVIDENCE.json",
                expected_iteration_number=ITERATION,
                expected_proposal_hash=ws_hash,
            ),
            master_proposal_text=GOOD_TEXT,
            workspace=ws,
        )
        entry = gate(result, "INTERNAL_CONTRADICTIONS")
        assert entry.status is ProposalHardGateStatus.FAIL
        assert entry.failure_class is HardGateFailureClass.PROPOSAL_ISSUE


# ---------------------------------------------------------------------------
# determinism (item 51, pure side)
# ---------------------------------------------------------------------------
def test_deterministic_evaluations_across_fresh_workspaces(tmp_path):
    result_a = evaluate(tmp_path / "a")
    result_b = evaluate(tmp_path / "b")
    assert result_a.to_dict() == result_b.to_dict()


# ---------------------------------------------------------------------------
# Session 016A — canonical claim_id + evaluation coherence invariants
# ---------------------------------------------------------------------------
def evaluate_with_ledger(tmp_path: Path, claims: list) -> HardGateRunResult:
    """Evaluate with ONE ``CLAIM_LEDGER.json`` claims array replaced."""
    ws, ws_hash = build_workspace(tmp_path)
    (ws / "02_EVIDENCE" / "CLAIM_LEDGER.json").write_text(
        json.dumps({"claims": claims}), encoding="utf-8"
    )
    return evaluate_hard_gates(
        evidence=load_hard_gate_evidence(
            ws / "05_CONTROL" / "HARD_GATE_EVIDENCE.json",
            expected_iteration_number=ITERATION,
            expected_proposal_hash=ws_hash,
        ),
        master_proposal_text=GOOD_TEXT,
        workspace=ws,
    )


class TestCanonicalClaimId:
    """CLAIM_LEDGER claims use the CANONICAL ``claim_id`` field ONLY."""

    def test_canonical_claim_id_passes(self, tmp_path):
        result = evaluate_with_ledger(
            tmp_path, [{"claim_id": "CL-1", "source_ref": "SRC-1"}]
        )
        assert (
            gate(result, "SOURCE_OF_TRUTH_INTEGRITY").status
            is ProposalHardGateStatus.PASS
        )

    def test_legacy_id_without_claim_id_is_evidence_invalid(self, tmp_path):
        result = evaluate_with_ledger(
            tmp_path, [{"id": "CL-1", "source_ref": "SRC-1"}]
        )
        entry = gate(result, "SOURCE_OF_TRUTH_INTEGRITY")
        assert entry.status is ProposalHardGateStatus.FAIL
        assert entry.failure_class is HardGateFailureClass.EVIDENCE_INVALID
        assert "claim_id" in entry.message

    def test_duplicate_claim_id_rejected(self, tmp_path):
        result = evaluate_with_ledger(
            tmp_path,
            [
                {"claim_id": "CL-1", "source_ref": "SRC-1"},
                {"claim_id": "CL-1", "source_ref": "SRC-1"},
            ],
        )
        entry = gate(result, "SOURCE_OF_TRUTH_INTEGRITY")
        assert entry.failure_class is HardGateFailureClass.EVIDENCE_INVALID
        assert "duplicate" in entry.message

    def test_both_claim_gates_use_the_same_canonical_claim_id(
        self, tmp_path
    ):
        # The canonical id is accepted by SOURCE_OF_TRUTH_INTEGRITY AND the
        # citation gate keys its verdict on the SAME field value.
        result = evaluate_with_ledger(
            tmp_path,
            [
                {
                    "claim_id": "CL-9",
                    "source_ref": "SRC-1",
                    "requires_citation": True,
                    "citation_verified": False,
                    "source_refs": ["SRC-1"],
                }
            ],
        )
        assert (
            gate(result, "SOURCE_OF_TRUTH_INTEGRITY").status
            is ProposalHardGateStatus.PASS
        )
        citation = gate(result, "CITATION_VERIFICATION")
        assert citation.status is ProposalHardGateStatus.FAIL
        assert "CL-9" in citation.message
        # The legacy `id` shape is refused by BOTH gates: one canonical
        # field, no fallback, no migration guessing.
        result2 = evaluate_with_ledger(
            tmp_path / "legacy",
            [
                {
                    "id": "CL-9",
                    "source_ref": "SRC-1",
                    "requires_citation": True,
                    "citation_verified": True,
                    "source_refs": ["SRC-1"],
                }
            ],
        )
        legacy_source = gate(result2, "SOURCE_OF_TRUTH_INTEGRITY")
        assert legacy_source.status is ProposalHardGateStatus.FAIL
        assert legacy_source.failure_class is HardGateFailureClass.EVIDENCE_INVALID
        legacy_citation = gate(result2, "CITATION_VERIFICATION")
        assert legacy_citation.status is ProposalHardGateStatus.FAIL
        assert legacy_citation.failure_class is HardGateFailureClass.EVIDENCE_INVALID


class TestEvaluationCoherence:
    """Session 016A: status/failure-class coherence protects COMPLETE.

    A FAIL always names why (never ``NONE``); PASS/NOT_APPLICABLE/WARN never
    carry a failure class.  Inconsistent pairs are refused at construction,
    and the disposition rule independently refuses COMPLETE unless every
    status is PASS or NOT_APPLICABLE.
    """

    GATE = HARD_GATE_IDS_TUPLE[3]
    NA_GATE = "SUBCONTRACTING_CORE_TASKS"

    def _run(self, evaluations) -> HardGateRunResult:
        return HardGateRunResult.from_evaluations(
            iteration_number=ITERATION,
            proposal_hash="a" * 64,
            evaluations=evaluations,
        )

    def _all_pass(self) -> list[HardGateEvaluation]:
        return [
            HardGateEvaluation(gate_id=g, status=ProposalHardGateStatus.PASS)
            for g in HARD_GATE_IDS_TUPLE
        ]

    # -- inconsistent pairs are refused at CONSTRUCTION ---------------------
    def test_fail_with_none_failure_class_cannot_construct(self):
        with pytest.raises(ValueError):
            HardGateEvaluation(
                gate_id=self.GATE,
                status=ProposalHardGateStatus.FAIL,
                failure_class=HardGateFailureClass.NONE,
            )
        # The deserialization path is equally refused.
        with pytest.raises(ValueError):
            HardGateEvaluation.from_dict(
                {
                    "gate_id": self.GATE,
                    "status": "FAIL",
                    "failure_class": "NONE",
                }
            )

    def test_pass_with_proposal_issue_cannot_construct(self):
        with pytest.raises(ValueError):
            HardGateEvaluation(
                gate_id=self.GATE,
                status=ProposalHardGateStatus.PASS,
                failure_class=HardGateFailureClass.PROPOSAL_ISSUE,
            )

    def test_not_applicable_with_evidence_failure_cannot_construct(self):
        for failure_class in (
            HardGateFailureClass.EVIDENCE_MISSING,
            HardGateFailureClass.EVIDENCE_INVALID,
        ):
            with pytest.raises(ValueError):
                HardGateEvaluation(
                    gate_id=self.GATE,
                    status=ProposalHardGateStatus.NOT_APPLICABLE,
                    failure_class=failure_class,
                )

    def test_warn_with_non_none_failure_class_cannot_construct(self):
        for failure_class in (
            HardGateFailureClass.PROPOSAL_ISSUE,
            HardGateFailureClass.EVIDENCE_MISSING,
            HardGateFailureClass.EVIDENCE_INVALID,
        ):
            with pytest.raises(ValueError):
                HardGateEvaluation(
                    gate_id=self.GATE,
                    status=ProposalHardGateStatus.WARN,
                    failure_class=failure_class,
                )

    # -- coherent pairs construct -------------------------------------------
    def test_warn_with_none_is_valid(self):
        evaluation = HardGateEvaluation(
            gate_id=self.GATE,
            status=ProposalHardGateStatus.WARN,
            failure_class=HardGateFailureClass.NONE,
        )
        assert evaluation.failure_class is HardGateFailureClass.NONE

    def test_pass_with_none_is_valid(self):
        evaluation = HardGateEvaluation(
            gate_id=self.GATE,
            status=ProposalHardGateStatus.PASS,
            failure_class=HardGateFailureClass.NONE,
        )
        assert evaluation.status is ProposalHardGateStatus.PASS

    def test_fail_with_proposal_issue_is_valid(self):
        evaluation = HardGateEvaluation(
            gate_id=self.GATE,
            status=ProposalHardGateStatus.FAIL,
            failure_class=HardGateFailureClass.PROPOSAL_ISSUE,
        )
        assert evaluation.failure_class is HardGateFailureClass.PROPOSAL_ISSUE

    def test_fail_with_evidence_missing_is_valid(self):
        evaluation = HardGateEvaluation(
            gate_id=self.GATE,
            status=ProposalHardGateStatus.FAIL,
            failure_class=HardGateFailureClass.EVIDENCE_MISSING,
        )
        assert evaluation.failure_class is HardGateFailureClass.EVIDENCE_MISSING

    # -- engine-level defense in depth ---------------------------------------
    def test_inconsistent_non_pass_status_cannot_complete(self):
        evaluations = self._all_pass()
        # Laundering attempt: a FAIL status appears WITHOUT a failure class
        # (post-construction mutation / future refactor).  The failure-class
        # routing finds nothing, so the engine-level guard must refuse.
        evaluations[3].status = ProposalHardGateStatus.FAIL
        with pytest.raises(HardGateEngineError):
            self._run(evaluations)
        # A stray WARN can never launder into COMPLETE either: it stays
        # INCOMPLETE.
        evaluations[3].status = ProposalHardGateStatus.WARN
        assert self._run(evaluations).disposition is (
            HardGateDisposition.INCOMPLETE
        )

    # -- the disposition rule itself -----------------------------------------
    def test_fourteen_pass_produces_complete(self):
        result = self._run(self._all_pass())
        assert result.disposition is HardGateDisposition.COMPLETE

    def test_pass_plus_justified_na_produces_complete(self):
        evaluations = self._all_pass()
        evaluations[HARD_GATE_IDS_TUPLE.index(self.NA_GATE)] = (
            HardGateEvaluation(
                gate_id=self.NA_GATE,
                status=ProposalHardGateStatus.NOT_APPLICABLE,
                message="no subcontracting exists in this programme",
                evidence="applicability.explicit_not_applicable",
                failure_class=HardGateFailureClass.NONE,
            )
        )
        result = self._run(evaluations)
        assert result.disposition is HardGateDisposition.COMPLETE

    def test_one_warn_remains_incomplete(self):
        evaluations = self._all_pass()
        evaluations[HARD_GATE_IDS_TUPLE.index("PAGE_LIMIT")] = (
            HardGateEvaluation(
                gate_id="PAGE_LIMIT",
                status=ProposalHardGateStatus.WARN,
                message="estimate",
                failure_class=HardGateFailureClass.NONE,
            )
        )
        result = self._run(evaluations)
        assert result.disposition is HardGateDisposition.INCOMPLETE

    def test_one_proposal_fail_is_revision_required(self):
        evaluations = self._all_pass()
        evaluations[HARD_GATE_IDS_TUPLE.index("MANDATORY_SECTIONS")] = (
            HardGateEvaluation(
                gate_id="MANDATORY_SECTIONS",
                status=ProposalHardGateStatus.FAIL,
                message="missing heading",
                failure_class=HardGateFailureClass.PROPOSAL_ISSUE,
            )
        )
        result = self._run(evaluations)
        assert result.disposition is HardGateDisposition.REVISION_REQUIRED

    def test_one_evidence_fail_is_blocked(self):
        evaluations = self._all_pass()
        evaluations[HARD_GATE_IDS_TUPLE.index("PAGE_LIMIT")] = (
            HardGateEvaluation(
                gate_id="PAGE_LIMIT",
                status=ProposalHardGateStatus.FAIL,
                message="PAGE_BUDGET.json is missing.",
                failure_class=HardGateFailureClass.EVIDENCE_MISSING,
            )
        )
        result = self._run(evaluations)
        assert result.disposition is HardGateDisposition.BLOCKED
