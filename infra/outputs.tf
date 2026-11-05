output "site_url" {
  description = "The public URL"
  value       = "https://${local.site_address}"
}

output "elastic_ip" {
  value = aws_eip.app.public_ip
}

output "ecr_repository_url" {
  value = aws_ecr_repository.roomly.repository_url
}

output "github_deploy_role_arn" {
  description = "Set as the AWS_ROLE_ARN repository variable in GitHub"
  value       = aws_iam_role.github_deploy.arn
}

output "autoscaling_group" {
  value = aws_autoscaling_group.app.name
}

output "db_endpoint" {
  value = aws_db_instance.roomly.address
}
