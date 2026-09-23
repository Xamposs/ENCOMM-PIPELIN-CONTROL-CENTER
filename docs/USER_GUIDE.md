# ENCOMM Pipeline Control Center — Operator Guide (v0.8)

This guide is written for the **operator** who runs real AI coding batches.
Everything below refers to actual controls in the desktop application.

---

## 1. Launching PCC

**From the packaged release (recommended):**

```text
dist\ENCOMM-PCC\ENCOMM-PCC.exe
```

**From source (development):**

```text
python main.py
```

Both launch modes use the same per-user data directory (see §21). The window
title shows the version. On launch the **DIAGNOSTICS** section is filled in
automatically — read it before doing anything else.

**Self-test** (no window, no model calls, exit code 0 on success):

```text
ENCOMM-PCC.exe --smoke-test
```

## 2. Selecting a workspace

In the **WORKSPACE** section enter a workspace *name* and the *repository
path* of the project the agents will work on. The path must be an existing
directory; a git repository is strongly recommended (the read-only planning
and final-audit guards need git).

## 3. Understanding workspace readiness

Press **REFRESH DIAGNOSTICS** (or change the workspace path). The DIAGNOSTICS
section reports:

- Application version and application data directory
- Database status (schema version)
- Workspace: path exists, directory accessible, git repository YES/NO,
  current HEAD, worktree CLEAN or DIRTY, and whether the read-only
  fingerprint guard is ACTIVE or UNAVAILABLE
- Each engine (Hermes, Codex, Generic CLI): implemented / found on PATH

A **DIRTY worktree is never touched** by PCC — it is surfaced so *you* decide
(uncommitted work may be valid development state).

## 4. Configuring ORCHESTRATOR (planning)

In the ROLES grid, ORCHESTRATOR panel:

- **Engine**: `codex` (recommended) or `hermes`. The orchestrator plans the
  batch; Codex is the proven planner. Codex needs no provider/model fields —
  it uses the installed Codex CLI's account.
- Leave the session selector on **New session** unless you want to bind an
  existing session (§8).

## 5. Configuring BUILDER

BUILDER panel:

- **Engine**: `hermes` (real adapter).
- **Project profile**: a Hermes profile that exists (e.g.
  `encomm-pipeline-control-center`). The field autocompletes from real
  profile discovery; the TASK panel also shows discovered profiles.
- **Provider / model**: e.g. `openrouter` / `deepseek/deepseek-v4.1-flash`.

Every build (and every fix) runs in a **brand-new session** — this is the
`always_new` policy and cannot be changed.

## 6. Configuring TASK_AUDITOR

Same fields as BUILDER (engine `hermes`, profile, provider, model). Its
policy is `persistent_per_batch`: the FIRST audit of a batch creates one
session, and every audit in that batch — including after a restart — reuses
it.

## 7. Configuring FINAL_AUDITOR

- **Engine**: `codex` recommended. Tick or clear **Same as Orchestrator**:
  when ticked, the role resolves the Orchestrator's engine and session
  surface. For the mixed configuration (Codex orchestrator + Codex final
  auditor) either works; to pin Codex explicitly, clear the checkbox and
  select `codex`.

## 8. Codex existing-session selection

For ORCHESTRATOR / FINAL_AUDITOR with engine `codex`:

- The session combo lists discovered Codex sessions (newest first, the
  workspace-matching ones first). Selecting one **binds** that external
  thread to the role — the next run RESUMES it.
- **Refresh Sessions** re-scans read-only (zero model calls).
- **New Session** clears the binding; the next run creates a fresh thread.
- Switching the role's engine invalidates a stale binding automatically.

## 9. Hermes profile / provider / model

Profile discovery runs read-only at launch. If no profiles are found, type
the profile name manually. Provider/model are free-text configuration passed
to the Hermes CLI.

## 10. Generic CLI configuration

Set the role's **Engine** to `generic_cli`, then press
**Configure Generic CLI…**. The structured dialog edits: executable (name on
PATH or absolute path), argument tokens (whitespace-separated, shell
metacharacters stay literal), prompt transport (`stdin` / temporary file),
result mode (verbatim stdout / json / jsonl field), timeout, and up to 16
`KEY=VALUE` environment overrides (values are redacted on config export).
Placeholders you may use as whole tokens: `{prompt_file}`, `{workspace}`,
`{model}`. An invalid configuration is refused, never saved. A configured
role with no stored Generic CLI config blocks before any process starts.

## 11. Entering the Project Brief

In the **BATCH** section, type the PROJECT BRIEF. This is durable (stored on
the batch row in SQLite) and is the ONLY context the Orchestrator gets about
*what* to plan. Describe the repository, the exact tasks to plan, and the
acceptance criteria (e.g. which test commands must exit 0).

