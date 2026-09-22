"""Hermes engine adapter — the first **real** driver.

Verified against the installed Hermes build (v0.21.3) on this host.  The
adapter drives the CLI non-interactively through :class:`SubprocessRunner`::

    hermes -p <profile> chat --query-file <file> --oneshot --quiet \
           --format stream-json --source tool [--in <workspace>] [-m <model>]

Design notes:

* **One process per prompt.**  ``chat --oneshot`` answers a single query and
  exits, so ``send_prompt``/``wait_for_completion`` own the process lifetime and
  the exit code is captured from the child, never inferred.
* **Session ids are reported, never invented.**  A fresh run creates a real
  session and the id arrives in the stream's ``init``/``result`` records (with
  the CLI's ``session_id:`` stderr line as a secondary source).  Until a prompt
  has run there is no id, and the handle carries ``session_id=None``.
* **Nothing is simulated.**  ``PromptResult.simulated`` is never set for a real
  run; a missing terminal record, a non-zero exit code or a timeout is a
  failure with the child's own words attached.
* **Capabilities are evidence-based.**  :data:`_LIVE_SMOKE_VERIFIED` and
  :data:`_LIVE_RESUME_VERIFIED` are flipped only when a real controlled run has
  proved the corresponding path; the flag is what the executor preflights, so a
  product path cannot dispatch an unverified adapter.
"""

from __future__ import annotations

import shutil
import tempfile
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
from .hermes_cli import (
    EXIT_OK,
    HermesCliError,
    build_chat_argv,
    child_environment,
    invocation_description,
    parse_stream_json,
)
from .process import ProcessSpec

__all__ = ["DEFAULT_PROMPT_TIMEOUT_S", "HermesDriver"]

#: Wall-clock budget for one controlled prompt.  A coding agent legitimately
#: works for minutes; this bounds a hung provider call instead of letting it
#: run forever.
DEFAULT_PROMPT_TIMEOUT_S = 900.0

#: True only after a **real** end-to-end smoke run has returned the expected
#: answer with a captured exit code (see `docs/reports/SESSION_002_HERMES_EXECUTOR.md`).
#: Verified on 2026-09-22: session 20260922_172437_722edb returned
#: ``ENCOMM_PCC_HERMES_SMOKE_OK`` with process exit code 0.  While this flag is
#: False the executor refuses to dispatch the adapter; it is flipped only by a
#: real, controlled run.
_LIVE_SMOKE_VERIFIED = True

#: True only after a real ``--resume`` run has continued the session created by
#: that smoke.  Everything else about resume is implemented (the argv, the
#: handle, the recorded id); the flag is the honest gate on advertising it.
#: Proven by `scripts/session_002_smoke.py` STEP 5 on 2026-09-22: the run
#: continued session 20260922_172437_722edb, exited 0 and re-reported the same
#: session id.
_LIVE_RESUME_VERIFIED = True


