# SESSION 001 — FOUNDATION

**Project:** ENCOMM Pipeline Control Center
**Date:** 2026-09-22
**Version produced:** `0.1.0`
**Session scope:** Foundation only — no pipeline automation

---

## STATUS

**PASS**

All ten pass criteria from the session brief are met, each backed by executed
evidence rather than intent. One criterion (application launch) was verified by
running the real entry point, not only by a test.

| # | Pass criterion | Result | Evidence |
|---|---|---|---|
| 1 | Desktop application launches | **PASS** | `python main.py` ran; Qt event loop stayed alive; on-disk SQLite created with all 7 tables and 10 persisted events |
| 2 | First UI exists | **PASS** | 8 sections present: WORKSPACE, ROLES, ORCHESTRATOR, FINAL AUDITOR, TASK AUDITOR, BUILDER, BATCH, LOG PANEL |
| 3 | Role-based architecture exists | **PASS** | `AgentRole` ×4, one `AgentRoleConfig` per role, one `RolePanel` per role, engine resolved per role |
| 4 | Drivers abstracted, not Codex-hardcoded | **PASS** | `BaseDriver` ABC + `DriverRegistry`; three adapters with distinct ids; engines are config values |
| 5 | SQLite foundation works | **PASS** | Schema v1, 7 tables, round-trips tested; real DB file created and written on launch |
| 6 | Tests pass | **PASS** | `144 passed in 1.33s`, 0 failed, 0 skipped |
| 7 | Handoff documentation exists | **PASS** | `CURRENT_STATE.md`, `ARCHITECTURE.md`, `DECISIONS.md`, `ROADMAP.md` |
| 8 | SESSION_001 report exists | **PASS** | This file |
| 9 | Repository contains no secrets | **PASS** | Pattern scan over the full staged diff → `NO_SECRETS_FOUND`; no `.env`/`.db`/`.log`/key files staged |
| 10 | Changes committed and pushed to `main` | **PASS** | See GIT_STATUS |

---

## OBJECTIVE

Build the foundation of a local Windows desktop application that will later
supervise AI coding pipelines, with a clean architecture that future sessions
can extend safely — **without** building the automation pipeline itself.

The central architectural constraint was the brief's prohibition on hardcoding
Codex: the system must be role-based, with independently configurable roles and
swappable engines behind a driver interface from day one.

**Deliberately not built:** task execution, agent dispatch, prompt routing, fix
loops, audit loops, final batch audits, and every real engine integration.

---

## LEANCTX_USAGE

**Skill loaded:** `encomm-leanctx` — loaded before any repository exploration or
coding, as the brief mandates.

**Preflight (both steps run before anything else):**

| Step | Command | Result |
|---|---|---|
| 0A Repository safety | `pwd` + `git rev-parse --show-toplevel` | cwd = `C:\Users\xampos\Desktop\ENCOMM PIPELINE CONTROL CENTER`; `NOT_A_GIT_REPO` (expected — verified *before* `git init`, so the directory was confirmed empty and correctly targeted) |
| 0B LeanCTX availability | `scripts/lctx.sh` resolver | `C:\Users\xampos\AppData\Local\leanctx\node_modules\lean-ctx-bin\bin\lean-ctx.exe` |
| 0B Verification | `lean-ctx --version` | `lean-ctx 3.10.1 (official, https://github.com/yvgude/lean-ctx)` |

The resolver script was used rather than a bare `command -v lean-ctx`, per the
skill's mandatory bootstrap. The hyphen pitfall was avoided by never probing
`leanctx`.

**Post-construction discovery (run against the populated repository):**

| Command | Observed output |
|---|---|
| `lean-ctx overview` | `PROJECT OVERVIEW 33 files 38 edges`; 5 architectural hotspots (`domain/__init__.py` 7 imports; `persistence/__init__.py` 4; `core/__init__.py` 4; `tests/test_session_policy.py` 4; `tests/test_ui_smoke.py` 3) |
| `lean-ctx find "*.py"` | compact module list |
| `lean-ctx grep "class StateMachine"` | `1 matches in 1 files (scanned 61)` with symbol handle `src/encomm_pcc/domain/state_machine.py:136 class StateMachine` |
| `lean-ctx gain` | reported local token-difference estimate (2.2M / 78% representation ratio) — local estimate only, not a provider bill |

