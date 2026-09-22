# Architecture — ENCOMM Pipeline Control Center

**Version:** 0.1 (foundation)
**Status:** accurate as of Session 001. This document describes what the code
actually does today, including what it deliberately does *not* do.

---

## 1. What this application is

A local Windows desktop application that will supervise AI coding pipelines.
The eventual pipeline runs batches of tasks where an **orchestrator** plans,
a **builder** implements, a **task auditor** checks each task, a fix loop runs
when needed, and a **final auditor** audits the completed batch.

**v0.1 delivers the architecture and the shell, not the executor.** The
application launches, persists real state, resolves driver capabilities, and
enforces session policy rules — but it does not dispatch work to any engine.

### Explicit non-goals for v0.1

- No task execution, no agent dispatch, no prompt routing.
- No real Codex / Hermes / Claude Code / OpenCode / Ollama / Kimi integration.
- No web server, Electron, browser frontend, Docker or cloud backend.
- No scheduled tasks, services or system-wide installs.

---

## 2. Layer model

```
┌──────────────────────────────────────────────────────────────────┐
│  UI  (PySide6)                     src/encomm_pcc/ui/            │
│  MainWindow · WorkspacePanel · RolePanel · BatchPanel · LogPanel │
│  Reads/writes only through PipelineController.                   │
└───────────────────────────┬──────────────────────────────────────┘
                            │  plain method calls + LogRecord callback
┌───────────────────────────▼──────────────────────────────────────┐
│  CORE                              src/encomm_pcc/core/          │
│  PipelineController  — state, transitions, control surface       │
│  SessionManager      — session policy enforcement + bookkeeping  │
│  EventLog            — append-only local event log               │
│  AppPaths / config   — paths and safe placeholder values         │
│  Qt-free.                                                        │
└──────────┬──────────────────────────────────┬────────────────────┘
           │                                  │
┌──────────▼───────────────────┐   ┌──────────▼────────────────────┐
│  DOMAIN                      │   │  DRIVERS                       │
│  src/encomm_pcc/domain/      │   │  src/encomm_pcc/drivers/       │
│  Enums · dataclasses ·       │   │  BaseDriver ABC · registry ·   │
│  StateMachine                │   │  Codex/Hermes/GenericCli stubs │
│  Pure, no I/O, no Qt.        │   │  ProcessSpec / ProcessRunner   │
└──────────────────────────────┘   └──────────┬────────────────────┘
                                              │
┌─────────────────────────────────────────────▼────────────────────┐
│  PERSISTENCE                     src/encomm_pcc/persistence/     │
│  Database (sqlite3 stdlib) · schema.sql                          │
└──────────────────────────────────────────────────────────────────┘
```

**Dependency rule:** `ui → core → {domain, drivers, persistence}`.
`domain` depends on nothing. `drivers` and `persistence` depend only on
`domain`. No layer may import upward. `tests/test_imports.py` asserts that
`domain`, `persistence` and `core` import in a process where `PySide6` is
never loaded — the UI is a consumer of the core, never a dependency of it.

---

## 3. Repository layout

```
ENCOMM PIPELINE CONTROL CENTER/
├── main.py                       Entry point (adds src/ to sys.path, runs app)
├── pyproject.toml                Metadata, packaging, pytest config
├── requirements.txt              Runtime deps: PySide6 only
├── requirements-dev.txt          + pytest
├── src/encomm_pcc/
│   ├── __init__.py               __version__ = "0.1.0"
│   ├── app.py                    Bootstrap: Database + EventLog + Controller + window
│   ├── domain/
│   │   ├── enums.py              AgentRole, SessionPolicy, PipelinePhase,
│   │   │                         TaskState, BatchStatus, EventLevel
│   │   ├── models.py             WorkspaceConfig, AgentRoleConfig,
│   │   │                         TaskStateRecord, BatchState, PipelineState
│   │   └── state_machine.py      TRANSITIONS graph, StateMachine, validator
│   ├── drivers/
│   │   ├── base.py               BaseDriver ABC, DriverCapabilities,
│   │   │                         SessionRequest, DriverSession, PromptHandle,
│   │   │                         PromptResult, DriverNotImplementedError
│   │   ├── process.py            ProcessSpec, ProcessResult, ProcessRunner,
│   │   │                         SubprocessRunner, NullProcessRunner
│   │   ├── codex.py              CodexDriver      (placeholder)
│   │   ├── hermes.py             HermesDriver     (placeholder)
│   │   ├── generic_cli.py        GenericCliDriver (placeholder)
│   │   └── registry.py           DriverRegistry, IMPLEMENTED_DRIVERS,
│   │                             PLANNED_DRIVERS
│   ├── core/
│   │   ├── config.py             AppPaths, batch-size bounds, placeholders
│   │   ├── events.py             EventLog, LogRecord, NullEventLog
│   │   ├── session_manager.py    decide_session_action, SessionManager
│   │   └── controller.py         PipelineController, ControlResult
│   ├── persistence/
│   │   ├── schema.sql            Schema v1 (7 tables)
│   │   └── database.py           Database — explicit data-access layer
│   └── ui/
│       ├── main_window.py        MainWindow
│       └── panels.py             WorkspacePanel, RolePanel, BatchPanel, LogPanel
├── tests/                        144 tests, 8 files
└── docs/                         This file + CURRENT_STATE, ROADMAP, DECISIONS
    └── reports/                  Per-session reports
```

