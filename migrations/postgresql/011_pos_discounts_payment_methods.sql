-- Build 15: apply once to the DEVELOPMENT PostgreSQL database before testing.
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS discount NUMERIC NOT NULL DEFAULT 0;
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS payment_method TEXT NOT NULL DEFAULT 'unspecified';
