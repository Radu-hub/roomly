# ADR 4: Traffic path and TLS

**Context.** Browsers need HTTPS at a stable address, on one small instance, without spending money.

**Options considered.**
- **Caddy on the instance + Elastic IP:** free certificates that renew themselves, and very little configuration.
- **ALB + ACM:** the standard production answer, with health checks and multi-AZ. But it costs about $16 a month or more, needs a real domain for ACM, and would front exactly one instance.
- **CloudFront:** free tier and global edge, but it adds a caching layer that a dynamic, per-user app gets little from, and more to explain.

**Decision.** Caddy + Elastic IP. The hostname is `roomly.<ip-with-dashes>.sslip.io`, a free DNS service that maps the name to the IP, unless a real domain is configured.

**Tradeoffs accepted.** TLS terminates on the instance, so there's a brief outage during replacement. Each replacement instance requests a new certificate, because certificates live on the instance, so repeated replacement can hit Let's Encrypt rate limits (the staging CA is a variable for testing). The site depends on sslip.io for DNS. Public IPv4 addresses are billed hourly outside the free allowance. An ALB is the first thing to add before real customers.
