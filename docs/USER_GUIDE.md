# ENCOMM Pipeline Control Center — Operator Guide (v1.0)

This guide is written for the **operator** who runs real AI coding batches.
Everything below refers to actual controls in the desktop application.
**Simple Mode is the default surface** — start there (§A). Advanced Mode
(the full panel grid, formerly the whole app) is one click away and is
covered in §B onwards.

---

# Part A — Simple Mode (default)

## A1. Launching PCC

**From the packaged release (recommended):**

```text
dist\ENCOMM-PCC\ENCOMM-PCC.exe
```

**From source (development):**

```text
python main.py
```

Both launch modes use the same per-user data directory (see §21). The window
opens on **Simple Mode**; the **ADVANCED / DETAILS** toggle switches to the
full panel grid. On launch the **DIAGNOSTICS** line is filled in
automatically — read it before doing anything else.

**Self-test** (no window, no model calls, exit code 0 on success):

```text
ENCOMM-PCC.exe --smoke-test
```

## A2. The five things Simple Mode asks for

| Control | What it is | Notes |
|---|---|---|
| **WORKSPACE** | name + repository path the agents work on | existing directory; git strongly recommended (the read-only planning/audit guards need git) |
| **PROJECT GOAL** | the brief the Architect plans from | durable (stored on the batch row); describe the repo, the tasks, the acceptance criteria |
| **ARCHITECT** | plans the batch AND runs the final audit | one Codex thread is recommended — tick *same as orchestrator* semantics by picking the same engine/profile; the selector can bind an existing Codex session (§A4) |
| **CODER** | implements each task | fresh session every build AND every fix (`always_new`); pick engine + Hermes profile + **provider + model** |
| **AUDITOR** | checks each task | ONE session reused for the whole batch (`persistent_per_batch`, survives restarts); same fields as CODER; **AUDITOR SESSION** offers discovered sessions for the current profile (read-only discovery, newest first) |

Tasks per batch: 1–5. Start with 1–2 on a new project.

All Simple Mode fields write the **same durable role configuration** the
Advanced panels use — there is no second source of truth, and switching
modes never loses settings.

## A3. START / PAUSE / STOP and CONTINUOUS

- **START** makes ONE real Architect call (the repository is fingerprinted
  before/after — a planning call that modifies the worktree BLOCKS the plan),
  materialises the planned tasks and begins executing them autonomously:
  build in a fresh Coder session → Auditor check → capped fix loop (max 3
  audit rounds) → next task.
- **CONTINUOUS** (tick box, default on): when a batch reaches
  `READY_FOR_FINAL_AUDIT`, the loop runs the **final audit automatically**.
  A PASS materialises the next batch (zero AI calls) and keeps going. The
  whole run needs exactly ONE planning call per session start.
- **PAUSE** stops after the current task/batch boundary; **STOP** stops
  safely after the current unit and persists everything. Both are honoured
  at boundaries — a running prompt finishes first, then the loop stops and
  the status line says, in plain language, what happened and why (stopped
  as requested, next batch refused, or the final-audit outcome).
- **CONTINUE** resumes from persisted state (after pause, stop, or app
  restart). APPROVED tasks are never re-run; nothing auto-runs at startup.

## A4. Binding existing sessions

- **ARCHITECT (Codex):** the session combo lists discovered Codex threads
  (workspace-matching first). Selecting one binds it — the next run RESUMES
  that thread. **New Session** clears the binding. Switching the engine
  clears a stale binding automatically.
- **AUDITOR (Hermes):** discovery is **profile-scoped** — sessions are
  listed for the selected Hermes profile (`hermes -p <profile> sessions
  list`), so you only see sessions that profile can actually resume. If a
  stored binding was made under a different profile, it is treated as
  inactive and cleared when the profile changes.
- Binding is a zero-model-call, read-only operation.

## A5. CODER RECOVERY OVERRIDE (one-shot)

The Coder always starts brand-new sessions. If a build/fix was interrupted
(e.g. the machine or the CLI died mid-task), the saved session id survives
in SQLite, and Simple Mode shows the **CODER RECOVERY OVERRIDE** affordance
listing that session. Pressing it **arms a one-shot override**: the NEXT
Coder operation resumes the saved session instead of refusing — exactly
once. After that one operation, the `always_new` policy is back in force.
If the provider has lost the session anyway, the operation fails honestly
and the override is consumed. Nothing here is automatic: recovery is always
an explicit operator decision.

## A6. What the operator sees while it runs

The STATUS line tracks the phase (planning → building → auditing → final
audit → handing off). The task table shows the current task, its state,
attempts, audit rounds and the real session ids. The log panel records every
boundary decision. At a final-audit PASS the next batch is materialised with
zero AI calls; the completed batch stays in HISTORY.

---

# Part B — Advanced Mode reference

## B1. Sections

