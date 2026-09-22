# SESSION 005 — ONE-CALL FINAL AUDIT + NEXT-BATCH HANDOFF

**Project:** ENCOMM Pipeline Control Center
**Date:** 2026-09-22
**Version produced:** `0.5.0`
**Session scope:** the final high-level gate — a real FINAL_AUDITOR running
through the generic role/driver architecture; a `FinalAuditPacket` built from
durable facts; ONE model call returning BOTH the cumulative final verdict AND
the next batch plan; a strict fails-closed parser that reuses the plan rules;
the read-only repository guard extended to the final audit;
`READY_FOR_FINAL_AUDIT → FINAL_AUDIT_RUNNING → BATCH_COMPLETE`; schema v5
(durable Final Audit + `pending_next_plans`); operator-controlled START NEXT
BATCH with zero AI calls; restart-safe handoff — proven by a 33-case offline
matrix, both `--fake` smokes, and ONE real Final Auditor call against a
scratch repository.

---

## STATUS

**PASS** — every Session 005 pass criterion is backed by executed evidence.
The decisive one — a single REAL Final Auditor operation returning
`final_verdict: PASS` plus exactly 4 next-batch tasks in the same response,
followed by `BATCH_COMPLETE`, a SQLite-reloaded durable audit/next plan, and
a START NEXT BATCH handoff with zero further model calls — ran end to end
against the installed Hermes CLI.

| # | Pass criterion | Result | Evidence |
|---|---|---|---|
| 1 | Session 004 multi-task architecture remains valid | **PASS** | 367 tests green incl. the whole Session 004 matrix; `session_004_multitask_smoke.py --fake` → SMOKE PASSED after all changes |
| 2 | FINAL_AUDITOR runs through generic role/driver architecture | **PASS** | `run_final_audit()` resolves `AgentRole.FINAL_AUDITOR → config → DriverRegistry → SessionManager`; no engine-specific audit code |
| 3 | Final Auditor inspects actual repository/tests | **PASS** | Live answer summary cites committed HEAD equality, per-test exit codes; 6 real tool calls in the session; `batch_assessment.tests_verified/diff_verified = true` |
| 4 | Auditor cannot modify the repo without being blocked | **PASS** | Fingerprint before/after (live run: unchanged); offline `test_auditor_modifying_repo_blocks_the_audit` — BLOCKED, file left, nothing persisted |
| 5 | Strict final parser fails closed | **PASS** | 12-case parser matrix + executor failure matrix; malformed can never become PASS |
| 6 | One call returns BOTH final verdict and next batch | **PASS** | Live run: verdict PASS + 4-task next_batch in ONE response |
| 7 | PASS reaches BATCH_COMPLETE | **PASS** | Live: phase BATCH_COMPLETE, batch COMPLETE, legal edges only |
| 8 | Next BatchPlan contains exactly the requested tasks | **PASS** | Requested 4 → exactly 4 tasks (indices 1..4); offline wrong-count rejection |
| 9 | Next BatchPlan is persisted | **PASS** | `pending_next_plans.nextplan_batch_5293638f`; reloaded from SQLite |
| 10 | Next batch is NOT auto-started | **PASS** | Nothing ran after PASS; `START NEXT BATCH` required |
| 11 | START NEXT BATCH uses the persisted plan without a planning call | **PASS** | `batch_a04e2f03`, 4 PENDING tasks, `ScriptedFinalDriver.starts` unchanged (offline) and no second live call |
| 12 | Restart preserves completed batch + next plan | **PASS** | `test_start_next_batch_after_restart_uses_persisted_plan` (real close/reopen of the DB file) + live STEP 5 reload |
| 13 | Failure cannot become COMPLETE | **PASS** | NEEDS_FIX/BLOCKED/malformed/child-failure all land BLOCKED/FAILED; `next_task_action` reports BLOCKED (never COMPLETE) in those states |
| 14 | Offline tests pass | **PASS** | `367 passed in ~40s`, 0 failed |
| 15 | Real smoke uses ONE real model operation | **PASS** | `REAL_MODEL_OPERATIONS = 1` (exactly one `FINAL_AUDIT` child process) |
| 16 | No secrets committed/persisted | **PASS** | see SECURITY_CHECK |
| 17 | Session report exists | **PASS** | this file |
| 18 | Changes are pushed to main | **PASS** | see GIT_STATUS |

