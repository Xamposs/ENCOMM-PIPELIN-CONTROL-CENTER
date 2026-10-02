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
from typing import Any, Mapping

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
    """

    iteration_number: int
    input_proposal_hash: str
    revised_proposal: str
    summary: str = ""
    applied_items: list[ProposalIntegrationItem] = field(default_factory=list)
    rejected_items: list[ProposalIntegrationItem] = field(default_factory=list)
    unresolved_items: list[ProposalIntegrationItem] = field(default_factory=list)

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
        return {
            "iteration_number": self.iteration_number,
            "input_proposal_hash": self.input_proposal_hash,
            "revised_proposal": self.revised_proposal,
            "summary": self.summary,
            "applied_items": [i.to_dict() for i in self.applied_items],
            "rejected_items": [i.to_dict() for i in self.rejected_items],
            "unresolved_items": [i.to_dict() for i in self.unresolved_items],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProposalIntegrationResult":
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
        )
