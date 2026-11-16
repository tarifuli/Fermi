# Build 17 — Final Security, Performance and Release Verification

## Implemented in this build
- Dashboard daily sales and refunds are aggregated into two database queries instead of fourteen daily queries.
- PostgreSQL DATE values are normalized to date strings to match SQLite and preserve chart labels.
- Inventory alerts use database counts and at most three product-name rows per alert type instead of loading the entire product inventory into Python.
- Regression coverage checks paid-only daily revenue, pending invoice exclusion, stock alerts, and cross-business isolation.

## Existing automated security and workflow coverage
The repository has tests for CSRF, login limits, password security, tenant isolation, POS, duplicate checkout, purchase orders, returns/refunds, onboarding, dashboard reporting, PostgreSQL integration and role permissions. Their presence does not substitute for authenticated live acceptance.

## Mandatory release gates
1. Latest commit passes **Fermi security and regression tests**.
2. Latest commit passes **PostgreSQL integration**.
3. Vercel development preview reports READY.
4. Authenticated browser acceptance: owner/staff/viewer access, team membership changes, settings and password change; inventory alerts and replenishment; checkout with discounts and recorded payment methods; invoice view/PDF; duplicate checkout; returns/refunds; purchase orders; cross-business isolation.
5. Confirm development PostgreSQL migration 011 was applied (user reported complete). Verify migration state on actual target before production deployment.
6. Production environment review: SECRET_KEY set and stable; HTTPS-only cookies; DATABASE_URL points to intended production Neon database; database backup/restore procedure documented and tested; secrets never logged; no debug mode; database migrations applied and backed up.
7. Review error logs and response latency under realistic workloads. Load/performance targets are not established; do not claim load-test success.
8. Obtain explicit production deployment approval. Production must not be changed as part of this build.

## Known limitations / security considerations
- Authenticated live browser acceptance is not yet available from GitHub CI or Vercel readiness checks.
- A password change signs out the current session only; older sessions on other devices are not invalidated.
- A user belonging to multiple businesses is currently assigned the first membership; no business switcher.
- Viewers have read-only access but can see business data. Only grant access to trusted users.
- Payment methods are bookkeeping labels, not payment processing.
- Fixed 5% tax remains hard-coded and may not match the user's jurisdiction.
- Do not claim an independent penetration test or production-grade load test was conducted.

## Release decision
Do not mark production-verified until all release gates are complete. Production stays unchanged.
