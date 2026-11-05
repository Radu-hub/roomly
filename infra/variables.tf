variable "region" {
  description = "AWS region for everything"
  type        = string
  default     = "us-west-2"
}

variable "alert_email" {
  description = "Receives alarm and budget emails. Set it in terraform.tfvars, which git ignores."
  type        = string
}

variable "github_repository" {
  description = "owner/name of the only GitHub repository allowed to deploy"
  type        = string
}

variable "instance_type" {
  description = "EC2 type. Must match instance_arch. Check it's free-tier eligible for your account."
  type        = string
  default     = "t4g.micro"
}

variable "instance_arch" {
  description = "arm64 for Graviton (t4g), x86_64 for t3/t2. Also set the RUNNER repository variable."
  type        = string
  default     = "arm64"

  validation {
    condition     = contains(["arm64", "x86_64"], var.instance_arch)
    error_message = "instance_arch must be arm64 or x86_64."
  }
}

variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t4g.micro"
}

variable "domain_name" {
  description = "A domain you own, already pointed at the Elastic IP. Empty uses sslip.io."
  type        = string
  default     = ""
}

variable "acme_ca" {
  description = "ACME directory. Use the Let's Encrypt staging URL while testing instance replacement."
  type        = string
  default     = "https://acme-v02.api.letsencrypt.org/directory"
}
