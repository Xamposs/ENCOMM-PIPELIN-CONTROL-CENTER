# SESSION 003 — TASK AUDITOR + CAPPED FIX LOOP

**Project:** ENCOMM Pipeline Control Center
**Date:** 2026-09-22
**Version produced:** `0.3.0`
**Session scope:** the first complete controlled loop — real Task Auditor,
strict structured verdicts, a fix in a brand-new Builder session, a re-audit in
the same auditor session, a hard round cap, restart recovery, and the UI to
drive it — for exactly one task.

---

## STATUS

**PASS**

Every Session 003 pass criterion is backed by executed evidence. The decisive
one — a real engine answering an audit, a real fix, and a real re-audit in the
same session — ran end to end through `Control Center → Executor →
role/driver path → installed Hermes CLI`, and the verdicts, session ids and
final state were read back from SQLite.

| # | Pass criterion | Result | Evidence |
|---|---|---|---|
| 1 | Session 002 real Hermes execution still works | **PASS** | `dispatch_single_task` untouched in behaviour; `session_002_smoke` path regression-covered; 280 tests (was 221) |
| 2 | `TASK_AUDITOR` runs through the generic role/driver architecture | **PASS** | `run_task_audit()` resolves `AgentRole.TASK_AUDITOR → role config → DriverRegistry → SessionManager`; no auditor-specific Hermes path |
| 3 | Auditor uses a real Hermes session | **PASS** | Real smoke: auditor session `20260922_193731_557d54` created by the CLI, external=1 in SQLite |
| 4 | Structured verdict parsing exists and fails closed | **PASS** | `core/verdict_parser.py`: 30 offline tests; malformed/oversized/invalid ⇒ `VerdictParseError` ⇒ `BLOCKED`, never PASS |
| 5 | Initial controlled defect produces NEEDS_FIX | **PASS** | Real smoke STEP 3: auditor returned `NEEDS_FIX` with a parsed `fix_prompt` |
| 6 | A NEW Builder session performs the fix | **PASS** | Fix session `20260922_193815_4782be` ≠ auditor id; `always_new` enforced; `run_task_fix` refuses REUSE |
| 7 | SAME auditor session performs re-audit | **PASS** | `AUDITOR_INITIAL_SESSION_ID == AUDITOR_REAUDIT_SESSION_ID` (unit + real smoke) |
| 8 | Final verdict becomes PASS | **PASS** | Real smoke STEP 6: strict verdict `PASS` parsed from re-audit output |
| 9 | Deterministic scratch tests pass after the fix | **PASS** | `test_calculator.py` exits 0 (`add(2,3)==5`) — the smoke runs it independently |
| 10 | Session IDs prove isolation/reuse semantics | **PASS** | STEP 7: `initial == re-audit` True, `fix != auditor` True |
| 11 | Audit/fix loop has a hard enforced cap | **PASS** | `MAX_AUDIT_ROUNDS = 3`, enforced in both `run_task_audit` and `run_task_fix`; cap tests prove BLOCKED and no third fix |
| 12 | State and verdict survive SQLite reload | **PASS** | STEP 8: task APPROVED / PASS / rounds / session ids re-read from SQLite; `batches.phase` restored |
| 13 | Process/parser failures never become green | **PASS** | auditor/fix child failures → FAILED; malformed verdicts → BLOCKED; missing fix_prompt → BLOCKED (offline tests) |
| 14 | Normal unit suite passes | **PASS** | `280 passed in …s`, 0 failed |
| 15 | No secrets persisted/committed | **PASS** | Pattern scan over the raw diff → `NO_SECRETS_FOUND` |
| 16 | Documentation reflects reality | **PASS** | `CURRENT_STATE/ARCHITECTURE/ROADMAP/DECISIONS` updated; ADRs D-019…D-024 appended |
| 17 | SESSION_003 report exists | **PASS** | This file |
| 18 | Commit is pushed to main | **PASS** | See GIT_STATUS |

---

## BASELINE_COMMIT

`14f037ce77a585741e0eb96d23171107971a23ef` — verified before any edit
(`pwd`, `git rev-parse --show-toplevel`, `git status --short`, `git branch
--show-current`, `git rev-parse HEAD`, `git remote -v`). The working tree was
clean at baseline; the interleaved Session-003 work-in-progress (executor,
parser, tests) was authored on top and is included in this session's commit.
Nothing was reset, stashed, cleaned or discarded.