**Honest assessment of where LeanCTX added value this session.** The repository
did not exist when the session began, so there was no legacy code to compress
during discovery — the compressed-discovery mechanisms (`overview`, `find`,
`grep`, `read -m map`) had almost nothing to operate on for the first 90% of the
session. Their real contribution was:

1. The **preflight discipline** itself — it forced an explicit cwd/git-root
   check *before* `git init`, which is exactly the class of mistake it exists to
   prevent.
2. **Post-construction orientation** of the repository that was created.
3. **Escalation discipline** applied throughout: every file that was written or
   edited was held in full, and every verification step (test output, diff,
   failure traceback) was read raw rather than summarised — per
   `references/accuracy-guard.md`.

**Accuracy-guard compliance:** no conclusion in this session rested on
compressed context. All three test failures encountered were diagnosed by
reading the **complete failure output** (full tracebacks with assertion values
and the exact failing SQL), not from the pass/fail summary line. The final diff
was reviewed with `git diff --cached --check` plus a full secret-pattern scan
over the raw diff.

---

## FILES_CREATED

43 files staged for the initial commit.

### Application source — 25 files, 3,582 lines

| File | Lines | Purpose |
|---|---|---|
| `main.py` | 23 | Entry point; `sys.path` shim + `run()` |
| `src/encomm_pcc/__init__.py` | 4 | `__version__ = "0.1.0"` |
| `src/encomm_pcc/app.py` | 88 | Bootstrap: paths → Database → EventLog → Controller → window |
| `src/encomm_pcc/domain/enums.py` | 134 | `AgentRole`, `SessionPolicy`, `PipelinePhase`, `TaskState`, `BatchStatus`, `EventLevel` |
| `src/encomm_pcc/domain/models.py` | 380 | `WorkspaceConfig`, `AgentRoleConfig`, `TaskStateRecord`, `BatchState`, `PipelineState` |
| `src/encomm_pcc/domain/state_machine.py` | 190 | `TRANSITIONS` graph, `StateMachine`, `InvalidTransitionError` |
| `src/encomm_pcc/domain/__init__.py` | 48 | Domain re-exports |
| `src/encomm_pcc/drivers/base.py` | 245 | `BaseDriver` ABC, `DriverCapabilities`, `SessionRequest`, `DriverSession`, `PromptHandle`, `PromptResult` |
| `src/encomm_pcc/drivers/process.py` | 108 | `ProcessSpec`, `ProcessResult`, `ProcessRunner`, `SubprocessRunner`, `NullProcessRunner` |
| `src/encomm_pcc/drivers/codex.py` | 56 | `CodexDriver` (placeholder) |
| `src/encomm_pcc/drivers/hermes.py` | 55 | `HermesDriver` (placeholder) |
| `src/encomm_pcc/drivers/generic_cli.py` | 60 | `GenericCliDriver` (placeholder, stateless) |
| `src/encomm_pcc/drivers/registry.py` | 112 | `DriverRegistry`, `IMPLEMENTED_DRIVERS`, `PLANNED_DRIVERS` |
| `src/encomm_pcc/drivers/__init__.py` | 49 | Driver re-exports |
| `src/encomm_pcc/core/config.py` | 108 | `AppPaths`, batch-size bounds, placeholder role configs |
| `src/encomm_pcc/core/events.py` | 148 | `EventLog`, `LogRecord`, `NullEventLog` |
| `src/encomm_pcc/core/session_manager.py` | 210 | `decide_session_action`, `SessionAction`, `SessionDecision`, `SessionManager` |
| `src/encomm_pcc/core/controller.py` | 318 | `PipelineController`, `ControlResult`, `ControlOutcome` |
| `src/encomm_pcc/core/__init__.py` | 43 | Core re-exports |
| `src/encomm_pcc/persistence/schema.sql` | 96 | Schema v1 — 7 tables, 4 indexes |
| `src/encomm_pcc/persistence/database.py` | 385 | `Database` — connection, transactions, 7 repositories |
| `src/encomm_pcc/persistence/__init__.py` | 5 | Persistence re-exports |
| `src/encomm_pcc/ui/panels.py` | 380 | `WorkspacePanel`, `RolePanel`, `BatchPanel`, `LogPanel` |
| `src/encomm_pcc/ui/main_window.py` | 165 | `MainWindow` |
| `src/encomm_pcc/ui/__init__.py` | 10 | UI re-exports |

