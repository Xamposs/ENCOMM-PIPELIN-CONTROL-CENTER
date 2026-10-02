"""Next-iteration revision handoff — ``05_CONTROL/NEXT_ITERATION.json``.

When integration changed the proposal and the review-freshness gate sends
``HARD_GATE_VALIDATION`` to ``REVISION_REQUIRED``, the runtime prepares the
next review iteration's handoff: a STRUCTURED, deterministic JSON document
carrying everything the next review cycle needs (iteration numbers, the
reviewed hash, the revised hash, the previous findings, the unresolved
integration items and the integration summary).

This is durable DATA, not model-generated prose.  Atomic write,
conflict-fail-closed (an existing handoff is accepted only when it is
byte-identical; anything else stops the run — evidence is never clobbered).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..proposal.integration_models import ProposalIntegrationResult

__all__ = [
    "NEXT_ITERATION_FILENAME",
    "REVISION_HANDOFF_SCHEMA",
    "NextIterationHandoffError",
    "build_next_iteration_payload",
    "write_next_iteration_handoff",
]

#: Canonical control-file name.
NEXT_ITERATION_FILENAME = "NEXT_ITERATION.json"

#: Schema identifier persisted inside the handoff document.
REVISION_HANDOFF_SCHEMA = "encomm-pcc.revision-handoff/v1"


class NextIterationHandoffError(RuntimeError):
    """The next-iteration handoff refused to proceed (fail closed)."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"[{reason}] {message}")


def build_next_iteration_payload(
    *,
    previous_iteration_number: int,
    previous_reviewed_hash: str,
    revised_proposal_hash: str,
    integration_result: ProposalIntegrationResult,
    integration_summary: str = "",
    previous_findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the structured handoff payload for iteration N+1.

    ``previous_findings`` is the previous iteration's aggregated findings
    (list of the bundle's ``findings`` ``to_dict`` entries — the caller
    passes them from the review bundle; empty when the caller has none).
    """
    if int(previous_iteration_number) < 1:
        raise NextIterationHandoffError(
            "invalid_previous_iteration",
            f"previous_iteration_number must be a positive integer, got "
            f"{previous_iteration_number!r}.",
        )
    if not str(previous_reviewed_hash or "").strip():
        raise NextIterationHandoffError(
            "missing_previous_hash",
            "previous_reviewed_hash is required.",
        )
    if not str(revised_proposal_hash or "").strip():
        raise NextIterationHandoffError(
            "missing_revised_hash",
            "revised_proposal_hash is required.",
        )
    return {
        "schema": REVISION_HANDOFF_SCHEMA,
        "next_iteration_number": int(previous_iteration_number) + 1,
        "previous_iteration_number": int(previous_iteration_number),
        "previous_reviewed_hash": str(previous_reviewed_hash).strip().lower(),
        "revised_proposal_hash": str(revised_proposal_hash).strip().lower(),
        "previous_findings": list(previous_findings or []),
        "unresolved_items": [
            i.to_dict() for i in integration_result.unresolved_items
        ],
        "integration_summary": str(
            integration_summary or integration_result.summary
        ),
    }


def write_next_iteration_handoff(
    *,
    workspace: Path,
    payload: dict[str, Any],
) -> Path:
    """Persist ``05_CONTROL/NEXT_ITERATION.json`` — conflict-fail-closed.

    An existing handoff is accepted ONLY when its canonical bytes equal the
    payload about to be written (identical safe reuse).  ANY other existing
    content is a :class:`NextIterationHandoffError`; the run stops instead
    of clobbering the operator's control state.
    """
    workspace = Path(workspace)
    target = workspace / "05_CONTROL" / NEXT_ITERATION_FILENAME
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    expected = (text + "\n").encode("utf-8")
    try:
        existing = target.read_bytes()
    except FileNotFoundError:
        existing = None
    except OSError as exc:
        raise NextIterationHandoffError(
            "handoff_unreadable",
            f"existing handoff cannot be read: {target} ({exc})",
        ) from exc
    if existing is not None:
        if existing != expected:
            raise NextIterationHandoffError(
                "handoff_conflict",
                f"{target} already exists with different content; refusing "
                "to overwrite the revision handoff.",
            )
        return target  # identical safe reuse
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=".tmp-handoff-", suffix=".part"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(expected)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise NextIterationHandoffError(
            "handoff_write_failed", f"atomic write failed: {exc}"
        ) from exc
    return target
