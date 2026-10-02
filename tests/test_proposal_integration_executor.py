"""Session 015 — ORCHESTRATOR integration runtime + composition tests.

Covers the mandated RUNTIME GUARDS / WRITE / ZERO-AI / REVISION FRESHNESS /
NO-CHANGE / COMPOSITION matrix sections (brief §18, items 13–50):

* RUNTIME GUARDS: wrong state, missing brief, brief iteration mismatch,
  brief/master hash mismatch — all fail BEFORE any driver call; driver and
  parser failures never touch MASTER_PROPOSAL; external mutation during the
  driver call is detected and never overwritten.
* WRITE: a valid integration atomically replaces MASTER_PROPOSAL (old/new
  hashes captured), the post-integration version is byte-exact, the
  integration_result artifact is written with no raw transcript; conflicting
  evidence fails closed.
* ZERO-AI: a clean-PASS ``integration_required=false`` brief makes ZERO
  driver calls, leaves the master byte-identical, records a no-op artifact
  and reaches HARD_GATE_VALIDATION.
* REVISION FRESHNESS: a changed proposal cannot COMPLETE — the composed
  iteration walks HARD_GATE_VALIDATION → REVISION_REQUIRED, writes
  NEXT_ITERATION.json with the correct next iteration number / revised hash /
  unresolved items, and the next cycle reviews the NEW hash.
* NO-CHANGE: an identical revised text keeps output hash == input hash,
  reports no change, reaches HARD_GATE_VALIDATION with freshness current.
* COMPOSITION: one iteration = review cycle + integration; clean PASS runs
  the zero-AI path; reviewer BLOCKED never calls the orchestrator.

All tests are OFFLINE and deterministic: scripted in-repo driver doubles,
zero network, zero model calls, real files in ``tmp_path`` only.
"""

from __future__ import annotations

import copy
import hashlib
import json
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

REVISION = "rev-1"
PROPOSAL_V1 = b"# MASTER PROPOSAL\n\nv1 content.\n"
HASH1 = hashlib.sha256(PROPOSAL_V1).hexdigest()
REVISED_TEXT = "# MASTER PROPOSAL\n\nv2 content — revised by the orchestrator.\n"
HASH2 = hashlib.sha256(REVISED_TEXT.encode("utf-8")).hexdigest()

