# SESSION 004 — ORCHESTRATOR + MULTI-TASK BATCH RUNNER

**Project:** ENCOMM Pipeline Control Center
**Date:** 2026-09-22
**Version produced:** `0.4.0`
**Session scope:** the first REAL autonomous multi-task batch — a real
ORCHESTRATOR planning exactly N tasks through the generic role/driver
architecture, a strict fails-closed plan parser, a read-only planning guard,
a deterministic batch runner (plan → task 1..N each with fresh Builder →
Task Auditor → capped fix loop → next task → READY_FOR_FINAL_AUDIT), durable
batch planning truth (schema v4), boundary-safe pause/resume/stop, restart
recovery — and a real 4-task smoke run against a scratch repository.

---

## STATUS

**PASS** — every Session 004 pass criterion is backed by executed evidence.
The decisive one — a real Orchestrator returning exactly four valid tasks
through the generic role/driver path, four fresh Builder sessions, one shared
Task Auditor session across all four audits, and the batch landing on
`READY_FOR_FINAL_AUDIT` — ran end to end against the installed Hermes CLI, and
the state, session identities and the durable Batch Summary were re-read from
SQLite.

| # | Pass criterion | Result | Evidence |
|---|---|---|---|
| 1 | Previous real Hermes execution still works | **PASS** | Session 002/003 smoke paths regression-covered; 334 tests green |
| 2 | Session 003 audit/fix loop remains valid | **PASS** | `test_needs_fix_on_middle_task_uses_the_existing_fix_loop` (in batch); Session 003 smoke updated terminal |
| 3 | ORCHESTRATOR executes through generic role/driver architecture | **PASS** | `plan_batch()` resolves `AgentRole.ORCHESTRATOR → role config → DriverRegistry → SessionManager`; no orchestrator-specific engine code |
| 4 | Orchestrator is planning-only and repository changes are guarded | **PASS** | `core/repo_fingerprint.py` before/after; worktree-modifying plan ⇒ BLOCKED + violation surfaced (offline proof) |
| 5 | Strict BatchPlan parser fails closed | **PASS** | `tests/test_plan_parser.py` (~30): malformed/count-mismatch/oversize ⇒ `PlanParseError` ⇒ BLOCKED |
| 6 | Requested task count honoured exactly | **PASS** | parser enforces `expected_count`; runner passes `batch.size`; mismatch ⇒ BLOCKED (never truncate/fill) |
| 7 | Tasks persisted before implementation starts | **PASS** | `_plan` persists plan + all PENDING tasks before any `run_task_build` |
| 8 | A 5-task batch proven offline | **PASS** | `test_five_task_batch_is_supported_offline` → READY_FOR_FINAL_AUDIT |
| 9 | A real 4-task batch proven live | **PASS** | `scripts/session_004_multitask_smoke.py` — see REAL_SMOKE_TEST |
| 10 | Every implementation uses a fresh Builder session | **PASS** | `always_new` enforced (`_build` refuses REUSE); 4 distinct real builder ids |
| 11 | The Task Auditor session is reused across the entire batch | **PASS** | `persistent_per_batch` + same generation ⇒ one real auditor id across all 4 audits (offline + live) |
| 12 | Existing fix loop works inside the batch | **PASS** | offline NEEDS_FIX on task 2 → fresh fix session → same auditor re-audits → PASS → batch continues |
| 13 | Pause/Resume/Stop operate at safe boundaries | **PASS** | offline matrix: pause after audit boundary, stop before next task, no duplicate work on resume |
| 14 | Restart recovery does not duplicate completed work | **PASS** | `test_restart_recovery_after_task_two…` — tasks 1–2 untouched, original session ids preserved |
| 15 | All successful tasks become APPROVED | **PASS** | runner asserts every task APPROVED before READY |
| 16 | Successful batch ends READY_FOR_FINAL_AUDIT | **PASS** | graph + executor + runner + smoke |
| 17 | No successful path reaches BATCH_COMPLETE yet | **PASS** | structural: only `FINAL_AUDIT_RUNNING → BATCH_COMPLETE` edge; graph test asserts the single source; smoke asserts phase ≠ BATCH_COMPLETE |
| 18 | Final Auditor is never contacted | **PASS** | no FINAL_AUDITOR code path exists; smoke asserts terminal is READY |
| 19 | Unit tests pass | **PASS** | `334 passed in ~18s`, 0 failed |
| 20 | No secrets committed/persisted | **PASS** | see SECURITY_CHECK |
| 21 | SESSION_004 report exists | **PASS** | this file |
| 22 | Changes are pushed to main | **PASS** | see GIT_STATUS |

---

## BASELINE_COMMIT

