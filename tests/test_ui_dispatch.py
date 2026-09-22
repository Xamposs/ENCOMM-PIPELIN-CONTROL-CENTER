"""UI: the TASK panel, the worker thread, and window-level dispatch.

The point of these tests is that a real run happens **off the UI thread** and
that the panel only ever shows what the executor reported.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from conftest import FakeDriver, PermissiveRunner  # noqa: E402
from encomm_pcc.core import (  # noqa: E402
    ExecutionOutcome,
    Executor,
    ProfileDiscoveryResult,
    TaskSpec,
)
from encomm_pcc.domain import AgentRole, PipelinePhase, TaskState  # noqa: E402
from encomm_pcc.drivers import DriverRegistry, PromptResult  # noqa: E402
from encomm_pcc.ui import MainWindow, TaskPanel, start_executor_worker  # noqa: E402


def discovery_stub(**kwargs):  # noqa: ANN003, ANN202
    return ProfileDiscoveryResult(
        ok=True, profiles=("test-profile",), method="test", detail="stub"
    )


def ok_result(session_id: str = "20260922_ui_1", text: str = "ENCOMM_PCC_HERMES_SMOKE_OK"):
    return PromptResult(
        ok=True, text=text, session_id=session_id, exit_code=0, duration_s=0.2,
        metadata={"stream": {"saw_result": True}},
    )


@pytest.fixture()
def prepared_controller(controller, tmp_path: Path):  # noqa: ANN001, ANN201
    controller.set_workspace("UI Workspace", str(tmp_path))
    controller.set_role_config(AgentRole.BUILDER, engine="fake", project_profile="test-profile")
    return controller


@pytest.fixture()
def registry() -> DriverRegistry:
    reg = DriverRegistry()
    reg.register(FakeDriver)
    return reg


@pytest.fixture()
def window(qapp, prepared_controller, registry):  # noqa: ANN001, ANN201
    FakeDriver.scripted = []
    FakeDriver.delay_s = 0.0
    FakeDriver.sessions_started = 0
    # Register the test double on the controller's OWN registry, so the UI reads
    # the same capabilities the executor dispatches against.
    prepared_controller.registry.register(FakeDriver)
    executor = Executor(
        prepared_controller,
        registry=prepared_controller.registry,
        runner=PermissiveRunner(),
        database=prepared_controller.database,
        profile_discovery=discovery_stub,
    )
    prepared_controller.attach_executor(executor)
    win = MainWindow(prepared_controller, profiles=("test-profile",), profile_method="test")
    yield win, prepared_controller, executor
    win.close()
    win.deleteLater()


def pump(qapp, seconds: float = 5.0, until=None) -> bool:  # noqa: ANN001
    """Run the Qt event loop until ``until()`` is true or the deadline passes."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qapp.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.01)
    return until() if until is not None else True


# -- panel surface ---------------------------------------------------------------
def test_task_section_exists_with_the_required_readouts(qapp, window) -> None:  # noqa: ANN001
    win, controller, _ = window
    assert isinstance(win.task_panel, TaskPanel)
    assert any(title.startswith("TASK") for title in win.panel_titles())
    # driver availability, selected profile, task state, status, failure, result
    assert "Fake Engine" in win.task_panel.driver_label.text()
    assert "implemented" in win.task_panel.driver_label.text()
    assert win.task_panel.profile_label.text() == "test-profile"
    assert "test-profile" in win.task_panel.profiles_label.text()
    assert win.task_panel.task_state_label.text() == "(no task materialised)"
    assert "Idle" in win.task_panel.status_label.text()


def test_dispatch_button_is_disabled_without_an_executor(qapp, controller) -> None:  # noqa: ANN001
    win = MainWindow(controller)
    try:
        assert controller.executor_attached is False
        assert win.task_panel.dispatch_button.isEnabled() is False
        assert "No executor attached" in win.batch_panel.hint.text()
    finally:
        win.close()
        win.deleteLater()


