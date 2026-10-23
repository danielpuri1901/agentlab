variable "aws_region" {
  description = "AWS region for all resources."
  type        = string
  default     = "eu-west-1"
}

variable "project" {
  description = "Value applied to the project tag on every resource via default_tags."
  type        = string
  default     = "agentlab"
}

variable "alert_email" {
  description = "Email address that receives the monthly budget notification."
  type        = string
}

variable "monthly_budget_usd" {
  description = "Monthly AWS cost budget limit in USD."
  type        = string
  default     = "50"
}

variable "state_table_name" {
  description = "DynamoDB table name for experiment state transitions. Must match the STATE_TABLE contract src/agentlab/worker.py already reads and writes: PK experiment_id (S), SK sk (S)."
  type        = string
  default     = "agentlab-state"
}

variable "results_bucket_name" {
  description = "S3 bucket for EvalLogs and reports. Globally unique, so suffixed with the account id."
  type        = string
}

variable "experiments_queue_name" {
  description = "SQS queue name that receives experiment submissions from `agentlab cloud submit`."
  type        = string
  default     = "agentlab-experiments"
}

variable "image_tag" {
  description = "Tag of the `agentlab` ECR image the ECS task definitions run. The build-images GitHub Actions workflow pushes app-<git-sha>; override with -var image_tag=<tag> at apply time to roll out a new build."
  type        = string
  default     = "latest"
}

variable "telegram_chat_id" {
  description = "Telegram chat/user id. The webhook Lambda accepts taps from this id only."
  type        = string
  sensitive   = true
}

variable "proposer_model" {
  description = "Model id used by the proposal and weekly consolidate tasks, in litellm form (anthropic/... for the Anthropic API)."
  type        = string
}

variable "deep_read_model" {
  description = "Model id that reads papers and topics: anthropic/<id> for the Anthropic API, bedrock/<id> for Bedrock."
  type        = string
}

variable "pick_model" {
  description = "Model id that ranks paper candidates: anthropic/<id> for the Anthropic API, bedrock/<id> for Bedrock."
  type        = string
}

variable "deep_read_price_model" {
  description = "Pricing id for deep_read_model, so its calls get a cost estimate even when deep_read_model is an application inference profile ARN."
  type        = string
  default     = "global.anthropic.claude-sonnet-5-5"
}

variable "deep_read_thinking" {
  description = "Thinking type the deep read call asks for (thinking.type). Sonnet 5.5 thinks by default and rejects \"disabled\"; \"between_tools\" switches thinking off for the deep read, which makes no tool calls."
  type        = string
  default     = "between_tools"
}

variable "story_model" {
  description = "Model id that writes the video storyboards. anthropic/<id> and Bedrock application inference profile ARNs send story_effort."
  type        = string
}

variable "scene_model" {
  description = "Model id that writes the Manim scene code. anthropic/<id> and Bedrock application inference profile ARNs send story_effort."
  type        = string
}

variable "story_price_model" {
  description = "Pricing id for story_model, so its calls get a cost estimate even when story_model is an application inference profile ARN."
  type        = string
  default     = "global.anthropic.claude-opus-5-5"
}

variable "scene_price_model" {
  description = "Pricing id for scene_model, so its calls get a cost estimate even when scene_model is an application inference profile ARN."
  type        = string
  default     = "global.anthropic.claude-opus-5-5"
}

variable "story_effort" {
  description = "Output effort the storyboard and scene code calls ask for (output_config.effort)."
  type        = string
  default     = "high"
}

variable "video_image_tag" {
  description = "Tag of the `agentlab` ECR image the explain task definition runs (same repo as image_tag, different image - it carries the render toolchain from Dockerfile.video). The build-images GitHub Actions workflow pushes video-<git-sha>; override with -var video_image_tag=<tag> at apply time to roll out a new build."
  type        = string
  default     = "latest-video"
}

variable "pick_price_model" {
  description = "Price entry for the paper pick model (a litellm model_cost key or an agentlab.costs rate-card key)."
  type        = string
  default     = "claude-haiku-4-5"
}

variable "chapter_lanes" {
  description = "Build each storyboard chapter in its own lane (1 on, 0 off). Not empty: ECS drops an empty environment value, and the explain task definition would then differ from its own state on every plan."
  type        = string
  default     = "0"
}

variable "reviewer_mode" {
  description = "Stills reviewer: off, log (verdict stored, nothing changes), fix (reserved, runs as log until calibrated)."
  type        = string
  default     = "off"
}