---

## 4. Role model

Four roles, defined by `AgentRole`, are **independent of engines**:

| Role | Fills | Default session policy | UI fields |
|---|---|---|---|
| `ORCHESTRATOR` | plans the batch | `persistent_optional` | engine, project/profile, session, New Session |
| `BUILDER` | implements and fixes | `always_new` | engine, Hermes profile, provider, model |
| `TASK_AUDITOR` | audits each task | `persistent_per_batch` | engine, Hermes profile, provider, model |
| `FINAL_AUDITOR` | audits the batch | `configurable` | engine, project/profile, session, Same as Orchestrator |

An `AgentRoleConfig` carries `engine` (a driver id resolved through the
registry), `project_profile`, `provider`, `model`, `session_policy`,
`session_id` and `same_as_orchestrator`. Nothing in the role path mentions a
specific engine by name — swapping engines is configuration only.

`PipelineState.resolved_engine_for(FINAL_AUDITOR)` returns the orchestrator's
engine when `same_as_orchestrator` is set.

**Why this matters:** the brief forbids hardcoding Codex. The test
`test_driver_classes_are_not_hardcoded_to_codex` and the registry design
enforce that a new engine is one class plus one `register()` call.

---

## 5. Session policies

`SessionPolicy` has five values; the brief's four rules map onto them:

| Policy | Meaning |
|---|---|
| `always_new` | A brand-new session per unit of work (implement *and* fix) |
| `persistent_per_batch` | One session per batch; a new batch starts a new session |
| `persistent` | One session reused for the whole run |
| `persistent_optional` | Prefer continuity, but never required |
| `configurable` | Operator decides at the UI level |

`decide_session_action()` in `core/session_manager.py` is the single place this
logic lives. It returns `SessionAction.NEW` / `REUSE` / `NONE` plus a reason
string. Two invariants are encoded there:

1. **Capability first.** A driver with `supports_sessions=False` always gets
   `NONE` — the decision is driven by `DriverCapabilities`, never assumed.
2. **Sessions are never the source of truth.** `SessionManager` mirrors every
   session id into the `sessions` table. Durable state lives in SQLite and
   workspace files; a lost session costs performance, never correctness.

`SessionManager.begin_new_batch()` increments a generation counter, which makes
`persistent_per_batch` sessions from the previous batch ineligible for reuse
without deleting their history.

---

## 6. Driver abstraction

`BaseDriver` (ABC) defines the contract:

```
start_session(request) -> DriverSession
resume_session(session_id, request) -> DriverSession
send_prompt(session, prompt) -> PromptHandle
wait_for_completion(handle, timeout_s) -> PromptResult
get_session_id() -> str | None
get_result() -> PromptResult | None
close_session(session) · cancel() · capabilities() · probe_availability()
```

Design points:

- **`DriverCapabilities` is the contract for optional features**:
  `supports_sessions`, `supports_resume`, `supports_streaming`,
  `supports_cancellation`, `supports_model_selection`, `implemented`.
  Callers branch on capabilities instead of assuming a provider's behaviour.
- **Session ids are optional.** `get_session_id()` returns `None` for stateless
  engines. `GenericCliDriver` is deliberately modelled with
  `supports_sessions=False` so the session-less path is exercised by tests.
- **Placeholders never fake success.** All three v0.1 drivers advertise
  `implemented=False` and raise `DriverNotImplementedError` from
  `start_session`, `resume_session`, `send_prompt` and `wait_for_completion`.
  There is no code path that returns a fabricated `PromptResult`.
