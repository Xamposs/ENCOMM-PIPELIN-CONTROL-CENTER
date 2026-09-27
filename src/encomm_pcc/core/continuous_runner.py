"""Continuous autonomous execution — batch after batch until STOP (Session 009).

The deterministic coordinator the Session 009 brief mandates (§26): it composes
the EXISTING proven components — :class:`~encomm_pcc.core.batch_runner.BatchRunner`,
``Executor.run_final_audit()`` and ``Executor.start_next_batch()`` — and owns
only the batch-to-batch loop.  It duplicates no component logic.

Token contract (§4, §5): after the initial plan, each subsequent batch is
materialised by ``start_next_batch()`` from the pending plan the PASSing Final
Audit already returned — **zero** additional Orchestrator planning calls.

Safety (§27, §28, §31, §32): STOP and PAUSE take effect at safe boundaries (an
in-flight prompt always finishes and persists first — the executor's own
boundary semantics); any non-PASS final audit, BLOCKED or FAILED batch, or a
missing pending plan stops the loop with an explicit reason.  No recursion, no
UI event loops: this is a plain deterministic while-loop over persisted state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import cycle guard for type checkers
    from .executor import Executor

from .batch_runner import BatchOutcome, BatchRunner

__all__ = ["ContinuousRunReport", "ContinuousRunner", "ContinuousStopReason"]


class ContinuousStopReason:
    """Why the continuous loop stopped (diagnostics/UI-facing strings)."""

    ALL_STOP = "STOP_REQUESTED"
    PAUSED = "PAUSED"
    BATCH_BLOCKED = "BATCH_BLOCKED"
    BATCH_FAILED = "BATCH_FAILED"
    FINAL_AUDIT_NOT_PASS = "FINAL_AUDIT_NOT_PASS"
    NO_PENDING_PLAN = "NO_PENDING_PLAN"
    NEXT_BATCH_REFUSED = "NEXT_BATCH_REFUSED"
    NEVER_STARTED = "NEVER_STARTED"


@dataclass(slots=True)
class _BatchCycle:
    """One completed batch cycle inside the continuous run."""

    outcome: str
    final_verdict: str
    planning_ai_calls: int


@dataclass(slots=True)
class ContinuousRunReport:
    """Terminal report of one continuous run (deterministic accounting)."""

    stop_reason: str
    message: str
    batches_run: int = 0
    final_audits: int = 0
    next_batch_handoffs: int = 0
    planning_ai_calls: int = 0
    cycles: list[_BatchCycle] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"continuous run: batches={self.batches_run} "
            f"final_audits={self.final_audits} "
            f"zero-AI handoffs={self.next_batch_handoffs} "
            f"planning_calls={self.planning_ai_calls} "
            f"stop={self.stop_reason}"
        )


class ContinuousRunner:
    """Runs batch after batch, autonomously, until a safe stop condition."""

    def __init__(self, executor: "Executor") -> None:
        self.executor = executor

    # -- loop control -------------------------------------------------------
    def request_stop(self) -> None:
        """Boundary-safe stop: the running operation finishes first (§27)."""
        self.executor.request_stop()

    def request_pause(self) -> None:
        """Boundary-safe pause: no automatic model call after the boundary (§28)."""
        self.executor.request_pause()

    # -- the loop -------------------------------------------------------------
    def run_continuous(
        self,
        *,
        project_brief: str = "",
        batch_size: int = 5,
        next_batch_size: int = 5,
        timeout_s: float | None = None,
    ) -> ContinuousRunReport:
        """Run batches autonomously until STOP/PAUSE/failure/blocked.

        One planning call happens only for the FIRST batch (§5).  Every later
        batch comes from ``start_next_batch()`` consuming the plan the previous
        PASSing Final Audit persisted — zero planning AI calls by construction.
        """
        runner = BatchRunner(self.executor)
        report = ContinuousRunReport(
            stop_reason=ContinuousStopReason.NEVER_STARTED, message=""
        )

        # ---- batch 1: the only planning call of the whole run -------------
        first = runner.run_batch(
            project_brief=project_brief,
            batch_size=batch_size,
            resume=False,
            timeout_s=timeout_s,
        )
        report.planning_ai_calls += 1
        return self._continue_loop(report, runner, first, next_batch_size, timeout_s)

    def resume_continuous(
        self,
        *,
        next_batch_size: int = 5,
        timeout_s: float | None = None,
    ) -> ContinuousRunReport:
        """Resume an in-flight batch from durable state, then continue (§26)."""
        runner = BatchRunner(self.executor)
        first = runner.run_batch(resume=True, timeout_s=timeout_s)
        report = ContinuousRunReport(
            stop_reason=ContinuousStopReason.NEVER_STARTED, message=""
        )
        # A resumed batch needed no NEW planning call: the plan predates it.
        return self._continue_loop(report, runner, first, next_batch_size, timeout_s)

    # -- internals --------------------------------------------------------------
    def _continue_loop(
        self,
        report: ContinuousRunReport,
        runner: BatchRunner,
        batch_report,
        next_batch_size: int,
        timeout_s: float | None,
    ) -> ContinuousRunReport:
        """Shared batch-to-batch loop (no recursion; a plain while)."""
        executor = self.executor
        while True:
            report.batches_run += 1
            report.cycles.append(
                _BatchCycle(
                    outcome=batch_report.outcome.value,
                    final_verdict="",
                    planning_ai_calls=1 if report.batches_run == 1 else 0,
                )
            )

            # A pause requested mid-batch is honoured at this boundary.
            if executor.pause_requested:
                report.stop_reason = ContinuousStopReason.PAUSED
                report.message = "Paused at a safe batch boundary."
                return report

            if batch_report.outcome is not BatchOutcome.READY_FOR_FINAL_AUDIT:
                # BLOCKED / FAILED / STOPPED / PAUSED batch: exit safely (§26).
                # A stop honoured MID-batch was already consumed by the batch
                # runner (its boundary path clears the executor flag), so the
                # STOPPED outcome itself is the deterministic stop signal.
                if (
                    executor.stop_requested
                    or batch_report.outcome is BatchOutcome.STOPPED
                ):
                    report.stop_reason = ContinuousStopReason.ALL_STOP
                    report.message = "Stopped safely after the current batch."
                elif batch_report.outcome is BatchOutcome.BLOCKED:
                    report.stop_reason = ContinuousStopReason.BATCH_BLOCKED
                    report.message = batch_report.message
                elif batch_report.outcome is BatchOutcome.FAILED:
                    report.stop_reason = ContinuousStopReason.BATCH_FAILED
                    report.message = batch_report.message
                else:
                    report.stop_reason = ContinuousStopReason.PAUSED
                    report.message = batch_report.message
                return report

            # ---- final audit: automatic in continuous mode (§29) ----------
            final = executor.run_final_audit(
                next_batch_size=next_batch_size, timeout_s=timeout_s
            )
            report.final_audits += 1
            verdict = ""
            try:
                verdict = str(getattr(final.result, "verdict", "") or "")
            except AttributeError:
                verdict = ""
            if report.cycles:
                report.cycles[-1].final_verdict = verdict

            if final.outcome.value != "PASSED":
                # §31: never hide/repair a failing final audit — stop loudly.
                report.stop_reason = ContinuousStopReason.FINAL_AUDIT_NOT_PASS
                report.message = final.message or (
                    f"Final audit returned {final.outcome.value}; operator handling required."
                )
                return report

            if executor.stop_requested:
                report.stop_reason = ContinuousStopReason.ALL_STOP
                report.message = (
                    "Final audit PASSED and persisted; stopping before the next batch."
                )
                return report

            # ---- zero-AI next-batch handoff (§30) --------------------------
            pending = self.executor.controller.database.load_open_pending_next_plan()
            if pending is None:
                report.stop_reason = ContinuousStopReason.NO_PENDING_PLAN
                report.message = (
                    "Final audit PASSED but no pending next plan was persisted — "
                    "stopping instead of planning again."
                )
                return report
            handed = executor.start_next_batch()
            if not getattr(handed, "ok", False):
                report.stop_reason = ContinuousStopReason.NEXT_BATCH_REFUSED
                report.message = getattr(handed, "message", "START NEXT BATCH refused.")
                return report
            report.next_batch_handoffs += 1

            # ---- run the materialised batch (no planning call) -------------
            batch_report = runner.run_batch(resume=True, timeout_s=timeout_s)
