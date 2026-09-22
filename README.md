# ENCOMM Pipeline Control Center

A local Windows desktop application that will supervise AI coding pipelines:
orchestrator agents, builders, task auditors and final auditors, with
configurable session policies, multi-task batches, automatic audit/fix loops
and final batch audits.

**Current version: 0.1.0 — foundation.** The architecture and the desktop shell
exist and are tested. The task executor and all engine integrations are
deliberate placeholders that refuse to do real work. See
[docs/CURRENT_STATE.md](docs/CURRENT_STATE.md) §5 for the precise list of what
is and is not implemented.

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

144 tests covering imports/compile, the domain model, the phase state machine,
persistence round-trips, driver refusal behaviour, session policy resolution,
the controller control surface, and the UI in Qt offscreen mode.

---

## What the UI gives you

| Section | Contents |
|---|---|
| **WORKSPACE** | Workspace name, repository path, path-exists indicator |
| **ORCHESTRATOR** | Engine, project/profile, session, New Session button (placeholder) |
| **FINAL AUDITOR** | Engine, project/profile, session, "Same as Orchestrator" |
| **TASK AUDITOR** | Engine, Hermes profile, provider, model, session policy "Persistent per batch" |
| **BUILDER** | Engine, Hermes profile, provider, model, session policy "Always new" |
| **BATCH** | Batch size (default 5), current phase, current status, Start / Pause / Resume / Stop |
| **LOG PANEL** | Timestamped local event log |

Start / Pause / Resume / Stop drive the **state machine and batch records**.
They do not dispatch work: every start logs an explicit
`Executor is not implemented in the v0.1 foundation` event, and
`ControlResult.executor_started` is always `False`.

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
| [docs/DECISIONS.md](docs/DECISIONS.md) | Architecture decisions D-001…D-013 with reasons |
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
