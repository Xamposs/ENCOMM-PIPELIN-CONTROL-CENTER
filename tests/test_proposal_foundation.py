"""Session 012 — Proposal Mode foundation tests.

Covers the mandated foundation matrix:

1.  ProposalRole has exactly the intended foundation roles.
2.  Proposal state transitions are deterministic.
3.  Invalid transitions are rejected.
4.  COMPLETE cannot silently transition back into an active state.
5.  REVISION_REQUIRED can begin another review iteration.
6.  Proposal models round-trip cleanly through primitive/JSON representations.
7.  Workspace initialization creates required directories.
8.  Workspace initialization is idempotent.
9.  Existing MASTER_PROPOSAL.md is NEVER overwritten.
10. Existing SOURCE_OF_TRUTH files are NEVER overwritten.
11. Windows-style workspace paths are handled safely.
12. Hard-gate identifiers are unique and stable.
13. The proposal package imports without loading PySide6.
14. The proposal foundation does not mutate or depend on the coding
    PipelinePhase/AgentRole semantics.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import encomm_pcc.proposal as pp

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"


# ---------------------------------------------------------------------------
# 1. Roles
# ---------------------------------------------------------------------------


class TestProposalRoles:
    def test_role_members_are_exactly_the_foundation_roles(self) -> None:
        assert {r.value for r in pp.ProposalRole} == {
            "ORCHESTRATOR",
            "SCIENTIFIC_REVIEWER",
            "PROPOSAL_ENGINEER",
            "RED_TEAM_REVIEWER",
        }
        assert len(pp.ProposalRole) == 4

    def test_no_second_integrator_role_exists(self) -> None:
        # The ORCHESTRATOR is the future integration editor/final authority;
        # no independent integrator role may exist.
        assert not any("INTEGRAT" in r.value for r in pp.ProposalRole)

    def test_roles_are_str_enums_for_json_sqlite_round_trip(self) -> None:
        for role in pp.ProposalRole:
            assert isinstance(role, str)
            assert role.value == str(role)


# ---------------------------------------------------------------------------
# 2-5. State machine
# ---------------------------------------------------------------------------


def _walk_happy_path(machine: pp.ProposalStateMachine) -> list[str]:
    """Walk IDLE → … → HARD_GATE_VALIDATION, recording the phase trail."""
    trail = [machine.phase.value]
    for phase in (
        pp.ProposalPhase.SOURCE_VALIDATION,
        pp.ProposalPhase.SCIENTIFIC_REVIEW,
        pp.ProposalPhase.IMPLEMENTATION_REVIEW,
        pp.ProposalPhase.RED_TEAM_REVIEW,
        pp.ProposalPhase.INTEGRATION,
        pp.ProposalPhase.HARD_GATE_VALIDATION,
    ):
        machine.transition_to(phase)
        trail.append(machine.phase.value)
    return trail


class TestProposalStateMachine:
    def test_happy_path_is_deterministic(self) -> None:
        t1 = _walk_happy_path(pp.ProposalStateMachine())
        t2 = _walk_happy_path(pp.ProposalStateMachine())
        assert t1 == t2 == [
            "IDLE",
            "SOURCE_VALIDATION",
            "SCIENTIFIC_REVIEW",
            "IMPLEMENTATION_REVIEW",
            "RED_TEAM_REVIEW",
            "INTEGRATION",
            "HARD_GATE_VALIDATION",
        ]

    def test_every_phase_appears_in_the_graph(self) -> None:
        assert set(pp.PROPOSAL_TRANSITIONS) == set(pp.ProposalPhase)

    def test_graph_validator_reports_no_defects(self) -> None:
        assert pp.validate_proposal_graph() == []

    def test_invalid_transitions_are_rejected_and_leave_phase_unchanged(self) -> None:
        machine = pp.ProposalStateMachine()
        # IDLE may ONLY begin SOURCE_VALIDATION.
        for illegal in (
            pp.ProposalPhase.SCIENTIFIC_REVIEW,
            pp.ProposalPhase.INTEGRATION,
            pp.ProposalPhase.COMPLETE,
        ):
            try:
                machine.transition_to(illegal)
            except pp.InvalidProposalTransitionError as exc:
                assert exc.source is pp.ProposalPhase.IDLE
                assert exc.target is illegal
            else:  # pragma: no cover - guard
                raise AssertionError(f"illegal edge accepted: IDLE -> {illegal}")
            assert machine.phase is pp.ProposalPhase.IDLE

    def test_skip_edges_are_rejected(self) -> None:
        machine = pp.ProposalStateMachine()
        machine.transition_to(pp.ProposalPhase.SOURCE_VALIDATION)
        # Source validation may not skip into a later review or COMPLETE.
        for illegal in (
            pp.ProposalPhase.RED_TEAM_REVIEW,
            pp.ProposalPhase.HARD_GATE_VALIDATION,
            pp.ProposalPhase.COMPLETE,
            pp.ProposalPhase.REVISION_REQUIRED,
        ):
            try:
                machine.transition_to(illegal)
            except pp.InvalidProposalTransitionError:
                pass
            else:  # pragma: no cover - guard
                raise AssertionError(f"skip edge accepted: {illegal}")
            assert machine.phase is pp.ProposalPhase.SOURCE_VALIDATION

    def test_complete_is_terminal_and_never_reopens(self) -> None:
        machine = pp.ProposalStateMachine()
        _walk_happy_path(machine)
        machine.transition_to(pp.ProposalPhase.COMPLETE)
        assert machine.is_terminal(machine.phase)
        # No edge at all out of COMPLETE — not even into a control state.
        assert pp.ProposalStateMachine.allowed_from(pp.ProposalPhase.COMPLETE) == frozenset()
        for target in pp.ProposalPhase:
            if target is pp.ProposalPhase.COMPLETE:
                continue
            assert not machine.can_go_to(target), target

    def test_failed_is_terminal_with_reset_escape_hatch(self) -> None:
        machine = pp.ProposalStateMachine()
        machine.transition_to(pp.ProposalPhase.SOURCE_VALIDATION)
        machine.transition_to(pp.ProposalPhase.FAILED)
        assert machine.is_terminal(machine.phase)
        assert not machine.can_go_to(pp.ProposalPhase.SCIENTIFIC_REVIEW)
        # reset() is the ONLY way back to IDLE (explicit operator escape).
        machine.reset()
        assert machine.phase is pp.ProposalPhase.IDLE

    def test_hard_gate_validation_outcomes(self) -> None:
        for outcome, legal in (
            (pp.ProposalPhase.COMPLETE, True),
            (pp.ProposalPhase.REVISION_REQUIRED, True),
            (pp.ProposalPhase.BLOCKED, True),
            (pp.ProposalPhase.FAILED, True),
            (pp.ProposalPhase.INTEGRATION, False),
            (pp.ProposalPhase.SCIENTIFIC_REVIEW, False),
        ):
            machine = pp.ProposalStateMachine()
            _walk_happy_path(machine)
            assert machine.can_go_to(outcome) is legal, outcome

    def test_revision_required_begins_a_fresh_review_iteration(self) -> None:
        machine = pp.ProposalStateMachine()
        _walk_happy_path(machine)
        machine.transition_to(pp.ProposalPhase.REVISION_REQUIRED)
        # A fresh iteration WITHOUT re-creating the application state: the
        # same machine object walks back into the review chain.
        machine.transition_to(pp.ProposalPhase.SCIENTIFIC_REVIEW)
        machine.transition_to(pp.ProposalPhase.IMPLEMENTATION_REVIEW)
        machine.transition_to(pp.ProposalPhase.RED_TEAM_REVIEW)
        machine.transition_to(pp.ProposalPhase.INTEGRATION)
        machine.transition_to(pp.ProposalPhase.HARD_GATE_VALIDATION)
        machine.transition_to(pp.ProposalPhase.COMPLETE)
        assert machine.phase is pp.ProposalPhase.COMPLETE

    def test_revision_required_cannot_jump_to_complete(self) -> None:
        machine = pp.ProposalStateMachine()
        _walk_happy_path(machine)
        machine.transition_to(pp.ProposalPhase.REVISION_REQUIRED)
        try:
            machine.transition_to(pp.ProposalPhase.COMPLETE)
        except pp.InvalidProposalTransitionError:
            pass
        else:  # pragma: no cover - guard
            raise AssertionError("REVISION_REQUIRED -> COMPLETE accepted")
        assert machine.phase is pp.ProposalPhase.REVISION_REQUIRED

    def test_paused_resumes_into_the_recorded_phase(self) -> None:
        machine = pp.ProposalStateMachine()
        machine.transition_to(pp.ProposalPhase.SOURCE_VALIDATION)
        machine.transition_to(pp.ProposalPhase.SCIENTIFIC_REVIEW)
        machine.transition_to(pp.ProposalPhase.PAUSED)
        assert machine.resume_phase is pp.ProposalPhase.SCIENTIFIC_REVIEW
        assert machine.resume_target() is pp.ProposalPhase.SCIENTIFIC_REVIEW
        machine.transition_to(machine.resume_target())
        assert machine.phase is pp.ProposalPhase.SCIENTIFIC_REVIEW

    def test_paused_is_reachable_from_revision_required(self) -> None:
        machine = pp.ProposalStateMachine()
        _walk_happy_path(machine)
        machine.transition_to(pp.ProposalPhase.REVISION_REQUIRED)
        machine.transition_to(pp.ProposalPhase.PAUSED)
        assert machine.resume_target() is pp.ProposalPhase.REVISION_REQUIRED

    def test_blocked_recovers_only_through_explicit_edges(self) -> None:
        machine = pp.ProposalStateMachine()
        machine.transition_to(pp.ProposalPhase.SOURCE_VALIDATION)
        machine.transition_to(pp.ProposalPhase.BLOCKED)
        # Recovery targets: source re-check, review restart, hard-gate re-run,
        # pause, fail — nothing else.
        allowed = pp.ProposalStateMachine.allowed_from(pp.ProposalPhase.BLOCKED)
        assert allowed == frozenset(
            {
                pp.ProposalPhase.SOURCE_VALIDATION,
                pp.ProposalPhase.SCIENTIFIC_REVIEW,
                pp.ProposalPhase.HARD_GATE_VALIDATION,
                pp.ProposalPhase.PAUSED,
                pp.ProposalPhase.FAILED,
            }
        )
        machine.transition_to(pp.ProposalPhase.HARD_GATE_VALIDATION)
        assert machine.phase is pp.ProposalPhase.HARD_GATE_VALIDATION


# ---------------------------------------------------------------------------
# 6. Model round-trips
# ---------------------------------------------------------------------------


class TestModelRoundTrips:
    def test_agent_config_round_trip(self) -> None:
        config = pp.ProposalAgentConfig(
            role=pp.ProposalRole.SCIENTIFIC_REVIEWER,
            engine="some_driver_id",
            project_profile="sci-reviewer",
            provider="provider-x",
            model="model-x",
            session_policy="persistent",
            session_id="20261001_101010_abcdef",
        )
        assert pp.ProposalAgentConfig.from_dict(config.to_dict()) == config

    def test_agent_config_carries_no_hardcoded_engine(self) -> None:
        # Engines/providers are runtime configuration values, never enums or
        # constants in the domain.
        config = pp.ProposalAgentConfig(
            role=pp.ProposalRole.PROPOSAL_ENGINEER, engine="whatever_driver"
        )
        assert config.provider == "" and config.model == ""
        assert pp.ProposalAgentConfig.from_dict(config.to_dict()) == config

    def test_finding_round_trip_with_multi_row_data(self) -> None:
        findings = [
            pp.ProposalFinding(
                severity=pp.ProposalFindingSeverity.CRITICAL,
                category="consistency",
                section="3.2 Budget",
                message="Person-month totals disagree",
                evidence="table vs text",
                source_refs=["02_EVIDENCE/CLAIM_LEDGER.json#12"],
                suggested_change="Realign WP3 person-months",
            ),
            pp.ProposalFinding(
                severity=pp.ProposalFindingSeverity.LOW,
                category="style",
                section="1. Introduction",
                message="Terminology drift",
            ),
        ]
        for finding in findings:
            assert pp.ProposalFinding.from_dict(finding.to_dict()) == finding

    def test_patch_round_trip(self) -> None:
        patch = pp.ProposalPatch(
            target_section="3.2 Budget",
            rationale="Totals must match the WP table",
            replacement_text="| WP3 | 24 pm |",
            source_refs=["00_SOURCE_OF_TRUTH/PROJECT_FACTS.md#pm"],
            confidence=0.8,
        )
        assert pp.ProposalPatch.from_dict(patch.to_dict()) == patch

    def test_review_result_round_trip_with_nested_children(self) -> None:
        result = pp.ProposalReviewResult(
            reviewer_role=pp.ProposalRole.RED_TEAM_REVIEWER,
            verdict=pp.ProposalReviewVerdict.NEEDS_REVISION,
            summary="Two blocking inconsistencies",
            findings=[
                pp.ProposalFinding(
                    severity=pp.ProposalFindingSeverity.HIGH,
                    category="claims",
                    section="2. State of the art",
                    message="Unverified TRL claim",
                )
            ],
            proposed_patches=[
                pp.ProposalPatch(
                    target_section="2. State of the art",
                    rationale="Claim lacks a source",
                    patch_instructions="Cite the registry entry or drop the claim",
                    confidence=0.6,
                )
            ],
            unverified_claims=["TRL 9 achieved by partner C"],
            iteration_number=2,
        )
        assert pp.ProposalReviewResult.from_dict(result.to_dict()) == result

    def test_review_result_never_fabricates_pass_from_bad_data(self) -> None:
        result = pp.ProposalReviewResult(
            reviewer_role=pp.ProposalRole.SCIENTIFIC_REVIEWER,
            verdict=pp.ProposalReviewVerdict.BLOCKED,
        )
        data = result.to_dict()
        data["verdict"] = "not-a-verdict"
        recovered = pp.ProposalReviewResult.from_dict(data)
        assert recovered.verdict is pp.ProposalReviewVerdict.BLOCKED

    def test_hard_gate_result_round_trip(self) -> None:
        gate = pp.ProposalHardGateResult(
            gate_id="PAGE_LIMIT",
            status=pp.ProposalHardGateStatus.WARN,
            message="Within limit but close",
            evidence="41/45 pages",
        )
        assert pp.ProposalHardGateResult.from_dict(gate.to_dict()) == gate

    def test_iteration_record_round_trip(self) -> None:
        record = pp.ProposalIterationRecord(
            iteration_number=1,
            proposal_revision="rev-2026-10-01-a",
            proposal_hash="deadbeef" * 8,
            review_results=[
                pp.ProposalReviewResult(
                    reviewer_role=pp.ProposalRole.SCIENTIFIC_REVIEWER,
                    verdict=pp.ProposalReviewVerdict.PASS,
                    iteration_number=1,
                )
            ],
            hard_gate_results=[
                pp.ProposalHardGateResult(
                    gate_id="MANDATORY_SECTIONS",
                    status=pp.ProposalHardGateStatus.PASS,
                ),
                pp.ProposalHardGateResult(
                    gate_id="BUDGET_CONSISTENCY",
                    status=pp.ProposalHardGateStatus.FAIL,
                    message="Totals mismatch",
                ),
            ],
            status=pp.ProposalPhase.REVISION_REQUIRED,
            started_at="2026-10-01T10:00:00Z",
            completed_at="2026-10-01T11:00:00Z",
        )
        assert pp.ProposalIterationRecord.from_dict(record.to_dict()) == record

    def test_iteration_record_survives_json_text_round_trip(self) -> None:
        record = pp.ProposalIterationRecord(
            iteration_number=3,
            proposal_revision="rev-c",
            proposal_hash="abcd",
            status=pp.ProposalPhase.COMPLETE,
        )
        text = json.dumps(record.to_dict())
        assert pp.ProposalIterationRecord.from_dict(json.loads(text)) == record

    def test_non_canonical_hard_gate_id_is_refused(self) -> None:
        try:
            pp.ProposalHardGateResult(
                gate_id="MADE_UP_GATE",
                status=pp.ProposalHardGateStatus.PASS,
            )
        except ValueError:
            pass
        else:  # pragma: no cover - guard
            raise AssertionError("non-canonical gate id accepted")


# ---------------------------------------------------------------------------
# 7-11. Workspace contract
# ---------------------------------------------------------------------------


class TestProposalWorkspace:
    def test_initialization_creates_required_directories(self, tmp_path: Path) -> None:
        ws = pp.ProposalWorkspace(tmp_path)
        created = ws.initialize()
        for name in pp.PROPOSAL_WORKSPACE_DIRS:
            assert (tmp_path / name).is_dir(), name
        assert ws.master_proposal_path() == tmp_path / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
        assert ws.master_proposal_path().exists()
        # The report includes exactly what was created.
        created_names = {p.name for p in created}
        assert {"00_SOURCE_OF_TRUTH", "07_FINAL"} <= created_names

    def test_initialization_is_idempotent(self, tmp_path: Path) -> None:
        ws = pp.ProposalWorkspace(tmp_path)
        first = ws.initialize()
        assert first, "first initialization must create the contract"
        second = ws.initialize()
        assert second == [], "second initialization must create NOTHING"

    def test_expected_seed_files_exist_after_init(self, tmp_path: Path) -> None:
        pp.ProposalWorkspace(tmp_path).initialize()
        expected = [
            "00_SOURCE_OF_TRUTH/MASTER_BLUEPRINT.md",
            "00_SOURCE_OF_TRUTH/PROJECT_FACTS.md",
            "00_SOURCE_OF_TRUTH/TEAM.md",
            "00_SOURCE_OF_TRUTH/ARCHITECTURE.md",
            "00_SOURCE_OF_TRUTH/TERMINOLOGY.md",
            "02_EVIDENCE/SOURCE_REGISTRY.json",
            "02_EVIDENCE/CLAIM_LEDGER.json",
            "02_EVIDENCE/AI_USAGE_LOG.json",
            "03_PROPOSAL/MASTER_PROPOSAL.md",
            "05_CONTROL/SCORECARD.json",
            "05_CONTROL/HARD_GATES.json",
            "05_CONTROL/ISSUES.json",
            "05_CONTROL/CONTRADICTIONS.json",
            "05_CONTROL/UNVERIFIED_CLAIMS.json",
            "05_CONTROL/PAGE_BUDGET.json",
        ]
        for rel in expected:
            assert (tmp_path / rel).is_file(), rel

    def test_existing_master_proposal_is_never_overwritten(self, tmp_path: Path) -> None:
        master = tmp_path / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
        master.parent.mkdir(parents=True)
        master.write_text("OPERATOR CONTENT — MUST SURVIVE", encoding="utf-8")
        pp.ProposalWorkspace(tmp_path).initialize()
        assert master.read_text(encoding="utf-8") == "OPERATOR CONTENT — MUST SURVIVE"

    def test_existing_source_of_truth_files_are_never_overwritten(
        self, tmp_path: Path
    ) -> None:
        facts = tmp_path / "00_SOURCE_OF_TRUTH" / "PROJECT_FACTS.md"
        facts.parent.mkdir(parents=True)
        facts.write_text("FACTS v1", encoding="utf-8")
        blueprint = tmp_path / "00_SOURCE_OF_TRUTH" / "MASTER_BLUEPRINT.md"
        blueprint.write_text("BLUEPRINT v1", encoding="utf-8")
        pp.ProposalWorkspace(tmp_path).initialize()
        assert facts.read_text(encoding="utf-8") == "FACTS v1"
        assert blueprint.read_text(encoding="utf-8") == "BLUEPRINT v1"
        # The rest of the contract still materialises.
        assert (tmp_path / "05_CONTROL" / "SCORECARD.json").is_file()

    def test_reinit_over_user_content_changes_nothing(self, tmp_path: Path) -> None:
        pp.ProposalWorkspace(tmp_path).initialize()
        master = tmp_path / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
        master.write_text("# Draft by orchestrator", encoding="utf-8")
        gates = tmp_path / "05_CONTROL" / "HARD_GATES.json"
        gates.write_text('{"gates": []}', encoding="utf-8")
        pp.ProposalWorkspace(tmp_path).initialize()
        assert master.read_text(encoding="utf-8") == "# Draft by orchestrator"
        assert gates.read_text(encoding="utf-8") == '{"gates": []}'

    def test_windows_style_paths_are_handled_safely(self, tmp_path: Path) -> None:
        # A Windows-style root string (drive letter + backslashes) and a
        # backslash-separated contract path must both work.
        root = Path(str(tmp_path))
        ws = pp.ProposalWorkspace(str(root) + "\\")
        ws.initialize()
        assert (root / "03_PROPOSAL" / "MASTER_PROPOSAL.md").is_file()
        # path_for accepts Windows separators on any host OS.
        assert ws.path_for("03_PROPOSAL\\MASTER_PROPOSAL.md") == (
            root / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
        )
        assert ws.path_for("05_CONTROL\\HARD_GATES.json").is_file()

    def test_path_traversal_is_refused(self, tmp_path: Path) -> None:
        ws = pp.ProposalWorkspace(tmp_path)
        for evil in ("..\\outside.md", "../outside.md", "03_PROPOSAL/../../x.md"):
            try:
                ws.path_for(evil)
            except ValueError:
                pass
            else:  # pragma: no cover - guard
                raise AssertionError(f"escape accepted: {evil}")

    def test_non_canonical_paths_are_refused(self, tmp_path: Path) -> None:
        ws = pp.ProposalWorkspace(tmp_path)
        try:
            ws.path_for("99_RANDOM/thing.md")
        except ValueError:
            pass
        else:  # pragma: no cover - guard
            raise AssertionError("non-canonical path accepted")

    def test_workspace_does_not_require_git(self, tmp_path: Path) -> None:
        # No .git anywhere: initialization must still succeed completely.
        assert not (tmp_path / ".git").exists()
        pp.ProposalWorkspace(tmp_path).initialize()
        assert (tmp_path / "07_FINAL").is_dir()

    def test_workspace_paths_helper_is_pure(self, tmp_path: Path) -> None:
        paths = pp.workspace_paths(tmp_path)
        assert set(paths) == set(pp.PROPOSAL_WORKSPACE_DIRS)
        assert paths["02_EVIDENCE"] == tmp_path / "02_EVIDENCE"
        # Pure: calling it created nothing.
        assert not (tmp_path / "02_EVIDENCE").exists()


# ---------------------------------------------------------------------------
# 12. Hard-gate identifiers
# ---------------------------------------------------------------------------


class TestHardGateIdentifiers:
    def test_identifiers_are_exactly_the_canonical_set(self) -> None:
        assert pp.HARD_GATE_IDS_TUPLE == (
            "MANDATORY_SECTIONS",
            "CHALLENGE_MAPPING",
            "SOURCE_OF_TRUTH_INTEGRITY",
            "UNVERIFIED_CLAIMS",
            "CITATION_VERIFICATION",
            "TERMINOLOGY_CONSISTENCY",
            "WP_TASK_CONSISTENCY",
            "WP_DELIVERABLE_CONSISTENCY",
            "WP_MILESTONE_CONSISTENCY",
            "PERSON_MONTH_CONSISTENCY",
            "BUDGET_CONSISTENCY",
            "SUBCONTRACTING_CORE_TASKS",
            "PAGE_LIMIT",
            "INTERNAL_CONTRADICTIONS",
        )

    def test_identifiers_are_unique(self) -> None:
        assert len(pp.HARD_GATE_IDS_TUPLE) == len(set(pp.HARD_GATE_IDS_TUPLE))
        assert len(pp.HARD_GATE_IDS) == len(pp.HARD_GATE_IDS_TUPLE) == 14

    def test_hard_gate_statuses_are_the_contract_set(self) -> None:
        assert {s.value for s in pp.ProposalHardGateStatus} == {
            "PASS",
            "FAIL",
            "WARN",
            "NOT_APPLICABLE",
        }
        assert pp.HARD_GATE_STATUS_VALUES == {
            s.value for s in pp.ProposalHardGateStatus
        }

    def test_review_verdicts_are_the_contract_set(self) -> None:
        assert pp.PROPOSAL_REVIEW_VERDICTS == {"PASS", "NEEDS_REVISION", "BLOCKED"}
        assert {s.value for s in pp.ProposalFindingSeverity} == {
            "critical",
            "high",
            "medium",
            "low",
        }


# ---------------------------------------------------------------------------
# 13-14. Isolation from Coding Mode
# ---------------------------------------------------------------------------


class TestIsolationFromCodingMode:
    def test_proposal_imports_without_pyside6(self) -> None:
        code = (
            "import sys;"
            "import encomm_pcc.proposal;"
            "leaked = [m for m in sys.modules if m.startswith('PySide6')];"
            "assert not leaked, f'Qt leaked into proposal: {leaked}';"
            "print('ok')"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC_ROOT)
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "ok" in result.stdout

    def test_proposal_never_imports_coding_mode_packages(self) -> None:
        code = (
            "import sys;"
            "import encomm_pcc.proposal;"
            "forbidden = [m for m in sys.modules if m.startswith(("
            "'encomm_pcc.core', 'encomm_pcc.domain', 'encomm_pcc.drivers', "
            "'encomm_pcc.persistence', 'encomm_pcc.ui'))];"
            "assert not forbidden, f'coding-mode import leaked: {forbidden}';"
            "print('ok')"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC_ROOT)
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "ok" in result.stdout

    def test_proposal_types_are_distinct_from_coding_types(self) -> None:
        from encomm_pcc.domain.enums import AgentRole, PipelinePhase

        # Parallel types — same values in places, but never the same type.
        assert pp.ProposalRole is not AgentRole
        assert pp.ProposalPhase is not PipelinePhase
        assert pp.ProposalPhase.IDLE is not PipelinePhase.IDLE

    def test_coding_mode_enums_are_unchanged(self) -> None:
        from encomm_pcc.domain.enums import AgentRole, PipelinePhase

        assert {r.value for r in AgentRole} == {
            "ORCHESTRATOR",
            "BUILDER",
            "TASK_AUDITOR",
            "FINAL_AUDITOR",
        }
        assert {p.value for p in PipelinePhase} == {
            "IDLE",
            "PLANNING_BATCH",
            "RUNNING_TASK",
            "AUDITING_TASK",
            "FIX_REQUIRED",
            "RUNNING_FIX",
            "READY_FOR_FINAL_AUDIT",
            "FINAL_AUDIT_RUNNING",
            "BATCH_COMPLETE",
            "PAUSED",
            "BLOCKED",
            "FAILED",
        }
        # No proposal phase leaked into the coding enum: none of the
        # proposal-specific phase names may appear among the coding values.
        proposal_only = {p.value for p in pp.ProposalPhase} - {
            p.value for p in PipelinePhase
        }
        assert proposal_only == {
            "SOURCE_VALIDATION",
            "SCIENTIFIC_REVIEW",
            "IMPLEMENTATION_REVIEW",
            "RED_TEAM_REVIEW",
            "INTEGRATION",
            "HARD_GATE_VALIDATION",
            "REVISION_REQUIRED",
            "COMPLETE",
        }
        assert not {p.value for p in PipelinePhase} & proposal_only

    def test_coding_mode_transition_graph_is_unchanged(self) -> None:
        from encomm_pcc.domain.enums import PipelinePhase
        from encomm_pcc.domain.state_machine import TRANSITIONS

        # Spot-pin the edges a Proposal Mode accident would most likely break.
        assert TRANSITIONS[PipelinePhase.IDLE] == frozenset(
            {PipelinePhase.PLANNING_BATCH}
        )
        assert TRANSITIONS[PipelinePhase.BATCH_COMPLETE] == frozenset(
            {PipelinePhase.IDLE, PipelinePhase.PLANNING_BATCH}
        )
        # The coding enum has no COMPLETE member at all (value-level check —
        # accessing a missing member would itself raise).
        assert "COMPLETE" not in {p.value for p in PipelinePhase}
        assert TRANSITIONS[PipelinePhase.FINAL_AUDIT_RUNNING] >= frozenset(
            {PipelinePhase.BATCH_COMPLETE, PipelinePhase.FIX_REQUIRED}
        )

    def test_coding_state_machine_unaffected_by_proposal_machine(self) -> None:
        from encomm_pcc.domain.enums import PipelinePhase
        from encomm_pcc.domain.state_machine import StateMachine

        coding = StateMachine()
        coding.transition_to(PipelinePhase.PLANNING_BATCH)
        proposal = pp.ProposalStateMachine()
        proposal.transition_to(pp.ProposalPhase.SOURCE_VALIDATION)
        # Independent instances, independent states.
        assert coding.phase is PipelinePhase.PLANNING_BATCH
        assert proposal.phase is pp.ProposalPhase.SOURCE_VALIDATION