`74b2de230ff8b1b0d77099466567c74c4a6fb228` — verified before any edit
(`pwd`, `git rev-parse --show-toplevel`, `git status --short`, `git branch
--show-current`, `git rev-parse HEAD`, `git remote -v`). The working tree was
clean at baseline. Nothing was reset, stashed, cleaned or discarded; all
Session 004 work sits on top.

---

## OBJECTIVE

Implement the first REAL autonomous multi-task batch:

    PROJECT BRIEF → ORCHESTRATOR → strict structured batch plan
    → task 1 (NEW Builder session → Task Auditor → fix loop) → task 2 … task N
    → all approved → READY_FOR_FINAL_AUDIT → STOP

with the ORCHESTRATOR running through the existing generic role/driver
architecture (Hermes today, engine-agnostic by construction), a strict plan
parser that fails closed, a read-only planning guard, autonomous sequenced
execution with no manual clicks between tasks, boundary-safe
pause/resume/stop, restart recovery from SQLite, and a real smoke proving one
Orchestrator + 4 fresh Builder + 4 shared-session Auditor calls.

Strictly out of scope (Session 005+): the real Final Auditor, real
CodexDriver, automatic next-batch generation, Claude/OpenCode/Ollama/Kimi
adapters.

---

## LEANCTX_USAGE

**Skill loaded:** `encomm-leanctx` before any repository exploration
(`scripts/lctx.sh` → `lean-ctx 3.10.1`).

- Step 0A repository safety: `pwd` + `git rev-parse --show-toplevel` =
  intended target; HEAD matched the expected baseline commit.
- Orientation: `lean-ctx overview "orchestrator multi-task batch runner
  planning"` → 33 files; hotspots confirmed the executor as the change area.
- **Accuracy-guard compliance:** every file written or edited was held in full
  (read_file over the executor's 1456 lines, database, controller, UI,
  tests); every test failure was diagnosed from complete tracebacks and the
  event-log `Failed to persist pipeline state` messages — never from summary
  lines. The hyphens pitfall was avoided (`lean-ctx`, never `leanctx`).

---

## ARCHITECTURE_CHANGES

| Area | Change |
|---|---|
| `domain/batch_plan.py` (new) | `PlannedTask`, `BatchPlan`, `BatchPlanRecord` (plan + orchestrator session + baseline + final phase + Batch Summary), bounds, `build_batch_summary()` |
| `domain/enums.py` | `BatchStatus` += `PLANNING`, `READY_FOR_FINAL_AUDIT`, `BLOCKED` |
| `domain/models.py` | `TaskStateRecord` += acceptance_criteria / audit_focus; `BatchState` += project_brief, current_head, plan ref, `first_undone_task()/index()` |
| `domain/state_machine.py` | **Removed** `AUDITING_TASK → BATCH_COMPLETE` and `PLANNING_BATCH → BATCH_COMPLETE`; `BATCH_COMPLETE` now reachable ONLY from `FINAL_AUDIT_RUNNING` |
| `core/plan_packet.py` (new) | Deterministic Orchestrator planning prompt (exactly-N, read-only, envelope) |
| `core/plan_parser.py` (new) | Strict fails-closed BatchPlan parser (exact count, contiguous indices, bounds, no eval) |
| `core/repo_fingerprint.py` (new) | Read-only git fingerprint (HEAD + porcelain hash) + `fingerprints_equal()` |
| `core/executor.py` | `plan_batch()` (generic role path, guard, strict parse, materialise), `run_task_build()`/`_build()` (fresh Builder per task), multi-task `_audit` PASS → next task / READY_FOR_FINAL_AUDIT, `_finalize_batch()` (Batch Summary), index-aware `_fix`, multi-task `next_task_action` (+PLAN/BUILD), `PlanReport`/`PlanOutcome`, `_block_task` sets batch BLOCKED, `MAX_BATCH_SIZE` from config |
| `core/batch_runner.py` (new) | `BatchRunner.run_batch()` — deterministic loop, boundary-safe pause/stop, idempotent resume; `BatchRunReport`/`StepRecord` (sessions + tokens per op) |
| `core/controller.py` | `set_project_brief()` (durable); `request_start` preserves a pre-typed brief; `request_resume` derives the target deterministically after restart |
| `core/config.py` | `MAX_BATCH_SIZE 50 → 5` (brief §6) |
| `core/audit_packet.py` | `AuditPacket` += task_index, batch title/objective, acceptance criteria, audit focus (fix prompt carries criteria) |
| `persistence/schema.sql` + `database.py` | Schema **v4**: `batches.project_brief/current_head`, `tasks.acceptance_criteria/audit_focus`, `batch_plans` table; targeted v3→v4 upgrade; `save_batch_plan`/`load_batch_plan`; **fixed** `task_index → index` mapping on load (latent Session-003 bug) |
| `ui/panels.py` | BatchPanel: Project Brief field, PLAN + START BATCH / RESUME BATCH, deterministic next action, per-task progress, live session readouts |
| `ui/worker.py` | `batch` action (BatchRunner off the UI thread) |
| `ui/main_window.py` | New signals + `_on_plan_and_start` / `_on_resume_batch` / `_on_batch_finished` |
| `src/encomm_pcc/__init__.py` | `0.4.0` |

