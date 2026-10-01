"""ENCOMM PCC — Proposal Mode (parallel domain, isolated from Coding Mode).

A self-contained parallel domain beside the Coding Mode packages.  It imports
NOTHING from ``encomm_pcc.core``, ``encomm_pcc.domain``, ``encomm_pcc.drivers``
or ``encomm_pcc.persistence``; the Coding Mode pipeline is untouched.
"""

from .enums import (
    HARD_GATE_IDS,
    HARD_GATE_IDS_TUPLE,
    HARD_GATE_STATUS_VALUES,
    PROPOSAL_REVIEW_VERDICTS,
    ProposalFindingSeverity,
    ProposalHardGateStatus,
    ProposalPhase,
    ProposalReviewVerdict,
    ProposalRole,
)
from .models import (
    ProposalAgentConfig,
    ProposalFinding,
    ProposalHardGateResult,
    ProposalIterationRecord,
    ProposalPatch,
    ProposalReviewResult,
)
from .state_machine import (
    PROPOSAL_TRANSITIONS,
    InvalidProposalTransitionError,
    ProposalStateMachine,
    validate_proposal_graph,
)
from .workspace import (
    MASTER_PROPOSAL_RELPATH,
    PROPOSAL_WORKSPACE_DIRS,
    ProposalWorkspace,
    workspace_paths,
)

__all__ = [
    "HARD_GATE_IDS",
    "HARD_GATE_IDS_TUPLE",
    "HARD_GATE_STATUS_VALUES",
    "InvalidProposalTransitionError",
    "MASTER_PROPOSAL_RELPATH",
    "PROPOSAL_REVIEW_VERDICTS",
    "PROPOSAL_TRANSITIONS",
    "PROPOSAL_WORKSPACE_DIRS",
    "ProposalAgentConfig",
    "ProposalFinding",
    "ProposalFindingSeverity",
    "ProposalHardGateResult",
    "ProposalHardGateStatus",
    "ProposalIterationRecord",
    "ProposalPatch",
    "ProposalPhase",
    "ProposalReviewResult",
    "ProposalReviewVerdict",
    "ProposalRole",
    "ProposalStateMachine",
    "ProposalWorkspace",
    "validate_proposal_graph",
    "workspace_paths",
]
