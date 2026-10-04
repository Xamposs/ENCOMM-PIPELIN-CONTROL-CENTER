"""Session 021 — Living Blueprint pair (offline, zero model calls).

Covers: initialization of the living pair from the immutable original
(exact bytes), idempotent re-init, conflict/missing/empty refusals, the
legacy migration path, drift validation (original/current), the durable
``BLUEPRINT_STATE.json`` manifest (round-trip, corruption, schema
conflict), original-integrity verification and the UI-facing pair status.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from encomm_pcc.proposal.living_blueprint import (
    BLUEPRINT_STATE_SCHEMA,
    BlueprintState,
    BlueprintStateError,
    blueprint_pair_status,
    current_blueprint_path,
    ensure_current_blueprint,
    initialize_living_blueprint,
    load_blueprint_state,
    master_blueprint_path,
    try_load_blueprint_state,
    validate_original_integrity,
    write_blueprint_state,
)

MASTER_BYTES = b"# BP\n"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# (a) initialize_living_blueprint — fresh workspace
# ---------------------------------------------------------------------------


def test_01_initialize_creates_current_with_exact_bytes(tmp_path: Path) -> None:
    master = master_blueprint_path(tmp_path)
    master.parent.mkdir(parents=True)
    master.write_bytes(MASTER_BYTES)  # NEVER text mode — hashes bind exact bytes

    state = initialize_living_blueprint(tmp_path)

    current = current_blueprint_path(tmp_path)
    assert current.is_file()
    assert current.read_bytes() == MASTER_BYTES
    assert state.original_blueprint_hash == _sha256(MASTER_BYTES)
    assert state.current_blueprint_hash == state.original_blueprint_hash
    assert state.current_blueprint_iteration == 0
    assert state.last_pair_revision_id == ""
    assert state.schema == BLUEPRINT_STATE_SCHEMA


def test_02_initialize_writes_state_manifest(tmp_path: Path) -> None:
    master = master_blueprint_path(tmp_path)
    master.parent.mkdir(parents=True)
    master.write_bytes(MASTER_BYTES)

    initialize_living_blueprint(tmp_path)

    manifest = tmp_path / "05_CONTROL" / "BLUEPRINT_STATE.json"
    assert manifest.is_file()
    data = json.loads(manifest.read_bytes().decode("utf-8"))
    expected_hash = _sha256(MASTER_BYTES)
    assert data["schema"] == "encomm-pcc.blueprint-state/v1"
    assert data["original_blueprint_hash"] == expected_hash
    assert data["current_blueprint_hash"] == expected_hash
    assert data["original_blueprint_hash"] == data["current_blueprint_hash"]
    assert data["current_blueprint_iteration"] == 0
    # Deterministic manifest: indent=2, sort_keys, LF, trailing newline.
    raw = manifest.read_bytes()
    assert raw.endswith(b"\n")
    assert b"\r\n" not in raw
    assert json.loads(raw.decode("utf-8")) == data


# ---------------------------------------------------------------------------
# (b) idempotent re-init over identical bytes
# ---------------------------------------------------------------------------


def test_03_reinit_over_identical_bytes_is_idempotent(tmp_path: Path) -> None:
    master = master_blueprint_path(tmp_path)
    master.parent.mkdir(parents=True)
    master.write_bytes(MASTER_BYTES)

    first = initialize_living_blueprint(tmp_path)
    current = current_blueprint_path(tmp_path)
    bytes_after_first = current.read_bytes()
    manifest_after_first = (
        tmp_path / "05_CONTROL" / "BLUEPRINT_STATE.json"
    ).read_bytes()

    second = initialize_living_blueprint(tmp_path)

    assert second.original_blueprint_hash == first.original_blueprint_hash
    assert second.current_blueprint_hash == first.current_blueprint_hash
    assert second.current_blueprint_iteration == 0
    assert current.read_bytes() == bytes_after_first
    # The immutable original is never touched by init.
    assert master.read_bytes() == MASTER_BYTES


# ---------------------------------------------------------------------------
# (c) conflicting CURRENT file is refused, MASTER untouched
# ---------------------------------------------------------------------------


def test_04_init_refuses_conflicting_current_and_keeps_master(tmp_path: Path) -> None:
    master = master_blueprint_path(tmp_path)
    master.parent.mkdir(parents=True)
    master.write_bytes(MASTER_BYTES)
    conflicting = b"# A DIFFERENT living blueprint\n"
    current = current_blueprint_path(tmp_path)
    current.write_bytes(conflicting)

    with pytest.raises(BlueprintStateError) as excinfo:
        initialize_living_blueprint(tmp_path)
    assert excinfo.value.reason == "current_conflict"

    # Fail closed: neither file was touched.
    assert current.read_bytes() == conflicting
    assert master.read_bytes() == MASTER_BYTES
    # No misleading state manifest was written.
    assert not (tmp_path / "05_CONTROL" / "BLUEPRINT_STATE.json").exists()


# ---------------------------------------------------------------------------
# (d)/(e) missing / empty original
# ---------------------------------------------------------------------------


def test_05_init_fails_without_master(tmp_path: Path) -> None:
    with pytest.raises(BlueprintStateError) as excinfo:
        initialize_living_blueprint(tmp_path)
    assert excinfo.value.reason == "missing_original"
    assert not current_blueprint_path(tmp_path).exists()
    assert not (tmp_path / "05_CONTROL" / "BLUEPRINT_STATE.json").exists()


def test_06_init_fails_on_empty_master(tmp_path: Path) -> None:
    master = master_blueprint_path(tmp_path)
    master.parent.mkdir(parents=True)
    master.write_bytes(b"")

    with pytest.raises(BlueprintStateError) as excinfo:
        initialize_living_blueprint(tmp_path)
    assert excinfo.value.reason == "empty_original"
    assert not current_blueprint_path(tmp_path).exists()


def test_07_init_fails_on_whitespace_only_master(tmp_path: Path) -> None:
    master = master_blueprint_path(tmp_path)
    master.parent.mkdir(parents=True)
    master.write_bytes(b"   \n\t\n")

    with pytest.raises(BlueprintStateError) as excinfo:
        initialize_living_blueprint(tmp_path)
    assert excinfo.value.reason == "empty_original"


# ---------------------------------------------------------------------------
# (f) ensure_current_blueprint — legacy migration
# ---------------------------------------------------------------------------


def test_08_ensure_migrates_legacy_workspace(tmp_path: Path) -> None:
    master = master_blueprint_path(tmp_path)
    master.parent.mkdir(parents=True)
    master.write_bytes(MASTER_BYTES)

    state, migrated = ensure_current_blueprint(tmp_path)

    assert migrated is True
    assert current_blueprint_path(tmp_path).read_bytes() == MASTER_BYTES
    assert state.original_blueprint_hash == _sha256(MASTER_BYTES)
    assert state.current_blueprint_hash == state.original_blueprint_hash
    assert state.current_blueprint_iteration == 0
    # The manifest was written by the migration.
    loaded = load_blueprint_state(tmp_path)
    assert loaded.current_blueprint_hash == state.current_blueprint_hash
    assert master.read_bytes() == MASTER_BYTES


# ---------------------------------------------------------------------------
# (g) original mutation is a typed error, never coerced
# ---------------------------------------------------------------------------


def test_09_ensure_detects_original_mutation(tmp_path: Path) -> None:
    master = master_blueprint_path(tmp_path)
    master.parent.mkdir(parents=True)
    master.write_bytes(MASTER_BYTES)
    initialize_living_blueprint(tmp_path)

    # The immutable original changed outside the contract.
    master.write_bytes(b"# BP TAMPERED\n")

    with pytest.raises(BlueprintStateError) as excinfo:
        ensure_current_blueprint(tmp_path)
    assert excinfo.value.reason == "original_mutated"


# ---------------------------------------------------------------------------
# (h) current drift is a typed error
# ---------------------------------------------------------------------------


def test_10_ensure_detects_current_drift(tmp_path: Path) -> None:
    master = master_blueprint_path(tmp_path)
    master.parent.mkdir(parents=True)
    master.write_bytes(MASTER_BYTES)
    initialize_living_blueprint(tmp_path)

    # The LIVING file drifted from the recorded state.
    current_blueprint_path(tmp_path).write_bytes(b"# DRIFTED\n")

    with pytest.raises(BlueprintStateError) as excinfo:
        ensure_current_blueprint(tmp_path)
    assert excinfo.value.reason == "current_drift"


def test_11_ensure_returns_state_unchanged_when_healthy(tmp_path: Path) -> None:
    master = master_blueprint_path(tmp_path)
    master.parent.mkdir(parents=True)
    master.write_bytes(MASTER_BYTES)
    first = initialize_living_blueprint(tmp_path)

    state, migrated = ensure_current_blueprint(tmp_path)

    assert migrated is False
    assert state.original_blueprint_hash == first.original_blueprint_hash
    assert state.current_blueprint_hash == first.current_blueprint_hash
    assert state.current_blueprint_iteration == first.current_blueprint_iteration


# ---------------------------------------------------------------------------
# (i) manifest absent but files present → derive from actual bytes
# ---------------------------------------------------------------------------


def test_12_ensure_derives_state_from_actual_bytes_without_manifest(
    tmp_path: Path,
) -> None:
    master = master_blueprint_path(tmp_path)
    master.parent.mkdir(parents=True)
    master.write_bytes(MASTER_BYTES)
    # Import crashed after the CURRENT write but before the manifest write;
    # the living file holds DIFFERENT (newer) bytes and is authoritative.
    current_bytes = b"# BP evolved\n"
    current_blueprint_path(tmp_path).write_bytes(current_bytes)

    state, migrated = ensure_current_blueprint(tmp_path)

    assert migrated is False
    assert state.original_blueprint_hash == _sha256(MASTER_BYTES)
    assert state.current_blueprint_hash == _sha256(current_bytes)
    assert state.current_blueprint_iteration == 0
    # Neither file was overwritten by the derivation.
    assert master.read_bytes() == MASTER_BYTES
    assert current_blueprint_path(tmp_path).read_bytes() == current_bytes
    # And the derived state is now durable.
    loaded = load_blueprint_state(tmp_path)
    assert loaded.current_blueprint_hash == _sha256(current_bytes)


# ---------------------------------------------------------------------------
# (j) load / try_load semantics
# ---------------------------------------------------------------------------


def test_13_load_state_missing_raises_and_try_load_returns_none(
    tmp_path: Path,
) -> None:
    with pytest.raises(BlueprintStateError) as excinfo:
        load_blueprint_state(tmp_path)
    assert excinfo.value.reason == "missing_state"
    assert try_load_blueprint_state(tmp_path) is None


# ---------------------------------------------------------------------------
# (k) write_blueprint_state round-trip + corruption + schema conflict
# ---------------------------------------------------------------------------


def test_14_write_state_round_trips_all_fields(tmp_path: Path) -> None:
    state = BlueprintState(
        original_blueprint_hash="a" * 64,
        current_blueprint_hash="b" * 64,
        current_blueprint_iteration=7,
        last_pair_revision_id="rev-1234567890abcdef",
        updated_at="2026-10-04T00:00:00Z",
    )
    write_blueprint_state(tmp_path, state)

    loaded = load_blueprint_state(tmp_path)
    assert loaded.schema == state.schema
    assert loaded.original_blueprint_hash == state.original_blueprint_hash
    assert loaded.current_blueprint_hash == state.current_blueprint_hash
    assert loaded.current_blueprint_iteration == state.current_blueprint_iteration
    assert loaded.last_pair_revision_id == state.last_pair_revision_id
    assert loaded.updated_at == state.updated_at

    data = json.loads(
        (tmp_path / "05_CONTROL" / "BLUEPRINT_STATE.json")
        .read_bytes()
        .decode("utf-8")
    )
    assert data == state.to_dict()


def test_15_write_state_fills_updated_at_when_empty(tmp_path: Path) -> None:
    state = BlueprintState(
        original_blueprint_hash="c" * 64,
        current_blueprint_hash="c" * 64,
        updated_at="",
    )
    write_blueprint_state(tmp_path, state)
    loaded = load_blueprint_state(tmp_path)
    assert loaded.updated_at  # timestamp materialized by the writer


def test_16_corrupted_state_json_raises(tmp_path: Path) -> None:
    control = tmp_path / "05_CONTROL"
    control.mkdir(parents=True)
    (control / "BLUEPRINT_STATE.json").write_bytes(b"{ not json at all ")

    with pytest.raises(BlueprintStateError) as excinfo:
        load_blueprint_state(tmp_path)
    assert excinfo.value.reason == "state_unreadable"


def test_17_wrong_schema_is_a_conflict(tmp_path: Path) -> None:
    control = tmp_path / "05_CONTROL"
    control.mkdir(parents=True)
    (control / "BLUEPRINT_STATE.json").write_bytes(
        json.dumps(
            {
                "schema": "encomm-pcc.blueprint-state/v0",
                "original_blueprint_hash": "d" * 64,
                "current_blueprint_hash": "d" * 64,
                "current_blueprint_iteration": 0,
            }
        ).encode("utf-8")
    )

    with pytest.raises(BlueprintStateError) as excinfo:
        load_blueprint_state(tmp_path)
    assert excinfo.value.reason == "state_schema_conflict"


def test_18_blueprint_state_from_dict_rejects_bad_iteration() -> None:
    with pytest.raises(BlueprintStateError):
        BlueprintState.from_dict(
            {"schema": BLUEPRINT_STATE_SCHEMA, "current_blueprint_iteration": -1}
        )
    with pytest.raises(BlueprintStateError) as excinfo:
        BlueprintState.from_dict(
            {"schema": BLUEPRINT_STATE_SCHEMA, "current_blueprint_iteration": "x"}
        )
    assert excinfo.value.reason == "invalid_iteration"


# ---------------------------------------------------------------------------
# (l) validate_original_integrity
# ---------------------------------------------------------------------------


def test_19_validate_original_integrity_returns_hash_when_consistent(
    tmp_path: Path,
) -> None:
    master = master_blueprint_path(tmp_path)
    master.parent.mkdir(parents=True)
    master.write_bytes(MASTER_BYTES)
    initialize_living_blueprint(tmp_path)

    assert validate_original_integrity(tmp_path) == _sha256(MASTER_BYTES)


def test_20_validate_original_integrity_flags_mutation(tmp_path: Path) -> None:
    master = master_blueprint_path(tmp_path)
    master.parent.mkdir(parents=True)
    master.write_bytes(MASTER_BYTES)
    initialize_living_blueprint(tmp_path)
    master.write_bytes(b"# BP swapped\n")

    with pytest.raises(BlueprintStateError) as excinfo:
        validate_original_integrity(tmp_path)
    assert excinfo.value.reason == "original_mutated"


def test_21_validate_original_integrity_tolerates_missing_manifest(
    tmp_path: Path,
) -> None:
    master = master_blueprint_path(tmp_path)
    master.parent.mkdir(parents=True)
    master.write_bytes(MASTER_BYTES)

    assert validate_original_integrity(tmp_path) == _sha256(MASTER_BYTES)


# ---------------------------------------------------------------------------
# (m) blueprint_pair_status
# ---------------------------------------------------------------------------


def test_22_pair_status_missing_when_nothing_exists(tmp_path: Path) -> None:
    assert blueprint_pair_status(tmp_path) == {
        "original": "MISSING",
        "current": "MISSING",
    }


def test_23_pair_status_after_full_initialization(tmp_path: Path) -> None:
    master = master_blueprint_path(tmp_path)
    master.parent.mkdir(parents=True)
    master.write_bytes(MASTER_BYTES)

    assert blueprint_pair_status(tmp_path)["original"] == "READY"
    assert blueprint_pair_status(tmp_path)["current"] == "MISSING"

    initialize_living_blueprint(tmp_path)
    status = blueprint_pair_status(tmp_path)
    assert status == {"original": "READY", "current": "READY"}


def test_24_pair_status_reports_empty_original(tmp_path: Path) -> None:
    master = master_blueprint_path(tmp_path)
    master.parent.mkdir(parents=True)
    master.write_bytes(b"")
    current = current_blueprint_path(tmp_path)
    current.write_bytes(b"non-empty living bytes\n")

    status = blueprint_pair_status(tmp_path)
    assert status == {"original": "EMPTY", "current": "READY"}
