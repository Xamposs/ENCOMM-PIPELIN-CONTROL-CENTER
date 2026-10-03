"""Deterministic reviewer prompt packet — PURE proposal domain.

Builds the full review prompt for ONE reviewer operation from explicit
inputs.  The packet knows NOTHING about engines, drivers, CLI tools or
providers (no Hermes, Codex, Claude, GLM, MiniMax, OpenRouter): it renders
text, and the ``proposal_runtime`` package is the only bridge that feeds the
rendered prompt into a driver.

Non-negotiable reviewer rules are embedded in EVERY rendered prompt:

* the reviewer is READ-ONLY and must never edit
  ``03_PROPOSAL/MASTER_PROPOSAL.md`` (write authority is the ORCHESTRATOR's
  alone, during INTEGRATION);
* the reviewer has no integration authority — it returns findings and patch
  PROPOSALS only (:class:`encomm_pcc.proposal.ProposalPatch` is a proposal,
  never an edit);
* source-of-truth facts must never be invented or silently changed;
* unsupported factual/scientific claims must be marked as unverified;
* citations must never be invented;
* findings must be classified (factual contradiction / missing evidence /
  weak wording / structural issue / recommendation);
* the reviewer obeys its specific :class:`ProposalRole` focus.

Rendering is DETERMINISTIC: identical inputs always produce byte-identical
output (sections are emitted in fixed order; there are no timestamps, ids or
environment details in the packet).

The strict output contract (envelope markers + JSON schema) is defined here
once and shared with the fail-closed parser
(``proposal.review_parser`` imports these constants) so the prompt and the
parser can never drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .enums import ProposalRole

__all__ = [
    "MAX_PACKET_SECTION_CHARS",
    "PROPOSAL_REVIEW_ENVELOPE_END",
    "PROPOSAL_REVIEW_ENVELOPE_START",
    "REVIEWER_ROLE_FOCUS",
    "ProposalReviewInputs",
    "ProposalReviewPacket",
    "build_review_packet",
]

#: Delimiter pair the reviewer prompt requires.  Defined ONCE, here; the
#: fail-closed parser (``proposal.review_parser``) imports these exact
#: constants, so prompt and parser share one definition.
PROPOSAL_REVIEW_ENVELOPE_START = "<<<ENCOMM_PROPOSAL_REVIEW_START>>>"
PROPOSAL_REVIEW_ENVELOPE_END = "<<<ENCOMM_PROPOSAL_REVIEW_END>>>"

#: Per-section render cap (characters).  Bounded by construction: the packet
#: refuses to embed an unbounded source document into a prompt.
MAX_PACKET_SECTION_CHARS = 400_000

#: Shared read-only / integrity preamble (identical for every role).
_PREAMBLE = """\
You are a proposal reviewer operating in STRICT READ-ONLY mode.

NON-NEGOTIABLE RULES — violating any single rule invalidates your answer:

A. You are READ-ONLY.  You do not open, create, modify or delete any file.
B. You MUST NOT edit 03_PROPOSAL/MASTER_PROPOSAL.md.  Nobody authorised you
   to write it; the ONLY write authority over that file is the Proposal
   ORCHESTRATOR during the INTEGRATION phase.
C. You have NO integration authority.  You cannot accept, apply or reject
   patches; you can only propose them.
D. You return findings and patch PROPOSALS only, inside the exact JSON
   envelope required at the end of this prompt.  Your entire deliverable is
   that one JSON object.
E. Source-of-truth facts must not be invented or silently changed.  If a
   fact conflicts with the source-of-truth documents, report it as a
   finding; never "fix" the fact itself.
F. Unsupported factual/scientific claims must be marked as unverified and
   listed under 'unverified_claims'.
G. Citations must never be invented.  If a citation cannot be supported
   from the provided sources, report it — never fabricate a reference.
H. Every finding must be classified as exactly one of:
   factual_contradiction | missing_evidence | weak_wording |
   structural_issue | recommendation.
