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
"""

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
from .review_loop import (
    REVIEW_SEQUENCE,
    ArtifactResumeError,
    ProposalReviewCycleOutcome,
    ProposalReviewCycleReport,
    SourceValidationError,
    run_review_cycle,
)
from .version_freeze import (
    VERSION_FREEZE_MD_SUFFIX,
    VERSION_FREEZE_SIDECAR_SUFFIX,
    VersionFreezeError,
    format_iteration_name,
    freeze_pre_review_version,
)

__all__ = [
    "MAX_EXCERPT_CHARS",
    "NEXT_REVIEW_PHASE",
    "PHASE_FOR_REVIEWER",
    "REVIEW_SEQUENCE",
    "REVIEW_PHASES",
    "VERSION_FREEZE_MD_SUFFIX",
    "VERSION_FREEZE_SIDECAR_SUFFIX",
    "ArtifactConflictError",
    "ArtifactResumeError",
    "ProposalReviewCycleOutcome",
    "ProposalReviewCycleReport",
    "ProposalReviewExecutionReport",
    "ProposalReviewGuardError",
    "ProposalReviewOutcome",
    "ReviewArtifactWriter",
    "SourceValidationError",
    "VersionFreezeError",
    "format_iteration_name",
    "freeze_pre_review_version",
    "persist_cycle_artifacts",
    "run_review",
    "run_review_cycle",
]
