# Bedrock credit smoke test

## Purpose

Verify which model families' Bedrock usage draws from the $1,000 AWS promotional credits.

AWS Marketplace charges are explicitly excluded from promotional credit coverage per the AWS Promotional Credit Terms.
Claude models on Bedrock are delivered as AWS Marketplace SaaS products.
Amazon Nova is first-party and bills as Amazon Bedrock service usage, so Nova is certain to draw from credits.
Claude credit coverage varies by credit grant and must be tested empirically before committing the experiment design to any Claude tier.

This runbook invokes Nova Lite once (its first-party credit coverage is near-certain, so one call is enough) and each of Claude Haiku 4.5 and Claude Sonnet 4.6 repeatedly with larger-than-normal requests, deriving enough repeats per Claude family to produce a real, attributable charge without needing a large dollar total.
The credit-coverage question this test answers is qualitative: which billing entity (AWS vs AWS Marketplace) a charge lands under, not how big the charge is.
Cost Explorer's CSV export and API views show sub-dollar amounts at full precision, so a small total answers the question just as well as a large one.
It then waits for the bill to settle and checks Cost Explorer to see which charges drew from credits.
If Claude usage lands under AWS Marketplace and is not offset by credits, the effective model budget for Claude collapses to zero and the experiment design must move to Amazon Nova.

This single test changes the plan by a factor of 33, so it is worth doing before writing any experiment configuration.

See `docs/research/bedrock-model-pricing.md` section 2.9 for the full credit-eligibility background.

## Runbook

### Prerequisites

- AWS Account with $1,000 promotional credits enabled and visible on the Credits page.
- Bedrock model access provisioned for Nova Lite, Claude Haiku 4.5, and Claude Sonnet 4.6 (verify via the AWS Bedrock console).
- AWS CLI credentials configured with `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` in the environment.
- Region comes from your ambient AWS CLI/SDK configuration.
  This script sets no default and does not override `AWS_REGION`.
  The project standard is `eu-west-1`, used with the global inference-profile endpoint for Claude, which carries no regional pricing premium and routes worldwide (see `docs/research/bedrock-model-pricing.md` section 5.2).
- Verify the model ids this script uses are actually provisioned in your account before running: `aws bedrock list-inference-profiles --region eu-west-1` (substitute your project's standard region if different).
  Confirmed live against a real account on 2026-08-16: look for `global.anthropic.claude-sonnet-4-6`, `global.anthropic.claude-haiku-4-5-20251001-v1:0`, and `eu.amazon.nova-lite-v1:0` (Nova Lite has no global profile, so it uses the eu regional one instead).
Note 2026-08-16: the Claude 5 family (`global.anthropic.claude-sonnet-5`) returned AccessDeniedException "not available for this account"; it is sales-gated, so Sonnet 4.6 is the frontier tier until that changes.
  Re-run this check before any future live run, since account provisioning can change.
  If a Claude id is missing, edit `scripts/credit_smoke.py`'s `MODELS` list to use a different region-prefixed id (`eu.anthropic.*` or `us.anthropic.*`), or fall back to the bare ids listed in the comment above `MODELS` in that file.

### Step 1: Run the smoke test

From the agentlab repository root:

```bash
uv run python scripts/credit_smoke.py
```

Expected output: a spend plan printed first (per-family call count and expected cost, before any request is sent), followed by actual input and output token counts and actual cost per model family as each family's calls complete, then a grand total.
Nova Lite makes exactly one call.
Each Claude family makes however many ~8,000-input-token calls its per-call cost requires to reach roughly $0.30 of expected spend (see `TARGET_DOLLARS_PER_CLAUDE_FAMILY` in `scripts/credit_smoke.py`), which at current Bedrock rates derives to roughly 15-30 calls per Claude family.
The grand total should land in the range of roughly $0.60-0.80: small on purpose, since the credit-coverage question is which billing entity a charge appears under, not how large the charge is.

### Step 2: Wait for billing to settle

AWS Cost Explorer and the Credits page typically update 24-48 hours after incurring charges.
Do not proceed until charges appear.

### Step 3: Verify credit coverage in Cost Explorer

Open the AWS Cost Explorer console.
Read exact amounts via Cost Explorer's CSV export or the Cost Explorer API rather than relying on the console UI alone: the UI rounds sub-dollar charges, and this test's total is deliberately sub-dollar.
Group by billing entity (AWS vs AWS Marketplace) and by service: that grouping, not the dollar amount, is the actual answer to the credit-coverage question, so get it from the CSV/API export even if the UI shows the total as $0.00.

#### 3a. Check service breakdown

Group by **Service**.
Filter by date range to include the hours when the smoke test ran.
Record whether Bedrock and AWS Marketplace line items appeared, and whether their charges are zero after credits applied.

#### 3b. Check Marketplace split

Add a second grouping by **Purchase Type** (or filter by AWS Marketplace if that option is available).
Record whether Claude line items appear under AWS Marketplace.

### Step 4: Fill in the results table

For each model family, record:
- Family name (Nova Lite, Haiku 4.5, Sonnet 4.6)
- Which service or purchase type the charge appeared under (Bedrock, AWS Marketplace, etc.)
- Whether the charge was fully offset by promotional credits (yes/no)

| Family | Billed via | Credit-covered |
|--------|-----------|-----------------|
| Nova Lite | | |
| Haiku 4.5 | | |
| Sonnet 4.6 | | |

### Step 5: Surface results and decide next steps

If all three model families are credit-covered, the experiment design can proceed as-is.
If Claude models (Haiku 4.5 or Sonnet 4.6) are not credit-covered or appear under AWS Marketplace without offset, notify the team.
The model selection strategy in `docs/research/bedrock-model-pricing.md` section 5 will need to be reworked to rely on Amazon Nova (which is certain to be credit-covered).

## Exact verification command (API, full precision)

Run this 24-48h after the smoke test (adjust dates):

```bash
aws ce get-cost-and-usage --time-period Start=2026-08-16,End=2026-08-18 \
  --granularity DAILY --metrics UnblendedCost \
  --group-by Type=DIMENSION,Key=BILLING_ENTITY Type=DIMENSION,Key=SERVICE
```

A Claude charge under billing entity "AWS Marketplace" means it is excluded from promotional credits per the credit terms.
A charge under billing entity "AWS" draws from credits.
First attempted 2026-08-16 ~13:45 UTC: data not yet propagated (expected lag up to 24h).

