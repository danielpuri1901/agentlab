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
  default     = "danielpuri1901@gmail.com"
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
  default     = "agentlab-results-891377302765"
}

variable "experiments_queue_name" {
  description = "SQS queue name that receives experiment submissions from `agentlab cloud submit`."
  type        = string
  default     = "agentlab-experiments"
}

variable "image_tag" {
  description = "Tag of the `agentlab` ECR image the ECS task definitions run. scripts/build_and_push_image.sh (Task 6) pushes and tags images with the git short SHA; override with -var image_tag=<sha> at apply time to roll out a new build."
  type        = string
  default     = "latest"
}

variable "telegram_chat_id" {
  description = "Daniel's Telegram chat/user id. The webhook Lambda accepts taps from this id only. Not a secret."
  type        = string
  default     = "6309668956"
}

variable "video_image_tag" {
  description = "Tag of the `agentlab` ECR image the explain task definition runs (same repo as image_tag, different image - it carries the render toolchain from Dockerfile.video). scripts/build_and_push_video_image.sh (Task 6) pushes and tags images with '<git short sha>-video'; override with -var video_image_tag=<tag> at apply time to roll out a new build."
  type        = string
  default     = "latest-video"
}
