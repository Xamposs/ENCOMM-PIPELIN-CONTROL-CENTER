"""The 14 REAL hard-gate validators — PURE proposal domain (Session 016).

Every validator is a DETERMINISTIC pure function of :class:`HardGateContext`
to :class:`HardGateEvaluation`.  No model call, no network, no provider code,
no semantic/fuzzy matching.  Each gate validates the OPERATOR-SUPPLIED
evidence plus the exact current proposal text — it validates the EVIDENCE
LEDGER, never external truth.

Failure semantics (fail closed):

* evidence section absent (``None``)                     → FAIL/EVIDENCE_MISSING
* evidence section present but malformed                 → FAIL/EVIDENCE_INVALID
* evidence present + valid, but the gate does not hold   → FAIL/PROPOSAL_ISSUE
* the one exception: PAGE_LIMIT with an estimate
  measurement method                                     → WARN (blocks
  COMPLETE; the report's INCOMPLETE disposition keeps the machine at
  ``HARD_GATE_VALIDATION``)

JSON structural parsing: values arrive from a parsed JSON document, so
array/object shape checks guard every access (never guess malformed data).
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from .hard_gates import (
    HardGateContext,
    HardGateEvaluation,
    HardGateFailureClass,
)
from .enums import ProposalHardGateStatus

__all__ = ["GATE_VALIDATORS"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _na(
    gate_id: str,
) -> HardGateEvaluation:
    """The NOT_APPLICABLE placeholder a validator never returns itself.

    Kept as documentation only; explicit N/A handling lives in
    ``hard_gates.evaluate_hard_gates`` (a gate never self-declares N/A).
    """
    raise NotImplementedError  # pragma: no cover


def _fail(
    gate_id: str,
    failure_class: HardGateFailureClass,
    message: str,
    evidence: str = "",
) -> HardGateEvaluation:
    return HardGateEvaluation(
        gate_id=gate_id,
        status=ProposalHardGateStatus.FAIL,
        message=message,
        evidence=evidence,
        failure_class=failure_class,
    )


def _pass(
    gate_id: str,
    message: str,
    evidence: str = "",
) -> HardGateEvaluation:
    return HardGateEvaluation(
        gate_id=gate_id,
        status=ProposalHardGateStatus.PASS,
        message=message,
        evidence=evidence,
        failure_class=HardGateFailureClass.NONE,
    )


def _require_object(
    gate_id: str,
    section: dict[str, Any] | None,
    section_name: str,
) -> tuple[dict[str, Any] | None, HardGateEvaluation | None]:
    """Absent → EVIDENCE_MISSING; non-dict cannot occur (loader enforces)."""
    if section is None:
        return None, _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_MISSING,
            f"evidence section '{section_name}' is absent; missing evidence "
            "never passes.",
        )
    return section, None


def _string_list(value: Any) -> list[str] | None:
    """Accept a JSON array of strings; ``None`` for anything else."""
    if not isinstance(value, list):
        return None
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        items.append(item)
    return items


def _normalize_heading(text: str) -> str:
    """Only whitespace + case normalisation — NEVER semantic matching."""
    return re.sub(r"\s+", " ", text.strip()).casefold()


def _as_decimal(value: Any) -> Decimal | None:
    """Deterministic numeric coercion for money/person-months.

    Accepts JSON numbers (int/float, NOT bool) and numeric strings; returns
    ``None`` for anything non-numeric.  Decimal arithmetic keeps budget/PM
    comparisons exact.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # Route through the repr string so the decimal value is the one the
        # operator wrote in JSON, not the binary-float artefact.
        try:
            return Decimal(repr(value))
        except InvalidOperation:  # pragma: no cover - repr(float) is valid
            return None
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except InvalidOperation:
            return None
    return None


def _iter_string_items(
    gate_id: str,
    value: Any,
    label: str,
) -> tuple[list[str] | None, HardGateEvaluation | None]:
    items = _string_list(value)
    if items is None:
        return None, _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            f"{label} must be an array of strings.",
        )
    return items, None


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


# ---------------------------------------------------------------------------
# 1. MANDATORY_SECTIONS
# ---------------------------------------------------------------------------
def _validate_mandatory_sections(ctx: HardGateContext) -> HardGateEvaluation:
    gate_id = "MANDATORY_SECTIONS"
    section, err = _require_object(
        gate_id, ctx.evidence.mandatory_sections, "mandatory_sections"
    )
    if err is not None:
        return err
    assert section is not None  # engine invariant: err is None ⇒ section set
    required, err = _iter_string_items(
        gate_id, section.get("required_headings"),
        "mandatory_sections.required_headings",
    )
    if err is not None:
        return err
    if not required:
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            "mandatory_sections.required_headings is empty; an empty "
            "requirement cannot attest compliance.",
        )
    # Parse the ATX headings out of the exact current proposal text.
    headings = set()
    for line in ctx.master_proposal_text.splitlines():
        match = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if match:
            headings.add(_normalize_heading(match.group(2)))
    missing = [
        heading
        for heading in required
        if _normalize_heading(heading) not in headings
    ]
    if missing:
        return _fail(
            gate_id,
            HardGateFailureClass.PROPOSAL_ISSUE,
            f"missing required section heading(s): {missing!r}",
            evidence="mandatory_sections.required_headings",
        )
    return _pass(
        gate_id,
        f"all {len(required)} required heading(s) present.",
        evidence="mandatory_sections.required_headings",
    )


