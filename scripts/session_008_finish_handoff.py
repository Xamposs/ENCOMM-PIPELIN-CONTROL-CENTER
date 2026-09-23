#!/usr/bin/env python3
"""Finish run C: reload the completed batch, START NEXT BATCH (zero AI calls)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from encomm_pcc.app import restore_state  # noqa: E402
from encomm_pcc.core import EventLog, Executor, PipelineController  # noqa: E402
from encomm_pcc.drivers import SubprocessRunner  # noqa: E402
from encomm_pcc.domain import BatchStatus, TaskState  # noqa: E402
from encomm_pcc.persistence import Database  # noqa: E402

scratch = Path(sys.argv[1])
db_path = scratch / "data" / "pipeline_control_center.db"

database = Database(db_path).open()
events = EventLog(database)
state = restore_state(database)
controller = PipelineController(database=database, event_log=events, state=state)

# The completed batch is terminal; find it and prove its durability.
batches = database.list_workspaces()
ws = state.workspace
# terminal batches live in history — load directly by id
import sqlite3

row = database.connection.execute(
    "SELECT batch_id FROM batches WHERE status='COMPLETE' ORDER BY rowid DESC LIMIT 1"
).fetchone()
batch_id = row[0]
stored = database.load_batch(batch_id)
final = database.load_final_audit(batch_id)
print("batch id          :", batch_id)
print("status            :", stored.status.value)
print("final verdict     :", final["final_verdict"])
print("final auditor     :", final["final_auditor_session_id"])
assert stored.status is BatchStatus.COMPLETE
assert final["final_verdict"] == "PASS"

# fresh controller + executor (the operator's restart), then START NEXT BATCH
executor = Executor(
    controller,
    runner=SubprocessRunner(),
    database=database,
    event_log=events,
)
controller.attach_executor(executor)
handoff = executor.start_next_batch()
print("handoff outcome   :", handoff.outcome.value)
print("handoff tasks     :", handoff.task_count)
new_tasks = {t.index: t.state.value for t in controller.state.batch.tasks}
print("new batch states  :", new_tasks)
assert handoff.outcome.value == "READY" and handoff.task_count == 4
assert set(new_tasks.values()) == {TaskState.PENDING.value}
assert len(new_tasks) == 4

# nothing auto-starts: the new batch sits waiting for the operator
print("START_NEXT_BATCH materialised 4 PENDING tasks with ZERO AI calls")
print("ACCEPTANCE HANDOFF PASSED")
database.close()
