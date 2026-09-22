"""Session 006 live validation — exactly TWO real Codex model operations.

REAL CALL 1 — new-session proof: one tiny controlled prompt through the real
``CodexDriver`` against a scratch git repository.  Proves: fresh session, real
thread id from the ``--json`` stream, marker response, exit code 0, and that
the session is discoverable through the new read-only discovery path and
bindable through the same generic path the UI uses.

REAL CALL 2 — resume + FINAL_AUDITOR proof: the EXACT session from CALL 1 is
bound to ``AgentRole.FINAL_AUDITOR`` through the generic binding path, and the
Session 005 final-audit pipeline runs over a deterministic completed scratch
batch — through Codex.  Proves: external session selection/binding, resume of
the same thread id, the generic FINAL_AUDITOR path with a non-Hermes engine,
strict final-audit parsing, PASS → BATCH_COMPLETE, next-plan persistence, and
no automatic next-batch start.

Cost guard (brief §19/§20): exactly 2 model operations, no retries of
successful calls, scratch-only workspace, production repo untouched.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from encomm_pcc.core import PipelineController  # noqa: E402
from encomm_pcc.core.events import NullEventLog  # noqa: E402
from encomm_pcc.core.executor import Executor, FinalAuditOutcome  # noqa: E402
from encomm_pcc.core.repo_fingerprint import capture_repo_fingerprint  # noqa: E402
from encomm_pcc.domain import (  # noqa: E402
    AgentRole,
    BatchState,
    BatchPlan,
    BatchPlanRecord,
    PipelinePhase,
    PlannedTask,
    SessionPolicy,
    TaskState,
    TaskStateRecord,
)
from encomm_pcc.drivers import CodexDriver, DriverRegistry, SessionRequest, SubprocessRunner  # noqa: E402
from encomm_pcc.persistence import Database  # noqa: E402

MARKER_NEW = "ENCOMM_PCC_CODEX_NEW_SESSION_OK"
PROMPT_NEW = (
    "Read nothing outside this scratch workspace. Do not edit files.\n"
    "Reply exactly:\n"
    f"{MARKER_NEW}"
)

FEATURES = {
    1: ("add", "def add(a, b):\n    return a + b\n", "assert add(2, 3) == 5"),
    2: ("subtract", "def subtract(a, b):\n    return a - b\n", "assert subtract(5, 2) == 3"),
    3: ("multiply", "def multiply(a, b):\n    return a * b\n", "assert multiply(3, 4) == 12"),
    4: (
        "capitalize_words",
        "def capitalize_words(s):\n    return ' '.join(w.capitalize() for w in s.split())\n",
        "assert capitalize_words('hello world') == 'Hello World'",
    ),
}


class CountingRunner:
    """Counts every real launch and preserves the raw protocol output.

    Session 006 evidence discipline (brief §20): the full stdout of every
    real Codex process is written to the scratch dir so a post-processing
    surprise can be diagnosed from preserved evidence — a successful model
    call is never repeated because local parsing failed.
    """

    def __init__(self, raw_dir: Path) -> None:
        self.inner = SubprocessRunner()
        self.launched: list[list[str]] = []
        self._raw_dir = raw_dir
        self._counter = 0

    def run(self, spec):
        self._counter += 1
        self.launched.append(spec.argv_list())
        result = self.inner.run(spec)
        raw_path = self._raw_dir / f"call_{self._counter}_raw.jsonl"
        try:
            raw_path.write_text(result.stdout or "", encoding="utf-8")
            (self._raw_dir / f"call_{self._counter}_stderr.txt").write_text(
                result.stderr or "", encoding="utf-8"
            )
        except OSError:
            pass
        return result


def run_git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


def seed_scratch_repo(root: Path) -> Path:
    repo = root / "scratch_repo"
    repo.mkdir(parents=True)
    run_git(repo, "init", "-q")
    run_git(repo, "config", "user.email", "smoke@localhost")
    run_git(repo, "config", "user.name", "PCC Smoke")
    # `.gitignore` keeps test-run artefacts (__pycache__) out of the porcelain
    # status so the read-only final-audit guard compares like with like.
    (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
    (repo / "README.md").write_text("Scratch repo for the Session 006 live proof.\n", encoding="utf-8")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "seed")
    return repo


def seed_completed_batch(controller: PipelineController, repo: Path) -> str:
    """Materialise a 4-task batch as completed (all APPROVED) — durable facts."""
    controller.set_workspace("Codex smoke", str(repo))
    batch = BatchState(workspace_id=controller.state.workspace.workspace_id, size=4)
    plan = BatchPlan(
        batch_title="Scratch features",
        batch_objective="Four deterministic features",
        tasks=[
            PlannedTask(
                index=i,
                title=f"Feature {i}: {name}",
                implementation_prompt=f"Implement {name}.",
                acceptance_criteria=[f"python tests/test_feature_{i}.py exits 0"],
                audit_focus=[f"{name} behaves as specified"],
            )
            for i, (name, _, _) in FEATURES.items()
        ],
    )
    batch.plan = BatchPlanRecord(
        plan=plan,
        project_brief="Four deterministic features",
        requested_size=4,
        plan_status="PLANNED",
        orchestrator_session_id="smoke_orch_historical",
        baseline_head="seed",
        current_head="",
        batch_summary_json=json.dumps({"batch_id": batch.batch_id, "tasks": 4}),
    )
    for i, (name, _, _) in FEATURES.items():
        batch.tasks.append(
            TaskStateRecord(
                index=i,
                title=f"Feature {i}: {name}",
                prompt=f"Implement {name}.",
                acceptance_criteria=[f"python tests/test_feature_{i}.py exits 0"],
                audit_focus=[f"{name} behaves as specified"],
                state=TaskState.APPROVED,
                attempts=1,
                audit_rounds=1,
                latest_verdict="PASS",
                builder_session_id=f"fake-builder-{i}",
                auditor_session_id="fake-auditor-shared",
            )
        )
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
    return batch.batch_id


def write_feature_files(repo: Path) -> None:
    tests = repo / "tests"
    tests.mkdir(exist_ok=True)
    for i, (name, impl, assertion) in FEATURES.items():
        (repo / f"{name}.py").write_text(impl, encoding="utf-8")
        (tests / f"test_feature_{i}.py").write_text(
            "import sys\nfrom pathlib import Path\n\n"
            f"sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n\n"
            f"from {name} import {name}\n\n"
            f"{assertion}\n"
            f"print('feature {i} OK')\n",
            encoding="utf-8",
        )
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "features + deterministic tests")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout-new", type=float, default=600.0)
    parser.add_argument("--timeout-final", type=float, default=1500.0)
    args = parser.parse_args()

    started = time.monotonic()
    scratch_root = Path(tempfile.mkdtemp(prefix="encomm-pcc-codex-smoke-"))
    repo = seed_scratch_repo(scratch_root)
    evidence_path = scratch_root / "evidence.json"
    evidence: dict = {"scratch_root": str(scratch_root), "repo": str(repo)}

    # The live proof IS this script: the evidence gates are flipped here, and
    # the commit that makes them permanent happens only after this run passes.
    import encomm_pcc.drivers.codex as codex_module

    codex_module._LIVE_SMOKE_VERIFIED = True
    codex_module._LIVE_RESUME_VERIFIED = True

    exe = CodexDriver.resolve_executable()
    print(f"[prep] codex executable: {exe}")
    print(f"[prep] scratch repo: {repo}")
    fingerprint_before = capture_repo_fingerprint(repo)
    evidence["fingerprint_before"] = fingerprint_before.to_json()

    db = Database(str(scratch_root / "pcc.db")).open()
    controller = PipelineController(database=db, event_log=NullEventLog())
    controller.set_workspace("Codex smoke", str(repo))

    runner = CountingRunner(raw_dir=scratch_root)

    # ------------------------------------------------------------------
    # REAL CALL 1 — new Codex session through the real driver
    # ------------------------------------------------------------------
    print("[call 1] new Codex session: dispatching the marker prompt …")
    registry = DriverRegistry()
    registry.register(CodexDriver)
    driver = registry.create("codex", runner=runner)
    request = SessionRequest(
        role=AgentRole.ORCHESTRATOR,
        workspace_path=str(repo),
        session_policy=SessionPolicy.PERSISTENT_OPTIONAL,
    )
    session = driver.start_session(request)
    assert session.session_id is None, "no id before the first prompt"
    handle = driver.send_prompt(session, PROMPT_NEW)
    result1 = driver.wait_for_completion(handle, timeout_s=args.timeout_new)

    evidence["call1"] = {
        "argv": (result1.metadata or {}).get("argv"),
        "exit_code": result1.exit_code,
        "duration_s": result1.duration_s,
        "ok": result1.ok,
        "text": (result1.text or "")[:2000],
        "stream": (result1.metadata or {}).get("stream"),
        "session_id": result1.session_id,
    }
    print(
        f"[call 1] ok={result1.ok} exit={result1.exit_code} "
        f"session={result1.session_id} duration={result1.duration_s:.1f}s"
    )
    if not result1.ok or MARKER_NEW not in (result1.text or "") or not result1.session_id:
        evidence_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(f"FAIL: call 1 did not verify — evidence at {evidence_path}")
        print(f"error: {result1.error}")
        return 1
    codex_session_id = result1.session_id

    # Discoverability proof: the new session must be found by the read-only
    # discovery path, workspace-matched to the scratch repo.
    discovery = driver.discover_sessions(workspace_path=str(repo), limit=50)
    found = [s for s in discovery.sessions if s.session_id == codex_session_id]
    evidence["call1_discovery"] = {
        "mechanism": discovery.mechanism,
        "found": bool(found),
        "matches_workspace": bool(found and found[0].matches_workspace),
        "total": len(discovery.sessions),
    }
    print(
        f"[call 1] discovery: found={bool(found)} "
        f"workspace_match={bool(found and found[0].matches_workspace)}"
    )
    if not found or not found[0].matches_workspace:
        evidence_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print("FAIL: the new session was not discoverable/workspace-matched")
        return 1

    # Binding proof through the same generic path the UI uses (zero AI calls).
    controller.set_role_config(AgentRole.ORCHESTRATOR, engine="codex", project_profile="")
    controller.bind_external_session(AgentRole.ORCHESTRATOR, "codex", codex_session_id)
    binding = controller.role_config(AgentRole.ORCHESTRATOR).external_session_binding()
    assert binding is not None and binding.external_session_id == codex_session_id

    # ------------------------------------------------------------------
    # REAL CALL 2 — resume + FINAL_AUDITOR through the generic pipeline
    # ------------------------------------------------------------------
    print("[call 2] seeding the completed scratch batch and dispatching the Final Auditor …")
    write_feature_files(repo)
    for i in FEATURES:
        proc = subprocess.run(
            [sys.executable, str(repo / "tests" / f"test_feature_{i}.py")],
            capture_output=True,
            text=True,
            cwd=str(repo),
        )
        assert proc.returncode == 0, f"seed test {i} failed: {proc.stderr}"
    pre_audit_fingerprint = capture_repo_fingerprint(repo)

    batch_id = seed_completed_batch(controller, repo)
    controller.set_role_config(
        AgentRole.FINAL_AUDITOR,
        engine="codex",
        project_profile="",  # Codex needs no Hermes profile
        same_as_orchestrator=False,
    )
    # Bind the EXACT session from CALL 1 through the generic UI path.
    controller.bind_external_session(AgentRole.FINAL_AUDITOR, "codex", codex_session_id)

    executor = Executor(
        controller,
        registry=registry,
        runner=runner,
        database=controller.database,
        event_log=NullEventLog(),
        profile_discovery=lambda **kwargs: None,
    )
    controller.attach_executor(executor)

    report = executor.run_final_audit(next_batch_size=4, timeout_s=args.timeout_final)
    result2 = report.prompt_result
    evidence["call2"] = {
        "outcome": report.outcome.value,
        "message": report.message,
        "session_id": report.session_id,
        "argv": (result2.metadata or {}).get("argv") if result2 else None,
        "exit_code": result2.exit_code if result2 else None,
        "duration_s": result2.duration_s if result2 else None,
        "executor_started": report.executor_started,
        "next_plan_id": report.next_plan_id,
        "output_excerpt": (report.output_excerpt or "")[:2000],
    }
    print(
        f"[call 2] outcome={report.outcome.value} session={report.session_id} "
        f"exit={result2.exit_code if result2 else '?'} "
        f"duration={result2.duration_s:.1f}s" if result2 else "[call 2] no result"
    )
    if report.outcome is not FinalAuditOutcome.PASSED or report.session_id != codex_session_id:
        evidence_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(f"FAIL: call 2 — evidence at {evidence_path}")
        return 1
    argv2 = (result2.metadata or {}).get("argv") or ""
    assert f"resume {codex_session_id}" in argv2 or codex_session_id in argv2, (
        "call 2 must have resumed the CALL 1 session"
    )

    # Post-conditions: phase, persistence, clean worktree, exactly 2 launches.
    phase_ok = controller.machine.phase is PipelinePhase.BATCH_COMPLETE
    stored = controller.database.load_final_audit(batch_id)
    pending = controller.database.load_open_pending_next_plan()
    fingerprint_after = capture_repo_fingerprint(repo)
    evidence["post"] = {
        "phase": controller.machine.phase.value,
        "phase_ok": phase_ok,
        "final_verdict": (stored or {}).get("final_verdict"),
        "pending_plan_id": (pending or {}).get("plan_id"),
        "worktree_unchanged": (
            pre_audit_fingerprint.status_hash == fingerprint_after.status_hash
            and pre_audit_fingerprint.head == fingerprint_after.head
        ),
        "fingerprint_after": fingerprint_after.to_json(),
        "pre_audit_fingerprint": pre_audit_fingerprint.to_json(),
        "real_model_operations": len(runner.launched),
    }
    print(
        f"[post] phase={controller.machine.phase.value} "
        f"verdict={(stored or {}).get('final_verdict')} "
        f"pending_plan={(pending or {}).get('plan_id')} "
        f"worktree_unchanged={evidence['post']['worktree_unchanged']} "
        f"model_operations={len(runner.launched)}"
    )

    db.close()
    evidence_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f"[done] evidence written: {evidence_path}")
    print(f"[done] wall clock: {time.monotonic() - started:.1f}s")
    ok = (
        phase_ok
        and evidence["post"]["final_verdict"] == "PASS"
        and evidence["post"]["pending_plan_id"]
        and evidence["post"]["worktree_unchanged"]
        and len(runner.launched) == 2
    )
    if not ok:
        print("FAIL: post-conditions not met — see evidence")
        return 1
    print("SMOKE PASSED (2 real Codex model operations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
