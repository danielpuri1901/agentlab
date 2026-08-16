# Amazon Bedrock model catalog, pricing and quotas for AgentLab

Research date: 2026-08-16.
Scope: what $1,000 of AWS credits buys in agent-run trials when all model calls go through Amazon Bedrock.
Every price below carries a source URL.
Prices are USD per 1 million tokens unless stated otherwise.

## 1. Summary

The Claude 5 family is live on Bedrock: Fable 5 at $10/$50, Opus 5 at $5/$25, Sonnet 5 at $2/$10, Haiku 4.5 at $1/$5, plus Amazon Nova Lite at $0.06/$0.24.
The biggest risk to the budget is not price, it is credit eligibility: Claude on Bedrock bills through AWS Marketplace, and the AWS Promotional Credit Terms exclude AWS Marketplace charges.
Amazon Nova is first-party and bills as Amazon Bedrock service usage, so Nova is the only model family that is certain to draw from the credits.
Verify credit coverage with a deliberate $5 smoke test per model family before committing the experiment design to any Claude tier.
Prompt caching cuts a medium agent run by about 51% at an 80% cache-read rate, and cache reads do not count against rate-limit quotas.
Caching is not available with the batch inference API, so the two discounts do not stack.
Batch inference is 50% off but the Claude 5 family is not yet listed as batch-supported on Bedrock, so batch is a judging lever, not an agent-loop lever.
Use the global endpoint from the Netherlands: it carries no premium, whereas Bedrock regional and geo endpoints add 10% for Claude, and Nova costs 15% more in eu-west-1 and 31% more in eu-central-1.
At $800 of model spend, one experiment of 2 configs x 30 trials on a medium cached profile costs $1.15 on Nova Lite, $18.90 on Haiku 4.5, $37.80 on Sonnet 5, $94.50 on Opus 5, and $189.00 on Fable 5.
Claude 4.7 and later use a tokenizer that produces about 30% more tokens for the same text, so real costs for Sonnet 5, Opus 5 and Fable 5 run about 30% above the table on a same-text basis.

## 2. Verified findings

### 2.1 Sources and how they were obtained

The Anthropic tables on the Bedrock pricing page are rendered behind JavaScript tabs and did not resolve on fetch.
Modern Claude models are also absent from the AWS Bedrock Price List API, because they are metered through AWS Marketplace rather than as Bedrock service SKUs.
Claude rates below therefore come from AWS Marketplace product pages (AWS primary) and Anthropic's own pricing documentation (vendor primary), which agree with each other.
Amazon Nova, Meta, Mistral and DeepSeek rates come from the AWS Bedrock Price List API, which is the most authoritative machine-readable source available.
Price List API file used: `https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonBedrock/20260813210707/us-east-1/index.json`, version `20260813210707`, publication date 2026-08-13.
Region index: `https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonBedrock/current/region_index.json`.

### 2.2 Claude catalog on Bedrock

Claude Fable 5, Claude Opus 5, Claude Opus 4.8, Claude Opus 4.7, Claude Sonnet 5 and Claude Haiku 4.5 are all available on Bedrock.
Fable 5, Opus 4.8, Opus 4.7, Sonnet 5 and Haiku 4.5 are open to all Bedrock customers, and Claude Mythos Preview is invitation only.
Source: https://platform.claude.com/docs/en/build-with-claude/claude-in-amazon-bedrock
Claude Sonnet 5 launched on Bedrock on 2026-06-30 and Claude Opus 5 on 2026-07-24.
Sources: https://aws.amazon.com/about-aws/whats-new/2026/06/claude-sonnet-5-now-available-on-aws/ and https://aws.amazon.com/about-aws/whats-new/2026/07/claude-opus-5-aws/
Claude Fable 5 launched on Bedrock on 2026-06-09, has a 1M token context window and 128K max output tokens.
Source: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-fable-5.html
Claude Sonnet 5 also has a 1M token context window and 128K max output tokens.
Source: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-5.html
Bedrock now exposes two inference endpoints, `bedrock-runtime` (Invoke and Converse) and `bedrock-mantle` (Anthropic Messages API at `/anthropic/v1/messages`).
Source: https://docs.aws.amazon.com/bedrock/latest/userguide/quotas.html

