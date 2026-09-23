# ENCOMM Pipeline Control Center

A local Windows desktop application that supervises real AI coding pipelines:
an orchestrator plans a batch of tasks, a builder implements each one in a
fresh session, a task auditor checks it (with a capped fix loop), and a final
auditor closes the batch and hands off the next one — all through external
engine CLIs (Hermes, Codex, or any compatible command-line agent), with
SQLite persistence, restart recovery, and strict fail-closed parsing of every
model answer.

**Current version: 0.8.0 — Windows release candidate.**

- **Works today:** role-based mixed-engine batches (e.g. Codex plans → Hermes
  builds → Hermes audits → Codex final audit), autonomous multi-task batches
  with the audit/fix loop, one-call final audit + next-batch handoff,
  restart/recovery at every phase, config export/import, run history, and a
  Windows packaged build with a self-test mode.
- **Operator guide:** [docs/USER_GUIDE.md](docs/USER_GUIDE.md).
- **What is deliberately not implemented:** see
  [docs/CURRENT_STATE.md](docs/CURRENT_STATE.md) §5.

---

## Requirements

- Windows 10/11 (the release is a Windows build)
- **Packaged release:** nothing else — engines (Hermes, Codex, …) are
  external tools discovered on PATH at runtime
- **From source:** Python 3.11+ and PySide6 6.6+ (the only runtime dependency)

## Run from source

```bash
pip install -r requirements.txt
python main.py
```

## Build the Windows release

```powershell
python -m pip install pyinstaller
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

The artifact is `dist\ENCOMM-PCC\ENCOMM-PCC.exe` (one-folder build). Verify
it without opening a window:

```bash
ENCOMM-PCC.exe --smoke-test   # exit 0 = package, SQLite, Qt, drivers all OK
```

The packaged app makes zero model calls at startup and bundles no
credentials — Hermes and Codex are discovered on the target machine at
runtime, and missing engines show as unavailable instead of crashing.

## Where data lives

```text
%LOCALAPPDATA%\ENCOMM Pipeline Control Center\
├── pipeline_control_center.db   # SQLite — all durable state
└── logs\bootstrap.log           # bounded rotating diagnostics log
```

Override with the `ENCOMM_PCC_DATA_DIR` environment variable.

## Basic workflow

1. Set the **WORKSPACE** path (a git repository is strongly recommended).
2. Check **DIAGNOSTICS**: data dir, database, workspace readiness, engines.
3. Configure the four roles (ORCHESTRATOR / BUILDER / TASK_AUDITOR /
   FINAL_AUDITOR) — engines are configuration, not code.
4. Enter the **PROJECT BRIEF**, choose the batch size (1–5), press
   **PLAN + START BATCH**.
5. The batch runs autonomously (fresh Builder → Task Auditor → capped fix
   loop → next task). Pause/Stop take effect at safe boundaries.
6. At `READY_FOR_FINAL_AUDIT`, press **RUN FINAL AUDIT** — one call returns
   the cumulative verdict AND the next batch plan.
7. On PASS: **VIEW NEXT TASKS**, then **START NEXT BATCH** (zero AI calls).

Full instructions with every button:
[docs/USER_GUIDE.md](docs/USER_GUIDE.md).

## Test

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The suite (570+ tests) never touches a network or a real engine. Real
end-to-end runs are separate, explicitly invoked scripts:

```bash
python scripts/session_004_multitask_smoke.py --fake                    # offline batch proof
python scripts/session_005_final_audit_smoke.py --fake                  # offline final-audit proof
python scripts/session_008_generic_cli_proof.py                         # ONE real Generic CLI call
python scripts/session_008_acceptance.py --profile <hermes-profile>     # real mixed-engine acceptance
python scripts/session_008_config_roundtrip.py                          # config export/import round trip
```

## Known v0.8 limitations

- No mid-prompt cancellation (boundary-only pause/stop; a running prompt
  finishes and persists first).
- Read-only planning/audit guards are vacuous on non-git workspaces.
- The packaged app ships the Codex/Hermes/Generic CLI drivers only; dedicated
  Claude Code / OpenCode / Ollama / Kimi adapters do not exist (the Generic
  CLI covers simple third-party CLIs).
- Config export/import is a core API surface, not a UI dialog.
- Restart mid-batch rebinds the shared Task Auditor session from SQLite; if
  the provider lost the session, the run fails explicitly instead of
  silently starting a new one.
- opencode's free-tier models require opencode ≥ 1.18.0 (the Session 008
  Generic CLI proof used an already-configured provider instead).

---

## Documentation

| File | Purpose |
|---|---|
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | **Operator guide** — every section and button |
| [docs/CURRENT_STATE.md](docs/CURRENT_STATE.md) | Canonical handoff file (read first in every dev session) |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | The actual architecture, including deliberate non-goals |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Architecture decisions with reasons |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Phased plan |
| [docs/reports/](docs/reports/) | Per-session reports with evidence |

---

## License

Proprietary — ENCOMM.
