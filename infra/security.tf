# sg_app: the web ports only. There is no port 22: shell access goes through SSM Session
# Manager, which opens no inbound port at all.
resource "aws_security_group" "app" {
  name        = "roomly-app"
  description = "Roomly web server: HTTP and HTTPS only, no SSH"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "sg_app" }
}

# Port 80 stays open so Caddy can answer Let's Encrypt and redirect visitors to HTTPS.
resource "aws_vpc_security_group_ingress_rule" "app_web" {
  for_each = {
    http-v4  = { port = 80, cidr_ipv4 = "0.0.0.0/0" }
    https-v4 = { port = 443, cidr_ipv4 = "0.0.0.0/0" }
    http-v6  = { port = 80, cidr_ipv6 = "::/0" }
    https-v6 = { port = 443, cidr_ipv6 = "::/0" }
  }

  security_group_id = aws_security_group.app.id
  description       = each.key
  ip_protocol       = "tcp"
  from_port         = each.value.port
  to_port           = each.value.port
  cidr_ipv4         = lookup(each.value, "cidr_ipv4", null)
  cidr_ipv6         = lookup(each.value, "cidr_ipv6", null)
}

resource "aws_vpc_security_group_egress_rule" "app_all_v4" {
  security_group_id = aws_security_group.app.id
  description       = "ECR, SSM, CloudWatch, Lets Encrypt and RDS"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# sg_db: Postgres from sg_app only. It references the group rather than a CIDR, so only
# instances in that group qualify, and it has no egress rules at all.
resource "aws_security_group" "db" {
  name        = "roomly-db"
  description = "Roomly database: Postgres from the app security group only"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "sg_db" }
}

resource "aws_vpc_security_group_ingress_rule" "db_from_app" {
  security_group_id            = aws_security_group.db.id
  description                  = "Postgres from sg_app"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.app.id
}
