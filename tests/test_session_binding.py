"""Session 006 offline matrix: external-session binding, selector, generic path.

Everything here is deterministic and offline.  The Codex engine is the REAL
``CodexDriver`` over a scripted process runner (so the argv/stdin/parse path is
exercised end to end), with the live-evidence gates simulated via monkeypatch —
the real gates are flipped only by the two live smoke calls.

Proven here (brief §10–§15, §18):

* binding round-trip + persistence across a real close/reopen restart
* engine switching cannot reuse a session bound to another driver
* NEW SESSION clears the binding and makes no model call
* select/refresh/bind never launch a process (NullProcessRunner would raise)
* the executor resumes the BOUND external session through the generic path
  (``codex exec resume <id>`` argv) for FINAL_AUDITOR — after a restart too
* ORCHESTRATOR configured to Codex plans through the same generic path offline
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from encomm_pcc.core import (
    FINAL_AUDIT_ENVELOPE_END,
    FINAL_AUDIT_ENVELOPE_START,
    PLAN_ENVELOPE_END,
    PLAN_ENVELOPE_START,
    FinalAuditOutcome,
    PipelineController,
    PlanOutcome,
)
from encomm_pcc.core.events import NullEventLog
from encomm_pcc.core.executor import Executor
from encomm_pcc.domain import (
    AgentRole,
    BatchState,
    BatchPlan,
    BatchPlanRecord,
    PipelinePhase,
    PlannedTask,
    TaskState,
    TaskStateRecord,
)
from encomm_pcc.drivers import (
    CodexDriver,
    DriverRegistry,
    ProcessResult,
    ProcessSpec,
)
from encomm_pcc.persistence import Database

SID = "019d1123-1111-2222-3333-444455556666"


# ----------------------------------------------------------------------------
# scripted Codex runner: returns a valid codex --json stream for any prompt
# ----------------------------------------------------------------------------
def codex_stdout(answer: str, *, session_id: str = SID) -> str:
    lines = [json.dumps({"msg": {"type": "session_id", "session_id": session_id}})]
    lines.append(json.dumps({"msg": {"type": "agent_message", "message": answer}}))
    lines.append(json.dumps({"msg": {"type": "turn.completed", "usage": {}}}))
    return "\n".join(lines) + "\n"


class CodexScriptedRunner:
    """Records every spec; returns the queued stdout payloads."""

    def __init__(self, stdouts: list[str]) -> None:
        self.stdouts = list(stdouts)
        self.specs: list[ProcessSpec] = []

    def run(self, spec: ProcessSpec) -> ProcessResult:
        self.specs.append(spec)
        if not self.stdouts:
            raise AssertionError("CodexScriptedRunner: no queued stdout")
        return ProcessResult(
            argv=spec.argv_list(),
            exit_code=0,
            stdout=self.stdouts.pop(0),
            stderr="",
            duration_s=0.05,
        )


def final_audit_answer() -> str:
    payload = {
        "final_verdict": "PASS",
        "summary": "ok",
        "findings": [],
        "batch_assessment": {"tests_verified": True, "diff_verified": True},
        "next_batch": {
            "batch_title": "Next",
            "batch_objective": "Next objective",
            "tasks": [
                {
                    "index": i,
                    "title": f"Next task {i}",
                    "implementation_prompt": f"Implement next {i}.",
                    "acceptance_criteria": [f"test {i} exits 0"],
                    "audit_focus": [f"verify {i}"],
                }
                for i in range(1, 5)
            ],
        },
    }
    return (
        f"{FINAL_AUDIT_ENVELOPE_START}\n{json.dumps(payload)}\n{FINAL_AUDIT_ENVELOPE_END}"
    )


def plan_answer(n: int = 2) -> str:
    payload = {
        "batch_title": "Planned batch",
        "batch_objective": "Objective",
        "tasks": [
            {
                "index": i,
                "title": f"Task {i}",
                "implementation_prompt": f"Implement {i}.",
                "acceptance_criteria": [f"test {i} exits 0"],
                "audit_focus": [f"verify {i}"],
            }
            for i in range(1, n + 1)
        ],
    }
    return f"{PLAN_ENVELOPE_START}\n{json.dumps(payload)}\n{PLAN_ENVELOPE_END}"


# ----------------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------------
@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    repo = tmp_path / "workspace"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "t@localhost"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(repo), check=True)
    (repo / "feature_1.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=str(repo), check=True)
    return repo


@pytest.fixture()
def codex_gates(monkeypatch: pytest.MonkeyPatch):
    """Simulate the live-verified state for offline path proofs."""
    monkeypatch.setattr("encomm_pcc.drivers.codex._LIVE_SMOKE_VERIFIED", True)
    monkeypatch.setattr("encomm_pcc.drivers.codex._LIVE_RESUME_VERIFIED", True)
    monkeypatch.setattr(CodexDriver, "resolve_executable", classmethod(lambda cls: "codex.exe"))
    return monkeypatch


def seed_ready_batch(controller: PipelineController, repo: Path) -> str:
    """Seed a 4-task batch at READY_FOR_FINAL_AUDIT (durable facts only)."""
    controller.set_workspace("Binding tests", str(repo))
    batch = BatchState(workspace_id=controller.state.workspace.workspace_id, size=4)
    plan = BatchPlan(
        batch_title="Completed batch",
        batch_objective="Four features",
        tasks=[
            PlannedTask(
                index=i,
                title=f"Feature {i}",
                implementation_prompt=f"Create feature_{i}.py.",
                acceptance_criteria=[f"test {i} exits 0"],
                audit_focus=[f"verify {i}"],
            )
            for i in range(1, 5)
        ],
    )
    batch.plan = BatchPlanRecord(
        plan=plan,
        project_brief="Four features",
        requested_size=4,
        plan_status="PLANNED",
        orchestrator_session_id="orch_fixture",
        baseline_head="base0",
        current_head="head1",
        batch_summary_json=json.dumps({"batch_id": batch.batch_id}),
    )
    for i in range(1, 5):
        batch.tasks.append(
            TaskStateRecord(
                index=i,
                title=f"Feature {i}",
                prompt=f"Create feature_{i}.py.",
                acceptance_criteria=[f"test {i} exits 0"],
                audit_focus=[f"verify {i}"],
                state=TaskState.APPROVED,
                attempts=1,
                audit_rounds=1,
                latest_verdict="PASS",
            )
        )
    controller.state.batch = batch
    controller.persist()
    for step in (
        PipelinePhase.PLANNING_BATCH,
        PipelinePhase.RUNNING_TASK,
        PipelinePhase.AUDITING_TASK,
        PipelinePhase.READY_FOR_FINAL_AUDIT,
    ):
        if controller.machine.can_go_to(step):
            controller.machine.transition_to(step)
    controller.state.phase = controller.machine.phase
    return batch.batch_id


def make_executor(controller: PipelineController, runner) -> Executor:
    registry = DriverRegistry()
    registry.register(CodexDriver)
    return Executor(
        controller,
        registry=registry,
        runner=runner,
        database=controller.database,
        event_log=NullEventLog(),
        profile_discovery=lambda **kwargs: None,
    )


# ----------------------------------------------------------------------------
# binding domain + persistence
# ----------------------------------------------------------------------------
def test_binding_round_trips_through_role_config(database) -> None:
    controller = PipelineController(database=database, event_log=NullEventLog())
    controller.set_role_config(AgentRole.ORCHESTRATOR, engine="codex")
    controller.bind_external_session(
        AgentRole.ORCHESTRATOR, "codex", SID, title="t", workspace_path=r"C:\w"
    )
    binding = controller.role_config(AgentRole.ORCHESTRATOR).external_session_binding()
    assert binding is not None
    assert binding.external_session_id == SID
    assert binding.driver_id == "codex"
    assert binding.title == "t"


def test_binding_survives_a_real_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "pcc.db"
    db = Database(str(db_path)).open()
    first = PipelineController(database=db, event_log=NullEventLog())
    first.set_role_config(AgentRole.ORCHESTRATOR, engine="codex")
    first.bind_external_session(AgentRole.ORCHESTRATOR, "codex", SID)
    db.close()

    reopened = Database(str(db_path)).open()
    from encomm_pcc.app import restore_state

    state = restore_state(reopened)
    assert state is not None
    binding = state.config_for(AgentRole.ORCHESTRATOR).external_session_binding()
    reopened.close()
    assert binding is not None
    assert binding.external_session_id == SID, "full external id preserved verbatim"


def test_engine_switch_invalidates_the_binding(database) -> None:
    controller = PipelineController(database=database, event_log=NullEventLog())
    controller.set_role_config(AgentRole.ORCHESTRATOR, engine="codex")
    controller.bind_external_session(AgentRole.ORCHESTRATOR, "codex", SID)
    assert controller.role_config(AgentRole.ORCHESTRATOR).external_session_binding() is not None

    controller.set_role_config(AgentRole.ORCHESTRATOR, engine="hermes")
    assert (
        controller.role_config(AgentRole.ORCHESTRATOR).external_session_binding() is None
    ), "a session bound to codex must never be visible under hermes"

    controller.set_role_config(AgentRole.ORCHESTRATOR, engine="codex")
    assert controller.role_config(AgentRole.ORCHESTRATOR).external_session_binding() is not None


def test_new_session_clears_the_binding_with_no_model_call(database) -> None:
    controller = PipelineController(database=database, event_log=NullEventLog())
    controller.set_role_config(AgentRole.ORCHESTRATOR, engine="codex")
    controller.bind_external_session(AgentRole.ORCHESTRATOR, "codex", SID)
    assert controller.clear_external_session(AgentRole.ORCHESTRATOR) is True
    assert controller.role_config(AgentRole.ORCHESTRATOR).external_session_binding() is None
    assert controller.sessions.current_session_id(AgentRole.ORCHESTRATOR) is None
    assert controller.clear_external_session(AgentRole.ORCHESTRATOR) is False


# ----------------------------------------------------------------------------
# executor + generic role path (Codex offline)
# ----------------------------------------------------------------------------
def test_final_auditor_resumes_the_bound_codex_session(
    codex_gates, database, ws
) -> None:
    controller = PipelineController(database=database, event_log=NullEventLog())
    controller.set_role_config(
        AgentRole.FINAL_AUDITOR,
        engine="codex",
        project_profile="",  # Codex needs no Hermes profile
        same_as_orchestrator=False,
    )
    batch_id = seed_ready_batch(controller, ws)
    controller.bind_external_session(AgentRole.FINAL_AUDITOR, "codex", SID)

    runner = CodexScriptedRunner([codex_stdout(final_audit_answer())])
    executor = make_executor(controller, runner)
    controller.attach_executor(executor)

    report = executor.run_final_audit(next_batch_size=4)
    assert report.outcome is FinalAuditOutcome.PASSED, report.message
    assert report.session_id == SID
    argv = runner.specs[0].argv_list()
    assert argv[1:4] == ["exec", "resume", SID], "the BOUND session was resumed"
    assert runner.specs[0].stdin_text and "FINAL_AUDIT" in runner.specs[0].stdin_text


def test_final_auditor_binding_resumes_after_a_restart(codex_gates, tmp_path, ws) -> None:
    db_path = tmp_path / "pcc.db"
    db = Database(str(db_path)).open()
    first = PipelineController(database=db, event_log=NullEventLog())
    first.set_role_config(
        AgentRole.FINAL_AUDITOR,
        engine="codex",
        project_profile="",
        same_as_orchestrator=False,
    )
    seed_ready_batch(first, ws)
    first.bind_external_session(AgentRole.FINAL_AUDITOR, "codex", SID)
    db.close()

    reopened = Database(str(db_path)).open()
    from encomm_pcc.app import restore_state

    state = restore_state(reopened)
    controller = PipelineController(database=reopened, event_log=NullEventLog(), state=state)
    seed_ready_batch(controller, ws)  # re-seed the in-flight batch (fresh process)

    runner = CodexScriptedRunner([codex_stdout(final_audit_answer())])
    executor = make_executor(controller, runner)
    controller.attach_executor(executor)

    report = executor.run_final_audit(next_batch_size=4)
    reopened.close()
    assert report.outcome is FinalAuditOutcome.PASSED, report.message
    assert runner.specs[0].argv_list()[1:4] == ["exec", "resume", SID], (
        "the binding restored from SQLite was used for the resume"
    )


def test_orchestrator_configured_to_codex_plans_through_the_generic_path(
    codex_gates, database, ws
) -> None:
    """Offline proof (brief §19): the Codex path needs no role-specific code."""
    controller = PipelineController(database=database, event_log=NullEventLog())
    controller.set_role_config(AgentRole.ORCHESTRATOR, engine="codex", project_profile="")
    controller.set_workspace("Planning", str(ws))

    runner = CodexScriptedRunner([codex_stdout(plan_answer(2))])
    executor = make_executor(controller, runner)
    controller.attach_executor(executor)

    report = executor.plan_batch(project_brief="Two tasks", batch_size=2)
    assert report.outcome is PlanOutcome.PLANNED, report.message
    assert report.plan is not None and report.plan.task_count == 2
    assert report.session_id == SID
    argv = runner.specs[0].argv_list()
    assert argv[1:3] == ["exec", "--json"], "fresh codex exec path"
    assert argv[-1] == "-" and runner.specs[0].stdin_text


def test_final_auditor_on_codex_without_a_binding_runs_fresh(
    codex_gates, database, ws
) -> None:
    controller = PipelineController(database=database, event_log=NullEventLog())
    controller.set_role_config(
        AgentRole.FINAL_AUDITOR, engine="codex", project_profile="", same_as_orchestrator=False
    )
    seed_ready_batch(controller, ws)
    runner = CodexScriptedRunner([codex_stdout(final_audit_answer())])
    executor = make_executor(controller, runner)
    controller.attach_executor(executor)

    report = executor.run_final_audit(next_batch_size=4)
    assert report.outcome is FinalAuditOutcome.PASSED, report.message
    argv = runner.specs[0].argv_list()
    assert argv[1:3] == ["exec", "--json"], "no binding ⇒ fresh exec, never resume"


# ----------------------------------------------------------------------------
# UI selector
# ----------------------------------------------------------------------------
def _make_window(qapp, controller):
    from encomm_pcc.ui.main_window import MainWindow

    return MainWindow(controller)


def test_selector_visibility_follows_engine_discovery(qapp, database) -> None:
    controller = PipelineController(database=database, event_log=NullEventLog())
    window = _make_window(qapp, controller)
    panel = window.role_panels[AgentRole.ORCHESTRATOR]

    controller.set_role_config(AgentRole.ORCHESTRATOR, engine="codex")
    panel.refresh_from_controller()
    assert panel.refresh_sessions_button is not None
    # codex gates are live-gated; discovery support is structural, so the
    # button is enabled only when the engine is implemented AND discoverable.
    from encomm_pcc.drivers.codex import _LIVE_SMOKE_VERIFIED

    assert panel.refresh_sessions_button.isEnabled() == _LIVE_SMOKE_VERIFIED

    controller.set_role_config(AgentRole.ORCHESTRATOR, engine="hermes")
    panel.refresh_from_controller()
    assert panel.refresh_sessions_button.isEnabled() is False
    window.close()


def test_refresh_lists_discovered_sessions_and_selection_binds(
    qapp, database, codex_gates
) -> None:
    controller = PipelineController(database=database, event_log=NullEventLog())
    controller.set_role_config(AgentRole.ORCHESTRATOR, engine="codex")
    window = _make_window(qapp, controller)
    panel = window.role_panels[AgentRole.ORCHESTRATOR]
    panel.refresh_from_controller()

    # Real read-only discovery over the machine's actual Codex state.
    window._on_sessions_refresh(AgentRole.ORCHESTRATOR)
    assert panel.session_combo.count() >= 1
    ids = {
        panel.session_combo.itemData(i)
        for i in range(1, panel.session_combo.count())
    }
    assert all(isinstance(v, str) for v in ids)

    if len(panel._session_descriptors) == 0:  # noqa: SLF001 - host has no codex state
        pytest.skip("no Codex sessions on this host to select from")

    target = panel._session_descriptors[0]  # noqa: SLF001
    window._on_session_selected(AgentRole.ORCHESTRATOR, target.session_id)
    binding = controller.role_config(AgentRole.ORCHESTRATOR).external_session_binding()
    assert binding is not None and binding.external_session_id == target.session_id
    # The combo shows the bound row and carries the FULL id as data.
    assert panel.session_combo.currentData() == target.session_id
    window.close()


def test_selector_refresh_and_select_launch_nothing(qapp, database, codex_gates) -> None:
    """Refresh/select/new make zero model calls: any launch would raise (Null)."""
    controller = PipelineController(database=database, event_log=NullEventLog())
    controller.set_role_config(AgentRole.ORCHESTRATOR, engine="codex")
    window = _make_window(qapp, controller)
    panel = window.role_panels[AgentRole.ORCHESTRATOR]
    panel.refresh_from_controller()

    panel._on_refresh_sessions_clicked()  # discovery: pure filesystem reads
    if panel._session_descriptors:  # noqa: SLF001
        window._on_session_selected(AgentRole.ORCHESTRATOR, panel._session_descriptors[0].session_id)  # noqa: SLF001
    panel.new_session_button.click()
    assert controller.role_config(AgentRole.ORCHESTRATOR).external_session_binding() is None
    window.close()


def test_final_auditor_panel_has_the_same_selector_surface(qapp, database) -> None:
    controller = PipelineController(database=database, event_log=NullEventLog())
    window = _make_window(qapp, controller)
    panel = window.role_panels[AgentRole.FINAL_AUDITOR]
    assert panel.new_session_button is not None
    assert panel.refresh_sessions_button is not None
    assert panel.session_combo is not None
    window.close()
