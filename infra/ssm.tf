# special = false keeps passwords safe to use inside connection strings. These values also
# end up in Terraform state, which is why the state bucket is private and encrypted
# (docs/adr/0006-secrets.md).
resource "random_password" "db_master" {
  length  = 32
  special = false
}

resource "random_password" "db_owner" {
  length  = 32
  special = false
}

resource "random_password" "db_app" {
  length  = 32
  special = false
}

resource "random_password" "session_secret" {
  length  = 64
  special = false
}

resource "random_password" "demo_password" {
  length  = 20
  special = false
}

locals {
  secure_parameters = {
    "db/master_password" = random_password.db_master.result
    "db/owner_password"  = random_password.db_owner.result
    "db/app_password"    = random_password.db_app.result
    "session_secret"     = random_password.session_secret.result
    "demo_password"      = random_password.demo_password.result
  }
}

resource "aws_ssm_parameter" "secure" {
  for_each = nonsensitive(toset(keys(local.secure_parameters)))

  name  = "/roomly/prod/${each.key}"
  type  = "SecureString" # encrypted with the default aws/ssm KMS key
  value = local.secure_parameters[each.key]
}

resource "aws_ssm_parameter" "db_host" {
  name  = "/roomly/prod/db/host"
  type  = "String"
  value = aws_db_instance.roomly.address
}

# The currently deployed image. CI updates it after each successful deploy, so a replacement
# instance boots the latest good version. Terraform only creates it.
resource "aws_ssm_parameter" "image_tag" {
  name  = "/roomly/prod/image_tag"
  type  = "String"
  value = "none"

  lifecycle {
    ignore_changes = [value]
  }
}
