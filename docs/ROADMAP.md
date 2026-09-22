# Roadmap — ENCOMM Pipeline Control Center

Incremental phases. Each phase must leave the repository in a state a brand-new
session can pick up from `docs/CURRENT_STATE.md` alone.

**Rule for every phase:** documentation and a session report are part of the
phase, not a follow-up. A phase that is not documented did not happen.

---

## Phase 0 — Foundation ✅ DONE (Session 001)

**Goal:** a launchable application with the real architecture underneath, and
no fake automation.

Delivered:

- Local repo bootstrapped on `main`, pushed to GitHub.
- PySide6 desktop app that launches with the required UI sections.
- Four independent roles, engine-agnostic by construction.
- `BaseDriver` abstraction + registry + three refusing placeholders.
- Session policy engine enforcing the brief's four rules.
- SQLite schema v1 (7 tables) with round-trip tests.
- Declarative pipeline phase state machine with a validator.
- 144 passing tests, including offscreen UI tests.
- `ARCHITECTURE.md`, `CURRENT_STATE.md`, `ROADMAP.md`, `DECISIONS.md`,
  `reports/SESSION_001_FOUNDATION.md`.

Exit criteria met: app launches, roles exist, drivers abstracted, SQLite works,
tests pass, docs and report exist, no secrets committed.

---

## Phase 1 — Executor skeleton + first real driver ✅ DONE (Session 002)

**Goal:** one task, end to end, with real subprocess execution and honest
reporting.

Delivered:

- **Task materialisation.** `Executor.materialise_task()` persists a real
  `TaskStateRecord` (including its implementation prompt) through the existing
  `tasks` table; schema bumped to **v2** with one targeted in-place upgrade.
- **`core/executor.py`.** Deterministic dispatch: read-only phase gate and
  preflight, `IDLE → PLANNING_BATCH → RUNNING_TASK → AUDITING_TASK`, boundary-only
  pause/stop, no UI-thread work.
- **`HermesDriver` implemented** over `SubprocessRunner`: real sessions, real exit
  codes, real stdout captured as `--format stream-json`, capability flags gated on
  live proof.
- **Honesty invariant.** `ControlResult.executor_started` stays `False` (a Start
  starts no process); `ExecutionReport.executor_started` becomes `True` only when
  a launch recorder saw a real `ProcessSpec`. A blocked preflight changes nothing.
- **UI:** a TASK section (driver availability, discovered Hermes profiles, task
  input, dispatch, live task state, real result/failure) plus a worker thread.
- 221 passing tests, including a real-driver failure-propagation test.

Exit criteria met:

- A batch of 1 real task ran to `AUDITING_TASK` and stopped there — proven live
  (session `20260922_172437_722edb`, exit code 0, answer
  `ENCOMM_PCC_HERMES_SMOKE_OK`).
- A non-zero child exit code produces a `FAILED` task, not a silent success
  (unit-tested for exit codes 2 and 3).
- `PromptResult` never carries `simulated=True` for real work.
- Tests cover failure propagation and restart-from-database.
- **Pause at a task boundary** is implemented as a pre-dispatch flag: with a single
  task there is no mid-run boundary, and mid-prompt suspension is deliberately not
  attempted (see D-018). Multi-task boundary pausing arrives with Phase 3.

---

## Phase 2 — Task auditor + fix loop ✅ DONE (Session 003)

**Goal:** the audit/fix loop closes for a single task.

Delivered:

- **Real Task Auditor.** `TASK_AUDITOR` driven by its own engine config and
  `persistent_per_batch` session policy, resolved through
  `SessionManager.decide()` and the same generic role/driver path as the
  Builder — no auditor-specific engine path.
- **Strict structured verdicts** (`domain/audit.py` +
  `core/verdict_parser.py`): PASS / NEEDS_FIX / BLOCKED with findings and a
  deterministic `fix_prompt`; parser treats model output as untrusted, is
  bounded and fails closed — malformed output can never become PASS.