# ---------------------------------------------------------------------------
# 2. CHALLENGE_MAPPING
# ---------------------------------------------------------------------------
def _validate_challenge_mapping(ctx: HardGateContext) -> HardGateEvaluation:
    gate_id = "CHALLENGE_MAPPING"
    section, err = _require_object(
        gate_id, ctx.evidence.challenge_mapping, "challenge_mapping"
    )
    if err is not None:
        return err
    assert section is not None  # engine invariant: err is None ⇒ section set
    required, err = _iter_string_items(
        gate_id, section.get("required_markers"),
        "challenge_mapping.required_markers",
    )
    if err is not None:
        return err
    if not required:
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            "challenge_mapping.required_markers is empty; an empty marker "
            "set cannot attest compliance.",
        )
    mappings_raw = section.get("mappings")
    if not isinstance(mappings_raw, list):
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            "challenge_mapping.mappings must be an array of mapping records.",
        )
    mapped: set[str] = set()
    for index, record in enumerate(mappings_raw):
        if not isinstance(record, dict):
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"challenge_mapping.mappings[{index}] must be an object.",
            )
        marker = record.get("marker")
        if not isinstance(marker, str) or not marker.strip():
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"challenge_mapping.mappings[{index}].marker must be a "
                "non-empty string.",
            )
        # Each mapping must name a proposal section AND carry explicit
        # evidence text or a reference to external evidence.
        has_section = _nonempty_str(record.get("proposal_section"))
        has_evidence = _nonempty_str(record.get("evidence_text")) or _nonempty_str(
            record.get("evidence_reference")
        )
        if not has_section or not has_evidence:
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"mapping for marker {marker!r} requires a non-empty "
                "'proposal_section' and 'evidence_text' or "
                "'evidence_reference'.",
            )
        mapped.add(marker.strip())
    missing = [marker for marker in required if marker.strip() not in mapped]
    if missing:
        return _fail(
            gate_id,
            HardGateFailureClass.PROPOSAL_ISSUE,
            f"required challenge marker(s) not explicitly mapped: {missing!r}",
            evidence="challenge_mapping.mappings",
        )
    return _pass(
        gate_id,
        f"all {len(required)} required marker(s) explicitly mapped.",
        evidence="challenge_mapping.mappings",
    )


# ---------------------------------------------------------------------------
# shared: 02_EVIDENCE JSON documents (fail closed)
# ---------------------------------------------------------------------------
def _load_workspace_json(
    ctx: HardGateContext,
    relpath: str,
) -> tuple[Any | None, str | None]:
    """Read a workspace JSON document; ``None`` with a reason when broken."""
    path = ctx.workspace / relpath
    if not path.is_file():
        return None, f"{relpath} is missing."
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, f"{relpath} cannot be read ({exc})."
    if not raw.strip():
        return None, f"{relpath} is empty (zero-byte seed)."
    try:
        return json.loads(raw.decode("utf-8")), None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"{relpath} is not valid JSON ({exc})."


# ---------------------------------------------------------------------------
# 3. SOURCE_OF_TRUTH_INTEGRITY
# ---------------------------------------------------------------------------
def _validate_source_of_truth(ctx: HardGateContext) -> HardGateEvaluation:
    gate_id = "SOURCE_OF_TRUTH_INTEGRITY"
    registry, registry_err = _load_workspace_json(ctx, "02_EVIDENCE/SOURCE_REGISTRY.json")
    if registry_err is not None:
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_MISSING
            if "is missing" in registry_err or "empty" in registry_err
            else HardGateFailureClass.EVIDENCE_INVALID,
            f"SOURCE_REGISTRY.json unusable: {registry_err}",
        )
    ledger, ledger_err = _load_workspace_json(ctx, "02_EVIDENCE/CLAIM_LEDGER.json")
    if ledger_err is not None:
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_MISSING
            if "is missing" in ledger_err or "empty" in ledger_err
            else HardGateFailureClass.EVIDENCE_INVALID,
            f"CLAIM_LEDGER.json unusable: {ledger_err}",
        )
    if not isinstance(registry, dict) or not isinstance(ledger, dict):
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            "SOURCE_REGISTRY.json and CLAIM_LEDGER.json must be JSON objects.",
        )
    sources = registry.get("sources")
    claims = ledger.get("claims")
    if not isinstance(sources, list) or not isinstance(claims, list):
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            "expected 'sources' and 'claims' arrays (minimal supported "
            "schema: sources[{id,...}], claims[{id,source_ref,...}]).",
        )
    source_ids: set[str] = set()
    for index, source in enumerate(sources):
        if not isinstance(source, dict) or not _nonempty_str(source.get("id")):
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"sources[{index}] must be an object with a non-empty 'id'.",
            )
        sid = source["id"].strip()
        if sid in source_ids:
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"duplicate source id {sid!r}.",
            )
        source_ids.add(sid)
    claim_ids: set[str] = set()
    for index, claim in enumerate(claims):
        cid = (
            claim.get("claim_id", claim.get("id"))
            if isinstance(claim, dict)
            else None
        )
        if not isinstance(cid, str) or not cid.strip():
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"claims[{index}] must be an object with a non-empty "
                "'claim_id'.",
            )
        cid = cid.strip()
        if cid in claim_ids:
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"duplicate claim id {cid!r}.",
            )
        claim_ids.add(cid)
        source_ref = claim.get("source_ref")
        if not isinstance(source_ref, str) or not source_ref.strip():
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"claim {cid!r} requires a non-empty 'source_ref'.",
            )
        if source_ref.strip() not in source_ids:
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_MISSING,
                f"claim {cid!r} references unregistered source "
                f"{source_ref!r}.",
            )
        if claim.get("conflicting") is True:
            return _fail(
                gate_id,
                HardGateFailureClass.PROPOSAL_ISSUE,
                f"claim {cid!r} is marked conflicting=true; unresolved "
                "source-integrity conflicts block completion.",
            )
    return _pass(
        gate_id,
        f"{len(source_ids)} source(s) registered, {len(claim_ids)} claim(s) "
        "resolve; no conflicts.",
        evidence="02_EVIDENCE/SOURCE_REGISTRY.json, 02_EVIDENCE/CLAIM_LEDGER.json",
    )


