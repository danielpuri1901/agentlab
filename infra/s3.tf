resource "aws_s3_bucket" "results" {
  bucket = var.results_bucket_name
}

# Versioning is explicitly off per the plan: every object key this bucket ever
# receives is deterministic (experiments/<id>/logs/<arm>.eval,
# experiments/<id>/report.md - see src/agentlab/worker.py) and written exactly
# once by the worker, so there is no overwrite-and-recover use case that would
# justify paying to retain old versions.
#
# "Disabled" (rather than omitting this resource) is the value the provider docs
# recommend for declaring an unversioned bucket explicitly, per
# https://raw.githubusercontent.com/hashicorp/terraform-provider-aws/main/website/docs/r/s3_bucket_versioning.html.markdown:
# "'Disabled' should only be used when creating or importing resources that
# correspond to unversioned S3 buckets."
resource "aws_s3_bucket_versioning" "results" {
  bucket = aws_s3_bucket.results.id
  versioning_configuration {
    status = "Disabled"
  }
}

resource "aws_s3_bucket_public_access_block" "results" {
  bucket = aws_s3_bucket.results.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# With versioning off, objects never become "noncurrent" - that concept only
# applies once versioning has been enabled at some point - so a
# noncurrent_version_expiration rule here would be a permanent no-op and is
# deliberately omitted. The lifecycle rule that IS meaningful for an unversioned
# bucket is cleaning up abandoned multipart uploads (e.g. a worker task killed
# mid-upload of an EvalLog), which otherwise sit in the bucket accruing storage
# cost with no expiration of their own.
resource "aws_s3_bucket_lifecycle_configuration" "results" {
  bucket = aws_s3_bucket.results.id

  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}
