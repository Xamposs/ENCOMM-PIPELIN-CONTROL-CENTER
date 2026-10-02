# Session 013 — Proposal Review Runtime Bridge (fail-closed, one-review)

**Date:** 2026-10-02
**Branch:** `proposal-session-013` (created from `main` @ `c82d688fadc3a47324efb25b163a22acd2cd3190`)
**Verdict:** ✅ **GREEN — full suite 753 passed / 0 failed (667 baseline + 86 new), `git diff --check` clean, zero Coding Mode source changes.**

## 1. Goal

Build the deterministic contracts and the runtime execution bridge for ONE
proposal reviewer operation: construct a deterministic review packet, execute
it through an injected driver, parse the answer FAIL-CLOSED, return a
`ProposalReviewResult`, advance the `ProposalStateMachine` only when valid,
never edit `MASTER_PROPOSAL.md`, and work fully offline in tests. No
autonomous 3-reviewer loop, no UI, no real model calls.

## 2. Baseline

| Item | Value |
|---|---|
| Baseline commit (pre-work HEAD) | `c82d688fadc3a47324efb25b163a22acd2cd3190` (PR #1 merge, exact) |
| Pre-existing work found | None — `git status` clean before the branch was created |
| Branch | `proposal-session-013`, created from that exact commit |
| PRE-TEST RESULT | **667 passed, 0 failed** (Python 3.12 project interpreter) |
| POST-TEST RESULT | **753 passed, 0 failed** (667 + 86 new) |

## 3. Architecture — pure proposal vs proposal_runtime boundary

Session 013 introduces the two-layer split mandated by the brief (appended as
ADR D-057/D-058):

```
encomm_pcc.proposal_runtime          (execution adapters — MAY import drivers)
        ↓ imports
encomm_pcc.proposal                  (PURE contracts — imports NOTHING from
        ↓                             core/domain/drivers/persistence/ui/runtime)
   (nothing)
```

* `encomm_pcc.proposal` gained fingerprint/packet/parser/snapshot modules and
  still imports NOTHING from any Coding Mode package nor from
  `proposal_runtime` (subprocess-proven, test 34).
* `encomm_pcc.proposal_runtime` imports ONLY `encomm_pcc.proposal` plus the
  generic existing driver infrastructure (`drivers.base` contract types,
  `domain.enums.SessionPolicy`/`AgentRole` as generic configuration values).
  It imports NO registry, NO provider code, NO UI, NO persistence
  (subprocess-proven, tests 35 + `test_proposal_runtime_never_imports_ui_or_persistence`).
* Coding Mode (`app`, `core`, `drivers`, `persistence`, `ui`, `domain`) never
  imports any proposal package (subprocess-proven, test 35).
* `ProposalRole` and `AgentRole` remain independent types (never aliased);
  reviewer sessions run under the generic `AgentRole.TASK_AUDITOR` value for
  the driver contract's sake only (test 36).

## 4. Files added / modified

**Added (pure proposal package):**

| File | Content |
|---|---|
| `src/encomm_pcc/proposal/fingerprint.py` | `proposal_fingerprint()` — canonical SHA-256 over EXACT file bytes; lowercase hex; explicit `ProposalFingerprintError` on missing/unreadable/oversized; `MAX_PROPOSAL_BYTES = 20_000_000`; `PROPOSAL_HASH_ALGORITHM = "sha256-exact-bytes-v1"` |
| `src/encomm_pcc/proposal/review_packet.py` | Envelope constants (`<<<ENCOMM_PROPOSAL_REVIEW_START>>>` / `<<<ENCOMM_PROPOSAL_REVIEW_END>>>`, defined ONCE, shared with the parser); `ProposalReviewInputs` (explicit required/optional); `ProposalReviewPacket`; `build_review_packet()` — deterministic, role-gated (ORCHESTRATOR refused), read-only rules A–I embedded, honest `[UNAVAILABLE — …]` rendering, per-section 400k cap |
| `src/encomm_pcc/proposal/review_parser.py` | `parse_proposal_review()` fail-closed parser + `ProposalReviewParseError` (stable `reason` tags); exactly-one-envelope rule (`multiple_envelopes` / `unterminated_envelope` typed failures); role whitelist excluding ORCHESTRATOR; role/iteration match enforced; plain-int iteration; severity/category whitelists; patch exactly-one-content-channel + confidence [0,1]; semantic rules PASS/NEEDS_REVISION/BLOCKED; bounds 400k raw / 50 findings / 50 patches / 100 claims / 20k strings |
| `src/encomm_pcc/proposal/source_snapshot.py` | `load_review_snapshot()` — bounded (400k chars/file), read-only reader for the 5 source-of-truth files + master proposal (REQUIRED) + `01_OFFICIAL/OFFICIAL_REQUIREMENTS.md` (optional, plain text/Markdown only; PDF/RTF honestly unavailable with reason) |

**Added (runtime bridge):**

| File | Content |
|---|---|
| `src/encomm_pcc/proposal_runtime/__init__.py` | Package exports |
| `src/encomm_pcc/proposal_runtime/review_executor.py` | `run_review()` (injected driver, fail-closed gates), `ProposalReviewExecutionReport` (bounded evidence, `to_dict()`), `ProposalReviewOutcome`, `ProposalReviewGuardError`, `PHASE_FOR_REVIEWER` / `NEXT_REVIEW_PHASE` mappings, `MAX_EXCERPT_CHARS = 4_000` |

**Added (tests):** `tests/test_proposal_review_contracts.py` (49 tests),
`tests/test_proposal_runtime.py` (37 tests).

**Modified:** `src/encomm_pcc/proposal/__init__.py` (exports only) and the
documentation files listed in §9. No Coding Mode production source file was
touched; zero imports were added to any existing module.

## 5. Review JSON contract (canonical)

```json
{
  "reviewer_role": "SCIENTIFIC_REVIEWER | PROPOSAL_ENGINEER | RED_TEAM_REVIEWER",
  "verdict": "PASS | NEEDS_REVISION | BLOCKED",
  "summary": "…",
  "findings": [{ "severity": "critical|high|medium|low",
                 "category": "factual_contradiction|missing_evidence|weak_wording|structural_issue|recommendation",
                 "section": "…", "message": "…", "evidence": "…",
                 "source_refs": [], "suggested_change": "…" }],
  "proposed_patches": [{ "target_section": "…", "rationale": "…",
                         "replacement_text": "…", "patch_instructions": "…",
                         "source_refs": [], "confidence": 0.0 }],
  "unverified_claims": ["…"],
  "iteration_number": 1
}
```

delivered inside EXACTLY one `<<<ENCOMM_PROPOSAL_REVIEW_START>>> …
<<<ENCOMM_PROPOSAL_REVIEW_END>>>` pair. The markers live in
`proposal.review_packet` and are imported by the parser — prompt and parser
cannot drift.

## 6. Fail-closed rules (parser)

* bounded input BEFORE parsing (`oversized_payload`); `json.loads` only —
  no eval/exec/YAML; non-object root → `unexpected_type`.
* EXACTLY one START and one END marker — the envelope is MANDATORY
  (corrected in Session 013A; the original balanced-brace rescue scan was
  REMOVED): duplicates → `multiple_envelopes`; unterminated →
  `unterminated_envelope`; zero markers → `missing_envelope` (even when the
  text carries a syntactically perfect bare JSON object — there is NO
  balanced-brace rescue and NO bare-JSON fallback). `no_json_object` now
  only covers an envelope pair present with an empty payload.
* `verdict` ∈ {PASS, NEEDS_REVISION, BLOCKED} (`invalid_verdict`).
* `reviewer_role` ∈ the three reviewer roles (`invalid_reviewer_role`) AND
  must equal the expected role (`reviewer_role_mismatch`); ORCHESTRATOR can
  never be a reviewer or an expected role (`invalid_expected_role`).
* `iteration_number` must be a plain JSON integer equal to the expected one
  (`unexpected_type` for `"1"`, `iteration_mismatch` otherwise).
* findings: whitelisted severity (`invalid_severity`) and category
  (`invalid_category`), non-empty message, bounded fields/lists.
* patches: target + rationale required (absent key → `unexpected_type`,
  blank → `empty_field`); content in EXACTLY one of replacement_text /
  patch_instructions (`ambiguous_patch`); confidence ∈ [0.0, 1.0].
* PASS with any critical/high finding → `pass_with_blocking_findings`;
  PASS with unverified claims → `pass_with_unverified_claims`;
  NEEDS_REVISION without actionable finding/patch →
  `needs_revision_without_actionables`;
  BLOCKED without reason → `blocked_without_reason`.
* Failure ALWAYS raises a typed error — a malformed answer is never coerced
  into a result, never into PASS.

## 7. Runtime executor + MASTER_PROPOSAL immutability

Gate order of `run_review()` (nothing advances the state machine unless ALL hold):

1. **Phase/role guard** (BEFORE driver contact): `SCIENTIFIC_REVIEW →
   SCIENTIFIC_REVIEWER`, `IMPLEMENTATION_REVIEW → PROPOSAL_ENGINEER`,
   `RED_TEAM_REVIEW → RED_TEAM_REVIEWER`; mismatch raises
   `ProposalReviewGuardError(phase_role_mismatch)` with ZERO driver calls.
2. **Fingerprint BEFORE**: SHA-256 of the exact master-proposal bytes;
   missing/unreadable → `fingerprint_failed` guard error.
3. **Driver call** through the injected `BaseDriver`
   (`start_session` → `send_prompt` → `wait_for_completion`; session policy
   and timeout forwarded). Any `DriverError`/failed/empty answer →
   `DRIVER_FAILED`.
4. **Strict parse** (§6) — failure → `PARSE_FAILED` with `parse_reason`.
5. **Fingerprint AFTER**: unchanged digest mandatory. A changed digest →
   `MUTATION_DETECTED`: the parsed verdict is REFUSED even when it was PASS,
   the state machine does NOT advance, the report carries both hashes, and
   the on-disk change is neither restored nor deleted (write-authority
   violation surfaced, proven by a malicious scripted driver that mutates
   `MASTER_PROPOSAL.md` from INSIDE the driver call in all three review
   phases).
6. **Advance exactly once**: PASS/NEEDS_REVISION walk
   `SCIENTIFIC_REVIEW → IMPLEMENTATION_REVIEW → RED_TEAM_REVIEW →
   INTEGRATION` (the last edge lands ON `INTEGRATION`; nothing advances past
   it and `COMPLETE` stays reserved for later hard-gate validation). A
   BLOCKED verdict is a VALID review: it moves through the explicit D-055
   `BLOCKED` edge instead.

Reports retain operational evidence only (outcome, role, session id,
duration, parsed result, error/parse reason, proposal hashes before/after,
driver id, and a raw excerpt BOUNDED to 4 000 chars). No full transcript is
persisted; the runtime package contains no persistence machinery at all
(structural test).

## 8. Test evidence (86 new, all offline/deterministic)

| Brief area | Tests |
|---|---|
| Review packet (1–5 + extras) | 16 — determinism, role focus, read-only rules, SOT protection, honest official-requirements absence, ORCHESTRATOR refusal, input validation, envelope-in-prompt |
| Fingerprint (6–8 + extras) | 7 — identical bytes, lowercase hex, one-byte change, no newline normalisation, missing/not-a-file/oversized failures, stable algorithm id |
| Parser valid (9–11 + extras) | 7 — PASS/NEEDS_REVISION/BLOCKED, prose around a correct envelope, case handling, full-field round-trip |
| Parser fail-closed (12–22 + extras) | 24 — every rule in §6, including duplicate/unterminated envelope, quoted iteration, both/none patch channels, oversized payload/list/string, never-coerce-to-PASS |
| Runtime (23–33 + extras) | 13 — phase mapping, pre-driver rejection, advance-exactly-once, INTEGRATION-never-passed, malformed/driver-failure no-advance, BLOCKED edge, hash before, mutation detection ×3 phases, bounded excerpt, no-persistence structure, report JSON, policy/timeout forwarding |
| Isolation (34–36 + extras) | 4 — subprocess proof ×3, distinct types |

```
PRE :  python -m pytest   → 667 passed  (baseline, unchanged HEAD c82d688)
POST:  python -m pytest   → 753 passed, 0 failed   (667 + 86 new)
git diff --check          → clean
```

## 9. Documentation changes

| File | Change |
|---|---|
| `docs/CURRENT_STATE.md` | §1 merge-state correction (Session 012 is MERGED: PR #1, feature `6742c39`, merge `c82d688`, 667-test baseline) + Session 013 state + §3 tree additions + §4 ADR rows |
| `docs/DECISIONS.md` | Appended D-057 (canonical proposal fingerprint), D-058 (proposal_runtime execution-adapter boundary). No existing ADR touched |
| `docs/reports/SESSION_013_PROPOSAL_REVIEW_RUNTIME.md` | This report |

The Session 012 report intentionally records the pre-merge historical state
and was left unchanged (this is the clearly-labelled post-merge note: the
foundation it describes is now merged to `main` via PR #1).

## 10. Known limitations

1. ONE reviewer operation only: no autonomous 3-reviewer loop, no parallel
   reviewers, no INTEGRATION executor — the loop orchestration (including
   who calls `run_review` and in which order) is the next session's work.
2. INTEGRATION is reached but never left; hard-gate validators, scorecards
   and COMPLETE semantics do not exist yet (D-056 still contract-only).
3. No persistence: review results/reports are returned, not stored; no
   SQLite schema change was made.
4. No UI; Coding Mode cannot see Proposal Mode.
5. Official requirements ingestion is plain text/Markdown only (no PDF/RTF).
6. `PromptResult.token` counts: the current generic `PromptResult` exposes
   tokens only via `metadata` when a driver provides them; the report
   surface keeps session id/duration but does not yet aggregate tokens.
7. The snapshot reader decodes UTF-8 with `errors="replace"` for prompt
   building; byte-exact hashing is separate (`proposal_fingerprint`).

## 11. Exact next recommended session (Session 014)

**Proposal review loop orchestration (still UI-free):**

1. A deterministic proposal-loop runner over `run_review`: walks
   SCIENTIFIC → IMPLEMENTATION → RED_TEAM for one iteration, aggregates the
   three `ProposalReviewExecutionReport`s, and parks on
   `REVISION_REQUIRED`/`BLOCKED` per the aggregated verdicts.
2. Durable recording of review results into the workspace (`04_REVIEWS/`
   structured artifacts) — file-based first, SQLite later.
3. INTEGRATION planning: how the ORCHESTRATOR applies accepted
   `ProposalPatch`es (the ONLY writer of `MASTER_PROPOSAL.md`), plus the
   iteration fingerprint freeze into `06_VERSIONS/`.

Explicitly NOT yet: UI, hard gates, scorecards, citation verification,
DOCX/PDF generation, database migrations.

## 12. Session 013A addendum — strict review envelope (corrective follow-up)

Follow-up commit on this branch, `fix(proposal): require strict review
envelope (Session 013A)`: the parser's balanced-brace rescue / bare-JSON
fallback was REMOVED — the envelope is mandatory.  Zero envelope markers now
reject with `missing_envelope` even when the text carries a syntactically
perfect bare JSON object; `no_json_object` only covers an envelope present
with an empty payload.  Prose around a CORRECT envelope stays accepted.
Regression pins: `tests/test_proposal_review_contracts.py::TestEnvelopeStrictness`.
Pushed to `origin/proposal-session-013`; still NOT merged to `main`.

## GIT_STATUS

- Branch: `proposal-session-013`
- Commit: `feat(proposal): add fail-closed review runtime bridge (Session 013)`
- Pushed to `origin/proposal-session-013`; NOT merged to `main` (per brief).
