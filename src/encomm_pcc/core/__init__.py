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
    DEFAULT_TASK_ROLE,
    ExecutionOutcome,
    ExecutionReport,
    Executor,
    TaskSpec,
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

__all__ = [
    "APP_NAME",
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
    "MAX_BATCH_SIZE",
    "MIN_BATCH_SIZE",
    "NullEventLog",
    "PipelineController",
    "ProfileDiscoveryResult",
    "ROLE_SESSION_POLICY_DESCRIPTION",
    "SessionAction",
    "SessionDecision",
    "SessionManager",
    "TaskSpec",
    "decide_session_action",
    "default_paths",
    "discover_profiles",
    "parse_profile_list",
    "placeholder_role_config",
    "profile_roots",
]