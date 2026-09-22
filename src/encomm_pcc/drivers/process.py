"""Process abstraction for future CLI drivers.

Nothing in v0.1 executes a real agent process.  This module exists so that
future drivers have exactly one place to spawn and supervise child processes,
and so the placeholder drivers can be wired to a runner that physically cannot
launch anything (:class:`NullProcessRunner`).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol, Sequence, runtime_checkable

__all__ = [
    "NullProcessRunner",
    "ProcessResult",
    "ProcessRunner",
    "ProcessSpec",
    "SubprocessRunner",
]


@dataclass(frozen=True, slots=True)
class ProcessSpec:
    """Declarative description of a process to run.

    Drivers build a ``ProcessSpec`` instead of touching ``subprocess``
    directly, which keeps command construction testable without executing it.
    """

    argv: Sequence[str]
    cwd: Path | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    stdin_text: str | None = None
    timeout_s: float | None = None
    #: When True the child is launched without a console window (Windows).
    no_window: bool = True

    def argv_list(self) -> list[str]:
        return [str(a) for a in self.argv]


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Outcome of a finished process."""

    argv: list[str]
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@runtime_checkable
class ProcessRunner(Protocol):
    """Minimal interface every driver uses to run a child process."""

    def run(self, spec: ProcessSpec) -> ProcessResult:  # pragma: no cover - protocol
        ...


class SubprocessRunner:
    """Real runner.  **Not used by any v0.1 driver.**

    It is provided so the executor phase can adopt it without inventing a new
    abstraction, and so the abstraction itself is exercised by tests.
    """

    def run(self, spec: ProcessSpec) -> ProcessResult:
        import time

        started = time.monotonic()
        creationflags = 0
        if spec.no_window and hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags = subprocess.CREATE_NO_WINDOW

        completed = subprocess.run(  # noqa: S603 - argv is a list, shell=False
            spec.argv_list(),
            cwd=str(spec.cwd) if spec.cwd else None,
            env={**dict(spec.env)} or None,
            input=spec.stdin_text,
            capture_output=True,
            text=True,
            timeout=spec.timeout_s,
            shell=False,
            creationflags=creationflags,
            check=False,
        )
        return ProcessResult(
            argv=spec.argv_list(),
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            duration_s=time.monotonic() - started,
            timed_out=False,
        )


class NullProcessRunner:
    """Runner that refuses to execute anything.

    Placeholder drivers are constructed with this runner so a coding mistake
    cannot start a real agent session during the foundation phase.
    """

    def __init__(self) -> None:
        self.attempted_specs: list[ProcessSpec] = []

    def run(self, spec: ProcessSpec) -> ProcessResult:
        self.attempted_specs.append(spec)
        raise RuntimeError(
            "NullProcessRunner: process execution is disabled in the v0.1 "
            f"foundation (attempted: {' '.join(spec.argv_list())!r})"
        )
