# Security

Do not open a public issue for a possible credential leak.

Use GitHub's private vulnerability reporting feature from the Security tab.
Include the affected file path, commit, and secret type.
Do not include the complete secret value.

Runtime secrets belong in AWS Systems Manager Parameter Store.
Local Terraform values belong in `infra/runtime.auto.tfvars`.
Git ignores that file.
