"""Proposal runtime bridge — the ONLY execution adapter layer for Proposal Mode.

Dependency direction (ADR, Session 013)::

    encomm_pcc.proposal_runtime
        ↓ imports
    encomm_pcc.proposal        (pure contracts)  +  encomm_pcc.drivers  (generic driver contract)

Nothing in ``encomm_pcc.proposal`` may import this package, and Coding Mode
packages must never import it either.  This package contains no provider
names, no CLI logic and no registry: it feeds a rendered
:class:`~encomm_pcc.proposal.ProposalReviewPacket` through the existing
``BaseDriver`` contract, parses the answer fail-closed, guards the master
proposal's immutability, and advances the proposal state machine only when
everything held.

Session 015 adds the REAL INTEGRATION: the ORCHESTRATOR integration
executor (packet -> driver -> strict parse -> mutation guard -> runtime-
owned atomic MASTER_PROPOSAL write), the post-integration version freeze,
the durable integration artifact, the review-freshness gate, the
``NEXT_ITERATION.json`` revision handoff and the one-iteration composition
helper.
"""

from .hard_gate_artifacts import (
    HARD_GATE_FEEDBACK_FILENAME,
    HARD_GATE_FEEDBACK_SCHEMA,
    HARD_GATES_ARTIFACT_SCHEMA,
    HARD_GATES_SNAPSHOT_FILENAME,
    HardGateArtifactConflictError,
    build_feedback_payload,
    build_run_artifact,
    update_latest_hard_gates_snapshot,
    write_hard_gate_feedback,
    write_iteration_hard_gates,
)
from .hard_gate_runner import (
    ProposalHardGateRunOutcome,
    ProposalHardGateRunReport,
    run_hard_gates,
)
from .integration_artifacts import (
    INTEGRATION_RESULT_FILENAME,
    INTEGRATION_RESULT_SCHEMA,
    write_integration_result,
)
from .integration_executor import (
    ProposalIntegrationExecutionReport,
    ProposalIntegrationOutcome,
    run_integration,
)
from .master_writer import (
    MASTER_PROPOSAL_FILENAME,
    MasterProposalWriteError,
    MasterProposalWriteReport,
    replace_master_proposal,
)
from .proposal_iteration import (
    ProposalIterationOutcome,
    ProposalIterationReport,
    run_iteration,
)
from .review_artifacts import (
    ArtifactConflictError,
    ReviewArtifactWriter,
    persist_cycle_artifacts,
)
from .review_executor import (
    MAX_EXCERPT_CHARS,
    NEXT_REVIEW_PHASE,
    PHASE_FOR_REVIEWER,
    ProposalReviewExecutionReport,
    ProposalReviewGuardError,
    ProposalReviewOutcome,
    REVIEW_PHASES,
    run_review,
)
from .review_freshness import (
    REVIEW_FRESHNESS_GATE_ID,
    ReviewFreshness,
    ReviewFreshnessError,
    evaluate_review_freshness,
)
from .review_loop import (
    REVIEW_SEQUENCE,
    ArtifactResumeError,
    ProposalReviewCycleOutcome,
    ProposalReviewCycleReport,
    SourceValidationError,
    run_review_cycle,
)
from .revision_handoff import (
    NEXT_ITERATION_FILENAME,
    REVISION_HANDOFF_SCHEMA,
    NextIterationHandoffError,
    build_next_iteration_payload,
    write_next_iteration_handoff,
)
from .version_freeze import (
    VERSION_FREEZE_MD_SUFFIX,
    VERSION_FREEZE_SIDECAR_SUFFIX,
    VersionFreezeError,
    format_iteration_name,
    freeze_pre_review_version,
)
from .version_freeze_post import (
    POST_INTEGRATION_MD_SUFFIX,
    POST_INTEGRATION_SIDECAR_SUFFIX,
    freeze_post_integration_version,
)
from .workspace_status import (
    PROPOSAL_STATUS_SCHEMA,
    ProposalWorkspaceStatus,
    latest_iteration_number,
    load_workspace_status,
)

__all__ = [
    "HARD_GATE_FEEDBACK_FILENAME",
    "HARD_GATE_FEEDBACK_SCHEMA",
    "HARD_GATES_ARTIFACT_SCHEMA",
    "HARD_GATES_SNAPSHOT_FILENAME",
    "INTEGRATION_RESULT_FILENAME",
    "INTEGRATION_RESULT_SCHEMA",
    "MASTER_PROPOSAL_FILENAME",
    "MAX_EXCERPT_CHARS",
    "NEXT_ITERATION_FILENAME",
    "NEXT_REVIEW_PHASE",
    "PHASE_FOR_REVIEWER",
    "PROPOSAL_STATUS_SCHEMA",
    "POST_INTEGRATION_MD_SUFFIX",
    "POST_INTEGRATION_SIDECAR_SUFFIX",
    "REVIEW_FRESHNESS_GATE_ID",
    "REVIEW_SEQUENCE",
    "REVIEW_PHASES",
    "REVISION_HANDOFF_SCHEMA",
    "VERSION_FREEZE_MD_SUFFIX",
    "VERSION_FREEZE_SIDECAR_SUFFIX",
    "ArtifactConflictError",
    "ArtifactResumeError",
    "HardGateArtifactConflictError",
    "MasterProposalWriteError",
    "MasterProposalWriteReport",
    "NextIterationHandoffError",
    "ProposalHardGateRunOutcome",
    "ProposalHardGateRunReport",
    "ProposalIntegrationExecutionReport",
    "ProposalIntegrationOutcome",
    "ProposalIterationOutcome",
    "ProposalIterationReport",
    "ProposalReviewCycleOutcome",
    "ProposalReviewCycleReport",
    "ProposalWorkspaceStatus",
    "ProposalReviewExecutionReport",
    "ProposalReviewGuardError",
    "ProposalReviewOutcome",
    "ReviewArtifactWriter",
    "ReviewFreshness",
    "ReviewFreshnessError",
    "SourceValidationError",
    "VersionFreezeError",
    "build_next_iteration_payload",
    "build_feedback_payload",
    "build_run_artifact",
    "evaluate_review_freshness",
    "format_iteration_name",
    "freeze_post_integration_version",
    "freeze_pre_review_version",
    "persist_cycle_artifacts",
    "replace_master_proposal",
    "run_hard_gates",
    "latest_iteration_number",
    "load_workspace_status",
    "run_integration",
    "run_iteration",
    "run_review",
    "run_review_cycle",
    "update_latest_hard_gates_snapshot",
    "write_integration_result",
    "write_hard_gate_feedback",
    "write_iteration_hard_gates",
    "write_next_iteration_handoff",
]
