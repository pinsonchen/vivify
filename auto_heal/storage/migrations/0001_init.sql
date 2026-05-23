-- 0001_init.sql — initial schema for auto-heal SQLite storage.
-- Idempotent: safe to re-run. Tracked via the `_schema_migrations` table.

CREATE TABLE IF NOT EXISTS _schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- ── feature_requests ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS feature_requests (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    title               TEXT    NOT NULL,
    description         TEXT    NOT NULL DEFAULT '',
    type                TEXT    NOT NULL DEFAULT 'feature'
                          CHECK (type IN ('feature', 'bug', 'optimization')),
    parent_goal         TEXT,
    parent_id           INTEGER REFERENCES feature_requests(id) ON DELETE SET NULL,
    priority            TEXT,
    status              TEXT    NOT NULL DEFAULT 'pending',
    development_result  TEXT    NOT NULL DEFAULT '',
    commit_hash         TEXT,
    pr_url              TEXT,
    feasibility         TEXT    NOT NULL DEFAULT '',
    summary             TEXT    NOT NULL DEFAULT '',
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_feature_requests_status      ON feature_requests(status);
CREATE INDEX IF NOT EXISTS idx_feature_requests_type        ON feature_requests(type);
CREATE INDEX IF NOT EXISTS idx_feature_requests_parent_id   ON feature_requests(parent_id);
CREATE INDEX IF NOT EXISTS idx_feature_requests_parent_goal ON feature_requests(parent_goal);

-- ── action_logs ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS action_logs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            TEXT    NOT NULL,
    round_num         INTEGER NOT NULL DEFAULT 0,
    action_type       TEXT    NOT NULL,
    status            TEXT    NOT NULL DEFAULT 'running',
    category          TEXT,
    level             TEXT,
    title             TEXT,
    prompt            TEXT,
    result_summary    TEXT,
    improved          INTEGER NOT NULL DEFAULT 0,
    duration_seconds  REAL,
    details_json      TEXT    NOT NULL DEFAULT '{}',
    commit_hash       TEXT,
    pr_url            TEXT,
    created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_action_logs_category    ON action_logs(category);
CREATE INDEX IF NOT EXISTS idx_action_logs_action_type ON action_logs(action_type);
CREATE INDEX IF NOT EXISTS idx_action_logs_created_at  ON action_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_action_logs_run_id      ON action_logs(run_id);

-- ── failure_tracking ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS failure_tracking (
    problem_hash      TEXT    PRIMARY KEY,
    category          TEXT    NOT NULL,
    title             TEXT    NOT NULL DEFAULT '',
    fail_count        INTEGER NOT NULL DEFAULT 0,
    upgraded_feature_id INTEGER REFERENCES feature_requests(id) ON DELETE SET NULL,
    first_seen_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_seen_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_failure_tracking_category ON failure_tracking(category);

-- ── knowledge ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS knowledge_entries (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    category          TEXT    NOT NULL,
    pattern           TEXT    NOT NULL,
    solution_summary  TEXT    NOT NULL DEFAULT '',
    success           INTEGER NOT NULL DEFAULT 1,
    feature_id        INTEGER REFERENCES feature_requests(id) ON DELETE SET NULL,
    created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_knowledge_entries_category ON knowledge_entries(category);
CREATE INDEX IF NOT EXISTS idx_knowledge_entries_pattern  ON knowledge_entries(pattern);

-- ── kpi_snapshots ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS kpi_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT    NOT NULL,
    metrics_json    TEXT    NOT NULL DEFAULT '{}',
    overall_score   REAL,
    grade           TEXT,
    captured_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_kpi_snapshots_source      ON kpi_snapshots(source);
CREATE INDEX IF NOT EXISTS idx_kpi_snapshots_captured_at ON kpi_snapshots(captured_at);

INSERT OR IGNORE INTO _schema_migrations(version) VALUES (1);
