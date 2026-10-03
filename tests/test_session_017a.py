"""Session 017A — corrective live-wiring + deterministic recovery tests.

Brief matrices (§4 live wiring, §13 regression matrix), OFFLINE + offscreen,
zero AI calls:

* LIVE AGENT CONFIG: a recording scripted driver captures the EXACT
  ``SessionRequest`` each reviewer / the ORCHESTRATOR receives through the
  full production composition (``run_iteration`` and the UI worker);
  profile/provider/model are each role's OWN values and ``workspace_path``
  is the ACTUAL proposal workspace for all four roles.  On the pre-fix
  code the reviewer requests carry empty profile/provider/model, so the
  core assertions cannot hold by accident.
* RECOVERY: restart at HARD_GATE_VALIDATION reconstructs the machine at
  that phase; RUN HARD GATES genuinely runs from it; a workspace switch
  never reuses the old machine; NEXT_ITERATION.json recovery;
  spinner restoration; PROPOSAL_CONFIG.json auto-load (Coding Mode
  untouched); phase-aware button gating.
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
    DriverSession,
    PromptHandle,
    PromptResult,
)
from encomm_pcc.drivers.base import SessionRequest  # noqa: E402
from encomm_pcc.domain.enums import SessionPolicy  # noqa: E402
from encomm_pcc.proposal.enums import ProposalPhase, ProposalRole  # noqa: E402
from encomm_pcc.proposal.models import ProposalAgentConfig  # noqa: E402
from encomm_pcc.proposal.state_machine import ProposalStateMachine  # noqa: E402
from encomm_pcc.proposal_runtime.revision_handoff import (  # noqa: E402
    NEXT_ITERATION_FILENAME,
    REVISION_HANDOFF_SCHEMA,
)
from encomm_pcc.ui.main_window import MainWindow  # noqa: E402


from conftest import qapp  # noqa: F401,E402  (offscreen QApplication fixture)

# ---------------------------------------------------------------------------
# shared fixtures
# ---------------------------------------------------------------------------
PROPOSAL_BYTES = b"# MASTER PROPOSAL\n\nSession 017A fixture.\n"
REVISED_BYTES = b"# MASTER PROPOSAL\n\nSession 017A fixture (REVISED).\n"
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


def review_envelope(payload: dict) -> str:
    return (
        f"{pp.PROPOSAL_REVIEW_ENVELOPE_START}\n{json.dumps(payload)}\n"
        f"{pp.PROPOSAL_REVIEW_ENVELOPE_END}"
    )


class RecordingReviewer:
    """Scripted reviewer that RECORDS every SessionRequest it receives.

    ``needs_revision=True``: returns a strict NEEDS_REVISION answer with one
    actionable finding — the aggregate then demands REAL integration, so
    the ORCHESTRATOR SessionRequest path is exercised (a clean-PASS cycle
    legitimately bypasses the orchestrator with zero calls).
    """

    def __init__(self, role: ProposalRole, iteration: int = 1, needs_revision: bool = False) -> None:
        self.role = role
        self.iteration = iteration
        self.needs_revision = needs_revision
        self.driver_id = f"scripted-{role.value.lower()}"
        self.requests: list[SessionRequest] = []

    def start_session(self, request: SessionRequest) -> DriverSession:
        self.requests.append(request)
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
        verdict = "NEEDS_REVISION" if self.needs_revision else "PASS"
        payload = review_payload(self.role, verdict, self.iteration)
        if self.needs_revision:
            payload["findings"] = [
                {
                    "severity": "medium",
                    "category": "weak_wording",
                    "section": "3",
                    "message": "Tighten the wording.",
                    "evidence": "",
                    "source_refs": [],
                    "suggested_change": "Reword.",
                }
            ]
        return PromptResult(
            ok=True,
            text=review_envelope(payload),
            session_id=f"ext-{self.role.value.lower()}-1",
            duration_s=0.001,
        )


class RecordingOrchestrator:
    """Scripted ORCHESTRATOR that records its SessionRequest.

    ``revised_text`` non-empty: returns a strict integration envelope whose
    revised proposal DIFFERS from the input (a real changed-proposal run).
    ``None``: the clean-PASS zero-AI bypass reaches it and it must never be
    called.
    """

    def __init__(self, revised_text: str | None = None) -> None:
        self.driver_id = "scripted-orchestrator"
        self.requests: list[SessionRequest] = []
        self.calls = 0
        self.revised_text = revised_text

    def start_session(self, request: SessionRequest) -> DriverSession:
        self.requests.append(request)
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
        assert self.revised_text is not None, "unexpected orchestrator contact"
        packet = handle.prompt
        marker = "input_proposal_hash (SHA-256 of the exact revision you are revising):"
        idx = packet.index(marker) + len(marker)
        # The packet line ends with an "(echo it EXACTLY)" annotation; the
        # hash is the FIRST whitespace token after the marker.
        hash_line = packet[idx:].strip().split()[0].strip()
        payload = {
            "role": "ORCHESTRATOR",
            "iteration_number": 1,
            "input_proposal_hash": hash_line,
            "revised_proposal": self.revised_text,
            "summary": "Applied revisions.",
            "applied_items": [],
            "rejected_items": [],
            "unresolved_items": [],
        }
        return PromptResult(
            ok=True,
            text=(
                f"{pp.PROPOSAL_INTEGRATION_ENVELOPE_START}\n{json.dumps(payload)}\n"
                f"{pp.PROPOSAL_INTEGRATION_ENVELOPE_END}"
            ),
            session_id="ext-orch-1",
            duration_s=0.001,
        )


def configs_for(
    prefix: str,
    roles: tuple[ProposalRole, ...],
) -> dict[ProposalRole, ProposalAgentConfig]:
    return {
        role: ProposalAgentConfig(
            role=role,
            engine="hermes",
            project_profile=f"{prefix}-profile",
            provider=f"{prefix}-provider",
            model=f"{prefix}-model",
        )
        for role in roles
    }


REVIEWER_ROLES = (
    ProposalRole.SCIENTIFIC_REVIEWER,
    ProposalRole.PROPOSAL_ENGINEER,
    ProposalRole.RED_TEAM_REVIEWER,
)


def fingerprint_bytes(data: bytes) -> str:
    """Canonical SHA-256 over EXACT bytes (test helper; mirrors the
    D-057 ``proposal_fingerprint`` algorithm without a file)."""
    import hashlib

    return hashlib.sha256(data).hexdigest()


def seed_workspace(tmp_path: Path, *, text: bytes = PROPOSAL_BYTES) -> tuple[Path, str]:
    ws = pp.ProposalWorkspace(tmp_path)
    ws.initialize()
    ws.master_proposal_path().write_bytes(text)
    return tmp_path, pp.proposal_fingerprint(ws.master_proposal_path())


def run_iteration_with_configs(tmp_path: Path):
    """Full production composition with distinct per-role configs.

    The SCIENTIFIC reviewer answers NEEDS_REVISION so the aggregate brief
    demands REAL integration: the ORCHESTRATOR is genuinely called and its
    SessionRequest is captured.  Returns
    ``(ws, reviewers, orchestrator, report)``.
    """
    ws, ws_hash = seed_workspace(tmp_path)
    reviewers = {role: RecordingReviewer(role, 1) for role in REVIEWER_ROLES}
    reviewers[ProposalRole.SCIENTIFIC_REVIEWER].needs_revision = True
    revised_text = REVISED_BYTES.decode("utf-8")
    orchestrator = RecordingOrchestrator(revised_text=revised_text)
    machine = ProposalStateMachine()
    report = prt.run_iteration(
        workspace=ws,
        state_machine=machine,
        iteration_number=1,
        proposal_revision=REVISION,
        reviewer_drivers=reviewers,
        orchestrator_driver=orchestrator,
        reviewer_agent_configs=configs_for("science", (ProposalRole.SCIENTIFIC_REVIEWER,))
        | configs_for("engineer", (ProposalRole.PROPOSAL_ENGINEER,))
        | configs_for("redteam", (ProposalRole.RED_TEAM_REVIEWER,)),
        orchestrator_agent_config=configs_for("orch", (ProposalRole.ORCHESTRATOR,))[
            ProposalRole.ORCHESTRATOR
        ],
    )
    return ws, reviewers, orchestrator, report


# ---------------------------------------------------------------------------
# §13 items 1–7: LIVE AGENT CONFIG WIRING (through run_iteration)
# ---------------------------------------------------------------------------
class TestLiveAgentConfigWiring:
    def test_core_all_four_roles_receive_their_own_config(self, tmp_path):
        ws, reviewers, orchestrator, report = run_iteration_with_configs(tmp_path)
        assert report.outcome is prt.ProposalIterationOutcome.READY_FOR_NEXT_ITERATION
        sci = reviewers[ProposalRole.SCIENTIFIC_REVIEWER].requests[0]
        assert sci.project_profile == "science-profile"
        assert sci.provider == "science-provider"
        assert sci.model == "science-model"
        eng = reviewers[ProposalRole.PROPOSAL_ENGINEER].requests[0]
        assert eng.project_profile == "engineer-profile"
        assert eng.provider == "engineer-provider"
        assert eng.model == "engineer-model"
        red = reviewers[ProposalRole.RED_TEAM_REVIEWER].requests[0]
        assert red.project_profile == "redteam-profile"
        assert red.provider == "redteam-provider"
        assert red.model == "redteam-model"
        orch = orchestrator.requests[0]
        assert orch.project_profile == "orch-profile"
        assert orch.provider == "orch-provider"
        assert orch.model == "orch-model"

    def test_item4_reviewer_workspace_path_is_the_actual_workspace(self, tmp_path):
        ws, reviewers, _orchestrator, report = run_iteration_with_configs(tmp_path)
        assert report.outcome is prt.ProposalIterationOutcome.READY_FOR_NEXT_ITERATION
        for role in REVIEWER_ROLES:
            request = reviewers[role].requests[0]
            assert request.workspace_path == str(ws), (
                f"{role.value} SessionRequest.workspace_path must be the "
                "actual proposal workspace"
            )
            assert request.workspace_path != ""

    def test_item7_orchestrator_workspace_path_is_the_actual_workspace(self, tmp_path):
        ws, _reviewers, orchestrator, report = run_iteration_with_configs(tmp_path)
        assert report.outcome is prt.ProposalIterationOutcome.READY_FOR_NEXT_ITERATION
        assert orchestrator.requests[0].workspace_path == str(ws)

    def test_no_configs_keeps_the_legacy_request_shape(self, tmp_path):
        """Backward compatibility: offline callers without configs are
        preserved exactly (empty profile/provider/model, packet fallback)."""
        ws, _ws_hash = seed_workspace(tmp_path)
        reviewers = {role: RecordingReviewer(role, 1) for role in REVIEWER_ROLES}
        report = prt.run_iteration(
            workspace=ws,
            state_machine=ProposalStateMachine(),
            iteration_number=1,
            proposal_revision=REVISION,
            reviewer_drivers=reviewers,
            orchestrator_driver=None,
        )
        assert report.outcome is prt.ProposalIterationOutcome.READY_FOR_HARD_GATES
        sci = reviewers[ProposalRole.SCIENTIFIC_REVIEWER].requests[0]
        assert sci.project_profile == ""
        assert sci.provider == ""
        assert sci.model == ""
        # Session 017A: the workspace is now ALWAYS the actual proposal
        # workspace (never accidentally empty in production).
        assert sci.workspace_path == str(ws)


# ---------------------------------------------------------------------------
# §13 items 1–7 THROUGH THE UI WORKER (panel → spec → runtime → SessionRequest)
# ---------------------------------------------------------------------------
class TestWorkerConfigPropagation:
    def _pump(self, worker_thread: QThread, cap: dict, timeout_s: float = 30.0):
        import time

        deadline = time.monotonic() + timeout_s
        while (worker_thread.isRunning() or cap["report"] is None) and (
            time.monotonic() < deadline
        ):
            QCoreApplication.processEvents()
        assert cap["report"] is not None, "worker never finished (wall-clock cap)"

    def _finish_thread(self, worker_thread: QThread) -> None:
        worker_thread.wait()
        QCoreApplication.processEvents()

    def _pump_panel(self, panel, timeout_s: float = 30.0):
        """Drain the panel's OWN worker started by the production click
        path, park it inside the test, and return the report.  Relies on
        the panel's own finished-slot (connected before thread.start()),
        so the report can never be missed by a late test-side connect."""
        import time

        thread = panel._thread
        deadline = time.monotonic() + timeout_s
        while (
            thread is not None and thread.isRunning() or panel._last_report is None
        ) and (time.monotonic() < deadline):
            QCoreApplication.processEvents()
        assert panel._last_report is not None, "panel worker never finished"
        self._finish_thread(thread)
        return panel._last_report

    def test_worker_forwards_panel_configs_into_session_requests(
        self, qapp, tmp_path
    ):
        controller = PipelineController(database=None, event_log=NullEventLog())
        panel = controller  # placeholder never used; panel built below
        del panel
        window = MainWindow(controller)
        panel = window.proposal_panel
        ws, _ws_hash = seed_workspace(tmp_path)
        panel.ws_edit.setText(str(ws))
        configs = (
            configs_for("science", (ProposalRole.SCIENTIFIC_REVIEWER,))
            | configs_for("engineer", (ProposalRole.PROPOSAL_ENGINEER,))
            | configs_for("redteam", (ProposalRole.RED_TEAM_REVIEWER,))
            | configs_for("orch", (ProposalRole.ORCHESTRATOR,))
        )
        panel.apply_role_configs(configs)
        reviewers = {role: RecordingReviewer(role, 1) for role in REVIEWER_ROLES}
        # NEEDS_REVISION + revised orchestrator: the ORCHESTRATOR is really
        # called (a clean PASS would legitimately bypass it, zero calls).
        reviewers[ProposalRole.SCIENTIFIC_REVIEWER].needs_revision = True
        orchestrator = RecordingOrchestrator(revised_text=REVISED_BYTES.decode("utf-8"))
        captured = {}

        def fake_build_drivers():
            captured["reviewers"] = reviewers
            captured["orchestrator"] = orchestrator
            return reviewers, orchestrator

        panel._build_drivers = fake_build_drivers
        panel._on_run_iteration()
        assert panel._running_action == "RUN ITERATION"
        spec = panel._worker.spec
        # The FOUR current configs ride the spec...
        assert spec.reviewer_agent_configs is not None
        assert (
            spec.reviewer_agent_configs[ProposalRole.SCIENTIFIC_REVIEWER].model
            == "science-model"
        )
        assert (
            spec.orchestrator_agent_config.model == "orch-model"
        )
        # ...and the worker forwards them into run_iteration.  The worker
        # started by the PRODUCTION click path (panel._start_worker) is the
        # one that runs the iteration — never start a SECOND worker over
        # the same workspace: two concurrent cycles on one workspace race
        # the durable review artifacts and the loser fail-closes with
        # ARTIFACT_CONFLICT (by design).  Drain the panel's own worker and
        # read the panel's report.
        self._pump_panel(panel)
        assert panel._last_report is not None
        assert panel._last_report["outcome"] == "READY_FOR_NEXT_ITERATION"
        sci = reviewers[ProposalRole.SCIENTIFIC_REVIEWER].requests[0]
        assert sci.model == "science-model"
        assert sci.project_profile == "science-profile"
        assert sci.workspace_path == str(ws)
        assert orchestrator.requests[0].model == "orch-model"
        assert orchestrator.requests[0].workspace_path == str(ws)


# ---------------------------------------------------------------------------
# §6/§13 items 8–10: STATE-MACHINE RECOVERY + WORKSPACE SWITCH SAFETY
# ---------------------------------------------------------------------------
def seed_iteration3_hgv(tmp_path: Path) -> tuple[Path, str]:
    """Iteration-3 workspace at HARD_GATE_VALIDATION (review+integration done).

    Seeded artifacts mirror the S015 writers' schemas exactly: review bundle
    over iteration-3 hash, integration_result (NO_CHANGE, output == input),
    NO NEXT_ITERATION.json, proposal review-current.
    """
    text = PROPOSAL_BYTES + b"iteration three\n"
    ws = pp.ProposalWorkspace(tmp_path)
    ws.initialize()
    ws.master_proposal_path().write_bytes(text)
    ws_hash = pp.proposal_fingerprint(ws.master_proposal_path())
    it_dir = tmp_path / "04_REVIEWS" / "iteration_003"
    it_dir.mkdir(parents=True, exist_ok=True)
    reviewer = review_payload(ProposalRole.SCIENTIFIC_REVIEWER, "PASS", 3)
    (it_dir / "review_bundle.json").write_text(
        json.dumps(
            {
                "iteration_number": 3,
                "proposal_revision": REVISION,
                "proposal_hash": ws_hash,
                "aggregate_verdict": "PASS",
                "findings": [],
            },
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    (it_dir / "integration_result.json").write_text(
        json.dumps(
            {
                "schema": "encomm-pcc.integration-result/v1",
                "iteration_number": 3,
                "proposal_revision": REVISION,
                "input_proposal_hash": ws_hash,
                "output_proposal_hash": ws_hash,
                "runtime": {"outcome": "COMPLETED_NO_CHANGE"},
            },
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    return tmp_path, ws_hash


def seed_iteration3_handoff(
    tmp_path: Path, *, revised: bytes = REVISED_BYTES, valid: bool = True
) -> tuple[Path, str]:
    """Iteration-3 workspace after integration CHANGED the proposal.

    ``valid=True``: the durable handoff matches the S015 contract exactly →
    recovery MUST be REVISION_REQUIRED with next_iteration_number 4.
    ``valid=False``: the handoff is malformed → recovery MUST be ambiguous.
    """
    ws = pp.ProposalWorkspace(tmp_path)
    ws.initialize()
    ws.master_proposal_path().write_bytes(revised)
    revised_hash = pp.proposal_fingerprint(ws.master_proposal_path())
    reviewed_text = PROPOSAL_BYTES
    reviewed_hash = fingerprint_bytes(reviewed_text)
    it_dir = tmp_path / "04_REVIEWS" / "iteration_003"
    it_dir.mkdir(parents=True, exist_ok=True)
    (it_dir / "review_bundle.json").write_text(
        json.dumps(
            {
                "iteration_number": 3,
                "proposal_revision": REVISION,
                "proposal_hash": reviewed_hash,
                "aggregate_verdict": "NEEDS_REVISION",
                "findings": [],
            },
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    (it_dir / "integration_result.json").write_text(
        json.dumps(
            {
                "schema": "encomm-pcc.integration-result/v1",
                "iteration_number": 3,
                "proposal_revision": REVISION,
                "input_proposal_hash": reviewed_hash,
                "output_proposal_hash": revised_hash,
                "runtime": {"outcome": "COMPLETED_CHANGED"},
            },
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    handoff = {
        "schema": REVISION_HANDOFF_SCHEMA,
        "previous_iteration_number": 3,
        "next_iteration_number": 4,
        "previous_reviewed_hash": reviewed_hash,
        "revised_proposal_hash": revised_hash,
        "previous_findings": [],
        "unresolved_items": [],
        "integration_summary": "Applied revisions.",
    }
    if valid:
        (tmp_path / "05_CONTROL" / NEXT_ITERATION_FILENAME).write_text(
            json.dumps(handoff, indent=2, sort_keys=True),
            encoding="utf-8",
            newline="\n",
        )
    else:
        (tmp_path / "05_CONTROL" / NEXT_ITERATION_FILENAME).write_text(
            "{not json at all",
            encoding="utf-8",
            newline="\n",
        )
    return tmp_path, revised_hash


class TestStateRecovery:
    def test_item8_restart_at_hgv_reconstructs_the_machine_at_that_phase(
        self, qapp, tmp_path
    ):
        ws, _ws_hash = seed_iteration3_hgv(tmp_path)
        controller = PipelineController(database=None, event_log=NullEventLog())
        window = MainWindow(controller)
        panel = window.proposal_panel
        panel.ws_edit.setText(str(ws))
        panel.refresh_status()
        # The recovered machine is at HARD_GATE_VALIDATION — NOT at IDLE.
        assert panel._machine is not None
        assert panel._machine.phase is ProposalPhase.HARD_GATE_VALIDATION

    def test_item9_run_hard_gates_actually_runs_from_the_recovered_phase(
        self, qapp, tmp_path
    ):
        ws, _ws_hash = seed_iteration3_hgv(tmp_path)
        # Make the gates runnable-but-failing honestly (no evidence): the
        # point is the machine was AT HARD_GATE_VALIDATION, never IDLE.
        controller = PipelineController(database=None, event_log=NullEventLog())
        window = MainWindow(controller)
        panel = window.proposal_panel
        panel.ws_edit.setText(str(ws))
        panel.refresh_status()
        assert panel.run_gates_button.isEnabled()
        assert not panel.run_iteration_button.isEnabled()
        panel._on_run_hard_gates()
        assert panel._running_action == "RUN HARD GATES"
        deadline = _monotonic_deadline(30.0)
        while (panel._thread.isRunning() or panel._last_report is None) and (
            _now() < deadline
        ):
            QCoreApplication.processEvents()
        panel._thread.wait()
        QCoreApplication.processEvents()
        report = panel._last_report
        assert isinstance(report, dict)
        # From IDLE the runner refuses at the entry guard ("the machine is
        # at IDLE"); from the recovered phase it RUNS and honestly BLOCKs.
        assert report["outcome"] in {"BLOCKED", "STALE_REVIEW", "RUN_FAILED"}
        assert "at IDLE" not in str(report.get("error", ""))

    def test_item10_workspace_switch_does_not_reuse_the_old_machine(
        self, qapp, tmp_path
    ):
        ws_a, _h = seed_iteration3_hgv(tmp_path / "a")
        ws_b, _h2 = seed_workspace(tmp_path / "b")
        controller = PipelineController(database=None, event_log=NullEventLog())
        window = MainWindow(controller)
        panel = window.proposal_panel
        panel.ws_edit.setText(str(ws_a))
        panel.refresh_status()
        machine_a = panel._machine
        assert machine_a is not None
        assert machine_a.phase is ProposalPhase.HARD_GATE_VALIDATION
        # Switch to B: the machine MUST be rebuilt as IDLE (fresh workspace).
        panel.ws_edit.setText(str(ws_b))
        panel.refresh_status()
        assert panel._machine is not machine_a
        assert panel._machine.phase is ProposalPhase.IDLE
        assert panel._machine_workspace == ws_b
        # And the reverse: back to A reconstructs HARD_GATE_VALIDATION.
        panel.ws_edit.setText(str(ws_a))
        panel.refresh_status()
        assert panel._machine.phase is ProposalPhase.HARD_GATE_VALIDATION


# ---------------------------------------------------------------------------
# §7/§13 items 11–13: NEXT_ITERATION RECOVERY + SPINNER
# ---------------------------------------------------------------------------
class TestNextIterationRecovery:
    def test_item11_valid_handoff_recovers_revision_required(self, tmp_path):
        ws, _revised_hash = seed_iteration3_handoff(tmp_path)
        status = prt.load_workspace_status(ws)
        assert status.phase is ProposalPhase.REVISION_REQUIRED
        assert status.ambiguous is False
        assert status.detail.get("next_iteration_number") == 4

    def test_item12_malformed_handoff_becomes_ambiguous(self, tmp_path):
        ws, _revised_hash = seed_iteration3_handoff(tmp_path, valid=False)
        status = prt.load_workspace_status(ws)
        assert status.ambiguous is True
        assert status.phase is not ProposalPhase.HARD_GATE_VALIDATION

    def test_missing_handoff_with_changed_proposal_is_ambiguous(self, tmp_path):
        ws = pp.ProposalWorkspace(tmp_path)
        ws.initialize()
        ws.master_proposal_path().write_bytes(REVISED_BYTES)
        reviewed_hash = fingerprint_bytes(PROPOSAL_BYTES)
        it_dir = tmp_path / "04_REVIEWS" / "iteration_001"
        it_dir.mkdir(parents=True, exist_ok=True)
        (it_dir / "review_bundle.json").write_text(
            json.dumps(
                {
                    "iteration_number": 1,
                    "proposal_revision": REVISION,
                    "proposal_hash": reviewed_hash,
                    "aggregate_verdict": "PASS",
                    "findings": [],
                },
                indent=2,
            ),
            encoding="utf-8",
            newline="\n",
        )
        (it_dir / "integration_result.json").write_text(
            json.dumps(
                {
                    "schema": "encomm-pcc.integration-result/v1",
                    "iteration_number": 1,
                    "proposal_revision": REVISION,
                    "input_proposal_hash": reviewed_hash,
                    "output_proposal_hash": pp.proposal_fingerprint(
                        ws.master_proposal_path()
                    ),
                    "runtime": {"outcome": "COMPLETED_CHANGED"},
                },
                indent=2,
            ),
            encoding="utf-8",
            newline="\n",
        )
        status = prt.load_workspace_status(tmp_path)
        assert status.ambiguous is True

    def test_item13_spinner_restored_from_durable_state(self, qapp, tmp_path):
        ws, _h = seed_iteration3_hgv(tmp_path)
        controller = PipelineController(database=None, event_log=NullEventLog())
        window = MainWindow(controller)
        panel = window.proposal_panel
        panel.ws_edit.setText(str(ws))
        panel.refresh_status()
        assert panel.iteration_spin.value() == 3

    def test_item13b_spinner_restored_to_next_iteration_after_handoff(
        self, qapp, tmp_path
    ):
        ws, _h = seed_iteration3_handoff(tmp_path)
        controller = PipelineController(database=None, event_log=NullEventLog())
        window = MainWindow(controller)
        panel = window.proposal_panel
        panel.ws_edit.setText(str(ws))
        panel.refresh_status()
        assert panel.iteration_spin.value() == 4


def _monotonic_deadline(seconds: float) -> float:
    import time

    return time.monotonic() + seconds


def _now() -> float:
    import time

    return time.monotonic()


# ---------------------------------------------------------------------------
# §9/§13 items 14–15: PROPOSAL_CONFIG.json AUTO-LOAD (Coding Mode untouched)
# ---------------------------------------------------------------------------
def write_workspace_config(ws: Path, tag: str) -> None:
    roles_payload = {
        role.value: ProposalAgentConfig(
            role=role,
            engine="hermes",
            project_profile=f"{tag}-profile",
            provider=f"{tag}-provider",
            model=f"{tag}-model",
        ).to_dict()
        for role in ProposalRole
    }
    (ws / "05_CONTROL" / "PROPOSAL_CONFIG.json").write_text(
        json.dumps(
            {"schema": "encomm-pcc.proposal-config/v1", "roles": roles_payload},
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
        newline="\n",
    )


def coding_config_snapshot(window: MainWindow) -> dict:
    state = window.controller.state
    snapshot = {}
    for role in state.role_configs:
        config = state.config_for(role)
        snapshot[role.value] = (
            config.to_dict() if hasattr(config, "to_dict") else vars(config).copy()
        )
    return snapshot


class TestConfigAutoLoad:
    def _window(self, qapp):
        controller = PipelineController(database=None, event_log=NullEventLog())
        return MainWindow(controller)

    def test_item14_config_autoloads_on_existing_workspace_open(self, qapp, tmp_path):
        ws, _h = seed_workspace(tmp_path)
        write_workspace_config(ws, "saved")
        window = self._window(qapp)
        panel = window.proposal_panel
        # A FRESH panel starts unconfigured.
        assert panel.role_configs()[ProposalRole.ORCHESTRATOR].engine == ""
        panel.ws_edit.setText(str(ws))
        panel.refresh_status()
        configs = panel.role_configs()
        assert configs[ProposalRole.SCIENTIFIC_REVIEWER].project_profile == "saved-profile"
        assert configs[ProposalRole.SCIENTIFIC_REVIEWER].model == "saved-model"
        assert configs[ProposalRole.PROPOSAL_ENGINEER].provider == "saved-provider"
        assert configs[ProposalRole.RED_TEAM_REVIEWER].engine == "hermes"
        assert configs[ProposalRole.ORCHESTRATOR].model == "saved-model"

    def test_item15_config_load_does_not_mutate_coding_mode(self, qapp, tmp_path):
        ws, _h = seed_workspace(tmp_path)
        write_workspace_config(ws, "saved")
        window = self._window(qapp)
        before = coding_config_snapshot(window)
        panel = window.proposal_panel
        panel.ws_edit.setText(str(ws))
        panel.refresh_status()
        after = coding_config_snapshot(window)
        assert before == after
        # The proposal config lives ONLY in the workspace's 05_CONTROL.
        assert (ws / "05_CONTROL" / "PROPOSAL_CONFIG.json").is_file()

    def test_workspace_switch_loads_that_workspaces_config(self, qapp, tmp_path):
        ws_a, _h1 = seed_iteration3_hgv(tmp_path / "a")
        ws_b, _h2 = seed_iteration3_hgv(tmp_path / "b")
        write_workspace_config(ws_a, "alpha")
        write_workspace_config(ws_b, "beta")
        window = self._window(qapp)
        panel = window.proposal_panel
        panel.ws_edit.setText(str(ws_a))
        panel.refresh_status()
        assert panel.role_configs()[ProposalRole.ORCHESTRATOR].model == "alpha-model"
        panel.ws_edit.setText(str(ws_b))
        panel.refresh_status()
        # B's config replaced A's — nothing from A leaks into B.
        assert panel.role_configs()[ProposalRole.ORCHESTRATOR].model == "beta-model"
        assert panel.role_configs()[ProposalRole.SCIENTIFIC_REVIEWER].project_profile == "beta-profile"

    def test_invalid_config_stays_unconfigured_never_guessed(self, qapp, tmp_path):
        ws, _h = seed_workspace(tmp_path)
        (ws / "05_CONTROL" / "PROPOSAL_CONFIG.json").write_text(
            "{not json", encoding="utf-8", newline="\n"
        )
        window = self._window(qapp)
        panel = window.proposal_panel
        panel.ws_edit.setText(str(ws))
        panel.refresh_status()
        # Invalid config: rows stay unconfigured — never guessed.
        for role in ProposalRole:
            assert panel.role_configs()[role].engine == ""


# ---------------------------------------------------------------------------
# §10/§13 items 16–20: PHASE-AWARE RUN BUTTONS
# ---------------------------------------------------------------------------
class TestPhaseAwareGating:
    def _panel_at(self, qapp, tmp_path, phase: ProposalPhase):
        ws, _h = seed_iteration3_hgv(tmp_path)
        window = MainWindow(PipelineController(database=None, event_log=NullEventLog()))
        panel = window.proposal_panel
        panel.ws_edit.setText(str(ws))
        panel._machine = ProposalStateMachine(phase)
        panel._machine_workspace = ws
        panel._apply_phase_gating(ws)
        return panel

    def test_item19_hgv_enables_only_the_gate_run(self, qapp, tmp_path):
        panel = self._panel_at(qapp, tmp_path, ProposalPhase.HARD_GATE_VALIDATION)
        assert panel.run_gates_button.isEnabled()
        assert not panel.run_iteration_button.isEnabled()

    def test_item20_revision_required_enables_only_the_iteration_run(
        self, qapp, tmp_path
    ):
        panel = self._panel_at(qapp, tmp_path, ProposalPhase.REVISION_REQUIRED)
        assert panel.run_iteration_button.isEnabled()
        assert not panel.run_gates_button.isEnabled()

    def test_iteration_entry_phases_enable_the_iteration_run(self, qapp, tmp_path):
        for phase in (
            ProposalPhase.IDLE,
            ProposalPhase.SOURCE_VALIDATION,
            ProposalPhase.SCIENTIFIC_REVIEW,
            ProposalPhase.IMPLEMENTATION_REVIEW,
            ProposalPhase.RED_TEAM_REVIEW,
        ):
            panel = self._panel_at(qapp, tmp_path, phase)
            assert panel.run_iteration_button.isEnabled(), phase.value
            assert not panel.run_gates_button.isEnabled(), phase.value

    def test_item18_complete_disables_execution(self, qapp, tmp_path):
        panel = self._panel_at(qapp, tmp_path, ProposalPhase.COMPLETE)
        assert not panel.run_iteration_button.isEnabled()
        assert not panel.run_gates_button.isEnabled()

    def test_blocked_failed_integration_disable_execution(self, qapp, tmp_path):
        for phase in (
            ProposalPhase.BLOCKED,
            ProposalPhase.FAILED,
            ProposalPhase.INTEGRATION,
        ):
            panel = self._panel_at(qapp, tmp_path, phase)
            assert not panel.run_iteration_button.isEnabled(), phase.value
            assert not panel.run_gates_button.isEnabled(), phase.value

    def test_item17_ambiguous_recovery_disables_execution(self, qapp, tmp_path):
        ws, _h = seed_iteration3_handoff(tmp_path, valid=False)
        window = MainWindow(PipelineController(database=None, event_log=NullEventLog()))
        panel = window.proposal_panel
        panel.ws_edit.setText(str(ws))
        panel.refresh_status()
        # No runnable machine is fabricated from an ambiguous recovery.
        assert panel._machine is None
        assert not panel.run_iteration_button.isEnabled()
        assert not panel.run_gates_button.isEnabled()
        assert "Recovery requires operator confirmation" in panel.ws_status_label.text()
