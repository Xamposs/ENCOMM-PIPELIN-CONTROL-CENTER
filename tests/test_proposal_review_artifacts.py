"""Session 014 — Proposal review ARTIFACT tests (04_REVIEWS/ + 06_VERSIONS/).

Covers the mandated matrix sections:

* ARTIFACTS (32-38): atomic writes (no temp residue), deterministic JSON
  (sorted keys, indent 2, LF, newline at EOF), existing identical artifact
  is safe evidence, conflicting artifact rejected, previous iteration never
  overwritten, zero-padded iteration names, full transcripts never
  persisted (the executor's bounded ``raw_excerpt`` is dropped at the
  artifact boundary).
* Aggregation contract: deterministic verdict rule + canonical finding
  ordering (severity → reviewer → original order), exact-duplicate MARKING
  (never semantic dedup), mixed-iteration/mixed-role refusal, brief
  projection.

All tests are OFFLINE; no engine, no network, no model output.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import encomm_pcc.proposal as pp
from encomm_pcc.proposal.review_aggregation import (
    INTEGRATION_BRIEF_SCHEMA,
    aggregate_reviews,
    build_integration_brief,
)
from encomm_pcc.proposal_runtime.review_artifacts import (
    BRIEF_FILENAME,
    BUNDLE_FILENAME,
    REVIEW_ARTIFACTS_SCHEMA,
    ArtifactConflictError,
    ReviewArtifactWriter,
    persist_cycle_artifacts,
)
from encomm_pcc.proposal_runtime.version_freeze import (
    VersionFreezeError,
    format_iteration_name,
    freeze_pre_review_version,
)

PROPOSAL_BYTES = b"# MASTER PROPOSAL\n\nArtifact-bound content.\n"
HASH = "a" * 64


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def result_for(
    role: pp.ProposalRole,
    verdict: str = "PASS",
    findings: list[pp.ProposalFinding] | None = None,
    patches: list[pp.ProposalPatch] | None = None,
    claims: list[str] | None = None,
    iteration: int = 1,
) -> pp.ProposalReviewResult:
    return pp.ProposalReviewResult(
        reviewer_role=role,
        verdict=pp.ProposalReviewVerdict(verdict),
        summary="Summary.",
        findings=findings or [],
        proposed_patches=patches or [],
        unverified_claims=claims or [],
        iteration_number=iteration,
    )


def pass_results(iteration: int = 1) -> dict[pp.ProposalRole, pp.ProposalReviewResult]:
    return {
        role: result_for(role, "PASS", iteration=iteration)
        for role in pp.REVIEWER_ORDER
    }


def make_bundle(
    tmp_path: Path,
    iteration: int = 1,
    results: dict[pp.ProposalRole, pp.ProposalReviewResult] | None = None,
) -> pp.ProposalReviewBundle:
    results = results or pass_results(iteration)
    return aggregate_reviews(
        iteration_number=iteration,
        proposal_revision="rev-1",
        proposal_hash=HASH,
        scientific_review=results[pp.ProposalRole.SCIENTIFIC_REVIEWER],
        implementation_review=results[pp.ProposalRole.PROPOSAL_ENGINEER],
        red_team_review=results[pp.ProposalRole.RED_TEAM_REVIEWER],
    )


def make_writer(tmp_path: Path, iteration: int = 1) -> ReviewArtifactWriter:
    return ReviewArtifactWriter(
        workspace=tmp_path,
        iteration_number=iteration,
        proposal_revision="rev-1",
        proposal_hash=HASH,
    )


class _Execution:
    """Minimal executor-report surface for artifact metadata tests."""

    class _Outcome:
        def __init__(self, value: str) -> None:
            self.value = value

    def __init__(self) -> None:
        self.outcome = self._Outcome("COMPLETED")
        self.session_id = "ext-session-9"
        self.driver_id = "scripted-artifacts"
        self.duration_s = 1.25
        self.state_advanced = True
        self.raw_excerpt = "RAW MODEL OUTPUT THAT MUST NEVER BE PERSISTED"


# ---------------------------------------------------------------------------
# ARTIFACTS (32-38)
# ---------------------------------------------------------------------------
class TestArtifactWrites:
    def test_32_atomic_write_no_temp_residue(self, tmp_path):
        writer = make_writer(tmp_path)
        bundle = make_bundle(tmp_path)
        persist_cycle_artifacts(
            workspace=tmp_path, bundle=bundle, executions={}
        )
        names = [p.name for p in (tmp_path / "04_REVIEWS" / "iteration_001").iterdir()]
        assert not any(name.startswith(".tmp-") for name in names)
        assert names == sorted([
            "implementation_review.json",
            "integration_brief.json",
            "red_team_review.json",
            "review_bundle.json",
            "scientific_review.json",
        ])

    def test_33_deterministic_json_formatting(self, tmp_path):
        writer = make_writer(tmp_path)
        result = result_for(pp.ProposalRole.SCIENTIFIC_REVIEWER)
        path = writer.write_review_artifact(
            pp.ProposalRole.SCIENTIFIC_REVIEWER, result=result
        )
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
        # Canonical formatting: indent 2, sort_keys, LF, EOF newline.
        expected = (
            json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        assert raw == expected
        assert b"\r\n" not in raw
        assert raw.endswith(b"\n")
        assert data["schema"] == REVIEW_ARTIFACTS_SCHEMA
        assert data["proposal_hash"] == HASH
        assert data["iteration_number"] == 1
        assert data["reviewer_role"] == "SCIENTIFIC_REVIEWER"

    def test_34_existing_identical_artifact_safe(self, tmp_path):
        writer = make_writer(tmp_path)
        result = result_for(pp.ProposalRole.SCIENTIFIC_REVIEWER)
        first = writer.write_review_artifact(
            pp.ProposalRole.SCIENTIFIC_REVIEWER, result=result
        )
        before = first.read_bytes()
        # Same identity again: treated as already-completed evidence.
        again = writer.write_review_artifact(
            pp.ProposalRole.SCIENTIFIC_REVIEWER, result=result
        )
        assert again == first
        assert first.read_bytes() == before

    def test_35_conflicting_artifact_rejected(self, tmp_path):
        writer = make_writer(tmp_path)
        result = result_for(pp.ProposalRole.SCIENTIFIC_REVIEWER)
        writer.write_review_artifact(
            pp.ProposalRole.SCIENTIFIC_REVIEWER, result=result
        )
        conflicting = result_for(
            pp.ProposalRole.SCIENTIFIC_REVIEWER, verdict="NEEDS_REVISION"
        )
        with pytest.raises(ArtifactConflictError):
            writer.write_review_artifact(
                pp.ProposalRole.SCIENTIFIC_REVIEWER, result=conflicting
            )

    def test_36_previous_iteration_never_overwritten(self, tmp_path):
        bundle1 = make_bundle(tmp_path, iteration=1)
        persist_cycle_artifacts(workspace=tmp_path, bundle=bundle1, executions={})
        iter1 = tmp_path / "04_REVIEWS" / "iteration_001"
        before = {p.name: p.read_bytes() for p in iter1.iterdir()}

        # Iteration 2 writes ITS OWN directory; iteration 1 untouched.
        bundle2 = make_bundle(tmp_path, iteration=2)
        persist_cycle_artifacts(workspace=tmp_path, bundle=bundle2, executions={})
        assert {p.name: p.read_bytes() for p in iter1.iterdir()} == before
        assert (tmp_path / "04_REVIEWS" / "iteration_002" / BUNDLE_FILENAME).exists()

    def test_37_iteration_names_zero_padded(self, tmp_path):
        assert format_iteration_name(1) == "iteration_001"
        assert format_iteration_name(42) == "iteration_042"
        assert format_iteration_name(999) == "iteration_999"
        with pytest.raises(VersionFreezeError):
            format_iteration_name(0)
        with pytest.raises(VersionFreezeError):
            format_iteration_name(1000)
        with pytest.raises(VersionFreezeError):
            format_iteration_name(-3)

    def test_38_full_transcripts_never_persisted(self, tmp_path):
        writer = make_writer(tmp_path)
        bundle = make_bundle(tmp_path)
        # The execution double carries a raw excerpt; the writer must drop it.
        persist_cycle_artifacts(
            workspace=tmp_path,
            bundle=bundle,
            executions={role: _Execution() for role in pp.REVIEWER_ORDER},
        )
        for path in (tmp_path / "04_REVIEWS" / "iteration_001").iterdir():
            text = path.read_text(encoding="utf-8")
            assert "RAW MODEL OUTPUT" not in text
            assert "ENCOMM_PROPOSAL_REVIEW" not in text
        # Runtime metadata IS present (structured, whitelisted).
        sci = json.loads(
            (tmp_path / "04_REVIEWS" / "iteration_001" / "scientific_review.json").read_text(
                encoding="utf-8"
            )
        )
        assert sci["runtime"]["outcome"] == "COMPLETED"
        assert sci["session_id"] == "ext-session-9"
        assert sci["driver_id"] == "scripted-artifacts"

    def test_bundle_conflict_refused(self, tmp_path):
        bundle = make_bundle(tmp_path)
        writer = make_writer(tmp_path)
        writer.write_bundle(bundle)
        with pytest.raises(ArtifactConflictError):
            writer.write_bundle(bundle)

    def test_brief_conflict_refused(self, tmp_path):
        bundle = make_bundle(tmp_path)
        writer = make_writer(tmp_path)
        writer.write_brief(bundle)
        with pytest.raises(ArtifactConflictError):
            writer.write_brief(bundle)

    def test_corrupt_existing_artifact_fails_closed(self, tmp_path):
        writer = make_writer(tmp_path)
        path = writer.review_artifact_path(pp.ProposalRole.SCIENTIFIC_REVIEWER)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"{not json at all", )
        with pytest.raises(ArtifactConflictError):
            writer.load_existing_review_result(pp.ProposalRole.SCIENTIFIC_REVIEWER)


# ---------------------------------------------------------------------------
# VERSION FREEZE
# ---------------------------------------------------------------------------
class TestVersionFreeze:
    def test_freeze_exact_bytes_and_sidecar(self, tmp_path):
        master = tmp_path / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
        master.parent.mkdir(parents=True, exist_ok=True)
        master.write_bytes(PROPOSAL_BYTES)
        md, cycle_hash = freeze_pre_review_version(
            workspace=tmp_path,
            master_proposal_path=master,
            iteration_number=1,
            proposal_revision="rev-1",
        )
        assert md.read_bytes() == PROPOSAL_BYTES  # byte-exact
        import hashlib

        assert cycle_hash == hashlib.sha256(PROPOSAL_BYTES).hexdigest()
        sidecar = json.loads(
            (tmp_path / "06_VERSIONS" / "iteration_001_pre_review.json").read_text(
                encoding="utf-8"
            )
        )
        assert sidecar == {
            "hash_algorithm": pp.PROPOSAL_HASH_ALGORITHM,
            "iteration_number": 1,
            "master_proposal_relpath": "03_PROPOSAL/MASTER_PROPOSAL.md",
            "proposal_hash": cycle_hash,
            "proposal_revision": "rev-1",
        }

    def test_freeze_reuse_identical(self, tmp_path):
        master = tmp_path / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
        master.parent.mkdir(parents=True, exist_ok=True)
        master.write_bytes(PROPOSAL_BYTES)
        md1, hash1 = freeze_pre_review_version(
            workspace=tmp_path,
            master_proposal_path=master,
            iteration_number=1,
            proposal_revision="rev-1",
        )
        md2, hash2 = freeze_pre_review_version(
            workspace=tmp_path,
            master_proposal_path=master,
            iteration_number=1,
            proposal_revision="rev-1",
        )
        assert md1 == md2 and hash1 == hash2

    def test_freeze_conflict_different_bytes(self, tmp_path):
        master = tmp_path / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
        master.parent.mkdir(parents=True, exist_ok=True)
        master.write_bytes(PROPOSAL_BYTES)
        freeze_pre_review_version(
            workspace=tmp_path,
            master_proposal_path=master,
            iteration_number=1,
            proposal_revision="rev-1",
        )
        master.write_bytes(b"changed content")
        with pytest.raises(VersionFreezeError) as excinfo:
            freeze_pre_review_version(
                workspace=tmp_path,
                master_proposal_path=master,
                iteration_number=1,
                proposal_revision="rev-1",
            )
        assert excinfo.value.reason in ("version_conflict", "hash_mismatch")

    def test_freeze_missing_master(self, tmp_path):
        with pytest.raises(VersionFreezeError) as excinfo:
            freeze_pre_review_version(
                workspace=tmp_path,
                master_proposal_path=tmp_path / "nope" / "MASTER_PROPOSAL.md",
                iteration_number=1,
                proposal_revision="rev-1",
            )
        assert excinfo.value.reason == "unreadable_master"


# ---------------------------------------------------------------------------
# AGGREGATION CONTRACT
# ---------------------------------------------------------------------------
class TestAggregation:
    def test_verdict_rule_blocked_dominates(self):
        results = {
            pp.ProposalRole.SCIENTIFIC_REVIEWER: result_for(
                pp.ProposalRole.SCIENTIFIC_REVIEWER, "NEEDS_REVISION"
            ),
            pp.ProposalRole.PROPOSAL_ENGINEER: result_for(
                pp.ProposalRole.PROPOSAL_ENGINEER, "BLOCKED"
            ),
            pp.ProposalRole.RED_TEAM_REVIEWER: result_for(
                pp.ProposalRole.RED_TEAM_REVIEWER, "PASS"
            ),
        }
        bundle = make_bundle(Path("."), results=results)
        assert bundle.aggregate_verdict is pp.ProposalCycleVerdict.BLOCKED

    def test_verdict_rule_needs_revision_when_any(self):
        results = pass_results()
        results[pp.ProposalRole.RED_TEAM_REVIEWER] = result_for(
            pp.ProposalRole.RED_TEAM_REVIEWER, "NEEDS_REVISION"
        )
        bundle = make_bundle(Path("."), results=results)
        assert bundle.aggregate_verdict is pp.ProposalCycleVerdict.NEEDS_REVISION

    def test_verdict_rule_pass_requires_all_three(self):
        bundle = make_bundle(Path("."))
        assert bundle.aggregate_verdict is pp.ProposalCycleVerdict.PASS

    def test_finding_order_severity_then_reviewer_then_original(self):
        def mk(sev: str, message: str) -> pp.ProposalFinding:
            return pp.ProposalFinding(
                severity=sev, category="weak_wording", section="s",
                message=message,
            )

        sci = result_for(
            pp.ProposalRole.SCIENTIFIC_REVIEWER, "NEEDS_REVISION",
            findings=[mk("low", "sci-low-1"), mk("high", "sci-high-1")],
        )
        eng = result_for(
            pp.ProposalRole.PROPOSAL_ENGINEER, "NEEDS_REVISION",
            findings=[mk("medium", "eng-med-1"), mk("high", "eng-high-1"),
                      mk("medium", "eng-med-2")],
        )
        red = result_for(
            pp.ProposalRole.RED_TEAM_REVIEWER, "NEEDS_REVISION",
            findings=[mk("critical", "red-crit-1")],
        )
        bundle = aggregate_reviews(
            iteration_number=1, proposal_revision="rev", proposal_hash=HASH,
            scientific_review=sci, implementation_review=eng,
            red_team_review=red,
        )
        order = [
            (item.finding.message, item.reviewer_role)
            for item in bundle.findings
        ]
        assert order == [
            ("red-crit-1", pp.ProposalRole.RED_TEAM_REVIEWER),
            ("sci-high-1", pp.ProposalRole.SCIENTIFIC_REVIEWER),
            ("eng-high-1", pp.ProposalRole.PROPOSAL_ENGINEER),
            ("eng-med-1", pp.ProposalRole.PROPOSAL_ENGINEER),
            ("eng-med-2", pp.ProposalRole.PROPOSAL_ENGINEER),
            ("sci-low-1", pp.ProposalRole.SCIENTIFIC_REVIEWER),
        ]

    def test_exact_duplicates_marked_not_merged(self):
        def mk(message: str) -> pp.ProposalFinding:
            return pp.ProposalFinding(
                severity="medium", category="weak_wording", section="s",
                message=message,
            )

        sci = result_for(
            pp.ProposalRole.SCIENTIFIC_REVIEWER, "NEEDS_REVISION",
            findings=[mk("same issue"), mk("same issue")],  # exact duplicates
        )
        eng = result_for(
            pp.ProposalRole.PROPOSAL_ENGINEER, "NEEDS_REVISION",
            findings=[mk("same issue")],
        )
        red = result_for(
            pp.ProposalRole.RED_TEAM_REVIEWER, "NEEDS_REVISION",
            findings=[mk("different issue")],
        )
        bundle = aggregate_reviews(
            iteration_number=1, proposal_revision="rev", proposal_hash=HASH,
            scientific_review=sci, implementation_review=eng,
            red_team_review=red,
        )
        assert len(bundle.findings) == 4  # nothing merged away
        dupes = [
            (i, item.duplicate_of_index)
            for i, item in enumerate(bundle.findings)
            if item.duplicate_of_index is not None
        ]
        assert dupes == [(1, 0), (2, 0)]  # deterministic first-occurrence

    def test_mixed_iteration_refused(self):
        results = pass_results()
        results[pp.ProposalRole.RED_TEAM_REVIEWER] = result_for(
            pp.ProposalRole.RED_TEAM_REVIEWER, "PASS", iteration=2
        )
        with pytest.raises(ValueError, match="mixed iterations"):
            make_bundle(Path("."), results=results)

    def test_role_slot_mismatch_refused(self):
        # Both slots carry the WRONG role for their position.
        with pytest.raises(ValueError, match="role mismatch"):
            aggregate_reviews(
                iteration_number=1, proposal_revision="rev", proposal_hash=HASH,
                scientific_review=result_for(pp.ProposalRole.RED_TEAM_REVIEWER),
                implementation_review=result_for(pp.ProposalRole.PROPOSAL_ENGINEER),
                red_team_review=result_for(pp.ProposalRole.RED_TEAM_REVIEWER),
            )

    def test_bundle_round_trip(self, tmp_path):
        def mk(message: str, sev: str) -> pp.ProposalFinding:
            return pp.ProposalFinding(
                severity=sev, category="missing_evidence", section="2",
                message=message, source_refs=["SOT/TEAM.md"],
            )

        sci = result_for(
            pp.ProposalRole.SCIENTIFIC_REVIEWER, "NEEDS_REVISION",
            findings=[mk("a", "high")], claims=["claim-1"], iteration=3,
        )
        eng = result_for(
            pp.ProposalRole.PROPOSAL_ENGINEER, "PASS",
            patches=[pp.ProposalPatch(
                target_section="5", rationale="clarity",
                replacement_text="new text", confidence=0.9,
            )],
            iteration=3,
        )
        red = result_for(pp.ProposalRole.RED_TEAM_REVIEWER, "PASS", iteration=3)
        bundle = aggregate_reviews(
            iteration_number=3, proposal_revision="rev-9", proposal_hash=HASH,
            scientific_review=sci, implementation_review=eng, red_team_review=red,
        )
        data = bundle.to_dict()
        revived = pp.ProposalReviewBundle.from_dict(data)
        assert revived.to_dict() == data
        assert revived.iteration_number == 3
        assert revived.aggregate_verdict is pp.ProposalCycleVerdict.NEEDS_REVISION

    def test_integration_brief_contract(self, tmp_path):
        def mk(message: str, sev: str, section: str) -> pp.ProposalFinding:
            return pp.ProposalFinding(
                severity=sev, category="structural_issue", section=section,
                message=message, source_refs=["01_OFFICIAL/OFFICIAL_REQUIREMENTS.md"],
            )

        sci = result_for(
            pp.ProposalRole.SCIENTIFIC_REVIEWER, "NEEDS_REVISION",
            findings=[mk("f1", "critical", "Section 2"), mk("f2", "low", "Section 7")],
        )
        eng = result_for(
            pp.ProposalRole.PROPOSAL_ENGINEER, "NEEDS_REVISION",
            patches=[pp.ProposalPatch(
                target_section="Section 9", rationale="trace",
                patch_instructions="fix", confidence=0.5,
            )],
        )
        red = result_for(
            pp.ProposalRole.RED_TEAM_REVIEWER, "PASS", claims=["overclaim X"]
        )
        bundle = aggregate_reviews(
            iteration_number=1, proposal_revision="rev", proposal_hash=HASH,
            scientific_review=sci, implementation_review=eng, red_team_review=red,
        )
        brief = build_integration_brief(bundle)
        assert brief["schema"] == INTEGRATION_BRIEF_SCHEMA
        assert brief["aggregate_verdict"] == "NEEDS_REVISION"
        assert brief["integration_required"] is True
        assert brief["reviewer_verdicts"] == {
            "SCIENTIFIC_REVIEWER": "NEEDS_REVISION",
            "PROPOSAL_ENGINEER": "NEEDS_REVISION",
            "RED_TEAM_REVIEWER": "PASS",
        }
        assert brief["counts_by_severity"] == {
            "critical": 1, "high": 0, "medium": 0, "low": 1,
        }
        assert brief["affected_sections"] == ["Section 2", "Section 7", "Section 9"]
        assert brief["source_refs"] == [
            "01_OFFICIAL/OFFICIAL_REQUIREMENTS.md"
        ]
        assert len(brief["findings"]) == 2
        assert len(brief["proposed_patches"]) == 1
        assert brief["unverified_claims"] == ["overclaim X"]

    def test_clean_pass_brief_integration_not_required(self, tmp_path):
        bundle = make_bundle(tmp_path)
        brief = build_integration_brief(bundle)
        assert brief["integration_required"] is False
        assert brief["aggregate_verdict"] == "PASS"
        assert brief["affected_sections"] == []
