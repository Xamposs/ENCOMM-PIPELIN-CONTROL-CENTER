"""Structured audit contract: the machine-readable verdict of a Task Auditor.

This module defines the **shape** of an audit result — what a verdict is, what
a finding is, how a verdict serialises.  It contains no parsing logic: the
parser in ``core/verdict_parser.py`` turns **untrusted** model output into an
``AuditVerdictResult`` and fails closed on anything malformed.

The contract (mirrored in the auditor prompt packet):

.. code-block:: json

    {
      "verdict": "PASS" | "NEEDS_FIX" | "BLOCKED",
      "summary": "...",
      "findings": [
        {"severity": "critical|high|medium|low", "message": "...", "evidence": "..."}
      ],
      "fix_prompt": "..."
    }

Rules (enforced by the parser, not by convention):

* ``PASS`` — ``fix_prompt`` must be empty and no finding may be\n
  ``critical``/``high`` (an unresolved defect contradicts a pass).
* ``NEEDS_FIX`` — ``fix_prompt`` MUST be present and non-empty: it is the
  deterministic correction handed to a brand-new Builder session.
* ``BLOCKED`` — the auditor cannot determine a safe correction or a manual
  decision is required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

__all__ = [
    "AuditFinding",
    "AuditVerdict",
    "AuditVerdictResult",
    "FindingSeverity",
    "UNRESOLVED_SEVERITIES",
    "VERDICTS",
]


class AuditVerdict(str, Enum):
    """Exact verdict a Task Auditor may return.  No other value is accepted."""

    PASS = "PASS"
    NEEDS_FIX = "NEEDS_FIX"
    BLOCKED = "BLOCKED"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


class FindingSeverity(str, Enum):
    """Severity of an audit finding; model output is normalised to these."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


#: Severities that contradict a PASS verdict (unresolved defects).
UNRESOLVED_SEVERITIES: frozenset[FindingSeverity] = frozenset(
    {FindingSeverity.CRITICAL, FindingSeverity.HIGH}
)

#: The only accepted verdict strings (strict parser whitelist).
VERDICTS: frozenset[str] = frozenset(v.value for v in AuditVerdict)


@dataclass(slots=True)
class AuditFinding:
    """One defect (or observation) the auditor found in the real repository."""

    severity: FindingSeverity
    message: str
    evidence: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.severity, FindingSeverity):
            self.severity = FindingSeverity(str(self.severity))

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity.value,
            "message": self.message,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AuditFinding":
        return cls(
            severity=FindingSeverity(str(data.get("severity") or "")),
            message=str(data.get("message") or ""),
            evidence=str(data.get("evidence") or ""),
        )


@dataclass(slots=True)
class AuditVerdictResult:
    """One strict, persisted audit result."""

    verdict: AuditVerdict
    summary: str = ""
    findings: list[AuditFinding] = field(default_factory=list)
    fix_prompt: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.verdict, AuditVerdict):
            self.verdict = AuditVerdict(str(self.verdict))

    @property
    def has_unresolved_defect(self) -> bool:
        """True when a critical/high finding is present (never allowed on PASS)."""
        return any(f.severity in UNRESOLVED_SEVERITIES for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "summary": self.summary,
            "findings": [f.to_dict() for f in self.findings],
            "fix_prompt": self.fix_prompt,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AuditVerdictResult":
        findings_raw = data.get("findings") or []
        try:
            verdict = AuditVerdict(str(data.get("verdict") or ""))
        except ValueError:
            verdict = AuditVerdict.BLOCKED  # never fabricate a PASS from bad data
        return cls(
            verdict=verdict,
            summary=str(data.get("summary") or ""),
            findings=[AuditFinding.from_dict(f) for f in findings_raw],
            fix_prompt=str(data.get("fix_prompt") or ""),
        )