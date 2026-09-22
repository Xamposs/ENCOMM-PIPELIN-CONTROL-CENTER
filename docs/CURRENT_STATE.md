# Current State — ENCOMM Pipeline Control Center

**This is the canonical handoff file.** It describes exactly what exists today.

---

## 0. MANDATORY NEW-SESSION HANDOFF CONTRACT

Every new development session — human or agent — must begin by reading, in
order:

1. `docs/CURRENT_STATE.md`   ← this file
2. `docs/ARCHITECTURE.md`
3. `docs/DECISIONS.md`

and must use the **LeanCTX** skill (`encomm-leanctx`) for repository discovery
before exploring or editing code.

**No critical architectural information may exist only in chat history.**
Anything a future session needs must be in this repository. If you learn
something that changes the architecture, write it into `ARCHITECTURE.md` and
append an entry to `DECISIONS.md` before you finish.

**Read the "Known limitations" section before claiming anything works.** Several
components are deliberate placeholders, and `ARCHITECTURE.md` §12 lists them
explicitly.

---

## 1. Version

`0.1.0` — foundation. Session 001.

---

## 2. What currently works

| Capability | Status | Evidence |
|---|---|---|
| Desktop application launches | **Works** | `python main.py` starts, Qt event loop runs, window constructed |
| Main window with all required sections | **Works** | WORKSPACE, ROLES (4 role panels), BATCH, LOG PANEL |
| Role-based architecture (4 independent roles) | **Works** | `AgentRole`, `AgentRoleConfig`, one `RolePanel` per role |
| Engine abstraction (no Codex hardcoding) | **Works** | `BaseDriver` ABC + `DriverRegistry`; engines are config values |
| SQLite persistence | **Works** | Schema v1, 7 tables, on-disk DB created and written on launch |
| Session policy engine | **Works** | `decide_session_action()`; all four brief-mandated rules enforced |
| Pipeline phase state machine | **Works** | Declarative graph + validator; illegal edges rejected |
| Batch control (Start/Pause/Resume/Stop) | **Works at state level** | Phase and `BatchState` change; **no task executes** |
| Event log (UI + database) | **Works** | Timestamped records in the log panel and in `app_events` |
| Automated tests | **Works** | 144 passed, 0 failed, 8 files |
| Driver adapters (Codex/Hermes/GenericCli) | **Placeholder** | All raise `DriverNotImplementedError`; `implemented=False` |
| Task execution / agent dispatch | **Not implemented** | Deliberate — see §5 |

### Verified at the end of Session 001

- `python -m pytest` → `144 passed in 1.33s`
- `python main.py` (offscreen) → process stayed alive in the Qt event loop;
  on-disk database created at the configured data dir with all 7 tables and
  10 persisted events, including one capability row per registered driver.
- Launch probe over the real bootstrap path → 8 UI sections present,
  placeholder engines resolved, `IDLE → PLANNING_BATCH → PAUSED → IDLE`
  transitions performed, 12 events persisted.

---

## 3. Current repository structure

```
ENCOMM PIPELINE CONTROL CENTER/
├── main.py                     Entry point
├── pyproject.toml              Metadata + packaging + pytest config
├── requirements.txt            PySide6 (only runtime dependency)
├── requirements-dev.txt        + pytest
├── .gitignore                  Ignores *.db, logs, .env, secrets
├── README.md
├── src/encomm_pcc/
│   ├── __init__.py             __version__ = "0.1.0"
│   ├── app.py                  run(), build_controller(), restore_state()
│   ├── domain/
│   │   ├── enums.py            AgentRole, SessionPolicy, PipelinePhase,
│   │   │                       TaskState, BatchStatus, EventLevel
│   │   ├── models.py           WorkspaceConfig, AgentRoleConfig,
│   │   │                       TaskStateRecord, BatchState, PipelineState
│   │   └── state_machine.py    TRANSITIONS, StateMachine, InvalidTransitionError
│   ├── drivers/
│   │   ├── base.py             BaseDriver ABC, DriverCapabilities,
│   │   │                       SessionRequest, DriverSession, PromptHandle,
│   │   │                       PromptResult, DriverNotImplementedError
│   │   ├── process.py          ProcessSpec, ProcessRunner, SubprocessRunner,
│   │   │                       NullProcessRunner
│   │   ├── codex.py            CodexDriver      (placeholder)
│   │   ├── hermes.py           HermesDriver     (placeholder)
│   │   ├── generic_cli.py      GenericCliDriver (placeholder, stateless)
│   │   └── registry.py         DriverRegistry, IMPLEMENTED_DRIVERS,
│   │                           PLANNED_DRIVERS
│   ├── core/
│   │   ├── config.py           AppPaths, batch-size bounds, placeholders
│   │   ├── events.py           EventLog, LogRecord, NullEventLog
│   │   ├── session_manager.py  decide_session_action, SessionManager
│   │   └── controller.py       PipelineController, ControlResult
│   ├── persistence/
│   │   ├── schema.sql          Schema v1 — 7 tables
│   │   └── database.py         Database
│   └── ui/
│       ├── main_window.py      MainWindow
│       └── panels.py           WorkspacePanel, RolePanel, BatchPanel, LogPanel
├── tests/                      144 tests across 8 files
└── docs/
    ├── ARCHITECTURE.md         Actual architecture (read second)
    ├── CURRENT_STATE.md        This file (read first)
    ├── ROADMAP.md              Phased plan
    ├── DECISIONS.md            D-001 … D-013 with reasons
    └── reports/
        └── SESSION_001_FOUNDATION.md
```

