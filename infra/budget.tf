# Cost guardrail, created before any compute infra exists (Global Constraints).
#
# Schema verified against
# https://raw.githubusercontent.com/hashicorp/terraform-provider-aws/main/website/docs/r/budgets_budget.html.markdown
# on 2026-08-16:
# - notification_type: ACTUAL or FORECASTED. The task requires alerting on money
#   actually spent, so ACTUAL (not a forecast) is used.
# - threshold_type PERCENTAGE + threshold 80 + comparison_operator GREATER_THAN
#   means "notify once actual spend this period crosses 80% of limit_amount."
#
# time_period_start/time_period_end are left unset deliberately. Per the AWS
# Budgets API docs
# (https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_budgets_TimePeriod.html):
# an unset start defaults to the beginning of the current MONTHLY period, and an
# unset end defaults to 2087-06-15 UTC - i.e. this budget recurs monthly with no
# practical end date, exactly like the AWS console's default recurring budget.
resource "aws_budgets_budget" "monthly_cost" {
  name         = "${var.project}-monthly-cost"
  budget_type  = "COST"
  limit_amount = var.monthly_budget_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
}
