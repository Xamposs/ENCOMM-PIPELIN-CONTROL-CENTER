# ENCOMM Pipeline Control Center — Operator Guide (v1.0)

This guide is written for the **operator** who runs real AI coding batches
(**Coding Mode**) and reviews/validates research proposals (**Proposal
Mode**, Session 017). Everything below refers to actual controls in the
desktop application. **Coding Simple Mode is the default production
surface** — start there (§A). **Proposal Mode** is the second production
surface, selected from the top `CODING MODE | PROPOSAL MODE` tabs (Part P). Advanced
Mode (the full panel grid, formerly the whole app) is the development/debug
surface: launch with `python main.py --debug-ui` to see the "Advanced /
Details…" button, then §B onwards covers it.

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

# Part P — Proposal Mode (Sessions 017–020)

Select **PROPOSAL MODE** from the top tab bar — or the **PROPOSAL MODE**
button inside Coding Simple Mode (both drive the same navigation state).
Press **BACK TO CODING MODE**, or the **CODING MODE** tab, to return.
Coding Mode is never modified by Proposal Mode (separate configuration,
separate workspace). The whole surface scrolls vertically — every section
below is reachable on a normal display.

## P1. The proposal flow (Session 020 production workflow)

```
PROJECT INPUTS → GENERATE INITIAL PROPOSAL → RUN PANEL ITERATION
  (or START CAMPAIGN — the bounded loop drives all of it)
→ RUN HARD GATES → COMPLETE
```

The legacy **RUN ITERATION** button (sequential review → integration)
remains for partial-phase resume; the production V2 button is
**RUN PANEL ITERATION**.

## P2. PROJECT INPUTS

- **Workspace** — choose (or BROWSE to) a folder and press **INITIALIZE**.
  This creates the contract directories and EMPTY seed files only when
  absent — an existing `MASTER_PROPOSAL.md` or any existing file is NEVER
  overwritten. **REFRESH** re-reads the durable state and rebuilds the
  recovery view (a restart never auto-runs AI).
- **IMPORT BLUEPRINT / IMPORT TEMPLATE** — the real source importer
  (`.md`, `.txt`, `.pdf`, `.docx`, `.rtf`; no OCR). The ORIGINAL bytes are
  preserved under `IMPORTS/`, a normalized canonical copy is derived, and
  every import is recorded in `05_CONTROL/SOURCE_IMPORT_MANIFEST.json`.
  Re-importing over a non-empty canonical file asks for explicit
  confirmation and freezes a backup first. Extraction warnings and
  failures are shown — never hidden.
- **ADD OFFICIAL DOCS** — multi-select; one normalized document per file
  under `01_OFFICIAL/NORMALIZED/`.
- **IMPORT PROPOSAL** — fills the (still empty) master from an existing
  draft; over a non-empty master it requires the explicit replace
  confirmation (backup-before-replace).
- **Source pack** — the blueprint character budget is displayed
  continuously; when a run would exceed it the label reads
  **SOURCE BUDGET EXCEEDED** before you can start any AI action.

## P3. AGENTS

Four roles — **ASTRA / ORCHESTRATOR — PANEL CHAIR**, **SCIENTIFIC
EVALUATOR**, **PROPOSAL / IMPLEMENTATION EVALUATOR**, **RED TEAM
EVALUATOR**. Each row: Engine, Profile, Provider, Model, Session Mode,
Session, REFRESH.

- For the `hermes` engine, **REFRESH** runs real read-only discovery:
  profiles from the installed CLI, provider/model derived HONESTLY from
  the selected profile's own `config.yaml` (labelled profile-derived —
  the CLI exposes no model catalogue), sessions profile-scoped from the
  production discovery bridge. Every combo stays EDITABLE — nothing is
  invented, anything can be typed.
- **Session Mode** defaults to **NEW SESSION**. RESUME SELECTED SESSION
  requires you to pick a real session id from the discovered list;
  switching profile (or engine) resets both — a resume binding is never
  carried across profiles implicitly.
- The configuration persists to the workspace
  (`05_CONTROL/PROPOSAL_CONFIG.json`) — never into Coding Mode.

## P4. PANEL / CAMPAIGN

- **GENERATE INITIAL PROPOSAL** — enabled only when the blueprint and
  template are READY, the master is EMPTY, all four engines are
  configured and nothing is running. Three specialists run in parallel,
  then ASTRA synthesizes (4 model calls).
- **RUN PANEL ITERATION** — the production V2 iteration: three parallel
  evaluator calls → three parallel consensus calls over the docket →
  ASTRA as PANEL CHAIR + sole editor (7 calls, 6 on the clean-pass
  bypass). Panel disagreements land in the consensus matrix (§P6).
- **RUN HARD GATES** — deterministic, zero-AI; enabled only from
  HARD_GATE_VALIDATION. Fill the evidence files first
  (**CREATE EVIDENCE SKELETON** creates placeholders ONLY where missing —
  skeletons are never pass-ready).
