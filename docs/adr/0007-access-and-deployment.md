# ADR 7: Access and deployment

**Context.** Deploys need to reach the instance, and so do humans occasionally. Open ports and long-lived keys are the two things most likely to be stolen.

**Options considered.**
- **SSM Run Command + GitHub OIDC:** no open port and no stored keys. GitHub gets short-lived credentials, only from `main` of this repository.
- **SSH + stored keys:** familiar, but needs port 22 open and a private key sitting in GitHub secrets, which never expires and has to be rotated by hand.
- **CodeDeploy:** a managed version of the same idea, with more configuration and agent setup, without adding much for one instance.
- **ECS:** a good production direction, but on EC2 it adds a layer to explain, and Fargate isn't free. The assignment also requires an EC2 instance.

**Decision.** SSM Run Command to run `deploy.sh`, and OIDC for AWS credentials. Terraform runs from the author's machine, not from CI, so the CI role only needs to push images and run one document on instances tagged `App=roomly`. It can't change infrastructure.

**Tradeoffs accepted.** Deploys are in-place, so there's a few seconds of downtime while containers restart. Rollback restores the app image, not the schema, so migrations have to stay backward compatible (expand, deploy, contract). Terraform changes depend on one person's machine and credentials.
