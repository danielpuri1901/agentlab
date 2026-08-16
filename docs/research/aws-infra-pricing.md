# AWS infrastructure pricing and quotas for AgentLab

Research date: 2026-08-16.
Regions compared: us-east-1 (N. Virginia), eu-west-1 (Ireland), eu-central-1 (Frankfurt).
All prices are USD, on-demand, excluding tax.

## 1. Summary

Fargate in Ireland costs exactly the same as N. Virginia ($0.04048 per vCPU-hour, $0.004445 per GB-hour), so there is no price reason to run AgentLab in the US.
Frankfurt is about 15% more expensive on Fargate and worse on almost every other line, so eu-west-1 is the right region.
ARM/Graviton Fargate is 20% cheaper than x86 at identical published rates in both us-east-1 and eu-west-1.
The default Fargate On-Demand vCPU quota is 6 vCPUs per region, which caps AgentLab at six concurrent 1-vCPU tasks until a quota increase is granted.
A NAT gateway costs $35.04 per month in eu-west-1 before it moves a single byte, and interface VPC endpoints are not cheaper at this scale.
Running tasks in a public subnet with `assignPublicIp=ENABLED` and gateway endpoints for S3 and DynamoDB avoids both, for well under $1 per month.
CloudWatch is the real silent cost: log ingestion is $0.57 per GB in eu-west-1 and custom metrics are $0.30 per metric-month, which explodes if `experiment_id` becomes a metric dimension.
Step Functions Standard is the correct choice because only Standard supports the ECS `.sync` "Run a Job" pattern, and Express workflows cap at five minutes.
A realistic development month (200 task-runs of 10 minutes at 1 vCPU/2 GB) costs about $12.90 in infrastructure.
That is a rounding error next to Sonnet 5 or Opus 5 token spend, but it is the majority of total cost if the workhorse model is Amazon Nova Lite.

## 2. Method and source quality

Nearly every number below comes from the AWS Price List Bulk API, which is the machine-readable feed behind the public pricing pages.
The region-specific offer files are at `https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/<ServiceCode>/current/<region>/index.json`.
This matters because the HTML pricing pages render their rate tables in JavaScript, so fetching the page returns prose and worked examples but no table values.
Each service section below names the offer code used and the file's `publicationDate`, which is AWS's own timestamp for that price set.

Two numbers could not be verified from a primary source and are labelled inline: exact Fargate Spot rates, and public IPv4 billing granularity for sub-hour tasks.

## 3. Verified findings

### 3.1 ECS on Fargate

Source: Price List offer `AmazonECS`, publicationDate 2026-07-07.
Source: https://aws.amazon.com/fargate/pricing/ for billing mechanics and Spot policy.

| Dimension | us-east-1 | eu-west-1 | eu-central-1 |
| --- | --- | --- | --- |
| Linux/x86 vCPU-hour | $0.04048 | $0.04048 | $0.04656 |
| Linux/x86 GB-hour | $0.004445 | $0.004445 | $0.00511 |
| Linux/ARM vCPU-hour | $0.03238 | $0.03238 | $0.03725 |
| Linux/ARM GB-hour | $0.00356 | $0.00356 | $0.00409 |
| Ephemeral storage GB-hour (beyond 20 GB) | $0.000111 | $0.000122 | $0.000132 |

Ireland is at exact parity with N. Virginia on both x86 and ARM.
Frankfurt is 15.0% higher on x86 vCPU and 15.0% higher on ARM vCPU.
ARM is 20.0% cheaper than x86 on vCPU and 19.9% cheaper on memory, in every region checked.

Billing granularity is per second with a one-minute minimum for Linux, and five minutes for Windows.
The billing clock starts when the container image download begins, not when the container starts running, per https://aws.amazon.com/fargate/pricing/.
That means slow image pulls are billed, which is a direct argument for small images.
20 GB of ephemeral storage is included with every task and only the excess is charged.

Fargate Spot is available for ECS on Linux x86 and ARM, and AWS describes it as "up to a 70% discount off the regular Fargate price".
Fargate Spot is not available for EKS.
The exact Spot rate table is rendered client-side and is not present in the Price List Bulk API, so I could not verify current per-vCPU-hour Spot numbers from a primary source.
Third-party sources quote roughly $0.0126 to $0.0129 per vCPU-hour for us-east-1, which is consistent with the stated 68-70% discount, but treat those specific figures as unverified.
Spot tasks receive a two-minute termination warning, so AgentLab must treat an interrupted trial as a retryable outcome rather than a failed one.

