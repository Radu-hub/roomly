terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.6"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "roomly"
      Env       = "prod"
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  azs        = slice(data.aws_availability_zones.available.names, 0, 2)

  # sslip.io resolves roomly.1-2-3-4.sslip.io to 1.2.3.4, giving us a hostname that
  # Let's Encrypt will certify without paying for a domain or a Route 53 zone.
  site_address = var.domain_name != "" ? var.domain_name : "roomly.${replace(aws_eip.app.public_ip, ".", "-")}.sslip.io"
}
