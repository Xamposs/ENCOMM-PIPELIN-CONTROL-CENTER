# Architecture — ENCOMM Pipeline Control Center

**Version:** 0.2 (real Hermes executor path)
**Status:** accurate as of Session 002. This document describes what the code
actually does today, including what it deliberately does *not* do.

---

## 1. What this application is

A local Windows desktop application that will supervise AI coding pipelines.
The eventual pipeline runs batches of tasks where an **orchestrator** plans,
a **builder** implements, a **task auditor** checks each task, a fix loop runs
when needed, and a **final auditor** audits the completed batch.

**v0.1 delivered the architecture and the shell; v0.2 adds the first real
execution path.** The application launches, persists real state, resolves driver
capabilities, enforces session policy rules, and can now dispatch **one**
controlled task through a deterministic executor to a **real Hermes session** —
capturing the real exit code, the real session id and the real output, and
persisting all of it.

### Explicit non-goals for v0.2

- No task auditor, no fix loop, no final batch audit.
- No multi-task batches, no orchestrator planning, no batch-size dispatch.
- No real Codex / Claude Code / OpenCode / Ollama / Kimi integration. Only Hermes
  is real; the other adapters remain refusing placeholders.
- No web server, Electron, browser frontend, Docker or cloud backend.
- No scheduled tasks, services, installer or system-wide installs.

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
│  Executor            — deterministic one-task dispatch            │
│  SessionManager      — session policy enforcement + bookkeeping  │
│  Hermes profiles     — read-only discovery (names only)          │
│  EventLog            — append-only local event log               │
│  AppPaths / config   — paths and safe placeholder values         │
│  Qt-free.                                                        │
└──────────┬──────────────────────────────────┬────────────────────┘
           │                                  │
┌──────────▼───────────────────┐   ┌──────────▼────────────────────┐
│  DOMAIN                      │   │  DRIVERS                       │
│  src/encomm_pcc/domain/      │   │  src/encomm_pcc/drivers/       │
│  Enums · dataclasses ·       │   │  BaseDriver ABC · registry ·   │
│  StateMachine                │   │  HermesDriver (real) · Codex / │
│  Pure, no I/O, no Qt.        │   │  GenericCli stubs · ProcessSpec│
│                              │   │  Hermes CLI contract · runners │
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
- **Placeholders never fake success.** `CodexDriver` and `GenericCliDriver`
  advertise `implemented=False` and raise `DriverNotImplementedError` from
  `start_session`, `resume_session`, `send_prompt` and `wait_for_completion`.
  There is no code path that returns a fabricated `PromptResult`.
- **`probe_availability()` only looks for a binary on `PATH`** via
  `shutil.which`; it never launches anything.

### Hermes — the first real driver (Session 002)

`HermesDriver` (`drivers/hermes.py`) drives the **installed** Hermes CLI through
the process layer. Its full contract lives in `drivers/hermes_cli.py` so it is
testable without a network:

```
hermes -p <profile> chat --query-file <file> --oneshot --quiet
       --format stream-json --source tool [--in <workspace>]
       [--resume <session_id>] [-m <model>] [--provider <provider>]
```

- **One process per prompt.** `chat --oneshot` answers a single query and exits,
  so the exit code is always the child's own. The prompt travels in a private
  temp file, never in argv.
- **Structured output.** `--format stream-json` emits one JSON object per stdout
  line (`system/init` with the model and session id, `text` deltas,
  `tool_use`/`tool_result`, then a terminal `result` carrying `session_id`,
  `exit_code`, `text` and token counts). A missing terminal record can never be
  success.
- **Session ids are reported, never invented.** A real id appears only after a
  prompt has run, is stored with `external=1`, and stays `None` when the CLI
  exposes none.
- **Capability flags are evidence-gated.** `implemented` and `supports_resume`
  mirror `_LIVE_SMOKE_VERIFIED` / `_LIVE_RESUME_VERIFIED`, which are flipped only
  by a real run. `supports_streaming` and `supports_cancellation` are `False`
  because the adapter surfaces neither.