WORKSPACE · ROLES (ORCHESTRATOR / BUILDER / TASK_AUDITOR / FINAL_AUDITOR) ·
BATCH · TASK · FINAL AUDIT · DIAGNOSTICS · HISTORY · LOG. Simple Mode
controls map onto these — Advanced just exposes more (Generic CLI config,
per-role engine switching, session selectors, manual per-step buttons).

## B2. Configuring ORCHESTRATOR (planning)

- **Engine**: `codex` (recommended) or `hermes`. Codex needs no
  provider/model fields — it uses the installed Codex CLI's account.
- Leave the session selector on **New session** unless you want to bind an
  existing session (§A4).

## B3. Configuring BUILDER / TASK_AUDITOR

Engine `hermes`, project profile (autocompleted from real profile
discovery), provider/model free-text passed to the Hermes CLI — or engine
`generic_cli` with **Configure Generic CLI…**: a structured dialog editing
executable, argument tokens (never a shell command), prompt transport
(stdin / temp file), result mode (stdout / json / jsonl field), timeout, and
up to 16 `KEY=VALUE` env overrides (redacted on export). An invalid config
is refused, never saved.

## B4. Configuring FINAL_AUDITOR

Engine `codex` recommended. **Same as Orchestrator** resolves the
Orchestrator's engine and session surface when ticked.

## B5. Manual batch controls

PLAN + START BATCH · RESUME BATCH · Pause · Stop · RUN FINAL AUDIT (choose
next-batch size 4–5) · VIEW NEXT TASKS · START NEXT BATCH (zero AI calls).
Each maps to one executor call; Simple Mode chains them via the continuous
loop, Advanced lets you drive every step by hand.

## B6. NEEDS_FIX / BLOCKED handling

- NEEDS_FIX lands the task in FIX_REQUIRED; **Run fix (NEW Builder
  session)** fixes in a brand-new session, then the SAME auditor session
  re-audits. Capped at 3 rounds; round-3 NEEDS_FIX escalates to BLOCKED.
- BLOCKED requires an operator decision. Nothing runs automatically.
- A malformed model answer is BLOCKED — never a pass.

## B7. History, diagnostics, config exchange

- **HISTORY** lists batches (newest first) with tasks, verdicts, session
  ids, final verdict and next-plan status. Read-only.
- **DIAGNOSTICS** reports version, data dir, DB schema, workspace readiness
  (git/HEAD/dirty/guard) and per-engine availability. Read-only.
- Config export/import is a versioned, secret-free JSON document
  (`encomm-pcc-config` v1) via the core API (`encomm_pcc.core.config_exchange`).
  Export redacts env values; import validates whole and refuses session
  bindings.

---

# Part C — Reference

## 21. Where local data is stored

```text
%LOCALAPPDATA%\ENCOMM Pipeline Control Center\
├── pipeline_control_center.db   (SQLite; all durable state)
└── logs\bootstrap.log           (bounded rotating diagnostics log)
```

Override with the `ENCOMM_PCC_DATA_DIR` environment variable (tests and
multi-instance use). Nothing is written inside the installation directory.

## 22. What happens after a restart

Recovery restores the last workspace, role configuration, the active batch
and its phase from SQLite. Nothing auto-runs: the operator decides
(CONTINUE / RESUME BATCH / Run Task Auditor / RUN FINAL AUDIT …). Completed
batches are not active work — they live in HISTORY. A mid-audit crash of
the Final Auditor recovers to FINAL_AUDIT_RUNNING and still requires a real
new call — never a silent pass. On the Builder side, the interrupted
session is offered through the recovery override (§A5).

## 23. Common error messages

| Message | Meaning / fix |
|---|---|
| `Codex CLI not detected on PATH.` | Install/configure Codex before selecting it for the role. |
| `The configured Generic CLI executable '…' could not be found on PATH.` | Fix the executable name/path in Configure Generic CLI… |
| `Refused locally: the PROJECT BRIEF is empty.` | Enter the Project Goal/Brief before planning. |
| `Workspace path does not exist: …` | Fix the workspace path in WORKSPACE. |
| `Database at … uses schema vX, but this …` | The DB was written by a NEWER PCC — update the application. |
| `Session policy wanted to resume auditor session …, but: …` | The real session is gone (provider-side); the run fails explicitly — start a NEW session for that role. For the BUILDER side, §A5's recovery override is the one-shot tool. |
| `Codex did not finish within Ns …` | Timeout with tree-kill; raise the timeout or simplify the task. |
| Final audit BLOCKED with file list | The auditor modified the worktree; the files are listed — resolve them by hand (nothing is auto-discarded). |
| `Plan BLOCKED: repository was modified during planning` | Something touched the repo during planning; check the event log. |
| Continuous stop: `NEXT_BATCH_REFUSED` | The next batch could not be materialised from the persisted plan; inspect HISTORY and the event log, then START again. |
