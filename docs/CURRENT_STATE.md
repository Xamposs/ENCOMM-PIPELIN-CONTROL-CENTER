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

`0.2.0` — real Hermes executor path. Session 002.

---

## 2. What currently works

| Capability | Status | Evidence |
|---|---|---|
| Desktop application launches | **Works** | `python main.py` starts, Qt event loop runs, window constructed |
| Main window sections | **Works** | WORKSPACE, ROLES (4 role panels), BATCH, TASK, LOG PANEL |
| Role-based architecture (4 independent roles) | **Works** | `AgentRole`, `AgentRoleConfig`, one `RolePanel` per role |
| Engine abstraction (no Codex hardcoding) | **Works** | `BaseDriver` ABC + `DriverRegistry`; engines are config values |
| SQLite persistence | **Works** | Schema **v2**, 7 tables, on-disk DB created and written on launch |
| Session policy engine | **Works** | `decide_session_action()`; all four brief-mandated rules enforced |
| Pipeline phase state machine | **Works** | Declarative graph + validator; illegal edges rejected |
| **Deterministic executor** | **Works** | `core/executor.py`: read-only gate + preflight, one task, `IDLE → PLANNING_BATCH → RUNNING_TASK → AUDITING_TASK` |
| **Real Hermes execution** | **Works — verified live** | Session `20260922_172437_722edb`, process exit code 0, answer `ENCOMM_PCC_HERMES_SMOKE_OK`, 12.7 s |
| **`HermesDriver`** | **Works — `implemented=True`** | Real argv, real exit codes, real session ids, `--resume` proven live |
| **Real session ids** | **Works** | Reported by the CLI, stored with `external=1`; stays `None` when the CLI exposes none |
| **Task materialisation** | **Works** | `Executor.materialise_task()` persists title/prompt/state/attempts |
| **Failure propagation** | **Works** | Non-zero exit → task `FAILED`, pipeline `FAILED`, error persisted and logged |
| **UI dispatch off the UI thread** | **Works** | `ExecutorWorker` on a `QThread`; the event loop stays responsive (tested) |
| **Hermes profile discovery** | **Works (read-only)** | `hermes profile list` parsed; identity-marker directory scan as fallback; 10 profiles on this host |
| Provider/model wiring | **Works — verified live** | `-m`/`--provider` per invocation; a probe run reported the requested model |
| Event log (UI + database) | **Works** | Timestamped records in the log panel and in `app_events` |
| Automated tests | **Works** | 221 passed, 0 failed, 13 files |
| `CodexDriver`, `GenericCliDriver` | **Placeholder** | Still raise `DriverNotImplementedError`; `implemented=False` |
| Task auditor / fix loop / orchestrator / final auditor | **Not implemented** | Deliberate — see §5 |

### Verified at the end of Session 002

- `python -m pytest` → `221 passed in 4.86s`
- `python scripts/session_002_smoke.py --profile encomm-pipeline-control-center`
  → `SMOKE PASSED`: real session `20260922_172437_722edb`, exit code 0, output
  exactly `ENCOMM_PCC_HERMES_SMOKE_OK`, result re-read from SQLite (task in
  `AUDITING`, session row `external=1`, event payload with real token counts).
- Resume re-continued that same session (`PROVEN`), and a model-override probe
  reported `deepseek/deepseek-v4.1-flash` — both with exit code 0.

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
│   └── session_002_smoke.py    The real end-to-end run (NOT part of the suite)
├── src/encomm_pcc/
│   ├── __init__.py             __version__ = "0.2.0"
│   ├── app.py                  run(), build_controller(), attach_default_executor(),
│   │                           restore_state(), discover_hermes_profiles()
│   ├── domain/
│   │   ├── enums.py            AgentRole, SessionPolicy, PipelinePhase,
│   │   │                       TaskState, BatchStatus, EventLevel
│   │   ├── models.py           WorkspaceConfig, AgentRoleConfig,
│   │   │                       TaskStateRecord (+prompt), BatchState, PipelineState
│   │   └── state_machine.py    TRANSITIONS, StateMachine, InvalidTransitionError
│   ├── drivers/
│   │   ├── base.py             BaseDriver ABC, DriverCapabilities,
│   │   │                       SessionRequest, DriverSession, PromptHandle,
│   │   │                       PromptResult, DriverNotImplementedError
│   │   ├── process.py          ProcessSpec, ProcessResult, SubprocessRunner
│   │   │                       (timeout + tree kill), NullProcessRunner
│   │   ├── hermes_cli.py       The verified Hermes CLI contract (argv builder,
│   │   │                       JSONL parser, exit codes, child-env filter)
│   │   ├── hermes.py           HermesDriver — REAL (implemented=True)
│   │   ├── codex.py            CodexDriver      (placeholder)
│   │   ├── generic_cli.py      GenericCliDriver (placeholder, stateless)
│   │   └── registry.py         DriverRegistry, IMPLEMENTED_DRIVERS,
│   │                           PLANNED_DRIVERS
│   ├── core/
│   │   ├── config.py           AppPaths, batch-size bounds, placeholders
│   │   ├── events.py           EventLog, LogRecord, NullEventLog
│   │   ├── session_manager.py  decide_session_action, SessionManager
│   │   ├── hermes_profiles.py  Read-only Hermes profile discovery
│   │   ├── executor.py         TaskSpec, ExecutionOutcome, ExecutionReport, Executor
│   │   └── controller.py       PipelineController, ControlResult
│   ├── persistence/
│   │   ├── schema.sql          Schema v2 — 7 tables (tasks.prompt added)
│   │   └── database.py         Database (+ targeted v1→v2 upgrade)
│   └── ui/
│       ├── main_window.py      MainWindow (+ dispatch wiring)
│       ├── panels.py           WorkspacePanel, RolePanel, BatchPanel, LogPanel,
│       │                       TaskPanel
│       └── worker.py           ExecutorWorker, start_executor_worker
├── tests/                      221 tests across 13 files
└── docs/
    ├── ARCHITECTURE.md         Actual architecture (read second)
    ├── CURRENT_STATE.md        This file (read first)
    ├── ROADMAP.md              Phased plan
    ├── DECISIONS.md            D-001 … D-018 with reasons
    └── reports/
        ├── SESSION_001_FOUNDATION.md
        └── SESSION_002_HERMES_EXECUTOR.md
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