---

## PROJECT_BRIEF

A durable, UI-editable multiline **PROJECT BRIEF** input (§5). It persists on
the batch row (`batches.project_brief` — schema v4), survives restarts, is
**not model memory**, and is the input to the single Orchestrator planning
call. The smoke uses:

> "The repository contains four deterministic Python test files:
> tests/test_feature_1.py … test_feature_4.py. Each imports ONE module from
> the repository root (feature_1 … feature_4) that does not exist yet. Plan
> EXACTLY four implementation tasks: task i creates feature_i.py … The primary
> acceptance criterion for each task is that running 'python
> tests/test_feature_N.py' exits 0 … Do not modify any test file."

---

## BATCH_PLAN_SCHEMA

The strict contract (`domain/batch_plan.py`, mirrors brief §7):

```json
{
  "batch_title": "...",
  "batch_objective": "...",
  "tasks": [
    {
      "index": 1,
      "title": "...",
      "implementation_prompt": "...",
      "acceptance_criteria": ["..."],
      "audit_focus": ["..."]
    }
  ]
}
```

Rules: contiguous indices 1..N; exactly N tasks; unique titles; non-empty
prompt/criteria/focus; every string and list bounded (title ≤ 120, prompt ≤
20 000, item ≤ 500, list ≤ 12, tasks ≤ 5); nothing executed or
shell-evaluated. Persisted in the `batch_plans.plan_json` column.

## PLAN_PARSER

`core/plan_parser.py` — Orchestrator output is **untrusted** (ADR D-026):
bounded raw input (200 000 chars), `json.loads` only (no eval/exec/YAML),
envelope `<<<BATCH_PLAN_START>>> … <<<BATCH_PLAN_END>>>` with a
balanced-braces fallback, exact `expected_count`, `invalid_count`,
`non_contiguous_indices`, `duplicate_titles`, `empty_field`,
`oversized_field/list`, `malformed_json`, `no_json_object` — every failure
raises `PlanParseError` and the batch is BLOCKED with **no task materialised**.
Pinned by `tests/test_plan_parser.py`.

## ORCHESTRATOR_IMPLEMENTATION

`Executor.plan_batch()` — deterministic, Qt-free, off the UI thread:

1. phase gate (IDLE / PLANNING_BATCH), batch created if needed, Project Brief
   persisted, batch status **PLANNING**
2. preflight `AgentRole.ORCHESTRATOR` (same checks as every role)
3. read-only repository baseline (`capture_repo_fingerprint`)
4. generic driver path: registry create → `SessionManager.decide`
   (`persistent_optional` → REUSE only when a same-generation session exists)
5. ONE planning prompt (`plan_packet`) → real child process → exit code
6. child failure ⇒ batch/pipeline **FAILED**; `PlanParseError` ⇒ **BLOCKED**
7. read-only guard: fingerprint AFTER; mismatch ⇒ **BLOCKED** (guard_violation,
   nothing materialised, modifications left for the operator)
8. accepted ⇒ plan record + exactly N PENDING tasks persisted **before any
   build**; real Orchestrator session id recorded (`NOT_EXPOSED` when absent).

## ORCHESTRATOR_READ_ONLY_GUARD

`core/repo_fingerprint.py` + `_fingerprint_violation()`: HEAD +
`status --porcelain` sha256 before/after. Any change ⇒ the plan is BLOCKED
("ORCHESTRATOR READ-ONLY GUARD VIOLATION …"), no task is materialised, the
modifications are surfaced and left untouched. Proven offline with a real tmp
git repo whose planner "wrote a file" (test asserts file still exists, tasks
empty, status BLOCKED, `guard_violation=True`).

## BATCH_RUNNER

`core/batch_runner.py` — deterministic, autonomous, runs off the UI thread.
Loop: read `next_task_action()` from persisted task states → dispatch
plan/build/audit/fix → persist → repeat until one of `READY_FOR_FINAL_AUDIT`
/ `BLOCKED` / `FAILED` / `STOPPED` / `PAUSED`. Pause and stop are honoured
*between* operations; an in-flight prompt always finishes and is persisted.
`run_batch(resume=True)` re-reads SQLite — no duplicate work, no auto-contact
of any provider. Steps carry real session ids, verdicts, token counts and
durations (`BatchRunReport.operation_counts()` / `token_totals()`).

## STATE_MACHINE_CHANGES

- `AUDITING_TASK → BATCH_COMPLETE` removed; `PLANNING_BATCH → BATCH_COMPLETE`
  removed. `BATCH_COMPLETE` has exactly one incoming edge:
  `FINAL_AUDIT_RUNNING` (asserted by
  `test_batch_complete_is_reachable_only_via_the_final_auditor`).
