"""The Generic CLI configuration contract — pure data + pure functions.

Session 007 turned ``GenericCliDriver`` from a refusing placeholder into a real
driver for *compatible* command-line agents.  Everything operator-configurable
about it lives here as structured, validated values — never as a shell command
string:

* ``executable`` — a bare name (resolved on PATH) or an absolute path.
* ``args`` — literal argv tokens.  Shell metacharacters stay **literal**;
  they are never interpreted by anything.
* ``prompt_transport`` — ``stdin`` (preferred) or ``temporary_file``.
* ``result_mode`` — ``stdout_text`` (default) or bounded ``json`` / ``jsonl``
  with one configured result field.
* ``model_args`` — optional argv prefix for model selection (``{model}``
  placeholder allowed).
* ``timeout_s`` — bounded wall-clock budget for one prompt.
* ``env_overrides`` — a small, bounded, key-validated overlay applied on top
  of the standard filtered child environment (D-015 discipline).

Security contract (see the Session 007 report / ADR):

* **No shell** — the only consumer of this config builds a ``ProcessSpec``
  (argv list, ``shell=False``, explicit cwd).
* **Placeholders fail closed** — ``{prompt_file}``, ``{workspace}`` and
  ``{model}`` are the only substitutions that exist; unknown ``{...}`` text in
  any token is a configuration error, never a silent pass-through.  No eval,
  no template languages, no string-expression machinery.
* **Secrets** — values here live in role config ``extra`` (SQLite); export
  redacts environment override VALUES (``core/config_exchange.py``).

This module is Qt-free and engine-free: it hardwires no OpenCode / Claude /
Kimi / Ollama / Codex / Hermes names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .base import DriverError

__all__ = [
    "GenericCliConfig",
    "GenericCliConfigError",
    "PROMPT_TRANSPORT_STDIN",
    "PROMPT_TRANSPORT_TEMP_FILE",
    "PROMPT_TRANSPORTS",
    "RESULT_MODE_JSON",
    "RESULT_MODE_JSONL",
    "RESULT_MODE_STDOUT_TEXT",
    "RESULT_MODES",
    "PLACEHOLDER_MODEL",
    "PLACEHOLDER_PROMPT_FILE",
    "PLACEHOLDER_WORKSPACE",
    "KNOWN_PLACEHOLDERS",
    "DEFAULT_TIMEOUT_S",
    "MIN_TIMEOUT_S",
    "MAX_TIMEOUT_S",
    "MAX_ARGV_TOKENS",
    "MAX_TOKEN_CHARS",
    "MAX_ENV_OVERRIDES",
    "MAX_ENV_KEY_CHARS",
    "MAX_ENV_VALUE_CHARS",
    "MAX_JSON_RESULT_CHARS",
    "MAX_JSONL_RECORDS",
    "CONFIG_EXTRA_KEY",
    "build_argv",
    "child_environment",
    "extract_result_text",
]


class GenericCliConfigError(DriverError):
    """The Generic CLI configuration is invalid or cannot build an argv.

    Subclasses :class:`~encomm_pcc.drivers.base.DriverError` so every existing
    executor dispatch path treats it exactly like any other refused driver
    configuration: the run is blocked/failed loudly, never guessed through.
    """


# -- prompt transports ---------------------------------------------------------
PROMPT_TRANSPORT_STDIN = "stdin"
PROMPT_TRANSPORT_TEMP_FILE = "temporary_file"
PROMPT_TRANSPORTS: tuple[str, ...] = (PROMPT_TRANSPORT_STDIN, PROMPT_TRANSPORT_TEMP_FILE)

# -- result modes ----------------------------------------------------------------
RESULT_MODE_STDOUT_TEXT = "stdout_text"
RESULT_MODE_JSON = "json"
RESULT_MODE_JSONL = "jsonl"
RESULT_MODES: tuple[str, ...] = (RESULT_MODE_STDOUT_TEXT, RESULT_MODE_JSON, RESULT_MODE_JSONL)

# -- placeholders -----------------------------------------------------------------
#: The complete whitelist.  Anything else in braces is rejected at parse time.
PLACEHOLDER_PROMPT_FILE = "{prompt_file}"
PLACEHOLDER_WORKSPACE = "{workspace}"
PLACEHOLDER_MODEL = "{model}"
KNOWN_PLACEHOLDERS: frozenset[str] = frozenset(
    {PLACEHOLDER_PROMPT_FILE, PLACEHOLDER_WORKSPACE, PLACEHOLDER_MODEL}
)

_PLACEHOLDER_RE = re.compile(r"\{[^{}]*\}")
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: A bare executable name or an absolute path.  Relative paths with separators
#: are refused (ambiguous — relative to WHICH cwd?) — a bare name is resolved
#: on PATH at launch time instead.
_EXECUTABLE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# -- bounded configuration (fail closed on excess) ----------------------------------
DEFAULT_TIMEOUT_S = 900.0
MIN_TIMEOUT_S = 1.0
MAX_TIMEOUT_S = 7200.0
MAX_ARGV_TOKENS = 64
MAX_TOKEN_CHARS = 4096
MAX_ENV_OVERRIDES = 16
MAX_ENV_KEY_CHARS = 128
MAX_ENV_VALUE_CHARS = 4096
#: stdout larger than this cannot be parsed as a structured result (bounded).
MAX_JSON_RESULT_CHARS = 2_000_000
MAX_JSONL_RECORDS = 4000

#: Key under ``AgentRoleConfig.extra`` where the safe config dict is stored.
CONFIG_EXTRA_KEY = "generic_cli"


def _config_error(message: str) -> GenericCliConfigError:
    return GenericCliConfigError(message)


def _validate_executable(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise _config_error(
            "Generic CLI executable is empty. Enter the command name exactly as "
            "it would appear on PATH (for example 'opencode'), or an absolute "
            "path to the executable."
        )
    if len(text) > MAX_TOKEN_CHARS:
        raise _config_error(
            f"Generic CLI executable is longer than {MAX_TOKEN_CHARS} characters."
        )
    if "\x00" in text or "\n" in text or "\r" in text:
        raise _config_error("Generic CLI executable contains control characters.")
    if any(ch in text for ch in "&|;<>()!\"'`^%,"):
        raise _config_error(
            "Generic CLI executable contains shell metacharacters "
            f"({text!r}). Enter the program name alone — this configuration is "
            "not a shell command and must never be one."
        )
    if _EXECUTABLE_NAME_RE.match(text):
        return text
    # Not a bare name: only an absolute path to a file is acceptable.
    from pathlib import Path

    candidate = Path(text)
    if not candidate.is_absolute():
        raise _config_error(
            f"Generic CLI executable {text!r} is neither a bare command name nor "
            "an absolute path. Bare names are resolved on PATH; relative paths "
            "are refused as ambiguous."
        )
    return text


def _validate_token(value: Any, *, kind: str) -> str:
    text = str(value if value is not None else "")
    if len(text) > MAX_TOKEN_CHARS:
        raise _config_error(
            f"Generic CLI {kind} token is longer than {MAX_TOKEN_CHARS} characters."
        )
    if "\x00" in text or "\n" in text or "\r" in text:
        raise _config_error(
            f"Generic CLI {kind} token contains control characters (argv tokens "
            "are literal; multi-line values are not valid here)."
        )
    return text


def _validate_placeholder_usage(tokens: tuple[str, ...], *, kind: str) -> None:
    """Whole-token placeholders must be known and used at most once.

    Placeholder semantics are **exact-token**: a token is a placeholder only
    when the token *is* ``{prompt_file}`` / ``{workspace}`` / ``{model}``.
    Braces inside a larger token are literal text (CLI tools legitimately
    receive JSON/Python-ish arguments containing braces).  A whole token that
    merely *looks* like a placeholder but names nothing known fails closed —
    that is a typo, not data.
    """
    seen: dict[str, str] = {}
    for token in tokens:
        if token in KNOWN_PLACEHOLDERS:
            if token in seen:
                raise _config_error(
                    f"Placeholder {token} appears more than once across the "
                    f"Generic CLI {kind} tokens ({seen[token]!r} and {token!r}). "
                    "Each placeholder may be used at most once."
                )
            seen[token] = token
            continue
        if _PLACEHOLDER_RE.fullmatch(token):
            raise _config_error(
                f"Unknown placeholder {token} in Generic CLI {kind} token. "
                "Only {prompt_file}, {workspace} and {model} exist; anything "
                "else fails closed."
            )


def _validate_env_overrides(data: Any) -> dict[str, str]:
    if data in (None, {}):
        return {}
    if not isinstance(data, Mapping):
        raise _config_error(
            "Generic CLI env_overrides must be a mapping of environment "
            "variable names to literal string values."
        )
    items = dict(data)
    if len(items) > MAX_ENV_OVERRIDES:
        raise _config_error(
            f"Generic CLI env_overrides exceeds the bound of {MAX_ENV_OVERRIDES} entries."
        )
    overrides: dict[str, str] = {}
    for key, value in items.items():
        key_text = str(key)
        if not key_text or len(key_text) > MAX_ENV_KEY_CHARS:
            raise _config_error(
                f"Generic CLI env_overrides key {key_text[:32]!r} is empty or "
                f"longer than {MAX_ENV_KEY_CHARS} characters."
            )
        if not _ENV_KEY_RE.match(key_text):
            raise _config_error(
                f"Generic CLI env_overrides key {key_text!r} is not a valid "
                "environment variable name."
            )
        value_text = _validate_token(value, kind="env value")
        if len(value_text) > MAX_ENV_VALUE_CHARS:
            raise _config_error(
                f"Generic CLI env_overrides value for {key_text!r} is longer "
                f"than {MAX_ENV_VALUE_CHARS} characters."
            )
        overrides[key_text] = value_text
    return overrides


@dataclass(frozen=True, slots=True)
class GenericCliConfig:
    """Validated, driver-neutral configuration for one Generic CLI engine."""

    executable: str
    args: tuple[str, ...] = ()
    prompt_transport: str = PROMPT_TRANSPORT_STDIN
    result_mode: str = RESULT_MODE_STDOUT_TEXT
    #: Field name extracted from the structured result (json/jsonl only).
    result_field: str = ""
    #: Argv prefix selecting a model when the request carries one; may contain
    #: the ``{model}`` placeholder (e.g. ``("--model", "{model}")``).
    model_args: tuple[str, ...] = ()
    timeout_s: float = DEFAULT_TIMEOUT_S
    env_overrides: Mapping[str, str] = field(default_factory=dict)

    # -- validation ------------------------------------------------------
    def __post_init__(self) -> None:
        object.__setattr__(self, "executable", _validate_executable(self.executable))

        if isinstance(self.args, str):
            raise _config_error(
                "Generic CLI args must be a list of separate argv tokens, not a "
                "single string. This configuration never receives a shell command."
            )
        args = tuple(_validate_token(a, kind="argument") for a in (self.args or ()))
        if len(args) > MAX_ARGV_TOKENS:
            raise _config_error(
                f"Generic CLI args exceed the bound of {MAX_ARGV_TOKENS} tokens."
            )
        object.__setattr__(self, "args", args)

        if isinstance(self.model_args, str):
            raise _config_error(
                "Generic CLI model_args must be a list of separate argv tokens, "
                "not a single string."
            )
        model_args = tuple(
            _validate_token(a, kind="model argument") for a in (self.model_args or ())
        )
        if len(model_args) > 8:
            raise _config_error("Generic CLI model_args may contain at most 8 tokens.")
        object.__setattr__(self, "model_args", model_args)

        if self.prompt_transport not in PROMPT_TRANSPORTS:
            raise _config_error(
                f"Generic CLI prompt_transport {self.prompt_transport!r} is not "
                "supported; allowed: " + ", ".join(PROMPT_TRANSPORTS) + "."
            )
        if self.result_mode not in RESULT_MODES:
            raise _config_error(
                f"Generic CLI result_mode {self.result_mode!r} is not supported; "
                "allowed: " + ", ".join(RESULT_MODES) + "."
            )
        if self.result_mode in (RESULT_MODE_JSON, RESULT_MODE_JSONL):
            field_name = str(self.result_field or "").strip()
            if not field_name:
                raise _config_error(
                    f"Generic CLI result_mode {self.result_mode!r} requires a "
                    "result_field (the JSON key holding the agent's answer)."
                )
            if len(field_name) > 256 or "." in field_name and len(field_name) > 256:
                raise _config_error("Generic CLI result_field is longer than 256 characters.")
            object.__setattr__(self, "result_field", field_name)
        else:
            object.__setattr__(self, "result_field", "")

        try:
            timeout = float(self.timeout_s)
        except (TypeError, ValueError):
            raise _config_error(
                f"Generic CLI timeout {self.timeout_s!r} is not a number of seconds."
            ) from None
        if not (MIN_TIMEOUT_S <= timeout <= MAX_TIMEOUT_S):
            raise _config_error(
                f"Generic CLI timeout must be between {MIN_TIMEOUT_S:g} and "
                f"{MAX_TIMEOUT_S:g} seconds (got {timeout:g})."
            )
        object.__setattr__(self, "timeout_s", timeout)

        object.__setattr__(
            self, "env_overrides", _validate_env_overrides(self.env_overrides)
        )

        _validate_placeholder_usage(args, kind="argument")
        _validate_placeholder_usage(model_args, kind="model argument")
        if PLACEHOLDER_PROMPT_FILE in args:
            if self.prompt_transport != PROMPT_TRANSPORT_TEMP_FILE:
                raise _config_error(
                    f"{PLACEHOLDER_PROMPT_FILE} may only be used with prompt "
                    f"transport '{PROMPT_TRANSPORT_TEMP_FILE}'; configured "
                    f"transport is {self.prompt_transport!r}."
                )
        if self.prompt_transport == PROMPT_TRANSPORT_TEMP_FILE and (
            PLACEHOLDER_PROMPT_FILE not in args
        ):
            raise _config_error(
                f"prompt_transport '{PROMPT_TRANSPORT_TEMP_FILE}' requires the "
                f"{PLACEHOLDER_PROMPT_FILE} placeholder in args, otherwise the "
                "CLI would never receive the prompt."
            )

    # -- serialisation -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """The safe, structured representation stored in role config ``extra``."""
        return {
            "executable": self.executable,
            "args": list(self.args),
            "prompt_transport": self.prompt_transport,
            "result_mode": self.result_mode,
            "result_field": self.result_field,
            "model_args": list(self.model_args),
            "timeout_s": self.timeout_s,
            "env_overrides": dict(self.env_overrides),
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "GenericCliConfig":
        """Strictly parse the operator configuration.

        Unknown keys are rejected (a typo like ``executble`` must fail closed,
        not silently drop a field).  Every bound is enforced here so callers
        can hand the config to :func:`build_argv` without re-checking.
        """
        if data is None:
            raise _config_error(
                "No Generic CLI configuration is stored for this role. Open the "
                "role's Generic CLI settings and configure the executable."
            )
        if not isinstance(data, Mapping):
            raise _config_error("Generic CLI configuration must be a mapping.")
        known = {
            "executable",
            "args",
            "prompt_transport",
            "result_mode",
            "result_field",
            "model_args",
            "timeout_s",
            "env_overrides",
        }
        unknown = set(map(str, data.keys())) - known
        if unknown:
            raise _config_error(
                f"Unknown Generic CLI configuration key(s): {sorted(unknown)}."
            )
        # Preserve a raw string for `args`/`model_args` so __post_init__ can
        # reject it with the "not a single string" guidance (tuple(str) would
        # silently explode it into characters).
        raw_args = data.get("args")
        raw_model_args = data.get("model_args")
        config = cls(
            executable=str(data.get("executable") or ""),
            args=raw_args if isinstance(raw_args, str) else tuple(raw_args or ()),
            prompt_transport=str(data.get("prompt_transport") or PROMPT_TRANSPORT_STDIN),
            result_mode=str(data.get("result_mode") or RESULT_MODE_STDOUT_TEXT),
            result_field=str(data.get("result_field") or ""),
            model_args=(
                raw_model_args
                if isinstance(raw_model_args, str)
                else tuple(raw_model_args or ())
            ),
            timeout_s=data.get("timeout_s", DEFAULT_TIMEOUT_S),
            env_overrides=dict(data.get("env_overrides") or {}),
        )
        return config


def build_argv(
    config: GenericCliConfig,
    *,
    workspace: str,
    model: str = "",
    prompt_file: str = "",
) -> list[str]:
    """Build the literal argv for one Generic CLI prompt.

    Pure token substitution — the three whitelisted placeholders are replaced
    by literal values; no quoting, escaping, shell or eval is involved.  The
    config was fully validated at construction, so an impossible request
    (e.g. ``{workspace}`` with no workspace configured, or ``{prompt_file}``
    in stdin mode) still fails closed here rather than producing a broken argv.
    """
    extra_tokens: list[str] = []
    if model:
        for token in config.model_args:
            extra_tokens.append(model if token == PLACEHOLDER_MODEL else token)
    elif PLACEHOLDER_MODEL in config.model_args:
        raise _config_error(
            "The role's model_args reference {model} but no model is configured "
            "for this run."
        )

    substitutions = {
        PLACEHOLDER_WORKSPACE: workspace,
        PLACEHOLDER_PROMPT_FILE: prompt_file,
    }
    argv: list[str] = [config.executable]
    for token in config.args:
        # Exact-token placeholders only (see _validate_placeholder_usage):
        # braces inside a larger token are literal text, never substituted.
        if token in substitutions:
            value = substitutions[token]
            if not value:
                raise _config_error(
                    f"Placeholder {token} is configured but no corresponding "
                    f"value is available ({token}: {value!r})."
                )
            argv.append(value)
        else:
            argv.append(token)
    argv.extend(extra_tokens)
    return argv


def child_environment(
    env_overrides: Mapping[str, str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Filtered child environment (D-015 discipline) plus bounded overrides.

    The supervisor's own scope (``HERMES_*``, ``ENCOMM_PCC_*``) and interpreter
    path (``PYTHONPATH`` / ``PYTHONHOME``) are dropped exactly like the Hermes
    and Codex adapters; the operator's validated ``env_overrides`` are applied
    on top.  The resulting environment is never logged.
    """
    import os

    source = os.environ if environ is None else environ
    drop_exact = {"PYTHONPATH", "PYTHONHOME"}
    drop_prefixes = ("HERMES_", "ENCOMM_PCC_")
    filtered: dict[str, str] = {}
    for key, value in source.items():
        if key in drop_exact:
            continue
        if any(key.startswith(prefix) for prefix in drop_prefixes):
            continue
        filtered[str(key)] = str(value)
    for key, value in dict(env_overrides or {}).items():
        filtered[key] = value
    return filtered


