CREATE TABLE IF NOT EXISTS notifications (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title VARCHAR NOT NULL,
  message TEXT NOT NULL,
  notification_type VARCHAR NOT NULL DEFAULT 'general',
  channel VARCHAR NOT NULL DEFAULT 'in_app',
  dedupe_key VARCHAR,
  metadata_json TEXT,
  read_at TIMESTAMP,
  scheduled_for TIMESTAMP,
  sent_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_notifications_user_dedupe_key
  ON notifications(user_id, dedupe_key)
  WHERE dedupe_key IS NOT NULL AND deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_notifications_user_created_at
  ON notifications(user_id, created_at);

CREATE INDEX IF NOT EXISTS ix_notifications_user_read_at
  ON notifications(user_id, read_at);
