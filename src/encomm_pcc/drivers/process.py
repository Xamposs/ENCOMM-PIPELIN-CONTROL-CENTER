"""Process abstraction for CLI drivers.

Drivers never touch ``subprocess`` directly.  They build a declarative
:class:`ProcessSpec` and hand it to a :class:`ProcessRunner`:

* :class:`SubprocessRunner` — the real implementation, adopted by the executor
  phase.  It is the **only** place in the application that spawns a process.
* :class:`NullProcessRunner` — the default for every driver unless the caller
  injects a real runner.  It physically cannot launch anything, so a wiring
  mistake cannot start an agent session.

Process-safety contract (Session 002):

* argv lists only, ``shell=False`` — no command string is ever interpreted.
* stdout, stderr and the exit code are always captured.
* a timeout **kills the whole child tree** (``taskkill /T`` on Windows,
  process group kill elsewhere) so no detached orphan survives the run;
  the result is marked ``timed_out`` and never reported as success.
* the caller passes an explicit ``cwd``; no runner chooses a directory.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol, Sequence, runtime_checkable

__all__ = [
    "NullProcessRunner",
    "ProcessResult",
    "ProcessRunner",
    "ProcessSpec",
    "SubprocessRunner",
    "TIMEOUT_EXIT_CODE",
    "kill_process_tree",
]

#: Exit code recorded when a child was killed because its timeout expired.
#: ``ProcessResult.timed_out`` is the authoritative signal — this numeric value
#: exists only so the field is never mistaken for a real process exit status.
TIMEOUT_EXIT_CODE = -1


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


def kill_process_tree(process: "subprocess.Popen[str]") -> None:
    """Best-effort kill of ``process`` **and everything it spawned**.

    A one-shot agent CLI can start its own children (tool subprocesses).  Killing
    only the direct child would leave them detached, so on Windows the tree is
    terminated with ``taskkill /T`` and elsewhere the child's process group is
    signalled.  Every step is best-effort: the caller is already handling a
    failure, and a cleanup error must not mask it.
    """

    if process.poll() is not None:
        return

    if os.name == "nt":  # pragma: no cover - exercised on the Windows host
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
                creationflags=creationflags,
            )
        except Exception:  # noqa: BLE001 - cleanup is best-effort by contract
            pass
    else:  # pragma: no cover - POSIX path, this project runs on Windows
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass

    try:
        process.kill()
    except Exception:  # noqa: BLE001
        pass


class SubprocessRunner:
    """Real runner: the executor phase's only way to start a process.

    ``subprocess.run`` cannot report a timeout as data, so this uses
    :class:`subprocess.Popen` directly: the child is waited on with
    ``communicate(timeout=...)``, and on expiry the tree is killed and the
    partial output is still returned with ``timed_out=True``.
    """

    def run(self, spec: ProcessSpec) -> ProcessResult:
        started = time.monotonic()
        creationflags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            if spec.no_window:
                creationflags |= subprocess.CREATE_NO_WINDOW
            # Own process group, so a timeout can terminate the whole tree.
            creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        popen_kwargs: dict[str, object] = {}
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True

        process: subprocess.Popen[str] = subprocess.Popen(  # noqa: S603 - argv list, shell=False
            spec.argv_list(),
            cwd=str(spec.cwd) if spec.cwd else None,
            env={**dict(spec.env)} or None,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            creationflags=creationflags,
            **popen_kwargs,  # type: ignore[arg-type]
        )

        timed_out = False
        try:
            stdout, stderr = process.communicate(input=spec.stdin_text, timeout=spec.timeout_s)
            exit_code = int(process.returncode if process.returncode is not None else TIMEOUT_EXIT_CODE)
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_process_tree(process)
            try:
                stdout, stderr = process.communicate(timeout=30)
            except Exception:  # noqa: BLE001 - the capture is best-effort after a kill
                stdout, stderr = "", ""
            exit_code = TIMEOUT_EXIT_CODE

        return ProcessResult(
            argv=spec.argv_list(),
            exit_code=exit_code,
            stdout=stdout or "",
            stderr=stderr or "",
            duration_s=time.monotonic() - started,
            timed_out=timed_out,
        )


class NullProcessRunner:
    """Runner that refuses to execute anything.

    Drivers are constructed with this runner unless the caller injects a real
    one, so a coding mistake cannot start a real engine session.
    """

    def __init__(self) -> None:
        self.attempted_specs: list[ProcessSpec] = []

    def run(self, spec: ProcessSpec) -> ProcessResult:
        self.attempted_specs.append(spec)
        raise RuntimeError(
            "NullProcessRunner: process execution is disabled for this driver "
            f"(attempted: {' '.join(spec.argv_list())!r})"
        )