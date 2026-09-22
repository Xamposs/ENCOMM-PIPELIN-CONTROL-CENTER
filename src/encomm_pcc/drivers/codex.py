"""Codex engine adapter — the second **real** driver (Session 006).

Verified against the installed Codex CLI (``codex-cli 0.154.0``) on this host.
The adapter drives the CLI non-interactively through the process layer::

    codex exec --json -s <sandbox> -C <workspace> [-m <model>] -
    codex exec resume <SESSION_ID> --json -s <sandbox> -C <workspace> [-m <model>] -

with the prompt on **stdin** (the trailing ``-``).  Design notes mirror
``hermes.py``:

* **One process per prompt.**  ``codex exec`` answers a single instruction and
  exits, so the exit code is the child's own.
* **Session ids are reported, never invented.**  The id arrives from the
  ``--json`` stream; until a prompt has run, the handle carries
  ``session_id=None``.
* **Nothing is simulated.**  A missing terminal ``turn.completed`` event, a
  non-zero exit code or a timeout is a failure with the child's own words.
* **Least privilege.**  The default sandbox is ``read-only``; roles that must
  write (Builder/fix) select ``workspace-write`` through the role config's
  ``extra`` — ``danger-full-access`` and the bypass flags are refused by
  :mod:`~encomm_pcc.drivers.codex_cli`.
* **Capabilities are evidence-gated** via :data:`_LIVE_SMOKE_VERIFIED` /
  :data:`_LIVE_RESUME_VERIFIED`, flipped only by the Session 006 live runs.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from ..domain import AgentRole
from .base import (
    BaseDriver,
    DriverCapabilities,
    DriverError,
    DriverNotImplementedError,
    DriverSession,
    PromptHandle,
    PromptResult,
    SessionRequest,
)
from .codex_cli import (
    CodexCliError,
    FORBIDDEN_FLAGS,
    build_exec_argv,
    build_exec_resume_argv,
    codex_child_environment,
    invocation_description,
    parse_exec_jsonl,
)
from .codex_discovery import CodexSessionDiscovery
from .process import ProcessSpec
from .session_discovery import (
    DEFAULT_DISCOVERY_LIMIT,
    SessionDiscoveryResult,
    SessionDiscoverer,
)

__all__ = ["DEFAULT_PROMPT_TIMEOUT_S", "CodexDriver"]

#: Wall-clock budget for one controlled Codex prompt.  A coding agent
#: legitimately works for minutes; this bounds a hung provider call.
DEFAULT_PROMPT_TIMEOUT_S = 900.0

#: Flipped to True only after REAL CALL 1 (new session) returned the expected
#: marker with exit code 0 through this exact argv path.  Verified 2026-09-23:
#: session 01a0cb24-8500-7652-a4cf-52d167569c5e answered
#: ``ENCOMM_PCC_CODEX_NEW_SESSION_OK`` (exit 0, 7.9 s) and was found by the
#: read-only discovery path with a workspace match.
#: (see docs/reports/SESSION_006_CODEX_DRIVER_SESSION_SELECTOR.md).
_LIVE_SMOKE_VERIFIED = True

#: True only after a real ``exec resume`` continued the session created by
#: REAL CALL 1.  Proven 2026-09-23: the Session 005 final-audit pipeline ran
#: through ``codex exec resume 01a0cb24-8500-7652-a4cf-52d167569c5e --json -``
#: (exit 0, 137 s), re-reported the SAME thread id, produced a strict
#: FINAL PASS with a 4-task next plan, and left the scratch worktree
#: untouched.  (An earlier attempt passed ``-s``/``-C`` to resume, which the
#: installed CLI rejects — usage error before any model work; corrected from
#: that evidence per the evidence-retry discipline.)
_LIVE_RESUME_VERIFIED = True


def _excerpt(text: str, limit: int) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + f"... [{len(text) - limit} chars omitted]"


class CodexDriver(BaseDriver, SessionDiscoverer):
    """Drives the installed Codex CLI through the non-interactive ``exec`` path."""

    driver_id = "codex"
    display_name = "Codex"
    executables = ("codex", "codex.cmd", "codex.exe")

    #: Default sandbox: least privilege.  ``workspace-write`` must be selected
    #: explicitly per role via ``SessionRequest.extra['sandbox']``.
    default_sandbox = "read-only"

    # -- capability / discovery --------------------------------------------
    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name=cls.display_name,
            supports_sessions=True,
            supports_resume=_LIVE_RESUME_VERIFIED,
            # ``codex exec --json`` emits the finished events; this adapter
            # surfaces the finished answer only — no delta callback exists.
            supports_streaming=False,
            # The supervised process is blocking; stop takes effect at the
            # next task boundary exactly like the Hermes adapter.
            supports_cancellation=False,
            # Verified per-invocation ``-m <model>`` flag.
            supports_model_selection=True,
            # Codex is configured with a workspace (+ optional model), not a
            # Hermes profile; the profile preflight must not block it.
            requires_profile=False,
            implemented=_LIVE_SMOKE_VERIFIED,
            notes=(
                "Real adapter for the installed Codex CLI (exec --json, stdin "
                "prompt). One process per prompt; real thread ids; read-only "
                "session discovery; no mid-prompt cancellation."
            ),
        )

    @classmethod
    def resolve_executable(cls) -> str | None:
        for name in cls.executables:
            found = shutil.which(name)
            if found:
                return found
        return None

    # -- session lifecycle ---------------------------------------------------
    def start_session(self, request: SessionRequest) -> DriverSession:
        """Prepare a session handle — no engine contact, no model call.

        Codex creates the real thread when the first prompt runs, so this
        returns ``session_id=None``; the id is attached by
        :meth:`wait_for_completion` once Codex actually reports it.
        """
        executable = self.resolve_executable()
        if not executable:
            raise DriverError(
                "Codex CLI not found on PATH (looked for: "
                + ", ".join(self.executables)
                + "). Install Codex or fix PATH before dispatching."
            )
        workspace = (request.workspace_path or "").strip()
        if workspace and not Path(workspace).expanduser().is_dir():
            raise DriverError(f"Workspace path does not exist: {workspace}")

        extra = dict(request.extra or {})
        sandbox = str(extra.get("sandbox") or self.default_sandbox)
        # Fail fast on an unsafe sandbox before anything is recorded.
        _validated_sandbox(sandbox)
        extra_args = _validated_extra_args(extra.get("extra_args") or ())

        session = DriverSession(
            driver_id=self.driver_id,
            role=request.role,
            session_id=None,
            persistent=False,
            external=False,
            metadata={
                "workspace_path": workspace,
                "model": request.model,
                "sandbox": sandbox,
                "executable": executable,
                "role": request.role.value,
                "session_policy": request.session_policy.value,
                "skip_git_repo_check": bool(extra.get("skip_git_repo_check", False)),
                "extra_args": list(extra_args),
            },
        )
        return self._set_session(session)

    def resume_session(self, session_id: str, request: SessionRequest) -> DriverSession:
        """Re-attach to a real Codex thread id (only when resume is proven)."""
        if not _LIVE_RESUME_VERIFIED:
            raise DriverNotImplementedError(
                "Codex resume is not verified in this build: the installed CLI "
                "supports 'exec resume <id>', but no live resume run has proved "
                "this adapter yet. capabilities().supports_resume is False, so "
                "callers must use a fresh session."
            )
        session_id = str(session_id or "").strip()
        if not session_id:
            raise DriverError("resume_session requires a non-empty Codex session id")
        session = self.start_session(request)
        session.session_id = session_id
        session.external = True
        session.metadata["resumed"] = True
        return self._set_session(session)

    # -- prompting -------------------------------------------------------------
    def send_prompt(self, session: DriverSession, prompt: str) -> PromptHandle:
        """Record the prompt; the process is launched by ``wait_for_completion``."""
        if self.resolve_executable() is None:
            raise DriverError("Codex CLI not found on PATH.")
        text = "" if prompt is None else str(prompt)
        if not text.strip():
            raise DriverError("Refusing to send an empty prompt to Codex.")
        return PromptHandle(session=session, prompt=text)

    def wait_for_completion(
        self, handle: PromptHandle, timeout_s: float | None = None
    ) -> PromptResult:
        """Run the prompt and map the real process outcome onto ``PromptResult``."""
        session = handle.session
        workspace = str(session.metadata.get("workspace_path") or "")
        model = str(session.metadata.get("model") or "")
        sandbox = str(session.metadata.get("sandbox") or self.default_sandbox)
        extra_args = list(session.metadata.get("extra_args") or [])
        skip_git_check = bool(session.metadata.get("skip_git_repo_check", False))
        resumed = bool(session.metadata.get("resumed"))
        executable = str(session.metadata.get("executable") or self.resolve_executable() or "")

        timeout = DEFAULT_PROMPT_TIMEOUT_S if timeout_s is None else float(timeout_s)
        started = time.monotonic()
        try:
            argv = self._build_argv(
                resumed=resumed,
                session_id=session.session_id,
                executable=executable,
                sandbox=sandbox,
                workspace=workspace,
                model=model,
                skip_git_check=skip_git_check,
                extra_args=extra_args,
            )
        except CodexCliError as exc:
            return self._record_result(
                PromptResult.failure(str(exc), phase="argument_build")
            )

        if workspace:
            cwd = Path(workspace).expanduser()
        else:
            raise DriverError(
                "Codex requires an explicit workspace (-C); refusing to launch "
                "without one."
            )

        spec = ProcessSpec(
            argv=argv,
            cwd=cwd,
            env=codex_child_environment(),
            stdin_text=handle.prompt,
            timeout_s=timeout,
            no_window=True,
        )
        process = self._runner.run(spec)
        duration = process.duration_s or (time.monotonic() - started)

        output = parse_exec_jsonl(process.stdout, exit_code=process.exit_code)
        # `-o` writes the last agent message to a file — a second, engine-side
        # authority for the final text when present.  (Kept simple: the JSONL
        # agent_message is primary; this avoids double persistence.)
        argv_text = invocation_description(argv)
        metadata = {
            "workspace_path": workspace,
            "argv": argv_text,
            "duration_s": round(duration, 3),
            "timed_out": process.timed_out,
            "stderr_excerpt": _excerpt(process.stderr, 2000),
            "stream": output.summary(),
            "simulated": False,
        }

        if output.session_id:
            session.session_id = output.session_id
            session.external = True

        if process.timed_out:
            return self._record_result(
                PromptResult(
                    ok=False,
                    text=output.final_text,
                    session_id=output.session_id,
                    exit_code=process.exit_code,
                    duration_s=duration,
                    error=(
                        f"Codex did not finish within {timeout:g}s and the process "
                        "tree was terminated."
                    ),
                    metadata=metadata,
                )
            )

        if not output.turn_completed:
            return self._record_result(
                PromptResult(
                    ok=False,
                    text=output.final_text,
                    session_id=output.session_id,
                    exit_code=process.exit_code,
                    duration_s=duration,
                    error=(
                        "Codex produced no terminal 'turn.completed' event on the "
                        f"--json channel (exit code {process.exit_code}); the run "
                        "cannot be verified."
                        + (f" Stream error: {output.error}" if output.error else "")
                    ),
                    metadata=metadata,
                )
            )

        ok = process.exit_code == 0 and not output.turn_failed
        error: str | None = None
        if not ok:
            error = (
                output.error
                or _excerpt(process.stderr, 1000)
                or f"Codex exited with code {process.exit_code}."
            )
        return self._record_result(
            PromptResult(
                ok=ok,
                text=output.final_text,
                session_id=output.session_id,
                exit_code=process.exit_code,
                duration_s=duration,
                simulated=False,
                error=error,
                metadata=metadata,
            )
        )

    # -- introspection -----------------------------------------------------------
    def cancel(self) -> bool:
        """Mid-prompt cancellation is not implemented (see capabilities)."""
        return False

    # -- session discovery (generic SessionDiscoverer contract) ------------------
    def discover_sessions(
        self,
        *,
        workspace_path: str | None = None,
        limit: int = DEFAULT_DISCOVERY_LIMIT,
    ) -> SessionDiscoveryResult:
        """List existing Codex sessions (read-only, bounded, newest first)."""
        return CodexSessionDiscovery().discover_sessions(
            workspace_path=workspace_path, limit=limit
        )

    # -- internals -----------------------------------------------------------------
    def _build_argv(
        self,
        *,
        resumed: bool,
        session_id: str | None,
        executable: str,
        sandbox: str,
        workspace: str,
        model: str,
        skip_git_check: bool,
        extra_args: list[str],
    ) -> list[str]:
        if resumed:
            # Verified live (0.154.0): exec resume takes NO -s/-C — the thread
            # inherits the original session's sandbox and working root.
            return build_exec_resume_argv(
                executable=executable,
                session_id=str(session_id or ""),
                model=model,
                skip_git_repo_check=skip_git_check,
                extra_args=extra_args,
            )
        return build_exec_argv(
            executable=executable,
            sandbox=sandbox,
            workspace=workspace,
            model=model,
            skip_git_repo_check=skip_git_check,
            extra_args=extra_args,
        )


# -- validation helpers (shared with codex_cli) ------------------------------------
def _validated_sandbox(sandbox: str) -> str:
    from .codex_cli import ALLOWED_SANDBOX_MODES, CodexCliError as _Err

    if sandbox not in ALLOWED_SANDBOX_MODES:
        raise _Err(
            f"Refusing sandbox mode {sandbox!r}; allowed: "
            + ", ".join(ALLOWED_SANDBOX_MODES)
            + "."
        )
    return sandbox


def _validated_extra_args(values) -> tuple[str, ...]:  # noqa: ANN001 - list/tuple/str
    if isinstance(values, str):
        values = (values,)
    args: list[str] = []
    for value in values or ():
        text = str(value)
        if text in FORBIDDEN_FLAGS:
            raise DriverError(f"Refusing to pass {text!r} to the Codex CLI.")
        args.append(text)
    return tuple(args)
