#!/usr/bin/env bash
# Builds the ARM64 explain-track (video render) image and pushes it to the
# same `agentlab` ECR repository as the main image, tagged video-<git-sha>
# so the two images can never collide
# on a tag and a plain `terraform apply` always knows which is which.
#
# Clone of build_and_push_image.sh (see that script for the login/push
# sequence citations); the only differences are -f Dockerfile.video, the
# "video-" tag prefix, and the .tfvars file/variable name this writes.
#
# Requires: docker buildx, the aws CLI, and AWS credentials for the account
# that owns the `agentlab` ECR repository (infra/ecr.tf).
#
# NOT run as part of Task 6's code-only work - this script makes live
# AWS/ECR calls and is first exercised by the controller's deploy step.
#
# After a successful push, writes infra/video_image_tag.auto.tfvars pinning
# video_image_tag to the tag just pushed. infra/variables.tf's
# video_image_tag defaults to "latest-video", but this script never pushes
# a mutable :latest-video tag (only video-<git-short-SHA> tags, for the same
# reproducibility reason as the main image), so a plain `terraform apply`
# with no override would otherwise reference an image that doesn't exist,
# or silently redeploy whatever stale image last happened to be tagged
# :latest-video. infra/.gitignore already ignores *.auto.tfvars-shaped
# build artifacts for the main image; see infra/README.md for the
# mechanism this mirrors.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -n "$(git -C "${REPO_ROOT}" status --porcelain)" ]]; then
    echo "error: commit or discard repository changes before building" >&2
    exit 1
fi

AWS_REGION="${AWS_REGION:-eu-west-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}"
ECR_REPOSITORY="${ECR_REPOSITORY:-agentlab}"
IMAGE_TAG="${IMAGE_TAG:-video-$(git -C "${REPO_ROOT}" rev-parse --short HEAD)}"

ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
IMAGE_URI="${ECR_REGISTRY}/${ECR_REPOSITORY}:${IMAGE_TAG}"

echo "Logging in to ${ECR_REGISTRY}..."
aws ecr get-login-password --region "${AWS_REGION}" \
    | docker login --username AWS --password-stdin "${ECR_REGISTRY}"

echo "Building and pushing ${IMAGE_URI}..."
docker buildx build \
    --platform linux/arm64 \
    --file "${REPO_ROOT}/Dockerfile.video" \
    --tag "${IMAGE_URI}" \
    --push \
    "${REPO_ROOT}"

echo "Pushed ${IMAGE_URI}"

TFVARS_FILE="${REPO_ROOT}/infra/video_image_tag.auto.tfvars"
printf 'video_image_tag = "%s"\n' "${IMAGE_TAG}" > "${TFVARS_FILE}"
echo "Wrote ${TFVARS_FILE} (video_image_tag = ${IMAGE_TAG}) - plain 'terraform apply' now picks up this build."
