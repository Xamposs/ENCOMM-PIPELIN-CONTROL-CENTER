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

`0.5.0` — one-call Final Audit + next-batch handoff. Session 005.

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
| Automated tests | **Works** | 334 passed, 0 failed (280 at Session 003, 18 files) |
| **Real ORCHESTRATOR planning** | **Works — Session 004** | `plan_batch()` through role config → driver registry → `SessionManager`; one planning call per batch; strict plan parser fails closed; exact task count honoured |
| **Orchestrator read-only guard** | **Works** | `core/repo_fingerprint.py`: HEAD + porcelain-status fingerprint before/after planning; a planning call that modified the worktree BLOCKS the plan (violation surfaced, never discarded) |
| **Multi-task autonomous runner** | **Works** | `core/batch_runner.py`: PLAN → per-task BUILD → AUDIT → fix loop → next task → READY_FOR_FINAL_AUDIT; boundary-safe pause/resume/stop; idempotent resume from SQLite |
| **Shared auditor, fresh builders** | **Works — proven** | ONE Task Auditor session reused for the whole batch; every build and fix in a BRAND-NEW Builder session (offline matrix + smoke) |
| **READY_FOR_FINAL_AUDIT terminal** | **Works** | `BATCH_COMPLETE` reachable ONLY from `FINAL_AUDIT_RUNNING` (structural graph guarantee; Session-003 terminal superseded — ADR D-025) |
| **Batch Summary** | **Works** | Durable structured summary written at READY_FOR_FINAL_AUDIT (plan row) for the Session 005 Final Auditor |
| **Real FINAL_AUDITOR — one call** | **Works — Session 005** | `run_final_audit()` resolves `AgentRole.FINAL_AUDITOR` → role config (`same_as_orchestrator` honoured) → driver registry → `SessionManager`; ONE call returns the cumulative verdict AND the next batch plan |
| **Strict final-audit parsing** | **Works — fails closed** | `core/final_audit_parser.py`: envelope `<<<FINAL_AUDIT_START>>>…`, `json.loads` only, bounded; PASS requires a next plan of EXACTLY the requested size validated by the SAME rules as an Orchestrator plan; PASS+critical/high, NEEDS_FIX/BLOCKED with next_batch all rejected |
| **Final-audit read-only guard** | **Works** | repository fingerprint before/after; an auditor that modified the worktree BLOCKS the audit (files surfaced, never auto-discarded, nothing persisted) |
| **BATCH_COMPLETE reachable** | **Works — Session 005** | `READY_FOR_FINAL_AUDIT → FINAL_AUDIT_RUNNING → BATCH_COMPLETE` on a strictly parsed PASS; verdict/findings/summary/auditor session/structured JSON persisted (schema v5) |
| **Next-batch handoff** | **Works** | PASS persists the plan in `pending_next_plans`; START NEXT BATCH materialises it deterministically (new batch generation, PENDING tasks, criteria/focus) with **zero AI calls**; plan consumed exactly once; nothing auto-starts |
| **Restart-safe handoff** | **Works** | READY_FOR_FINAL_AUDIT, FINAL_AUDIT_RUNNING (recovery requires a real re-run), completed audit and the pending next plan all survive restart; UI re-enables buttons from persisted state |
| `CodexDriver`, `GenericCliDriver` | **Placeholder** | Still raise `DriverNotImplementedError`; `implemented=False` |
| Real Codex adapter / engine switching UI | **Not implemented** | Deliberate — Session 006 |

### Verified at the end of Session 003

- `python -m pytest` → `280 passed`.
- Real smoke `python scripts/session_003_audit_fix_smoke.py --profile
  encomm-pipeline-control-center` proves: initial audit **NEEDS_FIX** → fix in
  a brand-new Builder session → deterministic scratch test passes → **SAME**
  auditor session re-audits → **PASS** → task `APPROVED`, pipeline
  `READY_FOR_FINAL_AUDIT` (since Session 004, `BATCH_COMPLETE` is reserved for
  the Final Auditor), state re-read from SQLite. Session ids, verdicts and
  wall-clock details are in `docs/reports/SESSION_003_AUDITOR_FIX_LOOP.md`.
- Session 002's `session_002_smoke.py` still passes (real Builder path intact).

### Verified at the end of Session 004

- `python -m pytest` → **334 passed, 0 failed**.
- `python scripts/session_004_multitask_smoke.py --fake` → **SMOKE PASSED**
  (2.5 s): every post-processing path proven offline before any real call.
