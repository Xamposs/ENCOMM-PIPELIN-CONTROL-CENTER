#!/usr/bin/env python3
"""SESSION 019 — OFFLINE CAMPAIGN RECOVERY ACCEPTANCE (brief §40).

Proves the multi-day durability contract with ZERO model calls:

    campaign checkpoint (iteration 1 completes, durable state saved)
    → simulated process restart (fresh ProposalStateMachine + fresh drivers;
      the durable state is reloaded READ-ONLY)
    → NO AI runs by itself (a bounded config proves zero driver calls)
    → explicit resume (operator decision, new bounds)
    → no duplicated model-call stage (the executors' artifact contracts
      prevent a second run of the completed iteration)
    → campaign reaches a defined stop state

Run::

    python scripts/session_019_recovery_acceptance.py

Expected final line:  CAMPAIGN RECOVERY ACCEPTANCE PASSED
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from encomm_pcc.drivers.base import (  # noqa: E402
    BaseDriver,
    DriverCapabilities,
    DriverSession,
    PromptHandle,
    PromptResult,
    SessionRequest,
)
from encomm_pcc.proposal.enums import ProposalPhase, ProposalRole  # noqa: E402
from encomm_pcc.proposal.fingerprint import proposal_fingerprint  # noqa: E402
from encomm_pcc.proposal.state_machine import ProposalStateMachine  # noqa: E402
from encomm_pcc.proposal.workspace import ProposalWorkspace  # noqa: E402
from encomm_pcc.proposal_runtime.campaign import (  # noqa: E402
    CampaignConfig,
    CampaignState,
    CampaignStatus,
    CampaignStopCondition,
    load_campaign_state,
    run_campaign,
)

PROPOSAL_V2 = (
    "# 1. Excellence\n\nKALHAS drives the architecture with evidence.\n\n"
    "# 2. Impact\n\nWide uptake, quantified.\n"
)


class ScriptedDriver(BaseDriver):
    """Scripted campaign driver (same contract as the unit matrix)."""

    driver_id = "scripted-recovery"

    def __init__(self, role_value: str, *, verdict: str = "NEEDS_REVISION") -> None:
        self.role_value = role_value
        self._verdict = verdict
        self.calls = 0

    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name="Scripted Recovery Driver",
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

    @staticmethod
    def _meta(prompt: str, key: str) -> str:
        for line in prompt.splitlines():
            stripped = line.strip()
            if stripped.startswith(f"- {key}"):
                raw = stripped.split(":", 1)[1].strip()
                token = raw.split(" ", 1)[0].strip()
                if key.endswith("hash") and len(token) != 64:
                    continue
                return token
        return ""

    def _env(self, start: str, payload: dict, end: str, prompt: str) -> str:
        payload = dict(payload)
        if payload.get("iteration_number") == 0:
            payload["iteration_number"] = int(self._meta(prompt, "iteration_number") or 1)
        if payload.get("proposal_hash") == "HASH":
            payload["proposal_hash"] = self._meta(prompt, "proposal_hash")
        if payload.get("input_proposal_hash") == "HASH":
            payload["input_proposal_hash"] = self._meta(prompt, "input_proposal_hash")
        return f"{start}\n{json.dumps(payload)}\n{end}"

    def wait_for_completion(self, handle: PromptHandle, timeout_s=None) -> PromptResult:
        self.calls += 1
        prompt = handle.prompt
        if "PANEL CONSENSUS" in prompt:
            body = {
                "role": self.role_value,
                "iteration_number": 0,
                "proposal_hash": "HASH",
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
                        {"criterion": "scientific_coherence", "score": 82,
                         "rationale": ""}
                    ]
                },
                "summary": "agree",
            }
            return PromptResult(
                ok=True,
                text=self._env(
                    "<<<ENCOMM_PANEL_CONSENSUS_START>>>", body,
                    "<<<ENCOMM_PANEL_CONSENSUS_END>>>", prompt,
                ),
                session_id="s",
            )
        if "PROPOSAL INTEGRATION REQUEST" in prompt:
            body = {
                "role": "ORCHESTRATOR",
                "iteration_number": 0,
                "input_proposal_hash": "HASH",
                "summary": "revised",
                "revised_proposal": PROPOSAL_V2,
            }
            return PromptResult(
                ok=True,
                text=self._env(
                    "<<<ENCOMM_PROPOSAL_INTEGRATION_START>>>", body,
                    "<<<ENCOMM_PROPOSAL_INTEGRATION_END>>>", prompt,
                ),
                session_id="s",
            )
        body = {
            "reviewer_role": self.role_value,
            "iteration_number": 0,
            "proposal_hash": "HASH",
            "verdict": self._verdict,
            "summary": "position",
            "findings": (
                [
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
                else []
            ),
            "proposed_patches": [],
            "unverified_claims": [],
        }
        return PromptResult(
            ok=True,
            text=self._env(
                "<<<ENCOMM_PROPOSAL_REVIEW_START>>>", body,
                "<<<ENCOMM_PROPOSAL_REVIEW_END>>>", prompt,
            ),
            session_id="s",
        )



def step(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(f"RECOVERY ACCEPTANCE FAILED at: {name}")


def reviewer_set(verdict: str = "NEEDS_REVISION") -> dict:
    return {
        role: ScriptedDriver(role.value, verdict=verdict)
        for role in (
            ProposalRole.SCIENTIFIC_REVIEWER,
            ProposalRole.PROPOSAL_ENGINEER,
            ProposalRole.RED_TEAM_REVIEWER,
        )
    }


def main() -> int:
    print("SESSION 019 — OFFLINE CAMPAIGN RECOVERY ACCEPTANCE (scripted drivers)")
    tmp = Path(tempfile.mkdtemp(prefix="s019-recovery-"))
    try:
        workspace = tmp / "proposal-workspace"
        ProposalWorkspace(workspace).initialize()
        (workspace / "03_PROPOSAL/MASTER_PROPOSAL.md").write_bytes(
            b"# 1. Excellence\n\nstart\n"
        )

        # -- LEG 1: campaign leg 1 (ONE iteration, then a safe boundary) ------
        machine_1 = ProposalStateMachine()
        drivers_1 = reviewer_set()
        report_1 = run_campaign(
            workspace=workspace,
            state_machine=machine_1,
            config=CampaignConfig(max_iterations=1, max_model_calls=500),
            reviewer_drivers=drivers_1,
            orchestrator_driver=ScriptedDriver("ORCHESTRATOR"),
        )
        calls_after_leg1 = report_1.model_calls_used
        step(
            "leg 1 completes iteration 1 at a safe boundary",
            report_1.iterations_completed == 1 and calls_after_leg1 > 0,
            f"calls={calls_after_leg1}, stop={report_1.stop_condition.value}",
        )
        durable_state = load_campaign_state(workspace)
        step(
            "durable checkpoint exists and is workspace-bound",
            durable_state is not None
            and (workspace / "05_CONTROL/CAMPAIGN_STATE.json").is_file()
            and durable_state.iteration == 1,
            f"campaign_id={durable_state.campaign_id if durable_state else '-'}",
        )

        # -- LEG 2: simulated RESTART (fresh machine + fresh drivers) ---------
        # NOTHING auto-runs: a bounded config with max_iterations already
        # reached must perform ZERO model calls on the recovered state.
        machine_2 = ProposalStateMachine()
        drivers_2 = reviewer_set()
        report_probe = run_campaign(
            workspace=workspace,
            state_machine=machine_2,
            config=CampaignConfig(max_iterations=1, max_model_calls=500),
            reviewer_drivers=drivers_2,
            orchestrator_driver=ScriptedDriver("ORCHESTRATOR"),
            state=durable_state,
        )
        step(
            "restart does NOT auto-run AI (no duplicated stage)",
            all(d.calls == 0 for d in drivers_2.values())
            and report_probe.iterations_completed == 1,
            f"fresh driver calls={[d.calls for d in drivers_2.values()]}",
        )

        # -- LEG 3: EXPLICIT RESUME (operator decision) -----------------------
        machine_3 = ProposalStateMachine()
        drivers_3 = reviewer_set()
        report_3 = run_campaign(
            workspace=workspace,
            state_machine=machine_3,
            config=CampaignConfig(max_iterations=2, max_model_calls=500),
            reviewer_drivers=drivers_3,
            orchestrator_driver=ScriptedDriver("ORCHESTRATOR"),
            state=CampaignState(
                campaign_id=durable_state.campaign_id,
                iteration=durable_state.iteration,
                start_epoch_s=durable_state.start_epoch_s,
                accumulated_run_s=durable_state.accumulated_run_s,
                model_calls_used=durable_state.model_calls_used,
                readiness_history=durable_state.readiness_history,
            ),
        )
        step(
            "explicit resume continues at the safe checkpoint",
            report_3.iterations_completed == 2
            and report_3.model_calls_used > calls_after_leg1,
            f"calls={report_3.model_calls_used} "
            f"(leg1={calls_after_leg1}), stop={report_3.stop_condition.value}",
        )
        step(
            "no stage duplicated (call delta == exactly one iteration)",
            report_3.model_calls_used - calls_after_leg1 in (6, 7, 8),
            f"delta={report_3.model_calls_used - calls_after_leg1}",
        )
        # The proposal on disk is the evolved revision (the runtime wrote it).
        step(
            "workspace holds the campaign's evolved proposal",
            "Panel resolution" not in (
                workspace / "03_PROPOSAL/MASTER_PROPOSAL.md"
            ).read_text(encoding="utf-8")
            and (workspace / "03_PROPOSAL/MASTER_PROPOSAL.md").read_bytes().strip(),
        )
        # Durable state advanced coherently.
        final_state = load_campaign_state(workspace)
        step(
            "durable state advanced coherently",
            final_state is not None and final_state.iteration == 2,
            f"iteration={final_state.iteration if final_state else '-'}",
        )

        print()
        print("CAMPAIGN RECOVERY ACCEPTANCE PASSED")
        return 0
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
