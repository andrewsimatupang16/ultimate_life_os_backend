CREATE TABLE IF NOT EXISTS finance_events (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  wallet_id UUID REFERENCES wallets(id) ON DELETE SET NULL,
  transaction_id UUID REFERENCES transactions(id) ON DELETE SET NULL,
  bill_id UUID REFERENCES bill_reminders(id) ON DELETE SET NULL,
  event_type VARCHAR NOT NULL,
  amount_delta DOUBLE PRECISION NOT NULL DEFAULT 0,
  balance_after DOUBLE PRECISION,
  description VARCHAR,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_finance_events_user_created_at
  ON finance_events(user_id, created_at);

CREATE INDEX IF NOT EXISTS ix_finance_events_wallet_created_at
  ON finance_events(wallet_id, created_at);
