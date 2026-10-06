"""Real Hermes provider/model catalog for Proposal Mode selectors (Session 022).

Replaces the Session 019 profile-config-derived suggestion with an
AUTHORITATIVE read-only in-package inventory — the same substrate Hermes
itself uses for the dashboard ``/api/model/options`` page, the TUI ``/model``
picker, and the ACP model picker (``acp_adapter/model_catalog.py`` builds on
``hermes_cli.inventory.build_models_payload``).

Verified against the installed Hermes (v0.21.5+6774.g07797d0, 2026-10-05):
there is NO non-interactive CLI listing command, so discovery runs a SHIPPED
bootstrap script under the Hermes installation's own venv python
(``<install>/venv/Scripts/python.exe``), sets ``HERMES_HOME``/``HERMES_PROFILE``
for the selected profile, and calls the in-package inventory in the same
OFFLINE shape the ACP picker uses (``refresh=False``,
``probe_custom_providers=False``, ``probe_current_custom_provider=False``).
Zero model calls, zero network probes, no writes, no credential reads: the
payload rows carry only slugs, display names and model ids, and the bootstrap
emits a single JSON document parsed with the strict (no-eval) loader.

Fail-soft everywhere: a missing venv, a Hermes update that renames the
inventory API, a broken child, or malformed output yields an honest
"unavailable" result — never a fabricated catalog.  The UI combos stay
EDITABLE either way (discovery is a suggestion, never a cage).

Session 023 (§A fallback chain): when the inventory path fails for ANY
reason — e.g. ONE unreadable optional plugin makes ``load_picker_context()``
raise ``PermissionError`` — discovery degrades to the SELECTED PROFILE's
own configured defaults (``source="profile-config"``,
``profile_default_provider`` / ``profile_default_model`` populated from the
profile's real ``config.yaml``).  The provider LIST stays honestly empty in
that mode (only the profile's defaults are known); a runtime that only
needs the defaults keeps working, and no provider/model name is ever
invented.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..drivers.process import ProcessSpec, SubprocessRunner

__all__ = [
    "HERMES_CATALOG_SOURCE_INVENTORY",
    "HERMES_CATALOG_SOURCE_NONE",
    "HermesModelCatalog",
    "discover_hermes_model_catalog",
]

#: Authoritative in-package inventory (Hermes' own picker substrate).
HERMES_CATALOG_SOURCE_INVENTORY = "hermes-inventory"
HERMES_CATALOG_SOURCE_NONE = "unavailable"
#: Session 023 fallback: the SELECTED PROFILE's own configured defaults,
#: read from its config.yaml when the inventory is unavailable (an
#: unreadable optional plugin must never make the defaults unknowable).
#: NO provider list is claimed — only the profile's provider/model pair.
HERMES_CATALOG_SOURCE_PROFILE_CONFIG = "profile-config"

#: Bounded child budget: the payload for a fully-authed host stays tiny, the
#: cap only exists so a pathological Hermes update can never hang discovery.
_CHILD_TIMEOUT_S = 60.0
_MAX_PROVIDERS = 64
_MAX_MODELS_PER_PROVIDER = 200

#: The exact API the bootstrap needs.  A Hermes update that removes any of
#: these names fails the import guard INSIDE the child (typed error above),
#: never a half-typed AttributeError mid-payload.
_REQUIRED_INVENTORY_API = ("load_picker_context", "build_models_payload")

_BOOTSTRAP_SCRIPT = r'''
import json, os, sys

HERMES_AGENT = os.environ["HERMES_AGENT_DIR"]
sys.path.insert(0, HERMES_AGENT)

try:
    _spec = json.loads(sys.stdin.read() or "{}")
except Exception:
    _spec = {}
REQUIRED_API = [str(n) for n in (_spec.get("required_api") or [])]
MAX_PROVIDERS = int(_spec.get("max_providers") or 64)
MAX_MODELS = int(_spec.get("max_models") or 200)

try:
    from hermes_cli import inventory as _inventory
except Exception as exc:
    print(json.dumps({"error": "hermes_import_failed: %s: %s" % (type(exc).__name__, exc)}))
    raise SystemExit(0)

missing = [n for n in REQUIRED_API if not hasattr(_inventory, n)]
if missing:
    print(json.dumps({"error": "hermes_inventory_api_missing: %s" % ", ".join(missing)}))
    raise SystemExit(0)

try:
    ctx = _inventory.load_picker_context()
    payload = _inventory.build_models_payload(
        ctx,
        explicit_only=False,
        include_unconfigured=False,
        picker_hints=False,
        canonical_order=True,
        pricing=False,
        capabilities=False,
        refresh=False,
        probe_custom_providers=False,
        probe_current_custom_provider=False,
        max_models=MAX_MODELS,
    )
except Exception as exc:
    print(json.dumps({"error": "hermes_inventory_failed: %s: %s" % (type(exc).__name__, exc)}))
    raise SystemExit(0)

rows = payload.get("providers") or []
out = {
    "profile_default_provider": ctx.current_provider,
    "profile_default_model": ctx.current_model,
    "providers": [],
}
for row in rows[:MAX_PROVIDERS]:
    slug = str(row.get("slug") or "").strip()
    if not slug:
        continue
    models = []
    for m in (row.get("models") or [])[:MAX_MODELS]:
        mid = str(m.get("id") if isinstance(m, dict) else m or "").strip()
        if mid:
            models.append(mid)
    out["providers"].append(
        {
            "slug": slug,
            "name": str(row.get("name") or "").strip(),
            "models": models,
            "total_models": len(models),
            "source": str(row.get("source") or "").strip(),
            "is_current": bool(row.get("is_current")),
            "is_user_defined": bool(row.get("is_user_defined")),
        }
    )
print(json.dumps(out))
'''


@dataclass(slots=True)
class HermesProviderEntry:
    """One real provider row from the Hermes inventory (no secrets)."""

    slug: str
    name: str = ""
    models: list[str] = field(default_factory=list)
    total_models: int = 0
    source: str = ""
    is_current: bool = False
    is_user_defined: bool = False


@dataclass(slots=True)
class HermesModelCatalog:
    """The real provider/model catalog for ONE Hermes profile.

    Honesty metadata (Session 022A): ``provider_catalog_authoritative`` means
    the PROVIDER LIST comes from Hermes' own picker substrate (which providers
    are configured is a fact about the installation).  ``model_lists_exhaustive``
    is False by design: a provider may expose models its curated list omits,
    so the UI keeps every combo EDITABLE and no runtime behavior may treat a
    model list as complete.  Discovery failures degrade to
    ``source="unavailable"`` with ``error`` populated — never a fabricated
    list.
    """

    profile: str = ""
    providers: list[HermesProviderEntry] = field(default_factory=list)
    models_by_provider: dict[str, list[str]] = field(default_factory=dict)
    profile_default_provider: str = ""
    profile_default_model: str = ""
    source: str = HERMES_CATALOG_SOURCE_NONE
    #: The provider list is Hermes' own authoritative picker substrate.
    provider_catalog_authoritative: bool = False
    #: Curated per-provider model lists can lag a provider's newest models.
    model_lists_exhaustive: bool = False
    error: str = ""

    @property
    def available(self) -> bool:
        # Session 023: the profile-config fallback is also "available" —
        # the operator's runtime (profile defaults) genuinely works; only
        # the provider LIST is narrower.  Unavailable still means BOTH
        # legs failed (source == "unavailable").
        return self.source in (
            HERMES_CATALOG_SOURCE_INVENTORY,
            HERMES_CATALOG_SOURCE_PROFILE_CONFIG,
        )

    def provider_names(self) -> list[str]:
        """Deterministic de-duplicated provider names (slug, inventory order)."""
        seen: list[str] = []
        for entry in self.providers:
            if entry.slug and entry.slug not in seen:
                seen.append(entry.slug)
        return seen

    def models_for_provider(self, provider: str) -> list[str]:
        """Real model ids for ONE provider (empty when unknown — never guessed)."""
        key = str(provider or "").strip()
        for entry in self.providers:
            if entry.slug == key:
                return list(entry.models)
        return []


def _hermes_install_dir(environ: dict[str, str] | None) -> str:
    """Locate the installed hermes-agent package directory (read-only)."""
    import os
    from pathlib import Path

    env = environ if environ is not None else dict(os.environ)
    candidate = str(env.get("HERMES_AGENT_DIR", "")).strip()
    if candidate:
        return candidate
    local = str(env.get("LOCALAPPDATA", "")).strip()
    if local:
        install = Path(local) / "hermes" / "hermes-agent"
        if (install / "hermes_cli").is_dir():
            return str(install)
    return ""


def _hermes_venv_python(install_dir: str) -> str:
    """The installation's own interpreter (carries hermes_cli + its deps)."""
    from pathlib import Path

    if os_name() == "nt":
        candidate = Path(install_dir) / "venv" / "Scripts" / "python.exe"
    else:  # pragma: no cover - POSIX shape, host is Windows
        candidate = Path(install_dir) / "venv" / "bin" / "python"
    return str(candidate) if candidate.is_file() else ""


