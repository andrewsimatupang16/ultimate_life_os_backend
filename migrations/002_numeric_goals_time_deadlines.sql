-- Run this after 001_remove_stamina_balance.sql for an existing PostgreSQL database.

ALTER TABLE goals
  ADD COLUMN IF NOT EXISTS target_value DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS current_value DOUBLE PRECISION DEFAULT 0,
  ADD COLUMN IF NOT EXISTS target_unit VARCHAR,
  ADD COLUMN IF NOT EXISTS progress_mode VARCHAR DEFAULT 'manual';

ALTER TABLE tasks
  ADD COLUMN IF NOT EXISTS due_date TIMESTAMP;

ALTER TABLE habits
  ADD COLUMN IF NOT EXISTS reminder_time VARCHAR;

CREATE TABLE IF NOT EXISTS bill_reminders (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title VARCHAR NOT NULL,
  category VARCHAR NOT NULL,
  amount DOUBLE PRECISION,
  due_date TIMESTAMP NOT NULL,
  is_paid BOOLEAN NOT NULL DEFAULT FALSE,
  paid_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_bill_reminders_user_id ON bill_reminders(user_id);
CREATE INDEX IF NOT EXISTS ix_bill_reminders_due_date ON bill_reminders(due_date);

CREATE TABLE IF NOT EXISTS activity_logs (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  category VARCHAR NOT NULL,
  title VARCHAR NOT NULL,
  duration_minutes INTEGER NOT NULL,
  activity_date TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  notes TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_activity_logs_user_id ON activity_logs(user_id);
CREATE INDEX IF NOT EXISTS ix_activity_logs_activity_date ON activity_logs(activity_date);
