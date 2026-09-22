"""Application-level configuration: paths, defaults and safe placeholder values.

Kept Qt-free so it can be imported by tests and by headless tools.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..domain import AgentRole, AgentRoleConfig, SessionPolicy, default_session_policy

__all__ = [
    "APP_NAME",
    "DEFAULT_BATCH_SIZE",
    "MAX_BATCH_SIZE",
    "MIN_BATCH_SIZE",
    "AppPaths",
    "placeholder_role_config",
    "default_paths",
]

APP_NAME = "ENCOMM Pipeline Control Center"
APP_SLUG = "encomm-pipeline-control-center"

MIN_BATCH_SIZE = 1
MAX_BATCH_SIZE = 50
DEFAULT_BATCH_SIZE = 5

#: Environment variable that redirects all application state (used by tests).
DATA_DIR_ENV = "ENCOMM_PCC_DATA_DIR"


@dataclass(frozen=True, slots=True)
class AppPaths:
    """Filesystem locations used by the application."""

    data_dir: Path
    database: Path
    logs_dir: Path

    @classmethod
    def resolve(cls, base: Path | None = None) -> "AppPaths":
        if base is None:
            override = os.environ.get(DATA_DIR_ENV)
            if override:
                base = Path(override).expanduser()
            else:
                local_appdata = os.environ.get("LOCALAPPDATA")
                root = Path(local_appdata) if local_appdata else Path.home() / ".local" / "share"
                base = root / APP_NAME
        base = Path(base)
        return cls(
            data_dir=base,
            database=base / "pipeline_control_center.db",
            logs_dir=base / "logs",
        )

    def ensure(self) -> "AppPaths":
        """Create the data/log directories if they do not exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        return self


def default_paths() -> AppPaths:
    return AppPaths.resolve()


#: Placeholder values shown in the first UI.  They are deliberately inert:
#: selecting them must not start a real engine session in v0.1.
_PLACEHOLDERS: dict[AgentRole, dict[str, str]] = {
    AgentRole.ORCHESTRATOR: {
        "engine": "hermes",
        "project_profile": "orchestrator-default",
        "provider": "",
        "model": "",
    },
    AgentRole.TASK_AUDITOR: {
        "engine": "hermes",
        "project_profile": "task-auditor-default",
        "provider": "openrouter",
        "model": "deepseek/deepseek-v4.1-flash",
    },
    AgentRole.BUILDER: {
        "engine": "hermes",
        "project_profile": "builder-default",
        "provider": "openrouter",
        "model": "deepseek/deepseek-v4.1-flash",
    },
    AgentRole.FINAL_AUDITOR: {
        "engine": "hermes",
        "project_profile": "final-auditor-default",
        "provider": "",
        "model": "",
    },
}


def placeholder_role_config(role: AgentRole) -> AgentRoleConfig:
    """Return a fresh config for ``role`` filled with safe placeholder values."""
    values = _PLACEHOLDERS.get(role, {})
    policy: SessionPolicy = default_session_policy(role)
    return AgentRoleConfig(
        role=role,
        engine=values.get("engine", ""),
        project_profile=values.get("project_profile", ""),
        provider=values.get("provider", ""),
        model=values.get("model", ""),
        session_policy=policy,
        same_as_orchestrator=(role is AgentRole.FINAL_AUDITOR),
    )
