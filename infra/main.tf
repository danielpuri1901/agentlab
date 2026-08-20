terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # Major version verified against the registry's JSON API
      # (https://registry.terraform.io/v1/providers/hashicorp/aws) on 2026-08-16:
      # latest published version was 6.60.0, so major version 6 is current.
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      project = var.project
    }
  }
}
