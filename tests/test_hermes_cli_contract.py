"""The verified Hermes CLI contract: argument construction and output parsing.

These tests are pure: they never launch a process, never touch a network and
never speak to a provider.  They pin the contract that was discovered from the
installed Hermes build (v0.21.3) — see ``docs/reports/SESSION_002_HERMES_EXECUTOR.md``.
"""

from __future__ import annotations

import json

import pytest

from encomm_pcc.drivers.hermes_cli import (
    CHILD_ENV_DROP_EXACT,
    CHILD_ENV_DROP_PREFIXES,
    EXIT_AGENT_FAILURE,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_USAGE,
    HermesCliError,
    build_chat_argv,
    child_environment,
    parse_stream_json,
)


# -- argument construction -----------------------------------------------------
def test_default_invocation_shape() -> None:
    argv = build_chat_argv(executable="hermes.exe", query_file="C:/tmp/p.txt")
    assert argv[0] == "hermes.exe"
    assert argv[1:3] == ["chat", "--query-file"]
    assert "C:/tmp/p.txt" in argv
    for flag in ("--oneshot", "--quiet", "--format", "stream-json", "--source"):
        assert flag in argv
    assert argv[argv.index("--format") + 1] == "stream-json"
    assert argv[argv.index("--source") + 1] == "tool"


def test_profile_and_workspace_are_passed_explicitly() -> None:
    argv = build_chat_argv(
        executable="hermes.exe",
        query_file="p.txt",
        profile="encomm-pipeline-control-center",
        workspace="C:/work/repo",
    )
    assert argv[1:3] == ["-p", "encomm-pipeline-control-center"]
    assert argv[argv.index("--in") + 1] == "C:/work/repo"


def test_model_and_provider_are_optional_and_paired() -> None:
    without = build_chat_argv(executable="h", query_file="p")
    assert "-m" not in without and "--provider" not in without

    model_only = build_chat_argv(executable="h", query_file="p", model="m/x")
    assert model_only[model_only.index("-m") + 1] == "m/x"
    assert "--provider" not in model_only

    both = build_chat_argv(executable="h", query_file="p", model="m/x", provider="openrouter")
    assert both[both.index("-m") + 1] == "m/x"
    assert both[both.index("--provider") + 1] == "openrouter"


def test_provider_without_model_is_refused_locally() -> None:
    """The installed CLI exits 2 on this combination; we refuse before spawning."""
    with pytest.raises(HermesCliError, match="provider"):
        build_chat_argv(executable="h", query_file="p", provider="openrouter")


def test_resume_adds_the_flag_only_when_requested() -> None:
    argv = build_chat_argv(executable="h", query_file="p", resume_session_id="2026_x")
    assert argv[argv.index("--resume") + 1] == "2026_x"
    assert "--resume" not in build_chat_argv(executable="h", query_file="p")


def test_prompt_text_never_appears_in_argv() -> None:
    """The prompt travels in --query-file, so quoting/length cannot mangle it."""
    argv = build_chat_argv(executable="h", query_file="C:/tmp/prompt.txt")
    assert all("ENCOMM_PCC" not in arg for arg in argv)


# -- exit-code contract --------------------------------------------------------
def test_exit_codes_match_the_documented_values() -> None:
    assert (EXIT_OK, EXIT_AGENT_FAILURE, EXIT_USAGE, EXIT_INTERRUPTED) == (0, 1, 2, 130)


# -- stream-json parsing -------------------------------------------------------
def test_parse_reads_session_id_text_and_tokens(stream_json_text) -> None:  # noqa: ANN001
    output = parse_stream_json(stream_json_text(session_id="20260922_1", text="hello"))
    assert output.session_id == "20260922_1"
    assert output.text == "hello"
    assert output.saw_init and output.saw_result
    assert output.reported_exit_code == 0
    assert output.tokens["total"] == 15
    assert output.model


def test_parse_reports_a_missing_result_record_honestly() -> None:
    stdout = json.dumps({"type": "system", "subtype": "init", "session_id": "s1"}) + "\n"
    output = parse_stream_json(stdout)
    assert output.saw_result is False
    assert output.text == ""


def test_parse_falls_back_to_streamed_deltas() -> None:
    stdout = "\n".join(
        [
            json.dumps({"type": "text", "text": "abc"}),
            json.dumps({"type": "text", "text": "def"}),
        ]
    )
    output = parse_stream_json(stdout)
    assert output.text == "abcdef"
    assert output.saw_result is False


def test_parse_counts_malformed_lines_without_guessing() -> None:
    stdout = "not json\n" + json.dumps({"type": "result", "text": "ok"}) + "\n"
    output = parse_stream_json(stdout)
    assert output.malformed_lines == 1
    assert output.text == "ok"


def test_parse_counts_tool_use_and_errors() -> None:
    stdout = "\n".join(
        [
            json.dumps({"type": "tool_use", "name": "terminal"}),
            json.dumps({"type": "tool_result", "name": "terminal", "is_error": False}),
            json.dumps({"type": "tool_result", "name": "terminal", "is_error": True}),
        ]
    )
    output = parse_stream_json(stdout)
    assert output.tool_use_count == 1
    assert output.tool_error_count == 1


def test_parse_never_invents_a_session_id() -> None:
    output = parse_stream_json(json.dumps({"type": "result", "text": "ok"}) + "\n")
    assert output.session_id is None


# -- child environment ---------------------------------------------------------
def test_child_environment_strips_supervisor_session_state() -> None:
    environ = {
        "PATH": "/usr/bin",
        "SYSTEMROOT": "C:/Windows",
        "HERMES_SESSION_ID": "20260922_142107_70d7e1",
        "HERMES_KANBAN_TASK": "t_123",
        "HERMES_INFERENCE_MODEL": "some/other-model",
        "PYTHONPATH": "C:/src",
    }
    filtered = child_environment(environ)
    assert filtered["PATH"] == "/usr/bin"
    assert filtered["SYSTEMROOT"] == "C:/Windows"
    for prefix in CHILD_ENV_DROP_PREFIXES:
        assert not [k for k in filtered if k.startswith(prefix)]
    assert not (set(CHILD_ENV_DROP_EXACT) & set(filtered))


def test_child_environment_is_a_copy_not_the_live_environment() -> None:
    environ = {"PATH": "/usr/bin", "HERMES_SESSION_ID": "x"}
    filtered = child_environment(environ)
    filtered["PATH"] = "/changed"
    assert environ["PATH"] == "/usr/bin"