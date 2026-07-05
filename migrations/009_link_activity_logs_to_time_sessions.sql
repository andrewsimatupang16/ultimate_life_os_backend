-- Link generated dashboard ActivityLog rows back to their source TimeSession.
-- This prevents duplicate time-allocation minutes when timer stop/sync is retried.

ALTER TABLE activity_logs
  ADD COLUMN IF NOT EXISTS time_session_id UUID;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'fk_activity_logs_time_session_id'
  ) THEN
    ALTER TABLE activity_logs
      ADD CONSTRAINT fk_activity_logs_time_session_id
      FOREIGN KEY (time_session_id)
      REFERENCES time_sessions(id)
      ON DELETE SET NULL;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_activity_logs_user_activity_date
  ON activity_logs(user_id, activity_date);

CREATE UNIQUE INDEX IF NOT EXISTS uq_activity_logs_time_session_active
  ON activity_logs(time_session_id)
  WHERE time_session_id IS NOT NULL AND deleted_at IS NULL;