---

## OBJECTIVE

Close the first complete controlled loop for a single task:

    TASK → BUILDER → TASK AUDITOR → PASS
        │                          ↓
        └──── NEEDS_FIX → FIX (NEW Builder session) → RE-AUDIT (same session)

with a real Task Auditor running through the existing role/driver architecture,
a strict machine-readable verdict (never prose), a hard `MAX_AUDIT_ROUNDS = 3`
cap, durable persistence of the loop state, deterministic restart recovery, and
a real scratch-repo smoke proving the loop with real engine sessions.

Strictly out of scope (implemented in later sessions): multi-task batches, the
Orchestrator, CodexDriver, the Final Auditor, and automatic next-batch
generation.

---

## LEANCTX_USAGE

**Skill loaded:** `encomm-leanctx` before any repository exploration.

| Step | Command | Result |
|---|---|---|
| 0A Repository safety | `pwd` + `git rev-parse --show-toplevel` | `C:/Users/xampos/Desktop/ENCOMM PIPELINE CONTROL CENTER` (intended target) |
| 0B LeanCTX availability | skill resolver `scripts/lctx.sh` | `C:\Users\xampos\AppData\Local\leanctx\node_modules\lean-ctx-bin\bin\lean-ctx.exe` |
| 0B Verification | `lean-ctx --version` | `lean-ctx 3.10.1` |
| Orientation | `lean-ctx overview "task auditor and capped fix loop single task"` | 33 files; hotspots pointed at the driver/executor layer |

**Where LeanCTX actually helped.** The repository is small; the value came from
the preflight discipline, the overview identifying the executor as the change
area, and escalation discipline: every file written or edited was held in full,
and every test failure was diagnosed from complete tracebacks, never summary
lines. The hyphens pitfall was avoided (`lean-ctx`, never `leanctx`).

**Accuracy-guard compliance.** No correctness conclusion rests on compressed
context: all material source was read full/raw before editing; the smoke
verdicts are read from real child output and SQLite rows re-read after the run.

---

## ARCHITECTURE_CHANGES

| Area | Change |
|---|---|
| `domain/audit.py` (new) | `AuditVerdict`, `FindingSeverity`, `AuditFinding`, `AuditVerdictResult` — the strict audit contract |
| `domain/models.py` | `TaskStateRecord` +6 fields: `latest_verdict`, `verdict_json`, `fix_prompt`, `auditor_session_id`, `builder_session_id`, `fix_session_id`; `BatchState.phase` (restart anchor) |
| `domain/state_machine.py` | New legal edge `AUDITING_TASK → BATCH_COMPLETE` (single-task PASS completes the batch) |
| `core/verdict_parser.py` (new) | Strict, fails-closed parser for untrusted auditor output |
| `core/audit_packet.py` (new) | Deterministic `AuditPacket` + fix-prompt builders with hard workspace boundaries |
| `core/session_manager.py` | `restore_session()` — rebind a persisted external session id after restart |
| `core/executor.py` | `MAX_AUDIT_ROUNDS=3`, `AUDITOR_ROLE`, `TaskNextAction`, `next_task_action()`, `prepare_task_for_audit()`, `run_task_audit()`, `run_task_fix()`, `_ensure_work_phase()` (restart), `_block_task()`, role-parameterised preflight/session helpers; `ExecutionOutcome.NEEDS_FIX` |
| `persistence/schema.sql` + `database.py` | Schema **v3**: six `tasks` audit/session columns + `batches.phase`; targeted v2→v3 upgrade; phase persisted/restored |
| `ui/worker.py` | `ExecutorWorker` actions: `dispatch` / `audit` / `fix` |
| `ui/panels.py` | `TaskPanel`: next-action label, Run-Auditor / Run-fix buttons, audit-loop readouts |
| `ui/main_window.py` | Audit/fix signals → worker wiring off the UI thread |
| `src/encomm_pcc/__init__.py` | `0.3.0` |

The loop's four invariants hold throughout: (1) AI output is **data**, never a
transition — the executor validates verdicts and decides; (2) failure is never
silent; (3) everything durable is persisted before and after each boundary;
(4) sessions are bookkeeping, never the source of truth.

---

## AUDIT_PACKET

