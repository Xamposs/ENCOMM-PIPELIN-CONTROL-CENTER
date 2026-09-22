# SESSION 002 — REAL HERMES EXECUTOR PATH

**Project:** ENCOMM Pipeline Control Center
**Date:** 2026-09-22
**Version produced:** `0.2.0`
**Session scope:** executor skeleton + the first real driver (`HermesDriver`),
ending at `AUDITING_TASK` for exactly one task.

---

## STATUS

**PASS**

Every Session 002 pass criterion is backed by executed evidence rather than
intent. The decisive one — a real Hermes session answering a controlled prompt —
ran end to end through `Control Center → Executor → HermesDriver → installed
Hermes CLI`, and its result, exit code and session id were read back from SQLite.

| # | Pass criterion | Result | Evidence |
|---|---|---|---|
| 1 | Session 001 architecture intact | **PASS** | Layer rule, roles/engines separation and the transition graph are unchanged; 221 tests pass (was 144) |
| 2 | CLI behaviour discovered from installed evidence | **PASS** | `hermes --help`, `hermes chat --help`, and the shipped source of `hermes_cli/oneshot.py`, `hermes_cli/stream_json.py`, `cli.py`, `hermes_cli/main.py`, `hermes_cli/profiles.py` |
| 3 | `HermesDriver` performs real work | **PASS** | A real child process answered in 12.7 s with exit code 0 (see REAL_SMOKE_TEST) |
| 4 | Placeholder success removed only where real integration exists | **PASS** | `hermes` is now `implemented=True`; `codex` and `generic_cli` still refuse every real operation (`DriverNotImplementedError`) |
| 5 | One real NEW Hermes session started | **PASS** | Session `20260922_172437_722edb` created by a `--oneshot` run |
| 6 | One controlled smoke prompt returned the expected response | **PASS** | Output was exactly `ENCOMM_PCC_HERMES_SMOKE_OK` |
| 7 | Real process exit code captured | **PASS** | `exit_code = 0`, plus the CLI's own terminal `result.exit_code = 0` |
| 8 | Session ID behaviour represented honestly | **PASS** | Real id recorded as `external=1`; when the CLI exposes none the driver keeps `None` (unit-tested) |
| 9 | One-task executor path works | **PASS** | `IDLE → PLANNING_BATCH → RUNNING_TASK → AUDITING_TASK`, task state `AUDITING` |
| 10 | State/result persisted | **PASS** | Task row, session row and the result event payload read back from SQLite |
| 11 | UI does not block during execution | **PASS** | Dispatch runs on a `QThread`; offscreen test proves the Qt event loop keeps ticking while the worker sleeps |
| 12 | Child-process failure propagates | **PASS** | Exit code 3 and exit code 2 both produce `FAILED` task + `FAILED` pipeline, persisted with the error |
| 13 | Unit tests pass | **PASS** | `221 passed in 4.86s`, 0 failed |
| 14 | No secrets committed/persisted | **PASS** | Pattern scan over the full raw diff → `NO_SECRETS_FOUND`; no environment dump is stored anywhere |
| 15 | `docs/CURRENT_STATE.md` reflects reality | **PASS** | Rewritten; §5 lists what is still absent |
| 16 | SESSION_002 report exists | **PASS** | This file |
| 17 | Committed and pushed to `main` | **PASS** | See GIT_STATUS |

---

## BASELINE_COMMIT

`0a47efba4036f44a97a83daad320495a0818295d` — `feat: ENCOMM Pipeline Control Center v0.1 foundation`

Verified before any edit: `pwd`, `git rev-parse --show-toplevel`, `git status
--short`, `git log -5 --oneline`, `git remote -v`. The only pre-existing local
change was the documentation-only SHA backfill in
`docs/reports/SESSION_001_FOUNDATION.md`, which was preserved and is included in
this session's commit. Nothing was reset, stashed, cleaned or discarded.

---

## OBJECTIVE

Implement the first real execution path — Control Center → executor skeleton →
`HermesDriver` → a real, fresh Hermes session → one controlled prompt → a real
completion with a real exit code → persisted result, state and event evidence —
and stop at `AUDITING_TASK`, without building the auditor, the fix loop, batches
of 4–5 tasks, the orchestrator, or any other engine.

---

## LEANCTX_USAGE

**Skill loaded:** `encomm-leanctx` before any repository exploration.

| Step | Command | Result |
|---|---|---|
| 0A Repository safety | `pwd` + `git rev-parse --show-toplevel` | `C:/Users/xampos/Desktop/ENCOMM PIPELINE CONTROL CENTER` (intended target) |
| 0B LeanCTX availability | skill resolver `scripts/lctx.sh` | `C:\Users\xampos\AppData\Local\leanctx\node_modules\lean-ctx-bin\bin\lean-ctx.exe` |
| 0B Verification | `lean-ctx --version` | `lean-ctx 3.10.1 (official, https://github.com/yvgude/lean-ctx)` |
| Orientation | `lean-ctx overview "Hermes driver executor implementation"` | 33 files, task-filtered hotspots (`test_drivers.py`, `registry.py`, `base.py`, `hermes.py`) |

