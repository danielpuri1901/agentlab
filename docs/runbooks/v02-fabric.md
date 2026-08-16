# v0.2 fabric runbook

## What exists

The cloud experiment fabric in eu-west-1: SQS queue -> EventBridge Pipe -> Step Functions Standard -> Fargate (ARM64) -> DynamoDB state + S3 results, all Terraform-managed in `infra/`, with a $50/month budget alarm.
Idle cost is ~$0: nothing runs between experiments.

## Deploy or update

1. `cd infra && terraform apply` (provider creds come from your `aws login` session via `eval "$(aws configure export-credentials --format env)"`).
2. After code changes: `./scripts/build_and_push_image.sh` builds, pushes, and pins the new image tag in `infra/image_tag.auto.tfvars`; then `terraform apply` again to point the task definitions at it.

## Run an experiment

```
export AGENTLAB_QUEUE_URL=$(terraform -chdir=infra output -raw experiments_queue_url)
export AWS_REGION=eu-west-1
uv run agentlab cloud submit --model bedrock/eu.amazon.nova-lite-v1:0 --tasks 51 --repeats 5 \
  --baseline-style structured --candidate-style codes_first \
  --n-facts 12 --filler-turns 120 --summary-budget 150 --max-connections 30
uv run agentlab cloud status <experiment-id>
uv run agentlab cloud report <experiment-id>
```

The submit/status/report commands run under your laptop session; the containers use the task role (no credentials shipped).
Laptop sessions hard-expire after ~6h; re-run `aws login` when boto3 reports expired credentials.

## Debugging surfaces, in order

1. `cloud status` (DynamoDB audit trail).
2. Step Functions console: the execution graph shows which state failed.
3. The pipe's error log group `/pipes/agentlab-experiments` (pipe-side failures).
4. Container logs: `aws logs tail /ecs/agentlab-arm-runner --since 30m`.
5. The DLQ `agentlab-experiments-dlq` (messages that failed 5 deliveries).
Timezone note: console timestamps are local (+02:00); the audit trail is UTC.

## Cost hygiene

- No NAT gateway, no load balancers, no idle compute; verify with `aws ecs list-tasks --cluster agentlab --desired-status RUNNING` returning empty between runs.
- Log groups have 30-day retention; the budget alarm emails at 80% of $50.
- Known deferred items: DLQ visibility timeout is the 30s default until operator tooling consumes it; `cloud status/report` bucket overrides are not cross-checked against the deployed fabric; `fetch_transitions` is unpaginated (fine at current item counts).

## Teardown

`cd infra && terraform destroy` removes everything; the S3 bucket must be emptied first if reports should not be retained.

## Reproduction record

2026-08-16: experiment 001 (51 tasks x 5 repeats, Nova Lite) reproduced on the fabric: PROMOTE, delta +0.2761, CI [0.2261, 0.3262] vs local PROMOTE, +0.2680, CI [0.207, 0.329] - cloud delta inside the local CI, criterion met.
