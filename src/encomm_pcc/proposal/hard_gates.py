"""Hard-gate engine contracts — PURE proposal domain (Session 016).

This module owns the typed contracts behind the deterministic hard-gate run:

* the machine-readable evidence contract
  (``05_CONTROL/HARD_GATE_EVIDENCE.json``, schema
  ``encomm-pcc.hard-gate-evidence/v1``) and its fail-closed loader;
* the typed :class:`HardGateEvaluation` (one gate's deterministic verdict,
  internally classified as a proposal issue or an evidence failure);
* the typed :class:`HardGateRunResult` (all 14 canonical gates in canonical
  order) and the deterministic disposition rule;

The 14 gate validators themselves live in
:mod:`encomm_pcc.proposal.hard_gate_validators` (also PURE).  The runtime
entry point is ``proposal_runtime.hard_gate_runner.run_hard_gates``.

HARD RULES (ADRs D-065/D-066/D-067):

* Every gate is DETERMINISTIC and EVIDENCE-BOUND: no model/LLM call, no
  network, no provider code ever decides a gate outcome.
* The evidence document is bound to ONE ``iteration_number`` and ONE exact
  ``proposal_hash``.  Stale evidence (bound to another revision or
  iteration) NEVER passes and is never silently re-bound.
* ``NOT_APPLICABLE`` is accepted ONLY from an explicit applicability entry
  (``applicable=false`` + non-empty reason).  Missing evidence never
  becomes PASS and never becomes NOT_APPLICABLE.
* ``WARN`` never permits COMPLETE.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from .enums import HARD_GATE_IDS, HARD_GATE_IDS_TUPLE, ProposalHardGateStatus
from .models import ProposalHardGateResult

__all__ = [
    "EVIDENCE_DOCUMENT_FILENAME",
    "HARD_GATE_EVIDENCE_SCHEMA",
    "HardGateContext",
    "HardGateDisposition",
    "HardGateEngineError",
    "HardGateEvaluation",
    "HardGateEvidenceDocument",
    "HardGateEvidenceError",
    "HardGateFailureClass",
    "HardGateRunResult",
    "build_gate_registry",
    "evaluate_hard_gates",
    "load_hard_gate_evidence",
    "validate_gate_registry",
]

#: Canonical control-file name of the evidence contract.
EVIDENCE_DOCUMENT_FILENAME = "HARD_GATE_EVIDENCE.json"

#: Schema identifier required inside the evidence document.
HARD_GATE_EVIDENCE_SCHEMA = "encomm-pcc.hard-gate-evidence/v1"


class HardGateEvidenceError(RuntimeError):
    """The evidence contract could not be loaded/bound (fail closed).

    ``reason`` is a short stable machine tag; the runner maps every
    :class:`HardGateEvidenceError` to a BLOCKED-class outcome — evidence
    problems are operator/input failures, never proposal failures.
    """

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"[{reason}] {message}")


class HardGateEngineError(RuntimeError):
    """An internal engine invariant was breached (never an input problem)."""


class HardGateFailureClass(str, Enum):
    """Internal classification of a non-passing gate evaluation.

    Determines the lifecycle outcome (ADR D-068):

    * ``PROPOSAL_ISSUE``                    → REVISION_REQUIRED
    * ``EVIDENCE_MISSING``/``EVIDENCE_INVALID`` → BLOCKED
    * ``NONE`` (PASS/NOT_APPLICABLE/WARN)   → see disposition rule
    """

    NONE = "NONE"
    PROPOSAL_ISSUE = "PROPOSAL_ISSUE"
    EVIDENCE_MISSING = "EVIDENCE_MISSING"
    EVIDENCE_INVALID = "EVIDENCE_INVALID"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


class HardGateDisposition(str, Enum):
    """Deterministic overall disposition of one complete hard-gate run.

    * every gate PASS or explicit NOT_APPLICABLE  → ``COMPLETE``
    * at least one ``PROPOSAL_ISSUE``             → ``REVISION_REQUIRED``
    * no proposal issue, but an evidence failure  → ``BLOCKED``
    * no failure at all, but a WARN remains       → ``INCOMPLETE``
      (the run may not COMPLETE; the machine legitimately stays at
      ``HARD_GATE_VALIDATION`` so the operator can supply authoritative
      evidence and re-run)
    """

    COMPLETE = "COMPLETE"
    REVISION_REQUIRED = "REVISION_REQUIRED"
    BLOCKED = "BLOCKED"
    INCOMPLETE = "INCOMPLETE"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


@dataclass(slots=True)
class GateApplicability:
    """One explicit applicability entry from the evidence document."""

    applicable: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"applicable": self.applicable, "reason": self.reason}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GateApplicability":
        return cls(
            applicable=bool(data.get("applicable", False)),
            reason=str(data.get("reason") or ""),
        )


@dataclass(slots=True)
class HardGateEvaluation:
    """The deterministic verdict of ONE canonical hard gate.

    Carries the :class:`ProposalHardGateResult` fields (``gate_id``,
    ``status``, ``message``, ``evidence``) plus the internal
    :class:`HardGateFailureClass` that drives the lifecycle outcome.
    """

    gate_id: str
    status: ProposalHardGateStatus
    message: str = ""
    evidence: str = ""
    failure_class: HardGateFailureClass = HardGateFailureClass.NONE

    def __post_init__(self) -> None:
        self.gate_id = str(self.gate_id)
        if self.gate_id not in HARD_GATE_IDS:
            raise ValueError(
                f"Non-canonical hard-gate id: {self.gate_id!r} "
                f"(must be one of the HARD_GATE_IDS constants)"
            )
        if not isinstance(self.status, ProposalHardGateStatus):
            self.status = ProposalHardGateStatus(str(self.status))
        if not isinstance(self.failure_class, HardGateFailureClass):
            self.failure_class = HardGateFailureClass(str(self.failure_class))
        # A passing/applicable gate can never carry a failure class.
        if (
            self.status in (ProposalHardGateStatus.PASS, ProposalHardGateStatus.NOT_APPLICABLE)
            and self.failure_class is not HardGateFailureClass.NONE
        ):
            raise ValueError(
                f"gate {self.gate_id}: status {self.status.value} cannot "
                f"carry failure_class {self.failure_class.value}."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "status": self.status.value,
            "message": self.message,
            "evidence": self.evidence,
            "failure_class": self.failure_class.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HardGateEvaluation":
        return cls(
            gate_id=str(data.get("gate_id") or ""),
            status=ProposalHardGateStatus(str(data.get("status") or "")),
            message=str(data.get("message") or ""),
            evidence=str(data.get("evidence") or ""),
            failure_class=HardGateFailureClass(
                str(data.get("failure_class") or HardGateFailureClass.NONE.value)
            ),
        )

    def to_gate_result(self) -> ProposalHardGateResult:
        """Project onto the canonical Session 012 result contract."""
        return ProposalHardGateResult(
            gate_id=self.gate_id,
            status=self.status,
            message=self.message,
            evidence=self.evidence,
        )


@dataclass(slots=True)
class HardGateRunResult:
    """The complete deterministic result of ONE hard-gate run.

    ``evaluations`` is ALWAYS the full canonical set in canonical
    (:data:`HARD_GATE_IDS_TUPLE`) order — a run that did not evaluate all
    14 gates cannot exist.
    """

    iteration_number: int
    proposal_hash: str
    evaluations: list[HardGateEvaluation] = field(default_factory=list)
    disposition: HardGateDisposition = HardGateDisposition.INCOMPLETE

    def __post_init__(self) -> None:
        ids = [e.gate_id for e in self.evaluations]
        if ids and tuple(ids) != HARD_GATE_IDS_TUPLE:
            raise HardGateEngineError(
                "hard-gate evaluations must be exactly the canonical gates "
                f"in canonical order; got {ids!r}."
            )

    @property
    def gate_results(self) -> list[ProposalHardGateResult]:
        """The 14 canonical results as Session 012 contract objects."""
        return [e.to_gate_result() for e in self.evaluations]

    @property
    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {
            status.value.lower(): 0 for status in ProposalHardGateStatus
        }
        for evaluation in self.evaluations:
            counts[evaluation.status.value.lower()] += 1
        counts["total"] = len(self.evaluations)
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration_number": self.iteration_number,
            "proposal_hash": self.proposal_hash,
            "evaluations": [e.to_dict() for e in self.evaluations],
            "disposition": self.disposition.value,
            "counts": self.counts,
        }

    @classmethod
    def from_evaluations(
        cls,
        *,
        iteration_number: int,
        proposal_hash: str,
        evaluations: list[HardGateEvaluation],
    ) -> "HardGateRunResult":
        """Build the run result AND derive the deterministic disposition.

        Disposition rule (fixed order, first match wins):

        1. any ``PROPOSAL_ISSUE``                  → ``REVISION_REQUIRED``
        2. any ``EVIDENCE_MISSING``/``INVALID``    → ``BLOCKED``
        3. any ``WARN``                            → ``INCOMPLETE``
        4. otherwise (PASS / explicit N/A only)    → ``COMPLETE``
        """
        ids = [e.gate_id for e in evaluations]
        if tuple(ids) != HARD_GATE_IDS_TUPLE:
            raise HardGateEngineError(
                "evaluate_hard_gates produced an incomplete or duplicated "
                f"gate set: {ids!r}."
            )
        classes = [e.failure_class for e in evaluations]
        if any(c is HardGateFailureClass.PROPOSAL_ISSUE for c in classes):
            disposition = HardGateDisposition.REVISION_REQUIRED
        elif any(
            c in (HardGateFailureClass.EVIDENCE_MISSING, HardGateFailureClass.EVIDENCE_INVALID)
            for c in classes
        ):
            disposition = HardGateDisposition.BLOCKED
        elif any(e.status is ProposalHardGateStatus.WARN for e in evaluations):
            disposition = HardGateDisposition.INCOMPLETE
        else:
            disposition = HardGateDisposition.COMPLETE
        return cls(
            iteration_number=int(iteration_number),
            proposal_hash=str(proposal_hash),
            evaluations=list(evaluations),
            disposition=disposition,
        )


@dataclass(slots=True)
class HardGateEvidenceDocument:
    """Typed view of one validated evidence contract document.

    The top-level binding fields (schema, iteration, hash, applicability)
    are validated by :func:`load_hard_gate_evidence`.  The per-gate section
    blocks are kept as raw dicts — EACH gate validator owns the shape and
    the failure semantics of its own section (absent → EVIDENCE_MISSING,
    present-but-malformed → EVIDENCE_INVALID).
    """

    iteration_number: int
    proposal_hash: str
    applicability: dict[str, GateApplicability] = field(default_factory=dict)
    mandatory_sections: dict[str, Any] | None = None
    challenge_mapping: dict[str, Any] | None = None
    terminology: dict[str, Any] | None = None
    workplan: dict[str, Any] | None = None
    budget: dict[str, Any] | None = None
    subcontracting: dict[str, Any] | None = None

    def not_applicable_entry(self, gate_id: str) -> GateApplicability | None:
        """Return the explicit N/A entry for ``gate_id`` when one exists.

        The loader guarantees every stored entry with ``applicable=false``
        carries a non-empty reason, so an explicit N/A is always justified.
        Absent entry ⇒ the gate runs as applicable (no implicit N/A).
        """
        entry = self.applicability.get(gate_id)
        if entry is not None and not entry.applicable:
            return entry
        return None

    def section(self, key: str) -> dict[str, Any] | None:
        """Raw section block by evidence-document key (``None`` when absent)."""
        return getattr(self, key)  # noqa: B009 - keys are fixed module constants


@dataclass(slots=True)
class HardGateContext:
    """Everything a gate validator may look at — nothing else exists."""

    evidence: HardGateEvidenceDocument
    master_proposal_text: str
    workspace: Path


#: A gate validator: pure function of the context to a typed evaluation.
GateValidator = Callable[[HardGateContext], HardGateEvaluation]


def build_gate_registry() -> dict[str, GateValidator]:
    """Build the canonical gate_id → validator registry.

    Imported lazily to keep this module import-light; the registry MUST
    cover exactly the canonical ids (verified by
    :func:`validate_gate_registry` on every run).
    """
    from .hard_gate_validators import GATE_VALIDATORS

    return dict(GATE_VALIDATORS)


def validate_gate_registry(registry: Mapping[str, GateValidator]) -> None:
    """Refuse a registry that is not exactly the canonical gate set.

    A missing gate, a duplicate key or an extra/non-canonical id is an
    engine bug and stops the run before any gate is evaluated (matrix:
    duplicate/missing gate impossible).
    """
    keys = list(registry.keys())
    if len(keys) != len(set(keys)):
        raise HardGateEngineError(f"duplicate gate ids in registry: {keys!r}")
    if set(keys) != HARD_GATE_IDS:
        missing = sorted(HARD_GATE_IDS - set(keys))
        extra = sorted(set(keys) - HARD_GATE_IDS)
        raise HardGateEngineError(
            f"gate registry must cover exactly the canonical ids; "
            f"missing={missing!r} extra={extra!r}"
        )


def evaluate_hard_gates(
    *,
    evidence: HardGateEvidenceDocument,
    master_proposal_text: str,
    workspace: Path,
) -> HardGateRunResult:
    """Evaluate ALL canonical gates in canonical order — deterministic.

    No model call, no network, no provider configuration.  Explicitly
    justified NOT_APPLICABLE gates are recorded from the applicability
    contract and skip their validator; every other gate runs.
    """
    registry = build_gate_registry()
    validate_gate_registry(registry)
    context = HardGateContext(
        evidence=evidence,
        master_proposal_text=master_proposal_text,
        workspace=Path(workspace),
    )
    evaluations: list[HardGateEvaluation] = []
    for gate_id in HARD_GATE_IDS_TUPLE:
        na_entry = evidence.not_applicable_entry(gate_id)
        if na_entry is not None:
            evaluations.append(
                HardGateEvaluation(
                    gate_id=gate_id,
                    status=ProposalHardGateStatus.NOT_APPLICABLE,
                    message=na_entry.reason,
                    evidence="applicability.explicit_not_applicable",
                    failure_class=HardGateFailureClass.NONE,
                )
            )
            continue
        evaluations.append(registry[gate_id](context))
    return HardGateRunResult.from_evaluations(
        iteration_number=evidence.iteration_number,
        proposal_hash=evidence.proposal_hash,
        evaluations=evaluations,
    )


# ---------------------------------------------------------------------------
# evidence document loader (fail closed)
# ---------------------------------------------------------------------------
def load_hard_gate_evidence(
    path: Path,
    *,
    expected_iteration_number: int,
    expected_proposal_hash: str,
) -> HardGateEvidenceDocument:
    """Load and BIND the evidence contract — fail closed on any mismatch.

    Raises :class:`HardGateEvidenceError` when the file is missing,
    zero-byte, unreadable, corrupt, carries a wrong schema, or is bound to
    a different iteration/proposal hash.  Stale evidence is REJECTED, never
    silently accepted or re-bound (ADR D-065).
    """
    path = Path(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise HardGateEvidenceError(
            "evidence_missing",
            f"hard-gate evidence document does not exist: {path}",
        ) from exc
    except OSError as exc:
        raise HardGateEvidenceError(
            "evidence_unreadable",
            f"hard-gate evidence document cannot be read: {path} ({exc})",
        ) from exc
    if not raw.strip():
        raise HardGateEvidenceError(
            "evidence_zero_byte",
            f"hard-gate evidence document is empty (uninitialised): {path}",
        )
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HardGateEvidenceError(
            "evidence_corrupt",
            f"hard-gate evidence document is not valid JSON: {path} ({exc})",
        ) from exc
    if not isinstance(data, dict):
        raise HardGateEvidenceError(
            "evidence_corrupt",
            f"hard-gate evidence document is not a JSON object: {path}",
        )
    schema = str(data.get("schema") or "")
    if schema != HARD_GATE_EVIDENCE_SCHEMA:
        raise HardGateEvidenceError(
            "evidence_schema_invalid",
            f"evidence schema is {schema!r}, expected "
            f"{HARD_GATE_EVIDENCE_SCHEMA!r}.",
        )

    iteration = data.get("iteration_number")
    if (
        not isinstance(iteration, int)
        or isinstance(iteration, bool)
        or iteration != expected_iteration_number
    ):
        raise HardGateEvidenceError(
            "evidence_iteration_mismatch",
            f"evidence iteration_number is {iteration!r}, expected "
            f"{expected_iteration_number!r}; stale evidence never runs.",
        )
    evidence_hash = str(data.get("proposal_hash") or "").strip().lower()
    expected_hash = str(expected_proposal_hash).strip().lower()
    if not evidence_hash:
        raise HardGateEvidenceError(
            "evidence_hash_missing",
            "evidence document carries no proposal_hash.",
        )
    if evidence_hash != expected_hash:
        raise HardGateEvidenceError(
            "evidence_hash_mismatch",
            f"evidence proposal_hash is bound to another revision "
            f"({evidence_hash[:12]}… != {expected_hash[:12]}…); stale "
            "evidence never passes.",
        )

    applicability_raw = data.get("applicability") or {}
    if not isinstance(applicability_raw, dict):
        raise HardGateEvidenceError(
            "evidence_applicability_invalid",
            "applicability must be a JSON object keyed by canonical gate id.",
        )
    applicability: dict[str, GateApplicability] = {}
    for gate_id, entry_raw in applicability_raw.items():
        gate_id = str(gate_id)
        if gate_id not in HARD_GATE_IDS:
            raise HardGateEvidenceError(
                "evidence_applicability_invalid",
                f"applicability carries non-canonical gate id {gate_id!r}.",
            )
        if not isinstance(entry_raw, dict):
            raise HardGateEvidenceError(
                "evidence_applicability_invalid",
                f"applicability entry for {gate_id} must be an object.",
            )
        applicable = entry_raw.get("applicable")
        if not isinstance(applicable, bool):
            raise HardGateEvidenceError(
                "evidence_applicability_invalid",
                f"applicability entry for {gate_id} requires a boolean "
                "'applicable' field.",
            )
        reason = str(entry_raw.get("reason") or "")
        if not applicable and not reason.strip():
            raise HardGateEvidenceError(
                "evidence_applicability_invalid",
                f"explicit NOT_APPLICABLE for {gate_id} requires a non-empty "
                "reason (no implicit N/A).",
            )
        applicability[gate_id] = GateApplicability(
            applicable=applicable, reason=reason
        )

    def _section(key: str) -> dict[str, Any] | None:
        block = data.get(key)
        if block is None:
            return None
        if not isinstance(block, dict):
            raise HardGateEvidenceError(
                "evidence_section_invalid",
                f"evidence section {key!r} must be a JSON object when present.",
            )
        return block

    return HardGateEvidenceDocument(
        iteration_number=iteration,
        proposal_hash=evidence_hash,
        applicability=applicability,
        mandatory_sections=_section("mandatory_sections"),
        challenge_mapping=_section("challenge_mapping"),
        terminology=_section("terminology"),
        workplan=_section("workplan"),
        budget=_section("budget"),
        subcontracting=_section("subcontracting"),
    )
