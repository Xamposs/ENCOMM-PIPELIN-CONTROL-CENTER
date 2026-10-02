"""Post-integration version freeze — exact-byte evidence of the NEW revision.

Mirrors :mod:`proposal_runtime.version_freeze` (the pre-review freeze) for
the AFTER side of an integration: after the runtime has atomically replaced
``03_PROPOSAL/MASTER_PROPOSAL.md``, the NEW exact bytes are frozen as::

    06_VERSIONS/iteration_NNN_post_integration.md      (the exact bytes)
    06_VERSIONS/iteration_NNN_post_integration.json    (sidecar metadata)

Hard rules (same discipline as the pre-review freeze):

* the frozen ``.md`` bytes are EXACTLY the bytes written into
  ``MASTER_PROPOSAL.md`` — the caller passes them; they are never re-read
  from disk for the freeze (no post-write race can masquerade as evidence);
* UTF-8 and newline handling are byte-preserved; the frozen copy is a byte
  image, never a re-rendering;
* freeze + sidecar writes are ATOMIC (same-directory temp + ``os.replace``);
* an existing frozen version is NEVER overwritten.  Identical bytes AND an
  identical sidecar contract allow safe reuse (a crash between the master
  write and the freeze); any conflict stops the operation — evidence is
  never clobbered.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..proposal.fingerprint import PROPOSAL_HASH_ALGORITHM
from .version_freeze import VersionFreezeError, format_iteration_name

__all__ = [
    "POST_INTEGRATION_MD_SUFFIX",
    "POST_INTEGRATION_SIDECAR_SUFFIX",
    "freeze_post_integration_version",
]

#: Canonical zero-padded post-integration file suffixes.
POST_INTEGRATION_MD_SUFFIX = "_post_integration.md"
POST_INTEGRATION_SIDECAR_SUFFIX = "_post_integration.json"


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    """Write ``data`` to ``target`` atomically (same-directory temp + replace)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=".tmp-postfreeze-", suffix=".part"
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


def _read_bytes_if_exists(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _canonical_sidecar_bytes(contract: dict[str, Any]) -> bytes:
    text = json.dumps(contract, indent=2, sort_keys=True, ensure_ascii=False)
    return (text + "\n").encode("utf-8")


def freeze_post_integration_version(
    *,
    workspace: Path,
    iteration_number: int,
    proposal_revision: str,
    previous_hash: str,
    new_bytes: bytes,
    new_hash: str,
    integration_artifact_path: str,
    pre_review_version_path: str = "",
) -> Path:
    """Freeze the EXACT post-integration bytes for ONE iteration.

    ``new_bytes`` MUST be the bytes written into MASTER_PROPOSAL.md and
    ``new_hash`` their SHA-256 (the caller computed both from the same
    write — the freeze never re-reads the live file).  Returns the frozen
    ``.md`` path.

    Safe reuse: when the frozen ``.md`` already exists with the SAME bytes
    AND the sidecar describes the SAME contract, the freeze is reported as
    already completed instead of rewritten.  Any other existing content is
    a CONFLICT (:class:`VersionFreezeError`, never a silent overwrite).
    """
    workspace = Path(workspace)
    if not new_bytes:
        raise VersionFreezeError(
            "empty_post_bytes",
            "post-integration freeze requires the exact written bytes.",
        )
    if not str(new_hash or "").strip():
        raise VersionFreezeError(
            "missing_post_hash",
            "post-integration freeze requires the new proposal hash.",
        )

    stem = format_iteration_name(iteration_number)
    versions_dir = workspace / "06_VERSIONS"
    md_path = versions_dir / f"{stem}{POST_INTEGRATION_MD_SUFFIX}"
    sidecar_path = versions_dir / f"{stem}{POST_INTEGRATION_SIDECAR_SUFFIX}"

    contract = {
        "hash_algorithm": PROPOSAL_HASH_ALGORITHM,
        "integration_artifact_path": str(integration_artifact_path),
        "iteration_number": int(iteration_number),
        "master_proposal_relpath": "03_PROPOSAL/MASTER_PROPOSAL.md",
        "previous_proposal_hash": str(previous_hash),
        "proposal_hash": str(new_hash),
        "proposal_revision": str(proposal_revision),
        "source_pre_review_version_path": str(pre_review_version_path or ""),
    }

    existing_md = _read_bytes_if_exists(md_path)
    existing_sidecar = _read_bytes_if_exists(sidecar_path)
    if existing_md is not None or existing_sidecar is not None:
        # Any prior evidence must match the WHOLE contract — both files,
        # exact bytes, exact metadata.  A partial match is a conflict.
        expected_sidecar = _canonical_sidecar_bytes(contract)
        if existing_md != new_bytes or existing_sidecar != expected_sidecar:
            raise VersionFreezeError(
                "post_version_conflict",
                f"{md_path.name} already exists with different content; "
                "refusing to overwrite a frozen post-integration version.",
            )
        return md_path  # exact safe reuse

    _atomic_write_bytes(md_path, new_bytes)
    _atomic_write_json(sidecar_path, contract)
    return md_path