class HermesDriver(BaseDriver):
    """Drives a named Hermes profile through the installed CLI."""

    driver_id = "hermes"
    display_name = "Hermes"
    executables = ("hermes", "hermes.exe", "hermes.cmd", "hermes.bat")

    #: Default ``--source`` tag: the CLI documents ``tool`` for third-party
    #: integrations, which keeps supervised runs out of the operator's own
    #: session list.  Overridable per request via ``SessionRequest.extra``.
    default_source = "tool"

    # -- capability / discovery -------------------------------------------
    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name=cls.display_name,
            supports_sessions=True,
            supports_resume=_LIVE_RESUME_VERIFIED,
            # The CLI streams deltas, but this adapter surfaces the finished
            # answer only; advertising streaming would promise a callback the
            # driver never delivers.
            supports_streaming=False,
            # Process supervision is blocking, so mid-prompt cancellation is not
            # implemented and is not advertised.  Stop takes effect at the next
            # task boundary (see the executor).
            supports_cancellation=False,
            # Real per-invocation ``-m`` / ``--provider`` flags (verified).
            supports_model_selection=True,
            implemented=_LIVE_SMOKE_VERIFIED,
            notes=(
                "Real adapter for the installed Hermes CLI (chat --oneshot, "
                "stream-json). One process per prompt; real session ids; no "
                "mid-prompt cancellation."
            ),
        )

    @classmethod
    def resolve_executable(cls) -> str | None:
        """Absolute path of the Hermes launcher, or ``None`` when absent."""
        for name in cls.executables:
            found = shutil.which(name)
            if found:
                return found
        return None

    # -- session lifecycle -------------------------------------------------
    def start_session(self, request: SessionRequest) -> DriverSession:
        """Prepare a session handle for ``request``.

        Hermes creates the session when the first prompt runs, so this returns a
        handle with ``session_id=None`` and ``external=False``: there is nothing
        real to report yet.  The id is attached by :meth:`wait_for_completion`
        once Hermes has actually reported it.
        """
        executable = self.resolve_executable()
        if not executable:
            raise DriverError(
                "Hermes CLI not found on PATH (looked for: "
                + ", ".join(self.executables)
                + "). Install Hermes or fix PATH before dispatching."
            )
        workspace = (request.workspace_path or "").strip()
        if workspace and not Path(workspace).expanduser().is_dir():
            raise DriverError(f"Workspace path does not exist: {workspace}")

        extra = dict(request.extra or {})
        extra_args = _validated_extra_args(extra.get("extra_args") or ())

        session = DriverSession(
            driver_id=self.driver_id,
            role=request.role,
            session_id=None,
            persistent=False,
            external=False,
            metadata={
                "profile": request.project_profile,
                "workspace_path": workspace,
                "model": request.model,
                "provider": request.provider,
                "executable": executable,
                "role": request.role.value,
                "session_policy": request.session_policy.value,
                "source": str(extra.get("source") or self.default_source),
                "extra_args": list(extra_args),
            },
        )
        # The role's session policy is applied by SessionManager before a session
        # is started; BUILDER's `always_new` policy therefore always lands here.
        return self._set_session(session)

    def resume_session(self, session_id: str, request: SessionRequest) -> DriverSession:
        """Re-attach to ``session_id`` (only when resume has been proved)."""
        if not _LIVE_RESUME_VERIFIED:
            raise DriverNotImplementedError(
                "Hermes resume is not verified in this build: the installed CLI "
                "supports '--resume', but no live resume run has proved this "
                "adapter yet. capabilities().supports_resume is False, so callers "
                "must use a fresh session."
            )
        if not session_id:
            raise DriverError("resume_session requires a session id")
        session = self.start_session(request)
        session.session_id = str(session_id)
        session.external = True
        session.metadata["resumed"] = True
        return self._set_session(session)

    # -- prompting ---------------------------------------------------------
    def send_prompt(self, session: DriverSession, prompt: str) -> PromptHandle:
        """Record the prompt; the process is launched by ``wait_for_completion``."""
        if self.resolve_executable() is None:
            raise DriverError("Hermes CLI not found on PATH.")
        text = "" if prompt is None else str(prompt)
        if not text.strip():
            raise DriverError("Refusing to send an empty prompt to Hermes.")
        return PromptHandle(session=session, prompt=text)

    def wait_for_completion(
        self, handle: PromptHandle, timeout_s: float | None = None
    ) -> PromptResult:
        """Run the prompt and map the real process outcome onto ``PromptResult``."""
        session = handle.session
        workspace = str(session.metadata.get("workspace_path") or "")
        profile = str(session.metadata.get("profile") or "")
        model = str(session.metadata.get("model") or "")
        provider = str(session.metadata.get("provider") or "")
        source = str(session.metadata.get("source") or self.default_source)
        extra_args = list(session.metadata.get("extra_args") or [])
        executable = str(session.metadata.get("executable") or self.resolve_executable() or "")

        timeout = DEFAULT_PROMPT_TIMEOUT_S if timeout_s is None else float(timeout_s)
        scratch = Path(tempfile.mkdtemp(prefix="encomm-pcc-hermes-"))
        prompt_file = scratch / "prompt.txt"
        started = time.monotonic()
        try:
            prompt_file.write_text(handle.prompt, encoding="utf-8")
            try:
                argv = build_chat_argv(
                    executable=executable,
                    query_file=str(prompt_file),
                    profile=profile,
                    workspace=workspace,
                    resume_session_id=(
                        session.session_id if session.metadata.get("resumed") else None
                    ),
                    model=model,
                    provider=provider,
                    source=source,
                    extra_args=extra_args,
                )
            except HermesCliError as exc:
                return self._record_result(PromptResult.failure(str(exc), phase="argument_build"))

            spec = ProcessSpec(
                argv=argv,
                cwd=Path(workspace).expanduser() if workspace else scratch,
                env=child_environment(),
                timeout_s=timeout,
                no_window=True,
            )
            process = self._runner.run(spec)
            duration = process.duration_s or (time.monotonic() - started)
            output = parse_stream_json(process.stdout)
            stderr_session_id = _session_id_from_stderr(process.stderr)
            if not output.session_id and stderr_session_id:
                output.session_id = stderr_session_id

            metadata = {
                "profile": profile,
                "workspace_path": workspace,
                "argv": invocation_description(argv),
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
                        text=output.text,
                        session_id=output.session_id,
                        exit_code=process.exit_code,
                        duration_s=duration,
                        error=(
                            f"Hermes did not finish within {timeout:g}s and the process "
                            "tree was terminated."
                        ),
                        metadata=metadata,
                    )
                )

            if not output.saw_result:
                return self._record_result(
                    PromptResult(
                        ok=False,
                        text=output.text,
                        session_id=output.session_id,
                        exit_code=process.exit_code,
                        duration_s=duration,
                        error=(
                            "Hermes produced no terminal 'result' record on the "
                            f"stream-json channel (exit code {process.exit_code}); the "
                            "run cannot be verified."
                        ),
                        metadata=metadata,
                    )
                )

            ok = process.exit_code == EXIT_OK and (output.reported_exit_code in (None, EXIT_OK))
            error: str | None = None
            if not ok:
                error = output.error or _excerpt(process.stderr, 1000) or (
                    f"Hermes exited with code {process.exit_code}."
                )
            return self._record_result(
                PromptResult(
                    ok=ok,
                    text=output.text,
                    session_id=output.session_id,
                    exit_code=process.exit_code,
                    duration_s=duration,
                    simulated=False,
                    error=error,
                    metadata=metadata,
                )
            )
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    # -- introspection ------------------------------------------------------
    def cancel(self) -> bool:
        """Mid-prompt cancellation is not implemented (see capabilities)."""
        return False