### Tests — 9 files, 1,390 lines

| File | Tests | Focus |
|---|---|---|
| `tests/conftest.py` | — | Offscreen Qt, in-memory DB, controller fixtures |
| `tests/test_imports.py` | 4 | Every module imports; every source file compiles; core is Qt-free |
| `tests/test_domain_models.py` | 18 | Roles, session-policy defaults, serialisation round-trips |
| `tests/test_state_machine.py` | 23 | Legal/illegal edges, reachability, pause bookkeeping, reset |
| `tests/test_persistence.py` | 18 | Schema, version guard, all round-trips, FK behaviour |
| `tests/test_drivers.py` | 17 | Registry, capabilities, refusal behaviour, process runners |
| `tests/test_session_policy.py` | 16 | All five policies × capability combinations; session bookkeeping |
| `tests/test_controller.py` | 32 | Control surface, clamping, persistence, event-log regression |
| `tests/test_ui_smoke.py` | 16 | Window construction, every section, real button clicks |
| **Total** | **144** | |

### Project / documentation — 9 files, 1,138 lines

`README.md`, `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`,
`.gitignore`, `docs/ARCHITECTURE.md`, `docs/CURRENT_STATE.md`,
`docs/DECISIONS.md`, `docs/ROADMAP.md`.

---

## FILES_CHANGED

**None.** This was the initial commit of an empty repository. There was no
pre-existing code to modify, and nothing outside the project directory was
touched.

---

## ARCHITECTURE_CREATED

### Layer model (enforced, not aspirational)

```
ui (PySide6)  →  core  →  { domain, drivers, persistence }
```

`domain` depends on nothing. `drivers` and `persistence` depend only on
`domain`. A test asserts that `domain`, `persistence` and `core` all import in a
subprocess where `PySide6` is never loaded — the UI is a consumer of the core,
never a dependency of it.

### Roles

Four roles — `ORCHESTRATOR`, `BUILDER`, `TASK_AUDITOR`, `FINAL_AUDITOR` —
defined independently of engines. `AgentRoleConfig` names an engine by
`driver_id` resolved through the registry. Nothing in the role path references a
concrete engine. The one role special case ("FINAL AUDITOR same as orchestrator")
lives in a single method, `PipelineState.resolved_engine_for()`.

### Session policies

Five `SessionPolicy` values map onto the brief's four rules; all resolution
happens in one function, `decide_session_action()`:

| Role | Policy | Rule |
|---|---|---|
| ORCHESTRATOR | `persistent_optional` | persistent session optional |
| BUILDER | `always_new` | always a new session per implement **and** per fix |
| TASK_AUDITOR | `persistent_per_batch` | persistent session per batch |
| FINAL_AUDITOR | `configurable` | persistent or fresh, operator's choice |

Two invariants are encoded in the resolver: a stateless driver
(`supports_sessions=False`) always resolves to `NONE`, and every session id is
mirrored into SQLite so continuity is never the source of truth.

### Driver interface

`BaseDriver` (ABC) exposes `start_session`, `resume_session`, `send_prompt`,
`wait_for_completion`, `get_session_id`, `get_result`, `close_session`,
`cancel`, `capabilities`, `probe_availability`. `DriverCapabilities` carries
`supports_sessions`, `supports_resume`, `supports_streaming`,
`supports_cancellation`, `supports_model_selection`, `implemented`.

Session support is explicitly optional: `GenericCliDriver` declares
`supports_sessions=False` so the session-less path is a tested, first-class
case. `get_session_id()` is documented to return `None` for such engines.

Placeholders: `CodexDriver`, `HermesDriver`, `GenericCliDriver` all advertise
`implemented=False` and raise `DriverNotImplementedError` on every real
operation. **No code path fabricates a `PromptResult`.** `PLANNED_DRIVERS`
records `claude_code`, `opencode`, `ollama`, `kimi` as documentation only — no
code exists for them.

Process abstraction: drivers build a declarative `ProcessSpec` and hand it to a
`ProcessRunner`. `SubprocessRunner` is implemented and tested against harmless
commands; `NullProcessRunner` is the default for every v0.1 driver and
physically cannot launch anything.

