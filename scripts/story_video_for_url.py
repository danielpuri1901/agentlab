"""Build one metaphor video from one URL on this machine.

AWS credentials must be current because the script uses Bedrock and Polly.
The script does not use DynamoDB or Telegram.
"""

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import boto3

from agentlab.scene_plan import DEFAULT_DEEP_READ_MODEL, deep_read
from agentlab.story_video import StoryFailed, compose_story_video
from agentlab.video_render import verify_voice


def run_story_video_for_url(url: str, out_dir: str) -> int:
    from agentlab.worker import _complete, _complete_long, _fetch_text

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    model = os.environ.get("DEEP_READ_MODEL", DEFAULT_DEEP_READ_MODEL)
    digest, plan = deep_read(url, _fetch_text, _complete, model=model)
    (out / "digest.md").write_text(digest, encoding="utf-8")
    (out / "scene_plan.json").write_text(
        plan.model_dump_json(indent=1), encoding="utf-8"
    )
    polly_client = boto3.client("polly")
    try:
        result = compose_story_video(
            digest,
            plan,
            polly_client,
            verify_voice(polly_client),
            _complete_long,
            out / "work",
            out / "video.mp4",
            story_model=os.environ.get("STORY_MODEL", model),
            scene_model=os.environ.get("SCENE_MODEL", model),
            judge_model=os.environ.get("JUDGE_MODEL", model),
        )
    except StoryFailed as exc:
        print(f"story failed: {exc}", file=sys.stderr)
        return 1

    (out / "storyboard.json").write_text(
        result.storyboard.model_dump_json(indent=1), encoding="utf-8"
    )
    (out / "paper_story.py").write_text(result.scene_source, encoding="utf-8")
    (out / "judgement.json").write_text(
        json.dumps(result.judgement, indent=1), encoding="utf-8"
    )
    print(
        f"video: {result.video_path}  attempts: {result.attempts}  "
        f"judge: {result.judge_score}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build one local metaphor video from a paper URL."
    )
    parser.add_argument("url")
    parser.add_argument("out_dir")
    args = parser.parse_args(argv)
    return run_story_video_for_url(args.url, args.out_dir)


if __name__ == "__main__":
    sys.exit(main())
