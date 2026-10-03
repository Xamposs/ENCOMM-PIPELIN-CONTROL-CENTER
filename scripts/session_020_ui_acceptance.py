"""Session 020 — Proposal Factory V2 UI acceptance (OFFLINE, zero AI calls).

Drives the REAL production ProposalModePanel through the operator workflow
with scripted drivers only:

    workspace initialization
    → source imports (blueprint + template through the REAL importer)
    → configure scripted agents
    → GENERATE INITIAL PROPOSAL   (specialists in parallel + ASTRA)
    → RUN PANEL ITERATION         (3 evaluators → consensus → ASTRA chair)
    → rendered readiness / campaign state / consensus artifacts asserted

Ends with the literal line ``PROPOSAL V2 UI ACCEPTANCE PASSED`` — printed
ONLY after every assertion held.  The workspace is a throwaway temp dir.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtCore import QCoreApplication, QThread  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import encomm_pcc.proposal as pp  # noqa: E402
import encomm_pcc.proposal_runtime as prt  # noqa: E402
from encomm_pcc.core import PipelineController  # noqa: E402
from encomm_pcc.core.events import NullEventLog  # noqa: E402
from encomm_pcc.drivers import (  # noqa: E402
    DriverSession,
    PromptHandle,
    PromptResult,
)
from encomm_pcc.drivers.base import SessionRequest  # noqa: E402
from encomm_pcc.proposal.enums import ProposalRole  # noqa: E402
from encomm_pcc.proposal.models import ProposalAgentConfig  # noqa: E402
from encomm_pcc.proposal.panel_contracts import (  # noqa: E402
    PANEL_CONSENSUS_ENVELOPE_END,
    PANEL_CONSENSUS_ENVELOPE_START,
    READINESS_CRITERIA,
)
from encomm_pcc.proposal.readiness import READINESS_DISCLAIMER  # noqa: E402
from encomm_pcc.ui.main_window import MainWindow  # noqa: E402
from encomm_pcc.ui.proposal_worker import CampaignControl  # noqa: E402

REVIEWER_ROLES = (
    ProposalRole.SCIENTIFIC_REVIEWER,
    ProposalRole.PROPOSAL_ENGINEER,
    ProposalRole.RED_TEAM_REVIEWER,
)
BLUEPRINT_TEXT = (
    "# FICTIONAL BLUEPRINT\n\n"
    "Objective: build a solar-powered greenhouse for the fictional town "
    "of Auralia.\n\n## 1. Excellence\nGrounded fictional content.\n"
)
TEMPLATE_TEXT = "FICTIONAL TEMPLATE v1\n\n1. Excellence\n2. Impact\n"


def review_payload(role: ProposalRole, verdict: str, iteration: int, summary: str) -> dict:
    return {
        "reviewer_role": role.value,
        "verdict": verdict,
        "summary": summary,
        "findings": [
            {
                "severity": "medium",
                "category": "weak_wording",
                "section": "1",
                "message": f"{role.value}: tighten the wording (pass {iteration}).",
                "evidence": "",
                "source_refs": [],
                "suggested_change": "Reword for clarity.",
            }
        ],
        "proposed_patches": [],
        "unverified_claims": [],
        "iteration_number": iteration,
    }


def consensus_payload(role: ProposalRole, prompt: str, iteration: int) -> dict:
    import re

    hash_match = re.search(r"proposal_hash[^:]*:\s*([0-9a-f]{64})", prompt)
    proposal_hash = hash_match.group(1) if hash_match else "0" * 64
    item_ids = re.findall(r"^- (F\d+|P\d+)", prompt, flags=re.MULTILINE)
    return {
        "role": role.value,
        "iteration_number": iteration,
        "proposal_hash": proposal_hash,
        "judgements": [
            {
                "item_id": item_id,
                "judgement": "AGREE",
                "rationale": "Consistent with the sources.",
                "proposed_resolution": "",
                "source_refs": [],
                "blocks_acceptance": False,
            }
            for item_id in item_ids
        ],
        "readiness_assessment": {
            "criteria": [
                {"criterion": c, "score": 55, "rationale": "reasonable"}
                for c in READINESS_CRITERIA
            ]
        },
        "summary": "All docket items agreed.",
    }


class AcceptanceReviewer:
    """Scripted panel evaluator: first-pass + consensus, both strict."""

    def __init__(self, role: ProposalRole) -> None:
        self.role = role
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
        self.calls += 1
        if "PANEL CONSENSUS" in handle.prompt:
            payload = consensus_payload(
                self.role, handle.prompt, 1
            )
            text = (
                f"{PANEL_CONSENSUS_ENVELOPE_START}\n{json.dumps(payload)}\n"
                f"{PANEL_CONSENSUS_ENVELOPE_END}"
            )
        elif "INITIAL GENERATION" in handle.prompt:
            text = (
                "<<<INITIAL_CONTRIBUTION_START>>>\n"
                f"## {self.role.value} contribution\n\n"
                "Grounded fictional content for the greenhouse.\n"
                "<<<INITIAL_CONTRIBUTION_END>>>"
            )
        else:
            payload = review_payload(
                self.role, "PASS", 1, "Minor wording risks."
            )
            text = (
                f"{pp.PROPOSAL_REVIEW_ENVELOPE_START}\n{json.dumps(payload)}\n"
                f"{pp.PROPOSAL_REVIEW_ENVELOPE_END}"
            )
        return PromptResult(
            ok=True,
            text=text,
            session_id=f"ext-{self.role.value.lower()}-1",
            duration_s=0.001,
        )


class AcceptanceOrchestrator:
    """Scripted ASTRA: initial synthesis + chair integration."""

    def __init__(self, revision_text: str) -> None:
        self.driver_id = "scripted-orchestrator"
        self.calls = 0
        self.revision_text = revision_text
        self.seen_chair_context = False

    def start_session(self, request: SessionRequest) -> DriverSession:
        self.calls += 1
        return DriverSession(
            driver_id=self.driver_id,
            role=request.role,
            session_id="ext-orch-1",
            external=True,
        )

    def resume_session(self, session_id: str, request: SessionRequest) -> DriverSession:
        raise NotImplementedError

    def send_prompt(self, session: DriverSession, prompt: str) -> PromptHandle:
        self.calls += 1
        return PromptHandle(session=session, prompt=prompt)

    def wait_for_completion(self, handle: PromptHandle, timeout_s=None) -> PromptResult:
        prompt = handle.prompt
        if "ASTRA SYNTHESIS" in prompt:
            # The synthesis is re-parsed through the STRICT integration
            # parser: the payload must echo role/iteration/hash AND the
            # proposal must sit in the dedicated block pair.
            import re as _re

            m = _re.search(
                r"input_proposal_hash[^:]*:\s*([0-9a-f]{64})", prompt
            )
            input_hash = m.group(1) if m else "0" * 64
            body = (
                "# FICTIONAL PROPOSAL (AURALIA GREENHOUSE)\n\n"
                "## 1. Excellence\n\n"
                "Solar greenhouses cut heating emissions while feeding "
                "Auralia.\n\n"
                "## 2. Impact\n\n"
                "Lower heating emissions, year-round local produce, and "
                "skills for Auralia residents.\n"
            )
            payload = {
                "role": "ORCHESTRATOR",
                "iteration_number": 1,
                "input_proposal_hash": input_hash,
                "revised_proposal": body,
                "summary": "Initial synthesis.",
                "applied_items": [],
                "rejected_items": [],
                "unresolved_items": [],
            }
            text = (
                "<<<INITIAL_PROPOSAL_START>>>\n"
                + json.dumps(payload)
                + "\n<<<INITIAL_PROPOSAL_END>>>"
            )
            return PromptResult(
                ok=True, text=text, session_id="ext-orch-1", duration_s=0.001
            )

        # The chair/integration packet: echo the REAL input hash.
        self.seen_chair_context = "PANEL CHAIR CONTEXT" in prompt
        marker = (
            "input_proposal_hash (SHA-256 of the exact revision you are "
            "revising):"
        )
        idx = prompt.index(marker) + len(marker)
        hash_line = prompt[idx:].strip().split()[0].strip()
        payload = {
            "role": "ORCHESTRATOR",
            "iteration_number": 1,
            "input_proposal_hash": hash_line,
            "revised_proposal": self.revision_text,
            "summary": "Applied panel revisions.",
            "applied_items": [],
            "rejected_items": [],
            "unresolved_items": [],
        }
        text = (
            f"{pp.PROPOSAL_INTEGRATION_ENVELOPE_START}\n{json.dumps(payload)}\n"
            f"{pp.PROPOSAL_INTEGRATION_ENVELOPE_END}"
        )
        return PromptResult(
            ok=True, text=text, session_id="ext-orch-1", duration_s=0.001
        )


def step(name: str) -> None:
    print(f"[acceptance] {name}", flush=True)


def pump(panel, timeout_s: float = 60.0) -> dict:
    thread = panel._thread
    import time

    deadline = time.monotonic() + timeout_s
    while (
        (thread is not None and thread.isRunning()) or panel._last_report is None
    ) and (time.monotonic() < deadline):
        QCoreApplication.processEvents()
    assert panel._last_report is not None, "worker never finished"
    thread.wait()
    QCoreApplication.processEvents()
    return panel._last_report


def main() -> int:
    app = QApplication.instance() or QApplication(["s020-acceptance"])
    scratch = Path(tempfile.mkdtemp(prefix="s020-ui-acceptance-"))
    try:
        ws = scratch / "proposal-workspace"

        # -- 1. workspace initialization through the REAL panel ------------
        step("workspace initialization")
        controller = PipelineController(database=None, event_log=NullEventLog())
        window = MainWindow(controller)
        panel = window.proposal_panel
        panel.ws_edit.setText(str(ws))
        panel._on_initialize()
        assert (ws / "03_PROPOSAL" / "MASTER_PROPOSAL.md").is_file()
        assert "Workspace ready" in panel.ws_status_label.text()

        # -- 2. source imports through the REAL importer --------------------
        step("source imports (blueprint + template)")
        bp_src = scratch / "blueprint.md"
        bp_src.write_text(BLUEPRINT_TEXT, encoding="utf-8", newline="\n")
        tpl_src = scratch / "template.md"
        tpl_src.write_text(TEMPLATE_TEXT, encoding="utf-8", newline="\n")
        panel._run_import([str(bp_src)], "master_blueprint", "MASTER_BLUEPRINT.md")
        panel._run_import(
            [str(tpl_src)], "application_template", "APPLICATION_TEMPLATE.md"
        )
        assert "Objective" in (
            ws / "00_SOURCE_OF_TRUTH" / "MASTER_BLUEPRINT.md"
        ).read_text(encoding="utf-8")
        assert "1. Excellence" in (
            ws / "01_OFFICIAL" / "APPLICATION_TEMPLATE.md"
        ).read_text(encoding="utf-8")
        manifest = json.loads(
            (ws / "05_CONTROL" / "SOURCE_IMPORT_MANIFEST.json").read_text(
                encoding="utf-8"
            )
        )
        assert len(manifest["imports"]) == 2
        panel.refresh_status()
        assert "READY" in panel.blueprint_label.text()
        assert "READY" in panel.template_label.text()

        # -- 3. configure scripted agents -----------------------------------
        step("configure scripted agents")
        reviewers = {role: AcceptanceReviewer(role) for role in REVIEWER_ROLES}
        orchestrator = AcceptanceOrchestrator(
            "# FICTIONAL PROPOSAL (AURALIA GREENHOUSE)\n\n"
            "## 1. Excellence\n\nRevised after the panel round.\n"
        )
        panel._build_drivers = lambda: (reviewers, orchestrator)
        configs = {
            role: ProposalAgentConfig(
                role=role,
                engine="hermes",
                project_profile=f"{role.value.lower()}-profile",
                provider="scripted",
                model="scripted-1",
            )
            for role in ProposalRole
        }
        panel.apply_role_configs(configs)
        panel._apply_phase_gating(ws)

        # -- 4. GENERATE INITIAL PROPOSAL ------------------------------------
        step("GENERATE INITIAL PROPOSAL")
        assert panel.generate_button.isEnabled()
        panel._on_generate_initial()
        report = pump(panel)
        assert report["outcome"] == "COMPLETED", report
        master = ws / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
        master_text = master.read_text(encoding="utf-8")
        assert "1. Excellence" in master_text and "2. Impact" in master_text
        gen_dir = ws / "04_REVIEWS" / "initial_generation"
        assert (gen_dir / "initial_generation.json").is_file()
        panel.refresh_status()
        assert "MASTER PROPOSAL: READY" in panel.master_label.text()

        # -- 5. RUN PANEL ITERATION ------------------------------------------
        step("RUN PANEL ITERATION (panel path, never sequential)")
        panel.refresh_status()
        assert panel.run_panel_button.isEnabled()
        panel._on_run_panel()
        report = pump(panel)
        assert report["outcome"] in {
            "READY_FOR_HARD_GATES",
            "READY_FOR_NEXT_ITERATION",
        }, report
        it_dir = ws / "04_REVIEWS" / "iteration_001"
        docket = json.loads((it_dir / "panel_docket.json").read_text("utf-8"))
        assert len(docket["items"]) == 3  # one finding per evaluator
        consensus = json.loads(
            (it_dir / "panel_consensus.json").read_text("utf-8")
        )
        assert consensus["schema"] == "encomm-pcc.panel-consensus-matrix/v1"
        assert len(consensus["rows"]) == 3
        assert (it_dir / "readiness.json").is_file()
        # Per evaluator: generation (start+wait) + first pass + consensus = 6.
        assert all(r.calls == 6 for r in reviewers.values())
        # The chair: synthesis + integration = 2 start_session contacts.
        assert orchestrator.calls >= 2

        # -- 6. rendered state ------------------------------------------------
        step("rendered readiness / campaign state / consensus")
        panel.refresh_status()
        assert not panel.readiness_disclaimer_label.isHidden()
        assert panel.readiness_disclaimer_label.text() == READINESS_DISCLAIMER
        assert panel.consensus_table.rowCount() == 3
        assert "Unresolved disagreements:" in panel.consensus_summary_label.text()
        readiness = json.loads(
            (it_dir / "readiness.json").read_text("utf-8")
        )
        assert readiness["disclaimer"] == READINESS_DISCLAIMER
        status = prt.load_workspace_status(ws)
        assert status.latest_iteration == 1
        assert status.phase.value in {"HARD_GATE_VALIDATION", "REVISION_REQUIRED"}

        # -- 7. zero-AI restart refresh ---------------------------------------
        step("restart refresh performs zero model calls")
        calls_before = sum(r.calls for r in reviewers.values()) + orchestrator.calls
        panel.ws_edit.setText("")  # force a full re-recovery on re-select
        panel.ws_edit.setText(str(ws))
        panel.refresh_status()
        calls_after = sum(r.calls for r in reviewers.values()) + orchestrator.calls
        assert calls_after == calls_before, "restart refresh contacted a driver"
        assert panel.gate_table.rowCount() == 14
        assert not panel._running_action

        window.close()
        app.processEvents()
        print("PROPOSAL V2 UI ACCEPTANCE PASSED")
        return 0
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
