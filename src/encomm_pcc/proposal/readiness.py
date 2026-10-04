"""Advisory internal readiness index (Session 019, briefs §22–§23).

NOT an official EIC score.  NEVER a prediction of acceptance.  The
displayed readiness is computed DETERMINISTICALLY from the three
evaluators' bounded rubric assessments:

* per-criterion MEDIAN across the participating evaluators;
* the visible disagreement spread (min/max) is reported, never hidden;
* explicit penalties for unresolved critical/high findings, unverified
  claims, missing official/template sources and unresolved contradictions;
* hard gates remain the final compliance authority — readiness NEVER
  overrides a failing gate (the gate runner does not read this file).

The rubric (criterion weights + labels) is persisted to
``05_CONTROL/READINESS_RUBRIC.json``; per-iteration results to
``04_REVIEWS/iteration_NNN/readiness.json`` and the latest snapshot to
``05_CONTROL/READINESS.json`` (all by the runtime artifact writer, not here
— this module is PURE computation).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Any, Mapping, Sequence

from .panel_contracts import READINESS_CRITERIA, ReadinessAssessment

__all__ = [
    "DEFAULT_READINESS_RUBRIC",
    "MAX_READINESS",
    "READINESS_DISCLAIMER",
    "PenaltyRule",
    "ReadinessInputs",
    "ReadinessRubric",
    "ReadinessResult",
    "compute_readiness",
    "default_rubric",
]

#: The honest label every UI surface must show next to the number.
READINESS_DISCLAIMER = "INTERNAL READINESS — NOT AN EIC SCORE"

#: Readiness is advisory; 100 is reachable only with zero penalties.
MAX_READINESS = 100


@dataclass(slots=True)
class PenaltyRule:
    """One deterministic penalty rule (points subtracted per occurrence)."""

    reason: str
    points: int

    def to_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "points": self.points}


@dataclass(slots=True)
class ReadinessRubric:
    """Transparent configurable weights for the readiness formula."""

    criteria: tuple[str, ...]
    weights: dict[str, float]
    penalties: tuple[PenaltyRule, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "criteria": list(self.criteria),
            "weights": dict(self.weights),
            "penalties": [p.to_dict() for p in self.penalties],
            "formula": (
                "weighted mean of per-criterion medians minus "
                "sum(points * occurrences) over penalty rules; floored at 0"
            ),
        }

    def validate(self) -> None:
        missing = [c for c in self.criteria if c not in self.weights]
        if missing:
            raise ValueError(f"rubric weights missing criteria: {missing}")
        unknown = [k for k in self.weights if k not in self.criteria]
        if unknown:
            raise ValueError(f"rubric weights name unknown criteria: {unknown}")
        total = sum(self.weights[c] for c in self.criteria)
        if abs(total - 1.0) > 1e-9:
            raise ValueError(
                f"rubric weights must sum to 1.0 (got {total})"
            )


#: The default transparent rubric (equal weights; explicit penalties).
DEFAULT_READINESS_RUBRIC = ReadinessRubric(
    criteria=READINESS_CRITERIA,
    weights={c: 1.0 / len(READINESS_CRITERIA) for c in READINESS_CRITERIA},
    penalties=(
        PenaltyRule(reason="unresolved_critical_finding", points=15),
        PenaltyRule(reason="unresolved_high_finding", points=8),
        PenaltyRule(reason="unverified_claim", points=3),
        PenaltyRule(reason="missing_official_source", points=10),
        PenaltyRule(reason="unresolved_contradiction", points=10),
    ),
)


def default_rubric() -> ReadinessRubric:
    """A fresh copy of the default rubric (callers may then adjust it)."""
    return ReadinessRubric(
        criteria=READINESS_CRITERIA,
        weights=dict(DEFAULT_READINESS_RUBRIC.weights),
        penalties=tuple(DEFAULT_READINESS_RUBRIC.penalties),
    )


@dataclass(slots=True)
class ReadinessInputs:
    """Everything the readiness computation consumes (all deterministic)."""

    assessments: Sequence[ReadinessAssessment]
    unresolved_critical_count: int = 0
    unresolved_high_count: int = 0
    unverified_claim_count: int = 0
    missing_official_source: bool = False
    unresolved_contradiction_count: int = 0


@dataclass(slots=True)
class ReadinessResult:
    """The computed advisory readiness (JSON-deterministic)."""

    readiness: float
    per_criterion_median: dict[str, float]
    per_criterion_min: dict[str, int]
    per_criterion_max: dict[str, int]
    penalties_applied: dict[str, int]      # reason → points total
    participating_assessments: int
    rubric: dict[str, Any]
    disclaimer: str = READINESS_DISCLAIMER

    def to_dict(self) -> dict[str, Any]:
        return {
            "readiness": round(self.readiness, 1),
            "per_criterion_median": {
                k: round(v, 1) for k, v in self.per_criterion_median.items()
            },
            "per_criterion_min": dict(self.per_criterion_min),
            "per_criterion_max": dict(self.per_criterion_max),
            "penalties_applied": dict(self.penalties_applied),
            "participating_assessments": self.participating_assessments,
            "rubric": self.rubric,
            "disclaimer": self.disclaimer,
        }


def compute_readiness(
    inputs: ReadinessInputs, rubric: ReadinessRubric | None = None
) -> ReadinessResult:
    """Deterministic readiness: weighted medians − explicit penalties.

    Same inputs + same rubric ⇒ byte-identical result.  No single agent
    (including ASTRA) can declare a number: the score comes ONLY from the
    evaluators' bounded assessments and the counted penalties.
    """
    rubric = rubric or DEFAULT_READINESS_RUBRIC
    rubric.validate()
    if not inputs.assessments:
        raise ValueError(
            "readiness requires at least one evaluator readiness assessment."
        )

    per_criterion_median: dict[str, float] = {}
    per_criterion_min: dict[str, int] = {}
    per_criterion_max: dict[str, int] = {}
    for criterion in rubric.criteria:
        scores: list[int] = []
        for assessment in inputs.assessments:
            found = [
                c.score
                for c in assessment.criteria
                if c.criterion == criterion
            ]
            if found:
                scores.append(found[0])
        if not scores:
            # A criterion no evaluator scored: count it as a 0 with full
            # spread — missing internal evidence can never look perfect.
            per_criterion_median[criterion] = 0.0
            per_criterion_min[criterion] = 0
            per_criterion_max[criterion] = 0
            continue
        per_criterion_median[criterion] = float(median(scores))
        per_criterion_min[criterion] = min(scores)
        per_criterion_max[criterion] = max(scores)

    weighted = sum(
        rubric.weights[criterion] * per_criterion_median[criterion]
        for criterion in rubric.criteria
    )

    occurrences: dict[str, int] = {
        "unresolved_critical_finding": max(0, int(inputs.unresolved_critical_count)),
        "unresolved_high_finding": max(0, int(inputs.unresolved_high_count)),
        "unverified_claim": max(0, int(inputs.unverified_claim_count)),
        "missing_official_source": 1 if inputs.missing_official_source else 0,
        "unresolved_contradiction": max(0, int(inputs.unresolved_contradiction_count)),
    }
    penalties_applied: dict[str, int] = {}
    penalty_total = 0
    for rule in rubric.penalties:
        count = occurrences.get(rule.reason, 0)
        if count:
            points = rule.points * count
            penalties_applied[rule.reason] = points
            penalty_total += points

    readiness = max(0.0, min(MAX_READINESS, weighted - penalty_total))
    return ReadinessResult(
        readiness=readiness,
        per_criterion_median=per_criterion_median,
        per_criterion_min=per_criterion_min,
        per_criterion_max=per_criterion_max,
        penalties_applied=penalties_applied,
        participating_assessments=len(inputs.assessments),
        rubric=rubric.to_dict(),
    )
