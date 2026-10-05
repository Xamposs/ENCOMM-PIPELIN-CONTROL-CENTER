"""Real Hermes discovery for Proposal Mode selectors — profile / provider /
model / session.  DISCOVER, NEVER INVENT (Session 019 brief §10).

Read-only surfaces, in priority order:

* **Profiles** — the production ``core.hermes_profiles.discover_profiles()``
  (CLI ``profile list`` with filesystem scan fallback; no model calls).
* **Provider / model** — moved OUT of this module in Session 022: the
  authoritative in-package inventory discovery lives in
  ``hermes_model_catalog.discover_hermes_model_catalog`` (the same substrate
  Hermes' own pickers use).  This module keeps profiles + sessions.
* **Sessions** — the production profile-scoped
  ``drivers.hermes_discovery.HermesSessionDiscovery`` (Priority A CLI
  listing, Priority B read-only store fallback).

No model call is ever made here.  All helpers are fail-soft: an unavailable
surface returns an empty/honest "unavailable" result, never a fabricated
list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.hermes_profiles import discover_profiles
from ..drivers.hermes import HermesDriver
from ..drivers.session_discovery import SessionDiscoveryResult

__all__ = [
    "ProfileOptions",
    "SessionOptions",
    "discover_hermes_profile_names",
    "discover_hermes_sessions",
]


@dataclass(slots=True)
class ProfileOptions:
    """Real Hermes profile names (never invented)."""

    profiles: list[str] = field(default_factory=list)
    source: str = ""  # "cli" or "filesystem" (from discover_profiles)
    ok: bool = False
    error: str = ""


@dataclass(slots=True)
class SessionOptions:
    """Profile-scoped real sessions (the generic discovery contract)."""

    session_ids: list[str] = field(default_factory=list)
    mechanism: str = ""
    ok: bool = False
    error: str = ""


def discover_hermes_profile_names(environ: dict[str, str] | None = None) -> ProfileOptions:
    """Real profile discovery (CLI first, filesystem fallback) — read-only."""
    try:
        result = discover_profiles(environ=environ)
    except Exception as exc:  # noqa: BLE001 - discovery must never raise
        return ProfileOptions(ok=False, error=f"{type(exc).__name__}: {exc}")
    return ProfileOptions(
        profiles=list(result.profiles),
        source=result.method,
        ok=bool(result.ok),
        error=result.error or "",
    )


def discover_hermes_sessions(
    *,
    profile: str,
    workspace_path: str | None = None,
    limit: int = 25,
    runner: Any = None,
) -> SessionOptions:
    """Profile-scoped real session discovery through the production bridge.

    ``runner`` defaults to the driver's own resolution (production passes a
    real ``SubprocessRunner``); the result is bounded and read-only.
    """
    discovery = HermesSessionDiscovery_bridge(runner=runner)
    result: SessionDiscoveryResult = discovery.discover_sessions(
        profile=str(profile or "").strip() or None,
        workspace_path=workspace_path,
        limit=max(1, int(limit)),
    )
    ids = [s.session_id for s in result.sessions if s.session_id]
    return SessionOptions(
        session_ids=ids,
        mechanism=result.mechanism,
        ok=bool(result.ok),
        error=result.error or "",
    )


def HermesSessionDiscovery_bridge(runner: Any = None):
    """Local indirection so tests can monkeypatch session discovery."""
    from ..drivers.hermes_discovery import HermesSessionDiscovery

    return HermesSessionDiscovery(runner=runner)


#: Kept for callers that want to assert the driver the bridge prefers.
HERMES_DRIVER_ID = HermesDriver.driver_id