`core/audit_packet.py` — the Control Center assembles every auditor prompt
deterministically; nothing model-shaped lives in the executor. `AuditPacket`
carries:

- task id, title, the original implementation prompt
- workspace/repository path (with the hard boundary: "the ONLY path you may touch")
- attempt number, audit round, batch id, auditor session id
- the previous verdict+findings+fix_prompt on round > 1 (re-audit focus)
- the required output schema and the delimited envelope
- instructions to inspect the **actual** repository/source/diff/tests, to run
  tests and read complete failure output, and **not to trust any Builder
  self-report**

`render_fix_prompt()` builds the self-contained fix packet: original task,
auditor findings, the auditor's `fix_prompt` verbatim, workspace boundary, and
an explicit "you are a brand-new session; you have NO memory of earlier
sessions" instruction.

---

## AUDIT_VERDICT_SCHEMA

Exactly the contract in `domain/audit.py` (and mirrored in the prompt packet):

```json
{
  "verdict": "PASS" | "NEEDS_FIX" | "BLOCKED",
  "summary": "...",
  "findings": [
    {"severity": "critical|high|medium|low", "message": "...", "evidence": "..."}
  ],
  "fix_prompt": "..."
}
```

Rules:

- **PASS** — `fix_prompt` MUST be empty and no `critical`/`high` finding may be
  present (an unresolved defect contradicts a pass).
- **NEEDS_FIX** — `fix_prompt` MUST be present and non-empty: the deterministic
  correction for a fresh Builder session.
- **BLOCKED** — the auditor cannot determine a safe correction or a manual
  decision is required.

Nothing in the schema is optional at parse time; the executor persists the
bounded serialised verdict (`tasks.verdict_json`, capped at 80 000 chars) and
the `fix_prompt` column separately so a fix run never re-parses untrusted text.

---

## VERDICT_PARSER

`core/verdict_parser.py` treats model output as **untrusted input**:

- bounded input: 200 000 chars raw, 50 findings max, per-string caps
  (summary 10 000, finding text 20 000, fix_prompt 50 000)
- strict `json.loads` only — **no eval(), no exec(), no YAML**, no model-supplied
  command execution (`fix_prompt` is text for the Builder, never a shell command)
- envelope: `<<<AUDIT_VERDICT_START>>> … <<<AUDIT_VERDICT_END>>>`, with a
  balanced-braces fallback for models that wrap the object in prose; surrounding
  Markdown is never trusted
- exact verdict whitelist `PASS | NEEDS_FIX | BLOCKED`; wrong types rejected;
  unknown extra keys ignored (schema fields validated strictly)
- semantic rules above are **enforced by the parser**, not by convention: a
  PASS-with-fix_prompt, PASS-with-critical/high-finding, or
  NEEDS_FIX-without-prompt is a `VerdictParseError`

Any error ⇒ the executor marks the task (and pipeline) `BLOCKED` — a malformed
response can **never** become PASS.

---

## TASK_AUDITOR_IMPLEMENTATION

`Executor.run_task_audit()` — deterministic, Qt-free, off the UI thread through
the existing worker:

1. gate: `AUDITING_TASK` only (recovered from restart by `_ensure_work_phase`)
2. guards: a task exists, is `AUDITING`, and `audit_rounds < MAX_AUDIT_ROUNDS`
3. preflight for the `TASK_AUDITOR` role config/engine (same checks as Builder)
4. driver registry `create` + `start_session`; session policy via
   `SessionManager.decide()` (`persistent_per_batch` → NEW on first audit,
   REUSE on re-audit; resume failure ⇒ task FAILED)
5. persist `audit_rounds + 1` **before** the prompt (a crash mid-audit is not a
   never-audited task)
6. send the `AuditPacket`, wait for the real child, capture the real exit code
7. child failure ⇒ `FAILED`; malformed verdict ⇒ `BLOCKED`
8. record the real external auditor session id (SQLite `sessions` + task)
9. apply PASS (approve + `BATCH_COMPLETE`) / NEEDS_FIX (→ `FIX_REQUIRED`,
   persist fix_prompt) / BLOCKED (→ `BLOCKED`) always through
   `controller.transition()` — legal edges only.

No hardcoded Hermes path: the role config names the engine, and any registered
driver with `implemented=True` fills the auditor role unchanged.

---

## AUDITOR_SESSION_POLICY

`persistent_per_batch` (unchanged default, D-021):