### 3.2 Fargate and ECS quotas

Source: https://docs.aws.amazon.com/general/latest/gr/ecs-service.html

| Quota | Default | Adjustable |
| --- | --- | --- |
| Fargate On-Demand vCPU resource count | 6 per region | Yes |
| Fargate Spot vCPU resource count | 6 per region | Yes |
| Fargate On-Demand Burst Launch Rate | 100 in eu-west-1 and us-east-1 | Yes |
| Fargate On-Demand Sustained Launch Rate | 20 per second in eu-west-1 and us-east-1 | Yes |
| Rate of tasks launched by a service on Fargate | 500 per minute in eu-west-1 | No |
| Tasks launched per RunTask call | 10 | No |
| Tasks per service (desired count) | 5,000 | No |
| Tags per resource | 50 | No |
| Task definition size | 64 KB | No |

The 6-vCPU default is the single most important operational number in this report.
At 1 vCPU per task that is six concurrent trials, which makes any meaningful parallel experiment impossible on a fresh account.
AWS states that "New AWS accounts might have initial lower quotas that can increase over time" and that Fargate raises them automatically based on observed usage, but relying on automatic growth is not a plan.
The quota is adjustable at https://console.aws.amazon.com/servicequotas/home/services/fargate/quotas/L-3032A538 and should be requested in week one.
The 50-tag limit is worth noting because AgentLab wants `experiment_id`, `trial_id`, `config_hash`, `agent_version` and similar on every task.

### 3.3 Fargate task startup latency

Source: https://aws.amazon.com/blogs/aws/aws-fargate-enables-faster-container-startup-using-seekable-oci/
Source: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-recommendations.html

AWS measured a PyTorch image taking 129 seconds from `createdAt` to `startedAt` without SOCI, and 60 seconds with SOCI indexing.
That is a deliberately large ML image, so treat 129 seconds as an upper bound rather than a typical case.
The `awsvpc` network mode that Fargate requires adds "an overhead of several seconds" because ECS must provision and attach an ENI through EC2 APIs.
On Fargate every task runs on its own single-use instance, so there is no image cache between tasks and the image is pulled every single time.
SOCI lazy loading is enabled automatically on Linux platform version 1.4 when a SOCI index exists in ECR, but AWS found it only helps images larger than 250 MB compressed and can slow down smaller images.
zstd-compressed images gave AWS up to a 27% startup reduction in internal testing.

I could not find a primary-source measured startup number for a small Python or Alpine image, so any claim like "typical 30 to 60 seconds" should be treated as unverified until AgentLab measures it directly.
The practical implication is that a 10-minute trial carries roughly 10% to 20% fixed startup overhead, which is billed, and which AgentLab should record as a separate field so trial durations stay comparable.

### 3.4 AWS Step Functions

Source: Price List offer `AmazonStates`, publicationDate 2025-08-28.
Source: https://docs.aws.amazon.com/step-functions/latest/dg/standard-vs-express.html

| Dimension | us-east-1 | eu-west-1 | eu-central-1 |
| --- | --- | --- | --- |
| Standard, per state transition | $0.000025 | $0.000025 | $0.000025 |
| Standard free tier | 4,000 transitions per month | same | same |
| Express, per request | $1.00 per million | $1.00 per million | $1.00 per million |
| Express duration, first 3.6M GB-s | $0.00001667 per GB-s | same | same |
| Express duration, next 14.4M GB-s | $0.00000833 per GB-s | same | same |
| Express duration, above 18M GB-s | $0.00000456 per GB-s | same | same |

Step Functions pricing is identical in all three regions.
The Standard free tier of 4,000 state transitions per month does not expire and applies to both new and existing customers, per https://aws.amazon.com/step-functions/pricing/.

