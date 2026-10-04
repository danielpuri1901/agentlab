# Runbook: taste flywheel

Spec: `docs/superpowers/specs/2026-10-02-taste-flywheel-design.md`.
Plan: `docs/superpowers/plans/2026-10-02-taste-flywheel.md`.

## What runs where

| Piece | Schedule | Task definition | Command |
|---|---|---|---|
| Proposer | 09:30 Europe/Amsterdam daily | `agentlab-proposer` | `worker propose` |
| Consolidate | 18:00 Europe/Amsterdam Sunday | `agentlab-proposer` | `worker consolidate` |
| Approval video | on APPROVE tap | `agentlab-explain` | `worker explain` with `PID`, `EXPLAIN_URL`, `EXPLAIN_TITLE` |

The profile pointer is the DynamoDB item `profile#current` / `profile` on `agentlab-state`.
Profile versions live in S3 under `profile/<version>.md`.
Every candidate, applied or not, lands under `profile/candidates/`.
Day records of what the proposer saw live under `proposals/days/<date>/sources.json`.

## Local runs

Local boto3 needs the `agentlab` AWS profile with its credential_process.
The plain `aws login` session is visible to the CLI but not to boto3.

```bash
export AWS_PROFILE=agentlab AWS_DEFAULT_REGION=eu-west-1
export STATE_TABLE=agentlab-state RESULTS_BUCKET=agentlab-results-891377302765
export PROPOSER_MODEL=bedrock/arn:aws:bedrock:eu-west-1:891377302765:application-inference-profile/kpbqsnaqf2ti
export CONSOLIDATE_MODEL=bedrock/arn:aws:bedrock:eu-west-1:891377302765:application-inference-profile/mfzwa25maf8z
uv run agentlab worker consolidate --dry-run
uv run agentlab worker consolidate
```

The dry run writes nothing and pings nobody.
The real run writes the version, flips the pointer, and pings with a REVERT button.
The dry run on 2026-10-02 took 7 minutes 20 seconds for 1 Sonnet call and about 160 Haiku probes.

## Deploy

1. Make sure `main` holds the code you want and the checkout is clean.
2. Push to main and wait for the `build-images` workflow.
   GitHub Actions builds and pushes both images; the laptop never builds them.
   `scripts/deploy_ci_images.sh` pins their tags and runs `terraform apply` from the main checkout.

3. In the checkout that holds `infra/terraform.tfstate`, make sure these gitignored files exist.

```
infra/image_tag.auto.tfvars         image_tag = "app-<sha>"
infra/video_image_tag.auto.tfvars   video_image_tag = "video-<sha>"
infra/runtime.auto.tfvars           proposer_model, deep_read_model, pick_model as inference profile ARNs
```

4. Run `terraform plan` inside `infra`, read it, then `terraform apply`.
5. Run the first consolidate by hand with the local run above, or with `aws ecs run-task` on `agentlab-proposer` and the command override `["worker", "consolidate"]`.
6. Check the phone for a "Taste profile update" ping with a REVERT button.
7. Next morning, the 09:30 proposer message shows `[FOUNDATIONAL]`, `[FRONTIER]`, or `[IMPLEMENT]` tags and real titles.

## Rollback

- Profile: tap REVERT on the latest consolidate ping, or set the pointer item's `s3_key` and `version` back by hand.
- Code: `terraform apply` with the previous image tags in the tfvars files.
