# ENCOMM Pipeline Control Center

A local Windows desktop application that supervises real AI coding pipelines:
an orchestrator plans a batch of tasks, a builder implements each one in a
fresh session, a task auditor checks it (with a capped fix loop), and a final
auditor closes the batch and hands off the next one — all through external
engine CLIs (Hermes, Codex, or any compatible command-line agent), with
SQLite persistence, restart recovery, and strict fail-closed parsing of every
model answer.

**Current version: 1.1.0 — Session 015: the REAL ORCHESTRATOR integration executor (strict envelope → fail-closed parse → mutation guard → runtime-owned atomic MASTER_PROPOSAL write), post-integration version freeze, durable integration_result.json, zero-AI clean-PASS bypass, review-freshness gate, NEXT_ITERATION.json revision handoff and one-iteration composition; Sessions 012–014 review infrastructure unchanged; 1.0.1's production UI unchanged.**

- **Works today:** Simple Mode (default, production surface) — enter the goal, pick an
 engine for Architect / Coder / Auditor, configure the Hermes profile (Coder and
 Auditor) plus provider/model, choose the Coder session mode, and press START:
 batch after batch runs autonomously with the audit/fix loop, an automatic
 final audit after each batch, and a zero-AI handoff to the next batch.
 ARCHITECT plans and final-audits on **one real thread**: planning and the
 Final Audit resume the same external session (live → durable binding →
 persisted plan after restart), and the Final Audit inherits the Architect's
 profile/provider/model configuration. The engine dropdowns are driven by the
 installed driver registry; Hermes profile changes re-scope session discovery
 and clear stale bindings automatically. Coder sessions run fresh by default,
 or **Resume selected session ONCE** for an interrupted build.
 Advanced Mode (every fine-grained control, the full Session 008 window) is
 available with `python main.py --debug-ui`.
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

## Basic workflow (Simple Mode)

1. Set the **WORKSPACE** name + path (a git repository is strongly
   recommended) and read the **DIAGNOSTICS** line.
2. **ARCHITECT** (plans and final-audits; one Codex thread), **CODER**
   (builds each task; fresh session every time), **AUDITOR** (checks each
   task; one session for the batch): pick engine, Hermes profile,
   provider, model — all from Simple Mode; they write the same durable role
   configuration as Advanced Mode.
3. Enter the **PROJECT GOAL** (the brief the Architect plans from), choose
   tasks per batch (1–5).
4. Press **START**. Leave **CONTINUOUS** ticked for batch-after-batch
   operation: each batch ends with an automatic final audit; a PASS hands
   off to the next batch with zero AI calls. PAUSE and STOP take effect at
   safe boundaries and are reported in plain language.
5. If a Builder session was lost provider-side (e.g. after a machine
   restart), the **CODER RECOVERY OVERRIDE** offers to resume the saved
   session for exactly one operation — an explicit operator decision, never
   automatic.

Full instructions with every button:
[docs/USER_GUIDE.md](docs/USER_GUIDE.md).

## Test

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The suite (600+ tests) never touches a network or a real engine. Real
end-to-end runs are separate, explicitly invoked scripts:

```bash
python scripts/session_004_multitask_smoke.py --fake                    # offline batch proof
python scripts/session_005_final_audit_smoke.py --fake                  # offline final-audit proof
python scripts/session_008_generic_cli_proof.py                         # ONE real Generic CLI call
python scripts/session_008_acceptance.py --profile <hermes-profile>     # real mixed-engine acceptance
python scripts/session_008_config_roundtrip.py                          # config export/import round trip
```

## Known limitations (v1.0)

- Live mixed-engine acceptance **PASSED** (2026-09-27): real Codex planning +
  final audit, 2 real Hermes builds (distinct sessions), ONE auditor session,
  durable FINAL PASS + pending plan, zero-AI next-batch handoff, and a live
  STOP-at-boundary proof through the continuous machinery
  (`scripts/session_010_acceptance.py`, evidence preserved).
- No mid-prompt cancellation (boundary-only pause/stop; a running prompt
  finishes and persists first).
- Read-only planning/audit guards are vacuous on non-git workspaces.
- The packaged app ships the Codex/Hermes/Generic CLI drivers only; dedicated
  Claude Code / OpenCode / Ollama / Kimi adapters do not exist (the Generic
  CLI covers simple third-party CLIs).
- Config export/import is a core API surface, not a UI dialog.
- Restart mid-batch rebinds the shared Task Auditor session from SQLite; if
  the provider lost the session, the run fails explicitly instead of
  silently starting a new one — and the one-shot Coder recovery override is
  the operator's tool for the Builder side.

---

## Documentation

| File | Purpose |
|---|---|
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | **Operator guide** — Simple Mode first, then Advanced |
| [docs/CURRENT_STATE.md](docs/CURRENT_STATE.md) | Canonical handoff file (read first in every dev session) |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | The actual architecture, including deliberate non-goals |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Architecture decisions with reasons |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Phased plan |
| [docs/reports/](docs/reports/) | Per-session reports with evidence |

---

## License

Proprietary — ENCOMM.
