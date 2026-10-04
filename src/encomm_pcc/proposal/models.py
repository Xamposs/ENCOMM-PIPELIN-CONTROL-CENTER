"""Proposal Mode typed domain structures — parallel to ``domain.models``.

Plain, dependency-free dataclasses: the single source of truth for proposal
configuration and review state, and what the (future) persistence layer will
serialise.  No Qt, no driver imports, no SDK objects, no provider names —
engines/providers/models are runtime CONFIGURATION (``ProposalAgentConfig``),
never architectural constants.

Everything here round-trips through primitive JSON-friendly representations
via ``to_dict``/``from_dict`` (same convention as ``domain.audit``).

Source-of-truth authority invariant (Section 4 contract): only the Proposal
ORCHESTRATOR will eventually hold write authority over
``03_PROPOSAL/MASTER_PROPOSAL.md``.  Reviewer roles are read-only with
respect to the master proposal and return structured results
(:class:`ProposalReviewResult`) with patch *proposals*
(:class:`ProposalPatch`) — never direct edits.  These models make that
architecture natural: a reviewer's only outputs ARE proposals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar, Mapping

from .enums import (
    DEFAULT_FINDING_TARGET,
    FINDING_TARGETS,
    HARD_GATE_IDS,
    ProposalFindingSeverity,
    ProposalFindingTarget,
    ProposalHardGateStatus,
    ProposalPhase,
    ProposalReviewVerdict,
    ProposalRole,
)

__all__ = [
    "ProposalAgentConfig",
    "ProposalFinding",
    "ProposalHardGateResult",
    "ProposalIterationRecord",
    "ProposalPatch",
    "ProposalReviewResult",
    "utc_now",
]


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp (second precision, ``Z`` suffix).

    Same convention as ``domain.models.utc_now``; duplicated instead of
    imported so the proposal package stays a fully isolated parallel domain
    with zero imports from the Coding Mode packages.
    """
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


