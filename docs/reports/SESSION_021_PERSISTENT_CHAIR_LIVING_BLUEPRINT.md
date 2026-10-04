# Session 021 — Persistent Codex Panel Chair + Living Blueprint / Dual-Document Proposal Loop

**Date:** 2026-10-04
**Branch:** `proposal-living-blueprint-session-021` (base `147b7f5`, NOT merged)
**Version:** 1.4.0 → **1.5.0**
**Full suite:** **1201 passed, 0 failed** (baseline 1158 + 43 new S021 tests)

## What was built

The production workflow changes from `BLUEPRINT → PROPOSAL → revise
PROPOSAL` to a **versioned document pair** reviewed and evolved together:

```
IMMUTABLE ORIGINAL BLUEPRINT (MASTER_BLUEPRINT.md, never written by AI)
        ↓ import (exact canonical bytes)
CURRENT / LIVING BLUEPRINT (CURRENT_BLUEPRINT.md — ASTRA-write-only)
        ↕                          MASTER PROPOSAL
   THREE PARALLEL EVALUATORS  (findings carry a typed TARGET)
        ↓
   STRUCTURED CONSENSUS       (target preserved per item)
        ↓
   ASTRA / CODEX PANEL CHAIR  (persistent, operator-selected session)
        ↓
   ALL-OR-ROLLBACK PAIR COMMIT → CURRENT_BLUEPRINT N+1 + MASTER_PROPOSAL N+1
        ↓
   DUAL FRESHNESS → hard gates → iterate or COMPLETE
```

### Living Blueprint (brief §4)
- `00_SOURCE_OF_TRUTH/MASTER_BLUEPRINT.md` — immutable original; drift is a
  typed `original_mutated` error, never coerced.
- `00_SOURCE_OF_TRUTH/CURRENT_BLUEPRINT.md` — living design; initialized
  with the EXACT canonical bytes (import hook in the UI + deterministic
  legacy migration for pre-021 workspaces); conflicting/diverging living
  files are never silently replaced.
- `05_CONTROL/BLUEPRINT_STATE.json` (`encomm-pcc.blueprint-state/v1`):
  original/current hashes, iteration 0, revision id.
- New pure module: `src/encomm_pcc/proposal/living_blueprint.py`.

### Typed document targets (brief §6–7)
- `ProposalFindingTarget` = `PROPOSAL | BLUEPRINT | BOTH` on findings and
  patches; fail-closed parse (`invalid_target`), legacy payloads default to
  `PROPOSAL`.
- Preserved through aggregation → docket items → consensus matrix rows →
  chair context rendering (`target:` in docket text) → UI tables.

### Dual-document integration (brief §8–9)
- Parser: optional `expected_input_blueprint_hash` activates the strict
  dual contract (`missing_blueprint_hash` / `blueprint_hash_mismatch`);
  `revised_blueprint` may be `null`/empty — a legitimate "no justified
  Blueprint change".
- Packet: hash-activated LIVING PROJECT DESIGN section + dual output-contract
  addendum (complete documents only, no churn, `[INPUT REQUIRED: ...]` for
  unsupported facts).
