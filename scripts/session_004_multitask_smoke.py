"""SESSION 004 REAL SMOKE — one orchestrated multi-task batch, live.

Not part of the unit suite: default mode talks to a real engine.  Run it
explicitly (with exactly the same profile the Control Center uses)::

    python scripts/session_004_multitask_smoke.py --profile encomm-pipeline-control-center

What it proves, in one controlled scenario:

    deterministic scratch git repo with 4 red Python tests
        ↓
    ONE real ORCHESTRATOR call   → exactly 4 valid tasks (strict plan parse)
        ↓
    per task:                                  (all inside one autonomous run)
        fresh BUILDER session  → creates feature_N.py
        Task Auditor (ONE session shared by ALL audits) → PASS
    ↓
    batch → READY_FOR_FINAL_AUDIT  (never BATCH_COMPLETE)
    SQLite reload preserves the terminal state + Batch Summary

COST MODEL — normal success = 1 Orchestrator + 4 Builders + 4 Audits = 9 real
model operations.  A genuine NEEDS_FIX adds its bounded fix/re-audit pair; the
report records the actual count.

COST GUARD — `--fake` first: the whole batch runs against an in-process
scripted driver (no network) so every post-processing path (plan parse,
session identity, SQLite reload, token aggregation) is proven BEFORE the first
real model call.  The real run happens once; a later reporting bug is fixed
and the already-persisted evidence is re-read, never brute-forced.

Safety properties (why this is safe to run):

* the scratch project is a fresh git repo under the system temp dir — never
  this Control Center repository and never a production repository;
* the application database is written to a scratch data directory;
* every prompt hard-restricts tool use to the scratch workspace path;
* session ids are read from what the engine really reported — never fabricated.
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
    AUDIT_ENVELOPE_END,
    AUDIT_ENVELOPE_START,
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
from encomm_pcc.core.executor import next_task_action  # noqa: E402

# ----------------------------------------------------------------------------
# deterministic scratch project: four independent modules, four red tests
# ----------------------------------------------------------------------------
FEATURES = {
    1: {
        "name": "feature_1 (add)",
        "test": (
            "from feature_1 import add\n"
            "assert add(2, 3) == 5, f\"add(2, 3) returned {add(2, 3)}\"\n"
            "assert add(0, 0) == 0\n"
            "print('test_feature_1: OK')\n"
        ),
    },
    2: {
        "name": "feature_2 (multiply)",
        "test": (
            "from feature_2 import multiply\n"
            "assert multiply(3, 4) == 12, f\"multiply(3, 4) returned {multiply(3, 4)}\"\n"
            "assert multiply(0, 9) == 0\n"
            "print('test_feature_2: OK')\n"
        ),
    },
    3: {
        "name": "feature_3 (is_even)",
        "test": (
            "from feature_3 import is_even\n"
            "assert is_even(4) is True, 'is_even(4) must be True'\n"
            "assert is_even(7) is False, 'is_even(7) must be False'\n"
            "print('test_feature_3: OK')\n"
        ),
    },
    4: {
        "name": "feature_4 (greet)",
        "test": (
            "from feature_4 import greet\n"
            "assert greet('Ada') == 'Hello, Ada!', f\"got {greet('Ada')!r}\"\n"
            "assert isinstance(greet(''), str)\n"
            "print('test_feature_4: OK')\n"
        ),
    },
}

PROJECT_BRIEF = (
    "The repository contains four deterministic Python test files: "
    "tests/test_feature_1.py, tests/test_feature_2.py, tests/test_feature_3.py "
    "and tests/test_feature_4.py. Each imports ONE module from the repository "
    "root (feature_1, feature_2, feature_3, feature_4) that does not exist yet. "
    "Plan EXACTLY four implementation tasks: task i creates feature_i.py at the "
    "repository root implementing exactly the functions and behaviour its test "
    "asserts. The primary acceptance criterion for each task is that running "
    "'python tests/test_feature_N.py' exits 0. Each audit_focus must verify the "
    "actual module in the repository and run that test. Do not modify any test "
    "file. Do not plan anything beyond these four modules."
)

_DISCOVERY = ProfileDiscoveryResult(ok=False, method="pending", detail="not run")


# ----------------------------------------------------------------------------
# smoke internals
# ----------------------------------------------------------------------------
def banner(text: str) -> None:
    print("\n" + "=" * 78)
    print(text)
    print("=" * 78, flush=True)


def line(label: str, value: object) -> None:
    print(f"{label:<34}: {value}", flush=True)


def run_repo_test(repo: Path, index: int) -> int:
    """Run the deterministic test with the REPO ROOT importable.

    ``python tests/test_feature_N.py`` only puts ``tests/`` on ``sys.path``,
    so a repo-root module (feature_N.py) would not be importable and the test
    would fail even when implemented correctly.  Running the same file with
    the workspace root prepended to ``sys.path`` mirrors how the Auditor
    verifies it and matches the acceptance criterion "the test exits 0".
    """
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


def seed_scratch_repo() -> dict:
    """Seed the deterministic scratch git repo; return paths + red-test proof."""
    scratch = Path(tempfile.mkdtemp(prefix="encomm-pcc-s004-"))
    repo = scratch / "workspace"
    repo.mkdir(parents=True)
    tests = repo / "tests"
    tests.mkdir()
    for index, spec in FEATURES.items():
        (tests / f"test_feature_{index}.py").write_text(spec["test"], encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "smoke@localhost"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "Session004"], cwd=str(repo), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-qm", "seed four deterministic tests"], cwd=str(repo), check=True)
    return {"scratch": scratch, "repo": repo}


def load_config(*, args: argparse.Namespace) -> dict:
    """Assemble a controller/executor/runner over a scratch database."""
    if not args.fake:
        from encomm_pcc.core.executor import RepoFingerprint  # noqa: F401,PLE0611
        from encomm_pcc.core.repo_fingerprint import capture_repo_fingerprint  # noqa: F401

    return {
        "orchestrator": {"provider": args.orchestrator_provider, "model": args.orchestrator_model},
        "builder": {"provider": args.builder_provider, "model": args.builder_model},
        "auditor": {"provider": args.auditor_provider, "model": args.auditor_model},
    }


def build_fake_plan_text(n: int) -> str:
    from encomm_pcc.core import PLAN_ENVELOPE_END, PLAN_ENVELOPE_START

    tasks = [
        {
            "index": i,
            "title": f"Implement {FEATURES[i]['name']}",
            "implementation_prompt": (
                f"Create feature_{i}.py at the repository root implementing "
                f"exactly the functions and behaviour "
                f"tests/test_feature_{i}.py asserts. Do NOT modify any test file."
            ),
            "acceptance_criteria": [f"python tests/test_feature_{i}.py exits 0"],
            "audit_focus": [f"verify feature_{i}.py exists and its test passes"],
        }
        for i in range(1, n + 1)
    ]
    payload = {
        "batch_title": "Four deterministic scratch features",
        "batch_objective": "Implement four independent modules so their tests pass.",
        "tasks": tasks,
    }
    return f"{PLAN_ENVELOPE_START}\n{json.dumps(payload, indent=2)}\n{PLAN_ENVELOPE_END}"


class ScriptedSmokeDriver:
    """In-process driver for `--fake`: proves all post-processing, zero cost."""

    driver_id = "smoke_fake"
    display_name = "Scripted Smoke Engine"
    executables = ("python",)

    shared: list[PromptResult] = []
    starts: int = 0
    resumed: list[str] = []

    @classmethod
    def reset(cls) -> None:
        cls.shared = []
        cls.starts = 0
        cls.resumed = []

    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name=cls.display_name,
            supports_sessions=True,
            supports_resume=True,
            implemented=True,
            notes="Fake driver for the session_004 smoke script.",
        )

    def __init__(self, runner=None) -> None:  # noqa: ANN001
        self._runner = runner

    def start_session(self, request: SessionRequest) -> DriverSession:
        type(self).starts += 1
        return DriverSession(
            driver_id=self.driver_id,
            role=request.role,
            session_id=None,
            metadata={"profile": request.project_profile, "workspace_path": request.workspace_path},
        )

    def resume_session(self, session_id: str, request: SessionRequest) -> DriverSession:
        type(self).resumed.append(str(session_id))
        session = self.start_session(request)
        session.session_id = str(session_id)
        session.external = True
        return session

    def send_prompt(self, session: DriverSession, prompt: str) -> PromptHandle:
        return PromptHandle(session=session, prompt=prompt)

    def wait_for_completion(self, handle: PromptHandle, timeout_s: float | None = None):
        if self._runner is not None:
            self._runner.run(ProcessSpec(argv=["smoke-fake"], timeout_s=timeout_s))
        if not type(self).shared:
            raise AssertionError("ScriptedSmokeDriver: no scripted result left")
        result = type(self).shared.pop(0)
        if result.session_id:
            handle.session.session_id = result.session_id
            handle.session.external = True
        return result


def _ok(session_id: str, text: str) -> PromptResult:
    return PromptResult(
        ok=True, text=text, session_id=session_id, exit_code=0, duration_s=0.05,
        metadata={"stream": {"saw_result": True, "tokens": {"input": 10, "output": 5, "total": 15}}},
    )


def _verdict(value: str) -> str:
    return (
        f"{AUDIT_ENVELOPE_START}\n"
        + json.dumps(
            {
                "verdict": value,
                "summary": "ok",
                "findings": [],
                "fix_prompt": "",
            }
        )
        + f"\n{AUDIT_ENVELOPE_END}"
    )


class _FakeRunner:
    """Records specs and always 'succeeds' — for the scripted smoke mode."""

    specs: list[ProcessSpec] = []

    def run(self, spec: ProcessSpec):
        self.specs.append(spec)
        return __import__(
            "encomm_pcc.drivers.process", fromlist=["ProcessResult"]
        ).ProcessResult(argv=spec.argv_list(), exit_code=0, stdout="", stderr="", duration_s=0.05)


def build_control_center(*, scratch: Path, repo: Path, args: argparse.Namespace) -> dict:
    """Wire ORCHESTRATOR / BUILDER / TASK_AUDITOR roles + a real executor."""
    data_dir = scratch / "data"
    data_dir.mkdir(parents=True)
    os.environ["ENCOMM_PCC_DATA_DIR"] = str(data_dir)

    database = Database(data_dir / "pipeline_control_center.db").open()
    events = EventLog(database)
    controller = PipelineController(database=database, event_log=events)
    controller.set_workspace("Session 004 smoke workspace", str(repo))

    if args.fake:
        registry = DriverRegistry()
        registry.register(ScriptedSmokeDriver)
        ScriptedSmokeDriver.reset()
        ScriptedSmokeDriver.shared = [_ok("orch_1", build_fake_plan_text(4))]
        for i in range(1, 5):
            ScriptedSmokeDriver.shared.append(_ok(f"builder_{i}", f"feature {i} implemented"))
            ScriptedSmokeDriver.shared.append(_ok("auditor_1", _verdict("PASS")))
        for role in (AgentRole.ORCHESTRATOR, AgentRole.BUILDER, AgentRole.TASK_AUDITOR):
            controller.set_role_config(role, engine="smoke_fake", project_profile=args.profile)
        runner = _FakeRunner()
    else:
        registry = None
        for role, values in (
            (AgentRole.ORCHESTRATOR, build_config["orchestrator"]),  # noqa: F821 - set below
            (AgentRole.BUILDER, build_config["builder"]),  # noqa: F821
            (AgentRole.TASK_AUDITOR, build_config["auditor"]),  # noqa: F821
        ):
            controller.set_role_config(
                role, engine="hermes", project_profile=args.profile,
                provider=values["provider"], model=values["model"],
            )
        runner = SubprocessRunner()

    executor = Executor(
        controller,
        registry=registry,
        runner=runner,
        database=database,
        event_log=events,
        profile_discovery=lambda **kwargs: _DISCOVERY,
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


# ----------------------------------------------------------------------------
# smoke flow
# ----------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    global build_config  # noqa: PLW0603
    parser = argparse.ArgumentParser(description="SESSION 004 real multi-task smoke")
    parser.add_argument("--profile", default="encomm-pipeline-control-center")
    parser.add_argument("--orchestrator-provider", default="")
    parser.add_argument("--orchestrator-model", default="")
    parser.add_argument("--builder-provider", default="")
    parser.add_argument("--builder-model", default="")
    parser.add_argument("--auditor-provider", default="")
    parser.add_argument("--auditor-model", default="")
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--keep-scratch", action="store_true")
    parser.add_argument("--fake", action="store_true", help="in-process scripted run (no engine)")
    args = parser.parse_args(argv)
    build_config = load_config(args=args)

    started = time.monotonic()

    banner("SESSION 004 — REAL ORCHESTRATED MULTI-TASK BATCH SMOKE")
    if args.fake:
        print("FAKE MODE — scripted in-process driver; NO real model calls.", flush=True)
    else:
        executable = HermesDriver.resolve_executable()
        line("hermes executable", executable)
        if not executable:
            print("FAIL: the Hermes CLI is not on PATH.")
            return 1
        line("driver implemented", HermesDriver.capabilities().implemented)
        line("driver resume", HermesDriver.capabilities().supports_resume)

    # -- 0. read-only profile discovery -------------------------------------
    banner("STEP 0 — read-only profile discovery")
    if not args.fake:
        _DISCOVERY = discover_profiles(runner=SubprocessRunner(), timeout_s=60.0)  # noqa: PLW0603
        line("discovery ok", _DISCOVERY.ok)
        line("method", _DISCOVERY.method)
        line("profiles found", len(_DISCOVERY.profiles))
        if not _DISCOVERY.ok or args.profile not in _DISCOVERY.profiles:
            print(f"FAIL: profile '{args.profile}' not discovered -> {_DISCOVERY.error}")
            return 1

    # -- 1. seed the deterministic scratch repository (tests RED) ------------
    banner("STEP 1 — deterministic scratch repository (4 red tests, committed)")
    seeded = seed_scratch_repo()
    repo = seeded["repo"]
    line("scratch dir", seeded["scratch"])
    line("scratch repo", repo)
    red = True
    for index in range(1, 5):
        rc = run_repo_test(repo, index)
        red = red and rc != 0
    line("all four tests red before the batch", red)
    if not red:
        print("FAIL: a scratch test passes already; the repo is not red.")
        return 1

    # -- 2. control center over scratch state --------------------------------
    banner("STEP 2 — build the Control Center (scratch DB + real runner)")
    ctx = build_control_center(scratch=seeded["scratch"], repo=repo, args=args)
    controller = ctx["controller"]
    executor = ctx["executor"]
    database = ctx["database"]

    # -- 3. THE ONE autonomous batch run --------------------------------------
    banner("STEP 3 — ONE autonomous run: orchestrator → 4 tasks → auditor")
    report = BatchRunner(executor).run_batch(
        project_brief=PROJECT_BRIEF,
        batch_size=4,
        timeout_s=args.timeout,
    )
    line("batch outcome", report.outcome.value)
    line("message", (report.message or "")[:160])
    line("operations", report.operation_counts())
    line("token totals per kind", report.token_totals())
    if report.outcome is not BatchOutcome.READY_FOR_FINAL_AUDIT:
        # Evidence-driven diagnosis: preserve the raw model output of the
        # failing operation so a BLOCKED/FAILED batch can be understood from
        # scratch evidence alone (brief §27: never brute-force).
        evidence_dir = seeded["scratch"]
        try:
            for step in report.steps:
                if step.outcome in ("BLOCKED", "FAILED") and step.output_excerpt:
                    target = evidence_dir / f"{step.kind.lower()}_step_output.txt"
                    target.write_text(
                        "\n".join(
                            [
                                f"step kind      : {step.kind}",
                                f"outcome       : {step.outcome}",
                                f"message       : {step.message}",
                                "--- raw model output (bounded) ---",
                                step.output_excerpt,
                            ]
                        ),
                        encoding="utf-8",
                    )
                    line("failing output written to", target)
        except OSError:  # pragma: no cover - best-effort evidence
            pass
        print(f"FAIL: batch ended {report.outcome.value}, expected "
              "READY_FOR_FINAL_AUDIT.")
        print(f"Scratch preserved for evidence: {evidence_dir}")
        return 1

    tasks = controller.state.batch.tasks
    line("tasks approved", f"{sum(1 for t in tasks if t.state is TaskState.APPROVED)}/{len(tasks)}")
    line("pipeline phase", executor.controller.machine.phase.value)
    if executor.controller.machine.phase is PipelinePhase.BATCH_COMPLETE:
        print("FAIL: no path may reach BATCH_COMPLETE without the Final Auditor.")
        return 1
    if not all(t.state is TaskState.APPROVED for t in tasks):
        print("FAIL: not every task is APPROVED.")
        return 1

    # -- 4. independent deterministic tests -----------------------------------
    banner("STEP 4 — independent deterministic acceptance runs")
    if args.fake:
        print("SKIPPED in --fake mode: the scripted driver creates no files; "
              "the real run verifies the tests (exit 0) independently.",
              flush=True)
    else:
        for index in range(1, 5):
            rc = run_repo_test(repo, index)
            line(f"test_feature_{index}.py", f"exit {rc}")
            if rc != 0:
                print(f"FAIL: test_feature_{index}.py does not pass.")
                return 1

    # -- 5. session identity proof --------------------------------------------
    banner("STEP 5 — session identity proof")
    builder_ids = [t.builder_session_id for t in tasks if t.builder_session_id]
    auditor_ids = sorted({t.auditor_session_id for t in tasks if t.auditor_session_id})
    orchestrator_sid = None
    plan_record = controller.state.batch.plan
    if plan_record is not None:
        orchestrator_sid = plan_record.orchestrator_session_id
    line("ORCHESTRATOR session", orchestrator_sid or "NOT_EXPOSED")
    for i, sid in enumerate(builder_ids, start=1):
        line(f"BUILDER task {i} session", sid or "NOT_EXPOSED")
    line("TASK AUDITOR session(s)", auditor_ids or "NOT_EXPOSED")
    all_builders_distinct = len(set(builder_ids)) == 4 and len(builder_ids) == 4
    single_auditor = len(auditor_ids) == 1
    auditor_not_builder = not (set(auditor_ids) & set(builder_ids))
    line("four distinct Builder sessions", all_builders_distinct)
    line("ONE shared Auditor session", single_auditor)
    line("Auditor distinct from Builders", auditor_not_builder)
    if not (all_builders_distinct and single_auditor and auditor_not_builder):
        print("FAIL: session identity contract violated.")
        return 1
    if orchestrator_sid and orchestrator_sid in builder_ids:
        print("FAIL: the Orchestrator session must not equal a Builder session.")
        return 1

    # -- 6. state reload from SQLite ------------------------------------------
    banner("STEP 6 — SQLite reload preserves READY_FOR_FINAL_AUDIT")
    workspace_id = controller.state.workspace.workspace_id
    batch_id = controller.state.batch.batch_id
    stored = database.load_batch(batch_id)
    line("stored batch status", stored.status.value if stored else None)
    line("stored batch phase", stored.phase if stored else None)
    line("stored plan status", stored.plan.plan_status if stored and stored.plan else None)
    line("stored final phase", stored.plan.final_phase if stored and stored.plan else None)
    if stored is None:
        print("FAIL: the batch did not survive a reload.")
        return 1
    if stored.status is not BatchStatus.READY_FOR_FINAL_AUDIT:
        print("FAIL: reload must preserve READY_FOR_FINAL_AUDIT.")
        return 1
    if stored.plan is None or stored.plan.batch_summary_json is None:
        print("FAIL: the durable Batch Summary is missing.")
        return 1

    actions = next_task_action(batch=stored, phase=executor.controller.machine.phase)
    line("next action after reload", actions.value)

    banner("SMOKE PASSED")
    line("total wall clock (s)", f"{time.monotonic() - started:.1f}")
    line("real model operations", report.operation_counts())
    line("batch outcome", report.outcome.value)
    line("final phase", executor.controller.machine.phase.value)
    line("scratch dir", seeded["scratch"])
    database.close()
    if not args.keep_scratch:
        shutil.rmtree(seeded["scratch"], ignore_errors=True)
        print("scratch removed (use --keep-scratch to inspect it).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())