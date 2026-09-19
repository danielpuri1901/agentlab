# AgentLab Video Pipeline Game Design

## Goal

Create one standalone browser game that teaches the complete daily paper-video workflow.
The player must make real architecture, caching, retry, storage, and delivery decisions.

## Learning contract

The game must keep the daily video workflow separate from the SQS and Step Functions experiment workflow.
It must show EventBridge Scheduler starting an ECS Fargate task at 10:30 Europe/Amsterdam.
It must show the CORE, CLASSIC, and NOVEL tracks as isolated runs.
It must show all steps from candidate collection through Telegram feedback.
It must distinguish cache eligibility, cache writes, cache reads, and uncached tokens.
It must show the real bounded retry rules: three storyboard attempts, four scene attempts, and a 900-second video deadline.
It must require a judge-passed candidate before a video can ship.
It must show that final medium-quality render failure may use the accepted low-resolution preview.
It must show durable S3 artifacts, DynamoDB ledger state, Telegram delivery, and the quiet-hours queue.

## Experience

The main mode is a guided pipeline run with 26 visible steps.
The player advances the live run and answers decisions at important gates.
A cache lab teaches where stable prefixes end and changing data begins.
A failure drill tests recovery behavior.
A state map explains what survives after the container stops.

The page uses a bright animation-editing desk style.
Cobalt shows control flow.
Orange shows token traffic.
Green shows accepted quality.
The design must remain readable on mobile and with reduced motion.

## Cost fixture

The page uses the observed Cache-to-Cache run as a fixed tutorial fixture.
The measured estimate is $1.5609504 for 11 model calls.
It includes 254,364 cache-write tokens, 3,168 cache-read tokens, and 40,409 output tokens.
The conservative improved cache-placement estimate is $1.3348.
The fixture is educational and does not claim to be a live AWS bill.

## Technical limits

The game is one HTML file with inline CSS and JavaScript.
It has no network requests, external fonts, package dependencies, or build step.
Progress can be stored in localStorage.
The file must open directly with a file URL in Chrome.
