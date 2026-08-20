# EventBridge Scheduler: three cron schedules in Europe/Amsterdam, each
# launching the proposer task definition on Fargate. 08:00 is a pure flush
# (delivers pings queued during quiet hours, which end at exactly 08:00);
# 09:30 and 12:00 are full proposer runs (they flush first, then propose).
# The target arn is the CLUSTER; the task definition rides in ecs_parameters,
# revision-pinned (.arn) so a redeploy updates the schedules deterministically.
#
# Shape verified on 2026-08-20 against:
# - https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/scheduler_schedule
#   (page did not render statically; verified instead against the provider's own
#   acceptance tests, internal/service/scheduler/schedule_test.go,
#   testAccScheduleConfig_targetECSParameters2/3 on the hashicorp/terraform-provider-aws
#   main branch): network_configuration DOES nest inside ecs_parameters, with
#   assign_public_ip/security_groups/subnets exactly as below - confirms the brief's
#   shape needed no fix.
# - https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_RunTask.html: the
#   RunTask "overrides" object's containerOverrides[] entries use camelCase "name" and
#   "command" keys - matches the `input` JSON below.
# - https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_event_target
#   (aws_cloudwatch_event_target, the EventBridge Rules equivalent that drives the same
#   underlying ECS RunTask call): its own documented example is
#   `input = jsonencode({ containerOverrides = [{ name = ..., command = [...] }] })`,
#   confirming the `input` shape used here (containerOverrides/name/command,
#   camelCase) is correct as written. No deviation from the brief.
resource "aws_iam_role" "scheduler" {
  name = "agentlab-scheduler"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "scheduler.amazonaws.com" }
        Action    = "sts:AssumeRole"
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  name = "run-proposer-task"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RunProposer"
        Effect = "Allow"
        Action = ["ecs:RunTask"]
        Resource = [
          "${aws_ecs_task_definition.proposer.arn_without_revision}:*",
        ]
      },
      {
        Sid      = "PassEcsRoles"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [aws_iam_role.ecs_execution.arn, aws_iam_role.proposer_task.arn]
        Condition = {
          StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" }
        }
      },
    ]
  })
}

locals {
  proposer_schedules = {
    flush-pings = {
      cron    = "cron(0 8 * * ? *)"
      command = ["worker", "flush-pings"]
    }
    propose-morning = {
      cron    = "cron(30 9 * * ? *)"
      command = ["worker", "propose"]
    }
    propose-midday = {
      cron    = "cron(0 12 * * ? *)"
      command = ["worker", "propose"]
    }
  }
}

resource "aws_scheduler_schedule" "proposer" {
  for_each = local.proposer_schedules

  name                         = "agentlab-${each.key}"
  schedule_expression          = each.value.cron
  schedule_expression_timezone = "Europe/Amsterdam"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_ecs_cluster.agentlab.arn
    role_arn = aws_iam_role.scheduler.arn

    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.proposer.arn
      launch_type         = "FARGATE"

      network_configuration {
        subnets          = data.aws_subnets.default_public.ids
        security_groups  = [aws_security_group.fargate_egress.id]
        assign_public_ip = true
      }
    }

    input = jsonencode({
      containerOverrides = [
        {
          name    = "proposer"
          command = each.value.command
        }
      ]
    })

    retry_policy {
      maximum_retry_attempts = 1
    }
  }
}