- **`probe_availability()` only looks for a binary on `PATH`** via
  `shutil.which`; it never launches anything.

### Process abstraction

Drivers never touch `subprocess` directly. They build a `ProcessSpec` (argv,
cwd, env, timeout, `no_window`) and hand it to a `ProcessRunner`:

- `SubprocessRunner` — the real implementation, ready for the executor phase.
  Not used by any v0.1 driver.
- `NullProcessRunner` — **the default for every v0.1 driver.** It records the
  attempted spec and raises. A coding mistake therefore cannot start a real
  agent session during the foundation phase.

### Registry

`DriverRegistry` maps `driver_id → driver class`, provides `create()`,
`capabilities()`, `describe_all()` and `display_name()`. `PLANNED_DRIVERS`
lists `claude_code`, `opencode`, `ollama`, `kimi` as documentation only — they
are not registered and have no code.

---

## 7. Persistence

SQLite via the Python stdlib (`sqlite3`), schema v1, seven tables:

| Table | Holds |
|---|---|
| `schema_meta` | `schema_version` (currently `1`) |
| `workspaces` | workspace id, name, repository path, timestamps |
| `role_configs` | one row per (workspace, role): engine, profile, provider, model, session policy, session id, `same_as_orchestrator`, `extra_json` |
| `sessions` | session id, workspace, role, driver, external session id, persistent/external flags, closed_at |
| `batches` | batch id, workspace, requested size, status |
| `tasks` | task id, batch, index, title, state, attempts, audit rounds, last error |
| `app_events` | timestamped event log (level, source, message, payload) |

Implementation notes:

- **One connection, one `threading.RLock`.** Single-user desktop app; a shared
  serialised connection is simpler and safer than a pool. Writes go through
  `Database.transaction()`, which commits or rolls back atomically.
- `PRAGMA foreign_keys = ON` — the `sessions → workspaces` foreign key is real
  and intentional: a session cannot outlive its workspace record.
- `PRAGMA journal_mode = WAL` for the on-disk database.
- **No migration framework.** `SCHEMA_VERSION` is recorded and a database
  written by a *newer* schema is refused rather than silently mis-read. See
  D-008 in `DECISIONS.md`.

Application state lives at `%LOCALAPPDATA%\ENCOMM Pipeline Control Center\`
(`pipeline_control_center.db`), overridable with `ENCOMM_PCC_DATA_DIR` (used by
tests and probes).

---

## 8. Pipeline phase state machine

`domain/state_machine.py` holds the transition graph declaratively:

```
IDLE ──▶ PLANNING_BATCH ──▶ RUNNING_TASK ──▶ AUDITING_TASK ─┬─▶ RUNNING_TASK (next task)
                                                             ├─▶ FIX_REQUIRED ──▶ RUNNING_FIX ──▶ AUDITING_TASK
                                                             └─▶ READY_FOR_FINAL_AUDIT ──▶ FINAL_AUDIT_RUNNING
                                                                                              ├─▶ BATCH_COMPLETE
                                                                                              └─▶ FIX_REQUIRED
