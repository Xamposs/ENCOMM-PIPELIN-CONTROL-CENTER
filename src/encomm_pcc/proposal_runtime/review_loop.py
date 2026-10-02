"""Deterministic three-reviewer proposal review cycle — runtime orchestration.

Executes ONE complete review iteration over the EXACT master-proposal
revision frozen at cycle start::

    SOURCE_VALIDATION
        → SCIENTIFIC_REVIEWER
        → PROPOSAL_ENGINEER
        → RED_TEAM_REVIEWER
        → deterministic aggregation
        → integration brief
        → INTEGRATION          (final state; never advanced past)

Sequence and failure semantics (fail closed, STRICTLY sequential — no
concurrency in Session 014):

1. SOURCE_VALIDATION (IDLE enters it; an already-validated cycle may start
   at SOURCE_VALIDATION): workspace exists, MASTER_PROPOSAL exists, is
   non-empty and readable, the canonical fingerprint computes, the bounded
   snapshot loads, the snapshot text matches the fingerprinted bytes, and
   ``iteration_number`` is a positive integer.  Missing/empty/unreadable
   master ⇒ the cycle fails BEFORE any reviewer driver is touched.
2. Version freeze: ``06_VERSIONS/iteration_NNN_pre_review.md`` + JSON
   sidecar hold the exact reviewed bytes (written from the same read that
   produced the cycle hash).
3. For each reviewer, in canonical order: verify the live master hash
   equals the cycle hash (a change BETWEEN reviewers ⇒ STALE_PROPOSAL, the
   next reviewer is never launched, nothing is aggregated), build the
   packet over the SAME frozen snapshot inputs, and call the existing
   ``run_review()``.  Operational failure (driver, parse, mutation) stops
   the cycle immediately; a BLOCKED verdict is a VALID review that stops
   the sequence through the state machine's explicit BLOCKED edge.
4. Aggregation runs ONLY over three same-revision results (hash re-verified
   before aggregating); artifacts + integration brief are persisted
   afterwards; the state machine finishes exactly at INTEGRATION.

The three reviewers of one iteration stay INDEPENDENT: every reviewer
packet is built over the same frozen snapshot inputs, and earlier
reviewers' raw outputs are never injected into a later reviewer's
proposal-source snapshot.  ``previous_findings`` from EARLIER iterations
may be supplied explicitly by the caller through the packet field.

Reviewers are injected as a mapping ``ProposalRole -> BaseDriver`` — no
engine, provider or model name is hardcoded anywhere in this module.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional

from ..drivers.base import BaseDriver
from ..domain.enums import SessionPolicy
from ..proposal.enums import ProposalPhase, ProposalRole, ProposalReviewVerdict
from ..proposal.fingerprint import proposal_fingerprint
from ..proposal.models import ProposalReviewResult
from ..proposal.review_aggregation import (
    ProposalReviewBundle,
    aggregate_reviews,
)
from ..proposal.review_packet import (
    ProposalReviewInputs,
    ProposalReviewPacket,
    build_review_packet,
)
from ..proposal.source_snapshot import (
    ReviewSourceSnapshot,
    load_review_snapshot,
)
from ..proposal.state_machine import (
    REVIEW_ITERATION_ENTRY,
    ProposalStateMachine,
)
from ..proposal.workspace import MASTER_PROPOSAL_RELPATH
from .review_artifacts import (
    ArtifactConflictError,
    ReviewArtifactWriter,
    persist_cycle_artifacts,
)
from .review_executor import (
    ProposalReviewGuardError,
    ProposalReviewOutcome,
    REVIEW_PHASES,
    run_review,
)
from .version_freeze import (
    VersionFreezeError,
    freeze_pre_review_version,
)

__all__ = [
    "REVIEW_SEQUENCE",
    "ArtifactResumeError",
    "ProposalReviewCycleOutcome",
    "ProposalReviewCycleReport",
    "SourceValidationError",
    "run_review_cycle",
]

#: Canonical strictly-sequential reviewer order of one review cycle.
REVIEW_SEQUENCE: tuple[ProposalRole, ...] = (
    ProposalRole.SCIENTIFIC_REVIEWER,
    ProposalRole.PROPOSAL_ENGINEER,
    ProposalRole.RED_TEAM_REVIEWER,
)


class ProposalReviewCycleOutcome(str, Enum):
    """Terminal outcome of ONE review cycle."""

    READY_FOR_INTEGRATION = "READY_FOR_INTEGRATION"
    BLOCKED = "BLOCKED"                        # a reviewer returned BLOCKED
    REVIEW_FAILED = "REVIEW_FAILED"            # operational failure (driver/parse/guard)
    SOURCE_VALIDATION_FAILED = "SOURCE_VALIDATION_FAILED"
    STALE_PROPOSAL = "STALE_PROPOSAL"          # master changed between reviewers
    ARTIFACT_CONFLICT = "ARTIFACT_CONFLICT"    # durable evidence conflicts

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


class SourceValidationError(RuntimeError):
    """The proposal workspace failed source validation (fail before drivers)."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"[{reason}] {message}")