### 2.3 Tokenizer change, which is a real cost factor

Claude 4.7 and later models use a newer tokenizer that produces approximately 30% more tokens for the same text.
Claude Sonnet 4.6 and earlier models use the previous tokenizer.
Source: https://platform.claude.com/docs/en/about-claude/pricing
This means Sonnet 5, Opus 5 and Fable 5 consume roughly 30% more billable tokens than Haiku 4.5 for identical prompt text.
Any cost table expressed in tokens, including the tables in section 4, understates same-text cost on those three models by about 30%.

### 2.4 Prompt caching

Claude cache pricing uses fixed multipliers on base input price: 5-minute cache write is 1.25x, 1-hour cache write is 2x, and a cache read is 0.1x.
Source: https://platform.claude.com/docs/en/about-claude/pricing
AWS Marketplace confirms the same rates on Bedrock for Claude Sonnet 4.6 ($3.00 input, $0.30 cache read, $3.75 cache write, $6.00 one-hour cache write).
Source: https://aws.amazon.com/marketplace/pp/prodview-o6w4hyizv7g64
AWS Marketplace confirms Claude Opus 4.7 at $5.00 input, $0.50 cache read, $6.25 cache write and $10.00 one-hour cache write.
Source: https://aws.amazon.com/marketplace/pp/prodview-nlhdwhja3uxx6
Fable 5 requires a minimum 1,024 tokens per cache checkpoint, supports 4 checkpoints and both 5-minute and 1-hour TTL.
Source: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-fable-5.html
Sonnet 5 requires a minimum 4,096 tokens per cache checkpoint, supports 4 checkpoints and both TTLs.
Source: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-5.html
Amazon Nova prices cache reads at 25% of the input rate and bills the cache-write SKU at $0.00.
Nova Pro is $0.80 input and $0.20 cache read, Nova Lite is $0.06 input and $0.015 cache read, Nova Micro is $0.035 input and $0.00875 cache read.
Source: AWS Bedrock Price List API, us-east-1, version 20260813210707.
Prompt caching is supported only on on-demand inference endpoints and is not supported with the batch inference API.
Cache checkpoints are processed in the order `tools`, then `system`, then `messages`, and changing an earlier section invalidates the cache for later sections.
Source: https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html
Cached input tokens read through prompt caching do not count against the input-tokens-per-minute quota on the `bedrock-mantle` endpoint.
Source: https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-mantle.html

### 2.5 Batch inference

Bedrock offers batch inference at a 50% discount versus on-demand pricing.
Source: https://aws.amazon.com/bedrock/pricing/
Batch requests process asynchronously within 24 hours and do not count against real-time TPM quotas.
Source: https://aws.amazon.com/blogs/machine-learning/global-cross-region-inference-for-latest-anthropic-claude-opus-sonnet-and-haiku-models-on-amazon-bedrock-in-thailand-malaysia-singapore-indonesia-and-taiwan/
The Bedrock batch-supported model table lists Claude Haiku 4.5, Claude Sonnet 4.6, Claude Opus 4.6, Claude Sonnet 4.5, Claude Opus 4.5 and older, plus Nova Micro, Nova Lite, Nova Pro, Nova Premier and Nova 2 Lite.
It does not list Claude Sonnet 5, Claude Opus 5, Claude Fable 5, Claude Opus 4.7 or Claude Opus 4.8.
Source: https://docs.aws.amazon.com/bedrock/latest/userguide/batch-inference-supported.html
Note a discrepancy: the AWS Marketplace page for Claude Opus 4.7 does publish batch dimensions at $2.50 input and $12.50 output, even though the Bedrock batch documentation does not list Opus 4.7.
Treat the Bedrock documentation as authoritative for whether a Bedrock batch job will accept the model, and confirm in the console before planning around it.
Haiku 4.5 batch is confirmed available at 50% savings.
Source: https://aws.amazon.com/blogs/machine-learning/global-cross-region-inference-for-latest-anthropic-claude-opus-sonnet-and-haiku-models-on-amazon-bedrock-in-thailand-malaysia-singapore-indonesia-and-taiwan/

