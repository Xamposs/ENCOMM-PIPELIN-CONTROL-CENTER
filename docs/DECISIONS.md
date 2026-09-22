# Architecture Decision Records — ENCOMM Pipeline Control Center

Each entry records a decision, the reason, and the consequence. New decisions
are appended; existing entries are not rewritten (supersede instead, and say so).

---

## D-001 — PySide6 for the desktop UI

**Date:** Session 001
**Status:** Accepted

**Context.** The application is a local Windows desktop tool that will
supervise long-running external processes and display live state.

**Decision.** Use PySide6 (Qt 6) for the UI. No web frontend, no Electron.

**Reason.** The brief mandates it. It is also the right shape for the job:
native process/thread integration, mature widgets, a real event loop that
coexists with `subprocess` supervision, and a single-language stack.

**Consequence.** `PySide6>=6.6,<7` is the only runtime dependency. The UI layer
is the only layer allowed to import Qt, and a test asserts that `domain`,
`persistence` and `core` import in a process where PySide6 is never loaded.

---

## D-002 — SQLite from the Python standard library

**Date:** Session 001
**Status:** Accepted

**Context.** The application needs durable local state: workspaces, role
configuration, session ids, batch/task state, events.

**Decision.** `sqlite3` from the stdlib, wrapped in an explicit `Database`
class. No ORM, no migration framework, no external database.

**Reason.** Zero additional dependencies, transactional, file-based, and more
than sufficient for a single-user desktop app. An ORM would add indirection
without removing work at this scale.

**Consequence.** Schema changes are hand-written DDL in `schema.sql` with a
bumped `SCHEMA_VERSION`. See D-008.

---

## D-003 — Roles are independent of engines

**Date:** Session 001
**Status:** Accepted

**Context.** The brief forbids hardcoding the system to Codex. The same engine
may fill several roles, and engines must be swappable.

**Decision.** Four roles (`ORCHESTRATOR`, `BUILDER`, `TASK_AUDITOR`,
`FINAL_AUDITOR`) are modelled as a `AgentRole` enum plus an `AgentRoleConfig`
that names an engine by `driver_id`. Role logic never references a concrete
engine.

**Reason.** Keeps configuration, capability resolution and future driver work
in one place, and makes "use Hermes as builder, Codex as orchestrator" a pure
configuration change.

**Consequence.** Adding an engine requires no change to role code, UI panels or
the executor. `PipelineState.resolved_engine_for()` handles the one role
special case ("FINAL_AUDITOR same as orchestrator") in a single method.

---

## D-004 — Capability-driven driver contract, sessions optional

**Date:** Session 001
**Status:** Accepted

**Context.** Some engines keep a conversation alive (Codex, Hermes); others are
stateless one-shot CLIs. The brief says not to assume every provider supports
session ids.

**Decision.** `DriverCapabilities` advertises `supports_sessions`,
`supports_resume`, `supports_streaming`, `supports_cancellation`,
`supports_model_selection` and `implemented`. `SessionPolicy` is resolved
against those flags in `decide_session_action()`. `GenericCliDriver` is
deliberately declared stateless so the session-less path is exercised by tests.

**Reason.** Prevents a class of bugs where a caller assumes a session id exists.
Makes the stateless case a first-class path instead of an afterthought.

**Consequence.** Callers must branch on capabilities. `get_session_id()` is
documented to return `None` and a test asserts that.

---

## D-005 — Placeholder drivers must refuse work, never simulate it

**Date:** Session 001
**Status:** Accepted

**Context.** The foundation phase needs `CodexDriver`, `HermesDriver` and
`GenericCliDriver` to exist, but must not start real AI sessions.

**Decision.** Every v0.1 driver advertises `implemented=False` and raises
`DriverNotImplementedError` from `start_session`, `resume_session`,
`send_prompt` and `wait_for_completion`. No code path fabricates a
`PromptResult`.

**Reason.** A stub that returns plausible-looking fake output is worse than a
stub that fails loudly: fake output would flow into audit loops and logs and be
indistinguishable from real agent work. `PromptResult.simulated` exists as an
explicit marker for any future dry-run mode.

**Consequence.** Nothing in v0.1 can accidentally run an agent. Tests assert
the refusal for all three adapters. The UI surfaces "placeholder — not
implemented" next to every engine.

---

## D-006 — `NullProcessRunner` as the default runner

**Date:** Session 001
**Status:** Accepted

**Context.** Drivers must not launch processes in this phase, but the process
abstraction needs to exist now so the executor phase does not invent a second
one.

**Decision.** Drivers build a declarative `ProcessSpec` and hand it to a
`ProcessRunner`. `BaseDriver` defaults to `NullProcessRunner`, which records the
attempted spec and raises `RuntimeError`. `SubprocessRunner` exists, is tested
against harmless commands, and is wired to nothing.

**Reason.** Defence in depth. Even if a driver's guard were removed, the
default runner still cannot execute anything.

**Consequence.** The executor phase changes one constructor argument per
driver. `SubprocessRunner` is already exercised by tests, so it is not untested
code being adopted later.

---

