# Session 012 — Proposal Mode Domain Foundation

**Date:** 2026-10-01
**Branch:** `proposal-mode` (created from `main` @ `6a58b430e3ad9683d5588721c8587678b164c303`)
**Verdict:** ✅ **GREEN — full suite 667 passed / 0 failed (620 baseline + 47 new), `git diff --check` clean, secret scan clean, zero Coding Mode changes.**

## 1. Goal

Create the isolated architectural FOUNDATION for a "Proposal Mode" — a future
autonomous multi-agent EIC/Horizon proposal review/integration loop — as a
parallel domain beside the existing Coding Mode. FOUNDATION ONLY: no UI, no
model calls, no validators, no persistence schema change, no prompt packets.

## 2. Baseline

| Item | Value |
|---|---|
| Baseline commit (pre-work HEAD) | `6a58b430e3ad9683d5588721c8587678b164c303` (v1.0.1) |
| Pre-existing work found | None — `git status` clean before the branch was created |
| Branch | `proposal-mode`, created from that exact commit |
| PRE-TEST RESULT | **620 passed, 0 failed** (Python 3.12 project interpreter) |
| POST-TEST RESULT | **667 passed, 0 failed** (29.3 s, same interpreter) |

## 3. Files added (all new; nothing else touched)

```
src/encomm_pcc/proposal/__init__.py        package exports (imports NOTHING from coding-mode packages)
src/encomm_pcc/proposal/enums.py           ProposalRole, ProposalPhase, ProposalReviewVerdict,
                                           ProposalFindingSeverity, ProposalHardGateStatus,
                                           HARD_GATE_IDS(_TUPLE), PROPOSAL_REVIEW_VERDICTS,
                                           HARD_GATE_STATUS_VALUES
src/encomm_pcc/proposal/models.py          ProposalAgentConfig, ProposalFinding, ProposalPatch,
                                           ProposalReviewResult, ProposalHardGateResult,
                                           ProposalIterationRecord (+ proposal-local utc_now)
src/encomm_pcc/proposal/state_machine.py   PROPOSAL_TRANSITIONS, ProposalStateMachine,
                                           InvalidProposalTransitionError, validate_proposal_graph
src/encomm_pcc/proposal/workspace.py       ProposalWorkspace, PROPOSAL_WORKSPACE_DIRS,
                                           SOURCE_OF_TRUTH_FILES, EVIDENCE_FILES, CONTROL_FILES,
                                           MASTER_PROPOSAL_RELPATH, workspace_paths
tests/test_proposal_foundation.py          47 tests (the mandated 14-area matrix)
docs/reports/SESSION_012_PROPOSAL_MODE_FOUNDATION.md   this report
```

**Existing files modified:** documentation only —

| File | Change |
|---|---|
| `docs/CURRENT_STATE.md` | §1 version note + new §1b "Proposal Mode foundation" paragraph + §3 tree addition + §4 D-054 row |
| `docs/DECISIONS.md` | Appended D-054, D-055, D-056 (no existing ADR touched) |

No production source file was modified. Zero imports were added to any
existing module.

## 4. Exact architectural decisions

1. **Fully isolated parallel domain.** `encomm_pcc.proposal` imports nothing
   from `encomm_pcc.core/domain/drivers/persistence/ui`. A subprocess test
   proves no coding-mode module lands in `sys.modules` when the proposal
   package is imported, and no `PySide6` module either (extends the existing
   D-layer Qt-free rule). Proposal types are distinct types from the coding
   ones (`ProposalPhase is not PipelinePhase`), even where values coincide.
2. **`ProposalRole` = {ORCHESTRATOR, SCIENTIFIC_REVIEWER,
   PROPOSAL_ENGINEER, RED_TEAM_REVIEWER}.** No second independent
   "integrator AI": integration authority is documented as a future DUTY of
   the ORCHESTRATOR (editor / final authority over the master proposal).
