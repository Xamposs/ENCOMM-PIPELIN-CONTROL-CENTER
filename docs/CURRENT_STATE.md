# Current State — ENCOMM Pipeline Control Center

**This is the canonical handoff file.** It describes exactly what exists today.

---

## 0. MANDATORY NEW-SESSION HANDOFF CONTRACT

Every new development session — human or agent — must begin by reading, in
order:

1. `docs/CURRENT_STATE.md`   ← this file
2. `docs/ARCHITECTURE.md`
3. `docs/DECISIONS.md`
4. `docs/ROADMAP.md`
5. the most recent report in `docs/reports/`

and must use the **LeanCTX** skill (`encomm-leanctx`) for repository discovery
before exploring or editing code.

**No critical architectural information may exist only in chat history.**
Anything a future session needs must be in this repository. If you learn
something that changes the architecture, write it into `ARCHITECTURE.md` and
append an entry to `DECISIONS.md` before you finish.

**Read §5 "Known limitations" before claiming anything works.**

---

## 1. Version

`1.0.2` — Deterministic three-reviewer proposal review cycle (Session 014).
Proposal Mode gains the REAL review loop: source validation, version
freeze, three strictly-sequential reviewers over ONE frozen revision,
deterministic aggregation, the integration brief, and durable
`04_REVIEWS/` + `06_VERSIONS/` artifacts with file-based safe resume — all
fail-closed, zero model calls in the suite. Coding Mode is unchanged: the
1.0.1 production surface (Simple Mode default, `same_as_orchestrator`
shared-Architect-thread continuity, Advanced/Details behind `--debug-ui`)
is fully subsumed. The test suite is **822 passed, 0 failed** (759 at the
Session 013 merge). References:
`docs/reports/SESSION_014_THREE_REVIEWER_CYCLE.md`,
`docs/reports/SESSION_013_PROPOSAL_REVIEW_RUNTIME.md`.

**Proposal Mode foundation (Session 012) is MERGED into `main`** via
**PR #1** (merge commit `c82d688fadc3a47324efb25b163a22acd2cd3190`, feature
commit `6742c3987b31fa18519e434acbdbbf530356fca7`). The isolated parallel
domain `src/encomm_pcc/proposal/` exists beside Coding Mode: enums
(`ProposalRole`, `ProposalPhase`, verdicts, severities, hard-gate statuses),
strict JSON-friendly models, an explicit deterministic phase state machine
and the idempotent proposal workspace contract — 47 foundation tests.
Post-merge baseline on `main`: **667 passed, 0 failed** (620 + 47). At the
Session 012 boundary no proposal UI, execution, persistence or hard-gate
validators existed (D-054…D-056;
`docs/reports/SESSION_012_PROPOSAL_MODE_FOUNDATION.md`).

**Proposal review runtime bridge (Session 013 + 013A) is MERGED into
`main`** via **PR #2** (merge commit
`7ff806861955bfb698ed3512d2e9f1709de9180e`; Session 013 feature commit
`d89b894c686c51ca96a8746daba131629bc4a24c`; strict-envelope correction
`7ba0ab87ffb23f1ba761fc0989e1b9cfe914e96e`). ONE-reviewer execution
infrastructure, still UI-free: the canonical SHA-256 proposal fingerprint
(`proposal_fingerprint`, exact bytes, D-057), the deterministic reviewer
prompt packet with non-negotiable read-only rules, the fail-closed review
parser (strict envelope + JSON contract, typed rejections), the bounded
read-only workspace snapshot loader, and the SMALL execution adapter package
`src/encomm_pcc/proposal_runtime/` (D-058) that drives an injected
`BaseDriver` through the packet→driver→parser→guarded-result flow with a
before/after MASTER_PROPOSAL immutability guard. Phase→role mapping
(SCIENTIFIC_REVIEW→SCIENTIFIC_REVIEWER, IMPLEMENTATION_REVIEW→
PROPOSAL_ENGINEER, RED_TEAM_REVIEW→RED_TEAM_REVIEWER) is enforced BEFORE any
driver call; only a fully valid execution advances the phase graph (BLOCKED
verdicts take the explicit D-055 BLOCKED edge; INTEGRATION is reached but
never left; COMPLETE stays reserved). All 86 new tests are offline
(scripted driver, zero model calls). Post-merge baseline on `main`:
**759 passed, 0 failed** (673 + 86 at `7ff8068`). At
the Session 013 boundary NO autonomous multi-review loop existed
(D-054…D-058; `docs/reports/SESSION_013_PROPOSAL_REVIEW_RUNTIME.md`).