- **A filtered child environment.** The supervisor's own `HERMES_*` state and
  `PYTHONPATH` are stripped before a launch, so a supervised agent never inherits
  the supervisor's session scope (D-015).
- **Profile discovery is read-only** (`core/hermes_profiles.py`): `hermes profile
  list` is parsed, with an identity-marker directory scan as a fallback. It is
  used as a guard — a profile discovery positively contradicts blocks the
  dispatch before anything starts.

### Process abstraction

Drivers never touch `subprocess` directly. They build a `ProcessSpec` (argv,
cwd, env, timeout, `no_window`) and hand it to a `ProcessRunner`:

- `SubprocessRunner` — the real implementation. `Popen`-based, so a **timeout is
  data**: `ProcessResult.timed_out` is set and the child's whole tree is killed
  (`taskkill /F /T` on Windows, process-group kill elsewhere) instead of being
  waited out. Nothing is detached or orphaned.
- `NullProcessRunner` — **the default for every driver.** It records the
  attempted spec and raises. Wiring the real runner happens in exactly one place
  (`app.attach_default_executor`), so tests and hand-built controllers cannot
  launch anything.

The executor hands the driver a *recording* wrapper around the injected runner;
that wrapper is what `ExecutionReport.executor_started` is derived from.

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

### Session 002 dispatch sequence (one task, stops at `AUDITING_TASK`)

`Executor.dispatch_single_task()` implements exactly this order, and each step is
a persist boundary:

1. **Phase gate** — read-only. Only `IDLE` or `PLANNING_BATCH` may dispatch;
   anything else is rejected with no state change.
2. **Preflight** — read-only. Workspace path set and existing, engine configured
   and registered, `implemented=True`, profile present (and not contradicted by
   discovery), driver accepts the session request. A blocked preflight leaves the
   pipeline exactly where it was and starts no process.
3. **Batch** — created from `IDLE` (batch size 1) or reused from `PLANNING_BATCH`.
4. **Materialise + `RUNNING_TASK`** — the task is persisted, `attempts`
   incremented, the batch set to `RUNNING`.
5. **Session policy** — `SessionManager.decide()` chooses NEW / REUSE / NONE from
   the role's policy and the driver's capabilities.
6. **Prompt** — `send_prompt()` then `wait_for_completion()`: a real process with
   the real exit code.
7. **Result** — success moves the task to `AUDITING` and the pipeline to
   `AUDITING_TASK` (which here only means "ready for the future Task Auditor");
   failure marks the task `FAILED`, records `last_error`, sets the batch to
   `FAILED` and moves the pipeline to `FAILED`. Nothing continues silently.

---

## 9. Control surface (what Start/Pause/Stop actually do)

`PipelineController` owns state. Start/Pause/Resume/Stop move **state**, and
since Session 002 an attached executor owns **dispatch**. Every control call still
returns a `ControlResult` with an explicit `executor_started` field.

| Call | Effect |
|---|---|
| `request_start(size)` | `IDLE → PLANNING_BATCH`, creates a `BatchState` with the clamped size, advances the session batch generation. With **no** executor attached it still logs `EXECUTOR_NOT_IMPLEMENTED`; with one attached it logs that dispatch is owned by the executor. `executor_started=False` either way — this call starts no process. |
| `request_pause()` | Current active phase → `PAUSED`, batch status → `PAUSED`. While a dispatch is running the UI instead asks the executor, which records a boundary pause request. |
| `request_resume()` | `PAUSED → ` recorded resume target, batch status → `RUNNING`; also clears the executor's control flags |
| `request_stop()` | → `IDLE` (forced `reset()` when `IDLE` is not a normal successor), batch status → `STOPPED`. While a dispatch is running the UI asks the executor instead, and the run finishes first. |
| `set_batch_size(n)` | Clamped to `[1, 50]`, default `5` |
| `set_role_config(role, **fields)` | Whitelisted fields only; unknown keys raise `ValueError` |
| `set_workspace(name, path)` | Updates and persists the workspace |
| `attach_executor(executor)` | Registers/clears the dispatcher (adds `controller.executor`) |
| `transition(phase, message=…)` | The executor's only way to move the pipeline: legal edges only, then persist |
| `persist()` | Public seam so the executor can persist a task mutation |
| `probe_session_decision(role)` | Read-only: what the session policy *would* do |

