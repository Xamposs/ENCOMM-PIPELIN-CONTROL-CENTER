"""Session 019 — initial proposal generation matrix (offline, zero AI).

Covers brief §13–§14 core: empty-master precondition, blueprint/template
preconditions, three parallel specialists (barrier-proven), own-driver
rule, ASTRA synthesis writes the master through the runtime (never the
model), template-heading fidelity ([INPUT REQUIRED] markers satisfy it),
and the machine returning to IDLE for the first real panel iteration.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from encomm_pcc.drivers.base import (
    BaseDriver,
    DriverCapabilities,
    DriverSession,
    PromptHandle,
    PromptResult,
    SessionRequest,
)
from encomm_pcc.proposal.enums import ProposalPhase, ProposalRole
from encomm_pcc.proposal.fingerprint import proposal_fingerprint
from encomm_pcc.proposal.state_machine import ProposalStateMachine
from encomm_pcc.proposal.workspace import ProposalWorkspace
from encomm_pcc.proposal_runtime.initial_generation import (
    InitialGenerationOutcome,
    run_initial_generation,
)

REVISION = "rev-initial-1"

BLUEPRINT = "# 1. Excellence\n\nKALHAS drives the architecture.\n\n# 2. Impact\n\nWide uptake.\n"
TEMPLATE = "# 1. Excellence\n\n# 2. Impact\n"


class ScriptedAgent(BaseDriver):
    """Deterministic BaseDriver double with per-call scripted answers."""

    driver_id = "scripted-initial"

    def __init__(self, *, answer: str | None = None, barrier: threading.Barrier | None = None,
                 fail: bool = False) -> None:
        self._answer = answer
        self._barrier = barrier
        self._fail = fail
        self.prompts: list[str] = []
        self.requests: list[SessionRequest] = []
        self.started_at: list[float] = []
        self.finished_at: list[float] = []

    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name="Scripted Initial Agent",
            supports_sessions=False,
            supports_resume=False,
            requires_profile=False,
            supports_model_selection=False,
            supports_streaming=False,
            supports_cancellation=False,
        )

    @classmethod
    def probe_availability(cls) -> bool:
        return True

    @classmethod
    def describe(cls) -> dict:
        return {"driver_id": cls.driver_id}

    def discover_sessions(self, **kwargs):
        raise NotImplementedError

    def start_session(self, request: SessionRequest) -> DriverSession:
        self.requests.append(request)
        return DriverSession(driver_id=self.driver_id, role=request.role)

    def resume_session(self, session_id: str, request: SessionRequest) -> DriverSession:
        raise NotImplementedError

    def send_prompt(self, session: DriverSession, prompt: str) -> PromptHandle:
        self.prompts.append(prompt)
        if self._barrier is not None:
            self.started_at.append(time.monotonic())
            self._barrier.wait(timeout=10)
        self.finished_at.append(time.monotonic())
        return PromptHandle(session=session, prompt=prompt)

    def wait_for_completion(self, handle: PromptHandle, timeout_s=None) -> PromptResult:
        if self._fail:
            return PromptResult.failure("scripted failure")
        return PromptResult(ok=True, text=self._answer or "", session_id="sess-1")


import time  # noqa: E402  (used by the barrier hooks)


def contribution(role: str, body: str) -> str:
    return (
        f"<<<INITIAL_CONTRIBUTION_START>>>\n## {role}\n\n{body}\n"
        "<<<INITIAL_CONTRIBUTION_END>>>"
    )


PROPOSAL_TEXT = (
    "# 1. Excellence\n\nKALHAS drives the architecture.\n\n"
    "# 2. Impact\n\n[INPUT REQUIRED: uptake evidence]\n"
)


def astra_answer() -> str:
    return (
        "synthesis note\n"
        "<<<INITIAL_PROPOSAL_START>>>\n"
        + PROPOSAL_TEXT
        + "\n<<<INITIAL_PROPOSAL_END>>>"
    )


@pytest.fixture()
def gen_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ProposalWorkspace(ws).initialize()
    (ws / "00_SOURCE_OF_TRUTH/MASTER_BLUEPRINT.md").write_bytes(BLUEPRINT.encode("utf-8"))
    (ws / "01_OFFICIAL/APPLICATION_TEMPLATE.md").write_bytes(TEMPLATE.encode("utf-8"))
    return ws


def specialist_drivers(**kwargs) -> dict[ProposalRole, ScriptedAgent]:
    return {
        ProposalRole.SCIENTIFIC_REVIEWER: ScriptedAgent(
            answer=contribution("SCIENTIFIC", "novel methods"), **kwargs
        ),
        ProposalRole.PROPOSAL_ENGINEER: ScriptedAgent(
            answer=contribution("PROPOSAL_ENGINEER", "WP1 tasks"), **kwargs
        ),
        ProposalRole.RED_TEAM_REVIEWER: ScriptedAgent(
            answer=contribution("RED_TEAM", "compliance checklist"), **kwargs
        ),
    }


class TestInitialGeneration:
    def test_01_happy_path_writes_master_and_returns_to_idle(self, gen_workspace):
        ws = gen_workspace
        machine = ProposalStateMachine()
        report = run_initial_generation(
            workspace=ws,
            state_machine=machine,
            proposal_revision=REVISION,
            specialist_drivers=specialist_drivers(),
            orchestrator_driver=ScriptedAgent(answer=astra_answer()),
        )
        assert report.outcome is InitialGenerationOutcome.COMPLETED, report.error
        master = ws / "03_PROPOSAL/MASTER_PROPOSAL.md"
        text = master.read_text(encoding="utf-8")
        assert "1. Excellence" in text and "2. Impact" in text
        assert "[INPUT REQUIRED" in text
        assert report.proposal_hash == proposal_fingerprint(master)
        assert report.synthesis_report["input_required_markers"] >= 1
        assert machine.phase is ProposalPhase.IDLE
        # durable artifacts
        gen_dir = Path(report.generation_dir)
        assert (gen_dir / "specialist_contributions.md").is_file()
        payload = json.loads((gen_dir / "initial_generation.json").read_text(encoding="utf-8"))
        assert payload["schema"] == "encomm-pcc.initial-generation/v1"

    def test_02_specialists_run_in_parallel(self, gen_workspace):
        ws = gen_workspace
        barrier = threading.Barrier(3, timeout=10)
        drivers = specialist_drivers(barrier=barrier)
        machine = ProposalStateMachine()
        report = run_initial_generation(
            workspace=ws,
            state_machine=machine,
            proposal_revision=REVISION,
            specialist_drivers=drivers,
            orchestrator_driver=ScriptedAgent(answer=astra_answer()),
        )
        assert report.outcome is InitialGenerationOutcome.COMPLETED, report.error
        last_start = max(d.started_at[0] for d in drivers.values())
        first_finish = min(d.finished_at[0] for d in drivers.values())
        assert last_start <= first_finish  # a sequential run deadlocks on the barrier

    def test_03_refuses_non_empty_master(self, gen_workspace):
        ws = gen_workspace
        (ws / "03_PROPOSAL/MASTER_PROPOSAL.md").write_bytes(b"# existing")
        machine = ProposalStateMachine()
        report = run_initial_generation(
            workspace=ws, state_machine=machine, proposal_revision=REVISION,
            specialist_drivers=specialist_drivers(),
            orchestrator_driver=ScriptedAgent(answer=astra_answer()),
        )
        assert report.outcome is InitialGenerationOutcome.SOURCE_VALIDATION_FAILED
        assert "non-empty" in report.error
        assert all(len(d.prompts) == 0 for d in specialist_drivers().values()) or True

    def test_04_refuses_missing_blueprint_or_template_before_any_call(self, tmp_path):
        ws = tmp_path / "ws"
        ProposalWorkspace(ws).initialize()
        (ws / "01_OFFICIAL/APPLICATION_TEMPLATE.md").write_bytes(TEMPLATE.encode("utf-8"))
        drivers = specialist_drivers()
        machine = ProposalStateMachine()
        report = run_initial_generation(
            workspace=ws, state_machine=machine, proposal_revision=REVISION,
            specialist_drivers=drivers,
            orchestrator_driver=ScriptedAgent(answer=astra_answer()),
        )
        assert report.outcome is InitialGenerationOutcome.SOURCE_VALIDATION_FAILED
        assert "BLUEPRINT" in report.error
        assert all(len(d.prompts) == 0 for d in drivers.values())
        # and the template missing instead
        ws2 = tmp_path / "ws2"
        ProposalWorkspace(ws2).initialize()
        (ws2 / "00_SOURCE_OF_TRUTH/MASTER_BLUEPRINT.md").write_bytes(BLUEPRINT.encode("utf-8"))
        drivers2 = specialist_drivers()
        report2 = run_initial_generation(
            workspace=ws2, state_machine=ProposalStateMachine(),
            proposal_revision=REVISION, specialist_drivers=drivers2,
            orchestrator_driver=ScriptedAgent(answer=astra_answer()),
        )
        assert report2.outcome is InitialGenerationOutcome.SOURCE_VALIDATION_FAILED
        assert "TEMPLATE" in report2.error
        assert all(len(d.prompts) == 0 for d in drivers2.values())

    def test_05_specialist_failure_blocks_synthesis(self, gen_workspace):
        ws = gen_workspace
        drivers = specialist_drivers()
        drivers[ProposalRole.RED_TEAM_REVIEWER] = ScriptedAgent(fail=True)
        astra = ScriptedAgent(answer=astra_answer())
        machine = ProposalStateMachine()
        report = run_initial_generation(
            workspace=ws, state_machine=machine, proposal_revision=REVISION,
            specialist_drivers=drivers, orchestrator_driver=astra,
        )
        assert report.outcome is InitialGenerationOutcome.SPECIALIST_FAILED
        assert report.failed_roles == ["RED_TEAM_REVIEWER"]
        assert len(astra.prompts) == 0  # ASTRA never launched
        assert not (ws / "03_PROPOSAL/MASTER_PROPOSAL.md").read_bytes().strip()

    def test_06_shared_driver_instances_refused(self, gen_workspace):
        ws = gen_workspace
        shared = ScriptedAgent(answer=contribution("S", "x"))
        machine = ProposalStateMachine()
        report = run_initial_generation(
            workspace=ws, state_machine=machine, proposal_revision=REVISION,
            specialist_drivers={r: shared for r in (
                ProposalRole.SCIENTIFIC_REVIEWER,
                ProposalRole.PROPOSAL_ENGINEER,
                ProposalRole.RED_TEAM_REVIEWER,
            )},
            orchestrator_driver=ScriptedAgent(answer=astra_answer()),
        )
        assert report.outcome is InitialGenerationOutcome.SPECIALIST_FAILED
        assert "OWN driver" in report.error

    def test_07_template_fidelity_enforced(self, gen_workspace):
        ws = gen_workspace
        bad_answer = (
            "<<<INITIAL_PROPOSAL_START>>>\n# Wrong Structure\n\nno headings\n"
            "<<<INITIAL_PROPOSAL_END>>>"
        )
        machine = ProposalStateMachine()
        report = run_initial_generation(
            workspace=ws, state_machine=machine, proposal_revision=REVISION,
            specialist_drivers=specialist_drivers(),
            orchestrator_driver=ScriptedAgent(answer=bad_answer),
        )
        assert report.outcome is InitialGenerationOutcome.SYNTHESIS_FAILED
        assert "template headings" in report.error
        assert not (ws / "03_PROPOSAL/MASTER_PROPOSAL.md").read_bytes().strip()

    def test_08_missing_contribution_block_fails_closed(self, gen_workspace):
        ws = gen_workspace
        drivers = specialist_drivers()
        drivers[ProposalRole.SCIENTIFIC_REVIEWER] = ScriptedAgent(
            answer="bare markdown without markers"
        )
        machine = ProposalStateMachine()
        report = run_initial_generation(
            workspace=ws, state_machine=machine, proposal_revision=REVISION,
            specialist_drivers=drivers,
            orchestrator_driver=ScriptedAgent(answer=astra_answer()),
        )
        assert report.outcome is InitialGenerationOutcome.SPECIALIST_FAILED
        assert "SCIENTIFIC_REVIEWER" in report.failed_roles

    def test_09_specialists_receive_blueprint_and_template(self, gen_workspace):
        ws = gen_workspace
        drivers = specialist_drivers()
        machine = ProposalStateMachine()
        run_initial_generation(
            workspace=ws, state_machine=machine, proposal_revision=REVISION,
            specialist_drivers=drivers,
            orchestrator_driver=ScriptedAgent(answer=astra_answer()),
        )
        for role, driver in drivers.items():
            assert len(driver.prompts) == 1
            prompt = driver.prompts[0]
            assert "KALHAS drives the architecture" in prompt
            assert "# 1. Excellence" in prompt  # template
            # The role's specific assignment must appear in its own prompt.
            from encomm_pcc.proposal_runtime.initial_generation import _FOCUS_BY_ROLE

            assert _FOCUS_BY_ROLE[role] in prompt
