# GitHub Actions builds both images on every push to main and pushes them to
# ECR (.github/workflows/build-images.yml). The laptop never builds images.
#
# The workflow signs in with a short-lived OIDC token from GitHub, so no AWS
# key is stored in GitHub. Only a workflow running on the main branch of this
# repository can assume the role: pull requests and forks get a different
# token subject and are refused. The role can only push and pull images in
# the agentlab repository.
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

resource "aws_iam_role" "github_images" {
  name = "agentlab-github-images"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = "sts:AssumeRoleWithWebIdentity"
        Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
            # GitHub names the repo by owner and repo ids as well as names
            # (seen in CloudTrail, 2026-10-04), so a deleted and re-created
            # repo with the same name cannot assume this role.
            "token.actions.githubusercontent.com:sub" = "repo:danielpuri1901@123932678/agentlab@1335976071:ref:refs/heads/main"
          }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "github_images" {
  name = "push-agentlab-images"
  role = aws_iam_role.github_images.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # GetAuthorizationToken has no resource-level permissions.
        Sid      = "EcrLogin"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        # Push the image and the build cache, and pull the cache back.
        Sid    = "EcrPushPull"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:CompleteLayerUpload",
          "ecr:GetDownloadUrlForLayer",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart",
        ]
        Resource = aws_ecr_repository.agentlab.arn
      },
    ]
  })
}

output "github_images_role_arn" {
  value = aws_iam_role.github_images.arn
}
