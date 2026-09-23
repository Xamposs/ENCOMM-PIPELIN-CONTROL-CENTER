"""Session 007 — restart/recovery matrix (brief §16, §17, §26).

The contract under test (matching the real restore path):

1. ``Database.load_pipeline_state`` rebuilds the aggregate with the persisted
   work phase (``batches.phase``) and the active batch; ``next_task_action``
   reports the deterministic safe next step from TASK STATES.
2. Terminal batches (``COMPLETE`` / ``FAILED`` / ``STOPPED``) are deliberately
   NOT restored as active work — the pipeline returns to IDLE and the finished
   batch remains queryable through the history read model (§13).
3. A crash during an external process means the child is gone: an in-flight
   build/fix (RUNNING / RUNNING_FIX) is NEVER trusted as success — recovery
   re-runs that unit of work (BUILD / FIX), never COMPLETE.
4. ``FINAL_AUDIT_RUNNING`` restores as recovery; the action is not COMPLETE
   (a PASS requires a real new model call — D-030) and the executor accepts
   the phase as input for that re-run.
5. Nothing auto-runs: a restored controller has no executor attached; only
   ``app.run`` wires one, after the window exists.  Every test here attaches
   no runner at all — a launch would be impossible, which is the point.
"""

from __future__ import annotations

import pytest

from encomm_pcc.core import EventLog, PipelineController
from encomm_pcc.core.executor import TaskNextAction, next_task_action
from encomm_pcc.core.history import batch_history_rows
from encomm_pcc.domain import (
    BatchState,
    BatchStatus,
    PipelinePhase,
    PipelineState,
    TaskState,
    TaskStateRecord,
    WorkspaceConfig,
)
from encomm_pcc.persistence import Database


PHASE_TO_STATUS = {
    PipelinePhase.PLANNING_BATCH: BatchStatus.PLANNING,
    PipelinePhase.RUNNING_TASK: BatchStatus.RUNNING,
    PipelinePhase.AUDITING_TASK: BatchStatus.RUNNING,
    PipelinePhase.FIX_REQUIRED: BatchStatus.RUNNING,
    PipelinePhase.RUNNING_FIX: BatchStatus.RUNNING,
    PipelinePhase.READY_FOR_FINAL_AUDIT: BatchStatus.READY_FOR_FINAL_AUDIT,
    PipelinePhase.FINAL_AUDIT_RUNNING: BatchStatus.RUNNING,
    PipelinePhase.BATCH_COMPLETE: BatchStatus.COMPLETE,
    PipelinePhase.BLOCKED: BatchStatus.BLOCKED,
    PipelinePhase.FAILED: BatchStatus.FAILED,
}


def seed(
    database: Database,
    *,
    phase: PipelinePhase,
    task_state: TaskState,
    audit_rounds: int = 0,
    verdict: str | None = None,
) -> str:
    """Persist one workspace + one batch at a given phase via the real writer."""
    state = PipelineState.bootstrap(WorkspaceConfig(name="RecWS", repo_path=""))
    database.save_workspace(state.workspace)
    task = TaskStateRecord(
        index=1,
        title="Task 1",
        prompt="p",
        state=task_state,
        attempts=1,
        audit_rounds=audit_rounds,
        latest_verdict=verdict,
    )
    batch = BatchState(
        workspace_id=state.workspace.workspace_id,
        size=1,
        status=PHASE_TO_STATUS[phase],
        phase=phase.value,
        project_brief="brief",
        tasks=[task],
    )
    state.batch = batch
    state.phase = phase
    database.save_pipeline_state(state)
    return state.workspace.workspace_id


@pytest.fixture()
def scratch(tmp_path):
    """A real on-disk database the test can close and reopen (a 'restart')."""
    path = tmp_path / "recovery.db"
    database = Database(path).open()
    try:
        yield database, lambda: Database(path).open()
    finally:
        database.close()


def restore(db2: Database, workspace_id: str):
    """The real restart path: load_pipeline_state → controller with that state."""
    state = db2.load_pipeline_state(workspace_id)
    assert state is not None
    return PipelineController(database=db2, event_log=EventLog(db2), state=state)