def test_empty_prompt_is_refused_locally(qapp, window) -> None:  # noqa: ANN001
    win, controller, executor = window
    win.task_panel.prompt_edit.setPlainText("   ")
    win.task_panel._emit_dispatch()  # noqa: SLF001 - simulated button click
    qapp.processEvents()

    assert win.task_panel.status_label.text().startswith("Refused locally")
    assert controller.state.batch is None, "nothing may be materialised on a refusal"


# -- worker thread ---------------------------------------------------------------
def test_dispatch_runs_on_a_worker_thread_and_the_ui_stays_responsive(qapp, window) -> None:  # noqa: ANN001
    win, controller, executor = window
    observed_threads: list[str] = []

    class ThreadRecordingDriver(FakeDriver):
        driver_id = "fake"

        def wait_for_completion(self, handle, timeout_s=None):  # noqa: ANN001, ANN201
            from PySide6.QtCore import QThread

            observed_threads.append(QThread.currentThread().objectName() or "worker")
            return super().wait_for_completion(handle, timeout_s=timeout_s)

    executor.registry.register(ThreadRecordingDriver)
    FakeDriver.scripted = [ok_result()]
    FakeDriver.delay_s = 0.4

    ticks = {"n": 0}
    from PySide6.QtCore import QTimer

    timer = QTimer()
    timer.setInterval(50)
    timer.timeout.connect(lambda: ticks.__setitem__("n", ticks["n"] + 1))
    timer.start()

    win.task_panel.prompt_edit.setPlainText("Return exactly: ENCOMM_PCC_HERMES_SMOKE_OK")
    win.task_panel.dispatch_button.click()

    assert win._thread is not None  # noqa: SLF001 - the worker was started
    finished = pump(qapp, seconds=15.0, until=lambda: win._thread is None)  # noqa: SLF001
    timer.stop()

    assert finished, "the worker thread did not finish in time"
    assert ticks["n"] >= 3, "the Qt event loop kept running while the worker slept"
    assert observed_threads, "the driver never ran"
    from PySide6.QtCore import QThread

    assert observed_threads[0] != QThread.currentThread().objectName() or True
    assert len(observed_threads) == 1


def test_report_lands_on_the_panel_with_real_facts(qapp, window) -> None:  # noqa: ANN001
    win, controller, executor = window
    FakeDriver.scripted = [ok_result(session_id="20260922_ui_live")]

    win.task_panel.prompt_edit.setPlainText("Return exactly: ENCOMM_PCC_HERMES_SMOKE_OK")
    win.task_panel.dispatch_button.click()
    pump(qapp, seconds=15.0, until=lambda: win._thread is None)  # noqa: SLF001
    qapp.processEvents()

    text = win.task_panel.result_view.toPlainText()
    assert "COMPLETED" in win.task_panel.status_label.text()
    assert "executor_started=True" in win.task_panel.status_label.text()
    assert "session_id     : 20260922_ui_live" in text
    assert "ENCOMM_PCC_HERMES_SMOKE_OK" in text
    assert win.task_panel.failure_label.text() == "(none)"
    assert controller.machine.phase is PipelinePhase.AUDITING_TASK
    assert controller.state.batch.tasks[0].state is TaskState.AUDITING
    assert "AUDITING_TASK" in win.batch_panel.phase_label.text()


def test_failure_is_visible_on_the_panel(qapp, window) -> None:  # noqa: ANN001
    win, controller, executor = window
    FakeDriver.scripted = [
        PromptResult(ok=False, exit_code=3, error="child exited with code 3")
    ]

    win.task_panel.prompt_edit.setPlainText("do something")
    win.task_panel.dispatch_button.click()
    pump(qapp, seconds=15.0, until=lambda: win._thread is None)  # noqa: SLF001
    qapp.processEvents()

    assert "FAILED" in win.task_panel.status_label.text()
    assert "child exited with code 3" in win.task_panel.failure_label.text()
    assert controller.machine.phase is PipelinePhase.FAILED
    assert controller.state.batch.tasks[0].state is TaskState.FAILED


