"""ASTRA panel-chair integration composition (Session 019, brief §21).

ONE deterministic composition that runs the FULL panel iteration as the
panel chair flow:

    run_parallel_panel_review_cycle()   (3 evaluators in parallel)
    → run_panel_consensus_round()       (3 evaluators judge the docket, in
                                         parallel; matrix + advisory
                                         readiness persisted)
    → run_integration()                 (ASTRA = PANEL CHAIR + SOLE EDITOR;
                                         the consensus matrix and advisory
                                         readiness reach the packet as the
                                         PANEL CHAIR CONTEXT section)
    → review freshness (changed proposal ⇒ revision handoff)

The composition duplicates NOTHING: every guard, artifact and state
advance belongs to the three existing executors.  There is NO loop here —
the campaign layer decides whether to iterate.

Model-call accounting (brief §28): 3 first-pass + 3 consensus + 1 ASTRA
= 7 per full iteration.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional

from ..drivers.base import BaseDriver
from ..domain.enums import SessionPolicy
from ..proposal.enums import ProposalPhase, ProposalRole
from ..proposal.fingerprint import proposal_fingerprint
from ..proposal.models import ProposalAgentConfig
from ..proposal.panel_contracts import PanelConsensusResult
from ..proposal.panel_matrix import PanelConsensusMatrix, PanelDocket
from ..proposal.review_aggregation import ProposalReviewBundle
from ..proposal.source_budget import SourceBudget
from ..proposal.source_snapshot import load_review_snapshot
from ..proposal.state_machine import ProposalStateMachine
from .integration_executor import (
    ProposalIntegrationOutcome,
    run_integration,
)
from .panel_runtime import (
    ProposalPanelOutcome,
    run_panel_consensus_round,
    run_parallel_panel_review_cycle,
)
from .review_freshness import (
    ReviewFreshnessError,
    evaluate_review_freshness,
)
from .revision_handoff import (
    NextIterationHandoffError,
    build_next_iteration_payload,
    write_next_iteration_handoff,
)

__all__ = [
    "PanelChairOutcome",
    "PanelChairReport",
    "render_consensus_for_chair",
    "run_panel_chair_iteration",
]


class PanelChairOutcome(str, Enum):
    """Terminal outcome of ONE panel-chair iteration."""

    READY_FOR_HARD_GATES = "READY_FOR_HARD_GATES"
    READY_FOR_NEXT_ITERATION = "READY_FOR_NEXT_ITERATION"
    REVIEW_BLOCKED = "REVIEW_BLOCKED"
    PANEL_FAILED = "PANEL_FAILED"
    SOURCE_VALIDATION_FAILED = "SOURCE_VALIDATION_FAILED"
    SOURCE_BUDGET_EXCEEDED = "SOURCE_BUDGET_EXCEEDED"
    STALE_PROPOSAL = "STALE_PROPOSAL"
    ARTIFACT_CONFLICT = "ARTIFACT_CONFLICT"
    INTEGRATION_FAILED = "INTEGRATION_FAILED"
    CONSENSUS_FAILED = "CONSENSUS_FAILED"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


@dataclass(slots=True)
class PanelChairReport:
    """JSON-friendly combined report of ONE panel-chair iteration."""

    outcome: PanelChairOutcome
    iteration_number: int
    proposal_revision: str = ""
    state_before: ProposalPhase = ProposalPhase.IDLE
    state_after: ProposalPhase = ProposalPhase.IDLE
    proposal_hash: str = ""
    source_pack_id: str = ""
    panel_report: Optional[dict[str, Any]] = None
    consensus_summary: Optional[dict[str, Any]] = None
    readiness: Optional[dict[str, Any]] = None
    integration_report: Optional[dict[str, Any]] = None
    review_freshness: Optional[dict[str, Any]] = None
    next_iteration_handoff_path: str = ""
    current_proposal_hash: str = ""
    model_calls_used: int = 0
    duration_s: float = 0.0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "iteration_number": self.iteration_number,
            "proposal_revision": self.proposal_revision,
            "state_before": self.state_before.value,
            "state_after": self.state_after.value,
            "proposal_hash": self.proposal_hash,
            "source_pack_id": self.source_pack_id,
            "panel_report": self.panel_report,
            "consensus_summary": self.consensus_summary,
            "readiness": self.readiness,
            "integration_report": self.integration_report,
            "review_freshness": self.review_freshness,
            "next_iteration_handoff_path": self.next_iteration_handoff_path,
            "current_proposal_hash": self.current_proposal_hash,
            "model_calls_used": self.model_calls_used,
            "duration_s": round(self.duration_s, 6),
            "error": self.error,
        }


def render_consensus_for_chair(
    matrix: PanelConsensusMatrix,
    consensus_results: Mapping[ProposalRole, PanelConsensusResult],
) -> str:
    """Deterministic consensus rendering for the ASTRA chair packet.

    Structured text only: per-item votes, counts, resolutions, blocking
    flags, then each evaluator's summary.  No transcripts, no raw envelopes.
    """
    lines: list[str] = []
    for row in matrix.rows:
        votes = " / ".join(
            f"{role}={vote}" for role, vote in sorted(row.votes.items())
        )
        lines.append(
            f"- {row.item_id}: {votes}"
            f" | unresolved={str(row.unresolved).lower()}"
            f" | blocks_acceptance={str(row.blocks_acceptance).lower()}"
        )
        for resolution in row.proposed_resolutions:
            lines.append(f"    resolution: {resolution}")
    for role in sorted(consensus_results):
        summary = consensus_results[role].summary.strip()
        if summary:
            lines.append(f"### {role.value} panel position: {summary}")
    return "\n".join(lines)


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _pre_review_path(workspace: Path, iteration_number: int) -> Path:
    return (
        workspace
        / "06_VERSIONS"
        / f"iteration_{iteration_number:03d}_pre_review.md"
    )


def run_panel_chair_iteration(
    *,
    workspace: Path,
    state_machine: ProposalStateMachine,
    iteration_number: int,
    proposal_revision: str,
    reviewer_drivers: Mapping[ProposalRole, BaseDriver],
    orchestrator_driver: Optional[BaseDriver],
    reviewer_agent_configs: Optional[Mapping[ProposalRole, ProposalAgentConfig]] = None,
    orchestrator_agent_config: Optional[ProposalAgentConfig] = None,
    timeout_s: Optional[float] = None,
    previous_findings: tuple[str, ...] = (),
    previous_findings_records: Optional[list[dict[str, Any]]] = None,
    extra_instructions: str = "",
    source_budget: SourceBudget | None = None,
    orchestrator_session_policy: SessionPolicy = SessionPolicy.ALWAYS_NEW,
) -> PanelChairReport:
    """Run ONE full panel-chair iteration (no loop — the caller decides)."""
    started = time.monotonic()
    workspace = Path(workspace)
    master_path = workspace / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
    state_before = state_machine.phase
    report = PanelChairReport(
        outcome=PanelChairOutcome.PANEL_FAILED,
        iteration_number=iteration_number,
        proposal_revision=str(proposal_revision),
        state_before=state_before,
    )

    def _finish(outcome: PanelChairOutcome, error: str = "") -> PanelChairReport:
        report.outcome = outcome
        if error:
            report.error = error
        report.state_after = state_machine.phase
        report.duration_s = time.monotonic() - started
        return report

    # -- ROUND 1: parallel independent first pass ---------------------------
    panel = run_parallel_panel_review_cycle(
        workspace=workspace,
        state_machine=state_machine,
        iteration_number=iteration_number,
        proposal_revision=str(proposal_revision),
        reviewer_drivers=reviewer_drivers,
        reviewer_agent_configs=reviewer_agent_configs,
        timeout_s=timeout_s,
        previous_findings=previous_findings,
        extra_instructions=extra_instructions,
        source_budget=source_budget,
    )
    report.panel_report = panel.to_dict()
    report.proposal_hash = panel.proposal_hash
    report.source_pack_id = panel.source_pack_id
    report.model_calls_used += 3

    if panel.outcome is not ProposalPanelOutcome.READY_FOR_INTEGRATION:
        mapped = {
            ProposalPanelOutcome.BLOCKED: PanelChairOutcome.REVIEW_BLOCKED,
            ProposalPanelOutcome.SOURCE_VALIDATION_FAILED: (
                PanelChairOutcome.SOURCE_VALIDATION_FAILED
            ),
            ProposalPanelOutcome.SOURCE_BUDGET_EXCEEDED: (
                PanelChairOutcome.SOURCE_BUDGET_EXCEEDED
            ),
            ProposalPanelOutcome.STALE_PROPOSAL: PanelChairOutcome.STALE_PROPOSAL,
            ProposalPanelOutcome.ARTIFACT_CONFLICT: PanelChairOutcome.ARTIFACT_CONFLICT,
        }.get(panel.outcome, PanelChairOutcome.PANEL_FAILED)
        return _finish(mapped, panel.error)

    # -- ROUND 2: parallel consensus ----------------------------------------
    from .panel_runtime import _panel_docket_path

    bundle = ProposalReviewBundle.from_dict(panel.aggregate_bundle)  # type: ignore[arg-type]
    docket_payload = _read_json(_panel_docket_path(workspace, iteration_number))
    if docket_payload is None:
        return _finish(
            PanelChairOutcome.ARTIFACT_CONFLICT,
            "panel docket artifact missing after a successful panel cycle.",
        )
    docket = PanelDocket.from_dict(docket_payload)
    snapshot = load_review_snapshot(
        workspace,
        blueprint_max_chars=(
            source_budget.blueprint_max_chars if source_budget else None
        ),
    )
    try:
        round2 = run_panel_consensus_round(
            workspace=workspace,
            iteration_number=iteration_number,
            proposal_revision=str(proposal_revision),
            proposal_hash=panel.proposal_hash,
            bundle=bundle,
            docket=docket,
            source_snapshot=snapshot,
            consensus_drivers=reviewer_drivers,  # same per-role instances
            consensus_agent_configs=reviewer_agent_configs,
            timeout_s=timeout_s,
        )
    except RuntimeError as exc:
        return _finish(PanelChairOutcome.CONSENSUS_FAILED, str(exc))
    report.model_calls_used += 3
    report.consensus_summary = {
        "matrix": round2["matrix"],
        "participating_roles": round2["participating_roles"],
    }
    report.readiness = round2.get("readiness")

    # -- ROUND 3: ASTRA as PANEL CHAIR + SOLE EDITOR ------------------------
    matrix = PanelConsensusMatrix.from_dict(round2["matrix"])
    consensus_results = {
        ProposalRole(role): PanelConsensusResult.from_dict(payload)
        for role, payload in round2.get("consensus_results", {}).items()
    }
    consensus_text = render_consensus_for_chair(matrix, consensus_results)
    readiness_text = ""
    if report.readiness is not None:
        readiness_data = report.readiness
        readiness_text = (
            "INTERNAL READINESS (advisory, NOT an EIC score): "
            f"{readiness_data.get('readiness')}/100; per-criterion medians: "
            + ", ".join(
                f"{k}={v}"
                for k, v in readiness_data.get("per_criterion_median", {}).items()
            )
            + "; penalties: "
            + json.dumps(
                readiness_data.get("penalties_applied", {}), sort_keys=True
            )
        )
    chair_instructions = "\n\n".join(
        part for part in (consensus_text, readiness_text) if part.strip()
    )
    combined_instructions = (
        f"{extra_instructions}\n\n{chair_instructions}".strip()
        if extra_instructions
        else chair_instructions
    )

    integration = run_integration(
        workspace=workspace,
        state_machine=state_machine,
        iteration_number=iteration_number,
        proposal_revision=str(proposal_revision),
        orchestrator_driver=orchestrator_driver,
        session_policy=orchestrator_session_policy,
        timeout_s=timeout_s,
        previous_findings=previous_findings_records,
        pre_review_version_path=str(_pre_review_path(workspace, iteration_number)),
        orchestrator_agent_config=orchestrator_agent_config,
        extra_instructions=combined_instructions,
        # Session 021B: the REAL source-pack identity the evaluators/chair
        # operated on is threaded into the integration (and thus into the
        # committed DOCUMENT_PAIR_STATE) instead of an empty placeholder.
        source_pack_id=panel.source_pack_id,
    )
    report.model_calls_used += (
        0
        if integration.outcome is ProposalIntegrationOutcome.NO_INTEGRATION_REQUIRED
        else 1
    )
    report.integration_report = integration.to_dict()

    if not integration.ok:
        if (
            integration.outcome is ProposalIntegrationOutcome.ARTIFACT_CONFLICT
            and state_machine.phase is ProposalPhase.HARD_GATE_VALIDATION
        ):
            report.outcome = PanelChairOutcome.ARTIFACT_CONFLICT
        else:
            report.outcome = PanelChairOutcome.INTEGRATION_FAILED
        return _finish(integration.error)
    report.current_proposal_hash = integration.output_proposal_hash

    # -- review freshness (same contract as the sequential iteration) -------
    try:
        current_hash = proposal_fingerprint(master_path)
    except OSError as exc:
        return _finish(
            PanelChairOutcome.INTEGRATION_FAILED,
            f"post-integration fingerprint failed: {exc}",
        )
    report.current_proposal_hash = current_hash
    try:
        freshness = evaluate_review_freshness(
            workspace=workspace, current_proposal_hash=current_hash
        )
    except ReviewFreshnessError as exc:
        return _finish(PanelChairOutcome.ARTIFACT_CONFLICT, str(exc))
    report.review_freshness = freshness.to_dict()

    if freshness.is_current:
        return _finish(PanelChairOutcome.READY_FOR_HARD_GATES)

    parsed = integration.parsed_result
    if parsed is None:
        return _finish(
            PanelChairOutcome.INTEGRATION_FAILED,
            "changed proposal without a parsed integration result; refusing "
            "to prepare a revision handoff.",
        )
    payload = build_next_iteration_payload(
        previous_iteration_number=iteration_number,
        previous_reviewed_hash=freshness.latest_reviewed_hash,
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
        return _finish(
            PanelChairOutcome.ARTIFACT_CONFLICT,
            f"the proposal changed but the revision handoff failed: {exc}",
        )
    state_machine.transition_to(ProposalPhase.REVISION_REQUIRED)
    report.next_iteration_handoff_path = str(handoff_path)
    return _finish(PanelChairOutcome.READY_FOR_NEXT_ITERATION)
