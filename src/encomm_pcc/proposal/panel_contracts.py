"""Panel contracts — consensus round packet/parser + readiness assessments.

Session 019 (Proposal Factory V2), PURE proposal domain (imports nothing
from the runtime).  The consensus round IS the controlled "discussion"
(brief §19): every evaluator receives the frozen proposal, the source pack,
ALL independent first-pass outputs and the panel docket, and returns a
strict per-item judgement — no free-form chat between agents.

The output contract is a STRICT-MANDATORY envelope (same philosophy as the
review parser): exactly one START/END pair, JSON only, no fallback.  A bare
JSON object is ``missing_envelope`` even when syntactically perfect.

Per-item judgements are exactly one of::

    AGREE | DISAGREE | PARTIAL | INSUFFICIENT_EVIDENCE

plus rationale, proposed resolution, source refs and a
``blocks_acceptance`` flag.  The deterministic consensus MATRIX is built
from three of these responses — it reports agreement structure only and
never decides scientific truth by majority vote (brief §20).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping
from typing import Optional

from .enums import ProposalRole

__all__ = [
    "CONSENSUS_JUDGEMENTS",
    "MAX_CONSENSUS_ITEMS",
    "MAX_JUDGEMENT_RATIONALE_CHARS",
    "PANEL_CONSENSUS_ENVELOPE_END",
    "PANEL_CONSENSUS_ENVELOPE_START",
    "READINESS_CRITERIA",
    "ConsensusJudgement",
    "PanelConsensusInputs",
    "PanelConsensusPacket",
    "PanelConsensusParseError",
    "PanelConsensusResult",
    "ReadinessAssessment",
    "ReadinessCriterionScore",
    "build_panel_consensus_packet",
    "parse_panel_consensus",
]

#: Envelope markers for the consensus round (distinct from the review
#: envelope so a reviewer can never reuse the wrong template).
PANEL_CONSENSUS_ENVELOPE_START = "<<<ENCOMM_PANEL_CONSENSUS_START>>>"
PANEL_CONSENSUS_ENVELOPE_END = "<<<ENCOMM_PANEL_CONSENSUS_END>>>"

#: The only accepted per-item judgements (strict whitelist).
CONSENSUS_JUDGEMENTS: frozenset[str] = frozenset(
    {"AGREE", "DISAGREE", "PARTIAL", "INSUFFICIENT_EVIDENCE"}
)

#: Upper bound on docket items one consensus answer may judge.
MAX_CONSENSUS_ITEMS = 100

#: Per-field text bounds (mirrors the review parser's philosophy).
MAX_JUDGEMENT_RATIONALE_CHARS = 4_000
MAX_RESOLUTION_CHARS = 2_000

#: The eight internal readiness criteria (brief §22).  These are INTERNAL
#: advisory dimensions — never official EIC criteria.
READINESS_CRITERIA: tuple[str, ...] = (
    "scientific_coherence",
    "novelty_differentiation",
    "challenge_requirement_alignment",
    "impact_coherence",
    "implementation_coherence",
    "evidence_completeness",
    "internal_consistency",
    "evaluator_clarity",
)


class PanelConsensusParseError(ValueError):
    """The consensus answer was malformed — fail closed, never lenient."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"[{reason}] {message}")


@dataclass(slots=True)
class ConsensusJudgement:
    """One evaluator's judgement on ONE docket item."""

    item_id: str
    judgement: str  # AGREE / DISAGREE / PARTIAL / INSUFFICIENT_EVIDENCE
    rationale: str
    proposed_resolution: str = ""
    source_refs: list[str] = field(default_factory=list)
    blocks_acceptance: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "judgement": self.judgement,
            "rationale": self.rationale,
            "proposed_resolution": self.proposed_resolution,
            "source_refs": list(self.source_refs),
            "blocks_acceptance": self.blocks_acceptance,
        }


@dataclass(slots=True)
class ReadinessCriterionScore:
    """One evaluator's bounded score for ONE readiness criterion."""

    criterion: str
    score: int  # 0..100 inclusive
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion": self.criterion,
            "score": self.score,
            "rationale": self.rationale,
        }


@dataclass(slots=True)
class ReadinessAssessment:
    """One evaluator's rubric assessment (bounded 0–100 per criterion)."""

    criteria: list[ReadinessCriterionScore] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"criteria": [c.to_dict() for c in self.criteria]}