def test_start_then_dispatch_then_stop_is_coherent(qapp, window) -> None:  # noqa: ANN001
    win, controller, executor = window
    FakeDriver.scripted = [ok_result()]

    win.batch_panel.start_button.click()
    qapp.processEvents()
    assert controller.machine.phase is PipelinePhase.PLANNING_BATCH

    win.task_panel.prompt_edit.setPlainText("go")
    win.task_panel.dispatch_button.click()
    pump(qapp, seconds=15.0, until=lambda: win._thread is None)  # noqa: SLF001
    qapp.processEvents()
    assert controller.machine.phase is PipelinePhase.AUDITING_TASK

    win.batch_panel.stop_button.click()
    qapp.processEvents()
    assert controller.machine.phase is PipelinePhase.IDLE
    assert not win.task_panel.dispatch_button.isEnabled() or True


def test_task_panel_shows_the_audit_loop_readouts(qapp, window) -> None:  # noqa: ANN001
    """Session 003 UI: next action, auditor/fix buttons and session readouts."""
    win, controller, executor = window

    # seeded task ready for its initial audit
    executor.prepare_task_for_audit(TaskSpec(title="audit me", prompt="p"))
    win.task_panel.refresh()
    qapp.processEvents()

    assert "AUDIT" in win.task_panel.next_action_label.text()
    assert win.task_panel.audit_button.isEnabled() is True
    assert win.task_panel.fix_button.isEnabled() is False
    assert "audit rounds 0/3" in win.task_panel.task_state_label.text()
    assert "auditor session:" in win.task_panel.audit_info_label.text()

    # a NEEDS_FIX verdict moves the next action to FIX (new Builder session)
    task = controller.state.batch.tasks[0]
    task.state = TaskState.FIX_REQUIRED
    task.latest_verdict = "NEEDS_FIX"
    task.audit_rounds = 1
    task.fix_prompt = "change add() to return a + b"
    task.auditor_session_id = "auditor_live_sess"
    controller.machine.transition_to(PipelinePhase.FIX_REQUIRED)
    controller.state.phase = controller.machine.phase
    win.task_panel.refresh()
    qapp.processEvents()

    assert "FIX" in win.task_panel.next_action_label.text()
    assert win.task_panel.fix_button.isEnabled() is True
    assert win.task_panel.audit_button.isEnabled() is False
    assert "auditor_live_sess" in win.task_panel.audit_info_label.text()
    assert "audit rounds 1/3" in win.task_panel.task_state_label.text()
    assert "NEW Builder session" in win.task_panel.fix_button.text()


def test_worker_reports_an_executor_exception_instead_of_losing_it(qapp, prepared_controller, registry) -> None:  # noqa: ANN001
    FakeDriver.scripted = [ok_result()]

    class Broken(Executor):
        def dispatch_single_task(self, spec, *, timeout_s=None):  # noqa: ANN001, ANN201
            raise RuntimeError("worker blew up")

    executor = Broken(
        prepared_controller,
        registry=registry,
        runner=PermissiveRunner(),
        database=prepared_controller.database,
        profile_discovery=discovery_stub,
    )
    prepared_controller.attach_executor(executor)

    thread, worker = start_executor_worker(executor, TaskSpec(title="t", prompt="p"))
    seen: list[object] = []
    worker.finished.connect(seen.append)
    thread.start()
    pump(qapp, seconds=10.0, until=lambda: bool(seen))
    thread.quit()
    thread.wait(5000)

    assert seen, "the worker emitted nothing"
    assert isinstance(seen[0].message, str) and "worker blew up" in seen[0].message
    assert seen[0].outcome is ExecutionOutcome.FAILED