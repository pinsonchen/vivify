-- 0004_rca_tables.sql — RCA reports table for root-cause analysis.
-- Idempotent: safe to re-run.

CREATE TABLE IF NOT EXISTS _schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT);
INSERT OR IGNORE INTO _schema_migrations (version, applied_at) VALUES (4, datetime('now'));

CREATE TABLE IF NOT EXISTS rca_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_hash TEXT NOT NULL,
    recurrence_count INTEGER DEFAULT 0,
    root_cause TEXT,
    pattern TEXT,
    suggested_strategy TEXT,
    related_issues TEXT,  -- JSON array of issue hashes
    confidence REAL DEFAULT 0.5,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rca_issue_hash ON rca_reports(issue_hash);
