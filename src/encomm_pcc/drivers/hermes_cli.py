"""The verified Hermes CLI contract, in one testable place.

Everything in this module was derived from the **installed** Hermes build
(v0.21.3, `hermes --help`, `hermes chat --help`, and the shipped source of
`hermes_cli/oneshot.py`, `hermes_cli/stream_json.py` and `cli.py`).  It is pure
data and pure functions: no process is started here, so argument construction
and output parsing are unit-testable without touching a network or an engine.

Verified invocation used by :class:`~encomm_pcc.drivers.hermes.HermesDriver`::

    hermes -p <profile> chat --query-file <file> --oneshot --quiet \
           --format stream-json --source tool [--in <workspace>] \
           [--resume <session_id>] [-m <model>] [--provider <provider>]

Verified behaviour (see SESSION_002 report for the evidence table):

* ``-p/--profile`` selects a named profile home before any Hermes module loads.
  An unknown profile is a hard error (exit 1) and creates nothing.
* ``chat --query-file`` reads the prompt from a file or ``-`` (stdin) verbatim:
  nothing is shell-interpreted.
* ``--oneshot`` + non-TTY stdio answers a single query and exits.
* ``--format stream-json`` writes one JSON object per stdout line and implies
  ``--quiet``: ``system/init`` (model + session_id), ``text`` deltas,
  ``tool_use`` / ``tool_result``, then one terminal ``result`` envelope
  carrying ``session_id``, ``exit_code``, ``text`` and token counts.
* Diagnostics and a ``session_id: <id>`` line go to **stderr**; stdout carries
  only the protocol (or the final text in ``--format text``).
* Exit codes: ``0`` completed, ``1`` failed / no final response, ``2`` usage
  error or a partial run, ``130`` interrupted.
* Sessions are real and are never required: the id is reported, not invented.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "CHILD_ENV_DROP_EXACT",
    "CHILD_ENV_DROP_PREFIXES",
    "EXIT_AGENT_FAILURE",
    "EXIT_INTERRUPTED",
    "EXIT_OK",
    "EXIT_USAGE",
    "HermesCliError",
    "HermesRunOutput",
    "build_chat_argv",
    "child_environment",
    "parse_stream_json",
]

# -- verified exit codes -------------------------------------------------------
EXIT_OK = 0
EXIT_AGENT_FAILURE = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130

#: JSONL record types emitted by ``--format stream-json``.
_INIT_TYPE = "system"
_TEXT_TYPE = "text"
_RESULT_TYPE = "result"

#: Upper bound on raw JSONL records retained for diagnostics.  The stream is
#: consumed in full; only the retained detail is capped.
_MAX_RETAINED_EVENTS = 500


class HermesCliError(RuntimeError):
    """The CLI contract was violated: bad arguments or unparsable output."""


# -- child environment ---------------------------------------------------------
#: Environment prefixes that are stripped before launching a supervised agent.
#: These carry the *supervisor's* identity (session id, spawn marker, approval
#: scope, profile home, an inherited inference override).  Leaking them into the
#: child would silently change its behaviour — e.g. ``HERMES_KANBAN_TASK``
#: rewrites the CLI's exit codes, and ``HERMES_INFERENCE_MODEL`` overrides the
#: model we pass explicitly.
CHILD_ENV_DROP_PREFIXES: tuple[str, ...] = ("HERMES_",)

#: Exact names dropped as well: the interpreter path of the *supervisor* must
#: not join the child's import path.
CHILD_ENV_DROP_EXACT: frozenset[str] = frozenset({"PYTHONPATH", "PYTHONHOME"})


def child_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the environment to hand a supervised Hermes process.

    A **filtered copy** of the supervisor's environment: everything is kept
    (``PATH``, ``SYSTEMROOT``, the user's home and temp locations are all needed
    for a launcher to work) except the supervisor's own Hermes session state and
    the interpreter path, listed in :data:`CHILD_ENV_DROP_PREFIXES` and
    :data:`CHILD_ENV_DROP_EXACT`.

    The returned mapping is never logged or persisted as a whole: it is exactly
    the child environment, and nothing else about the supervisor's environment
    is recorded.
    """
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
def build_chat_argv(
    *,
    executable: str,
    query_file: str,
    profile: str = "",
    workspace: str = "",
    resume_session_id: str | None = None,
    model: str = "",
    provider: str = "",
    source: str = "tool",
    extra_args: Sequence[str] = (),
) -> list[str]:
    """Build the argv list for one controlled Hermes prompt.

    The prompt itself is never part of the argv: it travels in ``query_file``,
    so arbitrary prompt text (quotes, newlines, ``$(...)``) cannot be mangled by
    argument parsing and long prompts cannot hit a command-line length limit.

    ``provider`` without ``model`` is refused here rather than at the engine:
    the installed CLI rejects that combination (exit 2), and a supervisor that
    sends it anyway would be guessing at the user's intent.
    """
    argv: list[str] = [executable]
    if profile:
        argv += ["-p", profile]
    argv += ["chat", "--query-file", str(query_file), "--oneshot", "--quiet"]
    argv += ["--format", "stream-json"]
    if source:
        argv += ["--source", source]
    if workspace:
        argv += ["--in", str(workspace)]
    if resume_session_id:
        argv += ["--resume", str(resume_session_id)]
    if model:
        argv += ["-m", model]
        if provider:
            argv += ["--provider", provider]
    elif provider:
        raise HermesCliError(
            "The installed Hermes CLI rejects '--provider' without '--model' "
            "(it cannot tell which model the account hosts). Configure a model "
            "for this role, or clear the provider."
        )
    argv += [str(arg) for arg in extra_args]
    return argv


