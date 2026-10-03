# Session 017 — Proposal Mode Operator UI + End-to-End Offline Acceptance

**Version:** 1.3.0 — the FIRST production Proposal Mode operator surface over
the Sessions 012–016 backend. Coding Mode remains the default production
surface and is behaviourally untouched.

## 1. Goal

Deliver the first USABLE Proposal Mode operator surface (brief §2–§12) over
the already-proven Proposal Factory backend, plus the read-only workspace
recovery loader (§13), the offscreen UI test matrix (§16) and the fully
offline end-to-end acceptance (§17). No backend redesign; no SQLite
migration; no Coding Mode regression.

## 2. Baseline

- Authoritative main: `8b2411abb37132a100ac195d6b4c70c101232263`
  (Session 016 + 016A merged via PR #5; feature commit
  `e351c42788e8f5bd108bb79b1ad65ca89f989130`; corrective commit
  `69668e8836d8e2aa1c9eda6283580dcf7f69e126`).
- PRE-test: **1021 passed, 0 failed** (verified on this host, Python 3.12).

## 3. Architecture — what was added where

```
MainWindow.mode_stack
  index 0  SimpleModePanel          (UNCHANGED default surface)
  index 1  Advanced/Details view    (UNCHANGED, --debug-ui only)
  index 2  ProposalModePanel        (NEW — Session 017)
```

| Module | Role |
|---|---|
| `ui/proposal_mode.py` | `ProposalModePanel`: WORKSPACE (init/refresh over the idempotent `ProposalWorkspace`), AGENTS (4 roles; engine dropdowns from the real `DriverRegistry`; isolated `05_CONTROL/PROPOSAL_CONFIG.json` with atomic writes), RUN (RUN ITERATION / RUN HARD GATES), CURRENT STATE (real `ProposalPhase`), REVIEW RESULTS / HARD GATES / EVIDENCE (renderers over durable artifacts) |
| `ui/proposal_worker.py` | `ProposalWorker` + `start_proposal_worker`: QThread + queued `finished` signal; bounded actions `RUN_ITERATION` / `RUN_HARD_GATES` only — calls the existing `run_iteration()` / `run_hard_gates()`, duplicates nothing, no loop |
| `proposal_runtime/workspace_status.py` | READ-ONLY `load_workspace_status()`: deterministic recovery status from durable artifacts (hard_gates.json → review_bundle + integration_result → partial → ambiguous ⇒ "Recovery requires operator confirmation"); never fabricates phases, never writes |
| `scripts/session_017_proposal_acceptance.py` | Offline scripted end-to-end acceptance (temporary workspace; real runtime code; only driver outputs scripted) |

Real-run wiring follows the Coding Executor pattern: the panel builds
drivers through `registry.create(engine, SubprocessRunner())` — a state-only
registry carrying `NullProcessRunner` can never silently execute a
"production" proposal run.

## 4. Files added / modified

**Added**

- `src/encomm_pcc/ui/proposal_mode.py`
- `src/encomm_pcc/ui/proposal_worker.py`
- `src/encomm_pcc/proposal_runtime/workspace_status.py`
- `scripts/session_017_proposal_acceptance.py`
- `tests/test_session_017.py` (26 tests)
- `docs/reports/SESSION_017_PROPOSAL_UI_ACCEPTANCE.md` (this file)

**Modified**

- `src/encomm_pcc/ui/main_window.py` — mode-stack index 2 + `_show_proposal_mode()` wiring only
- `src/encomm_pcc/ui/simple_mode.py` — the ONE navigation affordance (`PROPOSAL MODE` button + `request_proposal`) per brief §2
- `src/encomm_pcc/proposal_runtime/__init__.py` — exports for the status loader
- `tests/test_proposal_hard_gate_executor.py`, `tests/test_proposal_integration_isolation.py`, `tests/test_proposal_review_loop.py`, `tests/test_proposal_runtime.py`, `tests/test_session_009.py` — contract snapshots updated BY DESIGN (see §7)
- `pyproject.toml`, `src/encomm_pcc/__init__.py`, `tests/test_imports.py`, `README.md` — version 1.3.0

## 5. Test evidence (26 new, all offline/offscreen, zero AI)

`tests/test_session_017.py` — brief §16 matrix:

