"""One-review runtime executor — packet → driver → parser → guarded result.

The bridge is deliberately SMALL: it owns NO engine logic, NO provider code
and NO registry.  It drives the existing generic ``BaseDriver`` contract
through dependency injection (tests use a scripted driver; no network, no
real provider, no credentials), and maps the flow::

    ProposalReviewPacket
      → existing generic driver contract (start_session / send_prompt /
        wait_for_completion)
      → raw answer
      → ProposalReviewParser (fail closed)
      → ProposalReviewResult

Fail-closed order of operations (every gate runs BEFORE the driver is
called where stated; NO failure path ever advances the state machine):

1. phase/role guard (before driver): the state machine's current phase must
   map exactly to the requested reviewer role (SCIENTIFIC_REVIEW →
   SCIENTIFIC_REVIEWER, IMPLEMENTATION_REVIEW → PROPOSAL_ENGINEER,
   RED_TEAM_REVIEW → RED_TEAM_REVIEWER);
2. proposal fingerprint (before driver): SHA-256 of the EXACT master
   proposal bytes (D-057 canonical algorithm) — no hash, no execution;
3. driver call: any driver failure (DriverError, DriverNotImplementedError,
   timeout, empty/failed answer) fails the execution;
4. strict parse of the raw answer (fail closed);
5. proposal fingerprint AGAIN: an unchanged digest is mandatory — a changed
   digest is a write-authority violation and poisons the whole execution
   even when the parsed verdict was PASS;
6. state advance: ONLY after every gate held, through the explicit
   ProposalStateMachine edges (review → next review phase; INTEGRATION is
   reached but never left — COMPLETE stays reserved for later hard-gate
   validation).

The report keeps OPERATIONAL EVIDENCE ONLY: outcome, role, session id,
duration, parser error, proposal hashes and a BOUNDED raw excerpt.  Full
model transcripts are never persisted by this layer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from ..drivers.base import (
    BaseDriver,
    DriverError,
    DriverSession,
    PromptHandle,
    PromptResult,
    SessionRequest,
)
from ..domain.enums import AgentRole, SessionPolicy
from ..proposal.enums import ProposalPhase, ProposalRole, ProposalReviewVerdict
from ..proposal.fingerprint import proposal_fingerprint
from ..proposal.models import ProposalAgentConfig, ProposalReviewResult
from ..proposal.review_parser import ProposalReviewParseError, parse_proposal_review
from ..proposal.state_machine import ProposalStateMachine

__all__ = [
    "MAX_EXCERPT_CHARS",
    "NEXT_REVIEW_PHASE",
    "PHASE_FOR_REVIEWER",
    "REVIEW_PHASES",
    "ProposalReviewExecutionReport",
    "ProposalReviewGuardError",
    "ProposalReviewOutcome",
    "execute_review_call",
    "run_review",
]

#: Bounded raw-output excerpt retained on the report (never a full transcript).
MAX_EXCERPT_CHARS = 4_000

#: Review phases that map to a reviewer role, with the exact mapping.
PHASE_FOR_REVIEWER: dict[ProposalRole, ProposalPhase] = {
    ProposalRole.SCIENTIFIC_REVIEWER: ProposalPhase.SCIENTIFIC_REVIEW,
    ProposalRole.PROPOSAL_ENGINEER: ProposalPhase.IMPLEMENTATION_REVIEW,
    ProposalRole.RED_TEAM_REVIEWER: ProposalPhase.RED_TEAM_REVIEW,
}

#: Reviewer role a given review phase demands (inverse mapping).
REVIEW_PHASES: dict[ProposalPhase, ProposalRole] = {
    phase: role for role, phase in PHASE_FOR_REVIEWER.items()
}

#: Where each successful review phase goes next (INTEGRATION is the last
#: edge; leaving INTEGRATION is NOT this layer's business).
NEXT_REVIEW_PHASE: dict[ProposalPhase, ProposalPhase] = {
    ProposalPhase.SCIENTIFIC_REVIEW: ProposalPhase.IMPLEMENTATION_REVIEW,
    ProposalPhase.IMPLEMENTATION_REVIEW: ProposalPhase.RED_TEAM_REVIEW,
    ProposalPhase.RED_TEAM_REVIEW: ProposalPhase.INTEGRATION,
}


class ProposalReviewOutcome(str, Enum):
    """Terminal outcome of ONE reviewer execution attempt."""

    COMPLETED = "COMPLETED"      # parsed valid review AND state advanced
    PARSE_FAILED = "PARSE_FAILED"
    DRIVER_FAILED = "DRIVER_FAILED"
    PHASE_ROLE_MISMATCH = "PHASE_ROLE_MISMATCH"
    MUTATION_DETECTED = "MUTATION_DETECTED"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


class ProposalReviewGuardError(RuntimeError):
    """A guard rejected the review BEFORE the state machine could advance.

    Raised for phase/role mismatches and missing fingerprints — rejections
    that are operator/programming errors rather than model answers.
    """

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"[{reason}] {message}")


@dataclass(slots=True)
class ProposalReviewExecutionReport:
    """Operational evidence of ONE reviewer execution attempt.

    ``raw_excerpt`` is bounded (:data:`MAX_EXCERPT_CHARS`); full model
    transcripts are deliberately NOT retained here.
    """

    outcome: ProposalReviewOutcome
    reviewer_role: ProposalRole
    proposal_hash: str = ""
    proposal_hash_after: str = ""
    session_id: Optional[str] = None
    duration_s: float = 0.0
    result: Optional[ProposalReviewResult] = None
    error: str = ""
    parse_reason: str = ""
    raw_excerpt: str = ""
    state_advanced: bool = False
    new_phase: Optional[ProposalPhase] = None
    driver_id: str = ""

    # -- convenience -----------------------------------------------------
    @property
    def ok(self) -> bool:
        return self.outcome is ProposalReviewOutcome.COMPLETED

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "reviewer_role": self.reviewer_role.value,
            "proposal_hash": self.proposal_hash,
            "proposal_hash_after": self.proposal_hash_after,
            "session_id": self.session_id,
            "duration_s": round(self.duration_s, 6),
            "result": self.result.to_dict() if self.result is not None else None,
            "error": self.error,
            "parse_reason": self.parse_reason,
            "raw_excerpt": self.raw_excerpt,
            "state_advanced": self.state_advanced,
            "new_phase": self.new_phase.value if self.new_phase is not None else None,
            "driver_id": self.driver_id,
        }


def _bounded_excerpt(text: str) -> str:
    if len(text) <= MAX_EXCERPT_CHARS:
        return text
    return text[:MAX_EXCERPT_CHARS]


def run_review(
    *,
    packet: Any,
    driver: BaseDriver,
    state_machine: ProposalStateMachine,
    master_proposal_path: Path,
    session_policy: SessionPolicy = SessionPolicy.ALWAYS_NEW,
    timeout_s: Optional[float] = None,
    agent_config: Optional[ProposalAgentConfig] = None,
    proposal_workspace_path: Optional[Path] = None,
    extra_payload: Optional[dict] = None,
) -> ProposalReviewExecutionReport:
    """Execute ONE reviewer operation through ``driver`` — fail closed.

    Parameters
    ----------
    packet:
        A rendered :class:`~encomm_pcc.proposal.ProposalReviewPacket`.
    driver:
        Any existing ``BaseDriver`` implementation (injected; tests pass a
        scripted driver — zero model calls).
    state_machine:
        The proposal state machine whose CURRENT phase gates the operation.
        It is advanced ONLY on a fully valid execution.
    master_proposal_path:
        Path of ``03_PROPOSAL/MASTER_PROPOSAL.md``; fingerprinted before and
        after the driver call (immutability guard).
    session_policy:
        How the driver session is (re)used for this one call.
    timeout_s:
        Optional prompt timeout forwarded to the driver.
    agent_config:
        Optional :class:`~encomm_pcc.proposal.ProposalAgentConfig` for THIS
        reviewer role (Session 017A).  When supplied, its ``project_profile``,
        ``provider`` and ``model`` reach the driver's :class:`SessionRequest`
        verbatim — the operator's AGENTS configuration becomes authoritative
        for a real run.  When omitted, the offline/unit legacy shape is
        preserved unchanged.
    proposal_workspace_path:
        Optional ACTUAL proposal workspace root (Session 017A).  When
        supplied it becomes the ``SessionRequest.workspace_path`` so a real
        driver is never launched against an empty workspace.
    extra_payload:
        Optional extra parsed-result fields (Session 019 panel: the source
        pack identity each parallel call consumed).  Copied into the result
        BEFORE validation; keys must be JSON-friendly strings.
    """
    role: ProposalRole = packet.role

    # -- gate 1: phase → role mapping (BEFORE any driver contact) ---------
    expected_phase = PHASE_FOR_REVIEWER.get(role)
    if expected_phase is None:
        raise ProposalReviewGuardError(
            "not_a_reviewer_role",
            f"{role.value} is not a reviewer role.",
        )
    if state_machine.phase is not expected_phase:
        raise ProposalReviewGuardError(
            "phase_role_mismatch",
            f"reviewer {role.value} requires phase {expected_phase.value}, "
            f"but the state machine is at {state_machine.phase.value}; "
            "refusing to launch.",
        )

    # -- gate 2: fingerprint BEFORE execution -----------------------------
    try:
        proposal_fingerprint(Path(master_proposal_path))
    except OSError as exc:
        raise ProposalReviewGuardError(
            "fingerprint_failed", str(exc)
        ) from exc

    # -- the machine-free call (driver → parse → mutation guard) ----------
    report = execute_review_call(
        packet=packet,
        driver=driver,
        master_proposal_path=master_proposal_path,
        session_policy=session_policy,
        timeout_s=timeout_s,
        agent_config=agent_config,
        proposal_workspace_path=proposal_workspace_path,
        extra_payload=extra_payload,
    )

    # -- state advance (ONLY on a fully valid execution) -------------------
    # A BLOCKED verdict is a VALID review: it moves through the explicit
    # D-055 BLOCKED path instead of the review→review edge.  PASS and
    # NEEDS_REVISION walk the phase graph (RED_TEAM_REVIEW's success edge
    # ends AT INTEGRATION — this layer never advances past it, and COMPLETE
    # stays reserved for later hard-gate validation).
    if report.outcome is ProposalReviewOutcome.COMPLETED:
        parsed = report.result
        if parsed is not None and parsed.verdict is ProposalReviewVerdict.BLOCKED:
            target = ProposalPhase.BLOCKED
        else:
            target = NEXT_REVIEW_PHASE[state_machine.phase]
        state_machine.transition_to(target)  # raises on an illegal edge (never expected)
        report.state_advanced = True
        report.new_phase = target
    return report


def execute_review_call(
    *,
    packet: Any,
    driver: BaseDriver,
    master_proposal_path: Path,
    session_policy: SessionPolicy = SessionPolicy.ALWAYS_NEW,
    timeout_s: Optional[float] = None,
    agent_config: Optional[ProposalAgentConfig] = None,
    proposal_workspace_path: Optional[Path] = None,
    extra_payload: Optional[dict] = None,
) -> ProposalReviewExecutionReport:
    """Run ONE reviewer model call WITHOUT touching the state machine.

    Session 019 (parallel panel): the three evaluator calls of one round
    overlap in wall-clock time, so the state machine CANNOT be advanced
    per-call (brief §17 — durable advancement happens deterministically
    AFTER all valid results are joined).  This is the shared core of
    :func:`run_review`: fingerprint before, driver call, strict fail-closed
    parse, fingerprint after (mutation guard).  ``run_review`` wraps this
    with the phase/role gate and the single state advance; the panel calls
    THIS function concurrently and advances the graph itself in canonical
    order after the join.

    Carries ``extra_payload`` (e.g. the source-pack identity) into the
    parsed result BEFORE validation when supplied.
    """
    role: ProposalRole = packet.role
    started = time.monotonic()

    def _report(**kwargs: Any) -> ProposalReviewExecutionReport:
        kwargs.setdefault("reviewer_role", role)
        kwargs.setdefault("duration_s", time.monotonic() - started)
        return ProposalReviewExecutionReport(**kwargs)

    # -- fingerprint BEFORE execution --------------------------------------
    try:
        hash_before = proposal_fingerprint(Path(master_proposal_path))
    except OSError as exc:
        raise ProposalReviewGuardError(
            "fingerprint_failed", str(exc)
        ) from exc

    # -- driver call ------------------------------------------------------
    session: DriverSession | None = None
    try:
        request = _session_request(
            packet,
            session_policy,
            agent_config=agent_config,
            proposal_workspace_path=proposal_workspace_path,
        )
        resume_id = _resume_session_id(agent_config, driver)
        if resume_id:
            # Session 019 (brief §11): explicit RESUME SELECTED SESSION —
            # the driver contract verifies the id and the CLI/profile bind;
            # a resume failure fails this call honestly.
            session = driver.resume_session(resume_id, request)
        else:
            session = driver.start_session(request)
        handle: PromptHandle = driver.send_prompt(session, packet.prompt_text)
        prompt_result: PromptResult = driver.wait_for_completion(handle, timeout_s)
    except DriverError as exc:
        return _report(
            outcome=ProposalReviewOutcome.DRIVER_FAILED,
            proposal_hash=hash_before,
            error=f"driver error: {exc}",
            driver_id=getattr(driver, "driver_id", ""),
        )
    except (OSError, ValueError, NotImplementedError) as exc:
        # Defensive: driver implementations raise plain exceptions too.
        return _report(
            outcome=ProposalReviewOutcome.DRIVER_FAILED,
            proposal_hash=hash_before,
            error=f"driver failure: {exc}",
            driver_id=getattr(driver, "driver_id", ""),
        )

    session_id = prompt_result.session_id or (
        session.session_id if session is not None else None
    )
    if not prompt_result.ok:
        return _report(
            outcome=ProposalReviewOutcome.DRIVER_FAILED,
            proposal_hash=hash_before,
            session_id=session_id,
            error=prompt_result.error or "driver reported failure without an error",
            raw_excerpt=_bounded_excerpt(prompt_result.text),
            driver_id=getattr(driver, "driver_id", ""),
        )
    if not prompt_result.text.strip():
        return _report(
            outcome=ProposalReviewOutcome.DRIVER_FAILED,
            proposal_hash=hash_before,
            session_id=session_id,
            error="driver returned an empty answer",
            driver_id=getattr(driver, "driver_id", ""),
        )

    # -- strict parse (fail closed) ---------------------------------------
    try:
        parsed = parse_proposal_review(
            prompt_result.text,
            expected_role=role,
            expected_iteration=packet.iteration_number,
        )
    except ProposalReviewParseError as exc:
        return _report(
            outcome=ProposalReviewOutcome.PARSE_FAILED,
            proposal_hash=hash_before,
            session_id=session_id,
            parse_reason=exc.reason,
            error=str(exc),
            raw_excerpt=_bounded_excerpt(prompt_result.text),
            driver_id=getattr(driver, "driver_id", ""),
        )

    if extra_payload:
        # Session 019: the panel stamps structured fields (e.g. the source
        # pack identity this call consumed) into the result BEFORE any
        # consumer validates it.  An unknown key fails closed — silently
        # dropping panel identity would launder a mixed-source run.
        for key, value in extra_payload.items():
            if not hasattr(parsed, key):
                raise ProposalReviewParseError(
                    "unknown_extra_field",
                    f"extra payload key {key!r} does not exist on "
                    "ProposalReviewResult; refusing to invent fields.",
                )
            setattr(parsed, key, value)

    # -- gate 3: master proposal immutability AFTER execution -------------
    try:
        hash_after = proposal_fingerprint(Path(master_proposal_path))
    except OSError as exc:
        # The proposal became unreadable/missing DURING the review — treat it
        # as a mutation-class failure, never as success.
        return _report(
            outcome=ProposalReviewOutcome.MUTATION_DETECTED,
            proposal_hash=hash_before,
            session_id=session_id,
            result=parsed,
            error=f"post-review fingerprint failed: {exc}",
            raw_excerpt=_bounded_excerpt(prompt_result.text),
            state_advanced=False,
            driver_id=getattr(driver, "driver_id", ""),
        )
    if hash_after != hash_before:
        # Write-authority violation: reviewers are READ-ONLY.  The parsed
        # result is NOT accepted, the state machine does NOT advance, and the
        # change is surfaced — never silently restored or deleted.
        return _report(
            outcome=ProposalReviewOutcome.MUTATION_DETECTED,
            proposal_hash=hash_before,
            proposal_hash_after=hash_after,
            session_id=session_id,
            result=parsed,
            error=(
                "MASTER_PROPOSAL.md changed during the review (write-authority "
                f"violation): {hash_before} -> {hash_after}; reviewer result "
                "refused, state NOT advanced."
            ),
            raw_excerpt=_bounded_excerpt(prompt_result.text),
            state_advanced=False,
            driver_id=getattr(driver, "driver_id", ""),
        )

    # -- completed: the CALLER owns any state advance -----------------------
    # The machine-free core reports COMPLETED with the parsed result; the
    # phase graph is advanced by run_review (sequential) or the panel's
    # deterministic post-join walk (parallel), never here.
    return _report(
        outcome=ProposalReviewOutcome.COMPLETED,
        proposal_hash=hash_before,
        proposal_hash_after=hash_after,
        session_id=session_id,
        result=parsed,
        raw_excerpt=_bounded_excerpt(prompt_result.text),
        state_advanced=False,
        new_phase=None,
        driver_id=getattr(driver, "driver_id", ""),
    )


def _resume_session_id(
    agent_config: Optional[ProposalAgentConfig], driver: BaseDriver
) -> str:
    """Resolve the explicit resume id — NEVER invent one (brief §11).

    A resume happens only when the operator explicitly chose
    ``RESUME_SELECTED_SESSION`` AND the config carries a real session id AND
    the driver's capabilities expose ``supports_resume``.  Any explicit
    resume request that cannot be honoured raises ``DriverError`` (the call
    fails closed) — the requested session is never silently replaced by a
    fresh one.
    """
    if agent_config is None:
        return ""
    mode = getattr(agent_config, "session_mode", "NEW_SESSION")
    wanted_id = str(getattr(agent_config, "session_id", "") or "").strip()
    if mode != "RESUME_SELECTED_SESSION":
        if wanted_id and mode == "NEW_SESSION":
            # A leftover id without an explicit resume request is NOT used:
            # carrying sessions across runs would break evaluator
            # independence; the operator must opt in via the mode.
            return ""
        return ""
    capabilities = driver.capabilities()
    if not getattr(capabilities, "supports_resume", False):
        raise DriverError(
            f"session_mode RESUME_SELECTED_SESSION requires a driver with "
            f"supports_resume; {getattr(driver, 'driver_id', '?')} does not "
            "support it. Refusing to silently start a new session."
        )
    if not wanted_id:
        raise DriverError(
            "session_mode RESUME_SELECTED_SESSION requires a real session_id; "
            "refusing to invent one."
        )
    return wanted_id


def _session_request(
    packet: Any,
    session_policy: SessionPolicy,
    *,
    agent_config: Optional[ProposalAgentConfig] = None,
    proposal_workspace_path: Optional[Path] = None,
) -> Any:
    """Build the generic SessionRequest for ONE reviewer call.

    Imported lazily so this module's import surface stays minimal; the
    request carries the proposal role string for traceability only — role
    logic itself never references an engine.

    Session 017A: when ``agent_config`` is supplied, its
    ``project_profile`` / ``provider`` / ``model`` reach the request
    verbatim (the operator's configuration is authoritative).  When
    ``proposal_workspace_path`` is supplied it IS the request's
    ``workspace_path`` (the ACTUAL proposal workspace — never empty in
    production); without it the legacy packet fallback is preserved for
    offline/unit callers.  The pure packet never carries a path.
    """
    profile = ""
    provider = ""
    model = ""
    if agent_config is not None:
        profile = str(agent_config.project_profile or "")
        provider = str(agent_config.provider or "")
        model = str(agent_config.model or "")
    if proposal_workspace_path is not None:
        workspace_path = str(proposal_workspace_path)
    else:
        workspace_path = str(getattr(packet, "workspace_path", "") or "")
    # Session 021: the operator's per-role reasoning effort rides ONLY in the
    # SessionRequest extra — the Codex driver consumes and validates it, every
    # other engine ignores unknown extra keys (their own config namespaces are
    # separate), so a Hermes run can never receive a Codex flag.
    extra: dict[str, Any] = {
        "proposal_role": packet.role.value,
        "proposal_iteration": packet.iteration_number,
    }
    if agent_config is not None:
        effort = str(getattr(agent_config, "reasoning_effort", "") or "").strip()
        if effort:
            extra["reasoning_effort"] = effort
    return SessionRequest(
        role=_REVIEW_AGENT_ROLE,
        workspace_path=workspace_path,
        project_profile=profile,
        provider=provider,
        model=model,
        session_policy=session_policy,
        extra=extra,
    )


#: The generic agent role under which reviewer sessions run.  Proposal Mode
#: roles stay independent (never aliased); the driver contract simply needs A
#: generic role value, and a reviewer is conceptually an auditor of the
#: proposal documents.
_REVIEW_AGENT_ROLE = AgentRole.TASK_AUDITOR
