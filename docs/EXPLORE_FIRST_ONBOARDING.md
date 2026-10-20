# Explore-first onboarding specification

## Product decision
A newly registered Fermi user lands on the dashboard immediately. Creating a business profile is **optional at signup** and available afterward through a clear "Set up your business" action.

## User states
1. **New user without business:** Show a guided demo dashboard with clearly labeled sample metrics and data. The demo must be read-only or use a disposable per-user sandbox; never expose or modify real customers' records. Real invoice creation, inventory writes, POS checkout, and AI access to private business data require a business.
2. **User with business:** Open their own business workspace. All business-specific queries must be scoped by membership and business ID.
3. **User with multiple memberships (future):** Show a business switcher; changing business must update the authorized context.

## First-session UX
- Signup -> dashboard, not forced onboarding.
- Show a prominent but dismissible "Set up your business" card with a short explanation of what it unlocks.
- Provide an "Explore demo" affordance and clear demo badges on sample data.
- Keep navigation simple: Dashboard, Products, Invoices, Customers, Expenses, POS, Reports, AI.
- Business creation form starts with business name; industry, phone, address, logo, and language can be added later.
- Upon creation, route the user to an empty real-business dashboard with guided next actions. Do **not** copy demo transactions into the real business.

## Backend and data safety requirements before launch
- Create businesses and business_memberships; authorize each request using authenticated user membership, not a user-controlled business ID alone.
- Add business_id to products, invoices, customers, expenses, and POS transactions with indexes and appropriate constraints.
- Explicitly migrate existing unowned demo records; never automatically assign shared records to the first signup.
- Enforce tenant scope on reads, writes, PDFs, dashboards, exports, and AI context.
- Add automated cross-tenant isolation tests and unauthenticated-access tests.
- Maintain backwards-compatible login while migrating password storage.
- Use persistent production database storage and stable SECRET_KEY before deploying to real users.

## Feature sequence
1. Tenant isolation and explore-first onboarding.
2. Customer management with purchase history.
3. Expense tracking and profit/loss reports.
4. POS checkout with atomic stock and invoice updates.
5. Exportable reports.
6. Advanced AI assistant with tenant-scoped data and usage controls.

## Acceptance tests
- Signup -> demo dashboard without business creation.
- Demo data is explicitly labeled and cannot alter production records.
- Creating a business switches to its empty, private workspace.
- Two separate accounts cannot view, modify, export, or query each other's business data.
- Login survives application restart and deployment with configured persistent storage.
