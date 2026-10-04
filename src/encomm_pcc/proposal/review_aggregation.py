"""Deterministic three-reviewer aggregation — PURE proposal domain.

Consumes the three validated :class:`~encomm_pcc.proposal.ProposalReviewResult`
objects of ONE review iteration and produces the deterministic
:class:`ProposalReviewBundle` plus the deterministic integration-brief dict.
This module knows NOTHING about drivers, engines, files or providers — the
``proposal_runtime`` package feeds it parsed results and persists what it
returns.

Aggregation is DETERMINISTIC by construction:

* verdict rule — BLOCKED if any reviewer returned BLOCKED, otherwise
  NEEDS_REVISION if any returned NEEDS_REVISION, otherwise PASS (PASS requires
  all three);
* finding order — severity first (critical, high, medium, low), then reviewer
  order (SCIENTIFIC_REVIEWER, PROPOSAL_ENGINEER, RED_TEAM_REVIEWER), then the
  reviewer's original finding order (Python's stable sort keeps the
  reviewer/original order inside one severity class);
* patch/claim order — reviewer order, then original order (patches and claims
  carry no severity);
* NO semantic deduplication — findings that are semantically similar are never
  merged; EXACT duplicate findings (identical content fields) are kept and
  MARKED deterministically via ``duplicate_of_index`` (first occurrence wins);
* NO numeric 0–100 score exists in this contract, by design.

The integration brief is a structured projection of the bundle (verdicts,
ordered items, severity counts, affected sections, referenced sources and the
``integration_required`` flag).  It invents NO recommendation beyond reviewer
output and is computed with ZERO model involvement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .enums import ProposalFindingSeverity, ProposalReviewVerdict, ProposalRole
from .models import ProposalFinding, ProposalPatch, ProposalReviewResult

__all__ = [
    "INTEGRATION_BRIEF_SCHEMA",
    "REVIEWER_ORDER",
    "SEVERITY_ORDER",
    "AggregatedFinding",
    "AggregatedPatch",
    "ProposalCycleVerdict",
    "ProposalReviewBundle",
    "aggregate_reviews",
    "build_integration_brief",
]

#: Schema identifier persisted inside ``integration_brief.json``.
INTEGRATION_BRIEF_SCHEMA = "encomm-pcc.integration-brief/v1"

#: Canonical reviewer evaluation order (also the artifact/finding order).
REVIEWER_ORDER: tuple[ProposalRole, ...] = (
    ProposalRole.SCIENTIFIC_REVIEWER,
    ProposalRole.PROPOSAL_ENGINEER,
    ProposalRole.RED_TEAM_REVIEWER,
)

#: Canonical severity ordering (most severe first).
SEVERITY_ORDER: tuple[ProposalFindingSeverity, ...] = (
    ProposalFindingSeverity.CRITICAL,
    ProposalFindingSeverity.HIGH,
    ProposalFindingSeverity.MEDIUM,
    ProposalFindingSeverity.LOW,
)

_SEVERITY_RANK: dict[ProposalFindingSeverity, int] = {
    severity: rank for rank, severity in enumerate(SEVERITY_ORDER)
}

_ROLE_RANK: dict[ProposalRole, int] = {
    role: rank for rank, role in enumerate(REVIEWER_ORDER)
}


class ProposalCycleVerdict(str, Enum):
    """Deterministic verdict of ONE complete three-reviewer cycle."""

    PASS = "PASS"
    NEEDS_REVISION = "NEEDS_REVISION"
    BLOCKED = "BLOCKED"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


@dataclass(slots=True)
class AggregatedFinding:
    """One reviewer finding inside the deterministic aggregate order.

    ``duplicate_of_index`` is ``None`` for the first occurrence of a finding's
    exact content; an EXACT duplicate (identical severity/category/section/
    message/evidence/source_refs/suggested_change) carries the ordered index
    of its first occurrence.  Duplicates are never silently dropped.
    """

    reviewer_role: ProposalRole
    finding: ProposalFinding
    duplicate_of_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "reviewer_role": self.reviewer_role.value,
            "finding": self.finding.to_dict(),
            "duplicate_of_index": self.duplicate_of_index,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AggregatedFinding":
        dup = data.get("duplicate_of_index")
        return cls(
            reviewer_role=ProposalRole(str(data.get("reviewer_role") or "")),
            finding=ProposalFinding.from_dict(data.get("finding") or {}),
            duplicate_of_index=int(dup) if dup is not None else None,
        )


@dataclass(slots=True)
class AggregatedPatch:
    """One reviewer patch proposal inside the deterministic aggregate order."""

    reviewer_role: ProposalRole
    patch: ProposalPatch

    def to_dict(self) -> dict[str, Any]:
        return {
            "reviewer_role": self.reviewer_role.value,
            "patch": self.patch.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AggregatedPatch":
        return cls(
            reviewer_role=ProposalRole(str(data.get("reviewer_role") or "")),
            patch=ProposalPatch.from_dict(data.get("patch") or {}),
        )


@dataclass(slots=True)
class ProposalReviewBundle:
    """Deterministic aggregate of ONE complete three-reviewer iteration.

    Built ONLY by :func:`aggregate_reviews` from three validated results over
    the SAME proposal revision/hash — never assembled by hand.
    """

    iteration_number: int
    proposal_revision: str
    proposal_hash: str
    #: Session 021: the LIVING Blueprint hash this iteration reviewed (empty
    #: in legacy single-document bundles).  Freshness binds the PAIR.
    current_blueprint_hash: str = field(default="", kw_only=True)
    scientific_review: ProposalReviewResult
    implementation_review: ProposalReviewResult
    red_team_review: ProposalReviewResult
    aggregate_verdict: ProposalCycleVerdict
    findings: list[AggregatedFinding] = field(default_factory=list)
    proposed_patches: list[AggregatedPatch] = field(default_factory=list)
    unverified_claims: list[str] = field(default_factory=list)
    counts_by_severity: dict[str, int] = field(default_factory=dict)

    #: Reviewer verdict lookup (role value → verdict value).
    def verdict_for(self, role: ProposalRole) -> str:
        results = {
            ProposalRole.SCIENTIFIC_REVIEWER: self.scientific_review,
            ProposalRole.PROPOSAL_ENGINEER: self.implementation_review,
            ProposalRole.RED_TEAM_REVIEWER: self.red_team_review,
        }
        return results[role].verdict.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration_number": self.iteration_number,
            "proposal_revision": self.proposal_revision,
            "proposal_hash": self.proposal_hash,
            "current_blueprint_hash": self.current_blueprint_hash,
            "scientific_review": self.scientific_review.to_dict(),
            "implementation_review": self.implementation_review.to_dict(),
            "red_team_review": self.red_team_review.to_dict(),
            "aggregate_verdict": self.aggregate_verdict.value,
            "findings": [f.to_dict() for f in self.findings],
            "proposed_patches": [p.to_dict() for p in self.proposed_patches],
            "unverified_claims": list(self.unverified_claims),
            "counts_by_severity": dict(self.counts_by_severity),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProposalReviewBundle":
        verdict_raw = str(data.get("aggregate_verdict") or "")
        try:
            verdict = ProposalCycleVerdict(verdict_raw)
        except ValueError as exc:
            raise ValueError(
                f"unknown aggregate_verdict: {verdict_raw!r}"
            ) from exc
        return cls(
            iteration_number=int(data.get("iteration_number") or 0),
            proposal_revision=str(data.get("proposal_revision") or ""),
            proposal_hash=str(data.get("proposal_hash") or ""),
            current_blueprint_hash=str(data.get("current_blueprint_hash") or ""),
            scientific_review=ProposalReviewResult.from_dict(
                data.get("scientific_review") or {}
            ),
            implementation_review=ProposalReviewResult.from_dict(
                data.get("implementation_review") or {}
            ),
            red_team_review=ProposalReviewResult.from_dict(
                data.get("red_team_review") or {}
            ),
            aggregate_verdict=verdict,
            findings=[
                AggregatedFinding.from_dict(f) for f in data.get("findings") or []
            ],
            proposed_patches=[
                AggregatedPatch.from_dict(p)
                for p in data.get("proposed_patches") or []
            ],
            unverified_claims=[str(c) for c in data.get("unverified_claims") or []],
            counts_by_severity={
                str(k): int(v)
                for k, v in (data.get("counts_by_severity") or {}).items()
            },
        )


def _finding_content_signature(finding: ProposalFinding) -> tuple[Any, ...]:
    """Exact-content identity of a finding (used ONLY for duplicate marking)."""
    return (
        finding.severity.value,
        finding.category,
        finding.section,
        finding.message,
        finding.evidence,
        tuple(finding.source_refs),
        finding.suggested_change,
    )


def aggregate_reviews(
    *,
    iteration_number: int,
    proposal_revision: str,
    proposal_hash: str,
    scientific_review: ProposalReviewResult,
    implementation_review: ProposalReviewResult,
    red_team_review: ProposalReviewResult,
    current_blueprint_hash: str = "",
) -> ProposalReviewBundle:
    """Aggregate the three reviewer results of ONE iteration — fail closed.

    Raises ``ValueError`` when a result does not belong to its slot's reviewer
    role, when any result carries a different ``iteration_number``, or when
    the iteration number itself is not a positive integer.  Mixed-revision
    results can never reach this function through the review loop (the loop
    re-verifies the proposal hash before aggregating); the role/iteration
    checks here are the second line of defence.

    Session 021: ``current_blueprint_hash`` records the living-Blueprint
    revision the three results reviewed (empty = legacy single-document
    bundle) so review freshness can bind the DOCUMENT PAIR.
    """
    if (
        not isinstance(iteration_number, int)
        or isinstance(iteration_number, bool)
        or iteration_number < 1
    ):
        raise ValueError("iteration_number must be a positive integer.")

    slots = (
        (ProposalRole.SCIENTIFIC_REVIEWER, scientific_review),
        (ProposalRole.PROPOSAL_ENGINEER, implementation_review),
        (ProposalRole.RED_TEAM_REVIEWER, red_team_review),
    )
    by_role = dict(slots)
    for expected_role, result in slots:
        if not isinstance(result, ProposalReviewResult):
            raise ValueError(
                f"{expected_role.value} slot requires a ProposalReviewResult."
            )
        if result.reviewer_role is not expected_role:
            raise ValueError(
                f"result role mismatch: the {expected_role.value} slot carries "
                f"{result.reviewer_role.value}."
            )
        if result.iteration_number != iteration_number:
            raise ValueError(
                f"{expected_role.value} reviewed iteration "
                f"{result.iteration_number}, not {iteration_number}; refusing "
                "to aggregate mixed iterations."
            )

    verdicts = [result.verdict for _, result in slots]
    if ProposalReviewVerdict.BLOCKED in verdicts:
        aggregate = ProposalCycleVerdict.BLOCKED
    elif ProposalReviewVerdict.NEEDS_REVISION in verdicts:
        aggregate = ProposalCycleVerdict.NEEDS_REVISION
    else:
        aggregate = ProposalCycleVerdict.PASS

    # Findings: reviewer order → original order, then a STABLE sort by
    # severity rank keeps reviewer/original order inside one severity class.
    staged: list[AggregatedFinding] = []
    for role in REVIEWER_ORDER:
        result = by_role[role]
        for finding in result.findings:
            staged.append(AggregatedFinding(reviewer_role=role, finding=finding))
    staged.sort(key=lambda item: _SEVERITY_RANK[item.finding.severity])

    # Exact-duplicate MARKING (deterministic first-occurrence-wins scan over
    # the final order).  Nothing is removed.
    seen: dict[tuple[Any, ...], int] = {}
    for index, item in enumerate(staged):
        signature = _finding_content_signature(item.finding)
        if signature in seen:
            item.duplicate_of_index = seen[signature]
        else:
            seen[signature] = index

    patches: list[AggregatedPatch] = []
    claims: list[str] = []
    for role in REVIEWER_ORDER:
        result = by_role[role]
        for patch in result.proposed_patches:
            patches.append(AggregatedPatch(reviewer_role=role, patch=patch))
        claims.extend(result.unverified_claims)

    counts = {severity.value: 0 for severity in SEVERITY_ORDER}
    for item in staged:
        counts[item.finding.severity.value] += 1

    return ProposalReviewBundle(
        iteration_number=iteration_number,
        proposal_revision=proposal_revision,
        proposal_hash=proposal_hash,
        current_blueprint_hash=str(current_blueprint_hash or ""),
        scientific_review=scientific_review,
        implementation_review=implementation_review,
        red_team_review=red_team_review,
        aggregate_verdict=aggregate,
        findings=staged,
        proposed_patches=patches,
        unverified_claims=claims,
        counts_by_severity=counts,
    )


def build_integration_brief(bundle: ProposalReviewBundle) -> dict[str, Any]:
    """Project a bundle into the deterministic integration-brief dict.

    Pure structured aggregation — NO model call, NO invented recommendation.
    ``integration_required`` is TRUE whenever the aggregate verdict is not
    PASS (NEEDS_REVISION or BLOCKED) or ANY actionable item exists (finding,
    patch proposal or unverified claim); a clean PASS with no actionable item
    sets it FALSE.
    """
    affected_sections: set[str] = set()
    source_refs: set[str] = set()
    for item in bundle.findings:
        if item.finding.section:
            affected_sections.add(item.finding.section)
        source_refs.update(item.finding.source_refs)
    for item in bundle.proposed_patches:
        if item.patch.target_section:
            affected_sections.add(item.patch.target_section)
        source_refs.update(item.patch.source_refs)

    integration_required = bool(
        bundle.aggregate_verdict is not ProposalCycleVerdict.PASS
        or bundle.findings
        or bundle.proposed_patches
        or bundle.unverified_claims
    )

    return {
        "schema": INTEGRATION_BRIEF_SCHEMA,
        "iteration_number": bundle.iteration_number,
        "proposal_revision": bundle.proposal_revision,
        "proposal_hash": bundle.proposal_hash,
        "aggregate_verdict": bundle.aggregate_verdict.value,
        "reviewer_verdicts": {
            role.value: bundle.verdict_for(role) for role in REVIEWER_ORDER
        },
        "findings": [item.to_dict() for item in bundle.findings],
        "proposed_patches": [
            item.to_dict() for item in bundle.proposed_patches
        ],
        "unverified_claims": list(bundle.unverified_claims),
        "counts_by_severity": dict(bundle.counts_by_severity),
        "affected_sections": sorted(affected_sections),
        "source_refs": sorted(source_refs),
        "integration_required": integration_required,
    }
