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

`0.9.0` — Simple Mode + Continuous Run (pre-release; live acceptance pending
per the Session 010 brief). Session 009 delivered Simple Mode as the default
surface, the core `ContinuousRunner`, and profile-scoped Hermes session
discovery. Session 010 closed the reviewer-identified gaps: the Simple
START/CONTINUE path now executes through the **core ContinuousRunner** via the
worker actions `continuous` / `continuous_resume` (a UI-owned loop and a
silent worker-parameter-drop bug were found and removed), the **one-shot Coder
recovery override** exists (executor API + Simple Mode UI), Simple Mode exposes
**Provider/Model** for Coder and Auditor on the same durable role configs, and
16 new offline tests (604 total) pin all of it. Remaining for `1.0.0`: live
acceptance, the Windows rebuild + packaged checks, and the final report.

---

## 2. What currently works

| Capability | Status | Evidence |
|---|---|---|
| Desktop application launches | **Works** | `python main.py` starts, Qt event loop runs |
| **Simple Mode is the DEFAULT surface** | **Works — Session 009/010** | stack index 0; Advanced/Details (the full Session 008 window) is index 1 and unchanged |
| Simple Mode controls write the SAME durable role configs | **Works — §34** | profile + **provider + model** (Session 010) persist through `set_role_config`; no parallel config model |
| ARCHITECT = ORCHESTRATOR + FINAL_AUDITOR (one Codex thread) | **Works** | `same_as_orchestrator`; ONE planning call, ONE final-audit call per batch |
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
| Automated tests | **Works** | **604 passed, 0 failed** at the Session 010 baseline (588 at Session 009) |
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
│   ├── __init__.py             __version__ = "0.9.0"
│   ├── app.py                  run(), run_smoke_test(), build_controller(),
│   │                           restore_state(), discover_hermes_profiles()
│   ├── domain/                 enums, models, audit, batch_plan, final_audit,
│   │                           state_machine
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

---

## 5. Known limitations

Stated bluntly so nothing is over-claimed:

1. **Live acceptance for Sessions 009/010 is not yet done.** The continuous
   Simple Mode path, recovery override and provider/model controls are proven
   offline (604 tests) but not yet against real Codex + Hermes model calls.
   That is the explicit purpose of the next live run; `1.0.0` is reserved for
   its PASS.
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

**Session 010 completion — live acceptance, then `1.0.0`.**

1. Live acceptance on a disposable scratch repo: 2-task batch, Codex
   Architect + Hermes Coder `encomm-accounting-intelligence` + Hermes Auditor
   `encomm-auditor`; controlled restart; same Codex thread for plan + final
   audit; distinct Builder sessions; ONE Auditor session; continuous Final
   PASS → zero-AI next batch materialised → STOP at the earliest safe
   boundary.
2. Windows rebuild + packaged `--smoke-test` + one packaged GUI launch.
3. Version bump `0.9.0 → 1.0.0`, final report
   `docs/reports/SESSION_010_FINAL_V1_ACCEPTANCE.md`, push.

**Do not start** new engines, parallel batches, cloud backends or installers —
out of scope for v1.
