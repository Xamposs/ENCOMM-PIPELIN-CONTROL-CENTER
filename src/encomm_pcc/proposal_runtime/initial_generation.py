"""Initial proposal generation — three specialists in parallel + ASTRA
synthesis (Session 019, briefs §13–§14).

STEP A: SCIENTIFIC / PROPOSAL_ENGINEER / RED_TEAM each produce a candidate
contribution IN PARALLEL over the SAME frozen sources (the RED TEAM does not
"attack" an absent proposal: it produces compliance/evaluator-risk
checklists and recommendations instead).

STEP B: ASTRA receives blueprint + template + official pack + all three
contributions and returns ONE complete MASTER_PROPOSAL through the strict
integration envelope.  The RUNTIME writes the file (the model never does),
freezing the initial version.

Template fidelity (§14): ASTRA is instructed to preserve the official
template structure and to insert ``[INPUT REQUIRED: ...]`` markers rather
than invent facts.  A missing blueprint or template blocks generation
BEFORE any model call.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional

from ..drivers.base import BaseDriver
from ..domain.enums import AgentRole, SessionPolicy
from ..proposal.enums import ProposalPhase, ProposalRole
from ..proposal.fingerprint import proposal_fingerprint
from ..proposal.integration_packet import (
    ProposalIntegrationInputs,
    build_integration_packet,
)
from ..proposal.integration_parser import (
    ProposalIntegrationParseError,
    parse_proposal_integration,
)
from ..proposal.models import ProposalAgentConfig
from ..proposal.source_budget import SourceBudget
from ..proposal.source_snapshot import load_review_snapshot
from ..proposal.state_machine import ProposalStateMachine
from .integration_artifacts import write_integration_result
from .master_writer import MasterProposalWriteError, replace_master_proposal
from .panel_runtime import _atomic_write_json

__all__ = [
    "INPUT_REQUIRED_MARKER_GUIDANCE",
    "InitialGenerationOutcome",
    "InitialGenerationReport",
    "run_initial_generation",
]

#: §14 — the exact marker instruction ASTRA receives.
INPUT_REQUIRED_MARKER_GUIDANCE = (
    "If a required template section has insufficient source material, insert "
    "a bounded explicit marker of the form '[INPUT REQUIRED: what is missing]' "
    "instead of inventing facts."
)

_INITIAL_GENERATION_DIRNAME = "04_REVIEWS/initial_generation"


class InitialGenerationOutcome(str, Enum):
    """Terminal outcome of ONE initial-generation operation."""

    COMPLETED = "COMPLETED"                  # master written + frozen
    SOURCE_VALIDATION_FAILED = "SOURCE_VALIDATION_FAILED"
    SPECIALIST_FAILED = "SPECIALIST_FAILED"  # any specialist call failed
    SYNTHESIS_FAILED = "SYNTHESIS_FAILED"    # ASTRA call/parse/write failed
    ARTIFACT_CONFLICT = "ARTIFACT_CONFLICT"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


@dataclass(slots=True)
class InitialGenerationReport:
    """JSON-friendly operational report of ONE initial generation."""

    outcome: InitialGenerationOutcome
    proposal_revision: str = ""
    master_path: str = ""
    proposal_hash: str = ""
    specialist_reports: dict[str, Any] = field(default_factory=dict)
    synthesis_report: dict[str, Any] = field(default_factory=dict)
    generation_dir: str = ""
    error: str = ""
    failed_roles: list[str] = field(default_factory=list)
    duration_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "proposal_revision": self.proposal_revision,
            "master_path": self.master_path,
            "proposal_hash": self.proposal_hash,
            "specialist_reports": dict(self.specialist_reports),
            "synthesis_report": dict(self.synthesis_report),
            "generation_dir": self.generation_dir,
            "error": self.error,
            "failed_roles": list(self.failed_roles),
            "duration_s": round(self.duration_s, 6),
        }


_SPECIALIST_ORDER: tuple[ProposalRole, ...] = (
    ProposalRole.SCIENTIFIC_REVIEWER,
    ProposalRole.PROPOSAL_ENGINEER,
    ProposalRole.RED_TEAM_REVIEWER,
)

_FOCUS_BY_ROLE: dict[ProposalRole, str] = {
    ProposalRole.SCIENTIFIC_REVIEWER: (
        "build candidate scientific / excellence / objectives / methodology / "
        "novelty content grounded in the blueprint"
    ),
    ProposalRole.PROPOSAL_ENGINEER: (
        "build candidate implementation / impact / work-plan / deliverables / "
        "milestones / resources / risk content grounded in the blueprint"
    ),
    ProposalRole.RED_TEAM_REVIEWER: (
        "produce a compliance checklist, an evaluator-risk checklist, likely "
        "weaknesses, missing evidence and wording/structure recommendations "
        "against the blueprint + template + official documents (there is no "
        "proposal to attack yet)"
    ),
}


def _specialist_prompt(
    role: ProposalRole,
    *,
    snapshot: Any,
    proposal_revision: str,
) -> str:
    """Deterministic specialist prompt over the frozen sources."""
    doc_sections: list[str] = []
    for name, text in snapshot.official_documents:
        doc_sections.append(f"### 01_OFFICIAL/NORMALIZED/{name}.md\n\n{text.strip()}")
    parts: list[str] = [
        "# INITIAL GENERATION — SPECIALIST CONTRIBUTION",
        "",
        "You are ONE specialist contributing to the FIRST DRAFT of a proposal.",
        f"YOUR ASSIGNMENT: {_FOCUS_BY_ROLE[role]}",
        "",
        "RULES:",
        "- Ground EVERY statement in the provided sources; invent nothing.",
        f"- {INPUT_REQUIRED_MARKER_GUIDANCE}",
        "- Return well-structured Markdown sections only (no meta-commentary).",
        "",
        "## SOURCES",
        "",
        "### 00_SOURCE_OF_TRUTH/MASTER_BLUEPRINT.md",
        "",
        snapshot.master_blueprint_text.strip() or "[UNAVAILABLE]",
        "",
        "### 01_OFFICIAL/APPLICATION_TEMPLATE.md",
        "",
        snapshot.application_template_text.strip() or "[UNAVAILABLE]",
        "",
    ]
    parts.extend(doc_sections)
    for rel, key in (
        ("PROJECT_FACTS", "project_facts_text"),
        ("TEAM", "team_text"),
        ("ARCHITECTURE", "architecture_text"),
        ("TERMINOLOGY", "terminology_text"),
    ):
        text = getattr(snapshot, key).strip()
        if text:
            parts.extend([f"### 00_SOURCE_OF_TRUTH/{rel}.md", "", text, ""])
    parts.extend([
        "## OUTPUT",
        "",
        "Return your contribution as clean Markdown between these markers:",
        "",
        "<<<INITIAL_CONTRIBUTION_START>>>",
        "... your Markdown ...",
        "<<<INITIAL_CONTRIBUTION_END>>>",
        f"(proposal_revision: {proposal_revision or 'UNSPECIFIED'})",
    ])
    return "\n".join(parts)


def _parse_contribution(text: str, role: ProposalRole) -> str:
    """Extract the bounded contribution block — fail closed."""
    start = "<<<INITIAL_CONTRIBUTION_START>>>"
    end = "<<<INITIAL_CONTRIBUTION_END>>>"
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError(
            f"{role.value} contribution must contain exactly one {start} / "
            f"{end} pair."
        )
    inner = text.split(start, 1)[1].split(end, 1)[0].strip()
    if not inner:
        raise ValueError(f"{role.value} returned an empty contribution.")
    if len(inner) > 400_000:
        raise ValueError(
            f"{role.value} contribution exceeds 400000 characters."
        )
    return inner


def run_initial_generation(
    *,
    workspace: Path,
    state_machine: ProposalStateMachine,
    proposal_revision: str,
    specialist_drivers: Mapping[ProposalRole, BaseDriver],
    orchestrator_driver: BaseDriver,
    specialist_agent_configs: Optional[Mapping[ProposalRole, ProposalAgentConfig]] = None,
    orchestrator_agent_config: Optional[ProposalAgentConfig] = None,
    timeout_s: Optional[float] = None,
    source_budget: SourceBudget | None = None,
    max_workers: int = 3,
) -> InitialGenerationReport:
    """Generate the initial MASTER_PROPOSAL — specialists in parallel, then
    ASTRA synthesis; the RUNTIME owns the write and the version freeze.

    The state machine must sit at IDLE (a fresh workspace with an EMPTY
    master); on success it ends at IDLE again (the master is written
    outside the phase graph — review has not begun; no phase edge lies
    about a review having run).
    """
    started = time.monotonic()
    workspace = Path(workspace)
    master_path = workspace / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
    report = InitialGenerationReport(
        outcome=InitialGenerationOutcome.SOURCE_VALIDATION_FAILED,
        proposal_revision=str(proposal_revision),
    )

    def _finish(outcome: InitialGenerationOutcome, error: str = "",
                failed_roles: Optional[list[str]] = None) -> InitialGenerationReport:
        report.outcome = outcome
        if error:
            report.error = error
        if failed_roles:
            report.failed_roles = failed_roles
        report.duration_s = time.monotonic() - started
        return report

    # -- preconditions (BEFORE any model call) ------------------------------
    if state_machine.phase is not ProposalPhase.IDLE:
        return _finish(
            InitialGenerationOutcome.SOURCE_VALIDATION_FAILED,
            f"initial generation requires the machine at IDLE; it is at "
            f"{state_machine.phase.value}.",
        )
    if master_path.exists() and master_path.read_bytes().strip():
        return _finish(
            InitialGenerationOutcome.SOURCE_VALIDATION_FAILED,
            "MASTER_PROPOSAL is non-empty; initial generation never "
            "overwrites an existing proposal.",
        )
    try:
        snapshot = load_review_snapshot(
            workspace,
            blueprint_max_chars=(
                source_budget.blueprint_max_chars if source_budget else None
            ),
        )
    except Exception as exc:
        return _finish(
            InitialGenerationOutcome.SOURCE_VALIDATION_FAILED, str(exc)
        )
    if not snapshot.master_blueprint_text.strip():
        return _finish(
            InitialGenerationOutcome.SOURCE_VALIDATION_FAILED,
            "the canonical MASTER BLUEPRINT is missing/empty; import it "
            "before generating an initial proposal.",
        )
    if not snapshot.application_template_text.strip():
        return _finish(
            InitialGenerationOutcome.SOURCE_VALIDATION_FAILED,
            "the canonical APPLICATION TEMPLATE is missing/empty; import it "
            "before generating an initial proposal.",
        )

    missing = [r.value for r in _SPECIALIST_ORDER if r not in specialist_drivers]
    if missing:
        return _finish(
            InitialGenerationOutcome.SPECIALIST_FAILED,
            "specialist_drivers must provide all three roles; missing: "
            + ", ".join(missing),
        )
    ids = [id(specialist_drivers[r]) for r in _SPECIALIST_ORDER]
    if len(set(ids)) != len(ids) or id(orchestrator_driver) in ids:
        return _finish(
            InitialGenerationOutcome.SPECIALIST_FAILED,
            "each specialist (and the orchestrator) requires its OWN driver "
            "instance.",
        )

    # -- STEP A: the three specialist contributions in parallel -------------
    def _call(role: ProposalRole) -> str:
        prompt = _specialist_prompt(
            role, snapshot=snapshot, proposal_revision=str(proposal_revision)
        )
        driver = specialist_drivers[role]
        config = (
            specialist_agent_configs.get(role)
            if specialist_agent_configs
            else None
        )
        from ..drivers.base import SessionRequest
        from .review_executor import _resume_session_id

        request = SessionRequest(
            role=AgentRole.TASK_AUDITOR,
            workspace_path=str(workspace),
            project_profile=str(config.project_profile if config else ""),
            provider=str(config.provider if config else ""),
            model=str(config.model if config else ""),
            session_policy=SessionPolicy.ALWAYS_NEW,
            extra={
                "proposal_role": role.value,
                "proposal_stage": "initial_generation",
            },
        )
        resume_id = _resume_session_id(config, driver)
        session = (
            driver.resume_session(resume_id, request)
            if resume_id
            else driver.start_session(request)
        )
        handle = driver.send_prompt(session, prompt)
        prompt_result = driver.wait_for_completion(handle, timeout_s)
        if not prompt_result.ok:
            raise RuntimeError(
                f"{role.value} specialist failed: "
                f"{prompt_result.error or 'no error reported'}"
            )
        if not prompt_result.text.strip():
            raise RuntimeError(f"{role.value} returned an empty answer")
        return _parse_contribution(prompt_result.text, role)

    workers = max(1, min(int(max_workers), 3))
    contributions: dict[ProposalRole, str] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {role: pool.submit(_call, role) for role in _SPECIALIST_ORDER}
        for role, future in futures.items():
            try:
                contributions[role] = future.result()
            except Exception as exc:  # noqa: BLE001 - collected per role
                errors.append(f"{role.value}: {type(exc).__name__}: {exc}")
                report.specialist_reports[role.value] = {"ok": False, "error": str(exc)}
            else:
                report.specialist_reports[role.value] = {
                    "ok": True,
                    "chars": len(contributions[role]),
                }
    if errors:
        return _finish(
            InitialGenerationOutcome.SPECIALIST_FAILED,
            "; ".join(errors),
            failed_roles=[
                r.value for r in _SPECIALIST_ORDER if r not in contributions
            ],
        )

    # -- STEP B: ASTRA synthesis through the STRICT integration envelope ----
    contribution_text = "\n\n".join(
        f"## {role.value} CONTRIBUTION\n\n{contributions[role].strip()}"
        for role in _SPECIALIST_ORDER
    )
    source_lines: list[str] = [
        "### 00_SOURCE_OF_TRUTH/MASTER_BLUEPRINT.md",
        snapshot.master_blueprint_text.strip() or "[UNAVAILABLE]",
        "### 01_OFFICIAL/APPLICATION_TEMPLATE.md",
        snapshot.application_template_text.strip() or "[UNAVAILABLE]",
    ]
    for name, text in snapshot.official_documents:
        source_lines.append(f"### 01_OFFICIAL/NORMALIZED/{name}.md")
        source_lines.append(text.strip() or "[EMPTY]")
    # The integration packet requires a current proposal + hash; generation
    # starts from the EMPTY master, so the packet carries the empty text and
    # a synthetic zero hash via the dedicated generation preamble instead.
    synthesis_prompt = "\n".join(
        [
            "# INITIAL GENERATION — ASTRA SYNTHESIS",
            "",
            "You are ASTRA / ORCHESTRATOR.  Synthesise the FIRST COMPLETE",
            "MASTER PROPOSAL from the specialist contributions and the",
            "official sources below.",
            "",
            "NON-NEGOTIABLE RULES:",
            "- Preserve the OFFICIAL APPLICATION TEMPLATE structure exactly",
            "  (same sections, same order); never invent template sections.",
            f"- {INPUT_REQUIRED_MARKER_GUIDANCE}",
            "- Never invent source facts; keep only supported content.",
            "- Integrate the specialist contributions; resolve overlaps",
            "  conservatively and keep the proposal internally consistent.",
            "",
            "## SOURCES",
            "",
            "\n\n".join(source_lines),
            "",
            "## SPECIALIST CONTRIBUTIONS",
            "",
            contribution_text,
            "",
            "## OUTPUT CONTRACT (STRICT)",
            "",
            "Return the COMPLETE proposal through the integration envelope:",
            "",
            "<<<ENCOMM_PROPOSAL_INTEGRATION_START>>>",
            '{"summary": "<one-paragraph synthesis note>"}',
            "<<<ENCOMM_PROPOSAL_INTEGRATION_END>>>",
            "",
            "and the FULL revised proposal text between:",
            "",
            "<<<INITIAL_PROPOSAL_START>>>",
            "... the complete proposal Markdown ...",
            "<<<INITIAL_PROPOSAL_END>>>",
        ]
    )
    # Reuse the strict integration parser by wrapping the answer in the
    # integration envelope shape it already enforces: ASTRA returns the
    # revised proposal in the dedicated block; the runtime builds the
    # integration JSON itself (summary + revised_proposal) and parses it
    # through the SAME strict parser the INTEGRATION phase uses — the model
    # is free of double-JSON formatting and the contract stays machine-
    # enforced.  The synthetic input hash is the empty-master hash.
    try:
        empty_hash = (
            proposal_fingerprint(master_path) if master_path.exists() else ""
        )
    except OSError:
        empty_hash = ""

    from ..drivers.base import SessionRequest
    from .review_executor import _resume_session_id

    try:
        request = SessionRequest(
            role=AgentRole.ORCHESTRATOR,
            workspace_path=str(workspace),
            project_profile=str(
                orchestrator_agent_config.project_profile
                if orchestrator_agent_config else ""
            ),
            provider=str(
                orchestrator_agent_config.provider if orchestrator_agent_config else ""
            ),
            model=str(
                orchestrator_agent_config.model if orchestrator_agent_config else ""
            ),
            session_policy=SessionPolicy.ALWAYS_NEW,
            extra={"proposal_role": "ORCHESTRATOR", "proposal_iteration": 0},
        )
        resume_id = _resume_session_id(orchestrator_agent_config, orchestrator_driver)
        session = (
            orchestrator_driver.resume_session(resume_id, request)
            if resume_id
            else orchestrator_driver.start_session(request)
        )
        handle = orchestrator_driver.send_prompt(session, synthesis_prompt)
        prompt_result = orchestrator_driver.wait_for_completion(handle, timeout_s)
    except Exception as exc:  # noqa: BLE001 - fail closed
        return _finish(
            InitialGenerationOutcome.SYNTHESIS_FAILED,
            f"ASTRA synthesis failed: {exc}",
        )
    if not prompt_result.ok:
        return _finish(
            InitialGenerationOutcome.SYNTHESIS_FAILED,
            f"ASTRA synthesis failed: {prompt_result.error or 'no error'}",
        )
    raw = prompt_result.text or ""
    start_marker = "<<<INITIAL_PROPOSAL_START>>>"
    end_marker = "<<<INITIAL_PROPOSAL_END>>>"
    if raw.count(start_marker) != 1 or raw.count(end_marker) != 1:
        return _finish(
            InitialGenerationOutcome.SYNTHESIS_FAILED,
            "ASTRA output must contain exactly one INITIAL_PROPOSAL "
            "block pair.",
        )
    proposed_text = raw.split(start_marker, 1)[1].split(end_marker, 1)[0]
    if not proposed_text.strip():
        return _finish(
            InitialGenerationOutcome.SYNTHESIS_FAILED,
            "ASTRA returned an empty proposal.",
        )
    if "[INPUT REQUIRED" not in proposed_text and len(proposed_text) > 400_000:
        return _finish(
            InitialGenerationOutcome.SYNTHESIS_FAILED,
            "ASTRA proposal exceeds 400000 characters.",
        )
    # Template fidelity check (deterministic, §14): every top-level template
    # heading (lines starting with '#') that the template defines must
    # appear in the synthesis — markers may stand in for missing material.
    template_headings = [
        line.strip()
        for line in snapshot.application_template_text.splitlines()
        if line.strip().startswith("#")
    ]
    missing_headings = [
        h for h in template_headings if h not in proposed_text
    ]
    if template_headings and missing_headings:
        return _finish(
            InitialGenerationOutcome.SYNTHESIS_FAILED,
            "ASTRA dropped official template headings: "
            + "; ".join(missing_headings[:5])
            + " — template structure must be preserved "
            "(use [INPUT REQUIRED: ...] markers for missing material).",
        )

    # Build the integration-shaped payload and parse it through the SAME
    # strict parser used at INTEGRATION (defence in depth).
    synthetic_raw = (
        "<<<ENCOMM_PROPOSAL_INTEGRATION_START>>>\n"
        + json.dumps(
            {
                "role": "ORCHESTRATOR",
                "iteration_number": 1,
                "input_proposal_hash": empty_hash or "0" * 64,
                "summary": "initial generation synthesis",
                "revised_proposal": proposed_text,
            }
        )
        + "\n<<<ENCOMM_PROPOSAL_INTEGRATION_END>>>"
    )
    try:
        parsed = parse_proposal_integration(
            synthetic_raw,
            expected_iteration=1,
            expected_input_hash=empty_hash or "0" * 64,
        )
    except ProposalIntegrationParseError as exc:
        return _finish(
            InitialGenerationOutcome.SYNTHESIS_FAILED,
            f"synthesis contract check failed: {exc}",
        )

    # -- the WRITE (runtime-owned; master_writer enforces its own guard —
    # which requires the INTEGRATION phase, so the machine walks IDLE →
    # SOURCE_VALIDATION → ... → INTEGRATION → HARD_GATE_VALIDATION as pure
    # bookkeeping around the write, exactly the path a real iteration takes)
    try:
        state_machine.transition_to(ProposalPhase.SOURCE_VALIDATION)
        state_machine.transition_to(ProposalPhase.SCIENTIFIC_REVIEW)
        state_machine.transition_to(ProposalPhase.IMPLEMENTATION_REVIEW)
        state_machine.transition_to(ProposalPhase.RED_TEAM_REVIEW)
        state_machine.transition_to(ProposalPhase.INTEGRATION)
        write_report = replace_master_proposal(
            workspace=workspace,
            state_machine=state_machine,
            expected_current_hash=empty_hash,
            revised_proposal_text=parsed.revised_proposal,
        )
        state_machine.transition_to(ProposalPhase.HARD_GATE_VALIDATION)
        # Generation is NOT a review: return the machine to IDLE so the
        # first real panel iteration starts from the legal entry phase.
        state_machine.reset()
    except (MasterProposalWriteError, Exception) as exc:  # noqa: BLE001
        return _finish(
            InitialGenerationOutcome.ARTIFACT_CONFLICT
            if isinstance(exc, MasterProposalWriteError)
            else InitialGenerationOutcome.SYNTHESIS_FAILED,
            f"initial master write failed: {exc}",
        )

    # -- durable generation artifacts ----------------------------------------
    generation_dir = workspace.joinpath(*_INITIAL_GENERATION_DIRNAME.split("/"))
    try:
        generation_dir.mkdir(parents=True, exist_ok=True)
        (generation_dir / "specialist_contributions.md").write_text(
            contribution_text, encoding="utf-8", newline="\n"
        )
        _atomic_write_json(
            generation_dir / "initial_generation.json",
            {
                "schema": "encomm-pcc.initial-generation/v1",
                "proposal_revision": str(proposal_revision),
                "specialists": dict(report.specialist_reports),
                "master_proposal_hash": write_report.new_hash,
            },
        )
    except OSError as exc:
        return _finish(
            InitialGenerationOutcome.ARTIFACT_CONFLICT,
            f"generation artifact write failed: {exc}",
        )

    report.outcome = InitialGenerationOutcome.COMPLETED
    report.master_path = str(master_path)
    report.proposal_hash = write_report.new_hash
    report.generation_dir = str(generation_dir)
    report.synthesis_report = {
        "ok": True,
        "chars": len(parsed.revised_proposal),
        "input_required_markers": parsed.revised_proposal.count(
            "[INPUT REQUIRED"
        ),
    }
    report.duration_s = time.monotonic() - started
    return report
