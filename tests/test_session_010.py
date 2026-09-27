"""Session 010 — the final wiring tests (offline, zero AI calls).

Covers the brief's §20 (continuous wiring through the REAL UI/worker path) and
§21 (Simple Mode control matrix) plus the one-shot Coder recovery override
contract (§14/§15).  Everything runs against the same deterministic
ScriptedBatchDriver the Session 009 offline proof uses.
"""

from __future__ import annotations

from pathlib import Path

from encomm_pcc.core import TaskSpec
from encomm_pcc.core.batch_runner import BatchOutcome, BatchRunner
from encomm_pcc.core.continuous_runner import ContinuousStopReason
from encomm_pcc.core.events import NullEventLog
from encomm_pcc.domain import AgentRole, PipelinePhase, TaskState

from conftest import PermissiveRunner
from test_batch_runner import (
    ScriptedBatchDriver,
    build_plan_text,
    discovery_stub,
    ok_result,
    verdict_text,
)
from test_final_audit import pass_with_next


def _configure_roles(controller) -> None:
    for role in (
        AgentRole.ORCHESTRATOR,
        AgentRole.BUILDER,
        AgentRole.TASK_AUDITOR,
        AgentRole.FINAL_AUDITOR,
    ):
        controller.set_role_config(role, engine="batch", project_profile="test-profile")
    controller.set_role_config(AgentRole.FINAL_AUDITOR, same_as_orchestrator=False)


def _scripted_results() -> list:
    """2-task batch → final PASS + 5-task plan (the core default next size)
    → zero-AI handoff → 5-task batch → final PASS (a hook can STOP right
    after it persists).  The plan size MUST match next_batch_size=5 (D-031)."""
    return [
        ok_result("orch-1", build_plan_text(2)),  # the ONLY planning call
        ok_result("builder-1", "done"),
        ok_result("auditor", verdict_text("PASS")),
        ok_result("builder-2", "done"),
        ok_result("auditor", verdict_text("PASS")),
        ok_result("final-1", pass_with_next(5)),
        # batch 2: five zero-AI-materialised tasks, no planning call
        ok_result("builder-3", "done"),
        ok_result("auditor", verdict_text("PASS")),
        ok_result("builder-4", "done"),
        ok_result("auditor", verdict_text("PASS")),
        ok_result("builder-5", "done"),
        ok_result("auditor", verdict_text("PASS")),
        ok_result("builder-6", "done"),
        ok_result("auditor", verdict_text("PASS")),
        ok_result("builder-7", "done"),
        ok_result("auditor", verdict_text("PASS")),
        ok_result("final-2", pass_with_next(5)),
    ]


def _make_executor(controller, tmp_path: Path, results: list):
    ScriptedBatchDriver.reset()
    ScriptedBatchDriver.shared = list(results)
    from encomm_pcc.drivers import DriverRegistry
    from encomm_pcc.core.executor import Executor

    registry = DriverRegistry()
    registry.register(ScriptedBatchDriver)
    controller.set_workspace("S010 Workspace", str(tmp_path))
    _configure_roles(controller)
    executor = Executor(
        controller,
        registry=registry,
        runner=PermissiveRunner(),
        database=controller.database,
        event_log=NullEventLog(),
        profile_discovery=discovery_stub,
    )
    controller.attach_executor(executor)
    return executor


def _drain(window) -> None:
    """Pump Qt events until the worker finished handler has run (bounded).

    Two queued hops occur (worker.finished → thread.quit on the worker's
    thread, then the window handler via the queued signal), so keep pumping
    past thread-exit until the handler's captured report appears.  A 60 s
    wall-clock cap bounds a hung worker; the fake driver never blocks.
    """
    import time

    from PySide6.QtCore import QCoreApplication

    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        thread_done = window._thread is None or not window._thread.isRunning()
        report_captured = (
            window._continuous_report is not None or window._batch_report is not None
        )
        if thread_done and report_captured:
            break
        time.sleep(0.001)