### 2.6 Service tiers

Bedrock offers Standard, Priority, Flex and Reserved tiers.
Priority is priced at a 75% premium to Standard, and Flex is a 50% discount to Standard.
Source: https://aws.amazon.com/bedrock/pricing/
Claude Fable 5 supports only the Standard tier on Bedrock: Priority, Flex and Reserved are not supported.
Source: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-fable-5.html
Amazon Nova Pro and Nova 2 Lite do expose Flex pricing at 50% of Standard, confirmed in the Price List API (for example `USE1-NovaPro-input-tokens-flex` at $0.40 per 1M against $0.80 Standard).
Flex on Nova is therefore a genuine second discount lever that Claude does not offer.

### 2.7 Quotas

The `bedrock-runtime` endpoint applies per-model quotas: cross-Region tokens per minute, on-demand tokens per minute, max tokens per day, and requests per minute for some models.
TPM on `bedrock-runtime` counts input and output tokens together against one quota.
RPM is model-specific and is not enforced at all for Claude Opus 4.7 and Claude Opus 4.8.
The max-tokens-per-day quota defaults to the per-minute quota multiplied by 1,440, and new AWS accounts might receive reduced quotas.
Source: https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-runtime.html
The `bedrock-mantle` endpoint applies separate input-TPM and output-TPM quotas and enforces no RPM quota at all.
The only published default in that table is Claude Opus 4.7 at 20,000,000 input TPM and 4,000,000 output TPM.
Other models on that endpoint have no per-account TPM quota exposed in Service Quotas today.
Source: https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-mantle.html
Anthropic's own documentation states a different figure for Claude in Amazon Bedrock: a default of 2 million input TPM, increasable to 4 million input TPM without additional Anthropic approval.
Source: https://platform.claude.com/docs/en/build-with-claude/claude-in-amazon-bedrock
These two numbers conflict and neither is reconcilable from public documentation, so treat the real quota as account-specific and read it from the Service Quotas console.
Quota increases on `bedrock-runtime` are requested through the Service Quotas console, and `bedrock-mantle` increases must go through an AWS Support limit-increase case instead.
AWS states that priority for quota increases goes to customers who already consume their existing allocation, and requests may be denied otherwise.
Sources: https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-runtime.html and https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-mantle.html
Output tokens burn down quota at 5x on Claude Opus 4, Claude Sonnet 4.5, Claude Sonnet 4 and Claude 3.7 Sonnet, and 1:1 on all other models.
Source: https://docs.aws.amazon.com/bedrock/latest/userguide/global-cross-region-inference.html

### 2.8 Region availability and the endpoint premium

