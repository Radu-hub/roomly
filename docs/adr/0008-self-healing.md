# ADR 8: Self-healing

**Context.** The server should be disposable: if it's destroyed, the site should come back at the same URL with nobody touching anything.

**Options considered.**
- **Auto Scaling Group of 1 + Elastic IP re-association:** a dead instance is replaced from the launch template, across two AZs, at no extra cost.
- **Single instance + CloudWatch auto-recover:** simple, but only handles certain hardware failures, and can't move to another AZ or replace a terminated instance.
- **ALB + multiple instances:** real high availability with no downtime, but it costs more than the Free Tier allows.

**Decision.** ASG with min = max = desired = 1. All state lives in RDS, SSM and ECR. A new instance's user data installs Docker, claims the Elastic IP, reads the last good image tag from SSM and runs the normal `deploy.sh`.

**Tradeoffs accepted.** This is recovery, not high availability: expect a few minutes of downtime during replacement. `health_check_type = "EC2"` only notices instance failures, not a hung app, which is why the external probe exists. It alerts a human but doesn't replace the instance automatically.
