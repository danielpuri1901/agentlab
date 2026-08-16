# Resource shapes verified against
# https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/ecr_repository
# and
# https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/ecr_lifecycle_policy
# on 2026-08-16.
#
# Images are tagged with the git short SHA (see scripts/build_and_push_image.sh),
# not a shared prefix like "v", so the lifecycle rule matches tagStatus "any"
# rather than filtering on a tag prefix.
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
        description  = "Keep last 10 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}