Bedrock offers global endpoints (dynamic routing worldwide, no pricing premium) and regional endpoints (single region, 10% premium).
Routing across regions inside a geography uses a US, EU, JP or AU inference profile, which also carries the 10% premium.
This pricing structure applies to Claude Sonnet 4.5, Haiku 4.5, Opus 4.5 and all later models.
Sources: https://platform.claude.com/docs/en/build-with-claude/claude-in-amazon-bedrock and https://platform.claude.com/docs/en/about-claude/pricing
The global endpoint is available for Fable 5, Opus 5, Opus 4.8, Opus 4.7, Sonnet 5 and Haiku 4.5.
Claude is listed as available in eu-west-1 (Ireland), eu-central-1 (Frankfurt), eu-north-1 (Stockholm), eu-west-2, eu-west-3, eu-south-1, eu-south-2 and eu-central-2, all with Global and EU endpoint types.
Source: https://platform.claude.com/docs/en/build-with-claude/claude-in-amazon-bedrock
For Fable 5 specifically, the Bedrock model card shows every EU region as Global-only, with no In-Region and no Geo option, while us-east-1 supports all three.
Source: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-fable-5.html
Sonnet 5 does publish an EU geo inference ID, `eu.anthropic.claude-sonnet-5`, alongside the global ID.
Source: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-5.html
Claude Opus 5 was announced as available in Regions including us-east-1, ap-southeast-4, eu-west-1 and eu-north-1.
Source: https://aws.amazon.com/blogs/machine-learning/introducing-claude-opus-5-on-aws-anthropics-most-capable-opus-model/

Amazon Nova has genuine per-region price differences, not a flat 10% endpoint premium.
Measured from the Price List API for version 20260813210707:

| Model | us-east-1 in/out | eu-west-1 in/out | eu-central-1 in/out | EU premium |
| --- | --- | --- | --- | --- |
| Nova Micro | $0.035 / $0.14 | $0.040 / $0.16 | $0.046 / $0.184 | +14% Ireland, +31% Frankfurt |
| Nova Lite | $0.06 / $0.24 | $0.069 / $0.276 | $0.078 / $0.312 | +15% Ireland, +30% Frankfurt |
| Nova Pro | $0.80 / $3.20 | $0.92 / $3.68 | $1.05 / $4.20 | +15% Ireland, +31% Frankfurt |

Nova Premier is present in the us-east-1 price file but absent from both eu-west-1 and eu-central-1.
Llama 4 Scout and Llama 4 Maverick are present in us-east-1 and absent from both EU price files.
Source: AWS Bedrock Price List API, us-east-1 / eu-west-1 / eu-central-1, version 20260813210707.

### 2.9 Do AWS credits actually cover Bedrock usage

This is the single most consequential open risk for the AgentLab budget, and the public evidence is contradictory.
The AWS Promotional Credit Terms, last updated 2024-12-16, state that promotional credit will not be applied to fees or charges for AWS Marketplace, among other ineligible services, unless authorized by AWS.
Source: https://aws.amazon.com/awscredits/
AWS published a startups blog stating that AWS Activate credits are redeemable for third-party models on Amazon Bedrock, naming Anthropic explicitly.
Source: https://aws.amazon.com/blogs/startups/aws-activate-credits-now-accepted-for-third-party-models-on-amazon-bedrock/
Against that, Claude models on Bedrock carry AWS Marketplace product IDs and are delivered as Marketplace SaaS products.
The Bedrock model card for Fable 5 lists Marketplace product ID `prod-h6swdfybvty7y`, and Sonnet 5 lists `prod-4ezhkeia6k2cs`.
Sources: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-fable-5.html and https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-5.html
Corroborating this, modern Claude models do not appear in the AWS Bedrock Price List API at all, while Amazon Nova, Meta, Mistral, DeepSeek, MiniMax, Moonshot, NVIDIA and Google models do.
Only legacy Claude 2.0, Claude 2.1, Claude 3 Sonnet, Claude 3 Haiku and Claude Instant appear as Bedrock service SKUs in that file.
That absence is consistent with Claude being metered as a Marketplace charge rather than as Amazon Bedrock service usage.
Community reports (AWS re:Post, not primary documentation) describe exactly this failure mode in practice: one user reported Claude Opus 4.5 billed under AWS Marketplace and not offset by promotional credits, while Sonnet 4.5, Haiku 4.5 and Opus 4.1 were covered.
Source: https://repost.aws/questions/QUrl2nATrQRWi1g2CV5ra5eA/claude-opus-4-5-is-being-billed-under-aws-marketplace
Another re:Post thread from 2026-06 reports an AWS Activate startup with $1,000 in credits blocked from subscribing to Claude on Bedrock by `INVALID_PAYMENT_INSTRUMENT`, because Marketplace subscription requires a valid payment instrument regardless of credit balance.
Source: https://repost.aws/questions/QUGBeWfZc-R-Sc9n49TzmysA/has-anyone-with-an-aws-india-aispl-account-successfully-subscribed-to-anthropic-claude-on-amazon-bedrock-using-aws-activate-credits
Assessment: Amazon Nova is first-party and bills as Amazon Bedrock service usage, so Nova is credit-eligible with high confidence.
Claude on Bedrock is credit-eligible only if the specific credit grant designates it, and this varies by credit program and apparently by individual model.
This must be tested empirically, not assumed.