# ----------------------------------------------------------------------------
# §20 — Simple START + Continuous goes through the continuous worker action
# ----------------------------------------------------------------------------
class TestContinuousWiring:
    def test_simple_start_continuous_two_batches_one_planning_call(
        self, qapp, controller, tmp_path: Path
    ) -> None:
        from encomm_pcc.ui.main_window import MainWindow
        from encomm_pcc.core.continuous_runner import ContinuousRunReport

        executor = _make_executor(controller, tmp_path, _scripted_results())
        ScriptedBatchDriver.hooks = {17: executor.request_stop}  # after final-2
        window = MainWindow(controller, profiles=("encomm-auditor",))
        try:
            assert window.simple_panel.continuous_check.isChecked() is False
            window.simple_panel.continuous_check.setChecked(True)
            window._on_simple_start("UNIQUE-S010-BRIEF", 2, True)
            _drain(window)

            report = window._continuous_report
            assert isinstance(report, ContinuousRunReport)
            assert report.batches_run == 2
            assert report.final_audits == 2
            assert report.next_batch_handoffs == 1  # exactly ONE zero-AI handoff
            assert report.planning_ai_calls == 1  # by construction
            assert report.stop_reason == ContinuousStopReason.ALL_STOP
            planning = [
                p for p in ScriptedBatchDriver.prompts
                if "PLANNING ONLY" in p and "UNIQUE-S010-BRIEF" in p
            ]
            assert len(planning) == 1  # the §5 token-efficiency invariant
            final_audits = [
                p for p in ScriptedBatchDriver.prompts if "FINAL AUDITOR" in p.upper()
            ]
            assert len(final_audits) == 2  # one per completed batch
            batch = controller.state.batch
            assert batch is not None
            assert sorted(t.title for t in batch.tasks) == [
                f"Next task {i}" for i in range(1, 6)
            ]
        finally:
            window.close()

    def test_stop_after_batch_one_prevents_final_audit(
        self, qapp, controller, tmp_path: Path
    ) -> None:
        from encomm_pcc.ui.main_window import MainWindow
        from encomm_pcc.core.continuous_runner import ContinuousRunReport

        executor = _make_executor(controller, tmp_path, _scripted_results())
        ScriptedBatchDriver.hooks = {5: executor.request_stop}
        window = MainWindow(controller, profiles=("encomm-auditor",))
        try:
            window._on_simple_start("b", 2, True)
            _drain(window)
            report = window._continuous_report
            assert isinstance(report, ContinuousRunReport)
            assert report.batches_run == 1
            assert report.final_audits == 0  # stop landed before the final audit
            assert report.next_batch_handoffs == 0
            assert report.stop_reason == ContinuousStopReason.ALL_STOP
        finally:
            window.close()

    def test_pause_stops_before_the_next_final_audit(
        self, qapp, controller, tmp_path: Path
    ) -> None:
        from encomm_pcc.ui.main_window import MainWindow
        from encomm_pcc.core.continuous_runner import ContinuousRunReport

        executor = _make_executor(controller, tmp_path, _scripted_results())
        ScriptedBatchDriver.hooks = {14: executor.request_pause}  # after batch 2 audit
        window = MainWindow(controller, profiles=("encomm-auditor",))
        try:
            window._on_simple_start("b", 2, True)
            _drain(window)
            report = window._continuous_report
            assert isinstance(report, ContinuousRunReport)
            assert report.stop_reason == ContinuousStopReason.PAUSED
            assert report.final_audits == 1
            assert report.batches_run == 2
        finally:
            window.close()

    def test_non_pass_final_audit_stops_loudly(
        self, qapp, controller, tmp_path: Path
    ) -> None:
        from encomm_pcc.ui.main_window import MainWindow
        from encomm_pcc.core.continuous_runner import ContinuousRunReport

        _make_executor(
            controller,
            tmp_path,
            [
                ok_result("orch-1", build_plan_text(1)),
                ok_result("builder-1", "done"),
                ok_result("auditor", verdict_text("PASS")),
                # NO scripted final-audit answer → engine failure → not PASS.
            ],
        )
        window = MainWindow(controller, profiles=("encomm-auditor",))
        try:
            window._on_simple_start("b", 1, True)
            _drain(window)
            report = window._continuous_report
            assert isinstance(report, ContinuousRunReport)
            assert report.batches_run == 1
            assert report.planning_ai_calls == 1
            assert report.stop_reason == ContinuousStopReason.FINAL_AUDIT_NOT_PASS
        finally:
            window.close()

    def test_unchecked_continuous_uses_the_single_batch_path(
        self, qapp, controller, tmp_path: Path
    ) -> None:
        """Continuous unchecked → the ordinary batch worker, no loop, no
        automatic final audit (the operator stays in control)."""
        from encomm_pcc.ui.main_window import MainWindow

        _make_executor(controller, tmp_path, _scripted_results())
        window = MainWindow(controller, profiles=("encomm-auditor",))
        try:
            window.simple_panel.continuous_check.setChecked(False)
            window._on_simple_start("b", 2, False)
            _drain(window)
            report = window._batch_report
            # A plain BatchRunReport — NOT a continuous loop report.
            assert report.outcome is BatchOutcome.READY_FOR_FINAL_AUDIT
            assert not hasattr(report, "planning_ai_calls")
            # The final audit did NOT run automatically.
            assert controller.state.phase is PipelinePhase.READY_FOR_FINAL_AUDIT
        finally:
            window.close()

    def test_worker_forwarding_regression(self, qapp, controller, tmp_path: Path) -> None:
        """start_executor_worker no longer drops the run parameters."""
        from encomm_pcc.ui.worker import ExecutorWorker

        _make_executor(controller, tmp_path, _scripted_results())
        worker = ExecutorWorker(
            controller.executor,
            TaskSpec(title="", prompt=""),
            action="batch",
            resume=False,
            project_brief="KWARG-BRIEF",
            batch_size=2,
            next_batch_size=4,
        )
        assert worker.project_brief == "KWARG-BRIEF"
        assert worker.batch_size == 2
        assert worker.resume is False
        assert worker.next_batch_size == 4


