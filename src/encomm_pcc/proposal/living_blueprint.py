"""Living Blueprint model (Session 021) — PURE proposal domain.

The Blueprint becomes a VERSIONED PAIR under ``00_SOURCE_OF_TRUTH/``:

* ``MASTER_BLUEPRINT.md``  — the IMMUTABLE ORIGINAL imported canonical
  Blueprint.  An AI run NEVER modifies it; its bytes are the provenance
  anchor of the whole workspace.
* ``CURRENT_BLUEPRINT.md`` — the LIVING Blueprint controlled ONLY by the
  ASTRA/ORCHESTRATOR runtime integration (the same sole write authority
  that owns ``MASTER_PROPOSAL.md``).  It is initialized with the EXACT
  canonical bytes of the original and evolves from there.

Durable state lives in ``05_CONTROL/BLUEPRINT_STATE.json``
(schema ``encomm-pcc.blueprint-state/v1``) so recovery and the UI can
report hashes/iteration without re-deriving them.

Hard rules (fail closed):

* initialization/migration NEVER rewrites ``MASTER_BLUEPRINT.md`` —
  :func:`ensure_current_blueprint` copies the original's exact bytes into
  an ABSENT living file and nothing else;
* an existing non-empty living file is never silently replaced here
  (import-time initialization refuses on a conflicting CURRENT file);
* the state manifest is atomic, deterministic JSON (indent=2, sort_keys,
  LF, trailing newline) and every write re-verifies what it records;
* integrity checks surface original/current drift as typed errors — a
  mutated original must NEVER look healthy.

This module imports nothing from the coding pipeline, ``proposal_runtime``
or the UI, and holds no engine knowledge.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .models import utc_now

__all__ = [
    "BLUEPRINT_STATE_FILENAME",
    "BLUEPRINT_STATE_RELPATH",
    "BLUEPRINT_STATE_SCHEMA",
    "CURRENT_BLUEPRINT_RELPATH",
    "MASTER_BLUEPRINT_RELPATH",
    "BlueprintStateError",
    "BlueprintState",
    "blueprint_pair_status",
    "current_blueprint_path",
    "ensure_current_blueprint",
    "initialize_living_blueprint",
    "load_blueprint_state",
    "master_blueprint_path",
    "try_load_blueprint_state",
    "validate_original_integrity",
    "write_blueprint_state",
]

#: The immutable ORIGINAL canonical blueprint (existing contract path).
MASTER_BLUEPRINT_RELPATH = "00_SOURCE_OF_TRUTH/MASTER_BLUEPRINT.md"

#: The LIVING blueprint — ASTRA-runtime write authority ONLY (Session 021).
CURRENT_BLUEPRINT_RELPATH = "00_SOURCE_OF_TRUTH/CURRENT_BLUEPRINT.md"

#: Durable blueprint-pair state file (relative to the workspace root).
BLUEPRINT_STATE_FILENAME = "BLUEPRINT_STATE.json"
BLUEPRINT_STATE_RELPATH = f"05_CONTROL/{BLUEPRINT_STATE_FILENAME}"

#: Schema identifier persisted inside the state file; a mismatching schema
#: on an existing state file is a conflict, never silently upgraded.
BLUEPRINT_STATE_SCHEMA = "encomm-pcc.blueprint-state/v1"


class BlueprintStateError(RuntimeError):
    """A living-blueprint operation was refused or failed (fail closed).

    ``reason`` is a short stable machine tag; the message is bounded and
    human-readable.  No failure path leaves a partially written pair or a
    misleading state manifest.
    """

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"[{reason}] {message}")


def master_blueprint_path(root: Path) -> Path:
    """Absolute path of the immutable original canonical blueprint."""
    return Path(root).joinpath(*MASTER_BLUEPRINT_RELPATH.split("/"))


def current_blueprint_path(root: Path) -> Path:
    """Absolute path of the living current blueprint."""
    return Path(root).joinpath(*CURRENT_BLUEPRINT_RELPATH.split("/"))


def _state_path(root: Path) -> Path:
    return Path(root).joinpath(*BLUEPRINT_STATE_RELPATH.split("/"))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    """Same-directory temp file + flush + fsync + ``os.replace``."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp-bpstate-")
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


def _read_exact_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise BlueprintStateError(
            "unreadable_file", f"cannot read {path}: {exc}"
        ) from exc


