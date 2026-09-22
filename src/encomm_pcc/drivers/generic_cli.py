"""Generic CLI engine adapter — **v0.1 placeholder**.

Represents "any other command-line agent".  It is deliberately modelled as a
**stateless** engine (``supports_sessions=False``) so the rest of the system is
forced to handle the session-less case rather than assuming session ids always
exist.
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

__all__ = ["GenericCliDriver"]


class GenericCliDriver(BaseDriver):
    """Adapter for an arbitrary CLI agent configured by the operator."""

    driver_id = "generic_cli"
    display_name = "Generic CLI"
    executables: tuple[str, ...] = ()

    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name=cls.display_name,
            supports_sessions=False,
            supports_resume=False,
            supports_streaming=False,
            supports_cancellation=True,
            supports_model_selection=False,
            implemented=False,
            notes=(
                "Placeholder. Stateless: a fresh process per prompt; the command "
                "line comes from role configuration, not from code."
            ),
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