class ArtifactResumeError(RuntimeError):
    """Resume-position/artifact mismatch (never a silent clobber)."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"[{reason}] {message}")


@dataclass(slots=True)
class ProposalReviewCycleReport:
    """JSON-friendly operational report of ONE review cycle."""

    outcome: ProposalReviewCycleOutcome
    iteration_number: int
    proposal_hash: str = ""
    proposal_revision: str = ""
    state_before: ProposalPhase = ProposalPhase.IDLE
    state_after: ProposalPhase = ProposalPhase.IDLE
    completed_roles: list[str] = field(default_factory=list)
    execution_reports: dict[str, Any] = field(default_factory=dict)
    aggregate_bundle: Optional[dict[str, Any]] = None
    integration_brief_path: str = ""
    frozen_version_path: str = ""
    error: str = ""
    failed_role: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "iteration_number": self.iteration_number,
            "proposal_hash": self.proposal_hash,
            "proposal_revision": self.proposal_revision,
            "state_before": self.state_before.value,
            "state_after": self.state_after.value,
            "completed_roles": list(self.completed_roles),
            "execution_reports": dict(self.execution_reports),
            "aggregate_bundle": self.aggregate_bundle,
            "integration_brief_path": self.integration_brief_path,
            "frozen_version_path": self.frozen_version_path,
            "error": self.error,
            "failed_role": self.failed_role,
        }


# ---------------------------------------------------------------------------
# source validation
# ---------------------------------------------------------------------------
def _validate_sources(
    *,
    workspace: Path,
    master_proposal_path: Path,
    iteration_number: int,
) -> tuple[str, ReviewSourceSnapshot]:
    """Validate the review-cycle sources; return ``(cycle_hash, snapshot)``.

    Every check runs BEFORE any reviewer driver exists in the conversation.
    Empty-or-missing source-of-truth files are legitimate in an early
    workspace (the packet renders them as explicitly unavailable — never
    invented); only the MASTER PROPOSAL is required.
    """
    if not isinstance(iteration_number, int) or isinstance(
        iteration_number, bool
    ) or iteration_number < 1:
        raise SourceValidationError(
            "invalid_iteration_number",
            f"iteration_number must be a positive integer, got {iteration_number!r}.",
        )
    if not workspace.is_dir():
        raise SourceValidationError(
            "workspace_missing",
            f"proposal workspace does not exist: {workspace}",
        )
    if not master_proposal_path.exists():
        raise SourceValidationError(
            "master_proposal_missing",
            f"{MASTER_PROPOSAL_RELPATH} is missing; a review cycle without "
            "the master proposal is impossible.",
        )
    if not master_proposal_path.is_file():
        raise SourceValidationError(
            "master_proposal_not_a_file",
            f"{MASTER_PROPOSAL_RELPATH} is not a regular file.",
        )
    try:
        raw_bytes = master_proposal_path.read_bytes()
    except OSError as exc:
        raise SourceValidationError(
            "master_proposal_unreadable",
            f"{MASTER_PROPOSAL_RELPATH} cannot be read: {exc}",
        ) from exc
    if not raw_bytes.strip():
        raise SourceValidationError(
            "master_proposal_empty",
            f"{MASTER_PROPOSAL_RELPATH} is empty; a review cycle refuses to "
            "launch against an empty proposal.",
        )
    try:
        cycle_hash = proposal_fingerprint(master_proposal_path)
    except OSError as exc:
        raise SourceValidationError(
            "fingerprint_failed", str(exc)
        ) from exc
    try:
        snapshot = load_review_snapshot(workspace)
    except Exception as exc:  # SourceSnapshotError or an unreadable source
        raise SourceValidationError(
            "snapshot_failed", f"bounded snapshot could not be loaded: {exc}"
        ) from exc
    # The text that feeds the packets must be the fingerprinted revision.
    if snapshot.master_proposal_text.encode("utf-8") != raw_bytes:
        raise SourceValidationError(
            "snapshot_hash_mismatch",
            "snapshot master-proposal text does not match the fingerprinted "
            "file bytes; refusing to review a mixed revision.",
        )
    return cycle_hash, snapshot


# ---------------------------------------------------------------------------
# packet builder over the FROZEN snapshot inputs
# ---------------------------------------------------------------------------
def _build_packet(
    role: ProposalRole,
    *,
    snapshot: ReviewSourceSnapshot,
    iteration_number: int,
    proposal_revision: str,
    proposal_hash: str,
    previous_findings: tuple[str, ...],
    extra_instructions: str,
) -> ProposalReviewPacket:
    """Deterministic reviewer packet over the SAME frozen inputs.

    No earlier reviewer's output is injected here: all three packets of one
    iteration see identical proposal/source/iteration fields, so reviewer
    independence is structural.
    """
    inputs = ProposalReviewInputs(
        proposal_text=snapshot.master_proposal_text,
        iteration_number=iteration_number,
        proposal_revision=proposal_revision,
        proposal_hash=proposal_hash,
        master_blueprint_text=snapshot.master_blueprint_text,
        project_facts_text=snapshot.project_facts_text,
        team_text=snapshot.team_text,
        architecture_text=snapshot.architecture_text,
        terminology_text=snapshot.terminology_text,
        official_requirements_text=snapshot.official_requirements_text,
        official_requirements_available=snapshot.official_requirements_available,
        previous_findings=tuple(previous_findings),
        extra_instructions=extra_instructions,
    )
    return build_review_packet(role, inputs)


# ---------------------------------------------------------------------------
# the cycle
# ---------------------------------------------------------------------------
def run_review_cycle(
    *,
    workspace: Path,
    state_machine: ProposalStateMachine,
    iteration_number: int,
    proposal_revision: str,
    reviewer_drivers: Mapping[ProposalRole, BaseDriver],
    reviewer_session_policies: Optional[Mapping[ProposalRole, SessionPolicy]] = None,
    timeout_s: Optional[float] = None,
    previous_findings: tuple[str, ...] = (),
    extra_instructions: str = "",
) -> ProposalReviewCycleReport:
    """Run ONE deterministic three-reviewer cycle — fail closed throughout.

    Enters from ``IDLE`` (transitioning through SOURCE_VALIDATION) or from an
    already-validated ``SOURCE_VALIDATION`` position.  Finishes exactly at
    ``INTEGRATION`` on success; no failure path ever reaches INTEGRATION.

    ``reviewer_drivers`` MUST provide a driver for each of the three reviewer
    roles (engine choice is runtime configuration; nothing is hardcoded).
    ``reviewer_session_policies`` optionally overrides the default
    ``ALWAYS_NEW`` policy per reviewer role.
    """
    started = time.monotonic()
    workspace = Path(workspace)
    master_proposal_path = workspace / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
    state_before = state_machine.phase
    report = ProposalReviewCycleReport(
        outcome=ProposalReviewCycleOutcome.REVIEW_FAILED,
        iteration_number=iteration_number,
        proposal_revision=str(proposal_revision),
        state_before=state_before,
    )

    missing_roles = [role.value for role in REVIEW_SEQUENCE if role not in reviewer_drivers]
    if missing_roles:
        report.outcome = ProposalReviewCycleOutcome.REVIEW_FAILED
        report.error = (
            "reviewer_drivers must provide all three reviewer roles; missing: "
            + ", ".join(missing_roles)
        )
        return _finish(report, state_machine, started)

    # -- 1. position + source validation ----------------------------------
    RESUME_ENTRY_PHASES = (
        ProposalPhase.IDLE,
        ProposalPhase.SOURCE_VALIDATION,
        ProposalPhase.SCIENTIFIC_REVIEW,
        ProposalPhase.IMPLEMENTATION_REVIEW,
        ProposalPhase.RED_TEAM_REVIEW,
        # A fresh review iteration after integration: REVISION_REQUIRED walks
        # its explicit D-055 edge to SCIENTIFIC_REVIEW (REVIEW_ITERATION_ENTRY)
        # before the same-revision validation below.
        ProposalPhase.REVISION_REQUIRED,
    )
    if state_before is ProposalPhase.IDLE:
        state_machine.transition_to(ProposalPhase.SOURCE_VALIDATION)
    elif state_before is ProposalPhase.REVISION_REQUIRED:
        state_machine.transition_to(REVIEW_ITERATION_ENTRY)
    elif state_before not in RESUME_ENTRY_PHASES:
        report.outcome = ProposalReviewCycleOutcome.REVIEW_FAILED
        report.error = (
            "run_review_cycle must start from IDLE, SOURCE_VALIDATION or a "
            f"review phase with consistent artifacts; the state machine is "
            f"at {state_before.value}."
        )
        return _finish(report, state_machine, started)

    try:
        cycle_hash, snapshot = _validate_sources(
            workspace=workspace,
            master_proposal_path=master_proposal_path,
            iteration_number=iteration_number,
        )
    except SourceValidationError as exc:
        _fail_closed(report, state_machine, exc, ProposalReviewCycleOutcome.SOURCE_VALIDATION_FAILED)
        return _finish(report, state_machine, started)

    report.proposal_hash = cycle_hash

    # -- 2. version freeze (exact reviewed bytes) --------------------------
    try:
        frozen_path, _frozen_hash = freeze_pre_review_version(
            workspace=workspace,
            master_proposal_path=master_proposal_path,
            iteration_number=iteration_number,
            proposal_revision=proposal_revision,
            expected_hash=cycle_hash,
        )
    except (VersionFreezeError, OSError) as exc:
        report.outcome = ProposalReviewCycleOutcome.ARTIFACT_CONFLICT
        report.error = f"version freeze failed: {exc}"
        report.state_after = state_machine.phase
        return _finish(report, state_machine, started)
    report.frozen_version_path = str(frozen_path)

    # Validation succeeded: walk the explicit SOURCE_VALIDATION →
    # SCIENTIFIC_REVIEW edge (required before the first reviewer; the
    # executor's phase/role guard demands the machine sits exactly at the
    # reviewer's phase).  A cycle already at SCIENTIFIC_REVIEW (re-entry) is
    # left untouched.
    if state_machine.phase is ProposalPhase.SOURCE_VALIDATION:
        state_machine.transition_to(ProposalPhase.SCIENTIFIC_REVIEW)

    # Resume-safe artifact writer bound to THIS cycle identity.
    writer = ReviewArtifactWriter(
        workspace=workspace,
        iteration_number=iteration_number,
        proposal_revision=str(proposal_revision),
        proposal_hash=cycle_hash,
    )

    # -- 3. the three reviewers (strictly sequential, same revision) -------
    results: dict[ProposalRole, ProposalReviewResult] = {}
    reused_roles: set[ProposalRole] = set()
    executions: dict[ProposalRole, Any] = {}

    for index, role in enumerate(REVIEW_SEQUENCE):
        machine_position = REVIEW_SEQUENCE.index(_current_role(state_machine))
        # Hash guard BEFORE each reviewer: the exact frozen revision must
        # still be on disk; a change between reviewers is never aggregated.
        try:
            current_hash = proposal_fingerprint(master_proposal_path)
        except OSError as exc:
            _fail_closed(
                report, state_machine, exc,
                ProposalReviewCycleOutcome.STALE_PROPOSAL,
                failed_role=role.value,
            )
            return _finish(report, state_machine, started)
        if current_hash != cycle_hash:
            error = (
                f"MASTER_PROPOSAL changed between reviewers (expected "
                f"{cycle_hash}, found {current_hash}); the remaining "
                "reviewers were NOT launched and nothing was aggregated."
            )
            _fail_closed(
                report, state_machine, RuntimeError(error),
                ProposalReviewCycleOutcome.STALE_PROPOSAL,
                failed_role=role.value,
            )
            return _finish(report, state_machine, started)

        # Safe resume: an existing artifact must be consistent with THIS
        # cycle's identity and the machine position (load raises conflict).
        try:
            existing_result = writer.load_existing_review_result(role)
        except ArtifactConflictError as exc:
            _fail_closed(
                report, state_machine, exc,
                ProposalReviewCycleOutcome.ARTIFACT_CONFLICT,
                failed_role=role.value,
            )
            return _finish(report, state_machine, started)

        if index < machine_position:
            # Completed in a previous process: the durable artifact is the
            # REQUIRED evidence — a completed position without its artifact
            # is an inconsistent resume, never silently re-run.
            if existing_result is None:
                _fail_closed(
                    report, state_machine,
                    ArtifactResumeError(
                        "missing_resume_artifact",
                        f"state machine is at "
                        f"{_current_role(state_machine).value} but the "
                        f"completed {role.value} artifact is missing.",
                    ),
                    ProposalReviewCycleOutcome.ARTIFACT_CONFLICT,
                    failed_role=role.value,
                )
                return _finish(report, state_machine, started)
            results[role] = existing_result
            reused_roles.add(role)
            report.completed_roles.append(role.value)
            continue

        if existing_result is not None:
            # An artifact exists for the CURRENT position: the machine never
            # advanced past it — an ambiguous/crashed state.  Fail closed.
            _fail_closed(
                report, state_machine,
                ArtifactResumeError(
                    "resume_position_mismatch",
                    f"existing {role.value} artifact conflicts with the "
                    f"state-machine position {state_machine.phase.value}.",
                ),
                ProposalReviewCycleOutcome.ARTIFACT_CONFLICT,
                failed_role=role.value,
            )
            return _finish(report, state_machine, started)

        packet = _build_packet(
            role,
            snapshot=snapshot,
            iteration_number=iteration_number,
            proposal_revision=str(proposal_revision),
            proposal_hash=cycle_hash,
            previous_findings=previous_findings,
            extra_instructions=extra_instructions,
        )
        policy = SessionPolicy.ALWAYS_NEW
        if reviewer_session_policies and role in reviewer_session_policies:
            policy = reviewer_session_policies[role]
        try:
            execution = run_review(
                packet=packet,
                driver=reviewer_drivers[role],
                state_machine=state_machine,
                master_proposal_path=master_proposal_path,
                session_policy=policy,
                timeout_s=timeout_s,
            )
        except ProposalReviewGuardError as exc:
            # The executor's pre-driver fingerprint failed: the frozen
            # revision vanished/became unreadable — a revision-integrity
            # failure, never an aggregation input.
            _fail_closed(
                report, state_machine, exc,
                ProposalReviewCycleOutcome.STALE_PROPOSAL,
                failed_role=role.value,
            )
            return _finish(report, state_machine, started)
        executions[role] = execution
        report.execution_reports[role.value] = execution.to_dict()

        if not execution.ok:
            outcome = (
                ProposalReviewCycleOutcome.STALE_PROPOSAL
                if execution.outcome is ProposalReviewOutcome.MUTATION_DETECTED
                else ProposalReviewCycleOutcome.REVIEW_FAILED
            )
            _fail_closed(
                report, state_machine,
                RuntimeError(execution.error or execution.outcome.value),
                outcome,
                failed_role=role.value,
            )
            return _finish(report, state_machine, started)

        result = execution.result
        if result is None:  # defensive: COMPLETED always carries a result
            _fail_closed(
                report, state_machine,
                RuntimeError("completed execution carries no parsed result"),
                ProposalReviewCycleOutcome.REVIEW_FAILED,
                failed_role=role.value,
            )
            return _finish(report, state_machine, started)

        if result.verdict is ProposalReviewVerdict.BLOCKED:
            # A VALID review that stops the cycle: the executor already took
            # the explicit BLOCKED edge; INTEGRATION is never reached.  The
            # completed review's artifact is still persisted as evidence —
            # and a conflict while persisting it is FAIL-CLOSED (014A): it
            # is never swallowed, the cycle reports ARTIFACT_CONFLICT with
            # the real error, launches no later reviewer, and never
            # overwrites/repairs the conflicting artifact.  The machine
            # legitimately stays at BLOCKED (run_review took the D-055 edge
            # BEFORE persistence; that transition is not reversed) — the
            # report carries both facts.
            try:
                writer.write_review_artifact(
                    role, result=result, execution=execution
                )
            except ArtifactConflictError as exc:
                _fail_closed(
                    report, state_machine, exc,
                    ProposalReviewCycleOutcome.ARTIFACT_CONFLICT,
                    failed_role=role.value,
                )
                return _finish(report, state_machine, started)
            results[role] = result
            report.completed_roles.append(role.value)
            report.outcome = ProposalReviewCycleOutcome.BLOCKED
            report.error = (
                f"{role.value} returned BLOCKED; the review sequence stopped "
                "before the remaining reviewers."
            )
            return _finish(report, state_machine, started)

        results[role] = result
        report.completed_roles.append(role.value)
        # Durable per-review evidence, written AS the reviewer completes so a
        # later resume at the next phase finds it (safe-resume contract).
        try:
            writer.write_review_artifact(role, result=result, execution=execution)
        except ArtifactConflictError as exc:
            _fail_closed(
                report, state_machine, exc,
                ProposalReviewCycleOutcome.ARTIFACT_CONFLICT,
                failed_role=role.value,
            )
            return _finish(report, state_machine, started)

    # -- 4. hash re-verification before aggregation ------------------------
    try:
        final_hash = proposal_fingerprint(master_proposal_path)
    except OSError as exc:
        _fail_closed(
            report, state_machine, exc,
            ProposalReviewCycleOutcome.STALE_PROPOSAL,
        )
        return _finish(report, state_machine, started)
    if final_hash != cycle_hash:
        _fail_closed(
            report, state_machine,
            RuntimeError(
                f"MASTER_PROPOSAL changed after the final reviewer (expected "
                f"{cycle_hash}, found {final_hash}); refusing to aggregate "
                "mixed revisions."
            ),
            ProposalReviewCycleOutcome.STALE_PROPOSAL,
        )
        return _finish(report, state_machine, started)

    # -- 5. deterministic aggregation + durable artifacts ------------------
    try:
        bundle = aggregate_reviews(
            iteration_number=iteration_number,
            proposal_revision=str(proposal_revision),
            proposal_hash=cycle_hash,
            scientific_review=results[ProposalRole.SCIENTIFIC_REVIEWER],
            implementation_review=results[ProposalRole.PROPOSAL_ENGINEER],
            red_team_review=results[ProposalRole.RED_TEAM_REVIEWER],
        )
    except ValueError as exc:
        _fail_closed(
            report, state_machine, exc,
            ProposalReviewCycleOutcome.REVIEW_FAILED,
        )
        return _finish(report, state_machine, started)

    try:
        paths = persist_cycle_artifacts(
            workspace=workspace,
            bundle=bundle,
            executions=executions,
            reused_roles=frozenset(reused_roles),
        )
    except ArtifactConflictError as exc:
        _fail_closed(
            report, state_machine, exc,
            ProposalReviewCycleOutcome.ARTIFACT_CONFLICT,
        )
        return _finish(report, state_machine, started)

    report.aggregate_bundle = bundle.to_dict()
    report.integration_brief_path = str(paths["integration_brief"])
    report.outcome = ProposalReviewCycleOutcome.READY_FOR_INTEGRATION
    return _finish(report, state_machine, started)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _current_role(state_machine: ProposalStateMachine) -> ProposalRole:
    """Reviewer role demanded by the machine's CURRENT phase."""
    return REVIEW_PHASES[state_machine.phase]


def _fail_closed(
    report: ProposalReviewCycleReport,
    state_machine: ProposalStateMachine,
    exc: BaseException,
    outcome: ProposalReviewCycleOutcome,
    failed_role: str = "",
) -> None:
    """Record the failure.  The ONLY transition here is SOURCE_VALIDATION →
    FAILED for a failed validation (explicit graph edge); a failure during
    review NEVER transitions anywhere."""
    report.outcome = outcome
    report.error = str(exc)
    report.failed_role = failed_role
    if outcome is ProposalReviewCycleOutcome.SOURCE_VALIDATION_FAILED and (
        state_machine.phase is ProposalPhase.SOURCE_VALIDATION
    ):
        # Explicit D-055 edge out of SOURCE_VALIDATION; IDLE itself cannot
        # reach FAILED (the cycle already moved it to SOURCE_VALIDATION).
        state_machine.transition_to(ProposalPhase.FAILED)
    report.state_after = state_machine.phase


def _finish(
    report: ProposalReviewCycleReport,
    state_machine: ProposalStateMachine,
    started: float,
) -> ProposalReviewCycleReport:
    report.state_after = state_machine.phase
    return report
