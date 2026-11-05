resource "aws_db_subnet_group" "roomly" {
  name       = "roomly"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_parameter_group" "roomly" {
  name   = "roomly-pg16"
  family = "postgres16"

  # Refuse any connection that isn't encrypted.
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_db_instance" "roomly" {
  identifier     = "roomly"
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.db_instance_class

  allocated_storage = 20
  storage_type      = "gp3"
  storage_encrypted = true

  username = "roomly_master"
  password = random_password.db_master.result

  db_subnet_group_name   = aws_db_subnet_group.roomly.name
  parameter_group_name   = aws_db_parameter_group.roomly.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false
  multi_az               = false

  backup_retention_period = 1

  # Assignment only, so `terraform destroy` leaves nothing billing. See
  # docs/production-readiness.md for what changes before real customers.
  deletion_protection = false
  skip_final_snapshot = true

  # Both cost money beyond what this app needs.
  performance_insights_enabled = false
  monitoring_interval          = 0

  apply_immediately = true
}
