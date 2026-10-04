"""Session 021B — state/raw-contract invariant regression tests (offline).

Covers the brief's five correction points:

1.  BLUEPRINT_STATE advances with EVERY committed document pair
    (blueprint change / proposal-only change / both identical), a
    simulated BLUEPRINT_STATE write failure rolls the documents AND the
    state back exactly, a simulated DOCUMENT_PAIR_STATE final-write
    failure does the same, and a TWO-ITERATION sequence stays coherent.
2.  The consensus round is READ-ONLY against BOTH documents (a Blueprint
    mutation fails the round; an untouched pair succeeds).
3.  Initial-generation specialists are READ-ONLY against both living
    documents AND the immutable original (mutation barrier before ASTRA).
4.  The panel's REAL source_pack_id is preserved into the committed
    DOCUMENT_PAIR_STATE through the chair composition.

Zero model calls, zero network: every driver is a scripted double.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

import encomm_pcc.proposal as pp
import encomm_pcc.proposal_runtime as prt
from encomm_pcc.drivers.base import (
    DriverCapabilities,
    DriverSession,
    PromptHandle,
    PromptResult,
    SessionRequest,
)
from encomm_pcc.proposal.document_pair import (
    DocumentPairStateError,
    commit_document_pair,
    try_load_document_pair_state,
)
from encomm_pcc.proposal.enums import ProposalPhase, ProposalRole
from encomm_pcc.proposal.fingerprint import proposal_fingerprint
from encomm_pcc.proposal.living_blueprint import (
    BlueprintStateError,
    ensure_current_blueprint,
    load_blueprint_state,
)
from encomm_pcc.proposal.source_import import import_source
from encomm_pcc.proposal.state_machine import ProposalStateMachine
from encomm_pcc.proposal.workspace import MASTER_PROPOSAL_RELPATH, ProposalWorkspace
from encomm_pcc.proposal_runtime.initial_generation import (
    InitialGenerationOutcome,
    run_initial_generation,
)
from encomm_pcc.proposal_runtime.panel_chair import (
    PanelChairOutcome,
    run_panel_chair_iteration,
)
from encomm_pcc.proposal_runtime.panel_runtime import run_panel_consensus_round
from encomm_pcc.proposal.source_snapshot import load_review_snapshot

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = str(REPO_ROOT / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

CURRENT_BP_RELPATH = "00_SOURCE_OF_TRUTH/CURRENT_BLUEPRINT.md"
MASTER_BP_RELPATH = "00_SOURCE_OF_TRUTH/MASTER_BLUEPRINT.md"

TEMPLATE = "# 1. Excellence\n\n# 2. Impact\n"
BLUEPRINT_ORIGINAL = b"# Blueprint\n\nKALHAS evidence base.\n"
MASTER_V1 = "# 1. Excellence\n\nKALHAS drives the architecture.\n"
MASTER_V2 = (
    "# 1. Excellence\n\nKALHAS drives the architecture.\n\n"
    "## Panel resolution\n\nThe finding is addressed with evidence.\n"
)
BLUEPRINT_V2 = "# Blueprint\n\nKALHAS evidence base.\n\n## Revised design\n\nv2.\n"
REVISION = "rev-021b"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def seed_dual_workspace(tmp_path: Path, *, with_master: bool) -> Path:
    """Workspace + imported template/blueprint + living pair (and master)."""
    ws = tmp_path / "ws"
    ProposalWorkspace(ws).initialize()
    tpl_src = tmp_path / "tpl.md"
    tpl_src.write_bytes(TEMPLATE.encode("utf-8"))
    import_source(ws, tpl_src, import_role="application_template")
    bp_src = tmp_path / "bp.md"
    bp_src.write_bytes(BLUEPRINT_ORIGINAL)
    import_source(ws, bp_src, import_role="master_blueprint")
    ensure_current_blueprint(ws)
    if with_master:
        ws.joinpath(*MASTER_PROPOSAL_RELPATH.split("/")).write_bytes(
            MASTER_V1.encode("utf-8")
        )
    return ws


def walk_to_integration(machine: ProposalStateMachine) -> ProposalStateMachine:
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


# ---------------------------------------------------------------------------
# §1 — BLUEPRINT_STATE advances with every committed pair
# ---------------------------------------------------------------------------
class TestBlueprintStateAdvance:
    def _commit(self, ws: Path, *, iteration: int, bp_text: str, pr_text: str):
        machine = walk_to_integration(ProposalStateMachine())
        return commit_document_pair(
            workspace=ws,
            state_machine=machine,
            iteration_number=iteration,
            revised_blueprint_text=bp_text,
            revised_proposal_text=pr_text,
            source_pack_id="srcpack-021b",
        )

    def _live_bp(self, ws: Path) -> bytes:
        return ws.joinpath(*CURRENT_BP_RELPATH.split("/")).read_bytes()

    def _live_pr(self, ws: Path) -> bytes:
        return ws.joinpath(*MASTER_PROPOSAL_RELPATH.split("/")).read_bytes()

    def test_01_blueprint_change_advances_state_and_ensure_passes(
        self, tmp_path: Path
    ) -> None:
        ws = seed_dual_workspace(tmp_path, with_master=True)
        state_before, _ = ensure_current_blueprint(ws)
        report = self._commit(
            ws, iteration=1, bp_text=BLUEPRINT_V2, pr_text=MASTER_V2
        )
        # The state advanced with the pair…
        state = load_blueprint_state(ws)
        assert state.original_blueprint_hash == state_before.original_blueprint_hash
        assert state.current_blueprint_hash == sha(self._live_bp(ws))
        assert state.current_blueprint_hash == report.blueprint_hash
        assert state.current_blueprint_iteration == 1
        manifest = try_load_document_pair_state(ws)
        assert manifest is not None
        assert state.last_pair_revision_id == manifest.pair_revision_id
        # …and ensure_current_blueprint() succeeds immediately: NO drift.
        state_after, migrated = ensure_current_blueprint(ws)
        assert migrated is False
        assert state_after.current_blueprint_hash == sha(self._live_bp(ws))

    def test_02_identical_blueprint_proposal_change_still_advances(
        self, tmp_path: Path
    ) -> None:
        ws = seed_dual_workspace(tmp_path, with_master=True)
        live_bp = self._live_bp(ws)
        report = self._commit(
            ws, iteration=4, bp_text=live_bp.decode("utf-8"), pr_text=MASTER_V2
        )
        assert report.blueprint_changed is False
        assert report.proposal_changed is True
        state = load_blueprint_state(ws)
        assert state.current_blueprint_hash == sha(live_bp)
        assert state.current_blueprint_iteration == 4
        manifest = try_load_document_pair_state(ws)
        assert manifest is not None
        assert manifest.iteration == 4
        assert state.last_pair_revision_id == manifest.pair_revision_id
        ensure_current_blueprint(ws)  # must not raise current_drift

    def test_03_both_identical_state_stays_coherent(self, tmp_path: Path) -> None:
        ws = seed_dual_workspace(tmp_path, with_master=True)
        live_bp = self._live_bp(ws)
        live_pr = self._live_pr(ws)
        report = self._commit(
            ws,
            iteration=7,
            bp_text=live_bp.decode("utf-8"),
            pr_text=live_pr.decode("utf-8"),
        )
        assert report.blueprint_changed is False
        assert report.proposal_changed is False
        state = load_blueprint_state(ws)
        assert state.current_blueprint_hash == sha(live_bp)
        assert state.current_blueprint_iteration == 7
        manifest = try_load_document_pair_state(ws)
        assert manifest is not None
        assert manifest.blueprint_hash == sha(live_bp)
        assert manifest.proposal_hash == sha(live_pr)
        assert state.last_pair_revision_id == manifest.pair_revision_id
        ensure_current_blueprint(ws)  # coherent

    def test_04_state_write_failure_rolls_back_docs_and_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = seed_dual_workspace(tmp_path, with_master=True)
        state_before = load_blueprint_state(ws)
        prior_state_bytes = (
            ws / "05_CONTROL" / "BLUEPRINT_STATE.json"
        ).read_bytes()
        prior_bp = self._live_bp(ws)
        prior_pr = self._live_pr(ws)

        real_write = None
        from encomm_pcc.proposal import document_pair as dp

        real_write = dp._atomic_write_bytes
        calls: list[str] = []

        def flaky_write(target, data):
            name = Path(str(target)).name
            if name == "BLUEPRINT_STATE.json" and not calls:
                calls.append(name)
                raise OSError("simulated BLUEPRINT_STATE write failure")
            return real_write(target, data)

        monkeypatch.setattr(dp, "_atomic_write_bytes", flaky_write)

        machine = walk_to_integration(ProposalStateMachine())
        with pytest.raises(DocumentPairStateError) as excinfo:
            commit_document_pair(
                workspace=ws,
                state_machine=machine,
                iteration_number=1,
                revised_blueprint_text=BLUEPRINT_V2,
                revised_proposal_text=MASTER_V2,
            )
        error = excinfo.value
        assert error.reason == "state_write_failed"
        assert error.restored is True
        # EXACT rollback: both documents AND the prior state bytes.
        assert self._live_bp(ws) == prior_bp
        assert self._live_pr(ws) == prior_pr
        assert (
            ws / "05_CONTROL" / "BLUEPRINT_STATE.json"
        ).read_bytes() == prior_state_bytes
        assert load_blueprint_state(ws).current_blueprint_iteration == (
            state_before.current_blueprint_iteration
        )
        # No new commit marker: the previous pair state stays authoritative.
        assert not (ws / "05_CONTROL" / "DOCUMENT_PAIR_STATE.json").exists()

    def test_05_manifest_write_failure_rolls_back_docs_and_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = seed_dual_workspace(tmp_path, with_master=True)
        prior_state_bytes = (
            ws / "05_CONTROL" / "BLUEPRINT_STATE.json"
        ).read_bytes()
        prior_bp = self._live_bp(ws)
        prior_pr = self._live_pr(ws)

        from encomm_pcc.proposal import document_pair as dp

        real_write = dp._atomic_write_bytes

        def flaky_write(target, data):
            name = Path(str(target)).name
            if name == "DOCUMENT_PAIR_STATE.json":
                raise OSError("simulated final manifest write failure")
            return real_write(target, data)

        monkeypatch.setattr(dp, "_atomic_write_bytes", flaky_write)

        machine = walk_to_integration(ProposalStateMachine())
        with pytest.raises(DocumentPairStateError) as excinfo:
            commit_document_pair(
                workspace=ws,
                state_machine=machine,
                iteration_number=1,
                revised_blueprint_text=BLUEPRINT_V2,
                revised_proposal_text=MASTER_V2,
            )
        error = excinfo.value
        assert error.reason == "manifest_write_failed"
        assert error.restored is True
        assert self._live_bp(ws) == prior_bp
        assert self._live_pr(ws) == prior_pr
        # The advanced state was rolled back to the PRIOR bytes exactly.
        assert (
            ws / "05_CONTROL" / "BLUEPRINT_STATE.json"
        ).read_bytes() == prior_state_bytes
        # The manifest (the final commit marker) never landed.
        assert not (ws / "05_CONTROL" / "DOCUMENT_PAIR_STATE.json").exists()

    def test_06_two_iteration_sequence_tracks_both_pairs(
        self, tmp_path: Path
    ) -> None:
        ws = seed_dual_workspace(tmp_path, with_master=True)
        # ITERATION 1: the Blueprint changes.
        self._commit(ws, iteration=1, bp_text=BLUEPRINT_V2, pr_text=MASTER_V2)
        ensure_current_blueprint(ws)  # succeeds between the iterations
        bp_v2 = self._live_bp(ws)
        # ITERATION 2: another real pair commit over the evolved pair.
        bp_v3 = BLUEPRINT_V2 + "\n## v3 addendum\n\nmore design.\n"
        pr_v3 = MASTER_V2 + "\n## Iteration 2\n\nrefined.\n"
        self._commit(ws, iteration=2, bp_text=bp_v3, pr_text=pr_v3)
        state = load_blueprint_state(ws)
        assert state.current_blueprint_hash == sha(self._live_bp(ws))
        assert state.current_blueprint_iteration == 2
        manifest = try_load_document_pair_state(ws)
        assert manifest is not None
        assert manifest.iteration == 2
        assert manifest.previous_blueprint_hash == sha(bp_v2)
        assert state.last_pair_revision_id == manifest.pair_revision_id
        state_after, migrated = ensure_current_blueprint(ws)
        assert migrated is False


# ---------------------------------------------------------------------------
# §2 — the consensus round is READ-ONLY against BOTH documents
# ---------------------------------------------------------------------------
def _consensus_body(role_value: str, proposal_hash: str) -> dict:
    return {
        "role": role_value,
        "iteration_number": 1,
        "proposal_hash": proposal_hash,
        "judgements": [
            {
                "item_id": "F001",
                "judgement": "AGREE",
                "rationale": "assessed",
                "proposed_resolution": "add the evidence",
                "source_refs": [],
                "blocks_acceptance": False,
            }
        ],
        "readiness_assessment": {
            "criteria": [
                {"criterion": "scientific_coherence", "score": 80, "rationale": ""},
                {"criterion": "impact_coherence", "score": 70, "rationale": ""},
            ]
        },
        "summary": f"{role_value} consensus",
    }


def _consensus_text(role_value: str, proposal_hash: str) -> str:
    body = _consensus_body(role_value, proposal_hash)
    return (
        "<<<ENCOMM_PANEL_CONSENSUS_START>>>\n"
        + json.dumps(body)
        + "\n<<<ENCOMM_PANEL_CONSENSUS_END>>>"
    )


class ConsensusDriver:
    """Scripted consensus evaluator; optionally mutates a workspace file."""

    driver_id = "scripted-021b-consensus"

    def __init__(
        self,
        role: ProposalRole,
        proposal_hash: str,
        *,
        mutate_path: str = "",
        mutate_bytes: bytes = b"",
        create_bytes: bytes = b"",
    ) -> None:
        self.role = role
        self.proposal_hash = proposal_hash
        self.mutate_path = mutate_path
        self.mutate_bytes = mutate_bytes
        self.create_bytes = create_bytes
        self.calls = 0

    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name="Consensus",
        )

    def start_session(self, request):
        return DriverSession(
            driver_id=self.driver_id,
            role=request.role,
            metadata={"workspace_path": request.workspace_path},
        )

    def send_prompt(self, session, prompt):
        return PromptHandle(session=session, prompt=prompt)

    def wait_for_completion(self, handle, timeout_s=None):
        if self.mutate_path:
            ws = Path(handle.session.metadata["workspace_path"])
            target = ws.joinpath(*self.mutate_path.split("/"))
            if self.create_bytes and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(self.create_bytes)
            elif self.mutate_bytes:
                target.write_bytes(self.mutate_bytes)
        self.calls += 1
        return PromptResult(
            ok=True,
            text=_consensus_text(self.role.value, self.proposal_hash),
            session_id="cons-1",
        )


def _bundle_and_docket(ws: Path):
    from encomm_pcc.proposal.models import ProposalReviewResult
    from encomm_pcc.proposal.panel_matrix import build_panel_docket
    from encomm_pcc.proposal.review_aggregation import aggregate_reviews

    ph = proposal_fingerprint(
        ws.joinpath(*MASTER_PROPOSAL_RELPATH.split("/"))
    )

    def res(role_value: str, sev: str, msg: str) -> ProposalReviewResult:
        return ProposalReviewResult.from_dict(
            {
                "reviewer_role": role_value,
                "iteration_number": 1,
                "proposal_hash": ph,
                "verdict": "NEEDS_REVISION",
                "summary": "s",
                "findings": [
                    {
                        "severity": sev,
                        "category": "missing_evidence",
                        "section": "1. Excellence",
                        "message": msg,
                        "evidence": "e",
                        "source_refs": [],
                        "suggested_change": "add evidence",
                    }
                ],
                "proposed_patches": [],
                "unverified_claims": [],
            }
        )

    bundle = aggregate_reviews(
        iteration_number=1,
        proposal_revision=REVISION,
        proposal_hash=ph,
        scientific_review=res("SCIENTIFIC_REVIEWER", "critical", "crit"),
        implementation_review=res("PROPOSAL_ENGINEER", "high", "high issue"),
        red_team_review=res("RED_TEAM_REVIEWER", "low", "minor"),
    )
    docket = build_panel_docket(bundle, source_pack_id="srcpack-021b")
    return ph, bundle, docket


def _consensus_drivers(ws: Path, ph: str, *, mutate_role: ProposalRole | None = None,
                       mutate_path: str = "", mutate_bytes: bytes = b"",
                       create_bytes: bytes = b""):
    drivers = {}
    for role in (
        ProposalRole.SCIENTIFIC_REVIEWER,
        ProposalRole.PROPOSAL_ENGINEER,
        ProposalRole.RED_TEAM_REVIEWER,
    ):
        if role is mutate_role:
            drivers[role] = ConsensusDriver(
                role,
                ph,
                mutate_path=mutate_path,
                mutate_bytes=mutate_bytes,
                create_bytes=create_bytes,
            )
        else:
            drivers[role] = ConsensusDriver(role, ph)
    return drivers


class TestConsensusReadOnly:
    def test_07_consensus_mutates_blueprint_fails_closed(
        self, tmp_path: Path
    ) -> None:
        ws = seed_dual_workspace(tmp_path, with_master=True)
        ph, bundle, docket = _bundle_and_docket(ws)
        frozen_bp = ws.joinpath(*CURRENT_BP_RELPATH.split("/")).read_bytes()
        drivers = _consensus_drivers(
            ws,
            ph,
            mutate_role=ProposalRole.RED_TEAM_REVIEWER,
            mutate_path=CURRENT_BP_RELPATH,
            mutate_bytes=frozen_bp + b"mutated by consensus\n",
        )
        snapshot = load_review_snapshot(ws)
        with pytest.raises(RuntimeError) as excinfo:
            run_panel_consensus_round(
                workspace=ws,
                iteration_number=1,
                proposal_revision=REVISION,
                proposal_hash=ph,
                bundle=bundle,
                docket=docket,
                source_snapshot=snapshot,
                consensus_drivers=drivers,  # type: ignore[arg-type]
            )
        assert "CURRENT_BLUEPRINT changed during the consensus round" in str(
            excinfo.value
        )
        # Nothing accepted: no matrix, no readiness.
        assert not (ws / "04_REVIEWS/iteration_001/panel_consensus.json").exists()
        assert not (ws / "04_REVIEWS/iteration_001/readiness.json").exists()

    def test_08_consensus_mutates_proposal_fails_closed(
        self, tmp_path: Path
    ) -> None:
        ws = seed_dual_workspace(tmp_path, with_master=True)
        ph, bundle, docket = _bundle_and_docket(ws)
        drivers = _consensus_drivers(
            ws,
            ph,
            mutate_role=ProposalRole.PROPOSAL_ENGINEER,
            mutate_path=MASTER_PROPOSAL_RELPATH,
            mutate_bytes=b"# mutated proposal\n",
        )
        snapshot = load_review_snapshot(ws)
        with pytest.raises(RuntimeError) as excinfo:
            run_panel_consensus_round(
                workspace=ws,
                iteration_number=1,
                proposal_revision=REVISION,
                proposal_hash=ph,
                bundle=bundle,
                docket=docket,
                source_snapshot=snapshot,
                consensus_drivers=drivers,  # type: ignore[arg-type]
            )
        assert "MASTER_PROPOSAL changed during the consensus round" in str(
            excinfo.value
        )
        assert not (ws / "04_REVIEWS/iteration_001/panel_consensus.json").exists()

    def test_09_untouched_pair_consensus_succeeds(self, tmp_path: Path) -> None:
        ws = seed_dual_workspace(tmp_path, with_master=True)
        ph, bundle, docket = _bundle_and_docket(ws)
        frozen_bp = ws.joinpath(*CURRENT_BP_RELPATH.split("/")).read_bytes()
        drivers = _consensus_drivers(ws, ph)
        snapshot = load_review_snapshot(ws)
        summary = run_panel_consensus_round(
            workspace=ws,
            iteration_number=1,
            proposal_revision=REVISION,
            proposal_hash=ph,
            bundle=bundle,
            docket=docket,
            source_snapshot=snapshot,
            consensus_drivers=drivers,  # type: ignore[arg-type]
        )
        assert summary["matrix"]["iteration_number"] == 1
        assert (ws / "04_REVIEWS/iteration_001/panel_consensus.json").is_file()
        # The untouched living pair survived verbatim.
        assert (
            ws.joinpath(*CURRENT_BP_RELPATH.split("/")).read_bytes() == frozen_bp
        )


# ---------------------------------------------------------------------------
# §3 — initial specialists are READ-ONLY against the document pair
# ---------------------------------------------------------------------------
class SpecialistDriver:
    """Scripted initial-generation specialist with an optional mutation."""

    driver_id = "scripted-021b-specialist"

    def __init__(
        self,
        role: ProposalRole,
        *,
        mutate_path: str = "",
        mutate_bytes: bytes = b"",
        create_bytes: bytes = b"",
    ) -> None:
        self.role = role
        self.mutate_path = mutate_path
        self.mutate_bytes = mutate_bytes
        self.create_bytes = create_bytes
        self.calls = 0

    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name="Specialist",
        )

    def start_session(self, request):
        return DriverSession(
            driver_id=self.driver_id,
            role=request.role,
            metadata={"workspace_path": request.workspace_path},
        )

    def send_prompt(self, session, prompt):
        return PromptHandle(session=session, prompt=prompt)

    def wait_for_completion(self, handle, timeout_s=None):
        if self.mutate_path:
            ws = Path(handle.session.metadata["workspace_path"])
            target = ws.joinpath(*self.mutate_path.split("/"))
            if self.create_bytes and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(self.create_bytes)
            elif self.mutate_bytes:
                target.write_bytes(self.mutate_bytes)
        self.calls += 1
        return PromptResult(
            ok=True,
            text=(
                "<<<INITIAL_CONTRIBUTION_START>>>\n"
                f"{self.role.value} contribution grounded in the design.\n"
                "<<<INITIAL_CONTRIBUTION_END>>>"
            ),
            session_id="sp-1",
        )


class GenerationAstra:
    """Records prompts; answers the initial synthesis (keeps CURRENT)."""

    driver_id = "scripted-021b-gen-astra"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name="GenAstra",
        )

    def start_session(self, request):
        return DriverSession(driver_id=self.driver_id, role=request.role)

    def send_prompt(self, session, prompt):
        self.prompts.append(prompt)
        return PromptHandle(session=session, prompt=prompt)

    def wait_for_completion(self, handle, timeout_s=None):
        return PromptResult(
            ok=True,
            text=(
                "<<<INITIAL_PROPOSAL_START>>>\n"
                "# 1. Excellence\n\nInitial draft.\n\n# 2. Impact\n\n"
                "[INPUT REQUIRED: evidence]\n"
                "<<<INITIAL_PROPOSAL_END>>>"
            ),
            session_id="gen-astra-1",
        )


def _specialist_map(mutate: dict | None = None):
    mutate = mutate or {}
    return {
        role: SpecialistDriver(
            role,
            mutate_path=mutate.get("path", ""),
            mutate_bytes=mutate.get("bytes", b""),
            create_bytes=mutate.get("create", b""),
        )
        for role in (
            ProposalRole.SCIENTIFIC_REVIEWER,
            ProposalRole.PROPOSAL_ENGINEER,
            ProposalRole.RED_TEAM_REVIEWER,
        )
    }


class TestSpecialistBarrier:
    def test_10_specialist_mutates_current_blueprint_blocked(
        self, tmp_path: Path
    ) -> None:
        ws = seed_dual_workspace(tmp_path, with_master=False)
        frozen_bp = ws.joinpath(*CURRENT_BP_RELPATH.split("/")).read_bytes()
        astra = GenerationAstra()
        machine = ProposalStateMachine()
        report = run_initial_generation(
            workspace=ws,
            state_machine=machine,
            proposal_revision=REVISION,
            specialist_drivers=_specialist_map(
                {
                    "path": CURRENT_BP_RELPATH,
                    "bytes": frozen_bp + b"mutated by a specialist\n",
                }
            ),
            orchestrator_driver=astra,  # type: ignore[arg-type]
        )
        assert report.outcome is InitialGenerationOutcome.SPECIALIST_FAILED
        assert "SPECIALIST MUTATION" in report.error
        assert "CURRENT_BLUEPRINT" in report.error
        # ASTRA synthesis NEVER ran; the runtime wrote nothing.  The empty
        # MASTER_PROPOSAL scaffold stays byte-EMPTY (initialized empty).
        assert astra.prompts == []
        master_bytes = ws.joinpath(*MASTER_PROPOSAL_RELPATH.split("/")).read_bytes()
        assert master_bytes == b""
        assert not (ws / "05_CONTROL" / "DOCUMENT_PAIR_STATE.json").exists()
        assert machine.phase is ProposalPhase.IDLE

    def test_11_specialist_writes_into_empty_master_proposal_blocked(
        self, tmp_path: Path
    ) -> None:
        ws = seed_dual_workspace(tmp_path, with_master=False)
        astra = GenerationAstra()
        machine = ProposalStateMachine()
        report = run_initial_generation(
            workspace=ws,
            state_machine=machine,
            proposal_revision=REVISION,
            specialist_drivers=_specialist_map(
                {
                    # The scaffold file EXISTS but is EMPTY (0 bytes); the
                    # barrier froze exactly those bytes — a specialist
                    # drafting content into it MUTATES the frozen empty
                    # bytes, detected before ASTRA runs.
                    "path": MASTER_PROPOSAL_RELPATH,
                    "bytes": b"# specialist-drafted proposal\n",
                }
            ),
            orchestrator_driver=astra,  # type: ignore[arg-type]
        )
        assert report.outcome is InitialGenerationOutcome.SPECIALIST_FAILED
        assert "SPECIALIST MUTATION" in report.error
        assert "03_PROPOSAL/MASTER_PROPOSAL.md" in report.error
        assert astra.prompts == []
        assert machine.phase is ProposalPhase.IDLE

    def test_12_specialist_mutates_immutable_master_blueprint_blocked(
        self, tmp_path: Path
    ) -> None:
        ws = seed_dual_workspace(tmp_path, with_master=False)
        astra = GenerationAstra()
        machine = ProposalStateMachine()
        report = run_initial_generation(
            workspace=ws,
            state_machine=machine,
            proposal_revision=REVISION,
            specialist_drivers=_specialist_map(
                {
                    "path": MASTER_BP_RELPATH,
                    "bytes": b"# REWRITTEN immutable original\n",
                }
            ),
            orchestrator_driver=astra,  # type: ignore[arg-type]
        )
        assert report.outcome is InitialGenerationOutcome.SPECIALIST_FAILED
        assert "MASTER_BLUEPRINT" in report.error
        assert astra.prompts == []
        assert machine.phase is ProposalPhase.IDLE

    def test_13_clean_specialists_complete_and_advance_state(
        self, tmp_path: Path
    ) -> None:
        ws = seed_dual_workspace(tmp_path, with_master=False)
        astra = GenerationAstra()
        machine = ProposalStateMachine()
        report = run_initial_generation(
            workspace=ws,
            state_machine=machine,
            proposal_revision=REVISION,
            specialist_drivers=_specialist_map(),
            orchestrator_driver=astra,  # type: ignore[arg-type]
        )
        assert report.outcome is InitialGenerationOutcome.COMPLETED, (
            report.error
        )
        assert len(astra.prompts) == 1
        assert machine.phase is ProposalPhase.IDLE
        # The initial pair commit advanced BLUEPRINT_STATE too (S021B).
        manifest = try_load_document_pair_state(ws)
        assert manifest is not None and manifest.iteration == 1
        state = load_blueprint_state(ws)
        assert state.current_blueprint_iteration == 1
        assert state.current_blueprint_hash == sha(
            ws.joinpath(*CURRENT_BP_RELPATH.split("/")).read_bytes()
        )
        assert state.last_pair_revision_id == manifest.pair_revision_id
        ensure_current_blueprint(ws)  # no drift after generation


# ---------------------------------------------------------------------------
# §4 (+ §1 at composition level) — chair thread: source_pack_id + state
# ---------------------------------------------------------------------------
def _review_text(role_value: str, proposal_hash: str, iteration: int) -> str:
    body = {
        "reviewer_role": role_value,
        "iteration_number": iteration,
        "proposal_hash": proposal_hash,
        "verdict": "NEEDS_REVISION",
        "summary": f"{role_value} position",
        "findings": [
            {
                "severity": (
                    "high" if role_value == "SCIENTIFIC_REVIEWER" else "medium"
                ),
                "category": "missing_evidence",
                "section": "1. Excellence",
                "message": f"{role_value} flagged evidence",
                "evidence": "state it",
                "source_refs": [],
                "suggested_change": "add the evidence",
                "target": "PROPOSAL",
            }
        ],
        "proposed_patches": [],
        "unverified_claims": [],
    }
    return (
        f"{pp.PROPOSAL_REVIEW_ENVELOPE_START}\n"
        + json.dumps(body)
        + f"\n{pp.PROPOSAL_REVIEW_ENVELOPE_END}"
    )


def _prompt_meta(prompt: str, key: str) -> str:
    for line in prompt.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"- {key}"):
            raw = stripped.split(":", 1)[1].strip()
            token = raw.split(" ", 1)[0].strip()
            if key.endswith("hash") and len(token) != 64:
                continue
            return token
    return ""


class ChairDriver:
    """Scripted panel driver: review / consensus / dual ASTRA answers."""

    driver_id = "scripted-021b-chair"

    def __init__(
        self,
        role_value: str,
        *,
        revised_blueprint: str | None = BLUEPRINT_V2,
    ) -> None:
        self.role_value = role_value
        self.revised_blueprint = revised_blueprint
        self.prompts: list[str] = []

    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name="Chair Driver",
            supports_sessions=False,
            supports_resume=False,
            requires_profile=False,
            supports_model_selection=False,
            supports_streaming=False,
            supports_cancellation=False,
        )

    @classmethod
    def probe_availability(cls) -> bool:
        return True

    @classmethod
    def describe(cls) -> dict:
        return {"driver_id": cls.driver_id}

    def discover_sessions(self, **kwargs):
        raise NotImplementedError

    def start_session(self, request: SessionRequest) -> DriverSession:
        return DriverSession(driver_id=self.driver_id, role=request.role)

    def resume_session(self, session_id: str, request: SessionRequest):
        raise NotImplementedError

    def send_prompt(self, session: DriverSession, prompt: str) -> PromptHandle:
        self.prompts.append(prompt)
        return PromptHandle(session=session, prompt=prompt)

    def wait_for_completion(self, handle: PromptHandle, timeout_s=None) -> PromptResult:
        prompt = handle.prompt
        if "PROPOSAL INTEGRATION REQUEST" in prompt:
            iteration = 1
            for line in prompt.splitlines():
                s = line.strip()
                if s.startswith("- iteration_number"):
                    token = s.split(":", 1)[1].strip().split(" ", 1)[0]
                    if token.isdigit():
                        iteration = int(token)
                    break
            body = {
                "role": "ORCHESTRATOR",
                "iteration_number": iteration,
                "input_proposal_hash": _prompt_meta(prompt, "input_proposal_hash"),
                "summary": "chair synthesis applied",
                "revised_proposal": MASTER_V2,
            }
            bp_hash = _prompt_meta(prompt, "input_blueprint_hash")
            if bp_hash and self.revised_blueprint is not None:
                body["input_blueprint_hash"] = bp_hash
                body["revised_blueprint"] = self.revised_blueprint
            text = (
                f"{pp.PROPOSAL_INTEGRATION_ENVELOPE_START}\n"
                + json.dumps(body)
                + f"\n{pp.PROPOSAL_INTEGRATION_ENVELOPE_END}"
            )
            return PromptResult(ok=True, text=text, session_id="astra-1")
        if "PANEL CONSENSUS" in prompt:
            ph = _prompt_meta(prompt, "proposal_hash")
            return PromptResult(
                ok=True,
                text=_consensus_text(self.role_value, ph),
                session_id="cons-1",
            )
        # independent first-pass review
        ph = _prompt_meta(prompt, "proposal_hash")
        return PromptResult(
            ok=True,
            text=_review_text(self.role_value, ph, 1),
            session_id="rev-1",
        )


def _chair_drivers(ws: Path):
    drivers = {
        role: ChairDriver(role.value)
        for role in (
            ProposalRole.SCIENTIFIC_REVIEWER,
            ProposalRole.PROPOSAL_ENGINEER,
            ProposalRole.RED_TEAM_REVIEWER,
        )
    }
    astra = ChairDriver("ORCHESTRATOR")
    return drivers, astra


class TestChairThreadInvariants:
    def test_14_panel_source_pack_id_persists_into_pair_state(
        self, tmp_path: Path
    ) -> None:
        ws = seed_dual_workspace(tmp_path, with_master=True)
        machine = ProposalStateMachine()
        drivers, astra = _chair_drivers(ws)
        report = run_panel_chair_iteration(
            workspace=ws,
            state_machine=machine,
            iteration_number=1,
            proposal_revision=REVISION,
            reviewer_drivers=drivers,  # type: ignore[arg-type]
            orchestrator_driver=astra,  # type: ignore[arg-type]
        )
        assert report.outcome is PanelChairOutcome.READY_FOR_NEXT_ITERATION, (
            report.error
        )
        assert report.source_pack_id.startswith("srcpack")
        docket = json.loads(
            (ws / "04_REVIEWS/iteration_001/panel_docket.json").read_text(
                encoding="utf-8"
            )
        )
        manifest = try_load_document_pair_state(ws)
        assert manifest is not None
        # The EXACT source pack id the evaluators/chair operated on.
        assert docket["source_pack_id"] == report.source_pack_id
        assert manifest.source_pack_id == report.source_pack_id

        # §1 at composition level: BLUEPRINT_STATE advanced with the pair.
        state = load_blueprint_state(ws)
        live_bp = ws.joinpath(*CURRENT_BP_RELPATH.split("/")).read_bytes()
        assert state.current_blueprint_hash == sha(live_bp)
        assert state.current_blueprint_hash == manifest.blueprint_hash
        assert state.current_blueprint_iteration == manifest.iteration == 1
        assert state.last_pair_revision_id == manifest.pair_revision_id
        assert state.original_blueprint_hash == sha(
            ws.joinpath(*MASTER_BP_RELPATH.split("/")).read_bytes()
        )
        ensure_current_blueprint(ws)  # NO current_drift after a valid commit

    def test_15_chair_consensus_blueprint_mutation_maps_to_consensus_failed(
        self, tmp_path: Path
    ) -> None:
        ws = seed_dual_workspace(tmp_path, with_master=True)
        machine = ProposalStateMachine()
        drivers, astra = _chair_drivers(ws)
        # The RED TEAM evaluator mutates the living Blueprint during the
        # CONSENSUS round only (its first-pass answer is clean).
        red = drivers[ProposalRole.RED_TEAM_REVIEWER]
        original_wait = red.wait_for_completion

        def mutating_wait(handle, timeout_s=None):
            result = original_wait(handle, timeout_s)
            if "PANEL CONSENSUS" in handle.prompt:
                bp = ws.joinpath(*CURRENT_BP_RELPATH.split("/"))
                bp.write_bytes(bp.read_bytes() + b"consensus mutation\n")
            return result

        red.wait_for_completion = mutating_wait  # type: ignore[method-assign]

        report = run_panel_chair_iteration(
            workspace=ws,
            state_machine=machine,
            iteration_number=1,
            proposal_revision=REVISION,
            reviewer_drivers=drivers,  # type: ignore[arg-type]
            orchestrator_driver=astra,  # type: ignore[arg-type]
        )
        assert report.outcome is PanelChairOutcome.CONSENSUS_FAILED
        # No matrix/readiness accepted; ASTRA was never contacted.
        assert len(astra.prompts) == 0
        assert not (ws / "04_REVIEWS/iteration_001/panel_consensus.json").exists()
        assert not (ws / "04_REVIEWS/iteration_001/readiness.json").exists()
        assert not (ws / "05_CONTROL/DOCUMENT_PAIR_STATE.json").exists()