---

## BASELINE_COMMIT

`797cf228060bba3ccc3b4fe183196d75a3f4ad7a` — verified before any edit
(`pwd`, `git rev-parse --show-toplevel`, `git status --short`,
`git branch --show-current`, `git rev-parse HEAD`, `git remote -v`). A
mid-session WIP commit `6180f03` (offline foundations, honest `wip`
message) was pushed when the user asked for an interim push; all final work
sits on top of it. Nothing was reset, stashed, cleaned or discarded.

---

## OBJECTIVE

Implement the final high-level gate:

    READY_FOR_FINAL_AUDIT
        ↓ FINAL_AUDITOR (ONE real model call)
    STRICT CUMULATIVE FINAL VERDICT
        ↓ PASS
    BATCH_COMPLETE + NEXT BATCH PLAN (4–5 tasks)
        ↓ WAITING FOR USER (START NEXT BATCH is operator-controlled, zero AI)

with the Final Auditor running through the generic role architecture, the
actual repository/diff/tests as the ONLY authority (Batch Summary = evidence
index), a strict parser that fails closed, a read-only audit (any repo
modification BLOCKS), no automatic expensive loop anywhere, and full
restart-safe persistence of the completed audit and the pending next plan.

---

## LEANCTX_USAGE

**Skill loaded:** `encomm-leanctx` before any repository exploration
(`scripts/lctx.sh` → `lean-ctx 3.10.1`).

- Step 0A repository safety: `pwd` + `git rev-parse --show-toplevel` =
  intended target; HEAD matched the expected baseline.
- Orientation: `lean-ctx overview` (33 files) mapped the change area;
  `grep`/`find` for symbol locations.
- **Accuracy-guard compliance:** every file written or edited was held in
  full raw (`read_file` over executor, database, schema, controller, runner,
  parser, models, state machine, UI panels/window/worker, smoke scripts);
  every test failure was diagnosed from complete tracebacks (e.g. the
  `same_as_orchestrator` resolution root cause came from a live
  `Engine 'hermes' is not registered` reproduction, never a guess). The
  hyphen pitfall was avoided (`lean-ctx`, never `leanctx`).

---

## ARCHITECTURE_CHANGES

| Area | Change |
|---|---|
| `domain/final_audit.py` (new) | `FinalVerdict`, `FINAL_VERDICTS`, `FinalAuditPacket` (durable facts, read-only contract), `FinalAuditResult` (verdict + assessment flags + optional next plan) |
| `core/final_audit_packet.py` (new) | Deterministic ONE-call prompt: actual repo/diff/tests are authority; Batch Summary evidence-only; DO NOT EDIT FILES; verdict+next-plan schema in the strict envelope |
| `core/final_audit_parser.py` (new) | Strict fails-closed parser; envelope `<<<FINAL_AUDIT_START>>>…<<<FINAL_AUDIT_END>>>`; `json.loads` only; bounded; nested next plan validated by the SAME `plan_parser` rules (D-031) |
| `domain/enums.py` / `state_machine.py` | No graph change needed — `BATCH_COMPLETE` already reachable ONLY from `FINAL_AUDIT_RUNNING` (D-025); the executor now walks `READY → RUNNING → COMPLETE` legally |
| `core/executor.py` | `FINAL_AUDITOR_ROLE`, `MIN/MAX/DEFAULT_NEXT_BATCH_SIZE (4/5/5)`, `run_final_audit()` + `_build_final_audit_packet()`, `start_next_batch()`, `FinalAuditOutcome/Report`, `StartNextBatchOutcome/Report`; `next_task_action` now lets BLOCKED/FAILED phase dominate all-APPROVED tasks |
| `persistence/schema.sql` + `database.py` | **Schema v5** (D-032): `batch_plans` += 7 final-audit columns; new `pending_next_plans` table; targeted v4→v5 upgrade; `save/load_final_audit`, `save/load/load_open/mark_consumed_pending_next_plan` |
| `ui/panels.py` | **`FinalAuditPanel`** (new): batch/verdict state, resolved auditor (same-as-orchestrator aware), next-batch size spin (4–5), RUN FINAL AUDIT / VIEW NEXT TASKS / START NEXT BATCH, enabled only from legal persisted states |
| `ui/worker.py` | `final_audit` worker action (off the UI thread) |
| `ui/main_window.py` | Panel wiring + `_on_run_final_audit` / `_on_final_audit_finished` / `_on_start_next_batch` |
| `scripts/session_005_final_audit_smoke.py` (new) | Real one-call smoke (+ `--fake` offline mode; scratch repo pre-seeded as a completed batch) |
| `tests/test_final_audit.py` (new) | 33-test offline matrix |
| `src/encomm_pcc/__init__.py` | `0.5.0` |

