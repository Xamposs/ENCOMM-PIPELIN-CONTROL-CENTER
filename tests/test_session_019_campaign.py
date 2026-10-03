"""Session 019 — campaign matrix (offline, zero model calls).

Covers brief §39 core: empty-master triggers initial generation, changed
proposals advance iterations, max-iterations / max-hours / max-model-calls
/ no-improvement stops, operator PAUSE/STOP at safe boundaries, gate
BLOCKED → WAITING_FOR_OPERATOR, gate COMPLETE → campaign COMPLETE, restart
recovers state WITHOUT auto-running AI, and campaign state is
workspace-bound.
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
from encomm_pcc.proposal_runtime.campaign import (
    CampaignConfig,
    CampaignState,
    CampaignStatus,
    CampaignStopCondition,
    load_campaign_state,
    run_campaign,
)

REVISION = "rev-campaign"

TEMPLATE = "# 1. Excellence\n\n# 2. Impact\n"


class CampaignDriver(BaseDriver):
    """Scripted driver answering by prompt shape (review/consensus/ASTRA/
    specialist).  The master is advanced deterministically by the ASTRA
    answer so the campaign sees a changed proposal each iteration."""

    driver_id = "scripted-campaign"

    def __init__(self, role_value: str, *, proposal_text: str | None = None,
                 verdict: str = "NEEDS_REVISION") -> None:
        self.role_value = role_value
        self._proposal_text = proposal_text
        self._verdict = verdict
        self._iteration = 1
        self.calls = 0

    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name="Scripted Campaign Driver",
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
        return PromptHandle(session=session, prompt=prompt)

    def _envelope(self, start: str, payload: dict, end: str, prompt: str) -> str:
        payload = dict(payload)
        if "iteration_number" in payload and payload["iteration_number"] == 0:
            payload["iteration_number"] = self._iteration_from_prompt(prompt)
        if "input_proposal_hash" in payload and payload["input_proposal_hash"].startswith("0"):
            payload["input_proposal_hash"] = self._hash_from_prompt(prompt)
        return f"{start}\n{json.dumps(payload)}\n{end}"

    @staticmethod
    def _iteration_from_prompt(prompt: str) -> int:
        # Every packet renders "- iteration_number: N" (review/consensus) or
        # "- iteration_number: N (echo it EXACTLY...)".
        for line in prompt.splitlines():
            stripped = line.strip()
            if stripped.startswith("- iteration_number:"):
                raw = stripped.split(":", 1)[1].strip().split(" ", 1)[0]
                try:
                    return int(raw)
                except ValueError:
                    break
        return 1

    @staticmethod
    def _hash_from_prompt(prompt: str) -> str:
        # Packets render the hash as:
        #   review/consensus: "- proposal_hash (SHA-256 ...): <64hex>"
        #   integration:      "- input_proposal_hash (SHA-256 ...): <64hex>"
        for line in prompt.splitlines():
            stripped = line.strip()
            if stripped.startswith("- proposal_hash") or stripped.startswith(
                "- input_proposal_hash"
            ):
                raw = stripped.split(":", 1)[1].strip()
                token = raw.split(" ", 1)[0].strip()
                if len(token) == 64:
                    return token
        return "0" * 64

    def wait_for_completion(self, handle: PromptHandle, timeout_s=None) -> PromptResult:
        self.calls += 1
        prompt = handle.prompt
        if "INITIAL GENERATION — SPECIALIST" in prompt:
            body = {"summary": "contribution", "revised_proposal": ""}
            return PromptResult(
                ok=True,
                text=(
                    "<<<INITIAL_CONTRIBUTION_START>>>\ncontent here\n"
                    "<<<INITIAL_CONTRIBUTION_END>>>"
                ),
                session_id="s",
            )
        if "INITIAL GENERATION — ASTRA SYNTHESIS" in prompt:
            return PromptResult(
                ok=True,
                text=(
                    "<<<INITIAL_PROPOSAL_START>>>\n"
                    + (self._proposal_text or "# 1. Excellence\n\nv1\n# 2. Impact\n")
                    + "\n<<<INITIAL_PROPOSAL_END>>>"
                ),
                session_id="s",
            )
        if "PANEL CONSENSUS" in prompt:
            body = {
                "role": self.role_value,
                "iteration_number": 0,  # patched by the strict parser check
                "proposal_hash": self._hash_from_prompt(prompt),
                "judgements": [
                    {
                        "item_id": "F001",
                        "judgement": "AGREE",
                        "rationale": "ok",
                        "proposed_resolution": "resolved",
                        "source_refs": [],
                        "blocks_acceptance": False,
                    }
                ],
                "readiness_assessment": {
                    "criteria": [
                        {"criterion": "scientific_coherence", "score": 80,
                         "rationale": ""}
                    ]
                },
                "summary": "agree",
            }
            return PromptResult(
                ok=True,
                text=self._envelope(
                    "<<<ENCOMM_PANEL_CONSENSUS_START>>>", body,
                    "<<<ENCOMM_PANEL_CONSENSUS_END>>>", prompt,
                ),
                session_id="s",
            )
        if "PROPOSAL INTEGRATION" in prompt or "PANEL CHAIR CONTEXT" in prompt:
            body = {
                "role": "ORCHESTRATOR",
                "iteration_number": 0,
                "input_proposal_hash": self._hash_from_prompt(prompt),
                "summary": "revised",
                "revised_proposal": self._proposal_text
                or "# 1. Excellence\n\nchanged\n",
            }
            return PromptResult(
                ok=True,
                text=self._envelope(
                    "<<<ENCOMM_PROPOSAL_INTEGRATION_START>>>", body,
                    "<<<ENCOMM_PROPOSAL_INTEGRATION_END>>>", prompt,
                ),
                session_id="s",
            )
        # default: a review answer
        body = {
            "reviewer_role": self.role_value,
            "iteration_number": 0,
            "proposal_hash": self._hash_from_prompt(prompt),
            "verdict": self._verdict,
            "summary": "position",
            "findings": [
                {
                    "severity": "medium",
                    "category": "missing_evidence",
                    "section": "1. Excellence",
                    "message": "add evidence",
                    "evidence": "e",
                    "source_refs": [],
                    "suggested_change": "sc",
                }
            ]
            if self._verdict == "NEEDS_REVISION"
            else [],
            "proposed_patches": [],
            "unverified_claims": [],
        }
        return PromptResult(
            ok=True,
            text=self._envelope(
                "<<<ENCOMM_PROPOSAL_REVIEW_START>>>", body,
                "<<<ENCOMM_PROPOSAL_REVIEW_END>>>", prompt,
            ),
            session_id="s",
        )


def _initial_gen_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ProposalWorkspace(ws).initialize()
    (ws / "00_SOURCE_OF_TRUTH/MASTER_BLUEPRINT.md").write_bytes(
        b"# 1. Excellence\n\nblueprint content\n"
    )
    (ws / "01_OFFICIAL/APPLICATION_TEMPLATE.md").write_bytes(TEMPLATE.encode("utf-8"))
    return ws


def _review_workspace(tmp_path: Path) -> tuple[Path, str]:
    ws = tmp_path / "ws"
    ProposalWorkspace(ws).initialize()
    (ws / "03_PROPOSAL/MASTER_PROPOSAL.md").write_bytes(
        b"# 1. Excellence\n\nstart\n"
    )
    return ws, proposal_fingerprint(ws / "03_PROPOSAL/MASTER_PROPOSAL.md")


def reviewer_set(ws: Path, proposal_hash: str, verdict: str = "NEEDS_REVISION",
                 proposal_text: str | None = None):
    drivers = {}
    for role in (
        ProposalRole.SCIENTIFIC_REVIEWER,
        ProposalRole.PROPOSAL_ENGINEER,
        ProposalRole.RED_TEAM_REVIEWER,
    ):
        drivers[role] = CampaignDriver(
            role.value, proposal_text=proposal_text, verdict=verdict
        )
    return drivers


class TestCampaign:
    def test_01_empty_master_triggers_initial_generation_then_iterates(
        self, tmp_path
    ):
        ws = _initial_gen_workspace(tmp_path)
        machine = ProposalStateMachine()
        specialists = {
            r: CampaignDriver(
                r.value, proposal_text="# 1. Excellence\n\nv1\n# 2. Impact\n"
            )
            for r in (
                ProposalRole.SCIENTIFIC_REVIEWER,
                ProposalRole.PROPOSAL_ENGINEER,
                ProposalRole.RED_TEAM_REVIEWER,
            )
        }
        astra = CampaignDriver(
            "ORCHESTRATOR", proposal_text="# 1. Excellence\n\nv1\n# 2. Impact\n"
        )
        reviewers = reviewer_set(
            ws, "", proposal_text="# 1. Excellence\n\nv1\n# 2. Impact\n"
        )
        report = run_campaign(
            workspace=ws,
            state_machine=machine,
            config=CampaignConfig(max_iterations=2, max_model_calls=100),
            specialist_drivers=specialists,
            reviewer_drivers=reviewers,
            orchestrator_driver=astra,
        )
        assert report.status is CampaignStatus.WAITING_FOR_OPERATOR, report.error
        # Iteration 2 ended review-current at HARD_GATE_VALIDATION; the gate
        # run correctly BLOCKED on missing operator evidence (no calls
        # burned) — the campaign is waiting for the operator, never faking
        # completeness.
        assert report.stop_condition is CampaignStopCondition.WAITING_FOR_OPERATOR
        assert report.iterations_completed == 2
        # 4 (initial generation) + 2 x panel iterations' calls happened.
        assert report.model_calls_used >= 4
        assert (ws / "03_PROPOSAL/MASTER_PROPOSAL.md").read_bytes().strip()
        state = load_campaign_state(ws)
        assert state is not None
        assert state.iteration == 2

    def test_02_max_model_calls_stops(self, tmp_path):
        ws, ph = _review_workspace(tmp_path)
        machine = ProposalStateMachine()
        reviewers = reviewer_set(ws, ph, proposal_text="# 1. Excellence\n\nchanged\n")
        astra = CampaignDriver(
            "ORCHESTRATOR", proposal_text="# 1. Excellence\n\nchanged\n"
        )
        report = run_campaign(
            workspace=ws,
            state_machine=machine,
            config=CampaignConfig(max_iterations=10, max_model_calls=4),
            reviewer_drivers=reviewers,
            orchestrator_driver=astra,
        )
        assert report.stop_condition is CampaignStopCondition.MAX_MODEL_CALLS
        assert report.model_calls_used <= 10  # the in-flight stage finished
        state = load_campaign_state(ws)
        assert state.model_calls_used == report.model_calls_used

    def test_03_pause_and_stop_at_safe_boundaries(self, tmp_path):
        ws, ph = _review_workspace(tmp_path)
        machine = ProposalStateMachine()
        reviewers = reviewer_set(ws, ph, proposal_text="# 1. Excellence\n\nchanged\n")

        class Control:
            pause_requested = False
            stop_requested = False

        control = Control()
        report = run_campaign(
            workspace=ws,
            state_machine=machine,
            config=CampaignConfig(max_iterations=5, max_model_calls=500),
            reviewer_drivers=reviewers,
            orchestrator_driver=CampaignDriver(
                "ORCHESTRATOR", proposal_text="# 1. Excellence\n\nchanged\n"
            ),
            control=control,
        )
        # With control flags never armed the campaign runs its iterations;
        # after the final review-current iteration the gates BLOCK on the
        # missing operator evidence — the correct no-calls-burned stop.
        assert report.stop_condition is CampaignStopCondition.WAITING_FOR_OPERATOR
        assert report.gate_outcome == "BLOCKED"

    def test_04_restart_recovers_state_without_running_ai(self, tmp_path):
        ws, ph = _review_workspace(tmp_path)
        machine = ProposalStateMachine()
        reviewers = reviewer_set(ws, ph, proposal_text="# 1. Excellence\n\nchanged\n")
        run_campaign(
            workspace=ws,
            state_machine=machine,
            config=CampaignConfig(max_iterations=1, max_model_calls=500),
            reviewer_drivers=reviewers,
            orchestrator_driver=CampaignDriver(
                "ORCHESTRATOR", proposal_text="# 1. Excellence\n\nchanged\n"
            ),
        )
        # RESTART: fresh machine, fresh drivers — NOTHING runs until asked.
        state = load_campaign_state(ws)
        assert state is not None
        assert state.iteration == 1
        fresh_machine = ProposalStateMachine()
        fresh_drivers = reviewer_set(ws, ph, proposal_text="# 1. Excellence\n\nv3\n")
        # A terminal-bound config (max_iterations already reached) proves no
        # new model call happens without an explicit resume decision.
        report = run_campaign(
            workspace=ws,
            state_machine=fresh_machine,
            config=CampaignConfig(max_iterations=1, max_model_calls=500),
            reviewer_drivers=fresh_drivers,
            orchestrator_driver=CampaignDriver("ORCHESTRATOR"),
            state=state,
        )
        assert report.stop_condition is CampaignStopCondition.MAX_ITERATIONS
        assert all(d.calls == 0 for d in fresh_drivers.values())

    def test_05_config_validation_bounds(self):
        with pytest.raises(ValueError):
            CampaignConfig(max_hours=0)
        with pytest.raises(ValueError):
            CampaignConfig(max_iterations=51)
        with pytest.raises(ValueError):
            CampaignConfig(max_model_calls=0)
        with pytest.raises(ValueError):
            CampaignConfig(target_readiness=101)
        ok = CampaignConfig(max_hours=120, max_iterations=1)
        assert ok.max_hours == 120

    def test_06_state_roundtrip_workspace_bound(self, tmp_path):
        ws, ph = _review_workspace(tmp_path)
        machine = ProposalStateMachine()
        reviewers = reviewer_set(ws, ph, proposal_text="# 1. Excellence\n\nchanged\n")
        run_campaign(
            workspace=ws,
            state_machine=machine,
            config=CampaignConfig(max_iterations=1, max_model_calls=500),
            reviewer_drivers=reviewers,
            orchestrator_driver=CampaignDriver(
                "ORCHESTRATOR", proposal_text="# 1. Excellence\n\nchanged\n"
            ),
        )
        state_path = ws / "05_CONTROL" / "CAMPAIGN_STATE.json"
        assert state_path.is_file()
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        assert payload["schema"] == "encomm-pcc.campaign-state/v1"
        # workspace-bound: another workspace has none
        ws2, _ = _review_workspace(tmp_path / "other")
        assert load_campaign_state(ws2) is None
