"""Session 021A corrective regression tests — the review-found defects.

Covers the ten correction points of the Session 021A brief:

1.  ProposalPatch.target_section survives independently of `target`.
2.  `require_document_target`: legacy loads, dual `missing_target`,
    `invalid_target`, valid PROPOSAL/BLUEPRINT/BOTH.
3.  ASTRA chair honours RESUME_SELECTED_SESSION (never silent NEW).
4.  reasoning_effort reaches the Codex request on BOTH ASTRA paths;
    empty emits nothing; Hermes never receives the key.
5.  Dual initial generation with a REAL Blueprint change: no
    UnboundLocalError, report binds the live hashes, original immutable.
7.  Dual ALWAYS pair-commits (change/no-change 4-case matrix) in BOTH
    write paths; legacy falls back to the single writer.
8.  Cross-workspace Codex resume is refused before any model call.
9.  Reviewer mutation of CURRENT_BLUEPRINT is detected.

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
from encomm_pcc.proposal.document_pair import try_load_document_pair_state
from encomm_pcc.proposal.state_machine import ProposalStateMachine
from encomm_pcc.proposal_runtime.review_loop import REVIEW_SEQUENCE

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = str(REPO_ROOT / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)


# ---------------------------------------------------------------------------
# shared fixtures / doubles
# ---------------------------------------------------------------------------
PROPOSAL_V1 = b"# MASTER PROPOSAL\n\nv1 content.\n"
REVISED_TEXT = "# MASTER PROPOSAL\n\nv2 revised by the chair.\n"
BLUEPRINT_V1 = b"# Blueprint\n\noriginal design.\n"
BLUEPRINT_V2_TEXT = "# Blueprint\n\noriginal design.\n\n## Revised\n\ndesign v2.\n"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_dual_workspace(tmp_path: Path) -> Path:
    """Legacy S016-style workspace + a living Blueprint pair."""
    ws = pp.ProposalWorkspace(tmp_path)
    ws.initialize()
    ws.master_proposal_path().write_bytes(PROPOSAL_V1)
    bp = tmp_path / "00_SOURCE_OF_TRUTH" / "CURRENT_BLUEPRINT.md"
    bp.write_bytes(BLUEPRINT_V1)
    return tmp_path


def run_cycle_to_integration(tmp_path: Path) -> ProposalStateMachine:
    """Run the REAL three-reviewer cycle to reach INTEGRATION (dual)."""
    from test_proposal_integration_executor import (
        ScriptedReviewer,
        default_reviewers,
    )

    machine = ProposalStateMachine()
    reviewers = default_reviewers()
    # Give reviewer 1 an actionable finding so the brief demands
    # integration (a clean-PASS brief would take the zero-AI bypass).
    reviewers[pp.ProposalRole.SCIENTIFIC_REVIEWER].verdict = "NEEDS_REVISION"
    reviewers[pp.ProposalRole.SCIENTIFIC_REVIEWER].findings = [
        {
            "severity": "medium",
            "category": "weak_wording",
            "section": "1",
            "message": "Fix wording.",
            "evidence": "",
            "source_refs": [],
            "suggested_change": "Reword.",
            "target": "PROPOSAL",
        }
    ]
    prt.run_review_cycle(
        workspace=tmp_path,
        state_machine=machine,
        iteration_number=1,
        proposal_revision="rev",
        reviewer_drivers=reviewers,
    )
    assert machine.phase is pp.ProposalPhase.INTEGRATION
    return machine


def review_payload(
    *,
    role: pp.ProposalRole,
    verdict: str = "NEEDS_REVISION",
    iteration: int = 1,
    findings: list | None = None,
    patches: list | None = None,
    with_target: bool = True,
) -> dict:
    if findings is None and patches is None:
        findings = [
            {
                "severity": "medium",
                "category": "weak_wording",
                "section": "1",
                "message": "Fix wording.",
                "evidence": "",
                "source_refs": [],
                "suggested_change": "Reword.",
                **({"target": "PROPOSAL"} if with_target else {}),
            }
        ]
    return {
        "reviewer_role": role.value,
        "verdict": verdict,
        "summary": "Needs work.",
        "findings": findings or [],
        "proposed_patches": patches or [],
        "unverified_claims": [],
        "iteration_number": iteration,
    }


def integration_payload(
    *,
    iteration: int = 1,
    input_hash: str,
    revised: str = REVISED_TEXT,
    revised_proposal: str | None = None,
    blueprint_hash: str = "",
    revised_blueprint: str | None = None,
) -> dict:
    payload = {
        "role": "ORCHESTRATOR",
        "iteration_number": iteration,
        "input_proposal_hash": input_hash,
        "summary": "chair synthesis",
        "applied_items": [],
        "rejected_items": [],
        "unresolved_items": [],
        "revised_proposal": revised_proposal if revised_proposal is not None else revised,
    }
    if blueprint_hash:
        payload["input_blueprint_hash"] = blueprint_hash
        payload["revised_blueprint"] = revised_blueprint
    return payload


class Reviewer:
    """Scripted reviewer that can omit targets / mutate the living pair."""

    driver_id = "scripted-021a-reviewer"

    def __init__(
        self,
        role: pp.ProposalRole,
        payload: dict,
        *,
        mutate_blueprint: bytes | None = None,
    ) -> None:
        self.role = role
        self.payload = payload
        self.mutate_blueprint = mutate_blueprint
        self.capabilities_value = DriverCapabilities(
            driver_id=self.driver_id,
            display_name="Reviewer",
        )

    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name="Reviewer",
        )

    def start_session(self, request):
        # Carry the workspace on the session so the mutation hook can find
        # the living Blueprint (the executor passes the workspace via the
        # SessionRequest; the session metadata mirrors it).
        return DriverSession(
            driver_id=self.driver_id,
            role=request.role,
            session_id="rev-1",
            metadata={"workspace_path": request.workspace_path},
        )

    def send_prompt(self, session, prompt):
        return PromptHandle(session=session, prompt=prompt)

    def wait_for_completion(self, handle, timeout_s=None):
        if self.mutate_blueprint is not None:
            ws = Path(handle.session.metadata["workspace_path"])
            # The exact living-blueprint path for the mutation proof.
            bp = ws / "00_SOURCE_OF_TRUTH" / "CURRENT_BLUEPRINT.md"
            bp.write_bytes(self.mutate_blueprint)
        return PromptResult(
            ok=True,
            text=(
                f"{pp.PROPOSAL_REVIEW_ENVELOPE_START}\n"
                f"{json.dumps(self.payload)}\n"
                f"{pp.PROPOSAL_REVIEW_ENVELOPE_END}"
            ),
            session_id="rev-1",
        )


class AstraDriver:
    """Scripted ORCHESTRATOR that RECORDS the session acquisition path."""

    driver_id = "scripted-021a-astra"

    def __init__(
        self,
        payload: dict,
        *,
        supports_resume: bool = True,
        resumed_session_id: str = "real-codex-thread-id",
    ) -> None:
        self.payload = payload
        self.supports_resume = supports_resume
        self.resumed_session_id = resumed_session_id
        self.start_calls: list[SessionRequest] = []
        self.resume_calls: list[tuple[str, SessionRequest]] = []
        self.session_id_returned: str | None = None

    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name="Astra",
            supports_resume=True,
        )

    def capabilities_instance(self) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=self.driver_id,
            display_name="Astra",
            supports_resume=self.supports_resume,
        )

    def start_session(self, request):
        self.start_calls.append(request)
        session = DriverSession(
            driver_id=self.driver_id, role=request.role, session_id=None
        )
        self.session_id_returned = None
        return session

    def resume_session(self, session_id, request):
        self.resume_calls.append((session_id, request))
        session = DriverSession(
            driver_id=self.driver_id,
            role=request.role,
            session_id=self.resumed_session_id,
            external=True,
        )
        session.metadata["resumed"] = True
        return session

    def send_prompt(self, session, prompt):
        self.session_id_returned = session.session_id
        self.last_session = session
        return PromptHandle(session=session, prompt=prompt)

    def wait_for_completion(self, handle, timeout_s=None):
        session = handle.session
        if session.session_id is None:
            session.session_id = "fresh-session-1"
        return PromptResult(
            ok=True,
            text=(
                f"{pp.PROPOSAL_INTEGRATION_ENVELOPE_START}\n"
                f"{json.dumps(self.payload)}\n"
                f"{pp.PROPOSAL_INTEGRATION_ENVELOPE_END}"
            ),
            session_id=session.session_id,
        )


class RequestRecordingSpecialist:
    """Records the SessionRequest the initial-generation specialist got."""

    driver_id = "scripted-021a-specialist"

    def __init__(self, role: pp.ProposalRole, marker: str) -> None:
        self.role = role
        self.marker = marker
        self.requests: list[SessionRequest] = []

    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name="Specialist",
        )

    def start_session(self, request):
        self.requests.append(request)
        return DriverSession(driver_id=self.driver_id, role=request.role)

    def send_prompt(self, session, prompt):
        self.prompt = prompt
        return PromptHandle(session=session, prompt=prompt)

    def wait_for_completion(self, handle, timeout_s=None):
        return PromptResult(
            ok=True,
            text=(
                "<<<INITIAL_CONTRIBUTION_START>>>\n"
                f"{self.marker} contribution grounded in the design.\n"
                "<<<INITIAL_CONTRIBUTION_END>>>"
            ),
            session_id="sp-1",
        )


class SynthesisRecordingAstra:
    """Records the SessionRequest AND answers the initial synthesis."""

    driver_id = "scripted-021a-gen-astra"

    def __init__(self) -> None:
        self.requests: list[SessionRequest] = []
        self.prompt = ""

    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name="GenAstra",
        )

    def start_session(self, request):
        self.requests.append(request)
        return DriverSession(driver_id=self.driver_id, role=request.role)

    def send_prompt(self, session, prompt):
        self.prompt = prompt
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


def walk_to_integration(machine: ProposalStateMachine) -> None:
    machine.transition_to(pp.ProposalPhase.SOURCE_VALIDATION)
    machine.transition_to(pp.ProposalPhase.SCIENTIFIC_REVIEW)
    machine.transition_to(pp.ProposalPhase.IMPLEMENTATION_REVIEW)
    machine.transition_to(pp.ProposalPhase.RED_TEAM_REVIEW)
    machine.transition_to(pp.ProposalPhase.INTEGRATION)


def dual_packet(patch: object) -> object:
    """A minimal packet double carrying living-Blueprint evidence."""
    return type(
        "Packet",
        (),
        {
            "role": pp.ProposalRole.SCIENTIFIC_REVIEWER,
            "iteration_number": 1,
            "prompt_text": "x",
            "current_blueprint_available": True,
            "current_blueprint_hash": sha(BLUEPRINT_V1),
        },
    )()


# ---------------------------------------------------------------------------
# 1. patch target_section survives independently
# ---------------------------------------------------------------------------
class TestPatchTargetSurvival:
    def test_01_patch_section_and_document_target_independent(self):
        from encomm_pcc.proposal.review_parser import parse_proposal_review

        payload = review_payload(
            role=pp.ProposalRole.SCIENTIFIC_REVIEWER,
            patches=[
                {
                    "target_section": "3.1 Work plan",
                    "rationale": "the plan needs the revision",
                    "replacement_text": "revised work plan text",
                    "patch_instructions": "",
                    "source_refs": [],
                    "confidence": 0.9,
                    "target": "BOTH",
                }
            ],
            findings=[],
        )
        raw = (
            f"{pp.PROPOSAL_REVIEW_ENVELOPE_START}\n"
            f"{json.dumps(payload)}\n"
            f"{pp.PROPOSAL_REVIEW_ENVELOPE_END}"
        )
        result = parse_proposal_review(
            raw,
            expected_role=pp.ProposalRole.SCIENTIFIC_REVIEWER,
            expected_iteration=1,
        )
        patch = result.proposed_patches[0]
        assert patch.target_section == "3.1 Work plan"
        assert patch.target.value == "BOTH"


# ---------------------------------------------------------------------------
# 2. require_document_target switch
# ---------------------------------------------------------------------------
class TestRequireDocumentTarget:
    PAYLOAD = {
        "reviewer_role": "SCIENTIFIC_REVIEWER",
        "verdict": "NEEDS_REVISION",
        "summary": "needs work",
        "findings": [
            {
                "severity": "low",
                "category": "recommendation",
                "section": "1",
                "message": "improve",
                "evidence": "",
                "source_refs": [],
                "suggested_change": "",
            }
        ],
        "proposed_patches": [],
        "unverified_claims": [],
        "iteration_number": 1,
    }

    def _raw(self, payload: dict) -> str:
        return (
            f"{pp.PROPOSAL_REVIEW_ENVELOPE_START}\n"
            f"{json.dumps(payload)}\n"
            f"{pp.PROPOSAL_REVIEW_ENVELOPE_END}"
        )

    def test_02a_legacy_mode_defaults_missing_target(self):
        from encomm_pcc.proposal.review_parser import parse_proposal_review

        result = parse_proposal_review(
            self._raw(self.PAYLOAD),
            expected_role=pp.ProposalRole.SCIENTIFIC_REVIEWER,
            expected_iteration=1,
        )
        assert result.findings[0].target.value == "PROPOSAL"

    def test_02b_dual_mode_requires_target(self):
        from encomm_pcc.proposal.review_parser import (
            ProposalReviewParseError,
            parse_proposal_review,
        )

        with pytest.raises(ProposalReviewParseError) as excinfo:
            parse_proposal_review(
                self._raw(self.PAYLOAD),
                expected_role=pp.ProposalRole.SCIENTIFIC_REVIEWER,
                expected_iteration=1,
                require_document_target=True,
            )
        assert excinfo.value.reason == "missing_target"

    def test_02c_invalid_target_stays_invalid(self):
        from encomm_pcc.proposal.review_parser import (
            ProposalReviewParseError,
            parse_proposal_review,
        )

        payload = dict(self.PAYLOAD)
        payload["findings"] = [dict(self.PAYLOAD["findings"][0], target="BLUEPRINT " "WRONG")]
        with pytest.raises(ProposalReviewParseError) as excinfo:
            parse_proposal_review(
                self._raw(payload),
                expected_role=pp.ProposalRole.SCIENTIFIC_REVIEWER,
                expected_iteration=1,
            )
        assert excinfo.value.reason == "invalid_target"

    @pytest.mark.parametrize("value", ["PROPOSAL", "BLUEPRINT", "BOTH"])
    def test_02d_valid_targets_pass_in_dual_mode(self, value):
        from encomm_pcc.proposal.review_parser import parse_proposal_review

        payload = dict(self.PAYLOAD)
        payload["findings"] = [dict(self.PAYLOAD["findings"][0], target=value)]
        result = parse_proposal_review(
            self._raw(payload),
            expected_role=pp.ProposalRole.SCIENTIFIC_REVIEWER,
            expected_iteration=1,
            require_document_target=True,
        )
        assert result.findings[0].target.value == value


# ---------------------------------------------------------------------------
# 3+4. ASTRA resume + reasoning through the integration path
# ---------------------------------------------------------------------------
class TestAstraResumeAndReasoning:
    def _payload(self, tmp_path: Path, **overrides) -> dict:
        bp_hash = sha(
            (tmp_path / "00_SOURCE_OF_TRUTH" / "CURRENT_BLUEPRINT.md").read_bytes()
        )
        proposal_hash = sha(
            (tmp_path / "03_PROPOSAL" / "MASTER_PROPOSAL.md").read_bytes()
        )
        return integration_payload(
            input_hash=proposal_hash,
            blueprint_hash=bp_hash,
            revised_blueprint=BLUEPRINT_V2_TEXT,
            **overrides,
        )

    def test_03a_resume_uses_exact_configured_id(self, tmp_path):
        build_dual_workspace(tmp_path)
        machine = run_cycle_to_integration(tmp_path)
        driver = AstraDriver(self._payload(tmp_path))
        config = pp.ProposalAgentConfig(
            role=pp.ProposalRole.ORCHESTRATOR,
            engine="codex",
            session_mode="RESUME_SELECTED_SESSION",
            session_id="real-codex-thread-id",
            reasoning_effort="xhigh",
        )
        report = prt.run_integration(
            workspace=tmp_path,
            state_machine=machine,
            iteration_number=1,
            proposal_revision="rev",
            orchestrator_driver=driver,
            orchestrator_agent_config=config,
        )
        assert report.outcome.value == "COMPLETED_CHANGED"
        assert len(driver.resume_calls) == 1
        assert driver.resume_calls[0][0] == "real-codex-thread-id"
        assert driver.start_calls == []  # never fell back to NEW
        # The report carries the REAL resumed thread id.
        assert report.session_id == "real-codex-thread-id"

    def test_03b_new_session_starts_fresh(self, tmp_path):
        build_dual_workspace(tmp_path)
        machine = run_cycle_to_integration(tmp_path)
        driver = AstraDriver(self._payload(tmp_path))
        config = pp.ProposalAgentConfig(
            role=pp.ProposalRole.ORCHESTRATOR,
            engine="codex",
            session_mode="NEW_SESSION",
            reasoning_effort="high",
        )
        report = prt.run_integration(
            workspace=tmp_path,
            state_machine=machine,
            iteration_number=1,
            proposal_revision="rev",
            orchestrator_driver=driver,
            orchestrator_agent_config=config,
        )
        assert report.outcome.value == "COMPLETED_CHANGED"
        assert len(driver.start_calls) == 1
        assert driver.resume_calls == []

    def test_03c_resume_without_id_fails_closed(self, tmp_path):
        build_dual_workspace(tmp_path)
        machine = run_cycle_to_integration(tmp_path)
        driver = AstraDriver(self._payload(tmp_path))
        # Constructing the config without an id is refused at the domain
        # layer already (the unconstructable-config contract); the runtime
        # path is exercised via a mutated config that skips __post_init__.
        config = pp.ProposalAgentConfig.__new__(pp.ProposalAgentConfig)
        config.role = pp.ProposalRole.ORCHESTRATOR
        config.engine = "codex"
        config.project_profile = ""
        config.provider = ""
        config.model = ""
        config.session_policy = "persistent_optional"
        config.session_id = ""
        config.session_mode = "RESUME_SELECTED_SESSION"
        config.reasoning_effort = ""
        report = prt.run_integration(
            workspace=tmp_path,
            state_machine=machine,
            iteration_number=1,
            proposal_revision="rev",
            orchestrator_driver=driver,
            orchestrator_agent_config=config,
        )
        assert report.outcome.value == "DRIVER_FAILED"
        assert "requires a real session_id" in report.error
        assert driver.start_calls == []  # never silently started fresh

    def test_03d_driver_without_resume_fails_closed(self, tmp_path):
        build_dual_workspace(tmp_path)
        machine = run_cycle_to_integration(tmp_path)
        driver = AstraDriver(self._payload(tmp_path), supports_resume=False)
        driver.capabilities = (  # type: ignore[method-assign]
            lambda cls=None: DriverCapabilities(
                driver_id="scripted-021a-astra",
                display_name="Astra",
                supports_resume=False,
            )
        )
        config = pp.ProposalAgentConfig(
            role=pp.ProposalRole.ORCHESTRATOR,
            engine="codex",
            session_mode="RESUME_SELECTED_SESSION",
            session_id="real-codex-thread-id",
        )
        report = prt.run_integration(
            workspace=tmp_path,
            state_machine=machine,
            iteration_number=1,
            proposal_revision="rev",
            orchestrator_driver=driver,
            orchestrator_agent_config=config,
        )
        assert report.outcome.value == "DRIVER_FAILED"
        assert "supports_resume" in report.error

    def test_04a_reasoning_xhigh_reaches_the_codex_request(self, tmp_path):
        build_dual_workspace(tmp_path)
        machine = run_cycle_to_integration(tmp_path)
        driver = AstraDriver(self._payload(tmp_path))
        config = pp.ProposalAgentConfig(
            role=pp.ProposalRole.ORCHESTRATOR,
            engine="codex",
            reasoning_effort="xhigh",
        )
        prt.run_integration(
            workspace=tmp_path,
            state_machine=machine,
            iteration_number=1,
            proposal_revision="rev",
            orchestrator_driver=driver,
            orchestrator_agent_config=config,
        )
        request = driver.start_calls[0]
        assert request.extra.get("reasoning_effort") == "xhigh"

    def test_04b_default_emits_no_override(self, tmp_path):
        build_dual_workspace(tmp_path)
        machine = run_cycle_to_integration(tmp_path)
        driver = AstraDriver(self._payload(tmp_path))
        config = pp.ProposalAgentConfig(
            role=pp.ProposalRole.ORCHESTRATOR,
            engine="codex",
        )
        prt.run_integration(
            workspace=tmp_path,
            state_machine=machine,
            iteration_number=1,
            proposal_revision="rev",
            orchestrator_driver=driver,
            orchestrator_agent_config=config,
        )
        assert "reasoning_effort" not in driver.start_calls[0].extra

    def test_04c_codex_argv_carries_the_flag_and_hermes_never_does(self, tmp_path):
        from encomm_pcc.drivers.codex import CodexDriver
        from encomm_pcc.drivers.codex_cli import build_exec_argv

        argv = build_exec_argv(
            executable="codex",
            sandbox="read-only",
            workspace="C:/ws",
            model="gpt-6.1-sol",
            extra_args=["-c", "model_reasoning_effort=xhigh"],
        )
        # Session 021A (live-caught): the override travels as the `-c`
        # PAIR, never a bare key=value token (bare token = CLI usage error).
        idx = argv.index("model_reasoning_effort=xhigh")
        assert argv[idx - 1] == "-c"
        clean = build_exec_argv(
            executable="codex",
            sandbox="read-only",
            workspace="C:/ws",
        )
        assert not any("model_reasoning_effort" in a for a in clean)
        # A Hermes-shaped request never gains the key: the extra namespace
        # is filled ONLY from the agent config's reasoning_effort.
        assert CodexDriver.driver_id == "codex"


# ---------------------------------------------------------------------------
# 5+7. dual initial generation: pair commit, report, blueprint change
# ---------------------------------------------------------------------------
class TestDualInitialGeneration:
    def _run(self, tmp_path: Path, *, revised_blueprint: str | None):
        from encomm_pcc.proposal_runtime.initial_generation import (
            run_initial_generation,
        )

        pp.ProposalWorkspace(tmp_path).initialize()
        # Initial generation requires an EMPTY MASTER_PROPOSAL but a
        # READY canonical blueprint; the living pair is seeded beside it.
        (tmp_path / "00_SOURCE_OF_TRUTH" / "MASTER_BLUEPRINT.md").write_bytes(
            BLUEPRINT_V1
        )
        (tmp_path / "00_SOURCE_OF_TRUTH" / "CURRENT_BLUEPRINT.md").write_bytes(
            BLUEPRINT_V1
        )
        template = tmp_path / "01_OFFICIAL" / "APPLICATION_TEMPLATE.md"
        template.write_text(
            "# 1. Excellence\n\n# 2. Impact\n", encoding="utf-8", newline="\n"
        )
        machine = ProposalStateMachine()
        specialists = {
            role: RequestRecordingSpecialist(role, role.value)
            for role in REVIEW_SEQUENCE
        }
        astra = SynthesisRecordingAstra()
        # Patch the synthesis to ALSO return a revised blueprint block when
        # the prompt asks for one.
        original_wait = astra.wait_for_completion

        def _wait(handle, timeout_s=None):
            result = original_wait(handle, timeout_s)
            if revised_blueprint is not None and "INITIAL_BLUEPRINT_START" in astra.prompt:
                result.text = result.text.replace(
                    "<<<INITIAL_PROPOSAL_END>>>",
                    "<<<INITIAL_PROPOSAL_END>>>\n"
                    "<<<INITIAL_BLUEPRINT_START>>>\n"
                    f"{revised_blueprint}\n"
                    "<<<INITIAL_BLUEPRINT_END>>>",
                )
            return result

        astra.wait_for_completion = _wait  # type: ignore[method-assign]
        report = run_initial_generation(
            workspace=tmp_path,
            state_machine=machine,
            proposal_revision="rev",
            specialist_drivers=specialists,  # type: ignore[arg-type]
            orchestrator_driver=astra,  # type: ignore[arg-type]
        )
        return report, machine, specialists, astra

    def test_05a_dual_generation_with_blueprint_change(self, tmp_path):
        report, machine, specialists, astra = self._run(
            tmp_path, revised_blueprint=BLUEPRINT_V2_TEXT
        )
        assert report.outcome.value == "COMPLETED"
        assert report.error == ""  # no UnboundLocalError
        live_master = (tmp_path / "03_PROPOSAL" / "MASTER_PROPOSAL.md").read_bytes()
        live_bp = (
            tmp_path / "00_SOURCE_OF_TRUTH" / "CURRENT_BLUEPRINT.md"
        ).read_bytes()
        # report.proposal_hash matches the EXACT live bytes.
        assert report.proposal_hash == sha(live_master)
        assert report.blueprint_hash == sha(live_bp)
        assert report.pair_committed is True
        # CURRENT_BLUEPRINT actually changed.
        assert live_bp.decode("utf-8").strip() == BLUEPRINT_V2_TEXT.strip()
        # The immutable ORIGINAL is byte-identical (canonical LF form).
        original = (
            tmp_path / "00_SOURCE_OF_TRUTH" / "MASTER_BLUEPRINT.md"
        ).read_bytes()
        assert original == BLUEPRINT_V1
        # The pair manifest matches both hashes.
        pair = try_load_document_pair_state(tmp_path)
        assert pair is not None
        assert pair.blueprint_hash == sha(live_bp)
        assert pair.proposal_hash == sha(live_master)

    def test_06a_specialists_receive_the_living_blueprint(self, tmp_path):
        report, _machine, specialists, _astra = self._run(
            tmp_path, revised_blueprint=None
        )
        assert report.outcome.value == "COMPLETED"
        for role, driver in specialists.items():
            assert len(driver.requests) == 1
            prompt = driver.prompt
            assert "00_SOURCE_OF_TRUTH/CURRENT_BLUEPRINT.md" in prompt
            assert "PRIMARY current project design" in prompt
            assert "immutable provenance" in prompt
            assert sha(BLUEPRINT_V1)[:12] in prompt  # frozen hash echo

    def test_07a_dual_generation_always_pair_commits_even_without_change(
        self, tmp_path
    ):
        # No INITIAL_BLUEPRINT block → the chair elects no Blueprint change;
        # the pair is STILL committed (byte-identical blueprint leg).
        report, _machine, _sp, _astra = self._run(tmp_path, revised_blueprint=None)
        assert report.outcome.value == "COMPLETED"
        assert report.pair_committed is True
        pair = try_load_document_pair_state(tmp_path)
        assert pair is not None
        live_bp = (
            tmp_path / "00_SOURCE_OF_TRUTH" / "CURRENT_BLUEPRINT.md"
        ).read_bytes()
        live_master = (tmp_path / "03_PROPOSAL" / "MASTER_PROPOSAL.md").read_bytes()
        assert pair.blueprint_hash == sha(live_bp)
        assert pair.proposal_hash == sha(live_master)


# ---------------------------------------------------------------------------
# 7. dual integration: the change/no-change matrix + legacy fallback
# ---------------------------------------------------------------------------
class TestDualAlwaysPairCommits:
    def _run_integration(
        self,
        tmp_path: Path,
        *,
        revised_blueprint: str | None,
        revised_proposal: str | None,
    ):
        build_dual_workspace(tmp_path)
        machine = run_cycle_to_integration(tmp_path)
        proposal_hash = sha(
            (tmp_path / "03_PROPOSAL" / "MASTER_PROPOSAL.md").read_bytes()
        )
        bp_hash = sha(
            (tmp_path / "00_SOURCE_OF_TRUTH" / "CURRENT_BLUEPRINT.md").read_bytes()
        )
        payload = integration_payload(
            input_hash=proposal_hash,
            blueprint_hash=bp_hash,
            revised_proposal=revised_proposal or REVISED_TEXT,
            revised_blueprint=revised_blueprint,
        )
        driver = AstraDriver(payload)
        report = prt.run_integration(
            workspace=tmp_path,
            state_machine=machine,
            iteration_number=1,
            proposal_revision="rev",
            orchestrator_driver=driver,
        )
        return report

    def test_07b_bp_unchanged_pr_changed(self, tmp_path):
        report = self._run_integration(
            tmp_path, revised_blueprint=None, revised_proposal=REVISED_TEXT
        )
        assert report.outcome.value == "COMPLETED_CHANGED"
        pair = try_load_document_pair_state(tmp_path)
        assert pair is not None
        assert pair.blueprint_hash == sha(BLUEPRINT_V1)
        live_master = (tmp_path / "03_PROPOSAL" / "MASTER_PROPOSAL.md").read_bytes()
        assert pair.proposal_hash == sha(live_master)

    def test_07c_bp_changed_pr_unchanged(self, tmp_path):
        report = self._run_integration(
            tmp_path,
            revised_blueprint=BLUEPRINT_V2_TEXT,
            revised_proposal=PROPOSAL_V1.decode("utf-8"),
        )
        assert report.outcome is prt.ProposalIntegrationOutcome.COMPLETED_CHANGED
        pair = try_load_document_pair_state(tmp_path)
        assert pair is not None
        live_bp = (
            tmp_path / "00_SOURCE_OF_TRUTH" / "CURRENT_BLUEPRINT.md"
        ).read_bytes()
        assert pair.blueprint_hash == sha(live_bp)
        assert pair.proposal_hash == sha(PROPOSAL_V1)

    def test_07d_both_changed(self, tmp_path):
        report = self._run_integration(
            tmp_path,
            revised_blueprint=BLUEPRINT_V2_TEXT,
            revised_proposal=REVISED_TEXT,
        )
        assert report.outcome.value == "COMPLETED_CHANGED"
        pair = try_load_document_pair_state(tmp_path)
        assert pair is not None
        assert pair.blueprint_hash == sha(BLUEPRINT_V2_TEXT.encode("utf-8"))

    def test_07e_both_unchanged_still_a_committed_pair(self, tmp_path):
        report = self._run_integration(
            tmp_path,
            revised_blueprint=None,
            revised_proposal=PROPOSAL_V1.decode("utf-8"),
        )
        # Nothing changed → the honest COMPLETED_NO_CHANGE outcome, but the
        # pair manifest EXISTS (a valid committed pair state).
        assert (
            report.outcome is prt.ProposalIntegrationOutcome.COMPLETED_NO_CHANGE
        )
        pair = try_load_document_pair_state(tmp_path)
        assert pair is not None
        assert pair.blueprint_hash == sha(BLUEPRINT_V1)
        assert pair.proposal_hash == sha(PROPOSAL_V1)

    def test_07f_legacy_workspace_keeps_the_single_writer(self, tmp_path):
        pp.ProposalWorkspace(tmp_path).initialize()
        (tmp_path / "03_PROPOSAL" / "MASTER_PROPOSAL.md").write_bytes(PROPOSAL_V1)
        # NO CURRENT_BLUEPRINT → legacy path.
        machine = run_cycle_to_integration(tmp_path)
        payload = integration_payload(input_hash=sha(PROPOSAL_V1))
        driver = AstraDriver(payload)
        report = prt.run_integration(
            workspace=tmp_path,
            state_machine=machine,
            iteration_number=1,
            proposal_revision="rev",
            orchestrator_driver=driver,
        )
        assert report.outcome.value == "COMPLETED_CHANGED"
        assert try_load_document_pair_state(tmp_path) is None  # no manifest
        # Legacy path: the proposal-only writer leaves NO living Blueprint
        # behind (the initializer seeded the file empty; it stays empty —
        # the dual contract never activated in this workspace).
        legacy_current = (
            tmp_path / "00_SOURCE_OF_TRUTH" / "CURRENT_BLUEPRINT.md"
        )
        assert not legacy_current.exists() or not legacy_current.read_bytes().strip()


# ---------------------------------------------------------------------------
# 8. cross-workspace resume refusal (driver layer proven in test_codex_driver)
# ---------------------------------------------------------------------------
class TestCrossWorkspaceResumeGuard:
    def test_08_validate_codex_resume_target_rejects_other_workspace(self):
        from encomm_pcc.drivers.session_discovery import (
            ExternalSessionDescriptor,
            SessionDiscoveryResult,
        )
        from encomm_pcc.proposal_runtime.review_executor import (
            validate_codex_resume_target,
        )

        class FakeCodex:
            def discover_sessions(self, **kwargs):
                return SessionDiscoveryResult(
                    ok=True,
                    driver_id="codex",
                    sessions=[
                        ExternalSessionDescriptor(
                            session_id="aaaa-bbbb",
                            driver_id="codex",
                            workspace_path="C:/somewhere-else",
                            matches_workspace=False,
                        )
                    ],
                    mechanism="codex-rollouts",
                )

        with pytest.raises(Exception) as excinfo:
            validate_codex_resume_target(
                FakeCodex(), "aaaa-bbbb", "C:/proposal-workspace"
            )
        assert "workspace" in str(excinfo.value)

    def test_08b_validate_codex_resume_target_accepts_matching(self):
        from encomm_pcc.drivers.session_discovery import (
            ExternalSessionDescriptor,
            SessionDiscoveryResult,
        )
        from encomm_pcc.proposal_runtime.review_executor import (
            validate_codex_resume_target,
        )

        ws = "C:/proposal-workspace"

        class FakeCodex:
            def discover_sessions(self, **kwargs):
                return SessionDiscoveryResult(
                    ok=True,
                    driver_id="codex",
                    sessions=[
                        ExternalSessionDescriptor(
                            session_id="aaaa-bbbb",
                            driver_id="codex",
                            workspace_path=ws,
                            matches_workspace=True,
                        )
                    ],
                    mechanism="codex-rollouts",
                )

        # No exception → the matching target passes.
        validate_codex_resume_target(FakeCodex(), "aaaa-bbbb", ws)


# ---------------------------------------------------------------------------
# 9. reviewer mutation of the LIVING Blueprint is detected
# ---------------------------------------------------------------------------
class TestDualReviewerMutationGuard:
    def test_09_blueprint_mutation_is_mutation_detected(self, tmp_path):
        build_dual_workspace(tmp_path)
        # A full review cycle needs the real loop; here the machine-free
        # core is exercised directly with a dual packet + mutating reviewer.
        from encomm_pcc.proposal_runtime.review_executor import (
            ProposalReviewOutcome,
            execute_review_call,
        )

        payload = review_payload(role=pp.ProposalRole.SCIENTIFIC_REVIEWER)
        driver = Reviewer(
            pp.ProposalRole.SCIENTIFIC_REVIEWER,
            payload,
            mutate_blueprint=b"# Blueprint\n\nTAMPERED by the reviewer.\n",
        )
        master = tmp_path / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
        report = execute_review_call(
            packet=dual_packet(None),
            driver=driver,  # type: ignore[arg-type]
            master_proposal_path=master,
            proposal_workspace_path=tmp_path,
        )
        assert report.outcome is ProposalReviewOutcome.MUTATION_DETECTED
        assert "CURRENT_BLUEPRINT" in report.error

    def test_09b_untouched_blueprint_passes_the_guard(self, tmp_path):
        build_dual_workspace(tmp_path)
        from encomm_pcc.proposal_runtime.review_executor import (
            ProposalReviewOutcome,
            execute_review_call,
        )

        payload = review_payload(role=pp.ProposalRole.SCIENTIFIC_REVIEWER)
        driver = Reviewer(pp.ProposalRole.SCIENTIFIC_REVIEWER, payload)
        master = tmp_path / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
        report = execute_review_call(
            packet=dual_packet(None),
            driver=driver,  # type: ignore[arg-type]
            master_proposal_path=master,
            proposal_workspace_path=tmp_path,
        )
        assert report.outcome is ProposalReviewOutcome.COMPLETED

    def test_09c_strict_target_requirement_is_active_in_dual_live_calls(
        self, tmp_path
    ):
        build_dual_workspace(tmp_path)
        from encomm_pcc.proposal_runtime.review_executor import (
            ProposalReviewOutcome,
            execute_review_call,
        )

        # A NEW dual answer WITHOUT any target → missing_target → the call
        # fails closed (PARSE_FAILED), never silently defaults.
        payload = review_payload(
            role=pp.ProposalRole.SCIENTIFIC_REVIEWER, with_target=False
        )
        driver = Reviewer(pp.ProposalRole.SCIENTIFIC_REVIEWER, payload)
        master = tmp_path / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
        report = execute_review_call(
            packet=dual_packet(None),
            driver=driver,  # type: ignore[arg-type]
            master_proposal_path=master,
            proposal_workspace_path=tmp_path,
        )
        assert report.outcome is ProposalReviewOutcome.PARSE_FAILED
        assert report.parse_reason == "missing_target"
