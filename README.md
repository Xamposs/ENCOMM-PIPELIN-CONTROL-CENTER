# ENCOMM Pipeline Control Center

A local Windows desktop application that will supervise AI coding pipelines:
orchestrator agents, builders, task auditors and final auditors, with
configurable session policies, multi-task batches, automatic audit/fix loops
and final batch audits.

**Current version: 0.4.0 — orchestrated multi-task batches.** The architecture,
the desktop shell, the **first real engine integration**, the **complete
controlled audit/fix loop** (Session 003), and now the **first real
multi-task batch** exist and are tested: one autonomous run plans exactly N
tasks through the real ORCHESTRATOR role (a strict, fails-closed plan parser;
a read-only guard that BLOCKS any plan whose call modified the repository),
then executes every task deterministically — a BRAND-NEW Builder session per
task, a Task Auditor that reuses ONE session for the whole batch, the capped
fix loop inside the batch (NEEDS_FIX → fix in a fresh session → same auditor
re-audits, `MAX_AUDIT_ROUNDS = 3`), boundary-safe pause/resume/stop, and
crash recovery from SQLite. A fully passing batch stops at
**READY_FOR_FINAL_AUDIT** — never at BATCH_COMPLETE, which only the real
Final Auditor (a later session) may produce. The Codex and Generic-CLI
adapters remain deliberate placeholders that refuse to do real work. See
[docs/CURRENT_STATE.md](docs/CURRENT_STATE.md) §5 for the precise list of
what is and is not implemented.

---

## Requirements

- Python 3.11+
- PySide6 6.6+ (the only runtime dependency)
- Windows (developed and verified on Windows 10)

## Run

```bash
pip install -r requirements.txt
python main.py
```

No install step and no build step are required. Application state is written to
`%LOCALAPPDATA%\ENCOMM Pipeline Control Center\pipeline_control_center.db`
(override with the `ENCOMM_PCC_DATA_DIR` environment variable).

## Test

```bash
pip install -r requirements-dev.txt
python -m pytest
```

334 tests covering imports/compile, the domain model (including the audit
verdict contract and the strict batch-plan contract), the phase state machine,
persistence round-trips (schema v4 + the v1→v2→v3→v4 upgrade chain), driver
refusal behaviour, the Hermes CLI contract, the real driver's mapping of a
child process onto a `PromptResult`, the executor's transitions and failure
propagation, the strict verdict and plan parsers' fail-closed behaviour, the
capped audit/fix loop (session isolation, round cap, recovery), the
deterministic multi-task batch runner (session lifecycles, pause/resume/stop,
restart recovery, the read-only planning guard), the read-only repository
fingerprint, profile discovery, session policy resolution, the controller
control surface, the worker-thread dispatch path, and the UI in Qt offscreen
mode.

The suite never touches a network or a real engine. Real end-to-end runs are
separate, explicitly invoked scripts:

```bash
python scripts/session_002_smoke.py --profile <hermes-profile>          # builder path
python scripts/session_003_audit_fix_smoke.py --profile <hermes-profile>  # audit/fix loop
python scripts/session_004_multitask_smoke.py --profile <hermes-profile>  # orchestrated batch
python scripts/session_004_multitask_smoke.py --fake                       # offline post-processing proof
```

---

## What the UI gives you

| Section | Contents |
|---|---|
| **WORKSPACE** | Workspace name, repository path, path-exists indicator |
| **ORCHESTRATOR** | Engine, project/profile, session, New Session button (placeholder) |
| **FINAL AUDITOR** | Engine, project/profile, session, "Same as Orchestrator" |
| **TASK AUDITOR** | Engine, Hermes profile, provider, model, session policy "Persistent per batch" |
| **BUILDER** | Engine, Hermes profile, provider, model, session policy "Always new" |
| **BATCH** | **Project Brief** (persisted), batch size (1–5, default 5), current phase/status, deterministic **next action**, per-task progress, real session readouts, **PLAN + START BATCH**, **RESUME BATCH**, Start / Pause / Resume / Stop |
| **TASK** | Hermes driver availability + discovered profiles, task title/prompt, **Dispatch task**, the deterministic **next action** (AUDIT/FIX/RE-AUDIT/COMPLETE/BLOCKED), **Run Task Auditor** and **Run fix (NEW Builder session)**, audit-loop readouts (verdict, auditor/fix session ids, audit rounds), task state, status, failure and the real result |
| **LOG PANEL** | Timestamped local event log |

Start / Pause / Resume / Stop drive the **state machine and batch records**.
**PLAN + START BATCH** is the autonomous path: one Orchestrator planning call,
then every task runs back-to-back (fresh Builder → Task Auditor → fix loop →
next task) until READY_FOR_FINAL_AUDIT / BLOCKED / FAILED / STOPPED / PAUSED —
no clicks between tasks, all off the UI thread on a worker thread, and the
panel shows only what the executor reported — outcome, real exit code, real
session ids, output excerpt and any failure text.

`executor_started` is the machine-checkable "did a process really start?" flag.
It stays `False` when no executor is attached or when a preflight refuses, and
becomes `True` on an `ExecutionReport` only after a child process was launched.
"Stop" takes effect at the next safe task boundary; mid-prompt cancellation is
**not** implemented and is not advertised as a capability.

---

## Architecture in one paragraph

Four **roles** (`ORCHESTRATOR`, `BUILDER`, `TASK_AUDITOR`, `FINAL_AUDITOR`) are
configured independently of the **engines** that fill them. Engines are
resolved through a `DriverRegistry` behind a `BaseDriver` ABC whose
`DriverCapabilities` describe what an engine can actually do — including whether
it supports sessions at all. `SessionPolicy` is resolved against those
capabilities in one place. Durable truth lives in SQLite, never in a live
session. The pipeline phase state machine is declarative and validated, so the
future executor cannot invent transitions. Drivers build a declarative
`ProcessSpec` instead of touching `subprocess`, and the default runner
physically cannot launch anything.

Full detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Documentation

| File | Purpose |
|---|---|
| [docs/CURRENT_STATE.md](docs/CURRENT_STATE.md) | **Canonical handoff file.** Read first in every new session. |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | The actual architecture, including deliberate non-goals |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Architecture decisions D-001…D-029 with reasons |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Phased plan from foundation to operational hardening |
| [docs/reports/](docs/reports/) | Per-session reports |

### Handoff contract

Every new development session must begin by reading
`docs/CURRENT_STATE.md`, then `docs/ARCHITECTURE.md`, then `docs/DECISIONS.md`,
and must use the **LeanCTX** skill (`encomm-leanctx`) for repository discovery.

No critical architectural information may exist only in chat history.

---

## License

Proprietary — ENCOMM.