The hyphen pitfall was avoided by never probing `leanctx`, and LeanCTX was
never searched for as a Hermes tool.

**Where LeanCTX actually helped this session.** The repository is small (≈6.4 k
lines), so the compressed-discovery mechanisms had little to compress. The
value came from (a) the preflight discipline, (b) `overview` correctly pointing
at the driver layer as the area to change first, and (c) escalation discipline:
every file that was written or edited was held in full, and the three test
failures encountered were diagnosed from complete tracebacks (see
DEFECTS_FOUND), not from summary lines.

**Accuracy-guard compliance.** No conclusion rests on compressed context. The
Hermes CLI contract is documented only from raw `--help` output and raw source
reads of the installed build, and the smoke verdict is read from the real
process's captured stdout/stderr/exit code and from SQLite rows re-read after
the run.

---

## HERMES_VERSION

```
Hermes Agent v0.21.3 (2026.9.14) · upstream 00570550
Install directory: C:\Users\xampos\AppData\Local\hermes\hermes-agent
Install method:    git
Python:            3.11.15
```

Executable resolved by the driver: `C:\Users\xampos\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes.EXE`
(`shutil.which("hermes")` → `hermes.EXE`; the extension-less `Scripts/hermes`
shell wrapper is not used, because Windows `CreateProcess` cannot execute it).

---

## HERMES_CLI_DISCOVERY

Everything below was **read from the installed build**, never assumed.

| Question | Verified answer | Source |
|---|---|---|
| Executable | `…\venv\Scripts\hermes.EXE`, resolved via `shutil.which("hermes")` | runtime probe |
| Version | v0.21.3 (2026.9.14), upstream `00570550`, git install | `hermes --version` |
| Profile selection | `hermes -p <name> <cmd>` (also `--profile`), applied **before** any hermes module imports; an unknown name prints `Error:` and exits 1 **without creating anything** | `--help`; `hermes_cli/main.py::_apply_profile_override`; `hermes_cli/profiles.py::resolve_profile_env` (raises `_missing_profile_error`) |
| Non-interactive prompt | `chat --query-file <path\|->` reads the prompt verbatim from a file or stdin ("nothing is shell-interpreted"); `--oneshot` answers and exits; implied on non-TTY stdio and by `-Q` | `hermes chat --help` |
| Alternate one-shot | `-z/--oneshot PROMPT` prints **only** the final text to stdout, no session-id line | `--help`; `hermes_cli/oneshot.py::run_oneshot` |
| Working directory | `--in DIR` changes into `DIR` before starting/resuming; the child process also runs with `cwd` set by us | `--help` |
| Provider per invocation | `--provider PROVIDER` (applies to oneshot/TUI) — **but `--provider` without `--model` is rejected (exit 2)** | `--help`; `oneshot.py::run_oneshot` |
| Model per invocation | `-m/--model MODEL` (applies to oneshot/TUI); model-only auto-detects the provider | `--help`; `oneshot.py::_resolve_model_and_provider` |
| Structured output | `--format stream-json` emits one JSON object per stdout line: `system/init` (model + session_id), `text` deltas, `tool_use`/`tool_result`, terminal `result` (session_id, exit_code, text, tokens, duration_ms); diagnostics stay on stderr | `hermes chat --help`; `hermes_cli/stream_json.py` |
| Session ids | Real ids, reported on the `init` and `result` records; `-Q`/stream-json also print `session_id: <id>` to **stderr** | `cli.py::_run_quiet_single_query`; `stream_json.py::emit_result` |
| Resume | `--resume <id\|title\|latest>` loads and continues the transcript; unknown ids raise rather than silently starting fresh | `--help`; `oneshot.py::_load_resume_target` |
| Exit codes | `0` completed · `1` failed / no final response · `2` usage error or partial run · `130` interrupted | `oneshot.py::_oneshot_exit_code`; `cli.py::_single_query_exit_code` |
| Cancellation | No documented mid-prompt cancel for a one-shot run | `--help` (absent) |
| Session source tag | `chat --source tool` — documented for third-party integrations that should not appear in the user's session list | `hermes chat --help` |
| Profile listing | `hermes profile list` renders a plain text table (no `--json`); exit 0 | runtime probe; `hermes profile --help` |

**Invocation implemented by the driver** (the exact argv is unit-tested and was
captured from the real run):