Batch statuses are separate from phases: `CREATED`, `RUNNING`, `PAUSED`,
`COMPLETE`, `FAILED`, `STOPPED`.

`executor_started` is the machine-checkable "did a process really start?" flag:

* `ControlResult.executor_started` is always `False` — a control call never
  launches anything.
* `ExecutionReport.executor_started` becomes `True` **only** when the executor's
  launch recorder saw a real `ProcessSpec`. A blocked preflight, an illegal phase
  and a stop-before-dispatch are all `False`.

Stop and pause are **boundary-only**: a stop requested before dispatch prevents
the process entirely; a stop requested during a prompt lets the run finish, records
its real result, and reports `stop_requested=True`. Mid-prompt cancellation is not
implemented and not advertised (D-018).

---

## 10. UI structure

`MainWindow` (title `ENCOMM Pipeline Control Center — v0.2`):

1. **WORKSPACE** — workspace name, repository path, path-exists indicator.
2. **ROLES** — a 2×2 grid of `RolePanel`s: ORCHESTRATOR, FINAL AUDITOR,
   TASK AUDITOR, BUILDER. Each panel shows only the fields the brief specifies
   for that role (`_ROLE_FIELDS`), plus a live capability hint line
   ("implemented; sessions supported; binary found on PATH"). Profile fields get
   completion from the discovered Hermes profiles.
3. **BATCH** — size spin box (1–50, default 5), current phase, current status,
   Start / Pause / Resume / Stop buttons enabled from the state machine, and a
   hint that reflects whether an executor is attached.
4. **TASK** — the Session 002 dispatch control: Hermes driver availability and the
   resolved CLI path, discovered profiles and the selected Builder profile, task
   title and prompt, **Dispatch task**, current Task 1 state, status, failure text
   and the real result (outcome, exit code, session id, duration, argv, output
   excerpt). The button is disabled unless an executor is attached and the phase
   allows a dispatch.
5. **LOG PANEL** — timestamped event log (`QPlainTextEdit`, 5000-line cap,
   monospace, Clear button, event counter), fed by `EventLog.subscribe`.

Configuration is written back to the controller on focus-out
(`editingFinished`), not on every keystroke.

`RolePanel` is generic over role and driven by `_ROLE_FIELDS` — adding a field
to a role is a dict edit, not new widget code.

The TASK panel shows **only what the executor reported**: nothing is displayed
as a result that did not come back on an `ExecutionReport`.

---

## 11. Threading model

The Qt event loop drives the UI, and **no AI process ever runs on it**.

- **Dispatch runs on a worker thread.** `ui/worker.py` provides
  `ExecutorWorker` (a `QObject` with a `finished(object)` signal) and
  `start_executor_worker()`, which moves it onto a `QThread`. The window keeps the
  thread and worker references, connects `finished` to its handler (queued, so the
  report arrives on the UI thread) and connects `thread.finished` to a cleanup.
  `closeEvent` waits briefly (bounded) for a running worker instead of tearing the
  thread down.
- **State changes stay coordinated.** Only the executor runs off-thread, and it
  mutates state exclusively through `PipelineController.transition()` /
  `persist()`. SQLite is a single connection guarded by an `RLock` (D-009), so the
  worker and the UI can share it safely.
- **The log panel is thread-safe.** `LogPanel.record_received` is connected with
  `Qt.QueuedConnection`, so an `EventLog` emission from any thread is marshalled
  onto the UI thread.
- **The unit suite proves it.** `test_ui_dispatch.py` runs a dispatch with an
  artificially slow driver while a `QTimer` on the UI thread counts ticks (≥3
  fired), and asserts the driver ran on a different thread than the main one.

---

## 12. What is deliberately NOT implemented

Stated plainly so no future session mistakes a placeholder for a feature:

