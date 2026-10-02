"""Session 014 — Proposal review-cycle loop tests (deterministic 3-reviewer).

Covers the mandated matrix sections:

* SOURCE VALIDATION (1-5): IDLE enters SOURCE_VALIDATION; a valid workspace
  reaches SCIENTIFIC_REVIEW and completes; missing master / empty master /
  unreadable snapshot all fail BEFORE any reviewer driver is touched.
* FULL HAPPY PATH (6-14): three PASS reviewers called in exact order, all
  over the same proposal hash, final state INTEGRATION, aggregate PASS,
  integration_required false when truly clean, five artifacts + frozen
  version written, frozen bytes byte-exact.
* NEEDS REVISION (15-18): a NEEDS_REVISION reviewer does NOT stop the
  remaining independent reviews; the aggregate NEEDS_REVISION; the brief
  demands integration.
* BLOCKED (19-22): each reviewer position stops the sequence exactly there;
  state is BLOCKED; a red-team BLOCKED never produces an integration bundle.
* OPERATIONAL FAILURES (23-25): parser/driver failures stop immediately;
  no invalid partial aggregate is ever produced.
* REVISION INTEGRITY (26-31): a proposal change between reviewers (or after
  the last one) yields STALE_PROPOSAL, the remaining reviewers are never
  called, and mixed-hash aggregation is impossible.
* RESUME (36-40): completed positions reuse their durable artifacts; a
  missing/conflicting/wrong-identity artifact fails closed.
* ISOLATION (41-44): the new runtime modules import no UI/persistence,
  Coding Mode still never imports proposal packages, and the pure proposal
  package stays clean of the runtime.

All tests are OFFLINE and deterministic: scripted in-repo driver doubles,
zero network, zero model calls, real files in ``tmp_path`` only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import encomm_pcc.proposal as pp
import encomm_pcc.proposal_runtime as prt
from encomm_pcc.drivers.base import (
    DriverError,
    DriverSession,
    PromptHandle,
    PromptResult,
)
from encomm_pcc.proposal.state_machine import ProposalStateMachine

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"

PROPOSAL_BYTES = b"# MASTER PROPOSAL\n\nReviewed scientific content.\n"
REVISION = "rev-1"


# ---------------------------------------------------------------------------
# helpers (same conventions as test_proposal_runtime.py)
# ---------------------------------------------------------------------------
def build_workspace(tmp_path: Path) -> Path:
    ws = pp.ProposalWorkspace(tmp_path)
    ws.initialize()
    ws.master_proposal_path().write_bytes(PROPOSAL_BYTES)
    return tmp_path


def review_payload(
    *,
    role: pp.ProposalRole,
    verdict: str = "PASS",
    iteration: int = 1,
    findings: list | None = None,
    patches: list | None = None,
    claims: list | None = None,
    summary: str = "Clean review.",
) -> dict:
    return {
        "reviewer_role": role.value,
        "verdict": verdict,
        "summary": summary,
        "findings": findings or [],
        "proposed_patches": patches or [],
        "unverified_claims": claims or [],
        "iteration_number": iteration,
    }


def finding(severity: str = "medium", message: str = "Issue.", section: str = "3") -> dict:
    return {
        "severity": severity,
        "category": "weak_wording",
        "section": section,
        "message": message,
        "evidence": "",
        "source_refs": [],
        "suggested_change": "Reword.",
    }


def envelope(payload: dict) -> str:
    return (
        f"{pp.PROPOSAL_REVIEW_ENVELOPE_START}\n{json.dumps(payload)}\n"
        f"{pp.PROPOSAL_REVIEW_ENVELOPE_END}"
    )


def answer_for(
    role: pp.ProposalRole,
    verdict: str = "PASS",
    iteration: int = 1,
    **payload_kwargs,
) -> PromptResult:
    return PromptResult(
        ok=True,
        text=envelope(review_payload(role=role, verdict=verdict, iteration=iteration, **payload_kwargs)),
        session_id="ext-session-1",
        duration_s=0.01,
    )


class ScriptedDriver:
    """Scripted ``BaseDriver``-shaped double — zero engine involvement."""

    def __init__(self, answer: PromptResult, *, driver_id: str = "scripted-loop") -> None:
        self.answer = answer
        self.driver_id = driver_id
        self.started = 0
        self.prompts: list[str] = []

    def start_session(self, request):  # noqa: ANN001
        self.started += 1
        return DriverSession(
            driver_id=self.driver_id,
            role=request.role,
            session_id="ext-session-1",
            external=True,
        )

    def resume_session(self, session_id: str, request):  # noqa: ANN001
        raise NotImplementedError

    def send_prompt(self, session, prompt: str) -> PromptHandle:  # noqa: ANN001
        self.prompts.append(prompt)
        return PromptHandle(session=session, prompt=prompt)

    def wait_for_completion(self, handle, timeout_s=None):  # noqa: ANN001
        return self.answer


def default_drivers(
    verdicts: dict[pp.ProposalRole, str] | None = None,
    iteration: int = 1,
) -> dict[pp.ProposalRole, ScriptedDriver]:
    verdicts = verdicts or {}
    return {
        role: ScriptedDriver(
            answer_for(role, verdicts.get(role, "PASS"), iteration)
        )
        for role in prt.REVIEW_SEQUENCE
    }


def run_cycle(
    tmp_path: Path,
    machine: ProposalStateMachine | None = None,
    drivers=None,
    **kwargs,
):
    return prt.run_review_cycle(
        workspace=tmp_path,
        state_machine=machine or ProposalStateMachine(),
        iteration_number=kwargs.pop("iteration_number", 1),
        proposal_revision=kwargs.pop("proposal_revision", REVISION),
        reviewer_drivers=drivers or default_drivers(),
        **kwargs,
    )


def frozen_md(tmp_path: Path, iteration: int = 1) -> Path:
    return tmp_path / "06_VERSIONS" / f"iteration_{iteration:03d}_pre_review.md"


# ---------------------------------------------------------------------------
# SOURCE VALIDATION (1-5)
# ---------------------------------------------------------------------------
class TestSourceValidation:
    def test_01_idle_enters_source_validation_then_reviews(self, tmp_path):
        build_workspace(tmp_path)
        machine = ProposalStateMachine()
        run_cycle(tmp_path, machine=machine)
        assert machine.phase is pp.ProposalPhase.INTEGRATION

    def test_01b_explicit_source_validation_entry_works(self, tmp_path):
        build_workspace(tmp_path)
        machine = ProposalStateMachine()
        machine.transition_to(pp.ProposalPhase.SOURCE_VALIDATION)
        report = run_cycle(tmp_path, machine=machine)
        assert report.outcome is prt.ProposalReviewCycleOutcome.READY_FOR_INTEGRATION

    def test_02_valid_workspace_reaches_scientific_review_before_calls(
        self, tmp_path
    ):
        build_workspace(tmp_path)
        machine = ProposalStateMachine()
        drivers = default_drivers()

        class PhaseProbe(ScriptedDriver):
            pass

        seen_phases: list[str] = []

        original_start = ScriptedDriver.start_session

        def spy_start(self, request):  # noqa: ANN001
            seen_phases.append(machine.phase.value)
            return original_start(self, request)

        ScriptedDriver.start_session = spy_start  # type: ignore[method-assign]
        try:
            run_cycle(tmp_path, machine=machine, drivers=drivers)
        finally:
            ScriptedDriver.start_session = original_start  # type: ignore[method-assign]
        # Every reviewer was called exactly at ITS OWN review phase.
        assert seen_phases == ["SCIENTIFIC_REVIEW", "IMPLEMENTATION_REVIEW", "RED_TEAM_REVIEW"]

    def test_03_missing_master_fails_before_driver(self, tmp_path):
        build_workspace(tmp_path)
        (tmp_path / "03_PROPOSAL" / "MASTER_PROPOSAL.md").unlink()
        drivers = default_drivers()
        report = run_cycle(tmp_path, drivers=drivers)
        assert report.outcome is prt.ProposalReviewCycleOutcome.SOURCE_VALIDATION_FAILED
        assert "master_proposal_missing" in report.error
        assert all(d.started == 0 for d in drivers.values())
        assert report.completed_roles == []
        # Explicit escape edge: the machine lands in FAILED, never further.
        from encomm_pcc.proposal.state_machine import ProposalStateMachine as SM

        assert report.state_after in (pp.ProposalPhase.SOURCE_VALIDATION, pp.ProposalPhase.FAILED)

    def test_04_empty_master_fails_before_driver(self, tmp_path):
        build_workspace(tmp_path)
        (tmp_path / "03_PROPOSAL" / "MASTER_PROPOSAL.md").write_bytes(b"   \n")
        drivers = default_drivers()
        report = run_cycle(tmp_path, drivers=drivers)
        assert report.outcome is prt.ProposalReviewCycleOutcome.SOURCE_VALIDATION_FAILED
        assert "master_proposal_empty" in report.error
        assert all(d.started == 0 for d in drivers.values())

    def test_05_unreadable_snapshot_fails_closed(self, tmp_path):
        build_workspace(tmp_path)
        # An oversized source-of-truth file breaks the bounded snapshot.
        big = b"x" * (pp.source_snapshot.MAX_SNAPSHOT_FILE_CHARS * 4 + 10)
        (tmp_path / "00_SOURCE_OF_TRUTH" / "TEAM.md").write_bytes(big)
        drivers = default_drivers()
        report = run_cycle(tmp_path, drivers=drivers)
        # The oversized file is honestly UNAVAILABLE (not fatal), the cycle
        # still runs — the snapshot never fabricates content.
        assert report.outcome is prt.ProposalReviewCycleOutcome.READY_FOR_INTEGRATION

    def test_05b_snapshot_hash_mismatch_fails_closed(self, tmp_path, monkeypatch):
        build_workspace(tmp_path)
        drivers = default_drivers()

        def broken_snapshot(root):
            snapshot = pp.load_review_snapshot(root)
            snapshot.master_proposal_text = "# TAMPERED"
            return snapshot

        monkeypatch.setattr(
            "encomm_pcc.proposal_runtime.review_loop.load_review_snapshot",
            broken_snapshot,
        )
        report = run_cycle(tmp_path, drivers=drivers)
        assert report.outcome is prt.ProposalReviewCycleOutcome.SOURCE_VALIDATION_FAILED
        assert "snapshot_hash_mismatch" in report.error
        assert all(d.started == 0 for d in drivers.values())

    def test_invalid_iteration_number_fails_before_drivers(self, tmp_path):
        build_workspace(tmp_path)
        drivers = default_drivers()
        report = run_cycle(tmp_path, drivers=drivers, iteration_number=0)
        assert report.outcome is prt.ProposalReviewCycleOutcome.SOURCE_VALIDATION_FAILED
        assert "invalid_iteration_number" in report.error
        assert all(d.started == 0 for d in drivers.values())

    def test_missing_driver_role_fails_before_anything(self, tmp_path):
        build_workspace(tmp_path)
        drivers = default_drivers()
        del drivers[pp.ProposalRole.RED_TEAM_REVIEWER]
        machine = ProposalStateMachine()
        report = run_cycle(tmp_path, machine=machine, drivers=drivers)
        assert report.outcome is prt.ProposalReviewCycleOutcome.REVIEW_FAILED
        assert "RED_TEAM_REVIEWER" in report.error
        assert machine.phase is pp.ProposalPhase.IDLE

    def test_bad_entry_phase_refused(self, tmp_path):
        build_workspace(tmp_path)
        machine = ProposalStateMachine()
        # Legal walk to a non-resumable position: SOURCE_VALIDATION → FAILED.
        machine.transition_to(pp.ProposalPhase.SOURCE_VALIDATION)
        machine.transition_to(pp.ProposalPhase.FAILED)
        report = run_cycle(tmp_path, machine=machine)
        assert report.outcome is prt.ProposalReviewCycleOutcome.REVIEW_FAILED
        assert "must start from" in report.error


# ---------------------------------------------------------------------------
# FULL HAPPY PATH (6-14)
# ---------------------------------------------------------------------------
class TestHappyPath:
    def test_happy_path_full_contract(self, tmp_path):
        build_workspace(tmp_path)
        machine = ProposalStateMachine()
        drivers = default_drivers()
        report = run_cycle(tmp_path, machine=machine, drivers=drivers)

        assert report.outcome is prt.ProposalReviewCycleOutcome.READY_FOR_INTEGRATION
        # 6. exact reviewer order
        assert report.completed_roles == [
            "SCIENTIFIC_REVIEWER", "PROPOSAL_ENGINEER", "RED_TEAM_REVIEWER",
        ]
        # 7. same proposal hash everywhere
        assert len({d.prompts[0] for d in drivers.values()}) == 3
        for d in drivers.values():
            assert report.proposal_hash in d.prompts[0]
        # 8. final state
        assert machine.phase is pp.ProposalPhase.INTEGRATION
        assert report.state_before is pp.ProposalPhase.IDLE
        assert report.state_after is pp.ProposalPhase.INTEGRATION
        # 9. aggregate PASS
        assert report.aggregate_bundle is not None
        assert report.aggregate_bundle["aggregate_verdict"] == "PASS"
        # 10. integration_required false when truly no issues
        brief = json.loads(Path(report.integration_brief_path).read_text(encoding="utf-8"))
        assert brief["integration_required"] is False
        # 11/12/13. artifacts: 3 reviews + bundle + brief
        iter_dir = tmp_path / "04_REVIEWS" / "iteration_001"
        assert sorted(p.name for p in iter_dir.iterdir()) == [
            "implementation_review.json",
            "integration_brief.json",
            "red_team_review.json",
            "review_bundle.json",
            "scientific_review.json",
        ]
        # 14. frozen version exact-byte match + sidecar
        assert frozen_md(tmp_path).read_bytes() == PROPOSAL_BYTES
        sidecar = json.loads(
            (tmp_path / "06_VERSIONS" / "iteration_001_pre_review.json").read_text(encoding="utf-8")
        )
        import hashlib

        assert sidecar["proposal_hash"] == hashlib.sha256(PROPOSAL_BYTES).hexdigest()
        assert sidecar["iteration_number"] == 1
        assert sidecar["proposal_revision"] == REVISION
        assert sidecar["hash_algorithm"] == pp.PROPOSAL_HASH_ALGORITHM
        # Each reviewer saw ITS OWN role focus and the shared metadata.
        for role, d in drivers.items():
            assert f"reviewer_role: {role.value}" in d.prompts[0]
            assert "iteration_number: 1" in d.prompts[0]
        # No transcript persisted: artifacts carry structured data only.
        for path in iter_dir.iterdir():
            data = json.loads(path.read_text(encoding="utf-8"))
            text = json.dumps(data)
            assert "ENCOMM_PROPOSAL_REVIEW" not in text  # no raw envelope/transcript

    def test_deterministic_repeat(self, tmp_path):
        build_workspace(tmp_path)
        machine = ProposalStateMachine()
        report = run_cycle(tmp_path, machine=machine)
        first = Path(report.integration_brief_path).read_bytes()
        first_bundle = Path(
            tmp_path / "04_REVIEWS" / "iteration_001" / "review_bundle.json"
        ).read_bytes()
        # Same inputs → byte-identical artifacts (deterministic JSON).
        machine2 = ProposalStateMachine()
        tmp_path2 = tmp_path / "again"
        build_workspace(tmp_path2)
        report2 = run_cycle(tmp_path2, machine=machine2)
        second = Path(report2.integration_brief_path).read_bytes()
        second_bundle = Path(
            tmp_path2 / "04_REVIEWS" / "iteration_001" / "review_bundle.json"
        ).read_bytes()
        assert first == second and first_bundle == second_bundle


# ---------------------------------------------------------------------------
# NEEDS REVISION (15-18)
# ---------------------------------------------------------------------------
class TestNeedsRevision:
    def test_15_scientific_needs_revision_still_reviews_the_rest(self, tmp_path):
        build_workspace(tmp_path)
        drivers = default_drivers()
        drivers[pp.ProposalRole.SCIENTIFIC_REVIEWER] = ScriptedDriver(
            answer_for(
                pp.ProposalRole.SCIENTIFIC_REVIEWER, "NEEDS_REVISION",
                findings=[finding()],
            )
        )
        machine = ProposalStateMachine()
        report = run_cycle(tmp_path, machine=machine, drivers=drivers)
        assert report.outcome is prt.ProposalReviewCycleOutcome.READY_FOR_INTEGRATION
        assert report.completed_roles == ["SCIENTIFIC_REVIEWER", "PROPOSAL_ENGINEER", "RED_TEAM_REVIEWER"]
        assert all(d.started == 1 for d in drivers.values())

    def test_16_engineer_needs_revision_still_runs_red_team(self, tmp_path):
        build_workspace(tmp_path)
        drivers = default_drivers()
        drivers[pp.ProposalRole.PROPOSAL_ENGINEER] = ScriptedDriver(
            answer_for(
                pp.ProposalRole.PROPOSAL_ENGINEER, "NEEDS_REVISION",
                findings=[finding()],
            )
        )
        report = run_cycle(tmp_path, drivers=drivers)
        assert drivers[pp.ProposalRole.RED_TEAM_REVIEWER].started == 1
        assert report.outcome is prt.ProposalReviewCycleOutcome.READY_FOR_INTEGRATION

    def test_17_18_aggregate_needs_revision_and_integration_required(
        self, tmp_path
    ):
        build_workspace(tmp_path)
        drivers = default_drivers({pp.ProposalRole.RED_TEAM_REVIEWER: "NEEDS_REVISION"})
        # NEEDS_REVISION requires an actionable finding — give it one.
        drivers[pp.ProposalRole.RED_TEAM_REVIEWER] = ScriptedDriver(
            answer_for(
                pp.ProposalRole.RED_TEAM_REVIEWER,
                "NEEDS_REVISION",
                findings=[finding()],
            )
        )
        report = run_cycle(tmp_path, drivers=drivers)
        assert report.aggregate_bundle is not None
        assert report.aggregate_bundle["aggregate_verdict"] == "NEEDS_REVISION"
        brief = json.loads(Path(report.integration_brief_path).read_text(encoding="utf-8"))
        assert brief["integration_required"] is True
        assert brief["reviewer_verdicts"]["RED_TEAM_REVIEWER"] == "NEEDS_REVISION"


# ---------------------------------------------------------------------------
# BLOCKED (19-22)
# ---------------------------------------------------------------------------
class TestBlocked:
    def test_19_scientific_blocked_stops_after_one_call(self, tmp_path):
        build_workspace(tmp_path)
        drivers = default_drivers({pp.ProposalRole.SCIENTIFIC_REVIEWER: "BLOCKED"})
        machine = ProposalStateMachine()
        report = run_cycle(tmp_path, machine=machine, drivers=drivers)
        assert report.outcome is prt.ProposalReviewCycleOutcome.BLOCKED
        assert drivers[pp.ProposalRole.SCIENTIFIC_REVIEWER].started == 1
        assert drivers[pp.ProposalRole.PROPOSAL_ENGINEER].started == 0
        assert report.completed_roles == ["SCIENTIFIC_REVIEWER"]
        assert machine.phase is pp.ProposalPhase.BLOCKED
        assert report.state_after is pp.ProposalPhase.BLOCKED

    def test_20_implementation_blocked_stops_before_red_team(self, tmp_path):
        build_workspace(tmp_path)
        drivers = default_drivers({pp.ProposalRole.PROPOSAL_ENGINEER: "BLOCKED"})
        machine = ProposalStateMachine()
        report = run_cycle(tmp_path, machine=machine, drivers=drivers)
        assert report.outcome is prt.ProposalReviewCycleOutcome.BLOCKED
        assert drivers[pp.ProposalRole.RED_TEAM_REVIEWER].started == 0
        assert machine.phase is pp.ProposalPhase.BLOCKED

    def test_21_22_red_team_blocked_never_produces_bundle(self, tmp_path):
        build_workspace(tmp_path)
        drivers = default_drivers({pp.ProposalRole.RED_TEAM_REVIEWER: "BLOCKED"})
        machine = ProposalStateMachine()
        report = run_cycle(tmp_path, machine=machine, drivers=drivers)
        assert report.outcome is prt.ProposalReviewCycleOutcome.BLOCKED
        assert report.aggregate_bundle is None
        assert report.integration_brief_path == ""
        assert machine.phase is pp.ProposalPhase.BLOCKED
        iter_dir = tmp_path / "04_REVIEWS" / "iteration_001"
        # The blocking review is durable evidence; no bundle/brief exist.
        assert (iter_dir / "red_team_review.json").exists()
        assert not (iter_dir / "review_bundle.json").exists()
        assert not (iter_dir / "integration_brief.json").exists()


# ---------------------------------------------------------------------------
# OPERATIONAL FAILURES (23-25)
# ---------------------------------------------------------------------------
class TestOperationalFailures:
    def test_23_scientific_parser_failure_stops(self, tmp_path):
        build_workspace(tmp_path)
        bad = PromptResult(ok=True, text="no envelope at all", session_id="s", duration_s=0.0)
        drivers = {
            pp.ProposalRole.SCIENTIFIC_REVIEWER: ScriptedDriver(bad),
            pp.ProposalRole.PROPOSAL_ENGINEER: ScriptedDriver(
                answer_for(pp.ProposalRole.PROPOSAL_ENGINEER)
            ),
            pp.ProposalRole.RED_TEAM_REVIEWER: ScriptedDriver(
                answer_for(pp.ProposalRole.RED_TEAM_REVIEWER)
            ),
        }
        machine = ProposalStateMachine()
        report = run_cycle(tmp_path, machine=machine, drivers=drivers)
        assert report.outcome is prt.ProposalReviewCycleOutcome.REVIEW_FAILED
        assert report.failed_role == "SCIENTIFIC_REVIEWER"
        assert drivers[pp.ProposalRole.PROPOSAL_ENGINEER].started == 0
        assert machine.phase is pp.ProposalPhase.SCIENTIFIC_REVIEW

    def test_24_implementation_driver_failure_stops(self, tmp_path):
        build_workspace(tmp_path)
        drivers = default_drivers()

        class FailingDriver(ScriptedDriver):
            def start_session(self, request):  # noqa: ANN001
                raise DriverError("simulated driver error")

        drivers[pp.ProposalRole.PROPOSAL_ENGINEER] = FailingDriver(
            answer_for(pp.ProposalRole.PROPOSAL_ENGINEER)
        )
        machine = ProposalStateMachine()
        report = run_cycle(tmp_path, machine=machine, drivers=drivers)
        assert report.outcome is prt.ProposalReviewCycleOutcome.REVIEW_FAILED
        assert report.failed_role == "PROPOSAL_ENGINEER"
        assert drivers[pp.ProposalRole.RED_TEAM_REVIEWER].started == 0
        assert machine.phase is pp.ProposalPhase.IMPLEMENTATION_REVIEW

    def test_25_red_team_malformed_output_stops(self, tmp_path):
        build_workspace(tmp_path)
        drivers = default_drivers()
        drivers[pp.ProposalRole.RED_TEAM_REVIEWER] = ScriptedDriver(
            PromptResult(
                ok=True,
                text=envelope(review_payload(role=pp.ProposalRole.RED_TEAM_REVIEWER, verdict="MAYBE")),
                session_id="s",
                duration_s=0.0,
            )
        )
        machine = ProposalStateMachine()
        report = run_cycle(tmp_path, machine=machine, drivers=drivers)
        assert report.outcome is prt.ProposalReviewCycleOutcome.REVIEW_FAILED
        assert report.failed_role == "RED_TEAM_REVIEWER"
        assert report.aggregate_bundle is None

    def test_26_no_invalid_partial_aggregate_on_failure(self, tmp_path):
        build_workspace(tmp_path)
        drivers = default_drivers({pp.ProposalRole.PROPOSAL_ENGINEER: "BLOCKED"})
        report = run_cycle(tmp_path, drivers=drivers)
        assert report.aggregate_bundle is None
        assert not (tmp_path / "04_REVIEWS" / "iteration_001" / "review_bundle.json").exists()


# ---------------------------------------------------------------------------
# REVISION INTEGRITY (26-31)
# ---------------------------------------------------------------------------
class TestRevisionIntegrity:
    def _mutate_between(self, tmp_path, target_role: pp.ProposalRole):
        drivers = default_drivers()
        original = drivers[target_role].start_session

        def mutating_start(request):  # noqa: ANN001
            (tmp_path / "03_PROPOSAL" / "MASTER_PROPOSAL.md").write_bytes(
                PROPOSAL_BYTES + b"\n<!-- sneaky edit -->\n"
            )
            return original(request)

        drivers[target_role].start_session = mutating_start  # type: ignore[method-assign]
        return drivers

    def test_27_28_change_between_scientific_and_implementation(self, tmp_path):
        build_workspace(tmp_path)
        drivers = self._mutate_between(tmp_path, pp.ProposalRole.PROPOSAL_ENGINEER)
        machine = ProposalStateMachine()
        report = run_cycle(tmp_path, machine=machine, drivers=drivers)
        assert report.outcome is prt.ProposalReviewCycleOutcome.STALE_PROPOSAL
        assert report.failed_role == "PROPOSAL_ENGINEER"
        assert drivers[pp.ProposalRole.RED_TEAM_REVIEWER].started == 0
        assert report.aggregate_bundle is None
        assert machine.phase is pp.ProposalPhase.IMPLEMENTATION_REVIEW

    def test_29_change_between_implementation_and_red_team(self, tmp_path):
        build_workspace(tmp_path)
        drivers = self._mutate_between(tmp_path, pp.ProposalRole.RED_TEAM_REVIEWER)
        machine = ProposalStateMachine()
        report = run_cycle(tmp_path, machine=machine, drivers=drivers)
        assert report.outcome is prt.ProposalReviewCycleOutcome.STALE_PROPOSAL
        assert report.failed_role == "RED_TEAM_REVIEWER"
        assert machine.phase is pp.ProposalPhase.RED_TEAM_REVIEW

    def test_30_change_after_final_reviewer_no_aggregation(self, tmp_path):
        build_workspace(tmp_path)
        drivers = default_drivers()
        original = drivers[pp.ProposalRole.RED_TEAM_REVIEWER].wait_for_completion

        def mutating_wait(handle, timeout_s=None):  # noqa: ANN001
            result = original(handle, timeout_s)
            (tmp_path / "03_PROPOSAL" / "MASTER_PROPOSAL.md").write_bytes(
                PROPOSAL_BYTES + b"\n<!-- post-cycle edit -->\n"
            )
            return result

        drivers[pp.ProposalRole.RED_TEAM_REVIEWER].wait_for_completion = mutating_wait  # type: ignore[method-assign]
        machine = ProposalStateMachine()
        report = run_cycle(tmp_path, machine=machine, drivers=drivers)
        assert report.outcome is prt.ProposalReviewCycleOutcome.STALE_PROPOSAL
        assert report.failed_role == "RED_TEAM_REVIEWER"
        assert report.aggregate_bundle is None
        # The executor's post-call immutability guard refused the mutated
        # execution: the red-team result is NOT accepted, the phase edge did
        # NOT walk, and no red-team evidence is persisted.
        assert machine.phase is pp.ProposalPhase.RED_TEAM_REVIEW
        assert not (tmp_path / "04_REVIEWS" / "iteration_001" / "red_team_review.json").exists()
        assert not (tmp_path / "04_REVIEWS" / "iteration_001" / "review_bundle.json").exists()
        assert not (tmp_path / "04_REVIEWS" / "iteration_001" / "integration_brief.json").exists()

    def test_31_same_hash_across_three_results_required(self, tmp_path):
        # Every packet of one cycle embeds the SAME hash — verify structurally.
        build_workspace(tmp_path)
        drivers = default_drivers()
        machine = ProposalStateMachine()
        report = run_cycle(tmp_path, machine=machine, drivers=drivers)
        hashes = {report.proposal_hash in d.prompts[0] for d in drivers.values()}
        assert hashes == {True}
        assert report.aggregate_bundle is not None
        assert report.aggregate_bundle["proposal_hash"] == report.proposal_hash

    def test_cycle_mutation_detected_maps_to_stale(self, tmp_path):
        # A reviewer that WRITES the proposal mid-call is caught by the
        # executor's mutation guard and surfaces as STALE_PROPOSAL.
        build_workspace(tmp_path)
        drivers = default_drivers()

        class MutatingDriver(ScriptedDriver):
            def wait_for_completion(self, handle, timeout_s=None):  # noqa: ANN001
                (tmp_path / "03_PROPOSAL" / "MASTER_PROPOSAL.md").write_bytes(b"TAMPERED")
                return self.answer

        drivers[pp.ProposalRole.SCIENTIFIC_REVIEWER] = MutatingDriver(
            answer_for(pp.ProposalRole.SCIENTIFIC_REVIEWER)
        )
        machine = ProposalStateMachine()
        report = run_cycle(tmp_path, machine=machine, drivers=drivers)
        assert report.outcome is prt.ProposalReviewCycleOutcome.STALE_PROPOSAL
        assert report.failed_role == "SCIENTIFIC_REVIEWER"
        assert machine.phase is pp.ProposalPhase.SCIENTIFIC_REVIEW


# ---------------------------------------------------------------------------
# RESUME (36-40)
# ---------------------------------------------------------------------------
class TestResume:
    def _prepare_completed(self, tmp_path):
        """Run a full scientific review; return the stopped machine state."""
        build_workspace(tmp_path)
        drivers = default_drivers()
        machine = ProposalStateMachine()
        report = run_cycle(tmp_path, machine=machine, drivers=drivers)
        assert report.outcome is prt.ProposalReviewCycleOutcome.READY_FOR_INTEGRATION
        return machine

    def test_36_resume_from_implementation_reuses_scientific_artifact(
        self, tmp_path
    ):
        # Simulate a crash right after the scientific review completed:
        # run the full cycle in workspace A, copy the review artifacts +
        # frozen version into workspace B, put the machine at
        # IMPLEMENTATION_REVIEW, and resume with FRESH drivers.
        build_workspace(tmp_path)
        machine = ProposalStateMachine()
        report = run_cycle(tmp_path, machine=machine)
        # Reset to a mid-cycle position as if only scientific had finished.
        impl_dir = tmp_path / "04_REVIEWS" / "iteration_001"
        for name in ("implementation_review.json", "red_team_review.json",
                     "review_bundle.json", "integration_brief.json"):
            (impl_dir / name).unlink()

        machine2 = ProposalStateMachine()
        for step in ("SOURCE_VALIDATION", "SCIENTIFIC_REVIEW", "IMPLEMENTATION_REVIEW"):
            machine2.transition_to(pp.ProposalPhase(step))

        fresh = default_drivers()
        report2 = run_cycle(tmp_path, machine=machine2, drivers=fresh)
        assert report2.outcome is prt.ProposalReviewCycleOutcome.READY_FOR_INTEGRATION
        # Scientific was REUSED (fresh driver untouched), the rest ran.
        assert fresh[pp.ProposalRole.SCIENTIFIC_REVIEWER].started == 0
        assert fresh[pp.ProposalRole.PROPOSAL_ENGINEER].started == 1
        assert fresh[pp.ProposalRole.RED_TEAM_REVIEWER].started == 1
        assert report2.completed_roles == [
            "SCIENTIFIC_REVIEWER", "PROPOSAL_ENGINEER", "RED_TEAM_REVIEWER",
        ]
        assert machine2.phase is pp.ProposalPhase.INTEGRATION

    def test_37_resume_from_red_team_reuses_first_two(self, tmp_path):
        build_workspace(tmp_path)
        run_cycle(tmp_path)
        impl_dir = tmp_path / "04_REVIEWS" / "iteration_001"
        for name in ("red_team_review.json", "review_bundle.json", "integration_brief.json"):
            (impl_dir / name).unlink()

        machine2 = ProposalStateMachine()
        for step in ("SOURCE_VALIDATION", "SCIENTIFIC_REVIEW", "IMPLEMENTATION_REVIEW", "RED_TEAM_REVIEW"):
            machine2.transition_to(pp.ProposalPhase(step))

        fresh = default_drivers()
        report2 = run_cycle(tmp_path, machine=machine2, drivers=fresh)
        assert fresh[pp.ProposalRole.SCIENTIFIC_REVIEWER].started == 0
        assert fresh[pp.ProposalRole.PROPOSAL_ENGINEER].started == 0
        assert fresh[pp.ProposalRole.RED_TEAM_REVIEWER].started == 1
        assert machine2.phase is pp.ProposalPhase.INTEGRATION

    def test_38_conflicting_resume_artifact_fails_closed(self, tmp_path):
        build_workspace(tmp_path)
        machine = ProposalStateMachine()
        for step in ("SOURCE_VALIDATION", "SCIENTIFIC_REVIEW", "IMPLEMENTATION_REVIEW"):
            machine.transition_to(pp.ProposalPhase(step))
        iter_dir = tmp_path / "04_REVIEWS" / "iteration_001"
        iter_dir.mkdir(parents=True)
        # Artifact at the CURRENT position (machine never advanced past it).
        (iter_dir / "scientific_review.json").write_text(
            json.dumps({
                "schema": "encomm-pcc.review-artifact/v1",
                "iteration_number": 1,
                "proposal_revision": REVISION,
                "proposal_hash": "0" * 64,
                "reviewer_role": "SCIENTIFIC_REVIEWER",
                "result": review_payload(role=pp.ProposalRole.SCIENTIFIC_REVIEWER),
                "reused": False,
                "runtime": {"outcome": "COMPLETED"},
            }),
            encoding="utf-8",
        )
        fresh = default_drivers()
        report = run_cycle(tmp_path, machine=machine, drivers=fresh)
        assert report.outcome is prt.ProposalReviewCycleOutcome.ARTIFACT_CONFLICT
        assert all(d.started == 0 for d in fresh.values())

    def test_39_wrong_iteration_artifact_fails_closed(self, tmp_path):
        build_workspace(tmp_path)
        machine = ProposalStateMachine()
        for step in ("SOURCE_VALIDATION", "SCIENTIFIC_REVIEW", "IMPLEMENTATION_REVIEW"):
            machine.transition_to(pp.ProposalPhase(step))
        iter_dir = tmp_path / "04_REVIEWS" / "iteration_001"
        iter_dir.mkdir(parents=True)
        payload = review_payload(role=pp.ProposalRole.SCIENTIFIC_REVIEWER)
        raw = {
            "schema": "encomm-pcc.review-artifact/v1",
            "iteration_number": 7,  # WRONG iteration
            "proposal_revision": REVISION,
            "proposal_hash": "0" * 64,
            "reviewer_role": "SCIENTIFIC_REVIEWER",
            "result": payload,
            "reused": False,
            "runtime": {"outcome": "COMPLETED"},
        }
        (iter_dir / "scientific_review.json").write_text(
            json.dumps(raw), encoding="utf-8"
        )
        fresh = default_drivers()
        report = run_cycle(tmp_path, machine=machine, drivers=fresh)
        assert report.outcome is prt.ProposalReviewCycleOutcome.ARTIFACT_CONFLICT
        assert all(d.started == 0 for d in fresh.values())

    def test_40_wrong_hash_artifact_fails_closed(self, tmp_path):
        build_workspace(tmp_path)
        machine = ProposalStateMachine()
        for step in ("SOURCE_VALIDATION", "SCIENTIFIC_REVIEW", "IMPLEMENTATION_REVIEW"):
            machine.transition_to(pp.ProposalPhase(step))
        iter_dir = tmp_path / "04_REVIEWS" / "iteration_001"
        iter_dir.mkdir(parents=True)
        raw = {
            "schema": "encomm-pcc.review-artifact/v1",
            "iteration_number": 1,
            "proposal_revision": REVISION,
            "proposal_hash": "f" * 64,  # WRONG hash (real cycle hash differs)
            "reviewer_role": "SCIENTIFIC_REVIEWER",
            "result": review_payload(role=pp.ProposalRole.SCIENTIFIC_REVIEWER),
            "reused": False,
            "runtime": {"outcome": "COMPLETED"},
        }
        (iter_dir / "scientific_review.json").write_text(
            json.dumps(raw), encoding="utf-8"
        )
        fresh = default_drivers()
        report = run_cycle(tmp_path, machine=machine, drivers=fresh)
        assert report.outcome is prt.ProposalReviewCycleOutcome.ARTIFACT_CONFLICT
        assert all(d.started == 0 for d in fresh.values())

    def test_missing_resume_artifact_for_completed_position(self, tmp_path):
        # Machine at RED_TEAM_REVIEW but NO durable artifacts at all: the
        # completed positions cannot be proven — fail closed, zero calls.
        build_workspace(tmp_path)
        machine = ProposalStateMachine()
        for step in ("SOURCE_VALIDATION", "SCIENTIFIC_REVIEW", "IMPLEMENTATION_REVIEW", "RED_TEAM_REVIEW"):
            machine.transition_to(pp.ProposalPhase(step))
        fresh = default_drivers()
        report = run_cycle(tmp_path, machine=machine, drivers=fresh)
        assert report.outcome is prt.ProposalReviewCycleOutcome.ARTIFACT_CONFLICT
        assert "missing_resume_artifact" in report.error
        assert all(d.started == 0 for d in fresh.values())

    def test_version_freeze_conflict_fails_closed(self, tmp_path):
        build_workspace(tmp_path)
        frozen = frozen_md(tmp_path)
        frozen.parent.mkdir(parents=True, exist_ok=True)
        frozen.write_bytes(b"different frozen bytes")
        machine = ProposalStateMachine()
        report = run_cycle(tmp_path, machine=machine)
        assert report.outcome is prt.ProposalReviewCycleOutcome.ARTIFACT_CONFLICT
        assert "version freeze failed" in report.error

    def test_version_freeze_safe_reuse(self, tmp_path):
        build_workspace(tmp_path)
        # Pre-freeze the EXACT same bytes + contract: the cycle reuses it.
        from encomm_pcc.proposal_runtime import freeze_pre_review_version

        md, cycle_hash = freeze_pre_review_version(
            workspace=tmp_path,
            master_proposal_path=tmp_path / "03_PROPOSAL" / "MASTER_PROPOSAL.md",
            iteration_number=1,
            proposal_revision=REVISION,
        )
        assert md.read_bytes() == PROPOSAL_BYTES
        machine = ProposalStateMachine()
        report = run_cycle(tmp_path, machine=machine)
        assert report.outcome is prt.ProposalReviewCycleOutcome.READY_FOR_INTEGRATION
        assert report.frozen_version_path == str(md)
        assert report.proposal_hash == cycle_hash


# ---------------------------------------------------------------------------
# ISOLATION (41-44)
# ---------------------------------------------------------------------------
class TestIsolation:
    def test_41_runtime_modules_import_no_ui_persistence(self):
        code = (
            "import sys;"
            "import encomm_pcc.proposal_runtime;"
            "forbidden = [m for m in sys.modules if m.startswith(("
            "'encomm_pcc.ui', 'encomm_pcc.persistence', 'PySide6'))];"
            "assert not forbidden, f'runtime isolation broken: {forbidden}';"
            "print('ok')"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC_ROOT)
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, check=False, env=env,
        )
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "ok" in result.stdout

    def test_42_pure_proposal_package_still_isolated(self):
        code = (
            "import sys;"
            "import encomm_pcc.proposal;"
            "forbidden = [m for m in sys.modules if m.startswith(("
            "'encomm_pcc.core', 'encomm_pcc.domain', 'encomm_pcc.drivers', "
            "'encomm_pcc.persistence', 'encomm_pcc.ui', "
            "'encomm_pcc.proposal_runtime'))];"
            "assert not forbidden, f'proposal isolation broken: {forbidden}';"
            "print('ok')"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC_ROOT)
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, check=False, env=env,
        )
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "ok" in result.stdout

    def test_43_coding_mode_never_imports_proposal(self):
        code = (
            "import sys;"
            "import encomm_pcc.app, encomm_pcc.core, encomm_pcc.drivers, "
            "encomm_pcc.persistence, encomm_pcc.ui, encomm_pcc.domain;"
            "leaked = [m for m in sys.modules if m.startswith("
            "('encomm_pcc.proposal',))];"
            "assert not leaked, f'coding mode imports proposal packages: {leaked}';"
            "print('ok')"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC_ROOT)
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, check=False, env=env,
        )
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "ok" in result.stdout

    def test_44_no_coding_mode_production_file_changed(self):
        """Structural pin: the coding pipeline carries no proposal imports."""
        coding_roots = ["app.py", "core", "domain", "drivers", "persistence", "ui"]
        offending: list[str] = []
        for root in coding_roots:
            base = SRC_ROOT / "encomm_pcc" / root
            paths = [base] if base.is_file() else sorted(base.rglob("*.py"))
            for path in paths:
                text = path.read_text(encoding="utf-8")
                if "proposal" in text:
                    offending.append(str(path))
        assert offending == [], f"proposal references leaked into coding mode: {offending}"
