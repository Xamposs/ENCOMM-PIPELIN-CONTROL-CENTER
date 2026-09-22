# Current State — ENCOMM Pipeline Control Center

**This is the canonical handoff file.** It describes exactly what exists today.

---

## 0. MANDATORY NEW-SESSION HANDOFF CONTRACT

Every new development session — human or agent — must begin by reading, in
order:

1. `docs/CURRENT_STATE.md`   ← this file
2. `docs/ARCHITECTURE.md`
3. `docs/DECISIONS.md`
4. `docs/ROADMAP.md`
5. the most recent report in `docs/reports/`

and must use the **LeanCTX** skill (`encomm-leanctx`) for repository discovery
before exploring or editing code.

**No critical architectural information may exist only in chat history.**
Anything a future session needs must be in this repository. If you learn
something that changes the architecture, write it into `ARCHITECTURE.md` and
append an entry to `DECISIONS.md` before you finish.

**Read §5 "Known limitations" before claiming anything works.** Several
components are deliberate placeholders, and `ARCHITECTURE.md` §12 lists them
explicitly.

---

## 1. Version

`0.3.0` — single-task Task Auditor + capped fix loop. Session 003.

---

## 2. What currently works

| Capability | Status | Evidence |
|---|---|---|
| Desktop application launches | **Works** | `python main.py` starts, Qt event loop runs, window constructed |
| Main window sections | **Works** | WORKSPACE, ROLES (4 role panels), BATCH, TASK, LOG PANEL |
| Role-based architecture (4 independent roles) | **Works** | `AgentRole`, `AgentRoleConfig`, one `RolePanel` per role |
| Engine abstraction (no engine hardcoding) | **Works** | `BaseDriver` ABC + `DriverRegistry`; engines are config values |
| SQLite persistence | **Works** | Schema **v3**, 7 tables, on-disk DB created and written on launch |
| Session policy engine | **Works** | `decide_session_action()`; all four brief-mandated rules enforced |
| Pipeline phase state machine | **Works** | Declarative graph + validator; illegal edges rejected |
| **Deterministic executor** | **Works** | `core/executor.py`: read-only gate + preflight, one task, `IDLE → PLANNING_BATCH → RUNNING_TASK → AUDITING_TASK` |
| **Real Hermes execution** | **Works** | Session 002 + Session 003 smokes, real exit codes, real session ids |
| **`HermesDriver`** | **Works — `implemented=True`** | Real argv, real exit codes, real session ids, `--resume` proven live |
| **Real Task Auditor** | **Works — Session 003** | `TASK_AUDITOR` resolved through role config → driver registry → `SessionManager`; `persistent_per_batch`; runs in a real Hermes session |
| **Strict verdict parsing** | **Works — fails closed** | `core/verdict_parser.py`: bounded, JSON-only, whitelisted verdicts; malformed ⇒ `BLOCKED`, never `PASS` |
| **Audit/fix loop** | **Works — capped** | `AUDITING_TASK → FIX_REQUIRED → RUNNING_FIX → AUDITING_TASK`, `MAX_AUDIT_ROUNDS = 3`, escalation to `BLOCKED`; no infinite loop reachable |
| **Session isolation** | **Works — proven** | Fix always in a NEW Builder session (`always_new`); re-audit resumes the SAME auditor session (unit + real smoke) |
| **Restart recovery** | **Works** | `batches.phase` persisted; state/verdict/session ids reload from SQLite; `next_task_action()` decides, never auto-runs |
| **UI shows the loop** | **Works** | Next action (AUDIT/FIX/RE-AUDIT/COMPLETE/BLOCKED), verdict, audit round, session readouts, Run-Auditor / Run-fix buttons |
| Failure propagation | **Works** | Non-zero exit → task `FAILED`, pipeline `FAILED`, error persisted |
| UI dispatch off the UI thread | **Works** | `ExecutorWorker` on a `QThread`; audit/fix run through the same worker |
| Automated tests | **Works** | 280 passed, 0 failed (221 at Session 002, 18 files) |
| `CodexDriver`, `GenericCliDriver` | **Placeholder** | Still raise `DriverNotImplementedError`; `implemented=False` |
| Orchestrator / final auditor / multi-task batches | **Not implemented** | Deliberate — see §5 |

### Verified at the end of Session 003

- `python -m pytest` → `280 passed`.
- Real smoke `python scripts/session_003_audit_fix_smoke.py --profile
  encomm-pipeline-control-center` proves: initial audit **NEEDS_FIX** → fix in
  a brand-new Builder session → deterministic scratch test passes → **SAME**
  auditor session re-audits → **PASS** → task `APPROVED`, pipeline
  `BATCH_COMPLETE`, state re-read from SQLite. Session ids, verdicts and
  wall-clock details are in `docs/reports/SESSION_003_AUDITOR_FIX_LOOP.md`.
