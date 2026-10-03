"""One controlled proposal iteration — review cycle → integration composition.

A SMALL deterministic composition helper over the two existing executors.
It duplicates NOTHING: ``run_review_cycle()`` and ``run_integration()``
keep every internal (guards, artifacts, state advances); this module only
sequences them and interprets the combined result::

    run_review_cycle()  (IDLE/SOURCE_VALIDATION/review-phase → INTEGRATION
                         — or BLOCKED / a failure outcome)
    → if READY_FOR_INTEGRATION: run_integration()
        → COMPLETED_CHANGED      ⇒ freshness gate ⇒ READY_FOR_NEXT_ITERATION
                                   (REVISION_REQUIRED + NEXT_ITERATION.json)
        → COMPLETED_NO_CHANGE    ⇒ READY_FOR_HARD_GATES (review-current)
        → NO_INTEGRATION_REQUIRED ⇒ READY_FOR_HARD_GATES (review-current)
        → anything else          ⇒ BLOCKED/FAILED (the integration executor
                                   never advanced past a failure it owns;
                                   the two post-write ARTIFACT_CONFLICT
                                   partial-success cases legitimately sit at
                                   HARD_GATE_VALIDATION and are reported as
                                   BLOCKED outcomes with the machine state
                                   carried honestly)

ONE call = ONE controlled proposal iteration.  There is NO while loop and
no automatic re-entry: the future UI/controller decides whether to run
another iteration.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional

from ..drivers.base import BaseDriver
from ..domain.enums import SessionPolicy
from ..proposal.enums import ProposalPhase, ProposalRole
from ..proposal.fingerprint import proposal_fingerprint
from ..proposal.models import ProposalAgentConfig
from ..proposal.state_machine import ProposalStateMachine
from .integration_executor import (
    ProposalIntegrationExecutionReport,
    ProposalIntegrationOutcome,
    run_integration,
)
from .review_freshness import (
    ReviewFreshness,
    ReviewFreshnessError,
    evaluate_review_freshness,
)
from .review_loop import (
    ProposalReviewCycleOutcome,
    ProposalReviewCycleReport,
    run_review_cycle,
)
from .revision_handoff import (
    NextIterationHandoffError,
    build_next_iteration_payload,
    write_next_iteration_handoff,
)

__all__ = [
    "ProposalIterationOutcome",
    "ProposalIterationReport",
    "run_iteration",
]


class ProposalIterationOutcome(str, Enum):
    """Combined outcome of ONE composed proposal iteration."""

    READY_FOR_HARD_GATES = "READY_FOR_HARD_GATES"
    READY_FOR_NEXT_ITERATION = "READY_FOR_NEXT_ITERATION"
    REVIEW_BLOCKED = "REVIEW_BLOCKED"          # a reviewer returned BLOCKED
    REVIEW_FAILED = "REVIEW_FAILED"            # review-cycle operational failure
    SOURCE_VALIDATION_FAILED = "SOURCE_VALIDATION_FAILED"
    STALE_PROPOSAL = "STALE_PROPOSAL"
    ARTIFACT_CONFLICT = "ARTIFACT_CONFLICT"
    INTEGRATION_FAILED = "INTEGRATION_FAILED"  # integration executor failure

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


@dataclass(slots=True)
class ProposalIterationReport:
    """JSON-friendly combined report of ONE composed iteration."""

    outcome: ProposalIterationOutcome
    iteration_number: int
    proposal_revision: str = ""
    state_before: ProposalPhase = ProposalPhase.IDLE
    state_after: ProposalPhase = ProposalPhase.IDLE
    review_report: Optional[dict[str, Any]] = None
    integration_report: Optional[dict[str, Any]] = None
    review_freshness: Optional[dict[str, Any]] = None
    next_iteration_handoff_path: str = ""
    current_proposal_hash: str = ""
    duration_s: float = 0.0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "iteration_number": self.iteration_number,
            "proposal_revision": self.proposal_revision,
            "state_before": self.state_before.value,
            "state_after": self.state_after.value,
            "review_report": self.review_report,
            "integration_report": self.integration_report,
            "review_freshness": self.review_freshness,
            "next_iteration_handoff_path": self.next_iteration_handoff_path,
            "current_proposal_hash": self.current_proposal_hash,
            "duration_s": round(self.duration_s, 6),
            "error": self.error,
        }


_REVIEW_OUTCOME_MAP = {
    ProposalReviewCycleOutcome.BLOCKED: ProposalIterationOutcome.REVIEW_BLOCKED,
    ProposalReviewCycleOutcome.REVIEW_FAILED: ProposalIterationOutcome.REVIEW_FAILED,
    ProposalReviewCycleOutcome.SOURCE_VALIDATION_FAILED: (
        ProposalIterationOutcome.SOURCE_VALIDATION_FAILED
    ),
    ProposalReviewCycleOutcome.STALE_PROPOSAL: ProposalIterationOutcome.STALE_PROPOSAL,
    ProposalReviewCycleOutcome.ARTIFACT_CONFLICT: ProposalIterationOutcome.ARTIFACT_CONFLICT,
}


def run_iteration(
    *,
    workspace: Path,
    state_machine: ProposalStateMachine,
    iteration_number: int,
    proposal_revision: str,
    reviewer_drivers: Mapping[ProposalRole, BaseDriver],
    orchestrator_driver: Optional[BaseDriver],
    reviewer_session_policies: Optional[Mapping[ProposalRole, SessionPolicy]] = None,
    orchestrator_session_policy: SessionPolicy = SessionPolicy.ALWAYS_NEW,
    timeout_s: Optional[float] = None,
    previous_findings: tuple[str, ...] = (),
    previous_findings_records: Optional[list[dict[str, Any]]] = None,
    extra_instructions: str = "",
    reviewer_agent_configs: Optional[Mapping[ProposalRole, ProposalAgentConfig]] = None,
    orchestrator_agent_config: Optional[ProposalAgentConfig] = None,
) -> ProposalIterationReport:
    """Run ONE controlled proposal iteration — review cycle, then integration.

    Composition ONLY: every guard, artifact and state advance belongs to the
    two executors this function calls.  No loop, no automatic re-entry.

    Session 017A: ``reviewer_agent_configs`` /
    ``orchestrator_agent_config`` forward the operator's per-role
    :class:`~encomm_pcc.proposal.ProposalAgentConfig` objects into
    ``run_review_cycle()`` / ``run_integration()`` so the driver
    ``SessionRequest`` carries the ACTUAL profile/provider/model/workspace —
    exactly what the operator sees in AGENTS.  Both stay optional; existing
    scripted/offline callers keep working unchanged.
    """
    started = time.monotonic()
    workspace = Path(workspace)
    master_path = workspace / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
    state_before = state_machine.phase
    report = ProposalIterationReport(
        outcome=ProposalIterationOutcome.REVIEW_FAILED,
        iteration_number=iteration_number,
        proposal_revision=str(proposal_revision),
        state_before=state_before,
    )

    # -- 1. the review cycle -------------------------------------------------
    review = run_review_cycle(
        workspace=workspace,
        state_machine=state_machine,
        iteration_number=iteration_number,
        proposal_revision=str(proposal_revision),
        reviewer_drivers=reviewer_drivers,
        reviewer_session_policies=reviewer_session_policies,
        timeout_s=timeout_s,
        previous_findings=tuple(previous_findings),
        extra_instructions=extra_instructions,
        reviewer_agent_configs=reviewer_agent_configs,
        # The cycle's SessionRequest workspace IS the actual proposal
        # workspace (it already defaults to ``workspace``; pinned explicitly
        # so it is never accidentally empty in production).
        proposal_workspace_path=workspace,
    )
    report.review_report = review.to_dict()

    if review.outcome is not ProposalReviewCycleOutcome.READY_FOR_INTEGRATION:
        mapped = _REVIEW_OUTCOME_MAP.get(
            review.outcome, ProposalIterationOutcome.REVIEW_FAILED
        )
        report.outcome = mapped
        report.error = review.error
        report.state_after = state_machine.phase
        report.duration_s = time.monotonic() - started
        return report

    # -- 2. the integration (the machine is exactly at INTEGRATION) ----------
    integration = run_integration(
        workspace=workspace,
        state_machine=state_machine,
        iteration_number=iteration_number,
        proposal_revision=str(proposal_revision),
        orchestrator_driver=orchestrator_driver,
        session_policy=orchestrator_session_policy,
        timeout_s=timeout_s,
        previous_findings=previous_findings_records,
        pre_review_version_path=review.frozen_version_path,
        orchestrator_agent_config=orchestrator_agent_config,
    )
    report.integration_report = integration.to_dict()
    report.state_after = state_machine.phase

    if not integration.ok:
        if integration.outcome in (
            ProposalIntegrationOutcome.ARTIFACT_CONFLICT,
        ) and state_machine.phase is ProposalPhase.HARD_GATE_VALIDATION:
            # The honest partial-success case: the master write DID happen
            # but post-write evidence conflicts.  The machine legitimately
            # advanced; report the BLOCKED-class outcome with the real state.
            report.outcome = ProposalIterationOutcome.ARTIFACT_CONFLICT
        else:
            report.outcome = ProposalIterationOutcome.INTEGRATION_FAILED
        report.error = integration.error
        report.current_proposal_hash = integration.output_proposal_hash
        report.duration_s = time.monotonic() - started
        return report

    # -- 3. review freshness at HARD_GATE_VALIDATION (§13) -------------------
    try:
        current_hash = proposal_fingerprint(master_path)
    except OSError as exc:
        report.outcome = ProposalIterationOutcome.INTEGRATION_FAILED
        report.error = f"post-integration fingerprint failed: {exc}"
        report.duration_s = time.monotonic() - started
        return report
    report.current_proposal_hash = current_hash
    try:
        freshness = evaluate_review_freshness(
            workspace=workspace, current_proposal_hash=current_hash
        )
    except ReviewFreshnessError as exc:
        report.outcome = ProposalIterationOutcome.ARTIFACT_CONFLICT
        report.error = str(exc)
        report.duration_s = time.monotonic() - started
        return report
    report.review_freshness = freshness.to_dict()

    if freshness.is_current:
        # Output hash == latest reviewed hash: the proposal on disk IS what
        # the three reviewers approved — hard gates may proceed.
        report.outcome = ProposalIterationOutcome.READY_FOR_HARD_GATES
        report.duration_s = time.monotonic() - started
        return report

    # -- 4. changed proposal ⇒ revision handoff + REVISION_REQUIRED ---------
    previous_reviewed_hash = freshness.latest_reviewed_hash
    parsed = integration.parsed_result
    if parsed is None:
        # Zero-AI bypass cannot reach here (output hash == input hash ==
        # reviewed hash); a defensive guard stays cheap and loud.
        report.outcome = ProposalIterationOutcome.INTEGRATION_FAILED
        report.error = (
            "changed proposal without a parsed integration result; refusing "
            "to prepare a revision handoff."
        )
        report.duration_s = time.monotonic() - started
        return report
    payload = build_next_iteration_payload(
        previous_iteration_number=iteration_number,
        previous_reviewed_hash=previous_reviewed_hash,
        revised_proposal_hash=current_hash,
        integration_result=parsed,
        integration_summary=parsed.summary,
        previous_findings=previous_findings_records,
    )
    try:
        handoff_path = write_next_iteration_handoff(
            workspace=workspace, payload=payload
        )
    except (NextIterationHandoffError, OSError) as exc:
        report.outcome = ProposalIterationOutcome.ARTIFACT_CONFLICT
        report.error = (
            f"the proposal changed but the revision handoff failed: {exc}"
        )
        report.duration_s = time.monotonic() - started
        return report
    report.next_iteration_handoff_path = str(handoff_path)

    state_machine.transition_to(ProposalPhase.REVISION_REQUIRED)
    report.state_after = state_machine.phase
    report.outcome = ProposalIterationOutcome.READY_FOR_NEXT_ITERATION
    report.duration_s = time.monotonic() - started
    return report