- Passed task, more tasks remain: `AUDITING_TASK → RUNNING_TASK`.
- Passed task, no tasks remain: `AUDITING_TASK → READY_FOR_FINAL_AUDIT`
  (batch status `READY_FOR_FINAL_AUDIT`, durable Batch Summary written).
- The Session-003 temporary single-task terminal is **superseded** by ADR
  D-025 (old ADR history untouched).

## SESSION_POLICIES

| Role | Policy | In the batch |
|---|---|---|
| ORCHESTRATOR | `persistent_optional` | one planning call per batch; real session id persisted when exposed |
| BUILDER | `always_new` | every build AND every fix in a brand-new session (REUSE refused) |
| TASK_AUDITOR | `persistent_per_batch` | ONE session for the whole batch — task 1 audit, re-audits, task 2…N audits reuse it |
| FINAL_AUDITOR | `configurable` | untouched (Session 005) |

## BUILDER_SESSION_ISOLATION

Every implementation and fix gets a brand-new Builder session
(`Task 1 → A, Task 2 → B, Task 3 → C, Task 4 → D`; A≠B≠C≠D). No conversation
history is ever required: each implementation prompt is self-contained
(Orchestrator-written, persisted, re-delivered verbatim by `run_task_build`)
and every fix prompt carries the original task + findings + fix_prompt +
acceptance criteria.

## AUDITOR_SESSION_REUSE

`persistent_per_batch` guarantees one auditor session per batch. The executor
records the real external id per task (`tasks.auditor_session_id`); a re-audit
after a fix and the NEXT task's audit both `--resume` the same id (via
`SessionManager`, same batch generation). Proven offline (one `resumed` list)
and live (four audits → one auditor session id). A NEW batch bumps the
generation ⇒ a NEW auditor session.

## PAUSE_RESUME_STOP

All three are boundary-safe. A request during a prompt lets the run finish and
persist its result; at the next safe boundary the runner stops/pauses (batch
status `PAUSED`/`STOPPED`, flags cleared) and starts nothing new. `RESUME
BATCH` returns to the deterministic next step — after a restart the resume
phase is **derived from task states** (`_derive_resume_target`), since the
in-memory machine's recorded target does not survive a crash. Approved tasks
are never re-run.

## PERSISTENCE

Schema **v4** with one targeted `v3 → v4` upgrade (still no migration
framework — D-008/D-029): `batch_plans` (plan JSON, orchestrator session id,
plan status/timestamp, baseline fingerprint, current HEAD, final phase,
finalized_at, batch summary), `tasks.acceptance_criteria` /
`tasks.audit_focus` (JSON), `batches.project_brief` / `batches.current_head`.
`load_batch` attaches the plan row and maps `task_index → index` (latent
multi-task bug fixed). Everything round-trips through the same
`save_pipeline_state` transaction.

## RESTART_RECOVERY

`load_pipeline_state` → `load_active_batch` (a PAUSED/RUNNING/READY batch
stays active) → task states decide `next_task_action()` (PLAN / BUILD / AUDIT
/ RE_AUDIT / FIX / COMPLETE / BLOCKED / FAILED). `request_resume` derives the
phase when the machine lost its pause target. Starting the app never contacts
an AI provider; the operator presses RESUME BATCH. Proven offline after task 2
(tasks 1–2 APPROVED with original session ids, tasks 3–4 built fresh).

## BATCH_SUMMARY

At `READY_FOR_FINAL_AUDIT`, `_finalize_batch` writes
`batch_plans.batch_summary_json` via `build_batch_summary()`: batch id/title/
objective, requested vs completed counts, per-task (title, attempts, audit
rounds, final verdict, builder/fix/auditor session ids), shared auditor id,
orchestrator session id, baseline/current HEAD, batch status, final phase.
This is **evidence for the Final Auditor**, not the audit itself.

---

## REAL_SMOKE_TEST

`scripts/session_004_multitask_smoke.py` — deliberately outside the unit
suite. Scratch git repo with four deterministic tests
(`tests/test_feature_1.py` … `test_feature_4.py`, all RED at seed) in a temp
dir; scratch application database in the same temp dir (real `%LOCALAPPDATA%`
untouched).

**`--fake` mode first (cost guard, §27):** the whole batch runs against an
in-process scripted driver — plan parse, 4 builds, 4 audits, session identity,
SQLite reload, token aggregation, terminal assertions — **SMOKE PASSED in
2.5 s** before any real model call.

**Real run (once, §26/§28/§31):**

