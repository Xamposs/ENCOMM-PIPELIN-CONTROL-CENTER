"""Session policy engine.

This module is where the brief's session rules are enforced in one place:

===============  ==========================================
Role             Policy
===============  ==========================================
ORCHESTRATOR     persistent session optional
BUILDER          always a new session, for implement *and* fix
TASK_AUDITOR     one persistent session per batch
FINAL_AUDITOR    configurable: persistent or fresh
===============  ==========================================

Two invariants:

1. Session continuity is a performance optimisation only.  Durable truth is
   application state (SQLite + workspace files).
2. A driver that cannot hold sessions (``supports_sessions=False``) always gets
   ``NONE`` — the decision is capability-driven, never assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..domain import AgentRole, SessionPolicy
from ..drivers import DriverCapabilities
from ..persistence import Database

__all__ = [
    "ROLE_SESSION_POLICY_DESCRIPTION",
    "SessionAction",
    "SessionDecision",
    "SessionManager",
    "decide_session_action",
]


class SessionAction(str, Enum):
    """What to do about a session before running a unit of work."""

    #: Start a brand-new session.
    NEW = "NEW"
    #: Continue the existing session.
    REUSE = "REUSE"
    #: Engine is stateless; there is nothing to manage.
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class SessionDecision:
    """Result of applying a session policy."""

    action: SessionAction
    policy: SessionPolicy
    reason: str
    session_id: str | None = None

    @property
    def starts_new_session(self) -> bool:
        return self.action is SessionAction.NEW


#: Human-readable policy summary shown in the UI (read-only labels).
ROLE_SESSION_POLICY_DESCRIPTION: dict[AgentRole, str] = {
    AgentRole.ORCHESTRATOR: "Persistent session optional",
    AgentRole.BUILDER: "Always new",
    AgentRole.TASK_AUDITOR: "Persistent per batch",
    AgentRole.FINAL_AUDITOR: "Configurable (persistent or fresh)",
}


def decide_session_action(
    *,
    policy: SessionPolicy,
    capabilities: DriverCapabilities,
    existing_session_id: str | None,
    same_batch: bool = True,
) -> SessionDecision:
    """Decide whether to reuse, replace or skip a session.

    ``same_batch`` tells a per-batch policy whether the existing session belongs
    to the batch we are still working on.
    """
    if not capabilities.supports_sessions:
        return SessionDecision(
            action=SessionAction.NONE,
            policy=policy,
            reason=f"{capabilities.display_name} is stateless; a fresh process is used per prompt.",
        )

    has_session = bool(existing_session_id)

    if policy is SessionPolicy.ALWAYS_NEW:
        return SessionDecision(
            action=SessionAction.NEW,
            policy=policy,
            reason="Policy 'always_new': every unit of work gets its own session.",
        )

    if policy is SessionPolicy.PERSISTENT_PER_BATCH:
        if has_session and same_batch:
            return SessionDecision(
                action=SessionAction.REUSE,
                policy=policy,
                reason="Policy 'persistent_per_batch': reusing the session for this batch.",
                session_id=existing_session_id,
            )
        return SessionDecision(
            action=SessionAction.NEW,
            policy=policy,
            reason="Policy 'persistent_per_batch': a new batch requires a new session.",
        )

    if policy is SessionPolicy.PERSISTENT:
        if has_session:
            return SessionDecision(
                action=SessionAction.REUSE,
                policy=policy,
                reason="Policy 'persistent': reusing the open session.",
                session_id=existing_session_id,
            )
        return SessionDecision(
            action=SessionAction.NEW,
            policy=policy,
            reason="Policy 'persistent': no open session yet.",
        )

    # PERSISTENT_OPTIONAL and CONFIGURABLE both prefer continuity but never
    # require it; the caller may override.
    if has_session:
        return SessionDecision(
            action=SessionAction.REUSE,
            policy=policy,
            reason=f"Policy '{policy.value}': reusing the open session.",
            session_id=existing_session_id,
        )
    return SessionDecision(
        action=SessionAction.NEW,
        policy=policy,
        reason=f"Policy '{policy.value}': no session available to reuse.",
    )


class SessionManager:
    """Tracks the live session per role and mirrors it into SQLite.

    The manager owns *bookkeeping only*: it never talks to an engine.
    """

    def __init__(
        self,
        database: Database | None = None,
        workspace_id: str = "",
        batch_generation: int = 0,
    ) -> None:
        self._database = database
        self._workspace_id = workspace_id
        self._batch_generation = batch_generation
        self._sessions: dict[AgentRole, str] = {}
        self._session_batch: dict[AgentRole, int] = {}

    # -- batch lifecycle --------------------------------------------------
    def begin_new_batch(self) -> int:
        """Advance the batch generation, invalidating per-batch sessions."""
        self._batch_generation += 1
        return self._batch_generation

    @property
    def batch_generation(self) -> int:
        return self._batch_generation

    def set_workspace(self, workspace_id: str) -> None:
        self._workspace_id = workspace_id
        self._sessions.clear()
        self._session_batch.clear()

    # -- queries ----------------------------------------------------------
    def current_session_id(self, role: AgentRole) -> str | None:
        return self._sessions.get(role)

    def session_is_for_current_batch(self, role: AgentRole) -> bool:
        return self._session_batch.get(role, -1) == self._batch_generation

    def decide(
        self, role: AgentRole, policy: SessionPolicy, capabilities: DriverCapabilities
    ) -> SessionDecision:
        return decide_session_action(
            policy=policy,
            capabilities=capabilities,
            existing_session_id=self.current_session_id(role),
            same_batch=self.session_is_for_current_batch(role),
        )

    # -- mutations ---------------------------------------------------------
    def register_session(self, role: AgentRole, session_id: str, driver_id: str) -> None:
        """Record a session id for ``role`` in memory and in the database."""
        self._sessions[role] = session_id
        self._session_batch[role] = self._batch_generation
        if self._database is not None and self._workspace_id:
            self._database.record_session(
                session_id=session_id,
                workspace_id=self._workspace_id,
                role=role,
                driver_id=driver_id,
                persistent=True,
                external=False,
                metadata={"batch_generation": self._batch_generation},
            )

    def clear_session(self, role: AgentRole) -> None:
        """Forget the session for ``role`` (does not delete history)."""
        session_id = self._sessions.pop(role, None)
        self._session_batch.pop(role, None)
        if session_id and self._database is not None:
            self._database.close_session(session_id)

    def snapshot(self) -> dict[str, Any]:
        return {
            "workspace_id": self._workspace_id,
            "batch_generation": self._batch_generation,
            "sessions": {role.value: sid for role, sid in self._sessions.items()},
        }
