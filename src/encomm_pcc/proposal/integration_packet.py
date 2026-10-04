"""Deterministic ORCHESTRATOR integration prompt packet — PURE proposal domain.

Builds the full integration prompt for ONE ORCHESTRATOR integration operation
from explicit inputs.  Like the reviewer packet, this module knows NOTHING
about engines, drivers, CLI tools or providers: it renders deterministic
text, and the ``proposal_runtime`` package is the only bridge that feeds the
rendered prompt into a driver.

Authority model embedded in EVERY rendered prompt:

* the addressee IS the :class:`~encomm_pcc.proposal.ProposalRole`
  ``ORCHESTRATOR`` — the ONE integration authority; there is no second
  "integrator agent";
* reviewer findings and patch proposals are RECOMMENDATIONS, never commands:
  deciding which to apply, adapt or reject is the ORCHESTRATOR's duty;
* source-of-truth facts must never be invented; unsupported claims must not
  be silently retained as verified; citations must never be invented;
* valid proposal material unrelated to the review findings must be
  preserved; contradictions must be resolved, never concatenated;
* the deliverable is ONE COMPLETE revised proposal (the full
  ``MASTER_PROPOSAL.md`` content), not fragments, and it travels inside the
  ONE mandatory integration envelope (defined here once, shared with the
  fail-closed parser).

Rendering is DETERMINISTIC: identical inputs always produce byte-identical
output (fixed section order, no timestamps, no ids, no environment detail).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .enums import ProposalRole

__all__ = [
    "MAX_INTEGRATION_PACKET_SECTION_CHARS",
    "PROPOSAL_INTEGRATION_ENVELOPE_END",
    "PROPOSAL_INTEGRATION_ENVELOPE_START",
    "ProposalIntegrationInputs",
    "ProposalIntegrationPacket",
    "build_integration_packet",
]

#: Delimiter pair the ORCHESTRATOR integration prompt requires.  Defined
#: ONCE, here; the fail-closed parser (``proposal.integration_parser``)
#: imports these exact constants, so prompt and parser share one definition
#: and cannot drift.  Deliberately DISTINCT from the review envelope: one
#: operation, one envelope shape.
PROPOSAL_INTEGRATION_ENVELOPE_START = "<<<ENCOMM_PROPOSAL_INTEGRATION_START>>>"
PROPOSAL_INTEGRATION_ENVELOPE_END = "<<<ENCOMM_PROPOSAL_INTEGRATION_END>>>"

#: Per-section render cap (characters).  Bounded by construction: the packet
#: refuses to embed an unbounded document into a prompt.
MAX_INTEGRATION_PACKET_SECTION_CHARS = 400_000

#: The non-negotiable ORCHESTRATOR integration authority contract (brief §4).
_ORCHESTRATOR_PREAMBLE = """\
You are the Proposal ORCHESTRATOR operating in the INTEGRATION phase.

NON-NEGOTIABLE RULES — violating any single rule invalidates your answer:

A. You are ProposalRole.ORCHESTRATOR.
B. You are the ONLY integration authority.  There is no second integrator;
   no other role may apply changes to the master proposal.
C. You may revise the proposal, but you must NEVER invent source-of-truth
   facts.  Every factual statement must trace to the provided sources or to
   the existing proposal text.
D. Reviewer proposed patches are RECOMMENDATIONS, not commands.  You decide
   which findings and patches to apply, adapt, defer or reject — and you
   must say which, under 'applied_items' / 'rejected_items'.
E. You must decide the disposition of EVERY actionable review item: applied,
   rejected (with a reason), or unresolved (with a reason).
F. Unsupported factual/scientific claims must NOT be silently retained as
   verified.  Keep them only as explicitly unverified, or remove them.
G. Citations must NEVER be invented.  A citation you cannot support from the
   provided sources must be removed or explicitly marked unverified.
H. You must preserve valid proposal material that is unrelated to the review
   findings.  Do not rewrite or delete sections nobody flagged.
