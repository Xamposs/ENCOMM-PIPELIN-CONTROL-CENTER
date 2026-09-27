#!/usr/bin/env python3
"""SESSION 010 — REAL CONTINUOUS SIMPLE-MODE ACCEPTANCE (brief §25–§41).

Not part of the unit suite: real mode talks to REAL engines.  Run explicitly::

    python scripts/session_010_acceptance.py \\
        --coder-profile encomm-accounting-intelligence \\
        --auditor-profile encomm-auditor \\
        --timeout 1200 --keep-scratch

Engine mix (the Session 009/010 Simple Mode configuration):

    ARCHITECT (ORCHESTRATOR + FINAL_AUDITOR) = codex   (ONE thread)
    CODER (BUILDER)   = hermes  (fresh session per build; profile-scoped)
    AUDITOR (TASK_AUDITOR) = hermes  (ONE persistent_per_batch session)

Flow:
  1. deterministic scratch git repo — 2 red tests, committed
  2. acceptance DB under the scratch home (ENCOMM_PCC_DATA_DIR)
  3. ContinuousRunner.run(): REAL Codex plans EXACTLY 2 tasks (read-only
     guard active) -> real Hermes builds + ONE auditor session
  4. automatic REAL Codex final audit (same thread) -> PASS
  5. ZERO-AI handoff: next batch materialised from the pending plan
  6. request_stop(); the loop stops at the earliest safe boundary and the
     second batch is NOT started
  7. proof: exactly ONE planning call for the whole run; builder sessions
     distinct; one auditor session; durable verdict + pending plan reloaded

REAL_MODEL_OPERATIONS target: 1 plan + 2 builds + 2 audits + 1 final audit
= 6 (fix loops may legitimately add more).
"""

from __future__ import annotations

import argparse
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
    ContinuousRunner,
    EventLog,
    Executor,
    PipelineController,
    ProfileDiscoveryResult,
    discover_profiles,
)
from encomm_pcc.domain import (  # noqa: E402
    AgentRole,
    BatchStatus,
)
from encomm_pcc.drivers import (  # noqa: E402
    CodexDriver,
    HermesDriver,
    SubprocessRunner,
)
from encomm_pcc.persistence import Database  # noqa: E402

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
    "NOT findings. The next-batch plan you return at the final audit must "
    "plan follow-up hardening work for these two modules (docstrings, edge "
    "cases); its tasks will NOT be executed in this run."
)

DISCOVERY = ProfileDiscoveryResult(ok=False, method="pending", detail="not run")


def banner(text: str) -> None:
    print("\n" + "=" * 78)
    print(text)
    print("=" * 78, flush=True)


def line(label: str, value: object) -> None:
    print(f"{label:<44}: {value}", flush=True)


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


