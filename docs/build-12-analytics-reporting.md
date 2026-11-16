# Build 12 — Business Analytics and Reporting

## Scope implemented
- Paid sales and refund-aware net revenue charts for 7, 30, or 90 days, plus custom inclusive date ranges of up to 366 days.
- Comparison of current net paid revenue with the immediately preceding equal-length period, including refunds recorded in either period.
- Top-selling products by paid units and slow-moving stocked products by paid units in the chosen period.
- Current inventory value **at selling price**, low-stock and out-of-stock counts, using the business's configured threshold.
- Tenant-scoped CSV downloads for sales (gross, refunded, net), refunds and current inventory.
- CSV formula-injection protection for user-controlled cells; date range validation and no-store responses.
- Analytics page links to date-scoped sales/refund CSV exports and a current-inventory snapshot.

## Definitions and limitations
- Revenue is based on paid invoices; refunds are deducted on their **recorded date**, not the invoice date.
- Sales CSV lists invoices by invoice creation date, with lifetime refunds per invoice. Its row totals are therefore **not** intended to reconcile to the refund-date-based analytics period total when refunds cross reporting periods.
- Inventory valuation uses selling prices, **not acquisition cost**, and is not a profit measure.
- Product rankings count paid invoice line items and are not adjusted for returned units; refund values affect net revenue instead.
- Slow-moving rankings match products by their stored name; duplicate product names can be grouped together.
- Inventory CSV is a current snapshot, not a historical as-of-date report. It intentionally ignores date filters.
- No expense tracking, P&L, or customer management is added.

## Verification
Run both GitHub Actions workflows: Fermi security and regression tests, PostgreSQL integration.
Inspect the development Vercel preview and test the analytics page, CSV exports, date filtering, and tenant isolation with a disposable business.
**Do not deploy to production** until explicitly approved. Build 11's separate live verification remains outstanding.
