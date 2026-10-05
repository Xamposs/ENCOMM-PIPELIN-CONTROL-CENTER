# SESSION 022 REPORT — Proposal Agent Selector / Provider-Model Discovery Fix

**Branch:** `proposal-selector-discovery-session-022` (based on `main` @ `fc46858`)
**Status:** NOT MERGED — awaiting operator review.
**Version:** 1.5.0 → 1.5.1 (bugfix release)

## Summary

The Proposal Mode AGENTS selectors no longer derive providers/models from the
selected profile's `config.yaml` alone (the old behavior surfaced only two
choices on the real host). Discovery now runs the authoritative Hermes
in-package inventory — the SAME substrate Hermes' own pickers use — through a
shipped read-only bootstrap under the Hermes venv's python. Every configured
provider appears with its real per-provider model list; selecting a provider
repopulates models immediately; Codex shows disabled N/A profile/provider with
an editable model; reasoning is Codex-only.

## Real Hermes inspection (STEP 1)

* Installed Hermes: **v0.21.5+6774.g07797d0** (git install,
  `%LOCALAPPDATA%\hermes\hermes-agent`, venv Python 3.14.7).
* `hermes model` is interactive-only — NO non-interactive CLI listing exists.
  Priority A (CLI listing) does not exist; Priority B (stable in-package
  registry API) does and was chosen: `hermes_cli.inventory.load_picker_context()`
  + `build_models_payload(...)` with the ACP picker's OFFLINE shape
  (`refresh=False, probe_custom_providers=False,
  probe_current_custom_provider=False, canonical_order=True`).
* Verified read paths: `load_picker_context()` reads the config through
  **`HERMES_HOME`** (the `HERMES_PROFILE` variable alone does NOT scope it —
  caught live: erp-project first returned the default home's catalog until the
  child env scoped `HERMES_HOME` at the profile directory).
* Payload rows carry only slug/name/models/total/source/is_current/
  is_user_defined — no secret material. No `.env`/auth/credential store is
  touched; no model calls; no network probes (offline flag shape).
* Codex CLI 0.154.0: `codex models list` is an argument error — no verified
  model listing exists, so Codex Model stays EDITABLE with the placeholder
  "Enter exact Codex model id available to this account" (display-only, never
  persisted).

## Real-host acceptance (STEP 8)

Through the production `discover_hermes_model_catalog` via PCC's
`SubprocessRunner`:

| profile | providers | defaults | notable models |
|---|---|---|---|
| scientific | 7: openrouter(59), moa(1), lmstudio(0), copilot(17), deepseek(2), zai(11), tokenharbor(20) | zai / glm-5.3-flash | glm-5.3-flash, deepseek-v4-pro |
| erp-project | 11: nous(60), openrouter(59), moa(1), xai-oauth(9), copilot(17), deepseek(2), zai(13), opencode-zen(84), tokenharbor(64), lm-studio-qwen, ollama | xai-oauth / grok-4.6 | grok-4.6, glm-5.3 |

* Profiles include `scientific`, `implementation`, `red-team` ✓
* Provider list is no longer the 2-entry profile-config subset ✓
* Provider A → A's models; provider B → B's models (per-provider scoping
  proven per profile) ✓
* No fabricated names; no model calls; no credentials read ✓
* If the host had genuinely exposed only two providers, the report would say
  so — it does not: the real host exposes 7–11 per profile.

## Implementation (STEPS 2–7)

* NEW `src/encomm_pcc/proposal_runtime/hermes_model_catalog.py`:
  `HermesModelCatalog` / `HermesProviderEntry` /
  `discover_hermes_model_catalog(profile)` — fail-soft everywhere; strict
  single-JSON parse (no eval); deterministic dedup on first occurrence;
  inventory order preserved; `_profile_home_dir` resolves the profile home
  through the same two-level walk as `profile_roots()`.
