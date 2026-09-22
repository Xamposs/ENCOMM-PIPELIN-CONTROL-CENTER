# SESSION 006 — REAL CODEX DRIVER + GENERIC SESSION DISCOVERY / SELECTOR

**Project:** ENCOMM Pipeline Control Center
**Date:** 2026-09-23
**Version produced:** `0.6.0`
**Session scope:** the second real engine adapter — a real `CodexDriver` over
the installed Codex CLI (`codex-cli 0.154.0`), a driver-neutral
session-discovery abstraction, a read-only Codex rollout discovery module, a
durable external-session binding (engine-switch-safe, restart-safe), a real
session selector in the desktop UI (NEW SESSION = clear binding, REFRESH
SESSIONS = read-only discovery; zero model calls), and a capability-driven
`requires_profile` flag so no engine-specific branch ever enters role or
preflight logic. Proven by a 63-test offline matrix, both prior `--fake`
smokes, and EXACTLY TWO real Codex model operations against a scratch
repository (new-session marker proof; resume proof through the generic
Session 005 FINAL_AUDITOR pipeline).

---

## STATUS

**PASS** — every Session 006 completion criterion is backed by executed
evidence. The decisive one: a real Codex thread was created through the new
driver (marker `ENCOMM_PCC_CODEX_NEW_SESSION_OK`, exit 0), found by the new
read-only discovery path with a workspace match, bound through the same
generic path the UI uses, and then RESUMED by the Session 005 final-audit
pipeline — the SAME thread id was re-reported by Codex, the strict
final-audit parser returned FINAL PASS with exactly 4 next tasks, the batch
became `BATCH_COMPLETE`, the next plan persisted, and nothing auto-started.

| # | Pass criterion | Result | Evidence |
|---|---|---|---|
| 1 | Session 005 documentation drift reconciled | **PASS** | `CURRENT_STATE.md`: schema v3→**v5** (9 tables), 334→**367** tests, script list +005/+006 smokes; verified against the actual repo before editing |
| 2 | Installed Codex CLI inspected before implementation | **PASS** | `codex-cli 0.154.0` at `…\OpenAI\Codex\bin\codex.exe`; `--version`/`--help`/`exec --help`/`exec resume --help`/`resume --help` captured; `codex login status` checked (no secrets printed) |
| 3 | `CodexDriver` is real, `implemented=True` | **PASS** | evidence-gated `_LIVE_SMOKE_VERIFIED=True` after the live run; preflight dispatches Codex |
| 4 | Fresh Codex execution through `BaseDriver` | **PASS** | CALL 1: exit 0 in 7.9 s via `codex exec --json -s read-only -C <scratch> -` with the prompt on stdin |
| 5 | Real session id from authoritative CLI output | **PASS** | `01a0cb24-8500-7652-a4cf-52d167569c5e` from the `--json` `thread.started` event (never invented) |
| 6 | Real resume with the same external session id | **PASS** | CALL 2: `codex exec resume 01a0cb24-8500-7652-a4cf-52d167569c5e --json -` → exit 0, same id re-reported, 137 s |
| 7 | Discovery behind a generic driver-neutral interface | **PASS** | `SessionDiscoverer` protocol + `ExternalSessionDescriptor`; Codex implements it; no Codex paths in UI/core |
| 8 | Existing sessions selectable from the desktop UI | **PASS** | `RolePanel` selector: discovery-backed combo + Refresh; offline UI tests bind real discovered sessions |
| 9 | Select/refresh = zero model calls | **PASS** | discovery is pure filesystem reads; tests run it with a Null runner — any launch would raise |
| 10 | NEW SESSION clears binding, zero calls until execution | **PASS** | `clear_external_session` (controller) + UI test; the executor then follows `SessionPolicy.NEW` |
| 11 | Binding survives restart where valid | **PASS** | `test_binding_survives_a_real_restart` (real close/reopen); `test_final_auditor_binding_resumes_after_a_restart` |
| 12 | Engine switching cannot reuse another driver's session | **PASS** | `external_session_binding()` returns None on driver mismatch; `test_engine_switch_invalidates_the_binding` |
| 13 | ORCHESTRATOR on Codex without special pipeline logic | **PASS** | `test_orchestrator_configured_to_codex_plans_through_the_generic_path` — `plan_batch` untouched, generic path |
| 14 | FINAL_AUDITOR on Codex without special pipeline logic | **PASS** | `run_final_audit` untouched; binding seeded generically; live proof in CALL 2 |
| 15 | Session 005 Final Auditor flow passes through real Codex | **PASS** | strict `FINAL PASS` + 4-task next plan in ONE Codex response; `BATCH_COMPLETE`; plan persisted; worktree clean |
| 16 | Session 004/005 offline regression smokes remain green | **PASS** | both `--fake` smokes → SMOKE PASSED after all changes |
| 17 | Full pytest suite passes | **PASS** | `417 passed, 0 failed` (367 + 50 new) |
| 18 | No secrets/config credentials committed or printed | **PASS** | `auth.json` never read; secret scan over the full diff; no Codex config modified |
| 19 | Real Codex model operations kept to the minimum (2) | **PASS** | exactly 2 (see §RETRY below — the first CALL-2 attempt died in 0.2 s on a CLI usage error before any model work and was fixed per the evidence-retry discipline) |
| 20 | Documentation/report complete | **PASS** | this file + ADRs D-034…D-038 + CURRENT_STATE/ROADMAP/README updates |
| 21 | Working tree clean at the end | **PASS** | only the Session 006 changes staged/committed; scratch lives in the temp dir |
| 22 | Final commit pushed to `origin/main` | **PASS** | see GIT_STATUS |
| 23 | Final commit SHA reported | **PASS** | see GIT_STATUS |