BATCH_COMPLETE ──▶ IDLE | PLANNING_BATCH
```

- `PAUSED` may be entered from any *active* phase and may resume into any active
  phase; `StateMachine.resume_target()` returns the phase recorded at pause time.
- `BLOCKED` may recover to `IDLE`, `PLANNING_BATCH`, `PAUSED` or `FAILED`.
- `FAILED → IDLE` only. `BATCH_COMPLETE` and `FAILED` are terminal.
- `transition_to()` raises `InvalidTransitionError` on an illegal edge and
  leaves the phase unchanged.
- `StateMachine.reset()` is the operator escape hatch back to `IDLE`.

Tests assert that every phase is reachable from `IDLE` and that every target in
the graph is a real phase.

---

## 9. Control surface (what Start/Pause/Stop actually do)

`PipelineController` owns state and does **not** execute tasks. Every control
call returns a `ControlResult` with an explicit `executor_started` field, which
is **always `False` in v0.1**.

| Call | Effect |
|---|---|
| `request_start(size)` | `IDLE → PLANNING_BATCH`, creates a `BatchState` with the clamped size, advances the session batch generation, logs a warning that the executor is absent. `executor_started=False` |
| `request_pause()` | Current active phase → `PAUSED`, batch status → `PAUSED` |
| `request_resume()` | `PAUSED → ` recorded resume target, batch status → `RUNNING` |
| `request_stop()` | → `IDLE` (forced `reset()` when `IDLE` is not a normal successor), batch status → `STOPPED` |
| `set_batch_size(n)` | Clamped to `[1, 50]`, default `5` |
| `set_role_config(role, **fields)` | Whitelisted fields only; unknown keys raise `ValueError` |
| `set_workspace(name, path)` | Updates and persists the workspace |
| `probe_session_decision(role)` | Read-only: what the session policy *would* do |

Batch statuses are separate from phases: `CREATED`, `RUNNING`, `PAUSED`,
`COMPLETE`, `FAILED`, `STOPPED`.

The controller writes `EXECUTOR_NOT_IMPLEMENTED` to the event log on every
start, so the log itself is evidence that nothing was dispatched.

---

## 10. UI structure

`MainWindow` (title `ENCOMM Pipeline Control Center — v0.1 foundation`):

1. **WORKSPACE** — workspace name, repository path, path-exists indicator.
2. **ROLES** — a 2×2 grid of `RolePanel`s: ORCHESTRATOR, FINAL AUDITOR,
   TASK AUDITOR, BUILDER. Each panel shows only the fields the brief specifies
   for that role (`_ROLE_FIELDS`), plus a live capability hint line
   ("placeholder — not implemented; sessions supported; binary found on PATH").
3. **BATCH** — size spin box (1–50, default 5), current phase, current status,
   Start / Pause / Resume / Stop buttons enabled from the state machine, and an
   explicit hint that the executor is not implemented.
4. **LOG PANEL** — timestamped event log (`QPlainTextEdit`, 5000-line cap,
   monospace, Clear button, event counter), fed by `EventLog.subscribe`.

Configuration is written back to the controller on focus-out
(`editingFinished`), not on every keystroke.

`RolePanel` is generic over role and driven by `_ROLE_FIELDS` — adding a field
to a role is a dict edit, not new widget code.

---

## 11. Threading model

v0.1 is single-threaded: the Qt event loop drives everything, and the database
connection is serialised by an `RLock` so a future worker thread can share it
safely. `LogPanel` subscribes with a `Qt.QueuedConnection`, so an `EventLog`
emission from any thread is marshalled onto the UI thread.

---

## 12. What is deliberately NOT implemented

Stated plainly so no future session mistakes a placeholder for a feature:

- **No executor.** Nothing transitions past `PLANNING_BATCH` on its own.
- **No driver does anything.** `CodexDriver`, `HermesDriver`, `GenericCliDriver`
  raise `DriverNotImplementedError` for every real operation.
- **No prompt text, no task parsing, no plan generation.**
- **No "New Session" behaviour.** The button logs a placeholder event and
  creates no session.
- **No fix loop, no audit loop, no final batch audit.**
- **No multi-task dispatch.** `BatchState.tasks` stays empty; nothing
  materialises task records.
- **No CLI-level integration tests**, because there is no CLI to drive yet.

---

## 13. Extension points

| To add… | Do this |
|---|---|
| A new engine | Subclass `BaseDriver`, declare `driver_id` / `display_name` / `executables`, implement `capabilities()` and the lifecycle methods, add it to `IMPLEMENTED_DRIVERS`. No role, UI or executor change required. |
| A new role field | Add the field to `AgentRoleConfig`, add its key to `PipelineController.set_role_config`'s whitelist, and add the widget name to `_ROLE_FIELDS[role]`. |
| A new phase | Add it to `PipelinePhase`, then to `TRANSITIONS` and (if pausable) `ACTIVE_PIPELINE_PHASES`. `tests/test_state_machine.py` verifies reachability automatically. |
| A new session policy | Add it to `SessionPolicy` and handle it in `decide_session_action()`. |
| The executor | Implement dispatch against `PipelineController.state`, `DriverRegistry` and `SessionManager`; flip `ControlResult.executor_started` to reflect reality. |
| A schema change | Bump `SCHEMA_VERSION` in `persistence/database.py` and add the DDL to `schema.sql`. |

---

## 14. Verification status (v0.1)

| Check | Result |
|---|---|
| `python -m pytest` | 144 passed, 0 failed |
| `python main.py` (offscreen) | Launches; Qt event loop stays alive; on-disk SQLite created with all 7 tables; 10 events persisted |
| Domain/persistence/core import without Qt | Asserted by test |
| Every source file compiles | Asserted by test |
| Drivers refuse real work | Asserted by test for all three adapters |
| Repository contains no secrets | Scanned before commit — see `reports/SESSION_001_FOUNDATION.md` |
