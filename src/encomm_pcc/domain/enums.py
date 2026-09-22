"""Enumerations for the ENCOMM Pipeline Control Center domain.

All enums subclass ``str`` so they round-trip cleanly through SQLite text
columns, JSON payloads and Qt combo-box ``userData`` without adapter code.
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "AgentRole",
    "BatchStatus",
    "EventLevel",
    "PipelinePhase",
    "SessionPolicy",
    "TaskState",
    "TERMINAL_PIPELINE_PHASES",
]


class AgentRole(str, Enum):
    """Independent, swappable roles in the pipeline.

    The engine that fills a role is configured separately from the role
    itself.  The same engine may fill several roles at once; the roles stay
    independently configurable.
    """

    ORCHESTRATOR = "ORCHESTRATOR"
    BUILDER = "BUILDER"
    TASK_AUDITOR = "TASK_AUDITOR"
    FINAL_AUDITOR = "FINAL_AUDITOR"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


class SessionPolicy(str, Enum):
    """How a driver session is reused for a given role.

    Session continuity is a *performance* concern only.  Durable truth always
    lives in application state (SQLite + workspace files), never in a live
    session.
    """

    #: One session reused for the whole run.
    PERSISTENT = "persistent"
    #: One session per batch; a new batch starts a new session.
    PERSISTENT_PER_BATCH = "persistent_per_batch"
    #: A brand-new session for every unit of work (implement or fix).
    ALWAYS_NEW = "always_new"
    #: Caller decides; session may be reused or dropped per invocation.
    PERSISTENT_OPTIONAL = "persistent_optional"
    #: Configurable at the UI level, defaulting to ``fallback``.
    CONFIGURABLE = "configurable"


class PipelinePhase(str, Enum):
    """Top-level lifecycle phase of the pipeline engine.

    The executor is not implemented in v0.1; this enum defines the contract
    that the future executor must honour.
    """

    IDLE = "IDLE"
    PLANNING_BATCH = "PLANNING_BATCH"
    RUNNING_TASK = "RUNNING_TASK"
    AUDITING_TASK = "AUDITING_TASK"
    FIX_REQUIRED = "FIX_REQUIRED"
    RUNNING_FIX = "RUNNING_FIX"
    READY_FOR_FINAL_AUDIT = "READY_FOR_FINAL_AUDIT"
    FINAL_AUDIT_RUNNING = "FINAL_AUDIT_RUNNING"
    BATCH_COMPLETE = "BATCH_COMPLETE"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


class TaskState(str, Enum):
    """Lifecycle of a single task inside a batch."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    AUDITING = "AUDITING"
    FIX_REQUIRED = "FIX_REQUIRED"
    RUNNING_FIX = "RUNNING_FIX"
    APPROVED = "APPROVED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


class BatchStatus(str, Enum):
    """Lifecycle of a batch of tasks."""

    CREATED = "CREATED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    STOPPED = "STOPPED"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


class EventLevel(str, Enum):
    """Severity of an application event written to the local event log."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


#: Phases from which the pipeline does not advance on its own.
TERMINAL_PIPELINE_PHASES: frozenset[PipelinePhase] = frozenset(
    {
        PipelinePhase.BATCH_COMPLETE,
        PipelinePhase.FAILED,
    }
)
