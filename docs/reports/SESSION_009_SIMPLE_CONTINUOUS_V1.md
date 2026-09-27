# SESSION 009 — SIMPLE MODE + CONTINUOUS LOOP + HERMES SESSION DISCOVERY (INTERIM)

**Project:** ENCOMM Pipeline Control Center
**Date:** 2026-09-27
**Version produced:** `0.9.0` (pre-release — 1.0.0 is reserved for a full
live-acceptance PASS per the brief §49)
**Status:** implementation + offline proof complete and pushed; live
acceptance (§35–41) and the Windows rebuild (§48) remain for the next
working session.

---

## STATUS

**INTERIM PASS (offline)** — all Session 009 code is implemented, the full
suite grew 571 → **588 passed, 0 failed**, both `--fake` smokes pass, and the
commit is pushed. Not yet claimed: live model acceptance, packaged build,
1.0.0.

## BASELINE_COMMIT

`ccec2900663e7c5543ada07c7b9067142e5783f7` (verified clean, in sync with
origin before any edit).

## FINAL_COMMIT (this interim)

**`a23986400fe7a61634bee8be8ee5bbe3b7807079`** — pushed; `HEAD == origin/main`.

## DELIVERED (§55 criteria covered offline)

| Area | Delivered |
|---|---|
| Simple Mode (§7, §8) | `ui/simple_mode.py`; default surface (stack index 0); Advanced = the untouched full window; START/PAUSE/STOP wired through the existing worker path |
| One config source (§34) | Simple controls write the durable role configs; ARCHITECT maps to ORCHESTRATOR + FINAL_AUDITOR (`same_as_orchestrator`) so ONE Codex thread serves planning + every Final Audit (§6) |
| ContinuousRunner (§25, §26) | `core/continuous_runner.py`: composes BatchRunner + `run_final_audit` + `start_next_batch`; no duplicated logic; STOP/PAUSE at safe boundaries; non-PASS final audit stops loudly (§31) |
| Zero-AI next batch (§5, §30) | proven by test: exactly 1 ORCHESTRATOR planning prompt across a two-batch continuous run; handoff consumes the pending plan |
| Hermes session discovery (§12–§15) | `drivers/hermes_discovery.py`: Priority A = `hermes -p <profile> sessions list` (structural parser; strict session-id shape); Priority B = read-only `state.db` (`mode=ro`, metadata columns only — never credentials/transcripts); profile scoping is structural |
| Binding validity engine AND profile (§19) | `bind_external_session(..., profile=)`; `binding_profile_mismatch()`; a profile change deactivates/clears the binding (UI signal), never silently reused |
| Profile dropdown (§10) | RolePanel profile field is a discovered-profile combo (editable fallback) |
| Coder policy (§17) | unchanged `always_new`; Simple UI labels it |
| Launcher repair | `resolve_executable_path` prefers the distribution shim (`hermes\bin`); the rebuilt venv entry-point shim on this host exits 0 with EMPTY stdout — root-caused 2026-09-27 |
| §42 offline proof | deterministic two-batch continuous test: 1 planning call, 1 zero-AI handoff, 2 final audits, STOP prevents the next op, PAUSE stops before the next final audit |

## HOST FINDING (important for every future session)

The venv `hermes.exe` first on PATH (rebuilt 2026-09-24) silently emits empty
stdout for all machinery commands — Sessions 002–008 style probes return RC 0
with zero bytes. The distribution shim at
`%LOCALAPPDATA%\hermes\bin\hermes.exe` works. PCC now prefers the shim; live
validation on OTHER hosts should re-check `hermes --version` output before
trusting CLI-driven discovery.

## TESTS

588 passed, 0 failed (31 + 1 files). New: `tests/test_session_009.py` (17
cases: parser contract, store discovery, shim resolution, §19 binding rules,
§42 continuous loop, §44 Simple-default). Session-006 contract snapshot
updated by design (hermes now discoverable). `session_004 --fake` and
`session_005 --fake`: SMOKE PASSED.

## REMAINING FOR 1.0.0 (next session)

1. Live acceptance (§35–41) with `encomm-accounting-intelligence` (Coder) and
   `encomm-auditor` (Auditor) on a disposable scratch repo, 2-task batch,
   controlled restart, all session-id assertions.
2. Windows rebuild + packaged `--smoke-test` + one GUI launch (§48).
3. Coder recovery one-shot override UI (§18) — the backend binding path
   already exists; only the Simple/Advanced recovery affordance remains.
4. README/User Guide Simple-Mode-first rewrite (§50) and the final report.

`FINAL_ACCEPTANCE_STATUS = INTERIM (offline PASS; live acceptance pending)`
