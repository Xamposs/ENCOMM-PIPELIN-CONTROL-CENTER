"""Durable review artifacts — the 04_REVIEWS/ file-writing adapter.

Writes the per-iteration structured review evidence::

    04_REVIEWS/
        iteration_001/
            scientific_review.json
            implementation_review.json
            red_team_review.json
            review_bundle.json
            integration_brief.json

Hard rules:

* STRUCTURED parsed information only — no model transcript is ever
  persisted (the executor's bounded ``raw_excerpt`` is deliberately dropped
  here); every JSON writer whitelists the exact keys it persists;
* deterministic JSON: ``indent=2``, ``sort_keys=True``, UTF-8, LF newlines,
  newline at EOF;
* ATOMIC writes: temporary file in the SAME directory, then ``os.replace``;
* FAIL-CLOSED over existing evidence: a completed artifact is never silently
  overwritten.  An existing per-review artifact is acceptable ONLY when it
  parses back into the SAME (iteration, role, proposal_hash) contract with a
  valid structured result (safe resume); everything else — a conflicting
  verdict/content, another iteration, another hash, or any existing
  bundle/brief — is an :class:`ArtifactConflictError` that stops the cycle.
* previous iterations are never touched: every write is scoped to this
  iteration's zero-padded directory.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..proposal.enums import ProposalRole
from ..proposal.models import ProposalReviewResult
from ..proposal.review_aggregation import (
    INTEGRATION_BRIEF_SCHEMA,
    ProposalReviewBundle,
    build_integration_brief,
)

__all__ = [
    "BUNDLE_FILENAME",
    "BRIEF_FILENAME",
    "REVIEW_ARTIFACTS_SCHEMA",
    "REVIEW_FILENAME_BY_ROLE",
    "ArtifactConflictError",
    "ReviewArtifactWriter",
    "persist_cycle_artifacts",
]

#: Schema identifier persisted inside every per-review artifact.
REVIEW_ARTIFACTS_SCHEMA = "encomm-pcc.review-artifact/v1"

#: Canonical per-review artifact filename per reviewer role.
REVIEW_FILENAME_BY_ROLE: dict[ProposalRole, str] = {
    ProposalRole.SCIENTIFIC_REVIEWER: "scientific_review.json",
    ProposalRole.PROPOSAL_ENGINEER: "implementation_review.json",
    ProposalRole.RED_TEAM_REVIEWER: "red_team_review.json",
}

#: Aggregate artifact filenames.
BUNDLE_FILENAME = "review_bundle.json"
BRIEF_FILENAME = "integration_brief.json"


class ArtifactConflictError(RuntimeError):
    """An existing artifact conflicts — the cycle must stop, not clobber."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"[{reason}] {message}")


class ReviewArtifactWriter:
    """Writes ONE iteration's review artifacts under ``workspace/04_REVIEWS``.

    Holds the frozen cycle identity (iteration, revision, hash) so every
    artifact carries the same contract and every conflict check compares the
    full identity — never just the filename.
    """

    def __init__(
        self,
        *,
        workspace: Path,
        iteration_number: int,
        proposal_revision: str,
        proposal_hash: str,
    ) -> None:
        self._workspace = Path(workspace)
        self._iteration_number = int(iteration_number)
        self._proposal_revision = str(proposal_revision)
        self._proposal_hash = str(proposal_hash)

    # -- paths ------------------------------------------------------------
    @property
    def iteration_dir(self) -> Path:
        return self._workspace / "04_REVIEWS" / f"iteration_{self._iteration_number:03d}"

    def review_artifact_path(self, role: ProposalRole) -> Path:
        return self.iteration_dir / REVIEW_FILENAME_BY_ROLE[role]

    @property
    def bundle_path(self) -> Path:
        return self.iteration_dir / BUNDLE_FILENAME

    @property
    def brief_path(self) -> Path:
        return self.iteration_dir / BRIEF_FILENAME

    # -- writes ------------------------------------------------------------
    def write_review_artifact(
        self,
        role: ProposalRole,
        *,
        result: ProposalReviewResult,
        execution: Any = None,
        reused: bool = False,
    ) -> Path:
        """Persist ONE reviewer's structured artifact (fail-closed on conflict).

        ``execution`` is the executor's :class:`ProposalReviewExecutionReport`
        (runtime outcome/session/duration metadata); it is read through
        ``getattr`` so any object with the same surface works.  Its bounded
        ``raw_excerpt`` is deliberately NOT persisted.
        """
        artifact: dict[str, Any] = {
            "schema": REVIEW_ARTIFACTS_SCHEMA,
            "iteration_number": self._iteration_number,
            "proposal_revision": self._proposal_revision,
            "proposal_hash": self._proposal_hash,
            "reviewer_role": role.value,
            "result": result.to_dict(),
            "reused": bool(reused),
            "runtime": _runtime_metadata(execution),
        }
        if execution is not None:
            artifact["session_id"] = execution.session_id
            artifact["driver_id"] = execution.driver_id

        target = self.review_artifact_path(role)
        existing = _read_bytes_if_exists(target)
        if existing is not None:
            existing_result = _validate_existing_review_artifact(
                existing,
                role=role,
                iteration_number=self._iteration_number,
                proposal_hash=self._proposal_hash,
            )
            if existing_result.to_dict() != result.to_dict():
                raise ArtifactConflictError(
                    "artifact_content_conflict",
                    f"existing {role.value} artifact for this iteration/hash "
                    f"carries a different result "
                    f"({existing_result.verdict.value} vs "
                    f"{result.verdict.value}); refusing to overwrite review "
                    "evidence.",
                )
            return target  # exact safe reuse
        _atomic_write_json(target, artifact)
        return target

    def load_existing_review_result(self, role: ProposalRole) -> ProposalReviewResult | None:
        """Return the parsed result of an existing artifact, else ``None``.

        Fail-closed: an existing artifact that does not match THIS writer's
        (iteration, role, hash) identity or does not parse into a valid
        :class:`ProposalReviewResult` raises :class:`ArtifactConflictError`.
        Used by the review loop's safe-resume path.
        """
        raw = _read_bytes_if_exists(self.review_artifact_path(role))
        if raw is None:
            return None
        return _validate_existing_review_artifact(
            raw,
            role=role,
            iteration_number=self._iteration_number,
            proposal_hash=self._proposal_hash,
        )

    def write_bundle(self, bundle: ProposalReviewBundle) -> Path:
        """Persist ``review_bundle.json`` (refuses ANY existing content)."""
        target = self.bundle_path
        if target.exists():
            raise ArtifactConflictError(
                "bundle_conflict",
                f"{target} already exists; refusing to overwrite review "
                "evidence for this iteration.",
            )
        _atomic_write_json(target, bundle.to_dict())
        return target

    def write_brief(self, bundle: ProposalReviewBundle) -> Path:
        """Persist the deterministic ``integration_brief.json``."""
        target = self.brief_path
        if target.exists():
            raise ArtifactConflictError(
                "brief_conflict",
                f"{target} already exists; refusing to overwrite the "
                "integration brief for this iteration.",
            )
        _atomic_write_json(target, build_integration_brief(bundle))
        return target


