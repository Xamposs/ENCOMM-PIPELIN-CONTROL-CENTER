"""Version freeze adapter — exact-bytes iteration snapshots in 06_VERSIONS/.

Before the FIRST reviewer of an iteration launches, the review loop freezes
the EXACT proposal revision this cycle reviews::

    06_VERSIONS/iteration_001_pre_review.md      (the exact bytes)
    06_VERSIONS/iteration_001_pre_review.json    (sidecar metadata)

Hard rules:

* the frozen ``.md`` bytes are EXACTLY the bytes whose SHA-256 is the cycle
  hash (the freeze copy and the hash are computed from the same read);
* UTF-8 and newline handling are byte-preserved (written with
  ``newline=""``); the frozen copy is a byte image, never a re-rendering;
* freeze + sidecar writes are ATOMIC (temporary file in the same directory,
  then ``os.replace``);
* an existing frozen version is NEVER overwritten.  Identical bytes AND an
  identical sidecar contract allow safe reuse (crash between freeze and
  review); any conflict stops the cycle — operator evidence is never
  clobbered.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..proposal.fingerprint import (
    MAX_PROPOSAL_BYTES,
    PROPOSAL_HASH_ALGORITHM,
    ProposalFingerprintError,
)

__all__ = [
    "VERSION_FREEZE_MD_SUFFIX",
    "VERSION_FREEZE_SIDECAR_SUFFIX",
    "VersionFreezeError",
    "format_iteration_name",
    "freeze_pre_review_version",
]

#: Canonical zero-padded iteration directory/file stem (``iteration_001``).
VERSION_FREEZE_MD_SUFFIX = "_pre_review.md"
VERSION_FREEZE_SIDECAR_SUFFIX = "_pre_review.json"


class VersionFreezeError(RuntimeError):
    """A version freeze refused to proceed (missing source or conflict)."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"[{reason}] {message}")


def format_iteration_name(iteration_number: int) -> str:
    """Zero-padded iteration stem, e.g. ``iteration_001``.

    Refuses non-positive or absurdly large iteration numbers (fail closed —
    the zero-padding width is part of the artifact contract).
    """
    if (
        not isinstance(iteration_number, int)
        or isinstance(iteration_number, bool)
        or iteration_number < 1
        or iteration_number > 999
    ):
        raise VersionFreezeError(
            "invalid_iteration_number",
            f"iteration_number must be an integer in 1..999, got {iteration_number!r}.",
        )
    return f"iteration_{iteration_number:03d}"


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    """Write ``data`` to ``target`` atomically (same-directory temp + replace)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=".tmp-freeze-", suffix=".part"
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


def _atomic_write_json(target: Path, payload: dict[str, Any]) -> None:
    """Deterministic JSON: indent=2, sort_keys, UTF-8, LF, newline at EOF."""
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    _atomic_write_bytes(target, (text + "\n").encode("utf-8"))


def freeze_pre_review_version(
    *,
    workspace: Path,
    master_proposal_path: Path,
    iteration_number: int,
    proposal_revision: str,
    expected_hash: str | None = None,
) -> tuple[Path, str]:
    """Freeze the exact proposal bytes for ONE review iteration.

    Reads the master proposal ONCE, hashes those exact bytes (the cycle
    hash), writes ``06_VERSIONS/<iteration>_pre_review.md`` with those exact
    bytes plus the JSON sidecar.  Returns ``(frozen_md_path, cycle_hash)``.

    ``expected_hash`` (optional) re-verifies an already-decided cycle hash
    against the freshly read bytes — a mismatch is a mutation-class failure.

    Safe reuse: when the frozen ``.md`` already exists with the SAME bytes
    AND the sidecar describes the SAME contract (iteration, revision, hash,
    algorithm, source relpath), the freeze is reported as already completed
    instead of rewritten.  Any other existing content is a CONFLICT —
    :class:`VersionFreezeError`, never a silent overwrite.
    """
    workspace = Path(workspace)
    try:
        data = Path(master_proposal_path).read_bytes()
    except OSError as exc:
        raise VersionFreezeError(
            "unreadable_master", f"cannot read {master_proposal_path}: {exc}"
        ) from exc

    cycle_hash = proposal_fingerprint_from_bytes(data)
    if expected_hash is not None and cycle_hash != expected_hash:
        raise VersionFreezeError(
            "hash_mismatch",
            f"master proposal hash changed under the cycle: expected "
            f"{expected_hash}, read {cycle_hash}.",
        )

    stem = format_iteration_name(iteration_number)
    versions_dir = workspace / "06_VERSIONS"
    md_path = versions_dir / f"{stem}{VERSION_FREEZE_MD_SUFFIX}"
    sidecar_path = versions_dir / f"{stem}{VERSION_FREEZE_SIDECAR_SUFFIX}"

    contract = {
        "hash_algorithm": PROPOSAL_HASH_ALGORITHM,
        "iteration_number": iteration_number,
        "master_proposal_relpath": "03_PROPOSAL/MASTER_PROPOSAL.md",
        "proposal_hash": cycle_hash,
        "proposal_revision": str(proposal_revision),
    }

    existing_md = _read_bytes_if_exists(md_path)
    existing_sidecar = _read_bytes_if_exists(sidecar_path)
    if existing_md is not None or existing_sidecar is not None:
        # Any prior freeze evidence must match the WHOLE contract — both
        # files, exact bytes, exact metadata.  A partial match is a conflict.
        expected_sidecar = _canonical_sidecar_bytes(contract)
        if existing_md != data or existing_sidecar != expected_sidecar:
            raise VersionFreezeError(
                "version_conflict",
                f"{md_path.name} already exists with different content; "
                "refusing to overwrite a frozen proposal version.",
            )
        return md_path, cycle_hash  # exact safe reuse

    _atomic_write_bytes(md_path, data)
    _atomic_write_json(sidecar_path, contract)
    return md_path, cycle_hash


def proposal_fingerprint_from_bytes(data: bytes) -> str:
    """SHA-256 over the exact bytes — the D-057 algorithm on a byte string.

    Byte-identical to ``proposal_fingerprint`` on the same content (same
    function, same oversize guard): the frozen copy's digest therefore equals
    the digest the executor computes over the live file.
    """
    if len(data) > MAX_PROPOSAL_BYTES:
        raise ProposalFingerprintError(
            "oversized_file",
            f"proposal file exceeds {MAX_PROPOSAL_BYTES} bytes ({len(data)}); "
            "refusing to fingerprint.",
        )
    return hashlib.sha256(data).hexdigest()


def _read_bytes_if_exists(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _canonical_sidecar_bytes(contract: dict[str, Any]) -> bytes:
    text = json.dumps(contract, indent=2, sort_keys=True, ensure_ascii=False)
    return (text + "\n").encode("utf-8")