3. **Explicit deterministic transition graph** (D-055). Legal edges only:
   `IDLE → SOURCE_VALIDATION → SCIENTIFIC_REVIEW → IMPLEMENTATION_REVIEW →
   RED_TEAM_REVIEW → INTEGRATION → HARD_GATE_VALIDATION → {COMPLETE,
   REVISION_REQUIRED, BLOCKED, FAILED}`; `REVISION_REQUIRED →
   SCIENTIFIC_REVIEW` (same application state, fresh iteration);
   `PAUSED` from any active/REVISION phase with a recorded resume target;
   `BLOCKED` recovers only via explicit edges (source re-check, review
   restart, hard-gate re-run, pause, fail). `COMPLETE` and `FAILED` are
   terminal: **`COMPLETE` has zero outgoing edges by construction** — it
   cannot silently reopen; `FAILED` exits only through the explicit
   operator `reset()` escape hatch. Every active phase may also go
   `BLOCKED`/`FAILED`/`PAUSED`. Invalid edges raise
   `InvalidProposalTransitionError` and leave the phase unchanged.
4. **Models mirror repo conventions** (`str`-subclass enums;
   `@dataclass(slots=True)`; `to_dict`/`from_dict` to JSON-primitive
   mappings; never-fabricate-a-PASS coercion to BLOCKED on bad verdict data,
   mirroring `domain/audit.py`). No provider/engine/model name exists
   anywhere in the domain — `ProposalAgentConfig` carries them as runtime
   string configuration. `ProposalPatch` is explicitly a REVIEWER PROPOSAL
   (never an edit); `ProposalIterationRecord.proposal_hash` is a
   caller-computed deterministic fingerprint field (the foundation defines
   the field, not the hash function).
5. **Hard-gate contract without validators** (D-056). The 14 canonical gate
   identifiers are constants; `ProposalHardGateResult.__post_init__`
   REFUSES any non-canonical `gate_id` (fail closed), so no fabricated or
   drifted identifier can enter a result. No validator logic exists and no
   result may be synthesised.
6. **Idempotent workspace contract** (D-054). `ProposalWorkspace.initialize()`
   creates missing contract directories and seeds absent files with empty
   content (open mode "x"); existing files are never overwritten — proven
   for `MASTER_PROPOSAL.md`, the source-of-truth files and control files,
   including a re-init-over-user-content test. Paths are pure
   `pathlib.Path` arithmetic; `path_for()` normalises Windows separators,
   whitelists canonical paths and refuses traversal outside the root. The
   workspace is NOT required to be a git repository and the module never
   touches git.
7. **Write-authority invariant encoded in the models/docs** (Section 4 of
   the brief): only the Proposal ORCHESTRATOR will hold write authority over
   `03_PROPOSAL/MASTER_PROPOSAL.md`; reviewers are read-only with respect to
   it and their entire output is a structured `ProposalReviewResult` with
   patch PROPOSALS. The workspace module gives reviewers no write surface
   for the master proposal; no execution exists yet.

## 5. Proposal state machine

```
IDLE ──▶ SOURCE_VALIDATION ──▶ SCIENTIFIC_REVIEW ──▶ IMPLEMENTATION_REVIEW
     ──▶ RED_TEAM_REVIEW ──▶ INTEGRATION ──▶ HARD_GATE_VALIDATION
        ├─▶ COMPLETE                (terminal — no outgoing edges)
        ├─▶ REVISION_REQUIRED ──▶ SCIENTIFIC_REVIEW   (fresh iteration)
        ├─▶ BLOCKED
        └─▶ FAILED                  (terminal — reset() escape hatch only)
```

Every active phase (SOURCE_VALIDATION…HARD_GATE_VALIDATION) plus
REVISION_REQUIRED may enter `PAUSED` (recorded resume target) and
`BLOCKED`/`FAILED`. BLOCKED recovers only to {SOURCE_VALIDATION,
SCIENTIFIC_REVIEW, HARD_GATE_VALIDATION, PAUSED, FAILED}.

## 6. Proposal workspace contract

