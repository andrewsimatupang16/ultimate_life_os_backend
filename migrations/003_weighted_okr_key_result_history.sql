ALTER TABLE sub_goals
  ADD COLUMN IF NOT EXISTS weight INTEGER DEFAULT 1,
  ADD COLUMN IF NOT EXISTS target_value DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS current_value DOUBLE PRECISION DEFAULT 0,
  ADD COLUMN IF NOT EXISTS progress_mode VARCHAR DEFAULT 'manual';

UPDATE sub_goals
SET weight = 1
WHERE weight IS NULL;

UPDATE sub_goals
SET current_value = 0
WHERE current_value IS NULL;

UPDATE sub_goals
SET progress_mode = 'manual'
WHERE progress_mode IS NULL;

CREATE TABLE IF NOT EXISTS key_result_history (
  id UUID PRIMARY KEY,
  key_result_id UUID NOT NULL REFERENCES sub_goals(id) ON DELETE CASCADE,
  nilai_perubahan DOUBLE PRECISION NOT NULL,
  timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_key_result_history_key_result_id
  ON key_result_history(key_result_id);

CREATE INDEX IF NOT EXISTS ix_key_result_history_timestamp
  ON key_result_history(timestamp);
