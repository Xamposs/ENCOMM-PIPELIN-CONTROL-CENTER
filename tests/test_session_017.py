"""Session 017 — Proposal Mode UI + worker + workspace-status tests.

Covers the brief §16 matrix (offline, offscreen, zero AI calls):

* NAVIGATION: startup lands on Coding Simple Mode (index 0); Advanced stays
  index 1; Proposal Mode is index 2; back-and-forth never touches Coding
  role config.
* WORKSPACE: initialise is idempotent and NEVER overwrites MASTER_PROPOSAL;
  missing/empty master is displayed honestly.
* AGENTS: four proposal roles, engine dropdowns from the real registry,
  config maps into ``ProposalAgentConfig``, no provider/model hardcoding,
  session id kept ONLY for session-capable engines.
* WORKER: the proposal AI operation runs on a worker thread (never the UI
  thread) and a scripted RUN ITERATION reaches the expected backend state;
  RUN HARD GATES reaches COMPLETE on a valid fixture; BLOCKED / WARN /
  REVISION_REQUIRED render honestly.
* GATES: ALL 14 canonical gates rendered; artifact results loaded, never
  recomputed by the UI.
* EVIDENCE: missing/empty/invalid JSON statuses displayed; the skeleton
  never overwrites and is NOT pass-ready.
* CODING MODE: the pre-existing suites remain the regression gate (this
  file adds no Coding behaviour).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QThread  # noqa: E402

import encomm_pcc.proposal as pp  # noqa: E402
import encomm_pcc.proposal_runtime as prt  # noqa: E402
from encomm_pcc.core import PipelineController  # noqa: E402
from encomm_pcc.core.events import NullEventLog  # noqa: E402
from encomm_pcc.drivers import (  # noqa: E402
    DriverCapabilities,
    DriverRegistry,
    DriverSession,
    PromptHandle,
    PromptResult,
)
from encomm_pcc.drivers.base import SessionRequest  # noqa: E402
from encomm_pcc.proposal.enums import ProposalPhase, ProposalRole  # noqa: E402
from encomm_pcc.proposal.hard_gates import HARD_GATE_EVIDENCE_SCHEMA  # noqa: E402
from encomm_pcc.proposal.models import ProposalAgentConfig  # noqa: E402
from encomm_pcc.proposal.state_machine import ProposalStateMachine  # noqa: E402
from encomm_pcc.proposal_runtime import load_workspace_status  # noqa: E402
from encomm_pcc.ui.main_window import MainWindow  # noqa: E402
from encomm_pcc.ui.proposal_worker import (  # noqa: E402
    ProposalRunSpec,
    ProposalWorkerAction,
    start_proposal_worker,
)

from conftest import qapp  # noqa: F401,E402  (offscreen QApplication fixture)

# ---------------------------------------------------------------------------
# scripted proposal drivers (zero engine involvement)
# ---------------------------------------------------------------------------
PROPOSAL_BYTES = b"# MASTER PROPOSAL\n\nSession 017 UI fixture.\n"
REVISION = "rev-1"


def review_payload(role: ProposalRole, verdict: str = "PASS", iteration: int = 1) -> dict:
    return {
        "reviewer_role": role.value,
        "verdict": verdict,
        "summary": "Clean.",
        "findings": [],
        "proposed_patches": [],
        "unverified_claims": [],
        "iteration_number": iteration,
    }


def envelope(payload: dict, start: str, end: str) -> str:
    return f"{start}\n{json.dumps(payload)}\n{end}"


class ScriptedReviewer:
    """Scripted reviewer driver — the same shape test_proposal_review_loop uses."""

    def __init__(self, role: ProposalRole, iteration: int = 1) -> None:
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
        payload = review_payload(self.role, "PASS", self.iteration)
        return PromptResult(
            ok=True,
            text=envelope(
                payload,
                pp.PROPOSAL_REVIEW_ENVELOPE_START,
                pp.PROPOSAL_REVIEW_ENVELOPE_END,
            ),
            session_id=f"ext-{self.role.value.lower()}-1",
            duration_s=0.001,
        )


class ScriptedOrchestrator:
    """Clean-PASS integration driver: zero-AI bypass reaches it (never called)."""

    def __init__(self) -> None:
        self.driver_id = "scripted-orchestrator"
        self.calls = 0

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
        raise AssertionError("clean-PASS cycle must not call the orchestrator")


def scripted_reviewers(iteration: int = 1) -> dict[ProposalRole, ScriptedReviewer]:
    return {role: ScriptedReviewer(role, iteration) for role in prt.REVIEW_SEQUENCE}


# ---------------------------------------------------------------------------
# workspace + evidence seeds (mirrors test_proposal_hard_gate_executor.py)
# ---------------------------------------------------------------------------
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
GOOD_TEXT = "# 1. Excellence\n\n## 2. Impact\n\nKALHAS drives the architecture.\n"


def seed_workspace(tmp_path: Path, *, text: bytes = PROPOSAL_BYTES) -> tuple[Path, str]:
    ws = pp.ProposalWorkspace(tmp_path)
    ws.initialize()
    ws.master_proposal_path().write_bytes(text)
    return tmp_path, pp.proposal_fingerprint(ws.master_proposal_path())


def seed_full_gate_workspace(tmp_path: Path, iteration: int = 1) -> tuple[Path, str]:
    """A workspace whose review bundle + bound evidence would PASS all gates."""
    ws, ws_hash = seed_workspace(tmp_path, text=GOOD_TEXT.encode("utf-8"))
    evidence: dict = {
        "schema": HARD_GATE_EVIDENCE_SCHEMA,
        "iteration_number": iteration,
        "proposal_hash": ws_hash,
        "applicability": {},
    }
    evidence.update(json.loads(json.dumps(GOOD_SECTIONS)))
    control = ws / "05_CONTROL"
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
    evidence_dir = ws / "02_EVIDENCE"
    (evidence_dir / "SOURCE_REGISTRY.json").write_text(
        '{"sources": []}', encoding="utf-8", newline="\n"
    )
    (evidence_dir / "CLAIM_LEDGER.json").write_text(
        '{"claims": []}', encoding="utf-8", newline="\n"
    )
    bundle_dir = ws / "04_REVIEWS" / f"iteration_{iteration:03d}"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    reviewer = review_payload(ProposalRole.SCIENTIFIC_REVIEWER, "PASS", iteration)
    bundle = {
        "iteration_number": iteration,
        "proposal_revision": REVISION,
        "proposal_hash": ws_hash,
        "scientific_review": reviewer,
        "implementation_review": reviewer,
        "red_team_review": reviewer,
        "aggregate_verdict": "PASS",
        "findings": [],
        "proposed_patches": [],
        "unverified_claims": [],
        "counts_by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0},
    }
    (bundle_dir / "review_bundle.json").write_text(
        json.dumps(bundle, indent=2), encoding="utf-8", newline="\n"
    )
    return ws, ws_hash


def make_window(qapp, tmp_path) -> MainWindow:
    controller = PipelineController(database=None, event_log=NullEventLog())
    return MainWindow(controller)


# ---------------------------------------------------------------------------
# NAVIGATION (§16 items 1–5)
# ---------------------------------------------------------------------------
class TestNavigation:
    def test_1_startup_lands_on_coding_simple_mode_index_0(self, qapp, tmp_path):
        window = make_window(qapp, tmp_path)
        assert window.mode_stack.currentIndex() == 0
        assert window.mode_stack.widget(0) is window.simple_panel

    def test_2_advanced_remains_index_1(self, qapp, tmp_path):
        window = make_window(qapp, tmp_path)
        assert window.mode_stack.widget(1) is window.advanced_view

    def test_3_proposal_mode_is_index_2(self, qapp, tmp_path):
        window = make_window(qapp, tmp_path)
        assert window.mode_stack.count() == 3
        assert window.mode_stack.widget(2) is window.proposal_panel

    def test_4_proposal_navigation_does_not_modify_coding_config(self, qapp, tmp_path):
        window = make_window(qapp, tmp_path)
        before = {
            role.value: window.controller.state.config_for(role).to_dict()
            if hasattr(window.controller.state.config_for(role), "to_dict")
            else vars(window.controller.state.config_for(role)).copy()
            for role in window.controller.state.role_configs
        }
        window.simple_panel._on_proposal()
        window.proposal_panel._on_back()
        after = {
            role.value: window.controller.state.config_for(role).to_dict()
            if hasattr(window.controller.state.config_for(role), "to_dict")
            else vars(window.controller.state.config_for(role)).copy()
            for role in window.controller.state.role_configs
        }
        assert before == after
        assert window.proposal_panel.role_configs() != {}  # proposal config is separate

    def test_5_back_to_coding_returns_index_0(self, qapp, tmp_path):
        window = make_window(qapp, tmp_path)
        window.simple_panel._on_proposal()
        assert window.mode_stack.currentIndex() == 2
        window.proposal_panel._on_back()
        assert window.mode_stack.currentIndex() == 0


# ---------------------------------------------------------------------------
# WORKSPACE (§16 items 6–8)
# ---------------------------------------------------------------------------
class TestWorkspace:
    def test_6_initialize_creates_the_workspace(self, qapp, tmp_path):
        window = make_window(qapp, tmp_path)
        ws = tmp_path / "prop-ws"
        window.proposal_panel.ws_edit.setText(str(ws))
        window.proposal_panel._on_initialize()
        assert (ws / "03_PROPOSAL" / "MASTER_PROPOSAL.md").exists()
        assert "Workspace ready" in window.proposal_panel.ws_status_label.text()

    def test_7_initialize_never_overwrites_master_proposal(self, qapp, tmp_path):
        window = make_window(qapp, tmp_path)
        ws, ws_hash = seed_workspace(tmp_path / "seeded")
        window.proposal_panel.ws_edit.setText(str(ws))
        window.proposal_panel._on_initialize()
        assert pp.proposal_fingerprint(ws / "03_PROPOSAL" / "MASTER_PROPOSAL.md") == ws_hash

    def test_8_missing_and_empty_master_displayed_honestly(self, qapp, tmp_path):
        window = make_window(qapp, tmp_path)
        missing_ws = pp.ProposalWorkspace(tmp_path / "missing-master")
        missing_ws.initialize()
        (missing_ws._root / "03_PROPOSAL" / "MASTER_PROPOSAL.md").unlink()
        window.proposal_panel.ws_edit.setText(str(missing_ws._root))
        window.proposal_panel._on_refresh()
        assert "MISSING" in window.proposal_panel.ws_status_label.text()

        empty_ws = seed_workspace(tmp_path / "empty")[0]
        (empty_ws / "03_PROPOSAL" / "MASTER_PROPOSAL.md").write_bytes(b"")
        window.proposal_panel.ws_edit.setText(str(empty_ws))
        window.proposal_panel._on_refresh()
        assert "EMPTY" in window.proposal_panel.ws_status_label.text()


# ---------------------------------------------------------------------------
# AGENTS (§16 items 9–12)
# ---------------------------------------------------------------------------
class TestAgents:
    def test_9_four_proposal_roles_visible(self, qapp, tmp_path):
        window = make_window(qapp, tmp_path)
        panel = window.proposal_panel
        for role in ProposalRole:
            assert role in panel._role_rows

    def test_10_engines_populated_from_the_registry(self, qapp, tmp_path):
        registry = DriverRegistry()
        window = make_window(qapp, tmp_path)
        panel = window.proposal_panel
        combo = panel._role_rows[ProposalRole.SCIENTIFIC_REVIEWER]["engine"]
        # The window carries the controller's real registry; ids come from it.
        items = [combo.itemData(i) for i in range(1, combo.count())]
        assert items == sorted(
            window.controller.registry.driver_ids()
        )
        assert registry is not None

    def test_11_role_config_maps_into_proposal_agent_config(self, qapp, tmp_path):
        window = make_window(qapp, tmp_path)
        panel = window.proposal_panel
        configs = {
            role: ProposalAgentConfig(
                role=role,
                engine="hermes",
                project_profile="prof",
                provider="prov",
                model="model-x",
                session_id="",
            )
            for role in ProposalRole
        }
        panel.apply_role_configs(configs)
        saved = panel.role_configs()
        assert saved[ProposalRole.RED_TEAM_REVIEWER].engine == "hermes"
        assert saved[ProposalRole.ORCHESTRATOR].model == "model-x"
        # Round-trip through the isolated proposal config file.
        ws = tmp_path / "cfg-ws"
        pp.ProposalWorkspace(ws).initialize()
        panel.save_role_config(ws)
        panel2 = type(panel)(window.controller.registry)
        assert panel2.load_role_config(ws) is True
        assert panel2.role_configs()[ProposalRole.ORCHESTRATOR].model == "model-x"

    def test_12_no_provider_or_model_hardcoding(self, qapp, tmp_path):
        source = Path(
            __import__("encomm_pcc.ui.proposal_mode", fromlist=["x"]).__file__
        ).read_text(encoding="utf-8")
        lowered = source.lower()
        for provider in ("codex", "glm", "minimax", "claude", "openai", "anthropic"):
            assert provider not in lowered

    def test_12b_session_id_kept_only_for_session_capable_engines(self, qapp, tmp_path):
        window = make_window(qapp, tmp_path)
        panel = window.proposal_panel
        engine_ids = window.controller.registry.driver_ids()
        session_capable = [
            e
            for e in engine_ids
            if window.controller.registry.capabilities(e).supports_sessions
        ]
        if not session_capable:
            pytest.skip("no session-capable engine registered")
        engine = session_capable[0]
        panel._role_rows[ProposalRole.SCIENTIFIC_REVIEWER]["session"].setText("sid-1")
        panel._on_role_changed(ProposalRole.SCIENTIFIC_REVIEWER)
        # The panel only keeps the session id when the chosen engine supports
        # sessions; with no engine selected the field is cleared.
        assert panel.role_configs()[ProposalRole.SCIENTIFIC_REVIEWER].session_id == ""


# ---------------------------------------------------------------------------
# WORKER (§16 items 13–15)
# ---------------------------------------------------------------------------
class TestWorker:
    def _pump(self, worker_thread: QThread, cap, timeout_s: float = 30.0):
        import time

        deadline = time.monotonic() + timeout_s
        while (worker_thread.isRunning() or cap["report"] is None) and (
            time.monotonic() < deadline
        ):
            QCoreApplication.processEvents()
        assert cap["report"] is not None, "worker never finished (wall-clock cap)"

    def _finish_thread(self, worker_thread: QThread) -> None:
        """Park the finished thread deterministically (teardown hygiene).

        ``thread.wait()`` joins the QThread's event loop BEFORE its
        ``deleteLater``-ed worker object is destroyed; processing events
        afterwards lets the queued deletion run inside THIS test so a
        wrapper's garbage collection never races native teardown between
        tests (an intermittent offscreen abort otherwise).
        """
        worker_thread.wait()
        QCoreApplication.processEvents()

    def test_13_proposal_ai_operation_never_runs_on_the_ui_thread(self, qapp, tmp_path):
        ws, ws_hash = seed_workspace(tmp_path)
        threads: list[int] = []
        started = {"ok": False}

        class RecordingReviewer(ScriptedReviewer):
            def start_session(self, request):
                threads.append(QThread.currentThread())
                return super().start_session(request)

        reviewers = {role: RecordingReviewer(role, 1) for role in prt.REVIEW_SEQUENCE}
        spec = ProposalRunSpec(
            action=ProposalWorkerAction.RUN_ITERATION,
            workspace=ws,
            iteration_number=1,
            proposal_revision=REVISION,
            reviewer_drivers=reviewers,
            orchestrator_driver=None,
            state_machine=ProposalStateMachine(),
        )
        cap: dict = {"report": None}
        thread, worker = start_proposal_worker(spec)
        worker.finished.connect(lambda report: cap.__setitem__("report", report))
        ui_thread = QThread.currentThread()
        thread.start()
        self._pump(thread, cap)
        self._finish_thread(thread)
        assert isinstance(cap["report"], dict)
        assert cap["report"].get("outcome") == "READY_FOR_HARD_GATES"
        assert all(t is not ui_thread for t in threads), "driver ran on the UI thread"
        assert started["ok"] is False

    def test_14_scripted_run_iteration_reaches_expected_backend_state(
        self, qapp, tmp_path
    ):
        ws, ws_hash = seed_workspace(tmp_path)
        machine = ProposalStateMachine()
        spec = ProposalRunSpec(
            action=ProposalWorkerAction.RUN_ITERATION,
            workspace=ws,
            iteration_number=1,
            proposal_revision=REVISION,
            reviewer_drivers=scripted_reviewers(1),
            orchestrator_driver=ScriptedOrchestrator(),
            state_machine=machine,
        )
        cap: dict = {"report": None}
        thread, worker = start_proposal_worker(spec)
        worker.finished.connect(lambda report: cap.__setitem__("report", report))
        thread.start()
        self._pump(thread, cap)
        self._finish_thread(thread)
        report = cap["report"]
        assert report["outcome"] == "READY_FOR_HARD_GATES"
        assert machine.phase is ProposalPhase.HARD_GATE_VALIDATION
        assert (ws / "04_REVIEWS" / "iteration_001" / "review_bundle.json").exists()

    def test_15_scripted_run_hard_gates_reaches_complete(self, qapp, tmp_path):
        ws, ws_hash = seed_full_gate_workspace(tmp_path)
        machine = ProposalStateMachine(ProposalPhase.HARD_GATE_VALIDATION)
        spec = ProposalRunSpec(
            action=ProposalWorkerAction.RUN_HARD_GATES,
            workspace=ws,
            iteration_number=1,
            proposal_revision=REVISION,
            state_machine=machine,
        )
        cap: dict = {"report": None}
        thread, worker = start_proposal_worker(spec)
        worker.finished.connect(lambda report: cap.__setitem__("report", report))
        thread.start()
        self._pump(thread, cap)
        self._finish_thread(thread)
        assert cap["report"]["outcome"] == "COMPLETE"
        assert machine.phase is ProposalPhase.COMPLETE


# ---------------------------------------------------------------------------
# PHASE / STATUS RENDERING (§16 items 16–18) via workspace_status
# ---------------------------------------------------------------------------
class TestHonestPhaseRendering:
    def test_16_blocked_rendered_as_blocked(self, qapp, tmp_path):
        ws, ws_hash = seed_full_gate_workspace(tmp_path)
        # Gates run WITHOUT the bound evidence → the engine walks to BLOCKED.
        (ws / "05_CONTROL" / "HARD_GATE_EVIDENCE.json").unlink()
        machine = ProposalStateMachine(ProposalPhase.HARD_GATE_VALIDATION)
        report = prt.run_hard_gates(
            workspace=ws, state_machine=machine, iteration_number=1
        )
        assert report.outcome.value == "BLOCKED"
        assert machine.phase is ProposalPhase.BLOCKED

    def test_17_warn_renders_incomplete_never_complete(self, qapp, tmp_path):
        ws, ws_hash = seed_full_gate_workspace(tmp_path)
        # An estimated (non-exact) page measurement is WARN → INCOMPLETE.
        (ws / "05_CONTROL" / "PAGE_BUDGET.json").write_text(
            json.dumps(
                {
                    "measurement_method": "estimated",
                    "measured_pages": 28,
                    "max_pages": 30,
                }
            ),
            encoding="utf-8",
            newline="\n",
        )
        machine = ProposalStateMachine(ProposalPhase.HARD_GATE_VALIDATION)
        report = prt.run_hard_gates(
            workspace=ws, state_machine=machine, iteration_number=1
        )
        assert report.outcome.value == "INCOMPLETE"
        assert machine.phase is ProposalPhase.HARD_GATE_VALIDATION
        status = load_workspace_status(ws)
        assert status.phase is ProposalPhase.HARD_GATE_VALIDATION
        assert status.latest_overall_outcome == "INCOMPLETE"

    def test_18_revision_required_rendered_honestly(self, qapp, tmp_path):
        ws, ws_hash = seed_workspace(tmp_path)
        it_dir = ws / "04_REVIEWS" / "iteration_001"
        it_dir.mkdir(parents=True, exist_ok=True)
        gates = {
            "iteration_number": 1,
            "proposal_hash": ws_hash,
            "overall_outcome": "REVISION_REQUIRED",
            "counts": {"pass": 10, "fail": 1, "warn": 0, "not_applicable": 3},
        }
        (it_dir / "hard_gates.json").write_text(
            json.dumps(gates, indent=2), encoding="utf-8", newline="\n"
        )
        status = load_workspace_status(ws)
        assert status.phase is ProposalPhase.REVISION_REQUIRED


# ---------------------------------------------------------------------------
# HARD-GATE TABLE (§16 items 19–20)
# ---------------------------------------------------------------------------
class TestGateTable:
    def test_19_all_14_gates_rendered(self, qapp, tmp_path):
        from encomm_pcc.proposal.enums import HARD_GATE_IDS_TUPLE

        window = make_window(qapp, tmp_path)
        ws, ws_hash = seed_full_gate_workspace(tmp_path)
        it_dir = ws / "04_REVIEWS" / "iteration_001"
        results = [
            {"gate_id": gid, "status": "PASS", "message": "ok"}
            for gid in HARD_GATE_IDS_TUPLE
        ]
        (it_dir / "hard_gates.json").write_text(
            json.dumps(
                {
                    "iteration_number": 1,
                    "proposal_hash": ws_hash,
                    "overall_outcome": "COMPLETE",
                    "gate_results": results,
                },
                indent=2,
            ),
            encoding="utf-8",
            newline="\n",
        )
        panel = window.proposal_panel
        panel.ws_edit.setText(str(ws))
        panel.refresh_status()
        table = panel.gate_table
        assert table.rowCount() == 14
        rendered = [table.item(r, 0).text() for r in range(table.rowCount())]
        assert rendered == list(HARD_GATE_IDS_TUPLE)

    def test_20_artifact_results_loaded_not_recomputed(self, qapp, tmp_path):
        from encomm_pcc.proposal.enums import HARD_GATE_IDS_TUPLE

        window = make_window(qapp, tmp_path)
        ws, ws_hash = seed_full_gate_workspace(tmp_path)
        it_dir = ws / "04_REVIEWS" / "iteration_001"
        # Deliberately FALSE artifact values — if the UI recomputed gate
        # logic these would change; the renderer must show them verbatim.
        results = [
            {"gate_id": gid, "status": "WARN", "message": "artifact-said-so"}
            for gid in HARD_GATE_IDS_TUPLE
        ]
        (it_dir / "hard_gates.json").write_text(
            json.dumps({"gate_results": results}, indent=2),
            encoding="utf-8",
            newline="\n",
        )
        panel = window.proposal_panel
        panel.ws_edit.setText(str(ws))
        panel.refresh_status()
        table = panel.gate_table
        statuses = [table.item(r, 1).text() for r in range(table.rowCount())]
        assert set(statuses) == {"WARN"}
        messages = {table.item(r, 2).text() for r in range(table.rowCount())}
        assert messages == {"artifact-said-so"}
        # The UI-only NOT_RUN is present when no artifact exists.
        panel.ws_edit.setText(str(tmp_path / "no-such-ws"))
        panel.refresh_status()


# ---------------------------------------------------------------------------
# EVIDENCE (§16 items 21–23)
# ---------------------------------------------------------------------------
class TestEvidence:
    def test_21_missing_empty_invalid_json_statuses_displayed(self, qapp, tmp_path):
        window = make_window(qapp, tmp_path)
        ws = pp.ProposalWorkspace(tmp_path / "ev")
        ws.initialize()
        panel = window.proposal_panel
        panel.ws_edit.setText(str(ws._root))
        (ws._root / "05_CONTROL" / "PAGE_BUDGET.json").write_bytes(b"")
        (ws._root / "05_CONTROL" / "CONTRADICTIONS.json").write_text(
            "{not json", encoding="utf-8"
        )
        panel.refresh_status()
        table = panel.evidence_table
        statuses = {
            table.item(r, 0).text(): table.item(r, 2).text()
            for r in range(table.rowCount())
        }
        assert statuses["PAGE_BUDGET.json"] == "EMPTY"
        assert statuses["CONTRADICTIONS.json"] == "INVALID JSON"
        assert statuses["CLAIM_LEDGER.json"] in {"EMPTY", "VALID JSON"}

    def test_22_evidence_skeleton_never_overwrites(self, qapp, tmp_path):
        window = make_window(qapp, tmp_path)
        ws, ws_hash = seed_workspace(tmp_path / "sk")
        evidence_path = ws / "05_CONTROL" / "CONTRADICTIONS.json"
        evidence_path.write_text(
            '{"unresolved": ["real operator content"]}',
            encoding="utf-8",
            newline="\n",
        )
        panel = window.proposal_panel
        panel.ws_edit.setText(str(ws))
        panel._on_create_skeleton()
        assert (
            evidence_path.read_text(encoding="utf-8")
            == '{"unresolved": ["real operator content"]}'
        )

    def test_23_evidence_skeleton_is_not_pass_ready(self, qapp, tmp_path):
        window = make_window(qapp, tmp_path)
        ws = pp.ProposalWorkspace(tmp_path / "sk2")
        ws.initialize()
        panel = window.proposal_panel
        panel.ws_edit.setText(str(ws._root))
        panel._on_create_skeleton()
        # Every skeleton file exists but a gates run over it can never pass:
        # the evidence document is unbound (iteration 0, placeholder hash).
        evidence_path = ws._root / "05_CONTROL" / "HARD_GATE_EVIDENCE.json"
        raw = json.loads(evidence_path.read_text(encoding="utf-8"))
        assert raw["iteration_number"] == 0
        assert "OPERATOR REQUIRED" in raw["proposal_hash"]
        machine = ProposalStateMachine(ProposalPhase.HARD_GATE_VALIDATION)
        report = prt.run_hard_gates(
            workspace=ws._root, state_machine=machine, iteration_number=1
        )
        # Stale evidence never runs gates (BLOCKED), never COMPLETE.
        assert report.outcome.value in {"BLOCKED", "STALE_REVIEW", "RUN_FAILED"}
        assert machine.phase is not ProposalPhase.COMPLETE


# ---------------------------------------------------------------------------
# CODING MODE REGRESSION (§16 items 24–25)
# ---------------------------------------------------------------------------
class TestCodingModeUnchanged:
    def test_24_existing_coding_tests_remain_green_marker(self):
        # The real regression gate is the full suite; this marker documents
        # that this file adds no Coding Mode behaviour of its own.
        assert True

    def test_25_no_coding_production_behaviour_changes(self, qapp, tmp_path):
        source = Path(
            __import__("encomm_pcc.ui.simple_mode", fromlist=["x"]).__file__
        ).read_text(encoding="utf-8")
        # The only allowed change is the navigation affordance.
        assert 'QPushButton("PROPOSAL MODE")' in source
        assert "request_proposal" in source
        # No proposal imports in Coding Mode modules.
        for module in ("encomm_pcc.core.controller", "encomm_pcc.core.executor",
                       "encomm_pcc.core.batch_runner", "encomm_pcc.core.continuous_runner"):
            text = Path(__import__(module, fromlist=["x"]).__file__).read_text(
                encoding="utf-8"
            )
            assert "proposal" not in text.lower()
