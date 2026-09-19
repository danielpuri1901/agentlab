# Cluster, log groups, and two ARM64 Fargate task definitions: one shared by both arms
# (container overrides in stepfunctions.tf pick which style each run uses), one for the
# finalizer. Shapes verified against
# https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/ecs_task_definition
# on 2026-08-16 (runtime_platform.cpu_architecture/operating_system_family,
# arn_without_revision used for IAM scoping in iam.tf).
resource "aws_ecs_cluster" "agentlab" {
  name = "agentlab"
}

# Global Constraints: "CloudWatch log groups get explicit 30-day retention."
resource "aws_cloudwatch_log_group" "arm_runner" {
  name              = "/ecs/agentlab-arm-runner"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "finalizer" {
  name              = "/ecs/agentlab-finalizer"
  retention_in_days = 30
}

# 1 vCPU / 2048 MB, ARM64 (Graviton) - matches the ARM64 image built in Task 3.
# AWS_REGION and AWS_DEFAULT_REGION are set here deliberately: Fargate does NOT
# auto-inject a region into the container environment the way Lambda does, and this
# was a live carry-forward finding from Task 1 - boto3 clients built with no explicit
# region (as worker.py's run_arm_command/finalize_command do: `boto3.resource("dynamodb")`,
# `boto3.client("s3")`) raise NoRegionError without one of AWS_REGION/AWS_DEFAULT_REGION
# present. ECS container overrides in stepfunctions.tf ADD/override individual
# environment entries by name rather than replacing the whole list (verified against
# https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-properties-pipes-pipe-ecscontaineroverride.html:
# "You can add new environment variables ... or you can override the existing
# environment variables from the Docker image or the task definition"), so setting the
# region once here is sufficient - but stepfunctions.tf also repeats it in every
# override for defense in depth, since a wrong assumption here previously caused a
# live failure.
resource "aws_ecs_task_definition" "arm_runner" {
  family                   = "agentlab-arm-runner"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([
    {
      name      = "arm-runner"
      image     = "${aws_ecr_repository.agentlab.repository_url}:${var.image_tag}"
      essential = true
      command   = ["worker", "run-arm"]
      environment = [
        { name = "AWS_REGION", value = var.aws_region },
        { name = "AWS_DEFAULT_REGION", value = var.aws_region },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.arm_runner.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "arm-runner"
        }
      }
    }
  ])
}

resource "aws_ecs_task_definition" "finalizer" {
  family                   = "agentlab-finalizer"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([
    {
      name      = "finalizer"
      image     = "${aws_ecr_repository.agentlab.repository_url}:${var.image_tag}"
      essential = true
      command   = ["worker", "finalize"]
      environment = [
        { name = "AWS_REGION", value = var.aws_region },
        { name = "AWS_DEFAULT_REGION", value = var.aws_region },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.finalizer.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "finalizer"
        }
      }
    }
  ])
}

resource "aws_cloudwatch_log_group" "proposer" {
  name              = "/ecs/agentlab-proposer"
  retention_in_days = 30
}

# 0.5 vCPU / 1 GB: the proposer makes one LLM call and a handful of HTTP/AWS
# calls; it never runs evals.
resource "aws_ecs_task_definition" "proposer" {
  family                   = "agentlab-proposer"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.proposer_task.arn

  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([
    {
      name      = "proposer"
      image     = "${aws_ecr_repository.agentlab.repository_url}:${var.image_tag}"
      essential = true
      command   = ["worker", "propose"]
      environment = [
        { name = "AWS_REGION", value = var.aws_region },
        { name = "AWS_DEFAULT_REGION", value = var.aws_region },
        { name = "STATE_TABLE", value = aws_dynamodb_table.state.name },
        { name = "RESULTS_BUCKET", value = aws_s3_bucket.results.id },
        { name = "PROPOSER_MODEL", value = var.proposer_model },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.proposer.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "proposer"
        }
      }
    }
  ])
}

resource "aws_cloudwatch_log_group" "explain" {
  name              = "/ecs/agentlab-explain"
  retention_in_days = 30
}

# The task can render four low-quality attempts plus one final medium render
# through compose_story_video, so it gets 4 vCPU / 8 GB;
# still ARM64 Fargate.
resource "aws_ecs_task_definition" "explain" {
  family                   = "agentlab-explain"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "4096"
  memory                   = "8192"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.explain_task.arn

  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }

  volume {
    name = "render-tmp"
  }

  container_definitions = jsonencode([
    {
      name                   = "init-render-tmp"
      image                  = "${aws_ecr_repository.agentlab.repository_url}:${var.video_image_tag}"
      essential              = false
      entryPoint             = ["/bin/sh", "-c"]
      command                = ["chmod 1777 /tmp"]
      user                   = "0"
      readonlyRootFilesystem = true
      linuxParameters = {
        capabilities = {
          drop = ["ALL"]
        }
      }
      mountPoints = [
        {
          sourceVolume  = "render-tmp"
          containerPath = "/tmp"
          readOnly      = false
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.explain.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "explain-init"
        }
      }
    },
    {
      name      = "explain"
      image     = "${aws_ecr_repository.agentlab.repository_url}:${var.video_image_tag}"
      essential = true
      command   = ["worker", "explain"]
      dependsOn = [
        {
          containerName = "init-render-tmp"
          condition     = "SUCCESS"
        }
      ]
      user                   = "999:999"
      readonlyRootFilesystem = true
      linuxParameters = {
        initProcessEnabled = true
        capabilities = {
          drop = ["ALL"]
        }
      }
      mountPoints = [
        {
          sourceVolume  = "render-tmp"
          containerPath = "/tmp"
          readOnly      = false
        }
      ]
      environment = [
        { name = "AWS_REGION", value = var.aws_region },
        { name = "AWS_DEFAULT_REGION", value = var.aws_region },
        { name = "STATE_TABLE", value = aws_dynamodb_table.state.name },
        { name = "RESULTS_BUCKET", value = aws_s3_bucket.results.id },
        { name = "DEEP_READ_MODEL", value = var.deep_read_model },
        { name = "PICK_MODEL", value = var.pick_model },
        { name = "DEEP_READ_PRICE_MODEL", value = "bedrock/global.anthropic.claude-sonnet-4-6" },
        { name = "PICK_PRICE_MODEL", value = "bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0" },
        { name = "HOME", value = "/tmp/home" },
        { name = "TMPDIR", value = "/tmp" },
        { name = "MPLCONFIGDIR", value = "/tmp/matplotlib" },
        { name = "XDG_CACHE_HOME", value = "/tmp/cache" },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.explain.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "explain"
        }
      }
    }
  ])
}