## D-007 — Controller owns state; the executor is a separate, absent component

**Date:** Session 001
**Status:** Accepted

**Context.** The UI needs working Start / Pause / Stop controls, but the brief
says not to implement the whole executor yet.

**Decision.** `PipelineController` implements the *state* side of the control
surface — phase transitions, batch records, session generation, event logging —
and returns a `ControlResult` carrying an explicit `executor_started` flag that
is always `False` in v0.1. Every `request_start` writes
`EXECUTOR_NOT_IMPLEMENTED` to the event log.

**Reason.** Gives a functional UI without implying that agents are running. The
flag makes "did work actually start?" a machine-checkable fact rather than a
message the UI has to parse.

**Consequence.** The future executor implements dispatch against the same
`PipelineState`, `DriverRegistry` and `SessionManager`, and starts reporting
`executor_started=True` honestly. `BatchState.tasks` stays empty until then.

---

## D-008 — No migration framework in v0.1

**Date:** Session 001
**Status:** Accepted

**Context.** The brief says not to over-engineer schema migrations yet, but a
silently mis-read database would be a real hazard.

**Decision.** Record `schema_version` in `schema_meta`. `Database.initialize()`
creates tables idempotently and **refuses to open** a database whose recorded
version is newer than `SCHEMA_VERSION`.

**Reason.** Prevents an older build from misinterpreting a newer schema. Full
forward migrations are premature while the schema is still moving.

**Consequence.** A schema change means bumping `SCHEMA_VERSION` and adding DDL.
A migration mechanism becomes necessary the first time a released schema must
change in place; that decision is deferred, not forgotten.

---

## D-009 — One serialised SQLite connection with an `RLock`

**Date:** Session 001
**Status:** Accepted

**Context.** The UI thread owns the database today, but batch execution will
eventually need worker threads.

**Decision.** A single `sqlite3` connection with `check_same_thread=False`,
guarded by a `threading.RLock`, with writes going through
`Database.transaction()`.

**Reason.** A connection pool buys nothing for a single-user desktop app and
adds lifecycle complexity. Serialising writes is the correct semantics anyway.

**Consequence.** All writes are atomic and roll back on exception. If the
executor later needs concurrent readers, this is the single place to revisit.

---

## D-010 — `src/` layout with a `main.py` shim, no install step

**Date:** Session 001
**Status:** Accepted

**Context.** The project must run from a fresh checkout on Windows without a
packaging step, while still being importable as a package.

**Decision.** Package under `src/encomm_pcc/`. Root `main.py` inserts `src/`
into `sys.path` and calls `encomm_pcc.app.run()`. `pyproject.toml` declares the
same package for anyone who wants to install it; `pytest` gets `pythonpath =
["src"]`.

**Reason.** Keeps a clean package boundary, supports `pip install -e .` later,
and needs no build step to launch the app.

**Consequence.** `main.py` must keep its `sys.path` shim ahead of the
`encomm_pcc` import (marked with a `noqa` and a comment). Tests do not depend
on the shim; they rely on pytest's `pythonpath`.

---

## D-011 — Placeholder values fill only *unconfigured* roles

**Date:** Session 001
**Status:** Accepted

**Context.** `PipelineState.bootstrap()` creates one config per role with empty
fields, so a naive "fill missing roles" pass never runs — and the UI would show
empty engines instead of the placeholder values the brief requires.

**Decision.** `PipelineController._ensure_role_configs()` replaces a role's
config with the documented placeholder when the existing config has no
`engine`, no `project_profile` and no `session_id`. A role the operator has
actually configured is never overwritten.

**Reason.** Satisfies the brief's placeholder requirement while preserving the
project's config-preservation ethic: an explicit configuration is never
silently reset.

**Consequence.** Clearing all three fields deliberately re-arms the
placeholder. `apply_placeholders()` remains available as an explicit reset.

---

## D-012 — Explicit `None` checks instead of truthiness for injected objects

**Date:** Session 001
**Status:** Accepted

**Context.** `EventLog` defines `__len__`, which makes an *empty* log falsy.
`PipelineController` originally used `event_log or NullEventLog()`, which
silently discarded the caller's log and dropped every event — caught by
`test_events_are_written_to_the_database`.

**Decision.** Dependency injection in `PipelineController` uses
`x if x is not None else default`. Objects that define `__len__` are never used
as truthiness defaults.

**Reason.** A truthiness default on a container-like object is a silent
data-loss bug, not a style preference.

**Consequence.** A regression test asserts the supplied empty `EventLog` is
retained and that events reach both the log and the database.

---

## D-013 — Offscreen Qt for all automated UI testing

**Date:** Session 001
**Status:** Accepted

**Context.** The UI must be verified automatically, but this host must never
open stray windows.

**Decision.** `tests/conftest.py` sets `QT_QPA_PLATFORM=offscreen` before Qt is
imported. UI tests construct `MainWindow`, click real buttons and read real
widget state, with no visible window.

**Reason.** Full-fidelity UI verification without a GUI session or a popup.

**Consequence.** UI behaviour is covered by 16 tests. A future requirement to
test native windowing behaviour would need a different harness.
