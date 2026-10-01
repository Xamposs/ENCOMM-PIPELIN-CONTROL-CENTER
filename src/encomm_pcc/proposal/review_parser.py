"""Fail-closed parser for proposal reviewer output — PURE proposal domain.

The reviewer's answer is **untrusted model output**.  This module is the only
place that turns that text into a :class:`ProposalReviewResult`, and it fails
closed: anything malformed raises :class:`ProposalReviewParseError` — a
malformed answer can NEVER become PASS and is never coerced into a result.

Envelope.  The reviewer prompt requires exactly one delimited JSON object::

    <<<ENCOMM_PROPOSAL_REVIEW_START>>>
    { "reviewer_role": "...", ... }
    <<<ENCOMM_PROPOSAL_REVIEW_END>>>

The markers are IMPORTED from ``proposal.review_packet`` — prompt and parser
share one definition and cannot drift.  As a fallback, the first balanced
``{...}`` region is accepted (models occasionally wrap the object in prose);
surrounding Markdown is never trusted.

Hard rules (mirroring the Coding Mode verdict parser, D-019 conventions):

* bounded input size BEFORE any parsing;
* ``json.loads`` only — no eval(), no exec(), no YAML;
* unknown extra top-level keys are ignored; unexpected TYPES are rejected;
* ``reviewer_role`` must be one of the three reviewer roles AND must equal
  the expected role for this execution;
* ``iteration_number`` must be a plain JSON integer equal to the expected
  iteration;
* every string and collection is bounded;
* PASS: no critical/high finding AND no unverified claim;
* NEEDS_REVISION: at least one actionable finding or proposed patch;
* BLOCKED: a concrete blocking reason (finding or non-empty summary);
* every finding must carry a canonical category classification;
* every patch needs a target section + rationale AND content in exactly one
  of ``replacement_text`` / ``patch_instructions``.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from .enums import (
    PROPOSAL_REVIEW_VERDICTS,
    ProposalFindingSeverity,
    ProposalReviewVerdict,
    ProposalRole,
)
from .models import ProposalFinding, ProposalPatch, ProposalReviewResult
from .review_packet import (
    PROPOSAL_REVIEW_ENVELOPE_END,
    PROPOSAL_REVIEW_ENVELOPE_START,
)

__all__ = [
    "FINDING_CATEGORIES",
    "MAX_CLAIMS",
    "MAX_FINDINGS",
    "MAX_PATCHES",
    "MAX_RAW_CHARS",
    "MAX_SECTION_CHARS",
    "MAX_STRING_CHARS",
    "MAX_SUMMARY_CHARS",
    "ProposalReviewParseError",
    "REVIEWER_ROLES",
    "parse_proposal_review",
]

#: Delimiter pair (re-exported for callers/tests without touching the packet).
PROPOSAL_REVIEW_ENVELOPE_START = PROPOSAL_REVIEW_ENVELOPE_START
PROPOSAL_REVIEW_ENVELOPE_END = PROPOSAL_REVIEW_ENVELOPE_END

#: The only roles accepted as ``reviewer_role`` in a review payload.  The
#: ORCHESTRATOR is not a reviewer (integration authority, D-054).
REVIEWER_ROLES: frozenset[str] = frozenset(
    {
        ProposalRole.SCIENTIFIC_REVIEWER.value,
        ProposalRole.PROPOSAL_ENGINEER.value,
        ProposalRole.RED_TEAM_REVIEWER.value,
    }
)

#: The canonical finding categories (rule H of the reviewer contract).
FINDING_CATEGORIES: frozenset[str] = frozenset(
    {
        "factual_contradiction",
        "missing_evidence",
        "weak_wording",
        "structural_issue",
        "recommendation",
    }
)

#: Bounds — nothing model-controlled is unbounded on this path.
MAX_RAW_CHARS = 400_000       # the whole model answer we are willing to read
MAX_FINDINGS = 50             # findings per review
MAX_PATCHES = 50              # proposed patches per review
MAX_CLAIMS = 100              # unverified claims per review
MAX_STRING_CHARS = 20_000     # per finding/patch/claim string field
MAX_SECTION_CHARS = 2_000     # per section/target reference
MAX_SUMMARY_CHARS = 20_000    # review summary


class ProposalReviewParseError(ValueError):
    """Raised when a reviewer answer cannot become a strict review result.

    ``reason`` is a short stable machine tag (tests/event logs);
    the message is a bounded human-readable detail.
    """

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"[{reason}] {message}")


# --------------------------------------------------------------------------
# envelope extraction (same shape as the Coding Mode verdict parser)
# --------------------------------------------------------------------------
def _candidate_objects(raw: str) -> Iterable[str]:
    """Yield the marker-delimited payload, or balanced ``{...}`` regions.

    Envelope discipline (fail closed, Session 013): the reviewer prompt
    requires EXACTLY ONE ``START`` marker and EXACTLY ONE ``END`` marker.

    * zero markers on either side → no envelope at all; the balanced-brace
      scan rescues a prose-wrapped model (same convention as Coding Mode);
    * more than one START or more than one END → ``multiple_envelopes``;
    * exactly one START but no END (or the reverse) → unterminated envelope.

    When a well-formed envelope IS present, its payload is the ONLY
    candidate — surrounding content is never additionally scanned.
    """
    starts = raw.count(PROPOSAL_REVIEW_ENVELOPE_START)
    ends = raw.count(PROPOSAL_REVIEW_ENVELOPE_END)
    if starts >= 2 or ends >= 2:
        raise ProposalReviewParseError(
            "multiple_envelopes",
            f"exactly one review envelope is allowed (found {starts} START / "
            f"{ends} END markers); refusing to guess which answer counts.",
        )
    if starts == 1 and ends == 1:
        inner = raw.split(PROPOSAL_REVIEW_ENVELOPE_START, 1)[1].split(
            PROPOSAL_REVIEW_ENVELOPE_END, 1
        )[0]
        inner = inner.strip()
        if inner.startswith("```"):
            inner = inner.strip("`").strip()
            if inner.lower().startswith("json"):
                inner = inner[4:].strip()
        if inner:
            yield inner
        return
    if starts == 1 or ends == 1:
        raise ProposalReviewParseError(
            "unterminated_envelope",
            f"malformed review envelope ({starts} START / {ends} END markers); "
            "exactly one of each is required.",
        )

    depth = 0
    start = -1
    for index, char in enumerate(raw):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                yield raw[start : index + 1]
                start = -1


# --------------------------------------------------------------------------
# bounded-field helpers
# --------------------------------------------------------------------------
def _bounded_str(
    value: Any,
    *,
    limit: int,
    field: str,
    allow_empty: bool = True,
) -> str:
    if not isinstance(value, str):
        raise ProposalReviewParseError(
            "unexpected_type", f"'{field}' must be a string, got {type(value).__name__}"
        )
    if not allow_empty and not value.strip():
        raise ProposalReviewParseError("empty_field", f"'{field}' must not be empty")
    if len(value) > limit:
        raise ProposalReviewParseError(
            "oversized_field",
            f"'{field}' exceeds {limit} characters ({len(value)})",
        )
    return value


def _bounded_ref_list(value: Any, *, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProposalReviewParseError(
            "unexpected_type", f"'{field}' must be a list, got {type(value).__name__}"
        )
    if len(value) > MAX_CLAIMS:
        raise ProposalReviewParseError(
            "oversized_list", f"'{field}' exceeds {MAX_CLAIMS} entries ({len(value)})"
        )
    return [
        _bounded_str(item, limit=MAX_SECTION_CHARS, field=f"{field}[]")
        for item in value
    ]


# --------------------------------------------------------------------------
# findings / patches
# --------------------------------------------------------------------------
def _validate_findings(raw: Any) -> list[ProposalFinding]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ProposalReviewParseError(
            "unexpected_type", f"'findings' must be a list, got {type(raw).__name__}"
        )
    if len(raw) > MAX_FINDINGS:
        raise ProposalReviewParseError(
            "oversized_list",
            f"'findings' exceeds {MAX_FINDINGS} entries ({len(raw)})",
        )
    findings: list[ProposalFinding] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ProposalReviewParseError(
                "unexpected_type",
                f"each finding must be an object, got {type(item).__name__}",
            )
        severity_raw = item.get("severity")
        if not isinstance(severity_raw, str):
            raise ProposalReviewParseError(
                "unexpected_type", "finding 'severity' must be a string"
            )
        severity_value = severity_raw.strip().lower()
        try:
            severity = ProposalFindingSeverity(severity_value)
        except ValueError as exc:
            raise ProposalReviewParseError(
                "invalid_severity",
                "finding severity must be one of critical/high/medium/low, "
                f"got {severity_raw!r}",
            ) from exc
        category = _bounded_str(
            item.get("category"),
            limit=MAX_SECTION_CHARS,
            field="finding.category",
            allow_empty=False,
        ).strip().lower()
        if category not in FINDING_CATEGORIES:
            raise ProposalReviewParseError(
                "invalid_category",
                "finding category must be one of "
                f"{sorted(FINDING_CATEGORIES)}, got {category!r}",
            )
        message = _bounded_str(
            item.get("message"),
            limit=MAX_STRING_CHARS,
            field="finding.message",
            allow_empty=False,
        )
        evidence = _bounded_str(
            item.get("evidence", ""),
            limit=MAX_STRING_CHARS,
            field="finding.evidence",
        )
        section = _bounded_str(
            item.get("section", ""),
            limit=MAX_SECTION_CHARS,
            field="finding.section",
        )
        suggested = _bounded_str(
            item.get("suggested_change", ""),
            limit=MAX_STRING_CHARS,
            field="finding.suggested_change",
        )
        findings.append(
            ProposalFinding(
                severity=severity,
                category=category,
                section=section,
                message=message,
                evidence=evidence,
                source_refs=_bounded_ref_list(item.get("source_refs"), field="finding.source_refs"),
                suggested_change=suggested,
            )
        )
    return findings


def _validate_patches(raw: Any) -> list[ProposalPatch]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ProposalReviewParseError(
            "unexpected_type", f"'proposed_patches' must be a list, got {type(raw).__name__}"
        )
    if len(raw) > MAX_PATCHES:
        raise ProposalReviewParseError(
            "oversized_list",
            f"'proposed_patches' exceeds {MAX_PATCHES} entries ({len(raw)})",
        )
    patches: list[ProposalPatch] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ProposalReviewParseError(
                "unexpected_type",
                f"each patch must be an object, got {type(item).__name__}",
            )
        target = _bounded_str(
            item.get("target_section"),
            limit=MAX_SECTION_CHARS,
            field="patch.target_section",
            allow_empty=False,
        )
        rationale = _bounded_str(
            item.get("rationale"),
            limit=MAX_STRING_CHARS,
            field="patch.rationale",
            allow_empty=False,
        )
        replacement = _bounded_str(
            item.get("replacement_text", ""),
            limit=MAX_STRING_CHARS,
            field="patch.replacement_text",
        )
        instructions = _bounded_str(
            item.get("patch_instructions", ""),
            limit=MAX_STRING_CHARS,
            field="patch.patch_instructions",
        )
        # Exactly one content channel — an ambiguous patch is refused.
        if bool(replacement.strip()) == bool(instructions.strip()):
            raise ProposalReviewParseError(
                "ambiguous_patch",
                "a patch must carry content in exactly one of "
                "'replacement_text' / 'patch_instructions'",
            )
        confidence_raw = item.get("confidence", 0.0)
        if isinstance(confidence_raw, bool) or not isinstance(
            confidence_raw, (int, float)
        ):
            raise ProposalReviewParseError(
                "unexpected_type", "patch 'confidence' must be a JSON number"
            )
        confidence = float(confidence_raw)
        if confidence < 0.0 or confidence > 1.0:
            raise ProposalReviewParseError(
                "invalid_confidence",
                f"patch 'confidence' must be within [0.0, 1.0], got {confidence}",
            )
        patches.append(
            ProposalPatch(
                target_section=target,
                rationale=rationale,
                replacement_text=replacement,
                patch_instructions=instructions,
                source_refs=_bounded_ref_list(item.get("source_refs"), field="patch.source_refs"),
                confidence=confidence,
            )
        )
    return patches


# --------------------------------------------------------------------------
# schema validation (strict)
# --------------------------------------------------------------------------
def _validate(
    raw: Mapping[str, Any], *, expected_role: ProposalRole, expected_iteration: int
) -> ProposalReviewResult:
    verdict_raw = raw.get("verdict")
    if not isinstance(verdict_raw, str):
        raise ProposalReviewParseError(
            "missing_verdict" if verdict_raw is None else "unexpected_type",
            f"'verdict' must be a string, got {type(verdict_raw).__name__}",
        )
    verdict_value = verdict_raw.strip().upper()
    if verdict_value not in PROPOSAL_REVIEW_VERDICTS:
        raise ProposalReviewParseError(
            "invalid_verdict",
            f"'verdict' must be one of PASS/NEEDS_REVISION/BLOCKED, got {verdict_raw!r}",
        )
    verdict = ProposalReviewVerdict(verdict_value)

    role_raw = raw.get("reviewer_role")
    if not isinstance(role_raw, str):
        raise ProposalReviewParseError(
            "missing_reviewer_role" if role_raw is None else "unexpected_type",
            f"'reviewer_role' must be a string, got {type(role_raw).__name__}",
        )
    role_value = role_raw.strip().upper()
    if role_value not in REVIEWER_ROLES:
        raise ProposalReviewParseError(
            "invalid_reviewer_role",
            f"'reviewer_role' must be one of {sorted(REVIEWER_ROLES)}, got {role_raw!r}",
        )
    if role_value != expected_role.value:
        raise ProposalReviewParseError(
            "reviewer_role_mismatch",
            f"expected reviewer_role {expected_role.value}, got {role_value}",
        )

    iteration_raw = raw.get("iteration_number")
    if isinstance(iteration_raw, bool) or not isinstance(iteration_raw, int):
        raise ProposalReviewParseError(
            "missing_iteration" if iteration_raw is None else "unexpected_type",
            "'iteration_number' must be a plain JSON integer, "
            f"got {iteration_raw!r}",
        )
    if iteration_raw != expected_iteration:
        raise ProposalReviewParseError(
            "iteration_mismatch",
            f"expected iteration_number {expected_iteration}, got {iteration_raw}",
        )

    summary = _bounded_str(
        raw.get("summary", ""), limit=MAX_SUMMARY_CHARS, field="summary"
    )
    findings = _validate_findings(raw.get("findings"))
    patches = _validate_patches(raw.get("proposed_patches"))

    claims_raw = raw.get("unverified_claims")
    if claims_raw is None:
        claims: list[str] = []
    else:
        if not isinstance(claims_raw, list):
            raise ProposalReviewParseError(
                "unexpected_type",
                f"'unverified_claims' must be a list, got {type(claims_raw).__name__}",
            )
        if len(claims_raw) > MAX_CLAIMS:
            raise ProposalReviewParseError(
                "oversized_list",
                f"'unverified_claims' exceeds {MAX_CLAIMS} entries ({len(claims_raw)})",
            )
        claims = [
            _bounded_str(c, limit=MAX_STRING_CHARS, field="unverified_claims[]")
            for c in claims_raw
        ]

    result = ProposalReviewResult(
        reviewer_role=expected_role,
        verdict=verdict,
        summary=summary,
        findings=findings,
        proposed_patches=patches,
        unverified_claims=claims,
        iteration_number=iteration_raw,
    )

    # Semantic rules — a technically-valid payload can still be unacceptable.
    if verdict is ProposalReviewVerdict.PASS:
        if result.has_blocking_findings:
            raise ProposalReviewParseError(
                "pass_with_blocking_findings",
                "A PASS verdict cannot carry unresolved critical/high findings.",
            )
        if result.unverified_claims:
            raise ProposalReviewParseError(
                "pass_with_unverified_claims",
                "A PASS verdict cannot carry unverified claims.",
            )
    if verdict is ProposalReviewVerdict.NEEDS_REVISION:
        if not findings and not patches:
            raise ProposalReviewParseError(
                "needs_revision_without_actionables",
                "A NEEDS_REVISION verdict requires at least one actionable "
                "finding or proposed patch.",
            )
    if verdict is ProposalReviewVerdict.BLOCKED:
        has_reason = bool(summary.strip()) or any(
            f.message.strip() for f in findings
        )
        if not has_reason:
            raise ProposalReviewParseError(
                "blocked_without_reason",
                "A BLOCKED verdict must state the concrete blocking reason in "
                "'summary' or in a finding.",
            )
    return result


def parse_proposal_review(
    raw: str,
    *,
    expected_role: ProposalRole,
    expected_iteration: int,
) -> ProposalReviewResult:
    """Parse untrusted reviewer output into a strict ``ProposalReviewResult``.

    Raises :class:`ProposalReviewParseError` on ANY malformed input; it never
    falls back to a lenient interpretation and never fabricates a result.
    """
    if not isinstance(expected_role, ProposalRole):
        expected_role = ProposalRole(str(expected_role))
    if expected_role not in (
        ProposalRole.SCIENTIFIC_REVIEWER,
        ProposalRole.PROPOSAL_ENGINEER,
        ProposalRole.RED_TEAM_REVIEWER,
    ):
        raise ProposalReviewParseError(
            "invalid_expected_role",
            f"{expected_role.value} is not a reviewer role.",
        )
    if not isinstance(expected_iteration, int) or isinstance(
        expected_iteration, bool
    ) or expected_iteration < 1:
        raise ProposalReviewParseError(
            "invalid_expected_iteration",
            "expected_iteration must be a positive integer.",
        )

    if raw is None:
        raise ProposalReviewParseError("empty_output", "The reviewer produced no output.")
    if not isinstance(raw, str):
        raise ProposalReviewParseError(
            "unexpected_type", f"reviewer output must be text, got {type(raw).__name__}"
        )
    if len(raw) > MAX_RAW_CHARS:
        raise ProposalReviewParseError(
            "oversized_payload",
            f"reviewer output exceeds {MAX_RAW_CHARS} characters ({len(raw)}); "
            "refusing to parse.",
        )

    last_error: ProposalReviewParseError | None = None
    for candidate in _candidate_objects(raw):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = ProposalReviewParseError(
                "malformed_json", f"unparseable JSON object: {exc.msg}"
            )
            continue
        if not isinstance(parsed, Mapping):
            raise ProposalReviewParseError(
                "unexpected_type",
                f"the JSON payload must be an object, got {type(parsed).__name__}",
            )
        try:
            return _validate(
                parsed,
                expected_role=expected_role,
                expected_iteration=expected_iteration,
            )
        except ProposalReviewParseError as exc:
            last_error = exc
            # A dict that failed schema validation might be prose chatter;
            # the next candidate (if any) gets a chance, otherwise fail closed.

    if last_error is not None:
        raise last_error
    raise ProposalReviewParseError(
        "no_json_object",
        "No valid JSON object was found in the reviewer output.",
    )
