"""Persistence: schema creation, round-trips and the version guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from encomm_pcc.domain import (
    AgentRole,
    AgentRoleConfig,
    BatchState,
    BatchStatus,
    EventLevel,
    PipelineState,
    SessionPolicy,
    TaskState,
    TaskStateRecord,
    WorkspaceConfig,
)
from encomm_pcc.persistence import SCHEMA_VERSION, Database, PersistenceError

EXPECTED_TABLES = {
    "app_events",
    "batches",
    "role_configs",
    "schema_meta",
    "sessions",
    "tasks",
    "workspaces",
}


def test_schema_creates_all_tables(database: Database) -> None:
    assert EXPECTED_TABLES <= set(database.table_names())


def test_schema_version_is_recorded(database: Database) -> None:
    assert database.schema_version() == SCHEMA_VERSION


def test_open_is_idempotent(database: Database) -> None:
    before = set(database.table_names())
    database.open()
    database.initialize()
    assert set(database.table_names()) == before


def test_operations_before_open_raise() -> None:
    db = Database(":memory:")
    with pytest.raises(PersistenceError):
        _ = db.connection


def test_rejects_newer_schema(tmp_path: Path) -> None:
    path = tmp_path / "future.db"
    db = Database(path).open()
    db.connection.execute(
        "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
        (str(SCHEMA_VERSION + 1),),
    )
    db.connection.commit()
    db.close()

    with pytest.raises(PersistenceError) as excinfo:
        Database(path).open()
    assert "schema v" in str(excinfo.value)


# -- workspaces ------------------------------------------------------------
def test_workspace_round_trip(database: Database) -> None:
    workspace = WorkspaceConfig(name="Demo Repo", repo_path=r"C:\demo")
    database.save_workspace(workspace)

    loaded = database.get_workspace(workspace.workspace_id)
    assert loaded is not None
    assert loaded.name == "Demo Repo"
    assert loaded.repo_path == r"C:\demo"
    assert [w.workspace_id for w in database.list_workspaces()] == [workspace.workspace_id]


def test_workspace_upsert_updates_in_place(database: Database) -> None:
    workspace = WorkspaceConfig(name="First", repo_path="C:/a")
    database.save_workspace(workspace)
    workspace.name = "Second"
    workspace.repo_path = "C:/b"
    database.save_workspace(workspace)

    loaded = database.get_workspace(workspace.workspace_id)
    assert loaded is not None
    assert (loaded.name, loaded.repo_path) == ("Second", "C:/b")
    assert len(database.list_workspaces()) == 1


def test_missing_workspace_returns_none(database: Database) -> None:
    assert database.get_workspace("ws_missing") is None


# -- role configuration ---------------------------------------------------------
def test_role_config_round_trip(database: Database) -> None:
    workspace = database.save_workspace(WorkspaceConfig(name="cfg", repo_path="C:/cfg"))
    configs = {
        AgentRole.BUILDER: AgentRoleConfig(
            role=AgentRole.BUILDER,
            engine="hermes",
            project_profile="builder-default",
            provider="openrouter",
            model="deepseek/deepseek-v4.1-flash",
            session_policy=SessionPolicy.ALWAYS_NEW,
            extra={"k": "v"},
        ),
        AgentRole.FINAL_AUDITOR: AgentRoleConfig(
            role=AgentRole.FINAL_AUDITOR,
            engine="codex",
            project_profile="final-auditor-default",
            session_policy=SessionPolicy.CONFIGURABLE,
            same_as_orchestrator=True,
        ),
    }
    for config in configs.values():
        database.save_role_config(workspace.workspace_id, config)

    loaded = database.load_role_configs(workspace.workspace_id)
    assert set(loaded) == set(configs)
    assert loaded[AgentRole.BUILDER].model == "deepseek/deepseek-v4.1-flash"
    assert loaded[AgentRole.BUILDER].extra == {"k": "v"}
    assert loaded[AgentRole.FINAL_AUDITOR].same_as_orchestrator is True


def test_role_config_upsert_replaces_same_role(database: Database) -> None:
    workspace = database.save_workspace(WorkspaceConfig(name="cfg", repo_path="C:/cfg"))
    config = AgentRoleConfig(role=AgentRole.BUILDER, engine="codex")
    database.save_role_config(workspace.workspace_id, config)
    config.engine = "hermes"
    database.save_role_config(workspace.workspace_id, config)

    loaded = database.load_role_configs(workspace.workspace_id)
    assert len(loaded) == 1
    assert loaded[AgentRole.BUILDER].engine == "hermes"


# -- batches / tasks ---------------------------------------------------------------
def test_batch_and_tasks_round_trip(database: Database) -> None:
    workspace = database.save_workspace(WorkspaceConfig(name="b", repo_path="C:/b"))
    batch = BatchState(
        workspace_id=workspace.workspace_id,
        size=3,
        status=BatchStatus.RUNNING,
        tasks=[
            TaskStateRecord(index=0, title="one", state=TaskState.APPROVED),
            TaskStateRecord(index=1, title="two", state=TaskState.RUNNING_FIX, attempts=1),
            TaskStateRecord(index=2, title="three", state=TaskState.PENDING, last_error="boom"),
        ],
    )
    database.save_batch(batch)

    loaded = database.load_batch(batch.batch_id)
    assert loaded is not None
    assert loaded.status is BatchStatus.RUNNING
    assert [t.title for t in loaded.tasks] == ["one", "two", "three"]
    assert loaded.tasks[1].state is TaskState.RUNNING_FIX
    assert loaded.tasks[2].last_error == "boom"


def test_save_batch_replaces_tasks(database: Database) -> None:
    workspace = database.save_workspace(WorkspaceConfig(name="b", repo_path="C:/b"))
    batch = BatchState(
        workspace_id=workspace.workspace_id,
        size=2,
        tasks=[TaskStateRecord(index=0, title="a"), TaskStateRecord(index=1, title="b")],
    )
    database.save_batch(batch)
    batch.tasks = [TaskStateRecord(index=0, title="a-only")]
    database.save_batch(batch)

    loaded = database.load_batch(batch.batch_id)
    assert loaded is not None
    assert [t.title for t in loaded.tasks] == ["a-only"]


def test_active_batch_ignores_terminal_statuses(database: Database) -> None:
    workspace = database.save_workspace(WorkspaceConfig(name="b", repo_path="C:/b"))
    database.save_batch(
        BatchState(workspace_id=workspace.workspace_id, status=BatchStatus.COMPLETE)
    )
    assert database.load_active_batch(workspace.workspace_id) is None

    active = BatchState(workspace_id=workspace.workspace_id, status=BatchStatus.RUNNING)
    database.save_batch(active)
    loaded = database.load_active_batch(workspace.workspace_id)
    assert loaded is not None and loaded.batch_id == active.batch_id


# -- sessions ---------------------------------------------------------------------
def test_session_record_and_close(database: Database) -> None:
    workspace = database.save_workspace(WorkspaceConfig(name="s", repo_path="C:/s"))
    database.record_session(
        session_id="sess_1",
        workspace_id=workspace.workspace_id,
        role=AgentRole.TASK_AUDITOR,
        driver_id="hermes",
        persistent=True,
    )
    assert database.latest_session_id(workspace.workspace_id, AgentRole.TASK_AUDITOR) == "sess_1"

    database.close_session("sess_1")
    assert database.latest_session_id(workspace.workspace_id, AgentRole.TASK_AUDITOR) is None
    assert len(database.list_sessions(workspace.workspace_id)) == 1


# -- events --------------------------------------------------------------------------
def test_events_are_appended_and_counted(database: Database) -> None:
    database.log_event("first", level=EventLevel.INFO, source="test")
    database.log_event("second", level=EventLevel.ERROR, source="test", payload={"n": 1})

    assert database.event_count() == 2
    recent = database.recent_events(limit=10)
    assert [e["message"] for e in recent] == ["second", "first"]
    assert recent[0]["level"] == "ERROR"
    assert '"n": 1' in recent[0]["payload_json"]


# -- aggregate -------------------------------------------------------------------------
def test_pipeline_aggregate_round_trip(database: Database) -> None:
    state = PipelineState.bootstrap(WorkspaceConfig(name="agg", repo_path="C:/agg"))
    state.role_configs[AgentRole.BUILDER].engine = "hermes"
    state.role_configs[AgentRole.ORCHESTRATOR].engine = "codex"
    state.batch = BatchState(workspace_id=state.workspace.workspace_id, size=4)
    database.save_pipeline_state(state)

    restored = database.load_pipeline_state(state.workspace.workspace_id)
    assert restored is not None
    assert restored.workspace.name == "agg"
    assert restored.role_configs[AgentRole.BUILDER].engine == "hermes"
    assert restored.role_configs[AgentRole.ORCHESTRATOR].engine == "codex"
    assert restored.batch is not None and restored.batch.size == 4


def test_load_pipeline_state_for_unknown_workspace_is_none(database: Database) -> None:
    assert database.load_pipeline_state("ws_nope") is None


def test_database_file_is_created(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "state.db"
    with Database(path) as db:
        assert db.table_names()
    assert path.exists()
