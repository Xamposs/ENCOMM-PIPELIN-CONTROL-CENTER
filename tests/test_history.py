"""Session 007 — run/batch history matrix (brief §24).

Covers the read model (``core/history.py``) and the HistoryPanel surface:
no history, one batch, multiple batches, completed/blocked/failed batches,
pending + consumed next plans, final findings, session ids, reload after
restart, newest-first ordering, bounded text rendering.
"""

from __future__ import annotations

import pytest

from encomm_pcc.core.history import (
    HISTORY_TEXT_CAP,
    batch_history_detail,
    batch_history_rows,
)
from encomm_pcc.domain import (
    AgentRole,
    BatchPlan,
    BatchState,
    BatchStatus,
    PipelineState,
    TaskState,
    TaskStateRecord,
    WorkspaceConfig,
)
from encomm_pcc.persistence import Database


def seed_batch(
    database: Database,
    state: PipelineState,
    *,
    batch_id: str = "",
    status: BatchStatus = BatchStatus.COMPLETE,
    tasks: int = 2,
    brief: str = "A" * 300,
    task_state: TaskState = TaskState.APPROVED,
) -> BatchState:
    batch = BatchState(
        workspace_id=state.workspace.workspace_id,
        size=tasks,
        status=status,
        project_brief=brief,
    )
    if batch_id:
        batch.batch_id = batch_id
    batch.tasks = [
        TaskStateRecord(
            index=i + 1,
            title=f"Task {i + 1}",
            prompt=f"prompt {i + 1}",
            state=task_state,
            attempts=1,
            audit_rounds=1,
            builder_session_id=f"sess_b{i + 1}",
            auditor_session_id=f"sess_a{i + 1}",
        )
        for i in range(tasks)
    ]
    state.batch = batch
    database.save_pipeline_state(state)
    return batch


@pytest.fixture()
def state() -> PipelineState:
    return PipelineState.bootstrap(WorkspaceConfig(name="HistoryWS", repo_path=""))


@pytest.fixture()
def db() -> Database:
    database = Database(":memory:").open()
    try:
        yield database
    finally:
        database.close()