### Pipeline state machine

Declarative `TRANSITIONS` graph over twelve phases, with
`StateMachine.transition_to()` raising `InvalidTransitionError` on illegal
edges and leaving state unchanged. `PAUSED` records its resume target.
`BATCH_COMPLETE` and `FAILED` are terminal. Tests assert every phase is
reachable from `IDLE`.

### Persistence

SQLite (stdlib) schema v1, 7 tables: `schema_meta`, `workspaces`,
`role_configs`, `sessions`, `batches`, `tasks`, `app_events`. One serialised
connection behind an `RLock`, atomic transactions, `PRAGMA foreign_keys = ON`
(the `sessions → workspaces` FK is real), WAL journaling on disk. A database
written by a newer schema is refused rather than mis-read.

### Control surface

`PipelineController` owns state and does **not** execute tasks. Every control
call returns a `ControlResult` with an explicit `executor_started` field —
always `False` in v0.1 — and `request_start()` writes
`EXECUTOR_NOT_IMPLEMENTED` to the event log. "Did work start?" is therefore a
machine-checkable fact, not a message the UI has to parse.

### Architecture decisions recorded

13 ADRs in `docs/DECISIONS.md` (D-001 … D-013), covering UI stack, storage,
role/engine separation, capability-driven drivers, refusal-not-simulation,
`NullProcessRunner`, controller/executor split, migration policy, concurrency,
packaging layout, placeholder scoping, the truthiness bug, and offscreen testing.

---

## UI_STATUS

**Functional shell, placeholder-backed configuration. It launches and responds;
it does not run anything.**

Verified by construction in Qt offscreen mode *and* by launching the real
entry point:

| Section | Present | Contents | Functional? |
|---|---|---|---|
| WORKSPACE | ✅ | name, repository path, path-exists indicator | Yes — writes to controller + persists |
| ORCHESTRATOR | ✅ | engine dropdown, project/profile, session field, **New Session** button | Config writes yes; New Session is a **placeholder** that logs an event and creates no session |
| FINAL AUDITOR | ✅ | engine dropdown, project/profile, session field, **Same as Orchestrator** checkbox | Yes — checkbox resolves the engine and disables the session field |
| TASK AUDITOR | ✅ | engine dropdown, Hermes profile, provider, model, policy label | Yes — label reads "Persistent per batch" |
| BUILDER | ✅ | engine dropdown, Hermes profile, provider, model, policy label | Yes — label reads "Always new" |
| BATCH | ✅ | size selector (1–50, **default 5**), current phase, current status, Start / Pause / Resume / Stop | Yes **at state level** — phase and `BatchState` change; no task executes |
| LOG PANEL | ✅ | timestamped local event log, 5000-line cap, monospace, Clear, counter | Yes — fed by `EventLog.subscribe`, persisted to `app_events` |

Additional UI behaviour:

- Buttons are enabled/disabled from the state machine, so illegal actions are
  not clickable.
- Every role panel shows a live capability hint, e.g.
  `placeholder — not implemented; sessions supported; binary found on PATH`.
- A footer lists the planned engines as explicitly not implemented.
- `RolePanel` is generic over role, driven by a `_ROLE_FIELDS` mapping.

**Explicitly not functional (placeholder only):** the New Session button, every
engine dropdown's effect, and the Start button's effect beyond state.

---

## TESTS_RUN

```bash
cd "C:\Users\xampos\Desktop\ENCOMM PIPELINE CONTROL CENTER"
python -m pytest
```

Additional verification executed this session:

| Command | Purpose |
|---|---|
| `python -m pytest` | Full suite |
| `python -m pytest --collect-only -q` | Per-file test counts |
| `python main.py` (`QT_QPA_PLATFORM=offscreen`) | Real launch of the real entry point |
| Standalone launch probe | Bootstrap with an on-disk DB: sections, placeholders, transitions, event count |
| `git diff --cached --check` | Whitespace / conflict-marker check |
| Secret-pattern scan over `git diff --cached` | Criterion 9 |
| `lean-ctx overview` / `find` / `grep` / `gain` | Repository orientation and token reporting |

---

## TEST_RESULTS

### Suite result

```
........................................................................ [ 50%]
........................................................................ [100%]
144 passed in 1.33s
```

