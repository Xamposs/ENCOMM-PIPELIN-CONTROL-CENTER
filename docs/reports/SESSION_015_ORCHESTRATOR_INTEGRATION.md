# Session 015 — ORCHESTRATOR Integration + Deterministic Revision Handoff

**Date:** 2026-10-02
**Branch:** `proposal-session-015` (created from `main` @
`b9bba1cb3870b6922279949380b484b75feffde7`, the PR #3 merge commit, exact)
**Verdict:** ✅ **GREEN — full suite 920 passed / 0 failed (824 baseline +
96 new), `git diff --check` clean, zero Coding Mode source changes.**

## 1. Goal

Turn Session 014's dead-end INTEGRATION stop into the REAL continuation:

INTEGRATION → ORCHESTRATOR integration operation → atomic MASTER_PROPOSAL
replacement → post-integration revision artifact → HARD_GATE_VALIDATION →
(review-freshness gate) → REVISION_REQUIRED + NEXT_ITERATION handoff when
the proposal changed. Plus the deterministic zero-AI clean-PASS bypass, the
strict integration envelope contract, and the one-iteration composition
helper. No UI, no full 14-gate validators, no score, no parallel reviewers,
no real model calls.

## 2. Baseline

| Item | Value |
|---|---|
| Baseline commit (pre-work HEAD) | `b9bba1cb3870b6922279949380b484b75feffde7` (PR #3 merge, exact) |
| Local main fast-forwarded | `7ff8068` → `b9bba1c` (`git pull --ff-only`, clean) |
| Pre-existing work found | None — `git status` clean before the branch was created |
| Branch | `proposal-session-015`, created from that exact commit |
| PRE-TEST RESULT | **824 passed, 0 failed** (Python 3.12 project interpreter) |
| POST-TEST RESULT | **920 passed, 0 failed** (824 + 96 new) |

## 3. Architecture — what was added where

```
encomm_pcc.proposal_runtime                             (execution adapters)
    run_integration()          — the INTEGRATION executor (integration_executor.py)
    run_iteration()            — review cycle + integration composition (proposal_iteration.py)
    replace_master_proposal()  — the ONE master-proposal writer (master_writer.py)
    freeze_post_integration_version() — 06_VERSIONS/ post freeze (version_freeze_post.py)
    write_integration_result() — 04_REVIEWS/ integration evidence (integration_artifacts.py)
    evaluate_review_freshness() — the freshness gate (review_freshness.py)
    write_next_iteration_handoff() — 05_CONTROL/NEXT_ITERATION.json (revision_handoff.py)
        ↓ imports
encomm_pcc.proposal                                     (PURE contracts)
    build_integration_packet() — deterministic ORCHESTRATOR prompt (integration_packet.py)
    parse_proposal_integration() — fail-closed parser (integration_parser.py)
    ProposalIntegrationResult  — typed result + items (integration_models.py)
        ↓ imports
   (nothing)
```

* The pure modules import NOTHING outside the pure package (subprocess
  isolation re-pinned for the three new modules).
* The runtime modules import no UI/persistence/PySide6/core (subprocess
  re-pinned); no provider/engine name appears in any new module
  (structural test); the writer modules never serialise raw model output
  (structural test).
* Coding Mode: ZERO production files touched (structural scan re-pinned).
* One targeted runtime change by design: `run_review_cycle()` now accepts
  `REVISION_REQUIRED` as an entry phase and walks the EXISTING D-055 edge
  `REVISION_REQUIRED → SCIENTIFIC_REVIEW` (required by brief §14 so the
  next review cycle can start after a changed integration).

## 4. Files added / modified

**Added (pure proposal package):**

| File | Content |
|---|---|
| `src/encomm_pcc/proposal/integration_packet.py` | `PROPOSAL_INTEGRATION_ENVELOPE_START/END` (defined ONCE, shared with the parser), `ProposalIntegrationInputs`, `ProposalIntegrationPacket`, `build_integration_packet()` — deterministic prompt embedding the A–J authority rules (ORCHESTRATOR sole authority, patches are RECOMMENDATIONS, never invent source facts, resolve contradictions, ONE COMPLETE revised proposal), the current proposal text + exact input hash + iteration, the brief, the source snapshot, official-requirements honesty |
| `src/encomm_pcc/proposal/integration_parser.py` | `parse_proposal_integration()` — STRICT-MANDATORY envelope (zero markers → `missing_envelope` even around bare JSON, ≥2 → `multiple_envelopes`, one-sided → `unterminated_envelope`); `json.loads` only; rejects wrong role (`wrong_role`), non-integer/echoed iteration (`iteration_mismatch`), input-hash mismatch (`input_hash_mismatch`), empty/oversized/absent `revised_proposal`; STRICT item arrays (exactly `item_id`/`action`/`reason`, action must match its array, whitelisted actions); no bare-JSON fallback, no balanced-brace rescue |
| `src/encomm_pcc/proposal/integration_models.py` | `ProposalIntegrationResult` (iteration/input hash/summary/dispositions/`revised_proposal`; JSON-friendly round-trip), `ProposalIntegrationItem`, `INTEGRATION_ITEM_ACTIONS` frozenset |

**Added (runtime package):**

| File | Content |
|---|---|
| `src/encomm_pcc/proposal_runtime/integration_executor.py` | `run_integration()` — state guard (EXACTLY INTEGRATION) → input hash → brief load/schema/iteration/hash validation (ALL before driver contact) → zero-AI clean-PASS bypass (`integration_required=false` + verified clean PASS ⇒ ZERO driver calls, no-op artifact, INTEGRATION→HARD_GATE_VALIDATION; inconsistent brief ⇒ ARTIFACT_CONFLICT) → ORCHESTRATOR packet over frozen inputs → injected driver → strict parse → PRE-WRITE re-fingerprint (change ⇒ MUTATION_DETECTED, output REFUSED, externally modified file never overwritten) → `replace_master_proposal()` → post-integration freeze + `integration_result.json` (conflicts after a successful master write surface the honest partial-success: machine at HARD_GATE_VALIDATION, outcome ARTIFACT_CONFLICT) → state advance. `ProposalIntegrationOutcome`: COMPLETED_CHANGED / COMPLETED_NO_CHANGE / NO_INTEGRATION_REQUIRED / DRIVER_FAILED / PARSE_FAILED / STALE_INPUT / MUTATION_DETECTED / ARTIFACT_CONFLICT / WRITE_FAILED |
| `src/encomm_pcc/proposal_runtime/master_writer.py` | `replace_master_proposal()` — THE ONE legitimate master-proposal writer: usable ONLY at INTEGRATION (`phase_not_integration`), stale-input refusal (never overwrites a changed file), UTF-8 exact bytes with the ONE documented canonical EOF policy (append a single `\n` when absent, never strip), same-directory temp + fsync + `os.replace`, old/new hashes from the same bytes |
| `src/encomm_pcc/proposal_runtime/version_freeze_post.py` | `freeze_post_integration_version()` — `06_VERSIONS/iteration_NNN_post_integration.md` + JSON sidecar (previous hash, new hash, algorithm, artifact path, pre-review path) from the EXACT written bytes (never re-read); atomic; identical = safe reuse, anything else = `VersionFreezeError` |
| `src/encomm_pcc/proposal_runtime/integration_artifacts.py` | `write_integration_result()` — `04_REVIEWS/iteration_NNN/integration_result.json`: structured schema `encomm-pcc.integration-result/v1` (iteration, revision, input/output hash, dispositions, runtime outcome/driver id/session id/duration); deliberately NO `revised_proposal` copy (the master + freeze are its only durable homes), no transcript, no raw envelope; deterministic JSON; identical = safe reuse, anything else = `ArtifactConflictError` |
| `src/encomm_pcc/proposal_runtime/review_freshness.py` | `evaluate_review_freshness()` — current master hash vs the LATEST `review_bundle.json` hash; missing/unreadable/no-hash evidence FAILS CLOSED (never reads as fresh) |
| `src/encomm_pcc/proposal_runtime/revision_handoff.py` | `build_next_iteration_payload()` + `write_next_iteration_handoff()` — `05_CONTROL/NEXT_ITERATION.json` (`encomm-pcc.revision-handoff/v1`): next/previous iteration, previous reviewed hash, revised hash, previous findings, unresolved items, integration summary; atomic; identical = safe reuse, conflict = typed error |
| `src/encomm_pcc/proposal_runtime/proposal_iteration.py` | `run_iteration()` — composes `run_review_cycle()` + `run_integration()` + the freshness gate + the handoff; a changed proposal walks HARD_GATE_VALIDATION → REVISION_REQUIRED (`READY_FOR_NEXT_ITERATION`); an unchanged proposal stays review-current (`READY_FOR_HARD_GATES`); review BLOCKED/failures propagate with the orchestrator NEVER called; ONE call = ONE iteration, NO loop |

**Modified:**

| File | Change |
|---|---|
| `src/encomm_pcc/proposal_runtime/review_loop.py` | ONE addition by design (brief §14): `REVISION_REQUIRED` joins the resume-entry phases and walks `REVIEW_ITERATION_ENTRY` (the existing D-055 edge) before source validation |
| `src/encomm_pcc/proposal/__init__.py` | Pure-package exports for the three integration modules |
| `src/encomm_pcc/proposal_runtime/__init__.py` | Runtime exports for the seven new modules |
| `src/encomm_pcc/__init__.py`, `pyproject.toml`, `README.md`, `tests/test_imports.py` | Version gate 1.0.2 → 1.1.0 |
| `docs/CURRENT_STATE.md` | §1 stale merge-state CORRECTED (Session 014+014A MERGED via PR #3, all three commits, 824 baseline) + Session 015 state; §4 ADR rows D-061…D-064 |
| `docs/DECISIONS.md` | Appended D-061…D-064. No existing ADR touched |

**Added (tests):** `tests/test_proposal_integration_contracts.py` (46),
`tests/test_proposal_integration_executor.py` (43),
`tests/test_proposal_integration_isolation.py` (7) — **96 new offline
tests**, all deterministic scripted-driver/temp-file cases, zero model
calls.

## 5. Safety chain (the write path, in order)

```
guards (state == INTEGRATION; brief exists; brief.schema; brief.iteration;
        brief.proposal_hash == live SHA-256)     ← ALL before any driver contact
  → zero-AI bypass  OR  packet → driver → strict parse
  → re-fingerprint (change ⇒ MUTATION_DETECTED; output refused;
                     externally modified file NEVER overwritten)
  → atomic replace (temp in 03_PROPOSAL/ + fsync + os.replace;
                    expected hash re-verified inside the writer)
  → post-integration freeze (exact written bytes) + integration_result.json
  → INTEGRATION → HARD_GATE_VALIDATION
  → freshness gate: changed ⇒ REVISION_REQUIRED + NEXT_ITERATION.json
                    unchanged ⇒ review-current
```

Every driver/parse/stale/write failure parks the machine at INTEGRATION
with a typed outcome; no failure path fakes success. The one honest
exception: a post-write evidence conflict is surfaced as a partial success
(the write DID happen; the machine reports HARD_GATE_VALIDATION +
ARTIFACT_CONFLICT) — never a pretend rollback.

## 6. Test evidence (96 new, all offline/deterministic)

| Brief §18 area | Where | Tests |
|---|---|---|
| PACKET/PARSER (1–12) | `test_proposal_integration_contracts.py` | 46 — deterministic packet, hash/role embedding, envelope discipline (bare JSON / duplicate / unterminated / empty / non-object), wrong/missing role, iteration mismatch+string, input-hash mismatch+missing, empty/oversized/absent revised proposal, oversized raw payload, item-array contract (exact keys, action match, unknown action, empty id, defaults), round-trip, model guards |
| RUNTIME GUARDS (13–20) | `test_proposal_integration_executor.py` | 8 — wrong state / missing brief / brief-iteration mismatch / brief-master hash mismatch all fail with ZERO driver calls; driver failure, parser failure never edit the master; mid-call mutation detected and never overwritten (bytes asserted) |
| WRITE (21–28) | same | 8 — atomic replacement, old+new hashes captured, post-freeze byte-exact + sidecar contract, integration_result schema, no raw transcript anywhere in the workspace, conflicting integration_result rejected (master untouched), conflicting post-version rejected (honest partial success: machine at HARD_GATE_VALIDATION, stale evidence untouched) |
| ZERO-AI (29–32) | same | 4 — zero driver calls, byte-identical master, no-op artifact, HARD_GATE_VALIDATION |
| REVISION FRESHNESS (33–40) | same | 8 — changed proposal never COMPLETE, walks to REVISION_REQUIRED, NEXT_ITERATION.json created with next=2, previous hash + revised hash + unresolved items recorded, iteration 2 starts from REVISION_REQUIRED and reviews the NEW hash (brief + all three prompts embed HASH2, HASH1 absent) |
| NO-CHANGE (41–45) | same | 5 — identical text ⇒ COMPLETED_NO_CHANGE, changed=false, output==input hash, HARD_GATE_VALIDATION, freshness current |
| COMPOSITION (46–50) | same | 4 + units — NEEDS_REVISION → READY_FOR_NEXT_ITERATION, clean PASS → zero-AI → READY_FOR_HARD_GATES, reviewer BLOCKED → orchestrator never called, integration failure returns loudly; master_writer phase gate / stale refusal / canonical EOF / no temp residue; freshness fail-closed + current/stale; handoff conflict |
| ISOLATION (51–55) | `test_proposal_integration_isolation.py` | 7 — pure package + new pure modules import-clean (subprocess), runtime modules import no UI/persistence/core (subprocess), Coding Mode production scan, no process/network/provider-SDK surface in new modules, no provider hardcoding, writer transcript-mechanism scan |

```
PRE :  python -m pytest   → 824 passed  (baseline, unchanged HEAD b9bba1c)
POST:  python -m pytest   → 920 passed, 0 failed   (824 + 96 new)
git diff --check          → clean
```

**Runtime change by design (1 module):** `run_review_cycle()` previously
refused `REVISION_REQUIRED` as an entry phase, making the D-055
`REVISION_REQUIRED → SCIENTIFIC_REVIEW` edge unwalkable and the next
review iteration impossible (brief §14 requires it). The resume-entry set
now accepts it and walks the existing edge; Session 014's own tests stay
green unchanged.

> **Session 015A corrective note:** previous-iteration findings are now
> actually PROPAGATED into the ORCHESTRATOR integration packet, bounded and
> fail-closed. Session 015 defined `ProposalIntegrationInputs.previous_findings_text`
> and let `run_integration()` accept `previous_findings` (forwarded by
> `run_iteration()` as `previous_findings_records`), but the executor built
> the packet WITHOUT it — the records were silently dropped before the
> ORCHESTRATOR prompt, breaking the revision-context contract. Fix (ONE
> module, `integration_executor.py`): `_render_previous_findings()` converts
> the records to deterministic canonical JSON (`indent=2`, `sort_keys`,
> `ensure_ascii=False`; `None`/`[]` → empty string → the packet's existing
> UNAVAILABLE marker) and reuses the existing `PREVIOUS FINDINGS (earlier
> iterations)` section — no duplicated packet rendering. An oversized
> rendering exceeds `MAX_INTEGRATION_PACKET_SECTION_CHARS` and fails CLOSED
> BEFORE packet construction and any driver contact (no truncation, no
> partial serialization); non-list input is rejected; the input list is
> never mutated; no timestamps, provider/model metadata or raw transcripts
> are introduced. The zero-AI clean-PASS path is unchanged (zero driver
> calls, no rendering), and `NEXT_ITERATION.json` behavior is unchanged.
> 17 new offline tests (4 packet contracts + 13 runtime, including
> run_iteration forwarding and byte-identical determinism across two fresh
> workspaces); full suite 920 → **937 passed, 0 failed**. A consistency
> scan found no other Session 015 public parameter accepted but silently
> unused.

## 7. Failure semantics (implemented exactly)

Wrong state / missing brief / unreadable brief / schema mismatch /
iteration mismatch / hash mismatch ⇒ fail BEFORE the driver, machine stays
INTEGRATION. Driver failure / empty answer / parse failure ⇒ machine stays
INTEGRATION, master untouched. Pre-write mutation ⇒ MUTATION_DETECTED,
output refused, external bytes preserved. Writer stale-input ⇒
STALE_INPUT, file untouched. Post-write freeze/artifact conflict ⇒ honest
partial success (machine at HARD_GATE_VALIDATION, exact condition in the
report). Clean-PASS bypass inconsistency ⇒ ARTIFACT_CONFLICT before any
model call. A reviewer BLOCKED ⇒ iteration ends REVIEW_BLOCKED, zero
orchestrator calls. No failure path reaches COMPLETE.

## 8. Documentation changes

| File | Change |
|---|---|
| `docs/CURRENT_STATE.md` | §1 version section rewritten for 1.1.0; Session 014/014A merge state CORRECTED (PR #3, `b9bba1c`, 824 baseline); Session 015 state added; §4 ADR rows D-061…D-064 |
| `docs/DECISIONS.md` | Appended D-061 (sole ORCHESTRATOR integration authority), D-062 (model returns content, runtime owns the atomic write), D-063 (changed proposal MUST be re-reviewed), D-064 (zero-AI clean-pass bypass). No existing ADR touched |
| `docs/reports/SESSION_015_ORCHESTRATOR_INTEGRATION.md` | This report |

## 9. Known limitations

1. HARD_GATE_VALIDATION has exactly ONE real check — the review-freshness
   lifecycle invariant (D-063). No page/citation/budget/WP/challenge
   validator exists; nothing claims compliance (D-056 unchanged; full
   gates are Session 016).
2. `integration_result.json` intentionally does NOT duplicate the revised
   proposal bytes; reconstructing the full record needs the master + the
   post-freeze (both durable).
3. The integration item `item_id`s are the ORCHESTRATOR's references; the
   runtime does not re-verify them against the brief (the ORCHESTRATOR is
   the disposition authority; the records are traceability evidence).
4. `run_iteration()` is single-shot by design — the future UI/controller
   decides when another iteration runs; there is no autonomous loop.
5. No UI, no parallel reviewers, no scorecard, no DOCX/PDF, no SQLite
   persistence, no real-model acceptance (explicitly out of scope per
   brief §20; zero-AI validation consistent with Session 007+ discipline).
6. Session 014's per-review artifacts for a BLOCKED cycle stop the
   sequence before a bundle/brief exists; integration therefore requires a
   completed three-reviewer cycle (unchanged contract).

## 10. Exact next recommended session (Session 016)

**Hard-gate validators over the frozen revision:** implement the first
REAL validators behind the canonical `HARD_GATE_IDS` (e.g.
MANDATORY_SECTIONS, SOURCE_OF_TRUTH_INTEGRITY, UNVERIFIED_CLAIMS
ledger-matching, INTERNAL_CONTRADICTIONS from the review record) as PURE
proposal modules consuming the post-integration freeze, with the
`ProposalHardGateResult` contract from Session 012 — and the
HARD_GATE_VALIDATION → COMPLETE walk when every canonical gate holds.
Explicitly NOT yet: UI, parallel reviewers, DOCX/PDF, database
persistence.

## GIT_STATUS

- Branch: `proposal-session-015`
- Commit: `feat(proposal): add orchestrator integration and revision handoff (Session 015)`
- Pushed to `origin/proposal-session-015`; NOT merged to `main` (per brief).
