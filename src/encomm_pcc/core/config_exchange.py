"""Versioned configuration export/import (Session 007).

Move or recover the operator's configuration without re-entering it, with a
strict, fail-closed contract:

Export (``build_export`` → ``write_export``)
* A deterministic, versioned JSON document: format ``encomm-pcc-config``,
  ``version`` 1, application version, workspace display config, the four role
  configurations (engines, models, session policies, Generic CLI safe config,
  Hermes/Codex settings).
* **Zero secrets**: API keys / auth tokens never live in role config, and the
  one place operator-supplied values could hide them (Generic CLI
  ``env_overrides``) is exported with values REDACTED (keys preserved,
  values replaced by ``"<redacted>"``).  External session bindings are
  EXCLUDED by default (the safer option B of the brief) — they are engine
  state, not portable configuration.
* Zero model calls by construction: the module touches only SQLite/domain
  objects.

Import (``read_export`` → ``validate_export`` → ``apply_import``)
* The whole document is validated BEFORE anything is applied.  An invalid
  document changes NOTHING (the controller still owns persistence).
* Strict schema: exact format id, known version (future versions rejected
  with an explicit message), bounded size, unknown keys rejected, every role
  config re-validated through the domain layer (session policy must be a real
  policy; a stored Generic CLI config must satisfy ``GenericCliConfig``).
* ``apply_import`` stages all values first and lets the controller persist
  through its normal transactional path; env-override redactions are dropped
  rather than applied (the operator re-enters them) so a re-export never
  fabricates secret values.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..domain import AgentRole, SessionPolicy

__all__ = [
    "CONFIG_EXPORT_FORMAT",
    "CONFIG_EXPORT_VERSION",
    "MAX_EXPORT_BYTES",
    "REDACTED_VALUE",
    "ConfigExchangeError",
    "build_export",
    "write_export",
    "read_export",
    "validate_export",
    "apply_import",
]

CONFIG_EXPORT_FORMAT = "encomm-pcc-config"
CONFIG_EXPORT_VERSION = 1
#: A 4 MiB bound: a legitimate config is a few KB; anything larger is not a
#: config file and is refused before parsing.
MAX_EXPORT_BYTES = 4 * 1024 * 1024
REDACTED_VALUE = "<redacted>"

_ROLE_VALUES = [role.value for role in AgentRole]

# Keys of AgentRoleConfig.to_dict() that are exported per role.
_ROLE_CONFIG_KEYS = (
    "engine",
    "project_profile",
    "provider",
    "model",
    "session_policy",
    "same_as_orchestrator",
    "extra",
)


class ConfigExchangeError(ValueError):
    """The export document is invalid; nothing has been (or will be) applied."""


# -- redaction -----------------------------------------------------------------
def _redact_generic_cli(config_dict: Any) -> Any:
    """Redact env-override VALUES in a stored Generic CLI config dict."""
    if not isinstance(config_dict, dict):
        return config_dict
    redacted = dict(config_dict)
    env = redacted.get("env_overrides")
    if isinstance(env, dict):
        redacted["env_overrides"] = {
            str(key): REDACTED_VALUE for key in env.keys()
        }
    return redacted


def _strip_transient_extra(extra: Mapping[str, Any]) -> dict[str, Any]:
    """Remove engine-session state from a role ``extra`` mapping.

    Excluded by default (brief §12 option A): external-session bindings are
    real engine ids tied to one machine/engine — not portable configuration.
    The Generic CLI safe config STAYS (it is configuration).
    """
    cleaned: dict[str, Any] = {}
    for key, value in dict(extra or {}).items():
        if key == "external_session_binding":
            continue
        if key == "generic_cli":
            cleaned[key] = _redact_generic_cli(value)
            continue
        cleaned[key] = value
    return cleaned


# -- export ----------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ExportInput:
    """Everything the exporter needs (no controllers, no engines)."""

    application_version: str
    workspace_name: str
    workspace_path: str
    role_configs: Mapping[str, Mapping[str, Any]]  # role value -> config dict


def build_export(data: ExportInput) -> dict[str, Any]:
    """Build the versioned export document (deterministic, secret-free)."""
    roles: dict[str, Any] = {}
    for role_value in _ROLE_VALUES:
        raw = dict(data.role_configs.get(role_value) or {})
        roles[role_value] = {
            "engine": str(raw.get("engine") or ""),
            "project_profile": str(raw.get("project_profile") or ""),
            "provider": str(raw.get("provider") or ""),
            "model": str(raw.get("model") or ""),
            "session_policy": str(raw.get("session_policy") or ""),
            "same_as_orchestrator": bool(raw.get("same_as_orchestrator", False)),
            "extra": _strip_transient_extra(raw.get("extra") or {}),
        }
    return {
        "format": CONFIG_EXPORT_FORMAT,
        "version": CONFIG_EXPORT_VERSION,
        "application_version": str(data.application_version),
        "workspace": {
            "name": str(data.workspace_name or ""),
            "repo_path": str(data.workspace_path or ""),
        },
        "roles": roles,
    }


def write_export(document: Mapping[str, Any], path: str | Path) -> Path:
    """Serialise the export document deterministically and write it."""
    payload = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload + "\n", encoding="utf-8", newline="\n")
    return target


# -- import ----------------------------------------------------------------------
def read_export(path: str | Path) -> dict[str, Any]:
    """Read and parse an export file (bounded, strict JSON, no side effects)."""
    source = Path(path)
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise ConfigExchangeError(f"Cannot read the configuration file: {exc}") from None
    if size > MAX_EXPORT_BYTES:
        raise ConfigExchangeError(
            f"The configuration file is {size} bytes; the supported maximum is "
            f"{MAX_EXPORT_BYTES}. Refusing to import."
        )
    try:
        raw = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigExchangeError(
            f"The configuration file could not be read as UTF-8: {exc}"
        ) from None
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise ConfigExchangeError(f"The file is not valid JSON: {exc}") from None
    if not isinstance(document, dict):
        raise ConfigExchangeError("The file does not contain a JSON object.")
    return document


def _validate_role_config(role_value: str, payload: Any) -> dict[str, Any]:
    """Validate one role entry: structure, policy, engine string, extra shape."""
    if not isinstance(payload, dict):
        raise ConfigExchangeError(f"roles.{role_value} must be an object.")
    unknown = set(map(str, payload.keys())) - set(_ROLE_CONFIG_KEYS)
    if unknown:
        raise ConfigExchangeError(
            f"roles.{role_value} has unknown key(s): {sorted(unknown)}."
        )
    policy = str(payload.get("session_policy") or "")
    try:
        SessionPolicy(policy)
    except ValueError:
        raise ConfigExchangeError(
            f"roles.{role_value}.session_policy {policy!r} is not a valid "
            f"session policy ({', '.join(p.value for p in SessionPolicy)})."
        ) from None
    extra = payload.get("extra") or {}
    if not isinstance(extra, dict):
        raise ConfigExchangeError(f"roles.{role_value}.extra must be an object.")
    validated_extra: dict[str, Any] = {}
    for key, value in extra.items():
        key_text = str(key)
        if key_text == "external_session_binding":
            # Rejected rather than silently dropped: the operator chose to
            # include engine state; we refuse to apply it.
            raise ConfigExchangeError(
                f"roles.{role_value}.extra contains an external_session_binding; "
                "bindings are engine state and cannot be imported."
            )
        if key_text == "generic_cli":
            from ..drivers import GenericCliConfig, GenericCliConfigError

            try:
                validated_extra[key_text] = GenericCliConfig.from_mapping(value).to_dict()
            except GenericCliConfigError as exc:
                raise ConfigExchangeError(
                    f"roles.{role_value}.extra.generic_cli is invalid: {exc}"
                ) from None
            continue
        validated_extra[key_text] = value
    result = dict(payload)
    result["extra"] = validated_extra
    return result


def validate_export(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the WHOLE document and return the normalised payload.

    Raises :class:`ConfigExchangeError` on the first violation.  Nothing here
    mutates application state.
    """
    fmt = str(document.get("format") or "")
    if fmt != CONFIG_EXPORT_FORMAT:
        raise ConfigExchangeError(
            f"Not an {CONFIG_EXPORT_FORMAT} document (found format {fmt!r})."
        )
    version = document.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ConfigExchangeError("The export 'version' must be an integer.")
    if version > CONFIG_EXPORT_VERSION:
        raise ConfigExchangeError(
            f"The export version {version} is newer than this application "
            f"supports ({CONFIG_EXPORT_VERSION}). Update the Control Center "
            "before importing this file."
        )
    if version < CONFIG_EXPORT_VERSION:
        raise ConfigExchangeError(
            f"The export version {version} is no longer accepted "
            f"(this application imports version {CONFIG_EXPORT_VERSION})."
        )
    unknown_top = set(map(str, document.keys())) - {
        "format",
        "version",
        "application_version",
        "workspace",
        "roles",
    }
    if unknown_top:
        raise ConfigExchangeError(f"Unknown top-level key(s): {sorted(unknown_top)}.")

    workspace = document.get("workspace") or {}
    if not isinstance(workspace, dict):
        raise ConfigExchangeError("'workspace' must be an object.")
    unknown_ws = set(map(str, workspace.keys())) - {"name", "repo_path"}
    if unknown_ws:
        raise ConfigExchangeError(
            f"'workspace' has unknown key(s): {sorted(unknown_ws)}."
        )

    roles = document.get("roles")
    if not isinstance(roles, dict) or not roles:
        raise ConfigExchangeError("'roles' must be a non-empty object.")
    unknown_roles = set(map(str, roles.keys())) - set(_ROLE_VALUES)
    if unknown_roles:
        raise ConfigExchangeError(
            f"Unknown role(s): {sorted(unknown_roles)}; expected "
            f"{_ROLE_VALUES}."
        )
    normalised_roles = {
        role_value: _validate_role_config(role_value, roles[role_value])
        for role_value in _ROLE_VALUES
        if role_value in roles
    }
    return {
        "workspace": {
            "name": str(workspace.get("name") or ""),
            "repo_path": str(workspace.get("repo_path") or ""),
        },
        "roles": normalised_roles,
    }


