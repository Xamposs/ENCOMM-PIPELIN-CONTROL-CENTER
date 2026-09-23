# SESSION 007 — REAL GENERIC CLI DRIVER + OPERATIONAL HARDENING

**Project:** ENCOMM Pipeline Control Center
**Date:** 2026-09-23
**Version produced:** `0.7.0`
**Session scope:** Phase 5 completion (the `GenericCliDriver` placeholder became
a real, stateless, security-hardened engine adapter driven by a validated
structured argv configuration — never a shell command) plus the first tranche of
Phase 6 operational hardening: versioned secret-free configuration
export/import, a read-only batch/run history surface, bounded `app_events`
retention, a full restart/recovery test matrix, a structured Generic CLI
settings dialog, and the reconciliation of the documentation drift left by
Session 006.

---

## STATUS

**PASS** — every completion criterion below is backed by executed evidence from
this session. The decisive facts: the full test suite grew 417 → **542 tests,
0 failed**; both prior `--fake` smokes still pass; the Generic CLI driver was
proven through the REAL `SubprocessRunner` with deterministic `python -c`
child processes (real exit codes, real stdout, real tree-kill timeouts) at a
cost of **ZERO AI model operations**; and configuration export/import is
proven round-trip + restart-safe with secrets redacted.

| # | Pass criterion | Result | Evidence |
|---|---|---|---|
| 1 | Documentation drift reconciled | **PASS** | CURRENT_STATE/ARCHITECTURE/ROADMAP/README updated to the real 0.7.0 state; drift list in §DRIFT below |
| 2 | `GenericCliDriver` is real, `implemented=True`, honestly stateless | **PASS** | `drivers/generic_cli.py`; capabilities pinned by `test_generic_cli_stateless_contract` |
| 3 | Configuration is structured argv, never a shell command | **PASS** | `drivers/generic_cli_config.py`; string-args rejected; metacharacters stay literal tokens; unknown keys/placeholders fail closed |
| 4 | Prompt transport stdin + temp file | **PASS** | stdin echo test; temp-file test proves content + cleanup |
| 5 | Result modes stdout/json/jsonl, bounded, fail-closed | **PASS** | 9-case extraction matrix; malformed/missing/oversized all fail |
| 6 | Real process contract: exit code, timeout tree-kill, empty-stdout failure | **PASS** | 13-case `SubprocessRunner` matrix with `python -c` children |
| 7 | D-015 filtered child env + validated overrides, env never logged | **PASS** | filter test + metadata leak test |
| 8 | SessionManager decides NONE for the stateless driver | **PASS** | `decide_session_action` test |
| 9 | Structured settings dialog; invalid input cannot be saved | **PASS** | 17-case UI matrix (offscreen) |
| 10 | Config durability (extra, no schema change), engine-switch-safe | **PASS** | save/reload through SQLite; switch-preserve test |
| 11 | Unconfigured Generic CLI role blocks before any process | **PASS** | `test_unconfigured_generic_cli_blocks` (executor path) |
| 12 | Config export: versioned, deterministic, secret-free | **PASS** | redaction test (value absent, key preserved); deterministic bytes |
| 13 | Config import: validate-before-apply, strict version gate | **PASS** | 21-case matrix incl. malformed JSON, wrong format, future/past version, oversized, invalid policy/GenericCLI, binding refused |
| 14 | Invalid import changes nothing; valid import survives restart | **PASS** | before/after state comparison; `restore_state` reload |
| 15 | Import makes zero model calls | **PASS** | structural BoomRunner spy test |
| 16 | `app_events` retention: bounded, hysteresis, events-only | **PASS** | 9-case matrix incl. reopen + tasks-survive-prune |
| 17 | History read model + panel: rows, detail, bounded text | **PASS** | 12-case matrix |
| 18 | Recovery matrix: every phase restarts safe, nothing auto-runs | **PASS** | 13-case matrix (real close/reopen per phase) |
| 19 | Full pytest suite passes | **PASS** | **542 passed, 0 failed** (was 417) |
| 20 | Session 004/005 offline regression smokes remain green | **PASS** | both `--fake` → SMOKE PASSED |
| 21 | Real AI model operations kept to the minimum | **PASS** | **0** — every proof is offline (fake children, in-process fakes) |
| 22 | Documentation/report complete | **PASS** | this file + ADRs D-039…D-043 + README/CURRENT_STATE/ARCHITECTURE/ROADMAP updates |
| 23 | Final commit pushed to `origin/main` | **PASS** | see GIT_STATUS |