@dataclass(slots=True)
class ProposalAgentConfig:
    """Engine configuration for ONE proposal role.

    Mirrors the Coding Mode rule that roles are independent of engines: the
    engine (a driver id resolved through the registry), project profile,
    provider and model are all runtime configuration.  No provider name is
    ever hardcoded into proposal domain logic.
    """

    role: ProposalRole
    engine: str
    project_profile: str = ""
    provider: str = ""
    model: str = ""
    session_policy: str = "persistent_optional"
    #: Optional externally-known session id to resume (real ids only; never
    #: fabricated — empty when none).
    session_id: str = ""
    #: Session 019 (brief §11): how the driver session is obtained.
    #: ``NEW_SESSION`` (default, recommended for independent evaluator work)
    #: or ``RESUME_SELECTED_SESSION`` (requires a real ``session_id`` and a
    #: driver whose capabilities expose ``supports_resume``).
    session_mode: str = "NEW_SESSION"

    #: Session 021: optional per-invocation reasoning effort for engines
    #: whose verified contract exposes one (Codex: the official
    #: ``-c model_reasoning_effort="<level>"`` override; values
    #: minimal/low/medium/high/xhigh per the installed CLI's config
    #: reference).  Empty/default = the engine's own default (nothing is
    #: passed).  Non-empty values are fail-closed validated here against
    #: the VERIFIED whitelist; the driver passes the setting ONLY for an
    #: engine that declares support, so a Hermes run never receives a
    #: Codex flag.
    reasoning_effort: str = ""

    #: The only accepted session modes (fail-closed whitelist).
    SESSION_MODES: ClassVar[frozenset[str]] = frozenset(
        {"NEW_SESSION", "RESUME_SELECTED_SESSION"}
    )

    #: The only reasoning-effort values the verified Codex contract accepts.
    #: Not a marketing/model catalogue: these are the values of the CLI's
    #: own ``model_reasoning_effort`` config key.
    REASONING_EFFORTS: ClassVar[frozenset[str]] = frozenset(
        {"minimal", "low", "medium", "high", "xhigh"}
    )

    def __post_init__(self) -> None:
        if not isinstance(self.role, ProposalRole):
            self.role = ProposalRole(str(self.role))
        if self.session_mode not in self.SESSION_MODES:
            raise ValueError(
                f"session_mode must be one of {sorted(self.SESSION_MODES)}; "
                f"got {self.session_mode!r}."
            )
        if self.session_mode == "RESUME_SELECTED_SESSION" and not self.session_id.strip():
            raise ValueError(
                "session_mode RESUME_SELECTED_SESSION requires a real "
                "session_id (never invented)."
            )
        effort = str(self.reasoning_effort or "").strip().lower()
        self.reasoning_effort = effort
        if effort and effort not in self.REASONING_EFFORTS:
            raise ValueError(
                "reasoning_effort must be empty (engine default) or one of "
                f"{sorted(self.REASONING_EFFORTS)}; got "
                f"{self.reasoning_effort!r}."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role.value,
            "engine": self.engine,
            "project_profile": self.project_profile,
            "provider": self.provider,
            "model": self.model,
            "session_policy": self.session_policy,
            "session_id": self.session_id,
            "session_mode": self.session_mode,
            "reasoning_effort": self.reasoning_effort,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProposalAgentConfig":
        return cls(
            role=ProposalRole(str(data.get("role") or "")),
            engine=str(data.get("engine") or ""),
            project_profile=str(data.get("project_profile") or ""),
            provider=str(data.get("provider") or ""),
            model=str(data.get("model") or ""),
            session_policy=str(data.get("session_policy") or "persistent_optional"),
            session_id=str(data.get("session_id") or ""),
            session_mode=str(data.get("session_mode") or "NEW_SESSION"),
            reasoning_effort=str(data.get("reasoning_effort") or ""),
        )


@dataclass(slots=True)
class ProposalFinding:
    """One finding a reviewer reported against the document pair.

    Session 021: every finding declares WHICH document it targets —
    ``PROPOSAL`` (the wording/content of MASTER_PROPOSAL.md is weak while
    the Blueprint is correct), ``BLUEPRINT`` (the underlying technical
    design needs to change), or ``BOTH`` (the documents contradict one
    another or both must change).  The typed target rides the whole chain:
    parsers → aggregation → docket → consensus → persistence → UI.
    """

    severity: ProposalFindingSeverity
    category: str
    section: str
    message: str
    evidence: str = ""
    source_refs: list[str] = field(default_factory=list)
    suggested_change: str = ""
    #: Session 021 dual-document target (fail-closed whitelist).
    target: ProposalFindingTarget = ProposalFindingTarget.PROPOSAL

    def __post_init__(self) -> None:
        if not isinstance(self.severity, ProposalFindingSeverity):
            self.severity = ProposalFindingSeverity(str(self.severity))
        if not isinstance(self.target, ProposalFindingTarget):
            target_raw = str(self.target or DEFAULT_FINDING_TARGET).strip().upper()
            if target_raw not in FINDING_TARGETS:
                raise ValueError(
                    "finding target must be one of "
                    f"{sorted(FINDING_TARGETS)}; got {self.target!r}."
                )
            self.target = ProposalFindingTarget(target_raw)

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity.value,
            "category": self.category,
            "section": self.section,
            "message": self.message,
            "evidence": self.evidence,
            "source_refs": list(self.source_refs),
            "suggested_change": self.suggested_change,
            "target": self.target.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProposalFinding":
        refs_raw = data.get("source_refs") or []
        target_raw = str(data.get("target") or DEFAULT_FINDING_TARGET).strip().upper()
        if target_raw not in FINDING_TARGETS:
            raise ValueError(
                "finding target must be one of "
                f"{sorted(FINDING_TARGETS)}; got {data.get('target')!r}."
            )
        return cls(
            severity=ProposalFindingSeverity(str(data.get("severity") or "")),
            category=str(data.get("category") or ""),
            section=str(data.get("section") or ""),
            message=str(data.get("message") or ""),
            evidence=str(data.get("evidence") or ""),
            source_refs=[str(r) for r in refs_raw],
            suggested_change=str(data.get("suggested_change") or ""),
            target=ProposalFindingTarget(target_raw),
        )


@dataclass(slots=True)
class ProposalPatch:
    """A REVIEWER'S PROPOSED change to the master proposal.

    A patch is a proposal, never an edit: applying it is an ORCHESTRATOR
    integration decision (the sole write authority over
    ``MASTER_PROPOSAL.md``).  Exactly one of ``replacement_text`` (the full
    replacement for ``target_section``) or ``patch_instructions`` (structured
    instructions for the orchestrator) should carry content.
    """

    target_section: str
    rationale: str
    replacement_text: str = ""
    patch_instructions: str = ""
    source_refs: list[str] = field(default_factory=list)
    #: Explicit reviewer confidence metadata in ``[0.0, 1.0]``.
    confidence: float = 0.0
    #: Session 021 dual-document target (fail-closed whitelist).
    target: ProposalFindingTarget = ProposalFindingTarget.PROPOSAL

    def __post_init__(self) -> None:
        if not isinstance(self.target, ProposalFindingTarget):
            target_raw = str(self.target or DEFAULT_FINDING_TARGET).strip().upper()
            if target_raw not in FINDING_TARGETS:
                raise ValueError(
                    "patch target must be one of "
                    f"{sorted(FINDING_TARGETS)}; got {self.target!r}."
                )
            self.target = ProposalFindingTarget(target_raw)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_section": self.target_section,
            "rationale": self.rationale,
            "replacement_text": self.replacement_text,
            "patch_instructions": self.patch_instructions,
            "source_refs": list(self.source_refs),
            "confidence": self.confidence,
            "target": self.target.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProposalPatch":
        refs_raw = data.get("source_refs") or []
        try:
            confidence = float(data.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        target_raw = str(data.get("target") or DEFAULT_FINDING_TARGET).strip().upper()
        if target_raw not in FINDING_TARGETS:
            raise ValueError(
                "patch target must be one of "
                f"{sorted(FINDING_TARGETS)}; got {data.get('target')!r}."
            )
        return cls(
            target_section=str(data.get("target_section") or ""),
            rationale=str(data.get("rationale") or ""),
            replacement_text=str(data.get("replacement_text") or ""),
            patch_instructions=str(data.get("patch_instructions") or ""),
            source_refs=[str(r) for r in refs_raw],
            confidence=confidence,
            target=ProposalFindingTarget(target_raw),
        )


@dataclass(slots=True)
class ProposalReviewResult:
    """The structured result ONE reviewer returned for ONE iteration.

    Reviewers never edit the master proposal; this result IS their entire
    output (verdict + findings + patch proposals + unverified claims).
    """

    reviewer_role: ProposalRole
    verdict: ProposalReviewVerdict
    summary: str = ""
    findings: list[ProposalFinding] = field(default_factory=list)
    proposed_patches: list[ProposalPatch] = field(default_factory=list)
    unverified_claims: list[str] = field(default_factory=list)
    iteration_number: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.reviewer_role, ProposalRole):
            self.reviewer_role = ProposalRole(str(self.reviewer_role))
        if not isinstance(self.verdict, ProposalReviewVerdict):
            self.verdict = ProposalReviewVerdict(str(self.verdict))

    @property
    def has_blocking_findings(self) -> bool:
        """True when a critical/high finding is present."""
        return any(
            f.severity in (ProposalFindingSeverity.CRITICAL, ProposalFindingSeverity.HIGH)
            for f in self.findings
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "reviewer_role": self.reviewer_role.value,
            "verdict": self.verdict.value,
            "summary": self.summary,
            "findings": [f.to_dict() for f in self.findings],
            "proposed_patches": [p.to_dict() for p in self.proposed_patches],
            "unverified_claims": list(self.unverified_claims),
            "iteration_number": self.iteration_number,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProposalReviewResult":
        findings_raw = data.get("findings") or []
        patches_raw = data.get("proposed_patches") or []
        claims_raw = data.get("unverified_claims") or []
        try:
            iteration = int(data.get("iteration_number") or 0)
        except (TypeError, ValueError):
            iteration = 0
        try:
            verdict = ProposalReviewVerdict(str(data.get("verdict") or ""))
        except ValueError:
            # Never fabricate a PASS from bad data (same rule as
            # ``domain.audit.AuditVerdictResult.from_dict``).
            verdict = ProposalReviewVerdict.BLOCKED
        return cls(
            reviewer_role=ProposalRole(str(data.get("reviewer_role") or "")),
            verdict=verdict,
            summary=str(data.get("summary") or ""),
            findings=[ProposalFinding.from_dict(f) for f in findings_raw],
            proposed_patches=[ProposalPatch.from_dict(p) for p in patches_raw],
            unverified_claims=[str(c) for c in claims_raw],
            iteration_number=iteration,
        )


@dataclass(slots=True)
class ProposalHardGateResult:
    """The recorded outcome of ONE hard-gate check.

    CONTRACT ONLY: no validator exists yet (Session 012 foundation) and none
    may fabricate a result.  ``gate_id`` MUST be one of the canonical
    :data:`encomm_pcc.proposal.enums.HARD_GATE_IDS` — a non-canonical
    identifier is refused (fail closed), so the identifier set stays unique
    and stable.
    """

    gate_id: str
    status: ProposalHardGateStatus
    message: str = ""
    evidence: str = ""

    def __post_init__(self) -> None:
        self.gate_id = str(self.gate_id)
        if self.gate_id not in HARD_GATE_IDS:
            raise ValueError(
                f"Non-canonical hard-gate id: {self.gate_id!r} "
                f"(must be one of the HARD_GATE_IDS constants)"
            )
        if not isinstance(self.status, ProposalHardGateStatus):
            self.status = ProposalHardGateStatus(str(self.status))

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "status": self.status.value,
            "message": self.message,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProposalHardGateResult":
        # Constructor validation raises on a non-canonical gate_id —
        # identifier fabrication is refused, never coerced.
        return cls(
            gate_id=str(data.get("gate_id") or ""),
            status=ProposalHardGateStatus(str(data.get("status") or "")),
            message=str(data.get("message") or ""),
            evidence=str(data.get("evidence") or ""),
        )


@dataclass(slots=True)
class ProposalIterationRecord:
    """One durable review iteration of the proposal loop.

    ``proposal_hash`` is the deterministic fingerprint of the exact proposal
    revision this iteration reviewed (computed by the caller — e.g. a SHA-256
    over the master proposal text — and stored here verbatim; the foundation
    defines the field, not the hash function).  Reviewer outputs and hard-gate
    results are embedded as structured JSON-friendly records so a future
    persistence layer can serialise them directly.
    """

    iteration_number: int
    proposal_revision: str
    proposal_hash: str
    review_results: list[ProposalReviewResult] = field(default_factory=list)
    hard_gate_results: list[ProposalHardGateResult] = field(default_factory=list)
    status: ProposalPhase = ProposalPhase.REVISION_REQUIRED
    started_at: str = ""
    completed_at: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.status, ProposalPhase):
            self.status = ProposalPhase(str(self.status))

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration_number": self.iteration_number,
            "proposal_revision": self.proposal_revision,
            "proposal_hash": self.proposal_hash,
            "review_results": [r.to_dict() for r in self.review_results],
            "hard_gate_results": [g.to_dict() for g in self.hard_gate_results],
            "status": self.status.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProposalIterationRecord":
        reviews_raw = data.get("review_results") or []
        gates_raw = data.get("hard_gate_results") or []
        try:
            iteration = int(data.get("iteration_number") or 0)
        except (TypeError, ValueError):
            iteration = 0
        return cls(
            iteration_number=iteration,
            proposal_revision=str(data.get("proposal_revision") or ""),
            proposal_hash=str(data.get("proposal_hash") or ""),
            review_results=[ProposalReviewResult.from_dict(r) for r in reviews_raw],
            hard_gate_results=[
                ProposalHardGateResult.from_dict(g) for g in gates_raw
            ],
            status=ProposalPhase(str(data.get("status") or ProposalPhase.REVISION_REQUIRED.value)),
            started_at=str(data.get("started_at") or ""),
            completed_at=str(data.get("completed_at") or ""),
        )
