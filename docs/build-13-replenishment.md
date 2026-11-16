# Build 13 — Inventory Replenishment & Stock Planning

## Implemented
- Read-only authenticated endpoint: `GET /inventory/replenishment?days=7|30|90`.
- Per-business product sales from paid invoice line items, 14-day planning target, minimum stock floor from configured threshold, suggested order quantities, estimated stock coverage and priority.
- Interactive page: `/inventory/replenishment/plan` with sales-history selector, recommendations, editable quantities and required unit purchase costs.
- Supplier dropdown restricted to the authenticated business.
- User-confirmed creation of **draft** purchase orders using the existing validated `POST /purchase-orders` workflow. Draft creation does not receive stock, mark an order as placed, or contact suppliers.
- Link from Inventory to planner.
- Tests for recommendations, business isolation, read-only behavior, and planner navigation.

## Formula and limitations
- Daily observed demand = units on paid invoice lines in chosen lookback / lookback days.
- Target stock = max(low-stock threshold + 1, ceil(daily observed demand × 14)).
- Suggested quantity = max(0, target stock − current stock).
- Stock coverage = current stock / daily observed demand, or unknown when no paid sales.
- No lead times, seasonality, returns, outstanding purchase-order receipts or supplier-specific product mapping are incorporated.
- Recommendations are advisory; purchase costs must be entered manually and users must confirm the draft.
- Existing supplier, PO, receiving, inventory and refund workflows remain unchanged.

## Acceptance gates
- GitHub Actions security/regression and PostgreSQL integration pass at the final Build 13 SHA.
- Vercel preview reaches READY.
- Manual authenticated browser acceptance: confirm recommendations, draft creation, no automatic stock changes, supplier isolation, and existing PO receipt flow.
- No production changes without explicit approval.
