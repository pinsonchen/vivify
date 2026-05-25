-- 0003_enhance_feature_model.sql — channels-monitor 启发的字段扩展。
-- Backwards compatible: 所有新增列都允许 NULL 或带默认值。
-- 注意：parent_id 与 feasibility 已在 0001_init.sql 中存在，此处不再重复添加。

ALTER TABLE feature_requests ADD COLUMN image_urls          TEXT;
ALTER TABLE feature_requests ADD COLUMN idea_id             INTEGER;
ALTER TABLE feature_requests ADD COLUMN retry_count         INTEGER NOT NULL DEFAULT 0;
ALTER TABLE feature_requests ADD COLUMN batch_commit_hash   TEXT;
ALTER TABLE feature_requests ADD COLUMN verification_result TEXT;
ALTER TABLE feature_requests ADD COLUMN evaluated_at        TEXT;
ALTER TABLE feature_requests ADD COLUMN started_at          TEXT;
ALTER TABLE feature_requests ADD COLUMN verified_at         TEXT;
ALTER TABLE feature_requests ADD COLUMN completed_at        TEXT;

CREATE INDEX IF NOT EXISTS idx_feature_requests_idea_id           ON feature_requests(idea_id);
CREATE INDEX IF NOT EXISTS idx_feature_requests_batch_commit_hash ON feature_requests(batch_commit_hash);

INSERT OR IGNORE INTO _schema_migrations(version) VALUES (3);