I. You obey YOUR role focus below — review what your role owns, and say so.
"""

#: Output contract — the exact JSON envelope and its schema.
_OUTPUT_CONTRACT = """\
REQUIRED OUTPUT — EXACTLY ONE JSON object wrapped in EXACTLY these markers:

<<<ENCOMM_PROPOSAL_REVIEW_START>>>
{
  "reviewer_role": "<your exact role name>",
  "verdict": "PASS" | "NEEDS_REVISION" | "BLOCKED",
  "summary": "<bounded overall assessment>",
  "findings": [
    {
      "severity": "critical" | "high" | "medium" | "low",
      "category": "factual_contradiction | missing_evidence | weak_wording | structural_issue | recommendation",
      "section": "<proposal section the finding applies to>",
      "message": "<what is wrong and why it matters>",
      "evidence": "<quoted evidence or empty>",
      "source_refs": ["<source document references or empty list>"],
      "suggested_change": "<what should change, or empty>"
    }
  ],
  "proposed_patches": [
    {
      "target_section": "<section the patch proposes to change>",
      "rationale": "<why the change is needed>",
      "replacement_text": "<full replacement text, or empty>",
      "patch_instructions": "<structured instructions, or empty>",
      "source_refs": ["<supporting sources or empty list>"],
      "confidence": <number between 0.0 and 1.0>
    }
  ],
  "unverified_claims": ["<claims you could not verify>"],
  "iteration_number": <the iteration_number given in this prompt, as a plain JSON integer>
}
<<<ENCOMM_PROPOSAL_REVIEW_END>>>

HARD OUTPUT RULES:
- Output the envelope markers and NOTHING ELSE outside them: no preamble,
  no prose, no second envelope, no Markdown fences.
- 'verdict' must be exactly PASS, NEEDS_REVISION or BLOCKED.
- A PASS verdict may NOT carry any critical or high finding and may NOT
  list any unverified claim.  If such issues exist, the verdict is
  NEEDS_REVISION (actionable issues) or BLOCKED (you cannot review at all).
- NEEDS_REVISION requires at least one actionable finding or patch.
- BLOCKED requires a finding (or summary) stating the concrete blocking
  reason.
- Severity values are lowercase: critical, high, medium, low.
- Numbers are plain JSON numbers; strings are bounded (keep every string
  under ~4000 characters; keep findings/patches/claims lists compact).
- Use forward slashes in any path-like reference inside JSON strings;
  raw backslash escapes invalidate JSON and the whole answer is rejected.