### 2.10 Bedrock free tier

Amazon Bedrock has no permanent free tier.
New AWS accounts receive up to $200 in AWS credits, $100 on sign-up and up to $100 more for completing guided activities, and one of those activities is testing a prompt in Amazon Bedrock.
Free Tier credits expire 12 months from account creation, and the Free plan account itself closes after 6 months or when credits run out.
Sources: https://aws.amazon.com/free/ and https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-FAQ.html
Accounts on the Free plan are explicitly not eligible for promotional credits, and the Paid plan is required to use them.
Source: https://aws.amazon.com/free/
A 2026-07-30 re:Post report describes a new Free-plan account where Bedrock TPM and RPM quotas were zero for all models including Nova, and a quota increase request was denied outright.
Source: https://repost.aws/questions/QUAYZgebM3TLuryEwTzCf6uA/aws-free-account-with-200-free-credits-does-not-include-bedrock
This is a real operational risk for AgentLab: an account with zero quota cannot run a single trial regardless of budget.

## 3. Model pricing table

All rates are USD per 1 million tokens, on-demand Standard tier, global endpoint or us-east-1.
Add 10% for Claude on a Bedrock regional or geo endpoint.
Cache read and cache write columns use the 5-minute TTL.

| Model | Input | Output | Cache write (5m) | Cache read | Batch in/out | Context |
| --- | --- | --- | --- | --- | --- | --- |
| Amazon Nova Micro | $0.035 | $0.14 | $0.00 | $0.00875 | $0.0175 / $0.07 | see model card |
| Amazon Nova Lite | $0.06 | $0.24 | $0.00 | $0.015 | $0.03 / $0.12 | see model card |
| Amazon Nova Pro | $0.80 | $3.20 | $0.00 | $0.20 | $0.40 / $1.60 | see model card |
| Amazon Nova Premier | $2.50 | $12.50 | not published | $0.625 | $1.25 / $6.25 | see model card |
| Amazon Nova 2 Lite (global CRIS) | $0.30 | $2.50 | $0.00 | $0.075 | $0.15 / $1.25 | see model card |
| Amazon Nova 2 Pro (global CRIS) | $1.25 | $10.00 | not published | not published | $0.625 / $5.00 | see model card |
| Claude Haiku 4.5 | $1.00 | $5.00 | $1.25 | $0.10 | $0.50 / $2.50 | 200K |
| Claude Sonnet 4.6 | $3.00 | $15.00 | $3.75 | $0.30 | $1.50 / $7.50 | 200K |
| Claude Sonnet 5 | $2.00 | $10.00 | $2.50 | $0.20 | not batch-listed | 1M |
| Claude Opus 4.7 | $5.00 | $25.00 | $6.25 | $0.50 | $2.50 / $12.50 (see 2.5) | 200K |
| Claude Opus 4.8 | $5.00 | $25.00 | $6.25 | $0.50 | not batch-listed | 200K |
| Claude Opus 5 | $5.00 | $25.00 | $6.25 | $0.50 | not batch-listed | 1M |
| Claude Fable 5 | $10.00 | $50.00 | $12.50 | $1.00 | not batch-listed | 1M |
| Llama 4 Scout 17B | $0.17 | $0.66 | not offered | not offered | $0.085 / $0.33 | see model card |
| Llama 4 Maverick 17B | $0.24 | $0.97 | not offered | not offered | $0.12 / $0.485 | see model card |
| Mistral Large 3 | $0.50 | $1.50 | not offered | not offered | not listed | see model card |
| DeepSeek v3.2 | $0.62 | $1.85 | not offered | not offered | not listed | see model card |

