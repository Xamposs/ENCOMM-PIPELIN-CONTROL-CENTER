"""Session policy engine and session bookkeeping."""

from __future__ import annotations

import pytest

from encomm_pcc.core import (
    ROLE_SESSION_POLICY_DESCRIPTION,
    SessionAction,
    SessionManager,
    decide_session_action,
)
from encomm_pcc.domain import AgentRole, SessionPolicy, WorkspaceConfig
from encomm_pcc.drivers import DriverCapabilities
from encomm_pcc.persistence import Database

SESSIONFUL = DriverCapabilities(
    driver_id="hermes", display_name="Hermes", supports_sessions=True, supports_resume=True
)
STATELESS = DriverCapabilities(
    driver_id="generic_cli", display_name="Generic CLI", supports_sessions=False
)


def _persisted_workspace(database: Database) -> str:
    """Sessions carry a foreign key to workspaces, so one must exist first."""
    return database.save_workspace(
        WorkspaceConfig(name="policy-test", repo_path="C:/tmp/policy")
    ).workspace_id


def _decide(policy: SessionPolicy, session_id: str | None, *, same_batch: bool = True, caps=SESSIONFUL):
    return decide_session_action(
        policy=policy,
        capabilities=caps,
        existing_session_id=session_id,
        same_batch=same_batch,
    )


def test_stateless_engine_never_gets_a_session() -> None:
    decision = _decide(SessionPolicy.PERSISTENT, "sess_1", caps=STATELESS)
    assert decision.action is SessionAction.NONE
    assert "stateless" in decision.reason


def test_always_new_ignores_an_existing_session() -> None:
    decision = _decide(SessionPolicy.ALWAYS_NEW, "sess_1")
    assert decision.action is SessionAction.NEW
    assert decision.starts_new_session


def test_always_new_without_a_session_still_creates_one() -> None:
    assert _decide(SessionPolicy.ALWAYS_NEW, None).action is SessionAction.NEW


def test_persistent_per_batch_reuses_within_the_same_batch() -> None:
    decision = _decide(SessionPolicy.PERSISTENT_PER_BATCH, "sess_1", same_batch=True)
    assert decision.action is SessionAction.REUSE
    assert decision.session_id == "sess_1"


def test_persistent_per_batch_replaces_across_batches() -> None:
    decision = _decide(SessionPolicy.PERSISTENT_PER_BATCH, "sess_1", same_batch=False)
    assert decision.action is SessionAction.NEW
    assert decision.session_id is None


def test_persistent_reuses_or_creates() -> None:
    assert _decide(SessionPolicy.PERSISTENT, "sess_1").action is SessionAction.REUSE
    assert _decide(SessionPolicy.PERSISTENT, None).action is SessionAction.NEW


@pytest.mark.parametrize(
    "policy", [SessionPolicy.PERSISTENT_OPTIONAL, SessionPolicy.CONFIGURABLE]
)
def test_optional_policies_prefer_continuity(policy: SessionPolicy) -> None:
    assert _decide(policy, "sess_1").action is SessionAction.REUSE
    assert _decide(policy, None).action is SessionAction.NEW


def test_every_role_has_a_policy_description() -> None:
    assert set(ROLE_SESSION_POLICY_DESCRIPTION) == set(AgentRole)
    assert ROLE_SESSION_POLICY_DESCRIPTION[AgentRole.BUILDER] == "Always new"
    assert ROLE_SESSION_POLICY_DESCRIPTION[AgentRole.TASK_AUDITOR] == "Persistent per batch"


# -- SessionManager ----------------------------------------------------------
def test_manager_tracks_sessions_per_role() -> None:
    manager = SessionManager()
    manager.register_session(AgentRole.BUILDER, "sess_b", "hermes")
    manager.register_session(AgentRole.TASK_AUDITOR, "sess_a", "hermes")

    assert manager.current_session_id(AgentRole.BUILDER) == "sess_b"
    assert manager.current_session_id(AgentRole.TASK_AUDITOR) == "sess_a"
    assert manager.current_session_id(AgentRole.FINAL_AUDITOR) is None


def test_new_batch_invalidates_per_batch_sessions() -> None:
    manager = SessionManager()
    manager.register_session(AgentRole.TASK_AUDITOR, "sess_a", "hermes")
    assert manager.session_is_for_current_batch(AgentRole.TASK_AUDITOR)

    manager.begin_new_batch()
    assert not manager.session_is_for_current_batch(AgentRole.TASK_AUDITOR)

    decision = manager.decide(
        AgentRole.TASK_AUDITOR, SessionPolicy.PERSISTENT_PER_BATCH, SESSIONFUL
    )
    assert decision.action is SessionAction.NEW


def test_clear_session_forgets_and_closes(database: Database) -> None:
    workspace_id = _persisted_workspace(database)
    manager = SessionManager(database=database, workspace_id=workspace_id)
    manager.register_session(AgentRole.BUILDER, "sess_b", "hermes")
    assert database.latest_session_id(workspace_id, AgentRole.BUILDER) == "sess_b"

    manager.clear_session(AgentRole.BUILDER)
    assert manager.current_session_id(AgentRole.BUILDER) is None
    assert database.latest_session_id(workspace_id, AgentRole.BUILDER) is None


def test_register_session_is_persisted(database: Database) -> None:
    workspace_id = _persisted_workspace(database)
    manager = SessionManager(database=database, workspace_id=workspace_id)
    manager.register_session(AgentRole.ORCHESTRATOR, "sess_o", "codex")

    rows = database.list_sessions(workspace_id)
    assert len(rows) == 1
    assert rows[0]["session_id"] == "sess_o"
    assert rows[0]["driver_id"] == "codex"
    assert rows[0]["role"] == "ORCHESTRATOR"


def test_register_session_without_a_workspace_row_is_rejected(database: Database) -> None:
    """The FK is intentional: a session cannot outlive its workspace record."""
    import sqlite3

    manager = SessionManager(database=database, workspace_id="ws_missing")
    with pytest.raises(sqlite3.IntegrityError):
        manager.register_session(AgentRole.BUILDER, "sess_orphan", "hermes")


def test_setting_workspace_clears_in_memory_sessions() -> None:
    manager = SessionManager()
    manager.register_session(AgentRole.BUILDER, "sess_b", "hermes")
    manager.set_workspace("ws_other")
    assert manager.current_session_id(AgentRole.BUILDER) is None


def test_snapshot_shape() -> None:
    manager = SessionManager(workspace_id="ws_1")
    manager.register_session(AgentRole.BUILDER, "sess_b", "hermes")
    snapshot = manager.snapshot()
    assert snapshot["workspace_id"] == "ws_1"
    assert snapshot["batch_generation"] == 0
    assert snapshot["sessions"] == {"BUILDER": "sess_b"}
