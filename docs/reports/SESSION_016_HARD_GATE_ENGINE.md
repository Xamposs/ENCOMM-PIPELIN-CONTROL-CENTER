# Session 016 — Deterministic Hard-Gate Completion Engine

**Date:** 2026-10-02
**Branch:** `proposal-session-016` (created from `main` @
`fdaaf5d0f4b509b11a17f3843446310b2bc0a3f2`, the PR #4 merge commit, exact)
**Verdict:** ✅ **GREEN — full suite 1003 passed / 0 failed (937 baseline +
66 new), `git diff --check` clean, zero Coding Mode source changes, zero
model calls, zero network.**

## 1. Goal

Implement the REAL deterministic hard-gate engine and the FIRST legitimate
path to `ProposalPhase.COMPLETE`: strict reviewer contracts now feed 14
real validators over an operator-authored evidence contract — no fake PASS
results, no LLM compliance judgments, no fabricated evidence.

## 2. Baseline

| Item | Value |
|---|---|
| Baseline commit (pre-work HEAD) | `fdaaf5d0f4b509b11a17f3843446310b2bc0a3f2` (PR #4 merge, exact) |
| Pre-existing work found | None — `git status` clean before the branch was created |
| Branch | `proposal-session-016`, created from that exact commit |
| PRE-TEST RESULT | **937 passed, 0 failed** (Python 3.12 project interpreter) |
| POST-TEST RESULT | **1003 passed, 0 failed** (937 + 66 new) |

## 3. Architecture — what was added where

```
encomm_pcc.proposal                                  (PURE contracts)
    hard_gates.py            — evidence schema + loader (iteration/hash
                               binding), HardGateEvaluation/RunResult/
                               Disposition/FailureClass, canonical-order
                               evaluate_hard_gates(), registry validation
    hard_gate_validators.py  — the 14 REAL validators (one per canonical id)
        ↓ imports
   (nothing)

encomm_pcc.proposal_runtime                          (execution adapters)
    hard_gate_runner.py      — run_hard_gates(): state guard → freshness
                               precondition (reuses review_freshness.py) →
                               evidence binding → 14-gate evaluation →
                               disposition → state walk + artifacts
    hard_gate_artifacts.py   — durable per-iteration hard_gates.json
                               (immutable, conflict-fail-closed),
                               05_CONTROL/HARD_GATES.json snapshot
                               (current-state policy), HARD_GATE_FEEDBACK.json
```

* The pure modules import NOTHING outside the pure package (subprocess
  isolation re-pinned for both new modules).
* The runtime modules import no UI/persistence/core (subprocess re-pinned);
  no provider/engine name, no process/network surface, no transcript
  mechanism appears in any new module (structural tests).
* Coding Mode: ZERO production files touched (structural scan re-pinned).

## 4. Files added / modified

**Added (pure proposal package):**

| File | Content |
|---|---|
| `src/encomm_pcc/proposal/hard_gates.py` | `HARD_GATE_EVIDENCE_SCHEMA = "encomm-pcc.hard-gate-evidence/v1"`; `load_hard_gate_evidence()` (missing/zero-byte/corrupt/schema/applicability validation; iteration + exact-hash BINDING — stale evidence refused, never re-bound; explicit N/A requires `applicable=false` + non-empty reason; non-canonical gate id in applicability refused; non-object section refused); `HardGateEvaluation` (gate result + internal failure class; PASS/N-A can never carry a failure class); `HardGateRunResult` (ALWAYS the full canonical set in canonical order — `from_evaluations` derives the disposition: PROPOSAL_ISSUE → REVISION_REQUIRED > evidence classes → BLOCKED > WARN → INCOMPLETE > COMPLETE); `evaluate_hard_gates()` (explicit-N/A short-circuit, else registry validator, canonical order); `validate_gate_registry()` (exact canonical set; missing/duplicate/extra refused) |
| `src/encomm_pcc/proposal/hard_gate_validators.py` | The 14 REAL validators: MANDATORY_SECTIONS (ATX `#`…`######` parse, whitespace+case normalisation ONLY — no fuzzy/semantic matching); CHALLENGE_MAPPING (every required marker needs an explicit mapping record with proposal_section + evidence_text/reference); SOURCE_OF_TRUTH_INTEGRITY (SOURCE_REGISTRY + CLAIM_LEDGER; unique source/claim ids; every claim `source_ref` resolves; `conflicting=true` is a PROPOSAL_ISSUE; zero-byte seeds are NOT evidence); UNVERIFIED_CLAIMS (control file `unresolved` empty AND latest SAME-HASH review bundle claim-free); CITATION_VERIFICATION (every `requires_citation=true` claim `citation_verified=true` + ≥1 source_ref); TERMINOLOGY_CONSISTENCY (literal case-sensitive alias scan over the master text — no synonym detection); WP_TASK/DELIVERABLE/MILESTONE/PERSON_MONTH_CONSISTENCY (unique ids, exactly-one-WP tasks, required owner fields, reference resolution, Decimal-exact sums); BUDGET_CONSISTENCY (Decimal arithmetic — 0.1+0.2==0.3 pinned; negative amounts invalid; subtotals and declared total exact); SUBCONTRACTING_CORE_TASKS (entries must exist in the work plan, `core_task=true` subcontract is a PROPOSAL_ISSUE, justification required, `entries=[]` explicitly passes); PAGE_LIMIT (requires measurement_method/measured_pages/max_pages — no hardcoded limit; an explicit ESTIMATE method returns WARN, never PASS); INTERNAL_CONTRADICTIONS (`unresolved=[]` passes, entries are PROPOSAL_ISSUE) |

**Added (runtime package):**

| File | Content |
|---|---|
| `src/encomm_pcc/proposal_runtime/hard_gate_runner.py` | `run_hard_gates(workspace, state_machine, iteration_number)`: exact-phase guard (only HARD_GATE_VALIDATION) → canonical fingerprint → review-freshness precondition (`evaluate_review_freshness` reused; missing/unreadable bundle fails closed; stale ⇒ REVISION_REQUIRED + STALE_REVIEW, gates never run) → evidence binding (any `HardGateEvidenceError` ⇒ BLOCKED, exact reason) → deterministic 14-gate evaluation → durable artifacts → disposition walk (§20 COMPLETE / §21 REVISION_REQUIRED + feedback / §22 BLOCKED / INCOMPLETE stays at HARD_GATE_VALIDATION for operator re-run) |
| `src/encomm_pcc/proposal_runtime/hard_gate_artifacts.py` | `write_iteration_hard_gates()` — `04_REVIEWS/iteration_NNN/hard_gates.json`: schema `encomm-pcc.hard-gate-run/v1` (iteration, hash, all 14 gate results in canonical order, overall outcome, counts, evidence-file fingerprints); identical = safe reuse, ANY other existing content = `HardGateArtifactConflictError`, never clobbered; `update_latest_hard_gates_snapshot()` — `05_CONTROL/HARD_GATES.json` atomic current-state update (zero-byte seed replaced per the documented policy); `build_feedback_payload()`/`write_hard_gate_feedback()` — `encomm-pcc.hard-gate-feedback/v1`, deterministic structured remediation context only (failed gate ids, messages, evidence pointers, failure classes), no AI prose; deterministic JSON everywhere; no timestamps, no transcripts, no model/provider metadata |

**Modified:**

| File | Change |
|---|---|
| `src/encomm_pcc/proposal/__init__.py` | Pure-package exports for both hard-gate modules |
| `src/encomm_pcc/proposal_runtime/__init__.py` | Runtime exports for the two new modules |
| `src/encomm_pcc/__init__.py`, `pyproject.toml`, `README.md`, `tests/test_imports.py` | Version gate 1.1.0 → 1.2.0 |
| `docs/CURRENT_STATE.md` | §1: Session 015+015A merge state CORRECTED (PR #4, `fdaaf5d`, 937 baseline) + Session 016 state; §4 ADR rows D-061…D-064 corrected to MERGED + D-065…D-068 appended |
| `docs/DECISIONS.md` | Appended D-065…D-068. No existing ADR touched |

**Added (tests):** `tests/test_proposal_hard_gate_contracts.py` (39) and
`tests/test_proposal_hard_gate_executor.py` (27) — **66 new offline
tests**, all deterministic real-file/scripted cases, zero model calls.

## 5. Safety chain (the run path, in order)

```
state guard (EXACTLY HARD_GATE_VALIDATION)
  → fingerprint MASTER_PROPOSAL (canonical sha256-exact-bytes-v1)
  → freshness: current hash == latest review_bundle hash   ← D-063 reused
       stale  ⇒ HARD_GATE_VALIDATION → REVISION_REQUIRED  (gates NEVER run)
       absent ⇒ fail closed (never "fresh")
  → evidence bind: schema + iteration + exact proposal_hash ← D-065/D-066
       any problem ⇒ BLOCKED (exact reason; the proposal did NOT fail)
  → evaluate ALL 14 canonical gates in canonical order      ← pure, zero-AI
  → durable artifacts (04_REVIEWS immutable record + 05_CONTROL snapshot)
  → disposition:
       COMPLETE           ⇒ HARD_GATE_VALIDATION → COMPLETE   (FIRST time)
       REVISION_REQUIRED  ⇒ HARD_GATE_FEEDBACK.json → REVISION_REQUIRED
       BLOCKED            ⇒ BLOCKED (operator input missing)
       INCOMPLETE (WARN)  ⇒ stays at HARD_GATE_VALIDATION (re-runnable)
```

## 6. Test evidence (66 new, all offline/deterministic)

| Brief §25 area | Where | Tests |
|---|---|---|
| BASE CONTRACT (1–8) | `test_proposal_hard_gate_contracts.py` | 8 — canonical order (all 14), duplicate/missing/extra registry + incomplete evaluation set refused, hash mismatch, iteration mismatch, malformed (corrupt JSON / array / wrong schema / non-canonical applicability id / non-object section), zero-byte, explicit N/A with reason preserved, implicit N/A rejected (loader refuses reasonless N/A AND a missing section runs as EVIDENCE_MISSING, never N/A) |
| MANDATORY SECTIONS (9–11) | same | 3 — whitespace/case normalisation passes, missing heading = PROPOSAL_ISSUE → REVISION_REQUIRED, no fuzzy/substring matching + empty/non-array requirements = EVIDENCE_INVALID |
| CHALLENGE MAPPING (12–13) | same | 2 — all markers mapped passes, missing marker + evidence-less mapping record fail |
| SOURCE/CLAIMS (14–20) | same | 7 — valid registry+ledger pass, unregistered source_ref = EVIDENCE_MISSING, conflicting claim = PROPOSAL_ISSUE → REVISION_REQUIRED, control-file claims, same-hash review claims, citation-verified pass, unverified citation fail |
| TERMINOLOGY (21–22) | same | 2 — canonical-only passes (incl. unrelated names never flagged), forbidden alias fails |
| WORKPLAN (23–28) | same | 6 — valid plan passes all 4 gates, duplicate task relation + missing owner, deliverable ghost-task (PROPOSAL_ISSUE) + duplicate deliverable id (EVIDENCE_INVALID), milestone ghost ref, PM sums pass, PM mismatch (WP + global) + negative PM |
| BUDGET (29–31) | same | 3 — 0.1+0.2==0.3 Decimal-exact passes, participant + declared subtotal mismatches = PROPOSAL_ISSUE, negative amount = EVIDENCE_INVALID |
| SUBCONTRACTING (32–35) | same | 4 — explicit `entries=[]` passes, non-core justified passes, core subcontract = PROPOSAL_ISSUE, missing justification fails |
| PAGE (36–39) | same | 4 — authoritative within limit passes, over limit = PROPOSAL_ISSUE, estimate = WARN + INCOMPLETE disposition (never COMPLETE), missing/zero-byte = EVIDENCE_MISSING + BLOCKED |
| CONTRADICTIONS (40–41) | same | 2 — `unresolved=[]` passes, entries fail |
| Determinism (51 pure side) | same | 1 — two fresh workspaces ⇒ byte-identical evaluation dicts |
| FRESHNESS (42–43) | `test_proposal_hard_gate_executor.py` | 4 — stale ⇒ STALE_REVIEW + REVISION_REQUIRED with ZERO artifacts/gates, stale never COMPLETE, missing bundle fails closed, wrong-state refusal |
| OVERALL (44–50) | same | 7 — 14 PASS ⇒ COMPLETE (machine terminal), PASS + valid N/A ⇒ COMPLETE with the N/A reason in the report, one WARN ⇒ INCOMPLETE (machine stays, artifacts durable), one proposal FAIL ⇒ REVISION_REQUIRED, HARD_GATE_FEEDBACK.json schema/content pinned, missing evidence + stale-bound evidence ⇒ BLOCKED with distinct error, COMPLETE terminal re-run refused + graph validator green |
| ARTIFACTS (51–54) | same | 5 — durable artifact deterministic across workspaces + schema/order/counts/fingerprints, pre-seeded conflicting artifact fails closed with machine untouched + evidence preserved, zero-byte snapshot safely replaced + no temp residue + identical snapshots, no transcript/provider/model vocabulary in the artifact, artifact helper reuse/conflict contract |
| COMPOSITION (55–57) | same | 3 — real review cycle (scripted drivers) → zero-AI integration → HARD_GATE_VALIDATION → 14 PASS ⇒ COMPLETE end-to-end; proposal issue ⇒ REVISION_REQUIRED then the NEXT review iteration starts from REVISION_REQUIRED; evidence missing ⇒ BLOCKED |
| ISOLATION (58–60) | same | 6 — pure package + new pure modules subprocess-import-clean, runtime modules no UI/persistence/core (subprocess), Coding Mode production scan, no process/network/provider-SDK/transcript surface, no provider hardcoding |

```
PRE :  python -m pytest   → 937 passed  (baseline, unchanged HEAD fdaaf5d)
POST:  python -m pytest   → 1003 passed, 0 failed   (937 + 66 new)
git diff --check          → clean
```

> **Session 016A corrective note:** two hard-gate invariants are
> TIGHTENED on the same branch (no version bump, stays 1.2.0; full suite
> 1003 → **1021 passed, 0 failed**; zero Coding Mode changes, zero model
> calls, zero network):
>
> 1. **CLAIM_LEDGER claims carry the canonical `claim_id` ONLY.**
>    `SOURCE_OF_TRUTH_INTEGRITY` accepted a legacy fallback
>    (`claim.get("claim_id", claim.get("id"))`) that created schema drift
>    against `CITATION_VERIFICATION` (which always demanded `claim_id`).
>    The fallback is removed: the claim must be an object with a non-empty
>    string `claim_id`; a legacy-only `id` is FAIL/EVIDENCE_INVALID — no
>    migration, no guessing. Both claim gates now key on the SAME canonical
>    field, and the minimal-schema help text reads
>    `claims[{claim_id, source_ref, ...}]`.
> 2. **`COMPLETE` is protected by status AND failure-class coherence
>    invariants.** `HardGateEvaluation.__post_init__` now enforces the full
>    coherence matrix: PASS / NOT_APPLICABLE / WARN must carry
>    `failure_class=NONE`, and FAIL MUST carry a concrete class — so
>    `FAIL+NONE`, `PASS+PROPOSAL_ISSUE`, `WARN+EVIDENCE_*` and
>    `N/A+EVIDENCE_*` cannot be CONSTRUCTED (the `from_dict` path is
>    equally refused). Defense in depth at the engine level:
>    `HardGateRunResult.from_evaluations()` verifies before assigning
>    `COMPLETE` that EVERY status is PASS or NOT_APPLICABLE; any remaining
>    FAIL/WARN that was not already routed raises `HardGateEngineError`
>    instead of being silently coerced — an inconsistent evaluation can
>    never launder a FAIL into COMPLETE.
>
> 18 new offline regression tests (4 canonical-claim_id + 14
> coherence/disposition, in `test_proposal_hard_gate_contracts.py`);
> all 1003 pre-existing tests stay green unchanged.

## 7. Failure semantics (implemented exactly)

Wrong state ⇒ RUN_FAILED, machine untouched, no transition. Stale
proposal ⇒ STALE_REVIEW, machine → REVISION_REQUIRED, gates never run,
no artifacts. Missing/unreadable review bundle ⇒ fail closed (never
"fresh"). Missing/zero-byte/corrupt/mismatched evidence ⇒ BLOCKED with
the exact reason; the proposal is NOT reported as failing. Real
proposal-content failure ⇒ REVISION_REQUIRED with structured feedback.
WARN (estimate) ⇒ INCOMPLETE, machine stays re-runnable at
HARD_GATE_VALIDATION. Durable-artifact conflict ⇒ ARTIFACT_CONFLICT,
machine position untouched (D-060 convention), conflicting evidence
preserved. No failure path reaches COMPLETE.

## 8. Documentation changes

| File | Change |
|---|---|
| `docs/CURRENT_STATE.md` | §1 version section for 1.2.0; Session 015+015A merge state CORRECTED (PR #4, `fdaaf5d`, 937 baseline); Session 016 state added; §4 ADR rows D-061…D-064 marked merged + D-065…D-068 |
| `docs/DECISIONS.md` | Appended D-065 (deterministic + evidence-bound), D-066 (no missing evidence becomes PASS/N-A), D-067 (COMPLETE rule + WARN blocks), D-068 (proposal vs evidence failure split). No existing ADR touched |
| `docs/reports/SESSION_016_HARD_GATE_ENGINE.md` | This report |

## 9. Known limitations

1. The evidence contract is OPERATOR-AUTHORED by design: producing
   `HARD_GATE_EVIDENCE.json`, PAGE_BUDGET.json and the ledger files is
   the operator/tooling's duty; the engine validates, never generates.
2. UNVERIFIED_CLAIMS reads the LATEST review bundle and requires it to
   match the bound hash; an iteration-N run against an N-1 bundle is
   refused (stale evidence) — re-running the review cycle first is the
   operator's path.
3. TERMINOLOGY matching is deliberately literal (case-sensitive alias
   substrings); morphology/typo tolerance is out of scope by design.
4. The minimal ledger schema (`sources[{id,…}]`,
   `claims[{claim_id, source_ref, requires_citation, citation_verified,
   source_refs, conflicting}]`) is documented in the validators; richer
   legacy shapes are refused rather than guessed.
5. No UI, no PDF/DOCX, no web citation lookup, no SQLite persistence, no
   parallel reviewers, no real-model acceptance (brief §28; zero-AI
   validation consistent with Session 007+ discipline).
6. `run_hard_gates()` is single-shot by design; there is no autonomous
   loop and no automatic re-entry (brief §24).

## 10. Exact next recommended session (Session 017)

Proposal Mode UI/live acceptance: surface the proposal iteration +
hard-gate run in an operator surface (start iteration → review →
integration → gates → COMPLETE/REVISION_REQUIRED/BLOCKED readouts, plus
evidence-file authoring affordances), then the first LIVE end-to-end
acceptance over a real mini proposal. Explicitly still out of scope:
DOCX/PDF generation, web citation lookup, parallel reviewers, SQLite
migration.

## GIT_STATUS

- Branch: `proposal-session-016`
- Commit: `feat(proposal): add deterministic hard-gate completion engine (Session 016)`
- Pushed to `origin/proposal-session-016`; NOT merged to `main` (per brief).