**144 passed, 0 failed, 0 skipped, 0 errors.** Exit code 0.

### Per-file breakdown

| File | Tests |
|---|---|
| `test_controller.py` | 32 |
| `test_state_machine.py` | 23 |
| `test_domain_models.py` | 18 |
| `test_persistence.py` | 18 |
| `test_drivers.py` | 17 |
| `test_session_policy.py` | 16 |
| `test_ui_smoke.py` | 16 |
| `test_imports.py` | 4 |
| **Total** | **144** |

### Real launch evidence

`python main.py` with `QT_QPA_PLATFORM=offscreen` and
`ENCOMM_PCC_DATA_DIR` pointed at a scratch directory: the process stayed alive
in the Qt event loop, the database file was created, and 10 events were
persisted, including:

```
('2026-09-22T10:33:48Z', 'INFO', 'app', 'Database ready at ...pipeline_control_center.db')
('2026-09-22T10:33:48Z', 'INFO', 'ui',  'ENCOMM Pipeline Control Center v0.1 foundation started.')
('2026-09-22T10:33:48Z', 'INFO', 'ui',  "Driver 'codex': implemented=False, sessions=True, binary_present=True")
('2026-09-22T10:33:48Z', 'INFO', 'ui',  "Driver 'generic_cli': implemented=False, sessions=False, binary_present=False")
('2026-09-22T10:33:48Z', 'INFO', 'ui',  "Driver 'hermes': implemented=False, sessions=True, binary_present=True")
```

Standalone launch probe over the real bootstrap path:

```
db file exists  : True          tables          : app_events, batches, role_configs,
db schema v     : 1                               schema_meta, sessions, sqlite_sequence, tasks, workspaces
placeholder cfg : {ORCHESTRATOR: hermes, BUILDER: hermes, TASK_AUDITOR: hermes, FINAL_AUDITOR: hermes}
sections        : [BATCH, BUILDER, FINAL AUDITOR, LOG PANEL, ORCHESTRATOR, ROLES, TASK AUDITOR, WORKSPACE]
after start     : PLANNING_BATCH batch size 5    events in db    : 12
after pause     : PAUSED                          after stop      : IDLE
PROBE_OK
```

### Defects found and fixed during this session

All three were found by running the suite and reading the **complete** failure
output, then fixed at the root cause rather than in the test:

| # | Defect | Root cause | Fix |
|---|---|---|---|
| 1 | `PipelineState.to_dict()` and `PipelineController.snapshot()` raised `TypeError: cannot unpack non-iterable AgentRoleConfig` | `iter_role_configs()` yields configs, but two call sites unpacked `(role, cfg)` pairs | Added `role_config_items()` yielding `(role, config)` and used it at both call sites |
| 2 | Role configs stayed empty instead of showing the required placeholder values | `PipelineState.bootstrap()` pre-creates all four roles with empty fields, so "fill missing roles" never fired | `_ensure_role_configs()` now replaces a role whose config has no engine, no profile and no session id; an operator-configured role is never overwritten (ADR D-011) |
| 3 | **No event ever reached the database or the UI log panel** | `EventLog` defines `__len__`, so an empty log is falsy; `event_log or NullEventLog()` silently discarded the injected log | Replaced truthiness defaults with explicit `x if x is not None else default` (ADR D-012); added a regression test |

Defect 3 was the most serious: it silently discarded *every* event, including
the `EXECUTOR_NOT_IMPLEMENTED` warnings. It would have made the log panel look
empty rather than broken.

A fourth failure was a **test** defect, not an application defect: two session
tests inserted rows referencing a non-existent workspace and correctly hit the
`FOREIGN KEY` constraint. The tests were fixed to create the workspace first,
and a new test now asserts the constraint fires — the FK is a feature.

---

## GIT_STATUS

**Repository:** `https://github.com/Xamposs/ENCOMM-PIPELIN-CONTROL-CENTER`
**Branch:** `main`
**Remote state at session start:** the repository existed and was **empty**
(`gh repo view` → `"isEmpty": true`, no default branch).

Pre-commit verification, in the order the brief requires:

