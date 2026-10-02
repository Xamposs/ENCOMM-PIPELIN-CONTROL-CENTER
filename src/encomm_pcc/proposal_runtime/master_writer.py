"""The ONE legitimate Proposal Mode master-proposal writer — runtime adapter.

Session 015 gives the ORCHESTRATOR's integration decision its deterministic
filesystem authority.  The model NEVER touches the file: it returns the
complete revised proposal inside the strict envelope, and THIS module owns
the controlled atomic replacement of ``03_PROPOSAL/MASTER_PROPOSAL.md``
(ADR: model returns content; runtime owns the write).

Hard rules:

* usable ONLY while the state machine sits exactly at ``INTEGRATION`` —
  any other phase refuses with :class:`MasterProposalWriteError` BEFORE any
  filesystem effect;
* the caller supplies the expected current hash (the integration input
  hash); immediately before the replacement the live file is re-fingerprinted
  and MUST still equal it — a changed file is never overwritten
  (``stale_input``, the newer on-disk content wins and is surfaced);
* UTF-8 bytes, ``newline=""`` semantics via raw bytes — the revised text is
  written EXACTLY as returned except for the ONE documented canonical EOF
  policy: the content is written with exactly one trailing ``\\n`` appended
  when not already present (idempotent; never strips or rewrites anything
  else);
* atomic: temporary file in ``03_PROPOSAL/`` (same directory), flush +
  fsync, then ``os.replace`` — a failed write leaves either the old file
  or the new file, never a partial one;
* on success the NEW exact-byte SHA-256 is computed from the written bytes
  (never re-derived from a re-read that could race) and returned together
  with whether the content actually changed.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Optional

from ..proposal.enums import ProposalPhase
from ..proposal.fingerprint import (
    MAX_PROPOSAL_BYTES,
    PROPOSAL_HASH_ALGORITHM,
    ProposalFingerprintError,
    proposal_fingerprint,
)
from ..proposal.state_machine import ProposalStateMachine
from ..proposal.workspace import MASTER_PROPOSAL_RELPATH

__all__ = [
    "MASTER_PROPOSAL_FILENAME",
    "MasterProposalWriteError",
    "MasterProposalWriteReport",
    "replace_master_proposal",
]

#: The file this module — and ONLY an INTEGRATION-phase operation through
#: this module — is allowed to replace.
MASTER_PROPOSAL_FILENAME = "MASTER_PROPOSAL.md"


class MasterProposalWriteError(RuntimeError):
    """The master-proposal replacement refused to proceed (fail closed).

    ``reason`` is a short stable machine tag; the message is bounded and
    human-readable.  No failure path leaves a partially written file.
    """

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"[{reason}] {message}")


class MasterProposalWriteReport:
    """Operational evidence of ONE master-proposal replacement."""

    __slots__ = (
        "master_proposal_path",
        "previous_hash",
        "new_hash",
        "hash_algorithm",
        "bytes_written",
        "changed",
    )

    def __init__(
        self,
        *,
        master_proposal_path: Path,
        previous_hash: str,
        new_hash: str,
        bytes_written: int,
        changed: bool,
    ) -> None:
        self.master_proposal_path = master_proposal_path
        self.previous_hash = previous_hash
        self.new_hash = new_hash
        self.hash_algorithm = PROPOSAL_HASH_ALGORITHM
        self.bytes_written = bytes_written
        self.changed = changed

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"MasterProposalWriteReport(path={self.master_proposal_path}, "
            f"changed={self.changed}, new_hash={self.new_hash})"
        )


def _canonical_eof(data: bytes) -> bytes:
    """The ONE documented canonical EOF policy.

    The revised text is preserved EXACTLY as returned except that the file
    ends with exactly one ``\\n`` (a missing terminal newline is appended;
    an existing one is left alone; multiple trailing newlines are content
    and are never stripped).  Idempotent by construction.
    """
    if not data.endswith(b"\n"):
        return data + b"\n"
    return data


def replace_master_proposal(
    *,
    workspace: Path,
    state_machine: ProposalStateMachine,
    expected_current_hash: str,
    revised_proposal_text: str,
) -> MasterProposalWriteReport:
    """Atomically replace ``MASTER_PROPOSAL.md`` with the revised text.

    Fails closed (raising :class:`MasterProposalWriteError`) when the state
    machine is not at ``INTEGRATION``, when the revised text is empty,
    oversized or not text, or when the live file no longer matches
    ``expected_current_hash``.  On success returns the
    :class:`MasterProposalWriteReport` with the previous and new hashes.
    """
    workspace = Path(workspace)
    if state_machine.phase is not ProposalPhase.INTEGRATION:
        raise MasterProposalWriteError(
            "phase_not_integration",
            f"MASTER_PROPOSAL write authority exists ONLY during INTEGRATION "
            f"(the state machine is at {state_machine.phase.value}).",
        )
    if not isinstance(revised_proposal_text, str) or not revised_proposal_text.strip():
        raise MasterProposalWriteError(
            "empty_revised_proposal",
            "revised_proposal_text must be non-empty text.",
        )
    proposed_bytes = revised_proposal_text.encode("utf-8")
    if len(proposed_bytes) > MAX_PROPOSAL_BYTES:
        raise MasterProposalWriteError(
            "oversized_revised_proposal",
            f"revised proposal exceeds {MAX_PROPOSAL_BYTES} bytes "
            f"({len(proposed_bytes)}); refusing to write.",
        )
    if not str(expected_current_hash or "").strip():
        raise MasterProposalWriteError(
            "missing_expected_hash",
            "expected_current_hash is required.",
        )

    master_path = workspace / "03_PROPOSAL" / MASTER_PROPOSAL_FILENAME
    try:
        current_hash = proposal_fingerprint(master_path)
    except ProposalFingerprintError as exc:
        raise MasterProposalWriteError("fingerprint_failed", str(exc)) from exc
    if current_hash != str(expected_current_hash).strip().lower():
        raise MasterProposalWriteError(
            "stale_input",
            f"MASTER_PROPOSAL changed since the integration input (expected "
            f"{expected_current_hash.strip().lower()}, found {current_hash}); "
            "refusing to overwrite the newer file.",
        )

    new_bytes = _canonical_eof(proposed_bytes)
    new_hash = hashlib.sha256(new_bytes).hexdigest()
    changed = new_hash != current_hash

    master_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(master_path.parent), prefix=".tmp-master-", suffix=".part"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(new_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, master_path)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise MasterProposalWriteError(
            "write_failed", f"atomic replacement failed: {exc}"
        ) from exc

    return MasterProposalWriteReport(
        master_proposal_path=master_path,
        previous_hash=current_hash,
        new_hash=new_hash,
        bytes_written=len(new_bytes),
        changed=changed,
    )