```
00_SOURCE_OF_TRUTH/  MASTER_BLUEPRINT.md · PROJECT_FACTS.md · TEAM.md ·
                     ARCHITECTURE.md · TERMINOLOGY.md     (seed-if-absent, never overwritten)
01_OFFICIAL/
02_EVIDENCE/         SOURCE_REGISTRY.json · CLAIM_LEDGER.json · AI_USAGE_LOG.json
03_PROPOSAL/         MASTER_PROPOSAL.md   (ORCHESTRATOR write authority ONLY — future)
04_REVIEWS/
05_CONTROL/          SCORECARD.json · HARD_GATES.json · ISSUES.json ·
                     CONTRADICTIONS.json · UNVERIFIED_CLAIMS.json · PAGE_BUDGET.json
06_VERSIONS/
07_FINAL/
```

## 7. Tests added (47, `tests/test_proposal_foundation.py`)

| Brief area | Tests |
|---|---|
| 1 roles exact | 3 |
| 2 determinism, 3 invalid rejected, 4 COMPLETE closed, 5 revision iteration | 12 (incl. graph self-validation, pause/resume, BLOCKED recovery set, terminal FAILED) |
| 6 model round-trips | 10 (all six models, multi-row/nested data, JSON text round-trip, non-canonical gate id refused, no-PASS-from-bad-data) |
| 7–11 workspace | 11 (dirs, idempotence, seed set, master/SOT never overwritten, re-init over user content, Windows paths, traversal refused, non-canonical refused, git-free, pure path helper) |
| 12 gate identifiers | 4 (exact tuple, uniqueness=14, status set, verdict/severity sets) |
| 13 no PySide6, 14 coding-mode isolation | 7 (subprocess import matrix, distinct types, coding enums + graph spot-pins, independent machine instances) |

## 8. Full test result

```
PRE :  python -m pytest   → 620 passed  (baseline, unchanged HEAD 6a58b43)
POST:  python -m pytest   → 667 passed, 0 failed   (620 + 47 new)
git diff --check          → clean (no whitespace errors)
```

## 9. Known limitations

1. No execution of any kind: no model calls, no driver wiring, no prompt
   packets, no parsers — by design (FOUNDATION ONLY).
2. No persistence: proposal state is not yet stored in SQLite; the models
   are JSON/SQLite-ready but no schema, table or migration exists.
3. No UI and no controller integration; the coding pipeline cannot see or
   reach Proposal Mode yet.
4. Hard gates are identifiers + result/status contracts only — no validator
   semantics, no page counting, no citation lookup.
5. `ProposalIterationRecord.proposal_hash` has no canonical hash algorithm
   yet; the future executor must define it (SHA-256 over the master proposal
   text is the natural candidate).
6. Session policy for proposal roles is a plain string field; the future
   execution session may tighten it to an enum once real policies are known.
7. The proposal workspace initializer seeds EMPTY files only; template
   content for source-of-truth files is deliberately not fabricated.

## 10. Exact recommended next session (Session 013)

**Proposal Mode execution spine, still UI-free:**

1. `proposal/packets.py` — deterministic review packets per reviewer role
   (mirror `core/plan_packet.py` style; source-of-truth excerpts + master
   proposal + strict output contract).
2. `proposal/parsers.py` — fail-closed parser for `ProposalReviewResult`
   (`PROPOSAL_REVIEW_VERDICTS` whitelist, bounded fields, PASS must carry no
   critical/high findings — reuse the `references/fail-closed-parser.md`
   pattern).
3. `proposal/executor.py` — ORCHESTRATOR-resolved review dispatch through
   the EXISTING `DriverRegistry`/`SessionManager` (no driver duplication),
   phase-gated by `ProposalStateMachine`.
4. `proposal/fingerprint.py` — define the `proposal_hash` algorithm.
5. Tests for all of the above with scripted in-process drivers (zero AI).

Explicitly NOT yet: UI, SQLite schema, hard-gate validators, page counting,
automatic patch application.

## GIT_STATUS

- Branch: `proposal-mode`
- Commit: see the push record (conventional message
  `feat(proposal): add isolated Proposal Mode domain foundation (Session 012)`)
- Pushed to `origin/proposal-mode`; NOT merged to `main` (per brief).
