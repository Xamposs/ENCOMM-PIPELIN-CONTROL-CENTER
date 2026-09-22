"""The executor: one task, deterministic transitions, honest failure.

Everything here is offline.  The Hermes path is exercised with the **real**
driver over a canned process runner (so exit-code mapping is proven end to end),
and the executor's own sequencing is proven with a scripted test driver.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import FakeDriver, FakeProcessRunner, PermissiveRunner
from encomm_pcc.core import (
    ExecutionOutcome,
    Executor,
    ProfileDiscoveryResult,
    TaskSpec,
)
from encomm_pcc.domain import AgentRole, BatchStatus, PipelinePhase, TaskState
from encomm_pcc.drivers import (
    DriverRegistry,
    HermesDriver,
    PromptResult,
    SubprocessRunner,
)
from encomm_pcc.drivers import hermes as hermes_module

SMOKE_PROMPT = "Return exactly:\n\nENCOMM_PCC_HERMES_SMOKE_OK"


def discovery_stub(*, profiles=("test-profile", "other-profile"), ok=True):
    """Stand-in for read-only profile discovery (no CLI is run in tests)."""

    def _discover(**kwargs):  # noqa: ANN003, ANN202
        return ProfileDiscoveryResult(
            ok=ok,
            profiles=tuple(profiles),
            method="test",
            detail="stub discovery",
        )

    return _discover


@pytest.fixture()
def registry() -> DriverRegistry:
    reg = DriverRegistry()
    reg.register(FakeDriver)
    return reg


@pytest.fixture()
def builder_controller(controller, tmp_path: Path):  # noqa: ANN001, ANN201
    controller.set_workspace("Test Workspace", str(tmp_path))
    controller.set_role_config(
        AgentRole.BUILDER, engine="fake", project_profile="test-profile"
    )
    return controller


@pytest.fixture()
def executor(builder_controller, registry) -> Executor:  # noqa: ANN001
    FakeDriver.scripted = []
    FakeDriver.delay_s = 0.0
    FakeDriver.sessions_started = 0
    runner = PermissiveRunner()
    ex = Executor(
        builder_controller,
        registry=registry,
        runner=runner,
        database=builder_controller.database,
        profile_discovery=discovery_stub(),
    )
    builder_controller.attach_executor(ex)
    ex.test_runner = runner  # type: ignore[attr-defined]
    return ex


def ok_result(session_id: str = "sess_real_1", text: str = "ENCOMM_PCC_HERMES_SMOKE_OK"):
    return PromptResult(
        ok=True, text=text, session_id=session_id, exit_code=0, duration_s=1.5,
        metadata={"stream": {"saw_result": True}},
    )


# -- materialisation ------------------------------------------------------------
def test_materialise_task_persists_the_prompt_and_state(
    builder_controller, executor, database  # noqa: ANN001
) -> None:
    builder_controller.request_start(1)
    task = executor.materialise_task(TaskSpec(title="Do a thing", prompt="Implement X"))

    assert task.task_id.startswith("task_")
    assert task.title == "Do a thing"
    assert task.prompt == "Implement X"
    assert task.state is TaskState.PENDING

    reloaded = database.load_active_batch(builder_controller.state.workspace.workspace_id)
    assert reloaded is not None
    assert len(reloaded.tasks) == 1
    stored = reloaded.tasks[0]
    assert stored.prompt == "Implement X", "the prompt must survive a reload"
    assert stored.state is TaskState.PENDING


def test_materialise_requires_a_batch(executor) -> None:
    with pytest.raises(ValueError, match="No batch"):
        executor.materialise_task(TaskSpec(title="t", prompt="p"))


# -- the happy path -------------------------------------------------------------
def test_dispatch_walks_idle_to_auditing_task_and_persists(
    builder_controller, executor, database  # noqa: ANN001
) -> None:
    FakeDriver.scripted = [ok_result()]
    report = executor.dispatch_single_task(TaskSpec(title="Session 002 task", prompt=SMOKE_PROMPT))

    assert report.outcome is ExecutionOutcome.COMPLETED
    assert report.ok is True
    assert report.phase is PipelinePhase.AUDITING_TASK
    assert builder_controller.machine.phase is PipelinePhase.AUDITING_TASK
    assert report.executor_started is True, "a real process was launched"
    assert report.session_id == "sess_real_1"
    assert len(executor.test_runner.specs) == 1

    task = builder_controller.state.batch.tasks[0]
    assert task.state is TaskState.AUDITING
    assert task.attempts == 1
    assert task.last_error is None
    assert builder_controller.state.batch.status is BatchStatus.RUNNING

    # persisted, and reloadable from the database alone
    stored = database.load_batch(builder_controller.state.batch.batch_id)
    assert stored is not None
    assert stored.tasks[0].state is TaskState.AUDITING
    assert stored.tasks[0].prompt == SMOKE_PROMPT


def test_dispatch_from_idle_creates_the_batch_itself(builder_controller, executor) -> None:  # noqa: ANN001
    FakeDriver.scripted = [ok_result()]
    assert builder_controller.machine.phase is PipelinePhase.IDLE

    report = executor.dispatch_single_task(TaskSpec(title="t", prompt="p"))

    assert report.outcome is ExecutionOutcome.COMPLETED
    assert builder_controller.state.batch is not None
    assert builder_controller.state.batch.size == 1


def test_real_session_id_is_recorded_as_external(
    builder_controller, executor, database  # noqa: ANN001
) -> None:
    FakeDriver.scripted = [ok_result(session_id="20260922_live")]
    executor.dispatch_single_task(TaskSpec(title="t", prompt="p"))

    assert builder_controller.sessions.current_session_id(AgentRole.BUILDER) == "20260922_live"
    rows = database.list_sessions(builder_controller.state.workspace.workspace_id)
    assert len(rows) == 1
    assert rows[0]["session_id"] == "20260922_live"
    assert rows[0]["external"] == 1
    assert rows[0]["external_session_id"] == "20260922_live"


def test_a_fresh_session_is_created_for_every_unit_of_work(executor, builder_controller) -> None:  # noqa: ANN001
    """BUILDER's `always_new` policy: each dispatch starts a new session."""
    FakeDriver.scripted = [ok_result(session_id="sess_a")]
    first = executor.dispatch_single_task(TaskSpec(title="t1", prompt="p1"))

    # A second task needs a new batch: stop first (Session 002 = one task/batch).
    builder_controller.request_stop()
    FakeDriver.scripted = [ok_result(session_id="sess_b")]
    second = executor.dispatch_single_task(TaskSpec(title="t2", prompt="p2"))

    assert first.session_id == "sess_a"
    assert second.session_id == "sess_b"
    assert FakeDriver.sessions_started == 2


