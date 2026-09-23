"""Session 007 — the Generic CLI driver matrix.

Covers the brief's §22 test matrix offline: configuration validation, the
process contract through the REAL ``SubprocessRunner`` (deterministic
``python -c`` child processes — zero AI model calls), the security contract,
and the driver/SessionManager integration.

Test style notes:
* Config-validation cases construct :class:`GenericCliConfig` directly (the
  constructor is the single validation authority).
* Process cases run ``python -c`` snippets so stdout/exit/timeout behaviour is
  real but deterministic and free.
"""

from __future__ import annotations

import json
import sys

import pytest

from encomm_pcc.core.session_manager import SessionAction, decide_session_action
from encomm_pcc.domain import AgentRole, SessionPolicy
from encomm_pcc.drivers import (
    DriverRegistry,
    GenericCliConfig,
    GenericCliConfigError,
    GenericCliDriver,
    NullProcessRunner,
    SessionRequest,
    SubprocessRunner,
)
from encomm_pcc.drivers.generic_cli import DEFAULT_PROMPT_TIMEOUT_S
from encomm_pcc.drivers.generic_cli_config import (
    MAX_ARGV_TOKENS,
    MAX_ENV_OVERRIDES,
    build_argv,
    child_environment,
    extract_result_text,
)

PYTHON = sys.executable or "python"


# -- helpers --------------------------------------------------------------------
def make_config(**overrides) -> GenericCliConfig:
    base = {"executable": PYTHON, "args": ["-c", "print('ok')"]}
    base.update(overrides)
    return GenericCliConfig.from_mapping(base)


def make_request(workspace, config: GenericCliConfig, **kw) -> SessionRequest:
    request = SessionRequest(
        role=kw.pop("role", AgentRole.BUILDER),
        workspace_path=str(workspace),
        session_policy=SessionPolicy.ALWAYS_NEW,
        **kw,
    )
    request.extra["generic_cli"] = config.to_dict()
    return request


@pytest.fixture()
def driver() -> GenericCliDriver:
    return GenericCliDriver(runner=SubprocessRunner())


@pytest.fixture()
def registry() -> DriverRegistry:
    reg = DriverRegistry(runner=NullProcessRunner())
    reg.register_all((GenericCliDriver,))
    return reg