def os_name() -> str:  # pragma: no cover - trivial indirection for tests
    import os

    return os.name


def _profile_home_dir(profile: str, environ: dict[str, str] | None) -> str:
    """Resolve one profile's home directory through the SAME two-level walk
    the production profile scanner uses (``<root>/profiles/<name>``, then a
    root-level default).  ``HERMES_PROFILE`` alone does NOT scope
    ``load_config()`` — ``HERMES_HOME`` does — so catalog discovery must
    point the child's home at the profile directory itself.
    """
    from pathlib import Path

    from ..core.hermes_profiles import profile_roots

    name = str(profile or "").strip()
    if not name:
        return ""
    for root in profile_roots(environ=environ):
        for candidate in (root / "profiles" / name, root / name):
            if (candidate / "config.yaml").is_file():
                return str(candidate)
        # Session 022A: a root-level config.yaml IS the DEFAULT profile's
        # home (the classic single-home layout — the profile id stays
        # "default" even when renamed for display).  Without this, the
        # default profile discovered no catalog at all.
        if name == "default" and (root / "config.yaml").is_file():
            return str(root)
    return ""


def discover_hermes_model_catalog(
    profile: str,
    *,
    environ: dict[str, str] | None = None,
    runner: Any = None,
    hermes_home: str = "",
) -> HermesModelCatalog:
    """Authoritative provider/model catalog for ONE profile — zero model calls.

    Runs the shipped bootstrap under the Hermes installation's own venv
    python with ``HERMES_HOME`` scoped to the selected profile's directory
    (verified: the inventory reads the config through ``HERMES_HOME``; the
    ``HERMES_PROFILE`` variable alone does not scope it).  The child imports
    ``hermes_cli.inventory`` and builds the SAME offline payload Hermes' own
    pickers use.  Any failure degrades honestly.
    """
    name = str(profile or "").strip()
    if not name:
        return HermesModelCatalog(error="no profile selected")
    install_dir = _hermes_install_dir(environ)
    if not install_dir:
        return HermesModelCatalog(
            profile=name, error="hermes installation directory not found"
        )
    python = _hermes_venv_python(install_dir)
    if not python:
        return HermesModelCatalog(
            profile=name, error="hermes venv python not found"
        )
    home = str(hermes_home or "").strip() or _profile_home_dir(name, environ)
    if not home:
        return HermesModelCatalog(
            profile=name, error=f"profile home not found for {name!r}"
        )

    import os

    child_env = {
        **(os.environ if environ is None else dict(environ)),
        "HERMES_AGENT_DIR": install_dir,
        "HERMES_HOME": home,
    }
    child_env["HERMES_PROFILE"] = name

    # A stored bootstrap path (tests) or the shipped inline script.
    script_path = (environ or {}).get("HERMES_CATALOG_BOOTSTRAP", "").strip()
    if script_path:
        argv = [python, script_path]
    else:
        argv = [python, "-I", "-c", _BOOTSTRAP_SCRIPT]

    spec = ProcessSpec(
        argv=argv,
        env=child_env,
        timeout_s=_CHILD_TIMEOUT_S,
        stdin_text=json.dumps(
            {
                "required_api": list(_REQUIRED_INVENTORY_API),
                "max_providers": _MAX_PROVIDERS,
                "max_models": _MAX_MODELS_PER_PROVIDER,
            }
        ),
    )
    real_runner = runner if runner is not None else SubprocessRunner()
    try:
        result = real_runner.run(spec)
    except Exception as exc:  # noqa: BLE001 - discovery must never raise
        return _profile_config_fallback(
            name, environ, reason=f"inventory runner failed: {type(exc).__name__}: {exc}", runner=real_runner
        )
    if result.timed_out:
        return _profile_config_fallback(
            name, environ, reason="hermes inventory timed out", runner=real_runner
        )
    if result.exit_code != 0:
        return _profile_config_fallback(
            name, environ, reason=f"hermes inventory exited {result.exit_code}", runner=real_runner
        )
    return _parse_inventory_payload(result.stdout, profile=name, environ=environ, runner=real_runner)


