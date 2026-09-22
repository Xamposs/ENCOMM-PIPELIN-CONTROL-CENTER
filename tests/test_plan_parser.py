"""Strict BatchPlan parser: it must fail closed on anything malformed.

The Orchestrator's plan is untrusted model output.  These tests pin the exact
contract: valid 1/4/5-task plans parse; every violation — wrong count,
non-contiguous or duplicate indices, duplicate titles, empty prompt/criteria/
focus, oversize — raises ``PlanParseError`` and can never become a task list.
"""

from __future__ import annotations

import json

import pytest

from encomm_pcc.core import (
    PLAN_ENVELOPE_END,
    PLAN_ENVELOPE_START,
    PlanParseError,
    parse_batch_plan,
)
from encomm_pcc.domain import PLAN_RAW_MAX


def envelope(plan: dict) -> str:
    return f"{PLAN_ENVELOPE_START}\n{json.dumps(plan, indent=2)}\n{PLAN_ENVELOPE_END}"


def plan_with(n_tasks: int, **task_overrides) -> dict:  # noqa: ANN003
    tasks = []
    for i in range(1, n_tasks + 1):
        task = {
            "index": i,
            "title": f"Task {i}",
            "implementation_prompt": f"Implement feature {i} so its test passes.",
            "acceptance_criteria": [f"test_feature_{i}.py passes"],
            "audit_focus": [f"verify feature {i} behaviour in the repo"],
        }
        task.update(task_overrides)
        tasks.append(task)
    return {
        "batch_title": f"Batch of {n_tasks}",
        "batch_objective": f"Implement exactly {n_tasks} small features.",
        "tasks": tasks,
    }


# -- valid plans -------------------------------------------------------------
@pytest.mark.parametrize("n", [1, 4, 5])
def test_valid_plan_parses(n: int) -> None:
    plan = parse_batch_plan(envelope(plan_with(n)), expected_count=n)
    assert plan.task_count == n
    assert [t.index for t in plan.tasks] == list(range(1, n + 1))
    assert len({t.title for t in plan.tasks}) == n
    assert all(t.implementation_prompt.strip() for t in plan.tasks)
    assert all(t.acceptance_criteria for t in plan.tasks)
    assert all(t.audit_focus for t in plan.tasks)


def test_valid_plan_wrapped_in_prose_still_parses() -> None:
    raw = (
        "Here is my plan.\n"
        + envelope(plan_with(2))
        + "\nI hope this works."
    )
    plan = parse_batch_plan(raw, expected_count=2)
    assert plan.task_count == 2


def test_valid_plan_with_balanced_braces_only() -> None:
    plan = parse_batch_plan(json.dumps(plan_with(3)), expected_count=3)
    assert plan.task_count == 3


# -- exact task count --------------------------------------------------------
def test_task_count_mismatch_fewer_is_rejected() -> None:
    with pytest.raises(PlanParseError) as exc:
        parse_batch_plan(envelope(plan_with(3)), expected_count=4)
    assert exc.value.reason == "task_count_mismatch"


def test_task_count_mismatch_more_is_rejected() -> None:
    with pytest.raises(PlanParseError) as exc:
        parse_batch_plan(envelope(plan_with(5)), expected_count=4)
    assert exc.value.reason == "task_count_mismatch"


def test_empty_task_list_is_rejected() -> None:
    with pytest.raises(PlanParseError) as exc:
        parse_batch_plan(envelope(plan_with(0)), expected_count=0)
    assert exc.value.reason == "invalid_count"


def test_expected_count_beyond_the_supported_bound_is_rejected() -> None:
    with pytest.raises(PlanParseError) as exc:
        parse_batch_plan(envelope(plan_with(6)), expected_count=6)
    assert exc.value.reason == "invalid_count"


# -- structure ---------------------------------------------------------------
def test_duplicate_indices_are_rejected() -> None:
    raw = plan_with(2)
    raw["tasks"][1]["index"] = 1
    with pytest.raises(PlanParseError) as exc:
        parse_batch_plan(envelope(raw), expected_count=2)
    assert exc.value.reason == "non_contiguous_indices"


def test_non_contiguous_indices_are_rejected() -> None:
    raw = plan_with(3)
    raw["tasks"][2]["index"] = 5  # 1,2,5 — gap + out of range
    with pytest.raises(PlanParseError) as exc:
        parse_batch_plan(envelope(raw), expected_count=3)
    assert exc.value.reason == "non_contiguous_indices"


def test_duplicate_titles_are_rejected() -> None:
    raw = plan_with(2)
    raw["tasks"][1]["title"] = raw["tasks"][0]["title"]
    with pytest.raises(PlanParseError) as exc:
        parse_batch_plan(envelope(raw), expected_count=2)
    assert exc.value.reason == "duplicate_titles"