- first audit in a batch → **NEW** auditor session
- re-audit after a fix → **REUSE** the same session via the proven `--resume`
- a new batch (`begin_new_batch()` generation bump) → **NEW** again

The real external auditor session id is persisted (`tasks.auditor_session_id`)
and mirrored in `sessions` with `external=1`. After a restart,
`SessionManager.restore_session()` rebinds it in memory and
`Executor._restore_auditor_session()` re-arms the per-batch decision without
contacting the engine.

---

## FIX_LOOP_IMPLEMENTATION

`Executor.run_task_fix()` — from `FIX_REQUIRED` only:

- requires the persisted `fix_prompt` (missing ⇒ `BLOCKED` — no free-form fixes)
- resolves the **BUILDER** role config; the session decision MUST be NEW
  (`always_new`) — a REUSE decision is refused and the task is blocked
- increments `attempts`, persists `RUNNING_FIX`, sends the self-contained fix
  packet to a brand-new session
- child failure ⇒ `FAILED`; success ⇒ `RUNNING_FIX → AUDITING_TASK`, task
  `AUDITING`, `fix_session_id` persisted, awaiting the re-audit.

The fix Builder never sees the prior Builder conversation; everything it needs
is in the fix packet and the actual repository state.

---

## MAX_AUDIT_ROUNDS

`MAX_AUDIT_ROUNDS = 3` with these exact semantics (documented in code and D-020):

| Round | What runs | If NEEDS_FIX |
|---|---|---|
| Audit 1 | initial audit | Fix 1 (allowed: rounds 1 < 3) |
| Audit 2 | re-audit after Fix 1 | Fix 2 (allowed: rounds 2 < 3) |
| Audit 3 | re-audit after Fix 2 | **BLOCKED** — rounds 3 >= 3; no third fix exists |

Both `run_task_audit` and `run_task_fix` enforce the cap (`audit_rounds >= 3`
blocks any further audit or fix before a process starts). No infinite loop is
reachable by construction; the cap tests and the audit-beyond-cap test pin it.

---

## SESSION_ISOLATION

Proven offline (`tests/test_audit_fix_loop.py`) and live (real smoke STEP 7):

- `ScriptedLoopDriver.resumed == [auditor_id]` — the auditor session is resumed
  exactly once, for the re-audit.
- the fix run's session id differs from the auditor's, and the fix driver
  started a new session (`always_new`).
- a restart rebind (`restore_session`) leads to REUSE, not a fresh auditor.

Persisted on the task for auditability: `builder_session_id`,
`auditor_session_id`, `fix_session_id` — all real external ids, never
synthesised (`NOT_EXPOSED` when the CLI exposes none).

---

## PERSISTENCE

Schema v3 with **one targeted upgrade path** `v2 → v3` (D-023, still no
migration framework):

- `tasks.latest_verdict` — last strict verdict value
- `tasks.verdict_json` — bounded serialised `AuditVerdictResult` (summary,
  findings, fix_prompt; never a transcript)
- `tasks.fix_prompt` — the deterministic correction
- `tasks.auditor_session_id` / `builder_session_id` / `fix_session_id`
- `batches.phase` — the pipeline phase, so restart restores the work phase

Everything is written and read in the same `save_batch`/`load_batch`
transaction as before; no giant transcripts are stored anywhere.

---

## RESTART_RECOVERY

- `load_pipeline_state` restores `phase` from `batches.phase`.
- `SessionManager.restore_session()` rebinds the persisted auditor session id.
- `Executor._ensure_work_phase()` walks legal edges from a freshly-restored
  `IDLE` machine to the required work phase (bookkeeping only).
- `next_task_action()` is task-state-driven and answers **AUDIT / FIX /
  RE_AUDIT / COMPLETE / BLOCKED / FAILED / IDLE** from the task row alone.
- **Starting the application never starts an AI process**: recovery only
  identifies the safe next action; the operator triggers it from the UI
  (asserted in `test_recovery_resumes_the_auditor_session_after_restart`).

---

## REAL_SMOKE_TEST

`scripts/session_003_audit_fix_smoke.py` — deliberately outside the unit suite.
It builds a fresh scratch git repo under the system temp dir:

- `calculator.py`: `def add(a, b): return a - b`  ← the deliberate defect
- `test_calculator.py`: `assert add(2, 3) == 5` (plain python, run by the smoke
  itself with `sys.executable`, no pytest dependency)
