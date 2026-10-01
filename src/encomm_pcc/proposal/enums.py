"""Proposal Mode enums — parallel to the Coding Mode domain enums.

These are the Proposal Mode equivalents of ``encomm_pcc.domain.enums``.  They
share the same conventions (``str``-subclass enums so values round-trip
through JSON/SQLite/combo-box userData without adapter code) but are
deliberately SEPARATE types: Proposal Mode is an isolated parallel domain and
must not mutate, depend on or re-use Coding Mode semantics
(:class:`AgentRole`, :class:`PipelinePhase`).
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "HARD_GATE_IDS",
    "HARD_GATE_IDS_TUPLE",
    "HARD_GATE_STATUS_VALUES",
    "PROPOSAL_REVIEW_VERDICTS",
    "ProposalFindingSeverity",
    "ProposalHardGateStatus",
    "ProposalPhase",
    "ProposalReviewVerdict",
    "ProposalRole",
]


class ProposalRole(str, Enum):
    """Roles of the Proposal Mode review/integration loop.

    Engine independence mirrors the Coding Mode rule (D-003): a role is never
    bound to an engine or provider here.  The ORCHESTRATOR will later also act
    as the integration editor / final authority for the proposal document —
    there is no second independent "integrator AI"; integration authority is a
    duty of the ORCHESTRATOR role, not a separate role.
    """

    ORCHESTRATOR = "ORCHESTRATOR"
    SCIENTIFIC_REVIEWER = "SCIENTIFIC_REVIEWER"
    PROPOSAL_ENGINEER = "PROPOSAL_ENGINEER"
    RED_TEAM_REVIEWER = "RED_TEAM_REVIEWER"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


class ProposalPhase(str, Enum):
    """Proposal Mode lifecycle phases.

    A parallel domain contract: never shared with, derived from or mapped
    onto the Coding Mode :class:`PipelinePhase`.
    """

    IDLE = "IDLE"
    SOURCE_VALIDATION = "SOURCE_VALIDATION"
    SCIENTIFIC_REVIEW = "SCIENTIFIC_REVIEW"
    IMPLEMENTATION_REVIEW = "IMPLEMENTATION_REVIEW"
    RED_TEAM_REVIEW = "RED_TEAM_REVIEW"
    INTEGRATION = "INTEGRATION"
    HARD_GATE_VALIDATION = "HARD_GATE_VALIDATION"
    REVISION_REQUIRED = "REVISION_REQUIRED"
    COMPLETE = "COMPLETE"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


class ProposalReviewVerdict(str, Enum):
    """Exact verdict a proposal reviewer may return.

    Downstream consumers must accept no other value (fail closed, mirroring
    the Coding Mode verdict contract).
    """

    PASS = "PASS"
    NEEDS_REVISION = "NEEDS_REVISION"
    BLOCKED = "BLOCKED"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


class ProposalFindingSeverity(str, Enum):
    """Severity of a proposal review finding."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


class ProposalHardGateStatus(str, Enum):
    """Status of one hard-gate check (contract only — no validator exists yet)."""

    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    NOT_APPLICABLE = "NOT_APPLICABLE"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


#: The only accepted proposal review verdict strings (strict whitelist for
#: the future fail-closed parser; mirroring ``domain.audit.VERDICTS``).
PROPOSAL_REVIEW_VERDICTS: frozenset[str] = frozenset(
    v.value for v in ProposalReviewVerdict
)

#: Canonical, proposal-specific hard-gate identifiers (Section 5 contract).
#: These name the checks a future validator must implement; defining them
#: centrally keeps the identifiers unique and stable across sessions.  No
#: validator exists yet and none may fake a result.
HARD_GATE_IDS_TUPLE: tuple[str, ...] = (
    "MANDATORY_SECTIONS",
    "CHALLENGE_MAPPING",
    "SOURCE_OF_TRUTH_INTEGRITY",
    "UNVERIFIED_CLAIMS",
    "CITATION_VERIFICATION",
    "TERMINOLOGY_CONSISTENCY",
    "WP_TASK_CONSISTENCY",
    "WP_DELIVERABLE_CONSISTENCY",
    "WP_MILESTONE_CONSISTENCY",
    "PERSON_MONTH_CONSISTENCY",
    "BUDGET_CONSISTENCY",
    "SUBCONTRACTING_CORE_TASKS",
    "PAGE_LIMIT",
    "INTERNAL_CONTRADICTIONS",
)

#: Unique-by-construction canonical hard-gate id set.
HARD_GATE_IDS: frozenset[str] = frozenset(HARD_GATE_IDS_TUPLE)

#: The only accepted hard-gate status strings.
HARD_GATE_STATUS_VALUES: frozenset[str] = frozenset(
    v.value for v in ProposalHardGateStatus
)