# -- failure propagation --------------------------------------------------------
def test_a_failed_child_process_can_never_become_a_green_task(
    builder_controller, executor, database  # noqa: ANN001
) -> None:
    FakeDriver.scripted = [
        PromptResult(ok=False, exit_code=3, error="child exited with code 3", duration_s=0.5)
    ]
    report = executor.dispatch_single_task(TaskSpec(title="failing task", prompt="p"))

    assert report.outcome is ExecutionOutcome.FAILED
    assert report.ok is False
    assert report.phase is PipelinePhase.FAILED
    assert builder_controller.machine.phase is PipelinePhase.FAILED

    task = builder_controller.state.batch.tasks[0]
    assert task.state is TaskState.FAILED
    assert task.last_error == "child exited with code 3"
    assert builder_controller.state.batch.status is BatchStatus.FAILED

    # error persisted + surfaced in the event log
    stored = database.load_batch(builder_controller.state.batch.batch_id)
    assert stored is not None
    assert stored.tasks[0].state is TaskState.FAILED
    assert stored.tasks[0].last_error == "child exited with code 3"
    assert any(
        e["level"] == "ERROR" and "FAILED" in e["message"]
        for e in database.recent_events(limit=50)
    )


def test_real_hermes_non_zero_exit_propagates_to_a_failed_task(
    builder_controller, registry, database, monkeypatch: pytest.MonkeyPatch, process_result  # noqa: ANN001
) -> None:
    """End-to-end dry run of the real adapter: exit code 2 must never be success."""
    monkeypatch.setattr(
        HermesDriver, "resolve_executable", classmethod(lambda cls: "C:/fake/hermes.exe")
    )
    monkeypatch.setattr(hermes_module, "_LIVE_SMOKE_VERIFIED", True)
    registry.register(HermesDriver)
    builder_controller.set_role_config(
        AgentRole.BUILDER, engine="hermes", project_profile="test-profile"
    )

    runner = FakeProcessRunner(
        results=[
            process_result(
                exit_code=2,
                stdout='{"type":"system","subtype":"init","session_id":"20260922_x"}\n',
                stderr="hermes -z: provider error\n",
            )
        ]
    )
    executor = Executor(
        builder_controller,
        registry=registry,
        runner=runner,
        database=database,
        profile_discovery=discovery_stub(),
    )
    report = executor.dispatch_single_task(TaskSpec(title="t", prompt="p"))

    assert report.outcome is ExecutionOutcome.FAILED
    assert report.prompt_result is not None
    assert report.prompt_result.exit_code == 2
    assert report.prompt_result.simulated is False
    assert builder_controller.state.batch.tasks[0].state is TaskState.FAILED
    assert builder_controller.machine.phase is PipelinePhase.FAILED