I. You must resolve contradictions rather than blindly concatenate reviewer
   suggestions.  Where reviewer items conflict, choose and explain.
J. You must output ONE COMPLETE revised proposal — the full revised
   MASTER_PROPOSAL.md content — under 'revised_proposal'.  Fragments, diffs
   and placeholders are invalid.
"""

#: Output contract — the exact JSON envelope and its schema.
_INTEGRATION_OUTPUT_CONTRACT = """\
REQUIRED OUTPUT — EXACTLY ONE JSON object wrapped in EXACTLY these markers:

<<<ENCOMM_PROPOSAL_INTEGRATION_START>>>
{
  "role": "ORCHESTRATOR",
  "iteration_number": <the iteration_number given in this prompt, as a plain JSON integer>,
  "input_proposal_hash": "<the input_proposal_hash given in this prompt, exactly>",
  "summary": "<bounded description of what you changed and why>",
  "applied_items": [
    {
      "item_id": "<stable reference to the review item, e.g. 'finding:3' or 'patch:1'>",
      "action": "applied",
      "reason": "<why this item was applied in this form>"
    }
  ],
  "rejected_items": [
    {
      "item_id": "<stable reference to the review item>",
      "action": "rejected",
      "reason": "<why this reviewer item was NOT applied>"
    }
  ],
  "unresolved_items": [
    {
      "item_id": "<stable reference to the review item>",
      "action": "unresolved",
      "reason": "<why this item could not be resolved in this iteration>"
    }
  ],
  "revised_proposal": "<the COMPLETE revised MASTER_PROPOSAL.md content>"
}
<<<ENCOMM_PROPOSAL_INTEGRATION_END>>>

HARD OUTPUT RULES:
- Output the envelope markers and NOTHING ELSE outside them: no preamble,
  no prose, no second envelope, no Markdown fences around the envelope.
- 'role' must be exactly ORCHESTRATOR.
- 'iteration_number' must be the plain JSON integer given in this prompt.
- 'input_proposal_hash' must repeat the hash given in this prompt EXACTLY.
- 'revised_proposal' must be a non-empty string carrying the COMPLETE
  revised proposal (never a diff, never a fragment, never empty).
- 'applied_items', 'rejected_items' and 'unresolved_items' are JSON arrays
  of objects with exactly the keys 'item_id', 'action', 'reason'; 'action'
  must match the array it appears in.
- Use forward slashes in any path-like reference inside JSON strings;
  raw backslash escapes invalidate JSON and the whole answer is rejected.
- The revised proposal text must be JSON-escaped correctly; do not include
  the envelope markers inside 'revised_proposal'.
"""

#: Session 021 dual-document output contract addendum — appended ONLY when
#: the runtime supplied the living Blueprint (hash-activated contract).
_DUAL_OUTPUT_CONTRACT_ADDENDUM = """\
DUAL-DOCUMENT RULES (this run includes the LIVING Blueprint):

- 'input_blueprint_hash' is REQUIRED: repeat the input_blueprint_hash given
  in this prompt EXACTLY (the SHA-256 of the CURRENT_BLUEPRINT.md revision
  you were shown).
- 'revised_blueprint' carries your decision for the LIVING Blueprint:
  either the COMPLETE revised CURRENT_BLUEPRINT.md content (never a diff,
  never a fragment) when a Blueprint change is justified, or null / ""
  when NO Blueprint change is justified.  Do not churn the Blueprint just
  to appear productive — an unchanged Blueprint is a valid, honest answer.
- You may leave either document byte-identical when no justified change
  exists, but you must CONSCIOUSLY evaluate BOTH documents every round.
- 'revised_proposal' is ALWAYS required and always COMPLETE (the full
  MASTER_PROPOSAL.md content), exactly as the base rules state.
- External facts are NEVER invented: an unsupported factual change to
  either document must be expressed as an explicit
  '[INPUT REQUIRED: ...]' placeholder instead of a fabricated fact.