## 12. Choosing the batch size

**Batch size** spin box: 1–5 tasks (planning must produce exactly this
count). Start with 1–2 for a new project.

## 13. Planning a batch

Press **PLAN + START BATCH**. This makes ONE real Orchestrator call (the
repository is fingerprinted before/after — a planning call that modifies the
worktree BLOCKS the plan), materialises every planned task as PENDING, then
starts executing them autonomously.

## 14. Starting / resuming a batch

- **Start** / **PLAN + START BATCH**: plan and run.
- **RESUME BATCH**: after a restart or pause, continues from persisted state.
  APPROVED tasks are never re-run; no AI call happens automatically at
  startup.
- **Pause** / **Stop** take effect at safe boundaries — a prompt that is
  already running finishes and persists its result first.
- While tasks run, the TASK panel shows the current task, its state,
  attempts, audit rounds (max 3) and real session ids.

## 15. Handling NEEDS_FIX / BLOCKED

- A task audit returning NEEDS_FIX lands the task in FIX_REQUIRED; the
  **Run fix (NEW Builder session)** button runs the fix in a brand-new
  Builder session, then the SAME auditor session re-audits. The loop is
  capped at 3 audit rounds; round 3 NEEDS_FIX escalates to BLOCKED.
- BLOCKED requires an operator decision (fix manually, revert, or reset).
  Nothing runs automatically.
- A malformed model answer is treated as BLOCKED — never as a pass.

## 16. Running the Final Audit

When every task is APPROVED the pipeline reaches `READY_FOR_FINAL_AUDIT`.
In the **FINAL AUDIT** section choose the next-batch size (4–5) and press
**RUN FINAL AUDIT**. ONE real Final Auditor call inspects the actual
repository (read-only guard active) and returns BOTH the cumulative verdict
AND the next batch plan in the same response. A PASS completes the batch; a
NEEDS_FIX/BLOCKED lands in an explicit operator state with findings persisted.

## 17. Viewing next tasks

After a PASS, press **VIEW NEXT TASKS** to inspect the persisted next-batch
plan without starting anything.

## 18. Starting the next batch

Press **START NEXT BATCH**. This materialises the persisted plan into a new
batch (PENDING tasks with their criteria) with **ZERO AI calls**, and stops.
You then start it like any batch (§14). The completed batch remains in
HISTORY.

## 19. History

The **HISTORY** panel lists batches (newest first) for the current workspace;
select one to see its tasks, states, attempts, verdicts, real session ids,
the final verdict and the next-plan status. **Refresh history** re-reads the
durable records. History is read-only.

## 20. Config export / import

Configuration export/import is a versioned, secret-free JSON document
(`encomm-pcc-config` v1) built by the core API (`build_export` /
`write_export` / `read_export` / `validate_export` / `apply_import` in
`encomm_pcc.core.config_exchange`). Export redacts Generic CLI env values;
import refuses invalid documents whole and never fabricates secrets. Session
bindings are deliberately excluded. (No UI dialog in v0.8 — the feature is a
core surface used by scripts/tests.)

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
and its phase from SQLite. Nothing auto-runs: the operator decides (RESUME
BATCH / Run Task Auditor / RUN FINAL AUDIT …). Completed batches are not
active work — they live in HISTORY. A mid-audit crash of the Final Auditor
recovers to FINAL_AUDIT_RUNNING and still requires a real new call — never a
silent pass. A batch-finished terminal state is safe to reopen.

## 23. Common error messages

| Message | Meaning / fix |
|---|---|
| `Codex CLI not detected on PATH.` | Install/configure Codex before selecting it for the role. |
| `The configured Generic CLI executable '…' could not be found on PATH.` | Fix the executable name/path in Configure Generic CLI… |
| `Refused locally: the PROJECT BRIEF is empty.` | Enter a Project Brief before planning. |
| `Workspace path does not exist: …` | Fix the workspace path in WORKSPACE. |
| `Database at … uses schema vX, but this …` | The DB was written by a NEWER PCC — update the application. |
| `Session policy wanted to resume auditor session …, but: …` | The real session is gone (provider-side); the run fails explicitly — start a NEW session for that role. |
| `Codex did not finish within Ns …` | Timeout with tree-kill; raise the timeout or simplify the task. |
| Final audit BLOCKED with file list | The auditor modified the worktree; the files are listed — resolve them by hand (nothing is auto-discarded). |
| `Plan BLOCKED: repository was modified during planning` | Something touched the repo during planning; check the event log. |