- Real smoke (see `docs/reports/SESSION_004_ORCHESTRATOR_MULTITASK_BATCH.md`):
  one real Orchestrator call → exactly 4 planned tasks → 4 fresh Builder
  sessions → 4 Task Auditor calls in the SAME session → batch
  `READY_FOR_FINAL_AUDIT`, no path to `BATCH_COMPLETE`, reloaded from SQLite
  with the durable Batch Summary.
- The Session 003 fix loop still passes inside a batch (offline:
  `test_needs_fix_on_middle_task_uses_the_existing_fix_loop`; live: Session 003
  smoke, updated terminal).

### Verified at the end of Session 005

- `python -m pytest` → **367 passed, 0 failed** (334 + the 33-test final-audit
  matrix in `tests/test_final_audit.py`).
- `python scripts/session_005_final_audit_smoke.py --fake` → **SMOKE PASSED**
  (~2.5 s): every post-processing path proven offline before the real call.
- Real smoke (see
  `docs/reports/SESSION_005_FINAL_AUDIT_NEXT_BATCH.md`): a scratch repo
  pre-seeded as a completed 4-task batch → **ONE real Final Auditor call**
  (session `20260922_233757_2effcf`, zai / glm-5.3-flash, ≈360 s, 6 tool
  calls) → strict FINAL PASS + exactly 4 next tasks in the SAME response →
  all four scratch tests still exit 0, worktree clean → batch
  `BATCH_COMPLETE`, verdict+findings+next plan reloaded from SQLite →
  START NEXT BATCH materialised 4 PENDING tasks with zero additional model
  calls, the completed batch preserved.
- Session 004's `--fake` smoke still passes (the multi-task path is intact).

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
│   ├── session_003_audit_fix_smoke.py  Real audit/fix loop smoke (Session 003)
│   └── session_004_multitask_smoke.py  Real orchestrated batch smoke (Session 004; --fake offline mode)
├── src/encomm_pcc/
│   ├── __init__.py             __version__ = "0.5.0"
│   ├── app.py                  run(), build_controller(), attach_default_executor(),
│   │                           restore_state(), discover_hermes_profiles()
│   ├── domain/
│   │   ├── enums.py            AgentRole, SessionPolicy, PipelinePhase,
│   │   │                       TaskState, BatchStatus, EventLevel
│   │   ├── audit.py            AuditVerdict, FindingSeverity,
│   │   │                       AuditFinding, AuditVerdictResult
│   │   ├── batch_plan.py       PlannedTask, BatchPlan, BatchPlanRecord,
│   │   │                       bounds, build_batch_summary
│   │   ├── final_audit.py      (new) FinalVerdict, FinalAuditPacket,
│   │   │                       FinalAuditResult
│   │   ├── models.py           WorkspaceConfig, AgentRoleConfig,
│   │   │                       TaskStateRecord (+criteria/focus/verdict/session),
│   │   │                       BatchState (+project_brief/current_head/plan),
│   │   │                       PipelineState
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
│   │   ├── config.py           AppPaths, batch-size bounds (1..5), placeholders
│   │   ├── events.py           EventLog, LogRecord, NullEventLog
│   │   ├── session_manager.py  decide_session_action, SessionManager,
│   │   │                       restore_session (restart recovery)
│   │   ├── hermes_profiles.py  Read-only Hermes profile discovery
│   │   ├── verdict_parser.py   strict, fails-closed verdict parser
│   │   ├── plan_parser.py      strict, fails-closed BatchPlan parser
│   │   ├── plan_packet.py      deterministic Orchestrator planning prompt
│   │   ├── repo_fingerprint.py read-only git guard for planning + final audit
│   │   ├── final_audit_packet.py  (new) deterministic Final Auditor prompt
│   │   ├── final_audit_parser.py  (new) strict, fails-closed final-audit parser
│   │   ├── audit_packet.py     AuditPacket + fix-prompt builders (+criteria)
│   │   ├── executor.py         TaskSpec, ExecutionOutcome/Report, PlanReport,
│   │   │                       FinalAuditOutcome/Report, StartNextBatchReport,
│   │   │                       Executor (plan_batch / run_task_build /
│   │   │                       run_task_audit / run_task_fix / run_final_audit /
│   │   │                       start_next_batch, next_task_action,
│   │   │                       MAX_AUDIT_ROUNDS)
│   │   ├── batch_runner.py     BatchRunner, BatchRunReport, StepRecord
│   │   └── controller.py       PipelineController, ControlResult
│   ├── persistence/
│   │   ├── schema.sql          Schema v5 — final audit + pending_next_plans
│   │   └── database.py         Database (v1→…→v5 targeted upgrades)
│   ├── ui/
│   │   ├── main_window.py      MainWindow (+ audit/fix/final-audit worker wiring)
│   │   ├── panels.py           WorkspacePanel, RolePanel, BatchPanel,
│   │   │                       FinalAuditPanel, LogPanel, TaskPanel
│   │   └── worker.py           ExecutorWorker (dispatch/audit/fix/batch/
│   │                           final_audit actions)
├── tests/                      367 tests across 19 files
└── docs/
    ├── ARCHITECTURE.md         Actual architecture (read second)
    ├── CURRENT_STATE.md        This file (read first)
    ├── ROADMAP.md              Phased plan
    ├── DECISIONS.md            D-001 … D-033 with reasons
    └── reports/
        ├── SESSION_001_FOUNDATION.md
        ├── SESSION_002_HERMES_EXECUTOR.md
        ├── SESSION_003_AUDITOR_FIX_LOOP.md
        ├── SESSION_004_ORCHESTRATOR_MULTITASK_BATCH.md
        └── SESSION_005_FINAL_AUDIT_NEXT_BATCH.md
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
| D-025 | `READY_FOR_FINAL_AUDIT` supersedes the Session-003 single-task terminal (`BATCH_COMPLETE` only via the Final Auditor) |
| D-026 | Strict BatchPlan parser fails closed on Orchestrator output; exact task count |
| D-027 | Orchestrator is planning-only; read-only fingerprint guard blocks a worktree-modifying plan |
| D-028 | The batch runner owns sequencing; AI outputs are data; boundary-safe pause/stop; idempotent resume |
| D-029 | Schema v4: `batch_plans` durable planning truth + per-task criteria/audit focus + Project Brief |
| D-030 | The Final Auditor runs through the generic role path and is guarded read-only |
| D-031 | Strict final-audit parser fails closed; ONE call returns verdict AND next plan |
| D-032 | Schema v5: durable Final Audit + `pending_next_plans`; the operator starts the next batch |
| D-033 | Final-audit failures land in explicit operator states, never an automatic global fix loop |

