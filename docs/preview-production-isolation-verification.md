# Fermi database isolation and release verification — 2026-11-16

## Guardrails
Do not change production data, copy Production secrets to Preview, merge into `main`, or deploy Production without explicit owner approval. GitHub/CI temporary databases and Neon's development branch may be used for disposable acceptance tests.

## Verified Neon state (direct read-only inspection)
- Neon project: `fermi-preview` (`square-surf-62430524`); the project name is not evidence of the branch target.
- Production branch: `production` / `br-floral-thunder-b5t2gsvz`, default branch, endpoint `ep-old-waterfall-b595jgth`.
- Development branch: `development` / `br-soft-forest-b5utb5uy`, separate endpoint `ep-quiet-resonance-b5ctc044`.
- Both branches contain 20 application tables. The branch-to-branch PostgreSQL schema comparison returned an empty diff.
- Migration 011's intended schema is **present in both branches**: `invoices.discount NUMERIC NOT NULL DEFAULT 0` and `invoices.payment_method TEXT NOT NULL DEFAULT 'unspecified'`.
- Read-only row-count check: Production has 2 users, 2 businesses, 4 products, 17 invoices; development has zero rows in each of those tables. No customer contents, passwords or secret values were retrieved.
- Historical execution of migration scripts is not independently recorded by a migration ledger, so this confirms schema-equivalent application of 011, not the historical execution event. No production writes were made.
- No Neon snapshots were listed. Backups/restore readiness remains unverified.

## Verified Vercel state
- Vercel project `fermi`, ID `prj_VFTEglqd6TQPx4bJSP5MGHoUyAM3`.
- `DATABASE_URL` and `SECRET_KEY` now exist as **Preview-only sensitive variables**, created 2026-11-16. The Preview URL was obtained for the exact Neon development branch, and the Preview signing secret is independent of Production.
- Existing `DATABASE_URL` and `SECRET_KEY` remain **Production-only sensitive variables** and were not changed. Their contents remain encrypted in Vercel's read API; therefore, a direct comparison of the Production `DATABASE_URL` hostname against Neon's production endpoint has **not** been completed.
- A new **Preview-only** deployment of `feature/postgres-vercel-migration` commit `8456d9c3a834f94f6487baa18534bedd9be0b056` reached `READY`: https://fermi-q48yrtko2-fermi3.vercel.app
- Live GET smoke tests returned HTTP 200 for `/login` and `/signup`; anonymous requests to `/`, `/products`, `/pos`, and `/invoices` redirected to the login page. This confirms only basic rendering and anonymous access control.
- The current **Production** deployment from `main` (`7a5f59858f00cdfc45bf97f994d4549754f396da`) returned HTTP 500 on `/` and `/login`. Vercel runtime traceback confirmed `sqlite3.OperationalError: unable to open database file` during `app.py` import. Production was **not** changed.
- An isolated, ephemeral Vercel sandbox successfully opened a protected Preview login page. The transactional test automation was blocked by a tool safety check; the sandbox was subsequently stopped. Do not treat this as authenticated acceptance.

## CI
- PR #5 was merged into `feature/postgres-vercel-migration`, **not** `main`.
- GitHub Actions checks for the feature-branch commit passed, including PostgreSQL integration with migrations 001–011 and regression/security tests:
  - https://github.com/tarifuli/Fermi/actions/runs/43868586360
  - https://github.com/tarifuli/Fermi/actions/runs/43868586308
- CI runs against an isolated temporary PostgreSQL service, not live Neon Production.

## Remaining mandatory release gates
1. Verify the actual Production deployment connection points to Neon `production` (not `development`), using a secret-safe comparison by authorized infrastructure tooling. Do not print credentials.
2. Complete authenticated **live Preview** tests on the isolated development branch with a disposable user and business: signup/login/logout, optional onboarding, owner/staff/viewer roles, tenant isolation, products/stock/alerts, suppliers/PO receiving, POS checkout with discount/payment method, duplicate checkout replay, invoice view/PDF, returns/refunds, analytics/reports, and CSRF/rate limits.
3. Check Preview runtime logs after the actual authenticated tests, and reconcile expected stock, invoice, refund, and ledger records. Do not claim success from HTTP 200 login screens alone.
4. Prove that Production has a restorable backup/restore plan and establish load/performance targets.
5. Prepare/review a full release PR from the PostgreSQL feature branch to `main`, verify CI for the exact candidate commit, and obtain **explicit user approval** before any merge or Production deploy. Changing `main` may trigger automatic Production deployment.

**Release decision: NO-GO.** Production is currently unavailable on the old SQLite code; verified Preview smoke tests do not justify a release.
