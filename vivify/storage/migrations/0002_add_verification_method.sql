-- 0002_add_verification_method.sql — add verification_method to feature_requests.
-- Backwards compatible: column is nullable with no default constraint.

ALTER TABLE feature_requests ADD COLUMN verification_method TEXT;

INSERT OR IGNORE INTO _schema_migrations(version) VALUES (2);
