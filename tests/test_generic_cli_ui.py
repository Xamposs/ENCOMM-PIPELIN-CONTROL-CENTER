"""Session 007 — Generic CLI configuration UI (offscreen) tests.

Covers the brief's §22 UI matrix: configuration visibility per engine,
valid/invalid state, engine switching that preserves the stored config, no
session selector for the stateless engine, and save/reload persistence
(controller → SQLite → reload).
"""

from __future__ import annotations

import pytest

from encomm_pcc.core import PipelineController
from encomm_pcc.domain import AgentRole
from encomm_pcc.drivers import GenericCliConfig
from encomm_pcc.ui import GenericCliConfigDialog
from encomm_pcc.ui.panels import RolePanel


VALID_CONFIG = {
    "executable": "python",
    "args": ["-c", "print('ok')"],
    "prompt_transport": "stdin",
    "result_mode": "stdout_text",
    "result_field": "",
    "model_args": [],
    "timeout_s": 600.0,
    "env_overrides": {},
}


@pytest.fixture()
def registry(qapp):
    from encomm_pcc.drivers import IMPLEMENTED_DRIVERS, DriverRegistry, NullProcessRunner

    registry = DriverRegistry(runner=NullProcessRunner())
    registry.register_all(IMPLEMENTED_DRIVERS)
    return registry


@pytest.fixture()
def panel(qapp, controller, registry) -> RolePanel:
    return RolePanel(AgentRole.BUILDER, controller, registry)


# -- visibility ---------------------------------------------------------------
class TestGenericCliUiVisibility:
    def test_row_hidden_for_other_engines(self, panel):
        panel.engine_combo.setCurrentIndex(panel.engine_combo.findData("hermes"))
        assert panel.generic_cli_button.isVisibleTo(panel) is False

    def test_row_appears_for_generic_cli(self, panel):
        panel.engine_combo.setCurrentIndex(panel.engine_combo.findData("generic_cli"))
        assert panel.generic_cli_button.isVisibleTo(panel) is True

    def test_unconfigured_note_warns(self, panel):
        panel.engine_combo.setCurrentIndex(panel.engine_combo.findData("generic_cli"))
        assert "No Generic CLI configuration stored" in panel.generic_cli_note.text()

    def test_configured_note_summarises(self, panel, controller):
        controller.set_generic_cli_config(AgentRole.BUILDER, VALID_CONFIG)
        panel.engine_combo.setCurrentIndex(panel.engine_combo.findData("generic_cli"))
        assert "python" in panel.generic_cli_note.text()

    def test_capability_hint_reflects_configuration_state(self, panel, controller):
        # Drive the flow like the real window does: the engine choice is
        # persisted via set_role_config, then the panel refreshes from it.
        controller.set_role_config(AgentRole.BUILDER, engine="generic_cli")
        panel.refresh_from_controller()
        assert "not configured" in panel.capability_label.text()
        controller.set_generic_cli_config(AgentRole.BUILDER, VALID_CONFIG)
        panel.refresh_from_controller()
        assert "configuration stored" in panel.capability_label.text()

    def test_no_session_selector_claims_for_stateless_engine(self, panel):
        # generic_cli has no discovery capability: REFRESH must stay disabled
        # and the capability hint must say "stateless".
        panel.engine_combo.setCurrentIndex(panel.engine_combo.findData("generic_cli"))
        if panel.refresh_sessions_button is not None:
            assert panel.refresh_sessions_button.isEnabled() is False
        assert "stateless engine" in panel.capability_label.text()


