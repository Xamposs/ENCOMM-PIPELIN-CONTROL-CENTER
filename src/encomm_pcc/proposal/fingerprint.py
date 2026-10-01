"""Canonical proposal fingerprint — the ONE proposal_hash algorithm.

Session 012 defined the ``proposal_hash`` *field* (``ProposalIterationRecord``)
but deliberately left the hash function undefined.  This module defines the
canonical algorithm every future session must reuse:

    **SHA-256 over the EXACT file bytes** of ``03_PROPOSAL/MASTER_PROPOSAL.md``.

Hard rules:

* the raw file bytes are hashed — no newline normalisation, no text
  rewriting, no encoding transcoding, no mutation of any kind;
* the digest is returned as lowercase hexadecimal (``hexdigest()`` form);
* a missing file fails EXPLICITLY (:class:`ProposalFingerprintError`) —
  there is no hash-of-nothing;
* reading is bounded: an absurdly large file is refused rather than read
  into memory unbounded;
* repeated reads of identical bytes always produce the identical digest
  (deterministic by construction; pinned by tests).

The runtime review executor fingerprints the master proposal BEFORE and AFTER
every reviewer execution; a digest change proves a write-authority violation
(reviewers are read-only) and fails the operation closed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

__all__ = [
    "MAX_PROPOSAL_BYTES",
    "PROPOSAL_HASH_ALGORITHM",
    "ProposalFingerprintError",
    "proposal_fingerprint",
]

#: Identifier of the canonical algorithm (recorded in reports/evidence so a
#: future re-hash can detect an algorithm change instead of silently mixing
#: incompatible digests).
PROPOSAL_HASH_ALGORITHM = "sha256-exact-bytes-v1"

#: Absolute cap on the master proposal size we are willing to read for
#: hashing.  A real EIC/Horizon proposal is orders of magnitude below this;
#: anything larger is treated as a mistake and refused (fail closed) instead
#: of silently slurping unbounded bytes.
MAX_PROPOSAL_BYTES = 20_000_000


class ProposalFingerprintError(OSError):
    """Raised when the canonical proposal fingerprint cannot be computed.

    ``reason`` is a short stable machine tag; ``message`` is human-readable.
    Subclasses :class:`OSError` so callers that already handle IO failures
    catch it naturally.
    """

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"[{reason}] {message}")


def proposal_fingerprint(path: Path) -> str:
    """Return the canonical SHA-256 fingerprint of the file at ``path``.

    Hashes the EXACT file bytes (``read_bytes``) — never the decoded text —
    so the digest is independent of platform newline handling and of any
    text normalisation.  Deterministic across repeated reads of identical
    bytes; a single byte change always changes the digest.

    Raises :class:`ProposalFingerprintError` (never a bare FileNotFoundError)
    when the path does not exist, is not a file, cannot be read, or exceeds
    :data:`MAX_PROPOSAL_BYTES`.
    """
    target = Path(path)
    if not target.exists():
        raise ProposalFingerprintError(
            "missing_file", f"proposal file does not exist: {target}"
        )
    if not target.is_file():
        raise ProposalFingerprintError(
            "not_a_file", f"proposal path is not a regular file: {target}"
        )
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise ProposalFingerprintError(
            "unreadable_file", f"proposal file could not be read: {target} ({exc})"
        ) from exc
    if len(data) > MAX_PROPOSAL_BYTES:
        raise ProposalFingerprintError(
            "oversized_file",
            f"proposal file exceeds {MAX_PROPOSAL_BYTES} bytes ({len(data)}); "
            "refusing to fingerprint.",
        )
    return hashlib.sha256(data).hexdigest()