```
hermes -p <profile> chat --query-file <tmpfile> --oneshot --quiet
       --format stream-json --source tool [--in <workspace>]
       [--resume <session_id>] [-m <model>] [--provider <provider>]
```

Hermes configuration and profiles were **not** modified: the only profile
operations performed were `hermes profile list` (read-only) and `-p <existing
profile>` selections. No API key or secret was read, printed or stored.

---

## HERMES_DRIVER_IMPLEMENTATION

`src/encomm_pcc/drivers/hermes.py` is now a real adapter:

* **One process per prompt.** `start_session()` validates and returns a handle;
  `send_prompt()` records the prompt; `wait_for_completion()` writes the prompt to
  a private temp file, builds the argv, and runs it through the injected
  `ProcessRunner` (the executor hands it a recording wrapper around
  `SubprocessRunner`).
* **No shell.** `argv` list only, `shell=False`, `cwd` pinned to the supervised
  workspace, stdout/stderr/exit code always captured.
* **Sanitised child environment.** The supervisor's own `HERMES_*` state and
  `PYTHONPATH`/`PYTHONHOME` are stripped before launch (unit-tested). This is not
  cosmetic: the parent environment in a Hermes-hosted session carries
  `HERMES_SESSION_ID`, spawn markers and even a provider credential variable, and
  `HERMES_KANBAN_TASK` would silently rewrite the CLI's exit codes.
* **Honest results.** `PromptResult.ok` requires exit code 0 **and** a terminal
  `result` record **and** the CLI's own reported exit code 0. A timeout marks
  `timed_out` with the partial output preserved. `simulated` is never set for a
  real run. A missing `result` record can never become success.
* **Capability flags are evidence-gated.** `implemented` and `supports_resume`
  mirror `_LIVE_SMOKE_VERIFIED` / `_LIVE_RESUME_VERIFIED`; the executor preflights
  on `implemented`, so an unverified adapter cannot be dispatched (unit-tested
  both ways).
* **Streaming and cancellation are not advertised.** The CLI streams deltas, but
  this adapter surfaces only the finished answer, so `supports_streaming=False`;
  the process layer is blocking, so `supports_cancellation=False` and `cancel()`
  returns `False`.

`src/encomm_pcc/drivers/hermes_cli.py` holds the pure contract (argv builder,
JSONL parser, exit-code constants, environment filter) so all of it is testable
without a network or an engine.

---

## SESSION_ID_BEHAVIOUR

* A session id exists only **after** a prompt has run. `start_session()` returns
  `session_id=None`, `external=False` — and `get_session_id()` is therefore
  `None` before any prompt. Nothing is synthesised.
* The id is taken **verbatim** from the stream's `init`/`result` records, with
  the CLI's own `session_id:` stderr line as a documented secondary source.
* When neither is present, the id stays `None` — asserted by
  `test_no_session_id_anywhere_stays_none`.
* A real id is mirrored into SQLite with `external=1` and a matching
  `external_session_id`; a local handle is never marked external.
* `PromptResult.session_id` carries the engine's id, not ours.

---

## PROFILE_DISCOVERY

Implemented in `src/encomm_pcc/core/hermes_profiles.py`, read-only, two methods
in order:

1. **`hermes profile list`** (authoritative; also reports `default`). The table is
   parsed only *after* its box-drawing separator, so prose that merely mentions a
   profile cannot be mistaken for a row, and every candidate must match the
   profile-name grammar.
2. **Directory scan** of documented roots (`<HERMES_HOME>/…`, `%LOCALAPPDATA%\hermes`,
   `~/.hermes`), applying Hermes' own rule that a directory is a profile only when
   it carries an identity marker (`config.yaml`, `.env`, `SOUL.md`, `profile.yaml`,
   `auth.json`, `state.db`). Hidden/staging directories are never profiles.

Live result: **`ok=True`, method `cli`, 10 profiles discovered**, including the
target `encomm-pipeline-control-center`. It is used as a **guard**: when discovery
succeeds and the configured profile is not in the list, the executor blocks the
dispatch with the available names in the message instead of letting a typo reach
the CLI. When discovery fails it is advisory only — the CLI itself is the
authority and fails closed (exit 1, creating nothing). No secret is read: only
names, from a table and a directory listing.

The UI populates the profile fields from the discovered names (completion + a
discovered-profiles tooltip) and shows the method and count in the TASK panel.

---

## PROVIDER_MODEL_BEHAVIOUR

Real per-invocation wiring, because the installed CLI supports it:

* `-m <model>` and `--provider <provider>` are passed **only when configured**;
  model-only is passed alone (the CLI then auto-detects the provider).
* `--provider` without a model is refused **by the driver** with a clear message,
  matching the CLI's own rule (it exits 2 on that combination).
