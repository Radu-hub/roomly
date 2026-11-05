# ADR 1: EC2 instance type and architecture

**Context.** The app runs a few small containers. It has to stay inside the Free Tier and be fast enough for a demo.

**Options considered.**
- **t4g.micro (Graviton, arm64):** 2 vCPUs, 1 GB RAM, better price-performance, and free-tier eligible on accounts on the newer free plan.
- **t3.micro (x86):** 2 vCPUs, 1 GB RAM, and free-tier eligible on both older and newer accounts.
- **t2.micro (x86):** an older generation with 1 vCPU, and no benefit over t3.

**Decision.** t4g.micro, with `instance_type` and `instance_arch` as Terraform variables so that moving to t3.micro takes one line if an account needs it. Images build on GitHub's native arm64 runner, so no emulation is involved.

**Tradeoffs accepted.** Images must be built for arm64. Free-tier eligibility depends on the account's age and plan, so it has to be checked for each account. 1 GB of RAM leaves little headroom, which is fine at this traffic and would not be for real customers.
