-- PostgreSQL migration: integrity indexes and guarded constraints introduced in tahap 7.
-- NOT VALID keeps legacy rows from blocking startup, while still enforcing new/updated rows.

CREATE INDEX IF NOT EXISTS ix_transactions_wallet_transaction_date
  ON transactions(wallet_id, transaction_date);

CREATE INDEX IF NOT EXISTS ix_bill_reminders_user_due_date
  ON bill_reminders(user_id, due_date);

CREATE INDEX IF NOT EXISTS ix_bill_reminders_paid_transaction_id
  ON bill_reminders(paid_transaction_id);

CREATE INDEX IF NOT EXISTS ix_budgets_user_category_period
  ON budgets(user_id, category, period);

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
