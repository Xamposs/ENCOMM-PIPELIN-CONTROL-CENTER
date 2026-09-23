"""Session 008 — packaged bootstrap / ``--smoke-test`` contract.

The smoke mode reuses the REAL bootstrap (no parallel app), makes zero model
calls, never touches production projects, and fails closed on an
incompatible database.
"""

from __future__ import annotations

import sqlite3

from encomm_pcc.app import SMOKE_TEST_FLAG, run_smoke_test
from encomm_pcc.core import DATA_DIR_ENV, AppPaths
from encomm_pcc.persistence import Database


def _redirect_home(monkeypatch, tmp_path) -> AppPaths:
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "smoke-home"))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    return AppPaths.resolve().ensure()


def test_smoke_flag_is_stable():
    assert SMOKE_TEST_FLAG == "--smoke-test"


def test_smoke_test_passes_over_a_redirected_home(monkeypatch, tmp_path):
    paths = _redirect_home(monkeypatch, tmp_path)
    assert run_smoke_test() == 0
    assert (paths.database).exists()
    assert (paths.logs_dir / "bootstrap.log").exists()


def test_smoke_test_never_writes_outside_its_home(monkeypatch, tmp_path):
    paths = _redirect_home(monkeypatch, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    before = sorted(p.name for p in outside.iterdir())
    assert run_smoke_test() == 0
    assert sorted(p.name for p in outside.iterdir()) == before
    assert paths.data_dir.is_relative_to(tmp_path)


def test_smoke_test_fails_closed_on_a_newer_schema(monkeypatch, tmp_path):
    paths = _redirect_home(monkeypatch, tmp_path)
    Database(paths.database).open().close()
    conn = sqlite3.connect(paths.database)
    conn.execute("UPDATE schema_meta SET value = '99' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    assert run_smoke_test() == 1


def test_smoke_test_is_repeatable(monkeypatch, tmp_path):
    _redirect_home(monkeypatch, tmp_path)
    assert run_smoke_test() == 0
    assert run_smoke_test() == 0


def test_restore_state_restores_the_persisted_phase(monkeypatch, tmp_path):
    """Session 008 (real-acceptance finding): the production restart path
    (``app.restore_state`` → ``build_controller``) must restore the machine
    phase from the batch row — same contract as
    ``Database.load_pipeline_state``.  Without this, a restart at
    READY_FOR_FINAL_AUDIT made RUN FINAL AUDIT impossible from the UI."""
    from encomm_pcc.app import restore_state
    from encomm_pcc.core import EventLog, PipelineController
    from encomm_pcc.domain import (
        BatchStatus,
        PipelinePhase,
        TaskState,
    )
    from encomm_pcc.domain.models import TaskStateRecord

    paths = _redirect_home(monkeypatch, tmp_path)
    db = Database(paths.database).open()
    events = EventLog(db)
    controller = PipelineController(database=db, event_log=events)
    controller.set_workspace("Phase restore probe", "")
    controller.request_start(1)
    batch = controller.state.batch
    batch.tasks = [
        TaskStateRecord(index=1, title="t", prompt="p", state=TaskState.APPROVED)
    ]
    db.save_batch(batch)
    controller.transition("RUNNING_TASK", message="leg 1")
    controller.transition("AUDITING_TASK", message="audit boundary")
    controller.transition("READY_FOR_FINAL_AUDIT", message="acceptance boundary")
    batch_id = batch.batch_id
    db.close()

    # -- restart: fresh controller over the same DB -------------------------
    db2 = Database(paths.database).open()
    state = restore_state(db2)
    assert state is not None and state.batch is not None
    assert state.phase is PipelinePhase.READY_FOR_FINAL_AUDIT
    events2 = EventLog(db2)
    fresh = PipelineController(database=db2, event_log=events2, state=state)
    assert fresh.machine.phase is PipelinePhase.READY_FOR_FINAL_AUDIT
    db2.close()
