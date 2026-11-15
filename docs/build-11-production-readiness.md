# Build 11 — Production readiness and release checklist

Status: development hardening implemented; **not production approved**.

## Verified automatically
- Security/regression suite: GitHub Actions `Fermi security and regression tests`.
- Database integration: GitHub Actions `PostgreSQL integration`.
- Deployment: verify the same commit SHA is READY in the Vercel preview environment.

## Required before production release
1. Confirm the Vercel production environment has a strong unique `SECRET_KEY`, correct production `DATABASE_URL`, and the Anthropic API key if AI chat is enabled. Never print or commit values.
2. Take and test a restorable PostgreSQL backup (including schema and data) before migrations or promotion. Record restore instructions and responsible operator. Do not assume Neon branch creation equals a tested backup.
3. Test an isolated preview account and business: signup, login/logout, onboarding, business settings, product creation, stock threshold and adjustment, POS checkout, duplicate checkout protection, invoice view/PDF, purchase orders, returns/refunds, and business-to-business isolation.
4. Confirm no errors in Vercel runtime logs for the tested deployment and that all relevant GitHub Actions checks pass on the release commit.
5. Check browser network behavior for `Secure`, `HttpOnly`, and `SameSite=Lax` session cookie settings on HTTPS; confirm no authenticated pages remain accessible from browser cache after logout.
6. Obtain explicit owner approval before merging or deploying to production. Never run migrations on the production database without a separate review.

## Known residual risks
- Cross-site write checks based on Origin/Fetch Metadata are **defense in depth**, not complete CSRF protection. Implement token-based CSRF protection across HTML forms and JSON endpoints before a public production launch.
- Login rate limiting/account abuse protection is not yet implemented. A shared persistent store or trusted edge protection is needed; in-memory limits are unreliable on serverless deployments.
- Flask's signed client-side sessions do not provide immediate global session revocation. For stricter account security, add server-side session tracking and password-reset/revocation controls.
- No evidence here of completed penetration testing, disaster recovery rehearsal, or production load testing.

## Rollback
1. Stop promoting new deployments; keep the previous known-good production deployment ID.
2. Use Vercel's deployment rollback/promotion controls to restore the prior deployment, after confirming its configuration is compatible with the current database.
3. Do not automatically roll back database schema; restore data only from a tested backup under a separately approved recovery plan.
4. Re-run smoke tests and inspect runtime logs after rollback.