---

## FINAL_AUDIT_PACKET

`domain/final_audit.py::FinalAuditPacket` — assembled by
`Executor._build_final_audit_packet()` from durable facts only (never
conversation memory): batch id/title/objective, Project Brief, planned task
titles, per-task rows (title, implementation prompt, acceptance criteria,
audit focus, attempts, audit rounds, final verdict, builder/fix/auditor
session ids), the durable Batch Summary JSON, builder session ids, the
shared Task Auditor session id, the Orchestrator session id, baseline and
current HEAD, the workspace path, and the required next-batch size.

`core/final_audit_packet.py::render_final_audit_prompt()` adds the
non-negotiable instructions: inspect the ACTUAL repository and cumulative
diff; RUN the deterministic tests and read complete failure output; compare
against EVERY task contract; never trust prior Builder/Auditor summaries;
**READ-ONLY AUDIT — DO NOT EDIT FILES** (the supervisor fingerprints before
and after); restrict all tool use to the workspace; return ONE JSON object
in the `<<<FINAL_AUDIT_START>>>…<<<FINAL_AUDIT_END>>>` envelope carrying
BOTH the verdict and (PASS only) the next plan of EXACTLY N tasks.

---

## FINAL_RESULT_SCHEMA

```json
{
  "final_verdict": "PASS | NEEDS_FIX | BLOCKED",
  "summary": "...",
  "findings": [
    {"severity": "critical|high|medium|low", "message": "...", "evidence": "..."}
  ],
  "batch_assessment": {"tests_verified": true, "diff_verified": true},
  "next_batch": {
    "batch_title": "...",
    "batch_objective": "...",
    "tasks": [
      {"index": 1, "title": "...", "implementation_prompt": "...",
       "acceptance_criteria": ["..."], "audit_focus": ["..."]}
    ]
  }
}
```

Rules: PASS — no unresolved critical/high finding, `next_batch` REQUIRED
with exactly the requested task count; NEEDS_FIX — findings required,
`next_batch` forbidden; BLOCKED — `next_batch` forbidden.

## FINAL_RESULT_PARSER

`core/final_audit_parser.py` (`parse_final_audit(raw,
expected_next_tasks=N)`) — the Final Auditor's output is **untrusted**
(D-031): bounded raw input (200 000 chars), `json.loads` only (no
eval/exec/YAML), envelope with balanced-brace fallback, exact verdict
whitelist, boolean `batch_assessment` fields required, findings bounded
(50 × bounded strings). The nested next plan is validated by
`plan_parser._validate` — the SAME strict rules as an Orchestrator plan
(exact count, contiguous indices, unique titles, bounded fields). Failure
reasons: `malformed_json`, `invalid_final_verdict`,
`pass_with_unresolved_defect`, `pass_without_next_batch`,
`malformed_next_batch_*`, `needs_fix_with_next_batch`,
`needs_fix_without_findings`, `blocked_with_next_batch`,
`no_json_object`, `oversized_payload`. **A malformed result can never
become PASS** — the executor maps every `FinalAuditParseError` to BLOCKED.

