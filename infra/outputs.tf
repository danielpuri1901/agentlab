output "state_table_name" {
  description = "DynamoDB table name - the STATE_TABLE env var value for worker tasks."
  value       = aws_dynamodb_table.state.name
}

output "state_table_arn" {
  description = "DynamoDB table ARN, for scoping IAM policies in later tasks."
  value       = aws_dynamodb_table.state.arn
}

output "results_bucket_name" {
  description = "S3 bucket name - the RESULTS_BUCKET env var value for worker tasks."
  value       = aws_s3_bucket.results.id
}

output "results_bucket_arn" {
  description = "S3 bucket ARN, for scoping IAM policies in later tasks."
  value       = aws_s3_bucket.results.arn
}

output "experiments_queue_url" {
  description = "SQS queue URL that `agentlab cloud submit` (Task 5) sends experiment specs to."
  value       = aws_sqs_queue.experiments.url
}

output "experiments_queue_arn" {
  description = "SQS queue ARN, for the EventBridge Pipe source (Task 4)."
  value       = aws_sqs_queue.experiments.arn
}

output "experiments_dlq_url" {
  description = "Dead-letter queue URL, for operator inspection of failed submissions."
  value       = aws_sqs_queue.experiments_dlq.url
}

output "experiments_dlq_arn" {
  description = "Dead-letter queue ARN."
  value       = aws_sqs_queue.experiments_dlq.arn
}

output "ecr_repository_url" {
  description = "ECR repository URL - the push/pull target for scripts/build_and_push_image.sh."
  value       = aws_ecr_repository.agentlab.repository_url
}

output "ecr_repository_arn" {
  description = "ECR repository ARN, for scoping IAM policies in later tasks."
  value       = aws_ecr_repository.agentlab.arn
}

output "ecs_cluster_name" {
  description = "ECS cluster name, for operator visibility (e.g. inspecting running tasks)."
  value       = aws_ecs_cluster.agentlab.name
}

output "state_machine_arn" {
  description = "Step Functions state machine ARN that the EventBridge Pipe starts on each SQS message."
  value       = aws_sfn_state_machine.experiment.arn
}

output "pipe_arn" {
  description = "EventBridge Pipe ARN wiring the experiments queue to the state machine."
  value       = aws_pipes_pipe.experiments.arn
}
