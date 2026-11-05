resource "aws_cloudwatch_log_group" "app" {
  name              = "/roomly/prod/app"
  retention_in_days = 7
}

resource "aws_sns_topic" "alerts" {
  name = "roomly-alerts"
}

# AWS emails a confirmation link: the subscription does nothing until it's clicked.
resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ---------------------------------------------------------------------------
# External health probe: checks the site the way a user reaches it, over public HTTPS.
# ---------------------------------------------------------------------------
data "archive_file" "health_probe" {
  type        = "zip"
  source_file = "${path.module}/lambda/health_probe.py"
  output_path = "${path.module}/build/health_probe.zip"
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "health_probe" {
  name               = "roomly-health-probe"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "health_probe" {
  statement {
    sid       = "PublishRoomlyMetricsOnly"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"] # PutMetricData is scoped by the namespace condition instead

    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["Roomly"]
    }
  }

  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.health_probe.arn}:*"]
  }
}

resource "aws_iam_role_policy" "health_probe" {
  name   = "roomly-health-probe"
  role   = aws_iam_role.health_probe.id
  policy = data.aws_iam_policy_document.health_probe.json
}

resource "aws_cloudwatch_log_group" "health_probe" {
  name              = "/aws/lambda/roomly-health-probe"
  retention_in_days = 7
}

resource "aws_lambda_function" "health_probe" {
  function_name    = "roomly-health-probe"
  role             = aws_iam_role.health_probe.arn
  runtime          = "python3.12"
  handler          = "health_probe.handler"
  filename         = data.archive_file.health_probe.output_path
  source_code_hash = data.archive_file.health_probe.output_base64sha256
  memory_size      = 128
  timeout          = 15

  environment {
    variables = {
      HEALTH_URL = "https://${local.site_address}/health"
    }
  }

  depends_on = [aws_cloudwatch_log_group.health_probe]
}

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "roomly-probe-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.health_probe.arn]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "invoke-health-probe"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}

resource "aws_scheduler_schedule" "health_probe" {
  name                = "roomly-health-probe"
  schedule_expression = "rate(5 minutes)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.health_probe.arn
    role_arn = aws_iam_role.scheduler.arn
  }
}

# ---------------------------------------------------------------------------
# Alarms
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "health" {
  alarm_name          = "roomly-site-down"
  alarm_description   = "The public health check failed twice in a row, or stopped reporting."
  namespace           = "Roomly"
  metric_name         = "HealthCheckOk"
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 2
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  # If the probe itself stops running we'd have no signal at all, so silence counts as down.
  treat_missing_data = "breaching"
  alarm_actions      = [aws_sns_topic.alerts.arn]
  ok_actions         = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "instance_status" {
  alarm_name          = "roomly-instance-status-failed"
  alarm_description   = "An EC2 status check is failing."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed"
  dimensions          = { AutoScalingGroupName = aws_autoscaling_group.app.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "db_storage" {
  alarm_name          = "roomly-db-storage-low"
  alarm_description   = "Less than 2 GB of database storage left."
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.roomly.identifier }
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "LessThanThreshold"
  threshold           = 2147483648
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "db_cpu" {
  alarm_name          = "roomly-db-cpu-high"
  alarm_description   = "Database CPU above 80% for 15 minutes."
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.roomly.identifier }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  comparison_operator = "GreaterThanThreshold"
  threshold           = 80
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_dashboard" "roomly" {
  dashboard_name = "roomly"

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title   = "Site health (1 = up)"
          region  = var.region
          stat    = "Minimum"
          period  = 300
          metrics = [["Roomly", "HealthCheckOk"]]
          yAxis   = { left = { min = 0, max = 1 } }
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = {
          title   = "Health check latency (ms)"
          region  = var.region
          stat    = "Average"
          period  = 300
          metrics = [["Roomly", "HealthLatencyMs"]]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6
        properties = {
          title  = "Bookings"
          region = var.region
          stat   = "Sum"
          period = 300
          metrics = [
            ["Roomly", "BookingCreated"],
            ["Roomly", "BookingConflict"],
            ["Roomly", "BookingRejected"],
            ["Roomly", "NoShowsReleased"],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6
        properties = {
          title  = "CPU %"
          region = var.region
          stat   = "Average"
          period = 300
          metrics = [
            ["AWS/EC2", "CPUUtilization", "AutoScalingGroupName", aws_autoscaling_group.app.name],
            ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", aws_db_instance.roomly.identifier],
          ]
        }
      },
    ]
  })
}