# ---------------------------------------------------------------------------
# 4. UNVERIFIED_CLAIMS (control file + same-hash review bundle)
# ---------------------------------------------------------------------------
def _validate_unverified_claims(ctx: HardGateContext) -> HardGateEvaluation:
    gate_id = "UNVERIFIED_CLAIMS"
    control, control_err = _load_workspace_json(ctx, "05_CONTROL/UNVERIFIED_CLAIMS.json")
    if control_err is not None:
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_MISSING
            if "is missing" in control_err or "empty" in control_err
            else HardGateFailureClass.EVIDENCE_INVALID,
            f"UNVERIFIED_CLAIMS.json unusable: {control_err}",
        )
    if not isinstance(control, dict):
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            "UNVERIFIED_CLAIMS.json must be a JSON object.",
        )
    unresolved, err = _iter_string_items(
        gate_id, control.get("unresolved"),
        "UNVERIFIED_CLAIMS.unresolved",
    )
    if err is not None:
        return err
    if unresolved:
        return _fail(
            gate_id,
            HardGateFailureClass.PROPOSAL_ISSUE,
            f"{len(unresolved)} unverified claim(s) remain in the control "
            f"file: {unresolved!r}",
            evidence="05_CONTROL/UNVERIFIED_CLAIMS.json",
        )
    # Same-hash review bundle must also be claim-free.
    latest, bundle = _latest_bundle_for_hash(ctx, gate_id)
    if bundle is None:
        assert latest is not None  # engine invariant: bundle None ⇒ latest holds the failure
        return latest
    claims = bundle.get("unverified_claims")
    if not isinstance(claims, list):
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            "latest review_bundle.json carries a malformed "
            "'unverified_claims' field.",
        )
    claims = [c for c in claims if isinstance(c, str) and c.strip()]
    if claims:
        return _fail(
            gate_id,
            HardGateFailureClass.PROPOSAL_ISSUE,
            f"the latest same-hash review records {len(claims)} unverified "
            f"claim(s): {claims!r}",
            evidence="04_REVIEWS review_bundle.json",
        )
    return _pass(
        gate_id,
        "no unresolved unverified claims (control file + same-hash review).",
        evidence="05_CONTROL/UNVERIFIED_CLAIMS.json, 04_REVIEWS review_bundle.json",
    )


def _latest_bundle_for_hash(
    ctx: HardGateContext,
    gate_id: str,
) -> tuple[HardGateEvaluation, None] | tuple[None, dict[str, Any]]:
    """Load the LATEST review bundle and verify it matches THIS hash.

    Returns ``(None, bundle)`` on success and ``(failure, None)`` when the
    bundle evidence fails closed.
    """
    reviews_dir = ctx.workspace / "04_REVIEWS"
    iterations: list[int] = []
    if reviews_dir.is_dir():
        for entry in reviews_dir.iterdir():
            name = entry.name
            if entry.is_dir() and name.startswith("iteration_"):
                suffix = name[len("iteration_"):]
                if suffix.isdigit():
                    iterations.append(int(suffix))
    if not iterations:
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_MISSING,
            "no review iteration exists under 04_REVIEWS/.",
        ), None
    latest = max(iterations)
    bundle_path = reviews_dir / f"iteration_{latest:03d}" / "review_bundle.json"
    data, err = _load_workspace_json(ctx, str(bundle_path.relative_to(ctx.workspace)))
    if err is not None:
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            f"latest review bundle unusable: {err}",
        ), None
    if not isinstance(data, dict):
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            "latest review_bundle.json is not a JSON object.",
        ), None
    bundle_hash = str(data.get("proposal_hash") or "").strip().lower()
    if not bundle_hash:
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            "latest review_bundle.json carries no proposal_hash.",
        ), None
    if bundle_hash != ctx.evidence.proposal_hash.strip().lower():
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            "latest review bundle belongs to another proposal revision "
            "(stale evidence); the evidence contract must match the "
            "reviewed revision.",
        ), None
    return None, data


