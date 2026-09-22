"""SQLite persistence for the ENCOMM Pipeline Control Center.

Design notes
------------
* **One connection, one lock.**  This is a single-user desktop application;
  a shared connection guarded by a :class:`threading.Lock` is simpler and
  safer than a pool, and keeps the UI thread and any future worker thread on
  the same serialised view of state.
* **Durable truth lives here.**  Session continuity is never the source of
  truth; whatever a driver session knows must be recoverable from these tables
  plus workspace files.
* **No migration framework in v0.1.**  :data:`SCHEMA_VERSION` is recorded in
  ``schema_meta``; a database written by a *newer* schema is rejected rather
  than silently mis-read.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from ..domain import (
    AgentRole,
    AgentRoleConfig,
    BatchState,
    EventLevel,
    PipelinePhase,
    PipelineState,
    TaskStateRecord,
    WorkspaceConfig,
    utc_now,
)
from ..domain.models import new_id

__all__ = ["SCHEMA_PATH", "SCHEMA_VERSION", "Database", "PersistenceError"]

SCHEMA_VERSION = 1
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class PersistenceError(RuntimeError):
    """Raised for storage-level failures the caller should surface."""


class Database:
    """Thin, explicit SQLite layer.

    Use ``Database(":memory:")`` in tests for an isolated store.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    # -- connection lifecycle -------------------------------------------
    @property
    def connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise PersistenceError("Database is not open; call open() first")
        return self._conn

    def open(self) -> "Database":
        """Open (or create) the database file and ensure the schema exists."""
        if self._conn is not None:
            return self
        if self.path != ":memory:":
            parent = Path(self.path).parent
            if str(parent) and not parent.exists():
                parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        self._conn = conn
        self.initialize()
        return self

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "Database":
        return self.open()

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Serialise a write and commit it atomically."""
        with self._lock:
            conn = self.connection
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    # -- schema ----------------------------------------------------------
    def initialize(self) -> None:
        """Create tables and record/validate the schema version."""
        with self._lock:
            conn = self.connection
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            conn.commit()
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
                conn.commit()
            else:
                found = int(row["value"])
                if found > SCHEMA_VERSION:
                    raise PersistenceError(
                        f"Database at {self.path} uses schema v{found}, but this "
                        f"build supports v{SCHEMA_VERSION}. Refusing to open."
                    )

    def schema_version(self) -> int:
        row = self.connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        return int(row["value"]) if row else 0

    # -- workspaces ------------------------------------------------------
    def save_workspace(self, workspace: WorkspaceConfig) -> WorkspaceConfig:
        workspace.updated_at = utc_now()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO workspaces (workspace_id, name, repo_path, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (workspace_id) DO UPDATE SET
                    name = excluded.name,
                    repo_path = excluded.repo_path,
                    updated_at = excluded.updated_at
                """,
                (
                    workspace.workspace_id,
                    workspace.name,
                    workspace.repo_path,
                    workspace.created_at,
                    workspace.updated_at,
                ),
            )
        return workspace

    def get_workspace(self, workspace_id: str) -> WorkspaceConfig | None:
        row = self.connection.execute(
            "SELECT * FROM workspaces WHERE workspace_id = ?", (workspace_id,)
        ).fetchone()
        return WorkspaceConfig.from_dict(dict(row)) if row else None

    def list_workspaces(self) -> list[WorkspaceConfig]:
        rows = self.connection.execute(
            "SELECT * FROM workspaces ORDER BY created_at ASC"
        ).fetchall()
        return [WorkspaceConfig.from_dict(dict(r)) for r in rows]

    # -- role configuration ----------------------------------------------
    def save_role_config(
        self, workspace_id: str, config: AgentRoleConfig
    ) -> AgentRoleConfig:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO role_configs (
                    config_id, workspace_id, role, engine, project_profile, provider,
                    model, session_policy, session_id, same_as_orchestrator,
                    extra_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (workspace_id, role) DO UPDATE SET
                    engine = excluded.engine,
                    project_profile = excluded.project_profile,
                    provider = excluded.provider,
                    model = excluded.model,
                    session_policy = excluded.session_policy,
                    session_id = excluded.session_id,
                    same_as_orchestrator = excluded.same_as_orchestrator,
                    extra_json = excluded.extra_json,
                    updated_at = excluded.updated_at
                """,
                (
                    new_id("cfg"),
                    workspace_id,
                    config.role.value,
                    config.engine,
                    config.project_profile,
                    config.provider,
                    config.model,
                    config.session_policy.value,
                    config.session_id,
                    1 if config.same_as_orchestrator else 0,
                    json.dumps(config.extra, sort_keys=True) if config.extra else None,
                    utc_now(),
                ),
            )
        return config

    def load_role_configs(self, workspace_id: str) -> dict[AgentRole, AgentRoleConfig]:
        rows = self.connection.execute(
            "SELECT * FROM role_configs WHERE workspace_id = ?", (workspace_id,)
        ).fetchall()
        result: dict[AgentRole, AgentRoleConfig] = {}
        for row in rows:
            data = dict(row)
            data["extra"] = json.loads(data["extra_json"]) if data.get("extra_json") else {}
            data["same_as_orchestrator"] = bool(data.get("same_as_orchestrator"))
            config = AgentRoleConfig.from_dict(data)
            result[config.role] = config
        return result

    # -- sessions ---------------------------------------------------------
    def record_session(
        self,
        *,
        session_id: str,
        workspace_id: str,
        role: AgentRole | str,
        driver_id: str,
        external_session_id: str | None = None,
        persistent: bool = False,
        external: bool = False,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        role_value = role.value if isinstance(role, AgentRole) else str(role)
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    session_id, workspace_id, role, driver_id, external_session_id,
                    persistent, external, created_at, closed_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                ON CONFLICT (session_id) DO UPDATE SET
                    external_session_id = excluded.external_session_id,
                    persistent = excluded.persistent,
                    external = excluded.external,
                    metadata_json = excluded.metadata_json
                """,
                (
                    session_id,
                    workspace_id,
                    role_value,
                    driver_id,
                    external_session_id,
                    1 if persistent else 0,
                    1 if external else 0,
                    utc_now(),
                    json.dumps(dict(metadata), sort_keys=True) if metadata else None,
                ),
            )
        return session_id

    def close_session(self, session_id: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE sessions SET closed_at = ? WHERE session_id = ?",
                (utc_now(), session_id),
            )

    def list_sessions(self, workspace_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM sessions
            WHERE workspace_id = ?
            ORDER BY created_at DESC
            """,
            (workspace_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def latest_session_id(self, workspace_id: str, role: AgentRole | str) -> str | None:
        """Most recent open session id for a role, if any."""
        role_value = role.value if isinstance(role, AgentRole) else str(role)
        row = self.connection.execute(
            """
            SELECT session_id FROM sessions
            WHERE workspace_id = ? AND role = ? AND closed_at IS NULL
            ORDER BY created_at DESC LIMIT 1
            """,
            (workspace_id, role_value),
        ).fetchone()
        return str(row["session_id"]) if row else None

    # -- batches and tasks -------------------------------------------------
    def save_batch(self, batch: BatchState) -> BatchState:
        batch.updated_at = utc_now()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO batches (batch_id, workspace_id, size, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (batch_id) DO UPDATE SET
                    size = excluded.size,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (
                    batch.batch_id,
                    batch.workspace_id,
                    batch.size,
                    batch.status.value,
                    batch.created_at,
                    batch.updated_at,
                ),
            )
            conn.execute("DELETE FROM tasks WHERE batch_id = ?", (batch.batch_id,))
            for task in batch.tasks:
                conn.execute(
                    """
                    INSERT INTO tasks (
                        task_id, batch_id, task_index, title, state, attempts,
                        audit_rounds, last_error, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task.task_id,
                        batch.batch_id,
                        task.index,
                        task.title,
                        task.state.value,
                        task.attempts,
                        task.audit_rounds,
                        task.last_error,
                        task.updated_at,
                    ),
                )
        return batch

    def load_batch(self, batch_id: str) -> BatchState | None:
        row = self.connection.execute(
            "SELECT * FROM batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        task_rows = self.connection.execute(
            "SELECT * FROM tasks WHERE batch_id = ? ORDER BY task_index ASC",
            (batch_id,),
        ).fetchall()
        data["tasks"] = [dict(t) for t in task_rows]
        return BatchState.from_dict(data)

    def load_active_batch(self, workspace_id: str) -> BatchState | None:
        """Most recent non-terminal batch for a workspace, if any."""
        row = self.connection.execute(
            """
            SELECT batch_id FROM batches
            WHERE workspace_id = ? AND status NOT IN ('COMPLETE', 'FAILED', 'STOPPED')
            ORDER BY created_at DESC LIMIT 1
            """,
            (workspace_id,),
        ).fetchone()
        return self.load_batch(str(row["batch_id"])) if row else None

    def list_batches(self, workspace_id: str, limit: int = 20) -> list[BatchState]:
        rows = self.connection.execute(
            """
            SELECT batch_id FROM batches
            WHERE workspace_id = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (workspace_id, limit),
        ).fetchall()
        batches: list[BatchState] = []
        for row in rows:
            batch = self.load_batch(str(row["batch_id"]))
            if batch is not None:
                batches.append(batch)
        return batches

    # -- events ------------------------------------------------------------
    def log_event(
        self,
        message: str,
        *,
        level: EventLevel | str = EventLevel.INFO,
        source: str = "",
        payload: Mapping[str, Any] | None = None,
    ) -> int:
        level_value = level.value if isinstance(level, EventLevel) else str(level)
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO app_events (ts, level, source, message, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    utc_now(),
                    level_value,
                    source,
                    message,
                    json.dumps(dict(payload), sort_keys=True) if payload else None,
                ),
            )
            return int(cursor.lastrowid or 0)

    def recent_events(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM app_events ORDER BY event_id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def event_count(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS n FROM app_events").fetchone()
        return int(row["n"]) if row else 0

    # -- aggregate ---------------------------------------------------------
    def save_pipeline_state(self, state: PipelineState) -> PipelineState:
        """Persist a whole pipeline aggregate in one transaction."""
        state.updated_at = utc_now()
        self.save_workspace(state.workspace)
        for config in state.iter_role_configs():
            self.save_role_config(state.workspace.workspace_id, config)
        if state.batch is not None:
            state.batch.workspace_id = state.workspace.workspace_id
            self.save_batch(state.batch)
        return state

    def load_pipeline_state(self, workspace_id: str) -> PipelineState | None:
        """Rebuild a pipeline aggregate for ``workspace_id``."""
        workspace = self.get_workspace(workspace_id)
        if workspace is None:
            return None
        return PipelineState(
            phase=PipelinePhase.IDLE,
            workspace=workspace,
            role_configs=self.load_role_configs(workspace_id),
            batch=self.load_active_batch(workspace_id),
        )

    # -- introspection -----------------------------------------------------
    def table_names(self) -> Sequence[str]:
        rows = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
        return [str(r["name"]) for r in rows]