@dataclass(slots=True)
class BlueprintState:
    """Durable record of the immutable-original / living-blueprint pair."""

    original_blueprint_hash: str
    current_blueprint_hash: str
    current_blueprint_iteration: int = 0
    last_pair_revision_id: str = ""
    updated_at: str = ""
    schema: str = BLUEPRINT_STATE_SCHEMA

    def __post_init__(self) -> None:
        self.original_blueprint_hash = str(self.original_blueprint_hash)
        self.current_blueprint_hash = str(self.current_blueprint_hash)
        self.last_pair_revision_id = str(self.last_pair_revision_id)
        if not isinstance(self.current_blueprint_iteration, int) or isinstance(
            self.current_blueprint_iteration, bool
        ):
            raise BlueprintStateError(
                "invalid_iteration",
                "current_blueprint_iteration must be a plain integer.",
            )
        if self.current_blueprint_iteration < 0:
            raise BlueprintStateError(
                "invalid_iteration",
                "current_blueprint_iteration must be >= 0.",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "original_blueprint_hash": self.original_blueprint_hash,
            "current_blueprint_hash": self.current_blueprint_hash,
            "current_blueprint_iteration": self.current_blueprint_iteration,
            "last_pair_revision_id": self.last_pair_revision_id,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BlueprintState":
        if not isinstance(data, dict):
            raise BlueprintStateError(
                "state_not_object", "BLUEPRINT_STATE.json is not a JSON object."
            )
        if data.get("schema") != BLUEPRINT_STATE_SCHEMA:
            raise BlueprintStateError(
                "state_schema_conflict",
                f"BLUEPRINT_STATE.json carries schema {data.get('schema')!r}, "
                f"expected {BLUEPRINT_STATE_SCHEMA!r}.",
            )
        try:
            iteration = int(data.get("current_blueprint_iteration") or 0)
        except (TypeError, ValueError) as exc:
            raise BlueprintStateError(
                "invalid_iteration",
                "current_blueprint_iteration is not an integer.",
            ) from exc
        return cls(
            original_blueprint_hash=str(data.get("original_blueprint_hash") or ""),
            current_blueprint_hash=str(data.get("current_blueprint_hash") or ""),
            current_blueprint_iteration=iteration,
            last_pair_revision_id=str(data.get("last_pair_revision_id") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )


def _parse_state_file(root: Path) -> BlueprintState:
    path = _state_path(root)
    if not path.is_file():
        raise BlueprintStateError(
            "missing_state", f"{BLUEPRINT_STATE_RELPATH} does not exist."
        )
    try:
        data = json.loads(_read_exact_bytes(path).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BlueprintStateError(
            "state_unreadable", f"{BLUEPRINT_STATE_RELPATH}: {exc}"
        ) from exc
    return BlueprintState.from_dict(data)


def load_blueprint_state(root: Path) -> BlueprintState:
    """Load the durable blueprint state — raises when absent/corrupt."""
    return _parse_state_file(root)


def try_load_blueprint_state(root: Path) -> Optional[BlueprintState]:
    """Load the durable blueprint state or ``None`` when absent."""
    try:
        return _parse_state_file(root)
    except BlueprintStateError as exc:
        if exc.reason == "missing_state":
            return None
        raise


def write_blueprint_state(root: Path, state: BlueprintState) -> Path:
    """Atomically persist the blueprint state (deterministic JSON)."""
    root = Path(root)
    state.updated_at = state.updated_at or utc_now()
    payload = (
        json.dumps(state.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)
        + "\n"
    ).encode("utf-8")
    target = _state_path(root)
    _atomic_write_bytes(target, payload)
    return target


def _require_original(root: Path) -> bytes:
    path = master_blueprint_path(root)
    if not path.is_file():
        raise BlueprintStateError(
            "missing_original",
            f"{MASTER_BLUEPRINT_RELPATH} does not exist; there is no "
            "canonical Blueprint to anchor a living Blueprint on.",
        )
    data = _read_exact_bytes(path)
    if not data.strip():
        raise BlueprintStateError(
            "empty_original",
            f"{MASTER_BLUEPRINT_RELPATH} is empty; refusing to derive a "
            "living Blueprint from nothing.",
        )
    return data


def initialize_living_blueprint(root: Path) -> BlueprintState:
    """Initialize ``CURRENT_BLUEPRINT.md`` from the canonical original.

    Copies the EXACT bytes of ``MASTER_BLUEPRINT.md`` into an absent (or
    byte-identical) ``CURRENT_BLUEPRINT.md`` and writes the state manifest.
    Fail closed: a conflicting existing living file is never overwritten.
    Called by the import flow AFTER the canonical original was written.
    """
    root = Path(root)
    original_bytes = _require_original(root)
    original_hash = _sha256_bytes(original_bytes)

    current_path = current_blueprint_path(root)
    if current_path.exists():
        existing = _read_exact_bytes(current_path)
        if existing != original_bytes:
            raise BlueprintStateError(
                "current_conflict",
                f"{CURRENT_BLUEPRINT_RELPATH} already exists with different "
                "bytes; the living Blueprint is never silently replaced "
                "(use the pair-commit runtime path to evolve it).",
            )
        # Idempotent re-initialization over identical bytes.
    else:
        _atomic_write_bytes(current_path, original_bytes)

    state = BlueprintState(
        original_blueprint_hash=original_hash,
        current_blueprint_hash=original_hash,
        current_blueprint_iteration=0,
        last_pair_revision_id="",
        updated_at=utc_now(),
    )
    write_blueprint_state(root, state)
    return state


def ensure_current_blueprint(root: Path) -> tuple[BlueprintState, bool]:
    """Deterministic legacy migration / validation of the living pair.

    * state manifest present → validate it against the ACTUAL files
      (original drift and current drift are typed errors, never coerced);
    * manifest absent AND living file absent AND original present →
      migrate: CURRENT := exact MASTER bytes, iteration 0 (returns
      ``(state, migrated=True)``);
    * manifest absent but living file present → derive a state record
      from the actual bytes (import crashed before the manifest write;
      the files themselves are authoritative), never overwrite either.
    """
    root = Path(root)
    state = try_load_blueprint_state(root)
    if state is not None:
        actual_original = _sha256_bytes(_require_original(root))
        if actual_original != state.original_blueprint_hash:
            raise BlueprintStateError(
                "original_mutated",
                f"{MASTER_BLUEPRINT_RELPATH} hash {actual_original} != the "
                f"recorded original {state.original_blueprint_hash}; the "
                "immutable original changed outside the contract.",
            )
        current_bytes = _read_exact_bytes(current_blueprint_path(root))
        actual_current = _sha256_bytes(current_bytes)
        if actual_current != state.current_blueprint_hash:
            raise BlueprintStateError(
                "current_drift",
                f"{CURRENT_BLUEPRINT_RELPATH} hash {actual_current} != the "
                f"recorded current {state.current_blueprint_hash}; the state "
                "manifest is stale — recover via the runtime pair commit.",
            )
        return state, False

    original_bytes = _require_original(root)
    original_hash = _sha256_bytes(original_bytes)
    current_path = current_blueprint_path(root)
    if current_path.exists():
        current_bytes = _read_exact_bytes(current_path)
        state = BlueprintState(
            original_blueprint_hash=original_hash,
            current_blueprint_hash=_sha256_bytes(current_bytes),
            current_blueprint_iteration=0,
            last_pair_revision_id="",
            updated_at=utc_now(),
        )
        write_blueprint_state(root, state)
        return state, False

    # Legacy workspace: MASTER exists, CURRENT does not → migrate.
    _atomic_write_bytes(current_path, original_bytes)
    state = BlueprintState(
        original_blueprint_hash=original_hash,
        current_blueprint_hash=original_hash,
        current_blueprint_iteration=0,
        last_pair_revision_id="",
        updated_at=utc_now(),
    )
    write_blueprint_state(root, state)
    return state, True


def validate_original_integrity(root: Path) -> str:
    """Re-verify the immutable original against the state manifest.

    Returns the actual original hash.  Raises :class:`BlueprintStateError`
    (``original_mutated``) when the recorded original hash no longer
    matches the file — an AI run must NEVER mutate MASTER_BLUEPRINT.md.
    A missing state manifest is tolerated here (the hash of the actual
    file is returned; callers with a living Blueprint must use
    :func:`ensure_current_blueprint` first).
    """
    root = Path(root)
    actual = _sha256_bytes(_require_original(root))
    state = try_load_blueprint_state(root)
    if state is not None and state.original_blueprint_hash != actual:
        raise BlueprintStateError(
            "original_mutated",
            f"{MASTER_BLUEPRINT_RELPATH} hash {actual} != the recorded "
            f"original {state.original_blueprint_hash}.",
        )
    return actual


def blueprint_pair_status(root: Path) -> dict[str, str]:
    """Read-only status of both blueprint documents (UI-facing).

    Keys: ``original`` and ``current`` mapping to ``READY``/``MISSING``/
    ``EMPTY`` (mirrors ``source_import.blueprint_status`` conventions).
    """
    root = Path(root)

    def _status(path: Path) -> str:
        if not path.is_file():
            return "MISSING"
        try:
            if path.read_bytes().strip():
                return "READY"
        except OSError:
            return "MISSING"
        return "EMPTY"

    return {
        "original": _status(master_blueprint_path(root)),
        "current": _status(current_blueprint_path(root)),
    }