## FINAL_AUDITOR_IMPLEMENTATION

`Executor.run_final_audit()` — deterministic, Qt-free, off the UI thread:

1. phase gate (`READY_FOR_FINAL_AUDIT`, or `FINAL_AUDIT_RUNNING` as crash
   recovery); next-batch size gate (4..5); all-APPROVED gate
2. generic role path: resolve FINAL_AUDITOR (`same_as_orchestrator`
   honoured) → preflight → registry create → `SessionManager.decide`
   (`configurable` policy; REUSE resumes, a resume failure fails honestly)
3. transition `READY → FINAL_AUDIT_RUNNING` (after preflight, before the
   prompt) and persist
4. read-only repository fingerprint BEFORE the call
5. ONE packet prompt → real child process → exit code
6. child failure/timeout ⇒ pipeline **FAILED**
7. fingerprint AFTER; mismatch ⇒ **BLOCKED** (`guard_violation`,
   modifications left untouched, nothing persisted from the violating
   answer)
8. strict parse; `FinalAuditParseError` ⇒ **BLOCKED**
9. apply the verdict (below)

## FINAL_AUDITOR_SESSION_POLICY

The role keeps its brief-mandated `configurable` policy
(`decide_session_action`): continuity is preferred when a same-generation
session exists, NEW otherwise, and a REUSE resume failure fails the run
honestly — continuity is never fabricated. `same_as_orchestrator=True`
resolves the engine through
`PipelineState.resolved_engine_for(FINAL_AUDITOR)`; the UI label reflects
it. The Session 005 live smoke used a fresh session (acceptable per brief
§11) and recorded the real external session id on `batch_plans`.

## REPOSITORY_READ_ONLY_GUARD

`core/repo_fingerprint.py` (HEAD + porcelain hash) before/after the call;
any difference ⇒ `FINAL AUDITOR READ-ONLY GUARD VIOLATION`, the audit is
BLOCKED, the modifications are surfaced and left for the operator — never
auto-discarded, nothing persisted from the violating answer. Proven
offline (`test_auditor_modifying_repo_blocks_the_audit`) and honoured live
(fingerprints equal in the real run).

## STATE_MACHINE_CHANGES

No graph edge changed — Session 004's structural guarantee (D-025:
`BATCH_COMPLETE` reachable ONLY from `FINAL_AUDIT_RUNNING`) became a real
path: `READY_FOR_FINAL_AUDIT → FINAL_AUDIT_RUNNING` (after preflight,
before the prompt) → `BATCH_COMPLETE` on a strictly parsed PASS. A crash
mid-audit persists `FINAL_AUDIT_RUNNING`; recovery re-runs the audit (a
real new call) instead of silently passing. `next_task_action` now reports
BLOCKED/FAILED (never COMPLETE) when the pipeline phase is BLOCKED/FAILED
even with all tasks APPROVED — the final-audit failure signature.

## BATCH_COMPLETION

On PASS: the parsed result is persisted (`final_verdict`, `final_summary`,
`final_findings_json`, `final_audit_json`, `final_auditor_session_id`,
`final_audited_at`, `final_next_plan_id` on `batch_plans`), the plan's
`final_phase` is set to `BATCH_COMPLETE`, the batch status becomes
`COMPLETE`, and the phase walks `FINAL_AUDIT_RUNNING → BATCH_COMPLETE`
over the legal edge. Nothing else runs.

## NEXT_BATCH_GENERATION

The PASS payload's `next_batch` — already strictly validated (exact
operator-requested count, plan rules) — is serialised into
`pending_next_plans.plan_json` with `requested_size` and
`source_batch_id`. The Final Auditor generated 4 next tasks in the live
run (feature_5..8 extensions) in the SAME call as the verdict.

## NEXT_BATCH_PERSISTENCE

`pending_next_plans` (schema v5): `plan_id` (`nextplan_<batch_id>`),
`source_batch_id`, `requested_size`, `plan_json`, `created_at`,
`consumed_at`, `consumed_batch_id`. The open plan survives restarts; the
UI's VIEW NEXT TASKS / START NEXT BATCH enable from this row; consumption
is recorded so a plan can never be materialised twice.

