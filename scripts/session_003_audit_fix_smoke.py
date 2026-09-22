"""SESSION 003 REAL SMOKE — the complete controlled audit/fix loop, live.

Not part of the unit suite: this script talks to a real engine.  Run it
explicitly::

    python scripts/session_003_audit_fix_smoke.py --profile encomm-pipeline-control-center

What it proves, in one controlled scenario:

    deliberately defective scratch git repo (calculator.add returns a - b)
        ↓
    REAL TASK_AUDITOR Hermes session  → NEEDS_FIX  (strict structured verdict)
        ↓
    BRAND-NEW BUILDER Hermes session  → fixes calculator.py
        ↓
    deterministic test run            → add(2, 3) == 5
        ↓
    SAME auditor session resumed      → PASS
        ↓
    supervisor marks task COMPLETE  (phase READY_FOR_FINAL_AUDIT, persisted;
    BATCH_COMPLETE is only reachable through the Final Auditor, Session 005)

Safety properties (why this is safe to run):

* The scratch project is a fresh git repo under the system temp dir — never
  this Control Center repository and never any ENCOMM production repository.
* The application database is written to a scratch data directory.
* The Auditor and Builder prompts restrict ALL tool use to the scratch path.
* Maximum real model operations: initial audit, fix, re-audit = **3** runs.
  A failure stops the script with the child's own error text; it is diagnosed,
  never brute-forced with blind retries.
* Session ids are read from what the engine really reported — never fabricated.
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
    EventLog,
    ExecutionOutcome,
    Executor,
    PipelineController,
    ProfileDiscoveryResult,
    TaskSpec,
    discover_profiles,
)
from encomm_pcc.core.executor import MAX_AUDIT_ROUNDS  # noqa: E402
from encomm_pcc.domain import AgentRole, BatchStatus, PipelinePhase, TaskState  # noqa: E402
from encomm_pcc.drivers import HermesDriver, SubprocessRunner  # noqa: E402
from encomm_pcc.persistence import Database  # noqa: E402

CALCULATOR_BROKEN = "def add(a, b):\n    return a - b\n"
CALCULATOR_FIXED = "def add(a, b):\n    return a + b\n"
TEST_FILE = (
    "from calculator import add\n"
    "\n"
    "assert add(2, 3) == 5, f\"add(2, 3) returned {add(2, 3)}, expected 5\"\n"
    "assert add(0, 0) == 0\n"
    "print('test_calculator: OK')\n"
)
TASK_PROMPT = (
    "The implementation must make the deterministic test in this repository "
    "pass. Do not change the test file. Do not touch anything outside the "
    "workspace."
)

_discovery = ProfileDiscoveryResult(ok=False, method="pending", detail="not run")


def banner(text: str) -> None:
    print("\n" + "=" * 78)
    print(text)
    print("=" * 78, flush=True)


def line(label: str, value: object) -> None:
    print(f"{label:<28}: {value}", flush=True)


def run_scratch_test(workspace: Path) -> tuple[int, str]:
    """Run the deterministic scratch test with plain python (no pytest)."""
    proc = subprocess.run(
        [sys.executable, "test_calculator.py"],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output.strip()[-2000:]


def build_control_center(*, profile: str, provider: str, model: str) -> dict:
    """Assemble a scratch control center over the real runner. No repo writes."""
    scratch = Path(tempfile.mkdtemp(prefix="encomm-pcc-s003-"))
    workspace = scratch / "workspace"
    workspace.mkdir(parents=True)
    data_dir = scratch / "data"
    data_dir.mkdir(parents=True)
    os.environ["ENCOMM_PCC_DATA_DIR"] = str(data_dir)

    # the deliberately defective scratch REPOSITORY
    repo = workspace / "simple_project"
    repo.mkdir(parents=True)
    (repo / "calculator.py").write_text(CALCULATOR_BROKEN, encoding="utf-8")
    (repo / "test_calculator.py").write_text(TEST_FILE, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
    subprocess.run(
        ["git", "-c", "user.email=smoke@localhost", "-c", "user.name=Session003",
         "commit", "-q", "-m", "introduce the deliberate defect"],
        cwd=str(repo),
        check=True,
    )

    database = Database(data_dir / "pipeline_control_center.db").open()
    events = EventLog(database)
    controller = PipelineController(database=database, event_log=events)
    controller.set_workspace("Session 003 smoke workspace", str(repo))
    controller.set_role_config(
        AgentRole.BUILDER,
        engine="hermes",
        project_profile=profile,
        provider=provider,
        model=model,
    )
    controller.set_role_config(
        AgentRole.TASK_AUDITOR,
        engine="hermes",
        project_profile=profile,
        provider=provider,
        model=model,
    )
    runner = SubprocessRunner()
    executor = Executor(
        controller,
        runner=runner,
        database=database,
        event_log=events,
        profile_discovery=lambda **kwargs: _discovery,
    )
    controller.attach_executor(executor)
    return {
        "scratch": scratch,
        "repo": repo,
        "database": database,
        "controller": controller,
        "executor": executor,
        "events": events,
    }


BOUNDARY_EXTRA = (
    "CRITICAL BOUNDARY: you may read, run and edit files ONLY under the "
    "workspace path. Never touch anything outside it. Do not read Hermes "
    "config, the Control Center, or any other repository on this machine."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SESSION 003 real audit/fix smoke")
    parser.add_argument("--profile", default="encomm-pipeline-control-center")
    parser.add_argument("--provider", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--keep-scratch", action="store_true")
    args = parser.parse_args(argv)

    global _discovery  # noqa: PLW0603 - simple script-level wiring

    banner("SESSION 003 — REAL AUDITOR + CAPPED FIX LOOP SMOKE")
    started = time.monotonic()

    # -- 0. installed CLI + read-only profile discovery ---------------------
    from encomm_pcc.drivers import hermes as hermes_module

    repo_flag = hermes_module._LIVE_SMOKE_VERIFIED  # noqa: SLF001
    executable = HermesDriver.resolve_executable()
    line("hermes executable", executable)
    if not executable:
        print("FAIL: the Hermes CLI is not on PATH.")
        return 1
    hermes_module._LIVE_SMOKE_VERIFIED = True  # noqa: SLF001
    line("driver flag (repo)", repo_flag)
    line("driver implemented", HermesDriver.capabilities().implemented)
    line("driver resume", HermesDriver.capabilities().supports_resume)

    banner("STEP 1 — read-only profile discovery")
    _discovery = discover_profiles(runner=SubprocessRunner(), timeout_s=60.0)
    line("discovery ok", _discovery.ok)
    line("method", _discovery.method)
    line("profiles found", len(_discovery.profiles))
    if not _discovery.ok or args.profile not in _discovery.profiles:
        print(f"FAIL: profile '{args.profile}' was not discovered -> {_discovery.error}")
        return 1
    print(f"profile '{args.profile}' confirmed present.", flush=True)

    ctx = build_control_center(profile=args.profile, provider=args.provider, model=args.model)
    repo = ctx["repo"]
    controller = ctx["controller"]
    executor = ctx["executor"]
    database = ctx["database"]
    line("scratch dir", ctx["scratch"])
    line("scratch repo", repo)

    # -- 2. seed the defective task for audit --------------------------------
    banner("STEP 2 — seed the deliberately defective task for audit")
    executor.prepare_task_for_audit(TaskSpec(title="Fix the calculator", prompt=TASK_PROMPT))
    task = executor.controller.state.batch.tasks[0]
    line("task id", task.task_id)
    line("phase", executor.controller.machine.phase.value)
    line("next action", executor.next_action().value)
    rc, output = run_scratch_test(repo)
    line("defect confirmed (test exit)", rc)
    if rc == 0:
        print("FAIL: the scratch test passes already; the defect is missing.")
        return 1

    # -- 3. INITIAL AUDIT: real Task Auditor session --------------------------
    banner("STEP 3 — real Task Auditor (initial audit)")
    initial = executor.run_task_audit(timeout_s=args.timeout)
    line("outcome", initial.outcome.value)
    line("verdict", initial.audit_verdict.verdict.value if initial.audit_verdict else "NONE")
    line("message", initial.message)
    line("exit code", initial.prompt_result.exit_code if initial.prompt_result else None)
    line("error", initial.prompt_result.error if initial.prompt_result else None)
    auditor_initial = initial.session_id or "NOT_EXPOSED"
    line("auditor session (initial)", auditor_initial)
    if initial.audit_verdict is not None:
        print("--- auditor summary ---")
        print(initial.audit_verdict.summary)
        print("--- findings ---")
        for f in initial.audit_verdict.findings:
            print(f"  [{f.severity.value}] {f.message}")
        if initial.audit_verdict.findings and initial.audit_verdict.findings[0].evidence:
            print(f"  evidence: {initial.audit_verdict.findings[0].evidence}")
    if initial.outcome is not ExecutionOutcome.NEEDS_FIX:
        print(f"FAIL: expected NEEDS_FIX, got {initial.outcome.value}. Diagnose, do not retry.")
        return 1
    if not initial.audit_verdict or not initial.audit_verdict.fix_prompt.strip():
        print("FAIL: NEEDS_FIX without a fix_prompt cannot drive the loop.")
        return 1

    # -- 4. FIX: brand-new Builder session ------------------------------------
    banner("STEP 4 — fix in a BRAND-NEW Builder session")
    exporter = executor.controller.state.batch.tasks[0]
    line("fix prompt chars", len(exporter.fix_prompt or ""))
    fix = executor.run_task_fix(timeout_s=args.timeout)
    line("outcome", fix.outcome.value)
    line("message", fix.message)
    line("exit code", fix.prompt_result.exit_code if fix.prompt_result else None)
    line("error", fix.prompt_result.error if fix.prompt_result else None)
    fix_builder = fix.session_id or "NOT_EXPOSED"
    line("fix builder session", fix_builder)
    if fix.outcome is not ExecutionOutcome.COMPLETED:
        print(f"FAIL: the fix Builder did not complete ({fix.outcome.value}).")
        return 1
    if fix_builder != "NOT_EXPOSED" and fix_builder == auditor_initial:
        print("FAIL: the fix ran in the SAME session as the auditor; isolation broken.")
        return 1

    # -- 5. independent deterministic test on the fixed repo ------------------
    banner("STEP 5 — independent deterministic test after the fix")
    rc, output = run_scratch_test(repo)
    line("test exit code", rc)
    line("test output", output.splitlines()[-1] if output else "(empty)")
    if rc != 0:
        print("FAIL: the scratch test does not pass after the fix.")
        return 1

    # -- 6. RE-AUDIT: resume the SAME auditor session --------------------------
    banner("STEP 6 — re-audit in the SAME auditor session (resume)")
    final = executor.run_task_audit(timeout_s=args.timeout)
    line("outcome", final.outcome.value)
    line("verdict", final.audit_verdict.verdict.value if final.audit_verdict else "NONE")
    line("message", final.message)
    line("exit code", final.prompt_result.exit_code if final.prompt_result else None)
    line("error", final.prompt_result.error if final.prompt_result else None)
    reaudit = final.session_id or "NOT_EXPOSED"
    line("auditor session (re-audit)", reaudit)
    if final.audit_verdict is not None:
        print("--- re-audit summary ---")
        print(final.audit_verdict.summary)
    if final.outcome is not ExecutionOutcome.COMPLETED:
        print(f"FAIL: the re-audit did not pass ({final.outcome.value}).")
        return 1

    task = executor.controller.state.batch.tasks[0]
    line("task state", task.state.value)
    line("task attempts", task.attempts)
    line("task audit rounds", task.audit_rounds)
    line("pipeline phase", executor.controller.machine.phase.value)
    line("next action", executor.next_action().value)
    if task.state is not TaskState.APPROVED:
        print("FAIL: the task must be APPROVED after a PASS re-audit.")
        return 1
    if executor.controller.machine.phase is not PipelinePhase.READY_FOR_FINAL_AUDIT:
        print("FAIL: the pipeline must be READY_FOR_FINAL_AUDIT after a PASS "
              "(Session 004: BATCH_COMPLETE is only reachable via the Final Auditor).")
        return 1

    # -- 7. session identity proof ---------------------------------------------
    banner("STEP 7 — session identity proof")
    line("AUDITOR_INITIAL_SESSION_ID", auditor_initial)
    line("FIX_BUILDER_SESSION_ID", fix_builder)
    line("AUDITOR_REAUDIT_SESSION_ID", reaudit)
    same_auditor = auditor_initial != "NOT_EXPOSED" and auditor_initial == reaudit
    isolated_fix = fix_builder != "NOT_EXPOSED" and fix_builder != auditor_initial
    line("initial == re-audit", same_auditor)
    line("fix != auditor", isolated_fix)
    if not (same_auditor and isolated_fix):
        print("FAIL: session isolation/reuse semantics were not proven.")
        return 1

    # -- 8. persisted evidence read back from SQLite ---------------------------
    banner("STEP 8 — persisted evidence read back from SQLite")
    workspace_id = controller.state.workspace.workspace_id
    batch_id = controller.state.batch.batch_id
    # A COMPLETE batch is not "active", so load by id (the in-flight recovery
    # path uses load_active_batch; a finished batch loads by id).
    stored = database.load_batch(batch_id)
    line("batch status", stored.status.value if stored else None)
    line("stored batch phase", stored.phase if stored else None)
    if stored is not None:
        t = stored.tasks[0]
        line("stored task state", t.state.value)
        line("stored verdict", t.latest_verdict)
        line("stored rounds", t.audit_rounds)
        line("stored attempts", t.attempts)
        line("stored auditor session", t.auditor_session_id)
        line("stored fix session", t.fix_session_id)
        ok_persisted = (
            t.state is TaskState.APPROVED
            and t.latest_verdict == "PASS"
            and t.auditor_session_id == task.auditor_session_id
            and t.fix_session_id == task.fix_session_id
            and stored.status is BatchStatus.READY_FOR_FINAL_AUDIT
        )
    else:
        ok_persisted = False
    line("persisted round-trip ok", ok_persisted)
    if not ok_persisted:
        print("FAIL: the durable state did not survive a reload.")
        return 1

    banner("SMOKE PASSED")
    line("total wall clock (s)", f"{time.monotonic() - started:.1f}")
    line("real model runs", "3 (initial audit, fix, re-audit)")
    line("scratch dir", ctx["scratch"])
    if not args.keep_scratch:
        database.close()
        shutil.rmtree(ctx["scratch"], ignore_errors=True)
        print("scratch directory removed (use --keep-scratch to inspect it).")
    else:
        database.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())