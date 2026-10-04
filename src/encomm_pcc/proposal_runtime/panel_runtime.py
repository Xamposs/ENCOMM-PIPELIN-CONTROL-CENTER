"""Parallel evaluator panel runtime — Session 019 (briefs §15–§23).

Two parallel rounds over ONE frozen proposal revision:

    ROUND 1 — INDEPENDENT FIRST PASS (3 evaluator calls overlap)
      → all three valid, master unchanged → deterministic post-join walk
        of the proven phase graph (SCIENTIFIC_REVIEW →
        IMPLEMENTATION_REVIEW → RED_TEAM_REVIEW → INTEGRATION) in canonical
        order → aggregation → durable artifacts + panel_docket.json
    ROUND 2 — CONSENSUS (3 evaluator calls overlap again)
      → every evaluator judges each docket item + readiness rubric
        → deterministic consensus matrix + advisory readiness
        → panel_consensus.json + readiness.json

Safety contract:

* NO driver instance is shared between roles (each role owns its own);
* the three model calls of a round genuinely overlap (ThreadPoolExecutor);
* the state machine is NEVER touched from worker threads — durable
  advancement happens AFTER the join, deterministically (brief §17);
* any failed/unaligned call → NO aggregation, NO consensus, fail closed
  with per-role diagnostics (never pretend the panel finished);
* master re-fingerprinted after every round (mutation ⇒ STALE_PROPOSAL);
* no raw transcripts persisted; artifacts are structured and bounded.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional

from ..drivers.base import BaseDriver
from ..domain.enums import SessionPolicy
from ..proposal.enums import ProposalPhase, ProposalRole, ProposalReviewVerdict
from ..proposal.fingerprint import proposal_fingerprint
from ..proposal.models import ProposalAgentConfig, ProposalReviewResult
from ..proposal.panel_contracts import (
    PanelConsensusInputs,
    PanelConsensusPacket,
    PanelConsensusResult,
    build_panel_consensus_packet,
    parse_panel_consensus,
)
from ..proposal.panel_matrix import (
    PanelDocket,
    build_consensus_matrix,
    build_panel_docket,
    docket_text,
)
from ..proposal.readiness import (
    ReadinessAssessment,
    ReadinessInputs,
    ReadinessResult,
    compute_readiness,
)
from ..proposal.review_aggregation import ProposalReviewBundle, aggregate_reviews
from ..proposal.review_packet import ProposalReviewInputs, build_review_packet
from ..proposal.source_budget import SourceBudget, check_source_budget
from ..proposal.source_snapshot import ReviewSourceSnapshot, load_review_snapshot
from ..proposal.state_machine import ProposalStateMachine
from .review_artifacts import (
    ArtifactConflictError,
    ReviewArtifactWriter,
    persist_cycle_artifacts,
)
from .review_executor import (
    ProposalReviewExecutionReport,
    ProposalReviewGuardError,
    ProposalReviewOutcome,
    execute_review_call,
)
from .review_loop import (
    REVIEW_SEQUENCE,
    SourceValidationError,
    _validate_sources,
)
from .version_freeze import (
    VersionFreezeError,
    freeze_pre_review_blueprint_version,
    freeze_pre_review_version,
)

__all__ = [
    "SOURCE_PACK_ID_PREFIX",
    "ProposalPanelOutcome",
    "ProposalPanelReviewReport",
    "run_panel_consensus_round",
    "run_parallel_panel_review_cycle",
]

#: Deterministic source-pack identity prefix (sha over the frozen inputs'
#: provenance — identity, not content: the master hash already binds bytes).
SOURCE_PACK_ID_PREFIX = "srcpack"


class ProposalPanelOutcome(str, Enum):
    """Terminal outcome of ONE parallel panel review cycle."""

    READY_FOR_INTEGRATION = "READY_FOR_INTEGRATION"
    READY_FOR_NEXT_ITERATION = "READY_FOR_NEXT_ITERATION"
    BLOCKED = "BLOCKED"                        # a reviewer returned BLOCKED
    PANEL_FAILED = "PANEL_FAILED"              # any call failed → no aggregation
    SOURCE_VALIDATION_FAILED = "SOURCE_VALIDATION_FAILED"
    SOURCE_BUDGET_EXCEEDED = "SOURCE_BUDGET_EXCEEDED"
    STALE_PROPOSAL = "STALE_PROPOSAL"
    ARTIFACT_CONFLICT = "ARTIFACT_CONFLICT"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


@dataclass(slots=True)
class ProposalPanelReviewReport:
    """JSON-friendly operational report of ONE parallel panel review."""

    outcome: ProposalPanelOutcome
    iteration_number: int
    proposal_hash: str = ""
    proposal_revision: str = ""
    source_pack_id: str = ""
    state_before: ProposalPhase = ProposalPhase.IDLE
    state_after: ProposalPhase = ProposalPhase.IDLE
    completed_roles: list[str] = field(default_factory=list)
    execution_reports: dict[str, Any] = field(default_factory=dict)
    aggregate_bundle: Optional[dict[str, Any]] = None
    panel_docket: Optional[dict[str, Any]] = None
    docket_path: str = ""
    failed_roles: list[str] = field(default_factory=list)
    error: str = ""
    duration_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "iteration_number": self.iteration_number,
            "proposal_hash": self.proposal_hash,
            "proposal_revision": self.proposal_revision,
            "source_pack_id": self.source_pack_id,
            "state_before": self.state_before.value,
            "state_after": self.state_after.value,
            "completed_roles": list(self.completed_roles),
            "execution_reports": dict(self.execution_reports),
            "aggregate_bundle": self.aggregate_bundle,
            "panel_docket": self.panel_docket,
            "docket_path": self.docket_path,
            "failed_roles": list(self.failed_roles),
            "error": self.error,
            "duration_s": round(self.duration_s, 6),
        }


def _source_pack_id(snapshot: ReviewSourceSnapshot, proposal_hash: str) -> str:
    """Deterministic identity of the source pack a panel consumed.

    Character-length provenance + the master hash: two runs over the same
    workspace revision and sources produce the same id; any source change
    that alters a section length changes the id (identity binding, not a
    content hash — the master bytes are already hash-bound).
    """
    lengths = "|".join(
        str(len(text))
        for text in (
            snapshot.master_proposal_text,
            snapshot.master_blueprint_text,
            snapshot.current_blueprint_text,
            snapshot.application_template_text,
            snapshot.project_facts_text,
            snapshot.team_text,
            snapshot.architecture_text,
            snapshot.terminology_text,
        )
    )
    docs = ",".join(name for name, _ in snapshot.official_documents)
    raw = f"{SOURCE_PACK_ID_PREFIX}:{proposal_hash[:16]}:{lengths}:{docs}"
    # Bounded, filesystem-safe, deterministic.
    return raw.replace("/", "-").replace(" ", "")[:160]


def _atomic_write_json(target: Path, payload: dict[str, Any]) -> None:
    """Deterministic atomic JSON write (same convention as review artifacts)."""
    import json
    import os
    import tempfile

    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    data = (text + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp-panel-")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise


def _panel_docket_path(workspace: Path, iteration_number: int) -> Path:
    return workspace / "04_REVIEWS" / f"iteration_{iteration_number:03d}" / "panel_docket.json"


def _panel_consensus_path(workspace: Path, iteration_number: int) -> Path:
    return workspace / "04_REVIEWS" / f"iteration_{iteration_number:03d}" / "panel_consensus.json"


def _panel_readiness_path(workspace: Path, iteration_number: int) -> Path:
    return workspace / "04_REVIEWS" / f"iteration_{iteration_number:03d}" / "readiness.json"


# ---------------------------------------------------------------------------
# ROUND 1 — the parallel independent first pass
# ---------------------------------------------------------------------------
def run_parallel_panel_review_cycle(
    *,
    workspace: Path,
    state_machine: ProposalStateMachine,
    iteration_number: int,
    proposal_revision: str,
    reviewer_drivers: Mapping[ProposalRole, BaseDriver],
    reviewer_agent_configs: Optional[Mapping[ProposalRole, ProposalAgentConfig]] = None,
    reviewer_session_policies: Optional[Mapping[ProposalRole, SessionPolicy]] = None,
    timeout_s: Optional[float] = None,
    previous_findings: tuple[str, ...] = (),
    extra_instructions: str = "",
    source_budget: SourceBudget | None = None,
    max_workers: int = 3,
) -> ProposalPanelReviewReport:
    """Run ONE parallel three-evaluator panel cycle — fail closed.

    The machine enters from IDLE / SOURCE_VALIDATION / a review phase /
    REVISION_REQUIRED exactly like ``run_review_cycle`` and finishes at
    INTEGRATION (or an honest failure position).  The three evaluator calls
    overlap; phase advancement is walked AFTER the join in canonical order.
    """
    started = time.monotonic()
    workspace = Path(workspace)
    master_path = workspace / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
    state_before = state_machine.phase
    report = ProposalPanelReviewReport(
        outcome=ProposalPanelOutcome.PANEL_FAILED,
        iteration_number=iteration_number,
        proposal_revision=str(proposal_revision),
        state_before=state_before,
    )

    def _finish(outcome: ProposalPanelOutcome, error: str = "",
                failed_roles: Optional[list[str]] = None) -> ProposalPanelReviewReport:
        report.outcome = outcome
        if error:
            report.error = error
        if failed_roles:
            report.failed_roles = failed_roles
        report.state_after = state_machine.phase
        report.duration_s = time.monotonic() - started
        return report

    missing = [r.value for r in REVIEW_SEQUENCE if r not in reviewer_drivers]
    if missing:
        return _finish(
            ProposalPanelOutcome.PANEL_FAILED,
            "reviewer_drivers must provide all three reviewer roles "
            "(separate instances per role); missing: " + ", ".join(missing),
        )
    # D-058/§16: never one driver shared across roles.
    ids = [id(reviewer_drivers[r]) for r in REVIEW_SEQUENCE]
    if len(set(ids)) != len(ids):
        return _finish(
            ProposalPanelOutcome.PANEL_FAILED,
            "each evaluator role requires its OWN driver instance; sharing "
            "one driver across roles is a panel-independence violation.",
        )

    # -- position (entry contract) ------------------------------------------
    # The PANEL path is the FRESH-FULL-CYCLE composition: IDLE (fresh) or
    # REVISION_REQUIRED (next panel iteration).  Partial-phase resume
    # (SOURCE_VALIDATION / mid-review re-entry) belongs to the sequential
    # cycle's resume contract; the panel refuses those positions rather
    # than guess which reviewers already ran outside its artifacts.
    if state_before is ProposalPhase.IDLE:
        state_machine.transition_to(ProposalPhase.SOURCE_VALIDATION)
    elif state_before is ProposalPhase.REVISION_REQUIRED:
        from ..proposal.state_machine import REVIEW_ITERATION_ENTRY

        state_machine.transition_to(REVIEW_ITERATION_ENTRY)
    else:
        return _finish(
            ProposalPanelOutcome.PANEL_FAILED,
            f"run_parallel_panel_review_cycle runs FULL panel iterations: "
            f"start from IDLE (or REVISION_REQUIRED for the next iteration); "
            f"the machine is at {state_before.value}. For partial-phase "
            "resume use the sequential run_review_cycle.",
        )

    # -- source validation + budget gate (BEFORE any model call) -----------
    try:
        cycle_hash, snapshot = _validate_sources(
            workspace=workspace,
            master_proposal_path=master_path,
            iteration_number=iteration_number,
        )
    except SourceValidationError as exc:
        # Mirror the sequential contract: a validation failure walks the
        # explicit SOURCE_VALIDATION → FAILED edge when the machine sits
        # there (the parallel cycle enters through the same edge).
        if state_machine.phase is ProposalPhase.SOURCE_VALIDATION:
            state_machine.transition_to(ProposalPhase.FAILED)
        return _finish(ProposalPanelOutcome.SOURCE_VALIDATION_FAILED, str(exc))
    report.proposal_hash = cycle_hash

    try:
        blueprint_chars = check_source_budget(workspace, source_budget)
    except Exception as exc:  # SourceBudgetExceededError (ValueError subclass-safe)
        return _finish(ProposalPanelOutcome.SOURCE_BUDGET_EXCEEDED, str(exc))

    budget_cap = source_budget.blueprint_max_chars if source_budget else None
    try:
        # Re-load with the configured blueprint bound so packets carry the
        # whole canonical blueprint when the budget allows it.
        snapshot = load_review_snapshot(
            workspace, blueprint_max_chars=budget_cap
        )
    except Exception as exc:
        if state_machine.phase is ProposalPhase.SOURCE_VALIDATION:
            state_machine.transition_to(ProposalPhase.FAILED)
        return _finish(ProposalPanelOutcome.SOURCE_VALIDATION_FAILED, str(exc))
    del blueprint_chars  # informational only; the cap above binds the read

    source_pack_id = _source_pack_id(snapshot, cycle_hash)
    report.source_pack_id = source_pack_id

    # -- Session 021: fingerprint the LIVING Blueprint BEFORE any freeze ----
    from ..proposal.living_blueprint import CURRENT_BLUEPRINT_RELPATH

    current_bp_path = workspace.joinpath(*CURRENT_BLUEPRINT_RELPATH.split("/"))
    if current_bp_path.is_file() and snapshot.current_blueprint_available:
        try:
            current_bp_hash = proposal_fingerprint(current_bp_path)
            current_bp_available = True
        except OSError:
            current_bp_hash = ""
            current_bp_available = False
    else:
        current_bp_hash = ""
        current_bp_available = False

    # -- version freeze (exact reviewed bytes) ------------------------------
    try:
        freeze_pre_review_version(
            workspace=workspace,
            master_proposal_path=master_path,
            iteration_number=iteration_number,
            proposal_revision=str(proposal_revision),
            expected_hash=cycle_hash,
        )
    except (VersionFreezeError, OSError) as exc:
        return _finish(ProposalPanelOutcome.ARTIFACT_CONFLICT, f"version freeze failed: {exc}")
    try:
        # Session 021: the living Blueprint freezes beside the proposal so
        # every review artifact is traceable to the exact PAIR (no-op when
        # the workspace has no living Blueprint).
        freeze_pre_review_blueprint_version(
            workspace=workspace,
            iteration_number=iteration_number,
            expected_hash=current_bp_hash if current_bp_available else None,
        )
    except (VersionFreezeError, OSError) as exc:
        return _finish(
            ProposalPanelOutcome.ARTIFACT_CONFLICT,
            f"blueprint version freeze failed: {exc}",
        )

    if state_machine.phase is ProposalPhase.SOURCE_VALIDATION:
        state_machine.transition_to(ProposalPhase.SCIENTIFIC_REVIEW)

    writer = ReviewArtifactWriter(
        workspace=workspace,
        iteration_number=iteration_number,
        proposal_revision=str(proposal_revision),
        proposal_hash=cycle_hash,
    )

    # -- build ALL packets over the SAME frozen inputs, BEFORE launch -------
    # (current_bp_hash / current_bp_available were fingerprinted before the
    # version freeze above; every packet echoes the SAME pair binding.)
    def _packet(role: ProposalRole):
        inputs = ProposalReviewInputs(
            proposal_text=snapshot.master_proposal_text,
            iteration_number=iteration_number,
            proposal_revision=str(proposal_revision),
            proposal_hash=cycle_hash,
            master_blueprint_text=snapshot.master_blueprint_text,
            current_blueprint_text=snapshot.current_blueprint_text,
            current_blueprint_hash=current_bp_hash,
            current_blueprint_available=current_bp_available,
            project_facts_text=snapshot.project_facts_text,
            team_text=snapshot.team_text,
            architecture_text=snapshot.architecture_text,
            terminology_text=snapshot.terminology_text,
            official_requirements_text=snapshot.official_requirements_text,
            official_requirements_available=snapshot.official_requirements_available,
            previous_findings=tuple(previous_findings),
            extra_instructions=extra_instructions,
        )
        return build_review_packet(role, inputs, blueprint_max_chars=budget_cap)

    packets = {role: _packet(role) for role in REVIEW_SEQUENCE}

    # -- launch the three calls — genuinely overlapping ---------------------
    def _call(role: ProposalRole) -> ProposalReviewExecutionReport:
        # Each role owns its own driver instance; the state machine is NOT
        # touched here (machine-free core).
        return execute_review_call(
            packet=packets[role],
            driver=reviewer_drivers[role],
            master_proposal_path=master_path,
            session_policy=(
                reviewer_session_policies.get(role, SessionPolicy.ALWAYS_NEW)
                if reviewer_session_policies
                else SessionPolicy.ALWAYS_NEW
            ),
            timeout_s=timeout_s,
            agent_config=(
                reviewer_agent_configs.get(role)
                if reviewer_agent_configs
                else None
            ),
            proposal_workspace_path=workspace,
        )

    workers = max(1, min(int(max_workers), 3))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {role: pool.submit(_call, role) for role in REVIEW_SEQUENCE}
        joined: dict[ProposalRole, Any] = {}
        raised: dict[ProposalRole, BaseException] = {}
        for role, future in futures.items():
            try:
                joined[role] = future.result()
            except BaseException as exc:  # noqa: BLE001 - collected per role
                raised[role] = exc

    for role, exc in raised.items():
        report.execution_reports[role.value] = {
            "outcome": "GUARD_RAISED",
            "error": f"{type(exc).__name__}: {exc}",
        }
    if raised:
        return _finish(
            ProposalPanelOutcome.PANEL_FAILED,
            "a panel call raised before returning a report; no aggregation.",
            failed_roles=[r.value for r in raised],
        )

    executions: dict[ProposalRole, ProposalReviewExecutionReport] = joined  # type: ignore[assignment]
    for role in REVIEW_SEQUENCE:
        report.execution_reports[role.value] = executions[role].to_dict()
        if executions[role].ok:
            report.completed_roles.append(role.value)
    failed = [r for r in REVIEW_SEQUENCE if not executions[r].ok]
    if failed:
        # A MUTATION_DETECTED execution is a revision-integrity failure —
        # mapped to STALE_PROPOSAL exactly like the sequential cycle; any
        # other failure stays PANEL_FAILED.  Either way: nothing aggregated.
        if any(
            executions[r].outcome is ProposalReviewOutcome.MUTATION_DETECTED
            for r in failed
        ):
            return _finish(
                ProposalPanelOutcome.STALE_PROPOSAL,
                "; ".join(
                    f"{r.value}: {executions[r].error}"
                    for r in failed
                    if executions[r].error
                ),
                failed_roles=[r.value for r in failed],
            )
        return _finish(
            ProposalPanelOutcome.PANEL_FAILED,
            "; ".join(
                f"{r.value}: {executions[r].outcome.value} "
                f"({executions[r].error or executions[r].parse_reason})"
                for r in failed
            ),
            failed_roles=[r.value for r in failed],
        )

    # -- re-fingerprint: the exact frozen revision must have survived -------
    try:
        final_hash = proposal_fingerprint(master_path)
    except OSError as exc:
        return _finish(ProposalPanelOutcome.STALE_PROPOSAL, str(exc))
    if final_hash != cycle_hash:
        return _finish(
            ProposalPanelOutcome.STALE_PROPOSAL,
            f"MASTER_PROPOSAL changed during the panel (expected "
            f"{cycle_hash}, found {final_hash}); nothing aggregated.",
        )

    results: dict[ProposalRole, ProposalReviewResult] = {
        role: executions[role].result for role in REVIEW_SEQUENCE  # type: ignore[misc]
    }

    # -- durable per-review artifacts (deterministic canonical order) -------
    try:
        for role in REVIEW_SEQUENCE:
            writer.write_review_artifact(role, result=results[role], execution=executions[role])
    except ArtifactConflictError as exc:
        return _finish(ProposalPanelOutcome.ARTIFACT_CONFLICT, str(exc))

    # -- deterministic post-join phase walk (canonical order) ---------------
    blocked_role = next(
        (
            r
            for r in REVIEW_SEQUENCE
            if results[r].verdict is ProposalReviewVerdict.BLOCKED
        ),
        None,
    )
    if blocked_role is not None:
        # Walk to the FIRST blocked role's own phase, then take the explicit
        # D-055 BLOCKED edge — the same machine position the sequential
        # cycle would produce, applied deterministically after the join.
        for role in REVIEW_SEQUENCE:
            if role is blocked_role:
                break
            state_machine.transition_to(_NEXT_PHASE_OF[role])
        state_machine.transition_to(ProposalPhase.BLOCKED)
        return _finish(
            ProposalPanelOutcome.BLOCKED,
            f"{blocked_role.value} returned BLOCKED; the panel walked the "
            "graph to the explicit BLOCKED edge after the join.",
        )

    # -- aggregation (same PURE contract as the sequential cycle) -----------
    try:
        bundle = aggregate_reviews(
            iteration_number=iteration_number,
            proposal_revision=str(proposal_revision),
            proposal_hash=cycle_hash,
            scientific_review=results[ProposalRole.SCIENTIFIC_REVIEWER],
            implementation_review=results[ProposalRole.PROPOSAL_ENGINEER],
            red_team_review=results[ProposalRole.RED_TEAM_REVIEWER],
            current_blueprint_hash=current_bp_hash,
        )
    except ValueError as exc:
        return _finish(ProposalPanelOutcome.PANEL_FAILED, str(exc))

    # Walk SCIENTIFIC → IMPLEMENTATION → RED_TEAM → INTEGRATION as
    # bookkeeping (all three valid results exist).
    for role in REVIEW_SEQUENCE:
        state_machine.transition_to(_NEXT_PHASE_OF[role])

    try:
        persist_cycle_artifacts(
            workspace=workspace,
            bundle=bundle,
            executions=executions,
            reused_roles=frozenset(),
        )
    except ArtifactConflictError as exc:
        return _finish(ProposalPanelOutcome.ARTIFACT_CONFLICT, str(exc))

    docket = build_panel_docket(bundle, source_pack_id=source_pack_id)
    docket_path = _panel_docket_path(workspace, iteration_number)
    try:
        _atomic_write_json(docket_path, docket.to_dict())
    except OSError as exc:
        return _finish(ProposalPanelOutcome.ARTIFACT_CONFLICT, f"docket write failed: {exc}")

    report.aggregate_bundle = bundle.to_dict()
    report.panel_docket = docket.to_dict()
    report.docket_path = str(docket_path)
    return _finish(ProposalPanelOutcome.READY_FOR_INTEGRATION)


#: Where each reviewer's successful walk-step lands (canonical walk order).
_NEXT_PHASE_OF: dict[ProposalRole, ProposalPhase] = {
    ProposalRole.SCIENTIFIC_REVIEWER: ProposalPhase.IMPLEMENTATION_REVIEW,
    ProposalRole.PROPOSAL_ENGINEER: ProposalPhase.RED_TEAM_REVIEW,
    ProposalRole.RED_TEAM_REVIEWER: ProposalPhase.INTEGRATION,
}


# ---------------------------------------------------------------------------
# ROUND 2 — the parallel consensus round
# ---------------------------------------------------------------------------
def _render_first_pass_outputs(
    results: Mapping[ProposalRole, ProposalReviewResult]
) -> str:
    """Deterministic rendering of the three first-pass results (no raw)."""
    lines: list[str] = []
    for role in REVIEW_SEQUENCE:
        result = results[role]
        lines.append(f"### {role.value} — verdict: {result.verdict.value}")
        if result.summary.strip():
            lines.append(f"summary: {result.summary.strip()}")
        for index, finding in enumerate(result.findings, start=1):
            lines.append(
                f"- finding {index} [{finding.severity.value}] "
                f"({finding.category}; section: {finding.section or 'n/a'}): "
                f"{finding.message}"
            )
        for index, patch in enumerate(result.proposed_patches, start=1):
            lines.append(
                f"- patch {index} target={patch.target_section}: "
                f"{patch.rationale}"
            )
        if result.unverified_claims:
            lines.append(
                "- unverified claims: " + "; ".join(result.unverified_claims)
            )
        lines.append("")
    return "\n".join(lines).strip()


def run_panel_consensus_round(
    *,
    workspace: Path,
    iteration_number: int,
    proposal_revision: str,
    proposal_hash: str,
    bundle: ProposalReviewBundle,
    docket: PanelDocket,
    source_snapshot: ReviewSourceSnapshot,
    consensus_drivers: Mapping[ProposalRole, BaseDriver],
    consensus_agent_configs: Optional[Mapping[ProposalRole, ProposalAgentConfig]] = None,
    timeout_s: Optional[float] = None,
    source_budget: SourceBudget | None = None,
    max_workers: int = 3,
) -> dict[str, Any]:
    """Run the parallel consensus round — returns the durable round summary.

    Persists ``panel_consensus.json`` (the deterministic matrix) and
    ``readiness.json`` (the advisory index) for the iteration.  Raises
    ``RuntimeError`` (fail closed, nothing persisted) when any consensus
    call fails, the master changed mid-round, or the matrix cannot be
    built.  NO state-machine interaction: this round is READ-ONLY by
    construction.
    """
    started = time.monotonic()
    workspace = Path(workspace)
    master_path = workspace / "03_PROPOSAL" / "MASTER_PROPOSAL.md"

    # Session 021B: the consensus round is READ-ONLY against BOTH documents.
    # Whenever the living Blueprint is present (dual mode), its EXACT frozen
    # bytes are fingerprinted BEFORE the three calls and re-verified after
    # the join beside the master — a mutation of either document fails the
    # round closed: no matrix, no readiness, no artifacts.
    from ..proposal.living_blueprint import CURRENT_BLUEPRINT_RELPATH

    current_bp_path = workspace.joinpath(*CURRENT_BLUEPRINT_RELPATH.split("/"))
    dual = current_bp_path.is_file()
    blueprint_hash_before = ""
    if dual:
        try:
            blueprint_hash_before = proposal_fingerprint(current_bp_path)
        except OSError as exc:
            raise RuntimeError(
                f"pre-consensus CURRENT_BLUEPRINT fingerprint failed: {exc}"
            ) from exc

    missing = [r.value for r in REVIEW_SEQUENCE if r not in consensus_drivers]
    if missing:
        raise RuntimeError(
            "consensus_drivers must provide all three evaluator roles "
            "(separate instances); missing: " + ", ".join(missing)
        )
    ids = [id(consensus_drivers[r]) for r in REVIEW_SEQUENCE]
    if len(set(ids)) != len(ids):
        raise RuntimeError(
            "each consensus role requires its OWN driver instance."
        )

    first_pass = {
        role: bundle.scientific_review
        if role is ProposalRole.SCIENTIFIC_REVIEWER
        else bundle.implementation_review
        if role is ProposalRole.PROPOSAL_ENGINEER
        else bundle.red_team_review
        for role in REVIEW_SEQUENCE
    }
    first_pass_text = _render_first_pass_outputs(first_pass)
    docket_text_rendered = docket_text(docket)

    def _packet(role: ProposalRole) -> PanelConsensusPacket:
        source_lines: list[str] = [
            "### 00_SOURCE_OF_TRUTH/MASTER_BLUEPRINT.md",
            source_snapshot.master_blueprint_text.strip() or "[UNAVAILABLE]",
            "### 00_SOURCE_OF_TRUTH/CURRENT_BLUEPRINT.md (LIVING project design)",
            source_snapshot.current_blueprint_text.strip() or "[UNAVAILABLE]",
            "### 01_OFFICIAL/APPLICATION_TEMPLATE.md",
            source_snapshot.application_template_text.strip() or "[UNAVAILABLE]",
        ]
        for name, text in source_snapshot.official_documents:
            source_lines.append(f"### 01_OFFICIAL/NORMALIZED/{name}.md")
            source_lines.append(text.strip() or "[EMPTY]")
        if source_snapshot.official_requirements_available:
            source_lines.append("### 01_OFFICIAL/OFFICIAL_REQUIREMENTS.md")
            source_lines.append(
                source_snapshot.official_requirements_text.strip() or "[EMPTY]"
            )
        return build_panel_consensus_packet(
            PanelConsensusInputs(
                role=role,
                iteration_number=iteration_number,
                proposal_hash=proposal_hash,
                proposal_text=source_snapshot.master_proposal_text,
                first_pass_outputs_text=first_pass_text,
                panel_docket_text=docket_text_rendered,
                source_snapshot_text="\n\n".join(source_lines),
                proposal_revision=str(proposal_revision),
            )
        )

    packets = {role: _packet(role) for role in REVIEW_SEQUENCE}

    def _call(role: ProposalRole) -> PanelConsensusResult:
        packet = packets[role]
        agent_config = (
            consensus_agent_configs.get(role) if consensus_agent_configs else None
        )
        from ..drivers.base import SessionRequest
        from .review_executor import _resume_session_id, _session_request

        request = _session_request(
            packet,
            SessionPolicy.ALWAYS_NEW,
            agent_config=agent_config,
            proposal_workspace_path=workspace,
        )
        driver = consensus_drivers[role]
        resume_id = _resume_session_id(agent_config, driver)
        session = (
            driver.resume_session(resume_id, request)
            if resume_id
            else driver.start_session(request)
        )
        handle = driver.send_prompt(session, packet.prompt_text)
        prompt_result = driver.wait_for_completion(handle, timeout_s)
        if not prompt_result.ok:
            raise RuntimeError(
                f"{role.value} consensus driver failed: "
                f"{prompt_result.error or 'no error reported'}"
            )
        if not prompt_result.text.strip():
            raise RuntimeError(f"{role.value} consensus returned an empty answer")
        return parse_panel_consensus(
            prompt_result.text,
            expected_role=role,
            expected_iteration=iteration_number,
            expected_proposal_hash=proposal_hash,
        )

    workers = max(1, min(int(max_workers), 3))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {role: pool.submit(_call, role) for role in REVIEW_SEQUENCE}
        results: dict[ProposalRole, PanelConsensusResult] = {}
        errors: list[str] = []
        for role, future in futures.items():
            try:
                results[role] = future.result()
            except Exception as exc:  # noqa: BLE001 - collected per role
                errors.append(f"{role.value}: {type(exc).__name__}: {exc}")
    if errors:
        raise RuntimeError(
            "consensus round failed for: " + "; ".join(errors)
        )

    # Master must be unchanged by the consensus round (read-only contract).
    try:
        current_hash = proposal_fingerprint(master_path)
    except OSError as exc:
        raise RuntimeError(f"post-consensus fingerprint failed: {exc}") from exc
    if current_hash != proposal_hash:
        raise RuntimeError(
            f"MASTER_PROPOSAL changed during the consensus round "
            f"({proposal_hash} -> {current_hash}); refusing to build a "
            "matrix over a mixed revision."
        )
    # Session 021B: the LIVING Blueprint is equally read-only — the frozen
    # bytes fingerprinted before the calls must have survived verbatim.
    if dual:
        try:
            blueprint_hash_after = proposal_fingerprint(current_bp_path)
        except OSError as exc:
            raise RuntimeError(
                f"post-consensus CURRENT_BLUEPRINT fingerprint failed: {exc}"
            ) from exc
        if blueprint_hash_after != blueprint_hash_before:
            raise RuntimeError(
                f"CURRENT_BLUEPRINT changed during the consensus round "
                f"({blueprint_hash_before} -> {blueprint_hash_after}); the "
                "consensus round is READ-ONLY against BOTH documents and "
                "nothing is accepted over a mutated living Blueprint."
            )

    matrix = build_consensus_matrix(
        iteration_number=iteration_number,
        proposal_hash=proposal_hash,
        proposal_revision=str(proposal_revision),
        source_pack_id=docket.source_pack_id,
        consensus_results=results,
        docket=docket,
    )

    assessments = [results[role].readiness_assessment for role in REVIEW_SEQUENCE]
    assessments = [a for a in assessments if a.criteria]
    readiness: Optional[ReadinessResult] = None
    if assessments:
        unresolved_critical = sum(
            1
            for row in matrix.rows
            if row.unresolved
            and any(
                item.item_id == row.item_id
                and item.severity == "critical"
                for item in docket.items
            )
        )
        unresolved_high = sum(
            1
            for row in matrix.rows
            if row.unresolved
            and any(
                item.item_id == row.item_id and item.severity == "high"
                for item in docket.items
            )
        )
        unverified = sum(
            len(bundle.unverified_claims)
            for _ in (None,)
        )
        readiness = compute_readiness(
            ReadinessInputs(
                assessments=assessments,
                unresolved_critical_count=unresolved_critical,
                unresolved_high_count=unresolved_high,
                unverified_claim_count=unverified,
                missing_official_source=(
                    not source_snapshot.application_template_text.strip()
                    or not source_snapshot.official_requirements_available
                ),
            )
        )

    consensus_payload = matrix.to_dict()
    consensus_payload["consensus_results"] = {
        role.value: results[role].to_dict() for role in REVIEW_SEQUENCE
    }
    _atomic_write_json(
        _panel_consensus_path(workspace, iteration_number), consensus_payload
    )
    if readiness is not None:
        _atomic_write_json(
            _panel_readiness_path(workspace, iteration_number),
            readiness.to_dict(),
        )

    return {
        "matrix": matrix.to_dict(),
        "readiness": readiness.to_dict() if readiness else None,
        "participating_roles": [r.value for r in REVIEW_SEQUENCE],
        "duration_s": round(time.monotonic() - started, 6),
    }
