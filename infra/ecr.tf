# Resource shapes verified against
# https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/ecr_repository
# and
# https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/ecr_lifecycle_policy
# on 2026-08-16.
#
# Runtime and video images share this repository but use separate tag prefixes.
# Their lifecycle rules count each class independently, so frequent video builds
# cannot delete the image used by the proposer and experiment workers.
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
        rulePriority = 2
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
        rulePriority = 3
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