# -- configuration validation ------------------------------------------------------
class TestGenericCliConfigValidation:
    def test_minimal_valid_config_round_trips(self):
        config = make_config()
        assert config.executable == PYTHON
        assert config.prompt_transport == "stdin"
        assert config.result_mode == "stdout_text"
        assert config.timeout_s == 900.0
        assert GenericCliConfig.from_mapping(config.to_dict()) == config

    def test_missing_executable_is_rejected(self):
        with pytest.raises(GenericCliConfigError, match="executable is empty"):
            GenericCliConfig.from_mapping({"args": ["run"]})

    def test_missing_configuration_is_rejected_with_guidance(self):
        with pytest.raises(GenericCliConfigError, match="No Generic CLI configuration"):
            GenericCliConfig.from_mapping(None)

    def test_shell_command_string_as_args_is_rejected(self):
        # A single string is exactly the "arbitrary shell command" shape the
        # brief forbids; it must never be silently split or executed.
        with pytest.raises(GenericCliConfigError, match="not a single string"):
            make_config(args="run --json && rm -rf /")

    def test_shell_metacharacters_stay_literal_argv_tokens(self):
        # Metacharacters inside tokens are allowed but remain LITERAL tokens —
        # build_argv must never interpret them.
        config = make_config(args=["echo", "a&&b|c;d"])
        argv = build_argv(config, workspace="C:/ws")
        assert argv[2] == "a&&b|c;d"

    def test_unknown_placeholder_fails_closed(self):
        with pytest.raises(GenericCliConfigError, match="Unknown placeholder"):
            make_config(args=["run", "{session_id}"])

    def test_unknown_placeholder_in_model_args_fails_closed(self):
        with pytest.raises(GenericCliConfigError, match="Unknown placeholder"):
            make_config(model_args=("--model", "{model_name}"))

    def test_braces_inside_a_token_stay_literal(self):
        # Python/JSON-ish arguments legitimately contain braces; only a token
        # that IS a placeholder is treated as one.
        config = make_config(args=["-c", "print({'answer': 'STRUCTURED'})"])
        argv = build_argv(config, workspace="C:/ws")
        assert argv[-1] == "print({'answer': 'STRUCTURED'})"

    def test_duplicate_placeholder_fails_closed(self):
        with pytest.raises(GenericCliConfigError, match="more than once"):
            make_config(args=["{workspace}", "--also", "{workspace}"])

    def test_invalid_prompt_transport_fails_closed(self):
        with pytest.raises(GenericCliConfigError, match="prompt_transport"):
            make_config(prompt_transport="carrier_pigeon")

    def test_invalid_result_mode_fails_closed(self):
        with pytest.raises(GenericCliConfigError, match="result_mode"):
            make_config(result_mode="yaml")

    def test_json_result_mode_requires_result_field(self):
        with pytest.raises(GenericCliConfigError, match="result_field"):
            make_config(result_mode="json")

    def test_prompt_file_placeholder_requires_temp_transport(self):
        with pytest.raises(GenericCliConfigError, match="temporary_file"):
            make_config(args=["run", "{prompt_file}"])

    def test_temp_transport_requires_prompt_file_placeholder(self):
        with pytest.raises(GenericCliConfigError, match="requires the"):
            make_config(prompt_transport="temporary_file")

    def test_bad_timeout_too_small(self):
        with pytest.raises(GenericCliConfigError, match="timeout"):
            make_config(timeout_s=0.01)

    def test_bad_timeout_not_a_number(self):
        with pytest.raises(GenericCliConfigError, match="not a number"):
            make_config(timeout_s="forever")

    def test_oversized_configuration_fails_closed(self):
        with pytest.raises(GenericCliConfigError, match="exceed the bound"):
            make_config(args=[f"arg{i}" for i in range(MAX_ARGV_TOKENS + 1)])

    def test_oversized_env_overrides_fail_closed(self):
        with pytest.raises(GenericCliConfigError, match="bound of"):
            make_config(
                env_overrides={f"VAR_{i}": "x" for i in range(MAX_ENV_OVERRIDES + 1)}
            )

    def test_invalid_env_key_fails_closed(self):
        with pytest.raises(GenericCliConfigError, match="not a valid environment"):
            make_config(env_overrides={"BAD KEY!": "x"})

    def test_unknown_config_keys_fail_closed(self):
        with pytest.raises(GenericCliConfigError, match="Unknown Generic CLI"):
            GenericCliConfig.from_mapping(
                {"executable": "x", "shell_command": "rm -rf /"}
            )

    def test_model_args_with_model_placeholder(self):
        config = make_config(model_args=("--model", "{model}"))
        argv = build_argv(config, workspace="C:/ws", model="qwen3")
        assert argv[-2:] == ["--model", "qwen3"]

    def test_model_placeholder_without_model_fails_closed(self):
        config = make_config(model_args=("--model", "{model}"))
        with pytest.raises(GenericCliConfigError, match="no model is configured"):
            build_argv(config, workspace="C:/ws", model="")

    def test_workspace_placeholder_substitutes(self):
        config = make_config(args=["--cwd", "{workspace}", "run"])
        argv = build_argv(config, workspace="C:/repo")
        assert "--cwd" in argv and "C:/repo" in argv


