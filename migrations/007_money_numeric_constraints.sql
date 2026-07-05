-- PostgreSQL migration: store money-like values with fixed precision and add basic integrity checks.

ALTER TABLE wallets
  ALTER COLUMN balance TYPE NUMERIC(14, 2) USING ROUND(balance::numeric, 2),
  ALTER COLUMN balance SET DEFAULT 0;

ALTER TABLE transactions
  ALTER COLUMN amount TYPE NUMERIC(14, 2) USING ROUND(amount::numeric, 2);

ALTER TABLE budgets
  ALTER COLUMN limit_amount TYPE NUMERIC(14, 2) USING ROUND(limit_amount::numeric, 2),
  ALTER COLUMN current_spent TYPE NUMERIC(14, 2) USING ROUND(current_spent::numeric, 2),
  ALTER COLUMN current_spent SET DEFAULT 0;

ALTER TABLE bill_reminders
  ALTER COLUMN amount TYPE NUMERIC(14, 2) USING ROUND(amount::numeric, 2);

ALTER TABLE finance_events
  ALTER COLUMN amount_delta TYPE NUMERIC(14, 2) USING ROUND(amount_delta::numeric, 2),
  ALTER COLUMN balance_after TYPE NUMERIC(14, 2) USING ROUND(balance_after::numeric, 2),
  ALTER COLUMN amount_delta SET DEFAULT 0;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_transactions_amount_positive') THEN
    ALTER TABLE transactions ADD CONSTRAINT ck_transactions_amount_positive CHECK (amount > 0) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_budgets_limit_positive') THEN
    ALTER TABLE budgets ADD CONSTRAINT ck_budgets_limit_positive CHECK (limit_amount > 0) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_budgets_current_spent_nonnegative') THEN
    ALTER TABLE budgets ADD CONSTRAINT ck_budgets_current_spent_nonnegative CHECK (current_spent >= 0) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_budgets_valid_dates') THEN
    ALTER TABLE budgets ADD CONSTRAINT ck_budgets_valid_dates CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_bill_reminders_amount_nonnegative') THEN
    ALTER TABLE bill_reminders ADD CONSTRAINT ck_bill_reminders_amount_nonnegative CHECK (amount IS NULL OR amount >= 0) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_activity_logs_duration_positive') THEN
    ALTER TABLE activity_logs ADD CONSTRAINT ck_activity_logs_duration_positive CHECK (duration_minutes > 0) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_rewards_price_positive') THEN
    ALTER TABLE rewards ADD CONSTRAINT ck_rewards_price_positive CHECK (price > 0) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_time_sessions_duration_nonnegative') THEN
    ALTER TABLE time_sessions ADD CONSTRAINT ck_time_sessions_duration_nonnegative CHECK (duration_seconds IS NULL OR duration_seconds >= 0) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_time_sessions_valid_dates') THEN
    ALTER TABLE time_sessions ADD CONSTRAINT ck_time_sessions_valid_dates CHECK (ended_at IS NULL OR ended_at >= started_at) NOT VALID;
  END IF;
END $$;