---

## BASELINE_COMMIT

`de32aa47977e90b8f96b495272e58c1916f87f38` — verified before any edit
(`pwd`, `git rev-parse --show-toplevel`, `git status --short`,
`git branch --show-current`, `git rev-parse HEAD`, `git rev-parse origin/main`,
`git remote -v`). Clean tree, branch `main`, in sync with origin. Nothing was
reset, stashed, cleaned or discarded.

---

## DRIFT_RECONCILIATION (criterion 1)

Found and fixed in this session (all left by Session 006's doc pass):

| Doc | Stale claim | Now |
|---|---|---|
| `ARCHITECTURE.md` header | "Version: 0.5 … accurate as of Session 005" | 0.7 header + Session 007 section |
| `ARCHITECTURE.md` §3 | repo tree says `codex.py (placeholder)`, schema v1/7 tables, 144 tests | current tree/schema v5/9 tables/test count |
| `ARCHITECTURE.md` §1/§12 | "No real Codex driver … the Codex adapter is Session 006" | real drivers list (Hermes, Codex, Generic CLI) |
| `ARCHITECTURE.md` §7 | "schema v3, seven tables" | schema v5, nine tables |
| `CURRENT_STATE.md` §1 | version 0.6.0 | 0.7.0 |
| `CURRENT_STATE.md` §3 | tree marks `codex.py` placeholder; report list ends at 005 | real tree + SESSION_006/007 listed |
| `CURRENT_STATE.md` §4 | decisions summary stops at D-033 | D-001…D-043 |
| `CURRENT_STATE.md` §5.2/§10 | "CodexDriver/GenericCliDriver remain placeholders" | both real; remaining planned drivers named honestly |
| `README.md` | "Codex and Generic-CLI adapters remain deliberate placeholders"; 417 tests | real-adapter wording; 542 tests |
| `ROADMAP.md` | Phase 5 item 6 open; Phase 6 items open | marked DONE as delivered |

---

## ARCHITECTURE_CHANGES

| Area | Change |
|---|---|
| `drivers/generic_cli_config.py` (new) | The whole Generic CLI configuration contract: `GenericCliConfig` (validated, bounded, frozen), `build_argv` (exact-token placeholders), `child_environment` (D-015 filter + ≤16 validated overrides), `extract_result_text` (stdout_text/json/jsonl, `json.loads` only, 2 MB cap), `GenericCliConfigError(DriverError)` |
| `drivers/generic_cli.py` (rewritten) | Real `GenericCliDriver`: stateless, `implemented=True`, `requires_profile=False`; stdin/temp-file transports (private UTF-8 temp prompt, deleted after); argv-build errors → failure result, never a crash; explicit-cwd requirement (mirrors Codex); bounded stderr diagnostics; success = exit 0 + non-empty extracted answer |
| `drivers/__init__.py` | Exports: `GenericCliConfig`, `GenericCliConfigError`, `GENERIC_CLI_CONFIG_EXTRA_KEY`, `PROMPT_TRANSPORTS`, `RESULT_MODES` |
| `core/config_exchange.py` (new) | Versioned export/import (D-041): `build_export`/`write_export`/`read_export`/`validate_export`/`apply_import`, `ConfigExchangeError` |
| `core/history.py` (new) | Read model (D-043): `batch_history_rows` / `batch_history_detail`, `HISTORY_TEXT_CAP` |
| `core/config.py` | `MAX_APP_EVENTS = 10_000`, `EVENT_PRUNE_INTERVAL = 500` |
| `core/events.py` | `_maybe_prune_database()` opportunistic hysteresis retention; `retention_enabled` flag |
| `persistence/database.py` | `prune_app_events(keep)` — newest-N delete, events table only |
| `core/controller.py` | `set_generic_cli_config(role, dict|None)` (validate-before-persist) + `generic_cli_config(role)` |
| `ui/generic_cli_dialog.py` (new) | Structured config dialog (D-040): shlex token list editor, transport/result combos, bounded timeout spin, KEY=VALUE env row; `accept()` refuses invalid configurations |
| `ui/history_panel.py` (new) | HISTORY section: batch list + bounded detail view, read-only |
| `ui/panels.py` | `RolePanel`: capability-driven "Configure Generic CLI…" row + status note + generic-CLI-aware capability hint; `TaskPanel`: generic-CLI-aware driver availability readout |
| `ui/main_window.py` | History panel in the splitter; refresh on control/dispatch completion |
| `docs/DECISIONS.md` | ADRs D-039…D-043 appended (no old entry rewritten) |

## TESTS (417 → 542, all green)

| File | Cases | Covers |
|---|---|---|
| `tests/test_generic_cli_driver.py` (new) | 55 | config validation (18), result modes (9), REAL-process matrix via `python -c` (13), security (5), integration (6+) |
| `tests/test_generic_cli_ui.py` (new) | 17 | visibility, dialog validation, engine-switch preservation, SQLite round-trip, refusal paths |
| `tests/test_config_exchange.py` (new) | 21 | export shape/redaction/determinism, import strictness, restart-after-import, zero-model-call spy |
| `tests/test_event_retention.py` (new) | 9 | bound, hysteresis, reopen, disable flag, events-only |
| `tests/test_history.py` (new) | 12 | read model + panel, ordering, verdicts, pending/consumed plans, bounded text |
| `tests/test_recovery_matrix.py` (new) | 13 | per-phase restart matrix + structural no-auto-run guarantees |
| `tests/test_drivers.py` | 2 updated | placeholder→real contract (by design) |
| `tests/test_controller.py`, `tests/test_executor.py` | 2 updated | generic_cli `implemented=True`; unconfigured-role refusal message |
| `tests/test_imports.py` | version assertion | 0.7.0 |

## LIVE_VALIDATION

**REAL_MODEL_OPERATIONS = 0.** Per the cost guard, every post-processing and
process path was proven offline first: deterministic `python -c` children
through the real `SubprocessRunner` (real exit codes, real timeouts with real
tree kills, real stdin/temp-file transport), in-process fake drivers for the
executor path, and real SQLite round-trips. No real engine was contacted; no
scratch repos were created; no network calls were made by the application.

## SECURITY_CHECK

| Check | Result |
|---|---|
| Secret scan over the full diff | **NO_SECRETS_FOUND** (see commit gate below) |
| Shell execution surface | none — argv lists + `shell=False` only; no command strings stored or accepted anywhere |
| Env override values | redacted on export, dropped on import, never logged in result metadata (tested) |
| External session bindings | excluded from export; refused on import (tested) |
| Retention scope | `app_events` only (tested: batches/tasks survive) |
| Import validation | whole-document, before any mutation (tested: invalid ⇒ nothing changes) |
| Prompt temp files | private user temp dir, deleted after the run (tested) |

## KNOWN_LIMITATIONS

1. The Generic CLI driver has **no live external-CLI proof yet** (by design:
   zero-AI session). The argv/transport/result contract is fully pinned
   offline; a live run against a real third-party CLI is the natural first
   item for a follow-up session and costs exactly one child process.
2. Generic CLI result extraction supports one configured field (dotted path),
   not arbitrary query languages — deliberate (fail-closed simplicity).
3. Config import does not merge per-key env overrides with existing stored
   values beyond dropping redactions; a re-export after import shows empty
   env values until the operator re-enters them (documented in D-041).
4. `HistoryPanel` renders the newest 50 batches per workspace.
5. `app_events` retention is count-based; time-based retention was
   deliberately not added (one deterministic policy, D-042).
6. The Claude/OpenCode/Ollama/Kimi adapters still have no code — and no
   longer need a placeholder excuse: the Generic CLI covers simple CLIs
   today, and dedicated adapters arrive with their own discovery/binding
   work.

## GIT_STATUS

- Baseline: `de32aa47977e90b8f96b495272e58c1916f87f38` (verified before any edit)
- Final commit: **`1dade2cf1119fd0dbddfd90c5fe15bc82d20c22b`** —
  `feat: real Generic CLI driver + operational hardening (Session 007, v0.7.0)`
- Pushed to `origin/main`; verified `git rev-parse HEAD` ==
  `git rev-parse origin/main` (== `1dade2c…`) immediately after the push.
- 33 files changed, 4479 insertions(+), 97 deletions(-); 12 new files
  (1 report, 5 source modules, 6 test files). `git diff --check` clean;
  secret scan over the added lines: NO_SECRETS_FOUND; no `.db`/logs/scratch
  staged.

## NEXT_RECOMMENDED_SESSION

**Session 008** — either (a) a live Generic CLI proof against one real
third-party CLI (one child process, evidence-captured), or (b) the remaining
Phase 6 items: packaging, run-history browsing depth (event drill-down per
batch), or a `ClaudeCodeDriver` reusing the Session 006 discovery/binding
infrastructure. Out of scope as before: auto-chaining, cloud, parallel
batches.