# ---------------------------------------------------------------------------
# 5. CITATION_VERIFICATION
# ---------------------------------------------------------------------------
def _validate_citations(ctx: HardGateContext) -> HardGateEvaluation:
    gate_id = "CITATION_VERIFICATION"
    ledger, ledger_err = _load_workspace_json(ctx, "02_EVIDENCE/CLAIM_LEDGER.json")
    if ledger_err is not None:
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_MISSING
            if "is missing" in ledger_err or "empty" in ledger_err
            else HardGateFailureClass.EVIDENCE_INVALID,
            f"CLAIM_LEDGER.json unusable: {ledger_err}",
        )
    if not isinstance(ledger, dict):
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            "CLAIM_LEDGER.json must be a JSON object.",
        )
    claims = ledger.get("claims")
    if not isinstance(claims, list):
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            "CLAIM_LEDGER.claims must be an array (minimal schema: "
            "claims[{claim_id, requires_citation, citation_verified, "
            "source_refs}]).",
        )
    checked = 0
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"claims[{index}] must be an object.",
            )
        claim_id = claim.get("claim_id")
        if not _nonempty_str(claim_id):
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"claims[{index}].claim_id must be a non-empty string.",
            )
        if claim.get("requires_citation") is not True:
            continue
        checked += 1
        if claim.get("citation_verified") is not True:
            return _fail(
                gate_id,
                HardGateFailureClass.PROPOSAL_ISSUE,
                f"claim {claim_id!r} requires a citation but is not marked "
                "citation_verified=true.",
                evidence="02_EVIDENCE/CLAIM_LEDGER.json",
            )
        refs = claim.get("source_refs")
        if not isinstance(refs, list) or not any(
            isinstance(r, str) and r.strip() for r in refs
        ):
            return _fail(
                gate_id,
                HardGateFailureClass.PROPOSAL_ISSUE,
                f"claim {claim_id!r} requires a citation but carries no "
                "non-empty source_ref.",
                evidence="02_EVIDENCE/CLAIM_LEDGER.json",
            )
    return _pass(
        gate_id,
        f"{checked} citation-requiring claim(s) verified with at least one "
        "source_ref.",
        evidence="02_EVIDENCE/CLAIM_LEDGER.json",
    )


# ---------------------------------------------------------------------------
# 6. TERMINOLOGY_CONSISTENCY
# ---------------------------------------------------------------------------
def _validate_terminology(ctx: HardGateContext) -> HardGateEvaluation:
    gate_id = "TERMINOLOGY_CONSISTENCY"
    section, err = _require_object(gate_id, ctx.evidence.terminology, "terminology")
    if err is not None:
        return err
    assert section is not None  # engine invariant: err is None ⇒ section set
    rules = section.get("rules")
    if not isinstance(rules, list) or not rules:
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            "terminology.rules must be a non-empty array of "
            "{canonical, forbidden_aliases} rules.",
        )
    violations: list[str] = []
    text = ctx.master_proposal_text
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"terminology.rules[{index}] must be an object.",
            )
        canonical = rule.get("canonical")
        if not _nonempty_str(canonical):
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"terminology.rules[{index}].canonical must be a non-empty "
                "string.",
            )
        aliases, alias_err = _iter_string_items(
            gate_id, rule.get("forbidden_aliases"),
            f"terminology.rules[{index}].forbidden_aliases",
        )
        if alias_err is not None:
            return alias_err
        for alias in aliases:
            if not alias.strip():
                continue
            # Deterministic literal substring search — no semantic synonym
            # detection, no fuzzy matching, case-sensitive by contract.
            if alias in text:
                violations.append(f"{alias!r} (canonical {canonical!r})")
    if violations:
        return _fail(
            gate_id,
            HardGateFailureClass.PROPOSAL_ISSUE,
            f"forbidden terminology alias(es) occur in the proposal: "
            f"{violations!r}",
            evidence="terminology.rules",
        )
    return _pass(
        gate_id,
        f"no forbidden alias from {len(rules)} terminology rule(s) occurs.",
        evidence="terminology.rules",
    )


# ---------------------------------------------------------------------------
# 7–10. WORK-PLAN gates
# ---------------------------------------------------------------------------
def _require_workplan(
    gate_id: str,
    ctx: HardGateContext,
) -> tuple[list[dict[str, Any]] | None, HardGateEvaluation | None]:
    section, err = _require_object(gate_id, ctx.evidence.workplan, "workplan")
    if err is not None:
        return None, err
    assert section is not None  # engine invariant: err is None ⇒ section set
    work_packages = section.get("work_packages")
    if not isinstance(work_packages, list) or not work_packages:
        return None, _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            "workplan.work_packages must be a non-empty array of WP records.",
        )
    for index, wp in enumerate(work_packages):
        if not isinstance(wp, dict):
            return None, _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"workplan.work_packages[{index}] must be an object.",
            )
    return work_packages, None


def _wp_id(
    wp: dict[str, Any],
    index: int,
    gate_id: str,
) -> tuple[str | None, HardGateEvaluation | None]:
    wp_id = wp.get("id")
    if not isinstance(wp_id, str) or not wp_id.strip():
        return None, _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            f"work_packages[{index}].id must be a non-empty string.",
        )
    return wp_id.strip(), None