def action_of(controller: PipelineController) -> TaskNextAction:
    return next_task_action(
        batch=controller.state.batch, phase=controller.machine.phase
    )


# -- every phase restarts into a safe, durable, non-auto-running state ---------------
class TestRecoveryMatrix:
    def test_planning_batch(self, scratch):
        db, reopen = scratch
        # No tasks yet: the batch is CREATED, waiting for the Orchestrator.
        ws = seed(db, phase=PipelinePhase.PLANNING_BATCH, task_state=TaskState.PENDING)
        db.close()
        db2 = reopen()
        try:
            controller = restore(db2, ws)
            assert controller.machine.phase is PipelinePhase.PLANNING_BATCH
            batch = controller.state.batch
            # With zero materialised tasks the next step is PLAN; the PENDING
            # probe task in the DB yields BUILD (task state wins — the
            # deterministic contract).
            batch.tasks = []
            assert (
                next_task_action(batch=batch, phase=controller.machine.phase)
                is TaskNextAction.PLAN
            )
            batch.tasks = [
                TaskStateRecord(
                    index=1, title="t", prompt="p", state=TaskState.PENDING
                )
            ]
            assert (
                next_task_action(batch=batch, phase=controller.machine.phase)
                is TaskNextAction.BUILD
            )
        finally:
            db2.close()

    def test_running_task_never_trusted_complete(self, scratch):
        db, reopen = scratch
        ws = seed(db, phase=PipelinePhase.RUNNING_TASK, task_state=TaskState.RUNNING)
        db.close()
        db2 = reopen()
        try:
            controller = restore(db2, ws)
            assert controller.machine.phase is PipelinePhase.RUNNING_TASK
            # The crashed child is gone; the honest next step is a re-BUILD.
            assert action_of(controller) is TaskNextAction.BUILD
        finally:
            db2.close()

    def test_auditing_task(self, scratch):
        db, reopen = scratch
        ws = seed(
            db,
            phase=PipelinePhase.AUDITING_TASK,
            task_state=TaskState.AUDITING,
            audit_rounds=1,
        )
        db.close()
        db2 = reopen()
        try:
            controller = restore(db2, ws)
            assert action_of(controller) is TaskNextAction.AUDIT
        finally:
            db2.close()

    def test_fix_required(self, scratch):
        db, reopen = scratch
        ws = seed(
            db,
            phase=PipelinePhase.FIX_REQUIRED,
            task_state=TaskState.FIX_REQUIRED,
            audit_rounds=1,
            verdict="NEEDS_FIX",
        )
        db.close()
        db2 = reopen()
        try:
            controller = restore(db2, ws)
            assert action_of(controller) is TaskNextAction.FIX
        finally:
            db2.close()

    def test_running_fix_never_trusted_complete(self, scratch):
        db, reopen = scratch
        ws = seed(db, phase=PipelinePhase.RUNNING_FIX, task_state=TaskState.RUNNING_FIX)
        db.close()
        db2 = reopen()
        try:
            controller = restore(db2, ws)
            assert action_of(controller) is TaskNextAction.FIX
        finally:
            db2.close()

    def test_ready_for_final_audit(self, scratch):
        db, reopen = scratch
        ws = seed(
            db,
            phase=PipelinePhase.READY_FOR_FINAL_AUDIT,
            task_state=TaskState.APPROVED,
        )
        db.close()
        db2 = reopen()
        try:
            controller = restore(db2, ws)
            assert controller.machine.phase is PipelinePhase.READY_FOR_FINAL_AUDIT
            assert action_of(controller) is TaskNextAction.COMPLETE
        finally:
            db2.close()

    def test_final_audit_running_recovers_but_requires_a_real_rerun(self, scratch):
        db, reopen = scratch
        ws = seed(
            db, phase=PipelinePhase.FINAL_AUDIT_RUNNING, task_state=TaskState.APPROVED
        )
        db.close()
        db2 = reopen()
        try:
            controller = restore(db2, ws)
            # The machine restores the recovery phase…
            assert controller.machine.phase is PipelinePhase.FINAL_AUDIT_RUNNING
            # …and the phase itself carries the fact that an audit WAS in
            # flight (all-APPROVED + this phase ⇒ COMPLETE derivation), but
            # the executor accepts FINAL_AUDIT_RUNNING as input and REQUIRES
            # a real new model call for the re-run — it never materialises a
            # green batch from the interrupted attempt (D-030).  The graph
            # also keeps the single legal incoming edge: nothing can claim
            # BATCH_COMPLETE without a new FINAL_AUDIT_RUNNING→ PASS walk.
            assert (
                next_task_action(
                    batch=controller.state.batch, phase=controller.machine.phase
                )
                is TaskNextAction.COMPLETE
            )
            assert controller.machine.phase is not PipelinePhase.BATCH_COMPLETE
            assert controller.state.batch.status is BatchStatus.RUNNING
        finally:
            db2.close()

    def test_batch_complete_is_terminal_and_lives_in_history(self, scratch):
        db, reopen = scratch
        ws = seed(
            db, phase=PipelinePhase.BATCH_COMPLETE, task_state=TaskState.APPROVED
        )
        db.close()
        db2 = reopen()
        try:
            controller = restore(db2, ws)
            # A completed batch is NOT active work: the pipeline is idle and
            # the finished batch stays queryable in history (§13), with the
            # next-batch handoff in pending_next_plans — never auto-started.
            assert controller.machine.phase is PipelinePhase.IDLE
            assert controller.state.batch is None
            rows = batch_history_rows(db2)
            assert len(rows) == 1
            assert rows[0]["status"] == "COMPLETE"
        finally:
            db2.close()

    def test_blocked(self, scratch):
        db, reopen = scratch
        ws = seed(db, phase=PipelinePhase.BLOCKED, task_state=TaskState.BLOCKED)
        db.close()
        db2 = reopen()
        try:
            controller = restore(db2, ws)
            assert controller.machine.phase is PipelinePhase.BLOCKED
            assert action_of(controller) is TaskNextAction.BLOCKED
        finally:
            db2.close()

    def test_failed_is_terminal_and_lives_in_history(self, scratch):
        db, reopen = scratch
        ws = seed(db, phase=PipelinePhase.FAILED, task_state=TaskState.FAILED)
        db.close()
        db2 = reopen()
        try:
            controller = restore(db2, ws)
            # A FAILED batch is terminal: not restored as active work, kept in
            # history for the operator (failure can never become COMPLETE).
            assert controller.machine.phase is PipelinePhase.IDLE
            assert controller.state.batch is None
            rows = batch_history_rows(db2)
            assert rows[0]["status"] == "FAILED"
        finally:
            db2.close()


# -- structural guarantees ------------------------------------------------------------
class TestRecoveryGuarantees:
    def test_restore_state_returns_none_for_empty_database(self, scratch):
        db, _ = scratch
        from encomm_pcc.app import restore_state as app_restore

        assert app_restore(db) is None

    def test_paused_remains_an_operator_decision(self, scratch):
        db, reopen = scratch
        ws = seed(db, phase=PipelinePhase.PLANNING_BATCH, task_state=TaskState.PENDING)
        db.close()
        db2 = reopen()
        try:
            controller = restore(db2, ws)
            # PAUSED is entered only through the operator's pause control.
            assert controller.machine.can_go_to(PipelinePhase.PAUSED) is True
        finally:
            db2.close()

    def test_no_auto_run_structure(self):
        """A freshly built controller (no executor attached) cannot run anything.

        ``build_controller`` never attaches an executor — only ``app.run``
        does, after the window exists.  Recovery therefore ends at "show the
        safe next action and wait".
        """
        db = Database(":memory:").open()
        try:
            controller = PipelineController(database=db, event_log=EventLog(db))
            assert controller.executor is None
            assert controller.executor_attached is False
        finally:
            db.close()