---

## BASELINE_COMMIT

`2b880661bc10170622fa9765d48cd1ff84191331` — verified before any edit
(`pwd`, `git rev-parse --show-toplevel`, `git status --short`,
`git branch --show-current`, `git rev-parse HEAD`, `git rev-parse origin/main`,
`git remote -v`). Nothing was reset, stashed, cleaned or discarded.

---

## INSTALLED_CODEX_CLI

| Fact | Value |
|---|---|
| Version | `codex-cli 0.154.0` |
| Executable | `C:\Users\xampos\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe` |
| Auth | ChatGPT login (`codex login status`; no credentials read or printed) |
| Inspected (read-only) | `codex --version`, `codex --help`, `codex exec --help`, `codex exec resume --help`, `codex resume --help`, `codex features` |
| Session list API | **None** (no machine-readable listing subcommand) → read-only rollout discovery (D-036) |
| Local state | `$CODEX_HOME` (unset ⇒ `~/.codex`); `sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`; first line `session_meta` (session_id, cwd, originator, cli_version, source); `session_index.jsonl` id→thread_name. `auth.json`/`config.toml` never read. |

## VERIFIED_CLI_CONTRACT

```
# new prompt (prompt on stdin via the trailing '-'):
codex exec --json -s read-only|workspace-write -C <workspace> [-m <model>] -

# resume (NO -s/-C — the thread inherits the original session's settings):
codex exec resume <SESSION_ID> --json [-m <model>] -
```

- `--json`: one JSON object per stdout line. Live-verified top-level shapes:
  `{"type":"thread.started","thread_id":…}`, `{"type":"item.completed","item":{"type":"agent_message","text":…}}`,
  `{"type":"turn.completed","usage":{"input_tokens":…,"cached_input_tokens":…,"output_tokens":…}}`
  — pinned as a regression test (`test_parser_handles_the_live_0154_protocol_shape`).
- Least privilege: only `read-only`/`workspace-write` constructible;
  `danger-full-access` and both `--dangerously-bypass-*` flags (plus
  `--ephemeral`, `--ignore-user-config`) refused in code.
- Child env: filtered copy (D-015 discipline) dropping `HERMES_*`,
  `PYTHONPATH`/`PYTHONHOME`, plus `CODEX_HOME` and `ENCOMM_PCC_*`.

---

## ARCHITECTURE_CHANGES

| Area | Change |
|---|---|
| `drivers/codex_cli.py` (new) | Pure Codex CLI contract: argv builders, sandbox allowlist + forbidden-flag veto, filtered child env, JSONL parser (`turn.completed` + exit 0 required; missing terminal event can never be success), bounded event retention |
| `drivers/session_discovery.py` (new) | Driver-neutral contract: `ExternalSessionDescriptor`, `SessionDiscoveryResult`, `SessionDiscoverer` protocol, `normalise_workspace_key` |
| `drivers/codex_discovery.py` (new) | Read-only Codex rollout discovery: first-line `session_meta` only, titles from `session_index.jsonl` (never invented), dedupe (one session = many rollout files), newest-first, workspace-match first, bounded scan, fail-soft (missing/malformed/locked/schema-drift), zero credential reads, zero writes |
| `drivers/codex.py` (rewritten) | Real `CodexDriver`: handle-only `start_session` (no model call), evidence-gated resume, stdin transport, `discover_sessions`, `requires_profile=False`, live-verified gates |
| `drivers/base.py` | `DriverCapabilities.requires_profile` (default True) — the one generic change (D-038) |
| `drivers/__init__.py` | Codex contract/discovery + session-discovery exports |
| `domain/models.py` | `ExternalSessionBinding` + `AgentRoleConfig` binding helpers (rides in `extra`; **no schema change**) |
| `core/controller.py` | `bind_external_session()` / `clear_external_session()` — durable, engine-contact-free |
| `core/executor.py` | Capability-driven preflight (profile gate + discovery only when `requires_profile`); `_seed_binding_session()` before every session decision (plan/build/audit/fix/final-audit) so REUSE resumes the bound real id |
| `ui/panels.py` | `RolePanel`: discovery-backed session combo (full id as data, bounded label), `[bound]` row, `session_selected`/`sessions_refresh_requested` signals, capability-aware enablement, note label |
| `ui/main_window.py` | `_on_sessions_refresh` (driver discovery, never a model call) + `_on_session_selected` (bind via controller / NEW clears) |
| `scripts/session_006_codex_smoke.py` (new) | The two-call live validation (+ raw per-call evidence capture) |
| `scripts/session_006_call2_retry.py` (new) | The §20 evidence-driven retry of CALL 2 only |
| `tests/test_codex_driver.py` (new) | 21 cases: argv, sandbox/flag vetoes, env filter, parser matrix (incl. live shapes), lifecycle, resume gate, Null-runner safety |
| `tests/test_codex_discovery.py` (new) | 13 cases: fixtures, ordering, titles, dedupe, workspace match, missing/malformed/locked/drift fail-soft, credential never read |
| `tests/test_session_binding.py` (new) | 16 cases: binding round-trip + restart, engine-switch invalidation, NEW-clears, generic-path Codex plan/final-audit (resume argv asserted), selector UI matrix |
| updated tests | Placeholder-refusal tests moved to generic_cli-only; capability gates now assert evidence-mirroring; NEW SESSION UI test upgraded to real semantics; version 0.6.0 |