---

## 4. Key architectural decisions (summary)

Full reasoning in `DECISIONS.md`. The ones a future session must not undo
without a new ADR:

| # | Decision |
|---|---|
| D-001 | PySide6 is the only UI stack; `core`/`domain`/`persistence` stay Qt-free |
| D-002 | SQLite from the stdlib; no ORM, no external DB |
| D-003 | Roles are independent of engines — never hardcode an engine into role logic |
| D-004 | Driver capabilities drive everything; sessions are optional |
| D-005 | Placeholder drivers refuse work rather than simulate it |
| D-006 | `NullProcessRunner` is the default runner; drivers cannot launch processes |
| D-007 | `PipelineController` owns state; the executor is a separate, absent component |
| D-008 | No migration framework yet; a newer schema is refused, not mis-read |
| D-009 | One serialised SQLite connection behind an `RLock` |
| D-010 | `src/` layout + `main.py` shim, no install step |
| D-011 | Placeholders fill only *unconfigured* roles |
| D-012 | Explicit `None` checks, never truthiness, for injected objects |
| D-013 | `QT_QPA_PLATFORM=offscreen` for all automated UI testing |

---

## 5. Known limitations

Stated bluntly so nothing is over-claimed:

1. **No executor.** `request_start()` moves the phase to `PLANNING_BATCH` and
   creates a `BatchState`, then stops. `ControlResult.executor_started` is
   always `False` and `EXECUTOR_NOT_IMPLEMENTED` is written to the event log.
2. **`BatchState.tasks` is always empty.** No task records are materialised;
   the orchestrator does not exist yet.
3. **No driver performs any work.** All three adapters raise
   `DriverNotImplementedError` for `start_session`, `resume_session`,
   `send_prompt` and `wait_for_completion`.
4. **No real sessions.** The "New Session" button logs a placeholder event and
   creates nothing. `SessionManager` has no caller that registers a session in
   the running application (tests exercise it directly).
5. **No fix loop, no audit loop, no final batch audit.**
6. **Batch size is stored, not acted on.** Nothing is planned or dispatched.
7. **UI config is placeholder-driven.** Engines default to `hermes` for every
   role; provider/model defaults are inert strings.
8. **No packaging.** No installer, no `.exe`, no frozen build.
9. **`provider` and `model` are stored but unused** — no code path consumes
   them yet.
10. **`TaskState` and `BatchStatus.COMPLETE/FAILED` are defined but never
    produced** by the running application.
11. **No migration path** for a changed schema — only a version guard.
12. **Single-threaded.** No worker threads; the `RLock` is prepared but unused.
13. **`GenericCliDriver` has no configurable command line yet** — the brief's
    "generic CLI" needs an argv field in role config before it can be real.
14. **`TaskStateRecord.audit_rounds` / `attempts` / `last_error` are persisted
    but never incremented** by application code.

---

## 6. Files most likely relevant next

In the order a Session 002 is likely to touch them:

| File | Why |
|---|---|
| `src/encomm_pcc/core/controller.py` | Where the executor will attach; `dispatch_batch()` belongs near here |
| `src/encomm_pcc/drivers/base.py` | Contract the first real driver must satisfy |
| `src/encomm_pcc/drivers/process.py` | `SubprocessRunner` is the bridge from stub to real driver |
| `src/encomm_pcc/drivers/hermes.py` | Most likely first real integration (profiles + provider/model) |
| `src/encomm_pcc/core/session_manager.py` | Already complete for v0.1; the executor will call `decide()` |
| `src/encomm_pcc/domain/models.py` | `BatchState.tasks` materialisation starts here |
| `src/encomm_pcc/persistence/schema.sql` | Bump `SCHEMA_VERSION` if the schema changes |
| `src/encomm_pcc/ui/panels.py` | Add task-list / progress widgets here |
| `tests/test_controller.py` | Executor tests will extend this file |

---

## 7. Exact next recommended phase

**Session 002 — Executor skeleton + first real driver (`HermesDriver`) behind an
explicit dry-run gate.**

Recommended scope, in order:

1. **Materialise tasks.** Give `BatchState` a way to hold real
   `TaskStateRecord`s produced by a planning step, and persist them (the schema
   already supports this — no migration needed).
2. **Executor skeleton.** Add a `core/executor.py` with `dispatch_batch()`
   driving `IDLE → PLANNING_BATCH → RUNNING_TASK → AUDITING_TASK → …` through
   the existing `StateMachine`, honouring `PAUSED` / `STOPPED` between tasks.
   It must be *pausable at task boundaries*, and must not run on a UI thread.
3. **Wire one driver for real**, most naturally `HermesDriver`, through
   `SubprocessRunner`. Keep the refusal path intact: the driver stays
   `implemented=False` until its integration is genuinely verified, and every
   `PromptResult` must carry a real exit code.
4. **Keep the honesty invariant.** `ControlResult.executor_started` must only
   become `True` when a real process was actually launched. Do not weaken
   `EXECUTOR_NOT_IMPLEMENTED` logging before that is true.
5. **Extend tests** with executor state-transition tests (including pause at a
   task boundary and failure propagation), then update `ARCHITECTURE.md` §9/§12,
   append ADRs, and write `docs/reports/SESSION_002_*.md`.

Do **not** start with multi-task batch execution, fix loops or final batch
audits — those are Sessions 003+ and depend on the executor skeleton being
correct first. See `ROADMAP.md`.