# -- result modes -----------------------------------------------------------------
class TestGenericCliResultModes:
    def test_stdout_text_is_verbatim(self):
        assert (
            extract_result_text(result_mode="stdout_text", stdout="answer\n", result_field="")
            == "answer\n"
        )

    def test_json_extracts_field(self):
        assert (
            extract_result_text(
                result_mode="json",
                stdout=json.dumps({"answer": "hello", "other": 1}),
                result_field="answer",
            )
            == "hello"
        )

    def test_json_dotted_field(self):
        payload = json.dumps({"result": {"message": {"text": "deep"}}})
        assert (
            extract_result_text(
                result_mode="json", stdout=payload, result_field="result.message.text"
            )
            == "deep"
        )

    def test_json_malformed_fails_closed(self):
        with pytest.raises(GenericCliConfigError, match="parsing failed"):
            extract_result_text(result_mode="json", stdout="{not json", result_field="a")

    def test_json_non_object_fails_closed(self):
        with pytest.raises(GenericCliConfigError, match="JSON object"):
            extract_result_text(result_mode="json", stdout="[1,2,3]", result_field="a")

    def test_json_missing_field_fails_closed(self):
        with pytest.raises(GenericCliConfigError, match="was not found"):
            extract_result_text(
                result_mode="json", stdout='{"other": 1}', result_field="answer"
            )

    def test_jsonl_last_record_wins(self):
        lines = [
            json.dumps({"text": "first"}),
            json.dumps({"text": "second"}),
            "not json at all",
            json.dumps({"text": "final"}),
        ]
        assert (
            extract_result_text(
                result_mode="jsonl", stdout="\n".join(lines), result_field="text"
            )
            == "final"
        )

    def test_jsonl_no_parseable_records_fails_closed(self):
        with pytest.raises(GenericCliConfigError, match="no parseable JSON"):
            extract_result_text(result_mode="jsonl", stdout="garbage", result_field="t")

    def test_jsonl_field_missing_everywhere_fails_closed(self):
        with pytest.raises(GenericCliConfigError, match="carrying the field"):
            extract_result_text(
                result_mode="jsonl",
                stdout=json.dumps({"other": "x"}),
                result_field="text",
            )


