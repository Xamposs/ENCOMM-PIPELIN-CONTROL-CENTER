"""Driver-neutral external-session discovery.

Session 006's generic contract (brief §10): a driver *may* expose the sessions
that already exist on the engine side so the UI can offer them for selection.
The UI never learns how a specific engine stores its sessions — it asks a
driver that supports discovery through this interface and renders
:class:`ExternalSessionDescriptor` rows.

Rules every discovery implementation must honour:

* **Read-only.**  Discovery never writes, moves or deletes engine state.
* **No credentials.**  Auth files and configuration secrets are never read.
* **Fail soft.**  Missing/corrupt/locked engine state yields an empty (or
  partial) result with an actionable message — never an exception through the
  UI.
* **Bounded.**  The number of returned sessions is capped; newest first.
* **Never fabricated.**  A descriptor's id/title/path come verbatim from the
  engine's own state; when the engine exposes no title, the title stays
  ``None``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

__all__ = [
    "DEFAULT_DISCOVERY_LIMIT",
    "ExternalSessionDescriptor",
    "SessionDiscoveryResult",
    "SessionDiscoverer",
    "normalise_workspace_key",
]

#: Default cap on returned sessions (newest first).
DEFAULT_DISCOVERY_LIMIT = 50


def normalise_workspace_key(path: str | None) -> str:
    """Case/spelling-insensitive comparable key for a workspace path.

    Windows paths are case-insensitive; comparisons for workspace matching
    must be too.  Returns a normalised string (never raises).
    """
    import os

    text = str(path or "").strip()
    if not text:
        return ""
    try:
        return os.path.normcase(os.path.normpath(text))
    except (ValueError, TypeError):  # pragma: no cover - defensive
        return os.path.normcase(text)


@dataclass(frozen=True, slots=True)
class ExternalSessionDescriptor:
    """One real session that exists on the engine side.

    ``session_id`` is the engine's own full id (never shortened, never
    invented).  ``title`` is whatever the engine recorded for the session, or
    ``None`` when it exposes none — the UI shows the id instead of inventing a
    label.  ``matches_workspace`` is computed by the discovery call when a
    workspace filter was supplied.
    """

    session_id: str
    driver_id: str
    title: str | None = None
    workspace_path: str | None = None
    updated_at: str | None = None
    source: str | None = None
    matches_workspace: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def label(self, max_chars: int = 72) -> str:
        """Human-readable combo label: title/project | id (full id preserved in data)."""
        parts = []
        if self.title:
            parts.append(self.title)
        if self.workspace_path:
            import os

            parts.append(os.path.basename(self.workspace_path) or self.workspace_path)
        head = " | ".join(parts) if parts else "Codex session"
        tail = self.session_id
        label = f"{head}  ({tail})"
        if len(label) <= max_chars:
            return label
        return label[: max_chars - 1] + "…"

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "driver_id": self.driver_id,
            "title": self.title,
            "workspace_path": self.workspace_path,
            "updated_at": self.updated_at,
            "source": self.source,
            "matches_workspace": self.matches_workspace,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class SessionDiscoveryResult:
    """Outcome of one discovery pass — always safe to render."""

    ok: bool
    driver_id: str
    sessions: list[ExternalSessionDescriptor] = field(default_factory=list)
    #: How the state was read (e.g. ``codex-rollouts``) — diagnostics only.
    mechanism: str = ""
    #: Actionable message when ``ok`` is False (or a note on partial reads).
    error: str | None = None
    #: Sessions seen but skipped (unreadable/locked/malformed) — diagnostics.
    skipped: int = 0

    @classmethod
    def failure(cls, driver_id: str, error: str, *, mechanism: str = "") -> "SessionDiscoveryResult":
        return cls(ok=False, driver_id=driver_id, error=error, mechanism=mechanism)


@runtime_checkable
class SessionDiscoverer(Protocol):
    """A driver that can list the sessions that already exist on the engine.

    Implementations must be read-only, bounded and fail soft (module
    docstring).  Drivers that do not support discovery simply do not implement
    this protocol; the UI checks with ``isinstance``/``getattr`` and hides the
    selector for them.
    """

    driver_id: str

    def discover_sessions(
        self,
        *,
        workspace_path: str | None = None,
        limit: int = DEFAULT_DISCOVERY_LIMIT,
    ) -> SessionDiscoveryResult:  # pragma: no cover - protocol
        ...