* `hermes_selector_discovery.py`: the config-derived
  `discover_hermes_provider_model` / `ProviderModelOptions` are REMOVED
  (superseded, not deprecated); profiles + sessions contracts unchanged.
* `ui/proposal_mode.py`:
  * `_populate_provider_model` returns a note; populates ALL discovered
    providers, preselects the profile default provider + default model
    (when it belongs to that provider); combos stay EDITABLE.
  * NEW `_on_role_provider_changed` (wired to `provider.activated`):
    repopulates models IMMEDIATELY; keeps a manually-entered model only when
    it exists in the newly selected provider's real list; never substitutes
    an unrelated provider's model.
  * NEW `_apply_selector_gating` (capability-driven): profile enabled only
    when `requires_profile`; provider Hermes-only; model per
    `supports_model_selection`; Codex profile/provider DISABLED showing "N/A".
  * NEW `_sync_reasoning_availability`: reasoning enabled Codex-only; the
    DEFAULT row renders "N/A" elsewhere; non-Codex configs always persist "".
  * `_sync_role_config_from_widgets`: Codex persists `project_profile=""`,
    `provider=""`; the placeholder text is display-only (never persisted);
    Hermes never persists a reasoning effort.
  * `QSignalBlocker` wraps every programmatic combo repopulation (no signal
    recursion); both config→widget sync paths (`apply_role_configs`,
    `_sync_role_rows`) restore the engine-honest selector state.

## Tests (STEP 9)

`tests/test_session_022.py` (new, 25 tests) covers the brief's matrix:
catalog parsing, dedup, A→models-A / B→models-B, child-env scoping + argv,
fail-soft (empty/prose/error-payload/malformed/timeout/exit-code/missing
profile/unknown home), UI catalog population with defaults, provider-change
repopulation, unrelated-model refusal, editable combos, unavailable-catalog
honesty, zero worker starts during discovery, profile-change session reset,
Hermes persistence round-trip (reasoning ""), Codex N/A + empty persistence,
placeholder display-only + non-destructive, Codex reasoning + Hermes N/A
transition, generic-engine capability gating, Codex session discovery intact,
structural removal pin. The S020 pinned test moved to the new contract by
design.

**Full suite: 1269 passed, 0 failed** (baseline 1244; +25).

## Version / build (STEP 10)

Version pins bumped: `src/encomm_pcc/__init__.py`, `pyproject.toml`,
`tests/test_imports.py`, `README.md` (+ version blurb).
`git diff --check`: clean. Secret scan over the diff: clean (payload shapes
carry only provider/model names; no credential material anywhere).

Windows build + packaged smoke: run by the operator command in the brief
(`powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1`) —
see GIT_STATUS below for the executed result.

## GIT_STATUS

* Commit: `c68280567238483da71b70603b5497716c18c15b`
  (`fix(proposal-ui): complete Hermes provider-model discovery and engine
  selectors`)
* Remote head `origin/proposal-selector-discovery-session-022`:
  `c68280567238483da71b70603b5497716c18c15b` (verified via
  `git ls-remote`; local HEAD == remote head)
* Build: `BUILD OK: dist\ENCOMM-PCC\ENCOMM-PCC.exe`
* Packaged smoke: `SMOKE OK version=1.5.1 app_data_writable=…
  sqlite_ok=schema_v5 controller_constructed drivers=codex,generic_cli,hermes
  mode_tabs=2(coding,proposal) proposal_v2_ui=ok clean_shutdown` — exit 0.

## Known limitations

* The inventory's curated per-provider model lists can lag a provider's
  newest models; the combos stay EDITABLE so any exact id remains
  operator-enterable (discovery is a suggestion, never a cage).
* The catalog child imports the installed Hermes package's inventory API; a
  future Hermes update that renames it fails SOFT to "unavailable" (typed
  import guard) — the UI then shows empty-but-editable combos, never a
  fabricated list.
* Codex model discovery remains impossible by design (no CLI listing);
  the field is editable with a display-only placeholder.
