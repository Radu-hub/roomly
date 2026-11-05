data "aws_ssm_parameter" "al2023_ami" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-${var.instance_arch}"
}

resource "aws_eip" "app" {
  domain = "vpc"

  tags = { Name = "roomly" }
}

locals {
  # sha256 values from each release's published .sha256 file.
  compose_version = "v2.29.7"
  compose_builds = {
    arm64  = { arch = "aarch64", sha256 = "6e9fbd5daa20dca5d7d89145081ae8155d68ef2928b497d9f85b54fe0f9dbb2c" }
    x86_64 = { arch = "x86_64", sha256 = "383ce6698cd5d5bbf958d2c8489ed75094e34a77d340404d9f32c4ae9e12baf0" }
  }

  user_data = templatefile("${path.module}/../deploy/user-data.sh.tftpl", {
    region            = var.region
    eip_allocation_id = aws_eip.app.allocation_id
    image_repo        = aws_ecr_repository.roomly.repository_url
    site_address      = local.site_address
    acme_ca           = var.acme_ca
    compose_version   = local.compose_version
    compose_arch      = local.compose_builds[var.instance_arch].arch
    compose_sha256    = local.compose_builds[var.instance_arch].sha256
    # base64, because these files contain ${...} that Terraform must not interpret.
    compose_b64      = filebase64("${path.module}/../deploy/compose.prod.yml")
    caddyfile_b64    = filebase64("${path.module}/../deploy/Caddyfile")
    deploy_b64       = filebase64("${path.module}/../deploy/deploy.sh")
    bootstrap_db_b64 = filebase64("${path.module}/../scripts/bootstrap-db.sh")
  })
}

resource "aws_launch_template" "app" {
  name_prefix            = "roomly-"
  image_id               = data.aws_ssm_parameter.al2023_ami.value
  instance_type          = var.instance_type
  vpc_security_group_ids = [aws_security_group.app.id]
  user_data              = base64encode(local.user_data)
  update_default_version = true

  iam_instance_profile {
    arn = aws_iam_instance_profile.app.arn
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required" # IMDSv2 only
    # Containers are one network hop past the host. The default limit of 1 silently drops
    # the response on its way to them, so 2 is needed for instance identity and credentials.
    http_put_response_hop_limit = 2
  }

  block_device_mappings {
    device_name = "/dev/xvda"

    ebs {
      volume_type           = "gp3"
      volume_size           = 16
      encrypted             = true
      delete_on_termination = true
    }
  }

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name    = "roomly"
      App     = "roomly"
      Env     = "prod"
      Project = "roomly"
    }
  }

  tag_specifications {
    resource_type = "volume"
    tags = {
      Name    = "roomly"
      Project = "roomly"
    }
  }
}

# An Auto Scaling Group of exactly one: not for scaling but for self-healing. If the instance
# dies the group launches another, and user data re-attaches the Elastic IP and deploys.
resource "aws_autoscaling_group" "app" {
  name                = "roomly"
  min_size            = 1
  max_size            = 1
  desired_capacity    = 1
  vpc_zone_identifier = aws_subnet.public[*].id
  health_check_type   = "EC2"

  # User data takes minutes, so don't judge a new instance's health right away.
  health_check_grace_period = 300

  launch_template {
    id      = aws_launch_template.app.id
    version = "$Latest"
  }

  # Changing user data (for example deploy.sh) replaces the instance on terraform apply.
  # With one instance this means a short outage, which is an accepted tradeoff.
  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 0
    }
  }

  tag {
    key                 = "App"
    value               = "roomly"
    propagate_at_launch = true
  }

  tag {
    key                 = "Env"
    value               = "prod"
    propagate_at_launch = true
  }
}
