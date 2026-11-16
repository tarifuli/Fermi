# Fermi Preview and production database release gates

This checklist protects live customer and financial data. Do not use a production
`DATABASE_URL` in Preview, CI, or local development, including for read-only
acceptance tests that might unexpectedly write through startup hooks.

## Evidence from 2026-11-16 inspection
- The Vercel project `fermi` has a Preview-only `SECRET_KEY` and a
  Production-only `DATABASE_URL` listed. These settings do **not**
  establish a usable Preview PostgreSQL connection.
- The default `main` code is an older SQLite-backed application; the
  latest PostgreSQL-aware application is on
  `feature/postgres-vercel-migration`. Do not merge without approval.
- Feature-branch GitHub Actions PostgreSQL integration successfully
  applies migrations 001–011 to its **isolated ephemeral CI PostgreSQL**
  service. It does not inspect or migrate Neon production.

## Database verification (Neon SQL editor, read-only)
Run against the **production** branch and copy only table/column names,
never connection strings, record contents, or credentials.

```sql
SELECT current_database() AS database_name, current_schema() AS schema_name;
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
ORDER BY table_name;
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'invoices'
  AND column_name IN ('discount','payment_method')
ORDER BY column_name;
SELECT indexname
FROM pg_indexes
WHERE schemaname = 'public'
ORDER BY indexname;
```

Migration 011 is present only if `invoices.discount` and
`invoices.payment_method` exist with the expected definitions, and the
migration is applied to the correct database. There is no version ledger in
these plain SQL scripts; inspecting the schema is necessary. Do not execute
ALTER, INSERT, UPDATE, DELETE, or migration files on production without a
separate approval and a verified restorable backup.

## Vercel configuration prerequisites
1. In Neon, identify the production and development database branches and
   verify that their connection targets are different. Prefer a separate
   Preview database with separate credentials and least-privilege access.
2. In Vercel, configure `DATABASE_URL` as a server-only **Preview**
   Secret pointing exclusively to the development Neon branch. Never copy
   the Production `DATABASE_URL` to Preview.
3. Configure a stable, sufficiently random `SECRET_KEY` as **Production**
   Secret. Keep the Preview secret distinct from Production.
4. Keep Production `DATABASE_URL` scoped to Production. Never expose
   these secrets through `NEXT_PUBLIC_`, repository files, logs, or PRs.
5. The optional `ANTHROPIC_API_KEY` is only needed to test Claude chat.
   Gemini integration requires separate code and credentials; do not
   assume the existing chat is Gemini.
6. Vercel environment changes affect new deployments: trigger only a
   Preview redeployment after isolation is independently verified.
7. Verify the deployment branch/commit is the intended reviewed Preview
   revision; Production must remain unchanged.

## Preview acceptance checklist
Use a disposable test account and test business **only on the isolated
development database**, and remove test records when safe.

- [ ] GET /login and /signup work without server errors
- [ ] Signup, login, logout and repeated login work
- [ ] Explore-first onboarding, business profile and team roles work
- [ ] Owner/staff/viewer authorization and tenant separation hold
- [ ] Products, stock changes and low-stock notifications work
- [ ] Suppliers, purchase orders and receiving work
- [ ] POS checkout, discount, payment method and duplicate-key retry work
- [ ] Invoice view, PDF and history totals agree
- [ ] Returns/refunds correct inventory and sales reporting
- [ ] Reports and Fermi AI responses use correct scoped business data
- [ ] CSRF, cookies, rate limits and invalid inputs behave safely
- [ ] Preview runtime logs have no unexpected 500 errors
- [ ] All GitHub Actions checks pass on the **exact** candidate SHA

A `READY` deployment and HTTP 200 login page are insufficient evidence
for authenticated acceptance. Preserve a no-go state until all checks
are independently verified. No automatic merge or Production deployment.