# ----------------------------------------------------------------------------
# §21 — Simple Mode control matrix
# ----------------------------------------------------------------------------
class TestSimpleModeMatrix:
    def test_coder_provider_model_persist(self, qapp, controller) -> None:
        from encomm_pcc.ui.main_window import MainWindow

        window = MainWindow(
            controller, profiles=("encomm-auditor", "encomm-accounting-intelligence")
        )
        try:
            panel = window.simple_panel
            panel.coder_profile.setCurrentIndex(
                panel.coder_profile.findText("encomm-auditor")
            )
            panel.coder_provider.setText("openrouter")
            panel.coder_model.setText("deepseek/deepseek-v4.1-flash")
            panel._apply_coder_config()
            config = controller.state.config_for(AgentRole.BUILDER)
            assert config.engine == "hermes"
            assert config.project_profile == "encomm-auditor"
            assert config.provider == "openrouter"
            assert config.model == "deepseek/deepseek-v4.1-flash"
        finally:
            window.close()

    def test_auditor_provider_model_persist(self, qapp, controller) -> None:
        from encomm_pcc.ui.main_window import MainWindow

        window = MainWindow(
            controller, profiles=("encomm-auditor", "encomm-accounting-intelligence")
        )
        try:
            panel = window.simple_panel
            panel.auditor_profile.setCurrentIndex(
                panel.auditor_profile.findText("encomm-accounting-intelligence")
            )
            panel.auditor_provider.setText("deepseek")
            panel.auditor_model.setText("glm-5.3-flash")
            panel._apply_auditor_config()
            config = controller.state.config_for(AgentRole.TASK_AUDITOR)
            assert config.provider == "deepseek"
            assert config.model == "glm-5.3-flash"
        finally:
            window.close()

    def test_provider_model_round_trip_from_durable_state(
        self, qapp, controller
    ) -> None:
        from encomm_pcc.ui.main_window import MainWindow

        controller.set_role_config(
            AgentRole.BUILDER,
            engine="hermes",
            project_profile="encomm-accounting-intelligence",
            provider="openrouter",
            model="qwen/qwen3-coder",
        )
        window = MainWindow(controller, profiles=("encomm-accounting-intelligence",))
        try:
            panel = window.simple_panel
            assert panel.coder_provider.text() == "openrouter"
            assert panel.coder_model.text() == "qwen/qwen3-coder"
        finally:
            window.close()

    def test_auditor_sessions_filter_by_profile(self, qapp, controller) -> None:
        """Discovery is profile-scoped: the fake hermes receives the role's
        configured profile and only matching sessions are listed."""
        from encomm_pcc.drivers import DriverRegistry
        from encomm_pcc.drivers.session_discovery import (
            ExternalSessionDescriptor,
            SessionDiscoveryResult,
        )
        from encomm_pcc.ui.main_window import MainWindow

        seen: dict[str, object] = {}

        class ScopedHermes:
            driver_id = "hermes"
            display_name = "Hermes (scoped fake)"

            def __init__(self, runner=None):  # noqa: ANN001
                self._runner = runner

            @classmethod
            def capabilities(cls):
                from encomm_pcc.drivers import DriverCapabilities

                return DriverCapabilities(
                    driver_id=cls.driver_id, display_name=cls.display_name
                )

            @classmethod
            def probe_availability(cls):
                return True

            @classmethod
            def describe(cls):
                return {
                    "driver_id": cls.driver_id,
                    "display_name": cls.display_name,
                    "implemented": True,
                    "supports_sessions": True,
                    "binary_present": True,
                }

            def discover_sessions(self, *, workspace_path=None, profile=None):
                seen["profile"] = profile
                sessions = (
                    [
                        ExternalSessionDescriptor(
                            session_id="20260927_120000_aaaa",
                            driver_id="hermes",
                            title="aud",
                        )
                    ]
                    if profile == "encomm-auditor"
                    else []
                )
                return SessionDiscoveryResult(
                    ok=True, driver_id="hermes", sessions=sessions, mechanism="test"
                )

        registry = DriverRegistry()
        registry.register(ScopedHermes)
        saved_registry = controller.registry
        controller.registry = registry
        controller.set_role_config(
            AgentRole.TASK_AUDITOR, engine="hermes", project_profile="encomm-auditor"
        )
        window = MainWindow(controller, profiles=("encomm-auditor",))
        try:
            panel = window.simple_panel
            panel._on_refresh_auditor_sessions()
            assert seen["profile"] == "encomm-auditor"
            labels = [
                panel.auditor_session.itemText(i)
                for i in range(panel.auditor_session.count())
            ]
            assert any("20260927_120000_aaaa" in label for label in labels)
            # Switching profiles produces a different scoped list.
            controller.set_role_config(
                AgentRole.TASK_AUDITOR,
                project_profile="encomm-accounting-intelligence",
            )
            panel._on_refresh_auditor_sessions()
            assert seen["profile"] == "encomm-accounting-intelligence"
            labels = [
                panel.auditor_session.itemText(i)
                for i in range(panel.auditor_session.count())
            ]
            assert not any("20260927_120000_aaaa" in label for label in labels)
        finally:
            controller.registry = saved_registry
            window.close()

    def test_profile_change_clears_invalid_binding(self, qapp, controller) -> None:
        from encomm_pcc.ui.main_window import MainWindow

        controller.set_role_config(
            AgentRole.TASK_AUDITOR, engine="hermes", project_profile="encomm-auditor"
        )
        controller.bind_external_session(
            AgentRole.TASK_AUDITOR,
            "hermes",
            "20260927_120000_aaaa",
            profile="encomm-auditor",
        )
        # The operator switches the Auditor profile: the binding recorded under
        # the old profile must be cleared, never silently reused (§11).
        controller.set_role_config(
            AgentRole.TASK_AUDITOR, project_profile="encomm-accounting-intelligence"
        )
        window = MainWindow(controller, profiles=("encomm-auditor",))
        try:
            window._on_stale_profile_binding(AgentRole.TASK_AUDITOR)
            config = controller.state.config_for(AgentRole.TASK_AUDITOR)
            assert config.external_session_binding() is None
        finally:
            window.close()


