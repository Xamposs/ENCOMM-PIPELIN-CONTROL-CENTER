#!/usr/bin/env python3
"""SESSION 019 — OFFLINE PANEL CAMPAIGN ACCEPTANCE (brief §40).

Fully OFFLINE and SCRIPTED: real production Proposal runtime code (source
import → initial generation → parallel panel review → parallel consensus →
ASTRA chair revision → second iteration with rising readiness →
deterministic hard gates → COMPLETE).  Only the model/driver outputs are
scripted.  A TEMPORARY proposal workspace is used; the PCC repository is
never modified.

Run::

    python scripts/session_019_panel_acceptance.py

Expected final line:  PANEL CAMPAIGN ACCEPTANCE PASSED
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
from encomm_pcc.proposal.hard_gates import HARD_GATE_EVIDENCE_SCHEMA  # noqa: E402
from encomm_pcc.proposal.source_import import import_source  # noqa: E402
from encomm_pcc.proposal.state_machine import ProposalStateMachine  # noqa: E402
from encomm_pcc.proposal.workspace import ProposalWorkspace  # noqa: E402
from encomm_pcc.proposal_runtime import run_hard_gates  # noqa: E402
from encomm_pcc.proposal_runtime.initial_generation import (  # noqa: E402
    InitialGenerationOutcome,
    run_initial_generation,
)
from encomm_pcc.proposal_runtime.panel_chair import (  # noqa: E402
    PanelChairOutcome,
    run_panel_chair_iteration,
)

REVISION = "rev-accept-019"
TEMPLATE = "# 1. Excellence\n\n# 2. Impact\n"
PROPOSAL_V2 = (
    "# 1. Excellence\n\nKALHAS drives the architecture with evidence.\n\n"
    "# 2. Impact\n\nWide uptake, quantified.\n"
)

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


class ScriptedDriver(BaseDriver):
    """One scripted driver answering by prompt shape — the ONLY scripted part."""

    driver_id = "scripted-s019"

    def __init__(self, role_value: str, *, proposal_text: str = PROPOSAL_V2,
                 verdict: str = "NEEDS_REVISION", severity: str = "high",
                 first_judgement: str = "AGREE") -> None:
        self.role_value = role_value
        self._proposal_text = proposal_text
        self._verdict = verdict
        self._severity = severity
        self._first_judgement = first_judgement
        self.review_calls = 0
        self.consensus_calls = 0
        self.astra_calls = 0
        self.specialist_calls = 0
        # Readiness rubric scores reflect the verdict: a clean-PASS proposal
        # is scored higher by the same panel (deterministic per driver).
        base = 88 if verdict == "PASS" else 78
        self._readiness_scores = [
            base + 2, base - 4, base + 1,  # sci, impact, evidence anchors
        ]

    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name="Scripted S019 Driver",
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

    def _env(self, start: str, payload: dict, end: str) -> str:
        return f"{start}\n{json.dumps(payload)}\n{end}"

    def wait_for_completion(self, handle: PromptHandle, timeout_s=None) -> PromptResult:
        prompt = handle.prompt
        if "INITIAL GENERATION — SPECIALIST" in prompt:
            self.specialist_calls += 1
            return PromptResult(
                ok=True,
                text=(
                    "<<<INITIAL_CONTRIBUTION_START>>>\n"
                    f"{self.role_value} contribution grounded in the blueprint.\n"
                    "<<<INITIAL_CONTRIBUTION_END>>>"
                ),
                session_id="s",
            )
        if "INITIAL GENERATION — ASTRA SYNTHESIS" in prompt:
            self.astra_calls += 1
            return PromptResult(
                ok=True,
                text=(
                    "<<<INITIAL_PROPOSAL_START>>>\n"
                    "# 1. Excellence\n\nInitial KALHAS draft.\n\n# 2. Impact\n\n"
                    "[INPUT REQUIRED: uptake evidence]\n"
                    "<<<INITIAL_PROPOSAL_END>>>"
                ),
                session_id="s",
            )
        if "PANEL CONSENSUS" in prompt:
            self.consensus_calls += 1
            body = {
                "role": self.role_value,
                "iteration_number": int(self._meta(prompt, "iteration_number") or 1),
                "proposal_hash": self._meta(prompt, "proposal_hash"),
                "judgements": [
                    {
                        "item_id": "F001",
                        "judgement": self._first_judgement,
                        "rationale": "assessed against sources",
                        "proposed_resolution": "add the evidence",
                        "source_refs": [],
                        "blocks_acceptance": False,
                    }
                ],
                "readiness_assessment": {
                    "criteria": [
                        {"criterion": "scientific_coherence",
                         "score": self._readiness_scores[0], "rationale": ""},
                        {"criterion": "impact_coherence",
                         "score": self._readiness_scores[1], "rationale": ""},
                        {"criterion": "evidence_completeness",
                         "score": self._readiness_scores[2], "rationale": ""},
                    ]
                },
                "summary": f"{self.role_value} consensus position",
            }
            return PromptResult(
                ok=True,
                text=self._env(
                    "<<<ENCOMM_PANEL_CONSENSUS_START>>>", body,
                    "<<<ENCOMM_PANEL_CONSENSUS_END>>>",
                ),
                session_id="s",
            )
        if "PROPOSAL INTEGRATION REQUEST" in prompt:
            self.astra_calls += 1
            body = {
                "role": "ORCHESTRATOR",
                "iteration_number": int(self._meta(prompt, "iteration_number") or 1),
                "input_proposal_hash": self._meta(prompt, "input_proposal_hash"),
                "summary": "panel synthesis applied; evidence added",
                "revised_proposal": PROPOSAL_V2,
            }
            return PromptResult(
                ok=True,
                text=self._env(
                    "<<<ENCOMM_PROPOSAL_INTEGRATION_START>>>", body,
                    "<<<ENCOMM_PROPOSAL_INTEGRATION_END>>>",
                ),
                session_id="s",
            )
        # default: independent review
        self.review_calls += 1
        findings = (
            [
                {
                    "severity": self._severity,
                    "category": "missing_evidence",
                    "section": "1. Excellence",
                    "message": f"{self.role_value}: claim needs evidence",
                    "evidence": "state the source",
                    "source_refs": [],
                    "suggested_change": "add evidence",
                }
            ]
            if self._verdict == "NEEDS_REVISION"
            else []
        )
        body = {
            "reviewer_role": self.role_value,
            "iteration_number": int(self._meta(prompt, "iteration_number") or 1),
            "proposal_hash": self._meta(prompt, "proposal_hash"),
            "verdict": self._verdict,
            "summary": f"{self.role_value} review position",
            "findings": findings,
            "proposed_patches": [],
            "unverified_claims": [],
        }
        return PromptResult(
            ok=True,
            text=self._env(
                "<<<ENCOMM_PROPOSAL_REVIEW_START>>>", body,
                "<<<ENCOMM_PROPOSAL_REVIEW_END>>>",
            ),
            session_id="s",
        )


def step(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(f"ACCEPTANCE FAILED at: {name}")


def main() -> int:
    print("SESSION 019 — OFFLINE PANEL CAMPAIGN ACCEPTANCE (scripted drivers)")
    tmp = Path(tempfile.mkdtemp(prefix="s019-acceptance-"))
    try:
        workspace = tmp / "proposal-workspace"

        # -- 1. workspace + SOURCE IMPORTS -----------------------------------
        ProposalWorkspace(workspace).initialize()
        blueprint = tmp / "blueprint.md"
        blueprint.write_bytes(b"# Blueprint\n\nKALHAS evidence base.\n")
        record = import_source(workspace, blueprint, import_role="master_blueprint")
        template = tmp / "template.md"
        template.write_bytes(TEMPLATE.encode("utf-8"))
        import_source(workspace, template, import_role="application_template")
        step(
            "source import (blueprint + template)",
            record.extraction_status == "ok"
            and record.normalized_path == "00_SOURCE_OF_TRUTH/MASTER_BLUEPRINT.md",
            f"sha={record.sha256_original[:12]}",
        )

        # -- 2. INITIAL GENERATION (3 specialists parallel + ASTRA) -----------
        machine = ProposalStateMachine()
        specialists = {
            role: ScriptedDriver(role.value)
            for role in (
                ProposalRole.SCIENTIFIC_REVIEWER,
                ProposalRole.PROPOSAL_ENGINEER,
                ProposalRole.RED_TEAM_REVIEWER,
            )
        }
        astra = ScriptedDriver("ORCHESTRATOR")
        gen = run_initial_generation(
            workspace=workspace,
            state_machine=machine,
            proposal_revision=REVISION,
            specialist_drivers=specialists,
            orchestrator_driver=astra,
        )
        step(
            "initial generation (3 specialists → ASTRA synthesis)",
            gen.outcome is InitialGenerationOutcome.COMPLETED
            and machine.phase is ProposalPhase.IDLE,
            f"markers={gen.synthesis_report.get('input_required_markers')}",
        )

        # -- 3. ITERATION 1: panel + consensus + ASTRA chair ------------------
        reviewers = {
            role: ScriptedDriver(
                role.value, verdict="NEEDS_REVISION", severity="high"
            )
            for role in (
                ProposalRole.SCIENTIFIC_REVIEWER,
                ProposalRole.PROPOSAL_ENGINEER,
                ProposalRole.RED_TEAM_REVIEWER,
            )
        }
        it1 = run_panel_chair_iteration(
            workspace=workspace,
            state_machine=machine,
            iteration_number=1,
            proposal_revision=REVISION,
            reviewer_drivers=reviewers,
            orchestrator_driver=astra,
        )
        step(
            "iteration 1: parallel panel → consensus → ASTRA chair",
            it1.outcome is PanelChairOutcome.READY_FOR_NEXT_ITERATION
            and it1.model_calls_used == 7
            and machine.phase is ProposalPhase.REVISION_REQUIRED,
            f"calls={it1.model_calls_used}, readiness={it1.readiness['readiness'] if it1.readiness else None}",
        )
        it_dir = workspace / "04_REVIEWS" / "iteration_001"
        step(
            "durable panel artifacts (docket + consensus + readiness)",
            (it_dir / "panel_docket.json").is_file()
            and (it_dir / "panel_consensus.json").is_file()
            and (it_dir / "readiness.json").is_file(),
        )

        # -- 4. ITERATION 2: clean pass → gates reachable ----------------------
        clean = {
            role: ScriptedDriver(role.value, verdict="PASS")
            for role in (
                ProposalRole.SCIENTIFIC_REVIEWER,
                ProposalRole.PROPOSAL_ENGINEER,
                ProposalRole.RED_TEAM_REVIEWER,
            )
        }
        it2 = run_panel_chair_iteration(
            workspace=workspace,
            state_machine=machine,
            iteration_number=2,
            proposal_revision=f"{REVISION}-2",
            reviewer_drivers=clean,
            orchestrator_driver=astra,
        )
        step(
            "iteration 2: clean panel pass (zero-AI integration bypass)",
            it2.outcome is PanelChairOutcome.READY_FOR_HARD_GATES
            and machine.phase is ProposalPhase.HARD_GATE_VALIDATION
            and it2.model_calls_used == 6,
            f"calls={it2.model_calls_used}",
        )
        readiness_1 = it1.readiness["readiness"]
        readiness_2 = it2.readiness["readiness"]
        step(
            "advisory readiness present and labelled (NOT an EIC score)",
            readiness_2 is not None
            and readiness_2 > readiness_1,
            f"it1={readiness_1} → it2={readiness_2} (clean PASS, penalties dropped)",
        )

        # -- 5. operator evidence (harness = stand-in operator) ---------------
        current_hash = proposal_fingerprint(
            workspace / "03_PROPOSAL/MASTER_PROPOSAL.md"
        )
        evidence: dict = {
            "schema": HARD_GATE_EVIDENCE_SCHEMA,
            "iteration_number": 2,
            "proposal_hash": current_hash,
            "applicability": {},
        }
        evidence.update(json.loads(json.dumps(GOOD_SECTIONS)))
        control = workspace / "05_CONTROL"
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
                    "max_pages": 10,
                    "measurement_method": "harness-fixture",
                    "measured_pages": 2,
                },
                indent=2,
            ),
            encoding="utf-8",
            newline="\n",
        )
        (workspace / "02_EVIDENCE" / "SOURCE_REGISTRY.json").write_text(
            '{"sources": []}', encoding="utf-8", newline="\n"
        )
        (workspace / "02_EVIDENCE" / "CLAIM_LEDGER.json").write_text(
            '{"claims": []}', encoding="utf-8", newline="\n"
        )

        # -- 6. HARD GATES → COMPLETE -----------------------------------------
        gates = run_hard_gates(
            workspace=workspace,
            state_machine=machine,
            iteration_number=2,
        )
        step(
            "deterministic hard gates (14 gates, zero AI)",
            gates.outcome.value == "COMPLETE"
            and machine.phase is ProposalPhase.COMPLETE,
            f"outcome={gates.outcome.value}",
        )

        print()
        print("PANEL CAMPAIGN ACCEPTANCE PASSED")
        return 0
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
