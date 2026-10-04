# Session 019 — Proposal Factory V2: Parallel Evaluator Panel + Autonomous Campaign

Branch: `proposal-factory-v2-session-019` (from `main` @
`8b32ae727ff9685c3736cff6e73bdc89891fa54e` — the Session 018 release,
exactly the authoritative baseline).

## 1. Goal

Proposal Factory V2 (brief §§0–47): safe source imports, real Hermes
discovery, the parallel three-evaluator panel with a structured consensus
round, ASTRA as panel chair over an honest internal readiness index, the
bounded restart-safe campaign loop, and the Windows v1.4.0 build.

## 2. Baseline

- `main` fast-forward verified at `8b32ae7`; clean tree; full suite
  reproduced **1083 passed, 0 failed** on the new branch BEFORE feature
  work (two independent full-suite runs).
- Project interpreter Python 3.12 (`%LOCALAPPDATA%\Programs\Python\Python312`).

## 3. What was added (per layer)

### Pure proposal domain (`src/encomm_pcc/proposal/`)
- `source_import.py` — D-071: originals preserved under `IMPORTS/`,
  canonical normalized Markdown derived, atomic
  `05_CONTROL/SOURCE_IMPORT_MANIFEST.json`, backup-before-explicit-replace,
  real extraction (docx/pdf/rtf/md/txt — no OCR), fail-visible on
  empty/corrupt, `import_proposal` refuses a non-empty master.
- `source_budget.py` — operator-configured blueprint character budget
  (default 1.1M); `SourceBudgetExceededError` blocks BEFORE any model call;
  never truncates.
- `source_snapshot.py` (extension) — `blueprint_max_chars` seam raises ONLY
  the blueprint read bound; `APPLICATION_TEMPLATE` + `01_OFFICIAL/NORMALIZED`
  documents are first-class snapshot inputs (deterministic name order).
- `review_packet.py` (extension) — `blueprint_max_chars` seam +
  `_section_with_cap` so the whole canonical blueprint reaches reviewers
  within budget.
- `panel_contracts.py` — D-074: consensus packet + STRICT-MANDATORY
  envelope parser (role/iteration/hash echo enforced; per-item judgements
  `AGREE|DISAGREE|PARTIAL|INSUFFICIENT_EVIDENCE`; bounded readiness
  assessments).
- `panel_matrix.py` — deterministic docket (stable `F/P` ids) + consensus
  matrix (votes, counts, `unresolved`, `blocks_acceptance`); schema-checked
  round-trips.
- `readiness.py` — D-075: weighted per-criterion medians + explicit
  penalties; `INTERNAL READINESS — NOT AN EIC SCORE` on every artifact.
- `models.py` (extension) — `ProposalAgentConfig.session_mode`
  (`NEW_SESSION` default / `RESUME_SELECTED_SESSION` requiring a real
  `session_id`; fail-closed validation).

### Runtime (`src/encomm_pcc/proposal_runtime/`)
- `review_executor.py` (refactor, contract-preserving) — machine-free core
  `execute_review_call` extracted from `run_review` (which keeps the
  phase/role gate + the single state advance); `_resume_session_id`
  consumes explicit resume requests fail-closed (capabilities-checked,
  never invented, leftover ids under NEW_SESSION ignored).
- `panel_runtime.py` — D-073: `run_parallel_panel_review_cycle`
  (own-driver-instance rule, barrier-proven concurrency, post-join
  canonical phase walk, BLOCKED edge, docket artifact) +
  `run_panel_consensus_round` (parallel, read-only, persists
  `panel_consensus.json` + `readiness.json`).
- `panel_chair.py` — D-075 composition: panel → consensus →
  `run_integration(..., extra_instructions=)` (PANEL-CHAIR-CONTEXT seam);
  model-call accounting 7/iteration (6 on clean-pass bypass);
  `REVISION_REQUIRED` transition on changed proposals.
- `integration_executor.py` (extension) — bounded `extra_instructions`
  parameter; byte-identical packets for existing callers.
- `initial_generation.py` — briefs §13–14: three specialists in parallel
  (red team produces checklists, not attacks) → ASTRA synthesis through the
  STRICT integration parser → runtime-owned write via `master_writer` →
  template-fidelity check (dropped template headings fail closed;
  `[INPUT REQUIRED: …]` markers are the honest alternative) → durable
  `04_REVIEWS/initial_generation/` artifacts → machine back at IDLE.
- `campaign.py` — D-076: bounded campaign loop, durable
  `CAMPAIGN_CONFIG.json`/`CAMPAIGN_STATE.json`, all stop conditions,
  model-call accounting (4/7/6), read-only recovery, never auto-runs.
