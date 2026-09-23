"""Session 007 — ``app_events`` bounded retention matrix (brief §25).

Policy (documented in ``core/config.py`` + ADR-0041): deterministic
count-based retention, ``MAX_APP_EVENTS = 10_000`` newest rows preserved,
opportunistic cleanup (checked per append, executed at most once per
``EVENT_PRUNE_INTERVAL = 500`` appends and only when over the bound).
Retention touches ONLY ``app_events`` — batch/task/plan/audit rows are never
pruned.

Tests use a small injected bound via ``monkeypatch`` so the real 10k bound
stays production-facing while the hysteresis behaviour is exercised
deterministically.
"""

from __future__ import annotations

import pytest

from encomm_pcc.core import EventLog
from encomm_pcc.core.config import EVENT_PRUNE_INTERVAL, MAX_APP_EVENTS
from encomm_pcc.persistence import Database


@pytest.fixture()
def db() -> Database:
    database = Database(":memory:").open()
    try:
        yield database
    finally:
        database.close()


def emit(log: EventLog, n: int, prefix: str = "e") -> None:
    for i in range(n):
        log.info(f"{prefix}{i}")


class TestEventRetention:
    def test_constants_are_sane(self):
        assert 1000 <= MAX_APP_EVENTS <= 100_000
        assert 1 <= EVENT_PRUNE_INTERVAL <= MAX_APP_EVENTS

    def test_below_threshold_no_deletion(self, db, monkeypatch):
        monkeypatch.setattr(
            "encomm_pcc.core.config.MAX_APP_EVENTS", 100, raising=False
        )
        monkeypatch.setattr(
            "encomm_pcc.core.config.EVENT_PRUNE_INTERVAL", 10, raising=False
        )
        log = EventLog(db)
        emit(log, 50)
        assert db.event_count() == 50

    def test_above_threshold_oldest_removed_newest_preserved(self, db, monkeypatch):
        monkeypatch.setattr(
            "encomm_pcc.core.config.MAX_APP_EVENTS", 100, raising=False
        )
        monkeypatch.setattr(
            "encomm_pcc.core.config.EVENT_PRUNE_INTERVAL", 10, raising=False
        )
        log = EventLog(db)
        emit(log, 260)
        assert db.event_count() == 100
        newest = db.recent_events(limit=1)[0]
        assert newest["message"].startswith("e2")

    def test_exact_threshold_keeps_everything(self, db, monkeypatch):
        monkeypatch.setattr(
            "encomm_pcc.core.config.MAX_APP_EVENTS", 120, raising=False
        )
        monkeypatch.setattr(
            "encomm_pcc.core.config.EVENT_PRUNE_INTERVAL", 10, raising=False
        )
        log = EventLog(db)
        emit(log, 240)
        # At the bound exactly: nothing to delete (hysteresis keeps it at 120).
        assert db.event_count() == 120

    def test_prune_runs_at_most_once_per_interval(self, db, monkeypatch):
        monkeypatch.setattr(
            "encomm_pcc.core.config.MAX_APP_EVENTS", 50, raising=False
        )
        monkeypatch.setattr(
            "encomm_pcc.core.config.EVENT_PRUNE_INTERVAL", 25, raising=False
        )
        log = EventLog(db)
        calls = {"n": 0}
        original = db.prune_app_events

        def counting_prune(keep: int) -> int:
            calls["n"] += 1
            return original(keep)

        db.prune_app_events = counting_prune  # type: ignore[method-assign]
        emit(log, 200)
        # 200 appends / interval 25 → at most 8 prune opportunities.
        assert calls["n"] <= 8
        assert db.event_count() == 50

    def test_prune_disabled_flag_stops_cleanup(self, db, monkeypatch):
        monkeypatch.setattr(
            "encomm_pcc.core.config.MAX_APP_EVENTS", 50, raising=False
        )
        monkeypatch.setattr(
            "encomm_pcc.core.config.EVENT_PRUNE_INTERVAL", 5, raising=False
        )
        log = EventLog(db, retention_enabled=False)
        emit(log, 120)
        assert db.event_count() == 120  # unbounded, exactly as before 007

    def test_in_memory_log_unaffected(self, db):
        log = EventLog(db, retention_enabled=False)
        emit(log, 30)
        assert len(log) == 30
        assert len(log.history()) == 30

    def test_retention_survives_reopen(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "encomm_pcc.core.config.MAX_APP_EVENTS", 40, raising=False
        )
        monkeypatch.setattr(
            "encomm_pcc.core.config.EVENT_PRUNE_INTERVAL", 8, raising=False
        )
        path = tmp_path / "retention.db"
        db = Database(path).open()
        log = EventLog(db)
        emit(log, 150)
        db.close()

        db2 = Database(path).open()
        try:
            # Hysteresis invariant: the table never exceeds bound + interval
            # (a prune fires at most once per EVENT_PRUNE_INTERVAL appends).
            assert 40 <= db2.event_count() <= 40 + 8
            log2 = EventLog(db2)
            emit(log2, 30)  # over the bound again → next window prunes to 40
            assert 40 <= db2.event_count() <= 40 + 8
        finally:
            db2.close()

    def test_retention_never_touches_batches_tasks_or_audits(self, db, monkeypatch):
        from encomm_pcc.domain import (
            AgentRole,
            BatchState,
            PipelineState,
            TaskState,
            TaskStateRecord,
            WorkspaceConfig,
        )

        monkeypatch.setattr(
            "encomm_pcc.core.config.MAX_APP_EVENTS", 30, raising=False
        )
        monkeypatch.setattr(
            "encomm_pcc.core.config.EVENT_PRUNE_INTERVAL", 5, raising=False
        )

        state = PipelineState.bootstrap(WorkspaceConfig(name="WS", repo_path=""))
        task = TaskStateRecord(index=1, title="t", prompt="p", state=TaskState.APPROVED)
        batch = BatchState(
            workspace_id=state.workspace.workspace_id,
            size=1,
            project_brief="brief",
            tasks=[task],
        )
        state.batch = batch
        db.save_pipeline_state(state)

        log = EventLog(db)
        emit(log, 200)

        assert db.event_count() <= 30  # events were pruned…
        reloaded = db.load_batch(batch.batch_id)
        assert reloaded is not None
        assert reloaded.tasks[0].state is TaskState.APPROVED  # …tasks never
        assert len(db.list_workspaces()) == 1
        assert db.load_batch_plan(batch.batch_id) is None  # untouched (none existed)