* **Live proof (STEP 6 of the smoke):** a run configured with
  `model=deepseek/deepseek-v4.1-flash`, `provider=openrouter` exited 0 and the
  CLI's own stream reported `"model": "deepseek/deepseek-v4.1-flash"`.
* The profile's own default is used when the fields are empty — the STEP 2 run
  reported `"model": "glm-5.3-flash"` from the selected profile, which is why the
  UI wording is "profile default" rather than a hardcoded model.

No token, key or credential is displayed or persisted; the only provider data
stored is the non-secret provider/model name and token **counts**.

---

## EXECUTOR_IMPLEMENTATION

`src/encomm_pcc/core/executor.py` — deterministic, Qt-free, thread-agnostic:

* `TaskSpec` (title + prompt) → `materialise_task()` persists a real
  `TaskStateRecord` into the current batch through the existing `tasks` table.
* `dispatch_single_task()` sequence: phase gate (read-only) → preflight →
  create/reuse the batch → driver + session → materialise + persist `RUNNING_TASK`
  → session policy via `SessionManager.decide()` → prompt → persist the result →
  `AUDITING_TASK`.
* **Preflight blocks before any state change or process**: missing workspace path,
  non-existent path, no engine, unregistered engine, `implemented=False`,
  missing profile, or a profile that discovery positively contradicts. A blocked
  dispatch leaves the pipeline exactly where it was (unit-tested).
* **`executor_started` is derived from an actual launch.** The executor wraps the
  injected runner in a recorder and hands *that* to the driver; the flag is
  `bool(recorder.launched)`. A blocked preflight and a stop-before-dispatch are
  both `executor_started=False`.
* **Failure is never silent**: a failed child marks the task `FAILED`, writes
  `last_error`, sets the batch to `FAILED`, moves the pipeline to `FAILED`
  (a legal edge) and logs an ERROR event with the child's own error text. An
  unexpected driver exception is caught, recorded the same way, and reported —
  it does not escape and does not produce a green task.
* **Pause/stop are boundary-safe.** `request_pause()`/`request_stop()` set flags;
  a stop requested before dispatch produces `STOPPED` with no process. A stop
  requested *during* a prompt is reported as `stop_requested=True` on the report
  (the run finishes, its real result is recorded) — mid-prompt cancellation is
  deliberately not attempted and not advertised.
* The executor mutates state **only** through `controller.transition()` (which
  validates against the declarative graph and persists) and `controller.persist()`
  — the AI never chooses a transition.

---

## STATE_TRANSITIONS

Session 002 implements exactly one path and stops:

```
IDLE ──▶ PLANNING_BATCH ──▶ RUNNING_TASK ──▶ AUDITING_TASK   (stop)
                    │                  │
                    └────── ▶ BLOCKED  └────── ▶ FAILED
```

| Situation | Phase result | Task state |
|---|---|---|
| Successful dispatch | `AUDITING_TASK` | `AUDITING` |
| Child process failed | `FAILED` | `FAILED` (+ `last_error`) |
| Preflight refused | unchanged (`IDLE`/`PLANNING_BATCH`) | none materialised |
| Illegal phase at dispatch | unchanged | unchanged |
| Stop before dispatch | unchanged | none materialised |

`AUDITING_TASK` here means only "the Builder completed and the task is ready for
the future Task Auditor". There is no auditor, no fix loop, no multi-task batch,
and no transition out of `AUDITING_TASK` in this session.

---

## REAL_SMOKE_TEST

`scripts/session_002_smoke.py` — deliberately outside the unit suite (the suite
never touches a network or an engine). It builds a scratch workspace **and** a
scratch database under the system temp directory, so neither the repository nor
the real `%LOCALAPPDATA%` state is written.

Command actually run:

```bash
python scripts/session_002_smoke.py \
  --profile encomm-pipeline-control-center \
  --attempts 3 --keep-scratch \
  --probe-model "deepseek/deepseek-v4.1-flash" --probe-provider openrouter
```

Prompt sent (verbatim):

```
Return exactly:

ENCOMM_PCC_HERMES_SMOKE_OK

Do not use tools. Do not edit files. Do not run commands. Do not perform
repository work. Do not add explanation.
```

Real executions performed: **3** (one primary prompt, one resume proof, one
model-override probe). No retries were needed; the primary run succeeded on
attempt 1 of a permitted 3.

Result:

```
outcome                 : COMPLETED
executor_started        : True
phase after run         : AUDITING_TASK
process exit code       : 0
duration (s)            : 12.7
simulated               : False
error                   : None
answer contains token   : True
real session id         : 20260922_172437_722edb
```

The child's stream reported `tool_use_count = 0` — the "do not use tools"
instruction held, so the run performed no repository work.

---

## REAL_SMOKE_SESSION_ID

`20260922_172437_722edb`

(Reported by the Hermes CLI on the stream's `init` and `result` records and on
its own `session_id:` stderr line; not synthesised.)

