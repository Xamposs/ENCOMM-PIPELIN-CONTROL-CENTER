"""Core layer: configuration, event log, session policy, controller, executor.

Qt-free.  The UI is a consumer of this layer, never a peer of it.
"""

from .batch_runner import BatchOutcome, BatchRunReport, BatchRunner, StepRecord
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
    ORCHESTRATOR_ROLE,
    PlanOutcome,
    PlanReport,
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
from .plan_packet import PLANNING_OUTPUT_SCHEMA, render_planning_prompt
from .plan_parser import (
    PLAN_ENVELOPE_END,
    PLAN_ENVELOPE_START,
    PlanParseError,
    parse_batch_plan,
)
from .repo_fingerprint import (
    GIT_TIMEOUT_S,
    RepoFingerprint,
    capture_repo_fingerprint,
    fingerprints_equal,
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
    "BatchOutcome",
    "BatchRunReport",
    "BatchRunner",
    "ControlOutcome",
    "ControlResult",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_TASK_ROLE",
    "EventLog",
    "ExecutionOutcome",
    "ExecutionReport",
    "Executor",
    "GIT_TIMEOUT_S",
    "Listener",
    "LogRecord",
    "MAX_AUDIT_ROUNDS",
    "MAX_BATCH_SIZE",
    "MIN_BATCH_SIZE",
    "NullEventLog",
    "ORCHESTRATOR_ROLE",
    "PLAN_ENVELOPE_END",
    "PLAN_ENVELOPE_START",
    "PipelineController",
    "PlanOutcome",
    "PlanParseError",
    "PlanReport",
    "PLANNING_OUTPUT_SCHEMA",
    "ProfileDiscoveryResult",
    "ROLE_SESSION_POLICY_DESCRIPTION",
    "RepoFingerprint",
    "SessionAction",
    "SessionDecision",
    "SessionManager",
    "StepRecord",
    "TaskNextAction",
    "TaskSpec",
    "VerdictParseError",
    "capture_repo_fingerprint",
    "decide_session_action",
    "default_paths",
    "discover_profiles",
    "fingerprints_equal",
    "next_task_action",
    "parse_audit_verdict",
    "parse_batch_plan",
    "parse_profile_list",
    "placeholder_role_config",
    "profile_roots",
    "render_planning_prompt",
]