@dataclass(slots=True)
class PanelConsensusResult:
    """The strict parsed result of ONE consensus-round answer."""

    role: ProposalRole
    iteration_number: int
    proposal_hash: str
    judgements: list[ConsensusJudgement]
    readiness_assessment: ReadinessAssessment
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role.value,
            "iteration_number": self.iteration_number,
            "proposal_hash": self.proposal_hash,
            "judgements": [j.to_dict() for j in self.judgements],
            "readiness_assessment": self.readiness_assessment.to_dict(),
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PanelConsensusResult":
        try:
            role = ProposalRole(str(data.get("role") or ""))
        except ValueError as exc:
            raise ValueError(
                f"consensus result carries an unknown role: "
                f"{data.get('role')!r}"
            ) from exc
        assessment_raw = data.get("readiness_assessment") or {}
        criteria_raw = assessment_raw.get("criteria") or []
        return cls(
            role=role,
            iteration_number=int(data.get("iteration_number") or 0),
            proposal_hash=str(data.get("proposal_hash") or ""),
            judgements=[
                ConsensusJudgement(
                    item_id=str(j.get("item_id") or ""),
                    judgement=str(j.get("judgement") or ""),
                    rationale=str(j.get("rationale") or ""),
                    proposed_resolution=str(j.get("proposed_resolution") or ""),
                    source_refs=[str(r) for r in (j.get("source_refs") or [])],
                    blocks_acceptance=bool(j.get("blocks_acceptance") or False),
                )
                for j in (data.get("judgements") or [])
            ],
            readiness_assessment=ReadinessAssessment(
                criteria=[
                    ReadinessCriterionScore(
                        criterion=str(c.get("criterion") or ""),
                        score=int(c.get("score") or 0),
                        rationale=str(c.get("rationale") or ""),
                    )
                    for c in criteria_raw
                ]
            ),
            summary=str(data.get("summary") or ""),
        )


# ---------------------------------------------------------------------------
# packet
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class PanelConsensusInputs:
    """Explicit inputs for ONE consensus packet (all deterministic)."""

    role: ProposalRole
    iteration_number: int
    proposal_hash: str
    proposal_text: str
    first_pass_outputs_text: str   # rendered independent first-pass results
    panel_docket_text: str         # rendered docket (F/P ids)
    source_snapshot_text: str = ""
    proposal_revision: str = ""


@dataclass(slots=True)
class PanelConsensusPacket:
    role: ProposalRole
    prompt_text: str
    iteration_number: int
    proposal_hash: str


_PREAMBLE = """\
You are a proposal evaluator in the PANEL CONSENSUS round.

NON-NEGOTIABLE RULES — violating any single rule invalidates your answer:

A. You are READ-ONLY. You never open, create, modify or delete any file.
B. You judge EACH docket item independently and honestly. You MAY disagree
   with the finding's original reviewer — agreement is not obedience.
C. Your judgement for every item is exactly one of:
   AGREE | DISAGREE | PARTIAL | INSUFFICIENT_EVIDENCE
D. You never invent source facts; cite only the provided sources.
E. You never see or know the other evaluators' consensus answers — this
   round is an independent written judgement, not a chat.
F. You also provide your bounded readiness rubric assessment (0-100 per
   criterion). These are INTERNAL advisory scores, not official EIC scores.
G. Your entire deliverable is ONE JSON object inside the exact envelope
   required at the end of this prompt — nothing else is read.
"""

_OUTPUT_CONTRACT = """\
## OUTPUT CONTRACT (STRICT)

Return EXACTLY ONE JSON object between these exact markers:

<<<ENCOMM_PANEL_CONSENSUS_START>>>
{
  "role": "<YOUR_ROLE — echo it EXACTLY>",
  "iteration_number": <plain JSON integer — echo it EXACTLY>,
  "proposal_hash": "<echo the proposal hash EXACTLY>",
  "judgements": [
    {
      "item_id": "<docket id, e.g. F001 or P001>",
      "judgement": "AGREE|DISAGREE|PARTIAL|INSUFFICIENT_EVIDENCE",
      "rationale": "<bounded justification>",
      "proposed_resolution": "<your proposed resolution>",
      "source_refs": ["<source reference>"],
      "blocks_acceptance": <true|false — should this item block ASTRA acceptance>
    }
  ],
  "readiness_assessment": {
    "criteria": [
      {"criterion": "<criterion name>", "score": <0-100 integer>, "rationale": "<short>"}
    ]
  },
  "summary": "<one-paragraph panel position>"
}
<<<ENCOMM_PANEL_CONSENSUS_END>>>

Rules: every docket item appears exactly once in 'judgements'; 'score' is a
plain JSON integer 0-100; use ONLY the criterion names listed above; no
trailing commas; no comments; the envelope pair is mandatory.
"""


