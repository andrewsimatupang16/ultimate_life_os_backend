ALTER TABLE users
  ADD COLUMN IF NOT EXISTS task_current_streak INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS task_best_streak INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS task_last_completed_date DATE;

UPDATE users
SET
  task_current_streak = COALESCE(task_current_streak, 0),
  task_best_streak = COALESCE(task_best_streak, 0);

CREATE TABLE IF NOT EXISTS user_achievements (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  achievement_key VARCHAR NOT NULL,
  title VARCHAR NOT NULL,
  description VARCHAR,
  icon VARCHAR,
  awarded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  source_type VARCHAR,
  source_id UUID,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_user_achievements_user_key
  ON user_achievements(user_id, achievement_key)
  WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_user_achievements_user_awarded_at
  ON user_achievements(user_id, awarded_at);
