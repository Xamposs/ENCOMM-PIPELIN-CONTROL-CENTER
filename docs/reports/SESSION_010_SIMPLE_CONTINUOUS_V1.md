# Session 010 — Simple Continuous V1: Core Wiring, One-Shot Recovery, Test Matrix

**Date:** 2026-09-27
**Branch:** `main` (Session 010 part 1: `07d5341`; part 2: `3028527`; docs: `5465d83`; release: this commit)
**Baseline:** 588 tests (Session 009) → **604 passed, 0 failed**
**Result: v1.0.0 — live acceptance PASSED.**

---

## 1. Mandate

Close the four reviewer-identified gaps from the Session 009 handoff brief:

- **(A)** Wire Simple Mode Continuous Run through the core `ContinuousRunner`
  (§7 invariant: no pipeline logic in the UI).
- **(B)** One-shot Coder recovery override (§14/§15).
- **(C)** Provider/Model fields for CODER/AUDITOR in Simple Mode (§34: same
  durable `AgentRoleConfig`, no parallel config surface).
- **(D)** Documentation rewrite (Simple-Mode-first).

Plus: the §20/§21 test matrix through the REAL UI/worker path, and honest
verification before any push.

## 2. Defects found while implementing (all fixed)

| # | Defect | Where | Fix |
|---|---|---|---|
| 1 | `start_executor_worker()` silently dropped `resume`/`project_brief`/`batch_size`/`next_batch_size` → TypeError on every advanced PLAN + START BATCH / RUN FINAL AUDIT click; zero test coverage | `ui/worker.py:87` | kwargs forwarded; regression test added |
| 2 | `_continuous_handoff()` re-planned with a NEW planning call after `start_next_batch()` — duplicate loop in the UI + zero-AI-handoff violation | `ui/main_window.py:546` | deleted; replaced by `_start_continuous_worker` → worker action `continuous` → core `ContinuousRunner` |
| 3 | Stale "READY FOR FINAL AUDIT — the batch stops here" message shown even with Continuous checked | `ui/main_window.py:613` | removed with the manual-handoff path |
| 4 | A mid-batch STOP was honoured by `BatchRunner` (which clears the stop flag at its boundary), so `_continue_loop` mislabeled the stop reason as PAUSED | `core/continuous_runner.py` | STOPPED outcome is now the deterministic stop signal; PAUSED reserved for real pauses |
| 5 | `tests/test_session_009.py` still called the removed `_apply_role_profile` | tests | migrated to `_apply_role_config` |

## 3. Implementation summary

- **`ui/worker.py`** — action set `dispatch|audit|fix|batch|final_audit|continuous|continuous_resume`; `continuous*` delegate to the core `ContinuousRunner` on the worker thread; `start_executor_worker` forwards all run kwargs.
- **`core/executor.py`** — one-shot recovery state: `arm_coder_recovery()` / `coder_recovery_armed()` / `clear_coder_recovery()` + private consume, wired into `_build` BEFORE the session-policy decision; bypasses the REUSE refusal exactly once; `always_new` untouched; resume failure fails honestly and still consumes.
- **`ui/main_window.py`** — `_start_continuous_worker(brief, size, resume)`, `_on_continuous_finished`, `_continuous_stop_message` (plain-language §8 stop reasons); report-capture attributes (`_batch_report`, `_continuous_report`, `_final_audit_report`) for testability.
- **`ui/simple_mode.py`** — Provider/Model for CODER + AUDITOR writing durable `AgentRoleConfig` (`_apply_role_config`); CODER RECOVERY OVERRIDE group (hidden by default) that preselects the saved interrupted session (`fix_session_id or builder_session_id`) via `_refresh_recovery`; profile-scoped session refresh; `_sync_from_controller` reads provider/model back.

All recorded as ADRs **D-048 / D-049 / D-050**.

## 4. Verification (all offline, zero AI calls, `QT_QPA_PLATFORM=offscreen`)

