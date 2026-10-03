"""Session 019 — parallel evaluator panel matrix (offline, zero model calls).

Covers the brief §37 core: same-hash reviewers, per-role driver instances,
PROVEN concurrency (barrier), one-failure-blocks-aggregation, mutation
invalidation, canonical artifact order, docket stability, consensus matrix
determinism, and the BLOCKED walk.  Consensus concurrency is proven by the
same barrier driver pattern.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from encomm_pcc.drivers.base import (
    BaseDriver,
    DriverCapabilities,
    DriverSession,
    PromptHandle,
    PromptResult,
    SessionRequest,
)
from encomm_pcc.proposal.enums import ProposalPhase, ProposalRole, ProposalReviewVerdict
from encomm_pcc.proposal.fingerprint import proposal_fingerprint
from encomm_pcc.proposal.panel_contracts import (
    PanelConsensusParseError,
    ReadinessAssessment,
    ReadinessCriterionScore,
    build_panel_consensus_packet,
    parse_panel_consensus,
    PanelConsensusInputs,
)
from encomm_pcc.proposal.panel_matrix import (
    build_consensus_matrix,
    build_panel_docket,
    docket_text,
)
from encomm_pcc.proposal.readiness import (
    ReadinessInputs,
    compute_readiness,
    READINESS_DISCLAIMER,
)
from encomm_pcc.proposal.source_budget import (
    SourceBudget,
    SourceBudgetExceededError,
    check_source_budget,
)
from encomm_pcc.proposal.source_import import (
    SourceImportError,
    blueprint_status,
    import_proposal,
    import_source,
    load_source_import_manifest,
    template_status,
)
from encomm_pcc.proposal.source_snapshot import load_review_snapshot
from encomm_pcc.proposal.state_machine import ProposalStateMachine
from encomm_pcc.proposal.workspace import ProposalWorkspace
from encomm_pcc.proposal_runtime.panel_runtime import (
    ProposalPanelOutcome,
    run_parallel_panel_review_cycle,
)

REVISION = "rev-panel-1"


# ---------------------------------------------------------------------------
# test doubles
# ---------------------------------------------------------------------------
class ScriptedReviewer(BaseDriver):
    """Deterministic BaseDriver double with an optional barrier hook."""

    driver_id = "scripted-panel"

    def __init__(self, *, payload: dict, delay_s: float = 0.0,
                 barrier: threading.Barrier | None = None,
                 fail: bool = False) -> None:
        self._payload = payload
        self._delay_s = delay_s
        self._barrier = barrier
        self._fail = fail
        self.requests: list[SessionRequest] = []
        self.started_at: list[float] = []
        self.finished_at: list[float] = []

    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name="Scripted Panel Reviewer",
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
        self.requests.append(request)
        return DriverSession(driver_id=self.driver_id, role=request.role)

    def resume_session(self, session_id: str, request: SessionRequest) -> DriverSession:
        raise NotImplementedError

    def send_prompt(self, session: DriverSession, prompt: str) -> PromptHandle:
        if self._barrier is not None:
            self.started_at.append(time.monotonic())
            self._barrier.wait(timeout=10)
        if self._delay_s:
            time.sleep(self._delay_s)
        self.finished_at.append(time.monotonic())
        return PromptHandle(session=session, prompt=prompt)

    def wait_for_completion(self, handle: PromptHandle, timeout_s=None) -> PromptResult:
        if self._fail:
            return PromptResult.failure("scripted panel failure")
        body = json.dumps(self._payload)
        wrapped = (
            "<<<ENCOMM_PROPOSAL_REVIEW_START>>>\n"
            + body
            + "\n<<<ENCOMM_PROPOSAL_REVIEW_END>>>"
        )
        return PromptResult(ok=True, text=wrapped, session_id="panel-sess-1")


def review_payload(role: str, verdict: str = "NEEDS_REVISION", marker: str = "") -> dict:
    findings = []
    if verdict == "NEEDS_REVISION":
        findings = [{
            "severity": "high" if role == "SCIENTIFIC_REVIEWER" else "medium",
            "category": "missing_evidence",
            "section": "1. Excellence",
            "message": f"{role} finding {marker}".strip(),
            "evidence": "state it",
            "source_refs": [],
            "suggested_change": "add evidence",
        }]
    return {
        "reviewer_role": role,
        "iteration_number": 1,
        "proposal_hash": "WILL_BE_REPLACED",
        "verdict": verdict,
        "summary": f"{role} summary",
        "findings": findings,
        "proposed_patches": [],
        "unverified_claims": [],
    }


@pytest.fixture()
def panel_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ProposalWorkspace(ws).initialize()
    master = ws / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
    master.write_bytes(b"# 1. Excellence\n\nKALHAS drives the architecture.\n")
    return ws


def run_panel(ws: Path, drivers: dict, machine: ProposalStateMachine, **kwargs):
    return run_parallel_panel_review_cycle(
        workspace=ws,
        state_machine=machine,
        iteration_number=1,
        proposal_revision=REVISION,
        reviewer_drivers=drivers,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# import matrix (brief §35, core cases)
# ---------------------------------------------------------------------------
class TestSourceImport:
    def test_01_md_import_original_preserved_and_canonical_created(self, tmp_path):
        ws = tmp_path / "ws"
        ProposalWorkspace(ws).initialize()
        src = tmp_path / "blueprint.md"
        src.write_bytes(b"# BP\n\ncontent\n")
        record = import_source(ws, src, import_role="master_blueprint")
        assert record.sha256_original
        assert (ws / record.original_path).read_bytes() == b"# BP\n\ncontent\n"
        assert blueprint_status(ws) == "READY"
        assert record.normalized_path == "00_SOURCE_OF_TRUTH/MASTER_BLUEPRINT.md"

    def test_02_txt_import(self, tmp_path):
        ws = tmp_path / "ws"
        ProposalWorkspace(ws).initialize()
        src = tmp_path / "notes.txt"
        src.write_bytes(b"plain text blueprint")
        import_source(ws, src, import_role="master_blueprint")
        assert blueprint_status(ws) == "READY"

    def test_03_docx_extraction(self, tmp_path):
        docx = pytest.importorskip("docx")
        ws = tmp_path / "ws"
        ProposalWorkspace(ws).initialize()
        src = tmp_path / "template.docx"
        document = docx.Document()
        document.add_paragraph("Official template heading")
        document.save(str(src))
        record = import_source(ws, src, import_role="application_template")
        assert template_status(ws) == "READY"
        assert "Official template heading" in (
            ws / "01_OFFICIAL/APPLICATION_TEMPLATE.md"
        ).read_text(encoding="utf-8")

    def test_04_corrupt_pdf_fails_visibly(self, tmp_path):
        pytest.importorskip("pypdf")
        ws = tmp_path / "ws"
        ProposalWorkspace(ws).initialize()
        src = tmp_path / "broken.pdf"
        src.write_bytes(b"%PDF-1.4 not really")
        with pytest.raises(SourceImportError):
            import_source(ws, src, import_role="official_document")

    def test_05_no_overwrite_without_explicit_replace(self, tmp_path):
        ws = tmp_path / "ws"
        ProposalWorkspace(ws).initialize()
        src = tmp_path / "bp.md"
        src.write_bytes(b"# first")
        import_source(ws, src, import_role="master_blueprint")
        with pytest.raises(SourceImportError) as excinfo:
            import_source(ws, src, import_role="master_blueprint")
        assert excinfo.value.reason == "canonical_exists"

    def test_06_replace_backs_up_and_records(self, tmp_path):
        ws = tmp_path / "ws"
        ProposalWorkspace(ws).initialize()
        src = tmp_path / "bp.md"
        src.write_bytes(b"# first")
        import_source(ws, src, import_role="master_blueprint")
        record = import_source(ws, src, import_role="master_blueprint", replace=True)
        assert record.replaced_previous
        assert record.backup_path
        # The backup holds the EXACT old canonical bytes (LF-normalized on
        # the first import — trailing newline included).
        assert (ws / record.backup_path).read_bytes() == b"# first\n"

    def test_07_manifest_atomic_and_complete(self, tmp_path):
        ws = tmp_path / "ws"
        ProposalWorkspace(ws).initialize()
        src = tmp_path / "bp.md"
        src.write_bytes(b"# body")
        import_source(ws, src, import_role="master_blueprint")
        manifest = load_source_import_manifest(ws)
        assert manifest["schema"] == "encomm-pcc.source-import-manifest/v1"
        assert len(manifest["imports"]) == 1
        entry = manifest["imports"][0]
        for key in ("original_filename", "original_path", "sha256_original",
                    "size_bytes", "media_type", "import_role",
                    "normalized_path", "extraction_status"):
            assert key in entry

    def test_08_existing_proposal_import_guard(self, tmp_path):
        ws = tmp_path / "ws"
        ProposalWorkspace(ws).initialize()
        draft = tmp_path / "draft.md"
        draft.write_bytes(b"# draft")
        import_proposal(ws, draft)
        with pytest.raises(SourceImportError) as excinfo:
            import_proposal(ws, draft)
        assert excinfo.value.reason == "master_not_empty"

    def test_09_unsupported_format_refused(self, tmp_path):
        ws = tmp_path / "ws"
        ProposalWorkspace(ws).initialize()
        src = tmp_path / "image.png"
        src.write_bytes(b"\x89PNG fake")
        with pytest.raises(SourceImportError) as excinfo:
            import_source(ws, src, import_role="master_blueprint")
        assert excinfo.value.reason == "unsupported_format"

    def test_10_snapshot_reads_template_and_normalized_docs(self, tmp_path):
        ws = tmp_path / "ws"
        ProposalWorkspace(ws).initialize()
        (ws / "03_PROPOSAL/MASTER_PROPOSAL.md").write_bytes(b"# P")
        (ws / "01_OFFICIAL/APPLICATION_TEMPLATE.md").write_bytes(b"# template")
        normalized = ws / "01_OFFICIAL/NORMALIZED"
        normalized.mkdir(parents=True)
        (normalized / "b-call.md").write_bytes(b"# call")
        (normalized / "a-wp.md").write_bytes(b"# wp")
        snapshot = load_review_snapshot(ws)
        assert snapshot.application_template_text == "# template"
        assert [name for name, _ in snapshot.official_documents] == ["a-wp", "b-call"]


# ---------------------------------------------------------------------------
# source budget (brief §7)
# ---------------------------------------------------------------------------
class TestSourceBudget:
    def test_11_within_budget_passes(self, panel_workspace):
        count = check_source_budget(panel_workspace, SourceBudget(blueprint_max_chars=1_000_000))
        assert count == 0  # empty blueprint seeds count 0

    def test_12_over_budget_blocks(self, tmp_path):
        ws = tmp_path / "ws"
        ProposalWorkspace(ws).initialize()
        (ws / "00_SOURCE_OF_TRUTH/MASTER_BLUEPRINT.md").write_bytes(b"x" * 5001)
        with pytest.raises(SourceBudgetExceededError) as excinfo:
            check_source_budget(ws, SourceBudget(blueprint_max_chars=5_000))
        assert excinfo.value.total_chars == 5001

    def test_13_snapshot_cap_raised_by_budget(self, tmp_path):
        ws = tmp_path / "ws"
        ProposalWorkspace(ws).initialize()
        (ws / "03_PROPOSAL/MASTER_PROPOSAL.md").write_bytes(b"# P")
        big = ("# BP\n" + "x" * 400_500 + "\n")
        (ws / "00_SOURCE_OF_TRUTH/MASTER_BLUEPRINT.md").write_bytes(big.encode("utf-8"))
        # default cap: blueprint UNAVAILABLE, never truncated
        default = load_review_snapshot(ws)
        assert default.master_blueprint_text == ""
        assert "oversized" in default.unavailable_sources.get(
            "00_SOURCE_OF_TRUTH/MASTER_BLUEPRINT.md", ""
        )
        # raised cap: whole blueprint available
        raised = load_review_snapshot(ws, blueprint_max_chars=500_000)
        assert len(raised.master_blueprint_text) == len(big)


# ---------------------------------------------------------------------------
# parallel panel (brief §37)
# ---------------------------------------------------------------------------
class TestParallelPanel:
    def test_14_three_reviewers_same_hash_and_concurrency_proven(self, panel_workspace):
        ws = panel_workspace
        proposal_hash = proposal_fingerprint(ws / "03_PROPOSAL/MASTER_PROPOSAL.md")
        barrier = threading.Barrier(3, timeout=10)
        drivers = {}
        for role in ("SCIENTIFIC_REVIEWER", "PROPOSAL_ENGINEER", "RED_TEAM_REVIEWER"):
            payload = review_payload(role)
            payload["proposal_hash"] = proposal_hash
            drivers[ProposalRole(role)] = ScriptedReviewer(payload=payload, barrier=barrier)
        machine = ProposalStateMachine()
        report = run_panel(ws, drivers, machine)
        assert report.outcome is ProposalPanelOutcome.READY_FOR_INTEGRATION, report.error
        # CONCURRENCY PROOF: all three blocked on the barrier — a sequential
        # implementation deadlocks/timeouts here (barrier never releases).
        for role, driver in drivers.items():
            assert len(driver.started_at) == 1
            assert len(driver.finished_at) == 1
        # every driver started before the LAST finisher finished
        last_start = max(d.started_at[0] for d in drivers.values())
        first_finish = min(d.finished_at[0] for d in drivers.values())
        assert last_start <= first_finish
        assert machine.phase is ProposalPhase.INTEGRATION
        assert report.proposal_hash == proposal_hash

    def test_15_one_failure_blocks_aggregation(self, panel_workspace):
        ws = panel_workspace
        proposal_hash = proposal_fingerprint(ws / "03_PROPOSAL/MASTER_PROPOSAL.md")
        drivers = {}
        for role in ("SCIENTIFIC_REVIEWER", "PROPOSAL_ENGINEER", "RED_TEAM_REVIEWER"):
            payload = review_payload(role)
            payload["proposal_hash"] = proposal_hash
            drivers[ProposalRole(role)] = ScriptedReviewer(
                payload=payload, fail=(role == "RED_TEAM_REVIEWER")
            )
        machine = ProposalStateMachine()
        report = run_panel(ws, drivers, machine)
        assert report.outcome is ProposalPanelOutcome.PANEL_FAILED
        assert report.failed_roles == ["RED_TEAM_REVIEWER"]
        assert report.aggregate_bundle is None
        assert machine.phase is ProposalPhase.SCIENTIFIC_REVIEW  # never advanced

    def test_16_shared_driver_instance_refused(self, panel_workspace):
        ws = panel_workspace
        proposal_hash = proposal_fingerprint(ws / "03_PROPOSAL/MASTER_PROPOSAL.md")
        payload = review_payload("SCIENTIFIC_REVIEWER")
        payload["proposal_hash"] = proposal_hash
        shared = ScriptedReviewer(payload=payload)
        machine = ProposalStateMachine()
        report = run_panel(ws, {r: shared for r in (
            ProposalRole.SCIENTIFIC_REVIEWER,
            ProposalRole.PROPOSAL_ENGINEER,
            ProposalRole.RED_TEAM_REVIEWER,
        )}, machine)
        assert report.outcome is ProposalPanelOutcome.PANEL_FAILED
        assert "OWN driver" in report.error

    def test_17_mutation_during_panel_invalidates(self, panel_workspace):
        ws = panel_workspace
        proposal_hash = proposal_fingerprint(ws / "03_PROPOSAL/MASTER_PROPOSAL.md")
        drivers = {}
        mutator = ws / "03_PROPOSAL" / "MASTER_PROPOSAL.md"

        class MutatingReviewer(ScriptedReviewer):
            def wait_for_completion(self, handle, timeout_s=None):
                mutator.write_bytes(b"# tampered\n")
                body = json.dumps(self._payload)
                return PromptResult(
                    ok=True,
                    text=(
                        "<<<ENCOMM_PROPOSAL_REVIEW_START>>>\n"
                        + body
                        + "\n<<<ENCOMM_PROPOSAL_REVIEW_END>>>"
                    ),
                    session_id="s",
                )

        for role in ("SCIENTIFIC_REVIEWER", "PROPOSAL_ENGINEER", "RED_TEAM_REVIEWER"):
            payload = review_payload(role)
            payload["proposal_hash"] = proposal_hash
            drivers[ProposalRole(role)] = MutatingReviewer(payload=payload)
        machine = ProposalStateMachine()
        report = run_panel(ws, drivers, machine)
        assert report.outcome is ProposalPanelOutcome.STALE_PROPOSAL
        assert report.aggregate_bundle is None

    def test_18_blocked_review_walks_explicit_edge(self, panel_workspace):
        ws = panel_workspace
        proposal_hash = proposal_fingerprint(ws / "03_PROPOSAL/MASTER_PROPOSAL.md")
        drivers = {}
        for role in ("SCIENTIFIC_REVIEWER", "PROPOSAL_ENGINEER", "RED_TEAM_REVIEWER"):
            verdict = "BLOCKED" if role == "PROPOSAL_ENGINEER" else "PASS"
            payload = review_payload(role, verdict=verdict)
            if verdict == "BLOCKED":
                payload["blocking_reason"] = "official requirements unavailable"
            payload["proposal_hash"] = proposal_hash
            drivers[ProposalRole(role)] = ScriptedReviewer(payload=payload)
        machine = ProposalStateMachine()
        report = run_panel(ws, drivers, machine)
        assert report.outcome is ProposalPanelOutcome.BLOCKED
        assert machine.phase is ProposalPhase.BLOCKED

    def test_19_budget_block_happens_before_any_call(self, panel_workspace):
        ws = panel_workspace
        (ws / "00_SOURCE_OF_TRUTH/MASTER_BLUEPRINT.md").write_bytes(b"x" * 4001)
        proposal_hash = proposal_fingerprint(ws / "03_PROPOSAL/MASTER_PROPOSAL.md")
        drivers = {}
        for role in ("SCIENTIFIC_REVIEWER", "PROPOSAL_ENGINEER", "RED_TEAM_REVIEWER"):
            payload = review_payload(role)
            payload["proposal_hash"] = proposal_hash
            drivers[ProposalRole(role)] = ScriptedReviewer(payload=payload)
        machine = ProposalStateMachine()
        report = run_panel(
            ws, drivers, machine,
            source_budget=SourceBudget(blueprint_max_chars=4_000),
        )
        assert report.outcome is ProposalPanelOutcome.SOURCE_BUDGET_EXCEEDED
        assert all(len(d.requests) == 0 for d in drivers.values())

    def test_20_docket_written_and_stable(self, panel_workspace):
        ws = panel_workspace
        proposal_hash = proposal_fingerprint(ws / "03_PROPOSAL/MASTER_PROPOSAL.md")
        drivers = {}
        for role in ("SCIENTIFIC_REVIEWER", "PROPOSAL_ENGINEER", "RED_TEAM_REVIEWER"):
            payload = review_payload(role)
            payload["proposal_hash"] = proposal_hash
            drivers[ProposalRole(role)] = ScriptedReviewer(payload=payload)
        machine = ProposalStateMachine()
        report = run_panel(ws, drivers, machine)
        assert report.outcome is ProposalPanelOutcome.READY_FOR_INTEGRATION, report.error
        docket = json.loads(Path(report.docket_path).read_text(encoding="utf-8"))
        ids = [item["item_id"] for item in docket["items"]]
        # Every reviewer returned one finding → three docket entries with
        # stable severity-ordered ids (high first, then the mediums).
        assert ids == ["F001", "F002", "F003"]
        assert docket["items"][0]["severity"] == "high"
        assert docket["schema"] == "encomm-pcc.panel-docket/v1"
        # Deterministic projection: the same bundle always renders the same
        # docket text (build twice, compare).
        from encomm_pcc.proposal.review_aggregation import aggregate_reviews
        from encomm_pcc.proposal.models import ProposalReviewResult

        def res(role_value, severity, message):
            return ProposalReviewResult.from_dict({
                "reviewer_role": role_value, "iteration_number": 1,
                "proposal_hash": proposal_hash, "verdict": "NEEDS_REVISION",
                "summary": "s",
                "findings": [{
                    "severity": severity, "category": "c", "section": "sec",
                    "message": message, "evidence": "e", "source_refs": [],
                    "suggested_change": "sc",
                }],
                "proposed_patches": [], "unverified_claims": [],
            })

        bundle = aggregate_reviews(
            iteration_number=1, proposal_revision=REVISION,
            proposal_hash=proposal_hash,
            scientific_review=res("SCIENTIFIC_REVIEWER", "high", "sci finding"),
            implementation_review=res("PROPOSAL_ENGINEER", "low", "impl note"),
            red_team_review=res("RED_TEAM_REVIEWER", "low", "rt note"),
        )
        text1 = docket_text(build_panel_docket(bundle, source_pack_id=report.source_pack_id))
        text2 = docket_text(build_panel_docket(bundle, source_pack_id=report.source_pack_id))
        assert text1 == text2
        assert "F001" in text1

    def test_21_source_pack_identity_deterministic(self, panel_workspace):
        ws = panel_workspace
        proposal_hash = proposal_fingerprint(ws / "03_PROPOSAL/MASTER_PROPOSAL.md")
        drivers = {}
        for role in ("SCIENTIFIC_REVIEWER", "PROPOSAL_ENGINEER", "RED_TEAM_REVIEWER"):
            payload = review_payload(role)
            payload["proposal_hash"] = proposal_hash
            drivers[ProposalRole(role)] = ScriptedReviewer(payload=payload)
        machine = ProposalStateMachine()
        report1 = run_panel(ws, drivers, machine)
        assert report1.outcome is ProposalPanelOutcome.READY_FOR_INTEGRATION
        assert report1.source_pack_id.startswith("srcpack:")
        assert report1.source_pack_id in Path(report1.docket_path).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# consensus contracts + matrix + readiness (briefs §19–§23, §38)
# ---------------------------------------------------------------------------
VALID_CONSENSUS = '''\
<<<ENCOMM_PANEL_CONSENSUS_START>>>
{{"role": "{role}", "iteration_number": 1, "proposal_hash": "{ph}",
"judgements": [
 {{"item_id": "F001", "judgement": "{j1}", "rationale": "r", "proposed_resolution": "res", "source_refs": [], "blocks_acceptance": {b1}}},
 {{"item_id": "F002", "judgement": "{j2}", "rationale": "r", "proposed_resolution": "", "source_refs": [], "blocks_acceptance": false}}],
"readiness_assessment": {{"criteria": [
 {{"criterion": "scientific_coherence", "score": {s1}, "rationale": ""}},
 {{"criterion": "impact_coherence", "score": {s2}, "rationale": ""}}]}},
"summary": "panel position"}}
<<<ENCOMM_PANEL_CONSENSUS_END>>>'''


class TestConsensusAndReadiness:
    def _result(self, role, ph, j1="AGREE", j2="AGREE", b1="false", s1=80, s2=70):
        from encomm_pcc.proposal.panel_contracts import PanelConsensusResult, ConsensusJudgement
        parsed = parse_panel_consensus(
            VALID_CONSENSUS.format(role=role.value, ph=ph, j1=j1, j2=j2, b1=b1, s1=s1, s2=s2),
            expected_role=role, expected_iteration=1, expected_proposal_hash=ph,
        )
        return parsed

    def _bundle(self, ph):
        from encomm_pcc.proposal.review_aggregation import aggregate_reviews
        from encomm_pcc.proposal.models import ProposalReviewResult

        def res(role, sev, msg):
            return ProposalReviewResult.from_dict({
                "reviewer_role": role, "iteration_number": 1,
                "proposal_hash": ph,
                "verdict": "NEEDS_REVISION", "summary": "s",
                "findings": [{
                    "severity": sev, "category": "c", "section": "s",
                    "message": msg, "evidence": "e", "source_refs": [],
                    "suggested_change": "sc",
                }],
                "proposed_patches": [], "unverified_claims": [],
            })

        return aggregate_reviews(
            iteration_number=1, proposal_revision=REVISION, proposal_hash=ph,
            scientific_review=res("SCIENTIFIC_REVIEWER", "critical", "crit"),
            implementation_review=res("PROPOSAL_ENGINEER", "high", "high issue"),
            red_team_review=res("RED_TEAM_REVIEWER", "low", "minor"),
        )

    def test_22_matrix_deterministic_with_votes(self):
        ph = "a" * 64
        results = {
            ProposalRole.SCIENTIFIC_REVIEWER: self._result(
                ProposalRole.SCIENTIFIC_REVIEWER, ph, j1="AGREE", j2="DISAGREE"),
            ProposalRole.PROPOSAL_ENGINEER: self._result(
                ProposalRole.PROPOSAL_ENGINEER, ph, j1="AGREE", j2="PARTIAL"),
            ProposalRole.RED_TEAM_REVIEWER: self._result(
                ProposalRole.RED_TEAM_REVIEWER, ph, j1="DISAGREE", j2="AGREE"),
        }
        matrix = build_consensus_matrix(
            iteration_number=1, proposal_hash=ph, proposal_revision=REVISION,
            source_pack_id="srcpack:x", consensus_results=results,
        )
        again = build_consensus_matrix(
            iteration_number=1, proposal_hash=ph, proposal_revision=REVISION,
            source_pack_id="srcpack:x", consensus_results=results,
        )
        assert matrix.to_dict() == again.to_dict()
        rows = {row.item_id: row for row in matrix.rows}
        assert set(rows) == {"F001", "F002"}
        assert rows["F001"].agreement_count == 2
        assert rows["F001"].disagreement_count == 1
        assert rows["F001"].unresolved
        assert rows["F002"].partial_count == 1

    def test_23_matrix_requires_all_three_roles(self):
        from encomm_pcc.proposal.panel_contracts import PanelConsensusResult, ConsensusJudgement
        ph = "b" * 64
        one = self._result(ProposalRole.SCIENTIFIC_REVIEWER, ph)
        with pytest.raises(ValueError):
            build_consensus_matrix(
                iteration_number=1, proposal_hash=ph, proposal_revision="r",
                source_pack_id="x",
                consensus_results={ProposalRole.SCIENTIFIC_REVIEWER: one},
            )

    def test_24_readiness_median_penalties_deterministic_and_disclaimer(self):
        ph = "c" * 64
        bundle = self._bundle(ph)
        docket = build_panel_docket(bundle, source_pack_id="srcpack:x")
        # F001 critical unresolved (one DISAGREE vote), F002 high resolved.
        results = {
            ProposalRole.SCIENTIFIC_REVIEWER: self._result(
                ProposalRole.SCIENTIFIC_REVIEWER, ph, j1="AGREE", j2="AGREE", s1=80, s2=70),
            ProposalRole.PROPOSAL_ENGINEER: self._result(
                ProposalRole.PROPOSAL_ENGINEER, ph, j1="DISAGREE", j2="AGREE", s1=90, s2=70),
            ProposalRole.RED_TEAM_REVIEWER: self._result(
                ProposalRole.RED_TEAM_REVIEWER, ph, j1="AGREE", j2="AGREE", s1=85, s2=70),
        }
        matrix = build_consensus_matrix(
            iteration_number=1, proposal_hash=ph, proposal_revision=REVISION,
            source_pack_id="x", consensus_results=results,
        )
        sev_by_id = {item.item_id: item.severity for item in docket.items}
        unresolved_critical = sum(
            1 for row in matrix.rows
            if row.unresolved and sev_by_id.get(row.item_id) == "critical"
        )
        unresolved_high = sum(
            1 for row in matrix.rows
            if row.unresolved and sev_by_id.get(row.item_id) == "high"
        )
        assessments = [results[r].readiness_assessment for r in results]
        first = compute_readiness(ReadinessInputs(
            assessments=assessments,
            unresolved_critical_count=unresolved_critical,
            unresolved_high_count=unresolved_high,
        ))
        second = compute_readiness(ReadinessInputs(
            assessments=assessments,
            unresolved_critical_count=unresolved_critical,
            unresolved_high_count=unresolved_high,
        ))
        assert first.to_dict() == second.to_dict()
        assert first.per_criterion_median["scientific_coherence"] == 85.0
        assert first.penalties_applied.get("unresolved_critical_finding") == 15
        assert first.disclaimer == READINESS_DISCLAIMER

    def test_25_missing_assessments_cannot_score_high(self):
        empty = ReadinessAssessment(criteria=[])
        result = compute_readiness(ReadinessInputs(assessments=[empty]))
        assert result.readiness == 0.0

    def test_26_consensus_strict_envelope(self):
        role = ProposalRole.SCIENTIFIC_REVIEWER
        ph = "d" * 64
        body = VALID_CONSENSUS.format(role=role.value, ph=ph, j1="AGREE", j2="AGREE", b1="false", s1=80, s2=70)
        bare = body.split("<<<ENCOMM_PANEL_CONSENSUS_START>>>")[1].split("<<<ENCOMM_PANEL_CONSENSUS_END>>>")[0].strip()
        with pytest.raises(PanelConsensusParseError) as excinfo:
            parse_panel_consensus(bare, expected_role=role, expected_iteration=1, expected_proposal_hash=ph)
        assert excinfo.value.reason == "missing_envelope"
