"""Planning packet: deterministic, self-contained, exactly-N constrained."""

from __future__ import annotations

from encomm_pcc.core import (
    PLAN_ENVELOPE_END,
    PLAN_ENVELOPE_START,
    render_planning_prompt,
)


def test_prompt_contains_brief_boundary_and_exact_count() -> None:
    prompt = render_planning_prompt(
        project_brief="Build a tiny math library.",
        batch_size=4,
        workspace_path=r"C:\scratch\repo",
    )
    assert "Build a tiny math library." in prompt
    assert "C:\\scratch\\repo" in prompt
    assert "exactly 4 tasks" in prompt
    assert PLAN_ENVELOPE_START in prompt and PLAN_ENVELOPE_END in prompt


def test_prompt_forbids_editing_and_demands_self_contained_tasks() -> None:
    prompt = render_planning_prompt(
        project_brief="Plan it.", batch_size=2, workspace_path="/tmp/repo"
    )
    lower = prompt.lower()
    assert "planning only" in lower
    assert "must not edit" in lower
    assert "self-contained" in lower
    assert "read-only" in lower


def test_prompt_validates_batch_size_string_for_the_model() -> None:
    prompt = render_planning_prompt(
        project_brief="x", batch_size=5, workspace_path="/tmp/repo"
    )
    assert "exactly 5 tasks" in prompt
    assert "1..5" in prompt