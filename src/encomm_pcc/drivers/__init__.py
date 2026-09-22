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

__all__ = [
    "BaseDriver",
    "CodexDriver",
    "DriverCapabilities",
    "DriverError",
    "DriverNotImplementedError",
    "DriverRegistry",
    "DriverSession",
    "EXIT_AGENT_FAILURE",
    "EXIT_INTERRUPTED",
    "EXIT_OK",
    "EXIT_USAGE",
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
    "SessionRequest",
    "SubprocessRunner",
    "TIMEOUT_EXIT_CODE",
    "build_chat_argv",
    "child_environment",
    "default_registry",
    "kill_process_tree",
    "parse_stream_json",
]
