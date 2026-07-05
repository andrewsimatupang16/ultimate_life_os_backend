ALTER TABLE activity_logs
  ADD COLUMN IF NOT EXISTS duration_seconds INTEGER;

UPDATE activity_logs
SET duration_seconds = duration_minutes * 60
WHERE duration_seconds IS NULL
  AND duration_minutes IS NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'ck_activity_logs_duration_seconds_positive'
  ) THEN
    ALTER TABLE activity_logs
      ADD CONSTRAINT ck_activity_logs_duration_seconds_positive
      CHECK (duration_seconds IS NULL OR duration_seconds > 0) NOT VALID;
  END IF;
END $$;
