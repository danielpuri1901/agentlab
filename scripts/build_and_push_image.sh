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
