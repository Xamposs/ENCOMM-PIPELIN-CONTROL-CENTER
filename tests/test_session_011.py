"""Session 011 — production UI finalisation + true engine/profile/session control.

Covers the brief's §23–§25 acceptance matrix offline (zero AI calls):

* **Architect** — real engine dropdown (registry-driven), Codex selectable,
  session discovery/binding, and the TRUE same-thread contract: planning and
  Final Audit resolve the SAME actual external session (live → durable
  binding → persisted plan after restart), proven by the driver's real
  ``resume_session`` calls (brief §8/§9/§24, asserted strictly).
* **Coder** — Hermes profile selector, automatic fresh-session default, the
  *Resume selected session ONCE* mode (one-shot override armed → consumed →
  Automatic returns), profile-scoped discovery with auto-refresh.
* **Auditor** — profile-scoped sessions, durable binding per batch.
* **Engine / profile switching** — stale bindings are cleared; sessions never
  cross engines or profiles.
* **Production UI** — no Advanced button in normal launches, present behind
  ``--debug-ui``; operator status language; engine-specific field layout.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QPushButton  # noqa: E402

from encomm_pcc.core import Executor, NullEventLog, PipelineController  # noqa: E402
from encomm_pcc.core.batch_runner import BatchRunner  # noqa: E402
from encomm_pcc.domain import AgentRole  # noqa: E402
from encomm_pcc.drivers import (  # noqa: E402
    DriverCapabilities,
    DriverRegistry,
    ExternalSessionDescriptor,
    SessionDiscoveryResult,
)

# -- scripted drivers -------------------------------------------------------


def _make_scoped_driver(driver_id: str, display_name: str):
    """A registry driver with a canned, profile-capturing ``discover_sessions``."""

    class _Scoped:
        seen: dict = {}
        discovery_ok: bool = True

        @classmethod
        def capabilities(cls) -> DriverCapabilities:
            return DriverCapabilities(
                driver_id=cls.driver_id,
                display_name=cls.display_name,
                supports_sessions=True,
                supports_resume=False,
                implemented=True,
                notes="Session 011 test double.",
            )

        def __init__(self, runner=None):  # noqa: ANN001
            self._runner = runner

        def discover_sessions(self, *, workspace_path=None, profile=None, limit=50):
            type(self).seen["profile"] = profile
            type(self).seen["workspace"] = workspace_path
            session_id = str(driver_id) + "-20260927_120000_aaaa"
            sessions = [
                ExternalSessionDescriptor(
                    session_id=session_id,
                    driver_id=driver_id,
                    title="scoped-session",
                )
            ] if profile == "encomm-auditor" else []
            return SessionDiscoveryResult(
                ok=type(self).discovery_ok, driver_id=driver_id,
                sessions=sessions, mechanism="test",
            )

    # Class attribute assignment avoids the class-body NameError trap
    # (``driver_id = driver_id`` in a class body binds a fresh local).
    _Scoped.driver_id = driver_id
    _Scoped.display_name = display_name
    return _Scoped


class _ArmableExecutor:
    """Duck-typed one-shot recovery surface (Session 010 D-050 contract)."""

    def __init__(self) -> None:
        self.armed: str | None = None

    def arm_coder_recovery(self, session_id: str) -> None:
        self.armed = str(session_id)

    def clear_coder_recovery(self) -> None:
        self.armed = None

    def coder_recovery_armed(self) -> bool:
        return self.armed is not None


# -- helpers -----------------------------------------------------------------


def _same_as_controller(controller: PipelineController, tmp_path, results: list):
    """Controller whose FINAL_AUDITOR stays ``same_as_orchestrator=True``.

    This is the Session 011 production contract (Architect = one thread for
    planning AND Final Audit), unlike earlier suites that cleared the flag.
    """
    from conftest import PermissiveRunner
    from test_batch_runner import ScriptedBatchDriver, discovery_stub

    ScriptedBatchDriver.reset()
    ScriptedBatchDriver.shared = list(results)
    registry = DriverRegistry()
    registry.register(ScriptedBatchDriver)
    controller.set_workspace("Same-Thread Workspace", str(tmp_path))
    for role in (AgentRole.ORCHESTRATOR, AgentRole.BUILDER, AgentRole.TASK_AUDITOR):
        controller.set_role_config(role, engine="batch", project_profile="test-profile")
    executor = Executor(
        controller,
        registry=registry,
        runner=PermissiveRunner(),
        database=controller.database,
        event_log=NullEventLog(),
        profile_discovery=discovery_stub,
    )
    controller.attach_executor(executor)
    return executor, ScriptedBatchDriver


def _seed_ready_batch(controller: PipelineController, tmp_path, *, orchestrator_session_id):
    """A batch at READY_FOR_FINAL_AUDIT with a known persisted planning thread."""
    from test_final_audit import make_ready_batch

    make_ready_batch(controller, tmp_path, approved_session_ids=True)
    batch = controller.state.batch
    assert batch is not None
    batch.plan.orchestrator_session_id = orchestrator_session_id
    controller.persist()
    return batch.batch_id


# ---------------------------------------------------------------------------
# §8/§9/§24 — TRUE Architect thread continuity (planning == Final Audit)
# ---------------------------------------------------------------------------
class TestArchitectSameThreadContinuity:
    def test_final_audit_resumes_the_live_orchestrator_session(
        self, controller, tmp_path
    ) -> None:
        """Plan creates thread X; the Final Audit RESUMES X (never a new one)."""
        from test_batch_runner import build_plan_text, ok_result, verdict_text
        from test_final_audit import pass_with_next

        results = [
            ok_result("arch-X", build_plan_text(1)),
            ok_result("builder-1", "done"),
            ok_result("auditor", verdict_text("PASS")),
        ]
        executor, driver = _same_as_controller(controller, tmp_path, results)
        batch_report = BatchRunner(executor).run_batch(project_brief="b", batch_size=1)
        assert batch_report.outcome.value == "READY_FOR_FINAL_AUDIT"
        # The planner's real thread is now the LIVE orchestrator session.
        assert controller.sessions.current_session_id(AgentRole.ORCHESTRATOR) == "arch-X"

        driver.shared.append(ok_result("arch-X", pass_with_next(4)))
        report = executor.run_final_audit(next_batch_size=4)
        assert report.outcome.value == "PASSED"
        # THE assertion the product contract demands: the Final Audit resumed
        # the planning thread — the same real external session id.
        assert driver.resumed == ["arch-X"]
        assert report.session_id == "arch-X"

    def test_final_audit_resumes_a_durably_bound_architect_session(
        self, controller, tmp_path
    ) -> None:
        """Operator-bound Architect thread X ⇒ Final Audit uses X, not the plan."""
        from test_batch_runner import ok_result, verdict_text
        from test_final_audit import pass_with_next

        _seed_ready_batch(controller, tmp_path, orchestrator_session_id=None)
        controller.bind_external_session(
            AgentRole.ORCHESTRATOR, "batch", "bound-thread-X", profile=None
        )
        executor, driver = _same_as_controller(controller, tmp_path, [])
        driver.shared.append(ok_result("bound-thread-X", pass_with_next(4)))
        report = executor.run_final_audit(next_batch_size=4)
        assert report.outcome.value == "PASSED"
        assert driver.resumed == ["bound-thread-X"]
        assert report.session_id == "bound-thread-X"

    def test_final_audit_after_restart_resumes_the_persisted_plan_thread(
        self, controller, tmp_path
    ) -> None:
        """No live session and no binding ⇒ the persisted planning thread."""
        from test_batch_runner import ok_result, verdict_text
        from test_final_audit import pass_with_next

        _seed_ready_batch(controller, tmp_path, orchestrator_session_id="persisted-thread")
        executor, driver = _same_as_controller(controller, tmp_path, [])
        driver.shared.append(ok_result("persisted-thread", pass_with_next(4)))
        report = executor.run_final_audit(next_batch_size=4)
        assert report.outcome.value == "PASSED"
        assert driver.resumed == ["persisted-thread"]
        assert report.session_id == "persisted-thread"

    def test_dedicated_final_auditor_engine_keeps_its_own_session(
        self, controller, tmp_path
    ) -> None:
        """same_as_orchestrator=False stays per-role: no orchestrator resume."""
        from test_batch_runner import ok_result, verdict_text
        from test_final_audit import pass_with_next

        _seed_ready_batch(controller, tmp_path, orchestrator_session_id="arch-X")
        executor, driver = _same_as_controller(controller, tmp_path, [])
        controller.set_role_config(
            AgentRole.FINAL_AUDITOR,
            engine="batch",
            project_profile="test-profile",
            same_as_orchestrator=False,
        )
        driver.shared.append(ok_result("final-own", pass_with_next(4)))
        report = executor.run_final_audit(next_batch_size=4)
        assert report.outcome.value == "PASSED"
        assert driver.resumed == []  # never touches the orchestrator thread
        assert report.session_id == "final-own"

    def test_no_architect_thread_yet_creates_one(self, controller, tmp_path) -> None:
        """First Architect operation (no live/bound/persisted thread) → NEW."""
        from test_batch_runner import ok_result, verdict_text
        from test_final_audit import pass_with_next

        _seed_ready_batch(controller, tmp_path, orchestrator_session_id=None)
        executor, driver = _same_as_controller(controller, tmp_path, [])
        driver.shared.append(ok_result("fresh-final", pass_with_next(4)))
        report = executor.run_final_audit(next_batch_size=4)
        assert report.outcome.value == "PASSED"
        assert driver.resumed == []
        assert report.session_id == "fresh-final"


# ---------------------------------------------------------------------------
# §23 — UI engine / profile / session matrix (offscreen)
# ---------------------------------------------------------------------------
class TestProductionEngineSelectors:
    def _window(self, controller, *, debug_ui: bool = False):
        from encomm_pcc.ui.main_window import MainWindow

        return MainWindow(controller, profiles=("encomm-auditor",), debug_ui=debug_ui)

    def test_engine_dropdowns_come_from_the_registry_and_codex_is_selectable(
        self, qapp, database
    ) -> None:
        controller = PipelineController(database=database, event_log=NullEventLog())
        window = self._window(controller)
        try:
            panel = window.simple_panel
            for combo in (
                panel.architect_engine,
                panel.coder_engine,
                panel.auditor_engine,
            ):
                engines = [
                    combo.itemData(i) for i in range(combo.count())
                ]
                assert engines, "engine dropdown must never be empty"
                assert "codex" in engines and "hermes" in engines
            # Codex is directly selectable for the Architect.
            assert panel.architect_engine.findData("codex") >= 0
        finally:
            window.close()

    def test_architect_engine_selection_writes_the_durable_config_and_clears_stale_binding(
        self, qapp, database
    ) -> None:
        from encomm_pcc.ui.simple_mode import SimpleModePanel

        controller = PipelineController(database=database, event_log=NullEventLog())
        # A binding recorded under Codex must die when the engine changes.
        controller.set_role_config(AgentRole.ORCHESTRATOR, engine="codex")
        controller.bind_external_session(
            AgentRole.ORCHESTRATOR, "codex", "thread-A", profile=None
        )
        registry = DriverRegistry()
        registry.register(_make_scoped_driver("codex", "Codex"))
        registry.register(_make_scoped_driver("hermes", "Hermes"))
        saved = controller.registry
        controller.registry = registry
        try:
            panel = SimpleModePanel(controller, profiles=())
            index = panel.architect_engine.findData("hermes")
            assert index >= 0
            panel.architect_engine.setCurrentIndex(index)
            panel.architect_engine.activated.emit(index)
            config = controller.state.config_for(AgentRole.ORCHESTRATOR)
            assert config.engine == "hermes"
            assert config.external_session_binding() is None, (
                "an engine change must invalidate the old driver's binding"
            )
        finally:
            controller.registry = saved

    def test_engine_specific_field_layout(self, qapp, database) -> None:
        from encomm_pcc.ui.simple_mode import SimpleModePanel

        controller = PipelineController(database=database, event_log=NullEventLog())
        registry = DriverRegistry()
        registry.register(_make_scoped_driver("codex", "Codex"))
        registry.register(_make_scoped_driver("hermes", "Hermes"))
        saved = controller.registry
        controller.registry = registry
        try:
            panel = SimpleModePanel(controller, profiles=())
            # Default config engine HERMES → profile row visible, session row visible.
            assert not panel._arch_fields.profile_row.isHidden()
            assert not panel._arch_fields.session_row.isHidden()
            # Switch Architect to Codex → profile/provider/model disappear.
            index = panel.architect_engine.findData("codex")
            panel.architect_engine.setCurrentIndex(index)
            panel.architect_engine.activated.emit(index)
            assert panel._arch_fields.profile_row.isHidden()
            assert not panel._arch_fields.session_row.isHidden()
        finally:
            controller.registry = saved

    def test_normal_launch_has_no_advanced_button_and_operator_status_language(
        self, qapp, database
    ) -> None:
        controller = PipelineController(database=database, event_log=NullEventLog())
        window = self._window(controller)
        try:
            buttons = [
                b.text() for b in window.findChildren(QPushButton)
            ]
            assert not any("Advanced" in text for text in buttons), buttons
            message = window.statusBar().currentMessage()
            assert "Ready" in message
            assert "TASK" not in message.upper(), message
        finally:
            window.close()

    def test_debug_ui_restores_the_advanced_button(self, qapp, database) -> None:
        controller = PipelineController(database=database, event_log=NullEventLog())
        window = self._window(controller, debug_ui=True)
        try:
            buttons = [
                b.text() for b in window.findChildren(QPushButton)
            ]
            assert any("Advanced" in text for text in buttons), buttons
        finally:
            window.close()


class TestCoderSessionControl:
    def test_resume_once_arms_and_automatic_clears(self, qapp, database) -> None:
        from encomm_pcc.ui.simple_mode import SimpleModePanel

        controller = PipelineController(database=database, event_log=NullEventLog())
        executor = _ArmableExecutor()
        controller.attach_executor(executor)
        panel = SimpleModePanel(controller, profiles=("encomm-auditor",))
        try:
            # DEFAULT: Automatic — the resume selector is hidden.
            assert panel.coder_session_mode.currentData() == "automatic"
            assert panel._coder_fields.session_row.isHidden()

            # Operator chooses "Resume selected session ONCE" and a session.
            index = panel.coder_session_mode.findData("resume_once")
            panel.coder_session_mode.setCurrentIndex(index)
            panel.coder_session_mode.activated.emit(index)
            assert not panel._coder_fields.session_row.isHidden()
            panel._coder_fields.session.addItem("sess", "20260927_120000_aaaa")
            panel._coder_fields.session.setCurrentIndex(1)
            panel._coder_fields.session.activated.emit(1)
            assert executor.armed == "20260927_120000_aaaa"

            # Switching back to Automatic clears the one-shot override.
            index = panel.coder_session_mode.findData("automatic")
            panel.coder_session_mode.setCurrentIndex(index)
            panel.coder_session_mode.activated.emit(index)
            assert executor.armed is None
            assert panel._coder_fields.session_row.isHidden()
        finally:
            controller.attach_executor(None)

    def test_consumed_override_returns_the_mode_to_automatic(
        self, qapp, database
    ) -> None:
        from encomm_pcc.ui.simple_mode import SimpleModePanel

        controller = PipelineController(database=database, event_log=NullEventLog())
        executor = _ArmableExecutor()
        controller.attach_executor(executor)
        panel = SimpleModePanel(controller, profiles=())
        try:
            panel.coder_session_mode.setCurrentIndex(
                panel.coder_session_mode.findData("resume_once")
            )
            panel.coder_session_mode.activated.emit(
                panel.coder_session_mode.currentIndex()
            )
            executor.arm_coder_recovery("20260927_120000_aaaa")
            # The next Builder operation consumed the override → cleared.
            executor.clear_coder_recovery()
            panel.refresh()
            assert panel.coder_session_mode.currentData() == "automatic"
            assert panel._coder_fields.session_row.isHidden()
        finally:
            controller.attach_executor(None)


class TestProfileScopedSessions:
    def test_coder_discovery_is_profile_scoped_and_auto_refreshes(
        self, qapp, database
    ) -> None:
        from encomm_pcc.ui.simple_mode import SimpleModePanel

        controller = PipelineController(database=database, event_log=NullEventLog())
        scoped_hermes = _make_scoped_driver("hermes", "Hermes")
        registry = DriverRegistry()
        registry.register(scoped_hermes)
        saved = controller.registry
        controller.registry = registry
        try:
            controller.set_role_config(
                AgentRole.BUILDER,
                engine="hermes",
                project_profile="encomm-auditor",
            )
            panel = SimpleModePanel(
                controller, profiles=("encomm-auditor", "encomm-accounting-intelligence")
            )
            # §15: the profile change itself triggers a scoped refresh.
            panel._on_profile_changed(
                AgentRole.BUILDER, panel._coder_fields
            )
            assert scoped_hermes.seen["profile"] == "encomm-auditor"
            labels = [
                panel.coder_session.itemText(i)
                for i in range(panel.coder_session.count())
            ]
            assert any("20260927_120000_aaaa" in label for label in labels)
            # Operator switches the profile combo → the old profile's session
            # disappears from the scoped list and discovery re-runs scoped.
            index = panel.coder_profile.findText("encomm-accounting-intelligence")
            assert index >= 0
            panel.coder_profile.setCurrentIndex(index)
            panel._on_profile_changed(
                AgentRole.BUILDER, panel._coder_fields
            )
            assert scoped_hermes.seen["profile"] == "encomm-accounting-intelligence"
            labels = [
                panel.coder_session.itemText(i)
                for i in range(panel.coder_session.count())
            ]
            assert not any("20260927_120000_aaaa" in label for label in labels)
        finally:
            controller.registry = saved

    def test_profile_change_auto_clears_a_stale_binding(
        self, qapp, database
    ) -> None:
        from encomm_pcc.ui.simple_mode import SimpleModePanel

        controller = PipelineController(database=database, event_log=NullEventLog())
        scoped_hermes = _make_scoped_driver("hermes", "Hermes")
        registry = DriverRegistry()
        registry.register(scoped_hermes)
        saved = controller.registry
        controller.registry = registry
        try:
            controller.set_role_config(
                AgentRole.TASK_AUDITOR,
                engine="hermes",
                project_profile="encomm-auditor",
            )
            controller.bind_external_session(
                AgentRole.TASK_AUDITOR,
                "hermes",
                "20260927_120000_aaaa",
                profile="encomm-auditor",
            )
            panel = SimpleModePanel(
                controller, profiles=("encomm-auditor", "encomm-accounting-intelligence")
            )
            assert (
                controller.state.config_for(AgentRole.TASK_AUDITOR)
                .external_session_binding()
                is not None
            )
            # The operator switches the Auditor profile → the old binding dies.
            index = panel.auditor_profile.findText("encomm-accounting-intelligence")
            assert index >= 0
            panel.auditor_profile.setCurrentIndex(index)
            panel._on_profile_changed(
                AgentRole.TASK_AUDITOR, panel._auditor_fields
            )
            assert (
                controller.state.config_for(AgentRole.TASK_AUDITOR)
                .external_session_binding()
                is None
            )
        finally:
            controller.registry = saved


class TestStatusPanel:
    def test_status_shows_batch_agent_and_sessions(
        self, qapp, database, tmp_path
    ) -> None:
        from encomm_pcc.ui.simple_mode import SimpleModePanel

        controller = PipelineController(database=database, event_log=NullEventLog())
        panel = SimpleModePanel(controller, profiles=())
        try:
            panel.continuous_active = True
            panel.refresh()
            assert "Idle" in panel.status_label.text()

            _seed_ready_batch(
                controller, tmp_path, orchestrator_session_id="arch-1234567890abcdef"
            )
            controller.state.batch.tasks[0].builder_session_id = "builder-9"
            controller.state.batch.tasks[0].auditor_session_id = "auditor-shared"
            panel.refresh()
            text = panel.status_label.text()
            assert "Architect session:" in text
            assert "arch-1234567890abcdef" in text or "…" in text
            assert "Continuous Run: ON" in text
        finally:
            panel.continuous_active = False

    def test_idle_status_is_operator_language(self, qapp, database) -> None:
        from encomm_pcc.ui.simple_mode import SimpleModePanel

        controller = PipelineController(database=database, event_log=NullEventLog())
        panel = SimpleModePanel(controller, profiles=())
        panel.refresh()
        assert "Idle — configure the pipeline and press START." in panel.status_label.text()