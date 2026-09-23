#!/usr/bin/env python3
"""SESSION 008 — REAL MIXED-ENGINE 2-TASK PRODUCTION ACCEPTANCE (brief §18–§29).

Not part of the unit suite: real mode talks to REAL engines.  Run explicitly::

    python scripts/session_008_acceptance.py \
        --profile encomm-pipeline-control-center \
        --builder-provider openrouter --builder-model deepseek/deepseek-v4.1-flash \
        --auditor-provider openrouter --auditor-model deepseek/deepseek-v4.1-flash \
        --timeout 1200 --keep-scratch

Engine mix (the brief's preferred acceptance configuration):

    ORCHESTRATOR  = codex    (real Codex thread; planning is read-only-guarded)
    BUILDER       = hermes   (fresh session per build)
    TASK_AUDITOR  = hermes   (ONE persistent_per_batch session)
    FINAL_AUDITOR = codex    (same_as_orchestrator cleared)

Flow:
  1. deterministic scratch git repo — 2 red tests, committed
  2. acceptance DB under the scratch home (ENCOMM_PCC_DATA_DIR)
  3. REAL Codex Orchestrator plans EXACTLY 2 tasks (read-only guard active)
  4. LEG 1: real Hermes Builder (task 1) + real Hermes Task Auditor
  5. CONTROLLED RESTART: clean DB close/reopen, state restored, NO model run
  6. LEG 2: BatchRunner(resume=True) — task 2 with a NEW Builder, SAME auditor
     -> READY_FOR_FINAL_AUDIT
  7. REAL Codex Final Auditor: ONE call = cumulative verdict + next plan
  8. SQLite reload proof + START NEXT BATCH materialises the plan (zero AI)

REAL_MODEL_OPERATIONS target: 1 orchestrator + 2 builders + 2 auditors
+ 1 final auditor = 6 (fix loops may legitimately add more).
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
    BatchOutcome,
    BatchRunner,
    EventLog,
    Executor,
    PipelineController,
    ProfileDiscoveryResult,
    discover_profiles,
)
from encomm_pcc.domain import (  # noqa: E402
    AgentRole,
    BatchStatus,
    PipelinePhase,
    TaskState,
)
from encomm_pcc.drivers import (  # noqa: E402
    CodexDriver,
    HermesDriver,
    SubprocessRunner,
)
from encomm_pcc.persistence import Database  # noqa: E402

#: Committed import bootstrap (Session 008 acceptance finding: the literal
#: command 'python tests/test_feature_N.py' puts tests/ on sys.path[0]; the
#: bootstrap ships IN the seed commit so no Builder ever needs out-of-scope
#: shims — the class of defect the first acceptance run's Final Auditor
#: BLOCKED on).
_BOOTSTRAP = (
    "# Committed import bootstrap: running 'python tests/test_feature_N.py'\n"
    "# puts tests/ on sys.path[0]; make the repository root importable.\n"
    "import os, sys\n"
    "sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n\n"
)

FEATURES = {
    1: {
        "name": "feature_1 (slugify)",
        "test": _BOOTSTRAP + (
            "from feature_1 import slugify\n"
            "assert slugify('Hello World') == 'hello-world', f\"got {slugify('Hello World')!r}\"\n"
            "assert slugify('  Multiple   Spaces  ') == 'multiple-spaces'\n"
            "assert slugify('Already-Kebab') == 'already-kebab'\n"
            "print('test_feature_1: OK')\n"
        ),
    },
    2: {
        "name": "feature_2 (to_roman)",
        "test": _BOOTSTRAP + (
            "from feature_2 import to_roman\n"
            "assert to_roman(1) == 'I', f\"got {to_roman(1)!r}\"\n"
            "assert to_roman(9) == 'IX'\n"
            "assert to_roman(2026) == 'MMXXVI'\n"
            "print('test_feature_2: OK')\n"
        ),
    },
}

PROJECT_BRIEF = (
    "The repository contains two deterministic Python test files: "
    "tests/test_feature_1.py and tests/test_feature_2.py. Each imports ONE "
    "module from the repository root (feature_1, feature_2) that does not "
    "exist yet. Plan EXACTLY two implementation tasks: task i creates "
    "feature_i.py at the repository root implementing exactly the functions "
    "and behaviour its test asserts. The primary acceptance criterion for "
    "each task is that running 'python tests/test_feature_N.py' exits 0. "
    "Each audit_focus must verify the actual module in the repository and "
    "run that test. Do not modify any test file, the .gitignore, or the "
    "existing seed commit. Do not plan anything beyond these two modules. "
    "Create ONLY feature_1.py and feature_2.py at the repository root — no "
    "source files anywhere else (in particular: no import shims inside "
    "tests/, no conftest.py); the tests already contain a committed import "
    "bootstrap, so the literal test command works as soon as the root "
    "modules exist. Build caches (__pycache__, .pytest_cache) are "
    "gitignored byproducts of running the tests and are expected — they are "
    "NOT findings."
)

DISCOVERY = ProfileDiscoveryResult(ok=False, method="pending", detail="not run")


def banner(text: str) -> None:
    print("\n" + "=" * 78)
    print(text)
    print("=" * 78, flush=True)


def line(label: str, value: object) -> None:
    print(f"{label:<38}: {value}", flush=True)


def run_repo_test(repo: Path, index: int) -> int:
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy, sys; sys.path.insert(0, '.'); "
            "runpy.run_path(sys.argv[1], run_name='__main__')",
            f"tests/test_feature_{index}.py",
        ],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return proc.returncode


def seed_scratch_repo() -> dict:
    scratch = Path(tempfile.mkdtemp(prefix="encomm-pcc-s008-"))
    repo = scratch / "workspace"
    repo.mkdir(parents=True)
    tests = repo / "tests"
    tests.mkdir()
    for index, spec in FEATURES.items():
        (tests / f"test_feature_{index}.py").write_text(spec["test"], encoding="utf-8")
    (repo / ".gitignore").write_text(
        "__pycache__/\n*.pyc\n.pytest_cache/\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "acceptance@localhost"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "Session008"], cwd=str(repo), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-qm", "seed two deterministic red tests"], cwd=str(repo), check=True)
    return {"scratch": scratch, "repo": repo}


def build_control_center(*, scratch: Path, repo: Path, args) -> dict:
    data_dir = scratch / "data"
    data_dir.mkdir(parents=True)
    os.environ["ENCOMM_PCC_DATA_DIR"] = str(data_dir)

    database = None  # real assignment below
    from encomm_pcc.persistence import Database

    database = Database(data_dir / "pipeline_control_center.db").open()
    events = EventLog(database)
    controller = PipelineController(database=database, event_log=events)
    controller.set_workspace("Session 008 acceptance workspace", str(repo))

    # The brief's preferred MIXED configuration (§19).  Codex needs no
    # provider/model (account default); Hermes roles get provider/model.
    controller.set_role_config(
        AgentRole.ORCHESTRATOR, engine="codex", project_profile="", provider="", model=""
    )
    controller.set_role_config(
        AgentRole.BUILDER,
        engine="hermes",
        project_profile=args.profile,
        provider=args.builder_provider,
        model=args.builder_model,
    )
    controller.set_role_config(
        AgentRole.TASK_AUDITOR,
        engine="hermes",
        project_profile=args.profile,
        provider=args.auditor_provider,
        model=args.auditor_model,
    )
    # FINAL_AUDITOR: dedicated Codex role — clear same_as_orchestrator.
    controller.set_role_config(
        AgentRole.FINAL_AUDITOR,
        engine="codex",
        project_profile="",
        provider="",
        model="",
        same_as_orchestrator=False,
    )

    executor = Executor(
        controller,
        runner=SubprocessRunner(),
        database=database,
        event_log=events,
        profile_discovery=lambda **kwargs: DISCOVERY,
    )
    controller.attach_executor(executor)
    return {
        "scratch": scratch,
        "repo": repo,
        "data_dir": data_dir,
        "database": database,
        "controller": controller,
        "executor": executor,
    }


def reopen_control_center(*, scratch: Path, repo: Path, args) -> dict:
    """A REAL restart: fresh controller over the same acceptance DB.

    Nothing is auto-run: constructing a controller never launches a process.
    """
    from encomm_pcc.app import restore_state
    from encomm_pcc.persistence import Database

    data_dir = scratch / "data"
    database = Database(data_dir / "pipeline_control_center.db").open()
    events = EventLog(database)
    state = restore_state(database)
    controller = PipelineController(database=database, event_log=events, state=state)
    executor = Executor(
        controller,
        runner=SubprocessRunner(),
        database=database,
        event_log=events,
        profile_discovery=lambda **kwargs: DISCOVERY,
    )
    controller.attach_executor(executor)
    return {
        "scratch": scratch,
        "repo": repo,
        "data_dir": data_dir,
        "database": database,
        "controller": controller,
        "executor": executor,
    }


def _dump_final_audit_evidence(fa, evidence_dir: Path) -> None:
    """Preserve the failing final-audit raw output (evidence-retry rule)."""
    try:
        raw = ""
        if fa.prompt_result is not None:
            raw = fa.prompt_result.text or ""
        target = evidence_dir / "s008_final_audit_output.txt"
        target.write_text(
            f"outcome : {fa.outcome.value}\n"
            f"message : {fa.message}\n"
            "--- raw model output (bounded) ---\n"
            + raw[:20000],
            encoding="utf-8",
        )
        line("final-audit evidence written to", target)
    except OSError:  # pragma: no cover
        pass


def dump_failing_steps(report, evidence_dir: Path) -> None:
    """Preserve failing raw output from either a BatchRunReport or an
    ExecutionReport (evidence-retry rule — never diagnose from guesses)."""
    try:
        steps = getattr(report, "steps", None)
        if steps:
            for step in steps:
                if step.outcome in ("BLOCKED", "FAILED") and step.output_excerpt:
                    target = evidence_dir / f"s008_{step.kind.lower()}_step_output.txt"
                    target.write_text(
                        f"step kind : {step.kind}\n"
                        f"outcome   : {step.outcome}\n"
                        f"message   : {step.message}\n"
                        "--- raw model output (bounded) ---\n"
                        + step.output_excerpt,
                        encoding="utf-8",
                    )
                    line("failing output written to", target)
            return
        raw = ""
        if getattr(report, "prompt_result", None) is not None:
            raw = report.prompt_result.text or ""
        if getattr(report, "audit_verdict", None) is not None:
            raw += f"\nverdict: {report.audit_verdict.verdict.value}\n"
            raw += f"summary: {report.audit_verdict.summary}\n"
        target = evidence_dir / "s008_step_output.txt"
        target.write_text(
            f"outcome : {report.outcome.value}\n"
            f"message : {report.message}\n"
            "--- raw model output (bounded) ---\n"
            + raw[:20000],
            encoding="utf-8",
        )
        line("failing output written to", target)
    except OSError:  # pragma: no cover - best-effort evidence
        pass




def finalize_acceptance(args) -> int:
    """Final audit + next-batch proof on a preserved scratch already at
    READY_FOR_FINAL_AUDIT (evidence-retry rule: leg 1/leg 2 model operations
    already succeeded and are durable in the scratch DB — never repeated)."""
    started = time.monotonic()
    banner("SESSION 008 — FINALIZE from preserved READY_FOR_FINAL_AUDIT scratch")

    global DISCOVERY
    DISCOVERY = discover_profiles(runner=SubprocessRunner(), timeout_s=60.0)
    line("profile discovery", f"ok={DISCOVERY.ok} method={DISCOVERY.method} n={len(DISCOVERY.profiles)}")

    scratch = Path(args.finalize_scratch)
    repo = scratch / "workspace"
    if not scratch.is_dir() or not repo.is_dir():
        print(f"FAIL: scratch dir not found: {scratch}")
        return 1
    line("scratch", scratch)

    ctx = reopen_control_center(scratch=scratch, repo=repo, args=args)
    controller, executor, database = ctx["controller"], ctx["executor"], ctx["database"]
    batch = controller.state.batch
    if batch is None:
        print("FAIL: no active batch in the preserved DB.")
        return 1
    line("phase", executor.controller.machine.phase.value)
    line("batch status", batch.status.value)
    tasks = {t.index: t for t in batch.tasks}
    line("task states", {i: t.state.value for i, t in sorted(tasks.items())})
    if batch.status is not BatchStatus.READY_FOR_FINAL_AUDIT:
        print(f"FAIL: expected READY_FOR_FINAL_AUDIT, found {batch.status.value}.")
        return 1

    builder1 = batch.tasks[0].builder_session_id or "NOT_EXPOSED"
    return _finish_from_safe_boundary(scratch, args, started, batch.batch_id, builder1)


def resume_acceptance(args) -> int:
    """Continue a preserved acceptance scratch dir (evidence-retry rule).

    The prior run's model operations (Codex plan, Hermes build, audit) are
    durable in the scratch SQLite DB — they are NEVER repeated.  This
    finishes task 1's legitimate fix loop, then performs the SAME controlled
    restart + leg 2 + final audit + next-batch proof as a fresh run.
    """
    started = time.monotonic()
    banner("SESSION 008 — RESUME from preserved acceptance scratch")

    global DISCOVERY
    DISCOVERY = discover_profiles(runner=SubprocessRunner(), timeout_s=60.0)
    line("profile discovery", f"ok={DISCOVERY.ok} method={DISCOVERY.method} n={len(DISCOVERY.profiles)}")

    scratch = Path(args.resume_scratch)
    repo = scratch / "workspace"
    if not scratch.is_dir() or not repo.is_dir():
        print(f"FAIL: scratch dir not found: {scratch}")
        return 1
    line("scratch", scratch)

    ctx = reopen_control_center(scratch=scratch, repo=repo, args=args)
    controller, executor, database = ctx["controller"], ctx["executor"], ctx["database"]
    batch = controller.state.batch
    if batch is None:
        print("FAIL: no active batch in the preserved DB.")
        return 1
    batch_id = batch.batch_id
    tasks = {t.index: t for t in batch.tasks}
    line("task states", {i: t.state.value for i, t in sorted(tasks.items())})

    # -- finish task 1 (fix loop if the preserved state requires it) ---------
    task1 = tasks[1]
    audit_report = None
    if task1.state is TaskState.AUDITING:
        # An in-flight audit that never completed (crash/kill mid-prompt) is
        # never trusted complete: the executor re-runs the audit for real.
        line("task 1 was AUDITING mid-flight; re-running the audit", True)
        audit_report = executor.run_task_audit(index=1, timeout_s=args.timeout)
        line("audit outcome", audit_report.outcome.value)
        line("audit verdict",
             audit_report.audit_verdict.verdict.value if audit_report.audit_verdict else None)
        task1 = next(t for t in controller.state.batch.tasks if t.index == 1)
        line("task 1 state", task1.state.value)
    while task1.state is TaskState.FIX_REQUIRED:
        fx = executor.run_task_fix(index=1, timeout_s=args.timeout)
        line("fix outcome", fx.outcome.value)
        if fx.outcome.value != "COMPLETED":
            dump_failing_steps(fx, scratch)
            print("FAIL: the fix run did not complete.")
            return 1
        audit_report = executor.run_task_audit(index=1, timeout_s=args.timeout)
        line("re-audit outcome", audit_report.outcome.value)
        line("re-audit verdict",
             audit_report.audit_verdict.verdict.value if audit_report.audit_verdict else None)
        task1 = next(t for t in controller.state.batch.tasks if t.index == 1)
        line("task 1 state", task1.state.value)
    if task1.state is not TaskState.APPROVED:
        if audit_report is not None:
            dump_failing_steps(audit_report, scratch)
        print("FAIL: task 1 could not be approved by the fix loop.")
        return 1
    line("task 1 APPROVED (builder/auditor)",
         f"{task1.builder_session_id} / {task1.auditor_session_id}")

    # -- controlled restart at the safe boundary (§26) -----------------------
    banner("STEP 5 — CONTROLLED RESTART (after task 1 approval, before task 2)")
    line("phase at restart", executor.controller.machine.phase.value)
    database.close()
    line("database closed cleanly", True)

    return _finish_from_safe_boundary(
        scratch, args, started, batch_id, task1.builder_session_id
    )

def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="SESSION 008 real mixed-engine acceptance")
    parser.add_argument("--profile", default="encomm-pipeline-control-center")
    parser.add_argument("--builder-provider", default="openrouter")
    parser.add_argument("--builder-model", default="deepseek/deepseek-v4.1-flash")
    parser.add_argument("--auditor-provider", default="openrouter")
    parser.add_argument("--auditor-model", default="deepseek/deepseek-v4.1-flash")
    parser.add_argument("--timeout", type=float, default=1200.0)
    parser.add_argument("--next-batch-size", type=int, default=4)
    parser.add_argument("--keep-scratch", action="store_true")
    parser.add_argument(
        "--resume-scratch",
        default="",
        help="continue a preserved acceptance scratch dir (evidence-retry rule: "
        "never repeat model operations that already succeeded)",
    )
    parser.add_argument(
        "--finalize-scratch",
        default="",
        help="run the final audit + next-batch proof on a preserved scratch "
        "already at READY_FOR_FINAL_AUDIT (evidence-retry rule)",
    )
    args = parser.parse_args(argv)
    if args.finalize_scratch:
        return finalize_acceptance(args)
    if args.resume_scratch:
        return resume_acceptance(args)

    started = time.monotonic()
    banner("SESSION 008 — REAL MIXED-ENGINE 2-TASK ACCEPTANCE")

    # -- engines present? (read-only probes) ---------------------------------
    hermes_exe = HermesDriver.resolve_executable()
    codex_exe = CodexDriver.resolve_executable() if hasattr(CodexDriver, "resolve_executable") else shutil.which("codex")
    line("hermes executable", hermes_exe or "NOT FOUND")
    line("codex executable", codex_exe or "NOT FOUND")
    if not hermes_exe or not codex_exe:
        print("FAIL: both Hermes and Codex must be installed for this acceptance run.")
        return 1

    # -- profile discovery (read-only) ---------------------------------------
    global DISCOVERY
    DISCOVERY = discover_profiles(runner=SubprocessRunner(), timeout_s=60.0)
    line("profile discovery", f"ok={DISCOVERY.ok} method={DISCOVERY.method} n={len(DISCOVERY.profiles)}")
    if not DISCOVERY.ok or args.profile not in DISCOVERY.profiles:
        print(f"FAIL: profile '{args.profile}' not discovered -> {DISCOVERY.error}")
        return 1

    # -- 1. scratch repo, red -------------------------------------------------
    banner("STEP 1 — deterministic scratch repository (2 red tests)")
    seeded = seed_scratch_repo()
    repo = seeded["repo"]
    line("scratch", seeded["scratch"])
    red = all(run_repo_test(repo, i) != 0 for i in (1, 2))
    line("both tests red", red)
    if not red:
        print("FAIL: a scratch test already passes.")
        return 1

    # -- 2. control center ----------------------------------------------------
    banner("STEP 2 — acceptance Control Center (scratch DB, real runner)")
    ctx = build_control_center(scratch=seeded["scratch"], repo=repo, args=args)
    controller, executor, database = ctx["controller"], ctx["executor"], ctx["database"]

    # -- 3. REAL Codex orchestrator planning ----------------------------------
    banner("STEP 3 — REAL Codex ORCHESTRATOR plans exactly 2 tasks")
    plan_report = executor.plan_batch(
        project_brief=PROJECT_BRIEF, batch_size=2, timeout_s=args.timeout
    )
    line("plan outcome", plan_report.outcome.value)
    line("plan message", (plan_report.message or "")[:160])
    if plan_report.outcome.value != "PLANNED":
        print(f"FAIL: planning ended {plan_report.outcome.value}.")
        dump_failing_steps(plan_report, seeded["scratch"])
        return 1
    tasks = controller.state.batch.tasks
    line("planned tasks", len(tasks))
    plan_record = controller.state.batch.plan
    orch_sid = plan_record.orchestrator_session_id if plan_record else None
    line("orchestrator thread/session", orch_sid or "NOT_EXPOSED")
    if len(tasks) != 2:
        print("FAIL: the Orchestrator must plan exactly 2 tasks.")
        return 1
    if not orch_sid:
        print("FAIL: no real orchestrator session/thread id recorded.")
        return 1

    # -- 4. LEG 1: task 1 build + audit (Hermes) ------------------------------
    banner("STEP 4 — LEG 1: REAL Hermes Builder + Task Auditor for task 1")
    b1 = executor.run_task_build(index=1, timeout_s=args.timeout)
    line("build 1 outcome", b1.outcome.value)
    if b1.outcome.value != "COMPLETED":
        dump_failing_steps(b1, seeded["scratch"])
        print("FAIL: task 1 build did not succeed.")
        return 1
    a1 = executor.run_task_audit(index=1, timeout_s=args.timeout)
    line("audit 1 outcome", a1.outcome.value)
    line("audit 1 verdict", a1.audit_verdict.verdict.value if a1.audit_verdict else None)
    task1 = next(t for t in controller.state.batch.tasks if t.index == 1)
    line("task 1 state", task1.state.value)
    line("builder 1 session", task1.builder_session_id or "NOT_EXPOSED")
    line("auditor session", task1.auditor_session_id or "NOT_EXPOSED")

    # A legitimate NEEDS_FIX runs the capped fix loop (§23): fix in a
    # brand-new Builder session, then the SAME auditor session re-audits.
    # The executor enforces MAX_AUDIT_ROUNDS, so this loop is bounded.
    audit_report = a1
    while task1.state is TaskState.FIX_REQUIRED:
        fx = executor.run_task_fix(index=1, timeout_s=args.timeout)
        line("fix outcome", fx.outcome.value)
        if fx.outcome.value != "COMPLETED":
            dump_failing_steps(fx, seeded["scratch"])
            print("FAIL: the fix run did not complete.")
            return 1
        audit_report = executor.run_task_audit(index=1, timeout_s=args.timeout)
        line("re-audit outcome", audit_report.outcome.value)
        line("re-audit verdict",
             audit_report.audit_verdict.verdict.value if audit_report.audit_verdict else None)
        task1 = next(t for t in controller.state.batch.tasks if t.index == 1)
        line("task 1 state", task1.state.value)
    if task1.state is not TaskState.APPROVED:
        dump_failing_steps(audit_report, seeded["scratch"])
        print("FAIL: task 1 is not APPROVED after leg 1 (incl. the fix loop).")
        return 1

    # -- 5. CONTROLLED RESTART at the safe boundary ---------------------------
    banner("STEP 5 — CONTROLLED RESTART (after task 1 approval, before task 2)")
    phase_before = executor.controller.machine.phase.value
    batch_id = controller.state.batch.batch_id
    line("phase at restart", phase_before)
    database.close()
    line("database closed cleanly", True)

    return _finish_from_safe_boundary(
        seeded["scratch"], args, started, batch_id, task1.builder_session_id
    )


def _finish_from_safe_boundary(
    scratch: Path, args, started: float, batch_id: str, builder1_session_id: str
) -> int:
    """Leg 2 + Final Audit + next-batch proof, shared by fresh and resumed runs."""
    repo = scratch / "workspace"
    ctx2 = reopen_control_center(scratch=scratch, repo=repo, args=args)
    controller2, executor2, database2 = (
        ctx2["controller"], ctx2["executor"], ctx2["database"]
    )
    restored_phase = executor2.controller.machine.phase.value
    line("restored phase", restored_phase)
    restored_tasks = {t.index: t for t in executor2.controller.state.batch.tasks}
    line("restored task 1 state", restored_tasks[1].state.value)
    line("restored task 2 state", restored_tasks[2].state.value)
    if restored_tasks[1].state is not TaskState.APPROVED:
        print("FAIL: task 1 approval did not survive the restart.")
        return 1
    line("NO model auto-run on restart", "confirmed (fresh executor, nothing launched)")

    # -- 6. LEG 2: BatchRunner(resume=True) finishes task 2 -------------------
    banner("STEP 6 — resume: BatchRunner finishes task 2 (fresh Builder, SAME auditor)")
    runner = BatchRunner(executor2)
    report = runner.run_batch(resume=True, timeout_s=args.timeout)
    line("batch outcome", report.outcome.value)
    line("operations", report.operation_counts())
    line("token totals", report.token_totals())
    if report.outcome is not BatchOutcome.READY_FOR_FINAL_AUDIT:
        dump_failing_steps(report, scratch)
        print(f"FAIL: batch ended {report.outcome.value}, expected READY_FOR_FINAL_AUDIT.")
        return 1
    final_tasks = {t.index: t for t in executor2.controller.state.batch.tasks}
    b2_sid = final_tasks[2].builder_session_id or "NOT_EXPOSED"
    aud_same = (
        final_tasks[1].auditor_session_id == final_tasks[2].auditor_session_id
        and bool(final_tasks[2].auditor_session_id)
    )
    line("builder 2 session (new)", b2_sid)
    line("one auditor session across the batch", aud_same)
    line("builders distinct", builder1_session_id != final_tasks[2].builder_session_id)
    if not aud_same:
        print("FAIL: the auditor session did not survive the restart.")
        return 1
    if builder1_session_id == final_tasks[2].builder_session_id:
        print("FAIL: builder sessions must be distinct across tasks.")
        return 1
    if not all(t.state is TaskState.APPROVED for t in final_tasks.values()):
        print("FAIL: not every task is APPROVED.")
        return 1

    # -- 7. REAL Codex Final Auditor ------------------------------------------
    banner("STEP 7 — REAL Codex FINAL_AUDITOR (one call: verdict + next plan)")
    fa = executor2.run_final_audit(next_batch_size=args.next_batch_size, timeout_s=args.timeout)
    line("final-audit outcome", fa.outcome.value)
    line("final verdict", fa.result.verdict.value if fa.result else None)
    line("final-auditor session", fa.session_id or "NOT_EXPOSED")
    line("findings", len(fa.result.findings) if fa.result else 0)
    line("next tasks in the SAME call",
         fa.result.next_batch.task_count if (fa.result and fa.result.next_batch) else 0)
    if fa.outcome.value != "PASSED":
        _dump_final_audit_evidence(fa, scratch)
        print("FAIL: the final audit did not PASS.")
        return 1
    line("pipeline phase", executor2.controller.machine.phase.value)

    # -- 8. restart-safe proof + START NEXT BATCH ------------------------------
    banner("STEP 8 — reload from SQLite; START NEXT BATCH (zero AI calls)")
    database2.close()
    ctx3 = reopen_control_center(scratch=scratch, repo=repo, args=args)
    database3, executor3 = ctx3["database"], ctx3["executor"]
    stored = database3.load_batch(batch_id)
    line("stored batch status", stored.status.value if stored else None)
    stored_final = database3.load_final_audit(batch_id)
    line("stored final verdict", stored_final["final_verdict"] if stored_final else None)
    line("stored final auditor session",
         stored_final["final_auditor_session_id"] if stored_final else "NOT_EXPOSED")
    if stored is None or stored.status is not BatchStatus.COMPLETE:
        print("FAIL: the completed batch did not survive the reload.")
        return 1
    if not stored_final or stored_final["final_verdict"] != "PASS":
        print("FAIL: the durable Final Audit verdict is missing or not PASS.")
        return 1
    handoff = executor3.start_next_batch()
    line("handoff outcome", handoff.outcome.value)
    line("handoff tasks", handoff.task_count)
    if handoff.outcome.value != "READY" or handoff.task_count != args.next_batch_size:
        print("FAIL: START NEXT BATCH did not materialise the persisted plan.")
        return 1
    new_tasks = {t.index: t for t in executor3.controller.state.batch.tasks}
    line("new batch tasks", {i: t.state.value for i, t in sorted(new_tasks.items())})

    banner("ACCEPTANCE PASSED")
    line("total wall clock (s)", f"{time.monotonic() - started:.1f}")
    line("real model operations", report.operation_counts())
    line("scratch", scratch)
    database3.close()
    if not args.keep_scratch:
        shutil.rmtree(scratch, ignore_errors=True)
        print("scratch removed (use --keep-scratch to inspect it).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
