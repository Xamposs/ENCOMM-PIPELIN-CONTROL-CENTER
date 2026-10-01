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
from .fingerprint import (
    PROPOSAL_HASH_ALGORITHM,
    ProposalFingerprintError,
    proposal_fingerprint,
)
from .models import (
    ProposalAgentConfig,
    ProposalFinding,
    ProposalHardGateResult,
    ProposalIterationRecord,
    ProposalPatch,
    ProposalReviewResult,
)
from .review_packet import (
    PROPOSAL_REVIEW_ENVELOPE_END,
    PROPOSAL_REVIEW_ENVELOPE_START,
    ProposalReviewInputs,
    ProposalReviewPacket,
    build_review_packet,
)
from .review_parser import (
    ProposalReviewParseError,
    parse_proposal_review,
)
from .source_snapshot import (
    ReviewSourceSnapshot,
    SourceSnapshotError,
    load_review_snapshot,
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
    "PROPOSAL_HASH_ALGORITHM",
    "PROPOSAL_REVIEW_ENVELOPE_END",
    "PROPOSAL_REVIEW_ENVELOPE_START",
    "PROPOSAL_REVIEW_VERDICTS",
    "PROPOSAL_TRANSITIONS",
    "PROPOSAL_WORKSPACE_DIRS",
    "ProposalAgentConfig",
    "ProposalFinding",
    "ProposalFindingSeverity",
    "ProposalFingerprintError",
    "ProposalHardGateResult",
    "ProposalHardGateStatus",
    "ProposalIterationRecord",
    "ProposalPatch",
    "ProposalPhase",
    "ProposalReviewInputs",
    "ProposalReviewPacket",
    "ProposalReviewParseError",
    "ProposalReviewResult",
    "ProposalReviewVerdict",
    "ProposalRole",
    "ProposalStateMachine",
    "ProposalWorkspace",
    "ReviewSourceSnapshot",
    "SourceSnapshotError",
    "build_review_packet",
    "load_review_snapshot",
    "parse_proposal_review",
    "proposal_fingerprint",
    "validate_proposal_graph",
    "workspace_paths",
]
