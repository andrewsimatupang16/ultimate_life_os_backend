ALTER TABLE tasks
  ADD COLUMN IF NOT EXISTS priority VARCHAR;

UPDATE tasks
SET priority = CASE difficulty
  WHEN 'hard' THEN 'high'
  WHEN 'easy' THEN 'low'
  ELSE 'medium'
END
WHERE priority IS NULL;

ALTER TABLE tasks
  ALTER COLUMN priority SET DEFAULT 'medium',
  ALTER COLUMN priority SET NOT NULL;
