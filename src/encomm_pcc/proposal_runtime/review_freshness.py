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
    """Deterministic outcome of the review-freshness check."""

    #: True when the current proposal hash equals the latest reviewed hash.
    is_current: bool
    current_proposal_hash: str
    latest_reviewed_hash: str
    latest_iteration: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_current": self.is_current,
            "current_proposal_hash": self.current_proposal_hash,
            "latest_reviewed_hash": self.latest_reviewed_hash,
            "latest_iteration": self.latest_iteration,
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
) -> ReviewFreshness:
    """Compare the CURRENT proposal hash against the latest reviewed hash.

    ``current_proposal_hash`` is the caller-fingerprinted SHA-256 of the
    exact current master-proposal bytes (pass the value computed for the
    hard-gate decision; it is not re-read here).  Raises
    :class:`ReviewFreshnessError` when no review evidence exists or the
    latest bundle cannot be read — never silently treating that as fresh.
    """
    workspace = Path(workspace)
    latest, bundle = _latest_bundle(workspace)
    reviewed_hash = str(bundle.get("proposal_hash") or "")
    if not reviewed_hash:
        raise ReviewFreshnessError(
            "latest_bundle_missing_hash",
            "the latest review bundle carries no proposal_hash.",
        )
    return ReviewFreshness(
        is_current=(reviewed_hash == str(current_proposal_hash).strip().lower()),
        current_proposal_hash=str(current_proposal_hash).strip().lower(),
        latest_reviewed_hash=reviewed_hash,
        latest_iteration=latest,
    )
