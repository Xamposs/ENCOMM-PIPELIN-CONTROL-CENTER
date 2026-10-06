# Session 023 — Proposal Mode Simple UI + Hermes Discovery Resilience

**Branch:** `proposal-simple-ui-session-023` (from `main` @ `4bb56bf`) — **DO NOT MERGE YET**
**Version:** 1.5.1 → **1.6.0** (minor: meaningful UI change)

## Summary

Two deliverables:

1. **§A Discovery resilience** — an unreadable/unrelated optional Hermes
   plugin (`PermissionError [WinError 5]` while scanning
   `plugins/<name>/plugin.yaml`) could make the ENTIRE provider/model
   catalog unavailable in packaged v1.5.1, surfacing a raw exception to
   the operator. Discovery now fails soft down the exact brief fallback
   chain: authoritative inventory → the SELECTED PROFILE's own configured
   defaults (new `proposal_runtime/hermes_profile_defaults.py`) → manual
   override in Advanced. No plugin file is ever touched; no permissions
   change; no credential read; zero model calls.
2. **§B–§L Simple Mode** — the Proposal Mode surface becomes a
   three-section workflow (1. PROJECT / 2. AI TEAM / 3. RUN) + a Simple
   PROGRESS strip. Every technical surface remains, behind an explicit
   `ADVANCED SETTINGS / VIEW DETAILS` toggle (collapsed by default).
   The backend/runtime is untouched except for the catalog fallback.

## Real PermissionError reproduction + resolution

**Reproduction (S022 code, packaged 1.5.1):** the discovery bootstrap
imports `hermes_cli.inventory` and calls `load_picker_context()`. At
import, `hermes_cli.config` runs `_inject_platform_plugin_env_vars()`
which `os.stat()`s `<home>/plugins/<name>/plugin.yaml` for every user
plugin; one unreadable manifest escaped the "swallowed" handler and the
`PermissionError` propagated out of `load_picker_context()` into the
bootstrap's typed error document
(`hermes_inventory_failed: PermissionError: [WinError 5] Access is
denied: ...\plugins\homesassistant\plugin.yaml`).

**Timeline proof:** `plugins/homesassistant` (created 09:52 with bad
ACLs) was renamed to `homeassistant` at 10:03 — an hour after the
packaged failure was captured; the offending path no longer exists, so
live discovery succeeds today. The failure CLASS is what S023 removes.

**Resolution (PCC-side contract, per the brief — never modify Hermes):**
- `hermes_model_catalog.discover_hermes_model_catalog`: EVERY failure
  path (runner exception, timeout, non-zero exit, payload-level
  `error` — including the PermissionError document) degrades through
  `_profile_config_fallback`: catalog `source="profile-config"`,
  `profile_default_provider/model` populated from the profile's real
  `config.yaml`, the provider LIST honestly EMPTY,
  `provider_catalog_authoritative=False`.
- `hermes_profile_defaults.discover_hermes_profile_defaults`: the
  fallback child runs under the Hermes venv python, `HERMES_HOME`-scoped
  to the profile directory, and **NEVER imports `hermes_cli`** — it
  reads only `<profile>/config.yaml` (PyYAML) and prints one strict JSON
  document. The plugin-scan failure class cannot reach this path.
- Injected test runners never launch real fallback children (opt-in
  `handles_profile_defaults` doubles keep tests deterministic).

## Exact fallback mechanism

1. `discover_hermes_model_catalog(profile)` → inventory bootstrap
   (authoritative substrate, unchanged behavior on success).
2. On ANY inventory failure → `_profile_config_fallback` →
   `discover_hermes_profile_defaults(profile)` → the profile's own
   `model.provider` / `model.default` → catalog
   `source="profile-config"`, available, defaults filled.
3. If BOTH legs fail → `source="unavailable"` with BOTH reasons in the
   error string. Never invented names, never raised exceptions.
4. UI: the role card shows the friendly note
   `Hermes model catalogue unavailable.` + `Profile defaults will be
   used.` and the read-only defaults line — never a raw exception (§J).
   The runtime keeps working because empty provider/model = profile
   defaults (`build_chat_argv` emits `-p <profile>` and NO override —
   pre-existing verified contract, requirement E, unchanged).

## Simple Mode (final card contract)

- **1. PROJECT**: SELECT WORKSPACE / MANAGE SOURCES + six plain status
  rows (Workspace, Master Blueprint, Living Blueprint, Official
  Template, Official Documents N loaded, Master Proposal). Importers and
  budget/diagnostic text live in ADVANCED.