- Session 002's `session_002_smoke.py` still passes (real Builder path intact).

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
├── scripts/
│   ├── session_002_smoke.py    Real Hermes executor smoke (Session 002)
│   └── session_003_audit_fix_smoke.py  Real audit/fix loop smoke (Session 003)
├── src/encomm_pcc/
│   ├── __init__.py             __version__ = "0.3.0"
│   ├── app.py                  run(), build_controller(), attach_default_executor(),
│   │                           restore_state(), discover_hermes_profiles()
│   ├── domain/
│   │   ├── enums.py            AgentRole, SessionPolicy, PipelinePhase,
│   │   │                       TaskState, BatchStatus, EventLevel
│   │   ├── audit.py            (new) AuditVerdict, FindingSeverity,
│   │   │                       AuditFinding, AuditVerdictResult
│   │   ├── models.py           WorkspaceConfig, AgentRoleConfig,
│   │   │                       TaskStateRecord (+verdict/session fields),
│   │   │                       BatchState (+phase), PipelineState
│   │   └── state_machine.py    TRANSITIONS, StateMachine, InvalidTransitionError
│   ├── drivers/
│   │   ├── base.py             BaseDriver ABC, DriverCapabilities,
│   │   │                       SessionRequest, DriverSession, PromptHandle,
│   │   │                       PromptResult, DriverNotImplementedError
│   │   ├── process.py          ProcessSpec, ProcessResult, SubprocessRunner
│   │   │                       (timeout + tree kill), NullProcessRunner
│   │   ├── hermes_cli.py       The verified Hermes CLI contract
│   │   ├── hermes.py           HermesDriver — REAL (implemented=True)
│   │   ├── codex.py            CodexDriver      (placeholder)
│   │   ├── generic_cli.py      GenericCliDriver (placeholder, stateless)
│   │   └── registry.py         DriverRegistry, IMPLEMENTED_DRIVERS,
│   │                           PLANNED_DRIVERS
│   ├── core/
│   │   ├── config.py           AppPaths, batch-size bounds, placeholders
│   │   ├── events.py           EventLog, LogRecord, NullEventLog
│   │   ├── session_manager.py  decide_session_action, SessionManager,
│   │   │                       restore_session (restart recovery)
│   │   ├── hermes_profiles.py  Read-only Hermes profile discovery
│   │   ├── verdict_parser.py   (new) strict, fails-closed verdict parser
│   │   ├── audit_packet.py     (new) AuditPacket + fix-prompt builders
│   │   ├── executor.py         TaskSpec, ExecutionOutcome, ExecutionReport,
│   │   │                       Executor (dispatch + run_task_audit/run_task_fix,
│   │   │                       prepare_task_for_audit, next_task_action),
│   │   │                       MAX_AUDIT_ROUNDS, TaskNextAction
│   │   └── controller.py       PipelineController, ControlResult
│   ├── persistence/
│   │   ├── schema.sql          Schema v3 — tasks audit columns + batches.phase
│   │   └── database.py         Database (v1→v2→v3 targeted upgrades)
│   └── ui/
│       ├── main_window.py      MainWindow (+ audit/fix worker wiring)
│       ├── panels.py           WorkspacePanel, RolePanel, BatchPanel, LogPanel,
│       │                       TaskPanel (audit loop readouts + buttons)
│       └── worker.py           ExecutorWorker (dispatch/audit/fix actions)
├── tests/                      280 tests across 18 files
└── docs/
    ├── ARCHITECTURE.md         Actual architecture (read second)
    ├── CURRENT_STATE.md        This file (read first)
    ├── ROADMAP.md              Phased plan
    ├── DECISIONS.md            D-001 … D-024 with reasons
    └── reports/
        ├── SESSION_001_FOUNDATION.md
        ├── SESSION_002_HERMES_EXECUTOR.md
        └── SESSION_003_AUDITOR_FIX_LOOP.md
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
| D-006 | `NullProcessRunner` is the default runner; the real one is wired in exactly one place |
| D-007 | `PipelineController` owns state; the executor is a separate component |
| D-008 | No migration framework; a newer schema is refused, not mis-read |
| D-009 | One serialised SQLite connection behind an `RLock` |
| D-010 | `src/` layout + `main.py` shim, no install step |
| D-011 | Placeholders fill only *unconfigured* roles |
| D-012 | Explicit `None` checks, never truthiness, for injected objects |
| D-013 | `QT_QPA_PLATFORM=offscreen` for all automated UI testing |
| D-014 | The first real engine integration drives the installed Hermes CLI (verified contract) |
| D-015 | Supervised processes get a filtered environment (no `HERMES_*` / `PYTHONPATH` leak) |
| D-016 | Schema v2: a task keeps its implementation prompt (first in-place upgrade) |
| D-017 | The executor owns dispatch, the controller owns state; `executor_started` is launch-derived |
| D-018 | Honest, evidence-gated capabilities and boundary-only stop/pause |
| D-019 | Structured audit verdicts from untrusted model output — strict parser fails closed |
| D-020 | Capped single-task audit/fix loop (`MAX_AUDIT_ROUNDS = 3`); `AUDITING_TASK → BATCH_COMPLETE` |
| D-021 | Auditor session lifecycle: `persistent_per_batch` with restart restore |
| D-022 | Fixes always run in a brand-new Builder session |
| D-023 | Restart recovery restores the phase and decides, never auto-resumes |
| D-024 | The Session 003 smoke is scratch-only and costs at most three model runs |