---

## REAL_SMOKE_EXIT_CODE

`0` — the real process exit code, with `result.exit_code = 0` from the CLI's own
terminal record. The mapping to `ok=True` requires both.

---

## REAL_SMOKE_OUTPUT

```
ENCOMM_PCC_HERMES_SMOKE_OK
```

Exactly the expected token, with no additional explanation (26 characters).

---

## FILES_CREATED

| File | Lines | Purpose |
|---|---|---|
| `src/encomm_pcc/drivers/hermes_cli.py` | 266 | Verified CLI contract: argv builder, stream-json parser, exit codes, child-environment filter |
| `src/encomm_pcc/core/hermes_profiles.py` | 251 | Read-only profile discovery (CLI listing + identity-marker scan) |
| `src/encomm_pcc/core/executor.py` | 660 | Deterministic executor: materialisation, preflight, dispatch, failure propagation |
| `src/encomm_pcc/ui/worker.py` | 82 | `ExecutorWorker` / `start_executor_worker` — dispatch off the UI thread |
| `scripts/session_002_smoke.py` | 362 | The real, explicitly invoked end-to-end smoke |
| `tests/test_hermes_cli_contract.py` | 16 tests | Argv construction, parser, exit codes, environment filter |
| `tests/test_hermes_driver.py` | 16 tests | Driver lifecycle, result mapping, timeout, honest session ids, resume argv |
| `tests/test_executor.py` | 23 tests | Materialisation, transitions, failure propagation, preflight, restart, schema upgrade |
| `tests/test_hermes_profiles.py` | 13 tests | Table parsing, marker rule, fallbacks, no-write guarantee |
| `tests/test_ui_dispatch.py` | 8 tests | Worker thread, responsiveness, panel readouts, visible failures |

## FILES_CHANGED

| File | Change |
|---|---|
| `src/encomm_pcc/drivers/hermes.py` | Placeholder → real adapter (337 lines changed) |
| `src/encomm_pcc/drivers/process.py` | `Popen`-based runner: timeout is data, whole-tree kill, no orphans |
| `src/encomm_pcc/drivers/__init__.py` | Exports for the new contract |
| `src/encomm_pcc/core/__init__.py` | Exports for executor + discovery |
| `src/encomm_pcc/core/controller.py` | `attach_executor()`/`transition()`/`persist()`/`executor`, honest `request_start()` |
| `src/encomm_pcc/core/session_manager.py` | `register_session()` records real external ids |
| `src/encomm_pcc/domain/models.py` | `TaskStateRecord.prompt` |
| `src/encomm_pcc/persistence/schema.sql` | Schema v2: `tasks.prompt` |
| `src/encomm_pcc/persistence/database.py` | `SCHEMA_VERSION = 2`, targeted v1→v2 upgrade, prompt in the round-trip |
| `src/encomm_pcc/ui/main_window.py` | TASK section, dispatch wiring, worker lifecycle, v0.2 title |
| `src/encomm_pcc/ui/panels.py` | `TaskPanel`, profile completion, honest BATCH hint |
| `src/encomm_pcc/ui/__init__.py`, `src/encomm_pcc/app.py` | Exports; `attach_default_executor()`, startup discovery |
| `src/encomm_pcc/__init__.py` | `0.2.0` |
| `README.md`, `pyproject.toml`, `requirements.txt` | Version/claims updated to reality |
| `tests/conftest.py` | Fakes: `FakeProcessRunner`, `PermissiveRunner`, `FakeDriver`, stream builders |
| `tests/test_drivers.py` | Placeholder refusal scoped to Codex/GenericCli; timeout + tree-kill tests |
| `tests/test_imports.py`, `tests/test_ui_smoke.py`, `tests/test_controller.py` | Assertions updated to the new, real behaviour |
| `docs/reports/SESSION_001_FOUNDATION.md` | Pre-existing SHA backfill, preserved and now committed |

---

## TESTS_RUN

```bash
cd "C:\Users\xampos\Desktop\ENCOMM PIPELINE CONTROL CENTER"
python -m pytest
python -m pytest --collect-only -q          # per-file counts
python scripts/session_002_smoke.py …       # the real run (outside the suite)
git diff --check
# secret scan over the full raw diff
# .gitignore / artifact check
# Hermes profile + config integrity check (mtimes and listing)
```

---

## TEST_RESULTS

```
221 passed in 4.86s
```

**221 passed, 0 failed, 0 skipped, 0 errors.** Exit code 0.

