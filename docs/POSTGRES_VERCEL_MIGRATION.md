# Fermi PostgreSQL / Vercel migration checklist

## Current state
The existing Flask app uses SQLite directly (`sqlite3.connect`, SQLite `?` parameters, `PRAGMA table_info`, `last_insert_rowid()`, and SQLite-specific schema). **It is not safe to set a PostgreSQL URL and deploy unchanged.** This branch contains initial Vercel deployment scaffolding, not a completed database migration.

## Required code work before production
1. Replace SQLite access with a PostgreSQL-capable database layer, preferably SQLAlchemy, and convert all SQL statements, schema creation, inserts and transactions.
2. Create versioned schema migrations (Alembic). Model `users`, `businesses`, `business_memberships`, `products`, `invoices`, and `invoice_items`, including tenant-aware indexes and foreign keys.
3. Use `DATABASE_URL` only on the server, with TLS to a managed PostgreSQL provider. Never commit database credentials.
4. Remove import-time `init_db()` and run migrations separately; do not seed demo data into production.
5. Require a stable `SECRET_KEY`, upgrade legacy SHA-256 password hashes, and implement CSRF protection.
6. Verify tenant isolation, manual invoice items, invoice totals, stock updates, and transactional rollback against an actual PostgreSQL instance.
7. If the current Render SQLite database contains real data, export and validate it, migrate it separately, and verify row counts before switching traffic. Never assume the local SQLite file is available on Vercel.
8. Configure the Vercel project with server-only `DATABASE_URL`, `SECRET_KEY`, and optional `ANTHROPIC_API_KEY`; run staging smoke tests and check logs before cutover.

## Vercel
The root `vercel.json` is an initial Flask function routing configuration. It is **not** a production-ready deployment until the above blockers are resolved. Keep Render running until data migration and smoke tests succeed.
