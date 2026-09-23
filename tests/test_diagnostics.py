"""Session 008 — diagnostics + workspace readiness (brief §12/§13/§38).

The diagnostics read model is checked against REAL git workspaces created in
the temp dir (never the PCC repository, never a production data dir).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from encomm_pcc.core import (
    PipelineController,
    WorkspaceReadiness,
    collect_diagnostics,
    format_diagnostics,
    format_workspace_readiness,
    workspace_readiness_for,
)
from encomm_pcc.domain import AgentRole

# Windows shells differ; keep the helper local to this module.
def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        env=None,
    )


@pytest.fixture()
def controller(controller: PipelineController) -> PipelineController:  # noqa: F811
    return controller


def _git_workspace(tmp_path: Path, name: str = "ws") -> Path:
    repo = tmp_path / name
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("probe\n", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


# -- workspace readiness ------------------------------------------------------

def test_nonexistent_workspace_reports_clearly(tmp_path):
    ready = workspace_readiness_for(str(tmp_path / "missing"))
    assert ready.path_exists is False
    assert ready.directory_accessible is False
    assert ready.is_git_repo is False
    assert ready.fingerprint_guard == "UNAVAILABLE"
    text = "\n".join(format_workspace_readiness(ready))
    assert "does NOT exist" in text


def test_git_workspace_head_and_guard(tmp_path):
    repo = _git_workspace(tmp_path)
    ready = workspace_readiness_for(str(repo))
    assert ready.path_exists and ready.directory_accessible
    assert ready.is_git_repo is True
    assert ready.head and len(ready.head) == 40
    assert ready.worktree_clean is True
    assert ready.fingerprint_guard == "ACTIVE"


def test_dirty_worktree_is_surfaced_not_judged(tmp_path):
    repo = _git_workspace(tmp_path)
    (repo / "extra.txt").write_text("uncommitted\n", newline="\n")
    ready = workspace_readiness_for(str(repo))
    assert ready.worktree_clean is False
    assert ready.status_lines >= 1
    text = "\n".join(format_workspace_readiness(ready))
    assert "DIRTY" in text
    assert "operator decides" in text


def test_workspace_path_with_spaces_and_unicode(tmp_path):
    repo = _git_workspace(tmp_path, name="My Repo — Versión ß")
    ready = workspace_readiness_for(str(repo))
    assert ready.is_git_repo is True
    assert ready.worktree_clean is True


def test_deep_workspace_path(tmp_path):
    nested = tmp_path
    for part in ("a", "b", "c", "d", "e", "f", "g", "h", "i", "j"):
        nested = nested / part
    repo = _git_workspace(nested)
    ready = workspace_readiness_for(str(repo))
    assert ready.is_git_repo is True


def test_empty_path_ready_model():
    ready = workspace_readiness_for("")
    assert ready.path_exists is False
    lines = format_workspace_readiness(ready)
    assert lines == ["Workspace: no path configured."]


# -- diagnostics read model ---------------------------------------------------

def test_collect_diagnostics_reports_real_engine_paths(controller):
    report = collect_diagnostics(controller)
    assert report["version"]
    assert report["app_data_dir"]
    assert "OK" in report["database_status"]
    by_id = {d.driver_id: d for d in report["drivers"]}
    assert set(by_id) == {"hermes", "codex", "generic_cli"}
    for row in report["drivers"]:
        assert row.implemented is True
        # This host has Hermes and Codex installed; a found path must be a
        # real existing file.
        if row.executable:
            assert Path(row.executable).exists()
    # The Generic CLI takes its executable from the per-role configuration —
    # it has no fixed binary name to search for.
    assert by_id["generic_cli"].searched == ()
    assert by_id["generic_cli"].executable is None
    # Hermes and Codex do declare their candidate binaries.
    assert by_id["hermes"].searched
    assert by_id["codex"].searched


def test_diagnostics_text_contains_no_secrets_and_no_marker_leakage(controller):
    report = collect_diagnostics(controller)
    text = format_diagnostics(report)
    assert "Version:" in text
    assert "Application data:" in text
    assert "Database:" in text
    # The text is derived from versions, paths and binary locations only.
    for forbidden in ("api_key", "apikey", "authorization:", "password=", "secret"):
        assert forbidden not in text.lower()
    assert "ENCOMM_PCC_GENERIC" not in text  # no marker leakage


def test_missing_engine_message_is_operator_friendly(controller, monkeypatch):
    from encomm_pcc.core import diagnostics

    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)
    report = collect_diagnostics(controller)
    text = format_diagnostics(report)
    assert "not detected on PATH" in text
    assert "Install/configure it before selecting it for a role." in text


def test_generic_cli_role_state_is_reported(controller):
    controller.set_role_config(AgentRole.ORCHESTRATOR, engine="generic_cli")
    report = collect_diagnostics(controller)
    assert report["generic_cli"] == {"ORCHESTRATOR": "NOT_CONFIGURED"}
    controller.set_generic_cli_config(
        AgentRole.ORCHESTRATOR,
        {
            "executable": "python",
            "args": ["-c", "print('ok')"],
            "prompt_transport": "stdin",
            "result_mode": "stdout_text",
            "timeout_s": 30,
        },
    )
    report = collect_diagnostics(controller)
    assert report["generic_cli"] == {"ORCHESTRATOR": "CONFIGURED"}


def test_ready_class_matches_helper(tmp_path):
    repo = _git_workspace(tmp_path)
    assert (
        WorkspaceReadiness.for_path(str(repo)).head
        == workspace_readiness_for(str(repo)).head
    )