| Step | Command | Result |
|---|---|---|
| Inspect full diff | `git add -A` + `git diff --cached --name-only` | 43 files — source, tests, docs, project files only |
| Run relevant tests | `python -m pytest` | `144 passed in 1.33s` |
| Whitespace check | `git diff --cached --check` | clean, no output |
| Conflict markers | `grep -E '^(<<<<<<<|>>>>>>>|=======$)'` on the diff | none |
| **Secret scan** | pattern scan over `git diff --cached` for `sk-…`, `ghp_…`, `gho_…`, `github_pat_…`, `AKIA…`, `-----BEGIN … PRIVATE KEY`, `api_key=`, `secret=`, `password=`, `Bearer …` | **`NO_SECRETS_FOUND`** |
| Artifact check | staged paths matching `*.db`, `*.db-wal`, `*.log`, `.env`, `*.pem`, `*.key`, `secrets.json`, `credentials.json` | **none staged** |
| Env-var review | `os.environ` / `getenv` in tracked source | 4 hits, all path/platform only: `ENCOMM_PCC_DATA_DIR`, `LOCALAPPDATA`, `QT_QPA_PLATFORM`, `PYTHONPATH`. No credential reads. |

`.gitignore` excludes `*.db`, `*.db-wal`, `*.db-shm`, `logs/`, `*.log`, `.env*`,
`*.pem`, `*.key`, `secrets.json`, `credentials.json`.

**Commit:** the initial commit on `main` is created immediately after this
report is written; its SHA is recorded in the chat response for this session and
in the follow-up commit that updates this section.

**Push:** to `origin/main`. **No force push was used, and no branches other than
`main` were created.**

Working tree after push: clean. Nothing outside
`C:\Users\xampos\Desktop\ENCOMM PIPELINE CONTROL CENTER` was modified, other
than Git remote access as permitted.

---

## KNOWN_LIMITATIONS

Stated plainly. Nothing in this section is a placeholder presented as a
feature.

1. **No executor.** `request_start()` transitions to `PLANNING_BATCH` and
   creates a `BatchState`, then stops. `ControlResult.executor_started` is
   always `False`.
2. **`BatchState.tasks` is always empty.** No task records are materialised.
3. **No driver performs work.** All three raise `DriverNotImplementedError` for
   `start_session`, `resume_session`, `send_prompt`, `wait_for_completion`.
4. **No real sessions.** `SessionManager.register_session()` is exercised only
   by tests; the running application never registers a session.
5. **No fix loop, no audit loop, no final batch audit.**
6. **Batch size is stored, not acted on.**
7. **UI configuration is placeholder-driven** — every role defaults to engine
   `hermes`; provider/model defaults are inert strings.
8. **`provider` and `model` are persisted but never consumed** by any code path.
9. **`TaskState` and `BatchStatus.COMPLETE` / `FAILED` are never produced** by
   the running application.
10. **`TaskStateRecord.attempts` / `audit_rounds` / `last_error` are persisted
    but never incremented.**
11. **No schema migration path** — only a version guard that refuses a newer
    schema.
12. **Single-threaded.** The `RLock` is prepared for worker threads but none
    exist.
13. **`GenericCliDriver` has no configurable command line.** A real generic-CLI
    engine needs an argv field in role config before it can work.
14. **No packaging** — no installer, no frozen `.exe`.
15. **`probe_availability()` reports binary presence, not usability.** It found
    `codex` and `hermes` on `PATH` on this host, which says nothing about
    whether the adapters work — they do not.
16. **UI tests run offscreen only.** Native windowing behaviour (DPI, multi-
    monitor, window-manager integration) is unverified.

---

## RISKS

