"""Review-freshness gate — the ONE Session 015 hard-gate lifecycle check.

A proposal changed by integration is NOT review-current.  This module holds
the deterministic check behind that invariant: the CURRENT master-proposal
hash must equal the ``proposal_hash`` recorded in the LATEST review bundle
(``04_REVIEWS/iteration_NNN/review_bundle.json``).  When the hashes differ,
the proposal has been edited after its last review and
``HARD_GATE_VALIDATION`` must lead to ``REVISION_REQUIRED`` — never to
``COMPLETE``.

This is a lifecycle safety invariant, NOT a fake evaluation result: no page
limit, citation, challenge-compliance or budget claim is made here (those
validators are future work; the 14 canonical gate ids stay untouched).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..proposal.fingerprint import proposal_fingerprint
from ..proposal.workspace import MASTER_PROPOSAL_RELPATH
from .review_artifacts import BUNDLE_FILENAME

__all__ = [
    "REVIEW_FRESHNESS_GATE_ID",
    "ReviewFreshness",
    "ReviewFreshnessError",
    "evaluate_review_freshness",
]

#: Identifier of this lifecycle gate (NOT one of the 14 canonical
#: evaluation gate ids — it is a Session 015 lifecycle invariant name).
REVIEW_FRESHNESS_GATE_ID = "REVIEW_FRESHNESS"


class ReviewFreshnessError(RuntimeError):
    """The freshness check could not be performed (fail closed).

    ``reason`` is a short stable machine tag; the message is bounded and
    human-readable.  A missing or unreadable review bundle is NEVER
    interpreted as "fresh" — an unreviewable state must not look approved.
    """

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"[{reason}] {message}")


@dataclass(slots=True)
class ReviewFreshness:
    """Deterministic outcome of the review-freshness check.

    Session 021: ``blueprint_current`` and ``reviewed_blueprint_hash``
    extend the proposal-only check to the DOCUMENT PAIR.  A review is
    CURRENT only when BOTH hashes still match; ``is_current`` already
    accounts for the Blueprint whenever the latest bundle carries a
    Blueprint hash (dual runs).  Legacy bundles (no Blueprint hash) keep
    the exact proposal-only semantics.
    """

    #: True when the current proposal hash equals the latest reviewed hash
    #: (AND, for dual runs, the current living-Blueprint hash equals the
    #: reviewed one).
    is_current: bool
    current_proposal_hash: str
    latest_reviewed_hash: str
    latest_iteration: int
    #: Session 021: current CURRENT_BLUEPRINT.md hash ("" when the workspace
    #: has no living Blueprint or it became unreadable).
    current_blueprint_hash: str = ""
    #: Session 021: the living-Blueprint hash recorded in the latest bundle
    #: ("" for legacy single-document bundles).
    reviewed_blueprint_hash: str = ""

    @property
    def blueprint_current(self) -> bool:
        """True when no Blueprint binding exists OR the hash still matches."""
        return (
            not self.reviewed_blueprint_hash
            or self.reviewed_blueprint_hash == self.current_blueprint_hash
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_current": self.is_current,
            "current_proposal_hash": self.current_proposal_hash,
            "latest_reviewed_hash": self.latest_reviewed_hash,
            "latest_iteration": self.latest_iteration,
            "current_blueprint_hash": self.current_blueprint_hash,
            "reviewed_blueprint_hash": self.reviewed_blueprint_hash,
            "blueprint_current": self.blueprint_current,
        }


def _latest_bundle(workspace: Path) -> tuple[int, dict[str, Any]]:
    """Find and parse the LATEST review bundle (highest iteration dir)."""
    reviews_dir = workspace / "04_REVIEWS"
    if not reviews_dir.is_dir():
        raise ReviewFreshnessError(
            "no_reviews_dir",
            f"{reviews_dir} does not exist; no review evidence exists.",
        )
    iterations: list[int] = []
    for entry in reviews_dir.iterdir():
        name = entry.name
        if entry.is_dir() and name.startswith("iteration_"):
            suffix = name[len("iteration_"):]
            if suffix.isdigit():
                iterations.append(int(suffix))
    if not iterations:
        raise ReviewFreshnessError(
            "no_review_iterations",
            "no iteration directory exists under 04_REVIEWS/.",
        )
    latest = max(iterations)
    bundle_path = reviews_dir / f"iteration_{latest:03d}" / BUNDLE_FILENAME
    if not bundle_path.is_file():
        raise ReviewFreshnessError(
            "latest_bundle_missing",
            f"the latest review bundle is missing: {bundle_path}",
        )
    try:
        data = json.loads(bundle_path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewFreshnessError(
            "latest_bundle_unreadable",
            f"the latest review bundle cannot be read: {bundle_path} ({exc})",
        ) from exc
    if not isinstance(data, dict):
        raise ReviewFreshnessError(
            "latest_bundle_corrupt",
            f"the latest review bundle is not a JSON object: {bundle_path}",
        )
    return latest, data


def evaluate_review_freshness(
    *,
    workspace: Path,
    current_proposal_hash: str,
    current_blueprint_hash: str | None = None,
) -> ReviewFreshness:
    """Compare the CURRENT pair hashes against the latest reviewed pair.

    ``current_proposal_hash`` is the caller-fingerprinted SHA-256 of the
    exact current master-proposal bytes (pass the value computed for the
    hard-gate decision; it is not re-read here).  Raises
    :class:`ReviewFreshnessError` when no review evidence exists or the
    latest bundle cannot be read — never silently treating that as fresh.

    Session 021 DUAL FRESHNESS: the review is current ONLY if BOTH the
    proposal hash AND (when the latest bundle carries a living-Blueprint
    binding) the current ``CURRENT_BLUEPRINT.md`` hash still match.  When
    ``current_blueprint_hash`` is not supplied it is derived here from the
    workspace (empty when the file is absent/unreadable).  A bundle with no
    Blueprint binding (legacy) keeps the exact proposal-only semantics.
    """
    workspace = Path(workspace)
    latest, bundle = _latest_bundle(workspace)
    reviewed_hash = str(bundle.get("proposal_hash") or "")
    if not reviewed_hash:
        raise ReviewFreshnessError(
            "latest_bundle_missing_hash",
            "the latest review bundle carries no proposal_hash.",
        )
    reviewed_blueprint_hash = str(bundle.get("current_blueprint_hash") or "")
    if current_blueprint_hash is None:
        from ..proposal.living_blueprint import CURRENT_BLUEPRINT_RELPATH

        bp_path = workspace.joinpath(*CURRENT_BLUEPRINT_RELPATH.split("/"))
        derived: str = ""
        if bp_path.is_file():
            try:
                derived = proposal_fingerprint(bp_path)
            except OSError:
                derived = ""
        current_blueprint_hash = derived
    proposal_current = reviewed_hash == str(current_proposal_hash).strip().lower()
    blueprint_matches = (
        not reviewed_blueprint_hash
        or reviewed_blueprint_hash == str(current_blueprint_hash).strip().lower()
    )
    return ReviewFreshness(
        is_current=proposal_current and blueprint_matches,
        current_proposal_hash=str(current_proposal_hash).strip().lower(),
        latest_reviewed_hash=reviewed_hash,
        latest_iteration=latest,
        current_blueprint_hash=str(current_blueprint_hash or ""),
        reviewed_blueprint_hash=reviewed_blueprint_hash,
    )
