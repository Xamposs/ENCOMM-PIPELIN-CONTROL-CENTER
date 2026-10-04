"""ORCHESTRATOR integration result — typed PURE models.

The parsed outcome of ONE fail-closed integration operation: which review
items the ORCHESTRATOR applied / rejected / left unresolved, and the ONE
complete revised proposal.  Plain, dependency-free dataclasses that
round-trip through primitive JSON-friendly representations via
``to_dict``/``from_dict`` (the proposal-package convention).  No provider or
SDK objects — integration items retain exactly enough traceability to
explain reviewer-item → action → reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

__all__ = [
    "ProposalIntegrationItem",
    "ProposalIntegrationResult",
]

#: The only action values an integration item may carry (whitelist).
INTEGRATION_ITEM_ACTIONS: frozenset[str] = frozenset(
    {"applied", "rejected", "unresolved"}
)

@dataclass(slots=True)
class ProposalIntegrationItem:
    """One reviewer item disposition recorded by the ORCHESTRATOR.

    ``item_id`` is the ORCHESTRATOR's stable reference to the review item
    (the runtime does not re-verify the id against the brief — the brief is
    advisory input and the ORCHESTRATOR is the integration authority); the
    ``action`` MUST be one of :data:`INTEGRATION_ITEM_ACTIONS`.
    """

    item_id: str
    action: str
    reason: str = ""

    def __post_init__(self) -> None:
        self.item_id = str(self.item_id)
        self.action = str(self.action)
        self.reason = str(self.reason)
        if self.action not in INTEGRATION_ITEM_ACTIONS:
            raise ValueError(
                f"invalid integration item action: {self.action!r} "
                f"(must be one of {sorted(INTEGRATION_ITEM_ACTIONS)})"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "action": self.action,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProposalIntegrationItem":
        return cls(
            item_id=str(data.get("item_id") or ""),
            action=str(data.get("action") or ""),
            reason=str(data.get("reason") or ""),
        )


@dataclass(slots=True)
class ProposalIntegrationResult:
    """The structured result ONE ORCHESTRATOR integration produced.

    ``revised_proposal`` is the ONE COMPLETE revised
    ``MASTER_PROPOSAL.md`` content — the runtime (not the model) owns the
    atomic filesystem write of exactly this text.

    Session 021 (dual-document): ``revised_blueprint`` optionally carries
    the ONE COMPLETE revised ``CURRENT_BLUEPRINT.md`` content.  ``None``
    means "no Blueprint revision claimed" — the legacy single-document
    contract.  A Blueprint change must be JUSTIFIED: the runtime records
    whether the revised text differs from the live living Blueprint, and
    meaningless churn is the chair's honesty duty, not the parser's.
    """

    iteration_number: int
    input_proposal_hash: str
    revised_proposal: str
    summary: str = ""
    applied_items: list[ProposalIntegrationItem] = field(default_factory=list)
    rejected_items: list[ProposalIntegrationItem] = field(default_factory=list)
    unresolved_items: list[ProposalIntegrationItem] = field(default_factory=list)
    #: Session 021: the echoed input CURRENT_BLUEPRINT hash when the chair
    #: was given the document pair (empty in legacy single-document runs).
    input_blueprint_hash: str = ""
    #: Session 021: the COMPLETE revised living Blueprint, or None when the
    #: chair claims no Blueprint revision.
    revised_blueprint: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.iteration_number, int) or isinstance(
            self.iteration_number, bool
        ) or self.iteration_number < 1:
            raise ValueError(
                "iteration_number must be a positive integer, "
                f"got {self.iteration_number!r}."
            )
        self.input_proposal_hash = str(self.input_proposal_hash)
        self.revised_proposal = str(self.revised_proposal)
        self.summary = str(self.summary)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "iteration_number": self.iteration_number,
            "input_proposal_hash": self.input_proposal_hash,
            "revised_proposal": self.revised_proposal,
            "summary": self.summary,
            "applied_items": [i.to_dict() for i in self.applied_items],
            "rejected_items": [i.to_dict() for i in self.rejected_items],
            "unresolved_items": [i.to_dict() for i in self.unresolved_items],
        }
        # Session 021 dual fields — emitted ONLY when the dual contract is
        # in play, so legacy single-document payloads stay byte-shaped.
        if self.input_blueprint_hash or self.revised_blueprint is not None:
            payload["input_blueprint_hash"] = self.input_blueprint_hash
            payload["revised_blueprint"] = self.revised_blueprint
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProposalIntegrationResult":
        revised_blueprint = data.get("revised_blueprint")
        if revised_blueprint is not None and not isinstance(revised_blueprint, str):
            raise ValueError("revised_blueprint must be a string or null.")
        return cls(
            iteration_number=int(data.get("iteration_number") or 0),
            input_proposal_hash=str(data.get("input_proposal_hash") or ""),
            revised_proposal=str(data.get("revised_proposal") or ""),
            summary=str(data.get("summary") or ""),
            applied_items=[
                ProposalIntegrationItem.from_dict(i)
                for i in (data.get("applied_items") or [])
            ],
            rejected_items=[
                ProposalIntegrationItem.from_dict(i)
                for i in (data.get("rejected_items") or [])
            ],
            unresolved_items=[
                ProposalIntegrationItem.from_dict(i)
                for i in (data.get("unresolved_items") or [])
            ],
            input_blueprint_hash=str(data.get("input_blueprint_hash") or ""),
            revised_blueprint=revised_blueprint,
        )
