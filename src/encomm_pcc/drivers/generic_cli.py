"""Generic CLI engine adapter — **real** as of Session 007.

Turns ``GenericCliDriver`` from the refusing v0.1 placeholder into a genuine
driver for *compatible* command-line agents: the operator configures a safe,
structured invocation (executable + literal argv tokens + transport + result
mode) and the adapter runs **one supervised process per prompt**::

    <executable> [args…] [model_args…]     # prompt via stdin or a temp file

Design rules (Session 007 brief §4–§10, ADR-0039):

* **Generic means generic.**  No OpenCode / Claude / Kimi / Ollama / Codex /
  Hermes names appear here; the driver only knows the structured config
  (:mod:`~encomm_pcc.drivers.generic_cli_config`).
* **Stateless, honestly.**  ``supports_sessions=False`` / ``implemented=True``.
  The existing :class:`~encomm_pcc.core.session_manager.SessionManager` then
  naturally decides ``NONE`` — no pipeline logic changes anywhere.  Persistent
  sessions are NOT pretended: a CLI with no reliable resume contract gets none.
* **One process per prompt** through the injected
  :class:`~encomm_pcc.drivers.process.ProcessRunner` — the driver never touches
  ``subprocess``; the spec is argv-list-only with an explicit cwd; a timeout
  kills the child tree (existing ``SubprocessRunner`` guarantees).
* **Real result, real exit code, nothing silent.**  ``exit 0 + non-empty
  extracted answer`` is the success contract; timeouts, non-zero exits and
  structurally invalid output all fail with the child's own diagnostics.
* **Session id stays ``None``.**  There is no generic session protocol to
  verify, so none is invented.

Configured values travel in ``SessionRequest.extra['generic_cli']`` (durable in
``role_configs.extra_json`` — no schema change), built by
:meth:`GenericCliDriver.config_from_request`.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from .base import (
    BaseDriver,
    DriverCapabilities,
    DriverError,
    DriverSession,
    PromptHandle,
    PromptResult,
    SessionRequest,
)
from .generic_cli_config import (
    CONFIG_EXTRA_KEY,
    DEFAULT_TIMEOUT_S,
    GenericCliConfig,
    GenericCliConfigError,
    PLACEHOLDER_PROMPT_FILE,
    PROMPT_TRANSPORT_STDIN,
    build_argv,
    child_environment,
    extract_result_text,
)

__all__ = ["DEFAULT_PROMPT_TIMEOUT_S", "GenericCliDriver"]

#: Fallback wall-clock budget when a caller passes ``timeout_s=None``.  The
#: configured per-role value (bounded 1..7200 s) always wins when present.
DEFAULT_PROMPT_TIMEOUT_S = DEFAULT_TIMEOUT_S

#: Maximum stderr retained as diagnostics on any result (bounded, never logged
#: with the child environment).
_STDERR_DIAGNOSTIC_CHARS = 2000


class GenericCliDriver(BaseDriver):
    """Runs an operator-configured CLI agent: one supervised process per prompt."""

    driver_id = "generic_cli"
    display_name = "Generic CLI"
    executables: tuple[str, ...] = ()  # operator-configured; see resolve_executable

    # -- capability / configuration ---------------------------------------
    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name=cls.display_name,
            supports_sessions=False,
            supports_resume=False,
            supports_streaming=False,
            # Stop/pause stay boundary-only, exactly like Hermes/Codex: a
            # running prompt finishes; the boundary flag is honoured next.
            supports_cancellation=False,
            # Model selection exists only when the operator configures
            # model_args; the UI/config gate on that, not on a hardcoded yes.
            supports_model_selection=False,
            requires_profile=False,
            implemented=True,
            notes=(
                "Real adapter for operator-configured command-line agents. "
                "Structured argv (never a shell command), stdin or temp-file "
                "prompt transport, stdout/json/jsonl result extraction, one "
                "supervised process per prompt, stateless (no sessions)."
            ),
        )

    @classmethod
    def config_from_request(cls, request: SessionRequest) -> GenericCliConfig:
        """Strictly parse the role's Generic CLI configuration.

        The config dict is stored under ``extra['generic_cli']`` by the UI
        (``_save_generic_cli_config`` in ``ui/panels.py``).  A missing or
        invalid config raises :class:`GenericCliConfigError` (a
        :class:`DriverError`) — which every executor dispatch path already
        maps to a loud, blocked/failed run.
        """
        extra = dict(request.extra or {})
        return GenericCliConfig.from_mapping(extra.get(CONFIG_EXTRA_KEY))

    @classmethod
    def resolve_executable(cls, executable: str | None = None) -> str | None:
        """Resolve the configured executable (PATH lookup, never a launch)."""
        text = str(executable or "").strip()
        if not text:
            return None
        from pathlib import Path as _Path

        candidate = _Path(text)
        if candidate.is_absolute():
            return text if candidate.is_file() else None
        return shutil.which(text)

    # -- session lifecycle ---------------------------------------------------
    def start_session(self, request: SessionRequest) -> DriverSession:
        """Prepare the stateless run handle — validates config, starts nothing.

        This is where a bad configuration is refused (fail closed, before any
        process, before any state change): executable resolvable on PATH,
        workspace sane.  No model call, no process, no side effects.
        """
        config = self.config_from_request(request)

        executable = self.resolve_executable(config.executable)
        if not executable:
            if Path(config.executable).is_absolute():
                hint = f"The configured path does not exist: {config.executable!r}."
            else:
                hint = (
                    "Install it or fix the PATH before dispatching "
                    f"(configured value: {config.executable!r})."
                )
            raise DriverError(
                f"The configured Generic CLI executable {config.executable!r} "
                f"could not be found on PATH. {hint}"
            )

        workspace = str(request.workspace_path or "").strip()
        if workspace and not Path(workspace).expanduser().is_dir():
            raise DriverError(f"Workspace path does not exist: {workspace}")

        session = DriverSession(
            driver_id=self.driver_id,
            role=request.role,
            session_id=None,  # stateless: never a session id
            persistent=False,
            external=False,
            metadata={
                "workspace_path": workspace,
                "model": request.model,
                "role": request.role.value,
                "session_policy": request.session_policy.value,
                "executable": executable,
                "generic_cli_config": config.to_dict(),
            },
        )
        return self._set_session(session)

    def resume_session(self, session_id: str, request: SessionRequest) -> DriverSession:
        """Refuse honestly: a stateless driver has nothing to resume.

        ``supports_sessions=False`` means the ``SessionManager`` decides
        ``NONE`` and this is never reachable through the pipeline; it exists so
        a direct caller gets a loud refusal instead of a silent fresh run.
        """
        raise DriverError(
            "Generic CLI is stateless (supports_sessions=False): there is no "
            f"session {str(session_id or '')!r} to resume. Run a fresh prompt "
            "instead."
        )

    # -- prompting -----------------------------------------------------------
    def send_prompt(self, session: DriverSession, prompt: str) -> PromptHandle:
        """Record the prompt; the process is launched by ``wait_for_completion``."""
        config = GenericCliConfig.from_mapping(
            dict(session.metadata.get("generic_cli_config") or {})
        )
        text = "" if prompt is None else str(prompt)
        if not text.strip():
            raise DriverError("Refusing to send an empty prompt to the Generic CLI.")
        return PromptHandle(session=session, prompt=text)

    def wait_for_completion(
        self, handle: PromptHandle, timeout_s: float | None = None
    ) -> PromptResult:
        """Run one supervised process and map its real outcome honestly."""
        session = handle.session
        metadata_cfg = dict(session.metadata.get("generic_cli_config") or {})
        config = GenericCliConfig.from_mapping(metadata_cfg)
        workspace = str(session.metadata.get("workspace_path") or "")
        model = str(session.metadata.get("model") or "")

        if timeout_s is None:
            timeout_s = config.timeout_s
        timeout = float(timeout_s)
        started = time.monotonic()

        prompt_file: str = ""
        temp_path: Path | None = None
        stdin_text: str | None = None
        try:
            if config.prompt_transport == PROMPT_TRANSPORT_STDIN:
                stdin_text = handle.prompt
            else:
                temp_path = self._write_prompt_file(handle.prompt)
                prompt_file = str(temp_path)
            try:
                argv = build_argv(
                    config,
                    workspace=workspace,
                    model=model,
                    prompt_file=prompt_file,
                )
            except GenericCliConfigError as exc:
                # Configuration problems are honest failures, never crashes:
                # the run is reported with the operator-actionable reason.
                return self._record_result(
                    PromptResult.failure(
                        f"The Generic CLI run was refused before launch: {exc}",
                        phase="argument_build",
                    )
                )
            spec = self._build_spec(
                config,
                argv=argv,
                workspace=workspace,
                stdin_text=stdin_text,
                timeout=timeout,
            )
            process = self._runner.run(spec)
        finally:
            if temp_path is not None:
                self._cleanup_prompt_file(temp_path)
        duration = process.duration_s or (time.monotonic() - started)

        stderr_excerpt = self._excerpt(process.stderr, _STDERR_DIAGNOSTIC_CHARS)
        argv_text = self._render_argv(argv)
        result_metadata = {
            "workspace_path": workspace,
            "argv": argv_text,
            "duration_s": round(duration, 3),
            "timed_out": process.timed_out,
            "stderr_excerpt": stderr_excerpt,
            "prompt_transport": config.prompt_transport,
            "result_mode": config.result_mode,
            "simulated": False,
        }

        # -- timeout: never success, never silent ---------------------------
        if process.timed_out:
            return self._record_result(
                PromptResult(
                    ok=False,
                    text="",
                    session_id=None,
                    exit_code=process.exit_code,
                    duration_s=duration,
                    error=(
                        f"The Generic CLI process did not finish within {timeout:g}s "
                        "and the process tree was terminated."
                    ),
                    metadata=result_metadata,
                )
            )

        # -- structured result extraction (may fail closed) -------------------
        try:
            text = extract_result_text(
                result_mode=config.result_mode,
                stdout=process.stdout,
                result_field=config.result_field,
            )
        except GenericCliConfigError as exc:
            return self._record_result(
                PromptResult(
                    ok=False,
                    text="",
                    exit_code=process.exit_code,
                    duration_s=duration,
                    error=(
                        f"The CLI exited with code {process.exit_code} but its "
                        f"output did not satisfy the configured result mode: {exc}"
                    ),
                    metadata=result_metadata,
                )
            )

        # -- success contract: exit 0 + non-empty answer ----------------------
        if process.exit_code != 0:
            detail = stderr_excerpt or f"exit code {process.exit_code}"
            return self._record_result(
                PromptResult(
                    ok=False,
                    text=text,
                    exit_code=process.exit_code,
                    duration_s=duration,
                    error=(
                        f"The CLI exited with code {process.exit_code} before "
                        f"returning a valid result ({detail})."
                    ),
                    metadata=result_metadata,
                )
            )
        if not text.strip():
            return self._record_result(
                PromptResult(
                    ok=False,
                    text="",
                    exit_code=process.exit_code,
                    duration_s=duration,
                    error=(
                        "The CLI exited with code 0 but produced no output on "
                        "stdout; the run cannot be verified."
                    ),
                    metadata=result_metadata,
                )
            )

        return self._record_result(
            PromptResult(
                ok=True,
                text=text,
                session_id=None,  # stateless: never invented
                exit_code=process.exit_code,
                duration_s=duration,
                simulated=False,
                error=None,
                metadata=result_metadata,
            )
        )

    # -- internals --------------------------------------------------------------
    def _build_spec(
        self,
        config: GenericCliConfig,
        *,
        argv: list[str],
        workspace: str,
        stdin_text: str | None,
        timeout: float,
    ):
        from .process import ProcessSpec

        # Explicit cwd always (mirrors the Codex adapter): the executor
        # preflight guarantees a workspace; a direct caller gets a loud refusal.
        if not workspace:
            raise DriverError(
                "The Generic CLI requires an explicit workspace directory; "
                "refusing to launch without one."
            )
        cwd = Path(workspace).expanduser()
        return ProcessSpec(
            argv=argv,
            cwd=cwd,
            env=child_environment(config.env_overrides),
            stdin_text=stdin_text,
            timeout_s=timeout,
            no_window=True,
        )

    def _write_prompt_file(self, prompt: str) -> Path:
        """Private UTF-8 prompt file in the user's temp dir (never world-readable)."""
        import tempfile

        fd, name = tempfile.mkstemp(
            prefix="encomm_pcc_prompt_", suffix=".txt", text=True
        )
        path = Path(name)
        try:
            with open(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(prompt)
        except Exception:
            self._cleanup_prompt_file(path)
            raise
        return path

    def _cleanup_prompt_file(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass  # best-effort; the file lives in the private temp dir

    def _excerpt(self, text: str, limit: int) -> str:
        text = str(text or "")
        if len(text) <= limit:
            return text
        return text[:limit] + f"... [{len(text) - limit} chars omitted]"

    def _render_argv(self, argv: list[str]) -> str:
        from .codex_cli import invocation_description

        return invocation_description(argv)


# Backwards-compatible alias: the placeholder's refusal class is gone, but the
# module still exposes the config error under the driver namespace for tests.
__all__.append("GenericCliConfigError")
