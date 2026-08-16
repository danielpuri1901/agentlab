# SQS -> Step Functions StartExecution, batch size 1: one `agentlab cloud submit`
# message starts exactly one experiment execution. Schema (source_parameters block,
# target_parameters block, step_function_state_machine_parameters.invocation_type,
# target_parameters.input_template as a plain string) verified against
# https://raw.githubusercontent.com/hashicorp/terraform-provider-aws/main/website/docs/r/pipes_pipe.html.markdown
# on 2026-08-16.
#
# invocation_type = "FIRE_AND_FORGET" is not a style choice - it is the only legal
# value here. Per
# https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-pipes-pipe-pipetargetstatemachineparameters.html
# ("REQUEST_RESPONSE is not supported for STANDARD state machine workflows"),
# REQUEST_RESPONSE (the field's own default) would call
# states:StartSyncExecution, which STANDARD workflows reject outright - only Express
# workflows support synchronous invocation. FIRE_AND_FORGET calls states:StartExecution
# instead (see infra/iam.tf's pipe role policy) and returns as soon as the execution is
# accepted, which is correct here regardless: each experiment runs for up to ~3 x 45
# minutes (TimeoutSeconds per arm/finalize state in stepfunctions.tf), and nothing
# downstream of the pipe needs to block on that.
#
# No enrichment step: every field the state machine needs (experiment_id, model, tasks,
# repeats, n_facts, filler_turns, summary_budget, max_connections, baseline_style,
# candidate_style - see stepfunctions.tf) is already present in the SQS message body
# that `agentlab cloud submit` (Task 5) sends. An enrichment step exists to call out to
# an external service (Lambda, API destination, another state machine) to add data the
# source event doesn't already have; reshaping data that IS already present is exactly
# what a target input_template does on its own, so adding an enrichment stage here would
# be a Lambda invocation (and its own IAM role) that does nothing.
resource "aws_pipes_pipe" "experiments" {
  name     = "agentlab-experiments"
  role_arn = aws_iam_role.pipe.arn
  source   = aws_sqs_queue.experiments.arn
  target   = aws_sfn_state_machine.experiment.arn

  source_parameters {
    sqs_queue_parameters {
      batch_size = 1
    }
  }

  target_parameters {
    step_function_state_machine_parameters {
      invocation_type = "FIRE_AND_FORGET"
    }

    # `<$.body.field>` extracts a field straight out of the SQS message body as parsed
    # JSON - EventBridge Pipes' "implicit body data parsing" transforms the SQS body
    # field into valid JSON before evaluating JSON paths against it (verified against
    # https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-pipes-input-transformation.html,
    # "Implicit body data parsing" section, on 2026-08-16), so no `$parse()` or
    # enrichment step is needed to reach into a JSON-encoded SQS body.
    #
    # The numeric fields (tasks, repeats, n_facts, filler_turns, summary_budget,
    # max_connections) are deliberately left WITHOUT surrounding quotes in this
    # template, per the same page's "Common issues" section: "Quotes are not required
    # for variables that represent strings ... EventBridge Pipes does not add quotes to
    # variables that represent JSON objects or arrays" - the message body's JSON number
    # type is preserved as-is. The string fields (experiment_id, model, baseline_style,
    # candidate_style) keep their quotes so the result is valid JSON regardless of
    # whether EventBridge's auto-quoting behavior applies. stepfunctions.tf's Task
    # states re-stringify the numeric fields with JSONata's $string() when building
    # ECS container overrides, since Environment values must be strings either way.
    input_template = <<-EOT
      {
        "experiment_id": "<$.body.experiment_id>",
        "model": "<$.body.model>",
        "tasks": <$.body.tasks>,
        "repeats": <$.body.repeats>,
        "n_facts": <$.body.n_facts>,
        "filler_turns": <$.body.filler_turns>,
        "summary_budget": <$.body.summary_budget>,
        "max_connections": <$.body.max_connections>,
        "baseline_style": "<$.body.baseline_style>",
        "candidate_style": "<$.body.candidate_style>"
      }
    EOT
  }
}