# -- output parsing ------------------------------------------------------------
@dataclass(slots=True)
class HermesRunOutput:
    """Everything the stream-json protocol told us about one prompt run."""

    session_id: str | None = None
    final_text: str = ""
    streamed_text: str = ""
    model: str = ""
    reported_exit_code: int | None = None
    tokens: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    saw_init: bool = False
    saw_result: bool = False
    tool_use_count: int = 0
    tool_error_count: int = 0
    event_counts: dict[str, int] = field(default_factory=dict)
    malformed_lines: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def text(self) -> str:
        """The answer: the terminal ``result`` text, else the streamed deltas."""
        return self.final_text or self.streamed_text

    def summary(self) -> dict[str, Any]:
        """Non-secret facts for the event log / report."""
        return {
            "session_id": self.session_id,
            "model": self.model,
            "reported_exit_code": self.reported_exit_code,
            "saw_init": self.saw_init,
            "saw_result": self.saw_result,
            "tool_use_count": self.tool_use_count,
            "tool_error_count": self.tool_error_count,
            "tokens": dict(self.tokens),
            "malformed_lines": self.malformed_lines,
        }


def _as_int(value: Any) -> int:  # noqa: ANN401 - JSON payloads are untyped
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def parse_stream_json(stdout: str, *, max_retained_events: int = _MAX_RETAINED_EVENTS) -> HermesRunOutput:
    """Parse the JSONL stream produced by ``--format stream-json``.

    Non-JSON lines are counted, never guessed at: the protocol is line-delimited
    JSON, so anything else is recorded as ``malformed_lines`` and ignored.  A
    missing ``result`` record is *not* fabricated — :attr:`HermesRunOutput.saw_result`
    stays ``False`` and the caller must treat the run as unverified.
    """
    output = HermesRunOutput()
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

        record_type = str(record.get("type") or "unknown")
        output.event_counts[record_type] = output.event_counts.get(record_type, 0) + 1
        if len(output.events) < max_retained_events:
            output.events.append(record)

        if record_type == _INIT_TYPE and record.get("subtype") == "init":
            output.saw_init = True
            if record.get("session_id") and not output.session_id:
                output.session_id = str(record["session_id"])
            if record.get("model") and not output.model:
                output.model = str(record["model"])
        elif record_type == _TEXT_TYPE:
            chunk = record.get("text")
            if chunk:
                output.streamed_text += str(chunk)
        elif record_type == "tool_use":
            output.tool_use_count += 1
        elif record_type == "tool_result":
            if record.get("is_error"):
                output.tool_error_count += 1
        elif record_type == _RESULT_TYPE:
            output.saw_result = True
            if record.get("session_id"):
                output.session_id = str(record["session_id"])
            text = record.get("text")
            output.final_text = str(text) if text else ""
            if record.get("exit_code") is not None:
                output.reported_exit_code = _as_int(record.get("exit_code"))
            if record.get("error"):
                output.error = str(record["error"])
            tokens = record.get("tokens")
            if isinstance(tokens, dict):
                output.tokens = {str(k): _as_int(v) for k, v in tokens.items()}
    return output


def invocation_description(argv: Iterable[str]) -> str:
    """Render an argv list for logs, quoting only when an argument is empty."""
    return " ".join(f'"{a}"' if (not a or " " in a) else a for a in argv)