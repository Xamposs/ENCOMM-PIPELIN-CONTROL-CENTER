"""Pipeline controller: state transitions, control surface, persistence."""

from __future__ import annotations

import pytest

from encomm_pcc.core import (
    DEFAULT_BATCH_SIZE,
    MAX_BATCH_SIZE,
    ControlOutcome,
    PipelineController,
    placeholder_role_config,
)
from encomm_pcc.domain import AgentRole, BatchStatus, PipelinePhase, SessionPolicy
from encomm_pcc.persistence import Database


def test_initial_state_is_idle_with_placeholder_configs(controller: PipelineController) -> None:
    assert controller.machine.phase is PipelinePhase.IDLE
    assert controller.state.batch is None
    assert set(controller.state.role_configs) == set(AgentRole)
    assert controller.role_config(AgentRole.BUILDER).session_policy is SessionPolicy.ALWAYS_NEW


def test_placeholder_configs_match_the_brief(controller: PipelineController) -> None:
    assert controller.role_config(AgentRole.BUILDER).engine == "hermes"
    assert controller.role_config(AgentRole.TASK_AUDITOR).engine == "hermes"
    assert controller.role_config(AgentRole.FINAL_AUDITOR).same_as_orchestrator is True
    for role in AgentRole:
        assert placeholder_role_config(role).role is role


# -- start -------------------------------------------------------------------
def test_start_moves_to_planning_and_creates_a_batch(controller: PipelineController) -> None:
    result = controller.request_start(5)

    assert result.outcome is ControlOutcome.OK
    assert result.phase is PipelinePhase.PLANNING_BATCH
    assert controller.machine.phase is PipelinePhase.PLANNING_BATCH
    assert controller.state.batch is not None
    assert controller.state.batch.size == 5
    assert controller.state.batch.status is BatchStatus.CREATED


def test_start_never_claims_the_executor_ran(controller: PipelineController) -> None:
    result = controller.request_start(3)
    assert result.executor_started is False
    assert "not implemented" in result.message or "state only" in result.message

    messages = [r.message for r in controller.events.history()]
    assert any("Executor is not implemented" in m for m in messages)


def test_start_is_rejected_outside_idle(controller: PipelineController) -> None:
    controller.request_start(2)
    second = controller.request_start(2)
    assert second.outcome is ControlOutcome.REJECTED
    assert not second.ok


def test_start_defaults_to_five(controller: PipelineController) -> None:
    controller.request_start()
    assert controller.state.batch is not None
    assert controller.state.batch.size == DEFAULT_BATCH_SIZE == 5


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(-4, 1), (0, 1), (1, 1), (5, 5), (7, 5), (50, 5), (999, MAX_BATCH_SIZE)],
)
def test_batch_size_is_clamped(controller: PipelineController, requested: int, expected: int) -> None:
    controller.request_start(requested)
    assert controller.state.batch is not None
    assert controller.state.batch.size == expected


# -- pause / resume / stop -------------------------------------------------------
def test_pause_then_resume_returns_to_the_previous_phase(controller: PipelineController) -> None:
    controller.request_start(5)
    paused = controller.request_pause()
    assert paused.outcome is ControlOutcome.OK
    assert controller.machine.phase is PipelinePhase.PAUSED
    assert controller.state.batch is not None
    assert controller.state.batch.status is BatchStatus.PAUSED

    resumed = controller.request_resume()
    assert resumed.outcome is ControlOutcome.OK
    assert controller.machine.phase is PipelinePhase.PLANNING_BATCH
    assert controller.state.batch.status is BatchStatus.RUNNING


def test_pause_is_rejected_from_idle(controller: PipelineController) -> None:
    result = controller.request_pause()
    assert result.outcome is ControlOutcome.REJECTED
    assert controller.machine.phase is PipelinePhase.IDLE


def test_resume_is_rejected_when_not_paused(controller: PipelineController) -> None:
    assert controller.request_resume().outcome is ControlOutcome.REJECTED


def test_stop_returns_to_idle_and_marks_the_batch_stopped(controller: PipelineController) -> None:
    controller.request_start(4)
    batch = controller.state.batch
    assert batch is not None

    result = controller.request_stop()
    assert result.outcome is ControlOutcome.OK
    assert controller.machine.phase is PipelinePhase.IDLE
    assert batch.status is BatchStatus.STOPPED


def test_stop_from_idle_is_a_no_op(controller: PipelineController) -> None:
    result = controller.request_stop()
    assert result.outcome is ControlOutcome.OK
    assert controller.machine.phase is PipelinePhase.IDLE


# -- role configuration -----------------------------------------------------------
def test_set_role_config_updates_and_persists(controller: PipelineController) -> None:
    controller.set_role_config(
        AgentRole.BUILDER,
        engine="codex",
        project_profile="builder-x",
        provider="openrouter",
        model="m-1",
    )
    config = controller.role_config(AgentRole.BUILDER)
    assert (config.engine, config.project_profile, config.model) == ("codex", "builder-x", "m-1")


def test_set_role_config_coerces_session_policy(controller: PipelineController) -> None:
    controller.set_role_config(AgentRole.FINAL_AUDITOR, session_policy="persistent")
    assert controller.role_config(AgentRole.FINAL_AUDITOR).session_policy is SessionPolicy.PERSISTENT