Sources for this table:
Nova, Llama, Mistral and DeepSeek rows come from the AWS Bedrock Price List API, us-east-1, version 20260813210707, at `https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonBedrock/20260813210707/us-east-1/index.json`.
DeepSeek v3.2 and Mistral Large 3 are also listed on https://aws.amazon.com/bedrock/pricing/ at the same rates for us-east-1, us-east-2 and us-west-2.
Claude rows come from https://platform.claude.com/docs/en/about-claude/pricing and https://www.anthropic.com/pricing, cross-checked against AWS Marketplace at https://aws.amazon.com/marketplace/pp/prodview-o6w4hyizv7g64 (Sonnet 4.6) and https://aws.amazon.com/marketplace/pp/prodview-nlhdwhja3uxx6 (Opus 4.7).
Claude Fable 5 at $10/$50 with a 90% input discount for prompt caching is confirmed at https://www.anthropic.com/claude/fable.
Claude Opus 5 at $5/$25 is confirmed at https://www.anthropic.com/news/claude-opus-5.
Claude Sonnet 5 at $2/$10 is confirmed permanent: the introductory rate through 2026-08-31 became the standard price and the scheduled increase to $3/$15 will not occur, per https://platform.claude.com/docs/en/about-claude/pricing.
Batch rates shown are the documented 50% discount applied to Standard, per https://aws.amazon.com/bedrock/pricing/, and are verified directly in the Price List API for the Nova and Llama rows.
Context windows for Fable 5 and Sonnet 5 are from their Bedrock model cards; entries marked "see model card" were not separately verified in this pass and should not be quoted as fact.

## 4. Cost per agent run and runs per budget

### 4.1 Modelling assumptions

Three token profiles per agent run, as specified: small 100k input and 10k output, medium 500k input and 30k output, large 2M input and 100k output.
The cached variant assumes an 80% cache-read rate on input tokens.
The remaining 20% of input is priced at the cache-write rate for Claude, which is deliberately conservative because the write rate (1.25x) exceeds the fresh input rate (1.0x).
For Nova the remaining 20% is priced at the fresh input rate, because Nova bills the cache-write SKU at $0.00.
A well-behaved agent loop with a stable prefix will beat 80% cache reads, so these numbers are an upper bound on cached cost.
All figures use global endpoint or us-east-1 rates, Standard tier, no batch discount.
The model script is at `/private/tmp/claude-501/-Users-danielpuri-Desktop-Projects/eefbcb26-3bfe-4a0c-9a85-c24a1f68f192/scratchpad/costmodel.py`.

### 4.2 Cost per single agent run (USD)

| Profile | Nova Lite | Haiku 4.5 | Sonnet 5 | Opus 5 | Fable 5 |
| --- | --- | --- | --- | --- | --- |
| Small 100k/10k | $0.0084 | $0.1500 | $0.3000 | $0.7500 | $1.5000 |
| Small 100k/10k, 80% cache | $0.0048 | $0.0830 | $0.1660 | $0.4150 | $0.8300 |
| Medium 500k/30k | $0.0372 | $0.6500 | $1.3000 | $3.2500 | $6.5000 |
| Medium 500k/30k, 80% cache | $0.0192 | $0.3150 | $0.6300 | $1.5750 | $3.1500 |
| Large 2M/100k | $0.1440 | $2.5000 | $5.0000 | $12.5000 | $25.0000 |
| Large 2M/100k, 80% cache | $0.0720 | $1.1600 | $2.3200 | $5.8000 | $11.6000 |