FINDING = {
    "severity": "medium",
    "category": "weak_wording",
    "section": "1",
    "message": "Fix wording.",
    "evidence": "",
    "source_refs": [],
    "suggested_change": "Reword.",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def build_workspace(tmp_path: Path, proposal_bytes: bytes = PROPOSAL_V1) -> Path:
    ws = pp.ProposalWorkspace(tmp_path)
    ws.initialize()
    ws.master_proposal_path().write_bytes(proposal_bytes)
    return tmp_path


def review_payload(
    *,
    role: pp.ProposalRole,
    verdict: str = "PASS",
    iteration: int = 1,
    findings: list | None = None,
) -> dict:
    return {
        "reviewer_role": role.value,
        "verdict": verdict,
        "summary": "Needs work." if verdict == "NEEDS_REVISION" else "Clean.",
        "findings": findings or [],
        "proposed_patches": [],
        "unverified_claims": [],
        "iteration_number": iteration,
    }


def envelope(text: str, start: str, end: str) -> str:
    return f"{start}\n{text}\n{end}"


class ScriptedReviewer:
    """Scripted reviewer driver — zero engine involvement."""

    def __init__(
        self,
        role: pp.ProposalRole,
        verdict: str = "PASS",
        iteration: int = 1,
        findings: list | None = None,
    ) -> None:
        self.role = role
        self.verdict = verdict
        self.iteration = iteration
        self.findings = findings or []
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
        payload = review_payload(
            role=self.role,
            verdict=self.verdict,
            iteration=self.iteration,
            findings=self.findings,
        )
        return PromptResult(
            ok=True,
            text=envelope(
                json.dumps(payload),
                pp.PROPOSAL_REVIEW_ENVELOPE_START,
                pp.PROPOSAL_REVIEW_ENVELOPE_END,
            ),
            session_id="ext-reviewer-1",
            duration_s=0.001,
        )


def default_reviewers(
    iteration: int = 1,
    verdicts: dict[pp.ProposalRole, str] | None = None,
    findings_for: dict[pp.ProposalRole, list] | None = None,
) -> dict[pp.ProposalRole, ScriptedReviewer]:
    verdicts = verdicts or {}
    findings_for = findings_for or {}
    return {
        role: ScriptedReviewer(
            role,
            verdict=verdicts.get(role, "PASS"),
            iteration=iteration,
            findings=findings_for.get(role, []),
        )
        for role in prt.REVIEW_SEQUENCE
    }


def integration_payload(
    *,
    iteration: int = 1,
    input_hash: str = HASH1,
    revised: str = REVISED_TEXT,
    summary: str = "applied the finding",
    applied: list | None = None,
    unresolved: list | None = None,
) -> dict:
    return {
        "role": "ORCHESTRATOR",
        "iteration_number": iteration,
        "input_proposal_hash": input_hash,
        "summary": summary,
        "applied_items": applied if applied is not None else [
            {"item_id": "finding:1", "action": "applied", "reason": "valid"}
        ],
        "rejected_items": [],
        "unresolved_items": unresolved if unresolved is not None else [],
        "revised_proposal": revised,
    }


class ScriptedOrchestrator:
    """Scripted ORCHESTRATOR integration driver — zero engine involvement."""

    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload
        self.driver_id = "scripted-orchestrator"
        self.started = 0
        self.prompts: list[str] = []
        # Optional pre-write hook (mutation simulation): called right before
        # the answer is produced.
        self.on_wait = None

    def start_session(self, request):
        self.started += 1
        return DriverSession(
            driver_id=self.driver_id,
            role=request.role,
            session_id="ext-orchestrator-1",
            external=True,
        )

    def resume_session(self, session_id, request):
        raise NotImplementedError

    def send_prompt(self, session, prompt):
        self.prompts.append(prompt)
        return PromptHandle(session=session, prompt=prompt)

    def wait_for_completion(self, handle, timeout_s=None):
        if self.on_wait is not None:
            self.on_wait()
        payload = self.payload if self.payload is not None else integration_payload()
        return PromptResult(
            ok=True,
            text=envelope(
                json.dumps(payload),
                pp.PROPOSAL_INTEGRATION_ENVELOPE_START,
                pp.PROPOSAL_INTEGRATION_ENVELOPE_END,
            ),
            session_id="ext-orchestrator-1",
            duration_s=0.001,
        )


def run_cycle_to_integration(
    tmp_path: Path,
    machine: ProposalStateMachine,
    reviewers: dict[pp.ProposalRole, ScriptedReviewer],
):
    """Drive a full review cycle that lands the machine at INTEGRATION."""
    return prt.run_review_cycle(
        workspace=tmp_path,
        state_machine=machine,
        iteration_number=1,
        proposal_revision=REVISION,
        reviewer_drivers=reviewers,
    )


def brief_path(tmp_path: Path, iteration: int = 1) -> Path:
    return tmp_path / "04_REVIEWS" / f"iteration_{iteration:03d}" / "integration_brief.json"


def run_int(tmp_path, machine, driver, iteration=1, **kwargs):
    return prt.run_integration(
        workspace=tmp_path,
        state_machine=machine,
        iteration_number=iteration,
        proposal_revision=REVISION,
        orchestrator_driver=driver,
        **kwargs,
    )


def walk_to_integration(machine: ProposalStateMachine) -> None:
    machine.transition_to(pp.ProposalPhase.SOURCE_VALIDATION)
    machine.transition_to(pp.ProposalPhase.SCIENTIFIC_REVIEW)
    machine.transition_to(pp.ProposalPhase.IMPLEMENTATION_REVIEW)
    machine.transition_to(pp.ProposalPhase.RED_TEAM_REVIEW)
    machine.transition_to(pp.ProposalPhase.INTEGRATION)


# ---------------------------------------------------------------------------
# RUNTIME GUARDS (13–20)
# ---------------------------------------------------------------------------
class TestRuntimeGuards:
    def _needs_revision_to_integration(self, tmp_path):
        """A review cycle whose brief REQUIRES integration (actionable item).

        The clean-PASS zero-AI bypass never reaches the driver, so every
        driver-path guard test needs a NEEDS_REVISION cycle's brief.
        """
        build_workspace(tmp_path)
        reviewers = default_reviewers(
            verdicts={pp.ProposalRole.SCIENTIFIC_REVIEWER: "NEEDS_REVISION"},
            findings_for={pp.ProposalRole.SCIENTIFIC_REVIEWER: [dict(FINDING)]},
        )
        machine = ProposalStateMachine()
        run_cycle_to_integration(tmp_path, machine, reviewers)
        assert machine.phase is pp.ProposalPhase.INTEGRATION
        brief = json.loads(brief_path(tmp_path).read_text())
        assert brief["integration_required"] is True
        return machine

    def test_13_wrong_state_rejected_before_driver(self, tmp_path):
        build_workspace(tmp_path)
        machine = ProposalStateMachine()  # IDLE — not INTEGRATION
        driver = ScriptedOrchestrator()
        report = run_int(tmp_path, machine, driver)
        assert report.outcome is prt.ProposalIntegrationOutcome.DRIVER_FAILED
        assert driver.started == 0
        assert machine.phase is pp.ProposalPhase.IDLE

    def test_14_missing_integration_brief_fails_before_driver(self, tmp_path):
        build_workspace(tmp_path)
        machine = ProposalStateMachine()
        walk_to_integration(machine)
        driver = ScriptedOrchestrator()
        report = run_int(tmp_path, machine, driver)
        assert report.outcome is prt.ProposalIntegrationOutcome.ARTIFACT_CONFLICT
        assert driver.started == 0
        assert machine.phase is pp.ProposalPhase.INTEGRATION

    def test_15_brief_iteration_mismatch_fails_before_driver(self, tmp_path):
        build_workspace(tmp_path)
        reviewers = default_reviewers()
        machine = ProposalStateMachine()
        run_cycle_to_integration(tmp_path, machine, reviewers)
        assert machine.phase is pp.ProposalPhase.INTEGRATION
        driver = ScriptedOrchestrator()
        report = run_int(tmp_path, machine, driver, iteration=2)
        # The iteration_002 brief does not exist: a missing authoritative
        # brief is an evidence conflict detected BEFORE any driver contact.
        assert report.outcome is prt.ProposalIntegrationOutcome.ARTIFACT_CONFLICT
        assert driver.started == 0
        assert machine.phase is pp.ProposalPhase.INTEGRATION

    def test_16_brief_master_hash_mismatch_fails_before_driver(self, tmp_path):
        build_workspace(tmp_path)
        reviewers = default_reviewers()
        machine = ProposalStateMachine()
        run_cycle_to_integration(tmp_path, machine, reviewers)
        # The master changed AFTER the cycle froze its revision.
        ws = pp.ProposalWorkspace(tmp_path)
        ws.master_proposal_path().write_bytes(b"# changed under the cycle\n")
        driver = ScriptedOrchestrator()
        report = run_int(tmp_path, machine, driver)
        assert report.outcome is prt.ProposalIntegrationOutcome.STALE_INPUT
        assert driver.started == 0
        assert machine.phase is pp.ProposalPhase.INTEGRATION
        assert ws.master_proposal_path().read_bytes() == b"# changed under the cycle\n"

    def test_17_driver_failure_does_not_edit_master(self, tmp_path):
        machine = self._needs_revision_to_integration(tmp_path)

        class FailingDriver:
            driver_id = "failing-orchestrator"

            def start_session(self, request):
                raise DriverError("orchestrator exploded")

        report = run_int(tmp_path, machine, FailingDriver())
        assert report.outcome is prt.ProposalIntegrationOutcome.DRIVER_FAILED
        assert machine.phase is pp.ProposalPhase.INTEGRATION
        assert pp.ProposalWorkspace(tmp_path).master_proposal_path().read_bytes() == PROPOSAL_V1
        assert not (tmp_path / "06_VERSIONS" / "iteration_001_post_integration.md").exists()

    def test_18_parser_failure_does_not_edit_master(self, tmp_path):
        machine = self._needs_revision_to_integration(tmp_path)
        # A payload with the WRONG input hash passes the driver but must be
        # refused by the strict parser.
        driver = ScriptedOrchestrator(
            payload=integration_payload(input_hash="c" * 64)
        )
        report = run_int(tmp_path, machine, driver)
        assert report.outcome is prt.ProposalIntegrationOutcome.PARSE_FAILED
        assert report.parse_reason == "input_hash_mismatch"
        assert machine.phase is pp.ProposalPhase.INTEGRATION
        assert pp.ProposalWorkspace(tmp_path).master_proposal_path().read_bytes() == PROPOSAL_V1

    def test_19_external_mutation_during_driver_call_detected(self, tmp_path):
        machine = self._needs_revision_to_integration(tmp_path)
        ws = pp.ProposalWorkspace(tmp_path)

        def mutate_mid_call():
            ws.master_proposal_path().write_bytes(b"# externally mutated\n")

        driver = ScriptedOrchestrator()
        driver.on_wait = mutate_mid_call
        report = run_int(tmp_path, machine, driver)
        assert report.outcome is prt.ProposalIntegrationOutcome.MUTATION_DETECTED
        assert machine.phase is pp.ProposalPhase.INTEGRATION

    def test_20_external_mutation_is_never_overwritten(self, tmp_path):
        machine = self._needs_revision_to_integration(tmp_path)
        ws = pp.ProposalWorkspace(tmp_path)

        def mutate_mid_call():
            ws.master_proposal_path().write_bytes(b"# externally mutated\n")

        driver = ScriptedOrchestrator()
        driver.on_wait = mutate_mid_call
        report = run_int(tmp_path, machine, driver)
        assert ws.master_proposal_path().read_bytes() == b"# externally mutated\n"
        assert report.output_proposal_hash != HASH2  # nothing was written
        assert not (tmp_path / "06_VERSIONS" / "iteration_001_post_integration.md").exists()
        assert not brief_path(tmp_path).with_name("integration_result.json").exists()


# ---------------------------------------------------------------------------
# WRITE (21–28)
# ---------------------------------------------------------------------------
class TestMasterWrite:
    def _happy(self, tmp_path):
        build_workspace(tmp_path)
        reviewers = default_reviewers(
            verdicts={
                pp.ProposalRole.SCIENTIFIC_REVIEWER: "NEEDS_REVISION",
            },
            findings_for={pp.ProposalRole.SCIENTIFIC_REVIEWER: [dict(FINDING)]},
        )
        machine = ProposalStateMachine()
        report = prt.run_iteration(
            workspace=tmp_path,
            state_machine=machine,
            iteration_number=1,
            proposal_revision=REVISION,
            reviewer_drivers=reviewers,
            orchestrator_driver=ScriptedOrchestrator(),
        )
        return machine, report

    def test_21_valid_integration_atomically_replaces_master(self, tmp_path):
        machine, report = self._happy(tmp_path)
        assert report.outcome is prt.ProposalIterationOutcome.READY_FOR_NEXT_ITERATION
        ws = pp.ProposalWorkspace(tmp_path)
        assert ws.master_proposal_path().read_bytes() == REVISED_TEXT.encode("utf-8")

    def test_22_23_old_and_new_hashes_captured(self, tmp_path):
        machine, report = self._happy(tmp_path)
        integration = report.integration_report
        assert integration["input_proposal_hash"] == HASH1
        assert integration["output_proposal_hash"] == HASH2
        assert integration["changed"] is True

    def test_24_post_integration_version_byte_exact(self, tmp_path):
        machine, report = self._happy(tmp_path)
        post_md = tmp_path / "06_VERSIONS" / "iteration_001_post_integration.md"
        assert post_md.read_bytes() == REVISED_TEXT.encode("utf-8")
        assert (
            post_md.read_bytes()
            == pp.ProposalWorkspace(tmp_path).master_proposal_path().read_bytes()
        )
        sidecar = json.loads(
            (tmp_path / "06_VERSIONS" / "iteration_001_post_integration.json").read_text()
        )
        assert sidecar["proposal_hash"] == HASH2
        assert sidecar["previous_proposal_hash"] == HASH1
        assert sidecar["hash_algorithm"] == pp.PROPOSAL_HASH_ALGORITHM
        assert sidecar["iteration_number"] == 1

    def test_25_integration_result_written(self, tmp_path):
        machine, report = self._happy(tmp_path)
        artifact = brief_path(tmp_path).with_name("integration_result.json")
        data = json.loads(artifact.read_text())
        assert data["schema"] == prt.INTEGRATION_RESULT_SCHEMA
        assert data["input_proposal_hash"] == HASH1
        assert data["output_proposal_hash"] == HASH2
        assert data["integration_result"]["applied_items"][0]["item_id"] == "finding:1"
        assert data["runtime"]["outcome"] == "COMPLETED_CHANGED"

    def test_26_no_raw_transcript_persisted(self, tmp_path):
        machine, report = self._happy(tmp_path)
        artifact_text = brief_path(tmp_path).with_name("integration_result.json").read_text()
        payload_text = json.dumps(integration_payload())
        assert payload_text not in artifact_text
        assert pp.PROPOSAL_INTEGRATION_ENVELOPE_START not in artifact_text
        assert "revised_proposal" not in json.loads(artifact_text)["integration_result"]
        # No *step output / transcript files anywhere in the workspace.
        for path in tmp_path.rglob("*"):
            if path.is_file():
                assert "ENCOMM_PROPOSAL_INTEGRATION_START" not in path.read_text(
                    encoding="utf-8", errors="ignore"
                )

    def test_27_conflicting_integration_result_rejected(self, tmp_path):
        build_workspace(tmp_path)
        reviewers = default_reviewers()
        machine = ProposalStateMachine()
        run_cycle_to_integration(tmp_path, machine, reviewers)
        target = brief_path(tmp_path).with_name("integration_result.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"schema": "something-else"}\n', encoding="utf-8")
        driver = ScriptedOrchestrator()
        report = run_int(tmp_path, machine, driver)
        assert report.outcome is prt.ProposalIntegrationOutcome.ARTIFACT_CONFLICT
        assert target.read_text() == '{"schema": "something-else"}\n'
        assert machine.phase is pp.ProposalPhase.INTEGRATION
        assert pp.ProposalWorkspace(tmp_path).master_proposal_path().read_bytes() == PROPOSAL_V1

    def test_28_conflicting_post_integration_version_rejected(self, tmp_path):
        build_workspace(tmp_path)
        reviewers = default_reviewers(
            verdicts={pp.ProposalRole.SCIENTIFIC_REVIEWER: "NEEDS_REVISION"},
            findings_for={pp.ProposalRole.SCIENTIFIC_REVIEWER: [dict(FINDING)]},
        )
        machine = ProposalStateMachine()
        run_cycle_to_integration(tmp_path, machine, reviewers)
        post_md = tmp_path / "06_VERSIONS" / "iteration_001_post_integration.md"
        post_md.parent.mkdir(parents=True, exist_ok=True)
        post_md.write_bytes(b"# stale post version\n")
        driver = ScriptedOrchestrator()
        report = run_int(tmp_path, machine, driver)
        # The master write DID happen (atomic), then the freeze conflict is
        # surfaced honestly: machine at HARD_GATE_VALIDATION, outcome
        # ARTIFACT_CONFLICT, conflicting evidence untouched.
        assert report.outcome is prt.ProposalIntegrationOutcome.ARTIFACT_CONFLICT
        assert machine.phase is pp.ProposalPhase.HARD_GATE_VALIDATION
        assert post_md.read_bytes() == b"# stale post version\n"
        assert pp.ProposalWorkspace(tmp_path).master_proposal_path().read_bytes() == REVISED_TEXT.encode("utf-8")


# ---------------------------------------------------------------------------
# ZERO-AI clean pass (29–32)
# ---------------------------------------------------------------------------
class TestZeroAiCleanPass:
    def _clean(self, tmp_path):
        build_workspace(tmp_path)
        reviewers = default_reviewers()  # all PASS, no findings
        machine = ProposalStateMachine()
        review = run_cycle_to_integration(tmp_path, machine, reviewers)
        assert machine.phase is pp.ProposalPhase.INTEGRATION
        brief = json.loads(brief_path(tmp_path).read_text())
        assert brief["integration_required"] is False
        driver = ScriptedOrchestrator()
        return machine, run_int(tmp_path, machine, driver), driver

    def test_29_clean_pass_makes_zero_driver_calls(self, tmp_path):
        machine, report, driver = self._clean(tmp_path)
        assert report.outcome is prt.ProposalIntegrationOutcome.NO_INTEGRATION_REQUIRED
        assert driver.started == 0

    def test_30_master_remains_byte_identical(self, tmp_path):
        machine, report, _driver = self._clean(tmp_path)
        assert pp.ProposalWorkspace(tmp_path).master_proposal_path().read_bytes() == PROPOSAL_V1
        assert report.output_proposal_hash == HASH1
        assert report.changed is False

    def test_31_noop_integration_artifact_recorded(self, tmp_path):
        machine, report, _driver = self._clean(tmp_path)
        artifact = brief_path(tmp_path).with_name("integration_result.json")
        data = json.loads(artifact.read_text())
        assert data["runtime"]["outcome"] == "NO_INTEGRATION_REQUIRED"
        assert data["output_proposal_hash"] == data["input_proposal_hash"]

    def test_32_state_reaches_hard_gate_validation(self, tmp_path):
        machine, report, _driver = self._clean(tmp_path)
        assert report.state_advanced is True
        assert machine.phase is pp.ProposalPhase.HARD_GATE_VALIDATION


# ---------------------------------------------------------------------------
# PREVIOUS FINDINGS propagation (Session 015A corrective)
# ---------------------------------------------------------------------------
class TestPreviousFindingsPropagation:
    """Session 015A: previous findings reach the ORCHESTRATOR prompt.

    The brief's numbered matrix: None/[] render UNAVAILABLE, records reach
    the prompt deterministically (byte-identical, sorted keys), run_iteration
    forwarding works, oversized payloads fail BEFORE the driver call, the
    zero-AI clean-PASS path stays zero-call without rendering, and no raw
    transcript/provider/model data is introduced.
    """

    def _needs_revision_to_integration(self, tmp_path):
        build_workspace(tmp_path)
        reviewers = default_reviewers(
            verdicts={pp.ProposalRole.SCIENTIFIC_REVIEWER: "NEEDS_REVISION"},
            findings_for={pp.ProposalRole.SCIENTIFIC_REVIEWER: [dict(FINDING)]},
        )
        machine = ProposalStateMachine()
        run_cycle_to_integration(tmp_path, machine, reviewers)
        assert machine.phase is pp.ProposalPhase.INTEGRATION
        return machine

    def _clean_to_integration(self, tmp_path):
        build_workspace(tmp_path)
        reviewers = default_reviewers()  # all PASS, no findings
        machine = ProposalStateMachine()
        run_cycle_to_integration(tmp_path, machine, reviewers)
        assert machine.phase is pp.ProposalPhase.INTEGRATION
        return machine

    def test_none_renders_unavailable_section(self, tmp_path):
        machine = self._needs_revision_to_integration(tmp_path)
        driver = ScriptedOrchestrator()
        report = run_int(
            tmp_path, machine, driver, previous_findings=None
        )
        assert report.outcome is prt.ProposalIntegrationOutcome.COMPLETED_CHANGED
        assert (
            "## PREVIOUS FINDINGS (earlier iterations)\n\n[UNAVAILABLE"
            in driver.prompts[0]
        )

    def test_empty_list_renders_unavailable_section(self, tmp_path):
        machine = self._needs_revision_to_integration(tmp_path)
        driver = ScriptedOrchestrator()
        report = run_int(tmp_path, machine, driver, previous_findings=[])
        assert report.outcome is prt.ProposalIntegrationOutcome.COMPLETED_CHANGED
        assert (
            "## PREVIOUS FINDINGS (earlier iterations)\n\n[UNAVAILABLE"
            in driver.prompts[0]
        )

    def test_records_reach_the_orchestrator_prompt(self, tmp_path):
        machine = self._needs_revision_to_integration(tmp_path)
        records = [
            {
                "severity": "high",
                "message": "Claim X lacks a source",
                "reviewer_role": "SCIENTIFIC_REVIEWER",
                "source_refs": ["00_SOURCE_OF_TRUTH/PROJECT_FACTS.md"],
            },
            {
                "severity": "medium",
                "message": "Terminology drift in section 2",
                "reviewer_role": "RED_TEAM_REVIEWER",
            },
        ]
        driver = ScriptedOrchestrator()
        report = run_int(
            tmp_path, machine, driver, previous_findings=records
        )
        assert report.outcome is prt.ProposalIntegrationOutcome.COMPLETED_CHANGED
        prompt = driver.prompts[0]
        assert "PREVIOUS FINDINGS (earlier iterations)" in prompt
        # Scope to the previous-findings section body: earlier prompt
        # sections (the integration brief) legitimately carry similar keys.
        section = prompt.split(
            "## PREVIOUS FINDINGS (earlier iterations)\n\n", 1
        )[1].split("\n\nREQUIRED OUTPUT", 1)[0]
        for needle in (
            '"message": "Claim X lacks a source"',
            '"reviewer_role": "SCIENTIFIC_REVIEWER"',
            '"severity": "high"',
            '"message": "Terminology drift in section 2"',
            '"reviewer_role": "RED_TEAM_REVIEWER"',
            '"severity": "medium"',
        ):
            assert needle in section
        # Deterministic canonical JSON: keys inside every record sorted.
        assert section.index('"message"') < section.index('"severity"')
        assert section.index('"reviewer_role"') < section.index('"severity"')

    def test_rendering_is_byte_identical_across_runs(self, tmp_path):
        records = [
            {"z": "last", "a": "first", "m": {"y": 2, "b": 1}},
            {"k": [3, 1, 2], "id": "finding:7"},
        ]
        prompts: list[str] = []
        for root in (tmp_path / "run-a", tmp_path / "run-b"):
            machine = self._needs_revision_to_integration(root)
            driver = ScriptedOrchestrator()
            report = run_int(root, machine, driver, previous_findings=records)
            assert report.outcome is (
                prt.ProposalIntegrationOutcome.COMPLETED_CHANGED
            )
            assert len(driver.prompts) == 1
            prompts.append(driver.prompts[0])
        assert prompts[0] == prompts[1]
        # The rendering is exactly the specified canonical JSON.
        expected = json.dumps(
            records, indent=2, sort_keys=True, ensure_ascii=False
        )
        assert expected in prompts[0]

    def test_key_order_is_sorted_and_stable(self, tmp_path):
        machine = self._needs_revision_to_integration(tmp_path)
        records = [{"zebra": 1, "alpha": 2, "middle": 3}]
        driver = ScriptedOrchestrator()
        run_int(tmp_path, machine, driver, previous_findings=records)
        prompt = driver.prompts[0]
        alpha = prompt.index('"alpha"')
        middle = prompt.index('"middle"')
        zebra = prompt.index('"zebra"')
        assert alpha < middle < zebra

    def test_run_iteration_forwards_records_to_the_prompt(self, tmp_path):
        build_workspace(tmp_path)
        reviewers = default_reviewers(
            verdicts={pp.ProposalRole.SCIENTIFIC_REVIEWER: "NEEDS_REVISION"},
            findings_for={pp.ProposalRole.SCIENTIFIC_REVIEWER: [dict(FINDING)]},
        )
        machine = ProposalStateMachine()
        records = [
            {
                "severity": "high",
                "message": "iteration-1 unresolved finding",
                "id": "finding:2",
            }
        ]
        report = prt.run_iteration(
            workspace=tmp_path,
            state_machine=machine,
            iteration_number=1,
            proposal_revision=REVISION,
            reviewer_drivers=reviewers,
            orchestrator_driver=ScriptedOrchestrator(),
            previous_findings_records=records,
        )
        assert report.outcome is prt.ProposalIterationOutcome.READY_FOR_NEXT_ITERATION
        integration = report.integration_report
        assert integration is not None
        assert integration["outcome"] == "COMPLETED_CHANGED"
        # The handoff carries the records verbatim (unchanged NEXT_ITERATION
        # contract); the prompt assertion lives in the dedicated test below.
        handoff = json.loads(
            (tmp_path / "05_CONTROL" / "NEXT_ITERATION.json").read_text()
        )
        assert handoff["previous_findings"] == records

    def test_run_iteration_records_reach_driver_prompt(self, tmp_path):
        build_workspace(tmp_path)
        reviewers = default_reviewers(
            verdicts={pp.ProposalRole.SCIENTIFIC_REVIEWER: "NEEDS_REVISION"},
            findings_for={pp.ProposalRole.SCIENTIFIC_REVIEWER: [dict(FINDING)]},
        )
        machine = ProposalStateMachine()
        records = [{"message": "handoff-records prompt check", "n": 1}]
        orchestrator = ScriptedOrchestrator()
        report = prt.run_iteration(
            workspace=tmp_path,
            state_machine=machine,
            iteration_number=1,
            proposal_revision=REVISION,
            reviewer_drivers=reviewers,
            orchestrator_driver=orchestrator,
            previous_findings_records=records,
        )
        assert report.outcome is prt.ProposalIterationOutcome.READY_FOR_NEXT_ITERATION
        # ScriptedReviewer keeps exactly one attribute `.prompt`; the
        # orchestrator keeps `.prompts` — the single integration call.
        assert len(orchestrator.prompts) == 1
        assert (
            "## PREVIOUS FINDINGS (earlier iterations)"
            in orchestrator.prompts[0]
        )
        assert '"message": "handoff-records prompt check"' in (
            orchestrator.prompts[0]
        )

    def test_oversized_payload_fails_before_driver_call(self, tmp_path):
        machine = self._needs_revision_to_integration(tmp_path)
        # One field above the packet's per-section cap: the rendering is
        # deterministic and exceeds MAX_INTEGRATION_PACKET_SECTION_CHARS.
        oversized = [
            {
                "payload": "x" * (400_000 + 1),
            }
        ]
        driver = ScriptedOrchestrator()
        report = run_int(
            tmp_path, machine, driver, previous_findings=oversized
        )
        assert report.outcome is prt.ProposalIntegrationOutcome.DRIVER_FAILED
        assert "previous findings cannot be rendered" in report.error
        assert driver.started == 0  # ZERO driver contact
        assert machine.phase is pp.ProposalPhase.INTEGRATION
        assert (
            pp.ProposalWorkspace(tmp_path)
            .master_proposal_path()
            .read_bytes()
            == PROPOSAL_V1
        )

    def test_non_list_previous_findings_rejected(self, tmp_path):
        machine = self._needs_revision_to_integration(tmp_path)
        driver = ScriptedOrchestrator()
        report = run_int(
            tmp_path, machine, driver, previous_findings="not a list"
        )
        assert report.outcome is prt.ProposalIntegrationOutcome.DRIVER_FAILED
        assert "must be a list" in report.error
        assert driver.started == 0

    def test_input_records_never_mutated(self, tmp_path):
        machine = self._needs_revision_to_integration(tmp_path)
        records = [{"b": 2, "a": 1, "nested": {"y": 2, "x": 1}}]
        snapshot = copy.deepcopy(records)
        driver = ScriptedOrchestrator()
        report = run_int(
            tmp_path, machine, driver, previous_findings=records
        )
        assert report.outcome is prt.ProposalIntegrationOutcome.COMPLETED_CHANGED
        assert records == snapshot

    def test_no_transcript_or_provider_metadata_introduced(self, tmp_path):
        machine = self._needs_revision_to_integration(tmp_path)
        records = [
            {"message": "legitimate finding", "severity": "medium"},
        ]
        driver = ScriptedOrchestrator()
        run_int(tmp_path, machine, driver, previous_findings=records)
        prompt = driver.prompts[0]
        # Scope to the previous-findings section body: the OUTPUT CONTRACT
        # section after it legitimately quotes the envelope markers, and the
        # sections before it are not this feature's rendering.
        section = prompt.split(
            "## PREVIOUS FINDINGS (earlier iterations)\n\n", 1
        )[1].split("\n\nREQUIRED OUTPUT", 1)[0]
        assert "ENCOMM_PROPOSAL_INTEGRATION_START" not in section
        assert "ENCOMM_PROPOSAL_INTEGRATION_END" not in section
        for forbidden in (
            "provider",
            "model",
            "driver_id",
            "session_id",
            "transcript",
        ):
            # The section rendering carries ONLY the records themselves.
            assert forbidden not in section.lower()

    def test_zero_ai_clean_pass_needs_no_rendering(self, tmp_path):
        machine = self._clean_to_integration(tmp_path)
        driver = ScriptedOrchestrator()
        report = run_int(
            tmp_path,
            machine,
            driver,
            previous_findings=[{"message": "unused on this path"}],
        )
        assert report.outcome is (
            prt.ProposalIntegrationOutcome.NO_INTEGRATION_REQUIRED
        )
        assert driver.started == 0
        assert driver.prompts == []
        # The no-op artifact is still written and the machine advanced.
        assert report.state_advanced is True
        assert machine.phase is pp.ProposalPhase.HARD_GATE_VALIDATION

    def test_next_iteration_handoff_unchanged(self, tmp_path):
        build_workspace(tmp_path)
        reviewers = default_reviewers(
            verdicts={pp.ProposalRole.SCIENTIFIC_REVIEWER: "NEEDS_REVISION"},
            findings_for={pp.ProposalRole.SCIENTIFIC_REVIEWER: [dict(FINDING)]},
        )
        machine = ProposalStateMachine()
        report = prt.run_iteration(
            workspace=tmp_path,
            state_machine=machine,
            iteration_number=1,
            proposal_revision=REVISION,
            reviewer_drivers=reviewers,
            orchestrator_driver=ScriptedOrchestrator(),
        )
        assert report.outcome is prt.ProposalIterationOutcome.READY_FOR_NEXT_ITERATION
        data = json.loads(
            (tmp_path / "05_CONTROL" / "NEXT_ITERATION.json").read_text()
        )
        assert data["previous_findings"] == []
        assert data["next_iteration_number"] == 2
        assert data["previous_reviewed_hash"] == HASH1
        assert data["revised_proposal_hash"] == HASH2


# ---------------------------------------------------------------------------
# NO-CHANGE integration (41–45)
# ---------------------------------------------------------------------------
class TestNoChangeIntegration:
    def _nochange(self, tmp_path):
        build_workspace(tmp_path)
        reviewers = default_reviewers(
            verdicts={pp.ProposalRole.SCIENTIFIC_REVIEWER: "NEEDS_REVISION"},
            findings_for={pp.ProposalRole.SCIENTIFIC_REVIEWER: [dict(FINDING)]},
        )
        machine = ProposalStateMachine()
        run_cycle_to_integration(tmp_path, machine, reviewers)
        driver = ScriptedOrchestrator(
            payload=integration_payload(revised=PROPOSAL_V1.decode("utf-8"),
                                        summary="no change needed")
        )
        return machine, run_int(tmp_path, machine, driver)

    def test_41_orchestrator_returns_identical_text(self, tmp_path):
        machine, report = self._nochange(tmp_path)
        assert report.outcome is prt.ProposalIntegrationOutcome.COMPLETED_NO_CHANGE

    def test_42_no_false_changed_flag(self, tmp_path):
        machine, report = self._nochange(tmp_path)
        assert report.changed is False

    def test_43_output_hash_equals_input_hash(self, tmp_path):
        machine, report = self._nochange(tmp_path)
        assert report.output_proposal_hash == HASH1
        assert report.output_proposal_hash == report.input_proposal_hash

    def test_44_state_reaches_hard_gate_validation(self, tmp_path):
        machine, report = self._nochange(tmp_path)
        assert machine.phase is pp.ProposalPhase.HARD_GATE_VALIDATION

    def test_45_review_freshness_remains_current(self, tmp_path):
        machine, report = self._nochange(tmp_path)
        freshness = prt.evaluate_review_freshness(
            workspace=tmp_path, current_proposal_hash=HASH1
        )
        assert freshness.is_current is True


# ---------------------------------------------------------------------------
# REVISION FRESHNESS + handoff (33–40)
# ---------------------------------------------------------------------------
class TestRevisionFreshnessAndHandoff:
    def _changed(self, tmp_path):
        build_workspace(tmp_path)
        reviewers = default_reviewers(
            verdicts={pp.ProposalRole.SCIENTIFIC_REVIEWER: "NEEDS_REVISION"},
            findings_for={pp.ProposalRole.SCIENTIFIC_REVIEWER: [dict(FINDING)]},
        )
        machine = ProposalStateMachine()
        report = prt.run_iteration(
            workspace=tmp_path,
            state_machine=machine,
            iteration_number=1,
            proposal_revision=REVISION,
            reviewer_drivers=reviewers,
            orchestrator_driver=ScriptedOrchestrator(),
        )
        return machine, report

    def test_33_changed_proposal_cannot_complete(self, tmp_path):
        machine, report = self._changed(tmp_path)
        # A changed proposal never lands in COMPLETE through this flow.
        assert machine.phase is not pp.ProposalPhase.COMPLETE
        assert machine.phase is pp.ProposalPhase.REVISION_REQUIRED

    def test_34_changed_proposal_walks_to_revision_required(self, tmp_path):
        machine, report = self._changed(tmp_path)
        assert report.outcome is prt.ProposalIterationOutcome.READY_FOR_NEXT_ITERATION
        assert machine.phase is pp.ProposalPhase.REVISION_REQUIRED

    def test_35_next_iteration_json_created(self, tmp_path):
        machine, report = self._changed(tmp_path)
        handoff = tmp_path / "05_CONTROL" / "NEXT_ITERATION.json"
        assert handoff.is_file()
        data = json.loads(handoff.read_text())
        assert data["schema"] == prt.REVISION_HANDOFF_SCHEMA

    def test_36_next_iteration_number_correct(self, tmp_path):
        machine, report = self._changed(tmp_path)
        data = json.loads(
            (tmp_path / "05_CONTROL" / "NEXT_ITERATION.json").read_text()
        )
        assert data["previous_iteration_number"] == 1
        assert data["next_iteration_number"] == 2

    def test_37_revised_hash_recorded(self, tmp_path):
        machine, report = self._changed(tmp_path)
        data = json.loads(
            (tmp_path / "05_CONTROL" / "NEXT_ITERATION.json").read_text()
        )
        assert data["previous_reviewed_hash"] == HASH1
        assert data["revised_proposal_hash"] == HASH2

    def test_38_unresolved_items_recorded(self, tmp_path):
        build_workspace(tmp_path)
        reviewers = default_reviewers(
            verdicts={pp.ProposalRole.SCIENTIFIC_REVIEWER: "NEEDS_REVISION"},
            findings_for={pp.ProposalRole.SCIENTIFIC_REVIEWER: [dict(FINDING)]},
        )
        machine = ProposalStateMachine()
        prt.run_iteration(
            workspace=tmp_path,
            state_machine=machine,
            iteration_number=1,
            proposal_revision=REVISION,
            reviewer_drivers=reviewers,
            orchestrator_driver=ScriptedOrchestrator(
                payload=integration_payload(
                    unresolved=[
                        {
                            "item_id": "finding:9",
                            "action": "unresolved",
                            "reason": "needs experimental data",
                        }
                    ]
                )
            ),
        )
        data = json.loads(
            (tmp_path / "05_CONTROL" / "NEXT_ITERATION.json").read_text()
        )
        assert data["unresolved_items"] == [
            {"item_id": "finding:9", "action": "unresolved", "reason": "needs experimental data"}
        ]

    def test_39_next_review_cycle_starts_from_revision_required(self, tmp_path):
        machine, report = self._changed(tmp_path)
        reviewers = default_reviewers(iteration=2)
        review = prt.run_review_cycle(
            workspace=tmp_path,
            state_machine=machine,
            iteration_number=2,
            proposal_revision="rev-2",
            reviewer_drivers=reviewers,
        )
        assert review.outcome is prt.ProposalReviewCycleOutcome.READY_FOR_INTEGRATION
        assert machine.phase is pp.ProposalPhase.INTEGRATION

    def test_40_next_cycle_reviews_new_proposal_hash(self, tmp_path):
        machine, report = self._changed(tmp_path)
        reviewers = default_reviewers(iteration=2)
        prt.run_review_cycle(
            workspace=tmp_path,
            state_machine=machine,
            iteration_number=2,
            proposal_revision="rev-2",
            reviewer_drivers=reviewers,
        )
        for reviewer in reviewers.values():
            assert HASH2 in reviewer.prompt
            assert HASH1 not in reviewer.prompt
        # The new cycle's brief carries the NEW hash.
        brief2 = json.loads(
            (tmp_path / "04_REVIEWS" / "iteration_002" / "integration_brief.json").read_text()
        )
        assert brief2["proposal_hash"] == HASH2


# ---------------------------------------------------------------------------
# COMPOSITION (46–50)
# ---------------------------------------------------------------------------
class TestComposition:
    def test_47_needs_revision_reaches_ready_for_next_iteration(self, tmp_path):
        build_workspace(tmp_path)
        reviewers = default_reviewers(
            verdicts={pp.ProposalRole.RED_TEAM_REVIEWER: "NEEDS_REVISION"},
            findings_for={pp.ProposalRole.RED_TEAM_REVIEWER: [dict(FINDING)]},
        )
        machine = ProposalStateMachine()
        report = prt.run_iteration(
            workspace=tmp_path,
            state_machine=machine,
            iteration_number=1,
            proposal_revision=REVISION,
            reviewer_drivers=reviewers,
            orchestrator_driver=ScriptedOrchestrator(),
        )
        assert report.outcome is prt.ProposalIterationOutcome.READY_FOR_NEXT_ITERATION
        assert report.review_report is not None
        assert report.integration_report is not None
        assert machine.phase is pp.ProposalPhase.REVISION_REQUIRED

    def test_48_clean_pass_zero_ai_reaches_ready_for_hard_gates(self, tmp_path):
        build_workspace(tmp_path)
        reviewers = default_reviewers()
        machine = ProposalStateMachine()
        driver = ScriptedOrchestrator()
        report = prt.run_iteration(
            workspace=tmp_path,
            state_machine=machine,
            iteration_number=1,
            proposal_revision=REVISION,
            reviewer_drivers=reviewers,
            orchestrator_driver=driver,
        )
        assert report.outcome is prt.ProposalIterationOutcome.READY_FOR_HARD_GATES
        assert machine.phase is pp.ProposalPhase.HARD_GATE_VALIDATION
        assert driver.started == 0  # zero-AI bypass
        assert report.review_freshness["is_current"] is True

    def test_49_reviewer_blocked_never_calls_orchestrator(self, tmp_path):
        build_workspace(tmp_path)
        reviewers = default_reviewers(
            verdicts={pp.ProposalRole.SCIENTIFIC_REVIEWER: "BLOCKED"},
            findings_for={
                pp.ProposalRole.SCIENTIFIC_REVIEWER: [dict(FINDING, severity="high")]
            },
        )
        machine = ProposalStateMachine()
        driver = ScriptedOrchestrator()
        report = prt.run_iteration(
            workspace=tmp_path,
            state_machine=machine,
            iteration_number=1,
            proposal_revision=REVISION,
            reviewer_drivers=reviewers,
            orchestrator_driver=driver,
        )
        assert report.outcome is prt.ProposalIterationOutcome.REVIEW_BLOCKED
        assert driver.started == 0
        assert machine.phase is pp.ProposalPhase.BLOCKED
        assert pp.ProposalWorkspace(tmp_path).master_proposal_path().read_bytes() == PROPOSAL_V1

    def test_50_integration_failure_returns_loudly(self, tmp_path):
        build_workspace(tmp_path)
        reviewers = default_reviewers(
            verdicts={pp.ProposalRole.SCIENTIFIC_REVIEWER: "NEEDS_REVISION"},
            findings_for={pp.ProposalRole.SCIENTIFIC_REVIEWER: [dict(FINDING)]},
        )
        machine = ProposalStateMachine()
        run_cycle_to_integration(tmp_path, machine, reviewers)
        # Orchestrator returns malformed JSON → PARSE_FAILED, machine parked
        # at INTEGRATION, master untouched.
        driver = ScriptedOrchestrator(payload={"role": "ORCHESTRATOR"})
        report = run_int(tmp_path, machine, driver)
        assert report.outcome is prt.ProposalIntegrationOutcome.PARSE_FAILED
        assert report.error != ""
        assert machine.phase is pp.ProposalPhase.INTEGRATION


# ---------------------------------------------------------------------------
# master_writer unit surface (phase gate + canonical EOF + stale guard)
# ---------------------------------------------------------------------------
class TestMasterWriterContract:
    def test_phase_gate_refuses_non_integration(self, tmp_path):
        build_workspace(tmp_path)
        machine = ProposalStateMachine()  # IDLE
        with pytest.raises(prt.MasterProposalWriteError) as exc:
            prt.replace_master_proposal(
                workspace=tmp_path,
                state_machine=machine,
                expected_current_hash=HASH1,
                revised_proposal_text="# anything\n",
            )
        assert exc.value.reason == "phase_not_integration"

    def test_stale_input_refuses_overwrite(self, tmp_path):
        build_workspace(tmp_path)
        machine = ProposalStateMachine()
        walk_to_integration(machine)
        with pytest.raises(prt.MasterProposalWriteError) as exc:
            prt.replace_master_proposal(
                workspace=tmp_path,
                state_machine=machine,
                expected_current_hash="d" * 64,
                revised_proposal_text="# anything\n",
            )
        assert exc.value.reason == "stale_input"
        assert pp.ProposalWorkspace(tmp_path).master_proposal_path().read_bytes() == PROPOSAL_V1

    def test_canonical_eof_policy_appends_single_newline(self, tmp_path):
        build_workspace(tmp_path)
        machine = ProposalStateMachine()
        walk_to_integration(machine)
        no_eof = REVISED_TEXT.rstrip("\n")  # a revision WITHOUT trailing EOF
        report = prt.replace_master_proposal(
            workspace=tmp_path,
            state_machine=machine,
            expected_current_hash=HASH1,
            revised_proposal_text=no_eof,
        )
        assert report.changed is True
        assert (
            pp.ProposalWorkspace(tmp_path).master_proposal_path().read_bytes()
            == no_eof.encode("utf-8") + b"\n"
        )

    def test_no_temp_residue_after_write(self, tmp_path):
        build_workspace(tmp_path)
        machine = ProposalStateMachine()
        walk_to_integration(machine)
        prt.replace_master_proposal(
            workspace=tmp_path,
            state_machine=machine,
            expected_current_hash=HASH1,
            revised_proposal_text=REVISED_TEXT,
        )
        residue = [
            p.name
            for p in (tmp_path / "03_PROPOSAL").iterdir()
            if p.name.startswith(".tmp-")
        ]
        assert residue == []


# ---------------------------------------------------------------------------
# review freshness / handoff unit surface
# ---------------------------------------------------------------------------
class TestFreshnessAndHandoffUnits:
    def test_freshness_without_reviews_fails_closed(self, tmp_path):
        build_workspace(tmp_path)
        with pytest.raises(prt.ReviewFreshnessError):
            prt.evaluate_review_freshness(
                workspace=tmp_path, current_proposal_hash=HASH1
            )

    def test_freshness_current_and_stale(self, tmp_path):
        build_workspace(tmp_path)
        reviewers = default_reviewers()
        machine = ProposalStateMachine()
        run_cycle_to_integration(tmp_path, machine, reviewers)
        current = prt.evaluate_review_freshness(
            workspace=tmp_path, current_proposal_hash=HASH1
        )
        assert current.is_current is True
        assert current.latest_iteration == 1
        stale = prt.evaluate_review_freshness(
            workspace=tmp_path, current_proposal_hash=HASH2
        )
        assert stale.is_current is False

    def test_handoff_conflict_fails_closed(self, tmp_path):
        build_workspace(tmp_path)
        control = tmp_path / "05_CONTROL"
        control.mkdir(parents=True, exist_ok=True)
        (control / "NEXT_ITERATION.json").write_text(
            '{"schema": "other"}\n', encoding="utf-8"
        )
        result = pp.ProposalIntegrationResult(
            iteration_number=1,
            input_proposal_hash=HASH1,
            revised_proposal="x",
        )
        payload = prt.build_next_iteration_payload(
            previous_iteration_number=1,
            previous_reviewed_hash=HASH1,
            revised_proposal_hash=HASH2,
            integration_result=result,
        )
        with pytest.raises(prt.NextIterationHandoffError) as exc:
            prt.write_next_iteration_handoff(workspace=tmp_path, payload=payload)
        assert exc.value.reason == "handoff_conflict"