**Deterministic three-reviewer cycle (Session 014 — branch
`proposal-session-014`, NOT merged to `main`).** The review loop is REAL and
fail-closed: `run_review_cycle()` walks IDLE → SOURCE_VALIDATION (workspace
exists, MASTER_PROPOSAL non-empty/readable, fingerprint computes, bounded
snapshot matches the fingerprinted bytes, iteration number valid — all
BEFORE any driver call) → version freeze into `06_VERSIONS/` (exact bytes +
JSON sidecar) → SCIENTIFIC_REVIEWER → PROPOSAL_ENGINEER → RED_TEAM_REVIEWER
(strictly sequential, same frozen revision, hash re-verified before EACH
reviewer and before aggregation) → deterministic aggregation
(`proposal/review_aggregation.py`, verdict rule BLOCKED > NEEDS_REVISION >
PASS, severity→reviewer→original finding order, exact-duplicate MARKING, no
0–100 score) → integration brief (pure structured projection,
`integration_required` flag) → durable artifacts in `04_REVIEWS/iteration_NNN/`
(3 review JSONs + bundle + brief; deterministic JSON, atomic writes,
conflict-fail-closed, transcripts never persisted). Safe resume: completed
review phases reuse their durable artifacts when identity matches
(iteration + role + hash); anything inconsistent fails closed
(ARTIFACT_CONFLICT/STALE_PROPOSAL/REVIEW_FAILED/SOURCE_VALIDATION_FAILED).
63 new offline tests; full suite **822 passed, 0 failed** (v1.0.2).
No proposal UI, no INTEGRATION executor, no hard-gate validators, no score,
no parallel reviewers yet (D-054…D-060;
`docs/reports/SESSION_014_THREE_REVIEWER_CYCLE.md`).

---

## 2. What currently works

