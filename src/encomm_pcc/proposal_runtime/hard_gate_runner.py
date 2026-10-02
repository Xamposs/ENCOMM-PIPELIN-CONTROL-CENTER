"""The HARD_GATE_VALIDATION executor — the FIRST legitimate COMPLETE path.

``run_hard_gates()`` (brief §19) runs the deterministic hard-gate engine
over the CURRENT master proposal:

0. state guard: the proposal state machine must sit EXACTLY at
   ``HARD_GATE_VALIDATION`` (compose after ``run_iteration()``/integration);
1. REVIEW-FRESHNESS PRECONDITION (§18, D-063 reuse — never duplicated): the
   current master hash must equal the latest review bundle's hash; a stale
   proposal never runs gates and can never COMPLETE — the machine walks
   ``HARD_GATE_VALIDATION → REVISION_REQUIRED``;
2. evidence binding (§4): ``05_CONTROL/HARD_GATE_EVIDENCE.json`` must exist,
   parse, carry the evidence schema and be bound to THIS iteration and THIS
   exact proposal hash (missing/invalid → BLOCKED, never PASS);
3. deterministic evaluation of ALL 14 canonical gates in canonical order
   (no AI call, no network, no provider configuration);
4. outcome (§20–§22):
   * COMPLETE            — every gate PASS or explicitly justified N/A;
   * REVISION_REQUIRED   — ≥1 real PROPOSAL_ISSUE (+ HARD_GATE_FEEDBACK.json);
   * BLOCKED             — no proposal issue but missing/invalid evidence;
   * STALE_REVIEW        — the freshness precondition failed (machine at
                           REVISION_REQUIRED, no gates ever ran);
   * ARTIFACT_CONFLICT   — the durable per-iteration record conflicts.

Durable artifacts (§23): ``04_REVIEWS/iteration_NNN/hard_gates.json``
(immutable audit record, conflict-fail-closed) and
``05_CONTROL/HARD_GATES.json`` (latest-state snapshot) are written for every
evaluated run; ``05_CONTROL/HARD_GATE_FEEDBACK.json`` additionally for
REVISION_REQUIRED.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from ..proposal.enums import ProposalHardGateStatus, ProposalPhase
from ..proposal.fingerprint import proposal_fingerprint
from ..proposal.hard_gates import (
    EVIDENCE_DOCUMENT_FILENAME,
    HardGateDisposition,
    HardGateEvidenceDocument,
    HardGateEvidenceError,
    HardGateFailureClass,
    evaluate_hard_gates,
    load_hard_gate_evidence,
)
from ..proposal.state_machine import ProposalStateMachine
from ..proposal.workspace import MASTER_PROPOSAL_RELPATH
from .hard_gate_artifacts import (
    HardGateArtifactConflictError,
    build_feedback_payload,
    build_run_artifact,
    write_hard_gate_feedback,
    write_iteration_hard_gates,
    update_latest_hard_gates_snapshot,
)
from .review_freshness import ReviewFreshnessError, evaluate_review_freshness

__all__ = [
    "ProposalHardGateRunOutcome",
    "ProposalHardGateRunReport",
    "run_hard_gates",
]


class ProposalHardGateRunOutcome(str, Enum):
    """Terminal outcome of ONE hard-gate run (§19)."""

    COMPLETE = "COMPLETE"
    REVISION_REQUIRED = "REVISION_REQUIRED"
    BLOCKED = "BLOCKED"
    STALE_REVIEW = "STALE_REVIEW"
    ARTIFACT_CONFLICT = "ARTIFACT_CONFLICT"
    #: A WARN remains (e.g. an estimated page measurement): the run may not
    #: COMPLETE; the machine legitimately STAYS at HARD_GATE_VALIDATION so
    #: the operator can supply authoritative evidence and re-run.
    INCOMPLETE = "INCOMPLETE"
    RUN_FAILED = "RUN_FAILED"  # fingerprint/state operational failure

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


@dataclass(slots=True)
class ProposalHardGateRunReport:
    """JSON-friendly operational report of ONE hard-gate run."""

    outcome: ProposalHardGateRunOutcome
    iteration_number: int
    proposal_hash: str = ""
    state_before: ProposalPhase = ProposalPhase.IDLE
    state_after: ProposalPhase = ProposalPhase.IDLE
    disposition: str = ""
    gate_results: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    durable_artifact_path: str = ""
    snapshot_path: str = ""
    feedback_path: str = ""
    error: str = ""
    duration_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "iteration_number": self.iteration_number,
            "proposal_hash": self.proposal_hash,
            "state_before": self.state_before.value,
            "state_after": self.state_after.value,
            "disposition": self.disposition,
            "gate_results": list(self.gate_results),
            "counts": dict(self.counts),
            "durable_artifact_path": self.durable_artifact_path,
            "snapshot_path": self.snapshot_path,
            "feedback_path": self.feedback_path,
            "error": self.error,
            "duration_s": round(self.duration_s, 6),
        }


def _finish(
    report: ProposalHardGateRunReport,
    state_machine: ProposalStateMachine,
    started: float,
) -> ProposalHardGateRunReport:
    report.state_after = state_machine.phase
    report.duration_s = time.monotonic() - started
    return report


def run_hard_gates(
    *,
    workspace: Path,
    state_machine: ProposalStateMachine,
    iteration_number: int,
) -> ProposalHardGateRunReport:
    """Run ALL canonical hard gates — deterministic, zero-AI, fail closed.

    One public call, bounded work, no loop.  Never claims compliance: every
    gate result traces to real evidence checked against the exact current
    proposal revision.
    """
    started = time.monotonic()
    workspace = Path(workspace)
    master_path = workspace / MASTER_PROPOSAL_RELPATH
    state_before = state_machine.phase
    report = ProposalHardGateRunReport(
        outcome=ProposalHardGateRunOutcome.RUN_FAILED,
        iteration_number=int(iteration_number),
        state_before=state_before,
    )

    # -- 0. state guard (§19) ---------------------------------------------
    if state_before is not ProposalPhase.HARD_GATE_VALIDATION:
        report.error = (
            "run_hard_gates requires the state machine to be exactly at "
            f"HARD_GATE_VALIDATION; the machine is at {state_before.value}."
        )
        return _finish(report, state_machine, started)

    # -- 1. review-freshness precondition (§18, D-063) ---------------------
    try:
        current_hash = proposal_fingerprint(master_path)
    except OSError as exc:
        report.error = f"master proposal fingerprint failed: {exc}"
        return _finish(report, state_machine, started)
    report.proposal_hash = current_hash
    try:
        freshness = evaluate_review_freshness(
            workspace=workspace, current_proposal_hash=current_hash
        )
    except ReviewFreshnessError as exc:
        # Fail closed: no review evidence / unreadable bundle — never fresh.
        report.error = str(exc)
        return _finish(report, state_machine, started)
    if not freshness.is_current:
        # §18: a stale proposal NEVER runs gates and can never COMPLETE.
        state_machine.transition_to(ProposalPhase.REVISION_REQUIRED)
        report.outcome = ProposalHardGateRunOutcome.STALE_REVIEW
        report.error = (
            f"the current proposal hash {current_hash[:12]}… differs from "
            f"the latest reviewed hash {freshness.latest_reviewed_hash[:12]}… "
            "(iteration "
            f"{freshness.latest_iteration}); gates were NOT evaluated."
        )
        return _finish(report, state_machine, started)

    # -- 2. evidence binding (§4) ------------------------------------------
    try:
        evidence: HardGateEvidenceDocument = load_hard_gate_evidence(
            workspace / "05_CONTROL" / EVIDENCE_DOCUMENT_FILENAME,
            expected_iteration_number=int(iteration_number),
            expected_proposal_hash=current_hash,
        )
    except HardGateEvidenceError as exc:
        # Missing/corrupt/stale evidence is a BLOCKED-class failure (§22):
        # the operator/evidence input is missing, not the proposal failing.
        state_machine.transition_to(ProposalPhase.BLOCKED)
        report.outcome = ProposalHardGateRunOutcome.BLOCKED
        report.error = str(exc)
        return _finish(report, state_machine, started)

    # -- 3. deterministic evaluation of ALL 14 gates ------------------------
    try:
        master_text = master_path.read_text(encoding="utf-8")
    except OSError as exc:
        report.error = f"master proposal could not be read: {exc}"
        return _finish(report, state_machine, started)
    try:
        run_result = evaluate_hard_gates(
            evidence=evidence,
            master_proposal_text=master_text,
            workspace=workspace,
        )
    except Exception as exc:  # engine invariant breach — never an input issue
        report.error = f"hard-gate engine failure: {exc}"
        return _finish(report, state_machine, started)

    report.disposition = run_result.disposition.value
    report.gate_results = [
        {
            "gate_id": e.gate_id,
            "status": e.status.value,
            "message": e.message,
            "evidence": e.evidence,
            "failure_class": e.failure_class.value,
        }
        for e in run_result.evaluations
    ]
    report.counts = run_result.counts

    # -- 4. durable artifacts (§23) -----------------------------------------
    evidence_path = workspace / "05_CONTROL" / EVIDENCE_DOCUMENT_FILENAME
    try:
        artifact = build_run_artifact(
            run_result=run_result,
            evidence_document_path=evidence_path,
        )
        durable_path = write_iteration_hard_gates(
            workspace=workspace,
            iteration_number=int(iteration_number),
            artifact=artifact,
        )
        snapshot_path = update_latest_hard_gates_snapshot(
            workspace=workspace, artifact=artifact
        )
    except (ValueError, OSError) as exc:
        # D-060 convention: a durable-artifact conflict leaves the machine
        # position untouched (the run never happened durably) and reports
        # the conflict loudly — never a clobber, never a fake advance.
        report.outcome = ProposalHardGateRunOutcome.ARTIFACT_CONFLICT
        report.error = f"durable hard-gate artifact failed: {exc}"
        return _finish(report, state_machine, started)
    except HardGateArtifactConflictError as exc:
        # Same convention, typed conflict from the artifact writer.
        report.outcome = ProposalHardGateRunOutcome.ARTIFACT_CONFLICT
        report.error = str(exc)
        return _finish(report, state_machine, started)
    report.durable_artifact_path = str(durable_path)
    report.snapshot_path = str(snapshot_path)

    # -- 5. disposition → outcome + state (§20/§21/§22) ---------------------
    if run_result.disposition is HardGateDisposition.COMPLETE:
        # Every canonical gate resolved PASS or explicitly justified N/A:
        # the FIRST legitimate COMPLETE.
        state_machine.transition_to(ProposalPhase.COMPLETE)
        report.outcome = ProposalHardGateRunOutcome.COMPLETE
        return _finish(report, state_machine, started)

    if run_result.disposition is HardGateDisposition.REVISION_REQUIRED:
        feedback_payload = build_feedback_payload(run_result=run_result)
        try:
            feedback_path = write_hard_gate_feedback(
                workspace=workspace, payload=feedback_payload
            )
        except OSError as exc:
            state_machine.transition_to(ProposalPhase.BLOCKED)
            report.outcome = ProposalHardGateRunOutcome.ARTIFACT_CONFLICT
            report.error = f"the gates failed but the feedback write failed: {exc}"
            return _finish(report, state_machine, started)
        report.feedback_path = str(feedback_path)
        state_machine.transition_to(ProposalPhase.REVISION_REQUIRED)
        report.outcome = ProposalHardGateRunOutcome.REVISION_REQUIRED
        return _finish(report, state_machine, started)

    if run_result.disposition is HardGateDisposition.BLOCKED:
        state_machine.transition_to(ProposalPhase.BLOCKED)
        report.outcome = ProposalHardGateRunOutcome.BLOCKED
        report.error = (
            "no proposal-content failure exists, but at least one gate "
            "could not be evaluated (missing/invalid evidence); the "
            "operator must supply the evidence — the proposal itself did "
            "not fail."
        )
        return _finish(report, state_machine, started)

    # INCOMPLETE (a WARN remains): the machine legitimately stays at
    # HARD_GATE_VALIDATION — no feedback (not a proposal issue), no BLOCKED
    # (nothing is missing); the operator supplies authoritative evidence
    # (e.g. an exact page measurement) and re-runs from the same phase.
    report.outcome = ProposalHardGateRunOutcome.INCOMPLETE
    report.error = (
        "the run is INCOMPLETE: a WARN remains (e.g. an estimated page "
        "measurement); WARN never permits COMPLETE. Supply authoritative "
        "evidence and re-run from HARD_GATE_VALIDATION."
    )
    return _finish(report, state_machine, started)