| File | Tests |
|---|---|
| `test_controller.py` | 32 |
| `test_executor.py` | 23 |
| `test_state_machine.py` | 23 |
| `test_domain_models.py` | 18 |
| `test_persistence.py` | 18 |
| `test_drivers.py` | 18 |
| `test_session_policy.py` | 16 |
| `test_ui_smoke.py` | 16 |
| `test_hermes_cli_contract.py` | 16 |
| `test_hermes_driver.py` | 16 |
| `test_hermes_profiles.py` | 13 |
| `test_ui_dispatch.py` | 8 |
| `test_imports.py` | 4 |
| **Total** | **221** |

The unit suite is offline by construction: the Hermes path is exercised through
`FakeProcessRunner` (canned exit codes and stream-json), and the only real engine
run is `scripts/session_002_smoke.py`.

### DEFECTS_FOUND_AND_FIXED

All were found by running the suite and reading the **complete** failure output,
then fixed at the root cause:

| # | Defect | Root cause | Fix |
|---|---|---|---|
| 1 | `ExecutionReport.executor_started` was `False` after a real launch | the executor passed the *raw* runner to `registry.create()`, so the launch recorder never saw the spec | drivers now receive the recording runner; `executor_started` is `bool(recorder.launched)` |
| 2 | A blocked preflight left the pipeline in `PLANNING_BATCH` with an empty batch | the batch was created before the preflight ran | phase gate and preflight are now read-only and run first; a blocked dispatch changes no state |
| 3 | Batch status stayed `CREATED` during a run | nothing set `BatchStatus.RUNNING` | set when the task enters `RUNNING_TASK` |
| 4 | Profile discovery leaked the host's own Hermes installation into callers that supplied their own root | `extra_roots` was *appended* to the derived roots instead of replacing them | added a `roots=` parameter that replaces the candidates (the derived roots remain the default) |
| 5 | `parse_profile_list` accepted prose ("not a table at all" → `["not"]`) | rows were collected without requiring the table's separator | names are read only after the box-drawing separator row |

Test-only defects (2): a class-level counter that accumulated across tests, and a
stop-request test that captured the fixture executor instead of the executor under
test. Both were test bugs; the production behaviour was correct.

---

## FAILURE_PROPAGATION_TEST

Required assertion: **non-zero execution ≠ success**, with the error persisted and
surfaced.

| Test | Scenario | Asserted result |
|---|---|---|
| `test_a_failed_child_process_can_never_become_a_green_task` | driver returns a failure result | task `FAILED`, `last_error` recorded, batch `FAILED`, pipeline `FAILED`, ERROR event in the DB; reload from SQLite shows `FAILED` |
| `test_real_hermes_non_zero_exit_propagates_to_a_failed_task` | **the real HermesDriver** over a child that exited **2** with a bad stream | `ExecutionOutcome.FAILED`, `prompt_result.exit_code == 2`, `simulated is False`, task `FAILED` |
| `test_a_stream_without_a_result_record_cannot_be_success` | exit code 0 but no terminal record | `ok is False` with "no terminal 'result' record" |
| `test_timeout_is_reported_as_failure_not_success` | killed child | `ok is False`, `timed_out` metadata, failure text |
| `test_unexpected_driver_errors_fail_the_task_instead_of_escaping` | driver raises | `FAILED` report, error recorded, pipeline `FAILED` |

Additionally, the process layer itself asserts a non-zero exit is reported as
data, and a timed-out child is killed rather than waited out (`test_subprocess_runner_kills_the_tree_on_timeout`).

---

## UI_STATUS

`MainWindow` title is now `ENCOMM Pipeline Control Center — v0.2`. The layout
gained one section and kept the rest:

| Section | Contents | Functional? |
|---|---|---|
| WORKSPACE | name, repository path, exists indicator | Yes |
| ROLES (×4) | engine, profile (with completion from discovery), provider, model, session, policy | Yes — writes to the controller |
| BATCH | size, phase, status, Start/Pause/Resume/Stop + an honest hint that depends on whether an executor is attached | Yes (state level) |
| **TASK** | Hermes driver availability + resolved CLI path, discovered profiles, builder profile, task title/prompt, **Dispatch task**, task-1 state, status, failure, real result box | Yes — dispatches through the executor |
| LOG PANEL | timestamped event log | Yes |

**Threading.** Dispatch runs on a `QThread` via `ExecutorWorker`; the report
returns through a queued signal. `test_dispatch_runs_on_a_worker_thread_and_the_ui_stays_responsive`
proves the Qt event loop keeps processing timers while the worker sleeps, and that
the driver ran on a different thread. `closeEvent` waits briefly for a running
worker instead of tearing the thread down.

The panel shows only what the executor reported: outcome, phase, exit code, real
session id, duration, `simulated`, the argv, an output excerpt and the failure
text. When no executor is attached the dispatch button is disabled and the BATCH
hint says so.

---

## SECURITY_CHECK