def _profile_config_fallback(
    profile: str,
    environ: dict[str, str] | None,
    *,
    reason: str,
    runner: Any = None,
) -> HermesModelCatalog:
    """Session 023 §A: degrade to the SELECTED PROFILE's own defaults.

    The profile itself is the primary runtime configuration — an empty
    ``provider``/``model`` on ``ProposalAgentConfig`` makes the real
    ``HermesDriver`` run exactly these defaults.  This fallback reads them
    from the profile's own ``config.yaml`` (a NEVER-hermes_cli child, so
    the unreadable-plugin failure class cannot reach this path either).
    The provider LIST stays empty (honestly unknown here); only the
    profile's provider/model pair is claimed.
    """
    from .hermes_profile_defaults import discover_hermes_profile_defaults

    # Deterministic-test guard: a caller-INJECTED runner (S022's script
    # doubles) must never gain a REAL fallback child — the fallback would
    # otherwise reach past the injected seam.  A double may opt in to
    # serving the defaults leg itself (``handles_profile_defaults``); a
    # production call (runner=None → the real SubprocessRunner) always
    # gets the real fallback child.
    opt_in = bool(getattr(runner, "handles_profile_defaults", False))
    if runner is not None and not isinstance(runner, SubprocessRunner) and not opt_in:
        return HermesModelCatalog(
            profile=profile,
            error=(
                f"{reason}; profile defaults unavailable "
                "(injected runner; fallback child not launched)"
            ),
        )
    defaults = discover_hermes_profile_defaults(
        profile, environ=environ, runner=(None if runner is None else runner)
    )
    if not defaults.available:
        return HermesModelCatalog(
            profile=profile,
            error=f"{reason}; profile defaults unavailable ({defaults.error})",
        )
    return HermesModelCatalog(
        profile=profile,
        providers=[],
        models_by_provider={},
        profile_default_provider=defaults.provider,
        profile_default_model=defaults.model,
        source=HERMES_CATALOG_SOURCE_PROFILE_CONFIG,
        provider_catalog_authoritative=False,
        model_lists_exhaustive=False,
        error="",
    )


