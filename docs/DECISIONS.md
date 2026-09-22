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

---

## D-014 — The first real engine integration drives the installed Hermes CLI

**Date:** Session 002
**Status:** Accepted

**Context.** The foundation shipped three refusing placeholders. Session 002 had
to make exactly one engine real, without inventing syntax and without touching
Hermes configuration or profiles.

**Decision.** `HermesDriver` drives the **installed** CLI through
`SubprocessRunner`, using only behaviour read from the installed build
(v0.21.3): `hermes -p <profile> chat --query-file <file> --oneshot --quiet
--format stream-json --source tool [--in <dir>] [--resume <id>] [-m <model>]
[--provider <provider>]`. One process per prompt; the prompt travels in a file
(never in argv, so quotes/newlines/length cannot mangle it); stdout is parsed as
JSONL (`system/init`, `text`, `tool_use`, `tool_result`, terminal `result`).
Profile names are discovered read-only (`hermes profile list`, with an
identity-marker directory scan as fallback) and used as a **guard**: a profile
that discovery positively contradicts blocks the dispatch before anything starts.

**Reason.** The brief forbids guessing CLI syntax and forbids fabricating
capabilities. `--format stream-json` is the interface the CLI documents for CI
runners and orchestrators, and it carries the two facts that must never be
invented: the real session id and the CLI's own exit code.

**Consequence.** The CLI contract lives in one pure module
(`drivers/hermes_cli.py`) with its own tests, so argument construction and output
parsing are verified without a network. `--provider` without `--model` is refused
locally rather than by the engine. Codex and GenericCli are untouched.

---

## D-015 — Supervised processes get a filtered environment

**Date:** Session 002
**Status:** Accepted

**Context.** A control center can be launched from a shell (or an agent session)
whose environment carries the supervisor's own identity: `HERMES_SESSION_ID`,
spawn markers, an inherited `HERMES_INFERENCE_MODEL`, and provider credential
variables. Worse, `HERMES_KANBAN_TASK` silently rewrites the CLI's exit codes.

**Decision.** Every launched child receives a **filtered copy** of the
environment: `HERMES_*` and `PYTHONPATH`/`PYTHONHOME` are dropped, everything
else (PATH, SYSTEMROOT, home, temp) is preserved. The filter is applied at launch,
is unit-tested, and the resulting environment is never logged or persisted.

**Reason.** A supervised agent must not inherit the supervisor's session scope or
be silently reconfigured by an ambient variable, and the child's exit codes must
mean what they say. The interpreter path of the *supervisor* must not join the
child's import path either.

**Consequence.** The 2026-09-22 smoke run succeeded with the filtered environment,
proving the launcher works without those variables. Profile selection is explicit
(`-p`) rather than inherited, and a future engine needing a specific variable
extends the documented filter rather than bypassing it.

---

## D-016 — Schema v2: a task keeps its implementation prompt

**Date:** Session 002
**Status:** Accepted

**Context.** Session 001 left `BatchState.tasks` empty and the `tasks` table had
no place for the implementation prompt — the single most important part of a task
record. Durable truth lives in SQLite (D-002/D-004), so a task that cannot be
re-read cannot be re-dispatched, audited or reconciled after a restart.

**Decision.** Add `tasks.prompt` (`TEXT NOT NULL DEFAULT ''`) and
`TaskStateRecord.prompt`, and bump `SCHEMA_VERSION` to **2**. An existing v1
database is upgraded **in place** by one explicit, targeted `ALTER TABLE` in
`Database._upgrade()`; the version guard still refuses a newer schema.

**Reason.** The brief's rule is to avoid a schema change if the existing schema
suffices — it does not, because the alternative was to keep the prompt in memory
or to abuse `last_error`. D-008 anticipated exactly this moment; the smallest
honest change is preferable to a framework.

**Consequence.** This is the first in-place upgrade and still **not** a migration
framework: adding a version means adding the exact DDL and a test
(`test_a_v1_database_is_upgraded_in_place`). Future sessions must keep that
discipline.

---

## D-017 — The executor owns dispatch; the controller owns state

**Date:** Session 002
**Status:** Accepted

