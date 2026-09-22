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

## Phase 3 — Multi-task batches + orchestrator ✅ DONE (Session 004)

**Goal:** a full batch of N tasks planned and run.

Delivered:

- **Real ORCHESTRATOR planning** through the generic role/driver
  architecture (`AgentRole.ORCHESTRATOR` → role config → `DriverRegistry` →
  `SessionManager`) — one planning call per batch, nothing orchestrator-
  specific in the batch logic.
- **Strict BatchPlan parser** (`core/plan_parser.py`): fails closed on
  malformed output; exact requested task count; contiguous indices; bounded
  strings/lists; no eval/exec/YAML.
- **Orchestrator read-only guard** (`core/repo_fingerprint.py`): HEAD +
  porcelain-status fingerprint before/after planning; a planning call that
  modified the workspace BLOCKS the plan (violation surfaced, never
  discarded). Verified offline with a real tmp git repo.
- **Durable Project Brief** on the batch row (survives restart — never model
  memory); **batch size 1..5** honoured exactly.
- **Deterministic autonomous runner** (`core/batch_runner.py`): PLAN → per-task
  BUILD → AUDIT → (fix loop) → next task → … → `READY_FOR_FINAL_AUDIT`; pause/
  stop at safe boundaries; crash recovery resumes from SQLite without rework.
- **Batch lifecycle**: `BatchStatus` gains `PLANNING` / `READY_FOR_FINAL_AUDIT` /
  `BLOCKED`; `BATCH_COMPLETE` is reachable ONLY from `FINAL_AUDIT_RUNNING`
  (structural guarantee — Session 003's direct `AUDITING_TASK → BATCH_COMPLETE`
  terminal is superseded by ADR D-025).
- **Session lifecycles proven**: every build/fix runs in a fresh Builder
  session; the Task Auditor reuses ONE session for the whole batch.
- **Schema v4**: `batch_plans` table (strict plan, Orchestrator session id,
  baseline, final phase, durable Batch Summary), task criteria/audit-focus
  columns, Project Brief on batches.
- **UI**: PROJECT BRIEF field, PLAN + START BATCH / RESUME BATCH, per-task
  progress + current action + real session readouts — off the UI thread.
- **334 passing tests** (was 280), including the full offline batch-runner and
  plan-parser matrices.
- **Real smoke** `scripts/session_004_multitask_smoke.py`: scratch git repo,
  BATCH SIZE = 4 — one real Orchestrator call → 4 fresh Builder calls → 4 Task
  Auditor calls sharing one session → `READY_FOR_FINAL_AUDIT`, re-read from
  SQLite. `--fake` mode proves every post-processing path before any real call.

Exit criteria met (all four):

- A 5-task batch runs to `READY_FOR_FINAL_AUDIT` unattended — **proven
  offline** (`test_five_task_batch_is_supported_offline`); the real smoke uses
  4 tasks per the brief's cost target.
- Interrupting mid-batch and restarting resumes from database state — proven
  offline (`test_restart_recovery_after_task_two_resumes_without_rework`).
- Session policy behaviour matches the brief for every role — fresh Builders,
  one auditor session per batch, Orchestrator `persistent_optional`.

---

## Phase 4 — Final auditor + batch completion ✅ DONE (Session 005)

**Goal:** a completed batch audit and a durable report.

Delivered:

- **Real FINAL_AUDITOR** through the generic role/driver architecture
  (`AgentRole.FINAL_AUDITOR` → role config (`same_as_orchestrator` honoured)
  → `DriverRegistry` → `SessionManager`) — no final-audit-specific engine
  code.
- **One-call contract (D-031):** the single Final Auditor response contains
  BOTH the cumulative verdict AND, on PASS, the next batch plan (exactly the
  operator-requested 4–5 tasks, default 5) — validated by the SAME strict
  plan rules as an Orchestrator plan.
- **Strict final-audit parser** (`core/final_audit_parser.py`): fails closed;
  envelope `<<<FINAL_AUDIT_START>>>…`; `json.loads` only; bounded; PASS
  with critical/high findings, PASS without next_batch, wrong task count,
  NEEDS_FIX/BLOCKED with next_batch — all rejected.
- **Read-only guard** (D-030): the repository is fingerprinted before/after
  the call; an auditor that modified the worktree BLOCKS the audit, files
  surfaced and never auto-discarded.
- **`FinalAuditPacket`**: deterministic prompt from durable facts — Project
  Brief, original plan, Batch Summary (evidence only), per-task contracts,
  session ids, heads, DO-NOT-EDIT instruction, inspect-actual-repo-and-tests
  requirement.
- **State machine made real**: `READY_FOR_FINAL_AUDIT → FINAL_AUDIT_RUNNING →
  BATCH_COMPLETE` on a strictly parsed PASS (a crash mid-audit recovers to
  RUNNING and requires a real re-run — never a silent green).
- **Schema v5** (D-032): durable Final Audit on `batch_plans` (verdict,
  findings, summary, auditor session, structured JSON) + the
  `pending_next_plans` handoff table (consumed exactly once).
- **START NEXT BATCH**: deterministic materialisation of the persisted plan
  into a new batch generation — zero AI calls; the operator controls the
  start; the completed batch remains queryable.
- **Operator states on failure** (D-033): final-audit NEEDS_FIX/BLOCKED
  persist findings and land in `BLOCKED` — no automatic global fix loop.
- **UI**: FINAL AUDIT section — batch/verdict state, resolved auditor,
  next-batch size (4–5), RUN FINAL AUDIT / VIEW NEXT TASKS / START NEXT
  BATCH, enabled only from legal persisted states.
- **367 passing tests** (was 334), including the 33-case final-audit matrix.
- **Real smoke** `scripts/session_005_final_audit_smoke.py`: `--fake` proves
  every post-processing path first; the real run uses exactly ONE Final
  Auditor call (PASS + 4 next tasks in one response, worktree clean,
  restart-safe handoff re-verified from SQLite).

Exit criteria met:

- `BATCH_COMPLETE` is reachable and persists a durable audit record (schema
  v5) — and the next plan is persisted without starting it.
- A failing final audit lands in an explicit, persisted operator state
  (findings kept); failure can never become COMPLETE.

---

## Phase 5 — Additional engines

**Goal:** prove the abstraction by adding engines without touching role logic.

Order (each is one class + one registry entry):

1. `CodexDriver` — ✅ DONE (Session 006): the expensive
   Orchestrator/FINAL_AUDITOR roles are switchable to Codex from the UI.
   Includes the generic session-discovery abstraction, the durable
   external-session binding (engine-switch-safe) and the real session
   selector (ADR D-034…D-038). Live-proven: new session + resume through the
   generic FINAL_AUDITOR path with exactly 2 model operations.
2. `ClaudeCodeDriver`
3. `OpenCodeDriver`
4. `OllamaDriver` (local models)
5. `KimiDriver`
6. `GenericCliDriver` gains a configurable argv field in role config

Exit criteria: adding each engine changes no role, UI or executor code — only
the driver module and `IMPLEMENTED_DRIVERS`. Any required change elsewhere is a
design bug to fix, not a workaround to accept. Session 006's only generic
change was capability-driven (`requires_profile` on `DriverCapabilities`),
which future engines inherit for free.

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
