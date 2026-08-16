# Four roles, one per caller in the orchestration path, each scoped to only what that
# caller does (Global Constraints: "No wildcards on resources where an ARN is knowable").
#
#   ecs_execution - assumed by ECS to start a container: pull the image, ship logs.
#   ecs_task      - assumed by the running container itself: call Bedrock, read/write
#                   S3 and DynamoDB. Shared by both task definitions (arm-runner and
#                   finalizer) per the plan's singular "task role" - the finalizer never
#                   calls Bedrock but sharing one role for both is what Task 4's brief
#                   specifies, and the unused grant is the same low-risk shape as the
#                   rest of this role (read/write only, no destructive actions).
#   sfn           - assumed by Step Functions to run/monitor/stop ECS tasks via .sync.
#   pipe          - assumed by the EventBridge Pipe to read SQS and start executions.
#
# Every trust policy below adds an aws:SourceAccount condition scoping the assumption to
# this account (a standard confused-deputy mitigation for AWS-service trust policies).
# The pipe role cannot additionally condition on the pipe's own ARN (aws:SourceArn)
# without creating a dependency cycle: the pipe resource needs this role to exist first.
data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# ECS task execution role: ECR pull + CloudWatch Logs only.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "ecs_execution" {
  name = "agentlab-ecs-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "ecs-tasks.amazonaws.com" }
        Action    = "sts:AssumeRole"
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "ecs_execution" {
  name = "ecr-pull-and-logs"
  role = aws_iam_role.ecs_execution.id

  # ecr:GetAuthorizationToken does not support resource-level permissions - the ECR API
  # requires it be granted on Resource "*" (verified against the AWS-authored execution
  # role policy in https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-execution-IAM-role.html,
  # which grants this action on "*" for exactly this reason). The image-pull actions and
  # the log actions are both scoped to the specific repo/log-group ARNs they need.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrAuthToken"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Sid    = "EcrPull"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
        ]
        Resource = aws_ecr_repository.agentlab.arn
      },
      {
        Sid    = "Logs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = [
          "${aws_cloudwatch_log_group.arm_runner.arn}:*",
          "${aws_cloudwatch_log_group.finalizer.arn}:*",
        ]
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# ECS task role: what the worker code itself is allowed to do at runtime.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "ecs_task" {
  name = "agentlab-ecs-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "ecs-tasks.amazonaws.com" }
        Action    = "sts:AssumeRole"
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "ecs_task" {
  name = "bedrock-s3-dynamodb"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Bedrock model-invocation actions on Resource "*" per Task 4's binding
        # contract (the worker selects models by ID at runtime via the MODEL env var,
        # so no single foundation-model or inference-profile ARN can be known ahead of
        # time; Bedrock's own IAM examples for InvokeModel/Converse use "*" for this
        # reason - https://docs.aws.amazon.com/bedrock/latest/userguide/security_iam_id-based-policy-examples.html).
        Sid    = "BedrockInvoke"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
          "bedrock:Converse",
          "bedrock:ConverseStream",
        ]
        Resource = "*"
      },
      {
        Sid    = "ResultsReadWrite"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
        ]
        Resource = "${aws_s3_bucket.results.arn}/experiments/*"
      },
      {
        # ListBucket is a bucket-level action (needs the bucket ARN itself, not an
        # object ARN) - scoped further with an s3:prefix condition so it only lists
        # keys under experiments/, matching the object-level grant above.
        Sid      = "ResultsList"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.results.arn
        Condition = {
          StringLike = { "s3:prefix" = "experiments/*" }
        }
      },
      {
        Sid    = "StateTable"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:Query",
        ]
        Resource = aws_dynamodb_table.state.arn
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# Step Functions role: run/monitor/stop the ECS tasks the state machine drives.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "sfn" {
  name = "agentlab-sfn"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "states.amazonaws.com" }
        Action    = "sts:AssumeRole"
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "sfn" {
  name = "ecs-sync-integration"
  role = aws_iam_role.sfn.id

  # Shape verified against
  # https://docs.aws.amazon.com/step-functions/latest/dg/connect-ecs.html
  # ("IAM policies for calling Amazon ECS/AWS Fargate" > "Using Run a Job (.sync)",
  # "dynamic resources" variant, since both task definitions are managed by this same
  # Terraform root and their revisions change on redeploy) and
  # https://docs.aws.amazon.com/step-functions/latest/dg/service-integration-iam-templates.html
  # ("Additional permissions for tasks using .sync": ecs:DescribeTasks for polling, plus
  # events:PutTargets/PutRule/DescribeRule scoped to the AWS-managed
  # StepFunctionsGetEventsForECSTaskRule EventBridge rule Step Functions creates to learn
  # of task completion) on 2026-08-16.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RunTask"
        Effect = "Allow"
        Action = ["ecs:RunTask"]
        Resource = [
          "${aws_ecs_task_definition.arm_runner.arn_without_revision}:*",
          "${aws_ecs_task_definition.finalizer.arn_without_revision}:*",
        ]
      },
      {
        # StopTask/DescribeTasks cannot be scoped to a resource ARN (the task ARN is
        # not known until RunTask returns); Step Functions can only stop tasks it
        # started, per the same doc, despite the "*" resource.
        Sid      = "MonitorTask"
        Effect   = "Allow"
        Action   = ["ecs:StopTask", "ecs:DescribeTasks"]
        Resource = "*"
      },
      {
        Sid    = "SyncEventsRule"
        Effect = "Allow"
        Action = ["events:PutTargets", "events:PutRule", "events:DescribeRule"]
        Resource = [
          "arn:aws:events:${var.aws_region}:${data.aws_caller_identity.current.account_id}:rule/StepFunctionsGetEventsForECSTaskRule",
        ]
      },
      {
        # ECS RunTask must pass both roles to the ECS agent for every launched task.
        Sid      = "PassEcsRoles"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [aws_iam_role.ecs_execution.arn, aws_iam_role.ecs_task.arn]
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# EventBridge Pipe role: consume the queue, start the state machine.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "pipe" {
  name = "agentlab-pipe"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "pipes.amazonaws.com" }
        Action    = "sts:AssumeRole"
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "pipe" {
  name = "sqs-source-sfn-target"
  role = aws_iam_role.pipe.id

  # SQS source permissions verified against
  # https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-pipes-permissions.html
  # ("Amazon SQS execution role permissions") on 2026-08-16: exactly
  # sqs:ReceiveMessage, sqs:DeleteMessage, sqs:GetQueueAttributes, nothing broader.
  # states:StartExecution matches the FIRE_AND_FORGET invocation type set in pipe.tf
  # (see that file for why REQUEST_RESPONSE/StartSyncExecution is not an option here).
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ConsumeQueue"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
        ]
        Resource = aws_sqs_queue.experiments.arn
      },
      {
        Sid      = "StartExperiment"
        Effect   = "Allow"
        Action   = ["states:StartExecution"]
        Resource = aws_sfn_state_machine.experiment.arn
      },
    ]
  })
}
