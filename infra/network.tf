# Global Constraints (docs/plans/2026-08-16-v02-aws-fabric.md): public subnets with
# assignPublicIp=ENABLED, NO NAT gateway, NO load balancer, NO VPC endpoints except the
# free gateway endpoints for S3 and DynamoDB. So this fabric runs in the account's
# default VPC rather than provisioning a new one - the default VPC's subnets already
# have a route to an Internet Gateway and MapPublicIpOnLaunch=true, which is exactly
# the "public subnet, no NAT" shape the plan asks for, with nothing new to build or pay
# for.
data "aws_vpc" "default" {
  default = true
}

# Every default-VPC subnet is a "default for AZ" subnet, which is precisely the set of
# public subnets described above (one per AZ, IGW-routed). Filtering on
# default-for-az=true (rather than assuming a hardcoded subnet count) keeps this correct
# if AWS ever changes how many AZs a default VPC spans.
data "aws_subnets" "default_public" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }

  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

# Route tables associated with the default VPC, so the gateway endpoints below attach
# to whatever route table(s) actually route the public subnets' traffic (a default VPC
# normally has a single main route table shared by every subnet, but this does not
# assume that).
data "aws_route_tables" "default" {
  vpc_id = data.aws_vpc.default.id
}

# S3 and DynamoDB gateway endpoints are free (no hourly or per-GB charge, unlike
# interface endpoints) and the default VPC's public subnets already reach both services
# fine over their public IPs via the Internet Gateway - so these are not required for
# the fabric to work today. They are added anyway because the Global Constraints
# explicitly call them out as the one exception to "no VPC endpoints", and because they
# are harmless now (zero cost, zero behavior change - traffic to S3/DynamoDB from the
# associated route tables is simply preferred over the endpoint's prefix list instead of
# egressing to the internet) while future-proofing the fabric: if a later change removes
# public IP assignment or adds a route restriction, S3/DynamoDB access keeps working
# without another apply.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = data.aws_vpc.default.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = data.aws_route_tables.default.ids
}

resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = data.aws_vpc.default.id
  service_name      = "com.amazonaws.${var.aws_region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = data.aws_route_tables.default.ids
}

# Egress-only security group for the Fargate tasks: no ingress rules at all (the workers
# never accept inbound connections - they call out to Bedrock, S3, DynamoDB, and ECR),
# and unrestricted outbound so those HTTPS calls are never blocked. A custom security
# group (unlike the VPC's default SG) denies all traffic unless a rule explicitly allows
# it, so the egress rule below is required, not just documentation.
resource "aws_security_group" "fargate_egress" {
  name        = "agentlab-fargate-egress"
  description = "Egress-only SG for AgentLab Fargate tasks: no inbound, unrestricted outbound to Bedrock/S3/DynamoDB/ECR over HTTPS."
  vpc_id      = data.aws_vpc.default.id

  egress {
    description = "All outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
