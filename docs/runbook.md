# Runbook

## First-time setup

### 1. Prerequisites
On your machine: Terraform 1.10+, the AWS CLI logged in to your own account, and the `gh` CLI or the GitHub website. Create a billing alert first, as a safety net before anything exists in the account.

Check whether `t4g.micro` is free-tier eligible for your account:

```bash
aws ec2 describe-instance-types --filters Name=free-tier-eligible,Values=true --query 'InstanceTypes[].InstanceType' --region us-west-2
```

If it isn't listed, set `instance_type = "t3.micro"` and `instance_arch = "x86_64"` in `terraform.tfvars`, and set the `RUNNER` repository variable to `ubuntu-24.04`.

### 2. Create the Terraform state bucket (once, by hand)
Bucket names are global, so include something unique.

```bash
aws s3api create-bucket --bucket <your-state-bucket> --region us-west-2 --create-bucket-configuration LocationConstraint=us-west-2
```

```bash
aws s3api put-bucket-versioning --bucket <your-state-bucket> --versioning-configuration Status=Enabled
```

```bash
aws s3api put-public-access-block --bucket <your-state-bucket> --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

### 3. Create the infrastructure
```bash
cp infra/terraform.tfvars.example infra/terraform.tfvars
```

Edit that file, then:

```bash
terraform -chdir=infra init -backend-config="bucket=<your-state-bucket>"
```

```bash
terraform -chdir=infra apply
```

RDS takes about 10 minutes. Afterwards, **click the confirmation link in the SNS email**, or alarms will go nowhere. The health alarm stays in the ALARM state until the first deploy.

While testing, consider adding `acme_ca = "https://acme-staging-v02.api.letsencrypt.org/directory"` to `terraform.tfvars`, then removing it for the final demo. See [certificate limits](#certificate-limits).

### 4. Connect GitHub
Add these under Settings → Secrets and variables → Actions. No AWS keys go in GitHub. The role ARN and ECR URL aren't credentials, but they contain the AWS account ID and this repository's logs are public, so they're stored as secrets, which GitHub masks.

| Name | Kind | Value |
|------|------|-------|
| `AWS_ROLE_ARN` | Secret | `terraform -chdir=infra output -raw github_deploy_role_arn` |
| `ECR_REPO` | Secret | `terraform -chdir=infra output -raw ecr_repository_url` |
| `AWS_REGION` | Variable | `us-west-2` |
| `SITE_URL` | Variable | `terraform -chdir=infra output -raw site_url` |
| `RUNNER` | Variable | Only for x86: `ubuntu-24.04` |

### 5. First deploy and database bootstrap
Push to `main`. CI runs, then Deploy builds and pushes the image. **The first deploy's "Run deploy.sh" step is expected to fail**, because the database has no roles or tables yet. Copy the commit SHA, then open a shell on the instance. There's no SSH: use Session Manager.

```bash
aws ssm start-session --region us-west-2 --target "$(aws ec2 describe-instances --region us-west-2 --filters Name=tag:App,Values=roomly Name=instance-state-name,Values=running --query 'Reservations[0].Instances[0].InstanceId' --output text)"
```

On the instance:

```bash
sudo /opt/roomly/bootstrap-db.sh <commit-sha>
```

That creates the roles and database, migrates, seeds and deploys. Now re-run the failed GitHub Actions job so that it records the image tag in SSM, because replacement instances depend on it.

## Everyday operations

### Deploy
Push to `main`. To redeploy a version by hand, on the instance:

```bash
sudo /opt/roomly/deploy.sh <commit-sha>
```

`deploy.sh` and the compose file are copied to the instance at boot. To ship changes to them, run `terraform apply`, which replaces the instance.

### Roll back
`deploy.sh` does this automatically when `/health` fails. To go back to an older version on purpose, re-run its Deploy workflow, or run `deploy.sh <older-sha>`, then update the parameter so that replacement instances agree:

```bash
aws ssm put-parameter --name /roomly/prod/image_tag --value <older-sha> --overwrite --region us-west-2
```

Rollback restores the app, not the schema. That's why migrations must stay backward compatible with the previous version.

### Prove the automatic rollback
Change the last line of `app/Dockerfile` to `CMD ["python", "-c", "raise SystemExit(1)"]` and push. Tests and the image build pass, since nothing runs that command in CI, but the new container never becomes healthy. Within about 60 seconds the Deploy job fails with "rolling back" in its log, and the site keeps serving the old commit. Revert the commit afterwards.

### Prove self-healing
Note the instance id in the site's footer, then terminate it:

```bash
aws ec2 terminate-instances --region us-west-2 --instance-ids <instance-id>
```

A few minutes later the same URL serves traffic with a new instance id in the footer, and your bookings are still there.

### Rotate secrets
1. App password: generate a new value, run `ALTER ROLE roomly_app PASSWORD '...'` as `roomly_owner` or master, update `/roomly/prod/db/app_password`, then restart with `docker compose -p roomly -f /opt/roomly/compose.prod.yml restart app worker` (with `TAG` set).
2. Session secret: update `/roomly/prod/session_secret` and restart. Everyone is logged out.
3. Terraform's `random_password` values won't change unless you `terraform apply -replace`, and it will overwrite hand-edited SSM values only if their Terraform-side value changes.

### Tear down
```bash
terraform -chdir=infra destroy
```

Then delete by hand what Terraform doesn't own: the state bucket (empty all versions first), and any billing alert you made in step 1. Check Cost Explorer filtered on the tag `Project = roomly` a day later, and check that no Elastic IP or EBS volume remains.

## Troubleshooting

### Where to look
```bash
sudo tail -100 /var/log/roomly-bootstrap.log
```

```bash
sudo docker ps -a
```

```bash
aws logs tail /roomly/prod/app --region us-west-2 --follow
```

### Symptoms and likely causes
| Symptom | Likely cause | How to confirm |
|---------|--------------|----------------|
| Deploy fails with `Not authorized to perform sts:AssumeRoleWithWebIdentity` | The trust policy's subject doesn't match what GitHub sends. New repositories use immutable subjects with numeric IDs | `gh api repos/OWNER/NAME/actions/oidc/customization/sub --jq .sub_claim_prefix` must equal `github_oidc_sub_prefix` in `terraform.tfvars`. See [the postmortem](POSTMORTEM.md) |
| Footer says `local` on AWS | IMDS hop limit is 1, so the response is dropped at the container network hop | `docker run --rm curlimages/curl -m 2 http://169.254.169.254/latest/api/token -X PUT -H 'X-aws-ec2-metadata-token-ttl-seconds: 60'` times out |
| App can't read SSM, or times out at startup | Same hop limit, or a missing IAM permission | `docker logs` on the app container; `AccessDenied` means IAM, a timeout means the hop limit |
| `/health` 503 "database unreachable" | Security group, DB still starting, or wrong host | From the instance: `nc -zv <db-host> 5432`. A timeout points at the security group |
| App exits with "bypasses Row-Level Security" | It's connected as `roomly_owner` | Check how `DATABASE_URL` was built. This check is working as intended |
| `docker compose` isn't a command | The Compose plugin failed to install or its checksum didn't match | Check `/var/log/roomly-bootstrap.log` |
| Browser warns about the certificate | Staging CA is on, or the rate limit was reached | `docker logs` on the Caddy container |
| No `Roomly` metrics in CloudWatch | The log driver isn't sending the EMF header | `awslogs-format: json/emf` must be set in `compose.prod.yml` |
| Booking at the "wrong" hour | A time zone conversion outside `timeutil.py` | `test_timeutil.py` covers daylight-saving changes |

### Certificate limits
Each replacement instance requests a new certificate, and Let's Encrypt limits duplicates to 5 per week for the same set of names. Test with the staging CA. Browsers will warn, and the health probe and CI smoke test will fail, because the staging CA isn't trusted. Switch back to the production CA afterwards.

If sslip.io itself is rate limited, use a domain you own by setting `domain_name` in `terraform.tfvars` and pointing it at the Elastic IP.