- **NAVIGATION (1–5):** startup lands on index 0; Advanced stays 1; Proposal is 2; navigation round-trip never mutates Coding role config; BACK returns to 0.
- **WORKSPACE (6–8):** initialise creates the contract; initialise NEVER overwrites an existing MASTER_PROPOSAL (hash-pinned); missing/empty master displayed honestly (MISSING/EMPTY in the status label).
- **AGENTS (9–12):** four proposal roles present; engine dropdowns filled from the real registry ids (sorted); config maps into `ProposalAgentConfig` and round-trips through the isolated JSON file; no provider/model names in the panel source; session id cleared unless the engine's capabilities support sessions.
- **WORKER (13–15):** the driver calls run on a NON-UI thread (thread-identity pinned); scripted RUN ITERATION reaches `READY_FOR_HARD_GATES` with the machine at `HARD_GATE_VALIDATION` and durable artifacts on disk; scripted RUN HARD GATES reaches `COMPLETE` (machine terminal).
- **PHASE HONESTY (16–18):** missing evidence → BLOCKED; WARN (estimated page measurement) → INCOMPLETE, machine STAYS at HARD_GATE_VALIDATION, recovery loader agrees, never COMPLETE; a REVISION_REQUIRED artifact renders REVISION_REQUIRED.
- **GATE TABLE (19–20):** ALL 14 canonical gates rendered in canonical order; deliberately FALSE artifact values (all WARN) render verbatim — the UI never recomputes gate logic.
- **EVIDENCE (21–23):** MISSING / EMPTY / INVALID JSON statuses displayed; the skeleton NEVER overwrites existing content (byte-pinned); the skeleton is NOT pass-ready (unbound iteration 0 + placeholder hash → a gates run can never COMPLETE over it).
- **CODING (24–25):** no Coding pipeline module references the proposal domain; Simple Mode's only change is the navigation affordance.

Full suite after: **1070 passed, 0 failed** (1021 + 26 + 23 from the
Session 017A corrective, below).

Determinism note: the file is run-stable (4 consecutive green runs). An
intermittent offscreen teardown abort was eliminated by parking each worker
thread deterministically (`thread.wait()` + one `processEvents()` hop) so
`deleteLater`-ed worker wrappers never race interpreter teardown.

