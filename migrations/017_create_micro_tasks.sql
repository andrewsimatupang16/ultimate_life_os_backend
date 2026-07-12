CREATE TABLE IF NOT EXISTS micro_tasks (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title VARCHAR(180) NOT NULL,
  hint VARCHAR(240),
  duration_key VARCHAR(32) NOT NULL,
  category VARCHAR(48) NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_micro_tasks_user_duration_created_at
  ON micro_tasks(user_id, duration_key, created_at);

CREATE INDEX IF NOT EXISTS ix_micro_tasks_user_id
  ON micro_tasks(user_id);