Standard is the correct workflow type for AgentLab, for two independent reasons.
First, only Standard workflows support the "Run a Job" (`.sync`) integration pattern with Amazon ECS/Fargate, which is exactly the "launch a task and wait for it to finish" primitive AgentLab needs.
Express workflows support only the Request Response pattern, so an Express workflow would fire `RunTask` and immediately move on without waiting.
Second, Express workflows have a hard five-minute maximum duration, and a 10-minute agent trial does not fit.
Standard workflows run for up to one year and give exactly-once execution semantics, against at-least-once for Express.

Express also sends execution history to CloudWatch Logs rather than retaining it in Step Functions, which would add log ingestion cost on top.
The one genuine cost of Standard is that retries bill as extra state transitions, but at $0.000025 each this is irrelevant at AgentLab's scale.

### 3.5 Amazon SQS

Source: Price List offer `AWSQueueService`, publicationDate 2025-08-28.
Source: https://aws.amazon.com/sqs/pricing/

| Dimension | us-east-1 | eu-west-1 | eu-central-1 |
| --- | --- | --- | --- |
| Free tier | 1,000,000 requests per month | same | same |
| Standard queue, first 100 billion requests | $0.40 per million | $0.40 per million | $0.40 per million |
| FIFO queue, first 100 billion requests | $0.50 per million | $0.50 per million | $0.50 per million |
| Fair queue surcharge | $0.10 per million | $0.10 per million | $0.10 per million |

The 1 million request free tier is always free and is calculated monthly across all regions.
Standard and FIFO tier-1 pricing is identical across all three regions, and only the higher volume tiers diverge in Frankfurt.

On mechanics, AWS states that "Every Amazon SQS action counts as a request".
There is no separate charge for dead letter queues, for visibility timeout, or for message retention.
A DLQ is just another queue, so a message moved to a DLQ and later redriven simply generates additional billable requests.
`ChangeMessageVisibility` calls also count as requests, which matters if AgentLab extends visibility on long-running trials by heartbeating.

Two cost traps are worth flagging.
Each 64 KB chunk of payload bills as one request, so a 1 MiB message costs 16 requests, and trajectory payloads should be S3 pointers rather than inline bodies.
Long polling still bills each `ReceiveMessage` call, so a single consumer polling with a 20-second wait generates about 129,600 requests per month, and roughly seven always-on consumers would exhaust the free tier on empty polls alone.

### 3.6 Amazon DynamoDB

Source: Price List offer `AmazonDynamoDB`, publicationDate 2026-08-15.

| Dimension | us-east-1 | eu-west-1 | eu-central-1 |
| --- | --- | --- | --- |
| On-demand write request units | $0.625 per million | $0.705 per million | $0.7625 per million |
| On-demand read request units | $0.125 per million | $0.1415 per million | $0.1525 per million |
| Standard table storage | $0.25 per GB-month | $0.283 per GB-month | $0.306 per GB-month |
| Free storage | first 25 GB-months | first 25 GB-months | first 25 GB-months |
| Standard-IA write request units | $0.78 per million | $0.885 per million | $0.955 per million |
| Standard-IA read request units | $0.155 per million | $0.177 per million | $0.1905 per million |
| Standard-IA storage | $0.10 per GB-month | $0.1132 per GB-month | $0.1224 per GB-month |
| Point-in-time recovery storage | $0.20 per GB-month | $0.22 per GB-month | $0.2448 per GB-month |

Ireland is 12.8% more expensive than N. Virginia on writes and 13.2% on reads.
Frankfurt is 22.0% above N. Virginia on writes.
The 25 GB free storage tier appears directly in the Price List feed rather than only in free tier marketing, which means it is a standing discount rather than a 12-month new-account offer.

The Standard-IA table class is 60% cheaper on storage but 25% more expensive per request, so it suits archived experiment records and not the hot control-plane table.

### 3.7 Amazon S3

Source: Price List offer `AmazonS3`, publicationDate 2026-08-07.