**Context.** D-007 left the controller as the owner of state with the executor
absent. Session 002 adds the executor, and the honesty question "did work really
start?" becomes answerable for the first time.

**Decision.** `Executor` (`core/executor.py`) is deterministic and Qt-free. It
never mutates state directly: phase changes go through
`PipelineController.transition()`, which validates against the declarative graph
and persists. `PipelineController.attach_executor()` tells the controller a real
dispatcher exists, so `request_start()` stops claiming that none does —
`ControlResult.executor_started` remains `False` (that call starts no process),
and the field that becomes `True` is `ExecutionReport.executor_started`, derived
from a **launch recorder** wrapped around the injected runner.

**Reason.** The transition graph must stay the single authority, and "a process
started" must be observed rather than inferred. A recorder around the only code
path that can spawn a process is the strongest cheap guarantee available.

**Consequence.** A blocked preflight is read-only: it changes no phase and starts
no process, and leaves the pipeline exactly where it was (unit-tested). Any future
component that wants to move the pipeline must call the controller, not the state
machine, and the flag cannot be set without a launch.

---

## D-018 — Honest capabilities and boundary-only control

**Date:** Session 002
**Status:** Accepted

**Context.** `DriverCapabilities` is the contract callers branch on (D-004), and
the brief forbids advertising anything unproven: resume, cancellation and
"implemented" are exactly the flags that would be tempting to overstate.

**Decision.** Capability flags are **evidence-gated**:
`HermesDriver.capabilities().implemented` mirrors `_LIVE_SMOKE_VERIFIED` and
`supports_resume` mirrors `_LIVE_RESUME_VERIFIED`; both were flipped only after a
real run proved them (session `20260922_172437_722edb`, exit code 0; resume
recontinued the same session). `supports_streaming` and `supports_cancellation`
are `False` because the adapter does not surface deltas and cannot cancel a
blocking child; `cancel()` returns `False`. Stop and pause are **boundary-only**:
a stop requested before dispatch prevents the process entirely, and a stop
requested during a prompt lets the run finish, records its real result, and is
reported as `stop_requested=True` on the report.

**Reason.** A capability flag that overstates reality is the same class of defect
as a fabricated `PromptResult`: a caller codes against it and the failure surfaces
far from the cause. The executor preflights on `implemented`, so an unverified
adapter is undispatched rather than mis-dispatched.

**Consequence.** Flipping either flag is a one-line change that must be justified
by evidence in a session report. A future driver that really can cancel mid-prompt
advertises it only alongside a real kill path in the process layer — and
`scripts/session_002_smoke.py` is the harness that re-proves the Hermes claims.

---

## D-019 — Strict structured audit verdicts from untrusted model output

**Date:** Session 003
**Status:** Accepted

**Context.** The Task Auditor must gate task completion, so its answer flows
into a state machine. If that answer were free text, the state machine would be
controlled by prose — and a malformed or hallucinated answer could become a
green light.

**Decision.** The audit result is a strict, machine-checkable contract
(`domain/audit.py`): `verdict` ∈ {`PASS`, `NEEDS_FIX`, `BLOCKED`}, `summary`,
`findings` (severity ∈ {critical, high, medium, low} + message + evidence),
`fix_prompt`. Model output is parsed by `core/verdict_parser.py` as **untrusted
input**: bounded size (200 000 chars raw, 50 findings, per-string caps), strict
`json.loads` only — **no eval/exec/YAML** — an exact verdict whitelist, and
fails closed. Semantic rules encoded in the parser: `PASS` must have an empty
`fix_prompt` and no critical/high findings; `NEEDS_FIX` **must** carry a
non-empty `fix_prompt`. Any parse failure raises `VerdictParseError` and the
executor marks the task `BLOCKED` — a malformed response can never become
`PASS`. The auditor prompt packet (`core/audit_packet.py`) tells the model to
emit the verdict inside exactly delimited markers.

**Reason.** The loop is only safe if the supervisor, not the model, decides
transitions, and only if a broken answer stops the loop loudly instead of
corrupting it.

**Consequence.** Verdicts are stored bounded (`tasks.verdict_json`, capped) and
re-parsed on reload. The parser is unit-tested for every failure case.

---

