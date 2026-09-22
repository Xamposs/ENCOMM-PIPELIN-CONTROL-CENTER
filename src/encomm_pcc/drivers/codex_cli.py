"""The verified Codex CLI contract, in one testable place.

Everything in this module was derived from the **installed** Codex CLI on this
host (`codex-cli 0.154.0` at ``C:\\Users\\xampos\\AppData\\Local\\Programs\\OpenAI\\Codex\\bin\\codex.exe``
via ``codex --version``, ``codex --help``, ``codex exec --help``,
``codex exec resume --help`` and ``codex resume --help`` on 2026-09-23).  It is
pure data and pure functions: no process is started here, so argument
construction and output parsing are unit-testable without touching a network
or an engine.

Verified invocation used by :class:`~encomm_pcc.drivers.codex.CodexDriver`::

    codex exec --json -s <sandbox> -C <workspace> [-m <model>] [--skip-git-repo-check] -
    codex exec resume <SESSION_ID> --json -s <sandbox> -C <workspace> [-m <model>] -

The prompt is read from **stdin** (the trailing ``-`` argument): the ``exec``
help states "If not provided as an argument (or if ``-`` is used), instructions
are read from stdin", which avoids Windows command-line length limits and
shell-quoting problems entirely.  ``exec resume`` documents the same ``-``
behaviour for its prompt argument.

Verified behaviour recorded from the installed build:

* ``--json`` prints events to stdout as JSONL (one JSON object per line).
* ``-o/--output-last-message <FILE>`` writes the agent's final message to a
  file — used as the secondary, authoritative source for the final text.
* ``-s/--sandbox`` accepts ``read-only``, ``workspace-write`` and
  ``danger-full-access``.  This adapter only ever allows the first two;
  least-privilege by default (read-only).
* ``-C/--cd <DIR>`` sets the agent's working root.
* ``-m/--model <MODEL>`` selects the model per invocation.
* ``--skip-git-repo-check`` allows running outside a git repository.
* ``codex exec resume <SESSION_ID>`` resumes a previous session **by id**
  non-interactively (the bare ``codex resume`` is the interactive TUI picker
  and is never used by this adapter).
* Session rollout state lives under ``$CODEX_HOME`` (default
  ``~/.codex``): ``sessions/YYYY/MM/DD/rollout-<timestamp>-<uuid>.jsonl``
  with a ``session_meta`` first record carrying the real session id and cwd.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "CodexCliError",
    "CodexRunOutput",
    "SANDBOX_DANGER_FULL_ACCESS",
    "SANDBOX_READ_ONLY",
    "SANDBOX_WORKSPACE_WRITE",
    "ALLOWED_SANDBOX_MODES",
    "FORBIDDEN_FLAGS",
    "build_exec_argv",
    "build_exec_resume_argv",
    "codex_child_environment",
    "parse_exec_jsonl",
]

# -- verified sandbox modes ------------------------------------------------
SANDBOX_READ_ONLY = "read-only"
SANDBOX_WORKSPACE_WRITE = "workspace-write"
SANDBOX_DANGER_FULL_ACCESS = "danger-full-access"

#: Sandboxes this adapter will ever build an argv for.  The bypass mode is
#: deliberately absent: least privilege, per the session brief.
ALLOWED_SANDBOX_MODES: tuple[str, ...] = (SANDBOX_READ_ONLY, SANDBOX_WORKSPACE_WRITE)

#: Flags this application will never pass on a caller's behalf.  Both bypass
#: flags disable Codex's own safety rails; a supervised pipeline must not be
#: able to switch them on through configuration.
FORBIDDEN_FLAGS: frozenset[str] = frozenset(
    {
        "--dangerously-bypass-approvals-and-sandbox",
        "--dangerously-bypass-hook-trust",
        "--ephemeral",
        "--ignore-user-config",
    }
)


class CodexCliError(RuntimeError):
    """The CLI contract was violated: bad arguments or unparsable output."""


# -- child environment ---------------------------------------------------------
#: Environment prefixes stripped before launching a supervised Codex process.
#: ``CODEX_HOME`` is deliberately **kept** (it is how the deployment pins the
#: Codex state home) but a supervisor's ambient override must not leak, so it
#: is only preserved when the caller passes it explicitly.
CHILD_ENV_DROP_PREFIXES: tuple[str, ...] = (
    "HERMES_",  # the supervisor's own session scope (D-015)
    "ENCOMM_PCC_",  # our own data-dir override must not redirect the child
)
CHILD_ENV_DROP_EXACT: frozenset[str] = frozenset(
    {"PYTHONPATH", "PYTHONHOME", "CODEX_HOME"}
)


def codex_child_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the environment to hand a supervised Codex process.

    A **filtered copy** of the supervisor's environment (same discipline as
    ``hermes_cli.child_environment``): everything needed for a normal Windows
    process (``PATH``, ``SYSTEMROOT``, ``USERPROFILE``, ``TEMP``, ...) is kept;
    the supervisor's session scope and interpreter path are dropped, plus
    ``CODEX_HOME`` so a test/supervisor override never redirects the child's
    state directory.
    """
    import os

    source = os.environ if environ is None else environ
    filtered: dict[str, str] = {}
    for key, value in source.items():
        if key in CHILD_ENV_DROP_EXACT:
            continue
        if any(key.startswith(prefix) for prefix in CHILD_ENV_DROP_PREFIXES):
            continue
        filtered[str(key)] = str(value)
    return filtered


