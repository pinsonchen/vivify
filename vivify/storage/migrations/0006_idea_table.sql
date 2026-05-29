-- 0006_idea_table.sql — Ideas table: intermediate layer between Goal and FeatureRequest.
-- Idempotent: safe to re-run.

CREATE TABLE IF NOT EXISTS _schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT);
INSERT OR IGNORE INTO _schema_migrations (version, applied_at) VALUES (6, datetime('now'));

CREATE TABLE IF NOT EXISTS ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    goal_id INTEGER,
    status TEXT DEFAULT 'proposed',
    priority INTEGER DEFAULT 50,
    feasibility_score REAL,
    estimated_effort TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    approved_at TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_ideas_status ON ideas(status);
CREATE INDEX IF NOT EXISTS idx_ideas_goal_id ON ideas(goal_id);
