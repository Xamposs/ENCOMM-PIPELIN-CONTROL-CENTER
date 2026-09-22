"""Typed domain structures for the ENCOMM Pipeline Control Center.

These are plain, dependency-free dataclasses.  They are the single source of
truth for configuration and runtime state, and they are what the persistence
layer serialises.  No Qt or driver imports are allowed here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .enums import (
    AgentRole,
    BatchStatus,
    PipelinePhase,
    SessionPolicy,
    TaskState,
)

__all__ = [
    "AgentRoleConfig",
    "BatchState",
    "PipelineState",
    "TaskStateRecord",
    "WorkspaceConfig",
    "default_session_policy",
    "new_id",
    "utc_now",
]


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp (second precision, ``Z`` suffix)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    """Return a short, human-scannable identifier such as ``batch_1f3c9a2b``."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


#: Default session policy per role, as mandated by the project brief.
_ROLE_SESSION_POLICY: Mapping[AgentRole, SessionPolicy] = {
    AgentRole.ORCHESTRATOR: SessionPolicy.PERSISTENT_OPTIONAL,
    AgentRole.BUILDER: SessionPolicy.ALWAYS_NEW,
    AgentRole.TASK_AUDITOR: SessionPolicy.PERSISTENT_PER_BATCH,
    AgentRole.FINAL_AUDITOR: SessionPolicy.CONFIGURABLE,
}


def default_session_policy(role: AgentRole) -> SessionPolicy:
    """Return the brief-mandated default session policy for ``role``."""
    return _ROLE_SESSION_POLICY[role]


@dataclass(slots=True)
class WorkspaceConfig:
    """A supervised repository plus the display name shown in the UI."""

    name: str = "New Workspace"
    repo_path: str = ""
    workspace_id: str = field(default_factory=lambda: new_id("ws"))
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def normalised_path(self) -> Path:
        """Return the repository path as a ``Path`` (not resolved/created)."""
        return Path(self.repo_path).expanduser()

    def exists(self) -> bool:
        """True when the configured repository path exists on disk."""
        if not self.repo_path.strip():
            return False
        return self.normalised_path().exists()

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "name": self.name,
            "repo_path": self.repo_path,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkspaceConfig":
        return cls(
            name=str(data.get("name", "New Workspace")),
            repo_path=str(data.get("repo_path", "")),
            workspace_id=str(data.get("workspace_id") or new_id("ws")),
            created_at=str(data.get("created_at") or utc_now()),
            updated_at=str(data.get("updated_at") or utc_now()),
        )


@dataclass(slots=True)
class AgentRoleConfig:
    """Configuration for exactly one role.

    The role is intentionally decoupled from the engine: ``engine`` is a
    driver id resolved through the driver registry, so swapping Codex for
    Hermes (or anything else) never touches role logic.
    """

    role: AgentRole
    engine: str = ""
    project_profile: str = ""
    provider: str = ""
    model: str = ""
    session_policy: SessionPolicy = SessionPolicy.ALWAYS_NEW
    session_id: str | None = None
    same_as_orchestrator: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Accept plain strings so configs can be built from UI values / DB rows.
        if not isinstance(self.role, AgentRole):
            self.role = AgentRole(str(self.role))
        if not isinstance(self.session_policy, SessionPolicy):
            self.session_policy = SessionPolicy(str(self.session_policy))

    def is_placeholder(self) -> bool:
        """True when the role still carries only default placeholder values."""
        return not (self.engine and self.project_profile)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role.value,
            "engine": self.engine,
            "project_profile": self.project_profile,
            "provider": self.provider,
            "model": self.model,
            "session_policy": self.session_policy.value,
            "session_id": self.session_id,
            "same_as_orchestrator": self.same_as_orchestrator,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AgentRoleConfig":
        role = AgentRole(str(data["role"]))
        policy_raw = data.get("session_policy")
        policy = (
            SessionPolicy(str(policy_raw))
            if policy_raw
            else default_session_policy(role)
        )
        return cls(
            role=role,
            engine=str(data.get("engine", "")),
            project_profile=str(data.get("project_profile", "")),
            provider=str(data.get("provider", "")),
            model=str(data.get("model", "")),
            session_policy=policy,
            session_id=(str(data["session_id"]) if data.get("session_id") else None),
            same_as_orchestrator=bool(data.get("same_as_orchestrator", False)),
            extra=dict(data.get("extra") or {}),
        )


@dataclass(slots=True)
class TaskStateRecord:
    """Runtime record for a single task inside a batch."""

    task_id: str = field(default_factory=lambda: new_id("task"))
    index: int = 0
    title: str = ""
    state: TaskState = TaskState.PENDING
    attempts: int = 0
    audit_rounds: int = 0
    last_error: str | None = None
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not isinstance(self.state, TaskState):
            self.state = TaskState(str(self.state))

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "index": self.index,
            "title": self.title,
            "state": self.state.value,
            "attempts": self.attempts,
            "audit_rounds": self.audit_rounds,
            "last_error": self.last_error,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TaskStateRecord":
        return cls(
            task_id=str(data.get("task_id") or new_id("task")),
            index=int(data.get("index", 0)),
            title=str(data.get("title", "")),
            state=TaskState(str(data.get("state", TaskState.PENDING.value))),
            attempts=int(data.get("attempts", 0)),
            audit_rounds=int(data.get("audit_rounds", 0)),
            last_error=(str(data["last_error"]) if data.get("last_error") else None),
            updated_at=str(data.get("updated_at") or utc_now()),
        )