- **AUTONOMOUS PANEL CAMPAIGN** — configure Max hours (1–120), Max
  iterations (1–50), Target readiness (default 92), Max model calls, and
  the no-improvement limit; then START. The campaign drives generation →
  chair iterations → gates itself, checkpointing durable state after
  every stage. **PAUSE / STOP are boundary requests**: the current model
  call always finishes first ("Pause requested — current model
  call/stage will finish first."). **RESUME** appears for recoverable
  durable states (PAUSED / WAITING_FOR_OPERATOR / stopped bounds) and
  continues at the persisted checkpoint — a restart NEVER auto-runs.

## P5. CURRENT STATE & READINESS

Campaign status, phase, iteration, proposal hash, model calls used,
elapsed campaign time, and the advisory **Internal readiness** — always
labelled **INTERNAL READINESS — NOT AN EIC SCORE**. The readiness history
per iteration renders under it. Readiness never overrides the hard gates.

## P6. PANEL RESULTS

First-pass verdicts per evaluator, then the persisted consensus matrix:
ID, section, agreement/disagreement/insufficient-evidence counts,
blocking flag, and RESOLVED/UNRESOLVED per item, with the total
"Unresolved disagreements: N". Every value is read from the durable
`panel_consensus.json` — nothing is recomputed or faked green.

## P7. Reading the surfaces

- **CURRENT STATE** always shows the real phase (IDLE, SOURCE_VALIDATION,
  the review phases, INTEGRATION, HARD_GATE_VALIDATION,
  REVISION_REQUIRED, BLOCKED, FAILED, COMPLETE) — the UI never invents a
  nicer one.
- **HARD GATES** lists all 14 canonical gates with the exact status and
  message from the durable artifacts; `NOT_RUN` means no artifact has
  recorded that gate yet.
- **EVIDENCE** shows MISSING/EMPTY/VALID JSON/INVALID JSON per file — the
  hard-gate engine, not this table, is the authority.
- When the campaign stops at **WAITING_FOR_OPERATOR**, the panel explains:
  deterministic hard gates need operator evidence and NO further model
  calls will be made until it is resolved. Fix the evidence, then RESUME.
- If recovery is ambiguous (a partial run), the panel says **Recovery
  requires operator confirmation** and nothing auto-runs.

## P8. Buttons are disabled while a run is in progress

Only one proposal operation runs at a time (worker thread). Conflicting
buttons re-enable when the report lands; the campaign's PAUSE/STOP stay
armed while it runs (they are boundary requests, not cancellations).

---

# Part P1 — Living Blueprint + Document Pair + Codex chair (Session 021, v1.5.0)

**The Blueprint/Proposal are now a versioned pair.**

- **MASTER BLUEPRINT (original)** — imported once, NEVER modified by any
  AI run. Its hash is the provenance anchor (PROJECT INPUTS shows it).
- **CURRENT / LIVING BLUEPRINT** — created automatically when you import a
  blueprint (exact copy of the original). Only the ASTRA/ORCHESTRATOR
  runtime may evolve it; PROJECT INPUTS shows its status, hash and
  iteration. **DOCUMENT PAIR** shows the last committed iteration +
  revision id (`05_CONTROL/DOCUMENT_PAIR_STATE.json`).
- **Targets** — every panel finding now says which document it is about:
  PROPOSAL (wording), BLUEPRINT (the design must change), BOTH. The PANEL
  RESULTS tables carry a Target column; the chair sees the same targets.
- **Reviews go stale when EITHER document changes** — the next panel run
  re-reviews the new pair automatically.
- **Codex chair (AGENTS):** pick engine `codex` on a role. Profile and
  Provider are not applicable; **Model** is editable (type the exact model
  id your Codex account offers); **Reasoning** selects the per-invocation
  reasoning effort (DEFAULT, MINIMAL, LOW, MEDIUM, HIGH, XHIGH — verified
  Codex CLI contract; DEFAULT = Codex's own default). Press **REFRESH**
  for REAL Codex session discovery (zero model calls): workspace-matched
  sessions sort first (`[WS]`), other workspaces are marked. To show ASTRA
  inside the ChatGPT/Codex Desktop app: create your project/thread there
  in this workspace, press REFRESH, pick the matching session, then set
  Session Mode to RESUME SELECTED SESSION. Nothing is ever auto-armed:
  RESUME only happens when you explicitly select it.
- **Safety** — an all-or-rollback commit guarantees the Blueprint and
  Proposal always move together; a failed commit restores both documents.
  The immutable original is verified every run.

# Part P0 — Legacy sequential path (Session 017, unchanged)

The original one-click flow is still available exactly as documented in
v1.3: **RUN ITERATION** walks SOURCE_VALIDATION → the three sequential
reviewers → integration over the current master. Use it to resume a
partial-phase workspace; fresh work should use the panel path above.

---

---

# Part B — Advanced Mode reference

> **Access:** this entire surface is behind the debug flag — start the app as
> `python main.py --debug-ui`. Normal launches are Simple Mode only and show
> no Advanced button (Session 011, D-052).

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
