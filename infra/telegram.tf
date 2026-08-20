# Telegram channel parameters. The chat id is not a secret and is managed
# here. The bot token and the webhook secret ARE secrets: they are created
# out of band (aws ssm put-parameter + scripts/register_telegram_webhook.py)
# so they never appear in Terraform state or the repo. Everything that needs
# them reads them by NAME at runtime, so Terraform only handles the names.

resource "aws_ssm_parameter" "telegram_chat_id" {
  name  = "/agentlab/telegram/chat-id"
  type  = "String"
  value = var.telegram_chat_id
}

locals {
  telegram_token_param      = "/agentlab/telegram/bot-token"
  telegram_secret_param     = "/agentlab/telegram/webhook-secret"
  telegram_param_arn_prefix = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/agentlab/telegram"

  # Per-param ARNs, built from the names above, so each IAM role below can be scoped to
  # exactly the parameters its own runtime code reads (fix round 1: the shared
  # ".../telegram/*" wildcard let every role read the webhook secret, which only the
  # webhook Lambda needs). The webhook Lambda reads bot-token + webhook-secret
  # (approvals_webhook.py: TOKEN_PARAM, SECRET_PARAM); the ECS task roles read bot-token
  # + chat-id (src/agentlab/notify.py: TOKEN_PARAM, CHAT_ID_PARAM) and never touch the
  # webhook secret - they send Telegram messages, they never validate an inbound
  # webhook.
  telegram_token_param_arn   = "${local.telegram_param_arn_prefix}/bot-token"
  telegram_secret_param_arn  = "${local.telegram_param_arn_prefix}/webhook-secret"
  telegram_chat_id_param_arn = aws_ssm_parameter.telegram_chat_id.arn
}
