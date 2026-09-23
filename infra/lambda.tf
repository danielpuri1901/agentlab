# The approvals webhook: a single-file Python Lambda behind a public function
# URL. authorization_type NONE is deliberate (spec: Telegram cannot sign
# SigV4); the Telegram secret-token header, validated first thing in the
# handler, is the auth. The IAM role grants exactly what the handler does:
# read the two telegram parameters, read/write the state table, send to the
# experiments queue, write its own logs.

data "archive_file" "approvals_webhook" {
  type        = "zip"
  source_file = "${path.module}/lambda/approvals_webhook.py"
  output_path = "${path.module}/.build/approvals_webhook.zip"
}

resource "aws_cloudwatch_log_group" "approvals_webhook" {
  name              = "/aws/lambda/agentlab-approvals-webhook"
  retention_in_days = 30
}

resource "aws_iam_role" "approvals_webhook" {
  name = "agentlab-approvals-webhook"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
        Action    = "sts:AssumeRole"
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "approvals_webhook" {
  name = "webhook-runtime"
  role = aws_iam_role.approvals_webhook.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["${aws_cloudwatch_log_group.approvals_webhook.arn}:*"]
      },
      {
        Sid      = "TelegramParams"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = [local.telegram_token_param_arn, local.telegram_secret_param_arn]
      },
      {
        Sid      = "Ledger"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.state.arn
      },
      {
        Sid      = "AutoSubmit"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.experiments.arn
      },
      # An approved proposal with no prepared experiment builds a video of
      # its cited source, so the webhook starts the explain task the same
      # way the scheduler does.
      {
        Sid      = "BuildApprovedProposalVideo"
        Effect   = "Allow"
        Action   = ["ecs:RunTask"]
        Resource = ["${aws_ecs_task_definition.explain.arn_without_revision}:*"]
      },
      {
        Sid    = "PassExplainRoles"
        Effect = "Allow"
        Action = ["iam:PassRole"]
        Resource = [
          aws_iam_role.ecs_execution.arn,
          aws_iam_role.explain_task.arn,
        ]
        Condition = {
          StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" }
        }
      },
    ]
  })
}

resource "aws_lambda_function" "approvals_webhook" {
  function_name    = "agentlab-approvals-webhook"
  role             = aws_iam_role.approvals_webhook.arn
  runtime          = "python3.12"
  handler          = "approvals_webhook.handler"
  filename         = data.archive_file.approvals_webhook.output_path
  source_code_hash = data.archive_file.approvals_webhook.output_base64sha256
  timeout          = 10
  memory_size      = 256

  environment {
    variables = {
      STATE_TABLE     = aws_dynamodb_table.state.name
      QUEUE_URL       = aws_sqs_queue.experiments.url
      ALLOWED_USER_ID = var.telegram_chat_id
      TOKEN_PARAM     = local.telegram_token_param
      SECRET_PARAM    = local.telegram_secret_param

      EXPLAIN_CLUSTER         = aws_ecs_cluster.agentlab.name
      EXPLAIN_TASK_DEFINITION = aws_ecs_task_definition.explain.arn
      EXPLAIN_SUBNETS         = join(",", data.aws_subnets.default_public.ids)
      EXPLAIN_SECURITY_GROUP  = aws_security_group.fargate_egress.id
    }
  }

  depends_on = [aws_cloudwatch_log_group.approvals_webhook]
}

resource "aws_lambda_function_url" "approvals_webhook" {
  function_name      = aws_lambda_function.approvals_webhook.function_name
  authorization_type = "NONE"
}

# Function URLs created after October 2025 require BOTH permission statements below -
# lambda:InvokeFunctionUrl alone is no longer sufficient even with authorization_type
# NONE; every request 403s without the second lambda:InvokeFunction grant. Verified
# against https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html on 2026-08-20
# (fix round 1).
resource "aws_lambda_permission" "approvals_webhook_url" {
  statement_id           = "AllowPublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.approvals_webhook.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

# See the comment above aws_lambda_permission.approvals_webhook_url: this second
# statement is the other half of the October 2025 dual-permission requirement
# (https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html) - without it, calls to
# the function URL 403 even though authorization_type is NONE.
resource "aws_lambda_permission" "approvals_webhook_invoke" {
  statement_id             = "AllowPublicFunctionUrlInvoke"
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.approvals_webhook.function_name
  principal                = "*"
  invoked_via_function_url = true
}
