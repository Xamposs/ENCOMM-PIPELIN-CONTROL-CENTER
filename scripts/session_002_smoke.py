"""SESSION 002 REAL SMOKE — one controlled Hermes execution, end to end.

Not part of the unit suite: this script talks to a real engine.  Run it
explicitly::

    python scripts/session_002_smoke.py --profile encomm-pipeline-control-center

What it proves, in one controlled scenario:

    Control Center → Executor → HermesDriver → installed Hermes CLI
        → a genuinely NEW session → one prompt → real output
        → real process exit code → persisted application result

Safety properties (why this is safe to run):

* The prompt is a fixed literal that asks for a single token back and states
  that no tools, files, commands or repository work are to be used.
* The supervised workspace is a **fresh scratch directory** under the system
  temp dir — never this repository.
* The application database is written to a scratch data directory, so the real
  ``%LOCALAPPDATA%`` state is untouched.
* Nothing is retried blindly: at most ``--attempts`` (default 3) real runs are
  made, and a failure stops the script with the child's own error text.

Steps (each prints its own evidence):

1. scratch workspace + scratch database
2. read-only Hermes profile discovery (proves the profile exists)
3. dispatch through the real executor with the real ``SubprocessRunner``
4. verify the answer, the exit code and the session id
5. re-read the persisted task/session/event rows from SQLite
6. optionally prove ``--resume`` continues the session it created
7. optionally prove a per-invocation model override is honoured
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
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
from encomm_pcc.domain import AgentRole, PipelinePhase, TaskState  # noqa: E402
from encomm_pcc.drivers import HermesDriver, SubprocessRunner  # noqa: E402
from encomm_pcc.persistence import Database  # noqa: E402

EXPECTED_TOKEN = "ENCOMM_PCC_HERMES_SMOKE_OK"
SMOKE_PROMPT = (
    "Return exactly:\n\n"
    f"{EXPECTED_TOKEN}\n\n"
    "Do not use tools. Do not edit files. Do not run commands. "
    "Do not perform repository work. Do not add explanation."
)
RESUME_TOKEN = "ENCOMM_PCC_HERMES_SMOKE_RESUME_OK"
RESUME_PROMPT = (
    "Return exactly:\n\n"
    f"{RESUME_TOKEN}\n\n"
    "Do not use tools. Do not edit files. Do not run commands. "
    "Do not perform repository work. Do not add explanation."
)


def banner(text: str) -> None:
    print("\n" + "=" * 78)
    print(text)
    print("=" * 78, flush=True)


def line(label: str, value: object) -> None:
    print(f"{label:<24}: {value}", flush=True)


def build(source_label: str, *, profile: str, provider: str, model: str) -> dict:
    """Assemble a scratch control center over the real runner. No repo writes."""
    scratch = Path(tempfile.mkdtemp(prefix="encomm-pcc-smoke-"))
    workspace = scratch / "workspace"
    workspace.mkdir(parents=True)
    data_dir = scratch / "data"
    data_dir.mkdir(parents=True)
    os.environ["ENCOMM_PCC_DATA_DIR"] = str(data_dir)

    database = Database(data_dir / "pipeline_control_center.db").open()
    events = EventLog(database)
    controller = PipelineController(database=database, event_log=events)
    controller.set_workspace("Session 002 smoke workspace", str(workspace))
    controller.set_role_config(
        AgentRole.BUILDER,
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
        "workspace": workspace,
        "database": database,
        "controller": controller,
        "executor": executor,
        "events": events,
    }


_discovery = ProfileDiscoveryResult(ok=False, method="pending", detail="not run")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SESSION 002 real Hermes smoke")
    parser.add_argument("--profile", default="encomm-pipeline-control-center")
    parser.add_argument("--provider", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--skip-resume", action="store_true")
    parser.add_argument("--probe-model", default="", help="Prove a per-run model override.")
    parser.add_argument("--probe-provider", default="")
    parser.add_argument("--keep-scratch", action="store_true")
    args = parser.parse_args(argv)

    global _discovery  # noqa: PLW0603 - simple script-level wiring

    banner("SESSION 002 — REAL HERMES SMOKE")
    started = time.monotonic()

    # -- 0. installed CLI ---------------------------------------------------
    from encomm_pcc.drivers import hermes as hermes_module

    repo_flag = hermes_module._LIVE_SMOKE_VERIFIED  # noqa: SLF001
    executable = HermesDriver.resolve_executable()
    line("hermes executable", executable)
    if not executable:
        print("FAIL: the Hermes CLI is not on PATH.")
        return 1
    # The adapter goes on trial for this run: the executor refuses to dispatch an
    # unverified driver, and the repository value is only flipped after a PASS.
    hermes_module._LIVE_SMOKE_VERIFIED = True  # noqa: SLF001
    line("driver flag in repo", repo_flag)
    line("driver flag for trial", HermesDriver.capabilities().implemented)
    line("driver resume flag", HermesDriver.capabilities().supports_resume)

    # -- 1. read-only profile discovery -------------------------------------
    banner("STEP 1 — read-only profile discovery")
    _discovery = discover_profiles(runner=SubprocessRunner(), timeout_s=60.0)
    line("discovery ok", _discovery.ok)
    line("discovery method", _discovery.method)
    line("profiles found", len(_discovery.profiles))
    if not _discovery.ok or args.profile not in _discovery.profiles:
        print(f"FAIL: profile '{args.profile}' was not discovered -> {_discovery.error}")
        return 1
    print(f"profile '{args.profile}' confirmed present.", flush=True)

    ctx = build("smoke", profile=args.profile, provider=args.provider, model=args.model)
    controller = ctx["controller"]
    executor = ctx["executor"]
    database = ctx["database"]
    line("scratch dir", ctx["scratch"])
    line("supervised workspace", ctx["workspace"])

    # -- 2. the controlled run ---------------------------------------------
    banner("STEP 2 — one controlled execution through executor → HermesDriver")
    line("model override", args.model or "(profile default)")
    line("provider override", args.provider or "(profile default)")

    report = None
    attempts = max(1, args.attempts)
    for attempt in range(1, attempts + 1):
        print(f"\n-- real attempt {attempt}/{attempts} --", flush=True)
        report = executor.dispatch_single_task(
            TaskSpec(title="Session 002 controlled smoke task", prompt=SMOKE_PROMPT),
            timeout_s=args.timeout,
        )
        result = report.prompt_result
        line("outcome", report.outcome.value)
        line("executor_started", report.executor_started)
        line("phase after run", report.phase.value)
        if result is not None:
            line("process exit code", result.exit_code)
            line("duration (s)", f"{result.duration_s:.1f}")
            line("simulated", result.simulated)
            line("error", result.error)
        if report.outcome is ExecutionOutcome.COMPLETED:
            break
        print(f"attempt {attempt} did not complete: {report.message}", flush=True)
        if attempt < attempts:
            print("resetting stop/control flags and retrying once more…", flush=True)
            executor.clear_control_flags()
            controller.request_stop()
            controller.machine.reset()
            controller.state.phase = controller.machine.phase

    result = report.prompt_result
    text = (result.text or "").strip() if result is not None else ""
    session_id = report.session_id

    banner("STEP 3 — verification of the real result")
    line("outcome", report.outcome.value)
    line("expected token", EXPECTED_TOKEN)
    line("answer contains token", EXPECTED_TOKEN in text)
    line("real session id", session_id or "NOT_EXPOSED")
    line("process exit code", result.exit_code if result is not None else None)
    if text:
        print("\n--- child output ---")
        print(text)
        print("--- end child output ---")

    ok = (
        report.outcome is ExecutionOutcome.COMPLETED
        and EXPECTED_TOKEN in text
        and result is not None
        and result.exit_code == 0
        and result.simulated is False
        and report.executor_started is True
    )

    # -- 3. persisted evidence ---------------------------------------------
    banner("STEP 4 — persisted evidence read back from SQLite")
    workspace_id = controller.state.workspace.workspace_id
    batch = database.load_active_batch(workspace_id)
    if batch is not None:
        line("batch id", batch.batch_id)
        line("batch status", batch.status.value)
        for task in batch.tasks:
            line("task id", task.task_id)
            line("task state", task.state.value)
            line("task attempts", task.attempts)
            line("task prompt chars", len(task.prompt))
            line("task last_error", task.last_error)
    sessions = database.list_sessions(workspace_id)
    line("session rows", len(sessions))
    for row in sessions:
        line("  session id", row["session_id"])
        line("  external", row["external"])
        line("  driver", row["driver_id"])
        line("  metadata", (row["metadata_json"] or "")[:200])
    events = database.recent_events(limit=200)
    result_events = [e for e in events if "result" in e["message"] or "Task" in e["message"]]
    line("app_events total", len(events))
    print("\n--- last task events ---")
    for row in reversed(result_events[:6]):
        payload = (row["payload_json"] or "")[:400]
        print(f"[{row['level']}] {row['ts']} {row['message']}\n    payload: {payload}")

    if not ok:
        banner("SMOKE FAILED")
        hermes_module._LIVE_SMOKE_VERIFIED = repo_flag  # noqa: SLF001
        print(
            "The real execution did not meet the Session 002 pass criteria; the "
            "driver's implemented flag stays False in the repository."
        )
        if result is not None and result.error:
            print(f"child error: {result.error}")
        return 1

    # -- 4. optional resume proof ------------------------------------------
    if not args.skip_resume:
        banner("STEP 5 — resume proof (continues the session just created)")
        if not session_id:
            print("SKIPPED: the CLI exposed no session id, so resume cannot be addressed.")
        else:
            driver, rresult = _dispatch_resume(
                str(ctx["workspace"]), args.profile, session_id, args.timeout
            )
            line("resume exit code", rresult.exit_code)
            line("resume ok", rresult.ok)
            line("resume error", rresult.error)
            line("resume answer", (rresult.text or "").strip())
            line("resume session id", rresult.session_id or "NOT_EXPOSED")
            resumed_ok = (
                rresult.ok
                and rresult.exit_code == 0
                and RESUME_TOKEN in (rresult.text or "")
                and rresult.session_id == session_id
            )
            line("resume verdict", "PROVEN" if resumed_ok else "NOT PROVEN")

    # -- 5. optional model-override proof ----------------------------------
    if args.probe_model:
        banner("STEP 6 — per-invocation model override proof")
        controller.request_stop()
        controller.machine.reset()
        controller.state.phase = controller.machine.phase
        executor.clear_control_flags()
        controller.set_role_config(
            AgentRole.BUILDER,
            engine="hermes",
            project_profile=args.profile,
            provider=args.probe_provider,
            model=args.probe_model,
        )
        probe = executor.dispatch_single_task(
            TaskSpec(title="model override probe", prompt=SMOKE_PROMPT), timeout_s=args.timeout
        )
        presult = probe.prompt_result
        stream = (presult.metadata or {}).get("stream", {}) if presult else {}
        line("probe outcome", probe.outcome.value)
        line("probe exit code", presult.exit_code if presult else None)
        line("model reported by CLI", stream.get("model"))
        line("answer ok", EXPECTED_TOKEN in (presult.text or "") if presult else False)

    banner("SMOKE PASSED")
    line("total wall clock (s)", f"{time.monotonic() - started:.1f}")
    line("scratch dir", ctx["scratch"])
    if not args.keep_scratch:
        database.close()
        shutil.rmtree(ctx["scratch"], ignore_errors=True)
        print("scratch directory removed (use --keep-scratch to inspect it).")
    else:
        database.close()
    return 0


def _dispatch_resume(workspace: str, profile: str, session_id: str, timeout: float):  # noqa: ANN202
    """Prove the driver's resume path against the session the smoke created.

    ``HermesDriver.resume_session`` is capability-gated on a flag that is only
    set once resume has been *proven*, so this attempt flips the flag for the
    duration of the proof and restores it when the attempt fails.  Nothing is
    fabricated either way: the proof is a real ``--resume`` run, and its verdict
    is whatever the real process did.
    """
    from encomm_pcc.drivers import SessionRequest
    from encomm_pcc.drivers import hermes as hermes_module

    previous_flag = hermes_module._LIVE_RESUME_VERIFIED  # noqa: SLF001
    hermes_module._LIVE_RESUME_VERIFIED = True  # noqa: SLF001
    driver = HermesDriver(runner=SubprocessRunner())
    try:
        request = SessionRequest(
            role=AgentRole.BUILDER,
            workspace_path=workspace,
            project_profile=profile,
        )
        session = driver.resume_session(session_id, request)
        handle = driver.send_prompt(session, RESUME_PROMPT)
        result = driver.wait_for_completion(handle, timeout_s=timeout)
    finally:
        hermes_module._LIVE_RESUME_VERIFIED = previous_flag  # noqa: SLF001
    return driver, result


if __name__ == "__main__":
    raise SystemExit(main())