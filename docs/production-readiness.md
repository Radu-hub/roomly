# Before real customers

What I'd change before real customers used this. Costs are rough monthly estimates in us-west-2 at low traffic.

| Area | Today | Before customers | Rough monthly cost |
|------|-------|------------------|-------------------|
| **Availability** | One instance; replacement takes minutes | ALB + Auto Scaling Group of at least 2 across AZs | ALB ~$20 + instances ~$15 |
| **Database** | Single-AZ, 1-day backups, no deletion protection | Multi-AZ, point-in-time recovery, deletion protection, regularly tested restores | ~$30+ (roughly double) |
| **Edge** | Caddy on the instance | ALB + ACM certificates + WAF + CloudFront | WAF ~$10 + traffic |
| **Domain** | sslip.io | A real domain on Route 53 | ~$1.50 |
| **Secrets** | Parameter Store, manual rotation, one role per instance | Secrets Manager rotation, separate roles per service | ~$0.40 per secret |
| **Environments** | Production only, one account | Staging + production in separate AWS accounts | Roughly doubles |
| **Deploys** | In-place, seconds of downtime | Blue/green or ECS rolling deploys with no downtime | Included with ALB |
| **Observability** | Logs, basic alarms, one email address | Tracing, SLOs, error tracking, on-call paging | ~$20+ |
| **Security** | Least-privilege IAM, no SSH, encryption | GuardDuty, Config, Security Hub, an organization CloudTrail, image scanning that blocks deploys, a non-root Caddy | ~$10 to $30 |
| **Sessions** | Signed cookies, which can't be revoked before they expire | Server-side sessions so that logout and "disable user" take effect at once | Small |
| **Compliance and backups** | None beyond RDS defaults | Audit logs, cross-region backup copies, a documented restore time | Varies |
| **Auth** | Passwords, demo logins on | SSO/SAML, MFA, password reset, demo mode off | Varies |
| **Rate limiting** | None | Limits on login and booking, at the WAF and in the app | Included with WAF |
| **Data retention** | Bookings kept forever | A retention policy, and data export and deletion per tenant | Small |
| **Product (out of scope)** | - | Recurring bookings, calendar sync, email invites, self-service sign-up, i18n | - |

## If I could only do three

1. **ALB + 2 instances across AZs**, because a single instance is the most likely cause of an outage.
2. **Multi-AZ RDS with tested restores**, because lost data is the one failure that can't be fixed later.
3. **A staging environment**, because right now the first real test of any change is production.