## START_NEXT_BATCH_HANDOFF

`Executor.start_next_batch()` (D-032): legal only from
`IDLE`/`BATCH_COMPLETE`; loads the open pending plan; leaves
`BATCH_COMPLETE` over its legal IDLE edge; `request_start(N)` opens a NEW
batch generation; the already-generated tasks are materialised PENDING
with their criteria/audit focus; the plan is marked consumed; persist.
**No Orchestrator call, no Final Auditor call — zero AI.** The operator
then uses the normal START controls. Auto-start is not implemented
anywhere.

## RESTART_RECOVERY

- `READY_FOR_FINAL_AUDIT` survives restart (persisted phase) — proven
  offline (fixture seeds through SQLite + machine sync).
- `FINAL_AUDIT_RUNNING` never silently becomes PASS: recovery requires a
  real re-run (`test_restart_recovery_from_final_audit_running_never_silently_passes`).
- The completed Final Audit + next plan survive restart (file-backed DB
  close/reopen test; live STEP 5 reload).
- START NEXT BATCH after restart uses the persisted plan
  (`test_start_next_batch_after_restart_uses_persisted_plan`); the
  completed batch remains queryable.
- Starting the application never triggers AI.

---

## REAL_SMOKE_TEST

`scripts/session_005_final_audit_smoke.py` — deliberately outside the unit
suite. Scratch git repo pre-seeded as an ALREADY-completed 4-task batch
(`feature_1..4.py` + four deterministic tests, all committed, all green);
durable fixtures (Project Brief, strict BatchPlan, four APPROVED task rows
with clearly-marked `fake-*` historical session ids, Batch Summary, phase
`READY_FOR_FINAL_AUDIT`); scratch application database.

**`--fake` mode first (cost guard):** the whole flow runs against an
in-process scripted driver — parser, guard, PASS path, persistence,
reload, START NEXT BATCH, session-id evidence — **SMOKE PASSED in 2.5 s**
before any real call.

**Real run (once):**

```
python scripts/session_005_final_audit_smoke.py \
  --profile encomm-pipeline-control-center --timeout 1200 --keep-scratch
```

| Step | Real action | Result |
|---|---|---|
| 0 | Hermes CLI present | real `hermes.EXE`; driver implemented |
| 1 | scratch batch seeded, all green | 4 tests exit 0 pre-audit; HEAD `b877a9d4…` |
| 2 | durable fixtures | phase `READY_FOR_FINAL_AUDIT` from SQLite state |
| 3 | **ONE Final Auditor call** | **FINAL PASS + 4 next tasks, same response**; ≈360 s; 6 tool calls (the auditor ran the tests itself and inspected git) |
| 4 | independent re-verification | 4 tests exit 0 post-audit; worktree clean (guard held) |
| 5 | SQLite reload | `final_verdict PASS`, `nextplan_batch_5293638f` persisted |
| 6 | START NEXT BATCH | `batch_a04e2f03`, 4 PENDING tasks, phase `PLANNING_BATCH`, zero AI calls; previous batch `COMPLETE` and queryable |

The auditor's live summary (excerpt): *"All four feature modules exist, are
committed at HEAD b877a9d4… (identical to the recorded baseline …), and
every deterministic test exits 0 when run with the repo root importable."*
It also returned 3 non-blocking findings (medium: the literal
`python tests/test_feature_N.py` invocation fails from a bare shell —
import path; low: empty cumulative diff because HEAD equals baseline; low:
untracked `__pycache__/` from the test runs) — evidence of genuine
inspection rather than rubber-stamping. Severity rules kept the verdict
strictly valid (no critical/high on PASS).

## REAL_MODEL_OPERATIONS

**1** — exactly one `FINAL_AUDIT` child process (one real Final Auditor
operation). No Builder, Task Auditor, or Orchestrator calls. Session 004
used 9 model operations on its clean run; Session 005 closes a completed
batch with 1.

## REAL_PROVIDER

