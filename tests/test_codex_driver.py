"""CodexDriver: the real Session 006 adapter — full offline matrix.

Every claim here is proven against a scripted :class:`FakeProcessRunner`; the
real CLI is exercised once each for new-session and resume in
``scripts/session_006_codex_smoke.py`` (the cost guard).  The parser matrix is
deliberately wider than the happy path: a missing terminal event, malformed
protocol data and a non-zero exit can never become success.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from encomm_pcc.domain import AgentRole, SessionPolicy
from encomm_pcc.drivers import (
    CodexDriver,
    DriverError,
    NullProcessRunner,
    ProcessSpec,
    SessionRequest,
)
from encomm_pcc.drivers.codex import (
    _LIVE_RESUME_VERIFIED,
    _LIVE_SMOKE_VERIFIED,
    _validated_sandbox,
)
from encomm_pcc.drivers.codex_cli import (
    ALLOWED_SANDBOX_MODES,
    CodexCliError,
    build_exec_argv,
    build_exec_resume_argv,
    codex_child_environment,
    parse_exec_jsonl,
)

MARKER = "ENCOMM_PCC_CODEX_NEW_SESSION_OK"


@pytest.fixture()
def ws(tmp_path: Path) -> str:
    """A real directory: start_session validates workspace existence."""
    path = tmp_path / "ws"
    path.mkdir()
    return str(path)


def _request(**overrides) -> SessionRequest:
    values = dict(
        role=AgentRole.ORCHESTRATOR,
        workspace_path="C:/ws",
        project_profile="",
        provider="",
        model="",
        session_policy=SessionPolicy.PERSISTENT_OPTIONAL,
        extra={},
    )
    values.update(overrides)
    return SessionRequest(**values)


def _codex_jsonl(
    *,
    session_id: str = "019d1123-1111-2222-3333-444455556666",
    message: str = MARKER,
    completed: bool = True,
    failed: bool = False,
    usage: dict | None = None,
    extra_lines: list[str] | None = None,
) -> str:
    lines: list[str] = [f'{{"msg":{{"type":"session_id","session_id":"{session_id}"}}}}']
    lines.extend(extra_lines or [])
    if failed:
        lines.append('{"msg":{"type":"turn.failed","error":{"message":"nope"}}}')
    if completed and not failed:
        usage_payload = usage or {"input_tokens": 100, "output_tokens": 20}
        lines.append(
            json.dumps({"msg": {"type": "turn.completed", "usage": usage_payload}})
        )
    lines.append(json.dumps({"msg": {"type": "agent_message", "message": message}}))
    return "\n".join(lines) + "\n"


def _runner_result(argv, *, exit_code=0, stdout="", stderr="", timed_out=False):
    from encomm_pcc.drivers import ProcessResult

    return ProcessResult(
        argv=argv,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_s=0.25,
        timed_out=timed_out,
    )


class ScriptedCodexRunner:
    """Records specs, returns the queued results (one per run)."""

    def __init__(self, results):
        self.results = list(results)
        self.specs: list[ProcessSpec] = []

    def run(self, spec: ProcessSpec):
        self.specs.append(spec)
        if not self.results:
            raise AssertionError("ScriptedCodexRunner: no queued result")
        canned = self.results.pop(0)
        return replace(canned, argv=spec.argv_list())


# -- argv construction -------------------------------------------------------
def test_exec_argv_carries_json_sandbox_workspace_and_stdin_marker() -> None:
    argv = build_exec_argv(
        executable="codex.exe",
        sandbox="workspace-write",
        workspace="C:/ws",
        model="gpt-5.1-codex",
    )
    assert argv[:4] == ["codex.exe", "exec", "--json", "-s"]
    assert "workspace-write" in argv
    assert argv[argv.index("-C") + 1] == "C:/ws"
    assert argv[argv.index("-m") + 1] == "gpt-5.1-codex"
    assert argv[-1] == "-", "prompt must travel on stdin, never in argv"


def test_resume_argv_places_the_real_session_id() -> None:
    argv = build_exec_resume_argv(
        executable="codex.exe",
        session_id="019d1123-1111-2222-3333-444455556666",
    )
    assert argv[:5] == ["codex.exe", "exec", "resume", "019d1123-1111-2222-3333-444455556666", "--json"]
    assert argv[-1] == "-"
    # Verified live (0.154.0): resume accepts neither -s nor -C.
    assert "-s" not in argv
    assert "-C" not in argv


def test_resume_argv_refuses_an_empty_session_id() -> None:
    with pytest.raises(CodexCliError):
        build_exec_resume_argv(executable="codex.exe", session_id="   ")


def test_bypass_sandbox_and_flags_are_refused() -> None:
    with pytest.raises(CodexCliError):
        build_exec_argv(executable="codex.exe", sandbox="danger-full-access")
    with pytest.raises(CodexCliError):
        build_exec_argv(
            executable="codex.exe",
            extra_args=["--dangerously-bypass-approvals-and-sandbox"],
        )
    with pytest.raises(CodexCliError):
        build_exec_resume_argv(
            executable="codex.exe",
            session_id="019d",
            extra_args=["--ephemeral"],
        )
    assert "danger-full-access" not in ALLOWED_SANDBOX_MODES


def test_child_environment_drops_supervisor_state_and_codex_home() -> None:
    env = codex_child_environment(
        {
            "PATH": "C:/Windows",
            "SYSTEMROOT": "C:/Windows",
            "HERMES_SESSION_ID": "supervisor-session",
            "ENCOMM_PCC_DATA_DIR": "C:/supervisor-data",
            "PYTHONPATH": "C:/supervisor-src",
            "CODEX_HOME": "C:/supervisor-codex-home",
        }
    )
    assert env["PATH"] == "C:/Windows"
    assert env["SYSTEMROOT"] == "C:/Windows"
    assert "HERMES_SESSION_ID" not in env
    assert "ENCOMM_PCC_DATA_DIR" not in env
    assert "PYTHONPATH" not in env
    assert "CODEX_HOME" not in env, "a supervisor override must not redirect the child"


# -- JSONL parser matrix -------------------------------------------------------
def test_parser_extracts_thread_id_final_text_and_terminal_success() -> None:
    out = parse_exec_jsonl(
        _codex_jsonl(usage={"input_tokens": 11, "cached_input_tokens": 5, "output_tokens": 3}),
        exit_code=0,
    )
    assert out.session_id == "019d1123-1111-2222-3333-444455556666"
    assert out.final_text == MARKER
    assert out.turn_completed is True
    assert out.turn_failed is False
    assert out.ok is True
    assert out.input_tokens == 11
    assert out.cached_input_tokens == 5
    assert out.output_tokens == 3


def test_parser_missing_terminal_event_is_never_success() -> None:
    out = parse_exec_jsonl(
        _codex_jsonl(completed=False, extra_lines=['{"msg":{"type":"agent_reasoning","text":"hmm"}}']),
        exit_code=0,
    )
    assert out.ok is False
    assert out.turn_completed is False


def test_parser_turn_failed_is_never_success() -> None:
    out = parse_exec_jsonl(_codex_jsonl(failed=True), exit_code=0)
    assert out.ok is False
    assert out.turn_failed is True
    assert out.error == "nope"


def test_parser_nonzero_exit_is_never_success() -> None:
    out = parse_exec_jsonl(_codex_jsonl(), exit_code=3)
    assert out.ok is False
    assert out.exit_code == 3


def test_parser_malformed_lines_are_counted_not_fatal() -> None:
    raw = "not json at all\n" + _codex_jsonl()
    out = parse_exec_jsonl(raw, exit_code=0)
    assert out.ok is True
    assert out.malformed_lines == 1


def test_parser_missing_thread_id_stays_none() -> None:
    raw = "\n".join(
        [
            json.dumps({"msg": {"type": "agent_message", "message": "x"}}),
            json.dumps({"msg": {"type": "turn.completed", "usage": {}}}),
        ]
    )
    out = parse_exec_jsonl(raw, exit_code=0)
    assert out.session_id is None
    assert out.ok is True  # the turn completed; only the id is absent


def test_parser_event_retention_is_bounded() -> None:
    lines = [json.dumps({"msg": {"type": "item.started", "i": i}}) for i in range(2000)]
    out = parse_exec_jsonl("\n".join(lines), exit_code=None)
    assert len(out.events) <= 400


def test_parser_handles_the_live_0154_protocol_shape() -> None:
    """Pin the exact top-level record shapes observed in the live 0.154.0 run.

    Evidence: ``codex exec --json`` emits TOP-LEVEL records (no ``msg``
    wrapper): ``{"type":"thread.started","thread_id":...}``,
    ``{"type":"item.completed","item":{"type":"agent_message","text":...}}``
    and ``{"type":"turn.completed","usage":{...}}``.  These shapes drove the
    real Session 006 live proof; this test keeps the parser honest against
    regressions.
    """
    raw = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "01a0cb24-live"}),
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": "item_1", "type": "agent_message", "text": MARKER},
                }
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 220272,
                        "cached_input_tokens": 192512,
                        "output_tokens": 3158,
                        "reasoning_output_tokens": 959,
                    },
                }
            ),
        ]
    )
    out = parse_exec_jsonl(raw, exit_code=0)
    assert out.session_id == "01a0cb24-live"
    assert out.final_text == MARKER
    assert out.ok is True
    assert out.input_tokens == 220272
    assert out.cached_input_tokens == 192512
    assert out.output_tokens == 3158


# -- driver lifecycle ------------------------------------------------------------
def _ok_result(argv):
    return _runner_result(argv, exit_code=0, stdout=_codex_jsonl())


def test_start_session_prepares_a_handle_without_launching(ws) -> None:
    runner = ScriptedCodexRunner([])
    driver = CodexDriver(runner=runner)
    session = driver.start_session(_request(workspace_path=ws))
    assert session.session_id is None, "no model call: the id arrives only after a prompt"
    assert session.external is False
    assert runner.specs == [], "start_session must launch nothing"
    assert driver.get_session_id() is None


def test_start_session_requires_the_cli_and_a_real_workspace(tmp_path, ws) -> None:
    class NoCli(CodexDriver):
        @classmethod
        def resolve_executable(cls):
            return None

    with pytest.raises(DriverError):
        NoCli(runner=ScriptedCodexRunner([])).start_session(_request(workspace_path=ws))
    with pytest.raises(DriverError):
        CodexDriver(runner=ScriptedCodexRunner([])).start_session(
            _request(workspace_path=str(tmp_path / "missing"))
        )


def test_start_session_refuses_an_unsafe_sandbox_from_extra(ws) -> None:
    with pytest.raises((DriverError, CodexCliError)):
        CodexDriver(runner=ScriptedCodexRunner([])).start_session(
            _request(workspace_path=ws, extra={"sandbox": "danger-full-access"})
        )
    with pytest.raises(CodexCliError):
        _validated_sandbox("danger-full-access")


def test_fresh_prompt_runs_codex_and_attaches_the_real_thread_id(ws) -> None:
    runner = ScriptedCodexRunner([_ok_result([])])
    driver = CodexDriver(runner=runner)
    session = driver.start_session(_request(workspace_path=ws))
    handle = driver.send_prompt(session, "Reply exactly: " + MARKER)
    result = driver.wait_for_completion(handle, timeout_s=60)

    assert result.ok is True
    assert result.exit_code == 0
    assert result.session_id == "019d1123-1111-2222-3333-444455556666"
    assert result.text == MARKER
    assert session.session_id == result.session_id
    assert session.external is True
    assert driver.get_session_id() == result.session_id

    assert len(runner.specs) == 1
    spec = runner.specs[0]
    assert spec.argv_list()[-1] == "-"
    assert spec.argv_list()[1:3] == ["exec", "--json"]
    assert spec.stdin_text == "Reply exactly: " + MARKER, "prompt travels on stdin"
    assert spec.cwd is not None


def test_resume_prompt_reuses_the_bound_session_id(
    monkeypatch: pytest.MonkeyPatch, ws
) -> None:
    # Offline proof of the resume CODE PATH: the live gate is simulated as
    # already verified (the live proof itself happens once, in the smoke).
    monkeypatch.setattr("encomm_pcc.drivers.codex._LIVE_RESUME_VERIFIED", True)
    runner = ScriptedCodexRunner([_ok_result([])])
    driver = CodexDriver(runner=runner)
    session = driver.resume_session("019d1123-1111-2222-3333-444455556666", _request(workspace_path=ws))
    assert session.session_id == "019d1123-1111-2222-3333-444455556666"
    assert session.metadata.get("resumed") is True

    handle = driver.send_prompt(session, "continue")
    driver.wait_for_completion(handle, timeout_s=60)
    argv = runner.specs[0].argv_list()
    assert argv[1:4] == ["exec", "resume", "019d1123-1111-2222-3333-444455556666"]
    assert "--json" in argv
    assert argv[-1] == "-"


def test_resume_is_refused_until_live_verified(monkeypatch: pytest.MonkeyPatch, ws) -> None:
    monkeypatch.setattr("encomm_pcc.drivers.codex._LIVE_RESUME_VERIFIED", False)
    driver = CodexDriver(runner=ScriptedCodexRunner([]))
    with pytest.raises(Exception) as excinfo:
        driver.resume_session("019d", _request(workspace_path=ws))
    assert "not verified" in str(excinfo.value)


def test_driver_failure_propagation_nonzero_timeout_and_empty_prompt(ws) -> None:
    # Non-zero exit with a completed turn is still a failure.
    runner = ScriptedCodexRunner([_runner_result([], exit_code=2, stdout=_codex_jsonl())])
    driver = CodexDriver(runner=runner)
    result = driver.wait_for_completion(driver.send_prompt(driver.start_session(_request(workspace_path=ws)), "x"), 30)
    assert result.ok is False

    # Timeout is data: ok=False, timed_out recorded, child tree killed by the
    # real runner (the fake simply reports it).
    runner = ScriptedCodexRunner([_runner_result([], exit_code=0, timed_out=True, stdout=_codex_jsonl())])
    driver = CodexDriver(runner=runner)
    result = driver.wait_for_completion(driver.send_prompt(driver.start_session(_request(workspace_path=ws)), "x"), 30)
    assert result.ok is False
    assert result.metadata.get("timed_out") is True

    # An empty prompt is refused before any process starts.
    driver = CodexDriver(runner=ScriptedCodexRunner([]))
    with pytest.raises(DriverError):
        driver.send_prompt(driver.start_session(_request(workspace_path=ws)), "   ")


def test_codex_driver_defaults_to_the_non_executing_runner(ws) -> None:
    driver = CodexDriver()
    assert isinstance(driver._runner, NullProcessRunner)  # noqa: SL001
    with pytest.raises(RuntimeError, match="process execution is disabled"):
        driver.wait_for_completion(driver.send_prompt(driver.start_session(_request(workspace_path=ws)), "x"), 30)


def test_capabilities_are_evidence_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("encomm_pcc.drivers.codex._LIVE_SMOKE_VERIFIED", False)
    monkeypatch.setattr("encomm_pcc.drivers.codex._LIVE_RESUME_VERIFIED", False)
    assert CodexDriver.capabilities().implemented is False
    assert CodexDriver.capabilities().supports_resume is False
    monkeypatch.setattr("encomm_pcc.drivers.codex._LIVE_SMOKE_VERIFIED", True)
    monkeypatch.setattr("encomm_pcc.drivers.codex._LIVE_RESUME_VERIFIED", True)
    assert CodexDriver.capabilities().implemented is True
    assert CodexDriver.capabilities().supports_resume is True
    # Unchanged production flags (unverified at import time by definition).
    assert _LIVE_SMOKE_VERIFIED in (True, False)
    assert _LIVE_RESUME_VERIFIED in (True, False)


def test_discover_sessions_is_exposed_through_the_generic_protocol() -> None:
    from encomm_pcc.drivers.session_discovery import SessionDiscoverer

    driver = CodexDriver()
    assert isinstance(driver, SessionDiscoverer)
    result = driver.discover_sessions(limit=1)
    assert isinstance(result.ok, bool)
