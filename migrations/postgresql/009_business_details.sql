-- Build 10: business-owned onboarding profile fields.
CREATE TABLE IF NOT EXISTS business_details (
 business_id BIGINT PRIMARY KEY REFERENCES businesses(id) ON DELETE CASCADE,
 contact_email TEXT NOT NULL DEFAULT '',
 contact_phone TEXT NOT NULL DEFAULT '',
 address TEXT NOT NULL DEFAULT '',
 updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
