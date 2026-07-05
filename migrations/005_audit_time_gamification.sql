ALTER TABLE users
  ADD COLUMN IF NOT EXISTS timezone VARCHAR DEFAULT 'Asia/Jakarta';

ALTER TABLE bill_reminders
  ADD COLUMN IF NOT EXISTS paid_transaction_id UUID REFERENCES transactions(id);

CREATE TABLE IF NOT EXISTS habit_logs (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  habit_id UUID NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
  habit_type VARCHAR NOT NULL,
  local_date DATE NOT NULL,
  logged_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  xp_earned INTEGER NOT NULL DEFAULT 0,
  coin_earned INTEGER NOT NULL DEFAULT 0,
  penalty INTEGER NOT NULL DEFAULT 0,
  notes TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_habit_logs_habit_local_date
  ON habit_logs(habit_id, local_date)
  WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_habit_logs_user_local_date
  ON habit_logs(user_id, local_date);

CREATE TABLE IF NOT EXISTS time_sessions (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  task_id UUID REFERENCES tasks(id) ON DELETE SET NULL,
  category VARCHAR NOT NULL,
  title VARCHAR NOT NULL,
  started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ended_at TIMESTAMP,
  duration_seconds INTEGER,
  source VARCHAR NOT NULL DEFAULT 'timer',
  notes TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_time_sessions_user_started_at
  ON time_sessions(user_id, started_at);

CREATE TABLE IF NOT EXISTS gamification_events (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  event_key VARCHAR NOT NULL,
  event_type VARCHAR NOT NULL,
  source_type VARCHAR NOT NULL,
  source_id UUID,
  event_date DATE,
  xp_delta INTEGER NOT NULL DEFAULT 0,
  coin_delta INTEGER NOT NULL DEFAULT 0,
  description VARCHAR,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_gamification_events_user_event_key
  ON gamification_events(user_id, event_key)
  WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_gamification_events_user_created_at
  ON gamification_events(user_id, created_at);