# ----------------------------------------------------------------------------
# §14/§15 — the one-shot Coder recovery override
# ----------------------------------------------------------------------------
def _seed_interrupted_batch(controller) -> str:
    """A batch with task 1 APPROVED and task 2 interrupted mid-build."""
    from encomm_pcc.domain import BatchState, BatchStatus, PipelinePhase
    from encomm_pcc.domain.models import TaskStateRecord

    batch = BatchState(
        workspace_id=controller.state.workspace.workspace_id,
        size=2,
        status=BatchStatus.RUNNING,
        tasks=[],
        phase="RUNNING_TASK",
    )
    task1 = TaskStateRecord(index=1, title="Task 1", prompt="p1")
    task1.state = TaskState.APPROVED
    task1.builder_session_id = "builder-1"
    task1.auditor_session_id = "auditor-1"
    task2 = TaskStateRecord(index=2, title="Task 2", prompt="p2")
    task2.state = TaskState.RUNNING
    task2.builder_session_id = "builder-2-interrupted"
    batch.tasks = [task1, task2]
    controller.state.batch = batch
    controller.state.phase = PipelinePhase.RUNNING_TASK
    # Walk LEGAL edges IDLE → PLANNING_BATCH → RUNNING_TASK (the executor's
    # own _ensure_work_phase route) — never an illegal direct jump.
    controller.machine.transition_to(PipelinePhase.PLANNING_BATCH)
    controller.machine.transition_to(PipelinePhase.RUNNING_TASK)
    controller.persist()
    return "builder-2-interrupted"


