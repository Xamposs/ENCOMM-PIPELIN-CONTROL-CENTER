"""UI smoke test: the main window must construct and behave in offscreen mode.

The platform plugin is forced to ``offscreen`` in ``conftest.py`` before Qt is
imported, so these tests never open a real window.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from encomm_pcc.core import PipelineController  # noqa: E402
from encomm_pcc.domain import AgentRole, PipelinePhase  # noqa: E402
from encomm_pcc.ui import MainWindow  # noqa: E402


@pytest.fixture()
def window(qapp, controller: PipelineController):  # noqa: ANN001, ANN201
    win = MainWindow(controller)
    yield win
    win.close()
    win.deleteLater()


def test_window_constructs_offscreen(qapp, window) -> None:  # noqa: ANN001
    assert window.windowTitle().startswith("ENCOMM Pipeline Control Center")
    assert window.centralWidget() is not None


def test_all_required_sections_are_present(window) -> None:  # noqa: ANN001
    titles = set(window.panel_titles())
    for required in ("WORKSPACE", "ROLES", "BATCH", "LOG PANEL"):
        assert required in titles, f"missing section {required}; got {sorted(titles)}"
    for role in AgentRole:
        assert role.value.replace("_", " ") in titles
    # Session 002 adds the one-task dispatch control.
    assert any(t.startswith("TASK") for t in titles)


def test_one_panel_per_role(window) -> None:  # noqa: ANN001
    assert set(window.role_panels) == set(AgentRole)


def test_batch_panel_defaults_to_five(window) -> None:  # noqa: ANN001
    assert window.batch_panel.size_spin.value() == 5
    assert window.batch_panel.size_spin.minimum() == 1
    assert window.batch_panel.size_spin.maximum() == 5


def test_role_panels_show_the_mandated_session_policies(window) -> None:  # noqa: ANN001
    task_auditor = window.role_panels[AgentRole.TASK_AUDITOR]
    builder = window.role_panels[AgentRole.BUILDER]
    assert task_auditor.policy_label is not None
    assert builder.policy_label is not None
    assert "Persistent per batch" in task_auditor.policy_label.text()
    assert "Always new" in builder.policy_label.text()


def test_final_auditor_has_same_as_orchestrator_option(window) -> None:  # noqa: ANN001
    final = window.role_panels[AgentRole.FINAL_AUDITOR]
    assert final.same_as_check is not None
    assert final.same_as_check.text() == "Same as Orchestrator"


def test_orchestrator_has_a_new_session_button(window) -> None:  # noqa: ANN001
    orchestrator = window.role_panels[AgentRole.ORCHESTRATOR]
    assert orchestrator.new_session_button is not None
    assert orchestrator.new_session_button.text() == "New Session"


def test_new_session_button_starts_nothing(qapp, window, controller) -> None:  # noqa: ANN001
    window.role_panels[AgentRole.ORCHESTRATOR].new_session_button.click()
    assert controller.sessions.current_session_id(AgentRole.ORCHESTRATOR) is None
    messages = [r.message for r in controller.events.history()]
    assert any("placeholder only" in m for m in messages)


def test_engine_dropdowns_list_the_registered_drivers(window) -> None:  # noqa: ANN001
    combo = window.role_panels[AgentRole.BUILDER].engine_combo
    values = {combo.itemData(i) for i in range(combo.count())}
    assert {"codex", "hermes", "generic_cli", ""} <= values


def test_start_button_drives_the_controller(qapp, window, controller) -> None:  # noqa: ANN001
    window.batch_panel.size_spin.setValue(3)
    window.batch_panel.start_button.click()
    qapp.processEvents()

    assert controller.machine.phase is PipelinePhase.PLANNING_BATCH
    assert controller.state.batch is not None
    assert controller.state.batch.size == 3
    assert "PLANNING_BATCH" in window.batch_panel.phase_label.text()


def test_pause_resume_stop_buttons_follow_the_state_machine(qapp, window, controller) -> None:  # noqa: ANN001
    assert not window.batch_panel.pause_button.isEnabled()

    window.batch_panel.start_button.click()
    qapp.processEvents()
    assert window.batch_panel.pause_button.isEnabled()

    window.batch_panel.pause_button.click()
    qapp.processEvents()
    assert controller.machine.phase is PipelinePhase.PAUSED
    assert window.batch_panel.resume_button.isEnabled()

    window.batch_panel.resume_button.click()
    qapp.processEvents()
    assert controller.machine.phase is PipelinePhase.PLANNING_BATCH

    window.batch_panel.stop_button.click()
    qapp.processEvents()
    assert controller.machine.phase is PipelinePhase.IDLE
    assert not window.batch_panel.stop_button.isEnabled()


def test_log_panel_receives_timestamped_records(qapp, window, controller) -> None:  # noqa: ANN001
    before = window.log_panel.view.toPlainText()
    controller.events.info("smoke-test-line", source="test")
    qapp.processEvents()

    text = window.log_panel.view.toPlainText()
    assert text != before
    assert "smoke-test-line" in text
    assert "[test]" in text
    # Timestamp prefix, e.g. "2026-09-22T10:16:05Z  INFO"
    assert text.strip().splitlines()[-1][:4].isdigit()


def test_workspace_edit_updates_the_controller(qapp, window, controller) -> None:  # noqa: ANN001
    window.workspace_panel.name_edit.setText("Edited Workspace")
    window.workspace_panel.path_edit.setText(r"C:\edited")
    window.workspace_panel._emit_changed()  # noqa: SLF001 - simulated focus-out
    qapp.processEvents()

    assert controller.state.workspace.name == "Edited Workspace"
    assert controller.state.workspace.repo_path == r"C:\edited"


def test_role_edit_updates_the_controller(qapp, window, controller) -> None:  # noqa: ANN001
    panel = window.role_panels[AgentRole.BUILDER]
    panel.profile_edit.setText("builder-custom")
    panel._emit_changed()  # noqa: SLF001 - simulated focus-out
    qapp.processEvents()

    assert controller.role_config(AgentRole.BUILDER).project_profile == "builder-custom"


def test_same_as_orchestrator_disables_the_final_auditor_session_field(
    qapp, window, controller  # noqa: ANN001
) -> None:
    final = window.role_panels[AgentRole.FINAL_AUDITOR]
    final.same_as_check.setChecked(True)
    qapp.processEvents()
    assert final.session_combo is not None
    assert not final.session_combo.isEnabled()


def test_startup_summary_is_logged(qapp, window, controller) -> None:  # noqa: ANN001
    messages = [r.message for r in controller.events.history()]
    assert any("started." in m for m in messages)
    assert any("Driver 'codex'" in m for m in messages)