---

## LIVE_VALIDATION (REAL_MODEL_OPERATIONS = 2)

`python scripts/session_006_codex_smoke.py` — scratch git repo under the
system temp dir, pre-seeded `.gitignore` (keeps `__pycache__` out of the
porcelain fingerprint), 4 deterministic features + tests, batch seeded as
completed (all APPROVED) from durable facts.

| Step | Action | Result |
|---|---|---|
| CALL 1 | ONE new Codex session through `CodexDriver` (orchestrator request shape, read-only sandbox, `-C scratch`) | **exit 0, 7.9 s**; text = exactly `ENCOMM_PCC_CODEX_NEW_SESSION_OK`; thread **`01a0cb24-8500-7652-a4cf-52d167569c5e`** |
| discovery | `driver.discover_sessions(workspace_path=scratch)` immediately after | **found=True, workspace_match=True** (read-only rollout scan) |
| binding | `controller.bind_external_session(FINAL_AUDITOR, "codex", 01a0cb24…)` | durable; zero engine contact |
| CALL 2 | ONE resume through the generic `run_final_audit` pipeline (`codex exec resume 01a0cb24… --json -`) | **FINAL PASS** (strict envelope; `tests_verified=true`, `diff_verified=true`); exactly 4 next tasks in the SAME response; 32 command executions + 2 MCP calls by the auditor (it ran the tests itself); 137 s; **same thread id re-reported** |
| post | state machine + persistence + guard | phase `BATCH_COMPLETE`; batch `COMPLETE`; `final_verdict=PASS` reloaded from SQLite; `nextplan_batch_3d0bf698` persisted (4 tasks), nothing auto-started; fingerprints pre/post audit equal |

### RETRY_DISCIPLINE (§20) — why a second script ran

The first CALL-2 attempt failed in **0.2 s with exit 2 before any model
work**: the argv passed `-s`/`-C` to `codex exec resume`, which the installed
CLI rejects (`error: unexpected argument '-s' found` — preserved raw stderr).
Per the cost guard, the successful CALL 1 was never repeated; the preserved
scratch dir, session id and raw evidence were reused, the contract module was
fixed from the evidence (D-035), and ONLY the failed operation was re-run
(one real model operation). Total real Codex model operations: **2**.

---

## SECURITY_CHECK

| Check | Result |
|---|---|
| Secret scan over the full diff | **NO_SECRETS_FOUND** |
| `auth.json` / Codex config reads | **never** (discovery reads only `sessions/` + `session_index.jsonl`; tested) |
| Codex config modified | **none** — read-only inspection only |
| Bypass flags / dangerous sandbox | refused in code; tests pin the refusal |
| Child environment | filtered (D-015 discipline + `CODEX_HOME`/`ENCOMM_PCC_*` drops); never logged |
| Prompt transport | stdin only — never in argv |
| Session ids | verbatim from CLI output; never fabricated |
| Discovery writes | none (read-only scans; unit-tested with a real file lock) |
| Production repo touched by the live smoke | **no** — scratch git repo only; Control Center repo modified only by this session's own work |

## KNOWN_LIMITATIONS

1. `GenericCliDriver` remains the standing placeholder (`implemented=False`).
2. Codex resume inherits the original session's sandbox/cwd — by CLI design;
   the driver surfaces no override (there is none to pass safely).
3. `supports_cancellation=False` for Codex (boundary-only stop), same as Hermes.
4. Codex `supports_model_selection=True` (verified `-m`), but the live proof
   used the account's default model; no model pinning was exercised live.
5. Discovery is filesystem-scan based (the CLI exposes no listing API);
   a future CLI listing API should replace it behind the same protocol.
6. The resume path cannot re-pin a workspace: a bound session recorded under
   a different workspace will resume with ITS original cwd (Codex semantics);
   the selector shows each session's workspace so the operator can choose
   correctly.

## NEXT_RECOMMENDED_SESSION

**Session 007** — see `docs/CURRENT_STATE.md` §7: `GenericCliDriver` argv or
`ClaudeCodeDriver` (reusing the Session 006 discovery/binding), or Phase 6
hardening. Out of scope as before: auto-chaining, cloud, parallel batches.
