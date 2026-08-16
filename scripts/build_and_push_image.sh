#!/usr/bin/env bash
# Builds the ARM64 worker image and pushes it to ECR, tagged with the current
# git short SHA.
#
# Login/push sequence verified against
# https://docs.aws.amazon.com/cli/latest/reference/ecr/get-login-password.html
# and
# https://docs.aws.amazon.com/AmazonECR/latest/userguide/docker-push-ecr-image.html
# on 2026-08-16.
#
# Requires: docker buildx, the aws CLI, and AWS credentials for the account
# that owns the `agentlab` ECR repository (infra/ecr.tf).
#
# NOT run as part of Task 3 - this script makes live AWS/ECR calls and is
# first exercised in Task 6.
#
# After a successful push, writes infra/terraform.tfvars pinning image_tag to the tag
# just pushed. infra/variables.tf's image_tag defaults to "latest", but this script never
# pushes a mutable :latest tag (only git-short-SHA tags, for reproducibility - a given
# commit always maps to exactly one image), so a plain `terraform apply` with no override
# would otherwise reference an image that doesn't exist, or silently redeploy whatever
# stale image last happened to be tagged :latest. infra/.gitignore ignores
# terraform.tfvars - see infra/README.md for the full mechanism.

set -euo pipefail

AWS_REGION="${AWS_REGION:-eu-west-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}"
ECR_REPOSITORY="${ECR_REPOSITORY:-agentlab}"
IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD)}"

ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
IMAGE_URI="${ECR_REGISTRY}/${ECR_REPOSITORY}:${IMAGE_TAG}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Logging in to ${ECR_REGISTRY}..."
aws ecr get-login-password --region "${AWS_REGION}" \
    | docker login --username AWS --password-stdin "${ECR_REGISTRY}"

echo "Building and pushing ${IMAGE_URI}..."
docker buildx build \
    --platform linux/arm64 \
    --file "${REPO_ROOT}/Dockerfile" \
    --tag "${IMAGE_URI}" \
    --push \
    "${REPO_ROOT}"

echo "Pushed ${IMAGE_URI}"

TFVARS_FILE="${REPO_ROOT}/infra/terraform.tfvars"
printf 'image_tag = "%s"\n' "${IMAGE_TAG}" > "${TFVARS_FILE}"
echo "Wrote ${TFVARS_FILE} (image_tag = ${IMAGE_TAG}) - plain 'terraform apply' now picks up this build."
