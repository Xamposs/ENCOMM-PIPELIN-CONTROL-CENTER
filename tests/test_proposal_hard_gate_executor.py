"""Session 016 — hard-gate RUNTIME executor, composition and isolation tests.

Covers the mandated FRESHNESS / OVERALL / ARTIFACTS / COMPOSITION / ISOLATION
matrix sections (brief §25, items 42–60):

* FRESHNESS: a stale proposal never runs gates and never COMPLETEs
  (STALE_REVIEW walks HARD_GATE_VALIDATION → REVISION_REQUIRED); missing
  review evidence fails closed.
* OVERALL: 14 PASS ⇒ COMPLETE; PASS + explicit N/A ⇒ COMPLETE; one WARN ⇒
  INCOMPLETE (never COMPLETE); one proposal FAIL ⇒ REVISION_REQUIRED with
  HARD_GATE_FEEDBACK.json; missing evidence ⇒ BLOCKED; COMPLETE stays
  terminal.
* ARTIFACTS: the durable per-iteration record is deterministic, an existing
  conflicting record fails closed (and leaves the machine untouched), the
  HARD_GATES snapshot updates safely, and no transcript/provider/model info
  is persisted.
* COMPOSITION: run_iteration() → READY_FOR_HARD_GATES → run_hard_gates() ⇒
  COMPLETE on a fully passing workspace; a proposal issue walks to
  REVISION_REQUIRED; evidence missing walks to BLOCKED.
* ISOLATION: the pure package stays import-clean (subprocess-proven), the
  runtime modules import no UI/persistence/core (subprocess-proven), Coding
  Mode files are untouched, and the new modules contain no provider names,
  no process/network surface and no transcript persistence.

All tests are OFFLINE and deterministic: real files in ``tmp_path``, zero
model calls, zero network.
"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

import encomm_pcc.proposal as pp
import encomm_pcc.proposal_runtime as prt
from encomm_pcc.proposal.enums import HARD_GATE_IDS_TUPLE, ProposalPhase, ProposalRole
from encomm_pcc.proposal.hard_gates import (
    HARD_GATE_EVIDENCE_SCHEMA,
    HardGateDisposition,
    HardGateEvidenceError,
)
from encomm_pcc.proposal.state_machine import ProposalStateMachine
from encomm_pcc.drivers.base import DriverSession, PromptHandle, PromptResult

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
PYTHON = sys.executable

ITERATION = 2
GOOD_TEXT = "# 1. Excellence\n\n## 2. Impact\n\nKALHAS drives the architecture.\n"

GOOD_SECTIONS: dict = {
    "mandatory_sections": {"required_headings": ["1. Excellence", "2. Impact"]},
    "challenge_mapping": {
        "required_markers": ["C1"],
        "mappings": [
            {"marker": "C1", "proposal_section": "1. Excellence", "evidence_text": "yes"}
        ],
    },
    "terminology": {
        "rules": [{"canonical": "KALHAS", "forbidden_aliases": ["Kalchas"]}]
    },
    "workplan": {
        "work_packages": [
            {
                "id": "WP1",
                "tasks": [{"id": "T1.1", "owner": "AAI", "person_months": 12}],
                "person_months": 12,
                "deliverables": [{"id": "D1.1", "task_ids": ["T1.1"]}],
                "milestones": [{"id": "MS1", "wp_ref": "WP1"}],
            }
        ],
        "total_person_months": 12,
    },
    "budget": {
        "categories": [{"name": "staff", "amount": 100}],
        "participant_total": 100,
        "declared_total": 100,
    },
    "subcontracting": {"entries": []},
}

RUNTIME_HG_MODULES = (
    "encomm_pcc.proposal_runtime.hard_gate_runner",
    "encomm_pcc.proposal_runtime.hard_gate_artifacts",
)
PURE_HG_MODULES = (
    "encomm_pcc.proposal.hard_gates",
    "encomm_pcc.proposal.hard_gate_validators",
)


# ---------------------------------------------------------------------------
# seed helpers
# ---------------------------------------------------------------------------
def build_workspace(
    tmp_path: Path,
    *,
    text: str = GOOD_TEXT,
    iteration: int = ITERATION,
    applicability: dict | None = None,
    sections: dict | None = None,
    page_method: str = "pdf-layout-exact",
) -> tuple[Path, str]:
    """Seed a fully valid hard-gate workspace; return ``(workspace, hash)``."""
    ws = pp.ProposalWorkspace(tmp_path)
    ws.initialize()
    ws.master_proposal_path().write_bytes(text.encode("utf-8"))
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
        '{"sources": []}', encoding="utf-8", newline="\n"
    )
    (evidence_dir / "CLAIM_LEDGER.json").write_text(
        '{"claims": []}', encoding="utf-8", newline="\n"
    )
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


def run_gates(ws: Path, iteration: int = ITERATION):
    machine = ProposalStateMachine(ProposalPhase.HARD_GATE_VALIDATION)
    report = prt.run_hard_gates(
        workspace=ws, state_machine=machine, iteration_number=iteration
    )
    return machine, report


def write_json(path: Path, payload) -> None:
    path.write_text(
        json.dumps(payload, indent=2), encoding="utf-8", newline="\n"
    )


# ---------------------------------------------------------------------------
# FRESHNESS (items 42–43)
# ---------------------------------------------------------------------------
class TestFreshness:
    def test_42_stale_proposal_never_runs_gates(self, tmp_path):
        ws, ws_hash = build_workspace(tmp_path)
        # Change the master AFTER the review bundle was written: stale.
        ws.joinpath("03_PROPOSAL", "MASTER_PROPOSAL.md").write_bytes(
            b"# 1. Excellence\n\nedited after review.\n"
        )
        machine, report = run_gates(ws)
        assert report.outcome is prt.ProposalHardGateRunOutcome.STALE_REVIEW
        assert machine.phase is ProposalPhase.REVISION_REQUIRED
        # No gates ran: no durable artifact, no snapshot, no feedback.
        assert report.gate_results == []
        assert report.durable_artifact_path == ""
        assert not (ws / "04_REVIEWS" / f"iteration_{ITERATION:03d}" / "hard_gates.json").exists()
        assert not (ws / "05_CONTROL" / "HARD_GATE_FEEDBACK.json").exists()
        assert report.disposition == ""
        assert "gates were NOT evaluated" in report.error

    def test_43_stale_proposal_never_completes(self, tmp_path):
        ws, _ws_hash = build_workspace(tmp_path)
        ws.joinpath("03_PROPOSAL", "MASTER_PROPOSAL.md").write_bytes(b"edited\n")
        machine, report = run_gates(ws)
        assert machine.phase is ProposalPhase.REVISION_REQUIRED
        assert report.outcome is not prt.ProposalHardGateRunOutcome.COMPLETE

    def test_43b_missing_review_evidence_fails_closed(self, tmp_path):
        ws, _ws_hash = build_workspace(tmp_path)
        (ws / "04_REVIEWS" / f"iteration_{ITERATION:03d}" / "review_bundle.json").unlink()
        machine, report = run_gates(ws)
        # Never interpreted as fresh; no transition, loud failure.
        assert report.outcome is prt.ProposalHardGateRunOutcome.RUN_FAILED
        assert machine.phase is ProposalPhase.HARD_GATE_VALIDATION
        assert "no_review_iterations" in report.error or "latest_bundle" in report.error

    def test_43c_wrong_state_refused_without_transition(self, tmp_path):
        ws, _ws_hash = build_workspace(tmp_path)
        machine = ProposalStateMachine(ProposalPhase.INTEGRATION)
        report = prt.run_hard_gates(
            workspace=ws, state_machine=machine, iteration_number=ITERATION
        )
        assert report.outcome is prt.ProposalHardGateRunOutcome.RUN_FAILED
        assert machine.phase is ProposalPhase.INTEGRATION


# ---------------------------------------------------------------------------
# OVERALL (items 44–50)
# ---------------------------------------------------------------------------
class TestOverallOutcomes:
    def test_44_fourteen_pass_complete(self, tmp_path):
        ws, _ws_hash = build_workspace(tmp_path)
        machine, report = run_gates(ws)
        assert machine.phase is ProposalPhase.COMPLETE
        assert report.disposition == "COMPLETE"
        assert report.counts["pass"] == 14
        assert report.counts["fail"] == 0
        assert report.counts["warn"] == 0

    def test_45_pass_plus_valid_na_complete(self, tmp_path):
        ws, _ = build_workspace(
            tmp_path,
            applicability={
                "CHALLENGE_MAPPING": {
                    "applicable": False,
                    "reason": "the call has no challenge ID list",
                }
            },
        )
        machine, report = run_gates(ws)
        assert report.outcome is prt.ProposalHardGateRunOutcome.COMPLETE
        assert machine.phase is ProposalPhase.COMPLETE
        na = next(
            g for g in report.gate_results if g["gate_id"] == "CHALLENGE_MAPPING"
        )
        assert na["status"] == "NOT_APPLICABLE"
        assert "no challenge ID list" in na["message"]

    def test_46_one_warn_not_complete(self, tmp_path):
        ws, _ = build_workspace(tmp_path, page_method="operator estimate")
        machine, report = run_gates(ws)
        assert report.outcome is prt.ProposalHardGateRunOutcome.INCOMPLETE
        assert machine.phase is ProposalPhase.HARD_GATE_VALIDATION
        warn = next(g for g in report.gate_results if g["gate_id"] == "PAGE_LIMIT")
        assert warn["status"] == "WARN"
        assert report.counts["warn"] == 1
        # Artifacts still durable (the run DID happen) but never COMPLETE.
        assert Path(report.durable_artifact_path).exists()

    def test_47_one_proposal_fail_revision_required(self, tmp_path):
        ws, _ = build_workspace(tmp_path)
        # Break the mandatory sections: a real proposal issue.
        evidence_path = ws / "05_CONTROL" / "HARD_GATE_EVIDENCE.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["mandatory_sections"]["required_headings"] = [
            "1. Excellence",
            "9. Missing Section",
        ]
        write_json(evidence_path, evidence)
        machine, report = run_gates(ws)
        assert report.outcome is prt.ProposalHardGateRunOutcome.REVISION_REQUIRED
        assert machine.phase is ProposalPhase.REVISION_REQUIRED
        assert report.counts["fail"] == 1

    def test_48_hard_gate_feedback_written(self, tmp_path):
        ws, _ = build_workspace(tmp_path)
        evidence_path = ws / "05_CONTROL" / "HARD_GATE_EVIDENCE.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["mandatory_sections"]["required_headings"] = ["9. Missing"]
        write_json(evidence_path, evidence)
        machine, report = run_gates(ws)
        assert report.outcome is prt.ProposalHardGateRunOutcome.REVISION_REQUIRED
        feedback_path = Path(report.feedback_path)
        assert feedback_path.is_file()
        feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
        assert feedback["schema"] == prt.HARD_GATE_FEEDBACK_SCHEMA
        assert feedback["iteration_number"] == ITERATION
        assert feedback["proposal_hash"]
        failed = feedback["failed_gates"]
        assert [g["gate_id"] for g in failed] == ["MANDATORY_SECTIONS"]
        assert failed[0]["status"] == "FAIL"
        assert failed[0]["failure_class"] == "PROPOSAL_ISSUE"
        assert "9. Missing" in failed[0]["message"]
        # The feedback file carries the CURRENT revision demand (deterministic
        # structured context only — no prose generation).
        assert feedback["overall_outcome"] == "REVISION_REQUIRED"

    def test_49_missing_evidence_blocked(self, tmp_path):
        ws, _ = build_workspace(tmp_path)
        (ws / "05_CONTROL" / "HARD_GATE_EVIDENCE.json").unlink()
        machine, report = run_gates(ws)
        assert report.outcome is prt.ProposalHardGateRunOutcome.BLOCKED
        assert machine.phase is ProposalPhase.BLOCKED
        # The report distinguishes evidence failure from proposal failure.
        assert "evidence" in report.error.lower()
        assert report.gate_results == []
        # Also BLOCKED: evidence bound to a STALE hash.
        ws2, _ = build_workspace(Path(str(tmp_path) + "_stale"))
        evidence_path2 = ws2 / "05_CONTROL" / "HARD_GATE_EVIDENCE.json"
        evidence2 = json.loads(evidence_path2.read_text(encoding="utf-8"))
        evidence2["proposal_hash"] = "c" * 64
        write_json(evidence_path2, evidence2)
        machine2, report2 = run_gates(ws2)
        assert report2.outcome is prt.ProposalHardGateRunOutcome.BLOCKED
        assert machine2.phase is ProposalPhase.BLOCKED

    def test_50_complete_terminal_behavior_preserved(self, tmp_path):
        ws, _ = build_workspace(tmp_path)
        machine, report = run_gates(ws)
        assert machine.phase is ProposalPhase.COMPLETE
        # COMPLETE has no outgoing edges — re-running gates is refused.
        report2 = prt.run_hard_gates(
            workspace=ws, state_machine=machine, iteration_number=ITERATION
        )
        assert report2.outcome is prt.ProposalHardGateRunOutcome.RUN_FAILED
        assert machine.phase is ProposalPhase.COMPLETE
        # The graph validator still pins COMPLETE's terminality.
        assert pp.validate_proposal_graph() == []


# ---------------------------------------------------------------------------
# ARTIFACTS (items 51–54)
# ---------------------------------------------------------------------------
class TestArtifacts:
    def test_51_durable_hard_gates_json_deterministic(self, tmp_path):
        ws_a, _ = build_workspace(tmp_path / "a")
        ws_b, _ = build_workspace(tmp_path / "b")
        machine_a, report_a = run_gates(ws_a)
        machine_b, report_b = run_gates(ws_b)
        assert report_a.outcome is prt.ProposalHardGateRunOutcome.COMPLETE
        bytes_a = Path(report_a.durable_artifact_path).read_bytes()
        bytes_b = Path(report_b.durable_artifact_path).read_bytes()
        assert bytes_a == bytes_b
        artifact = json.loads(bytes_a.decode("utf-8"))
        assert artifact["schema"] == prt.HARD_GATES_ARTIFACT_SCHEMA
        assert artifact["iteration_number"] == ITERATION
        assert artifact["proposal_hash"]
        assert [g["gate_id"] for g in artifact["gate_results"]] == list(
            HARD_GATE_IDS_TUPLE
        )
        assert artifact["overall_outcome"] == "COMPLETE"
        assert artifact["counts"]["total"] == 14
        assert "HARD_GATE_EVIDENCE.json" in artifact["evidence_fingerprints"]

    def test_52_conflict_fails_closed(self, tmp_path):
        ws, _ = build_workspace(tmp_path)
        # Pre-seed a CONFLICTING durable artifact for this iteration.
        durable_dir = ws / "04_REVIEWS" / f"iteration_{ITERATION:03d}"
        (durable_dir / "hard_gates.json").write_text(
            '{"schema": "something-else"}', encoding="utf-8"
        )
        machine, report = run_gates(ws)
        assert report.outcome is prt.ProposalHardGateRunOutcome.ARTIFACT_CONFLICT
        # D-060 convention: the machine position is untouched.
        assert machine.phase is ProposalPhase.HARD_GATE_VALIDATION
        # The conflicting evidence was never clobbered.
        assert (
            json.loads((durable_dir / "hard_gates.json").read_text(encoding="utf-8"))
            == {"schema": "something-else"}
        )

    def test_53_latest_hard_gates_snapshot_updated_safely(self, tmp_path):
        ws, _ = build_workspace(tmp_path)
        # The seed ships an empty HARD_GATES.json control file.
        assert (ws / "05_CONTROL" / "HARD_GATES.json").read_bytes() == b""
        machine, report = run_gates(ws)
        assert report.outcome is prt.ProposalHardGateRunOutcome.COMPLETE
        snapshot = json.loads(
            (ws / "05_CONTROL" / "HARD_GATES.json").read_text(encoding="utf-8")
        )
        assert snapshot["overall_outcome"] == "COMPLETE"
        # Replacing a zero-byte seed is the documented current-state policy;
        # no temp residue remains.
        residues = [p for p in (ws / "05_CONTROL").iterdir() if p.name.startswith(".tmp-")]
        assert residues == []
        # A second run over a SECOND workspace produces the identical snapshot.
        ws2, _ = build_workspace(tmp_path / "again")
        run_gates(ws2)
        assert (ws2 / "05_CONTROL" / "HARD_GATES.json").read_bytes() == (
            ws / "05_CONTROL" / "HARD_GATES.json").read_bytes()

    def test_54_no_transcript_provider_model_info(self, tmp_path):
        ws, _ = build_workspace(tmp_path)
        _machine, report = run_gates(ws)
        durable = Path(report.durable_artifact_path).read_text(encoding="utf-8")
        for fragment in ("provider", "model", "session_id", "driver", "transcript",
                         "raw_excerpt", "prompt"):
            assert fragment not in durable.lower(), fragment
        feedback_absent = report.feedback_path == ""
        assert feedback_absent  # COMPLETE run writes no feedback file

    def test_54b_artifact_helpers_reuse_and_conflict(self, tmp_path):
        ws, _ = build_workspace(tmp_path)
        machine, report = run_gates(ws)
        artifact = json.loads(
            Path(report.durable_artifact_path).read_text(encoding="utf-8")
        )
        # Identical reuse is accepted; anything else is a conflict.
        path = prt.write_iteration_hard_gates(
            workspace=ws, iteration_number=ITERATION, artifact=artifact
        )
        assert path.is_file()
        conflicting = dict(artifact)
        conflicting["overall_outcome"] = "BLOCKED"
        with pytest.raises(prt.HardGateArtifactConflictError):
            prt.write_iteration_hard_gates(
                workspace=ws, iteration_number=ITERATION, artifact=conflicting
            )


# ---------------------------------------------------------------------------
# COMPOSITION (items 55–57)
# ---------------------------------------------------------------------------
class ScriptedReviewer:
    """Scripted reviewer driver — zero engine involvement."""

    def __init__(
        self,
        role: ProposalRole,
        verdict: str = "PASS",
        iteration: int = 1,
    ) -> None:
        self.role = role
        self.verdict = verdict
        self.iteration = iteration
        self.driver_id = f"scripted-reviewer-{role.value.lower()}"
        self.started = 0
        self.prompt = ""

    def start_session(self, request):
        self.started += 1
        return DriverSession(
            driver_id=self.driver_id,
            role=request.role,
            session_id="ext-reviewer-1",
            external=True,
        )

    def resume_session(self, session_id, request):
        raise NotImplementedError

    def send_prompt(self, session, prompt):
        self.prompt = prompt
        return PromptHandle(session=session, prompt=prompt)

    def wait_for_completion(self, handle, timeout_s=None):
        payload = {
            "reviewer_role": self.role.value,
            "verdict": self.verdict,
            "summary": "Clean." if self.verdict == "PASS" else "Needs work.",
            "findings": [],
            "proposed_patches": [],
            "unverified_claims": [],
            "iteration_number": self.iteration,
        }
        return PromptResult(
            ok=True,
            text=(
                f"{pp.PROPOSAL_REVIEW_ENVELOPE_START}\n"
                f"{json.dumps(payload)}\n"
                f"{pp.PROPOSAL_REVIEW_ENVELOPE_END}"
            ),
            session_id="ext-reviewer-1",
            duration_s=0.001,
        )


def _reviewers(iteration: int) -> dict[ProposalRole, ScriptedReviewer]:
    return {
        role: ScriptedReviewer(role, verdict="PASS", iteration=iteration)
        for role in prt.REVIEW_SEQUENCE
    }


class TestComposition:
    def _prepare(self, tmp_path: Path) -> tuple[Path, str]:
        """A proposal workspace whose MASTER content passes ALL 14 gates."""
        return build_workspace(tmp_path)

    def _prepare_for_real_cycle(self, tmp_path: Path) -> tuple[Path, str]:
        """Like ``_prepare`` but WITHOUT the seeded review evidence.

        The REAL review cycle in the test writes its own durable artifacts;
        the seeded bundle would collide with it (fail-closed by design).
        """
        ws, ws_hash = build_workspace(tmp_path)
        import shutil

        shutil.rmtree(ws / "04_REVIEWS")
        return ws, ws_hash

    def test_55_clean_reviewed_proposal_all_pass_complete(self, tmp_path):
        ws, ws_hash = self._prepare_for_real_cycle(tmp_path)
        machine = ProposalStateMachine(ProposalPhase.IDLE)
        review = prt.run_review_cycle(
            workspace=ws,
            state_machine=machine,
            iteration_number=ITERATION,
            proposal_revision=f"rev-{ITERATION}",
            reviewer_drivers=_reviewers(ITERATION),
        )
        assert review.outcome is prt.ProposalReviewCycleOutcome.READY_FOR_INTEGRATION
        integration = prt.run_integration(
            workspace=ws,
            state_machine=machine,
            iteration_number=ITERATION,
            proposal_revision=f"rev-{ITERATION}",
            orchestrator_driver=None,
        )
        assert integration.outcome in (
            prt.ProposalIntegrationOutcome.NO_INTEGRATION_REQUIRED,
            prt.ProposalIntegrationOutcome.COMPLETED_NO_CHANGE,
        )
        assert machine.phase is ProposalPhase.HARD_GATE_VALIDATION
        gates = prt.run_hard_gates(
            workspace=ws, state_machine=machine, iteration_number=ITERATION
        )
        assert gates.outcome is prt.ProposalHardGateRunOutcome.COMPLETE
        assert machine.phase is ProposalPhase.COMPLETE

    def test_56_gate_proposal_issue_ready_for_next_iteration(self, tmp_path):
        ws, _ws_hash = self._prepare(tmp_path)
        machine = ProposalStateMachine(ProposalPhase.HARD_GATE_VALIDATION)
        # A proposal issue in the EVIDENCE (required heading absent).
        evidence_path = ws / "05_CONTROL" / "HARD_GATE_EVIDENCE.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["mandatory_sections"]["required_headings"] = ["9. Missing"]
        write_json(evidence_path, evidence)
        gates = prt.run_hard_gates(
            workspace=ws, state_machine=machine, iteration_number=ITERATION
        )
        assert gates.outcome is prt.ProposalHardGateRunOutcome.REVISION_REQUIRED
        assert machine.phase is ProposalPhase.REVISION_REQUIRED
        # The next review iteration starts from REVISION_REQUIRED (S015 edge).
        review2 = prt.run_review_cycle(
            workspace=ws,
            state_machine=machine,
            iteration_number=ITERATION + 1,
            proposal_revision=f"rev-{ITERATION + 1}",
            reviewer_drivers=_reviewers(ITERATION + 1),
        )
        assert review2.outcome is prt.ProposalReviewCycleOutcome.READY_FOR_INTEGRATION

    def test_57_evidence_missing_blocked(self, tmp_path):
        ws, _ws_hash = self._prepare(tmp_path)
        (ws / "05_CONTROL" / "HARD_GATE_EVIDENCE.json").unlink()
        machine = ProposalStateMachine(ProposalPhase.HARD_GATE_VALIDATION)
        gates = prt.run_hard_gates(
            workspace=ws, state_machine=machine, iteration_number=ITERATION
        )
        assert gates.outcome is prt.ProposalHardGateRunOutcome.BLOCKED
        assert machine.phase is ProposalPhase.BLOCKED


# ---------------------------------------------------------------------------
# ISOLATION (items 58–60)
# ---------------------------------------------------------------------------
def _run_and_report(code: str) -> list[str]:
    proc = subprocess.run(
        [PYTHON, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(REPO_ROOT),
    )
    if proc.returncode != 0:
        raise AssertionError(f"probe failed: {proc.stderr}")
    return proc.stdout.strip().splitlines()


class TestIsolation:
    def test_58_pure_package_still_import_clean(self) -> None:
        code = (
            "import sys; sys.path.insert(0, r'%s');\n"
            "import encomm_pcc.proposal as pp;\n"
            "banned = ('PySide6', 'encomm_pcc.core', 'encomm_pcc.domain',\n"
            " 'encomm_pcc.drivers', 'encomm_pcc.persistence',\n"
            " 'encomm_pcc.proposal_runtime', 'encomm_pcc.ui')\n"
            "hits = [m for m in sys.modules for b in banned\n"
            "        if m == b or m.startswith(b + '.')]\n"
            "print('\\n'.join(hits))\n" % str(SRC_ROOT)
        )
        assert _run_and_report(code) == []

    def test_58b_pure_hard_gate_modules_import_clean(self) -> None:
        mods = ", ".join(repr(m) for m in PURE_HG_MODULES)
        code = (
            "import sys; sys.path.insert(0, r'%s');\n"
            "for m in (%s,): __import__(m)\n"
            "banned = ('PySide6', 'encomm_pcc.core', 'encomm_pcc.domain',\n"
            " 'encomm_pcc.drivers', 'encomm_pcc.persistence',\n"
            " 'encomm_pcc.proposal_runtime', 'encomm_pcc.ui')\n"
            "hits = [m for m in sys.modules for b in banned\n"
            "        if m == b or m.startswith(b + '.')]\n"
            "print('\\n'.join(hits))\n" % (str(SRC_ROOT), mods)
        )
        assert _run_and_report(code) == []

    def test_58c_runtime_hard_gate_modules_import_no_ui_persistence(self) -> None:
        mods = ", ".join(repr(m) for m in RUNTIME_HG_MODULES)
        code = (
            "import sys; sys.path.insert(0, r'%s');\n"
            "for m in (%s,): __import__(m)\n"
            "banned = ('PySide6', 'encomm_pcc.persistence', 'encomm_pcc.ui',\n"
            " 'encomm_pcc.core')\n"
            "hits = [m for m in sys.modules for b in banned\n"
            "        if m == b or m.startswith(b + '.')]\n"
            "print('\\n'.join(hits))\n" % (str(SRC_ROOT), mods)
        )
        assert _run_and_report(code) == []

    def test_59_coding_mode_production_files_untouched(self) -> None:
        # No Coding Mode production file may reference the proposal domain.
        # Session 017 (by design): the UI package now hosts the Proposal Mode
        # operator surface (proposal_mode.py + proposal_worker.py) and the
        # MainWindow wiring — the core pipeline packages stay proposal-free.
        proposal_ui_modules = {
            SRC_ROOT / "encomm_pcc" / "ui" / "proposal_mode.py",
            SRC_ROOT / "encomm_pcc" / "ui" / "proposal_worker.py",
            # Session 017 wiring: the mode-stack surface + the Simple Mode
            # navigation affordance (brief §2: changes limited to it).
            SRC_ROOT / "encomm_pcc" / "ui" / "main_window.py",
            SRC_ROOT / "encomm_pcc" / "ui" / "simple_mode.py",
            # Session 020: the readiness-disclaimer label module (imports
            # ONLY proposal.readiness's constant; name-only exemption).
            SRC_ROOT / "encomm_pcc" / "ui" / "readiness_disclaimer.py",
        }
        offenders: list[str] = []
        for pkg in ("core", "domain", "drivers", "persistence", "ui"):
            for path in (SRC_ROOT / "encomm_pcc" / pkg).rglob("*.py"):
                if path in proposal_ui_modules:
                    continue
                text = path.read_text(encoding="utf-8")
                if "proposal" in text:
                    offenders.append(str(path.relative_to(REPO_ROOT)))
        assert offenders == []

    def test_60_no_ai_calls_provider_names_or_transcripts(self) -> None:
        forbidden = (
            "import socket",
            "import requests",
            "import httpx",
            "urllib.request",
            "subprocess",
        )
        provider_names = (
            "codex",
            "hermes",
            "openrouter",
            "glm",
            "minimax",
            "claude",
            "openai",
            "anthropic",
        )
        offenders: list[str] = []
        for name in PURE_HG_MODULES + RUNTIME_HG_MODULES:
            rel = Path(*name.split(".")).with_suffix(".py")
            text = (SRC_ROOT / rel).read_text(encoding="utf-8")
            lowered = text.lower()
            for fragment in forbidden:
                if fragment in text:
                    offenders.append(f"{name}: {fragment}")
            for provider in provider_names:
                if provider in lowered:
                    offenders.append(f"{name}: provider:{provider}")
            for mechanism in ("raw_excerpt", "prompt_text", "raw_text"):
                if mechanism in text:
                    offenders.append(f"{name}: {mechanism}")
        assert offenders == []
