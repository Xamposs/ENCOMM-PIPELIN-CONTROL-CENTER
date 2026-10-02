#!/usr/bin/env python3
"""SESSION 017 — OFFLINE PROPOSAL MODE END-TO-END ACCEPTANCE (brief §17).

Fully OFFLINE and SCRIPTED by default: real production Proposal runtime code
(``run_iteration`` → ``run_hard_gates``), real workspace/artifact contracts —
only the model/driver outputs are scripted.  A TEMPORARY proposal workspace
is created; the PCC repository itself is never modified.

Proven chain:

    workspace init
    → 3 scripted reviewers (SCIENTIFIC / PROPOSAL_ENGINEER / RED_TEAM)
    → deterministic aggregation + integration (clean-PASS zero-AI bypass)
    → READY_FOR_HARD_GATES
    → operator-authored evidence seeded by the ACCEPTANCE HARNESS
    → deterministic hard gates (14 canonical gates, zero AI)
    → ProposalPhase.COMPLETE

Run::

    python scripts/session_017_proposal_acceptance.py

Expected final line:  PROPOSAL ACCEPTANCE PASSED
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import encomm_pcc.proposal as pp  # noqa: E402
import encomm_pcc.proposal_runtime as prt  # noqa: E402
from encomm_pcc.drivers.base import (  # noqa: E402
    DriverSession,
    PromptHandle,
    PromptResult,
)
from encomm_pcc.drivers.base import SessionRequest  # noqa: E402
from encomm_pcc.proposal.enums import ProposalPhase, ProposalRole  # noqa: E402
from encomm_pcc.proposal.hard_gates import HARD_GATE_EVIDENCE_SCHEMA  # noqa: E402
from encomm_pcc.proposal.state_machine import ProposalStateMachine  # noqa: E402

REVISION = "rev-accept-1"
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


class ScriptedReviewer:
    """Scripted reviewer driver — the ONLY scripted part of this acceptance."""

    def __init__(self, role: ProposalRole, iteration: int) -> None:
        self.role = role
        self.iteration = iteration
        self.driver_id = f"scripted-{role.value.lower()}"
        self.calls = 0

    def start_session(self, request: SessionRequest) -> DriverSession:
        self.calls += 1
        return DriverSession(
            driver_id=self.driver_id,
            role=request.role,
            session_id=f"ext-{self.role.value.lower()}-1",
            external=True,
        )

    def resume_session(self, session_id: str, request: SessionRequest) -> DriverSession:
        raise NotImplementedError

    def send_prompt(self, session: DriverSession, prompt: str) -> PromptHandle:
        return PromptHandle(session=session, prompt=prompt)

    def wait_for_completion(self, handle: PromptHandle, timeout_s=None) -> PromptResult:
        payload = {
            "reviewer_role": self.role.value,
            "verdict": "PASS",
            "summary": "Acceptance: clean review.",
            "findings": [],
            "proposed_patches": [],
            "unverified_claims": [],
            "iteration_number": self.iteration,
        }
        text = (
            f"{pp.PROPOSAL_REVIEW_ENVELOPE_START}\n{json.dumps(payload)}\n"
            f"{pp.PROPOSAL_REVIEW_ENVELOPE_END}"
        )
        return PromptResult(
            ok=True, text=text, session_id=f"ext-{self.role.value.lower()}-1",
            duration_s=0.001,
        )


def step(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(f"ACCEPTANCE FAILED at: {name}")


def main() -> int:
    print("SESSION 017 — OFFLINE PROPOSAL ACCEPTANCE (scripted drivers)")
    tmp = Path(tempfile.mkdtemp(prefix="s017-acceptance-"))
    try:
        workspace = tmp / "proposal-workspace"

        # -- 1. workspace init ------------------------------------------------
        ws = pp.ProposalWorkspace(workspace)
        created = ws.initialize()
        master = ws.master_proposal_path()
        master.write_bytes(GOOD_TEXT.encode("utf-8"))
        step(
            "workspace init",
            master.is_file() and len(created) > 0,
            f"{len(created)} path(s) created",
        )

        # -- 2. ONE scripted iteration ---------------------------------------
        machine = ProposalStateMachine()
        reviewers = {role: ScriptedReviewer(role, 1) for role in prt.REVIEW_SEQUENCE}
        iteration_report = prt.run_iteration(
            workspace=workspace,
            state_machine=machine,
            iteration_number=1,
            proposal_revision=REVISION,
            reviewer_drivers=reviewers,
            orchestrator_driver=None,  # clean PASS ⇒ zero-AI integration bypass
        )
        step(
            "run_iteration (3 scripted reviewers → integration)",
            iteration_report.outcome.value == "READY_FOR_HARD_GATES",
            f"outcome={iteration_report.outcome.value}, "
            f"machine={machine.phase.value}",
        )
        assert machine.phase is ProposalPhase.HARD_GATE_VALIDATION
        it_dir = workspace / "04_REVIEWS" / "iteration_001"
        step(
            "durable review artifacts",
            (it_dir / "review_bundle.json").is_file()
            and (it_dir / "integration_result.json").is_file(),
        )

        # -- 3. operator-authored evidence (harness = stand-in operator) ------
        current_hash = pp.proposal_fingerprint(master)
        evidence: dict = {
            "schema": HARD_GATE_EVIDENCE_SCHEMA,
            "iteration_number": 1,
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
                    "measurement_method": "pdf-layout-exact",
                    "measured_pages": 28,
                    "max_pages": 30,
                }
            ),
            encoding="utf-8",
            newline="\n",
        )
        evidence_dir = workspace / "02_EVIDENCE"
        (evidence_dir / "SOURCE_REGISTRY.json").write_text(
            '{"sources": []}', encoding="utf-8", newline="\n"
        )
        (evidence_dir / "CLAIM_LEDGER.json").write_text(
            '{"claims": []}', encoding="utf-8", newline="\n"
        )
        step(
            "operator evidence authored + bound (iteration 1, exact hash)",
            True,
            f"hash={current_hash[:12]}…",
        )

        # -- 4. deterministic hard gates ---------------------------------------
        gates_report = prt.run_hard_gates(
            workspace=workspace,
            state_machine=machine,
            iteration_number=1,
        )
        step(
            "run_hard_gates (14 canonical gates, zero AI)",
            gates_report.outcome.value == "COMPLETE",
            f"outcome={gates_report.outcome.value}, "
            f"counts={gates_report.counts}",
        )
        step(
            "state machine terminal COMPLETE",
            machine.phase is ProposalPhase.COMPLETE,
        )

        # -- 5. the operator status loader agrees ------------------------------
        status = prt.load_workspace_status(workspace)
        step(
            "workspace recovery status reads COMPLETE",
            status.phase is ProposalPhase.COMPLETE
            and status.latest_overall_outcome == "COMPLETE"
            and not status.ambiguous,
        )

        print()
        print(f"scratch workspace: {tmp}")
        print("PROPOSAL ACCEPTANCE PASSED")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