## D-020 — Capped single-task audit/fix loop; `AUDITING_TASK → BATCH_COMPLETE`

**Date:** Session 003
**Status:** Accepted

**Context.** Session 002 ended at `AUDITING_TASK` with no way out. The brief
requires a real fix loop with a hard round cap existing from the first commit.

**Decision.** `Executor.run_task_audit()` dispatches `TASK_AUDITOR` through the
generic role→config→driver→`SessionManager` path; `run_task_fix()` dispatches a
corrective `BUILDER` run. `MAX_AUDIT_ROUNDS = 3` with these exact semantics:
Audit 1 is the initial audit, Fix 1 → Audit 2, Fix 2 → Audit 3; a `NEEDS_FIX`
returned by round 3 escalates to `BLOCKED` and no fix is ever started beyond the
cap (both `run_task_audit` and `run_task_fix` enforce it). A successful PASS on
the single-task audit transitions `AUDITING_TASK → BATCH_COMPLETE` with the task
`APPROVED` and the batch `COMPLETE` — a new legal edge added to the state
machine (the batch is genuinely complete; the final-audit phase is a later
session). `attempts` increments per Builder unit (initial build and every fix);
`audit_rounds` increments per audit.

**Reason.** The cap must be structural, not habitual: an audit loop without a
cap is an unbounded money/energy loop (Session 002 risk R4). The transition to
`BATCH_COMPLETE` for a one-task batch is the honest terminal state.

**Consequence.** `next_task_action()` (task-state-driven) reports the
deterministic next step (`AUDIT`/`FIX`/`RE_AUDIT`/`COMPLETE`/`BLOCKED`) used by
the UI and by recovery. Cap exhaustion, malformed verdicts and auditor
`BLOCKED` verdicts all end in `BLOCKED`, never green.

---

## D-021 — Auditor session lifecycle: `persistent_per_batch` with restart restore

**Date:** Session 003
**Status:** Accepted

**Context.** The auditor must remember the batch across a fix (re-audit = same
session) while a new batch must start fresh, and the loop must survive a
restart without a live session.

**Decision.** The auditor's session policy stays `persistent_per_batch`
(`SessionManager.decide`), so the first audit of a batch is NEW and a re-audit
after a fix is REUSE (`--resume`). The real external auditor session id is
persisted on the task (`tasks.auditor_session_id`) and mirrored in the
`sessions` table. After a restart, `SessionManager.restore_session()` rebinds
the persisted id in memory (never contacting the engine) and the executor's
`_restore_auditor_session()` + `_ensure_work_phase()` recover the per-batch
decision and the phase. **Sessions remain bookkeeping, never the source of
truth** — durable truth is SQLite + workspace files (Session 001 invariant).

**Reason.** "Persist the real external auditor session id" is a Session 003
requirement, and re-auditing in a fresh auditor session would lose the batch
context the brief wants preserved.

**Consequence.** The Session 003 smoke proves `AUDITOR_INITIAL_SESSION_ID ==
AUDITOR_REAUDIT_SESSION_ID` while `FIX_BUILDER_SESSION_ID` is a distinct new
session (always_new). A `begin_new_batch()` increments the generation and makes
the old auditor session ineligible — per-batch, as specified.

---

## D-022 — Fixes always run in a brand-new Builder session

**Date:** Session 003
**Status:** Accepted

**Context.** Session 002 established `BUILDER = always_new`; the fix loop makes
that rule security-relevant: a fix built on the previous Builder conversation is
a fork of an untrusted implementation narrative.

**Decision.** `run_task_fix()` resolves the BUILDER role config and refuses to
proceed if the session decision is ever REUSE (`always_new` guarantees NEW), and
records the fix run's real external session id in `tasks.fix_session_id`. The
fix prompt (`core/audit_packet.render_fix_prompt`) is self-contained: original
task prompt + auditor findings + auditor `fix_prompt` + workspace boundary,
explicitly telling the Builder it has no memory of prior sessions.

**Reason.** The audit/fix loop is only meaningful if the correction is
deterministic and reproducible; the auditor's `fix_prompt` is the tested
interface between the two roles.

**Consequence.** `FIX_BUILDER_SESSION_ID != AUDITOR_INITIAL_SESSION_ID` is both
unit-tested and proven in the real smoke. The executor refuses a fix whose
session decision is REUSE.

