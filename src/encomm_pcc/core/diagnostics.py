"""Session 008 — operator diagnostics and workspace readiness (brief §12/§13).

A pure, Qt-free read model: it reports what is installed, where data lives,
whether the database is healthy, and whether the selected workspace is ready
for a real batch.  It NEVER launches an engine, NEVER reads credentials, and
NEVER modifies anything — engine availability is a ``shutil.which`` probe and
workspace facts come from the read-only git helpers in
:mod:`encomm_pcc.core.repo_fingerprint`.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .. import __version__
from ..drivers import IMPLEMENTED_DRIVERS
from .config import AppPaths
from .repo_fingerprint import RepoFingerprint, capture_repo_fingerprint

__all__ = [
    "DriverDiagnostics",
    "WorkspaceReadiness",
    "workspace_readiness_for",
    "collect_driver_diagnostics",
    "collect_diagnostics",
    "format_diagnostics",
    "format_workspace_readiness",
    "GENERIC_CLI_ENGINE_ID",
]

#: The Generic CLI driver id (kept in sync with the driver module's constant).
GENERIC_CLI_ENGINE_ID = "generic_cli"

_TEXT_CAP = 400


@dataclass(frozen=True, slots=True)
class DriverDiagnostics:
    """Availability facts for one registered engine."""

    driver_id: str
    display_name: str
    implemented: bool
    #: The first executable of the driver found on PATH, if any.
    executable: str | None
    #: Every candidate binary name the driver would accept.
    searched: tuple[str, ...]


def collect_driver_diagnostics() -> list[DriverDiagnostics]:
    """Probe every registered driver WITHOUT launching anything."""
    rows: list[DriverDiagnostics] = []
    for cls in IMPLEMENTED_DRIVERS:
        caps = cls.capabilities()
        executable: str | None = None
        for name in cls.executables:
            found = shutil.which(name)
            if found:
                executable = found
                break
        rows.append(
            DriverDiagnostics(
                driver_id=cls.driver_id,
                display_name=caps.display_name,
                implemented=caps.implemented,
                executable=executable,
                searched=tuple(cls.executables),
            )
        )
    return rows


@dataclass(frozen=True, slots=True)
class WorkspaceReadiness:
    """Whether the selected workspace can host a real batch (brief §13)."""

    path: str
    path_exists: bool
    directory_accessible: bool
    is_git_repo: bool
    head: str | None
    branch: str | None
    worktree_clean: bool | None  # None = not a git repo / unknown
    status_lines: int
    #: ACTIVE only when the read-only git guard can actually compare
    #: fingerprints (a git repository); UNAVAILABLE otherwise (vacuous guard).
    fingerprint_guard: str

    @classmethod
    def for_path(cls, path: str) -> "WorkspaceReadiness":
        exists = False
        accessible = False
        if path:
            candidate = Path(path)
            exists = candidate.exists()
            if exists and candidate.is_dir():
                try:
                    next(candidate.iterdir(), None)
                    accessible = True
                except OSError:
                    accessible = False

        fingerprint = RepoFingerprint(is_git_repo=False)
        if accessible:
            try:
                fingerprint = capture_repo_fingerprint(path)
            except Exception:  # noqa: BLE001 - readiness is advisory, never fatal
                fingerprint = RepoFingerprint(is_git_repo=False)

        clean: bool | None = None
        if fingerprint.is_git_repo:
            clean = fingerprint.clean_worktree
        return cls(
            path=path or "",
            path_exists=exists,
            directory_accessible=accessible,
            is_git_repo=fingerprint.is_git_repo,
            head=fingerprint.head,
            branch=fingerprint.branch,
            worktree_clean=clean,
            status_lines=fingerprint.status_lines,
            fingerprint_guard="ACTIVE" if fingerprint.is_git_repo else "UNAVAILABLE",
        )


def workspace_readiness_for(path: str) -> WorkspaceReadiness:
    """Compute the readiness read model for one workspace path."""
    return WorkspaceReadiness.for_path(path)


def collect_diagnostics(
    controller, *, paths: AppPaths | None = None  # noqa: ANN001 - PipelineController (Qt-free import cycle avoided)
) -> dict:
    """Assemble the full diagnostics read model (brief §12)."""
    resolved = paths or AppPaths.resolve()
    db = controller.database
    workspace = controller.state.workspace

    drivers = collect_driver_diagnostics()

    generic_cli_state: dict[str, str] = {}
    for role, config in controller.state.role_configs.items():
        if config.engine != GENERIC_CLI_ENGINE_ID:
            continue
        configured = bool(config.extra.get("generic_cli"))
        generic_cli_state[role.value] = "CONFIGURED" if configured else "NOT_CONFIGURED"

    try:
        db_status = (
            f"OK (schema v{db.schema_version()}, {db.path})"
            if db.path != ":memory:"
            else "OK (in-memory)"
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics must never raise
        db_status = f"ERROR: {exc}"

    return {
        "version": __version__,
        "app_data_dir": str(resolved.data_dir),
        "database_status": db_status,
        "workspace_name": workspace.name,
        "workspace_path": workspace.repo_path,
        "workspace": WorkspaceReadiness.for_path(workspace.repo_path),
        "drivers": drivers,
        "generic_cli": generic_cli_state,
    }


def _short(value: str, cap: int = _TEXT_CAP) -> str:
    value = value.strip()
    return value if len(value) <= cap else value[: cap - 3] + "..."


def format_workspace_readiness(ready: WorkspaceReadiness) -> list[str]:
    """Human-readable readiness lines; a dirty worktree is surfaced, never judged."""
    if not ready.path:
        return ["Workspace: no path configured."]
    if not ready.path_exists:
        return [f"Workspace path does NOT exist: {ready.path}"]
    if not ready.directory_accessible:
        return [f"Workspace path is not accessible: {ready.path}"]
    lines = [f"Workspace path: {ready.path}"]
    if ready.is_git_repo:
        lines.append(f"Git repository: YES ({ready.branch or 'detached HEAD'})")
        lines.append(f"HEAD: {ready.head or 'unknown'}")
        if ready.worktree_clean:
            lines.append("Worktree: CLEAN")
        else:
            lines.append(
                f"Worktree: DIRTY ({ready.status_lines} entries) — left untouched; "
                "the operator decides how to proceed."
            )
        lines.append(f"Read-only fingerprint guard: {ready.fingerprint_guard}")
    else:
        lines.append("Git repository: NO — read-only planning/audit guards are UNAVAILABLE.")
    return lines


def format_diagnostics(report: dict) -> str:
    """Render the diagnostics read model as operator-facing text (no secrets)."""
    lines = [
        f"Version: {report['version']}",
        f"Application data: {report['app_data_dir']}",
        f"Database: {_short(report['database_status'])}",
        "",
    ]
    lines.extend(format_workspace_readiness(report["workspace"]))
    lines.append("")
    lines.append("Engines:")
    for driver in report["drivers"]:
        state = "IMPLEMENTED" if driver.implemented else "NOT IMPLEMENTED"
        if driver.executable:
            lines.append(f"  {driver.display_name}: {state} — {driver.executable}")
        else:
            lines.append(
                f"  {driver.display_name}: not detected on PATH "
                f"(searched: {', '.join(driver.searched)}). "
                "Install/configure it before selecting it for a role."
            )
    if report["generic_cli"]:
        lines.append("Generic CLI roles:")
        for role, state in sorted(report["generic_cli"].items()):
            lines.append(f"  {role}: {state}")
    return "\n".join(lines)