| Dimension | us-east-1 | eu-west-1 | eu-central-1 |
| --- | --- | --- | --- |
| Standard storage, first 50 TB | $0.023 per GB-month | $0.023 per GB-month | $0.0245 per GB-month |
| Standard-IA storage | $0.0125 per GB-month | $0.0125 per GB-month | $0.0135 per GB-month |
| PUT/COPY/POST/LIST | $0.005 per 1,000 | $0.005 per 1,000 | $0.0054 per 1,000 |
| GET and all other | $0.0004 per 1,000 | $0.0004 per 1,000 | $0.00043 per 1,000 |
| Standard-IA PUT | $0.01 per 1,000 | $0.01 per 1,000 | $0.01 per 1,000 |
| Standard-IA GET | $0.001 per 1,000 | $0.001 per 1,000 | $0.001 per 1,000 |
| Lifecycle transition to Standard-IA or One Zone-IA | $0.01 per 1,000 objects | same | same |
| Lifecycle transition to Glacier Instant Retrieval | $0.02 per 1,000 objects | same | same |
| Glacier Instant Retrieval storage | see note | see note | see note |
| Intelligent-Tiering monitoring | $0.0025 per 1,000 objects | same | same |

Ireland is at exact parity with N. Virginia on S3 standard storage and requests.
Frankfurt is 6.5% higher on storage and 8% higher on requests.
Glacier Instant Retrieval storage rates were not extracted in this pass and are marked as not verified here.

The lifecycle trap is that transitions are charged per object, not per gigabyte.
Moving 1,000,000 small trajectory files to Standard-IA costs $10 in transition requests, which can exceed the storage saving if the objects are small.
The rule of thumb is that lifecycle to IA pays off on few large objects and loses on many small ones, so AgentLab should batch trajectory steps into per-trial archives before any lifecycle rule applies.

### 3.8 Amazon EventBridge

Source: Price List offer `AWSEvents`, publicationDate 2026-05-29.

| Dimension | us-east-1 | eu-west-1 | eu-central-1 |
| --- | --- | --- | --- |
| Custom events published | $1.00 per million | $1.00 per million | $1.00 per million |
| AWS management events ingested | free | free | free |
| Cross-account delivery of custom events | $0.05 per million | $0.05 per million | $0.05 per million |
| API destination invocations | $0.20 per million | $0.20 per million | $0.24 per million |
| Pipes requests | $0.40 per million | $0.40 per million | $0.46 per million |
| Scheduler invocations | $1.00 per million after 14M free | same | $1.15 per million after 14M free |
| Archive processing | $0.10 per GB | $0.11 per GB | $0.12 per GB |
| Archive storage | $0.023 per GB-month | $0.023 per GB-month | $0.0245 per GB-month |

Each 64 KB chunk of payload bills as one event.
Events emitted by AWS services themselves (including ECS task state changes) are AWS management events and are ingested free, which means AgentLab can subscribe to ECS task state transitions at no ingestion cost.
EventBridge Scheduler gives 14 million free invocations per month, so scheduled experiment runs are effectively free.

### 3.9 Amazon CloudWatch

Source: Price List offer `AmazonCloudWatch`, publicationDate 2026-08-06.
Source: https://aws.amazon.com/cloudwatch/pricing/ for free tier composition.

| Dimension | us-east-1 | eu-west-1 | eu-central-1 |
| --- | --- | --- | --- |
| Logs ingestion, Standard class | $0.50 per GB | $0.57 per GB | $0.63 per GB |
| Logs ingestion, Infrequent Access class | $0.25 per GB | $0.285 per GB | not extracted |
| Logs storage | $0.03 per GB-month | $0.03 per GB-month | $0.0324 per GB-month |
| Logs storage, IA class | $0.018 per GB-month | $0.018 per GB-month | not extracted |
| Logs Insights data scanned | $0.005 per GB | $0.0057 per GB | $0.0063 per GB |
| Live Tail | $0.01 per minute | $0.01 per minute | $0.01 per minute |
| Custom metrics, first 10,000 | $0.30 per metric-month | $0.30 per metric-month | $0.30 per metric-month |
| Custom metrics, next 240,000 | $0.10 per metric-month | $0.10 per metric-month | $0.10 per metric-month |
| PutMetricData API | $0.01 per 1,000 requests | same | same |
| Standard resolution alarm | $0.10 per alarm-month | $0.10 per alarm-month | $0.10 per alarm-month |
| High resolution alarm | $0.30 per alarm-month | $0.30 per alarm-month | $0.30 per alarm-month |
| Composite alarm | $0.50 per alarm-month | $0.50 per alarm-month | $0.50 per alarm-month |
| OpenTelemetry metrics ingestion | $0.50 per GB | not extracted | not extracted |