- `hermes_selector_discovery.py` — D-072: profiles (production discovery),
  sessions (profile-scoped bridge), provider/model honestly derived from
  the profile's own `config.yaml` (v0.21.5 exposes no listing command —
  verified live).

### Version / packaging
- 1.3.1 → **1.4.0** (`__init__.py`, `pyproject.toml`,
  `tests/test_imports.py`).
- `requirements.txt` += `pypdf`, `python-docx`, `striprtf`, `PyYAML`
  (lazy imports; `ENCOMM-PCC.spec` `hiddenimports` updated).

## 4. Test evidence

- **44 new offline tests, zero model calls**:
  - `tests/test_session_019_panel.py` (26): import matrix, budget gate,
    snapshot seams, panel concurrency (barrier), own-driver rule, one-
    failure-blocks-aggregation, mutation → STALE_PROPOSAL, BLOCKED walk,
    docket stability, budget-before-calls, consensus contracts, matrix
    determinism, readiness medians/penalties/disclaimer.
  - `tests/test_session_019_initial_generation.py` (9): happy path,
    barrier-proven specialist concurrency, non-empty-master refusal,
    blueprint/template preconditions before any call, specialist-failure
    blocks synthesis, shared-driver refusal, template fidelity, strict
    contribution envelope, per-role focus in prompts.
  - `tests/test_session_019_panel_chair.py` (3): full chain →
    REVISION_REQUIRED with 7 calls and consensus in the ASTRA packet;
    consensus failure fails closed before ASTRA; clean-pass 6-call bypass.
  - `tests/test_session_019_campaign.py` (6): empty-master → generation →
    iterations → gate-BLOCKED WAITING_FOR_OPERATOR (no calls burned);
    max-model-calls stop; boundary controls; restart recovery with ZERO
    fresh-driver calls; config bounds; workspace-bound state roundtrip.
- Full suite after Session 019: **1127 passed, 0 failed** (1083 + 44).

## 5. Acceptance scripts (offline, scripted drivers, real production code)

- `scripts/session_019_panel_acceptance.py` →
  **PANEL CAMPAIGN ACCEPTANCE PASSED** (source import → initial generation
  → iteration 1 full panel 7 calls → durable artifacts → iteration 2 clean
  pass 6 calls → readiness rises 19.1→22.9 with the disclaimer → operator
  evidence fixture → 14 hard gates → COMPLETE).
- `scripts/session_019_recovery_acceptance.py` →
  **CAMPAIGN RECOVERY ACCEPTANCE PASSED** (leg 1 checkpoint → simulated
  restart with fresh machine/drivers and ZERO auto-run calls → explicit
  resume continues at the checkpoint → exactly one iteration's call delta
  → durable state coherent).
- `scripts/session_017_proposal_acceptance.py` → PROPOSAL ACCEPTANCE
  PASSED (regression).
- `python main.py --smoke-test` → `SMOKE OK version=1.4.0 …
  mode_tabs=2(coding,proposal) … clean_shutdown`.

## 6. Known limitations

- ~~The proposal-mode UI (§29–32) is runtime-complete but NOT yet wired
  into `ui/proposal_mode.py`.~~ **RESOLVED in Session 020** — see the
  SESSION 020 UI COMPLETION NOTE below.
- Campaign wall-clock accounting counts only campaign-driven time.
- Readiness is advisory by construction (D-075); gates rule.
- Live mini-panel test NOT run in this session (per brief §41 timing) —
  the offline matrices and both acceptance scripts are the evidence base.
  (Session 020 subsequently ran the controlled live mini acceptance —
  see the completion note below.)

## 7. Decisions

D-071…D-076 appended to `docs/DECISIONS.md` (source imports, discovery,
parallel panel, consensus round, chair + readiness, campaign).

## 8. GIT_STATUS

- Branch: `proposal-factory-v2-session-019`
- Pushed to `origin/proposal-factory-v2-session-019`; **DO NOT MERGE** —
  the operator reviews the GitHub diff first (brief §46).

## 9. SESSION 020 UI COMPLETION NOTE

Session 020 (same branch, same v1.4.0 — the brief explicitly forbids a
version bump) wires the Session 019 backend into the production Proposal
Mode UI. The §6 limitation above ("the proposal-mode UI … NOT yet wired")
is RESOLVED and no longer applies.

- `ui/proposal_mode.py` is now the Proposal Factory V2 production surface
  (vertically scrollable; sections PROJECT INPUTS → AGENTS → PANEL /
  CAMPAIGN → CURRENT STATE & READINESS → PANEL RESULTS → HARD GATES →
  EVIDENCE). All S017/S017A widget contracts and recovery semantics are
  preserved (the 49 existing tests stay green unchanged).
