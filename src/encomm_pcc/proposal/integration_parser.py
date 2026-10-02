"""Fail-closed parser for ORCHESTRATOR integration output — PURE proposal.

The ORCHESTRATOR's integration answer is **untrusted model output**.  This
module is the only place that turns that text into a
:class:`~encomm_pcc.proposal.ProposalIntegrationResult`, and it fails
closed: anything malformed raises
:class:`ProposalIntegrationParseError` — a malformed answer can NEVER
become a master-proposal replacement and is never coerced into a result.

Envelope.  The integration prompt requires exactly one delimited JSON
object::

    <<<ENCOMM_PROPOSAL_INTEGRATION_START>>>
    { "role": "ORCHESTRATOR", ... }
    <<<ENCOMM_PROPOSAL_INTEGRATION_END>>>

The markers are IMPORTED from ``proposal.integration_packet`` — prompt and
parser share one definition and cannot drift.  Like the review parser
(strict-mandatory envelope since Session 013A, deliberately diverging from
the core parsers' envelope+fallback convention) there is NO balanced-brace
rescue and NO bare-JSON fallback: a syntactically perfect integration
object without the envelope is rejected as ``missing_envelope``.  Prose
AROUND a correct envelope is tolerated; prose without one never parses.

Hard rules:

* bounded input size BEFORE any parsing;
* ``json.loads`` only — no eval(), no exec(), no YAML;
* unknown extra top-level keys are ignored; unexpected TYPES are rejected;
* ``role`` must be exactly ORCHESTRATOR (the sole integration authority);
* ``iteration_number`` must be a plain JSON integer equal to the expected
  iteration;
* ``input_proposal_hash`` must equal the expected exact-byte hash
  (case-insensitively — the canonical digest is lowercase, but refusing to
  reject on upper-case caller echo is not a relaxation of content);
* ``revised_proposal`` must be a non-empty bounded string;
* ``applied_items`` / ``rejected_items`` / ``unresolved_items`` must be
  lists of objects with exactly the keys ``item_id``/``action``/``reason``
  and a whitelisted action matching its array;
* PASS-style semantic rule: an integration that claims NO changed content
  while the parser-level contract requires disposition of actionable brief
  items is NOT the parser's business — the runtime validates the
  change/no-change outcome against the brief; the parser pins the
  STRUCTURE only.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from .integration_models import (
    INTEGRATION_ITEM_ACTIONS,
    ProposalIntegrationItem,
    ProposalIntegrationResult,
)
from .integration_packet import (
    PROPOSAL_INTEGRATION_ENVELOPE_END,
    PROPOSAL_INTEGRATION_ENVELOPE_START,
)

__all__ = [
    "INTEGRATION_ITEM_ACTIONS",
    "MAX_INTEGRATION_RAW_CHARS",
    "MAX_INTEGRATION_REVISED_CHARS",
    "MAX_INTEGRATION_SUMMARY_CHARS",
    "ProposalIntegrationParseError",
    "parse_proposal_integration",
]

#: Re-exported delimiter pair for callers/tests without touching the packet.
PROPOSAL_INTEGRATION_ENVELOPE_START = PROPOSAL_INTEGRATION_ENVELOPE_START
PROPOSAL_INTEGRATION_ENVELOPE_END = PROPOSAL_INTEGRATION_ENVELOPE_END

#: The only role an integration payload may claim.
ORCHESTRATOR_ROLE = "ORCHESTRATOR"

#: Bounds — nothing model-controlled is unbounded on this path.
MAX_INTEGRATION_RAW_CHARS = 2_000_000
#: The revised proposal must fit the same 20 MB fingerprint cap with room
#: for the rest of the JSON envelope; anything larger is refused.
MAX_INTEGRATION_REVISED_CHARS = 1_000_000
MAX_INTEGRATION_SUMMARY_CHARS = 20_000
MAX_INTEGRATION_ITEMS = 200          # per disposition array
MAX_INTEGRATION_ITEM_TEXT = 20_000   # per item_id / reason string

#: Exact key set of one integration item (strict contract).
_ITEM_KEYS = frozenset({"item_id", "action", "reason"})


class ProposalIntegrationParseError(ValueError):
    """Raised when an integration answer cannot become a strict result.

    ``reason`` is a short stable machine tag (tests/event logs); the
    message is a bounded human-readable detail.
    """

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"[{reason}] {message}")


# --------------------------------------------------------------------------
# envelope extraction (STRICT — the envelope is mandatory)
# --------------------------------------------------------------------------
def _candidate_objects(raw: str) -> Iterable[str]:
    """Yield the payload of the ONE mandatory integration envelope.

    Envelope discipline mirrors ``proposal.review_parser`` (fail closed):
    zero markers → ``missing_envelope`` (even around a syntactically perfect
    bare JSON object — NO balanced-brace rescue, NO bare-JSON fallback);
    more than one START or END → ``multiple_envelopes``; exactly one of the
    two → ``unterminated_envelope``; exactly one of each → the payload
    between the markers is the ONLY candidate.
    """
    starts = raw.count(PROPOSAL_INTEGRATION_ENVELOPE_START)
    ends = raw.count(PROPOSAL_INTEGRATION_ENVELOPE_END)
    if starts >= 2 or ends >= 2:
        raise ProposalIntegrationParseError(
            "multiple_envelopes",
            f"exactly one integration envelope is allowed (found {starts} "
            f"START / {ends} END markers); refusing to guess which answer "
            "counts.",
        )
    if starts == 0 and ends == 0:
        raise ProposalIntegrationParseError(
            "missing_envelope",
            "no integration envelope found: exactly one "
            f"'{PROPOSAL_INTEGRATION_ENVELOPE_START}' ... "
            f"'{PROPOSAL_INTEGRATION_ENVELOPE_END}' pair is required; bare "
            "JSON or prose is never accepted.",
        )
    if starts != 1 or ends != 1:
        raise ProposalIntegrationParseError(
            "unterminated_envelope",
            f"malformed integration envelope ({starts} START / {ends} END "
            "markers); exactly one of each is required.",
        )
    inner = raw.split(PROPOSAL_INTEGRATION_ENVELOPE_START, 1)[1].split(
        PROPOSAL_INTEGRATION_ENVELOPE_END, 1
    )[0]
    inner = inner.strip()
    if inner.startswith("```"):
        inner = inner.strip("`").strip()
        if inner.lower().startswith("json"):
            inner = inner[4:].strip()
    if inner:
        yield inner


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
        raise ProposalIntegrationParseError(
            "unexpected_type",
            f"'{field}' must be a string, got {type(value).__name__}",
        )
    if not allow_empty and not value.strip():
        raise ProposalIntegrationParseError(
            "empty_field", f"'{field}' must not be empty"
        )
    if len(value) > limit:
        raise ProposalIntegrationParseError(
            "oversized_field",
            f"'{field}' exceeds {limit} characters ({len(value)})",
        )
    return value


def _validate_items(raw: Any, *, field: str, action: str) -> list[ProposalIntegrationItem]:
    """Validate one disposition array against the STRICT item contract.

    Each entry must be a JSON object carrying EXACTLY the keys
    ``item_id``/``action``/``reason`` (unknown keys rejected — the
    integration item contract is strict because these records are the
    durable traceability evidence), a non-empty ``item_id``, a ``reason``
    string, and an ``action`` equal to the array it appears in.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ProposalIntegrationParseError(
            "unexpected_type",
            f"'{field}' must be a list, got {type(raw).__name__}",
        )
    if len(raw) > MAX_INTEGRATION_ITEMS:
        raise ProposalIntegrationParseError(
            "oversized_list",
            f"'{field}' exceeds {MAX_INTEGRATION_ITEMS} entries ({len(raw)})",
        )
    items: list[ProposalIntegrationItem] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ProposalIntegrationParseError(
                "unexpected_type",
                f"each {field} entry must be an object, got "
                f"{type(item).__name__}",
            )
        keys = frozenset(item.keys())
        if keys != _ITEM_KEYS:
            raise ProposalIntegrationParseError(
                "malformed_item",
                f"{field}[{index}] must carry exactly the keys "
                f"item_id/action/reason, got {sorted(keys)}",
            )
        item_id = _bounded_str(
            item.get("item_id"),
            limit=MAX_INTEGRATION_ITEM_TEXT,
            field=f"{field}[{index}].item_id",
            allow_empty=False,
        )
        item_action_raw = _bounded_str(
            item.get("action"),
            limit=32,
            field=f"{field}[{index}].action",
            allow_empty=False,
        )
        item_action = item_action_raw.strip().lower()
        if item_action not in INTEGRATION_ITEM_ACTIONS:
            raise ProposalIntegrationParseError(
                "invalid_item_action",
                f"{field}[{index}].action must be one of "
                f"{sorted(INTEGRATION_ITEM_ACTIONS)}, got {item_action_raw!r}",
            )
        if item_action != action:
            raise ProposalIntegrationParseError(
                "item_action_mismatch",
                f"{field}[{index}].action is {item_action!r}, but the array "
                f"is '{action}'.",
            )
        reason = _bounded_str(
            item.get("reason"),
            limit=MAX_INTEGRATION_ITEM_TEXT,
            field=f"{field}[{index}].reason",
        )
        items.append(
            ProposalIntegrationItem(
                item_id=item_id, action=item_action, reason=reason
            )
        )
    return items