def build_panel_consensus_packet(
    inputs: PanelConsensusInputs,
) -> PanelConsensusPacket:
    """Render the deterministic consensus prompt for ``inputs.role``."""
    if inputs.role not in (
        ProposalRole.SCIENTIFIC_REVIEWER,
        ProposalRole.PROPOSAL_ENGINEER,
        ProposalRole.RED_TEAM_REVIEWER,
    ):
        raise ValueError(
            f"{inputs.role.value} is not an evaluator role."
        )
    if not inputs.proposal_text.strip():
        raise ValueError("proposal_text is required for a consensus packet.")
    if not inputs.proposal_hash.strip():
        raise ValueError("proposal_hash is required for a consensus packet.")
    if not isinstance(inputs.iteration_number, int) or isinstance(
        inputs.iteration_number, bool
    ) or inputs.iteration_number < 1:
        raise ValueError("iteration_number must be a positive integer.")
    criteria_list = ", ".join(f'"{c}"' for c in READINESS_CRITERIA)
    parts: list[str] = [
        "# PANEL CONSENSUS REQUEST",
        "",
        _PREAMBLE,
        "## CONSENSUS METADATA",
        "",
        f"- evaluator_role: {inputs.role.value}",
        f"- iteration_number: {inputs.iteration_number} (echo it EXACTLY)",
        f"- proposal_hash (SHA-256 of the exact frozen revision): "
        f"{inputs.proposal_hash}",
        f"- proposal_revision: {inputs.proposal_revision or 'UNSPECIFIED'}",
        "",
        "## FROZEN PROPOSAL UNDER REVIEW — 03_PROPOSAL/MASTER_PROPOSAL.md",
        "",
        inputs.proposal_text.strip(),
        "",
        "## INDEPENDENT FIRST-PASS PANEL OUTPUTS (all evaluators)",
        "",
        inputs.first_pass_outputs_text.strip() or "[UNAVAILABLE]",
        "",
        "## PANEL DOCKET (items you must judge)",
        "",
        inputs.panel_docket_text.strip() or "[NO DOCKET ITEMS]",
        "",
    ]
    if inputs.source_snapshot_text.strip():
        parts.extend(
            [
                "## SOURCE PACK (blueprint / template / official documents)",
                "",
                inputs.source_snapshot_text.strip(),
                "",
            ]
        )
    parts.extend(
        [
            "## READINESS CRITERIA (use ONLY these names)",
            "",
            criteria_list,
            "",
            _OUTPUT_CONTRACT,
        ]
    )
    return PanelConsensusPacket(
        role=inputs.role,
        prompt_text="\n".join(parts),
        iteration_number=inputs.iteration_number,
        proposal_hash=inputs.proposal_hash,
    )


# ---------------------------------------------------------------------------
# strict parser — no fallback, ever
# ---------------------------------------------------------------------------
def _candidate_objects(raw: str):
    """Extract the payload of the ONE mandatory consensus envelope.

    Same strict semantics as the review parser: prose AROUND a correct
    envelope is accepted; a bare JSON object is NOT; one-sided or multiple
    markers are rejected.
    """
    starts = raw.count(PANEL_CONSENSUS_ENVELOPE_START)
    ends = raw.count(PANEL_CONSENSUS_ENVELOPE_END)
    if starts == 0 and ends == 0:
        raise PanelConsensusParseError(
            "missing_envelope",
            f"'{PANEL_CONSENSUS_ENVELOPE_START}' ... "
            f"'{PANEL_CONSENSUS_ENVELOPE_END}' pair is required; bare JSON "
            "or prose is never accepted.",
        )
    if starts != 1 or ends != 1:
        raise PanelConsensusParseError(
            "unterminated_envelope",
            f"malformed consensus envelope ({starts} START / {ends} END "
            "markers); exactly one of each is required.",
        )
    inner = raw.split(PANEL_CONSENSUS_ENVELOPE_START, 1)[1].split(
        PANEL_CONSENSUS_ENVELOPE_END, 1
    )[0]
    inner = inner.strip()
    if inner.startswith("```"):
        inner = inner.strip("`").strip()
        if inner.lower().startswith("json"):
            inner = inner[4:].strip()
    if not inner:
        raise PanelConsensusParseError(
            "no_json_object",
            "the consensus envelope contained no JSON object.",
        )
    yield inner


