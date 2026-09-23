#!/usr/bin/env python3
"""SESSION 008 — config export/import production-style round trip (brief §35).

Uses the real v0.7 feature end to end:

    configure roles → export → inspect (no secrets) →
    fresh isolated PCC home → import → restart → configuration restored

Zero model calls by construction; session bindings are excluded per D-041 and
redacted Generic CLI env values are dropped, never fabricated, on import.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from encomm_pcc import __version__  # noqa: E402
from encomm_pcc.core import (  # noqa: E402
    EventLog,
    ExportInput,
    PipelineController,
    apply_import,
    build_export,
    validate_export,
    write_export,
)
from encomm_pcc.domain import AgentRole  # noqa: E402
from encomm_pcc.persistence import Database  # noqa: E402

GENERIC_CLI_SECRET = {"MY_SECRET_TOKEN": "super-secret-value"}


def fresh_controller(home: Path) -> PipelineController:
    from encomm_pcc.app import restore_state

    db = Database(home / "pipeline_control_center.db").open()
    events = EventLog(db)
    state = restore_state(db)
    controller = PipelineController(database=db, event_log=events, state=state)
    if controller.state.workspace.repo_path == "":
        controller.set_workspace("Acceptance workspace", "")
    return controller


def configure(controller: PipelineController) -> None:
    controller.set_role_config(
        AgentRole.ORCHESTRATOR, engine="codex", project_profile=""
    )
    controller.set_role_config(
        AgentRole.BUILDER,
        engine="hermes",
        project_profile="encomm-pipeline-control-center",
        provider="openrouter",
        model="deepseek/deepseek-v4.1-flash",
    )
    controller.set_role_config(
        AgentRole.TASK_AUDITOR,
        engine="hermes",
        project_profile="encomm-pipeline-control-center",
        provider="openrouter",
        model="deepseek/deepseek-v4.1-flash",
    )
    controller.set_role_config(
        AgentRole.FINAL_AUDITOR,
        engine="codex",
        project_profile="",
        same_as_orchestrator=False,
    )
    controller.set_generic_cli_config(
        AgentRole.BUILDER,
        {
            "executable": "opencode",
            "args": ["run"],
            "prompt_transport": "stdin",
            "result_mode": "stdout_text",
            "timeout_s": 600,
            "env_overrides": dict(GENERIC_CLI_SECRET),
        },
    )


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="encomm-pcc-s008-config-"))
    home_a, home_b = tmp / "home-a", tmp / "home-b"
    home_a.mkdir(parents=True)
    home_b.mkdir(parents=True)
    export_path = tmp / "pcc-config.json"
    print(f"isolated homes : {tmp}")

    # 1. configure + export ---------------------------------------------------
    controller_a = fresh_controller(home_a)
    configure(controller_a)
    source = controller_a.state.role_configs
    document = build_export(
        ExportInput(
            application_version=__version__,
            workspace_name=controller_a.state.workspace.name,
            workspace_path=controller_a.state.workspace.repo_path,
            role_configs={
                role.value: {
                    "engine": source[role].engine,
                    "project_profile": source[role].project_profile,
                    "provider": source[role].provider,
                    "model": source[role].model,
                    "session_policy": source[role].session_policy.value,
                    "same_as_orchestrator": source[role].same_as_orchestrator,
                    "extra": dict(source[role].extra),
                }
                for role in AgentRole
            },
        )
    )
    write_export(document, export_path)
    controller_a.database.close()

    # 2. inspect the exported JSON -------------------------------------------
    raw = export_path.read_text(encoding="utf-8")
    parsed = json.loads(raw)
    assert parsed["format"] == "encomm-pcc-config" and parsed["version"] == 1
    print("env value redacted on export:", "super-secret-value" not in raw)
    assert "super-secret-value" not in raw, "an env override VALUE leaked"
    assert "MY_SECRET_TOKEN" in raw, "the env override KEY should survive redaction"

    # 3. validate + import into a fresh home, then restart ---------------------
    validated = validate_export(parsed)
    controller_b = fresh_controller(home_b)
    apply_import(controller_b, validated)
    controller_b.database.close()

    restored = fresh_controller(home_b)  # "restart": only SQLite survives
    configs = restored.state.role_configs
    assert configs[AgentRole.ORCHESTRATOR].engine == "codex"
    assert configs[AgentRole.BUILDER].engine == "hermes"
    assert configs[AgentRole.BUILDER].model == "deepseek/deepseek-v4.1-flash"
    assert configs[AgentRole.FINAL_AUDITOR].engine == "codex"
    assert configs[AgentRole.FINAL_AUDITOR].same_as_orchestrator is False
    gc = configs[AgentRole.BUILDER].extra.get("generic_cli") or {}
    env = gc.get("env_overrides") or {}
    print(f"generic cli env keys after import: {sorted(env)}")
    assert "MY_SECRET_TOKEN" not in env, "a redacted env value was fabricated on import"
    assert gc.get("executable") == "opencode", "the non-secret config must survive"
    restored.database.close()

    print("CONFIG_ROUND_TRIP PASSED (no secrets exported, none fabricated on import)")
    print(f"temp homes kept : {tmp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