- a scratch application database under the same temp dir; the real
  `%LOCALAPPDATA%` state is untouched

Command actually run (three times — run 1 and run 2 proved the loop and each
exposed one smoke-script bug in STEP 8 (a loader, then a missing import); both
were fixed and neither affected the loop results; run 3 is the clean record):

```bash
python scripts/session_003_audit_fix_smoke.py --profile encomm-pipeline-control-center --keep-scratch
```

Flow and observed results (final run, the clean record):

| Step | Real action | Result |
|---|---|---|
| 1 | read-only profile discovery | ok, profile confirmed |
| 2 | seed defective scratch repo as task ready for audit | defect confirmed (`test_calculator.py` exits ≠ 0) |
| 3 | REAL Task Auditor (initial audit) | **NEEDS_FIX** round 1, structured verdict parsed, fix_prompt 396 chars, 1 finding |
| 4 | REAL fix in a **new** Builder session | COMPLETED (attempt 1); `calculator.py` fixed |
| 5 | independent deterministic test | exit code **0**, `test_calculator: OK` |
| 6 | re-audit, **same** auditor session resumed | **PASS** round 2; re-audit summary confirms `calculator.py now returns a + b`, test unmodified and passing |
| 7 | session identity proof | see the three IDs below |
| 8 | persisted evidence from SQLite | task APPROVED / PASS / rounds 2 / attempts 1 / session ids — `persisted round-trip ok: True`, batch status COMPLETE, stored phase BATCH_COMPLETE |

Maximum real model operations: **3** (initial audit, fix, re-audit). Failures
were diagnosed, never brute-forced. Event-log evidence from SQLite (final run):
`Task task_078063be NEEDS_FIX after audit round 1` → `fix completed (attempt 1);
fix session …` → `Task task_078063be PASSED audit round 2; the single-task
batch is complete.`

---

## AUDITOR_INITIAL_SESSION_ID

`20260922_193731_557d54` — reported by the Hermes CLI on the stream and stored
with `external=1`.

## FIX_BUILDER_SESSION_ID

`20260922_193815_4782be` — a genuinely new Builder session, distinct from the
auditor's.

## AUDITOR_REAUDIT_SESSION_ID

`20260922_193731_557d54` — equals `AUDITOR_INITIAL_SESSION_ID` (the re-audit
resumed the same session via `--resume`).

(All three ids are from the final clean run. Earlier verification runs used
auditor `20260922_193119_6cd0fb`/fix `20260922_193219_c6561f`, then auditor
`20260922_192003_d2ec6c`/fix `20260922_192105_8864df`; in every run the same
isolation/reuse identities held.)

```
AUDITOR_INITIAL_SESSION_ID == AUDITOR_REAUDIT_SESSION_ID  → True
FIX_BUILDER_SESSION_ID      != AUDITOR_INITIAL_SESSION_ID  → True
```

---

## REAL_SMOKE_INITIAL_VERDICT

**NEEDS_FIX** — initial audit of the deliberately defective scratch repo.

## REAL_SMOKE_FIX_RESULT

The fix Builder (new session) edited `calculator.py` to `return a + b`. The
smoke's independent test run: `test_calculator: OK`, exit code 0.

## REAL_SMOKE_FINAL_VERDICT

**PASS** — re-audit in the same auditor session; task `APPROVED`, pipeline
`BATCH_COMPLETE`, next action `COMPLETE`.

---

## FILES_CREATED

| File | Lines | Purpose |
|---|---|---|
| `src/encomm_pcc/domain/audit.py` | 120 | Audit verdict contract: enum, severity, findings, result model |
| `src/encomm_pcc/core/verdict_parser.py` | 310 | Strict fails-closed parser (bounded, JSON-only, whitelist verdicts) |
| `src/encomm_pcc/core/audit_packet.py` | 220 | Deterministic AuditPacket + fix-prompt builders |
| `scripts/session_003_audit_fix_smoke.py` | 380 | Real end-to-end audit/fix loop smoke (outside the suite) |
| `tests/test_verdict_parser.py` | 30 tests | Parser happy paths + full failure-case matrix |
| `tests/test_audit_fix_loop.py` | 25 tests | Loop: isolation, cap, failures, recovery, next-action |
| `tests/test_audit_packet.py` | 10 tests | Packet content, determinism, self-containment |

