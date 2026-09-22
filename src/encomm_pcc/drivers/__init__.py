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
from .process import (
    NullProcessRunner,
    ProcessResult,
    ProcessRunner,
    ProcessSpec,
    SubprocessRunner,
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
    "GenericCliDriver",
    "HermesDriver",
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
    "default_registry",
]