# -- process contract through the REAL SubprocessRunner ------------------------------
class TestGenericCliProcess:
    def test_stdout_success_contract(self, driver, tmp_path):
        config = make_config(args=["-c", "print('ENCOMM_GENERIC_OK')"])
        driver._runner = SubprocessRunner()
        session = driver.start_session(make_request(tmp_path, config))
        result = driver.wait_for_completion(driver.send_prompt(session, "hi"))
        assert result.ok is True
        assert result.text.strip() == "ENCOMM_GENERIC_OK"
        assert result.exit_code == 0
        assert result.simulated is False
        assert result.session_id is None
        assert result.duration_s > 0

    def test_prompt_travels_on_stdin(self, driver, tmp_path):
        config = make_config(
            args=["-c", "import sys; sys.stdout.write(sys.stdin.read().strip())"]
        )
        session = driver.start_session(make_request(tmp_path, config))
        result = driver.wait_for_completion(driver.send_prompt(session, "ECHO_ME"))
        assert result.ok is True
        assert result.text == "ECHO_ME"

    def test_prompt_travels_via_temp_file_and_is_cleaned(self, driver, tmp_path):
        config = make_config(
            args=[
                "-c",
                "import sys; sys.stdout.write(open(sys.argv[1], encoding='utf-8').read())",
                "{prompt_file}",
            ],
            prompt_transport="temporary_file",
        )
        session = driver.start_session(make_request(tmp_path, config))
        argv = build_argv(config, workspace=str(tmp_path), prompt_file="PLACEHOLDER")
        assert argv[-1] == "PLACEHOLDER"
        result = driver.wait_for_completion(driver.send_prompt(session, "FILE_PROMPT"))
        assert result.ok is True
        assert "FILE_PROMPT" in result.text
        assert result.metadata["prompt_transport"] == "temporary_file"

    def test_nonzero_exit_is_an_honest_failure(self, driver, tmp_path):
        config = make_config(args=["-c", "print('partial'); raise SystemExit(2)"])
        session = driver.start_session(make_request(tmp_path, config))
        result = driver.wait_for_completion(driver.send_prompt(session, "hi"))
        assert result.ok is False
        assert result.exit_code == 2
        assert "exited with code 2" in result.error
        assert result.text.strip() == "partial"  # captured, but NOT a success

    def test_timeout_kills_and_fails(self, driver, tmp_path):
        config = make_config(
            args=["-c", "import time; time.sleep(30)"], timeout_s=2.0
        )
        session = driver.start_session(make_request(tmp_path, config))
        result = driver.wait_for_completion(driver.send_prompt(session, "hi"))
        assert result.ok is False
        assert result.metadata["timed_out"] is True
        assert "did not finish within 2s" in result.error

    def test_empty_stdout_on_exit_zero_fails(self, driver, tmp_path):
        config = make_config(args=["-c", "pass"])
        session = driver.start_session(make_request(tmp_path, config))
        result = driver.wait_for_completion(driver.send_prompt(session, "hi"))
        assert result.ok is False
        assert "no output" in result.error

    def test_stderr_is_captured_as_bounded_diagnostics(self, driver, tmp_path):
        config = make_config(
            args=["-c", "import sys; sys.stderr.write('E: bad input' * 500); raise SystemExit(3)"]
        )
        session = driver.start_session(make_request(tmp_path, config))
        result = driver.wait_for_completion(driver.send_prompt(session, "hi"))
        assert result.ok is False
        excerpt = result.metadata["stderr_excerpt"]
        assert "E: bad input" in excerpt
        assert len(excerpt) <= 2100  # bounded

    def test_json_result_mode_against_a_real_child(self, driver, tmp_path):
        config = make_config(
            args=[
                "-c",
                "import json,sys; json.dump({'answer': 'STRUCTURED', 'n': 1}, sys.stdout)",
            ],
            result_mode="json",
            result_field="answer",
        )
        session = driver.start_session(make_request(tmp_path, config))
        result = driver.wait_for_completion(driver.send_prompt(session, "hi"))
        assert result.ok is True
        assert result.text == "STRUCTURED"

    def test_json_mode_with_malformed_child_output_fails(self, driver, tmp_path):
        config = make_config(
            args=["-c", "print('just text')"], result_mode="json", result_field="answer"
        )
        session = driver.start_session(make_request(tmp_path, config))
        result = driver.wait_for_completion(driver.send_prompt(session, "hi"))
        assert result.ok is False
        assert "result mode" in result.error

    def test_model_args_reach_the_child(self, driver, tmp_path):
        config = make_config(
            args=["-c", "import sys; print('model=' + sys.argv[1])"],
            model_args=("{model}",),
        )
        session = driver.start_session(make_request(tmp_path, config))
        session.metadata["model"] = "qwen3"
        result = driver.wait_for_completion(driver.send_prompt(session, "hi"))
        assert result.ok is True
        assert result.text.strip() == "model=qwen3"

    def test_cwd_is_the_workspace(self, driver, tmp_path):
        config = make_config(args=["-c", "import os; print(os.getcwd())"])
        session = driver.start_session(make_request(tmp_path, config))
        result = driver.wait_for_completion(driver.send_prompt(session, "hi"))
        assert result.ok is True
        assert str(tmp_path) in result.text

    def test_env_overrides_reach_the_child(self, driver, tmp_path):
        config = make_config(
            args=["-c", "import os; print(os.environ.get('ENCOMM_TEST_VAR', 'missing'))"],
            env_overrides={"ENCOMM_TEST_VAR": "visible"},
        )
        session = driver.start_session(make_request(tmp_path, config))
        result = driver.wait_for_completion(driver.send_prompt(session, "hi"))
        assert result.ok is True
        assert result.text.strip() == "visible"

    def test_empty_prompt_is_refused(self, driver, tmp_path):
        config = make_config()
        session = driver.start_session(make_request(tmp_path, config))
        with pytest.raises(Exception, match="empty prompt"):
            driver.send_prompt(session, "   ")


