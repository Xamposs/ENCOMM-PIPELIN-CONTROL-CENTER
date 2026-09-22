-- ENCOMM Pipeline Control Center — schema v4.
-- v1 → v2 adds `tasks.prompt` (the implementation prompt a task must keep so it
-- can be re-read, re-dispatched and audited after a restart).
-- v2 → v3 adds the audit/fix round-trip columns (Session 003): the latest
-- structured verdict, its serialised payload + fix_prompt, and the real
-- external session ids of the initial Builder, the Task Auditor and the fix
-- Builder, so the session-isolation contract survives a restart.
-- v3 → v4 adds Scope-004 batch planning: the durable Project Brief and the
-- finished HEAD on `batches`, the Orchestrator-supplied acceptance
-- criteria/audit focus on `tasks`, and the `batch_plans` table holding the
-- strict plan, the Orchestrator session id, the read-only baseline, and the
-- durable Batch Summary for the Final Auditor.
-- All in-place upgrades are single targeted ALTERs in Database._upgrade() —
-- still no migration framework; see docs/DECISIONS.md.

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    repo_path    TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS role_configs (
    config_id             TEXT PRIMARY KEY,
    workspace_id          TEXT NOT NULL,
    role                  TEXT NOT NULL,
    engine                TEXT NOT NULL DEFAULT '',
    project_profile       TEXT NOT NULL DEFAULT '',
    provider              TEXT NOT NULL DEFAULT '',
    model                 TEXT NOT NULL DEFAULT '',
    session_policy        TEXT NOT NULL,
    session_id            TEXT,
    same_as_orchestrator  INTEGER NOT NULL DEFAULT 0,
    extra_json            TEXT,
    updated_at            TEXT NOT NULL,
    UNIQUE (workspace_id, role),
    FOREIGN KEY (workspace_id) REFERENCES workspaces (workspace_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id          TEXT PRIMARY KEY,
    workspace_id        TEXT NOT NULL,
    role                TEXT NOT NULL,
    driver_id           TEXT NOT NULL,
    external_session_id TEXT,
    persistent          INTEGER NOT NULL DEFAULT 0,
    external            INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    closed_at           TEXT,
    metadata_json       TEXT,
    FOREIGN KEY (workspace_id) REFERENCES workspaces (workspace_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessions_workspace_role
    ON sessions (workspace_id, role);

CREATE TABLE IF NOT EXISTS batches (
    batch_id     TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    size         INTEGER NOT NULL DEFAULT 5,
    status       TEXT NOT NULL,
    phase        TEXT NOT NULL DEFAULT 'IDLE',
    project_brief TEXT NOT NULL DEFAULT '',
    current_head TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    FOREIGN KEY (workspace_id) REFERENCES workspaces (workspace_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_batches_workspace
    ON batches (workspace_id, created_at DESC);

-- Durable planning truth for every batch (Session 004).  The strict plan JSON
-- is the only source of the task list; the Orchestrator session id is the real
-- external id when the engine exposed one (never fabricated); the baseline is
-- captured read-only before planning so a planning call that modifies the
-- workspace is detected and the plan is BLOCKED.
CREATE TABLE IF NOT EXISTS batch_plans (
    batch_id                 TEXT PRIMARY KEY,
    batch_title              TEXT NOT NULL DEFAULT '',
    batch_objective          TEXT NOT NULL DEFAULT '',
    plan_json                TEXT,
    orchestrator_session_id  TEXT,
    plan_status              TEXT NOT NULL DEFAULT '',
    planned_at               TEXT,
    baseline_head            TEXT NOT NULL DEFAULT '',
    baseline_fingerprint_json TEXT,
    current_head             TEXT NOT NULL DEFAULT '',
    final_phase              TEXT NOT NULL DEFAULT '',
    finalized_at             TEXT,
    batch_summary_json       TEXT,
    FOREIGN KEY (batch_id) REFERENCES batches (batch_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id            TEXT PRIMARY KEY,
    batch_id           TEXT NOT NULL,
    task_index         INTEGER NOT NULL DEFAULT 0,
    title              TEXT NOT NULL DEFAULT '',
    prompt             TEXT NOT NULL DEFAULT '',
    acceptance_criteria TEXT,
    audit_focus        TEXT,
    state              TEXT NOT NULL,
    attempts           INTEGER NOT NULL DEFAULT 0,
    audit_rounds       INTEGER NOT NULL DEFAULT 0,
    last_error         TEXT,
    latest_verdict     TEXT,
    verdict_json       TEXT,
    fix_prompt         TEXT,
    auditor_session_id TEXT,
    builder_session_id TEXT,
    fix_session_id     TEXT,
    updated_at         TEXT NOT NULL,
    FOREIGN KEY (batch_id) REFERENCES batches (batch_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tasks_batch
    ON tasks (batch_id, task_index);

CREATE TABLE IF NOT EXISTS app_events (
    event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    level        TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT '',
    message      TEXT NOT NULL,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_app_events_ts
    ON app_events (ts DESC);