- Imports go through the REAL `source_import` (originals preserved,
  manifest, backup-before-explicit-replace with a confirmation dialog,
  extraction warnings surfaced, no OCR); the source budget displays
  SOURCE BUDGET EXCEEDED before any AI action.
- Agent rows use the REAL `hermes_selector_discovery` (profiles,
  profile-derived provider/model, profile-scoped sessions; editable
  combos). NEW: a profile switch (or leaving Hermes) resets the session
  selection AND the session mode — a RESUME mode without an id is an
  unconstructable config, so the UI resets it instead of constructing an
  invalid one (fail-closed, found by the item-8 test).
- GENERATE INITIAL PROPOSAL and RUN PANEL ITERATION call the REAL
  `run_initial_generation` / `run_panel_chair_iteration` (the legacy
  sequential RUN ITERATION button remains for partial-phase resume, per
  brief §19). Campaign START/RESUME call the REAL `run_campaign` with the
  operator's `CampaignConfig`; PAUSE/STOP arm the boundary control object
  consumed at safe stage boundaries — the UI text never claims an instant
  stop; RESUME is enabled only for recoverable durable states and re-enters
  through `load_campaign_state` (a restart performs zero model calls).
- The readiness render carries `INTERNAL READINESS — NOT AN EIC SCORE`
  verbatim (a UI-only module `ui/readiness_disclaimer.py` re-exports the
  domain constant); the history table renders the campaign's persisted
  readiness history; the consensus table renders `panel_consensus.json`
  (unresolved counts included) — nothing recomputed.
- Tests: 31 new offline cases in `tests/test_session_020.py` covering the
  §24 matrix (construction, importers incl. replace-confirmation,
  selector discovery incl. profile-switch invalidation, GENERATE
  preconditions, panel-path proof, campaign config from UI, boundary
  semantics, zero-call restart, disclaimer/history/consensus rendering,
  WAITING_FOR_OPERATOR, gate table, Coding Mode + tab regression). Suite:
  **1158 passed, 0 failed** (1127 + 31).
- New acceptance: `scripts/session_020_ui_acceptance.py` (offline,
  scripted drivers) drives init → imports → agents → generation → panel →
  rendered state → zero-call restart and ends `PROPOSAL V2 UI ACCEPTANCE
  PASSED`. The three prior acceptances stay green.
- Smoke: `python main.py --smoke-test` (and the packaged exe) now also
  proves the V2 panel constructs (`proposal_v2_ui=ok`): controls, 4 agent
  rows, scrollability, disclaimer, 14-gate table. Zero model calls.

No new ADR: Session 020 introduces no new durable UI architectural
decision — it composes D-071…D-076 through the proven S017 worker/panel
patterns (the S017A "panel is a renderer + worker owns execution"
discipline is unchanged).

### LIVE MINI VALIDATION (Session 020, brief §31)

`scripts/session_020_live_mini.py <profile>` runs ONE controlled live
acceptance: a disposable fictional workspace (tiny blueprint/template) →
initial generation (4 real calls) → ONE panel-chair iteration (7 real
calls).  First run exposed TWO real findings, both handled:

1. The real Hermes agent children ran with cwd/`--in` INSIDE the proposal
   workspace and one 'helpfully' wrote a draft into MASTER_PROPOSAL.md —
   the runtime's stale-input write guard correctly refused the synthesis
   write (`stale_input`).  The harness now starts every live child in a
   scratch directory (`NonWorkspaceDriver`); the production SessionRequest
   contract is unchanged and the guard needs no change — it did exactly
   its job against a real mutating agent.
2. The real model returned review patches with BOTH content fields empty
   — the strict parser rejected them (`ambiguous_patch`, fail-closed).
   Fixed at the PROMPT (never the parser): the review packet's hard output
   rules now state the exactly-one-of rule explicitly.

Final run against the `default` profile (glm-5.3-flash): initial
generation COMPLETED (8,325-char master written by the runtime); the ONE
panel-chair iteration then completed `READY_FOR_NEXT_ITERATION` — 3
parallel real evaluator first-passes, 3 parallel consensus calls over the
real docket, and ASTRA revising the master (8,325 → 8,391 bytes) with the
revision handoff written.  All durable artifacts landed
(`review_bundle.json`, `panel_docket.json`, `panel_consensus.json`,
`readiness.json`, integration brief).  Script output: `LIVE MINI TEST
PASSED`.  Real model calls in the accepted run: 4 (generation) + 7
(panel-chair); no campaign; the disposable workspace was deleted.
