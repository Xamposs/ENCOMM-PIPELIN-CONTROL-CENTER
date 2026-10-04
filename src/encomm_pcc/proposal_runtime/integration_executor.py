"""ORCHESTRATOR integration executor — the REAL INTEGRATION phase.

Executes ONE integration operation over the EXACT master-proposal revision
the review brief froze::

    INTEGRATION (exact entry phase)
        → guards (state / brief presence / brief iteration / brief hash)
        → zero-AI clean-PASS bypass  OR  ORCHESTRATOR packet → driver
        → strict fail-closed parse
        → re-fingerprint BEFORE any write (mutation ⇒ MUTATION_DETECTED)
        → atomic MASTER_PROPOSAL replacement (runtime-owned; the model
          NEVER touches the file — it returns the revised content through
          the strict envelope)
        → post-integration version freeze + durable integration artifact
        → HARD_GATE_VALIDATION

Failure semantics (fail closed, §17): a driver or parse failure, a stale
input hash or an artifact conflict NEVER advances the state machine — the
machine remains at INTEGRATION and the run can be retried/repaired.  The
ONE honest exception is a failure AFTER the atomic master write succeeded
(e.g. the post-integration freeze conflicts): the proposal IS the new
revision on disk, so the executor surfaces the exact partial-success
condition (``state_advanced=True``, new hash reported, outcome
``ARTIFACT_CONFLICT``) and the machine legitimately walks to
HARD_GATE_VALIDATION — it never pretends a rollback happened, and it never
pretends the write failed.

Clean-pass bypass (§12): when the brief says ``integration_required=false``,
the executor verifies the brief belongs to the current exact hash AND that
the aggregate verdict is a truly clean PASS (no findings, no patches, no
unverified claims), makes ZERO driver calls, writes a no-op
``integration_result.json``, leaves MASTER_PROPOSAL byte-identical
(output hash == input hash) and walks INTEGRATION → HARD_GATE_VALIDATION.
Any ``integration_required=false`` brief that nevertheless carries
actionable items fails closed BEFORE the bypass.

Review freshness (§13) is deliberately NOT advanced here: entering
HARD_GATE_VALIDATION is where the caller checks
``evaluate_review_freshness()`` — a changed proposal can never COMPLETE
(``proposal_iteration`` owns that composition).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from ..drivers.base import BaseDriver
from ..domain.enums import AgentRole, SessionPolicy
from ..proposal.document_pair import (
    DocumentPairStateError,
    commit_document_pair,
)
from ..proposal.enums import ProposalPhase
from ..proposal.fingerprint import proposal_fingerprint
from ..proposal.integration_models import ProposalIntegrationResult
from ..proposal.models import ProposalAgentConfig
from ..proposal.integration_packet import (
    MAX_INTEGRATION_PACKET_SECTION_CHARS,
    ProposalIntegrationInputs,
    build_integration_packet,
)
from ..proposal.integration_parser import (
    ProposalIntegrationParseError,
    parse_proposal_integration,
)
from ..proposal.living_blueprint import CURRENT_BLUEPRINT_RELPATH
from ..proposal.review_aggregation import INTEGRATION_BRIEF_SCHEMA
from ..proposal.source_snapshot import load_review_snapshot
from ..proposal.state_machine import ProposalStateMachine
from .integration_artifacts import (
    build_integration_result_payload,
    integration_result_path,
    write_integration_result,
)
from .master_writer import (
    MasterProposalWriteError,
    MasterProposalWriteReport,
    replace_master_proposal,
)
from .review_artifacts import ArtifactConflictError, BRIEF_FILENAME
from .version_freeze_post import (
    freeze_post_integration_blueprint_version,
    freeze_post_integration_version,
)

__all__ = [
    "INTEGRATION_BRIEF_SCHEMA",
    "ProposalIntegrationExecutionReport",
    "ProposalIntegrationOutcome",
    "run_integration",
]

class ProposalIntegrationOutcome(str, Enum):
    """Terminal outcome of ONE integration operation (brief §17)."""

    COMPLETED_CHANGED = "COMPLETED_CHANGED"      # master replaced, hash differs
    COMPLETED_NO_CHANGE = "COMPLETED_NO_CHANGE"  # orchestrator returned identical text
    NO_INTEGRATION_REQUIRED = "NO_INTEGRATION_REQUIRED"  # zero-AI clean-PASS path
    DRIVER_FAILED = "DRIVER_FAILED"              # driver call failed; stays INTEGRATION
    PARSE_FAILED = "PARSE_FAILED"                # strict parse refused; stays INTEGRATION
    STALE_INPUT = "STALE_INPUT"                  # hash mismatch before/after; stays INTEGRATION
    MUTATION_DETECTED = "MUTATION_DETECTED"      # file changed during the driver call
    ARTIFACT_CONFLICT = "ARTIFACT_CONFLICT"      # durable evidence conflicts
    WRITE_FAILED = "WRITE_FAILED"                # atomic replacement failed; stays INTEGRATION

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


@dataclass(slots=True)
class ProposalIntegrationExecutionReport:
    """JSON-friendly operational report of ONE integration operation."""

    outcome: ProposalIntegrationOutcome
    iteration_number: int
    input_proposal_hash: str = ""
    output_proposal_hash: str = ""
    #: Session 021: the committed living-Blueprint hash (dual path only).
    output_blueprint_hash: str = ""
    #: Session 021: the deterministic pair revision id (dual path only).
    pair_revision_id: str = ""
    proposal_revision: str = ""
    state_before: ProposalPhase = ProposalPhase.INTEGRATION
    state_after: ProposalPhase = ProposalPhase.INTEGRATION
    state_advanced: bool = False
    brief_path: str = ""
    integration_result_path: str = ""
    post_integration_version_path: str = ""
    next_iteration_handoff_path: str = ""
    session_id: Optional[str] = None
    driver_id: str = ""
    duration_s: float = 0.0
    changed: bool = False
    parsed_result: Optional[ProposalIntegrationResult] = None
    error: str = ""
    parse_reason: str = ""
    raw_excerpt: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome in (
            ProposalIntegrationOutcome.COMPLETED_CHANGED,
            ProposalIntegrationOutcome.COMPLETED_NO_CHANGE,
            ProposalIntegrationOutcome.NO_INTEGRATION_REQUIRED,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "iteration_number": self.iteration_number,
            "input_proposal_hash": self.input_proposal_hash,
            "output_proposal_hash": self.output_proposal_hash,
            "output_blueprint_hash": self.output_blueprint_hash,
            "pair_revision_id": self.pair_revision_id,
            "proposal_revision": self.proposal_revision,
            "state_before": self.state_before.value,
            "state_after": self.state_after.value,
            "state_advanced": self.state_advanced,
            "brief_path": self.brief_path,
            "integration_result_path": self.integration_result_path,
            "post_integration_version_path": self.post_integration_version_path,
            "next_iteration_handoff_path": self.next_iteration_handoff_path,
            "session_id": self.session_id,
            "driver_id": self.driver_id,
            "duration_s": round(self.duration_s, 6),
            "changed": self.changed,
            "parsed_result": (
                self.parsed_result.to_dict()
                if self.parsed_result is not None
                else None
            ),
            "error": self.error,
            "parse_reason": self.parse_reason,
            "raw_excerpt": self.raw_excerpt,
        }


def _load_brief(brief_path: Path) -> dict[str, Any]:
    """Read and structurally validate the authoritative integration brief."""
    try:
        raw = brief_path.read_bytes()
    except OSError as exc:
        raise RuntimeError(
            f"integration brief cannot be read: {brief_path} ({exc})"
        ) from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"integration brief is not valid JSON: {brief_path} ({exc})"
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeError(
            f"integration brief is not a JSON object: {brief_path}"
        )
    if data.get("schema") != INTEGRATION_BRIEF_SCHEMA:
        raise RuntimeError(
            f"integration brief schema mismatch: expected "
            f"{INTEGRATION_BRIEF_SCHEMA}, got {data.get('schema')!r}."
        )
    return data


def _brief_actionable(brief: dict[str, Any]) -> bool:
    """True when the brief carries ANY actionable item."""
    return bool(
        brief.get("findings") or brief.get("proposed_patches")
        or brief.get("unverified_claims")
    )


def _render_previous_findings(
    previous_findings: list[dict[str, Any]] | None,
) -> str:
    """Deterministic bounded canonical-JSON rendering of previous findings.

    ``None`` and ``[]`` render as the empty string, so the packet renders
    its existing explicit UNAVAILABLE marker for the section.  Rendering is
    deterministic (``sort_keys``, fixed indent, ``ensure_ascii=False``) and
    carries NOTHING but the records themselves: no timestamps, no
    provider/model metadata, no raw transcripts.  The input list is never
    mutated.  Only ``None`` or a ``list`` is accepted (the public type
    contract); anything else fails closed.  An oversized rendering fails
    CLOSED here — before packet construction and any driver contact — and
    is never truncated, partially serialised or silently dropped.
    """
    if previous_findings is None:
        return ""
    if not isinstance(previous_findings, list):
        raise ValueError(
            "previous_findings must be a list of finding records or None; "
            f"got {type(previous_findings).__name__}."
        )
    if not previous_findings:
        return ""
    rendered = json.dumps(
        previous_findings,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )
    if len(rendered) > MAX_INTEGRATION_PACKET_SECTION_CHARS:
        raise ValueError(
            "rendered previous findings exceed "
            f"{MAX_INTEGRATION_PACKET_SECTION_CHARS} characters "
            f"({len(rendered)}); refusing to embed unbounded input."
        )
    return rendered


def run_integration(
    *,
    workspace: Path,
    state_machine: ProposalStateMachine,
    iteration_number: int,
    proposal_revision: str,
    orchestrator_driver: Optional[BaseDriver],
    session_policy: SessionPolicy = SessionPolicy.ALWAYS_NEW,
    timeout_s: Optional[float] = None,
    previous_findings: list[dict[str, Any]] | None = None,
    pre_review_version_path: str = "",
    orchestrator_agent_config: Optional[ProposalAgentConfig] = None,
    extra_instructions: str = "",
    source_pack_id: str = "",
) -> ProposalIntegrationExecutionReport:
    """Run ONE INTEGRATION operation — fail closed throughout.

    Parameters mirror ``run_review_cycle``: the workspace root, the proposal
    state machine (which MUST sit exactly at INTEGRATION), the iteration
    number and revision, the injected ORCHESTRATOR ``BaseDriver`` (ignored —
    and REQUIRED to be ``None``-tolerant — on the zero-AI clean-pass path)
    and the optional session policy/timeout forwarded to the driver.

    ``previous_findings`` (the earlier iterations' finding records) is
    rendered deterministically into the packet's existing PREVIOUS FINDINGS
    section — bounded and fail-closed BEFORE any driver contact.

    Session 017A: ``orchestrator_agent_config`` optionally supplies the
    ORCHESTRATOR :class:`~encomm_pcc.proposal.ProposalAgentConfig`; when
    present, its ``project_profile`` / ``provider`` / ``model`` reach the
    driver's ``SessionRequest`` and the request's ``workspace_path`` is the
    ACTUAL proposal workspace.  Omitted (offline/unit callers) keeps the
    previous legacy request shape.

    Session 019: ``extra_instructions`` (optional, bounded by the packet's
    own section cap) is appended to the packet as an explicit PANEL CHAIR
    CONTEXT section — the parallel panel's consensus matrix and advisory
    readiness reach ASTRA through this seam WITHOUT changing the strict
    integration parser contract.  Empty by default (byte-identical packets
    for existing callers).

    Session 021B: ``source_pack_id`` (optional) is the REAL source-pack
    identity the panel/chair operated on; on the dual write path it is
    PRESERVED into the committed ``DOCUMENT_PAIR_STATE`` manifest.  Empty
    (legacy/standalone callers) keeps the previous manifest shape.
    """
    started = time.monotonic()
    workspace = Path(workspace)
    master_path = workspace / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
    brief_path = (
        workspace / "04_REVIEWS" / f"iteration_{int(iteration_number):03d}"
        / BRIEF_FILENAME
    )
    state_before = state_machine.phase
    report = ProposalIntegrationExecutionReport(
        outcome=ProposalIntegrationOutcome.DRIVER_FAILED,
        iteration_number=iteration_number,
        proposal_revision=str(proposal_revision),
        state_before=state_before,
        brief_path=str(brief_path),
    )

    def _fail(
        outcome: ProposalIntegrationOutcome,
        error: str,
        *,
        parse_reason: str = "",
        raw_excerpt: str = "",
        session_id: str | None = None,
        driver_id: str = "",
    ) -> ProposalIntegrationExecutionReport:
        report.outcome = outcome
        report.error = error
        report.parse_reason = parse_reason
        report.raw_excerpt = raw_excerpt
        report.session_id = session_id
        report.driver_id = driver_id
        report.state_after = state_machine.phase
        report.duration_s = time.monotonic() - started
        return report

    # -- 1. state guard (BEFORE anything else) -----------------------------
    if state_before is not ProposalPhase.INTEGRATION:
        return _fail(
            ProposalIntegrationOutcome.DRIVER_FAILED,
            f"run_integration requires the state machine to be exactly at "
            f"INTEGRATION; it is at {state_before.value}.",
        )

    # -- 2. input hash (BEFORE any driver contact) --------------------------
    try:
        input_hash = proposal_fingerprint(master_path)
    except OSError as exc:
        return _fail(ProposalIntegrationOutcome.STALE_INPUT, str(exc))
    report.input_proposal_hash = input_hash

    # -- 3. the authoritative integration brief -----------------------------
    if not brief_path.is_file():
        return _fail(
            ProposalIntegrationOutcome.ARTIFACT_CONFLICT,
            f"the authoritative integration brief is missing: {brief_path}",
        )
    try:
        brief = _load_brief(brief_path)
    except RuntimeError as exc:
        return _fail(ProposalIntegrationOutcome.ARTIFACT_CONFLICT, str(exc))
    if brief.get("iteration_number") != iteration_number:
        return _fail(
            ProposalIntegrationOutcome.STALE_INPUT,
            f"integration brief iteration {brief.get('iteration_number')!r} "
            f"!= requested iteration {iteration_number}.",
        )
    brief_hash = str(brief.get("proposal_hash") or "")
    if brief_hash.lower() != input_hash:
        return _fail(
            ProposalIntegrationOutcome.STALE_INPUT,
            f"integration brief proposal_hash {brief_hash!r} != current "
            f"MASTER_PROPOSAL hash {input_hash}; refusing to integrate a "
            "stale revision.",
        )

    # -- 4. clean-PASS zero-AI bypass (§12) ---------------------------------
    if brief.get("integration_required") is False:
        verdict_raw = str(brief.get("aggregate_verdict") or "")
        if verdict_raw != "PASS" or _brief_actionable(brief):
            return _fail(
                ProposalIntegrationOutcome.ARTIFACT_CONFLICT,
                "integration brief claims integration_required=false but is "
                f"not a truly clean PASS (verdict={verdict_raw!r}, "
                "actionable items present); failing closed.",
            )
        # Deterministic no-op integration: zero driver calls, master stays
        # byte-identical, a no-op artifact is recorded, the machine walks
        # INTEGRATION → HARD_GATE_VALIDATION.
        payload = build_integration_result_payload(
            iteration_number=iteration_number,
            proposal_revision=str(proposal_revision),
            input_hash=input_hash,
            output_hash=input_hash,
            result=ProposalIntegrationResult(
                iteration_number=iteration_number,
                input_proposal_hash=input_hash,
                revised_proposal="",
                summary="clean PASS — zero-AI integration bypass",
            ),
            runtime_outcome=ProposalIntegrationOutcome.NO_INTEGRATION_REQUIRED.value,
        )
        try:
            artifact_path = write_integration_result(
                workspace=workspace, payload=payload
            )
        except ArtifactConflictError as exc:
            return _fail(ProposalIntegrationOutcome.ARTIFACT_CONFLICT, str(exc))
        state_machine.transition_to(ProposalPhase.HARD_GATE_VALIDATION)
        report.outcome = ProposalIntegrationOutcome.NO_INTEGRATION_REQUIRED
        report.output_proposal_hash = input_hash
        report.changed = False
        report.integration_result_path = str(artifact_path)
        report.state_advanced = True
        report.state_after = state_machine.phase
        report.duration_s = time.monotonic() - started
        return report

    # -- 5. ORCHESTRATOR packet over the FROZEN inputs -----------------------
    try:
        snapshot = load_review_snapshot(workspace)
    except Exception as exc:
        return _fail(
            ProposalIntegrationOutcome.STALE_INPUT,
            f"bounded source snapshot could not be loaded: {exc}",
        )
    # Session 021: the DUAL-DOCUMENT contract activates whenever the living
    # Blueprint exists and is readable — the chair then gets BOTH documents
    # and must consciously evaluate both every round.  A missing/corrupt
    # living Blueprint degrades safely to the legacy single-document path.
    current_bp_path = workspace.joinpath(*CURRENT_BLUEPRINT_RELPATH.split("/"))
    dual = False
    current_bp_hash = ""
    if current_bp_path.is_file() and snapshot.current_blueprint_available:
        try:
            current_bp_hash = proposal_fingerprint(current_bp_path)
            dual = True
        except OSError:
            dual = False
            current_bp_hash = ""
    brief_text = json.dumps(brief, indent=2, sort_keys=True, ensure_ascii=False)
    try:
        previous_findings_text = _render_previous_findings(previous_findings)
    except (TypeError, ValueError) as exc:
        return _fail(
            ProposalIntegrationOutcome.DRIVER_FAILED,
            f"previous findings cannot be rendered for the integration "
            f"packet: {exc}",
        )
    if extra_instructions and extra_instructions.strip():
        # Session 019: the panel-chair context (consensus matrix + advisory
        # readiness) is an explicit, labelled packet section.  The packet's
        # own section cap still bounds it (fail closed, never truncated).
        panel_section = (
            "PANEL CHAIR CONTEXT — structured consensus matrix and advisory "
            "readiness from the parallel evaluator panel.  Address every "
            "unresolved item explicitly.\n\n" + extra_instructions.strip()
        )
        if len(panel_section) > MAX_INTEGRATION_PACKET_SECTION_CHARS:
            return _fail(
                ProposalIntegrationOutcome.DRIVER_FAILED,
                f"panel-chair context exceeds "
                f"{MAX_INTEGRATION_PACKET_SECTION_CHARS} characters; "
                "refusing to embed unbounded input.",
            )
        previous_findings_text = (
            previous_findings_text + "\n\n" + panel_section
            if previous_findings_text.strip()
            else panel_section
        )
    source_lines: list[str] = []
    source_lines.append("### 00_SOURCE_OF_TRUTH/MASTER_BLUEPRINT.md")
    source_lines.append(
        snapshot.master_blueprint_text.strip() or "[UNAVAILABLE]"
    )
    source_lines.append("### 00_SOURCE_OF_TRUTH/CURRENT_BLUEPRINT.md (LIVING project design)")
    source_lines.append(
        snapshot.current_blueprint_text.strip() or "[UNAVAILABLE]"
    )
    source_lines.append("### 00_SOURCE_OF_TRUTH/PROJECT_FACTS.md")
    source_lines.append(snapshot.project_facts_text.strip() or "[UNAVAILABLE]")
    source_lines.append("### 00_SOURCE_OF_TRUTH/TEAM.md")
    source_lines.append(snapshot.team_text.strip() or "[UNAVAILABLE]")
    source_lines.append("### 00_SOURCE_OF_TRUTH/ARCHITECTURE.md")
    source_lines.append(snapshot.architecture_text.strip() or "[UNAVAILABLE]")
    source_lines.append("### 00_SOURCE_OF_TRUTH/TERMINOLOGY.md")
    source_lines.append(snapshot.terminology_text.strip() or "[UNAVAILABLE]")
    source_snapshot_text = "\n\n".join(source_lines)

    try:
        packet = build_integration_packet(
            ProposalIntegrationInputs(
                current_proposal_text=snapshot.master_proposal_text,
                current_proposal_hash=input_hash,
                iteration_number=iteration_number,
                proposal_revision=str(proposal_revision),
                integration_brief_text=brief_text,
                source_snapshot_text=source_snapshot_text,
                official_requirements_text=snapshot.official_requirements_text,
                official_requirements_available=(
                    snapshot.official_requirements_available
                ),
                previous_findings_text=previous_findings_text,
                current_blueprint_text=snapshot.current_blueprint_text,
                current_blueprint_hash=current_bp_hash,
                current_blueprint_available=dual,
            )
        )
    except ValueError as exc:
        return _fail(ProposalIntegrationOutcome.DRIVER_FAILED, str(exc))

    if orchestrator_driver is None:
        return _fail(
            ProposalIntegrationOutcome.DRIVER_FAILED,
            "an ORCHESTRATOR driver is required when the brief demands "
            "integration.",
        )

    # -- 6. driver call ------------------------------------------------------
    session = None
    try:
        from ..drivers.base import SessionRequest

        # Session 017A: the ORCHESTRATOR config (when supplied) is
        # authoritative for profile/provider/model; the workspace is the
        # ACTUAL proposal workspace — never empty in production.  No engine
        # behaviour is hardcoded here: the drivers keep interpreting
        # SessionRequest.
        if orchestrator_agent_config is not None:
            effort = str(orchestrator_agent_config.reasoning_effort or "").strip()
            extra = {
                "proposal_role": "ORCHESTRATOR",
                "proposal_iteration": iteration_number,
            }
            if effort:
                extra["reasoning_effort"] = effort
            request = SessionRequest(
                role=_ORCHESTRATOR_AGENT_ROLE,
                workspace_path=str(workspace),
                project_profile=str(orchestrator_agent_config.project_profile or ""),
                provider=str(orchestrator_agent_config.provider or ""),
                model=str(orchestrator_agent_config.model or ""),
                session_policy=session_policy,
                extra=extra,
            )
        else:
            request = SessionRequest(
                role=_ORCHESTRATOR_AGENT_ROLE,
                workspace_path=str(workspace),
                session_policy=session_policy,
                extra={
                    "proposal_role": "ORCHESTRATOR",
                    "proposal_iteration": iteration_number,
                },
            )
        # Session 021A: the PERSISTENT CHAIR contract — the operator's
        # explicit RESUME_SELECTED_SESSION choice is honoured EXACTLY like
        # every other proposal runtime path (the proven _resume_session_id
        # contract): never silently fall back to a fresh session.
        from .review_executor import _resume_session_id

        resume_id = _resume_session_id(orchestrator_agent_config, orchestrator_driver)
        if resume_id:
            session = orchestrator_driver.resume_session(resume_id, request)
        else:
            session = orchestrator_driver.start_session(request)
        handle = orchestrator_driver.send_prompt(session, packet.prompt_text)
        prompt_result = orchestrator_driver.wait_for_completion(handle, timeout_s)
    except Exception as exc:  # DriverError hierarchy + defensive plain errors
        return _fail(
            ProposalIntegrationOutcome.DRIVER_FAILED,
            f"driver error: {exc}",
            driver_id=getattr(orchestrator_driver, "driver_id", ""),
            session_id=(session.session_id if session is not None else None),
        )

    driver_id = getattr(orchestrator_driver, "driver_id", "")
    session_id = getattr(prompt_result, "session_id", None) or (
        session.session_id if session is not None else None
    )
    if not getattr(prompt_result, "ok", False):
        return _fail(
            ProposalIntegrationOutcome.DRIVER_FAILED,
            getattr(prompt_result, "error", None)
            or "driver reported failure without an error",
            driver_id=driver_id,
            session_id=session_id,
        )
    raw_text = getattr(prompt_result, "text", "") or ""
    if not raw_text.strip():
        return _fail(
            ProposalIntegrationOutcome.DRIVER_FAILED,
            "driver returned an empty answer",
            driver_id=driver_id,
            session_id=session_id,
        )

    # -- 7. strict parse (fail closed) ---------------------------------------
    try:
        parsed = parse_proposal_integration(
            raw_text,
            expected_iteration=iteration_number,
            expected_input_hash=input_hash,
            expected_input_blueprint_hash=current_bp_hash,
        )
    except ProposalIntegrationParseError as exc:
        return _fail(
            ProposalIntegrationOutcome.PARSE_FAILED,
            str(exc),
            parse_reason=exc.reason,
            raw_excerpt=raw_text[:4000],
            driver_id=driver_id,
            session_id=session_id,
        )
    report.parsed_result = parsed

    # -- 8. re-fingerprint BEFORE any write (mutation guard, §8) -------------
    try:
        before_write_hash = proposal_fingerprint(master_path)
    except OSError as exc:
        return _fail(
            ProposalIntegrationOutcome.MUTATION_DETECTED,
            f"pre-write fingerprint failed: {exc}",
            driver_id=driver_id,
            session_id=session_id,
        )
    if before_write_hash != input_hash:
        return _fail(
            ProposalIntegrationOutcome.MUTATION_DETECTED,
            f"MASTER_PROPOSAL changed during the ORCHESTRATOR call "
            f"({input_hash} -> {before_write_hash}); the parsed output is "
            "REFUSED and the externally modified file is never overwritten.",
            driver_id=driver_id,
            session_id=session_id,
        )
    if dual:
        # Session 021: the LIVING Blueprint must ALSO be unchanged during
        # the chair call — the same write-authority guard as the master.
        try:
            bp_before_write_hash = proposal_fingerprint(current_bp_path)
        except OSError as exc:
            return _fail(
                ProposalIntegrationOutcome.MUTATION_DETECTED,
                f"pre-write CURRENT_BLUEPRINT fingerprint failed: {exc}",
                driver_id=driver_id,
                session_id=session_id,
            )
        if bp_before_write_hash != current_bp_hash:
            return _fail(
                ProposalIntegrationOutcome.MUTATION_DETECTED,
                f"CURRENT_BLUEPRINT changed during the ORCHESTRATOR call "
                f"({current_bp_hash} -> {bp_before_write_hash}); the parsed "
                "output is REFUSED.",
                driver_id=driver_id,
                session_id=session_id,
            )

    # -- 9. runtime-owned write (the model NEVER touches files) --------------
    if dual:
        # Session 021A core invariant: a DUAL workspace ALWAYS commits the
        # document pair.  "No Blueprint change" is the chair's explicit
        # decision — the CURRENT bytes are re-committed verbatim
        # (byte-identical Blueprint + changed Proposal, or both unchanged,
        # are valid committed pairs; never a proposal-only fallback).
        from ..proposal.living_blueprint import current_blueprint_path

        revised_bp_text = (
            parsed.revised_blueprint
            if parsed.revised_blueprint is not None
            and parsed.revised_blueprint.strip()
            else current_blueprint_path(workspace).read_bytes().decode("utf-8")
        )
        # DUAL DOCUMENT PAIR COMMIT — all-or-rollback.  The parsed pair is
        # written together with ONE durable manifest as the commit marker;
        # any failure restores the touched document(s) from their exact
        # snapshots and the machine stays at INTEGRATION (fail closed).
        try:
            pair_report = commit_document_pair(
                workspace=workspace,
                state_machine=state_machine,
                iteration_number=iteration_number,
                revised_blueprint_text=revised_bp_text,
                revised_proposal_text=parsed.revised_proposal,
                # Session 021B: the panel's real source-pack identity is
                # PRESERVED into the committed pair manifest (never an
                # empty placeholder when the caller carries one).
                source_pack_id=str(source_pack_id or ""),
                proposal_revision=str(proposal_revision),
            )
        except DocumentPairStateError as exc:
            outcome = (
                ProposalIntegrationOutcome.STALE_INPUT
                if exc.reason in {"missing_current_blueprint", "missing_master_proposal"}
                else ProposalIntegrationOutcome.WRITE_FAILED
            )
            return _fail(
                outcome,
                f"dual-document pair commit refused ({exc.reason}): {exc}",
                driver_id=driver_id,
                session_id=session_id,
            )
        new_hash = pair_report.proposal_hash
        new_blueprint_hash = pair_report.blueprint_hash
        pair_changed = (
            pair_report.blueprint_changed or pair_report.proposal_changed
        )
        report.output_proposal_hash = new_hash
        report.output_blueprint_hash = new_blueprint_hash
        report.pair_revision_id = pair_report.pair_revision_id
        report.changed = pair_changed
        post_freeze_exception: Optional[Exception] = None
        try:
            frozen_path = freeze_post_integration_version(
                workspace=workspace,
                iteration_number=iteration_number,
                proposal_revision=str(proposal_revision),
                previous_hash=input_hash,
                new_bytes=_written_bytes(write_report=None, parsed=parsed),
                new_hash=new_hash,
                integration_artifact_path=str(
                    integration_result_path(workspace, iteration_number)
                ),
                pre_review_version_path=str(pre_review_version_path or ""),
            )
        except Exception as exc:  # VersionFreezeError or OSError
            post_freeze_exception = exc
        if post_freeze_exception is None:
            # Session 021: the post-integration LIVING Blueprint freeze —
            # same exact-bytes discipline; a Blueprint kept byte-identical
            # is frozen as such (hash equality recorded).  The frozen bytes
            # are the COMMITTED bytes (chair's revision or the verbatim
            # current text), re-derived through the same canonical EOF.
            committed_bp_text = revised_bp_text
            blueprint_data = committed_bp_text.encode("utf-8")
            if not blueprint_data.endswith(b"\n"):
                blueprint_data += b"\n"
            try:
                freeze_post_integration_blueprint_version(
                    workspace=workspace,
                    iteration_number=iteration_number,
                    blueprint_bytes=blueprint_data,
                    blueprint_hash=new_blueprint_hash,
                    previous_blueprint_hash=current_bp_hash,
                    pair_revision_id=pair_report.pair_revision_id,
                )
            except Exception as exc:  # VersionFreezeError or OSError
                post_freeze_exception = exc
    else:
        write_report = replace_master_proposal(
            workspace=workspace,
            state_machine=state_machine,
            expected_current_hash=input_hash,
            revised_proposal_text=parsed.revised_proposal,
        )
        new_hash = write_report.new_hash
        new_blueprint_hash = ""
        report.output_proposal_hash = new_hash
        report.changed = write_report.changed
        post_freeze_exception = None
        try:
            frozen_path = freeze_post_integration_version(
                workspace=workspace,
                iteration_number=iteration_number,
                proposal_revision=str(proposal_revision),
                previous_hash=input_hash,
                new_bytes=_written_bytes(write_report, parsed),
                new_hash=new_hash,
                integration_artifact_path=str(
                    integration_result_path(workspace, iteration_number)
                ),
                pre_review_version_path=str(pre_review_version_path or ""),
            )
        except Exception as exc:  # VersionFreezeError or OSError
            post_freeze_exception = exc

    if post_freeze_exception is not None:
        # PARTIAL SUCCESS, surfaced honestly: the document write(s) DID
        # happen (single master write or committed pair), but the durable
        # post-integration evidence refused — the machine legitimately
        # walks to HARD_GATE_VALIDATION and the conflict is surfaced.
        state_machine.transition_to(ProposalPhase.HARD_GATE_VALIDATION)
        return _fail(
            ProposalIntegrationOutcome.ARTIFACT_CONFLICT,
            f"master proposal was atomically replaced ({input_hash} -> "
            f"{new_hash}) but the post-integration freeze failed: "
            f"{post_freeze_exception}",
            driver_id=driver_id,
            session_id=session_id,
        )
    report.post_integration_version_path = str(frozen_path)

    payload = build_integration_result_payload(
        iteration_number=iteration_number,
        proposal_revision=str(proposal_revision),
        input_hash=input_hash,
        output_hash=new_hash,
        result=parsed,
        runtime_outcome=(
            ProposalIntegrationOutcome.COMPLETED_CHANGED.value
            if report.changed
            else ProposalIntegrationOutcome.COMPLETED_NO_CHANGE.value
        ),
        driver_id=driver_id,
        session_id=session_id,
        duration_s=time.monotonic() - started,
    )
    try:
        artifact_path = write_integration_result(
            workspace=workspace, payload=payload
        )
    except ArtifactConflictError as exc:
        # PARTIAL SUCCESS, surfaced honestly (same rule as the freeze).
        state_machine.transition_to(ProposalPhase.HARD_GATE_VALIDATION)
        return _fail(
            ProposalIntegrationOutcome.ARTIFACT_CONFLICT,
            f"master proposal was atomically replaced ({input_hash} -> "
            f"{new_hash}) but the integration artifact write failed: {exc}",
            driver_id=driver_id,
            session_id=session_id,
        )
    report.integration_result_path = str(artifact_path)

    # -- 11. state advance (ONLY after every gate and write held) ------------
    state_machine.transition_to(ProposalPhase.HARD_GATE_VALIDATION)
    report.outcome = (
        ProposalIntegrationOutcome.COMPLETED_CHANGED
        if report.changed
        else ProposalIntegrationOutcome.COMPLETED_NO_CHANGE
    )
    report.state_advanced = True
    report.state_after = state_machine.phase
    report.driver_id = driver_id
    report.session_id = session_id
    report.duration_s = time.monotonic() - started
    return report


def _written_bytes(
    write_report: Optional[MasterProposalWriteReport],
    parsed: ProposalIntegrationResult,
) -> bytes:
    """The exact bytes written into MASTER_PROPOSAL.md.

    Re-derived from the parsed text with the writer's ONE canonical EOF
    policy (append ``\\n`` when absent) — identical to what the writer
    hashed (both over the same transformation of the same text).
    ``write_report`` is None on the dual-document pair-commit path (the
    pair writer hashes the same canonical transformation itself); only
    the parsed text matters either way.
    """
    data = parsed.revised_proposal.encode("utf-8")
    if not data.endswith(b"\n"):
        data += b"\n"
    return data


#: The generic agent role under which the ORCHESTRATOR integration session
#: runs.  Proposal Mode roles stay independent (never aliased); the driver
#: contract simply needs A generic role value, and the orchestrator maps to
#: the coding-domain ORCHESTRATOR the same way a reviewer maps to
#: TASK_AUDITOR (Session 013 convention).
_ORCHESTRATOR_AGENT_ROLE = AgentRole.ORCHESTRATOR