---

## 5. Known limitations

Stated bluntly so nothing is over-claimed:

1. **One task per batch.** A second dispatch into the same batch is rejected.
   There is no planner, no orchestrator and no batch editor.
2. **The audit/fix loop is single-task and capped at `MAX_AUDIT_ROUNDS = 3`.**
   A NEEDS_FIX on round 3 blocks the task; the operator must intervene.
3. **No multi-task sequencing, no Orchestrator, no Final Auditor.** `BATCH_COMPLETE`
   is reached directly from a single-task PASS; `READY_FOR_FINAL_AUDIT` and
   `FINAL_AUDIT_RUNNING` are graph nodes only, unreachable in this session.
4. **No mid-prompt cancellation.** `supports_cancellation=False`; `cancel()`
   returns `False`. A stop requested during a prompt lets the run finish.
5. **Pause is boundary-only.** With one task there is no mid-run boundary.
6. **`BatchState.tasks` holds only manually supplied tasks.**
7. **`CodexDriver` and `GenericCliDriver` remain placeholders.**
8. **No streaming surface.** The CLI's deltas are consumed internally.
9. **Output retention is bounded** — 4000-char excerpts in event payloads.
10. **`Executor` pause/stop flags are in memory.** Persisted state survives a
    crash; the stop *request* does not.
11. **`BLOCKED` leaves the batch status `RUNNING`** (there is no
    `BatchStatus.BLOCKED`); the authoritative signal is the pipeline phase and
    the task state, both persisted.
12. **Provider/model are real per invocation**, but stored role values are
    operator-typed strings — no validation against a provider catalogue.
13. **`verdict_json` stores the bounded structured verdict** (summary, findings,
    fix prompt) — never a full transcript.
14. **Schema v3 has exactly two upgrade steps** (v1→v2, v2→v3). No framework.
15. **UI tests are offscreen only.**
16. **The real smoke costs exactly three model runs** (initial audit, fix,
    re-audit); provider failures mid-loop and multi-round cap behaviour are
    unit-tested offline, not exercised live.
17. **No packaging.** No installer, no frozen `.exe`.

---

## 6. Files most likely relevant next

In the order a Session 004 (multi-task batches / orchestrator) is likely to
touch them:

| File | Why |
|---|---|
| `src/encomm_pcc/core/executor.py` | `_dispatch()` and the audit/fix loop are the sequence to generalise to N tasks |
| `src/encomm_pcc/core/controller.py` | `request_start`/batch lifecycle will need per-task iteration |
| `src/encomm_pcc/domain/state_machine.py` | `RUNNING_TASK` already has a "next task" edge; batch-loop logic goes with the orchestrator |
| `src/encomm_pcc/persistence/database.py` | Batch/task rows already round-trip; sequence planning adds rows |
| `src/encomm_pcc/ui/panels.py` | Batch progress / per-task table |
| `docs/ROADMAP.md` | Phase 3 spec |

---

## 7. Exact next recommended phase

**Session 004 — Multi-task batches + Orchestrator.**

1. `ORCHESTRATOR` plans a batch of N tasks from a workspace/project brief;
   tasks are materialised and sequenced, and batch size from the UI is honoured.
2. `RUNNING_TASK → AUDITING_TASK → (PASS) → RUNNING_TASK (next task)` per task,
   with the Session 003 audit/fix loop reused per task inside the batch.
3. Per-task progress and batch status surface live in the UI.
4. Batch completion leads to `READY_FOR_FINAL_AUDIT` (Phase 4 adds the real
   Final Auditor).

**Do not start** planning/orchestration until the single-task audit/fix loop
(the Session 003 deliverable) is fully reviewed. See `ROADMAP.md`.