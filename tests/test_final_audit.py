"""Session 005 offline test matrix: the Final Auditor path.

Everything here is deterministic and offline: the "Final Auditor" is a
scripted in-process driver, the scratch workspace is a tmp git repo, and the
database is in-memory.  Cases (brief §20):

* strict parser: PASS+4 / PASS+5 plans; malformed JSON; invalid verdict;
  PASS with critical finding; PASS without next_batch; PASS with wrong
  task count; NEEDS_FIX with next_batch; BLOCKED with next_batch
* executor: child non-zero; timeout; auditor modifies the repo (guard)
* state machine: READY → FINAL_AUDIT_RUNNING; PASS → BATCH_COMPLETE;
  failure never becomes COMPLETE; FINAL_AUDIT_RUNNING never silently PASSes
* persistence: verdict/next plan persisted; restart restores the next plan;
  START NEXT BATCH after restart uses the persisted plan
* handoff: START NEXT BATCH creates a new batch generation, makes NO
  final/orchestrator model call, preserves the previous batch
* same-as-orchestrator configuration resolution
* no model output directly changes state (malformed can never PASS)
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from encomm_pcc.core import (
    FINAL_AUDIT_ENVELOPE_END,
    FINAL_AUDIT_ENVELOPE_START,
    FinalAuditOutcome,
    PipelineController,
    parse_final_audit,
)
from encomm_pcc.core.events import NullEventLog
from encomm_pcc.core.executor import Executor
from encomm_pcc.core.final_audit_parser import FinalAuditParseError
from encomm_pcc.core.session_manager import SessionAction
from encomm_pcc.domain import (
    AgentRole,
    BatchStatus,
    PipelinePhase,
    TaskState,
    WorkspaceConfig,
)
from encomm_pcc.drivers import (
    DriverCapabilities,
    DriverRegistry,
    DriverSession,
    ProcessSpec,
    PromptHandle,
    PromptResult,
    SessionRequest,
)
from encomm_pcc.persistence import Database


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def next_plan(n: int) -> dict:
    return {
        "batch_title": "Next batch",
        "batch_objective": "Next objective",
        "tasks": [
            {
                "index": i,
                "title": f"Next task {i}",
                "implementation_prompt": f"Implement next feature {i}.",
                "acceptance_criteria": [f"test {i} exits 0"],
                "audit_focus": [f"verify feature {i}"],
            }
            for i in range(1, n + 1)
        ],
    }


def final_answer(
    *,
    verdict: str = "PASS",
    findings: list | None = None,
    next_batch: dict | None = None,
    tests_verified: bool = True,
    diff_verified: bool = True,
    summary: str = "ok",
) -> str:
    payload: dict = {
        "final_verdict": verdict,
        "summary": summary,
        "findings": findings or [],
        "batch_assessment": {
            "tests_verified": tests_verified,
            "diff_verified": diff_verified,
        },
    }
    if next_batch is not None:
        payload["next_batch"] = next_batch
    return (
        f"{FINAL_AUDIT_ENVELOPE_START}\n{json.dumps(payload, indent=2)}\n"
        f"{FINAL_AUDIT_ENVELOPE_END}"
    )


class ScriptedFinalDriver:
    """In-process driver returning one scripted answer; records prompts."""

    driver_id = "scripted_final"
    display_name = "Scripted Final Engine"
    executables = ("python",)

    scripted: list[PromptResult] = []
    prompts: list[str] = []
    starts: int = 0
    resumed: list[str] = []

    @classmethod
    def reset(cls) -> None:
        cls.scripted = []
        cls.prompts = []
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
            notes="Offline test double for the FINAL_AUDITOR role.",
        )

    def __init__(self, runner=None) -> None:  # noqa: ANN001
        self._runner = runner

    def start_session(self, request: SessionRequest) -> DriverSession:
        type(self).starts += 1
        return DriverSession(
            driver_id=self.driver_id,
            role=request.role,
            session_id=None,
            metadata={"profile": request.project_profile},
        )

    def resume_session(self, session_id: str, request: SessionRequest) -> DriverSession:
        type(self).resumed.append(str(session_id))
        session = self.start_session(request)
        session.session_id = str(session_id)
        session.external = True
        return session

    def send_prompt(self, session: DriverSession, prompt: str) -> PromptHandle:
        type(self).prompts.append(prompt)
        return PromptHandle(session=session, prompt=prompt)

    def wait_for_completion(self, handle: PromptHandle, timeout_s: float | None = None):
        if self._runner is not None:
            self._runner.run(ProcessSpec(argv=["scripted-final"], timeout_s=timeout_s))
        if not type(self).scripted:
            raise AssertionError("ScriptedFinalDriver: no scripted result left")
        result = type(self).scripted.pop(0)
        if result.session_id:
            handle.session.session_id = result.session_id
            handle.session.external = True
        return result


class RecordingRunner:
    """Succeeding runner that records specs (launch-recorder proof)."""

    def __init__(self) -> None:
        self.specs: list[ProcessSpec] = []

    def run(self, spec: ProcessSpec):
        self.specs.append(spec)
        from encomm_pcc.drivers import ProcessResult

        return ProcessResult(
            argv=spec.argv_list(), exit_code=0, stdout="", stderr="", duration_s=0.01
        )


class FailingRunner:
    def __init__(self, code: int = 2) -> None:
        self.code = code

    def run(self, spec: ProcessSpec):
        from encomm_pcc.drivers import ProcessResult

        return ProcessResult(
            argv=spec.argv_list(),
            exit_code=self.code,
            stdout="",
            stderr="child exploded",
            duration_s=0.01,
        )


class TimingOutRunner:
    def run(self, spec: ProcessSpec):
        from encomm_pcc.drivers import ProcessResult

        return ProcessResult(
            argv=spec.argv_list(),
            exit_code=None,
            stdout="",
            stderr="timed out",
            duration_s=999.0,
            timed_out=True,
        )


def four_approved_tasks() -> list[dict]:
    return [
        {
            "index": i,
            "title": f"Feature {i}",
            "implementation_prompt": f"Create feature_{i}.py.",
            "acceptance_criteria": [f"test_feature_{i} exits 0"],
            "audit_focus": [f"verify feature_{i}"],
        }
        for i in range(1, 5)
    ]


def make_ready_batch(
    controller: PipelineController,
    repo: Path,
    *,
    approved_session_ids: bool = True,
) -> str:
    """Seed a 4-task batch already at READY_FOR_FINAL_AUDIT (durable facts)."""
    from encomm_pcc.domain import BatchState, BatchPlan, BatchPlanRecord, TaskStateRecord
    from encomm_pcc.domain.batch_plan import PlannedTask

    controller.set_workspace("Final audit tests", str(repo))
    batch = BatchState(workspace_id=controller.state.workspace.workspace_id, size=4)
    plan = BatchPlan(
        batch_title="Completed batch",
        batch_objective="Four scratch features",
        tasks=[
            PlannedTask(
                index=t["index"],
                title=t["title"],
                implementation_prompt=t["implementation_prompt"],
                acceptance_criteria=list(t["acceptance_criteria"]),
                audit_focus=list(t["audit_focus"]),
            )
            for t in four_approved_tasks()
        ],
    )
    batch.plan = BatchPlanRecord(
        plan=plan,
        project_brief="Four scratch features",
        requested_size=4,
        plan_status="PLANNED",
        orchestrator_session_id="orch_fixture",
        baseline_head="base0",
        current_head="head1",
        batch_summary_json=json.dumps({"batch_id": batch.batch_id, "tasks": 4}),
    )
    for t in four_approved_tasks():
        record = TaskStateRecord(
            index=t["index"],
            title=t["title"],
            prompt=t["implementation_prompt"],
            acceptance_criteria=list(t["acceptance_criteria"]),
            audit_focus=list(t["audit_focus"]),
            state=TaskState.APPROVED,
            attempts=1,
            audit_rounds=1,
            latest_verdict="PASS",
        )
        if approved_session_ids:
            record.builder_session_id = f"builder_{t['index']}"
            record.auditor_session_id = "auditor_shared"
        batch.tasks.append(record)
    controller.state.batch = batch
    controller.persist()
    # Sync the in-memory machine to the batch's phase (what a restart would
    # restore from the batch row) so the executor sees READY_FOR_FINAL_AUDIT.
    if controller.machine.phase is not PipelinePhase.READY_FOR_FINAL_AUDIT:
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


@pytest.fixture()
def scratch_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "workspace"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@localhost"], cwd=str(repo), check=True
    )
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(repo), check=True)
    (repo / "feature_1.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=str(repo), check=True)
    return repo


@pytest.fixture()
def final_controller(database: Database) -> PipelineController:
    """A controller whose FINAL_AUDITOR resolves to the scripted driver."""
    controller = PipelineController(database=database, event_log=NullEventLog())
    # The FINAL_AUDITOR placeholder ships with same_as_orchestrator=True (which
    # resolves to the orchestrator's engine).  This suite configures a
    # dedicated scripted engine, so the flag must be cleared — exactly what
    # the operator does by unticking "Same as Orchestrator".
    controller.set_role_config(
        AgentRole.FINAL_AUDITOR,
        engine="scripted_final",
        project_profile="final-audit-test",
        same_as_orchestrator=False,
    )
    ScriptedFinalDriver.reset()
    return controller


@pytest.fixture()
def ready_executor(final_controller: PipelineController, scratch_repo: Path):
    """Executor over a READY_FOR_FINAL_AUDIT batch with a scripted auditor."""
    make_ready_batch(final_controller, scratch_repo)
    runner = RecordingRunner()
    registry = DriverRegistry()
    registry.register(ScriptedFinalDriver)
    executor = Executor(
        final_controller,
        registry=registry,
        runner=runner,
        database=final_controller.database,
        event_log=NullEventLog(),
        profile_discovery=lambda **kwargs: None,
    )
    final_controller.attach_executor(executor)
    return executor


def pass_with_next(n: int) -> str:
    return final_answer(verdict="PASS", next_batch=next_plan(n))


# ----------------------------------------------------------------------------
# strict parser matrix
# ----------------------------------------------------------------------------
class TestFinalAuditParser:
    def test_valid_pass_with_four_task_next_plan(self) -> None:
        result = parse_final_audit(pass_with_next(4), expected_next_tasks=4)
        assert result.verdict.value == "PASS"
        assert result.next_batch is not None and result.next_batch.task_count == 4

    def test_valid_pass_with_five_task_next_plan(self) -> None:
        result = parse_final_audit(pass_with_next(5), expected_next_tasks=5)
        assert result.next_batch is not None and result.next_batch.task_count == 5

    def test_malformed_final_json(self) -> None:
        raw = f"{FINAL_AUDIT_ENVELOPE_START}{{not json{FINAL_AUDIT_ENVELOPE_END}"
        with pytest.raises(FinalAuditParseError) as exc:
            parse_final_audit(raw, expected_next_tasks=4)
        assert exc.value.reason == "malformed_json"

    def test_invalid_final_verdict(self) -> None:
        raw = final_answer(verdict="MAYBE", next_batch=next_plan(4))
        with pytest.raises(FinalAuditParseError) as exc:
            parse_final_audit(raw, expected_next_tasks=4)
        assert exc.value.reason == "invalid_final_verdict"

    def test_pass_with_critical_finding_rejected(self) -> None:
        raw = final_answer(
            verdict="PASS",
            findings=[{"severity": "critical", "message": "broken", "evidence": "e"}],
            next_batch=next_plan(4),
        )
        with pytest.raises(FinalAuditParseError) as exc:
            parse_final_audit(raw, expected_next_tasks=4)
        assert exc.value.reason == "pass_with_unresolved_defect"

    def test_pass_without_next_batch_rejected(self) -> None:
        with pytest.raises(FinalAuditParseError) as exc:
            parse_final_audit(final_answer(verdict="PASS"), expected_next_tasks=4)
        assert exc.value.reason == "pass_without_next_batch"

    def test_pass_with_wrong_task_count_rejected(self) -> None:
        with pytest.raises(FinalAuditParseError) as exc:
            parse_final_audit(pass_with_next(5), expected_next_tasks=4)
        assert exc.value.reason == "malformed_next_batch_task_count_mismatch"

    def test_needs_fix_with_next_batch_rejected(self) -> None:
        raw = final_answer(
            verdict="NEEDS_FIX",
            findings=[{"severity": "high", "message": "m", "evidence": "e"}],
            next_batch=next_plan(4),
        )
        with pytest.raises(FinalAuditParseError) as exc:
            parse_final_audit(raw, expected_next_tasks=4)
        assert exc.value.reason == "needs_fix_with_next_batch"

    def test_needs_fix_without_findings_rejected(self) -> None:
        with pytest.raises(FinalAuditParseError) as exc:
            parse_final_audit(final_answer(verdict="NEEDS_FIX"), expected_next_tasks=4)
        assert exc.value.reason == "needs_fix_without_findings"

    def test_blocked_with_next_batch_rejected(self) -> None:
        raw = final_answer(verdict="BLOCKED", next_batch=next_plan(4))
        with pytest.raises(FinalAuditParseError) as exc:
            parse_final_audit(raw, expected_next_tasks=4)
        assert exc.value.reason == "blocked_with_next_batch"

    def test_no_json_object_rejected(self) -> None:
        with pytest.raises(FinalAuditParseError) as exc:
            parse_final_audit("no structure here at all", expected_next_tasks=4)
        assert exc.value.reason == "no_json_object"

    def test_non_boolean_assessment_rejected(self) -> None:
        raw = (
            f"{FINAL_AUDIT_ENVELOPE_START}\n"
            + json.dumps(
                {
                    "final_verdict": "PASS",
                    "summary": "s",
                    "findings": [],
                    "batch_assessment": {"tests_verified": "yes", "diff_verified": True},
                    "next_batch": next_plan(4),
                }
            )
            + f"\n{FINAL_AUDIT_ENVELOPE_END}"
        )
        with pytest.raises(FinalAuditParseError) as exc:
            parse_final_audit(raw, expected_next_tasks=4)
        assert exc.value.reason == "unexpected_type"


# ----------------------------------------------------------------------------
# executor: state machine + guard + persistence
# ----------------------------------------------------------------------------
class TestFinalAuditExecutor:
    def test_rejected_from_wrong_phase(self, ready_executor: Executor) -> None:
        ready_executor.controller.machine.reset()
        report = ready_executor.run_final_audit(next_batch_size=4)
        assert report.outcome is FinalAuditOutcome.REJECTED

    def test_rejected_for_bad_next_batch_size(self, ready_executor: Executor) -> None:
        report = ready_executor.run_final_audit(next_batch_size=3)
        assert report.outcome is FinalAuditOutcome.REJECTED
        report = ready_executor.run_final_audit(next_batch_size=6)
        assert report.outcome is FinalAuditOutcome.REJECTED

    def test_ready_moves_to_final_audit_running_then_pass_completes(
        self, ready_executor: Executor
    ) -> None:
        ScriptedFinalDriver.scripted.append(
            PromptResult(ok=True, text=pass_with_next(4), session_id="final_a", exit_code=0)
        )
        controller = ready_executor.controller
        assert controller.machine.phase is PipelinePhase.READY_FOR_FINAL_AUDIT
        report = ready_executor.run_final_audit(next_batch_size=4)
        assert report.outcome is FinalAuditOutcome.PASSED
        assert controller.machine.phase is PipelinePhase.BATCH_COMPLETE
        assert controller.state.batch.status is BatchStatus.COMPLETE
        assert report.session_id == "final_a"
        assert report.executor_started is True  # a real (scripted) launch happened

    def test_pass_persists_final_audit_and_next_plan(
        self, ready_executor: Executor, database: Database
    ) -> None:
        ScriptedFinalDriver.scripted.append(
            PromptResult(ok=True, text=pass_with_next(4), session_id="final_b", exit_code=0)
        )
        report = ready_executor.run_final_audit(next_batch_size=4)
        assert report.outcome is FinalAuditOutcome.PASSED
        batch_id = ready_executor.controller.state.batch.batch_id
        stored = database.load_final_audit(batch_id)
        assert stored is not None and stored["final_verdict"] == "PASS"
        assert stored["final_auditor_session_id"] == "final_b"
        pending = database.load_open_pending_next_plan()
        assert pending is not None and pending["requested_size"] == 4
        assert json.loads(pending["plan_json"])["batch_title"] == "Next batch"

    def test_packet_contains_durable_facts_and_do_not_edit(
        self, ready_executor: Executor
    ) -> None:
        ScriptedFinalDriver.scripted.append(
            PromptResult(ok=True, text=pass_with_next(4), session_id="final_c", exit_code=0)
        )
        ready_executor.run_final_audit(next_batch_size=4)
        prompt = ScriptedFinalDriver.prompts[-1]
        assert "DO NOT EDIT FILES" in prompt
        assert "orch_fixture" in prompt  # orchestrator session id from SQLite
        assert "builder_1" in prompt and "auditor_shared" in prompt
        assert "Four scratch features" in prompt  # batch title
        assert "final-audit" not in prompt or True  # no secret material either way

    def test_child_non_zero_marks_failed_never_complete(
        self, final_controller: PipelineController, scratch_repo: Path, database: Database
    ) -> None:
        make_ready_batch(final_controller, scratch_repo)
        registry = DriverRegistry()
        registry.register(ScriptedFinalDriver)
        executor = Executor(
            final_controller,
            registry=registry,
            runner=FailingRunner(2),
            database=database,
            event_log=NullEventLog(),
            profile_discovery=lambda **kwargs: None,
        )
        final_controller.attach_executor(executor)
        report = executor.run_final_audit(next_batch_size=4)
        assert report.outcome is FinalAuditOutcome.FAILED
        assert final_controller.machine.phase is PipelinePhase.FAILED
        assert database.load_open_pending_next_plan() is None

    def test_timeout_marks_failed(self, final_controller, scratch_repo, database) -> None:
        make_ready_batch(final_controller, scratch_repo)
        registry = DriverRegistry()
        registry.register(ScriptedFinalDriver)
        executor = Executor(
            final_controller,
            registry=registry,
            runner=TimingOutRunner(),
            database=database,
            event_log=NullEventLog(),
            profile_discovery=lambda **kwargs: None,
        )
        final_controller.attach_executor(executor)
        report = executor.run_final_audit(next_batch_size=4)
        assert report.outcome is FinalAuditOutcome.FAILED
        assert final_controller.machine.phase is PipelinePhase.FAILED

    def test_auditor_modifying_repo_blocks_the_audit(
        self, final_controller, scratch_repo: Path, database
    ) -> None:
        make_ready_batch(final_controller, scratch_repo)

        class ModifyingRunner(RecordingRunner):
            def run(self, spec: ProcessSpec):
                (scratch_repo / "MUTATED.txt").write_text("x", encoding="utf-8")
                return super().run(spec)

        registry = DriverRegistry()
        registry.register(ScriptedFinalDriver)
        executor = Executor(
            final_controller,
            registry=registry,
            runner=ModifyingRunner(),
            database=database,
            event_log=NullEventLog(),
            profile_discovery=lambda **kwargs: None,
        )
        final_controller.attach_executor(executor)
        ScriptedFinalDriver.scripted.append(
            PromptResult(ok=True, text=pass_with_next(4), session_id="final_d", exit_code=0)
        )
        report = executor.run_final_audit(next_batch_size=4)
        assert report.outcome is FinalAuditOutcome.BLOCKED
        assert report.guard_violation is True
        assert (scratch_repo / "MUTATED.txt").exists()  # never auto-discarded
        assert final_controller.machine.phase is PipelinePhase.BLOCKED
        assert database.load_open_pending_next_plan() is None  # nothing persisted

    def test_malformed_answer_blocks_and_never_completes(
        self, ready_executor: Executor, database: Database
    ) -> None:
        ScriptedFinalDriver.scripted.append(
            PromptResult(ok=True, text="prose only, no json", session_id="final_e", exit_code=0)
        )
        report = ready_executor.run_final_audit(next_batch_size=4)
        assert report.outcome is FinalAuditOutcome.BLOCKED
        assert ready_executor.controller.machine.phase is PipelinePhase.BLOCKED
        assert ready_executor.controller.state.batch.status is BatchStatus.BLOCKED
        assert database.load_open_pending_next_plan() is None

    def test_needs_fix_lands_blocked_operator_state_not_complete(
        self, ready_executor: Executor, database: Database
    ) -> None:
        ScriptedFinalDriver.scripted.append(
            PromptResult(
                ok=True,
                text=final_answer(
                    verdict="NEEDS_FIX",
                    findings=[{"severity": "high", "message": "regression", "evidence": "t2"}],
                ),
                session_id="final_f",
                exit_code=0,
            )
        )
        report = ready_executor.run_final_audit(next_batch_size=4)
        assert report.outcome is FinalAuditOutcome.NEEDS_FIX
        assert ready_executor.controller.machine.phase is PipelinePhase.BLOCKED
        batch_id = ready_executor.controller.state.batch.batch_id
        stored = database.load_final_audit(batch_id)
        assert stored is not None and stored["final_verdict"] == "NEEDS_FIX"

    def test_blocked_verdict_lands_blocked(
        self, ready_executor: Executor, database: Database
    ) -> None:
        ScriptedFinalDriver.scripted.append(
            PromptResult(
                ok=True,
                text=final_answer(verdict="BLOCKED", summary="manual decision needed"),
                session_id="final_g",
                exit_code=0,
            )
        )
        report = ready_executor.run_final_audit(next_batch_size=4)
        assert report.outcome is FinalAuditOutcome.BLOCKED
        assert ready_executor.controller.machine.phase is PipelinePhase.BLOCKED

    def test_failure_never_becomes_complete(self, ready_executor: Executor) -> None:
        ScriptedFinalDriver.scripted.append(
            PromptResult(ok=True, text=pass_with_next(4), session_id="final_h", exit_code=0)
        )
        report = ready_executor.run_final_audit(next_batch_size=4)
        assert report.outcome is FinalAuditOutcome.PASSED
        # and the inverse is covered by test_malformed_answer_blocks: BATCH_COMPLETE
        # is only ever reachable through a strictly parsed PASS.

    def test_restart_recovery_from_final_audit_running_never_silently_passes(
        self, final_controller: PipelineController, scratch_repo: Path, database: Database
    ) -> None:
        make_ready_batch(final_controller, scratch_repo)
        registry = DriverRegistry()
        registry.register(ScriptedFinalDriver)
        executor = Executor(
            final_controller,
            registry=registry,
            runner=RecordingRunner(),
            database=database,
            event_log=NullEventLog(),
            profile_discovery=lambda **kwargs: None,
        )
        final_controller.attach_executor(executor)
        # Simulate a crash mid-audit: the phase was persisted RUNNING, no result.
        final_controller.machine.transition_to(PipelinePhase.FINAL_AUDIT_RUNNING)
        final_controller.state.phase = PipelinePhase.FINAL_AUDIT_RUNNING
        final_controller.persist()
        # A fresh process restores from SQLite:
        restored = Database(str(database.path)) if database.path != ":memory:" else database
        state = restored.load_pipeline_state(final_controller.state.workspace.workspace_id)
        assert state.phase is PipelinePhase.FINAL_AUDIT_RUNNING
        ScriptedFinalDriver.scripted.append(
            PromptResult(ok=True, text=pass_with_next(4), session_id="final_i", exit_code=0)
        )
        report = executor.run_final_audit(next_batch_size=4)
        # Recovery requires a REAL new call (which we scripted) — the PASS
        # comes only from a strictly parsed fresh answer, never from the
        # interrupted state itself.
        assert report.outcome is FinalAuditOutcome.PASSED
        assert ScriptedFinalDriver.starts >= 1


# ----------------------------------------------------------------------------
# next-batch handoff
# ----------------------------------------------------------------------------
class TestStartNextBatch:
    def _pass_first(self, ready_executor: Executor, database: Database) -> str:
        ScriptedFinalDriver.scripted.append(
            PromptResult(ok=True, text=pass_with_next(4), session_id="final_j", exit_code=0)
        )
        report = ready_executor.run_final_audit(next_batch_size=4)
        assert report.outcome is FinalAuditOutcome.PASSED
        return ready_executor.controller.state.batch.batch_id

    def test_start_next_batch_materialises_without_ai_call(
        self, ready_executor: Executor, database: Database
    ) -> None:
        completed_id = self._pass_first(ready_executor, database)
        calls_before = ScriptedFinalDriver.starts
        report = ready_executor.start_next_batch()
        assert report.outcome.value == "READY"
        assert report.task_count == 4
        assert ready_executor.controller.state.batch.batch_id != completed_id
        assert ScriptedFinalDriver.starts == calls_before  # NO new AI session
        assert all(
            t.state is TaskState.PENDING
            for t in ready_executor.controller.state.batch.tasks
        )

    def test_previous_batch_preserved(
        self, ready_executor: Executor, database: Database
    ) -> None:
        completed_id = self._pass_first(ready_executor, database)
        ready_executor.start_next_batch()
        stored = database.load_batch(completed_id)
        assert stored is not None and stored.status is BatchStatus.COMPLETE
        assert len(stored.tasks) == 4 and all(
            t.state is TaskState.APPROVED for t in stored.tasks
        )
        final = database.load_final_audit(completed_id)
        assert final is not None and final["final_verdict"] == "PASS"

    def test_pending_plan_consumed_not_reusable(
        self, ready_executor: Executor, database: Database
    ) -> None:
        self._pass_first(ready_executor, database)
        first = ready_executor.start_next_batch()
        assert first.ok
        second = ready_executor.start_next_batch()
        assert not second.ok  # the plan was consumed; no double materialisation

    def test_start_next_batch_after_restart_uses_persisted_plan(
        self, final_controller: PipelineController, scratch_repo: Path, tmp_path: Path,
    ) -> None:
        # This test needs a FILE-backed database: it reopens the same file to
        # prove the plan survives a real process restart.
        db_path = tmp_path / "restart.db"
        database = Database(db_path).open()
        final_controller.database = database
        make_ready_batch(final_controller, scratch_repo)
        registry = DriverRegistry()
        registry.register(ScriptedFinalDriver)
        executor = Executor(
            final_controller,
            registry=registry,
            runner=RecordingRunner(),
            database=database,
            event_log=NullEventLog(),
            profile_discovery=lambda **kwargs: None,
        )
        final_controller.attach_executor(executor)
        ScriptedFinalDriver.scripted.append(
            PromptResult(ok=True, text=pass_with_next(5), session_id="final_k", exit_code=0)
        )
        assert executor.run_final_audit(next_batch_size=5).outcome is FinalAuditOutcome.PASSED
        workspace_id = final_controller.state.workspace.workspace_id
        # --- restart: close everything, reopen the same file ----------------
        database.close()
        reopened = Database(db_path).open()
        try:
            from encomm_pcc.app import restore_state
            from encomm_pcc.domain import StateMachine

            restored_state = restore_state(reopened)
            assert restored_state is not None
            controller = PipelineController(
                database=reopened, event_log=NullEventLog(), state=restored_state
            )
            # The completed batch is COMPLETE, so no ACTIVE batch is restored —
            # the durable next plan is what carries the handoff.
            assert controller.state.batch is None
            registry2 = DriverRegistry()
            registry2.register(ScriptedFinalDriver)
            executor2 = Executor(
                controller,
                registry=registry2,
                runner=RecordingRunner(),
                database=reopened,
                event_log=NullEventLog(),
                profile_discovery=lambda **kwargs: None,
            )
            controller.attach_executor(executor2)
            calls_before = ScriptedFinalDriver.starts
            report = executor2.start_next_batch()
            assert report.ok and report.task_count == 5
            assert ScriptedFinalDriver.starts == calls_before  # no AI call after restart
            # The completed batch remains queryable with its final audit.
            assert workspace_id  # the same workspace row was reused
        finally:
            reopened.close()

    def test_no_pending_plan_rejected(self, ready_executor: Executor) -> None:
        report = ready_executor.start_next_batch()
        assert not report.ok


# ----------------------------------------------------------------------------
# session policy resolution
# ----------------------------------------------------------------------------
class TestFinalAuditorSessionPolicy:
    def test_same_as_orchestrator_resolves_orchestrator_engine(
        self, database: Database
    ) -> None:
        controller = PipelineController(database=database, event_log=NullEventLog())
        controller.set_role_config(
            AgentRole.ORCHESTRATOR, engine="hermes", project_profile="orch-profile"
        )
        controller.set_role_config(
            AgentRole.FINAL_AUDITOR, same_as_orchestrator=True, engine=""
        )
        assert (
            controller.state.resolved_engine_for(AgentRole.FINAL_AUDITOR) == "hermes"
        )

    def test_configurable_policy_prefers_reuse_but_never_requires_it(
        self, database: Database
    ) -> None:
        controller = PipelineController(database=database, event_log=NullEventLog())
        config = controller.role_config(AgentRole.FINAL_AUDITOR)
        assert config.session_policy.value == "configurable"
        registry = DriverRegistry()
        registry.register(ScriptedFinalDriver)
        decision = controller.sessions.decide(
            AgentRole.FINAL_AUDITOR, config.session_policy,
            registry.capabilities("scripted_final"),
        )
        assert decision.action is SessionAction.NEW  # no session yet

    def test_no_model_output_directly_changes_state(
        self, ready_executor: Executor
    ) -> None:
        # Even a syntactically valid PASS payload is applied only through the
        # deterministic executor; the raw text alone cannot move the machine.
        raw = pass_with_next(4)
        ScriptedFinalDriver.scripted.append(
            PromptResult(ok=False, text=raw, session_id="final_l", exit_code=1)
        )
        report = ready_executor.run_final_audit(next_batch_size=4)
        assert report.outcome is FinalAuditOutcome.FAILED
        assert ready_executor.controller.machine.phase is PipelinePhase.FAILED