Caching saves about 45% of total run cost on the small profile, 51% on medium and 54% on large.
The saving grows with the input-to-output ratio, because caching only discounts input.

### 4.3 Runs that fit in $800 (reserving $200 for infrastructure)

| Profile | Nova Lite | Haiku 4.5 | Sonnet 5 | Opus 5 | Fable 5 |
| --- | --- | --- | --- | --- | --- |
| Small 100k/10k | 95,238 | 5,333 | 2,666 | 1,066 | 533 |
| Small 100k/10k, 80% cache | 166,666 | 9,638 | 4,819 | 1,927 | 963 |
| Medium 500k/30k | 21,505 | 1,230 | 615 | 246 | 123 |
| Medium 500k/30k, 80% cache | 41,666 | 2,539 | 1,269 | 507 | 253 |
| Large 2M/100k | 5,555 | 320 | 160 | 64 | 32 |
| Large 2M/100k, 80% cache | 11,111 | 689 | 344 | 137 | 68 |

### 4.4 Experiments that fit in $800, where one experiment is 2 configs x 30 trials (60 runs)

| Profile | Nova Lite | Haiku 4.5 | Sonnet 5 | Opus 5 | Fable 5 |
| --- | --- | --- | --- | --- | --- |
| Small 100k/10k | 1,587 | 88 | 44 | 17 | 8 |
| Small 100k/10k, 80% cache | 2,777 | 160 | 80 | 32 | 16 |
| Medium 500k/30k | 358 | 20 | 10 | 4 | 2 |
| Medium 500k/30k, 80% cache | 694 | 42 | 21 | 8 | 4 |
| Large 2M/100k | 92 | 5 | 2 | 1 | 0 |
| Large 2M/100k, 80% cache | 185 | 11 | 5 | 2 | 1 |

### 4.5 Cost of one experiment (60 runs) on the medium cached profile

| Model | Cost of one experiment |
| --- | --- |
| Nova Lite | $1.15 |
| Haiku 4.5 | $18.90 |
| Sonnet 5 | $37.80 |
| Opus 5 | $94.50 |
| Fable 5 | $189.00 |

Adjust Sonnet 5, Opus 5 and Fable 5 upward by roughly 30% if the profiles are read as text volume rather than token counts, per the tokenizer change in section 2.3.
On that basis one medium cached experiment is about $49 on Sonnet 5, $123 on Opus 5 and $246 on Fable 5.

## 5. Assessment and recommendations

### 5.1 Model selection by job

Pipeline debugging and smoke tests: Amazon Nova Lite.
At $0.0192 per medium cached run it is 16x cheaper than Haiku 4.5 and 82x cheaper than Opus 5, it is first-party so credits definitely apply, and 41,666 medium runs fit in $800.
Nova Lite will not behave like a frontier agent, but pipeline debugging is about exercising trajectory capture, evaluation wiring and gate logic, not about agent quality.
Budget roughly $20 for the entire debugging phase and it will be invisible against the $1,000.

Real baseline-versus-candidate comparisons: Claude Sonnet 5.
It is the best price-to-capability point on Bedrock at $2/$10 with a 1M context window, and 21 full experiments fit in $800 on the medium cached profile.
Claude Haiku 4.5 at $1/$5 is the fallback if trial counts need to double, and it has the additional advantage of using the older tokenizer, so its effective same-text cost advantage over Sonnet 5 is larger than the headline 2x suggests.
Reserve Opus 5 and Fable 5 for a small number of headline comparisons: at 8 and 4 experiments respectively in $800, they cannot be the workhorse.