## FILES_CHANGED

| File | Change |
|---|---|
| `src/encomm_pcc/domain/models.py` | TaskStateRecord verdict/session fields; `BatchState.phase` |
| `src/encomm_pcc/domain/state_machine.py` | `AUDITING_TASK → BATCH_COMPLETE` edge |
| `src/encomm_pcc/domain/__init__.py`, `core/__init__.py` | Exports for the new audit contract/parser/packet |
| `src/encomm_pcc/core/executor.py` | Audit/fix dispatchers, cap, next-action, recovery, role-aware helpers |
| `src/encomm_pcc/core/session_manager.py` | `restore_session()` |
| `src/encomm_pcc/persistence/schema.sql`, `database.py` | Schema v3 + targeted v2→v3 upgrade + phase persistence |
| `src/encomm_pcc/ui/worker.py` | Worker actions dispatch/audit/fix |
| `src/encomm_pcc/ui/panels.py`, `main_window.py` | TaskPanel loop readouts + buttons; window wiring |
| `src/encomm_pcc/__init__.py` | `0.3.0` |
| `tests/test_executor.py` | Schema-version tests → v3; v1→v2→v3 and v2→v3 upgrade tests |
| `tests/test_imports.py` | Version assertion → 0.3.0 |
| `tests/test_state_machine.py` | New nominal edge in the graph test |
| `tests/test_ui_dispatch.py` | Audit-loop readout test |
| `README.md` | Claims updated to v0.3 |
| `docs/CURRENT_STATE.md`, `ARCHITECTURE.md`, `ROADMAP.md`, `DECISIONS.md` | v0.3 reality + ADRs D-019…D-024 |

---

## TESTS_RUN

```bash
python -m pytest                        # full offline suite
python -m pytest tests/test_verdict_parser.py -q
python -m pytest tests/test_audit_fix_loop.py tests/test_audit_packet.py -q
python -m pytest tests/test_ui_dispatch.py -q
python scripts/session_003_audit_fix_smoke.py --profile encomm-pipeline-control-center
git diff --check
# secret scan over the full raw diff · .gitignore/artifact check ·
# Hermes profile/config integrity check
```

## TEST_RESULTS

```
280 passed in ~5s   (0 failed, 0 skipped, 0 errors) — exit code 0
```

| File | Tests |
|---|---|
| existing 13 files (Session 002 baseline) | 221 |
| `test_verdict_parser.py` | 30 |
| `test_audit_fix_loop.py` | 25 |
| `test_audit_packet.py` | 10 |
| `test_ui_dispatch.py` (added readout test) | +1 (from 8 → 9) |
| `test_executor.py` (added v2→v3 upgrade test) | +2 (from 23 → 25, schema tests reworked) |
| `test_state_machine.py` | +0 (edge added to parametrized list) |
| `test_imports.py` | +0 (version value updated) |
| **Total** | **280** |

The suite is offline by construction: the Hermes path is exercised through
canned runners/fakes, and the only real engine runs are the two explicit smoke
scripts.

### DEFECTS_FOUND_AND_FIXED

| # | Defect | Root cause | Fix |
|---|---|---|---|
| 1 | Restart could not restore the work phase | `load_pipeline_state` hardcoded `phase=IDLE` | Persist `batches.phase` (schema v3); restore it in `load_pipeline_state` |
| 2 | `next_task_action` depended on the (unpersisted) phase | recovery would mis-declare the next step | Made it task-state-driven; phase is only a secondary signal |
| 3 | Re-audit after restart could not resume the auditor | in-memory `SessionManager` was empty on start | `restore_session()` + `_restore_auditor_session()` + `_ensure_work_phase()` |
| 4 | Smoke STEP 8 loaded a COMPLETE batch as "active" | `load_active_batch` excludes COMPLETE by design | STEP 8 now loads by batch id (found by the first smoke run; fixed and re-run) |
| 5 | Test-data bugs in new tests (wrong result counts, wrong reason tags, premature save) | authoring slips | fixed in the test files; the parser/executor behaviour was already correct |

---

## FAILURE_CASES

All offline-tested; each is provably non-green:

- PASS verdict parses; NEEDS_FIX parses (fix_prompt required); BLOCKED parses
- malformed JSON / missing verdict / invalid verdict value / non-object JSON
  → `VerdictParseError`
