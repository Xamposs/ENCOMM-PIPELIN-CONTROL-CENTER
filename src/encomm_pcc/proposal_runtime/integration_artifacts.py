"""Durable integration artifacts — the 04_REVIEWS/ integration writer.

Writes the structured evidence of ONE ORCHESTRATOR integration::

    04_REVIEWS/iteration_NNN/integration_result.json

Hard rules (same discipline as ``review_artifacts.py``, ADR D-060):

* STRUCTURED parsed information only — the raw model transcript and the raw
  envelope text are NEVER persisted (no excerpt of the raw answer reaches
  disk on a successful integration);
* deterministic JSON: ``indent=2``, ``sort_keys=True``, UTF-8, LF newlines,
  newline at EOF;
* ATOMIC writes: temporary file in the SAME directory, then ``os.replace``;
* FAIL-CLOSED over existing evidence: an existing ``integration_result.json``
  is accepted ONLY when it describes the EXACT same contract (same
  iteration, same input hash, same output hash, same outcome — the
  idempotent-retry signal); everything else is an
  :class:`ArtifactConflictError` that stops the run.  Evidence is never
  clobbered.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..proposal.enums import ProposalRole
from ..proposal.integration_models import ProposalIntegrationResult
from .review_artifacts import ArtifactConflictError

__all__ = [
    "INTEGRATION_RESULT_FILENAME",
    "INTEGRATION_RESULT_SCHEMA",
    "write_integration_result",
]

#: Schema identifier persisted inside ``integration_result.json``.
INTEGRATION_RESULT_SCHEMA = "encomm-pcc.integration-result/v1"

#: Canonical artifact filename inside ``04_REVIEWS/iteration_NNN/``.
INTEGRATION_RESULT_FILENAME = "integration_result.json"


def integration_result_path(workspace: Path, iteration_number: int) -> Path:
    """Canonical path of the iteration's ``integration_result.json``."""
    return (
        Path(workspace)
        / "04_REVIEWS"
        / f"iteration_{int(iteration_number):03d}"
        / INTEGRATION_RESULT_FILENAME
    )


def build_integration_result_payload(
    *,
    iteration_number: int,
    proposal_revision: str,
    input_hash: str,
    output_hash: str,
    result: ProposalIntegrationResult,
    runtime_outcome: str,
    driver_id: str = "",
    session_id: str | None = None,
    duration_s: float = 0.0,
) -> dict[str, Any]:
    """Build the whitelisted STRUCTURED payload (no transcript, no raw text).

    The parsed integration result's ``revised_proposal`` is deliberately
    NOT embedded: the revised content's durable home is
    ``03_PROPOSAL/MASTER_PROPOSAL.md`` + the ``06_VERSIONS/`` freeze —
    duplicating it here would create two competing copies of the proposal
    bytes.
    """
    return {
        "schema": INTEGRATION_RESULT_SCHEMA,
        "iteration_number": int(iteration_number),
        "proposal_revision": str(proposal_revision),
        "input_proposal_hash": str(input_hash),
        "output_proposal_hash": str(output_hash),
        "hash_algorithm": "sha256-exact-bytes-v1",
        "integration_result": {
            "summary": result.summary,
            "applied_items": [i.to_dict() for i in result.applied_items],
            "rejected_items": [i.to_dict() for i in result.rejected_items],
            "unresolved_items": [i.to_dict() for i in result.unresolved_items],
        },
        "runtime": {
            "outcome": str(runtime_outcome),
            "driver_id": str(driver_id),
            "session_id": session_id,
            "duration_s": round(float(duration_s), 6),
            "orchestrator_role": ProposalRole.ORCHESTRATOR.value,
        },
    }


def _atomic_write_json(target: Path, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    data = (text + "\n").encode("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=".tmp-integration-", suffix=".part"
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


def write_integration_result(
    *,
    workspace: Path,
    payload: dict[str, Any],
) -> Path:
    """Persist ``integration_result.json`` — conflict-fail-closed.

    An existing artifact is accepted ONLY when its canonical bytes equal
    the payload about to be written (identical safe reuse — the crash-
    between-write-and-freeze signal).  ANY other existing content is an
    :class:`ArtifactConflictError`; the run stops instead of clobbering.
    """
    target = integration_result_path(workspace, payload["iteration_number"])
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    expected = (text + "\n").encode("utf-8")
    try:
        existing = target.read_bytes()
    except FileNotFoundError:
        existing = None
    except OSError as exc:
        raise ArtifactConflictError(
            "integration_artifact_unreadable",
            f"existing integration artifact cannot be read: {target} ({exc})",
        ) from exc
    if existing is not None:
        if existing != expected:
            raise ArtifactConflictError(
                "integration_artifact_conflict",
                f"{target} already exists with different content; refusing "
                "to overwrite integration evidence.",
            )
        return target  # identical safe reuse
    _atomic_write_json(target, payload)
    return target