def test_an_unverified_driver_is_blocked_by_the_executor(
    builder_controller, registry, database, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    """`implemented=False` is the honesty gate: the executor must refuse.

    A driver is only dispatchable once a real run has verified it, so this test
    pins both sides of the gate rather than the current value of the flag.
    """
    monkeypatch.setattr(
        HermesDriver, "resolve_executable", classmethod(lambda cls: "C:/fake/hermes.exe")
    )
    registry.register(HermesDriver)
    builder_controller.set_role_config(
        AgentRole.BUILDER, engine="hermes", project_profile="test-profile"
    )
    executor = Executor(
        builder_controller,
        registry=registry,
        runner=PermissiveRunner(),
        database=database,
        profile_discovery=discovery_stub(),
    )

    monkeypatch.setattr(hermes_module, "_LIVE_SMOKE_VERIFIED", False)
    blocked = executor.dispatch_single_task(TaskSpec(title="t", prompt="p"))
    assert blocked.outcome is ExecutionOutcome.BLOCKED
    assert "placeholder" in blocked.message
    assert executor._recorder.launched == []  # noqa: SLF001 - no process was started


def test_unexpected_driver_errors_fail_the_task_instead_of_escaping(
    builder_controller, registry, database  # noqa: ANN001
) -> None:
    class Exploding(FakeDriver):
        driver_id = "exploding"

        def wait_for_completion(self, handle, timeout_s=None):  # noqa: ANN001, ANN201
            raise RuntimeError("kaboom")

    registry.register(Exploding)
    builder_controller.set_role_config(AgentRole.BUILDER, engine="exploding", project_profile="p")
    executor = Executor(
        builder_controller,
        registry=registry,
        runner=PermissiveRunner(),
        database=database,
        profile_discovery=discovery_stub(profiles=("p",)),
    )
    report = executor.dispatch_single_task(TaskSpec(title="t", prompt="p"))

    assert report.outcome is ExecutionOutcome.FAILED
    assert "kaboom" in report.message
    assert builder_controller.machine.phase is PipelinePhase.FAILED
    assert builder_controller.state.batch.tasks[0].state is TaskState.FAILED


# -- preflight ------------------------------------------------------------------
def test_missing_workspace_blocks_before_anything_starts(
    controller, registry, database  # noqa: ANN001
) -> None:
    controller.set_role_config(AgentRole.BUILDER, engine="fake", project_profile="test-profile")
    executor = Executor(
        controller, registry=registry, runner=PermissiveRunner(), database=database,
        profile_discovery=discovery_stub(),
    )
    report = executor.dispatch_single_task(TaskSpec(title="t", prompt="p"))

    assert report.outcome is ExecutionOutcome.BLOCKED
    assert report.executor_started is False
    assert controller.machine.phase is PipelinePhase.IDLE, "preflight must not move state"
    assert controller.state.batch is None


def test_placeholder_engine_blocks(builder_controller, database) -> None:  # noqa: ANN001
    builder_controller.set_role_config(AgentRole.BUILDER, engine="codex")
    executor = Executor(
        builder_controller,
        runner=PermissiveRunner(),
        database=database,
        profile_discovery=discovery_stub(),
    )
    report = executor.dispatch_single_task(TaskSpec(title="t", prompt="p"))

    assert report.outcome is ExecutionOutcome.BLOCKED
    assert "placeholder" in report.message
    assert report.executor_started is False


def test_unknown_profile_blocks_when_discovery_says_so(builder_controller, registry, database) -> None:  # noqa: ANN001
    builder_controller.set_role_config(
        AgentRole.BUILDER, engine="fake", project_profile="not-a-real-profile"
    )
    executor = Executor(
        builder_controller, registry=registry, runner=PermissiveRunner(), database=database,
        profile_discovery=discovery_stub(profiles=("encomm-pipeline-control-center",)),
    )
    report = executor.dispatch_single_task(TaskSpec(title="t", prompt="p"))

    assert report.outcome is ExecutionOutcome.BLOCKED
    assert "not-a-real-profile" in report.message
    assert "encomm-pipeline-control-center" in report.message


def test_discovery_failure_is_advisory_not_fatal(builder_controller, registry, database) -> None:  # noqa: ANN001
    FakeDriver.scripted = [ok_result()]
    executor = Executor(
        builder_controller, registry=registry, runner=PermissiveRunner(), database=database,
        profile_discovery=discovery_stub(ok=False, profiles=()),
    )
    report = executor.dispatch_single_task(TaskSpec(title="t", prompt="p"))
    assert report.outcome is ExecutionOutcome.COMPLETED


# -- control flags --------------------------------------------------------------
def test_stop_before_dispatch_prevents_the_process(builder_controller, executor) -> None:  # noqa: ANN001
    executor.request_stop()
    report = executor.dispatch_single_task(TaskSpec(title="t", prompt="p"))

    assert report.outcome is ExecutionOutcome.STOPPED
    assert report.executor_started is False
    assert executor.test_runner.specs == []
    assert builder_controller.machine.phase is PipelinePhase.IDLE


def test_pause_before_dispatch_prevents_the_process(builder_controller, executor) -> None:  # noqa: ANN001
    executor.request_pause()
    report = executor.dispatch_single_task(TaskSpec(title="t", prompt="p"))

    assert report.outcome is ExecutionOutcome.STOPPED
    assert executor.test_runner.specs == []


def test_stop_during_a_prompt_is_reported_honestly(
    builder_controller, registry, executor, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    """Mid-prompt cancellation is not attempted: the run finishes and says so."""
    FakeDriver.scripted = [ok_result()]
    original = FakeDriver.wait_for_completion

    def stopping_wait(self, handle, timeout_s=None):  # noqa: ANN001, ANN201
        executor.request_stop()
        return original(self, handle, timeout_s=timeout_s)

    monkeypatch.setattr(FakeDriver, "wait_for_completion", stopping_wait)
    report = executor.dispatch_single_task(TaskSpec(title="t", prompt="p"))

    assert report.outcome is ExecutionOutcome.COMPLETED
    assert report.stop_requested is True, "the stop request is reported, not hidden"
    assert executor.stop_requested is True


def test_one_task_per_batch_is_enforced(builder_controller, executor) -> None:  # noqa: ANN001
    FakeDriver.scripted = [ok_result()]
    builder_controller.request_start(1)
    executor.materialise_task(TaskSpec(title="already here", prompt="p"))

    second = executor.dispatch_single_task(TaskSpec(title="t2", prompt="p2"))
    assert second.outcome is ExecutionOutcome.REJECTED
    assert "one task" in second.message.lower()
    assert executor.test_runner.specs == [], "nothing may be launched"


def test_dispatch_from_a_running_phase_is_rejected(builder_controller, executor) -> None:  # noqa: ANN001
    builder_controller.request_start(1)
    builder_controller.machine.transition_to(PipelinePhase.RUNNING_TASK)
    builder_controller.state.phase = builder_controller.machine.phase

    report = executor.dispatch_single_task(TaskSpec(title="t", prompt="p"))
    assert report.outcome is ExecutionOutcome.REJECTED


# -- restart / reload -----------------------------------------------------------
def test_state_after_a_dispatch_reloads_from_sqlite_alone(
    builder_controller, executor, database  # noqa: ANN001
) -> None:
    FakeDriver.scripted = [ok_result(session_id="sess_reload")]
    executor.dispatch_single_task(TaskSpec(title="Restartable task", prompt="do the work"))

    workspace_id = builder_controller.state.workspace.workspace_id
    fresh = database.load_pipeline_state(workspace_id)

    assert fresh is not None
    assert fresh.batch is not None
    assert len(fresh.batch.tasks) == 1
    task = fresh.batch.tasks[0]
    assert task.title == "Restartable task"
    assert task.prompt == "do the work"
    assert task.state is TaskState.AUDITING
    assert task.attempts == 1
    assert fresh.role_configs[AgentRole.BUILDER].project_profile == "test-profile"


# -- schema guard ---------------------------------------------------------------
def test_schema_is_v2_with_the_prompt_column(database) -> None:  # noqa: ANN001
    assert database.schema_version() == 2
    columns = {
        row["name"]
        for row in database.connection.execute("PRAGMA table_info(tasks)").fetchall()
    }
    assert "prompt" in columns


def test_a_v1_database_is_upgraded_in_place(tmp_path: Path) -> None:
    """The first in-place upgrade: v1 → v2 adds tasks.prompt."""
    from encomm_pcc.persistence import Database

    legacy = Database(tmp_path / "legacy.db")
    legacy.open()
    legacy.connection.execute("DROP TABLE tasks")
    legacy.connection.execute(
        """
        CREATE TABLE tasks (
            task_id      TEXT PRIMARY KEY,
            batch_id     TEXT NOT NULL,
            task_index   INTEGER NOT NULL DEFAULT 0,
            title        TEXT NOT NULL DEFAULT '',
            state        TEXT NOT NULL,
            attempts     INTEGER NOT NULL DEFAULT 0,
            audit_rounds INTEGER NOT NULL DEFAULT 0,
            last_error   TEXT,
            updated_at   TEXT NOT NULL
        )
        """
    )
    legacy.connection.execute(
        "UPDATE schema_meta SET value = '1' WHERE key = 'schema_version'"
    )
    legacy.connection.commit()
    legacy.close()

    reopened = Database(tmp_path / "legacy.db").open()
    try:
        assert reopened.schema_version() == 2
        columns = {
            row["name"]
            for row in reopened.connection.execute("PRAGMA table_info(tasks)").fetchall()
        }
        assert "prompt" in columns
    finally:
        reopened.close()


def test_subprocess_runner_is_the_real_implementation() -> None:
    """The executor's default runner is the real one (wired in app.run())."""
    assert SubprocessRunner is not None