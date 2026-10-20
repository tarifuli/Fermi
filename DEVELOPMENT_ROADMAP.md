# Fermi development roadmap

## Priority 0: Reliable login and persistent data
- Configure a stable `SECRET_KEY` in Render. Rotating it logs users out, but does not delete their accounts.
- Configure `DATABASE_PATH` to a **persistent disk path**, or migrate to managed PostgreSQL before accepting real customers. Render instances with ephemeral filesystems can lose `fermi.db` across deployments/restarts.
- Never copy a development database over a production database. Back up existing records before migration.
- Login now verifies both legacy SHA-256 hashes and secure Werkzeug hashes. Legacy hashes are upgraded on successful login.
- Run `python -m unittest discover -s tests` locally and verify signup → logout → login across a full service restart.

## Priority 0: Tenant isolation (before public launch)
- Add businesses and memberships tables; link all products, invoices, invoice items, customers, expenses, and AI queries to a business.
- Enforce authorization and business filters on every route and SQL query, including downloads.
- Add CSRF protection, input validation, rate limiting, secure session cookie configuration, and security regression tests.
- Validate invoice totals and inventory changes server-side inside one transaction.
- Plan a reversible data migration and an explicit decision for existing unowned demo records.

## Priority 1: Core operations
1. Customer profiles, purchase history, and customer search.
2. Expenses and profit/loss reporting, with a documented cost-of-goods methodology.
3. POS checkout using the same transaction-safe inventory/invoice engine.
4. Exportable CSV/PDF business reports.
5. Advanced AI assistant scoped to the current business, with clear data permissions and safe error handling.

## Priority 2: Ease of use and growth
- Guided shop onboarding, Bangla/English UX, mobile-first screens, accessible navigation.
- Role-based staff access, subscriptions, billing, backups, monitoring, and performance tests.

**Launch gate:** No real multi-business customers until isolation, persistence, security tests, and backup/restore procedures are verified.
