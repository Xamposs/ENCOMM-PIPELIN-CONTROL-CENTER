"""Codex engine adapter — **v0.1 placeholder**.

The adapter exists so Codex is a *configurable option*, never a hardcoded
assumption.  It advertises its intended capabilities honestly
(``implemented=False``) and refuses real work until a later phase wires it to
:class:`~encomm_pcc.drivers.process.SubprocessRunner`.
"""

from __future__ import annotations

from .base import (
    BaseDriver,
    DriverCapabilities,
    DriverSession,
    PromptHandle,
    PromptResult,
    SessionRequest,
)

__all__ = ["CodexDriver"]


class CodexDriver(BaseDriver):
    """Adapter for the Codex CLI agent (not yet implemented)."""

    driver_id = "codex"
    display_name = "Codex"
    executables = ("codex", "codex.cmd", "codex.exe")

    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name=cls.display_name,
            supports_sessions=True,
            supports_resume=True,
            supports_streaming=False,
            supports_cancellation=True,
            supports_model_selection=True,
            implemented=False,
            notes="Placeholder. Will run the Codex CLI in a supervised workdir.",
        )

    def start_session(self, request: SessionRequest) -> DriverSession:
        raise self._not_implemented("start_session")

    def resume_session(self, session_id: str, request: SessionRequest) -> DriverSession:
        raise self._not_implemented("resume_session")

    def send_prompt(self, session: DriverSession, prompt: str) -> PromptHandle:
        raise self._not_implemented("send_prompt")

    def wait_for_completion(
        self, handle: PromptHandle, timeout_s: float | None = None
    ) -> PromptResult:
        raise self._not_implemented("wait_for_completion")
