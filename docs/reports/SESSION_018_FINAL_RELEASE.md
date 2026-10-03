# Session 018 — Final Dual-Mode UI + Windows v1.3.1 Release

Branch: `release-session-018` (from `main` @ `4ecdbf4f27ba015d41dec7c9cd243412ae427451` —
the Session 017/017A PR #6 merge, exactly the authoritative baseline).

## 1. Goal

Make the two production product modes obvious in ONE shipped executable: a
persistent top-level `CODING MODE | PROPOSAL MODE` tab selector, version
1.3.0 → 1.3.1, full-suite green, offline acceptance green, and a fresh
Windows PyInstaller build (`dist/ENCOMM-PCC/ENCOMM-PCC.exe`) replacing the
old v1.0.1 dist, proven by a packaged `--smoke-test` (exit 0,
`version=1.3.1`, zero model calls).

## 2. Baseline

- `main` fast-forwarded to `4ecdbf4` (PR #6 merge); working tree clean.
- Python 3.12.10 (`%LOCALAPPDATA%\Programs\Python\Python312`).
- Expected 1070-passed baseline was **not** green: a merged Session 017A
  test failed in every full-suite run (order-pollution) while passing
  alone — repaired FIRST (below), then the true 1070 baseline was
  reproduced before any feature work.

### Session 017A test repair (test-shape defect, NO production change)

`TestWorkerConfigPropagation::test_worker_forwards_panel_configs_into_session_requests`
called `panel._on_run_iteration()` — which since the 017A fix REALLY starts
the panel's worker thread — and then started a SECOND worker over the SAME
workspace without joining the first. Two concurrent review cycles raced the
workspace's durable review artifacts; the loser fail-closed with
`ARTIFACT_CONFLICT` (correct behaviour). Prior-test allocation churn merely
shifted the race outcome (why it passed alone). Repair: the test now drains
the panel's OWN worker (the production click path) via a `_pump_panel`
helper reading the panel's race-free `_last_report` slot — one cycle, one
workspace. After repair: **1070 passed, 0 failed** (46.31 s), reproducible.

## 3. Architecture — what was added where

- `ui/main_window.py`: `QTabBar` product selector (exactly TWO tabs,
  `CODING MODE` / `PROPOSAL MODE`) above the mode stack inside one central
  container. Tab 0 → mode_stack index 0, tab 1 → index 2; `_on_mode_tab_changed`
  maps tabs→stack; `_set_mode_tab` (no-op when already selected — no signal
  re-entry) makes `_show_simple_mode` / `_show_proposal_mode` (and therefore
  the Simple PROPOSAL affordance and Proposal BACK) keep ONE navigation
  state. Stack indices 0/1/2 UNCHANGED (D-069 frozen contract). Advanced /
  Details stays index 1, `--debug-ui`-only, NO product tab (D-070).
- `app.py` `run_smoke_test`: packaged smoke now proves the selector with
  zero model calls — exactly 2 tabs, startup CODING/stack 0, proposal tab →
  stack 2 → `proposal_panel`, stack 1 stays `advanced_view`, internal
  navigation sync; emits `mode_tabs=2(coding,proposal)` in the smoke line.
- Version 1.3.1: `pyproject.toml`, `src/encomm_pcc/__init__.py`,
  `tests/test_imports.py`; window title follows `__version__`
  (`ENCOMM Pipeline Control Center — v1.3.1`).

Decision record: **D-070** appended to `docs/DECISIONS.md` (one application,
one persistent top-level product tab bar; refines D-069's layout without
touching the stack indices).

## 4. Files added / modified

- `src/encomm_pcc/ui/main_window.py` — global product tabs + sync
- `src/encomm_pcc/app.py` — packaged smoke selector proof
- `src/encomm_pcc/__init__.py`, `pyproject.toml`, `tests/test_imports.py` — 1.3.1
- `tests/test_session_018.py` — NEW: 13 focused tests (brief §7 items 1–13)
- `tests/test_session_017a.py` — worker test double-booking repair
- `tests/test_proposal_review_loop.py` — `test_44` contract sharpened (by
  design): `app.py` joins the exemption set as the bootstrap WIRING site,
  with an explicit no-proposal-package-import assertion so the exemption
  cannot hide a domain leak
- `README.md`, `docs/CURRENT_STATE.md`, `docs/USER_GUIDE.md` — 1.3.1 + tabs
- `docs/DECISIONS.md` — D-070

## 5. Test evidence (13 new, all offline/offscreen, zero AI)

- Pre-change baseline (after the S017A test repair): **1070 passed, 0 failed**
- Full suite after Session 018: **1083 passed, 0 failed** (48.23 s) —
  1070 + 13 new; brief §7 items 14/15 (existing 017/017A UI tests and all
  Coding Mode tests green) are proven by this run
- `scripts/session_017_proposal_acceptance.py` → `PROPOSAL ACCEPTANCE PASSED`
- Source smoke: `SMOKE OK version=1.3.1 … mode_tabs=2(coding,proposal) … clean_shutdown`
- `git diff --check`: clean; secret scan over the raw diff: clean; no
  db/log/scratch/binary artifacts in the diff

## 6. Windows build + packaged smoke

- Build: `powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1`
  → `BUILD OK: dist\ENCOMM-PCC\ENCOMM-PCC.exe` (PyInstaller 6.22.3 on the
  project Python 3.12.10; build script removed the old `build\` + `dist\`
  including the v1.0.1 exe).
  - Environment notes: the script must find the PROJECT Python first on
    PATH (a bare shell resolves Python 3.14 without PyInstaller); the first
    cleanup attempt failed on `qwindows.dll` Access-Denied because a stale
    v1.0.1 `ENCOMM-PCC.exe` (PID 30560) was RUNNING and locking the old
    dist — it was stopped (`Stop-Process -Force`), the rebuild then
    succeeded. The old app is SQLite-backed and restart-safe by design.
- Dist sanity: fresh exe (same-day timestamp, ~2.6 MB), PyInstaller
  `_internal` support tree present, `schema.sql` data file shipped.
- PACKAGED smoke (isolated data dir `ENCOMM_PCC_DATA_DIR` pointed at a
  scratch path, operator database untouched):
  `SMOKE OK version=1.3.1 … mode_tabs=2(coding,proposal) … clean_shutdown`,
  **exit code 0**, same line in the isolated `bootstrap.log`.

## 7. Contract snapshots updated by design

- `test_44_no_coding_mode_production_file_changed`: `app.py` exemption
  (wiring/proof site) + hard import ban — isolation teeth kept.
- `tests/test_session_017a.py` worker test: production click path is now
  the only execution path in the test (mirrors the S017 click-path rule).
- `tests/test_session_009.py` / `test_session_017.py` stack contracts:
  UNCHANGED and still green (3 widgets, indices 0/1/2).

## 8. Documentation changes

`README.md` (1.3.1 claim + dual-mode navigation), `docs/CURRENT_STATE.md`
(new 1.3.1 version block, 1083 count, release summary), `docs/USER_GUIDE.md`
(tabs replace the button-only wording in the intro and Part P),
`docs/DECISIONS.md` (D-070), this report. Historical reports untouched.

## 9. Known limitations

- Advanced / Details remains a developer surface (`--debug-ui`); it has no
  product tab by design.
- The packaged smoke proves construction + navigation wiring, not visual
  rendering — the operator visual test (double-click the exe, switch tabs)
  is the next step.
- Proposal session-id resume remains DOCUMENTED future work (Session 017A).
- Real proposal-model acceptance is intentionally NOT run in this session
  (zero AI calls); the operator can run it interactively after the visual
  inspection.

## 10. GIT_STATUS

- Branch: `release-session-018` (from `main` @ `4ecdbf4`)
- Commit: `release: finalize dual-mode UI and Windows v1.3.1 build (Session 018)`
- Pushed to `origin/release-session-018`; PR → `main` follows this commit
  (normal merge commit, no squash/rebase; the merge SHA is recorded in the
  session handoff). The final packaged build is produced from this exact
  source tree, which becomes `main` byte-for-byte on the merge.