---

## 5. Known limitations

Stated bluntly so nothing is over-claimed:

1. **One task per batch.** A second dispatch into the same batch is rejected.
   There is no planner, no orchestrator and no batch editor.
2. **Execution stops at `AUDITING_TASK`.** The Task Auditor does not exist, so
   nothing transitions out of it yet.
3. **No mid-prompt cancellation.** `supports_cancellation=False`; `cancel()`
   returns `False`. A stop requested during a prompt lets the run finish, records
   its real result, and is reported as `stop_requested=True`.
4. **Pause is boundary-only.** With one task there is no mid-run boundary, so a
   pause requested mid-prompt cannot suspend anything.
5. **`BatchState.tasks` holds only manually supplied tasks.** No planning step
   produces them.
6. **`CodexDriver` and `GenericCliDriver` remain placeholders.**
   `GenericCliDriver` still has no configurable command line.
7. **No streaming surface.** The CLI's deltas are consumed internally and never
   surfaced to callers.
8. **Output retention is bounded** — a 4000-character excerpt in the event
   payload; the full text lives on the returned `PromptResult`, and there is no
   artefact store yet.
9. **`Executor` pause/stop flags are in memory.** Persisted phase/task/batch state
   survives a crash; the stop *request* does not.
10. **No fix loop, no audit loop, no final batch audit.**
11. **Batch size is stored, not acted on.** Nothing is sequenced or planned.
12. **Provider/model are real per invocation**, but the stored role values are
    still operator-typed strings — no validation against a provider catalogue.
13. **`TaskStateRecord.audit_rounds` is persisted but never incremented** (no
    auditor yet). `attempts` is now incremented for real.
14. **Schema v2 has exactly one upgrade step.** No migration framework (by design,
    D-008).
15. **UI tests are offscreen only.** Native windowing behaviour is unverified.
16. **The live smoke covers one prompt + one resume + one model override.** Long
    prompts, multi-turn tool use and provider failure modes were not exercised live.
17. **No packaging.** No installer, no frozen `.exe`.

---

## 6. Files most likely relevant next

In the order a Session 003 (Task Auditor + capped fix loop) is likely to touch
them:

| File | Why |
|---|---|
| `src/encomm_pcc/core/executor.py` | Where the audit and fix dispatches belong; `_dispatch()` is the sequence to extend |
| `src/encomm_pcc/core/session_manager.py` | `persistent_per_batch` for the auditor; already complete |
| `src/encomm_pcc/drivers/hermes_cli.py` | The verdict parser will reuse the same stream-json contract |
| `src/encomm_pcc/drivers/hermes.py` | Resume is proven and available for per-batch auditor sessions |
| `src/encomm_pcc/domain/models.py` | `audit_rounds` and any verdict field on `TaskStateRecord` |
| `src/encomm_pcc/persistence/schema.sql` | Bump to v3 with a new `_upgrade()` step if a verdict column is needed |
| `src/encomm_pcc/ui/panels.py` | Task list / audit verdict display |
| `scripts/session_002_smoke.py` | The template for a Session 003 end-to-end smoke |
| `tests/test_executor.py` | Where the loop-cap and verdict tests belong |

---

## 7. Exact next recommended phase

**Session 003 — Task Auditor + a capped fix loop for a single task.**

1. Dispatch `TASK_AUDITOR` through the same executor, resolving its session
   policy with `SessionManager.decide()` (`persistent_per_batch`) and reusing the
   proven `--resume` path so one auditor session spans the batch.
2. Require a **structured verdict** (pass / fail / needs-fix) parsed from the
   auditor's answer and persisted — not free text.
3. Implement `AUDITING_TASK → FIX_REQUIRED → RUNNING_FIX → AUDITING_TASK` with a
   **hard round cap that exists from the first commit** (risk R4), escalating to
   `BLOCKED` when it is hit, and incrementing `attempts` / `audit_rounds` for real.
4. Extend the smoke with an auditor run; keep it outside the unit suite.
5. Update `ARCHITECTURE.md`, append ADRs, rewrite this file, and write
   `docs/reports/SESSION_003_*.md`.

**Do not start** with multi-task batches, orchestrator planning or the final batch
auditor — they depend on the single-task audit/fix loop being correct first. See
`ROADMAP.md`.