`zai` (Hermes billing metadata of the session; the CLI ran with the
`encomm-pipeline-control-center` profile's configured provider).

## REAL_MODEL

`glm-5.3-flash`

## REAL_SESSION_ID

`20260922_233757_2effcf` (persisted on
`batch_plans.final_auditor_session_id`, mirrored into `sessions` with
`external=1`).

## REAL_INPUT_TOKENS

**35 830** main task (plus 464 for Hermes' one automatic
title-generation side call; 5 API calls, 81 856 cache-read, 10 607
reasoning tokens included in the main row).

## REAL_OUTPUT_TOKENS

**14 311** main task (plus 37 title-generation).

## REAL_TOTAL_TOKENS

**50 141 input+output as reported by `session_model_usage`**
(35 830 + 14 311 main; +501 with the title side call). Cache-read tokens
are reported separately by the provider and not summed into billing
totals. **No billing-savings claims are made** — evidence only. The key
comparison: Session 004 = 9 model operations for a full batch; Session 005
= 1 operation to close the batch and plan the next one.

## REAL_WALL_CLOCK

≈ **362 s** for the ONE call (20:37:56Z dispatch → 20:43:56Z result);
smoke total ≈ 362 s including seeding/verification.

## REAL_FINAL_VERDICT

`PASS` (strict; `batch_assessment.tests_verified = true`,
`diff_verified = true`; 3 findings, none critical/high).

## REAL_NEXT_TASK_COUNT

**4** (exactly the requested size; indices contiguous 1..4: feature_5
subtract, feature_6 power, feature_7 mean-with-empty-guard, feature_8
capitalize_words — each with self-contained prompts, deterministic
criteria and audit focus).

## REAL_AUDITOR_TOOL_CALLS

6 (read/edit-free: test runs, git inspection, file reads). The worktree
fingerprint was unchanged after the call — the read-only contract held
live.

---

## FILES_CREATED

| File | Purpose |
|---|---|
| `src/encomm_pcc/domain/final_audit.py` | Final-audit contract (packet + result + verdict enum) |
| `src/encomm_pcc/core/final_audit_packet.py` | Deterministic one-call prompt builder |
| `src/encomm_pcc/core/final_audit_parser.py` | Strict fails-closed final-audit parser |
| `scripts/session_005_final_audit_smoke.py` | Real one-call smoke (+ `--fake`) |
| `tests/test_final_audit.py` | 33-case offline matrix |
| `docs/reports/SESSION_005_FINAL_AUDIT_NEXT_BATCH.md` | this file |

## FILES_CHANGED

`src/encomm_pcc/__init__.py` (0.5.0), `pyproject.toml`, `README.md`,
`core/executor.py` (final-audit + handoff + action fix), `core/__init__.py`,
`domain/__init__.py`, `persistence/schema.sql` + `database.py` (v5),
`ui/panels.py` (FinalAuditPanel), `ui/main_window.py`, `ui/worker.py`,
`tests/test_executor.py` (schema guard v5), `tests/test_imports.py`
(version), `docs/CURRENT_STATE.md`, `docs/ARCHITECTURE.md`,
`docs/ROADMAP.md`, `docs/DECISIONS.md` (D-030…D-033).

## TESTS_RUN

```bash
python -m pytest                                        # full offline suite (367)
python -m pytest tests/test_final_audit.py              # the new matrix
python scripts/session_005_final_audit_smoke.py --fake  # offline post-processing proof
python scripts/session_005_final_audit_smoke.py --profile encomm-pipeline-control-center …  # REAL (once)
python scripts/session_004_multitask_smoke.py --fake    # Session 004 regression guard
git diff --check                                        # whitespace/conflict markers
# secret scan over the full diff · artefact check · Hermes config integrity check
```

## TEST_RESULTS

```
367 passed in ~40s  (0 failed, 0 skipped, 0 errors) — exit code 0
```

| Area | Tests |
|---|---|
| existing suite (Sessions 001–004) | 334 (schema-guard + version tests updated) |
| `test_final_audit.py` (new) | +33 |
| **Total** | **367** |

### DEFECTS_FOUND_AND_FIXED

| # | Defect | Root cause | Fix |
|---|---|---|---|
| 1 | `run_final_audit` would move `READY → BATCH_COMPLETE` directly | illegal edge (only `FINAL_AUDIT_RUNNING → BATCH_COMPLETE` exists) | transition to `FINAL_AUDIT_RUNNING` after preflight, before the prompt; RUNNING also accepted on restart recovery with a mandatory real re-run |
| 2 | Final-audit NEEDS_FIX/BLOCKED batch reported next action `COMPLETE` | all-APPROVED tasks shadowed the pipeline phase in `next_task_action` | BLOCKED/FAILED phase now dominates the all-APPROVED case |
| 3 | Test fixtures: FINAL_AUDITOR always resolved to `hermes` | the role placeholder ships `same_as_orchestrator=True` | fixtures clear the flag (as the operator does); the live smoke passes an explicit engine |
| 4 | Smoke crashed at `git rev-parse HEAD` | scratch repo had no commit yet | added the seed commit |
| 5 | Restart test opened a closed `:memory:` DB | in-memory DB dies with the connection | file-backed DB + real close/reopen (stronger proof anyway) |
| 6 | `D-029` header briefly mangled during the ADR append | patch overlap | restored verbatim |

## FAILURE_CASES

All offline-tested; each is provably non-green:

- parser: malformed JSON, invalid verdict, PASS+critical/high, PASS without
  next_batch, wrong next-task count, NEEDS_FIX without findings / with
  next_batch, BLOCKED with next_batch, non-boolean assessment, no JSON,
  oversized payload
- executor: child non-zero ⇒ FAILED; timeout ⇒ FAILED; auditor modifies
  the worktree ⇒ BLOCKED guard violation (file left, nothing persisted);
  malformed answer ⇒ BLOCKED; NEEDS_FIX/BLOCKED verdicts ⇒ persisted
  operator BLOCKED state; wrong/absent phase or size ⇒ REJECTED
- handoff: no pending plan ⇒ REJECTED; consumed plan not reusable;
  START NEXT BATCH makes no AI call (asserted)

---

## UI_STATUS

New **FINAL AUDIT** section between BATCH and TASK: current
batch/verdict line (reads the durable audit row), resolved Final Auditor
(engine/profile/provider/model/session, `(same as orchestrator)` badge),
next-batch size spin (4–5, default 5), and buttons — **RUN FINAL AUDIT**
(enabled only with an executor attached at `READY_FOR_FINAL_AUDIT`),
**VIEW NEXT TASKS** and **START NEXT BATCH** (enabled only when a plan is
persisted and the phase allows the handoff). After PASS the status bar
reads `FINAL AUDIT: PASS — batch COMPLETE … press START NEXT BATCH when
ready (nothing auto-runs)`. All final-audit work runs off the UI thread
through the executor worker.

## SECURITY_CHECK

| Check | Result |
|---|---|
| Secret pattern scan over the full diff | **`NO_SECRETS_FOUND`** |
| Model output | parsed as untrusted input; only the bounded strict result JSON persisted — no transcripts |
| No shell/exec surface | `final_audit_parser` is `json.loads`-only; the next plan is validated, never executed |
| Workspace boundary in prompts | the final-audit packet hard-restricts tool use to the workspace path; DO-NOT-EDIT instruction + fingerprint guard |
| Read-only guard | git read-only commands only; violations surfaced, never auto-reverted |
| Child environment | unchanged filtered env (D-015) |
| Hermes profiles/config modified | **none** — read-only discovery plus `-p <existing profile>` |
| Runtime artefacts tracked | none — scratch under the system temp dir; its DB was removed after evidence extraction |
| API keys read/printed/logged | none |

## GIT_STATUS

**Repository:** `https://github.com/Xamposs/ENCOMM-PIPELIN-CONTROL-CENTER`
**Branch:** `main`
**Baseline:** `797cf228060bba3ccc3b4fe183196d75a3f4ad7a`
**Mid-session WIP commit:** `6180f03d53468bc99b7e2fb53af324b78de669b3`
(offline foundations, pushed on request with an honest `wip(session-005)`
message — `origin/main` verified equal at push time)

Pre-commit verification, in order:

| Step | Command | Result |
|---|---|---|
| Re-read material changed source in full | raw reads of every file written/edited | done |
| Full unit suite | `python -m pytest` | `367 passed` |
| Fake smokes (post-processing) | both `--fake` smokes | `SMOKE PASSED` |
| Real Session 005 smoke | one Final Auditor call | see REAL_SMOKE_TEST |
| Inspect complete raw model output | live answer + findings + next plan | done |
| Independent scratch tests | 4 deterministic tests | all exit 0 |
| Whitespace / conflict markers | `git diff --check` | clean |
| Secret scan | pattern scan over the diff | `NO_SECRETS_FOUND` |
| Runtime artefacts ignored | `.gitignore` + staged path check | none staged |
| Hermes config/profiles not modified | profile listing before/after | unchanged |
| Only intended files changed | `git status --short` review | only project files |

Commit + push: `feat: add one-call final audit and next-batch handoff` →
pushed to `origin/main` (no force push); `git rev-parse HEAD` ==
`git rev-parse origin/main` verified after the push. Working tree clean
after the push.

---

## KNOWN_LIMITATIONS

1. **Live proof used a 4-task next plan** (requested 4); 5 tasks are
   proven offline (parser + handoff matrix).
2. **Real CodexDriver and the Claude/OpenCode/Ollama/Kimi adapters do not
   exist** — Session 006 implements Codex so the expensive
   Orchestrator/FINAL_AUDITOR roles can switch engines from the UI.
3. **Pause/stop are boundary-only**, including during a running final-audit
   prompt (`supports_cancellation=False`).
4. **The guards are vacuous on non-git workspaces** (documented).
5. **A final-audit NEEDS_FIX/BLOCKED is operator-territory**: findings are
   persisted, the pipeline is BLOCKED; targeted fixes / revert / batch
   restart are human decisions — no automatic batch-wide fix loop exists.
6. **Sequential execution**; no parallel batches/concurrency.
7. **UI tests are offscreen only; no packaging.**

## RISKS

| # | Risk | Severity | Mitigation in place | Residual |
|---|---|---|---|---|
| R1 | A malformed final answer becomes green | High | **Closed**: strict parser + executor mapping, 12-case rejection matrix | — |
| R2 | The auditor edits the repo during the audit | High | **Closed**: fingerprint guard ⇒ BLOCKED, files surfaced, nothing persisted | Operator decides on left files |
| R3 | Next plan materialised twice | Medium | **Closed**: pending plan consumed exactly once (`consumed_at`) | — |
| R4 | Unbounded loops | High | **Closed by construction**: no automatic fix/audit/next-batch path exists | Human clears BLOCKED |
| R5 | Provider session loss on a resumed FINAL_AUDITOR | Low | resume failure fails the run loudly; never silently fresh | — |
| R6 | Restart mid-audit mistaken for completion | Medium | **Closed**: RUNNING recovery requires a real re-run (test-pinned) | — |
| R7 | Token/cost drain | Medium | `--fake` first; ONE call per completed batch; no auto-start | Cost inherent to real engines |
| R8 | Hermes CLI flags drift | Medium | contract in `hermes_cli.py`; capabilities evidence-gated | Inherent to external CLI |

## NEXT_RECOMMENDED_SESSION

**Session 006 — real CodexDriver + engine switching (Phase 5).**

1. Implement `CodexDriver` (evidence-gated capabilities, `BaseDriver`
   contract, real argv/exit codes/session ids) and register it.
2. Switch the ORCHESTRATOR/FINAL_AUDITOR roles to Codex **from the UI**
   (configuration only) and prove a scratch final audit through Codex.
3. Optionally `GenericCliDriver`'s configurable argv; Phase 6 hardening
   after that.

**Do not start** Session 006 until this session's one-call final audit is
independently reviewed. Automatic batch chaining, packaging, and the
remaining engine adapters stay out of scope.
