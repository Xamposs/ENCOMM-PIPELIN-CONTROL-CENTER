"""Strict parser for the Orchestrator's batch plan.

The Orchestrator's answer is **untrusted model output**, treated exactly like
the auditor's verdict: this module is the only place that turns raw model text
into a :class:`~encomm_pcc.domain.batch_plan.BatchPlan`, and it fails closed.
Any violation — malformed JSON, wrong task count, non-contiguous indices,
duplicate titles, an empty implementation prompt, anything oversize — raises
:class:`PlanParseError` and the batch is BLOCKED.  A malformed plan can
**never** lead to execution.

Envelope.  The planning prompt requires exactly one delimited JSON object:

    <<<BATCH_PLAN_START>>>
    { "batch_title": "...", "batch_objective": "...", "tasks": [...] }
    <<<BATCH_PLAN_END>>>

Like the verdict parser, a balanced-brace fallback scans for the first valid
JSON object when the model wraps it in prose.

Hard rules implemented here (docs/DECISIONS.md D-026):

* bounded raw input before any parsing
* ``json.loads`` only — no eval/exec/YAML, no shell interpretation
* exactly ``expected_count`` tasks (requested batch size) — no truncation,
  no invention, no tolerance
* task indices contiguous ``1..N``; duplicates rejected
* unique, non-empty titles
* ``implementation_prompt`` / ``acceptance_criteria`` / ``audit_focus``
  required and non-empty for every task
* every string and list bounded; task count bounded (1..PLAN_MAX_TASKS)
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from ..domain import (
    PLAN_FOCUS_MAX,
    PLAN_ITEM_MAX,
    PLAN_LIST_MAX,
    PLAN_MAX_TASKS,
    PLAN_PROMPT_MAX,
    PLAN_RAW_MAX,
    PLAN_TITLE_MAX,
    BatchPlan,
    PlannedTask,
)

__all__ = [
    "PLAN_ENVELOPE_END",
    "PLAN_ENVELOPE_START",
    "PlanParseError",
    "parse_batch_plan",
]

PLAN_ENVELOPE_START = "<<<BATCH_PLAN_START>>>"
PLAN_ENVELOPE_END = "<<<BATCH_PLAN_END>>>"


class PlanParseError(ValueError):
    """Raised when a model answer cannot become a strict batch plan.

    ``reason`` is a short stable machine tag (used in tests and event logs);
    ``message`` is a bounded human-readable detail.
    """

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"[{reason}] {message}")


# --------------------------------------------------------------------------
# envelope extraction (mirrors verdict_parser._candidate_objects)
# --------------------------------------------------------------------------
def _candidate_objects(raw: str) -> list[str]:
    """Return balanced-brace JSON candidates from ``raw``.

    The marker-delimited payload comes first; then every balanced ``{...}``
    region in document order (first match wins in :func:`parse_batch_plan`).
    """
    if PLAN_ENVELOPE_START in raw and PLAN_ENVELOPE_END in raw:
        inner = raw.split(PLAN_ENVELOPE_START, 1)[1].split(PLAN_ENVELOPE_END, 1)[0]
        inner = inner.strip()
        if inner.startswith("```"):
            inner = inner.strip("`").strip()
            if inner.lower().startswith("json"):
                inner = inner[4:].strip()
        if inner:
            yield inner

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
# schema validation (strict)
# --------------------------------------------------------------------------
def _bounded_str(
    value: Any, *, limit: int, field: str, allow_empty: bool
) -> str:
    if not isinstance(value, str):
        raise PlanParseError(
            "unexpected_type", f"'{field}' must be a string, got {type(value).__name__}"
        )
    if not allow_empty and not value.strip():
        raise PlanParseError("empty_field", f"'{field}' must not be empty")
    if len(value) > limit:
        raise PlanParseError(
            "oversized_field",
            f"'{field}' exceeds {limit} characters ({len(value)})",
        )
    return value


def _bounded_str_list(value: Any, *, field: str, item_limit: int) -> list[str]:
    if not isinstance(value, list):
        raise PlanParseError(
            "unexpected_type", f"'{field}' must be a list, got {type(value).__name__}"
        )
    if not value:
        raise PlanParseError("empty_field", f"'{field}' must not be empty")
    if len(value) > PLAN_LIST_MAX:
        raise PlanParseError(
            "oversized_list",
            f"'{field}' exceeds {PLAN_LIST_MAX} entries ({len(value)})",
        )
    result: list[str] = []
    for item in value:
        result.append(
            _bounded_str(item, limit=item_limit, field=field, allow_empty=False)
        )
    return result


def _validate_task(raw: Any) -> PlannedTask:
    if not isinstance(raw, Mapping):
        raise PlanParseError(
            "unexpected_type", f"each task must be an object, got {type(raw).__name__}"
        )
    index_raw = raw.get("index")
    if not isinstance(index_raw, int) or isinstance(index_raw, bool):
        raise PlanParseError("unexpected_type", "'index' must be an integer")
    title = _bounded_str(
        raw.get("title"), limit=PLAN_TITLE_MAX, field="task.title", allow_empty=False
    )
    prompt = _bounded_str(
        raw.get("implementation_prompt"),
        limit=PLAN_PROMPT_MAX,
        field="task.implementation_prompt",
        allow_empty=False,
    )
    criteria = _bounded_str_list(
        raw.get("acceptance_criteria"),
        field="task.acceptance_criteria",
        item_limit=PLAN_ITEM_MAX,
    )
    focus = _bounded_str_list(
        raw.get("audit_focus"),
        field="task.audit_focus",
        item_limit=PLAN_FOCUS_MAX,
    )
    return PlannedTask(
        index=int(index_raw),
        title=title,
        implementation_prompt=prompt,
        acceptance_criteria=criteria,
        audit_focus=focus,
    )


def _validate(raw: Mapping[str, Any], *, expected_count: int) -> BatchPlan:
    batch_title = _bounded_str(
        raw.get("batch_title"), limit=PLAN_TITLE_MAX, field="batch_title", allow_empty=False
    )
    batch_objective = _bounded_str(
        raw.get("batch_objective"),
        limit=2000,
        field="batch_objective",
        allow_empty=False,
    )
    tasks_raw = raw.get("tasks")
    if not isinstance(tasks_raw, list):
        raise PlanParseError(
            "unexpected_type", f"'tasks' must be a list, got {type(tasks_raw).__name__}"
        )
    if len(tasks_raw) != expected_count:
        raise PlanParseError(
            "task_count_mismatch",
            f"expected exactly {expected_count} tasks, got {len(tasks_raw)}",
        )
    if expected_count < 1 or expected_count > PLAN_MAX_TASKS:
        raise PlanParseError(
            "invalid_count",
            f"requested batch size must be 1..{PLAN_MAX_TASKS}, got {expected_count}",
        )

    tasks = [_validate_task(t) for t in tasks_raw]

    # Contiguity + uniqueness — duplicates and gaps are structural errors.
    indexes = [t.index for t in tasks]
    if sorted(indexes) != list(range(1, expected_count + 1)):
        raise PlanParseError(
            "non_contiguous_indices",
            f"task indices must be exactly 1..{expected_count}, got {sorted(indexes)}",
        )
    titles = [t.title for t in tasks]
    if len(set(titles)) != len(titles):
        raise PlanParseError("duplicate_titles", "task titles must be unique")

    return BatchPlan(batch_title=batch_title, batch_objective=batch_objective, tasks=tasks)


def parse_batch_plan(raw: str, *, expected_count: int) -> BatchPlan:
    """Parse untrusted Orchestrator output into a strict :class:`BatchPlan`.

    Raises :class:`PlanParseError` on any malformed input; never falls back to
    a lenient interpretation and never fabricates a missing task.
    """
    if raw is None:
        raise PlanParseError("empty_output", "The Orchestrator produced no output.")
    if not isinstance(raw, str):
        raise PlanParseError(
            "unexpected_type", f"planning output must be text, got {type(raw).__name__}"
        )
    if len(raw) > PLAN_RAW_MAX:
        raise PlanParseError(
            "oversized_payload",
            f"planning output exceeds {PLAN_RAW_MAX} characters ({len(raw)}); refusing to parse.",
        )

    last_error: PlanParseError | None = None
    for candidate in _candidate_objects(raw):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = PlanParseError("malformed_json", f"unparseable JSON object: {exc.msg}")
            continue
        if not isinstance(parsed, Mapping):
            raise PlanParseError(
                "unexpected_type",
                f"the verified JSON object must be an object, got {type(parsed).__name__}",
            )
        try:
            return _validate(parsed, expected_count=expected_count)
        except PlanParseError as exc:
            last_error = exc
            # A dict that failed validation might be prose chatter; the next
            # candidate (if any) gets a chance, otherwise we fail closed.

    if last_error is not None:
        raise last_error
    raise PlanParseError(
        "no_json_object", "No valid JSON object was found in the Orchestrator output."
    )