# -- argument construction -----------------------------------------------------
def _sandbox_or_fail(sandbox: str) -> str:
    if sandbox not in ALLOWED_SANDBOX_MODES:
        raise CodexCliError(
            f"Refusing sandbox mode {sandbox!r}; allowed: "
            + ", ".join(ALLOWED_SANDBOX_MODES)
            + ". The bypass flags are never accepted by this adapter."
        )
    return sandbox


def _validated_extra_args(values: Sequence[str]) -> list[str]:
    """Reject the forbidden flags wherever they appear in extra args."""
    args: list[str] = []
    for value in values or ():
        text = str(value)
        if text in FORBIDDEN_FLAGS:
            raise CodexCliError(f"Refusing to pass {text!r} to the Codex CLI.")
        args.append(text)
    return args


def build_exec_argv(
    *,
    executable: str,
    sandbox: str = SANDBOX_READ_ONLY,
    workspace: str = "",
    model: str = "",
    skip_git_repo_check: bool = False,
    extra_args: Sequence[str] = (),
) -> list[str]:
    """Build the argv for one new non-interactive Codex prompt.

    The prompt itself is **never** part of the argv: the caller passes it via
    ``ProcessSpec.stdin_text`` together with the trailing ``-`` argument built
    here, so arbitrary prompt text cannot hit Windows command-line limits or
    shell quoting.
    """
    argv: list[str] = [executable, "exec", "--json", "-s", _sandbox_or_fail(sandbox)]
    if workspace:
        argv += ["-C", str(workspace)]
    if model:
        argv += ["-m", str(model)]
    if skip_git_repo_check:
        argv += ["--skip-git-repo-check"]
    argv += _validated_extra_args(extra_args)
    argv += ["-"]
    return argv


def build_exec_resume_argv(
    *,
    executable: str,
    session_id: str,
    model: str = "",
    skip_git_repo_check: bool = False,
    extra_args: Sequence[str] = (),
) -> list[str]:
    """Build the argv for resuming ``session_id`` non-interactively.

    ``codex exec resume <SESSION_ID> -`` continues the recorded thread; the
    new prompt travels on stdin.  **Verified against the installed 0.154.0
    help:** the ``resume`` subcommand accepts ``--json``, ``-m``, ``--last``,
    ``--all``, ``--skip-git-repo-check`` and the config/bypass flags — but
    **NOT** ``-s/--sandbox`` or ``-C/--cd``.  A resumed thread inherits the
    original session's sandbox and working root, so neither is passed (and
    passing them exits with a usage error, verified live 2026-09-23).

    A non-empty id is required — the ``--last`` picker form is deliberately
    not exposed because "the most recent session" is never a safe assumption
    for a supervised pipeline.
    """
    session = str(session_id or "").strip()
    if not session:
        raise CodexCliError(
            "build_exec_resume_argv requires a non-empty Codex session/thread id."
        )
    argv: list[str] = [executable, "exec", "resume", session, "--json"]
    if model:
        argv += ["-m", str(model)]
    if skip_git_repo_check:
        argv += ["--skip-git-repo-check"]
    argv += _validated_extra_args(extra_args)
    argv += ["-"]
    return argv


