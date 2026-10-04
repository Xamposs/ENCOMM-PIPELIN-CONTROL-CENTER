"""Session 021 — all-or-rollback DOCUMENT PAIR commit (offline, zero model calls).

Covers: the happy-path pair commit (canonical EOF, manifest, deterministic
pair revision id), phase-gating, empty-text refusals, missing-document
refusals, iteration validation, manifest load semantics (missing/corrupt/
schema conflict), the manifest dataclass round-trip, hash/manifest
consistency, and a SIMULATED second-replace failure proving the rollback
restores the exact pre-commit bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from encomm_pcc.proposal.document_pair import (
    DOCUMENT_PAIR_STATE_SCHEMA,
    DocumentPairState,
    DocumentPairStateError,
    commit_document_pair,
    load_document_pair_state,
    try_load_document_pair_state,
)
from encomm_pcc.proposal.enums import ProposalPhase
from encomm_pcc.proposal.state_machine import ProposalStateMachine
from encomm_pcc.proposal.workspace import MASTER_PROPOSAL_RELPATH

CURRENT_BLUEPRINT_RELPATH = "00_SOURCE_OF_TRUTH/CURRENT_BLUEPRINT.md"

ORIGINAL_BP = b"# Original blueprint\n"
ORIGINAL_PR = b"# Original proposal\n"

REVISED_BP_TEXT = "# Revised blueprint\n\nBody of the revised blueprint.\n"
REVISED_PR_TEXT = "# Revised proposal\n\nBody of the revised proposal."


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _seed_workspace(tmp_path: Path) -> None:
    """Seed the pair-commit contract paths with real bytes."""
    bp = tmp_path.joinpath(*CURRENT_BLUEPRINT_RELPATH.split("/"))
    pr = tmp_path.joinpath(*MASTER_PROPOSAL_RELPATH.split("/"))
    bp.parent.mkdir(parents=True, exist_ok=True)
    pr.parent.mkdir(parents=True, exist_ok=True)
    bp.write_bytes(ORIGINAL_BP)
    pr.write_bytes(ORIGINAL_PR)


def _integration_machine() -> ProposalStateMachine:
    """Walk the legal proposal edges from IDLE to INTEGRATION."""
    machine = ProposalStateMachine()
    for phase in (
        ProposalPhase.SOURCE_VALIDATION,
        ProposalPhase.SCIENTIFIC_REVIEW,
        ProposalPhase.IMPLEMENTATION_REVIEW,
        ProposalPhase.RED_TEAM_REVIEW,
        ProposalPhase.INTEGRATION,
    ):
        machine.transition_to(phase)
    assert machine.phase is ProposalPhase.INTEGRATION
    return machine


def _commit(
    workspace: Path,
    *,
    machine: ProposalStateMachine | None = None,
    iteration: int = 1,
    bp_text: str = REVISED_BP_TEXT,
    pr_text: str = REVISED_PR_TEXT,
    source_pack_id: str = "",
) -> object:
    return commit_document_pair(
        workspace=workspace,
        state_machine=machine if machine is not None else _integration_machine(),
        iteration_number=iteration,
        revised_blueprint_text=bp_text,
        revised_proposal_text=pr_text,
        source_pack_id=source_pack_id,
    )


def _live_bytes(root: Path) -> tuple[bytes, bytes]:
    bp = root.joinpath(*CURRENT_BLUEPRINT_RELPATH.split("/"))
    pr = root.joinpath(*MASTER_PROPOSAL_RELPATH.split("/"))
    return bp.read_bytes(), pr.read_bytes()


# ---------------------------------------------------------------------------
# (a) successful commit
# ---------------------------------------------------------------------------


def test_01_commit_replaces_both_files_with_canonical_eof(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    report = _commit(tmp_path, iteration=3)

    bp_bytes, pr_bytes = _live_bytes(tmp_path)
    # The proposal text has NO trailing newline → exactly one is appended.
    assert bp_bytes == REVISED_BP_TEXT.encode("utf-8")
    assert pr_bytes == REVISED_PR_TEXT.encode("utf-8") + b"\n"
    assert report.iteration == 3
    assert report.blueprint_changed is True
    assert report.proposal_changed is True
    assert report.state_advanced is False


def test_02_commit_manifest_content_and_hash_consistency(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    report = _commit(tmp_path, iteration=2, source_pack_id="pack-1")

    manifest = tmp_path / "05_CONTROL" / "DOCUMENT_PAIR_STATE.json"
    assert manifest.is_file()
    data = json.loads(manifest.read_bytes().decode("utf-8"))
    assert data["schema"] == "encomm-pcc.document-pair-state/v1"
    assert data["iteration"] == 2
    assert data["source_pack_id"] == "pack-1"
    # Both NEW hashes (canonical-EOF proposal bytes)…
    assert data["blueprint_hash"] == _sha256(REVISED_BP_TEXT.encode("utf-8"))
    assert data["proposal_hash"] == _sha256(REVISED_PR_TEXT.encode("utf-8") + b"\n")
    # …and both PREVIOUS hashes (the seeded live pair).
    assert data["previous_blueprint_hash"] == _sha256(ORIGINAL_BP)
    assert data["previous_proposal_hash"] == _sha256(ORIGINAL_PR)
    assert data["pair_revision_id"] == report.pair_revision_id

    # (i) re-fingerprint the files: manifest values equal actual file hashes.
    bp_bytes, pr_bytes = _live_bytes(tmp_path)
    assert _sha256(bp_bytes) == data["blueprint_hash"]
    assert _sha256(pr_bytes) == data["proposal_hash"]


def test_03_pair_revision_id_is_deterministic(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    other = tmp_path / "twin-workspace"
    bp = other.joinpath(*CURRENT_BLUEPRINT_RELPATH.split("/"))
    pr = other.joinpath(*MASTER_PROPOSAL_RELPATH.split("/"))
    bp.parent.mkdir(parents=True, exist_ok=True)
    pr.parent.mkdir(parents=True, exist_ok=True)
    bp.write_bytes(ORIGINAL_BP)
    pr.write_bytes(ORIGINAL_PR)

    first = _commit(tmp_path, iteration=1)
    second = _commit(other, iteration=1)

    assert first.pair_revision_id == second.pair_revision_id
    manifest = other / "05_CONTROL" / "DOCUMENT_PAIR_STATE.json"
    data = json.loads(manifest.read_bytes().decode("utf-8"))
    assert data["pair_revision_id"] == first.pair_revision_id


def test_04_changed_flags_false_when_recommitting_identical_content(
    tmp_path: Path,
) -> None:
    _seed_workspace(tmp_path)
    live_bp, live_pr = _live_bytes(tmp_path)
    report = _commit(
        tmp_path,
        iteration=1,
        bp_text=live_bp.decode("utf-8"),
        pr_text=live_pr.decode("utf-8"),
    )
    assert report.blueprint_changed is False
    assert report.proposal_changed is False
    # The live pair is byte-identical to what was already there.
    bp_bytes, pr_bytes = _live_bytes(tmp_path)
    assert bp_bytes == live_bp
    assert pr_bytes == live_pr


def test_05_source_pack_id_round_trips_through_the_manifest(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    _commit(tmp_path, iteration=1, source_pack_id="sp-42")
    loaded = load_document_pair_state(tmp_path)
    assert loaded.source_pack_id == "sp-42"
    assert loaded.iteration == 1


# ---------------------------------------------------------------------------
# (b) phase gating — the machine must sit at INTEGRATION
# ---------------------------------------------------------------------------


def test_06_commit_refuses_off_integration_and_changes_nothing(
    tmp_path: Path,
) -> None:
    _seed_workspace(tmp_path)
    machine = ProposalStateMachine()
    machine.transition_to(ProposalPhase.SOURCE_VALIDATION)

    with pytest.raises(DocumentPairStateError) as excinfo:
        _commit(tmp_path, machine=machine)
    assert excinfo.value.reason == "phase_not_integration"
    assert excinfo.value.restored is False

    # NO file changed: documents keep their original bytes and no manifest.
    bp_bytes, pr_bytes = _live_bytes(tmp_path)
    assert bp_bytes == ORIGINAL_BP
    assert pr_bytes == ORIGINAL_PR
    assert not (tmp_path / "05_CONTROL" / "DOCUMENT_PAIR_STATE.json").exists()


def test_07_idle_machine_is_also_refused(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    with pytest.raises(DocumentPairStateError) as excinfo:
        _commit(tmp_path, machine=ProposalStateMachine())
    assert excinfo.value.reason == "phase_not_integration"
    bp_bytes, pr_bytes = _live_bytes(tmp_path)
    assert bp_bytes == ORIGINAL_BP
    assert pr_bytes == ORIGINAL_PR


# ---------------------------------------------------------------------------
# (c) empty revised texts
# ---------------------------------------------------------------------------


def test_08_empty_revised_texts_are_refused(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    cases = [
        ("empty_revised_blueprint", {"bp_text": ""}),
        ("empty_revised_blueprint", {"bp_text": "   \n\t"}),
        ("empty_revised_proposal", {"pr_text": ""}),
        ("empty_revised_proposal", {"pr_text": " \n "}),
    ]
    for expected_reason, overrides in cases:
        with pytest.raises(DocumentPairStateError) as excinfo:
            _commit(tmp_path, **overrides)  # type: ignore[arg-type]
        assert excinfo.value.reason == expected_reason
        assert excinfo.value.restored is False

    # Nothing was touched by any refused attempt.
    bp_bytes, pr_bytes = _live_bytes(tmp_path)
    assert bp_bytes == ORIGINAL_BP
    assert pr_bytes == ORIGINAL_PR
    assert not (tmp_path / "05_CONTROL" / "DOCUMENT_PAIR_STATE.json").exists()


# ---------------------------------------------------------------------------
# (d) missing CURRENT blueprint / missing proposal
# ---------------------------------------------------------------------------


def test_09_missing_current_blueprint_is_refused(tmp_path: Path) -> None:
    pr = tmp_path.joinpath(*MASTER_PROPOSAL_RELPATH.split("/"))
    pr.parent.mkdir(parents=True, exist_ok=True)
    pr.write_bytes(ORIGINAL_PR)

    with pytest.raises(DocumentPairStateError) as excinfo:
        _commit(tmp_path)
    assert excinfo.value.reason == "missing_current_blueprint"
    assert pr.read_bytes() == ORIGINAL_PR
    assert not (tmp_path / "05_CONTROL" / "DOCUMENT_PAIR_STATE.json").exists()


def test_10_missing_master_proposal_is_refused(tmp_path: Path) -> None:
    bp = tmp_path.joinpath(*CURRENT_BLUEPRINT_RELPATH.split("/"))
    bp.parent.mkdir(parents=True, exist_ok=True)
    bp.write_bytes(ORIGINAL_BP)

    with pytest.raises(DocumentPairStateError) as excinfo:
        _commit(tmp_path)
    assert excinfo.value.reason == "missing_master_proposal"
    assert bp.read_bytes() == ORIGINAL_BP
    assert not (tmp_path / "05_CONTROL" / "DOCUMENT_PAIR_STATE.json").exists()


# ---------------------------------------------------------------------------
# (e) iteration validation
# ---------------------------------------------------------------------------


def test_11_iteration_below_one_is_refused(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    for bad_iteration in (0, -1, -99):
        with pytest.raises(DocumentPairStateError) as excinfo:
            _commit(tmp_path, iteration=bad_iteration)
        assert excinfo.value.reason == "invalid_iteration"
    # Non-int / bool iterations are refused too.
    for bad in ("2", 1.5, True):
        with pytest.raises(DocumentPairStateError) as excinfo:
            commit_document_pair(
                workspace=tmp_path,
                state_machine=_integration_machine(),
                iteration_number=bad,  # type: ignore[arg-type]
                revised_blueprint_text=REVISED_BP_TEXT,
                revised_proposal_text=REVISED_PR_TEXT,
            )
        assert excinfo.value.reason == "invalid_iteration"

    bp_bytes, pr_bytes = _live_bytes(tmp_path)
    assert bp_bytes == ORIGINAL_BP
    assert pr_bytes == ORIGINAL_PR
    assert not (tmp_path / "05_CONTROL" / "DOCUMENT_PAIR_STATE.json").exists()


# ---------------------------------------------------------------------------
# (f) manifest load semantics
# ---------------------------------------------------------------------------


def test_12_load_manifest_missing_raises_and_try_load_returns_none(
    tmp_path: Path,
) -> None:
    with pytest.raises(DocumentPairStateError) as excinfo:
        load_document_pair_state(tmp_path)
    assert excinfo.value.reason == "missing_state"
    assert try_load_document_pair_state(tmp_path) is None


def test_13_corrupt_manifest_is_state_unreadable(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    control = tmp_path / "05_CONTROL"
    control.mkdir(parents=True, exist_ok=True)
    (control / "DOCUMENT_PAIR_STATE.json").write_bytes(b"{ corrupted json ")

    with pytest.raises(DocumentPairStateError) as excinfo:
        load_document_pair_state(tmp_path)
    assert excinfo.value.reason == "state_unreadable"
    # try_load does NOT swallow corruption — only absence.
    with pytest.raises(DocumentPairStateError) as excinfo:
        try_load_document_pair_state(tmp_path)
    assert excinfo.value.reason == "state_unreadable"


def test_14_wrong_schema_is_a_conflict(tmp_path: Path) -> None:
    control = tmp_path / "05_CONTROL"
    control.mkdir(parents=True, exist_ok=True)
    (control / "DOCUMENT_PAIR_STATE.json").write_bytes(
        json.dumps(
            {
                "schema": "encomm-pcc.document-pair-state/v0",
                "iteration": 1,
                "blueprint_hash": "a" * 64,
                "proposal_hash": "b" * 64,
                "previous_blueprint_hash": "",
                "previous_proposal_hash": "",
                "pair_revision_id": "x",
            }
        ).encode("utf-8")
    )

    with pytest.raises(DocumentPairStateError) as excinfo:
        load_document_pair_state(tmp_path)
    assert excinfo.value.reason == "state_schema_conflict"


# ---------------------------------------------------------------------------
# (g) DocumentPairState dataclass round-trip
# ---------------------------------------------------------------------------


def test_15_document_pair_state_round_trips_all_fields() -> None:
    state = DocumentPairState(
        iteration=5,
        blueprint_hash="a" * 64,
        proposal_hash="b" * 64,
        previous_blueprint_hash="c" * 64,
        previous_proposal_hash="d" * 64,
        pair_revision_id="e" * 32,
        source_pack_id="pack-7",
    )
    assert state.schema == DOCUMENT_PAIR_STATE_SCHEMA

    data = state.to_dict()
    assert data["schema"] == DOCUMENT_PAIR_STATE_SCHEMA
    assert data["iteration"] == 5
    assert data["source_pack_id"] == "pack-7"

    restored = DocumentPairState.from_dict(dict(data))
    assert restored == state
    assert restored.to_dict() == data


def test_16_from_dict_rejects_invalid_iterations() -> None:
    base = {
        "schema": DOCUMENT_PAIR_STATE_SCHEMA,
        "iteration": 3,
        "blueprint_hash": "a" * 64,
        "proposal_hash": "b" * 64,
    }
    # NB: ``from_dict`` coerces floats via ``int()`` (2.5 → 2 is accepted by
    # design); only the direct constructor refuses non-int iterations.
    for bad_value in (0, -2, "x"):
        payload = dict(base, iteration=bad_value)
        with pytest.raises(DocumentPairStateError) as excinfo:
            DocumentPairState.from_dict(payload)
        assert excinfo.value.reason == "invalid_iteration"


def test_17_from_dict_rejects_non_object_and_bad_schema() -> None:
    with pytest.raises(DocumentPairStateError) as excinfo:
        DocumentPairState.from_dict(["not", "an", "object"])  # type: ignore[arg-type]
    assert excinfo.value.reason == "state_not_object"

    with pytest.raises(DocumentPairStateError) as excinfo:
        DocumentPairState.from_dict({"schema": "some-other/schema", "iteration": 1})
    assert excinfo.value.reason == "state_schema_conflict"


# ---------------------------------------------------------------------------
# (h) SIMULATED second-replace failure → rollback proof
# ---------------------------------------------------------------------------


def test_18_second_replace_failure_restores_blueprint_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_workspace(tmp_path)
    real_replace = os.replace
    proposal_attempts: list[str] = []

    def flaky_replace(src: object, dst: object, **kwargs: object) -> None:
        # Let the FIRST replacement (the blueprint) through untouched; fail
        # ONLY the MASTER_PROPOSAL.md destination (the second replacement).
        if str(dst).endswith("MASTER_PROPOSAL.md"):
            proposal_attempts.append(str(dst))
            raise OSError("simulated second-replace failure")
        real_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "encomm_pcc.proposal.document_pair.os.replace", flaky_replace
    )

    with pytest.raises(DocumentPairStateError) as excinfo:
        _commit(tmp_path, iteration=1)

    error = excinfo.value
    assert error.reason == "proposal_replace_failed"
    assert error.restored is True

    # The blueprint was restored to its EXACT pre-commit bytes.
    bp_bytes, pr_bytes = _live_bytes(tmp_path)
    assert bp_bytes == ORIGINAL_BP
    # The proposal was NEVER replaced — still the original bytes.
    assert pr_bytes == ORIGINAL_PR
    # No manifest exists: the commit never became visible.
    assert not (tmp_path / "05_CONTROL" / "DOCUMENT_PAIR_STATE.json").exists()
    # NOTE (product observation, not asserted): the staged proposal temp
    # file (.tmp-pairpr-*.part) is not unlinked on the second-replace
    # failure path — a benign hygiene leak, reported to the caller.


# ---------------------------------------------------------------------------
# (i) hashes/versions after success — explicit re-fingerprint
# ---------------------------------------------------------------------------


def test_19_refingerprinted_files_match_manifest_after_success(
    tmp_path: Path,
) -> None:
    _seed_workspace(tmp_path)
    report = _commit(tmp_path, iteration=9)

    bp_bytes, pr_bytes = _live_bytes(tmp_path)
    assert hashlib.sha256(bp_bytes).hexdigest() == report.blueprint_hash
    assert hashlib.sha256(pr_bytes).hexdigest() == report.proposal_hash
    assert report.previous_blueprint_hash == _sha256(ORIGINAL_BP)
    assert report.previous_proposal_hash == _sha256(ORIGINAL_PR)

    manifest = load_document_pair_state(tmp_path)
    assert manifest.iteration == 9
    assert manifest.blueprint_hash == report.blueprint_hash
    assert manifest.proposal_hash == report.proposal_hash
    assert manifest.previous_blueprint_hash == report.previous_blueprint_hash
    assert manifest.previous_proposal_hash == report.previous_proposal_hash
