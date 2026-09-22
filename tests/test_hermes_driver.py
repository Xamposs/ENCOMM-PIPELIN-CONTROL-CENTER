"""HermesDriver: the real adapter's behaviour, proven without a network.

Every test here drives the driver through a :class:`FakeProcessRunner`, so the
mapping from a real child process outcome (exit code, stream-json records,
stderr) onto :class:`PromptResult` is verified deterministically.  The one real
engine run lives in ``scripts/session_002_smoke.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import FakeProcessRunner
from encomm_pcc.domain import AgentRole, SessionPolicy
from encomm_pcc.drivers import (
    DriverError,
    HermesDriver,
    SessionRequest,
)
from encomm_pcc.drivers import hermes as hermes_module


@pytest.fixture()
def fake_cli(monkeypatch: pytest.MonkeyPatch) -> str:
    """Pretend the Hermes launcher exists, so no test depends on this host."""
    path = "C:/fake/hermes.exe"
    monkeypatch.setattr(
        HermesDriver, "resolve_executable", classmethod(lambda cls: path)
    )
    return path


@pytest.fixture()
def workspace(tmp_path: Path) -> str:
    return str(tmp_path)


def request_for(workspace: str, **overrides) -> SessionRequest:
    values = {
        "role": AgentRole.BUILDER,
        "workspace_path": workspace,
        "project_profile": "encomm-pipeline-control-center",
        "provider": "",
        "model": "",
        "session_policy": SessionPolicy.ALWAYS_NEW,
    }
    values.update(overrides)
    return SessionRequest(**values)


# -- capabilities ---------------------------------------------------------------
def test_capabilities_are_honest_and_conservative() -> None:
    caps = HermesDriver.capabilities()
    assert caps.supports_sessions is True
    # Mid-prompt cancellation is not implemented: the flag must not promise it.
    assert caps.supports_cancellation is False
    assert HermesDriver(runner=FakeProcessRunner()).cancel() is False
    # Streaming deltas are consumed but not surfaced to callers.
    assert caps.supports_streaming is False
    # Resume and `implemented` mirror their live-verification flags exactly:
    # both were proven by a real run (see the SESSION_002 report).
    assert caps.supports_resume is hermes_module._LIVE_RESUME_VERIFIED
    assert caps.implemented is hermes_module._LIVE_SMOKE_VERIFIED


def test_resume_passes_the_session_id_to_the_cli(fake_cli: str, workspace: str) -> None:
    """The resume path is real: it addresses the session through ``--resume``."""
    driver = HermesDriver(runner=FakeProcessRunner())
    session = driver.resume_session("20260922_172437_722edb", request_for(workspace))
    assert session.session_id == "20260922_172437_722edb"
    assert session.external is True
    assert session.metadata["resumed"] is True


def test_resume_needs_an_id(fake_cli: str, workspace: str) -> None:
    driver = HermesDriver(runner=FakeProcessRunner())
    with pytest.raises(DriverError, match="requires a session id"):
        driver.resume_session("", request_for(workspace))


def test_a_resumed_run_passes_the_resume_flag_to_the_cli(
    fake_cli: str, workspace: str, stream_json_text, process_result  # noqa: ANN001
) -> None:
    runner = FakeProcessRunner(
        results=[process_result(stdout=stream_json_text(session_id="20260922_172437_722edb"))]
    )
    driver = HermesDriver(runner=runner)
    session = driver.resume_session("20260922_172437_722edb", request_for(workspace))
    handle = driver.send_prompt(session, "continue")
    result = driver.wait_for_completion(handle, timeout_s=30.0)

    argv = runner.specs[0].argv_list()
    assert argv[argv.index("--resume") + 1] == "20260922_172437_722edb"
    assert result.session_id == "20260922_172437_722edb"
    # A fresh session must NOT carry --resume.
    runner2 = FakeProcessRunner(results=[process_result(stdout=stream_json_text())])
    driver2 = HermesDriver(runner=runner2)
    fresh = driver2.start_session(request_for(workspace))
    driver2.wait_for_completion(driver2.send_prompt(fresh, "hello"), timeout_s=30.0)
    assert "--resume" not in runner2.specs[0].argv_list()


# -- session lifecycle ----------------------------------------------------------
def test_start_session_is_fresh_and_has_no_fabricated_id(fake_cli: str, workspace: str) -> None:
    runner = FakeProcessRunner()
    driver = HermesDriver(runner=runner)
    session = driver.start_session(request_for(workspace))

    assert session.session_id is None, "no id may exist before a prompt has run"
    assert session.external is False
    assert session.persistent is False
    assert session.metadata["profile"] == "encomm-pipeline-control-center"
    assert session.metadata["workspace_path"] == workspace
    assert runner.call_count == 0, "start_session must not launch anything"
    assert driver.get_session_id() is None


def test_start_session_requires_a_real_workspace(fake_cli: str, tmp_path: Path) -> None:
    driver = HermesDriver(runner=FakeProcessRunner())
    missing = str(tmp_path / "does-not-exist")
    with pytest.raises(DriverError, match="Workspace path does not exist"):
        driver.start_session(request_for(missing))


def test_start_session_requires_a_cli(monkeypatch: pytest.MonkeyPatch, workspace: str) -> None:
    monkeypatch.setattr(HermesDriver, "resolve_executable", classmethod(lambda cls: None))
    driver = HermesDriver(runner=FakeProcessRunner())
    with pytest.raises(DriverError, match="not found on PATH"):
        driver.start_session(request_for(workspace))


def test_empty_prompt_is_refused(fake_cli: str, workspace: str) -> None:
    driver = HermesDriver(runner=FakeProcessRunner())
    session = driver.start_session(request_for(workspace))
    with pytest.raises(DriverError, match="empty prompt"):
        driver.send_prompt(session, "   ")


# -- prompt execution -----------------------------------------------------------
def _run_prompt(runner: FakeProcessRunner, workspace: str, **kwargs):  # noqa: ANN201
    driver = HermesDriver(runner=runner)
    session = driver.start_session(request_for(workspace, **kwargs))
    handle = driver.send_prompt(session, "Return exactly: ENCOMM_PCC_HERMES_SMOKE_OK")
    return driver, session, driver.wait_for_completion(handle, timeout_s=30.0)


def test_successful_prompt_maps_the_real_process_outcome(
    fake_cli: str, workspace: str, stream_json_text, process_result  # noqa: ANN001
) -> None:
    runner = FakeProcessRunner(
        results=[
            process_result(
                exit_code=0,
                stdout=stream_json_text(session_id="20260922_150000_abcdef"),
                stderr="\nsession_id: 20260922_150000_abcdef\n",
            )
        ]
    )
    driver, session, result = _run_prompt(runner, workspace)

    assert result.ok is True
    assert result.text == "ENCOMM_PCC_HERMES_SMOKE_OK"
    assert result.exit_code == 0
    assert result.session_id == "20260922_150000_abcdef"
    assert result.simulated is False, "a real run is never marked simulated"
    assert result.error is None
    assert result.metadata["stream"]["saw_result"] is True
    # the handle now carries the real, engine-reported id
    assert session.session_id == "20260922_150000_abcdef"
    assert session.external is True
    assert driver.get_session_id() == "20260922_150000_abcdef"


def test_the_process_is_started_with_the_verified_argv(
    fake_cli: str, workspace: str, stream_json_text, process_result  # noqa: ANN001
) -> None:
    runner = FakeProcessRunner(results=[process_result(stdout=stream_json_text())])
    _run_prompt(runner, workspace, model="deepseek/deepseek-v4.1-flash", provider="openrouter")

    assert runner.call_count == 1
    spec = runner.specs[0]
    argv = spec.argv_list()
    assert argv[0] == "C:/fake/hermes.exe"
    assert argv[1:3] == ["-p", "encomm-pipeline-control-center"]
    assert "chat" in argv and "--oneshot" in argv
    assert argv[argv.index("--format") + 1] == "stream-json"
    assert argv[argv.index("-m") + 1] == "deepseek/deepseek-v4.1-flash"
    assert argv[argv.index("--provider") + 1] == "openrouter"
    # cwd is pinned to the workspace, and the prompt is not in the argv at all
    assert str(spec.cwd) == workspace
    assert all("ENCOMM_PCC" not in a for a in argv)
    # the environment handed to the child carries no supervisor session state
    assert not [k for k in spec.env if k.startswith("HERMES_")]


def test_non_zero_exit_is_a_failure_with_the_child_own_words(
    fake_cli: str, workspace: str, stream_json_text, process_result  # noqa: ANN001
) -> None:
    runner = FakeProcessRunner(
        results=[
            process_result(
                exit_code=2,
                stdout=stream_json_text(exit_code=2, text="partial"),
                stderr="provider error: boom\n",
            )
        ]
    )
    _, _, result = _run_prompt(runner, workspace)

    assert result.ok is False
    assert result.exit_code == 2
    assert result.error and "boom" in result.error
    assert result.simulated is False


def test_a_stream_without_a_result_record_cannot_be_success(
    fake_cli: str, workspace: str, process_result  # noqa: ANN001
) -> None:
    runner = FakeProcessRunner(
        results=[process_result(exit_code=0, stdout='{"type":"system","subtype":"init"}\n')]
    )
    _, _, result = _run_prompt(runner, workspace)

    assert result.ok is False
    assert "no terminal 'result' record" in (result.error or "")


def test_timeout_is_reported_as_failure_not_success(
    fake_cli: str, workspace: str, process_result  # noqa: ANN001
) -> None:
    runner = FakeProcessRunner(
        results=[process_result(exit_code=-1, stdout="", stderr="", timed_out=True)]
    )
    _, _, result = _run_prompt(runner, workspace)

    assert result.ok is False
    assert result.metadata["timed_out"] is True
    assert "did not finish" in (result.error or "")


def test_session_id_falls_back_to_the_cli_stderr_line(
    fake_cli: str, workspace: str, stream_json_text, process_result  # noqa: ANN001
) -> None:
    """The id is taken verbatim from the child's output — never generated."""
    stream = stream_json_text(session_id="")
    runner = FakeProcessRunner(
        results=[
            process_result(
                stdout=stream,
                stderr="\nsession_id: 20260922_160000_zzzzzz\n",
            )
        ]
    )
    _, _, result = _run_prompt(runner, workspace)
    assert result.session_id == "20260922_160000_zzzzzz"


