"""Core layer: configuration, event log, session policy, controller, executor.

Qt-free.  The UI is a consumer of this layer, never a peer of it.
"""

from .config import (
    APP_NAME,
    DEFAULT_BATCH_SIZE,
    MAX_BATCH_SIZE,
    MIN_BATCH_SIZE,
    AppPaths,
    default_paths,
    placeholder_role_config,
)
from .controller import ControlOutcome, ControlResult, PipelineController
from .events import EventLog, Listener, LogRecord, NullEventLog
from .executor import (
    AUDITOR_ROLE,
    DEFAULT_TASK_ROLE,
    ExecutionOutcome,
    ExecutionReport,
    Executor,
    MAX_AUDIT_ROUNDS,
    TaskNextAction,
    TaskSpec,
    next_task_action,
)
from .hermes_profiles import (
    ProfileDiscoveryResult,
    discover_profiles,
    parse_profile_list,
    profile_roots,
)
from .session_manager import (
    ROLE_SESSION_POLICY_DESCRIPTION,
    SessionAction,
    SessionDecision,
    SessionManager,
    decide_session_action,
)
from .verdict_parser import (
    AUDIT_ENVELOPE_END,
    AUDIT_ENVELOPE_START,
    VerdictParseError,
    parse_audit_verdict,
)

__all__ = [
    "APP_NAME",
    "AUDIT_ENVELOPE_END",
    "AUDIT_ENVELOPE_START",
    "AUDITOR_ROLE",
    "AppPaths",
    "ControlOutcome",
    "ControlResult",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_TASK_ROLE",
    "EventLog",
    "ExecutionOutcome",
    "ExecutionReport",
    "Executor",
    "Listener",
    "LogRecord",
    "MAX_AUDIT_ROUNDS",
    "MAX_BATCH_SIZE",
    "MIN_BATCH_SIZE",
    "NullEventLog",
    "PipelineController",
    "ProfileDiscoveryResult",
    "ROLE_SESSION_POLICY_DESCRIPTION",
    "SessionAction",
    "SessionDecision",
    "SessionManager",
    "TaskNextAction",
    "TaskSpec",
    "VerdictParseError",
    "decide_session_action",
    "default_paths",
    "discover_profiles",
    "next_task_action",
    "parse_audit_verdict",
    "parse_profile_list",
    "placeholder_role_config",
    "profile_roots",
]