---

## D-023 — Restart recovery restores the phase and decides, never auto-resumes

**Date:** Session 003
**Status:** Accepted

**Context.** A crash must not require model memory; the app must know whether
the next action is AUDIT, FIX, RE-AUDIT or BLOCKED. Session 002 persisted task
records but `load_pipeline_state` always returned the phase as `IDLE`.

**Decision.** Schema v3 persists the pipeline phase on the batch row
(`batches.phase`), written by `save_pipeline_state` and restored by
`load_pipeline_state`; `next_task_action()` is task-state-driven so the decision
survives even a phase-less legacy row, and `Executor._ensure_work_phase()`
walks legal edges from `IDLE` to the required work phase when a restored batch
is in flight. **Starting the application never starts an AI process**: recovery
only identifies the safe next action; the operator triggers it from the UI.

**Reason.** "The application knows whether it should audit, fix, re-audit, or
remain blocked" is a Session 003 pass criterion, and auto-resuming on launch is
explicitly forbidden.

**Consequence.** The migration path is `v2 → v3` (one targeted upgrade adding
six `tasks` columns and `batches.phase`). Recovery is covered by
`test_recovery_resumes_the_auditor_session_after_restart` and the real smoke's
STEP 8 reload.

---

## D-024 — The Session 003 smoke is scratch-only and costs at most three model runs

**Date:** Session 003
**Status:** Accepted

**Context.** The loop must be proven with a real engine, but a real engine is
expensive and dangerous if pointed at a production repository.

**Decision.** `scripts/session_003_audit_fix_smoke.py` builds a throwaway git
repo under the system temp dir with a deliberately defective `calculator.py`
(`add` returns `a - b`) and a deterministic plain-python test. The Auditor and
Builder prompts restrict all tool use to that scratch path. Maximum real model
runs: initial audit, fix, re-audit = **3**. A failure stops the script with the
child's own error text and is diagnosed, never brute-forced with retries. No
production repository, no Control Center source, no Hermes/LeanCTX config is
ever touched.

**Reason.** The brief demands real-loop proof without modelling an AI that
intentionally writes a bad implementation (the defect pre-exists the loop).

**Consequence.** The smoke asserts the session identities and the PASS verdict
from real engine output, re-reads the state from SQLite, and refuses to call
itself green on anything less.

---

## D-025 — `READY_FOR_FINAL_AUDIT` supersedes the Session-003 single-task terminal

**Date:** Session 004
**Status:** Accepted — **supersedes the terminal part of D-020**

**Context.** Session 003 closed a one-task batch with
`AUDITING_TASK → BATCH_COMPLETE` because the batch really was complete up to
the final-audit phase. Session 004 introduces the real batch lifecycle, and a
successful batch must stop at a point where the real Final Auditor (Session
005) takes over — not at a state that claims the pipeline is finished.

**Decision.** From now on the universal successful batch terminal before the
Final Auditor is **`READY_FOR_FINAL_AUDIT`** — never `BATCH_COMPLETE`.

- A passed task with more tasks remaining advances
  `AUDITING_TASK → RUNNING_TASK` (next task).
- A passed task with no tasks remaining advances
  `AUDITING_TASK → READY_FOR_FINAL_AUDIT`; the batch status becomes
  `READY_FOR_FINAL_AUDIT` and a durable Batch Summary is written.
- The state machine removes **both** `AUDITING_TASK → BATCH_COMPLETE` and
  `PLANNING_BATCH → BATCH_COMPLETE`. `BATCH_COMPLETE` is reachable ONLY from
  `FINAL_AUDIT_RUNNING` (a test asserts exactly one incoming edge).
  Structurally, no successful path can reach `BATCH_COMPLETE` without the
  Final Auditor.
- `BatchStatus` gains `PLANNING`, `READY_FOR_FINAL_AUDIT` and `BLOCKED` so the
  durable batch status matches the lifecycle.

**Reason.** The old optimistic terminal would let a batch claim completion
with no final audit; the new graph makes the Final Auditor structurally
required (brief §10, §32).

