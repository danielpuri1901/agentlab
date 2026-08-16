# Table shape is a binding contract with src/agentlab/worker.py's transition():
# PK "experiment_id" (S), SK "sk" (S). On-demand billing (PAY_PER_REQUEST) per the
# plan - experiment volume is low and bursty, so there is nothing to provision or
# tune, and idle cost is zero.
resource "aws_dynamodb_table" "state" {
  name         = var.state_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "experiment_id"
  range_key    = "sk"

  attribute {
    name = "experiment_id"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }
}