def extract_result_text(
    *,
    result_mode: str,
    stdout: str,
    result_field: str,
) -> str:
    """Extract the agent's answer from the child's stdout.

    Deterministic and bounded — this is deliberately NOT a universal parser:

    * ``stdout_text``: the stdout verbatim (caller checks non-empty).
    * ``json``: the whole stdout must be one JSON object; ``result_field``
      must exist and hold a string.  Anything else fails closed.
    * ``jsonl``: each line is a JSON object; the LAST non-empty string found
      at ``result_field`` wins (final-message semantics).  Malformed lines are
      tolerated and counted by the caller via the metadata, but a stream with
      no parseable record at all fails.

    Raises :class:`GenericCliConfigError` on any structural violation so the
    driver can report an honest failure.
    """
    text = str(stdout or "")
    if result_mode == RESULT_MODE_STDOUT_TEXT:
        return text
    if len(text) > MAX_JSON_RESULT_CHARS:
        raise _config_error(
            f"Structured result exceeds the {MAX_JSON_RESULT_CHARS} character "
            f"bound ({len(text)} characters); refusing to parse."
        )
    if result_mode == RESULT_MODE_JSON:
        try:
            payload = _loads(text)
        except ValueError as exc:
            raise _config_error(
                f"result_mode 'json' requires the whole stdout to be one JSON "
                f"object; parsing failed ({exc})."
            ) from None
        if not isinstance(payload, dict):
            raise _config_error(
                "result_mode 'json' requires a JSON object at the top level; "
                f"got {type(payload).__name__}."
            )
        return _string_field(payload, result_field)
    if result_mode == RESULT_MODE_JSONL:
        last_value: str | None = None
        parsed_any = False
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = _loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            parsed_any = True
            try:
                value = _string_field(record, result_field)
            except GenericCliConfigError:
                continue
            if value:
                last_value = value
        if not parsed_any:
            raise _config_error(
                "result_mode 'jsonl' found no parseable JSON object line on stdout."
            )
        if last_value is None:
            raise _config_error(
                f"result_mode 'jsonl' found no record carrying the field "
                f"{result_field!r}."
            )
        return last_value
    raise _config_error(f"Unsupported result_mode {result_mode!r}.")


def _loads(text: str) -> Any:  # noqa: ANN401 - JSON payloads are untyped
    import json

    return json.loads(text)


def _string_field(payload: dict[str, Any], field_name: str) -> str:
    """Extract a required string field (simple dotted paths, no JSONPath)."""
    value: Any = payload
    for part in field_name.split("."):
        if not isinstance(value, dict) or part not in value:
            raise _config_error(
                f"The configured result_field {field_name!r} was not found in "
                "the structured result."
            )
        value = value[part]
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or isinstance(value, (int, float)):
        # Deterministic scalar rendering; never a silently-mangled object.
        return str(value)
    raise _config_error(
        f"The configured result_field {field_name!r} did not hold a string or "
        f"scalar (got {type(value).__name__}); refusing to guess."
    )
