# ADR 6: Secrets

**Context.** The app needs database passwords and a session key. None of these can live in the repository, and the environment should stay within the free tier.

**Options considered.**
- **SSM Parameter Store (SecureString):** encrypted with KMS, access controlled by IAM, and free on the standard tier.
- **Secrets Manager:** adds automatic rotation, but costs about $0.40 per secret per month, which adds up to about $2 for five secrets.
- **Env files on the instance:** simple, but secrets sit on disk, a new instance has to get them from somewhere, and they tend to end up in git.

**Decision.** Parameter Store. The instance role reads parameters at startup, and each process fetches only the ones it needs by name, so nothing secret is written to disk or to a compose file. Terraform generates the values with `random_password`.

**Tradeoffs accepted.** No automatic rotation (the steps are in the runbook). The generated values exist in Terraform state, so the state bucket is private, encrypted and versioned. All containers on the instance share one IAM role, so a compromised container could request the owner password even though the web app never does. Per-container roles (ECS task roles) would close that gap.