**Consequence.** The Session 003 smoke was updated to expect
`READY_FOR_FINAL_AUDIT`; the fix loop itself is unchanged and remains a
reusable component of the batch loop.

---

## D-026 — The strict BatchPlan parser fails closed on Orchestrator output

**Date:** Session 004
**Status:** Accepted

**Context.** The Orchestrator's plan flows into execution. If that answer were
free text, the supervisor would be controlled by prose — the same class of
risk D-019 closed for audit verdicts.

**Decision.** `core/plan_parser.py` treats Orchestrator output as **untrusted
input**: bounded raw size (200 000 chars), strict `json.loads` only (no
eval/exec/YAML), a delimited envelope
`<<<BATCH_PLAN_START>>> … <<<BATCH_PLAN_END>>>` with a balanced-braces
fallback, and these hard rules — **exactly** the requested task count (never
truncate, never fill), contiguous indices `1..N`, unique non-empty titles,
non-empty `implementation_prompt` / `acceptance_criteria` / `audit_focus`,
bounded strings and lists. Any violation raises `PlanParseError` and the
batch is BLOCKED with **no task materialised**. Nothing in the structure is
ever executed or shell-evaluated.

**Reason.** "A malformed plan must NEVER lead to execution" (brief §8); the
exact-count rule makes the UI's requested batch size authoritative.

**Consequence.** `_plan` maps every `PlanParseError` to a BLOCKED batch; the
offline matrix (valid 1/4/5-task plans, every mismatch/structure/bounds case)
pins the parser.

---

## D-027 — The Orchestrator is planning-only and guarded by a read-only fingerprint

**Date:** Session 004
**Status:** Accepted

**Context.** An Orchestrator that edits the repository while planning destroys
the audit trail and makes plans untrustworthy.

**Decision.** `Executor._plan` captures a **read-only repository fingerprint**
(HEAD + `status --porcelain` hash via `core/repo_fingerprint.py`) BEFORE the
planning call and verifies it AFTER. A difference (any file change, any new
HEAD) BLOCKS the plan: the parsed answer is discarded, **no task is
materialised**, the batch is BLOCKED and the violation is surfaced to the
operator. The modifications are never silently accepted and never
auto-reverted — the operator decides. Non-git workspaces make the guard
vacuous (documented, not guessed).

**Reason.** Planning-only behaviour is a Session 004 requirement, and honest
capabilities/gates are the project's stated discipline (D-018).

**Consequence.** The offline suite proves a worktree-modifying plan is BLOCKED
with the modified file left in place; the plan prompt itself instructs the
Orchestrator that it must not edit files.

---

## D-028 — The batch runner owns sequencing; AI outputs are data

**Date:** Session 004
**Status:** Accepted

**Context.** Session 002/003 proved the state machine must not be driven by
model output. Session 004 generalises that to the *whole batch*: with 4–5
tasks plus fix loops, a batch that waits for operator clicks between every
step is not a batch.

**Decision.** `core/batch_runner.py` (`BatchRunner.run_batch`) is a
deterministic loop: read `next_task_action()` from persisted task states, call
the matching executor method (plan/build/audit/fix), persist everything, and
repeat until one terminal batch outcome — `READY_FOR_FINAL_AUDIT`, `BLOCKED`,
`FAILED`, `STOPPED` or `PAUSED`. Pause and stop are boundary-safe: an in-flight
prompt always finishes and persists its result; the flag is then honoured and
cleared. `run_batch(resume=True)` is idempotent — it re-reads durable state and
continues at the first task that still needs work, so APPROVED tasks are never
re-run and a restart never contacts an AI provider. Run off the UI thread via
the executor worker.

**Reason.** "The state machine, not the AI model, owns sequencing. AI outputs
DATA. Supervisor decides transitions" (brief §12); no conjured "plan needed /
builder needed …" decision may live in chat memory (§17).

**Consequence.** The UI needs only **PLAN + START BATCH** and **RESUME BATCH**;
the manual TASK-panel buttons remain for debugging. `next_task_action` gained
`PLAN` and `BUILD` and is multi-task aware (first undone task in index order).

---

## D-029 — Schema v4: durable planning truth in `batch_plans`

**Date:** Session 004
**Status:** Accepted