- oversized payload (raw > 200k), oversize summary/finding/fix_prompt, findings
  > 50 → rejected
- NEEDS_FIX without fix_prompt → rejected
- PASS with a critical/high finding, PASS with a fix_prompt → rejected
- auditor child process non-zero → task/pipeline `FAILED`
- fix Builder child process non-zero → task/pipeline `FAILED`
- max audit rounds exceeded → `BLOCKED`, no further fix/audit starts
- auditor session resumes correctly / fix Builder gets a fresh session (driver
  logs asserted)
- state persists and reloads (verdict, rounds, sessions, phase)
- no infinite state-machine path (cap tests)
- **no malformed response can become PASS** (parser + executor)

---

## UI_STATUS

`MainWindow` title is now `ENCOMM Pipeline Control Center — v0.3`. The TASK
section gained the Session 003 surface:

| Readout/control | Shows |
|---|---|
| **Next action** | `AUDIT` / `FIX` / `RE_AUDIT` / `COMPLETE` / `BLOCKED` (+ phase, batch status) |
| **Run Task Auditor** button | enabled only when next action is AUDIT/RE_AUDIT |
| **Run fix (NEW Builder session)** button | enabled only when next action is FIX; tooltip previews the fix_prompt |
| **Audit loop** line | latest verdict, auditor session id, fix/builder session id, findings severity counts |
| Task state line | state, task id, attempts, `audit rounds x/3` |
| Result box | report + the **strict audit verdict** (verdict/summary/findings/fix_prompt length) when an audit produced one |

Dispatch/audit/fix all run off the UI thread through the same `ExecutorWorker`
(`action` selects the executor method). Offscreen tests cover the readouts and
remain green.

---

## SECURITY_CHECK

| Check | Result |
|---|---|
| Secret pattern scan over the full raw diff (`sk-…`, `ghp_…`, `AKIA…`, private keys, `api_key=`, `secret=`, `password=`, `Bearer …`) | **`NO_SECRETS_FOUND`** |
| Model output stored | bounded excerpts (4000 chars) + the **bounded structured verdict** (80k cap) — no transcripts |
| `fix_prompt` executed anywhere | **never** — it is text handed to the Builder; the parser and executor have no shell/exec surface for it |
| Parser security | JSON-only, no `eval`/`exec`/YAML; bounded input and counts; exact verdict whitelist |
| Child environment | unchanged filtered env (D-015) — no `HERMES_*`/`PYTHONPATH` leak into auditor/fix children |
| Workspace boundary in prompts | both the AuditPacket and the fix packet hard-restrict tool use to the workspace path; the smoke adds an explicit CRITICAL BOUNDARY line |
| Hermes profiles/config modified | **none** — read-only discovery plus `-p <existing profile>`; profile listing unchanged |
| Runtime artefacts tracked | none — `*.db`, `*.db-wal`, `*.db-shm`, `logs/`, `.env*`, keys are ignored |
| API keys read/printed/logged | none |

---

## GIT_STATUS

**Repository:** `https://github.com/Xamposs/ENCOMM-PIPELIN-CONTROL-CENTER`
**Branch:** `main`
**Baseline:** `14f037ce77a585741e0eb96d23171107971a23ef`
**Commit:** `feat: add task auditor and capped fix loop`

Pre-commit verification, in the order the brief requires:

| Step | Command | Result |
|---|---|---|
| Re-read material changed source in full | raw reads of every file written/edited | done |
| Inspect the complete raw diff | `git add -A` + `git diff --cached` | reviewed |
| Full unit suite | `python -m pytest` | `280 passed` |
| Real Session 003 smoke | `python scripts/session_003_audit_fix_smoke.py …` | `SMOKE PASSED` (see REAL_SMOKE_TEST) |
| Inspect full auditor/fix/re-audit output | full stdout of all three steps | done |
| Independently run the scratch deterministic test | `python test_calculator.py` in the scratch repo | exit 0, `OK` |
| Verify the actual session IDs | STEP 7 assertions | `initial == re-audit`, `fix != auditor` |
| Whitespace / conflict markers | `git diff --check` | clean |
| Secret scan | pattern scan over the raw staged diff | `NO_SECRETS_FOUND` |
| Runtime DB/log/scratch artefacts ignored | `.gitignore` + staged path check | none staged |
| Hermes config/profiles not modified | profile listing before/after | unchanged |
| Only intended files changed | `git status --short` review | only project files |