CloudWatch Logs ingestion is the one place where Ireland is materially worse than N. Virginia, at 14% higher.
Frankfurt is 26% higher than N. Virginia.

The free tier, per the CloudWatch pricing page, includes 5 GB of log data covering ingestion, archive storage and Logs Insights scanning combined, 10 custom metrics, 10 alarm metrics, 3 dashboards, and 1 million API requests.
Note that the 5 GB is shared across three different uses, so ingesting 5 GB consumes the whole allowance and storage then bills from the first gigabyte.

Two traps deserve emphasis.
The first is that CloudWatch "treats each unique combination of dimensions as a separate metric, even if the metrics have the same metric name".
If AgentLab emits a metric dimensioned by `experiment_id`, then 100 experiments times 5 metrics equals 500 billable metrics, which is $150 per month for what feels like five metrics.
Experiment identifiers belong in structured log fields and in DynamoDB, never in a metric dimension.
The second is that agent trajectories are verbose, and at $0.57 per GB a chatty debug logger is easy to underestimate.
The Infrequent Access log class halves ingestion cost to $0.285 per GB in Ireland and is the right default for raw trajectory logs that are queried rarely.

### 3.10 Cost traps

#### NAT gateway

Source: Price List offer `AmazonEC2`, filtered for NAT gateway descriptions.
Source: https://aws.amazon.com/vpc/pricing/

| Dimension | us-east-1 | eu-west-1 | eu-central-1 |
| --- | --- | --- | --- |
| NAT gateway per hour | $0.045 | $0.048 | $0.052 |
| NAT gateway data processing per GB | $0.045 | $0.048 | $0.052 |

At eu-west-1 rates a single NAT gateway costs $35.04 per month before processing any data.
AWS confirms that "Each partial NAT Gateway-hour consumed is billed as a full hour" and that data processing applies "regardless of the traffic's source or destination".
A regional NAT gateway spanning three availability zones bills three NAT gateway-hours per hour, so a naive multi-AZ setup costs $105 per month.

#### VPC endpoints

Source: Price List offer `AmazonVPC`, publicationDate 2026-07-24.

| Dimension | us-east-1 | eu-west-1 | eu-central-1 |
| --- | --- | --- | --- |
| Interface endpoint per hour | $0.010 | $0.011 | $0.012 |
| Interface endpoint data processing, up to 1 PB | $0.010 per GB | $0.010 per GB | $0.010 per GB |
| Gateway endpoint (S3, DynamoDB) | no charge | no charge | no charge |

The usual advice to "replace NAT with VPC endpoints" does not save money at AgentLab's scale.
AWS confirms gateway endpoints for S3 and DynamoDB have "no data processing or hourly charges", and those are free wins that should always be used.
But interface endpoints bill per hour per availability zone, so covering ECR API, ECR Docker, CloudWatch Logs and Secrets Manager across two AZs costs 4 times 2 times 730 times $0.011, which is $64.24 per month in Ireland.
That is nearly double the cost of the NAT gateway it was meant to replace.

The cheapest correct answer for AgentLab is a public subnet with `assignPublicIp=ENABLED` on the task, plus free gateway endpoints for S3 and DynamoDB.
Egress then flows through the internet gateway, which has no hourly or per-GB charge of its own.
The only cost is the public IPv4 address at $0.005 per hour while the task runs, which for 33 task-hours per month is about $0.17.
The security posture is handled by a security group with no inbound rules, which is standard for batch tasks that only make outbound calls.

#### Public IPv4 addresses

| Dimension | us-east-1 | eu-west-1 | eu-central-1 |
| --- | --- | --- | --- |
| In-use public IPv4 per hour | $0.005 | $0.005 | $0.005 |
| Idle public IPv4 per hour | $0.005 | $0.005 | $0.005 |

Pricing is identical in all three regions.
Whether a 10-minute task is billed for 10 minutes or rounded to a full hour is not stated in the sources I checked, so the per-task figure is uncertain by roughly 6x.
The difference between the two interpretations for 200 monthly runs is $0.17 against $1.00, which is immaterial, but it is unverified either way.

#### Data transfer

Source: Price List offer `AWSDataTransfer`, publicationDate 2026-07-20.

