"""All-or-rollback Blueprint/Proposal PAIR commit (Session 021).

The Blueprint and Proposal are a VERSIONED PAIR: a committed iteration N
always carries BOTH documents at the same iteration.  A single
``os.replace`` is atomic for ONE file; the filesystem offers no magical
two-file atomic rename.  This module therefore implements HONEST
all-or-rollback semantics for the pair:

1. both model outputs were validated by the caller BEFORE this module is
   invoked (it never parses model text);
2. both outputs are STAGED to temporary files in their target directories
   (flush + fsync);
3. the current pair is snapshotted (exact in-memory bytes + hashes);
4. the pair is replaced (first the Blueprint, then the Proposal) — each
   via a same-directory atomic ``os.replace`` of the staged temp file;
5. if the SECOND replacement fails, the FIRST document is restored from
   its exact snapshot bytes (best-effort, re-verified by fingerprint);
6. ONLY after both replacements are durable is the pair manifest
   ``05_CONTROL/DOCUMENT_PAIR_STATE.json`` written (atomically,
   deterministically) — the manifest's existence IS the commit marker.

There is never an accepted durable state where Blueprint iteration N+1
and Proposal iteration N are considered a valid committed pair: before
the manifest write, the caller-visible state is "pair commit failed"
(with restoration evidence); after it, BOTH files hash to the manifest
values.

Schema ``encomm-pcc.document-pair-state/v1`` carries iteration, both
hashes, both previous hashes, a deterministic pair revision id and the
source pack id when available.

This module imports nothing from the coding pipeline, ``proposal_runtime``
or the UI, and holds no engine knowledge.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .enums import ProposalPhase
from .fingerprint import MAX_PROPOSAL_BYTES, ProposalFingerprintError
from .state_machine import ProposalStateMachine
from .workspace import MASTER_PROPOSAL_RELPATH

__all__ = [
    "DOCUMENT_PAIR_STATE_FILENAME",
    "DOCUMENT_PAIR_STATE_RELPATH",
    "DOCUMENT_PAIR_STATE_SCHEMA",
    "DocumentPairState",
    "DocumentPairStateError",
    "DocumentPairWriteReport",
    "commit_document_pair",
    "load_document_pair_state",
    "try_load_document_pair_state",
]

#: Durable pair manifest (relative to the workspace root).
DOCUMENT_PAIR_STATE_FILENAME = "DOCUMENT_PAIR_STATE.json"
DOCUMENT_PAIR_STATE_RELPATH = f"05_CONTROL/{DOCUMENT_PAIR_STATE_FILENAME}"

#: Schema identifier persisted inside the manifest; a mismatching schema on
#: an existing manifest is a conflict, never silently upgraded.
DOCUMENT_PAIR_STATE_SCHEMA = "encomm-pcc.document-pair-state/v1"

#: Living blueprint contract path (mirrors living_blueprint, without a
#: domain import cycle risk — the relpath is pinned by its own module).
CURRENT_BLUEPRINT_RELPATH = "00_SOURCE_OF_TRUTH/CURRENT_BLUEPRINT.md"

#: Files over 20 MB are refused for either document (same cap as the
#: canonical fingerprint — a real proposal/blueprint is far below).


class DocumentPairStateError(RuntimeError):
    """The pair commit refused or failed (fail closed).

    ``reason`` is a short stable machine tag; the message is bounded and
    human-readable.  When ``restored`` is true, the live pair was left at
    the pre-commit state (restoration verified by fingerprint).
    """

    def __init__(self, reason: str, message: str, *, restored: bool = False) -> None:
        self.reason = reason
        self.restored = restored
        super().__init__(f"[{reason}] {message}")


def _canonical_eof(data: bytes) -> bytes:
    """The ONE documented canonical EOF policy (same as the master writer)."""
    if not data.endswith(b"\n"):
        return data + b"\n"
    return data


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(slots=True)
class DocumentPairState:
    """Durable manifest of ONE committed Blueprint/Proposal pair."""

    iteration: int
    blueprint_hash: str
    proposal_hash: str
    previous_blueprint_hash: str
    previous_proposal_hash: str
    pair_revision_id: str
    source_pack_id: str = ""
    schema: str = DOCUMENT_PAIR_STATE_SCHEMA

    def __post_init__(self) -> None:
        if not isinstance(self.iteration, int) or isinstance(self.iteration, bool):
            raise DocumentPairStateError(
                "invalid_iteration", "iteration must be a plain integer."
            )
        if self.iteration < 1:
            raise DocumentPairStateError(
                "invalid_iteration", "iteration must be >= 1."
            )
        self.blueprint_hash = str(self.blueprint_hash)
        self.proposal_hash = str(self.proposal_hash)
        self.previous_blueprint_hash = str(self.previous_blueprint_hash)
        self.previous_proposal_hash = str(self.previous_proposal_hash)
        self.pair_revision_id = str(self.pair_revision_id)
        self.source_pack_id = str(self.source_pack_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "iteration": self.iteration,
            "blueprint_hash": self.blueprint_hash,
            "proposal_hash": self.proposal_hash,
            "previous_blueprint_hash": self.previous_blueprint_hash,
            "previous_proposal_hash": self.previous_proposal_hash,
            "pair_revision_id": self.pair_revision_id,
            "source_pack_id": self.source_pack_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DocumentPairState":
        if not isinstance(data, dict):
            raise DocumentPairStateError(
                "state_not_object", "DOCUMENT_PAIR_STATE.json is not a JSON object."
            )
        if data.get("schema") != DOCUMENT_PAIR_STATE_SCHEMA:
            raise DocumentPairStateError(
                "state_schema_conflict",
                f"DOCUMENT_PAIR_STATE.json carries schema "
                f"{data.get('schema')!r}, expected {DOCUMENT_PAIR_STATE_SCHEMA!r}.",
            )
        try:
            iteration = int(data.get("iteration") or 0)
        except (TypeError, ValueError) as exc:
            raise DocumentPairStateError(
                "invalid_iteration", "iteration is not an integer."
            ) from exc
        return cls(
            iteration=iteration,
            blueprint_hash=str(data.get("blueprint_hash") or ""),
            proposal_hash=str(data.get("proposal_hash") or ""),
            previous_blueprint_hash=str(data.get("previous_blueprint_hash") or ""),
            previous_proposal_hash=str(data.get("previous_proposal_hash") or ""),
            pair_revision_id=str(data.get("pair_revision_id") or ""),
            source_pack_id=str(data.get("source_pack_id") or ""),
        )


def _manifest_path(root: Path) -> Path:
    return Path(root).joinpath(*DOCUMENT_PAIR_STATE_RELPATH.split("/"))


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp-pairstate-")
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


def load_document_pair_state(root: Path) -> DocumentPairState:
    """Load the durable pair manifest — raises when absent/corrupt."""
    path = _manifest_path(root)
    if not path.is_file():
        raise DocumentPairStateError(
            "missing_state",
            f"{DOCUMENT_PAIR_STATE_RELPATH} does not exist; no pair has "
            "been committed yet.",
        )
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DocumentPairStateError(
            "state_unreadable", f"{DOCUMENT_PAIR_STATE_RELPATH}: {exc}"
        ) from exc
    return DocumentPairState.from_dict(data)


def try_load_document_pair_state(root: Path) -> Optional[DocumentPairState]:
    """Load the durable pair manifest or ``None`` when absent."""
    try:
        return load_document_pair_state(root)
    except DocumentPairStateError as exc:
        if exc.reason == "missing_state":
            return None
        raise


def _document_path(root: Path, relpath: str) -> Path:
    return Path(root).joinpath(*relpath.split("/"))


def _fingerprint(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise DocumentPairStateError(
            "fingerprint_failed", f"cannot read {path}: {exc}"
        ) from exc
    if len(data) > MAX_PROPOSAL_BYTES:
        raise DocumentPairStateError(
            "oversized_document",
            f"{path} exceeds {MAX_PROPOSAL_BYTES} bytes; refusing.",
        )
    return _sha256_bytes(data)


def _restore_document(path: Path, snapshot: Optional[bytes]) -> None:
    """Restore one document from its exact snapshot (best-effort, verified)."""
    if snapshot is None:
        # The document did not exist before the commit; remove the partial
        # write so the pre-commit shape is restored exactly.
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return
    _atomic_write_bytes(path, snapshot)


@dataclass(slots=True)
class DocumentPairWriteReport:
    """Operational evidence of ONE committed document pair."""

    iteration: int
    blueprint_path: str = ""
    proposal_path: str = ""
    previous_blueprint_hash: str = ""
    previous_proposal_hash: str = ""
    blueprint_hash: str = ""
    proposal_hash: str = ""
    blueprint_changed: bool = False
    proposal_changed: bool = False
    pair_revision_id: str = ""
    manifest_path: str = ""
    state_advanced: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "blueprint_path": self.blueprint_path,
            "proposal_path": self.proposal_path,
            "previous_blueprint_hash": self.previous_blueprint_hash,
            "previous_proposal_hash": self.previous_proposal_hash,
            "blueprint_hash": self.blueprint_hash,
            "proposal_hash": self.proposal_hash,
            "blueprint_changed": self.blueprint_changed,
            "proposal_changed": self.proposal_changed,
            "pair_revision_id": self.pair_revision_id,
            "manifest_path": self.manifest_path,
            "state_advanced": self.state_advanced,
        }


def commit_document_pair(
    *,
    workspace: Path,
    state_machine: ProposalStateMachine,
    iteration_number: int,
    revised_blueprint_text: str,
    revised_proposal_text: str,
    source_pack_id: str = "",
    proposal_revision: str = "",
) -> DocumentPairWriteReport:
    """Commit the Blueprint/Proposal pair — all-or-rollback, fail closed.

    Both texts must be non-empty bounded strings; the CURRENT blueprint and
    the MASTER proposal must exist and match the caller-fingerprinted
    expected hashes (stale-input refusal BEFORE any write).  The commit is
    visible ONLY when both replacements are durable and the manifest is
    written; any failure in between restores the touched file from its
    exact snapshot and raises with ``restored=True``.

    The state machine must sit at INTEGRATION (the same write authority
    window as the single-document writer) and is left where it was: the
    caller owns the graph advance after the pair commit succeeded.
    """
    workspace = Path(workspace)
    if state_machine.phase is not ProposalPhase.INTEGRATION:
        raise DocumentPairStateError(
            "phase_not_integration",
            f"pair-commit write authority exists ONLY during INTEGRATION "
            f"(the state machine is at {state_machine.phase.value}).",
        )
    if not isinstance(revised_blueprint_text, str) or not revised_blueprint_text.strip():
        raise DocumentPairStateError(
            "empty_revised_blueprint", "revised_blueprint_text must be non-empty."
        )
    if not isinstance(revised_proposal_text, str) or not revised_proposal_text.strip():
        raise DocumentPairStateError(
            "empty_revised_proposal", "revised_proposal_text must be non-empty."
        )
    if not isinstance(iteration_number, int) or isinstance(iteration_number, bool):
        raise DocumentPairStateError(
            "invalid_iteration", "iteration_number must be a plain integer."
        )
    if iteration_number < 1:
        raise DocumentPairStateError(
            "invalid_iteration", "iteration_number must be >= 1."
        )

    blueprint_path = _document_path(workspace, CURRENT_BLUEPRINT_RELPATH)
    proposal_path = _document_path(workspace, MASTER_PROPOSAL_RELPATH)

    blueprint_bytes = _canonical_eof(revised_blueprint_text.encode("utf-8"))
    proposal_bytes = _canonical_eof(revised_proposal_text.encode("utf-8"))
    if len(blueprint_bytes) > MAX_PROPOSAL_BYTES:
        raise DocumentPairStateError(
            "oversized_revised_blueprint",
            f"revised blueprint exceeds {MAX_PROPOSAL_BYTES} bytes.",
        )
    if len(proposal_bytes) > MAX_PROPOSAL_BYTES:
        raise DocumentPairStateError(
            "oversized_revised_proposal",
            f"revised proposal exceeds {MAX_PROPOSAL_BYTES} bytes.",
        )
    new_blueprint_hash = _sha256_bytes(blueprint_bytes)
    new_proposal_hash = _sha256_bytes(proposal_bytes)

    # -- 1. snapshot the CURRENT live pair (exact bytes) ---------------------
    if not blueprint_path.is_file():
        raise DocumentPairStateError(
            "missing_current_blueprint",
            f"{CURRENT_BLUEPRINT_RELPATH} does not exist; initialize the "
            "living Blueprint first.",
        )
    if not proposal_path.is_file():
        raise DocumentPairStateError(
            "missing_master_proposal",
            f"{MASTER_PROPOSAL_RELPATH} does not exist.",
        )
    try:
        live_blueprint = blueprint_path.read_bytes()
        live_proposal = proposal_path.read_bytes()
    except OSError as exc:
        raise DocumentPairStateError(
            "fingerprint_failed", f"cannot read the live pair: {exc}"
        ) from exc
    previous_blueprint_hash = _sha256_bytes(live_blueprint)
    previous_proposal_hash = _sha256_bytes(live_proposal)

    blueprint_changed = new_blueprint_hash != previous_blueprint_hash
    proposal_changed = new_proposal_hash != previous_proposal_hash

    # Deterministic pair revision id: stable over the same committed
    # content, unique per committed pair (no timestamps, no randomness).
    pair_revision_id = _sha256_bytes(
        (
            f"encomm-pcc.document-pair/v1\n{iteration_number}\n"
            f"{new_blueprint_hash}\n{new_proposal_hash}\n"
        ).encode("utf-8")
    )[:32]

    # -- 2. STAGE both outputs BEFORE touching any live file -----------------
    def _stage(target: Path, data: bytes, prefix: str) -> tuple[Path, str]:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=prefix, suffix=".part")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            tmp_path.unlink(missing_ok=True)
            raise
        # Windows os.replace cannot clobber an existing target's open
        # handles, but it DOES replace existing files atomically.
        return tmp_path, tmp_name

    try:
        bp_tmp, bp_tmp_name = _stage(blueprint_path, blueprint_bytes, ".tmp-pairbp-")
    except OSError as exc:
        raise DocumentPairStateError(
            "stage_failed", f"blueprint staging failed: {exc}"
        ) from exc
    try:
        pr_tmp, pr_tmp_name = _stage(proposal_path, proposal_bytes, ".tmp-pairpr-")
    except OSError as exc:
        Path(bp_tmp_name).unlink(missing_ok=True)
        raise DocumentPairStateError(
            "stage_failed", f"proposal staging failed: {exc}"
        ) from exc

    # -- 3. replace the pair (blueprint first, then proposal) ----------------
    try:
        os.replace(bp_tmp, blueprint_path)
    except OSError as exc:
        Path(pr_tmp_name).unlink(missing_ok=True)
        raise DocumentPairStateError(
            "blueprint_replace_failed",
            f"atomic blueprint replacement failed: {exc}",
        ) from exc
    try:
        os.replace(pr_tmp, proposal_path)
    except OSError as exc:
        # Second replacement failed: restore the FIRST from its exact
        # snapshot and verify the restoration.
        _restore_document(blueprint_path, live_blueprint)
        blueprint_after = _fingerprint(blueprint_path)
        restored = blueprint_after == previous_blueprint_hash
        raise DocumentPairStateError(
            "proposal_replace_failed",
            f"atomic proposal replacement failed: {exc}; the blueprint "
            f"was restored from its snapshot "
            f"({'verified' if restored else 'RESTORE UNVERIFIED — operator inspection required'}).",
            restored=restored,
        ) from exc

    # -- 4. both files durable → write the manifest (the commit marker) ------
    state = DocumentPairState(
        iteration=iteration_number,
        blueprint_hash=new_blueprint_hash,
        proposal_hash=new_proposal_hash,
        previous_blueprint_hash=previous_blueprint_hash,
        previous_proposal_hash=previous_proposal_hash,
        pair_revision_id=pair_revision_id,
        source_pack_id=str(source_pack_id or ""),
    )
    try:
        _atomic_write_bytes(
            _manifest_path(workspace),
            (
                json.dumps(state.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)
                + "\n"
            ).encode("utf-8"),
        )
    except OSError as exc:
        # Both files ARE the new pair on disk but the manifest refused.
        # Restore BOTH from snapshots so the durable state never shows a
        # half-committed pair.
        _restore_document(blueprint_path, live_blueprint)
        _restore_document(proposal_path, live_proposal)
        bp_ok = _fingerprint(blueprint_path) == previous_blueprint_hash
        pr_ok = _fingerprint(proposal_path) == previous_proposal_hash
        raise DocumentPairStateError(
            "manifest_write_failed",
            f"pair manifest write failed after both replacements: {exc}; "
            f"both documents were restored "
            f"({'verified' if bp_ok and pr_ok else 'RESTORE UNVERIFIED — operator inspection required'}).",
            restored=bp_ok and pr_ok,
        ) from exc

    return DocumentPairWriteReport(
        iteration=iteration_number,
        blueprint_path=str(blueprint_path),
        proposal_path=str(proposal_path),
        previous_blueprint_hash=previous_blueprint_hash,
        previous_proposal_hash=previous_proposal_hash,
        blueprint_hash=new_blueprint_hash,
        proposal_hash=new_proposal_hash,
        blueprint_changed=blueprint_changed,
        proposal_changed=proposal_changed,
        pair_revision_id=pair_revision_id,
        manifest_path=str(_manifest_path(workspace)),
        state_advanced=False,
    )
