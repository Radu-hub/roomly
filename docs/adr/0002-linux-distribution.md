# ADR 2: Linux distribution

**Context.** The instance only hosts Docker. It needs to boot unattended and work with SSM from the first second, because there is no SSH.

**Options considered.**
- **Amazon Linux 2023:** the SSM agent and AWS CLI are preinstalled, it's tuned for EC2, and AWS publishes the latest AMI ID as a public SSM parameter.
- **Ubuntu 24.04:** more familiar and a larger package ecosystem, but with more moving parts for no benefit to a host that only runs containers.
- **Debian:** minimal and stable, but the SSM agent and CLI have to be installed by hand, and that install can fail at boot with no shell to debug it.

**Decision.** Amazon Linux 2023. Everything the app depends on is in the container, so the host only needs to be boring and well integrated with AWS.

**Tradeoffs accepted.** AL2023 doesn't package the Docker Compose plugin, so user data downloads a pinned release and verifies its checksum. It's also less familiar than Ubuntu, and any commands are AWS-specific (`dnf`, not `apt`).