| Dimension | us-east-1 | eu-west-1 | eu-central-1 |
| --- | --- | --- | --- |
| Out to internet, global free allowance | 100 GB per month | same | same |
| Out to internet, first 10 TB | $0.090 per GB | $0.090 per GB | $0.090 per GB |
| Out to internet, next 40 TB | $0.085 per GB | $0.085 per GB | $0.085 per GB |
| Regional transfer between AZs | $0.010 per GB | $0.010 per GB | $0.010 per GB |

Internet egress is identical in all three regions and the first 100 GB per month is free, "aggregated globally".
The cross-AZ charge of $0.01 per GB applies in each direction, so a chatty cross-AZ design pays twice.
Keeping Fargate tasks, S3 and DynamoDB in one region means Bedrock calls and artifact writes stay on the free in-region path.

#### ECR storage

Source: Price List offer `AmazonECR`, publicationDate 2025-11-21.

ECR storage is $0.10 per GB-month in all three regions.
That is over 4x the cost of S3 standard storage, so untagged image layers accumulating from CI are a slow leak.
An ECR lifecycle policy that expires untagged images after a few days is the fix, and it costs nothing.

#### Idle load balancers

Source: Price List offer `AWSELB`, publicationDate 2026-07-20.

| Dimension | us-east-1 | eu-west-1 | eu-central-1 |
| --- | --- | --- | --- |
| Application Load Balancer per hour | $0.0225 | $0.0252 | not extracted |
| Network Load Balancer per hour | $0.0225 | $0.0252 | not extracted |
| ALB capacity unit (LCU) per hour | $0.008 | $0.008 | $0.008 |

An idle ALB in Ireland costs $18.40 per month plus LCU charges, whether or not anything routes through it.
AgentLab's task runners are batch jobs that nothing connects to inbound, so they need no load balancer at all.
Only a persistent control-plane API would justify one, and for a low-traffic internal API a Lambda function URL or API Gateway avoids the fixed hourly floor entirely.

### 3.11 AWS Budgets and cost allocation

Source: https://aws.amazon.com/aws-cost-management/aws-budgets/pricing/
Source: https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/activating-tags.html
Source: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/ecs-using-tags.html

Budget monitoring and notifications are free of charge.
Action-enabled budgets, which can actually apply an IAM or Service Control Policy to stop spend, are free for the first two per month and then cost $0.10 per day each.
Budget reports cost $0.01 per delivered report.

This means AgentLab can have a hard stop for free.
The pattern is one action-enabled budget at a chosen threshold of the $1,000 credit pool, with an action that attaches a deny-all Service Control Policy or detaches the execution role's permissions.
That is a genuine circuit breaker rather than an email, and it stays inside the two free action-enabled budgets.

For per-experiment attribution, tagging alone is not enough.
Tags must be explicitly activated as cost allocation tags in the Billing console, or through the `UpdateCostAllocationTagsStatus` API, before they appear in Cost Explorer or the Cost and Usage Report.
Activation can take up to 24 hours to appear and another 24 hours to take effect, so this should be done on day one rather than when the first bill looks wrong.
Only the management account of an organization, or a standalone account, can access the cost allocation tags manager.

For ECS specifically there are two flags that are both off by default and both required.
`enableECSManagedTags` makes ECS tag newly launched tasks with cluster and service information.
`propagateTags` copies tags from the task definition or service onto the task itself, and AWS documentation states "The PropagateTags parameter isn't activated by default".
Without `propagateTags`, an `experiment_id` tag on the task definition never reaches the running task and never reaches the bill.
For a standalone `RunTask` call the tag should be passed directly in the `tags` parameter of the call, which is the cleanest path for AgentLab because each trial is a distinct one-off task.

There is one further step worth knowing about: Split Cost Allocation Data can be enabled to get task-level CPU and memory cost in the Cost and Usage Report, which is what makes true per-trial cost attribution possible rather than per-cluster.

## 4. Monthly cost scenario

### 4.1 Assumptions

Region eu-west-1 (Ireland).
200 Fargate task-runs per month, each 10 minutes, at 1 vCPU and 2 GB, Linux/x86, on-demand.
40 seconds of image pull time per task, which is billable.
Tasks run in a public subnet with a public IP, with no NAT gateway and no load balancer.
Gateway VPC endpoints for S3 and DynamoDB, which are free.
One Step Functions Standard execution per trial at 25 state transitions including retries.
DynamoDB usage of about 68,000 write request units and 100,000 read request units, with under 25 GB stored.
S3 holding 20 GB with 40,000 PUTs and 100,000 GETs per month.
CloudWatch receiving 10 GB of logs, 30 custom metrics and 15 alarms.
5 GB of container images in ECR.

