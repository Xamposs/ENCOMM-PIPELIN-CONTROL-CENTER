"""Durable hard-gate artifacts — the Session 016 file-writing adapter.

Two persistence surfaces, with DELIBERATELY different conflict policies:

* ``04_REVIEWS/iteration_NNN/hard_gates.json`` — the IMMUTABLE per-iteration
  audit record.  Written once per run; an existing artifact is accepted ONLY
  when byte-identical (safe reuse); ANY other existing content is a typed
  conflict that stops the run (same contract as every other 04_REVIEWS
  artifact, D-060).
* ``05_CONTROL/HARD_GATES.json`` and ``05_CONTROL/HARD_GATE_FEEDBACK.json``
  — CURRENT-STATE control files (the latest snapshot / the current revision
  demand).  They are updated ATOMICALLY with the current run's content by
  the explicitly documented current-state policy (brief §23): a zero-byte
  seed is an uninitialised control file and is replaced; existing real
  content is replaced atomically (temp + fsync + ``os.replace``) because the
  file's MEANING is "latest", while the immutable per-iteration record
  remains the append-only audit history.

All JSON is deterministic (``indent=2``, ``sort_keys=True``, UTF-8, LF,
newline at EOF).  No timestamps, no transcripts, no model/provider metadata.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..proposal.enums import HARD_GATE_IDS_TUPLE
from ..proposal.hard_gates import (
    EVIDENCE_DOCUMENT_FILENAME,
    HardGateDisposition,
    HardGateFailureClass,
    HardGateRunResult,
)

__all__ = [
    "HARD_GATE_FEEDBACK_FILENAME",
    "HARD_GATE_FEEDBACK_SCHEMA",
    "HARD_GATES_ARTIFACT_SCHEMA",
    "HARD_GATES_SNAPSHOT_FILENAME",
    "HardGateArtifactConflictError",
    "build_feedback_payload",
    "build_run_artifact",
    "file_sha256",
    "update_latest_hard_gates_snapshot",
    "write_hard_gate_feedback",
    "write_iteration_hard_gates",
]

#: Schema identifier of the durable per-iteration run artifact.
HARD_GATES_ARTIFACT_SCHEMA = "encomm-pcc.hard-gate-run/v1"

#: Schema identifier of the revision feedback document.
HARD_GATE_FEEDBACK_SCHEMA = "encomm-pcc.hard-gate-feedback/v1"

#: Canonical file names.
HARD_GATES_SNAPSHOT_FILENAME = "HARD_GATES.json"
HARD_GATE_FEEDBACK_FILENAME = "HARD_GATE_FEEDBACK.json"


class HardGateArtifactConflictError(RuntimeError):
    """An existing per-iteration artifact conflicts — never clobbered."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"[{reason}] {message}")


