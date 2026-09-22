"""Read-only repository fingerprint for the Orchestrator planning guard.

The Orchestrator is planning-only: it must **not** modify the supervised
workspace.  Before invoking it, the batch runner captures a fingerprint of
the repository (HEAD + porcelain status); after it returns, the fingerprint is
captured again and compared.  A difference means the planning call touched the
worktree — the plan is BLOCKED and the violation is surfaced to the operator,
never silently accepted and never auto-reverted.

Everything here is read-only git: ``rev-parse`` / ``symbolic-ref`` /
``status --porcelain``.  Nothing is written, stashed, reset or cleaned.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..drivers.hermes_cli import child_environment

__all__ = [
    "GIT_TIMEOUT_S",
    "RepoFingerprint",
    "capture_repo_fingerprint",
    "fingerprints_equal",
    "run_read_only_git",
]

GIT_TIMEOUT_S = 20.0


def run_read_only_git(
    repo_path: str | Path,
    args: Sequence[str],
    *,
    timeout_s: float = GIT_TIMEOUT_S,
) -> tuple[int, str]:
    """Run one read-only git command against ``repo_path``.

    Returns ``(exit_code, stdout)``.  Never raises for a non-zero exit — the
    caller interprets the absence of a git repository.  The child receives the
    filtered environment (no supervisor ``HERMES_*`` / ``PYTHONPATH`` leak).
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), *[str(a) for a in args]],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            env=child_environment(),
        )
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""
    return int(proc.returncode), (proc.stdout or "").strip()


@dataclass(frozen=True, slots=True)
class RepoFingerprint:
    """A point-in-time, comparable view of the supervised repository."""

    #: True when ``git`` reported we are inside a work tree.
    is_git_repo: bool
    #: The ``HEAD`` commit (``git rev-parse HEAD``), or ``None``.
    head: str | None = None
    #: Current branch (``symbolic-ref --short HEAD``), or ``None``.
    branch: str | None = None
    #: Hex digest of the porcelain status — the worktree fingerprint.
    status_hash: str = ""
    #: Number of status lines (dirty or untracked entries).
    status_lines: int = 0
    #: Raw ``git status --porcelain`` output (bounded for diagnostics).
    raw_status: str = ""

    @property
    def clean_worktree(self) -> bool:
        """True when git reports a clean, tracked-only worktree."""
        return self.status_lines == 0

    def to_json(self) -> str:
        """Compact serialisable form; stored on the plan record."""
        import json

        return json.dumps(
            {
                "is_git_repo": self.is_git_repo,
                "head": self.head,
                "branch": self.branch,
                "status_hash": self.status_hash,
                "status_lines": self.status_lines,
                "raw_status": self.raw_status[:2000],
            },
            sort_keys=True,
        )


def capture_repo_fingerprint(repo_path: str | Path) -> RepoFingerprint:
    """Capture the repository state — HEAD + porcelain status (read-only)."""
    rc, _ = run_read_only_git(repo_path, ["rev-parse", "--is-inside-work-tree"])
    is_git_repo = rc == 0

    head: str | None = None
    branch: str | None = None
    if is_git_repo:
        rc, head = run_read_only_git(repo_path, ["rev-parse", "HEAD"])
        if rc != 0:
            head = None
        rc, branch = run_read_only_git(repo_path, ["symbolic-ref", "--short", "HEAD"])
        if rc != 0:
            branch = None

    rc, status = run_read_only_git(repo_path, ["status", "--porcelain"])
    raw = status if rc == 0 else ""
    status_lines = len([ln for ln in raw.splitlines() if ln.strip()])
    status_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return RepoFingerprint(
        is_git_repo=is_git_repo,
        head=head,
        branch=branch,
        status_hash=status_hash,
        status_lines=status_lines,
        raw_status=raw[:2000],
    )


def fingerprints_equal(before: RepoFingerprint, after: RepoFingerprint) -> bool:
    """True when a planning call left the repository untouched.

    When the workspace is not a git repository the guard is vacuous (both
    fingerprints report ``is_git_repo=False`` and empty hashes) — documented
    in the plan record rather than guessed at.
    """
    if not before.is_git_repo and not after.is_git_repo:
        return True
    return (
        before.head == after.head
        and before.status_hash == after.status_hash
        and before.is_git_repo == after.is_git_repo
    )