# Session 014 — Deterministic Three-Reviewer Proposal Review Cycle

**Date:** 2026-10-02
**Branch:** `proposal-session-014` (created from `main` @ `7ff806861955bfb698ed3512d2e9f1709de9180e`, the PR #2 merge commit, exact)
**Verdict:** ✅ **GREEN — full suite 822 passed / 0 failed (759 baseline + 63 new), `git diff --check` clean, zero Coding Mode source changes.**

## 1. Goal

Turn the one-review Session 013 runtime into a REAL deterministic
three-reviewer proposal review cycle:
SOURCE_VALIDATION → SCIENTIFIC_REVIEWER → PROPOSAL_ENGINEER →
RED_TEAM_REVIEWER → deterministic aggregation → integration brief →
INTEGRATION. Durable structured review artifacts, a same-revision guarantee
across all three reviewers, and file-based safe resume — while preserving
Coding Mode and every Session 012/013 boundary. No UI, no INTEGRATION
executor, no hard gates, no score, no parallel reviewers, no real model
calls.

## 2. Baseline

| Item | Value |
|---|---|
| Baseline commit (pre-work HEAD) | `7ff806861955bfb698ed3512d2e9f1709de9180e` (PR #2 merge, exact) |
| Local main fast-forwarded | `c82d688` → `7ff8068` (`git pull --ff-only`, clean) |
| Pre-existing work found | None — `git status` clean before the branch was created |
| Branch | `proposal-session-014`, created from that exact commit |
| PRE-TEST RESULT | **759 passed, 0 failed** (Python 3.12 project interpreter) |
| POST-TEST RESULT | **822 passed, 0 failed** (759 + 63 new) |

## 3. Architecture — what was added where

```
encomm_pcc.proposal_runtime                        (execution adapters)
    run_review_cycle()        — the cycle orchestrator (review_loop.py)
    freeze_pre_review_version() — 06_VERSIONS/ freeze (version_freeze.py)
    ReviewArtifactWriter / persist_cycle_artifacts — 04_REVIEWS/ writers
    run_review()              — Session 013 executor, UNCHANGED
        ↓ imports
encomm_pcc.proposal                                (PURE contracts)
    review_aggregation.py     — NEW: aggregate_reviews() + brief (D-059)
        ↓ imports
   (nothing)
```

* `encomm_pcc.proposal.review_aggregation` imports NOTHING outside the pure
  package (subprocess isolation re-pinned by tests).
* The runtime gained THREE modules; dependency direction unchanged
  (`runtime → proposal + generic driver infrastructure`). The two writers
  are the ONLY file writers in the package (structurally pinned); the
  Session 013 executor remains write-free.
* Coding Mode: ZERO production files touched (structural test pins that no
  coding package even contains the word "proposal").

## 4. Files added / modified

**Added (pure proposal package):**

| File | Content |
|---|---|
| `src/encomm_pcc/proposal/review_aggregation.py` | `ProposalCycleVerdict`, `AggregatedFinding`/`AggregatedPatch`, `ProposalReviewBundle` (with `duplicate_of_index` marking, severity counts, round-trip), `aggregate_reviews()` (role/iteration validation + BLOCKED>NEEDS_REVISION>PASS rule + stable severity→reviewer→original ordering + exact-duplicate MARKING, NO semantic dedup, NO score), `build_integration_brief()` (schema `encomm-pcc.integration-brief/v1`, reviewer verdicts, ordered items, severity counts, sorted affected sections/source refs, `integration_required`) |

**Added (runtime package):**

| File | Content |
|---|---|
| `src/encomm_pcc/proposal_runtime/review_loop.py` | `run_review_cycle()` — entry at IDLE/SOURCE_VALIDATION/review phases; full source validation BEFORE any driver (workspace dir, master exists/non-empty/regular/readable, fingerprint, bounded snapshot, snapshot-text==fingerprinted-bytes, positive int iteration); version freeze with the cycle hash; SOURCE_VALIDATION→SCIENTIFIC_REVIEW edge; per-reviewer hash guard (change ⇒ STALE_PROPOSAL, next reviewer never launched); packets built over the SAME frozen snapshot inputs (reviewer independence — no earlier reviewer output injected; `previous_findings` only from earlier iterations via the existing packet field); existing `run_review()` reused per reviewer; per-review artifact written AS each reviewer completes; BLOCKED stops the sequence via the D-055 edge (with durable evidence, no bundle/brief); executor MUTATION_DETECTED ⇒ STALE_PROPOSAL; pre-driver guard error (proposal vanished) ⇒ STALE_PROPOSAL; hash re-verified before aggregation; `aggregate_reviews()` + `persist_cycle_artifacts()`; final state exactly INTEGRATION; report `ProposalReviewCycleReport` (outcome/iteration/hash/revision/state before+after/completed roles/execution reports/bundle/brief path/frozen path/error/failed_role, JSON-friendly) |
| `src/encomm_pcc/proposal_runtime/version_freeze.py` | `freeze_pre_review_version()` — exact-byte `.md` + sidecar JSON (iteration/revision/hash/`sha256-exact-bytes-v1`/relpath) from ONE read; atomic; identical-existing = safe reuse; any conflict = `VersionFreezeError`; `format_iteration_name()` zero-padding contract (1..999) |
| `src/encomm_pcc/proposal_runtime/review_artifacts.py` | `ReviewArtifactWriter` + `persist_cycle_artifacts()` — `04_REVIEWS/iteration_NNN/` layout (3 review JSONs + bundle + brief); `encomm-pcc.review-artifact/v1`; whitelisted structured fields (schema/iteration/revision/hash/role/result/runtime metadata/session/driver id); deterministic JSON (indent=2, sort_keys, LF, EOF newline); atomic writes; conflict-fail-closed (`ArtifactConflictError`: identity mismatch, content mismatch, corrupt, any existing bundle/brief); `load_existing_review_result()` for resume; reused roles are never rewritten |

**Added (tests):** `tests/test_proposal_review_loop.py` (41 tests),
`tests/test_proposal_review_artifacts.py` (22 tests).

**Modified:** `src/encomm_pcc/proposal/__init__.py` (pure-package exports),
`src/encomm_pcc/proposal_runtime/__init__.py` (package exports),
`src/encomm_pcc/__init__.py` + `pyproject.toml` + `README.md` +
`tests/test_imports.py` (version gate 1.0.1→1.0.2),
`tests/test_proposal_runtime.py` (ONE structural test updated BY DESIGN —
see §8), and the documentation files in §10.

## 5. Source validation (before ANY driver call)

`_validate_sources()` fails closed with typed reasons: `workspace_missing`,
`master_proposal_missing`, `master_proposal_not_a_file`,
`master_proposal_unreadable`, `master_proposal_empty` (whitespace-only
refused), `fingerprint_failed`, `snapshot_failed`,
`snapshot_hash_mismatch` (snapshot text must be the fingerprinted bytes),
`invalid_iteration_number`. Source-of-truth files may be empty/absent in an
early workspace — the packet renders them honestly unavailable; absence of
official requirements is never converted into fake compliance. A failed
validation takes the explicit SOURCE_VALIDATION→FAILED edge (IDLE→FAILED is
not a legal edge — the cycle transitions IDLE→SOURCE_VALIDATION first);
validation succeeded + freeze conflict parks the machine at
SOURCE_VALIDATION with `ARTIFACT_CONFLICT`.

## 6. Same-revision guarantee

`cycle_hash` = SHA-256 of the exact bytes read ONCE at cycle start; the
freeze is written from that same read. Before EACH reviewer the live file
is re-fingerprinted: a change ⇒ `STALE_PROPOSAL`, remaining reviewers never
launched, nothing aggregated. The executor's own before/after immutability
guard additionally refuses a reviewer that writes the proposal mid-call
(MUTATION_DETECTED ⇒ cycle STALE_PROPOSAL, result refused, no artifact).
After the third reviewer the hash is re-verified AGAIN before aggregation;
`aggregate_reviews()` additionally refuses mixed roles/iterations. Tests
prove the stale case at every seam (between sci→impl, impl→red, after red)
and that the next driver's call count stays 0.

## 7. Artifacts, freeze, resume

* `04_REVIEWS/iteration_001/{scientific_review,implementation_review,red_team_review,review_bundle,integration_brief}.json`
* `06_VERSIONS/iteration_001_pre_review.md` + `.json` sidecar (hash/algorithm/iteration/revision/relpath)
* Deterministic JSON: `indent=2, sort_keys=True`, UTF-8, LF, EOF newline —
  byte-identical across identical runs (test-pinned).
* Atomic: same-directory temp + `os.replace`; no temp residue (test-pinned).
* Never clobber: identical existing per-review artifact = already-completed
  evidence (safe); identity/content conflict = typed error, cycle stops;
  previous iterations are never touched (test-pinned).
* Resume: machine at IMPLEMENTATION_REVIEW (or RED_TEAM_REVIEW) + the
  earlier phases' artifacts ⇒ those reviewers are REUSED (driver call
  count 0, proven), the remaining ones run. Missing artifact for a
  completed position, artifact at the current position, wrong iteration or
  wrong hash ⇒ `ARTIFACT_CONFLICT` with zero driver calls.

## 8. Test evidence (63 new, all offline/deterministic)

> **Session 014A corrective note:** BLOCKED-review artifact conflicts are
> now FAIL-CLOSED and test-pinned. The BLOCKED persistence path inside
> `run_review_cycle()` previously swallowed `ArtifactConflictError`
> (`except: pass`); it now reports `ARTIFACT_CONFLICT` with the real error
> and `failed_role`, launches no later reviewer, and never touches the
> conflicting evidence — the machine legitimately stays at BLOCKED (the
> D-055 edge is taken before persistence and is not reversed). A regression
> test pins the race window (conflicting artifact appears after the loop's
> initial lookup, during the reviewer call) and the conflict-free BLOCKED
> path is pinned as still persisting valid evidence. Full-suite count
> becomes 824 with this commit; an audit confirmed no silent
> `ArtifactConflictError` swallowing remains anywhere in the runtime.

| Brief area | Tests |
|---|---|
| Source validation (1–5 + extras) | 9 — IDLE→SOURCE_VALIDATION entry, explicit SOURCE_VALIDATION entry, per-phase call observation, missing/empty master before drivers, oversized-SOT honesty, snapshot hash mismatch, invalid iteration, missing driver role, bad entry phase |
| Happy path (6–14) | 2 — full contract (order, same hash in all 3 prompts, INTEGRATION, aggregate PASS, brief `integration_required=false`, exactly 5 artifacts, byte-exact freeze + sidecar contract, role focus + metadata in prompts, no envelope/transcript in artifacts) + cross-workspace determinism |
| Needs revision (15–18) | 3 — sci/impl NEEDS_REVISION still reviews the rest, aggregate + `integration_required=true` |
| BLOCKED (19–22) | 3 — stop at each position, state BLOCKED, no bundle/brief from red-team BLOCKED, durable evidence preserved |
| Operational failures (23–26) | 4 — parser/driver/malformed stops, no partial aggregate |
| Revision integrity (26–31 + extras) | 5 — mutation between reviewers ×2, mutation after final reviewer (executor guard refuses; no evidence/bundle/brief), same-hash structural pin, mid-driver mutation ⇒ STALE_PROPOSAL |
| Resume (36–40 + extras) | 8 — reuse from IMPLEMENTATION_REVIEW and RED_TEAM_REVIEW (call counts), conflicting/wrong-iteration/wrong-hash artifacts fail closed with 0 calls, missing-artifact-for-position, freeze conflict, freeze safe reuse |
| Artifacts (32–38) | 10 — atomicity, deterministic JSON, identical-safe, content-conflict, previous-iteration untouched, zero-padding bounds, transcripts never persisted + metadata present, bundle/brief conflict, corrupt artifact |
| Aggregation | 9 — verdict rules ×3, ordering matrix, exact-duplicate marking, mixed iteration, role mismatch, round-trip, brief contract, clean-PASS brief |
| Version freeze | 3 — exact bytes + sidecar, reuse, conflict/missing |
| Isolation (41–44) | 4 — runtime imports no UI/persistence/PySide6 (subprocess), pure package still isolated (subprocess), Coding Mode imports no proposal (subprocess), zero coding production file mentions proposal |

```
PRE :  python -m pytest   → 759 passed  (baseline, unchanged HEAD 7ff8068)
POST:  python -m pytest   → 822 passed, 0 failed   (759 + 63 new)
git diff --check          → clean
```

**Contract update by design (1 test):** Session 013's
`test_33_no_full_transcript_persistence_is_introduced` asserted NO file
writers anywhere in `proposal_runtime` — a Session 013 boundary snapshot the
Session 014 brief legitimately supersedes for the two artifact writers. The
test now pins the SHARPENED contract: no DB machinery anywhere in the
package; the executor write-free (no `open(`/`write*`); the writers never
touch `.raw_excerpt` nor serialise the whole execution report.

## 9. Failure semantics (implemented exactly)

SCIENTIFIC failure ⇒ implementation/red-team never called; implementation
failure ⇒ red-team never called; red-team failure ⇒ no aggregation; any
BLOCKED ⇒ stop (valid review, D-055 edge, durable evidence, no
bundle/brief); MASTER_PROPOSAL change between/after calls ⇒
STALE_PROPOSAL stop; artifact conflict ⇒ stop; aggregation validation
failure ⇒ stop. No failure path transitions to INTEGRATION; only a
complete, valid, same-revision 3-reviewer cycle finishes there.

## 10. Documentation changes

| File | Change |
|---|---|
| `docs/CURRENT_STATE.md` | §1 stale merge-state CORRECTED (Session 013+013A merged via PR #2, commits listed, 759 baseline) + Session 014 state; version section rewritten for 1.0.2; §4 ADR rows for D-059/D-060 |
| `docs/DECISIONS.md` | Appended D-059 (pure deterministic aggregation contract, no score) and D-060 (artifacts as ONLY persistence, conflict-fail-closed, file-based resume). No existing ADR touched |
| `docs/reports/SESSION_014_THREE_REVIEWER_CYCLE.md` | This report |

The Session 013 report is intentionally unchanged (historical record; the
merge state it lacked now lives in CURRENT_STATE §1).

## 11. Known limitations

1. The cycle ends AT INTEGRATION; no ORCHESTRATOR integration executor
   exists — `MASTER_PROPOSAL.md` is still never written by this system
   (D-054 write authority unchanged; Session 015's work).
2. No revision ITERATION yet: aggregation is produced once per cycle;
   automatic re-looping after INTEGRATION (REVISION_REQUIRED) is out of
   scope (per brief §17).
3. Resume requires the state machine AND artifacts to agree exactly; there
   is no reconciliation for a machine advanced past missing evidence
   (deliberate: fail closed beats guess).
4. No UI, no hard-gate validators, no scorecard, no 0–100 score, no
   parallel reviewers, no real-model acceptance, no PDF/RTF ingestion, no
   DOCX/PDF generation, no SQLite migrations, no web citation
   verification (all per brief §17).
5. `previous_findings` is a caller-supplied string tuple (existing packet
   field); Session 015 can feed it from the previous iteration's bundle.
6. Zero-AI validation: the full matrix is proven with scripted drivers —
   consistent with every Session 007+ discipline (real-model acceptance is
   an explicit out-of-scope item, not a silent deviation).

## 12. Exact next recommended session (Session 015)

**INTEGRATION executor (ORCHESTRATOR becomes the sole writer):**

1. Consume `integration_brief.json` (D-059 contract) in an
   ORCHESTRATOR-driven INTEGRATION executor inside `proposal_runtime`:
   ordered findings/patches in, reviewer-validated edit proposal out.
2. The ORCHESTRATOR applies accepted patches to MASTER_PROPOSAL.md during
   INTEGRATION (the ONLY writer, per D-054), then re-fingerprints; a
   changed hash produces the NEXT iteration's freeze (iteration N+1,
   `previous_findings` from the N-th bundle) and walks
   INTEGRATION→HARD_GATE_VALIDATION or REVISION_REQUIRED per the graph.
3. Keep every Session 014 guarantee: same-revision guards now extend to
   the writer (hash must equal the brief's `proposal_hash` before the
   first edit), artifacts stay conflict-fail-closed, transcripts never.

Explicitly NOT yet: UI, hard-gate validators, score, parallel reviewers,
DOCX/PDF, database persistence.

## GIT_STATUS

- Branch: `proposal-session-014`
- Commit: `feat(proposal): add deterministic three-reviewer cycle (Session 014)`
- Pushed to `origin/proposal-session-014`; NOT merged to `main` (per brief).