> **Session 017A corrective note:** live-runtime wiring and deterministic
> recovery gaps found in final review are FIXED on the same branch (no
> version bump, stays 1.3.0; full suite 1047 → **1070 passed, 0 failed**;
> zero Coding Mode changes, zero model calls, zero network):
>
> 1. **`ProposalAgentConfig` profile/provider/model are now REAL runtime
>    inputs.** The Session 017 report's §1 claim that the operator
>    configuration reaches a real run was NOT yet true: the reviewer and
>    orchestrator `SessionRequest`s carried neither the per-role
>    profile/provider/model nor a guaranteed workspace. `run_review()`,
>    `run_review_cycle()`, `run_integration()` and `run_iteration()` now
>    accept optional per-role `ProposalAgentConfig` mappings; the panel
>    forwards the FOUR current AGENTS configs through `ProposalRunSpec`,
>    and the request `workspace_path` is ALWAYS the actual proposal
>    workspace. The values the operator sees are provably the values the
>    drivers receive (recording-driver tests + a new acceptance step).
> 2. **Session ids remain a known limitation** — stored and
>    capability-gated only; no resume-capable proposal runtime path exists
>    and none was added in 017A. Session resume is future work.
> 3. **State-machine recovery is artifact-derived and workspace-bound.**
>    The panel previously fabricated a fresh IDLE machine whenever it had
>    none — a restart at `HARD_GATE_VALIDATION` honestly DISPLAYED that
>    phase while RUN HARD GATES received a machine at IDLE. The machine is
>    now reconstructed from `load_workspace_status()`, tracked per
>    workspace (`_machine_workspace`), and NEVER carried across a
>    workspace switch. Ambiguous recovery fabricates NO machine and no AI
>    call can start ("Recovery requires operator confirmation").
> 4. **`NEXT_ITERATION.json` recovery.** A valid handoff (schema +
>    iteration linkage + `revised_proposal_hash` == current master hash +
>    `previous_reviewed_hash` == bundle hash) recovers
>    `REVISION_REQUIRED` with `next_iteration_number`; a malformed/stale
>    handoff — or a changed proposal whose required handoff is missing —
>    is AMBIGUOUS, never a fake `HARD_GATE_VALIDATION`. A freeze-only
>    interrupted iteration (06_VERSIONS freeze without a reviews
>    directory) is now discoverable. The iteration spinner synchronises
>    from durable state (latest iteration, or the handoff's next
>    iteration), never while a worker runs.
> 5. **`PROPOSAL_CONFIG.json` auto-loads ONCE per selected workspace**
>    (rows repopulated verbatim; invalid/missing config stays
>    unconfigured, never guessed; Coding Mode config untouched;
>    workspace-switch loads THAT workspace's config — nothing leaks).
> 6. **Phase-aware run controls.** RUN HARD GATES is enabled only at
>    `HARD_GATE_VALIDATION`; RUN ITERATION only at IDLE / SOURCE_VALIDATION
>    / a review phase / REVISION_REQUIRED (the exact `run_review_cycle()`
>    entry phases). INTEGRATION (recovery not implemented in this MVP),
>    BLOCKED, FAILED, COMPLETE and ambiguous recovery disable both.
> 7. **A real Session 017 click-path defect found by the 017A recovery
>    tests:** `_start_worker()` never called `thread.start()` — every
>    panel button press left the worker thread unstarted forever
>    ("Running…" stuck, buttons dead). The S017 tests started the worker
>    themselves and never exercised the click path. Fixed and pinned by
>    the item-9 test (RUN HARD GATES genuinely runs from the recovered
>    phase).
>
> 23 new offline regression tests (`tests/test_session_017a.py`, brief
> §13 matrix); the offline acceptance gains a SessionRequest
> profile/provider/model/workspace proof step (still zero AI).

## 6. Acceptance (§17 — offline, scripted)

`python scripts/session_017_proposal_acceptance.py` (exit 0):

```
[PASS] workspace init — 23 path(s) created
[PASS] run_iteration (3 scripted reviewers → integration) — outcome=READY_FOR_HARD_GATES, machine=HARD_GATE_VALIDATION
[PASS] durable review artifacts
[PASS] operator evidence authored + bound (iteration 1, exact hash)
[PASS] run_hard_gates (14 canonical gates, zero AI) — outcome=COMPLETE, counts={'pass': 14, ...}
[PASS] state machine terminal COMPLETE
[PASS] workspace recovery status reads COMPLETE
PROPOSAL ACCEPTANCE PASSED
```

Real production runtime code end-to-end (`run_iteration` → `run_hard_gates`);
the ONLY scripted part is the three reviewer driver outputs (clean PASS
envelopes). The temporary scratch workspace is removed; the PCC repository
is never modified. The smoke self-test passes: `SMOKE OK version=1.3.0 …
main_window_constructed clean_shutdown`.

## 7. Contract snapshots updated by design

Session 017 legitimately changes the UI package, so four structural pins
were sharpened (never deleted):

1. `test_59` / `test_52` / `test_44` (isolation scans): the ONLY
   proposal-referencing files allowed under `ui/` are `proposal_mode.py`,
   `proposal_worker.py` and the two wiring files (`main_window.py`,
   `simple_mode.py`); core/domain/drivers/persistence stay proposal-free.
2. `test_43` / `test_35` (subprocess import-clean probes): drop
   `encomm_pcc.ui` from the banned-import probe — the UI package now hosts
   the Proposal surface; the CODING pipeline modules remain probed.
3. `test_session_009` stack-count pin: `mode_stack.count() == 3` with
   `widget(2) is proposal_panel`.

## 8. Documentation changes

- `docs/CURRENT_STATE.md` — §1 corrected (PR #5 merge state) + 1.3.0 entry; §2 capability row; recovery-loader structure note.
- `docs/USER_GUIDE.md` — Proposal Mode operator chapter (flow: Workspace → Agents → Run Iteration → Hard Gates → COMPLETE / Revision / Blocked).
- `docs/DECISIONS.md` — ADR **D-069** (Proposal Mode config + worker/UI architecture) appended; no historical ADR rewritten.

## 9. Known limitations

1. **One iteration per button press.** The UI deliberately exposes no
   automatic loop; the operator re-presses after each report.
2. **RUN NEXT STEP is not implemented** (the brief listed it optional);
   phase-dependent enable/disable of the two run buttons is.
3. **Proposal config is file-scoped** (`05_CONTROL/PROPOSAL_CONFIG.json`);
   there is no SQLite migration (per brief §5).
4. **Session ids in Proposal Mode are stored but not yet consumed by a
   resume-capable proposal driver path** — the field exists and is
   capability-gated; wiring resume into the proposal executors is future work.
5. **REAL ENGINE ACCEPTANCE: NOT RUN — prerequisites unavailable.** The
   scripted acceptance is the recorded evidence; no real-model proposal run
   was performed in this session.
6. The gate table renders NOT_RUN (UI-only) for gates without an artifact;
   that value is never written into domain artifacts (verified by test 20).

## 10. Exact next recommended session (Session 018)

First LIVE end-to-end proposal acceptance over a real mini workspace (tiny
engines, one iteration), plus resume-capable proposal session wiring and —
only after the operator surface stabilises — the DOCX/PDF evidence tooling
still out of scope.

## GIT_STATUS

- Branch: `proposal-session-017`
- Commit: `feat(proposal): add Proposal Mode UI and end-to-end acceptance (Session 017)`
- Pushed to `origin/proposal-session-017`; NOT merged to `main` (per brief).