def persist_cycle_artifacts(
    *,
    workspace: Path,
    bundle: ProposalReviewBundle,
    executions: dict[ProposalRole, Any],
    reused_roles: frozenset[ProposalRole] = frozenset(),
) -> dict[str, Path]:
    """Persist a COMPLETE cycle: per-review artifacts + bundle + brief.

    Returns a mapping of logical name → written path.  Called by the review
    loop ONLY after all three reviewers completed validly over one hash —
    no partial cycle ever reaches this function.
    """
    writer = ReviewArtifactWriter(
        workspace=workspace,
        iteration_number=bundle.iteration_number,
        proposal_revision=bundle.proposal_revision,
        proposal_hash=bundle.proposal_hash,
    )
    for role, result in (
        (ProposalRole.SCIENTIFIC_REVIEWER, bundle.scientific_review),
        (ProposalRole.PROPOSAL_ENGINEER, bundle.implementation_review),
        (ProposalRole.RED_TEAM_REVIEWER, bundle.red_team_review),
    ):
        if role in reused_roles:
            # The reused artifact IS the durable evidence from the process
            # that ran the reviewer; rewriting it would clobber evidence.
            continue
        writer.write_review_artifact(
            role,
            result=result,
            execution=executions.get(role),
            reused=False,
        )
    writer.write_bundle(bundle)
    brief_path = writer.write_brief(bundle)
    return {
        "iteration_dir": writer.iteration_dir,
        "bundle": writer.bundle_path,
        "integration_brief": brief_path,
    }


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------
def _runtime_metadata(execution: Any) -> dict[str, Any]:
    """Whitelisted runtime metadata from the executor report (no transcript)."""
    if execution is None:
        return {"outcome": "REUSED"}
    # Unwrap enum-or-plain outcome values (Enum and .value doubles both work).
    outcome = getattr(execution.outcome, "value", execution.outcome)
    return {
        "outcome": str(outcome),
        "duration_s": round(float(getattr(execution, "duration_s", 0.0)), 6),
        "state_advanced": bool(getattr(execution, "state_advanced", False)),
    }


def _atomic_write_json(target: Path, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    data = (text + "\n").encode("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=".tmp-artifact-", suffix=".part"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise


def _read_bytes_if_exists(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        # An unreadable existing artifact is a conflict, not a silence.
        raise ArtifactConflictError(
            "artifact_unreadable",
            f"existing artifact cannot be read: {path}",
        )


def _validate_existing_review_artifact(
    raw: bytes,
    *,
    role: ProposalRole,
    iteration_number: int,
    proposal_hash: str,
) -> ProposalReviewResult:
    """Accept an existing artifact ONLY for the SAME identity + valid result.

    Implements the safe-resume contract: same iteration, same role, same
    proposal hash, parseable back into a valid :class:`ProposalReviewResult`
    (returned).  Everything else fails closed with
    :class:`ArtifactConflictError`.
    """
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactConflictError(
            "artifact_corrupt", f"{role.value} artifact is not valid JSON: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ArtifactConflictError(
            "artifact_corrupt", f"{role.value} artifact is not a JSON object."
        )
    checks = (
        (data.get("iteration_number"), iteration_number, "iteration_number"),
        (data.get("reviewer_role"), role.value, "reviewer_role"),
        (data.get("proposal_hash"), proposal_hash, "proposal_hash"),
    )
    for actual, expected, label in checks:
        if actual != expected:
            raise ArtifactConflictError(
                "artifact_identity_conflict",
                f"existing {role.value} artifact {label} is {actual!r}, "
                f"expected {expected!r}.",
            )
    try:
        parsed = ProposalReviewResult.from_dict(data.get("result") or {})
    except (ValueError, TypeError) as exc:
        raise ArtifactConflictError(
            "artifact_invalid_result",
            f"existing {role.value} artifact carries an invalid result: {exc}",
        ) from exc
    if parsed.reviewer_role is not role:
        raise ArtifactConflictError(
            "artifact_invalid_result",
            f"existing {role.value} artifact carries result role "
            f"{parsed.reviewer_role.value}.",
        )
    return parsed
