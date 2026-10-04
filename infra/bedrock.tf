# The story and scene stages run on Claude Opus 4.6 through this application
# inference profile. The profile carries the project tag (default_tags), so
# its spend shows in the tagged cost report, and an ARN model id takes the
# Bedrock Converse path, which sends the effort setting. Opus 4.6 is the
# newest Opus this account may use: AWS refused Opus 5.5 and Sonnet 5.5 for
# this account on 2026-10-04 (support case 179110368000596). Bedrock usage is
# paid by the account's AWS credits; Claude Platform on AWS would bill through
# AWS Marketplace, which the credits do not cover.
resource "aws_bedrock_inference_profile" "opus" {
  name        = "agentlab-opus-4-6"
  description = "AgentLab story and scene code on Claude Opus 4.6"

  model_source {
    copy_from = "arn:aws:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:inference-profile/global.anthropic.claude-opus-4-6-v1"
  }
}

output "opus_model_id" {
  description = "Model id for story_model and scene_model in runtime.auto.tfvars."
  value       = "bedrock/${aws_bedrock_inference_profile.opus.arn}"
}