def _bounded_str(value: Any, *, limit: int, field_name: str) -> str:
    if not isinstance(value, str):
        raise PanelConsensusParseError(
            "unexpected_type",
            f"'{field_name}' must be a string, got {type(value).__name__}",
        )
    if len(value) > limit:
        raise PanelConsensusParseError(
            "oversized_field",
            f"'{field_name}' exceeds {limit} characters ({len(value)})",
        )
    return value


def _validate_judgements(raw: Any) -> list[ConsensusJudgement]:
    if not isinstance(raw, list):
        raise PanelConsensusParseError(
            "unexpected_type", "'judgements' must be a list"
        )
    if len(raw) > MAX_CONSENSUS_ITEMS:
        raise PanelConsensusParseError(
            "oversized_list",
            f"'judgements' exceeds {MAX_CONSENSUS_ITEMS} entries ({len(raw)})",
        )
    if not raw:
        raise PanelConsensusParseError(
            "empty_judgements",
            "a consensus answer must judge at least one docket item.",
        )
    seen: set[str] = set()
    judgements: list[ConsensusJudgement] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise PanelConsensusParseError(
                "unexpected_type", "each judgement must be an object"
            )
        item_id = _bounded_str(
            item.get("item_id"), limit=32, field_name="item_id"
        ).strip()
        if not item_id:
            raise PanelConsensusParseError(
                "empty_field", "'item_id' must not be empty"
            )
        if item_id in seen:
            raise PanelConsensusParseError(
                "duplicate_item_id",
                f"docket item {item_id!r} judged more than once",
            )
        seen.add(item_id)
        judgement_raw = item.get("judgement")
        if not isinstance(judgement_raw, str):
            raise PanelConsensusParseError(
                "unexpected_type", "'judgement' must be a string"
            )
        judgement_value = judgement_raw.strip().upper()
        if judgement_value not in CONSENSUS_JUDGEMENTS:
            raise PanelConsensusParseError(
                "invalid_judgement",
                "judgement must be one of "
                f"{sorted(CONSENSUS_JUDGEMENTS)}, got {judgement_raw!r}",
            )
        blocks = item.get("blocks_acceptance", False)
        if not isinstance(blocks, bool):
            raise PanelConsensusParseError(
                "unexpected_type", "'blocks_acceptance' must be a boolean"
            )
        refs_raw = item.get("source_refs") or []
        if not isinstance(refs_raw, list):
            raise PanelConsensusParseError(
                "unexpected_type", "'source_refs' must be a list"
            )
        judgements.append(
            ConsensusJudgement(
                item_id=item_id,
                judgement=judgement_value,
                rationale=_bounded_str(
                    item.get("rationale"),
                    limit=MAX_JUDGEMENT_RATIONALE_CHARS,
                    field_name="rationale",
                ),
                proposed_resolution=_bounded_str(
                    item.get("proposed_resolution") or "",
                    limit=MAX_RESOLUTION_CHARS,
                    field_name="proposed_resolution",
                ),
                source_refs=[
                    _bounded_str(r, limit=500, field_name="source_refs[]")
                    for r in refs_raw
                ],
                blocks_acceptance=blocks,
            )
        )
    return judgements


