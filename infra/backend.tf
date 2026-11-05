# State lives in a private, versioned, encrypted S3 bucket created by hand once (see
# docs/runbook.md). The bucket name is passed at init time so it isn't in the repo:
#   terraform init -backend-config="bucket=<your-state-bucket>"
terraform {
  backend "s3" {
    key          = "roomly/prod/terraform.tfstate"
    region       = "us-west-2"
    encrypt      = true
    use_lockfile = true
  }
}