def _validate_wp_tasks(ctx: HardGateContext) -> HardGateEvaluation:
    gate_id = "WP_TASK_CONSISTENCY"
    work_packages, err = _require_workplan(gate_id, ctx)
    if err is not None:
        return err
    wp_ids: set[str] = set()
    task_owners: dict[str, str] = {}
    for index, wp in enumerate(work_packages):
        wp_id, wp_err = _wp_id(wp, index, gate_id)
        if wp_err is not None:
            return wp_err
        assert wp_id is not None  # engine invariant: err is None => id set
        if wp_id in wp_ids:
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"duplicate WP id {wp_id!r}.",
            )
        wp_ids.add(wp_id)
        tasks = wp.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"WP {wp_id!r} must carry a non-empty 'tasks' array (per "
                "the evidence schema, each task owner/reference field is "
                "required).",
            )
        for t_index, task in enumerate(tasks):
            if not isinstance(task, dict):
                return _fail(
                    gate_id,
                    HardGateFailureClass.EVIDENCE_INVALID,
                    f"WP {wp_id!r} tasks[{t_index}] must be an object.",
                )
            task_id = task.get("id")
            if not _nonempty_str(task_id):
                return _fail(
                    gate_id,
                    HardGateFailureClass.EVIDENCE_INVALID,
                    f"WP {wp_id!r} tasks[{t_index}].id must be a non-empty "
                    "string.",
                )
            task_id = task_id.strip()
            if task_id in task_owners:
                return _fail(
                    gate_id,
                    HardGateFailureClass.PROPOSAL_ISSUE,
                    f"task {task_id!r} belongs to more than one WP "
                    f"({task_owners[task_id]!r} and {wp_id!r}); every task "
                    "must belong to exactly one WP.",
                )
            task_owners[task_id] = wp_id
            if not _nonempty_str(task.get("owner")):
                return _fail(
                    gate_id,
                    HardGateFailureClass.PROPOSAL_ISSUE,
                    f"task {task_id!r} is missing its required 'owner' "
                    "reference field.",
                )
    return _pass(
        gate_id,
        f"{len(wp_ids)} WP(s), {len(task_owners)} task(s); ids unique, every "
        "task in exactly one WP, owner fields present.",
        evidence="workplan.work_packages",
    )


def _validate_wp_deliverables(ctx: HardGateContext) -> HardGateEvaluation:
    gate_id = "WP_DELIVERABLE_CONSISTENCY"
    work_packages, err = _require_workplan(gate_id, ctx)
    if err is not None:
        return err
    wp_ids: set[str] = set()
    task_ids: set[str] = set()
    for index, wp in enumerate(work_packages):
        wp_id, wp_err = _wp_id(wp, index, gate_id)
        if wp_err is not None:
            return wp_err
        assert wp_id is not None  # engine invariant: err is None => id set
        wp_ids.add(wp_id)
        for task in wp.get("tasks") or []:
            if isinstance(task, dict) and _nonempty_str(task.get("id")):
                task_ids.add(task["id"].strip())
    deliverable_ids: set[str] = set()
    for index, wp in enumerate(work_packages):
        wp_id, wp_err = _wp_id(wp, index, gate_id)
        if wp_err is not None:
            return wp_err
        assert wp_id is not None  # engine invariant: err is None => id set
        deliverables = wp.get("deliverables")
        if deliverables is None:
            continue
        if not isinstance(deliverables, list) or not deliverables:
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"WP {wp_id!r} 'deliverables' must be a non-empty array "
                "when present.",
            )
        for d_index, deliverable in enumerate(deliverables):
            if not isinstance(deliverable, dict):
                return _fail(
                    gate_id,
                    HardGateFailureClass.EVIDENCE_INVALID,
                    f"WP {wp_id!r} deliverables[{d_index}] must be an object.",
                )
            d_id = deliverable.get("id")
            if not _nonempty_str(d_id):
                return _fail(
                    gate_id,
                    HardGateFailureClass.EVIDENCE_INVALID,
                    f"WP {wp_id!r} deliverables[{d_index}].id must be a "
                    "non-empty string.",
                )
            d_id = d_id.strip()
            if d_id in deliverable_ids:
                return _fail(
                    gate_id,
                    HardGateFailureClass.EVIDENCE_INVALID,
                    f"duplicate deliverable id {d_id!r}.",
                )
            deliverable_ids.add(d_id)
            # A deliverable record sits under a WP: that WP is its reference.
            if d_id and wp_id not in wp_ids:  # pragma: no cover - structural
                return _fail(
                    gate_id,
                    HardGateFailureClass.PROPOSAL_ISSUE,
                    f"deliverable {d_id!r} references unknown WP {wp_id!r}.",
                )
            for ref_key in ("task_ids", "task_refs"):
                if deliverable.get(ref_key) is None:
                    continue
                refs, ref_err = _iter_string_items(
                    gate_id, deliverable.get(ref_key),
                    f"deliverable {d_id!r} {ref_key}",
                )
                if ref_err is not None:
                    return ref_err
                for ref in refs:
                    if ref.strip() and ref.strip() not in task_ids:
                        return _fail(
                            gate_id,
                            HardGateFailureClass.PROPOSAL_ISSUE,
                            f"deliverable {d_id!r} references unknown task "
                            f"{ref.strip()!r}.",
                        )
    return _pass(
        gate_id,
        f"{len(deliverable_ids)} deliverable(s); ids unique, all WP/task "
        "references resolve.",
        evidence="workplan.work_packages",
    )


