#!/usr/bin/env bash
# Deploy the images GitHub Actions built for the current commit.
#
# The laptop never builds images. .github/workflows/build-images.yml builds
# and pushes app-<sha7> and video-<sha7> for every push to main. This script
# checks that both tags exist in ECR, pins them in
# infra/image_tag.auto.tfvars and infra/video_image_tag.auto.tfvars, and runs
# terraform apply. Run it from the main checkout, where the Terraform state
# lives. Extra arguments go to terraform apply.
#
# Usage: AWS_PROFILE=agentlab scripts/deploy_ci_images.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export AWS_REGION="${AWS_REGION:-eu-west-1}"
SHA="$(git -C "${REPO_ROOT}" rev-parse HEAD | cut -c1-7)"

for prefix in app video; do
    if ! aws ecr describe-images --repository-name agentlab \
        --image-ids "imageTag=${prefix}-${SHA}" >/dev/null 2>&1; then
        echo "error: ${prefix}-${SHA} is not in ECR. Push this commit to main and wait for" >&2
        echo "the build-images workflow: gh run list --workflow build-images.yml" >&2
        exit 1
    fi
done

printf 'image_tag = "app-%s"\n' "${SHA}" > "${REPO_ROOT}/infra/image_tag.auto.tfvars"
printf 'video_image_tag = "video-%s"\n' "${SHA}" > "${REPO_ROOT}/infra/video_image_tag.auto.tfvars"
echo "Pinned app-${SHA} and video-${SHA}."
terraform -chdir="${REPO_ROOT}/infra" apply "$@"
