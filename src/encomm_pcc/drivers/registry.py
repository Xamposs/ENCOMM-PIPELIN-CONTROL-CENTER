"""Driver registry: the only place engine ids are resolved to adapters.

Adding a future engine means adding one class and one ``register`` call — no
role logic, UI panel or executor code has to change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Type

from .base import BaseDriver, DriverCapabilities
from .codex import CodexDriver
from .generic_cli import GenericCliDriver
from .hermes import HermesDriver
from .process import NullProcessRunner, ProcessRunner

__all__ = [
    "IMPLEMENTED_DRIVERS",
    "PLANNED_DRIVERS",
    "DriverRegistry",
    "PlannedDriver",
    "default_registry",
]


@dataclass(frozen=True, slots=True)
class PlannedDriver:
    """A future engine that is deliberately *not* implemented in v0.1.

    Surfaced so the UI and docs can show the roadmap without pretending the
    integration exists.
    """

    driver_id: str
    display_name: str
    notes: str


#: Drivers with a real (if placeholder) adapter class in this phase.
IMPLEMENTED_DRIVERS: tuple[Type[BaseDriver], ...] = (
    CodexDriver,
    HermesDriver,
    GenericCliDriver,
)

#: Engines named in the project brief for later phases.
PLANNED_DRIVERS: tuple[PlannedDriver, ...] = (
    PlannedDriver("claude_code", "Claude Code", "Planned adapter; no code yet."),
    PlannedDriver("opencode", "OpenCode", "Planned adapter; no code yet."),
    PlannedDriver("ollama", "Ollama", "Planned local-model adapter; no code yet."),
    PlannedDriver("kimi", "Kimi", "Planned adapter; no code yet."),
)


class DriverRegistry:
    """Maps ``driver_id`` -> driver class and instantiates adapters."""

    def __init__(self, runner: ProcessRunner | None = None) -> None:
        self._classes: dict[str, Type[BaseDriver]] = {}
        self._runner: ProcessRunner = runner or NullProcessRunner()

    # -- registration ----------------------------------------------------
    def register(self, driver_cls: Type[BaseDriver]) -> None:
        driver_id = driver_cls.driver_id
        if not driver_id or driver_id == "base":
            raise ValueError(f"Driver {driver_cls!r} has no usable driver_id")
        self._classes[driver_id] = driver_cls

    def register_all(self, classes: Iterable[Type[BaseDriver]]) -> None:
        for driver_cls in classes:
            self.register(driver_cls)

    # -- lookup ----------------------------------------------------------
    def driver_ids(self) -> list[str]:
        """Registered ids, sorted for stable UI ordering."""
        return sorted(self._classes)

    def is_registered(self, driver_id: str) -> bool:
        return driver_id in self._classes

    def get_class(self, driver_id: str) -> Type[BaseDriver]:
        try:
            return self._classes[driver_id]
        except KeyError as exc:
            raise KeyError(
                f"Unknown driver id {driver_id!r}; registered: {self.driver_ids()}"
            ) from exc

    def create(self, driver_id: str, runner: ProcessRunner | None = None) -> BaseDriver:
        """Instantiate a driver.  Defaults to the non-executing runner."""
        return self.get_class(driver_id)(runner=runner or self._runner)

    def capabilities(self, driver_id: str) -> DriverCapabilities:
        return self.get_class(driver_id).capabilities()

    def describe_all(self) -> list[Mapping[str, Any]]:
        """Capability summaries for every registered driver."""
        return [self.get_class(did).describe() for did in self.driver_ids()]

    def display_name(self, driver_id: str) -> str:
        if not self.is_registered(driver_id):
            return driver_id or "(none)"
        return self.get_class(driver_id).display_name


def default_registry(runner: ProcessRunner | None = None) -> DriverRegistry:
    """Build a registry containing every v0.1 adapter."""
    registry = DriverRegistry(runner=runner)
    registry.register_all(IMPLEMENTED_DRIVERS)
    return registry
