"""Driver layer: engine adapters, the registry and the process abstraction.

Engines are swappable.  Roles are configured separately, so nothing here may
assume a specific engine.
"""

from .base import (
    BaseDriver,
    DriverCapabilities,
    DriverError,
    DriverNotImplementedError,
    DriverSession,
    PromptHandle,
    PromptResult,
    SessionRequest,
)
from .codex import CodexDriver
from .codex_cli import (
    ALLOWED_SANDBOX_MODES,
    FORBIDDEN_FLAGS,
    CodexCliError,
    CodexRunOutput,
    build_exec_argv,
    build_exec_resume_argv,
    codex_child_environment,
    parse_exec_jsonl,
)
from .codex_discovery import CodexSessionDiscovery
from .generic_cli import GenericCliDriver
from .hermes import HermesDriver
from .hermes_cli import (
    EXIT_AGENT_FAILURE,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_USAGE,
    HermesCliError,
    HermesRunOutput,
    build_chat_argv,
    child_environment,
    parse_stream_json,
)
from .process import (
    TIMEOUT_EXIT_CODE,
    NullProcessRunner,
    ProcessResult,
    ProcessRunner,
    ProcessSpec,
    SubprocessRunner,
    kill_process_tree,
)
from .registry import (
    IMPLEMENTED_DRIVERS,
    PLANNED_DRIVERS,
    DriverRegistry,
    PlannedDriver,
    default_registry,
)
from .session_discovery import (
    DEFAULT_DISCOVERY_LIMIT,
    ExternalSessionDescriptor,
    SessionDiscoveryResult,
    SessionDiscoverer,
    normalise_workspace_key,
)

__all__ = [
    "ALLOWED_SANDBOX_MODES",
    "BaseDriver",
    "CodexCliError",
    "CodexDriver",
    "CodexRunOutput",
    "CodexSessionDiscovery",
    "DEFAULT_DISCOVERY_LIMIT",
    "DriverCapabilities",
    "DriverError",
    "DriverNotImplementedError",
    "DriverRegistry",
    "DriverSession",
    "EXIT_AGENT_FAILURE",
    "EXIT_INTERRUPTED",
    "EXIT_OK",
    "EXIT_USAGE",
    "ExternalSessionDescriptor",
    "FORBIDDEN_FLAGS",
    "GenericCliDriver",
    "HermesCliError",
    "HermesDriver",
    "HermesRunOutput",
    "IMPLEMENTED_DRIVERS",
    "NullProcessRunner",
    "PLANNED_DRIVERS",
    "PlannedDriver",
    "ProcessResult",
    "ProcessRunner",
    "ProcessSpec",
    "PromptHandle",
    "PromptResult",
    "SessionDiscoveryResult",
    "SessionDiscoverer",
    "SessionRequest",
    "SubprocessRunner",
    "TIMEOUT_EXIT_CODE",
    "build_chat_argv",
    "build_exec_argv",
    "build_exec_resume_argv",
    "child_environment",
    "codex_child_environment",
    "default_registry",
    "kill_process_tree",
    "normalise_workspace_key",
    "parse_exec_jsonl",
    "parse_stream_json",
]