### 4.2 Infrastructure cost

| Line item | Calculation | Monthly cost |
| --- | --- | --- |
| Fargate vCPU | 33.33 vCPU-hr x $0.04048 | $1.35 |
| Fargate memory | 66.67 GB-hr x $0.004445 | $0.30 |
| Fargate image pull overhead | 2.22 vCPU-hr + 4.44 GB-hr | $0.11 |
| Public IPv4 | 33.33 hr x $0.005 | $0.17 |
| Step Functions Standard | (5,000 - 4,000 free) x $0.000025 | $0.03 |
| SQS | under 1M request free tier | $0.00 |
| DynamoDB writes | 68,000 x $0.705/M | $0.05 |
| DynamoDB reads | 100,000 x $0.1415/M | $0.01 |
| DynamoDB storage | under 25 GB free | $0.00 |
| S3 storage | 20 GB x $0.023 | $0.46 |
| S3 PUT | 40,000 x $0.005/1,000 | $0.20 |
| S3 GET | 100,000 x $0.0004/1,000 | $0.04 |
| CloudWatch Logs ingestion | (10 - 5 free) GB x $0.57 | $2.85 |
| CloudWatch Logs storage | 10 GB x $0.03 | $0.30 |
| CloudWatch custom metrics | (30 - 10 free) x $0.30 | $6.00 |
| CloudWatch alarms | (15 - 10 free) x $0.10 | $0.50 |
| EventBridge | 4,000 custom events | $0.00 |
| ECR storage | 5 GB x $0.10 | $0.50 |
| Data transfer | in-region, under 100 GB free | $0.00 |
| **Total** | | **$12.87** |

### 4.3 What the avoidable mistakes would add

| Mistake | Monthly cost added | Multiple of the base bill |
| --- | --- | --- |
| One NAT gateway left running | $35.04 | 2.7x |
| One idle ALB left running | $18.40 | 1.4x |
| Four interface VPC endpoints across 2 AZs | $64.24 | 5.0x |
| `experiment_id` as a metric dimension, 100 experiments x 5 metrics | $147.00 | 11.4x |
| Verbose logging at 50 GB instead of 10 GB | $22.80 | 1.8x |

Every row in this table costs more than the entire correctly-designed infrastructure bill.
The NAT gateway, the idle ALB and the interface endpoints all bill by the hour whether AgentLab runs an experiment or not, which is the defining property of the traps that hurt.

### 4.4 Infrastructure against model tokens

Model costs are taken from the sibling report at `/Users/danielpuri/Desktop/Projects/agentlab/docs/research/bedrock-model-pricing.md`, using its medium profile of 500k input and 30k output tokens per run.

| Workhorse model | 200 runs of model spend | Infra at $12.87 | Infra share of total |
| --- | --- | --- | --- |
| Amazon Nova Lite | $7.44 | $12.87 | 63% |
| Claude Haiku 4.5 | $130.00 | $12.87 | 9.0% |
| Claude Sonnet 5 | $260.00 | $12.87 | 4.7% |
| Claude Opus 5 | $650.00 | $12.87 | 1.9% |
| Claude Fable 5 | $1,300.00 | $12.87 | 1.0% |

The honest answer to "is infrastructure a rounding error" is that it depends entirely on which model AgentLab picks as its workhorse.
On Sonnet 5 or anything above it, infrastructure is under 5% and not worth optimising.
On Nova Lite, which the Bedrock report recommends as the workhorse for volume, infrastructure is the majority of total spend and becomes the thing to optimise.
Adding a single NAT gateway to the Nova Lite scenario would make infrastructure 87% of total cost, which would be an absurd way to spend the credit pool.

## 5. Assessment and recommendations for AgentLab

