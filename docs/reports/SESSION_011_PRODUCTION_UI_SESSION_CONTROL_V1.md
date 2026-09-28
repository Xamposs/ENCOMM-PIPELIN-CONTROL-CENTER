# Session 011 — Production UI Finalisation & True Session Control (v1.0.1)

**Date:** 2026-09-28
**Version:** 1.0.1 (source + package)
**Verdict:** ✅ **GREEN — full suite 620 passed / 0 failed, both packaged gates clean, release pushed.**

## 1. What changed

| # | Change | Where |
|---|---|---|
| 1 | **`same_as_orchestrator` = ONE real Architect thread.** The Final Audit now resumes the SAME external session used for planning — live orchestrator session → durable binding on ORCHESTRATOR → persisted `plan.orchestrator_session_id` after restart — and the session decision is forced to REUSE that thread while the flag is set (`_architect_session_id()`, `SessionDecision` override in `_final_audit`). | `core/executor.py` |
| 2 | **Final Auditor inherits the Architect's profile/provider/model** under `same_as_orchestrator`, so preflight and the session request run under the operator's real configuration (a Hermes Architect no longer blocks the Final Audit on placeholder values). | `core/executor.py` |
| 3 | **Real engine dropdowns.** ARCHITECT/CODER/AUDITOR selectors enumerate `DriverRegistry.driver_ids()`; fields adapt to the engine's capabilities (Hermes: profile + provider/model + session; Codex: session only; stateless: hint). Engine switches invalidate the previous driver's binding. | `ui/simple_mode.py` |
| 4 | **Coder session-mode selector** — *Automatic* (fresh session, default) or *Resume selected session ONCE* (arms the D-050 one-shot override; consumed exactly once; auto-reverts on refresh). | `ui/simple_mode.py` |
| 5 | **Profile-scoped auto-refresh.** Hermes profile changes re-run session discovery scoped to the new profile and auto-clear stale bindings (Architect, Coder, Auditor). | `ui/simple_mode.py` |
| 6 | **Advanced/Details gated behind `--debug-ui`.** Normal launches are Simple-only (no Advanced button); `main.py --debug-ui` restores the full Session 008 window. | `app.py`, `ui/main_window.py` |
| 7 | **Operator language.** Status bar: “Ready — choose a project and configure the pipeline.” / “Ready — state-only mode (no executor attached).”; batch-telemetry status panel shows batch/task/phase/agent/Architect+AUDITOR sessions/Continuous ON-OFF. | `ui/main_window.py`, `ui/simple_mode.py` |
| 8 | **Version 1.0.1** everywhere (`__init__.py`, `pyproject.toml`, `tests/test_imports.py` — the version gate was stale at `0.9.0`). | all |

## 2. Acceptance evidence (offline, zero AI calls)

| Gate | Result | Evidence |
|---|---|---|
| §8/§9 same-thread continuity | **PASS** | Scripted driver: planning thread `arch-X` is RESUMED by the Final Audit (`resumed == ["arch-X"]`, `report.session_id == "arch-X"`); bound thread wins over plan; persisted plan thread resumes after restart; `same_as=False` never touches the orchestrator thread; first Architect op creates fresh. **16 tests in `tests/test_session_011.py`.** |
| §23 engine/profile/session matrix | **PASS** | Engine dropdowns registry-driven with Codex selectable; engine change persists durable config and clears stale binding; per-engine field layout; Coder one-shot arm/consume/auto-revert; profile-scoped discovery + stale-binding auto-clear. |
| §17 debug-ui gate | **PASS** | No Advanced button in a normal launch; present with `--debug-ui`. |
| §18 operator language | **PASS** | Idle status contains no TASK/debug references. |
| §27 version sync | **PASS** | `grep version tests/test_imports.py` → `__version__ == "1.0.1"`, `pyproject.toml version = "1.0.1"`. |
| Full regression | **PASS** | **620 passed, 0 failed** (604 baseline + 16 new), `QT_QPA_PLATFORM=offscreen`, Python 3.12. |
| Packaged self-test | **PASS** | `dist/ENCOMM-PCC/ENCOMM-PCC.exe --smoke-test` → exit 0 (`version=1.0.1 … sqlite_ok=schema_v5 … clean_shutdown`). |
| Source smokes | **PASS** | `python main.py --smoke-test session_004 --fake` and `session_005` → `SMOKE OK version=1.0.1 …` exit 0 each. |

## 3. Docs

- README: v1.0.1, production Simple Mode, `--debug-ui`.
- `docs/CURRENT_STATE.md`: version rewritten, capability table updated, 620-test baseline.
- `docs/DECISIONS.md`: **D-051** (shared Architect thread + inherited config), **D-052** (Advanced behind `--debug-ui`), **D-053** (registry-driven engine selectors).
- This report.

## 4. Release

`git tag`-less single release commit pushed to `origin/main` — verified `HEAD == origin/main`. Version sync checked by `tests/test_imports.py`.