"""Session 019 — panel-chair composition + model-call accounting (offline).

Proves the FULL §21 chain: parallel first pass → parallel consensus →
ASTRA chair revision (the consensus matrix demonstrably reaches the ASTRA
packet) → freshness → next-iteration handoff.  Zero model calls.
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
from encomm_pcc.proposal_runtime.panel_chair import (
    PanelChairOutcome,
    run_panel_chair_iteration,
)

REVISION = "rev-chair-1"
MASTER_V1 = "# 1. Excellence\n\nKALHAS drives the architecture.\n"
MASTER_V2 = (
    "# 1. Excellence\n\nKALHAS drives the architecture.\n\n"
    "## Panel resolution\n\nThe high finding is addressed with evidence.\n"
)


class ChairDriver(BaseDriver):
    """Scripted driver: reviewers answer verdicts; ASTRA answers revised."""

    driver_id = "scripted-chair"

    def __init__(self, *, role_value: str, answer: str,
                 consensus_answer: str | None = None,
                 barrier: threading.Barrier | None = None) -> None:
        self.role_value = role_value
        self._answer = answer
        self._consensus_answer = consensus_answer
        self._barrier = barrier
        self.prompts: list[str] = []

    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name="Scripted Chair Driver",
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
        return DriverSession(driver_id=self.driver_id, role=request.role)

    def resume_session(self, session_id: str, request: SessionRequest) -> DriverSession:
        raise NotImplementedError

    def send_prompt(self, session: DriverSession, prompt: str) -> PromptHandle:
        if self._barrier is not None:
            self._barrier.wait(timeout=10)
        self.prompts.append(prompt)
        return PromptHandle(session=session, prompt=prompt)

    def wait_for_completion(self, handle: PromptHandle, timeout_s=None) -> PromptResult:
        text = self._consensus_answer if (self._consensus_answer is not None and "PANEL CONSENSUS" in self.prompts[-1]) else self._answer
        return PromptResult(ok=True, text=text, session_id="s")


def review_answer(role_value: str, proposal_hash: str, verdict: str = "NEEDS_REVISION",
                  severity: str = "high") -> str:
    body = {
        "reviewer_role": role_value,
        "iteration_number": 1,
        "proposal_hash": proposal_hash,
        "verdict": verdict,
        "summary": f"{role_value} position",
        "findings": [
            {
                "severity": severity,
                "category": "missing_evidence",
                "section": "1. Excellence",
                "message": f"{role_value} flagged evidence",
                "evidence": "state it",
                "source_refs": [],
                "suggested_change": "add the evidence",
            }
        ]
        if verdict == "NEEDS_REVISION"
        else [],
        "proposed_patches": [],
        "unverified_claims": [],
    }
    return (
        "<<<ENCOMM_PROPOSAL_REVIEW_START>>>\n"
        + json.dumps(body)
        + "\n<<<ENCOMM_PROPOSAL_REVIEW_END>>>"
    )


def consensus_answer(role_value: str, proposal_hash: str, j1: str) -> str:
    body = {
        "role": role_value,
        "iteration_number": 1,
        "proposal_hash": proposal_hash,
        "judgements": [
            {
                "item_id": "F001",
                "judgement": j1,
                "rationale": "assessed",
                "proposed_resolution": "add evidence" if j1 != "DISAGREE" else "drop it",
                "source_refs": [],
                "blocks_acceptance": j1 == "DISAGREE",
            }
        ],
        "readiness_assessment": {
            "criteria": [
                {"criterion": "scientific_coherence", "score": 80, "rationale": ""},
                {"criterion": "impact_coherence", "score": 70, "rationale": ""},
            ]
        },
        "summary": f"{role_value} consensus summary",
    }
    return (
        "<<<ENCOMM_PANEL_CONSENSUS_START>>>\n"
        + json.dumps(body)
        + "\n<<<ENCOMM_PANEL_CONSENSUS_END>>>"
    )


def astra_answer(input_hash: str) -> str:
    body = {
        "role": "ORCHESTRATOR",
        "iteration_number": 1,
        "input_proposal_hash": input_hash,
        "summary": "panel synthesis applied",
        "revised_proposal": MASTER_V2,
    }
    return (
        "<<<ENCOMM_PROPOSAL_INTEGRATION_START>>>\n"
        + json.dumps(body)
        + "\n<<<ENCOMM_PROPOSAL_INTEGRATION_END>>>"
    )


@pytest.fixture()
def chair_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ProposalWorkspace(ws).initialize()
    (ws / "03_PROPOSAL/MASTER_PROPOSAL.md").write_bytes(MASTER_V1.encode("utf-8"))
    return ws


def _drivers(ws: Path, barrier: threading.Barrier | None = None):
    proposal_hash = proposal_fingerprint(ws / "03_PROPOSAL/MASTER_PROPOSAL.md")
    astra = ChairDriver(
        role_value="ORCHESTRATOR", answer=astra_answer(proposal_hash)
    )
    drivers = {
        ProposalRole.SCIENTIFIC_REVIEWER: ChairDriver(
            role_value="SCIENTIFIC_REVIEWER",
            answer=review_answer("SCIENTIFIC_REVIEWER", proposal_hash),
            consensus_answer=consensus_answer(
                "SCIENTIFIC_REVIEWER", proposal_hash, "AGREE"
            ),
            barrier=barrier,
        ),
        ProposalRole.PROPOSAL_ENGINEER: ChairDriver(
            role_value="PROPOSAL_ENGINEER",
            answer=review_answer("PROPOSAL_ENGINEER", proposal_hash, severity="medium"),
            consensus_answer=consensus_answer(
                "PROPOSAL_ENGINEER", proposal_hash, "AGREE"
            ),
            barrier=barrier,
        ),
        ProposalRole.RED_TEAM_REVIEWER: ChairDriver(
            role_value="RED_TEAM_REVIEWER",
            answer=review_answer("RED_TEAM_REVIEWER", proposal_hash, severity="medium"),
            consensus_answer=consensus_answer(
                "RED_TEAM_REVIEWER", proposal_hash, "AGREE"
            ),
            barrier=barrier,
        ),
    }
    return drivers, astra


class TestPanelChair:
    def test_01_full_chain_changed_proposal_handoff(self, chair_workspace):
        ws = chair_workspace
        machine = ProposalStateMachine()
        drivers, astra = _drivers(ws)
        report = run_panel_chair_iteration(
            workspace=ws,
            state_machine=machine,
            iteration_number=1,
            proposal_revision=REVISION,
            reviewer_drivers=drivers,
            orchestrator_driver=astra,
        )
        assert report.outcome is PanelChairOutcome.READY_FOR_NEXT_ITERATION, report.error
        assert report.model_calls_used == 7  # 3 + 3 + 1 (brief §28)
        assert machine.phase is ProposalPhase.REVISION_REQUIRED
        # The ASTRA packet carried the CONSENSUS + readiness context.
        assert any("PANEL CHAIR CONTEXT" in p for p in astra.prompts)
        assert any("F001" in p and "unresolved=false" in p for p in astra.prompts)
        assert any("INTERNAL READINESS" in p for p in astra.prompts)
        # Durable artifacts landed.
        assert (ws / "04_REVIEWS/iteration_001/panel_docket.json").is_file()
        assert (ws / "04_REVIEWS/iteration_001/panel_consensus.json").is_file()
        assert (ws / "04_REVIEWS/iteration_001/readiness.json").is_file()
        readiness = json.loads(
            (ws / "04_REVIEWS/iteration_001/readiness.json").read_text(encoding="utf-8")
        )
        assert readiness["disclaimer"] == "INTERNAL READINESS — NOT AN EIC SCORE"
        assert report.readiness is not None
        # Master was rewritten by the runtime (ASTRA is the sole editor).
        assert "Panel resolution" in (ws / "03_PROPOSAL/MASTER_PROPOSAL.md").read_text(encoding="utf-8")
        # Handoff written for iteration 2.
        assert report.next_iteration_handoff_path

    def test_02_consensus_failure_fails_closed(self, chair_workspace):
        ws = chair_workspace
        machine = ProposalStateMachine()
        drivers, astra = _drivers(ws)
        drivers[ProposalRole.RED_TEAM_REVIEWER] = ChairDriver(
            role_value="RED_TEAM_REVIEWER",
            answer=review_answer(
                "RED_TEAM_REVIEWER",
                proposal_fingerprint(ws / "03_PROPOSAL/MASTER_PROPOSAL.md"),
            ),
            consensus_answer="garbage without envelope",
        )
        report = run_panel_chair_iteration(
            workspace=ws,
            state_machine=machine,
            iteration_number=1,
            proposal_revision=REVISION,
            reviewer_drivers=drivers,
            orchestrator_driver=astra,
        )
        assert report.outcome is PanelChairOutcome.CONSENSUS_FAILED
        # ASTRA was never contacted after the consensus failure.
        assert len(astra.prompts) == 0
        # No matrix/readiness artifacts persisted.
        assert not (ws / "04_REVIEWS/iteration_001/panel_consensus.json").exists()

    def test_03_clean_pass_bypass_counts_four_calls(self, chair_workspace):
        ws = chair_workspace
        proposal_hash = proposal_fingerprint(ws / "03_PROPOSAL/MASTER_PROPOSAL.md")
        machine = ProposalStateMachine()
        astra = ChairDriver(role_value="ORCHESTRATOR", answer="never called")
        drivers = {
            ProposalRole.SCIENTIFIC_REVIEWER: ChairDriver(
                role_value="SCIENTIFIC_REVIEWER",
                answer=review_answer("SCIENTIFIC_REVIEWER", proposal_hash, verdict="PASS"),
                consensus_answer=consensus_answer("SCIENTIFIC_REVIEWER", proposal_hash, "AGREE"),
            ),
            ProposalRole.PROPOSAL_ENGINEER: ChairDriver(
                role_value="PROPOSAL_ENGINEER",
                answer=review_answer("PROPOSAL_ENGINEER", proposal_hash, verdict="PASS"),
                consensus_answer=consensus_answer("PROPOSAL_ENGINEER", proposal_hash, "AGREE"),
            ),
            ProposalRole.RED_TEAM_REVIEWER: ChairDriver(
                role_value="RED_TEAM_REVIEWER",
                answer=review_answer("RED_TEAM_REVIEWER", proposal_hash, verdict="PASS"),
                consensus_answer=consensus_answer("RED_TEAM_REVIEWER", proposal_hash, "AGREE"),
            ),
        }
        report = run_panel_chair_iteration(
            workspace=ws,
            state_machine=machine,
            iteration_number=1,
            proposal_revision=REVISION,
            reviewer_drivers=drivers,
            orchestrator_driver=astra,
        )
        assert report.outcome is PanelChairOutcome.READY_FOR_HARD_GATES, report.error
        assert report.model_calls_used == 6  # 3 + 3 + 0 (zero-AI bypass)
        assert len(astra.prompts) == 0
        assert machine.phase is ProposalPhase.HARD_GATE_VALIDATION
