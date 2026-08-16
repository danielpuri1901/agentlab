# Bedrock credit smoke test

## Purpose

Verify which model families' Bedrock usage draws from the $1,000 AWS promotional credits.

AWS Marketplace charges are explicitly excluded from promotional credit coverage per the AWS Promotional Credit Terms.
Claude models on Bedrock are delivered as AWS Marketplace SaaS products.
Amazon Nova is first-party and bills as Amazon Bedrock service usage, so Nova is certain to draw from credits.
Claude credit coverage varies by credit grant and must be tested empirically before committing the experiment design to any Claude tier.

This runbook invokes Nova Lite, Claude Haiku 4.5, and Claude Sonnet 5 for a few dollars each, waits for the bill to settle, then checks Cost Explorer to see which charges drew from credits.
If Claude usage lands under AWS Marketplace and is not offset by credits, the effective model budget for Claude collapses to zero and the experiment design must move to Amazon Nova.

This single test changes the plan by a factor of 33, so it is worth doing before writing any experiment configuration.

See `docs/research/bedrock-model-pricing.md` section 2.9 for the full credit-eligibility background.

## Runbook

### Prerequisites

- AWS Account with $1,000 promotional credits enabled and visible on the Credits page.
- Bedrock model access provisioned for Nova Lite, Claude Haiku 4.5, and Claude Sonnet 5 (verify via the AWS Bedrock console).
- AWS CLI credentials configured with `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` in the environment.
- Default region set to `us-east-1` or override with `AWS_REGION=us-east-1` (models are priced the same on us-east-1 globally and regional endpoints).

### Step 1: Run the smoke test

From the agentlab repository root:

```bash
uv run python scripts/credit_smoke.py
```

Expected output: three successful API calls per model (Nova Lite, Haiku 4.5, Sonnet 5), with actual input and output token counts and expected cost per model family printed to stdout.
Total expected cost should be under $1.

### Step 2: Wait for billing to settle

AWS Cost Explorer and the Credits page typically update 24-48 hours after incurring charges.
Do not proceed until charges appear.

### Step 3: Verify credit coverage in Cost Explorer

Open the AWS Cost Explorer console.

#### 3a. Check service breakdown

Group by **Service**.
Filter by date range to include the hours when the smoke test ran.
Record whether Bedrock and AWS Marketplace line items appeared, and whether their charges are zero after credits applied.

#### 3b. Check Marketplace split

Add a second grouping by **Purchase Type** (or filter by AWS Marketplace if that option is available).
Record whether Claude line items appear under AWS Marketplace.

### Step 4: Fill in the results table

For each model family, record:
- Family name (Nova Lite, Haiku 4.5, Sonnet 5)
- Which service or purchase type the charge appeared under (Bedrock, AWS Marketplace, etc.)
- Whether the charge was fully offset by promotional credits (yes/no)

| Family | Billed via | Credit-covered |
|--------|-----------|-----------------|
| Nova Lite | | |
| Haiku 4.5 | | |
| Sonnet 5 | | |

### Step 5: Surface results and decide next steps

If all three model families are credit-covered, the experiment design can proceed as-is.
If Claude models (Haiku 4.5 or Sonnet 5) are not credit-covered or appear under AWS Marketplace without offset, notify the team.
The model selection strategy in `docs/research/bedrock-model-pricing.md` section 5 will need to be reworked to rely on Amazon Nova (which is certain to be credit-covered).