def _validate_wp_milestones(ctx: HardGateContext) -> HardGateEvaluation:
    gate_id = "WP_MILESTONE_CONSISTENCY"
    work_packages, err = _require_workplan(gate_id, ctx)
    if err is not None:
        return err
    wp_ids: set[str] = set()
    task_ids: set[str] = set()
    deliverable_ids: set[str] = set()
    for wp in work_packages:
        wp_id, wp_err = _wp_id(wp, 0, gate_id)
        if wp_err is not None:
            return wp_err
        assert wp_id is not None  # engine invariant: err is None => id set
        wp_ids.add(wp_id)
        for task in wp.get("tasks") or []:
            if isinstance(task, dict) and _nonempty_str(task.get("id")):
                task_ids.add(task["id"].strip())
        for deliverable in wp.get("deliverables") or []:
            if isinstance(deliverable, dict) and _nonempty_str(deliverable.get("id")):
                deliverable_ids.add(deliverable["id"].strip())
    milestone_ids: set[str] = set()
    for index, wp in enumerate(work_packages):
        wp_id, wp_err = _wp_id(wp, index, gate_id)
        if wp_err is not None:
            return wp_err
        assert wp_id is not None  # engine invariant: err is None => id set
        milestones = wp.get("milestones")
        if milestones is None:
            continue
        if not isinstance(milestones, list) or not milestones:
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"WP {wp_id!r} 'milestones' must be a non-empty array when "
                "present.",
            )
        for m_index, milestone in enumerate(milestones):
            if not isinstance(milestone, dict):
                return _fail(
                    gate_id,
                    HardGateFailureClass.EVIDENCE_INVALID,
                    f"WP {wp_id!r} milestones[{m_index}] must be an object.",
                )
            m_id = milestone.get("id")
            if not _nonempty_str(m_id):
                return _fail(
                    gate_id,
                    HardGateFailureClass.EVIDENCE_INVALID,
                    f"WP {wp_id!r} milestones[{m_index}].id must be a "
                    "non-empty string.",
                )
            m_id = m_id.strip()
            if m_id in milestone_ids:
                return _fail(
                    gate_id,
                    HardGateFailureClass.EVIDENCE_INVALID,
                    f"duplicate milestone id {m_id!r}.",
                )
            milestone_ids.add(m_id)
            for field_name, valid_ids in (
                ("wp_ref", wp_ids),
                ("task_ref", task_ids),
                ("deliverable_ref", deliverable_ids),
            ):
                ref = milestone.get(field_name)
                if ref is None:
                    continue
                if not _nonempty_str(ref):
                    return _fail(
                        gate_id,
                        HardGateFailureClass.EVIDENCE_INVALID,
                        f"milestone {m_id!r} {field_name} must be a "
                        "non-empty string when supplied.",
                    )
                if ref.strip() not in valid_ids:
                    return _fail(
                        gate_id,
                        HardGateFailureClass.PROPOSAL_ISSUE,
                        f"milestone {m_id!r} references unknown "
                        f"{field_name} {ref.strip()!r}.",
                    )
    return _pass(
        gate_id,
        f"{len(milestone_ids)} milestone(s); ids unique, all supplied "
        "references resolve.",
        evidence="workplan.work_packages",
    )


def _wp_person_months(
    wp: dict[str, Any],
    gate_id: str,
) -> tuple[
    "Decimal | None",
    "list[tuple[str, Decimal]] | None",
    "HardGateEvaluation | None",
]:
    """Read a WP's declared PM total and per-task PM values (or error)."""
    raw_total = wp.get("person_months")
    if raw_total is None:
        return None, None, _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            f"WP {wp.get('id')!r} must declare numeric 'person_months'.",
        )
    total = _as_decimal(raw_total)
    if total is None or total < 0:
        return None, None, _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            f"WP {wp.get('id')!r} person_months must be a numeric value >= 0.",
        )
    task_pms: list[tuple[str, Decimal]] = []
    for task in wp.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        task_id = task.get("id")
        raw_pm = task.get("person_months")
        if raw_pm is None:
            continue
        pm = _as_decimal(raw_pm)
        if pm is None or pm < 0:
            return None, None, _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"task {task_id!r} person_months must be numeric >= 0.",
            )
        task_pms.append((str(task_id), pm))
    return total, task_pms, None


def _validate_person_months(ctx: HardGateContext) -> HardGateEvaluation:
    gate_id = "PERSON_MONTH_CONSISTENCY"
    work_packages, err2 = _require_workplan(gate_id, ctx)
    if err2 is not None:
        return err2
    assert ctx.evidence.workplan is not None  # engine invariant (workplan gate)
    section = ctx.evidence.workplan
    wp_sum = Decimal(0)
    for index, wp in enumerate(work_packages):
        wp_id, wp_err = _wp_id(wp, index, gate_id)
        if wp_err is not None:
            return wp_err
        assert wp_id is not None  # engine invariant: err is None => id set
        total, task_pms, pm_err = _wp_person_months(wp, gate_id)
        if pm_err is not None:
            return pm_err
        assert total is not None and task_pms is not None  # engine invariant
        wp_sum += total
        if task_pms:
            task_sum = sum((pm for _tid, pm in task_pms), Decimal(0))
            if task_sum != total:
                return _fail(
                    gate_id,
                    HardGateFailureClass.PROPOSAL_ISSUE,
                    f"WP {wp_id!r} declared person_months {total} != task "
                    f"sum {task_sum}.",
                    evidence="workplan.work_packages",
                )
    global_total = section.get("total_person_months")
    if global_total is not None:
        declared = _as_decimal(global_total)
        if declared is None or declared < 0:
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                "workplan.total_person_months must be numeric >= 0 when "
                "supplied.",
            )
        if declared != wp_sum:
            return _fail(
                gate_id,
                HardGateFailureClass.PROPOSAL_ISSUE,
                f"declared total_person_months {declared} != WP sum "
                f"{wp_sum}.",
                evidence="workplan.work_packages",
            )
    return _pass(
        gate_id,
        "person-month values numeric/non-negative; supplied sums match "
        "(Decimal-exact).",
        evidence="workplan.work_packages",
    )


