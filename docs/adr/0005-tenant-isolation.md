# ADR 5: Tenant isolation

**Context.** Many organizations share one deployment. One forgotten `WHERE org_id = ...` must not expose another customer's data.

**Options considered.**
- **Row-Level Security in the database + role checks in the app:** one schema, and the database filters every query by tenant.
- **Filtering only in application code:** simplest, but one missed filter is a data breach, and a test can't prove that no query forgot it.
- **Schema per tenant:** strong isolation, but every migration runs once per tenant, and it's easy to get the search path wrong.
- **Database per tenant:** the strongest isolation, but it's expensive and heavy to operate for small tenants.

**Decision.** RLS keyed on a per-transaction setting (`set_config('app.org_id', ..., true)`), with composite foreign keys so that a booking can't reference another tenant's room or user. The app connects as `roomly_app`, which doesn't own the tables, because owners bypass RLS. The split is deliberate: **which tenant** is the database's job, and **what you may do inside it** (employee or admin, your own booking) is the app's.

**Tradeoffs accepted.** Every query must run inside `tenant_tx`. A few cross-tenant needs (resolving an org slug at login, health counts) go through narrow `SECURITY DEFINER` functions. The classic failure, connecting as the owner, would silently disable RLS. So the app refuses to start in that state, the connection pool rejects any such connection, and `/health` reports the result.