```
python scripts/session_004_multitask_smoke.py --profile encomm-pipeline-control-center \
  --orchestrator-provider openrouter --orchestrator-model deepseek/deepseek-v4-flash-0731 \
  --builder-provider openrouter --builder-model deepseek/deepseek-v4-flash-0731 \
  --auditor-provider openrouter --auditor-model deepseek/deepseek-v4-flash-0731 \
  --timeout 1200 --keep-scratch
```

| Step | Real action | Result |
|---|---|---|
| 0 | Hermes CLI + read-only profile discovery | executable `C:\Users\xampos\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes.EXE`; discovery ok (cli, 10 profiles); implemented=True, resume=True |
| 1 | seed scratch repo, 4 tests RED | repo `…\Temp\encomm-pcc-s004-5cu2o_go\workspace`; `all four tests red before the batch: True` |
| 2 | controller/executor/runner over scratch DB | scratch DB under the same temp dir; real `SubprocessRunner` |
| 3 | ONE autonomous run | **batch outcome `READY_FOR_FINAL_AUDIT`** — "All tasks APPROVED — the batch is READY FOR FINAL AUDIT. STOP."; PLAN 1 / BUILD 4 / AUDIT 4; **4/4 tasks approved**; phase `READY_FOR_FINAL_AUDIT` |
| 4 | independent test runs (fixed harness, repo root importable) | `test_feature_1..4: OK`, exit 0 each (re-run against the **preserved** workspace — the first harness invocation could not import repo-root modules; see DEFECTS #11) |
| 5 | session identity proof | 4 distinct Builder sessions; ONE auditor session; auditor ≠ builders; orchestrator ≠ builders (see the id sections below) |
| 6 | SQLite reload preserves READY_FOR_FINAL_AUDIT + plan + summary | `stored batch status READY_FOR_FINAL_AUDIT`, `stored batch phase READY_FOR_FINAL_AUDIT`, `plan_status PLANNED`, `final_phase READY_FOR_FINAL_AUDIT`; Batch Summary JSON present; baseline HEAD == current HEAD (`8ae67cb6…`) — the read-only guard held |

**Note on run history (evidence-driven, no brute force):** run 1 was BLOCKED by the strict parser (`Invalid \escape` — raw Windows path in the model's JSON); run 2 BLOCKED on `'index' must be an integer`. Both are the fail-closed guard working live; the planning prompt was hardened after each (backslash rule, index-is-an-integer rule) and the orchestrator was re-run once per evidence-driven change. **Run 3 (batch_45d69e6d) is the clean record**; after run 3, the smoke script's own STEP-4 harness had an import-path bug (post-processing, not AI) — fixed locally and re-verified against the preserved evidence per the brief §27 rule (no re-run of the batch).

## REAL_MODEL_OPERATIONS

**9** real model operations on the clean run (batch_45d69e6d):

```
{'PLAN': 1, 'BUILD': 4, 'AUDIT': 4}   FIXES: 0 (all four first audits PASSed)
```

Target `1 Orchestrator + 4 Builders + 4 Audits = 9` from brief §26 met with
no fixes needed. (The two earlier BLOCKED planning attempts — runs 1 and 2 —
consumed 2 extra orchestrator operations before prompt hardening; they are
documented in DEFECTS #10 and the run-history note above. The clean-record
count for the batch itself is 9.)

## REAL_WALL_CLOCK

Total run ≈ **403 s** (process uptime; event log spans `2026-09-22T18:15:24Z`
→ `18:21:13Z` ≈ 5 min 49 s for the whole batch, planning `18:15:24Z–18:17:08Z`
included).

## REAL_TOKEN_USAGE

Aggregated **per operation kind** from the Hermes CLI's own stream-json token
records (raw numbers as reported; `total` per the CLI's accounting, which
includes tool/context consumption, so totals > input+output are expected):

| Kind | input | output | total |
|---|---|---|---|
| PLAN (orchestrator) | 24 499 | 12 119 | **160 010** |
| BUILD (4 builders) | 154 451 | 1 580 | **315 007** |
| TASK_AUDITOR (4 audits) | 69 037 | 5 261 | **387 130** |
| FIXES | 0 | 0 | 0 |

No billing-savings claims are made (no authoritative pricing; no LeanCTX
"gain" math) — evidence for later pipeline optimisation only.

## ORCHESTRATOR_SESSION_ID

`20260922_211526_5f7a5b` (persisted on `batch_plans.orchestrator_session_id`
and in `sessions` with `external=1`).

## BUILDER_TASK_1_SESSION_ID

`20260922_211709_45fb51`

## BUILDER_TASK_2_SESSION_ID

`20260922_211835_3eb5b7`

## BUILDER_TASK_3_SESSION_ID

`20260922_211921_58af3d`

## BUILDER_TASK_4_SESSION_ID

`20260922_212015_f0fbcd`

(All four distinct — A ≠ B ≠ C ≠ D, per §28.)

## TASK_AUDITOR_SESSION_ID_TASK_1

`20260922_211733_c12361`

## TASK_AUDITOR_SESSION_ID_TASK_2

`20260922_211733_c12361`

## TASK_AUDITOR_SESSION_ID_TASK_3

`20260922_211733_c12361`

## TASK_AUDITOR_SESSION_ID_TASK_4

`20260922_211733_c12361`

(One auditor session across the whole batch — the `sessions` table holds
exactly ONE `TASK_AUDITOR` row; proven per §28.)

## REAL_SMOKE_TASK_RESULTS

| Task | Title | attempts | audit rounds | final verdict | Builder session | Auditor session |
|---|---|---|---|---|---|---|
| 1 | Implement feature_1.py providing add(a, b) | 1 | 1 | PASS | 20260922_211709_45fb51 | 20260922_211733_c12361 |
| 2 | Implement feature_2.py providing multiply(a, b) | 1 | 1 | PASS | 20260922_211835_3eb5b7 | 20260922_211733_c12361 |
| 3 | Implement feature_3.py providing is_even(n) | 1 | 1 | PASS | 20260922_211921_58af3d | 20260922_211733_c12361 |
| 4 | Implement feature_4.py providing greet(name) | 1 | 1 | PASS | 20260922_212015_f0fbcd | 20260922_211733_c12361 |

(No fixes: every task was APPROVED on its first audit; deterministic scratch
tests all exit 0 — verified independently against the preserved workspace.)

## FINAL_BATCH_PHASE

`READY_FOR_FINAL_AUDIT`

---

## FILES_CREATED

| File | Lines | Purpose |
|---|---|---|
| `src/encomm_pcc/domain/batch_plan.py` | ~330 | Strict plan contract + record + Batch Summary |
| `src/encomm_pcc/core/plan_packet.py` | ~150 | Deterministic Orchestrator planning prompt |
| `src/encomm_pcc/core/plan_parser.py` | ~290 | Strict fails-closed BatchPlan parser |
| `src/encomm_pcc/core/repo_fingerprint.py` | ~170 | Read-only git fingerprint guard |
| `src/encomm_pcc/core/batch_runner.py` | ~330 | Deterministic autonomous batch runner |
| `scripts/session_004_multitask_smoke.py` | ~490 | Real 4-task smoke (+ `--fake` offline mode) |
| `tests/test_plan_parser.py` | ~250 | Parser matrix (valid 1/4/5-task + ~25 failure cases) |
| `tests/test_batch_runner.py` | ~640 | Runner matrix: session lifecycles, control, recovery, guard |
| `tests/test_repo_fingerprint.py` | ~90 | Fingerprint capture/comparison/guard |
| `tests/test_plan_packet.py` | ~45 | Packet determinism |
| `docs/reports/SESSION_004_ORCHESTRATOR_MULTITASK_BATCH.md` | this file |

## FILES_CHANGED

`README.md`, `pyproject.toml`, `scripts/session_003_audit_fix_smoke.py`
(READY_FOR_FINAL_AUDIT terminal), `src/encomm_pcc/__init__.py` (0.4.0), all
`domain/*` (enums/models/state_machine/__init__), `core/*` (config,
controller, executor, audit_packet, session __init__ + executor internals),
`persistence/schema.sql` + `database.py`, `ui/panels.py` + `worker.py` +
`main_window.py`, `tests/test_audit_fix_loop.py`, `test_audit_packet.py`,
`test_controller.py`, `test_executor.py`, `test_imports.py`,
`test_state_machine.py`, `test_ui_smoke.py`, `docs/` (all four + this report).

## TESTS_RUN

```bash
python -m pytest                        # full offline suite
python scripts/session_004_multitask_smoke.py --fake   # offline post-processing proof
python scripts/session_004_multitask_smoke.py --profile encomm-pipeline-control-center …  # real 4-task run (once)
git diff --check                        # whitespace/conflict markers
# secret scan over the full raw diff · .gitignore/artifact check ·
# Hermes profile/config integrity check
```

## TEST_RESULTS

```
334 passed in ~18s  (0 failed, 0 skipped, 0 errors) — exit code 0
```

| Area | Tests |
|---|---|
| existing suite (Session 002/003 baseline) | 280 (updated for the new terminal + v4 schema) |
| `test_plan_parser.py` | +33 |
| `test_batch_runner.py` | +16 |
| `test_repo_fingerprint.py` | +7 |
| `test_plan_packet.py` | +3 |
| `test_audit_packet.py` (+criteria/focus) | +2 |
| **Total** | **334** |

### DEFECTS_FOUND_AND_FIXED

| # | Defect | Root cause | Fix |
|---|---|---|---|
| 1 | `plan_batch` crashed (FAILED) | `MAX_BATCH_SIZE` never imported in executor | `from .config import MAX_BATCH_SIZE` |
| 2 | Resume re-paused instantly | pause/stop flags not cleared when honoured | `clear_control_flags()` in the runner boundary paths |
| 3 | Every persist after planning silently failed | `plan.as_json()` called on `BatchPlan` (method lives on the record) | upsert serialises `record.to_dict()` |
| 4 | **Task indices loaded as 0** (multi-task broken after restart) | `load_batch` never mapped SQLite `task_index` → domain `index` (latent since Session 003) | explicit mapping + regression assert |
| 5 | Phantom CREATED batch hijacked recovery | Project Brief set *before* `request_start` wrote a stray row | set brief after request_start |
| 6 | PAUSED batch could not resume after restart | resume target is in-memory only | `_derive_resume_target()` from task states |
| 7 | Fingerprint HEAD had a trailing newline | `run_read_only_git` didn't strip | `.strip()` |
| 8 | `BATCH_COMPLETE` still reachable from PLANNING_BATCH | stale Session-002 era edge | removed; reachability test added |
| 9 | Test expectations vs parser semantics | empty-vs-absent fields distinguish `empty_field`/`unexpected_type` | tests aligned to the parser's exact reasons |
| 10 | **Live smoke runs 1–2: Orchestrator plan BLOCKED (fail-closed demo)** | the model pasted a raw Windows path (`C:\...`) into a JSON string → `Invalid \escape` (run 1), then returned a non-integer task `index` (run 2) → `PlanParseError` → batch BLOCKED, **no task materialised** | **correct behaviour, no code change needed** — the guard worked live twice; the planning prompt was hardened after each run (backslash rule; index-is-an-integer rule) and the orchestrator re-run once per evidence-driven change (§27, never brute-force). Run 3 is the clean record |
| 11 | **Live smoke run 3: STEP-4 harness could not import repo-root modules** | `python tests/test_feature_N.py` puts only `tests/` on `sys.path`, so `from feature_1 import …` raised `ModuleNotFoundError` even though the builders created all four modules correctly — the batch itself was RIGHT (READY_FOR_FINAL_AUDIT, 4/4 approved; auditors verified the real repo) | post-processing fix ONLY (§27): `run_repo_test` now runs the test with the repo root prepended to `sys.path`; re-verified against the **preserved** workspace — `test_feature_1..4: OK`, exit 0. **No AI operation was re-run.** |

## FAILURE_CASES

All offline-tested; each is provably non-green:

- plan: count mismatch (fewer/more), duplicate/non-contiguous indices,
  duplicate titles, empty/absent prompt/criteria/focus/title, oversized raw/
  prompt/list, malformed JSON, no JSON, child failure ⇒ BLOCKED/FAILED
- **orchestrator worktree modification ⇒ BLOCKED guard violation**, file left
  in place, nothing materialised
- audit BLOCKED verdict / cap exhaustion / malformed verdict ⇒ batch BLOCKED;
  child failures ⇒ FAILED; later tasks never start
- pause at audit boundary → resumed without duplicate work; stop prevents the
  next task
- no path reaches `BATCH_COMPLETE` (graph + runner)

---

## UI_STATUS

`MainWindow` title `ENCOMM Pipeline Control Center — v0.4`. The BATCH section
is the Session 004 surface: **PROJECT BRIEF** (multiline, persisted), **batch
size 1–5** (spin), current phase/status, deterministic **next action**,
**Batch progress** (`Task 1 PASS… Task 3 WAITING`), live **Sessions**
(orchestrator / auditor / current builder), **PLAN + START BATCH** (one
autonomous run), **RESUME BATCH**, plus the existing Start/Pause/Resume/Stop.
The manual TASK-panel controls remain for debugging. All batch work runs off
the UI thread through the executor worker; the window stays responsive
(offscreen test with a slow driver).

## SECURITY_CHECK

| Check | Result |
|---|---|
| Secret pattern scan over the full raw diff | **`NO_SECRETS_FOUND`** |
| Plan/verdict model output | parsed as untrusted input; only the bounded strict plan/verdict JSON persisted — no transcripts |
| No shell/exec surface | `plan_parser` is `json.loads`-only; nothing in the plan is executed or eval'd |
| Workspace boundary in prompts | planning + audit + fix packets hard-restrict tool use to the workspace path; the smoke adds the CRITICAL BOUNDARY line |
| Read-only planning guard | git read-only commands only (`rev-parse`, `symbolic-ref`, `status --porcelain`); never reset/stash/clean |
| Child environment | unchanged filtered env (D-015) — no `HERMES_*`/`PYTHONPATH` leak into planner/builder/auditor children |
| Hermes profiles/config modified | **none** — read-only discovery plus `-p <existing profile>`; profile listing unchanged |
| Runtime artefacts tracked | none — `*.db*`, `logs/`, `.env*`, keys ignored; scratch lives under the system temp dir with `--keep-scratch` evidence |
| API keys read/printed/logged | none |

## GIT_STATUS

**Repository:** `https://github.com/Xamposs/ENCOMM-PIPELIN-CONTROL-CENTER`
**Branch:** `main`
**Baseline:** `74b2de230ff8b1b0d77099466567c74c4a6fb228`

Pre-commit verification, in the order the brief requires:

| Step | Command | Result |
|---|---|---|
| Re-read material changed source in full | raw reads of every file written/edited | done |
| Inspect the complete raw diff | `git add -A` + `git diff --cached` | reviewed |
| Full unit suite | `python -m pytest` | `334 passed` |
| Fake-mode smoke (post-processing) | `session_004_multitask_smoke.py --fake` | `SMOKE PASSED` |
| Real Session 004 smoke | `session_004_multitask_smoke.py …` | see REAL_SMOKE_TEST |
| Inspect Orchestrator output / 4 Builder results / Auditor results | full step output | done (live run) |
| Run all deterministic scratch acceptance tests | the 4 `test_feature_*.py` | all exit 0 |
| Verify session identities | STEP 5 assertions | done |
| Whitespace / conflict markers | `git diff --check` | clean |
| Secret scan | pattern scan over the raw diff | `NO_SECRETS_FOUND` |
| Runtime DB/log/scratch artefacts ignored | `.gitignore` + staged path check | none staged |
| Hermes config/profiles not modified | profile listing before/after | unchanged |
| Only intended files changed | `git status --short` review | only project files |

Commit + push: `feat: add orchestrated multi-task batch runner` → pushed to
`origin/main` (no force push); `git rev-parse HEAD` == `git rev-parse
origin/main` verified after the push.

---

## KNOWN_LIMITATIONS

1. **No Final Auditor yet** — `BATCH_COMPLETE` is unreachable until Session
   005 wires `FINAL_AUDIT_RUNNING`.
2. **Real live proof is a 4-task batch** (§26); 5 tasks are proven offline.
3. **Pause/stop are boundary-only** — a running prompt always finishes;
   `supports_cancellation=False`.
4. **Planning guard is vacuous on non-git workspaces** (documented).
5. **Engine swaps are configuration-only but untested live** — only Hermes is
   implemented; Codex/GenericCli remain refusing placeholders.
6. **No streaming surface** (`supports_streaming=False`).
7. **`run_batch` is sequential**; no parallel batches/concurrency.
8. **UI tests are offscreen only**; no packaging.

## RISKS

| # | Risk | Severity | Mitigation in place | Residual |
|---|---|---|---|---|
| R1 | A future session mistakes a placeholder for a working engine | High | `implemented=False` + preflight gate + docs | Review discipline |
| R2 | A fabricated `PromptResult` is introduced | High | `simulated` flag; real runs never set it | Review discipline |
| R3 | A malformed plan/verdict becomes green | High | **Closed**: strict parsers fail closed; malformed ⇒ BLOCKED, never materialised | — |
| R4 | Unbounded loop | High | **Closed by construction**: `MAX_AUDIT_ROUNDS=3`, terminal batch outcomes, cap tests | Human clears BLOCKED |
| R5 | Orchestrator modifies the worktree while planning | Medium | **Closed**: read-only fingerprint guard BLOCKS the plan and surfaces the violation | Operator decides on the left files |
| R6 | Restart recovery mis-guesses the next step | Medium | task-state-driven `next_task_action` + derived resume target; reload test | — |
| R7 | Real smoke cost/token drain | Medium | `--fake` mode proves all post-processing first; smoke runs once; needs-fix limited by cap | Cost inherent to real engines |
| R8 | Hermes CLI flags drift on upgrade | Medium | contract in `hermes_cli.py`; capabilities evidence-gated | Inherent to external CLI |
| R9 | Shared auditor session lost provider-side mid-batch | Low | resume failure fails the task loudly; never silently fresh | — |

**Highest-priority item addressed this session:** R5 (the planner can no
longer touch the repo) and the structural READY terminal (brief §10/§32).

---

## NEXT_RECOMMENDED_SESSION

**Session 005 — Final Auditor + batch completion (Phase 4).**

1. Real `FINAL_AUDITOR` through the generic role/driver path; consume the
   durable Batch Summary (`batch_plans.batch_summary_json`); honour
   `same_as_orchestrator` end to end.
2. `READY_FOR_FINAL_AUDIT → FINAL_AUDIT_RUNNING → BATCH_COMPLETE` becomes
   reachable; a failing final audit re-enters `FIX_REQUIRED`.
3. Final-audit report artefact; then engines (Codex first) and operational
   hardening.

**Do not start** Session 005 until this session's orchestrated batch runner is
independently reviewed. Real CodexDriver, automatic next-batch generation and
the Claude/OpenCode/Ollama/Kimi adapters remain strictly out of scope.