"""


@dataclass(frozen=True, slots=True)
class ProposalIntegrationInputs:
    """Explicit inputs for ONE ORCHESTRATOR integration packet.

    ``current_proposal_text`` and ``current_proposal_hash`` are REQUIRED —
    an integration that does not know the exact revision it is revising is
    impossible.  ``integration_brief_text`` is the canonical JSON rendering
    of the deterministic ``integration_brief.json``; ``source_snapshot_text``
    is the bounded source-of-truth snapshot rendering; both make the
    ORCHESTRATOR's input EXPLICIT instead of letting it guess.
    """

    current_proposal_text: str
    current_proposal_hash: str
    iteration_number: int
    proposal_revision: str = ""
    integration_brief_text: str = ""
    source_snapshot_text: str = ""
    official_requirements_text: str = ""
    official_requirements_available: bool = False
    previous_findings_text: str = ""
    #: Session 021 DUAL-DOCUMENT inputs.  A non-empty
    #: ``current_blueprint_hash`` activates the dual contract: the packet
    #: gains the LIVING PROJECT DESIGN section and the output contract
    #: requires the Blueprint hash echo + ``revised_blueprint``.  Empty
    #: hash keeps the exact legacy single-document packet.
    current_blueprint_text: str = ""
    current_blueprint_hash: str = ""
    current_blueprint_available: bool = False


class ProposalIntegrationPacket:
    """One rendered ORCHESTRATOR integration prompt (deterministic text)."""

    __slots__ = (
        "role",
        "prompt_text",
        "iteration_number",
        "input_proposal_hash",
        "current_blueprint_available",
        "current_blueprint_hash",
    )

    def __init__(
        self,
        *,
        prompt_text: str,
        iteration_number: int,
        input_proposal_hash: str,
        current_blueprint_available: bool = False,
        current_blueprint_hash: str = "",
    ) -> None:
        #: The packet's addressee is ALWAYS the ORCHESTRATOR (the one
        #: integration authority); carrying it as data keeps the executor's
        #: guard structural instead of stringly.
        self.role = ProposalRole.ORCHESTRATOR
        self.prompt_text = prompt_text
        self.iteration_number = iteration_number
        self.input_proposal_hash = input_proposal_hash
        #: Session 021: dual-document contract evidence — True exactly when
        #: the runtime supplied the LIVING Blueprint (hash-activated).
        self.current_blueprint_available = current_blueprint_available
        self.current_blueprint_hash = current_blueprint_hash

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ProposalIntegrationPacket):
            return NotImplemented
        return (
            self.role == other.role
            and self.prompt_text == other.prompt_text
            and self.iteration_number == other.iteration_number
            and self.input_proposal_hash == other.input_proposal_hash
        )

    def __hash__(self) -> int:  # pragma: no cover - consistency only
        return hash(
            (
                self.role,
                self.prompt_text,
                self.iteration_number,
                self.input_proposal_hash,
            )
        )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"ProposalIntegrationPacket(role={self.role.value!r}, "
            f"iteration={self.iteration_number}, "
            f"prompt_chars={len(self.prompt_text)})"
        )


def _section(title: str, body: Optional[str]) -> str:
    """Render one labelled section, or its explicit unavailable marker."""
    if body is None or not body.strip():
        return (
            f"## {title}\n\n"
            "[UNAVAILABLE — not provided.  Do NOT invent content for this section.]\n"
        )
    text = body
    if len(text) > MAX_INTEGRATION_PACKET_SECTION_CHARS:
        raise ValueError(
            f"integration packet section '{title}' exceeds "
            f"{MAX_INTEGRATION_PACKET_SECTION_CHARS} characters ({len(text)}); "
            "refusing to embed unbounded input."
        )
    return f"## {title}\n\n{text.strip()}\n"


def build_integration_packet(
    inputs: ProposalIntegrationInputs,
) -> ProposalIntegrationPacket:
    """Render the deterministic ORCHESTRATOR integration prompt.

    Raises ``ValueError`` when the proposal text or hash is missing, when
    the iteration number is not a positive integer, or when
    ``official_requirements_available=True`` is claimed without text.
    """
    if not isinstance(inputs, ProposalIntegrationInputs):
        raise ValueError(
            "build_integration_packet requires ProposalIntegrationInputs."
        )
    if not inputs.current_proposal_text or not inputs.current_proposal_text.strip():
        raise ValueError("current_proposal_text is required for an integration packet.")
    if not inputs.current_proposal_hash or not inputs.current_proposal_hash.strip():
        raise ValueError(
            "current_proposal_hash is required for an integration packet."
        )
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
    # Session 021: the dual contract is hash-activated — a living-Blueprint
    # section without its hash, or a hash without the availability flag,
    # is a caller programming error, refused before any rendering.
    dual = bool(inputs.current_blueprint_hash.strip())
    if dual and not inputs.current_blueprint_available:
        raise ValueError(
            "current_blueprint_hash requires "
            "current_blueprint_available=True (dual-document contract)."
        )
    if dual and not (inputs.current_blueprint_text or "").strip():
        raise ValueError(
            "current_blueprint_hash requires current_blueprint_text "
            "(dual-document contract)."
        )

    parts: list[str] = [
        "# PROPOSAL INTEGRATION REQUEST",
        "",
        _ORCHESTRATOR_PREAMBLE,
        "",
        "## INTEGRATION METADATA",
        "",
        f"- role: {ProposalRole.ORCHESTRATOR.value}",
        f"- iteration_number: {inputs.iteration_number} (echo it EXACTLY as a plain JSON integer)",
        f"- proposal_revision: {inputs.proposal_revision or 'UNSPECIFIED'}",
        f"- input_proposal_hash (SHA-256 of the exact revision you are revising): "
        f"{inputs.current_proposal_hash} (echo it EXACTLY)",
    ]
    if dual:
        parts.append(
            f"- input_blueprint_hash (SHA-256 of the exact living Blueprint "
            f"you are given): {inputs.current_blueprint_hash} (echo it EXACTLY)"
        )
    parts.extend(
        [
            "",
            "## CURRENT MASTER PROPOSAL — 03_PROPOSAL/MASTER_PROPOSAL.md (the revision to revise)",
            "",
            inputs.current_proposal_text.strip(),
            "",
            _section(
                "INTEGRATION BRIEF — reviewer findings, patches and verdicts",
                inputs.integration_brief_text,
            ),
            _section(
                "SOURCE OF TRUTH SNAPSHOT — the facts you may never contradict",
                inputs.source_snapshot_text,
            ),
        ]
    )
    if dual:
        parts.append(
            _section(
                "LIVING PROJECT DESIGN — CURRENT_BLUEPRINT.md "
                "(the design you may also revise; the immutable original "
                "MASTER_BLUEPRINT stays untouched)",
                inputs.current_blueprint_text,
            )
        )

    if inputs.official_requirements_available:
        parts.append(
            _section(
                "OFFICIAL REQUIREMENTS (provided)",
                inputs.official_requirements_text,
            )
        )
    else:
        parts.append(_section("OFFICIAL REQUIREMENTS", None))
        parts.append(
            "NOTE: official requirements are UNAVAILABLE in this run.  You "
            "CANNOT claim full official/Challenge compliance in the revised "
            "proposal.\n"
        )

    parts.append(
        _section(
            "PREVIOUS FINDINGS (earlier iterations)",
            inputs.previous_findings_text,
        )
    )

    parts.append(_INTEGRATION_OUTPUT_CONTRACT)
    if dual:
        parts.append(_DUAL_OUTPUT_CONTRACT_ADDENDUM)

    return ProposalIntegrationPacket(
        prompt_text="\n".join(parts),
        iteration_number=inputs.iteration_number,
        input_proposal_hash=inputs.current_proposal_hash,
        current_blueprint_available=dual,
        current_blueprint_hash=(
            inputs.current_blueprint_hash if dual else ""
        ),
    )