def test_missing_implementation_prompt_is_rejected() -> None:
    raw = plan_with(1)
    raw["tasks"][0]["implementation_prompt"] = ""  # empty, not absent
    with pytest.raises(PlanParseError) as exc:
        parse_batch_plan(envelope(raw), expected_count=1)
    assert exc.value.reason == "empty_field"


def test_absent_implementation_prompt_key_is_rejected() -> None:
    raw = plan_with(1)
    del raw["tasks"][0]["implementation_prompt"]
    with pytest.raises(PlanParseError) as exc:
        parse_batch_plan(envelope(raw), expected_count=1)
    assert exc.value.reason == "unexpected_type"


def test_empty_acceptance_criteria_is_rejected() -> None:
    raw = plan_with(1)
    raw["tasks"][0]["acceptance_criteria"] = []
    with pytest.raises(PlanParseError) as exc:
        parse_batch_plan(envelope(raw), expected_count=1)
    assert exc.value.reason == "empty_field"


def test_empty_audit_focus_is_rejected() -> None:
    raw = plan_with(1)
    raw["tasks"][0]["audit_focus"] = []
    with pytest.raises(PlanParseError) as exc:
        parse_batch_plan(envelope(raw), expected_count=1)
    assert exc.value.reason == "empty_field"


def test_missing_batch_title_is_rejected() -> None:
    raw = plan_with(1)
    raw["batch_title"] = ""  # empty, not absent
    with pytest.raises(PlanParseError) as exc:
        parse_batch_plan(envelope(raw), expected_count=1)
    assert exc.value.reason == "empty_field"


def test_non_string_prompt_is_rejected() -> None:
    raw = plan_with(1)
    raw["tasks"][0]["implementation_prompt"] = 12345
    with pytest.raises(PlanParseError) as exc:
        parse_batch_plan(envelope(raw), expected_count=1)
    assert exc.value.reason == "unexpected_type"


def test_non_integer_index_is_rejected() -> None:
    raw = plan_with(1)
    raw["tasks"][0]["index"] = "1"
    with pytest.raises(PlanParseError) as exc:
        parse_batch_plan(envelope(raw), expected_count=1)
    assert exc.value.reason == "unexpected_type"


# -- bounds ------------------------------------------------------------------
def test_oversized_raw_payload_is_rejected() -> None:
    with pytest.raises(PlanParseError) as exc:
        parse_batch_plan("x" * (PLAN_RAW_MAX + 1), expected_count=1)
    assert exc.value.reason == "oversized_payload"


def test_oversized_prompt_is_rejected() -> None:
    raw = plan_with(1)
    raw["tasks"][0]["implementation_prompt"] = "p" * 30_000
    with pytest.raises(PlanParseError) as exc:
        parse_batch_plan(envelope(raw), expected_count=1)
    assert exc.value.reason == "oversized_field"


def test_oversized_criteria_list_is_rejected() -> None:
    raw = plan_with(1)
    raw["tasks"][0]["acceptance_criteria"] = ["c"] * 20
    with pytest.raises(PlanParseError) as exc:
        parse_batch_plan(envelope(raw), expected_count=1)
    assert exc.value.reason == "oversized_list"


# -- malformed text ----------------------------------------------------------
def test_malformed_json_is_rejected() -> None:
    with pytest.raises(PlanParseError) as exc:
        parse_batch_plan(f"{PLAN_ENVELOPE_START}\n{{not json\n{PLAN_ENVELOPE_END}", expected_count=1)
    assert exc.value.reason in ("malformed_json", "no_json_object")


def test_no_json_object_at_all_is_rejected() -> None:
    with pytest.raises(PlanParseError) as exc:
        parse_batch_plan("This plan is prose only. No JSON anywhere.", expected_count=1)
    assert exc.value.reason == "no_json_object"


def test_empty_output_is_rejected() -> None:
    with pytest.raises(PlanParseError) as exc:
        parse_batch_plan("", expected_count=1)
    assert exc.value.reason == "no_json_object"


def test_none_output_is_rejected() -> None:
    with pytest.raises(PlanParseError) as exc:
        parse_batch_plan(None, expected_count=1)  # type: ignore[arg-type]
    assert exc.value.reason == "empty_output"


def test_wrong_expected_count_cannot_be_silently_filled() -> None:
    """A 3-task answer can never become a valid 4-task batch."""
    with pytest.raises(PlanParseError):
        parse_batch_plan(envelope(plan_with(3)), expected_count=4)
    with pytest.raises(PlanParseError):
        parse_batch_plan(envelope(plan_with(5)), expected_count=4)  # never truncated