Push: to `origin/main`, no force push, no other branch created. Remote state
re-verified after the push with `git rev-parse HEAD` and
`git rev-parse origin/main` (both point at the new commit).

---

## KNOWN_LIMITATIONS

1. **One task per batch.** A second dispatch into the same batch is rejected.
   No planner, no orchestrator, no batch editor.
2. **Loop is single-task and capped at 3 rounds.** A NEEDS_FIX on round 3
   blocks the task; a human must intervene.
3. **No Final Auditor / orchestrator / multi-task sequencing.**
   `READY_FOR_FINAL_AUDIT`/`FINAL_AUDIT_RUNNING` are unreachable graph nodes;
   a single-task PASS goes straight to `BATCH_COMPLETE`.
4. **No auto resume at start** (by design, D-023): recovery decides, the
   operator acts.
5. **`BatchStatus.BLOCKED` does not exist**; a blocked task leaves the batch
   status `RUNNING` — the authoritative signals are the pipeline phase and task
   state, both persisted.
6. **`CodexDriver`/`GenericCliDriver` remain placeholders.**
7. **No mid-prompt cancellation, no streaming surface.**
8. **A `--resume` failure fails the task** (audit proceeds only if the session
   resumes; a provider-side session loss is surfaced, never papered over).
9. **The auditor/Builder share the same profile/model in the smoke** (role
   configs are identical by design for the test; production can diverge them
   per role with no code change).
10. **UI tests are offscreen only.**
11. **No packaging.**

---

## RISKS

| # | Risk | Severity | Mitigation in place | Residual |
|---|---|---|---|---|
| R1 | A future session mistakes a remaining placeholder for a working engine | High | `implemented=False` + preflight gate + docs | Review discipline |
| R2 | The `executor_started` invariant is weakened later | High | launch-recorder derivation + pinned tests | Needs care with new drivers |
| R3 | A fabricated `PromptResult` is introduced | High | `simulated` flag; real runs never set it | Review discipline |
| R4 | **Unbounded audit/fix loop** (Session 002's open risk) | High | **Closed by construction**: `MAX_AUDIT_ROUNDS=3` enforced in both dispatchers; cap tests | Human must clear a BLOCKED task |
| R5 | A provider/session failure mid-loop costs money or stalls | Medium | per-prompt timeout + tree kill; a resume failure fails loudly, never slides into a fresh audit silently | Cost is inherent to real engines |
| R6 | Verdict schema drifts from the prompt packet | Medium | both defined from the same constants; the packet embeds the schema text; parser unit-tested | Manual when schema evolves |
| R7 | Hermes CLI flags drift on upgrade | Medium | contract lives in `hermes_cli.py`; capability flags evidence-gated; smokes re-prove live | Inherent to external CLI |
| R8 | `restore_session` rebuts a stale id after a batch restart | Medium | generation bump on `begin_new_batch()` makes old sessions ineligible; restore only from the persisted task row | None observed |
| R9 | Over-faithful model output (wraps the object in prose) | Low | markers + balanced-brace fallback + strict schema validation | A cleverly malformed payload is still BLOCKED, never PASS |

**Highest-priority item addressed this session:** R4 — the loop cap exists from
the first commit.

---

## NEXT_RECOMMENDED_SESSION

**Session 004 — Multi-task batches + Orchestrator.**

1. `ORCHESTRATOR` plans a batch of N tasks from a workspace/project brief;
   materialise and sequence tasks, honour the batch size from the UI.
2. Reuse the Session 003 audit/fix loop per task; drive
   `RUNNING_TASK → AUDITING_TASK → (PASS) → RUNNING_TASK (next task)` with the
   graph's existing `AUDITING_TASK → RUNNING_TASK` edge.
3. Multi-task boundary pausing (the D-018 "next task boundary" becomes real).
4. Per-task progress + batch status in the UI; `READY_FOR_FINAL_AUDIT` becomes
   reachable for a whole batch.
5. Phase 4 then wires the real Final Auditor.

**Do not start** planning/orchestration until the single-task audit/fix loop is
independently reviewed. Multi-task, Orchestrator, CodexDriver, Final Auditor
and automatic next-batch generation remain strictly out of scope for any
Session-004 kickoff that pre-empts that review. See `ROADMAP.md`.