**Context.** After a restart the app must know whether the next step is plan,
build, audit, fix, next task or READY_FOR_FINAL_AUDIT — from SQLite alone.
Session 003's schema v3 had no place for the plan, the Orchestrator session
id, the baseline, or per-task acceptance criteria.

**Decision.** Targeted in-place **v3 → v4** upgrade (one explicit `ALTER` set,
still no migration framework — D-008):

- `batches` += `project_brief` (the durable brief — never model memory) and
  `current_head`
- `tasks` += `acceptance_criteria` / `audit_focus` (JSON lists; the plan's
  contract travels with each task into the auditor packet)
- new `batch_plans` table (1:1 with a batch): strict plan JSON, Orchestrator
  session id, planning status/timestamp, the read-only baseline fingerprint,
  and — at the end — `final_phase`, `finalized_at` and the durable structured
  **Batch Summary** (`batch_summary_json`) that Session 005's Final Auditor
  consumes.

**Reason.** Durable truth lives in SQLite (D-002/D-004); the batch's planning
truth must survive the process that created it.

**Consequence.** `load_batch` attaches the plan row; `next_task_action` derives
the safe next step from task states; the offline suite proves a
partially-finished batch reloads and resumes with the same session identities.

---

## D-030 — The Final Auditor runs through the generic role path and is guarded read-only

**Date:** Session 005
**Status:** Accepted

**Context.** `BATCH_COMPLETE` was reachable only from `FINAL_AUDIT_RUNNING`
(structural guarantee since D-025), but no real Final Auditor existed. The
brief forbids engine-specific audit logic and requires the final model to
inspect the ACTUAL repository, not approve the Batch Summary.

**Decision.** `Executor.run_final_audit()` resolves
`AgentRole.FINAL_AUDITOR` → role config (honouring
`same_as_orchestrator`) → driver registry → `SessionManager` — zero
final-audit-specific engine code, exactly like the Orchestrator (D-027).
The repository is fingerprinted read-only BEFORE and AFTER the call; ANY
worktree modification BLOCKS the final audit (violation surfaced, files
never auto-discarded, nothing persisted from the violating answer). The
packet (`FinalAuditPacket`) is assembled from durable facts only (batch
row, plan record, task rows, heads) and explicitly instructs: inspect the
actual repository and cumulative diff, RUN the deterministic tests, read
complete failure output, treat the Batch Summary as evidence only, and DO
NOT EDIT FILES.

**Consequence.** A later Codex/Claude/Kimi driver fills the role by
configuration alone. `FINAL_AUDIT_RUNNING` is also accepted as input —
restart recovery after a crash mid-audit — but a PASS then still requires
a REAL new model call; an interrupted audit never silently becomes green.

---

## D-031 — Strict final-audit parser fails closed; one call returns verdict AND next plan

**Date:** Session 005
**Status:** Accepted

**Context.** The Final Auditor's answer gates batch completion AND, on
pass, produces the next batch. Two expensive model calls (audit, then
planning) contradict the cost contract; a lenient parser would let a
malformed answer become a green batch.

**Decision.** ONE call must return BOTH: the cumulative verdict and, on
PASS only, the next `BatchPlan` (exactly `next_batch_size` tasks, 4 or 5;
default 5). `core/final_audit_parser.py` treats the answer as untrusted:
bounded raw input, `json.loads` only (no eval/exec/YAML), envelope
`<<<FINAL_AUDIT_START>>>…<<<FINAL_AUDIT_END>>>` with a balanced-brace
fallback, boolean `batch_assessment.tests_verified/diff_verified`
required. The nested next plan is validated by the SAME strict
`plan_parser` rules as an Orchestrator plan (D-026) — no second weaker
parser. PASS with an unresolved critical/high finding is rejected;
NEEDS_FIX requires findings and forbids next_batch; BLOCKED forbids
next_batch. Any violation raises `FinalAuditParseError` and the batch is
BLOCKED — a malformed result can never become PASS.

**Consequence.** `REAL_MODEL_OPERATIONS = 1` per completed batch cycle.
The exact next-batch count is operator-authoritative.

---

## D-032 — Schema v5: durable Final Audit + `pending_next_plans` handoff; the operator starts the next batch