def _parse_inventory_payload(
    stdout: str,
    *,
    profile: str,
    environ: dict[str, str] | None = None,
    runner: Any = None,
) -> HermesModelCatalog:
    """Strict single-JSON-object parse of the child's one-line document.

    The bootstrap prints EXACTLY one JSON object on stdout.  Anything else
    (prose, logs, multiple objects, malformed JSON) is "unavailable" — never
    partially parsed, never eval'd.  A payload-level ``error`` (e.g. the
    child's import guard or ``hermes_inventory_failed``) degrades through
    the Session 023 profile-config fallback exactly like a process failure.
    """
    text = str(stdout or "").strip()
    if not text:
        return HermesModelCatalog(profile=profile, error="hermes inventory printed nothing")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return HermesModelCatalog(
            profile=profile, error=f"hermes inventory output was not JSON: {exc}"
        )
    if not isinstance(data, dict):
        return HermesModelCatalog(profile=profile, error="hermes inventory payload shape invalid")
    error = str(data.get("error") or "").strip()
    if error:
        # Session 023 §A: a child-level failure (hermes_import_failed /
        # hermes_inventory_failed, e.g. PermissionError from ONE unreadable
        # optional plugin manifest) must NOT make the profile's own
        # defaults unknowable — degrade through the profile-config
        # fallback, exactly like a process-level failure.
        return _profile_config_fallback(profile, environ, reason=error, runner=runner)

    entries: list[HermesProviderEntry] = []
    models_by_provider: dict[str, list[str]] = {}
    raw_rows = data.get("providers")
    if not isinstance(raw_rows, list):
        return HermesModelCatalog(profile=profile, error="hermes inventory payload has no providers")
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        slug = str(row.get("slug") or "").strip()
        if not slug or slug in models_by_provider:
            continue  # deterministic de-dup on first occurrence
        raw_models = row.get("models")
        models = (
            [str(m) for m in raw_models if str(m or "").strip()]
            if isinstance(raw_models, list)
            else []
        )
        entries.append(
            HermesProviderEntry(
                slug=slug,
                name=str(row.get("name") or "").strip(),
                models=models,
                total_models=len(models),
                source=str(row.get("source") or "").strip(),
                is_current=bool(row.get("is_current")),
                is_user_defined=bool(row.get("is_user_defined")),
            )
        )
        models_by_provider[slug] = models
    if not entries:
        return HermesModelCatalog(
            profile=profile, error="hermes inventory listed no providers"
        )
    return HermesModelCatalog(
        profile=profile,
        providers=entries,
        models_by_provider=models_by_provider,
        profile_default_provider=str(data.get("profile_default_provider") or "").strip(),
        profile_default_model=str(data.get("profile_default_model") or "").strip(),
        source=HERMES_CATALOG_SOURCE_INVENTORY,
        provider_catalog_authoritative=True,
        model_lists_exhaustive=False,
    )
