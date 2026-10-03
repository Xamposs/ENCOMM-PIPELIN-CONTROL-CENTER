"""Bounded source-budget gate — the canonical blueprint is never truncated.

Session 019 (Proposal Factory V2).  The review snapshot's per-file read cap
(``MAX_SNAPSHOT_FILE_CHARS``) protects packet rendering, but a large MASTER
BLUEPRINT must be RECEIVABLE BY ASTRA whole when the configured budget
allows it.  This module decides, BEFORE any model invocation, whether the
canonical source set fits the configured character budget:

* fits       → the run may proceed (the full canonical text reaches packets);
* does not   → a BLOCKING, operator-actionable error is raised — the sources
               are never silently summarized away or truncated.

The budget is operator configuration, not a hardcoded number.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .source_snapshot import MAX_SNAPSHOT_FILE_CHARS

__all__ = [
    "DEFAULT_SOURCE_BUDGET_CHARS",
    "SourceBudget",
    "SourceBudgetExceededError",
    "blueprint_char_count",
    "check_source_budget",
]


#: Default canonical blueprint budget (~1.1M chars ≈ 200 pages at ~5,500
#: chars/page — the brief's suggested 1,000,000–1,200,000 band).
DEFAULT_SOURCE_BUDGET_CHARS = 1_100_000


class SourceBudgetExceededError(RuntimeError):
    """The canonical sources exceed the configured budget — BLOCK before AI.

    Carries operator-actionable numbers; never a silent truncation.
    """

    def __init__(
        self,
        reason: str,
        message: str,
        *,
        total_chars: int = 0,
        budget_chars: int = 0,
    ) -> None:
        self.reason = reason
        self.total_chars = total_chars
        self.budget_chars = budget_chars
        super().__init__(f"[{reason}] {message}")


@dataclass(slots=True)
class SourceBudget:
    """Operator-configured source budget (characters)."""

    blueprint_max_chars: int = DEFAULT_SOURCE_BUDGET_CHARS

    def __post_init__(self) -> None:
        if not isinstance(self.blueprint_max_chars, int) or isinstance(
            self.blueprint_max_chars, bool
        ):
            raise ValueError("blueprint_max_chars must be an integer.")
        if self.blueprint_max_chars < 1000:
            raise ValueError(
                "blueprint_max_chars must be at least 1,000 characters "
                f"(got {self.blueprint_max_chars})."
            )

    def to_dict(self) -> dict[str, int]:
        return {"blueprint_max_chars": self.blueprint_max_chars}

    @classmethod
    def from_dict(cls, data: dict[str, int] | None) -> "SourceBudget":
        data = data or {}
        return cls(blueprint_max_chars=int(data.get("blueprint_max_chars") or DEFAULT_SOURCE_BUDGET_CHARS))


def blueprint_char_count(workspace: Path) -> int:
    """Character count of the canonical blueprint (0 when absent/empty)."""
    path = Path(workspace) / "00_SOURCE_OF_TRUTH" / "MASTER_BLUEPRINT.md"
    if not path.is_file():
        return 0
    try:
        return len(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return 0


def check_source_budget(
    workspace: Path, budget: SourceBudget | None = None
) -> int:
    """Verify the canonical blueprint fits the budget; return its char count.

    Raises :class:`SourceBudgetExceededError` (operator-actionable) when the
    blueprint exceeds the budget.  A MISSING blueprint is NOT this module's
    error — workflow-level validation owns that.
    """
    budget = budget or SourceBudget()
    total = blueprint_char_count(workspace)
    if total > budget.blueprint_max_chars:
        raise SourceBudgetExceededError(
            "source_budget_exceeded",
            f"the canonical MASTER BLUEPRINT is {total} characters but the "
            f"configured source budget is {budget.blueprint_max_chars}. "
            "The blueprint is never truncated or silently summarized: raise "
            "the budget (if the configured engine context allows it) or split "
            "the source before running.",
            total_chars=total,
            budget_chars=budget.blueprint_max_chars,
        )
    return total


#: Re-exported so callers quote ONE per-file cap constant.
SNAPSHOT_FILE_CAP = MAX_SNAPSHOT_FILE_CHARS
