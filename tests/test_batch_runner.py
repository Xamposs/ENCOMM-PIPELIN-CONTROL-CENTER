"""The deterministic multi-task batch runner (Session 004), fully offline.

A scripted fake driver shares its result queue across instances (the executor
creates a fresh driver per dispatch — exactly like the real registry path), so
the tests prove the session-lifecycle contract: every build/fix gets a NEW
Builder session, the Task Auditor resumes ONE session for the WHOLE batch, and
a resumed batch never re-runs an APPROVED task.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import PermissiveRunner
from encomm_pcc.core import (
    AUDIT_ENVELOPE_END,
    AUDIT_ENVELOPE_START,
    EventLog,
    Executor,
    PipelineController,
    PlanOutcome,
    ProfileDiscoveryResult,
    BatchOutcome,
    BatchRunner,
)
from encomm_pcc.core.executor import next_task_action
from encomm_pcc.domain import AgentRole, BatchStatus, PipelinePhase, TaskState
from encomm_pcc.drivers import (
    DriverCapabilities,
    DriverRegistry,
    DriverSession,
    ProcessSpec,
    PromptResult,
    SessionRequest,
)


def discovery_stub(**kwargs):  # noqa: ANN003, ANN202
    return ProfileDiscoveryResult(
        ok=True, profiles=("test-profile",), method="test", detail="stub"
    )


def build_plan_text(n: int) -> str:
    """Valid plan envelope using the BATCH_PLAN markers (not the audit ones)."""
    from encomm_pcc.core import PLAN_ENVELOPE_END, PLAN_ENVELOPE_START

    tasks = [
        {
            "index": i,
            "title": f"Task {i}",
            "implementation_prompt": f"Implement feature {i} so its test passes.",
            "acceptance_criteria": [f"test_feature_{i}.py exits 0"],
            "audit_focus": [f"verify feature {i} in the repo"],
        }
        for i in range(1, n + 1)
    ]
    payload = {
        "batch_title": f"Scratch batch of {n}",
        "batch_objective": f"Implement exactly {n} independent small features.",
        "tasks": tasks,
    }
    return f"{PLAN_ENVELOPE_START}\n{json.dumps(payload, indent=2)}\n{PLAN_ENVELOPE_END}"


def verdict_text(value: str, *, summary: str = "summary", fix_prompt: str = "") -> str:
    return (
        f"{AUDIT_ENVELOPE_START}\n"
        + json.dumps(
            {
                "verdict": value,
                "summary": summary,
                "findings": (
                    []
                    if value == "PASS"
                    else [
                        {"severity": "high", "message": "defect found", "evidence": "x"}
                    ]
                ),
                "fix_prompt": fix_prompt,
            }
        )
        + f"\n{AUDIT_ENVELOPE_END}"
    )


def ok_result(session_id: str, text: str) -> PromptResult:
    return PromptResult(
        ok=True,
        text=text,
        session_id=session_id,
        exit_code=0,
        duration_s=0.2,
        metadata={"stream": {"saw_result": True, "tokens": {"input": 10, "output": 5, "total": 15}}},
    )


class ScriptedBatchDriver:
    """Fake driver with CLASS-shared state (the registry makes one per dispatch)."""

    driver_id = "batch"
    display_name = "Batch Engine"
    executables = ("python",)

    shared: list[PromptResult] = []
    starts: int = 0
    resumed: list[str] = []
    prompts: list[str] = []
    #: Called after result #i is popped (1-based) — pause/stop injection.
    hooks: dict[int, object] = {}
    #: When True, a planning call writes a file into the workspace (used to
    #: prove the read-only guard blocks a violating plan).
    touch_on_plan: bool = False

    @classmethod
    def reset(cls) -> None:
        cls.shared = []
        cls.starts = 0
        cls.resumed = []
        cls.prompts = []
        cls.hooks = {}
        cls.touch_on_plan = False

    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name=cls.display_name,
            supports_sessions=True,
            supports_resume=True,
            implemented=True,
            notes="Test double for the multi-task batch runner.",
        )

    def __init__(self, runner=None) -> None:  # noqa: ANN001
        self._runner = runner

    def start_session(self, request: SessionRequest) -> DriverSession:
        type(self).starts += 1
        return DriverSession(
            driver_id=self.driver_id,
            role=request.role,
            session_id=None,
            metadata={
                "profile": request.project_profile,
                "workspace_path": request.workspace_path,
            },
        )

    def resume_session(self, session_id: str, request: SessionRequest) -> DriverSession:
        type(self).resumed.append(str(session_id))
        session = self.start_session(request)
        session.session_id = str(session_id)
        session.external = True
        session.metadata["resumed"] = True
        return session

    def send_prompt(self, session: DriverSession, prompt: str):
        from encomm_pcc.drivers import PromptHandle

        return PromptHandle(session=session, prompt=prompt)

    def wait_for_completion(self, handle, timeout_s=None):  # noqa: ANN001
        if self._runner is not None:
            self._runner.run(ProcessSpec(argv=["batch-engine"], timeout_s=timeout_s))
        if not type(self).shared:
            raise AssertionError("ScriptedBatchDriver: no scripted PromptResult left")
        if type(self).touch_on_plan and "ORCHESTRATOR" in handle.prompt:
            workspace = str(handle.session.metadata.get("workspace_path") or "")
            if workspace:
                try:
                    (Path(workspace) / "_planner_touched.txt").write_text(
                        "the planner edited the workspace", encoding="utf-8"
                    )
                except OSError:  # pragma: no cover - the test dir always exists
                    pass
        result = type(self).shared.pop(0)
        type(self).prompts.append(handle.prompt)
        if result.session_id:
            handle.session.session_id = result.session_id
            handle.session.external = True
        step = len(type(self).prompts)
        hook = type(self).hooks.get(step)
        if callable(hook):
            hook()
        return result


@pytest.fixture()
def registry() -> DriverRegistry:
    reg = DriverRegistry()
    reg.register(ScriptedBatchDriver)
    return reg


@pytest.fixture()
def batch_controller(controller, tmp_path: Path):  # noqa: ANN001
    controller.set_workspace("Batch Workspace", str(tmp_path))
    controller.set_role_config(
        AgentRole.ORCHESTRATOR, engine="batch", project_profile="test-profile"
    )
    controller.set_role_config(
        AgentRole.BUILDER, engine="batch", project_profile="test-profile"
    )
    controller.set_role_config(
        AgentRole.TASK_AUDITOR, engine="batch", project_profile="test-profile"
    )
    return controller


@pytest.fixture()
def make_runner(batch_controller, registry):  # noqa: ANN001, ANN201
    def _make(controller=None, results=None):  # noqa: ANN001
        target = controller or batch_controller
        ex = Executor(
            target,
            registry=registry,
            runner=PermissiveRunner(),
            database=target.database,
            profile_discovery=discovery_stub,
        )
        target.attach_executor(ex)
        runner = BatchRunner(ex)
        if results is not None:
            ScriptedBatchDriver.shared = list(results)
        return runner, ex

    return _make


# -- the happy multi-task batch -----------------------------------------------
def test_four_tasks_run_in_order_with_fresh_builders_and_shared_auditor(
    make_runner,  # noqa: ANN001
) -> None:
    ScriptedBatchDriver.reset()
    runner, executor = make_runner(
        results=[
            ok_result("orch_1", build_plan_text(4)),
            ok_result("builder_1", "feature 1 done"),
            ok_result("auditor_1", verdict_text("PASS", summary="t1 ok")),
            ok_result("builder_2", "feature 2 done"),
            ok_result("auditor_1", verdict_text("PASS", summary="t2 ok")),
            ok_result("builder_3", "feature 3 done"),
            ok_result("auditor_1", verdict_text("PASS", summary="t3 ok")),
            ok_result("builder_4", "feature 4 done"),
            ok_result("auditor_1", verdict_text("PASS", summary="t4 ok")),
        ]
    )

    report = runner.run_batch(project_brief="Build four features.", batch_size=4)

    assert report.outcome is BatchOutcome.READY_FOR_FINAL_AUDIT
    assert report.completed_tasks == 4
    assert report.total_tasks == 4
    batch = executor.controller.state.batch
    assert all(t.state is TaskState.APPROVED for t in batch.tasks)
    assert executor.controller.machine.phase is PipelinePhase.READY_FOR_FINAL_AUDIT
    assert batch.status is BatchStatus.READY_FOR_FINAL_AUDIT
    assert executor.next_action().value == "COMPLETE"

    # Builder session isolation: every build is a distinct, fresh session.
    builder_ids = [t.builder_session_id for t in batch.tasks]
    assert len(set(builder_ids)) == 4, "all four Builder sessions must differ"
    # ONE auditor session across the whole batch.
    auditor_ids = {t.auditor_session_id for t in batch.tasks}
    assert auditor_ids == {"auditor_1"}
    assert "auditor_1" not in builder_ids
    assert report.operation_counts()["PLAN"] == 1

    # The batch never reached BATCH_COMPLETE.
    assert executor.controller.machine.phase is not PipelinePhase.BATCH_COMPLETE


def test_five_task_batch_is_supported_offline(make_runner) -> None:  # noqa: ANN001
    ScriptedBatchDriver.reset()
    results = [ok_result("orch_1", build_plan_text(5))]
    for i in range(1, 6):
        results.append(ok_result(f"builder_{i}", f"feature {i} done"))
        results.append(ok_result("auditor_1", verdict_text("PASS")))
    runner, executor = make_runner(results=results)

    report = runner.run_batch(project_brief="Five features.", batch_size=5)

    assert report.outcome is BatchOutcome.READY_FOR_FINAL_AUDIT
    assert report.completed_tasks == 5
    assert executor.controller.machine.phase is PipelinePhase.READY_FOR_FINAL_AUDIT


# -- plan failure paths -------------------------------------------------------
def test_plan_count_mismatch_blocks_the_batch_and_materialises_nothing(
    make_runner,  # noqa: ANN001
) -> None:
    ScriptedBatchDriver.reset()
    runner, executor = make_runner(
        results=[ok_result("orch_1", build_plan_text(3))]  # requested 4
    )

    report = runner.run_batch(project_brief="Four features.", batch_size=4)

    assert report.outcome is BatchOutcome.BLOCKED
    batch = executor.controller.state.batch
    assert batch.tasks == [], "a mismatched plan must never materialise tasks"
    assert batch.status is BatchStatus.BLOCKED
    assert "Malformed" in report.message


def test_plan_count_too_many_never_truncates(make_runner) -> None:  # noqa: ANN001
    ScriptedBatchDriver.reset()
    runner, _ = make_runner(results=[ok_result("orch_1", build_plan_text(5))])

    report = runner.run_batch(project_brief="Four features.", batch_size=4)

    assert report.outcome is BatchOutcome.BLOCKED
    assert "task_count_mismatch" in report.message or "Malformed" in report.message


def test_orchestrator_child_failure_fails_the_batch(make_runner) -> None:  # noqa: ANN001
    ScriptedBatchDriver.reset()
    runner, executor = make_runner(
        results=[PromptResult(ok=False, exit_code=3, error="orchestrator crashed")]
    )

    report = runner.run_batch(project_brief="x", batch_size=2)

    assert report.outcome is BatchOutcome.FAILED
    assert executor.controller.state.batch.status is BatchStatus.FAILED


def test_orchestrator_worktree_modification_blocks_the_plan(make_runner, tmp_path: Path) -> None:  # noqa: ANN001
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(tmp_path), check=True)
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")

    ScriptedBatchDriver.reset()
    ScriptedBatchDriver.touch_on_plan = True
    runner, executor = make_runner(results=[ok_result("orch_1", build_plan_text(2))])

    report = runner.run_batch(project_brief="x", batch_size=2)

    assert report.outcome is BatchOutcome.BLOCKED
    assert report.steps[-1].guard_violation is True
    assert "READ-ONLY GUARD" in report.message
    batch = executor.controller.state.batch
    assert batch.status is BatchStatus.BLOCKED
    assert batch.tasks == [], "a guard violation must not materialise tasks"
    # the violation is surfaced, not discarded
    assert (tmp_path / "_planner_touched.txt").exists()


# -- per-task failure paths ----------------------------------------------------
def test_blocked_verdict_stops_later_tasks(make_runner) -> None:  # noqa: ANN001
    ScriptedBatchDriver.reset()
    runner, executor = make_runner(
        results=[
            ok_result("orch_1", build_plan_text(3)),
            ok_result("builder_1", "done"),
            ok_result("auditor_1", verdict_text("BLOCKED", summary="manual check needed")),
        ]
    )

    report = runner.run_batch(project_brief="x", batch_size=3)

    assert report.outcome is BatchOutcome.BLOCKED
    tasks = executor.controller.state.batch.tasks
    assert tasks[0].state is TaskState.BLOCKED
    assert tasks[1].state is TaskState.PENDING, "later tasks must never start"
    assert tasks[2].state is TaskState.PENDING


def test_failed_build_stops_later_tasks(make_runner) -> None:  # noqa: ANN001
    ScriptedBatchDriver.reset()
    runner, executor = make_runner(
        results=[
            ok_result("orch_1", build_plan_text(3)),
            ok_result("builder_1", "done"),
            ok_result("auditor_1", verdict_text("PASS")),
            PromptResult(ok=False, exit_code=3, error="builder_2 crashed"),
        ]
    )

    report = runner.run_batch(project_brief="x", batch_size=3)

    assert report.outcome is BatchOutcome.FAILED
    tasks = executor.controller.state.batch.tasks
    assert tasks[1].state is TaskState.FAILED
    assert tasks[2].state is TaskState.PENDING


# -- fix loop inside the batch ------------------------------------------------
def test_needs_fix_on_middle_task_uses_the_existing_fix_loop(make_runner) -> None:  # noqa: ANN001
    ScriptedBatchDriver.reset()
    runner, executor = make_runner(
        results=[
            ok_result("orch_1", build_plan_text(3)),
            ok_result("builder_1", "t1 done"),
            ok_result("auditor_1", verdict_text("PASS", summary="t1 ok")),
            ok_result("builder_2", "t2 done"),
            ok_result("auditor_1", verdict_text("NEEDS_FIX", fix_prompt="fix t2")),
            ok_result("fix_2", "t2 fixed"),
            ok_result("auditor_1", verdict_text("PASS", summary="t2 ok now")),
            ok_result("builder_3", "t3 done"),
            ok_result("auditor_1", verdict_text("PASS", summary="t3 ok")),
        ]
    )

    report = runner.run_batch(project_brief="x", batch_size=3)

    assert report.outcome is BatchOutcome.READY_FOR_FINAL_AUDIT
    task2 = executor.controller.state.batch.tasks[1]
    assert task2.state is TaskState.APPROVED
    assert task2.audit_rounds == 2
    assert task2.attempts == 2  # initial build + fix
    assert task2.fix_session_id == "fix_2"
    assert task2.fix_session_id not in ("builder_1", "builder_2", "builder_3")
    # auditor session is still the single batch session; it was resumed for
    # t2's audit, t2's re-audit and t3's audit (t1's was the batch's first).
    assert task2.auditor_session_id == "auditor_1"
    assert ScriptedBatchDriver.resumed == ["auditor_1", "auditor_1", "auditor_1"]
    kinds = report.operation_counts()
    assert kinds["FIX"] == 1


def test_audit_cap_stops_the_batch(make_runner) -> None:  # noqa: ANN001
    ScriptedBatchDriver.reset()
    runner, executor = make_runner(
        results=[
            ok_result("orch_1", build_plan_text(2)),
            ok_result("builder_1", "done"),
            ok_result("auditor_1", verdict_text("NEEDS_FIX", fix_prompt="fix1")),
            ok_result("fix_1", "fixed"),
            ok_result("auditor_1", verdict_text("NEEDS_FIX", fix_prompt="fix2")),
            ok_result("fix_2", "fixed again"),
            ok_result("auditor_1", verdict_text("NEEDS_FIX", fix_prompt="still broken")),
        ]
    )

    report = runner.run_batch(project_brief="x", batch_size=2)

    assert report.outcome is BatchOutcome.BLOCKED
    task1 = executor.controller.state.batch.tasks[0]
    assert task1.state is TaskState.BLOCKED
    assert task1.audit_rounds == 3
    assert executor.controller.state.batch.tasks[1].state is TaskState.PENDING


# -- boundary control ---------------------------------------------------------
def test_pause_at_task_boundary_then_resume_does_not_duplicate(
    make_runner,  # noqa: ANN001
) -> None:
    ScriptedBatchDriver.reset()
    results = [
        ok_result("orch_1", build_plan_text(3)),
        ok_result("builder_1", "t1 done"),
        ok_result("auditor_1", verdict_text("PASS")),
        # interruption requested after op 3 (task 1 fully approved)
        ok_result("builder_2", "t2 done"),
        ok_result("auditor_1", verdict_text("PASS")),
        ok_result("builder_3", "t3 done"),
        ok_result("auditor_1", verdict_text("PASS")),
    ]
    runner, executor = make_runner(results=results)
    ScriptedBatchDriver.hooks[3] = executor.request_pause

    report = runner.run_batch(project_brief="x", batch_size=3)
    assert report.outcome is BatchOutcome.PAUSED
    assert executor.controller.machine.phase is PipelinePhase.PAUSED
    task1 = executor.controller.state.batch.tasks[0]
    assert task1.state is TaskState.APPROVED, "the finished audit result must be persisted"

    # Resume: task 1 must NOT be re-run (queue would underflow if it were).
    ScriptedBatchDriver.hooks = {}
    report2 = runner.run_batch(resume=True)
    assert report2.outcome is BatchOutcome.READY_FOR_FINAL_AUDIT
    assert executor.controller.state.batch.tasks[0].builder_session_id == "builder_1"


def test_stop_at_task_boundary_prevents_the_next_task(make_runner) -> None:  # noqa: ANN001
    ScriptedBatchDriver.reset()
    results = [
        ok_result("orch_1", build_plan_text(3)),
        ok_result("builder_1", "t1 done"),
        ok_result("auditor_1", verdict_text("PASS")),
    ]
    runner, executor = make_runner(results=results)
    ScriptedBatchDriver.hooks[3] = executor.request_stop

    report = runner.run_batch(project_brief="x", batch_size=3)

    assert report.outcome is BatchOutcome.STOPPED
    assert executor.controller.state.batch.status is BatchStatus.STOPPED
    assert executor.controller.state.batch.tasks[1].state is TaskState.PENDING


# -- restart recovery ---------------------------------------------------------
def test_restart_recovery_after_task_two_resumes_without_rework(
    batch_controller, database, registry  # noqa: ANN001
) -> None:
    ScriptedBatchDriver.reset()
    executor = Executor(
        batch_controller,
        registry=registry,
        runner=PermissiveRunner(),
        database=database,
        profile_discovery=discovery_stub,
    )
    batch_controller.attach_executor(executor)
    ScriptedBatchDriver.shared = [
        ok_result("orch_1", build_plan_text(4)),
        ok_result("builder_1", "t1 done"),
        ok_result("auditor_1", verdict_text("PASS")),
        ok_result("builder_2", "t2 done"),
        ok_result("auditor_1", verdict_text("PASS")),
    ]
    # Pause at the boundary right after task 2's audit PASS (op 5): the
    # persisted state is a resumable PAUSED/RUNNING_TASK batch — exactly what
    # a crash mid-batch looks like from the database's point of view.
    ScriptedBatchDriver.hooks[5] = executor.request_pause
    first = BatchRunner(executor).run_batch(project_brief="x", batch_size=4)
    assert first.outcome is BatchOutcome.PAUSED
    workspace_id = batch_controller.state.workspace.workspace_id
    batch_id = batch_controller.state.batch.batch_id

    # -- "restart": only SQLite survives ------------------------------------
    restored = database.load_pipeline_state(workspace_id)
    assert restored is not None and restored.batch is not None
    assert restored.batch.batch_id == batch_id
    assert restored.batch.status is BatchStatus.PAUSED
    assert [t.state.value for t in restored.batch.tasks][:2] == ["APPROVED", "APPROVED"]

    ScriptedBatchDriver.reset()
    ScriptedBatchDriver.shared = [
        ok_result("builder_3", "t3 done"),
        ok_result("auditor_1", verdict_text("PASS")),
        ok_result("builder_4", "t4 done"),
        ok_result("auditor_1", verdict_text("PASS")),
    ]
    fresh_events = EventLog(database)
    fresh_controller = PipelineController(
        database=database, event_log=fresh_events, state=restored
    )
    fresh_executor = Executor(
        fresh_controller,
        registry=registry,
        runner=PermissiveRunner(),
        database=database,
        profile_discovery=discovery_stub,
    )
    fresh_controller.attach_executor(fresh_executor)
    fresh_runner = BatchRunner(fresh_executor)

    # Recovery decides WITHOUT starting anything.
    assert next_task_action(
        batch=fresh_controller.state.batch, phase=fresh_controller.machine.phase
    ).value in ("BUILD",)
    assert fresh_executor.is_running is False

    report = fresh_runner.run_batch(resume=True)
    assert report.outcome is BatchOutcome.READY_FOR_FINAL_AUDIT
    tasks = fresh_controller.state.batch.tasks
    assert [t.state.value for t in tasks] == ["APPROVED"] * 4
    # no rework: original session ids preserved on tasks 1-2
    assert tasks[0].builder_session_id == "builder_1"
    assert tasks[1].builder_session_id == "builder_2"
    assert tasks[0].auditor_session_id == "auditor_1"
    assert tasks[3].builder_session_id == "builder_4"


def test_ready_batch_reloads_from_sqlite_with_plan_and_summary(
    make_runner, database  # noqa: ANN001
) -> None:
    ScriptedBatchDriver.reset()
    runner, executor = make_runner(
        results=[
            ok_result("orch_1", build_plan_text(3)),
            ok_result("builder_1", "t1"),
            ok_result("auditor_1", verdict_text("PASS")),
            ok_result("builder_2", "t2"),
            ok_result("auditor_1", verdict_text("PASS")),
            ok_result("builder_3", "t3"),
            ok_result("auditor_1", verdict_text("PASS")),
        ]
    )
    report = runner.run_batch(project_brief="brief", batch_size=3)
    assert report.outcome is BatchOutcome.READY_FOR_FINAL_AUDIT

    batch_id = executor.controller.state.batch.batch_id
    stored = database.load_batch(batch_id)
    assert stored is not None
    assert stored.status is BatchStatus.READY_FOR_FINAL_AUDIT
    assert stored.phase == "READY_FOR_FINAL_AUDIT"
    assert stored.plan is not None
    assert stored.plan.plan_status == "PLANNED"
    assert stored.plan.orchestrator_session_id == "orch_1"
    assert stored.plan.final_phase == "READY_FOR_FINAL_AUDIT"
    assert stored.plan.batch_summary_json is not None
    # task indices and plan contract survive the SQLite round-trip
    assert [t.index for t in stored.tasks] == [1, 2, 3]
    summary = json.loads(stored.plan.batch_summary_json)
    assert summary["batch_id"] == batch_id
    assert summary["completed_task_count"] == 3
    assert summary["shared_auditor_session_id"] == "auditor_1"
    assert summary["orchestrator_session_id"] == "orch_1"
    assert summary["final_phase"] == "READY_FOR_FINAL_AUDIT"

def test_restart_between_tasks_resumes_the_shared_auditor_session(
    batch_controller, database, registry  # noqa: ANN001
) -> None:
    """Session 008 regression (brief §25/§27): a restart BETWEEN two tasks
    must resume the batch's ONE auditor session for the next task's FIRST
    audit.

    Found by the real mixed-engine acceptance run: the pre-fix restore only
    consulted the current task's own ``auditor_session_id`` — empty for a
    task that had not been audited yet — so a mid-batch restart silently
    opened a SECOND auditor session, violating ``persistent_per_batch``.
    """
    ScriptedBatchDriver.reset()
    executor = Executor(
        batch_controller,
        registry=registry,
        runner=PermissiveRunner(),
        database=database,
        profile_discovery=discovery_stub,
    )
    batch_controller.attach_executor(executor)
    ScriptedBatchDriver.shared = [
        ok_result("orch_1", build_plan_text(2)),
        ok_result("builder_1", "t1 done"),
        ok_result("auditor_1", verdict_text("PASS")),
        # pause right after task 1 is approved (op 3) — the safe boundary
    ]
    ScriptedBatchDriver.hooks[3] = executor.request_pause
    first = BatchRunner(executor).run_batch(project_brief="x", batch_size=2)
    assert first.outcome is BatchOutcome.PAUSED

    workspace_id = batch_controller.state.workspace.workspace_id
    restored = database.load_pipeline_state(workspace_id)
    assert restored is not None and restored.batch is not None
    states = {t.index: t.state for t in restored.batch.tasks}
    assert states[1] is TaskState.APPROVED
    assert states[2] is TaskState.PENDING
    assert restored.batch.tasks[0].auditor_session_id == "auditor_1"

    # -- "restart": only SQLite survives ------------------------------------
    ScriptedBatchDriver.reset()
    ScriptedBatchDriver.shared = [
        ok_result("builder_2", "t2 done"),
        ok_result("auditor_1", verdict_text("PASS")),
    ]
    fresh_events = EventLog(database)
    fresh_controller = PipelineController(
        database=database, event_log=fresh_events, state=restored
    )
    fresh_executor = Executor(
        fresh_controller,
        registry=registry,
        runner=PermissiveRunner(),
        database=database,
        profile_discovery=discovery_stub,
    )
    fresh_controller.attach_executor(fresh_executor)

    report = BatchRunner(fresh_executor).run_batch(resume=True)
    assert report.outcome is BatchOutcome.READY_FOR_FINAL_AUDIT

    tasks = {t.index: t for t in fresh_executor.controller.state.batch.tasks}
    # The SAME auditor session was resumed for task 2's first audit.
    assert ScriptedBatchDriver.resumed == ["auditor_1"], (
        "a restart between tasks must resume the batch's shared auditor session"
    )
    assert tasks[1].auditor_session_id == tasks[2].auditor_session_id == "auditor_1"
    # Builders stay always-new.
    assert tasks[1].builder_session_id != tasks[2].builder_session_id
    assert {t.state for t in tasks.values()} == {TaskState.APPROVED}


def auditor_session_of(tasks, index: int) -> str | None:
    by_index = {t.index: t for t in tasks}
    return by_index[index].auditor_session_id
