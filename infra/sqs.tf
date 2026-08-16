# EventBridge Pipes SQS-source guidance verified against
# https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-pipes-sqs.html
# on 2026-08-16.
#
# Visibility timeout: the doc says "set the source queue's visibility timeout to
# at least six times the combined runtime of the pipe enrichment and target
# components." Our target (built in Task 4) is a Step Functions STANDARD
# workflow, and Pipes can only invoke a STANDARD workflow asynchronously
# (FIRE_AND_FORGET) - REQUEST_RESPONSE/synchronous invocation is explicitly not
# supported for STANDARD workflows, per
# https://docs.aws.amazon.com/cli/latest/reference/pipes/create-pipe.html. That
# means the pipe's own hand-off is just the StartExecution API call returning -
# on the order of seconds, not the ~45-minute per-arm execution that follows it
# and that the queue never waits on. A literal 6x of that hand-off would be well
# under a minute. 900s (15 min) is instead a deliberately generous fixed buffer:
# it comfortably absorbs Pipes-side throttling retries and headroom for any
# future enrichment step, without needing to track downstream execution
# duration, while staying far under the 12-hour SQS maximum.
#
# Redrive: the same page recommends maxReceiveCount >= 5 ("to give messages more
# chances to be processed before sending them to the dead-letter queue"). This
# queue deliberately keeps maxReceiveCount=3 per the plan instead: our failure
# modes here are hand-off errors (bad IAM, malformed message, throttling), not
# variable-duration processing, so a bad experiment spec should reach the DLQ
# for triage sooner rather than being retried five times first.
resource "aws_sqs_queue" "experiments_dlq" {
  name                      = "${var.experiments_queue_name}-dlq"
  message_retention_seconds = 1209600 # 14 days: the SQS maximum, giving the most time to notice and redrive failures
}

resource "aws_sqs_queue" "experiments" {
  name                       = var.experiments_queue_name
  visibility_timeout_seconds = 900

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.experiments_dlq.arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "experiments_dlq" {
  queue_url = aws_sqs_queue.experiments_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.experiments.arn]
  })
}