| Capability | Status | Evidence |
|---|---|---|
| Desktop application launches | **Works** | `python main.py` starts, Qt event loop runs |
| **Simple Mode is the DEFAULT production surface** | **Works — Session 011** | stack index 0; the Advanced/Details window (full Session 008 window) is index 1 and exists ONLY behind `--debug-ui` (§17); normal launches expose no Advanced button |
| Simple Mode controls write the SAME durable role configs | **Works — §34** | profile + **provider + model** (Session 010) persist through `set_role_config`; no parallel config model |
| ARCHITECT = ORCHESTRATOR + FINAL_AUDITOR (one Codex thread) | **Works — Session 011** | `same_as_orchestrator` means the SAME real thread: the Final Audit RESUMES the planning session (live → durable binding → persisted plan after restart), proven by the driver's real `resume_session` calls; the Final Audit inherits the Architect's profile/provider/model |
| **Continuous Run executes through the core ContinuousRunner** | **Works — Session 010** | Simple START/CONTINUE + Continuous → worker actions `continuous`/`continuous_resume`; the UI owns no loop logic; stop reasons are translated to operator language (§8) |
| Automatic Final Audit inside the continuous loop | **Works** | `ContinuousRunner._continue_loop` runs `run_final_audit()` after every `READY_FOR_FINAL_AUDIT` batch |
| **Zero-AI next-batch handoff in the loop** | **Works** | PASS consumes the pending plan via `start_next_batch()`; exactly 1 planning call for the whole run (test-pinned) |
| **One-shot Coder recovery override** | **Works — Session 010** | `arm_coder_recovery()`/consume/clear; resumes the saved session for exactly ONE build/fix; `always_new` policy unchanged; Simple Mode recovery affordance preselects the saved session id |
| **Simple Mode provider/model round-trip** | **Works — Session 010** | Coder + Auditor provider/model persist and reload from durable state |
| Auditor session discovery is PROFILE-SCOPED | **Works — Session 009** | `hermes -p <profile> sessions list` (Priority A) with read-only `state.db` metadata fallback (Priority B); strict session-id shape; no transcripts/credentials read |
| Binding validity covers engine AND profile | **Works — §19** | a binding recorded under another profile is inactive and cleared on profile change |
| Codex session selector (Architect) | **Works — Session 006/009** | Simple + Advanced surfaces bind/clear discovered Codex threads read-only |
| Role-based architecture (4 independent roles) | **Works** | `AgentRole`, `AgentRoleConfig`, engine = configuration |
| Engine abstraction | **Works** | Hermes, Codex, Generic CLI are real; registry-driven |
| SQLite persistence | **Works** | Schema **v5**, 9 tables |
| Strict fail-closed parsing (plan / verdict / final audit) | **Works** | malformed output can never become PASS |
| Audit/fix loop, capped | **Works** | `MAX_AUDIT_ROUNDS = 3`, escalation to BLOCKED |
| Session lifecycles | **Works** | BUILDER `always_new` (implement AND fix), TASK_AUDITOR `persistent_per_batch` (survives restarts, D-047), ORCHESTRATOR `persistent_optional` |
| Restart recovery | **Works** | phase + task states + session ids reload from SQLite; nothing auto-runs |
| Windows packaging | **Works — Session 008** | `dist/ENCOMM-PCC/ENCOMM-PCC.exe` + `--smoke-test` |
| Per-user data root | **Works — Session 008** | `%LOCALAPPDATA%\ENCOMM Pipeline Control Center\`, `ENCOMM_PCC_DATA_DIR` override |
| DIAGNOSTICS / HISTORY / config export-import / bounded retention | **Works — Session 007/008** | read-only surfaces over durable tables |
| Automated tests | **Works** | **620 passed, 0 failed** at the Session 011 baseline (604 at Session 010, 588 at Session 009) |
| Real mixed-engine acceptance | **Works — Session 008** | Codex Orchestrator → 2 fresh Hermes Builders → ONE persistent Task Auditor across a controlled restart → Codex Final Auditor; final verdict + next plan; zero-AI START NEXT BATCH |

### Verified at the end of Session 009

- `python -m pytest` → **588 passed, 0 failed** (17 new cases in
  `tests/test_session_009.py`).
- Deterministic two-batch continuous proof: 1 planning call, 1 zero-AI
  handoff, 2 final audits, STOP/PAUSE/non-PASS all stop safely.
- Launcher repair: `resolve_executable_path` prefers the distribution shim —
  the venv `hermes.exe` on this host exits 0 with EMPTY stdout for machinery
  commands (root-caused 2026-09-27; the real CLI lives at
  `%LOCALAPPDATA%\hermes\bin\hermes.exe`).

### Verified at the end of Session 010 (offline)

- `python -m pytest` → **604 passed, 0 failed** (16 new cases in
  `tests/test_session_010.py`).
- Continuous wiring proven through the REAL `MainWindow` → worker → core path:
  Simple START + Continuous runs two batches with exactly one planning call,
  one zero-AI handoff and two final audits; STOP before the next final audit,
  PAUSE at a batch boundary, and a non-PASS final audit all stop the loop with
  the correct reason.
- A latent worker bug was found and fixed: `start_executor_worker` silently
  dropped `resume`/`project_brief`/`batch_size`/`next_batch_size`.
- A `ContinuousRunner` stop-reason bug was found and fixed: a stop honoured
  mid-batch (flag cleared by `BatchRunner`'s boundary) was reported as PAUSED;
  the STOPPED outcome is now the deterministic stop signal.
- One-shot Coder recovery matrix: recovery UI offers the saved interrupted
  session; the override resumes it for exactly one operation; the next Builder
  operation is brand-new; resume failure fails honestly and still consumes the
  override.
- Both `--fake` smokes (`session_004`, `session_005`) → SMOKE PASSED.

---

## 3. Current repository structure

```
ENCOMM PIPELINE CONTROL CENTER/
├── main.py                     Entry point
├── pyproject.toml              Metadata + packaging + pytest config
├── requirements.txt            PySide6 (only runtime dependency)
├── requirements-dev.txt        + pytest
├── .gitignore                  Ignores *.db, logs, .env, secrets
├── README.md
├── scripts/                    Session 002…008 smokes + acceptance scripts
│   ├── session_002_smoke.py            real Hermes executor smoke
│   ├── session_003_audit_fix_smoke.py  real audit/fix loop smoke
│   ├── session_004_multitask_smoke.py  batch smoke (--fake offline mode)
│   ├── session_005_final_audit_smoke.py  final-audit smoke (--fake offline)
│   ├── session_006_codex_smoke.py      Codex new + resume smoke
│   ├── session_008_generic_cli_proof.py  ONE real Generic CLI call
│   ├── session_008_acceptance.py       real mixed-engine acceptance
│   └── session_008_config_roundtrip.py config export/import round trip
├── src/encomm_pcc/
│   ├── __init__.py             __version__ = "1.0.2"
│   ├── app.py                  run(), run_smoke_test(), build_controller(),
│   │                           restore_state(), discover_hermes_profiles()
│   ├── domain/                 enums, models, audit, batch_plan, final_audit,
│   │                           state_machine
│   ├── proposal/               ISOLATED parallel domain (Sessions 012–013):
│   │                           enums, models, state_machine, workspace,
│   │                           fingerprint (canonical proposal_hash),
│   │                           review_packet, review_parser,
│   │                           source_snapshot — imports nothing from the
│   │                           packages below nor from proposal_runtime;
│   │                           coding pipeline untouched
│   ├── proposal_runtime/       (Session 013) SMALL execution-adapter
│   │                           package: review_executor run_review()
│   │                           (packet → injected BaseDriver → fail-closed
│   │                           parse → guarded result + MASTER_PROPOSAL
│   │                           immutability); imports ONLY proposal +
│   │                           drivers.base/domain.enums generics
│   ├── drivers/                base, process, hermes(+hermes_cli),
│   │                           codex(+codex_cli, codex_discovery),
│   │                           generic_cli(+generic_cli_config),
│   │                           session_discovery, hermes_discovery, registry
│   ├── core/
│   │   ├── controller.py       PipelineController (state, bindings, brief)
│   │   ├── executor.py         Executor (plan/build/audit/fix/final audit/
│   │   │                       start_next_batch + one-shot Coder recovery)
│   │   ├── batch_runner.py     BatchRunner (autonomous single batch)
│   │   ├── continuous_runner.py  ContinuousRunner (batch-after-batch loop,
│   │   │                       ContinuousRunReport, ContinuousStopReason)
│   │   ├── session_manager.py  decide_session_action + SessionManager
│   │   ├── hermes_profiles.py  read-only profile discovery
│   │   ├── plan/verdict/final_audit parsers + packets (fail-closed)
│   │   ├── repo_fingerprint.py read-only git guard
│   │   ├── config.py, events.py, history.py, config_exchange.py
│   │   ├── diagnostics.py
│   ├── persistence/            schema.sql (v5), database.py
│   └── ui/
│       ├── simple_mode.py      SimpleModePanel — the DEFAULT surface
│       │                       (PROJECT/GOAL/ARCHITECT/CODER/AUDITOR/
│       │                       tasks-per-batch/CONTINUOUS/START-PAUSE-STOP/
│       │                       STATUS/RECOVERY + one-shot Coder override)
│       ├── main_window.py      MainWindow (mode stack, worker wiring,
│       │                       continuous actions, report capture)
│       ├── worker.py           ExecutorWorker + start_executor_worker
│       │                       (dispatch/audit/fix/batch/final_audit/
│       │                       continuous/continuous_resume)
│       ├── panels.py           RolePanel, BatchPanel, TaskPanel, …
│       ├── diagnostics_panel.py, history_panel.py
├── ENCOMM-PCC.spec             PyInstaller one-folder build spec
├── dist/                       Windows build output (never committed)
├── tests/                      604 tests across 33 files
└── docs/                       THIS file, ARCHITECTURE, DECISIONS, ROADMAP,
    └── reports/                SESSION_001…009 (+ this session's report)
```

---

## 4. Key architectural decisions (summary)

Full reasoning in `DECISIONS.md`. The ones a future session must not undo
without a new ADR:

| # | Decision |
|---|---|
| D-001…D-018 | Foundation: PySide6-only UI, SQLite, role↔engine independence, capabilities-driven, honest executorStarted, boundary-only stop/pause |
| D-019…D-025 | Strict audit verdicts, capped fix loop, auditor session lifecycle, `READY_FOR_FINAL_AUDIT` terminal |
| D-026…D-028 | Strict plan parser, planning-only read-only-guarded Orchestrator, BatchRunner owns sequencing |
| D-029…D-033 | Schema v4/v5, ONE-call Final Auditor (verdict + next plan), operator states on failure |
| D-034…D-038 | Codex CLI contract + durable external-session binding + capability-driven selector |
| D-039…D-043 | Generic CLI driver + durable validated config + config exchange + bounded retention + history read model |
| D-044…D-047 | Per-user data root, packaged `--smoke-test`, bounded bootstrap log, auditor continuity across restart |
| D-048 | Simple Mode is the default operator surface; its controls write the SAME durable role configs (Session 009) |
| D-049 | Continuous execution is owned by the core `ContinuousRunner`, invoked through the worker actions `continuous`/`continuous_resume`; the UI never re-implements the loop (Session 010) |
| D-050 | The Coder recovery override is ONE-SHOT: armed explicitly with a real session id, consumed by exactly one Builder operation; `always_new` itself is never changed (Session 010) |
| D-054…D-056 | Proposal Mode FOUNDATION (merged via PR #1, Session 012): fully isolated parallel domain + idempotent never-overwriting workspace contract (D-054); explicit proposal phase graph with a permanently closed COMPLETE and operator-only FAILED escape (D-055); fail-closed canonical hard-gate identifiers, no validators yet (D-056) |
| D-057, D-058 | Proposal review runtime bridge (merged via PR #2, Session 013): canonical SHA-256 exact-bytes proposal fingerprint is the ONE `proposal_hash` algorithm (D-057); `proposal_runtime` is the ONLY execution-adapter package — pure `proposal` stays import-clean, Coding Mode never imports proposal packages, one-review executor enforces phase→role mapping before driver contact and a before/after MASTER_PROPOSAL immutability guard (D-058) |
| D-059, D-060 | Three-reviewer cycle (branch `proposal-session-014`, Session 014): review aggregation is a PURE deterministic contract — verdict rule BLOCKED > NEEDS_REVISION > PASS, severity→reviewer→original finding order, exact-duplicate MARKING, no numeric score, integration brief as structured projection (D-059); durable review artifacts are the ONLY persistence — `04_REVIEWS/iteration_NNN/` + `06_VERSIONS/` freeze, deterministic atomic JSON, conflict-fail-closed never-clobber, file-based safe resume keyed on (iteration, role, hash), transcripts never persisted (D-060) |

---

## 5. Known limitations

Stated bluntly so nothing is over-claimed:

1. **Live acceptance for Sessions 009/010 PASSED (2026-09-27).** Real Codex
   planning + final audit, 2 real Hermes builds in distinct sessions, ONE
   auditor session, durable FINAL PASS + pending next plan, zero-AI handoff,
   live STOP-at-boundary through the continuous machinery
   (`scripts/session_010_acceptance.py`). It remains a one-off proof — it is
   not part of the automated suite and should be re-run after engine-CLI
   upgrades.
2. **Pause/stop are boundary-only** (a running prompt finishes and persists
   its result first). No mid-prompt cancellation.
3. **Planning/audit guards are vacuous on non-git workspaces.**
4. **A final-audit NEEDS_FIX/BLOCKED lands in `BLOCKED`** with findings
   persisted — resolving it is an operator decision.
5. **UI tests are offscreen only.**
6. **No installer** — one-folder PyInstaller build.
7. **The Claude/OpenCode/Ollama/Kimi dedicated adapters do not exist**; the
   Generic CLI driver covers simple third-party CLIs (live-proven in
   Session 008).
8. **The Coder recovery override is process-scoped, not durable** — it is
   armed in the running process by an operator decision (after a restart, the
   recovery affordance is what exposes it; the run that consumes it executes
   in the same process).

---

## 6. Files most likely relevant next

| File | Why |
|---|---|
| `docs/USER_GUIDE.md` | Simple-Mode-first operator guide (rewritten Session 010) |
| `src/encomm_pcc/ui/simple_mode.py` | The default surface incl. recovery override |
| `src/encomm_pcc/core/continuous_runner.py` | The continuous loop (core-owned) |
| `src/encomm_pcc/ui/worker.py` | The worker actions incl. `continuous` |
| `tests/test_session_010.py` | The Session 010 wiring matrix |
| `scripts/session_008_acceptance.py` | Model for the Session 010 live acceptance script |

---

## 7. Exact next recommended phase

**v1.0 is released.** Session 010 is complete: core wiring, one-shot
recovery, the 604-test offline matrix, the docs rewrite, the live mixed-engine
acceptance PASS, and the Windows rebuild + packaged checks. Candidate items
for a future session (none are promised):

1. Live recovery-override acceptance (interrupt a real build, then resume the
   saved Builder session through the Simple Mode affordance).
2. A `ClaudeCodeDriver` reusing the Session 006 discovery/binding
   infrastructure.
3. Final-audit report drill-down in the Advanced UI (token usage is already
   parsed and persisted).

**Do not start** automatic batch chaining, cloud backends, parallel batches
or installers — still out of scope. See `ROADMAP.md`.