- **`FIX_REQUIRED → RUNNING_FIX → AUDITING_TASK` loop with a hard round cap**
  (`MAX_AUDIT_ROUNDS = 3`) and escalation to `BLOCKED` when the cap is hit,
  with `attempts` and `audit_rounds` incremented for real. No infinite loop is
  reachable by construction.
- **Session isolation:** fixes run in brand-new Builder sessions; re-audits
  resume the same auditor session (proven offline and in the real smoke).
- **Restart recovery:** `batches.phase` persisted; state/verdict/session ids
  reload from SQLite; `next_task_action()` decides the safe next step without
  auto-running anything.
- **UI:** the TASK panel shows the next action (AUDIT/FIX/RE-AUDIT/COMPLETE/
  BLOCKED), verdict, audit round, session readouts, and Run-Auditor / Run-fix
  buttons.
- **280 passing tests** (was 221), including the full failure-case matrix for
  the parser and the loop.
- **Real end-to-end smoke** `scripts/session_003_audit_fix_smoke.py`: a
  deliberately defective scratch git repo → real auditor NEEDS_FIX → real
  fix in a NEW Builder session → deterministic test passes → same auditor
  session re-audits → PASS → task APPROVED, pipeline BATCH_COMPLETE.

Exit criteria met:

- A deliberately failing task is fixed and re-audited to a pass (real smoke).
- The round cap is enforced and observable in the event log (unit-tested).
- No infinite loop is reachable by construction (cap tests).
- Session IDs prove isolation/reuse semantics (real smoke STEP 7).
- State and verdict survive SQLite reload (STEP 8).

---

## Phase 3 — Multi-task batches + orchestrator

**Goal:** a full batch of N tasks planned and run.

Scope:

- `ORCHESTRATOR` plans a batch from a workspace/project brief.
- Batch size from the UI is honoured; tasks are sequenced and persisted.
- Per-task progress and batch status surface live in the UI.
- `PLANNING_BATCH → RUNNING_TASK … → READY_FOR_FINAL_AUDIT` for a whole batch.

Exit criteria:

- A 5-task batch runs to `READY_FOR_FINAL_AUDIT` unattended.
- Interrupting mid-batch and restarting resumes from database state.
- Session policy behaviour matches the brief for every role.

---

## Phase 4 — Final auditor + batch completion

**Goal:** a completed batch audit and a durable report.

Scope:

- `FINAL_AUDITOR` runs with its `configurable` session policy and the
  "same as orchestrator" option honoured end to end.
- `FINAL_AUDIT_RUNNING → BATCH_COMPLETE`, or back to `FIX_REQUIRED` on failure.
- A written batch report artefact under `docs/reports/` (or a configured path).

Exit criteria:

- `BATCH_COMPLETE` is reachable and produces a report file.
- A failing final audit re-enters the fix loop correctly.

---

## Phase 5 — Additional engines

**Goal:** prove the abstraction by adding engines without touching role logic.

Order (each is one class + one registry entry):

1. `ClaudeCodeDriver`
2. `OpenCodeDriver`
3. `OllamaDriver` (local models)
4. `KimiDriver`
5. `GenericCliDriver` gains a configurable argv field in role config

Exit criteria: adding each engine changes no role, UI or executor code — only
the driver module and `IMPLEMENTED_DRIVERS`. Any required change elsewhere is a
design bug to fix, not a workaround to accept.

---

## Phase 6 — Operational hardening

- Packaging (PyInstaller or equivalent), versioned releases.
- Schema migrations, once a released schema must change in place.
- Config export/import; run history browsing.
- Bounded retention for `app_events`.
- Recovery from crash mid-batch, driven purely by persisted state.
- Optional: MCP surface for shaped shell output across long sessions
  (see the `encomm-leanctx` skill's `OPTIONAL_MCP_CAPABILITY` note).

---

## Deferred / explicitly out of scope

- Web frontend, Electron, Docker, cloud backend — forbidden by the brief.
- Multi-user or remote operation.
- Any autonomous run that cannot be paused at a task boundary.
- Anything that requires session continuity as the source of truth.
