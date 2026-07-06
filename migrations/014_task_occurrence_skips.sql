CREATE TABLE IF NOT EXISTS task_occurrence_skips (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id),
  task_id UUID NOT NULL REFERENCES tasks(id),
  local_date DATE NOT NULL,
  skipped_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMP,
  CONSTRAINT uq_task_occurrence_skips_task_local_date UNIQUE (task_id, local_date)
);

CREATE INDEX IF NOT EXISTS ix_task_occurrence_skips_id
  ON task_occurrence_skips (id);

CREATE INDEX IF NOT EXISTS ix_task_occurrence_skips_user_local_date
  ON task_occurrence_skips (user_id, local_date);