Choose eu-west-1 (Ireland).
It matches us-east-1 exactly on Fargate, S3, Step Functions, SQS, EventBridge and data transfer, and it is only worse on CloudWatch Logs (14%) and DynamoDB (13%).
Those two deltas cost about $0.40 per month at the modelled scale.
Frankfurt is worse on essentially everything and has no compensating advantage.
The one thing that could override this is Bedrock model availability, which is the sibling report's territory and should be confirmed before the region is fixed.

Request the Fargate On-Demand vCPU quota increase immediately.
The default of 6 vCPUs is the hard ceiling on experiment parallelism, and quota requests take time to approve.
Ask for a number tied to the intended design, for example 64 vCPUs to support 64 concurrent 1-vCPU trials.

Build on ARM/Graviton from the start.
It is 20% cheaper at identical published rates, the discount applies in both candidate regions, and retrofitting a multi-architecture container build later is more painful than doing it now.
The only blocker would be an x86-only dependency in the agent image, which is worth checking early.

Use no NAT gateway.
Run tasks in a public subnet with `assignPublicIp=ENABLED`, a security group with no inbound rules, and free gateway endpoints for S3 and DynamoDB.
This is the single largest cost decision in the whole design and it saves $35 per month against the obvious default, which over a six-month project is 21% of the entire credit pool.

Use Step Functions Standard, not Express.
Only Standard supports the ECS `.sync` pattern needed to launch a task and wait for it, and Express cannot run longer than five minutes.
At $0.000025 per state transition with 4,000 free per month, Standard is effectively free at AgentLab's scale.

Treat CloudWatch as the thing most likely to surprise you.
Never put `experiment_id`, `trial_id` or `config_hash` in a CloudWatch metric dimension, because each unique dimension combination is a separately billed metric.
Put those in structured log fields and in DynamoDB, and keep the metric namespace to a small fixed set like tasks started, tasks failed, and trial duration.
Set an explicit retention period on every log group, because the default is to retain forever and storage accrues silently.
Use the Infrequent Access log class for raw trajectory logs, which halves ingestion to $0.285 per GB in Ireland.

Set up cost control on day one, not after the first bill.
Activate `experiment_id` as a cost allocation tag and pass it in the `tags` parameter of every `RunTask` call, remembering that `propagateTags` is off by default.
Create one action-enabled budget, which is free, with an action that actually revokes permissions rather than sending an email.
Enable Split Cost Allocation Data if per-trial cost attribution matters, which it does for a platform whose whole point is comparing configurations.

Fargate Spot is worth using for trial workloads but only after the retry semantics exist.
The published discount is up to 70%, which would cut the Fargate line from $1.65 to roughly $0.50, so the absolute saving at current scale is about $1 per month.
That is not worth any complexity today, and it becomes worth it only if trial volume grows by two orders of magnitude.
The reason to build interruption tolerance is scientific validity under retries, not cost.

Finally, measure startup latency and record it as a separate field per trial.
A 10-minute trial carries roughly 10% to 20% billable startup overhead that is not agent work, and comparing configurations without separating the two would contaminate any duration-based metric.

## 6. Open questions

Exact Fargate Spot per-vCPU-hour and per-GB-hour rates could not be verified from a primary source, because the table renders client-side and is absent from the Price List Bulk API.
The only primary-source statement is "up to a 70% discount".
This can be resolved by reading the rendered pricing page in a browser or by running a Spot task and checking Cost Explorer.

Public IPv4 billing granularity for sub-hour tasks is unverified.
The difference is $0.17 against $1.00 per month at the modelled scale, so it is not urgent.

Whether Bedrock model invocation from a Fargate task in a public subnet incurs any data transfer charge was not separately verified.
In-region traffic to an AWS service endpoint over the internet gateway should be free, but this is worth confirming against the first bill rather than assuming.

Glacier Instant Retrieval storage rates and CloudWatch OpenTelemetry ingestion rates for eu-west-1 were not extracted in this pass.
Neither affects the modelled scenario.

Bedrock regional availability for the chosen model has not been confirmed for eu-west-1 in this report and is the deciding input for region choice.
If the intended model is only available in us-east-1, the Fargate and S3 price parity means moving the whole stack there costs nothing extra, and it would actually save 14% on CloudWatch Logs and 13% on DynamoDB.

Whether the $1,000 credit pool applies to all of these services equally, or excludes some, was not verified.
AWS promotional credits sometimes carry service restrictions, and this is worth confirming before the architecture depends on it.
