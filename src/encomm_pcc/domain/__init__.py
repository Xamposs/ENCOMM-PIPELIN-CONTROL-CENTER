"""Domain layer: typed structures, enums and the phase state machine.

This package has no Qt and no driver dependencies so it can be unit-tested and
reused headlessly.
"""

from .enums import (
    AgentRole,
    BatchStatus,
    EventLevel,
    PipelinePhase,
    SessionPolicy,
    TaskState,
    TERMINAL_PIPELINE_PHASES,
)
from .models import (
    AgentRoleConfig,
    BatchState,
    PipelineState,
    TaskStateRecord,
    WorkspaceConfig,
    default_session_policy,
    new_id,
    utc_now,
)
from .state_machine import (
    ACTIVE_PIPELINE_PHASES,
    PAUSE_RESUME_TARGETS,
    TRANSITIONS,
    InvalidTransitionError,
    StateMachine,
)

__all__ = [
    "ACTIVE_PIPELINE_PHASES",
    "AgentRole",
    "AgentRoleConfig",
    "BatchState",
    "BatchStatus",
    "EventLevel",
    "InvalidTransitionError",
    "PAUSE_RESUME_TARGETS",
    "PipelinePhase",
    "PipelineState",
    "SessionPolicy",
    "StateMachine",
    "TERMINAL_PIPELINE_PHASES",
    "TRANSITIONS",
    "TaskState",
    "TaskStateRecord",
    "WorkspaceConfig",
    "default_session_policy",
    "new_id",
    "utc_now",
]