- Executor: dual mode when the living Blueprint exists; blueprint mutation
  guard mirrors the master's; **all-or-rollback pair commit** (`proposal/
  document_pair.py`): stage both → snapshot → replace (restore-on-second-
  failure, verified) → manifest `05_CONTROL/DOCUMENT_PAIR_STATE.json`
  (`encomm-pcc.document-pair-state/v1`) written LAST as the commit marker.
- The dual-mode decision is document-presence-driven: legacy workspaces
  without a living Blueprint keep the byte-identical single-document path.

### Dual freshness (brief §11)
- Bundles carry `current_blueprint_hash`; `evaluate_review_freshness` is
  current ONLY when BOTH hashes still match (derived from the workspace when
  not supplied). Legacy bundles keep proposal-only semantics.

### Initial generation (brief §12)
- The living Blueprint enters the synthesis prompt; an optional
  `INITIAL_BLUEPRINT` block carries the chair's revision; the synthetic
  payload parses through the SAME strict dual parser; a justified Blueprint
  revision commits as a pair, otherwise the single master write path runs.
  Model-call accounting stays 3 specialists + 1 chair = 4.

### Codex engine (brief §1–3, §16)
- **Verified contract (installed codex-cli 0.154.0, zero model calls used
  to verify):** `codex exec` / `codex exec resume` accept
  `-c <key>=<value>` TOML overrides; `model_reasoning_effort` is the
  official config key (host config uses it; official docs:
  minimal/low/medium/high/xhigh, xhigh model-dependent). The driver
  validates fail-closed (`REASONING_EFFORT_LEVELS`) and emits
  `-c model_reasoning_effort=<level>` on BOTH new and resumed invocations.
- `ProposalAgentConfig.reasoning_effort` (empty = engine default; persisted;
  carried only in `SessionRequest.extra` — no other engine reads it).
- UI: per-role **Reasoning** combo (DEFAULT + 5 levels, Codex-only),
  editable model, profile/provider N/A for Codex, **real Codex session
  discovery** (`CodexSessionDiscovery`, zero model calls) with
  workspace-matched sessions first (`[WS]`), real full ids as item data,
  titles display-only, RESUME never auto-armed, stale bindings invalidated
  on engine/profile switches.
- PROJECT INPUTS renders `CURRENT / LIVING BLUEPRINT` (status + hash +
  iteration) and `DOCUMENT PAIR` (iteration + revision id).

### Versioning + tests (brief §10, §19–20)
- `06_VERSIONS/iteration_NNN_pre_review_blueprint.md`(+`.json`) and
  `..._post_integration_blueprint.md`(+`.json`) freeze BOTH documents.
- 43 new tests: `tests/test_session_021_living_blueprint.py`,
  `tests/test_session_021_document_pair.py` (incl. the simulated
  second-replace-failure rollback proof). Contract snapshots updated by
  design: S017 no-hardcoding scan now pins exactly ONE `"codex"` engine-id
  literal; UI worker test set 149 passed.
- `scripts/session_021_dual_document_acceptance.py` — **DUAL DOCUMENT
  PROPOSAL ACCEPTANCE PASSED** (14 steps: import → living init → generation
  4 calls → panel 7 calls → BLUEPRINT-target docket/consensus → pair commit
  → original untouched → versions → dual freshness both legs → clean second
  iteration 6 calls → gates COMPLETE).

## Verification

| Gate | Result |
| --- | --- |
| Full suite | **1201 passed, 0 failed** |
| S021 acceptance | `DUAL DOCUMENT PROPOSAL ACCEPTANCE PASSED` |
| S019 panel acceptance | `PANEL CAMPAIGN ACCEPTANCE PASSED` |
| S019 recovery acceptance | `CAMPAIGN RECOVERY ACCEPTANCE PASSED` |
| S017 proposal acceptance | `PROPOSAL ACCEPTANCE PASSED` |
| S020 UI acceptance | `PROPOSAL V2 UI ACCEPTANCE PASSED` |
| Contract test updates | S017 scan sharpened (single engine-id literal) |

## Known limitations

- Reasoning effort is passed per invocation but per-MODEL acceptance is the
  engine's own concern: an unsupported level surfaces the real engine error
  (no client-side model×level matrix is invented).
- Codex discovery depends on the local rollout store; it is read-only and
  never fabricates titles/ids.
- The campaign resume path reads existing durable state (unchanged); pair
  coherence across restarts is guaranteed by the manifest-last commit
  order, not by an extra campaign field.
- Live Codex mini test on a disposable workspace was NOT run in this
  session window (offline acceptances + real-CLI contract verification
  stand in); the operator can run it per the S020 recipe before merge.

## Git status

- Branch: `proposal-living-blueprint-session-021` (base main `147b7f5`)
- Feature commit: `0a7f0ebc2b28f3a4244aa4e179a0980c20a6c1c5`
- Remote: pushed and verified — `origin/proposal-living-blueprint-session-021`
  == `0a7f0eb` (28 files changed, +1387/−148 across the feature commit)
- Windows build: REBUILT from this exact source — `BUILD OK`,
  packaged smoke `SMOKE OK version=1.5.0` exit 0
  (`mode_tabs=2(coding,proposal)`, `proposal_v2_ui=ok`,
  drivers `codex,generic_cli,hermes`)
- DO NOT MERGE YET — operator reviews the GitHub diff first.

## Session 021A corrective note

> **Session 021A corrective note:** a live GitHub code review found nine
> production defects the original suite missed. All closed on the SAME
> branch (corrective commit `be6d33a`, version stays 1.5.0):
> (1) ProposalPatch.target_section clobbered by the target enum — distinct
> names; (2) `require_document_target` switch: NEW dual answers fail
> `missing_target` when a finding/patch omits target, legacy artifacts
> still load; (3) the ASTRA chair now HONOURS RESUME_SELECTED_SESSION via
> the proven `_resume_session_id` contract (never silent NEW; the report
> carries the real resumed thread id); (4) reasoning_effort reaches BOTH
> initial-generation paths via SessionRequest.extra; (5) the dual
> initial-generation report bug (UnboundLocalError on `write_report`)
> fixed — report binds `new_proposal_hash`/`blueprint_hash`/
> `pair_committed`; (6) all three initial specialists now receive
> CURRENT_BLUEPRINT as the PRIMARY design (frozen bytes/hash, authority
> preamble); (7) a dual workspace ALWAYS pair-commits (change/no-change
> matrix) on BOTH write paths; (8) cross-workspace Codex resume is
> refused fail-closed at the driver (discovery revalidation before any
> model call) and other-ws sessions are disabled (not just marked) in the
> UI + invalidated on workspace change; (9) the dual reviewer mutation
> guard covers CURRENT_BLUEPRINT. LIVE-CAUGHT defect fixed: the reasoning
> override must travel as the `-c key=value` PAIR (a bare token is a CLI
> usage error, exit 2). **LIVE CODEX MINI TEST PASSED** with the real
> installed CLI (model `gpt-5.6-sol` — the host-config `gpt-6.1-sol` is
> rejected by this ChatGPT account): disposable git-init'ed workspace,
> ONE real session created and RESUMED (same id returned, model+reasoning
> proven in argv), pair committed, MASTER byte-identical, session still
> discoverable. Suite **1229 passed, 0 failed** (27 new S021A tests);
> all six acceptances green; rebuild + packaged smoke
> `SMOKE OK version=1.5.0` exit 0.
