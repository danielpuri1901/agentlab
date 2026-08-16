# Standard workflow: ArmA -> ArmB -> Finalize, each an ECS Fargate task run via the
# `.sync` optimized integration (Step Functions waits for the task to finish before
# advancing). Written in JSONata (QueryLanguage = "JSONata") rather than the older
# JSONPath ASL dialect: this is the query language AWS's own current Step Functions
# documentation demonstrates for arn:aws:states:::ecs:runTask.sync
# (https://docs.aws.amazon.com/step-functions/latest/dg/connect-ecs.html, fetched
# 2026-08-16 - the page's worked examples use "Arguments"/"{% ... %}", not the classic
# "Parameters"/".$" fields), and is AWS's stated direction for new state machines.
# Under JSONata, "Parameters" becomes "Arguments"; the inner field names and casing
# (LaunchType, NetworkConfiguration, AwsvpcConfiguration, Overrides.ContainerOverrides,
# Environment[].Name/Value) are unchanged by that switch - verified against the same
# page plus the ECS RunTask.sync worked example in
# https://repost.aws/knowledge-center/ecs-fargate-network-interface-errors, which
# shows NetworkConfiguration.AwsvpcConfiguration.{Subnets,SecurityGroups,AssignPublicIp}
# verbatim (note: "AwsvpcConfiguration", one hump on "vpc" - this is the Step Functions
# ECS integration's own casing, distinct from the unrelated EventBridge Rule
# EcsParameters schema, which happens to use "AwsVpcConfiguration" with two humps).
#
# The RAW execution input Pipes hands the state machine is NOT the flat message body -
# it is a ONE-ELEMENT JSON ARRAY wrapping it. Per
# https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-pipes-input-transformation.html
# ("For Lambda or Step Functions enrichments or targets, batches are delivered to the
# target as JSON arrays, even if the batch size is 1. However, input transformers will
# still be applied to individual records in the JSON Array, not the array as a whole."),
# fetched 2026-08-16: pipe.tf's input_template is applied to the single SQS record FIRST,
# producing {experiment_id, model, tasks, repeats, n_facts, filler_turns, summary_budget,
# max_connections, baseline_style, candidate_style} - and only THEN does Pipes wrap that
# object in a length-1 array (batch_size = 1 in pipe.tf) because Step Functions has no
# batch API of its own. So `$states.input` at StartAt is `[{...}]`, not `{...}`.
#
# The NormalizeInput state below (Output = "{% $states.input[0] %}") unwraps this before
# ArmA. JSONata's "Singleton array and value equivalence" rule
# (https://docs.jsonata.org/predicate: "any value (which is not itself an array) and an
# array containing just that value are deemed to be equivalent" - e.g. the worked example
# `Phone[0].number` matches a single (non-array) value) means `[0]` correctly extracts the
# object whether `$states.input` is the documented one-element array or, if Pipes'
# behavior here ever changed, a bare object - hence "robust to both shapes" rather than a
# bet on the current array-wrapping behavior specifically.
#
# From NormalizeInput onward, `$states.input` IS that flat object (a Pass state's Output
# becomes the next state's input), and every Task state's Output re-emits `$states.input`
# unchanged, so it stays available as `$states.input` in every state in the chain.
locals {
  # RESULTS_BUCKET/STATE_TABLE/AWS_REGION are static per-deploy values, not part of the
  # experiment spec, so they come straight from Terraform resources/vars rather than
  # `$states.input`. Everything else is read out of the state machine input with
  # JSONata; TASKS/REPEATS/N_FACTS/FILLER_TURNS/SUMMARY_BUDGET/MAX_CONNECTIONS are
  # wrapped in $string() because ECS ContainerOverride Environment values must be
  # strings, and worker.py (src/agentlab/worker.py) does its own int() parsing on the
  # way back out.
  arm_a_environment = [
    { Name = "EXPERIMENT_ID", Value = "{% $states.input.experiment_id %}" },
    { Name = "ARM_STYLE", Value = "{% $states.input.baseline_style %}" },
    { Name = "MODEL", Value = "{% $states.input.model %}" },
    { Name = "TASKS", Value = "{% $string($states.input.tasks) %}" },
    { Name = "REPEATS", Value = "{% $string($states.input.repeats) %}" },
    { Name = "N_FACTS", Value = "{% $string($states.input.n_facts) %}" },
    { Name = "FILLER_TURNS", Value = "{% $string($states.input.filler_turns) %}" },
    { Name = "SUMMARY_BUDGET", Value = "{% $string($states.input.summary_budget) %}" },
    { Name = "MAX_CONNECTIONS", Value = "{% $string($states.input.max_connections) %}" },
    { Name = "RESULTS_BUCKET", Value = aws_s3_bucket.results.id },
    { Name = "STATE_TABLE", Value = aws_dynamodb_table.state.name },
    { Name = "AWS_REGION", Value = var.aws_region },
    { Name = "AWS_DEFAULT_REGION", Value = var.aws_region },
  ]

  arm_b_environment = [
    { Name = "EXPERIMENT_ID", Value = "{% $states.input.experiment_id %}" },
    { Name = "ARM_STYLE", Value = "{% $states.input.candidate_style %}" },
    { Name = "MODEL", Value = "{% $states.input.model %}" },
    { Name = "TASKS", Value = "{% $string($states.input.tasks) %}" },
    { Name = "REPEATS", Value = "{% $string($states.input.repeats) %}" },
    { Name = "N_FACTS", Value = "{% $string($states.input.n_facts) %}" },
    { Name = "FILLER_TURNS", Value = "{% $string($states.input.filler_turns) %}" },
    { Name = "SUMMARY_BUDGET", Value = "{% $string($states.input.summary_budget) %}" },
    { Name = "MAX_CONNECTIONS", Value = "{% $string($states.input.max_connections) %}" },
    { Name = "RESULTS_BUCKET", Value = aws_s3_bucket.results.id },
    { Name = "STATE_TABLE", Value = aws_dynamodb_table.state.name },
    { Name = "AWS_REGION", Value = var.aws_region },
    { Name = "AWS_DEFAULT_REGION", Value = var.aws_region },
  ]

  # `agentlab worker finalize` reads a DIFFERENT env contract than run-arm (verified
  # against src/agentlab/worker.py's finalize_command): no ARM_STYLE, N_FACTS,
  # FILLER_TURNS, SUMMARY_BUDGET, or MAX_CONNECTIONS (it never calls _run_arm, so none
  # of the eval-shaping knobs apply); instead BASELINE_STYLE and CANDIDATE_STYLE, used
  # to download and pair both arms' logs.
  finalize_environment = [
    { Name = "EXPERIMENT_ID", Value = "{% $states.input.experiment_id %}" },
    { Name = "MODEL", Value = "{% $states.input.model %}" },
    { Name = "TASKS", Value = "{% $string($states.input.tasks) %}" },
    { Name = "REPEATS", Value = "{% $string($states.input.repeats) %}" },
    { Name = "BASELINE_STYLE", Value = "{% $states.input.baseline_style %}" },
    { Name = "CANDIDATE_STYLE", Value = "{% $states.input.candidate_style %}" },
    { Name = "RESULTS_BUCKET", Value = aws_s3_bucket.results.id },
    { Name = "STATE_TABLE", Value = aws_dynamodb_table.state.name },
    { Name = "AWS_REGION", Value = var.aws_region },
    { Name = "AWS_DEFAULT_REGION", Value = var.aws_region },
  ]

  network_configuration = {
    AwsvpcConfiguration = {
      Subnets        = data.aws_subnets.default_public.ids
      SecurityGroups = [aws_security_group.fargate_egress.id]
      AssignPublicIp = "ENABLED"
    }
  }

  # Two distinct transient-failure classes, per
  # https://repost.aws/knowledge-center/ecs-fargate-runtask-capacity ("If you start
  # RunTask from Step Functions and the task fails because of limited capacity, then
  # Step Functions records an ECS.AmazonECSException"): that error is the ECS RunTask
  # *API call* failing (capacity/throttling) before a task ever starts and before any
  # Bedrock spend happens, so it stays at the more generous MaxAttempts=3.
  # States.TaskFailed instead means the task started and the container exited non-zero
  # or failed to reach RUNNING - this could be a genuine bug in the eval or a one-off
  # infra blip (ENI attach timeout, image pull flake), and a retry can re-run
  # Bedrock-billed work. Adjudicated to MaxAttempts=2 rather than 1: the asymmetry
  # favors retrying once more, since the worst case of over-retrying a real bug is
  # under $1 (a full paired run is ~$0.54 per the plan's measured baseline), while the
  # worst case of under-retrying a transient blip is losing an entire ~45-minute
  # experiment to manual re-submission.
  ecs_transient_retry = [
    {
      ErrorEquals     = ["ECS.AmazonECSException"]
      IntervalSeconds = 10
      MaxAttempts     = 3
      BackoffRate     = 2.0
      JitterStrategy  = "FULL"
    },
    {
      ErrorEquals     = ["States.TaskFailed"]
      IntervalSeconds = 30
      MaxAttempts     = 2
      BackoffRate     = 2.0
    },
  ]

  # After retries are exhausted for any of the three Task states, land on the same
  # terminal Fail state with the error captured via Assign (the JSONata error-handling
  # pattern for "Retry and Catch with User-Friendly Error" - see the aws-step-functions
  # skill's references/error-handling.md, retrieved 2026-08-16). A Catcher's Output
  # replaces the state's input for whatever state it transitions to, so ExperimentFailed
  # reads the original experiment_id via `$states.context.Execution.Input` (always the
  # unmodified top-level input, per any-state) rather than `$states.input`.
  #
  # `Execution.Input` is fixed for the life of the execution to the RAW input Pipes
  # delivered - the one-element array described at the top of this file - and is
  # unaffected by NormalizeInput's own Output reassignment (a state's Output only ever
  # changes `$states.input` for whatever comes next, never `Execution.Input`). Reasoned
  # through: `Execution.Input.experiment_id` would technically still resolve here too, via
  # JSONata's implicit map-over-array field access on the one-element array (the same
  # "sequence flattening" that made the pre-fix code work at all) - but that relies on an
  # implicit quirk a future reader has to already know about. Indexing `[0]` explicitly
  # below matches NormalizeInput's own defensive pattern instead and doesn't depend on it.
  failure_catch = [
    {
      ErrorEquals = ["States.ALL"]
      Assign      = { error = "{% $states.errorOutput %}" }
      Next        = "ExperimentFailed"
    },
  ]

  definition = {
    Comment       = "AgentLab paired experiment: run the baseline and candidate arms on Fargate, then finalize the verdict."
    QueryLanguage = "JSONata"
    StartAt       = "NormalizeInput"
    States = {
      # Unwraps the Pipe's one-element array delivery (see this file's top comment) into
      # the flat experiment-spec object every downstream state's JSONata expects. `[0]` is
      # robust to both the documented array shape and a bare object, per JSONata's
      # singleton array/value equivalence.
      NormalizeInput = {
        Type   = "Pass"
        Output = "{% $states.input[0] %}"
        Next   = "ArmA"
      }
      ArmA = {
        Type     = "Task"
        Resource = "arn:aws:states:::ecs:runTask.sync"
        Arguments = {
          Cluster              = aws_ecs_cluster.agentlab.arn
          TaskDefinition       = aws_ecs_task_definition.arm_runner.arn
          LaunchType           = "FARGATE"
          NetworkConfiguration = local.network_configuration
          Overrides = {
            ContainerOverrides = [
              {
                Name        = "arm-runner"
                Environment = local.arm_a_environment
              },
            ]
          }
        }
        TimeoutSeconds = 2700
        Retry          = local.ecs_transient_retry
        Catch          = local.failure_catch
        Output         = "{% $states.input %}"
        Next           = "ArmB"
      }
      ArmB = {
        Type     = "Task"
        Resource = "arn:aws:states:::ecs:runTask.sync"
        Arguments = {
          Cluster              = aws_ecs_cluster.agentlab.arn
          TaskDefinition       = aws_ecs_task_definition.arm_runner.arn
          LaunchType           = "FARGATE"
          NetworkConfiguration = local.network_configuration
          Overrides = {
            ContainerOverrides = [
              {
                Name        = "arm-runner"
                Environment = local.arm_b_environment
              },
            ]
          }
        }
        TimeoutSeconds = 2700
        Retry          = local.ecs_transient_retry
        Catch          = local.failure_catch
        Output         = "{% $states.input %}"
        Next           = "Finalize"
      }
      Finalize = {
        Type     = "Task"
        Resource = "arn:aws:states:::ecs:runTask.sync"
        Arguments = {
          Cluster              = aws_ecs_cluster.agentlab.arn
          TaskDefinition       = aws_ecs_task_definition.finalizer.arn
          LaunchType           = "FARGATE"
          NetworkConfiguration = local.network_configuration
          Overrides = {
            ContainerOverrides = [
              {
                Name        = "finalizer"
                Environment = local.finalize_environment
              },
            ]
          }
        }
        TimeoutSeconds = 2700
        Retry          = local.ecs_transient_retry
        Catch          = local.failure_catch
        End            = true
      }
      ExperimentFailed = {
        Type  = "Fail"
        Error = "AgentLabExperimentFailed"
        Cause = "{% 'Experiment ' & $states.context.Execution.Input[0].experiment_id & ' failed: ' & ($exists($error.Error) ? $error.Error : 'Unknown') & ' - ' & ($exists($error.Cause) ? $error.Cause : 'No details') %}"
      }
    }
  }
}

# Bedrock model calls happen inside the container (agentlab worker run-arm/finalize
# call the Inspect eval machinery, which talks to Bedrock directly using the task
# role's credentials) - the state machine itself never calls Bedrock, only ECS RunTask.
resource "aws_sfn_state_machine" "experiment" {
  name       = "agentlab-experiment"
  role_arn   = aws_iam_role.sfn.arn
  type       = "STANDARD"
  definition = jsonencode(local.definition)
  depends_on = [aws_iam_role_policy.sfn]
}