LLM-as-judge and evaluation: Claude Haiku 4.5 via batch inference.
Batch is a 50% discount, judging is inherently asynchronous so the 24-hour turnaround does not matter, and batch does not consume real-time TPM quota, which keeps quota headroom for the agent loops themselves.
Haiku 4.5 is confirmed batch-supported on Bedrock, whereas the Claude 5 family is not currently listed as batch-supported.
Do not try to combine batch with prompt caching, because caching is not supported with the batch inference API.

### 5.2 Region choice from the Netherlands

Use the global endpoint for Claude, which carries no pricing premium and routes worldwide.
Do not use a regional or EU geo inference profile unless data residency is an actual requirement, because that costs 10% more for zero benefit to an experimentation platform.
If Nova is used heavily, run it in us-east-1 rather than eu-central-1: Frankfurt is about 31% more expensive and Ireland about 15% more expensive for the same Nova model.
us-east-1 also has the widest catalog, including Nova Premier and Llama 4, which are absent from both EU price files.

### 5.3 Budget shape

Reserving $200 for infrastructure and spending $800 on models, a defensible allocation is roughly $30 for Nova Lite pipeline work, $500 for Sonnet 5 and Haiku 4.5 comparison runs, $200 for a handful of Opus 5 or Fable 5 headline experiments, and $70 for batch judging.
That buys on the order of 13 Sonnet 5 experiments, 2 Opus 5 experiments, and effectively unlimited Nova Lite debugging, all on the medium cached profile.
Caching is worth more than model choice at the margin within a tier: turning it on halves every run, so build cache checkpoint placement into the agent harness from day one rather than retrofitting it.
Place cache checkpoints after `tools` and `system` and before variable `messages` content, because changing an earlier section invalidates the cache for everything after it.

### 5.4 The one thing to do before anything else

Run a deliberate $5 credit-eligibility smoke test before committing to any Claude tier.
Invoke Nova Lite, Claude Haiku 4.5 and Claude Sonnet 5 for a few dollars each, wait for the bill to settle, then check AWS Cost Explorer and the Credits page to see which line items the credits actually offset.
If Claude usage lands under AWS Marketplace and is not offset, the effective model budget for Claude collapses to zero and the entire experiment design has to move to Amazon Nova, where the same $800 buys 41,666 medium cached runs instead of 1,269.
This single test changes the plan by a factor of 33, so it is worth doing before writing any experiment configuration.

## 6. Open questions

Whether the specific $1,000 credit grant designates Claude on Bedrock as an eligible service is not determinable from public sources, because eligibility is set per credit grant and is visible only on the Credits page of the account.
Whether the account can obtain non-zero Bedrock TPM and RPM quotas is unknown and is a hard blocker if it cannot, given the 2026-07-30 report of new accounts receiving zero quota and being denied an increase.
The default TPM and RPM values for Claude Sonnet 5, Claude Fable 5, Claude Haiku 4.5 and the Nova models are not published in the AWS General Reference and must be read from the Service Quotas console.
The conflict between AWS documenting 20,000,000 input TPM for Claude Opus 4.7 on `bedrock-mantle` and Anthropic documenting a 2,000,000 input TPM default for Claude in Amazon Bedrock is unresolved.
Whether Claude Sonnet 5, Opus 5 and Fable 5 will accept Bedrock batch inference jobs is contradicted between the Bedrock batch documentation (not listed) and the Opus 4.7 Marketplace page (batch dimensions published), and needs a console check.
The exact cache-write rate for Amazon Nova Premier and Nova 2 Pro is not published in the Price List API, so the $0.00 cache-write behaviour observed for Nova Micro, Lite, Pro and Nova 2 Lite should not be assumed to extend to them.
Context windows for the Amazon Nova family, Llama 4, Mistral Large 3 and DeepSeek v3.2 were not verified in this pass and are marked "see model card" rather than guessed.
Whether AgentLab's real agent runs actually achieve an 80% cache-read rate is an empirical question that only instrumented trajectory data can answer, and the entire cached column of section 4 depends on it.