def _validate_assessment(raw: Any) -> ReadinessAssessment:
    if raw is None:
        return ReadinessAssessment(criteria=[])
    if not isinstance(raw, Mapping):
        raise PanelConsensusParseError(
            "unexpected_type", "'readiness_assessment' must be an object"
        )
    criteria_raw = raw.get("criteria")
    if not isinstance(criteria_raw, list):
        raise PanelConsensusParseError(
            "unexpected_type",
            "'readiness_assessment.criteria' must be a list",
        )
    criteria: list[ReadinessCriterionScore] = []
    seen: set[str] = set()
    for item in criteria_raw:
        if not isinstance(item, Mapping):
            raise PanelConsensusParseError(
                "unexpected_type", "each criterion must be an object"
            )
        name = _bounded_str(
            item.get("criterion"), limit=80, field_name="criterion"
        ).strip()
        if name not in READINESS_CRITERIA:
            raise PanelConsensusParseError(
                "invalid_criterion",
                f"criterion {name!r} is not one of {list(READINESS_CRITERIA)}",
            )
        if name in seen:
            raise PanelConsensusParseError(
                "duplicate_criterion",
                f"criterion {name!r} scored more than once",
            )
        seen.add(name)
        score = item.get("score")
        if isinstance(score, bool) or not isinstance(score, int):
            raise PanelConsensusParseError(
                "unexpected_type",
                f"score for '{name}' must be a plain JSON integer",
            )
        if score < 0 or score > 100:
            raise PanelConsensusParseError(
                "score_out_of_range",
                f"score for '{name}' must be 0-100, got {score}",
            )
        criteria.append(
            ReadinessCriterionScore(
                criterion=name,
                score=score,
                rationale=_bounded_str(
                    item.get("rationale") or "",
                    limit=MAX_RESOLUTION_CHARS,
                    field_name="criterion rationale",
                ),
            )
        )
    return ReadinessAssessment(criteria=criteria)


def parse_panel_consensus(
    raw: str,
    *,
    expected_role: ProposalRole,
    expected_iteration: int,
    expected_proposal_hash: str,
) -> PanelConsensusResult:
    """Parse untrusted consensus output — fail closed on ANY deviation."""
    if raw is None or not isinstance(raw, str):
        raise PanelConsensusParseError(
            "unexpected_type", "consensus output must be text"
        )
    if not str(expected_proposal_hash or "").strip():
        raise PanelConsensusParseError(
            "invalid_expected_hash", "expected_proposal_hash is required."
        )
    if not isinstance(expected_iteration, int) or isinstance(
        expected_iteration, bool
    ) or expected_iteration < 1:
        raise PanelConsensusParseError(
            "invalid_expected_iteration",
            "expected_iteration must be a positive integer.",
        )
    for candidate in _candidate_objects(raw):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise PanelConsensusParseError(
                "malformed_json", f"unparseable JSON object: {exc.msg}"
            ) from exc
        if not isinstance(parsed, Mapping):
            raise PanelConsensusParseError(
                "unexpected_type",
                f"the JSON payload must be an object, got "
                f"{type(parsed).__name__}",
            )
        role_raw = parsed.get("role")
        if not isinstance(role_raw, str) or role_raw.strip() != expected_role.value:
            raise PanelConsensusParseError(
                "role_mismatch",
                f"consensus answer role {role_raw!r} != expected "
                f"{expected_role.value!r}",
            )
        iteration = parsed.get("iteration_number")
        if isinstance(iteration, bool) or iteration != expected_iteration:
            raise PanelConsensusParseError(
                "iteration_mismatch",
                f"consensus iteration {iteration!r} != expected "
                f"{expected_iteration}",
            )
        proposal_hash = parsed.get("proposal_hash")
        if (
            not isinstance(proposal_hash, str)
            or proposal_hash.strip().lower()
            != expected_proposal_hash.strip().lower()
        ):
            raise PanelConsensusParseError(
                "proposal_hash_mismatch",
                "consensus answer does not echo the frozen proposal hash.",
            )
        return PanelConsensusResult(
            role=expected_role,
            iteration_number=expected_iteration,
            proposal_hash=proposal_hash.strip().lower(),
            judgements=_validate_judgements(parsed.get("judgements")),
            readiness_assessment=_validate_assessment(
                parsed.get("readiness_assessment")
            ),
            summary=_bounded_str(
                parsed.get("summary") or "",
                limit=4_000,
                field_name="summary",
            ),
        )
    raise PanelConsensusParseError(
        "no_json_object", "the consensus envelope contained no JSON object."
    )