# ---------------------------------------------------------------------------
# 11. BUDGET_CONSISTENCY
# ---------------------------------------------------------------------------
def _validate_budget(ctx: HardGateContext) -> HardGateEvaluation:
    gate_id = "BUDGET_CONSISTENCY"
    section, err = _require_object(gate_id, ctx.evidence.budget, "budget")
    if err is not None:
        return err
    assert section is not None  # engine invariant: err is None ⇒ section set
    categories = section.get("categories")
    if not isinstance(categories, list) or not categories:
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            "budget.categories must be a non-empty array of "
            "{name/category, amount} records.",
        )
    calculated = Decimal(0)
    category_ids: set[str] = set()
    for index, category in enumerate(categories):
        if not isinstance(category, dict):
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"budget.categories[{index}] must be an object.",
            )
        name = category.get("name", category.get("category"))
        if not _nonempty_str(name):
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"budget.categories[{index}] requires a non-empty "
                "'name' (or 'category').",
            )
        name = name.strip()
        if name in category_ids:
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"duplicate budget category {name!r}.",
            )
        category_ids.add(name)
        amount = _as_decimal(category.get("amount"))
        if amount is None:
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"budget category {name!r} requires a numeric 'amount'.",
            )
        if amount < 0:
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"budget category {name!r} amount is negative "
                f"({amount}); all amounts must be >= 0.",
            )
        calculated += amount
    participant_total = section.get("participant_total")
    if participant_total is not None:
        declared_participant = _as_decimal(participant_total)
        if declared_participant is None or declared_participant < 0:
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                "budget.participant_total must be numeric >= 0 when supplied.",
            )
        if declared_participant != calculated:
            return _fail(
                gate_id,
                HardGateFailureClass.PROPOSAL_ISSUE,
                f"participant_total {declared_participant} != category sum "
                f"{calculated}.",
                evidence="budget.categories",
            )
    declared_total = section.get("declared_total")
    if declared_total is None:
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            "budget.declared_total is required.",
        )
    declared = _as_decimal(declared_total)
    if declared is None or declared < 0:
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            "budget.declared_total must be numeric >= 0.",
        )
    if declared != calculated:
        return _fail(
            gate_id,
            HardGateFailureClass.PROPOSAL_ISSUE,
            f"declared_total {declared} != calculated category sum "
            f"{calculated}.",
            evidence="budget.categories",
        )
    return _pass(
        gate_id,
        f"budget arithmetic exact (Decimal): {len(category_ids)} category/ies "
        f"sum to {calculated} == declared_total.",
        evidence="budget.categories, budget.declared_total",
    )


# ---------------------------------------------------------------------------
# 12. SUBCONTRACTING_CORE_TASKS
# ---------------------------------------------------------------------------
def _validate_subcontracting(ctx: HardGateContext) -> HardGateEvaluation:
    gate_id = "SUBCONTRACTING_CORE_TASKS"
    section, err = _require_object(
        gate_id, ctx.evidence.subcontracting, "subcontracting"
    )
    if err is not None:
        return err
    assert section is not None  # engine invariant: err is None ⇒ section set
    entries = section.get("entries")
    if entries is None:
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            "subcontracting.entries is required (use [] when there is no "
            "subcontracting).",
        )
    if not isinstance(entries, list):
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            "subcontracting.entries must be an array.",
        )
    if not entries:
        return _pass(
            gate_id,
            "explicitly no subcontracting (entries=[]).",
            evidence="subcontracting.entries",
        )
    work_packages, wp_err = _require_workplan(gate_id, ctx)
    if wp_err is not None:
        return wp_err
    task_ids: set[str] = set()
    for wp in work_packages:
        for task in wp.get("tasks") or []:
            if isinstance(task, dict) and _nonempty_str(task.get("id")):
                task_ids.add(task["id"].strip())
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"subcontracting.entries[{index}] must be an object with "
                "task_id/subcontracted/core_task/justification.",
            )
        task_id = entry.get("task_id")
        if not _nonempty_str(task_id):
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"subcontracting.entries[{index}].task_id must be a "
                "non-empty string.",
            )
        task_id = task_id.strip()
        if task_id not in task_ids:
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"subcontracted task {task_id!r} does not exist in the "
                "structured work plan.",
            )
        if entry.get("subcontracted") is not True:
            return _fail(
                gate_id,
                HardGateFailureClass.EVIDENCE_INVALID,
                f"entry {task_id!r} sets subcontracted != true; only "
                "actually-subcontracted tasks belong in entries.",
            )
        if entry.get("core_task") is True:
            return _fail(
                gate_id,
                HardGateFailureClass.PROPOSAL_ISSUE,
                f"task {task_id!r} is subcontracted AND marked core_task "
                "= true; core tasks may never be subcontracted.",
                evidence="subcontracting.entries",
            )
        if not _nonempty_str(entry.get("justification")):
            return _fail(
                gate_id,
                HardGateFailureClass.PROPOSAL_ISSUE,
                f"subcontracted task {task_id!r} carries no justification.",
                evidence="subcontracting.entries",
            )
    return _pass(
        gate_id,
        f"{len(entries)} subcontracted entry/ies: all exist in the work "
        "plan, none is a core task, all justified.",
        evidence="subcontracting.entries",
    )