- **No task auditor, no fix loop, no final batch audit.** Execution stops at
  `AUDITING_TASK`, which only means "ready for the future Task Auditor".
- **`CodexDriver` and `GenericCliDriver` do nothing.** Both raise
  `DriverNotImplementedError` for every real operation and report
  `implemented=False`. `PLANNED_DRIVERS` (`claude_code`, `opencode`, `ollama`,
  `kimi`) still has no code.
- **No multi-task batches, no orchestrator, no planning.** One task per batch, and
  `BatchState.tasks` only ever holds manually supplied tasks.
- **No batch-size dispatch.** The size is stored and displayed, never acted on.
- **No mid-prompt cancellation** (`supports_cancellation=False`) and no streaming
  surface (`supports_streaming=False`).
- **No task parsing or plan generation.** No prompt text is generated anywhere.
- **No full-access "New Session" behaviour.** The button logs a placeholder event
  and creates nothing.
- **No output artefact store.** Engine output is kept as a bounded excerpt in the
  event payload and in memory on the `PromptResult`.
- **No packaging**, installer or frozen `.exe`.

---

## 13. Extension points

| To add… | Do this |
|---|---|
| A new engine | Subclass `BaseDriver`, declare `driver_id` / `display_name` / `executables`, implement `capabilities()` and the lifecycle methods, add it to `IMPLEMENTED_DRIVERS`. No role, UI or executor change required. Gate any capability flag on real evidence (D-018). |
| A new role field | Add the field to `AgentRoleConfig`, add its key to `PipelineController.set_role_config`'s whitelist, and add the widget name to `_ROLE_FIELDS[role]`. |
| A new phase | Add it to `PipelinePhase`, then to `TRANSITIONS` and (if pausable) `ACTIVE_PIPELINE_PHASES`. `tests/test_state_machine.py` verifies reachability automatically. |
| A new session policy | Add it to `SessionPolicy` and handle it in `decide_session_action()`. |
| The auditor / fix loop | Extend `Executor` (a second dispatch for `TASK_AUDITOR`, then the `AUDITING_TASK → FIX_REQUIRED → RUNNING_FIX → AUDITING_TASK` loop) with a hard round cap from the first commit; move state only through `controller.transition()`. |
| A schema change | Bump `SCHEMA_VERSION` in `persistence/database.py`, add the DDL to `schema.sql`, **and add the exact `ALTER` step to `Database._upgrade()`** with a test (D-016). |
| A new UI surface | Add a panel under `ui/panels.py` that reads the controller (and, for dispatch, the executor) — never a driver directly. Long work goes through `start_executor_worker`. |

---

## 14. Verification status (v0.2)

| Check | Result |
|---|---|
| `python -m pytest` | **221 passed, 0 failed** (13 files) |
| Real end-to-end run (`scripts/session_002_smoke.py`) | **SMOKE PASSED** — real session `20260922_172437_722edb`, process exit code `0`, output exactly `ENCOMM_PCC_HERMES_SMOKE_OK`, 12.7 s |
| Resume proof | **PROVEN** — `--resume` continued that same session, exit code 0 |
| Model-override proof | **PROVEN** — a run with `-m deepseek/deepseek-v4.1-flash --provider openrouter` exited 0 and the CLI reported that model |
| Persisted result read back from SQLite | Task `AUDITING` (attempts 1, prompt stored), session row `external=1`, result event payload with real token counts |
| Domain/persistence/core import without Qt | Asserted by test |
| Every source file compiles | Asserted by test |
| Codex/GenericCli still refuse real work | Asserted by test |
| A failed child can never be a green task | Asserted by test at both layers (exit codes 2 and 3) |
| UI stays responsive during a dispatch | Asserted by offscreen test (event loop ticks while the worker sleeps) |
| Repository contains no secrets | Pattern-scanned before commit — see `reports/SESSION_002_HERMES_EXECUTOR.md` §SECURITY_CHECK |
| Hermes profiles/config modified | **None** — read-only discovery plus `-p <existing profile>` only |
