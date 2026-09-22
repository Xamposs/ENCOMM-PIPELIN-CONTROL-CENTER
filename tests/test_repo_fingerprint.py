"""Read-only repository fingerprint: the Orchestrator planning guard.

`capture_repo_fingerprint` must never modify the repository — the guard's
whole point is to detect a planning call that did.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from encomm_pcc.core import (
    RepoFingerprint,
    capture_repo_fingerprint,
    fingerprints_equal,
)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    return (proc.stdout or "").strip()


def _init_commit(repo: Path, *, filename: str = "a.txt", content: str = "x") -> None:
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / filename).write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")


def test_captures_head_and_clean_status(tmp_path: Path) -> None:
    _init_commit(tmp_path)
    fp = capture_repo_fingerprint(tmp_path)
    assert fp.is_git_repo is True
    assert fp.head == _git(tmp_path, "rev-parse", "HEAD")
    assert fp.status_lines == 0
    assert fp.clean_worktree is True
    assert fp.status_hash != ""


def test_dirty_worktree_differs_from_clean(tmp_path: Path) -> None:
    _init_commit(tmp_path)
    clean = capture_repo_fingerprint(tmp_path)

    (tmp_path / "uncommitted.txt").write_text("dirty", encoding="utf-8")
    dirty = capture_repo_fingerprint(tmp_path)

    assert dirty.status_lines >= 1
    assert dirty.clean_worktree is False
    assert not fingerprints_equal(clean, dirty)


def test_head_change_is_detected(tmp_path: Path) -> None:
    _init_commit(tmp_path)
    before = capture_repo_fingerprint(tmp_path)

    (tmp_path / "b.txt").write_text("y", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "second")
    after = capture_repo_fingerprint(tmp_path)

    assert before.head != after.head
    assert not fingerprints_equal(before, after)


def test_non_git_directory_is_vacuous_and_never_written(tmp_path: Path) -> None:
    (tmp_path / "plain.txt").write_text("no git here", encoding="utf-8")
    before = capture_repo_fingerprint(tmp_path)
    after = capture_repo_fingerprint(tmp_path)
    assert before.is_git_repo is False
    assert before.head is None
    assert fingerprints_equal(before, after) is True, "non-git guard must be vacuous"
    assert (tmp_path / "plain.txt").read_text(encoding="utf-8") == "no git here"


def test_fingerprint_capture_never_creates_files(tmp_path: Path) -> None:
    _init_commit(tmp_path)
    snapshot = sorted(p.name for p in tmp_path.iterdir())
    capture_repo_fingerprint(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == snapshot


def test_identical_repositories_compare_equal(tmp_path: Path) -> None:
    _init_commit(tmp_path)
    assert fingerprints_equal(
        capture_repo_fingerprint(tmp_path), capture_repo_fingerprint(tmp_path)
    )