# --------------------------------------------------------------------------
# schema validation (strict)
# --------------------------------------------------------------------------
def _validate(
    raw: Mapping[str, Any],
    *,
    expected_iteration: int,
    expected_input_hash: str,
) -> ProposalIntegrationResult:
    role_raw = raw.get("role")
    if not isinstance(role_raw, str):
        raise ProposalIntegrationParseError(
            "missing_role" if role_raw is None else "unexpected_type",
            f"'role' must be a string, got {type(role_raw).__name__}",
        )
    if role_raw.strip().upper() != ORCHESTRATOR_ROLE:
        raise ProposalIntegrationParseError(
            "wrong_role",
            f"'role' must be exactly {ORCHESTRATOR_ROLE}, got {role_raw!r}",
        )

    iteration_raw = raw.get("iteration_number")
    if isinstance(iteration_raw, bool) or not isinstance(iteration_raw, int):
        raise ProposalIntegrationParseError(
            "missing_iteration" if iteration_raw is None else "unexpected_type",
            "'iteration_number' must be a plain JSON integer, "
            f"got {iteration_raw!r}",
        )
    if iteration_raw != expected_iteration:
        raise ProposalIntegrationParseError(
            "iteration_mismatch",
            f"expected iteration_number {expected_iteration}, got {iteration_raw}",
        )

    hash_raw = raw.get("input_proposal_hash")
    if not isinstance(hash_raw, str):
        raise ProposalIntegrationParseError(
            "missing_input_hash" if hash_raw is None else "unexpected_type",
            f"'input_proposal_hash' must be a string, got "
            f"{type(hash_raw).__name__}",
        )
    if hash_raw.strip().lower() != str(expected_input_hash).strip().lower():
        raise ProposalIntegrationParseError(
            "input_hash_mismatch",
            "expected input_proposal_hash "
            f"{str(expected_input_hash)!r}, got {hash_raw!r}",
        )

    revised = _bounded_str(
        raw.get("revised_proposal"),
        limit=MAX_INTEGRATION_REVISED_CHARS,
        field="revised_proposal",
        allow_empty=False,
    )

    summary = _bounded_str(
        raw.get("summary", ""),
        limit=MAX_INTEGRATION_SUMMARY_CHARS,
        field="summary",
    )
    applied = _validate_items(raw.get("applied_items"), field="applied_items", action="applied")
    rejected = _validate_items(raw.get("rejected_items"), field="rejected_items", action="rejected")
    unresolved = _validate_items(
        raw.get("unresolved_items"), field="unresolved_items", action="unresolved"
    )

    return ProposalIntegrationResult(
        iteration_number=iteration_raw,
        input_proposal_hash=hash_raw.strip(),
        revised_proposal=revised,
        summary=summary,
        applied_items=applied,
        rejected_items=rejected,
        unresolved_items=unresolved,
    )


