"""Driver abstraction: the contract every engine adapter must satisfy.

Design rules encoded here:

* **Roles are not drivers.**  A driver is an *engine* (Codex, Hermes, a generic
  CLI, later Claude Code / OpenCode / Ollama / Kimi).  Which engine fills which
  role is configuration, never code.
* **Session support is optional.**  ``supports_sessions`` distinguishes
  persistent-session engines from stateless ones; callers must branch on the
  capability instead of assuming a session id exists.
* **Placeholders never lie.**  A driver whose integration is not implemented
  advertises ``implemented=False`` and raises
  :class:`DriverNotImplementedError` on real work rather than returning fake
  success.
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..domain import AgentRole, SessionPolicy, utc_now
from ..domain.models import new_id
from .process import NullProcessRunner, ProcessRunner

__all__ = [
    "BaseDriver",
    "DriverCapabilities",
    "DriverError",
    "DriverNotImplementedError",
    "DriverSession",
    "PromptHandle",
    "PromptResult",
    "SessionRequest",
]


class DriverError(RuntimeError):
    """Base class for driver failures."""


class DriverNotImplementedError(DriverError):
    """Raised when a driver placeholder is asked to do real work.

    This is the correct v0.1 behaviour: the foundation must never start a real
    AI session.
    """


@dataclass(frozen=True, slots=True)
class DriverCapabilities:
    """What an engine can actually do.

    Callers must branch on these flags rather than assuming provider features.
    """

    driver_id: str
    display_name: str
    #: Engine can keep a conversation alive across prompts.
    supports_sessions: bool = False
    #: Engine can re-attach to a previously started session.
    supports_resume: bool = False
    #: Engine can stream partial output while a prompt runs.
    supports_streaming: bool = False
    #: Engine can abort a running prompt.
    supports_cancellation: bool = False
    #: Engine exposes a selectable model.
    supports_model_selection: bool = True
    #: Engine requires a Hermes-style profile for dispatch (Session 006).
    #: Engines that don't (e.g. Codex, which uses a workspace + model) are
    #: exempt from the profile preflight and from Hermes profile discovery.
    requires_profile: bool = True
    #: False while the adapter is a v0.1 placeholder.
    implemented: bool = False
    notes: str = ""

    @property
    def is_sessionless(self) -> bool:
        """True for stateless engines that must run fresh every time."""
        return not self.supports_sessions


@dataclass(slots=True)
class SessionRequest:
    """Everything a driver needs in order to (re)start a session.

    The driver receives *configuration*, not role logic — the same request
    shape is used for every role.
    """

    role: AgentRole
    workspace_path: str = ""
    project_profile: str = ""
    provider: str = ""
    model: str = ""
    session_policy: SessionPolicy = SessionPolicy.ALWAYS_NEW
    extra: dict[str, Any] = field(default_factory=dict)

    def cache_key(self) -> tuple[str, str, str, str]:
        """Identity of a session: changing any of these invalidates reuse."""
        return (
            self.role.value,
            self.workspace_path,
            self.project_profile,
            self.model,
        )


@dataclass(slots=True)
class DriverSession:
    """A (possibly not-yet-real) engine session handle.

    ``external`` is False while the handle only exists in our own state.  A
    real provider session id only appears once a driver is implemented.
    """

    driver_id: str
    role: AgentRole
    session_id: str | None = None
    persistent: bool = False
    external: bool = False
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def local_handle(
        cls,
        driver_id: str,
        role: AgentRole,
        persistent: bool,
        **metadata: Any,
    ) -> "DriverSession":
        """Create a locally-tracked handle with a generated id."""
        return cls(
            driver_id=driver_id,
            role=role,
            session_id=new_id("sess"),
            persistent=persistent,
            external=False,
            metadata=dict(metadata),
        )


@dataclass(slots=True)
class PromptHandle:
    """Reference to an in-flight prompt, used by ``wait_for_completion``."""

    session: DriverSession
    prompt: str
    handle_id: str = field(default_factory=lambda: new_id("ph"))
    started_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class PromptResult:
    """Outcome of one prompt.

    ``simulated`` marks results produced without contacting a real engine.
    Any consumer that sees ``simulated=True`` must treat the content as a
    placeholder, not as agent output.
    """

    ok: bool
    text: str = ""
    session_id: str | None = None
    exit_code: int | None = None
    duration_s: float = 0.0
    simulated: bool = False
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def failure(cls, error: str, **metadata: Any) -> "PromptResult":
        return cls(ok=False, error=error, metadata=dict(metadata))


class BaseDriver(ABC):
    """Abstract engine adapter.

    Subclasses declare :attr:`driver_id` and :attr:`capabilities`, then
    implement the lifecycle methods.  Stateless engines may implement
    ``start_session`` as a no-op returning a handle with ``session_id=None``;
    callers must therefore never treat a session id as guaranteed.
    """

    #: Stable identifier used in configuration and the driver registry.
    driver_id: str = "base"
    #: Human-readable name for the UI.
    display_name: str = "Base Driver"
    #: Executable names probed on PATH by :meth:`probe_availability`.
    executables: tuple[str, ...] = ()

    def __init__(self, runner: ProcessRunner | None = None) -> None:
        # Default to the runner that cannot execute anything.
        self._runner: ProcessRunner = runner or NullProcessRunner()
        self._current_session: DriverSession | None = None
        self._last_result: PromptResult | None = None

    # -- capability / discovery -----------------------------------------
    @classmethod
    @abstractmethod
    def capabilities(cls) -> DriverCapabilities:
        """Return the capability descriptor for this engine."""

    @classmethod
    def probe_availability(cls) -> bool:
        """True when a required executable exists on PATH.

        This **only** checks for the binary; it never launches it, so it is
        safe to call during the foundation phase.
        """
        if not cls.executables:
            return False
        return any(shutil.which(name) for name in cls.executables)

    # -- session lifecycle ----------------------------------------------
    @abstractmethod
    def start_session(self, request: SessionRequest) -> DriverSession:
        """Start (or logically open) a session for ``request``."""

    @abstractmethod
    def resume_session(self, session_id: str, request: SessionRequest) -> DriverSession:
        """Re-attach to ``session_id``.

        Stateless drivers raise :class:`DriverNotImplementedError` here.
        """

    def close_session(self, session: DriverSession | None = None) -> None:
        """Release the session.  Safe to call when no session is open."""
        target = session or self._current_session
        if target is None:
            return
        if self._current_session is not None and target.session_id == self._current_session.session_id:
            self._current_session = None

    # -- prompting -------------------------------------------------------
    @abstractmethod
    def send_prompt(self, session: DriverSession, prompt: str) -> PromptHandle:
        """Dispatch a prompt and return a handle to await."""

    @abstractmethod
    def wait_for_completion(
        self, handle: PromptHandle, timeout_s: float | None = None
    ) -> PromptResult:
        """Block until the prompt finishes (or the timeout expires)."""

    # -- introspection ---------------------------------------------------
    def get_session_id(self) -> str | None:
        """Current session id, or ``None`` for stateless engines."""
        return self._current_session.session_id if self._current_session else None

    def get_result(self) -> PromptResult | None:
        """Most recent :class:`PromptResult`, if any."""
        return self._last_result

    def cancel(self) -> bool:
        """Abort the in-flight prompt.  Returns True when something was aborted."""
        return False

    # -- helpers for subclasses -----------------------------------------
    def _set_session(self, session: DriverSession) -> DriverSession:
        self._current_session = session
        return session

    def _record_result(self, result: PromptResult) -> PromptResult:
        self._last_result = result
        return result

    def _not_implemented(self, operation: str) -> DriverNotImplementedError:
        return DriverNotImplementedError(
            f"{self.display_name} ({self.driver_id}): '{operation}' is a v0.1 "
            "placeholder and must not start a real engine session."
        )

    @classmethod
    def describe(cls) -> Mapping[str, Any]:
        caps = cls.capabilities()
        return {
            "driver_id": caps.driver_id,
            "display_name": caps.display_name,
            "implemented": caps.implemented,
            "supports_sessions": caps.supports_sessions,
            "binary_present": cls.probe_availability(),
        }