- **2. AI TEAM**: four role cards.
  - Hermes evaluator card: Engine, Profile, read-only
    `Using profile defaults: <provider> / <model>` + READY/warning
    state, Refresh. NO Provider/Model override/Session Mode/Session.
  - ASTRA / Codex card: Engine, Model, Reasoning, Session, Refresh;
    NO Profile/Provider.
  - REFRESH on a fresh (unconfigured) panel discovers REAL profiles and
    preselects the §I defaults (ASTRA→codex; scientific /
    implementation / red-team when those profiles exist); operator
    choices are never overridden.
- **3. RUN**: GENERATE INITIAL PROPOSAL → RUN PANEL REVIEW → START
  AUTONOMOUS REFINEMENT (+ PAUSE/RESUME/STOP by campaign state). Legacy
  RUN ITERATION and RUN HARD GATES remain wired in ADVANCED.
- **PROGRESS**: Proposal / Iteration / Internal readiness / Hard Gates /
  Panel / Current activity + the mandatory disclaimer.
- **ADVANCED SETTINGS / VIEW DETAILS**: every Session 020 surface
  (importers, provider/model/session-mode overrides, campaign limits,
  legacy run controls, hash/state detail, panel results, consensus,
  14-gate table, evidence) — nothing deleted.

## Tests

- `tests/test_session_023.py` — 22 tests: PermissionError payload →
  profile-config fallback; timeout/injected-runner honesty; defaults
  module hermetic contract; friendly note + defaults line; Codex card
  shape; §I preselection (+ never-overridden); empty provider/model argv
  contract; and the **native-Qt stress regression** (repeated ADVANCED
  toggle + panel show/hide, one-layout invariant over the full layout
  tree — every persistent widget/label exactly once, exactly one
  layout).
- Full suite: **1306 passed, 0 failures** (baseline 1284 + 22).
- S022's `test_refresh_with_no_engine_calls_nothing` SHARPENED by design
  (fresh-card REFRESH now preselects from real profiles; the no-profiles
  honest-note path is still pinned).

## Crash-class fixed during this session (documented for future sessions)

A native access violation on `setVisible(True)` had TWO co-root causes,
both bisect-proven: (1) a widget added to TWO layouts (stale
QLayoutItem); (2) `addWidget` into a NOT-YET-INSTALLED (floating)
layout — floating layouts never adopt widgets, leaving them parentless;
Qt later crashes showing/painting them. Fix: single-layout ownership +
DEFERRED `_place_role_widgets` (only after `_build_advanced_section`
installs the grids) + a hidden panel-owned parking container for
detached widgets (never parentless). Pinned by the stress regression.

## Packaged build / smoke / visual acceptance

- `powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1`
  → `BUILD OK dist\ENCOMM-PCC\ENCOMM-PCC.exe` (final rebuild after the
  last source change).
- Packaged smoke: `SMOKE OK version=1.6.0 … mode_tabs=2(coding,proposal)
  proposal_v2_ui=ok clean_shutdown`, `EXIT=0`.
- Packaged visual acceptance (UIA-driven, PrintWindow screenshots):
  PROJECT rows clean; evaluator cards Engine/Profile/defaults+READY/
  Refresh only; ASTRA card Model/Reasoning/Session/Refresh; REFRESH on
  the fresh app preselected codex + hermes/scientific (+ the exact
  defaults line `Using profile defaults: zai / glm-5.3-flash`) +
  implementation + red-team; PROGRESS strip exact; RUN simple; ADVANCED
  collapsed by default; 6 UIA toggle cycles (via MANAGE SOURCES → the
  same `_toggle_advanced` handler) with the process alive and back at
  collapsed; NO error/exception text anywhere in the normal UI.
- Fallback acceptance (packaged-equivalent offscreen probe over the
  same source): note + defaults line exact, provider list empty,
  config `hermes/scientific/''/''` — PASS.

## GIT_STATUS

- Branch: `proposal-simple-ui-session-023`
- Base SHA: `4bb56bfcf91f552005a79929f048a19a33b38713`
- Commit SHA: see the handoff message (filled at commit time)
- Pushed: `origin/proposal-simple-ui-session-023` — **DO NOT MERGE**

## Known limitations

- The fallback claims ONLY the profile's defaults; the provider LIST is
  unavailable in that mode (honest; combos stay editable; ADVANCED
  exposes the manual override).
- `model_lists_exhaustive` remains False by design (S022).
- The ADVANCED toggle button is virtualized out of Qt's accessibility
  tree while scrolled out of view (Qt a11y behavior); the stress was
  driven through MANAGE SOURCES (the same `_toggle_advanced` handler).
- Codex still has NO model listing (upstream CLI); the model field
  stays editable with the exact-id placeholder.
- The §I preselection is a UI convenience; it never persists until a
  sync runs and never overrides operator/saved choices.