@dataclass(slots=True)
class BatchState:
    """State of one batch of tasks.

    ``size`` is the requested batch size; ``tasks`` is the materialised list of
    task records (which may be shorter than ``size`` until the orchestrator
    plans the batch).
    """

    batch_id: str = field(default_factory=lambda: new_id("batch"))
    workspace_id: str = ""
    size: int = 5
    status: BatchStatus = BatchStatus.CREATED
    tasks: list[TaskStateRecord] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not isinstance(self.status, BatchStatus):
            self.status = BatchStatus(str(self.status))

    @property
    def completed_count(self) -> int:
        return sum(1 for t in self.tasks if t.state is TaskState.APPROVED)

    @property
    def failed_count(self) -> int:
        return sum(1 for t in self.tasks if t.state is TaskState.FAILED)

    @property
    def current_index(self) -> int | None:
        """Index of the task currently being worked, if any."""
        for task in self.tasks:
            if task.state in (TaskState.RUNNING, TaskState.AUDITING, TaskState.RUNNING_FIX):
                return task.index
        return None

    def progress_label(self) -> str:
        return f"{self.completed_count}/{len(self.tasks) or self.size} approved"

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "workspace_id": self.workspace_id,
            "size": self.size,
            "status": self.status.value,
            "tasks": [t.to_dict() for t in self.tasks],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BatchState":
        return cls(
            batch_id=str(data.get("batch_id") or new_id("batch")),
            workspace_id=str(data.get("workspace_id", "")),
            size=int(data.get("size", 5)),
            status=BatchStatus(str(data.get("status", BatchStatus.CREATED.value))),
            tasks=[TaskStateRecord.from_dict(t) for t in (data.get("tasks") or [])],
            created_at=str(data.get("created_at") or utc_now()),
            updated_at=str(data.get("updated_at") or utc_now()),
        )


@dataclass(slots=True)
class PipelineState:
    """Aggregate root: workspace + per-role configs + phase + current batch."""

    phase: PipelinePhase = PipelinePhase.IDLE
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    role_configs: dict[AgentRole, AgentRoleConfig] = field(default_factory=dict)
    batch: BatchState | None = None
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not isinstance(self.phase, PipelinePhase):
            self.phase = PipelinePhase(str(self.phase))
        # Normalise string keys coming from JSON/DB into AgentRole members.
        self.role_configs = {
            (k if isinstance(k, AgentRole) else AgentRole(str(k))): v
            for k, v in self.role_configs.items()
        }

    @classmethod
    def bootstrap(cls, workspace: WorkspaceConfig | None = None) -> "PipelineState":
        """Create an IDLE pipeline with one default config per role."""
        ws = workspace or WorkspaceConfig()
        configs: dict[AgentRole, AgentRoleConfig] = {}
        for role in AgentRole:
            configs[role] = AgentRoleConfig(
                role=role,
                session_policy=default_session_policy(role),
            )
        return cls(
            phase=PipelinePhase.IDLE,
            workspace=ws,
            role_configs=configs,
            batch=None,
        )

    def config_for(self, role: AgentRole) -> AgentRoleConfig:
        """Return the config for ``role``, creating a default if absent."""
        if role not in self.role_configs:
            self.role_configs[role] = AgentRoleConfig(
                role=role,
                session_policy=default_session_policy(role),
            )
        return self.role_configs[role]

    def resolved_engine_for(self, role: AgentRole) -> str:
        """Return the effective engine for ``role``.

        ``FINAL_AUDITOR`` may be configured as "same as orchestrator", in which
        case the orchestrator's engine is returned.
        """
        config = self.config_for(role)
        if role is AgentRole.FINAL_AUDITOR and config.same_as_orchestrator:
            return self.config_for(AgentRole.ORCHESTRATOR).engine
        return config.engine

    def iter_role_configs(self) -> Iterable[AgentRoleConfig]:
        for role in AgentRole:
            yield self.config_for(role)

    def role_config_items(self) -> Iterable[tuple[AgentRole, AgentRoleConfig]]:
        """Yield ``(role, config)`` pairs, creating defaults for missing roles."""
        for role in AgentRole:
            yield role, self.config_for(role)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "workspace": self.workspace.to_dict(),
            "role_configs": {
                role.value: cfg.to_dict() for role, cfg in self.role_config_items()
            },
            "batch": self.batch.to_dict() if self.batch else None,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PipelineState":
        raw_configs = data.get("role_configs") or {}
        configs = {
            AgentRole(str(role)): AgentRoleConfig.from_dict(cfg)
            for role, cfg in raw_configs.items()
        }
        batch_raw = data.get("batch")
        return cls(
            phase=PipelinePhase(str(data.get("phase", PipelinePhase.IDLE.value))),
            workspace=WorkspaceConfig.from_dict(data.get("workspace") or {}),
            role_configs=configs,
            batch=BatchState.from_dict(batch_raw) if batch_raw else None,
            updated_at=str(data.get("updated_at") or utc_now()),
        )
