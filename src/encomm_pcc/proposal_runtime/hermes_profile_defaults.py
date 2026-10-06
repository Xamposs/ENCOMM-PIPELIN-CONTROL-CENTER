"""Profile-defaults fallback for Proposal Mode role displays (Session 023).

Requirement (brief §A/§D/§E): when the authoritative provider/model inventory
is unavailable (e.g. ONE unreadable optional plugin makes
``hermes_cli.inventory.load_picker_context()`` raise ``PermissionError``), the
Simple UI must STILL show the selected profile's real configured defaults —
read from the profile's OWN ``config.yaml`` (``model.provider`` /
``model.default``).  The profile itself is the primary runtime configuration:
an empty ``provider``/``model`` on ``ProposalAgentConfig`` makes the real
``HermesDriver`` run exactly these defaults (``build_chat_argv`` emits
``-p <profile>`` and NO ``--provider``/``-m`` override — verified contract).

Mechanism mirrors ``hermes_model_catalog`` (Session 022): a shipped inline
bootstrap runs under the Hermes installation's OWN venv python (which carries
PyYAML; the packaged PCC deliberately does not), scoped with ``HERMES_HOME``
at the profile's directory (``HERMES_PROFILE`` alone does not scope config
reads — Session 022, verified live).  Unlike the inventory bootstrap this one
NEVER imports ``hermes_cli``: it parses only the profile's ``config.yaml``
with ``yaml.safe_load`` and prints ONE strict JSON document — no plugin scan,
no credentials, no writes, no network, zero model calls.

Honesty contract: the result is a READ of the profile's own configuration —
never a fabricated catalogue.  Failures degrade to ``source="unavailable"``
with a short error (never an exception, never invented names).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..drivers.process import ProcessSpec, SubprocessRunner
from .hermes_model_catalog import _hermes_install_dir, _hermes_venv_python, _profile_home_dir

__all__ = [
    "HERMES_DEFAULTS_SOURCE_CONFIG",
    "HERMES_DEFAULTS_SOURCE_NONE",
    "HermesProfileDefaults",
    "discover_hermes_profile_defaults",
]

#: Read from the selected profile's own config.yaml (honest fallback).
HERMES_DEFAULTS_SOURCE_CONFIG = "profile-config"
HERMES_DEFAULTS_SOURCE_NONE = "unavailable"

#: Bounded child budget: parsing one local YAML file is fast; the cap only
#: exists so a pathological host state can never hang the UI refresh.
_CHILD_TIMEOUT_S = 30.0

#: Never imports hermes_cli (the Session 022 bug class: an unreadable
#: optional plugin must not reach the defaults path at all).  Reads ONLY
#: the profile home's config.yaml and prints ONE JSON object.
_BOOTSTRAP_SCRIPT = r'''
import json, os, sys

home = os.environ.get("HERMES_HOME", "")
cfg_path = os.path.join(home, "config.yaml") if home else ""
out = {"provider": "", "model": "", "error": ""}

if not cfg_path:
    out["error"] = "profile home not resolved"
elif not os.path.isfile(cfg_path):
    out["error"] = "profile config.yaml not found"
else:
    try:
        import yaml
        with open(cfg_path, "r", encoding="utf-8-sig") as fh:
            data = yaml.safe_load(fh)
    except Exception as exc:
        out["error"] = "profile config unreadable: %s: %s" % (
            type(exc).__name__, exc)
    else:
        model = {}
        if isinstance(data, dict):
            raw = data.get("model")
            if isinstance(raw, dict):
                model = raw
            elif isinstance(raw, str) and raw.strip():
                # Older configs carry a bare default model string.
                model = {"default": raw}
        out["provider"] = str(model.get("provider", "") or "").strip()
        out["model"] = str(
            model.get("default", model.get("name", "")) or "").strip()
        if not out["provider"] and not out["model"]:
            out["error"] = "profile config declares no model defaults"

print(json.dumps(out))
'''


@dataclass(slots=True)
class HermesProfileDefaults:
    """The selected Hermes profile's OWN configured defaults (no secrets).

    ``available`` means the values were really read from the profile's
    ``config.yaml``; any failure degrades to ``source="unavailable"`` with a
    short ``error`` — never an exception, never invented names.
    """

    profile: str = ""
    provider: str = ""
    model: str = ""
    source: str = HERMES_DEFAULTS_SOURCE_NONE
    error: str = ""

    @property
    def available(self) -> bool:
        return self.source == HERMES_DEFAULTS_SOURCE_CONFIG


def discover_hermes_profile_defaults(
    profile: str,
    *,
    environ: dict[str, str] | None = None,
    runner: Any = None,
) -> HermesProfileDefaults:
    """The selected profile's real provider/model defaults — zero model calls.

    Runs the shipped bootstrap under the Hermes installation's own venv
    python with ``HERMES_HOME`` scoped to the profile directory.  The child
    reads ONLY ``<home>/config.yaml`` (PyYAML from the Hermes venv) and
    never imports ``hermes_cli``, so an unreadable optional plugin can never
    affect this path.  Any failure degrades honestly.
    """
    name = str(profile or "").strip()
    if not name:
        return HermesProfileDefaults(error="no profile selected")
    install_dir = _hermes_install_dir(environ)
    if not install_dir:
        return HermesProfileDefaults(
            profile=name, error="hermes installation directory not found"
        )
    python = _hermes_venv_python(install_dir)
    if not python:
        return HermesProfileDefaults(
            profile=name, error="hermes venv python not found"
        )
    home = _profile_home_dir(name, environ)
    if not home:
        return HermesProfileDefaults(
            profile=name, error=f"profile home not found for {name!r}"
        )

    import os

    child_env = {
        **(os.environ if environ is None else dict(environ)),
        "HERMES_AGENT_DIR": install_dir,
        "HERMES_HOME": home,
    }
    child_env["HERMES_PROFILE"] = name

    # A stored bootstrap path (tests) or the shipped inline script — the
    # same seam the catalog discovery uses.
    script_path = (environ or {}).get("HERMES_CATALOG_BOOTSTRAP", "").strip()
    if script_path:
        argv = [python, script_path]
    else:
        argv = [python, "-I", "-c", _BOOTSTRAP_SCRIPT]

    spec = ProcessSpec(
        argv=argv,
        env=child_env,
        timeout_s=_CHILD_TIMEOUT_S,
        stdin_text="{}",
    )
    real_runner = runner if runner is not None else SubprocessRunner()
    try:
        result = real_runner.run(spec)
    except Exception as exc:  # noqa: BLE001 - fallback must never raise
        return HermesProfileDefaults(
            profile=name,
            error=f"profile defaults runner failed: {type(exc).__name__}: {exc}",
        )
    if result.timed_out:
        return HermesProfileDefaults(profile=name, error="profile defaults read timed out")
    if result.exit_code != 0:
        return HermesProfileDefaults(
            profile=name,
            error=f"profile defaults reader exited {result.exit_code}",
        )
    return _parse_defaults_payload(result.stdout, profile=name)


def _parse_defaults_payload(stdout: str, *, profile: str) -> HermesProfileDefaults:
    """Strict single-JSON-object parse of the child's one-line document.

    Anything else (prose, logs, multiple objects, malformed JSON) is
    "unavailable" — never partially parsed, never eval'd.
    """
    text = str(stdout or "").strip()
    if not text:
        return HermesProfileDefaults(profile=profile, error="profile defaults printed nothing")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return HermesProfileDefaults(
            profile=profile, error=f"profile defaults output was not JSON: {exc}"
        )
    if not isinstance(data, dict):
        return HermesProfileDefaults(
            profile=profile, error="profile defaults payload shape invalid"
        )
    error = str(data.get("error") or "").strip()
    provider = str(data.get("provider") or "").strip()
    model = str(data.get("model") or "").strip()
    if error:
        return HermesProfileDefaults(
            profile=profile, provider=provider, model=model, error=error
        )
    if not provider and not model:
        return HermesProfileDefaults(
            profile=profile, error="profile config declares no model defaults"
        )
    return HermesProfileDefaults(
        profile=profile,
        provider=provider,
        model=model,
        source=HERMES_DEFAULTS_SOURCE_CONFIG,
    )
