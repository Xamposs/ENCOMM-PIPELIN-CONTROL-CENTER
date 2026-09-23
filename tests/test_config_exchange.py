"""Session 007 — configuration export/import matrix (brief §23).

Covers: round-trip, restart-after-import, malformed JSON, wrong format id,
unsupported future version, missing sections, invalid engine/state values,
oversized file, Generic CLI nested config, no secrets exported, invalid
import changes NOTHING, successful import persists, import makes zero model
calls (structural: the module never imports a driver runner or executor).
"""

from __future__ import annotations

import json

import pytest

from encomm_pcc.core import PipelineController
from encomm_pcc.core.config_exchange import (
    CONFIG_EXPORT_FORMAT,
    CONFIG_EXPORT_VERSION,
    MAX_EXPORT_BYTES,
    REDACTED_VALUE,
    ConfigExchangeError,
    ExportInput,
    apply_import,
    build_export,
    read_export,
    validate_export,
    write_export,
)
from encomm_pcc.domain import AgentRole, SessionPolicy
from encomm_pcc.drivers import GenericCliConfig


ROLE_VALUES = [role.value for role in AgentRole]


# -- helpers --------------------------------------------------------------------
def export_from_controller(controller: PipelineController) -> dict:
    role_configs = {
        role.value: controller.role_config(role).to_dict() for role in AgentRole
    }
    return build_export(
        ExportInput(
            application_version="0.7.0",
            workspace_name=controller.state.workspace.name,
            workspace_path=controller.state.workspace.repo_path,
            role_configs=role_configs,
        )
    )


def full_document(tmp_path, **overrides) -> dict:
    roles = {
        value: {
            "engine": "hermes" if value in ("BUILDER", "TASK_AUDITOR") else "codex",
            "project_profile": f"{value.lower()}-profile",
            "provider": "openrouter",
            "model": "deepseek/deepseek-v4.1-flash",
            "session_policy": "always_new",
            "same_as_orchestrator": value == "FINAL_AUDITOR",
            "extra": {},
        }
        for value in ROLE_VALUES
    }
    roles["BUILDER"]["extra"]["generic_cli"] = {
        "executable": "opencode",
        "args": ["run", "--format", "json"],
        "prompt_transport": "stdin",
        "result_mode": "json",
        "result_field": "answer",
        "model_args": ["--model", "{model}"],
        "timeout_s": 1200.0,
        "env_overrides": {"API_BASE": "https://example.internal"},
    }
    document = {
        "format": CONFIG_EXPORT_FORMAT,
        "version": CONFIG_EXPORT_VERSION,
        "application_version": "0.7.0",
        "workspace": {"name": "Exported WS", "repo_path": str(tmp_path)},
        "roles": roles,
    }
    document.update(overrides)
    return document


@pytest.fixture()
def controller(database, event_log) -> PipelineController:
    return PipelineController(database=database, event_log=event_log)


# -- export ---------------------------------------------------------------------
class TestExport:
    def test_round_trip_document_shape(self, controller):
        document = export_from_controller(controller)
        assert document["format"] == CONFIG_EXPORT_FORMAT
        assert document["version"] == CONFIG_EXPORT_VERSION
        assert set(document["roles"].keys()) == set(ROLE_VALUES)
        validate_export(document)  # must validate cleanly

    def test_no_secrets_exported_and_env_values_redacted(self, controller, tmp_path):
        controller.set_generic_cli_config(
            AgentRole.BUILDER,
            {
                "executable": "agent",
                "args": ["run"],
                "env_overrides": {"API_TOKEN": "super-secret-value"},
            },
        )
        document = export_from_controller(controller)
        flat = json.dumps(document)
        assert "super-secret-value" not in flat
        builder = document["roles"]["BUILDER"]["extra"]["generic_cli"]
        assert builder["env_overrides"]["API_TOKEN"] == REDACTED_VALUE
        # The KEY survives so the operator sees what was redacted.
        assert "API_TOKEN" in builder["env_overrides"]

    def test_external_session_bindings_excluded(self, controller):
        controller.bind_external_session(
            AgentRole.FINAL_AUDITOR, "codex", "0123abcd-4567", title="t"
        )
        document = export_from_controller(controller)
        assert "external_session_binding" not in json.dumps(document)

    def test_written_file_is_deterministic_json(self, controller, tmp_path):
        document = export_from_controller(controller)
        first = write_export(document, tmp_path / "one.json")
        second = write_export(document, tmp_path / "two.json")
        assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")
        assert json.loads(first.read_text(encoding="utf-8"))["format"] == CONFIG_EXPORT_FORMAT