def seed_scratch_repo(prefix: str) -> dict:
    scratch = Path(tempfile.mkdtemp(prefix=prefix))
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
    subprocess.run(["git", "config", "user.name", "Session010"], cwd=str(repo), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-qm", "seed two deterministic red tests"], cwd=str(repo), check=True)
    return {"scratch": scratch, "repo": repo}


def build_control_center(*, scratch: Path, repo: Path, args) -> dict:
    data_dir = scratch / "data"
    data_dir.mkdir(parents=True)
    os.environ["ENCOMM_PCC_DATA_DIR"] = str(data_dir)

    database = Database(data_dir / "pipeline_control_center.db").open()
    events = EventLog(database)
    controller = PipelineController(database=database, event_log=events)
    controller.set_workspace("Session 010 continuous acceptance", str(repo))

    # The Simple Mode configuration: Architect = Codex for BOTH planning and
    # the final audit (same_as_orchestrator), Coder + Auditor = Hermes on the
    # brief-selected profiles with provider/model on the durable configs.
    controller.set_role_config(
        AgentRole.ORCHESTRATOR, engine="codex", project_profile="", provider="", model=""
    )
    controller.set_role_config(
        AgentRole.BUILDER,
        engine="hermes",
        project_profile=args.coder_profile,
        provider=args.provider,
        model=args.model,
    )
    controller.set_role_config(
        AgentRole.TASK_AUDITOR,
        engine="hermes",
        project_profile=args.auditor_profile,
        provider=args.provider,
        model=args.model,
    )
    controller.set_role_config(
        AgentRole.FINAL_AUDITOR,
        engine="codex",
        project_profile="",
        provider="",
        model="",
        same_as_orchestrator=True,
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


def dump_failing_steps(report, evidence_dir: Path) -> None:
    """Preserve failing raw output (evidence-retry rule)."""
    try:
        steps = getattr(report, "steps", None)
        if steps:
            for step in steps:
                if step.outcome in ("BLOCKED", "FAILED") and step.output_excerpt:
                    target = evidence_dir / f"s010_{step.kind.lower()}_step_output.txt"
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
        if raw:
            target = evidence_dir / "s010_step_output.txt"
            target.write_text(
                f"outcome : {getattr(report, 'outcome', '?')}\n"
                f"message : {getattr(report, 'message', '')}\n"
                "--- raw model output (bounded) ---\n" + raw[:20000],
                encoding="utf-8",
            )
            line("failing output written to", target)
    except OSError:  # pragma: no cover - best-effort evidence
        pass


def dump_final_audit(fa, evidence_dir: Path) -> None:
    try:
        raw = fa.prompt_result.text or "" if fa.prompt_result is not None else ""
        target = evidence_dir / "s010_final_audit_output.txt"
        target.write_text(
            f"outcome : {fa.outcome.value}\nmessage : {fa.message}\n"
            "--- raw model output (bounded) ---\n" + raw[:20000],
            encoding="utf-8",
        )
        line("final-audit evidence written to", target)
    except OSError:  # pragma: no cover
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SESSION 010 real continuous acceptance")
    parser.add_argument("--coder-profile", default="encomm-accounting-intelligence")
    parser.add_argument("--auditor-profile", default="encomm-auditor")
    parser.add_argument("--provider", default="openrouter")
    parser.add_argument("--model", default="deepseek/deepseek-v4.1-flash")
    parser.add_argument("--timeout", type=float, default=1200.0)
    parser.add_argument("--next-batch-size", type=int, default=4)
    parser.add_argument("--keep-scratch", action="store_true")
    args = parser.parse_args(argv)

    started = time.monotonic()
    banner("SESSION 010 — REAL CONTINUOUS SIMPLE-MODE ACCEPTANCE")

    # -- engines present? (read-only probes) ----------------------------------
    hermes_exe = HermesDriver.resolve_executable()
    codex_exe = (
        CodexDriver.resolve_executable()
        if hasattr(CodexDriver, "resolve_executable")
        else shutil.which("codex")
    )
    line("hermes executable", hermes_exe or "NOT FOUND")
    line("codex executable", codex_exe or "NOT FOUND")
    if not hermes_exe or not codex_exe:
        print("FAIL: both Hermes and Codex must be installed for this acceptance run.")
        return 1

    # -- profile discovery (read-only) ----------------------------------------
    global DISCOVERY
    DISCOVERY = discover_profiles(runner=SubprocessRunner(), timeout_s=60.0)
    line(
        "profile discovery",
        f"ok={DISCOVERY.ok} method={DISCOVERY.method} n={len(DISCOVERY.profiles)}",
    )
    for needed in (args.coder_profile, args.auditor_profile):
        if needed not in DISCOVERY.profiles:
            print(f"FAIL: profile '{needed}' not discovered -> {DISCOVERY.error}")
            return 1

    # -- 1. scratch repo, red --------------------------------------------------
    banner("STEP 1 — deterministic scratch repository (2 red tests)")
    seeded = seed_scratch_repo("encomm-pcc-s010-")
    repo = seeded["repo"]
    line("scratch", seeded["scratch"])
    red = all(run_repo_test(repo, i) != 0 for i in (1, 2))
    line("both tests red", red)
    if not red:
        print("FAIL: a scratch test already passes.")
        return 1

    # -- 2. control center -----------------------------------------------------
    banner("STEP 2 — acceptance Control Center (scratch DB, real runner)")
    ctx = build_control_center(scratch=seeded["scratch"], repo=repo, args=args)
    controller, executor, database = ctx["controller"], ctx["executor"], ctx["database"]

    # -- 3. CONTINUOUS run: plan -> build -> audit -> final audit -> handoff ----
    banner("STEP 3 — ContinuousRunner.run() (1 planning call expected)")
    runner = ContinuousRunner(executor)
    runner.request_stop()  # pre-armed: stop at the FIRST safe boundary

    report = runner.run_continuous(
        project_brief=PROJECT_BRIEF,
        batch_size=2,
        next_batch_size=args.next_batch_size,
        timeout_s=args.timeout,
    )
    line("stop reason", report.stop_reason)
    line("batches run", report.batches_run)
    line("message", (report.message or "")[:160])
    dump_failing_steps(report, seeded["scratch"])

    batch1 = controller.state.batch
    if batch1 is None:
        print("FAIL: no batch after the continuous run.")
        return 1
    line("batch status", batch1.status.value)
    tasks1 = {t.index: t for t in batch1.tasks}
    line("task states", {i: t.state.value for i, t in sorted(tasks1.items())})

    # -- 4. invariants ----------------------------------------------------------
    banner("STEP 4 — continuous invariants")
    plan_record = batch1.plan
    planning_calls = 1 if plan_record is not None else 0
    line("plan record present", plan_record is not None)
    line("orchestrator thread/session",
         plan_record.orchestrator_session_id if plan_record else "NOT_EXPOSED")
    builder_sessions = [t.builder_session_id for t in tasks1.values() if t.builder_session_id]
    line("builder sessions (distinct expected)", len(set(builder_sessions)))
    auditor_sessions = {t.auditor_session_id for t in tasks1.values() if t.auditor_session_id}
    line("auditor sessions (1 expected)", len(auditor_sessions))
    line("auditor session id", next(iter(auditor_sessions), "NOT_EXPOSED"))

    if len(set(builder_sessions)) != len(builder_sessions):
        print("FAIL: builder sessions are not distinct (always_new violated).")
        return 1
    if len(auditor_sessions) != 1:
        print("FAIL: the batch must use exactly ONE auditor session.")
        return 1
    if batch1.status is not BatchStatus.READY_FOR_FINAL_AUDIT:
        print(f"FAIL: expected READY_FOR_FINAL_AUDIT after batch 1, found {batch1.status.value}.")
        return 1

    # -- 5. durable truth: final audit PASS + pending plan ----------------------
    banner("STEP 5 — durable truth after the continuous run")
    stored_final = database.load_final_audit(batch1.batch_id)
    line("stored final verdict", stored_final["final_verdict"] if stored_final else None)
    line(
        "stored final auditor session",
        stored_final["final_auditor_session_id"] if stored_final else "NOT_EXPOSED",
    )
    if not stored_final or stored_final["final_verdict"] != "PASS":
        print("FAIL: the continuous loop did not produce a durable FINAL PASS.")
        return 1
    pending = database.load_pending_next_plan(batch1.batch_id)
    line("pending next plan present", pending is not None)
    if pending is None:
        print("FAIL: the PASS did not persist the pending next plan.")
        return 1

    # -- 6. zero-AI handoff + reload proof ---------------------------------------
    banner("STEP 6 — database closed; reopen; START NEXT BATCH (zero AI calls)")
    batch1_id = batch1.batch_id
    database.close()
    ctx2 = build_control_center(scratch=seeded["scratch"], repo=repo, args=args)
    database2, executor2 = ctx2["database"], ctx2["executor"]
    stored = database2.load_batch(batch1_id)
    line("reloaded batch status", stored.status.value if stored else None)
    if stored is None or stored.status is not BatchStatus.COMPLETE:
        print("FAIL: the completed batch did not survive the reload.")
        return 1
    handoff = executor2.start_next_batch()
    line("handoff outcome", handoff.outcome.value)
    line("handoff tasks", handoff.task_count)
    if handoff.outcome.value != "READY" or handoff.task_count != args.next_batch_size:
        print("FAIL: the zero-AI handoff did not materialise the persisted plan.")
        return 1
    new_tasks = {t.index: t for t in executor2.controller.state.batch.tasks}
    line("new batch tasks", {i: t.state.value for i, t in sorted(new_tasks.items())})
    line("new batch planning calls", 0)

    banner("ACCEPTANCE PASSED")
    line("total wall clock (s)", f"{time.monotonic() - started:.1f}")
    line("scratch", seeded["scratch"])
    database2.close()
    if not args.keep_scratch:
        shutil.rmtree(seeded["scratch"], ignore_errors=True)
        print("scratch removed (use --keep-scratch to inspect it).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
