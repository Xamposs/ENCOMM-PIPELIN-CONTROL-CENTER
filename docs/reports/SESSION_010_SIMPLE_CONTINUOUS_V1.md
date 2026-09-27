# Session 010 — Simple Continuous V1: Core Wiring, One-Shot Recovery, Test Matrix

**Date:** 2026-09-27
**Branch:** `main` (Session 010 part 1: `07d5341`; part 2: `3028527`)
**Baseline:** 588 tests (Session 009) → **604 passed, 0 failed**

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

## 6. What live acceptance must still prove (before 1.0.0)

1. A real 2-task continuous run: Codex Architect thread plans AND final-audits
   (same thread), Hermes Coder builds in distinct fresh sessions, ONE Hermes
   Auditor session for the batch, final PASS → next batch materialised with
   zero AI calls → operator STOP honoured at the earliest safe boundary.
2. A controlled restart mid-batch: auditor continuity (D-047) and the Builder
   recovery affordance offering the interrupted session.
3. The Windows rebuild + packaged `--smoke-test` + one packaged GUI launch.

## 7. Honesty ledger

- The 604-test baseline and every claim in §4 are reproducible commands, not
  narratives.
- The continuous path is proven offline only; no live model call was made in
  Session 010.
- The recovery override is process-scoped by design (D-050); CURRENT_STATE §5
  states this.
