# Build 14 — Dashboard and Actionable Notifications

## Delivered
- Dashboard action center showing live out-of-stock, low-stock, pending invoice, and purchase order receipt alerts.
- Clear links to the replenishment planner, inventory, invoices, and purchase orders.
- Authenticated `GET /notifications` JSON endpoint with business-scoped, no-store alerts.
- Configured low-stock threshold respected; out-of-stock and low-stock are separate categories.
- Dashboard top-product sales rankings now count paid invoices only.
- Automated checks for live changes, tenant isolation, and dashboard display.

## Behavior and limitations
- Alerts are recalculated from current business records at request time; there is no persistent inbox, dismissal, notification delivery, email, push, or scheduled background task.
- The action center does not mutate stock or invoice state.
- An alert's product detail previews at most three names.
- This build does not introduce a new schema migration.

## Release gates
- Both GitHub Actions workflows must pass at final commit.
- Development Vercel preview must be READY.
- Authenticated live acceptance should confirm navigation, stock threshold changes, pending invoice alerts, PO status alerts, and cross-business isolation.
- Production stays unchanged until explicitly approved.