| Check | Result |
|---|---|
| Secret pattern scan over the full raw diff (`sk-…`, `ghp_…`, `github_pat_…`, `AKIA…`, `-----BEGIN … PRIVATE KEY`, `api_key=`, `secret=`, `password=`, `Bearer …`) | **`NO_SECRETS_FOUND`** |
| Complete environment dumps persisted | none — the child environment exists only in memory for the launch; the event payload stores profile/model/exit code/token **counts** and a bounded text excerpt |
| `Authorization` / bearer headers / provider secrets in SQLite, logs, reports, fixtures | none |
| Secrets in the smoke output or the report | none — only profile name, model name, ids, exit codes and token counts |
| `os.environ` reads in tracked source | path/platform only (`ENCOMM_PCC_DATA_DIR`, `LOCALAPPDATA`, `QT_QPA_PLATFORM`), plus the documented environment *filter* which reads and drops (never stores) `HERMES_*` keys |
| Hermes profiles/config modified | **none** — only `hermes profile list` (read-only) and `-p <existing>` selections; profile directory listing and mtimes unchanged |
| Runtime artefacts tracked by git | none — `*.db`, `logs/`, `*.log`, `.env*`, keys and secrets are ignored by `.gitignore` |
| API keys read, printed or logged by the application | none |

Command diagnostics are not persisted verbatim: stderr is bounded to a 2000-char
excerpt and the argv (paths/profile/model only) is recorded for traceability.

---

## GIT_STATUS

**Repository:** `https://github.com/Xamposs/ENCOMM-PIPELIN-CONTROL-CENTER`
**Branch:** `main`
**Baseline:** `0a47efba4036f44a97a83daad320495a0818295d`
**Commit:** `feat: add real Hermes executor path`

Pre-commit verification, in the order the brief requires:

| Step | Command | Result |
|---|---|---|
| Re-read material changed source in full | raw reads of every file written or edited | done |
| Inspect the complete raw diff | `git add -A` + `git diff --cached` (statistics and full text) | reviewed |
| Full unit suite | `python -m pytest` | `221 passed` |
| Real controlled Hermes smoke | `python scripts/session_002_smoke.py …` | `SMOKE PASSED` (see REAL_SMOKE_TEST) |
| Inspect the complete smoke output | full stdout of the script, read in full | done |
| Whitespace / conflict markers | `git diff --cached --check` | clean |
| Secret scan | pattern scan over the raw staged diff | `NO_SECRETS_FOUND` |
| Runtime DB/logs ignored | staged path check against `.gitignore` patterns | none staged |
| Only intended files changed | `git status --short` review | only project files |
| No Hermes profile/config modified | profile listing + mtimes before/after | unchanged |

Push: to `origin/main`, no force push, no other branch created. Remote state
re-verified with `git ls-remote --heads origin`, `git rev-parse HEAD` and
`git rev-parse origin/main` after the push (all pointing at the new commit).

---

## KNOWN_LIMITATIONS

Stated plainly. Nothing here is a placeholder presented as a feature.

1. **One task per batch.** Session 002 dispatches exactly one task; a second
   dispatch into the same batch is rejected. There is no planner and no batch
   editor.
2. **Execution stops at `AUDITING_TASK`.** Nothing transitions out of it yet: the
   Task Auditor does not exist.
3. **No mid-prompt cancellation.** `supports_cancellation=False` and `cancel()`
   returns `False`. Stop takes effect at the next safe task boundary; a running
   prompt is allowed to finish and its real result is recorded.
4. **Pause is boundary-only.** With a single task there is no mid-run boundary, so
   a pause requested during a prompt is recorded and reported but cannot suspend
   anything.
5. **`CodexDriver` and `GenericCliDriver` remain placeholders** and refuse real
   work. `GenericCliDriver` still has no configurable command line.
6. **No streaming surface.** The CLI streams deltas and the driver consumes them,
   but callers only ever see the finished answer.
7. **Output retention is bounded.** The event payload keeps a 4000-character
   excerpt of the engine's answer (the full text lives on the returned
   `PromptResult`); there is no separate artefact store yet.
8. **Runner default is still `NullProcessRunner`.** Only `app.run()` (via
   `attach_default_executor`) wires the real `SubprocessRunner`; a controller built
   by hand, or by tests, cannot launch anything.
9. **Schema v2 has exactly one in-place upgrade step** (v1→v2). There is still no
   migration framework, by design.
10. **`Executor` holds pause/stop flags in memory.** A crash mid-dispatch loses the
    stop request; persisted state (phase, task, batch) is recoverable.
11. **Auditor/fix-loop/orchestrator/final-auditor do not exist.** Batches larger
    than one task are not planned or sequenced.
12. **UI tests are offscreen only**; native windowing behaviour is unverified.
13. **Smoke coverage is one prompt + one resume + one model override.** Long
    prompts, multi-turn tool use and provider failure modes were not exercised
    live.