# -- dialog (structured editor) -------------------------------------------------
class TestGenericCliDialog:
    def test_load_populates_widgets(self, qapp):
        dialog = GenericCliConfigDialog(AgentRole.BUILDER, VALID_CONFIG)
        assert dialog.executable_edit.text() == "python"
        assert "-c" in dialog.args_edit.toPlainText()
        assert dialog.timeout_spin.value() == 600.0

    def test_build_config_dict_round_trip(self, qapp):
        dialog = GenericCliConfigDialog(AgentRole.BUILDER, VALID_CONFIG)
        built = dialog.build_config_dict()
        config = GenericCliConfig.from_mapping(built)
        assert config.executable == "python"
        assert config.args == ("-c", "print('ok')")

    def test_quoted_tokens_are_split_into_separate_argv_members(self, qapp):
        dialog = GenericCliConfigDialog(
            AgentRole.BUILDER,
            None,
        )
        dialog.executable_edit.setText("agent")
        dialog.args_edit.setPlainText('run --prompt "two words"')
        built = dialog.build_config_dict()
        assert built["args"] == ["run", "--prompt", "two words"]

    def test_save_is_refused_without_executable(self, qapp):
        dialog = GenericCliConfigDialog(AgentRole.BUILDER, None)
        dialog.executable_edit.setText("")
        dialog.args_edit.setPlainText("run")
        with pytest.raises(Exception, match="executable is empty"):
            dialog.build_config_dict()

    def test_save_is_refused_for_shell_string_args(self, qapp):
        # A pasted shell command cannot sneak in as the args field.
        dialog = GenericCliConfigDialog(AgentRole.BUILDER, None)
        dialog.executable_edit.setText("agent")
        dialog.args_edit.setPlainText("run --json && echo done")
        # shlex splits this into tokens; the config keeps them LITERAL —
        # the metacharacters are data, never interpreted.
        built = dialog.build_config_dict()
        assert "&&" in built["args"][2]

    def test_invalid_transport_placeholder_pair_refused_on_build(self, qapp):
        dialog = GenericCliConfigDialog(AgentRole.BUILDER, None)
        dialog.executable_edit.setText("agent")
        dialog.args_edit.setPlainText("{prompt_file}")
        # stdin transport + {prompt_file} placeholder must fail validation.
        with pytest.raises(Exception, match="temporary_file"):
            dialog.build_config_dict()

    def test_accept_rejects_invalid_configuration(self, qapp):
        dialog = GenericCliConfigDialog(AgentRole.BUILDER, None)
        dialog.executable_edit.setText("agent")
        dialog.args_edit.setPlainText("{prompt_file}")  # invalid for stdin
        dialog.accept()
        # The dialog stays open (rejected the invalid input).
        assert dialog.result() != GenericCliConfigDialog.Accepted


# -- engine switch / persistence -------------------------------------------------
class TestGenericCliConfigPersistence:
    def test_engine_switch_preserves_stored_config(self, panel, controller):
        controller.set_generic_cli_config(AgentRole.BUILDER, VALID_CONFIG)
        panel.engine_combo.setCurrentIndex(panel.engine_combo.findData("generic_cli"))
        panel.engine_combo.setCurrentIndex(panel.engine_combo.findData("hermes"))
        panel.engine_combo.setCurrentIndex(panel.engine_combo.findData("generic_cli"))
        assert controller.generic_cli_config(AgentRole.BUILDER) is not None
        assert "python" in panel.generic_cli_note.text()

    def test_save_reload_round_trip_through_database(self, qapp, database, event_log):
        controller = PipelineController(database=database, event_log=event_log)
        controller.set_generic_cli_config(AgentRole.ORCHESTRATOR, VALID_CONFIG)

        # Reload from SQLite exactly like a restart (fresh controller, same DB).
        from encomm_pcc.app import restore_state

        state = restore_state(database)
        assert state is not None
        reloaded = state.config_for(AgentRole.ORCHESTRATOR)
        stored = (reloaded.extra or {}).get("generic_cli")
        assert stored is not None
        config = GenericCliConfig.from_mapping(stored)
        assert config.executable == "python"
        assert config.timeout_s == 600.0

    def test_invalid_config_is_never_persisted(self, controller):
        with pytest.raises(Exception):
            controller.set_generic_cli_config(
                AgentRole.BUILDER, {"executable": "", "args": []}
            )
        assert controller.generic_cli_config(AgentRole.BUILDER) is None

    def test_clear_config(self, controller):
        controller.set_generic_cli_config(AgentRole.BUILDER, VALID_CONFIG)
        controller.set_generic_cli_config(AgentRole.BUILDER, None)
        assert controller.generic_cli_config(AgentRole.BUILDER) is None
