-- ENCOMM Pipeline Control Center — schema v1 (foundation).
-- Deliberately small. No migration framework yet; see docs/DECISIONS.md (D-008).

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
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    FOREIGN KEY (workspace_id) REFERENCES workspaces (workspace_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_batches_workspace
    ON batches (workspace_id, created_at DESC);

CREATE TABLE IF NOT EXISTS tasks (
    task_id      TEXT PRIMARY KEY,
    batch_id     TEXT NOT NULL,
    task_index   INTEGER NOT NULL DEFAULT 0,
    title        TEXT NOT NULL DEFAULT '',
    state        TEXT NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    audit_rounds INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,
    updated_at   TEXT NOT NULL,
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