---

## RISKS

| # | Risk | Severity | Mitigation in place | Residual |
|---|---|---|---|---|
| R1 | A future session mistakes a remaining placeholder (Codex/GenericCli) for a working engine | **High** | `implemented=False` on both; the executor preflights on `implemented`; `ARCHITECTURE.md` §12 and `CURRENT_STATE.md` §5 list it | Reduced; the handoff contract must be followed |
| R2 | The `executor_started` invariant is weakened later | **High** | Derived from a launch recorder around the injected runner, not from intent; unit tests pin both the blocked (`False`) and dispatched (`True`) cases | Needs care when new drivers arrive |
| R3 | A fabricated `PromptResult` is introduced to "make the pipeline flow" | **High** | `simulated` exists; a real run never sets it; a missing terminal record cannot be success | Review discipline |
| R4 | An unbounded fix/audit loop arrives with Phase 2 | **High** | Not implemented — no loop exists. `audit_rounds` is ready to carry a cap | **Open by design.** Phase 2 must cap rounds and escalate to `BLOCKED` |
| R5 | A real engine run costs money or runs away | Medium | Per-prompt timeout (900 s default) with whole-tree kill; the executor runs one task per batch; the smoke is explicit and bounded | Cost per run is inherent |
| R6 | Child processes orphaned on timeout | Medium | `kill_process_tree` (`taskkill /F /T` on Windows, process-group kill elsewhere) is invoked on expiry and covered by a test | Grandchildren of grandchildren are best-effort |
| R7 | Hermes CLI flags/behaviour drift on upgrade | Medium | The contract lives in one module with its own tests; capability flags are evidence-gated; the smoke script re-proves the live path on demand | Inherent to an external CLI |
| R8 | `NullProcessRunner` default is removed "temporarily" | Medium | Default lives in `BaseDriver.__init__`; wiring the real runner happens in exactly one place (`attach_default_executor`); tests assert the default | Would re-open accidental agent launches |
| R9 | Schema drift with no framework | Medium | Version guard refuses a newer schema; the v1→v2 upgrade is explicit and unit-tested | Grows with each change |
| R10 | The sanitised child environment hides a variable a future engine needs | Low | The filter is documented and unit-tested; `PATH`/`SYSTEMROOT`/home/temp are preserved and the run works | Revisit if a driver needs a specific variable |
| R11 | Session ids become a de-facto source of truth | Medium | Ids are mirrored to SQLite with `external=1`; task state and results are authoritative | Design-level; enforce when the auditor lands |
| R12 | A queued worker outlives the window | Low | `closeEvent` waits (bounded) for a running worker; the thread reference is retained so it cannot be collected | Closing during a long run still blocks up to 10 s |

**Highest-priority risk to address next:** R4 (unbounded loop) — Phase 2 introduces
the loop, so the round cap must exist from its first commit.

---

## NEXT_RECOMMENDED_SESSION

**Session 003 — Task Auditor + capped fix loop for a single task.**

1. A `TASK_AUDITOR` dispatch through the same executor (`persistent_per_batch`
   policy, resolved by `SessionManager.decide()`), reusing `ExecutionReport` and
   the failure contract.
2. **Structured verdicts** (pass / fail / needs-fix) parsed from the auditor's
   answer — not free text — with the verdict persisted.
3. `AUDITING_TASK → FIX_REQUIRED → RUNNING_FIX → AUDITING_TASK` with a **hard round
   cap** written from the first commit, escalating to `BLOCKED` when it is hit, and
   incrementing `TaskStateRecord.attempts` / `audit_rounds` for real.
4. Use the now-proven resume path so the auditor keeps one session per batch.
5. Extend the smoke script with an auditor run, keeping it outside the unit suite.

**Do not start** multi-task batches, orchestrator planning or the final batch
auditor — they depend on the single-task audit/fix loop being correct first.

---

## VERIFICATION CHECKLIST (accuracy guard)

- [x] Actual changed source read in full — every file created or edited was
      authored and reviewed in full
- [x] Complete raw diff reviewed before commit
- [x] Relevant tests run **and their output read** — `221 passed in 4.86s`
- [x] Complete failure output inspected — all five production defects were
      diagnosed from full tracebacks, not summary lines
- [x] CRITICAL claims point to exact evidence — the real-execution claim rests on a
      captured exit code, the child's own stdout, and SQLite rows re-read after the
      run (`docs/reports/SESSION_002_HERMES_EXECUTOR.md` REAL_SMOKE_*)
- [x] No conclusion rests solely on a compressed representation
- [x] No functionality claimed that is only a placeholder — see
      KNOWN_LIMITATIONS and `ARCHITECTURE.md` §12
- [x] Nothing outside this repository was modified; Hermes configuration and
      profiles are untouched