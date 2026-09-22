"""Domain model behaviour: roles, session policies, serialisation."""

from __future__ import annotations

import pytest

from encomm_pcc.domain import (
    AgentRole,
    AgentRoleConfig,
    BatchState,
    BatchStatus,
    PipelinePhase,
    PipelineState,
    SessionPolicy,
    TaskState,
    TaskStateRecord,
    WorkspaceConfig,
    default_session_policy,
)


# -- roles -----------------------------------------------------------------
def test_all_four_roles_exist() -> None:
    assert {r.value for r in AgentRole} == {
        "ORCHESTRATOR",
        "BUILDER",
        "TASK_AUDITOR",
        "FINAL_AUDITOR",
    }


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (AgentRole.ORCHESTRATOR, SessionPolicy.PERSISTENT_OPTIONAL),
        (AgentRole.BUILDER, SessionPolicy.ALWAYS_NEW),
        (AgentRole.TASK_AUDITOR, SessionPolicy.PERSISTENT_PER_BATCH),
        (AgentRole.FINAL_AUDITOR, SessionPolicy.CONFIGURABLE),
    ],
)
def test_brief_mandated_default_session_policies(role: AgentRole, expected: SessionPolicy) -> None:
    assert default_session_policy(role) is expected


def test_role_config_accepts_plain_strings() -> None:
    config = AgentRoleConfig(role="BUILDER", session_policy="always_new")
    assert config.role is AgentRole.BUILDER
    assert config.session_policy is SessionPolicy.ALWAYS_NEW


def test_role_config_rejects_unknown_role() -> None:
    with pytest.raises(ValueError):
        AgentRoleConfig(role="NOT_A_ROLE")


def test_role_config_round_trip() -> None:
    original = AgentRoleConfig(
        role=AgentRole.TASK_AUDITOR,
        engine="hermes",
        project_profile="auditor",
        provider="openrouter",
        model="deepseek/deepseek-v4.1-flash",
        session_policy=SessionPolicy.PERSISTENT_PER_BATCH,
        session_id="sess_abc12345",
        extra={"note": "round-trip"},
    )
    restored = AgentRoleConfig.from_dict(original.to_dict())
    assert restored.to_dict() == original.to_dict()


def test_role_config_placeholder_detection() -> None:
    assert AgentRoleConfig(role=AgentRole.BUILDER).is_placeholder()
    assert not AgentRoleConfig(
        role=AgentRole.BUILDER, engine="hermes", project_profile="builder-default"
    ).is_placeholder()


# -- workspace --------------------------------------------------------------
def test_workspace_round_trip() -> None:
    workspace = WorkspaceConfig(name="ENCOMM ERP", repo_path=r"C:\Users\xampos\Desktop\ERP")
    restored = WorkspaceConfig.from_dict(workspace.to_dict())
    assert restored == workspace


def test_workspace_reports_missing_path(tmp_path) -> None:
    assert not WorkspaceConfig(name="x", repo_path="").exists()
    assert not WorkspaceConfig(name="x", repo_path=str(tmp_path / "nope")).exists()
    assert WorkspaceConfig(name="x", repo_path=str(tmp_path)).exists()


# -- batch / task -------------------------------------------------------------
def test_batch_progress_and_current_index() -> None:
    batch = BatchState(
        workspace_id="ws_1",
        size=3,
        status=BatchStatus.RUNNING,
        tasks=[
            TaskStateRecord(index=0, title="a", state=TaskState.APPROVED),
            TaskStateRecord(index=1, title="b", state=TaskState.AUDITING),
            TaskStateRecord(index=2, title="c", state=TaskState.PENDING),
        ],
    )
    assert batch.completed_count == 1
    assert batch.failed_count == 0
    assert batch.current_index == 1
    assert batch.progress_label() == "1/3 approved"


def test_batch_default_size_is_five() -> None:
    assert BatchState().size == 5


def test_batch_round_trip() -> None:
    batch = BatchState(
        workspace_id="ws_1",
        size=2,
        status=BatchStatus.PAUSED,
        tasks=[TaskStateRecord(index=0, title="one", state=TaskState.FAILED, attempts=2)],
    )
    restored = BatchState.from_dict(batch.to_dict())
    assert restored.to_dict() == batch.to_dict()


# -- pipeline aggregate ---------------------------------------------------------
def test_bootstrap_creates_one_config_per_role() -> None:
    state = PipelineState.bootstrap()
    assert state.phase is PipelinePhase.IDLE
    assert set(state.role_configs) == set(AgentRole)
    for role, config in state.role_configs.items():
        assert config.role is role
        assert config.session_policy is default_session_policy(role)


def test_final_auditor_same_as_orchestrator_resolution() -> None:
    state = PipelineState.bootstrap()
    state.role_configs[AgentRole.ORCHESTRATOR].engine = "codex"
    final = state.role_configs[AgentRole.FINAL_AUDITOR]
    final.engine = "hermes"

    final.same_as_orchestrator = True
    assert state.resolved_engine_for(AgentRole.FINAL_AUDITOR) == "codex"

    final.same_as_orchestrator = False
    assert state.resolved_engine_for(AgentRole.FINAL_AUDITOR) == "hermes"


def test_pipeline_state_round_trip() -> None:
    state = PipelineState.bootstrap(WorkspaceConfig(name="demo", repo_path="C:/demo"))
    state.role_configs[AgentRole.BUILDER].engine = "hermes"
    state.phase = PipelinePhase.PAUSED
    state.batch = BatchState(workspace_id=state.workspace.workspace_id, size=7)

    restored = PipelineState.from_dict(state.to_dict())
    assert restored.phase is PipelinePhase.PAUSED
    assert restored.workspace == state.workspace
    assert restored.role_configs[AgentRole.BUILDER].engine == "hermes"
    assert restored.batch is not None and restored.batch.size == 7


def test_config_for_creates_missing_role() -> None:
    state = PipelineState.bootstrap()
    state.role_configs.pop(AgentRole.BUILDER)
    config = state.config_for(AgentRole.BUILDER)
    assert config.role is AgentRole.BUILDER
    assert config.session_policy is SessionPolicy.ALWAYS_NEW