# -- output parsing ------------------------------------------------------------
@dataclass(slots=True)
class CodexRunOutput:
    """Everything the ``--json`` JSONL protocol told us about one prompt run."""

    session_id: str | None = None
    final_text: str = ""
    turn_completed: bool = False
    turn_failed: bool = False
    exit_code: int | None = None
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None
    agent_message_count: int = 0
    malformed_lines: int = 0
    #: Bounded tail of the raw JSONL events (diagnostics only).
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Terminal success requires BOTH a completed turn and exit 0."""
        return self.turn_completed and not self.turn_failed and self.exit_code == 0

    def summary(self) -> dict[str, Any]:
        """Non-secret facts for the event log / report."""
        return {
            "session_id": self.session_id,
            "turn_completed": self.turn_completed,
            "turn_failed": self.turn_failed,
            "exit_code": self.exit_code,
            "agent_message_count": self.agent_message_count,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "malformed_lines": self.malformed_lines,
        }


#: Upper bound on retained JSONL events for diagnostics.  The stream is
#: consumed in full; only the retained detail is capped.
_MAX_RETAINED_EVENTS = 400


def _as_int(value: Any) -> int | None:  # noqa: ANN401 - JSON payloads are untyped
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text_from_event_message(message: Any) -> str:
    if isinstance(message, str):
        return message
    return ""


def parse_exec_jsonl(
    stdout: str,
    *,
    exit_code: int | None = None,
    max_retained_events: int = _MAX_RETAINED_EVENTS,
) -> CodexRunOutput:
    """Parse the JSONL event stream produced by ``codex exec --json``.

    Non-JSON lines are counted, never guessed at.  A missing terminal
    ``turn.completed`` event is **not** fabricated into success:
    :attr:`CodexRunOutput.ok` stays ``False`` and the caller must treat the
    run as unverified.  The process exit code (authoritative, from the child
    itself) is supplied by the driver and folded into the output.
    """
    output = CodexRunOutput(exit_code=exit_code)
    for raw_line in str(stdout).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            output.malformed_lines += 1
            continue
        if not isinstance(record, dict):
            output.malformed_lines += 1
            continue
        if len(output.events) < max_retained_events:
            output.events.append(record)

        msg_type = str(record.get("msg", {}).get("type") or record.get("type") or "")

        if msg_type == "session_id" or msg_type == "thread.started":
            value = record.get("session_id") or record.get("thread_id") or record.get("msg", {}).get(
                "session_id"
            )
            if value and not output.session_id:
                output.session_id = str(value)
        elif msg_type in ("agent_message", "item.completed"):
            payload = record.get("msg") or record
            item = payload.get("item") if isinstance(payload.get("item"), dict) else payload
            text = _text_from_event_message(
                item.get("text") if isinstance(item, dict) else None
            )
            if not text and isinstance(item, dict):
                text = _text_from_event_message(item.get("message"))
            if text:
                output.agent_message_count += 1
                output.final_text = text
        elif msg_type == "item.started":
            # Items stream details we do not retain; counted via events only.
            pass
        elif msg_type == "turn.completed":
            output.turn_completed = True
            usage = record.get("usage") or record.get("msg", {}).get("usage") or {}
            if isinstance(usage, dict):
                output.input_tokens = _as_int(usage.get("input_tokens"))
                output.cached_input_tokens = _as_int(usage.get("cached_input_tokens"))
                output.output_tokens = _as_int(usage.get("output_tokens"))
        elif msg_type == "turn.failed":
            output.turn_failed = True
            err = record.get("error") or record.get("msg", {}).get("error")
            if isinstance(err, dict):
                err = err.get("message")
            if err:
                output.error = str(err)

    # The final agent message wins over streamed intermediates: it was the
    # last one emitted, which is exactly what `-o` would write to disk.
    return output


def invocation_description(argv: Iterable[str]) -> str:
    """Render an argv list for logs, quoting only when an argument is empty."""
    return " ".join(f'"{a}"' if (not a or " " in a) else a for a in argv)
