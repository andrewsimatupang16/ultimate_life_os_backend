ALTER TABLE budgets
  ADD COLUMN IF NOT EXISTS period VARCHAR DEFAULT 'monthly',
  ADD COLUMN IF NOT EXISTS start_date TIMESTAMP,
  ADD COLUMN IF NOT EXISTS end_date TIMESTAMP;

UPDATE budgets
SET
  period = COALESCE(period, 'monthly'),
  start_date = COALESCE(start_date, date_trunc('month', CURRENT_TIMESTAMP)),
  end_date = COALESCE(
    end_date,
    date_trunc('month', CURRENT_TIMESTAMP) + INTERVAL '1 month' - INTERVAL '1 microsecond'
  )
WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_budgets_user_category_period ON budgets(user_id, category, period);
CREATE INDEX IF NOT EXISTS ix_budgets_start_end_date ON budgets(start_date, end_date);
