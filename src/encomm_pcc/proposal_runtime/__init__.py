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

from .review_executor import (
    MAX_EXCERPT_CHARS,
    NEXT_REVIEW_PHASE,
    PHASE_FOR_REVIEWER,
    ProposalReviewExecutionReport,
    ProposalReviewGuardError,
    ProposalReviewOutcome,
    run_review,
)

__all__ = [
    "MAX_EXCERPT_CHARS",
    "NEXT_REVIEW_PHASE",
    "PHASE_FOR_REVIEWER",
    "ProposalReviewExecutionReport",
    "ProposalReviewGuardError",
    "ProposalReviewOutcome",
    "run_review",
]