"""


@dataclass(frozen=True, slots=True)
class ProposalReviewInputs:
    """Explicit inputs for ONE reviewer packet.

    ``proposal_text`` is REQUIRED (a review without the proposal is
    impossible); every source-of-truth section is OPTIONAL — absent sources
    are rendered as explicitly unavailable so the reviewer knows it cannot
    claim full compliance, and can never invent their content.

    ``official_requirements_text`` is the canonical plain-text/Markdown
    rendering of ``01_OFFICIAL/OFFICIAL_REQUIREMENTS.md`` (no PDF/RTF
    ingestion exists); ``official_requirements_available`` states honestly
    whether it was provided.
    """

    proposal_text: str
    iteration_number: int
    proposal_revision: str = ""
    proposal_hash: str = ""
    master_blueprint_text: str = ""
    project_facts_text: str = ""
    team_text: str = ""
    architecture_text: str = ""
    terminology_text: str = ""
    official_requirements_text: str = ""
    official_requirements_available: bool = False
    previous_findings: tuple[str, ...] = ()
    extra_instructions: str = ""


#: Role-specific review focus (rule I).  Keyed by the three reviewer roles.
REVIEWER_ROLE_FOCUS: dict[ProposalRole, str] = {
    ProposalRole.SCIENTIFIC_REVIEWER: (
        "ROLE FOCUS — SCIENTIFIC_REVIEWER.  Review ONLY:\n"
        "- scientific coherence of the proposal\n"
        "- novelty and state-of-the-art differentiation\n"
        "- methodology soundness\n"
        "- assumptions and their plausibility\n"
        "- Challenge alignment (ONLY when official requirements are available;\n"
        "  if they are unavailable, state that official compliance cannot be\n"
        "  claimed and mark Challenge alignment findings accordingly)"
    ),
    ProposalRole.PROPOSAL_ENGINEER: (
        "ROLE FOCUS — PROPOSAL_ENGINEER.  Review ONLY:\n"
        "- work-plan structure (WPs and tasks)\n"
        "- deliverables and milestones\n"
        "- risks and their mitigation\n"
        "- resource/person-month consistency\n"
        "- implementation coherence and cross-section consistency\n"
        "(structures must match each other: every deliverable traces to a WP\n"
        "task, every milestone to a deliverable, person-months to the team)"
    ),
    ProposalRole.RED_TEAM_REVIEWER: (
        "ROLE FOCUS — RED_TEAM_REVIEWER.  Attack the proposal like a hostile\n"
        "evaluator.  Review ONLY:\n"
        "- unsupported or overclaimed statements\n"
        "- ambiguity an evaluator could misread\n"
        "- internal contradictions\n"
        "- missing evidence for load-bearing claims\n"
        "- readability and evaluator-confusion risks"
    ),
}


class ProposalReviewPacket:
    """One rendered reviewer prompt (deterministic; plain frozen text)."""

    __slots__ = ("role", "prompt_text", "iteration_number", "proposal_hash")

    def __init__(
        self,
        *,
        role: ProposalRole,
        prompt_text: str,
        iteration_number: int,
        proposal_hash: str,
    ) -> None:
        self.role = role
        self.prompt_text = prompt_text
        self.iteration_number = iteration_number
        self.proposal_hash = proposal_hash

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ProposalReviewPacket):
            return NotImplemented
        return (
            self.role == other.role
            and self.prompt_text == other.prompt_text
            and self.iteration_number == other.iteration_number
            and self.proposal_hash == other.proposal_hash
        )

    def __hash__(self) -> int:  # pragma: no cover - consistency only
        return hash((self.role, self.prompt_text, self.iteration_number, self.proposal_hash))

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"ProposalReviewPacket(role={self.role.value!r}, "
            f"iteration={self.iteration_number}, "
            f"prompt_chars={len(self.prompt_text)})"
        )


def _section(title: str, body: Optional[str]) -> str:
    """Render one labelled section, or its explicit unavailable marker."""
    if body is None or not body.strip():
        return f"## {title}\n\n[UNAVAILABLE — not provided.  Do NOT invent content for this section.]\n"
    text = body
    if len(text) > MAX_PACKET_SECTION_CHARS:
        raise ValueError(
            f"packet section '{title}' exceeds {MAX_PACKET_SECTION_CHARS} characters "
            f"({len(text)}); refusing to embed unbounded input."
        )
    return f"## {title}\n\n{text.strip()}\n"


def _section_with_cap(
    title: str, body: Optional[str], *, max_chars: int
) -> str:
    """``_section`` with an explicit per-call cap (Session 019 budget seam).

    ``max_chars=None`` callers use ``_section``; this helper applies the
    given cap so a raised budget flows through without touching other
    sections.
    """
    if body is None or not body.strip():
        return _section(title, None)
    if len(body) > max_chars:
        raise ValueError(
            f"packet section '{title}' exceeds {max_chars} characters "
            f"({len(body)}); refusing to embed unbounded input."
        )
    return f"## {title}\n\n{body.strip()}\n"


def build_review_packet(
    role: ProposalRole,
    inputs: ProposalReviewInputs,
    *,
    blueprint_max_chars: int | None = None,
) -> ProposalReviewPacket:
    """Render the deterministic review prompt for ``role``.

    Session 019: ``blueprint_max_chars`` raises ONLY the MASTER BLUEPRINT
    section's render cap (default: the historical 400,000 per-section cap)
    so a large canonical blueprint reaches reviewers whole when the
    configured source budget allows it.  Every other section keeps the
    standard cap.

    Raises ``ValueError`` when the role is not one of the three reviewer
    roles (the ORCHESTRATOR is the integration authority, never a reviewer),
    when the proposal text is missing, or when the iteration number is not a
    positive integer.
    """
    if not isinstance(role, ProposalRole):
        role = ProposalRole(str(role))
    if role not in REVIEWER_ROLE_FOCUS:
        raise ValueError(
            f"{role.value} is not a reviewer role: the ORCHESTRATOR holds "
            "integration authority and must never be given a reviewer packet."
        )
    if not inputs.proposal_text or not inputs.proposal_text.strip():
        raise ValueError("proposal_text is required for a review packet.")
    if not isinstance(inputs.iteration_number, int) or isinstance(
        inputs.iteration_number, bool
    ) or inputs.iteration_number < 1:
        raise ValueError("iteration_number must be a positive integer.")

    if inputs.official_requirements_available and not (
        inputs.official_requirements_text or ""
    ).strip():
        raise ValueError(
            "official_requirements_available=True requires "
            "official_requirements_text."
        )
    blueprint_cap = (
        MAX_PACKET_SECTION_CHARS
        if blueprint_max_chars is None
        else max(1, int(blueprint_max_chars))
    )

    previous = (
        "\n".join(f"- {line.strip()}" for line in inputs.previous_findings if line.strip())
        if inputs.previous_findings
        else ""
    )

    parts: list[str] = [
        "# PROPOSAL REVIEW REQUEST",
        "",
        _PREAMBLE,
        REVIEWER_ROLE_FOCUS[role],
        "",
        "## REVIEW METADATA",
        "",
        f"- reviewer_role: {role.value}",
        f"- iteration_number: {inputs.iteration_number} (echo it EXACTLY as a plain JSON integer)",
        f"- proposal_revision: {inputs.proposal_revision or 'UNSPECIFIED'}",
        f"- proposal_hash (SHA-256 of the exact reviewed revision): {inputs.proposal_hash or 'UNSPECIFIED'}",
        "",
        "## PROPOSAL UNDER REVIEW — 03_PROPOSAL/MASTER_PROPOSAL.md (READ-ONLY for you)",
        "",
        inputs.proposal_text.strip(),
        "",
        _section_with_cap(
            "SOURCE OF TRUTH — MASTER_BLUEPRINT.md",
            inputs.master_blueprint_text,
            max_chars=blueprint_cap,
        ),
        _section("SOURCE OF TRUTH — PROJECT_FACTS.md", inputs.project_facts_text),
        _section("SOURCE OF TRUTH — TEAM.md", inputs.team_text),
        _section("SOURCE OF TRUTH — ARCHITECTURE.md", inputs.architecture_text),
        _section("SOURCE OF TRUTH — TERMINOLOGY.md", inputs.terminology_text),
    ]

    if inputs.official_requirements_available:
        parts.append(
            _section(
                "OFFICIAL REQUIREMENTS (provided)",
                inputs.official_requirements_text,
            )
        )
    else:
        parts.append(
            _section("OFFICIAL REQUIREMENTS", None)
        )
        parts.append(
            "NOTE: official requirements are UNAVAILABLE in this run.  You "
            "CANNOT claim full official/Challenge compliance.  If compliance "
            "assessment is essential to your verdict, return BLOCKED with the "
            "missing requirements as the blocking reason.\n"
        )

    parts.append(
        _section(
            "PREVIOUS FINDINGS (earlier iterations)",
            previous if previous else None,
        )
    )

    if inputs.extra_instructions and inputs.extra_instructions.strip():
        parts.append(
            _section("EXTRA OPERATOR INSTRUCTIONS", inputs.extra_instructions)
        )

    parts.append(_OUTPUT_CONTRACT)

    return ProposalReviewPacket(
        role=role,
        prompt_text="\n".join(parts),
        iteration_number=inputs.iteration_number,
        proposal_hash=inputs.proposal_hash,
    )
