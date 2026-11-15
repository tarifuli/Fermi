# Build 15 — POS Discounts and Payment Method Recording

## Delivered
- Fixed-amount discount entry on POS; the browser previews subtotal, discount, 5% tax on the discounted amount, and total.
- Server-side Decimal validation of discount (nonnegative, <= subtotal, at most two decimal places) and payment method allowlist.
- Allowed payment methods: unspecified, cash, card, mobile_banking, bank_transfer, other.
- Invoice persists discount and payment method. Existing invoice line items retain their original undiscounted price and total; invoice-level discount is applied to the aggregate.
- Invoice HTML/print view and PDF show discount and payment method.
- Checkout idempotency continues to hash the request payload, including payment and discount fields.
- Regression test for valid discounted checkout, invalid discounts, tax calculation and invalid payment method.

## Required DEVELOPMENT database migration
Apply `migrations/postgresql/011_pos_discounts_payment_methods.sql` to the development Neon database **before testing checkout**. SQLite development/test databases add the columns through `init_db()`.
Do not apply this migration to production until explicitly approved.

## Accounting and operational boundaries
- Tax is the existing fixed 5% rate, calculated after discount; this build does not add configurable tax jurisdictions.
- Discounts reduce the final invoice total, which existing revenue/refund reports use.
- No real card, bank or mobile payment is processed or verified; payment method is a recorded label only.
- Existing stock deduction, duplicate checkout protection, and returns/refunds flows remain in place.

## Verification gates
- Final security/regression and PostgreSQL integration workflows green.
- Development migration 011 applied.
- Authenticated preview checkout tests: full discount, partial discount, invalid discount, cash/card/mobile method, invoice view/PDF, duplicate checkout, refund, and stock changes.
- Production unchanged pending approval.
