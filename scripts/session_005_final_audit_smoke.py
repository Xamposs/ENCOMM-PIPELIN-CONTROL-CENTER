"""SESSION 005 REAL SMOKE — the ONE-call Final Audit + next-batch handoff.

Not part of the unit suite: real mode talks to a real engine.  Run it
explicitly::

    python scripts/session_005_final_audit_smoke.py --profile encomm-pipeline-control-center \
        --provider openrouter --model deepseek/deepseek-v4-flash-0731

Design (brief §14–§17):

* a scratch git repo ALREADY represents a completed 4-task batch
  (feature_1..4.py + four deterministic tests, all committed, all passing);
* the Control Center state is seeded as durable fixtures: Project Brief,
  strict BatchPlan, four APPROVED task rows with clearly-fake historical
  session ids (``fake-*``), a Batch Summary, phase READY_FOR_FINAL_AUDIT;
* `--fake` proves every post-processing path with an in-process driver
  (zero cost) BEFORE any real call;
* the ONLY live model operation is ONE Final Auditor call which must
  inspect the actual repo, run the tests, return FINAL PASS and exactly
  4 next tasks — in the same response;
* after PASS: strict parse → persist verdict + next plan → BATCH_COMPLETE
  → reload from SQLite → START NEXT BATCH materialises 4 PENDING tasks
  with NO second model call;
* token evidence for the ONE call is printed (provider/model/tokens/
  duration/session id) — never translated into billing claims.

REAL_MODEL_OPERATIONS target: exactly 1.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from encomm_pcc.core import (  # noqa: E402
    FINAL_AUDIT_ENVELOPE_END,
    FINAL_AUDIT_ENVELOPE_START,
    EventLog,
    Executor,
    FinalAuditOutcome,
    PipelineController,
)
from encomm_pcc.core.events import NullEventLog  # noqa: E402
from encomm_pcc.core.executor import (  # noqa: E402
    StartNextBatchOutcome,
    next_task_action,
)
from encomm_pcc.domain import (  # noqa: E402
    AgentRole,
    BatchPlan,
    BatchPlanRecord,
    BatchStatus,
    PipelinePhase,
    PlannedTask,
    TaskState,
    TaskStateRecord,
    BatchState,
)
from encomm_pcc.drivers import (  # noqa: E402
    DriverCapabilities,
    DriverRegistry,
    DriverSession,
    HermesDriver,
    ProcessSpec,
    PromptHandle,
    PromptResult,
    SessionRequest,
    SubprocessRunner,
)
from encomm_pcc.persistence import Database  # noqa: E402

# ----------------------------------------------------------------------------
# the completed 4-task scratch batch (deterministic, all green, committed)
# ----------------------------------------------------------------------------
FEATURES = {
    1: (
        "def add(a, b):\n    return a + b\n",
        "from feature_1 import add\nassert add(2, 3) == 5\nassert add(0, 0) == 0\n"
        "print('test_feature_1: OK')\n",
    ),
    2: (
        "def multiply(a, b):\n    return a * b\n",
        "from feature_2 import multiply\nassert multiply(3, 4) == 12\n"
        "assert multiply(0, 9) == 0\nprint('test_feature_2: OK')\n",
    ),
    3: (
        "def is_even(n):\n    return n % 2 == 0\n",
        "from feature_3 import is_even\nassert is_even(4) is True\n"
        "assert is_even(7) is False\nprint('test_feature_3: OK')\n",
    ),
    4: (
        "def greet(name):\n    return f'Hello, {name}!'\n",
        "from feature_4 import greet\nassert greet('Ada') == 'Hello, Ada!'\n"
        "print('test_feature_4: OK')\n",
    ),
}

PROJECT_BRIEF = (
    "The repository contains four deterministic Python test files "
    "(tests/test_feature_1..4.py) and their four implemented modules "
    "(feature_1..4.py). Every test must exit 0. Do not modify any test file."
)


def banner(text: str) -> None:
    print("\n" + "=" * 78)
    print(text)
    print("=" * 78, flush=True)


def line(label: str, value: object) -> None:
    print(f"{label:<40}: {value}", flush=True)


def run_repo_test(repo: Path, index: int) -> int:
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import runpy, sys; "
                "sys.path.insert(0, '.'); "
                "runpy.run_path(sys.argv[1], run_name='__main__')"
            ),
            f"tests/test_feature_{index}.py",
        ],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return proc.returncode


def seed_completed_batch() -> dict:
    """Scratch git repo that ALREADY is a completed 4-task batch (all green)."""
    scratch = Path(tempfile.mkdtemp(prefix="encomm-pcc-s005-"))
    repo = scratch / "workspace"
    (repo / "tests").mkdir(parents=True)
    for index, (module, test) in FEATURES.items():
        (repo / f"feature_{index}.py").write_text(module, encoding="utf-8")
        (repo / "tests" / f"test_feature_{index}.py").write_text(test, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "smoke@localhost"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "Session005"], cwd=str(repo), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
    subprocess.run(
        ["git", "commit", "-qm", "completed batch: four features, four green tests"],
        cwd=str(repo),
        check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True, check=True
    ).stdout.strip()
    return {"scratch": scratch, "repo": repo, "head": head}


def seed_durable_state(controller: PipelineController, repo: Path, head: str) -> str:
    """Seed the durable completed-batch fixtures; return the batch id."""
    controller.set_workspace("Session 005 final-audit smoke", str(repo))
    batch = BatchState(workspace_id=controller.state.workspace.workspace_id, size=4)
    batch.project_brief = PROJECT_BRIEF
    tasks = []
    for index, (module, _test) in FEATURES.items():
        tasks.append(
            PlannedTask(
                index=index,
                title=f"Implement feature_{index}.py",
                implementation_prompt=(
                    f"Create feature_{index}.py at the repository root implementing "
                    f"exactly the behaviour tests/test_feature_{index}.py asserts. "
                    "Do NOT modify any test file."
                ),
                acceptance_criteria=[f"python tests/test_feature_{index}.py exits 0"],
                audit_focus=[f"verify feature_{index}.py exists and its test passes"],
            )
        )
    plan = BatchPlan(
        batch_title="Four deterministic scratch features",
        batch_objective="All four module tests exit 0.",
        tasks=tasks,
    )
    batch.plan = BatchPlanRecord(
        plan=plan,
        project_brief=PROJECT_BRIEF,
        requested_size=4,
        plan_status="PLANNED",
        # Historical ids are clearly-marked TEST FIXTURES, not live sessions.
        orchestrator_session_id="fake-orchestrator-fixture",
        baseline_head=head,
        current_head=head,
        batch_summary_json=json.dumps(
            {
                "batch_id": batch.batch_id,
                "batch_title": plan.batch_title,
                "requested_task_count": 4,
                "completed_task_count": 4,
                "historical_fixture": True,
                "builder_session_ids": [
                    f"fake-builder-{i}" for i in range(1, 5)
                ],
                "shared_auditor_session_id": "fake-auditor-fixture",
                "baseline_head": head,
                "current_head": head,
                "batch_status": "READY_FOR_FINAL_AUDIT",
            },
            sort_keys=True,
        ),
    )
    for index, planned in enumerate(plan.tasks, start=1):
        record = TaskStateRecord(
            index=planned.index,
            title=planned.title,
            prompt=planned.implementation_prompt,
            acceptance_criteria=list(planned.acceptance_criteria),
            audit_focus=list(planned.audit_focus),
            state=TaskState.APPROVED,
            attempts=1,
            audit_rounds=1,
            latest_verdict="PASS",
        )
        record.builder_session_id = f"fake-builder-{index}"
        record.auditor_session_id = "fake-auditor-fixture"
        batch.tasks.append(record)
    batch.status = BatchStatus.READY_FOR_FINAL_AUDIT
    controller.state.batch = batch
    controller.persist()
    for step in (
        PipelinePhase.PLANNING_BATCH,
        PipelinePhase.RUNNING_TASK,
        PipelinePhase.AUDITING_TASK,
        PipelinePhase.READY_FOR_FINAL_AUDIT,
    ):
        if controller.machine.can_go_to(step):
            controller.machine.transition_to(step)
    controller.state.phase = controller.machine.phase
    controller.persist()
    return batch.batch_id


# ----------------------------------------------------------------------------
# fake driver (--fake proves every post-processing path at zero cost)
# ----------------------------------------------------------------------------
class ScriptedFinalDriver:
    driver_id = "smoke_fake_final"
    display_name = "Scripted Final Engine"
    executables = ("python",)

    scripted: list[PromptResult] = []

    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name=cls.display_name,
            supports_sessions=True,
            supports_resume=False,
            implemented=True,
            notes="Fake driver for the session_005 smoke --fake mode.",
        )

    def __init__(self, runner=None) -> None:  # noqa: ANN001
        self._runner = runner

    def start_session(self, request: SessionRequest) -> DriverSession:
        return DriverSession(
            driver_id=self.driver_id, role=request.role, session_id=None, metadata={}
        )

    def resume_session(self, session_id: str, request: SessionRequest) -> DriverSession:
        raise NotImplementedError

    def send_prompt(self, session: DriverSession, prompt: str) -> PromptHandle:
        return PromptHandle(session=session, prompt=prompt)

    def wait_for_completion(self, handle: PromptHandle, timeout_s: float | None = None):
        if not type(self).scripted:
            raise AssertionError("ScriptedFinalDriver: no scripted result")
        return type(self).scripted.pop(0)


class _FakeRunner:
    def run(self, spec: ProcessSpec):
        from encomm_pcc.drivers import ProcessResult

        return ProcessResult(
            argv=spec.argv_list(), exit_code=0, stdout="", stderr="", duration_s=0.01
        )


def build_fake_pass() -> str:
    next_tasks = [
        {
            "index": i,
            "title": f"Next batch task {i}: extend feature_{i} with docs",
            "implementation_prompt": (
                f"Add a module docstring to feature_{i}.py describing its public "
                f"function. Do not change behaviour; tests/test_feature_{i}.py "
                "must still exit 0."
            ),
            "acceptance_criteria": [
                f"python tests/test_feature_{i}.py still exits 0",
                f"feature_{i}.py begins with a module docstring",
            ],
            "audit_focus": [
                f"verify feature_{i}.py has a docstring and its test still passes"
            ],
        }
        for i in range(1, 5)
    ]
    payload = {
        "final_verdict": "PASS",
        "summary": (
            "All four deterministic tests pass in the actual repository and "
            "match every task contract; the next batch adds docstrings."
        ),
        "findings": [],
        "batch_assessment": {"tests_verified": True, "diff_verified": True},
        "next_batch": {
            "batch_title": "Documentation pass over the four features",
            "batch_objective": "Add module docstrings without behaviour change.",
            "tasks": next_tasks,
        },
    }
    return (
        f"{FINAL_AUDIT_ENVELOPE_START}\n{json.dumps(payload, indent=2)}\n"
        f"{FINAL_AUDIT_ENVELOPE_END}"
    )


# ----------------------------------------------------------------------------
# smoke flow
# ----------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SESSION 005 real final-audit smoke")
    parser.add_argument("--profile", default="encomm-pipeline-control-center")
    parser.add_argument("--provider", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--next-batch-size", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=1200.0)
    parser.add_argument("--keep-scratch", action="store_true")
    parser.add_argument("--fake", action="store_true", help="in-process scripted run")
    args = parser.parse_args(argv)

    started = time.monotonic()
    banner("SESSION 005 — ONE-CALL FINAL AUDIT + NEXT-BATCH HANDOFF SMOKE")
    if args.fake:
        print("FAKE MODE — scripted in-process driver; NO real model calls.", flush=True)
    else:
        executable = HermesDriver.resolve_executable()
        line("hermes executable", executable)
        if not executable:
            print("FAIL: the Hermes CLI is not on PATH.")
            return 1
        line("driver implemented", HermesDriver.capabilities().implemented)

    # -- 1. scratch repo: a completed 4-task batch, all tests green ---------
    banner("STEP 1 — completed 4-task scratch batch (tests GREEN, committed)")
    seeded = seed_completed_batch()
    repo, head = seeded["repo"], seeded["head"]
    line("scratch dir", seeded["scratch"])
    line("baseline HEAD", head[:12])
    green = all(run_repo_test(repo, i) == 0 for i in range(1, 5))
    line("all four tests green before the audit", green)
    if not green:
        print("FAIL: the scratch batch is not complete (a test is red).")
        return 1

    # -- 2. control center + durable fixtures -------------------------------
    banner("STEP 2 — durable fixtures (brief/plan/4 APPROVED tasks/summary)")
    data_dir = seeded["scratch"] / "data"
    data_dir.mkdir(parents=True)
    os.environ["ENCOMM_PCC_DATA_DIR"] = str(data_dir)
    database = Database(data_dir / "pipeline_control_center.db").open()
    events = EventLog(database)
    controller = PipelineController(database=database, event_log=events)
    batch_id = seed_durable_state(controller, repo, head)
    line("batch id", batch_id)
    line("pipeline phase", controller.machine.phase.value)
    if controller.machine.phase is not PipelinePhase.READY_FOR_FINAL_AUDIT:
        print("FAIL: fixtures did not reach READY_FOR_FINAL_AUDIT.")
        return 1

    # -- 3. ONE final audit call ---------------------------------------------
    banner("STEP 3 — THE one Final Auditor call (verdict + next 4 tasks)")
    registry = None
    if args.fake:
        registry = DriverRegistry()
        registry.register(ScriptedFinalDriver)
        ScriptedFinalDriver.scripted.append(
            PromptResult(
                ok=True,
                text=build_fake_pass(),
                session_id="fake-final-auditor-smoke",
                exit_code=0,
                metadata={"stream": {"tokens": {"input": 1, "output": 1, "total": 1}}},
            )
        )
        runner = _FakeRunner()
        controller.set_role_config(
            AgentRole.FINAL_AUDITOR,
            engine="smoke_fake_final",
            project_profile=args.profile,
            same_as_orchestrator=False,
        )
    else:
        runner = SubprocessRunner()
        controller.set_role_config(
            AgentRole.FINAL_AUDITOR,
            engine="hermes",
            project_profile=args.profile,
            provider=args.provider,
            model=args.model,
            same_as_orchestrator=False,
        )
        line("provider", args.provider or "(profile default)")
        line("model", args.model or "(profile default)")
    executor = Executor(
        controller,
        registry=registry,
        runner=runner,
        database=database,
        event_log=events,
        profile_discovery=lambda **kwargs: None,
    )
    controller.attach_executor(executor)

    report = executor.run_final_audit(
        next_batch_size=args.next_batch_size, timeout_s=args.timeout
    )
    line("final-audit outcome", report.outcome.value)
    line("message", (report.message or "")[:200])
    if report.outcome is not FinalAuditOutcome.PASSED:
        # §21 cost guard: preserve raw evidence, never brute-force a re-run.
        evidence = seeded["scratch"] / "final_audit_output.txt"
        evidence.write_text(
            f"outcome : {report.outcome.value}\nmessage : {report.message}\n"
            "--- raw model output (bounded) ---\n{report.output_excerpt}",
            encoding="utf-8",
        )
        print(f"FAIL: final audit ended {report.outcome.value}.")
        print(f"Scratch preserved for evidence: {seeded['scratch']}")
        return 1
    assert controller.machine.phase is PipelinePhase.BATCH_COMPLETE
    assert controller.state.batch.status is BatchStatus.COMPLETE
    line("final phase", controller.machine.phase.value)
    line("FINAL_AUDITOR session", report.session_id or "NOT_EXPOSED")
    result = report.result
    line("final verdict", result.verdict.value)
    line("next tasks from the SAME call", result.next_batch.task_count)
    stream = dict((report.prompt_result.metadata or {}).get("stream") or {})
    tokens = dict(stream.get("tokens") or {})
    line("tokens (input/output/total)", tokens)
    line("duration s", round(report.prompt_result.duration_s, 1))

    # -- 4. independent deterministic tests (repo unchanged) ------------------
    banner("STEP 4 — independent deterministic runs + read-only guard check")
    for index in range(1, 5):
        rc = run_repo_test(repo, index)
        line(f"test_feature_{index}.py", f"exit {rc}")
        if rc != 0:
            print("FAIL: a scratch test no longer passes — the repo changed?")
            return 1
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(repo), capture_output=True, text=True, check=True,
    ).stdout.strip()
    line("worktree clean after the audit", status == "" or "MUTATED" not in status)

    # -- 5. reload from SQLite -------------------------------------------------
    banner("STEP 5 — SQLite reload: COMPLETE batch + persisted next plan")
    stored = database.load_batch(batch_id)
    if stored is None or stored.status is not BatchStatus.COMPLETE:
        print("FAIL: the completed batch did not survive a reload.")
        return 1
    final_row = database.load_final_audit(batch_id)
    line("stored final verdict", final_row["final_verdict"] if final_row else None)
    pending = database.load_open_pending_next_plan()
    line("pending next plan", pending["plan_id"] if pending else None)
    if final_row is None or final_row["final_verdict"] != "PASS" or pending is None:
        print("FAIL: the final audit / next plan did not persist.")
        return 1

    # -- 6. START NEXT BATCH — deterministic, ZERO model calls -----------------
    banner("STEP 6 — START NEXT BATCH (persisted plan, no AI call)")
    handoff = executor.start_next_batch()
    line("handoff outcome", handoff.outcome.value)
    if handoff.outcome is not StartNextBatchOutcome.READY:
        print(f"FAIL: START NEXT BATCH refused: {handoff.message}")
        return 1
    line("new batch id", handoff.batch_id)
    line("new batch tasks", handoff.task_count)
    line("new batch phase", controller.machine.phase.value)
    if handoff.task_count != args.next_batch_size:
        print("FAIL: the materialised task count does not match the plan.")
        return 1
    if not all(
        t.state is TaskState.PENDING for t in controller.state.batch.tasks
    ):
        print("FAIL: the materialised tasks are not PENDING.")
        return 1
    stored_old = database.load_batch(batch_id)
    if stored_old is None or stored_old.status is not BatchStatus.COMPLETE:
        print("FAIL: the completed batch was not preserved.")
        return 1
    line("previous batch preserved", stored_old.status.value)

    banner("SMOKE PASSED")
    line("REAL model operations", 1)
    line("total wall clock s", f"{time.monotonic() - started:.1f}")
    line("scratch dir", seeded["scratch"])
    database.close()
    if not args.keep_scratch:
        shutil.rmtree(seeded["scratch"], ignore_errors=True)
        print("scratch removed (use --keep-scratch to inspect it).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