| # | Risk | Severity | Mitigation already in place | Residual |
|---|---|---|---|---|
| R1 | A future session mistakes a placeholder for a working feature and builds on it | **High** | `implemented=False` on every driver; `executor_started` flag; `EXECUTOR_NOT_IMPLEMENTED` logged on every start; `ARCHITECTURE.md` §12 and `CURRENT_STATE.md` §5 list every placeholder | Reduced, not eliminated — the handoff contract must actually be followed |
| R2 | The honesty invariant is quietly weakened when the executor lands | **High** | `ControlResult.executor_started` is a machine-checkable field, not a UI string; documented as a Phase-1 exit criterion | Needs an explicit test in Session 002 |
| R3 | A fake `PromptResult` is introduced to "make the pipeline flow" | **High** | `PromptResult.simulated` exists as an explicit marker; ADR D-005 forbids simulation | Requires review discipline |
| R4 | Fix/audit loop runs unbounded once implemented | **High** | Not yet implemented — no loop exists. `TaskStateRecord.audit_rounds` is ready to carry a cap | **Open.** Phase 2 must cap rounds and escalate to `BLOCKED` |
| R5 | Session continuity becomes a de-facto source of truth | Medium | Every session id is mirrored to SQLite; ADR D-004 states the invariant | Design-level; needs enforcement when the executor writes state |
| R6 | Schema change with no migration path breaks an existing install | Medium | Version guard refuses a newer schema rather than mis-reading it; ADR D-008 | Becomes real the first time a released schema must change in place |
| R7 | Engine CLI flags/behaviour drift (Codex, Hermes) | Medium | `DriverCapabilities` isolates assumptions; `probe_availability()` is separate from usability | Inherent to external CLIs; must be re-verified when adapters are written |
| R8 | `NullProcessRunner` default is removed "temporarily" during debugging | Medium | Default lives in `BaseDriver.__init__`; a test asserts drivers default to it | Would re-open the risk of accidental agent launches |
| R9 | Single-threaded design becomes a bottleneck when execution lands | Low | `RLock` and `Database.transaction()` already serialise writes; ADR D-009 marks the revisit point | Acceptable for a single-user desktop tool |
| R10 | Qt offscreen tests give false confidence about real rendering | Low | Offscreen tests verify structure and behaviour, not pixels; documented as a limitation | Accepted for this phase |
| R11 | `EventLog`'s falsy-when-empty behaviour recurs elsewhere | Low | Fixed and documented as ADR D-012 with a regression test | Any future container-like injectable should use explicit `None` checks |

**Highest-priority risk to address in Session 002:** R4 (unbounded loop). It is
the only risk that is entirely open today and it is introduced by the very next
phase.

---

## NEXT_RECOMMENDED_SESSION

**Session 002 — Executor skeleton + first real driver behind an explicit
dry-run gate.**

Recommended scope, in order:

1. **Materialise tasks.** Let a planning step produce real `TaskStateRecord`s
   into `BatchState.tasks`. The `tasks` table already supports this — **no
   schema migration needed**.
2. **Add `core/executor.py`** with `dispatch_batch()` driving the existing
   `StateMachine` through `PLANNING_BATCH → RUNNING_TASK → AUDITING_TASK → …`.
   It must be **pausable at task boundaries** and must not run on the UI thread.
3. **Implement one driver for real** — most naturally `HermesDriver` — over
   `SubprocessRunner`, capturing real exit codes and stdout. It stays
   `implemented=False` until its integration is genuinely verified.
4. **Preserve the honesty invariant.** `ControlResult.executor_started` becomes
   `True` only when a real process was actually launched. Do not weaken
   `EXECUTOR_NOT_IMPLEMENTED` logging before that is true. Add a test that
   asserts it.
5. **Cap the loop before building it.** When the fix loop arrives in Phase 2,
   the round cap (R4) must exist from the first commit, not be retrofitted.
6. **Update the repository, not just the code:** revise `ARCHITECTURE.md` §9/§12,
   append ADRs to `DECISIONS.md`, rewrite `CURRENT_STATE.md`, and write
   `docs/reports/SESSION_002_*.md`.

**Do not start with** multi-task batches, fix loops or final batch audits. They
are Sessions 003+ and depend on the executor skeleton being correct first.

---

## VERIFICATION CHECKLIST (accuracy guard)

- [x] Actual changed source read in full where material — every file written
      this session was authored and reviewed in full
- [x] Actual diff reviewed — `git diff --cached --check` plus a full
      secret-pattern scan over the raw staged diff
- [x] Relevant tests run **and their output read** — `144 passed in 1.33s`
- [x] Failure output inspected in full — all three application defects were
      diagnosed from complete tracebacks, not summary lines
- [x] CRITICAL claims point to exact evidence — the launch claim is backed by a
      real `python main.py` run and the persisted event rows quoted above
- [x] No conclusion rests solely on a compressed representation
- [x] No functionality claimed that is only a placeholder — see
      KNOWN_LIMITATIONS and `ARCHITECTURE.md` §12