**Date:** Session 005
**Status:** Accepted

**Context.** After a PASS the app must show, across restarts: the
completed batch (with verdict/findings/auditor session), the generated
next plan READY, and no auto-started work. `BATCH_COMPLETE` is terminal
in the phase graph, so the next generation needs a durable, non-phase
home.

**Decision.** Targeted in-place **v4 → v5** upgrade (D-008 discipline):
`batch_plans` gains the Final Audit columns (`final_verdict`,
`final_summary`, `final_findings_json`, `final_audit_json`,
`final_auditor_session_id`, `final_audited_at`, `final_next_plan_id`);
a new `pending_next_plans` table holds the generated next plan (plan
JSON + requested size) until `start_next_batch()` consumes it
(`consumed_at`/`consumed_batch_id` recorded — a plan can never be
materialised twice). `start_next_batch()` is deterministic and makes NO
model call: it leaves `BATCH_COMPLETE` over its legal IDLE edge, calls
`request_start(N)` (a NEW batch generation), materialises the persisted
tasks as PENDING with their criteria/audit focus, and stops — the
operator then uses the normal START controls. The completed batch row,
its tasks and its Final Audit remain queryable forever.

**Consequence.** Closing the app after a PASS and reopening still shows
the next plan READY; START NEXT BATCH after a restart uses the persisted
plan without contacting the Orchestrator or the Final Auditor.

---

## D-033 — Final-audit failures land in explicit operator states, never an automatic global fix loop

**Date:** Session 005
**Status:** Accepted

**Context.** A NEEDS_FIX from the Final Auditor means the WHOLE batch has
defects; an uncontrolled batch-wide fix loop would burn model budget and
the phase graph has no safe automatic return path to task fixing.

**Decision.** On NEEDS_FIX the findings, summary and structured JSON are
persisted and the pipeline moves to `BLOCKED` with the verdict recorded
(`next_task_action` reports BLOCKED — never COMPLETE — because the
BLOCKED/FAILED phase now dominates all-APPROVED task states in the
action derivation). On BLOCKED the same persistence happens with verdict
BLOCKED. Both states require operator/supervisor handling; no automatic
fix, no automatic re-audit.

**Consequence.** Failure can never become COMPLETE; every non-green
final-audit outcome is loud, durable and human-actionable.

---

## D-034 — The Codex CLI contract is a pure module; stdin prompts; least privilege

**Date:** Session 006
**Status:** Accepted