def test_set_role_config_rejects_unknown_fields(controller: PipelineController) -> None:
    with pytest.raises(ValueError, match="Unknown role config field"):
        controller.set_role_config(AgentRole.BUILDER, nonsense="x")


def test_set_workspace_updates_and_logs(controller: PipelineController) -> None:
    workspace = controller.set_workspace("My Repo", r"C:\repo")
    assert workspace.name == "My Repo"
    assert workspace.repo_path == r"C:\repo"
    assert any("Workspace set" in r.message for r in controller.events.history())


def test_blank_workspace_name_falls_back(controller: PipelineController) -> None:
    assert controller.set_workspace("   ", "").name == "New Workspace"


# -- driver / session introspection ------------------------------------------------
def test_driver_capabilities_are_exposed(controller: PipelineController) -> None:
    rows = controller.driver_capabilities()
    assert {r["driver_id"] for r in rows} == {"codex", "generic_cli", "hermes"}
    by_id = {r["driver_id"]: r for r in rows}
    # Session 007: GenericCli is a real, stateless driver (it cannot launch
    # anything without the operator's stored configuration).
    assert by_id["generic_cli"]["implemented"] is True
    # Codex gates its flag on live evidence (D-018)…
    from encomm_pcc.drivers.codex import _LIVE_SMOKE_VERIFIED

    assert by_id["codex"]["implemented"] == _LIVE_SMOKE_VERIFIED
    # …and Hermes reports its own verification state, which is a bool either way.
    assert isinstance(by_id["hermes"]["implemented"], bool)


def test_probe_session_decision_is_read_only(controller: PipelineController) -> None:
    decision = controller.probe_session_decision(AgentRole.BUILDER)
    assert decision is not None
    assert decision.action.value == "NEW"  # BUILDER policy is always_new
    assert controller.sessions.current_session_id(AgentRole.BUILDER) is None


def test_probe_session_decision_without_engine_returns_none(controller: PipelineController) -> None:
    controller.set_role_config(AgentRole.BUILDER, engine="")
    assert controller.probe_session_decision(AgentRole.BUILDER) is None


def test_final_auditor_can_inherit_the_orchestrator_engine(controller: PipelineController) -> None:
    controller.set_role_config(AgentRole.ORCHESTRATOR, engine="codex")
    controller.set_role_config(AgentRole.FINAL_AUDITOR, engine="hermes", same_as_orchestrator=True)
    assert controller.state.resolved_engine_for(AgentRole.FINAL_AUDITOR) == "codex"

    controller.set_role_config(AgentRole.FINAL_AUDITOR, same_as_orchestrator=False)
    assert controller.state.resolved_engine_for(AgentRole.FINAL_AUDITOR) == "hermes"


# -- persistence ---------------------------------------------------------------------
def test_state_is_persisted_and_reloadable(database: Database, controller: PipelineController) -> None:
    controller.set_workspace("Persisted", r"C:\persisted")
    controller.set_role_config(AgentRole.ORCHESTRATOR, engine="codex")
    controller.request_start(4)

    workspace_id = controller.state.workspace.workspace_id
    reloaded = database.load_pipeline_state(workspace_id)
    assert reloaded is not None
    assert reloaded.workspace.name == "Persisted"
    assert reloaded.role_configs[AgentRole.ORCHESTRATOR].engine == "codex"
    assert reloaded.batch is not None and reloaded.batch.size == 4


def test_events_are_written_to_the_database(database: Database, controller: PipelineController) -> None:
    controller.request_start(2)
    assert database.event_count() > 0
    assert any("Batch" in e["message"] for e in database.recent_events(limit=20))


def test_snapshot_contains_everything_the_ui_needs(controller: PipelineController) -> None:
    controller.request_start(5)
    snapshot = controller.snapshot()

    assert snapshot["phase"] == PipelinePhase.PLANNING_BATCH.value
    assert set(snapshot["role_configs"]) == {r.value for r in AgentRole}
    assert snapshot["batch"]["size"] == 5
    assert snapshot["workspace"]["name"] == controller.state.workspace.name
    assert snapshot["drivers"]
    assert "batch_generation" in snapshot["sessions"]


def test_apply_placeholders_resets_roles(controller: PipelineController) -> None:
    controller.set_role_config(AgentRole.BUILDER, engine="codex", project_profile="custom")
    controller.apply_placeholders()
    config = controller.role_config(AgentRole.BUILDER)
    assert config.engine == "hermes"
    assert config.project_profile == "builder-default"


def test_controller_works_without_a_database_or_event_log() -> None:
    bare = PipelineController()
    result = bare.request_start(1)
    assert result.outcome is ControlOutcome.OK
    assert bare.state.batch is not None


def test_controller_keeps_the_supplied_event_log_even_when_empty(
    database: Database, event_log  # noqa: ANN001
) -> None:
    """Regression: EventLog defines __len__, so an empty log is falsy.

    A truthiness-based default would swap in a NullEventLog and silently drop
    every event.
    """
    assert len(event_log) == 0
    controller = PipelineController(database=database, event_log=event_log)
    assert controller.events is event_log

    controller.request_start(1)
    assert len(event_log) > 0
    assert database.event_count() > 0
