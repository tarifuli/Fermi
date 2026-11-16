# Build 16 — Business Settings, Team Roles and Account Security

## Delivered
- Owner/staff/viewer roles enforced server-side. Existing owners remain owners.
- Owner-only team management: add an already registered user by email as staff/viewer; change roles; remove members.
- Protection against deleting or demoting the last business owner.
- Staff retains operational write access but cannot manage team membership or change business settings.
- Viewer can read business pages but cannot perform POST/PUT/PATCH/DELETE business mutations, enforced in a central before-request guard.
- Existing onboarding business settings page and its validation are preserved; team management is linked from it.
- Authenticated password change requires the current password, at least 12 characters for the replacement, and clears the current session.
- New regression tests cover owner protection, role restrictions, duplicate members, unknown users, and password change.

## Routes
- `GET /business/team`
- `POST /business/team/members`
- `PATCH|DELETE /business/team/members/<user_id>`
- `POST /account/password`
- Existing `GET|POST /business/settings` remains managed by onboarding blueprint.

## Important limitations
- Users must register themselves before owners add them; this does not send invitations.
- The current app selects the first business membership for a user. A multi-business switcher is **not** included; adding users who already belong to another business will not give them a business-switching UI.
- Password changes revoke only the current session, not previously issued sessions on other devices. Global session invalidation and 2FA are not included.
- Viewer read-only access does not redact sensitive financial information; use the role only for people trusted to see business data.
- This build does not introduce new database tables or require a PostgreSQL migration.

## Acceptance gates
- Final GitHub security/regression and PostgreSQL integration checks pass.
- Vercel preview READY.
- Authenticated live test for owner, staff and viewer, last-owner protection, business settings and password change.
- No production changes without explicit approval.