---

## 5. Known limitations

Stated bluntly so nothing is over-claimed:

1. **Real live proof is a 4-task next plan** (the smoke requests 4); the
   5-task next plan is proven offline (parser + START NEXT BATCH matrix).
2. **`CodexDriver`/`GenericCliDriver` remain placeholders.** The FINAL_AUDITOR
   works through the generic path today with Hermes; swapping engines is
   configuration-only, and Session 006 adds the real Codex adapter plus the
   UI for switching the expensive roles to it.
3. **Pause/stop are boundary-only** (a running prompt finishes and persists its
   result first). No mid-prompt cancellation (`supports_cancellation=False`).
   This includes a running final-audit call.
4. **Planning/audit guards are vacuous on non-git workspaces** (documented,
   not guessed): the read-only fingerprints need a git repository to compare.
5. **A final-audit NEEDS_FIX/BLOCKED lands in `BLOCKED`** with findings
   persisted — resolving it (targeted fixes, revert, or restart of the
   batch) is an operator decision; no automatic batch-wide fix loop exists.
6. **`run_batch`/`run_final_audit` run synchronously in a worker thread**;
   further concurrency (e.g. parallel batches) is deliberately out of scope.
7. **A `--resume` failure fails the run** (provider-side session loss is
   surfaced, never papered over).
8. **UI tests are offscreen only.**
9. **No packaging.**
10. **The real CodexDriver and the Claude/OpenCode/Ollama/Kimi adapters do
    not exist yet** — Session 006+.

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

**Session 006 — the real Codex adapter + engine switching (Phase 5).**

1. Implement a real `CodexDriver` (evidence-gated capabilities, same
   `BaseDriver` contract) and register it; the Orchestrator and
   FINAL_AUDITOR roles can then be switched to Codex **from the UI** —
   configuration only, no role/executor changes.
2. Keep Hermes as the Builder/Task Auditor workhorse; prove role/engine
   separation live by running the Final Auditor through Codex against a
   scratch batch.
3. `GenericCliDriver` gains its configurable argv (the last Phase 5 item)
   or Claude/OpenCode follow; operational hardening (Phase 6) after that.

**Do not start** Session 006 until this session's final-audit path has been
independently reviewed. Automatic batch chaining, packaging, and the
Claude/OpenCode/Ollama/Kimi adapters beyond Codex remain strictly out of
scope. See `ROADMAP.md`.