def parse_proposal_integration(
    raw: str,
    *,
    expected_iteration: int,
    expected_input_hash: str,
) -> ProposalIntegrationResult:
    """Parse untrusted ORCHESTRATOR output into a strict result — fail closed.

    Raises :class:`ProposalIntegrationParseError` on ANY malformed input;
    it never falls back to a lenient interpretation and never fabricates a
    result.  The parsed result is checked against the expected iteration
    number AND the expected exact-byte input proposal hash (fail closed
    BEFORE any caller trusts the answer).
    """
    if not isinstance(expected_iteration, int) or isinstance(
        expected_iteration, bool
    ) or expected_iteration < 1:
        raise ProposalIntegrationParseError(
            "invalid_expected_iteration",
            "expected_iteration must be a positive integer.",
        )
    if not str(expected_input_hash or "").strip():
        raise ProposalIntegrationParseError(
            "invalid_expected_hash",
            "expected_input_hash must be a non-empty string.",
        )

    if raw is None:
        raise ProposalIntegrationParseError(
            "empty_output", "The ORCHESTRATOR produced no output."
        )
    if not isinstance(raw, str):
        raise ProposalIntegrationParseError(
            "unexpected_type",
            f"integration output must be text, got {type(raw).__name__}",
        )
    if len(raw) > MAX_INTEGRATION_RAW_CHARS:
        raise ProposalIntegrationParseError(
            "oversized_payload",
            f"integration output exceeds {MAX_INTEGRATION_RAW_CHARS} "
            f"characters ({len(raw)}); refusing to parse.",
        )

    # At most ONE candidate exists: the payload of the one mandatory
    # envelope.  Envelope errors raise directly from _candidate_objects.
    for candidate in _candidate_objects(raw):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ProposalIntegrationParseError(
                "malformed_json", f"unparseable JSON object: {exc.msg}"
            ) from exc
        if not isinstance(parsed, Mapping):
            raise ProposalIntegrationParseError(
                "unexpected_type",
                f"the JSON payload must be an object, got "
                f"{type(parsed).__name__}",
            )
        return _validate(
            parsed,
            expected_iteration=expected_iteration,
            expected_input_hash=expected_input_hash,
        )
    # The envelope was present but carried no JSON payload at all.
    raise ProposalIntegrationParseError(
        "no_json_object",
        "the integration envelope contained no JSON object.",
    )