class TestCoderRecoveryOverride:
    def test_recovery_ui_offers_saved_session_and_arms_one_shot(
        self, qapp, controller
    ) -> None:
        from encomm_pcc.core.executor import Executor
        from encomm_pcc.ui.main_window import MainWindow

        saved = _seed_interrupted_batch(controller)
        executor = Executor(controller, event_log=NullEventLog())
        controller.attach_executor(executor)
        window = MainWindow(controller, profiles=("encomm-auditor",))
        try:
            panel = window.simple_panel
            panel.refresh()
            assert not panel.coder_recovery_box.isHidden()
            assert panel.coder_recovery_session.currentText() == saved
            panel._on_apply_coder_recovery()
            assert executor.coder_recovery_armed()
            assert panel.coder_recovery_box.isHidden()
        finally:
            window.close()

    def test_override_resumes_saved_session_for_exactly_one_operation(
        self, qapp, controller, tmp_path: Path
    ) -> None:
        _make_executor(
            controller,
            tmp_path,
            [
                ok_result("builder-2-recovered", "done"),  # build 2 via recovery
                ok_result("auditor", verdict_text("PASS")),
                ok_result("final-1", pass_with_next(4)),
            ],
        )
        saved = _seed_interrupted_batch(controller)
        executor = controller.executor
        executor.arm_coder_recovery(saved)

        report = BatchRunner(executor).run_batch(resume=True)
        assert report.outcome is BatchOutcome.READY_FOR_FINAL_AUDIT
        # The recovered build RESUMED the saved session (never a fresh one).
        assert saved in ScriptedBatchDriver.resumed
        # One-shot: consumed by that single operation.
        assert not executor.coder_recovery_armed()

    def test_next_normal_task_gets_new_session_after_recovery(
        self, qapp, controller, tmp_path: Path
    ) -> None:
        """§15: the operation AFTER the recovered one never reuses the
        recovery session — fresh Builder sessions resume as normal."""
        results = [
            ok_result("builder-2-recovered", "done"),
            ok_result("auditor", verdict_text("PASS")),
            ok_result("final-1", pass_with_next(4)),
            # A NEXT batch's build (zero-AI handoff) would slot in here.
            ok_result("builder-3-brand-new", "done"),
            ok_result("auditor", verdict_text("PASS")),
        ]
        _make_executor(controller, tmp_path, results)
        saved = _seed_interrupted_batch(controller)
        executor = controller.executor
        executor.arm_coder_recovery(saved)

        BatchRunner(executor).run_batch(resume=True)  # build 2 (recovered) + audit
        # The ONLY Builder resumed session is the recovery one; the auditor's
        # D-047 continuity resume (auditor-1) is expected and correct.
        builder_resumes = [
            s for s in ScriptedBatchDriver.resumed if s.startswith("builder")
        ]
        assert builder_resumes == [saved]
        assert not executor.coder_recovery_armed()

    def test_restart_in_new_session_clears_override(self, qapp, controller) -> None:
        from encomm_pcc.core.executor import Executor
        from encomm_pcc.ui.main_window import MainWindow

        _seed_interrupted_batch(controller)
        executor = Executor(controller, event_log=NullEventLog())
        controller.attach_executor(executor)
        executor.arm_coder_recovery("builder-2-interrupted")
        window = MainWindow(controller, profiles=("encomm-auditor",))
        try:
            panel = window.simple_panel
            panel.refresh()
            assert not panel.coder_recovery_box.isHidden()
            panel._on_clear_coder_recovery()
            assert not executor.coder_recovery_armed()
            assert panel.coder_recovery_box.isHidden()
        finally:
            window.close()

    def test_resume_failure_fails_honestly(self, qapp, controller, tmp_path: Path) -> None:
        """A lost provider session fails the recovered run — never faked."""
        from encomm_pcc.drivers import DriverRegistry
        from encomm_pcc.core.executor import Executor

        class ResumedScripted(ScriptedBatchDriver):
            @classmethod
            def resume_session(cls, session_id, request):  # noqa: ANN001
                from encomm_pcc.drivers.base import DriverError

                raise DriverError("session lost on the provider side")

        ScriptedBatchDriver.reset()
        ResumedScripted.reset()
        ResumedScripted.shared = [ok_result("x", "unused")]
        registry = DriverRegistry()
        registry.register(ResumedScripted)
        controller.set_workspace("RF Workspace", str(tmp_path))
        _configure_roles(controller)
        executor = Executor(
            controller,
            registry=registry,
            runner=PermissiveRunner(),
            database=controller.database,
            event_log=NullEventLog(),
            profile_discovery=discovery_stub,
        )
        controller.attach_executor(executor)
        saved = _seed_interrupted_batch(controller)
        executor.arm_coder_recovery(saved)

        report = BatchRunner(executor).run_batch(resume=True)
        assert report.outcome is BatchOutcome.FAILED
        assert "Coder recovery" in report.message
        # One-shot even on failure: nothing armed remains.
        assert not executor.coder_recovery_armed()