| Check | Result |
|---|---|
| `tests/test_session_010.py` (16 cases, §20 + §21) | **PASS** — continuous wiring through the REAL `MainWindow` → worker → core path: 2 batches, exactly ONE planning call, ONE zero-AI handoff, 2 final audits; STOP before the next final audit; PAUSE at a batch boundary; non-PASS final audit stops loudly; worker-kwargs regression; Simple provider/model persistence + round-trip; profile-scoped auditor discovery; stale-binding cleared on profile change; one-shot recovery matrix (UI preselects saved session; consumed exactly once; next op brand-new; restart-in-NEW clears; resume failure honest) |
| Full suite `python -m pytest` | **604 passed, 0 failed** |
| `scripts/session_004_multitask_smoke.py --fake` | SMOKE PASSED |
| `scripts/session_005_final_audit_smoke.py --fake` | SMOKE PASSED |
| Secret scan of both diffs | clean |

## 5. Documentation reconciliation (§22/§23)

- `docs/CURRENT_STATE.md` — rewritten for the Session 010 reality (0.9.0,
  Simple Mode default, continuous-through-core, one-shot recovery, D-048…050,
  604-test baseline, live acceptance named as the explicit remaining item).
- `docs/USER_GUIDE.md` — rewritten Simple-Mode-first (Part A: five controls,
  START/PAUSE/STOP + CONTINUOUS, session binding, recovery override) with the
  advanced reference condensed in Part B.
- `README.md` — Simple-Mode workflow, 600+ tests, v0.9 limitations (live
  acceptance pending = the 1.0.0 gate).
- `docs/DECISIONS.md` — D-048, D-049, D-050 appended.

## 6. Live mixed-engine acceptance (§25–§41) — PASSED 2026-09-27

Real run via `scripts/session_010_acceptance.py`
(`--coder-profile encomm-accounting-intelligence --auditor-profile
encomm-auditor --provider openrouter --model deepseek/deepseek-v4.1-flash`),
zero-AI proof completed via `--finalize-scratch` on the preserved evidence:

| # | Proof | Evidence |
|---|---|---|
| 1 | Deterministic scratch repo starts 2/2 RED | both tests exit non-zero before the run |
| 2 | REAL Codex Orchestrator plans exactly 2 tasks (read-only guard active) | thread `01a0e3a7-2f1d-7bc3-85ae-06bca5bca3bf` |
| 3 | 2 REAL Hermes builds in DISTINCT fresh sessions; both APPROVED | token totals: BUILD 228 482 total |
| 4 | ONE auditor session for the whole batch (`persistent_per_batch`) | `20260927_191817_517e8d` |
| 5 | REAL Codex Final Auditor: ONE call → strict PASS + exactly 4 next tasks | thread `01a0e3aa-3f78-7070-b45b-72297d376fb4`; AUDIT 160 753 tokens |
| 6 | Durable truth: batch COMPLETE, verdict PASS, pending plan unconsumed | SQLite reloaded in a fresh process |
| 7 | START NEXT BATCH materialises 4 PENDING tasks with ZERO AI calls | outcome READY, planning calls 0 |
| 8 | LIVE STOP-at-boundary: `resume_continuous()` with a pre-armed stop → STOP_REQUESTED, 0 planning calls, phase IDLE | continuous machinery exercised for real |

Both scratch tests still exit 0 after the run; the batch worktree was left
with only the two intended modules (read-only guards active on plan and
final audit throughout).

Method notes (honest ledger):

- The first attempt (v1 script) pre-armed STOP **before** `run_continuous`,
  which correctly stops at the batch ENTRY — zero model calls, no work. That
  exposed real entry semantics; the script was restructured to run batch 1
  explicitly.
- The second attempt PASSED all real legs but failed on an over-strict
  script assertion (final-auditor thread == planning thread). The code is
  correct: `same_as_orchestrator` shares the ENGINE; session continuity is
  per-role (D-034…D-038). The assertion was corrected to the documented
  contract, and a `--finalize-scratch` evidence-retry mode was added so the
  already-completed model legs were never repeated.
- A completed batch is terminal history: `load_active_batch` deliberately
  does not rehydrate it (D-023). The finalize path therefore asserts durable
  truth on disk, not in-memory state.

## 7. Honesty ledger

- The 604-test baseline and every claim in §4 are reproducible commands, not
  narratives.
- The continuous path is proven offline (604 tests) AND live (§6); the live
  run used real Codex + real Hermes (OpenRouter `deepseek-v4.1-flash`) model
  calls and its scratch evidence is preserved on disk.
- The recovery override is process-scoped by design (D-050); CURRENT_STATE §5
  states this. The live recovery-override acceptance (interrupting a real
  build) is deliberately listed as future work in CURRENT_STATE §7.