class TestHistoryReadModel:
    def test_no_history(self, db, state):
        db.save_workspace(state.workspace)
        assert batch_history_rows(db) == []

    def test_one_batch_row_shape(self, db, state):
        db.save_workspace(state.workspace)
        batch = seed_batch(db, state)
        rows = batch_history_rows(db)
        assert len(rows) == 1
        row = rows[0]
        assert row["batch_id"] == batch.batch_id
        assert row["status"] == "COMPLETE"
        assert row["task_count"] == 2
        assert row["approved_count"] == 2
        assert row["workspace_name"] == "HistoryWS"

    def test_multiple_batches_newest_first(self, db, state):
        db.save_workspace(state.workspace)
        first = seed_batch(db, state, brief="first")
        # Force a distinct created_at ordering (second created later).
        import time

        time.sleep(0.01)
        second = seed_batch(db, state, brief="second")
        rows = batch_history_rows(db)
        assert len(rows) == 2
        assert rows[0]["batch_id"] == second.batch_id
        assert rows[1]["batch_id"] == first.batch_id

    def test_completed_batch_with_final_verdict_and_sessions(self, db, state):
        db.save_workspace(state.workspace)
        batch = seed_batch(db, state)
        db.save_batch_plan(
            batch.batch_id,
            __import__("encomm_pcc.domain", fromlist=["BatchPlanRecord"]).BatchPlanRecord(
                plan=BatchPlan(
                    batch_title="T",
                    batch_objective="O",
                    tasks=[],
                ),
                project_brief="brief",
                requested_size=2,
                plan_status="PLANNED",
                orchestrator_session_id="sess_orch_1",
                final_phase="BATCH_COMPLETE",
            ),
        )
        db.save_final_audit(
            batch.batch_id,
            verdict="PASS",
            summary="all good",
            findings_json="[]",
            audit_json="{}",
            auditor_session_id="sess_final_1",
            next_plan_id=None,
        )
        row = batch_history_rows(db)[0]
        assert row["final_verdict"] == "PASS"
        assert row["orchestrator_session_id"] == "sess_orch_1"
        assert row["final_auditor_session_id"] == "sess_final_1"

        detail = batch_history_detail(db, batch.batch_id)
        assert detail["final_verdict"] == "PASS"
        assert detail["final_summary"] == "all good"
        assert detail["tasks"][0]["builder_session_id"] == "sess_b1"
        assert detail["tasks"][0]["auditor_session_id"] == "sess_a1"

    def test_blocked_and_failed_batches_appear(self, db, state):
        db.save_workspace(state.workspace)
        seed_batch(db, state, brief="b1", status=BatchStatus.BLOCKED, task_state=TaskState.BLOCKED)
        seed_batch(db, state, brief="b2", status=BatchStatus.FAILED, task_state=TaskState.FAILED)
        rows = batch_history_rows(db)
        assert {r["status"] for r in rows} == {"BLOCKED", "FAILED"}

    def test_pending_next_plan_reported_open(self, db, state):
        db.save_workspace(state.workspace)
        batch = seed_batch(db, state)
        db.save_pending_next_plan(
            plan_id="nextplan_x",
            source_batch_id=batch.batch_id,
            requested_size=4,
            plan_json="{}",
        )
        row = batch_history_rows(db)[0]
        assert row["next_plan_open"] is True
        detail = batch_history_detail(db, batch.batch_id)
        assert detail["next_plan"]["plan_id"] == "nextplan_x"
        assert detail["next_plan"]["consumed_at"] is None

    def test_consumed_next_plan_reported_consumed(self, db, state):
        db.save_workspace(state.workspace)
        batch = seed_batch(db, state)
        db.save_pending_next_plan(
            plan_id="nextplan_y",
            source_batch_id=batch.batch_id,
            requested_size=4,
            plan_json="{}",
        )
        db.mark_pending_next_plan_consumed("nextplan_y", "batch_next")
        detail = batch_history_detail(db, batch.batch_id)
        assert detail["next_plan"]["consumed_at"] is not None
        assert detail["next_plan"]["consumed_batch_id"] == "batch_next"

    def test_unknown_batch_id_returns_none(self, db, state):
        db.save_workspace(state.workspace)
        assert batch_history_detail(db, "batch_nope") is None

    def test_long_text_is_bounded(self, db, state):
        db.save_workspace(state.workspace)
        seed_batch(db, state, brief="Z" * 5000)
        detail = batch_history_detail(db, batch_history_rows(db)[0]["batch_id"])
        assert len(detail["project_brief"]) <= HISTORY_TEXT_CAP + 40

    def test_reload_after_restart(self, db, state, tmp_path):
        path = tmp_path / "hist.db"
        database = Database(path).open()
        st = PipelineState.bootstrap(WorkspaceConfig(name="R", repo_path=""))
        database.save_workspace(st.workspace)
        seed_batch(database, st)
        database.close()

        db2 = Database(path).open()
        try:
            assert len(batch_history_rows(db2)) == 1
        finally:
            db2.close()


class TestHistoryPanel:
    def test_panel_lists_batches_and_detail(self, qapp, db, state):
        from encomm_pcc.core import PipelineController
        from encomm_pcc.ui import HistoryPanel

        database = db
        database.save_workspace(state.workspace)
        seed_batch(database, state)
        events = __import__("encomm_pcc.core", fromlist=["EventLog"]).EventLog(database)
        controller = PipelineController(database=database, event_log=events)
        panel = HistoryPanel(controller)
        assert panel.batch_list.count() == 1
        assert "durable detail" in panel.detail_view.toPlainText() or "batch id" in panel.detail_view.toPlainText()

        # Selecting the row renders the detail view.
        panel.batch_list.setCurrentRow(0)
        assert "batch id" in panel.detail_view.toPlainText()
        assert "Task 1" in panel.detail_view.toPlainText()

    def test_panel_empty_state(self, qapp, db):
        from encomm_pcc.core import EventLog, PipelineController
        from encomm_pcc.ui import HistoryPanel

        events = EventLog(db)
        controller = PipelineController(database=db, event_log=events)
        panel = HistoryPanel(controller)
        assert panel.batch_list.count() == 0
        assert panel.empty_label.isVisibleTo(panel)