**Context.** Session 006 had to make a real Codex adapter without inventing
syntax. The installed build (`codex-cli 0.154.0`) was inspected first
(`--help` surfaces for `exec`, `exec resume`, `resume`), and one live usage
error was converted into contract knowledge (see D-035's evidence rule).

**Decision.** `drivers/codex_cli.py` holds the entire verified contract as
pure data/functions: `codex exec --json -s <sandbox> -C <ws> [-m <model>] -`
for a fresh prompt and `codex exec resume <SESSION_ID> --json [-m <model>] -`
for a resume — **the prompt always travels on stdin** (the trailing `-`), so
Windows argv length limits and shell quoting are unreachable. Only
`read-only` (default) and `workspace-write` sandboxes are constructible;
`danger-full-access`, `--dangerously-bypass-approvals-and-sandbox`,
`--dangerously-bypass-hook-trust`, `--ephemeral` and `--ignore-user-config`
are refused by construction. The child receives the same filtered
environment discipline as Hermes (D-015), extended by `CODEX_HOME` and
`ENCOMM_PCC_*` drops so a supervisor override can never redirect the child's
state or data directories.

**Consequence.** Argument construction and JSONL parsing are unit-testable
with no network and no engine. The bypass surface is unreachable from role
configuration. `codex resume` (interactive TUI) and `--last` are never used.

---

## D-035 — Codex resume takes no `-s`/`-C`; the installed CLI is the only authority

**Date:** Session 006
**Status:** Accepted

**Context.** The first live resume attempt (2026-09-23) exited 2 in 0.2 s:
the CLI rejected `-s` with `unexpected argument '-s' found` — before any
model work. The preserved raw stderr identified the cause: `exec resume`
does not accept sandbox/cwd overrides; a resumed thread **inherits** the
original session's sandbox and working root.

**Decision.** `build_exec_resume_argv` constructs only flags the installed
`exec resume` help actually lists (`--json`, `-m`, `--skip-git-repo-check`,
config overrides). The help text captured at discovery time was treated as
provisional; the live exit-2 evidence is what fixed the contract, exactly per
the evidence-retry discipline: preserve the scratch state, fix local code
from the raw evidence, re-run only the failed operation.

**Consequence.** The retried resume ran the Session 005 final-audit pipeline
through Codex (exit 0, 137 s, same thread id re-reported). The parser also
gained the live protocol shapes (`thread.started` / `item.completed` /
`turn.completed` top-level records) as pinned regression tests.

---

## D-036 — Read-only Codex session discovery behind a driver-neutral interface

**Date:** Session 006
**Status:** Accepted

**Context.** The installed CLI has no machine-readable session-list
subcommand (`codex exec --help`: resume/fork/review only). The UI needed a
real existing-session selector without Codex filesystem knowledge leaking
into widgets.

**Decision.** A generic contract (`drivers/session_discovery.py`:
`ExternalSessionDescriptor`, `SessionDiscoveryResult`, `SessionDiscoverer`
protocol) plus a Codex-specific implementation
(`drivers/codex_discovery.py`) that READ-ONLY scans
`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl` first-line
`session_meta` records (real id + cwd), titles from
`session_index.jsonl` when present, never inventing labels. Missing,
malformed, locked and schema-drifted state fail soft (skipped + message);
results are deduplicated (one session owns several rollout files), bounded,
newest-first, and workspace-matched first (case-insensitive). No credential
material is ever read; nothing is ever written.

**Consequence.** Future drivers (Claude/OpenCode/…) implement the same
protocol without new UI code. Discovery makes zero model calls, verified by
tests where any launch would raise.

---

## D-037 — External-session binding is durable config state, invalidated by engine switch

**Date:** Session 006
**Status:** Accepted

**Context.** A session selected in the UI must survive a restart, must never
be reused after switching the role's engine, and binding must never contact
the engine.

**Decision.** `ExternalSessionBinding` (driver id + full external id + title
+ workspace + timestamp) lives in `AgentRoleConfig.extra` — it round-trips
through the existing `role_configs.extra_json` with **no schema change**.
`config.external_session_binding()` returns `None` when the binding's driver
does not match the currently configured engine, so a codex-bound session can
never leak into a hermes-configured role (and re-selecting the original
engine restores it). The executor's `_seed_binding_session()` re-seeds the
binding into `SessionManager` before every session decision, making the
REUSE decision resume the bound real id — after a restart too. Binding is
pure bookkeeping; durable pipeline truth remains SQLite + repository state.

**Consequence.** NEW SESSION (`clear_external_session`) deletes the binding
and the next real execution creates a fresh engine session. Engine switching
needs no extra code — the driver-mismatch rule already isolates stale
bindings.

---

## D-038 — The selector is a capability-driven UI surface; `requires_profile` joins the capability contract

**Date:** Session 006
**Status:** Accepted

**Context.** The executor preflight required a Hermes profile for every
role, and the session combo showed only the role's live session id. Codex is
configured with a workspace + model, not a Hermes profile — and the brief
forbids engine-specific branches in role/preflight logic.

**Decision.** `DriverCapabilities.requires_profile` (default `True`) gates
both the profile-required check and Hermes profile discovery in
`_preflight`; Codex declares `requires_profile=False`. The `RolePanel`
session field becomes a real selector for roles whose engine implements
`discover_sessions` and is implemented: "New session (next run creates one)"
+ discovered rows (full id as item data, bounded display label), REFRESH
SESSIONS (read-only discovery, zero model calls), and NEW SESSION (clears
the binding). Panels emit signals; the window resolves them through the
controller — panels never touch drivers. FINAL_AUDITOR keeps
`Same as Orchestrator` semantics unchanged (it resolves the engine — and
therefore the whole session surface — from the Orchestrator).

**Consequence.** Switching an expensive role between Hermes and Codex stays
configuration-only; the selector appears or disables itself from
capabilities alone, with no Codex names in role/executor code.
