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
from .hard_gate_validators import GATE_VALIDATORS
from .hard_gates import (
    EVIDENCE_DOCUMENT_FILENAME,
    HARD_GATE_EVIDENCE_SCHEMA,
    HardGateContext,
    HardGateDisposition,
    HardGateEngineError,
    HardGateEvaluation,
    HardGateEvidenceDocument,
    HardGateEvidenceError,
    HardGateFailureClass,
    HardGateRunResult,
    build_gate_registry,
    evaluate_hard_gates,
    load_hard_gate_evidence,
    validate_gate_registry,
)
from .integration_models import (
    INTEGRATION_ITEM_ACTIONS,
    ProposalIntegrationItem,
    ProposalIntegrationResult,
)
from .integration_packet import (
    PROPOSAL_INTEGRATION_ENVELOPE_END,
    PROPOSAL_INTEGRATION_ENVELOPE_START,
    ProposalIntegrationInputs,
    ProposalIntegrationPacket,
    build_integration_packet,
)
from .integration_parser import (
    ProposalIntegrationParseError,
    parse_proposal_integration,
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
from .review_aggregation import (
    INTEGRATION_BRIEF_SCHEMA,
    REVIEWER_ORDER,
    SEVERITY_ORDER,
    AggregatedFinding,
    AggregatedPatch,
    ProposalCycleVerdict,
    ProposalReviewBundle,
    aggregate_reviews,
    build_integration_brief,
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
    "AggregatedFinding",
    "AggregatedPatch",
    "EVIDENCE_DOCUMENT_FILENAME",
    "GATE_VALIDATORS",
    "HARD_GATE_EVIDENCE_SCHEMA",
    "HARD_GATE_IDS",
    "HARD_GATE_IDS_TUPLE",
    "HARD_GATE_STATUS_VALUES",
    "INTEGRATION_BRIEF_SCHEMA",
    "INTEGRATION_ITEM_ACTIONS",
    "InvalidProposalTransitionError",
    "MASTER_PROPOSAL_RELPATH",
    "PROPOSAL_HASH_ALGORITHM",
    "PROPOSAL_INTEGRATION_ENVELOPE_END",
    "PROPOSAL_INTEGRATION_ENVELOPE_START",
    "PROPOSAL_REVIEW_ENVELOPE_END",
    "PROPOSAL_REVIEW_ENVELOPE_START",
    "PROPOSAL_REVIEW_VERDICTS",
    "PROPOSAL_TRANSITIONS",
    "PROPOSAL_WORKSPACE_DIRS",
    "ProposalAgentConfig",
    "ProposalCycleVerdict",
    "ProposalFinding",
    "ProposalFindingSeverity",
    "ProposalFingerprintError",
    "ProposalHardGateResult",
    "ProposalHardGateStatus",
    "HardGateContext",
    "HardGateDisposition",
    "HardGateEngineError",
    "HardGateEvaluation",
    "HardGateEvidenceDocument",
    "HardGateEvidenceError",
    "HardGateFailureClass",
    "HardGateRunResult",
    "ProposalIntegrationInputs",
    "ProposalIntegrationItem",
    "ProposalIntegrationPacket",
    "ProposalIntegrationParseError",
    "ProposalIntegrationResult",
    "ProposalIterationRecord",
    "ProposalPatch",
    "ProposalPhase",
    "ProposalReviewBundle",
    "ProposalReviewInputs",
    "ProposalReviewPacket",
    "ProposalReviewParseError",
    "ProposalReviewResult",
    "ProposalReviewVerdict",
    "ProposalRole",
    "ProposalStateMachine",
    "ProposalWorkspace",
    "REVIEWER_ORDER",
    "ReviewSourceSnapshot",
    "SEVERITY_ORDER",
    "SourceSnapshotError",
    "aggregate_reviews",
    "build_gate_registry",
    "build_integration_packet",
    "build_integration_brief",
    "build_review_packet",
    "load_review_snapshot",
    "evaluate_hard_gates",
    "load_hard_gate_evidence",
    "parse_proposal_integration",
    "parse_proposal_review",
    "proposal_fingerprint",
    "validate_gate_registry",
    "validate_proposal_graph",
    "workspace_paths",
]
