# Video Pipeline Game Implementation Plan

**Spec:** `docs/superpowers/specs/2026-09-19-video-pipeline-game-design.md`

## Global constraints

Use one standalone HTML file.
Use no external resources or network calls.
Keep the daily video path separate from the experiment fabric.
Use the current worker and story-video code as the source of truth.
Require a judge-passed candidate before delivery.
Open the finished file in the user's real Chrome.

## Task 1: Build the game

Create `docs/architecture/video-pipeline-game.html`.
Implement the guided 26-step run, cache lab, failure drill, and durable-state map.
Add responsive layout, keyboard access, reduced-motion support, reset, and local progress.
Use the fixed observed cost fixture from the design spec.

## Task 2: Verify the artifact

Validate the HTML structure with Python standard-library parsing.
Check the inline JavaScript syntax with Node when available.
Check that the file has no external network dependency.
Run the repository test suite.
Open the file with the macOS `open` command and inspect it in Chrome.
