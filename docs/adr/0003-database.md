# ADR 3: Database

**Context.** The two strongest guarantees of this app, no double bookings and tenant isolation, should hold even if application code has a bug. That means the database itself has to enforce them.

**Options considered.**
- **RDS PostgreSQL:** has the exclusion constraints and Row-Level Security this design relies on. AWS handles backups and patching.
- **DynamoDB:** free forever at this scale and has no server, but has no range-overlap constraint and no row-level policies. Both guarantees would become application code, which is what we're trying not to trust.
- **Postgres in Docker on the instance:** has the same features and no RDS bill, but the data would die with the instance, which defeats self-healing.
- **SQLite:** simple, but it's a file on the instance with the same problem, and it has no roles or RLS.

**Decision.** RDS PostgreSQL 16, `db.t4g.micro`, single-AZ, in private subnets.

**Tradeoffs accepted.** Single-AZ means an AZ failure takes the database down, and backup retention is only 1 day. `deletion_protection = false` and `skip_final_snapshot = true` let `terraform destroy` leave nothing billing, which is right for an assignment and wrong for production.
