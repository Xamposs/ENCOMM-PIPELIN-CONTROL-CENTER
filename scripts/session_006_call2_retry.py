"""Session 006 — §20 evidence-driven retry of REAL CALL 2 only.

CALL 1 (new Codex session) succeeded and is preserved.  CALL 2 failed before
any model work (0.2 s, exit 2): the first attempt passed ``-s``/``-C`` to
``codex exec resume``, which the installed CLI rejects (verified: stderr
"unexpected argument '-s' found").  The contract module was corrected from
that evidence; this script re-runs ONLY the final-audit operation against the
PRESERVED scratch repository and the SAME Codex session id.  No other real
Codex call is made.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

SCRATCH = Path(sys.argv[1]).expanduser().resolve()
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

spec = importlib.util.spec_from_file_location(
    "session_006_codex_smoke", ROOT / "scripts" / "session_006_codex_smoke.py"
)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)

import encomm_pcc.drivers.codex as codex_module  # noqa: E402

# The live proof for the fresh-session path already happened (CALL 1).  This
# retry proves the resume path; the gates are set here and persisted in the
# source only after the run passes.
codex_module._LIVE_SMOKE_VERIFIED = True
codex_module._LIVE_RESUME_VERIFIED = True

from encomm_pcc.core import PipelineController  # noqa: E402
from encomm_pcc.core.events import NullEventLog  # noqa: E402
from encomm_pcc.core.executor import Executor, FinalAuditOutcome  # noqa: E402
from encomm_pcc.core.repo_fingerprint import capture_repo_fingerprint  # noqa: E402
from encomm_pcc.domain import AgentRole, PipelinePhase  # noqa: E402
from encomm_pcc.drivers import CodexDriver, DriverRegistry  # noqa: E402
from encomm_pcc.persistence import Database  # noqa: E402

def main() -> int:
    started = time.monotonic()
    evidence = json.loads((SCRATCH / "evidence.json").read_text(encoding="utf-8"))
    codex_session_id = evidence["call1"]["session_id"]
    repo = Path(evidence["repo"])
    raw_dir = SCRATCH / "retry"
    raw_dir.mkdir(exist_ok=True)

    print(f"[retry] session: {codex_session_id}")
    print(f"[retry] repo:    {repo}")
    pre = capture_repo_fingerprint(repo)

    db = Database(str(SCRATCH / "pcc_retry.db")).open()
    controller = PipelineController(database=db, event_log=NullEventLog())
    controller.set_workspace("Codex smoke", str(repo))
    batch_id = smoke.seed_completed_batch(controller, repo)
    controller.set_role_config(
        AgentRole.FINAL_AUDITOR,
        engine="codex",
        project_profile="",
        same_as_orchestrator=False,
    )
    controller.bind_external_session(AgentRole.FINAL_AUDITOR, "codex", codex_session_id)

    runner = smoke.CountingRunner(raw_dir=raw_dir)
    registry = DriverRegistry()
    registry.register(CodexDriver)
    executor = Executor(
        controller,
        registry=registry,
        runner=runner,
        database=controller.database,
        event_log=NullEventLog(),
        profile_discovery=lambda **kwargs: None,
    )
    controller.attach_executor(executor)

    print("[retry] dispatching the Final Auditor (ONE real Codex resume call) …")
    report = executor.run_final_audit(next_batch_size=4, timeout_s=1500.0)
    result = report.prompt_result
    stored = controller.database.load_final_audit(batch_id)
    pending = controller.database.load_open_pending_next_plan()
    after = capture_repo_fingerprint(repo)

    retry_evidence = {
        "outcome": report.outcome.value,
        "message": report.message,
        "session_id": report.session_id,
        "expected_session_id": codex_session_id,
        "same_session": report.session_id == codex_session_id,
        "argv": (result.metadata or {}).get("argv") if result else None,
        "exit_code": result.exit_code if result else None,
        "duration_s": result.duration_s if result else None,
        "phase": controller.machine.phase.value,
        "final_verdict": (stored or {}).get("final_verdict"),
        "pending_plan_id": (pending or {}).get("plan_id"),
        "worktree_unchanged": pre.status_hash == after.status_hash and pre.head == after.head,
        "real_model_operations": len(runner.launched),
        "wall_clock_s": round(time.monotonic() - started, 1),
    }
    (raw_dir / "retry_evidence.json").write_text(
        json.dumps(retry_evidence, indent=2), encoding="utf-8"
    )
    print(json.dumps(retry_evidence, indent=2))
    db.close()

    ok = (
        report.outcome is FinalAuditOutcome.PASSED
        and retry_evidence["same_session"]
        and retry_evidence["final_verdict"] == "PASS"
        and retry_evidence["pending_plan_id"]
        and retry_evidence["worktree_unchanged"]
        and retry_evidence["real_model_operations"] == 1
        and controller.machine.phase is PipelinePhase.BATCH_COMPLETE
    )
    print("RETRY SMOKE PASSED" if ok else "RETRY FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
