"""Panel docket + deterministic consensus matrix (Session 019).

PURE proposal domain.  After the independent first pass, the aggregated
review bundle becomes a DOCKET: stable ids ``F001, F002, ...`` for findings
and ``P001, P002, ...`` for patch proposals, ordered by the SAME stable
aggregation order (severity → reviewer → original).  After the consensus
round, the three per-item judgement sets become the CONSENSUS MATRIX — a
deterministic report of agreement structure.

Honesty rules (brief §20):

* the matrix NEVER decides scientific truth by majority vote;
* it reports agreement/disagreement/insufficient-evidence counts, the
  proposed resolutions and an ``unresolved`` flag;
* ASTRA remains responsible for synthesis while source-of-truth rules stay
  absolute.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .enums import FINDING_TARGETS, ProposalRole
from .review_aggregation import ProposalReviewBundle
from .panel_contracts import ConsensusJudgement, PanelConsensusResult

__all__ = [
    "CONSENSUS_MATRIX_SCHEMA",
    "PANEL_DOCKET_SCHEMA",
    "ConsensusItemRow",
    "PanelConsensusMatrix",
    "PanelDocket",
    "PanelDocketItem",
    "build_consensus_matrix",
    "build_panel_docket",
    "docket_text",
]

#: Schema identifiers persisted inside the durable artifacts.
PANEL_DOCKET_SCHEMA = "encomm-pcc.panel-docket/v1"
CONSENSUS_MATRIX_SCHEMA = "encomm-pcc.panel-consensus-matrix/v1"

#: Canonical evaluator order (mirrors the aggregation order).
_EVALUATOR_ORDER: tuple[ProposalRole, ...] = (
    ProposalRole.SCIENTIFIC_REVIEWER,
    ProposalRole.PROPOSAL_ENGINEER,
    ProposalRole.RED_TEAM_REVIEWER,
)


@dataclass(slots=True)
class PanelDocketItem:
    """One docket entry — one aggregated finding or patch proposal.

    Session 021: ``target`` is the finding/patch's document target
    (PROPOSAL / BLUEPRINT / BOTH) preserved from the parsed review result —
    the whole chain (docket → consensus → chair context) keeps it.
    """

    item_id: str               # F001… / P001…
    kind: str                  # "finding" | "patch"
    source_reviewer: str
    severity: str              # findings only ("" for patches)
    category: str
    section: str
    message: str
    evidence_refs: list[str] = field(default_factory=list)
    #: Index into the bundle's original list (traceability).
    source_index: int = -1
    #: Session 021 dual-document target (validated whitelist value).
    target: str = "PROPOSAL"

    def __post_init__(self) -> None:
        target = str(self.target or "PROPOSAL").strip().upper()
        if target not in FINDING_TARGETS:
            raise ValueError(
                f"docket item target must be one of {sorted(FINDING_TARGETS)}; "
                f"got {self.target!r}."
            )
        self.target = target

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "kind": self.kind,
            "source_reviewer": self.source_reviewer,
            "severity": self.severity,
            "category": self.category,
            "section": self.section,
            "message": self.message,
            "evidence_refs": list(self.evidence_refs),
            "source_index": self.source_index,
            "target": self.target,
        }


@dataclass(slots=True)
class PanelDocket:
    """The deterministic panel docket for ONE review iteration."""

    iteration_number: int
    proposal_hash: str
    proposal_revision: str
    source_pack_id: str
    items: list[PanelDocketItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": PANEL_DOCKET_SCHEMA,
            "iteration_number": self.iteration_number,
            "proposal_hash": self.proposal_hash,
            "proposal_revision": self.proposal_revision,
            "source_pack_id": self.source_pack_id,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PanelDocket":
        if data.get("schema") not in (PANEL_DOCKET_SCHEMA,):
            raise ValueError(
                f"panel docket schema {data.get('schema')!r} != "
                f"{PANEL_DOCKET_SCHEMA!r}"
            )
        return cls(
            iteration_number=int(data.get("iteration_number") or 0),
            proposal_hash=str(data.get("proposal_hash") or ""),
            proposal_revision=str(data.get("proposal_revision") or ""),
            source_pack_id=str(data.get("source_pack_id") or ""),
            items=[
                PanelDocketItem(
                    item_id=str(item.get("item_id") or ""),
                    kind=str(item.get("kind") or ""),
                    source_reviewer=str(item.get("source_reviewer") or ""),
                    severity=str(item.get("severity") or ""),
                    category=str(item.get("category") or ""),
                    section=str(item.get("section") or ""),
                    message=str(item.get("message") or ""),
                    evidence_refs=[
                        str(r) for r in (item.get("evidence_refs") or [])
                    ],
                    source_index=int(item.get("source_index") if item.get("source_index") is not None else -1),
                    target=str(item.get("target") or "PROPOSAL"),
                )
                for item in (data.get("items") or [])
            ],
        )


@dataclass(slots=True)
class ConsensusItemRow:
    """Consensus structure for ONE docket item across the three evaluators."""

    item_id: str
    votes: dict[str, str]              # role value → judgement
    agreement_count: int               # AGREE votes
    disagreement_count: int            # DISAGREE votes
    insufficient_count: int            # INSUFFICIENT_EVIDENCE votes
    partial_count: int                 # PARTIAL votes
    blocks_acceptance: bool            # ANY evaluator says the item blocks
    proposed_resolutions: list[str]    # deterministic evaluator order
    unresolved: bool
    #: Session 021: the document target carried from the docket item
    #: (PROPOSAL / BLUEPRINT / BOTH) — empty only in legacy artifacts.
    target: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "votes": dict(self.votes),
            "agreement_count": self.agreement_count,
            "disagreement_count": self.disagreement_count,
            "insufficient_count": self.insufficient_count,
            "partial_count": self.partial_count,
            "blocks_acceptance": self.blocks_acceptance,
            "proposed_resolutions": list(self.proposed_resolutions),
            "unresolved": self.unresolved,
            "target": self.target,
        }


@dataclass(slots=True)
class PanelConsensusMatrix:
    """The deterministic consensus matrix for ONE panel round."""

    iteration_number: int
    proposal_hash: str
    proposal_revision: str
    source_pack_id: str
    rows: list[ConsensusItemRow] = field(default_factory=list)
    #: Roles that contributed (exactly the three evaluators, canonical order).
    participating_roles: list[str] = field(default_factory=list)

    @property
    def unresolved_count(self) -> int:
        return sum(1 for row in self.rows if row.unresolved)

    @property
    def blocking_count(self) -> int:
        return sum(1 for row in self.rows if row.blocks_acceptance)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CONSENSUS_MATRIX_SCHEMA,
            "iteration_number": self.iteration_number,
            "proposal_hash": self.proposal_hash,
            "proposal_revision": self.proposal_revision,
            "source_pack_id": self.source_pack_id,
            "participating_roles": list(self.participating_roles),
            "unresolved_count": self.unresolved_count,
            "blocking_count": self.blocking_count,
            "rows": [row.to_dict() for row in self.rows],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PanelConsensusMatrix":
        if data.get("schema") not in (CONSENSUS_MATRIX_SCHEMA,):
            raise ValueError(
                f"consensus matrix schema {data.get('schema')!r} != "
                f"{CONSENSUS_MATRIX_SCHEMA!r}"
            )
        return cls(
            iteration_number=int(data.get("iteration_number") or 0),
            proposal_hash=str(data.get("proposal_hash") or ""),
            proposal_revision=str(data.get("proposal_revision") or ""),
            source_pack_id=str(data.get("source_pack_id") or ""),
            participating_roles=[
                str(r) for r in (data.get("participating_roles") or [])
            ],
            rows=[
                ConsensusItemRow(
                    item_id=str(row.get("item_id") or ""),
                    votes={
                        str(k): str(v)
                        for k, v in (row.get("votes") or {}).items()
                    },
                    agreement_count=int(row.get("agreement_count") or 0),
                    disagreement_count=int(row.get("disagreement_count") or 0),
                    insufficient_count=int(row.get("insufficient_count") or 0),
                    partial_count=int(row.get("partial_count") or 0),
                    blocks_acceptance=bool(row.get("blocks_acceptance") or False),
                    proposed_resolutions=[
                        str(r) for r in (row.get("proposed_resolutions") or [])
                    ],
                    unresolved=bool(row.get("unresolved") or False),
                    target=str(row.get("target") or ""),
                )
                for row in (data.get("rows") or [])
            ],
        )


def build_panel_docket(
    bundle: ProposalReviewBundle, *, source_pack_id: str
) -> PanelDocket:
    """Project the aggregated bundle into the stable-id panel docket.

    Ordering follows the bundle's OWN deterministic order (severity →
    reviewer → original); ids are assigned sequentially per kind, so the
    same bundle always yields the same ids.
    """
    items: list[PanelDocketItem] = []
    finding_no = 0
    for index, aggregated in enumerate(bundle.findings):
        finding_no += 1
        items.append(
            PanelDocketItem(
                item_id=f"F{finding_no:03d}",
                kind="finding",
                source_reviewer=aggregated.reviewer_role.value,
                severity=aggregated.finding.severity.value,
                category=aggregated.finding.category,
                section=aggregated.finding.section,
                message=aggregated.finding.message,
                evidence_refs=list(aggregated.finding.source_refs),
                source_index=index,
                target=aggregated.finding.target.value,
            )
        )
    patch_no = 0
    for index, aggregated in enumerate(bundle.proposed_patches):
        patch_no += 1
        items.append(
            PanelDocketItem(
                item_id=f"P{patch_no:03d}",
                kind="patch",
                source_reviewer=aggregated.reviewer_role.value,
                severity="",
                category=aggregated.patch.target_section,
                section=aggregated.patch.target_section,
                message=aggregated.patch.rationale,
                evidence_refs=list(aggregated.patch.source_refs),
                source_index=index,
                target=aggregated.patch.target.value,
            )
        )
    return PanelDocket(
        iteration_number=bundle.iteration_number,
        proposal_hash=bundle.proposal_hash,
        proposal_revision=bundle.proposal_revision,
        source_pack_id=source_pack_id,
        items=items,
    )


def docket_text(docket: PanelDocket) -> str:
    """Deterministic plain-text docket rendering for prompts/artifacts."""
    if not docket.items:
        return "[NO DOCKET ITEMS]"
    lines: list[str] = []
    for item in docket.items:
        severity = f" [{item.severity}]" if item.severity else ""
        lines.append(
            f"- {item.item_id}{severity} ({item.kind}; by "
            f"{item.source_reviewer}; section: {item.section or 'n/a'}; "
            f"target: {item.target}): "
            f"{item.message}"
        )
    return "\n".join(lines)


def build_consensus_matrix(
    *,
    iteration_number: int,
    proposal_hash: str,
    proposal_revision: str,
    source_pack_id: str,
    consensus_results: Mapping[ProposalRole, PanelConsensusResult],
    docket: PanelDocket | None = None,
) -> PanelConsensusMatrix:
    """Aggregate the three consensus answers into the deterministic matrix.

    Every docket item id seen in ANY answer gets a row; votes are read per
    evaluator in canonical order.  An item is ``unresolved`` when the
    evaluators are not unanimous: any DISAGREE, PARTIAL or
    INSUFFICIENT_EVIDENCE vote makes the item unresolved.  Unanimity on
    AGREE resolves the item.  NOTHING here decides which content is true.

    Session 021: when ``docket`` is supplied, every row carries the docket
    item's document target (PROPOSAL / BLUEPRINT / BOTH) so the chair
    context and the UI can see WHICH document each consensus row is about.
    """
    missing = [role for role in _EVALUATOR_ORDER if role not in consensus_results]
    if missing:
        raise ValueError(
            "consensus_results must provide all three evaluator roles; "
            "missing: " + ", ".join(role.value for role in missing)
        )
    extra = [
        role
        for role in consensus_results
        if role not in _EVALUATOR_ORDER
    ]
    if extra:
        raise ValueError(
            "consensus_results accepts only the three evaluator roles; "
            "unexpected: " + ", ".join(role.value for role in extra)
        )

    judgements: dict[str, dict[str, ConsensusJudgement]] = {}
    for role in _EVALUATOR_ORDER:
        for judgement in consensus_results[role].judgements:
            judgements.setdefault(judgement.item_id, {})[role.value] = judgement

    # Session 021: docket item targets by id (the docket is the authoritative
    # source of the target; an id absent from the docket carries no target).
    targets_by_id: dict[str, str] = {}
    if docket is not None:
        targets_by_id = {item.item_id: item.target for item in docket.items}

    rows: list[ConsensusItemRow] = []
    for item_id in sorted(judgements):
        per_item = judgements[item_id]
        votes: dict[str, str] = {}
        agree = disagree = insufficient = partial = 0
        resolutions: list[str] = []
        blocks = False
        for role in _EVALUATOR_ORDER:
            judgement = per_item.get(role.value)
            if judgement is None:
                # An evaluator skipped this item: treat as insufficient —
                # never silently pretend a missing vote agrees.
                votes[role.value] = "MISSING"
                insufficient += 1
                continue
            votes[role.value] = judgement.judgement
            if judgement.judgement == "AGREE":
                agree += 1
            elif judgement.judgement == "DISAGREE":
                disagree += 1
            elif judgement.judgement == "INSUFFICIENT_EVIDENCE":
                insufficient += 1
            else:
                partial += 1
            if judgement.proposed_resolution.strip():
                resolutions.append(
                    f"{role.value}: {judgement.proposed_resolution.strip()}"
                )
            if judgement.blocks_acceptance:
                blocks = True
        unresolved = bool(disagree or insufficient or partial)
        rows.append(
            ConsensusItemRow(
                item_id=item_id,
                votes=votes,
                agreement_count=agree,
                disagreement_count=disagree,
                insufficient_count=insufficient,
                partial_count=partial,
                blocks_acceptance=blocks,
                proposed_resolutions=resolutions,
                unresolved=unresolved,
                target=targets_by_id.get(item_id, ""),
            )
        )
    return PanelConsensusMatrix(
        iteration_number=iteration_number,
        proposal_hash=proposal_hash,
        proposal_revision=proposal_revision,
        source_pack_id=source_pack_id,
        rows=rows,
        participating_roles=[role.value for role in _EVALUATOR_ORDER],
    )
