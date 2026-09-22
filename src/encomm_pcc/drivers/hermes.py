"""Hermes engine adapter — **v0.1 placeholder**.

Hermes is addressed through a *profile* plus provider/model, which is why
:class:`SessionRequest` carries those fields for every driver.
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

__all__ = ["HermesDriver"]


class HermesDriver(BaseDriver):
    """Adapter for Hermes agent profiles (not yet implemented)."""

    driver_id = "hermes"
    display_name = "Hermes"
    executables = ("hermes", "hermes.cmd", "hermes.exe")

    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name=cls.display_name,
            supports_sessions=True,
            supports_resume=True,
            supports_streaming=True,
            supports_cancellation=True,
            supports_model_selection=True,
            implemented=False,
            notes="Placeholder. Will drive a named Hermes profile non-interactively.",
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