# -- security contract ---------------------------------------------------------------
class TestGenericCliSecurity:
    def test_spec_is_argv_only_with_explicit_cwd(self, driver, tmp_path):
        recorded = []
        from encomm_pcc.drivers import ProcessResult, ProcessSpec

        class SpyRunner:
            def run(self, spec: ProcessSpec) -> ProcessResult:
                recorded.append(spec)
                return ProcessResult(argv=spec.argv_list(), exit_code=0, stdout="ok")

        spy = SpyRunner()
        driver._runner = spy
        config = make_config(args=["run", "a&&b", "|rm", ";x"])
        session = driver.start_session(make_request(tmp_path, config))
        result = driver.wait_for_completion(driver.send_prompt(session, "p"))
        assert result.ok is True
        spec = recorded[0]
        assert spec.argv_list()[0] == PYTHON
        assert "a&&b" in spec.argv_list()  # literal token
        assert spec.cwd is not None and str(spec.cwd) == str(tmp_path)
        assert spec.timeout_s == 900.0

    def test_child_environment_is_filtered_and_overrides_applied(self):
        env = child_environment(
            {"MY_VAR": "v"},
            environ={
                "PATH": "C:/Windows",
                "HERMES_SESSION_ID": "supervisor",
                "ENCOMM_PCC_DATA_DIR": "elsewhere",
                "PYTHONPATH": "C:/bad",
                "SYSTEMROOT": "C:/Windows",
            },
        )
        assert env["MY_VAR"] == "v"
        assert "HERMES_SESSION_ID" not in env
        assert "ENCOMM_PCC_DATA_DIR" not in env
        assert "PYTHONPATH" not in env
        assert env["PATH"] == "C:/Windows"

    def test_env_is_never_logged_in_metadata(self, driver, tmp_path):
        config = make_config(env_overrides={"SECRET_VALUE_XYZ": "s3cr3t"})
        session = driver.start_session(make_request(tmp_path, config))
        result = driver.wait_for_completion(driver.send_prompt(session, "hi"))
        assert result.ok is True
        flat = json.dumps(result.metadata)
        assert "s3cr3t" not in flat
        assert "SECRET_VALUE_XYZ" not in flat

    def test_null_runner_default_cannot_execute(self, tmp_path):
        driver = GenericCliDriver()  # no runner injected
        config = make_config()
        session = driver.start_session(make_request(tmp_path, config))
        handle = driver.send_prompt(session, "p")
        with pytest.raises(RuntimeError, match="process execution is disabled"):
            driver.wait_for_completion(handle)
        assert isinstance(driver._runner, NullProcessRunner)  # noqa: SLF001

    def test_prompt_file_is_private_and_utf8(self, driver, tmp_path):
        text = "unicode prompt: Ελληνικά 中文 🚀"
        path = driver._write_prompt_file(text)  # noqa: SLF001
        try:
            assert path.exists()
            assert path.read_text(encoding="utf-8") == text
        finally:
            driver._cleanup_prompt_file(path)
        assert not path.exists()


# -- driver / SessionManager integration ---------------------------------------------
class TestGenericCliDriverIntegration:
    def test_capabilities_are_honest(self):
        caps = GenericCliDriver.capabilities()
        assert caps.implemented is True
        assert caps.supports_sessions is False
        assert caps.supports_resume is False
        assert caps.supports_streaming is False
        assert caps.supports_cancellation is False
        assert caps.requires_profile is False
        assert caps.is_sessionless is True

    def test_default_timeout_constant_is_bounded(self):
        assert 1.0 <= DEFAULT_PROMPT_TIMEOUT_S <= 7200.0

    def test_session_manager_chooses_none_for_the_stateless_driver(self):
        decision = decide_session_action(
            policy=SessionPolicy.PERSISTENT_PER_BATCH,
            capabilities=GenericCliDriver.capabilities(),
            existing_session_id=None,
            same_batch=True,
        )
        assert decision.action is SessionAction.NONE
        assert "stateless" in decision.reason

    def test_resume_is_refused_loudly(self, tmp_path):
        driver = GenericCliDriver(runner=NullProcessRunner())
        config = make_config()
        with pytest.raises(Exception, match="stateless"):
            driver.resume_session("sess_123", make_request(tmp_path, config))

    def test_registry_path_still_works(self, registry: DriverRegistry, tmp_path):
        driver = registry.create("generic_cli", runner=SubprocessRunner())
        config = make_config(args=["-c", "print('via-registry')"])
        session = driver.start_session(make_request(tmp_path, config))
        result = driver.wait_for_completion(driver.send_prompt(session, "go"))
        assert result.ok is True
        assert result.text.strip() == "via-registry"
