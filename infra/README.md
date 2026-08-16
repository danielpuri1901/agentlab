# infra

Terraform foundation for AgentLab's AWS fabric (v0.2).
This root manages the account-level cost guardrail and the two data stores the cloud worker commands already depend on.
State is local (no S3 backend) for v0.2.
Nothing here has been applied yet.

## What exists

- `main.tf` - provider configuration: AWS provider pinned to `~> 6.0`, region `eu-west-1`, `default_tags` applying `project = agentlab` to every resource.
- `variables.tf` - input variables with defaults matching the contracts below.
- `outputs.tf` - the table name, bucket name, and queue URL/ARN that later tasks (ECS task definitions, the cloud CLI) will consume.
- `budget.tf` - an `aws_budgets_budget` monthly cost budget of $50, emailing an ACTUAL (not forecasted) spend alert at 80% to danielpuri1901@gmail.com.
- `dynamodb.tf` - the `agentlab-state` table (PK `experiment_id`, SK `sk`, both String, on-demand billing), matching the shape `src/agentlab/worker.py` already reads and writes.
- `s3.tf` - the `agentlab-results-891377302765` bucket: versioning explicitly off, all public access blocked, and a lifecycle rule that aborts abandoned multipart uploads after 7 days.
- `sqs.tf` - the `agentlab-experiments` queue plus its dead-letter queue, with a redrive policy and the visibility-timeout reasoning cited inline.
- `.gitignore` - ignores `.terraform/` and Terraform state files; the `.terraform.lock.hcl` provider lock file is committed as normal.

## What does not exist yet

No compute, no IAM roles beyond what the budget resource needs, no ECR, no Step Functions, no EventBridge Pipe.
Those arrive in later tasks once this foundation is reviewed.

## How to validate

Run these from inside `infra/`:

```
terraform init
terraform fmt -check
terraform validate
```

None of these commands touch real AWS resources.
`terraform init` only downloads the AWS provider binary.
Do not run `terraform apply` from this task - that is gated to a later, explicitly-approved deploy task.

## Notes on specific values

The AWS provider major version, the budget notification schema, and the SQS visibility-timeout reasoning were each verified against current AWS/Terraform registry docs at the time of writing.
See the comments in `main.tf`, `budget.tf`, and `sqs.tf` for the exact URLs and reasoning.