def test_no_session_id_anywhere_stays_none(
    fake_cli: str, workspace: str, process_result  # noqa: ANN001
) -> None:
    stdout = (
        '{"type":"result","exit_code":0,"text":"ENCOMM_PCC_HERMES_SMOKE_OK",'
        '"tokens":{"total":1}}\n'
    )
    runner = FakeProcessRunner(results=[process_result(stdout=stdout, stderr="noise\n")])
    driver, session, result = _run_prompt(runner, workspace)

    assert result.ok is True
    assert result.session_id is None
    assert session.session_id is None
    assert driver.get_session_id() is None


def test_extra_args_cannot_smuggle_an_approval_bypass(fake_cli: str, workspace: str) -> None:
    driver = HermesDriver(runner=FakeProcessRunner())
    request = request_for(workspace)
    request.extra = {"extra_args": ["--yolo"]}
    with pytest.raises(DriverError, match="Refusing to pass"):
        driver.start_session(request)


def test_last_result_is_retained_for_introspection(
    fake_cli: str, workspace: str, stream_json_text, process_result  # noqa: ANN001
) -> None:
    runner = FakeProcessRunner(results=[process_result(stdout=stream_json_text())])
    driver, _, result = _run_prompt(runner, workspace)
    assert driver.get_result() is result