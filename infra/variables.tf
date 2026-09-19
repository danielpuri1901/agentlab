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
  description = "Tag of the `agentlab` ECR image the ECS task definitions run. scripts/build_and_push_image.sh pushes app-<git-sha>; override with -var image_tag=<tag> at apply time to roll out a new build."
  type        = string
  default     = "latest"
}

variable "telegram_chat_id" {
  description = "Telegram chat/user id. The webhook Lambda accepts taps from this id only."
  type        = string
  sensitive   = true
}

variable "proposer_model" {
  description = "Bedrock model id used by the proposal task."
  type        = string
}

variable "deep_read_model" {
  description = "Bedrock model id used to read papers and generate videos."
  type        = string
}

variable "pick_model" {
  description = "Bedrock model id used to rank paper candidates."
  type        = string
}

variable "video_image_tag" {
  description = "Tag of the `agentlab` ECR image the explain task definition runs (same repo as image_tag, different image - it carries the render toolchain from Dockerfile.video). scripts/build_and_push_video_image.sh pushes video-<git-sha>; override with -var video_image_tag=<tag> at apply time to roll out a new build."
  type        = string
  default     = "latest-video"
}
