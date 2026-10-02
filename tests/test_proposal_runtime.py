"""Session 013 — Proposal RUNTIME tests (executor / guards / immutability).

Covers the mandated matrix sections:

* RUNTIME (23-33): correct phase→reviewer mapping; wrong reviewer rejected
  BEFORE any driver contact; valid review advances the phase EXACTLY once;
  malformed output never advances; driver failure never advances; BLOCKED
  behaves explicitly; proposal hash captured before execution; a malicious
  write to MASTER_PROPOSAL during execution is DETECTED and blocks state
  advancement; only a bounded raw excerpt is retained; no full transcript
  persistence is introduced.
* ISOLATION (34-36): ``encomm_pcc.proposal`` still imports no coding/runtime
  packages (subprocess-proof); Coding Mode never imports
  ``proposal_runtime``; ``ProposalRole`` and coding ``AgentRole`` remain
  independent types.

All tests are OFFLINE and deterministic: a scripted in-repo ``BaseDriver``
double, no network, no engine, no provider, no credentials.
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
from encomm_pcc.domain.enums import AgentRole, SessionPolicy
from encomm_pcc.proposal.state_machine import ProposalStateMachine

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"

PROPOSAL_BYTES = b"# MASTER PROPOSAL\n\nSome reviewed content.\n"


# ---------------------------------------------------------------------------
# workspace / packet helpers
# ---------------------------------------------------------------------------
def build_workspace(tmp_path: Path) -> Path:
    ws = pp.ProposalWorkspace(tmp_path)
    ws.initialize()
    ws.master_proposal_path().write_bytes(PROPOSAL_BYTES)
    return tmp_path


def make_packet(
    role: pp.ProposalRole = pp.ProposalRole.SCIENTIFIC_REVIEWER,
    iteration: int = 1,
) -> pp.ProposalReviewPacket:
    inputs = pp.ProposalReviewInputs(
        proposal_text="# MASTER PROPOSAL\n\nSome reviewed content.\n",
        iteration_number=iteration,
        proposal_revision="rev-1",
    )
    return pp.build_review_packet(role, inputs)


def review_payload(
    *,
    role: pp.ProposalRole,
    verdict: str = "PASS",
    iteration: int = 1,
    findings: list | None = None,
    summary: str = "Clean review.",
) -> dict:
    return {
        "reviewer_role": role.value,
        "verdict": verdict,
        "summary": summary,
        "findings": findings or [],
        "proposed_patches": [],
        "unverified_claims": [],
        "iteration_number": iteration,
    }


def envelope(payload: dict) -> str:
    return (
        f"{pp.PROPOSAL_REVIEW_ENVELOPE_START}\n{json.dumps(payload)}\n"
        f"{pp.PROPOSAL_REVIEW_ENVELOPE_END}"
    )


# ---------------------------------------------------------------------------
# scripted driver double (offline; no engine involved)
# ---------------------------------------------------------------------------
class ScriptedDriver:
    """Scripted ``BaseDriver``-shaped double — zero engine involvement.

    ``answers`` are consumed one ``PromptResult`` per prompt.
    ``on_prompt`` runs BEFORE the answer is returned — the malicious-driver
    scenario uses it to mutate MASTER_PROPOSAL mid-execution (proving the
    mutation guard catches a write from INSIDE the driver call).
    """

    def __init__(
        self,
        answers: list[PromptResult] | None = None,
        *,
        on_prompt=None,  # noqa: ANN001
        fail_start: bool = False,
        raise_driver_error: bool = False,
        session_id: str | None = "ext-session-1",
        driver_id: str = "scripted-review",
    ) -> None:
        self.answers = list(answers or [])
        self.on_prompt = on_prompt
        self.fail_start = fail_start
        self.raise_driver_error = raise_driver_error
        self.session_id = session_id
        self.driver_id = driver_id
        self.started = 0
        self.prompts: list[str] = []

    def start_session(self, request):  # noqa: ANN001
        if self.fail_start:
            raise OSError("simulated start failure")
        if self.raise_driver_error:
            raise DriverError("simulated driver error")
        self.started += 1
        return DriverSession(
            driver_id=self.driver_id,
            role=request.role,
            session_id=self.session_id,
            external=bool(self.session_id),
        )

    def resume_session(self, session_id: str, request):  # noqa: ANN001
        raise NotImplementedError

    def send_prompt(self, session, prompt: str) -> PromptHandle:  # noqa: ANN001
        self.prompts.append(prompt)
        return PromptHandle(session=session, prompt=prompt)

    def wait_for_completion(
        self, handle: PromptHandle, timeout_s: float | None = None
    ) -> PromptResult:
        if callable(self.on_prompt):
            self.on_prompt(handle.prompt)
        if not self.answers:
            raise AssertionError("ScriptedDriver: no scripted answer")
        return self.answers.pop(0)


def passing_answer(
    role: pp.ProposalRole = pp.ProposalRole.SCIENTIFIC_REVIEWER,
    iteration: int = 1,
) -> PromptResult:
    return PromptResult(
        ok=True,
        text=envelope(review_payload(role=role, verdict="PASS", iteration=iteration)),
        session_id="ext-session-1",
        duration_s=0.01,
    )


def running_machine(phase: pp.ProposalPhase) -> ProposalStateMachine:
    """A machine legally walked to ``phase`` from IDLE."""
    machine = ProposalStateMachine()
    path = {
        pp.ProposalPhase.SCIENTIFIC_REVIEW: ["SOURCE_VALIDATION", "SCIENTIFIC_REVIEW"],
        pp.ProposalPhase.IMPLEMENTATION_REVIEW: [
            "SOURCE_VALIDATION",
            "SCIENTIFIC_REVIEW",
            "IMPLEMENTATION_REVIEW",
        ],
        pp.ProposalPhase.RED_TEAM_REVIEW: [
            "SOURCE_VALIDATION",
            "SCIENTIFIC_REVIEW",
            "IMPLEMENTATION_REVIEW",
            "RED_TEAM_REVIEW",
        ],
    }[phase]
    for step in path:
        machine.transition_to(pp.ProposalPhase(step))
    return machine


# ---------------------------------------------------------------------------
# RUNTIME
# ---------------------------------------------------------------------------
class TestReviewExecutor:
    def test_23_correct_phase_calls_correct_reviewer(self, tmp_path: Path) -> None:
        root = build_workspace(tmp_path)
        machine = running_machine(pp.ProposalPhase.IMPLEMENTATION_REVIEW)
        driver = ScriptedDriver(
            [passing_answer(pp.ProposalRole.PROPOSAL_ENGINEER, 1)]
        )
        report = prt.run_review(
            packet=make_packet(pp.ProposalRole.PROPOSAL_ENGINEER),
            driver=driver,  # type: ignore[arg-type]
            state_machine=machine,
            master_proposal_path=root / "03_PROPOSAL" / "MASTER_PROPOSAL.md",
        )
        assert report.ok
        assert driver.started == 1
        assert "PROPOSAL_ENGINEER" in driver.prompts[0]
        assert report.new_phase is pp.ProposalPhase.RED_TEAM_REVIEW

    def test_24_wrong_reviewer_for_phase_rejected_before_driver(
        self, tmp_path: Path
    ) -> None:
        root = build_workspace(tmp_path)
        machine = running_machine(pp.ProposalPhase.SCIENTIFIC_REVIEW)
        driver = ScriptedDriver([passing_answer()])
        with pytest.raises(prt.ProposalReviewGuardError) as excinfo:
            prt.run_review(
                packet=make_packet(pp.ProposalRole.RED_TEAM_REVIEWER),
                driver=driver,  # type: ignore[arg-type]
                state_machine=machine,
                master_proposal_path=root / "03_PROPOSAL" / "MASTER_PROPOSAL.md",
            )
        assert excinfo.value.reason == "phase_role_mismatch"
        # Rejected BEFORE any driver contact.
        assert driver.started == 0
        assert driver.prompts == []
        assert machine.phase is pp.ProposalPhase.SCIENTIFIC_REVIEW

    def test_wrong_phase_for_role_rejected_before_driver(self, tmp_path: Path) -> None:
        root = build_workspace(tmp_path)
        machine = running_machine(pp.ProposalPhase.RED_TEAM_REVIEW)
        driver = ScriptedDriver([passing_answer()])
        with pytest.raises(prt.ProposalReviewGuardError):
            prt.run_review(
                packet=make_packet(pp.ProposalRole.SCIENTIFIC_REVIEWER),
                driver=driver,  # type: ignore[arg-type]
                state_machine=machine,
                master_proposal_path=root / "03_PROPOSAL" / "MASTER_PROPOSAL.md",
            )
        assert driver.started == 0

    def test_25_valid_review_advances_phase_exactly_once(self, tmp_path: Path) -> None:
        root = build_workspace(tmp_path)
        machine = running_machine(pp.ProposalPhase.SCIENTIFIC_REVIEW)
        driver = ScriptedDriver([passing_answer()])
        report = prt.run_review(
            packet=make_packet(),
            driver=driver,  # type: ignore[arg-type]
            state_machine=machine,
            master_proposal_path=root / "03_PROPOSAL" / "MASTER_PROPOSAL.md",
        )
        assert report.ok
        assert report.state_advanced is True
        assert machine.phase is pp.ProposalPhase.IMPLEMENTATION_REVIEW
        # Exactly once: a second identical call now hits the phase guard.
        driver2 = ScriptedDriver([passing_answer()])
        with pytest.raises(prt.ProposalReviewGuardError):
            prt.run_review(
                packet=make_packet(),
                driver=driver2,  # type: ignore[arg-type]
                state_machine=machine,
                master_proposal_path=root / "03_PROPOSAL" / "MASTER_PROPOSAL.md",
            )
        assert driver2.started == 0
        assert machine.phase is pp.ProposalPhase.IMPLEMENTATION_REVIEW

    def test_red_team_success_lands_on_integration_never_past(self, tmp_path: Path) -> None:
        root = build_workspace(tmp_path)
        machine = running_machine(pp.ProposalPhase.RED_TEAM_REVIEW)
        driver = ScriptedDriver(
            [passing_answer(pp.ProposalRole.RED_TEAM_REVIEWER, 1)]
        )
        report = prt.run_review(
            packet=make_packet(pp.ProposalRole.RED_TEAM_REVIEWER),
            driver=driver,  # type: ignore[arg-type]
            state_machine=machine,
            master_proposal_path=root / "03_PROPOSAL" / "MASTER_PROPOSAL.md",
        )
        assert report.ok
        assert machine.phase is pp.ProposalPhase.INTEGRATION
        # COMPLETE stays reserved: the machine can NEVER leave INTEGRATION
        # through this layer, and COMPLETE has no incoming edge from it.
        assert not machine.can_transition(
            pp.ProposalPhase.INTEGRATION, pp.ProposalPhase.COMPLETE
        )

    def test_26_malformed_output_does_not_advance_state(self, tmp_path: Path) -> None:
        root = build_workspace(tmp_path)
        machine = running_machine(pp.ProposalPhase.SCIENTIFIC_REVIEW)
        driver = ScriptedDriver(
            [PromptResult(ok=True, text="All good — PASS, ship it.", duration_s=0.01)]
        )
        report = prt.run_review(
            packet=make_packet(),
            driver=driver,  # type: ignore[arg-type]
            state_machine=machine,
            master_proposal_path=root / "03_PROPOSAL" / "MASTER_PROPOSAL.md",
        )
        assert report.outcome is prt.ProposalReviewOutcome.PARSE_FAILED
        assert report.parse_reason == "missing_envelope"
        assert machine.phase is pp.ProposalPhase.SCIENTIFIC_REVIEW
        assert report.state_advanced is False

    def test_27_driver_failure_does_not_advance_state(self, tmp_path: Path) -> None:
        root = build_workspace(tmp_path)
        machine = running_machine(pp.ProposalPhase.SCIENTIFIC_REVIEW)

        failing = ScriptedDriver([PromptResult.failure("engine crashed")])
        report = prt.run_review(
            packet=make_packet(),
            driver=failing,  # type: ignore[arg-type]
            state_machine=machine,
            master_proposal_path=root / "03_PROPOSAL" / "MASTER_PROPOSAL.md",
        )
        assert report.outcome is prt.ProposalReviewOutcome.DRIVER_FAILED
        assert "engine crashed" in report.error
        assert machine.phase is pp.ProposalPhase.SCIENTIFIC_REVIEW

        raising = ScriptedDriver(raise_driver_error=True)
        report2 = prt.run_review(
            packet=make_packet(),
            driver=raising,  # type: ignore[arg-type]
            state_machine=machine,
            master_proposal_path=root / "03_PROPOSAL" / "MASTER_PROPOSAL.md",
        )
        assert report2.outcome is prt.ProposalReviewOutcome.DRIVER_FAILED
        assert machine.phase is pp.ProposalPhase.SCIENTIFIC_REVIEW

        start_fail = ScriptedDriver(fail_start=True)
        report3 = prt.run_review(
            packet=make_packet(),
            driver=start_fail,  # type: ignore[arg-type]
            state_machine=machine,
            master_proposal_path=root / "03_PROPOSAL" / "MASTER_PROPOSAL.md",
        )
        assert report3.outcome is prt.ProposalReviewOutcome.DRIVER_FAILED
        assert machine.phase is pp.ProposalPhase.SCIENTIFIC_REVIEW

    def test_empty_answer_is_driver_failure(self, tmp_path: Path) -> None:
        root = build_workspace(tmp_path)
        machine = running_machine(pp.ProposalPhase.SCIENTIFIC_REVIEW)
        driver = ScriptedDriver([PromptResult(ok=True, text="   ")])
        report = prt.run_review(
            packet=make_packet(),
            driver=driver,  # type: ignore[arg-type]
            state_machine=machine,
            master_proposal_path=root / "03_PROPOSAL" / "MASTER_PROPOSAL.md",
        )
        assert report.outcome is prt.ProposalReviewOutcome.DRIVER_FAILED
        assert "empty" in report.error
        assert machine.phase is pp.ProposalPhase.SCIENTIFIC_REVIEW

    def test_28_blocked_behaves_explicitly(self, tmp_path: Path) -> None:
        root = build_workspace(tmp_path)
        machine = running_machine(pp.ProposalPhase.SCIENTIFIC_REVIEW)
        blocked = PromptResult(
            ok=True,
            text=envelope(
                review_payload(
                    role=pp.ProposalRole.SCIENTIFIC_REVIEWER,
                    verdict="BLOCKED",
                    summary="Official requirements unavailable; cannot assess.",
                )
            ),
        )
        report = prt.run_review(
            packet=make_packet(),
            driver=ScriptedDriver([blocked]),  # type: ignore[arg-type]
            state_machine=machine,
            master_proposal_path=root / "03_PROPOSAL" / "MASTER_PROPOSAL.md",
        )
        # A BLOCKED verdict is a VALID review: execution COMPLETED and the
        # machine took the explicit D-055 BLOCKED edge — never a fake success.
        assert report.outcome is prt.ProposalReviewOutcome.COMPLETED
        assert report.result is not None
        assert report.result.verdict is pp.ProposalReviewVerdict.BLOCKED
        assert machine.phase is pp.ProposalPhase.BLOCKED
        assert report.new_phase is pp.ProposalPhase.BLOCKED

    def test_29_proposal_hash_captured_before_execution(self, tmp_path: Path) -> None:
        root = build_workspace(tmp_path)
        machine = running_machine(pp.ProposalPhase.SCIENTIFIC_REVIEW)
        report = prt.run_review(
            packet=make_packet(),
            driver=ScriptedDriver([passing_answer()]),  # type: ignore[arg-type]
            state_machine=machine,
            master_proposal_path=root / "03_PROPOSAL" / "MASTER_PROPOSAL.md",
        )
        expected = pp.proposal_fingerprint(
            root / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
        )
        assert report.proposal_hash == expected
        assert report.proposal_hash_after == expected
        assert len(report.proposal_hash) == 64

    def test_30_31_malicious_master_proposal_write_is_detected_and_blocks(
        self, tmp_path: Path
    ) -> None:
        root = build_workspace(tmp_path)
        master = root / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
        machine = running_machine(pp.ProposalPhase.SCIENTIFIC_REVIEW)
        original = master.read_bytes()

        def malicious_write(prompt: str) -> None:
            # The "reviewer" edits the master proposal DURING execution.
            master.write_bytes(PROPOSAL_BYTES + b"\nTAMPERED BY THE REVIEWER\n")

        driver = ScriptedDriver(
            [passing_answer()], on_prompt=malicious_write
        )
        report = prt.run_review(
            packet=make_packet(),
            driver=driver,  # type: ignore[arg-type]
            state_machine=machine,
            master_proposal_path=master,
        )
        assert report.outcome is prt.ProposalReviewOutcome.MUTATION_DETECTED
        assert not report.ok
        # The parsed verdict was PASS — refused anyway.
        assert report.result is not None
        assert report.result.verdict is pp.ProposalReviewVerdict.PASS
        assert report.state_advanced is False
        assert machine.phase is pp.ProposalPhase.SCIENTIFIC_REVIEW
        assert report.proposal_hash != report.proposal_hash_after
        assert "write-authority violation" in report.error
        # The guard never restores/deletes user or model changes silently:
        assert b"TAMPERED BY THE REVIEWER" in master.read_bytes()
        assert original != master.read_bytes()

    def test_31_mutation_detection_prevents_state_advancement_everywhere(
        self, tmp_path: Path
    ) -> None:
        for phase, role in (
            (pp.ProposalPhase.IMPLEMENTATION_REVIEW, pp.ProposalRole.PROPOSAL_ENGINEER),
            (pp.ProposalPhase.RED_TEAM_REVIEW, pp.ProposalRole.RED_TEAM_REVIEWER),
        ):
            root = build_workspace(tmp_path / phase.value)
            master = root / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
            machine = running_machine(phase)
            driver = ScriptedDriver(
                [passing_answer(role, 1)],
                on_prompt=lambda _p: master.write_bytes(b"rewritten\n"),
            )
            report = prt.run_review(
                packet=make_packet(role),
                driver=driver,  # type: ignore[arg-type]
                state_machine=machine,
                master_proposal_path=master,
            )
            assert report.outcome is prt.ProposalReviewOutcome.MUTATION_DETECTED
            assert machine.phase is phase

    def test_fingerprint_failure_before_execution_is_a_guard_error(
        self, tmp_path: Path
    ) -> None:
        # No workspace at all: the master proposal path does not exist, so the
        # pre-execution fingerprint must refuse BEFORE any driver contact.
        machine = running_machine(pp.ProposalPhase.SCIENTIFIC_REVIEW)
        absent = tmp_path / "no_workspace" / "MASTER_PROPOSAL.md"
        with pytest.raises(prt.ProposalReviewGuardError) as excinfo:
            prt.run_review(
                packet=make_packet(),
                driver=ScriptedDriver([passing_answer()]),  # type: ignore[arg-type]
                state_machine=machine,
                master_proposal_path=absent,
            )
        assert excinfo.value.reason == "fingerprint_failed"

    def test_32_runtime_stores_only_bounded_raw_excerpt(self, tmp_path: Path) -> None:
        root = build_workspace(tmp_path)
        machine = running_machine(pp.ProposalPhase.SCIENTIFIC_REVIEW)
        huge = "x" * (prt.MAX_EXCERPT_CHARS * 3 + 17)
        driver = ScriptedDriver(
            [PromptResult(ok=True, text=huge, duration_s=0.01)]
        )
        report = prt.run_review(
            packet=make_packet(),
            driver=driver,  # type: ignore[arg-type]
            state_machine=machine,
            master_proposal_path=root / "03_PROPOSAL" / "MASTER_PROPOSAL.md",
        )
        assert report.outcome is prt.ProposalReviewOutcome.PARSE_FAILED
        assert len(report.raw_excerpt) == prt.MAX_EXCERPT_CHARS

    def test_33_no_full_transcript_persistence_is_introduced(self) -> None:
        # Session 013 contract, UPDATED BY DESIGN in Session 014: the runtime
        # package still contains NO database/persistence machinery at all,
        # and the executor still performs no file I/O (bounded evidence stays
        # in memory).  Session 014 adds the 04_REVIEWS/+06_VERSIONS/ writers
        # (review_artifacts.py / version_freeze.py) as the ONLY file writers
        # in the package — structured artifacts only, transcripts never.
        runtime_dir = SRC_ROOT / "encomm_pcc" / "proposal_runtime"
        db_forbidden = ("sqlite3", "Database(", "INSERT INTO", "to_csv")
        for path in sorted(runtime_dir.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for forbidden in db_forbidden:
                assert forbidden not in text, f"{path}: contains {forbidden!r}"
        # The executor itself performs NO file writes at all.
        executor = (runtime_dir / "review_executor.py").read_text(encoding="utf-8")
        for forbidden in ("write_text", "write_bytes", ".write(", "open("):
            assert forbidden not in executor, (
                f"review_executor.py: contains {forbidden!r}"
            )
        # The artifact writers never route the executor's raw-excerpt surface
        # to disk: no attribute access to raw_excerpt and no whole-report
        # to_dict() serialisation inside the writers (the loop may keep the
        # execution to_dict() IN MEMORY; only the whitelisted writers persist).
        for name in ("review_artifacts.py", "version_freeze.py"):
            text = (runtime_dir / name).read_text(encoding="utf-8")
            for forbidden in (".raw_excerpt", "execution.to_dict"):
                assert forbidden not in text, f"{name}: contains {forbidden!r}"

    def test_report_serialises_to_json_friendly_dict(self, tmp_path: Path) -> None:
        root = build_workspace(tmp_path)
        machine = running_machine(pp.ProposalPhase.SCIENTIFIC_REVIEW)
        report = prt.run_review(
            packet=make_packet(),
            driver=ScriptedDriver([passing_answer()]),  # type: ignore[arg-type]
            state_machine=machine,
            master_proposal_path=root / "03_PROPOSAL" / "MASTER_PROPOSAL.md",
        )
        data = report.to_dict()
        assert data["outcome"] == "COMPLETED"
        assert data["reviewer_role"] == "SCIENTIFIC_REVIEWER"
        assert data["session_id"] == "ext-session-1"
        json.dumps(data)  # must be JSON-serialisable

    def test_session_policy_is_forwarded_to_the_driver(self, tmp_path: Path) -> None:
        root = build_workspace(tmp_path)
        machine = running_machine(pp.ProposalPhase.SCIENTIFIC_REVIEW)
        seen: dict = {}

        class PolicyProbe(ScriptedDriver):
            def start_session(self, request):  # noqa: ANN001
                seen["policy"] = request.session_policy
                seen["extra"] = dict(request.extra)
                return super().start_session(request)

        prt.run_review(
            packet=make_packet(),
            driver=PolicyProbe([passing_answer()]),  # type: ignore[arg-type]
            state_machine=machine,
            master_proposal_path=root / "03_PROPOSAL" / "MASTER_PROPOSAL.md",
            session_policy=SessionPolicy.PERSISTENT_PER_BATCH,
        )
        assert seen["policy"] is SessionPolicy.PERSISTENT_PER_BATCH
        assert seen["extra"]["proposal_role"] == "SCIENTIFIC_REVIEWER"

    def test_timeout_is_forwarded(self, tmp_path: Path) -> None:
        root = build_workspace(tmp_path)
        machine = running_machine(pp.ProposalPhase.SCIENTIFIC_REVIEW)
        seen: dict = {}

        class TimeoutProbe(ScriptedDriver):
            def wait_for_completion(self, handle, timeout_s=None):  # noqa: ANN001
                seen["timeout"] = timeout_s
                return passing_answer()

        prt.run_review(
            packet=make_packet(),
            driver=TimeoutProbe([passing_answer()]),  # type: ignore[arg-type]
            state_machine=machine,
            master_proposal_path=root / "03_PROPOSAL" / "MASTER_PROPOSAL.md",
            timeout_s=42.0,
        )
        assert seen["timeout"] == 42.0


# ---------------------------------------------------------------------------
# ISOLATION
# ---------------------------------------------------------------------------
class TestIsolation:
    def test_34_pure_proposal_package_stays_isolated(self) -> None:
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
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "ok" in result.stdout

    def test_35_coding_mode_never_imports_proposal_runtime(self) -> None:
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
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "ok" in result.stdout

    def test_proposal_runtime_never_imports_ui_or_persistence(self) -> None:
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
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "ok" in result.stdout

    def test_36_proposal_role_and_agent_role_remain_independent(self) -> None:
        # Parallel types — never aliases, even where values coincide.
        assert pp.ProposalRole is not AgentRole
        assert pp.ProposalRole.ORCHESTRATOR is not AgentRole.ORCHESTRATOR
        assert pp.ProposalRole.SCIENTIFIC_REVIEWER.value not in {
            r.value for r in AgentRole
        }
        # The runtime maps reviewer work onto a GENERIC driver role value
        # without aliasing the types.
        assert prt.__dict__  # runtime imports cleanly beside the assertion
        from encomm_pcc.proposal_runtime.review_executor import _REVIEW_AGENT_ROLE

        assert isinstance(_REVIEW_AGENT_ROLE, AgentRole)
        assert not isinstance(_REVIEW_AGENT_ROLE, pp.ProposalRole)

    def test_phase_role_mapping_is_total_over_reviewers(self) -> None:
        assert prt.PHASE_FOR_REVIEWER == {
            pp.ProposalRole.SCIENTIFIC_REVIEWER: pp.ProposalPhase.SCIENTIFIC_REVIEW,
            pp.ProposalRole.PROPOSAL_ENGINEER: pp.ProposalPhase.IMPLEMENTATION_REVIEW,
            pp.ProposalRole.RED_TEAM_REVIEWER: pp.ProposalPhase.RED_TEAM_REVIEW,
        }
        # The ORCHESTRATOR never appears in the reviewer mapping.
        assert pp.ProposalRole.ORCHESTRATOR not in prt.PHASE_FOR_REVIEWER