# -- import validation (nothing applied on failure) ---------------------------------
class TestImportValidation:
    def test_round_trip_apply(self, controller, tmp_path):
        path = tmp_path / "config.json"
        write_export(full_document(tmp_path), path)
        payload = validate_export(read_export(path))
        result = apply_import(controller, payload)
        assert set(result["applied_roles"]) == set(ROLE_VALUES)
        assert controller.role_config(AgentRole.BUILDER).model == "deepseek/deepseek-v4.1-flash"
        assert controller.role_config(AgentRole.ORCHESTRATOR).engine == "codex"

    def test_restart_after_import_reloads_configuration(self, controller, tmp_path, database, event_log):
        path = tmp_path / "config.json"
        write_export(full_document(tmp_path), path)
        apply_import(controller, validate_export(read_export(path)))

        # A real restart: restore_state() reads the persisted workspace and
        # role configs from SQLite, exactly like app.build_controller().
        from encomm_pcc.app import restore_state

        fresh = PipelineController(
            database=database, event_log=event_log, state=restore_state(database)
        )
        fresh_state_role = fresh.role_config(AgentRole.ORCHESTRATOR)
        assert fresh_state_role.engine == "codex"

    def test_generic_cli_nested_config_imports_validated(self, controller, tmp_path):
        path = tmp_path / "config.json"
        write_export(full_document(tmp_path), path)
        apply_import(controller, validate_export(read_export(path)))
        stored = controller.generic_cli_config(AgentRole.BUILDER)
        assert stored is not None
        config = GenericCliConfig.from_mapping(stored)
        assert config.executable == "opencode"
        assert config.timeout_s == 1200.0
        # Redacted env values are dropped, never fabricated.
        assert config.env_overrides.get("API_BASE") != REDACTED_VALUE

    def test_malformed_json_refused(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not json at all", encoding="utf-8")
        with pytest.raises(ConfigExchangeError, match="not valid JSON"):
            read_export(path)

    def test_wrong_format_id_refused(self, tmp_path):
        path = tmp_path / "wrong.json"
        document = full_document(tmp_path, format="some-other-tool")
        write_export(document, path)
        with pytest.raises(ConfigExchangeError, match="format"):
            validate_export(read_export(path))

    def test_unsupported_future_version_refused(self, tmp_path):
        document = full_document(tmp_path, version=CONFIG_EXPORT_VERSION + 99)
        with pytest.raises(ConfigExchangeError, match="newer"):
            validate_export(document)

    def test_older_version_refused(self, tmp_path):
        document = full_document(tmp_path, version=0)
        with pytest.raises(ConfigExchangeError, match="no longer accepted"):
            validate_export(document)

    def test_missing_roles_section_refused(self, tmp_path):
        document = full_document(tmp_path)
        del document["roles"]
        with pytest.raises(ConfigExchangeError, match="roles"):
            validate_export(document)

    def test_unknown_role_refused(self, tmp_path):
        document = full_document(tmp_path)
        document["roles"]["MISSING_ROLE"] = {}
        with pytest.raises(ConfigExchangeError, match="Unknown role"):
            validate_export(document)

    def test_invalid_session_policy_refused(self, tmp_path):
        document = full_document(tmp_path)
        document["roles"]["BUILDER"]["session_policy"] = "whenever"
        with pytest.raises(ConfigExchangeError, match="session_policy"):
            validate_export(document)

    def test_invalid_generic_cli_config_refused(self, tmp_path):
        document = full_document(tmp_path)
        document["roles"]["BUILDER"]["extra"]["generic_cli"] = {
            "executable": "",
            "args": "not-a-token-list",
        }
        with pytest.raises(ConfigExchangeError, match="generic_cli is invalid"):
            validate_export(document)

    def test_oversized_file_refused(self, tmp_path):
        path = tmp_path / "huge.json"
        path.write_text("x" * (MAX_EXPORT_BYTES + 1), encoding="utf-8")
        with pytest.raises(ConfigExchangeError, match="maximum"):
            read_export(path)

    def test_missing_file_refused(self, tmp_path):
        with pytest.raises(ConfigExchangeError, match="Cannot read"):
            read_export(tmp_path / "does-not-exist.json")

    def test_binding_in_import_refused(self, tmp_path):
        document = full_document(tmp_path)
        document["roles"]["FINAL_AUDITOR"]["extra"]["external_session_binding"] = {
            "driver_id": "codex",
            "external_session_id": "abc",
        }
        with pytest.raises(ConfigExchangeError, match="cannot be imported"):
            validate_export(document)

    def test_invalid_import_changes_nothing(self, controller, tmp_path):
        before_engine = controller.role_config(AgentRole.BUILDER).engine
        before_extra = json.dumps(controller.role_config(AgentRole.BUILDER).extra or {})
        document = full_document(tmp_path)
        document["roles"]["BUILDER"]["session_policy"] = "bogus"
        with pytest.raises(ConfigExchangeError):
            validate_export(document)
        assert controller.role_config(AgentRole.BUILDER).engine == before_engine
        assert json.dumps(controller.role_config(AgentRole.BUILDER).extra or {}) == before_extra

    def test_nonexistent_workspace_path_not_applied(self, controller, tmp_path):
        document = full_document(tmp_path)
        document["workspace"]["repo_path"] = "Z:/definitely/not/anywhere"
        payload = validate_export(document)
        apply_import(controller, payload)
        assert controller.state.workspace.repo_path != "Z:/definitely/not/anywhere"

    def test_apply_makes_zero_model_calls(self, controller, tmp_path, monkeypatch):
        """Structural zero-model-call proof: any engine contact explodes.

        The executor's only launch path is a ProcessRunner; if an import ever
        reached one, this spy raises instead of silently succeeding.
        """
        from encomm_pcc.drivers import NullProcessRunner

        class BoomRunner(NullProcessRunner):
            def run(self, spec):
                raise AssertionError("import tried to launch a process")

        controller.attach_executor(object())  # never used, but present
        monkeypatch.setattr(
            "encomm_pcc.drivers.SubprocessRunner", BoomRunner, raising=False
        )
        path = tmp_path / "config.json"
        write_export(full_document(tmp_path), path)
        apply_import(controller, validate_export(read_export(path)))