# ---------------------------------------------------------------------------
# 13. PAGE_LIMIT
# ---------------------------------------------------------------------------
def _validate_page_limit(ctx: HardGateContext) -> HardGateEvaluation:
    gate_id = "PAGE_LIMIT"
    page_budget, page_err = _load_workspace_json(ctx, "05_CONTROL/PAGE_BUDGET.json")
    if page_err is not None:
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_MISSING
            if "is missing" in page_err or "empty" in page_err
            else HardGateFailureClass.EVIDENCE_INVALID,
            f"PAGE_BUDGET.json unusable: {page_err}",
        )
    if not isinstance(page_budget, dict):
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            "PAGE_BUDGET.json must be a JSON object.",
        )
    method = page_budget.get("measurement_method")
    if not _nonempty_str(method):
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            "PAGE_BUDGET.measurement_method must be a non-empty string.",
        )
    measured = _as_decimal(page_budget.get("measured_pages"))
    if measured is None or measured < 0:
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            "PAGE_BUDGET.measured_pages must be numeric >= 0.",
        )
    maximum = _as_decimal(page_budget.get("max_pages"))
    if maximum is None or maximum <= 0:
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            "PAGE_BUDGET.max_pages must be numeric > 0.",
        )
    method_lower = method.strip().casefold()
    if "estimate" in method_lower:
        # Never claim exact compliance from an estimate: WARN blocks COMPLETE.
        return HardGateEvaluation(
            gate_id=gate_id,
            status=ProposalHardGateStatus.WARN,
            message=(
                f"page measurement is an ESTIMATE ({method.strip()}): "
                f"{measured}/{maximum} pages within limit, but an "
                "authoritative measurement is required before COMPLETE."
            ),
            evidence="05_CONTROL/PAGE_BUDGET.json",
            failure_class=HardGateFailureClass.NONE,
        )
    if measured > maximum:
        return _fail(
            gate_id,
            HardGateFailureClass.PROPOSAL_ISSUE,
            f"measured pages {measured} exceed the limit {maximum}.",
            evidence="05_CONTROL/PAGE_BUDGET.json",
        )
    return _pass(
        gate_id,
        f"{measured}/{maximum} pages by {method.strip()} (authoritative).",
        evidence="05_CONTROL/PAGE_BUDGET.json",
    )


# ---------------------------------------------------------------------------
# 14. INTERNAL_CONTRADICTIONS
# ---------------------------------------------------------------------------
def _validate_internal_contradictions(ctx: HardGateContext) -> HardGateEvaluation:
    gate_id = "INTERNAL_CONTRADICTIONS"
    contradictions, c_err = _load_workspace_json(ctx, "05_CONTROL/CONTRADICTIONS.json")
    if c_err is not None:
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_MISSING
            if "is missing" in c_err or "empty" in c_err
            else HardGateFailureClass.EVIDENCE_INVALID,
            f"CONTRADICTIONS.json unusable: {c_err}",
        )
    if not isinstance(contradictions, dict):
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            "CONTRADICTIONS.json must be a JSON object.",
        )
    unresolved = contradictions.get("unresolved")
    if not isinstance(unresolved, list):
        return _fail(
            gate_id,
            HardGateFailureClass.EVIDENCE_INVALID,
            "CONTRADICTIONS.unresolved must be an array (use [] when there "
            "are none).",
        )
    if unresolved:
        return _fail(
            gate_id,
            HardGateFailureClass.PROPOSAL_ISSUE,
            f"{len(unresolved)} unresolved contradiction(s) recorded: "
            f"{unresolved!r}",
            evidence="05_CONTROL/CONTRADICTIONS.json",
        )
    return _pass(
        gate_id,
        "no unresolved contradiction recorded.",
        evidence="05_CONTROL/CONTRADICTIONS.json",
    )


#: The canonical registry: exactly the 14 canonical gate ids, each bound to
#: its deterministic validator (verified by ``validate_gate_registry``).
GATE_VALIDATORS: dict[str, Any] = {
    "MANDATORY_SECTIONS": _validate_mandatory_sections,
    "CHALLENGE_MAPPING": _validate_challenge_mapping,
    "SOURCE_OF_TRUTH_INTEGRITY": _validate_source_of_truth,
    "UNVERIFIED_CLAIMS": _validate_unverified_claims,
    "CITATION_VERIFICATION": _validate_citations,
    "TERMINOLOGY_CONSISTENCY": _validate_terminology,
    "WP_TASK_CONSISTENCY": _validate_wp_tasks,
    "WP_DELIVERABLE_CONSISTENCY": _validate_wp_deliverables,
    "WP_MILESTONE_CONSISTENCY": _validate_wp_milestones,
    "PERSON_MONTH_CONSISTENCY": _validate_person_months,
    "BUDGET_CONSISTENCY": _validate_budget,
    "SUBCONTRACTING_CORE_TASKS": _validate_subcontracting,
    "PAGE_LIMIT": _validate_page_limit,
    "INTERNAL_CONTRADICTIONS": _validate_internal_contradictions,
}
