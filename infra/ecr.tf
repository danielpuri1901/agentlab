# Resource shapes verified against
# https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/ecr_repository
# and
# https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/ecr_lifecycle_policy
# on 2026-08-16.
#
# Runtime and video images share this repository but use separate tag prefixes,
# and every push to main builds one of each. A count rule alone is not safe:
# on 2026-10-23 more than ten merges in one day expired app-cf46ce4 and
# video-cf46ce4 while live task definitions still used them, and the next
# morning's scheduled tasks failed to pull their image without a log line.
# scripts/deploy_ci_images.sh therefore tags the deployed pair live-app and
# live-video, and rule 1 protects them: an image that matches a higher-priority
# rule cannot be expired by a lower one (ECR lifecycle evaluation rules,
# https://docs.aws.amazon.com/AmazonECR/latest/userguide/LifecyclePolicies.html).
resource "aws_ecr_repository" "agentlab" {
  name = "agentlab"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "agentlab" {
  repository = aws_ecr_repository.agentlab.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Never expire the deployed images (live-app, live-video)"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["live-"]
          countType     = "imageCountMoreThan"
          countNumber   = 2
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 2
        description  = "Keep last 10 runtime images"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["app-"]
          countType     = "imageCountMoreThan"
          countNumber   = 10
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 3
        description  = "Keep last 10 video images"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["video-"]
          countType     = "imageCountMoreThan"
          countNumber   = 10
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 4
        description  = "Remove untagged build artifacts after one day"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}
