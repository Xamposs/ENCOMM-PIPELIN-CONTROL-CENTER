"""Real Hermes discovery for Proposal Mode selectors — profile / provider /
model / session.  DISCOVER, NEVER INVENT (Session 019 brief §10).

Read-only surfaces, in priority order:

* **Profiles** — the production ``core.hermes_profiles.discover_profiles()``
  (CLI ``profile list`` with filesystem scan fallback; no model calls).
* **Provider / model** — the installed Hermes CLI exposes NO authoritative
  provider/model listing command (verified against v0.21.5: ``hermes model``
  is interactive-only and ``hermes model list`` is an argument error), so
  this module derives HONEST defaults from the selected profile's own
  ``config.yaml`` (``model.provider`` / ``model.default`` / the ``providers:``
  map) when that file is readable, and always labels the result as
  profile-derived rather than exhaustive.  The UI keeps the combos EDITABLE
  so any other value remains operator-enterable.
* **Sessions** — the production profile-scoped
  ``drivers.hermes_discovery.HermesSessionDiscovery`` (Priority A CLI
  listing, Priority B read-only store fallback).

No model call is ever made here.  All helpers are fail-soft: an unavailable
surface returns an empty/honest "unavailable" result, never a fabricated
list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.hermes_profiles import discover_profiles
from ..drivers.hermes import HermesDriver
from ..drivers.session_discovery import SessionDiscoveryResult

__all__ = [
    "HERMES_DISCOVERY_SOURCE_CONFIG",
    "HERMES_DISCOVERY_SOURCE_NONE",
    "ProfileOptions",
    "ProviderModelOptions",
    "SessionOptions",
    "discover_hermes_profile_names",
    "discover_hermes_provider_model",
    "discover_hermes_sessions",
]

#: Honest source labels surfaced in the UI next to the options.
HERMES_DISCOVERY_SOURCE_CONFIG = "profile-config"
HERMES_DISCOVERY_SOURCE_NONE = "unavailable"

_HERMES_CONFIG_FILENAME = "config.yaml"


@dataclass(slots=True)
class ProfileOptions:
    """Real Hermes profile names (never invented)."""

    profiles: list[str] = field(default_factory=list)
    source: str = ""  # "cli" or "filesystem" (from discover_profiles)
    ok: bool = False
    error: str = ""


@dataclass(slots=True)
class ProviderModelOptions:
    """Profile-derived provider/model defaults — explicitly NOT exhaustive.

    ``current_provider`` / ``current_model`` are what the profile's own
    config names; ``known_providers`` are the additional provider blocks the
    config defines (empty when the config is unreadable — never guessed).
    """

    current_provider: str = ""
    current_model: str = ""
    known_providers: list[str] = field(default_factory=list)
    source: str = HERMES_DISCOVERY_SOURCE_NONE
    error: str = ""

    @property
    def available(self) -> bool:
        return self.source == HERMES_DISCOVERY_SOURCE_CONFIG


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


def _profile_config_path(profile: str, environ: dict[str, str] | None) -> Path | None:
    """Locate one profile's ``config.yaml`` through the SAME roots the
    production profile scanner walks (no invented paths)."""
    from ..core.hermes_profiles import profile_roots

    name = str(profile or "").strip()
    if not name:
        return None
    for root in profile_roots(environ=environ):
        # Named profiles live under the home's profiles/ directory (the same
        # walk _scan_root uses); the default profile may sit at the root.
        for candidate in (
            root / "profiles" / name / _HERMES_CONFIG_FILENAME,
            root / name / _HERMES_CONFIG_FILENAME,
        ):
            if candidate.is_file():
                return candidate
    return None


def _load_yaml_config(path: Path) -> dict[str, Any] | None:
    try:
        import yaml  # PyYAML (lazy import; shipped with the app)
    except ImportError:  # pragma: no cover - environment guard
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a broken config is "unavailable"
        return None
    return data if isinstance(data, dict) else None


def discover_hermes_provider_model(
    profile: str, environ: dict[str, str] | None = None
) -> ProviderModelOptions:
    """Profile-derived provider/model defaults from the profile's config.

    This is an HONEST profile-derived suggestion, NOT an authoritative model
    catalogue: the installed CLI exposes no such listing.  The UI must keep
    the fields editable and label them accordingly.
    """
    path = _profile_config_path(profile, environ)
    if path is None:
        return ProviderModelOptions(
            error=f"no config.yaml found for profile {profile!r}"
        )
    data = _load_yaml_config(path)
    if data is None:
        return ProviderModelOptions(
            error=f"config.yaml for profile {profile!r} is unreadable"
        )
    model_block = data.get("model")
    current_provider = ""
    current_model = ""
    if isinstance(model_block, dict):
        current_provider = str(model_block.get("provider") or "")
        current_model = str(model_block.get("default") or "")
    known: list[str] = []
    providers_block = data.get("providers")
    if isinstance(providers_block, dict):
        for key in providers_block:
            name = str(key or "").strip()
            if name and name not in known:
                known.append(name)
    if not current_provider and not current_model and not known:
        return ProviderModelOptions(
            error=f"config.yaml for profile {profile!r} names no provider/model"
        )
    return ProviderModelOptions(
        current_provider=current_provider,
        current_model=current_model,
        known_providers=known,
        source=HERMES_DISCOVERY_SOURCE_CONFIG,
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
