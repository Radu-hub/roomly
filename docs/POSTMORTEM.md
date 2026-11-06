# Postmortem: the first deploy couldn't log in to AWS

> **Status: draft.** Written from the deploy logs with an AI assistant. Check each step against the linked runs and commands, re-tell it in your own words, then delete this note.

## Summary
The first GitHub Actions deploy failed before it built anything: AWS refused to let the workflow assume its deploy role. The role's trust policy expected GitHub to identify the repository as `repo:Radu-hub/roomly`, but new repositories now send a subject that includes numeric IDs, `repo:Radu-hub@175894609/roomly@1446514766`. The two never matched.

## Impact
The site wasn't live yet, so no users were affected. The first deploy was delayed by about 8 minutes, from the failure at 04:23 to a successful deploy at 04:29 on 5 November 2026. One side effect: the failed run's public log shows the AWS account ID (see "Also found" below).

## Timeline
All times UTC, 5 November 2026.
- `04:20` First push to `main`. CI passes.
- `04:21` The Deploy workflow starts ([run 41967262294](https://github.com/Radu-hub/roomly/actions/runs/41967262294)).
- `04:21–04:23` The "configure AWS credentials" step retries 12 times, then fails with `Not authorized to perform sts:AssumeRoleWithWebIdentity`.
- `04:23–04:25` Investigation (below).
- `04:25` Trust policy fixed with Terraform and the change pushed ([commit 2a0aee4](https://github.com/Radu-hub/roomly/commit/2a0aee4be7074900bea5ec438fefe7b70f67ba72)).
- `04:26` The next deploy ([run 41967823461](https://github.com/Radu-hub/roomly/actions/runs/41967823461)) gets AWS credentials, builds and pushes the image.
- `04:27` That deploy stops at `deploy.sh`, as the runbook says the first one will, because the database has no roles or tables yet.
- `04:28` One-time database setup (`bootstrap-db.sh`) run on the instance through SSM.
- `04:29` Re-run of the failed job passes, including the smoke test against the public URL. Fixed.

## Detection
The failed GitHub Actions run. There was no alarm for this: the site's health alarm only watches a site that's already running.

## Investigation
STS's error doesn't say which part of the trust policy failed, so I checked each thing the policy depends on, cheapest first.

1. **Hypothesis:** the workflow asked for the wrong role, for example a typo in the repository variable. **Checked by:** the role ARN printed in the run log, against `terraform output github_deploy_role_arn`. **Result:** ruled out, they matched.
2. **Hypothesis:** the GitHub OIDC provider is missing in IAM, or has the wrong audience. **Checked by:** `aws iam get-open-id-connect-provider`. **Result:** ruled out. The URL was `token.actions.githubusercontent.com` and the audience was `sts.amazonaws.com`, matching the workflow.
3. **Hypothesis:** the token's subject names a different branch or event than the policy allows. The policy only accepts `ref:refs/heads/main`, and this job is started by a `workflow_run` event, not a push. **Checked by:** `gh run view` showed the branch was `main`, and `workflow_run` jobs run in the default branch's context, which is `main`. **Result:** ruled out as far as the branch goes, but it pointed at the subject claim as the part to look at.
4. **Hypothesis:** GitHub formats the subject differently for this repository. **Checked by:** `gh api repos/Radu-hub/roomly/actions/oidc/customization/sub`, which returned `"use_immutable_subject": true` and `"sub_claim_prefix": "repo:Radu-hub@175894609/roomly@1446514766"`. **Result:** confirmed. The policy compared the subject with `StringEquals` against `repo:Radu-hub/roomly:ref:refs/heads/main`, which can never equal the new format.

I didn't decode the token from inside the runner, so the final proof is that the next deploy assumed the role as soon as the policy used the new prefix.

## Root cause
- **Why did the deploy fail?** STS refused to issue credentials for the deploy role.
- **Why?** The trust policy's `sub` condition didn't match the token GitHub sent.
- **Why?** The Terraform built the expected subject as `repo:<owner>/<name>`, the format in most tutorials and in AWS's own examples.
- **Why was that wrong here?** GitHub now gives new repositories immutable subject claims, which include the numeric owner and repository IDs, so that a renamed or re-created repository can't inherit another one's AWS access.

The thing I could change was the assumption in Terraform about the subject's format.

## Fix
[Commit 2a0aee4](https://github.com/Radu-hub/roomly/commit/2a0aee4be7074900bea5ec438fefe7b70f67ba72): a new required Terraform variable, `github_oidc_sub_prefix`, holds the exact prefix that GitHub's API reports for the repository. The trust policy now accepts `<prefix>:ref:refs/heads/main`. The condition still uses `StringEquals`, so the role is still limited to one repository's `main` branch. It's arguably tighter than before, because the numeric IDs survive a rename.

## Prevention
- The variable is required and has no default, so a new environment can't silently use the old format. `terraform.tfvars.example` shows the `gh api` command that prints the right value.
- The runbook's troubleshooting table now lists this error and how to confirm the cause in one command.
- Before real customers: run one throwaway workflow that only calls `aws sts get-caller-identity` before the first real deploy, so a trust problem shows up on its own and not in the middle of a release.

## What I learned
An authorization error that says only "not authorized" usually means the conditions don't match, not that the resource is missing. The fastest route was to list every value the policy compares and check each one against its source, instead of trusting the format I expected.

## Also found
- **The account ID was printed in a public log.** The workflow masks the account ID, but GitHub prints the job's variables before that step runs, so the role ARN and ECR URL, which contain it, appeared in the failed run's log. They're now Actions secrets, which GitHub masks everywhere, in the same commit. An account ID doesn't grant access, but this repository's logs are public.
- **A smaller failure before this one.** The first `terraform apply` stopped partway because a security group rule's description contained an apostrophe ("Let's Encrypt"), which EC2 doesn't allow. `terraform validate` and `plan` both passed, because only the AWS API checks that character set. The instance had already started without its outbound rule, so after removing the apostrophe I had Auto Scaling replace it.

---

## Where to look when something breaks

Likely candidates, and the commands to investigate them, are in [the runbook](runbook.md#troubleshooting).
