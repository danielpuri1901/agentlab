# Daniel's interests: the pick profile for worker explain

This file drives the CORE and NOVEL track picks in `worker explain`
(docs/specs/2026-08-23-daily-paper-videos.md).
The pick prompt reads this file as plain text, so write plain sentences
here, not structured fields.

This file is explicitly hand-editable.
Nothing in the pipeline writes to it.
Edit it directly whenever Daniel's interests shift.
Per the spec's v1 scope cut, no automated process updates this file yet;
that is a registered follow-up, not something worker explain does today.

## Core interests (the CORE track ranks candidates against this list)

Daniel wants the single best new paper each day on:

- Agents: agent architectures, agent harnesses, tool use, planning loops.
- Evals: how to measure agent behavior, benchmarks, LLM-as-judge methods.
- RSI: recursive self-improvement, self-play, self-modifying systems.
- Compaction: context compression, summarization under a token budget.
- Agent memory: long-term memory, retrieval for agents, learned state.
- Harnesses: the software that runs an agent loop end to end.
- Verification: checking agent output, grounding, hallucination control.
- Multi-agent coordination: multiple agents working together or against
  each other, coordination failures, emergent behavior.

Papers outrank HN links. Pick the single candidate most relevant to this
list, not the loudest one.

## Novel track note (the NOVEL track picks the opposite way)

The NOVEL track does not rank against the list above.
Pick whatever Daniel does NOT already know about: something that sounds
cool, frontier-opening, outside the core interests list.
World models, diffusion, robotics, new training methods, whatever is
trending that day, all fair game.
Only skip a candidate if it plainly overlaps something in the core
interests list above; otherwise favor surprise over relevance.
The point of this track is to widen what Daniel tracks, not to serve what
he already tracks.
