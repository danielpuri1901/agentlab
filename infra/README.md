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
- `ecr.tf` - the `agentlab` ECR repository (scan on push) and a lifecycle policy keeping the last 10 images, tagged with the git short SHA by `scripts/build_and_push_image.sh`.
- `.gitignore` - ignores `.terraform/`, Terraform state files, and `image_tag.auto.tfvars` (see "image_tag bootstrap" below); the `.terraform.lock.hcl` provider lock file is committed as normal.

## image_tag bootstrap

`variables.tf`'s `image_tag` defaults to `"latest"`, but `scripts/build_and_push_image.sh` deliberately never pushes a mutable `:latest` tag to ECR - only immutable git-short-SHA tags, one per commit, so a given image build is always reproducible from the commit that produced it.
That means a plain `terraform apply` with no override would try to run an image tag (`:latest`) that was never pushed, or - worse, if `:latest` happened to exist from some earlier manual push - silently redeploy stale task definitions without anyone noticing.

The mechanism that prevents this: every successful run of `scripts/build_and_push_image.sh` writes `infra/image_tag.auto.tfvars` with `image_tag = "<git short sha>"` for the commit it just built and pushed.
Terraform automatically loads `image_tag.auto.tfvars` from the working directory on every plan/apply, with no `-var` flag needed, so a plain `terraform apply` after a push always picks up the exact image that was just built - never `:latest`, never a stale pin.
`image_tag.auto.tfvars` is gitignored on purpose: it is a local build artifact recording "what did I last push," not a checked-in value, and it will differ between whoever last ran the build script.

## What does not exist yet

No compute, no IAM roles beyond what the budget resource needs, no Step Functions, no EventBridge Pipe.
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