def apply_import(controller, payload: Mapping[str, Any]) -> dict[str, Any]:  # noqa: ANN001 - PipelineController (import cycle avoided)
    """Apply a VALIDATED payload through the controller (transactional persist).

    Behaviour:

    * Role values are written through ``set_role_config`` (the controller's
      whitelist — nothing bypasses validation) and ``extra`` is merged
      field-by-field on top of the existing extra.
    * REDACTED env-override values inside a Generic CLI config are DROPPED,
      not applied: an import must never fabricate secret material.  The rest
      of that Generic CLI config is applied.
    * The workspace path is applied ONLY when it exists on disk or is empty;
      a non-existent exported path would break every dispatch preflight.
    * Zero model calls, zero engine contact.
    """
    applied_roles: list[str] = []
    workspace = payload.get("workspace") or {}
    name = str(workspace.get("name") or "")
    repo_path = str(workspace.get("repo_path") or "")
    if repo_path:
        from pathlib import Path as _Path

        if not _Path(repo_path).expanduser().is_dir():
            repo_path = ""  # never import a broken workspace path

    for role_value, role_payload in (payload.get("roles") or {}).items():
        role = AgentRole(str(role_value))
        extra = dict(role_payload.get("extra") or {})
        generic_cli = extra.get("generic_cli")
        if isinstance(generic_cli, dict):
            cleaned = dict(generic_cli)
            env = cleaned.get("env_overrides")
            if isinstance(env, dict):
                # Drop redacted values instead of applying them.
                cleaned["env_overrides"] = {
                    key: value
                    for key, value in env.items()
                    if value != REDACTED_VALUE
                }
            controller.set_generic_cli_config(role, cleaned)
            extra.pop("generic_cli", None)
        controller.set_role_config(
            role,
            engine=str(role_payload.get("engine") or ""),
            project_profile=str(role_payload.get("project_profile") or ""),
            provider=str(role_payload.get("provider") or ""),
            model=str(role_payload.get("model") or ""),
            session_policy=SessionPolicy(str(role_payload.get("session_policy"))),
            same_as_orchestrator=bool(role_payload.get("same_as_orchestrator", False)),
        )
        if extra:
            config = controller.role_config(role)
            merged = dict(config.extra or {})
            merged.update(extra)
            config.extra = merged
            controller.persist()
        applied_roles.append(str(role_value))

    if name or repo_path:
        current = controller.state.workspace
        controller.set_workspace(name or current.name, repo_path or current.repo_path)

    return {"applied_roles": applied_roles, "workspace_applied": bool(name or repo_path)}