def file_sha256(path: Path) -> str:
    """Deterministic SHA-256 over the exact file bytes (evidence fingerprint)."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_run_artifact(
    *,
    run_result: HardGateRunResult,
    evidence_document_path: Path,
) -> dict[str, Any]:
    """Build the deterministic per-iteration artifact payload (§23)."""
    if tuple(e.gate_id for e in run_result.evaluations) != HARD_GATE_IDS_TUPLE:
        # Defensive: never persist a non-canonical run record.
        raise ValueError("run result does not carry the canonical gate set.")
    return {
        "schema": HARD_GATES_ARTIFACT_SCHEMA,
        "iteration_number": run_result.iteration_number,
        "proposal_hash": run_result.proposal_hash,
        "gate_results": [
            {
                "gate_id": e.gate_id,
                "status": e.status.value,
                "message": e.message,
                "evidence": e.evidence,
            }
            for e in run_result.evaluations
        ],
        "overall_outcome": run_result.disposition.value,
        "counts": run_result.counts,
        "evidence_fingerprints": {
            EVIDENCE_DOCUMENT_FILENAME: file_sha256(evidence_document_path),
        },
    }


def write_iteration_hard_gates(
    *,
    workspace: Path,
    iteration_number: int,
    artifact: dict[str, Any],
) -> Path:
    """Persist the IMMUTABLE per-iteration audit record (conflict-fail-closed).

    An existing artifact is accepted only when its canonical bytes equal the
    artifact about to be written (identical safe reuse); any other existing
    content is :class:`HardGateArtifactConflictError`.
    """
    workspace = Path(workspace)
    target = (
        workspace / "04_REVIEWS" / f"iteration_{int(iteration_number):03d}"
        / "hard_gates.json"
    )
    text = json.dumps(artifact, indent=2, sort_keys=True, ensure_ascii=False)
    expected = (text + "\n").encode("utf-8")
    try:
        existing = target.read_bytes()
    except FileNotFoundError:
        existing = None
    except OSError as exc:
        raise HardGateArtifactConflictError(
            "artifact_unreadable",
            f"existing hard-gate artifact cannot be read: {target} ({exc})",
        ) from exc
    if existing is not None:
        if existing != expected:
            raise HardGateArtifactConflictError(
                "artifact_content_conflict",
                f"{target} already exists with different content; the "
                "per-iteration audit record is never overwritten.",
            )
        return target  # identical safe reuse
    _atomic_write(target, expected)
    return target


def update_latest_hard_gates_snapshot(
    *,
    workspace: Path,
    artifact: dict[str, Any],
) -> Path:
    """Refresh ``05_CONTROL/HARD_GATES.json`` — the CURRENT-state snapshot.

    Documented current-state policy (brief §23): the snapshot always carries
    the latest run; updates are atomic; a zero-byte seed is an uninitialised
    control file.  The durable per-iteration artifact remains the immutable
    audit record.
    """
    workspace = Path(workspace)
    target = workspace / "05_CONTROL" / HARD_GATES_SNAPSHOT_FILENAME
    text = json.dumps(artifact, indent=2, sort_keys=True, ensure_ascii=False)
    _atomic_write(target, (text + "\n").encode("utf-8"))
    return target


def build_feedback_payload(*, run_result: HardGateRunResult) -> dict[str, Any]:
    """Build the structured ``HARD_GATE_FEEDBACK.json`` payload (§21).

    Carries ONLY deterministic remediation context derived directly from the
    validators (gate id, status, message, evidence pointer, failure class).
    No prose is generated, no model is involved.
    """
    failed = [
        {
            "gate_id": e.gate_id,
            "status": e.status.value,
            "message": e.message,
            "evidence": e.evidence,
            "failure_class": e.failure_class.value,
        }
        for e in run_result.evaluations
        if e.failure_class is HardGateFailureClass.PROPOSAL_ISSUE
    ]
    if not failed:
        # Defensive: feedback is only ever requested for real proposal issues.
        raise ValueError(
            "build_feedback_payload requires at least one PROPOSAL_ISSUE gate."
        )
    return {
        "schema": HARD_GATE_FEEDBACK_SCHEMA,
        "iteration_number": run_result.iteration_number,
        "proposal_hash": run_result.proposal_hash,
        "overall_outcome": run_result.disposition.value,
        "failed_gates": failed,
    }


def write_hard_gate_feedback(
    *,
    workspace: Path,
    payload: dict[str, Any],
) -> Path:
    """Persist ``05_CONTROL/HARD_GATE_FEEDBACK.json`` (current-state policy).

    The feedback is the CURRENT revision demand for the next iteration: it
    is written atomically with the latest run's content (same documented
    current-state policy as the HARD_GATES snapshot).
    """
    workspace = Path(workspace)
    target = workspace / "05_CONTROL" / HARD_GATE_FEEDBACK_FILENAME
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    _atomic_write(target, (text + "\n").encode("utf-8"))
    return target


def _atomic_write(target: Path, data: bytes) -> None:
    """Same-directory temp + fsync + ``os.replace`` (deterministic JSON)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=".tmp-hardgate-", suffix=".part"
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
