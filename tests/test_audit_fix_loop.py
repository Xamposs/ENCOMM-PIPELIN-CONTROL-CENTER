"""The Session 003 audit/fix loop: verdicts, isolation, cap, recovery.

Everything here is offline.  A scripted loop driver shares its results queue
and its start/resume logs across instances, because the executor creates a
fresh driver per dispatch — which is exactly how the session-isolation contract
is exercised: the auditor session is resumed, the fix Builder session is new.
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
    ExecutionOutcome,
    Executor,
    MAX_AUDIT_ROUNDS,
    PipelineController,
    ProfileDiscoveryResult,
    TaskNextAction,
    TaskSpec,
)
from encomm_pcc.core.executor import next_task_action
from encomm_pcc.domain import (
    AgentRole,
    BatchStatus,
    PipelinePhase,
    TaskState,
)
from encomm_pcc.drivers import (
    DriverRegistry,
    DriverSession,
    ProcessSpec,
    PromptResult,
    SessionRequest,
)


def discovery_stub(*, profiles=("test-profile",), ok=True):
    def _discover(**kwargs):  # noqa: ANN003, ANN202
        return ProfileDiscoveryResult(
            ok=ok, profiles=tuple(profiles), method="test", detail="stub"
        )

    return _discover


def envelope(payload: dict) -> str:
    return f"{AUDIT_ENVELOPE_START}\n{json.dumps(payload)}\n{AUDIT_ENVELOPE_END}"


def verdict_text(value: str, *, summary: str = "summary", fix_prompt: str = "") -> str:
    return envelope(
        {
            "verdict": value,
            "summary": summary,
            "findings": (
                []
                if value == "PASS"
                else [{"severity": "high", "message": "defect found", "evidence": "x"}]
            ),
            "fix_prompt": fix_prompt,
        }
    )


class ScriptedLoopDriver:
    """Fake driver whose state is SHARED across every instance.

    The executor instantiates a driver per dispatch via the registry, so a
    cross-instance queue is the only way to script a multi-step audit→fix→
    re-audit sequence and to record who started/resumed what.
    """

    driver_id = "loop"
    display_name = "Loop Engine"
    executables = ("python",)

    shared: list[PromptResult] = []
    starts: int = 0
    resumed: list[str] = []
    prompts: list[str] = []
    _runner = None

    @classmethod
    def reset(cls) -> None:
        cls.shared = []
        cls.starts = 0
        cls.resumed = []
        cls.prompts = []

    @classmethod
    def capabilities(cls):
        from encomm_pcc.drivers import DriverCapabilities

        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name=cls.display_name,
            supports_sessions=True,
            supports_resume=True,
            implemented=True,
            notes="Test double for the audit/fix loop.",
        )

    def __init__(self, runner=None) -> None:  # noqa: ANN001
        self._runner = runner

    def start_session(self, request: SessionRequest) -> DriverSession:
        type(self).starts += 1
        return DriverSession(
            driver_id=self.driver_id,
            role=request.role,
            session_id=None,
            metadata={"profile": request.project_profile},
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
            self._runner.run(ProcessSpec(argv=["loop-engine"], timeout_s=timeout_s))
        if not type(self).shared:
            raise AssertionError("ScriptedLoopDriver: no scripted PromptResult left")
        result = type(self).shared.pop(0)
        type(self).prompts.append(handle.prompt)
        if result.session_id:
            handle.session.session_id = result.session_id
            handle.session.external = True
        return result

    def cancel(self) -> bool:
        return False


def ok_result(session_id: str, text: str) -> PromptResult:
    return PromptResult(
        ok=True, text=text, session_id=session_id, exit_code=0, duration_s=0.2,
        metadata={"stream": {"saw_result": True}},
    )


@pytest.fixture()
def registry() -> DriverRegistry:
    reg = DriverRegistry()
    reg.register(ScriptedLoopDriver)
    return reg


@pytest.fixture()
def loop_controller(controller, tmp_path: Path):  # noqa: ANN001
    controller.set_workspace("Loop Workspace", str(tmp_path))
    controller.set_role_config(
        AgentRole.BUILDER, engine="loop", project_profile="test-profile"
    )
    controller.set_role_config(
        AgentRole.TASK_AUDITOR, engine="loop", project_profile="test-profile"
    )
    return controller


@pytest.fixture()
def executor(loop_controller, registry):  # noqa: ANN001
    ScriptedLoopDriver.reset()
    ex = Executor(
        loop_controller,
        registry=registry,
        runner=PermissiveRunner(),
        database=loop_controller.database,
        profile_discovery=discovery_stub(),
    )
    loop_controller.attach_executor(ex)
    return ex


def seed(executor) -> None:  # noqa: ANN001
    executor.prepare_task_for_audit(
        TaskSpec(title="defective task", prompt="Make the tests pass.")
    )


# -- the happy NEEDS_FIX → fix → PASS loop -------------------------------------
def test_initial_audit_needs_fix_moves_to_fix_required(executor) -> None:  # noqa: ANN001
    seed(executor)
    ScriptedLoopDriver.shared = [
        ok_result("auditor_001", verdict_text("NEEDS_FIX", fix_prompt="fix add()"))
    ]

    report = executor.run_task_audit()

    assert report.outcome is ExecutionOutcome.NEEDS_FIX
    assert report.ok is False, "NEEDS_FIX is not a green outcome"
    assert report.audit_verdict is not None
    assert executor.controller.machine.phase is PipelinePhase.FIX_REQUIRED

    task = executor.controller.state.batch.tasks[0]
    assert task.state is TaskState.FIX_REQUIRED
    assert task.audit_rounds == 1
    assert task.latest_verdict == "NEEDS_FIX"
    assert task.fix_prompt == "fix add()"
    assert task.auditor_session_id == "auditor_001"
    assert executor.next_action() is TaskNextAction.FIX


def test_pass_verdict_approves_task_and_completes_the_batch(executor) -> None:  # noqa: ANN001
    seed(executor)
    ScriptedLoopDriver.shared = [ok_result("auditor_001", verdict_text("PASS"))]

    report = executor.run_task_audit()

    assert report.outcome is ExecutionOutcome.COMPLETED
    assert report.ok is True
    assert executor.controller.machine.phase is PipelinePhase.BATCH_COMPLETE

    task = executor.controller.state.batch.tasks[0]
    assert task.state is TaskState.APPROVED
    assert task.latest_verdict == "PASS"
    assert executor.controller.state.batch.status is BatchStatus.COMPLETE
    assert executor.next_action() is TaskNextAction.COMPLETE


def test_full_loop_isolates_builder_and_resumes_auditor(executor) -> None:  # noqa: ANN001
    """The central Session 003 contract, proven offline:
    auditor session A, fix Builder session C (new), re-audit resumes A."""
    seed(executor)
    ScriptedLoopDriver.shared = [
        ok_result("auditor_001", verdict_text("NEEDS_FIX", fix_prompt="make add() add")),
        ok_result("fix_002", verdict_text("PASS", summary="fixed")),
        ok_result("auditor_001", verdict_text("PASS", summary="re-audit ok")),
    ]

    assert executor.run_task_audit().outcome is ExecutionOutcome.NEEDS_FIX
    fix_report = executor.run_task_fix()
    assert fix_report.outcome is ExecutionOutcome.COMPLETED

    task = executor.controller.state.batch.tasks[0]
    # fix used a NEW Builder session; re-audit will resume the auditor session
    assert task.fix_session_id == "fix_002"
    assert task.fix_session_id != task.auditor_session_id, "fix Builder must be a distinct session"

    reaudit = executor.run_task_audit()
    assert reaudit.outcome is ExecutionOutcome.COMPLETED
    assert reaudit.audit_verdict is not None
    assert reaudit.audit_verdict.verdict.value == "PASS"
    assert reaudit.session_id == "auditor_001", "the re-audit resumed the SAME auditor session"

    assert ScriptedLoopDriver.resumed == ["auditor_001"]
    assert task.state is TaskState.APPROVED
    assert executor.controller.machine.phase is PipelinePhase.BATCH_COMPLETE
    # session isolation: the auditor session id never changes across the loop
    assert task.auditor_session_id == "auditor_001"


def test_fix_runs_in_a_fresh_builder_session(executor) -> None:  # noqa: ANN001
    seed(executor)
    ScriptedLoopDriver.shared = [
        ok_result("auditor_001", verdict_text("NEEDS_FIX", fix_prompt="fix")),
        ok_result("fix_new_session", verdict_text("PASS")),
    ]
    executor.run_task_audit()
    starts_before = ScriptedLoopDriver.starts
    executor.run_task_fix()

    task = executor.controller.state.batch.tasks[0]
    assert task.fix_session_id == "fix_new_session"
    assert task.fix_session_id != task.auditor_session_id
    assert ScriptedLoopDriver.starts >= starts_before + 1, "the fix started a new session"


def test_reaudit_resumes_the_same_auditor_session(executor) -> None:  # noqa: ANN001
    seed(executor)
    ScriptedLoopDriver.shared = [
        ok_result("auditor_a", verdict_text("NEEDS_FIX", fix_prompt="fix")),
        ok_result("fix_b", verdict_text("PASS")),
    ]
    executor.run_task_audit()
    executor.run_task_fix()
    executor.run_task_audit()

    assert ScriptedLoopDriver.resumed == ["auditor_a"], "only the auditor session is resumed"
    assert executor.controller.state.batch.tasks[0].auditor_session_id == "auditor_a"


# -- the hard round cap ---------------------------------------------------------
def test_max_audit_rounds_is_enforced_and_blocks(executor) -> None:  # noqa: ANN001
    """Audit1 NEEDS_FIX → fix1 → Audit2 NEEDS_FIX → fix2 → Audit3 NEEDS_FIX → BLOCKED."""
    assert MAX_AUDIT_ROUNDS == 3
    seed(executor)
    ScriptedLoopDriver.shared = [
        ok_result("auditor_1", verdict_text("NEEDS_FIX", fix_prompt="fix")),
        ok_result("fix_1", verdict_text("PASS")),
        ok_result("auditor_2", verdict_text("NEEDS_FIX", fix_prompt="fix again")),
        ok_result("fix_2", verdict_text("PASS")),
        ok_result("auditor_3", verdict_text("NEEDS_FIX", fix_prompt="still broken")),
    ]

    executor.run_task_audit()
    executor.run_task_fix()
    executor.run_task_audit()
    executor.run_task_fix()
    final = executor.run_task_audit()

    assert final.outcome is ExecutionOutcome.BLOCKED
    assert "MAX_AUDIT_ROUNDS" in final.message or "Max audit rounds" in final.message
    task = executor.controller.state.batch.tasks[0]
    assert task.state is TaskState.BLOCKED
    assert task.audit_rounds == MAX_AUDIT_ROUNDS
    assert executor.controller.machine.phase is PipelinePhase.BLOCKED
    assert executor.next_action() is TaskNextAction.BLOCKED

    # a further fix is refused in ANY non-green way (no infinite loop is
    # reachable): the pipeline is BLOCKED, so the phase gate rejects the run
    # before anything can start.
    refused = executor.run_task_fix()
    assert refused.outcome in (ExecutionOutcome.BLOCKED, ExecutionOutcome.REJECTED)
    assert refused.executor_started is False


def test_audit_beyond_cap_is_blocked(executor) -> None:  # noqa: ANN001
    seed(executor)
    task = executor.controller.state.batch.tasks[0]
    task.audit_rounds = MAX_AUDIT_ROUNDS  # simulate a cap-exhausted stored task
    ScriptedLoopDriver.shared = []
    report = executor.run_task_audit()
    assert report.outcome is ExecutionOutcome.BLOCKED
    assert task.state is TaskState.BLOCKED
    assert ScriptedLoopDriver.shared == [], "no further audit may run beyond the cap"


# -- failure cases never go green ------------------------------------------------
def test_malformed_auditor_output_blocks_never_pass(executor) -> None:  # noqa: ANN001
    seed(executor)
    ScriptedLoopDriver.shared = [
        ok_result("auditor_x", "This is not a verdict. Just prose with no JSON object.")
    ]
    report = executor.run_task_audit()

    assert report.outcome is ExecutionOutcome.BLOCKED
    assert "Malformed auditor output" in report.message
    task = executor.controller.state.batch.tasks[0]
    assert task.state is TaskState.BLOCKED
    assert task.latest_verdict is None, "a malformed verdict must never be recorded as PASS"
    assert executor.controller.machine.phase is PipelinePhase.BLOCKED


def test_auditor_process_failure_fails_the_task(executor) -> None:  # noqa: ANN001
    seed(executor)
    ScriptedLoopDriver.shared = [
        PromptResult(ok=False, exit_code=3, error="auditor crashed", duration_s=0.5)
    ]
    report = executor.run_task_audit()

    assert report.outcome is ExecutionOutcome.FAILED
    task = executor.controller.state.batch.tasks[0]
    assert task.state is TaskState.FAILED
    assert task.last_error == "auditor crashed"
    assert executor.controller.machine.phase is PipelinePhase.FAILED


def test_fix_process_failure_fails_the_task(executor) -> None:  # noqa: ANN001
    seed(executor)
    ScriptedLoopDriver.shared = [
        ok_result("auditor_1", verdict_text("NEEDS_FIX", fix_prompt="fix")),
        PromptResult(ok=False, exit_code=3, error="fixer crashed", duration_s=0.5),
    ]
    executor.run_task_audit()
    report = executor.run_task_fix()

    assert report.outcome is ExecutionOutcome.FAILED
    task = executor.controller.state.batch.tasks[0]
    assert task.state is TaskState.FAILED
    assert executor.controller.machine.phase is PipelinePhase.FAILED


def test_fix_without_a_fix_prompt_is_blocked(executor) -> None:  # noqa: ANN001
    seed(executor)
    ScriptedLoopDriver.shared = [
        ok_result("auditor_1", verdict_text("NEEDS_FIX", fix_prompt="fix"))
    ]
    executor.run_task_audit()
    task = executor.controller.state.batch.tasks[0]
    task.fix_prompt = None  # defensive: a persisted task without a correction

    report = executor.run_task_fix()
    assert report.outcome is ExecutionOutcome.BLOCKED
    assert "fix_prompt" in report.message


def test_phase_gates_are_enforced(executor) -> None:  # noqa: ANN001
    # audit from IDLE is rejected
    rejected = executor.run_task_audit()
    assert rejected.outcome is ExecutionOutcome.REJECTED
    # fix from IDLE is rejected
    rejected = executor.run_task_fix()
    assert rejected.outcome is ExecutionOutcome.REJECTED


def test_fix_from_a_non_fix_phase_is_rejected(executor) -> None:  # noqa: ANN001
    seed(executor)
    ScriptedLoopDriver.shared = [ok_result("auditor_1", verdict_text("PASS"))]
    executor.run_task_audit()  # now BATCH_COMPLETE
    report = executor.run_task_fix()
    assert report.outcome is ExecutionOutcome.REJECTED


# -- persistence / recovery ------------------------------------------------------
def test_verdict_state_persists_and_reloads(executor, database) -> None:  # noqa: ANN001
    seed(executor)
    ScriptedLoopDriver.shared = [
        ok_result("auditor_p", verdict_text("NEEDS_FIX", fix_prompt="persisted fix"))
    ]
    executor.run_task_audit()

    controller = executor.controller
    workspace_id = controller.state.workspace.workspace_id
    fresh = database.load_pipeline_state(workspace_id)
    assert fresh is not None
    task = fresh.batch.tasks[0]
    assert task.state is TaskState.FIX_REQUIRED
    assert task.audit_rounds == 1
    assert task.latest_verdict == "NEEDS_FIX"
    assert task.fix_prompt == "persisted fix"
    assert task.auditor_session_id == "auditor_p"
    assert task.verdict_json is not None and "NEEDS_FIX" in task.verdict_json


def test_recovery_resumes_the_auditor_session_after_restart(
    executor, database, controller  # noqa: ANN001
) -> None:
    """Restart proof: a brand-new controller+executor over the SAME database
    must recover the persisted state AND resume the persisted auditor session
    (persistent_per_batch) for a re-audit — without any in-memory leftovers."""
    seed(executor)
    ScriptedLoopDriver.shared = [
        ok_result("auditor_live", verdict_text("NEEDS_FIX", fix_prompt="fix")),
        ok_result("fix_new", verdict_text("PASS")),
    ]
    executor.run_task_audit()
    executor.run_task_fix()
    workspace_id = executor.controller.state.workspace.workspace_id

    # -- "restart": a fresh process, only SQLite survives --------------------
    restored = database.load_pipeline_state(workspace_id)
    assert restored is not None
    assert restored.batch is not None
    task = restored.batch.tasks[0]
    assert task.state is TaskState.AUDITING
    assert task.audit_rounds == 1  # one audit so far; the fix does not add a round
    assert task.auditor_session_id == "auditor_live"
    assert task.latest_verdict == "NEEDS_FIX"

    ScriptedLoopDriver.reset()
    ScriptedLoopDriver.shared = [
        ok_result("auditor_live", verdict_text("PASS", summary="re-audit ok"))
    ]
    reg = DriverRegistry()
    reg.register(ScriptedLoopDriver)
    fresh_events = EventLog(database)
    fresh_controller = PipelineController(database=database, event_log=fresh_events, state=restored)
    fresh_executor = Executor(
        fresh_controller,
        registry=reg,
        runner=PermissiveRunner(),
        database=database,
        profile_discovery=discovery_stub(),
    )
    fresh_controller.attach_executor(fresh_executor)

    # Recovery identifies the next action WITHOUT starting anything.
    assert fresh_executor.next_action() is TaskNextAction.RE_AUDIT
    assert fresh_executor.is_running is False, "recovery must not auto-resume work"

    report = fresh_executor.run_task_audit()
    assert report.outcome is ExecutionOutcome.COMPLETED
    assert report.session_id == "auditor_live", "restart recovery resumed the auditor session"
    assert ScriptedLoopDriver.resumed == ["auditor_live"]
    assert fresh_executor.controller.state.batch.tasks[0].state is TaskState.APPROVED


# -- next_action is deterministic and drives the UI ------------------------------
def test_next_action_through_the_loop(executor) -> None:  # noqa: ANN001
    seed(executor)
    assert executor.next_action() is TaskNextAction.AUDIT
    ScriptedLoopDriver.shared = [
        ok_result("a1", verdict_text("NEEDS_FIX", fix_prompt="fix")),
        ok_result("f1", verdict_text("PASS")),
        ok_result("a1", verdict_text("PASS", summary="re-audit ok")),
    ]
    executor.run_task_audit()
    assert executor.next_action() is TaskNextAction.FIX
    executor.run_task_fix()
    assert executor.next_action() is TaskNextAction.RE_AUDIT
    executor.run_task_audit()
    assert executor.next_action() is TaskNextAction.COMPLETE


def test_next_action_idle_without_a_task(executor) -> None:  # noqa: ANN001
    action = next_task_action(batch=None, phase=PipelinePhase.IDLE)
    assert action is TaskNextAction.IDLE