#: Extra CLI flags this application will never pass on a caller's behalf.
_FORBIDDEN_EXTRA_ARGS = frozenset({"--yolo", "-z", "--oneshot", "--profile", "-p"})


def _validated_extra_args(values) -> tuple[str, ...]:  # noqa: ANN001 - list/tuple/str
    """Normalise optional extra CLI args, refusing the ones we never send.

    ``--yolo`` (blanket approval bypass) is refused outright: the pipeline runs
    with the CLI's own approval behaviour, and a caller must not be able to turn
    that off through configuration.  ``-p/--profile`` is refused because the
    profile is the driver's own concern and a duplicate flag would be ambiguous.
    """
    if isinstance(values, str):
        values = (values,)
    args: list[str] = []
    for value in values or ():
        text = str(value)
        if text in _FORBIDDEN_EXTRA_ARGS:
            raise DriverError(f"Refusing to pass '{text}' to the Hermes CLI.")
        args.append(text)
    return tuple(args)


def _session_id_from_stderr(stderr: str) -> str | None:
    """Secondary source for the session id: the CLI's own ``session_id:`` line.

    Used only when the stream did not carry one.  The value is taken verbatim
    from the child's output — it is never generated here.
    """
    for line in reversed(str(stderr).splitlines()):
        stripped = line.strip()
        if stripped.startswith("session_id:"):
            value = stripped.split(":", 1)[1].strip()
            if value:
                return value
    return None


def _excerpt(text: str, limit: int) -> str:
    """Bound a captured stream for storage; the full text stays in memory."""
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + f"... [{len(text) - limit} chars omitted]"